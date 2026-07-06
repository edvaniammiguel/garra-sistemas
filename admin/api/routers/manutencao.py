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
    base = os.path.join(os.path.dirname(__file__), "..", "..", "..", "manutencao", "static")
    path = os.path.abspath(os.path.join(base, "manutencao.html"))
    return open(path, encoding="utf-8").read()
