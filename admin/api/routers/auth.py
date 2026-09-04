"""routers.auth — Autenticação (login, reset, renovar, WebAuthn/biometria),
Usuários e Permissões: 23 rotas do domínio de identidade.

Refatoração Fase 2 · Etapa 4 (04/07/2026). Corpos IDÊNTICOS aos do main.py.
"""
import os, json, time, secrets
import bcrypt
import jwt as pyjwt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends, Header, Body
from fastapi.responses import JSONResponse

from core.config import JWT_SECRET, JWT_EXPIRY_HOURS, FRONTEND_URL
from core.db import get_db, ajard_query
from core.auth import (
    check_rate_limit, validar_senha,
    verificar_token, verificar_admin, verificar_gestor,
)
from core.webauthn import (
    gerar_opcoes_registro, verificar_registro,
    gerar_opcoes_login, verificar_login,
)
from core.helpers import enviar_email_smtp
from core.models import (
    LoginRequest, UsuarioCreate, UsuarioEdit, SenhaChange,
    SenhaResetRequest, SenhaResetConfirm, PerfilCreate, PerfilUpdate,
)
from core.permissions import MODULOS_DISPONIVEIS, perfil_modulos_padrao

router = APIRouter()

@router.post("/auth/login")
async def login(req: LoginRequest, request: Request, db=Depends(get_db)):
    check_rate_limit(request.client.host)
    ident = (req.login or "").strip().lower()
    user = await db.fetchrow(
        "SELECT * FROM public.usuarios_garra WHERE (LOWER(login)=$1 OR LOWER(email)=$1) AND ativo=TRUE",
        ident
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
        "luana": "/admin",
        "campo": "/mobile",
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
        padrao = await perfil_modulos_padrao(user["perfil"])
        for m in MODULOS_DISPONIVEIS:
            if m["id"] not in perms:
                perms[m["id"]] = m["id"] in padrao
        if user["perfil"] == "admin":
            perms = {m["id"]: True for m in MODULOS_DISPONIVEIS}
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

@router.post("/auth/solicitar-reset")
async def solicitar_reset(req: SenhaResetRequest, db=Depends(get_db)):
    # O modal pede o EMAIL, mas usuários também podem digitar o login —
    # buscar pelos dois (mesmo padrão do /auth/login).
    user = await db.fetchrow(
        "SELECT id, nome, email FROM public.usuarios_garra WHERE (login=$1 OR email=$1) AND ativo=TRUE", req.login
    )
    if not user or not user["email"]:
        return {"ok": True, "msg": "Se o usuário existir, um email será enviado."}
    token = secrets.token_urlsafe(32)
    await db.execute(
        "INSERT INTO public.senha_reset_tokens (usuario_id, token, expira_em) "
        "VALUES ($1, $2, NOW() + INTERVAL '1 hour')",
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
        # incluir_cc=False: link de redefinição é PESSOAL — nunca pode ir em
        # cópia para as caixas da empresa (permitiria redefinir senha alheia).
        enviar_email_smtp(user["email"], "Redefinição de senha — Garra Gestão", corpo, incluir_cc=False)
        print(f"[Reset] SMTP aceitou o email de redefinição para {user['email']}")
    except Exception as e:
        print(f"[Reset] FALHA SMTP ao enviar para {user['email']}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail="Erro ao enviar email.")
    return {"ok": True, "msg": "Email enviado com sucesso."}

@router.post("/auth/confirmar-reset")
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

@router.post("/auth/renovar")
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
    user = await ajard_query(
        "SELECT id, perfil, pts, total_envios FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
        (login, login), fetch="one"
    )

    # Carregar permissões efetivas (DB + padrão do perfil) — mesma lógica do /auth/login
    perms = {}
    if user:
        try:
            perfil = user.get("perfil") or payload.get("perfil", "")
            rows_perm = await ajard_query(
                "SELECT modulo, permitido FROM public.permissoes_colaborador WHERE usuario_id=%s",
                (str(user["id"]),), fetch="all"
            )
            perms = {r["modulo"]: r["permitido"] for r in (rows_perm or [])}
            padrao = await perfil_modulos_padrao(perfil)
            for m in MODULOS_DISPONIVEIS:
                if m["id"] not in perms:
                    perms[m["id"]] = m["id"] in padrao
            if perfil == "admin":
                perms = {m["id"]: True for m in MODULOS_DISPONIVEIS}
        except Exception:
            perms = {}

    return {
        "token": novo_token,
        "id": str(user["id"]) if user else None,
        "login": login,
        "nome": payload.get("nome", ""),
        "perfil": payload.get("perfil", ""),
        "perfil_checklist": payload.get("perfil_checklist", ""),
        "pts": (user["pts"] if user else 0) or 0,
        "total_envios": (user["total_envios"] if user else 0) or 0,
        "permsDB": perms,
    }

@router.post("/auth/webauthn/registro/desafio")
async def webauthn_registro_desafio(db=Depends(get_db), payload=Depends(verificar_token)):
    """Passo 1 do cadastro (usuário LOGADO): gera as opções para
    navigator.credentials.create() e persiste o desafio."""
    user = await db.fetchrow(
        "SELECT id, login, nome FROM public.usuarios_garra WHERE login=$1 AND ativo=TRUE",
        payload["login"]
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    existentes = await db.fetch(
        "SELECT credential_id FROM public.credenciais_webauthn WHERE usuario_id=$1 AND ativo=TRUE",
        user["id"]
    )
    options_json, desafio = gerar_opcoes_registro(dict(user), [dict(r) for r in existentes])
    await db.execute("DELETE FROM public.webauthn_desafios WHERE login=$1 AND tipo='registro'", user["login"])
    await db.execute(
        "INSERT INTO public.webauthn_desafios (login, desafio, tipo) VALUES ($1,$2,'registro')",
        user["login"], desafio
    )
    return json.loads(options_json)

@router.post("/auth/webauthn/registro/verificar")
async def webauthn_registro_verificar(request: Request, db=Depends(get_db), payload=Depends(verificar_token)):
    """Passo 2 do cadastro: valida a resposta do create() e salva a chave pública."""
    body = await request.json()
    credencial = body.get("credencial")
    apelido = (body.get("apelido") or "")[:60]
    if not credencial:
        raise HTTPException(status_code=400, detail="Credencial ausente")
    row = await db.fetchrow(
        "SELECT desafio FROM public.webauthn_desafios "
        "WHERE login=$1 AND tipo='registro' AND criado_em > NOW() - INTERVAL '5 minutes' "
        "ORDER BY criado_em DESC LIMIT 1",
        payload["login"]
    )
    if not row:
        raise HTTPException(status_code=400, detail="Desafio expirado — tente novamente")
    try:
        dados = verificar_registro(credencial, row["desafio"])
    except Exception:
        raise HTTPException(status_code=400, detail="Falha na validação da credencial")
    user = await db.fetchrow("SELECT id FROM public.usuarios_garra WHERE login=$1", payload["login"])
    await db.execute(
        "INSERT INTO public.credenciais_webauthn (usuario_id, credential_id, public_key, sign_count, apelido) "
        "VALUES ($1,$2,$3,$4,$5) ON CONFLICT (credential_id) DO NOTHING",
        user["id"], dados["credential_id"], dados["public_key"], dados["sign_count"], apelido
    )
    await db.execute("DELETE FROM public.webauthn_desafios WHERE login=$1 AND tipo='registro'", payload["login"])
    return {"ok": True, "mensagem": "Biometria cadastrada — próximo login pode ser pela digital"}

@router.post("/auth/webauthn/login/desafio")
async def webauthn_login_desafio(request: Request, db=Depends(get_db)):
    """Passo 1 do login por digital (DESLOGADO): recebe o login/email e
    gera as opções para navigator.credentials.get()."""
    check_rate_limit(request.client.host)
    body = await request.json()
    login_req = (body.get("login") or "").strip()
    if not login_req:
        raise HTTPException(status_code=400, detail="Informe o login")
    user = await db.fetchrow(
        "SELECT id, login FROM public.usuarios_garra WHERE (login=$1 OR email=$1) AND ativo=TRUE",
        login_req
    )
    creds = []
    if user:
        creds = await db.fetch(
            "SELECT credential_id FROM public.credenciais_webauthn WHERE usuario_id=$1 AND ativo=TRUE",
            user["id"]
        )
    if not user or not creds:
        # mesma mensagem para login inexistente e sem biometria — não vaza cadastro
        raise HTTPException(status_code=404, detail="Biometria não cadastrada para este usuário")
    options_json, desafio = gerar_opcoes_login([dict(r) for r in creds])
    await db.execute("DELETE FROM public.webauthn_desafios WHERE login=$1 AND tipo='login'", user["login"])
    await db.execute(
        "INSERT INTO public.webauthn_desafios (login, desafio, tipo) VALUES ($1,$2,'login')",
        user["login"], desafio
    )
    return json.loads(options_json)

@router.post("/auth/webauthn/login/verificar")
async def webauthn_login_verificar(request: Request, db=Depends(get_db)):
    """Passo 2 do login por digital: valida a assinatura e emite o MESMO
    payload do /auth/login (token + permsDB + redirect)."""
    check_rate_limit(request.client.host)
    body = await request.json()
    login_req = (body.get("login") or "").strip()
    credencial = body.get("credencial")
    if not login_req or not credencial:
        raise HTTPException(status_code=400, detail="Dados incompletos")
    user = await db.fetchrow(
        "SELECT * FROM public.usuarios_garra WHERE (login=$1 OR email=$1) AND ativo=TRUE",
        login_req
    )
    if not user:
        raise HTTPException(status_code=401, detail="Falha na autenticação biométrica")
    desafio_row = await db.fetchrow(
        "SELECT desafio FROM public.webauthn_desafios "
        "WHERE login=$1 AND tipo='login' AND criado_em > NOW() - INTERVAL '5 minutes' "
        "ORDER BY criado_em DESC LIMIT 1",
        user["login"]
    )
    if not desafio_row:
        raise HTTPException(status_code=400, detail="Desafio expirado — tente novamente")
    cred_id = (credencial.get("id") or "")
    cred_row = await db.fetchrow(
        "SELECT credential_id, public_key, sign_count FROM public.credenciais_webauthn "
        "WHERE usuario_id=$1 AND credential_id=$2 AND ativo=TRUE",
        user["id"], cred_id
    )
    if not cred_row:
        raise HTTPException(status_code=401, detail="Falha na autenticação biométrica")
    try:
        novo_count = verificar_login(
            credencial, desafio_row["desafio"],
            cred_row["public_key"], cred_row["sign_count"] or 0
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Falha na autenticação biométrica")
    await db.execute(
        "UPDATE public.credenciais_webauthn SET sign_count=$1, ultimo_uso=NOW() WHERE credential_id=$2",
        novo_count, cred_id
    )
    await db.execute("DELETE FROM public.webauthn_desafios WHERE login=$1 AND tipo='login'", user["login"])

    # ── Mesmo payload do /auth/login ──
    token = pyjwt.encode({
        "sub":              user["login"],
        "login":            user["login"],
        "nome":             user["nome"],
        "perfil":           user["perfil"],
        "perfil_checklist": user["perfil_checklist"],
        "exp":              datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }, JWT_SECRET, algorithm="HS256")
    redirects = {
        "admin": "/admin", "gestor": "/admin", "luana": "/admin",
        "campo": "/mobile", "operador": "/mobile", "motorista": "/mobile", "bruna": "/mobile"
    }
    try:
        rows_perm = await db.fetch(
            "SELECT modulo, permitido FROM public.permissoes_colaborador WHERE usuario_id=$1",
            user["id"]
        )
        perms = {r["modulo"]: r["permitido"] for r in (rows_perm or [])}
        padrao = await perfil_modulos_padrao(user["perfil"])
        for m in MODULOS_DISPONIVEIS:
            if m["id"] not in perms:
                perms[m["id"]] = m["id"] in padrao
        if user["perfil"] == "admin":
            perms = {m["id"]: True for m in MODULOS_DISPONIVEIS}
    except Exception:
        perms = {}
    return {
        "token": token,
        "id": str(user["id"]),
        "login": user["login"], "nome": user["nome"],
        "perfil": user["perfil"], "perfil_checklist": user["perfil_checklist"],
        "role": user["perfil_checklist"] or user["perfil"],
        "redirect_url": redirects.get(user["perfil"], "/admin"),
        "permsDB": perms,
        "pts": user["pts"] or 0, "total_envios": user["total_envios"] or 0,
        "email": user["email"] or "",
    }

@router.post("/auth/alterar-senha")
async def alterar_senha(req: SenhaChange, db=Depends(get_db), _auth=Depends(verificar_token)):
    # Login vem do token do usuário logado (não query param) — mais seguro
    login = _auth.get("sub") or _auth.get("login")
    if not login:
        raise HTTPException(status_code=401, detail="Token inválido")
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

@router.get("/usuarios")
async def listar_usuarios(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch(
        "SELECT id,login,nome,email,perfil,perfil_checklist,pts,total_envios,ativo,criado_em FROM public.usuarios_garra ORDER BY nome"
    )
    return [dict(r) for r in rows]

@router.post("/usuarios")
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

@router.post("/usuarios/{login}/editar")
async def editar_usuario(login: str, dados: UsuarioEdit, db=Depends(get_db), _auth=Depends(verificar_admin)):
    d = dados.dict(exclude_none=True)
    # Senha é tratada à parte: NUNCA vai direto pro SET (a coluna é senha_hash
    # e o valor precisa de bcrypt). Antes deste fix, o campo era descartado
    # silenciosamente pelo Pydantic — a troca de senha pelo Admin não funcionava.
    senha = d.pop("senha", None)
    sets, params = [], []
    for campo, valor in d.items():
        params.append(valor); sets.append(f"{campo}=${len(params)}")
    if senha:
        erro = validar_senha(senha)
        if erro: raise HTTPException(status_code=400, detail=erro)
        novo_hash = bcrypt.hashpw(senha.encode(), bcrypt.gensalt(12)).decode()
        params.append(novo_hash); sets.append(f"senha_hash=${len(params)}")
    if not sets: return {"ok": True}
    params.append(login)
    try:
        await db.execute(
            f"UPDATE public.usuarios_garra SET {','.join(sets)},atualizado_em=NOW() WHERE login=${len(params)}", *params
        )
    except Exception as e:
        if "email" in str(e).lower() and "unique" in str(e).lower():
            raise HTTPException(status_code=409, detail="Este email já está cadastrado em outro usuário.")
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {str(e)[:120]}")
    return {"ok": True}

@router.delete("/usuarios/{login}")
async def remover_usuario(login: str, db=Depends(get_db), _auth=Depends(verificar_admin)):
    await db.execute("UPDATE public.usuarios_garra SET ativo=FALSE WHERE login=$1", login)
    return {"ok": True}

@router.patch("/usuarios/{login}/pts")
async def atualizar_pts(login: str, pts: int, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=$1, atualizado_em=NOW() WHERE login=$2", pts, login
    )
    return {"ok": True}

@router.post("/usuarios/{login}/ajustar-pts")
async def ajustar_pts(login: str, request: Request, db=Depends(get_db), _auth=Depends(verificar_gestor)):
    """Ajusta pontos (subtrair ou adicionar) com motivo registrado."""
    body = await request.json()
    ajuste = int(body.get("ajuste", 0))
    motivo = (body.get("motivo") or "").strip()
    if ajuste == 0:
        raise HTTPException(status_code=400, detail="Ajuste não pode ser zero")
    if not motivo:
        raise HTTPException(status_code=400, detail="Motivo é obrigatório")

    # Buscar usuário
    user = await db.fetchrow(
        "SELECT id, login, nome, pts FROM public.usuarios_garra WHERE login=$1 AND ativo=true", login
    )
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    pts_atual = user["pts"] or 0
    pts_novo = max(0, pts_atual + ajuste)  # Não permite negativo

    # Atualizar pontos
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=$1, atualizado_em=NOW() WHERE login=$2",
        pts_novo, login
    )

    # Registrar log do ajuste
    gestor_login = _auth.get("sub", "") or _auth.get("login", "")
    try:
        await db.execute("""
            INSERT INTO public.log_ajuste_pontos
            (usuario_login, usuario_nome, pts_antes, ajuste, pts_depois, motivo, ajustado_por, criado_em)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
        """, login, user["nome"], pts_atual, ajuste, pts_novo, motivo, gestor_login)
    except Exception:
        # Tabela pode não existir ainda — cria na próxima migration
        pass

    return {
        "ok": True,
        "login": login,
        "pts_antes": pts_atual,
        "ajuste": ajuste,
        "pts_depois": pts_novo,
        "motivo": motivo
    }

@router.get("/permissoes/perfis")
async def listar_perfis(payload=Depends(verificar_admin)):
    """Lista todos os perfis persistidos (usado para hidratar a tela Permissões)."""
    rows = await ajard_query(
        "SELECT nome, label, modulos FROM public.perfis_customizados WHERE ativo=true ORDER BY nome",
        fetch="all"
    ) or []
    return [
        {
            "nome": r["nome"],
            "label": r["label"],
            "modulos": [m.strip() for m in (r.get("modulos") or "").split(",") if m.strip()],
        }
        for r in rows
    ]

@router.post("/permissoes/perfis")
async def criar_perfil(dados: PerfilCreate, payload=Depends(verificar_admin)):
    """Cria um novo perfil, persistido no banco."""
    nome = dados.nome.strip().lower().replace(" ", "_")
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório")
    existe = await ajard_query("SELECT nome FROM public.perfis_customizados WHERE nome=%s", (nome,), fetch="one")
    if existe:
        raise HTTPException(status_code=409, detail="Perfil já existe")
    await ajard_query(
        "INSERT INTO public.perfis_customizados (nome, label, modulos) VALUES (%s, %s, %s)",
        (nome, dados.label, ",".join(dados.modulos)), fetch="none"
    )
    return {"ok": True, "nome": nome}

@router.put("/permissoes/perfis/{nome}")
async def atualizar_perfil(nome: str, dados: PerfilUpdate, payload=Depends(verificar_admin)):
    """Atualiza os módulos padrão (e opcionalmente o label) de um perfil existente."""
    modulos_str = ",".join(dados.modulos)
    if dados.label:
        await ajard_query(
            "UPDATE public.perfis_customizados SET modulos=%s, label=%s, atualizado_em=NOW() WHERE nome=%s",
            (modulos_str, dados.label, nome), fetch="none"
        )
    else:
        await ajard_query(
            "UPDATE public.perfis_customizados SET modulos=%s, atualizado_em=NOW() WHERE nome=%s",
            (modulos_str, nome), fetch="none"
        )
    return {"ok": True}

@router.delete("/permissoes/perfis/{nome}")
async def excluir_perfil_db(nome: str, payload=Depends(verificar_admin)):
    """Remove um perfil — bloqueado se houver colaboradores ativos usando ele."""
    if nome == "admin":
        raise HTTPException(status_code=400, detail="Perfil Admin não pode ser removido")
    count = await ajard_query(
        "SELECT COUNT(*) as c FROM public.usuarios_garra WHERE perfil=%s AND ativo=true",
        (nome,), fetch="one"
    )
    if count and count.get("c", 0) > 0:
        raise HTTPException(status_code=400, detail=f"Impossível — {count['c']} colaborador(es) com este perfil")
    await ajard_query("DELETE FROM public.perfis_customizados WHERE nome=%s", (nome,), fetch="none")
    return {"ok": True}

@router.get("/permissoes/modulos")
async def listar_modulos(_auth=Depends(verificar_admin)):
    """Lista módulos disponíveis."""
    return MODULOS_DISPONIVEIS

@router.get("/permissoes/usuario/{usuario_id}")
async def get_permissoes_usuario(usuario_id: str, payload=Depends(verificar_token)):
    """Retorna permissões. Admin vê qualquer usuário; usuário vê só as próprias."""
    sub = payload.get("sub","")
    perfil = payload.get("perfil","")
    # Buscar UUID do usuário logado se sub for login
    if perfil != "admin":
        user = await ajard_query(
            "SELECT id FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
            (sub, sub), fetch="one"
        )
        uid_logado = str(user["id"]) if user else None
        if uid_logado != usuario_id:
            raise HTTPException(status_code=403, detail="Acesso negado")
    rows = await ajard_query(
        "SELECT modulo, permitido FROM public.permissoes_colaborador WHERE usuario_id=%s",
        (usuario_id,)
    )
    perms = {r["modulo"]: r["permitido"] for r in (rows or [])}
    # Se não tem registro, usa padrão do perfil
    user = await ajard_query(
        "SELECT perfil FROM public.usuarios_garra WHERE id=%s AND ativo=true",
        (usuario_id,), fetch="one"
    )
    if user:
        padrao = await perfil_modulos_padrao(user["perfil"])
        for m in MODULOS_DISPONIVEIS:
            if m["id"] not in perms:
                perms[m["id"]] = m["id"] in padrao
        if user["perfil"] == "admin":
            perms = {m["id"]: True for m in MODULOS_DISPONIVEIS}
    return perms

@router.post("/permissoes/usuario/{usuario_id}")
async def salvar_permissoes_usuario(usuario_id: str, request: Request, _auth=Depends(verificar_admin)):
    """Salva permissões de um colaborador. Body: {modulo: bool, ...}"""
    d = await request.json()
    for modulo, permitido in d.items():
        await ajard_query(
            """INSERT INTO public.permissoes_colaborador (usuario_id, modulo, permitido)
               VALUES (%s, %s, %s)
               ON CONFLICT (usuario_id, modulo)
               DO UPDATE SET permitido=%s, atualizado_em=now()""",
            (usuario_id, modulo, bool(permitido), bool(permitido)), fetch="none"
        )
    return {"ok": True}

@router.get("/permissoes/todos")
async def get_todas_permissoes(_auth=Depends(verificar_admin)):
    """Retorna permissões de todos os usuários ativos para a tela de gestão."""
    usuarios = await ajard_query(
        "SELECT id, login, nome, perfil FROM public.usuarios_garra WHERE ativo=true ORDER BY perfil, nome"
    )
    perms = await ajard_query(
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
        padrao = await perfil_modulos_padrao(u["perfil"])
        user_perms = {}
        for m in MODULOS_DISPONIVEIS:
            if uid in perm_map and m["id"] in perm_map[uid]:
                user_perms[m["id"]] = perm_map[uid][m["id"]]
            else:
                user_perms[m["id"]] = m["id"] in padrao
        if u["perfil"] == "admin":
            user_perms = {m["id"]: True for m in MODULOS_DISPONIVEIS}
        result.append({
            "id": uid,
            "login": u["login"],
            "nome": u["nome"],
            "perfil": u["perfil"],
            "permissoes": user_perms,
        })
    return result
