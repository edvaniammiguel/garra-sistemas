# ═══════════════════════════════════════════════════════════
# main.py — API Garra Gestão v6 — UNIFICADO
# FastAPI + asyncpg + Neon PostgreSQL
# Módulos: Checklist + Jardinagem
# ═══════════════════════════════════════════════════════════

from fastapi import FastAPI, HTTPException, Depends, Header, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, bcrypt, os, json, time, secrets, smtplib, uuid, io, calendar
import psycopg2, psycopg2.extras
import requests as req_lib
from datetime import datetime, timedelta, date
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from PIL import Image

app = FastAPI(title="Garra Gestão API", version="6.0.0")

# ── CONFIG ────────────────────────────────────────────────────
DATABASE_URL         = os.environ.get("DATABASE_URL", "")
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
JWT_SECRET           = os.environ.get("JWT_SECRET", "dev-secret")
JWT_EXPIRY_HOURS     = int(os.environ.get("JWT_EXPIRY_HOURS", "8"))
MAIL_USERNAME        = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD        = os.environ.get("MAIL_PASSWORD", "")
MAIL_HOST            = os.environ.get("MAIL_HOST", "smtp.hostinger.com")
MAIL_PORT            = int(os.environ.get("MAIL_PORT", "587"))
MAIL_DESTINO         = os.environ.get("MAIL_DESTINO", "")
MAIL_CC              = os.environ.get("MAIL_CC", "")
FRONTEND_URL         = os.environ.get("FRONTEND_URL", "https://garra-checklist-app.onrender.com")
BUCKET_NAME          = "jardinagem-fotos"

# ── RATE LIMITER ──────────────────────────────────────────────
_login_attempts: dict = defaultdict(list)
MAX_ATTEMPTS = 10
WINDOW_SECS  = 300

def check_rate_limit(ip: str):
    now  = time.time()
    reqs = [t for t in _login_attempts[ip] if now - t < WINDOW_SECS]
    _login_attempts[ip] = reqs
    if len(reqs) >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429,
            detail=f"Muitas tentativas. Aguarde {WINDOW_SECS//60} minutos.")
    _login_attempts[ip].append(now)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://garra-checklist-app.onrender.com",
        "https://garra-sistemas.onrender.com",
        "https://garra-jardinagem.onrender.com",
        "http://localhost:3000",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["GET","POST","DELETE","PATCH","PUT"],
    allow_headers=["Content-Type","Authorization"],
)

# ── SECURITY HEADERS ──────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "SAMEORIGIN"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    return response

# ══════════════════════════════════════════════════════════════
# BANCO — DOIS MODOS
# asyncpg (async) → checklist
# psycopg2 (sync) → jardinagem (mantém compatibilidade)
# ══════════════════════════════════════════════════════════════

# ── asyncpg (checklist) ───────────────────────────────────────
async def get_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

# ── psycopg2 (jardinagem) ─────────────────────────────────────
import threading
_local = threading.local()

def get_jard_db():
    """Conexão psycopg2 para rotas síncronas do jardinagem."""
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
        sslmode="require",
        connect_timeout=10
    )
    return conn

def jard_query(sql, params=None, fetch="all"):
    conn = get_jard_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
            if fetch == "one":  return cur.fetchone()
            if fetch == "all":  return cur.fetchall()
            if fetch == "none": return None
    finally:
        conn.close()

def jard_query_id(sql, params=None):
    conn = get_jard_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql + " RETURNING *", params or ())
            conn.commit()
            return cur.fetchone()
    finally:
        conn.close()

# ── STARTUP ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute("SET search_path TO public, checklist, jardinagem")
        print("Garra Gestao v6 - banco unificado conectado")
        print("JARD_DIR:", JARD_DIR)
        print("TEMPLATES exists:", os.path.exists(TEMPLATES_DIR))
        print("STATIC exists:", os.path.exists(STATIC_DIR))
    except Exception as e:
        print("Erro no startup:", e)
    finally:
        await conn.close()

# ── STATIC FILES — JARDINAGEM ─────────────────────────────────
# checklist/api/main.py → checklist/ → raiz → jardinagem/
JARD_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "jardinagem")
JARD_DIR = os.path.abspath(JARD_DIR)
STATIC_DIR    = os.path.join(JARD_DIR, "static")
TEMPLATES_DIR = os.path.join(JARD_DIR, "templates")

if os.path.exists(STATIC_DIR):
    app.mount("/jardinagem/static", StaticFiles(directory=STATIC_DIR), name="jard_static")

# ── SUPABASE STORAGE ──────────────────────────────────────────
def storage_upload(dados: bytes, path: str) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("Supabase não configurado")
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{path}"
    r = req_lib.post(url, headers={
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "image/jpeg",
        "x-upsert": "true"
    }, data=dados, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload falhou [{r.status_code}]: {r.text}")
    return path

def storage_url(path: str, segundos: int = 3600) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not path:
        return ""
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET_NAME}/{path}",
            headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            json={"expiresIn": segundos}, timeout=10
        )
        if r.status_code == 200:
            return f"{SUPABASE_URL}/storage/v1{r.json().get('signedURL','')}"
    except: pass
    return ""

def storage_delete(paths: list):
    if not paths or not SUPABASE_URL: return
    try:
        req_lib.delete(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}",
            headers={"Authorization": f"Bearer {SUPABASE_SERVICE_KEY}"},
            json={"prefixes": paths}, timeout=10
        )
    except: pass

# ── JWT (jardinagem) ──────────────────────────────────────────
import jwt as pyjwt

def gerar_token_jard(usuario: dict) -> str:
    return pyjwt.encode({
        "sub":    str(usuario["id"]),
        "nome":   usuario["nome"],
        "perfil": usuario["perfil"],
        "email":  usuario.get("email",""),
        "exp":    datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }, JWT_SECRET, algorithm="HS256")

def verificar_token_jard(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Não autenticado")
    token = authorization[7:]
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessão expirada")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

# ── HELPERS JARDINAGEM ────────────────────────────────────────
def comprimir_imagem(dados: bytes, max_px: int = 1400, qualidade: int = 82) -> bytes:
    img = Image.open(io.BytesIO(dados))
    if img.mode not in ("RGB","L"):
        img = img.convert("RGB")
    img.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=qualidade, optimize=True)
    return buf.getvalue()

def next_code(n: int = 2) -> int:
    row = jard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    atual = int(row["valor"])
    jard_query("UPDATE jardinagem.config SET valor=%s WHERE chave='next_code'",
               (str(atual + n),), fetch="none")
    return atual

def semanas_do_mes(ano: int, mes: int, mes_id: int):
    _, ultimo_dia = calendar.monthrange(ano, mes)
    intervalos = [(1,7),(8,14),(15,21),(22,ultimo_dia)]
    for i, (ini, fim) in enumerate(intervalos):
        label = f"Semana {i+1} — {ini:02d}/{mes:02d} a {fim:02d}/{mes:02d}/{ano}"
        jard_query("""INSERT INTO jardinagem.semanas
                      (mes_id,label,data_ini,data_fim,ordem,status)
                      VALUES (%s,%s,%s,%s,%s,'aberta')""",
                   (mes_id, label,
                    f"{ano}-{mes:02d}-{ini:02d}",
                    f"{ano}-{mes:02d}-{fim:02d}", i), fetch="none")

def enviar_email_smtp(destino: str, assunto: str, corpo_html: str, anexos: list = None):
    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"]    = f"Garra Terraplenagem <{MAIL_USERNAME}>"
    msg["To"]      = destino
    if MAIL_CC:
        msg["Cc"] = MAIL_CC
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    if anexos:
        for nome, dados in anexos:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(dados)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{nome}"')
            msg.attach(part)
    destinatarios = [destino] + ([MAIL_CC] if MAIL_CC else [])
    with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as s:
        s.ehlo(); s.starttls()
        s.login(MAIL_USERNAME, MAIL_PASSWORD)
        s.sendmail(MAIL_USERNAME, destinatarios, msg.as_string())

# ── PYDANTIC MODELS ───────────────────────────────────────────
class LoginRequest(BaseModel):
    login: str
    senha: str

class UsuarioCreate(BaseModel):
    login: str; nome: str; email: str; senha: str
    perfil: str; perfil_checklist: Optional[str] = None

class UsuarioEdit(BaseModel):
    nome: Optional[str] = None; email: Optional[str] = None
    perfil: Optional[str] = None; perfil_checklist: Optional[str] = None
    ativo: Optional[bool] = None

class SenhaChange(BaseModel):
    senha_atual: str; senha_nova: str

class SenhaResetRequest(BaseModel):
    login: str

class SenhaResetConfirm(BaseModel):
    token: str; senha_nova: str

class EnvioCreate(BaseModel):
    envio_id: str; usuario_login: str; usuario_nome: str
    cl_id: str; cl_label: Optional[str] = ""
    meta: dict = {}; respostas: dict = {}
    pts: int = 0; tem_nc: bool = False; total_nc: int = 0
    enviado_em: Optional[str] = None

class FrotaItem(BaseModel):
    categoria: str; identificacao: str; descricao: Optional[str] = ""

class ChecklistModeloCreate(BaseModel):
    cl_id: str; label: str; icon: str = "📋"
    descricao: Optional[str] = ""; vehicle_cat: Optional[str] = ""
    is_default: bool = False; score_full: int = 100
    score_nc: int = 60; score_obs: int = 20; score_ontime: int = 10
    questions: List[dict] = []; steps: List[dict] = []

class LogMotoristaCreate(BaseModel):
    motor_id: str; nome: str; cpf: Optional[str] = ""
    cnh: Optional[str] = ""; telefone: Optional[str] = ""
    status: str = "ativo"; observacoes: Optional[str] = ""

class LogVeiculoCreate(BaseModel):
    veiculo_id: str; car_id: str; placa: Optional[str] = ""
    modelo: Optional[str] = ""; ano: Optional[int] = None
    cor: Optional[str] = ""; status: str = "disponivel"
    extras: List[dict] = []; observacoes: Optional[str] = ""

class LogRegistroCreate(BaseModel):
    registro_id: str; responsavel: str
    data_hora: str; carros: List[dict] = []

def validar_senha(senha: str) -> Optional[str]:
    if len(senha) < 6: return "Senha deve ter no mínimo 6 caracteres."
    return None

# ══════════════════════════════════════════════════════════════
# ROTAS CHECKLIST — iguais ao main.py original
# ══════════════════════════════════════════════════════════════

@app.post("/auth/login")
async def login(req: LoginRequest, request: Request, db=Depends(get_db)):
    check_rate_limit(request.client.host)
    user = await db.fetchrow(
        "SELECT * FROM public.usuarios_garra WHERE login=$1 AND ativo=TRUE", req.login
    )
    if not user or not bcrypt.checkpw(req.senha.encode(), user["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    return {
        "login": user["login"], "nome": user["nome"],
        "perfil": user["perfil"], "perfil_checklist": user["perfil_checklist"],
        "role": user["perfil_checklist"] or user["perfil"],
        "pts": user["pts"] or 0, "total_envios": user["total_envios"] or 0,
        "email": user["email"] or "",
    }

@app.post("/auth/solicitar-reset")
async def solicitar_reset(req: SenhaResetRequest, db=Depends(get_db)):
    user = await db.fetchrow(
        "SELECT id, nome, email FROM public.usuarios_garra WHERE login=$1 AND ativo=TRUE", req.login
    )
    if not user or not user["email"]:
        return {"ok": True, "msg": "Se o usuário existir, um email será enviado."}
    token = secrets.token_urlsafe(32)
    await db.execute(
        "INSERT INTO public.senha_reset_tokens (usuario_id, token) VALUES ($1,$2)",
        user["id"], token
    )
    link = f"{FRONTEND_URL}/reset-senha.html?token={token}"
    corpo = f"""<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">
      <div style="background:#1A2A5E;padding:20px;border-bottom:3px solid #E8820C;">
        <h2 style="color:#fff;margin:0;">Garra Terraplenagem</h2></div>
      <div style="padding:24px;background:#F0F4FF;">
        <p>Olá, <strong>{user['nome']}</strong>!</p>
        <p>Recebemos uma solicitação para redefinir sua senha.</p>
        <p style="margin:24px 0;">
          <a href="{link}" style="background:#1A2A5E;color:#fff;padding:12px 24px;
             border-radius:8px;text-decoration:none;font-weight:bold;">Redefinir minha senha</a></p>
        <p style="color:#64748B;font-size:12px;">Este link expira em 1 hora.</p>
      </div></div>"""
    try:
        enviar_email_smtp(user["email"], "Redefinição de senha — Garra Gestão", corpo)
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao enviar email.")
    return {"ok": True, "msg": "Email enviado com sucesso."}

@app.post("/auth/confirmar-reset")
async def confirmar_reset(req: SenhaResetConfirm, db=Depends(get_db)):
    erro = validar_senha(req.senha_nova)
    if erro: raise HTTPException(status_code=400, detail=erro)
    token_row = await db.fetchrow(
        """SELECT t.*, u.id as uid FROM public.senha_reset_tokens t
           JOIN public.usuarios_garra u ON u.id=t.usuario_id
           WHERE t.token=$1 AND t.usado=FALSE AND t.expira_em > NOW()""", req.token
    )
    if not token_row: raise HTTPException(status_code=400, detail="Token inválido ou expirado.")
    novo_hash = bcrypt.hashpw(req.senha_nova.encode(), bcrypt.gensalt(12)).decode()
    await db.execute(
        "UPDATE public.usuarios_garra SET senha_hash=$1, atualizado_em=NOW() WHERE id=$2",
        novo_hash, token_row["uid"]
    )
    await db.execute("UPDATE public.senha_reset_tokens SET usado=TRUE WHERE token=$1", req.token)
    return {"ok": True, "msg": "Senha redefinida com sucesso."}

@app.post("/auth/alterar-senha")
async def alterar_senha(req: SenhaChange, login: str, db=Depends(get_db)):
    erro = validar_senha(req.senha_nova)
    if erro: raise HTTPException(status_code=400, detail=erro)
    user = await db.fetchrow("SELECT * FROM public.usuarios_garra WHERE login=$1", login)
    if not user: raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not bcrypt.checkpw(req.senha_atual.encode(), user["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Senha atual incorreta")
    novo_hash = bcrypt.hashpw(req.senha_nova.encode(), bcrypt.gensalt(12)).decode()
    await db.execute(
        "UPDATE public.usuarios_garra SET senha_hash=$1, atualizado_em=NOW() WHERE login=$2",
        novo_hash, login
    )
    return {"ok": True}

@app.get("/usuarios")
async def listar_usuarios(db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT login,nome,email,perfil,perfil_checklist,pts,total_envios,ativo,criado_em FROM public.usuarios_garra ORDER BY nome"
    )
    return [dict(r) for r in rows]

@app.post("/usuarios")
async def criar_usuario(u: UsuarioCreate, db=Depends(get_db)):
    erro = validar_senha(u.senha)
    if erro: raise HTTPException(status_code=400, detail=erro)
    existe = await db.fetchval(
        "SELECT id FROM public.usuarios_garra WHERE login=$1 OR email=$2", u.login, u.email
    )
    if existe: raise HTTPException(status_code=409, detail="Login ou email já cadastrado.")
    hash_senha = bcrypt.hashpw(u.senha.encode(), bcrypt.gensalt(12)).decode()
    await db.execute(
        "INSERT INTO public.usuarios_garra (login,nome,email,senha_hash,perfil,perfil_checklist) VALUES ($1,$2,$3,$4,$5,$6)",
        u.login, u.nome, u.email, hash_senha, u.perfil, u.perfil_checklist
    )
    return {"ok": True}

@app.post("/usuarios/{login}/editar")
async def editar_usuario(login: str, dados: UsuarioEdit, db=Depends(get_db)):
    sets, params = [], []
    for campo, valor in dados.dict(exclude_none=True).items():
        params.append(valor); sets.append(f"{campo}=${len(params)}")
    if not sets: return {"ok": True}
    params.append(login)
    await db.execute(
        f"UPDATE public.usuarios_garra SET {','.join(sets)},atualizado_em=NOW() WHERE login=${len(params)}", *params
    )
    return {"ok": True}

@app.delete("/usuarios/{login}")
async def remover_usuario(login: str, db=Depends(get_db)):
    await db.execute("UPDATE public.usuarios_garra SET ativo=FALSE WHERE login=$1", login)
    return {"ok": True}

@app.patch("/usuarios/{login}/pts")
async def atualizar_pts(login: str, pts: int, db=Depends(get_db)):
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=$1, atualizado_em=NOW() WHERE login=$2", pts, login
    )
    return {"ok": True}

@app.get("/checklist/modelos")
async def listar_modelos(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist.modelos WHERE ativo=TRUE ORDER BY label")
    result = []
    for r in rows:
        d = dict(r)
        d["questions"] = d["questions"] if isinstance(d["questions"],list) else json.loads(d["questions"] or "[]")
        d["steps"]     = d["steps"]     if isinstance(d["steps"],list)     else json.loads(d["steps"]     or "[]")
        result.append(d)
    return result

@app.post("/checklist/modelos")
async def salvar_modelo(cl: ChecklistModeloCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist.modelos WHERE cl_id=$1", cl.cl_id)
    if existe:
        await db.execute(
            "UPDATE checklist.modelos SET label=$1,icon=$2,descricao=$3,vehicle_cat=$4,is_default=$5,score_full=$6,score_nc=$7,score_obs=$8,score_ontime=$9,questions=$10,steps=$11 WHERE cl_id=$12",
            cl.label,cl.icon,cl.descricao,cl.vehicle_cat,cl.is_default,cl.score_full,cl.score_nc,cl.score_obs,cl.score_ontime,json.dumps(cl.questions),json.dumps(cl.steps),cl.cl_id
        )
    else:
        await db.execute(
            "INSERT INTO checklist.modelos (cl_id,label,icon,descricao,vehicle_cat,is_default,score_full,score_nc,score_obs,score_ontime,questions,steps) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            cl.cl_id,cl.label,cl.icon,cl.descricao,cl.vehicle_cat,cl.is_default,cl.score_full,cl.score_nc,cl.score_obs,cl.score_ontime,json.dumps(cl.questions),json.dumps(cl.steps)
        )
    return {"ok": True}

@app.delete("/checklist/modelos/{cl_id}")
async def remover_modelo(cl_id: str, db=Depends(get_db)):
    await db.execute("UPDATE checklist.modelos SET ativo=FALSE WHERE cl_id=$1", cl_id)
    return {"ok": True}

@app.get("/checklist/envios")
async def listar_envios(usuario: Optional[str]=None, cl_id: Optional[str]=None, limit: int=100, db=Depends(get_db)):
    where, params = "WHERE arquivado=FALSE", []
    if usuario: params.append(usuario); where += f" AND usuario_login=${len(params)}"
    if cl_id:   params.append(cl_id);   where += f" AND cl_id=${len(params)}"
    params.append(limit)
    rows = await db.fetch(f"SELECT * FROM checklist.envios {where} ORDER BY enviado_em DESC LIMIT ${len(params)}", *params)
    result = []
    for r in rows:
        d = dict(r)
        d["meta"]      = d["meta"]      if isinstance(d["meta"],dict)      else json.loads(d["meta"]      or "{}")
        d["respostas"] = d["respostas"] if isinstance(d["respostas"],dict) else json.loads(d["respostas"] or "{}")
        result.append(d)
    return result

@app.post("/checklist/envios")
async def salvar_envio(e: EnvioCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist.envios WHERE envio_id=$1", e.envio_id)
    if existe: return {"ok": True, "duplicado": True}
    data = datetime.fromisoformat(e.enviado_em) if e.enviado_em else datetime.now()
    await db.execute(
        "INSERT INTO checklist.envios (envio_id,usuario_login,usuario_nome,cl_id,cl_label,meta,respostas,pts,tem_nc,total_nc,enviado_em) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        e.envio_id,e.usuario_login,e.usuario_nome,e.cl_id,e.cl_label,json.dumps(e.meta),json.dumps(e.respostas),e.pts,e.tem_nc,e.total_nc,data
    )
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=pts+$1, total_envios=total_envios+1, atualizado_em=NOW() WHERE login=$2",
        e.pts, e.usuario_login
    )
    return {"ok": True}

@app.patch("/checklist/envios/{envio_id}/arquivar")
async def arquivar_envio(envio_id: str, db=Depends(get_db)):
    await db.execute("UPDATE checklist.envios SET arquivado=TRUE WHERE envio_id=$1", envio_id)
    return {"ok": True}

@app.get("/frota")
async def listar_frota(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist.frota WHERE ativo=TRUE ORDER BY categoria, identificacao")
    return [dict(r) for r in rows]

@app.post("/frota")
async def salvar_frota(item: FrotaItem, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist.frota WHERE categoria=$1 AND identificacao=$2", item.categoria, item.identificacao)
    if existe:
        await db.execute("UPDATE checklist.frota SET descricao=$1,ativo=TRUE WHERE categoria=$2 AND identificacao=$3", item.descricao,item.categoria,item.identificacao)
    else:
        await db.execute("INSERT INTO checklist.frota (categoria,identificacao,descricao) VALUES ($1,$2,$3)", item.categoria,item.identificacao,item.descricao)
    return {"ok": True}

@app.delete("/frota/{categoria}/{identificacao}")
async def remover_frota(categoria: str, identificacao: str, db=Depends(get_db)):
    await db.execute("UPDATE checklist.frota SET ativo=FALSE WHERE categoria=$1 AND identificacao=$2", categoria, identificacao)
    return {"ok": True}

@app.get("/logistica/motoristas")
async def listar_motoristas(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist.log_motoristas ORDER BY nome")
    return [dict(r) for r in rows]

@app.post("/logistica/motoristas")
async def salvar_motorista(m: LogMotoristaCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist.log_motoristas WHERE motor_id=$1", m.motor_id)
    if existe:
        await db.execute("UPDATE checklist.log_motoristas SET nome=$1,cpf=$2,cnh=$3,telefone=$4,status=$5,observacoes=$6,atualizado_em=NOW() WHERE motor_id=$7", m.nome,m.cpf,m.cnh,m.telefone,m.status,m.observacoes,m.motor_id)
    else:
        await db.execute("INSERT INTO checklist.log_motoristas (motor_id,nome,cpf,cnh,telefone,status,observacoes) VALUES ($1,$2,$3,$4,$5,$6,$7)", m.motor_id,m.nome,m.cpf,m.cnh,m.telefone,m.status,m.observacoes)
    return {"ok": True}

@app.delete("/logistica/motoristas/{motor_id}")
async def remover_motorista(motor_id: str, db=Depends(get_db)):
    await db.execute("DELETE FROM checklist.log_motoristas WHERE motor_id=$1", motor_id)
    return {"ok": True}

@app.get("/logistica/veiculos")
async def listar_veiculos(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist.log_veiculos ORDER BY car_id")
    result = []
    for r in rows:
        d = dict(r); d["extras"] = d["extras"] if isinstance(d["extras"],list) else json.loads(d["extras"] or "[]")
        result.append(d)
    return result

@app.post("/logistica/veiculos")
async def salvar_veiculo(v: LogVeiculoCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist.log_veiculos WHERE veiculo_id=$1", v.veiculo_id)
    if existe:
        await db.execute("UPDATE checklist.log_veiculos SET car_id=$1,placa=$2,modelo=$3,ano=$4,cor=$5,status=$6,extras=$7,observacoes=$8,atualizado_em=NOW() WHERE veiculo_id=$9", v.car_id,v.placa,v.modelo,v.ano,v.cor,v.status,json.dumps(v.extras),v.observacoes,v.veiculo_id)
    else:
        await db.execute("INSERT INTO checklist.log_veiculos (veiculo_id,car_id,placa,modelo,ano,cor,status,extras,observacoes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", v.veiculo_id,v.car_id,v.placa,v.modelo,v.ano,v.cor,v.status,json.dumps(v.extras),v.observacoes)
    return {"ok": True}

@app.delete("/logistica/veiculos/{veiculo_id}")
async def remover_veiculo(veiculo_id: str, db=Depends(get_db)):
    await db.execute("DELETE FROM checklist.log_veiculos WHERE veiculo_id=$1", veiculo_id)
    return {"ok": True}

@app.get("/logistica/registros")
async def listar_registros(limit: int=50, db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist.log_registros ORDER BY data_hora DESC LIMIT $1", limit)
    result = []
    for r in rows:
        d = dict(r); d["carros"] = d["carros"] if isinstance(d["carros"],list) else json.loads(d["carros"] or "[]")
        result.append(d)
    return result

@app.post("/logistica/registros")
async def salvar_registro(r: LogRegistroCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist.log_registros WHERE registro_id=$1", r.registro_id)
    if existe:
        await db.execute("UPDATE checklist.log_registros SET responsavel=$1,data_hora=$2,carros=$3 WHERE registro_id=$4", r.responsavel,datetime.fromisoformat(r.data_hora),json.dumps(r.carros),r.registro_id)
    else:
        await db.execute("INSERT INTO checklist.log_registros (registro_id,responsavel,data_hora,carros) VALUES ($1,$2,$3,$4)", r.registro_id,r.responsavel,datetime.fromisoformat(r.data_hora),json.dumps(r.carros))
    return {"ok": True}

@app.delete("/logistica/registros/{registro_id}")
async def remover_registro(registro_id: str, db=Depends(get_db)):
    await db.execute("DELETE FROM checklist.log_registros WHERE registro_id=$1", registro_id)
    return {"ok": True}

# ══════════════════════════════════════════════════════════════
# ROTAS JARDINAGEM — prefixo /jardinagem
# ══════════════════════════════════════════════════════════════

# ── PAGES ─────────────────────────────────────────────────────
@app.get("/jardinagem", response_class=HTMLResponse)
@app.get("/jardinagem/", response_class=HTMLResponse)
async def jard_index():
    path = os.path.join(TEMPLATES_DIR, "index.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/mobile", response_class=HTMLResponse)
async def jard_mobile():
    path = os.path.join(TEMPLATES_DIR, "mobile.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/manifest.json")
async def jard_manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"))

@app.get("/jardinagem/sw.js")
async def jard_sw():
    return FileResponse(os.path.join(STATIC_DIR, "js", "sw.js"),
                        headers={"Service-Worker-Allowed":"/jardinagem","Cache-Control":"no-cache"})

# ── AUTH JARDINAGEM ───────────────────────────────────────────
@app.post("/jardinagem/api/login")
async def jard_login(request: Request):
    d = await request.json()
    email = (d.get("email") or "").strip().lower()
    senha = (d.get("senha") or "").encode()
    usuario = jard_query(
        "SELECT * FROM public.usuarios_garra WHERE email=%s AND ativo=true LIMIT 1",
        (email,), fetch="one"
    )
    if not usuario or not bcrypt.checkpw(senha, usuario["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    token = gerar_token_jard(dict(usuario))
    return {"token": token, "nome": usuario["nome"], "perfil": usuario["perfil"]}

@app.get("/jardinagem/api/me")
async def jard_me(payload=Depends(verificar_token_jard)):
    return payload

@app.post("/jardinagem/api/logout")
async def jard_logout():
    return {"ok": True}

# ── MESES ─────────────────────────────────────────────────────
@app.get("/jardinagem/api/meses")
async def jard_list_meses(payload=Depends(verificar_token_jard)):
    meses = jard_query("""
        SELECT m.*, COUNT(DISTINCT s.id) as total_semanas
        FROM jardinagem.meses m
        LEFT JOIN jardinagem.semanas s ON s.mes_id=m.id
        GROUP BY m.id ORDER BY m.ano DESC, m.mes DESC
    """)
    return [dict(r) for r in meses]

@app.post("/jardinagem/api/meses")
async def jard_criar_mes(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    ano, mes = int(d["ano"]), int(d["mes"])
    nomes = ["","Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    label = d.get("label") or f"{nomes[mes]}/{ano}"
    exist = jard_query("SELECT id FROM jardinagem.meses WHERE ano=%s AND mes=%s", (ano,mes), fetch="one")
    if exist:
        mes_id = exist["id"]
    else:
        row = jard_query_id("INSERT INTO jardinagem.meses(ano,mes,label) VALUES(%s,%s,%s)", (ano,mes,label))
        mes_id = row["id"]
    sem_exist = jard_query("SELECT id FROM jardinagem.semanas WHERE mes_id=%s LIMIT 1", (mes_id,), fetch="one")
    if not sem_exist:
        semanas_do_mes(ano, mes, mes_id)
    mes_data = jard_query("SELECT * FROM jardinagem.meses WHERE id=%s", (mes_id,), fetch="one")
    return dict(mes_data)

@app.get("/jardinagem/api/meses/{mid}")
async def jard_get_mes(mid: int, payload=Depends(verificar_token_jard)):
    m = jard_query("SELECT * FROM jardinagem.meses WHERE id=%s", (mid,), fetch="one")
    if not m: raise HTTPException(status_code=404, detail="Não encontrado")
    result = dict(m)
    result["semanas"] = []
    sems = jard_query("SELECT * FROM jardinagem.semanas WHERE mes_id=%s ORDER BY ordem", (mid,))
    for s in sems:
        sd = dict(s)
        sd["pares"] = []
        pares = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem", (s["id"],))
        for p in pares:
            pd = dict(p)
            fotos = jard_query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
            pd["fotos"] = []
            for f in fotos:
                fd = dict(f)
                fd["url"] = storage_url(f["storage_path"]) if f["storage_path"] else ""
                pd["fotos"].append(fd)
            sd["pares"].append(pd)
        result["semanas"].append(sd)
    return result

# ── SEMANAS ───────────────────────────────────────────────────
@app.get("/jardinagem/api/semanas/ativa")
async def jard_semana_ativa(payload=Depends(verificar_token_jard)):
    hoje = date.today().isoformat()
    row = jard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                        FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                        WHERE s.data_ini<=%s AND s.data_fim>=%s LIMIT 1""", (hoje,hoje), fetch="one")
    if not row: raise HTTPException(status_code=404, detail="Sem semana ativa")
    return dict(row)

@app.post("/jardinagem/api/semanas")
async def jard_criar_semana(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    mes_id = d.get("mes_id"); label = (d.get("label") or "").strip(); ordem = d.get("ordem",0)
    if not mes_id or not label: raise HTTPException(status_code=400, detail="mes_id e label obrigatórios")
    row = jard_query_id("INSERT INTO jardinagem.semanas (mes_id,label,ordem,status) VALUES (%s,%s,%s,'aberta')", (mes_id,label,ordem))
    return dict(row)

@app.patch("/jardinagem/api/semanas/{sid}")
async def jard_patch_semana(sid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    for col in ["label","status","enviado_em"]:
        if col in d:
            jard_query(f"UPDATE jardinagem.semanas SET {col}=%s WHERE id=%s", (d[col],sid), fetch="none")
    return {"ok": True}

@app.delete("/jardinagem/api/semanas/{sid}")
async def jard_del_semana(sid: int, payload=Depends(verificar_token_jard)):
    pares = jard_query("SELECT id FROM jardinagem.pares WHERE semana_id=%s", (sid,))
    for p in pares:
        fotos = jard_query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        paths = [f["storage_path"] for f in fotos if f["storage_path"]]
        if paths: storage_delete(paths)
    jard_query("DELETE FROM jardinagem.semanas WHERE id=%s", (sid,), fetch="none")
    return {"ok": True}

@app.get("/jardinagem/api/semanas/{sid}/status")
async def jard_status_semana(sid: int, payload=Depends(verificar_token_jard)):
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (sid,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Não encontrada")
    emails = jard_query("SELECT * FROM jardinagem.emails_enviados WHERE semana_id=%s ORDER BY enviado_em DESC", (sid,))
    tp = jard_query("SELECT COUNT(*) as n FROM jardinagem.pares WHERE semana_id=%s", (sid,), fetch="one")
    tf = jard_query("SELECT COUNT(*) as n FROM jardinagem.fotos f JOIN jardinagem.pares p ON p.id=f.par_id WHERE p.semana_id=%s", (sid,), fetch="one")
    tr = jard_query("SELECT COUNT(*) as n FROM jardinagem.relatorios_diarios WHERE semana_id=%s", (sid,), fetch="one")
    return {"semana":dict(sem),"total_pares":tp["n"],"total_fotos":tf["n"],"total_relatorios":tr["n"],"emails":[dict(e) for e in emails]}

# ── PARES ─────────────────────────────────────────────────────
@app.post("/jardinagem/api/pares")
async def jard_criar_par(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    cod = next_code(2)
    row = jard_query_id("INSERT INTO jardinagem.pares (semana_id,codigo_a,codigo_d,local_nome,data_label,ordem) VALUES (%s,%s,%s,%s,%s,%s)",
                        (d["semana_id"],cod,cod+1,d.get("local_nome",""),d.get("data_label",""),d.get("ordem",0)))
    return dict(row)

@app.patch("/jardinagem/api/pares/{pid}")
async def jard_patch_par(pid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    for col in ["local_nome","ordem","semana_id","data_label"]:
        if col in d:
            jard_query(f"UPDATE jardinagem.pares SET {col}=%s WHERE id=%s", (d[col],pid), fetch="none")
    return {"ok": True}

@app.delete("/jardinagem/api/pares/{pid}")
async def jard_del_par(pid: int, payload=Depends(verificar_token_jard)):
    fotos = jard_query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (pid,))
    paths = [f["storage_path"] for f in fotos if f["storage_path"]]
    if paths: storage_delete(paths)
    jard_query("DELETE FROM jardinagem.pares WHERE id=%s", (pid,), fetch="none")
    return {"ok": True}

# ── FOTOS ─────────────────────────────────────────────────────
@app.post("/jardinagem/api/fotos/avulsa")
async def jard_foto_avulsa(
    par_id: int = Form(...), tipo: str = Form(...),
    foto: UploadFile = File(...), payload=Depends(verificar_token_jard)
):
    try:
        dados = comprimir_imagem(await foto.read())
        path  = storage_upload(dados, f"{datetime.now().strftime('%Y/%m')}/{uuid.uuid4().hex}.jpg")
        antiga = jard_query("SELECT id,storage_path FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s", (par_id,tipo), fetch="one")
        if antiga:
            storage_delete([antiga["storage_path"]])
            jard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (antiga["id"],), fetch="none")
        row = jard_query_id(
            "INSERT INTO jardinagem.fotos (par_id,tipo,origem,enviado_por,storage_path,filename_orig,sincronizado) VALUES (%s,%s,'desktop',%s,%s,%s,true)",
            (par_id,tipo,payload["sub"],path,foto.filename)
        )
        fd = dict(row); fd["url"] = storage_url(path)
        return fd
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/jardinagem/api/fotos/{fid}")
async def jard_del_foto(fid: int, payload=Depends(verificar_token_jard)):
    f = jard_query("SELECT storage_path FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="one")
    if f: storage_delete([f["storage_path"]]); jard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="none")
    return {"ok": True}

@app.get("/jardinagem/api/fotos/{fid}/url")
async def jard_url_foto(fid: int, payload=Depends(verificar_token_jard)):
    f = jard_query("SELECT storage_path FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="one")
    if not f: raise HTTPException(status_code=404, detail="Não encontrado")
    return {"url": storage_url(f["storage_path"])}

# ── RELATÓRIO DIÁRIO ──────────────────────────────────────────
@app.post("/jardinagem/api/relatorios/km")
async def jard_criar_km(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    semana_id = d.get("semana_id")
    if not semana_id:
        hoje = date.today().isoformat()
        row = jard_query("SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1", (hoje,hoje), fetch="one")
        if not row: raise HTTPException(status_code=404, detail="Sem semana ativa")
        semana_id = row["id"]
    local_nome  = (d.get("local_nome") or "").strip()
    km_ini      = d.get("km_inicial"); km_fin = d.get("km_final")
    if not local_nome: raise HTTPException(status_code=400, detail="local_nome obrigatório")
    if km_ini is None or km_fin is None: raise HTTPException(status_code=400, detail="km_inicial e km_final obrigatórios")
    if float(km_fin) <= float(km_ini): raise HTTPException(status_code=400, detail="km_final deve ser maior que km_inicial")
    offline_id = d.get("offline_id")
    if offline_id:
        exist = jard_query("SELECT id FROM jardinagem.relatorios_diarios WHERE offline_id=%s", (offline_id,), fetch="one")
        if exist: return {"ok": True, "duplicado": True, "id": exist["id"]}
    row = jard_query_id("""INSERT INTO jardinagem.relatorios_diarios
        (semana_id,usuario_id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao,offline_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (semana_id,payload["sub"],d.get("data",date.today().isoformat()),local_nome,
         float(km_ini),float(km_fin),d.get("hora_inicio"),d.get("hora_fim"),d.get("observacao",""),offline_id))
    return {"ok": True, "id": row["id"]}

@app.get("/jardinagem/api/historico/hoje")
async def jard_historico_hoje(semana_id: Optional[int]=None, payload=Depends(verificar_token_jard)):
    hoje = date.today().isoformat()
    if semana_id:
        fotos_raw = jard_query("""SELECT f.id,f.tipo,f.storage_path,f.filename_orig,p.local_nome,f.criado_em
            FROM jardinagem.fotos f JOIN jardinagem.pares p ON p.id=f.par_id
            WHERE p.semana_id=%s AND f.enviado_por=%s AND DATE(f.criado_em)=%s ORDER BY f.criado_em DESC""",
            (semana_id,payload["sub"],hoje))
        km_raw = jard_query("""SELECT id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao
            FROM jardinagem.relatorios_diarios WHERE semana_id=%s AND usuario_id=%s AND data=%s ORDER BY criado_em DESC""",
            (semana_id,payload["sub"],hoje))
    else:
        fotos_raw = jard_query("""SELECT f.id,f.tipo,f.storage_path,f.filename_orig,p.local_nome,f.criado_em
            FROM jardinagem.fotos f JOIN jardinagem.pares p ON p.id=f.par_id
            WHERE f.enviado_por=%s AND DATE(f.criado_em)=%s ORDER BY f.criado_em DESC""",
            (payload["sub"],hoje))
        km_raw = jard_query("""SELECT id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao
            FROM jardinagem.relatorios_diarios WHERE usuario_id=%s AND data=%s ORDER BY criado_em DESC""",
            (payload["sub"],hoje))
    fotos = []
    for f in fotos_raw:
        fd = dict(f); fd["url"] = storage_url(f["storage_path"]) if f["storage_path"] else ""
        fd["criado_em"] = f["criado_em"].isoformat() if f["criado_em"] else ""
        fotos.append(fd)
    km_list = []; km_total = 0.0
    for r in km_raw:
        rd = dict(r)
        ini = float(r["km_inicial"] or 0); fin = float(r["km_final"] or 0)
        rd["km_percorrido"] = round(fin-ini,1); rd["km_inicial"] = ini; rd["km_final"] = fin
        rd["hora_inicio"] = str(r["hora_inicio"]) if r["hora_inicio"] else ""
        rd["hora_fim"]    = str(r["hora_fim"])    if r["hora_fim"]    else ""
        km_total += rd["km_percorrido"]; km_list.append(rd)
    return {"data":hoje,"fotos":fotos,"km":km_list,"km_total":round(km_total,1)}

# ── CONFIG ────────────────────────────────────────────────────
@app.get("/jardinagem/api/config")
async def jard_config(payload=Depends(verificar_token_jard)):
    rows = jard_query("SELECT * FROM jardinagem.config")
    return {r["chave"]: r["valor"] for r in rows}

@app.get("/jardinagem/api/clientes")
async def jard_clientes(payload=Depends(verificar_token_jard)):
    rows = jard_query("SELECT id,nome FROM public.clientes_garra WHERE ativo=true")
    return [dict(r) for r in rows]

# ── PREVIEW RELATÓRIO ─────────────────────────────────────────
@app.get("/jardinagem/api/relatorios/{semana_id}/preview")
async def jard_preview(semana_id: int, payload=Depends(verificar_token_jard)):
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    pares_raw = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem,id", (semana_id,))
    pares = []
    for p in pares_raw:
        fotos = jard_query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        fa = next((f for f in fotos if f["tipo"]=="antes"), None)
        fd = next((f for f in fotos if f["tipo"]=="depois"), None)
        pares.append({"id":p["id"],"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":bool(fa),"foto_depois":bool(fd),
                      "url_antes":storage_url(fa["storage_path"]) if fa and fa.get("storage_path") else "",
                      "url_depois":storage_url(fd["storage_path"]) if fd and fd.get("storage_path") else ""})
    kms_raw = jard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.semana_id=%s ORDER BY r.data,r.criado_em""", (semana_id,))
    kms = [{"id":r["id"],"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local_nome":r["local_nome"] or "",
            "km_inicial":float(r["km_inicial"] or 0),"km_final":float(r["km_final"] or 0),
            "hora_inicio":str(r["hora_inicio"]) if r["hora_inicio"] else "",
            "hora_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
            "observacao":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    return {"semana_id":semana_id,"label":sem["label"],"pares":pares,"relatorios":kms,
            "total_pares":len(pares),"pares_completos":sum(1 for p in pares if p["foto_antes"] and p["foto_depois"]),"total_km":len(kms)}

# ── DOWNLOAD EXCEL ────────────────────────────────────────────
@app.get("/jardinagem/api/relatorios/{semana_id}/fotos")
async def jard_excel_fotos(semana_id: int, payload=Depends(verificar_token_jard)):
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_fotos
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    pares_raw = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem,id", (semana_id,))
    pares = []
    for p in pares_raw:
        fotos = jard_query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        pares.append({"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":next((dict(f) for f in fotos if f["tipo"]=="antes"),None),
                      "foto_depois":next((dict(f) for f in fotos if f["tipo"]=="depois"),None)})
    buf = gerar_relatorio_fotos(semana_dict, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition":f"attachment; filename=Relatorio-Fotos-{semana_id}.xlsx"})

@app.get("/jardinagem/api/relatorios/{semana_id}/km")
async def jard_excel_km(semana_id: int, payload=Depends(verificar_token_jard)):
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_km
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    kms_raw = jard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.semana_id=%s ORDER BY r.data,r.criado_em""", (semana_id,))
    relatorios = [{"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local":r["local_nome"] or "",
                   "km_ini":float(r["km_inicial"] or 0),"km_fin":float(r["km_final"] or 0),
                   "hr_ini":str(r["hora_inicio"]) if r["hora_inicio"] else "","hr_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
                   "obs":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    buf = gerar_relatorio_km(semana_dict, relatorios)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition":f"attachment; filename=Relatorio-KM-{semana_id}.xlsx"})

@app.post("/jardinagem/api/relatorios/{semana_id}/enviar")
async def jard_enviar_email(semana_id: int, payload=Depends(verificar_token_jard)):
    if not all([MAIL_USERNAME, MAIL_PASSWORD, MAIL_DESTINO]):
        raise HTTPException(status_code=400, detail="Email não configurado")
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_fotos, gerar_relatorio_km
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    pares_raw = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s ORDER BY ordem,id", (semana_id,))
    pares = []
    for p in pares_raw:
        fotos = jard_query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        pares.append({"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":next((dict(f) for f in fotos if f["tipo"]=="antes"),None),
                      "foto_depois":next((dict(f) for f in fotos if f["tipo"]=="depois"),None)})
    kms_raw = jard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.semana_id=%s ORDER BY r.data,r.criado_em""", (semana_id,))
    relatorios = [{"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local":r["local_nome"] or "",
                   "km_ini":float(r["km_inicial"] or 0),"km_fin":float(r["km_final"] or 0),
                   "hr_ini":str(r["hora_inicio"]) if r["hora_inicio"] else "","hr_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
                   "obs":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    try:
        buf_fotos = gerar_relatorio_fotos(semana_dict, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
        buf_km    = gerar_relatorio_km(semana_dict, relatorios)
        corpo = f"""<div style="font-family:Arial;padding:20px;">
            <h2 style="color:#1A2A5E;">Relatório Jardinagem — {semana_dict['label']}</h2>
            <p>Segue em anexo os relatórios fotográfico e de KM da semana.</p>
            <p style="color:#64748B;font-size:12px;">Garra Terraplenagem e Caçambas</p></div>"""
        enviar_email_smtp(MAIL_DESTINO, f"Relatório Jardinagem — {semana_dict['label']}", corpo,
                          [(f"Fotos-{semana_id}.xlsx", buf_fotos.getvalue()),
                           (f"KM-{semana_id}.xlsx", buf_km.getvalue())])
        jard_query("INSERT INTO jardinagem.emails_enviados (semana_id,destinatario,assunto,status) VALUES (%s,%s,%s,'enviado')",
                   (semana_id,MAIL_DESTINO,f"Relatório {semana_dict['label']}"), fetch="none")
        jard_query("UPDATE jardinagem.semanas SET status='enviada',enviado_em=NOW() WHERE id=%s", (semana_id,), fetch="none")
        return {"ok": True, "mensagem": f"Relatórios enviados para {MAIL_DESTINO}"}
    except Exception as e:
        jard_query("INSERT INTO jardinagem.emails_enviados (semana_id,destinatario,assunto,status,erro_msg) VALUES (%s,%s,%s,'erro',%s)",
                   (semana_id,MAIL_DESTINO,f"Relatório {semana_dict['label']}",str(e)), fetch="none")
        raise HTTPException(status_code=500, detail=f"Falha no envio: {str(e)}")


# ── FALLBACK — assets sem prefixo /jardinagem ────────────────
# Compatibilidade com browsers que cachearam URLs antigas
from fastapi.responses import RedirectResponse

@app.get("/manifest.json")
async def redirect_manifest():
    return RedirectResponse(url="/jardinagem/manifest.json")

@app.get("/sw.js")
async def redirect_sw():
    return RedirectResponse(url="/jardinagem/sw.js")

@app.get("/mobile")
async def redirect_mobile():
    return RedirectResponse(url="/jardinagem/mobile")

@app.get("/static/icons/{filename}")
async def redirect_static_icons(filename: str):
    return RedirectResponse(url=f"/jardinagem/static/icons/{filename}")

@app.get("/favicon.ico")
async def redirect_favicon():
    return RedirectResponse(url="/jardinagem/static/icons/favicon.ico")

# ── HEALTH CHECK ──────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status":"ok","sistema":"Garra Gestão API","versao":"6.0.0","modulos":["checklist","jardinagem"]}

@app.get("/jardinagem/api/health")
async def jard_health():
    return {"status":"ok","modulo":"jardinagem"}
