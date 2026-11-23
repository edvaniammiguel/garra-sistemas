# Bateria — Despacho de OS (22/11/2026): quem tem a permissão vê todas as OS em aberto e envia a OS para um operador
import time

from bat_base import teste, chamar, sql, sql1, usuario, status, igual, colunas, Pular, TAG

NUM = "BAT-DSP-" + TAG


def _limpar():
    sql("delete from public.permissoes_colaborador where modulo='operacional_despacho' and usuario_id in "
        "(select id from public.usuarios_garra where login = any($1::text[]))",
        [usuario("op1")["login"], usuario("op2")["login"], usuario("admin")["login"]])
    sql("delete from public.log_sistema where item_id in (select id::text from operacional.ordens_servico where numero like 'BAT-DSP-%')")
    sql("delete from public.notificacoes where titulo like '%BAT-DSP-%'")
    sql("delete from operacional.ordens_servico where numero like 'BAT-DSP-%'")


def _os(sufixo, operador, st="aberta_sem_erp"):
    return sql1("insert into operacional.ordens_servico (numero,origem,status,criado_por,operador_id,data_inicio,obra) "
                "values ($1,'campo',$4,$2::uuid,$3::uuid,current_date,'BAT-DSP obra') returning id::text as id",
                NUM + sufixo, usuario("admin")["id"], operador, st)["id"]


def _dar_despacho(papel):
    sql("insert into public.permissoes_colaborador (usuario_id, modulo, permitido) values ($1::uuid,'operacional_despacho',true)",
        usuario(papel)["id"])


def _minhas(papel):
    c = status(chamar(papel, "GET", "/operacional/api/minhas-os"), 200, "lista de OS do app")
    return {o["numero"]: o for o in c if str(o["numero"]).startswith(NUM)}


@teste("Despacho", "operador comum continua vendo só as próprias OS e não envia OS de ninguém")
def comum():
    if "descricao" not in colunas("operacional.ordens_servico"):
        raise Pular("tabela de OS simplificada neste laboratório")
    _limpar()
    a, b = usuario("op1")["id"], usuario("op2")["id"]
    os_a, os_b, os_orf = _os("-A", a), _os("-B", b), _os("-ORF", None)
    vistas = _minhas("op1")
    igual(sorted(vistas), [NUM + "-A"], "OS que o operador comum enxerga")
    igual(vistas[NUM + "-A"]["pode_enviar"], False, "operador comum não tem o botão de enviar")
    status(chamar("op1", "POST", f"/operacional/api/os/{os_b}/encaminhar", {"operador_id": a}), 403, "comum tentando enviar OS")
    status(chamar("op1", "GET", "/operacional/api/despacho/operadores"), 403, "comum pedindo a lista de operadores")
    status(chamar(None, "POST", f"/operacional/api/os/{os_b}/encaminhar", {"operador_id": a}), 401, "sem login")
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", os_b)["o"], b, "a OS não mudou de mão")


@teste("Despacho", "quem tem o Despacho vê todas as OS em aberto, com o nome do responsável")
def ve_todas():
    _dar_despacho("op2")
    vistas = _minhas("op2")
    igual(sorted(vistas), [NUM + "-A", NUM + "-B", NUM + "-ORF"], "OS que o despachante enxerga")
    igual(vistas[NUM + "-B"]["minha"], True, "a OS dele vem marcada como dele")
    igual(vistas[NUM + "-A"]["minha"], False, "a OS do colega não é dele")
    assert vistas[NUM + "-A"]["operador_nome"], "faltou o nome do responsável na OS do colega"
    igual(vistas[NUM + "-ORF"]["operador_nome"], None, "OS sem responsável vem sem nome")
    assert all(o["pode_enviar"] for o in vistas.values()), "todas deveriam poder ser enviadas"
    concl = _os("-FIM", usuario("op1")["id"], "concluida_sem_erp")
    assert NUM + "-FIM" not in _minhas("op2"), "OS concluída não entra na lista de despacho"
    status(chamar("op2", "POST", f"/operacional/api/os/{concl}/encaminhar", {"operador_id": usuario("op1")["id"]}), 400, "enviar OS concluída")


@teste("Despacho", "enviar a OS grava o novo responsável, avisa o operador e fica no log")
def envia():
    a, b = usuario("op1"), usuario("op2")
    orf = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-ORF")["id"]
    c = status(chamar("op2", "POST", f"/operacional/api/os/{orf}/encaminhar", {"operador_id": a["id"]}), 200, "despachante enviando")
    nome_a = sql1("select nome from public.usuarios_garra where id=$1::uuid", a["id"])["nome"]
    igual(c["operador_nome"], nome_a, "resposta diz para quem foi")
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", orf)["o"], a["id"], "o servidor gravou")
    assert NUM + "-ORF" in _minhas("op1"), "a OS não apareceu na lista do operador que recebeu"
    n = sql("select 1 from public.notificacoes where usuario_id=$1::uuid and titulo like $2", a["id"], "%" + NUM + "-ORF aberta para você%")
    assert n, "o operador não recebeu o aviso 'OS aberta para você'"
    fim = time.time() + 3
    while True:
        log = sql("select usuario_nome, resumo from public.log_sistema where item_id=$1 and evento='Encaminhamento'", orf)
        if log or time.time() > fim:
            break
        time.sleep(0.15)
    assert log, "o envio não ficou no log do sistema"
    assert nome_a in log[0]["resumo"], f"o log não diz para quem foi: {log[0]['resumo']}"
    # trocar de um operador para outro (OS que já tinha responsável) e devolver para si mesmo
    os_a = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-A")["id"]
    status(chamar("op2", "POST", f"/operacional/api/os/{os_a}/encaminhar", {"operador_id": b["id"]}), 200, "puxando a OS do colega para si")
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", os_a)["o"], b["id"], "troca de responsável gravada")
    assert sql("select 1 from public.notificacoes where usuario_id=$1::uuid and titulo like $2", a["id"], "%" + NUM + "-A passou para%"), \
        "o operador anterior não foi avisado de que a OS saiu dele"


@teste("Despacho", "dá para tirar a OS do operador e deixar sem responsável, para decidir depois")
def sem_responsavel():
    a = usuario("op1")
    orf = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-ORF")["id"]
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", orf)["o"], a["id"], "a OS estava com o operador")
    c = status(chamar("op2", "POST", f"/operacional/api/os/{orf}/encaminhar", {"sem_responsavel": True}), 200, "despachante tirando a OS do operador")
    igual(c.get("sem_responsavel"), True, "resposta confirma que ficou sem responsável")
    igual(sql1("select operador_id from operacional.ordens_servico where id=$1::uuid", orf)["operador_id"], None, "o servidor gravou sem responsável")
    assert NUM + "-ORF" not in _minhas("op1"), "a OS continuou na lista do operador que perdeu"
    v = _minhas("op2")[NUM + "-ORF"]
    igual((v["operador_nome"], v["pode_enviar"]), (None, True), "o despachante continua vendo a OS, sem responsável, e pode enviar")
    assert sql("select 1 from public.notificacoes where usuario_id=$1::uuid and titulo like $2", a["id"], "%" + NUM + "-ORF saiu de você%"), \
        "o operador não foi avisado de que a OS saiu dele"
    status(chamar("op1", "POST", f"/operacional/api/os/{orf}/encaminhar", {"sem_responsavel": True}), 403, "operador comum tentando o mesmo")
    # e depois escolhe para quem vai
    status(chamar("op2", "POST", f"/operacional/api/os/{orf}/encaminhar", {"operador_id": a["id"]}), 200, "decidiu depois: enviou ao operador")
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", orf)["o"], a["id"], "voltou a ter responsável")


@teste("Despacho", "só dá para enviar a operador ou motorista ativo; sem escolher ninguém é recusado")
def destino_valido():
    os_b = sql1("select id::text as id from operacional.ordens_servico where numero=$1", NUM + "-B")["id"]
    status(chamar("op2", "POST", f"/operacional/api/os/{os_b}/encaminhar", {}), 400, "sem escolher operador")
    status(chamar("op2", "POST", f"/operacional/api/os/{os_b}/encaminhar", {"operador_id": usuario("mec")["id"]}), 400, "pessoa que não é operador nem motorista")
    status(chamar("op2", "POST", f"/operacional/api/os/{os_b}/encaminhar", {"operador_id": "00000000-0000-0000-0000-000000000000"}), 400, "pessoa que não existe")
    lista = status(chamar("op2", "GET", "/operacional/api/despacho/operadores"), 200, "lista para escolher")
    perfis = {o["perfil"] for o in lista}
    assert perfis <= {"operador", "motorista"}, f"a lista trouxe outros perfis: {perfis}"
    assert usuario("mec")["id"] not in [o["id"] for o in lista], "a lista trouxe quem não é operador"
    igual(sql1("select operador_id::text as o from operacional.ordens_servico where id=$1::uuid", os_b)["o"], usuario("op2")["id"], "a OS não mudou")


@teste("Despacho", "ligar o Despacho para uma pessoa não muda a lista de mais ninguém, nem do Master")
def so_quem_tem():
    vistas = _minhas("admin")
    assert not any(o["pode_enviar"] for o in vistas.values()), "o Master sem a permissão marcada não deveria ver o despacho"
    donos = {o["operador_id"] for o in vistas.values()}
    assert donos <= {usuario("admin")["id"]}, "o Master passou a ver OS dos outros no app"
    # tirar a permissão volta ao normal
    sql("update public.permissoes_colaborador set permitido=false where modulo='operacional_despacho' and usuario_id=$1::uuid", usuario("op2")["id"])
    assert all(o["minha"] for o in _minhas("op2").values()), "sem a permissão, voltou a ver OS dos outros"
    status(chamar("op2", "GET", "/operacional/api/despacho/operadores"), 403, "sem a permissão, lista de operadores")
    _limpar()
