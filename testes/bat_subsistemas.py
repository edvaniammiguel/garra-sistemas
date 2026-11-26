# Bateria — RELATÓRIOS DE SUBSISTEMAS: posição, vida, trocas, custo e previsão de troca (26/11/2026)
# Monta uma máquina fictícia com três componentes e confere cada opção do campo "Mostrar" contra a conta feita à mão.
import urllib.request
from datetime import date, timedelta
from bat_base import teste, chamar, sql, sql1, status, igual, perto, equipamento_bat, token, URL

EST = {}
MAQ, P1, P2, P3 = "BAT-SB-MAQ", "BAT-SB-P1", "BAT-SB-P2", "BAT-SB-P3"


def _rel(visao, extra=""):
    return status(chamar("admin", "GET", f"/manutencao/api/relatorios/subsistemas?visao={visao}&equipamento_id={EST['maq']}{extra}"), 200, visao)


def _linha(r, comp):
    ls = [l for l in r["linhas"] if l["componente"] == comp]
    assert ls, f"{comp} não saiu em {r['titulo']}"
    return ls[0]


def _horimetro(v):
    sql("update operacional.equipamentos set horimetro_atual=$1 where id=$2::uuid", v, EST["maq"])


def _limpar():
    ids = [EST[k] for k in ("maq", "p1", "p2", "p3")]
    for i in ids:
        sql("delete from manutencao.ot_outros where ot_id in (select id from manutencao.ot where equipamento_id=$1::uuid)", i)
        sql("delete from manutencao.ot where equipamento_id=$1::uuid", i)
        sql("delete from manutencao.montagens where equipamento_id=$1::uuid or pai_id=$1::uuid", i)
        sql("delete from operacional.abastecimentos where equipamento_id=$1::uuid", i)
    for k in ("p1", "p2", "p3"):
        sql("update operacional.equipamentos set equipamento_pai=null, posicao=null where id=$1::uuid", EST[k])


@teste("Subsistemas", "posição atual, vida acumulada e histórico de trocas saem da montagem e desmontagem")
def posicao_vida_trocas():
    EST["maq"] = equipamento_bat(MAQ, "h")
    EST["p1"], EST["p2"], EST["p3"] = equipamento_bat(P1, "h"), equipamento_bat(P2, "h"), equipamento_bat(P3, "h")
    status(chamar("admin", "GET", f"/manutencao/api/equipamentos/{EST['maq']}/subsistemas"), 200, "abrir subsistemas (cria as tabelas)")
    _limpar()
    sql("update operacional.equipamentos set km_atual=null, vida_util_h=1000, valor_aquisicao=2000 where id=$1::uuid", EST["p1"])
    sql("update operacional.equipamentos set km_atual=null, vida_util_h=null, valor_aquisicao=800 where id=$1::uuid", EST["p2"])
    sql("update operacional.equipamentos set categoria='componente', vida_util_h=null, valor_aquisicao=null where id=$1::uuid", EST["p3"])
    sql("update operacional.equipamentos set km_atual=null where id=$1::uuid", EST["maq"])
    # P1 roda 500 h na posição DE e sai; P2 entra no lugar e já rodou 200 h; P3 nunca foi montado
    _horimetro(1000)
    status(chamar("admin", "POST", f"/manutencao/api/equipamentos/{EST['maq']}/montar", {"componente_id": EST["p1"], "posicao": "DE", "forcar": True}), 200, "montar P1")
    _horimetro(1500)
    status(chamar("admin", "POST", f"/manutencao/api/equipamentos/{EST['p1']}/desmontar", {}), 200, "desmontar P1")
    status(chamar("admin", "POST", f"/manutencao/api/equipamentos/{EST['maq']}/montar", {"componente_id": EST["p2"], "posicao": "DE", "forcar": True}), 200, "montar P2")
    _horimetro(1700)

    pos = _rel("posicao")
    igual(pos["visao"], "posicao", "visão devolvida")
    igual([l["componente"] for l in pos["linhas"]], [P2], "na máquina hoje só está o P2")
    l = pos["linhas"][0]
    igual(l["posicao"], "DE", "posição"); perto(l["rodado"], 200, "rodado nesta montagem"); igual(l["unidade"], "h", "unidade")
    assert "Fotografia de hoje" in pos["filtros"], "posição atual não tem período"
    soltos = status(chamar("admin", "GET", "/manutencao/api/relatorios/subsistemas?visao=posicao"), 200, "posição sem filtro")
    s3 = _linha(soltos, P3)
    assert s3["maquina"].startswith("— solto"), "componente nunca montado devia sair como solto"
    assert _linha(soltos, P1)["maquina"].startswith("— solto"), "P1 desmontado devia sair como solto"

    vida = status(chamar("admin", "GET", "/manutencao/api/relatorios/subsistemas?visao=vida"), 200, "vida")
    v1 = _linha(vida, P1)
    perto(v1["vida"], 500, "vida do P1"); perto(v1["consumido"], 0.5, "% consumido do P1"); perto(v1["custo_unit"], 4, "R$/h do P1 (2000 ÷ 500)")
    igual(v1["situacao"], "solto", "situação do P1")
    v2 = _linha(vida, P2)
    perto(v2["vida"], 200, "vida do P2 (montagem em curso conta)"); igual(v2["situacao"], "montado em " + MAQ, "situação do P2")

    # campo Objeto com o próprio componente (e não a máquina) mostra esse componente — antes vinha vazio
    def do_comp(visao, chave):
        return status(chamar("admin", "GET", f"/manutencao/api/relatorios/subsistemas?visao={visao}&equipamento_id={EST[chave]}"), 200, visao + " pelo componente")
    igual([l["componente"] for l in do_comp("vida", "p2")["linhas"]], [P2], "vida filtrando pelo próprio pneu montado")
    igual([l["maquina"] for l in do_comp("posicao", "p2")["linhas"]], [MAQ], "posição filtrando pelo próprio pneu")
    igual([l["componente"] for l in do_comp("vida", "p1")["linhas"]], [P1], "vida filtrando por componente solto")
    igual([l["componente"] for l in do_comp("trocas", "p1")["linhas"]], [P1], "trocas filtrando pelo componente")
    igual([l["componente"] for l in do_comp("previsao", "p2")["linhas"]], [P2], "previsão filtrando pelo próprio pneu")

    tr = _rel("trocas")
    assert "Período" in tr["filtros"], "histórico de trocas tem período"
    igual([(l["componente"], l["situacao"]) for l in tr["linhas"]], [(P1, "desmontado"), (P2, "montado")], "as duas montagens, em ordem")
    perto(tr["linhas"][0]["rodado"], 500, "rodado do P1"); perto(tr["linhas"][0]["leitura_desmontagem"], 1500, "leitura na desmontagem")
    perto(tr["totais"]["trocas"], 2, "nº de montagens")


@teste("Subsistemas", "custo por subsistema e previsão de troca (referência, ritmo, data e valor a reservar)")
def custo_previsao():
    ot = sql1("""insert into manutencao.ot (equipamento_id, descricao, status, ativo, data_abertura)
                 values ($1::uuid, 'BAT recapagem', 'concluida', true, now()) returning id::text as id""", EST["p2"])["id"]
    sql("insert into manutencao.ot_outros (ot_id, descricao, valor, ativo) values ($1::uuid,'Recapagem',120,true)", ot)
    cu = _rel("custo")
    igual([l["componente"] for l in cu["linhas"]], [P2], "só sai quem teve OT no período")
    perto(cu["linhas"][0]["total"], 120, "custo do P2"); perto(cu["linhas"][0]["outros"], 120, "outros do P2")
    perto(cu["linhas"][0]["aquisicao"], 800, "aquisição do P2")

    # sem leituras da máquina não há ritmo: o relatório diz isso em vez de inventar data
    pv = _linha(_rel("previsao"), P2)
    perto(pv["referencia"], 500, "referência = quanto rodou o anterior na mesma posição"); perto(pv["resta"], 300, "falta")
    assert pv["situacao"].startswith("sem ritmo"), "sem leituras devia sair 'sem ritmo', veio " + pv["situacao"]
    igual(pv["data_prevista"], None, "sem ritmo não tem data"); perto(pv["reservar"], 0, "sem data não entra no valor a reservar")
    # máquina rodando 10 h/dia (1300 → 1700 em 40 dias): faltam 300 h → 30 dias
    hoje = date.today()
    for dias, leitura in ((40, 1300), (0, 1700)):
        sql("""insert into operacional.abastecimentos (equipamento_id, data, litros, valor_total, leitura, destino_tipo, ativo)
               values ($1::uuid, (now() - make_interval(days => $2)), 10, 60, $3, 'equipamento', true)""", EST["maq"], dias, leitura)
    pv = _linha(_rel("previsao"), P2)
    perto(pv["ritmo"], 10, "ritmo da máquina"); igual(pv["dias"], 30, "dias até a troca")
    igual(pv["situacao"], "até 30 dias", "situação")
    assert abs((date.fromisoformat(pv["data_prevista"][:10]) - (hoje + timedelta(days=30))).days) <= 1, "data prevista fora do esperado"
    perto(pv["reservar"], 800, "valor a reservar em 90 dias")
    # vida útil cadastrada manda na referência; passou dela → vencido
    sql("update operacional.equipamentos set vida_util_h=150 where id=$1::uuid", EST["p2"])
    pv = _linha(_rel("previsao"), P2)
    igual(pv["fonte"], "vida útil cadastrada", "origem da referência"); igual(pv["situacao"], "vencido", "200 h rodadas com vida útil de 150 h")
    # componente sem vida útil e sem anterior na posição não ganha prazo inventado
    sql("update operacional.equipamentos set vida_util_h=null where id=$1::uuid", EST["p2"])
    status(chamar("admin", "POST", f"/manutencao/api/equipamentos/{EST['maq']}/montar", {"componente_id": EST["p3"], "posicao": "TE", "forcar": True}), 200, "montar P3 em posição nova")
    igual(_linha(_rel("previsao"), P3)["situacao"], "sem referência", "P3 em posição sem histórico")

    for fmt, assinatura in (("xlsx", b"PK"), ("pdf", b"%PDF")):
        req = urllib.request.Request(URL + f"/manutencao/api/relatorios/subsistemas?visao=previsao&equipamento_id={EST['maq']}&formato={fmt}",
                                     headers={"Authorization": "Bearer " + token("admin")})
        assert urllib.request.urlopen(req).read()[:4].startswith(assinatura), f"arquivo {fmt} não veio no formato esperado"
    cat = status(chamar("admin", "GET", "/manutencao/api/relatorios"), 200, "catálogo")
    grupo = [g for g in cat["grupos"] if g["grupo"] == "Subsistemas"]
    assert grupo and len(grupo[0]["itens"]) == 1, "Subsistemas devia ser um item só no menu"
    igual([g["grupo"] for g in cat["grupos"]][:2], ["Custos", "Subsistemas"], "Subsistemas vem logo abaixo de Custos no menu")
    igual([v["v"] for v in grupo[0]["itens"][0]["visoes"]], ["posicao", "vida", "trocas", "custo", "previsao"], "opções do campo Mostrar")
    status(chamar("op1", "GET", "/manutencao/api/relatorios/subsistemas"), 403, "operador sem Manutenção pedindo o relatório")
    _limpar()
