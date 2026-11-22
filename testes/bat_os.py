# Bateria — OS no aplicativo: só quem lançou altera ou exclui (regra de 21/11/2026)
from bat_base import teste, chamar, sql, sql1, usuario, status, igual, colunas, Pular, TAG


def _limpar():
    sql("delete from operacional.partes_diarias where observacao like 'BAT-OS%'")
    sql("delete from operacional.ordens_servico where numero like 'BAT-OS-%'")


def _os(numero, criador, condutor):
    return sql1("insert into operacional.ordens_servico (numero,origem,status,criado_por,operador_id,data_inicio) "
                "values ($1,'campo','aberta_sem_erp',$2::uuid,$3::uuid,current_date) returning id::text as id", numero, criador, condutor)["id"]


def _parte(os_id, operador, autor, obs, dias=0):
    return sql1("insert into operacional.partes_diarias (os_id,operador_id,criado_por,data,observacao,horas_trabalhadas) "
                "values ($1::uuid,$2::uuid,$3::uuid,current_date-$4::int,$5,8) returning id::text as id", os_id, operador, autor, dias, obs)["id"]


def _partes_vistas(papel, os_id):
    corpo = status(chamar(papel, "GET", f"/operacional/api/os/{os_id}/partes"), 200, "listar partes")
    lista = corpo.get("partes", corpo) if isinstance(corpo, dict) else corpo
    return {p["observacao"]: p.get("meu") for p in lista}


@teste("OS", "quem assume a OS não altera nem exclui o registro do colega")
def colega_protegido():
    _limpar()
    a, b = usuario("op1")["id"], usuario("op2")["id"]
    os1 = _os("BAT-OS-" + TAG + "-1", a, b)                    # criada por A, conduzida por B
    do_a = _parte(os1, a, a, "BAT-OS do A", 1)
    status(chamar("op2", "DELETE", f"/operacional/api/minhas-partes/{do_a}"), 403, "B excluir registro do A")
    status(chamar("op2", "PATCH", f"/operacional/api/minhas-partes/{do_a}", {"observacao": "x"}), 403, "B alterar registro do A")
    igual(sql1("select ativo from operacional.partes_diarias where id=$1::uuid", do_a)["ativo"], True, "registro do A continua ativo")


@teste("OS", "na OS, o operador só vê os próprios registros (com lápis e lixeira)")
def marca_meu():
    b = usuario("op2")["id"]
    os1 = sql1("select id::text as id from operacional.ordens_servico where numero=$1", "BAT-OS-" + TAG + "-1")["id"]
    _parte(os1, b, b, "BAT-OS do B")
    igual(_partes_vistas("op2", os1), {"BAT-OS do B": True}, "visão do B (não vê as horas do colega)")
    igual(_partes_vistas("op1", os1), {"BAT-OS do A": True}, "visão do A")
    visto = _partes_vistas("admin", os1)
    assert set(visto) == {"BAT-OS do A", "BAT-OS do B"}, "gestão deveria ver os dois registros, viu " + str(sorted(visto))


@teste("OS", "quem lançou exclui o próprio registro")
def exclui_o_proprio():
    do_b = sql1("select id::text as id from operacional.partes_diarias where observacao='BAT-OS do B' and ativo")["id"]
    status(chamar("op2", "DELETE", f"/operacional/api/minhas-partes/{do_b}"), 200, "B excluir o próprio")


@teste("OS", "quem lançou altera o próprio registro")
def altera_o_proprio():
    do_a = sql1("select id::text as id from operacional.partes_diarias where observacao='BAT-OS do A' and ativo")["id"]
    r = chamar("op1", "PATCH", f"/operacional/api/minhas-partes/{do_a}", {"observacao": "BAT-OS do A", "horas_trabalhadas": 7})
    if r[0] == 500 and "horimetro_inicial" not in colunas("operacional.partes_diarias"):
        raise Pular("tabela de partes simplificada neste laboratório (faltam colunas que a edição grava)")
    status(r, 200, "A alterar o próprio")
    igual(sql1("select ativo from operacional.partes_diarias where id=$1::uuid", do_a)["ativo"], True, "registro continua ativo depois da edição")


@teste("OS", "OS só pode ser excluída por quem a criou, e vazia")
def excluir_os():
    a, b = usuario("op1")["id"], usuario("op2")["id"]
    os1 = sql1("select id::text as id from operacional.ordens_servico where numero=$1", "BAT-OS-" + TAG + "-1")["id"]
    status(chamar("op2", "DELETE", f"/operacional/api/os/{os1}"), (400, 403, 409), "B excluir OS com registro do A")
    os2 = _os("BAT-OS-" + TAG + "-2", a, b)                    # vazia, criada por A, conduzida por B
    status(chamar("op2", "DELETE", f"/operacional/api/os/{os2}"), 403, "B excluir OS vazia que não criou")
    os3 = _os("BAT-OS-" + TAG + "-3", b, b)                    # vazia, criada e conduzida por B
    minhas = status(chamar("op2", "GET", "/operacional/api/minhas-os"), 200, "minhas-os")
    pode = {o["numero"]: o.get("pode_excluir") for o in minhas if str(o.get("numero", "")).startswith("BAT-OS-" + TAG)}
    igual(pode.get("BAT-OS-" + TAG + "-2"), False, "lixeira na OS que não criou")
    igual(pode.get("BAT-OS-" + TAG + "-3"), True, "lixeira na OS que criou")
    status(chamar("op2", "DELETE", f"/operacional/api/os/{os3}"), 200, "B excluir a OS que criou")
    status(chamar("admin", "DELETE", f"/operacional/api/os/{os2}"), 200, "gestão excluir OS vazia")


@teste("OS", "registro antigo sem autor: vale o operador do registro")
def registro_sem_autor():
    a = usuario("op1")["id"]
    os1 = sql1("select id::text as id from operacional.ordens_servico where numero=$1", "BAT-OS-" + TAG + "-1")["id"]
    antigo = sql1("insert into operacional.partes_diarias (os_id,operador_id,criado_por,data,observacao) "
                  "values ($1::uuid,$2::uuid,null,current_date-2,'BAT-OS antigo') returning id::text as id", os1, a)["id"]
    status(chamar("op2", "DELETE", f"/operacional/api/minhas-partes/{antigo}"), 403, "B excluir registro antigo do A")
    status(chamar("op1", "DELETE", f"/operacional/api/minhas-partes/{antigo}"), 200, "A excluir o próprio registro antigo")
    _limpar()
