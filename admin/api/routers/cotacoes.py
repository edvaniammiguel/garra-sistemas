"""routers.cotacoes — Cotação de compras (12/11/2026)

UMA cotação, N fornecedores. Nasce das mesmas origens da OC (mínimo, OT, 🛒 Comprar),
vive no estágio "A cotar" da vista Pedidos de compra e, ao ser ENCERRADA, gera uma OC
por fornecedor vencedor (compras.ordens_compra + cotacao_id) e grava preço/prazo de
TODOS os que responderam em manutencao.peca_fornecedores — a próxima cotação já abre
preenchida.

Sem data-limite (decisão 12/11): o prazo prometido fica guardado e, ao receber a OC,
compara-se prometido × real → histórico de entrega do fornecedor (no mapa das próximas).

Tabelas (schema compras): cotacoes, cotacao_itens, cotacao_fornecedores, cotacao_precos.
"""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request

from core.db import ajard_query, ajard_query_id
from routers.compras import (verificar_compras, verificar_compras_solicitante, _usuario_id,
                             _criar_oc_dados, solicitar_aprovacao)

router = APIRouter()

_DDL = """
CREATE TABLE IF NOT EXISTS compras.cotacoes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    numero TEXT NOT NULL UNIQUE, ano INT NOT NULL, sequencia INT NOT NULL,
    setor_codigo TEXT, origem TEXT DEFAULT 'manual',
    ot_id UUID, equipamento_id UUID,
    status TEXT DEFAULT 'aberta',            -- aberta | encerrada | cancelada
    criterio TEXT DEFAULT 'preco',           -- preco (prazo desempata) | prazo (mais rápido, preço desempata)
    modo TEXT DEFAULT 'fornecedor',          -- fornecedor (1 vencedor) | item (vencedor por item)
    observacao TEXT,
    criado_por UUID, criado_em TIMESTAMPTZ DEFAULT now(),
    encerrada_por UUID, encerrada_em TIMESTAMPTZ,
    ativo BOOLEAN DEFAULT true
);
CREATE TABLE IF NOT EXISTS compras.cotacao_itens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cotacao_id UUID NOT NULL REFERENCES compras.cotacoes(id) ON DELETE CASCADE,
    peca_id UUID, peca_codigo TEXT, descricao TEXT NOT NULL,
    quantidade NUMERIC(14,3) NOT NULL DEFAULT 1, unidade TEXT DEFAULT 'UN', ordem INT DEFAULT 0,
    vencedor_cf_id UUID, motivo TEXT,
    ativo BOOLEAN DEFAULT true
);
CREATE TABLE IF NOT EXISTS compras.cotacao_fornecedores (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    cotacao_id UUID NOT NULL REFERENCES compras.cotacoes(id) ON DELETE CASCADE,
    fornecedor_id UUID NOT NULL,
    status TEXT DEFAULT 'aguardando',        -- aguardando | respondeu | sem_resposta
    prazo_dias INT, frete NUMERIC(14,2) DEFAULT 0, condicao_pagamento TEXT, validade DATE,
    observacao TEXT, anexo_path TEXT, respondido_em TIMESTAMPTZ,
    oc_id UUID,                              -- OC gerada para este fornecedor (vencedor)
    entregue_em TIMESTAMPTZ, dias_reais INT, -- prometido × real (preenchido ao receber a OC)
    UNIQUE (cotacao_id, fornecedor_id)
);
CREATE TABLE IF NOT EXISTS compras.cotacao_precos (
    item_id UUID NOT NULL REFERENCES compras.cotacao_itens(id) ON DELETE CASCADE,
    cf_id UUID NOT NULL REFERENCES compras.cotacao_fornecedores(id) ON DELETE CASCADE,
    preco_unit NUMERIC(14,4),                -- NULL = fornecedor não tem o item
    prazo_dias INT, observacao TEXT,
    PRIMARY KEY (item_id, cf_id)
);
ALTER TABLE compras.ordens_compra ADD COLUMN IF NOT EXISTS cotacao_id UUID;
CREATE INDEX IF NOT EXISTS ix_cotacoes_status ON compras.cotacoes(status) WHERE ativo;
-- (12/11/2026) oferta real do fornecedor por célula: igual ao pedido ou SUBSTITUTO (marca/código diferente),
-- com decisão manual aceito/recusado. Substituto sem ✔ não entra na comparação.
ALTER TABLE compras.cotacao_precos ADD COLUMN IF NOT EXISTS oferta TEXT DEFAULT 'igual';
ALTER TABLE compras.cotacao_precos ADD COLUMN IF NOT EXISTS subst_peca_codigo TEXT;
ALTER TABLE compras.cotacao_precos ADD COLUMN IF NOT EXISTS subst_descricao TEXT;
ALTER TABLE compras.cotacao_precos ADD COLUMN IF NOT EXISTS aceito BOOLEAN;
ALTER TABLE compras.cotacoes ADD COLUMN IF NOT EXISTS enviada_em TIMESTAMPTZ;
ALTER TABLE compras.cotacoes ADD COLUMN IF NOT EXISTS cotacao_origem_id UUID;
-- (12/11/2026) itens que ficaram sem compra ao encerrar (sem oferta aceita) — baixa MANUAL
CREATE TABLE IF NOT EXISTS compras.pendencias_compra (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    peca_id UUID, peca_codigo TEXT, descricao TEXT NOT NULL,
    quantidade NUMERIC(14,3) DEFAULT 1, unidade TEXT DEFAULT 'UN',
    setor_codigo TEXT, ot_id UUID, equipamento_id UUID,
    origem_tipo TEXT DEFAULT 'cotacao', cotacao_id UUID, cotacao_numero TEXT,
    status TEXT DEFAULT 'aberta',            -- aberta | resolvida
    criado_em TIMESTAMPTZ DEFAULT now(), criado_por UUID,
    resolvido_em TIMESTAMPTZ, resolvido_por UUID, motivo TEXT, resolvido_ref TEXT
);
"""
_pronto = False


async def _garantir():
    global _pronto
    if _pronto:
        return
    for sql in _DDL.split(";"):
        if sql.strip():
            await ajard_query(sql, fetch="none")
    _pronto = True


def _f(v):
    return float(v) if v is not None else None


def _obs_cel(precos, item_id, cf_id):
    """Observação lançada na célula item × fornecedor (ex.: 'substituto: marca X'), ignorando o aviso de pré-preenchimento."""
    for p in precos:
        if str(p["item_id"]) == str(item_id) and str(p["cf_id"]) == str(cf_id):
            o = (p.get("observacao") or "").strip()
            return "" if o.startswith("último preço conhecido") else o
    return ""


# ── Histórico de entrega do fornecedor (prometido × real) ──────────────────
async def _historico_entrega(fornecedor_ids):
    """Para cada fornecedor: cotações vencidas com entrega registrada → % no prazo e atraso médio.
    Fonte: cotacao_fornecedores.dias_reais × prazo_dias (preenchido por registrar_entrega)."""
    if not fornecedor_ids:
        return {}
    rows = await ajard_query(
        """SELECT fornecedor_id, COUNT(*)::int AS n,
                  SUM(CASE WHEN dias_reais <= COALESCE(prazo_dias, dias_reais) THEN 1 ELSE 0 END)::int AS no_prazo,
                  AVG(GREATEST(dias_reais - COALESCE(prazo_dias, dias_reais), 0))::numeric(8,1) AS atraso_medio
             FROM compras.cotacao_fornecedores
            WHERE fornecedor_id = ANY(%s) AND dias_reais IS NOT NULL
            GROUP BY fornecedor_id""", (list(fornecedor_ids),)) or []
    return {str(r["fornecedor_id"]): {"n": r["n"], "no_prazo": r["no_prazo"], "atraso_medio": _f(r["atraso_medio"])} for r in rows}


# ── Cálculo da sugestão (preço × prazo) ────────────────────────────────────
def _oferta_conta(p):
    """(12/11/2026) Uma célula entra na comparação se tem preço e: é igual ao pedido, ou é substituto ACEITO."""
    if p.get("preco_unit") is None:
        return False
    if (p.get("oferta") or "igual") == "substituto":
        return p.get("aceito") is True
    return True


def _tabela_valida(precos):
    return {(str(p["item_id"]), str(p["cf_id"])): float(p["preco_unit"]) for p in precos if _oferta_conta(p)}


def _sugerir(cot, itens, forns, precos):
    """Devolve {por_item: {item_id: cf_id}, por_fornecedor: cf_id|None, totais: {cf_id: {...}}}.
    criterio 'preco': menor custo total (preço×qtd + frete), prazo desempata.
    criterio 'prazo': menor prazo, custo desempata. Fornecedor que não cotou TODOS os itens
    não concorre no modo 'por fornecedor' (fica 'incompleto')."""
    respondeu = [f for f in forns if f["status"] == "respondeu"]
    tabela = _tabela_valida(precos)   # (item_id, cf_id) -> preco, só ofertas que contam
    chave = (lambda custo, prazo: (custo, prazo if prazo is not None else 9999)) if cot["criterio"] != "prazo" \
        else (lambda custo, prazo: (prazo if prazo is not None else 9999, custo))
    por_item = {}
    for it in itens:
        cands = []
        for f in respondeu:
            pu = tabela.get((str(it["id"]), str(f["id"])))
            if pu is None:
                continue
            cands.append((chave(pu * float(it["quantidade"]), f.get("prazo_dias")), str(f["id"])))
        if cands:
            por_item[str(it["id"])] = min(cands)[1]
    totais, melhor = {}, None
    for f in respondeu:
        custo, completo = float(f.get("frete") or 0), True
        for it in itens:
            pu = tabela.get((str(it["id"]), str(f["id"])))
            if pu is None:
                completo = False
                continue
            custo += pu * float(it["quantidade"])
        cobre = sum(1 for it in itens if (str(it["id"]), str(f["id"])) in tabela)
        totais[str(f["id"])] = {"custo": round(custo, 2), "completo": completo, "prazo_dias": f.get("prazo_dias"), "cobre": cobre}
        if completo:
            k = chave(custo, f.get("prazo_dias"))
            if melhor is None or k < melhor[0]:
                melhor = (k, str(f["id"]))
    # (12/11/2026) ninguém cobre tudo → sugere o PARCIAL que cobre mais itens (os demais viram pendência ao encerrar)
    parcial = False
    if melhor is None:
        cands = [(-t["cobre"], chave(t["custo"], t["prazo_dias"]), cf) for cf, t in totais.items() if t["cobre"] > 0]
        if cands:
            melhor = (None, min(cands)[2]); parcial = True
    # custo do modo "por item" (soma dos melhores + um frete por fornecedor envolvido)
    custo_item, fretes = 0.0, set()
    for it in itens:
        cf = por_item.get(str(it["id"]))
        if cf:
            custo_item += tabela[(str(it["id"]), cf)] * float(it["quantidade"])
            fretes.add(cf)
    for cf in fretes:
        f = next((x for x in respondeu if str(x["id"]) == cf), None)
        custo_item += float((f or {}).get("frete") or 0)
    return {"por_item": por_item, "por_fornecedor": melhor[1] if melhor else None, "por_fornecedor_parcial": parcial, "totais": totais,
            "custo_por_item": round(custo_item, 2) if por_item else None, "fornecedores_por_item": len(fretes),
            "itens_sem_cotacao": [str(it["id"]) for it in itens if str(it["id"]) not in por_item]}


async def _carregar(cid):
    cot = await ajard_query(
        """SELECT c.*, se.nome AS setor_nome, ot.numero AS ot_numero, eq.codigo AS equipamento_codigo,
                  u.nome AS criado_por_nome
             FROM compras.cotacoes c
             LEFT JOIN compras.setores se ON se.codigo = c.setor_codigo
             LEFT JOIN manutencao.ot ot ON ot.id = c.ot_id
             LEFT JOIN operacional.equipamentos eq ON eq.id = c.equipamento_id
             LEFT JOIN public.usuarios_garra u ON u.id = c.criado_por
            WHERE c.id=%s AND c.ativo=true""", (cid,), fetch="one")
    if not cot:
        raise HTTPException(404, "Cotação não encontrada")
    itens = await ajard_query(
        "SELECT * FROM compras.cotacao_itens WHERE cotacao_id=%s AND ativo=true ORDER BY ordem, descricao", (cid,)) or []
    forns = await ajard_query(
        """SELECT cf.*, f.nome AS fornecedor_nome, f.telefone AS fornecedor_telefone,
                  oc.numero AS oc_numero, oc.status AS oc_status
             FROM compras.cotacao_fornecedores cf
             JOIN public.fornecedores f ON f.id = cf.fornecedor_id
             LEFT JOIN compras.ordens_compra oc ON oc.id = cf.oc_id
            WHERE cf.cotacao_id=%s ORDER BY f.nome""", (cid,)) or []
    precos = await ajard_query(
        """SELECT p.* FROM compras.cotacao_precos p
             JOIN compras.cotacao_itens i ON i.id = p.item_id WHERE i.cotacao_id=%s""", (cid,)) or []
    return dict(cot), [dict(i) for i in itens], [dict(f) for f in forns], [dict(p) for p in precos]


def _prefill(p):
    """(12/11/2026) célula só com o 'último preço conhecido' sugerido pelo sistema — ainda não é resposta do fornecedor."""
    return (p.get("observacao") or "").startswith("último preço conhecido")


def _fase(cot, forns, precos):
    """(12/11/2026) Fase da cotação aberta — a tela só cobra resposta depois que alguém respondeu.
    montando → enviada (📨 pedido de cotação enviado/copiado) → respondida (algum preço ou situação ≠ aguardando).
    Encerrada/cancelada: a própria situação."""
    if cot["status"] != "aberta":
        return cot["status"]
    if any(f.get("status") and f["status"] != "aguardando" for f in forns) or any(p.get("preco_unit") is not None and not _prefill(p) for p in precos):
        return "respondida"
    return "enviada" if cot.get("enviada_em") else "montando"


def _saida(cot, itens, forns, precos, hist):
    sug = _sugerir(cot, itens, forns, precos)
    return {
        **{k: (str(v) if k.endswith("_id") or k == "id" else v) for k, v in cot.items()},
        "fase": _fase(cot, forns, precos),
        "itens": [{**{k: (str(v) if k in ("id", "cotacao_id", "peca_id", "vencedor_cf_id") and v is not None else v) for k, v in i.items()},
                   "quantidade": _f(i["quantidade"])} for i in itens],
        "fornecedores": [{**{k: (str(v) if k in ("id", "cotacao_id", "fornecedor_id", "oc_id") and v is not None else v) for k, v in f.items()},
                          "frete": _f(f.get("frete")), "historico": hist.get(str(f["fornecedor_id"]))} for f in forns],
        "precos": [{"item_id": str(p["item_id"]), "cf_id": str(p["cf_id"]), "preco_unit": _f(p["preco_unit"]),
                    "prazo_dias": p.get("prazo_dias"), "observacao": p.get("observacao"),
                    "oferta": p.get("oferta") or "igual", "subst_peca_codigo": p.get("subst_peca_codigo"),
                    "subst_descricao": p.get("subst_descricao"), "aceito": p.get("aceito"), "conta": _oferta_conta(p)} for p in precos],
        "sem_oferta": [str(it["id"]) for it in itens if not any(_oferta_conta(p) and str(p["item_id"]) == str(it["id"]) for p in precos)],
        "sugestao": sug,
    }


# ── Endpoints ──────────────────────────────────────────────────────────────
@router.get("/compras/api/cotacoes")
async def listar(status: str = "aberta", setor: str = None, busca: str = None, _auth=Depends(verificar_compras)):
    await _garantir()
    where, params = ["c.ativo=true"], []
    if status and status != "todas":
        where.append("c.status=%s"); params.append(status)
    if setor:
        where.append("c.setor_codigo=%s"); params.append(setor.upper())
    if busca and busca.strip():
        b = f"%{busca.strip()}%"
        where.append("(c.numero ILIKE %s OR ot.numero ILIKE %s OR eq.codigo ILIKE %s OR EXISTS (SELECT 1 FROM compras.cotacao_itens i "
                     "WHERE i.cotacao_id=c.id AND i.ativo AND (i.descricao ILIKE %s OR i.peca_codigo ILIKE %s)) "
                     "OR EXISTS (SELECT 1 FROM compras.cotacao_fornecedores cf JOIN public.fornecedores f ON f.id=cf.fornecedor_id WHERE cf.cotacao_id=c.id AND f.nome ILIKE %s))")
        params += [b, b, b, b, b, b]
    rows = await ajard_query(
        f"""SELECT c.id, c.numero, c.status, c.origem, c.criterio, c.modo, c.criado_em, c.encerrada_em, c.enviada_em, c.setor_codigo,
                   ot.numero AS ot_numero, eq.codigo AS equipamento_codigo, u.nome AS criado_por_nome,
                   (SELECT string_agg(trim(to_char(i.quantidade,'FM9999990.##'))||' '||COALESCE(i.unidade,'UN')||' '||i.descricao, ' · ' ORDER BY i.ordem)
                      FROM compras.cotacao_itens i WHERE i.cotacao_id=c.id AND i.ativo) AS itens_resumo,
                   (SELECT COUNT(*)::int FROM compras.cotacao_fornecedores cf WHERE cf.cotacao_id=c.id) AS forn_n,
                   (SELECT COUNT(*)::int FROM compras.cotacao_fornecedores cf WHERE cf.cotacao_id=c.id AND cf.status='respondeu') AS forn_resp,
                   (SELECT string_agg(oc.numero, ', ') FROM compras.ordens_compra oc WHERE oc.cotacao_id=c.id AND oc.ativo) AS ocs,
                   (SELECT co.numero FROM compras.cotacoes co WHERE co.id=c.cotacao_origem_id) AS origem_numero,
                   (SELECT COUNT(*)::int FROM compras.pendencias_compra pc WHERE pc.cotacao_id=c.id AND pc.status='aberta') AS pendencias
              FROM compras.cotacoes c
              LEFT JOIN manutencao.ot ot ON ot.id = c.ot_id
              LEFT JOIN operacional.equipamentos eq ON eq.id = c.equipamento_id
              LEFT JOIN public.usuarios_garra u ON u.id = c.criado_por
             WHERE {' AND '.join(where)} ORDER BY c.criado_em DESC LIMIT 300""", params) or []
    out = []
    for r in rows:
        d = dict(r); d["id"] = str(d["id"])
        # (12/11/2026) fase para a lista (mesma regra de _fase, sem carregar os preços: forn_resp já conta quem respondeu)
        d["fase"] = d["status"] if d["status"] != "aberta" else ("respondida" if d["forn_resp"] else ("enviada" if d.get("enviada_em") else "montando"))
        out.append(d)
    return out


@router.post("/compras/api/cotacoes")
async def criar(request: Request, payload=Depends(verificar_compras_solicitante)):
    """Body: {setor_codigo, origem, ot_id?, equipamento_id?, observacao?,
              itens: [{peca_id?, peca_codigo?, descricao, quantidade, unidade}], fornecedores?: [fornecedor_id]}
    Fornecedores não informados → sugeridos pela referência das peças e por quem já vendeu."""
    await _garantir()
    d = await request.json()
    itens = [i for i in (d.get("itens") or []) if (i.get("descricao") or "").strip()]
    # (12/11/2026) cotação pode nascer vazia (itens entram pelo ＋ item do mapa); encerrar sem item é que não pode
    setor = (d.get("setor_codigo") or "").strip().upper() or None
    seq = await ajard_query("SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM compras.cotacoes WHERE ano=%s", (date.today().year,), fetch="one")
    numero = f"COT-{date.today().year}-{int(seq['n']):04d}"
    uid = await _usuario_id(payload)
    row = await ajard_query_id(
        # (13/11/2026) modo padrão = 'item' (melhor de cada): na prática a compra quase sempre sai dividida
        # entre fornecedores. Com 1 fornecedor só dá no mesmo (1 OC); 'por fornecedor' continua no seletor.
        """INSERT INTO compras.cotacoes (numero, ano, sequencia, setor_codigo, origem, ot_id, equipamento_id, observacao, criado_por, modo)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'item')""",
        (numero, date.today().year, int(seq["n"]), setor, (d.get("origem") or "manual"), d.get("ot_id") or None,
         d.get("equipamento_id") or None, d.get("observacao"), uid))
    cid = row["id"]
    codigos = []
    for n, it in enumerate(itens):
        pid, cod = it.get("peca_id") or None, (it.get("peca_codigo") or "").strip() or None
        if cod and not pid:
            p = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s", (cod,), fetch="one")
            pid = p["id"] if p else None
        if pid and not cod:
            p = await ajard_query("SELECT codigo FROM manutencao.pecas WHERE id=%s", (pid,), fetch="one")
            cod = p["codigo"] if p else None
        if cod:
            codigos.append(cod)
        await ajard_query(
            "INSERT INTO compras.cotacao_itens (cotacao_id, peca_id, peca_codigo, descricao, quantidade, unidade, ordem) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (cid, pid, cod, it["descricao"].strip(), float(it.get("quantidade") or 1), (it.get("unidade") or "UN"), n), fetch="none")
    # fornecedores: informados ou sugeridos (referência + quem já vendeu as peças)
    fids = [f for f in (d.get("fornecedores") or []) if f]
    if not fids and codigos:
        rows = await ajard_query(
            """SELECT DISTINCT fornecedor_id FROM manutencao.peca_fornecedores
                WHERE peca_codigo = ANY(%s) AND ativo=true AND fornecedor_id IS NOT NULL""", (codigos,)) or []
        fids = [str(r["fornecedor_id"]) for r in rows]
    for fid in dict.fromkeys(fids):
        await ajard_query("INSERT INTO compras.cotacao_fornecedores (cotacao_id, fornecedor_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                          (cid, fid), fetch="none")
    # pré-preenche preços conhecidos (última referência de cada fornecedor) — a cotação "já abre preenchida"
    await _prefill(cid)
    return await ver(str(cid), payload)


async def _prefill(cid):
    await ajard_query(
        """INSERT INTO compras.cotacao_precos (item_id, cf_id, preco_unit, prazo_dias, observacao)
           SELECT i.id, cf.id, r.preco, r.prazo_dias, 'último preço conhecido — confirme'
             FROM compras.cotacao_itens i
             JOIN compras.cotacao_fornecedores cf ON cf.cotacao_id = i.cotacao_id
             JOIN manutencao.peca_fornecedores r ON r.peca_codigo = i.peca_codigo AND r.fornecedor_id = cf.fornecedor_id AND r.ativo AND r.preco IS NOT NULL
            WHERE i.cotacao_id=%s AND i.ativo
           ON CONFLICT DO NOTHING""", (cid,), fetch="none")


@router.get("/compras/api/cotacoes/{cid}")
async def ver(cid: str, _auth=Depends(verificar_compras)):
    await _garantir()
    cot, itens, forns, precos = await _carregar(cid)
    hist = await _historico_entrega([f["fornecedor_id"] for f in forns])
    return _saida(cot, itens, forns, precos, hist)


@router.put("/compras/api/cotacoes/{cid}")
async def editar(cid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    await _garantir()
    d = await request.json()
    sets, params = [], []
    if d.get("criterio") in ("preco", "prazo"):
        sets.append("criterio=%s"); params.append(d["criterio"])
    if d.get("modo") in ("fornecedor", "item"):
        sets.append("modo=%s"); params.append(d["modo"])
    if "observacao" in d:
        sets.append("observacao=%s"); params.append(d.get("observacao"))
    if sets:
        params.append(cid)
        await ajard_query(f"UPDATE compras.cotacoes SET {', '.join(sets)} WHERE id=%s AND status='aberta'", params, fetch="none")
    return await ver(cid, payload)


@router.post("/compras/api/cotacoes/{cid}/itens")
async def item_add(cid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    await _garantir()
    d = await request.json()
    if not (d.get("descricao") or "").strip():
        raise HTTPException(400, "Item sem descrição")
    pid, cod = d.get("peca_id") or None, (d.get("peca_codigo") or "").strip() or None
    if cod and not pid:
        p = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s", (cod,), fetch="one"); pid = p["id"] if p else None
    n = await ajard_query("SELECT COALESCE(MAX(ordem),0)+1 AS n FROM compras.cotacao_itens WHERE cotacao_id=%s", (cid,), fetch="one")
    await ajard_query(
        "INSERT INTO compras.cotacao_itens (cotacao_id, peca_id, peca_codigo, descricao, quantidade, unidade, ordem) VALUES (%s,%s,%s,%s,%s,%s,%s)",
        (cid, pid, cod, d["descricao"].strip(), float(d.get("quantidade") or 1), d.get("unidade") or "UN", n["n"]), fetch="none")
    await _prefill(cid)
    return await ver(cid, payload)


@router.put("/compras/api/cotacoes/{cid}/itens/{item_id}")
async def item_editar(cid: str, item_id: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    """(12/11/2026) Quantidade do item editável no mapa (cotação aberta)."""
    d = await request.json()
    q = d.get("quantidade")
    try:
        q = float(str(q).replace(",", "."))
    except Exception:
        raise HTTPException(400, "Quantidade inválida")
    if q <= 0:
        raise HTTPException(400, "Quantidade deve ser maior que zero")
    await ajard_query(
        "UPDATE compras.cotacao_itens i SET quantidade=%s FROM compras.cotacoes c WHERE i.id=%s AND i.cotacao_id=%s AND c.id=i.cotacao_id AND c.status='aberta'",
        (q, item_id, cid), fetch="none")
    return await ver(cid, payload)


@router.delete("/compras/api/cotacoes/{cid}/itens/{item_id}")
async def item_del(cid: str, item_id: str, payload=Depends(verificar_compras_solicitante)):
    await ajard_query("UPDATE compras.cotacao_itens SET ativo=false WHERE id=%s AND cotacao_id=%s", (item_id, cid), fetch="none")
    return await ver(cid, payload)


@router.post("/compras/api/cotacoes/{cid}/fornecedores")
async def forn_add(cid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    await _garantir()
    d = await request.json()
    fid = (d.get("fornecedor_id") or "").strip()
    if not fid:
        raise HTTPException(400, "Fornecedor obrigatório")
    await ajard_query("INSERT INTO compras.cotacao_fornecedores (cotacao_id, fornecedor_id) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cid, fid), fetch="none")
    await _prefill(cid)
    return await ver(cid, payload)


@router.delete("/compras/api/cotacoes/{cid}/fornecedores/{cf_id}")
async def forn_del(cid: str, cf_id: str, payload=Depends(verificar_compras_solicitante)):
    await ajard_query("DELETE FROM compras.cotacao_fornecedores WHERE id=%s AND cotacao_id=%s AND oc_id IS NULL", (cf_id, cid), fetch="none")
    return await ver(cid, payload)


@router.put("/compras/api/cotacoes/{cid}/fornecedores/{cf_id}")
async def forn_resposta(cid: str, cf_id: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    """Resposta do fornecedor: {status?, prazo_dias, frete, condicao_pagamento, validade, observacao, anexo_path,
       precos: [{item_id, preco_unit|null, prazo_dias?, observacao?}]}. Qualquer preço lançado marca 'respondeu'."""
    await _garantir()
    d = await request.json()
    status = d.get("status")
    if "precos" in d:
        for p in d["precos"] or []:
            pu = p.get("preco_unit")
            pu = float(str(pu).replace(",", ".")) if pu not in (None, "") else None
            if pu is not None and pu < 0:
                raise HTTPException(400, "Preço não pode ser negativo")
            oferta = p.get("oferta") or "igual"
            if oferta not in ("igual", "substituto"):
                oferta = "igual"
            sub_cod = (p.get("subst_peca_codigo") or "").strip() or None
            if sub_cod:
                ok = await ajard_query("SELECT 1 FROM manutencao.pecas WHERE codigo=%s", (sub_cod,), fetch="one")
                if not ok:
                    raise HTTPException(400, f"Peça substituta '{sub_cod}' não existe no catálogo — cadastre antes ou deixe só a descrição")
            aceito = p.get("aceito") if oferta == "substituto" else None
            await ajard_query(
                """INSERT INTO compras.cotacao_precos (item_id, cf_id, preco_unit, prazo_dias, observacao, oferta, subst_peca_codigo, subst_descricao, aceito)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (item_id, cf_id) DO UPDATE SET preco_unit=EXCLUDED.preco_unit, prazo_dias=EXCLUDED.prazo_dias, observacao=EXCLUDED.observacao,
                     oferta=EXCLUDED.oferta, subst_peca_codigo=EXCLUDED.subst_peca_codigo, subst_descricao=EXCLUDED.subst_descricao, aceito=EXCLUDED.aceito""",
                (p["item_id"], cf_id, pu, p.get("prazo_dias") or None, p.get("observacao"), oferta, sub_cod,
                 (p.get("subst_descricao") or "").strip() or None, aceito), fetch="none")
        # (12/11/2026) preço PRÉ-PREENCHIDO (último conhecido, ainda não confirmado) não vale como resposta
        if status is None and any(p.get("preco_unit") not in (None, "") and not _prefill(p) for p in (d["precos"] or [])):
            status = "respondeu"
    sets, params = [], []
    for k in ("prazo_dias", "frete", "condicao_pagamento", "validade", "observacao", "anexo_path"):
        if k in d:
            v = d[k]
            if k in ("prazo_dias",) and v not in (None, ""):
                v = int(v)
            if k == "frete":
                v = float(str(v).replace(",", ".")) if v not in (None, "") else 0
            if v == "":
                v = None
            sets.append(f"{k}=%s"); params.append(v)
    if status in ("aguardando", "respondeu", "sem_resposta"):
        sets.append("status=%s"); params.append(status)
        if status == "respondeu":
            sets.append("respondido_em=COALESCE(respondido_em, now())")
    if sets:
        params += [cf_id, cid]
        await ajard_query(f"UPDATE compras.cotacao_fornecedores SET {', '.join(sets)} WHERE id=%s AND cotacao_id=%s", params, fetch="none")
    return await ver(cid, payload)


@router.put("/compras/api/cotacoes/{cid}/celula")
async def celula_decidir(cid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    """(12/11/2026) Decisão manual sobre um substituto: {item_id, cf_id, aceito: true|false|null}."""
    d = await request.json()
    aceito = d.get("aceito")
    await ajard_query("UPDATE compras.cotacao_precos SET aceito=%s WHERE item_id=%s AND cf_id=%s AND oferta='substituto'",
                      (aceito, d.get("item_id"), d.get("cf_id")), fetch="none")
    return await ver(cid, payload)


@router.post("/compras/api/cotacoes/{cid}/saldo")
async def cotacao_saldo(cid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    """(12/11/2026) Itens sem oferta aceita → NOVA cotação ("saldo da COT-x"), vinculada à original.
    Body: {itens: [item_id…]}. Os itens saem da cotação atual (ativo=false, motivo) e entram na nova."""
    await _garantir()
    d = await request.json()
    cot, itens, forns, precos = await _carregar(cid)
    if cot["status"] != "aberta":
        raise HTTPException(400, "Cotação não está aberta")
    ids = set(str(i) for i in (d.get("itens") or []))
    mover = [i for i in itens if str(i["id"]) in ids]
    if not mover:
        raise HTTPException(400, "Nenhum item informado")
    seq = await ajard_query("SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM compras.cotacoes WHERE ano=%s", (date.today().year,), fetch="one")
    numero = f"COT-{date.today().year}-{int(seq['n']):04d}"
    uid = await _usuario_id(payload)
    row = await ajard_query_id(
        """INSERT INTO compras.cotacoes (numero, ano, sequencia, setor_codigo, origem, ot_id, equipamento_id, observacao, criado_por, cotacao_origem_id, modo)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'item')""",
        (numero, date.today().year, int(seq["n"]), cot.get("setor_codigo"), "saldo", cot.get("ot_id"), cot.get("equipamento_id"),
         f"Saldo da {cot['numero']}: itens sem oferta aceita", uid, cid))
    nid = row["id"]
    for n, i in enumerate(mover):
        await ajard_query(
            "INSERT INTO compras.cotacao_itens (cotacao_id, peca_id, peca_codigo, descricao, quantidade, unidade, ordem) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (nid, i.get("peca_id"), i.get("peca_codigo"), i["descricao"], float(i["quantidade"]), i.get("unidade") or "UN", n), fetch="none")
        await ajard_query("UPDATE compras.cotacao_itens SET ativo=false, motivo=%s WHERE id=%s", (f"levado para {numero}", i["id"]), fetch="none")
    await _prefill(nid)
    return {"ok": True, "id": str(nid), "numero": numero}


@router.get("/compras/api/pendencias")
async def pendencias_listar(status: str = "aberta", setor: str = None, _auth=Depends(verificar_compras)):
    """(12/11/2026) Itens que ficaram sem compra (baixa manual com motivo)."""
    await _garantir()
    where, params = [], []
    if status != "todas":
        where.append("p.status=%s"); params.append(status)
    if setor:
        where.append("p.setor_codigo=%s"); params.append(setor.upper())
    rows = await ajard_query(
        f"""SELECT p.*, ot.numero AS ot_numero, eq.codigo AS equipamento_codigo, u.nome AS resolvido_por_nome
              FROM compras.pendencias_compra p
              LEFT JOIN manutencao.ot ot ON ot.id = p.ot_id
              LEFT JOIN operacional.equipamentos eq ON eq.id = p.equipamento_id
              LEFT JOIN public.usuarios_garra u ON u.id = p.resolvido_por
             {('WHERE ' + ' AND '.join(where)) if where else ''} ORDER BY p.criado_em DESC LIMIT 300""", params) or []
    out = []
    for r in rows:
        x = {k: (str(v) if k in ("id", "peca_id", "ot_id", "equipamento_id", "cotacao_id", "criado_por", "resolvido_por") and v is not None else v) for k, v in dict(r).items()}
        x["quantidade"] = _f(r["quantidade"]); out.append(x)
    return out


@router.post("/compras/api/pendencias/{pid}/resolver")
async def pendencia_resolver(pid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    """Baixa manual: {motivo, ref?} (ref = nº da OC/cotação que resolveu, se houver)."""
    d = await request.json()
    motivo = (d.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(400, "Informe o motivo da baixa (ex.: comprado na OC-2026-0091; não precisa mais)")
    uid = await _usuario_id(payload)
    await ajard_query("UPDATE compras.pendencias_compra SET status='resolvida', resolvido_em=now(), resolvido_por=%s, motivo=%s, resolvido_ref=%s WHERE id=%s AND status='aberta'",
                      (uid, motivo, (d.get("ref") or "").strip() or None, pid), fetch="none")
    return {"ok": True}


@router.post("/compras/api/pendencias/{pid}/reabrir")
async def pendencia_reabrir(pid: str, payload=Depends(verificar_compras_solicitante)):
    await ajard_query("UPDATE compras.pendencias_compra SET status='aberta', resolvido_em=NULL, resolvido_por=NULL, motivo=NULL, resolvido_ref=NULL WHERE id=%s", (pid,), fetch="none")
    return {"ok": True}


@router.get("/compras/api/cotacoes/{cid}/texto")
async def texto_pedido(cid: str, _auth=Depends(verificar_compras)):
    """Texto único do pedido de cotação (o mesmo para todos os fornecedores) — WhatsApp / e-mail."""
    cot, itens, forns, _ = await _carregar(cid)
    nome = "Garra Terraplenagem e Caçambas"
    linhas = [f"*Pedido de cotação {cot['numero']}* — {nome}", ""]
    linhas += [f"• {float(i['quantidade']):g} {i.get('unidade') or 'UN'} — {i['descricao']}" + (f" (cód. {i['peca_codigo']})" if i.get('peca_codigo') else "") for i in itens]
    linhas += ["", "Por favor informar: *preço unitário* de cada item, *prazo de entrega* (dias), *frete* e *condição de pagamento*.",
               "Se não tiver algum item, indicar.", "Obrigado!"]
    return {"texto": "\n".join(linhas), "fornecedores": [{"nome": f["fornecedor_nome"], "whatsapp": f.get("fornecedor_telefone")} for f in forns]}


@router.post("/compras/api/cotacoes/{cid}/encerrar")
async def encerrar(cid: str, request: Request, payload=Depends(verificar_compras_solicitante)):
    """Body: {modo: 'fornecedor'|'item', vencedores: {item_id: cf_id} (modo item) | {'*': cf_id} (modo fornecedor),
              motivos?: {item_id: texto}, solicitar: bool}
    Gera 1 OC por fornecedor vencedor; grava preço/prazo de TODOS que responderam em peca_fornecedores."""
    await _garantir()
    d = await request.json()
    cot, itens, forns, precos = await _carregar(cid)
    if cot["status"] != "aberta":
        raise HTTPException(400, f"Cotação já {cot['status']}")
    if not itens:
        raise HTTPException(400, "Cotação sem itens — acrescente ao menos um antes de encerrar")
    modo = d.get("modo") or cot.get("modo") or "fornecedor"
    venc = d.get("vencedores") or {}
    sug = _sugerir(cot, itens, forns, precos)
    tabela = _tabela_valida(precos)   # só ofertas que contam (igual, ou substituto aceito)
    # resolve vencedor por item; (12/11/2026) item sem oferta aceita NÃO bloqueia: vira pendência (se o front confirmou)
    por_item, pendentes = {}, []
    if modo == "fornecedor":
        cf = venc.get("*") or sug["por_fornecedor"]
        if not cf:
            raise HTTPException(400, "Nenhum fornecedor com oferta aceita — lance os preços ou aceite um substituto")
        for it in itens:
            if (str(it["id"]), cf) in tabela:
                por_item[str(it["id"])] = cf
            else:
                pendentes.append(it)
    else:
        for it in itens:
            cf = venc.get(str(it["id"])) or sug["por_item"].get(str(it["id"]))
            if cf and (str(it["id"]), cf) in tabela:
                por_item[str(it["id"])] = cf
            else:
                pendentes.append(it)
    if pendentes and not d.get("aceitar_pendencias"):
        raise HTTPException(409, "Há itens sem oferta aceita: " + ", ".join(i["descricao"] for i in pendentes)
                            + ". Confirme para encerrar mesmo assim (eles vão para Pendentes de compra).")
    if not por_item:
        raise HTTPException(400, "Nenhum item com oferta aceita — nada para comprar")
    uid = await _usuario_id(payload)
    motivos = d.get("motivos") or {}
    cel = {(str(p["item_id"]), str(p["cf_id"])): p for p in precos}
    # agrupa por fornecedor → OC
    por_cf = {}
    for it in itens:
        if str(it["id"]) in por_item:
            por_cf.setdefault(por_item[str(it["id"])], []).append(it)
    ocs = []
    for cf_id, lista in por_cf.items():
        f = next(x for x in forns if str(x["id"]) == cf_id)
        dados = {
            "setor_codigo": cot.get("setor_codigo"), "fornecedor_id": str(f["fornecedor_id"]),
            "ot_id": str(cot["ot_id"]) if cot.get("ot_id") else None,
            "equipamento_id": str(cot["equipamento_id"]) if cot.get("equipamento_id") else None,
            "condicao_pagamento": f.get("condicao_pagamento"), "origem": "cotacao",
            "observacao": f"Cotação {cot['numero']} · prazo prometido {f.get('prazo_dias') or '?'} dia(s)" + (f" · frete R$ {float(f.get('frete') or 0):.2f}" if f.get("frete") else ""),
            "itens": [await _item_oc(i, cf_id, cel, tabela, precos, cot) for i in lista],
        }
        if f.get("frete"):
            dados["itens"].append({"descricao": "Frete", "quantidade": 1, "unidade": "SV", "valor_unit": float(f["frete"])})
        oc = await _criar_oc_dados(dados, payload)
        await ajard_query("UPDATE compras.ordens_compra SET cotacao_id=%s WHERE id=%s", (cid, oc["id"]), fetch="none")
        await ajard_query("UPDATE compras.cotacao_fornecedores SET oc_id=%s WHERE id=%s", (oc["id"], cf_id), fetch="none")
        for i in lista:
            await ajard_query("UPDATE compras.cotacao_itens SET vencedor_cf_id=%s, motivo=%s WHERE id=%s",
                              (cf_id, motivos.get(str(i["id"])), i["id"]), fetch="none")
        if d.get("solicitar"):
            try:
                r = await solicitar_aprovacao(str(oc["id"]), payload)
                oc["status"] = r.get("status")
            except HTTPException as e:
                oc["aviso"] = e.detail
        ocs.append({"id": str(oc["id"]), "numero": oc["numero"], "fornecedor": f["fornecedor_nome"], "valor_total": _f(oc.get("valor_total")), "status": oc.get("status", "rascunho"), "aviso": oc.get("aviso")})
    # aprendizado: preço + prazo de TODOS que responderam (a referência continua sendo o menor preço)
    for f in forns:
        if f["status"] != "respondeu":
            continue
        for it in itens:
            pu = tabela.get((str(it["id"]), str(f["id"])))
            if pu is None:
                continue
            # (12/11/2026) substituto aceito com peça do catálogo → o preço aprende na peça SUBSTITUTA
            pc = cel.get((str(it["id"]), str(f["id"]))) or {}
            cod_aprender = pc.get("subst_peca_codigo") if (pc.get("oferta") == "substituto") else it.get("peca_codigo")
            if not cod_aprender:
                continue
            it = dict(it); it["peca_codigo"] = cod_aprender
            prazo = next((p.get("prazo_dias") for p in precos if str(p["item_id"]) == str(it["id"]) and str(p["cf_id"]) == str(f["id"]) and p.get("prazo_dias")), None) or f.get("prazo_dias")
            ex = await ajard_query("SELECT id FROM manutencao.peca_fornecedores WHERE peca_codigo=%s AND fornecedor_id=%s", (it["peca_codigo"], f["fornecedor_id"]), fetch="one")
            if ex:
                await ajard_query("UPDATE manutencao.peca_fornecedores SET preco=%s, prazo_dias=COALESCE(%s, prazo_dias), ativo=true, atualizado_em=now() WHERE id=%s",
                                  (pu, prazo, ex["id"]), fetch="none")
            else:
                await ajard_query("INSERT INTO manutencao.peca_fornecedores (peca_codigo, fornecedor_id, preco, prazo_dias, observacao) VALUES (%s,%s,%s,%s,%s)",
                                  (it["peca_codigo"], f["fornecedor_id"], pu, prazo, f"cotação {cot['numero']}"), fetch="none")
    # (12/11/2026) itens sem compra → Pendentes de compra (baixa manual)
    pend_out = []
    for it in pendentes:
        await ajard_query(
            """INSERT INTO compras.pendencias_compra (peca_id, peca_codigo, descricao, quantidade, unidade, setor_codigo, ot_id, equipamento_id, origem_tipo, cotacao_id, cotacao_numero, criado_por)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'cotacao',%s,%s,%s)""",
            (it.get("peca_id"), it.get("peca_codigo"), it["descricao"], float(it["quantidade"]), it.get("unidade") or "UN", cot.get("setor_codigo"),
             cot.get("ot_id"), cot.get("equipamento_id"), cid, cot["numero"], uid), fetch="none")
        await ajard_query("UPDATE compras.cotacao_itens SET motivo=%s WHERE id=%s", ("sem oferta aceita → Pendentes de compra", it["id"]), fetch="none")
        pend_out.append(it["descricao"])
    await ajard_query("UPDATE compras.cotacoes SET status='encerrada', modo=%s, encerrada_por=%s, encerrada_em=now() WHERE id=%s", (modo, uid, cid), fetch="none")
    return {"ok": True, "ocs": ocs, "pendentes": pend_out}


async def _item_oc(i, cf_id, cel, tabela, precos, cot):
    """(12/11/2026) Item da OC a partir da célula vencedora. Substituto aceito: a OC nasce com a PEÇA SUBSTITUTA
    (se cadastrada) e a descrição diz 'em lugar de …' — o recebimento cai na peça certa e a referência aprende."""
    p = cel.get((str(i["id"]), cf_id)) or {}
    peca_id = str(i["peca_id"]) if i.get("peca_id") else None
    desc = i["descricao"]
    if (p.get("oferta") or "igual") == "substituto":
        sub_cod, sub_desc = p.get("subst_peca_codigo"), p.get("subst_descricao") or ""
        if sub_cod:
            sp = await ajard_query("SELECT id, descricao FROM manutencao.pecas WHERE codigo=%s", (sub_cod,), fetch="one")
            if sp:
                peca_id = str(sp["id"])
                desc = f"{sub_cod} — {sp['descricao']}" + (f" ({sub_desc})" if sub_desc else "")
        elif sub_desc:
            desc = f"SUBSTITUTO: {sub_desc}"
        desc += f" · em lugar de {i.get('peca_codigo') or i['descricao']} ({cot['numero']})"
    else:
        obs = _obs_cel(precos, i["id"], cf_id)
        if obs:
            desc += f" ({obs})"
    return {"peca_id": peca_id, "descricao": desc, "quantidade": float(i["quantidade"]),
            "unidade": i.get("unidade") or "UN", "valor_unit": tabela[(str(i["id"]), cf_id)]}


@router.post("/compras/api/cotacoes/{cid}/enviar")
async def marcar_enviada(cid: str, payload=Depends(verificar_compras_solicitante)):
    """(12/11/2026) Pedido de cotação foi enviado (WhatsApp ou texto copiado): grava enviada_em na 1ª vez.
    A partir daqui a lista mostra 'aguardando respostas há N dias'."""
    await _garantir()
    await ajard_query("UPDATE compras.cotacoes SET enviada_em = COALESCE(enviada_em, now()) WHERE id=%s AND status='aberta'", (cid,), fetch="none")
    return await ver(cid, payload)


@router.post("/compras/api/cotacoes/{cid}/cancelar")
async def cancelar(cid: str, payload=Depends(verificar_compras_solicitante)):
    await ajard_query("UPDATE compras.cotacoes SET status='cancelada' WHERE id=%s AND status='aberta'", (cid,), fetch="none")
    return {"ok": True}


async def registrar_entrega(oc_id):
    """Chamado pelo Compras ao registrar recebimento: fecha prometido × real do fornecedor da cotação.
    dias_reais = do envio (ou aprovação) até o 1º recebimento."""
    try:
        await _garantir()
        await ajard_query(
            """UPDATE compras.cotacao_fornecedores cf
                  SET entregue_em = now(),
                      dias_reais = GREATEST(0, (EXTRACT(EPOCH FROM (now() - COALESCE(oc.enviado_em, oc.data_aprovacao, oc.criado_em))) / 86400)::int)
                 FROM compras.ordens_compra oc
                WHERE oc.id = %s AND cf.oc_id = oc.id AND cf.entregue_em IS NULL""", (oc_id,), fetch="none")
    except Exception:
        pass
