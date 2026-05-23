# ═══════════════════════════════════════════════════════════
# main.py — API Garra Check List v5
# FastAPI + asyncpg + Neon PostgreSQL
# Auth unificada com usuarios_garra + redefinição de senha
# ═══════════════════════════════════════════════════════════

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from typing import Optional, List
import asyncpg, bcrypt, os, json, time, secrets, smtplib
from datetime import datetime
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

app = FastAPI(title="Garra Gestão API", version="5.0.0")

# ── CONFIG ────────────────────────────────────────────────────
DATABASE_URL  = os.environ.get("DATABASE_URL", "")
MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
FRONTEND_URL  = os.environ.get("FRONTEND_URL", "https://garra-checklist-app.onrender.com")

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
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    return response

# ── BANCO ─────────────────────────────────────────────────────
async def get_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

# ── STARTUP ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Garantir schema e tabelas existem (migration já rodada no Neon)
        await conn.execute("SET search_path TO public, checklist")
        print("✅ API conectada ao Neon — banco unificado Garra")
    except Exception as e:
        print(f"⚠️ Erro no startup: {e}")
    finally:
        await conn.close()

# ── EMAIL ─────────────────────────────────────────────────────
def enviar_email(destino: str, assunto: str, corpo_html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"]    = MAIL_USERNAME
    msg["To"]      = destino
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(MAIL_USERNAME, MAIL_PASSWORD)
        s.sendmail(MAIL_USERNAME, destino, msg.as_string())

def validar_senha(senha: str) -> str | None:
    """Retorna mensagem de erro se senha inválida, None se ok."""
    if len(senha) < 8:
        return "A senha deve ter no mínimo 8 caracteres."
    if not any(c.isupper() for c in senha):
        return "A senha deve conter pelo menos uma letra maiúscula."
    if not any(c.isdigit() for c in senha):
        return "A senha deve conter pelo menos um número."
    if not any(c in "!@#$%^&*()_+-=[]{}|;':\",./<>?" for c in senha):
        return "A senha deve conter pelo menos um caractere especial (!@#$...)."
    return None

# ── MODELOS Pydantic ──────────────────────────────────────────
class LoginRequest(BaseModel):
    login: str
    senha: str

class UsuarioCreate(BaseModel):
    login: str
    nome: str
    email: str
    senha: str
    perfil: str
    perfil_checklist: Optional[str] = None

class UsuarioEdit(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    perfil: Optional[str] = None
    perfil_checklist: Optional[str] = None
    ativo: Optional[bool] = None

class SenhaChange(BaseModel):
    senha_atual: str
    senha_nova: str

class SenhaResetRequest(BaseModel):
    login: str

class SenhaResetConfirm(BaseModel):
    token: str
    senha_nova: str

class EnvioCreate(BaseModel):
    envio_id: str
    usuario_login: str
    usuario_nome: str
    cl_id: str
    cl_label: Optional[str] = ""
    meta: dict = {}
    respostas: dict = {}
    pts: int = 0
    tem_nc: bool = False
    total_nc: int = 0
    enviado_em: Optional[str] = None

class FrotaItem(BaseModel):
    categoria: str
    identificacao: str
    descricao: Optional[str] = ""

class ChecklistModeloCreate(BaseModel):
    cl_id: str
    label: str
    icon: str = "📋"
    descricao: Optional[str] = ""
    vehicle_cat: Optional[str] = ""
    is_default: bool = False
    score_full: int = 100
    score_nc: int = 60
    score_obs: int = 20
    score_ontime: int = 10
    questions: List[dict] = []
    steps: List[dict] = []

class LogMotoristaCreate(BaseModel):
    motor_id: str
    nome: str
    cpf: Optional[str] = ""
    cnh: Optional[str] = ""
    telefone: Optional[str] = ""
    status: str = "ativo"
    observacoes: Optional[str] = ""

class LogVeiculoCreate(BaseModel):
    veiculo_id: str
    car_id: str
    placa: Optional[str] = ""
    modelo: Optional[str] = ""
    ano: Optional[int] = None
    cor: Optional[str] = ""
    status: str = "disponivel"
    extras: List[dict] = []
    observacoes: Optional[str] = ""

class LogRegistroCreate(BaseModel):
    registro_id: str
    responsavel: str
    data_hora: str
    carros: List[dict] = []

# ═══════════════════════════════════════════════════════════
# AUTH — LOGIN
# ═══════════════════════════════════════════════════════════

@app.post("/auth/login")
async def login(req: LoginRequest, request: Request, db=Depends(get_db)):
    check_rate_limit(request.client.host)
    user = await db.fetchrow(
        "SELECT * FROM public.usuarios_garra WHERE login=$1 AND ativo=TRUE",
        req.login
    )
    if not user:
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    if not bcrypt.checkpw(req.senha.encode(), user["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")

    return {
        "login":        user["login"],
        "nome":         user["nome"],
        "perfil":       user["perfil"],
        "perfil_checklist": user["perfil_checklist"],
        "role":         user["perfil_checklist"] or user["perfil"],
        "pts":          user["pts"] or 0,
        "total_envios": user["total_envios"] or 0,
        "email":        user["email"] or "",
    }

# ═══════════════════════════════════════════════════════════
# AUTH — REDEFINIÇÃO DE SENHA
# ═══════════════════════════════════════════════════════════

@app.post("/auth/solicitar-reset")
async def solicitar_reset(req: SenhaResetRequest, db=Depends(get_db)):
    """Envia email com link para redefinir senha."""
    user = await db.fetchrow(
        "SELECT id, nome, email FROM public.usuarios_garra WHERE login=$1 AND ativo=TRUE",
        req.login
    )
    # Sempre retorna sucesso (não revela se usuário existe)
    if not user or not user["email"]:
        return {"ok": True, "msg": "Se o usuário existir, um email será enviado."}

    token = secrets.token_urlsafe(32)
    await db.execute(
        """INSERT INTO public.senha_reset_tokens (usuario_id, token)
           VALUES ($1, $2)""",
        user["id"], token
    )

    link = f"{FRONTEND_URL}/reset-senha.html?token={token}"
    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">
      <div style="background:#1A2A5E;padding:20px;border-bottom:3px solid #E8820C;">
        <h2 style="color:#fff;margin:0;">Garra Terraplenagem</h2>
      </div>
      <div style="padding:24px;background:#F0F4FF;">
        <p>Olá, <strong>{user['nome']}</strong>!</p>
        <p>Recebemos uma solicitação para redefinir sua senha.</p>
        <p style="margin:24px 0;">
          <a href="{link}"
             style="background:#1A2A5E;color:#fff;padding:12px 24px;
                    border-radius:8px;text-decoration:none;font-weight:bold;">
            Redefinir minha senha
          </a>
        </p>
        <p style="color:#64748B;font-size:12px;">
          Este link expira em 1 hora.<br>
          Se você não solicitou, ignore este email.
        </p>
      </div>
    </div>
    """
    try:
        enviar_email(user["email"], "Redefinição de senha — Garra Gestão", corpo)
    except Exception as e:
        print(f"Erro ao enviar email: {e}")
        raise HTTPException(status_code=500, detail="Erro ao enviar email.")

    return {"ok": True, "msg": "Email enviado com sucesso."}


@app.post("/auth/confirmar-reset")
async def confirmar_reset(req: SenhaResetConfirm, db=Depends(get_db)):
    """Confirma redefinição de senha com o token recebido."""
    # Validar senha
    erro = validar_senha(req.senha_nova)
    if erro:
        raise HTTPException(status_code=400, detail=erro)

    # Buscar token válido
    token_row = await db.fetchrow(
        """SELECT t.*, u.id as uid FROM public.senha_reset_tokens t
           JOIN public.usuarios_garra u ON u.id = t.usuario_id
           WHERE t.token=$1 AND t.usado=FALSE AND t.expira_em > NOW()""",
        req.token
    )
    if not token_row:
        raise HTTPException(status_code=400, detail="Token inválido ou expirado.")

    # Atualizar senha
    novo_hash = bcrypt.hashpw(req.senha_nova.encode(), bcrypt.gensalt(12)).decode()
    await db.execute(
        "UPDATE public.usuarios_garra SET senha_hash=$1, atualizado_em=NOW() WHERE id=$2",
        novo_hash, token_row["uid"]
    )
    # Marcar token como usado
    await db.execute(
        "UPDATE public.senha_reset_tokens SET usado=TRUE WHERE token=$1",
        req.token
    )
    return {"ok": True, "msg": "Senha redefinida com sucesso."}


@app.post("/auth/alterar-senha")
async def alterar_senha(req: SenhaChange, login: str, db=Depends(get_db)):
    """Usuário logado altera a própria senha."""
    erro = validar_senha(req.senha_nova)
    if erro:
        raise HTTPException(status_code=400, detail=erro)

    user = await db.fetchrow(
        "SELECT * FROM public.usuarios_garra WHERE login=$1", login
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if not bcrypt.checkpw(req.senha_atual.encode(), user["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Senha atual incorreta")

    novo_hash = bcrypt.hashpw(req.senha_nova.encode(), bcrypt.gensalt(12)).decode()
    await db.execute(
        "UPDATE public.usuarios_garra SET senha_hash=$1, atualizado_em=NOW() WHERE login=$2",
        novo_hash, login
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# USUÁRIOS
# ═══════════════════════════════════════════════════════════

@app.get("/usuarios")
async def listar_usuarios(db=Depends(get_db)):
    rows = await db.fetch(
        """SELECT login, nome, email, perfil, perfil_checklist,
                  pts, total_envios, ativo, criado_em
           FROM public.usuarios_garra ORDER BY nome"""
    )
    return [dict(r) for r in rows]

@app.post("/usuarios")
async def criar_usuario(u: UsuarioCreate, db=Depends(get_db)):
    erro = validar_senha(u.senha)
    if erro:
        raise HTTPException(status_code=400, detail=erro)

    existe = await db.fetchval(
        "SELECT id FROM public.usuarios_garra WHERE login=$1 OR email=$2",
        u.login, u.email
    )
    if existe:
        raise HTTPException(status_code=409, detail="Login ou email já cadastrado.")

    hash_senha = bcrypt.hashpw(u.senha.encode(), bcrypt.gensalt(12)).decode()
    await db.execute(
        """INSERT INTO public.usuarios_garra
           (login, nome, email, senha_hash, perfil, perfil_checklist)
           VALUES ($1,$2,$3,$4,$5,$6)""",
        u.login, u.nome, u.email, hash_senha, u.perfil, u.perfil_checklist
    )

    # Enviar email de boas-vindas com a senha temporária
    if u.email and MAIL_USERNAME:
        corpo = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;">
          <div style="background:#1A2A5E;padding:20px;border-bottom:3px solid #E8820C;">
            <h2 style="color:#fff;margin:0;">Bem-vindo à Garra Gestão!</h2>
          </div>
          <div style="padding:24px;background:#F0F4FF;">
            <p>Olá, <strong>{u.nome}</strong>!</p>
            <p>Seu acesso foi criado. Seus dados de login:</p>
            <div style="background:#fff;border:1.5px solid #CBD5E1;border-radius:8px;padding:16px;margin:16px 0;">
              <p><strong>Usuário:</strong> {u.login}</p>
              <p><strong>Senha temporária:</strong> {u.senha}</p>
            </div>
            <p style="color:#E8820C;font-weight:bold;">
              Por segurança, altere sua senha no primeiro acesso.
            </p>
            <p style="color:#64748B;font-size:12px;">
              Acesse: {FRONTEND_URL}
            </p>
          </div>
        </div>
        """
        try:
            enviar_email(u.email, "Acesso criado — Garra Gestão", corpo)
        except Exception as e:
            print(f"Erro email boas-vindas: {e}")

    return {"ok": True}

@app.post("/usuarios/{login}/editar")
async def editar_usuario(login: str, dados: UsuarioEdit, db=Depends(get_db)):
    sets, params = [], []
    for campo, valor in dados.dict(exclude_none=True).items():
        params.append(valor)
        sets.append(f"{campo}=${len(params)}")
    if not sets:
        return {"ok": True}
    params.append(login)
    await db.execute(
        f"UPDATE public.usuarios_garra SET {','.join(sets)}, atualizado_em=NOW() WHERE login=${len(params)}",
        *params
    )
    return {"ok": True}

@app.delete("/usuarios/{login}")
async def remover_usuario(login: str, db=Depends(get_db)):
    await db.execute(
        "UPDATE public.usuarios_garra SET ativo=FALSE WHERE login=$1", login
    )
    return {"ok": True}

@app.patch("/usuarios/{login}/pts")
async def atualizar_pts(login: str, pts: int, db=Depends(get_db)):
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=$1, atualizado_em=NOW() WHERE login=$2",
        pts, login
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# CHECKLIST MODELOS
# ═══════════════════════════════════════════════════════════

@app.get("/checklist/modelos")
async def listar_modelos(db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT * FROM checklist.modelos WHERE ativo=TRUE ORDER BY label"
    )
    result = []
    for r in rows:
        d = dict(r)
        d["questions"] = d["questions"] if isinstance(d["questions"], list) else json.loads(d["questions"] or "[]")
        d["steps"]     = d["steps"]     if isinstance(d["steps"],     list) else json.loads(d["steps"]     or "[]")
        result.append(d)
    return result

@app.post("/checklist/modelos")
async def salvar_modelo(cl: ChecklistModeloCreate, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM checklist.modelos WHERE cl_id=$1", cl.cl_id
    )
    if existe:
        await db.execute(
            """UPDATE checklist.modelos SET label=$1,icon=$2,descricao=$3,vehicle_cat=$4,
               is_default=$5,score_full=$6,score_nc=$7,score_obs=$8,score_ontime=$9,
               questions=$10,steps=$11 WHERE cl_id=$12""",
            cl.label, cl.icon, cl.descricao, cl.vehicle_cat, cl.is_default,
            cl.score_full, cl.score_nc, cl.score_obs, cl.score_ontime,
            json.dumps(cl.questions), json.dumps(cl.steps), cl.cl_id
        )
    else:
        await db.execute(
            """INSERT INTO checklist.modelos
               (cl_id,label,icon,descricao,vehicle_cat,is_default,
                score_full,score_nc,score_obs,score_ontime,questions,steps)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
            cl.cl_id, cl.label, cl.icon, cl.descricao, cl.vehicle_cat, cl.is_default,
            cl.score_full, cl.score_nc, cl.score_obs, cl.score_ontime,
            json.dumps(cl.questions), json.dumps(cl.steps)
        )
    return {"ok": True}

@app.delete("/checklist/modelos/{cl_id}")
async def remover_modelo(cl_id: str, db=Depends(get_db)):
    await db.execute(
        "UPDATE checklist.modelos SET ativo=FALSE WHERE cl_id=$1", cl_id
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# CHECKLIST ENVIOS
# ═══════════════════════════════════════════════════════════

@app.get("/checklist/envios")
async def listar_envios(
    usuario: Optional[str] = None,
    cl_id:   Optional[str] = None,
    limit:   int = 100,
    db=Depends(get_db)
):
    where, params = "WHERE arquivado=FALSE", []
    if usuario:
        params.append(usuario)
        where += f" AND usuario_login=${len(params)}"
    if cl_id:
        params.append(cl_id)
        where += f" AND cl_id=${len(params)}"
    params.append(limit)
    rows = await db.fetch(
        f"SELECT * FROM checklist.envios {where} ORDER BY enviado_em DESC LIMIT ${len(params)}",
        *params
    )
    result = []
    for r in rows:
        d = dict(r)
        d["meta"]      = d["meta"]      if isinstance(d["meta"],      dict) else json.loads(d["meta"]      or "{}")
        d["respostas"] = d["respostas"] if isinstance(d["respostas"], dict) else json.loads(d["respostas"] or "{}")
        result.append(d)
    return result

@app.post("/checklist/envios")
async def salvar_envio(e: EnvioCreate, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM checklist.envios WHERE envio_id=$1", e.envio_id
    )
    if existe:
        return {"ok": True, "duplicado": True}
    data = datetime.fromisoformat(e.enviado_em) if e.enviado_em else datetime.now()
    await db.execute(
        """INSERT INTO checklist.envios
           (envio_id,usuario_login,usuario_nome,cl_id,cl_label,
            meta,respostas,pts,tem_nc,total_nc,enviado_em)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
        e.envio_id, e.usuario_login, e.usuario_nome, e.cl_id, e.cl_label,
        json.dumps(e.meta), json.dumps(e.respostas), e.pts, e.tem_nc, e.total_nc, data
    )
    await db.execute(
        """UPDATE public.usuarios_garra
           SET pts=pts+$1, total_envios=total_envios+1, atualizado_em=NOW()
           WHERE login=$2""",
        e.pts, e.usuario_login
    )
    return {"ok": True}

@app.patch("/checklist/envios/{envio_id}/arquivar")
async def arquivar_envio(envio_id: str, db=Depends(get_db)):
    await db.execute(
        "UPDATE checklist.envios SET arquivado=TRUE WHERE envio_id=$1", envio_id
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# FROTA
# ═══════════════════════════════════════════════════════════

@app.get("/frota")
async def listar_frota(db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT * FROM checklist.frota WHERE ativo=TRUE ORDER BY categoria, identificacao"
    )
    return [dict(r) for r in rows]

@app.post("/frota")
async def salvar_frota(item: FrotaItem, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM checklist.frota WHERE categoria=$1 AND identificacao=$2",
        item.categoria, item.identificacao
    )
    if existe:
        await db.execute(
            "UPDATE checklist.frota SET descricao=$1, ativo=TRUE WHERE categoria=$2 AND identificacao=$3",
            item.descricao, item.categoria, item.identificacao
        )
    else:
        await db.execute(
            "INSERT INTO checklist.frota (categoria,identificacao,descricao) VALUES ($1,$2,$3)",
            item.categoria, item.identificacao, item.descricao
        )
    return {"ok": True}

@app.delete("/frota/{categoria}/{identificacao}")
async def remover_frota(categoria: str, identificacao: str, db=Depends(get_db)):
    await db.execute(
        "UPDATE checklist.frota SET ativo=FALSE WHERE categoria=$1 AND identificacao=$2",
        categoria, identificacao
    )
    await db.execute(
        "UPDATE checklist.envios SET arquivado=TRUE WHERE meta->>'equipamento'=$1 OR meta->>'veiculo'=$1",
        identificacao
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# LOGÍSTICA
# ═══════════════════════════════════════════════════════════

@app.get("/logistica/motoristas")
async def listar_motoristas(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist.log_motoristas ORDER BY nome")
    return [dict(r) for r in rows]

@app.post("/logistica/motoristas")
async def salvar_motorista(m: LogMotoristaCreate, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM checklist.log_motoristas WHERE motor_id=$1", m.motor_id
    )
    if existe:
        await db.execute(
            """UPDATE checklist.log_motoristas
               SET nome=$1,cpf=$2,cnh=$3,telefone=$4,status=$5,
                   observacoes=$6,atualizado_em=NOW() WHERE motor_id=$7""",
            m.nome, m.cpf, m.cnh, m.telefone, m.status, m.observacoes, m.motor_id
        )
    else:
        await db.execute(
            """INSERT INTO checklist.log_motoristas
               (motor_id,nome,cpf,cnh,telefone,status,observacoes)
               VALUES ($1,$2,$3,$4,$5,$6,$7)""",
            m.motor_id, m.nome, m.cpf, m.cnh, m.telefone, m.status, m.observacoes
        )
    return {"ok": True}

@app.delete("/logistica/motoristas/{motor_id}")
async def remover_motorista(motor_id: str, db=Depends(get_db)):
    await db.execute(
        "DELETE FROM checklist.log_motoristas WHERE motor_id=$1", motor_id
    )
    return {"ok": True}

@app.get("/logistica/veiculos")
async def listar_veiculos(db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT * FROM checklist.log_veiculos ORDER BY car_id"
    )
    result = []
    for r in rows:
        d = dict(r)
        d["extras"] = d["extras"] if isinstance(d["extras"], list) else json.loads(d["extras"] or "[]")
        result.append(d)
    return result

@app.post("/logistica/veiculos")
async def salvar_veiculo(v: LogVeiculoCreate, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM checklist.log_veiculos WHERE veiculo_id=$1", v.veiculo_id
    )
    if existe:
        await db.execute(
            """UPDATE checklist.log_veiculos
               SET car_id=$1,placa=$2,modelo=$3,ano=$4,cor=$5,
                   status=$6,extras=$7,observacoes=$8,atualizado_em=NOW()
               WHERE veiculo_id=$9""",
            v.car_id, v.placa, v.modelo, v.ano, v.cor,
            v.status, json.dumps(v.extras), v.observacoes, v.veiculo_id
        )
    else:
        await db.execute(
            """INSERT INTO checklist.log_veiculos
               (veiculo_id,car_id,placa,modelo,ano,cor,status,extras,observacoes)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            v.veiculo_id, v.car_id, v.placa, v.modelo, v.ano, v.cor,
            v.status, json.dumps(v.extras), v.observacoes
        )
    return {"ok": True}

@app.delete("/logistica/veiculos/{veiculo_id}")
async def remover_veiculo(veiculo_id: str, db=Depends(get_db)):
    await db.execute(
        "DELETE FROM checklist.log_veiculos WHERE veiculo_id=$1", veiculo_id
    )
    return {"ok": True}

@app.get("/logistica/registros")
async def listar_registros(limit: int = 50, db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT * FROM checklist.log_registros ORDER BY data_hora DESC LIMIT $1", limit
    )
    result = []
    for r in rows:
        d = dict(r)
        d["carros"] = d["carros"] if isinstance(d["carros"], list) else json.loads(d["carros"] or "[]")
        result.append(d)
    return result

@app.post("/logistica/registros")
async def salvar_registro(r: LogRegistroCreate, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM checklist.log_registros WHERE registro_id=$1", r.registro_id
    )
    if existe:
        await db.execute(
            """UPDATE checklist.log_registros
               SET responsavel=$1,data_hora=$2,carros=$3 WHERE registro_id=$4""",
            r.responsavel, datetime.fromisoformat(r.data_hora),
            json.dumps(r.carros), r.registro_id
        )
    else:
        await db.execute(
            """INSERT INTO checklist.log_registros
               (registro_id,responsavel,data_hora,carros)
               VALUES ($1,$2,$3,$4)""",
            r.registro_id, r.responsavel,
            datetime.fromisoformat(r.data_hora), json.dumps(r.carros)
        )
    return {"ok": True}

@app.delete("/logistica/registros/{registro_id}")
async def remover_registro(registro_id: str, db=Depends(get_db)):
    await db.execute(
        "DELETE FROM checklist.log_registros WHERE registro_id=$1", registro_id
    )
    return {"ok": True}

# ── HEALTH CHECK ──────────────────────────────────────────────
@app.get("/")
async def health():
    return {
        "status":  "ok",
        "sistema": "Garra Gestão API",
        "versao":  "5.0.0",
        "banco":   "Neon PostgreSQL — banco unificado"
    }
