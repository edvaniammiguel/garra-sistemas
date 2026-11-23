"""core.acesso_politica — (21/11/2026) Controle de acesso por aparelho e horário (versão simples, aprovada em 21/11).

Um login só por pessoa. Três coisas, e nada mais:
  1. Aparelhos da empresa → cookie HttpOnly `garra_disp` criado no login; o JWT carrega `disp`. Cada aparelho é
     nao_classificado | corporativo | pessoal (só o admin muda; vale para todos os logins naquele aparelho).
  2. Horário de acesso    → UM para a empresa (acesso_config 'horario': dias + início/fim; padrão seg–sáb 06:30–18:00).
  3. Na pessoa (acesso_pessoa) → duas chaves e uma liberação:
       so_empresa   : trabalho só em aparelho da empresa; em outro aparelho, só o pessoal.
       so_horario   : trabalho só dentro do horário de acesso; fora dele, só o pessoal.
       liberado_de / liberado_ate : "Liberar fora do expediente" — nesse período (dias inteiros, inclusive)
                      o horário não vale.
     "O pessoal" = Mural, avisos e os módulos de MODULOS_PESSOAIS (dado da própria pessoa).

Quem não tem chave ligada funciona como sempre. Perfil admin (Master) nunca é bloqueado. Aparelho REVOGADO
perde o acesso aos módulos sempre. A decisão é do servidor, em core.permissions.tem_modulo.

Sem configuração, por baixo:
  • Tolerância fixa de 30 min depois do fim: quem usou o sistema na última hora ainda ENVIA (POST/PUT/PATCH/DELETE)
    o que já tinha aberto; abrir tela nova (GET) não. Registro feito sem sinal chega com X-Garra-Criado-Em e vale
    pela hora em que foi criado.
  • 403 negado por esta regra sai com {"modo_pessoal": true} (middleware) — o app mostra a faixa, não desloga.
  • Avisos: fora do horário não vai push (fica na lista); quem é "só aparelho da empresa" recebe push de trabalho
    só nos aparelhos corporativos.
  • Aparelho novo: quando alguém que já tinha aparelho conhecido entra num aparelho que o sistema nunca viu, os
    Administradores recebem o aviso "aparelho_novo" (não bloqueia; o primeiro aparelho de cada pessoa é silencioso).
"""
import base64
import contextvars
import hashlib
import hmac
import json
import secrets
import time as _time
from datetime import datetime, date, time as dtime, timedelta

from .config import JWT_SECRET
from .db import ajard_query
from .fuso import agora, FUSO

COOKIE_DISP = "garra_disp"
COOKIE_MAX_AGE = 400 * 24 * 3600          # teto do Chrome para cookie persistente

HORARIO_PADRAO = {"dias": "1,2,3,4,5,6", "inicio": "06:30", "fim": "18:00"}   # seg–sáb (21/11/2026)
MODULOS_PESSOAIS = {"operacional_historico"}                                   # abre também no Modo Pessoal
TOLERANCIA_MIN = 30

ACESSO_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS public.acesso_config (
    chave TEXT PRIMARY KEY,
    valor JSONB NOT NULL,
    atualizado_em TIMESTAMPTZ DEFAULT now(),
    atualizado_por TEXT
);
CREATE TABLE IF NOT EXISTS public.acesso_dispositivos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id UUID NOT NULL REFERENCES public.usuarios_garra(id),
    chave_hash TEXT NOT NULL,
    classificacao TEXT NOT NULL DEFAULT 'nao_classificado'
        CHECK (classificacao IN ('nao_classificado','corporativo','pessoal')),
    apelido TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    credential_id TEXT,
    criado_em TIMESTAMPTZ DEFAULT now(),
    ultimo_uso TIMESTAMPTZ DEFAULT now(),
    ativo BOOLEAN DEFAULT true,
    revogado_em TIMESTAMPTZ,
    revogado_por TEXT,
    UNIQUE (usuario_id, chave_hash)
);
ALTER TABLE public.acesso_dispositivos ADD COLUMN IF NOT EXISTS substituido_por UUID;
CREATE TABLE IF NOT EXISTS public.acesso_pessoa (
    usuario_id UUID PRIMARY KEY REFERENCES public.usuarios_garra(id),
    so_empresa BOOLEAN NOT NULL DEFAULT false,
    so_horario BOOLEAN NOT NULL DEFAULT false,
    liberado_de DATE,
    liberado_ate DATE,
    atualizado_em TIMESTAMPTZ DEFAULT now(),
    atualizado_por TEXT
);
ALTER TABLE public.acesso_pessoa ADD COLUMN IF NOT EXISTS liberado_de DATE;
CREATE TABLE IF NOT EXISTS public.acesso_log (
    id BIGSERIAL PRIMARY KEY,
    em TIMESTAMPTZ DEFAULT now(),
    usuario_id UUID,
    login TEXT,
    dispositivo_id UUID,
    modulo TEXT,
    decisao TEXT,
    motivo TEXT,
    modo TEXT
);
CREATE INDEX IF NOT EXISTS acesso_log_em ON public.acesso_log (em DESC);
CREATE TABLE IF NOT EXISTS public.acesso_atividade (
    usuario_id UUID PRIMARY KEY,
    ultimo_trabalho TIMESTAMPTZ NOT NULL
);
"""

_TABELAS_OK = False


async def garantir_tabelas_acesso():
    global _TABELAS_OK
    if _TABELAS_OK:
        return
    # Um comando por vez, com espera curta de trava. Tenta UMA vez por processo (login nunca espera por isso).
    _TABELAS_OK = True
    falhas = []
    for cmd in [c.strip() for c in ACESSO_TABLES_SQL.split(";") if c.strip()]:
        try:
            # SET LOCAL + comando numa só chamada = uma transação implícita: se falhar, desfaz sozinha
            await ajard_query("SET LOCAL lock_timeout = '5s'; " + cmd, fetch="none")
        except Exception as e:
            falhas.append(f"{cmd.splitlines()[0][:70]} → {type(e).__name__}")
    try:
        await ajard_query("""INSERT INTO public.acesso_config (chave, valor) VALUES ('horario', %s::jsonb)
                             ON CONFLICT (chave) DO NOTHING""", (json.dumps(HORARIO_PADRAO),), fetch="none")
        await ajard_query("DELETE FROM public.acesso_log WHERE em < now() - interval '90 days'", fetch="none")   # registro: 90 dias
    except Exception as e:
        falhas.append(f"sementes → {type(e).__name__}")
    if falhas:
        raise RuntimeError(" | ".join(falhas))


# ── Cache curto (tem_modulo é chamado muitas vezes por tela) ──────────────
_CACHE_TTL = 30
_cache_cfg = {"em": 0.0, "horario": dict(HORARIO_PADRAO)}
_cache_usr: dict = {}      # login → (em, dados)
_cache_disp: dict = {}     # disp_id → (em, row)
_log_recente: dict = {}    # (login, modulo, motivo) → em   (evita encher o registro)


def limpar_cache(login: str = None):
    _cache_cfg["em"] = 0.0
    _cache_disp.clear()
    if login is None:
        _cache_usr.clear()
    else:
        _cache_usr.pop(login, None)


async def ler_horario() -> dict:
    """Horário de acesso da empresa (um só)."""
    if _time.time() - _cache_cfg["em"] < _CACHE_TTL:
        return _cache_cfg["horario"]
    try:
        await garantir_tabelas_acesso()
        r = await ajard_query("SELECT valor FROM public.acesso_config WHERE chave='horario'", fetch="one")
        v = r["valor"] if r else None
        if isinstance(v, str):          # asyncpg devolve JSONB como texto
            v = json.loads(v)
        if isinstance(v, dict) and v.get("inicio") and v.get("fim"):
            _cache_cfg["horario"] = {**HORARIO_PADRAO, **v}
        _cache_cfg["em"] = _time.time()
    except Exception as e:
        print("acesso_politica: falha ao ler o horário (segue o padrão):", e)
    return _cache_cfg["horario"]


async def _dados_usuario(login: str):
    """Usuário + as chaves dele (cache 30 s)."""
    c = _cache_usr.get(login)
    if c and _time.time() - c[0] < _CACHE_TTL:
        return c[1]
    u = await ajard_query("SELECT id, perfil FROM public.usuarios_garra WHERE (login=%s OR email=%s) AND ativo=true",
                          (login, login), fetch="one")
    dados = {"usuario": u, "pessoa": None}
    if u:
        p = await ajard_query("SELECT so_empresa, so_horario, liberado_de, liberado_ate FROM public.acesso_pessoa WHERE usuario_id=%s",
                              (u["id"],), fetch="one")
        if p and (p["so_empresa"] or p["so_horario"]):
            dados["pessoa"] = p
    _cache_usr[login] = (_time.time(), dados)
    return dados


async def _dispositivo(disp_id):
    if not disp_id:
        return None
    c = _cache_disp.get(disp_id)
    if c and _time.time() - c[0] < _CACHE_TTL:
        return c[1]
    try:
        row = await ajard_query("SELECT id, usuario_id, classificacao, ativo, substituido_por FROM public.acesso_dispositivos WHERE id=%s",
                                (disp_id,), fetch="one")
        # (23/11/2026) registro duplicado do MESMO aparelho: vale o registro que ficou (ver conciliar_dispositivo)
        if row and row.get("substituido_por"):
            alvo = await ajard_query("SELECT id, usuario_id, classificacao, ativo, substituido_por FROM public.acesso_dispositivos WHERE id=%s",
                                     (row["substituido_por"],), fetch="one")
            row = alvo or row
    except Exception:
        try:      # coluna nova ainda não criada: segue como antes
            row = await ajard_query("SELECT id, usuario_id, classificacao, ativo FROM public.acesso_dispositivos WHERE id=%s",
                                    (disp_id,), fetch="one")
        except Exception:
            row = None
    _cache_disp[disp_id] = (_time.time(), row)
    return row


def _hm(v) -> dtime:
    if isinstance(v, dtime):
        return v
    return dtime.fromisoformat(str(v)[:5])


def _dia(v):
    return v if isinstance(v, date) or v is None else date.fromisoformat(str(v)[:10])


def dentro_do_horario(horario: dict, momento: datetime = None) -> bool:
    """Dia da semana (1=seg … 7=dom) no horário de acesso e início ≤ agora < fim."""
    momento = momento or agora()
    h = momento.time().replace(tzinfo=None)
    dias = {int(d) for d in str(horario.get("dias") or "").split(",") if d.strip().isdigit()}
    return momento.isoweekday() in dias and _hm(horario["inicio"]) <= h < _hm(horario["fim"])


def liberado(pessoa: dict, momento: datetime = None) -> bool:
    """'Liberar fora do expediente': vale o dia inteiro, de liberado_de a liberado_ate (inclusive)."""
    de, ate = _dia((pessoa or {}).get("liberado_de")), _dia((pessoa or {}).get("liberado_ate"))
    dia = (momento or agora()).date()
    return bool(ate and dia <= ate and (not de or de <= dia))


def decidir(modulo: str, pessoa: dict, disp: dict, horario: dict, momento: datetime = None):
    """Regra pura (testável sem banco). Retorna (permitido, motivo)."""
    if modulo in MODULOS_PESSOAIS:
        return True, ""
    if pessoa.get("so_empresa") and (not disp or disp.get("classificacao") != "corporativo"):
        return False, "aparelho pessoal" if disp and disp.get("classificacao") == "pessoal" else "aparelho não classificado"
    if pessoa.get("so_horario") and not liberado(pessoa, momento) and not dentro_do_horario(horario, momento):
        return False, "fora do expediente"
    return True, ""


async def _registrar(dados, login, disp_id, modulo, decisao, motivo, modo):
    chave = (login, modulo, motivo, decisao)
    agora_s = _time.time()
    if agora_s - _log_recente.get(chave, 0) < 600:      # 1 registro a cada 10 min por pessoa/módulo/motivo
        return
    _log_recente[chave] = agora_s
    try:
        if not dados:
            c = _cache_usr.get(login)
            dados = c[1] if c else None
        uid = dados["usuario"]["id"] if dados and dados.get("usuario") else None
        await ajard_query("""INSERT INTO public.acesso_log (usuario_id, login, dispositivo_id, modulo, decisao, motivo, modo)
                             VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                          (uid, login, disp_id, modulo, decisao, motivo, modo), fetch="none")
    except Exception as e:
        print("acesso_politica: falha ao registrar:", e)


# ── Contexto da requisição (middleware) ──────────────────────────────────
_req = contextvars.ContextVar("garra_acesso_req", default=None)
_METODOS_ESCRITA = {"POST", "PUT", "PATCH", "DELETE"}


class MiddlewarePoliticaAcesso:
    """ASGI puro: guarda método e X-Garra-Criado-Em para a regra; quando a política negou, o 403 da rota
    sai com {"detail", "modo_pessoal": true, "motivo"} — o app mostra o aviso em vez de deslogar."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        hdrs = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        holder = {"method": scope.get("method", "GET"), "criado_em": hdrs.get("x-garra-criado-em"), "negado": None}
        _req.set(holder)
        estado = {"start": None, "body": b""}

        async def _send(msg):
            if msg["type"] == "http.response.start":
                if msg.get("status") == 403 and holder["negado"]:
                    estado["start"] = msg
                    return
                return await send(msg)
            if msg["type"] == "http.response.body" and estado["start"] is not None:
                estado["body"] += msg.get("body", b"")
                if msg.get("more_body"):
                    return
                st = estado["start"]
                corpo_orig = estado["body"]
                if any(k.lower() == b"content-encoding" and v.lower() == b"gzip" for k, v in st.get("headers", [])):
                    try:
                        import gzip
                        corpo_orig = gzip.decompress(corpo_orig)
                    except Exception:
                        corpo_orig = b"{}"
                try:
                    detalhe = json.loads(corpo_orig or b"{}").get("detail")
                except Exception:
                    detalhe = None
                motivo = holder["negado"]
                corpo = json.dumps({"detail": _texto_negado(motivo, detalhe), "modo_pessoal": True,
                                    "motivo": motivo}, ensure_ascii=False).encode()
                headers = [(k, v) for k, v in st.get("headers", [])
                           if k.lower() not in (b"content-length", b"content-type", b"content-encoding")]
                headers += [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode())]
                await send({**st, "headers": headers})
                return await send({"type": "http.response.body", "body": corpo})
            return await send(msg)

        return await self.app(scope, receive, _send)


# (21/11/2026) Textos aprovados pela Edvania: tom de descanso, não de bloqueio.
_FAIXA = {
    "fora do expediente": ("Bom descanso!", "Seu expediente terminou. Por aqui ficam o Mural e os seus avisos. O trabalho continua no próximo turno."),
    "aparelho pessoal": ("Este é seu celular pessoal.", "As telas de gestão abrem no aparelho da empresa. Por aqui: Mural, avisos e o que for do seu dia a dia."),
    "aparelho não classificado": ("Este aparelho ainda não está cadastrado.", "As telas de gestão abrem no aparelho da empresa. Peça à administração para marcar este aparelho, se ele for da empresa."),
    "aparelho revogado": ("Aparelho desligado do sistema.", "Fale com a administração."),
}


def _faixa(motivo):
    t = _FAIXA.get(motivo)
    return {"titulo": t[0], "aviso": t[1]} if t else {}


def _texto_negado(motivo, detalhe=None):
    if motivo == "fora do expediente":
        return "Bom descanso! Seu expediente terminou — esta parte volta no próximo turno."
    if motivo in ("aparelho pessoal", "aparelho não classificado"):
        return "Este aparelho não é da empresa. As telas de gestão abrem no aparelho da empresa."
    if motivo in _FAIXA:
        return " ".join(_FAIXA[motivo])
    return detalhe or "Acesso não liberado agora."


def _criado_em_cliente(holder):
    """Hora em que o registro foi criado no aparelho (fila offline), se for plausível: até 72 h atrás."""
    try:
        ms = int(str(holder.get("criado_em") or "").strip())
        quando = datetime.fromtimestamp(ms / 1000, FUSO)
    except (ValueError, TypeError, OverflowError, OSError):
        return None
    agora_ = agora()
    if quando > agora_ + timedelta(minutes=5) or quando < agora_ - timedelta(hours=72):
        return None
    return quando


async def _marcar_atividade(uid):
    """Última vez que a pessoa usou o sistema DENTRO da jornada (base da tolerância). Grava no máx. a cada 2 min."""
    chave = ("ativ", str(uid))
    if _time.time() - _log_recente.get(chave, 0) < 120:
        return
    _log_recente[chave] = _time.time()
    try:
        await ajard_query("""INSERT INTO public.acesso_atividade (usuario_id, ultimo_trabalho) VALUES (%s, now())
                             ON CONFLICT (usuario_id) DO UPDATE SET ultimo_trabalho=now()""", (uid,), fetch="none")
    except Exception:
        pass


async def _tolerancia(dados, modulo, disp, horario, holder):
    """Escrita fora do horário: vale se (a) o registro foi criado offline dentro do horário, ou (b) estamos até
    TOLERANCIA_MIN depois do fim de hoje e a pessoa trabalhou na última hora antes do fim."""
    if not holder or holder.get("method") not in _METODOS_ESCRITA:
        return False
    pessoa = dados["pessoa"]
    criado = _criado_em_cliente(holder)
    if criado and (dentro_do_horario(horario, criado) or liberado(pessoa, criado)):
        return True
    agora_ = agora()
    dias = {int(d) for d in str(horario.get("dias") or "").split(",") if d.strip().isdigit()}
    if agora_.isoweekday() not in dias:
        return False
    fim = datetime.combine(agora_.date(), _hm(horario["fim"]), FUSO)
    if not fim <= agora_ <= fim + timedelta(minutes=TOLERANCIA_MIN):
        return False
    try:
        a = await ajard_query("SELECT ultimo_trabalho FROM public.acesso_atividade WHERE usuario_id=%s",
                              (dados["usuario"]["id"],), fetch="one")
    except Exception:
        a = None
    return bool(a and a["ultimo_trabalho"] >= fim - timedelta(minutes=60))


async def avaliar(login: str, disp_id, modulo: str, holder=None):
    """Núcleo da decisão. Retorna (permitido, motivo, controlado). Sem efeitos além da marca de atividade."""
    disp = await _dispositivo(disp_id)
    if disp and not disp.get("ativo"):
        return False, "aparelho revogado", True
    dados = await _dados_usuario(login)
    u, pessoa = dados["usuario"], dados["pessoa"]
    if not u or (u.get("perfil") or "").lower() == "admin" or not pessoa:
        return True, "", False
    if disp and str(disp.get("usuario_id")) != str(u["id"]):
        disp = None                                   # cookie de outra pessoa no mesmo aparelho: não vale
    horario = await ler_horario()
    ok, motivo = decidir(modulo, pessoa, disp, horario)
    if ok:
        if pessoa.get("so_horario") and modulo not in MODULOS_PESSOAIS and dentro_do_horario(horario):
            await _marcar_atividade(u["id"])
        return True, "", True
    if motivo == "fora do expediente" and await _tolerancia(dados, modulo, disp, horario, holder):
        return True, "", True
    return False, motivo, True


async def contexto_permite(payload: dict, modulo: str) -> bool:
    """Chamado por tem_modulo DEPOIS que a permissão já liberou o módulo.
    Nunca derruba o sistema: erro aqui = segue como sempre (permitido)."""
    try:
        login = payload.get("login") or payload.get("sub") or ""
        disp_id = payload.get("disp")
        holder = _req.get()
        ok, motivo, _ = await avaliar(login, disp_id, modulo, holder)
        if ok:
            return True
        await _registrar(None, login, disp_id, modulo, "negado", motivo, "ativa")
        if holder is not None:
            holder["negado"] = motivo
        return False
    except Exception as e:
        print("acesso_politica: falha na avaliação (liberado como sempre):", e)
        return True


async def exigir_contexto(payload: dict, modulo: str):
    """Para rotas do app que só exigem login (ex.: lançar horas na OS): aplica só esta regra, sem mudar permissão."""
    from fastapi import HTTPException
    if not await contexto_permite(payload, modulo):
        raise HTTPException(status_code=403, detail=_texto_negado((_req.get() or {}).get("negado")))


async def modo_acesso(login: str, disp_id) -> dict:
    """Resumo para o app: Modo Trabalho ou Pessoal agora, com a faixa a mostrar."""
    try:
        disp = await _dispositivo(disp_id)
        if disp and not disp.get("ativo"):
            return {"modo": "pessoal", "motivo": "aparelho revogado", **_faixa("aparelho revogado")}
        dados = await _dados_usuario(login)
        u, pessoa = dados["usuario"], dados["pessoa"]
        if not u or (u.get("perfil") or "").lower() == "admin" or not pessoa:
            return {"modo": "trabalho"}
        if disp and str(disp.get("usuario_id")) != str(u["id"]):
            disp = None
        ok, motivo = decidir("", pessoa, disp, await ler_horario())
        if ok:
            return {"modo": "trabalho"}
        return {"modo": "pessoal", "motivo": motivo, **_faixa(motivo)}
    except Exception as e:
        print("acesso_politica: falha ao calcular o modo:", e)
        return {"modo": "trabalho"}


async def filtrar_perms(perms: dict, login: str, disp_id) -> dict:
    """permsDB do login/renovar sem o que esta regra não libera AGORA."""
    try:
        if not perms:
            return perms
        out = dict(perms)
        for m, v in perms.items():
            if v:
                ok, _, controlado = await avaliar(login, disp_id, m)
                if not controlado:
                    return perms
                out[m] = ok
        return out
    except Exception:
        return perms


async def estado_aviso(usuario: dict) -> str:
    """Para core.notificar: 'fora' = fora do horário de acesso (aviso fica na lista, sem push); senão 'normal'."""
    try:
        if (usuario.get("perfil") or "").lower() == "admin":
            return "normal"
        pessoa = (await _dados_usuario(usuario.get("login") or ""))["pessoa"]
        if pessoa and pessoa.get("so_horario") and not liberado(pessoa) and not dentro_do_horario(await ler_horario()):
            return "fora"
        return "normal"
    except Exception:
        return "normal"


async def aviso_so_corporativo(usuario: dict, modulo) -> bool:
    """Push de aviso de trabalho para quem é "só aparelho da empresa" vai só para os aparelhos corporativos dela
    (o título já mostra informação de trabalho). Avisos gerais (módulo "*", ex.: Mural) vão para todos."""
    try:
        if modulo in (None, "*") or (usuario.get("perfil") or "").lower() == "admin":
            return False
        pessoa = (await _dados_usuario(usuario.get("login") or ""))["pessoa"]
        return bool(pessoa and pessoa.get("so_empresa"))
    except Exception:
        return False


# ── Aparelho: cookie + registro ───────────────────────────────────────────
def _hash(chave: str) -> str:
    return hashlib.sha256(chave.encode()).hexdigest()


def _resumo_ua(ua: str) -> str:
    so = next((n for k, n in (("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
                              ("Mac OS", "Mac"), ("Linux", "Linux")) if k in (ua or "")), "Aparelho")
    nav = next((n for k, n in (("Edg/", "Edge"), ("Chrome/", "Chrome"), ("Firefox/", "Firefox"), ("Safari/", "Safari")) if k in (ua or "")), "")
    return so + (" · " + nav if nav else "")


async def _avisar_aparelho_novo(usuario_id, ua):
    """Aviso aos Administradores: a pessoa entrou num aparelho que o sistema ainda não conhecia. Não bloqueia nada."""
    try:
        from core.notificar import notificar
        u = await ajard_query("SELECT nome, login FROM public.usuarios_garra WHERE id=%s", (usuario_id,), fetch="one")
        admins = await ajard_query("SELECT id FROM public.usuarios_garra WHERE ativo=true AND lower(perfil)='admin'") or []
        if not u or not admins:
            return
        await notificar("aparelho_novo", f"Aparelho novo: {u.get('nome') or u.get('login')}",
                        f"Entrou num aparelho que o sistema ainda não conhecia ({_resumo_ua(ua)}). Confira em Permissões › Pessoas › Aparelhos.",
                        link="/admin", apenas=[a["id"] for a in admins])
    except Exception as e:
        print("acesso_politica: falha ao avisar aparelho novo:", e)


async def tocar_dispositivo(disp_id):
    """Abriu o app já logado (renovar): atualiza o 'último uso' do aparelho — a mesma linha, só a hora."""
    if not disp_id:
        return
    try:
        await ajard_query("UPDATE public.acesso_dispositivos SET ultimo_uso=now() WHERE id=%s", (disp_id,), fetch="none")
    except Exception:
        pass


def _ip_do_pedido(request) -> str:
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "")


async def _chave_primeiro_contato(request, usuario_id) -> str:
    """(23/11/2026) Aparelho ainda sem cookie. Antes: cada pedido sorteava uma chave. Quando o app abria várias telas
    ao mesmo tempo (o shell do celular + os módulos dentro dele, ou o Admin + um módulo), cada uma chegava sem cookie e
    ganhava uma chave diferente — o MESMO aparelho virava 2, 3, 4 registros, e o que a administração nomeava e
    classificava "voltava sem nome". Agora a chave do primeiro contato é a mesma para os pedidos da mesma pessoa, no
    mesmo navegador (user-agent) e na mesma rede (IP), dentro de uma janela de 2 minutos: todos caem no mesmo registro.
    Só quem tem o segredo do servidor consegue calcular a chave. Depois do primeiro contato vale o cookie, como sempre."""
    ua = (request.headers.get("user-agent") or "")[:300]
    base = f"{usuario_id}|{ua}|{_ip_do_pedido(request)}"
    balde = int(_time.time() // 120)

    def chave_de(b):
        d = hmac.new(JWT_SECRET.encode(), f"disp1|{base}|{b}".encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(d).decode().rstrip("=")      # 43 caracteres

    try:      # pedido que cai logo depois da virada da janela: reaproveita a chave da janela anterior, se foi usada agora
        ant = chave_de(balde - 1)
        usado = await ajard_query(
            "SELECT 1 AS x FROM public.acesso_dispositivos WHERE usuario_id=%s AND chave_hash=%s AND criado_em > now() - interval '2 minutes'",
            (usuario_id, _hash(ant)), fetch="one")
        if usado:
            return ant
    except Exception:
        pass
    return chave_de(balde)


async def conciliar_dispositivo(request, response, disp_id):
    """(23/11/2026) Conserta sozinho os registros duplicados que já existem. O token de cada módulo guarda o registro
    do aparelho em que nasceu; o cookie é um só por navegador. Se o token aponta para um registro e o cookie para outro,
    os dois são o MESMO aparelho: fica o do cookie. O que a administração já definiu (nome, da empresa/pessoal) é levado
    para o registro que fica; aparelho revogado continua revogado. O registro antigo não é apagado — só passa a apontar
    para o que ficou (substituido_por) e sai da lista. Devolve o registro que vale para este navegador."""
    if not disp_id:
        return disp_id
    try:
        tok = await ajard_query("""SELECT id, usuario_id, chave_hash, classificacao, apelido, ativo, substituido_por, user_agent
                                     FROM public.acesso_dispositivos WHERE id=%s""", (disp_id,), fetch="one")
        if not tok:
            return disp_id
        if tok.get("substituido_por"):                  # já conciliado antes: o token passa para o registro que ficou
            return str(tok["substituido_por"])
        chave = request.cookies.get(COOKIE_DISP)
        if not chave or len(chave) < 32:
            return disp_id
        h = _hash(chave)
        if tok["chave_hash"] == h:
            return disp_id
        # Segurança: só concilia se o navegador de agora é do mesmo tipo do registro do token (ex.: Android · Chrome).
        # Um token levado de um notebook para um celular NÃO junta os dois nem passa "da empresa" para o celular.
        if _resumo_ua(tok.get("user_agent") or "") != _resumo_ua(request.headers.get("user-agent") or ""):
            return disp_id
        fica_id = await vincular_dispositivo(request, response, tok["usuario_id"])
        if not fica_id or str(fica_id) == str(disp_id):
            return disp_id
        fica = await ajard_query("SELECT classificacao, apelido, ativo FROM public.acesso_dispositivos WHERE id=%s", (fica_id,), fetch="one") or {}
        sets, vals = [], []
        if (fica.get("classificacao") or "nao_classificado") == "nao_classificado" and tok["classificacao"] != "nao_classificado":
            sets.append("classificacao=%s"); vals.append(tok["classificacao"])
        if not (fica.get("apelido") or "").strip() and (tok.get("apelido") or "").strip():
            sets.append("apelido=%s"); vals.append(tok["apelido"])
        if tok.get("ativo") is False and fica.get("ativo") is not False:
            sets.append("ativo=false"); sets.append("revogado_em=now()"); sets.append("revogado_por='duplicado de aparelho revogado'")
        if sets:
            await ajard_query(f"UPDATE public.acesso_dispositivos SET {', '.join(sets)} WHERE chave_hash=%s AND substituido_por IS NULL",
                              tuple(vals) + (h,), fetch="none")
        # todos os logins que usaram a chave antiga neste aparelho apontam para o registro equivalente na chave que ficou
        await ajard_query(
            """UPDATE public.acesso_dispositivos a SET substituido_por = COALESCE(
                   (SELECT n.id FROM public.acesso_dispositivos n WHERE n.chave_hash=%s AND n.usuario_id=a.usuario_id AND n.substituido_por IS NULL LIMIT 1), %s::uuid)
               WHERE a.chave_hash=%s AND a.substituido_por IS NULL""", (h, fica_id, tok["chave_hash"]), fetch="none")
        try:      # avisos (push) assinados no registro antigo passam para o que ficou
            await ajard_query("UPDATE public.push_assinaturas SET dispositivo_id=%s WHERE dispositivo_id=%s", (fica_id, disp_id), fetch="none")
        except Exception:
            pass
        _cache_disp.clear()
        return str(fica_id)
    except Exception as e:
        print("acesso_politica: falha ao conciliar aparelho (segue normal):", e)
        return disp_id


async def vincular_dispositivo(request, response, usuario_id, credential_id: str = None):
    """Lê (ou cria) o cookie do aparelho, registra o aparelho para a pessoa e devolve o id.
    (21/11/2026) Aparelho compartilhado (ex.: celular do administrativo): a classificação, o nome e a revogação
    são do APARELHO — quem entra nele pela primeira vez herda o que já foi definido. Aparelho novo: 'nao_classificado'."""
    try:
        await garantir_tabelas_acesso()
        chave = request.cookies.get(COOKIE_DISP)
        if not chave or len(chave) < 32:
            chave = await _chave_primeiro_contato(request, usuario_id)
        seguro = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(COOKIE_DISP, chave, max_age=COOKIE_MAX_AGE, httponly=True,
                            secure=seguro, samesite="lax", path="/")
        ua = (request.headers.get("user-agent") or "")[:300]
        h = _hash(chave)
        ja_tinha = await ajard_query("SELECT count(*) AS n FROM public.acesso_dispositivos WHERE usuario_id=%s AND chave_hash<>%s",
                                     (usuario_id, h), fetch="one")
        herda = await ajard_query(
            """SELECT classificacao, apelido, ativo FROM public.acesso_dispositivos WHERE chave_hash=%s AND substituido_por IS NULL
                ORDER BY (classificacao <> 'nao_classificado') DESC, ultimo_uso DESC LIMIT 1""", (h,), fetch="one")
        row = await ajard_query(
            """INSERT INTO public.acesso_dispositivos (usuario_id, chave_hash, classificacao, apelido, ativo, user_agent, credential_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (usuario_id, chave_hash) DO UPDATE
                  SET ultimo_uso=now(), user_agent=EXCLUDED.user_agent,
                      credential_id=COALESCE(EXCLUDED.credential_id, public.acesso_dispositivos.credential_id)
               RETURNING id, (xmax = 0) AS novo""",
            (usuario_id, h, herda["classificacao"] if herda else "nao_classificado", herda["apelido"] if herda else "",
             herda["ativo"] if herda else True, ua, credential_id), fetch="one")
        if row and row.get("novo") and not herda and ja_tinha and ja_tinha["n"] > 0:
            await _avisar_aparelho_novo(usuario_id, ua)
        return str(row["id"]) if row else None
    except Exception as e:
        print("acesso_politica: falha ao vincular aparelho (login segue normal):", e)
        return None
