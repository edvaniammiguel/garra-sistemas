"""core.auth — JWT, verificadores de perfil, rate limiter e validação de senha."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import time
import jwt as pyjwt
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, Header, Depends
from .config import JWT_SECRET, JWT_EXPIRY_HOURS
from .db import jard_query
from .permissions import perfil_modulos_padrao

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

def exigir_acesso_jardinagem(payload=Depends(verificar_token_jard)):
    """Garante que o usuário tem permissão de jardinagem (banco tem prioridade
    sobre o padrão do perfil). Bloqueia operador/motorista/bruna."""
    perfil = (payload.get("perfil") or "").lower()
    uid = payload.get("sub")
    # 1. Permissão explícita no banco (admin marcou)
    permitido = None
    try:
        row = jard_query(
            "SELECT permitido FROM public.permissoes_colaborador "
            "WHERE usuario_id=%s AND modulo IN ('jardinagem_mobile','jardinagem_desktop') "
            "ORDER BY permitido DESC LIMIT 1",
            (uid,), fetch="one"
        )
        if row is not None:
            permitido = bool(row["permitido"])
    except Exception:
        permitido = None
    # 2. Sem registro no banco → usa padrão do perfil
    if permitido is None:
        padrao = perfil_modulos_padrao(perfil)
        permitido = ("jardinagem_mobile" in padrao) or ("jardinagem_desktop" in padrao)
    if not permitido:
        raise HTTPException(status_code=403, detail="Sem acesso ao módulo de Jardinagem")
    return payload

def validar_senha(senha: str) -> Optional[str]:
    if len(senha) < 6: return "Senha deve ter no mínimo 6 caracteres."
    return None

# ══════════════════════════════════════════════════════════════
# ROTAS CHECKLIST
# ══════════════════════════════════════════════════════════════

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

