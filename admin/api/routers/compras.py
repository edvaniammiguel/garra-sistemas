# ══════════════════════════════════════════════════════════════
# MÓDULO COMPRAS — Ordens de Compra (v28)
# Fluxo: rascunho → solicitada → aprovada → enviada
#        → recebida_parcial → recebida | rejeitada | cancelada
# Regras:
#   - Toda OC tem SETOR (compras.setores, parametrizável — Regra 63)
#   - Alçadas por usuário (compras.alcadas): valor_limite NULL = sem limite;
#     OC dentro da alçada do criador → aprovada em 1 passo
#   - Aprovação/rejeição = assinatura digital (usuario + timestamp na trilha)
#   - Rejeição exige motivo
#   - Recebimento confere item a item (qtd_recebida acumulada)
#   - OC vinculada a OT: ao receber, o valor recebido soma no custo_total da OT
#   - peca_id em oc_itens é opcional — fase 2 liga ao manutencao.pecas/estoque
# Permissões (public.permissoes_colaborador):
#   'compras_solicitar' → criar/editar/enviar/receber OC
#   'compras_aprovar'   → aprovar/rejeitar (limitado pela alçada)
# ══════════════════════════════════════════════════════════════
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
import os

from core.auth import verificar_token
from core.db import ajard_query, ajard_query_id

router = APIRouter()

_PERFIS_COMPRAS = {"admin", "gestor", "luana"}

_TRANSICOES = {
    "rascunho":         {"solicitada", "cancelada"},
    "solicitada":       {"aprovada", "rejeitada", "cancelada"},
    "aprovada":         {"enviada", "cancelada"},
    "enviada":          {"recebida_parcial", "recebida", "cancelada"},
    "recebida_parcial": {"recebida", "cancelada"},
    "recebida":         set(),
    "rejeitada":        set(),
    "cancelada":        set(),
}


async def _usuario_id(payload):
    u = await ajard_query(
        "SELECT id FROM public.usuarios_garra WHERE login=%s",
        (payload.get("sub", ""),), fetch="one")
    return u["id"] if u else None


async def _tem_permissao(payload, modulo):
    """Perfil liberado OU permissão concedida em permissoes_colaborador."""
    if (payload.get("perfil") or "").lower() in _PERFIS_COMPRAS:
        return True
    uid = await _usuario_id(payload)
    if not uid:
        return False
    p = await ajard_query(
        """SELECT permitido FROM public.permissoes_colaborador
           WHERE usuario_id=%s AND modulo=%s""",
        (uid, modulo), fetch="one")
    return bool(p and p.get("permitido"))


async def verificar_compras(payload=Depends(verificar_token)):
    """Gate geral do módulo: solicitar OU aprovar dá acesso de leitura."""
    if await _tem_permissao(payload, "compras_solicitar"):
        return payload
    if await _tem_permissao(payload, "compras_aprovar"):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para o módulo Compras")


async def verificar_compras_aprovador(payload=Depends(verificar_token)):
    if await _tem_permissao(payload, "compras_aprovar"):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para aprovar compras")


async def _alcada_do_usuario(uid):
    """Retorna (tem_alcada, valor_limite). valor_limite None = sem limite.
    Sem linha em compras.alcadas = sem alçada nenhuma."""
    a = await ajard_query(
        "SELECT valor_limite FROM compras.alcadas WHERE usuario_id=%s AND ativo=true",
        (uid,), fetch="one")
    if not a:
        return (False, None)
    return (True, a.get("valor_limite"))


def _valor_dentro_alcada(valor, tem_alcada, limite):
    if not tem_alcada:
        return False
    if limite is None:
        return True
    try:
        return float(valor or 0) <= float(limite)
    except (TypeError, ValueError):
        return False


async def _trilha(oc_id, status_de, status_para, observacao, uid):
    await ajard_query(
        """INSERT INTO compras.oc_historico (oc_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (oc_id, status_de, status_para, observacao, uid), fetch="none")


async def _recalcular_total(oc_id):
    t = await ajard_query(
        """SELECT COALESCE(SUM(quantidade*valor_unit),0) AS total
           FROM compras.oc_itens WHERE oc_id=%s AND ativo=true""",
        (oc_id,), fetch="one")
    total = t["total"] if t else 0
    await ajard_query(
        "UPDATE compras.ordens_compra SET valor_total=%s, atualizado_em=now() WHERE id=%s",
        (total, oc_id), fetch="none")
    return total


# ── SETORES (parametrização — Regra 63) ───────────────────────

@router.get("/compras/api/setores")
async def listar_setores(_auth=Depends(verificar_compras)):
    rows = await ajard_query(
        "SELECT * FROM compras.setores WHERE ativo=true ORDER BY nome")
    return [dict(r) for r in rows]


@router.post("/compras/api/setores")
async def criar_setor(request: Request, _auth=Depends(verificar_compras_aprovador)):
    d = await request.json()
    codigo = (d.get("codigo") or "").strip().upper()
    nome = (d.get("nome") or "").strip()
    if not codigo or not nome:
        raise HTTPException(status_code=400, detail="Código e nome são obrigatórios")
    await ajard_query(
        """INSERT INTO compras.setores (codigo, nome, cor)
           VALUES (%s,%s,%s) ON CONFLICT (codigo) DO NOTHING""",
        (codigo, nome, d.get("cor")), fetch="none")
    return {"ok": True, "codigo": codigo}


@router.patch("/compras/api/setores/{codigo}")
async def editar_setor(codigo: str, request: Request,
                       _auth=Depends(verificar_compras_aprovador)):
    d = await request.json()
    updates, params = [], []
    for c in ("nome", "cor", "ativo"):
        if c in d:
            updates.append(f"{c}=%s")
            params.append(d[c])
    if not updates:
        return {"ok": True}
    params.append(codigo)
    await ajard_query(
        f"UPDATE compras.setores SET {', '.join(updates)} WHERE codigo=%s",
        params, fetch="none")
    return {"ok": True}


# ── ALÇADAS (parametrização) ──────────────────────────────────

@router.get("/compras/api/alcadas")
async def listar_alcadas(_auth=Depends(verificar_compras_aprovador)):
    rows = await ajard_query(
        """SELECT a.*, u.nome AS usuario_nome, u.login AS usuario_login, u.perfil
           FROM compras.alcadas a
           JOIN public.usuarios_garra u ON u.id = a.usuario_id
           ORDER BY u.nome""")
    return [dict(r) for r in rows]


@router.post("/compras/api/alcadas")
async def salvar_alcada(request: Request, _auth=Depends(verificar_compras_aprovador)):
    """Cria/atualiza a alçada de um usuário.
    valor_limite null no body = sem limite; ativo=false desativa."""
    d = await request.json()
    uid = d.get("usuario_id")
    if not uid:
        raise HTTPException(status_code=400, detail="usuario_id é obrigatório")
    await ajard_query(
        """INSERT INTO compras.alcadas (usuario_id, valor_limite, ativo)
           VALUES (%s,%s,%s)
           ON CONFLICT (usuario_id)
           DO UPDATE SET valor_limite=EXCLUDED.valor_limite,
                         ativo=EXCLUDED.ativo, atualizado_em=now()""",
        (uid, d.get("valor_limite"), d.get("ativo", True)), fetch="none")
    return {"ok": True}


# ── ORDENS DE COMPRA ──────────────────────────────────────────

@router.post("/compras/api/ocs")
async def criar_oc(request: Request, payload=Depends(verificar_compras)):
    """Cria OC em rascunho (cotação). Itens no body: lista de
    {descricao, quantidade, unidade, valor_unit, peca_id?}."""
    d = await request.json()
    setor = (d.get("setor_codigo") or "").strip().upper()
    if not setor:
        raise HTTPException(status_code=400, detail="Setor é obrigatório")
    s = await ajard_query(
        "SELECT codigo FROM compras.setores WHERE codigo=%s AND ativo=true",
        (setor,), fetch="one")
    if not s:
        raise HTTPException(status_code=404, detail="Setor não encontrado")

    itens = d.get("itens") or []
    if not itens:
        raise HTTPException(status_code=400, detail="A OC precisa de ao menos 1 item")

    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM compras.ordens_compra
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    from datetime import date as _date
    ano = _date.today().year
    numero = f"OC-{ano}-{int(seq['n']):04d}"

    uid = await _usuario_id(payload)
    row = await ajard_query_id(
        """INSERT INTO compras.ordens_compra
              (numero, ano, sequencia, setor_codigo, fornecedor_id, ot_id,
               equipamento_id, prioridade, condicao_pagamento, observacao,
               solicitante_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (numero, ano, int(seq["n"]), setor, d.get("fornecedor_id"),
         d.get("ot_id"), d.get("equipamento_id"),
         d.get("prioridade", "normal"), d.get("condicao_pagamento"),
         d.get("observacao"), uid))

    for i, it in enumerate(itens):
        desc = (it.get("descricao") or "").strip()
        if not desc:
            continue
        await ajard_query(
            """INSERT INTO compras.oc_itens
                  (oc_id, peca_id, descricao, quantidade, unidade, valor_unit, ordem)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (row["id"], it.get("peca_id"), desc,
             it.get("quantidade") or 1, it.get("unidade", "UN"),
             it.get("valor_unit") or 0, i), fetch="none")

    total = await _recalcular_total(row["id"])
    await _trilha(row["id"], None, "rascunho", "OC criada (cotação)", uid)
    out = dict(row)
    out["valor_total"] = total
    return out


@router.get("/compras/api/ocs")
async def listar_ocs(status: str = None, setor: str = None,
                     fornecedor_id: str = None, mes: str = None,
                     _auth=Depends(verificar_compras)):
    where, params = ["oc.ativo=true"], []
    if status:
        params.append(status)
        where.append("oc.status=%s")
    if setor:
        params.append(setor.upper())
        where.append("oc.setor_codigo=%s")
    if fornecedor_id:
        params.append(fornecedor_id)
        where.append("oc.fornecedor_id=%s")
    if mes:  # 'YYYY-MM'
        params.append(mes)
        where.append("to_char(oc.criado_em,'YYYY-MM')=%s")
    rows = await ajard_query(
        f"""SELECT oc.*, se.nome AS setor_nome, se.cor AS setor_cor,
                  fo.nome AS fornecedor_nome,
                  us.nome AS solicitante_nome, ua.nome AS aprovador_nome,
                  ot.numero AS ot_numero,
                  eq.codigo AS equipamento_codigo
           FROM compras.ordens_compra oc
           JOIN compras.setores se ON se.codigo = oc.setor_codigo
           LEFT JOIN public.fornecedores fo ON fo.id = oc.fornecedor_id
           LEFT JOIN public.usuarios_garra us ON us.id = oc.solicitante_id
           LEFT JOIN public.usuarios_garra ua ON ua.id = oc.aprovador_id
           LEFT JOIN manutencao.ot ot ON ot.id = oc.ot_id
           LEFT JOIN operacional.equipamentos eq ON eq.id = oc.equipamento_id
           WHERE {' AND '.join(where)}
           ORDER BY oc.criado_em DESC""", params)
    return [dict(r) for r in rows]


@router.get("/compras/api/resumo")
async def resumo_compras(_auth=Depends(verificar_compras)):
    r = await ajard_query("""
        SELECT
          COUNT(*) FILTER (WHERE status='solicitada')                    AS aguardando_aprovacao,
          COUNT(*) FILTER (WHERE status='aprovada')                      AS aprovadas,
          COUNT(*) FILTER (WHERE status IN ('enviada','recebida_parcial')) AS aguardando_entrega,
          COUNT(*) FILTER (WHERE status='recebida'
                           AND date_trunc('month',criado_em)=date_trunc('month',now())) AS recebidas_mes,
          COALESCE(SUM(valor_total) FILTER (WHERE status IN ('aprovada','enviada','recebida_parcial','recebida')
                           AND date_trunc('month',criado_em)=date_trunc('month',now())),0) AS valor_mes
        FROM compras.ordens_compra WHERE ativo=true""", fetch="one")
    return dict(r)


@router.get("/compras/api/pendentes-aprovacao")
async def fila_aprovacao(payload=Depends(verificar_compras_aprovador)):
    """Fila do aprovador: OCs solicitadas dentro da alçada dele."""
    uid = await _usuario_id(payload)
    tem, limite = await _alcada_do_usuario(uid)
    if not tem:
        return []
    where, params = ["oc.ativo=true", "oc.status='solicitada'"], []
    if limite is not None:
        params.append(limite)
        where.append("oc.valor_total<=%s")
    rows = await ajard_query(
        f"""SELECT oc.*, se.nome AS setor_nome, se.cor AS setor_cor,
                  fo.nome AS fornecedor_nome, us.nome AS solicitante_nome,
                  ot.numero AS ot_numero, eq.codigo AS equipamento_codigo
           FROM compras.ordens_compra oc
           JOIN compras.setores se ON se.codigo = oc.setor_codigo
           LEFT JOIN public.fornecedores fo ON fo.id = oc.fornecedor_id
           LEFT JOIN public.usuarios_garra us ON us.id = oc.solicitante_id
           LEFT JOIN manutencao.ot ot ON ot.id = oc.ot_id
           LEFT JOIN operacional.equipamentos eq ON eq.id = oc.equipamento_id
           WHERE {' AND '.join(where)}
           ORDER BY CASE oc.prioridade WHEN 'urgente' THEN 0 ELSE 1 END, oc.criado_em""",
        params)
    return [dict(r) for r in rows]


@router.get("/compras/api/ocs/{oc_id}")
async def detalhe_oc(oc_id: str, _auth=Depends(verificar_compras)):
    oc = await ajard_query(
        """SELECT oc.*, se.nome AS setor_nome, se.cor AS setor_cor,
                  fo.nome AS fornecedor_nome, fo.telefone AS fornecedor_telefone,
                  us.nome AS solicitante_nome, ua.nome AS aprovador_nome,
                  ot.numero AS ot_numero, eq.codigo AS equipamento_codigo
           FROM compras.ordens_compra oc
           JOIN compras.setores se ON se.codigo = oc.setor_codigo
           LEFT JOIN public.fornecedores fo ON fo.id = oc.fornecedor_id
           LEFT JOIN public.usuarios_garra us ON us.id = oc.solicitante_id
           LEFT JOIN public.usuarios_garra ua ON ua.id = oc.aprovador_id
           LEFT JOIN manutencao.ot ot ON ot.id = oc.ot_id
           LEFT JOIN operacional.equipamentos eq ON eq.id = oc.equipamento_id
           WHERE oc.id=%s AND oc.ativo=true""", (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    itens = await ajard_query(
        """SELECT * FROM compras.oc_itens
           WHERE oc_id=%s AND ativo=true ORDER BY ordem""", (oc_id,))
    hist = await ajard_query(
        """SELECT h.*, u.nome AS usuario_nome
           FROM compras.oc_historico h
           LEFT JOIN public.usuarios_garra u ON u.id = h.usuario_id
           WHERE h.oc_id=%s ORDER BY h.criado_em""", (oc_id,))
    d = dict(oc)
    d["itens"] = [dict(i) for i in itens]
    d["historico"] = [dict(h) for h in hist]
    return d


@router.patch("/compras/api/ocs/{oc_id}")
async def editar_oc(oc_id: str, request: Request, payload=Depends(verificar_compras)):
    """Edição só em rascunho ou solicitada. Se vier 'itens', substitui a lista."""
    d = await request.json()
    oc = await ajard_query(
        "SELECT id, status FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if oc["status"] not in ("rascunho", "solicitada"):
        raise HTTPException(status_code=400,
                            detail=f"OC em '{oc['status']}' não pode mais ser editada")

    campos = ["setor_codigo", "fornecedor_id", "ot_id", "equipamento_id",
              "prioridade", "condicao_pagamento", "observacao"]
    updates, params = [], []
    for c in campos:
        if c in d:
            updates.append(f"{c}=%s")
            params.append(d[c])
    if updates:
        params.append(oc_id)
        await ajard_query(
            f"UPDATE compras.ordens_compra SET {', '.join(updates)}, atualizado_em=now() WHERE id=%s",
            params, fetch="none")

    if "itens" in d:
        await ajard_query(
            "UPDATE compras.oc_itens SET ativo=false WHERE oc_id=%s",
            (oc_id,), fetch="none")
        for i, it in enumerate(d["itens"] or []):
            desc = (it.get("descricao") or "").strip()
            if not desc:
                continue
            await ajard_query(
                """INSERT INTO compras.oc_itens
                      (oc_id, peca_id, descricao, quantidade, unidade, valor_unit, ordem)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (oc_id, it.get("peca_id"), desc,
                 it.get("quantidade") or 1, it.get("unidade", "UN"),
                 it.get("valor_unit") or 0, i), fetch="none")
    await _recalcular_total(oc_id)
    return {"ok": True}


@router.post("/compras/api/ocs/{oc_id}/solicitar")
async def solicitar_aprovacao(oc_id: str, payload=Depends(verificar_compras)):
    """Rascunho → solicitada. Se o valor está dentro da alçada de quem
    solicita → aprova direto (1 passo), registrando as duas etapas na trilha."""
    oc = await ajard_query(
        "SELECT id, status, valor_total FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if "solicitada" not in _TRANSICOES.get(oc["status"], set()):
        raise HTTPException(status_code=400,
                            detail=f"Transição inválida: {oc['status']} → solicitada")
    uid = await _usuario_id(payload)
    await ajard_query(
        "UPDATE compras.ordens_compra SET status='solicitada', atualizado_em=now() WHERE id=%s",
        (oc_id,), fetch="none")
    await _trilha(oc_id, oc["status"], "solicitada", "Aprovação solicitada", uid)

    tem, limite = await _alcada_do_usuario(uid)
    if _valor_dentro_alcada(oc["valor_total"], tem, limite):
        await ajard_query(
            """UPDATE compras.ordens_compra
               SET status='aprovada', aprovador_id=%s, data_aprovacao=now(),
                   atualizado_em=now() WHERE id=%s""",
            (uid, oc_id), fetch="none")
        await _trilha(oc_id, "solicitada", "aprovada",
                      "Aprovada em 1 passo (dentro da alçada)", uid)
        return {"ok": True, "status": "aprovada", "auto_aprovada": True}
    return {"ok": True, "status": "solicitada", "auto_aprovada": False}


@router.post("/compras/api/ocs/{oc_id}/aprovar")
async def aprovar_oc(oc_id: str, request: Request,
                     payload=Depends(verificar_compras_aprovador)):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    oc = await ajard_query(
        "SELECT id, status, valor_total FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if "aprovada" not in _TRANSICOES.get(oc["status"], set()):
        raise HTTPException(status_code=400,
                            detail=f"Transição inválida: {oc['status']} → aprovada")
    uid = await _usuario_id(payload)
    tem, limite = await _alcada_do_usuario(uid)
    if not _valor_dentro_alcada(oc["valor_total"], tem, limite):
        raise HTTPException(
            status_code=403,
            detail="Valor acima da sua alçada de aprovação")
    await ajard_query(
        """UPDATE compras.ordens_compra
           SET status='aprovada', aprovador_id=%s, data_aprovacao=now(),
               atualizado_em=now() WHERE id=%s""",
        (uid, oc_id), fetch="none")
    await _trilha(oc_id, oc["status"], "aprovada",
                  (d.get("observacao") or "").strip() or "OC aprovada", uid)
    return {"ok": True, "status": "aprovada"}


@router.post("/compras/api/ocs/{oc_id}/rejeitar")
async def rejeitar_oc(oc_id: str, request: Request,
                      payload=Depends(verificar_compras_aprovador)):
    d = await request.json()
    motivo = (d.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="Motivo da rejeição é obrigatório")
    oc = await ajard_query(
        "SELECT id, status FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if "rejeitada" not in _TRANSICOES.get(oc["status"], set()):
        raise HTTPException(status_code=400,
                            detail=f"Transição inválida: {oc['status']} → rejeitada")
    uid = await _usuario_id(payload)
    await ajard_query(
        """UPDATE compras.ordens_compra
           SET status='rejeitada', motivo_rejeicao=%s, atualizado_em=now() WHERE id=%s""",
        (motivo, oc_id), fetch="none")
    await _trilha(oc_id, oc["status"], "rejeitada", motivo, uid)
    return {"ok": True, "status": "rejeitada"}


@router.post("/compras/api/ocs/{oc_id}/enviar")
async def marcar_enviada(oc_id: str, payload=Depends(verificar_compras)):
    """Marca a OC como enviada ao fornecedor (após gerar PDF / link WhatsApp)."""
    oc = await ajard_query(
        "SELECT id, status FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if "enviada" not in _TRANSICOES.get(oc["status"], set()):
        raise HTTPException(status_code=400,
                            detail=f"Transição inválida: {oc['status']} → enviada")
    uid = await _usuario_id(payload)
    await ajard_query(
        """UPDATE compras.ordens_compra
           SET status='enviada', enviado_por=%s, enviado_em=now(),
               atualizado_em=now() WHERE id=%s""",
        (uid, oc_id), fetch="none")
    await _trilha(oc_id, oc["status"], "enviada", "Enviada ao fornecedor", uid)
    return {"ok": True, "status": "enviada"}


@router.post("/compras/api/ocs/{oc_id}/receber")
async def receber_oc(oc_id: str, request: Request, payload=Depends(verificar_compras)):
    """Recebimento item a item. Body:
    { nf_numero?, itens: [{item_id, qtd_recebida}] } — qtd é o RECEBIDO AGORA
    (acumula). Todas completas → 'recebida'; senão → 'recebida_parcial'.
    OC com ot_id: o valor recebido agora soma no custo_total da OT."""
    d = await request.json()
    oc = await ajard_query(
        "SELECT id, status, ot_id FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if oc["status"] not in ("enviada", "recebida_parcial"):
        raise HTTPException(status_code=400,
                            detail=f"OC em '{oc['status']}' não está aguardando entrega")

    uid = await _usuario_id(payload)
    valor_recebido_agora = 0.0
    for it in d.get("itens") or []:
        item = await ajard_query(
            """SELECT id, quantidade, qtd_recebida, valor_unit
               FROM compras.oc_itens WHERE id=%s AND oc_id=%s AND ativo=true""",
            (it.get("item_id"), oc_id), fetch="one")
        if not item:
            continue
        qtd_agora = float(it.get("qtd_recebida") or 0)
        if qtd_agora <= 0:
            continue
        await ajard_query(
            """UPDATE compras.oc_itens
               SET qtd_recebida = COALESCE(qtd_recebida,0)+%s WHERE id=%s""",
            (qtd_agora, item["id"]), fetch="none")
        valor_recebido_agora += qtd_agora * float(item.get("valor_unit") or 0)

    if d.get("nf_numero"):
        await ajard_query(
            "UPDATE compras.ordens_compra SET nf_numero=%s WHERE id=%s",
            ((d.get("nf_numero") or "").strip(), oc_id), fetch="none")

    pend = await ajard_query(
        """SELECT COUNT(*) AS n FROM compras.oc_itens
           WHERE oc_id=%s AND ativo=true
             AND COALESCE(qtd_recebida,0) < quantidade""",
        (oc_id,), fetch="one")
    novo = "recebida" if int(pend["n"]) == 0 else "recebida_parcial"
    await ajard_query(
        "UPDATE compras.ordens_compra SET status=%s, atualizado_em=now() WHERE id=%s",
        (novo, oc_id), fetch="none")
    await _trilha(oc_id, oc["status"], novo,
                  f"Recebimento registrado (R$ {valor_recebido_agora:.2f})", uid)

    if oc.get("ot_id") and valor_recebido_agora > 0:
        await ajard_query(
            """UPDATE manutencao.ot
               SET custo_total = COALESCE(custo_total,0)+%s, atualizado_em=now()
               WHERE id=%s""",
            (valor_recebido_agora, oc["ot_id"]), fetch="none")

    return {"ok": True, "status": novo, "valor_recebido": valor_recebido_agora}


@router.post("/compras/api/ocs/{oc_id}/cancelar")
async def cancelar_oc(oc_id: str, request: Request,
                      payload=Depends(verificar_compras_aprovador)):
    d = {}
    try:
        d = await request.json()
    except Exception:
        pass
    oc = await ajard_query(
        "SELECT id, status FROM compras.ordens_compra WHERE id=%s AND ativo=true",
        (oc_id,), fetch="one")
    if not oc:
        raise HTTPException(status_code=404, detail="OC não encontrada")
    if "cancelada" not in _TRANSICOES.get(oc["status"], set()):
        raise HTTPException(status_code=400,
                            detail=f"Transição inválida: {oc['status']} → cancelada")
    uid = await _usuario_id(payload)
    await ajard_query(
        "UPDATE compras.ordens_compra SET status='cancelada', atualizado_em=now() WHERE id=%s",
        (oc_id,), fetch="none")
    await _trilha(oc_id, oc["status"], "cancelada",
                  (d.get("motivo") or "").strip() or "OC cancelada", uid)
    return {"ok": True, "status": "cancelada"}


# ── INTELIGÊNCIA: último preço pago ───────────────────────────

@router.get("/compras/api/ultimo-preco")
async def ultimo_preco(q: str = "", _auth=Depends(verificar_compras)):
    """Histórico de preço por descrição (OCs recebidas). Alimenta a dica
    'Último preço: R$ X · FORNECEDOR · data' ao digitar o item."""
    q = (q or "").strip()
    if len(q) < 3:
        return []
    rows = await ajard_query(
        """SELECT i.descricao, i.valor_unit, i.unidade,
                  fo.nome AS fornecedor_nome, oc.criado_em::date AS data_compra
           FROM compras.oc_itens i
           JOIN compras.ordens_compra oc ON oc.id = i.oc_id
           LEFT JOIN public.fornecedores fo ON fo.id = oc.fornecedor_id
           WHERE i.ativo=true AND oc.ativo=true
             AND oc.status IN ('recebida','recebida_parcial')
             AND i.descricao ILIKE %s
           ORDER BY oc.criado_em DESC LIMIT 5""",
        (f"%{q}%",))
    return [dict(r) for r in rows]


# ── PÁGINA DO MÓDULO (desktop + mobile, SSO — Regra 60) ───────

@router.get("/compras", response_class=HTMLResponse)
async def pagina_compras():
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "compras"))
    candidatos = [
        os.path.join(raiz, "static", "compras.html"),
        os.path.join(raiz, "compras.html"),
    ]
    for p in candidatos:
        if os.path.isfile(p):
            return open(p, encoding="utf-8").read()
    raise HTTPException(status_code=404, detail="Página do módulo Compras não encontrada")
