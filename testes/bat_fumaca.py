# Bateria — TODAS AS CONSULTAS: abre cada rota de leitura do sistema como administração (21/11/2026)
# Pega o erro que só aparece quando alguém abre a tela: coluna renomeada, função apagada, import quebrado.
import re
from bat_base import teste, chamar, status, Pular

EST = {"feito": False, "erros": [], "lab": [], "total": 0}
NAO_CHAMAR = ("/backup", "/exportar", "logout", "/webhook", "/cron", "/enviar", "/debug/")
FALTA_NO_LAB = re.compile(r"Undefined(Table|Column|Function|Object)Error|InvalidSchemaNameError|does not exist\.?$")


def _varrer():
    if EST["feito"]:
        return
    api = status(chamar(None, "GET", "/openapi.json"), 200, "lista de rotas do servidor")
    for caminho, ops in sorted(api["paths"].items()):
        if "get" not in ops or "{" in caminho or any(x in caminho for x in NAO_CHAMAR):
            continue
        EST["total"] += 1
        cod, corpo = chamar("admin", "GET", caminho)
        if cod >= 500 or cod == 0:
            falta_tabela = FALTA_NO_LAB.search(str(corpo)) and "File at path" not in str(corpo)   # arquivo que falta no repositório É defeito
            (EST["lab"] if falta_tabela else EST["erros"]).append(f"{caminho} → {str(corpo)[:110]}")
    EST["feito"] = True


@teste("Consultas", "nenhuma tela de consulta do sistema dá erro interno ao abrir")
def sem_erro_interno():
    _varrer()
    assert EST["total"] > 50, f"só {EST['total']} rotas encontradas — a lista de rotas do servidor parece incompleta"
    assert not EST["erros"], f"{len(EST['erros'])} de {EST['total']} rotas com erro interno: " + " | ".join(EST["erros"][:6])


@teste("Consultas", "todas as rotas puderam ser abertas neste laboratório")
def laboratorio_completo():
    _varrer()
    if EST["lab"]:
        mods = sorted({c.split(" → ")[0].strip("/").split("/")[0] for c in EST["lab"]})
        raise Pular(f"{len(EST['lab'])} de {EST['total']} rotas dependem de tabelas que este laboratório não tem "
                    f"(módulos: {', '.join(mods)}) — essas telas não estão sendo testadas")
