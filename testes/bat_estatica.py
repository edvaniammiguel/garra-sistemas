# Bateria — ESTRUTURA DO CÓDIGO: o que quebra sem ninguém clicar (21/11/2026)
# Não usa servidor nem banco: lê os arquivos do repositório.
import os, re, json, subprocess, tempfile, shutil
from bat_base import teste, Pular

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORA = (os.sep + "testes" + os.sep, os.sep + ".git" + os.sep, "__pycache__", "node_modules")


def _arquivos(*ext):
    for pasta, _, nomes in os.walk(RAIZ):
        if any(f in pasta + os.sep for f in FORA):
            continue
        for n in nomes:
            if n.endswith(ext):
                yield os.path.join(pasta, n)


def _rel(p):
    return os.path.relpath(p, RAIZ)


def _ler(p):
    return open(p, encoding="utf-8", errors="replace").read()


def _scripts(html):
    """Blocos <script> sem src (texto JS de verdade)."""
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        attrs, corpo = m.group(1), m.group(2)
        if "src=" in attrs.lower() or not corpo.strip():
            continue
        if re.search(r'type\s*=\s*["\'](?!text/javascript|module)', attrs, re.I):
            continue
        yield html[:m.start(2)].count("\n") + 1, corpo


@teste("Estrutura", "todo arquivo Python do sistema compila")
def python_compila():
    ruins = []
    for p in _arquivos(".py"):
        try:
            compile(_ler(p), p, "exec")
        except SyntaxError as e:
            ruins.append(f"{_rel(p)}:{e.lineno} {e.msg}")
    assert not ruins, "; ".join(ruins)


@teste("Estrutura", "nenhuma rota do servidor é declarada duas vezes")
def rotas_duplicadas():
    vistas, dup = {}, []
    for p in _arquivos(".py"):
        for i, linha in enumerate(_ler(p).splitlines(), 1):
            m = re.match(r'\s*@(?:router|app)\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)["\']', linha)
            if m:
                ch = (m.group(1).upper(), m.group(2))
                if ch in vistas:
                    dup.append(f"{ch[0]} {ch[1]} em {vistas[ch]} e {_rel(p)}:{i}")
                else:
                    vistas[ch] = f"{_rel(p)}:{i}"
    assert not dup, f"{len(dup)} rota(s) duplicada(s): " + "; ".join(dup[:8])


@teste("Estrutura", "nenhuma função do servidor é definida duas vezes no mesmo arquivo")
def funcoes_python_duplicadas():
    dup = []
    for p in _arquivos(".py"):
        vistas = {}
        for i, linha in enumerate(_ler(p).splitlines(), 1):
            m = re.match(r"(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", linha)      # só nível do módulo (coluna 0)
            if m:
                if m.group(1) in vistas:
                    dup.append(f"{_rel(p)}: {m.group(1)} (linhas {vistas[m.group(1)]} e {i})")
                vistas[m.group(1)] = i
    assert not dup, f"{len(dup)} função(ões) repetida(s): " + "; ".join(dup[:8])


@teste("Estrutura", "todo JavaScript das telas tem sintaxe válida")
def javascript_valido():
    node = shutil.which("node")
    if not node:
        raise Pular("este laboratório não tem o Node instalado")
    ruins, n = [], 0
    with tempfile.TemporaryDirectory() as tmp:
        for p in list(_arquivos(".html")) + list(_arquivos(".js")):
            blocos = [(1, _ler(p))] if p.endswith(".js") else list(_scripts(_ler(p)))
            if p.endswith(".js"):
                try:
                    json.loads(blocos[0][1]); continue          # arquivo .js que na verdade é JSON (manifesto)
                except ValueError:
                    pass
            for linha0, corpo in blocos:
                n += 1
                arq = os.path.join(tmp, "b.js")
                open(arq, "w", encoding="utf-8").write(corpo)
                r = subprocess.run([node, "--check", arq], capture_output=True, text=True)
                if r.returncode:
                    msg = [l for l in r.stderr.splitlines() if "Error" in l][:1]
                    lin = re.search(r"b\.js:(\d+)", r.stderr)
                    ruins.append(f"{_rel(p)} linha ~{linha0 + int(lin.group(1)) - 1 if lin else linha0}: {(msg or ['erro de sintaxe'])[0][:90]}")
    assert n, "nenhum bloco de script encontrado"
    assert not ruins, f"{len(ruins)} bloco(s) com erro: " + "; ".join(ruins[:6])


def _funcoes_js(texto):
    """nome → linhas onde há 'function nome(' no nível de cima (sem recuo ou recuo do bloco <script>)."""
    achados = {}
    for i, linha in enumerate(texto.splitlines(), 1):
        m = re.match(r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", linha)
        if m:
            achados.setdefault(m.group(1), []).append(i)
    return achados


@teste("Estrutura", "nenhuma função de tela é declarada duas vezes (a última ganharia em silêncio)")
def funcoes_js_duplicadas():
    dup = []
    for p in list(_arquivos(".html")) + list(_arquivos(".js")):
        for nome, linhas in _funcoes_js(_ler(p)).items():
            if len(linhas) > 1:
                dup.append(f"{_rel(p)}: {nome} (linhas {', '.join(map(str, linhas))})")
    assert not dup, f"{len(dup)} função(ões) repetida(s): " + "; ".join(dup[:8])


@teste("Estrutura", "todo botão das telas chama uma função que existe")
def botoes_sem_funcao():
    # nomes definidos em qualquer .js do repositório contam para todas as telas (são carregados por <script src>)
    globais = set()
    for p in _arquivos(".js"):
        t = _ler(p)
        globais |= set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", t))
        globais |= set(re.findall(r"(?:window\.|const\s+|let\s+|var\s+)([A-Za-z_$][\w$]*)\s*=", t))
    nativos = {"alert", "confirm", "prompt", "event", "this", "if", "return", "window", "document", "history", "location", "print",
               "setTimeout", "clearTimeout", "setInterval", "clearInterval", "fetch", "console", "navigator", "open", "close", "Number", "String", "parseInt", "parseFloat", "void", "new", "typeof"}
    orfaos = []
    for p in _arquivos(".html"):
        t = _ler(p)
        definidos = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)\s*\(", t))
        definidos |= set(re.findall(r"(?:window\.|const\s+|let\s+|var\s+)([A-Za-z_$][\w$]*)\s*=", t))
        usados = {}
        for m in re.finditer(r"""\bon(?:click|change|input|submit|keyup|keydown|blur|focus|dblclick|contextmenu)\s*=\s*\\?["']\s*(?:return\s+)?([A-Za-z_$][\w$]*)\s*\(""", t):
            usados.setdefault(m.group(1), t[:m.start()].count("\n") + 1)
        for nome, linha in sorted(usados.items()):
            if nome not in definidos and nome not in globais and nome not in nativos:
                orfaos.append(f"{_rel(p)}:{linha} {nome}()")
    assert not orfaos, f"{len(orfaos)} chamada(s) sem função: " + "; ".join(orfaos[:10])
