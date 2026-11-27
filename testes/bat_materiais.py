# Bateria — MATERIAIS: entrada por documento, custo médio, saída, estorno (21/11/2026)
from bat_base import teste, chamar, sql, sql1, status, igual, perto, q, Pular, TAG

EST = {}
COD = "BAT." + TAG


def _saldo(almox):
    linhas = status(chamar("admin", "GET", "/manutencao/api/estoque-saldos?peca_codigo=" + q(COD)), 200, "ler saldo")
    return sum(float(l["quantidade"] or 0) for l in linhas if l["almox"] == almox)


def _custo():
    r = status(chamar("admin", "GET", "/manutencao/api/pecas?busca=" + q(COD)), 200, "ler peça")
    it = [i for i in r["itens"] if i["codigo"] == COD]
    assert it, "peça da bateria não encontrada pela busca"
    return it[0]["custo_medio"]


def _entrada(numero, qtd, custo, acr=0, total=None):
    tot = round(qtd * custo + acr, 2) if total is None else total
    return chamar("admin", "POST", "/manutencao/api/entradas", {
        "documento_tipo": "01", "documento_numero": numero, "fornecedor_id": EST["forn"], "destino_tipo": "almox", "destino": EST["a1"],
        "itens": [{"peca": COD, "quantidade": qtd, "custo_unitario": custo}], "acrescimos": acr, "desconto": 0, "total_nf": tot})


@teste("Materiais", "peça nova entra no catálogo e é achada pela busca")
def cadastrar_peca():
    sql("update manutencao.pecas set ativo=false where codigo like 'BAT.%' and codigo<>$1", COD)
    f = sql1("select id::text as id from public.fornecedores order by nome limit 1")
    if not f:
        raise Pular("laboratório sem nenhum fornecedor cadastrado")
    EST["forn"] = f["id"]
    alm = status(chamar("admin", "GET", "/manutencao/api/almoxarifados"), 200, "almoxarifados")
    if len(alm) < 2:
        raise Pular("laboratório com menos de dois almoxarifados")
    EST["a1"], EST["a2"] = alm[0]["codigo"], alm[1]["codigo"]
    status(chamar("admin", "POST", "/manutencao/api/pecas",
                  {"codigo": COD, "descricao": "BAT filtro de teste", "unidade": "UN", "codigo_fabricante": "BATFAB" + TAG}), 200, "criar peça")
    r = status(chamar("admin", "GET", "/manutencao/api/pecas?busca=" + q("BATFAB" + TAG)), 200, "buscar pelo código do fabricante")
    assert any(i["codigo"] == COD for i in r["itens"]), "busca pelo código do fabricante não achou a peça"
    r = status(chamar("admin", "GET", "/manutencao/api/pecas?busca=" + q("filtro teste BAT")), 200, "buscar por palavras")
    assert any(i["codigo"] == COD for i in r["itens"]), "busca palavra por palavra não achou a peça"


@teste("Materiais", "entrada que não fecha com o total da nota é recusada")
def entrada_nao_fecha():
    status(_entrada("BAT" + TAG + "X", 10, 20.0, acr=10, total=999.99), 400, "entrada com total errado")
    igual(_saldo(EST["a1"]), 0.0, "saldo depois da entrada recusada")


@teste("Materiais", "entrada sobe o saldo e rateia o acréscimo no custo médio")
def entrada_ok():
    r = status(_entrada("BAT" + TAG + "A", 10, 20.0, acr=10), 200, "entrada 1")
    EST["lote1"] = r.get("lote") if isinstance(r, dict) else None
    igual(_saldo(EST["a1"]), 10.0, "saldo depois da entrada 1")
    perto(_custo(), 21.0, "custo médio depois da entrada 1 (20,00 + 1,00 de frete)")
    r = status(_entrada("BAT" + TAG + "B", 10, 31.0), 200, "entrada 2")
    EST["lote2"] = r.get("lote") if isinstance(r, dict) else None
    igual(_saldo(EST["a1"]), 20.0, "saldo depois da entrada 2")
    perto(_custo(), 26.0, "custo médio ponderado (10×21 + 10×31)")


@teste("Materiais", "saída baixa o saldo pelo custo médio (ignora preço digitado) e não deixa sair mais do que existe")
def saida():
    mv = lambda corpo: chamar("admin", "POST", "/manutencao/api/estoque/movimentar", corpo)
    # (26/11/2026) o preço não se digita na baixa: a tela trava o campo e o servidor ignora o que vier (vale o custo médio)
    status(mv({"tipo": "saida", "peca": COD, "quantidade": 3, "origem": EST["a1"], "custo_unitario": "999,99", "observacao": "BAT saída avulsa"}), 200, "saída de 3")
    m = sql1("""select m.custo_unitario from manutencao.movimentacoes m join manutencao.pecas p on p.id=m.peca_id
                 where p.codigo=$1 and m.tipo='saida' order by m.criado_em desc limit 1""", COD)
    perto(m["custo_unitario"], 26.0, "saída gravada pelo custo médio, não pelo preço enviado")
    igual(_saldo(EST["a1"]), 17.0, "saldo depois da saída")
    status(mv({"tipo": "saida", "peca": COD, "quantidade": 500, "origem": EST["a1"], "observacao": "BAT acima do saldo"}), (400, 409), "saída acima do saldo")
    igual(_saldo(EST["a1"]), 17.0, "saldo depois da saída recusada")
    perto(_custo(), 26.0, "custo médio não muda na saída")


@teste("Materiais", "transferência move o saldo entre almoxarifados")
def transferencia():
    status(chamar("admin", "POST", "/manutencao/api/estoque/movimentar",
                  {"tipo": "transferencia", "peca": COD, "quantidade": 5, "origem": EST["a1"], "destino": EST["a2"]}), 200, "transferir 5")
    igual(_saldo(EST["a1"]), 12.0, "saldo na origem")
    igual(_saldo(EST["a2"]), 5.0, "saldo no destino")


@teste("Materiais", "estorno desfaz a entrada inteira, com motivo")
def estorno():
    if not EST.get("lote2"):
        raise Pular("a entrada não devolveu o lote")
    status(chamar("admin", "POST", "/manutencao/api/entradas/estornar", {"lote": EST["lote2"], "motivo": ""}), 400, "estornar sem motivo")
    status(chamar("admin", "POST", "/manutencao/api/entradas/estornar", {"lote": EST["lote2"], "motivo": "BAT lançada em duplicidade"}), 200, "estornar")
    igual(_saldo(EST["a1"]) + _saldo(EST["a2"]), 7.0, "saldo total depois do estorno")
    status(chamar("admin", "POST", "/manutencao/api/entradas/estornar", {"lote": EST["lote2"], "motivo": "BAT de novo"}), (400, 404, 409), "estornar duas vezes")


@teste("Materiais", "entrada guarda a data da nota separada da data do lançamento, e recusa data futura")
def data_da_nota():
    import datetime as dt
    if not EST.get("a1"):
        raise Pular("o cenário de materiais não foi montado")
    hoje = dt.date.today()
    nota = hoje - dt.timedelta(days=12)
    base = {"documento_tipo": "01", "fornecedor_id": EST["forn"], "destino_tipo": "almox", "destino": EST["a1"],
            "itens": [{"peca": COD, "quantidade": 1, "custo_unitario": 10}], "acrescimos": 0, "desconto": 0, "total_nf": 10}
    amanha = (hoje + dt.timedelta(days=2)).isoformat()
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": "BAT-DT-F-" + TAG, "data_documento": amanha}), 400, "nota com data futura")
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": "BAT-DT-X-" + TAG, "data_documento": "11/11/2026"}), 400, "data fora do formato")
    igual(sql1("select count(*) as n from manutencao.movimentacoes where documento_numero like $1", "BAT-DT-_-" + TAG)["n"], 0, "nada gravado nas recusas")
    num = "BAT-DT-A-" + TAG
    r = status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": num, "data_documento": nota.isoformat()}), 200, "entrada com a data da nota")
    m = sql1("select data_documento, (criado_em at time zone 'America/Sao_Paulo')::date as lancada from manutencao.movimentacoes where documento_numero=$1", num)
    igual(m["data_documento"], nota, "data da nota gravada")
    assert m["lancada"] >= hoje - dt.timedelta(days=1), "a data do lançamento deveria ser a de agora"
    # o extrato acha a nota pelo dia dela, e não pelo dia em que foi lançada
    def _extrato(dia):
        e = status(chamar("admin", "GET", f"/manutencao/api/movimentacoes?de={dia}&ate={dia}&documento={q(num)}&limit=50"), 200, "extrato")
        return [i for i in e["itens"] if i["documento_numero"] == num]
    achou = _extrato(nota.isoformat())
    assert achou, "o extrato filtrado pelo dia da nota não trouxe a entrada"
    igual(str(achou[0]["data_documento"])[:10], nota.isoformat(), "o extrato devolve a data da nota")
    igual(_extrato(hoje.isoformat()), [], "o extrato do dia do lançamento não traz a nota de outro dia")
    # quem não informa a data (conferência do recebimento de OC) fica com o dia de hoje
    num2 = "BAT-DT-B-" + TAG
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": num2}), 200, "entrada sem data")
    d2 = sql1("select data_documento from manutencao.movimentacoes where documento_numero=$1", num2)["data_documento"]
    assert abs((d2 - hoje).days) <= 1, f"sem data informada deveria ficar com hoje, ficou {d2}"
    # desfaz as duas entradas do teste
    for n in (num, num2):
        lote = sql1("select lote::text as l from manutencao.movimentacoes where documento_numero=$1", n)["l"]
        status(chamar("admin", "POST", "/manutencao/api/entradas/estornar", {"lote": lote, "motivo": "BAT limpeza do teste de data"}), 200, "estornar a entrada do teste")


@teste("Materiais", "aplicação direta na OT não grava sem fornecedor")
def direta_exige_fornecedor():
    ot = sql1("select id::text as id from manutencao.ot order by criado_em desc limit 1")
    if not ot or not EST.get("forn"):
        raise Pular("sem OT ou sem fornecedor no cenário")
    base = {"documento_tipo": "03", "destino_tipo": "ot", "ot_id": ot["id"], "documento_numero": "BAT-SF-" + TAG,
            "itens": [{"peca": COD, "quantidade": 1, "custo_unitario": 10}], "acrescimos": 0, "desconto": 0}
    r = chamar("admin", "POST", "/manutencao/api/entradas", base)
    status(r, 400, "aplicação direta sem fornecedor")
    assert "fornecedor" in str(r[1]).lower(), f"a recusa não explica que falta o fornecedor: {r[1]}"
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "fornecedor_id": "00000000-0000-0000-0000-000000000000"}), 400,
           "aplicação direta com fornecedor que não existe")
    igual(sql1("select count(*) as n from manutencao.movimentacoes where documento_numero=$1", "BAT-SF-" + TAG)["n"], 0, "nada gravado nas recusas")


@teste("Materiais", "peça aplicada na OT guarda a data de cada notinha, mostra na lista e deixa corrigir")
def direta_data_da_nota():
    # (27/11/2026) OT com várias notas de datas diferentes: cada item aparece com o nº e a data da sua nota
    from datetime import date, timedelta
    ot = sql1("select id::text as id from manutencao.ot where ativo and status in ('aberta','em_andamento','programada') order by criado_em desc limit 1")
    if not ot or not EST.get("forn"):
        raise Pular("sem OT aberta ou sem fornecedor no cenário")
    hoje = date.today()
    d1, d2 = (hoje - timedelta(days=9)).isoformat(), (hoje - timedelta(days=3)).isoformat()
    base = {"documento_tipo": "03", "destino_tipo": "ot", "ot_id": ot["id"], "fornecedor_id": EST["forn"],
            "itens": [{"peca": COD, "quantidade": 1, "custo_unitario": 10}], "acrescimos": 0, "desconto": 0}
    n1, n2, n3 = "BAT-DN1-" + TAG, "BAT-DN2-" + TAG, "BAT-DN3-" + TAG
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": n2, "data_documento": d2}), 200, "nota de 3 dias atrás")
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": n1, "data_documento": d1}), 200, "nota de 9 dias atrás")
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": n3}), 200, "nota sem data (vale hoje)")
    # (27/11/2026) compra direta não grava sem número de documento (sem nota, a tela usa a data do dia como número)
    r = chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": "  "})
    status(r, 400, "aplicação direta sem número de documento")
    assert "documento" in str(r[1]).lower(), f"a recusa não explica que falta o documento: {r[1]}"
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": hoje.strftime("%d/%m/%Y") + " BAT-DN4-" + TAG}), 200, "a data do dia como documento")
    status(chamar("admin", "POST", "/manutencao/api/entradas", {**base, "documento_numero": "BAT-DNF-" + TAG,
                                                              "data_documento": (hoje + timedelta(days=1)).isoformat()}), 400, "nota com data futura")
    lista = [i for i in status(chamar("admin", "GET", f"/manutencao/api/ots/{ot['id']}/consumos"), 200, "peças da OT")["itens"] if i["documento"] in (n1, n2, n3)]
    igual([(i["documento"], i["data"][:10]) for i in lista], [(n1, d1), (n2, d2), (n3, hoje.isoformat())], "lista em ordem de data da nota, cada uma com a sua")
    assert all(i.get("lancado_em") for i in lista), "o dia do lançamento continua guardado junto"
    # corrigir a data de uma nota já lançada
    alvo = [i for i in lista if i["documento"] == n2][0]
    nova = (hoje - timedelta(days=20)).isoformat()
    rota = f"/manutencao/api/ots/{ot['id']}/consumos/{alvo['id']}"
    status(chamar("admin", "PATCH", rota, {"data_documento": nova}), 200, "corrigir a data da nota")
    status(chamar("admin", "PATCH", rota, {"data_documento": (hoje + timedelta(days=2)).isoformat()}), 400, "corrigir para data futura")
    status(chamar("admin", "PATCH", rota, {"documento_numero": ""}), 400, "apagar o número do documento de uma compra direta")
    depois = [i for i in status(chamar("admin", "GET", f"/manutencao/api/ots/{ot['id']}/consumos"), 200, "peças da OT")["itens"] if i["documento"] in (n1, n2, n3)]
    igual([i["documento"] for i in depois], [n2, n1, n3], "depois da correção a nota vai para o lugar certo na ordem")
    igual(depois[0]["data"][:10], nova, "data corrigida")
    sql("delete from manutencao.movimentacoes where documento_numero like $1", "BAT-DN%-" + TAG)
    sql("delete from manutencao.movimentacoes where documento_numero like $1", "%BAT-DN4-" + TAG)


@teste("Materiais", "peça com barra no código (W950/26) abre 'em quais equipamentos foi aplicada' sem 'Rota não encontrada'")
def peca_com_barra_equipamentos():
    # (26/11/2026) a rota nova nasceu sem :path — o código com barra quebrava o endereço e o servidor respondia 404
    for cod in ("BAT%2F950%2F26", "BATSEMBARRA"):
        st, r = chamar("admin", "GET", f"/manutencao/api/pecas/{cod}/equipamentos-aplicados")
        assert st == 200, f"código {cod}: esperado 200, veio {st} — {str(r)[:120]}"
