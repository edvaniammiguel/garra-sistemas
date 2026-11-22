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


@teste("Materiais", "saída baixa o saldo e não deixa sair mais do que existe")
def saida():
    mv = lambda corpo: chamar("admin", "POST", "/manutencao/api/estoque/movimentar", corpo)
    status(mv({"tipo": "saida", "peca": COD, "quantidade": 3, "origem": EST["a1"], "observacao": "BAT saída avulsa"}), 200, "saída de 3")
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
