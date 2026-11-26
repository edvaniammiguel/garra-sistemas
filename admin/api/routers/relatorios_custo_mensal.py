"""
(26/11/2026) CUSTO MENSAL POR MÁQUINA — combustível + manutenção + pneus, mês a mês e na média.

Dois relatórios, registrados no catálogo de routers/relatorios.py (grupo "Custos"):
  custo-mensal        → uma linha por máquina e mês
  custo-medio-mensal  → uma linha por máquina, com a média mensal do período

De onde vem cada número (as mesmas regras dos outros relatórios de custo):
  combustível = Σ operacional.abastecimentos.valor_total da máquina (ativos; galão fica de fora),
                no mês da data do abastecimento (hora de Brasília)
  manutenção  = peças + mão de obra + outros das OTs da máquina (não canceladas),
                no mês em que a OT foi aberta — igual ao "Custo de manutenção por objeto"
  pneus       = a parte da manutenção que é pneu: peça da família de pneus (ou com "pneu" na
                descrição) e serviço lançado em Outros com pneu/borracharia/recapagem/vulcanização.
                Pneu NÃO entra em "manutenção": as duas colunas somam, sem contar duas vezes.

Média mensal: total ÷ meses, contando do primeiro lançamento da máquina dentro do período
até a data final (mínimo 1 mês). Assim, máquina que só começou a ser lançada há 3 meses
não tem a média diluída em 12.

Este arquivo não importa routers/relatorios.py (quem importa é ele), para não fechar um ciclo.
"""
from datetime import date as _date, datetime as _dt, timedelta as _td

from core.db import ajard_query
from core.fuso import TZ_SQL

# o que conta como pneu (%% porque a consulta passa pelo formatador de parâmetros)
_PNEU_PECA = "(p.descricao ILIKE '%%pneu%%' OR COALESCE(f.nome, '') ILIKE '%%pneu%%')"
_PNEU_SERV = ("(x.descricao ILIKE '%%pneu%%' OR x.descricao ILIKE '%%borrach%%' "
              "OR x.descricao ILIKE '%%recap%%' OR x.descricao ILIKE '%%vulcaniz%%')")
_OT_VALIDA = "o.ativo = true AND o.status <> 'cancelada' AND o.data_abertura >= %s AND o.data_abertura < %s"
_MES_OT = f"date_trunc('month', o.data_abertura AT TIME ZONE '{TZ_SQL}')::date"
_DIAS_MES = 30.4375   # 365,25 ÷ 12


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _r2(v):
    return round(_f(v), 2)


def _dia(v):
    """data (date) de um valor que pode vir como date, datetime ou texto"""
    if v is None:
        return None
    if isinstance(v, _dt):
        return v.date()
    if isinstance(v, _date):
        return v
    try:
        return _date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


async def _coletar(F):
    """{(equipamento_id, mes): {...}} e {equipamento_id: dados da máquina} — período e filtros de F."""
    periodo = (F.ini(), F.fim_excl())
    maquinas, celulas = {}, {}

    def cel(r):
        eid = str(r["id"])
        maquinas.setdefault(eid, {"objeto": r["codigo"], "descricao": r["descricao"], "sistema": r["sistema_codigo"],
                                  "centro_custo": r["centro_custo"], "unidade": "km" if r["medicao"] == "km" else "h",
                                  "desde": None})
        prim = _dia(r["primeiro"])
        if prim and (maquinas[eid]["desde"] is None or prim < maquinas[eid]["desde"]):
            maquinas[eid]["desde"] = prim
        return celulas.setdefault((eid, _dia(r["mes"])), {"combustivel": 0.0, "litros": 0.0, "abastecimentos": 0,
                                                          "manutencao": 0.0, "pneus": 0.0, "ots": 0})

    eq_cols = "e.id, e.codigo, e.descricao, e.sistema_codigo, e.centro_custo, e.medicao"

    # combustível
    params = []
    we = F.where_eq(params)
    for r in await ajard_query(f"""
        SELECT {eq_cols}, date_trunc('month', a.data AT TIME ZONE '{TZ_SQL}')::date AS mes,
               SUM(a.valor_total) AS valor, SUM(a.litros) AS litros, COUNT(*)::int AS n,
               MIN((a.data AT TIME ZONE '{TZ_SQL}')::date) AS primeiro
          FROM operacional.abastecimentos a JOIN operacional.equipamentos e ON e.id = a.equipamento_id
         WHERE a.ativo = true AND COALESCE(a.destino_tipo, 'equipamento') = 'equipamento'
           AND a.data >= %s AND a.data < %s AND {we}
         GROUP BY {eq_cols}, 7""", (*periodo, *params)):
        c = cel(r)
        c["combustivel"] += _f(r["valor"]); c["litros"] += _f(r["litros"]); c["abastecimentos"] += r["n"]

    # OTs do período (conta as OTs e marca o primeiro lançamento, mesmo a OT sem custo)
    params = []
    we = F.where_eq(params)
    for r in await ajard_query(f"""
        SELECT {eq_cols}, {_MES_OT} AS mes, COUNT(*)::int AS n,
               MIN((o.data_abertura AT TIME ZONE '{TZ_SQL}')::date) AS primeiro
          FROM manutencao.ot o JOIN operacional.equipamentos e ON e.id = o.equipamento_id
         WHERE {_OT_VALIDA} AND {we}
         GROUP BY {eq_cols}, 7""", (*periodo, *params)):
        cel(r)["ots"] += r["n"]

    # peças (pneu separado)
    params = []
    we = F.where_eq(params)
    for r in await ajard_query(f"""
        SELECT {eq_cols}, {_MES_OT} AS mes, NULL::date AS primeiro,
               SUM(CASE WHEN {_PNEU_PECA} THEN v.valor ELSE 0 END) AS pneus,
               SUM(CASE WHEN {_PNEU_PECA} THEN 0 ELSE v.valor END) AS pecas
          FROM manutencao.ot o
          JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          JOIN manutencao.movimentacoes m ON m.ot_id = o.id AND m.tipo IN ('saida', 'entrada', 'aplicacao_direta')
          JOIN manutencao.pecas p ON p.id = m.peca_id
          LEFT JOIN manutencao.familias f ON f.codigo = p.familia_codigo
          CROSS JOIN LATERAL (SELECT (CASE WHEN m.tipo = 'entrada' THEN -1 ELSE 1 END) * m.quantidade
                                     * COALESCE(m.custo_unitario, p.custo_medio, 0) AS valor) v
         WHERE {_OT_VALIDA} AND {we}
         GROUP BY {eq_cols}, 7""", (*periodo, *params)):
        c = cel(r)
        c["manutencao"] += _f(r["pecas"]); c["pneus"] += _f(r["pneus"])

    # mão de obra
    params = []
    we = F.where_eq(params)
    for r in await ajard_query(f"""
        SELECT {eq_cols}, {_MES_OT} AS mes, NULL::date AS primeiro, SUM(x.horas * COALESCE(x.custo_hora, 0)) AS valor
          FROM manutencao.ot o
          JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          JOIN manutencao.ot_mao_obra x ON x.ot_id = o.id AND x.ativo = true
         WHERE {_OT_VALIDA} AND {we}
         GROUP BY {eq_cols}, 7""", (*periodo, *params)):
        cel(r)["manutencao"] += _f(r["valor"])

    # outros serviços (borracharia e afins vão para pneus)
    params = []
    we = F.where_eq(params)
    for r in await ajard_query(f"""
        SELECT {eq_cols}, {_MES_OT} AS mes, NULL::date AS primeiro,
               SUM(CASE WHEN {_PNEU_SERV} THEN x.valor ELSE 0 END) AS pneus,
               SUM(CASE WHEN {_PNEU_SERV} THEN 0 ELSE x.valor END) AS outros
          FROM manutencao.ot o
          JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          JOIN manutencao.ot_outros x ON x.ot_id = o.id AND x.ativo = true
         WHERE {_OT_VALIDA} AND {we}
         GROUP BY {eq_cols}, 7""", (*periodo, *params)):
        c = cel(r)
        c["manutencao"] += _f(r["outros"]); c["pneus"] += _f(r["pneus"])

    return maquinas, celulas


async def rel_custo_mensal(F):
    """Uma linha por máquina e mês."""
    maquinas, celulas = await _coletar(F)
    out = []
    for (eid, mes), c in celulas.items():
        m = maquinas[eid]
        tot = c["combustivel"] + c["manutencao"] + c["pneus"]
        out.append({"objeto": m["objeto"], "descricao": m["descricao"], "sistema": m["sistema"], "mes": mes,
                    "combustivel": _r2(c["combustivel"]), "litros": _r2(c["litros"]), "abastecimentos": c["abastecimentos"],
                    "manutencao": _r2(c["manutencao"]), "pneus": _r2(c["pneus"]), "ots": c["ots"], "total": _r2(tot)})
    out.sort(key=lambda l: (l["objeto"] or "", l["mes"] or _date.min))
    return out


COL_CUSTO_MENSAL = [("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"), ("mes", "Mês", "data"),
                    ("combustivel", "Combustível (R$)", "moeda", "sum"), ("litros", "Litros", "num", "sum"),
                    ("abastecimentos", "Abastecimentos", "int", "sum"),
                    ("manutencao", "Manutenção sem pneus (R$)", "moeda", "sum"), ("pneus", "Pneus (R$)", "moeda", "sum"),
                    ("ots", "OTs", "int", "sum"), ("total", "Total (R$)", "moeda", "sum")]


async def _operadas(F):
    """horas/km rodados por máquina no período (maior leitura − menor leitura), como no custo por objeto"""
    from routers.manutencao import _garantir_leituras
    await _garantir_leituras()
    params = []
    we = F.where_eq(params)
    rows = await ajard_query(f"""
        SELECT v.equipamento_id, MAX(v.leitura) - MIN(v.leitura) AS operadas, COUNT(*)::int AS n
          FROM operacional.v_leituras v JOIN operacional.equipamentos e ON e.id = v.equipamento_id
         WHERE v.data >= %s AND v.data < %s AND {we}
         GROUP BY v.equipamento_id""", (F.de, F.ate + _td(days=1), *params))
    return {str(r["equipamento_id"]): (_f(r["operadas"]) if r["n"] > 1 else 0.0) for r in rows}


async def rel_custo_medio_mensal(F):
    """Uma linha por máquina: totais do período e a média por mês."""
    maquinas, celulas = await _coletar(F)
    operadas = await _operadas(F) if maquinas else {}
    soma = {}
    for (eid, _mes), c in celulas.items():
        s = soma.setdefault(eid, {"combustivel": 0.0, "litros": 0.0, "manutencao": 0.0, "pneus": 0.0})
        for k in s:
            s[k] += c[k]
    out = []
    for eid, s in soma.items():
        m = maquinas[eid]
        desde = max(m["desde"] or F.de, F.de)
        meses = max(1.0, ((F.ate - desde).days + 1) / _DIAS_MES)
        tot = s["combustivel"] + s["manutencao"] + s["pneus"]
        op = operadas.get(eid, 0.0)
        out.append({"objeto": m["objeto"], "descricao": m["descricao"], "sistema": m["sistema"], "centro_custo": m["centro_custo"],
                    "desde": desde, "meses": round(meses, 1),
                    "combustivel_mes": _r2(s["combustivel"] / meses), "litros_mes": _r2(s["litros"] / meses),
                    "manutencao_mes": _r2(s["manutencao"] / meses), "pneus_mes": _r2(s["pneus"] / meses),
                    "total_mes": _r2(tot / meses),
                    "combustivel": _r2(s["combustivel"]), "manutencao": _r2(s["manutencao"]), "pneus": _r2(s["pneus"]),
                    "total": _r2(tot), "operadas": _r2(op), "unidade": m["unidade"],
                    "custo_unit": (_r2(tot / op) if op > 0 else None)})
    out.sort(key=lambda l: (-l["total_mes"], l["objeto"] or ""))
    return out


COL_CUSTO_MEDIO_MENSAL = [("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"),
                          ("centro_custo", "C. custo", "txt"), ("desde", "1º lançamento", "data"), ("meses", "Meses", "num"),
                          ("combustivel_mes", "Combustível/mês (R$)", "moeda", "sum"), ("litros_mes", "Litros/mês", "num", "sum"),
                          ("manutencao_mes", "Manutenção sem pneus/mês (R$)", "moeda", "sum"), ("pneus_mes", "Pneus/mês (R$)", "moeda", "sum"),
                          ("total_mes", "Total/mês (R$)", "moeda", "sum"),
                          ("combustivel", "Combustível no período (R$)", "moeda", "sum"),
                          ("manutencao", "Manutenção sem pneus no período (R$)", "moeda", "sum"),
                          ("pneus", "Pneus no período (R$)", "moeda", "sum"), ("total", "Total no período (R$)", "moeda", "sum"),
                          ("operadas", "Operadas", "num", "sum"), ("unidade", "Un.", "txt"), ("custo_unit", "R$/h·km", "moeda")]
