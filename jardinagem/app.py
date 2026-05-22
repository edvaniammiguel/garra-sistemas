"""
Garra Terraplenagem — Sistema de Fotos Jardinagem
Backend Flask + Supabase
"""

import os, io, uuid, json, calendar, base64
from datetime import datetime, timedelta, date
from functools import wraps

import bcrypt
import jwt
from flask import Flask, request, jsonify, send_from_directory, g
from PIL import Image
from dotenv import load_dotenv
from supabase import create_client, Client
from apscheduler.schedulers.background import BackgroundScheduler

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────
SUPABASE_URL        = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY= os.getenv("SUPABASE_SERVICE_KEY")
JWT_SECRET          = os.getenv("JWT_SECRET", "dev-secret-troque")
JWT_EXPIRY_HOURS    = int(os.getenv("JWT_EXPIRY_HOURS", 8))
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
BUCKET_NAME         = "jardinagem-fotos"

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50MB por request

# ── SUPABASE CLIENT ───────────────────────────────────────────
sb: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


# ══════════════════════════════════════════════════════════════
# AUTH — JWT
# ══════════════════════════════════════════════════════════════

def gerar_token(usuario: dict) -> str:
    payload = {
        "sub":    str(usuario["id"]),
        "nome":   usuario["nome"],
        "perfil": usuario["perfil"],
        "exp":    datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def verificar_token(f):
    """Decorator — protege rotas. Injeta g.usuario."""
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
    """Decorator — restringe acesso por perfil."""
    def decorator(f):
        @wraps(f)
        @verificar_token
        def decorated(*args, **kwargs):
            if g.usuario.get("perfil") not in perfis:
                return jsonify({"erro": "Sem permissão"}), 403
            return f(*args, **kwargs)
        return decorated
    return decorator


# ══════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════

def next_code(n: int = 2) -> int:
    """Retorna o próximo código sequencial e avança n posições."""
    row = sb.schema("jardinagem").table("config").select("valor").eq("chave", "next_code").single().execute()
    atual = int(row.data["valor"])
    sb.schema("jardinagem").table("config").update({"valor": str(atual + n)}).eq("chave", "next_code").execute()
    return atual


def comprimir_imagem(dados: bytes, max_px: int = 1400, qualidade: int = 82) -> bytes:
    """Redimensiona e comprime imagem mantendo proporção."""
    img = Image.open(io.BytesIO(dados))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=qualidade, optimize=True)
    return buf.getvalue()


def upload_supabase(dados: bytes, filename: str) -> str:
    """Faz upload no Supabase Storage e retorna o path."""
    path = f"{datetime.now().strftime('%Y/%m')}/{filename}"
    sb.storage.from_(BUCKET_NAME).upload(
        path,
        dados,
        {"content-type": "image/jpeg", "upsert": "true"}
    )
    return path


def url_assinada(path: str, segundos: int = 3600) -> str:
    """Gera URL assinada para acesso à foto."""
    res = sb.storage.from_(BUCKET_NAME).create_signed_url(path, segundos)
    return res.get("signedURL", "")


def semanas_do_mes(ano: int, mes: int, mes_id: int):
    """Cria as 4 semanas automáticas de um mês."""
    _, ultimo_dia = calendar.monthrange(ano, mes)
    intervalos = [(1, 7), (8, 14), (15, 21), (22, ultimo_dia)]
    for i, (ini, fim) in enumerate(intervalos):
        label = f"Semana {i+1} — {ini:02d}/{mes:02d} a {fim:02d}/{mes:02d}/{ano}"
        sb.schema("jardinagem").table("semanas").insert({
            "mes_id":   mes_id,
            "label":    label,
            "data_ini": f"{ano}-{mes:02d}-{ini:02d}",
            "data_fim": f"{ano}-{mes:02d}-{fim:02d}",
            "ordem":    i,
            "status":   "aberta"
        }).execute()


def semana_ativa(mes_id: int) -> dict | None:
    """Retorna a semana cujo intervalo cobre a data de hoje."""
    hoje = date.today().isoformat()
    res = sb.schema("jardinagem").table("semanas")\
        .select("*")\
        .eq("mes_id", mes_id)\
        .lte("data_ini", hoje)\
        .gte("data_fim", hoje)\
        .limit(1).execute()
    return res.data[0] if res.data else None


# ══════════════════════════════════════════════════════════════
# AGENDADOR — cria mês seguinte no dia 25
# ══════════════════════════════════════════════════════════════

def criar_proximo_mes():
    hoje = date.today()
    if hoje.day != 25:
        return
    proximo = (hoje.replace(day=1) + timedelta(days=32)).replace(day=1)
    ano, mes = proximo.year, proximo.month
    nomes = ["Janeiro","Fevereiro","Março","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    label = f"{nomes[mes-1]}/{ano}"
    try:
        res = sb.schema("jardinagem").table("meses").insert({
            "ano": ano, "mes": mes, "label": label
        }).execute()
        if res.data:
            mes_id = res.data[0]["id"]
            semanas_do_mes(ano, mes, mes_id)
            print(f"[Agendador] {label} criado com 4 semanas.")
    except Exception as e:
        print(f"[Agendador] Erro ao criar mês: {e}")


scheduler = BackgroundScheduler()
scheduler.add_job(criar_proximo_mes, "cron", hour=8, minute=0)
scheduler.start()


# ══════════════════════════════════════════════════════════════
# ROTAS — STATIC / FRONTEND
# ══════════════════════════════════════════════════════════════

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

@app.route("/static/<path:fn>")
def static_files(fn):
    return send_from_directory("static", fn)


# ══════════════════════════════════════════════════════════════
# ROTAS — AUTH
# ══════════════════════════════════════════════════════════════

@app.route("/api/login", methods=["POST"])
def login():
    d = request.json or {}
    email = (d.get("email") or "").strip().lower()
    senha = (d.get("senha") or "").encode()

    res = sb.table("usuarios").select("*").eq("email", email).eq("ativo", True).limit(1).execute()
    if not res.data:
        return jsonify({"erro": "Credenciais inválidas"}), 401

    usuario = res.data[0]
    if not bcrypt.checkpw(senha, usuario["senha_hash"].encode()):
        return jsonify({"erro": "Credenciais inválidas"}), 401

    token = gerar_token(usuario)
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


# ══════════════════════════════════════════════════════════════
# ROTAS — MESES
# ══════════════════════════════════════════════════════════════

@app.route("/api/meses", methods=["GET"])
@verificar_token
def list_meses():
    res = sb.schema("jardinagem").table("meses").select("*").order("ano", desc=True).order("mes", desc=True).execute()
    meses = res.data
    # Conta semanas e fotos para cada mês
    for m in meses:
        sems = sb.schema("jardinagem").table("semanas").select("id").eq("mes_id", m["id"]).execute()
        m["total_semanas"] = len(sems.data)
    return jsonify(meses)


@app.route("/api/meses", methods=["POST"])
@requer_perfil("admin", "luana")
def criar_mes():
    d = request.json or {}
    ano, mes = int(d["ano"]), int(d["mes"])
    nomes = ["","Janeiro","Fevereiro","Março","Abril","Maio","Junho",
             "Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    label = d.get("label") or f"{nomes[mes]}/{ano}"

    try:
        res = sb.schema("jardinagem").table("meses").insert(
            {"ano": ano, "mes": mes, "label": label,
             "cliente_id": d.get("cliente_id")}
        ).execute()
        mes_id = res.data[0]["id"]
    except Exception:
        # mês já existe
        res = sb.schema("jardinagem").table("meses").select("*").eq("ano", ano).eq("mes", mes).single().execute()
        mes_id = res.data["id"]

    # Cria as 4 semanas automaticamente
    sem_exist = sb.schema("jardinagem").table("semanas").select("id").eq("mes_id", mes_id).execute()
    if not sem_exist.data:
        semanas_do_mes(ano, mes, mes_id)

    mes_data = sb.schema("jardinagem").table("meses").select("*").eq("id", mes_id).single().execute()
    return jsonify(mes_data.data), 201


@app.route("/api/meses/<int:mid>")
@verificar_token
def get_mes(mid):
    m = sb.schema("jardinagem").table("meses").select("*").eq("id", mid).single().execute()
    if not m.data:
        return jsonify({"erro": "Não encontrado"}), 404

    sems = sb.schema("jardinagem").table("semanas").select("*")\
        .eq("mes_id", mid).order("ordem").execute()

    result = m.data
    result["semanas"] = []

    for s in sems.data:
        sd = dict(s)
        pares_res = sb.schema("jardinagem").table("pares").select("*")\
            .eq("semana_id", s["id"]).order("ordem").execute()
        sd["pares"] = []

        for p in pares_res.data:
            pd = dict(p)
            fotos_res = sb.schema("jardinagem").table("fotos").select("*")\
                .eq("par_id", p["id"]).execute()

            pd["fotos"] = []
            for f in fotos_res.data:
                fd = dict(f)
                fd["url"] = url_assinada(f["storage_path"])
                pd["fotos"].append(fd)

            sd["pares"].append(pd)
        result["semanas"].append(sd)

    return jsonify(result)


# ══════════════════════════════════════════════════════════════
# ROTAS — SEMANAS
# ══════════════════════════════════════════════════════════════

@app.route("/api/semanas/ativa")
@verificar_token
def semana_ativa_route():
    """Retorna a semana que corresponde à data de hoje."""
    hoje = date.today().isoformat()
    res = sb.schema("jardinagem").table("semanas")\
        .select("*, meses(id, ano, mes, label)")\
        .lte("data_ini", hoje)\
        .gte("data_fim", hoje)\
        .limit(1).execute()
    if not res.data:
        return jsonify({"erro": "Sem semana ativa"}), 404
    return jsonify(res.data[0])


@app.route("/api/semanas/<int:sid>", methods=["PATCH"])
@requer_perfil("admin", "luana")
def patch_semana(sid):
    d = request.json or {}
    campos = {k: v for k, v in d.items() if k in ("label", "status", "enviado_em")}
    if campos:
        sb.schema("jardinagem").table("semanas").update(campos).eq("id", sid).execute()
    return jsonify({"ok": True})


@app.route("/api/semanas/<int:sid>", methods=["DELETE"])
@requer_perfil("admin", "luana")
def del_semana(sid):
    # Apaga fotos do storage antes
    pares = sb.schema("jardinagem").table("pares").select("id").eq("semana_id", sid).execute()
    for p in pares.data:
        fotos = sb.schema("jardinagem").table("fotos").select("storage_path").eq("par_id", p["id"]).execute()
        for f in fotos.data:
            try:
                sb.storage.from_(BUCKET_NAME).remove([f["storage_path"]])
            except Exception:
                pass
    sb.schema("jardinagem").table("semanas").delete().eq("id", sid).execute()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════
# ROTAS — PARES
# ══════════════════════════════════════════════════════════════

@app.route("/api/pares", methods=["POST"])
@verificar_token
def criar_par():
    d = request.json or {}
    cod = next_code(2)
    res = sb.schema("jardinagem").table("pares").insert({
        "semana_id":  d["semana_id"],
        "codigo_a":   cod,
        "codigo_d":   cod + 1,
        "local_nome": d.get("local_nome", ""),
        "data_label": d.get("data_label", ""),
        "ordem":      d.get("ordem", 0)
    }).execute()
    return jsonify(res.data[0]), 201


@app.route("/api/pares/<int:pid>", methods=["PATCH"])
@verificar_token
def patch_par(pid):
    d = request.json or {}
    campos = {k: v for k, v in d.items() if k in ("local_nome", "ordem", "semana_id", "data_label")}
    if campos:
        sb.schema("jardinagem").table("pares").update(campos).eq("id", pid).execute()
    return jsonify({"ok": True})


@app.route("/api/pares/<int:pid>", methods=["DELETE"])
@verificar_token
def del_par(pid):
    fotos = sb.schema("jardinagem").table("fotos").select("storage_path").eq("par_id", pid).execute()
    for f in fotos.data:
        try:
            sb.storage.from_(BUCKET_NAME).remove([f["storage_path"]])
        except Exception:
            pass
    sb.schema("jardinagem").table("pares").delete().eq("id", pid).execute()
    return jsonify({"ok": True})


# ══════════════════════════════════════════════════════════════
# ROTAS — FOTOS (desktop — Luana)
# ══════════════════════════════════════════════════════════════

@app.route("/api/fotos/avulsa", methods=["POST"])
@requer_perfil("admin", "luana")
def foto_avulsa():
    """Upload de foto pelo desktop (Luana). Substitui se já existir."""
    par_id = int(request.form["par_id"])
    tipo   = request.form["tipo"]
    file   = request.files["foto"]

    dados = comprimir_imagem(file.read())
    fn    = f"{uuid.uuid4().hex}.jpg"
    path  = upload_supabase(dados, fn)

    # Remove foto anterior do mesmo slot
    antiga = sb.schema("jardinagem").table("fotos")\
        .select("id, storage_path").eq("par_id", par_id).eq("tipo", tipo).execute()
    if antiga.data:
        try:
            sb.storage.from_(BUCKET_NAME).remove([antiga.data[0]["storage_path"]])
        except Exception:
            pass
        sb.schema("jardinagem").table("fotos").delete().eq("id", antiga.data[0]["id"]).execute()

    res = sb.schema("jardinagem").table("fotos").insert({
        "par_id":       par_id,
        "tipo":         tipo,
        "origem":       "desktop",
        "enviado_por":  g.usuario["sub"],
        "storage_path": path,
        "filename_orig": file.filename,
        "sincronizado": True
    }).execute()

    foto = res.data[0]
    foto["url"] = url_assinada(path)
    return jsonify(foto), 201


# ══════════════════════════════════════════════════════════════
# ROTAS — FOTOS MOBILE (Arthur / Breno)
# ══════════════════════════════════════════════════════════════

@app.route("/api/fotos/mobile", methods=["POST"])
@requer_perfil("admin", "campo", "luana")
def foto_mobile():
    """
    Upload de foto pelo celular.
    Aceita multipart/form-data com:
      - foto: arquivo da imagem
      - semana_id: id da semana (ou 'ativa' para calcular)
      - local_nome: nome do local (opcional)
      - tipo: 'antes' | 'depois'
      - offline_id: UUID gerado no celular (idempotência)
    """
    semana_id_raw = request.form.get("semana_id", "ativa")
    tipo       = request.form.get("tipo", "antes")
    local_nome = request.form.get("local_nome", "")
    offline_id = request.form.get("offline_id", "")
    file       = request.files.get("foto")

    if not file:
        return jsonify({"erro": "Foto não enviada"}), 400

    # Resolve semana ativa se não informada
    if semana_id_raw == "ativa" or not semana_id_raw:
        hoje = date.today().isoformat()
        res = sb.schema("jardinagem").table("semanas")\
            .select("id").lte("data_ini", hoje).gte("data_fim", hoje).limit(1).execute()
        if not res.data:
            return jsonify({"erro": "Nenhuma semana ativa no momento"}), 404
        semana_id = res.data[0]["id"]
    else:
        semana_id = int(semana_id_raw)

    # Idempotência — se offline_id já foi processado, retorna ok
    if offline_id:
        exist = sb.schema("jardinagem").table("fila_sync")\
            .select("id").eq("id", offline_id).eq("processado", True).execute()
        if exist.data:
            return jsonify({"ok": True, "duplicado": True}), 200

    # Comprime e faz upload
    dados = comprimir_imagem(file.read(), max_px=1400, qualidade=80)
    fn    = f"{uuid.uuid4().hex}.jpg"
    path  = upload_supabase(dados, fn)

    # Cria par novo se local_nome informado, senão adiciona ao último par sem depois
    par_id = None
    if local_nome:
        cod = next_code(2)
        res_par = sb.schema("jardinagem").table("pares").insert({
            "semana_id":  semana_id,
            "codigo_a":   cod,
            "codigo_d":   cod + 1,
            "local_nome": local_nome,
            "ordem":      99
        }).execute()
        par_id = res_par.data[0]["id"]
    else:
        # Busca par aberto (sem foto do tipo solicitado)
        pares = sb.schema("jardinagem").table("pares")\
            .select("id").eq("semana_id", semana_id).order("criado_em", desc=True).limit(10).execute()
        for p in pares.data:
            fotos = sb.schema("jardinagem").table("fotos")\
                .select("id").eq("par_id", p["id"]).eq("tipo", tipo).execute()
            if not fotos.data:
                par_id = p["id"]
                break

        if not par_id:
            cod = next_code(2)
            res_par = sb.schema("jardinagem").table("pares").insert({
                "semana_id": semana_id,
                "codigo_a":  cod,
                "codigo_d":  cod + 1,
                "ordem":     99
            }).execute()
            par_id = res_par.data[0]["id"]

    # Salva foto
    res_foto = sb.schema("jardinagem").table("fotos").insert({
        "par_id":       par_id,
        "tipo":         tipo,
        "origem":       "mobile",
        "enviado_por":  g.usuario["sub"],
        "storage_path": path,
        "filename_orig": file.filename,
        "sincronizado": True
    }).execute()

    # Marca fila como processado
    if offline_id:
        sb.schema("jardinagem").table("fila_sync").upsert({
            "id":          offline_id,
            "usuario_id":  g.usuario["sub"],
            "semana_id":   semana_id,
            "local_nome":  local_nome,
            "tipo":        tipo,
            "storage_path": path,
            "processado":  True
        }).execute()

    foto = res_foto.data[0]
    foto["url"] = url_assinada(path)
    return jsonify(foto), 201


@app.route("/api/fotos/<int:fid>", methods=["DELETE"])
@verificar_token
def del_foto(fid):
    f = sb.schema("jardinagem").table("fotos").select("storage_path").eq("id", fid).single().execute()
    if f.data:
        try:
            sb.storage.from_(BUCKET_NAME).remove([f.data["storage_path"]])
        except Exception:
            pass
        sb.schema("jardinagem").table("fotos").delete().eq("id", fid).execute()
    return jsonify({"ok": True})


@app.route("/api/fotos/<int:fid>/url")
@verificar_token
def url_foto(fid):
    """Gera URL assinada fresca para uma foto."""
    f = sb.schema("jardinagem").table("fotos").select("storage_path").eq("id", fid).single().execute()
    if not f.data:
        return jsonify({"erro": "Não encontrado"}), 404
    return jsonify({"url": url_assinada(f.data["storage_path"])})


# ══════════════════════════════════════════════════════════════
# ROTAS — ANÁLISE IA
# ══════════════════════════════════════════════════════════════

@app.route("/api/ia/analisar", methods=["POST"])
@requer_perfil("admin", "luana")
def ia_analisar():
    """Analisa uma foto com Claude Vision e retorna metadados."""
    if not ANTHROPIC_API_KEY:
        return jsonify({"erro": "ANTHROPIC_API_KEY não configurada"}), 400

    import anthropic as ant

    foto_id  = request.json.get("foto_id")
    fila     = request.json.get("storage_path")

    path = fila
    if foto_id:
        f = sb.schema("jardinagem").table("fotos").select("storage_path").eq("id", foto_id).single().execute()
        path = f.data["storage_path"]

    # Baixa imagem do storage
    img_bytes = sb.storage.from_(BUCKET_NAME).download(path)
    img_b64   = base64.standard_b64encode(img_bytes).decode()

    client = ant.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=300,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
                },
                {
                    "type": "text",
                    "text": (
                        "Analise esta foto de jardinagem/área verde urbana. "
                        "Responda APENAS em JSON com as chaves: "
                        "local (nome descritivo do local, ex: 'Canteiro Rua Principal'), "
                        "estado ('antes' ou 'depois' baseado na aparência), "
                        "id_visual (string curta identificando este local para agrupar com outras fotos do mesmo lugar, "
                        "use cor de muro, tipo de vegetação, estruturas fixas — ex: 'muro-amarelo-arvore-grande'), "
                        "descricao (1 frase descrevendo o estado). "
                        "Sem markdown, apenas JSON puro."
                    )
                }
            ]
        }]
    )

    try:
        dados = json.loads(msg.content[0].text)
    except Exception:
        dados = {"local": "", "estado": "", "id_visual": "", "descricao": msg.content[0].text}

    # Atualiza no banco se foto_id informado
    if foto_id:
        sb.schema("jardinagem").table("fotos").update({
            "ia_descricao": dados.get("descricao", ""),
            "ia_local":     dados.get("local", ""),
            "ia_estado":    dados.get("estado", ""),
            "ia_id_visual": dados.get("id_visual", "")
        }).eq("id", foto_id).execute()

    return jsonify(dados)


# ══════════════════════════════════════════════════════════════
# ROTAS — CONFIG
# ══════════════════════════════════════════════════════════════

@app.route("/api/config")
@requer_perfil("admin", "luana")
def get_config():
    res = sb.schema("jardinagem").table("config").select("*").execute()
    return jsonify({r["chave"]: r["valor"] for r in res.data})


@app.route("/api/clientes")
@verificar_token
def list_clientes():
    res = sb.table("clientes").select("id, nome").eq("ativo", True).execute()
    return jsonify(res.data)


# ══════════════════════════════════════════════════════════════
# INIT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("\n🌿 Garra — Sistema de Fotos Jardinagem")
    print("   Backend: Flask + Supabase")
    print("   Acesse:  http://localhost:5000\n")
    app.run(host="0.0.0.0", port=5000, debug=False)
