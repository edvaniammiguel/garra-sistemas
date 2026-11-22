# Bateria — OT: pedido → OT → emitir → registrar → encerrar (21/11/2026)
import datetime as dt
from bat_base import teste, chamar, sql, sql1, status, igual, equipamento_bat, TAG

EST = {}


def _ot(ot_id):
    return status(chamar("admin", "GET", f"/manutencao/api/ots/{ot_id}"), 200, "ler OT")


@teste("OT", "pedido de manutenção vira OT aberta, com número")
def pedido_vira_ot():
    sql("update manutencao.ot set ativo=false where descricao like 'BAT-OT %' and ativo")
    eq = equipamento_bat("BAT-OT-01")
    p = status(chamar("admin", "POST", "/manutencao/api/pedidos",
                      {"equipamento_id": eq, "descricao": "BAT-OT " + TAG + " freio baixo", "grau_urgencia": "alta", "via": "pessoal"}), 200, "criar pedido")
    ot = status(chamar("admin", "POST", f"/manutencao/api/pedidos/{p['id']}/converter", {}), 200, "converter pedido")
    EST["ot"] = ot["id"]
    o = _ot(ot["id"])
    igual(o["status"], "aberta", "estado da OT recém-convertida")
    assert str(o.get("numero", "")).startswith("OT-"), "OT sem número"


@teste("OT", "OT não emitida não aceita encerramento nem registro do mecânico")
def antes_de_emitir():
    ot = EST["ot"]
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "concluida"}), 400, "encerrar OT aberta")
    status(chamar("mec", "POST", f"/manutencao/api/ots/{ot}/apontamento",
                  {"relato": "BAT tentativa antes de emitir", "horas": 1, "concluido": False}), (400, 403, 409), "registrar em OT não emitida")


@teste("OT", "emitir grava o início e libera o registro pelo aplicativo")
def emitir_e_registrar():
    ot = EST["ot"]
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "em_andamento"}), 200, "emitir")
    o = _ot(ot)
    igual(o["status"], "em_andamento", "estado depois de emitir")
    assert o.get("data_inicio_real"), "Emitir não gravou o Início"
    status(chamar("mec", "POST", f"/manutencao/api/ots/{ot}/apontamento",
                  {"relato": "BAT trocadas as lonas e testado", "horas": 2.5, "concluido": True}), 200, "registrar execução")
    lista = status(chamar("admin", "GET", "/manutencao/api/ots"), 200, "listar OTs")
    lista = lista if isinstance(lista, list) else lista.get("itens", [])
    minha = [x for x in lista if x["id"] == ot]
    assert minha and minha[0].get("sinal_concluido"), "OT não ficou sinalizada para encerrar"


@teste("OT", "Aguardando exige motivo; cancelar e reabrir exigem motivo")
def motivos_obrigatorios():
    ot = EST["ot"]
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "aguardando_peca"}), 400, "aguardando sem motivo")
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "cancelada"}), 400, "cancelar sem motivo")


@teste("OT", "encerrar exige o Fim, recusa data no futuro e grava Fim, leitura e custo")
def encerrar():
    ot = EST["ot"]
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "concluida"}), 400, "encerrar sem informar o Fim")
    igual(_ot(ot)["status"], "em_andamento", "OT continua em curso depois do encerramento recusado")
    futuro = (dt.datetime.now() + dt.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "concluida", "data_conclusao": futuro}), 400, "encerrar no futuro")
    agora = (dt.datetime.now() - dt.timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M")
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status",
                  {"status": "concluida", "data_conclusao": agora, "horimetro": "1234", "custo_total": 150.0}), 200, "encerrar")
    o = _ot(ot)
    igual(o["status"], "concluida", "estado depois de encerrar")
    assert o.get("data_conclusao"), "Encerrar não gravou o Fim"
    igual(float(o.get("horimetro_conclusao") or 0), 1234.0, "leitura do encerramento")


@teste("OT", "OT encerrada não aceita mais registro; reabrir pede motivo")
def depois_de_encerrar():
    ot = EST["ot"]
    status(chamar("mec", "POST", f"/manutencao/api/ots/{ot}/apontamento",
                  {"relato": "BAT tentativa depois de encerrada", "horas": 1}), (400, 403, 409), "registrar em OT encerrada")
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status", {"status": "em_andamento"}), 400, "reabrir sem motivo")
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status",
                  {"status": "em_andamento", "observacao": "BAT reaberta pela bateria"}), 200, "reabrir com motivo")
    igual(_ot(ot)["status"], "em_andamento", "estado depois de reabrir")
    status(chamar("admin", "PATCH", f"/manutencao/api/ots/{ot}/status",
                  {"status": "cancelada", "observacao": "BAT fim do teste"}), 200, "cancelar com motivo")
    sql("update manutencao.ot set ativo=false where descricao like 'BAT-OT %' and ativo")
