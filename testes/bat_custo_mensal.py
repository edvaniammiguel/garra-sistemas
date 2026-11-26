# Bateria — CUSTO MENSAL POR MÁQUINA: combustível + manutenção + pneus (26/11/2026)
# Monta uma máquina fictícia com lançamentos conhecidos e confere os dois relatórios contra a conta feita à mão.
import urllib.request
from bat_base import teste, chamar, sql, sql1, status, igual, perto, equipamento_bat, token, URL, TAG

EST = {}
COD = "BAT-CM-01"
PER = "de=2025-01-01&ate=2025-03-31"          # período fechado no passado: o resultado não muda com o dia do teste


def _limpar(eid):
    sql("delete from manutencao.movimentacoes where ot_id in (select id from manutencao.ot where equipamento_id=$1::uuid)", eid)
    sql("delete from manutencao.ot_outros where ot_id in (select id from manutencao.ot where equipamento_id=$1::uuid)", eid)
    sql("delete from manutencao.ot_mao_obra where ot_id in (select id from manutencao.ot where equipamento_id=$1::uuid)", eid)
    sql("delete from manutencao.ot where equipamento_id=$1::uuid", eid)
    sql("delete from operacional.abastecimentos where equipamento_id=$1::uuid", eid)


def _ot(eid, quando, status_ot="concluida"):
    return sql1("""insert into manutencao.ot (equipamento_id, descricao, status, ativo, data_abertura)
                   values ($1::uuid, 'BAT custo mensal', $2, true, $3::text::timestamptz) returning id::text as id""",
                eid, status_ot, quando)["id"]


def _peca(codigo, descricao):
    p = sql1("select id::text as id from manutencao.pecas where codigo=$1", codigo)
    if p:
        return p["id"]
    return sql1("insert into manutencao.pecas (codigo, descricao, unidade, ativo) values ($1,$2,'UN',true) returning id::text as id",
                codigo, descricao)["id"]


def _rel(nome, extra=""):
    return status(chamar("admin", "GET", f"/manutencao/api/relatorios/{nome}?{PER}&equipamento_id={EST['eid']}{extra}"), 200, nome)


@teste("Custo mensal", "visão mês a mês separa combustível, manutenção e pneus sem contar duas vezes")
def mes_a_mes():
    eid = EST["eid"] = equipamento_bat(COD, "h")
    _limpar(eid)
    # combustível: jan 1000 + 500 · fev 800 · galão (não conta) · excluído (não conta)
    for quando, litros, valor, destino, ativo in (
            ("2025-01-10 12:00-03", 160, 1000, "equipamento", True), ("2025-01-25 12:00-03", 80, 500, "equipamento", True),
            ("2025-02-05 12:00-03", 130, 800, "equipamento", True), ("2025-02-06 12:00-03", 50, 300, "galao", True),
            ("2025-02-07 12:00-03", 99, 999, "equipamento", False)):
        sql("""insert into operacional.abastecimentos (equipamento_id, data, litros, valor_total, destino_tipo, ativo)
               values ($1::uuid, $2::text::timestamptz, $3, $4, $5, $6)""", eid, quando, litros, valor, destino, ativo)
    # janeiro: filtro 2×50 + mão de obra 3h×40 + serviço 200  → manutenção 420 · pneu 1×900 + borracharia 150 → pneus 1050
    ot1 = _ot(eid, "2025-01-15 09:00-03")
    filtro, pneu = _peca("BAT.CM.FIL", "BAT filtro de teste"), _peca("BAT.CM.PN", "BAT PNEU 1000R20 de teste")
    sql("insert into manutencao.movimentacoes (tipo, peca_id, quantidade, custo_unitario, ot_id) values ('saida',$1::uuid,2,50,$2::uuid)", filtro, ot1)
    sql("insert into manutencao.movimentacoes (tipo, peca_id, quantidade, custo_unitario, ot_id) values ('aplicacao_direta',$1::uuid,1,900,$2::uuid)", pneu, ot1)
    sql("insert into manutencao.ot_mao_obra (ot_id, horas, custo_hora, ativo) values ($1::uuid,3,40,true)", ot1)
    sql("insert into manutencao.ot_outros (ot_id, descricao, valor, ativo) values ($1::uuid,'Solda no chassi',200,true)", ot1)
    sql("insert into manutencao.ot_outros (ot_id, descricao, valor, ativo) values ($1::uuid,'Borracharia - troca de pneu',150,true)", ot1)
    # março: só serviço 300 · OT cancelada não conta · último dia de março às 23h (Brasília) ainda é março
    ot3 = _ot(eid, "2025-03-31 23:00-03")
    sql("insert into manutencao.ot_outros (ot_id, descricao, valor, ativo) values ($1::uuid,'Torno',300,true)", ot3)
    otc = _ot(eid, "2025-03-10 09:00-03", "cancelada")
    sql("insert into manutencao.ot_outros (ot_id, descricao, valor, ativo) values ($1::uuid,'Não conta',5000,true)", otc)

    r = _rel("custo-objeto", "&visao=mes")
    igual(r["visao"], "mes", "visão devolvida")
    por_mes = {l["mes"][:7]: l for l in r["linhas"]}
    igual(sorted(por_mes), ["2025-01", "2025-02", "2025-03"], "meses do relatório")
    j, f, m = por_mes["2025-01"], por_mes["2025-02"], por_mes["2025-03"]
    perto(j["combustivel"], 1500, "combustível de janeiro"); perto(j["litros"], 240, "litros de janeiro")
    perto(j["manutencao"], 420, "manutenção de janeiro (sem pneus)"); perto(j["pneus"], 1050, "pneus de janeiro")
    perto(j["total"], 2970, "total de janeiro")
    perto(f["combustivel"], 800, "combustível de fevereiro (galão e excluído de fora)"); perto(f["total"], 800, "total de fevereiro")
    perto(m["manutencao"], 300, "manutenção de março (cancelada de fora; 31/03 23h é março)"); perto(m["combustivel"], 0, "março sem combustível")
    perto(r["totais"]["total"], 4070, "total geral do período")


@teste("Custo mensal", "média mensal (÷ meses desde o 1º lançamento) em Excel e PDF; custo por máquina é um item só no menu")
def media():
    r = _rel("custo-objeto", "&visao=media")
    igual(r["visao"], "media", "visão devolvida")
    igual(len(r["linhas"]), 1, "uma linha por máquina")
    igual(_rel("custo-medio-mensal")["linhas"], r["linhas"], "o nome antigo continua abrindo a média")
    igual(_rel("custo-mensal")["visao"], "mes", "o nome antigo do mês a mês continua abrindo")
    l = r["linhas"][0]
    igual(l["desde"][:10], "2025-01-10", "primeiro lançamento")
    meses = round(81 / 30.4375, 1)                     # 10/01 a 31/03 = 81 dias
    perto(l["meses"], meses, "meses contados")
    perto(l["total"], 4070, "total no período")
    perto(l["total_mes"], 4070 / (81 / 30.4375), "total por mês")
    perto(l["combustivel_mes"] + l["manutencao_mes"] + l["pneus_mes"], l["total_mes"], "as três médias somam o total", tol=0.02)
    perto(l["pneus"], 1050, "pneus no período")
    for fmt, assinatura in (("xlsx", b"PK"), ("pdf", b"%PDF")):
        req = urllib.request.Request(URL + f"/manutencao/api/relatorios/custo-objeto?{PER}&equipamento_id={EST['eid']}&visao=media&formato={fmt}",
                                     headers={"Authorization": "Bearer " + token("admin")})
        corpo = urllib.request.urlopen(req).read()
        assert corpo[:4].startswith(assinatura), f"arquivo {fmt} não veio no formato esperado"
    # um item só no menu de Custos para tudo o que é "por máquina"; sem escolher, abre como abria antes (manutenção no período)
    cat = status(chamar("admin", "GET", "/manutencao/api/relatorios"), 200, "catálogo")
    custos = {i["nome"]: i for g in cat["grupos"] if g["grupo"] == "Custos" for i in g["itens"]}
    for antigo in ("top-custo", "custo-mensal", "custo-medio-mensal"):
        assert antigo not in custos, f"{antigo} devia ser opção do campo Mostrar, não item do menu"
    vis = {v["v"]: v for v in custos["custo-objeto"]["visoes"]}
    igual(list(vis), ["total", "top10", "media", "mes"], "opções do campo Mostrar")
    assert "tipo_trabalho" in vis["total"]["filtros"] and "tipo_trabalho" not in vis["media"]["filtros"], "cada opção tem os seus filtros"
    assert "objeto" not in vis["top10"]["filtros"], "top 10 não filtra por objeto"
    assert not custos["custo-sistema"].get("visoes"), "custo por sistema não tem campo Mostrar"
    padrao = _rel("custo-objeto")
    igual(padrao["visao"], "total", "visão padrão")
    assert any(c["k"] == "n_corretivas" for c in padrao["colunas"]), "padrão devia ser o custo de manutenção no período"
    top = _rel("top-custo")
    igual(top["visao"], "top10", "o nome antigo do top 10 continua abrindo")
    assert any(c["k"] == "acum" for c in top["colunas"]), "top 10 sem a coluna de % acumulado"
    _limpar(EST["eid"])
    sql("delete from manutencao.pecas where codigo in ('BAT.CM.FIL','BAT.CM.PN')")
