# Bateria — ABASTECIMENTO: nota, cupom repetido, leitura fora da sequência (21/11/2026)
import datetime as dt
from bat_base import teste, chamar, sql, sql1, status, igual, equipamento_bat, TAG

EST = {}


def _limpar():
    sql("delete from operacional.abastecimentos where nota_id in (select id from operacional.abastecimento_notas where numero_cupom like 'BAT%')")
    sql("delete from operacional.abastecimento_notas where numero_cupom like 'BAT%'")


def _nota(cupom, leitura, dias, litros=100.0, itens=None):
    quando = (dt.datetime.now() - dt.timedelta(days=dias)).strftime("%d/%m/%Y 08:00")
    itens = itens or [{"equipamento_id": EST["eq"], "litros": litros, "leitura": leitura}]
    return chamar("op1", "POST", "/operacional/api/abastecimentos/nota", {
        "nota": {"numero_cupom": cupom, "fornecedor_id": EST["posto"], "data_hora": quando, "combustivel": "DIESEL_S10",
                 "litros_total": litros, "preco_litro": 6.0, "valor_total": round(litros * 6.0, 2)}, "itens": itens})


def _contador():
    return float(sql1("select coalesce(km_atual,0) as k from operacional.equipamentos where id=$1::uuid", EST["eq"])["k"])


@teste("Abastecimento", "nota grava e move o contador do equipamento")
def nota_grava():
    _limpar()
    EST["eq"] = equipamento_bat("BAT-AB-01", "km")
    sql("update operacional.equipamentos set km_atual=1000 where id=$1::uuid", EST["eq"])
    ctx = status(chamar("op1", "GET", "/operacional/api/abastecimentos/contexto"), 200, "contexto do app")
    postos = [p for p in ctx["postos"] if p["nome"].startswith("BAT POSTO")]
    if not postos:
        novo = status(chamar("admin", "POST", "/operacional/api/abastecimentos/postos", {"nome": "BAT POSTO DE TESTE"}), 200, "criar posto")
        ctx = status(chamar("op1", "GET", "/operacional/api/abastecimentos/contexto"), 200, "contexto do app")
        postos = [p for p in ctx["postos"] if p["nome"].startswith("BAT POSTO") or p["id"] == (novo or {}).get("id")]
    assert postos, "posto criado pela bateria não aparece no app"
    EST["posto"] = postos[0]["id"]
    assert any(e["id"] == EST["eq"] for e in ctx["frota"]), "equipamento da bateria não aparece no app"
    status(_nota("BAT" + TAG + "1", 1500, 3), 200, "primeira nota")
    igual(_contador(), 1500.0, "contador depois da primeira nota")
    status(_nota("BAT" + TAG + "2", 2100, 2), 200, "segunda nota")
    igual(_contador(), 2100.0, "contador depois da segunda nota")


@teste("Abastecimento", "o mesmo cupom do mesmo posto não entra duas vezes")
def cupom_repetido():
    status(_nota("BAT" + TAG + "2", 2200, 1), (400, 409), "cupom repetido")
    igual(_contador(), 2100.0, "contador depois do cupom recusado")


@teste("Abastecimento", "leitura fora da sequência gera alerta e não move o contador")
def fora_da_sequencia():
    r = status(_nota("BAT" + TAG + "3", 1800, 1), 200, "nota com leitura menor que a anterior")
    igual(_contador(), 2100.0, "contador depois da leitura fora da sequência")
    reg = sql1("select a.divergencia_leitura as divergente from operacional.abastecimentos a join operacional.abastecimento_notas n on n.id=a.nota_id "
               "where n.numero_cupom=$1", "BAT" + TAG + "3")
    igual(bool(reg["divergente"]), True, "registro marcado como divergente")
    itens = r.get("itens", []) if isinstance(r, dict) else []
    assert any(i.get("divergencia_leitura") for i in itens), "a resposta ao app não marcou a leitura como divergente (é o que acende o aviso na tela)"


@teste("Abastecimento", "os litros dos itens precisam fechar com a nota")
def itens_fecham():
    itens = [{"equipamento_id": EST["eq"], "litros": 30.0, "leitura": 2300}]
    status(_nota("BAT" + TAG + "4", 2300, 0, litros=100.0, itens=itens), 400, "itens que não fecham com a nota")
    _limpar()
