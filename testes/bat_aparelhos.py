# Bateria — Aparelhos (23/11/2026): o mesmo aparelho não pode virar vários registros, e os duplicados antigos se conciliam sozinhos
import hashlib
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import jwt

from bat_base import teste, chamar, sql, sql1, usuario, status, igual, token, SEGREDO, URL

UA = "Mozilla/5.0 (Linux; Android 14; BAT-APARELHO) Chrome/130 Mobile"
K1, K2 = "bat-aparelho-chave-um-" + "1" * 24, "bat-aparelho-chave-dois-" + "2" * 22


def _h(chave):
    return hashlib.sha256(chave.encode()).hexdigest()


def _limpar():
    sql("delete from public.acesso_dispositivos where user_agent like '%BAT-APARELHO%' or apelido like 'BAT %'")


def _tok(papel, disp=None):
    u = usuario(papel)
    p = {"sub": u["login"], "login": u["login"], "perfil": u["perfil"], "exp": int(time.time()) + 3600}
    if disp:
        p["disp"] = disp
    return jwt.encode(p, SEGREDO, algorithm="HS256")


def _renovar(tok, cookie=None):
    """Devolve (registro do aparelho no token novo, cookie que o servidor mandou guardar)."""
    h = {"Authorization": "Bearer " + tok, "User-Agent": UA, "Content-Type": "application/json", "X-Forwarded-For": "203.0.113.7"}
    if cookie:
        h["Cookie"] = "garra_disp=" + cookie
    r = urllib.request.urlopen(urllib.request.Request(URL + "/auth/renovar", data=b"{}", headers=h, method="POST"), timeout=30)
    sc = r.headers.get("Set-Cookie") or ""
    corpo = json.loads(r.read())
    disp = jwt.decode(corpo["token"], SEGREDO, algorithms=["HS256"]).get("disp")
    return disp, (sc.split("garra_disp=")[1].split(";")[0] if "garra_disp=" in sc else None)


def _linha(uid, chave, classificacao="nao_classificado", apelido="", ativo=True):
    return sql1("insert into public.acesso_dispositivos (usuario_id, chave_hash, classificacao, apelido, ativo, user_agent) "
                "values ($1::uuid,$2,$3,$4,$5,$6) returning id::text as id", uid, _h(chave), classificacao, apelido, ativo, UA)["id"]


@teste("Aparelhos", "várias telas abrindo juntas num aparelho novo criam UM registro, não vários")
def um_so():
    _limpar()
    uid = usuario("mec")["id"]
    tok = _tok("mec")                                   # token antigo, sem aparelho
    with ThreadPoolExecutor(5) as ex:
        res = list(ex.map(lambda _: _renovar(tok), range(5)))
    igual(len({c for _, c in res}), 1, "chaves diferentes devolvidas para o mesmo aparelho")
    igual(len({d for d, _ in res}), 1, "registros diferentes nos tokens")
    igual(sql1("select count(*) as n from public.acesso_dispositivos where usuario_id=$1::uuid and user_agent like '%BAT-APARELHO%'", uid)["n"],
          1, "registros do aparelho no banco")
    # o cookie guardado mantém o mesmo registro nas próximas vezes
    d2, _ = _renovar(tok, res[0][1])
    igual(d2, res[0][0], "com o cookie guardado, continua o mesmo registro")
    # outra pessoa, mesmo navegador e mesma rede: NÃO cai no registro da primeira
    d_outro, c_outro = _renovar(_tok("op1"))
    assert c_outro != res[0][1], "duas pessoas diferentes receberam a mesma chave de aparelho"


@teste("Aparelhos", "duplicado antigo se concilia: fica um registro, com o nome e a classificação já dados")
def concilia():
    _limpar()
    uid = usuario("mec")["id"]
    velho = _linha(uid, K1, "corporativo", "BAT Notebook da Patricia")      # o que a administração já nomeou
    novo = _linha(uid, K2)                                                   # o que "voltou sem nome"
    disp, _ = _renovar(_tok("mec", velho), K2)                               # token aponta para um, cookie para o outro
    igual(disp, novo, "o token passa a apontar para o registro do cookie")
    r = sql1("select classificacao, apelido, ativo, substituido_por from public.acesso_dispositivos where id=$1::uuid", novo)
    igual((r["classificacao"], r["apelido"], r["ativo"], r["substituido_por"]), ("corporativo", "BAT Notebook da Patricia", True, None),
          "o registro que ficou herdou nome e classificação")
    igual(str(sql1("select substituido_por from public.acesso_dispositivos where id=$1::uuid", velho)["substituido_por"]), novo,
          "o registro antigo aponta para o que ficou")
    lista = status(chamar("admin", "GET", "/acesso/aparelhos"), 200, "lista de aparelhos")
    meus = [a for a in lista if "BAT-APARELHO" in (a.get("user_agent") or "")]
    igual(len(meus), 1, "aparelhos na lista da administração")
    igual(meus[0]["apelido"], "BAT Notebook da Patricia", "nome que aparece na lista")
    # token de outro módulo, ainda com o registro antigo e sem cookie: já vale o registro que ficou
    d3, _ = _renovar(_tok("mec", velho))
    igual(d3, novo, "token antigo sem cookie também passa para o registro que ficou")
    # nada muda quando token e cookie já batem
    d4, _ = _renovar(_tok("mec", novo), K2)
    igual(d4, novo, "token e cookie iguais: mesmo registro")
    igual(sql1("select count(*) as n from public.acesso_dispositivos where usuario_id=$1::uuid and user_agent like '%BAT-APARELHO%'", uid)["n"], 2,
          "nenhum registro novo criado na conciliação")


@teste("Aparelhos", "token levado para outro tipo de aparelho não junta os dois nem passa 'da empresa' adiante")
def nao_junta_aparelhos_diferentes():
    _limpar()
    uid = usuario("mec")["id"]
    notebook = sql1("insert into public.acesso_dispositivos (usuario_id, chave_hash, classificacao, apelido, user_agent) "
                    "values ($1::uuid,$2,'corporativo','BAT notebook',$3) returning id::text as id", uid, _h(K1),
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; BAT-APARELHO) Chrome/130")["id"]
    celular = _linha(uid, K2)                                   # celular (Android), não classificado
    disp, _ = _renovar(_tok("mec", notebook), K2)               # token do notebook usado no celular
    igual(disp, notebook, "o token continua no registro dele")
    r = sql1("select classificacao, apelido from public.acesso_dispositivos where id=$1::uuid", celular)
    igual((r["classificacao"], r["apelido"]), ("nao_classificado", ""), "o celular não herdou 'da empresa'")
    igual(sql1("select substituido_por from public.acesso_dispositivos where id=$1::uuid", notebook)["substituido_por"], None,
          "o notebook não foi substituído")


@teste("Aparelhos", "conciliar duplicado não devolve acesso a aparelho revogado")
def revogado_fica_revogado():
    _limpar()
    uid = usuario("mec")["id"]
    velho = _linha(uid, K1, "pessoal", "BAT revogado", ativo=False)
    novo = _linha(uid, K2)
    disp, _ = _renovar(_tok("mec", velho), K2)
    igual(disp, novo, "passa para o registro do cookie")
    igual(sql1("select ativo from public.acesso_dispositivos where id=$1::uuid", novo)["ativo"], False, "o registro que ficou continua revogado")
    _limpar()
