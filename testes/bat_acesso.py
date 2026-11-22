# Bateria — ACESSO: quem entra, quem é barrado (21/11/2026)
from bat_base import teste, chamar, token, status


@teste("Acesso", "sem login, as rotas do sistema recusam")
def sem_token():
    for rota in ("/operacional/api/minhas-os", "/manutencao/api/ots", "/rh/api/ferias", "/operacional/api/abastecimentos/contexto"):
        status(chamar(None, "GET", rota), (401, 403), "sem token em " + rota)


@teste("Acesso", "token assinado com outra chave é recusado")
def token_falso():
    falso = token("admin", segredo="chave-que-nao-e-a-do-sistema")
    status(chamar(None, "GET", "/manutencao/api/ots", tok=falso), (401, 403), "token falso")


@teste("Acesso", "token vencido é recusado")
def token_vencido():
    velho = token("admin", validade=-60)
    status(chamar(None, "GET", "/manutencao/api/ots", tok=velho), (401, 403), "token vencido")


@teste("Acesso", "administração abre Manutenção e RH")
def admin_entra():
    status(chamar("admin", "GET", "/manutencao/api/ots"), 200, "admin em Manutenção")
    status(chamar("admin", "GET", "/rh/api/ferias"), 200, "admin no RH")


@teste("Acesso", "operador não abre os dados do RH")
def operador_fora_do_rh():
    status(chamar("op1", "GET", "/rh/api/ferias"), 403, "operador em férias")
    status(chamar("op1", "GET", "/rh/api/colaboradores"), 403, "operador em colaboradores")


@teste("Acesso", "nenhuma rota de dados responde sem login (varredura das 548 rotas)")
def varredura_sem_login():
    import re
    api = status(chamar(None, "GET", "/openapi.json"), 200, "lista de rotas do servidor")
    publicas = re.compile(r"^/auth/|/health|manifest\.json$|sw\.js$|favicon|^/jardinagem/api/(login|logout)$|^/openapi\.json$|^/docs|^/redoc")
    zero = "00000000-0000-0000-0000-000000000000"
    abertas, n = [], 0
    for caminho, ops in sorted(api["paths"].items()):
        if publicas.search(caminho):
            continue
        alvo = re.sub(r"\{[^}]+\}", zero, caminho)
        for metodo in ops:
            n += 1
            cod, corpo = chamar(None, metodo.upper(), alvo, {} if metodo != "get" else None)
            if cod in (401, 403):
                continue
            if metodo == "get" and str(corpo).lstrip().lower().startswith(("<!doctype", "<html")):
                continue                      # página de tela: o HTML é público, os dados dela pedem login
            abertas.append(f"{metodo.upper()} {caminho} → {cod}")
    assert n > 300, f"só {n} rotas verificadas — lista incompleta"
    assert not abertas, f"{len(abertas)} rota(s) respondem sem login: " + "; ".join(abertas[:8])
