"""
(11/10/2026) RELATÓRIOS DO MÓDULO MANUTENÇÃO — um lugar só, tudo exportável.

Padrão:
  GET /manutencao/api/relatorios                      → catálogo (grupos, relatórios, filtros aceitos)
  GET /manutencao/api/relatorios/{nome}?de=&ate=&...  → JSON {titulo, colunas, linhas, totais, filtros}
  GET /manutencao/api/relatorios/{nome}?...&formato=xlsx → arquivo .xlsx (números como números)
  GET /manutencao/api/relatorios/{nome}?...&formato=pdf  → arquivo .pdf (padrão documental Garra)
  POST /manutencao/api/exportar-xlsx {titulo, colunas, linhas} → .xlsx de qualquer lista da tela
  POST /manutencao/api/exportar-pdf  {titulo, colunas, linhas} → .pdf  de qualquer lista da tela

(12/10/2026) Identidade documental Garra nos dois formatos: logotipo + "GARRA
TERRAPLENAGEM E CAÇAMBAS" no cabeçalho, moldura laranja fina (PDF), azul
#001F4D nas tabelas, rodapé com emissão/emitente e "Página X de Y".

Cada relatório é uma função que devolve linhas (dicts). As COLUNAS levam o tipo
(txt · int · num · moeda · pct · data · datahora) — é o tipo que decide o formato
no Excel e o alinhamento na tela. Uma única rotina gera a planilha para todos.

Convenções de custo (as mesmas de /projectos e da Análise ABC):
  peças   = Σ movimentações da OT (saída/aplicação direta +, devolução à OT −) × custo unitário (ou custo médio)
  MO      = Σ ot_mao_obra.horas × custo_hora (ativo)
  outros  = Σ ot_outros.valor (ativo)
Horas operadas no período = maior leitura − menor leitura em operacional.v_leituras (partes, checklists, abastecimentos).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz
import io
import os
import re

from core.db import ajard_query
from routers.manutencao import (verificar_manutencao, _calcular_previsoes, _garantir_colunas_ot,
                                _garantir_leituras, _garantir_planos_cols, _garantir_funcoes,
                                _garantir_mao_obra, _garantir_tabela, _garantir_ficha_cols)

router = APIRouter()


# ── filtros comuns ────────────────────────────────────────────────────────
def _data(v, nome):
    v = (v or "").strip()
    if not v:
        return None
    try:
        d = _date.fromisoformat(v[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{nome} inválida: {v}")
    if not 2000 <= d.year <= 2100:
        raise HTTPException(status_code=400, detail=f"{nome} com ano inválido ({d.year})")
    return d


class _F:
    """Filtros normalizados. Período padrão: últimos 12 meses até hoje."""
    def __init__(self, q):
        hoje = _date.today()
        self.ate = _data(q.get("ate"), "Data final") or hoje
        self.de = _data(q.get("de"), "Data inicial") or (self.ate - _td(days=365))
        if self.de > self.ate:
            raise HTTPException(status_code=400, detail="Data inicial maior que a final")
        self.sistema = (q.get("sistema") or "").strip() or None
        self.equipamento_id = (q.get("equipamento_id") or "").strip() or None
        self.tipo_trabalho = (q.get("tipo_trabalho") or "").strip().upper() or None
        self.setor = (q.get("setor") or "").strip().upper() or None
        self.familia = (q.get("familia") or "").strip() or None

    def rotulo(self, com_periodo=True):
        p = [f"Período {self.de.strftime('%d/%m/%Y')} a {self.ate.strftime('%d/%m/%Y')}"] if com_periodo else ["Fotografia de hoje"]
        if self.sistema: p.append(f"Sistema {self.sistema}")
        if self.equipamento_id: p.append("Objeto filtrado")
        if self.tipo_trabalho: p.append(f"Tipo de trabalho {self.tipo_trabalho}")
        if self.setor: p.append(f"Setor {self.setor}")
        if self.familia: p.append(f"Família {self.familia}")
        return " · ".join(p)

    # cláusula para o objeto (e = operacional.equipamentos) — sistema pega o ramo inteiro
    def where_eq(self, params, alias="e"):
        w = [f"{alias}.ativo = true"]
        if self.sistema:
            w.append(f"({alias}.sistema_codigo = %s OR {alias}.sistema_codigo LIKE %s)")
            params += [self.sistema, self.sistema + ".%"]
        if self.equipamento_id:
            w.append(f"{alias}.id = %s"); params.append(self.equipamento_id)
        return " AND ".join(w)

    def ate_fim(self):
        """fim do dia da data final (para colunas timestamptz)"""
        return _dt.combine(self.ate, _dt.max.time()).replace(microsecond=0)


def _f(v):
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _r2(v):
    return round(_f(v), 2)


# ── horas/km operadas por objeto no período (fonte única de leituras) ──────
async def _horas_operadas(F):
    await _garantir_leituras()
    params = []
    rows = await ajard_query(f"""
        SELECT v.equipamento_id, MAX(v.leitura) - MIN(v.leitura) AS operadas, COUNT(*)::int AS n
          FROM operacional.v_leituras v JOIN operacional.equipamentos e ON e.id = v.equipamento_id
         WHERE v.data >= %s AND v.data < %s AND {F.where_eq(params)}
         GROUP BY v.equipamento_id""", (F.de, F.ate + _td(days=1), *params))
    return {str(r["equipamento_id"]): (_f(r["operadas"]) if r["n"] > 1 else 0.0) for r in rows}


# ── sub-consultas de custo por OT (mesma regra de /projectos) ─────────────
_SQL_PECAS_OT = """COALESCE((SELECT SUM((CASE WHEN m.tipo = 'entrada' THEN -1 ELSE 1 END) * m.quantidade
                                 * COALESCE(m.custo_unitario, p.custo_medio, 0))
                        FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id = m.peca_id
                       WHERE m.ot_id = o.id AND m.tipo IN ('saida','entrada','aplicacao_direta')), 0)"""
_SQL_MO_OT = """COALESCE((SELECT SUM(x.horas * COALESCE(x.custo_hora, 0)) FROM manutencao.ot_mao_obra x
                       WHERE x.ot_id = o.id AND x.ativo = true), 0)"""
_SQL_HH_OT = """COALESCE((SELECT SUM(x.horas) FROM manutencao.ot_mao_obra x WHERE x.ot_id = o.id AND x.ativo = true), 0)"""
_SQL_OUTROS_OT = """COALESCE((SELECT SUM(x.valor) FROM manutencao.ot_outros x WHERE x.ot_id = o.id AND x.ativo = true), 0)"""


async def _prep():
    await _garantir_colunas_ot(); await _garantir_ficha_cols(); await _garantir_planos_cols()
    await _garantir_funcoes(); await _garantir_mao_obra()
    for t in ("ot-outros", "ot-mo-previsto"):
        try:
            await _garantir_tabela(t)
        except Exception:
            pass


# ═════════════════════════════ CUSTOS ═════════════════════════════════════
async def rel_custo_objeto(F):
    """Custo de manutenção por objeto no período (OTs abertas no período OU com lançamentos no período).
    Base: OTs com data_abertura no período (todas as situações, exceto canceladas)."""
    params = []
    we = F.where_eq(params)
    extra = ""
    if F.tipo_trabalho:
        extra += " AND upper(COALESCE(o.tipo_trabalho,'')) LIKE %s"; params.append(F.tipo_trabalho + "%")
    if F.setor:
        extra += " AND upper(COALESCE(o.setor_interventor,'')) = %s"; params.append(F.setor)
    rows = await ajard_query(f"""
        SELECT e.id, e.codigo, e.descricao, e.sistema_codigo, e.centro_custo, e.medicao,
               COUNT(o.id)::int AS n_ots,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'C%%' OR o.tipo='corretiva' THEN 1 ELSE 0 END)::int AS n_corretivas,
               SUM({_SQL_PECAS_OT}) AS pecas, SUM({_SQL_MO_OT}) AS mao_obra, SUM({_SQL_OUTROS_OT}) AS outros, SUM({_SQL_HH_OT}) AS hh
          FROM operacional.equipamentos e
          JOIN manutencao.ot o ON o.equipamento_id = e.id AND o.ativo = true AND o.status <> 'cancelada'
               AND o.data_abertura >= %s AND o.data_abertura < %s {extra}
         WHERE {we}
         GROUP BY e.id, e.codigo, e.descricao, e.sistema_codigo, e.centro_custo, e.medicao
         ORDER BY (SUM({_SQL_PECAS_OT}) + SUM({_SQL_MO_OT}) + SUM({_SQL_OUTROS_OT})) DESC, e.codigo""",
        (F.de, F.ate + _td(days=1), *params))
    horas = await _horas_operadas(F)
    out = []
    for r in rows:
        tot = _f(r["pecas"]) + _f(r["mao_obra"]) + _f(r["outros"])
        op = horas.get(str(r["id"]), 0.0)
        out.append({"objeto": r["codigo"], "descricao": r["descricao"], "sistema": r["sistema_codigo"], "centro_custo": r["centro_custo"],
                    "n_ots": r["n_ots"], "n_corretivas": r["n_corretivas"], "pecas": _r2(r["pecas"]), "mao_obra": _r2(r["mao_obra"]),
                    "outros": _r2(r["outros"]), "total": _r2(tot), "hh": _r2(r["hh"]),
                    "operadas": _r2(op), "unidade": "km" if r["medicao"] == "km" else "h",
                    "custo_unit": (_r2(tot / op) if op > 0 else None)})
    return out

COL_CUSTO_OBJETO = [("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"), ("centro_custo", "C. custo", "txt"),
                    ("n_ots", "OTs", "int", "sum"), ("n_corretivas", "Corretivas", "int", "sum"),
                    ("pecas", "Peças (R$)", "moeda", "sum"), ("mao_obra", "Mão de obra (R$)", "moeda", "sum"), ("outros", "Outros (R$)", "moeda", "sum"),
                    ("total", "Total (R$)", "moeda", "sum"), ("hh", "HH", "num", "sum"), ("operadas", "Operadas", "num", "sum"), ("unidade", "Un.", "txt"),
                    ("custo_unit", "R$/h·km", "moeda")]


async def rel_custo_sistema(F):
    linhas = await rel_custo_objeto(F)
    agg = {}
    for l in linhas:
        k = l["sistema"] or "— sem sistema"
        a = agg.setdefault(k, {"sistema": k, "objetos": 0, "n_ots": 0, "pecas": 0.0, "mao_obra": 0.0, "outros": 0.0, "total": 0.0})
        a["objetos"] += 1; a["n_ots"] += l["n_ots"]
        for c in ("pecas", "mao_obra", "outros", "total"):
            a[c] += l[c]
    nomes = {r["codigo"]: r["nome"] for r in await ajard_query("SELECT codigo, nome FROM manutencao.sistemas")}
    tot = sum(a["total"] for a in agg.values()) or 0.0
    out = sorted(agg.values(), key=lambda a: -a["total"])
    for a in out:
        a["nome"] = nomes.get(a["sistema"], "")
        a["pct"] = round(a["total"] / tot, 4) if tot else 0.0
        for c in ("pecas", "mao_obra", "outros", "total"):
            a[c] = _r2(a[c])
    return out

COL_CUSTO_SISTEMA = [("sistema", "Sistema", "txt"), ("nome", "Nome", "txt"), ("objetos", "Objetos", "int", "sum"), ("n_ots", "OTs", "int", "sum"),
                     ("pecas", "Peças (R$)", "moeda", "sum"), ("mao_obra", "Mão de obra (R$)", "moeda", "sum"), ("outros", "Outros (R$)", "moeda", "sum"),
                     ("total", "Total (R$)", "moeda", "sum"), ("pct", "% do total", "pct")]


async def rel_custo_conta(F):
    """Plano de contas: peças (movimentações.rubrica), MO (ot_mao_obra.rubrica), outros (ot_outros.rubrica)."""
    params = []
    we = F.where_eq(params)
    de, ate = F.de, F.ate + _td(days=1)
    rows = await ajard_query(f"""
        SELECT conta, SUM(valor) AS valor, COUNT(*)::int AS lancamentos FROM (
          SELECT COALESCE(NULLIF(m.rubrica,''), '2') AS conta,
                 (CASE WHEN m.tipo='entrada' THEN -1 ELSE 1 END) * m.quantidade * COALESCE(m.custo_unitario, p.custo_medio, 0) AS valor
            FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id = m.peca_id
            JOIN manutencao.ot o ON o.id = m.ot_id JOIN operacional.equipamentos e ON e.id = o.equipamento_id
           WHERE m.tipo IN ('saida','entrada','aplicacao_direta') AND m.criado_em >= %s AND m.criado_em < %s AND {we}
          UNION ALL
          SELECT COALESCE(NULLIF(x.rubrica,''), '1') AS conta, x.horas * COALESCE(x.custo_hora,0)
            FROM manutencao.ot_mao_obra x JOIN manutencao.ot o ON o.id = x.ot_id JOIN operacional.equipamentos e ON e.id = o.equipamento_id
           WHERE x.ativo = true AND x.data >= %s AND x.data <= %s AND {we}
          UNION ALL
          SELECT COALESCE(NULLIF(x.rubrica,''), '3') AS conta, COALESCE(x.valor,0)
            FROM manutencao.ot_outros x JOIN manutencao.ot o ON o.id = x.ot_id JOIN operacional.equipamentos e ON e.id = o.equipamento_id
           WHERE x.ativo = true AND COALESCE(x.data, x.criado_em::date) >= %s AND COALESCE(x.data, x.criado_em::date) <= %s AND {we}
        ) t GROUP BY conta ORDER BY conta""",
        (de, ate, *params, de, F.ate, *params, de, F.ate, *params))
    nomes = {r["codigo"]: r["nome"] for r in await ajard_query("SELECT codigo, nome FROM manutencao.rubricas")}
    tot = sum(_f(r["valor"]) for r in rows) or 0.0
    out = []
    for r in rows:
        c = r["conta"]
        out.append({"conta": c, "descricao": nomes.get(c, ""), "grupo": nomes.get(c.split(".")[0], ""),
                    "lancamentos": r["lancamentos"], "valor": _r2(r["valor"]), "pct": round(_f(r["valor"]) / tot, 4) if tot else 0.0})
    return out

COL_CUSTO_CONTA = [("conta", "Conta", "txt"), ("descricao", "Descrição", "txt"), ("grupo", "Grupo", "txt"),
                   ("lancamentos", "Lançamentos", "int", "sum"), ("valor", "Valor (R$)", "moeda", "sum"), ("pct", "% do total", "pct")]


async def rel_top_custo(F):
    linhas = await rel_custo_objeto(F)
    tot = sum(l["total"] for l in linhas) or 0.0
    acum, out = 0.0, []
    for i, l in enumerate(linhas[:10], 1):
        acum += l["total"]
        out.append({"pos": i, "objeto": l["objeto"], "descricao": l["descricao"], "sistema": l["sistema"], "n_ots": l["n_ots"],
                    "total": l["total"], "pct": round(l["total"] / tot, 4) if tot else 0.0, "acum": round(acum / tot, 4) if tot else 0.0,
                    "custo_unit": l["custo_unit"]})
    return out

COL_TOP_CUSTO = [("pos", "#", "int"), ("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"),
                 ("n_ots", "OTs", "int", "sum"), ("total", "Total (R$)", "moeda", "sum"), ("pct", "%", "pct"), ("acum", "% acumulado", "pct"), ("custo_unit", "R$/h·km", "moeda")]


# ═════════════════════════════ TRABALHOS ══════════════════════════════════
_STATUS_PT = {"rascunho": "Em criação", "programada": "Programada", "aberta": "Aberta", "em_andamento": "Em curso",
              "aguardando_peca": "Aguard. peça", "concluida": "Encerrada", "cancelada": "Cancelada"}


async def rel_backlog(F):
    """Fotografia: tudo que não está encerrado/cancelado (período não se aplica)."""
    params = []
    we = F.where_eq(params)
    extra = ""
    if F.tipo_trabalho:
        extra += " AND upper(COALESCE(o.tipo_trabalho,'')) LIKE %s"; params.append(F.tipo_trabalho + "%")
    if F.setor:
        extra += " AND upper(COALESCE(o.setor_interventor,'')) = %s"; params.append(F.setor)
    rows = await ajard_query(f"""
        SELECT o.numero, o.descricao, o.status, o.tipo_trabalho, o.tipo, o.prioridade, o.data_abertura, o.data_prevista,
               o.setor_interventor, o.interventor, e.codigo AS objeto, e.sistema_codigo,
               u.nome AS responsavel, fo.nome AS fornecedor,
               {_SQL_PECAS_OT} AS pecas, {_SQL_MO_OT} AS mao_obra, {_SQL_OUTROS_OT} AS outros
          FROM manutencao.ot o JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          LEFT JOIN public.usuarios_garra u ON u.id = o.responsavel_id
          LEFT JOIN public.fornecedores fo ON fo.id = o.fornecedor_id
         WHERE o.ativo = true AND o.status NOT IN ('concluida','cancelada') AND {we} {extra}
         ORDER BY o.data_abertura""", tuple(params))
    hoje = _date.today()
    out = []
    for r in rows:
        ab = r["data_abertura"].date() if r["data_abertura"] else None
        out.append({"ot": r["numero"], "objeto": r["objeto"], "sistema": r["sistema_codigo"], "descricao": r["descricao"],
                    "tipo_trabalho": r["tipo_trabalho"] or "", "tipo": r["tipo"], "prioridade": r["prioridade"],
                    "estado": _STATUS_PT.get(r["status"], r["status"]), "abertura": ab, "dias": (hoje - ab).days if ab else None,
                    "prevista": r["data_prevista"], "atraso": ((hoje - r["data_prevista"]).days if r["data_prevista"] and r["data_prevista"] < hoje else 0),
                    "responsavel": r["responsavel"] or r["fornecedor"] or "", "setor": r["setor_interventor"] or ("EXT" if r["interventor"] == "externo" else ""),
                    "custo_ate_agora": _r2(_f(r["pecas"]) + _f(r["mao_obra"]) + _f(r["outros"]))})
    return out

COL_BACKLOG = [("ot", "OT", "txt"), ("objeto", "Objeto", "txt"), ("sistema", "Sistema", "txt"), ("descricao", "Descrição", "txt"),
               ("tipo_trabalho", "Tipo trab.", "txt"), ("tipo", "Classe", "txt"), ("prioridade", "Urgência", "txt"), ("estado", "Estado", "txt"),
               ("abertura", "Abertura", "data"), ("dias", "Dias em aberto", "int"), ("prevista", "Programada", "data"), ("atraso", "Atraso (d)", "int"),
               ("responsavel", "Responsável", "txt"), ("setor", "Setor", "txt"), ("custo_ate_agora", "Custo até agora (R$)", "moeda", "sum")]


async def rel_encerradas(F):
    params = []
    we = F.where_eq(params)
    extra = ""
    if F.tipo_trabalho:
        extra += " AND upper(COALESCE(o.tipo_trabalho,'')) LIKE %s"; params.append(F.tipo_trabalho + "%")
    if F.setor:
        extra += " AND upper(COALESCE(o.setor_interventor,'')) = %s"; params.append(F.setor)
    rows = await ajard_query(f"""
        SELECT o.numero, o.descricao, o.tipo_trabalho, o.tipo, o.data_abertura, o.data_conclusao, o.tdm_previsto, o.horas_parada,
               o.setor_interventor, o.interventor, e.codigo AS objeto, e.sistema_codigo, u.nome AS responsavel, fo.nome AS fornecedor,
               {_SQL_PECAS_OT} AS pecas, {_SQL_MO_OT} AS mao_obra, {_SQL_OUTROS_OT} AS outros, {_SQL_HH_OT} AS hh
          FROM manutencao.ot o JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          LEFT JOIN public.usuarios_garra u ON u.id = o.responsavel_id
          LEFT JOIN public.fornecedores fo ON fo.id = o.fornecedor_id
         WHERE o.ativo = true AND o.status = 'concluida' AND o.data_conclusao >= %s AND o.data_conclusao < %s AND {we} {extra}
         ORDER BY o.data_conclusao""", (F.de, F.ate + _td(days=1), *params))
    out = []
    for r in rows:
        ab = r["data_abertura"].date() if r["data_abertura"] else None
        fim = r["data_conclusao"].date() if r["data_conclusao"] else None
        out.append({"ot": r["numero"], "objeto": r["objeto"], "sistema": r["sistema_codigo"], "descricao": r["descricao"],
                    "tipo_trabalho": r["tipo_trabalho"] or "", "tipo": r["tipo"], "abertura": ab, "fim": fim,
                    "duracao": (fim - ab).days if ab and fim else None,
                    "tdm_prev": _r2(r["tdm_previsto"]) if r["tdm_previsto"] is not None else None,
                    "parada": _r2(r["horas_parada"]) if r["horas_parada"] is not None else None,
                    "hh": _r2(r["hh"]), "responsavel": r["responsavel"] or r["fornecedor"] or "",
                    "setor": r["setor_interventor"] or ("EXT" if r["interventor"] == "externo" else ""),
                    "pecas": _r2(r["pecas"]), "mao_obra": _r2(r["mao_obra"]), "outros": _r2(r["outros"]),
                    "total": _r2(_f(r["pecas"]) + _f(r["mao_obra"]) + _f(r["outros"]))})
    return out

COL_ENCERRADAS = [("ot", "OT", "txt"), ("objeto", "Objeto", "txt"), ("sistema", "Sistema", "txt"), ("descricao", "Descrição", "txt"),
                  ("tipo_trabalho", "Tipo trab.", "txt"), ("tipo", "Classe", "txt"), ("abertura", "Abertura", "data"), ("fim", "Encerramento", "data"),
                  ("duracao", "Dias", "int"), ("tdm_prev", "TDM prev. (h)", "num", "sum"), ("parada", "Parada real (h)", "num", "sum"), ("hh", "HH", "num", "sum"),
                  ("responsavel", "Responsável", "txt"), ("setor", "Setor", "txt"),
                  ("pecas", "Peças (R$)", "moeda", "sum"), ("mao_obra", "MO (R$)", "moeda", "sum"), ("outros", "Outros (R$)", "moeda", "sum"), ("total", "Total (R$)", "moeda", "sum")]


async def rel_corretiva_preventiva(F):
    """Por objeto: OTs abertas no período divididas em corretivas (C*, ou classe corretiva) × preventivas (A*/B*, ou classe preventiva)."""
    params = []
    we = F.where_eq(params)
    rows = await ajard_query(f"""
        SELECT e.codigo AS objeto, e.descricao, e.sistema_codigo,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'C%%' OR (COALESCE(o.tipo_trabalho,'')='' AND o.tipo='corretiva') THEN 1 ELSE 0 END)::int AS n_c,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'A%%' OR upper(COALESCE(o.tipo_trabalho,'')) LIKE 'B%%' OR (COALESCE(o.tipo_trabalho,'')='' AND o.tipo='preventiva') THEN 1 ELSE 0 END)::int AS n_p,
               COUNT(o.id)::int AS n,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'C%%' OR (COALESCE(o.tipo_trabalho,'')='' AND o.tipo='corretiva')
                        THEN {_SQL_PECAS_OT} + {_SQL_MO_OT} + {_SQL_OUTROS_OT} ELSE 0 END) AS custo_c,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'A%%' OR upper(COALESCE(o.tipo_trabalho,'')) LIKE 'B%%' OR (COALESCE(o.tipo_trabalho,'')='' AND o.tipo='preventiva')
                        THEN {_SQL_PECAS_OT} + {_SQL_MO_OT} + {_SQL_OUTROS_OT} ELSE 0 END) AS custo_p
          FROM operacional.equipamentos e
          JOIN manutencao.ot o ON o.equipamento_id = e.id AND o.ativo = true AND o.status <> 'cancelada'
               AND o.data_abertura >= %s AND o.data_abertura < %s
         WHERE {we}
         GROUP BY e.codigo, e.descricao, e.sistema_codigo ORDER BY e.codigo""", (F.de, F.ate + _td(days=1), *params))
    out = []
    for r in rows:
        n = r["n"] or 0
        out.append({"objeto": r["objeto"], "descricao": r["descricao"], "sistema": r["sistema_codigo"], "n_c": r["n_c"], "n_p": r["n_p"],
                    "outras": n - r["n_c"] - r["n_p"], "pct_c": round(r["n_c"] / n, 4) if n else 0.0,
                    "custo_c": _r2(r["custo_c"]), "custo_p": _r2(r["custo_p"]),
                    "pct_custo_c": round(_f(r["custo_c"]) / (_f(r["custo_c"]) + _f(r["custo_p"])), 4) if (_f(r["custo_c"]) + _f(r["custo_p"])) else 0.0})
    return out

COL_CXP = [("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"),
           ("n_c", "Corretivas", "int", "sum"), ("n_p", "Preventivas", "int", "sum"), ("outras", "Outras", "int", "sum"), ("pct_c", "% corretivas", "pct"),
           ("custo_c", "Custo corretivas (R$)", "moeda", "sum"), ("custo_p", "Custo preventivas (R$)", "moeda", "sum"), ("pct_custo_c", "% custo corretivo", "pct")]


async def rel_avarias(F):
    params = []
    we = F.where_eq(params)
    rows = await ajard_query(f"""
        SELECT COALESCE(NULLIF(o.sintoma_codigo,''), '—') AS sintoma, COALESCE(NULLIF(o.causa_codigo,''), '—') AS causa,
               COUNT(*)::int AS n, COUNT(DISTINCT o.equipamento_id)::int AS objetos,
               SUM({_SQL_PECAS_OT} + {_SQL_MO_OT} + {_SQL_OUTROS_OT}) AS custo, SUM(COALESCE(o.horas_parada,0)) AS parada
          FROM manutencao.ot o JOIN operacional.equipamentos e ON e.id = o.equipamento_id
         WHERE o.ativo = true AND o.status <> 'cancelada' AND o.data_abertura >= %s AND o.data_abertura < %s
           AND (upper(COALESCE(o.tipo_trabalho,'')) LIKE 'C%%' OR o.tipo = 'corretiva') AND {we}
         GROUP BY 1, 2 ORDER BY n DESC, custo DESC""", (F.de, F.ate + _td(days=1), *params))
    sint = {r["codigo"]: r["nome"] for r in await ajard_query("SELECT codigo, nome FROM manutencao.sintomas")}
    caus = {r["codigo"]: r["nome"] for r in await ajard_query("SELECT codigo, nome FROM manutencao.causas")}
    tot = sum(r["n"] for r in rows) or 0
    acum, out = 0, []
    for r in rows:
        acum += r["n"]
        out.append({"sintoma": r["sintoma"], "sintoma_nome": sint.get(r["sintoma"], ""), "causa": r["causa"], "causa_nome": caus.get(r["causa"], ""),
                    "n": r["n"], "pct": round(r["n"] / tot, 4) if tot else 0.0, "acum": round(acum / tot, 4) if tot else 0.0,
                    "objetos": r["objetos"], "custo": _r2(r["custo"]), "parada": _r2(r["parada"])})
    return out

COL_AVARIAS = [("sintoma", "Sintoma", "txt"), ("sintoma_nome", "Descrição", "txt"), ("causa", "Causa", "txt"), ("causa_nome", "Descrição", "txt"),
               ("n", "OTs", "int", "sum"), ("pct", "%", "pct"), ("acum", "% acumulado", "pct"), ("objetos", "Objetos", "int"),
               ("custo", "Custo (R$)", "moeda", "sum"), ("parada", "Horas paradas", "num", "sum")]


# ═════════════════════════════ PREVENTIVAS ════════════════════════════════
_PREV_PT = {"ok": "Em dia", "a_vencer": "A vencer", "vencida": "Vencida", "programada": "OT programada", "sem_base": "Sem baseline", "sem_periodo": "Sem período"}


async def rel_aderencia_fmp(F):
    prev = await _calcular_previsoes()
    eqs = {str(r["id"]): r for r in await ajard_query("SELECT id, codigo, sistema_codigo FROM operacional.equipamentos WHERE ativo = true")}
    hoje = _date.today()
    out = []
    for it in prev["itens"]:
        e = eqs.get(it["equipamento_id"])
        if not e:
            continue
        if F.sistema and not ((e["sistema_codigo"] or "") == F.sistema or (e["sistema_codigo"] or "").startswith(F.sistema + ".")):
            continue
        if F.equipamento_id and str(e["id"]) != F.equipamento_id:
            continue
        prox = it.get("proxima_data")
        atraso = 0
        if it["status"] == "vencida" and prox:
            try:
                atraso = (hoje - _date.fromisoformat(prox)).days
            except ValueError:
                atraso = 0
        out.append({"objeto": e["codigo"], "sistema": e["sistema_codigo"], "fmp": it["plano_codigo"], "descricao": it["plano_descricao"],
                    "periodicidade": f"{it['periodo_qtd']:g} {it['periodo'] or ''}".strip() if it.get("periodo_qtd") is not None else (it.get("periodo") or ""),
                    "modo": "calendário" if it.get("modo") == "calendario" else ("registro" if it.get("modo") else ""),
                    "ultima": it.get("ultima_data"), "ultima_leitura": it.get("ultima_leitura"),
                    "proxima": prox, "leitura_alvo": it.get("leitura_alvo"), "leitura_atual": it.get("leitura_atual"),
                    "situacao": _PREV_PT.get(it["status"], it["status"]), "atraso": atraso if atraso > 0 else None, "ot": (it.get("ot") or {}).get("numero") if isinstance(it.get("ot"), dict) else (it.get("ot") or "")})
    # resumo de aderência: em dia + a vencer + programada ÷ (todas com base)
    return out

COL_ADERENCIA = [("objeto", "Objeto", "txt"), ("sistema", "Sistema", "txt"), ("fmp", "FMP", "txt"), ("descricao", "Descrição", "txt"),
                 ("periodicidade", "Periodicidade", "txt"), ("modo", "Modo", "txt"), ("ultima", "Última", "data"), ("ultima_leitura", "Últ. leitura", "num"),
                 ("proxima", "Próxima", "data"), ("leitura_alvo", "Leitura alvo", "num"), ("leitura_atual", "Leitura atual", "num"),
                 ("situacao", "Situação", "txt"), ("atraso", "Atraso (d)", "int"), ("ot", "OT", "txt")]


async def rel_preventivas_vencidas(F):
    return [l for l in await rel_aderencia_fmp(F) if l["situacao"] in ("Vencida", "A vencer")]


# ═════════════════════════════ DISPONIBILIDADE ════════════════════════════
async def rel_indisponibilidade(F):
    """Por objeto: horas paradas (OTs encerradas no período, horas_parada), nº paradas, MTTR, MTBF, disponibilidade.
    Horas disponíveis do período = dias × 8 h (jornada) — ajuste em `jornada` se a frota opera em outro regime."""
    jornada = 8.0
    params = []
    we = F.where_eq(params)
    rows = await ajard_query(f"""
        SELECT e.id, e.codigo, e.descricao, e.sistema_codigo,
               COUNT(o.id)::int AS n_ots,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'C%%' OR o.tipo='corretiva' THEN 1 ELSE 0 END)::int AS n_corr,
               SUM(COALESCE(o.horas_parada,0)) AS parada,
               SUM(CASE WHEN upper(COALESCE(o.tipo_trabalho,'')) LIKE 'C%%' OR o.tipo='corretiva' THEN COALESCE(o.horas_parada,0) ELSE 0 END) AS parada_corr
          FROM operacional.equipamentos e
          JOIN manutencao.ot o ON o.equipamento_id = e.id AND o.ativo = true AND o.status = 'concluida'
               AND o.data_conclusao >= %s AND o.data_conclusao < %s
         WHERE {we}
         GROUP BY e.id, e.codigo, e.descricao, e.sistema_codigo ORDER BY parada DESC, e.codigo""", (F.de, F.ate + _td(days=1), *params))
    horas = await _horas_operadas(F)
    dias = (F.ate - F.de).days + 1
    disp_h = dias * jornada
    out = []
    for r in rows:
        op = horas.get(str(r["id"]), 0.0)
        parada = _f(r["parada"]); nc = r["n_corr"] or 0
        out.append({"objeto": r["codigo"], "descricao": r["descricao"], "sistema": r["sistema_codigo"], "n_ots": r["n_ots"], "n_corr": nc,
                    "parada": _r2(parada), "parada_corr": _r2(r["parada_corr"]),
                    "mttr": _r2(_f(r["parada_corr"]) / nc) if nc else None,
                    "operadas": _r2(op), "mtbf": _r2(op / nc) if nc and op > 0 else None,
                    "disponibilidade": round(max(0.0, (disp_h - parada) / disp_h), 4) if disp_h else None})
    return out

COL_INDISP = [("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"), ("n_ots", "OTs encerradas", "int", "sum"),
              ("n_corr", "Corretivas", "int", "sum"), ("parada", "Horas paradas", "num", "sum"), ("parada_corr", "Paradas corretivas (h)", "num", "sum"),
              ("mttr", "MTTR (h)", "num"), ("operadas", "Operadas (h·km)", "num", "sum"), ("mtbf", "MTBF", "num"), ("disponibilidade", "Disponibilidade", "pct")]


# ═════════════════════════════ MÃO DE OBRA ════════════════════════════════
async def rel_hh(F):
    """Setor › função › pessoa: HH e custo realizados (ot_mao_obra) no período; planejado vem do ot_mo_previsto das mesmas OTs."""
    params = []
    we = F.where_eq(params)
    extra = ""
    if F.setor:
        extra += " AND upper(COALESCE(f.setor, o.setor_interventor, '')) = %s"; params.append(F.setor)
    rows = await ajard_query(f"""
        SELECT COALESCE(f.setor, o.setor_interventor, '—') AS setor, COALESCE(x.funcao_codigo, '—') AS funcao, f.nome AS funcao_nome,
               COALESCE(u.nome, x.nome, '—') AS pessoa,
               SUM(x.horas) AS hh, SUM(x.horas * COALESCE(x.custo_hora,0)) AS custo, COUNT(DISTINCT x.ot_id)::int AS n_ots
          FROM manutencao.ot_mao_obra x JOIN manutencao.ot o ON o.id = x.ot_id JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          LEFT JOIN manutencao.funcoes f ON f.codigo = x.funcao_codigo
          LEFT JOIN public.usuarios_garra u ON u.id = x.usuario_id
         WHERE x.ativo = true AND x.data >= %s AND x.data <= %s AND {we} {extra}
         GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 4""", (F.de, F.ate, *params))
    # planejado por função nas OTs com lançamento no período
    params2 = []
    we2 = F.where_eq(params2)
    prev = await ajard_query(f"""
        SELECT COALESCE(p.funcao_codigo,'—') AS funcao, SUM(p.hh) AS hh_prev, SUM(p.hh * COALESCE(p.custo_hh,0)) AS custo_prev
          FROM manutencao.ot_mo_previsto p JOIN manutencao.ot o ON o.id = p.ot_id JOIN operacional.equipamentos e ON e.id = o.equipamento_id
         WHERE p.ativo = true AND {we2}
           AND EXISTS (SELECT 1 FROM manutencao.ot_mao_obra x WHERE x.ot_id = o.id AND x.ativo = true AND x.data >= %s AND x.data <= %s)
         GROUP BY 1""", (*params2, F.de, F.ate))
    pv = {r["funcao"]: r for r in prev}
    out = []
    vistos = set()
    for r in rows:
        f = r["funcao"]
        p = pv.get(f)
        primeiro = f not in vistos; vistos.add(f)
        out.append({"setor": r["setor"], "funcao": f, "funcao_nome": r["funcao_nome"] or "", "pessoa": r["pessoa"],
                    "hh": _r2(r["hh"]), "custo": _r2(r["custo"]), "n_ots": r["n_ots"],
                    "hh_prev": _r2(p["hh_prev"]) if (p and primeiro) else None, "custo_prev": _r2(p["custo_prev"]) if (p and primeiro) else None})
    return out

COL_HH = [("setor", "Setor", "txt"), ("funcao", "Função", "txt"), ("funcao_nome", "Nome da função", "txt"), ("pessoa", "Pessoa", "txt"),
          ("n_ots", "OTs", "int"), ("hh", "HH realizado", "num", "sum"), ("custo", "Custo realizado (R$)", "moeda", "sum"),
          ("hh_prev", "HH planejado (função)", "num", "sum"), ("custo_prev", "Custo planejado (R$)", "moeda", "sum")]


# ═════════════════════════════ MATERIAIS ══════════════════════════════════
async def rel_consumo_pecas(F):
    params = []
    we = F.where_eq(params)
    extra = ""
    if F.familia:
        extra += " AND (p.familia_codigo = %s OR p.familia_codigo LIKE %s)"; params += [F.familia, F.familia + ".%"]
    rows = await ajard_query(f"""
        SELECT m.criado_em, m.tipo, p.codigo, p.descricao, p.unidade, p.familia_codigo,
               (CASE WHEN m.tipo='entrada' THEN -1 ELSE 1 END) * m.quantidade AS qtd,
               COALESCE(m.custo_unitario, p.custo_medio, 0) AS custo_unit,
               (CASE WHEN m.tipo='entrada' THEN -1 ELSE 1 END) * m.quantidade * COALESCE(m.custo_unitario, p.custo_medio, 0) AS valor,
               o.numero AS ot, e.codigo AS objeto, m.rubrica, fo.nome AS fornecedor
          FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id = m.peca_id
          LEFT JOIN manutencao.ot o ON o.id = m.ot_id
          LEFT JOIN operacional.equipamentos e ON e.id = o.equipamento_id
          LEFT JOIN public.fornecedores fo ON fo.id = m.fornecedor_id
         WHERE (m.tipo IN ('saida','aplicacao_direta') OR (m.tipo = 'entrada' AND m.ot_id IS NOT NULL))
           AND m.criado_em >= %s AND m.criado_em < %s""" +
        (f" AND (e.id IS NULL OR ({we}))" if (F.sistema or F.equipamento_id) else "") + extra +
        " ORDER BY m.criado_em", (F.de, F.ate + _td(days=1), *params))
    out = []
    for r in rows:
        out.append({"data": r["criado_em"], "item": r["codigo"], "descricao": r["descricao"], "familia": r["familia_codigo"], "unidade": r["unidade"],
                    "tipo": {"saida": "Saída estoque", "aplicacao_direta": "Aplicação direta", "entrada": "Devolução"}.get(r["tipo"], r["tipo"]),
                    "qtd": _r2(r["qtd"]), "custo_unit": _r2(r["custo_unit"]), "valor": _r2(r["valor"]),
                    "ot": r["ot"] or "", "objeto": r["objeto"] or "", "conta": r["rubrica"] or "", "fornecedor": r["fornecedor"] or ""})
    return out

COL_CONSUMO = [("data", "Data", "datahora"), ("item", "Item", "txt"), ("descricao", "Descrição", "txt"), ("familia", "Família", "txt"), ("unidade", "Un.", "txt"),
               ("tipo", "Movimento", "txt"), ("qtd", "Qtd", "num", "sum"), ("custo_unit", "Custo unit. (R$)", "moeda"), ("valor", "Valor (R$)", "moeda", "sum"),
               ("ot", "OT", "txt"), ("objeto", "Objeto", "txt"), ("conta", "Conta", "txt"), ("fornecedor", "Fornecedor", "txt")]


async def rel_consumo_familia(F):
    linhas = await rel_consumo_pecas(F)
    agg = {}
    for l in linhas:
        k = (l["familia"] or "—").split(".")[0]
        a = agg.setdefault(k, {"familia": k, "itens": set(), "qtd": 0.0, "valor": 0.0, "movimentos": 0})
        a["itens"].add(l["item"]); a["qtd"] += l["qtd"]; a["valor"] += l["valor"]; a["movimentos"] += 1
    nomes = {r["codigo"]: r["nome"] for r in await ajard_query("SELECT codigo, nome FROM manutencao.familias")}
    tot = sum(a["valor"] for a in agg.values()) or 0.0
    out = []
    for a in sorted(agg.values(), key=lambda x: -x["valor"]):
        out.append({"familia": a["familia"], "nome": nomes.get(a["familia"], ""), "itens": len(a["itens"]), "movimentos": a["movimentos"],
                    "qtd": _r2(a["qtd"]), "valor": _r2(a["valor"]), "pct": round(a["valor"] / tot, 4) if tot else 0.0})
    return out

COL_CONSUMO_FAM = [("familia", "Família", "txt"), ("nome", "Nome", "txt"), ("itens", "Itens distintos", "int"), ("movimentos", "Movimentos", "int", "sum"),
                   ("qtd", "Qtd", "num", "sum"), ("valor", "Valor (R$)", "moeda", "sum"), ("pct", "% do total", "pct")]


async def rel_estoque_baixo(F):
    rows = await ajard_query("""
        SELECT p.codigo, p.descricao, p.unidade, p.familia_codigo, a.codigo AS almox, s.quantidade, s.minimo, s.maximo, s.localizacao, p.custo_medio
          FROM manutencao.estoque s JOIN manutencao.pecas p ON p.id = s.peca_id
          JOIN manutencao.almoxarifados a ON a.id = s.almoxarifado_id
         WHERE p.ativo = true AND s.minimo IS NOT NULL AND s.minimo > 0 AND COALESCE(s.quantidade,0) < s.minimo
         ORDER BY (s.minimo - COALESCE(s.quantidade,0)) DESC, p.codigo""")
    out = []
    for r in rows:
        q, mn, mx = _f(r["quantidade"]), _f(r["minimo"]), (_f(r["maximo"]) if r["maximo"] is not None else None)
        alvo = mx if mx else 2 * mn
        out.append({"item": r["codigo"], "descricao": r["descricao"], "familia": r["familia_codigo"], "unidade": r["unidade"], "estoque": r["almox"],
                    "local": r["localizacao"] or "", "saldo": _r2(q), "minimo": _r2(mn), "maximo": _r2(mx) if mx else None,
                    "sugestao": _r2(max(0.0, alvo - q)), "custo_medio": _r2(r["custo_medio"]), "valor_compra": _r2(max(0.0, alvo - q) * _f(r["custo_medio"]))})
    return out

COL_ESTOQUE_BAIXO = [("item", "Item", "txt"), ("descricao", "Descrição", "txt"), ("familia", "Família", "txt"), ("unidade", "Un.", "txt"), ("estoque", "Estoque", "txt"),
                     ("local", "Local", "txt"), ("saldo", "Saldo", "num"), ("minimo", "Mínimo", "num"), ("maximo", "Máximo", "num"),
                     ("sugestao", "Sugestão compra", "num", "sum"), ("custo_medio", "Custo médio (R$)", "moeda"), ("valor_compra", "Valor estimado (R$)", "moeda", "sum")]


async def rel_giro_estoque(F):
    """Saldo atual × consumo dos últimos 12 meses → meses de cobertura (parado = sem consumo)."""
    rows = await ajard_query("""
        SELECT p.codigo, p.descricao, p.unidade, p.familia_codigo, p.custo_medio,
               COALESCE(SUM(s.quantidade), 0) AS saldo,
               COALESCE((SELECT SUM((CASE WHEN m.tipo='entrada' THEN -1 ELSE 1 END) * m.quantidade)
                           FROM manutencao.movimentacoes m
                          WHERE m.peca_id = p.id AND (m.tipo IN ('saida','aplicacao_direta') OR (m.tipo='entrada' AND m.ot_id IS NOT NULL))
                            AND m.criado_em >= now() - interval '365 days'), 0) AS consumo_12m,
               (SELECT MAX(m.criado_em) FROM manutencao.movimentacoes m WHERE m.peca_id = p.id AND m.tipo IN ('saida','aplicacao_direta')) AS ultima_saida
          FROM manutencao.pecas p LEFT JOIN manutencao.estoque s ON s.peca_id = p.id
         WHERE p.ativo = true
         GROUP BY p.id, p.codigo, p.descricao, p.unidade, p.familia_codigo, p.custo_medio
         HAVING COALESCE(SUM(s.quantidade),0) > 0 OR COALESCE((SELECT SUM(m.quantidade) FROM manutencao.movimentacoes m WHERE m.peca_id = p.id AND m.tipo IN ('saida','aplicacao_direta') AND m.criado_em >= now() - interval '365 days'),0) > 0
         ORDER BY p.codigo""")
    out = []
    for r in rows:
        saldo, cons = _f(r["saldo"]), _f(r["consumo_12m"])
        mensal = cons / 12.0
        cob = (saldo / mensal) if mensal > 0 else None
        out.append({"item": r["codigo"], "descricao": r["descricao"], "familia": r["familia_codigo"], "unidade": r["unidade"],
                    "saldo": _r2(saldo), "valor_saldo": _r2(saldo * _f(r["custo_medio"])), "consumo_12m": _r2(cons), "consumo_mes": _r2(mensal),
                    "cobertura_meses": (_r2(cob) if cob is not None else None),
                    "situacao": ("Parado (sem consumo 12 m)" if saldo > 0 and cons <= 0 else "Cobertura > 12 m" if cob and cob > 12 else "Falta (sem saldo)" if saldo <= 0 else "Normal"),
                    "ultima_saida": r["ultima_saida"]})
    return out

COL_GIRO = [("item", "Item", "txt"), ("descricao", "Descrição", "txt"), ("familia", "Família", "txt"), ("unidade", "Un.", "txt"),
            ("saldo", "Saldo", "num", "sum"), ("valor_saldo", "Valor em estoque (R$)", "moeda", "sum"), ("consumo_12m", "Consumo 12 m", "num", "sum"),
            ("consumo_mes", "Consumo/mês", "num"), ("cobertura_meses", "Cobertura (meses)", "num"), ("situacao", "Situação", "txt"), ("ultima_saida", "Última saída", "datahora")]


# ═════════════════════════════ UTILIZAÇÃO ═════════════════════════════════
async def rel_utilizacao(F):
    await _garantir_leituras()
    params = []
    we = F.where_eq(params)
    rows = await ajard_query(f"""
        SELECT e.codigo, e.descricao, e.sistema_codigo, e.medicao, date_trunc('month', v.data)::date AS mes,
               MIN(v.leitura) AS ini, MAX(v.leitura) AS fim, COUNT(*)::int AS registros, COUNT(DISTINCT v.data::date)::int AS dias
          FROM operacional.v_leituras v JOIN operacional.equipamentos e ON e.id = v.equipamento_id
         WHERE v.data >= %s AND v.data < %s AND {we}
         GROUP BY e.codigo, e.descricao, e.sistema_codigo, e.medicao, 5 ORDER BY e.codigo, 5""", (F.de, F.ate + _td(days=1), *params))
    out = []
    for r in rows:
        op = _f(r["fim"]) - _f(r["ini"]) if r["registros"] > 1 else 0.0
        out.append({"objeto": r["codigo"], "descricao": r["descricao"], "sistema": r["sistema_codigo"], "mes": r["mes"],
                    "unidade": "km" if r["medicao"] == "km" else "h", "leitura_ini": _r2(r["ini"]), "leitura_fim": _r2(r["fim"]),
                    "operadas": _r2(op), "dias_com_registro": r["dias"], "media_dia": _r2(op / r["dias"]) if r["dias"] else None, "registros": r["registros"]})
    return out

COL_UTIL = [("objeto", "Objeto", "txt"), ("descricao", "Descrição", "txt"), ("sistema", "Sistema", "txt"), ("mes", "Mês", "data"), ("unidade", "Un.", "txt"),
            ("leitura_ini", "Leitura inicial", "num"), ("leitura_fim", "Leitura final", "num"), ("operadas", "Operadas", "num", "sum"),
            ("dias_com_registro", "Dias c/ registro", "int"), ("media_dia", "Média/dia (FMD)", "num"), ("registros", "Registros", "int", "sum")]


async def rel_pontos(F):
    params = []
    we = F.where_eq(params)
    rows = await ajard_query(f"""
        SELECT e.codigo AS objeto, e.descricao AS obj_desc, e.sistema_codigo, p.codigo, p.descricao, p.unidade, p.leitura_atual, p.data_leitura,
               p.limiar_atencao, p.limiar_urgente, p.limiar_maximo
          FROM manutencao.pontos_controle p JOIN operacional.equipamentos e ON e.id = p.equipamento_id
         WHERE p.ativo = true AND {we} ORDER BY e.codigo, p.codigo""", tuple(params))
    out = []
    for r in rows:
        leit = _f(r["leitura_atual"]) if r["leitura_atual"] is not None else None
        st = "Em dia"
        if leit is not None:
            if r["limiar_maximo"] is not None and leit >= _f(r["limiar_maximo"]): st = "🔴 Máximo"
            elif r["limiar_urgente"] is not None and leit >= _f(r["limiar_urgente"]): st = "🟠 Urgente"
            elif r["limiar_atencao"] is not None and leit >= _f(r["limiar_atencao"]): st = "🟡 Atenção"
        out.append({"objeto": r["objeto"], "obj_desc": r["obj_desc"], "sistema": r["sistema_codigo"], "ponto": r["codigo"], "descricao": r["descricao"] or "",
                    "unidade": r["unidade"] or "", "leitura": leit, "data": r["data_leitura"],
                    "atencao": _f(r["limiar_atencao"]) if r["limiar_atencao"] is not None else None,
                    "urgente": _f(r["limiar_urgente"]) if r["limiar_urgente"] is not None else None,
                    "maximo": _f(r["limiar_maximo"]) if r["limiar_maximo"] is not None else None, "situacao": st})
    return out

COL_PONTOS = [("objeto", "Objeto", "txt"), ("obj_desc", "Descrição", "txt"), ("sistema", "Sistema", "txt"), ("ponto", "Ponto", "txt"), ("descricao", "Descrição do ponto", "txt"),
              ("unidade", "Un.", "txt"), ("leitura", "Leitura", "num"), ("data", "Data", "datahora"), ("atencao", "🟡", "num"), ("urgente", "🟠", "num"), ("maximo", "🔴", "num"), ("situacao", "Situação", "txt")]


# ═════════════════════════════ CATÁLOGO ═══════════════════════════════════
# filtros: periodo · sistema · objeto · tipo_trabalho · setor · familia  (o front mostra só os aceitos)
RELATORIOS = {
    "custo-objeto":        {"grupo": "Custos", "titulo": "Custo de manutenção por objeto", "sub": "peças + mão de obra + outros das OTs abertas no período; R$ por hora/km operado", "fn": rel_custo_objeto, "cols": COL_CUSTO_OBJETO, "filtros": ["periodo", "sistema", "objeto", "tipo_trabalho", "setor"]},
    "custo-sistema":       {"grupo": "Custos", "titulo": "Custo por sistema", "sub": "onde o dinheiro vai, por ramo da organização", "fn": rel_custo_sistema, "cols": COL_CUSTO_SISTEMA, "filtros": ["periodo", "sistema", "tipo_trabalho", "setor"]},
    "custo-conta":         {"grupo": "Custos", "titulo": "Custo por plano de contas", "sub": "para o financeiro: lançamentos de peças, MO e outros por conta", "fn": rel_custo_conta, "cols": COL_CUSTO_CONTA, "filtros": ["periodo", "sistema", "objeto"]},
    "top-custo":           {"grupo": "Custos", "titulo": "Top 10 objetos por custo", "sub": "Pareto: quem concentra o gasto", "fn": rel_top_custo, "cols": COL_TOP_CUSTO, "filtros": ["periodo", "sistema", "tipo_trabalho"]},
    "backlog":             {"grupo": "Trabalhos", "titulo": "Backlog de OTs (em aberto)", "sub": "fotografia de hoje: tudo que não está encerrado, com idade e atraso", "fn": rel_backlog, "cols": COL_BACKLOG, "filtros": ["sistema", "objeto", "tipo_trabalho", "setor"]},
    "encerradas":          {"grupo": "Trabalhos", "titulo": "OTs encerradas no período", "sub": "produção da oficina: duração, TDM previsto × parada real, custo", "fn": rel_encerradas, "cols": COL_ENCERRADAS, "filtros": ["periodo", "sistema", "objeto", "tipo_trabalho", "setor"]},
    "corretiva-preventiva": {"grupo": "Trabalhos", "titulo": "Corretiva × Preventiva", "sub": "por objeto: a frota está sendo mantida ou apagando incêndio?", "fn": rel_corretiva_preventiva, "cols": COL_CXP, "filtros": ["periodo", "sistema", "objeto"]},
    "avarias":             {"grupo": "Trabalhos", "titulo": "Avarias por sintoma e causa", "sub": "Pareto das corretivas: o que mais quebra e por quê", "fn": rel_avarias, "cols": COL_AVARIAS, "filtros": ["periodo", "sistema", "objeto"]},
    "aderencia-fmp":       {"grupo": "Preventivas", "titulo": "Aderência das FMPs", "sub": "situação de cada ficha planejada (em dia · a vencer · vencida) — fotografia de hoje", "fn": rel_aderencia_fmp, "cols": COL_ADERENCIA, "filtros": ["sistema", "objeto"]},
    "preventivas-vencidas": {"grupo": "Preventivas", "titulo": "Preventivas vencidas e a vencer", "sub": "lista de ação imediata", "fn": rel_preventivas_vencidas, "cols": COL_ADERENCIA, "filtros": ["sistema", "objeto"]},
    "indisponibilidade":   {"grupo": "Disponibilidade", "titulo": "Indisponibilidade por objeto", "sub": "horas paradas, MTTR, MTBF e disponibilidade (jornada 8 h/dia)", "fn": rel_indisponibilidade, "cols": COL_INDISP, "filtros": ["periodo", "sistema", "objeto"]},
    "hh":                  {"grupo": "Mão de obra", "titulo": "HH por setor › função › pessoa", "sub": "horas e custo realizados × planejado por função", "fn": rel_hh, "cols": COL_HH, "filtros": ["periodo", "sistema", "objeto", "setor"]},
    "consumo-pecas":       {"grupo": "Materiais", "titulo": "Consumo de peças", "sub": "cada saída/aplicação (e devolução) com OT, objeto, conta e fornecedor", "fn": rel_consumo_pecas, "cols": COL_CONSUMO, "filtros": ["periodo", "sistema", "objeto", "familia"]},
    "consumo-familia":     {"grupo": "Materiais", "titulo": "Consumo por família", "sub": "peso de filtros, pneus, óleos… no gasto com peças", "fn": rel_consumo_familia, "cols": COL_CONSUMO_FAM, "filtros": ["periodo", "sistema", "objeto"]},
    "estoque-baixo":       {"grupo": "Materiais", "titulo": "Estoque baixo / sugestão de compra", "sub": "itens abaixo do mínimo com quantidade e valor sugeridos", "fn": rel_estoque_baixo, "cols": COL_ESTOQUE_BAIXO, "filtros": []},
    "giro-estoque":        {"grupo": "Materiais", "titulo": "Giro de estoque", "sub": "saldo × consumo 12 meses: itens parados e cobertura", "fn": rel_giro_estoque, "cols": COL_GIRO, "filtros": []},
    "utilizacao":          {"grupo": "Utilização", "titulo": "Horas/km por objeto e mês", "sub": "quanto cada máquina rodou (base do R$/h e do FMD)", "fn": rel_utilizacao, "cols": COL_UTIL, "filtros": ["periodo", "sistema", "objeto"]},
    "pontos":              {"grupo": "Utilização", "titulo": "Pontos de leitura", "sub": "situação dos pontos com limiares 🟡🟠🔴", "fn": rel_pontos, "cols": COL_PONTOS, "filtros": ["sistema", "objeto"]},
}
_ORDEM_GRUPOS = ["Custos", "Trabalhos", "Preventivas", "Disponibilidade", "Mão de obra", "Materiais", "Utilização"]


def _cols_json(cols):
    return [{"k": c[0], "t": c[1], "tipo": c[2], "soma": (len(c) > 3 and c[3] == "sum")} for c in cols]


def _totais(cols, linhas):
    t = {}
    for c in cols:
        if len(c) > 3 and c[3] == "sum":
            t[c[0]] = round(sum(_f(l.get(c[0])) for l in linhas), 2)
    return t


def _json_val(v):
    if isinstance(v, (_dt, _date)):
        return v.isoformat()
    return v


@router.get("/manutencao/api/relatorios")
async def catalogo(_auth=Depends(verificar_manutencao)):
    grupos = []
    for g in _ORDEM_GRUPOS:
        itens = [{"nome": k, "titulo": v["titulo"], "sub": v["sub"], "filtros": v["filtros"]} for k, v in RELATORIOS.items() if v["grupo"] == g]
        if itens:
            grupos.append({"grupo": g, "itens": itens})
    return {"grupos": grupos}


@router.get("/manutencao/api/relatorios/{nome}")
async def relatorio(nome: str, request: Request, _auth=Depends(verificar_manutencao)):
    cfg = RELATORIOS.get(nome)
    if not cfg:
        raise HTTPException(status_code=404, detail="Relatório desconhecido")
    await _prep()
    F = _F(request.query_params)
    linhas = await cfg["fn"](F)
    cols = cfg["cols"]
    formato = (request.query_params.get("formato") or "").lower()
    if formato == "xlsx":
        return _xlsx_response(cfg["titulo"], F.rotulo("periodo" in cfg["filtros"]), _cols_json(cols), linhas, nome, _emitente(_auth))
    if formato == "pdf":
        return _pdf_response(cfg["titulo"], F.rotulo("periodo" in cfg["filtros"]), _cols_json(cols), linhas, _totais(cols, linhas), nome, _emitente(_auth))
    return {"nome": nome, "titulo": cfg["titulo"], "sub": cfg["sub"], "filtros": F.rotulo("periodo" in cfg["filtros"]),
            "colunas": _cols_json(cols), "linhas": [{k: _json_val(v) for k, v in l.items()} for l in linhas], "totais": _totais(cols, linhas)}


# ═════════════════════════════ EXCEL ══════════════════════════════════════
_FMT = {"int": "#,##0", "num": "#,##0.00", "moeda": '"R$" #,##0.00', "pct": "0.0%", "data": "dd/mm/yyyy", "datahora": "dd/mm/yyyy hh:mm"}


def _coagir(v, tipo):
    """valor da tela → célula do Excel com o tipo certo"""
    if v is None or v == "":
        return None
    if tipo in ("int", "num", "moeda", "pct"):
        if isinstance(v, (int, float)):
            return v
        s = str(v).replace("R$", "").replace("%", "").strip()
        s = s.replace(".", "").replace(",", ".") if ("," in s) else s
        try:
            return float(s) if tipo != "int" else int(float(s))
        except ValueError:
            return str(v)
    if tipo in ("data", "datahora"):
        if isinstance(v, _dt):
            # horário de Brasília na planilha (o banco guarda em UTC)
            if v.tzinfo is not None:
                v = v.astimezone(_tz(_td(hours=-3)))
            return v.replace(tzinfo=None, microsecond=0)
        if isinstance(v, _date):
            return v
        s = str(v)
        try:
            if len(s) >= 19 and s[10] in "T ":
                return _dt.fromisoformat(s[:19])
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s[:10]):
                return _date.fromisoformat(s[:10])
            if re.fullmatch(r"\d{2}/\d{2}/\d{4}.*", s):
                d, m, a = s[:10].split("/")
                return _date(int(a), int(m), int(d))
        except ValueError:
            pass
        return s
    return str(v)



# ── identidade documental Garra (skill garra-identidade-documental) ─────────
AZUL, LARANJA, CINZA, CINZA_TXT = "001F4D", "FF9500", "F2F2F2", "999999"
EMPRESA = "GARRA TERRAPLENAGEM E CAÇAMBAS"
_LOGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "core", "logo-garra.png")


def _emitente(auth):
    return (auth or {}).get("nome") or (auth or {}).get("login") or ""


def _br(v, tipo):
    """valor → texto pt-BR para o PDF (o Excel recebe o valor cru tipado)"""
    v = _coagir(v, tipo)
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if tipo == "int":
        return f"{int(v):,}".replace(",", ".")
    if tipo in ("num", "moeda", "pct"):
        if tipo == "pct":
            v = v * 100
        t = f"{v:,.{1 if tipo == 'pct' else 2}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return ("R$ " + t) if tipo == "moeda" else (t + "%" if tipo == "pct" else t)
    if tipo == "datahora":
        return v.strftime("%d/%m/%Y %H:%M")
    if tipo == "data":
        return v.strftime("%d/%m/%Y")
    return str(v)


def _subtitulo(filtros, n, emitente):
    return f"{filtros} · {n} linha(s) · emitido em {_dt.now().strftime('%d/%m/%Y %H:%M')}" + (f" por {emitente}" if emitente else "")

def _xlsx_bytes(titulo, filtros, colunas, linhas, emitente=""):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    from openpyxl.drawing.image import Image as XImg
    wb = Workbook(); ws = wb.active
    ws.title = re.sub(r"[\[\]\*\?/\\:]", "-", titulo)[:31] or "Relatório"
    ncol = max(len(colunas), 4)
    fim_col = get_column_letter(ncol)
    # ── cabeçalho Garra: logotipo (A1:A3) + empresa / título / subtítulo (B..) ──
    try:
        img = XImg(_LOGO); img.width, img.height = 110, 52
        ws.add_image(img, "A1")
    except Exception as e:
        print(f"[Relatórios] logotipo no Excel falhou: {e}")
    ws.column_dimensions["A"].width = 18
    ws.row_dimensions[1].height = 22; ws.row_dimensions[2].height = 18; ws.row_dimensions[3].height = 14
    ws.merge_cells(f"B1:{fim_col}1"); ws.merge_cells(f"B2:{fim_col}2"); ws.merge_cells(f"B3:{fim_col}3")
    ws["B1"] = EMPRESA; ws["B1"].font = Font(bold=True, size=14, color=AZUL); ws["B1"].alignment = Alignment(vertical="center")
    ws["B2"] = f"Relatório de Manutenção — {titulo}"; ws["B2"].font = Font(bold=True, size=11, color=AZUL); ws["B2"].alignment = Alignment(vertical="center")
    ws["B3"] = _subtitulo(filtros, len(linhas), emitente); ws["B3"].font = Font(size=8, color="64748B"); ws["B3"].alignment = Alignment(vertical="center")
    for col in range(1, ncol + 1):
        ws.cell(row=3, column=col).border = Border(bottom=Side(style="medium", color=LARANJA))
    hdr = 5
    fino = Side(style="thin", color="CBD5E1")
    for j, c in enumerate(colunas, 1):
        cell = ws.cell(row=hdr, column=j, value=c["t"])
        cell.font = Font(bold=True, color="FFFFFF"); cell.fill = PatternFill("solid", fgColor=AZUL)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True); cell.border = Border(bottom=Side(style="thin", color=LARANJA))
    zebra = PatternFill("solid", fgColor=CINZA)
    for i, l in enumerate(linhas, hdr + 1):
        for j, c in enumerate(colunas, 1):
            v = _coagir(l.get(c["k"]), c["tipo"])
            cell = ws.cell(row=i, column=j, value=v)
            if c["tipo"] in _FMT and v is not None and not isinstance(v, str):
                cell.number_format = _FMT[c["tipo"]]
            if c["tipo"] in ("int", "num", "moeda", "pct"):
                cell.alignment = Alignment(horizontal="right")
            cell.border = Border(bottom=fino)
            if (i - hdr) % 2 == 0:
                cell.fill = zebra
    fim = hdr + len(linhas)
    if linhas and any(c.get("soma") for c in colunas):
        tr = fim + 1
        ws.cell(row=tr, column=1, value="Total").font = Font(bold=True, color=AZUL)
        for j, c in enumerate(colunas, 1):
            ws.cell(row=tr, column=j).border = Border(top=Side(style="thin", color=LARANJA))
            if c.get("soma"):
                L = get_column_letter(j)
                cell = ws.cell(row=tr, column=j, value=f"=SUBTOTAL(9,{L}{hdr+1}:{L}{fim})")
                cell.font = Font(bold=True, color=AZUL); cell.number_format = _FMT.get(c["tipo"], "#,##0.00"); cell.alignment = Alignment(horizontal="right")
                cell.fill = PatternFill("solid", fgColor="FFF7ED")
    ws.freeze_panes = ws.cell(row=hdr + 1, column=1)
    if linhas:
        ws.auto_filter.ref = f"A{hdr}:{get_column_letter(len(colunas))}{fim}"
    for j, c in enumerate(colunas, 1):
        larg = max([len(str(c["t"]))] + [len(str(_coagir(l.get(c["k"]), "txt") or "")) for l in linhas[:500]])
        ws.column_dimensions[get_column_letter(j)].width = max(ws.column_dimensions[get_column_letter(j)].width or 0, min(max(8, larg + 2), 48))
    ws.row_dimensions[hdr].height = 30
    # ── impressão: paisagem, cabe na largura, cabeçalho repete, rodapé Garra ──
    ws.page_setup.orientation = "landscape"; ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1; ws.page_setup.fitToHeight = 0; ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = f"1:{hdr}"
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4; ws.page_margins.top = 0.5; ws.page_margins.bottom = 0.6
    ws.oddFooter.left.text = f"Garra Sistemas — Manutenção · {titulo}"; ws.oddFooter.left.size = 8
    ws.oddFooter.center.text = f"Emitido em {_dt.now().strftime('%d/%m/%Y %H:%M')}" + (f" por {emitente}" if emitente else ""); ws.oddFooter.center.size = 8
    ws.oddFooter.right.text = "Página &P de &N"; ws.oddFooter.right.size = 8
    bio = io.BytesIO(); wb.save(bio); bio.seek(0)
    return bio


# ═════════════════════════════ PDF ════════════════════════════════════════
def _pdf_bytes(titulo, filtros, colunas, linhas, totais=None, emitente=""):
    """PDF no padrão documental Garra: moldura laranja fina nas 4 laterais,
    cabeçalho com logotipo + empresa, tabela azul #001F4D com zebra, rodapé
    com emissão/emitente e "Página X de Y". Paisagem quando a tabela é larga."""
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.pdfgen import canvas as _cv
    from xml.sax.saxutils import escape as _x

    azul, laranja, cinza = colors.HexColor("#" + AZUL), colors.HexColor("#" + LARANJA), colors.HexColor("#" + CINZA)
    largo = len(colunas) > 6 or sum(min(max(len(str(c["t"])), max([len(str(_br(l.get(c["k"]), c["tipo"]))) for l in linhas[:300]] or [0])), 40) for c in colunas) > 110
    pagesize = landscape(A4) if largo else A4
    W, H = pagesize
    mx, topo, base = 12 * mm, 30 * mm, 16 * mm
    sub = _subtitulo(filtros, len(linhas), emitente)
    emissao = f"Emitido em {_dt.now().strftime('%d/%m/%Y %H:%M')}" + (f" por {emitente}" if emitente else "")

    class _Canvas(_cv.Canvas):
        """desenha moldura, cabeçalho e rodapé em cada página, com total de páginas"""
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self._paginas = []
        def showPage(self):
            self._paginas.append(dict(self.__dict__)); self._startPage()
        def save(self):
            n = len(self._paginas)
            for st in self._paginas:
                self.__dict__.update(st); self._moldura(n); super().showPage()
            super().save()
        def _moldura(self, n):
            c = self
            c.setStrokeColor(laranja); c.setLineWidth(1)
            c.rect(6 * mm, 6 * mm, W - 12 * mm, H - 12 * mm)
            # cabeçalho
            try:
                c.drawImage(_LOGO, mx, H - topo + 6 * mm, width=38 * mm, height=18 * mm, preserveAspectRatio=True, anchor="w", mask="auto")
            except Exception:
                pass
            c.setFillColor(azul); c.setFont("Helvetica-Bold", 13)
            c.drawCentredString(mx + 42 * mm + (W - 2 * mx - 42 * mm) / 2, H - topo + 15 * mm, EMPRESA)
            c.setFont("Helvetica-Bold", 10)
            c.drawCentredString(mx + 42 * mm + (W - 2 * mx - 42 * mm) / 2, H - topo + 9.5 * mm, "RELATÓRIO DE MANUTENÇÃO")
            c.setStrokeColor(azul); c.setLineWidth(0.8); c.line(mx, H - topo + 4 * mm, W - mx, H - topo + 4 * mm)
            # rodapé
            c.setStrokeColor(colors.HexColor("#CBD5E1")); c.setLineWidth(0.5); c.line(mx, base - 3 * mm, W - mx, base - 3 * mm)
            c.setFillColor(colors.HexColor("#" + CINZA_TXT)); c.setFont("Helvetica", 7)
            c.drawString(mx, base - 7 * mm, f"Garra Sistemas — Manutenção · {titulo}")
            c.drawCentredString(W / 2, base - 7 * mm, emissao)
            c.drawRightString(W - mx, base - 7 * mm, f"Página {c._pageNumber} de {n}")

    from reportlab.pdfbase.pdfmetrics import stringWidth as _sw
    st_t = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=13, textColor=azul, leading=16)
    st_s = ParagraphStyle("s", fontName="Helvetica", fontSize=7.5, textColor=colors.HexColor("#64748B"), leading=10)
    num = lambda t: t in ("int", "num", "moeda", "pct")
    tem_total = bool(linhas) and bool(totais)
    disp = W - 2 * mx
    pad = 6  # LEFTPADDING + RIGHTPADDING
    # ── larguras: cada coluna precisa ao menos da maior "palavra" (código,
    # data, valor — nunca quebram no meio); o que sobra vai proporcional ao
    # texto completo. Se nem o mínimo cabe, reduz a fonte (7.5 → 6).
    textos = [[_br(l.get(c["k"]), c["tipo"]) for c in colunas] for l in linhas[:300]]
    tot_txt = [(_br(totais[c["k"]], c["tipo"]) if tem_total and c["k"] in totais else "") for c in colunas]
    for fs in (7.5, 7.0, 6.5, 6.0):
        fb, fn = ("Helvetica-Bold", fs), ("Helvetica", fs)
        minimo, desejado = [], []
        for j, c in enumerate(colunas):
            cab = str(c["t"])
            palavras = [_sw(w, *fb) for w in cab.split()] or [0]
            vals = [textos[i][j] for i in range(len(textos))] + [tot_txt[j]]
            if num(c["tipo"]):   # número nunca quebra
                m = max(palavras + [_sw(v, *fb) for v in vals])
            else:                # texto quebra entre palavras, nunca no meio de uma
                m = max(palavras + [_sw(w, *fn) for v in vals for w in v.split()])
            d = max([_sw(cab, *fb)] + [_sw(v, *fn) for v in vals])
            minimo.append(min(m, 110) + pad); desejado.append(min(max(d, m), 230) + pad)
        if sum(minimo) <= disp:
            break
    if sum(desejado) <= disp:
        larguras = [d * disp / sum(desejado) for d in desejado]
    else:
        sobra = max(disp - sum(minimo), 0); extra = [d - m for d, m in zip(desejado, minimo)]; te = sum(extra) or 1
        larguras = [m + sobra * e / te for m, e in zip(minimo, extra)]
        if sum(larguras) > disp:  # nem o mínimo coube com fonte 6: encolhe tudo
            larguras = [w * disp / sum(larguras) for w in larguras]
    st_h = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=fs, textColor=colors.white, leading=fs + 1.5, alignment=1)
    st_c = ParagraphStyle("c", fontName="Helvetica", fontSize=fs, leading=fs + 1.5)
    st_r = ParagraphStyle("r", parent=st_c, alignment=2)
    dados = [[Paragraph(_x(str(c["t"])), st_h) for c in colunas]]
    for i in range(len(linhas)):
        l = linhas[i]
        dados.append([Paragraph(_x(_br(l.get(c["k"]), c["tipo"])), st_r if num(c["tipo"]) else st_c) for c in colunas])
    if tem_total:
        fila = []
        for j, c in enumerate(colunas):
            if j == 0:
                fila.append(Paragraph("<b>Total</b>", st_c))
            elif c["k"] in totais:
                fila.append(Paragraph("<b>" + _x(_br(totais[c["k"]], c["tipo"])) + "</b>", st_r))
            else:
                fila.append("")
        dados.append(fila)
    t = Table(dados, colWidths=larguras, repeatRows=1)
    estilo = [("BACKGROUND", (0, 0), (-1, 0), azul), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
              ("LINEBELOW", (0, 0), (-1, 0), 0.8, laranja), ("GRID", (0, 1), (-1, -1), 0.3, colors.HexColor("#CBD5E1")),
              ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
              ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    for i in range(2, len(dados) - (1 if tem_total else 0), 2):
        estilo.append(("BACKGROUND", (0, i), (-1, i), cinza))
    if tem_total:
        estilo += [("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#FFF7ED")), ("LINEABOVE", (0, -1), (-1, -1), 0.8, laranja)]
    t.setStyle(TableStyle(estilo))
    corpo = [Paragraph(_x(titulo), st_t), Paragraph(_x(sub), st_s), Spacer(1, 4 * mm), t]
    if not linhas:
        corpo.append(Spacer(1, 3 * mm)); corpo.append(Paragraph("Sem dados para os filtros escolhidos.", st_s))
    bio = io.BytesIO()
    doc = SimpleDocTemplate(bio, pagesize=pagesize, leftMargin=mx, rightMargin=mx, topMargin=topo, bottomMargin=base,
                            title=f"{titulo} — Garra Terraplenagem", author="Garra Sistemas")
    doc.build(corpo, canvasmaker=_Canvas)
    bio.seek(0)
    return bio


def _pdf_response(titulo, filtros, colunas, linhas, totais, nome, emitente=""):
    bio = _pdf_bytes(titulo, filtros, colunas, linhas, totais, emitente)
    arq = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-") or "relatorio"
    return StreamingResponse(bio, media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="garra-{arq}-{_date.today().isoformat()}.pdf"'})


def _xlsx_response(titulo, filtros, colunas, linhas, nome, emitente=""):
    bio = _xlsx_bytes(titulo, filtros, colunas, linhas, emitente)
    arq = re.sub(r"[^a-z0-9]+", "-", nome.lower()).strip("-") or "relatorio"
    return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": f'attachment; filename="garra-{arq}-{_date.today().isoformat()}.xlsx"'})


def _lista_da_tela(d):
    cols = d.get("colunas") or []
    linhas = d.get("linhas") or []
    if not cols:
        raise HTTPException(status_code=400, detail="Sem colunas")
    if len(linhas) > 50000:
        raise HTTPException(status_code=400, detail="Lista grande demais (máx. 50.000 linhas)")
    for c in cols:
        c.setdefault("tipo", "txt"); c.setdefault("t", c.get("k", ""))
    return d.get("titulo") or "Lista", d.get("filtros") or "Conforme a tela", cols, linhas


@router.post("/manutencao/api/exportar-xlsx")
async def exportar_xlsx(request: Request, _auth=Depends(verificar_manutencao)):
    """Qualquer lista da tela → Excel. body: {titulo, filtros?, colunas:[{k,t,tipo,soma?}], linhas:[{...}]}"""
    titulo, filtros, cols, linhas = _lista_da_tela(await request.json())
    return _xlsx_response(titulo, filtros, cols, linhas, titulo, _emitente(_auth))


@router.post("/manutencao/api/exportar-pdf")
async def exportar_pdf(request: Request, _auth=Depends(verificar_manutencao)):
    """Qualquer lista da tela → PDF no padrão Garra. Mesmo body do exportar-xlsx."""
    titulo, filtros, cols, linhas = _lista_da_tela(await request.json())
    totais = {}
    for c in cols:
        if c.get("soma"):
            vals = [_coagir(l.get(c["k"]), c["tipo"]) for l in linhas]
            totais[c["k"]] = sum(v for v in vals if isinstance(v, (int, float)))
    return _pdf_response(titulo, filtros, cols, linhas, totais, titulo, _emitente(_auth))
