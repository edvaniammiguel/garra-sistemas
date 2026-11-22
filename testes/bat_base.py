# Bateria de fluxos do Garra Sistemas — base comum (21/11/2026)
# Roda SOMENTE em laboratório, com dados fictícios marcados com "BAT".
# Configuração por variáveis de ambiente (os padrões são os do laboratório do chat):
#   BAT_URL  endereço do servidor de teste        BAT_JWT  segredo do JWT do laboratório
#   BAT_DSN  conexão do banco de teste            BAT_ADMIN / BAT_OP1 / BAT_OP2 / BAT_MEC  logins de teste
import os, json, time, asyncio, warnings, urllib.request, urllib.error, urllib.parse, datetime as dt

warnings.simplefilter("ignore")   # o segredo curto do laboratório gera aviso do PyJWT; não interessa aqui

URL = os.environ.get("BAT_URL", "http://127.0.0.1:8099").rstrip("/")
SEGREDO = os.environ.get("BAT_JWT", "teste-local")
DSN = os.environ.get("BAT_DSN", "postgresql://garra@/garra?host=/var/tmp/garra-pg&port=5499")
LOGINS = {"admin": os.environ.get("BAT_ADMIN", "edvania"), "op1": os.environ.get("BAT_OP1", "zeca"),
          "op2": os.environ.get("BAT_OP2", "eliton"), "mec": os.environ.get("BAT_MEC", "luiz")}
TAG = time.strftime("%d%H%M%S")          # marca desta rodada (deixa os dados únicos)
HOJE = dt.date.today()


def travar_producao():
    """(21/11/2026) A bateria grava e apaga dados: nunca pode apontar para a produção."""
    alvo = (URL + " " + DSN).lower()
    for proibido in ("garraterraplenagem", "onrender.com", "sistema-garra-us", "neon.tech"):
        if proibido in alvo and os.environ.get("BAT_SOU_LABORATORIO") != "sim":
            raise SystemExit("BATERIA RECUSADA: o endereço/banco parece de produção ou do Neon (" + proibido + "). "
                             "Use o laboratório. Para um banco de teste no Neon, defina BAT_SOU_LABORATORIO=sim.")
    if "garraterraplenagem" in alvo or "onrender.com" in alvo:
        raise SystemExit("BATERIA RECUSADA: endereço de produção.")


class Pular(Exception):
    """O teste não se aplica a este laboratório (ex.: tabela simplificada). Não é falha."""


# ── banco ──────────────────────────────────────────────────────────────────
_loop = asyncio.new_event_loop()
_con = None


def _conexao():
    global _con
    if _con is None:
        import asyncpg
        _con = _loop.run_until_complete(asyncpg.connect(DSN))
    return _con


def sql(texto, *args):
    """Uma linha → dict; várias → lista de dicts; comando → None."""
    c = _conexao()
    linhas = _loop.run_until_complete(c.fetch(texto, *args))
    return [dict(l) for l in linhas]


def sql1(texto, *args):
    l = sql(texto, *args)
    return l[0] if l else None


def colunas(tabela):
    esquema, nome = tabela.split(".")
    return {l["column_name"] for l in sql(
        "select column_name from information_schema.columns where table_schema=$1 and table_name=$2", esquema, nome)}


# ── usuários e tokens ──────────────────────────────────────────────────────
_usuarios = {}


def usuario(papel):
    if papel not in _usuarios:
        u = sql1("select id::text as id, login, perfil from public.usuarios_garra where login=$1", LOGINS[papel])
        if not u:
            raise Pular("usuário de teste '" + LOGINS[papel] + "' não existe neste laboratório")
        _usuarios[papel] = u
    return _usuarios[papel]


def token(papel, segredo=None, validade=3600):
    import jwt
    u = usuario(papel)
    return jwt.encode({"sub": u["login"], "login": u["login"], "perfil": u["perfil"], "exp": int(time.time()) + validade},
                      segredo or SEGREDO, algorithm="HS256")


# ── HTTP ───────────────────────────────────────────────────────────────────
def chamar(papel, metodo, caminho, corpo=None, tok=None):
    """Devolve (status, json|texto). papel=None → sem token."""
    cab = {"Content-Type": "application/json"}
    t = tok if tok is not None else (token(papel) if papel else None)
    if t:
        cab["Authorization"] = "Bearer " + t
    req = urllib.request.Request(URL + caminho, method=metodo, headers=cab,
                                 data=json.dumps(corpo).encode() if corpo is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            bruto = r.read()
            try:
                return r.status, json.loads(bruto or b"null")
            except ValueError:
                return r.status, bruto.decode("utf-8", "replace")[:300]
    except urllib.error.HTTPError as e:
        bruto = e.read()
        try:
            j = json.loads(bruto)
            return e.code, (j.get("detail", j) if isinstance(j, dict) else j)
        except ValueError:
            return e.code, bruto.decode("utf-8", "replace")[:300]
    except (urllib.error.URLError, OSError) as e:
        return 0, "sem resposta do servidor: " + str(e)[:120]


def q(texto):
    return urllib.parse.quote(str(texto), safe="")


# ── verificações ───────────────────────────────────────────────────────────
def igual(obtido, esperado, o_que):
    assert obtido == esperado, f"{o_que}: esperado {esperado!r}, veio {obtido!r}"


def status(resp, esperado, o_que):
    cod = resp[0]
    aceitos = esperado if isinstance(esperado, (tuple, list, set)) else (esperado,)
    assert cod in aceitos, f"{o_que}: esperado HTTP {'/'.join(map(str, aceitos))}, veio {cod} — {str(resp[1])[:160]}"
    return resp[1]


def perto(obtido, esperado, o_que, tol=0.011):
    assert obtido is not None and abs(float(obtido) - float(esperado)) <= tol, f"{o_que}: esperado {esperado}, veio {obtido}"


# ── registro dos testes ────────────────────────────────────────────────────
TESTES = []


def teste(modulo, descricao):
    def reg(f):
        TESTES.append((modulo, descricao, f))
        return f
    return reg


def equipamento_bat(codigo, medicao="km"):
    """Equipamento fictício da bateria (criado uma vez por laboratório)."""
    e = sql1("select id::text as id from operacional.equipamentos where codigo=$1", codigo)
    if e:
        return e["id"]
    cols = colunas("operacional.equipamentos")
    campos, vals = ["codigo", "descricao"], [codigo, "BAT equipamento de teste"]
    for c, v in (("ativo", True), ("medicao", medicao), ("categoria", "caminhao")):
        if c in cols:
            campos.append(c); vals.append(v)
    marc = ",".join("$" + str(i + 1) for i in range(len(vals)))
    return sql1(f"insert into operacional.equipamentos ({','.join(campos)}) values ({marc}) returning id::text as id", *vals)["id"]
