"""
(26/11/2026) RELATÓRIOS DE SUBSISTEMAS — pneus, motores, bombas, prancha: tudo o que monta numa máquina.

Um relatório só no catálogo de routers/relatorios.py (grupo "Subsistemas"), com o campo "Mostrar":
  posicao   → Posição atual        o que está montado em cada máquina hoje e o que está solto
  vida      → Vida dos componentes quanto cada um já rodou (somando todas as máquinas) e o R$ por h/km
  trocas    → Histórico de trocas  cada montagem/desmontagem do período e quanto durou
  custo     → Custo por subsistema OTs abertas no próprio componente no período + valor de aquisição
  previsao  → Previsão de troca    quando cada componente montado deve chegar ao fim e quanto reservar

De onde vêm os dados (nada novo é gravado — só leitura):
  vínculo vivo  = operacional.equipamentos.equipamento_pai + posicao
  histórico     = manutencao.montagens (leitura da máquina na montagem e na desmontagem)
  rodado        = leitura na desmontagem − leitura na montagem; montagem em curso usa a leitura atual da máquina
  custo         = OTs do componente, pela mesma regra dos relatórios de custo (peças + mão de obra + outros)

Quem é subsistema: o que está montado em alguma máquina, o que já teve montagem registrada
ou o que é de categoria marcada como componente em Parametrizar.

Filtros "Sistema" e "Objeto" aceitam a MÁQUINA (mostra tudo o que está nela) ou o próprio COMPONENTE (mostra só ele).
Previsão de troca — vida de referência, nesta ordem: (1) vida útil cadastrada na ficha do componente;
(2) média do que rodaram os componentes anteriores na mesma máquina e posição; sem nenhuma das duas,
a linha sai como "sem referência" (não se inventa prazo).

Este arquivo não importa routers/relatorios.py no topo (quem importa é ele), para não fechar um ciclo.
"""
from datetime import date as _date, datetime as _dt, timedelta as _td

from core.db import ajard_query
from core.fuso import TZ_SQL, hoje as _hoje

_JANELA_RITMO = 90       # dias de leituras usados para medir o ritmo da máquina
_HORIZONTE = 90          # "reservar": trocas previstas para os próximos N dias


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _r2(v):
    return round(_f(v), 2)


def _dia(v):
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


def _leitura(medicao, horimetro, km):
    """leitura corrente de uma máquina (a mesma regra de manutencao._leitura_de)"""
    v = km if medicao == "km" else horimetro
    if v is None:
        v = horimetro if horimetro is not None else km
    return float(v) if v is not None else None


def _un(medicao):
    return "km" if medicao == "km" else "h"


def _filtro_pai(F, params, alias="p", comp="c"):
    """Sistema/Objeto valem para a máquina (pai) OU para o próprio componente.
    (26/11/2026) antes só olhava a máquina: escolher o pneu no campo Objeto devolvia a tela vazia."""
    w = []
    if F.sistema:
        w.append(f"({alias}.sistema_codigo = %s OR {alias}.sistema_codigo LIKE %s OR {comp}.sistema_codigo = %s OR {comp}.sistema_codigo LIKE %s)")
        params += [F.sistema, F.sistema + ".%", F.sistema, F.sistema + ".%"]
    if F.equipamento_id:
        w.append(f"({alias}.id = %s OR {comp}.id = %s)")
        params += [F.equipamento_id, F.equipamento_id]
    return (" AND " + " AND ".join(w)) if w else ""


async def _garantir():
    from routers.manutencao import _garantir_montagens, _garantir_dominios
    await _garantir_montagens()
    await _garantir_dominios()


async def _componentes(F):
    """Componentes (ativos) com a máquina atual, mais todas as montagens de cada um."""
    await _garantir()
    params = []
    filtro = _filtro_pai(F, params)
    comps = await ajard_query(f"""
        SELECT c.id, c.codigo, c.descricao, c.categoria, c.marca, c.modelo, c.posicao,
               c.valor_aquisicao, c.vida_util_h, c.data_aquisicao,
               p.id AS pai_id, p.codigo AS pai_codigo, p.descricao AS pai_desc, p.medicao AS pai_medicao,
               p.horimetro_atual AS pai_horimetro, p.km_atual AS pai_km
          FROM operacional.equipamentos c
          LEFT JOIN operacional.equipamentos p ON p.id = c.equipamento_pai
         WHERE c.ativo = true
           AND (c.equipamento_pai IS NOT NULL
                OR EXISTS (SELECT 1 FROM manutencao.montagens m WHERE m.equipamento_id = c.id)
                OR EXISTS (SELECT 1 FROM manutencao.categorias_objeto k
                            WHERE k.codigo = lower(c.categoria) AND k.componente = true))
           {filtro}
         ORDER BY p.codigo NULLS LAST, c.posicao NULLS LAST, c.codigo""", tuple(params))
    mont = await ajard_query(f"""
        SELECT m.equipamento_id, m.pai_id, m.posicao, m.data_montagem, m.data_desmontagem,
               m.leitura_pai_montagem, m.leitura_pai_desmontagem,
               p.codigo AS pai_codigo, p.medicao AS pai_medicao, p.horimetro_atual AS pai_horimetro, p.km_atual AS pai_km
          FROM manutencao.montagens m JOIN operacional.equipamentos p ON p.id = m.pai_id
         ORDER BY m.data_montagem""", ())
    por_comp = {}
    for m in mont:
        por_comp.setdefault(str(m["equipamento_id"]), []).append(m)
    return comps, por_comp, mont


def _rodado(m):
    """quanto o componente rodou numa montagem (None quando falta leitura)"""
    ini = m["leitura_pai_montagem"]
    if ini is None:
        return None
    fim = m["leitura_pai_desmontagem"]
    if fim is None and m["data_desmontagem"] is None:
        fim = _leitura(m["pai_medicao"], m["pai_horimetro"], m["pai_km"])
    if fim is None:
        return None
    r = float(fim) - float(ini)
    return r if r > 0 else 0.0


def _vida(montagens):
    """vida acumulada, unidade (da última máquina), nº de montagens e a montagem em curso"""
    vida, un, aberta = 0.0, None, None
    for m in montagens:
        r = _rodado(m)
        if r:
            vida += r
        un = _un(m["pai_medicao"])
        if m["data_desmontagem"] is None:
            aberta = m
    return vida, un, aberta


async def _ots_abertas(ids):
    if not ids:
        return {}
    rows = await ajard_query("""
        SELECT o.equipamento_id, COUNT(*)::int AS n FROM manutencao.ot o
         WHERE o.ativo = true AND o.status NOT IN ('concluida', 'cancelada') AND o.equipamento_id::text = ANY(%s)
         GROUP BY 1""", (list(ids),))
    return {str(r["equipamento_id"]): r["n"] for r in rows}


async def _custo_ots(ids, de=None, ate=None):
    """peças, mão de obra e outros das OTs de cada componente (com período, pela data de abertura da OT)"""
    if not ids:
        return {}
    from routers.relatorios import _SQL_PECAS_OT, _SQL_MO_OT, _SQL_OUTROS_OT, _prep
    await _prep()
    params, per = [list(ids)], ""
    if de is not None:
        per = " AND o.data_abertura >= %s AND o.data_abertura < %s"
        params += [de, ate]
    rows = await ajard_query(f"""
        SELECT o.equipamento_id, COUNT(*)::int AS n,
               SUM({_SQL_PECAS_OT}) AS pecas, SUM({_SQL_MO_OT}) AS mao_obra, SUM({_SQL_OUTROS_OT}) AS outros
          FROM manutencao.ot o
         WHERE o.ativo = true AND o.status <> 'cancelada' AND o.equipamento_id::text = ANY(%s){per}
         GROUP BY 1""", tuple(params))
    return {str(r["equipamento_id"]): {"n": r["n"], "pecas": _f(r["pecas"]), "mao_obra": _f(r["mao_obra"]), "outros": _f(r["outros"])} for r in rows}


def _onde(c):
    return c["pai_codigo"] if c["pai_id"] else "— solto (sem máquina) —"


# ═════════════════════════ 1 · POSIÇÃO ATUAL ═════════════════════════
async def rel_sub_posicao(F):
    comps, por_comp, _ = await _componentes(F)
    abertas = await _ots_abertas([str(c["id"]) for c in comps])
    out = []
    for c in comps:
        ms = por_comp.get(str(c["id"]), [])
        vida, un, aberta = _vida(ms)
        if c["pai_id"]:
            un = _un(c["pai_medicao"])
        out.append({"maquina": _onde(c), "maquina_desc": c["pai_desc"] if c["pai_id"] else None, "posicao": c["posicao"],
                    "componente": c["codigo"], "descricao": c["descricao"], "categoria": c["categoria"],
                    "marca": c["marca"], "modelo": c["modelo"],
                    "montado_em": _dia(aberta["data_montagem"]) if (aberta and c["pai_id"]) else None,
                    "rodado": _r2(_rodado(aberta)) if (aberta and c["pai_id"] and _rodado(aberta) is not None) else None,
                    "vida": _r2(vida), "unidade": un, "ots_abertas": abertas.get(str(c["id"]), 0)})
    return out


COL_SUB_POSICAO = [("maquina", "Máquina", "txt"), ("maquina_desc", "Descrição da máquina", "txt"), ("posicao", "Posição", "txt"),
                   ("componente", "Componente", "txt"), ("descricao", "Descrição", "txt"), ("categoria", "Categoria", "txt"),
                   ("marca", "Marca", "txt"), ("modelo", "Modelo", "txt"), ("montado_em", "Montado em", "data"),
                   ("rodado", "Rodado nesta montagem", "num"), ("vida", "Vida acumulada", "num"), ("unidade", "Un.", "txt"),
                   ("ots_abertas", "OTs abertas", "int", "sum")]


# ═════════════════════════ 2 · VIDA DOS COMPONENTES ═════════════════════════
async def rel_sub_vida(F):
    comps, por_comp, _ = await _componentes(F)
    custos = await _custo_ots([str(c["id"]) for c in comps])
    out = []
    for c in comps:
        ms = por_comp.get(str(c["id"]), [])
        vida, un, _aberta = _vida(ms)
        if c["pai_id"]:
            un = _un(c["pai_medicao"])
        k = custos.get(str(c["id"]), {})
        manut = k.get("pecas", 0.0) + k.get("mao_obra", 0.0) + k.get("outros", 0.0)
        aquis = _f(c["valor_aquisicao"])
        prevista = _f(c["vida_util_h"]) or None
        out.append({"componente": c["codigo"], "descricao": c["descricao"], "categoria": c["categoria"],
                    "marca": c["marca"], "modelo": c["modelo"], "situacao": ("montado em " + c["pai_codigo"]) if c["pai_id"] else "solto",
                    "montagens": len(ms), "vida": _r2(vida), "unidade": un,
                    "vida_prevista": _r2(prevista) if prevista else None,
                    "consumido": round(vida / prevista, 4) if prevista else None,
                    "aquisicao": _r2(aquis) if c["valor_aquisicao"] is not None else None,
                    "manutencao": _r2(manut), "total": _r2(aquis + manut),
                    "custo_unit": _r2((aquis + manut) / vida) if (vida > 0 and (aquis + manut) > 0) else None})
    out.sort(key=lambda l: (-(l["vida"] or 0), l["componente"] or ""))
    return out


COL_SUB_VIDA = [("componente", "Componente", "txt"), ("descricao", "Descrição", "txt"), ("categoria", "Categoria", "txt"),
                ("marca", "Marca", "txt"), ("modelo", "Modelo", "txt"), ("situacao", "Situação", "txt"),
                ("montagens", "Montagens", "int", "sum"), ("vida", "Vida acumulada", "num"), ("unidade", "Un.", "txt"),
                ("vida_prevista", "Vida útil cadastrada", "num"), ("consumido", "% consumido", "pct"),
                ("aquisicao", "Aquisição (R$)", "moeda", "sum"), ("manutencao", "Manutenção (R$)", "moeda", "sum"),
                ("total", "Custo total (R$)", "moeda", "sum"), ("custo_unit", "R$/h·km", "moeda")]


# ═════════════════════════ 3 · HISTÓRICO DE TROCAS ═════════════════════════
async def rel_sub_trocas(F):
    """Montagens que começaram ou terminaram no período."""
    await _garantir()
    params = [F.ini(), F.fim_excl(), F.ini(), F.fim_excl()]
    filtro = _filtro_pai(F, params)
    rows = await ajard_query(f"""
        SELECT m.posicao, m.data_montagem, m.data_desmontagem, m.leitura_pai_montagem, m.leitura_pai_desmontagem, m.observacao,
               c.codigo AS comp_codigo, c.descricao AS comp_desc, c.marca, c.modelo,
               p.codigo AS pai_codigo, p.descricao AS pai_desc, p.medicao AS pai_medicao,
               p.horimetro_atual AS pai_horimetro, p.km_atual AS pai_km
          FROM manutencao.montagens m
          JOIN operacional.equipamentos c ON c.id = m.equipamento_id
          JOIN operacional.equipamentos p ON p.id = m.pai_id
         WHERE ((m.data_montagem >= %s AND m.data_montagem < %s)
                OR (m.data_desmontagem >= %s AND m.data_desmontagem < %s)){filtro}
         ORDER BY p.codigo, m.posicao NULLS LAST, m.data_montagem""", tuple(params))
    hoje = _hoje()
    out = []
    for m in rows:
        ini, fim = _dia(m["data_montagem"]), _dia(m["data_desmontagem"])
        rod = _rodado(m)
        out.append({"maquina": m["pai_codigo"], "maquina_desc": m["pai_desc"], "posicao": m["posicao"],
                    "componente": m["comp_codigo"], "descricao": m["comp_desc"], "marca": m["marca"], "modelo": m["modelo"],
                    "montado_em": ini, "desmontado_em": fim, "situacao": "desmontado" if fim else "montado",
                    "leitura_montagem": _r2(m["leitura_pai_montagem"]) if m["leitura_pai_montagem"] is not None else None,
                    "leitura_desmontagem": _r2(m["leitura_pai_desmontagem"]) if m["leitura_pai_desmontagem"] is not None else None,
                    "rodado": _r2(rod) if rod is not None else None, "unidade": _un(m["pai_medicao"]),
                    "dias": ((fim or hoje) - ini).days if ini else None, "trocas": 1, "observacao": m["observacao"]})
    return out


COL_SUB_TROCAS = [("maquina", "Máquina", "txt"), ("maquina_desc", "Descrição da máquina", "txt"), ("posicao", "Posição", "txt"),
                  ("componente", "Componente", "txt"), ("descricao", "Descrição", "txt"), ("marca", "Marca", "txt"), ("modelo", "Modelo", "txt"),
                  ("montado_em", "Montado em", "data"), ("desmontado_em", "Desmontado em", "data"), ("situacao", "Situação", "txt"),
                  ("leitura_montagem", "Leitura na montagem", "num"), ("leitura_desmontagem", "Leitura na desmontagem", "num"),
                  ("rodado", "Rodado", "num", "sum"), ("unidade", "Un.", "txt"), ("dias", "Dias montado", "int"),
                  ("trocas", "Montagens", "int", "sum"), ("observacao", "Observação", "txt")]


# ═════════════════════════ 4 · CUSTO POR SUBSISTEMA ═════════════════════════
async def rel_sub_custo(F):
    """OTs abertas no próprio componente dentro do período (só sai quem teve OT)."""
    comps, por_comp, _ = await _componentes(F)
    custos = await _custo_ots([str(c["id"]) for c in comps], F.ini(), F.fim_excl())
    out = []
    for c in comps:
        k = custos.get(str(c["id"]))
        if not k:
            continue
        tot = k["pecas"] + k["mao_obra"] + k["outros"]
        vida, un, _a = _vida(por_comp.get(str(c["id"]), []))
        out.append({"componente": c["codigo"], "descricao": c["descricao"], "categoria": c["categoria"],
                    "marca": c["marca"], "modelo": c["modelo"], "maquina": _onde(c), "posicao": c["posicao"],
                    "n_ots": k["n"], "pecas": _r2(k["pecas"]), "mao_obra": _r2(k["mao_obra"]), "outros": _r2(k["outros"]),
                    "total": _r2(tot), "aquisicao": _r2(c["valor_aquisicao"]) if c["valor_aquisicao"] is not None else None,
                    "vida": _r2(vida), "unidade": _un(c["pai_medicao"]) if c["pai_id"] else un})
    out.sort(key=lambda l: (-l["total"], l["componente"] or ""))
    return out


COL_SUB_CUSTO = [("componente", "Componente", "txt"), ("descricao", "Descrição", "txt"), ("categoria", "Categoria", "txt"),
                 ("marca", "Marca", "txt"), ("modelo", "Modelo", "txt"), ("maquina", "Máquina atual", "txt"), ("posicao", "Posição", "txt"),
                 ("n_ots", "OTs", "int", "sum"), ("pecas", "Peças (R$)", "moeda", "sum"), ("mao_obra", "Mão de obra (R$)", "moeda", "sum"),
                 ("outros", "Outros (R$)", "moeda", "sum"), ("total", "Total no período (R$)", "moeda", "sum"),
                 ("aquisicao", "Aquisição (R$)", "moeda"), ("vida", "Vida acumulada", "num"), ("unidade", "Un.", "txt")]


# ═════════════════════════ 5 · PREVISÃO DE TROCA ═════════════════════════
async def _ritmo_por_maquina(ids):
    """h ou km por dia de cada máquina, pelas leituras dos últimos 90 dias"""
    if not ids:
        return {}
    from routers.manutencao import _garantir_leituras
    await _garantir_leituras()
    hoje = _hoje()
    rows = await ajard_query(f"""
        SELECT v.equipamento_id, MAX(v.leitura) - MIN(v.leitura) AS rodado, COUNT(*)::int AS n,
               MIN((v.data AT TIME ZONE '{TZ_SQL}')::date) AS d0, MAX((v.data AT TIME ZONE '{TZ_SQL}')::date) AS d1
          FROM operacional.v_leituras v
         WHERE v.data >= %s AND v.data < %s AND v.equipamento_id::text = ANY(%s)
         GROUP BY 1""", (hoje - _td(days=_JANELA_RITMO), hoje + _td(days=1), list(ids)))
    out = {}
    for r in rows:
        d0, d1 = _dia(r["d0"]), _dia(r["d1"])
        dias = (d1 - d0).days if (d0 and d1) else 0
        if r["n"] > 1 and dias > 0 and _f(r["rodado"]) > 0:
            out[str(r["equipamento_id"])] = _f(r["rodado"]) / dias
    return out


async def rel_sub_previsao(F):
    """Componentes montados hoje: quanto falta e quando deve ser a troca."""
    comps, por_comp, todas = await _componentes(F)
    montados = [c for c in comps if c["pai_id"]]
    ritmo = await _ritmo_por_maquina({str(c["pai_id"]) for c in montados})
    # vida média dos componentes anteriores em cada máquina + posição (só montagens encerradas e com leitura)
    hist = {}
    for m in todas:
        if m["data_desmontagem"] is None:
            continue
        r = _rodado(m)
        if r:
            hist.setdefault((str(m["pai_id"]), (m["posicao"] or "").strip().upper()), []).append(r)
    hoje = _hoje()
    out = []
    for c in montados:
        ms = por_comp.get(str(c["id"]), [])
        vida, _un_, aberta = _vida(ms)
        un = _un(c["pai_medicao"])
        anteriores = hist.get((str(c["pai_id"]), (c["posicao"] or "").strip().upper()), [])
        if _f(c["vida_util_h"]) > 0:
            ref, fonte, usado = _f(c["vida_util_h"]), "vida útil cadastrada", vida
        elif anteriores:
            ref, fonte = sum(anteriores) / len(anteriores), f"média de {len(anteriores)} anterior(es) na posição"
            usado = _rodado(aberta) if aberta and _rodado(aberta) is not None else 0.0
        else:
            ref, fonte, usado = None, "sem referência", None
        rit = ritmo.get(str(c["pai_id"]))
        resta = (ref - usado) if ref is not None else None
        dias = data = None
        if resta is not None and resta <= 0:
            situ, dias, data = "vencido", 0, hoje
        elif resta is None:
            situ = "sem referência"
        elif not rit:
            situ = "sem ritmo (máquina sem leituras em 90 dias)"
        else:
            dias = int(resta / rit)
            data = hoje + _td(days=min(dias, 3650))
            situ = "até 30 dias" if dias <= 30 else ("até 90 dias" if dias <= _HORIZONTE else "mais de 90 dias")
        valor = _f(c["valor_aquisicao"])
        out.append({"maquina": c["pai_codigo"], "maquina_desc": c["pai_desc"], "posicao": c["posicao"],
                    "componente": c["codigo"], "descricao": c["descricao"], "marca": c["marca"], "modelo": c["modelo"],
                    "situacao": situ, "usado": _r2(usado) if usado is not None else None,
                    "referencia": _r2(ref) if ref is not None else None, "fonte": fonte,
                    "resta": _r2(resta) if resta is not None else None, "unidade": un,
                    "ritmo": _r2(rit) if rit else None, "dias": dias, "data_prevista": data,
                    "valor": _r2(valor) if c["valor_aquisicao"] is not None else None,
                    "reservar": _r2(valor) if (dias is not None and dias <= _HORIZONTE) else 0.0})
    ordem = {"vencido": 0, "até 30 dias": 1, "até 90 dias": 2, "mais de 90 dias": 3}
    out.sort(key=lambda l: (ordem.get(l["situacao"], 9), l["dias"] if l["dias"] is not None else 10 ** 9, l["maquina"] or "", l["posicao"] or ""))
    return out


COL_SUB_PREVISAO = [("situacao", "Situação", "txt"), ("maquina", "Máquina", "txt"), ("maquina_desc", "Descrição da máquina", "txt"),
                    ("posicao", "Posição", "txt"), ("componente", "Componente", "txt"), ("descricao", "Descrição", "txt"),
                    ("marca", "Marca", "txt"), ("modelo", "Modelo", "txt"),
                    ("usado", "Já rodou", "num"), ("referencia", "Vida de referência", "num"), ("fonte", "De onde vem a referência", "txt"),
                    ("resta", "Falta", "num"), ("unidade", "Un.", "txt"), ("ritmo", "Ritmo da máquina (por dia)", "num"),
                    ("dias", "Dias até a troca", "int"), ("data_prevista", "Troca prevista", "data"),
                    ("valor", "Valor de reposição (R$)", "moeda"), ("reservar", "Reservar em 90 dias (R$)", "moeda", "sum")]


VISOES_SUBSISTEMAS = [
    {"v": "posicao", "t": "Posição atual", "fn": rel_sub_posicao, "cols": COL_SUB_POSICAO, "filtros": ["sistema", "objeto", "visao"],
     "sub": "fotografia de hoje: o que está montado em cada máquina e o que está solto"},
    {"v": "vida", "t": "Vida dos componentes", "fn": rel_sub_vida, "cols": COL_SUB_VIDA, "filtros": ["sistema", "objeto", "visao"],
     "sub": "quanto cada componente já rodou, somando todas as máquinas, e o R$ por hora/km"},
    {"v": "trocas", "t": "Histórico de trocas", "fn": rel_sub_trocas, "cols": COL_SUB_TROCAS, "filtros": ["periodo", "sistema", "objeto", "visao"],
     "sub": "montagens e desmontagens do período e quanto cada componente durou na posição"},
    {"v": "custo", "t": "Custo por subsistema", "fn": rel_sub_custo, "cols": COL_SUB_CUSTO, "filtros": ["periodo", "sistema", "objeto", "visao"],
     "sub": "peças, mão de obra e serviços das OTs abertas no próprio componente"},
    {"v": "previsao", "t": "Previsão de troca", "fn": rel_sub_previsao, "cols": COL_SUB_PREVISAO, "filtros": ["sistema", "objeto", "visao"],
     "sub": "quando cada componente montado deve chegar ao fim e quanto reservar para os próximos 90 dias"},
]
