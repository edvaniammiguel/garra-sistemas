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
from dotenv import load_dotenv
load_dotenv()
import psycopg2, psycopg2.extras
import requests as req_lib
from datetime import datetime, timedelta, date
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from PIL import Image

app = FastAPI(title="Garra Gestão API", version="6.0.0")  # main em admin/api/main.py

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
        "https://garra-jardinagem-app.onrender.com",  # Static Site PWA mobile
        "http://localhost:8000",   # Dev local
        "http://127.0.0.1:8000",  # Dev local alternativo
        "http://localhost:3000",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
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
# Em Render, o working directory é a raiz do repo
import sys
current_file = os.path.abspath(__file__)
possible_paths = [
    os.path.join(os.path.dirname(current_file), "..", "..", "jardinagem"),  # Local dev
    "/app/jardinagem",  # Render default
    os.path.join(os.getcwd(), "jardinagem"),  # Render com working dir = raiz
]

JARD_DIR = None
for path in possible_paths:
    if os.path.exists(os.path.join(path, "templates")):
        JARD_DIR = os.path.abspath(path)
        break

if not JARD_DIR:
    JARD_DIR = os.path.abspath(possible_paths[0])  # Fallback

STATIC_DIR    = os.path.join(JARD_DIR, "static")
TEMPLATES_DIR = os.path.join(JARD_DIR, "templates")

print(f"JARD_DIR: {JARD_DIR}")
print(f"TEMPLATES_DIR: {TEMPLATES_DIR} (exists: {os.path.exists(TEMPLATES_DIR)})")
print(f"STATIC_DIR: {STATIC_DIR} (exists: {os.path.exists(STATIC_DIR)})")

if os.path.exists(STATIC_DIR):
    app.mount("/jardinagem/static", StaticFiles(directory=STATIC_DIR), name="jard_static")

# Ícones globais — servidos como /static/icons/ para todos os módulos
ICONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "checklist", "icons")
if os.path.exists(ICONS_DIR):
    app.mount("/static/icons", StaticFiles(directory=ICONS_DIR), name="static_icons")
    print(f"ICONS_DIR: {ICONS_DIR} (exists: True)")

# Operacional static files (idb.js, sw.js, offline-ui.js, etc)
OPERACIONAL_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "static")
if os.path.exists(OPERACIONAL_STATIC_DIR):
    # Rota dedicada para sw.js com header Service-Worker-Allowed: /
    # Permite o SW ter scope na raiz do site mesmo sendo servido de subpasta
    @app.get("/operacional/static/sw.js")
    async def serve_sw_js():
        return FileResponse(
            os.path.join(OPERACIONAL_STATIC_DIR, "sw.js"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"}
        )
    app.mount("/operacional/static", StaticFiles(directory=OPERACIONAL_STATIC_DIR), name="operacional_static")
    print(f"OPERACIONAL_STATIC_DIR: {OPERACIONAL_STATIC_DIR} (exists: True)")

# Assets do checklist (css, js)
CHECKLIST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "checklist")
if os.path.exists(CHECKLIST_DIR):
    app.mount("/checklist/css",      StaticFiles(directory=os.path.join(CHECKLIST_DIR, "css")), name="checklist_css")
    app.mount("/checklist/js",       StaticFiles(directory=os.path.join(CHECKLIST_DIR, "js")),  name="checklist_js")
    app.mount("/checklist/icons",    StaticFiles(directory=os.path.join(CHECKLIST_DIR, "icons")), name="checklist_icons_sub")
    # Caminhos relativos do checklist quando carregado no iframe
    if os.path.exists(os.path.join(CHECKLIST_DIR, "css")):
        app.mount("/css", StaticFiles(directory=os.path.join(CHECKLIST_DIR, "css")), name="checklist_css_rel")
    if os.path.exists(os.path.join(CHECKLIST_DIR, "js")):
        app.mount("/js",  StaticFiles(directory=os.path.join(CHECKLIST_DIR, "js")),  name="checklist_js_rel")
    if os.path.exists(os.path.join(CHECKLIST_DIR, "icons")):
        app.mount("/icons", StaticFiles(directory=os.path.join(CHECKLIST_DIR, "icons")), name="checklist_icons_rel")

# ── SUPABASE STORAGE ──────────────────────────────────────────
def storage_upload(dados: bytes, path: str) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("Supabase não configurado")
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": "image/jpeg",
        "x-upsert": "true"
    }
    r = req_lib.post(url, headers=headers, data=dados, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload falhou [{r.status_code}]: {r.text}")
    return path

# Cache de URLs assinadas — evita chamadas repetidas ao Supabase
# TTL: 23h (URLs do Supabase expiram em 1h por padrão, mas geramos com 24h)
_url_cache: dict = {}  # {storage_path: (url, expires_at)}
_URL_TTL = 23 * 3600   # 23 horas em segundos
_URL_SUPABASE_EXPIRY = 24 * 3600  # 24h — URL válida no Supabase

def storage_url(path: str, segundos: int = _URL_SUPABASE_EXPIRY) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not path:
        return ""
    # Verificar cache
    agora = time.time()
    cached = _url_cache.get(path)
    if cached:
        url, expires_at = cached
        if agora < expires_at:
            return url
    # Gerar URL nova no Supabase
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/storage/v1/object/sign/{BUCKET_NAME}/{path}",
            headers={
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "apikey": SUPABASE_SERVICE_KEY
            },
            json={"expiresIn": segundos}, timeout=10
        )
        if r.status_code == 200:
            url = f"{SUPABASE_URL}/storage/v1{r.json().get('signedURL','')}"
            _url_cache[path] = (url, agora + _URL_TTL)
            return url
    except: pass
    return ""

def storage_delete(paths: list):
    if not paths or not SUPABASE_URL: return
    try:
        req_lib.delete(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET_NAME}",
            headers={
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "apikey": SUPABASE_SERVICE_KEY
            },
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
        payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        # Se sub não é UUID (token do auth central tem sub=login), busca o UUID
        sub = payload.get("sub", "")
        import re as _re
        uuid_pattern = _re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', _re.I)
        if sub and not uuid_pattern.match(str(sub)):
            # sub é login — busca UUID no banco
            row = jard_query(
                "SELECT id FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
                (sub, sub), fetch="one"
            )
            if row:
                payload["sub"] = str(row["id"])
        return payload
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
    # Suporta múltiplos destinatários separados por vírgula em MAIL_DESTINO e MAIL_CC
    lista_to = [e.strip() for e in destino.split(",") if e.strip()]
    lista_cc = [e.strip() for e in MAIL_CC.split(",") if e.strip()] if MAIL_CC else []
    msg = MIMEMultipart("mixed")
    msg["Subject"] = assunto
    msg["From"]    = f"Garra Terraplenagem <{MAIL_USERNAME}>"
    msg["To"]      = ", ".join(lista_to)
    if lista_cc:
        msg["Cc"]  = ", ".join(lista_cc)
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    if anexos:
        for nome, dados in anexos:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(dados)
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{nome}"')
            msg.attach(part)
    destinatarios = lista_to + lista_cc
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
# ROTAS CHECKLIST
# ══════════════════════════════════════════════════════════════

@app.post("/auth/login")
async def login(req: LoginRequest, request: Request, db=Depends(get_db)):
    check_rate_limit(request.client.host)
    user = await db.fetchrow(
        "SELECT * FROM public.usuarios_garra WHERE (login=$1 OR email=$1) AND ativo=TRUE",
        req.login
    )
    if not user or not bcrypt.checkpw(req.senha.encode(), user["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    token = pyjwt.encode({
        "sub":              user["login"],
        "login":            user["login"],
        "nome":             user["nome"],
        "perfil":           user["perfil"],
        "perfil_checklist": user["perfil_checklist"],
        "exp":              datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }, JWT_SECRET, algorithm="HS256")
    
    # Determinar redirect_url baseado no perfil
    redirects = {
        "admin": "/admin",
        "gestor": "/admin",
        "luana": "/jardinagem/desktop",
        "campo": "/jardinagem/mobile",
        "operador": "/mobile",
        "motorista": "/mobile",
        "bruna": "/mobile"
    }
    redirect_url = redirects.get(user["perfil"], "/admin")
    
    # Carregar permissões efetivas (DB + padrão do perfil)
    try:
        rows_perm = await db.fetch(
            "SELECT modulo, permitido FROM public.permissoes_colaborador WHERE usuario_id=$1",
            user["id"]
        )
        perms = {r["modulo"]: r["permitido"] for r in (rows_perm or [])}
        padrao = PERFIL_MODULOS_PADRAO.get(user["perfil"], [])
        for m in MODULOS_DISPONIVEIS:
            if m["id"] not in perms:
                perms[m["id"]] = m["id"] in padrao
    except Exception:
        perms = {}
    
    return {
        "token": token,
        "id": str(user["id"]),
        "login": user["login"], "nome": user["nome"],
        "perfil": user["perfil"], "perfil_checklist": user["perfil_checklist"],
        "role": user["perfil_checklist"] or user["perfil"],
        "redirect_url": redirect_url,
        "permsDB": perms,
        "pts": user["pts"] or 0, "total_envios": user["total_envios"] or 0,
        "email": user["email"] or "",
    }

# ── VERIFICADORES JWT CHECKLIST ───────────────────────────────
def verificar_token(authorization: Optional[str] = Header(None)):
    """Exige login válido. Retorna o payload do JWT."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Não autenticado")
    token = authorization[7:]
    try:
        return pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Sessão expirada")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token inválido")

def verificar_admin(payload=Depends(verificar_token)):
    """Exige login válido E perfil admin."""
    if payload.get("perfil") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    return payload

def verificar_gestor(payload=Depends(verificar_token)):
    """Exige login válido E perfil de gestão (admin, gestor ou luana)."""
    perfil = payload.get("perfil")
    if perfil not in ("admin", "gestor", "luana"):
        raise HTTPException(status_code=403, detail="Acesso restrito a gestores")
    return payload

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

@app.post("/auth/renovar")
async def renovar_token(payload=Depends(verificar_token)):
    """Token válido → gera novo com validade renovada. Chamado silenciosamente ao abrir o app."""
    novo_token = pyjwt.encode({
        "sub":              payload["sub"],
        "login":            payload.get("login") or payload.get("sub", ""),
        "nome":             payload.get("nome", ""),
        "perfil":           payload.get("perfil", ""),
        "perfil_checklist": payload.get("perfil_checklist", ""),
        "exp":              datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }, JWT_SECRET, algorithm="HS256")

    login = payload.get("login") or payload.get("sub", "")
    user = jard_query(
        "SELECT id, pts, total_envios FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
        (login, login), fetch="one"
    )

    return {
        "token": novo_token,
        "id": str(user["id"]) if user else None,
        "login": login,
        "nome": payload.get("nome", ""),
        "perfil": payload.get("perfil", ""),
        "perfil_checklist": payload.get("perfil_checklist", ""),
        "pts": (user["pts"] if user else 0) or 0,
        "total_envios": (user["total_envios"] if user else 0) or 0,
    }

@app.post("/auth/alterar-senha")
async def alterar_senha(req: SenhaChange, login: str, db=Depends(get_db), _auth=Depends(verificar_token)):
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
async def listar_usuarios(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch(
        "SELECT id,login,nome,email,perfil,perfil_checklist,pts,total_envios,ativo,criado_em FROM public.usuarios_garra ORDER BY nome"
    )
    return [dict(r) for r in rows]

@app.post("/usuarios")
async def criar_usuario(u: UsuarioCreate, db=Depends(get_db), _auth=Depends(verificar_admin)):
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
async def editar_usuario(login: str, dados: UsuarioEdit, db=Depends(get_db), _auth=Depends(verificar_admin)):
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
async def remover_usuario(login: str, db=Depends(get_db), _auth=Depends(verificar_admin)):
    await db.execute("UPDATE public.usuarios_garra SET ativo=FALSE WHERE login=$1", login)
    return {"ok": True}

@app.patch("/usuarios/{login}/pts")
async def atualizar_pts(login: str, pts: int, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=$1, atualizado_em=NOW() WHERE login=$2", pts, login
    )
    return {"ok": True}

@app.get("/checklist/modelos")
async def listar_modelos(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.modelos WHERE ativo=TRUE ORDER BY label")
    result = []
    for r in rows:
        d = dict(r)
        d["questions"] = d["questions"] if isinstance(d["questions"],list) else json.loads(d["questions"] or "[]")
        d["steps"]     = d["steps"]     if isinstance(d["steps"],list)     else json.loads(d["steps"]     or "[]")
        result.append(d)
    return result

@app.post("/checklist/modelos")
async def salvar_modelo(cl: ChecklistModeloCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
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
async def remover_modelo(cl_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("UPDATE checklist.modelos SET ativo=FALSE WHERE cl_id=$1", cl_id)
    return {"ok": True}

@app.get("/checklist/envios")
async def listar_envios(usuario: Optional[str]=None, cl_id: Optional[str]=None, limit: int=100, db=Depends(get_db), _auth=Depends(verificar_token)):
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
async def salvar_envio(e: EnvioCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
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
async def arquivar_envio(envio_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("UPDATE checklist.envios SET arquivado=TRUE WHERE envio_id=$1", envio_id)
    return {"ok": True}

@app.get("/frota")
async def listar_frota(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.frota WHERE ativo=TRUE ORDER BY categoria, identificacao")
    return [dict(r) for r in rows]

@app.post("/frota")
async def salvar_frota(item: FrotaItem, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.frota WHERE categoria=$1 AND identificacao=$2", item.categoria, item.identificacao)
    if existe:
        await db.execute("UPDATE checklist.frota SET descricao=$1,ativo=TRUE WHERE categoria=$2 AND identificacao=$3", item.descricao,item.categoria,item.identificacao)
    else:
        await db.execute("INSERT INTO checklist.frota (categoria,identificacao,descricao) VALUES ($1,$2,$3)", item.categoria,item.identificacao,item.descricao)
    return {"ok": True}

@app.delete("/frota/{categoria}/{identificacao}")
async def remover_frota(categoria: str, identificacao: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("UPDATE checklist.frota SET ativo=FALSE WHERE categoria=$1 AND identificacao=$2", categoria, identificacao)
    return {"ok": True}

@app.get("/logistica/motoristas")
async def listar_motoristas(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.log_motoristas ORDER BY nome")
    return [dict(r) for r in rows]

@app.post("/logistica/motoristas")
async def salvar_motorista(m: LogMotoristaCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.log_motoristas WHERE motor_id=$1", m.motor_id)
    if existe:
        await db.execute("UPDATE checklist.log_motoristas SET nome=$1,cpf=$2,cnh=$3,telefone=$4,status=$5,observacoes=$6,atualizado_em=NOW() WHERE motor_id=$7", m.nome,m.cpf,m.cnh,m.telefone,m.status,m.observacoes,m.motor_id)
    else:
        await db.execute("INSERT INTO checklist.log_motoristas (motor_id,nome,cpf,cnh,telefone,status,observacoes) VALUES ($1,$2,$3,$4,$5,$6,$7)", m.motor_id,m.nome,m.cpf,m.cnh,m.telefone,m.status,m.observacoes)
    return {"ok": True}

@app.delete("/logistica/motoristas/{motor_id}")
async def remover_motorista(motor_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("DELETE FROM checklist.log_motoristas WHERE motor_id=$1", motor_id)
    return {"ok": True}

@app.get("/logistica/veiculos")
async def listar_veiculos(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.log_veiculos ORDER BY car_id")
    result = []
    for r in rows:
        d = dict(r); d["extras"] = d["extras"] if isinstance(d["extras"],list) else json.loads(d["extras"] or "[]")
        result.append(d)
    return result

@app.post("/logistica/veiculos")
async def salvar_veiculo(v: LogVeiculoCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.log_veiculos WHERE veiculo_id=$1", v.veiculo_id)
    if existe:
        await db.execute("UPDATE checklist.log_veiculos SET car_id=$1,placa=$2,modelo=$3,ano=$4,cor=$5,status=$6,extras=$7,observacoes=$8,atualizado_em=NOW() WHERE veiculo_id=$9", v.car_id,v.placa,v.modelo,v.ano,v.cor,v.status,json.dumps(v.extras),v.observacoes,v.veiculo_id)
    else:
        await db.execute("INSERT INTO checklist.log_veiculos (veiculo_id,car_id,placa,modelo,ano,cor,status,extras,observacoes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", v.veiculo_id,v.car_id,v.placa,v.modelo,v.ano,v.cor,v.status,json.dumps(v.extras),v.observacoes)
    return {"ok": True}

@app.delete("/logistica/veiculos/{veiculo_id}")
async def remover_veiculo(veiculo_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("DELETE FROM checklist.log_veiculos WHERE veiculo_id=$1", veiculo_id)
    return {"ok": True}

@app.get("/logistica/registros")
async def listar_registros(limit: int=50, db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.log_registros ORDER BY data_hora DESC LIMIT $1", limit)
    result = []
    for r in rows:
        d = dict(r); d["carros"] = d["carros"] if isinstance(d["carros"],list) else json.loads(d["carros"] or "[]")
        result.append(d)
    return result

@app.post("/logistica/registros")
async def salvar_registro(r: LogRegistroCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.log_registros WHERE registro_id=$1", r.registro_id)
    if existe:
        await db.execute("UPDATE checklist.log_registros SET responsavel=$1,data_hora=$2,carros=$3 WHERE registro_id=$4", r.responsavel,datetime.fromisoformat(r.data_hora),json.dumps(r.carros),r.registro_id)
    else:
        await db.execute("INSERT INTO checklist.log_registros (registro_id,responsavel,data_hora,carros) VALUES ($1,$2,$3,$4)", r.registro_id,r.responsavel,datetime.fromisoformat(r.data_hora),json.dumps(r.carros))
    return {"ok": True}

@app.delete("/logistica/registros/{registro_id}")
async def remover_registro(registro_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("DELETE FROM checklist.log_registros WHERE registro_id=$1", registro_id)
    return {"ok": True}

# ══════════════════════════════════════════════════════════════
# ROTAS JARDINAGEM — prefixo /jardinagem
# ══════════════════════════════════════════════════════════════

# ── PAGES ─────────────────────────────────────────────────────
@app.get("/operacional/manifest.json")
async def operacional_manifest():
    """PWA manifest para o mobile operacional."""
    return {
        "name": "Garra Operacional",
        "short_name": "Garra Op",
        "start_url": "/operacional/mobile",
        "display": "standalone",
        "background_color": "#F0F4FF",
        "theme_color": "#1A2A5E",
        "icons": [
            {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }

@app.get("/operacional/mobile", response_class=HTMLResponse)
async def operacional_mobile():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "operacional-mobile.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Mobile operacional não encontrado")
    return open(path, encoding="utf-8").read()

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "admin", "admin-app.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Admin app não encontrado")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem", response_class=HTMLResponse)
@app.get("/jardinagem/", response_class=HTMLResponse)
async def jard_index():
    path = os.path.join(TEMPLATES_DIR, "desk-admin.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/desktop", response_class=HTMLResponse)
async def jard_desktop_login():
    # Desktop login para Luana/Admin
    path = os.path.join(TEMPLATES_DIR, "desk-login.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/desktop-app", response_class=HTMLResponse)
async def jard_desktop_app():
    # Desktop app para Luana/Admin
    path = os.path.join(TEMPLATES_DIR, "desk-app.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/mobile", response_class=HTMLResponse)
async def jard_mobile():
    # Mobile PWA login para Arthur/Breno
    path = os.path.join(STATIC_DIR, "pwa-login.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/mobile-app", response_class=HTMLResponse)
async def jard_mobile_app():
    # Mobile PWA app para Arthur/Breno
    path = os.path.join(STATIC_DIR, "pwa-app.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/pwa-login.html", response_class=HTMLResponse)
async def jard_pwa_login_html():
    path = os.path.join(STATIC_DIR, "pwa-login.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/pwa-app.html", response_class=HTMLResponse)
async def jard_pwa_app_html():
    # Resolve redirect "./pwa-app.html" do pwa-login.html
    path = os.path.join(STATIC_DIR, "pwa-app.html")
    return open(path, encoding="utf-8").read()

@app.get("/jardinagem/manifest.json")
async def jard_manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"))

@app.get("/jardinagem/sw.js")
async def jard_sw():
    # sw.js na raiz de static/ (não em static/js/)
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"),
                        headers={"Service-Worker-Allowed": "/jardinagem", "Cache-Control": "no-cache"})

# ── AUTH JARDINAGEM ───────────────────────────────────────────
@app.post("/jardinagem/api/login")
async def jard_login(request: Request):
    d = await request.json()
    email = (d.get("email") or "").strip().lower()
    senha = (d.get("senha") or "").encode()
    usuario = jard_query(
        "SELECT * FROM public.usuarios_garra WHERE (email=%s OR login=%s) AND ativo=true LIMIT 1",
        (email, email), fetch="one"
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

@app.delete("/jardinagem/api/meses/{mid}")
async def jard_del_mes(mid: int, payload=Depends(verificar_token_jard)):
    mes = jard_query("SELECT id FROM jardinagem.meses WHERE id=%s", (mid,), fetch="one")
    if not mes:
        raise HTTPException(status_code=404, detail="Mês não encontrado")
    semanas = jard_query("SELECT id FROM jardinagem.semanas WHERE mes_id=%s", (mid,))
    for s in semanas:
        pares = jard_query("SELECT id FROM jardinagem.pares WHERE semana_id=%s", (s["id"],))
        for p in pares:
            fotos = jard_query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
            paths = [f["storage_path"] for f in fotos if f["storage_path"]]
            if paths: storage_delete(paths)
            jard_query("DELETE FROM jardinagem.fotos WHERE par_id=%s", (p["id"],), fetch="none")
        jard_query("DELETE FROM jardinagem.pares WHERE semana_id=%s", (s["id"],), fetch="none")
        jard_query("DELETE FROM jardinagem.fila_sync WHERE semana_id=%s", (s["id"],), fetch="none")
        jard_query("DELETE FROM jardinagem.emails_enviados WHERE semana_id=%s", (s["id"],), fetch="none")
        jard_query("DELETE FROM jardinagem.relatorios_diarios WHERE semana_id=%s", (s["id"],), fetch="none")
    jard_query("DELETE FROM jardinagem.semanas WHERE mes_id=%s", (mid,), fetch="none")
    jard_query("DELETE FROM jardinagem.meses WHERE id=%s", (mid,), fetch="none")
    return {"ok": True}

@app.post("/jardinagem/api/meses")
async def jard_criar_mes(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    ano, mes = int(d["ano"]), int(d["mes"])
    nomes = ["","Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    label = d.get("label") or f"{nomes[mes]}/{ano}"
    exist = jard_query("SELECT id FROM jardinagem.meses WHERE ano=%s AND mes=%s", (ano,mes), fetch="one")
    ja_existia = False
    if exist:
        mes_id = exist["id"]
        ja_existia = True
    else:
        row = jard_query_id("INSERT INTO jardinagem.meses(ano,mes,label) VALUES(%s,%s,%s)", (ano,mes,label))
        mes_id = row["id"]
    sem_exist = jard_query("SELECT id FROM jardinagem.semanas WHERE mes_id=%s LIMIT 1", (mes_id,), fetch="one")
    if not sem_exist:
        semanas_do_mes(ano, mes, mes_id)
    mes_data = jard_query("SELECT * FROM jardinagem.meses WHERE id=%s", (mes_id,), fetch="one")
    result = dict(mes_data)
    result["ja_existia"] = ja_existia
    return result

@app.patch("/jardinagem/api/meses/{mid}")
async def jard_patch_mes(mid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    if "label" in d:
        jard_query("UPDATE jardinagem.meses SET label=%s WHERE id=%s", (d["label"], mid), fetch="none")
    return {"ok": True}

@app.get("/jardinagem/api/meses/{mid}")
async def jard_get_mes(mid: int, payload=Depends(verificar_token_jard)):
    from concurrent.futures import ThreadPoolExecutor
    m = jard_query("SELECT * FROM jardinagem.meses WHERE id=%s", (mid,), fetch="one")
    if not m: raise HTTPException(status_code=404, detail="Não encontrado")
    result = dict(m)
    # 1 query semanas
    sems = jard_query("SELECT * FROM jardinagem.semanas WHERE mes_id=%s ORDER BY ordem", (mid,))
    if not sems:
        result["semanas"] = []
        return result
    sem_ids = [s["id"] for s in sems]
    # 1 query todos os pares do mês (elimina N+1)
    placeholders = ",".join(["%s"] * len(sem_ids))
    pares_raw = jard_query(
        f"SELECT * FROM jardinagem.pares WHERE semana_id IN ({placeholders}) AND (ativo IS NULL OR ativo=true) ORDER BY semana_id, codigo_a",
        tuple(sem_ids)
    )
    par_ids = [p["id"] for p in pares_raw] if pares_raw else []
    # 1 query todas as fotos do mês (elimina N+1)
    fotos_raw = []
    if par_ids:
        ph2 = ",".join(["%s"] * len(par_ids))
        fotos_raw = jard_query(
            f"SELECT * FROM jardinagem.fotos WHERE par_id IN ({ph2})",
            tuple(par_ids)
        )
    # Gerar todas as URLs em paralelo
    paths_map = {f["id"]: f["storage_path"] for f in fotos_raw if f.get("storage_path")}
    urls = {}
    if paths_map:
        with ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(storage_url, path): fid for fid, path in paths_map.items()}
            for future, fid in futures.items():
                try: urls[fid] = future.result(timeout=10)
                except: urls[fid] = ""
    # Montar estrutura por par
    fotos_por_par = {}
    for f in fotos_raw:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = []
        fd = dict(f)
        fd["url"] = urls.get(f["id"], "")
        fotos_por_par[pid].append(fd)
    # Montar estrutura por semana
    pares_por_sem = {}
    for p in pares_raw:
        sid = p["semana_id"]
        if sid not in pares_por_sem: pares_por_sem[sid] = []
        pd = dict(p)
        pd["fotos"] = fotos_por_par.get(p["id"], [])
        pares_por_sem[sid].append(pd)
    # Montar resultado final
    result["semanas"] = []
    for s in sems:
        sd = dict(s)
        sd["mes_id"] = mid
        sd["pares"] = pares_por_sem.get(s["id"], [])
        result["semanas"].append(sd)
    return result

# ── SEMANAS ───────────────────────────────────────────────────
@app.get("/jardinagem/api/semanas")
async def jard_listar_semanas(mes_id: int = None, payload=Depends(verificar_token_jard)):
    if not mes_id:
        hoje = date.today().isoformat()
        row = jard_query("""SELECT m.id FROM jardinagem.meses m
                           JOIN jardinagem.semanas s ON s.mes_id=m.id
                           WHERE s.data_ini<=%s AND s.data_fim>=%s LIMIT 1""", (hoje,hoje), fetch="one")
        if not row:
            row = jard_query("SELECT id FROM jardinagem.meses ORDER BY ano DESC, mes DESC LIMIT 1", fetch="one")
        mes_id = row["id"] if row else None
    if not mes_id:
        return {"ok": True, "semanas": []}
    rows = jard_query(
        "SELECT * FROM jardinagem.semanas WHERE mes_id=%s ORDER BY ordem",
        (mes_id,)
    )
    semanas = []
    for r in rows:
        s = dict(r)
        s["data_inicio"] = r["data_ini"].isoformat() if r["data_ini"] else ""
        s["data_fim"]    = r["data_fim"].isoformat() if r["data_fim"] else ""
        semanas.append(s)
    return {"ok": True, "semanas": semanas}

@app.get("/jardinagem/api/semanas/ativa")
async def jard_semana_ativa(payload=Depends(verificar_token_jard)):
    hoje = date.today().isoformat()
    row = jard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                        FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                        WHERE s.data_ini::date<=%s AND s.data_fim::date>=%s
                        AND s.status='aberta' LIMIT 1""", (hoje,hoje), fetch="one")
    if not row:
        row = jard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                            FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                            WHERE s.status='aberta'
                            ORDER BY s.id DESC LIMIT 1""", fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Sem semana ativa")
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
        jard_query("DELETE FROM jardinagem.fotos WHERE par_id=%s", (p["id"],), fetch="none")
    jard_query("DELETE FROM jardinagem.pares WHERE semana_id=%s", (sid,), fetch="none")
    jard_query("DELETE FROM jardinagem.fila_sync WHERE semana_id=%s", (sid,), fetch="none")
    jard_query("DELETE FROM jardinagem.emails_enviados WHERE semana_id=%s", (sid,), fetch="none")
    jard_query("DELETE FROM jardinagem.relatorios_diarios WHERE semana_id=%s", (sid,), fetch="none")
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
@app.get("/jardinagem/api/pares")
async def jard_listar_pares(semana_id: int = None, payload=Depends(verificar_token_jard)):
    if not semana_id:
        return {"ok": False, "error": "semana_id obrigatório"}
    pares_raw = jard_query(
        "SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a",
        (semana_id,)
    )
    pares = []
    for p in pares_raw:
        pd = dict(p)
        fotos = jard_query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        pd["fotos"] = []
        for f in fotos:
            fd = dict(f)
            fd["url"] = storage_url(f["storage_path"]) if f["storage_path"] else ""
            pd["fotos"].append(fd)
        pares.append(pd)
    return {"ok": True, "pares": pares}

@app.post("/jardinagem/api/pares")
async def jard_criar_par(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    # Buscar o último codigo_d ativo no banco
    ultimo = jard_query(
        "SELECT MAX(codigo_d) as max_cod FROM jardinagem.pares WHERE (ativo IS NULL OR ativo=true)",
        fetch="one"
    )
    # Buscar next_code configurado (valor mínimo garantido)
    cfg = jard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    min_code = int(cfg["valor"]) - 1 if cfg else 6049  # next_code aponta para o próximo, então -1 é o piso
    max_cod = max(int(ultimo.get("max_cod") or 0), min_code)
    cod = max_cod + 1  # Próximo código é sempre MAX+1

    # Atualizar config.next_code para manter sincronizado
    jard_query("UPDATE jardinagem.config SET valor=%s WHERE chave='next_code'",
               (str(cod + 2),), fetch="none")

    row = jard_query_id("INSERT INTO jardinagem.pares (semana_id,codigo_a,codigo_d,local_nome,data_label,ordem,ativo) VALUES (%s,%s,%s,%s,%s,%s,true)",
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
    # Soft delete: marcar par como inativo (campo ativo=false)
    # Fotos não são deletadas — seguem vinculadas mas não aparecem
    # next_code NÃO é alterado — sequência de códigos é imutável
    jard_query("UPDATE jardinagem.pares SET ativo=false WHERE id=%s", (pid,), fetch="none")
    return {"ok": True}

# ── FOTOS ─────────────────────────────────────────────────────
@app.patch("/jardinagem/api/fotos/{fid}")
async def jard_patch_foto(fid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    if "tipo" in d and d["tipo"] in ("antes", "depois"):
        jard_query("UPDATE jardinagem.fotos SET tipo=%s WHERE id=%s", (d["tipo"], fid), fetch="none")
    return {"ok": True}

@app.post("/jardinagem/api/fotos/avulsa")
async def jard_foto_avulsa(
    par_id: int = Form(...), tipo: str = Form(...),
    foto: UploadFile = File(...), payload=Depends(verificar_token_jard)
):
    import logging
    log = logging.getLogger(__name__)
    try:
        conteudo = await foto.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail="Arquivo vazio")
        dados = comprimir_imagem(conteudo)
        path  = storage_upload(dados, f"{datetime.now().strftime('%Y/%m')}/{uuid.uuid4().hex}.jpg")
        antiga = jard_query("SELECT id,storage_path FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s", (par_id,tipo), fetch="one")
        if antiga:
            if antiga.get("storage_path"):
                storage_delete([antiga["storage_path"]])
            jard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (antiga["id"],), fetch="none")
        row = jard_query_id(
            "INSERT INTO jardinagem.fotos (par_id,tipo,origem,enviado_por,storage_path,filename_orig,sincronizado) VALUES (%s,%s,'desktop',%s,%s,%s,true)",
            (par_id,tipo,str(payload["sub"]),path,foto.filename or "foto.jpg")
        )
        fd = dict(row); fd["url"] = storage_url(path)
        return fd
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"fotos/avulsa erro: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar foto: {str(e)}")

@app.post("/jardinagem/api/fotos/mobile")
async def jard_foto_mobile(
    semana_id: str = Form(...),
    local_nome: str = Form(""),
    tipo: str = Form("antes"),
    par_id: Optional[str] = Form(None),
    offline_id: Optional[str] = Form(None),
    foto: UploadFile = File(...),
    payload=Depends(verificar_token_jard)
):
    """Upload de foto pelo mobile (câmera ou galeria)."""
    import logging
    log = logging.getLogger(__name__)
    try:
        sid = None
        if semana_id and semana_id != "ativa":
            try: sid = int(semana_id)
            except: pass

        if not sid:
            import datetime as dt
            hoje = dt.date.today().isoformat()
            row = jard_query("""
                SELECT id FROM jardinagem.semanas
                WHERE data_ini <= %s AND data_fim >= %s LIMIT 1
            """, (hoje, hoje), fetch="one")
            if row: sid = row["id"]

        if not sid:
            raise HTTPException(status_code=400, detail="Sem semana ativa. Crie um mês primeiro.")

        pid = None
        if par_id and par_id not in ("null","undefined",""):
            try: pid = int(par_id)
            except: pass

        if not pid:
            row = jard_query_id(
                "INSERT INTO jardinagem.pares (semana_id,codigo_a,codigo_d,local_nome,data_label,ordem) VALUES (%s,%s,%s,%s,%s,%s)",
                (sid, 0, 0, local_nome or "", "", 99)
            )
            pid = row["id"]
            cod = next_code(2)
            jard_query("UPDATE jardinagem.pares SET codigo_a=%s, codigo_d=%s WHERE id=%s",
                      (cod, cod+1, pid), fetch="none")

        if offline_id:
            exist = jard_query(
                "SELECT id FROM jardinagem.fotos WHERE offline_id=%s", (offline_id,), fetch="one"
            )
            if exist:
                return {"ok": True, "foto_id": exist["id"], "duplicado": True}

        conteudo = await foto.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail="Arquivo vazio")

        dados = comprimir_imagem(conteudo)
        path  = storage_upload(dados, f"{uuid.uuid4().hex}.jpg")

        antiga = jard_query(
            "SELECT id, storage_path FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s",
            (pid, tipo), fetch="one"
        )
        if antiga:
            if antiga.get("storage_path"): storage_delete([antiga["storage_path"]])
            jard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (antiga["id"],), fetch="none")

        row = jard_query_id("""
            INSERT INTO jardinagem.fotos
            (par_id, tipo, origem, enviado_por, storage_path, filename_orig, sincronizado, offline_id)
            VALUES (%s, %s, 'mobile', %s, %s, %s, true, %s)
        """, (pid, tipo, str(payload["sub"]), path, foto.filename or "foto.jpg", offline_id))

        fd = dict(row)
        fd["url"] = storage_url(path)
        fd["par_id"] = pid
        return {"ok": True, "foto_id": fd["id"], "storage_path": path, "url": fd["url"], "par_id": pid}

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"fotos/mobile erro: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro: {str(e)}")

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
    data_km = d.get("data") or date.today().isoformat()
    row = jard_query("SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1", (data_km,data_km), fetch="one")
    if row:
        semana_id = row["id"]
    else:
        semana_id = d.get("semana_id")
        if not semana_id:
            hoje = date.today().isoformat()
            row2 = jard_query("SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1", (hoje,hoje), fetch="one")
            if not row2: raise HTTPException(status_code=404, detail="Sem semana ativa")
            semana_id = row2["id"]
    local_nome  = (d.get("local_nome") or "").strip()
    km_ini      = d.get("km_inicial"); km_fin = d.get("km_final")
    if not local_nome: raise HTTPException(status_code=400, detail="local_nome obrigatório")
    if km_ini is None or km_fin is None: raise HTTPException(status_code=400, detail="km_inicial e km_final obrigatórios")
    if float(km_fin) < float(km_ini): raise HTTPException(status_code=400, detail="km_final não pode ser menor que km_inicial")
    offline_id = d.get("offline_id")
    if offline_id:
        exist = jard_query("SELECT id FROM jardinagem.relatorios_diarios WHERE offline_id=%s", (offline_id,), fetch="one")
        if exist: return {"ok": True, "duplicado": True, "id": exist["id"]}
    row = jard_query_id("""INSERT INTO jardinagem.relatorios_diarios
        (semana_id,usuario_id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao,offline_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (semana_id,payload["sub"],data_km,local_nome,
         float(km_ini),float(km_fin),d.get("hora_inicio"),d.get("hora_fim"),d.get("observacao",""),offline_id))
    return {"ok": True, "id": row["id"]}

@app.patch("/jardinagem/api/relatorios/{km_id}")
async def jard_editar_km(km_id: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    local_nome  = (d.get("local_nome") or "").strip()
    km_ini      = d.get("km_inicial"); km_fin = d.get("km_final")
    if not local_nome: raise HTTPException(status_code=400, detail="local_nome obrigatório")
    if km_ini is None or km_fin is None: raise HTTPException(status_code=400, detail="km_inicial e km_final obrigatórios")
    if float(km_fin) < float(km_ini): raise HTTPException(status_code=400, detail="km_final não pode ser menor que km_inicial")

    data_km = d.get("data", date.today().isoformat())
    row = jard_query("SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1", (data_km,data_km), fetch="one")

    if row:
        jard_query("""UPDATE jardinagem.relatorios_diarios 
            SET data=%s, semana_id=%s, local_nome=%s, km_inicial=%s, km_final=%s, 
                hora_inicio=%s, hora_fim=%s, observacao=%s
            WHERE id=%s""",
            (data_km, row["id"], local_nome,
             float(km_ini), float(km_fin),
             d.get("hora_inicio"), d.get("hora_fim"), d.get("observacao",""),
             km_id), fetch="none")
    else:
        jard_query("""UPDATE jardinagem.relatorios_diarios 
            SET data=%s, local_nome=%s, km_inicial=%s, km_final=%s, 
                hora_inicio=%s, hora_fim=%s, observacao=%s
            WHERE id=%s""",
            (data_km, local_nome,
             float(km_ini), float(km_fin),
             d.get("hora_inicio"), d.get("hora_fim"), d.get("observacao",""),
             km_id), fetch="none")
    return {"ok": True, "id": km_id}

@app.delete("/jardinagem/api/relatorios/{km_id}")
async def jard_deletar_km(km_id: int, payload=Depends(verificar_token_jard)):
    jard_query("DELETE FROM jardinagem.relatorios_diarios WHERE id=%s", (km_id,), fetch="none")
    return {"ok": True, "id": km_id}

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
    # Gerar URLs das fotos em paralelo
    from concurrent.futures import ThreadPoolExecutor
    fotos_list = [dict(f) for f in fotos_raw]
    paths_hoje = [(i, f["storage_path"]) for i, f in enumerate(fotos_list) if f.get("storage_path")]
    urls_hoje = {}
    if paths_hoje:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(storage_url, path): i for i, path in paths_hoje}
            for future, i in futures.items():
                try: urls_hoje[i] = future.result(timeout=10)
                except: urls_hoje[i] = ""
    fotos = []
    for i, f in enumerate(fotos_list):
        f["url"] = urls_hoje.get(i, "") if f.get("storage_path") else ""
        f["criado_em"] = f["criado_em"].isoformat() if f.get("criado_em") else ""
        fotos.append(f)
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
@app.get("/jardinagem/api/inicio")
async def jard_inicio(payload=Depends(verificar_token_jard)):
    """Rota de carregamento rápido — retorna semana ativa + pares + config em 1 chamada."""
    hoje = date.today().isoformat()
    # 1. Semana ativa
    semana = jard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                        FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                        WHERE s.data_ini::date<=%s AND s.data_fim::date>=%s
                        AND s.status='aberta' LIMIT 1""", (hoje,hoje), fetch="one")
    if not semana:
        semana = jard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                            FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                            WHERE s.status='aberta'
                            ORDER BY s.id DESC LIMIT 1""", fetch="one")
    if not semana:
        return {"semana": None, "pares": [], "next_code": 6050}
    sid = semana["id"]
    # 2. Pares da semana (com fotos)
    pares = jard_query("""SELECT p.id,p.codigo_a,p.codigo_d,p.local_nome,p.ordem
                          FROM jardinagem.pares p
                          WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)
                          ORDER BY p.codigo_a""", (sid,))
    fotos = jard_query("""SELECT f.id,f.par_id,f.tipo,f.storage_path
                          FROM jardinagem.fotos f
                          JOIN jardinagem.pares p ON p.id=f.par_id
                          WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (sid,))
    fotos_por_par = {}
    for f in fotos:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = []
        fotos_por_par[pid].append({"id":f["id"],"tipo":f["tipo"],"storage_path":f["storage_path"]})
    pares_com_fotos = []
    for p in pares:
        pd = dict(p)
        pd["fotos"] = fotos_por_par.get(p["id"], [])
        pares_com_fotos.append(pd)
    # 3. Config (next_code)
    cfg = jard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    next_code = int(cfg["valor"]) if cfg else 6050
    return {"semana": dict(semana), "pares": pares_com_fotos, "next_code": next_code}

@app.get("/jardinagem/api/config")
async def jard_config(payload=Depends(verificar_token_jard)):
    rows = jard_query("SELECT * FROM jardinagem.config")
    return {r["chave"]: r["valor"] for r in rows}

@app.get("/jardinagem/api/clientes")
async def jard_clientes(payload=Depends(verificar_token_jard)):
    rows = jard_query("SELECT id,nome FROM public.clientes_garra WHERE ativo=true")
    return [dict(r) for r in rows]

# ── PREVIEW RELATÓRIO ─────────────────────────────────────────
@app.get("/jardinagem/api/km/mes/{mes_id}")
async def jard_km_mes(mes_id: int, payload=Depends(verificar_token_jard)):
    """Retorna todos os KMs do mês em 1 chamada — evita N chamadas /preview."""
    m = jard_query("SELECT ano, mes FROM jardinagem.meses WHERE id=%s", (mes_id,), fetch="one")
    if not m: raise HTTPException(status_code=404, detail="Mês não encontrado")
    kms_raw = jard_query("""
        SELECT r.id, r.data, r.local_nome, r.km_inicial, r.km_final,
               r.hora_inicio, r.hora_fim, r.observacao, r.responsavel,
               u.nome as responsavel_nome
        FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id = r.usuario_id
        WHERE EXTRACT(YEAR FROM r.data) = %s AND EXTRACT(MONTH FROM r.data) = %s
        ORDER BY r.data, r.criado_em
    """, (m["ano"], m["mes"]))
    kms = [{"id": r["id"],
            "data": r["data"].strftime("%d/%m/%Y") if r["data"] else "",
            "local_nome": r["local_nome"] or "",
            "km_inicial": float(r["km_inicial"] or 0),
            "km_final": float(r["km_final"] or 0),
            "hora_inicio": str(r["hora_inicio"]) if r["hora_inicio"] else "",
            "hora_fim": str(r["hora_fim"]) if r["hora_fim"] else "",
            "observacao": r["observacao"] or "",
            "responsavel": r["responsavel_nome"] or r["responsavel"] or ""
            } for r in kms_raw]
    return {"mes_id": mes_id, "relatorios": kms}

@app.get("/jardinagem/api/relatorios/{semana_id}/preview")
async def jard_preview(semana_id: int, payload=Depends(verificar_token_jard)):
    from concurrent.futures import ThreadPoolExecutor
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    # 1 query pares + 1 query fotos (elimina N+1)
    pares_raw = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a", (semana_id,))
    fotos_raw = jard_query("""SELECT f.* FROM jardinagem.fotos f
        JOIN jardinagem.pares p ON p.id=f.par_id
        WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (semana_id,))
    fotos_por_par = {}
    for f in fotos_raw:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = {}
        fotos_por_par[pid][f["tipo"]] = f
    # Coletar paths para gerar URLs em paralelo
    paths_map = {}
    for p in pares_raw:
        pid = p["id"]
        fp = fotos_por_par.get(pid, {})
        fa = fp.get("antes"); fd = fp.get("depois")
        if fa and fa.get("storage_path"): paths_map[f"{pid}_antes"] = fa["storage_path"]
        if fd and fd.get("storage_path"): paths_map[f"{pid}_depois"] = fd["storage_path"]
    # Gerar todas as URLs em paralelo
    urls = {}
    if paths_map:
        with ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(storage_url, path): key for key, path in paths_map.items()}
            for future, key in futures.items():
                try: urls[key] = future.result(timeout=10)
                except: urls[key] = ""
    pares = []
    for p in pares_raw:
        pid = p["id"]
        fp = fotos_por_par.get(pid, {})
        fa = fp.get("antes"); fd = fp.get("depois")
        pares.append({"id":pid,"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":bool(fa),"foto_depois":bool(fd),
                      "url_antes":urls.get(f"{pid}_antes",""),
                      "url_depois":urls.get(f"{pid}_depois","")})
    kms_raw = jard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.data>=%s AND r.data<=%s ORDER BY r.data,r.criado_em""", (sem["data_ini"],sem["data_fim"]))
    kms = [{"id":r["id"],"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local_nome":r["local_nome"] or "",
            "km_inicial":float(r["km_inicial"] or 0),"km_final":float(r["km_final"] or 0),
            "hora_inicio":str(r["hora_inicio"]) if r["hora_inicio"] else "",
            "hora_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
            "observacao":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    return {"semana_id":semana_id,"label":sem["label"],"pares":pares,"relatorios":kms,
            "total_pares":len(pares),"pares_completos":sum(1 for p in pares if p["foto_antes"] and p["foto_depois"]),"total_km":len(kms)}

# ── HELPERS NOME DE ARQUIVO ───────────────────────────────────
_MESES_PT = {1:"Jan",2:"Fev",3:"Mar",4:"Abr",5:"Mai",6:"Jun",
             7:"Jul",8:"Ago",9:"Set",10:"Out",11:"Nov",12:"Dez"}

def nome_arquivo_semana(sem, tipo: str) -> str:
    """Gera nome legível: relatorio-fotos-jun2026-semana1.xlsx"""
    import re
    # Extrair número da semana do label
    num = "?"
    try:
        m = re.search(r'Semana\s*(\d+)', sem.get("label") or "", re.IGNORECASE)
        if m: num = m.group(1)
    except: pass
    # Extrair mês e ano — tenta data_ini primeiro, depois extrai do label
    mes_abr, ano = "???", "????"
    try:
        di = sem.get("data_ini")
        if di and hasattr(di, "month"):
            mes_abr = _MESES_PT.get(di.month, f"{di.month:02d}").lower()
            ano = str(di.year)
        else:
            # Extrai do label: "Semana 1 — 01/06/2026 ..."
            dm = re.search(r'(\d{2})/(\d{2})/(\d{4})', sem.get("label") or "")
            if dm:
                mes_abr = _MESES_PT.get(int(dm.group(2)), dm.group(2)).lower()
                ano = dm.group(3)
    except: pass
    return f"relatorio-{tipo.lower()}-{mes_abr}{ano}-semana{num}.xlsx"

# ── DOWNLOAD EXCEL ────────────────────────────────────────────
@app.get("/jardinagem/api/relatorios/{semana_id}/fotos")
async def jard_excel_fotos(semana_id: int, payload=Depends(verificar_token_jard)):
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_fotos
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    pares_raw = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a", (semana_id,))
    # 1 query para todas as fotos (elimina N+1)
    fotos_raw = jard_query("""SELECT f.* FROM jardinagem.fotos f
        JOIN jardinagem.pares p ON p.id=f.par_id
        WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (semana_id,))
    fotos_por_par = {}
    for f in fotos_raw:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = {}
        fotos_por_par[pid][f["tipo"]] = dict(f)
    pares = []
    for p in pares_raw:
        fp = fotos_por_par.get(p["id"], {})
        pares.append({"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":fp.get("antes"), "foto_depois":fp.get("depois")})
    buf = gerar_relatorio_fotos(semana_dict, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition":f'attachment; filename="{nome_arquivo_semana(sem, "Fotos")}"'})

@app.get("/jardinagem/api/relatorios/{semana_id}/km")
async def jard_excel_km(semana_id: int, payload=Depends(verificar_token_jard)):
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_km
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    kms_raw = jard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.data>=%s AND r.data<=%s ORDER BY r.data,r.criado_em""", (sem["data_ini"],sem["data_fim"]))
    relatorios = [{"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local":r["local_nome"] or "",
                   "km_ini":float(r["km_inicial"] or 0),"km_fin":float(r["km_final"] or 0),
                   "hr_ini":str(r["hora_inicio"]) if r["hora_inicio"] else "","hr_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
                   "obs":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    buf = gerar_relatorio_km(semana_dict, relatorios)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition":f'attachment; filename="{nome_arquivo_semana(sem, "KM")}"'})

@app.post("/jardinagem/api/relatorios/{semana_id}/enviar")
async def jard_enviar_email(semana_id: int, payload=Depends(verificar_token_jard)):
    if not all([MAIL_USERNAME, MAIL_PASSWORD, MAIL_DESTINO]):
        raise HTTPException(status_code=400, detail="Email não configurado")
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_fotos, gerar_relatorio_km
    sem = jard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    pares_raw = jard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a", (semana_id,))
    # 1 query para todas as fotos (elimina N+1)
    fotos_raw_email = jard_query("""SELECT f.* FROM jardinagem.fotos f
        JOIN jardinagem.pares p ON p.id=f.par_id
        WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (semana_id,))
    fotos_por_par_email = {}
    for f in fotos_raw_email:
        pid = f["par_id"]
        if pid not in fotos_por_par_email: fotos_por_par_email[pid] = {}
        fotos_por_par_email[pid][f["tipo"]] = dict(f)
    pares = []
    for p in pares_raw:
        fp = fotos_por_par_email.get(p["id"], {})
        pares.append({"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":fp.get("antes"), "foto_depois":fp.get("depois")})
    kms_raw = jard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.data>=%s AND r.data<=%s ORDER BY r.data,r.criado_em""", (sem["data_ini"],sem["data_fim"]))
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
                          [(nome_arquivo_semana(sem, 'Fotos'), buf_fotos.getvalue()),
                           (nome_arquivo_semana(sem, 'KM'),    buf_km.getvalue())])
        jard_query("INSERT INTO jardinagem.emails_enviados (semana_id,destinatario,assunto,status) VALUES (%s,%s,%s,'enviado')",
                   (semana_id,MAIL_DESTINO,f"Relatório {semana_dict['label']}"), fetch="none")
        jard_query("UPDATE jardinagem.semanas SET status='enviada',enviado_em=NOW() WHERE id=%s", (semana_id,), fetch="none")
        return {"ok": True, "mensagem": f"Relatórios enviados para {MAIL_DESTINO} (cc: {MAIL_CC})"}
    except Exception as e:
        jard_query("INSERT INTO jardinagem.emails_enviados (semana_id,destinatario,assunto,status,erro_msg) VALUES (%s,%s,%s,'erro',%s)",
                   (semana_id,MAIL_DESTINO,f"Relatório {semana_dict['label']}",str(e)), fetch="none")
        raise HTTPException(status_code=500, detail=f"Falha no envio: {str(e)}")

# ═══════════════════════════════════════════════════════════════════════════
# MÓDULO OPERACIONAL — Ordens de Serviço, Partes Diárias, Comissões
# Adicionado em 09/06/2026 — Fase B
# Schema: operacional.* no Neon
# ═══════════════════════════════════════════════════════════════════════════

# ── HELPERS DE QUERY (usa jard_query — mesmo padrão da jardinagem) ──────────

# ── LISTAS BÁSICAS (para popular selects) ───────────────────────────────────

@app.get("/operacional/api/tipos-servico")
async def op_listar_tipos_servico(_auth=Depends(verificar_token)):
    """Lista tipos de serviço ativos para popular select."""
    rows = jard_query(
        "SELECT id, nome, descricao FROM operacional.tipos_servico WHERE ativo=true ORDER BY nome"
    )
    return [dict(r) for r in (rows or [])]

@app.post("/operacional/api/tipos-servico")
async def op_criar_tipo_servico(request: Request, payload=Depends(verificar_admin)):
    """Cria novo tipo de serviço (somente admin)."""
    d = await request.json()
    nome = (d.get("nome") or "").strip()
    descricao = (d.get("descricao") or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório")
    try:
        row = jard_query_id(
            "INSERT INTO operacional.tipos_servico (nome, descricao) VALUES (%s, %s)",
            (nome, descricao or None)
        )
        return dict(row)
    except Exception as e:
        if "duplicate" in str(e).lower():
            raise HTTPException(status_code=409, detail="Tipo de serviço já existe")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/operacional/api/equipamentos")
async def op_listar_equipamentos(_auth=Depends(verificar_token)):
    """Lista equipamentos ativos para popular select."""
    rows = jard_query(
        """SELECT id, codigo, descricao, categoria, medicao, horimetro_atual, km_atual
           FROM operacional.equipamentos
           WHERE ativo=true
           ORDER BY categoria, codigo"""
    )
    return [dict(r) for r in (rows or [])]

@app.get("/operacional/api/clientes")
async def op_listar_clientes(_auth=Depends(verificar_token)):
    """Lista clientes ativos para popular select da OS."""
    rows = jard_query(
        "SELECT id, nome FROM public.clientes_garra WHERE ativo=true ORDER BY nome"
    )
    return [dict(r) for r in (rows or [])]

@app.get("/operacional/api/operadores")
async def op_listar_operadores(_auth=Depends(verificar_token)):
    """Lista usuários elegíveis a operar equipamentos (operadores, motoristas, campo)."""
    rows = jard_query(
        """SELECT id, nome, login, perfil
           FROM public.usuarios_garra
           WHERE ativo=true AND perfil IN ('operador','motorista','campo')
           ORDER BY nome"""
    )
    return [dict(r) for r in (rows or [])]


# ── NUMERAÇÃO DE OS ─────────────────────────────────────────────────────────

@app.get("/operacional/api/proximo-numero")
async def op_proximo_numero(_auth=Depends(verificar_gestor)):
    """Retorna o próximo número de OS disponível para o ano atual."""
    ano = datetime.utcnow().year
    row = jard_query(
        "SELECT operacional.proximo_numero_os(%s) AS numero",
        (ano,), fetch="one"
    )
    return {"numero": row["numero"], "ano": ano}


# ── ORDENS DE SERVIÇO ───────────────────────────────────────────────────────

@app.post("/operacional/api/os")
async def op_criar_os(request: Request, payload=Depends(verificar_gestor)):
    """Cria nova OS. Somente admin, gestor ou luana."""
    d = await request.json()

    # Campos obrigatórios
    obra = (d.get("obra") or "").strip()
    if not obra:
        raise HTTPException(status_code=400, detail="Obra é obrigatória")

    # Cliente: ou cliente_id (cadastrado) OU cliente_nome_avulso
    cliente_id = d.get("cliente_id")
    cliente_nome_avulso = (d.get("cliente_nome_avulso") or "").strip() or None
    if not cliente_id and not cliente_nome_avulso:
        raise HTTPException(status_code=400, detail="Informe cliente cadastrado ou nome avulso")

    tipo_servico_id     = d.get("tipo_servico_id")
    equipamento_id      = d.get("equipamento_id") or None
    operador_id         = d.get("operador_id") or None
    endereco            = (d.get("endereco") or "").strip() or None
    descricao           = (d.get("descricao") or "").strip() or None
    data_inicio         = d.get("data_inicio") or datetime.utcnow().date().isoformat()
    data_fim_prevista   = d.get("data_fim_prevista") or None
    codigo_erp          = (d.get("codigo_erp") or "").strip() or None
    origem              = d.get("origem") or "escritorio"

    # Gerar número de OS
    ano = datetime.utcnow().year
    row = jard_query("SELECT operacional.proximo_numero_os(%s) AS numero", (ano,), fetch="one")
    numero = row["numero"]
    sequencia = int(numero.split("-")[-1])

    # Status inicial baseado em ter ou não codigo_erp
    status = "aberta_completa" if codigo_erp else "aberta_sem_erp"

    # Snapshot do criador
    criado_por = payload.get("sub")  # login
    user_row = jard_query("SELECT id FROM public.usuarios_garra WHERE login=%s",
                         (criado_por,), fetch="one")
    criado_por_id = user_row["id"] if user_row else None

    codigo_erp_em  = datetime.utcnow() if codigo_erp else None
    codigo_erp_por = criado_por_id if codigo_erp else None

    try:
        os_row = jard_query_id(
            """INSERT INTO operacional.ordens_servico
               (numero, ano, sequencia, codigo_erp, codigo_erp_em, codigo_erp_por,
                cliente_id, cliente_nome_avulso, tipo_servico_id,
                equipamento_id, operador_id,
                obra, endereco, descricao,
                data_inicio, data_fim_prevista,
                status, origem, criado_por)
               VALUES (%s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s,%s,%s)""",
            (numero, ano, sequencia, codigo_erp, codigo_erp_em, codigo_erp_por,
             cliente_id, cliente_nome_avulso, tipo_servico_id,
             equipamento_id, operador_id,
             obra, endereco, descricao,
             data_inicio, data_fim_prevista,
             status, origem, criado_por_id)
        )
        return dict(os_row)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao criar OS: {str(e)}")


@app.get("/operacional/api/os")
async def op_listar_os(
    status: Optional[str] = None,
    cliente_id: Optional[str] = None,
    ano: Optional[int] = None,
    busca: Optional[str] = None,
    limit: int = 100,
    _auth=Depends(verificar_token)
):
    """Lista OS com filtros opcionais."""
    sql = """
        SELECT
            os.id, os.numero, os.ano, os.sequencia,
            os.codigo_erp, os.obra, os.endereco, os.descricao,
            os.data_inicio, os.data_fim_prevista, os.data_fim_real,
            os.status, os.origem, os.criado_em,
            os.cliente_id, COALESCE(c.nome, os.cliente_nome_avulso) AS cliente_nome,
            os.tipo_servico_id, ts.nome AS tipo_servico_nome,
            os.equipamento_id, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_descricao,
            os.operador_id, op.nome AS operador_nome,
            u.nome AS criado_por_nome
        FROM operacional.ordens_servico os
        LEFT JOIN public.clientes_garra c       ON c.id = os.cliente_id
        LEFT JOIN operacional.tipos_servico ts  ON ts.id = os.tipo_servico_id
        LEFT JOIN operacional.equipamentos eq   ON eq.id = os.equipamento_id
        LEFT JOIN public.usuarios_garra op      ON op.id = os.operador_id
        LEFT JOIN public.usuarios_garra u       ON u.id = os.criado_por
        WHERE os.ativo = true
    """
    params = []
    if status:
        sql += " AND os.status = %s"; params.append(status)
    if cliente_id:
        sql += " AND os.cliente_id = %s"; params.append(cliente_id)
    if ano:
        sql += " AND os.ano = %s"; params.append(ano)
    if busca:
        sql += " AND (os.numero ILIKE %s OR os.obra ILIKE %s OR os.codigo_erp ILIKE %s)"
        like = f"%{busca}%"
        params.extend([like, like, like])
    sql += " ORDER BY os.ano DESC, os.sequencia DESC LIMIT %s"
    params.append(limit)

    rows = jard_query(sql, tuple(params))
    return [dict(r) for r in (rows or [])]


@app.get("/operacional/api/os/{os_id}")
async def op_detalhe_os(os_id: str, _auth=Depends(verificar_token)):
    """Retorna detalhe completo da OS, com partes diárias."""
    row = jard_query(
        """SELECT os.*,
                  COALESCE(c.nome, os.cliente_nome_avulso) AS cliente_nome,
                  ts.nome AS tipo_servico_nome,
                  u.nome AS criado_por_nome
           FROM operacional.ordens_servico os
           LEFT JOIN public.clientes_garra c       ON c.id = os.cliente_id
           LEFT JOIN operacional.tipos_servico ts  ON ts.id = os.tipo_servico_id
           LEFT JOIN operacional.equipamentos eq   ON eq.id = os.equipamento_id
           LEFT JOIN public.usuarios_garra u       ON u.id = os.criado_por
           LEFT JOIN public.usuarios_garra op      ON op.id = os.operador_id
           WHERE os.id = %s AND os.ativo = true""",
        (os_id,), fetch="one"
    )
    if not row:
        raise HTTPException(status_code=404, detail="OS não encontrada")

    # Buscar partes diárias da OS
    partes = jard_query(
        """SELECT pd.*,
                  e.codigo AS equipamento_codigo, e.descricao AS equipamento_descricao,
                  e.categoria AS equipamento_categoria,
                  COALESCE(u.nome, pd.operador_nome_avulso) AS operador_nome
           FROM operacional.partes_diarias pd
           LEFT JOIN operacional.equipamentos e  ON e.id = pd.equipamento_id
           LEFT JOIN public.usuarios_garra u    ON u.id = pd.operador_id
           WHERE pd.os_id = %s AND pd.ativo = true
           ORDER BY pd.data DESC, pd.criado_em DESC""",
        (os_id,)
    )

    os_dict = dict(row)
    os_dict["partes_diarias"] = [dict(p) for p in (partes or [])]
    return os_dict


@app.patch("/operacional/api/os/{os_id}")
async def op_atualizar_os(os_id: str, request: Request, payload=Depends(verificar_gestor)):
    """Atualiza OS — útil para inserir codigo_erp retroativo, mudar status, etc."""
    d = await request.json()

    # Verificar se OS existe
    existente = jard_query(
        "SELECT * FROM operacional.ordens_servico WHERE id=%s AND ativo=true",
        (os_id,), fetch="one"
    )
    if not existente:
        raise HTTPException(status_code=404, detail="OS não encontrada")

    # Campos editáveis
    campos_editaveis = ["codigo_erp", "obra", "endereco", "descricao",
                        "data_fim_prevista", "data_fim_real", "status",
                        "tipo_servico_id", "cliente_id", "cliente_nome_avulso",
                        "equipamento_id", "operador_id"]
    updates = []
    params = []
    for campo in campos_editaveis:
        if campo in d:
            updates.append(f"{campo} = %s")
            params.append(d[campo] if d[campo] != "" else None)

    if not updates:
        return dict(existente)

    # Snapshot quem inseriu codigo_erp
    if "codigo_erp" in d and d["codigo_erp"]:
        login = payload.get("sub")
        user = jard_query("SELECT id FROM public.usuarios_garra WHERE login=%s",
                         (login,), fetch="one")
        if user:
            updates.append("codigo_erp_em = %s")
            params.append(datetime.utcnow())
            updates.append("codigo_erp_por = %s")
            params.append(user["id"])
        # Se tinha status aberta_sem_erp e agora tem erp, vira aberta_completa
        if existente["status"] == "aberta_sem_erp":
            updates.append("status = %s")
            params.append("aberta_completa")

    updates.append("atualizado_em = %s")
    params.append(datetime.utcnow())
    params.append(os_id)

    sql = f"UPDATE operacional.ordens_servico SET {', '.join(updates)} WHERE id = %s"
    jard_query(sql, tuple(params), fetch="none")

    # Retornar atualizado
    return await op_detalhe_os(os_id, _auth=payload)


@app.delete("/operacional/api/os/{os_id}")
async def op_remover_os(os_id: str, _auth=Depends(verificar_admin)):
    """Soft delete da OS. Somente admin."""
    jard_query(
        "UPDATE operacional.ordens_servico SET ativo=false, atualizado_em=now() WHERE id=%s",
        (os_id,), fetch="none"
    )
    return {"ok": True, "id": os_id}


# ── PARTES DIÁRIAS ──────────────────────────────────────────────────────────

@app.post("/operacional/api/os/{os_id}/partes")
async def op_criar_parte(os_id: str, request: Request, payload=Depends(verificar_token)):
    """Registra parte diária. Qualquer operador logado pode registrar numa OS ativa."""
    d = await request.json()
    login = payload.get("sub","")

    # Verificar se OS existe e está ativa
    os_row = jard_query(
        "SELECT * FROM operacional.ordens_servico WHERE id=%s AND ativo=true",
        (os_id,), fetch="one"
    )
    if not os_row:
        raise HTTPException(status_code=404, detail="OS não encontrada")

    # Campos obrigatórios
    data = d.get("data")
    equipamento_id = d.get("equipamento_id") or os_row.get("equipamento_id")
    if not data or not equipamento_id:
        raise HTTPException(status_code=400, detail="Data e equipamento são obrigatórios")

    # Calcular horas trabalhadas automaticamente
    h_ini = d.get("horimetro_inicial")
    h_fin = d.get("horimetro_final")
    horas = None
    if h_ini is not None and h_fin is not None:
        try:
            horas = round(float(h_fin) - float(h_ini), 2)
            if horas < 0:
                raise HTTPException(status_code=400, detail="Horímetro final menor que inicial")
        except (TypeError, ValueError):
            pass

    # Buscar ID do operador pelo login (quem está logado = criado_por)
    user_row = jard_query(
        "SELECT id FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
        (login, login), fetch="one"
    )
    criado_por_id = user_row["id"] if user_row else None

    # operador_id: pode ser informado no payload (Gilson registrando pelo Emilson)
    # ou default para quem está logado
    operador_id = d.get("operador_id") or criado_por_id

    # Calcular KM percorrido
    km_ini = d.get("km_inicial")
    km_fin = d.get("km_final")
    km_perc = None
    if km_ini is not None and km_fin is not None:
        try: km_perc = round(float(km_fin) - float(km_ini), 1)
        except: pass

    try:
        parte = jard_query_id(
            """INSERT INTO operacional.partes_diarias
               (os_id, equipamento_id, operador_id, operador_nome_avulso,
                data, hora_inicio, hora_fim,
                tipo_medicao, horimetro_inicial, horimetro_final, horas_trabalhadas,
                km_inicial, km_final, km_percorrido,
                quantidade_diarias, qtd_viagens,
                vinculo_operador, observacao, trajeto, por_conta_de, criado_por)
               VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s,%s,%s)""",
            (os_id, equipamento_id, operador_id, d.get("operador_nome_avulso"),
             data, d.get("hora_inicio"), d.get("hora_fim"),
             d.get("tipo_medicao","horimetro"), h_ini, h_fin, horas,
             km_ini, km_fin, km_perc,
             d.get("quantidade_diarias", 0), d.get("qtd_viagens", 0),
             d.get("vinculo_operador","proprio"), d.get("observacao"),
             d.get("trajeto"), d.get("por_conta_de","empresa"),
             criado_por_id)
        )
        # Atualizar horímetro atual do equipamento
        if h_fin is not None:
            jard_query(
                "UPDATE operacional.equipamentos SET horimetro_atual=%s, atualizado_em=now() WHERE id=%s",
                (h_fin, equipamento_id), fetch="none"
            )
        return dict(parte)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao registrar parte: {str(e)}")


@app.get("/operacional/api/os/{os_id}/partes")
async def op_listar_partes(os_id: str, _auth=Depends(verificar_token)):
    """Lista todas as partes diárias de uma OS, com totais acumulados."""
    partes = jard_query(
        """SELECT pd.*,
                  e.codigo  AS equipamento_codigo,
                  e.descricao AS equipamento_descricao,
                  e.categoria AS equipamento_categoria,
                  COALESCE(u.nome, pd.operador_nome_avulso) AS operador_nome
           FROM operacional.partes_diarias pd
           LEFT JOIN operacional.equipamentos e ON e.id = pd.equipamento_id
           LEFT JOIN public.usuarios_garra u    ON u.id = pd.operador_id
           WHERE pd.os_id = %s AND pd.ativo = true
           ORDER BY pd.data ASC, pd.criado_em ASC""",
        (os_id,)
    )
    lista = [dict(p) for p in (partes or [])]

    # Totais acumulados
    total_horas     = sum(float(p.get("horas_trabalhadas") or 0) for p in lista)
    # Total horas cobradas: usa horas_cobradas se editado, senão horas_trabalhadas
    total_horas_cob = sum(
        float(p.get("horas_cobradas") or 0) if float(p.get("horas_cobradas") or 0) > 0
        else float(p.get("horas_trabalhadas") or 0)
        for p in lista
    )
    total_diarias   = sum(float(p.get("quantidade_diarias") or 0) for p in lista)
    total_viagens   = sum(float(p.get("qtd_viagens")       or 0) for p in lista)
    dias_trabalhados= len(set(str(p.get("data",""))[:10] for p in lista if p.get("data")))

    return {
        "partes": lista,
        "totais": {
            "dias_trabalhados":  dias_trabalhados,
            "total_horas":       round(total_horas, 2),
            "total_horas_cobradas": round(total_horas_cob, 2),
            "total_diarias":     total_diarias,
            "total_viagens":     total_viagens,
        }
    }


@app.patch("/operacional/api/partes/{parte_id}")
async def op_atualizar_parte(parte_id: str, request: Request, payload=Depends(verificar_gestor)):
    """Luana/Admin atualiza parte diária — ajusta horas cobradas, diárias, observação."""
    d = await request.json()
    existente = jard_query(
        "SELECT * FROM operacional.partes_diarias WHERE id=%s AND ativo=true",
        (parte_id,), fetch="one"
    )
    if not existente:
        raise HTTPException(status_code=404, detail="Parte diária não encontrada")
    if existente.get("fechado"):
        raise HTTPException(status_code=400, detail="Parte já fechada — não pode editar")

    campos = ["horas_cobradas","quantidade_diarias","qtd_viagens",
              "observacao","hora_inicio","hora_fim",
              "horimetro_inicial","horimetro_final","operador_nome_avulso"]
    updates, params = [], []
    for c in campos:
        if c in d:
            updates.append(f"{c} = %s")
            params.append(d[c] if d[c] != "" else None)

    # Recalcular horas se horímetro foi atualizado
    if "horimetro_inicial" in d or "horimetro_final" in d:
        h_ini = d.get("horimetro_inicial", existente.get("horimetro_inicial"))
        h_fin = d.get("horimetro_final",   existente.get("horimetro_final"))
        if h_ini is not None and h_fin is not None:
            horas = round(float(h_fin) - float(h_ini), 2)
            updates.append("horas_trabalhadas = %s")
            params.append(horas)

    if not updates:
        return dict(existente)

    params.append(parte_id)
    jard_query(
        f"UPDATE operacional.partes_diarias SET {', '.join(updates)} WHERE id=%s",
        tuple(params), fetch="none"
    )
    row = jard_query(
        "SELECT * FROM operacional.partes_diarias WHERE id=%s", (parte_id,), fetch="one"
    )
    return dict(row)


@app.delete("/operacional/api/partes/{parte_id}")
async def op_remover_parte(parte_id: str, _auth=Depends(verificar_gestor)):
    """Soft delete de parte diária."""
    existente = jard_query(
        "SELECT fechado FROM operacional.partes_diarias WHERE id=%s AND ativo=true",
        (parte_id,), fetch="one"
    )
    if not existente:
        raise HTTPException(status_code=404, detail="Parte não encontrada")
    if existente.get("fechado"):
        raise HTTPException(status_code=400, detail="Parte fechada — não pode remover")
    jard_query(
        "UPDATE operacional.partes_diarias SET ativo=false WHERE id=%s",
        (parte_id,), fetch="none"
    )
    return {"ok": True, "id": parte_id}


@app.post("/operacional/api/os/{os_id}/fechar")
async def op_fechar_os(os_id: str, request: Request, payload=Depends(verificar_gestor)):
    """Fecha OS após revisão. Congela todas as partes diárias."""
    os_row = jard_query(
        "SELECT * FROM operacional.ordens_servico WHERE id=%s AND ativo=true",
        (os_id,), fetch="one"
    )
    if not os_row:
        raise HTTPException(status_code=404, detail="OS não encontrada")
    if os_row.get("status") in ("concluida_completa","concluida_sem_erp","cancelada"):
        raise HTTPException(status_code=400, detail="OS já está fechada")

    login = payload.get("sub","")
    user  = jard_query(
        "SELECT id FROM public.usuarios_garra WHERE login=%s", (login,), fetch="one"
    )
    fechado_por_id = user["id"] if user else None
    agora = datetime.utcnow()

    # Auto-preencher horas_cobradas = horas_trabalhadas onde não foi editado (cobradas=0 ou null)
    jard_query(
        """UPDATE operacional.partes_diarias
           SET horas_cobradas = horas_trabalhadas
           WHERE os_id=%s AND ativo=true AND fechado=false
             AND (horas_cobradas IS NULL OR horas_cobradas = 0)
             AND horas_trabalhadas > 0""",
        (os_id,), fetch="none"
    )

    # Fechar todas as partes abertas
    jard_query(
        """UPDATE operacional.partes_diarias
           SET fechado=true, fechado_em=%s, fechado_por=%s
           WHERE os_id=%s AND ativo=true AND fechado=false""",
        (agora, fechado_por_id, os_id), fetch="none"
    )

    # Determinar status final
    novo_status = "concluida_completa" if os_row.get("codigo_erp") else "concluida_sem_erp"

    jard_query(
        """UPDATE operacional.ordens_servico
           SET status=%s, data_fim_real=%s, atualizado_em=%s
           WHERE id=%s""",
        (novo_status, agora.date(), agora, os_id), fetch="none"
    )

    return await op_detalhe_os(os_id, _auth=payload)


@app.get("/operacional/api/os/{os_id}/revisao")
async def op_revisao_os(os_id: str, _auth=Depends(verificar_gestor)):
    """Tela de revisão antes de fechar: OS + partes + totais consolidados."""
    os_detail = await op_detalhe_os(os_id, _auth=_auth)
    partes_data = await op_listar_partes(os_id, _auth=_auth)
    return {
        "os":     os_detail,
        "partes": partes_data["partes"],
        "totais": partes_data["totais"],
    }


@app.get("/operacional/api/minhas-partes")
async def op_minhas_partes(payload=Depends(verificar_token)):
    """Operador vê histórico de partes diárias próprias."""
    login = payload.get("sub","")
    user = jard_query(
        "SELECT id FROM public.usuarios_garra WHERE login=%s", (login,), fetch="one"
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    rows = jard_query(
        """SELECT pd.*, os.numero AS numero_os, os.obra,
                  e.codigo AS equipamento_codigo
           FROM operacional.partes_diarias pd
           JOIN operacional.ordens_servico os ON os.id = pd.os_id
           LEFT JOIN operacional.equipamentos e ON e.id = pd.equipamento_id
           WHERE pd.operador_id = %s AND pd.ativo = true
           ORDER BY pd.data DESC, pd.criado_em DESC
           LIMIT 60""",
        (user["id"],)
    )
    return [dict(r) for r in (rows or [])]


@app.get("/operacional/api/minhas-os")
async def op_minhas_os(payload=Depends(verificar_token)):
    """Operador/motorista vê APENAS as OS onde é o operador previsto e status ativo."""
    login = payload.get("sub","")
    user  = jard_query(
        "SELECT id FROM public.usuarios_garra WHERE login=%s", (login,), fetch="one"
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    rows = jard_query(
        """SELECT os.id, os.numero, os.obra, os.regime_cobranca,
                  os.data_inicio, os.data_fim_prevista, os.status,
                  COALESCE(c.nome, os.cliente_nome_avulso) AS cliente_nome,
                  e.codigo AS equipamento_codigo, e.descricao AS equipamento_descricao,
                  ts.nome AS tipo_servico_nome
           FROM operacional.ordens_servico os
           LEFT JOIN public.clientes_garra c      ON c.id = os.cliente_id
           LEFT JOIN operacional.equipamentos e   ON e.id = os.equipamento_id
           LEFT JOIN operacional.tipos_servico ts ON ts.id = os.tipo_servico_id
           WHERE os.operador_id = %s
             AND os.ativo = true
             AND os.status NOT IN ('concluida_completa','concluida_sem_erp','cancelada')
           ORDER BY os.data_inicio DESC""",
        (user["id"],)
    )
    # Sem campos financeiros — operador não vê valores
    return [dict(r) for r in (rows or [])]


@app.get("/operacional/api/minhas-os/debug")
async def op_minhas_os_debug(payload=Depends(verificar_token)):
    """DEBUG — mostra todos os campos para diagnosticar por que OS não aparece."""
    login = payload.get("sub","") or payload.get("login","")
    user = jard_query(
        "SELECT id, login, nome, perfil FROM public.usuarios_garra WHERE login=%s",
        (login,), fetch="one"
    )
    if not user:
        return {"erro": "usuário não encontrado", "login_buscado": login}
    
    todas_os = jard_query(
        """SELECT id, numero, status, ativo, operador_id, obra
           FROM operacional.ordens_servico
           WHERE operador_id = %s
           ORDER BY data_inicio DESC""",
        (user["id"],)
    )
    
    os_visiveis = jard_query(
        """SELECT id, numero, status
           FROM operacional.ordens_servico
           WHERE operador_id = %s
             AND ativo = true
             AND status NOT IN ('concluida_completa','concluida_sem_erp','cancelada')""",
        (user["id"],)
    )
    
    return {
        "login_jwt": login,
        "usuario": dict(user),
        "total_os_vinculadas": len(todas_os or []),
        "todas_os": [dict(r) for r in (todas_os or [])],
        "os_visiveis_no_mobile": [dict(r) for r in (os_visiveis or [])]
    }


@app.post("/operacional/api/os/avulsa")
async def op_criar_os_avulsa(req: Request, payload=Depends(verificar_token)):
    """Operador cria OS avulsa do campo — sem código ERP, status aberta_sem_erp."""
    login = payload.get("sub","") or payload.get("login","")
    user = jard_query(
        "SELECT id, nome, perfil FROM public.usuarios_garra WHERE login=%s",
        (login,), fetch="one"
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    
    # Perfis autorizados
    if user["perfil"] not in ("operador", "motorista", "admin", "gestor", "luana"):
        raise HTTPException(status_code=403, detail="Perfil sem permissão para criar OS avulsa")
    
    body = await req.json()
    obra            = (body.get("obra") or "").strip()
    cliente_nome    = (body.get("cliente_nome") or "").strip()
    equipamento_id  = body.get("equipamento_id")
    tipo_servico_id = body.get("tipo_servico_id")
    regime_cobranca = (body.get("regime_cobranca") or "diaria").strip()
    observacao      = (body.get("observacao") or "").strip()
    
    if not obra:
        raise HTTPException(status_code=400, detail="Obra é obrigatória")
    
    # Gerar próximo número
    ano = datetime.utcnow().year
    ult = jard_query(
        "SELECT numero, sequencia FROM operacional.ordens_servico WHERE numero LIKE %s ORDER BY sequencia DESC LIMIT 1",
        (f"OS-{ano}-%",), fetch="one"
    )
    if ult and ult.get("sequencia"):
        seq = int(ult["sequencia"]) + 1
    elif ult:
        try:
            seq = int(ult["numero"].split("-")[-1]) + 1
        except Exception:
            seq = 1
    else:
        seq = 1
    numero = f"OS-{ano}-{seq:04d}"
    
    nova = jard_query(
        """INSERT INTO operacional.ordens_servico
           (numero, ano, sequencia, obra, cliente_nome_avulso,
            equipamento_id, tipo_servico_id, regime_cobranca,
            operador_id, status, origem, descricao,
            data_inicio, ativo, criado_por, criado_em)
           VALUES (%s, %s, %s, %s, %s,
                   %s, %s, %s,
                   %s, 'aberta_sem_erp', 'campo', %s,
                   CURRENT_DATE, true, %s, NOW())
           RETURNING id, numero, obra, status""",
        (numero, ano, seq, obra, cliente_nome or None,
         equipamento_id, tipo_servico_id, regime_cobranca,
         user["id"], observacao or None, user["id"]),
        fetch="one"
    )
    return {"ok": True, "os": dict(nova) if nova else {"numero": numero}}

# ═══════════════════════════════════════════════════════════════════════════
# FIM MÓDULO OPERACIONAL
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# MÓDULO PERMISSÕES — controle por colaborador
# ═══════════════════════════════════════════════════════════════════════════

MODULOS_DISPONIVEIS = [
    {"id": "admin_master",        "label": "Admin Master",          "desc": "Painel de gestão"},
    {"id": "jardinagem_desktop",  "label": "Jardinagem Desktop",    "desc": "Relatórios e fotos"},
    {"id": "jardinagem_mobile",   "label": "Jardinagem Mobile",     "desc": "Campo — fotos e KM"},
    {"id": "operacional_mobile",  "label": "Operacional Mobile",    "desc": "OS e horímetro"},
    {"id": "checklist",           "label": "Checklist",             "desc": "Checklist de máquinas"},
    {"id": "checklist_logistica", "label": "Logística (Checklist)", "desc": "Aba de carros de apoio dentro do Checklist"},
]

PERFIL_MODULOS_PADRAO = {
    "admin":     ["admin_master","jardinagem_desktop","jardinagem_mobile","operacional_mobile","checklist","checklist_logistica"],
    "gestor":    ["admin_master","jardinagem_desktop","operacional_mobile"],
    "luana":     ["admin_master","jardinagem_desktop","operacional_mobile"],
    "bruna":     ["admin_master","checklist"],
    "operador":  ["operacional_mobile","checklist"],
    "motorista": ["operacional_mobile","checklist"],
    "campo":     ["jardinagem_mobile"],
}

@app.get("/permissoes/modulos")
async def listar_modulos(_auth=Depends(verificar_admin)):
    """Lista módulos disponíveis."""
    return MODULOS_DISPONIVEIS

@app.get("/permissoes/usuario/{usuario_id}")
async def get_permissoes_usuario(usuario_id: str, payload=Depends(verificar_token)):
    """Retorna permissões. Admin vê qualquer usuário; usuário vê só as próprias."""
    sub = payload.get("sub","")
    perfil = payload.get("perfil","")
    # Buscar UUID do usuário logado se sub for login
    if perfil != "admin":
        user = jard_query(
            "SELECT id FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
            (sub, sub), fetch="one"
        )
        uid_logado = str(user["id"]) if user else None
        if uid_logado != usuario_id:
            raise HTTPException(status_code=403, detail="Acesso negado")
    rows = jard_query(
        "SELECT modulo, permitido FROM public.permissoes_colaborador WHERE usuario_id=%s",
        (usuario_id,)
    )
    perms = {r["modulo"]: r["permitido"] for r in (rows or [])}
    # Se não tem registro, usa padrão do perfil
    user = jard_query(
        "SELECT perfil FROM public.usuarios_garra WHERE id=%s AND ativo=true",
        (usuario_id,), fetch="one"
    )
    if user:
        padrao = PERFIL_MODULOS_PADRAO.get(user["perfil"], [])
        for m in MODULOS_DISPONIVEIS:
            if m["id"] not in perms:
                perms[m["id"]] = m["id"] in padrao
    return perms

@app.post("/permissoes/usuario/{usuario_id}")
async def salvar_permissoes_usuario(usuario_id: str, request: Request, _auth=Depends(verificar_admin)):
    """Salva permissões de um colaborador. Body: {modulo: bool, ...}"""
    d = await request.json()
    for modulo, permitido in d.items():
        jard_query(
            """INSERT INTO public.permissoes_colaborador (usuario_id, modulo, permitido)
               VALUES (%s, %s, %s)
               ON CONFLICT (usuario_id, modulo)
               DO UPDATE SET permitido=%s, atualizado_em=now()""",
            (usuario_id, modulo, bool(permitido), bool(permitido)), fetch="none"
        )
    return {"ok": True}

@app.get("/permissoes/todos")
async def get_todas_permissoes(_auth=Depends(verificar_admin)):
    """Retorna permissões de todos os usuários ativos para a tela de gestão."""
    usuarios = jard_query(
        "SELECT id, login, nome, perfil FROM public.usuarios_garra WHERE ativo=true ORDER BY perfil, nome"
    )
    perms = jard_query(
        "SELECT usuario_id, modulo, permitido FROM public.permissoes_colaborador"
    )
    perm_map = {}
    for p in (perms or []):
        uid = str(p["usuario_id"])
        if uid not in perm_map: perm_map[uid] = {}
        perm_map[uid][p["modulo"]] = p["permitido"]

    result = []
    for u in (usuarios or []):
        uid = str(u["id"])
        padrao = PERFIL_MODULOS_PADRAO.get(u["perfil"], [])
        user_perms = {}
        for m in MODULOS_DISPONIVEIS:
            if uid in perm_map and m["id"] in perm_map[uid]:
                user_perms[m["id"]] = perm_map[uid][m["id"]]
            else:
                user_perms[m["id"]] = m["id"] in padrao
        result.append({
            "id": uid,
            "login": u["login"],
            "nome": u["nome"],
            "perfil": u["perfil"],
            "permissoes": user_perms,
        })
    return result

# FIM MÓDULO PERMISSÕES
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/checklist")
async def checklist_app():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "checklist", "index.html")
    return FileResponse(path)

@app.get("/checklist/sw.js")
async def checklist_sw():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "checklist", "sw.js")
    return FileResponse(path, media_type="application/javascript")

@app.get("/checklist/manifest.json")
async def checklist_manifest():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "operacional", "checklist", "manifest.json")
    return FileResponse(path)

@app.get("/mobile")
async def mobile_app():
    return FileResponse(os.path.join(os.path.dirname(__file__), "../../operacional/static/mobile.html"))

@app.get("/mobile/sw.js")
async def mobile_sw():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "../../operacional/static/sw.js"),
        media_type="application/javascript"
    )

@app.get("/mobile/manifest.json")
async def mobile_manifest():
    return FileResponse(os.path.join(os.path.dirname(__file__), "../../operacional/static/mobile.manifest.json"))

# ── FALLBACK — compatibilidade com browsers que cachearam URLs antigas ───────
from fastapi.responses import RedirectResponse

@app.get("/manifest.json")
async def redirect_manifest():
    return RedirectResponse(url="/mobile/manifest.json")

@app.get("/sw.js")
async def redirect_sw():
    return RedirectResponse(url="/mobile/sw.js")

@app.get("/favicon.ico")
async def redirect_favicon():
    return RedirectResponse(url="/static/icons/favicon.ico")

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    from fastapi.responses import JSONResponse, Response
    path = request.url.path
    if path.startswith("/jardinagem/api/") or path.startswith("/api/") or path.startswith("/auth/") or path.startswith("/usuarios") or path.startswith("/checklist/") or path.startswith("/frota") or path.startswith("/logistica/") or path.startswith("/operacional/"):
        return JSONResponse({"ok": False, "error": "Rota não encontrada", "path": path}, status_code=404)
    return Response(status_code=404)

@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    from fastapi.responses import JSONResponse, Response
    path = request.url.path
    if path.startswith("/jardinagem/api/") or path.startswith("/api/") or path.startswith("/auth/") or path.startswith("/usuarios") or path.startswith("/checklist/") or path.startswith("/frota") or path.startswith("/logistica/") or path.startswith("/operacional/"):
        return JSONResponse({"ok": False, "error": "Erro interno do servidor"}, status_code=500)
    return Response(status_code=500)
    raise exc

# ── HEALTH CHECK — mantém banco Neon acordado ──────────────────
@app.get("/api/health")
async def health():
    try:
        jard_query("SELECT 1", fetch="one")
        return {"status":"ok","db":"conectado","sistema":"Garra Gestão API","versao":"6.0.0"}
    except Exception as e:
        return {"status":"erro","db":str(e)}

@app.get("/api/debug/usuarios")
async def debug_usuarios(_auth=Depends(verificar_admin)):
    """Diagnóstico de usuários — somente admin."""
    rows = jard_query(
        """SELECT login, email, perfil, ativo,
                  LEFT(senha_hash,7) AS hash_inicio,
                  CASE WHEN senha_hash = '$2b$12$y4jgMhNSKtoeBtad7lKEOev.tHk8S9OA1SpPHrowz5XT.AQJK.iZK'
                       THEN 'padrao_1234' ELSE 'outra' END AS senha_status
           FROM public.usuarios_garra
           ORDER BY ativo DESC, perfil, login""",
        fetch="all"
    )
    return [dict(r) for r in (rows or [])]

@app.get("/")
async def root():
    return RedirectResponse(url="/admin")

@app.get("/jardinagem/api/health")
async def jard_health():
    try:
        jard_query("SELECT 1", fetch="one")
        return {"status":"ok","db":"conectado","modulo":"jardinagem"}
    except Exception as e:
        return {"status":"erro","db":str(e)}
