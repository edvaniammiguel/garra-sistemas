# ═══════════════════════════════════════════════════════════
# main.py — API Garra Check List
# FastAPI + PostgreSQL (asyncpg)
# Deploy: Render Web Service (Python 3)
# ═══════════════════════════════════════════════════════════

from fastapi import FastAPI, HTTPException, Depends, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Any
import asyncpg
import bcrypt
import os
import json
import time
from datetime import datetime
from collections import defaultdict

app = FastAPI(title="Garra Check List API", version="1.0.0")

# ── RATE LIMITER SIMPLES (brute force protection) ───────────
_login_attempts: dict = defaultdict(list)
MAX_ATTEMPTS  = 10   # máximo de tentativas
WINDOW_SECS   = 300  # janela de 5 minutos

def check_rate_limit(ip: str):
    now  = time.time()
    reqs = [t for t in _login_attempts[ip] if now - t < WINDOW_SECS]
    _login_attempts[ip] = reqs
    if len(reqs) >= MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail=f"Muitas tentativas. Aguarde {WINDOW_SECS//60} minutos.")
    _login_attempts[ip].append(now)

# ── CORS — permite o front-end acessar a API ────────────────
ALLOWED_ORIGINS = [
    "https://garra-checklist-app.onrender.com",
    "https://garra-sistemas.onrender.com",
    "http://localhost:3000",   # desenvolvimento local
    "http://127.0.0.1:5500",  # Live Server local
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
    allow_headers=["Content-Type", "Authorization"],
)

# ── BANCO DE DADOS ──────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

async def get_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

# ── STARTUP: criar tabelas e usuários padrão ────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["X-Frame-Options"]           = "DENY"
    response.headers["X-XSS-Protection"]          = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "geolocation=(), microphone=()"
    return response

@app.on_event("startup")
async def startup():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Lê e executa o schema SQL
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        if os.path.exists(schema_path):
            with open(schema_path) as f:
                sql = f.read()
            # Remove os INSERTs de usuários do schema (vamos fazer com hash correto)
            sql_limpo = sql.split("-- Usuários padrão")[0]
            await conn.execute(sql_limpo)

        # Cria usuários com hash bcrypt correto
        usuarios_padrao = [
            ("admin",     "Administrador Garra", "garra2024", "manager"),
            ("gestor",    "Gestor de Frota",     "garra2024", "manager"),
            ("gilson",    "Gilson",               "garra2024", "superior"),
            ("marco",     "Marco Aurélio",        "garra2024", "superior"),
            ("andre",     "André",                "123456",    "driver"),
            ("emerson",   "Emerson",              "123456",    "driver"),
            ("samuel",    "Samuel",               "123456",    "driver"),
            ("franciele", "Franciele",            "123456",    "driver"),
            ("gilberto",  "Gilberto",             "123456",    "driver"),
            ("geraldo",   "Geraldo",              "123456",    "driver"),
            ("joao",      "João Pedro",           "123456",    "driver"),
            ("marcio",    "Márcio",               "123456",    "driver"),
            ("motorista", "Motorista Demo",       "123456",    "driver"),
        ]
        for login, nome, senha, perfil in usuarios_padrao:
            existe = await conn.fetchval("SELECT id FROM usuarios WHERE login=$1", login)
            if not existe:
                hash_senha = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
                await conn.execute(
                    "INSERT INTO usuarios (login, nome, senha_hash, perfil) VALUES ($1,$2,$3,$4)",
                    login, nome, hash_senha, perfil
                )
        # Atualiza constraint de perfil para incluir diarista
        try:
            await conn.execute("""
                ALTER TABLE usuarios DROP CONSTRAINT IF EXISTS usuarios_perfil_check;
                ALTER TABLE usuarios ADD CONSTRAINT usuarios_perfil_check
                CHECK (perfil IN ('manager','superior','driver','diarista'));
            """)
        except Exception as ce:
            print(f"⚠️ Constraint update: {ce}")
        print("✅ Banco inicializado com sucesso")
    except Exception as e:
        print(f"⚠️ Erro no startup: {e}")
    finally:
        await conn.close()

# ═══════════════════════════════════════════════════════════
# MODELOS Pydantic
# ═══════════════════════════════════════════════════════════

class LoginRequest(BaseModel):
    login: str
    senha: str

class UsuarioCreate(BaseModel):
    login: str
    nome: str
    senha: str
    perfil: str

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

class LogRegistroCreate(BaseModel):
    registro_id: str
    responsavel: str
    data_hora: str
    carros: List[dict] = []

class FrotaItem(BaseModel):
    categoria: str
    identificacao: str
    descricao: Optional[str] = ""

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

# ═══════════════════════════════════════════════════════════
# AUTENTICAÇÃO
# ═══════════════════════════════════════════════════════════

@app.post("/auth/login")
async def login(req: LoginRequest, request: Request, db=Depends(get_db)):
    check_rate_limit(request.client.host)
    user = await db.fetchrow(
        "SELECT * FROM usuarios WHERE login=$1 AND ativo=TRUE", req.login
    )
    if not user:
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    if not bcrypt.checkpw(req.senha.encode(), user["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Usuário ou senha incorretos")
    return {
        "id": str(user["id"]),
        "login": user["login"],
        "nome": user["nome"],
        "perfil": user["perfil"],
        "pts": user["pts"],
        "submissions": user["total_envios"],
    }

# ═══════════════════════════════════════════════════════════
# USUÁRIOS
# ═══════════════════════════════════════════════════════════

@app.get("/usuarios")
async def listar_usuarios(db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT id, login, nome, perfil, pts, total_envios, ativo FROM usuarios WHERE ativo=TRUE ORDER BY nome"
    )
    return [dict(r) for r in rows]

@app.post("/usuarios")
async def criar_usuario(u: UsuarioCreate, db=Depends(get_db)):
    # Valida campos
    if not u.login or len(u.login) < 3:
        raise HTTPException(status_code=400, detail="Login deve ter pelo menos 3 caracteres")
    if len(u.login) > 50:
        raise HTTPException(status_code=400, detail="Login muito longo")
    if not u.nome or len(u.nome.strip()) < 2:
        raise HTTPException(status_code=400, detail="Nome inválido")
    if not u.senha or len(u.senha) < 4:
        raise HTTPException(status_code=400, detail="Senha deve ter pelo menos 4 caracteres")
    if u.perfil not in ('manager', 'superior', 'driver', 'diarista'):
        raise HTTPException(status_code=400, detail="Perfil inválido")
    # Sanitiza login — apenas letras, números, ponto e hífen
    import re
    if not re.match(r'^[a-z0-9._-]+$', u.login):
        raise HTTPException(status_code=400, detail="Login deve conter apenas letras minúsculas, números, ponto ou hífen")
    existe = await db.fetchval("SELECT id FROM usuarios WHERE login=$1", u.login)
    if existe:
        raise HTTPException(status_code=400, detail="Login já existe")
    hash_senha = bcrypt.hashpw(u.senha.encode(), bcrypt.gensalt()).decode()
    row = await db.fetchrow(
        "INSERT INTO usuarios (login, nome, senha_hash, perfil) VALUES ($1,$2,$3,$4) RETURNING id, login, nome, perfil",
        u.login, u.nome, hash_senha, u.perfil
    )
    return dict(row)

@app.post("/usuarios/{login}/editar")
async def editar_usuario(login: str, dados: dict, db=Depends(get_db)):
    nome   = dados.get("nome")
    perfil = dados.get("perfil")
    senha  = dados.get("senha")
    if nome and perfil:
        await db.execute(
            "UPDATE usuarios SET nome=$1, perfil=$2, atualizado_em=NOW() WHERE login=$3",
            nome, perfil, login
        )
    if senha:
        hash_senha = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()
        await db.execute(
            "UPDATE usuarios SET senha_hash=$1, atualizado_em=NOW() WHERE login=$2",
            hash_senha, login
        )
    return {"ok": True}

@app.delete("/usuarios/{login}")
async def remover_usuario(login: str, db=Depends(get_db)):
    # Marca como inativo E atualiza timestamp
    await db.execute(
        "UPDATE usuarios SET ativo=FALSE, atualizado_em=NOW() WHERE login=$1", login
    )
    return {"ok": True, "login": login}

@app.patch("/usuarios/{login}/pts")
async def atualizar_pts(login: str, pts: int, db=Depends(get_db)):
    await db.execute(
        "UPDATE usuarios SET pts=pts+$1, total_envios=total_envios+1, atualizado_em=NOW() WHERE login=$2",
        pts, login
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# CHECK LIST — MODELOS
# ═══════════════════════════════════════════════════════════

@app.get("/checklist/modelos")
async def listar_modelos(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM checklist_modelos WHERE ativo=TRUE ORDER BY is_default DESC, label")
    result = []
    for r in rows:
        d = dict(r)
        d["questions"] = json.loads(d["questions"]) if isinstance(d["questions"], str) else d["questions"]
        d["steps"]     = json.loads(d["steps"])     if isinstance(d["steps"], str)     else d["steps"]
        result.append(d)
    return result

@app.post("/checklist/modelos")
async def salvar_modelo(m: ChecklistModeloCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist_modelos WHERE cl_id=$1", m.cl_id)
    if existe:
        await db.execute("""
            UPDATE checklist_modelos SET label=$1, icon=$2, descricao=$3, vehicle_cat=$4,
            score_full=$5, score_nc=$6, score_obs=$7, score_ontime=$8,
            questions=$9, steps=$10, atualizado_em=NOW() WHERE cl_id=$11
        """, m.label, m.icon, m.descricao, m.vehicle_cat,
            m.score_full, m.score_nc, m.score_obs, m.score_ontime,
            json.dumps(m.questions), json.dumps(m.steps), m.cl_id)
    else:
        await db.execute("""
            INSERT INTO checklist_modelos (cl_id, label, icon, descricao, vehicle_cat,
            is_default, score_full, score_nc, score_obs, score_ontime, questions, steps)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        """, m.cl_id, m.label, m.icon, m.descricao, m.vehicle_cat,
            m.is_default, m.score_full, m.score_nc, m.score_obs, m.score_ontime,
            json.dumps(m.questions), json.dumps(m.steps))
    return {"ok": True}

@app.delete("/checklist/modelos/{cl_id}")
async def remover_modelo(cl_id: str, db=Depends(get_db)):
    await db.execute("UPDATE checklist_modelos SET ativo=FALSE WHERE cl_id=$1", cl_id)
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# CHECK LIST — ENVIOS
# ═══════════════════════════════════════════════════════════

@app.get("/checklist/envios")
async def listar_envios(
    usuario: Optional[str] = None,
    cl_id: Optional[str] = None,
    limit: int = 100,
    db=Depends(get_db)
):
    where = "WHERE TRUE"
    params = []
    if usuario:
        params.append(usuario)
        where += f" AND usuario_login=${len(params)}"
    if cl_id:
        params.append(cl_id)
        where += f" AND cl_id=${len(params)}"
    params.append(limit)
    rows = await db.fetch(
        f"SELECT * FROM checklist_envios {where} ORDER BY enviado_em DESC LIMIT ${len(params)}",
        *params
    )
    result = []
    for r in rows:
        d = dict(r)
        d["meta"]      = json.loads(d["meta"])      if isinstance(d["meta"], str)      else d["meta"]
        d["respostas"] = json.loads(d["respostas"]) if isinstance(d["respostas"], str) else d["respostas"]
        result.append(d)
    return result

@app.post("/checklist/envios")
async def salvar_envio(e: EnvioCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM checklist_envios WHERE envio_id=$1", e.envio_id)
    if existe:
        return {"ok": True, "duplicado": True}
    data = datetime.fromisoformat(e.enviado_em) if e.enviado_em else datetime.now()
    await db.execute("""
        INSERT INTO checklist_envios
        (envio_id, usuario_login, usuario_nome, cl_id, cl_label, meta, respostas, pts, tem_nc, total_nc, enviado_em)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
    """, e.envio_id, e.usuario_login, e.usuario_nome, e.cl_id, e.cl_label,
        json.dumps(e.meta), json.dumps(e.respostas), e.pts, e.tem_nc, e.total_nc, data)
    # Atualiza pontos do usuário
    await db.execute(
        "UPDATE usuarios SET pts=pts+$1, total_envios=total_envios+1, atualizado_em=NOW() WHERE login=$2",
        e.pts, e.usuario_login
    )
    return {"ok": True}

@app.patch("/checklist/envios/{envio_id}/arquivar")
async def arquivar_envio(envio_id: str, db=Depends(get_db)):
    await db.execute("UPDATE checklist_envios SET arquivado=TRUE WHERE envio_id=$1", envio_id)
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# FROTA
# ═══════════════════════════════════════════════════════════

@app.get("/frota")
async def listar_frota(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM frota ORDER BY categoria, identificacao")
    return [dict(r) for r in rows]

@app.post("/frota")
async def salvar_frota(item: FrotaItem, db=Depends(get_db)):
    existe = await db.fetchval(
        "SELECT id FROM frota WHERE categoria=$1 AND identificacao=$2",
        item.categoria, item.identificacao
    )
    if existe:
        await db.execute(
            "UPDATE frota SET descricao=$1, ativo=TRUE WHERE categoria=$2 AND identificacao=$3",
            item.descricao, item.categoria, item.identificacao
        )
    else:
        await db.execute(
            "INSERT INTO frota (categoria, identificacao, descricao) VALUES ($1,$2,$3)",
            item.categoria, item.identificacao, item.descricao
        )
    return {"ok": True}

@app.delete("/frota/{categoria}/{identificacao}")
async def remover_frota(categoria: str, identificacao: str, db=Depends(get_db)):
    await db.execute(
        "UPDATE frota SET ativo=FALSE WHERE categoria=$1 AND identificacao=$2",
        categoria, identificacao
    )
    # Arquiva envios relacionados
    await db.execute(
        "UPDATE checklist_envios SET arquivado=TRUE WHERE meta->>'equipamento'=$1 OR meta->>'veiculo'=$1",
        identificacao
    )
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# LOGÍSTICA — MOTORISTAS
# ═══════════════════════════════════════════════════════════

@app.get("/logistica/motoristas")
async def listar_motoristas(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM log_motoristas ORDER BY nome")
    return [dict(r) for r in rows]

@app.post("/logistica/motoristas")
async def salvar_motorista(m: LogMotoristaCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM log_motoristas WHERE motor_id=$1", m.motor_id)
    if existe:
        await db.execute("""
            UPDATE log_motoristas SET nome=$1, cpf=$2, cnh=$3, telefone=$4,
            status=$5, observacoes=$6, atualizado_em=NOW() WHERE motor_id=$7
        """, m.nome, m.cpf, m.cnh, m.telefone, m.status, m.observacoes, m.motor_id)
    else:
        await db.execute("""
            INSERT INTO log_motoristas (motor_id, nome, cpf, cnh, telefone, status, observacoes)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
        """, m.motor_id, m.nome, m.cpf, m.cnh, m.telefone, m.status, m.observacoes)
    return {"ok": True}

@app.delete("/logistica/motoristas/{motor_id}")
async def remover_motorista(motor_id: str, db=Depends(get_db)):
    await db.execute("DELETE FROM log_motoristas WHERE motor_id=$1", motor_id)
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# LOGÍSTICA — VEÍCULOS
# ═══════════════════════════════════════════════════════════

@app.get("/logistica/veiculos")
async def listar_veiculos(db=Depends(get_db)):
    rows = await db.fetch("SELECT * FROM log_veiculos ORDER BY car_id")
    result = []
    for r in rows:
        d = dict(r)
        d["extras"] = json.loads(d["extras"]) if isinstance(d["extras"], str) else d["extras"]
        result.append(d)
    return result

@app.post("/logistica/veiculos")
async def salvar_veiculo(v: LogVeiculoCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM log_veiculos WHERE veiculo_id=$1", v.veiculo_id)
    if existe:
        await db.execute("""
            UPDATE log_veiculos SET car_id=$1, placa=$2, modelo=$3, ano=$4, cor=$5,
            status=$6, extras=$7, observacoes=$8, atualizado_em=NOW() WHERE veiculo_id=$9
        """, v.car_id, v.placa, v.modelo, v.ano, v.cor,
            v.status, json.dumps(v.extras), v.observacoes, v.veiculo_id)
    else:
        await db.execute("""
            INSERT INTO log_veiculos (veiculo_id, car_id, placa, modelo, ano, cor, status, extras, observacoes)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
        """, v.veiculo_id, v.car_id, v.placa, v.modelo, v.ano, v.cor,
            v.status, json.dumps(v.extras), v.observacoes)
    return {"ok": True}

@app.delete("/logistica/veiculos/{veiculo_id}")
async def remover_veiculo(veiculo_id: str, db=Depends(get_db)):
    await db.execute("DELETE FROM log_veiculos WHERE veiculo_id=$1", veiculo_id)
    return {"ok": True}

# ═══════════════════════════════════════════════════════════
# LOGÍSTICA — REGISTROS
# ═══════════════════════════════════════════════════════════

@app.get("/logistica/registros")
async def listar_registros(limit: int = 50, db=Depends(get_db)):
    rows = await db.fetch(
        "SELECT * FROM log_registros ORDER BY data_hora DESC LIMIT $1", limit
    )
    result = []
    for r in rows:
        d = dict(r)
        d["carros"] = json.loads(d["carros"]) if isinstance(d["carros"], str) else d["carros"]
        result.append(d)
    return result

@app.post("/logistica/registros")
async def salvar_registro(r: LogRegistroCreate, db=Depends(get_db)):
    existe = await db.fetchval("SELECT id FROM log_registros WHERE registro_id=$1", r.registro_id)
    if existe:
        await db.execute("""
            UPDATE log_registros SET responsavel=$1, data_hora=$2, carros=$3 WHERE registro_id=$4
        """, r.responsavel, datetime.fromisoformat(r.data_hora), json.dumps(r.carros), r.registro_id)
    else:
        await db.execute("""
            INSERT INTO log_registros (registro_id, responsavel, data_hora, carros)
            VALUES ($1,$2,$3,$4)
        """, r.registro_id, r.responsavel, datetime.fromisoformat(r.data_hora), json.dumps(r.carros))
    return {"ok": True}

@app.delete("/logistica/registros/{registro_id}")
async def remover_registro(registro_id: str, db=Depends(get_db)):
    await db.execute("DELETE FROM log_registros WHERE registro_id=$1", registro_id)
    return {"ok": True}

# ── HEALTH CHECK ────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "ok", "sistema": "Garra Check List API", "versao": "1.0.0"}
