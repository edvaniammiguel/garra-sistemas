# Bateria — RH: colaborador fictício e férias (21/11/2026). Nenhum dado pessoal real.
import datetime as dt
from bat_base import teste, chamar, sql, status, igual, HOJE, TAG

EST = {}
NOME = "BAT Ficticio " + TAG


def _limpar():
    ids = [l["id"] for l in sql("select id from rh.colaboradores where nome like 'BAT Ficticio%'")]
    for i in ids:
        for t in ("rh.ferias_lanc", "rh.ferias_periodos", "rh.treinamentos"):
            sql(f"delete from {t} where colaborador_id=$1", i)
        sql("delete from rh.colaboradores where id=$1", i)
    sql("delete from rh.cursos where codigo like 'BATNR%'")


def _meu():
    f = status(chamar("admin", "GET", "/rh/api/ferias"), 200, "painel de férias")
    eu = [i for i in f["itens"] if i["nome"] == NOME]
    assert eu, "colaborador da bateria não aparece no painel de férias"
    return eu[0]


@teste("RH", "colaborador novo entra no controle de férias com o período certo")
def colaborador_e_periodo():
    status(chamar("admin", "GET", "/rh/api/ferias"), 200, "abrir férias (cria as tabelas)")
    _limpar()
    adm = HOJE.replace(year=HOJE.year - 2) + dt.timedelta(days=60)      # 1 período vencido, limite ainda à frente
    status(chamar("admin", "POST", "/rh/api/colaboradores", {"nome": NOME, "data_admissao": adm.isoformat()}), 200, "criar colaborador")
    eu = _meu()
    assert eu["periodos"], "nenhum período aquisitivo calculado"
    p = eu["periodos"][0]
    igual(p["aq_ini"], adm.isoformat(), "início do período aquisitivo")
    igual(int(p["direito"]), 30, "dias de direito")
    EST["col"], EST["aq"], EST["ci"] = eu["colaborador_id"], p["aq_ini"], p["conc_ini"]


@teste("RH", "férias: não lança além do direito, nem abono acima de 10 dias")
def limites_de_ferias():
    lanc = lambda corpo: chamar("admin", "POST", "/rh/api/ferias/lancamentos", {"colaborador_id": EST["col"], "aquisitivo_inicio": EST["aq"], **corpo})
    ini = (HOJE + dt.timedelta(days=5)).isoformat()
    status(lanc({"inicio": ini, "dias": 31}), (400, 422), "31 dias de férias")
    status(lanc({"tipo": "abono", "dias": 11}), (400, 422), "abono de 11 dias")
    status(lanc({"inicio": ini, "dias": 15}), 200, "15 dias de férias")
    status(lanc({"inicio": (HOJE + dt.timedelta(days=10)).isoformat(), "dias": 5}), (400, 409, 422), "férias sobrepostas")
    p = _meu()["periodos"][0]
    igual(int(p["saldo"]), 15, "saldo depois de programar 15 dias")


@teste("RH", "NR concluída ganha o vencimento pelo prazo do catálogo, e o prazo novo recalcula")
def vencimento_de_nr():
    cod = "BATNR" + TAG
    k = status(chamar("admin", "POST", "/rh/api/cursos", {"codigo": cod, "nome": "BAT treinamento de teste", "tipo": "NR", "validade_meses": 12}), 200, "criar NR no catálogo")
    status(chamar("admin", "POST", "/rh/api/cursos", {"codigo": cod, "nome": "BAT repetido", "tipo": "NR"}), 409, "código de NR repetido")
    feito = HOJE - dt.timedelta(days=30)
    base = f"/rh/api/colaboradores/{EST['col']}/treinamentos"
    status(chamar("admin", "POST", base, {"curso_id": k["id"], "situacao": "concluido",
                                          "data_conclusao": (HOJE + dt.timedelta(days=3)).isoformat()}), 422, "conclusão no futuro")
    status(chamar("admin", "POST", base, {"curso_id": k["id"], "situacao": "concluido"}), 422, "concluído sem data")
    status(chamar("admin", "POST", base, {"curso_id": k["id"], "situacao": "concluido", "data_conclusao": feito.isoformat()}), 200, "lançar NR concluída")
    venc = lambda: sql("select validade_ate from rh.treinamentos where colaborador_id=$1::uuid and curso_id=$2::uuid and ativo", EST["col"], k["id"])[0]["validade_ate"]
    igual(venc(), feito.replace(year=feito.year + 1), "vencimento = conclusão + 12 meses")
    status(chamar("admin", "POST", "/rh/api/cursos", {"id": k["id"], "codigo": cod, "nome": "BAT treinamento de teste", "tipo": "NR", "validade_meses": 24}), 200, "mudar o prazo para 24 meses")
    igual(venc(), feito.replace(year=feito.year + 2), "vencimento recalculado para 24 meses")


@teste("RH", "férias e colaboradores só para quem tem o RH")
def so_rh():
    status(chamar("op1", "GET", "/rh/api/ferias"), 403, "operador nas férias")
    status(chamar("op1", "POST", "/rh/api/ferias/lancamentos",
                  {"colaborador_id": EST["col"], "aquisitivo_inicio": EST["aq"], "tipo": "abono", "dias": 5}), 403, "operador lançando férias")
    _limpar()
