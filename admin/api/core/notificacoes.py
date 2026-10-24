"""(24/10/2026) Notificações do Garra Sistemas — núcleo.

Uma notificação é um registro em public.notificacoes (quem, tipo, título, texto, link). A partir dele:
  • o app (celular) mostra a lista em Minha conta e o número no ícone (badge);
  • o desktop mostra o sino no cabeçalho;
  • se o usuário ativou "receber no celular/computador", vai também um Web Push (pywebpush + VAPID).

Quem recebe cada tipo: EVENTOS[tipo] = módulo que habilita (quem PODE receber) + perfis em que vem ligado por
padrão; a tabela public.notificacao_prefs guarda o que cada usuário ligou/desligou por cima do padrão.
As chaves VAPID nascem sozinhas na primeira vez (public.push_vapid) — nada a configurar no Render.
O push é best-effort: falhou, a notificação continua na lista; assinatura morta (404/410) é desativada.
"""
import json
import asyncio
import logging
from core.db import ajard_query
from core.permissions import perfil_modulos_padrao

log = logging.getLogger("notificacoes")

# tipo → {modulo (quem pode), padrao (perfis com o tipo ligado), nome (tela de preferências)}
EVENTOS = {
    "pedido_novo":     {"modulo": "manutencao",     "padrao": {"bruna"},                       "nome": "Pedido de OT novo (qualquer urgência)"},
    "pedido_urgente":  {"modulo": "manutencao",     "padrao": {"admin", "gestor", "bruna"},    "nome": "Pedido de OT urgente para virar OT"},
    "ot_urgente":      {"modulo": "manutencao",     "padrao": {"admin", "gestor"},             "nome": "OT urgente aberta"},
    "ot_concluida":    {"modulo": "manutencao",     "padrao": {"bruna"},                       "nome": "Mecânico marcou OT como concluída"},
    "abast_divergente": {"modulo": "manutencao",    "padrao": {"bruna"},                       "nome": "Abastecimento com leitura divergente"},
    "preventiva":      {"modulo": "manutencao",     "padrao": {"bruna"},                       "nome": "Preventiva vencendo (a vencer / vencida)"},
    "peca_chegou":     {"modulo": "manutencao",     "padrao": {"bruna"},                       "nome": "Peça de OT em espera foi recebida"},
    "oc_aprovar":      {"modulo": "compras_aprovar", "padrao": {"admin", "gestor", "luana"},   "nome": "Ordem de compra aguardando aprovação"},
    "ot_atribuida":    {"modulo": "ot_executar",    "padrao": {"bruna", "mecanico", "mecanica"}, "nome": "OT emitida para mim (responsável)"},
    "nota_lancar":     {"modulo": ("abastecimento_notas", "manutencao_notas"), "padrao": {"luana"}, "nome": "Nota nova para lançar (abastecimento e OT)"},
}

_OK = False


async def _garantir():
    global _OK
    if _OK:
        return
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS public.notificacoes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id UUID NOT NULL,
            tipo TEXT NOT NULL,
            titulo TEXT NOT NULL,
            texto TEXT,
            link TEXT,
            lida BOOLEAN NOT NULL DEFAULT false,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now())""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_notif_usuario ON public.notificacoes (usuario_id, lida, criado_em DESC)", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS public.push_assinaturas (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            usuario_id UUID NOT NULL,
            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            aparelho TEXT,
            ativo BOOLEAN NOT NULL DEFAULT true,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
            usado_em TIMESTAMPTZ)""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS public.notificacao_prefs (
            usuario_id UUID NOT NULL,
            tipo TEXT NOT NULL,
            ativo BOOLEAN NOT NULL DEFAULT true,
            PRIMARY KEY (usuario_id, tipo))""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS public.notificacao_marcas (
            chave TEXT PRIMARY KEY,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now())""", fetch="none")
    await _semear_prefs()
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS public.push_vapid (
            id INT PRIMARY KEY DEFAULT 1,
            chave_privada TEXT NOT NULL,
            chave_publica TEXT NOT NULL,
            criado_em TIMESTAMPTZ NOT NULL DEFAULT now())""", fetch="none")
    _OK = True


# Estado inicial por LOGIN (uma vez; depois cada um ajusta em Minha conta › Notificações). Necessário porque
# na Garra a Bruna e o Gilson são os dois "gestor" — o perfil não diz quem faz a triagem da manutenção.
_SEED_LOGIN = {
    "bruna":  {"pedido_novo": True, "pedido_urgente": True, "ot_concluida": True, "abast_divergente": True, "preventiva": True, "peca_chegou": True, "ot_urgente": False, "oc_aprovar": False},
    "gilson": {"pedido_urgente": True, "ot_urgente": True, "oc_aprovar": True, "pedido_novo": False},
    "luana":  {"nota_lancar": True, "oc_aprovar": True},
}


async def _semear_prefs():
    ja = await ajard_query("SELECT 1 FROM public.notificacao_marcas WHERE chave='seed:prefs:v1'", fetch="one")
    if ja:
        return
    for login, prefs in _SEED_LOGIN.items():
        u = await ajard_query("SELECT id FROM public.usuarios_garra WHERE login=%s AND ativo=true", (login,), fetch="one")
        if not u:
            continue
        for tipo, ativo in prefs.items():
            await ajard_query("INSERT INTO public.notificacao_prefs (usuario_id, tipo, ativo) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (u["id"], tipo, ativo), fetch="none")
    await ajard_query("INSERT INTO public.notificacao_marcas (chave) VALUES ('seed:prefs:v1') ON CONFLICT DO NOTHING", fetch="none")


# ── VAPID ──
_VAPID = None


async def vapid():
    """Par de chaves da Garra (gerado uma vez e guardado no banco). None se a lib não estiver instalada."""
    global _VAPID
    if _VAPID:
        return _VAPID
    await _garantir()
    row = await ajard_query("SELECT chave_privada, chave_publica FROM public.push_vapid WHERE id=1", fetch="one")
    if not row:
        try:
            from py_vapid import Vapid, b64urlencode
            from cryptography.hazmat.primitives import serialization
            v = Vapid()
            v.generate_keys()
            priv = b64urlencode(v.private_key.private_numbers().private_value.to_bytes(32, "big"))
            pub = b64urlencode(v.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        except Exception as e:
            log.warning("push desativado (py_vapid ausente): %s", e)
            return None
        await ajard_query("INSERT INTO public.push_vapid (id, chave_privada, chave_publica) VALUES (1,%s,%s) ON CONFLICT (id) DO NOTHING", (priv, pub), fetch="none")
        row = await ajard_query("SELECT chave_privada, chave_publica FROM public.push_vapid WHERE id=1", fetch="one")
    _VAPID = {"privada": row["chave_privada"], "publica": row["chave_publica"]}
    return _VAPID


# ── destinatários ──
async def _usuarios_ativos():
    return await ajard_query("SELECT id, login, nome, perfil FROM public.usuarios_garra WHERE ativo=true") or []


async def _tem_modulo_usuario(u, modulo, individuais):
    perfil = (u.get("perfil") or "").lower()
    if perfil == "admin":
        return True
    ind = individuais.get(str(u["id"]), {})
    padrao = set(await perfil_modulos_padrao(perfil))
    for m in (modulo if isinstance(modulo, (tuple, list, set)) else (modulo,)):
        if (bool(ind[m]) if m in ind else (m in padrao)):
            return True
    return False


async def prefs_de(usuario_id, perfil):
    """Preferências efetivas do usuário: padrão do perfil sobrescrito pelo que ele marcou."""
    await _garantir()
    rows = await ajard_query("SELECT tipo, ativo FROM public.notificacao_prefs WHERE usuario_id=%s", (usuario_id,)) or []
    marcadas = {r["tipo"]: bool(r["ativo"]) for r in rows}
    perfil = (perfil or "").lower()
    return {t: marcadas.get(t, perfil in ev["padrao"]) for t, ev in EVENTOS.items()}


async def destinatarios(tipo, apenas=None, excluir=None):
    """Usuários que recebem `tipo`: têm o módulo do evento E o tipo ligado nas preferências.
    `apenas` = lista de usuario_id para restringir (ex.: responsável da OT) — aí ignora a preferência de perfil,
    só respeita um desligamento explícito."""
    ev = EVENTOS.get(tipo)
    if not ev:
        return []
    await _garantir()
    usuarios = await _usuarios_ativos()
    if apenas is not None:
        ids = {str(x) for x in apenas if x}
        usuarios = [u for u in usuarios if str(u["id"]) in ids]
    if excluir:
        ex = {str(x) for x in excluir if x}
        usuarios = [u for u in usuarios if str(u["id"]) not in ex]
    if not usuarios:
        return []
    rows = await ajard_query("SELECT usuario_id, modulo, permitido FROM public.permissoes_colaborador") or []
    individuais = {}
    for r in rows:
        individuais.setdefault(str(r["usuario_id"]), {})[r["modulo"]] = r["permitido"]
    prefs = await ajard_query("SELECT usuario_id, ativo FROM public.notificacao_prefs WHERE tipo=%s", (tipo,)) or []
    marcadas = {str(p["usuario_id"]): bool(p["ativo"]) for p in prefs}
    out = []
    for u in usuarios:
        if not await _tem_modulo_usuario(u, ev["modulo"], individuais):
            continue
        uid = str(u["id"])
        if uid in marcadas:
            ligado = marcadas[uid]
        elif apenas is not None:
            ligado = True
        else:
            ligado = (u.get("perfil") or "").lower() in ev["padrao"]
        if ligado:
            out.append(u)
    return out


# ── envio ──
async def pendentes(usuario_id):
    await _garantir()
    r = await ajard_query("SELECT COUNT(*)::int AS n FROM public.notificacoes WHERE usuario_id=%s AND lida=false", (usuario_id,), fetch="one")
    return int((r or {}).get("n") or 0)


def _push_sync(sub, payload, priv):
    from pywebpush import webpush, WebPushException
    from py_vapid import Vapid
    try:
        webpush(subscription_info={"endpoint": sub["endpoint"], "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]}},
                data=json.dumps(payload), vapid_private_key=Vapid.from_raw(priv.encode()),
                vapid_claims={"sub": "mailto:sistemas@garraterraplenagem.com.br"}, ttl=86400, timeout=10)
        return "ok"
    except WebPushException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        return "morta" if code in (404, 410) else f"erro {code or e}"
    except Exception as e:
        return f"erro {e}"


async def _push(usuario_id, payload):
    v = await vapid()
    if not v:
        return
    subs = await ajard_query("SELECT id, endpoint, p256dh, auth FROM public.push_assinaturas WHERE usuario_id=%s AND ativo=true", (usuario_id,)) or []
    if not subs:
        return
    loop = asyncio.get_running_loop()
    for s in subs:
        try:
            res = await loop.run_in_executor(None, _push_sync, dict(s), payload, v["privada"])
        except Exception as e:
            res = f"erro {e}"
        if res == "ok":
            await ajard_query("UPDATE public.push_assinaturas SET usado_em=now() WHERE id=%s", (s["id"],), fetch="none")
        elif res == "morta":
            await ajard_query("UPDATE public.push_assinaturas SET ativo=false WHERE id=%s", (s["id"],), fetch="none")
        else:
            log.warning("push %s: %s", s["endpoint"][:40], res)


async def notificar(tipo, titulo, texto=None, link=None, apenas=None, excluir=None, chave=None):
    """Cria a notificação para cada destinatário e dispara o push. `chave` evita repetir (ex.: a mesma
    preventiva a vencer a cada recálculo). Nunca levanta exceção — quem chama não pode falhar por causa do aviso."""
    try:
        await _garantir()
        if chave:
            ja = await ajard_query("INSERT INTO public.notificacao_marcas (chave) VALUES (%s) ON CONFLICT (chave) DO NOTHING RETURNING chave", (chave,), fetch="one")
            if not ja:
                return 0
        dest = await destinatarios(tipo, apenas=apenas, excluir=excluir)
        n = 0
        for u in dest:
            await ajard_query("INSERT INTO public.notificacoes (usuario_id, tipo, titulo, texto, link) VALUES (%s,%s,%s,%s,%s)",
                              (u["id"], tipo, titulo[:200], (texto or "")[:500] or None, link), fetch="none")
            n += 1
            payload = {"titulo": titulo[:200], "texto": (texto or "")[:500], "link": link or "/mobile", "tipo": tipo,
                       "pendentes": await pendentes(u["id"])}
            asyncio.create_task(_push(u["id"], payload))
        return n
    except Exception as e:
        log.warning("notificar(%s) falhou: %s", tipo, e)
        return 0
