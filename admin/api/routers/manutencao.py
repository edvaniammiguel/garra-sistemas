# ══════════════════════════════════════════════════════════════
# MÓDULO MANUTENÇÃO — fundação v26 (06/07/2026)
# Referência: ManWinWin Express. Lifecycle da OT (Bruna):
#   aberta → em_andamento → aguardando_peca → concluida | cancelada
# Categoria 'apoio' (Combinado) é EXCLUÍDA de OT por decisão de projeto.
# ══════════════════════════════════════════════════════════════
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
import os

from core.auth import verificar_token, verificar_gestor
from core.db import ajard_query, ajard_query_id

router = APIRouter()

_TRANSICOES = {
    "aberta":          {"em_andamento", "cancelada"},
    "em_andamento":    {"aguardando_peca", "concluida", "cancelada"},
    "aguardando_peca": {"em_andamento", "concluida", "cancelada"},
    "concluida":       set(),
    "cancelada":       set(),
}


async def _usuario_id(payload):
    u = await ajard_query(
        "SELECT id FROM public.usuarios_garra WHERE login=%s",
        (payload.get("sub", ""),), fetch="one")
    return u["id"] if u else None


# ── FORNECEDORES (cadastro único, public) ─────────────────────

@router.get("/manutencao/api/fornecedores")
async def listar_fornecedores(_auth=Depends(verificar_token)):
    rows = await ajard_query(
        "SELECT * FROM public.fornecedores WHERE ativo=true ORDER BY nome")
    return [dict(r) for r in rows]


@router.post("/manutencao/api/fornecedores")
async def criar_fornecedor(request: Request, payload=Depends(verificar_gestor)):
    d = await request.json()
    nome = (d.get("nome") or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome é obrigatório")
    row = await ajard_query_id(
        """INSERT INTO public.fornecedores (nome, cnpj, telefone, email, tipo, observacao)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (nome, d.get("cnpj"), d.get("telefone"), d.get("email"),
         d.get("tipo", "pecas"), d.get("observacao")))
    return dict(row)


@router.patch("/manutencao/api/fornecedores/{fid}")
async def editar_fornecedor(fid: str, request: Request, payload=Depends(verificar_gestor)):
    d = await request.json()
    campos = ["nome", "cnpj", "telefone", "email", "tipo", "observacao", "ativo"]
    updates, params = [], []
    for c in campos:
        if c in d:
            updates.append(f"{c}=%s")
            params.append(d[c])
    if not updates:
        return {"ok": True}
    params.append(fid)
    await ajard_query(
        f"UPDATE public.fornecedores SET {', '.join(updates)} WHERE id=%s",
        params, fetch="none")
    return {"ok": True}


# ── ORDENS DE TRABALHO (OT) ───────────────────────────────────

@router.post("/manutencao/api/ots")
async def abrir_ot(request: Request, payload=Depends(verificar_token)):
    """Abre OT. Bloqueia categoria 'apoio' e captura o horímetro atual."""
    d = await request.json()
    eq_id = d.get("equipamento_id")
    descricao = (d.get("descricao") or "").strip()
    if not eq_id or not descricao:
        raise HTTPException(status_code=400, detail="Equipamento e descrição são obrigatórios")

    eq = await ajard_query(
        "SELECT id, codigo, categoria, horimetro_atual FROM operacional.equipamentos WHERE id=%s AND ativo=true",
        (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    if (eq.get("categoria") or "").lower() == "apoio":
        raise HTTPException(
            status_code=400,
            detail="Equipamento de Apoio/Combinado não entra em manutenção (decisão de projeto).")

    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.ot
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    from datetime import date as _date
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"

    uid = await _usuario_id(payload)
    row = await ajard_query_id(
        """INSERT INTO manutencao.ot
              (numero, ano, sequencia, equipamento_id, tipo, prioridade,
               descricao, solicitante_id, responsavel_id, fornecedor_id,
               horimetro_na_abertura)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (numero, ano, int(seq["n"]), eq_id,
         d.get("tipo", "corretiva"), d.get("prioridade", "media"),
         descricao, uid, d.get("responsavel_id"), d.get("fornecedor_id"),
         eq.get("horimetro_atual")))
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,NULL,'aberta','OT criada',%s)""",
        (row["id"], uid), fetch="none")
    return dict(row)


@router.get("/manutencao/api/ots")
async def listar_ots(status: str = None, equipamento_id: str = None,
                     _auth=Depends(verificar_token)):
    where, params = ["ot.ativo=true"], []
    if status:
        params.append(status)
        where.append("ot.status=%s")
    if equipamento_id:
        params.append(equipamento_id)
        where.append("ot.equipamento_id=%s")
    rows = await ajard_query(
        f"""SELECT ot.*, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                  us.nome AS solicitante_nome, ur.nome AS responsavel_nome,
                  fo.nome AS fornecedor_nome
           FROM manutencao.ot ot
           JOIN operacional.equipamentos eq ON eq.id = ot.equipamento_id
           LEFT JOIN public.usuarios_garra us ON us.id = ot.solicitante_id
           LEFT JOIN public.usuarios_garra ur ON ur.id = ot.responsavel_id
           LEFT JOIN public.fornecedores fo ON fo.id = ot.fornecedor_id
           WHERE {' AND '.join(where)}
           ORDER BY ot.data_abertura DESC""", params)
    return [dict(r) for r in rows]


@router.get("/manutencao/api/ots/{ot_id}")
async def detalhe_ot(ot_id: str, _auth=Depends(verificar_token)):
    ot = await ajard_query(
        """SELECT ot.*, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc
           FROM manutencao.ot ot
           JOIN operacional.equipamentos eq ON eq.id = ot.equipamento_id
           WHERE ot.id=%s AND ot.ativo=true""", (ot_id,), fetch="one")
    if not ot:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    hist = await ajard_query(
        """SELECT h.*, u.nome AS usuario_nome
           FROM manutencao.ot_historico h
           LEFT JOIN public.usuarios_garra u ON u.id = h.usuario_id
           WHERE h.ot_id=%s ORDER BY h.criado_em""", (ot_id,))
    d = dict(ot)
    d["historico"] = [dict(h) for h in hist]
    return d


@router.patch("/manutencao/api/ots/{ot_id}/status")
async def mudar_status_ot(ot_id: str, request: Request, payload=Depends(verificar_token)):
    d = await request.json()
    novo = (d.get("status") or "").strip()
    ot = await ajard_query(
        "SELECT id, status FROM manutencao.ot WHERE id=%s AND ativo=true",
        (ot_id,), fetch="one")
    if not ot:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    atual = ot["status"]
    if novo not in _TRANSICOES.get(atual, set()):
        raise HTTPException(
            status_code=400,
            detail=f"Transição inválida: {atual} → {novo}")
    uid = await _usuario_id(payload)
    extras, params = "", [novo]
    if novo == "concluida":
        extras = ", data_conclusao=now(), observacao_conclusao=%s, custo_total=%s"
        params += [(d.get("observacao") or "").strip() or None, d.get("custo_total")]
    params.append(ot_id)
    await ajard_query(
        f"UPDATE manutencao.ot SET status=%s, atualizado_em=now(){extras} WHERE id=%s",
        params, fetch="none")
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, atual, novo, (d.get("observacao") or "").strip() or None, uid), fetch="none")
    return {"ok": True, "status": novo}


@router.patch("/manutencao/api/ots/{ot_id}")
async def editar_ot(ot_id: str, request: Request, payload=Depends(verificar_gestor)):
    d = await request.json()
    campos = ["tipo", "prioridade", "descricao", "responsavel_id",
              "fornecedor_id", "custo_total"]
    updates, params = [], []
    for c in campos:
        if c in d:
            updates.append(f"{c}=%s")
            params.append(d[c])
    if not updates:
        return {"ok": True}
    params.append(ot_id)
    await ajard_query(
        f"UPDATE manutencao.ot SET {', '.join(updates)}, atualizado_em=now() WHERE id=%s",
        params, fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/resumo")
async def resumo_manutencao(_auth=Depends(verificar_token)):
    rows = await ajard_query(
        """SELECT status, COUNT(*)::int AS n
           FROM manutencao.ot WHERE ativo=true GROUP BY status""")
    base = {"aberta": 0, "em_andamento": 0, "aguardando_peca": 0,
            "concluida": 0, "cancelada": 0}
    for r in rows:
        base[r["status"]] = r["n"]
    return base


# ── PÁGINA DO MÓDULO (desktop, protótipo em ligação progressiva) ──
@router.get("/manutencao", response_class=HTMLResponse)
async def manutencao_desktop():
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "manutencao"))
    # Aceita os dois layouts de pasta (robustez a variações de upload)
    candidatos = [
        os.path.join(raiz, "static", "manutencao.html"),
        os.path.join(raiz, "manutencao.html"),
    ]
    for p in candidatos:
        if os.path.isfile(p):
            return open(p, encoding="utf-8").read()
    raise HTTPException(status_code=404, detail="manutencao.html não encontrado no repositório")


# ── SEMÁFOROS DA FROTA (Onda 3 viva — 06/07/2026) ──
# Junta equipamentos + pontos de controle + OTs abertas; status pelo pior
# ponto de cada equipamento: 🔴 >= máximo · 🟠 >= urgente · 🟡 >= atenção · 🟢 ok

@router.get("/manutencao/api/semaforos")
async def semaforos_frota(_auth=Depends(verificar_token)):
    rows = await ajard_query(
        """SELECT e.id, e.codigo, e.descricao, e.horimetro_atual, e.medicao,
                  p.codigo AS ponto, p.leitura_atual, p.limiar_atencao,
                  p.limiar_urgente, p.limiar_maximo,
                  (SELECT COUNT(*)::int FROM manutencao.ot o
                    WHERE o.equipamento_id = e.id AND o.ativo = true
                      AND o.status IN ('aberta','em_andamento','aguardando_peca')) AS ots_abertas
           FROM operacional.equipamentos e
           LEFT JOIN manutencao.pontos_controle p
                  ON p.equipamento_id = e.id AND p.ativo = true
           WHERE e.ativo = true AND COALESCE(e.categoria,'') <> 'apoio'
           ORDER BY e.codigo, p.codigo""")
    equipes = {}
    ordem = {"vermelho": 0, "laranja": 1, "amarelo": 2, "verde": 3}
    for r in rows:
        eq = equipes.setdefault(str(r["id"]), {
            "id": str(r["id"]), "codigo": r["codigo"], "descricao": r["descricao"],
            "horimetro": float(r["horimetro_atual"] or 0), "medicao": r["medicao"],
            "ots_abertas": r["ots_abertas"], "status": "verde", "pontos": []})
        if r["ponto"]:
            leit = float(r["leitura_atual"] or 0)
            st = "verde"
            if r["limiar_maximo"] is not None and leit >= float(r["limiar_maximo"]): st = "vermelho"
            elif r["limiar_urgente"] is not None and leit >= float(r["limiar_urgente"]): st = "laranja"
            elif r["limiar_atencao"] is not None and leit >= float(r["limiar_atencao"]): st = "amarelo"
            eq["pontos"].append({"codigo": r["ponto"], "leitura": leit,
                                 "atencao": float(r["limiar_atencao"] or 0) or None,
                                 "urgente": float(r["limiar_urgente"] or 0) or None,
                                 "maximo": float(r["limiar_maximo"] or 0) or None,
                                 "status": st})
            if ordem[st] < ordem[eq["status"]]:
                eq["status"] = st
    lista = sorted(equipes.values(), key=lambda x: (ordem[x["status"]], x["codigo"]))
    return lista


# ── ALMOXARIFADO: peças reais (Onda 1) com busca ──
@router.get("/manutencao/api/pecas")
async def listar_pecas(busca: str = None, limit: int = 100, _auth=Depends(verificar_token)):
    limit = min(max(int(limit or 100), 1), 300)
    if busca and busca.strip():
        b = f"%{busca.strip()}%"
        rows = await ajard_query(
            """SELECT codigo, descricao, unidade, familia_codigo, custo_medio
               FROM manutencao.pecas WHERE ativo=true
                 AND (codigo ILIKE %s OR descricao ILIKE %s)
               ORDER BY codigo LIMIT """ + str(limit), (b, b))
    else:
        rows = await ajard_query(
            """SELECT codigo, descricao, unidade, familia_codigo, custo_medio
               FROM manutencao.pecas WHERE ativo=true
               ORDER BY codigo LIMIT """ + str(limit))
    total = await ajard_query("SELECT COUNT(*)::int AS n FROM manutencao.pecas WHERE ativo=true", fetch="one")
    return {"total": total["n"], "itens": [dict(r) for r in rows]}


# ── FICHA DO EQUIPAMENTO: Ondas 1+2+3 agregadas ──
@router.get("/manutencao/api/equipamentos/{eq_id}/detalhe")
async def detalhe_equipamento(eq_id: str, _auth=Depends(verificar_token)):
    eq = await ajard_query(
        """SELECT e.*, p.codigo AS pai_codigo,
                  (SELECT COUNT(*)::int FROM operacional.equipamentos f
                    WHERE f.equipamento_pai = e.id) AS filhos
           FROM operacional.equipamentos e
           LEFT JOIN operacional.equipamentos p ON p.id = e.equipamento_pai
           WHERE e.id=%s""", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    pontos = await ajard_query(
        """SELECT codigo, leitura_atual, data_leitura, limiar_atencao, limiar_urgente, limiar_maximo
           FROM manutencao.pontos_controle WHERE equipamento_id=%s AND ativo=true ORDER BY codigo""", (eq_id,))
    planos = await ajard_query(
        """SELECT codigo, descricao, tempo_horas, hh_previsto, custo_previsto, plano_proximo_codigo
           FROM manutencao.planos WHERE equipamento_id=%s AND ativo=true ORDER BY codigo""", (eq_id,))
    ots = await ajard_query(
        """SELECT numero, tipo, prioridade, status, descricao, data_abertura, data_conclusao, custo_total
           FROM manutencao.ot WHERE equipamento_id=%s AND ativo=true
           ORDER BY data_abertura DESC LIMIT 25""", (eq_id,))
    filhos = await ajard_query(
        """SELECT codigo, descricao, posicao FROM operacional.equipamentos
           WHERE equipamento_pai=%s ORDER BY codigo""", (eq_id,))
    d = dict(eq)
    d["pontos"] = [dict(x) for x in pontos]
    d["planos"] = [dict(x) for x in planos]
    d["ots"] = [dict(x) for x in ots]
    d["componentes"] = [dict(x) for x in filhos]
    return d


# ── ALMOXARIFADOS + ESTOQUE (07/07/2026) ──
# Movimentos: entrada (destino) · saida (origem, valida saldo) ·
# transferencia (origem→destino, atômica) · ajuste (define saldo exato)

@router.get("/manutencao/api/almoxarifados")
async def listar_almoxarifados(_auth=Depends(verificar_token)):
    rows = await ajard_query(
        """SELECT a.id, a.codigo, a.nome,
                  COALESCE((SELECT COUNT(*) FROM manutencao.estoque e
                            WHERE e.almoxarifado_id=a.id AND e.quantidade>0),0)::int AS itens
           FROM manutencao.almoxarifados a WHERE a.ativo=true ORDER BY a.codigo""")
    return [dict(r) for r in rows]


@router.get("/manutencao/api/estoque/{peca_codigo}")
async def saldo_peca(peca_codigo: str, _auth=Depends(verificar_token)):
    rows = await ajard_query(
        """SELECT a.codigo AS almox, a.nome, COALESCE(e.quantidade,0) AS quantidade, e.minimo
           FROM manutencao.almoxarifados a
           LEFT JOIN manutencao.estoque e ON e.almoxarifado_id=a.id
             AND e.peca_id=(SELECT id FROM manutencao.pecas WHERE codigo=%s)
           WHERE a.ativo=true ORDER BY a.codigo""", (peca_codigo,))
    return [dict(r) for r in rows]


@router.post("/manutencao/api/estoque/movimentar")
async def movimentar_estoque(request: Request, payload=Depends(verificar_token)):
    d = await request.json()
    tipo = (d.get("tipo") or "").strip()
    qtd = float(d.get("quantidade") or 0)
    if tipo not in ("entrada", "saida", "transferencia", "ajuste"):
        raise HTTPException(status_code=400, detail="Tipo inválido")
    if qtd <= 0 and tipo != "ajuste":
        raise HTTPException(status_code=400, detail="Quantidade deve ser positiva")
    peca = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s OR id::text=%s",
                             (d.get("peca"), str(d.get("peca"))), fetch="one")
    if not peca:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    pid = peca["id"]

    async def _alm(cod):
        if not cod: return None
        a = await ajard_query("SELECT id FROM manutencao.almoxarifados WHERE codigo=%s OR id::text=%s",
                              (cod, str(cod)), fetch="one")
        if not a: raise HTTPException(status_code=404, detail=f"Almoxarifado {cod} não encontrado")
        return a["id"]

    origem = await _alm(d.get("origem"))
    destino = await _alm(d.get("destino"))

    async def _saldo(alm):
        r = await ajard_query("SELECT quantidade FROM manutencao.estoque WHERE peca_id=%s AND almoxarifado_id=%s",
                              (pid, alm), fetch="one")
        return float(r["quantidade"]) if r else 0.0

    async def _soma(alm, delta):
        await ajard_query("""
            INSERT INTO manutencao.estoque (peca_id, almoxarifado_id, quantidade)
            VALUES (%s,%s,%s)
            ON CONFLICT (peca_id, almoxarifado_id)
            DO UPDATE SET quantidade = manutencao.estoque.quantidade + EXCLUDED.quantidade""",
            (pid, alm, delta), fetch="none")

    if tipo == "entrada":
        if not destino: raise HTTPException(status_code=400, detail="Entrada exige destino")
        await _soma(destino, qtd)
    elif tipo == "saida":
        if not origem: raise HTTPException(status_code=400, detail="Saída exige origem")
        if await _saldo(origem) < qtd:
            raise HTTPException(status_code=400, detail="Saldo insuficiente no almoxarifado de origem")
        await _soma(origem, -qtd)
    elif tipo == "transferencia":
        if not origem or not destino: raise HTTPException(status_code=400, detail="Transferência exige origem e destino")
        if await _saldo(origem) < qtd:
            raise HTTPException(status_code=400, detail="Saldo insuficiente no almoxarifado de origem")
        await _soma(origem, -qtd)
        await _soma(destino, qtd)
    elif tipo == "ajuste":
        if not destino: raise HTTPException(status_code=400, detail="Ajuste exige destino")
        atual = await _saldo(destino)
        await _soma(destino, qtd - atual)

    uid = await _usuario_id(payload)
    await ajard_query("""
        INSERT INTO manutencao.movimentacoes (tipo, peca_id, almox_origem, almox_destino, quantidade, ot_id, usuario_id, observacao)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tipo, pid, origem, destino, qtd, d.get("ot_id"), uid, (d.get("observacao") or "").strip() or None), fetch="none")
    return {"ok": True, "saldo_origem": (await _saldo(origem)) if origem else None,
            "saldo_destino": (await _saldo(destino)) if destino else None}


# ── PARAMETRIZAÇÃO (Regra 63): domínios editáveis ──
_DOMINIOS_PARAM = {
    "tipos-manutencao": "manutencao.tipos_manutencao",
    "tipos-trabalho": "manutencao.tipos_trabalho",
    "setores-interventor": "manutencao.setores_interventor",
    "setores-atividade": "manutencao.setores_atividade",
    "tipos-equipamento": "manutencao.tipos_equipamento",
    "sistemas": "manutencao.sistemas",
    "familias": "manutencao.familias",
    "sintomas": "manutencao.sintomas",
    "causas": "manutencao.causas",
}

@router.get("/manutencao/api/param/{dominio}")
async def listar_dominio(dominio: str, _auth=Depends(verificar_token)):
    tabela = _DOMINIOS_PARAM.get(dominio)
    if not tabela:
        raise HTTPException(status_code=404, detail="Domínio não parametrizável")
    rows = await ajard_query(f"SELECT * FROM {tabela} ORDER BY 1")
    return [dict(r) for r in rows]


@router.post("/manutencao/api/param/{dominio}")
async def upsert_dominio(dominio: str, request: Request, payload=Depends(verificar_gestor)):
    tabela = _DOMINIOS_PARAM.get(dominio)
    if not tabela:
        raise HTTPException(status_code=404, detail="Domínio não parametrizável")
    d = await request.json()
    cod = (d.get("codigo") or "").strip()
    nome = (d.get("nome") or "").strip()
    if not cod or not nome:
        raise HTTPException(status_code=400, detail="Código e nome são obrigatórios")
    pk = "sigla" if "tipos_equipamento" in tabela else "codigo"
    await ajard_query(
        f"""INSERT INTO {tabela} ({pk}, nome) VALUES (%s,%s)
            ON CONFLICT ({pk}) DO UPDATE SET nome=EXCLUDED.nome""",
        (cod, nome), fetch="none")
    return {"ok": True}
