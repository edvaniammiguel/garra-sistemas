"""core.logs_sistema — (22/11/2026) Log do sistema inteiro: quem fez o quê, quando e em qual módulo.

Dois níveis, gravados na tabela public.log_sistema (separada das tabelas de cada módulo):
  1. AUTOMÁTICO — toda gravação (POST/PUT/PATCH/DELETE) de qualquer módulo vira uma linha:
     usuário, módulo, evento, rota e resultado. Recusas (4xx) e erros (5xx) também entram.
  2. DETALHADO — para o Operacional (OS e registros do dia): o valor de ANTES e o de DEPOIS
     de cada campo. Os outros módulos entram um por vez, acrescentando regras em _ENTIDADES.

Regras de privacidade: o corpo do pedido NUNCA é guardado (senhas, tokens e dados digitados
não entram); o endereço é guardado sem a parte depois do "?". O log nunca derruba a rota:
qualquer falha aqui é engolida.

Guarda: 24 meses do automático; o antes/depois é apagado depois de 12 meses.
"""
import asyncio
import gzip
import json
import re
import time
from datetime import date, datetime, time as dtime
from decimal import Decimal

import jwt as pyjwt

from .config import JWT_SECRET
from .db import ajard_query

LOGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.log_sistema (
    id BIGSERIAL PRIMARY KEY,
    em TIMESTAMPTZ NOT NULL DEFAULT now(),
    usuario_id TEXT,
    usuario_nome TEXT,
    login TEXT,
    modulo TEXT NOT NULL,
    evento TEXT NOT NULL,
    item_tipo TEXT,
    item_id TEXT,
    item_rotulo TEXT,
    resumo TEXT,
    metodo TEXT,
    rota TEXT,
    status INTEGER,
    origem TEXT,
    motivo TEXT,
    detalhe JSONB
);
CREATE INDEX IF NOT EXISTS log_sistema_em ON public.log_sistema (em DESC);
CREATE INDEX IF NOT EXISTS log_sistema_item ON public.log_sistema (item_tipo, item_id, em)
"""

_TABELA_OK = False


async def garantir_log_sistema():
    """Cria a tabela e faz a limpeza de prazo. Uma tentativa por processo."""
    global _TABELA_OK
    if _TABELA_OK:
        return
    _TABELA_OK = True
    for cmd in [c.strip() for c in LOGS_TABLE_SQL.split(";") if c.strip()]:
        await ajard_query(cmd, fetch="none")
    try:
        await ajard_query("DELETE FROM public.log_sistema WHERE em < now() - interval '24 months'", fetch="none")
        await ajard_query("UPDATE public.log_sistema SET detalhe = NULL "
                          "WHERE detalhe IS NOT NULL AND em < now() - interval '12 months'", fetch="none")
    except Exception:
        pass


# ── O que NÃO vira log (ruído: renovação de sessão, avisos lidos, a própria consulta de logs) ──
_IGNORAR = (
    "/auth/renovar", "/auth/logout", "/api/notificacoes", "/api/mural/lido", "/logs-sistema",
    "/acesso/meu-modo", "/api/push", "/health",
)
_IGNORAR_CONTEM = ("webhook", "/extrair")   # chamadas de fora (sem usuário) e leitura por IA (não grava nada)

_MODULOS = (   # prefixo do endereço → nome do módulo (o mais específico primeiro)
    ("/operacional/api/abastecimento", "Abastecimento"),
    ("/abastecimento", "Abastecimento"),
    ("/operacional", "Operacional"),
    ("/manutencao", "Manutenção"),
    ("/compras", "Compras"), ("/cotacoes", "Compras"), ("/cotacao", "Compras"),
    ("/rh", "RH"),
    ("/checklist", "Checklist"),
    ("/jardinagem", "Jardinagem"),
    ("/atendimento", "Atendimento"), ("/whatsapp", "Atendimento"),
    ("/acesso", "Acesso"), ("/auth", "Acesso"),
)


def modulo_da_rota(path: str) -> str:
    for pref, nome in _MODULOS:
        if path.startswith(pref):
            return nome
    return "Administração"


_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
_RE_ID = re.compile(rf"/({_UUID}|\d+)(?=/|$)")


def rota_generica(path: str) -> str:
    """Troca códigos por {id}: a mesma ação fica com o mesmo texto."""
    return _RE_ID.sub("/{id}", path)


# ── Nível detalhado: Operacional (OS e registros do dia) ─────────────────────
_RE_OS = re.compile(rf"^/operacional/api/os/({_UUID})(?:/(liberar|concluir|reabrir|fechar))?$")
_RE_PARTE = re.compile(rf"^/operacional/api/(?:minhas-partes|partes)/({_UUID})$")
_RE_NOVA_PARTE = re.compile(rf"^/operacional/api/os/({_UUID})/partes$")
_RE_NOVA_OS = re.compile(r"^/operacional/api/os(?:/avulsa)?$")

_ROTULO_OS = {
    "operador_id": "Responsável", "status": "Status", "obra": "Obra", "cliente_id": "Cliente",
    "cliente_nome_avulso": "Cliente (avulso)", "equipamento_id": "Máquina", "tipo_servico_id": "Tipo de serviço",
    "regime_cobranca": "Regime de cobrança", "data_inicio": "Início", "data_fim": "Fim", "descricao": "Descrição",
    "codigo_erp": "Código no ERP", "valor_hora": "Valor da hora", "valor_metro": "Valor do metro",
    "valor_diaria": "Valor da diária", "valor_km": "Valor do km", "valor_viagem": "Valor da viagem",
    "ativo": "Ativa",
}
_ROTULO_PARTE = {
    "data": "Dia", "horimetro_inicial": "Horímetro inicial", "horimetro_final": "Horímetro final",
    "hora_inicio": "Hora início", "hora_fim": "Hora fim", "horas_trabalhadas": "Horas trabalhadas",
    "horas_cobradas": "Horas cobradas", "km_inicial": "KM inicial", "km_final": "KM final",
    "km_percorrido": "KM percorrido", "qtd_metros": "Metros", "qtd_viagens": "Viagens",
    "quantidade_diarias": "Diárias", "observacao": "Observação", "equipamento_id": "Máquina",
    "operador_id": "Operador", "tipo_medicao": "Medição", "sem_almoco": "Sem almoço",
    "vinculo_operador": "Vínculo", "fornecedor": "Fornecedor", "equipamento_terceiro": "Máquina de terceiro",
    "por_conta_de": "Por conta de", "trajeto": "Trajeto", "ativo": "Ativo",
}
_NAO_COMPARAR = {"id", "criado_em", "atualizado_em", "client_id", "criado_por", "os_id", "ano", "sequencia", "numero"}
_CAMPOS_NOVA_PARTE = ("data", "equipamento_id", "operador_id", "tipo_medicao", "horimetro_inicial", "horimetro_final",
                      "hora_inicio", "hora_fim", "km_inicial", "km_final", "qtd_metros", "qtd_viagens",
                      "quantidade_diarias", "observacao")
_CAMPOS_NOVA_OS = ("obra", "cliente_id", "cliente_nome_avulso", "equipamento_id", "operador_id", "tipo_servico_id",
                   "regime_cobranca", "status")

_cache_nome: dict = {}   # (tabela, id) → (em, texto)


async def _nome(tabela: str, coluna: str, ident):
    if ident in (None, ""):
        return None
    chave = (tabela, str(ident))
    c = _cache_nome.get(chave)
    if c and time.time() - c[0] < 600:
        return c[1]
    try:
        r = await ajard_query(f"SELECT {coluna} AS t FROM {tabela} WHERE id::text=%s", (str(ident),), fetch="one")
        txt = (r or {}).get("t") or str(ident)
    except Exception:
        txt = str(ident)
    _cache_nome[chave] = (time.time(), txt)
    return txt


_FK = {
    "operador_id": ("public.usuarios_garra", "nome"),
    "equipamento_id": ("operacional.equipamentos", "codigo"),
    "cliente_id": ("public.clientes_garra", "nome"),
    "tipo_servico_id": ("operacional.tipos_servico", "nome"),
}


async def _texto(campo: str, v):
    """Valor de um campo em texto de gente (None = vazio)."""
    if v is None or v == "":
        return None
    if campo in _FK:
        return await _nome(_FK[campo][0], _FK[campo][1], v)
    if isinstance(v, bool):
        return "Sim" if v else "Não"
    if isinstance(v, datetime):
        return v.strftime("%d/%m/%Y %H:%M")
    if isinstance(v, date):
        return v.strftime("%d/%m/%Y")
    if isinstance(v, dtime):
        return v.strftime("%H:%M")
    if isinstance(v, (Decimal, float)):
        t = f"{float(v):.2f}".rstrip("0").rstrip(".")
        return t.replace(".", ",")
    return str(v)


def _igual(a, b) -> bool:
    if a in (None, "") and b in (None, ""):
        return True
    if isinstance(a, (Decimal, float, int)) and isinstance(b, (Decimal, float, int)) \
            and not isinstance(a, bool) and not isinstance(b, bool):
        return abs(float(a) - float(b)) < 0.0005
    return a == b


async def _diferencas(antes: dict, depois: dict, rotulos: dict):
    """Lista [{campo, antes, depois}] dos campos que mudaram."""
    out = []
    for campo in depois.keys():
        if campo in _NAO_COMPARAR:
            continue
        if _igual((antes or {}).get(campo), depois.get(campo)):
            continue
        out.append({"campo": rotulos.get(campo) or campo.replace("_", " ").capitalize(), "chave": campo,
                    "antes": await _texto(campo, (antes or {}).get(campo)),
                    "depois": await _texto(campo, depois.get(campo))})
    return out


async def _linha(tabela: str, ident):
    try:
        return await ajard_query(f"SELECT * FROM {tabela} WHERE id::text=%s", (str(ident),), fetch="one")
    except Exception:
        return None


def _rotulo_os(os_row) -> str:
    if not os_row:
        return "OS"
    seq = os_row.get("sequencia")
    if not seq:
        try:
            seq = int(str(os_row.get("numero") or "").split("-")[-1])
        except Exception:
            seq = None
    return f"OS {int(seq)}" if seq else (os_row.get("numero") or "OS")


def _dia(parte) -> str:
    d = (parte or {}).get("data")
    return d.strftime("%d/%m") if isinstance(d, (date, datetime)) else (str(d) if d else "")


async def _preparar(metodo: str, path: str):
    """Antes da rota rodar: tira a foto do que vai ser alterado. Devolve o contexto ou None."""
    m = _RE_OS.match(path)
    if m and metodo in ("PATCH", "PUT", "DELETE", "POST"):
        if metodo == "POST" and not m.group(2):
            return None
        return {"tipo": "os", "id": m.group(1), "acao": m.group(2),
                "antes": await _linha("operacional.ordens_servico", m.group(1))}
    m = _RE_PARTE.match(path)
    if m and metodo in ("PATCH", "PUT", "DELETE"):
        antes = await _linha("operacional.partes_diarias", m.group(1))
        return {"tipo": "parte", "id": m.group(1), "antes": antes}
    m = _RE_NOVA_PARTE.match(path)
    if m and metodo == "POST":
        return {"tipo": "nova_parte", "os_id": m.group(1)}
    if _RE_NOVA_OS.match(path) and metodo == "POST":
        return {"tipo": "nova_os"}
    return None


async def _concluir(ctx: dict, metodo: str, corpo_resp):
    """Depois da rota (sucesso): compara com a foto e devolve (evento, item_tipo, item_id, rotulo, resumo, detalhe)."""
    t = ctx["tipo"]
    if t == "os":
        antes = ctx.get("antes") or {}
        depois = await _linha("operacional.ordens_servico", ctx["id"]) or {}
        difs = await _diferencas(antes, depois, _ROTULO_OS)
        rot = _rotulo_os(depois or antes)
        chaves = {d["chave"] for d in difs}
        if metodo == "DELETE" or ("ativo" in chaves and depois.get("ativo") is False):
            return "Exclusão", "os", ctx["id"], rot, f"Excluiu a {rot}", difs
        if ctx.get("acao") == "liberar":
            return "Liberação", "os", ctx["id"], rot, f"Liberou a {rot}, que ficou sem responsável", difs
        if ctx.get("acao") in ("concluir", "fechar", "reabrir") or chaves == {"status"}:
            st = next((d for d in difs if d["chave"] == "status"), None)
            txt = f"{rot}: status de {st['antes'] or '(vazio)'} para {st['depois'] or '(vazio)'}" if st else f"{rot}: {ctx.get('acao') or 'status'}"
            return "Mudou status", "os", ctx["id"], rot, txt, difs
        if "operador_id" in chaves:
            op = next(d for d in difs if d["chave"] == "operador_id")
            return ("Encaminhamento", "os", ctx["id"], rot,
                    f"{rot} passou de {op['antes'] or 'sem responsável'} para {op['depois'] or 'sem responsável'}", difs)
        if not difs:
            return "Alteração", "os", ctx["id"], rot, f"Salvou a {rot} sem mudar nenhum campo", difs
        return "Alteração", "os", ctx["id"], rot, f"Alterou a {rot} ({len(difs)} campo(s))".replace("1 campo(s)", "1 campo").replace(" campo(s)", " campos"), difs
    if t == "parte":
        antes = ctx.get("antes") or {}
        depois = await _linha("operacional.partes_diarias", ctx["id"]) or {}
        base = depois or antes
        os_row = await _linha("operacional.ordens_servico", base.get("os_id")) if base.get("os_id") else None
        rot = _rotulo_os(os_row)
        difs = await _diferencas(antes, depois, _ROTULO_PARTE) if depois else []
        chaves = {d["chave"] for d in difs}
        if metodo == "DELETE" or not depois or ("ativo" in chaves and depois.get("ativo") is False):
            if not difs:   # exclusão definitiva: guarda o que havia
                difs = await _diferencas({}, {k: antes.get(k) for k in _CAMPOS_NOVA_PARTE if antes.get(k) not in (None, "", 0)}, _ROTULO_PARTE)
                difs = [{"campo": d["campo"], "chave": d["chave"], "antes": d["depois"], "depois": None} for d in difs]
            return "Exclusão", "os", str(base.get("os_id") or ""), rot, f"Excluiu o registro do dia {_dia(base)} da {rot}", difs
        return ("Alteração", "os", str(base.get("os_id") or ""), rot,
                f"Corrigiu o registro do dia {_dia(base)} da {rot} ({len(difs)} campo(s))".replace("1 campo(s)", "1 campo").replace(" campo(s)", " campos"), difs)
    if t == "nova_parte":
        os_row = await _linha("operacional.ordens_servico", ctx["os_id"])
        rot = _rotulo_os(os_row)
        nova = corpo_resp if isinstance(corpo_resp, dict) else {}
        linha = await _linha("operacional.partes_diarias", nova.get("id")) if nova.get("id") else None
        difs = await _diferencas({}, {k: linha.get(k) for k in _CAMPOS_NOVA_PARTE if linha.get(k) not in (None, "", 0)}, _ROTULO_PARTE) if linha else []
        return "Inclusão", "os", ctx["os_id"], rot, f"Lançou registro do dia {_dia(linha)} na {rot}".replace("  ", " "), difs
    if t == "nova_os":
        nova = corpo_resp if isinstance(corpo_resp, dict) else {}
        nova = nova.get("os") if isinstance(nova.get("os"), dict) else nova
        linha = await _linha("operacional.ordens_servico", nova.get("id")) if nova.get("id") else None
        if not linha:
            return "Inclusão", "os", None, nova.get("numero") or "OS", "Abriu uma OS", []
        rot = _rotulo_os(linha)
        difs = await _diferencas({}, {k: linha.get(k) for k in _CAMPOS_NOVA_OS if linha.get(k) not in (None, "", 0)}, _ROTULO_OS)
        return "Inclusão", "os", str(linha["id"]), rot, f"Abriu a {rot}" + (f" ({linha.get('obra')})" if linha.get("obra") else ""), difs
    return None


async def _rotulo_recusa(ctx: dict):
    """Item de uma tentativa recusada (para a recusa aparecer no rastreio da OS)."""
    try:
        if ctx["tipo"] == "os":
            return "os", ctx["id"], _rotulo_os(ctx.get("antes"))
        if ctx["tipo"] == "parte":
            os_id = (ctx.get("antes") or {}).get("os_id")
            return "os", str(os_id or ""), _rotulo_os(await _linha("operacional.ordens_servico", os_id) if os_id else None)
        if ctx["tipo"] == "nova_parte":
            return "os", ctx["os_id"], _rotulo_os(await _linha("operacional.ordens_servico", ctx["os_id"]))
    except Exception:
        pass
    return None, None, None


# ── Quem é o usuário (a partir do token; nunca guarda o token) ──────────────
_cache_user: dict = {}


async def _usuario(authorization: str):
    if not authorization or not authorization.startswith("Bearer "):
        return None
    try:
        p = pyjwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
    except Exception:
        return None
    login = p.get("login") or p.get("sub") or ""
    c = _cache_user.get(login)
    if c and time.time() - c[0] < 600:
        return c[1]
    u = None
    try:
        u = await ajard_query("SELECT id, nome, login FROM public.usuarios_garra WHERE login=%s OR id::text=%s",
                              (login, login), fetch="one")
    except Exception:
        pass
    dados = {"id": str((u or {}).get("id") or ""), "nome": (u or {}).get("nome") or p.get("nome") or login,
             "login": (u or {}).get("login") or login}
    _cache_user[login] = (time.time(), dados)
    return dados


_EVENTO_METODO = {"POST": "Inclusão", "PUT": "Alteração", "PATCH": "Alteração", "DELETE": "Exclusão"}
_VERBO = {"POST": "incluir", "PUT": "alterar", "PATCH": "alterar", "DELETE": "excluir"}


async def gravar_log(**c):
    """Grava uma linha. Um único parâmetro JSON (evita conversão automática de textos em data/hora)."""
    try:
        await garantir_log_sistema()
        c["detalhe"] = c.get("detalhe") or None
        await ajard_query(
            """INSERT INTO public.log_sistema
                 (usuario_id, usuario_nome, login, modulo, evento, item_tipo, item_id, item_rotulo,
                  resumo, metodo, rota, status, origem, motivo, detalhe)
               SELECT x.usuario_id, x.usuario_nome, x.login, x.modulo, x.evento, x.item_tipo, x.item_id, x.item_rotulo,
                      x.resumo, x.metodo, x.rota, x.status, x.origem, x.motivo, x.detalhe
               FROM jsonb_to_record(%s::jsonb) AS x(
                  usuario_id text, usuario_nome text, login text, modulo text, evento text, item_tipo text,
                  item_id text, item_rotulo text, resumo text, metodo text, rota text, status int,
                  origem text, motivo text, detalhe jsonb)""",
            (json.dumps(c, ensure_ascii=False, default=str),), fetch="none")
    except Exception as e:   # o log nunca derruba nada
        print(f"[LOG-SISTEMA] falhou ao gravar: {type(e).__name__}: {e}")


def _origem(referer: str) -> str:
    r = referer or ""
    m = re.match(r"^https?://[^/]+(/[^?#]*)", r)
    caminho = m.group(1) if m else ""
    if caminho.startswith("/mobile") or "/mobile" in caminho:
        return "Aplicativo (celular)"
    if caminho.startswith("/admin"):
        return "Admin"
    return caminho[:60] or None


_TAREFAS: set = set()


class MiddlewareLogSistema:
    """ASGI puro. Só olha gravações (POST/PUT/PATCH/DELETE). Leitura (GET) passa direto, sem custo."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        metodo = scope.get("method", "GET")
        path = scope.get("path", "")
        if metodo not in ("POST", "PUT", "PATCH", "DELETE") or path.startswith(_IGNORAR) \
                or any(t in path for t in _IGNORAR_CONTEM):
            return await self.app(scope, receive, send)

        hdrs = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers") or []}
        ctx = None
        try:
            ctx = await _preparar(metodo, path)
        except Exception:
            ctx = None
        est = {"status": None, "gzip": False, "corpo": b"", "cheio": False}

        async def _send(msg):
            if msg["type"] == "http.response.start":
                est["status"] = msg.get("status")
                est["gzip"] = any(k.lower() == b"content-encoding" and b"gzip" in v.lower() for k, v in msg.get("headers", []))
            elif msg["type"] == "http.response.body" and not est["cheio"]:
                est["corpo"] += msg.get("body", b"")
                if len(est["corpo"]) > 300_000:
                    est["cheio"], est["corpo"] = True, b""
            await send(msg)

        try:
            await self.app(scope, receive, _send)
        finally:
            if est["status"] is not None:
                t = asyncio.create_task(self._registrar(metodo, path, hdrs, ctx, est))
                _TAREFAS.add(t)                      # guarda a referência até terminar (senão a tarefa pode sumir)
                t.add_done_callback(_TAREFAS.discard)

    async def _registrar(self, metodo, path, hdrs, ctx, est):
        try:
            status = est["status"] or 0
            corpo = None
            if est["corpo"]:
                bruto = est["corpo"]
                try:
                    if est["gzip"]:
                        bruto = gzip.decompress(bruto)
                    corpo = json.loads(bruto)
                except Exception:
                    corpo = None
            u = await _usuario(hdrs.get("authorization"))
            modulo = modulo_da_rota(path)
            base = {"metodo": metodo, "rota": rota_generica(path), "status": status, "modulo": modulo,
                    "origem": _origem(hdrs.get("referer")),
                    "usuario_id": (u or {}).get("id"), "usuario_nome": (u or {}).get("nome") or "(não identificado)",
                    "login": (u or {}).get("login")}
            if path == "/auth/login":
                if status < 400 and isinstance(corpo, dict):
                    us = corpo.get("usuario") if isinstance(corpo.get("usuario"), dict) else corpo
                    base.update(usuario_nome=us.get("nome") or us.get("login") or base["usuario_nome"], login=us.get("login"))
                    return await gravar_log(**base, evento="Entrada", resumo="Entrou no sistema")
                return await gravar_log(**base, evento="Recusa", resumo="Tentativa de entrada recusada",
                                        motivo=self._motivo(corpo))
            if status >= 400:
                if not u:          # sem login válido: não é ação de ninguém (robôs, sessão vencida)
                    return
                it, iid, rot = (await _rotulo_recusa(ctx)) if ctx else (None, None, None)
                return await gravar_log(**base, evento=("Erro" if status >= 500 else "Recusa"),
                                        item_tipo=it, item_id=iid, item_rotulo=rot,
                                        resumo=f"Tentou {_VERBO[metodo]} e o servidor "
                                               + ("deu erro" if status >= 500 else "recusou"),
                                        motivo=self._motivo(corpo))
            if ctx:
                r = await _concluir(ctx, metodo, corpo)
                if r:
                    evento, it, iid, rot, resumo, difs = r
                    return await gravar_log(**base, evento=evento, item_tipo=it, item_id=iid, item_rotulo=rot,
                                            resumo=resumo,
                                            detalhe=[{k: d[k] for k in ("campo", "antes", "depois")} for d in difs] or None)
            await gravar_log(**base, evento=_EVENTO_METODO[metodo], resumo=None)
        except Exception as e:
            print(f"[LOG-SISTEMA] falhou: {type(e).__name__}: {e}")

    @staticmethod
    def _motivo(corpo):
        if isinstance(corpo, dict):
            d = corpo.get("detail")
            if isinstance(d, str):
                return d[:400]
            if d is not None:
                return json.dumps(d, ensure_ascii=False)[:400]
        return None
