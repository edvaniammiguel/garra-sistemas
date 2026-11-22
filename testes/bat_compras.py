# Bateria — COMPRAS: ordem de compra, recebimento, conferência e cotação (21/11/2026)
from bat_base import teste, chamar, sql, sql1, status, igual, perto, q, Pular, TAG

EST = {}
COD = "BAT.C" + TAG


def _saldo(almox):
    linhas = status(chamar("admin", "GET", "/manutencao/api/estoque-saldos?peca_codigo=" + q(COD)), 200, "ler saldo")
    return sum(float(l["quantidade"] or 0) for l in linhas if l["almox"] == almox)


def _oc(oc_id):
    return status(chamar("admin", "GET", f"/compras/api/ocs/{oc_id}"), 200, "ler OC")


@teste("Compras", "ordem de compra nasce em rascunho, com número e valor total")
def criar_oc():
    sql("update manutencao.pecas set ativo=false where codigo like 'BAT.C%' and codigo<>$1", COD)
    fs = [l["id"] for l in sql("select id::text as id from public.fornecedores order by nome limit 2")]
    if not fs:
        raise Pular("laboratório sem fornecedor cadastrado")
    EST["forn"] = fs
    setores = status(chamar("admin", "GET", "/compras/api/setores"), 200, "setores de compra")
    alm = status(chamar("admin", "GET", "/manutencao/api/almoxarifados"), 200, "almoxarifados")
    if not setores or not alm:
        raise Pular("laboratório sem setor de compra ou sem almoxarifado")
    EST["setor"], EST["alm"] = setores[0]["codigo"], alm[0]["codigo"]
    p = status(chamar("admin", "POST", "/manutencao/api/pecas", {"codigo": COD, "descricao": "BAT correia de teste", "unidade": "UN"}), 200, "criar peça")
    EST["peca"] = p["id"]
    status(chamar("admin", "POST", "/compras/api/ocs", {"fornecedor_id": fs[0], "itens": []}), 400, "OC sem setor")
    oc = status(chamar("admin", "POST", "/compras/api/ocs", {
        "setor_codigo": EST["setor"], "fornecedor_id": fs[0], "almoxarifado_codigo": EST["alm"], "observacao": "BAT OC de teste",
        "itens": [{"peca_id": EST["peca"], "descricao": "BAT correia de teste", "quantidade": 4, "unidade": "UN", "valor_unit": 25.0}]}), 200, "criar OC")
    EST["oc"] = oc["id"]
    igual(oc["status"], "rascunho", "estado inicial da OC")
    perto(oc["valor_total"], 100.0, "valor total (4 × 25,00)")
    assert str(oc.get("numero", "")).startswith("OC-"), "OC sem número"


@teste("Compras", "OC segue solicitar → aprovar → enviar, sem pular etapa")
def caminho_da_oc():
    oc = EST["oc"]
    status(chamar("admin", "POST", f"/compras/api/ocs/{oc}/receber", {"nf_numero": "BAT", "itens": []}), (400, 409), "receber OC ainda em rascunho")
    r = status(chamar("admin", "POST", f"/compras/api/ocs/{oc}/solicitar", {}), 200, "solicitar")
    if r.get("status") != "aprovada":
        status(chamar("admin", "POST", f"/compras/api/ocs/{oc}/aprovar", {"observacao": "BAT aprovado"}), 200, "aprovar")
    igual(_oc(oc)["status"], "aprovada", "estado depois da aprovação")
    status(chamar("admin", "POST", f"/compras/api/ocs/{oc}/aprovar", {}), 400, "aprovar duas vezes")
    status(chamar("admin", "POST", f"/compras/api/ocs/{oc}/enviar", {}), 200, "enviar ao fornecedor")
    igual(_oc(oc)["status"], "enviada", "estado depois de enviar")


@teste("Compras", "receber a OC não sobe o saldo: só a conferência do almoxarifado sobe")
def receber_e_conferir():
    oc = EST["oc"]
    item = _oc(oc)["itens"][0]["id"]
    antes = _saldo(EST["alm"])
    r = status(chamar("admin", "POST", f"/compras/api/ocs/{oc}/receber",
                      {"nf_numero": "BAT-NF-" + TAG, "itens": [{"item_id": item, "qtd_recebida": 4, "valor_unit": 25.0}]}), 200, "registrar recebimento")
    igual(r.get("status"), "recebida", "estado da OC recebida")
    igual(_saldo(EST["alm"]), antes, "saldo logo depois do recebimento (não pode subir ainda)")
    pend = [x for x in status(chamar("admin", "GET", "/manutencao/api/recebimentos"), 200, "recebimentos a conferir") if x["oc_id"] == oc]
    assert pend, "o recebimento não apareceu para conferência no almoxarifado"
    rid = pend[0]["id"]
    status(chamar("admin", "POST", f"/manutencao/api/recebimentos/{rid}/conferir", {"quantidade": 0}), 400, "conferir quantidade zero")
    status(chamar("admin", "POST", f"/manutencao/api/recebimentos/{rid}/conferir", {"quantidade": 3, "observacao": "BAT faltou 1 na caixa"}), 200, "conferir 3 de 4")
    igual(_saldo(EST["alm"]), antes + 3, "saldo depois da conferência")
    status(chamar("admin", "POST", f"/manutencao/api/recebimentos/{rid}/conferir", {"quantidade": 3}), 400, "conferir duas vezes")
    igual(_saldo(EST["alm"]), antes + 3, "saldo depois da conferência repetida")


@teste("Compras", "cotação com dois fornecedores: preços gravados e preço negativo recusado")
def cotacao():
    if len(EST["forn"]) < 2:
        raise Pular("laboratório com menos de dois fornecedores")
    c = status(chamar("admin", "POST", "/compras/api/cotacoes", {
        "setor_codigo": EST["setor"], "observacao": "BAT cotação de teste", "fornecedores": EST["forn"],
        "itens": [{"peca_id": EST["peca"], "descricao": "BAT correia de teste", "quantidade": 2, "unidade": "UN"}]}), 200, "criar cotação")
    cid = c["id"]
    assert str(c.get("numero", "")).startswith("COT-"), "cotação sem número"
    igual(len(c["itens"]), 1, "itens da cotação")
    for campo in ("ot_id", "equipamento_id", "cotacao_origem_id", "pendencia_id"):
        igual(c.get(campo), None, f"cotação sem vínculo: {campo} vazio de verdade (não o texto 'None')")
    igual(len(c["fornecedores"]), 2, "fornecedores da cotação")
    item = c["itens"][0]["id"]
    status(chamar("admin", "POST", f"/compras/api/cotacoes/{cid}/enviar", {}), 200, "enviar cotação")
    for cf, preco in zip(c["fornecedores"], (30.0, 22.0)):
        status(chamar("admin", "PUT", f"/compras/api/cotacoes/{cid}/fornecedores/{cf['id']}",
                      {"precos": [{"item_id": item, "preco_unit": preco}]}), 200, "lançar preço do fornecedor")
    status(chamar("admin", "PUT", f"/compras/api/cotacoes/{cid}/fornecedores/{c['fornecedores'][0]['id']}",
                  {"precos": [{"item_id": item, "preco_unit": -5}]}), 400, "preço negativo")
    lida = status(chamar("admin", "GET", f"/compras/api/cotacoes/{cid}"), 200, "ler cotação")
    precos = sorted(float(p["preco_unit"]) for p in lida.get("precos", []) if p.get("preco_unit") is not None)
    igual(precos, [22.0, 30.0], "preços gravados")
    EST["cot"] = cid
    sql("update compras.cotacoes set ativo=false where observacao like 'BAT cotação%' and id<>$1::uuid", cid)
