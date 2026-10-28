"""(26/10/2026) Atendimento WhatsApp — núcleo: schema `whatsapp`, textos, máquina de estados da conversa.

Substitui o BotConversa. A conversa no WhatsApp é UM fio por colaborador; no sistema ela é dividida em
ATENDIMENTOS, cada um de um setor (Comercial, Financeiro e RH, Gerência, Manutenção). Permissão é por
atendimento (ver routers/whatsapp_atendimento.py).

Fluxo do bot (textos exatos do BotConversa, editáveis em whatsapp.parametros):
  sem atendimento aberto → saudação + menu → número de setor → abre atendimento e avisa a atendente
  mídia sem atendimento   → "Pra quem devo direcionar esse(s) arquivo(s)?" → número → abre no setor
  com atendimento aberto  → mensagem vai para a atendente; se a conversa parou há X horas, pergunta
                            se quer outro setor ("Seu atendimento está atribuído à …")
  MENU                    → encerra o atendimento aberto e mostra o menu
  atendente respondeu pelo celular do número → bot fica quieto para aquele contato

Provedor (Evolution) só em core/whatsapp_provedor.py.
"""
import re
import json
import time
import asyncio
import logging
import unicodedata
from datetime import datetime, timedelta, timezone

from core.db import _get_apool, _converter_placeholders, _ERROS_CONEXAO
from core import whatsapp_provedor as P

log = logging.getLogger("whatsapp")
FUSO = timezone(timedelta(hours=-3))      # Brasília (sem horário de verão)


async def q(sql, params=None, fetch="all"):
    """Mesma semântica do ajard_query, SEM a conversão automática de strings em data/hora: aqui circula
    texto livre de WhatsApp ("07:30", "2026-10-27") que precisa continuar texto. Datas vão como datetime."""
    args = list(params) if params else []
    for tentativa in (1, 2):
        try:
            pool = await _get_apool()
            async with pool.acquire() as conn:
                if fetch == "none" and not args:
                    await conn.execute(sql)
                    return None
                sq = _converter_placeholders(sql)
                if fetch == "one":
                    r = await conn.fetchrow(sq, *args)
                    return dict(r) if r is not None else None
                if fetch == "all":
                    return [dict(r) for r in await conn.fetch(sq, *args)]
                await conn.execute(sq, *args)
                return None
        except _ERROS_CONEXAO:
            if tentativa == 2:
                raise

# ══════════════════════════════════════════════════════════════
# SCHEMA (nasce do código — banco zerado sobe sozinho)
# ══════════════════════════════════════════════════════════════
_OK = False

DDL = """
CREATE SCHEMA IF NOT EXISTS whatsapp;

CREATE TABLE IF NOT EXISTS whatsapp.setores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo TEXT NOT NULL UNIQUE,
    nome TEXT NOT NULL,
    nome_msg TEXT NOT NULL,
    restrito BOOLEAN NOT NULL DEFAULT false,
    responsavel_id UUID,
    ordem INT NOT NULL DEFAULT 0,
    ativo BOOLEAN NOT NULL DEFAULT true,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    deletado_em TIMESTAMPTZ);

CREATE TABLE IF NOT EXISTS whatsapp.setor_atendentes (
    setor_id UUID NOT NULL REFERENCES whatsapp.setores(id),
    usuario_id UUID NOT NULL,
    assinatura TEXT,
    ativo BOOLEAN NOT NULL DEFAULT true,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (setor_id, usuario_id));

CREATE TABLE IF NOT EXISTS whatsapp.acessos_setor (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id UUID NOT NULL,
    setor_id UUID NOT NULL REFERENCES whatsapp.setores(id),
    nivel TEXT NOT NULL CHECK (nivel IN ('ver','atender')),
    valido_ate TIMESTAMPTZ,
    motivo TEXT,
    concedido_por UUID,
    concedido_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    revogado_por UUID,
    revogado_em TIMESTAMPTZ);

CREATE TABLE IF NOT EXISTS whatsapp.menus (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    codigo TEXT NOT NULL UNIQUE,
    titulo TEXT NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT true,
    deletado_em TIMESTAMPTZ);

CREATE TABLE IF NOT EXISTS whatsapp.opcoes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    menu_id UUID NOT NULL REFERENCES whatsapp.menus(id),
    numero INT NOT NULL,
    rotulo TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('texto','setor','submenu')),
    resposta TEXT,
    setor_id UUID REFERENCES whatsapp.setores(id),
    submenu_id UUID REFERENCES whatsapp.menus(id),
    ativo BOOLEAN NOT NULL DEFAULT true,
    deletado_em TIMESTAMPTZ);
CREATE UNIQUE INDEX IF NOT EXISTS ux_wa_opcao_numero ON whatsapp.opcoes (menu_id, numero) WHERE deletado_em IS NULL;

CREATE TABLE IF NOT EXISTS whatsapp.contatos (
    telefone TEXT PRIMARY KEY,
    nome_whatsapp TEXT,
    usuario_id UUID,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS whatsapp.sessoes (
    telefone TEXT PRIMARY KEY,
    menu_atual_id UUID,
    aguardando TEXT,
    midias_pendentes UUID[] NOT NULL DEFAULT '{}',
    bot_pausado_ate TIMESTAMPTZ,
    ultima_interacao TIMESTAMPTZ NOT NULL DEFAULT now());
ALTER TABLE whatsapp.sessoes ADD COLUMN IF NOT EXISTS atendimento_ativo UUID;

CREATE TABLE IF NOT EXISTS whatsapp.atendimentos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero BIGSERIAL,
    telefone TEXT NOT NULL,
    setor_id UUID NOT NULL REFERENCES whatsapp.setores(id),
    status TEXT NOT NULL DEFAULT 'aberto' CHECK (status IN ('aberto','em_atendimento','pendente','encerrado')),
    motivo_pendencia TEXT,
    atribuido_a UUID,
    aberto_por TEXT NOT NULL DEFAULT 'colaborador',
    fora_horario BOOLEAN NOT NULL DEFAULT false,
    aberto_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    primeira_resposta_em TIMESTAMPTZ,
    ultima_msg_colaborador_em TIMESTAMPTZ,
    ultima_msg_atendente_em TIMESTAMPTZ,
    ultima_atividade_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    encerrado_por UUID,
    encerrado_em TIMESTAMPTZ);
-- (26/10/2026) atendimentos em paralelo: um aberto POR SETOR (antes era um por contato)
DROP INDEX IF EXISTS whatsapp.ux_wa_atend_aberto;
CREATE UNIQUE INDEX IF NOT EXISTS ux_wa_atend_aberto_setor ON whatsapp.atendimentos (telefone, setor_id) WHERE status <> 'encerrado';
CREATE INDEX IF NOT EXISTS ix_wa_atend_lista ON whatsapp.atendimentos (status, ultima_atividade_em DESC);

CREATE TABLE IF NOT EXISTS whatsapp.atendimento_eventos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atendimento_id UUID NOT NULL REFERENCES whatsapp.atendimentos(id),
    tipo TEXT NOT NULL,
    de_usuario_id UUID,
    para_usuario_id UUID,
    de_setor_id UUID,
    para_setor_id UUID,
    observacao TEXT,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ix_wa_eventos ON whatsapp.atendimento_eventos (atendimento_id, criado_em);

CREATE TABLE IF NOT EXISTS whatsapp.mensagens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider_msg_id TEXT UNIQUE,
    telefone TEXT NOT NULL,
    atendimento_id UUID REFERENCES whatsapp.atendimentos(id),
    direcao TEXT NOT NULL CHECK (direcao IN ('in','out')),
    origem TEXT NOT NULL CHECK (origem IN ('colaborador','bot','atendente','celular','sistema')),
    usuario_id UUID,
    assinatura TEXT,
    tipo TEXT NOT NULL DEFAULT 'texto',
    texto TEXT,
    midia_caminho TEXT,
    mimetype TEXT,
    nome_arquivo TEXT,
    citado_provider_id TEXT,
    reacao TEXT,
    status TEXT,
    erro TEXT,
    lida_em TIMESTAMPTZ,
    criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ix_wa_msg_tel ON whatsapp.mensagens (telefone, criado_em);
CREATE INDEX IF NOT EXISTS ix_wa_msg_at ON whatsapp.mensagens (atendimento_id, criado_em);

CREATE TABLE IF NOT EXISTS whatsapp.respostas_rapidas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    atalho TEXT NOT NULL,
    texto TEXT NOT NULL,
    setor_id UUID REFERENCES whatsapp.setores(id),
    ordem INT NOT NULL DEFAULT 0,
    deletado_em TIMESTAMPTZ);

CREATE TABLE IF NOT EXISTS whatsapp.parametros (
    chave TEXT PRIMARY KEY,
    valor TEXT,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now());
"""

# Textos exatos levantados do histórico do BotConversa (26/10/2026). {chaves} são preenchidas pelo motor.
PARAMETROS_PADRAO = {
    "saudacao": "{saudacao}, {nome}!\nDigite *apenas o número* de uma das opções abaixo, por favor:\n\n{menu}",
    "abertura_setor": "Olá, você está falando com {atendente} da {setor}. Aguarde um instante que já vou te atender.",
    "paralelo": "Seu atendimento com {outros} continua aberto.",
    "rodape": "Se precisar de algo mais, digite o número do setor que deseja falar ou *0* para ver o menu.",
    "retorno_aberto": "Seu atendimento está atribuído à {atendente}. Se precisar falar em outro setor, *digite o número*, por favor:\n\n{menu}\n\n👉🏻 Mas se sua mensagem é realmente para {atendente}, basta registrar sua solicitação abaixo.",
    "midia_direcionar": "{saudacao}, {nome}! 😊\nPra quem devo direcionar esse(s) arquivo(s) enviados?\n\n{setores}\n\nDigite apenas o número.",
    "encerramento": "{atendente} encerrou atendimento.\n\nSe precisar de mais alguma coisa é só chamar. 🙂",
    "iniciar_conversa": "Olá {nome}.\nAqui é {atendente}. Tudo bem?",
    "opcao_invalida": "*Opção inválida*\nTente novamente por favor",
    "fora_horario": "Nosso horário de atendimento é {horario}. Sua mensagem ficou registrada e será respondida assim que possível.",
    "horario": json.dumps({"1": "07:00-17:30", "2": "07:00-17:30", "3": "07:00-17:30", "4": "07:00-17:30", "5": "07:00-17:30", "6": "07:00-11:00"}),
    "horario_texto": "de segunda a sexta, das 7h às 17h30, e sábado, das 7h às 11h",
    "retorno_horas": "4",
    "sessao_minutos": "30",
    "retencao_meses": "12",
}

SETORES_SEED = [
    # codigo, nome (lista/tela), nome_msg (dentro da frase), restrito, ordem, login provável, assinatura
    ("comercial", "Comercial", "COMERCIAL", False, 1, "luana", "Luana - Atendimento"),
    ("financeiro", "Financeiro e RH", "FINANCEIRO E RH", True, 2, "patricia", "Patrícia de Lourdes Miguel"),
    ("gerencia", "Gerência", "GERÊNCIA", False, 3, "edvania", "Gerencia Geral Edvania"),
    ("manutencao", "Manutenção", "MANUTENÇÃO", False, 4, "bruna", "Manutenção - BRUNA"),
]
MENU_SEED = [
    (1, "Envios diários e Caçambas", "setor", "comercial", None),
    (2, "Dep. Pessoal, Doc´s e Financeiro", "setor", "financeiro", None),
    (3, "Duvidas sobre a Marcação de ponto", "setor", "gerencia", None),
    (4, "Manutenção", "setor", "manutencao", None),
    (5, "Link CHECK LIST", "texto", None,
     "App Garra 📲\n\nTudo da operação em um só lugar — checklist, rotinas e registros:\n👉 https://garra-sistemas.onrender.com/mobile\n\nChecklist preenchido antes de operar. Sempre. 🧡"),
]
RESPOSTAS_SEED = [
    ("Link do app", "Abre pelo app: https://garra-sistemas.onrender.com/mobile"),
    ("Pedir foto", "Me manda uma foto, por favor."),
    ("Vem ao escritório", "Passa aqui no escritório, por favor."),
]


async def garantir():
    global _OK
    if _OK:
        return
    await q("CREATE EXTENSION IF NOT EXISTS pgcrypto", fetch="none")
    await q(DDL, fetch="none")
    for k, v in PARAMETROS_PADRAO.items():
        await q("INSERT INTO whatsapp.parametros (chave, valor) VALUES (%s,%s) ON CONFLICT (chave) DO NOTHING", (k, v), fetch="none")
    if not await q("SELECT 1 FROM whatsapp.parametros WHERE chave='seed:v1'", fetch="one"):
        await _semear()
    _OK = True


async def _usuario_por_nome(chave):
    return await q(
        """SELECT id FROM public.usuarios_garra WHERE ativo=true AND
           (lower(login)=%s OR lower(translate(split_part(nome,' ',1),'áàâãéêíóôõúçÁÉÍÓÚÇ','aaaaeeiooouucAEIOUC'))=%s)
           ORDER BY (lower(login)=%s) DESC LIMIT 1""", (chave, chave, chave), fetch="one")


async def _semear():
    """Uma vez: setores, atendentes (por login/primeiro nome), menu principal e respostas rápidas."""
    ids = {}
    for codigo, nome, nome_msg, restrito, ordem, login, assinatura in SETORES_SEED:
        u = await _usuario_por_nome(login)
        s = await q(
            """INSERT INTO whatsapp.setores (codigo, nome, nome_msg, restrito, ordem, responsavel_id)
               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (codigo) DO UPDATE SET codigo=EXCLUDED.codigo RETURNING id""",
            (codigo, nome, nome_msg, restrito, ordem, u["id"] if u else None), fetch="one")
        ids[codigo] = s["id"]
        if u:
            await q(
                """INSERT INTO whatsapp.setor_atendentes (setor_id, usuario_id, assinatura) VALUES (%s,%s,%s)
                   ON CONFLICT DO NOTHING""", (s["id"], u["id"], assinatura), fetch="none")
    m = await q(
        """INSERT INTO whatsapp.menus (codigo, titulo) VALUES ('principal','Menu principal')
           ON CONFLICT (codigo) DO UPDATE SET codigo=EXCLUDED.codigo RETURNING id""", fetch="one")
    for numero, rotulo, tipo, setor, resposta in MENU_SEED:
        await q(
            """INSERT INTO whatsapp.opcoes (menu_id, numero, rotulo, tipo, setor_id, resposta)
               SELECT %s,%s,%s,%s,%s,%s WHERE NOT EXISTS
               (SELECT 1 FROM whatsapp.opcoes WHERE menu_id=%s AND numero=%s AND deletado_em IS NULL)""",
            (m["id"], numero, rotulo, tipo, ids.get(setor), resposta, m["id"], numero), fetch="none")
    for i, (atalho, texto) in enumerate(RESPOSTAS_SEED):
        await q("INSERT INTO whatsapp.respostas_rapidas (atalho, texto, ordem) VALUES (%s,%s,%s)", (atalho, texto, i), fetch="none")
    await q("INSERT INTO whatsapp.parametros (chave, valor) VALUES ('seed:v1','26/10/2026') ON CONFLICT DO NOTHING", fetch="none")


# ══════════════════════════════════════════════════════════════
# UTILITÁRIOS
# ══════════════════════════════════════════════════════════════
_cache_par = {"em": 0, "v": {}}


async def parametros(forcar=False):
    if forcar or time.time() - _cache_par["em"] > 30:
        rows = await q("SELECT chave, valor FROM whatsapp.parametros") or []
        _cache_par["v"] = {r["chave"]: r["valor"] for r in rows}
        _cache_par["em"] = time.time()
    return _cache_par["v"]


def limpar_cache():
    _cache_par["em"] = 0


async def par(chave):
    return (await parametros()).get(chave) or PARAMETROS_PADRAO.get(chave, "")


async def par_int(chave, padrao):
    try:
        return int(await par(chave))
    except (TypeError, ValueError):
        return padrao


def agora():
    return datetime.now(timezone.utc)


def saudacao_hora(dt=None):
    h = (dt or agora()).astimezone(FUSO).hour
    return "Bom dia" if 4 <= h < 12 else ("Boa tarde" if 12 <= h < 18 else "Boa noite")


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower().strip()


def telefone_de_jid(valor):
    """'5537991531090@s.whatsapp.net' → '5537991531090'. None para grupo/status/canal."""
    v = (valor or "").strip()
    if not v or v.endswith("@g.us") or v.startswith("status@") or v.endswith("@newsletter") or v.endswith("@broadcast"):
        return None
    d = re.sub(r"\D", "", v.split("@")[0].split(":")[0])
    return d or None


def primeiro_nome(nome):
    return (nome or "").strip().split(" ")[0] if nome else ""


async def fora_do_horario(dt=None):
    try:
        grade = json.loads(await par("horario"))
    except Exception:
        return False
    local = (dt or agora()).astimezone(FUSO)
    faixa = grade.get(str(local.isoweekday()))
    if not faixa:
        return True
    try:
        ini, fim = faixa.split("-")
        hm = local.strftime("%H:%M")
        return not (ini.strip() <= hm <= fim.strip())
    except ValueError:
        return False


def preencher(modelo, **valores):
    out = modelo or ""
    for k, v in valores.items():
        out = out.replace("{" + k + "}", str(v if v is not None else ""))
    return out.replace(", !", "!").replace(" .", ".").replace(" ,", ",")      # nome vazio não deixa "Olá ."


# ══════════════════════════════════════════════════════════════
# DADOS
# ══════════════════════════════════════════════════════════════
async def contato(telefone, nome_whatsapp=None):
    """Garante o contato e devolve {telefone, nome, usuario_id}. Nome do cadastro tem prioridade."""
    await q(
        """INSERT INTO whatsapp.contatos (telefone, nome_whatsapp) VALUES (%s,%s)
           ON CONFLICT (telefone) DO UPDATE SET nome_whatsapp=COALESCE(EXCLUDED.nome_whatsapp, whatsapp.contatos.nome_whatsapp),
           atualizado_em=now()""", (telefone, (nome_whatsapp or None)), fetch="none")
    c = await q(
        """SELECT c.telefone, c.usuario_id, COALESCE(u.nome, c.nome_whatsapp, c.telefone) AS nome
           FROM whatsapp.contatos c LEFT JOIN public.usuarios_garra u ON u.id=c.usuario_id WHERE c.telefone=%s""",
        (telefone,), fetch="one")
    return c


async def sessao(telefone):
    await q("INSERT INTO whatsapp.sessoes (telefone) VALUES (%s) ON CONFLICT DO NOTHING", (telefone,), fetch="none")
    return await q("SELECT * FROM whatsapp.sessoes WHERE telefone=%s", (telefone,), fetch="one")


async def sessao_set(telefone, **campos):
    if not campos:
        return
    sets = ", ".join(f"{k}=%s" for k in campos)
    await q(f"UPDATE whatsapp.sessoes SET {sets}, ultima_interacao=now() WHERE telefone=%s",
                      (*campos.values(), telefone), fetch="none")


_SQL_AT = """SELECT a.*, s.nome AS setor_nome, s.nome_msg, u.nome AS atendente_nome
           FROM whatsapp.atendimentos a JOIN whatsapp.setores s ON s.id=a.setor_id
           LEFT JOIN public.usuarios_garra u ON u.id=a.atribuido_a"""


async def atendimentos_abertos(telefone):
    """(26/10/2026) Um colaborador pode ter vários atendimentos abertos — um por setor. Mais recente primeiro."""
    return await q(_SQL_AT + " WHERE a.telefone=%s AND a.status<>'encerrado' ORDER BY a.ultima_atividade_em DESC",
                   (telefone,)) or []


async def atendimento_por_id(at_id):
    return await q(_SQL_AT + " WHERE a.id=%s", (at_id,), fetch="one")


async def ativo(telefone, abertos=None, citado=None):
    """Para qual atendimento vai a mensagem do colaborador:
    1. o da mensagem que ele respondeu em cima (citação);
    2. o ativo da sessão — último setor que ele escolheu ou última atendente que respondeu;
    3. o de atividade mais recente."""
    abertos = abertos if abertos is not None else await atendimentos_abertos(telefone)
    if not abertos:
        return None
    if citado:
        m = await q("SELECT atendimento_id FROM whatsapp.mensagens WHERE provider_msg_id=%s", (citado,), fetch="one")
        hit = next((a for a in abertos if m and a["id"] == m["atendimento_id"]), None)
        if hit:
            return hit
    s = await q("SELECT atendimento_ativo FROM whatsapp.sessoes WHERE telefone=%s", (telefone,), fetch="one")
    return next((a for a in abertos if s and a["id"] == s["atendimento_ativo"]), None) or abertos[0]


async def marcar_ativo(telefone, at_id):
    await sessao(telefone)
    await q("UPDATE whatsapp.sessoes SET atendimento_ativo=%s WHERE telefone=%s", (at_id, telefone), fetch="none")


async def soltar(row):
    """Mensagem de controle do menu (MENU, número escolhido) não fica no atendimento de outro setor."""
    await q("UPDATE whatsapp.mensagens SET atendimento_id=NULL, atualizado_em=now() WHERE id=%s", (row["id"],), fetch="none")


async def anexar_soltas(telefone, at_id, minutos):
    await q("""UPDATE whatsapp.mensagens SET atendimento_id=%s, atualizado_em=now()
               WHERE telefone=%s AND atendimento_id IS NULL AND criado_em > now() - (%s || ' minutes')::interval""",
            (at_id, telefone, str(minutos)), fetch="none")


async def setor(setor_id):
    return await q(
        """SELECT s.*, u.nome AS responsavel_nome FROM whatsapp.setores s
           LEFT JOIN public.usuarios_garra u ON u.id=s.responsavel_id WHERE s.id=%s""", (setor_id,), fetch="one")


async def setores_ativos():
    return await q(
        """SELECT s.*, u.nome AS responsavel_nome FROM whatsapp.setores s
           LEFT JOIN public.usuarios_garra u ON u.id=s.responsavel_id
           WHERE s.ativo AND s.deletado_em IS NULL ORDER BY s.ordem, s.nome""") or []


async def menu_principal():
    return await q("SELECT * FROM whatsapp.menus WHERE codigo='principal'", fetch="one")


async def opcoes(menu_id):
    return await q(
        """SELECT * FROM whatsapp.opcoes WHERE menu_id=%s AND ativo AND deletado_em IS NULL ORDER BY numero""",
        (menu_id,)) or []


def texto_menu(ops):
    return "\n".join(f"{o['numero']} - {o['rotulo']}" for o in ops)


async def registrar(telefone, direcao, origem, tipo="texto", texto=None, provider_msg_id=None, atendimento_id=None,
                    usuario_id=None, assinatura=None, midia_caminho=None, mimetype=None, nome_arquivo=None,
                    citado_provider_id=None, status=None, erro=None):
    """Grava a mensagem. Devolve a linha, ou None se o provider_msg_id já existe (webhook repetido)."""
    row = await q(
        """INSERT INTO whatsapp.mensagens (telefone, direcao, origem, tipo, texto, provider_msg_id, atendimento_id,
               usuario_id, assinatura, midia_caminho, mimetype, nome_arquivo, citado_provider_id, status, erro)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (provider_msg_id) DO NOTHING RETURNING *""",
        (telefone, direcao, origem, tipo, texto, provider_msg_id, atendimento_id, usuario_id, assinatura,
         midia_caminho, mimetype, nome_arquivo, citado_provider_id, status, erro), fetch="one")
    if row and atendimento_id:
        campo = "ultima_msg_colaborador_em" if direcao == "in" else "ultima_msg_atendente_em"
        await q(f"UPDATE whatsapp.atendimentos SET ultima_atividade_em=now(), {campo}=now() WHERE id=%s",
                          (atendimento_id,), fetch="none")
    return row


async def enviar_bot(telefone, texto, atendimento_id=None):
    """Mensagem automática: sai pelo provedor e fica no histórico (origem bot)."""
    try:
        pid = await P.enviar_texto(telefone, texto)
        return await registrar(telefone, "out", "bot", texto=texto, provider_msg_id=pid,
                               atendimento_id=atendimento_id, status="enviada")
    except Exception as e:
        log.warning("bot→%s falhou: %s", telefone, e)
        return await registrar(telefone, "out", "bot", texto=texto, atendimento_id=atendimento_id, status="erro", erro=str(e)[:300])


async def evento(atendimento_id, tipo, de_usuario_id=None, para_usuario_id=None, de_setor_id=None, para_setor_id=None, observacao=None):
    await q(
        """INSERT INTO whatsapp.atendimento_eventos
           (atendimento_id, tipo, de_usuario_id, para_usuario_id, de_setor_id, para_setor_id, observacao)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (atendimento_id, tipo, de_usuario_id, para_usuario_id, de_setor_id, para_setor_id, observacao), fetch="none")


async def _avisar(tipo, at_id, titulo, texto, usuario_id, chave=None):
    if not usuario_id:
        return
    try:
        from core.notificar import notificar
        await notificar(tipo, titulo, texto, link=f"/atendimento?at={at_id}", apenas=[usuario_id], chave=chave)
    except Exception as e:
        log.warning("aviso atendimento: %s", e)


async def avisar_nova_msg(at, contato_nome, texto):
    bloco = int(time.time() // 120)       # no máximo 1 aviso a cada 2 min por conversa
    await _avisar("atendimento_msg", at["id"], f"💬 {contato_nome}", f"“{(texto or '')[:200]}”", at.get("atribuido_a"),
                  chave=f"wa-msg:{at['id']}:{bloco}")


# ══════════════════════════════════════════════════════════════
# AÇÕES DE ATENDIMENTO (usadas pelo motor e pelas rotas)
# ══════════════════════════════════════════════════════════════
async def abrir_atendimento(telefone, setor_id, aberto_por="colaborador", usuario_id=None, anexar_desde_min=30):
    """Abre atendimento no setor. Mensagens soltas recentes do contato (menu, mídias) entram nele."""
    s = await setor(setor_id)
    atribuido = usuario_id or s["responsavel_id"]
    fora = await fora_do_horario() if aberto_por == "colaborador" else False
    at = await q(
        """INSERT INTO whatsapp.atendimentos (telefone, setor_id, atribuido_a, aberto_por, fora_horario,
               status, primeira_resposta_em)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
        (telefone, setor_id, atribuido, aberto_por, fora, "em_atendimento" if aberto_por == "atendente" else "aberto",
         agora() if aberto_por == "atendente" else None), fetch="one")
    await anexar_soltas(telefone, at["id"], anexar_desde_min)
    await marcar_ativo(telefone, at["id"])
    await evento(at["id"], "aberto", de_usuario_id=usuario_id, para_usuario_id=atribuido, para_setor_id=setor_id,
                 observacao=aberto_por)
    at.update(setor_nome=s["nome"], nome_msg=s["nome_msg"], responsavel_nome=s.get("responsavel_nome"))
    return at


async def transferir(at, para_setor_id, para_usuario_id=None, por_usuario_id=None, silencioso=False):
    """Se o colaborador já tem atendimento aberto no setor de destino, as mensagens são JUNTADAS nele
    (um aberto por setor) e este é encerrado; senão o próprio atendimento muda de setor."""
    s = await setor(para_setor_id)
    de = por_usuario_id or at.get("atribuido_a")
    ja = await q("""SELECT id, numero, atribuido_a FROM whatsapp.atendimentos
                    WHERE telefone=%s AND setor_id=%s AND status<>'encerrado' AND id<>%s""",
                 (at["telefone"], para_setor_id, at["id"]), fetch="one")
    if ja:
        alvo, destino = ja["id"], para_usuario_id or ja["atribuido_a"] or s["responsavel_id"]
        await q("UPDATE whatsapp.mensagens SET atendimento_id=%s, atualizado_em=now() WHERE atendimento_id=%s",
                (alvo, at["id"]), fetch="none")
        await q("""UPDATE whatsapp.atendimentos SET status='encerrado', encerrado_por=%s, encerrado_em=now(),
                   ultima_atividade_em=now() WHERE id=%s""", (de, at["id"]), fetch="none")
        await q("UPDATE whatsapp.atendimentos SET atribuido_a=%s, ultima_atividade_em=now() WHERE id=%s", (destino, alvo), fetch="none")
        await evento(at["id"], "unido", de_usuario_id=de, para_setor_id=para_setor_id, observacao=f"juntado ao atendimento #{ja['numero']}")
        await evento(alvo, "transferido", de_usuario_id=de, para_usuario_id=destino, de_setor_id=at["setor_id"],
                     para_setor_id=para_setor_id, observacao="silencioso" if silencioso else "juntado")
    else:
        alvo, destino = at["id"], para_usuario_id or s["responsavel_id"]
        await q(
            """UPDATE whatsapp.atendimentos SET setor_id=%s, atribuido_a=%s, status='aberto', ultima_atividade_em=now()
               WHERE id=%s""", (para_setor_id, destino, alvo), fetch="none")
        await evento(alvo, "transferido", de_usuario_id=de, para_usuario_id=destino,
                     de_setor_id=at["setor_id"], para_setor_id=para_setor_id,
                     observacao="silencioso" if silencioso else None)
    await marcar_ativo(at["telefone"], alvo)
    nome_dest = await _nome_usuario(destino)
    if not silencioso:
        await enviar_bot(at["telefone"], preencher(await par("abertura_setor"), atendente=primeiro_nome(nome_dest) or s["nome"],
                                                   setor=s["nome_msg"]), alvo)
    c = await contato(at["telefone"])
    await _avisar("atendimento_novo", alvo, f"⇄ Conversa transferida: {c['nome']}", f"Agora em {s['nome']}", destino)
    return alvo


async def encerrar(at, por_usuario_id=None, avisar_colaborador=True):
    await q(
        """UPDATE whatsapp.atendimentos SET status='encerrado', encerrado_por=%s, encerrado_em=now(), ultima_atividade_em=now()
           WHERE id=%s AND status<>'encerrado'""", (por_usuario_id, at["id"]), fetch="none")
    await evento(at["id"], "encerrado", de_usuario_id=por_usuario_id, observacao=None if por_usuario_id else "colaborador")
    if avisar_colaborador and por_usuario_id:
        nome = await _nome_usuario(por_usuario_id)
        await enviar_bot(at["telefone"], preencher(await par("encerramento"), atendente=primeiro_nome(nome)), at["id"])
    restantes = await atendimentos_abertos(at["telefone"])
    await q("UPDATE whatsapp.sessoes SET atendimento_ativo=%s WHERE telefone=%s AND atendimento_ativo=%s",
            (restantes[0]["id"] if restantes else None, at["telefone"], at["id"]), fetch="none")
    if not restantes:
        await sessao_set(at["telefone"], aguardando=None, menu_atual_id=None, bot_pausado_ate=None)


async def _nome_usuario(uid):
    if not uid:
        return ""
    u = await q("SELECT nome FROM public.usuarios_garra WHERE id=%s", (uid,), fetch="one")
    return (u or {}).get("nome") or ""


# ══════════════════════════════════════════════════════════════
# WEBHOOK → MOTOR
# ══════════════════════════════════════════════════════════════
QR_ATUAL = {"base64": None, "em": 0}


def _desembrulhar(msg):
    """Tira os envelopes (temporária, visualização única, documento com legenda)."""
    for _ in range(4):
        if not isinstance(msg, dict):
            return {}
        for env in ("ephemeralMessage", "viewOnceMessage", "viewOnceMessageV2", "viewOnceMessageV2Extension",
                    "documentWithCaptionMessage"):
            if env in msg:
                msg = (msg[env] or {}).get("message") or {}
                break
        else:
            return msg
    return msg


def interpretar(msg):
    """Mensagem do WhatsApp → (tipo, texto, extra). tipo None = ignorar."""
    m = _desembrulhar(msg or {})
    ctx = None
    if "conversation" in m:
        return "texto", m.get("conversation") or "", {}
    if "extendedTextMessage" in m:
        e = m["extendedTextMessage"] or {}
        ctx = e.get("contextInfo") or {}
        return "texto", e.get("text") or "", {"citado": ctx.get("stanzaId")}
    for chave, tipo in (("imageMessage", "imagem"), ("videoMessage", "video"), ("audioMessage", "audio"),
                        ("documentMessage", "documento"), ("stickerMessage", "figurinha")):
        if chave in m:
            e = m[chave] or {}
            ctx = e.get("contextInfo") or {}
            return tipo, e.get("caption") or "", {"mimetype": e.get("mimetype"), "nome_arquivo": e.get("fileName"),
                                                  "citado": ctx.get("stanzaId"), "midia": True}
    if "locationMessage" in m:
        e = m["locationMessage"] or {}
        lat, lng = e.get("degreesLatitude"), e.get("degreesLongitude")
        return "localizacao", f"📍 {e.get('name') or 'Localização'} — https://maps.google.com/?q={lat},{lng}", {}
    if "contactMessage" in m:
        e = m["contactMessage"] or {}
        return "contato", f"👤 {e.get('displayName') or ''}\n{e.get('vcard') or ''}", {}
    if "reactionMessage" in m:
        e = m["reactionMessage"] or {}
        return "reacao", e.get("text") or "", {"alvo": (e.get("key") or {}).get("id")}
    return None, None, {}


async def processar_webhook(corpo):
    """Ponto de entrada do webhook da Evolution. Nunca levanta exceção."""
    try:
        await garantir()
        ev = (corpo.get("event") or "").lower().replace("_", ".")
        dados = corpo.get("data")
        itens = dados if isinstance(dados, list) else [dados or {}]
        if ev == "messages.upsert":
            for d in itens:
                await _mensagem_recebida(d)
        elif ev == "messages.update":
            for d in itens:
                await _status_mensagem(d)
        elif ev == "connection.update":
            await _conexao(itens[0])
        elif ev == "qrcode.updated":
            q = (itens[0].get("qrcode") or {}) if isinstance(itens[0].get("qrcode"), dict) else {}
            QR_ATUAL.update(base64=q.get("base64") or itens[0].get("base64"), em=time.time())
    except Exception as e:
        log.exception("webhook whatsapp: %s", e)


async def _status_mensagem(d):
    pid = d.get("keyId") or (d.get("key") or {}).get("id") or d.get("messageId")
    st = (d.get("status") or "").upper()
    novo = {"SERVER_ACK": "enviada", "DELIVERY_ACK": "entregue", "READ": "lida", "PLAYED": "lida", "ERROR": "erro"}.get(st)
    if pid and novo:
        await q("""UPDATE whatsapp.mensagens SET status=%s, atualizado_em=now() WHERE provider_msg_id=%s
                             AND COALESCE(status,'') <> 'lida'""", (novo, pid), fetch="none")


async def _conexao(d):
    st = d.get("state") or d.get("status") or ""
    await q("""INSERT INTO whatsapp.parametros (chave, valor, atualizado_em) VALUES ('conexao_estado',%s,now())
                         ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=now()""", (st,), fetch="none")
    limpar_cache()
    if st == "open":
        QR_ATUAL.update(base64=None)
    if st == "close":
        try:
            from core.notificar import notificar
            await notificar("atendimento_conexao", "⚠️ WhatsApp do atendimento desconectado",
                            "O número parou de receber mensagens. Abra Atendimento › Configurar › Conexão e leia o QR Code.",
                            link="/atendimento?config=conexao", chave=f"wa-conexao:{int(time.time() // 3600)}")
        except Exception as e:
            log.warning("aviso conexão: %s", e)


async def _mensagem_recebida(d):
    key = d.get("key") or {}
    remoto = key.get("remoteJid") or ""
    if remoto.endswith("@lid"):
        remoto = key.get("remoteJidAlt") or key.get("senderPn") or d.get("senderPn") or ""
    telefone = telefone_de_jid(remoto)
    pid = key.get("id")
    if not telefone or not pid:
        return
    tipo, texto, extra = interpretar(d.get("message") or {})
    if tipo is None:
        return
    if tipo == "reacao":
        if extra.get("alvo"):
            await q("UPDATE whatsapp.mensagens SET reacao=%s, atualizado_em=now() WHERE provider_msg_id=%s",
                              (texto or None, extra["alvo"]), fetch="none")
        return
    if key.get("fromMe"):
        # Saiu pelo próprio celular do número (não pelo sistema): entra no histórico e o bot se cala.
        if await q("SELECT 1 FROM whatsapp.mensagens WHERE provider_msg_id=%s", (pid,), fetch="one"):
            return
        at = await ativo(telefone)
        row = await registrar(telefone, "out", "celular", tipo=tipo, texto=texto, provider_msg_id=pid,
                              atendimento_id=at["id"] if at else None, mimetype=extra.get("mimetype"),
                              nome_arquivo=extra.get("nome_arquivo"), citado_provider_id=extra.get("citado"),
                              status="enviada")
        await sessao(telefone)
        await sessao_set(telefone, bot_pausado_ate=agora() + timedelta(hours=await par_int("retorno_horas", 4)), aguardando=None)
        if row and extra.get("midia"):
            asyncio.create_task(baixar_e_guardar(row))
        return

    c = await contato(telefone, d.get("pushName"))
    abertos = await atendimentos_abertos(telefone)
    at = await ativo(telefone, abertos, extra.get("citado"))
    row = await registrar(telefone, "in", "colaborador", tipo=tipo, texto=texto, provider_msg_id=pid,
                          atendimento_id=at["id"] if at else None, mimetype=extra.get("mimetype"),
                          nome_arquivo=extra.get("nome_arquivo"), citado_provider_id=extra.get("citado"))
    if not row:
        return                                   # webhook repetido
    if extra.get("midia"):
        asyncio.create_task(baixar_e_guardar(row))
    await decidir(c, row, at, abertos)


async def decidir(c, row, at, abertos):
    """Máquina de estados: o que o bot responde a uma mensagem do colaborador.
    `at` = atendimento para onde a mensagem foi (ativo); `abertos` = todos os abertos do colaborador (um por setor)."""
    telefone = c["telefone"]
    s = await sessao(telefone)
    texto = (row.get("texto") or "").strip()
    eh_midia = row["tipo"] in ("imagem", "video", "audio", "documento", "figurinha")
    numero = int(texto) if texto.isdigit() and len(texto) <= 2 else None
    nome = primeiro_nome(c["nome"]) if c["nome"] != telefone else ""
    menu = await menu_principal()
    ops = await opcoes(menu["id"]) if menu else []

    # MENU mostra o menu de qualquer ponto — NÃO encerra atendimento aberto (26/10/2026): a atendente
    # pode ainda não ter lido; ele continua na lista dela até ela encerrar.
    # (27/10/2026) Atalhos: 0, # e * valem como MENU — mais fácil para o operador no campo.
    # O 0 só não vale dentro de um submenu com sessão viva, onde ele já é "voltar" (tratado adiante).
    em_submenu = s.get("menu_atual_id") and menu and s["menu_atual_id"] != menu["id"]
    if sem_acento(texto) in ("menu", "#", "*") or (texto == "0" and not em_submenu):
        if abertos:
            await soltar(row)
        await sessao_set(telefone, aguardando="menu" if abertos else None, menu_atual_id=menu["id"] if menu else None,
                         midias_pendentes=[], bot_pausado_ate=None)
        await enviar_bot(telefone, preencher(await par("saudacao"), saudacao=saudacao_hora(), nome=nome, menu=texto_menu(ops)))
        return

    # ── com atendimento(s) aberto(s) ──
    if abertos:
        if s.get("aguardando") in ("menu", "troca_setor"):
            await sessao_set(telefone, aguardando=None)
            op = next((o for o in ops if o["numero"] == numero), None) if numero is not None else None
            if op:
                await soltar(row)
                if op["tipo"] == "setor" and op["setor_id"]:
                    await escolher_setor(c, op["setor_id"], abertos)
                else:
                    await enviar_bot(telefone, (op["resposta"] or "") + "\n\n" + await par("rodape"))
                return
        else:
            ultima = await q(
                """SELECT max(criado_em) AS t FROM whatsapp.mensagens WHERE atendimento_id=%s AND id<>%s""",
                (at["id"], row["id"]), fetch="one")
            parada = (ultima or {}).get("t")
            horas = await par_int("retorno_horas", 4)
            if parada and agora() - parada > timedelta(hours=horas):
                atend = primeiro_nome(at.get("atendente_nome")) or at["setor_nome"]
                await enviar_bot(telefone, preencher(await par("retorno_aberto"), atendente=atend, menu=texto_menu(ops)), at["id"])
                await sessao_set(telefone, aguardando="troca_setor")
        if at["status"] == "pendente":
            await q("UPDATE whatsapp.atendimentos SET status='em_atendimento', motivo_pendencia=NULL WHERE id=%s",
                              (at["id"],), fetch="none")
            await evento(at["id"], "respondeu_pendencia")
        await avisar_nova_msg(at, c["nome"], texto or f"[{row['tipo']}]")
        return

    # ── bot em pausa (atendente falou pelo celular) ──
    if s.get("bot_pausado_ate") and s["bot_pausado_ate"] > agora():
        return

    # ── mídia sem atendimento: perguntar para quem vai ──
    if eh_midia:
        pend = list(s.get("midias_pendentes") or []) + [row["id"]]
        ja_perguntou = s.get("aguardando") == "midia_setor"
        await sessao_set(telefone, midias_pendentes=pend, aguardando="midia_setor")
        if not ja_perguntou:
            setores = [x for x in await setores_ativos()]
            lista = "\n".join(f"{i} - {primeiro_nome(x.get('responsavel_nome')) or x['nome']} - {x['nome']}"
                              for i, x in enumerate(setores, 1))
            await enviar_bot(telefone, preencher(await par("midia_direcionar"), saudacao=saudacao_hora(), nome=nome, setores=lista))
        return

    if s.get("aguardando") == "midia_setor" and numero is not None:
        setores = await setores_ativos()
        if 1 <= numero <= len(setores):
            await sessao_set(telefone, aguardando=None, midias_pendentes=[])
            await escolher_setor(c, setores[numero - 1]["id"], [], minutos=24 * 60)
            return

    # ── número do menu ──
    sessao_viva = s.get("menu_atual_id") and s.get("ultima_interacao") and \
        agora() - s["ultima_interacao"] < timedelta(minutes=await par_int("sessao_minutos", 30))
    menu_id = s.get("menu_atual_id") if sessao_viva else (menu["id"] if menu else None)
    ops_atual = await opcoes(menu_id) if menu_id and menu_id != (menu or {}).get("id") else ops
    if numero is not None:
        op = next((o for o in ops_atual if o["numero"] == numero), None)
        if op:
            if op["tipo"] == "setor" and op["setor_id"]:
                await sessao_set(telefone, aguardando=None, menu_atual_id=None)
                await escolher_setor(c, op["setor_id"], [])
            elif op["tipo"] == "submenu" and op["submenu_id"]:
                sub = await opcoes(op["submenu_id"])
                await sessao_set(telefone, menu_atual_id=op["submenu_id"])
                await enviar_bot(telefone, f"{op['rotulo']}\n\n{texto_menu(sub)}\n\n0 - Voltar")
            else:
                await sessao_set(telefone, menu_atual_id=menu["id"] if menu else None)
                await enviar_bot(telefone, (op["resposta"] or "") + "\n\n" + await par("rodape"))
            return
        if numero == 0 and sessao_viva and menu and menu_id != menu["id"]:
            await sessao_set(telefone, menu_atual_id=menu["id"])
            await enviar_bot(telefone, preencher(await par("saudacao"), saudacao=saudacao_hora(), nome=nome, menu=texto_menu(ops)))
            return
        if sessao_viva:
            await enviar_bot(telefone, await par("opcao_invalida") + "\n\n" + texto_menu(ops_atual))
            return

    # ── qualquer outra coisa: saudação + menu ──
    await sessao_set(telefone, aguardando=None, menu_atual_id=menu["id"] if menu else None)
    await enviar_bot(telefone, preencher(await par("saudacao"), saudacao=saudacao_hora(), nome=nome, menu=texto_menu(ops)))


async def escolher_setor(c, setor_id, abertos, minutos=30):
    """Colaborador escolheu um setor. Já tem atendimento aberto nele → volta para ele (vira o ativo).
    Senão abre um novo — os de outros setores CONTINUAM abertos, cada um na lista da sua atendente."""
    telefone = c["telefone"]
    existente = next((a for a in abertos if a["setor_id"] == setor_id), None)
    if existente:
        await anexar_soltas(telefone, existente["id"], minutos)
        await marcar_ativo(telefone, existente["id"])
        atend = primeiro_nome(existente.get("atendente_nome")) or existente["setor_nome"]
        await enviar_bot(telefone, preencher(await par("abertura_setor"), atendente=atend, setor=existente["nome_msg"]), existente["id"])
        await avisar_nova_msg(existente, c["nome"], "voltou a chamar pelo menu")
        return
    at = await abrir_atendimento(telefone, setor_id, anexar_desde_min=minutos)
    if abertos:
        outros = " e ".join(f"{primeiro_nome(a.get('atendente_nome')) or a['setor_nome']} ({a['setor_nome']})" for a in abertos)
        await enviar_bot(telefone, preencher(await par("paralelo"), outros=outros), at["id"])
    atend = primeiro_nome(await _nome_usuario(at["atribuido_a"])) or at["setor_nome"]
    await enviar_bot(telefone, preencher(await par("abertura_setor"), atendente=atend, setor=at["nome_msg"]), at["id"])
    if at["fora_horario"]:
        await enviar_bot(c["telefone"], preencher(await par("fora_horario"), horario=await par("horario_texto")), at["id"])
    await _avisar("atendimento_novo", at["id"], f"💬 Nova conversa: {c['nome']}", f"{at['setor_nome']} — pelo menu do WhatsApp",
                  at["atribuido_a"])


# ══════════════════════════════════════════════════════════════
# MÍDIA RECEBIDA → Supabase (garra-fotos:whatsapp/…)
# ══════════════════════════════════════════════════════════════
EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "video/mp4": "mp4", "audio/ogg": "ogg",
       "audio/mpeg": "mp3", "audio/mp4": "m4a", "application/pdf": "pdf"}


def extensao(mimetype, nome=None):
    if nome and "." in nome:
        return nome.rsplit(".", 1)[1].lower()[:5]
    base = (mimetype or "").split(";")[0].strip()
    return EXT.get(base) or (base.split("/")[-1][:5] if "/" in base else "bin")


async def baixar_e_guardar(row):
    try:
        dados, mime = await P.baixar_midia(row["provider_msg_id"])
        if not dados:
            return
        from core.storage import storage_upload
        mime = mime or row.get("mimetype") or "application/octet-stream"
        caminho = f"whatsapp/{agora().astimezone(FUSO):%Y-%m}/{row['telefone']}/{row['id']}.{extensao(mime, row.get('nome_arquivo'))}"
        salvo = await asyncio.to_thread(storage_upload, dados, caminho, mime.split(";")[0])
        await q("UPDATE whatsapp.mensagens SET midia_caminho=%s, mimetype=%s, atualizado_em=now() WHERE id=%s",
                          (salvo, mime, row["id"]), fetch="none")
    except Exception as e:
        log.warning("mídia %s: %s", row.get("provider_msg_id"), e)
        await q("UPDATE whatsapp.mensagens SET erro=%s, atualizado_em=now() WHERE id=%s",
                          (f"mídia: {e}"[:300], row["id"]), fetch="none")
