"""(24/10/2026) Notificações — API usada pelo app (badge, lista em Minha conta, assinatura de push,
preferências) e pelo sino dos módulos desktop. Regras e envio em core/notificar.py."""
from fastapi import APIRouter, Depends, HTTPException, Request
from core.auth import verificar_token
from core.db import ajard_query
from core import notificar as N

router = APIRouter()


async def _uid(payload):
    u = await ajard_query("SELECT id, perfil FROM public.usuarios_garra WHERE login=%s AND ativo=true",
                          (payload.get("sub") or payload.get("login") or "",), fetch="one")
    if not u:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    return u


@router.get("/api/notificacoes")
async def listar(limite: int = 30, payload=Depends(verificar_token)):
    u = await _uid(payload)
    await N._garantir()
    rows = await ajard_query(
        """SELECT id, tipo, titulo, texto, link, lida, criado_em FROM public.notificacoes
           WHERE usuario_id=%s ORDER BY lida, criado_em DESC LIMIT %s""", (u["id"], max(1, min(int(limite or 30), 200)))) or []
    return {"pendentes": await N.pendentes(u["id"]),
            "itens": [dict(r, id=str(r["id"]), criado_em=r["criado_em"].isoformat() if r["criado_em"] else None) for r in rows]}


@router.post("/api/notificacoes/lidas")
async def marcar_lidas(request: Request, payload=Depends(verificar_token)):
    """{ids:[…]} marca essas; sem ids marca todas as do usuário."""
    u = await _uid(payload)
    await N._garantir()
    d = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    ids = [str(i) for i in (d.get("ids") or []) if i]
    if ids:
        await ajard_query("UPDATE public.notificacoes SET lida=true WHERE usuario_id=%s AND id = ANY(%s::uuid[])", (u["id"], ids), fetch="none")
    else:
        await ajard_query("UPDATE public.notificacoes SET lida=true WHERE usuario_id=%s AND lida=false", (u["id"],), fetch="none")
    return {"ok": True, "pendentes": await N.pendentes(u["id"])}


@router.get("/api/notificacoes/prefs")
async def prefs(payload=Depends(verificar_token)):
    u = await _uid(payload)
    efet = await N.prefs_de(u["id"], u.get("perfil"))
    # só os tipos cujo módulo o usuário tem — o resto não aparece
    out = []
    for tipo, ev in N.EVENTOS.items():
        dest = await N.destinatarios(tipo, apenas=[u["id"]])
        if dest:
            out.append({"tipo": tipo, "nome": ev["nome"], "ativo": efet[tipo]})
    return out


@router.put("/api/notificacoes/prefs")
async def prefs_salvar(request: Request, payload=Depends(verificar_token)):
    u = await _uid(payload)
    await N._garantir()
    d = await request.json()
    for tipo, ativo in (d or {}).items():
        if tipo in N.EVENTOS:
            await ajard_query(
                """INSERT INTO public.notificacao_prefs (usuario_id, tipo, ativo) VALUES (%s,%s,%s)
                   ON CONFLICT (usuario_id, tipo) DO UPDATE SET ativo=EXCLUDED.ativo""", (u["id"], tipo, bool(ativo)), fetch="none")
    return {"ok": True}


@router.get("/api/notificacoes/vapid")
async def vapid_publica(_auth=Depends(verificar_token)):
    v = await N.vapid()
    return {"publica": (v or {}).get("publica"), "disponivel": bool(v)}


@router.post("/api/notificacoes/assinar")
async def assinar(request: Request, payload=Depends(verificar_token)):
    """Assinatura Web Push deste aparelho/navegador (uma por endpoint; troca de usuário no mesmo aparelho
    reaponta a assinatura)."""
    u = await _uid(payload)
    await N._garantir()
    d = await request.json()
    ep = (d.get("endpoint") or "").strip()
    keys = d.get("keys") or {}
    if not ep or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(status_code=400, detail="Assinatura incompleta")
    await ajard_query(
        """INSERT INTO public.push_assinaturas (usuario_id, endpoint, p256dh, auth, aparelho)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (endpoint) DO UPDATE SET usuario_id=EXCLUDED.usuario_id, p256dh=EXCLUDED.p256dh,
                auth=EXCLUDED.auth, aparelho=EXCLUDED.aparelho, ativo=true""",
        (u["id"], ep, keys["p256dh"], keys["auth"], (d.get("aparelho") or "")[:200] or None), fetch="none")
    return {"ok": True}


@router.delete("/api/notificacoes/assinar")
async def desassinar(request: Request, payload=Depends(verificar_token)):
    u = await _uid(payload)
    await N._garantir()
    d = await request.json()
    await ajard_query("UPDATE public.push_assinaturas SET ativo=false WHERE endpoint=%s AND usuario_id=%s",
                      ((d.get("endpoint") or ""), u["id"]), fetch="none")
    return {"ok": True}


@router.get("/api/notificacoes/assinaturas")
async def assinaturas(payload=Depends(verificar_token)):
    u = await _uid(payload)
    await N._garantir()
    rows = await ajard_query("SELECT endpoint, aparelho, criado_em, usado_em FROM public.push_assinaturas WHERE usuario_id=%s AND ativo=true ORDER BY criado_em", (u["id"],)) or []
    return [dict(r, criado_em=r["criado_em"].isoformat() if r["criado_em"] else None, usado_em=r["usado_em"].isoformat() if r["usado_em"] else None) for r in rows]


@router.post("/api/notificacoes/teste")
async def teste(payload=Depends(verificar_token)):
    """Manda uma notificação de teste para o próprio usuário (confere push + badge no aparelho)."""
    u = await _uid(payload)
    await N._garantir()
    await ajard_query("INSERT INTO public.notificacoes (usuario_id, tipo, titulo, texto, link) VALUES (%s,'teste','🔔 Teste de notificação','Se você está lendo isto no celular, está tudo certo.','/mobile')", (u["id"],), fetch="none")
    await N._push(u["id"], {"titulo": "🔔 Teste de notificação", "texto": "Se você está lendo isto no celular, está tudo certo.", "link": "/mobile", "tipo": "teste", "pendentes": await N.pendentes(u["id"])})
    return {"ok": True}
