"""
Garra Terraplenagem — Sistema de Fotos Jardinagem v2
Backend Flask + PostgreSQL direto (psycopg2) + Supabase Storage
"""

import os, io, uuid, json, calendar, base64, logging
from datetime import datetime, timedelta, date
from functools import wraps

import bcrypt
import jwt
import psycopg2
import psycopg2.extras
import psycopg2.extensions
from flask import Flask, request, jsonify, send_from_directory, g
from PIL import Image
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────
DB_URL               = os.getenv("DATABASE_URL")
SUPABASE_URL         = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
JWT_SECRET           = os.getenv("JWT_SECRET", "dev-secret")
JWT_EXPIRY_HOURS     = int(os.getenv("JWT_EXPIRY_HOURS", 8))
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
BUCKET_NAME          = "jardinagem-fotos"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024

# ── ERROR HANDLERS GLOBAIS (sempre JSON, nunca HTML) ──────────
@app.errorhandler(400)
def err_400(e):
    return jsonify({"erro": "Requisição inválida", "detalhe": str(e)}), 400

@app.errorhandler(401)
def err_401(e):
    return jsonify({"erro": "Não autenticado"}), 401

@app.errorhandler(403)
def err_403(e):
    return jsonify({"erro": "Sem permissão"}), 403

@app.errorhandler(404)
def err_404(e):
    return jsonify({"erro": f"Rota não encontrada: {request.path}"}), 404

@app.errorhandler(500)
def err_500(e):
    log.error(f"Erro 500: {e}")
    return jsonify({"erro": "Erro interno do servidor", "detalhe": str(e)}), 500

# ── STORAGE (Supabase Storage via requests) ───────────────────
import requests as req_lib

def _checar_supabase():
    """Valida variáveis de ambiente antes de qualquer operação de storage."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError(
            "Variáveis SUPABASE_URL e SUPABASE_SERVICE_KEY não configuradas. "
            "Verifique o Environment no Render."
        )

def storage_upload(dados: bytes, path: str) -> str:
    _checar_supabase()
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "image/jpeg",
        "x-upsert":      "true"
    }
    try:
        r = req_lib.post(url, headers=headers, data=dados, timeout=30)
    except req_lib.exceptions.Timeout:
        raise RuntimeError("Storage: timeout ao tentar enviar foto")
    except req_lib.exceptions.ConnectionError as e:
        raise RuntimeError(f"Storage: falha de conexão com Supabase — {e}")

    if r.status_code not in (200, 201):
        raise RuntimeError(
            f"Storage upload falhou [{r.status_code}]: {r.text}"
        )
    return path

def storage_url(path: str, segundos: int = 3600) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not path:
        return ""
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET_NAME}/{path}"
        headers = {"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        r = req_lib.post(url, headers=headers, json={"expiresIn": segundos}, timeout=10)
        if r.status_code == 200:
            data = r.json()
            return f"{SUPABASE_URL}/storage/v1{data.get('signedURL', '')}"
    except Exception as e:
        log.warning(f"storage_url falhou para {path}: {e}")
    return ""

def storage_delete(paths: list):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not paths:
        return
    try:
        url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}"
        headers = {"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"}
        req_lib.delete(url, headers=headers, json={"prefixes": paths}, timeout=10)
    except Exception as e:
        log.warning(f"storage_delete falhou: {e}")

# ── DATABASE ──────────────────────────────────────────────────
def get_db():
    if "db" not in g:
        try:
            g.db = psycopg2.connect(
                DB_URL,
                cursor_factory=psycopg2.extras.RealDictCursor,
                sslmode="require",
                connect_timeout=10
            )
        except Exception as e:
            log.error(f"DB connection error: {e}")
            raise
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def query(sql, params=None, fetch="all"):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql, params or ())
        db.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        if fetch == "none":
            return None

def query_id(sql, params=None):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(sql + " RETURNING *", params or ())
        db.commit()
        return cur.fetchone()

# ── AUTH ──────────────────────────────────────────────────────
def gerar_token(usuario: dict) -> str:
    payload = {
        "sub":    str(usuario["id"]),
        "nome":   usuario["nome"],
        "perfil": usuario["perfil"],
        "exp":    datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")

def verificar_token(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        if not token:
            token = request.cookies.get("garra_token")
        if not token:
            return jsonify({"erro": "Não autenticado"}), 401
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            g.usuario = payload
        except jwt.ExpiredSignatureError:
            return jsonify({"erro": "Sessão expirada"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"erro": "Token inválido"}), 401
        return f(*args, **kwargs)
    return decorated

def requer_perfil(*perfis):
    def decorator(f):
        @wraps(f)
        @verificar_token
        def decorated(*args, **kwargs):
            if g.usuario.get("perfil") not in perfis:
                return jsonify({"erro": "Sem permissão"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator

# ── HELPERS ───────────────────────────────────────────────────
def next_code(n: int = 2) -> int:
    row = query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    atual = int(row["valor"])
    query("UPDATE jardinagem.config SET valor=%s WHERE chave='next_code'",
          (str(atual + n),), fetch="none")
    return atual

def comprimir_imagem(dados: bytes, max_px: int = 1400, qualidade: int = 82) -> bytes:
    img = Image.open(io.BytesIO(dados))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=qualidade, optimize=True)
    return buf.getvalue()

def semanas_do_mes(ano: int, mes: int, mes_id: int):
    _, ultimo_dia = calendar.monthrange(ano, mes)
    intervalos = [(1, 7), (8, 14), (15, 21), (22, ultimo_dia)]
    for i, (ini, fim) in enumerate(intervalos):
        label = f"Semana {i+1} — {ini:02d}/{mes:02d} a {fim:02d}/{mes:02d}/{ano}"
        query("""INSERT INTO jardinagem.semanas
                 (mes_id, label, data_ini, data_fim, ordem, status)
                 VALUES (%s,%s,%s,%s,%s,'aberta')""",
              (mes_id, label,
               f"{ano}-{mes:02d}-{ini:02d}",
               f"{ano}-{mes:02d}-{fim:02d}", i),
              fetch="none")

# ── STATIC ────────────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("templates", "index.html")

@app.route("/mobile")
def mobile():
    return send_from_directory("templates", "mobile.html")

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json")

@app.route("/sw.js")
def service_worker():
    resp = send_from_directory("static/js", "sw.js")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp

# ── AUTH ROUTES ───────────────────────────────────────────────
@app.route("/api/login", methods=["POST"])
def login():
    d = request.json or {}
    email = (d.get("email") or "").strip().lower()
    senha = (d.get("senha") or "").encode()

    try:
        usuario = query(
            "SELECT * FROM public.usuarios_garra WHERE email=%s AND ativo=true LIMIT 1",
            (email,), fetch="one"
        )
    except Exception as e:
        log.error(f"DB error no login: {e}")
        return jsonify({"erro": "Erro interno"}), 500

    if not usuario:
        return jsonify({"erro": "Credenciais inválidas"}), 401

    try:
        if not bcrypt.checkpw(senha, usuario["senha_hash"].encode()):
            return jsonify({"erro": "Credenciais inválidas"}), 401
    except Exception as e:
        log.error(f"Bcrypt error: {e}")
        return jsonify({"erro": "Erro interno"}), 500

    token = gerar_token(dict(usuario))
    resp = jsonify({
        "token":  token,
        "nome":   usuario["nome"],
        "perfil": usuario["perfil"]
    })
    resp.set_cookie("garra_token", token, httponly=True, samesite="Strict",
                    max_age=JWT_EXPIRY_HOURS * 3600)
    return resp

@app.route("/api/me")
@verificar_token
def me():
    return jsonify(g.usuario)

@app.route("/api/logout", methods=["POST"])
def logout():
    resp = jsonify({"ok": True})
    resp.delete_cookie("garra_token")
    return resp

# ── MESES ─────────────────────────────────────────────────────
@app.route("/api/meses")
@verificar_token
def list_meses():
    meses = query("""
        SELECT m.*, COUNT(DISTINCT s.id) as total_semanas
        FROM jardinagem.meses m
        LEFT JOIN jardinagem.semanas s ON s.mes_id = m.id
        GROUP BY m.id ORDER BY m.ano DESC, m.mes DESC
    """)
    return jsonify([dict(r) for r in meses])

@app.route("/api/meses", methods=["POST"])
@requer_perfil("admin", "luana")
def criar_mes():
    d = request.json or {}
    ano, mes = int(d["ano"]), int(d["mes"])
    nomes = ["","Janeiro","Fevereiro","Março","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    label = d.get("label") or f"{nomes[mes]}/{ano}"

    exist = query("SELECT id FROM jardinagem.meses WHERE ano=%s AND mes=%s",
                  (ano, mes), fetch="one")
    if exist:
        mes_id = exist["id"]
    else:
        row = query_id("INSERT INTO jardinagem.meses(ano,mes,label) VALUES(%s,%s,%s)",
                       (ano, mes, label))
        mes_id = row["id"]

    sem_exist = query("SELECT id FROM jardinagem.semanas WHERE mes_id=%s LIMIT 1",
                      (mes_id,), fetch="one")
    if not sem_exist:
        semanas_do_mes(ano, mes, mes_id)

    mes_data = query("SELECT * FROM jardinagem.meses WHERE id=%s", (mes_id,), fetch="one")
    return jsonify(dict(mes_data)), 201

@app.route("/api/meses/<int:mid>")
@verificar_token
def get_mes(mid):
    m = query("SELECT * FROM jardinagem.meses WHERE id=%s", (mid,), fetch="one")
    if not m:
        return jsonify({"erro": "Não encontrado"}), 404

    result = dict(m)
    result["semanas"] = []

    sems = query("SELECT * FROM jardinagem.semanas WHERE mes_id=%s ORDER BY ordem", (mid,))
    for s in sems:
        sd = dict(s)
        sd["pares"] = []
        pares = query("SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem",
                      (s["id"],))
        for p in pares:
            pd = dict(p)
            fotos = query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
            pd["fotos"] = []
            for f in fotos:
                fd = dict(f)
                fd["url"] = storage_url(f["storage_path"]) if f["storage_path"] else ""
                pd["fotos"].append(fd)
            sd["pares"].append(pd)
        result["semanas"].append(sd)

    return jsonify(result)

# ── SEMANAS ───────────────────────────────────────────────────

@app.route("/api/semanas", methods=["POST"])
@verificar_token
def criar_semana():
    d = request.json or {}
    mes_id = d.get("mes_id")
    label  = (d.get("label") or "").strip()
    ordem  = d.get("ordem", 0)
    if not mes_id or not label:
        return jsonify({"erro": "mes_id e label são obrigatórios"}), 400
    try:
        row = query_id(
            "INSERT INTO jardinagem.semanas (mes_id, label, ordem, status) VALUES (%s,%s,%s,'aberta')",
            (mes_id, label, ordem)
        )
        return jsonify(dict(row)), 201
    except Exception as e:
        log.error(f"criar_semana erro: {e}")
        return jsonify({"erro": str(e)}), 500

@app.route("/api/semanas/ativa")
@verificar_token
def semana_ativa():
    hoje = date.today().isoformat()
    row = query("""SELECT s.*, m.id as mes_id, m.ano, m.mes, m.label as mes_label
                   FROM jardinagem.semanas s
                   JOIN jardinagem.meses m ON m.id = s.mes_id
                   WHERE s.data_ini <= %s AND s.data_fim >= %s
                   LIMIT 1""", (hoje, hoje), fetch="one")
    if not row:
        return jsonify({"erro": "Sem semana ativa"}), 404
    return jsonify(dict(row))

@app.route("/api/semanas/<int:sid>", methods=["PATCH"])
@requer_perfil("admin", "luana")
def patch_semana(sid):
    d = request.json or {}
    for col in ["label", "status", "enviado_em"]:
        if col in d:
            query(f"UPDATE jardinagem.semanas SET {col}=%s WHERE id=%s",
                  (d[col], sid), fetch="none")
    return jsonify({"ok": True})

@app.route("/api/semanas/<int:sid>", methods=["DELETE"])
@requer_perfil("admin", "luana")
def del_semana(sid):
    pares = query("SELECT id FROM jardinagem.pares WHERE semana_id=%s", (sid,))
    for p in pares:
        fotos = query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        paths = [f["storage_path"] for f in fotos if f["storage_path"]]
        if paths:
            storage_delete(paths)
    query("DELETE FROM jardinagem.semanas WHERE id=%s", (sid,), fetch="none")
    return jsonify({"ok": True})

# ── PARES ─────────────────────────────────────────────────────
@app.route("/api/pares", methods=["POST"])
@verificar_token
def criar_par():
    d = request.json or {}
    cod = next_code(2)
    row = query_id("""INSERT INTO jardinagem.pares
                      (semana_id, codigo_a, codigo_d, local_nome, data_label, ordem)
                      VALUES (%s,%s,%s,%s,%s,%s)""",
                   (d["semana_id"], cod, cod+1,
                    d.get("local_nome",""), d.get("data_label",""), d.get("ordem",0)))
    return jsonify(dict(row)), 201

@app.route("/api/pares/<int:pid>", methods=["PATCH"])
@verificar_token
def patch_par(pid):
    d = request.json or {}
    for col in ["local_nome", "ordem", "semana_id", "data_label"]:
        if col in d:
            query(f"UPDATE jardinagem.pares SET {col}=%s WHERE id=%s",
                  (d[col], pid), fetch="none")
    return jsonify({"ok": True})

@app.route("/api/pares/<int:pid>", methods=["DELETE"])
@verificar_token
def del_par(pid):
    fotos = query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (pid,))
    paths = [f["storage_path"] for f in fotos if f["storage_path"]]
    if paths:
        storage_delete(paths)
    query("DELETE FROM jardinagem.pares WHERE id=%s", (pid,), fetch="none")
    return jsonify({"ok": True})

# ── FOTOS DESKTOP (Luana) ─────────────────────────────────────
@app.route("/api/fotos/avulsa", methods=["POST"])
@verificar_token
def foto_avulsa():
    try:
        par_id = int(request.form["par_id"])
        tipo   = request.form["tipo"]
        file   = request.files["foto"]

        dados = comprimir_imagem(file.read())
        path  = storage_upload(dados, f"{datetime.now().strftime('%Y/%m')}/{uuid.uuid4().hex}.jpg")

        antiga = query("SELECT id, storage_path FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s",
                       (par_id, tipo), fetch="one")
        if antiga:
            storage_delete([antiga["storage_path"]])
            query("DELETE FROM jardinagem.fotos WHERE id=%s", (antiga["id"],), fetch="none")

        row = query_id("""INSERT INTO jardinagem.fotos
                          (par_id, tipo, origem, enviado_por, storage_path, filename_orig, sincronizado)
                          VALUES (%s,%s,'desktop',%s,%s,%s,true)""",
                       (par_id, tipo, g.usuario["sub"], path, file.filename))
        foto = dict(row)
        foto["url"] = storage_url(path)
        return jsonify(foto), 201

    except KeyError as e:
        return jsonify({"erro": f"Campo obrigatório ausente: {e}"}), 400
    except (ValueError, RuntimeError) as e:
        log.error(f"foto_avulsa storage error: {e}")
        return jsonify({"erro": str(e)}), 500
    except Exception as e:
        log.error(f"foto_avulsa erro inesperado: {e}")
        return jsonify({"erro": "Erro interno ao salvar foto"}), 500

# ── FOTOS MOBILE (Arthur / Breno) ────────────────────────────
@app.route("/api/fotos/mobile", methods=["POST"])
@verificar_token
def foto_mobile():
    try:
        semana_id_raw = request.form.get("semana_id", "ativa")
        tipo          = request.form.get("tipo", "antes")
        local_nome    = request.form.get("local_nome", "")
        offline_id    = request.form.get("offline_id", "")
        file          = request.files.get("foto")

        if not file:
            return jsonify({"erro": "Foto não enviada"}), 400

        if semana_id_raw == "ativa" or not semana_id_raw:
            hoje = date.today().isoformat()
            row = query("SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1",
                        (hoje, hoje), fetch="one")
            if not row:
                return jsonify({"erro": "Nenhuma semana ativa"}), 404
            semana_id = row["id"]
        else:
            semana_id = int(semana_id_raw)

        if offline_id:
            exist = query("SELECT id FROM jardinagem.fila_sync WHERE id=%s AND processado=true",
                          (offline_id,), fetch="one")
            if exist:
                return jsonify({"ok": True, "duplicado": True}), 200

        dados = comprimir_imagem(file.read(), max_px=1400, qualidade=80)
        path  = storage_upload(dados, f"{datetime.now().strftime('%Y/%m')}/{uuid.uuid4().hex}.jpg")

        if local_nome:
            cod = next_code(2)
            par = query_id("INSERT INTO jardinagem.pares(semana_id,codigo_a,codigo_d,local_nome,ordem) VALUES(%s,%s,%s,%s,99)",
                           (semana_id, cod, cod+1, local_nome))
            par_id = par["id"]
        else:
            pares = query("SELECT id FROM jardinagem.pares WHERE semana_id=%s ORDER BY criado_em DESC LIMIT 10",
                          (semana_id,))
            par_id = None
            for p in pares:
                f = query("SELECT id FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s",
                          (p["id"], tipo), fetch="one")
                if not f:
                    par_id = p["id"]
                    break
            if not par_id:
                cod = next_code(2)
                par = query_id("INSERT INTO jardinagem.pares(semana_id,codigo_a,codigo_d,ordem) VALUES(%s,%s,%s,99)",
                               (semana_id, cod, cod+1))
                par_id = par["id"]

        row = query_id("""INSERT INTO jardinagem.fotos
                          (par_id,tipo,origem,enviado_por,storage_path,filename_orig,sincronizado)
                          VALUES(%s,%s,'mobile',%s,%s,%s,true)""",
                       (par_id, tipo, g.usuario["sub"], path, file.filename))

        if offline_id:
            query("""INSERT INTO jardinagem.fila_sync(id,usuario_id,semana_id,local_nome,tipo,storage_path,processado)
                     VALUES(%s,%s,%s,%s,%s,%s,true)
                     ON CONFLICT(id) DO UPDATE SET processado=true""",
                  (offline_id, g.usuario["sub"], semana_id, local_nome, tipo, path), fetch="none")

        foto = dict(row)
        foto["url"] = storage_url(path)
        return jsonify(foto), 201

    except (ValueError, RuntimeError) as e:
        log.error(f"foto_mobile storage error: {e}")
        return jsonify({"erro": str(e)}), 500
    except Exception as e:
        log.error(f"foto_mobile erro inesperado: {e}")
        return jsonify({"erro": "Erro interno ao salvar foto"}), 500

@app.route("/api/fotos/<int:fid>", methods=["DELETE"])
@verificar_token
def del_foto(fid):
    f = query("SELECT storage_path FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="one")
    if f:
        storage_delete([f["storage_path"]])
        query("DELETE FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="none")
    return jsonify({"ok": True})

@app.route("/api/fotos/<int:fid>/url")
@verificar_token
def url_foto(fid):
    f = query("SELECT storage_path FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="one")
    if not f:
        return jsonify({"erro": "Não encontrado"}), 404
    return jsonify({"url": storage_url(f["storage_path"])})

# ── CONFIG ────────────────────────────────────────────────────
@app.route("/api/config")
@requer_perfil("admin", "luana")
def get_config():
    rows = query("SELECT * FROM jardinagem.config")
    return jsonify({r["chave"]: r["valor"] for r in rows})

@app.route("/api/clientes")
@verificar_token
def list_clientes():
    rows = query("SELECT id, nome FROM public.clientes_garra WHERE ativo=true")
    return jsonify([dict(r) for r in rows])

# ── RELATÓRIOS ────────────────────────────────────────────────
def _montar_dados_semana(semana_id: int):
    """Monta estrutura completa de dados de uma semana."""
    sem = query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem:
        return None, None, None

    mes = query("SELECT * FROM jardinagem.meses WHERE id=%s", (sem["mes_id"],), fetch="one")

    data_ini = sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else ""
    data_fim = sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""

    semana_dict = {
        "label":    sem["label"],
        "data_ini": data_ini,
        "data_fim": data_fim,
    }

    pares_raw = query(
        "SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem, id",
        (semana_id,)
    )
    pares = []
    for p in pares_raw:
        fotos = query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        foto_antes  = next((dict(f) for f in fotos if f["tipo"]=="antes"),  None)
        foto_depois = next((dict(f) for f in fotos if f["tipo"]=="depois"), None)
        pares.append({
            "codigo_a":   p["codigo_a"],
            "codigo_d":   p["codigo_d"],
            "local_nome": p["local_nome"] or "",
            "foto_antes":  foto_antes,
            "foto_depois": foto_depois,
        })

    relatorios_raw = query(
        """SELECT r.*, u.nome as responsavel_nome
           FROM jardinagem.relatorios_diarios r
           JOIN public.usuarios_garra u ON u.id = r.usuario_id
           WHERE r.semana_id=%s ORDER BY r.data, r.criado_em""",
        (semana_id,)
    )
    relatorios = []
    for r in relatorios_raw:
        relatorios.append({
            "data":        r["data"].strftime("%d/%m/%Y") if r["data"] else "",
            "local":       r["local_nome"] or "",
            "km_ini":      float(r["km_inicial"] or 0),
            "km_fin":      float(r["km_final"]   or 0),
            "hr_ini":      str(r["hora_inicio"] or ""),
            "hr_fim":      str(r["hora_fim"]    or ""),
            "obs":         r["observacao"] or "",
            "responsavel": r["responsavel_nome"] or "",
        })

    return semana_dict, pares, relatorios



@app.route("/api/relatorios/<int:semana_id>/preview")
@verificar_token
def preview_relatorio(semana_id):
    """Retorna dados estruturados da semana para preview no desktop."""
    try:
        sem = query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
        if not sem:
            return jsonify({"erro": "Semana não encontrada"}), 404

        # Pares com status de fotos
        pares_raw = query(
            "SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem, id",
            (semana_id,)
        )
        pares = []
        for p in pares_raw:
            fotos = query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
            fa = next((f for f in fotos if f["tipo"]=="antes"),  None)
            fd = next((f for f in fotos if f["tipo"]=="depois"), None)
            pares.append({
                "id":         p["id"],
                "codigo_a":   p["codigo_a"],
                "codigo_d":   p["codigo_d"],
                "local_nome": p["local_nome"] or "",
                "foto_antes":  bool(fa),
                "foto_depois": bool(fd),
            })

        # Relatórios de KM
        kms_raw = query("""
            SELECT r.*, u.nome as responsavel_nome
            FROM jardinagem.relatorios_diarios r
            JOIN public.usuarios_garra u ON u.id = r.usuario_id
            WHERE r.semana_id=%s ORDER BY r.data, r.criado_em
        """, (semana_id,))
        kms = []
        for r in kms_raw:
            kms.append({
                "id":          r["id"],
                "data":        r["data"].strftime("%d/%m/%Y") if r["data"] else "",
                "local_nome":  r["local_nome"] or "",
                "km_inicial":  float(r["km_inicial"] or 0),
                "km_final":    float(r["km_final"]   or 0),
                "hora_inicio": str(r["hora_inicio"]) if r["hora_inicio"] else "",
                "hora_fim":    str(r["hora_fim"])    if r["hora_fim"]    else "",
                "observacao":  r["observacao"] or "",
                "responsavel": r["responsavel_nome"] or "",
            })

        return jsonify({
            "semana_id":  semana_id,
            "label":      sem["label"],
            "pares":      pares,
            "relatorios": kms,
            "total_pares":    len(pares),
            "pares_completos": sum(1 for p in pares if p["foto_antes"] and p["foto_depois"]),
            "total_km":   len(kms),
        })

    except Exception as e:
        log.error(f"preview_relatorio erro: {e}")
        return jsonify({"erro": str(e)}), 500

@app.route("/api/relatorios/<int:semana_id>/fotos")
@requer_perfil("admin", "luana")
def baixar_relatorio_fotos(semana_id):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from gerar_relatorio import gerar_relatorio_fotos

    semana, pares, _ = _montar_dados_semana(semana_id)
    if not semana:
        return jsonify({"erro": "Semana não encontrada"}), 404

    try:
        buf = gerar_relatorio_fotos(semana, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
        nome = f"Relatorio-Fotos-{semana_id}.xlsx"
        from flask import send_file
        return send_file(buf, as_attachment=True, download_name=nome,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        log.error(f"Erro ao gerar relatório de fotos: {e}")
        return jsonify({"erro": f"Erro ao gerar relatório: {str(e)}"}), 500


@app.route("/api/relatorios/<int:semana_id>/km")
@requer_perfil("admin", "luana")
def baixar_relatorio_km(semana_id):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from gerar_relatorio import gerar_relatorio_km

    semana, _, relatorios = _montar_dados_semana(semana_id)
    if not semana:
        return jsonify({"erro": "Semana não encontrada"}), 404

    try:
        buf = gerar_relatorio_km(semana, relatorios)
        nome = f"Relatorio-KM-{semana_id}.xlsx"
        from flask import send_file
        return send_file(buf, as_attachment=True, download_name=nome,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        log.error(f"Erro ao gerar relatório de KM: {e}")
        return jsonify({"erro": f"Erro ao gerar relatório: {str(e)}"}), 500


@app.route("/api/relatorios/<int:semana_id>/enviar", methods=["POST"])
@requer_perfil("admin", "luana")
def enviar_relatorio_email(semana_id):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from gerar_relatorio import gerar_relatorio_fotos, gerar_relatorio_km, enviar_relatorios_email

    mail_user    = os.getenv("MAIL_USERNAME", "")
    mail_pass    = os.getenv("MAIL_PASSWORD", "")
    mail_destino = os.getenv("MAIL_DESTINO",  "")
    mail_cc      = os.getenv("MAIL_CC",       "")

    if not all([mail_user, mail_pass, mail_destino]):
        return jsonify({"erro": "Email não configurado. Verifique as variáveis MAIL_* no Render."}), 400

    semana, pares, relatorios = _montar_dados_semana(semana_id)
    if not semana:
        return jsonify({"erro": "Semana não encontrada"}), 404

    try:
        buf_fotos = gerar_relatorio_fotos(semana, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
        buf_km    = gerar_relatorio_km(semana, relatorios)
        enviar_relatorios_email(semana, buf_fotos, buf_km,
                                mail_user, mail_pass, mail_destino, mail_cc)

        query("""INSERT INTO jardinagem.emails_enviados
                 (semana_id, destinatario, assunto, status)
                 VALUES (%s,%s,%s,'enviado')""",
              (semana_id, mail_destino,
               f"Relatório {semana['label']}"), fetch="none")

        query("UPDATE jardinagem.semanas SET status='enviada', enviado_em=NOW() WHERE id=%s",
              (semana_id,), fetch="none")

        return jsonify({"ok": True, "mensagem": f"Relatórios enviados para {mail_destino}"})

    except Exception as e:
        log.error(f"Erro ao enviar relatório: {e}")
        query("""INSERT INTO jardinagem.emails_enviados
                 (semana_id, destinatario, assunto, status, erro_msg)
                 VALUES (%s,%s,%s,'erro',%s)""",
              (semana_id, mail_destino,
               f"Relatório {semana['label']}", str(e)), fetch="none")
        return jsonify({"erro": f"Falha no envio: {str(e)}"}), 500


@app.route("/api/semanas/<int:sid>/status")
@verificar_token
def status_semana(sid):
    sem = query("SELECT * FROM jardinagem.semanas WHERE id=%s", (sid,), fetch="one")
    if not sem:
        return jsonify({"erro": "Não encontrada"}), 404

    emails = query(
        "SELECT * FROM jardinagem.emails_enviados WHERE semana_id=%s ORDER BY enviado_em DESC",
        (sid,)
    )
    total_pares = query(
        "SELECT COUNT(*) as n FROM jardinagem.pares WHERE semana_id=%s", (sid,), fetch="one"
    )
    total_fotos = query(
        """SELECT COUNT(*) as n FROM jardinagem.fotos f
           JOIN jardinagem.pares p ON p.id=f.par_id WHERE p.semana_id=%s""",
        (sid,), fetch="one"
    )
    total_relats = query(
        "SELECT COUNT(*) as n FROM jardinagem.relatorios_diarios WHERE semana_id=%s",
        (sid,), fetch="one"
    )

    return jsonify({
        "semana":           dict(sem),
        "total_pares":      total_pares["n"],
        "total_fotos":      total_fotos["n"],
        "total_relatorios": total_relats["n"],
        "emails":           [dict(e) for e in emails],
    })


# ── INIT ──────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n🌿 Garra — Sistema de Fotos Jardinagem")
    print("   Backend: Flask + PostgreSQL direto")
    print("   Acesse:  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)


# ══════════════════════════════════════════════════════════════
# ROTAS — RELATÓRIO DIÁRIO (KM/Serviço) — mobile Arthur/Breno
# ══════════════════════════════════════════════════════════════

@app.route("/api/relatorios/km", methods=["POST"])
@verificar_token
def criar_relatorio_km():
    """Recebe registro de KM/serviço do mobile e salva no banco."""
    try:
        d = request.json or {}

        semana_id = d.get("semana_id")
        if not semana_id:
            hoje = date.today().isoformat()
            row = query(
                "SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1",
                (hoje, hoje), fetch="one"
            )
            if not row:
                return jsonify({"erro": "Nenhuma semana ativa"}), 404
            semana_id = row["id"]

        local_nome   = (d.get("local_nome") or "").strip()
        km_inicial   = d.get("km_inicial")
        km_final     = d.get("km_final")
        hora_inicio  = d.get("hora_inicio") or None
        hora_fim     = d.get("hora_fim")    or None
        observacao   = (d.get("observacao") or "").strip()
        data_ref     = d.get("data") or date.today().isoformat()
        offline_id   = d.get("offline_id")  or None

        if not local_nome:
            return jsonify({"erro": "Campo local_nome obrigatório"}), 400
        if km_inicial is None or km_final is None:
            return jsonify({"erro": "Campos km_inicial e km_final obrigatórios"}), 400
        if float(km_final) <= float(km_inicial):
            return jsonify({"erro": "km_final deve ser maior que km_inicial"}), 400

        # Deduplicação offline
        if offline_id:
            exist = query(
                "SELECT id FROM jardinagem.relatorios_diarios WHERE offline_id=%s",
                (offline_id,), fetch="one"
            )
            if exist:
                return jsonify({"ok": True, "duplicado": True, "id": exist["id"]}), 200

        row = query_id("""
            INSERT INTO jardinagem.relatorios_diarios
                (semana_id, usuario_id, data, local_nome,
                 km_inicial, km_final, hora_inicio, hora_fim,
                 observacao, offline_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            semana_id, g.usuario["sub"], data_ref, local_nome,
            float(km_inicial), float(km_final), hora_inicio, hora_fim,
            observacao, offline_id
        ))

        return jsonify({"ok": True, "id": row["id"]}), 201

    except Exception as e:
        log.error(f"criar_relatorio_km erro: {e}")
        return jsonify({"erro": str(e)}), 500


@app.route("/api/historico/hoje")
@verificar_token
def historico_hoje():
    """Retorna fotos e KMs do dia atual para o usuário logado."""
    try:
        semana_id = request.args.get("semana_id")
        hoje      = date.today().isoformat()

        # Fotos do dia
        if semana_id:
            fotos_raw = query("""
                SELECT f.id, f.tipo, f.storage_path, f.filename_orig,
                       p.local_nome, f.criado_em
                FROM jardinagem.fotos f
                JOIN jardinagem.pares p ON p.id = f.par_id
                WHERE p.semana_id = %s
                  AND f.enviado_por = %s
                  AND DATE(f.criado_em) = %s
                ORDER BY f.criado_em DESC
            """, (semana_id, g.usuario["sub"], hoje))
        else:
            fotos_raw = query("""
                SELECT f.id, f.tipo, f.storage_path, f.filename_orig,
                       p.local_nome, f.criado_em
                FROM jardinagem.fotos f
                JOIN jardinagem.pares p ON p.id = f.par_id
                WHERE f.enviado_por = %s
                  AND DATE(f.criado_em) = %s
                ORDER BY f.criado_em DESC
            """, (g.usuario["sub"], hoje))

        fotos = []
        for f in fotos_raw:
            fd = dict(f)
            fd["url"] = storage_url(f["storage_path"]) if f["storage_path"] else ""
            fd["criado_em"] = f["criado_em"].isoformat() if f["criado_em"] else ""
            fotos.append(fd)

        # KMs do dia
        if semana_id:
            km_raw = query("""
                SELECT id, data, local_nome, km_inicial, km_final,
                       hora_inicio, hora_fim, observacao, criado_em
                FROM jardinagem.relatorios_diarios
                WHERE semana_id = %s
                  AND usuario_id = %s
                  AND data = %s
                ORDER BY criado_em DESC
            """, (semana_id, g.usuario["sub"], hoje))
        else:
            km_raw = query("""
                SELECT id, data, local_nome, km_inicial, km_final,
                       hora_inicio, hora_fim, observacao, criado_em
                FROM jardinagem.relatorios_diarios
                WHERE usuario_id = %s
                  AND data = %s
                ORDER BY criado_em DESC
            """, (g.usuario["sub"], hoje))

        km_list = []
        km_total = 0.0
        for r in km_raw:
            rd = dict(r)
            ini = float(r["km_inicial"] or 0)
            fin = float(r["km_final"]   or 0)
            rd["km_percorrido"] = round(fin - ini, 1)
            rd["km_inicial"]    = ini
            rd["km_final"]      = fin
            rd["hora_inicio"]   = str(r["hora_inicio"]) if r["hora_inicio"] else ""
            rd["hora_fim"]      = str(r["hora_fim"])    if r["hora_fim"]    else ""
            rd["criado_em"]     = r["criado_em"].isoformat() if r["criado_em"] else ""
            km_total           += rd["km_percorrido"]
            km_list.append(rd)

        return jsonify({
            "data":     hoje,
            "fotos":    fotos,
            "km":       km_list,
            "km_total": round(km_total, 1),
        })

    except Exception as e:
        log.error(f"historico_hoje erro: {e}")
        return jsonify({"erro": str(e)}), 500
