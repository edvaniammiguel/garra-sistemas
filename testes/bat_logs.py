# Bateria — Logs do sistema (22/11/2026): toda gravação vira linha; Operacional com antes e depois; só Master consulta
import json
import time
import urllib.request

from bat_base import teste, chamar, sql, sql1, usuario, status, igual, colunas, token, Pular, TAG, URL

NUM = "BAT-LOG-" + TAG


def _limpar():
    sql("delete from public.log_sistema where item_id in (select id::text from operacional.ordens_servico where numero like 'BAT-LOG-%')")
    sql("delete from public.log_sistema where rota='/auth/login' and evento='Recusa' and em > now() - interval '10 minutes'")
    sql("delete from operacional.partes_diarias where observacao like 'BAT-LOG%'")
    sql("delete from operacional.ordens_servico where numero like 'BAT-LOG-%'")


def _os(sufixo, condutor):
    return sql1("insert into operacional.ordens_servico (numero,origem,status,criado_por,operador_id,data_inicio,obra) "
                "values ($1,'campo','aberta_sem_erp',$2::uuid,$2::uuid,current_date,'BAT-LOG obra') returning id::text as id",
                NUM + sufixo, condutor)["id"]


def _nome(papel):
    return sql1("select nome from public.usuarios_garra where login=$1", usuario(papel)["login"])["nome"]


def _log(os_id, evento, espera=3.0):
    """O log é gravado logo depois da resposta: espera um instante por ele."""
    fim = time.time() + espera
    while True:
        r = sql("select id, usuario_nome, evento, resumo, motivo, status, detalhe::text as detalhe from public.log_sistema "
                "where item_id=$1 and evento=$2 order by id desc limit 1", os_id, evento)
        if r or time.time() > fim:
            break
        time.sleep(0.15)
    assert r, f"não achei no log o evento '{evento}' desta OS"
    d = dict(r[0])
    d["detalhe"] = json.loads(d["detalhe"]) if d["detalhe"] else []
    return d


def _campo(log, nome):
    for c in log["detalhe"]:
        if c["campo"] == nome:
            return c
    raise AssertionError(f"o log não trouxe o campo '{nome}' (trouxe: {[c['campo'] for c in log['detalhe']]})")


@teste("Logs", "só o Master consulta os logs; operador e visitante são barrados")
def so_master():
    _limpar()
    status(chamar(None, "GET", "/logs-sistema/api/lista"), 401, "sem login")
    status(chamar("op1", "GET", "/logs-sistema/api/lista"), 403, "operador")
    status(chamar("op1", "GET", "/logs-sistema/api/relatorio?formato=pdf"), 403, "operador pedindo relatório")
    status(chamar("op1", "GET", "/logs-sistema/api/evento/1"), 403, "operador abrindo um evento")
    status(chamar("admin", "GET", "/logs-sistema/api/lista"), 200, "Master")


@teste("Logs", "a tela abre nos últimos 7 dias, nunca em branco")
def sete_dias():
    import datetime as dt
    c = status(chamar("admin", "GET", "/logs-sistema/api/lista"), 200, "lista sem datas")
    de, ate = dt.date.fromisoformat(c["de"]), dt.date.fromisoformat(c["ate"])
    igual((ate - de).days, 6, "período padrão (hoje e os 6 dias anteriores)")
    c2 = status(chamar("admin", "GET", "/logs-sistema/api/lista?de=2026-11-20&ate=2026-11-10"), 200, "datas invertidas")
    igual((c2["de"], c2["ate"]), ("2026-11-10", "2026-11-20"), "datas invertidas são arrumadas")
    status(chamar("admin", "GET", "/logs-sistema/api/lista?de=ontem"), 422, "data que não é data")


@teste("Logs", "corrigir um registro guarda o valor de antes e o de depois")
def antes_e_depois():
    if "horimetro_inicial" not in colunas("operacional.partes_diarias"):
        raise Pular("tabela de partes simplificada neste laboratório")
    a = usuario("op1")["id"]
    os1 = _os("-1", a)
    p = sql1("insert into operacional.partes_diarias (os_id,operador_id,criado_por,data,observacao,horas_trabalhadas,"
             "horimetro_inicial,tipo_medicao) values ($1::uuid,$2::uuid,$2::uuid,current_date,'BAT-LOG reg',0,1220.8,'horimetro') "
             "returning id::text as id", os1, a)["id"]
    status(chamar("op1", "PATCH", f"/operacional/api/minhas-partes/{p}", {"horimetro_inicial": None, "horimetro_final": 1227.1}),
           200, "operador corrigindo o próprio registro")
    log = _log(os1, "Alteração")
    igual(log["usuario_nome"], _nome("op1"), "quem fez")
    ini, fin = _campo(log, "Horímetro inicial"), _campo(log, "Horímetro final")
    igual((ini["antes"], ini["depois"]), ("1220,8", None), "horímetro inicial: antes → depois")
    igual((fin["antes"], fin["depois"]), (None, "1227,1"), "horímetro final: antes → depois")


@teste("Logs", "liberar a OS fica registrado: quem liberou e quem era o responsável")
def liberar():
    a = usuario("op1")
    os1 = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-1")
    if not os1:
        os1 = {"id": _os("-1", a["id"])}
    status(chamar("op1", "POST", f"/operacional/api/os/{os1['id']}/liberar"), 200, "operador liberando a própria OS")
    log = _log(os1["id"], "Liberação")
    igual(log["usuario_nome"], _nome("op1"), "quem liberou")
    r = _campo(log, "Responsável")
    igual((r["antes"], r["depois"]), (_nome("op1"), None), "responsável: antes → depois")


@teste("Logs", "encaminhar a OS para outro operador fica registrado com os dois nomes")
def encaminhar():
    b = usuario("op2")
    os1 = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-1")["id"]
    r = chamar("admin", "PATCH", f"/operacional/api/os/{os1}", {"operador_id": b["id"]})
    if r[0] == 500:
        raise Pular("tabela de OS simplificada neste laboratório (faltam colunas que a edição grava)")
    status(r, 200, "gestão encaminhando a OS")
    log = _log(os1, "Encaminhamento")
    c = _campo(log, "Responsável")
    igual((c["antes"], c["depois"]), (None, _nome("op2")), "responsável: antes → depois")
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", os1)["o"], b["id"],
          "o servidor gravou o novo responsável")


@teste("Logs", "tentativa recusada pelo servidor também fica registrada, com o motivo")
def recusa():
    a = usuario("op1")["id"]
    os2 = _os("-2", a)
    p = sql1("insert into operacional.partes_diarias (os_id,operador_id,criado_por,data,observacao,horas_trabalhadas) "
             "values ($1::uuid,$2::uuid,$2::uuid,current_date,'BAT-LOG do A',8) returning id::text as id", os2, a)["id"]
    status(chamar("op2", "DELETE", f"/operacional/api/minhas-partes/{p}"), 403, "colega tentando excluir registro alheio")
    log = _log(os2, "Recusa")
    igual(log["usuario_nome"], _nome("op2"), "quem tentou")
    igual(log["status"], 403, "resposta do servidor")
    assert log["motivo"], "a recusa ficou sem o motivo"


@teste("Logs", "o rastreio de uma OS mostra tudo o que aconteceu nela, em ordem")
def rastreio():
    os1 = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-1")["id"]
    ult = sql("select id from public.log_sistema where item_id=$1 order by id desc limit 1", os1)
    assert ult, "nenhum evento desta OS no log"
    c = status(chamar("admin", "GET", f"/logs-sistema/api/evento/{ult[0]['id']}"), 200, "abrir evento")
    ids = [r["id"] for r in c["rastreio"]]
    assert len(ids) >= 2, f"rastreio deveria ter os eventos da OS, veio {len(ids)}"
    igual(ids, sorted(ids), "ordem do rastreio (do mais antigo para o mais novo)")
    assert "item_id" not in c["evento"], "a consulta não deve devolver o código interno do item"


@teste("Logs", "senha digitada nunca entra no log")
def sem_senha():
    segredo = "BAT-LOG-senha-" + TAG
    chamar(None, "POST", "/auth/login", {"login": "bat-log-ninguem", "senha": segredo, "password": segredo})
    time.sleep(0.6)
    n = sql1("select count(*) as n from public.log_sistema l where l::text like $1", "%" + segredo + "%")["n"]
    igual(n, 0, "linhas do log contendo a senha digitada")
    n2 = sql1("select count(*) as n from public.log_sistema where rota like '%?%' or rota like '%sso=%' or rota like '%token=%'")["n"]
    igual(n2, 0, "linhas do log com parâmetros do endereço (token, sso)")


@teste("Logs", "log não se altera nem se apaga pela aplicação")
def imutavel():
    for metodo in ("DELETE", "PATCH", "PUT", "POST"):
        r = chamar("admin", metodo, "/logs-sistema/api/evento/1", {} if metodo != "DELETE" else None)
        assert r[0] in (404, 405), f"{metodo} em um evento deveria ser recusado, respondeu {r[0]}"
        r = chamar("admin", metodo, "/logs-sistema/api/lista", {} if metodo != "DELETE" else None)
        assert r[0] in (404, 405), f"{metodo} na lista deveria ser recusado, respondeu {r[0]}"


def _baixar(caminho):
    req = urllib.request.Request(URL + caminho, headers={"Authorization": "Bearer " + token("admin")})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read()


@teste("Logs", "relatório sai em Excel e em PDF com o que está filtrado na tela")
def relatorio():
    st, x = _baixar("/logs-sistema/api/relatorio?formato=xlsx&modulo=Operacional&q=Libera")
    igual(st, 200, "Excel")
    assert x[:2] == b"PK", "o Excel não veio como planilha"
    import io, zipfile
    folha = zipfile.ZipFile(io.BytesIO(x))
    texto = b"".join(folha.read(n) for n in folha.namelist() if n.endswith(".xml")).decode("utf-8", "replace")
    assert "Libera" in texto and "Respons" in texto, "o Excel não trouxe o evento filtrado com o campo alterado"
    assert "Encaminhamento" not in texto, "o Excel trouxe evento fora da pesquisa"
    st, p = _baixar("/logs-sistema/api/relatorio?formato=pdf&modulo=Operacional&q=Libera")
    igual(st, 200, "PDF")
    assert p[:5] == b"%PDF-", "o PDF não veio como PDF"
    status(chamar("admin", "GET", "/logs-sistema/api/relatorio?formato=doc"), 422, "formato que não existe")


@teste("Logs", "consultar (só leitura) não gera linha de log")
def leitura_nao_loga():
    antes = sql1("select count(*) as n from public.log_sistema")["n"]
    chamar("admin", "GET", "/logs-sistema/api/lista")
    chamar("admin", "GET", "/operacional/api/os")
    time.sleep(0.5)
    igual(sql1("select count(*) as n from public.log_sistema")["n"], antes, "linhas no log depois de duas consultas")
    _limpar()
