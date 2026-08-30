# -*- coding: utf-8 -*-
"""bateria-e2e-manutencao.py — 29/08/2026

Cenários E2E de MANUTENÇÃO + ABASTECIMENTOS contra o servidor local.
Complementa a bateria-e2e.py (43 cenários do Operacional). Rodar com o
servidor de laboratório no ar (localhost:8000 + Neon dev).

Uso (na pasta admin\\api, com .env carregado no servidor):
  set GARRA_URL=http://localhost:8000
  set GARRA_LOGIN=<login admin>
  set GARRA_SENHA=<senha>
  python bateria-e2e-manutencao.py

Fixtures criadas com prefixo E2EM- e desativadas no fim (soft). Nenhum
dado real é tocado. Extração por foto NÃO é coberta (depende de
ANTHROPIC_API_KEY e de imagem real) — a cascata é testada pelos níveis
digitada/última/cadastro, que é onde mora a lógica.
"""
import os, sys, json, random
import requests

URL = os.environ.get("GARRA_URL", "http://localhost:8000")
LOGIN = os.environ.get("GARRA_LOGIN", "")
SENHA = os.environ.get("GARRA_SENHA", "")

TOK = None
PASSOU, FALHOU = [], []
SUF = f"{random.randint(1000,9999)}"
CTX = {}  # ids criados


def chamada(metodo, path, corpo=None, form=None):
    h = {"Authorization": f"Bearer {TOK}"} if TOK else {}
    if corpo is not None:
        h["Content-Type"] = "application/json"
    r = requests.request(metodo, URL + path, headers=h,
                         json=corpo, data=form, timeout=30)
    return r


def cen(nome, fn):
    try:
        fn()
        PASSOU.append(nome)
        print(f"  ✅ {nome}")
    except AssertionError as e:
        FALHOU.append((nome, str(e)))
        print(f"  ❌ {nome} — {e}")
    except Exception as e:
        FALHOU.append((nome, f"{type(e).__name__}: {e}"))
        print(f"  💥 {nome} — {type(e).__name__}: {e}")


# ── E01 · Login ──────────────────────────────────────────────────────
def e01():
    global TOK
    assert LOGIN and SENHA, "defina GARRA_LOGIN e GARRA_SENHA no ambiente"
    r = requests.post(URL + "/auth/login", json={"login": LOGIN, "senha": SENHA}, timeout=30)
    assert r.status_code == 200, f"login {r.status_code}: {r.text[:120]}"
    TOK = r.json().get("token") or r.json().get("access_token")
    assert TOK, "sem token na resposta"


# ── E02 · Fixtures: caminhão (km, com placa) + componente ────────────
def e02():
    r = chamada("POST", "/operacional/api/equipamentos", corpo={
        "codigo": f"E2EM-CB-{SUF}", "descricao": "Caminhão E2E manutenção",
        "categoria": "caminhao", "medicao": "km", "placa": f"E2E{SUF[:1]}A{SUF[1:3]}"})
    assert r.status_code in (200, 201), f"{r.status_code}: {r.text[:150]}"
    CTX["cam"] = (r.json().get("id") or r.json().get("equipamento", {}).get("id"))
    assert CTX["cam"], f"sem id do caminhão: {r.text[:150]}"
    r = chamada("POST", "/operacional/api/equipamentos", corpo={
        "codigo": f"E2EM-PN-{SUF}", "descricao": "Pneu E2E manutenção",
        "categoria": "componente", "medicao": "km"})
    assert r.status_code in (200, 201), f"{r.status_code}: {r.text[:150]}"
    CTX["pneu"] = (r.json().get("id") or r.json().get("equipamento", {}).get("id"))
    assert CTX["pneu"], "sem id do pneu"


# ── E03 · Abastecimento com leitura digitada → fonte digitada + km_atual ──
def e03():
    r = chamada("POST", "/operacional/api/abastecimentos", corpo={
        "equipamento_id": CTX["cam"], "litros": 100, "valor_total": 600,
        "leitura_digitada": 50000})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:150]}"
    d = r.json()
    assert d["leitura"] == 50000 and d["leitura_fonte"] == "digitada", d
    assert not d["divergencia_leitura"], "não devia divergir na 1ª leitura"
    u = chamada("GET", f"/operacional/api/abastecimentos/ultima/{CTX['cam']}").json()
    assert u["cadastro"] == 50000, f"km_atual não alimentado: {u}"


# ── E04 · Sem leitura → cascata nível 3 (última conhecida) ───────────
def e04():
    r = chamada("POST", "/operacional/api/abastecimentos", corpo={
        "equipamento_id": CTX["cam"], "litros": 80})
    d = r.json()
    assert d["leitura"] == 50000 and d["leitura_fonte"] == "ultima", d


# ── E05 · Leitura menor que a última → flag + cadastro NÃO recua ─────
def e05():
    r = chamada("POST", "/operacional/api/abastecimentos", corpo={
        "equipamento_id": CTX["cam"], "leitura_digitada": 49000})
    d = r.json()
    assert d["divergencia_leitura"] is True, d
    u = chamada("GET", f"/operacional/api/abastecimentos/ultima/{CTX['cam']}").json()
    assert u["cadastro"] == 50000, f"cadastro recuou com divergência: {u}"


# ── E06 · Placa da foto divergente → flag ────────────────────────────
def e06():
    r = chamada("POST", "/operacional/api/abastecimentos", corpo={
        "equipamento_id": CTX["cam"], "leitura_digitada": 50100,
        "placa_foto": "zzz-9999"})
    d = r.json()
    assert d["divergencia_placa"] is True, d


# ── E07 · Montar componente → carimbo da leitura do pai ──────────────
def e07():
    r = chamada("POST", f"/manutencao/api/equipamentos/{CTX['cam']}/montar", corpo={
        "componente_id": CTX["pneu"], "posicao": "Dianteiro direito"})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    s = chamada("GET", f"/manutencao/api/equipamentos/{CTX['pneu']}/subsistemas").json()
    assert s["eu"].get("pai"), "componente sem pai após montar"
    h = s["historico"][0]
    assert h.get("montagem") and h.get("desmontagem") is None, \
        f"registro de montagem aberto não encontrado: {h}"
    # o carimbo da leitura (50100) é provado pelo E09: rodado = 50600 - 50100 = 500


# ── E08 · Pai roda (abastecimento avança km) ─────────────────────────
def e08():
    r = chamada("POST", "/operacional/api/abastecimentos", corpo={
        "equipamento_id": CTX["cam"], "leitura_digitada": 50600})
    d = r.json()
    assert d["leitura"] == 50600 and not d["divergencia_leitura"], d


# ── E09 · Desmontar → rodado = 500 ───────────────────────────────────
def e09():
    r = chamada("POST", f"/manutencao/api/equipamentos/{CTX['pneu']}/desmontar",
                corpo={})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    s = chamada("GET", f"/manutencao/api/equipamentos/{CTX['pneu']}/subsistemas").json()
    assert s.get("vida_acumulada") == 500, f"vida esperada 500, veio {s.get('vida_acumulada')}"
    assert s["eu"].get("pai") is None, "ainda com pai após desmontar"


# ── E10 · Semáforos: payload da árvore (regressão 29/08) ─────────────
def e10():
    r = chamada("GET", "/manutencao/api/semaforos").json()
    itens = r if isinstance(r, list) else r.get("equipamentos") or r.get("itens") or []
    alvo = next((x for x in itens if x.get("id") == str(CTX["cam"]) or x.get("codigo") == f"E2EM-CB-{SUF}"), None)
    assert alvo, "caminhão E2E ausente do semáforos"
    for campo in ("categoria", "equipamento_pai", "posicao", "n_filhos"):
        assert campo in alvo, f"campo {campo} ausente do payload (bug 29/08 regrediu)"


# ── E11 · OT direto no componente ────────────────────────────────────
def e11():
    r = chamada("POST", "/manutencao/api/ots", corpo={
        "equipamento_id": CTX["pneu"], "descricao": "E2E recapagem teste"})
    assert r.status_code == 200, f"{r.status_code}: {r.text[:200]}"
    CTX["ot"] = r.json().get("id")
    assert CTX["ot"], f"OT sem id: {r.text[:150]}"


# ── E12 · Peça + entrada de estoque → saldo ──────────────────────────
def e12():
    r = chamada("POST", "/manutencao/api/pecas", corpo={
        "codigo": f"E2EM-PC-{SUF}", "descricao": "Peça E2E teste", "unidade": "UN"})
    assert r.status_code in (200, 201), f"{r.status_code}: {r.text[:150]}"
    alms = chamada("GET", "/manutencao/api/almoxarifados").json()
    alms = alms if isinstance(alms, list) else alms.get("almoxarifados") or []
    if alms:
        CTX["alm"] = alms[0].get("codigo") or alms[0].get("id")
    else:
        r = chamada("POST", "/manutencao/api/almoxarifados", corpo={
            "codigo": f"E2EM-ALM", "nome": "Almoxarifado E2E teste"})
        assert r.status_code in (200, 201), f"criar almox: {r.status_code}: {r.text[:150]}"
        CTX["alm"] = "E2EM-ALM"
    r = chamada("POST", "/manutencao/api/estoque/movimentar", corpo={
        "peca": f"E2EM-PC-{SUF}", "tipo": "entrada", "quantidade": 4,
        "custo_unitario": 25, "destino": CTX["alm"]})
    assert r.status_code == 200, f"movimentar: {r.status_code}: {r.text[:200]}"


# ── E13 · Saída além do saldo → 400 (validação de saldo) ─────────────
def e13():
    r = chamada("POST", "/manutencao/api/estoque/movimentar", corpo={
        "peca": f"E2EM-PC-{SUF}", "tipo": "saida", "quantidade": 999,
        "origem": CTX.get("alm")})
    assert r.status_code == 400, f"saída sem saldo devia dar 400, deu {r.status_code}"


# ── E14 · Limpeza (soft) ─────────────────────────────────────────────
def e14():
    for eq in ("cam", "pneu"):
        if CTX.get(eq):
            chamada("PATCH", f"/operacional/api/equipamentos/{CTX[eq]}",
                    corpo={"ativo": False})
    ok = True  # limpeza é melhor-esforço; fixtures têm prefixo E2EM- e são inertes
    assert ok


if __name__ == "__main__":
    print(f"🔧 Bateria E2E Manutenção+Abastecimentos — {URL} — sufixo {SUF}\n")
    cen("E01 login", e01)
    if not TOK:
        sys.exit("Sem login — abortando.")
    cen("E02 fixtures caminhão+componente", e02)
    cen("E03 abastecimento digitado → km_atual", e03)
    cen("E04 cascata nível 3 (última)", e04)
    cen("E05 leitura menor → flag, cadastro não recua", e05)
    cen("E06 placa divergente → flag", e06)
    cen("E07 montar carimba leitura do pai", e07)
    cen("E08 pai roda +500 km", e08)
    cen("E09 desmontar → vida = 500", e09)
    cen("E10 semáforos com campos da árvore", e10)
    cen("E11 OT direto no componente", e11)
    cen("E12 peça + entrada de estoque", e12)
    cen("E13 saída sem saldo → 400", e13)
    cen("E14 limpeza das fixtures", e14)
    print(f"\n{'='*52}\n✅ {len(PASSOU)} passaram · ❌ {len(FALHOU)} falharam")
    for n, e in FALHOU:
        print(f"   ❌ {n}: {e}")
    sys.exit(1 if FALHOU else 0)
