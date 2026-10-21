# ══════════════════════════════════════════════════════════════
# MÓDULO MANUTENÇÃO — fundação v26 (06/07/2026)
# Referência: ManWinWin Express. Lifecycle da OT (Bruna):
#   aberta → em_andamento → aguardando_peca → concluida | cancelada
# Categoria 'apoio' (Combinado) é EXCLUÍDA de OT por decisão de projeto.
# ══════════════════════════════════════════════════════════════
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse
import os
import json as _json
import re
from datetime import datetime

from core.auth import verificar_token, verificar_gestor
from core.db import ajard_query, ajard_query_id
from core.storage import storage_upload, storage_url, storage_urls, storage_delete
from core.helpers import comprimir_imagem
from core.permissions import perfil_modulos_padrao

router = APIRouter()

# (21/10/2026) Sem lista de perfis "livres": a tela de Permissões é a única fonte (admin = Master).
from core.permissions import tem_modulo as _tem_modulo


async def verificar_manutencao(payload=Depends(verificar_token)):
    """Gate do módulo Manutenção Desktop: módulo 'manutencao' (perfil padrão + exceções)."""
    if await _tem_modulo(payload, {"manutencao"}):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para o módulo Manutenção")

async def verificar_pedir_ot(payload=Depends(verificar_token)):
    """Gate do CANAL EXTERNO (app Pedir OT): pedir_ot, ot_executar ou manutencao."""
    if await _tem_modulo(payload, {"pedir_ot", "ot_executar", "manutencao"}):
        return payload
    raise HTTPException(status_code=403,
                        detail="Sem permissão para pedir OT — marque o módulo na matriz de Permissões (perfil Mecânica já vem com ele)")

_TRANSICOES = {
    # (23/09/2026) rascunho = OT em criação (número reservado, invisível) até OK/Emitir
    "rascunho":        {"programada", "aberta", "em_andamento", "cancelada"},
    "programada":      {"aberta", "em_andamento", "cancelada"},
    "aberta":          {"em_andamento", "cancelada"},
    "em_andamento":    {"aguardando_peca", "concluida", "cancelada"},
    "aguardando_peca": {"em_andamento", "concluida", "cancelada"},
    # (15/10/2026) Reabrir: gestão desfaz um encerramento/cancelamento por engano
    # (botão Cancelar… clicado achando que cancelava a edição). Exige motivo.
    "concluida":       {"em_andamento"},
    "cancelada":       {"em_andamento", "aberta"},
}


_PEDIDOS_OK = False
async def _garantir_pedidos():
    """(24/08/2026) Pacote Pedidos — a demanda que origina a OT (aba Pedidos
    do ManWinWin). Fontes: manual (triagem da Bruna) e, na próxima etapa,
    NC do Checklist (colunas origem/nc_ref já preparadas)."""
    global _PEDIDOS_OK
    if _PEDIDOS_OK:
        return
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.pedidos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ano INT NOT NULL,
            sequencia INT NOT NULL,
            numero TEXT NOT NULL UNIQUE,
            data_pedido TIMESTAMPTZ DEFAULT now(),
            via TEXT DEFAULT 'sistema',
            grau_urgencia TEXT DEFAULT 'normal',
            descricao TEXT NOT NULL,
            equipamento_id UUID,
            solicitante_id UUID,
            cliente_nome TEXT,
            contato TEXT,
            status TEXT DEFAULT 'aberto',
            ot_id UUID,
            motivo_recusa TEXT,
            origem TEXT DEFAULT 'manual',
            nc_ref TEXT,
            criado_por UUID,
            ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    _PEDIDOS_OK = True


_OT_COLS_OK = False
_LEITURAS_OK = False

async def _garantir_leituras():
    """(30/08→31/08/2026) Fonte ÚNICA de leituras do equipamento: partes
    diárias do Operacional + abastecimentos sadios (sem divergência, destino
    equipamento) + checklist (meta.km/horimetro casado pelo código da frota;
    depende de operacional.f_num_br criada no DDL do abastecimento). Consumida pelo FMD
    e pelo Registo Funcionamento — a leitura da bomba passa a mover a
    preventiva, como no ManWinWin. View idempotente; viaja no código."""
    global _LEITURAS_OK
    if _LEITURAS_OK:
        return
    from routers.abastecimentos import _ddl as _ddl_abast
    await _ddl_abast()
    await _garantir_colunas_ot()   # (19/10/2026) a view lê ot.horimetro_conclusao
    # (24/09/2026) Correção de leitura (Registro Funcionamento ▸ ✏️/✕): o documento de
    # origem (parte, checklist, abastecimento) fica intacto; a leitura é corrigida
    # ou excluída só para o FMD/funcionamento, com motivo e trilha no diário.
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.leituras_correcao (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL,
            fonte TEXT NOT NULL,
            ref TEXT NOT NULL,
            data TIMESTAMPTZ,
            leitura_original NUMERIC,
            leitura_nova NUMERIC,
            anulada BOOLEAN NOT NULL DEFAULT false,
            motivo TEXT NOT NULL,
            usuario_id UUID,
            criado_em TIMESTAMPTZ DEFAULT now(),
            UNIQUE (fonte, ref))""", fetch="none")
    _parte = """
        SELECT equipamento_id, data,
               GREATEST(COALESCE(horimetro_final,0), COALESCE(km_final,0)) AS leitura,
               'parte'::text AS fonte, id::text AS ref
          FROM operacional.partes_diarias
         WHERE COALESCE(ativo, true) = true
        UNION ALL
        SELECT equipamento_id, data, leitura, 'abastecimento'::text AS fonte, id::text AS ref
          FROM operacional.abastecimentos
         WHERE ativo = true AND leitura IS NOT NULL
           AND COALESCE(divergencia_leitura, false) = false
           AND COALESCE(destino_tipo,'equipamento') = 'equipamento'
        UNION ALL
        SELECT equipamento_id, data_conclusao AS data, horimetro_conclusao AS leitura,
               'ot'::text AS fonte, id::text AS ref
          FROM manutencao.ot
         WHERE ativo = true AND status = 'concluida' AND horimetro_conclusao IS NOT NULL"""
    _checklist = """
        UNION ALL
        SELECT e.id AS equipamento_id, c.enviado_em AS data,
               operacional.f_num_br(COALESCE(m.j->>'km', m.j->>'horimetro')) AS leitura,
               'checklist'::text AS fonte, c.envio_id::text AS ref
          FROM checklist.envios c
          CROSS JOIN LATERAL (SELECT c.meta::jsonb AS j) m
          JOIN operacional.equipamentos e
            ON upper(trim(e.codigo)) = upper(trim(COALESCE(m.j->>'veiculo', m.j->>'identificacao', m.j->>'equipamento')))
         WHERE operacional.f_num_br(COALESCE(m.j->>'km', m.j->>'horimetro')) > 0"""
    _view = lambda fontes: f"""
        CREATE OR REPLACE VIEW operacional.v_leituras AS
        SELECT b.equipamento_id, b.data, COALESCE(c.leitura_nova, b.leitura) AS leitura, b.fonte,
               b.ref, b.leitura AS leitura_original, (c.id IS NOT NULL) AS corrigida
          FROM ({fontes}) b
          LEFT JOIN manutencao.leituras_correcao c ON c.fonte = b.fonte AND c.ref = b.ref
         WHERE COALESCE(c.anulada, false) = false"""
    _sql_completa = _view(_parte + _checklist)
    _sql_base = _view(_parte)
    try:
        await ajard_query(_sql_completa, fetch="none")
    except Exception:
        # checklist.envios ausente/ilegível neste ambiente: a fonte única não pode
        # cair por causa de uma das fontes — nasce com partes + abastecimentos.
        await ajard_query(_sql_base, fetch="none")
    _LEITURAS_OK = True


async def _garantir_colunas_ot():
    """(24/08/2026) Pacote 2 — fluxo da OT com semáforo real. Colunas da OT
    programada garantidas por ALTER idempotente que viaja no código (Ciclo
    Garra): previsão por data OU por horímetro, plano de origem e selo de
    origem (preparo do pacote de migração ManWinWin)."""
    global _OT_COLS_OK
    if _OT_COLS_OK:
        return
    for ddl in (
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS data_prevista DATE",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS horimetro_previsto NUMERIC",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS plano_id UUID",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS origem TEXT DEFAULT 'garra'",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS sintoma_codigo TEXT",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS causa_codigo TEXT",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS projecto_id UUID",
        # (10/10/2026) Origem no padrão ManWinWin: Interventor (interno = setor · externo = fornecedor),
        # Contrato, Prev. TDM (h) e bloqueio temporário
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS interventor TEXT DEFAULT 'interno'",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS setor_interventor TEXT",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS contrato TEXT",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS tdm_previsto NUMERIC",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS bloqueada BOOLEAN DEFAULT false",
        # (10/10/2026) "Data Programada Alterada" do ManWinWin: a programação saiu do cálculo da FMP
        # por decisão humana (Avançado). Recalcular pelo plano (Aplicar FMP) não sobrepõe; Novo ciclo limpa.
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS prog_alterada BOOLEAN DEFAULT false",
        # (19/10/2026) Encerramento informa a leitura (horímetro/km) da máquina no fim do serviço:
        # vira baseline da FMP e fonte de leitura do Registo Funcionamento ('ot')
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS horimetro_conclusao NUMERIC",
        # (19/10/2026) Aba Tarefas no padrão ManWinWin: dois textos livres — Planejado (da FMP) e Realizado (do app + gestão)
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS tarefas_planejado TEXT",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS tarefas_realizado TEXT",
        # (19/10/2026) Gestão no padrão ManWinWin: Início real editável (+ leitura), Data Limite e carimbo do Encerramento
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS data_inicio_real TIMESTAMPTZ",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS horimetro_inicio NUMERIC",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS data_limite DATE",
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS encerrado_em TIMESTAMPTZ",
        # (19/10/2026) horas de serviço do interventor EXTERNO (sem custo próprio) — entram no TDR, ficam na Gestão
        "ALTER TABLE manutencao.ot ADD COLUMN IF NOT EXISTS horas_externo NUMERIC",
    ):
        await ajard_query(ddl, fetch="none")
    # OT que já tem fornecedor é externa (migração idempotente)
    await ajard_query("UPDATE manutencao.ot SET interventor='externo' WHERE fornecedor_id IS NOT NULL AND COALESCE(interventor,'interno')='interno' AND setor_interventor IS NULL", fetch="none")
    # (21/10/2026) Datas previstas com ano digitado pela metade (0202-10-15 → "vencida há 666209 d"), gravadas
    # antes do _data_ok: limpa e deixa o Atualizar datas OT recalcular pela FMP (sem FMP fica "sem data" = 🔴)
    await ajard_query("""UPDATE manutencao.ot SET data_prevista=NULL, prog_alterada=false
                         WHERE data_prevista IS NOT NULL AND EXTRACT(YEAR FROM data_prevista) < 2000""", fetch="none")
    await ajard_query("""UPDATE manutencao.ot SET data_limite=NULL
                         WHERE data_limite IS NOT NULL AND EXTRACT(YEAR FROM data_limite) < 2000""", fetch="none")
    # (10/10/2026) Saneia tipo_trabalho gravado por extenso pela Origem ('preventiva'…): volta a sigla da FMP, ou limpa
    await ajard_query("""UPDATE manutencao.ot o SET tipo_trabalho = (SELECT p.tipo_trabalho FROM manutencao.planos p WHERE p.id = o.plano_id)
                         WHERE lower(COALESCE(o.tipo_trabalho,'')) IN ('corretiva','preventiva','reforma','melhoria')""", fetch="none")
    _OT_COLS_OK = True


def _data_ok(v, campo="Data prevista"):
    """(10/10/2026) O <input type=date> do Chrome aceita ano parcial ('0202' enquanto se
    digita 2026) e a OT ficava gravada com ano 0202 → 'vencida há 666198 d'.
    Devolve 'AAAA-MM-DD' ou None; fora de 2000–2100 recusa com 400."""
    v = (str(v or "")).strip()
    if not v:
        return None
    try:
        ano = int(v[:4])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{campo} inválida: {v}")
    if not 2000 <= ano <= 2100:
        raise HTTPException(status_code=400, detail=f"{campo} com ano inválido ({ano}). Confira o ano e grave de novo.")
    return v


def _vencimento_ot(data_prevista, horimetro_previsto, horimetro_vivo, hoje, unidade="h"):
    """Semáforo REAL da OT programada — calculado de data/horímetro contra
    hoje (decisão 24/08: semáforo é estado, nunca enfeite).
    Régua dupla: vence pelo que chegar primeiro (data OU horímetro).
    Níveis: 0=vencida 🔴 · 1=a vencer (≤7 dias / ≤50 h·km) 🟡 · 2=em dia 🟢."""
    por_data = (data_prevista - hoje).days if data_prevista else None
    por_hor = (float(horimetro_previsto) - float(horimetro_vivo or 0)) \
        if horimetro_previsto is not None else None
    if por_data is None and por_hor is None:
        return None
    partes = []
    if (por_data is not None and por_data <= 0) or (por_hor is not None and por_hor <= 0):
        # Regra 28/08: vence HOJE = age hoje = vencida (dia D é vermelho)
        if por_data is not None and por_data < 0:
            partes.append(f"vencida há {-por_data} d")
        elif por_data is not None and por_data == 0:
            partes.append("vence HOJE")
        if por_hor is not None and por_hor <= 0:
            partes.append(f"passou {abs(round(por_hor))} {unidade}")
        return {"nivel": 0, "texto": " · ".join(partes) or "vencida"}
    if por_data is not None:
        partes.append(f"vence em {por_data} d")
    if por_hor is not None:
        partes.append(f"faltam {round(por_hor)} {unidade}")
    nivel = 1 if ((por_data is not None and por_data <= 7)
                  or (por_hor is not None and por_hor <= 50)) else 2
    return {"nivel": nivel, "texto": " · ".join(partes)}


async def _usuario_id(payload):
    u = await ajard_query(
        "SELECT id FROM public.usuarios_garra WHERE login=%s",
        (payload.get("sub", ""),), fetch="one")
    return u["id"] if u else None


# ── FORNECEDORES (cadastro único, public) ─────────────────────

@router.get("/manutencao/api/fornecedores")
async def listar_fornecedores(_auth=Depends(verificar_manutencao)):
    rows = await ajard_query(
        "SELECT * FROM public.fornecedores WHERE ativo=true ORDER BY nome")
    return [dict(r) for r in rows]


@router.post("/manutencao/api/fornecedores")
async def criar_fornecedor(request: Request, payload=Depends(verificar_manutencao)):
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
async def editar_fornecedor(fid: str, request: Request, payload=Depends(verificar_manutencao)):
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

async def _inserir_pedido(d: dict, descricao: str, uid):
    """(28/08/2026) Núcleo único de criação de Pedido — usado pela janela
    do desktop, pelo canal MOBILE do mecânico e pela NC do Checklist.
    Numeração PED-AAAA-NNNN à prova de duplicação de lógica."""
    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.pedidos
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    from datetime import date as _date
    ano = _date.today().year
    numero = f"PED-{ano}-{int(seq['n']):04d}"
    row = await ajard_query_id(
        """INSERT INTO manutencao.pedidos
              (ano, sequencia, numero, via, grau_urgencia, descricao,
               equipamento_id, solicitante_id, cliente_nome, contato, origem, nc_ref, criado_por)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ano, int(seq["n"]), numero,
         (d.get("via") or "sistema"), (d.get("grau_urgencia") or "normal"), descricao,
         d.get("equipamento_id"), d.get("solicitante_id"),
         (d.get("cliente_nome") or "").strip() or None, (d.get("contato") or "").strip() or None,
         (d.get("origem") or "manual"), d.get("nc_ref"), uid))
    return row, numero


@router.post("/manutencao/api/pedidos")
async def criar_pedido(request: Request, payload=Depends(verificar_manutencao)):
    """Pedido manual (janela Pedido do ManWinWin): nº automático, via,
    urgência, solicitante interno OU cliente externo. Avisa duplicados
    (pedidos abertos do mesmo equipamento) sem bloquear — triagem decide."""
    await _garantir_pedidos()
    d = await request.json()
    descricao = (d.get("descricao") or "").strip()
    if not descricao:
        raise HTTPException(status_code=400, detail="Descreva o pedido")
    uid = await _usuario_id(payload)
    row, numero = await _inserir_pedido(d, descricao, uid)
    dup = 0
    if d.get("equipamento_id"):
        r = await ajard_query(
            """SELECT COUNT(*)::int AS n FROM manutencao.pedidos
               WHERE equipamento_id=%s AND status='aberto' AND ativo=true AND id<>%s""",
            (d["equipamento_id"], row["id"]), fetch="one")
        dup = int(r["n"] or 0)
    out = dict(row)
    out["duplicados_abertos"] = dup
    return out


@router.get("/manutencao/api/pedidos")
async def listar_pedidos(status: str = "aberto", equipamento_id: str = None,
                         _auth=Depends(verificar_manutencao)):
    await _garantir_pedidos()
    where, params = ["p.ativo=true"], []
    if status:
        where.append("p.status=%s"); params.append(status)
    if equipamento_id:
        where.append("p.equipamento_id=%s"); params.append(equipamento_id)
    rows = await ajard_query(
        f"""SELECT p.*, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                   us.nome AS solicitante_nome, ot.numero AS ot_numero
            FROM manutencao.pedidos p
            LEFT JOIN operacional.equipamentos eq ON eq.id = p.equipamento_id
            LEFT JOIN public.usuarios_garra us ON us.id = p.solicitante_id
            LEFT JOIN manutencao.ot ot ON ot.id = p.ot_id
            WHERE {' AND '.join(where)}
            ORDER BY p.data_pedido DESC""", params)
    return [dict(r) for r in rows]


@router.get("/manutencao/api/pedidos/{pid}/fotos-nc")
async def fotos_nc_pedido(pid: str, _auth=Depends(verificar_manutencao)):
    """(10/09/2026) Ponte Checklist → Pedido. O pedido automático guarda
    nc_ref = envio_id do checklist que o gerou; as fotos moram no Storage em
    checklist/{envio_id}/{item_id}.jpg. Aqui o pedido 'olha de volta' para o
    envio e devolve só os itens NÃO CONFORMES, com nome do item, observação
    do operador e URL assinada — para a triagem ver a evidência sem sair da tela."""
    from core.storage import _checklist_assinar_fotos_para_leitura
    import json as _json
    ped = await ajard_query(
        "SELECT nc_ref, via FROM manutencao.pedidos WHERE id=%s AND ativo=true",
        (pid,), fetch="one")
    if not ped:
        raise HTTPException(404, "Pedido não encontrado")
    nc_ref = ped.get("nc_ref")
    if not nc_ref:
        return {"origem_checklist": False, "itens": []}
    env = await ajard_query(
        "SELECT cl_id, cl_label, usuario_nome, enviado_em, respostas, meta "
        "FROM checklist.envios WHERE envio_id=%s", (nc_ref,), fetch="one")
    if not env:
        return {"origem_checklist": True, "envio_id": nc_ref, "itens": [],
                "aviso": "Envio do checklist não localizado"}
    resp = env.get("respostas")
    resp = resp if isinstance(resp, dict) else _json.loads(resp or "{}")
    resp = _checklist_assinar_fotos_para_leitura(resp)
    rotulos = {}
    try:
        steps = await ajard_query(
            "SELECT steps FROM checklist.modelos WHERE cl_id=%s", (env.get("cl_id"),), fetch="one")
        st_list = (steps or {}).get("steps")
        st_list = st_list if isinstance(st_list, list) else _json.loads(st_list or "[]")
        for st in st_list:
            for it in (st.get("items") or []):
                if it.get("id"):
                    rotulos[str(it["id"])] = str(it.get("label") or it["id"])
    except Exception:
        pass
    itens = []
    for chave, ans in resp.items():
        if not isinstance(ans, dict):
            continue
        if str(ans.get("val") or "").strip().upper() != "NC":
            continue
        itens.append({
            "item_id": str(chave),
            "item": rotulos.get(str(chave), str(chave)),
            "obs": (ans.get("obs") or "").strip(),
            "foto_url": ans.get("photo") or None,
        })
    meta = env.get("meta")
    meta = meta if isinstance(meta, dict) else _json.loads(meta or "{}")
    return {
        "origem_checklist": True,
        "envio_id": nc_ref,
        "checklist": env.get("cl_label") or env.get("cl_id"),
        "operador": env.get("usuario_nome"),
        "enviado_em": env.get("enviado_em"),
        "equipamento": meta.get("veiculo") or meta.get("equipamento") or None,
        "itens": itens,
    }


@router.post("/manutencao/api/pedidos/{pid}/recusar")
async def recusar_pedido(pid: str, request: Request, payload=Depends(verificar_manutencao)):
    """Pedido nunca some — recusado com motivo vira história."""
    await _garantir_pedidos()
    d = await request.json()
    motivo = (d.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="Motivo da recusa é obrigatório")
    r = await ajard_query(
        "UPDATE manutencao.pedidos SET status='recusado', motivo_recusa=%s WHERE id=%s AND status='aberto' RETURNING numero",
        (motivo, pid), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Pedido não encontrado ou já tratado")
    return {"ok": True, "numero": r["numero"]}


@router.post("/manutencao/api/pedidos/{pid}/converter")
async def converter_pedido(pid: str, request: Request, payload=Depends(verificar_manutencao)):
    """Triagem: pedido aberto → OT pré-preenchida, com elo dos dois lados
    (ot.pedido_id ↔ pedido.ot_id) e selo na trilha."""
    await _garantir_pedidos()
    await _garantir_colunas_ot()
    d = await request.json()
    ped = await ajard_query(
        "SELECT * FROM manutencao.pedidos WHERE id=%s AND ativo=true", (pid,), fetch="one")
    if not ped:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    if ped["status"] != "aberto":
        raise HTTPException(status_code=400, detail=f"Pedido já {ped['status']}")
    eq_id = d.get("equipamento_id") or ped["equipamento_id"]
    if not eq_id:
        raise HTTPException(status_code=400, detail="Defina o equipamento para converter")
    eq = await ajard_query(
        "SELECT id, categoria, horimetro_atual FROM operacional.equipamentos WHERE id=%s AND ativo=true",
        (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    if (eq.get("categoria") or "") == "apoio":
        raise HTTPException(status_code=400, detail="Equipamento de apoio não recebe OT")
    seq = await _proximo_seq_ot()
    from datetime import date as _date
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"
    mapa_pri = {"normal": "media", "urgente": "urgente", "alta": "alta", "baixa": "baixa"}
    corpo = {
        "equipamento_id": str(eq_id),
        "descricao": d.get("descricao") or ped["descricao"],
        "prioridade": d.get("prioridade") or mapa_pri.get(ped["grau_urgencia"], "media"),
        "tipo_trabalho": d.get("tipo_trabalho") or "C1",   # (20/10/2026) pedido = avaria → C1 por padrão
        "fornecedor_id": d.get("fornecedor_id"),
        "responsavel_id": d.get("responsavel_id"),
        # (20/10/2026) MWW: ao virar OT já nasce com Prog. Início (= hoje, ou a data escolhida na triagem);
        # Prog. Fim deriva (Prog. Início + Prev. TDM). Início só no Emitir; Fim só no Encerrar.
        "data_prevista": d.get("data_prevista") or _date.today().isoformat(),
        "horimetro_previsto": d.get("horimetro_previsto"),
        "_nasce_aberta": True,
        "pedido_id": str(pid),
        "origem": {"mobile": "mobile-mecanico", "checklist": "checklist-nc"}.get(
            (ped.get("via") or "").lower(), "pedido-desktop"),
        "_obs_criacao": f"OT criada do pedido {ped['numero']}",
    }
    uid = await _usuario_id(payload)
    return await _inserir_ot(corpo, dict(eq), uid, numero, ano, seq)


@router.post("/manutencao/api/ots")
async def abrir_ot(request: Request, payload=Depends(verificar_manutencao)):
    """Abre OT. Bloqueia categoria 'apoio' e captura o horímetro atual."""
    await _garantir_colunas_ot()
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

    seq = await _proximo_seq_ot()
    from datetime import date as _date
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"

    uid = await _usuario_id(payload)
    return await _inserir_ot(d, eq, uid, numero, ano, seq)


async def _proximo_seq_ot():
    """(10/10/2026) Próximo número do ano, à prova de buraco: olha a coluna sequencia E o número gravado
    (OT-AAAA-NNNN) — uma OT antiga com ano/sequencia nulos deixava MAX(sequencia) atrás do número real e
    o INSERT estourava 'duplicate key ordens_trabalho_numero_key'."""
    r = await ajard_query(
        """SELECT GREATEST(
                    COALESCE((SELECT MAX(sequencia) FROM manutencao.ot WHERE ano = EXTRACT(YEAR FROM now())::int), 0),
                    COALESCE((SELECT MAX(NULLIF(regexp_replace(split_part(numero,'-',3), '\\D', '', 'g'), '')::int)
                                FROM manutencao.ot
                               WHERE numero LIKE 'OT-' || EXTRACT(YEAR FROM now())::int || '-%%'), 0)
                  ) + 1 AS n""", fetch="one")
    return {"n": int(r["n"])}


async def _inserir_ot(d, eq, uid, numero, ano, seq):
    """Miolo compartilhado: abertura manual e conversão de pedido."""
    eq_id = d.get("equipamento_id")
    descricao = (d.get("descricao") or "").strip()
    data_prev = _data_ok(d.get("data_prevista"))
    hor_prev = d.get("horimetro_previsto")
    try:
        hor_prev = float(str(hor_prev).replace(",", ".")) if hor_prev not in (None, "") else None
    except ValueError:
        hor_prev = None
    # (23/09/2026) MWW: OT criada pela janela nasce PROGRAMADA (número reservado, não emitida);
    # vira em curso no Emitir. Só a conversão de pedido nasce aberta.
    programada = bool(data_prev or hor_prev is not None) and not d.get("_nasce_aberta")
    status_ini = "rascunho" if d.get("rascunho") else ("programada" if programada else "aberta")
    tt = (d.get("tipo_trabalho") or "").strip().upper() or None
    _classe = {"A": "preventiva", "B": "preventiva", "C": "corretiva",
               "M": "melhoria", "R": "reforma"}
    tipo_padrao = _classe.get((tt or " ")[0], "preventiva" if programada else "corretiva")
    import asyncpg as _apg
    for _tent in range(5):
        try:
            row = await ajard_query_id(
                """INSERT INTO manutencao.ot
                      (numero, ano, sequencia, equipamento_id, tipo, prioridade,
                       descricao, solicitante_id, responsavel_id, fornecedor_id,
                       horimetro_na_abertura, status, data_prevista, horimetro_previsto, plano_id,
                       sintoma_codigo, causa_codigo, tipo_trabalho)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (numero, ano, int(seq["n"]), eq_id,
                 d.get("tipo", tipo_padrao), d.get("prioridade", "media"),
                 descricao, uid, d.get("responsavel_id"), d.get("fornecedor_id"),
                 eq.get("horimetro_atual"), status_ini, data_prev, hor_prev, d.get("plano_id"),
                 (d.get("sintoma_codigo") or None), (d.get("causa_codigo") or None), tt))
            break
        except _apg.exceptions.UniqueViolationError:
            # corrida ou buraco na numeração: recalcula e tenta de novo
            seq = await _proximo_seq_ot(); numero = f"OT-{ano}-{int(seq['n']):04d}"
            if _tent == 4: raise
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,NULL,%s,%s,%s)""",
        (row["id"], status_ini,
         d.get("_obs_criacao") or ("OT programada" if programada else "OT criada"), uid), fetch="none")
    if d.get("pedido_id"):
        await ajard_query(
            "UPDATE manutencao.pedidos SET status='convertido', ot_id=%s WHERE id=%s AND status='aberto'",
            (row["id"], d["pedido_id"]), fetch="none")
        await ajard_query(
            "UPDATE manutencao.ot SET pedido_id=%s WHERE id=%s",
            (d["pedido_id"], row["id"]), fetch="none")
    return dict(row)


@router.get("/manutencao/api/ots")
async def listar_ots(status: str = None, equipamento_id: str = None, fechadas_dias: int = 90,
                     _auth=Depends(verificar_manutencao)):
    """(20/10/2026) Lista de trabalho: OTs vivas sempre; encerradas/canceladas só as dos últimos
    `fechadas_dias` (padrão 90; 0 = todas). O histórico completo fica na ficha do equipamento e na busca."""
    await _garantir_colunas_ot(); await _garantir_apontamentos()
    where, params = ["ot.ativo=true"], []
    if status:
        params.append(status)
        where.append("ot.status=%s")
    else:
        where.append("ot.status <> 'rascunho'")
        if fechadas_dias and int(fechadas_dias) > 0 and not equipamento_id:
            params.append(int(fechadas_dias))
            where.append("(ot.status NOT IN ('concluida','cancelada') OR COALESCE(ot.encerrado_em, ot.data_conclusao, ot.atualizado_em) >= now() - make_interval(days => %s))")
    if equipamento_id:
        params.append(equipamento_id)
        where.append("ot.equipamento_id=%s")
    rows = await ajard_query(
        f"""SELECT ot.*, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                  eq.horimetro_atual AS equipamento_horimetro, eq.medicao AS equipamento_medicao,
                  us.nome AS solicitante_nome, ur.nome AS responsavel_nome,
                  fo.nome AS fornecedor_nome, pd.via AS pedido_via,
                  COALESCE(ot.data_inicio_real, (SELECT MIN(h.criado_em) FROM manutencao.ot_historico h
                    WHERE h.ot_id = ot.id AND h.status_para = 'em_andamento')) AS data_inicio_exec,
                  (SELECT COUNT(*) FROM manutencao.ot_apontamentos a WHERE a.ot_id=ot.id AND a.ativo=true)::int AS apontamentos,
                  (SELECT bool_or(a.concluido) FROM manutencao.ot_apontamentos a WHERE a.ot_id=ot.id AND a.ativo=true) AS sinal_concluido
           FROM manutencao.ot ot
           JOIN operacional.equipamentos eq ON eq.id = ot.equipamento_id
           LEFT JOIN public.usuarios_garra us ON us.id = ot.solicitante_id
           LEFT JOIN public.usuarios_garra ur ON ur.id = ot.responsavel_id
           LEFT JOIN public.fornecedores fo ON fo.id = ot.fornecedor_id
           LEFT JOIN manutencao.pedidos pd ON pd.id = ot.pedido_id
           WHERE {' AND '.join(where)}
           ORDER BY ot.data_abertura DESC""", params)
    from datetime import date as _date
    hoje = _date.today()
    saida = []
    for r in rows:
        d = dict(r)
        if d.get("status") == "programada":
            d["vencimento"] = _vencimento_ot(
                d.get("data_prevista"), d.get("horimetro_previsto"),
                d.get("equipamento_horimetro"), hoje,
                "km" if (d.get("equipamento_medicao") or "") == "km" else "h")
        elif d.get("status") in ("aberta", "em_andamento", "aguardando_peca") and d.get("data_limite"):
            # (19/10/2026) Data Limite (padrão MWW) dá semáforo real às corretivas: prazo para concluir
            v = _vencimento_ot(d.get("data_limite"), None, None, hoje)
            if v:
                v["texto"] = "limite: " + v["texto"]
            d["vencimento"] = v
        saida.append(d)
    return saida


@router.get("/manutencao/api/ots/proximo-numero")
async def proximo_numero_ot(_auth=Depends(verificar_manutencao)):
    """(24/08/2026) Prévia do próximo número para exibir no modal Nova OT
    (padrão ManWinWin). Prévia informativa — quem reserva de verdade é o
    POST, pela mesma sequência, à prova de corrida."""
    seq = await _proximo_seq_ot()
    from datetime import date as _date
    return {"numero": f"OT-{_date.today().year}-{int(seq['n']):04d}"}


@router.get("/manutencao/api/ots/{ot_id}")
async def detalhe_ot(ot_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_colunas_ot()
    ot = await ajard_query(
        """SELECT ot.*, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                  eq.medicao AS equipamento_medicao, eq.horimetro_atual AS equipamento_horimetro, eq.km_atual AS equipamento_km,
                  eq.sistema_codigo, si.nome AS sistema_nome,
                  eq.centro_custo, cc.nome AS centro_custo_nome,
                  ur.nome AS responsavel_nome, fo.nome AS fornecedor_nome
           FROM manutencao.ot ot
           JOIN operacional.equipamentos eq ON eq.id = ot.equipamento_id
           LEFT JOIN manutencao.sistemas si ON si.codigo = eq.sistema_codigo
           LEFT JOIN manutencao.centros_custo cc ON cc.codigo = eq.centro_custo
           LEFT JOIN public.usuarios_garra ur ON ur.id = ot.responsavel_id
           LEFT JOIN public.fornecedores fo ON fo.id = ot.fornecedor_id
           WHERE ot.id=%s AND ot.ativo=true""", (ot_id,), fetch="one")
    if not ot:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    hist = await ajard_query(
        """SELECT h.*, u.nome AS usuario_nome
           FROM manutencao.ot_historico h
           LEFT JOIN public.usuarios_garra u ON u.id = h.usuario_id
           WHERE h.ot_id=%s ORDER BY h.criado_em""", (ot_id,))
    ultima = await ajard_query(
        """SELECT numero, data_conclusao, horimetro_na_abertura
           FROM manutencao.ot
           WHERE equipamento_id=%s AND status='concluida' AND ativo=true AND id<>%s
           ORDER BY data_conclusao DESC LIMIT 1""",
        (ot["equipamento_id"], ot_id), fetch="one")
    d = dict(ot)
    d["historico"] = [dict(h) for h in hist]
    d["ultima_ot"] = dict(ultima) if ultima else None
    # (21/10/2026) OT ainda não emitida: o registo do Pedido é o contador vivo da máquina até a abertura —
    # se a leitura de origem (abastecimento) foi corrigida, a OT acompanha. Depois de emitida, é foto fixa.
    if d.get("status") in ("programada", "aberta", "rascunho"):
        try:
            await _garantir_leituras()
            lp = await ajard_query(
                "SELECT MAX(leitura) AS l FROM operacional.v_leituras WHERE equipamento_id=%s AND data <= %s",
                (d["equipamento_id"], d["data_abertura"]), fetch="one")
            if lp and lp.get("l") is not None and (d.get("horimetro_na_abertura") is None or float(lp["l"]) != float(d["horimetro_na_abertura"])):
                await ajard_query("UPDATE manutencao.ot SET horimetro_na_abertura=%s WHERE id=%s", (float(lp["l"]), ot_id), fetch="none")
                d["horimetro_na_abertura"] = float(lp["l"])
        except Exception:
            pass
    # (15/10/2026) Registros do mecânico pelo app (relato, horas, horímetro, concluído, fotos)
    # viajam com a ficha — a gestão vê na aba Gestão sem caçar no histórico.
    await _garantir_apontamentos()
    aps = await ajard_query(
        """SELECT a.id, a.relato, a.horas, a.horimetro, a.concluido, a.criado_em, a.atualizado_em, u.nome AS usuario_nome,
                  (SELECT COUNT(*) FROM manutencao.equipamento_documentos f WHERE f.apontamento_id=a.id AND f.ativo=true)::int AS fotos
           FROM manutencao.ot_apontamentos a LEFT JOIN public.usuarios_garra u ON u.id = a.usuario_id
           WHERE a.ot_id=%s AND a.ativo=true ORDER BY a.criado_em""", (ot_id,)) or []
    d["apontamentos"] = [dict(a) for a in aps]
    # (19/10/2026) Gestão › Início › Registo: automático (padrão MWW, campo cinza) = última leitura da
    # máquina até o momento do início (partes, abastecimentos, checklist, OTs encerradas)
    d["leitura_inicio"] = None
    try:
        await _garantir_leituras()
        ini = d.get("data_inicio_real") or (await ajard_query(
            "SELECT MIN(criado_em) AS t FROM manutencao.ot_historico WHERE ot_id=%s AND status_para='em_andamento'", (ot_id,), fetch="one") or {}).get("t")
        if ini:
            lr = await ajard_query(
                "SELECT leitura FROM operacional.v_leituras WHERE equipamento_id=%s AND data <= %s ORDER BY data DESC LIMIT 1",
                (d["equipamento_id"], ini), fetch="one")
            if lr and lr.get("leitura") is not None:
                d["leitura_inicio"] = float(lr["leitura"])
    except Exception:
        pass
    # (19/10/2026) Tarefas › Planejado: se ainda não foi escrito, sugere o checklist antigo (ot_tarefas) ou o
    # procedimento da FMP / modelo da biblioteca — o front mostra como texto editável.
    if not (d.get("tarefas_planejado") or "").strip():
        sug = ""
        try:
            await _garantir_tabela("ot-tarefas")
            tl = await ajard_query("SELECT descricao, feita FROM manutencao.ot_tarefas WHERE ot_id=%s AND ativo=true ORDER BY ordem, criado_em", (ot_id,)) or []
            if tl:
                sug = "\n".join(("- " + (t["descricao"] or "")) for t in tl)
            elif d.get("plano_id"):
                pl = await ajard_query("SELECT procedimento, preparacao_codigo FROM manutencao.planos WHERE id=%s", (d["plano_id"],), fetch="one")
                sug = (pl or {}).get("procedimento") or ""
                if not sug.strip() and (pl or {}).get("preparacao_codigo"):
                    bib = await ajard_query("SELECT tarefas FROM manutencao.biblioteca_preparacoes WHERE codigo=%s", (pl["preparacao_codigo"],), fetch="one")
                    sug = (bib or {}).get("tarefas") or ""
        except Exception:
            sug = ""
        d["tarefas_planejado_sugerido"] = sug
    mo = await ajard_query("SELECT COALESCE(SUM(horas),0) AS h FROM manutencao.ot_mao_obra WHERE ot_id=%s AND ativo=true", (ot_id,), fetch="one")
    d["mo_horas"] = float(mo["h"]) if mo and mo["h"] is not None else 0.0
    d["outros_horas"] = float(d.get("horas_externo") or 0)   # (19/10/2026) horas do interventor externo (Gestão)
    d["pedido"] = None
    if d.get("pedido_id"):
        await _garantir_pedidos()
        ped = await ajard_query(
            """SELECT p.*, us.nome AS solicitante_nome
               FROM manutencao.pedidos p
               LEFT JOIN public.usuarios_garra us ON us.id = p.solicitante_id
               WHERE p.id=%s""", (d["pedido_id"],), fetch="one")
        d["pedido"] = dict(ped) if ped else None
    return d


@router.patch("/manutencao/api/ots/{ot_id}/status")
async def mudar_status_ot(ot_id: str, request: Request, payload=Depends(verificar_manutencao)):
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
    if novo == "cancelada" and len((d.get("observacao") or "").strip()) < 5:
        raise HTTPException(status_code=400, detail="Motivo do cancelamento é obrigatório (mínimo 5 caracteres)")
    reabrindo = atual in ("concluida", "cancelada")
    if reabrindo and len((d.get("observacao") or "").strip()) < 5:
        raise HTTPException(status_code=400, detail="Motivo da reabertura é obrigatório (mínimo 5 caracteres)")
    uid = await _usuario_id(payload)
    extras, params = "", [novo]
    if reabrindo:
        extras = ", data_conclusao=NULL, data_retorno_operacao=NULL, encerrado_em=NULL"
    elif novo == "em_andamento":
        # (20/10/2026) Emitir = Início real da OT (MWW). Só preenche se ainda não havia.
        extras = ", data_inicio_real=COALESCE(data_inicio_real, now())"
    dt_conc = None
    hor_conc = None
    if novo == "concluida":
        # (19/10/2026) Data/hora de conclusão e leitura (horímetro/km) informadas no Encerrar;
        # sem informar, vale agora / nada.
        dc = (d.get("data_conclusao") or "").strip()
        if dc:
            try:
                dt_conc = datetime.fromisoformat(dc.replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail="Data de conclusão inválida")
            from datetime import timedelta
            agora = datetime.now(dt_conc.tzinfo) if dt_conc.tzinfo else datetime.now()
            if dt_conc > agora + timedelta(minutes=10):
                raise HTTPException(status_code=400, detail="Data de conclusão não pode ser no futuro")
        hc = d.get("horimetro")
        if hc not in (None, ""):
            try:
                hor_conc = float(str(hc).replace(",", "."))
            except ValueError:
                raise HTTPException(status_code=400, detail="Horímetro/km inválido")
            if hor_conc < 0:
                raise HTTPException(status_code=400, detail="Horímetro/km inválido")
        extras = (", data_conclusao=COALESCE(%s, now()), data_retorno_operacao=COALESCE(%s, now())::date, "
                  "observacao_conclusao=%s, custo_total=%s, horimetro_conclusao=%s, encerrado_em=now()")
        params += [dt_conc, dt_conc, (d.get("observacao") or "").strip() or None, d.get("custo_total"), hor_conc]
        hp = d.get("horas_parada")
        if hp not in (None, ""):
            try:
                params.append(float(str(hp).replace(",", ".")))
                extras += ", horas_parada=%s"
            except ValueError:
                params.pop()
    params.append(ot_id)
    await ajard_query(
        f"UPDATE manutencao.ot SET status=%s, atualizado_em=now(){extras} WHERE id=%s",
        params, fetch="none")
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, atual, novo, ("↩ REABERTA: " if reabrindo else "") + ((d.get("observacao") or "").strip() or ""), uid), fetch="none")
    if novo == "concluida":
        # (19/10/2026) Regra Garra: máquina parada = da emissão (Início real) ao Fim informado.
        # Sem PDI digitado, o sistema calcula sozinho.
        await ajard_query(
            """UPDATE manutencao.ot o SET horas_parada = ROUND(GREATEST(0, EXTRACT(EPOCH FROM (o.data_conclusao -
                     COALESCE(o.data_inicio_real, (SELECT MIN(h.criado_em) FROM manutencao.ot_historico h WHERE h.ot_id=o.id AND h.status_para='em_andamento'), o.data_abertura)))/3600.0)::numeric, 1)
               WHERE o.id=%s AND o.horas_parada IS NULL AND o.data_conclusao IS NOT NULL""", (ot_id,), fetch="none")
        # (15/09/2026) Fecha o ciclo da FMP: a conclusão vira a nova baseline (data + leitura viva)
        await _garantir_planos_cols()
        await ajard_query(
            """UPDATE manutencao.planos p SET
                      ultima_data = CASE WHEN COALESCE(p.data_fixa,false) AND o.data_prevista IS NOT NULL
                                         THEN o.data_prevista ELSE COALESCE(o.data_conclusao::date, now()::date) END,
                      ultima_leitura = CASE WHEN COALESCE(p.registro_fixo,false) AND o.horimetro_previsto IS NOT NULL
                                            THEN o.horimetro_previsto
                                            ELSE COALESCE(o.horimetro_conclusao, e.horimetro_atual) END
               FROM manutencao.ot o JOIN operacional.equipamentos e ON e.id = o.equipamento_id
               WHERE o.id=%s AND o.plano_id IS NOT NULL AND p.id = o.plano_id""",
            (ot_id,), fetch="none")
        pl = await ajard_query("SELECT plano_id FROM manutencao.ot WHERE id=%s", (ot_id,), fetch="one")
        if pl and pl.get("plano_id"):
            try:
                await _garantir_ot_programada(pl["plano_id"], payload)
            except HTTPException:
                pass
    return {"ok": True, "status": novo}


@router.post("/manutencao/api/ots/{ot_id}/reprogramar")
async def reprogramar_ot(ot_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(24/08/2026) Remanejo de manutenção (padrão ManWinWin 'Programação OT'):
    nova data e/ou horímetro previsto, com MOTIVO obrigatório (domínio
    motivos-reprogramacao do Parametrizar) e trilha completa no histórico."""
    await _garantir_colunas_ot()
    d = await request.json()
    motivo = (d.get("motivo_codigo") or "").strip() or "AJUSTE"   # (19/10/2026) motivo passou a ser opcional (padrão MWW)
    ot = await ajard_query(
        "SELECT id, status, data_prevista, horimetro_previsto FROM manutencao.ot WHERE id=%s AND ativo=true",
        (ot_id,), fetch="one")
    if not ot:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    if ot["status"] not in ("programada", "aberta", "em_andamento", "aguardando_peca"):
        raise HTTPException(status_code=400, detail="OT encerrada não se reprograma")
    data_n = _data_ok(d.get("data_prevista"), "Nova data")
    hor_n = d.get("horimetro_previsto")
    try:
        hor_n = float(str(hor_n).replace(",", ".")) if hor_n not in (None, "") else None
    except ValueError:
        hor_n = None
    if not data_n and hor_n is None:
        raise HTTPException(status_code=400, detail="Informe nova data e/ou novo horímetro")
    await ajard_query(
        "UPDATE manutencao.ot SET data_prevista=%s, horimetro_previsto=%s, prog_alterada=true, atualizado_em=now() WHERE id=%s",
        (data_n, hor_n, ot_id), fetch="none")
    uid = await _usuario_id(payload)
    antes = f"{ot['data_prevista'] or '—'} · {ot['horimetro_previsto'] or '—'}"
    depois = f"{data_n or '—'} · {hor_n if hor_n is not None else '—'}"
    obs = f"Reprogramada [{motivo}]: {antes} → {depois}"
    if (d.get("observacao") or "").strip():
        obs += " · " + d["observacao"].strip()
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, ot["status"], ot["status"], obs, uid), fetch="none")
    return {"ok": True, "data_prevista": data_n, "horimetro_previsto": hor_n}


@router.delete("/manutencao/api/ots/{ot_id}")
async def descartar_rascunho_ot(ot_id: str, _auth=Depends(verificar_manutencao)):
    """(23/09/2026) Cancelar na OT em criação: só rascunho pode ser descartado (soft delete)."""
    o = await ajard_query("SELECT status FROM manutencao.ot WHERE id=%s AND ativo=true", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    if o["status"] != "rascunho":
        raise HTTPException(status_code=400, detail="Só uma OT em criação (rascunho) pode ser descartada — use Cancelar")
    await ajard_query("UPDATE manutencao.ot SET ativo=false, atualizado_em=now() WHERE id=%s", (ot_id,), fetch="none")
    return {"ok": True}


@router.patch("/manutencao/api/ots/{ot_id}")
async def editar_ot(ot_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(24/08/2026) Pacote 2 — aba Origem editável: previsão entra na
    whitelist (paridade EDITÁVEIS × UPDATE — campo aceito É campo gravado)."""
    await _garantir_colunas_ot()
    d = await request.json()
    if "horimetro_previsto" in d and d["horimetro_previsto"] not in (None, ""):
        try:
            d["horimetro_previsto"] = float(str(d["horimetro_previsto"]).replace(",", "."))
        except ValueError:
            d.pop("horimetro_previsto")
    for c in ("sintoma_codigo", "causa_codigo", "setor_interventor", "contrato", "responsavel_id", "fornecedor_id"):
        if c in d and d[c] == "":
            d[c] = None
    if "tdm_previsto" in d:
        try:
            d["tdm_previsto"] = float(str(d["tdm_previsto"]).replace(",", ".")) if d["tdm_previsto"] not in (None, "") else None
        except ValueError:
            d.pop("tdm_previsto")
    if d.get("interventor") not in (None, "interno", "externo"):
        d.pop("interventor")
    # Interventor é um OU outro: interno limpa fornecedor, externo limpa setor
    if d.get("interventor") == "interno":
        d["fornecedor_id"] = None
    elif d.get("interventor") == "externo":
        d["setor_interventor"] = None
        d["responsavel_id"] = None   # (19/10/2026) externo: o responsável é o fornecedor
    if "bloqueada" in d:
        d["bloqueada"] = bool(d["bloqueada"])
    # (19/10/2026) Gestão: Início real / leitura no início / Data Limite
    if "data_inicio_real" in d:
        v = (str(d["data_inicio_real"] or "")).strip()
        try:
            d["data_inicio_real"] = datetime.fromisoformat(v.replace("Z", "+00:00")) if v else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Data de início inválida")
    if "horimetro_inicio" in d:
        v = str(d["horimetro_inicio"] if d["horimetro_inicio"] is not None else "").strip()
        try:
            d["horimetro_inicio"] = float(v.replace(",", ".")) if v else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Leitura no início inválida")
    if "horas_externo" in d:
        v = str(d["horas_externo"] if d["horas_externo"] is not None else "").strip()
        try:
            d["horas_externo"] = float(v.replace(",", ".")) if v else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Horas do serviço externo inválidas")
    if "data_limite" in d:
        v = (str(d["data_limite"] or "")).strip()
        try:
            d["data_limite"] = datetime.fromisoformat(v).date() if v else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Data limite inválida")
    # (10/10/2026) ManWinWin: a programação (data/registro previstos) NÃO se edita na ficha —
    # só pelo Avançado (/reprogramar, com motivo e histórico) ou pelo plano (aplicar-fmp, novo-ciclo).
    for c in ("data_prevista", "horimetro_previsto"):
        d.pop(c, None)
    campos = ["tipo", "prioridade", "descricao", "responsavel_id",
              "fornecedor_id", "custo_total",
              "sintoma_codigo", "causa_codigo", "tipo_trabalho", "projecto_id", "plano_id",
              "interventor", "setor_interventor", "contrato", "tdm_previsto", "bloqueada",
              "tarefas_planejado", "tarefas_realizado",
              "data_inicio_real", "horimetro_inicio", "data_limite", "horas_externo"]
    if "projecto_id" in d and d["projecto_id"] == "":
        d["projecto_id"] = None
    # (20/10/2026) Um campo só (MWW): Tipo de Trabalho manda; a classe (tipo) deriva da letra
    if d.get("tipo_trabalho"):
        d["tipo_trabalho"] = str(d["tipo_trabalho"]).strip().upper()
        d["tipo"] = {"A": "preventiva", "B": "preventiva", "C": "corretiva",
                     "M": "melhoria", "R": "reforma"}.get(d["tipo_trabalho"][0], d.get("tipo") or "corretiva")
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
async def resumo_manutencao(_auth=Depends(verificar_manutencao)):
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
async def semaforos_frota(_auth=Depends(verificar_manutencao)):
    """(24/08/2026) Pacote 2 — SEMÁFORO REAL, calculado contra HOJE:
    1. Ponto de controle compara a PRÓPRIA leitura (ciclo migrado do
       ManWinWin, ex.: 481 dentro do ciclo de 500h) com seus limiares —
       nunca o horímetro absoluto da máquina, que é outra régua (misturar
       as duas fabrica vencidos falsos — bug corrigido 24/08).
       Sincronizar o ciclo com o horímetro do Operacional exige a baseline
       da última revisão → entra no pacote de preventivas (plano×horímetro).
    2. OTs PROGRAMADAS entram no estado do equipamento contra HOJE: vencida
       (data passou OU horímetro previsto ≤ horímetro atual da máquina —
       mesma régua absoluta) → 🔴; a vencer (≤7 d / ≤50 h·km) → 🟡.
    Semáforo é estado real, nunca enfeite (decisão 24/08)."""
    await _garantir_colunas_ot()
    await _garantir_ficha_cols()
    rows = await ajard_query(
        """SELECT e.id, e.codigo, e.descricao, e.horimetro_atual, e.medicao,
                  e.categoria, e.equipamento_pai, e.posicao,
                  (SELECT COUNT(*)::int FROM operacional.equipamentos f
                    WHERE f.equipamento_pai = e.id AND f.ativo = true) AS n_filhos,
                  e.sistema_codigo, e.tipo_sigla,
                  (SELECT u.nome FROM public.usuarios_garra u WHERE u.id = e.operador_responsavel_id) AS operador_nome,
                  p.codigo AS ponto, p.leitura_atual, p.limiar_atencao,
                  p.limiar_urgente, p.limiar_maximo,
                  (SELECT COUNT(*)::int FROM manutencao.ot o
                    WHERE o.equipamento_id = e.id AND o.ativo = true
                      AND o.status IN ('aberta','em_andamento','aguardando_peca')) AS ots_abertas
           FROM operacional.equipamentos e
           LEFT JOIN manutencao.pontos_controle p
                  ON p.equipamento_id = e.id AND p.ativo = true
                 -- (19/09/2026) equipamento com FMP ativa: o ciclo vivo é a FMP; o ponto
                 -- legado (contador próprio, não sincronizado) sai do semáforo
                 AND NOT EXISTS (SELECT 1 FROM manutencao.planos pl
                                  WHERE pl.equipamento_id = e.id AND pl.ativo = true)
           WHERE e.ativo = true AND COALESCE(e.categoria,'') <> 'apoio'
           ORDER BY e.codigo, p.codigo""")
    equipes = {}
    ordem = {"vermelho": 0, "laranja": 1, "amarelo": 2, "verde": 3}
    # (15/09/2026) 3ª fonte do semáforo: previsão das FMPs (última execução + período × leitura viva)
    prev_por_eq = {}
    try:
        for it in (await _calcular_previsoes())["itens"]:
            if it["status"] in ("vencida", "a_vencer"):
                prev_por_eq.setdefault(it["equipamento_id"], []).append(it)
    except Exception:
        prev_por_eq = {}
    for r in rows:
        vivo = float(r["horimetro_atual"] or 0)
        eq = equipes.setdefault(str(r["id"]), {
            "id": str(r["id"]), "codigo": r["codigo"], "descricao": r["descricao"],
            "horimetro": vivo, "medicao": r["medicao"],
            "sistema_codigo": r.get("sistema_codigo"), "tipo_sigla": r.get("tipo_sigla"),
            "operador_nome": r.get("operador_nome"),
            "categoria": r.get("categoria"),
            "equipamento_pai": str(r["equipamento_pai"]) if r.get("equipamento_pai") else None,
            "posicao": r.get("posicao"), "n_filhos": int(r.get("n_filhos") or 0),
            "ots_abertas": r["ots_abertas"], "ots_programadas": 0,
            "status": "verde", "motivo": None, "proxima": None, "pontos": []})
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
                eq["motivo"] = f"ponto {r['ponto']} em {int(leit)}"
    prog = await ajard_query(
        """SELECT o.equipamento_id, o.numero, o.data_prevista, o.horimetro_previsto
           FROM manutencao.ot o
           WHERE o.ativo = true AND o.status = 'programada'
           ORDER BY o.data_prevista NULLS LAST, o.horimetro_previsto NULLS LAST""")
    from datetime import date as _date
    hoje = _date.today()
    nivel_st = {0: "vermelho", 1: "amarelo"}
    for o in prog:
        eq = equipes.get(str(o["equipamento_id"]))
        if not eq:
            continue
        eq["ots_programadas"] += 1
        v = _vencimento_ot(o["data_prevista"], o["horimetro_previsto"],
                           eq["horimetro"], hoje,
                           "km" if (eq.get("medicao") or "") == "km" else "h")
        if not v:
            continue
        txt = f"{o['numero']} · {v['texto']}"
        if eq["proxima"] is None or v["nivel"] < eq["proxima"][0]:
            eq["proxima"] = (v["nivel"], txt)
        st = nivel_st.get(v["nivel"])
        if st and ordem[st] < ordem[eq["status"]]:
            eq["status"] = st
            eq["motivo"] = txt
    # FMPs vencidas / a vencer sem OT programada (a OT, quando existe, já entrou acima)
    for eq_id, its in prev_por_eq.items():
        eq = equipes.get(eq_id)
        if not eq:
            continue
        for it in its:
            if it.get("ot"):
                continue
            nivel = 0 if it["status"] == "vencida" else 1
            resto = it.get("restante")
            if it["modo"] == "calendario":
                det = ("vencida há %d d" % abs(int(resto))) if nivel == 0 else ("vence em %d d" % int(resto))
            else:
                det = ("vencida há %s %s" % (abs(int(resto)), it["unidade"])) if nivel == 0 else ("faltam %s %s" % (int(resto), it["unidade"]))
            txt = f"FMP {it['plano_codigo']} · {det}"
            if eq["proxima"] is None or nivel < eq["proxima"][0]:
                eq["proxima"] = (nivel, txt)
            st = nivel_st[nivel]
            if ordem[st] < ordem[eq["status"]]:
                eq["status"] = st
                eq["motivo"] = txt
    for eq in equipes.values():
        if eq["proxima"] is not None:
            eq["proxima"] = eq["proxima"][1]
    lista = sorted(equipes.values(), key=lambda x: (ordem[x["status"]], x["codigo"]))
    return lista


# ── ALMOXARIFADO: peças reais (Onda 1) com busca ──
# (30/09/2026) Família EFETIVA da peça: a cadastrada quando já tem subfamília (AC.010);
# senão a que a própria codificação carrega (C.AC.010.001 → AC.010). Assim a Norma de
# Materiais mostra as subfamílias mesmo onde o cadastro só apontou a raiz (AC).
def _classe_cond(alias: str = "") -> str:
    """(19/10/2026) Filtro por CLASSE da Norma ('@G'): classe gravada na peça,
    ou prefixo do código (G.BT.010…), ou classe da família cadastrada."""
    a = f"{alias}." if alias else ""
    fe = _fam_efetiva(alias)
    return (f"(upper(COALESCE({a}classe,'')) = %s OR upper({a}codigo) LIKE %s "
            f"OR split_part({fe},'.',1) IN (SELECT codigo FROM manutencao.familias WHERE upper(COALESCE(classe,''))=%s))")


def _classe_params(familia: str) -> list:
    cl = familia.strip()[1:].upper()
    return [cl, cl + ".%", cl]


def _fam_efetiva(alias: str = "") -> str:
    a = f"{alias}." if alias else ""
    return (f"CASE WHEN {a}familia_codigo LIKE '%%.%%' THEN {a}familia_codigo "
            f"WHEN {a}codigo ~ '^[A-Za-z]\\.[A-Za-z0-9]+\\.[0-9]+\\.' THEN split_part({a}codigo,'.',2)||'.'||split_part({a}codigo,'.',3) "
            f"ELSE {a}familia_codigo END")


# (10/10/2026) Busca sem acento: "oleo" acha "Óleo", "filtro de ar" acha "FILTRO DE AR" —
# regra única para o catálogo e para o armazém (digitação na oficina não tem acento).
_SEM_ACENTO_DE = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
_SEM_ACENTO_PARA = "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
def _sem_acento_sql(col: str) -> str:
    return f"translate({col}, '{_SEM_ACENTO_DE}', '{_SEM_ACENTO_PARA}')"
def _sem_acento(txt: str) -> str:
    return (txt or "").translate(str.maketrans(_SEM_ACENTO_DE, _SEM_ACENTO_PARA))


# ══════════════════════════════════════════════════════════════
# (10/10/2026) RUBRICA DE MATERIAL — automática, padrão ManWinWin (árvore 2 — Peças e consumíveis):
#   do ESTOQUE Garra (saída de armazém)      2.01 consumo · 2.02 peças de reposição · 2.03 lubrificantes
#   DIRETO do fornecedor/oficina (aplicação) 2.04 consumo · 2.05 peças de reposição · 2.06 lubrificantes
# A classe sai da família/descrição da peça; o usuário só diz de ONDE veio. Fica gravada na
# movimentação para o relatório de custos por conta.
_RX_LUB = re.compile(r"\b(oleo|óleo|graxa|lubrif|fluido|arla|aditivo|hidraul)", re.I)
_RX_CONS = re.compile(r"\b(parafuso|porca|arruela|abra[cç]adeira|fita|estopa|lixa|eletrodo|solda|tinta|spray|pano|luva|rebite|anilha|silicone|cola|adesivo|desengrip|wd|contato|terminal|fus[ií]vel|l[aâ]mpada|cabo|fio)", re.I)
def _rubrica_material(tipo_mov: str, familia: str, descricao: str) -> str:
    fam = (familia or "").upper()
    desc = descricao or ""
    if fam.startswith("LU") or _RX_LUB.search(desc):
        classe = 3
    elif fam.startswith("CO") or _RX_CONS.search(desc):
        classe = 1
    else:
        classe = 2
    base = 4 if tipo_mov == "aplicacao_direta" else 1
    return f"2.0{base + classe - 1}"


@router.get("/manutencao/api/pecas")
async def listar_pecas(busca: str = None, familia: str = None, limit: int = 100,
                       _auth=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    limit = min(max(int(limit or 100), 1), 300)
    where, params = ["ativo=true"], []
    if busca and busca.strip():
        # (19/10/2026) cada palavra tem de aparecer (em qualquer campo): "kondor 45" acha
        # "Bateria Kondor 45 Amp" mesmo com palavras fora de ordem
        for palavra in _sem_acento(busca.strip()).split():
            b = f"%{palavra}%"
            where.append("(" + " OR ".join(f"{_sem_acento_sql(c)} ILIKE %s" for c in ("codigo", "descricao", "codigo_externo", "codigo_fabricante")) + ")"); params += [b, b, b, b]
    if familia and familia.strip().startswith("@"):
        # (19/10/2026) '@G' = toda uma CLASSE da Norma (classe gravada na peça
        # OU prefixo do código OU classe da família cadastrada)
        where.append(_classe_cond()); params += _classe_params(familia)
    elif familia and familia.strip():
        # subfamílias por pontuação (MC pega MC e MC.010…)
        fe = _fam_efetiva()
        where.append(f"({fe} = %s OR {fe} LIKE %s)")
        params += [familia.strip(), familia.strip() + ".%"]
    rows = await ajard_query(
        f"""SELECT id, codigo, descricao, unidade, familia_codigo, custo_medio
            FROM manutencao.pecas WHERE {' AND '.join(where)}
            ORDER BY codigo LIMIT {limit}""", params)
    for r in rows:
        r["id"] = str(r["id"]) if r.get("id") is not None else None
    total = await ajard_query("SELECT COUNT(*)::int AS n FROM manutencao.pecas WHERE ativo=true", fetch="one")
    return {"total": total["n"], "itens": [dict(r) for r in rows]}


# ── FICHA DO EQUIPAMENTO: Ondas 1+2+3 agregadas ──
@router.patch("/manutencao/api/equipamentos/{eq_id}/ficha")
async def editar_ficha(eq_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(26/08/2026) Ficha editável padrão ManWinWin. Regra sagrada: CÓDIGO
    nunca muda (identidade histórica). Paridade: campo aceito = campo gravado."""
    await _garantir_ficha_cols()
    d = await request.json()
    import json as _json
    campos_txt = ["descricao", "sistema_codigo", "centro_custo", "localizacao",
                  "marca", "modelo", "ano_fabricacao", "num_serie", "tipo_sigla",
                  "criticidade", "cor", "posicao"]
    sets, params = [], []
    for c in campos_txt:
        if c in d:
            sets.append(f"{c}=%s"); params.append((str(d[c]).strip() or None) if d[c] is not None else None)
    if "operador_responsavel_id" in d:
        sets.append("operador_responsavel_id=%s"); params.append(d["operador_responsavel_id"] or None)
    if "equipamento_pai" in d:
        sets.append("equipamento_pai=%s"); params.append(d["equipamento_pai"] or None)
    if "data_aquisicao" in d:
        sets.append("data_aquisicao=%s"); params.append((d["data_aquisicao"] or "").strip() or None)
    if "valor_aquisicao" in d:
        try:
            sets.append("valor_aquisicao=%s")
            params.append(float(str(d["valor_aquisicao"]).replace(",", ".")) if d["valor_aquisicao"] not in (None, "") else None)
        except ValueError:
            sets.pop()
    # (01/09/2026) Novo Objeto — réplica ManWinWin: fornecedor, vida útil,
    # valor atual, operador desde, notas
    if "fornecedor_id" in d:
        sets.append("fornecedor_id=%s"); params.append(d["fornecedor_id"] or None)
    if "operador_desde" in d:
        sets.append("operador_desde=%s"); params.append((str(d["operador_desde"] or "")).strip() or None)
    if "notas" in d:
        sets.append("notas=%s"); params.append((str(d["notas"] or "")).strip() or None)
    for c_num in ("vida_util_h", "valor_atual"):
        if c_num in d:
            try:
                sets.append(f"{c_num}=%s")
                params.append(float(str(d[c_num]).replace(".", "").replace(",", ".")) if d[c_num] not in (None, "") else None)
            except ValueError:
                sets.pop()
    # leitura inicial ("Registro (H)"): só preenche se o cadastro ainda está zerado —
    # leitura nunca recua, nem no nascimento
    if "leitura_inicial" in d and d["leitura_inicial"] not in (None, ""):
        try:
            li = float(str(d["leitura_inicial"]).replace(".", "").replace(",", "."))
            med = await ajard_query("SELECT medicao FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
            campo = "km_atual" if (med and med["medicao"] == "km") else "horimetro_atual"
            sets.append(f"{campo}=CASE WHEN COALESCE({campo},0)=0 THEN %s ELSE {campo} END"); params.append(li)
        except ValueError:
            pass
    if "caracteristicas" in d:
        sets.append("caracteristicas=%s"); params.append(_json.dumps(d["caracteristicas"] or []))
    if "garantia" in d:
        _f = (d["garantia"] or {}).get("fmd") or {}
        if _f.get("calc") is False and not _fmd_num(_f.get("valor")):
            raise HTTPException(status_code=400, detail="Desmarcou 'Calcular por registos': informe o FMD manual")
        sets.append("garantia=%s"); params.append(_json.dumps(d["garantia"] or {}))
    # (14/09/2026) Info. Complementares (réplica MWW): codificação, tipos,
    # identificações, histórico, família, combustível — JSON livre
    if "info_compl" in d:
        sets.append("info_compl=%s"); params.append(_json.dumps(d["info_compl"] or {}))
    # (15/09/2026) Inactivo pela própria ficha do Objeto (MWW): ativa/inativa sem sair do cadastro
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if "os_alimenta_manutencao" in d:
        sets.append("os_alimenta_manutencao=%s"); params.append(bool(d["os_alimenta_manutencao"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(eq_id)
    r = await ajard_query(
        f"UPDATE operacional.equipamentos SET {', '.join(sets)} WHERE id=%s AND ativo=true RETURNING codigo",
        tuple(params), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    return {"ok": True, "codigo": r["codigo"]}


_MO_OK = False
async def _garantir_mao_obra():
    """(01/09/2026) Registo de Mão de Obra por OT (HH) — padrão ManWinWin:
    uma linha por colaborador × dia × horas, custo/hora na linha (o último
    custo usado pelo colaborador é a sugestão). Alimenta HHT/HHMC/HHMP/CLM
    das Análises. Também: Parâmetros Manuais por objecto/ano/mês e
    Documentos do objecto (anexo Ficheiro → Supabase Storage)."""
    global _MO_OK
    if _MO_OK:
        return
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.ot_mao_obra (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ot_id UUID NOT NULL,
            usuario_id UUID,
            nome TEXT,
            data DATE NOT NULL DEFAULT now()::date,
            horas NUMERIC(8,2) NOT NULL,
            custo_hora NUMERIC(12,2),
            observacao TEXT,
            origem TEXT DEFAULT 'desktop',
            ativo BOOLEAN DEFAULT true,
            criado_por UUID,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_ot_mao_obra_ot ON manutencao.ot_mao_obra (ot_id)", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.parametros_manuais (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL,
            ano INT NOT NULL,
            mes INT NOT NULL,
            codigo TEXT NOT NULL,
            nome TEXT,
            valor NUMERIC(14,2),
            usuario_id UUID,
            atualizado_em TIMESTAMPTZ DEFAULT now(),
            UNIQUE (equipamento_id, ano, mes, codigo))""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.equipamento_documentos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL,
            nome TEXT NOT NULL,
            path TEXT NOT NULL,
            content_type TEXT,
            tamanho INT,
            descricao TEXT,
            usuario_id UUID,
            ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    _MO_OK = True


def _mo_num(v):
    if v in (None, ""):
        return None
    txt = str(v).strip().replace("R$", "").replace(" ", "")
    if "," in txt:
        txt = txt.replace(".", "").replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


# ══════════════════════════════════════════════════════════════
# (10/10/2026) FUNÇÕES × CUSTO HH — padrão ManWinWin (Organograma › Função › Custo HH) adaptado:
# a função carrega o custo/HH padrão e a conta; o colaborador aponta para uma função (com
# custo próprio opcional). Na OT, escolher o colaborador (ou a função do terceiro) preenche o
# custo/h sozinho e a conta sai automática (1.01 interno · 1.03 externo, ou a da função).
# ══════════════════════════════════════════════════════════════
_FUNC_OK = False
async def _garantir_funcoes():
    global _FUNC_OK
    if _FUNC_OK:
        return
    await _garantir_mao_obra()
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.funcoes (
            codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, setor TEXT, custo_hh NUMERIC(12,2),
            rubrica TEXT DEFAULT '1.01', externa BOOLEAN DEFAULT false, ativo BOOLEAN DEFAULT true)""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.colaborador_funcao (
            usuario_id UUID PRIMARY KEY, funcao_codigo TEXT, custo_hh NUMERIC(12,2),
            atualizado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    for ddl in ("ALTER TABLE manutencao.ot_mao_obra ADD COLUMN IF NOT EXISTS funcao_codigo TEXT",
                "ALTER TABLE manutencao.ot_mao_obra ADD COLUMN IF NOT EXISTS fornecedor_id UUID",   # (19/10/2026) terceiro = fornecedor do cadastro
                "ALTER TABLE manutencao.ot_mao_obra ADD COLUMN IF NOT EXISTS rubrica TEXT"):
        await ajard_query(ddl, fetch="none")
    # (10/10/2026) ORGANOGRAMA da empresa (ManWinWin): setor › função › pessoas. Funções reais da Garra,
    # idempotente — custo HH e conta ficam para a Parametrização. Setor INT (Inativo) junta-se a MAN/PRD/EXT/ADM.
    await _garantir_dominios()
    await ajard_query("""INSERT INTO manutencao.setores_interventor (codigo,nome) VALUES ('INT','Inativo')
                         ON CONFLICT (codigo) DO NOTHING""", fetch="none")
    await ajard_query("""
        INSERT INTO manutencao.funcoes (codigo, nome, setor, rubrica, externa) VALUES
          ('MAN.01','Responsável Manutenção','MAN','1.01',false), ('MAN.02','Canalizador/Eletricista','MAN','1.01',false),
          ('MAN.03','Serralheiro Mecânico','MAN','1.01',false),   ('MAN.04','Preparador Trabalho','MAN','1.01',false),
          ('MAN.05','Eletromecânico','MAN','1.01',false),         ('MAN.06','Rasteleiro','MAN','1.01',false),
          ('MAN.07','Servente','MAN','1.01',false),               ('MAN.08','Auxiliar Manutenção','MAN','1.01',false),
          ('MAN.09','Operadores','PRD','1.02',false),             ('MAN.10','Responsável Produção','PRD','1.02',false),
          ('MAN.11','Manutenção externa','EXT','1.03',true)
        ON CONFLICT (codigo) DO NOTHING""", fetch="none")
    # seed provisório anterior (códigos inventados) sai se nunca foi usado
    await ajard_query("""DELETE FROM manutencao.funcoes f
                          WHERE f.codigo IN ('MEC','ELE','SOL','BOR','LUB','AJM','OPE','MOT','SER','ADM','TER','OFI')
                            AND f.custo_hh IS NULL
                            AND NOT EXISTS (SELECT 1 FROM manutencao.colaborador_funcao c WHERE c.funcao_codigo=f.codigo)
                            AND NOT EXISTS (SELECT 1 FROM manutencao.ot_mao_obra m WHERE m.funcao_codigo=f.codigo)""", fetch="none")
    _FUNC_OK = True


async def _funcoes_lista(so_ativas=True):
    await _garantir_funcoes()
    rows = await ajard_query(
        "SELECT codigo, nome, setor, custo_hh, rubrica, externa, ativo FROM manutencao.funcoes"
        + (" WHERE ativo=true" if so_ativas else "") + " ORDER BY codigo") or []
    out = []
    for r in rows:
        d = dict(r); d["custo_hh"] = float(d["custo_hh"]) if d["custo_hh"] is not None else None
        out.append(d)
    return out


async def _colaboradores_funcao():
    await _garantir_funcoes()
    rows = await ajard_query(
        """SELECT cf.usuario_id, cf.funcao_codigo, cf.custo_hh, f.nome AS funcao_nome, f.custo_hh AS custo_funcao, f.rubrica
           FROM manutencao.colaborador_funcao cf LEFT JOIN manutencao.funcoes f ON f.codigo = cf.funcao_codigo""") or []
    return {str(r["usuario_id"]): {"funcao_codigo": r["funcao_codigo"], "funcao_nome": r["funcao_nome"],
                                   "custo_hh": float(r["custo_hh"]) if r["custo_hh"] is not None else
                                               (float(r["custo_funcao"]) if r["custo_funcao"] is not None else None),
                                   "custo_proprio": r["custo_hh"] is not None, "rubrica": r["rubrica"]}
            for r in rows}


@router.get("/manutencao/api/funcoes")
async def funcoes_listar(_auth=Depends(verificar_manutencao)):
    """Funções (todas, inclusive inativas) + apontamento colaborador→função, para Parametrizar."""
    return {"funcoes": await _funcoes_lista(False), "colaboradores": await _colaboradores_funcao()}


@router.post("/manutencao/api/funcoes")
async def funcoes_upsert(request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_funcoes()
    d = await request.json()
    cod = (d.get("codigo") or "").strip().upper()
    nome = (d.get("nome") or "").strip()
    if not cod or not nome:
        raise HTTPException(status_code=400, detail="Código e nome são obrigatórios")
    await ajard_query(
        """INSERT INTO manutencao.funcoes (codigo, nome, setor, custo_hh, rubrica, externa)
           VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (codigo) DO UPDATE SET nome=EXCLUDED.nome, setor=EXCLUDED.setor, custo_hh=EXCLUDED.custo_hh,
                                              rubrica=EXCLUDED.rubrica, externa=EXCLUDED.externa""",
        (cod, nome, (d.get("setor") or "").strip() or None, _mo_num(d.get("custo_hh")),
         (d.get("rubrica") or "").strip() or ("1.03" if d.get("externa") else "1.01"), bool(d.get("externa"))), fetch="none")
    return {"ok": True, "codigo": cod}


@router.patch("/manutencao/api/funcoes/{codigo}")
async def funcoes_editar(codigo: str, request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_funcoes()
    d = await request.json()
    sets, params = [], []
    if "nome" in d and (d.get("nome") or "").strip():
        sets.append("nome=%s"); params.append(d["nome"].strip())
    if "setor" in d:
        sets.append("setor=%s"); params.append((d.get("setor") or "").strip() or None)
    if "custo_hh" in d:
        sets.append("custo_hh=%s"); params.append(_mo_num(d.get("custo_hh")))
    if "rubrica" in d:
        sets.append("rubrica=%s"); params.append((d.get("rubrica") or "").strip() or None)
    if "externa" in d:
        sets.append("externa=%s"); params.append(bool(d["externa"]))
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(codigo)
    await ajard_query(f"UPDATE manutencao.funcoes SET {', '.join(sets)} WHERE codigo=%s", tuple(params), fetch="none")
    return {"ok": True}


@router.delete("/manutencao/api/funcoes/{codigo}")
async def funcoes_excluir(codigo: str, payload=Depends(verificar_manutencao)):
    """Exclusão definitiva só se nunca usada (OT ou colaborador); senão 409 → desativar."""
    await _garantir_funcoes()
    uso = await ajard_query(
        """SELECT (SELECT COUNT(*) FROM manutencao.ot_mao_obra WHERE funcao_codigo=%s AND ativo=true)
                + (SELECT COUNT(*) FROM manutencao.colaborador_funcao WHERE funcao_codigo=%s) AS n""",
        (codigo, codigo), fetch="one")
    if uso and int(uso["n"]) > 0:
        raise HTTPException(status_code=409, detail="Função em uso (OTs ou colaboradores) — desative em vez de excluir")
    await ajard_query("DELETE FROM manutencao.funcoes WHERE codigo=%s", (codigo,), fetch="none")
    return {"ok": True}


@router.put("/manutencao/api/colaboradores/{usuario_id}/funcao")
async def colaborador_funcao(usuario_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """Aponta a função do colaborador (e um custo/HH próprio, opcional — vazio usa o da função)."""
    await _garantir_funcoes()
    d = await request.json()
    fc = (d.get("funcao_codigo") or "").strip() or None
    custo = _mo_num(d.get("custo_hh"))
    if not fc and custo is None:
        await ajard_query("DELETE FROM manutencao.colaborador_funcao WHERE usuario_id=%s", (usuario_id,), fetch="none")
        return {"ok": True}
    await ajard_query(
        """INSERT INTO manutencao.colaborador_funcao (usuario_id, funcao_codigo, custo_hh)
           VALUES (%s,%s,%s)
           ON CONFLICT (usuario_id) DO UPDATE SET funcao_codigo=EXCLUDED.funcao_codigo, custo_hh=EXCLUDED.custo_hh, atualizado_em=now()""",
        (usuario_id, fc, custo), fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/ots/{ot_id}/mao-obra")
async def ot_mao_obra_listar(ot_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_funcoes()
    rows = await ajard_query(
        """SELECT m.id, m.usuario_id, COALESCE(u.nome, fo.nome, m.nome) AS nome, m.data, m.horas,
                  m.custo_hora, m.observacao, m.origem, m.criado_em, m.funcao_codigo, m.rubrica, f.nome AS funcao_nome,
                  m.fornecedor_id, fo.nome AS fornecedor_nome
           FROM manutencao.ot_mao_obra m
           LEFT JOIN public.usuarios_garra u ON u.id = m.usuario_id
           LEFT JOIN public.fornecedores fo ON fo.id = m.fornecedor_id
           LEFT JOIN manutencao.funcoes f ON f.codigo = m.funcao_codigo
           WHERE m.ot_id=%s AND m.ativo=true
           ORDER BY m.data DESC, m.criado_em DESC""", (ot_id,)) or []
    itens, th, tc = [], 0.0, 0.0
    for r in rows:
        h = float(r["horas"] or 0); ch = float(r["custo_hora"]) if r["custo_hora"] is not None else None
        v = round(h * ch, 2) if ch is not None else None
        th += h; tc += (v or 0)
        itens.append({"id": str(r["id"]), "usuario_id": str(r["usuario_id"]) if r["usuario_id"] else None,
                      "nome": r["nome"], "data": r["data"].isoformat() if r["data"] else None,
                      "horas": h, "custo_hora": ch, "valor": v, "observacao": r["observacao"],
                      "origem": r["origem"], "funcao_codigo": r["funcao_codigo"], "funcao_nome": r["funcao_nome"],
                      "rubrica": r["rubrica"], "externo": r["usuario_id"] is None,
                      "fornecedor_id": str(r["fornecedor_id"]) if r.get("fornecedor_id") else None, "fornecedor_nome": r.get("fornecedor_nome")})
    # sugestão de custo/hora por colaborador = último usado (fallback quando não há função apontada)
    sug = await ajard_query(
        """SELECT DISTINCT ON (usuario_id) usuario_id, custo_hora
           FROM manutencao.ot_mao_obra
           WHERE ativo=true AND usuario_id IS NOT NULL AND custo_hora IS NOT NULL
           ORDER BY usuario_id, criado_em DESC""") or []
    # PLANEJADO: previsões manuais da OT (ot_mo_previsto) + mão de obra prevista na FMP (plano.mao_obra JSONB)
    prev = await ajard_query(
        """SELECT p.hh_previsto, p.custo_previsto, p.mao_obra, p.codigo AS plano_codigo FROM manutencao.ot o
           JOIN manutencao.planos p ON p.id = o.plano_id WHERE o.id=%s""", (ot_id,), fetch="one")
    await _garantir_tabela("ot-mo-previsto")
    await _garantir_dominios()
    import json as _json
    previstos = []
    if prev and prev.get("mao_obra"):
        mo = prev["mao_obra"]
        if isinstance(mo, str):
            try: mo = _json.loads(mo)
            except Exception: mo = []
        for it in (mo or []):
            try:
                hh = float(str(it.get("hh") or 0).replace(",", ".")); ch = float(str(it.get("custo") or 0).replace(",", "."))
            except ValueError:
                hh, ch = 0.0, 0.0
            previstos.append({"id": None, "origem": prev["plano_codigo"], "funcao_codigo": None, "funcao_nome": it.get("funcao") or "",
                              "hh": hh, "custo_hh": ch, "custo": round(hh * ch, 2), "rubrica": it.get("rubrica") or "1.01"})
    for r in (await ajard_query(
            """SELECT t.id, t.funcao_codigo, COALESCE(f.nome, t.funcao_nome) AS funcao_nome, t.hh, t.custo_hh, t.rubrica, t.observacao
               FROM manutencao.ot_mo_previsto t LEFT JOIN manutencao.funcoes f ON f.codigo = t.funcao_codigo
               WHERE t.ot_id=%s AND t.ativo=true ORDER BY t.criado_em""", (ot_id,)) or []):
        hh = float(r["hh"] or 0); ch = float(r["custo_hh"] or 0)
        previstos.append({"id": str(r["id"]), "origem": "OT", "funcao_codigo": r["funcao_codigo"], "funcao_nome": r["funcao_nome"] or "",
                          "hh": hh, "custo_hh": ch, "custo": round(hh * ch, 2), "rubrica": r["rubrica"], "observacao": r["observacao"]})
    hh_prev = round(sum(x["hh"] for x in previstos), 2) if previstos else (float(prev["hh_previsto"]) if prev and prev["hh_previsto"] is not None else None)
    custo_prev = round(sum(x["custo"] for x in previstos), 2) if previstos else (float(prev["custo_previsto"]) if prev and prev["custo_previsto"] is not None else None)
    setores = await ajard_query("SELECT codigo, nome FROM manutencao.setores_interventor WHERE ativo=true ORDER BY codigo") or []
    return {"itens": itens, "total_horas": round(th, 2), "total_custo": round(tc, 2),
            "custo_hora_sugerido": {str(x["usuario_id"]): float(x["custo_hora"]) for x in sug},
            "funcoes": await _funcoes_lista(True), "colaboradores": await _colaboradores_funcao(),
            "setores": [dict(s) for s in setores], "previstos": previstos,
            "hh_previsto": hh_prev, "custo_previsto": custo_prev}


@router.post("/manutencao/api/ots/{ot_id}/mao-obra")
async def ot_mao_obra_criar(ot_id: str, request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    d = await request.json()
    horas = _mo_num(d.get("horas"))
    if not horas or horas <= 0:
        raise HTTPException(status_code=400, detail="Informe as horas trabalhadas")
    o = await ajard_query("SELECT status FROM manutencao.ot WHERE id=%s AND ativo=true", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    uid = await _usuario_id(payload)
    usuario_id = (d.get("usuario_id") or "").strip() or None
    nome = (d.get("nome") or "").strip() or None
    fornecedor_id = (d.get("fornecedor_id") or "").strip() or None   # (19/10/2026) terceiro do cadastro
    if fornecedor_id and not nome:
        fo = await ajard_query("SELECT nome FROM public.fornecedores WHERE id=%s", (fornecedor_id,), fetch="one")
        nome = fo["nome"] if fo else None
    if not usuario_id and not nome:
        raise HTTPException(status_code=400, detail="Informe o colaborador ou o fornecedor")
    data = (d.get("data") or "").strip() or None
    # (10/10/2026) função → custo/HH e conta automáticos quando não vierem preenchidos
    await _garantir_funcoes()
    funcao = (d.get("funcao_codigo") or "").strip() or None
    custo = _mo_num(d.get("custo_hora"))
    rubrica = (d.get("rubrica") or "").strip() or None
    if usuario_id:
        cf = (await _colaboradores_funcao()).get(usuario_id)
        if cf:
            funcao = funcao or cf.get("funcao_codigo")
            if custo is None: custo = cf.get("custo_hh")
            rubrica = rubrica or cf.get("rubrica")
    if funcao:
        f = await ajard_query("SELECT custo_hh, rubrica FROM manutencao.funcoes WHERE codigo=%s", (funcao,), fetch="one")
        if f:
            if custo is None and f["custo_hh"] is not None: custo = float(f["custo_hh"])
            rubrica = rubrica or f["rubrica"]
    rubrica = rubrica or ("1.01" if usuario_id else "1.03")
    row = await ajard_query_id(
        """INSERT INTO manutencao.ot_mao_obra (ot_id, usuario_id, nome, data, horas, custo_hora, observacao, origem, criado_por, funcao_codigo, rubrica, fornecedor_id)
           VALUES (%s,%s,%s,COALESCE(%s::date, now()::date),%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ot_id, usuario_id, nome, data, horas, custo,
         (d.get("observacao") or "").strip() or None, (d.get("origem") or "desktop"), uid, funcao, rubrica, fornecedor_id))
    return {"ok": True, "id": str(row["id"]) if row else None}


@router.delete("/manutencao/api/mao-obra/{mo_id}")
async def ot_mao_obra_excluir(mo_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    await ajard_query("UPDATE manutencao.ot_mao_obra SET ativo=false WHERE id=%s", (mo_id,), fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/equipamentos/{eq_id}/parametros-manuais")
async def param_manuais_listar(eq_id: str, ano: int, _auth=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    rows = await ajard_query(
        """SELECT codigo, MAX(nome) AS nome, mes, SUM(valor) AS valor
           FROM manutencao.parametros_manuais WHERE equipamento_id=%s AND ano=%s
           GROUP BY codigo, mes ORDER BY codigo, mes""", (eq_id, ano)) or []
    out = {}
    for r in rows:
        p = out.setdefault(r["codigo"], {"codigo": r["codigo"], "nome": r["nome"], "meses": [None] * 12})
        if r["nome"] and not p["nome"]: p["nome"] = r["nome"]
        m = int(r["mes"])
        if 1 <= m <= 12 and r["valor"] is not None:
            p["meses"][m - 1] = float(r["valor"])
    for p in out.values():
        p["total"] = round(sum(x for x in p["meses"] if x is not None), 2)
    return list(out.values())


@router.put("/manutencao/api/equipamentos/{eq_id}/parametros-manuais")
async def param_manuais_gravar(eq_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """Grava um parâmetro manual: {ano, codigo, nome, meses:[12 valores|null]}.
    Mês vazio apaga a célula. Código em maiúsculas, sem espaços."""
    await _garantir_mao_obra()
    d = await request.json()
    cod = (d.get("codigo") or "").strip().upper().replace(" ", "_")
    ano = int(d.get("ano") or 0)
    if not cod or not ano:
        raise HTTPException(status_code=400, detail="Informe código e ano")
    meses = d.get("meses") or []
    if len(meses) != 12:
        raise HTTPException(status_code=400, detail="Envie 12 valores (Jan..Dez)")
    uid = await _usuario_id(payload)
    nome = (d.get("nome") or "").strip() or None
    for i, v in enumerate(meses):
        val = _mo_num(v)
        if val is None:
            await ajard_query("DELETE FROM manutencao.parametros_manuais WHERE equipamento_id=%s AND ano=%s AND mes=%s AND codigo=%s",
                              (eq_id, ano, i + 1, cod), fetch="none")
        else:
            await ajard_query(
                """INSERT INTO manutencao.parametros_manuais (equipamento_id, ano, mes, codigo, nome, valor, usuario_id, atualizado_em)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT (equipamento_id, ano, mes, codigo)
                   DO UPDATE SET valor=EXCLUDED.valor, nome=COALESCE(EXCLUDED.nome, manutencao.parametros_manuais.nome),
                                 usuario_id=EXCLUDED.usuario_id, atualizado_em=now()""",
                (eq_id, ano, i + 1, cod, nome, val, uid), fetch="none")
    return {"ok": True, "codigo": cod}


@router.delete("/manutencao/api/equipamentos/{eq_id}/parametros-manuais/{codigo}")
async def param_manuais_excluir(eq_id: str, codigo: str, ano: int, _auth=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    await ajard_query("DELETE FROM manutencao.parametros_manuais WHERE equipamento_id=%s AND ano=%s AND codigo=%s",
                      (eq_id, ano, codigo.upper()), fetch="none")
    return {"ok": True}


# (18/10/2026) Categorias dos documentos do objecto — a "pasta" da máquina.
DOC_CATEGORIAS = [("nota_fiscal", "Nota fiscal"), ("crlv", "CRLV / licenciamento"), ("seguro", "Seguro"),
                  ("contrato", "Contrato / financiamento"), ("manual", "Manual / catálogo"),
                  ("laudo", "Laudo / inspeção"), ("foto_servico", "Foto do serviço"), ("outro", "Outro")]
_DOC_CAT_OK = {c for c, _ in DOC_CATEGORIAS}


@router.get("/manutencao/api/documentos-categorias")
async def docs_categorias(_auth=Depends(verificar_manutencao)):
    return [{"id": c, "label": l} for c, l in DOC_CATEGORIAS]


async def _doc_guardar(eq_id, ficheiro: UploadFile, descricao, categoria, uid, ot_id=None):
    """Upload de um ficheiro do objecto (até 15 MB) para o Storage + linha em equipamento_documentos."""
    conteudo = await ficheiro.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    if len(conteudo) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo acima de 15 MB")
    import uuid as _uuid, re as _re
    nome = (ficheiro.filename or "ficheiro").strip()
    seguro = _re.sub(r"[^A-Za-z0-9._-]+", "_", nome)[:80]
    ctype = ficheiro.content_type or "application/octet-stream"
    cat = (categoria or "").strip().lower()
    if cat and cat not in _DOC_CAT_OK:
        cat = "outro"
    path = storage_upload(conteudo, f"manutencao/equipamentos/{eq_id}/docs/{_uuid.uuid4().hex}_{seguro}", content_type=ctype)
    row = await ajard_query_id(
        """INSERT INTO manutencao.equipamento_documentos (equipamento_id, ot_id, nome, path, content_type, tamanho, descricao, categoria, usuario_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (eq_id, ot_id, nome, path, ctype, len(conteudo), (descricao or "").strip() or None, cat or None, uid))
    return row, path


@router.get("/manutencao/api/equipamentos/{eq_id}/documentos")
async def docs_listar(eq_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_ficha_cols()
    rows = await ajard_query(
        """SELECT d.id, d.nome, d.path, d.content_type, d.tamanho, d.descricao, d.categoria, d.criado_em,
                  u.nome AS usuario_nome, o.numero AS ot_numero
           FROM manutencao.equipamento_documentos d
           LEFT JOIN public.usuarios_garra u ON u.id = d.usuario_id
           LEFT JOIN manutencao.ot o ON o.id = d.ot_id
           WHERE d.equipamento_id=%s AND d.ativo=true ORDER BY d.criado_em DESC""", (eq_id,)) or []
    urls = storage_urls([r["path"] for r in rows])
    out = []
    for r in rows:
        out.append({"id": str(r["id"]), "nome": r["nome"], "content_type": r["content_type"],
                    "tamanho": r["tamanho"], "descricao": r["descricao"], "categoria": r["categoria"],
                    "ot_numero": r["ot_numero"],
                    "criado_em": r["criado_em"].isoformat() if r["criado_em"] else None,
                    "usuario_nome": r["usuario_nome"], "url": urls.get(r["path"], "")})
    return out


@router.post("/manutencao/api/equipamentos/{eq_id}/documentos")
async def docs_enviar(eq_id: str, ficheiro: UploadFile = File(...), descricao: str = Form(None), categoria: str = Form(None),
                      payload=Depends(verificar_manutencao)):
    # (18/10/2026) descricao/categoria vêm no multipart → precisam de Form(); como query simples chegavam vazios.
    """Anexo 'Ficheiro' do objecto: manual, nota fiscal, laudo, CRLV… (PDF,
    imagem, planilha — até 15 MB). Vai ao bucket garra-fotos em
    manutencao/equipamentos/{id}/docs/."""
    await _garantir_ficha_cols()
    eq = await ajard_query("SELECT id FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    uid = await _usuario_id(payload)
    row, path = await _doc_guardar(eq_id, ficheiro, descricao, categoria, uid)
    return {"ok": True, "id": str(row["id"]) if row else None, "url": storage_url(path)}


@router.post("/manutencao/api/equipamentos/{eq_id}/notas-anexo")
async def criar_nota_anexo(eq_id: str, descricao: str = Form(None), categoria: str = Form(None),
                           ficheiro: UploadFile = File(None), payload=Depends(verificar_manutencao)):
    """(18/10/2026) Nova Nota no padrão ManWinWin: descrição + ficheiro opcional.
    O ficheiro entra na pasta do objecto (equipamento_documentos, com categoria)
    e a nota aponta para ele — a aba Notas mostra o 📎 e o acervo lista o documento."""
    await _garantir_ficha_cols()
    descricao = (descricao or "").strip()
    tem_arq = ficheiro is not None and bool(ficheiro.filename)
    if not descricao and not tem_arq:
        raise HTTPException(status_code=400, detail="Escreva a nota ou anexe um ficheiro")
    eq = await ajard_query("SELECT id FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    uid = await _usuario_id(payload)
    doc_id = None
    if tem_arq:
        row, _ = await _doc_guardar(eq_id, ficheiro, descricao, categoria, uid)
        doc_id = row["id"] if row else None
        if not descricao:
            descricao = f"📎 {ficheiro.filename}"
    nota = await ajard_query_id(
        "INSERT INTO manutencao.equipamento_notas (equipamento_id, usuario_id, descricao, documento_id) VALUES (%s,%s,%s,%s)",
        (eq_id, uid, descricao, doc_id))
    return {"ok": True, "id": str(nota["id"]) if nota else None, "documento_id": str(doc_id) if doc_id else None}


@router.delete("/manutencao/api/documentos/{doc_id}")
async def docs_excluir(doc_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    await ajard_query("UPDATE manutencao.equipamento_documentos SET ativo=false WHERE id=%s", (doc_id,), fetch="none")
    return {"ok": True}


# ── MOTOR GENÉRICO DE TABELAS (01/09/2026) ─────────────────────────────
# Um CRUD só para os cadastros "de apoio" do ManWinWin que faltavam:
# contratos, projetos, guias de transporte, substitutos, referências de
# fornecedor, empresa, config, e os registros da OT (outros, tarefas,
# ferramentas, leituras, documentos). Colunas em whitelist (paridade
# EDITÁVEIS × UPDATE por construção), soft delete, DDL idempotente.
# ── NÚCLEO DO SCHEMA (02/09/2026) ───────────────────────────────────────
# As 6 tabelas que vieram do SQL da migração inicial e não nasciam do código:
# ot, ot_historico, planos, pontos_controle, estoque, movimentacoes.
# Estrutura espelhada da produção (information_schema em 02/09/2026).
# Com isto o schema manutencao INTEIRO nasce de subir o servidor — banco
# zerado (dev-novo) e Reset from parent deixam de depender de SQL externo.
_NUCLEO_OK = False


async def garantir_nucleo():
    global _NUCLEO_OK
    if _NUCLEO_OK:
        return
    await ajard_query("CREATE SCHEMA IF NOT EXISTS manutencao", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.ot (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            numero TEXT, ano INT, sequencia INT,
            equipamento_id UUID NOT NULL,
            tipo TEXT DEFAULT 'corretiva', prioridade TEXT DEFAULT 'media', status TEXT DEFAULT 'aberta',
            descricao TEXT NOT NULL,
            solicitante_id UUID, responsavel_id UUID, fornecedor_id UUID,
            horimetro_na_abertura NUMERIC, custo_total NUMERIC,
            data_abertura TIMESTAMPTZ DEFAULT now(), data_conclusao TIMESTAMPTZ,
            observacao_conclusao TEXT, ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now(), atualizado_em TIMESTAMPTZ,
            numero_nf TEXT, valor_servico NUMERIC, data_nf DATE,
            horas_parada NUMERIC, data_retorno_operacao DATE,
            enviado_por UUID, enviado_em TIMESTAMPTZ,
            ot_origem_id UUID, ot_proxima_id UUID, pedido_id UUID,
            tipo_trabalho TEXT, interventor TEXT,
            data_prevista DATE, horimetro_previsto NUMERIC, plano_id UUID,
            origem TEXT DEFAULT 'garra', sintoma_codigo TEXT, causa_codigo TEXT,
            projecto_id UUID)""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.ot_historico (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ot_id UUID NOT NULL, status_de TEXT, status_para TEXT,
            observacao TEXT, usuario_id UUID, criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.planos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL, codigo TEXT NOT NULL, descricao TEXT NOT NULL,
            procedimento TEXT, tipo_trabalho TEXT,
            periodo_codigo TEXT, periodo_qtd NUMERIC, tempo_horas NUMERIC,
            hh_previsto NUMERIC, custo_previsto NUMERIC, plano_proximo_codigo TEXT,
            ativo BOOLEAN DEFAULT true, criado_em TIMESTAMPTZ DEFAULT now(),
            preparacao_codigo TEXT, mao_obra JSONB, pecas JSONB, outros JSONB, ferramentas JSONB)""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.pontos_controle (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL, codigo TEXT NOT NULL,
            leitura_atual NUMERIC, data_leitura TIMESTAMPTZ,
            limiar_atencao NUMERIC, limiar_urgente NUMERIC, limiar_maximo NUMERIC,
            ativo BOOLEAN DEFAULT true,
            UNIQUE (equipamento_id, codigo))""", fetch="none")
    # (11/10/2026) Pontos de leitura por objeto (janela própria): descrição, unidade e histórico de leituras
    await ajard_query("""ALTER TABLE manutencao.pontos_controle
        ADD COLUMN IF NOT EXISTS descricao TEXT, ADD COLUMN IF NOT EXISTS unidade TEXT,
        ADD COLUMN IF NOT EXISTS observacao TEXT""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.pontos_leituras (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ponto_id UUID NOT NULL, leitura NUMERIC NOT NULL, data TIMESTAMPTZ DEFAULT now(),
            usuario_id UUID, observacao TEXT, criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.estoque (
            peca_id UUID NOT NULL, almoxarifado_id UUID NOT NULL,
            quantidade NUMERIC DEFAULT 0, minimo NUMERIC, localizacao TEXT, maximo NUMERIC,
            PRIMARY KEY (peca_id, almoxarifado_id))""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.movimentacoes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tipo TEXT NOT NULL, peca_id UUID NOT NULL,
            almox_origem UUID, almox_destino UUID, quantidade NUMERIC NOT NULL,
            ot_id UUID, usuario_id UUID, observacao TEXT,
            criado_em TIMESTAMPTZ DEFAULT now(), custo_unitario NUMERIC,
            documento_tipo TEXT, documento_numero TEXT, fornecedor_id UUID, oc_numero TEXT)""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_ot_equip ON manutencao.ot (equipamento_id)", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_ot_status ON manutencao.ot (status) WHERE ativo=true", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_othist_ot ON manutencao.ot_historico (ot_id)", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_mov_peca ON manutencao.movimentacoes (peca_id)", fetch="none")
    await ajard_query("ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS rubrica TEXT", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_mov_ot ON manutencao.movimentacoes (ot_id)", fetch="none")
    _NUCLEO_OK = True


_TABELAS = {
    "contratos": ("manutencao.contratos", {
        "numero": "TEXT", "fornecedor_id": "UUID", "objeto": "TEXT", "data_inicio": "DATE", "data_fim": "DATE",
        "valor": "NUMERIC(14,2)", "condicao_pagamento": "TEXT", "observacao": "TEXT", "status": "TEXT"}),
    "projectos": ("manutencao.projectos", {
        "codigo": "TEXT", "nome": "TEXT", "descricao": "TEXT", "data_inicio": "DATE", "data_fim": "DATE",
        "status": "TEXT", "responsavel_id": "UUID"}),
    "guias-transporte": ("manutencao.guias_transporte", {
        "numero": "TEXT", "equipamento_id": "UUID", "peca_codigo": "TEXT", "fornecedor_id": "UUID", "ot_id": "UUID",
        "data_saida": "DATE", "data_retorno_prevista": "DATE", "data_retorno": "DATE", "motivo": "TEXT",
        "transportadora": "TEXT", "status": "TEXT", "observacao": "TEXT"}),
    "substitutos": ("manutencao.peca_substitutos", {
        "peca_codigo": "TEXT", "substituto_codigo": "TEXT", "observacao": "TEXT"}),
    "referencias-fornecedor": ("manutencao.peca_fornecedores", {
        "peca_codigo": "TEXT", "fornecedor_id": "UUID", "referencia": "TEXT", "preco": "NUMERIC(14,2)",
        "prazo_dias": "INT", "observacao": "TEXT"}),
    "empresa": ("manutencao.empresa", {
        "razao_social": "TEXT", "nome_fantasia": "TEXT", "cnpj": "TEXT", "endereco": "TEXT", "cidade": "TEXT",
        "uf": "TEXT", "telefone": "TEXT", "email": "TEXT", "responsavel_manutencao": "TEXT"}),
    "config": ("manutencao.config", {"chave": "TEXT", "valor": "TEXT", "descricao": "TEXT"}),
    # (10/10/2026) Previsão de mão de obra da OT (Planejado × Realizado, como no ManWinWin)
    "ot-mo-previsto": ("manutencao.ot_mo_previsto", {
        "ot_id": "UUID", "funcao_codigo": "TEXT", "funcao_nome": "TEXT", "hh": "NUMERIC(8,2)",
        "custo_hh": "NUMERIC(12,2)", "rubrica": "TEXT", "observacao": "TEXT"}),
    "ot-outros": ("manutencao.ot_outros", {
        "ot_id": "UUID", "data": "DATE", "descricao": "TEXT", "fornecedor_id": "UUID", "documento": "TEXT",
        "rubrica": "TEXT", "valor": "NUMERIC(14,2)",
        "planejado": "BOOLEAN",
        "horas": "NUMERIC(8,2)"}),   # (19/10/2026) tempo do serviço externo (conta no TDR mesmo sem custo)   # (19/10/2026) true = linha do quadro Planejado (previsão); null/false = Realizado
    "ot-tarefas": ("manutencao.ot_tarefas", {
        "ot_id": "UUID", "ordem": "INT", "descricao": "TEXT", "feita": "BOOLEAN", "feita_por": "UUID",
        "feita_em": "TIMESTAMPTZ", "observacao": "TEXT"}),
    "ot-ferramentas": ("manutencao.ot_ferramentas", {
        "ot_id": "UUID", "tipo_codigo": "TEXT", "descricao": "TEXT", "quantidade": "NUMERIC(10,2)", "observacao": "TEXT"}),
    "ot-leituras": ("manutencao.ot_leituras", {
        "ot_id": "UUID", "data": "DATE", "leitura": "NUMERIC(12,1)", "momento": "TEXT", "observacao": "TEXT"}),
}
_TAB_OK = set()


async def _garantir_tabela(nome):
    if nome in _TAB_OK:
        return _TABELAS[nome]
    tab, cols = _TABELAS[nome]
    await ajard_query(f"""CREATE TABLE IF NOT EXISTS {tab} (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        ativo BOOLEAN DEFAULT true, criado_por UUID, criado_em TIMESTAMPTZ DEFAULT now(),
        atualizado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    for c, t in cols.items():
        await ajard_query(f"ALTER TABLE {tab} ADD COLUMN IF NOT EXISTS {c} {t}", fetch="none")
    _TAB_OK.add(nome)
    return _TABELAS[nome]


def _tab_val(t, v):
    if v in (None, ""):
        return None
    import datetime as _dtv
    if isinstance(v, (_dtv.date, _dtv.datetime)):
        return v
    if t.startswith("NUMERIC") or t == "INT":
        txt = str(v).strip().replace("R$", "").replace(" ", "")
        if "," in txt:                      # pt-BR: 1.500,50 → 1500.50
            txt = txt.replace(".", "").replace(",", ".")
        try:
            return float(txt) if t.startswith("NUMERIC") else int(float(txt))
        except ValueError:
            return None
    if t == "BOOLEAN":
        return bool(v) and str(v).lower() not in ("false", "0", "nao", "não")
    return str(v).strip() or None


def _tab_row(r):
    d = {}
    for k, v in dict(r).items():
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        elif hasattr(v, "hex") and not isinstance(v, (str, bytes)):
            v = str(v)
        elif type(v).__name__ == "Decimal":
            v = float(v)
        d[k] = v
    return d


@router.get("/manutencao/api/tab/{nome}")
async def tab_listar(nome: str, request: Request, _auth=Depends(verificar_manutencao)):
    """Filtros por query: qualquer coluna da whitelist (igualdade) + busca=
    (ILIKE nas colunas TEXT) + inativos=1."""
    if nome not in _TABELAS:
        raise HTTPException(status_code=404, detail="Tabela desconhecida")
    tab, cols = await _garantir_tabela(nome)
    q = dict(request.query_params)
    where, params = [], []
    if not q.get("inativos"):
        where.append("t.ativo=true")
    for c, t in cols.items():
        if c in q and q[c] != "":
            where.append(f"t.{c}=%s"); params.append(_tab_val(t, q[c]))
    if q.get("busca"):
        txt = [c for c, t in cols.items() if t == "TEXT"]
        if txt:
            where.append("(" + " OR ".join(f"t.{c} ILIKE %s" for c in txt) + ")")
            params += [f"%{q['busca']}%"] * len(txt)
    extra = ""
    if "fornecedor_id" in cols:
        extra += ", (SELECT nome FROM public.fornecedores f WHERE f.id=t.fornecedor_id) AS fornecedor_nome"
    if "equipamento_id" in cols:
        extra += ", (SELECT codigo FROM operacional.equipamentos e WHERE e.id=t.equipamento_id) AS equipamento_codigo"
    if "responsavel_id" in cols:
        extra += ", (SELECT nome FROM public.usuarios_garra u WHERE u.id=t.responsavel_id) AS responsavel_nome"
    if "feita_por" in cols:
        extra += ", (SELECT nome FROM public.usuarios_garra u WHERE u.id=t.feita_por) AS feita_por_nome"
    if "ot_id" in cols and nome == "guias-transporte":
        extra += ", (SELECT numero FROM manutencao.ot o WHERE o.id=t.ot_id) AS ot_numero"
    ordem = "t.ordem, t.criado_em" if "ordem" in cols else ("t.data DESC, t.criado_em DESC" if "data" in cols else "t.criado_em DESC")
    rows = await ajard_query(
        f"SELECT t.*{extra} FROM {tab} t" + (" WHERE " + " AND ".join(where) if where else "") + f" ORDER BY {ordem} LIMIT 500",
        tuple(params)) or []
    return [_tab_row(r) for r in rows]


@router.post("/manutencao/api/tab/{nome}")
async def tab_criar(nome: str, request: Request, payload=Depends(verificar_manutencao)):
    if nome not in _TABELAS:
        raise HTTPException(status_code=404, detail="Tabela desconhecida")
    tab, cols = await _garantir_tabela(nome)
    d = await request.json()
    campos, vals = [], []
    for c, t in cols.items():
        if c in d:
            campos.append(c); vals.append(_tab_val(t, d[c]))
    if not campos:
        raise HTTPException(status_code=400, detail="Nada para gravar")
    # unicidades naturais
    for chave in ("numero", "codigo", "chave"):
        if chave in cols and d.get(chave):
            ex = await ajard_query(f"SELECT 1 FROM {tab} WHERE {chave}=%s AND ativo=true", (str(d[chave]).strip(),), fetch="one")
            if ex:
                raise HTTPException(status_code=409, detail=f"Já existe {chave} {d[chave]}")
    if nome == "empresa":
        ex = await ajard_query(f"SELECT id FROM {tab} WHERE ativo=true LIMIT 1", fetch="one")
        if ex:  # empresa é linha única: POST vira PATCH
            sets = ", ".join(f"{c}=%s" for c in campos)
            await ajard_query(f"UPDATE {tab} SET {sets}, atualizado_em=now() WHERE id=%s", tuple(vals) + (ex["id"],), fetch="none")
            return {"ok": True, "id": str(ex["id"])}
    uid = await _usuario_id(payload)
    campos.append("criado_por"); vals.append(uid)
    row = await ajard_query_id(
        f"INSERT INTO {tab} ({', '.join(campos)}) VALUES ({', '.join(['%s'] * len(campos))})", tuple(vals))
    if nome == "ot-leituras" and (d.get("momento") or "") == "fim" and _tab_val("NUMERIC", d.get("leitura")):
        # leitura de fim de serviço atualiza o contador do objeto — nunca recua
        leit = _tab_val("NUMERIC", d.get("leitura"))
        await ajard_query(
            """UPDATE operacional.equipamentos e SET
                 horimetro_atual = CASE WHEN COALESCE(e.medicao,'horimetro') <> 'km' THEN GREATEST(COALESCE(e.horimetro_atual,0), %s) ELSE e.horimetro_atual END,
                 km_atual = CASE WHEN e.medicao = 'km' THEN GREATEST(COALESCE(e.km_atual,0), %s) ELSE e.km_atual END
               WHERE e.id = (SELECT equipamento_id FROM manutencao.ot WHERE id=%s)""",
            (leit, leit, d.get("ot_id")), fetch="none")
    return {"ok": True, "id": str(row["id"]) if row else None}


@router.patch("/manutencao/api/tab/{nome}/{rid}")
async def tab_editar(nome: str, rid: str, request: Request, _auth=Depends(verificar_manutencao)):
    if nome not in _TABELAS:
        raise HTTPException(status_code=404, detail="Tabela desconhecida")
    tab, cols = await _garantir_tabela(nome)
    d = await request.json()
    if nome == "ot-tarefas" and "feita" in d:
        # quem marcou e quando vêm do token, nunca do cliente
        import datetime as _dtm
        d["feita_por"] = await _usuario_id(_auth) if d["feita"] else None
        d["feita_em"] = _dtm.datetime.now(_dtm.timezone.utc) if d["feita"] else None
    sets, vals = [], []
    for c, t in cols.items():
        if c in d:
            sets.append(f"{c}=%s"); vals.append(_tab_val(t, d[c]))
    if "ativo" in d:
        sets.append("ativo=%s"); vals.append(bool(d["ativo"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada para alterar")
    await ajard_query(f"UPDATE {tab} SET {', '.join(sets)}, atualizado_em=now() WHERE id=%s", tuple(vals) + (rid,), fetch="none")
    return {"ok": True}


@router.delete("/manutencao/api/tab/{nome}/{rid}")
async def tab_excluir(nome: str, rid: str, _auth=Depends(verificar_manutencao)):
    if nome not in _TABELAS:
        raise HTTPException(status_code=404, detail="Tabela desconhecida")
    tab, _ = await _garantir_tabela(nome)
    await ajard_query(f"UPDATE {tab} SET ativo=false, atualizado_em=now() WHERE id=%s", (rid,), fetch="none")
    return {"ok": True}


@router.post("/manutencao/api/ots/{ot_id}/aplicar-fmp")
async def ot_aplicar_fmp(ot_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(23/09/2026) Botão FMP da Origem (ManWinWin): a OT assume da ficha a descrição,
    o tipo de trabalho (A1…), Preventiva, TDM, e a programação (próxima data/leitura
    das previsões). As tarefas entram pela rota /tarefas/da-fmp que o front chama em seguida."""
    d = await request.json()
    pid = (d.get("plano_id") or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="Informe a FMP")
    await _garantir_planos_cols()
    p = await ajard_query("SELECT * FROM manutencao.planos WHERE id=%s AND ativo=true", (pid,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="FMP não encontrada")
    o = await ajard_query("SELECT id, equipamento_id, status, plano_id, prog_alterada, data_prevista, horimetro_previsto FROM manutencao.ot WHERE id=%s AND ativo=true", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    if str(o["equipamento_id"]) != str(p["equipamento_id"]):
        raise HTTPException(status_code=400, detail="A FMP é de outro equipamento")
    prev = await _calcular_previsoes()
    it = next((i for i in prev["itens"] if str(i["plano_id"]) == str(pid)), None)
    # (10/10/2026) Programação alterada à mão (Avançado) na MESMA FMP não é sobreposta pelo recálculo
    manter = bool(o.get("prog_alterada")) and str(o.get("plano_id") or "") == str(pid)
    if manter:
        nova_data, novo_hor = o["data_prevista"], o["horimetro_previsto"]
    else:
        nova_data, novo_hor = (it or {}).get("proxima_data"), (it or {}).get("leitura_alvo")
    await ajard_query(
        """UPDATE manutencao.ot SET plano_id=%s, descricao=%s, tipo='preventiva', tipo_trabalho=%s,
               data_prevista=COALESCE(%s, data_prevista), horimetro_previsto=COALESCE(%s, horimetro_previsto),
               prog_alterada=%s, atualizado_em=now()
           WHERE id=%s""",
        (pid, p["descricao"], p["tipo_trabalho"], nova_data, novo_hor, manter, ot_id), fetch="none")
    return {"ok": True, "descricao": p["descricao"], "tipo_trabalho": p["tipo_trabalho"], "prog_alterada": manter,
            "data_prevista": (nova_data.isoformat() if hasattr(nova_data, "isoformat") else nova_data),
            "horimetro_previsto": (float(novo_hor) if novo_hor is not None else None)}


@router.post("/manutencao/api/ots/{ot_id}/tarefas/da-fmp")
async def ot_tarefas_da_fmp(ot_id: str, _auth=Depends(verificar_manutencao)):
    """Copia as tarefas do plano (FMP) da OT para o checklist da OT — uma
    linha por linha de texto do plano. Só se a OT ainda não tem tarefas."""
    await _garantir_tabela("ot-tarefas")
    o = await ajard_query("SELECT plano_id, descricao FROM manutencao.ot WHERE id=%s", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    ja = await ajard_query("SELECT COUNT(*)::int AS n FROM manutencao.ot_tarefas WHERE ot_id=%s AND ativo=true", (ot_id,), fetch="one")
    if ja and ja["n"]:
        raise HTTPException(status_code=409, detail="A OT já tem tarefas")
    texto = ""
    if o.get("plano_id"):
        # tarefas da FMP: procedimento do plano; sem ele, as tarefas do modelo da biblioteca
        pl = await ajard_query("SELECT procedimento, preparacao_codigo FROM manutencao.planos WHERE id=%s", (o["plano_id"],), fetch="one")
        texto = (pl or {}).get("procedimento") or ""
        if not texto.strip() and (pl or {}).get("preparacao_codigo"):
            bib = await ajard_query("SELECT tarefas FROM manutencao.biblioteca_preparacoes WHERE codigo=%s", (pl["preparacao_codigo"],), fetch="one")
            texto = (bib or {}).get("tarefas") or ""
    linhas = [l.strip(" -•*\t") for l in (texto or "").splitlines() if l.strip(" -•*\t")]
    if not linhas:
        linhas = [o.get("descricao") or "Executar o serviço descrito na OT"]
    for i, l in enumerate(linhas, 1):
        await ajard_query("INSERT INTO manutencao.ot_tarefas (ot_id, ordem, descricao) VALUES (%s,%s,%s)", (ot_id, i, l[:300]), fetch="none")
    return {"ok": True, "n": len(linhas)}


@router.get("/manutencao/api/ots/{ot_id}/preparacao")
async def ot_preparacao(ot_id: str, _auth=Depends(verificar_manutencao)):
    """Preparação da OT = o plano (FMP) de origem com tudo que ele previa."""
    o = await ajard_query("SELECT plano_id FROM manutencao.ot WHERE id=%s", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    if not o.get("plano_id"):
        return {"plano": None}
    pl = await ajard_query("SELECT * FROM manutencao.planos WHERE id=%s", (o["plano_id"],), fetch="one")
    if not pl:
        return {"plano": None}
    d = _tab_row(pl)
    d["tdm_horas"] = d.get("tempo_horas")
    d["tarefas"] = d.get("procedimento") or ""
    if d.get("preparacao_codigo"):
        bib = await ajard_query("SELECT * FROM manutencao.biblioteca_preparacoes WHERE codigo=%s", (d["preparacao_codigo"],), fetch="one")
        if bib:
            b = _tab_row(bib)
            if not (d["tarefas"] or "").strip():
                d["tarefas"] = b.get("tarefas") or ""
            if d.get("tdm_horas") is None:
                d["tdm_horas"] = b.get("tdm_horas")
            for k in ("mao_obra", "pecas", "outros", "ferramentas", "criticidade"):
                if not d.get(k):
                    d[k] = b.get(k)
    return {"plano": d}


@router.get("/manutencao/api/ots/{ot_id}/documentos")
async def ot_docs_listar(ot_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS ot_id UUID", fetch="none")
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS categoria TEXT", fetch="none")
    rows = await ajard_query(
        """SELECT d.id, d.nome, d.path, d.content_type, d.tamanho, d.descricao, d.categoria, d.criado_em, u.nome AS usuario_nome
           FROM manutencao.equipamento_documentos d LEFT JOIN public.usuarios_garra u ON u.id = d.usuario_id
           WHERE d.ot_id=%s AND d.ativo=true ORDER BY d.criado_em DESC""", (ot_id,)) or []
    return [{"id": str(r["id"]), "nome": r["nome"], "content_type": r["content_type"], "tamanho": r["tamanho"],
             "descricao": r["descricao"], "categoria": r.get("categoria"),
             "criado_em": r["criado_em"].isoformat() if r["criado_em"] else None,
             "usuario_nome": r["usuario_nome"], "url": storage_url(r["path"])} for r in rows]


@router.post("/manutencao/api/ots/{ot_id}/documentos")
async def ot_docs_enviar(ot_id: str, ficheiro: UploadFile = File(...), descricao: str = Form(None),
                         payload=Depends(verificar_manutencao)):
    await _garantir_mao_obra()
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS ot_id UUID", fetch="none")
    o = await ajard_query("SELECT equipamento_id FROM manutencao.ot WHERE id=%s", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    conteudo = await ficheiro.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    if len(conteudo) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Arquivo acima de 15 MB")
    import uuid as _uuid, re as _re
    nome = (ficheiro.filename or "ficheiro").strip()
    seguro = _re.sub(r"[^A-Za-z0-9._-]+", "_", nome)[:80]
    ctype = ficheiro.content_type or "application/octet-stream"
    path = storage_upload(conteudo, f"manutencao/ots/{ot_id}/{_uuid.uuid4().hex}_{seguro}", content_type=ctype)
    uid = await _usuario_id(payload)
    row = await ajard_query_id(
        """INSERT INTO manutencao.equipamento_documentos (equipamento_id, ot_id, nome, path, content_type, tamanho, descricao, usuario_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (o["equipamento_id"], ot_id, nome, path, ctype, len(conteudo), (descricao or "").strip() or None, uid))
    return {"ok": True, "id": str(row["id"]) if row else None, "url": storage_url(path)}


@router.get("/manutencao/api/documentos")
async def docs_todos(busca: str = None, _auth=Depends(verificar_manutencao)):
    """Parametrização ▸ Documentos de equipamentos — acervo inteiro."""
    await _garantir_mao_obra()
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS ot_id UUID", fetch="none")
    where, params = ["d.ativo=true"], []
    if busca:
        where.append("(d.nome ILIKE %s OR d.descricao ILIKE %s OR e.codigo ILIKE %s)"); params += [f"%{busca}%"] * 3
    rows = await ajard_query(
        f"""SELECT d.id, d.nome, d.path, d.content_type, d.tamanho, d.descricao, d.criado_em, d.ot_id,
                   e.codigo AS equipamento_codigo, u.nome AS usuario_nome
            FROM manutencao.equipamento_documentos d
            LEFT JOIN operacional.equipamentos e ON e.id = d.equipamento_id
            LEFT JOIN public.usuarios_garra u ON u.id = d.usuario_id
            WHERE {' AND '.join(where)} ORDER BY d.criado_em DESC LIMIT 300""", tuple(params)) or []
    return [{"id": str(r["id"]), "nome": r["nome"], "content_type": r["content_type"], "tamanho": r["tamanho"],
             "descricao": r["descricao"], "criado_em": r["criado_em"].isoformat() if r["criado_em"] else None,
             "equipamento_codigo": r["equipamento_codigo"], "ot": bool(r["ot_id"]),
             "usuario_nome": r["usuario_nome"], "url": storage_url(r["path"])} for r in rows]


@router.patch("/manutencao/api/pecas/ativo")
async def peca_ativo(request: Request, _auth=Depends(verificar_manutencao)):
    """Desativar/reativar artigo (soft). Código no corpo (códigos com barra)."""
    d = await request.json()
    cod = (d.get("peca") or "").strip()
    p = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s", (cod,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    ativar = bool(d.get("ativo"))
    if not ativar:
        saldo = await ajard_query("SELECT COALESCE(SUM(quantidade),0) AS q FROM manutencao.estoque WHERE peca_id=%s", (p["id"],), fetch="one")
        if saldo and float(saldo["q"] or 0) > 0:
            raise HTTPException(status_code=400, detail=f"Peça com saldo em estoque ({float(saldo['q']):g}) — dê saída/ajuste antes de desativar")
    await ajard_query("UPDATE manutencao.pecas SET ativo=%s WHERE id=%s", (ativar, p["id"]), fetch="none")
    return {"ok": True, "ativo": ativar}


@router.get("/manutencao/api/analises/abc")
async def analise_abc(dias: int = 365, _auth=Depends(verificar_manutencao)):
    """Curva ABC de consumo (valor das saídas/aplicações diretas − devoluções
    no período): A = até 80 % do valor acumulado, B = até 95 %, C = resto."""
    rows = await ajard_query(
        """SELECT p.codigo, p.descricao, p.unidade, p.familia_codigo,
                  SUM(CASE WHEN m.tipo='entrada' AND m.ot_id IS NOT NULL THEN -1 ELSE 1 END
                      * m.quantidade * COALESCE(m.custo_unitario, p.custo_medio, 0)) AS valor,
                  SUM(CASE WHEN m.tipo='entrada' AND m.ot_id IS NOT NULL THEN -m.quantidade ELSE m.quantidade END) AS qtd,
                  COUNT(*)::int AS movimentos
           FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id = m.peca_id
           WHERE (m.tipo IN ('saida','aplicacao_direta') OR (m.tipo='entrada' AND m.ot_id IS NOT NULL))
             AND m.criado_em >= now() - (%s || ' days')::interval
           GROUP BY p.codigo, p.descricao, p.unidade, p.familia_codigo
           HAVING SUM(CASE WHEN m.tipo='entrada' AND m.ot_id IS NOT NULL THEN -1 ELSE 1 END
                      * m.quantidade * COALESCE(m.custo_unitario, p.custo_medio, 0)) > 0
           ORDER BY valor DESC""", (str(int(dias)),)) or []
    total = sum(float(r["valor"] or 0) for r in rows) or 0.0
    acum, out = 0.0, []
    for r in rows:
        v = float(r["valor"] or 0); acum += v
        pct = (acum / total * 100) if total else 0
        out.append({"codigo": r["codigo"], "descricao": r["descricao"], "unidade": r["unidade"],
                    "familia": r["familia_codigo"], "valor": round(v, 2), "qtd": float(r["qtd"] or 0),
                    "movimentos": r["movimentos"], "pct": round(v / total * 100, 2) if total else 0,
                    "acumulado": round(pct, 2), "classe": "A" if pct <= 80 else ("B" if pct <= 95 else "C")})
    res = {"A": 0, "B": 0, "C": 0}
    for x in out: res[x["classe"]] += 1
    return {"dias": dias, "total": round(total, 2), "n": len(out), "classes": res, "itens": out}


@router.get("/manutencao/api/analises/carga")
async def carga_trabalhos(dias: int = 30, _auth=Depends(verificar_manutencao)):
    """Carga de trabalhos por responsável: OTs em carteira (por estado) e
    HH lançadas no período."""
    await _garantir_mao_obra()
    ots = await ajard_query(
        """SELECT COALESCE(u.nome, '(sem responsável)') AS nome, o.responsavel_id, o.status, COUNT(*)::int AS n,
                  SUM(CASE WHEN o.data_prevista IS NOT NULL AND o.data_prevista < now()::date
                            AND o.status IN ('programada','aberta') THEN 1 ELSE 0 END)::int AS vencidas
           FROM manutencao.ot o LEFT JOIN public.usuarios_garra u ON u.id = o.responsavel_id
           WHERE o.ativo=true AND o.status IN ('programada','aberta','em_andamento','aguardando_peca')
           GROUP BY 1, 2, 3""") or []
    hh = await ajard_query(
        """SELECT COALESCE(u.nome, m.nome, '(sem nome)') AS nome, m.usuario_id, SUM(m.horas) AS horas,
                  SUM(m.horas * COALESCE(m.custo_hora,0)) AS custo, COUNT(DISTINCT m.ot_id)::int AS ots
           FROM manutencao.ot_mao_obra m LEFT JOIN public.usuarios_garra u ON u.id = m.usuario_id
           WHERE m.ativo=true AND m.data >= %s
           GROUP BY 1, 2""", ((__import__("datetime").date.today() - __import__("datetime").timedelta(days=int(dias))),)) or []
    pess = {}
    for r in ots:
        p = pess.setdefault(r["nome"], {"nome": r["nome"], "programada": 0, "aberta": 0, "em_andamento": 0,
                                        "aguardando_peca": 0, "vencidas": 0, "horas": 0.0, "custo": 0.0, "ots_hh": 0})
        p[r["status"]] = p.get(r["status"], 0) + int(r["n"]); p["vencidas"] += int(r["vencidas"] or 0)
    for r in hh:
        p = pess.setdefault(r["nome"], {"nome": r["nome"], "programada": 0, "aberta": 0, "em_andamento": 0,
                                        "aguardando_peca": 0, "vencidas": 0, "horas": 0.0, "custo": 0.0, "ots_hh": 0})
        p["horas"] += float(r["horas"] or 0); p["custo"] += float(r["custo"] or 0); p["ots_hh"] += int(r["ots"] or 0)
    for p in pess.values():
        p["carteira"] = p["programada"] + p["aberta"] + p["em_andamento"] + p["aguardando_peca"]
        p["horas"] = round(p["horas"], 2); p["custo"] = round(p["custo"], 2)
    return {"dias": dias, "pessoas": sorted(pess.values(), key=lambda x: (-x["carteira"], x["nome"]))}


@router.post("/manutencao/api/estoque/inventario")
async def inventario(request: Request, payload=Depends(verificar_manutencao)):
    """Inventário (contagem física) de um almoxarifado: recebe
    {almox, itens:[{peca, contagem}], observacao}; cada diferença vira um
    movimento 'ajuste' (saldo passa a ser a contagem). Devolve o resumo."""
    d = await request.json()
    alm = (d.get("almox") or "").strip()
    a = await ajard_query("SELECT id, codigo FROM manutencao.almoxarifados WHERE codigo=%s OR id::text=%s", (alm, alm), fetch="one")
    if not a:
        raise HTTPException(status_code=404, detail="Almoxarifado não encontrado")
    uid = await _usuario_id(payload)
    obs = (d.get("observacao") or "").strip() or "Inventário"
    ajustados, iguais, erros = [], 0, []
    for it in d.get("itens") or []:
        cod = (it.get("peca") or "").strip()
        if it.get("contagem") in (None, ""):
            continue
        try:
            cont = float(str(it["contagem"]).replace(",", "."))
        except ValueError:
            erros.append(f"{cod}: contagem inválida"); continue
        p = await ajard_query("SELECT id, custo_medio FROM manutencao.pecas WHERE codigo=%s", (cod,), fetch="one")
        if not p:
            erros.append(f"{cod}: peça não encontrada"); continue
        s = await ajard_query("SELECT quantidade FROM manutencao.estoque WHERE peca_id=%s AND almoxarifado_id=%s", (p["id"], a["id"]), fetch="one")
        atual = float(s["quantidade"]) if s else 0.0
        if abs(cont - atual) < 1e-9:
            iguais += 1; continue
        await ajard_query("""INSERT INTO manutencao.estoque (peca_id, almoxarifado_id, quantidade) VALUES (%s,%s,%s)
                             ON CONFLICT (peca_id, almoxarifado_id) DO UPDATE SET quantidade = EXCLUDED.quantidade""",
                          (p["id"], a["id"], cont), fetch="none")
        await ajard_query("""INSERT INTO manutencao.movimentacoes (tipo, peca_id, almox_destino, quantidade, usuario_id, observacao, custo_unitario)
                             VALUES ('ajuste',%s,%s,%s,%s,%s,%s)""",
                          (p["id"], a["id"], cont, uid, f"{obs} · de {atual:g} para {cont:g}",
                           float(p["custo_medio"]) if p.get("custo_medio") is not None else None), fetch="none")
        ajustados.append({"peca": cod, "de": atual, "para": cont, "delta": round(cont - atual, 3)})
    return {"ok": True, "almox": a["codigo"], "ajustados": ajustados, "iguais": iguais, "erros": erros}


@router.get("/manutencao/api/equipamentos-ficha")
async def equipamentos_ficha(_auth=Depends(verificar_manutencao)):
    """Vínculos vivos da frota única para a Parametrização (operador, fornecedor)."""
    await _garantir_ficha_cols()
    rows = await ajard_query(
        """SELECT id, codigo, descricao, categoria, operador_responsavel_id, fornecedor_id, sistema_codigo, tipo_sigla, centro_custo, localizacao
           FROM operacional.equipamentos WHERE ativo=true AND COALESCE(categoria,'') <> 'apoio' ORDER BY codigo""") or []
    return [_tab_row(r) for r in rows]



_planos_cols_ok = False
async def _garantir_planos_cols():
    """planos.ativo + baseline do ciclo (ultima_data / ultima_leitura). Idempotente, uma vez por processo."""
    global _planos_cols_ok
    if _planos_cols_ok:
        return
    await ajard_query("ALTER TABLE manutencao.planos ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT true, "
                      "ADD COLUMN IF NOT EXISTS ultima_data DATE, ADD COLUMN IF NOT EXISTS ultima_leitura NUMERIC, "
                      # (10/10/2026) ManWinWin "Data Fixa" / "Registo Fixo": o próximo ciclo conta a partir da
                      # data/leitura PROGRAMADA (calendário rígido) e não da execução real (calendário flutuante)
                      "ADD COLUMN IF NOT EXISTS data_fixa BOOLEAN DEFAULT false, ADD COLUMN IF NOT EXISTS registro_fixo BOOLEAN DEFAULT false", fetch="none")
    _planos_cols_ok = True

@router.get("/manutencao/api/planos-todos")
async def planos_todos(_auth=Depends(verificar_manutencao)):
    """Parametrização ▸ Planos preventivos: todas as FMPs da frota."""
    await _garantir_planos_cols()
    rows = await ajard_query(
        """SELECT p.id, p.codigo, p.descricao, p.periodo_codigo, p.tempo_horas AS tdm_horas, p.ativo, p.equipamento_id,
                  e.codigo AS equipamento_codigo, e.descricao AS equipamento_desc
           FROM manutencao.planos p JOIN operacional.equipamentos e ON e.id = p.equipamento_id
           WHERE COALESCE(p.ativo,true)=true ORDER BY e.codigo, p.codigo""") or []
    return [_tab_row(r) for r in rows]


@router.get("/manutencao/api/equipamentos/{eq_id}/analises")
async def analises_equipamento(eq_id: str, ano: int = None, _auth=Depends(verificar_manutencao)):
    """(01/09/2026) Réplica da janela Análises do ManWinWin — Parâmetros
    Automáticos por mês. Cada parâmetro nasce de dados reais (OTs, peças
    baixadas, leituras, abastecimentos); nada é enfeite. Custo entra no mês
    da CONCLUSÃO da OT; contagem de OTs no mês da ABERTURA."""
    from datetime import date as _date
    await _garantir_colunas_ot()
    await _garantir_mao_obra()
    ano = int(ano or _date.today().year)
    eq = await ajard_query("SELECT id, codigo, descricao, medicao FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    un = "km" if (eq.get("medicao") == "km") else "h"
    P = [
        ("NOT",   "Número de OTs abertas", "n"),
        ("NOTP",  "Número de OTs preventivas", "n"),
        ("NAV",   "Número de avarias (OTs corretivas)", "n"),
        ("NOTCD", "Número de OTs concluídas", "n"),
        ("CM",    "Custo total de manutenção (R$)", "r"),
        ("CMC",   "Custo manutenção corretiva (R$)", "r"),
        ("CMP",   "Custo manutenção preventiva (R$)", "r"),
        ("CSE",   "Custo serviços externos (R$)", "r"),
        ("CPI",   "Custo peças de stock aplicadas (R$)", "r"),
        ("COU",   "Outros custos lançados na OT (R$)", "r"),
        ("TDR",   "Tempo de reparação (dias)", "d"),
        ("PDI",   "Período de indisponibilidade (dias)", "d"),
        ("RF",    f"Registro de funcionamento ({un})", "u"),
        ("HHT",   "Total HH de mão de obra (h)", "u"),
        ("HHMC",  "HH em manutenção corretiva (h)", "u"),
        ("HHMP",  "HH em manutenção preventiva (h)", "u"),
        ("CLM",   "Custo de mão de obra (R$)", "r"),
        ("CMB",   "Combustível (litros)", "u"),
        ("CCB",   "Custo combustível (R$)", "r"),
    ]
    vals = {c: [0.0] * 12 for c, _, _ in P}

    def _mes(dt):
        return (dt.month - 1) if dt and getattr(dt, "year", None) == ano else None

    ots = await ajard_query(
        """SELECT tipo, status, data_abertura, data_conclusao, data_retorno_operacao,
                  custo_total, fornecedor_id
           FROM manutencao.ot
           WHERE equipamento_id=%s AND ativo=true
             AND (EXTRACT(YEAR FROM data_abertura)=%s OR EXTRACT(YEAR FROM data_conclusao)=%s)""",
        (eq_id, ano, ano)) or []
    for o in ots:
        ma = _mes(o.get("data_abertura")); mc = _mes(o.get("data_conclusao"))
        tipo = (o.get("tipo") or "").lower()
        if ma is not None:
            vals["NOT"][ma] += 1
            if tipo == "preventiva": vals["NOTP"][ma] += 1
            if tipo == "corretiva": vals["NAV"][ma] += 1
        if mc is not None and (o.get("status") or "") == "concluida":
            vals["NOTCD"][mc] += 1
            c = float(o.get("custo_total") or 0)
            vals["CM"][mc] += c
            if tipo == "corretiva": vals["CMC"][mc] += c
            if tipo == "preventiva": vals["CMP"][mc] += c
            if o.get("fornecedor_id"): vals["CSE"][mc] += c
            da, dc = o.get("data_abertura"), o.get("data_conclusao")
            if da and dc:
                vals["TDR"][mc] += max(0.0, (dc - da).total_seconds() / 86400.0)
            dr = o.get("data_retorno_operacao")
            if tipo == "corretiva" and da and dr:
                vals["PDI"][mc] += max(0.0, (dr - da.date()).days)
    pecas = await ajard_query(
        """SELECT m.criado_em, m.tipo, m.quantidade, COALESCE(m.custo_unitario, p.custo_medio) AS custo
           FROM manutencao.movimentacoes m
           JOIN manutencao.pecas p ON p.id = m.peca_id
           JOIN manutencao.ot o ON o.id = m.ot_id
           WHERE o.equipamento_id=%s AND m.tipo IN ('saida','entrada','aplicacao_direta')
             AND EXTRACT(YEAR FROM m.criado_em)=%s""", (eq_id, ano)) or []
    for m in pecas:
        mm = _mes(m.get("criado_em"))
        if mm is None: continue
        sinal = -1 if m.get("tipo") == "entrada" else 1
        vals["CPI"][mm] += sinal * float(m.get("quantidade") or 0) * float(m.get("custo") or 0)
    await _garantir_tabela("ot-outros")
    ou = await ajard_query(
        """SELECT x.data, x.valor FROM manutencao.ot_outros x JOIN manutencao.ot o ON o.id = x.ot_id
           WHERE o.equipamento_id=%s AND x.ativo=true AND EXTRACT(YEAR FROM x.data)=%s""", (eq_id, ano)) or []
    for x in ou:
        mm = _mes(x.get("data"))
        if mm is not None: vals["COU"][mm] += float(x.get("valor") or 0)
    mo = await ajard_query(
        """SELECT m.data, m.horas, m.custo_hora, o.tipo
           FROM manutencao.ot_mao_obra m JOIN manutencao.ot o ON o.id = m.ot_id
           WHERE o.equipamento_id=%s AND m.ativo=true AND EXTRACT(YEAR FROM m.data)=%s""", (eq_id, ano)) or []
    for m in mo:
        mm = _mes(m.get("data"))
        if mm is None: continue
        h = float(m.get("horas") or 0); tipo = (m.get("tipo") or "").lower()
        vals["HHT"][mm] += h
        if tipo == "corretiva": vals["HHMC"][mm] += h
        if tipo == "preventiva": vals["HHMP"][mm] += h
        if m.get("custo_hora") is not None: vals["CLM"][mm] += h * float(m["custo_hora"])
    try:
        leit = await ajard_query(
            """SELECT EXTRACT(MONTH FROM data)::int AS m, MAX(leitura) AS mx, MIN(leitura) AS mn, COUNT(*)::int AS n
               FROM operacional.v_leituras
               WHERE equipamento_id=%s AND EXTRACT(YEAR FROM data)=%s AND leitura > 0
               GROUP BY 1""", (eq_id, ano)) or []
        for r in leit:
            if int(r["n"]) >= 2:
                vals["RF"][int(r["m"]) - 1] = float(r["mx"]) - float(r["mn"])
    except Exception:
        pass
    try:
        ab = await ajard_query(
            """SELECT EXTRACT(MONTH FROM data)::int AS m, SUM(litros) AS l, SUM(valor_total) AS v
               FROM operacional.abastecimentos
               WHERE equipamento_id=%s AND ativo=true AND COALESCE(destino_tipo,'equipamento')='equipamento'
                 AND EXTRACT(YEAR FROM data)=%s
               GROUP BY 1""", (eq_id, ano)) or []
        for r in ab:
            vals["CMB"][int(r["m"]) - 1] = float(r["l"] or 0)
            vals["CCB"][int(r["m"]) - 1] = float(r["v"] or 0)
    except Exception:
        pass
    anos = await ajard_query(
        """SELECT DISTINCT EXTRACT(YEAR FROM data_abertura)::int AS a FROM manutencao.ot
           WHERE equipamento_id=%s AND ativo=true ORDER BY 1 DESC""", (eq_id,)) or []
    lista_anos = sorted({int(r["a"]) for r in anos if r.get("a")} | {ano, _date.today().year}, reverse=True)
    manuais = await param_manuais_listar(eq_id, ano, _auth)
    return {"equipamento": eq["codigo"], "descricao": eq.get("descricao"), "ano": ano, "anos": lista_anos,
            "medicao": un,
            "parametros": [{"codigo": c, "nome": n, "fmt": f, "meses": [round(x, 2) for x in vals[c]],
                            "total": round(sum(vals[c]), 2)} for c, n, f in P],
            "manuais": manuais}


@router.post("/manutencao/api/equipamentos/{eq_id}/acerto-horimetro")
async def acerto_horimetro(eq_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(18/09/2026) Acerto do contador de MANUTENÇÃO (ManWinWin ▸ Registos ▸ Acerto):
    única forma de baixar uma leitura (troca de horímetro físico, contaminação por OS de
    cliente, erro de digitação). Gestão, motivo obrigatório, fica no diário do objecto."""
    d = await request.json()
    try:
        novo = float(str(d.get("valor")).replace(",", "."))
    except Exception:
        raise HTTPException(status_code=400, detail="Informe o valor do acerto")
    motivo = (d.get("motivo") or "").strip()
    if len(motivo) < 5:
        raise HTTPException(status_code=400, detail="Motivo do acerto é obrigatório")
    eq = await ajard_query("SELECT codigo, medicao, horimetro_atual, km_atual FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    km = (eq.get("medicao") == "km")
    antes = float((eq["km_atual"] if km else eq["horimetro_atual"]) or 0)
    await ajard_query(f"UPDATE operacional.equipamentos SET {'km_atual' if km else 'horimetro_atual'}=%s, atualizado_em=now() WHERE id=%s",
                      (novo, eq_id), fetch="none")
    uid = await _usuario_id(payload)
    await ajard_query(
        "INSERT INTO manutencao.equipamento_notas (equipamento_id, usuario_id, descricao) VALUES (%s,%s,%s)",
        (eq_id, uid, f"⚙ Acerto de {'km' if km else 'horímetro'} (manutenção): {antes:g} → {novo:g}. Motivo: {motivo}"), fetch="none")
    return {"ok": True, "antes": antes, "depois": novo}


@router.delete("/manutencao/api/equipamentos/{eq_id}/foto")
async def foto_equipamento_excluir(eq_id: str, payload=Depends(verificar_manutencao)):
    """(14/09/2026) Remove a figura do objecto: apaga do Storage e limpa foto_path."""
    eq = await ajard_query("SELECT id, foto_path FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    if eq.get("foto_path"):
        try:
            storage_delete([eq["foto_path"]])
        except Exception:
            pass
    await ajard_query("UPDATE operacional.equipamentos SET foto_path=NULL WHERE id=%s", (eq_id,), fetch="none")
    return {"ok": True}

@router.post("/manutencao/api/equipamentos/{eq_id}/foto")
async def foto_equipamento(eq_id: str, foto: UploadFile = File(...), payload=Depends(verificar_manutencao)):
    """Figura do equipamento — Supabase Storage (bucket garra-fotos,
    pasta manutencao/equipamentos). Troca substitui e apaga a anterior."""
    await _garantir_ficha_cols()
    eq = await ajard_query("SELECT id, foto_path FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    conteudo = await foto.read()
    if not conteudo:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    import uuid as _uuid
    dados = comprimir_imagem(conteudo)
    path = storage_upload(dados, f"manutencao/equipamentos/{eq_id}/{_uuid.uuid4().hex}.jpg")
    if eq.get("foto_path"):
        try:
            storage_delete([eq["foto_path"]])
        except Exception:
            pass
    await ajard_query("UPDATE operacional.equipamentos SET foto_path=%s WHERE id=%s", (path, eq_id), fetch="none")
    return {"ok": True, "foto_url": storage_url(path)}


@router.post("/manutencao/api/equipamentos/{eq_id}/notas")
async def criar_nota(eq_id: str, request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_ficha_cols()
    d = await request.json()
    descricao = (d.get("descricao") or "").strip()
    if not descricao:
        raise HTTPException(status_code=400, detail="Escreva a nota")
    uid = await _usuario_id(payload)
    row = await ajard_query_id(
        "INSERT INTO manutencao.equipamento_notas (equipamento_id, usuario_id, descricao) VALUES (%s,%s,%s)",
        (eq_id, uid, descricao))
    return dict(row)


@router.delete("/manutencao/api/equipamentos/{eq_id}/notas/{nota_id}")
async def excluir_nota(eq_id: str, nota_id: str, payload=Depends(verificar_manutencao)):
    r = await ajard_query(
        "UPDATE manutencao.equipamento_notas SET ativo=false WHERE id=%s AND equipamento_id=%s RETURNING id",
        (nota_id, eq_id), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Nota não encontrada")
    return {"ok": True}


@router.get("/manutencao/api/equipamentos/{eq_id}/ritmo")
async def ritmo_equipamento(eq_id: str, _auth=Depends(verificar_manutencao)):
    """(24/08→24/09/2026) Ritmo de uso — ficha do Objecto (FMD / Data) e reprogramação.
    media_h_dia = FMD-R dos registos (v_leituras: partes + checklists + abastecimentos,
    com correções; JANELA_FMD_DIAS; _fmd_serie). fmd_em_uso = manual da ficha se
    desmarcado 'Calcular por registos', senão o calculado — o mesmo das previsões."""
    await _garantir_leituras()
    rows = await ajard_query(
        """SELECT data::date AS d, MAX(leitura) AS leitura
           FROM operacional.v_leituras
           WHERE equipamento_id=%s AND data >= now() - make_interval(days => %s) AND leitura > 0
           GROUP BY data::date""", (eq_id, JANELA_FMD_DIAS)) or []
    serie = sorted((r["d"], float(r["leitura"])) for r in rows)
    ult = serie[-1] if serie else None
    calc = _fmd_serie(serie)
    manual = (await _fmd_manual(eq_id)).get(str(eq_id))
    return {"media_h_dia": calc, "fmd_manual": manual, "fmd_em_uso": manual or calc,
            "janela_dias": JANELA_FMD_DIAS, "dias_com_uso": len(serie),
            "ultima_data": ult[0].isoformat() if ult else None,
            "ultima_leitura": ult[1] if ult else None,
            "avanco": round(serie[-1][1] - serie[0][1], 1) if len(serie) > 1 else 0}


@router.get("/manutencao/api/equipamentos/{eq_id}/detalhe")
async def detalhe_equipamento(eq_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_planos_cols()
    await _garantir_ficha_cols()
    eq = await ajard_query(
        """SELECT e.*, p.codigo AS pai_codigo,
                  (SELECT COUNT(*)::int FROM operacional.equipamentos f
                    WHERE f.equipamento_pai = e.id) AS filhos,
                  (SELECT u.nome FROM public.usuarios_garra u WHERE u.id = e.operador_responsavel_id) AS operador_nome,
                  (SELECT fo.nome FROM public.fornecedores fo WHERE fo.id = e.fornecedor_id) AS fornecedor_nome
           FROM operacional.equipamentos e
           LEFT JOIN operacional.equipamentos p ON p.id = e.equipamento_pai
           WHERE e.id=%s""", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    pontos = await ajard_query(
        """SELECT codigo, leitura_atual, data_leitura, limiar_atencao, limiar_urgente, limiar_maximo
           FROM manutencao.pontos_controle WHERE equipamento_id=%s AND ativo=true ORDER BY codigo""", (eq_id,))
    await _garantir_biblioteca()
    planos = await ajard_query(
        """SELECT id, codigo, descricao, tipo_trabalho, periodo_codigo, periodo_qtd, tempo_horas, hh_previsto, custo_previsto, procedimento, plano_proximo_codigo,
                  mao_obra, pecas, outros, ferramentas, ultima_data, ultima_leitura, preparacao_codigo,
                  COALESCE(data_fixa,false) AS data_fixa, COALESCE(registro_fixo,false) AS registro_fixo
           FROM manutencao.planos WHERE equipamento_id=%s AND ativo=true ORDER BY codigo""", (eq_id,))
    ots = await ajard_query(
        """SELECT numero, tipo, prioridade, status, descricao, data_abertura, data_conclusao, custo_total
           FROM manutencao.ot WHERE equipamento_id=%s AND ativo=true
           ORDER BY data_abertura DESC LIMIT 25""", (eq_id,))
    filhos = await ajard_query(
        """SELECT codigo, descricao, posicao FROM operacional.equipamentos
           WHERE equipamento_pai=%s ORDER BY codigo""", (eq_id,))
    notas = await ajard_query(
        """SELECT n.id, n.data_nota, n.descricao, u.nome AS usuario_nome,
                  d.id AS documento_id, d.nome AS doc_nome, d.path AS doc_path, d.categoria AS doc_categoria, d.content_type AS doc_tipo
           FROM manutencao.equipamento_notas n
           LEFT JOIN public.usuarios_garra u ON u.id = n.usuario_id
           LEFT JOIN manutencao.equipamento_documentos d ON d.id = n.documento_id AND d.ativo=true
           WHERE n.equipamento_id=%s AND n.ativo=true
           ORDER BY n.data_nota DESC""", (eq_id,))
    _urls = storage_urls([x["doc_path"] for x in notas if x.get("doc_path")])
    for x in notas:
        x["doc_url"] = _urls.get(x["doc_path"], "") if x.get("doc_path") else None
        x.pop("doc_path", None)
    oper = None
    if eq.get("operador_responsavel_id"):
        o = await ajard_query("SELECT nome FROM public.usuarios_garra WHERE id=%s",
                              (eq["operador_responsavel_id"],), fetch="one")
        oper = o["nome"] if o else None
    d = dict(eq)
    d["pontos"] = [dict(x) for x in pontos]
    d["planos"] = [dict(x) for x in planos]
    d["ots"] = [dict(x) for x in ots]
    d["componentes"] = [dict(x) for x in filhos]
    # (18/10/2026) `notas` (coluna texto = observações livres) ≠ diário de notas: a lista ia no mesmo nome,
    # sobrescrevia o texto e a ficha gravava "[object Object]" de volta no banco.
    d["notas_lista"] = [dict(n) for n in notas]
    if isinstance(d.get("notas"), str) and re.fullmatch(r"(\[object Object\],?\s*)+", d["notas"].strip()):
        d["notas"] = None
        await ajard_query(r"UPDATE operacional.equipamentos SET notas=NULL WHERE id=%s AND notas ~ '^(\[object Object\],?\s*)+$'", (eq_id,), fetch="none")
    d["operador_nome"] = oper
    d["foto_url"] = storage_url(d["foto_path"]) if d.get("foto_path") else None
    return d


# ── ALMOXARIFADOS + ESTOQUE (07/07/2026) ──
# Movimentos: entrada (destino) · saida (origem, valida saldo) ·
# transferencia (origem→destino, atômica) · ajuste (define saldo exato)

@router.post("/manutencao/api/almoxarifados")
async def criar_almoxarifado(request: Request, payload=Depends(verificar_manutencao)):
    """(25/08/2026) Local físico novo = cadastro, não código. Estrutura
    parametrizável: a Garra cria/renomeia/desativa almoxarifados sozinha."""
    d = await request.json()
    cod = (d.get("codigo") or "").strip().upper()
    nome = (d.get("nome") or "").strip()
    if not cod or not nome:
        raise HTTPException(status_code=400, detail="Informe código e nome")
    existe = await ajard_query("SELECT 1 FROM manutencao.almoxarifados WHERE codigo=%s", (cod,), fetch="one")
    if existe:
        raise HTTPException(status_code=409, detail=f"Código {cod} já existe")
    row = await ajard_query_id(
        "INSERT INTO manutencao.almoxarifados (codigo, nome) VALUES (%s,%s)", (cod, nome))
    return dict(row)


@router.patch("/manutencao/api/almoxarifados/{aid}")
async def editar_almoxarifado(aid: str, request: Request, payload=Depends(verificar_manutencao)):
    d = await request.json()
    sets, params = [], []
    if (d.get("nome") or "").strip():
        sets.append("nome=%s"); params.append(d["nome"].strip())
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(aid)
    r = await ajard_query(
        f"UPDATE manutencao.almoxarifados SET {', '.join(sets)} WHERE id::text=%s OR codigo=%s RETURNING *",
        tuple(params + [params[-1]]), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Almoxarifado não encontrado")
    return dict(r)


@router.get("/manutencao/api/almoxarifados")
async def listar_almoxarifados(_auth=Depends(verificar_manutencao)):
    rows = await ajard_query(
        """SELECT a.id, a.codigo, a.nome,
                  COALESCE((SELECT COUNT(*) FROM manutencao.estoque e
                            WHERE e.almoxarifado_id=a.id AND e.quantidade>0),0)::int AS itens
           FROM manutencao.almoxarifados a WHERE a.ativo=true ORDER BY a.codigo""")
    return [dict(r) for r in rows]


_FICHA_COLS_OK = False
async def _garantir_ficha_cols():
    """(26/08/2026) Ficha do equipamento padrão ManWinWin: características
    livres (20 pares rótulo/valor em JSONB), criticidade, garantia/contrato,
    foto no Supabase (garra-fotos/manutencao/equipamentos) e notas com trilha."""
    global _FICHA_COLS_OK
    if _FICHA_COLS_OK:
        return
    await ajard_query("""
        ALTER TABLE operacional.equipamentos
          ADD COLUMN IF NOT EXISTS caracteristicas JSONB,
          ADD COLUMN IF NOT EXISTS criticidade TEXT,
          ADD COLUMN IF NOT EXISTS localizacao TEXT,
          ADD COLUMN IF NOT EXISTS data_aquisicao DATE,
          ADD COLUMN IF NOT EXISTS garantia JSONB,
          ADD COLUMN IF NOT EXISTS foto_path TEXT,
          ADD COLUMN IF NOT EXISTS fornecedor_id UUID,
          ADD COLUMN IF NOT EXISTS vida_util_h NUMERIC(12,1),
          ADD COLUMN IF NOT EXISTS valor_atual NUMERIC(14,2),
          ADD COLUMN IF NOT EXISTS operador_desde DATE,
          ADD COLUMN IF NOT EXISTS operador_responsavel_id UUID,
          ADD COLUMN IF NOT EXISTS notas TEXT,
          ADD COLUMN IF NOT EXISTS info_compl JSONB""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.equipamento_notas (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL,
            data_nota TIMESTAMPTZ DEFAULT now(),
            usuario_id UUID,
            descricao TEXT NOT NULL,
            ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    # (18/10/2026) Nota com ficheiro (padrão ManWinWin: Nova Nota = data · funcionário · descrição · ficheiro)
    await ajard_query("ALTER TABLE manutencao.equipamento_notas ADD COLUMN IF NOT EXISTS documento_id UUID", fetch="none")
    await _garantir_mao_obra()
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS categoria TEXT", fetch="none")
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS ot_id UUID", fetch="none")
    _FICHA_COLS_OK = True


_ESTQ_COLS_OK = False
async def _garantir_estoque_cols():
    """(25/08/2026) Endereçamento físico nasce aqui (a licença gratuita do
    ManWinWin não exportava localização). Padrão Garra: C-P-N
    (Corredor–Prateleira–Nicho), preenchido na ENTRADA da peça."""
    global _ESTQ_COLS_OK
    if _ESTQ_COLS_OK:
        return
    await ajard_query("ALTER TABLE manutencao.estoque ADD COLUMN IF NOT EXISTS localizacao TEXT", fetch="none")
    await ajard_query("ALTER TABLE manutencao.estoque ADD COLUMN IF NOT EXISTS maximo NUMERIC", fetch="none")
    _ESTQ_COLS_OK = True


_PECA_COLS_OK = False
async def _garantir_peca_cols():
    """(26/08/2026) Ficha da peça padrão ManWinWin, decisões Garra:
    código do FABRICANTE é a identidade (não muda); Cód. Estruturado do
    ManWinWin não é replicado (redundante com família+código); custo médio
    editável como referência até o recebimento de OC alimentar sozinho."""
    global _PECA_COLS_OK
    if _PECA_COLS_OK:
        return
    await ajard_query("""
        ALTER TABLE manutencao.pecas
          ADD COLUMN IF NOT EXISTS caracteristicas JSONB,
          ADD COLUMN IF NOT EXISTS observacoes TEXT,
          ADD COLUMN IF NOT EXISTS codigo_externo TEXT,
          ADD COLUMN IF NOT EXISTS classe TEXT,
          ADD COLUMN IF NOT EXISTS espec_compra TEXT,
          ADD COLUMN IF NOT EXISTS codigo_fabricante TEXT""", fetch="none")
    _PECA_COLS_OK = True


_CODIGO_DOMINIOS = {
    "peca": "manutencao.pecas",
    "equipamento": "operacional.equipamentos",
    "almoxarifado": "manutencao.almoxarifados",
}


@router.get("/manutencao/api/proximo-codigo")
async def proximo_codigo(dominio: str, prefixo: str = "", sep: str = ".", largura: int = 4,
                         verificar: str = None, _auth=Depends(verificar_manutencao)):
    """(01/09/2026) Regra Garra para TODO cadastro com código: a estrutura é o
    gatilho — dado o prefixo (sigla do tipo, sub-família, código pai) devolve o
    próximo número livre; o usuário pode alterar; `verificar` diz se um código
    já existe. Largura segue a dos códigos já cadastrados no mesmo prefixo."""
    import re as _re
    tabela = _CODIGO_DOMINIOS.get(dominio) or _DOMINIOS_PARAM.get(dominio)
    if not tabela:
        raise HTTPException(status_code=400, detail="Domínio desconhecido")
    if sep not in (".", "-", ""):
        sep = "."
    pref = (prefixo or "").strip().upper()
    out = {"dominio": dominio, "prefixo": pref, "sep": sep}
    if verificar is not None:
        ex = await ajard_query(f"SELECT 1 FROM {tabela} WHERE upper(codigo)=%s", (verificar.strip().upper(),), fetch="one")
        out["existe"] = bool(ex)
        out["codigo"] = verificar.strip().upper()
        return out
    rows = await ajard_query(f"SELECT codigo FROM {tabela} WHERE upper(codigo) LIKE %s",
                             (pref + "%",), fetch="all")
    rx = _re.compile("^" + _re.escape(pref) + ("[.-]?" if pref else "") + r"(\d+)$", _re.I)
    maior, larg = 0, 0
    for r in rows or []:
        m = rx.match(str(r.get("codigo") or "").strip())
        if m:
            maior = max(maior, int(m.group(1)))
            larg = max(larg, len(m.group(1)))
    larg = larg or max(1, min(int(largura), 6))
    num = str(maior + 1).zfill(larg)
    out["numero"] = num
    out["codigo"] = (pref + sep + num) if pref else num
    return out


@router.post("/manutencao/api/pecas")
async def criar_peca(request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    d = await request.json()
    cod = (d.get("codigo") or "").strip()
    desc = (d.get("descricao") or "").strip()
    if not cod or not desc:
        raise HTTPException(status_code=400, detail="Informe código e descrição")
    existe = await ajard_query("SELECT 1 FROM manutencao.pecas WHERE codigo=%s", (cod,), fetch="one")
    if existe:
        raise HTTPException(status_code=409, detail=f"Peça {cod} já cadastrada")
    custo = None
    try:
        custo = float(str(d.get("custo_medio")).replace(",", ".")) if d.get("custo_medio") not in (None, "") else None
    except ValueError:
        pass
    import json as _json
    row = await ajard_query_id(
        """INSERT INTO manutencao.pecas
             (codigo, descricao, unidade, familia_codigo, custo_medio,
              codigo_externo, classe, espec_compra, caracteristicas, observacoes, codigo_fabricante)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (cod, desc, (d.get("unidade") or "UN").strip().upper(),
         (d.get("familia_codigo") or "").strip() or None, custo,
         (d.get("codigo_externo") or "").strip() or None,
         (d.get("classe") or "").strip() or None,
         (d.get("espec_compra") or "").strip() or None,
         _json.dumps(d.get("caracteristicas") or []),
         (d.get("observacoes") or "").strip() or None,
         (d.get("codigo_fabricante") or "").strip() or None))
    return dict(row)


@router.get("/manutencao/api/peca-ficha")
async def ficha_peca(peca_codigo: str, _auth=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    p = await ajard_query("SELECT * FROM manutencao.pecas WHERE codigo=%s", (peca_codigo,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    return dict(p)


@router.patch("/manutencao/api/peca-ficha")
async def editar_peca(request: Request, payload=Depends(verificar_manutencao)):
    """Código de peça viaja no corpo (à prova de barra). Código não se edita."""
    await _garantir_peca_cols()
    d = await request.json()
    p = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s", ((d.get("peca") or "").strip(),), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    import json as _json
    sets, params = [], []
    for c in ["descricao", "unidade", "familia_codigo", "codigo_externo", "observacoes", "classe", "espec_compra", "codigo_fabricante"]:
        if c in d:
            v = (str(d[c]).strip() or None) if d[c] is not None else None
            if c == "unidade" and v:
                v = v.upper()
            sets.append(f"{c}=%s"); params.append(v)
    if "custo_medio" in d:
        try:
            sets.append("custo_medio=%s")
            params.append(float(str(d["custo_medio"]).replace(",", ".")) if d["custo_medio"] not in (None, "") else None)
        except ValueError:
            sets.pop()
    if "caracteristicas" in d:
        sets.append("caracteristicas=%s"); params.append(_json.dumps(d["caracteristicas"] or []))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(p["id"])
    await ajard_query(f"UPDATE manutencao.pecas SET {', '.join(sets)} WHERE id=%s", tuple(params), fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/estoque-saldos")
async def saldo_peca(peca_codigo: str, _auth=Depends(verificar_manutencao)):
    """(25/08) Código de peça viaja por query (?peca_codigo=), nunca pelo
    caminho: o acervo real tem códigos com barra ('1620/VW 24250') que
    quebram rota de path. Lição permanente para catálogos migrados."""
    await _garantir_estoque_cols()
    rows = await ajard_query(
        """SELECT a.codigo AS almox, a.nome, COALESCE(e.quantidade,0) AS quantidade,
                  e.minimo, e.maximo, e.localizacao
           FROM manutencao.almoxarifados a
           LEFT JOIN manutencao.estoque e ON e.almoxarifado_id=a.id
             AND e.peca_id=(SELECT id FROM manutencao.pecas WHERE codigo=%s)
           WHERE a.ativo=true ORDER BY a.codigo""", (peca_codigo,))
    return [dict(r) for r in rows]


@router.patch("/manutencao/api/estoque-meta")
async def meta_estoque(request: Request, payload=Depends(verificar_manutencao)):
    """Localização e mínimo por peça×almoxarifado (paridade: campo aceito é
    campo gravado). Cria a linha de estoque zerada se não existir."""
    await _garantir_estoque_cols()
    d = await request.json()
    peca_codigo = (d.get("peca") or "").strip()
    peca = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s", (peca_codigo,), fetch="one")
    if not peca:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    alm = await ajard_query("SELECT id FROM manutencao.almoxarifados WHERE codigo=%s OR id::text=%s",
                            (d.get("almoxarifado"), str(d.get("almoxarifado"))), fetch="one")
    if not alm:
        raise HTTPException(status_code=404, detail="Almoxarifado não encontrado")
    await ajard_query("""
        INSERT INTO manutencao.estoque (peca_id, almoxarifado_id, quantidade)
        VALUES (%s,%s,0) ON CONFLICT (peca_id, almoxarifado_id) DO NOTHING""",
        (peca["id"], alm["id"]), fetch="none")
    sets, params = [], []
    if "localizacao" in d:
        sets.append("localizacao=%s"); params.append((d["localizacao"] or "").strip().upper() or None)
    for campo in ("minimo", "maximo"):
        if campo in d:
            try:
                sets.append(f"{campo}=%s")
                params.append(float(str(d[campo]).replace(",", ".")) if d[campo] not in (None, "") else None)
            except ValueError:
                sets.pop()
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params += [peca["id"], alm["id"]]
    await ajard_query(
        f"UPDATE manutencao.estoque SET {', '.join(sets)} WHERE peca_id=%s AND almoxarifado_id=%s",
        tuple(params), fetch="none")
    return {"ok": True}


_REC_OK = False
async def _garantir_recebimentos():
    """(01/09/2026) Elo Compras → Estoque com conferência física (decisão da
    Luana): o recebimento da OC no Compras gera uma ENTRADA PENDENTE; o saldo
    só sobe quando o almoxarifado confere. Uma linha por item recebido."""
    global _REC_OK
    if _REC_OK:
        return
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.entradas_pendentes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            oc_id UUID, oc_numero TEXT, oc_item_id UUID,
            peca_id UUID NOT NULL, quantidade NUMERIC(12,3) NOT NULL,
            custo_unitario NUMERIC(14,4), almoxarifado_codigo TEXT,
            nf_numero TEXT, fornecedor_id UUID, ot_id UUID,
            status TEXT DEFAULT 'pendente',
            quantidade_conferida NUMERIC(12,3), observacao TEXT,
            conferido_por UUID, conferido_em TIMESTAMPTZ,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_entradas_pend_status ON manutencao.entradas_pendentes (status)", fetch="none")
    _REC_OK = True


async def _em_pedido_por_peca():
    """Quantidade ainda por chegar em OCs vivas (aprovada/enviada/parcial) e
    quantidade recebida aguardando conferência, por peca_id."""
    pedido, pend, ocs = {}, {}, {}
    try:
        rows = await ajard_query(
            """SELECT i.peca_id, oc.numero, oc.status,
                      SUM(i.quantidade - COALESCE(i.qtd_recebida,0)) AS falta
               FROM compras.oc_itens i JOIN compras.ordens_compra oc ON oc.id = i.oc_id
               WHERE i.ativo=true AND oc.ativo=true AND i.peca_id IS NOT NULL
                 AND oc.status IN ('rascunho','solicitada','aprovada','enviada','recebida_parcial')
               GROUP BY i.peca_id, oc.numero, oc.status""") or []
        for r in rows:
            k = str(r["peca_id"])
            pedido[k] = pedido.get(k, 0.0) + max(0.0, float(r["falta"] or 0))
            ocs.setdefault(k, []).append(f"{r['numero']} ({r['status']})")
    except Exception:
        pass
    await _garantir_recebimentos()
    rows = await ajard_query(
        """SELECT peca_id, SUM(quantidade) AS q FROM manutencao.entradas_pendentes
           WHERE status='pendente' GROUP BY peca_id""") or []
    for r in rows:
        pend[str(r["peca_id"])] = float(r["q"] or 0)
    return pedido, pend, ocs


# ══ (20/10/2026) COMPROMETIDO (padrão MWW) ═══════════════════════════════════════════════════
# Quantidade prevista nas OTs PROGRAMADAS (itens da FMP) que ainda vai sair do estoque.
# Disponível = existência − comprometido. A etiqueta não reserva nada: a reserva vive aqui.
async def _comprometido_por_peca():
    import json as _json
    rows = await ajard_query(
        """SELECT o.numero, o.data_prevista, eq.codigo AS equipamento, p.pecas
           FROM manutencao.ot o
           JOIN manutencao.planos p ON p.id = o.plano_id
           JOIN operacional.equipamentos eq ON eq.id = o.equipamento_id
           WHERE o.ativo=true AND o.status='programada' AND p.pecas IS NOT NULL""") or []
    out = {}
    for r in rows:
        itens = r["pecas"]
        if isinstance(itens, str):
            try:
                itens = _json.loads(itens)
            except Exception:
                itens = []
        for it in itens or []:
            cod = str(it.get("peca") or it.get("codigo") or "").strip()
            if not cod:
                continue
            try:
                q = float(str(it.get("qtd") or it.get("quantidade") or 0).replace(",", "."))
            except ValueError:
                q = 0.0
            if q <= 0:
                continue
            d = out.setdefault(cod, {"qtd": 0.0, "ots": []})
            d["qtd"] += q
            d["ots"].append({"numero": r["numero"], "equipamento": r["equipamento"], "qtd": q,
                             "data_prevista": r["data_prevista"].isoformat() if r.get("data_prevista") else None})
    return out


@router.get("/manutencao/api/pecas/{codigo}/comprometido")
async def peca_comprometido(codigo: str, _auth=Depends(verificar_manutencao)):
    """Existência, comprometido (OTs programadas) e disponível de um item — usado na baixa da OT."""
    p = await ajard_query(
        """SELECT p.id, p.codigo, p.descricao, p.unidade, p.custo_medio,
                  COALESCE((SELECT SUM(e.quantidade) FROM manutencao.estoque e WHERE e.peca_id=p.id),0) AS saldo
           FROM manutencao.pecas p WHERE p.codigo=%s""", (codigo,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    c = (await _comprometido_por_peca()).get(codigo, {"qtd": 0.0, "ots": []})
    saldo = float(p["saldo"] or 0)
    return {"peca_id": str(p["id"]), "codigo": p["codigo"], "descricao": p["descricao"], "unidade": p["unidade"],
            "custo_medio": float(p["custo_medio"]) if p["custo_medio"] is not None else None,
            "saldo": saldo, "comprometido": round(c["qtd"], 3), "disponivel": round(saldo - c["qtd"], 3), "ots": c["ots"]}


@router.get("/manutencao/api/estoque-baixo")
async def estoque_baixo(_auth=Depends(verificar_manutencao)):
    """Peças abaixo do mínimo — com o que já está a caminho (OCs vivas +
    recebimentos a conferir) e a SUGESTÃO de compra:
    sugestão = máximo − saldo − em pedido − a conferir (sem máximo: repõe até 2× o mínimo).
    Traz o fornecedor de referência (menor preço cadastrado) para a OC sugerida."""
    await _garantir_estoque_cols()
    await _garantir_tabela("referencias-fornecedor")
    rows = await ajard_query(
        """SELECT p.id AS peca_id, p.codigo, p.descricao, p.unidade, p.custo_medio, a.codigo AS almox,
                  e.quantidade, e.minimo, e.maximo, e.localizacao,
                  (SELECT COALESCE(SUM(quantidade),0) FROM manutencao.estoque x WHERE x.peca_id = p.id) AS saldo_total,
                  rf.fornecedor_id AS ref_fornecedor_id, rf.preco AS ref_preco, rf.prazo_dias AS ref_prazo, rf.referencia AS ref_codigo,
                  (SELECT nome FROM public.fornecedores f WHERE f.id = rf.fornecedor_id) AS ref_fornecedor_nome
           FROM manutencao.estoque e
           JOIN manutencao.pecas p ON p.id = e.peca_id AND p.ativo=true
           JOIN manutencao.almoxarifados a ON a.id = e.almoxarifado_id AND a.ativo=true
           LEFT JOIN LATERAL (SELECT fornecedor_id, preco, prazo_dias, referencia FROM manutencao.peca_fornecedores r
                              WHERE r.peca_codigo = p.codigo AND r.ativo=true AND r.fornecedor_id IS NOT NULL
                              ORDER BY r.preco NULLS LAST, r.atualizado_em DESC LIMIT 1) rf ON true
           WHERE e.minimo IS NOT NULL
           ORDER BY (e.minimo - e.quantidade) DESC""") or []
    pedido, pend, ocs = await _em_pedido_por_peca()
    comp = await _comprometido_por_peca()   # (20/10/2026) abaixo do mínimo = DISPONÍVEL (existência − comprometido) < mínimo
    out = []
    for r in rows:
        d = _tab_row(r)
        k = str(r["peca_id"])
        cq = float(comp.get(r["codigo"], {}).get("qtd", 0.0))
        disp_total = float(r["saldo_total"] or 0) - cq
        if not (float(r["quantidade"] or 0) < float(r["minimo"] or 0) or disp_total < float(r["minimo"] or 0)):
            continue
        d["comprometido"] = round(cq, 3)
        d["disponivel"] = round(disp_total, 3)
        d["em_pedido"] = round(pedido.get(k, 0.0), 3)
        d["a_conferir"] = round(pend.get(k, 0.0), 3)
        d["ocs_abertas"] = ocs.get(k, [])
        saldo = float(r["quantidade"] or 0); mn = float(r["minimo"] or 0)
        alvo = float(r["maximo"]) if r.get("maximo") is not None else mn * 2
        d["alvo"] = alvo
        d["sugestao"] = round(max(0.0, alvo - min(saldo, disp_total) - d["em_pedido"] - d["a_conferir"]), 3)
        d["preco_sugerido"] = float(r["ref_preco"]) if r.get("ref_preco") is not None else (float(r["custo_medio"]) if r.get("custo_medio") is not None else None)
        out.append(d)
    return out


@router.get("/manutencao/api/oc-existe")
async def oc_existe(numero: str, _auth=Depends(verificar_manutencao)):
    oc = await _oc_por_numero((numero or "").strip())
    return {"existe": bool(oc), "status": oc["status"] if oc else None}


@router.get("/manutencao/api/ots/{ot_id}/faltantes")
async def ot_faltantes(ot_id: str, _auth=Depends(verificar_manutencao)):
    """Peças previstas na FMP da OT que o estoque não cobre → itens da OC.
    faltante = previsto − saldo total − em pedido − a conferir."""
    o = await ajard_query("SELECT plano_id, equipamento_id, numero FROM manutencao.ot WHERE id=%s", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    import json as _json
    prev = []
    if o.get("plano_id"):
        pl = await ajard_query("SELECT pecas FROM manutencao.planos WHERE id=%s", (o["plano_id"],), fetch="one")
        raw = (pl or {}).get("pecas")
        try:
            prev = raw if isinstance(raw, list) else (_json.loads(raw) if raw else [])
        except Exception:
            prev = []
    pedido, pend, ocs = await _em_pedido_por_peca()
    itens = []
    for x in prev:
        cod = (x.get("peca") or "").strip()
        if not cod: continue
        p = await ajard_query(
            """SELECT p.id, p.codigo, p.descricao, p.unidade, p.custo_medio,
                      (SELECT COALESCE(SUM(quantidade),0) FROM manutencao.estoque e WHERE e.peca_id=p.id) AS saldo
               FROM manutencao.pecas p WHERE p.codigo=%s""", (cod,), fetch="one")
        if not p:
            itens.append({"peca_id": None, "codigo": cod, "descricao": x.get("descricao") or cod, "unidade": x.get("un") or "UN",
                          "previsto": _mo_num(x.get("qtd")) or 0, "saldo": 0, "em_pedido": 0, "faltante": _mo_num(x.get("qtd")) or 0,
                          "preco": _mo_num(x.get("custo")), "sem_cadastro": True})
            continue
        k = str(p["id"]); q = _mo_num(x.get("qtd")) or 0
        falta = max(0.0, q - float(p["saldo"] or 0) - pedido.get(k, 0.0) - pend.get(k, 0.0))
        itens.append({"peca_id": k, "codigo": p["codigo"], "descricao": p["descricao"], "unidade": p["unidade"] or "UN",
                      "previsto": q, "saldo": float(p["saldo"] or 0), "em_pedido": pedido.get(k, 0.0) + pend.get(k, 0.0),
                      "faltante": round(falta, 3), "preco": float(p["custo_medio"]) if p["custo_medio"] is not None else _mo_num(x.get("custo")),
                      "ocs": ocs.get(k, [])})
    return {"ot": o["numero"], "equipamento_id": str(o["equipamento_id"]) if o.get("equipamento_id") else None, "itens": itens}


@router.get("/manutencao/api/recebimentos")
async def recebimentos_listar(status: str = "pendente", _auth=Depends(verificar_manutencao)):
    await _garantir_recebimentos()
    rows = await ajard_query(
        """SELECT r.*, p.codigo AS peca_codigo, p.descricao AS peca_descricao, p.unidade,
                  (SELECT nome FROM public.fornecedores f WHERE f.id = r.fornecedor_id) AS fornecedor_nome,
                  (SELECT numero FROM manutencao.ot o WHERE o.id = r.ot_id) AS ot_numero,
                  (SELECT nome FROM public.usuarios_garra u WHERE u.id = r.conferido_por) AS conferido_por_nome
           FROM manutencao.entradas_pendentes r JOIN manutencao.pecas p ON p.id = r.peca_id
           WHERE r.status = %s ORDER BY r.criado_em DESC LIMIT 300""", (status,)) or []
    return [_tab_row(r) for r in rows]


@router.post("/manutencao/api/recebimentos/{rid}/conferir")
async def recebimento_conferir(rid: str, request: Request, payload=Depends(verificar_manutencao)):
    """Conferência física: o saldo sobe AGORA, pela mesma entrada por
    documento (custo médio ponderado, NF, oc_numero, fornecedor). Quantidade
    conferida pode ser menor (falta na caixa) — a diferença fica na trilha."""
    await _garantir_recebimentos()
    d = await request.json()
    r = await ajard_query("SELECT r.*, p.codigo FROM manutencao.entradas_pendentes r JOIN manutencao.pecas p ON p.id=r.peca_id WHERE r.id=%s", (rid,), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Recebimento não encontrado")
    if r["status"] != "pendente":
        raise HTTPException(status_code=400, detail=f"Recebimento já {r['status']}")
    qtd = _mo_num(d.get("quantidade")) if d.get("quantidade") not in (None, "") else float(r["quantidade"])
    if not qtd or qtd <= 0:
        raise HTTPException(status_code=400, detail="Quantidade conferida deve ser positiva")
    alm = (d.get("almox") or r.get("almoxarifado_codigo") or "").strip()
    if not alm:
        a = await ajard_query("SELECT codigo FROM manutencao.almoxarifados WHERE ativo=true ORDER BY codigo LIMIT 1", fetch="one")
        alm = a["codigo"] if a else ""
    obs = (d.get("observacao") or "").strip()
    corpo = {"documento_tipo": "nf", "documento_numero": r.get("nf_numero") or r.get("oc_numero") or "OC",
             "fornecedor_id": str(r["fornecedor_id"]) if r.get("fornecedor_id") else None,
             "oc_numero": r.get("oc_numero"), "destino": alm,
             "observacao": ("Conferência do recebimento " + (r.get("oc_numero") or "") + (" · " + obs if obs else "")).strip(),
             "itens": [{"peca": r["codigo"], "quantidade": qtd,
                        "custo_unitario": float(r["custo_unitario"]) if r.get("custo_unitario") is not None else None}]}
    res = await _entrada_core(corpo, payload)
    uid = await _usuario_id(payload)
    await ajard_query(
        """UPDATE manutencao.entradas_pendentes SET status='conferida', quantidade_conferida=%s, almoxarifado_codigo=%s,
           observacao=%s, conferido_por=%s, conferido_em=now() WHERE id=%s""",
        (qtd, alm, obs or None, uid, rid), fetch="none")
    if r.get("ot_id"):
        await ajard_query(
            """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
               SELECT id, status, status, %s, %s FROM manutencao.ot WHERE id=%s""",
            (f"📦 Peça {r['codigo']} conferida no almoxarifado {alm} ({qtd:g}) — OC {r.get('oc_numero') or ''}", uid, r["ot_id"]), fetch="none")
    return {"ok": True, "entrada": res, "diferenca": round(qtd - float(r["quantidade"]), 3)}


@router.post("/manutencao/api/recebimentos/{rid}/rejeitar")
async def recebimento_rejeitar(rid: str, request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_recebimentos()
    d = await request.json()
    motivo = (d.get("motivo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="Informe o motivo da rejeição")
    uid = await _usuario_id(payload)
    await ajard_query(
        """UPDATE manutencao.entradas_pendentes SET status='rejeitada', observacao=%s, conferido_por=%s, conferido_em=now()
           WHERE id=%s AND status='pendente'""", (motivo, uid, rid), fetch="none")
    return {"ok": True}


@router.post("/manutencao/api/estoque/movimentar")
async def movimentar_estoque(request: Request, payload=Depends(verificar_manutencao)):
    d = await request.json()
    tipo = (d.get("tipo") or "").strip()
    qtd = float(d.get("quantidade") or 0)
    if tipo not in ("entrada", "saida", "transferencia", "ajuste"):
        raise HTTPException(status_code=400, detail="Tipo inválido")
    if qtd <= 0 and tipo != "ajuste":
        raise HTTPException(status_code=400, detail="Quantidade deve ser positiva")
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")
    peca = await ajard_query(
        "SELECT id, custo_medio, familia_codigo, descricao FROM manutencao.pecas WHERE codigo=%s OR id::text=%s",
        (d.get("peca"), str(d.get("peca"))), fetch="one")
    if not peca:
        raise HTTPException(status_code=404, detail="Peça não encontrada")
    pid = peca["id"]
    try:
        custo_unit = float(str(d.get("custo_unitario")).replace(",", ".")) if d.get("custo_unitario") not in (None, "") \
            else (float(peca["custo_medio"]) if peca["custo_medio"] is not None else None)
    except (TypeError, ValueError):
        custo_unit = float(peca["custo_medio"]) if peca["custo_medio"] is not None else None

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
        if custo_unit is not None and d.get("custo_unitario") not in (None, "") and not d.get("ot_id"):
            # (01/09/2026) custo médio ponderado: (saldo × médio atual + qtd × custo da entrada) / (saldo + qtd)
            tot = await ajard_query("SELECT COALESCE(SUM(quantidade),0) AS q FROM manutencao.estoque WHERE peca_id=%s", (pid,), fetch="one")
            saldo_total = float(tot["q"] or 0) if tot else 0.0
            medio_atual = float(peca["custo_medio"]) if peca["custo_medio"] is not None else None
            novo = custo_unit if (saldo_total <= 0 or medio_atual is None) else (saldo_total * medio_atual + qtd * custo_unit) / (saldo_total + qtd)
            await ajard_query("UPDATE manutencao.pecas SET custo_medio=%s WHERE id=%s", (round(novo, 4), pid), fetch="none")
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
    # conta escolhida na tela (cadastro de Parametrizar) vale; sem escolha, a sugestão automática
    rub = ((d.get("rubrica") or "").strip() or _rubrica_material(tipo, peca["familia_codigo"], peca["descricao"])) if tipo == "saida" and d.get("ot_id") else None
    await ajard_query("""
        INSERT INTO manutencao.movimentacoes (tipo, peca_id, almox_origem, almox_destino, quantidade, ot_id, usuario_id, observacao, custo_unitario, rubrica)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tipo, pid, origem, destino, qtd, d.get("ot_id"), uid, (d.get("observacao") or "").strip() or None, custo_unit, rub), fetch="none")
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
    # (19/10/2026) Norma de materiais em 3 níveis (ManWinWin ARQ01/02/03): Classe → Família → Subfamília.
    # Classe mora aqui; famílias ganham coluna `classe`; subfamílias continuam como "BT.010".
    "classes-materiais": "manutencao.classes_materiais",
    # (19/10/2026) Famílias de OBJETOS (escavadeiras, caminhões…) separadas da Norma de
    # materiais (AC, AU, MC…): as duas moravam em manutencao.familias e colidiam (CB).
    "familias-objetos": "manutencao.familias_objetos",
    "sintomas": "manutencao.sintomas",
    "causas": "manutencao.causas",
    "centros-custo": "manutencao.centros_custo",
    "motivos-reprogramacao": "manutencao.motivos_reprogramacao",
    "motivos-pendente": "manutencao.motivos_pendente",
    "periodos": "manutencao.periodos",
    "tipos-ferramenta": "manutencao.tipos_ferramenta",
    "rubricas": "manutencao.rubricas",
    "combustiveis": "manutencao.combustiveis",
    "tipos-documento": "manutencao.tipos_documento",
    "condicoes-pagamento": "manutencao.condicoes_pagamento",
    "horarios-trabalho": "manutencao.horarios_trabalho",
}

_DOM_OK = False
async def _garantir_dominios():
    """(28/08/2026) Pacote C — DDL dos domínios viaja no código (Ciclo
    Garra): Reset from parent não pode mais derrubar a Parametrização.
    Todas as tabelas de domínio nascem idempotentes (codigo/nome/ativo)."""
    global _DOM_OK
    if _DOM_OK:
        return
    for _t in _DOMINIOS_PARAM.values():
        await ajard_query(
            f"""CREATE TABLE IF NOT EXISTS {_t} (
                codigo TEXT PRIMARY KEY, nome TEXT, ativo BOOLEAN DEFAULT true)""",
            fetch="none")
    await ajard_query("ALTER TABLE manutencao.familias ADD COLUMN IF NOT EXISTS classe TEXT", fetch="none")
    await ajard_query("ALTER TABLE manutencao.familias ADD COLUMN IF NOT EXISTS caracteristicas JSONB", fetch="none")
    # (26/09/2026) Hierarquia ManWinWin dos Tipos de Trabalho (domínio "tipos-manutencao",
    # o que alimenta a árvore de OTs): a letra é a pasta, o subtipo (A1, C1, C2…) é o
    # que a OT/FMP recebe. Idempotente: só insere o que falta; nomes existentes ficam.
    await ajard_query("""
        INSERT INTO manutencao.tipos_manutencao (codigo, nome) VALUES
          ('A','Preventivo Sistemático'), ('A1','Sistemático'),
          ('B','Preventivo Condicionado'), ('B1','Condicionado'),
          ('C','Corretivo'), ('C1','Reparação Avaria'), ('C2','Corretiva Deferida'),
          ('M','Melhoria'), ('M1','Melhoria')
        ON CONFLICT (codigo) DO NOTHING""", fetch="none")
    # OTs sem tipo de trabalho válido herdam o subtipo padrão da sua classe (tipo)
    await ajard_query("""
        UPDATE manutencao.ot SET tipo_trabalho = CASE tipo
            WHEN 'preventiva' THEN 'A1' WHEN 'corretiva' THEN 'C1'
            WHEN 'melhoria' THEN 'M1' WHEN 'reforma' THEN 'M1' ELSE 'C1' END
        WHERE tipo_trabalho IS NULL
           OR tipo_trabalho NOT IN (SELECT codigo FROM manutencao.tipos_manutencao)""", fetch="none")
    # (27/09/2026) PT-BR: "planeado" → "planejado" no status dos projetos (valor gravado)
    await _garantir_tabela("projectos")
    await ajard_query("UPDATE manutencao.projectos SET status='planejado' WHERE status='planeado'", fetch="none")
    _DOM_OK = True

_BIB_OK = False
async def _garantir_biblioteca():
    """(26/08/2026) Biblioteca de Preparações Padrão — modelo COMPLETO
    reutilizável (tarefas, mão de obra, peças, outros, ferramentas) com
    código automático por tipo de equipamento (EH-00001…). Rubricas e
    Tipos de Ferramenta nascem como domínios parametrizáveis."""
    global _BIB_OK
    if _BIB_OK:
        return
    await _garantir_dominios()
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.tipos_ferramenta (
            codigo TEXT PRIMARY KEY, nome TEXT, ativo BOOLEAN DEFAULT true)""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.rubricas (
            codigo TEXT PRIMARY KEY, nome TEXT, ativo BOOLEAN DEFAULT true)""", fetch="none")
    await ajard_query("""
        INSERT INTO manutencao.rubricas (codigo, nome) VALUES
          ('1','Mão de Obra'), ('1.01','Pessoal interno'), ('1.02','Pessoal produção'), ('1.03','Pessoal externo'),
          ('2','Peças e consumíveis aplicados'),
          ('2.01','Saída estoque - mater.consumo'), ('2.02','Saída estoque - peças de reposição'), ('2.03','Saída estoque - lubrificantes'),
          ('2.04','Aplicação direta - mat.consumo'), ('2.05','Aplicação direta - peças de reposição'), ('2.06','Aplicação direta - lubrificantes'),
          ('3','Serviços aplicados'), ('3.01','Aquisição serviços'), ('3.02','Contratos manutenção'),
          ('4','Estrutura depart. manutenção'),
          ('4.01','Salá. + encargos pess.directo'), ('4.02','Salá. + encarg. pess.indirecto'), ('4.03','Ferramentas'),
          ('4.04','Energia e fluídos p/ depto.'), ('4.05','Funcionamento (geral)'),
          ('5','Aquisição materiais p/ estoque'),
          ('5.01','Entradas estoque mat.consumo'), ('5.02','Entradas estoque peças de reposição'), ('5.03','Entradas estoque lubrificantes'),
          ('6','Combustível, energia e fluídos'),
          ('6.01','Electricidade'), ('6.02','Gás'), ('6.03','Água'), ('6.04','Combustíveis'),
          ('6.04.01','Óleo Diesel'), ('6.04.02','Gasolina'), ('6.04.03','Etanol'),
          ('7','Custo padrão de referência'), ('7.01','Custos indisponibilidade'),
          ('8','Funcionário'), ('8.01','Funcionário ativo'), ('8.02','Funcionário inativo')
        ON CONFLICT DO NOTHING""", fetch="none")
    # (10/10/2026) PT-BR: "armazém" → "estoque" nos nomes já gravados no banco (idempotente)
    await ajard_query("""UPDATE manutencao.rubricas
                            SET nome = regexp_replace(replace(replace(replace(replace(nome,'armazém','estoque'),'Armazém','Estoque'),'armaz.','estoque '),'armz.','estoque '), '\\s{2,}', ' ', 'g')
                          WHERE nome ILIKE '%armaz%'""", fetch="none")
    await ajard_query("""UPDATE manutencao.rubricas
                            SET nome = replace(replace(replace(replace(replace(nome,'Aplica.directa','Aplicação direta'),'Aplicação directa','Aplicação direta'),'directa','direta'),'sobressalentes','peças de reposição'),'sobressalente','peças de reposição')
                          WHERE nome ILIKE '%direct%' OR nome ILIKE '%sobressalente%'""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.biblioteca_preparacoes (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tipo_sigla TEXT NOT NULL,
            sequencia INT NOT NULL,
            codigo TEXT NOT NULL UNIQUE,
            descricao TEXT NOT NULL,
            tdm_horas NUMERIC,
            periodo_codigo TEXT,
            criticidade TEXT DEFAULT 'normal',
            tarefas TEXT,
            mao_obra JSONB,
            pecas JSONB,
            outros JSONB,
            ferramentas JSONB,
            ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
    for _col in ("mao_obra", "pecas", "outros", "ferramentas"):
        await ajard_query(
            f"ALTER TABLE manutencao.planos ADD COLUMN IF NOT EXISTS {_col} JSONB",
            fetch="none")
    _BIB_OK = True


@router.post("/manutencao/api/planos")
async def criar_plano(request: Request, payload=Depends(verificar_manutencao)):
    """(26/08/2026) Nova FMP do equipamento — código automático por tipo
    (A1-01, A1-02… por equipamento, padrão ManWinWin). Pode nascer da
    Biblioteca de Preparações (herda descrição, período, TDM e tarefas)."""
    await _garantir_biblioteca()
    d = await request.json()
    eq_id = d.get("equipamento_id")
    tt = (d.get("tipo_trabalho") or "").strip().upper()
    desc = (d.get("descricao") or "").strip()
    if not eq_id or not tt or not desc:
        raise HTTPException(status_code=400, detail="Informe equipamento, tipo de trabalho e descrição")
    seq = await ajard_query(
        """SELECT COALESCE(MAX(CAST(NULLIF(split_part(codigo, '-', 2), '') AS INT)), 0) + 1 AS n
           FROM manutencao.planos
           WHERE equipamento_id=%s AND codigo LIKE %s""",
        (eq_id, tt + "-%"), fetch="one")
    codigo = f"{tt}-{int(seq['n']):02d}"
    tdm = None
    try:
        tdm = float(str(d.get("tempo_horas")).replace(",", ".")) if d.get("tempo_horas") not in (None, "") else None
    except ValueError:
        pass
    custo = None
    try:
        custo = float(str(d.get("custo_previsto")).replace(",", ".")) if d.get("custo_previsto") not in (None, "") else None
    except ValueError:
        pass
    qtd = None
    try:
        qtd = float(str(d.get("periodo_qtd")).replace(",", ".")) if d.get("periodo_qtd") not in (None, "") else None
    except ValueError:
        pass
    row = await ajard_query_id(
        """INSERT INTO manutencao.planos
              (equipamento_id, codigo, descricao, tipo_trabalho, periodo_codigo, periodo_qtd,
               tempo_horas, custo_previsto, procedimento)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (eq_id, codigo, desc, tt, (d.get("periodo_codigo") or "").strip() or None,
         qtd, tdm, custo, (d.get("procedimento") or "").strip() or None))
    return dict(row)


@router.patch("/manutencao/api/planos/{pid}")
async def editar_plano(pid: str, request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_biblioteca()
    d = await request.json()
    sets, params = [], []
    for c in ["descricao", "tipo_trabalho", "periodo_codigo", "procedimento", "plano_proximo_codigo"]:
        if c in d:
            sets.append(f"{c}=%s"); params.append((str(d[c]).strip() or None) if d[c] is not None else None)
    for c in ["periodo_qtd", "tempo_horas", "custo_previsto"]:
        if c in d:
            try:
                sets.append(f"{c}=%s")
                params.append(float(str(d[c]).replace(",", ".")) if d[c] not in (None, "") else None)
            except ValueError:
                sets.pop()
    import json as _json
    for c in ["mao_obra", "pecas", "outros", "ferramentas"]:
        if c in d:
            sets.append(f"{c}=%s"); params.append(_json.dumps(d[c] or []))
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    for c in ("data_fixa", "registro_fixo"):
        if c in d:
            sets.append(f"{c}=%s"); params.append(bool(d[c]))
    if "preparacao_codigo" in d:
        sets.append("preparacao_codigo=%s"); params.append((d.get("preparacao_codigo") or "").strip() or None)
    _blocos = [d.get("mao_obra") or [], d.get("pecas") or [], d.get("outros") or []]
    if any(isinstance(b, list) and len(b) for b in _blocos):
        def _n(v):
            try:
                return float(str(v).replace(",", "."))
            except (TypeError, ValueError):
                return 0.0
        _soma = sum(_n(x.get("custo")) for x in _blocos[0] if isinstance(x, dict))
        _soma += sum(_n(x.get("qtd")) * _n(x.get("custo")) for x in _blocos[1] if isinstance(x, dict))
        _soma += sum(_n(x.get("custo")) for x in _blocos[2] if isinstance(x, dict))
        _soma = round(_soma, 2)
        if "custo_previsto=%s" in sets:
            params[sets.index("custo_previsto=%s")] = _soma
        else:
            sets.append("custo_previsto=%s"); params.append(_soma)
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(pid)
    r = await ajard_query(
        f"UPDATE manutencao.planos SET {', '.join(sets)} WHERE id=%s RETURNING codigo",
        tuple(params), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
    # (13/10/2026) período/baseline mudou → OT programada da FMP acompanha (se não alterada à mão)
    try:
        await _garantir_ot_programada(pid, payload)
    except HTTPException:
        pass
    return {"ok": True, "codigo": r["codigo"]}


@router.get("/manutencao/api/biblioteca")
async def listar_biblioteca(tipo_sigla: str = None, _auth=Depends(verificar_manutencao)):
    await _garantir_biblioteca()
    where, params = ["b.ativo=true"], []
    if tipo_sigla:
        where.append("b.tipo_sigla=%s"); params.append(tipo_sigla)
    rows = await ajard_query(
        f"""SELECT b.*, p.nome AS periodo_nome, t.nome AS tipo_nome
            FROM manutencao.biblioteca_preparacoes b
            LEFT JOIN manutencao.periodos p ON p.codigo = b.periodo_codigo
            LEFT JOIN manutencao.tipos_equipamento t ON t.sigla = b.tipo_sigla
            WHERE {' AND '.join(where)} ORDER BY b.codigo""", params)
    return [dict(r) for r in rows]


@router.post("/manutencao/api/biblioteca")
async def criar_preparacao(request: Request, payload=Depends(verificar_manutencao)):
    """Código automático por tipo (SIGLA-00001) — a parametrização define."""
    await _garantir_biblioteca()
    d = await request.json()
    sigla = (d.get("tipo_sigla") or "").strip().upper()
    desc = (d.get("descricao") or "").strip()
    if not sigla or not desc:
        raise HTTPException(status_code=400, detail="Informe tipo de equipamento e descrição")
    seq = await ajard_query(
        "SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.biblioteca_preparacoes WHERE tipo_sigla=%s",
        (sigla,), fetch="one")
    codigo = f"{sigla}-{int(seq['n']):05d}"
    import json as _json
    row = await ajard_query_id(
        """INSERT INTO manutencao.biblioteca_preparacoes
              (tipo_sigla, sequencia, codigo, descricao, tdm_horas, periodo_codigo,
               criticidade, tarefas, mao_obra, pecas, outros, ferramentas)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (sigla, int(seq["n"]), codigo, desc,
         (float(str(d["tdm_horas"]).replace(",", ".")) if d.get("tdm_horas") not in (None, "") else None),
         (d.get("periodo_codigo") or "").strip() or None,
         (d.get("criticidade") or "normal"), (d.get("tarefas") or "").strip() or None,
         _json.dumps(d.get("mao_obra") or []), _json.dumps(d.get("pecas") or []),
         _json.dumps(d.get("outros") or []), _json.dumps(d.get("ferramentas") or [])))
    return dict(row)


@router.patch("/manutencao/api/biblioteca/{bid}")
async def editar_preparacao(bid: str, request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_biblioteca()
    d = await request.json()
    import json as _json
    sets, params = [], []
    for c in ["descricao", "periodo_codigo", "criticidade", "tarefas"]:
        if c in d:
            sets.append(f"{c}=%s"); params.append((str(d[c]).strip() or None) if d[c] is not None else None)
    if "tdm_horas" in d:
        try:
            sets.append("tdm_horas=%s")
            params.append(float(str(d["tdm_horas"]).replace(",", ".")) if d["tdm_horas"] not in (None, "") else None)
        except ValueError:
            sets.pop()
    for c in ["mao_obra", "pecas", "outros", "ferramentas"]:
        if c in d:
            sets.append(f"{c}=%s"); params.append(_json.dumps(d[c] or []))
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(bid)
    r = await ajard_query(
        f"UPDATE manutencao.biblioteca_preparacoes SET {', '.join(sets)} WHERE id=%s RETURNING codigo",
        tuple(params), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Preparação não encontrada")
    return {"ok": True, "codigo": r["codigo"]}


@router.patch("/manutencao/api/param/{dominio}/{codigo}")
async def editar_dominio(dominio: str, codigo: str, request: Request, payload=Depends(verificar_manutencao)):
    """(23/08/2026) Completa o CRUD da parametrização (Regra 63): renomear e
    desativar/reativar código. Soft delete SEMPRE — código usado em OT
    histórica nunca some, sai de cena (ativo=false). Coluna ativo garantida
    por ALTER idempotente (viaja no código, padrão do Ciclo Garra)."""
    tabela = _DOMINIOS_PARAM.get(dominio)
    if not tabela:
        raise HTTPException(status_code=404, detail="Domínio não parametrizável")
    await ajard_query(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT true", fetch="none")
    d = await request.json()
    pk = "sigla" if "tipos_equipamento" in tabela else "codigo"
    sets, params = [], []
    if "nome" in d and (d.get("nome") or "").strip():
        sets.append("nome=%s"); params.append(d["nome"].strip())
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if "rubrica" in d and "combustiveis" in tabela:
        sets.append("rubrica=%s"); params.append((d.get("rubrica") or "").strip() or None)
    if "classe" in d and tabela == "manutencao.familias":
        sets.append("classe=%s"); params.append((d.get("classe") or "").strip().upper() or None)
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(codigo)
    await ajard_query(f"UPDATE {tabela} SET {', '.join(sets)} WHERE {pk}=%s",
                      tuple(params), fetch="none")
    rows = await ajard_query(f"SELECT * FROM {tabela} WHERE {pk}=%s", (codigo,))
    if not rows:
        raise HTTPException(status_code=404, detail="Código não encontrado")
    return dict(rows[0])


@router.delete("/manutencao/api/param/{dominio}/{codigo}")
async def excluir_dominio(dominio: str, codigo: str, payload=Depends(verificar_manutencao)):
    """(24/08/2026) Exclusão DEFINITIVA — só para código nunca usado (lixo de
    carga). Se algo o referencia (FK), o banco recusa e devolvemos 409
    orientando a desativar. Em uso = história; história não se apaga."""
    tabela = _DOMINIOS_PARAM.get(dominio)
    if not tabela:
        raise HTTPException(status_code=404, detail="Domínio não parametrizável")
    pk = "sigla" if "tipos_equipamento" in tabela else "codigo"
    import asyncpg as _apg
    try:
        await ajard_query(f"DELETE FROM {tabela} WHERE {pk}=%s", (codigo,), fetch="none")
    except _apg.exceptions.ForeignKeyViolationError:
        raise HTTPException(
            status_code=409,
            detail="Código em uso no histórico — não pode ser excluído. Use Desativar.")
    return {"ok": True, "excluido": codigo}


@router.get("/manutencao/api/param/{dominio}")
async def listar_dominio(dominio: str, _auth=Depends(verificar_manutencao)):
    await _garantir_dominios()
    tabela = _DOMINIOS_PARAM.get(dominio)
    if not tabela:
        raise HTTPException(status_code=404, detail="Domínio não parametrizável")
    await ajard_query(f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT true", fetch="none")
    rows = await ajard_query(f"SELECT * FROM {tabela} ORDER BY 1")
    out = []
    for r in rows:
        d = dict(r)
        # (19/10/2026) JSONB chega como texto do asyncpg → lista (características da subfamília)
        if isinstance(d.get("caracteristicas"), str):
            try: d["caracteristicas"] = _json.loads(d["caracteristicas"])
            except Exception: pass
        out.append(d)
    return out


@router.post("/manutencao/api/param/{dominio}")
async def upsert_dominio(dominio: str, request: Request, payload=Depends(verificar_manutencao)):
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
    if tabela == "manutencao.familias" and (d.get("classe") or "").strip():
        await ajard_query("UPDATE manutencao.familias SET classe=%s, ativo=true WHERE codigo=%s",
                          ((d.get("classe") or "").strip().upper(), cod), fetch="none")
    return {"ok": True}


# ══════════════════════════════════════════════════════════════════════
# (28/08/2026) PACOTE C — MOTOR PREVENTIVO
# Lógica ManWinWin replicada: última realização + periodicidade
# (calendário OU leitura, o que vencer) = próxima manutenção; FMD-R
# (funcionamento médio diário) projeta a DATA — mínimo 4 registros de
# leitura para o FMD valer (regra ManWinWin); OT programada existente
# do plano é respeitada (reagendamento manual da Bruna nunca é
# sobrescrito pelo motor).
# ══════════════════════════════════════════════════════════════════════

def _resolver_periodo(nome, qtd):
    """Traduz período parametrizado → (modo, total, unidade).
    modo 'leitura' (h/km) ou 'calendario' (dias). Entende dois formatos:
    unidade pura + qtd do plano ('Meses' × 3) e número embutido no nome
    ('500 HORAS', 'A CADA 15 MIL KM'). Indecifrável → (None, 0, None)."""
    import re as _re
    import unicodedata as _ud
    s = ''.join(c for c in _ud.normalize('NFD', str(nome or '')) if _ud.category(c) != 'Mn').upper()
    try:
        q = float(str(qtd).replace(',', '.')) if qtd not in (None, '') else 0.0
    except (TypeError, ValueError):
        q = 0.0
    m = _re.search(r'(\d+[.,]?\d*)', s)
    emb = float(m.group(1).replace(',', '.')) if m else 0.0
    mil = 1000.0 if 'MIL' in s else 1.0
    if 'KM' in s or 'HORA' in s or s.strip() in ('H', 'HS', 'HRS'):
        unidade = 'km' if 'KM' in s else 'h'
        total = (emb or 1.0) * mil * (q or 1.0)
        if not emb and mil == 1.0 and not q:
            return (None, 0, None)
        return ('leitura', total, unidade)
    dias_uni = None
    if 'ANO' in s or 'ANUAL' in s: dias_uni = 365
    elif 'SEMESTR' in s: dias_uni = 182
    elif 'TRIMESTR' in s: dias_uni = 91
    elif 'QUINZEN' in s: dias_uni = 15
    elif 'SEMAN' in s: dias_uni = 7
    elif 'MES' in s or 'MENSAL' in s: dias_uni = 30
    elif 'DIA' in s or s.strip() == 'D': dias_uni = 1
    if dias_uni is None:
        return (None, 0, None)
    total = dias_uni * (emb or 1.0) * (q or 1.0)
    return ('calendario', total, 'd')


def _resolver_descricao(descricao):
    """Fallback da migração ManWinWin: nos planos por leitura (código 6) a
    quantidade não migrou para periodo_qtd — mas sobreviveu na descrição
    ('Revisão 500 horas', 'Troca de óleo 15000 km', 'TRIMESTRAL')."""
    import re as _re
    import unicodedata as _ud
    s = ''.join(c for c in _ud.normalize('NFD', str(descricao or '')) if _ud.category(c) != 'Mn').upper()
    m = _re.search(r'(\d+[.,]?\d*)\s*(MIL\s*)?(KM)\b', s)
    if m:
        return ('leitura', float(m.group(1).replace(',', '.')) * (1000 if m.group(2) else 1), 'km')
    m = _re.search(r'(\d+[.,]?\d*)\s*(MIL\s*)?(HORAS?|HRS?|HS)\b', s)
    if m:
        return ('leitura', float(m.group(1).replace(',', '.')) * (1000 if m.group(2) else 1), 'h')
    m = _re.search(r'(\d+[.,]?\d*)\s*MES', s)
    if m:
        return ('calendario', float(m.group(1).replace(',', '.')) * 30, 'd')
    m = _re.search(r'(\d+[.,]?\d*)\s*ANO', s)
    if m:
        return ('calendario', float(m.group(1).replace(',', '.')) * 365, 'd')
    for pal, dias in (('TRIMESTRAL', 91), ('SEMESTRAL', 182), ('ANUAL', 365),
                      ('MENSAL', 30), ('QUINZENAL', 15), ('SEMANAL', 7)):
        if pal in s:
            return ('calendario', dias, 'd')
    return (None, 0, None)


def _resolver_plano(periodo_nome, periodo_qtd, descricao):
    """Cascata: período parametrizado resolve → usa; senão lê a descrição."""
    r = _resolver_periodo(periodo_nome, periodo_qtd)
    if r[0] and r[1] > 0:
        return r
    return _resolver_descricao(descricao)


JANELA_FMD_DIAS = 90   # (24/09/2026) janela ÚNICA do FMD-R: semáforo, Registo Funcionamento, ficha, reprogramação


def _fmd_num(v):
    try:
        n = float(str(v).replace(",", "."))
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def _fmd_serie(serie):
    """(24/09/2026) Regra ÚNICA do FMD-R (ManWinWin): série diária [(data, maior leitura do dia)],
    em qualquer ordem; vale com ≥4 dias e leitura crescente. Usada pelo semáforo/previsões,
    Registo Funcionamento e ficha do Objecto."""
    s = sorted(serie)
    if len(s) < 4:
        return None
    (d0, l0), (d1, l1) = s[0], s[-1]
    if l1 <= l0:
        return None
    return round((l1 - l0) / max(1, (d1 - d0).days), 2)


async def _fmd_manual(eq_id: str = None):
    """FMD informado à mão na ficha (☐ Calcular por registos + valor > 0) — ManWinWin."""
    sql = """SELECT id, garantia->'fmd'->>'valor' AS v FROM operacional.equipamentos
             WHERE (garantia->'fmd'->>'calc') = 'false'"""
    rows = (await ajard_query(sql + " AND id=%s", (eq_id,)) if eq_id else await ajard_query(sql)) or []
    return {str(r["id"]): n for r in rows if (n := _fmd_num(r["v"]))}


async def _fmd_por_equipamento():
    """(24/09/2026) FMD EM USO por equipamento — o que move previsões e ciclo:
    manual da ficha quando desmarcado 'Calcular por registos'; senão o FMD-R dos
    registos (v_leituras, JANELA_FMD_DIAS, regra _fmd_serie). None = sem projeção
    de data (o vencimento por leitura continua)."""
    await _garantir_leituras()
    rows = await ajard_query(
        """SELECT equipamento_id, data::date AS d, MAX(leitura) AS leitura
           FROM operacional.v_leituras
           WHERE data >= now() - make_interval(days => %s) AND leitura > 0
           GROUP BY equipamento_id, data::date""", (JANELA_FMD_DIAS,))
    por_eq = {}
    for r in rows:
        por_eq.setdefault(str(r["equipamento_id"]), []).append((r["d"], float(r["leitura"])))
    fmd = {k: v for k, regs in por_eq.items() if (v := _fmd_serie(regs))}
    fmd.update(await _fmd_manual())
    return fmd


@router.get("/manutencao/api/previsoes")
async def previsoes_frota(_auth=Depends(verificar_manutencao)):
    return await _calcular_previsoes()


async def _calcular_previsoes():
    await _garantir_planos_cols()
    """Trabalhos previstos: um item por plano ativo, com próxima manutenção
    calculada, data projetada pelo FMD e status (vencida / a_vencer /
    programada / ok / sem_base / sem_periodo)."""
    from datetime import date as _date, timedelta as _td
    await _garantir_colunas_ot()
    await _garantir_dominios()
    for _cod, _nom in (("1", "Semanas"), ("2", "Meses"), ("6", "Registros (horímetro/km)")):
        await ajard_query(
            """INSERT INTO manutencao.periodos (codigo, nome, ativo)
               VALUES (%s, %s, true) ON CONFLICT (codigo) DO NOTHING""",
            (_cod, _nom), fetch="none")
    hoje = _date.today()
    planos = await ajard_query(
        """SELECT p.id, p.equipamento_id, p.codigo, p.descricao, p.tipo_trabalho,
                  p.periodo_codigo, p.periodo_qtd, p.custo_previsto, p.ultima_data, p.ultima_leitura, p.criado_em,
                  p.plano_proximo_codigo, COALESCE(p.data_fixa,false) AS data_fixa, COALESCE(p.registro_fixo,false) AS registro_fixo,
                  e.codigo AS eq_codigo, e.descricao AS eq_desc,
                  e.horimetro_atual, e.km_atual, e.medicao,
                  per.nome AS periodo_nome
           FROM manutencao.planos p
           JOIN operacional.equipamentos e
             ON e.id = p.equipamento_id AND e.ativo = true
                AND COALESCE(e.categoria,'') <> 'apoio'
           LEFT JOIN manutencao.periodos per ON per.codigo = p.periodo_codigo
           WHERE p.ativo = true
           ORDER BY e.codigo, p.codigo""")
    ults = await ajard_query(
        """SELECT DISTINCT ON (plano_id) plano_id, data_conclusao::date AS dt,
                  horimetro_na_abertura AS leitura
           FROM manutencao.ot
           WHERE status = 'concluida' AND ativo = true AND plano_id IS NOT NULL
           ORDER BY plano_id, data_conclusao DESC""")
    ult = {str(u["plano_id"]): u for u in ults}
    # (15/09/2026) Baseline manual da FMP ("Última execução") quando não há OT concluída —
    # é o que liga os 105 planos migrados do ManWinWin à régua viva do horímetro.
    for p in planos:
        pid = str(p["id"])
        if pid not in ult and (p.get("ultima_data") or p.get("ultima_leitura") is not None):
            ult[pid] = {"plano_id": pid, "dt": p.get("ultima_data"), "leitura": p.get("ultima_leitura")}
        elif pid in ult:
            # (13/10/2026) OT concluída sem leitura na abertura (ou sem data) não pode deixar
            # a FMP "sem base" quando a ficha tem a última execução informada
            u = ult[pid]
            if u.get("leitura") is None and p.get("ultima_leitura") is not None:
                u["leitura"] = p.get("ultima_leitura")
            if not u.get("dt") and p.get("ultima_data"):
                u["dt"] = p.get("ultima_data")
    progs = await ajard_query(
        """SELECT DISTINCT ON (plano_id) plano_id, id, numero, data_prevista, horimetro_previsto
           FROM manutencao.ot
           WHERE status = 'programada' AND ativo = true AND plano_id IS NOT NULL
           ORDER BY plano_id, data_prevista NULLS LAST""")
    prog = {str(o["plano_id"]): o for o in progs}
    fmd = await _fmd_por_equipamento()
    # (13/10/2026) leitura VIVA por objeto: maior leitura da fonte única (partes, checklists,
    # abastecimentos) ou o cadastro, o que for maior — e km_atual para quem mede em km
    # (antes lia horimetro_atual para todos, inclusive caminhões)
    vivas = {}
    try:
        for r in await ajard_query(
                "SELECT equipamento_id, MAX(leitura) AS l FROM operacional.v_leituras WHERE leitura > 0 GROUP BY equipamento_id"):
            vivas[str(r["equipamento_id"])] = float(r["l"] or 0)
    except Exception as e:
        print(f"[Previsões] leituras vivas indisponíveis: {e}")

    itens = []
    for p in planos:
        pid = str(p["id"])
        modo, total, unidade = _resolver_plano(p["periodo_nome"] or p["periodo_codigo"], p["periodo_qtd"], p["descricao"])
        if modo == "leitura":
            # (13/10/2026) o período genérico "Registros (horímetro/km)" tem "KM" no nome e
            # rotulava tudo como KM — a unidade é a medição do objeto
            unidade = "km" if (p.get("medicao") == "km") else "h"
        u = ult.get(pid)
        base_dt = (u["dt"] if u and u["dt"] else (p["criado_em"].date() if p["criado_em"] else hoje))
        base_leitura = (float(u["leitura"]) if u and u["leitura"] is not None else None)
        cad = p["km_atual"] if p.get("medicao") == "km" else p["horimetro_atual"]
        atual = max(float(cad or 0), vivas.get(str(p["equipamento_id"]), 0.0))
        eq_fmd = fmd.get(str(p["equipamento_id"]))
        item = {"plano_id": pid, "plano_codigo": p["codigo"], "plano_descricao": p["descricao"],
                "equipamento_id": str(p["equipamento_id"]), "eq_codigo": p["eq_codigo"],
                "eq_descricao": p["eq_desc"], "tipo_trabalho": p["tipo_trabalho"],
                "periodo": p["periodo_nome"] or p["periodo_codigo"], "periodo_qtd": (float(p["periodo_qtd"]) if p["periodo_qtd"] is not None else None),
                "modo": modo, "unidade": unidade, "custo_previsto": (float(p["custo_previsto"]) if p["custo_previsto"] is not None else None),
                "proxima_fmp": p.get("plano_proximo_codigo"), "data_fixa": bool(p.get("data_fixa")), "registro_fixo": bool(p.get("registro_fixo")),
                "ultima_data": (base_dt.isoformat() if u else None),
                "ultima_leitura": base_leitura, "leitura_atual": atual or None,
                "fmd": eq_fmd, "proxima_data": None, "leitura_alvo": None,
                "restante": None, "status": "ok", "ot": None}
        o = prog.get(pid)
        if o:
            item["ot"] = {"id": str(o["id"]), "numero": o["numero"],
                          "data_prevista": (o["data_prevista"].isoformat() if o["data_prevista"] else None),
                          "horimetro_previsto": (float(o["horimetro_previsto"]) if o["horimetro_previsto"] is not None else None)}
        if modo is None:
            item["status"] = "sem_periodo"
        elif modo == "calendario":
            prox = base_dt + _td(days=int(total))
            item["proxima_data"] = prox.isoformat()
            rest = (prox - hoje).days
            item["restante"] = rest
            item["status"] = "vencida" if rest < 0 else ("a_vencer" if rest <= 7 else "ok")
        else:
            if base_leitura is None:
                item["status"] = "sem_base"
            else:
                alvo = base_leitura + total
                item["leitura_alvo"] = alvo
                rest = alvo - atual
                item["restante"] = round(rest, 1)
                if eq_fmd:
                    # (13/10/2026) vencida também ganha data (no passado): "quando venceu"
                    item["proxima_data"] = (hoje + _td(days=int(rest / eq_fmd))).isoformat()
                item["status"] = "vencida" if rest <= 0 else ("a_vencer" if rest <= 50 else "ok")
        if o and item["status"] in ("vencida", "a_vencer", "ok"):
            item["status"] = "programada"
        itens.append(item)

    peso = {"vencida": 0, "a_vencer": 1, "programada": 2, "ok": 3, "sem_base": 4, "sem_periodo": 5}
    itens.sort(key=lambda x: (peso.get(x["status"], 9), x["proxima_data"] or "9999", x["eq_codigo"]))
    return {"gerado_em": hoje.isoformat(), "total": len(itens),
            "vencidas": sum(1 for x in itens if x["status"] == "vencida"),
            "a_vencer": sum(1 for x in itens if x["status"] == "a_vencer"),
            "sem_base": sum(1 for x in itens if x["status"] == "sem_base"),
            "itens": itens}


async def _gerar_ot_previsao(plano_id, d, payload):
    """Transforma previsão em OT PROGRAMADA herdando a FMP (plano_id,
    tipo de trabalho, descrição) com data/leitura previstas. Recusa se o
    plano já tem OT programada ativa (respeito ao agendamento existente)."""
    from datetime import date as _date
    await _garantir_colunas_ot()
    ja = await ajard_query(
        """SELECT numero FROM manutencao.ot
           WHERE plano_id=%s AND status='programada' AND ativo=true LIMIT 1""",
        (plano_id,), fetch="one")
    if ja:
        raise HTTPException(status_code=400, detail=f"Plano já tem OT programada ({ja['numero']})")
    p = await ajard_query(
        """SELECT p.*, e.id AS eq_id FROM manutencao.planos p
           JOIN operacional.equipamentos e ON e.id = p.equipamento_id AND e.ativo = true
           WHERE p.id=%s AND p.ativo=true""", (plano_id,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
    eq = await ajard_query(
        "SELECT id, codigo, categoria, horimetro_atual FROM operacional.equipamentos WHERE id=%s AND ativo=true",
        (str(p["equipamento_id"]),), fetch="one")
    seq = await _proximo_seq_ot()
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"
    uid = await _usuario_id(payload)
    corpo = {"equipamento_id": str(p["equipamento_id"]),
             "descricao": f"{p['codigo']} — {p['descricao'] or ''}".strip(" —"),
             "tipo_trabalho": p["tipo_trabalho"],
             "plano_id": plano_id,
             "data_prevista": _data_ok(d.get("data_prevista")),
             "horimetro_previsto": d.get("horimetro_previsto"),
             "prioridade": d.get("prioridade", "media")}
    return await _inserir_ot(corpo, eq, uid, numero, ano, seq)


@router.post("/manutencao/api/previsoes/{plano_id}/gerar-ot")
async def previsao_gerar_ot(plano_id: str, request: Request, payload=Depends(verificar_manutencao)):
    try:
        d = await request.json()
    except Exception:
        d = {}
    return await _gerar_ot_previsao(plano_id, d, payload)


async def _garantir_ot_programada(plano_id, payload):
    """(23/09/2026) Encadeamento MWW: uma FMP com última execução tem sempre UMA OT
    programada (a próxima ocorrência). Chamado ao definir a baseline e ao concluir a OT
    preventiva anterior. Idempotente: se já existe OT programada, não faz nada."""
    prev = await _calcular_previsoes()
    it = next((i for i in prev["itens"] if str(i["plano_id"]) == str(plano_id)), None)
    if not it:
        return None
    if it.get("ot"):
        # (13/10/2026) Já existe OT programada (ex.: aberta à mão pela FMP antes da baseline):
        # ela herda a previsão recalculada — a não ser que a programação tenha sido
        # alterada no Avançado (prog_alterada), que é soberana.
        o = await ajard_query("SELECT id, prog_alterada FROM manutencao.ot WHERE id=%s AND ativo=true",
                              (it["ot"]["id"],), fetch="one")
        if o and not o.get("prog_alterada") and (it.get("proxima_data") or it.get("leitura_alvo") is not None):
            await ajard_query(
                """UPDATE manutencao.ot SET data_prevista=%s, horimetro_previsto=%s, atualizado_em=now()
                   WHERE id=%s""", (it.get("proxima_data"), it.get("leitura_alvo"), o["id"]), fetch="none")
        return None
    if it["status"] not in ("ok", "a_vencer", "vencida"):
        return None
    return await _gerar_ot_previsao(plano_id, {
        "data_prevista": it.get("proxima_data"),
        "horimetro_previsto": it.get("leitura_alvo"),
        "prioridade": "alta" if it["status"] == "vencida" else "media"}, payload)


@router.post("/manutencao/api/ots/atualizar-datas")
async def ots_atualizar_datas(payload=Depends(verificar_manutencao)):
    """(13/10/2026) ManWinWin "Actualizar Datas OT": recalcula data/horímetro previstos
    de TODAS as OTs programadas ligadas a uma FMP a partir da previsão viva (baseline +
    período + leituras + FMD). Respeita a programação alterada à mão (prog_alterada).
    Também é chamado automaticamente ao abrir o módulo — o botão existe para o usuário
    forçar na hora, depois de lançar leituras."""
    from datetime import date as _date
    await _garantir_colunas_ot()
    prev = await _calcular_previsoes()
    ots = {str(o["id"]): o for o in await ajard_query(
        """SELECT id, numero, plano_id, data_prevista, horimetro_previsto, COALESCE(prog_alterada,false) AS prog_alterada
           FROM manutencao.ot WHERE status='programada' AND ativo=true AND plano_id IS NOT NULL""")}
    atualizadas, mantidas = [], 0
    for it in prev["itens"]:
        o = it.get("ot") and ots.get(str(it["ot"]["id"]))
        if not o:
            continue
        if o["prog_alterada"]:
            mantidas += 1
            continue
        nd, nh = it.get("proxima_data"), it.get("leitura_alvo")
        if nd is None and nh is None:
            continue
        nd_dt = _date.fromisoformat(nd) if nd else None
        if (o["data_prevista"] == nd_dt) and ((float(o["horimetro_previsto"]) if o["horimetro_previsto"] is not None else None) == nh):
            continue
        await ajard_query("UPDATE manutencao.ot SET data_prevista=%s, horimetro_previsto=%s, atualizado_em=now() WHERE id=%s",
                          (nd_dt, nh, o["id"]), fetch="none")
        atualizadas.append({"ot": o["numero"], "equipamento": it["eq_codigo"], "data_prevista": nd, "horimetro_previsto": nh})
    return {"atualizadas": atualizadas, "total": len(atualizadas), "mantidas_manuais": mantidas}


@router.post("/manutencao/api/previsoes/gerar")
async def previsoes_gerar_lote(payload=Depends(verificar_manutencao)):
    """(15/09/2026) Botão "Gerar preventivas" (ManWinWin): cria a OT programada de toda
    FMP vencida ou a vencer que ainda não tem OT. Uma por plano; falhas não param o lote."""
    prev = await _calcular_previsoes()
    geradas, puladas = [], []
    for it in prev["itens"]:
        # (23/09/2026) Modelo MWW: toda FMP com baseline tem UMA OT programada (em dia, a vencer ou vencida)
        if it["status"] not in ("ok", "vencida", "a_vencer") or it.get("ot"):
            continue
        try:
            r = await _gerar_ot_previsao(it["plano_id"], {
                "data_prevista": it.get("proxima_data"),
                "horimetro_previsto": it.get("leitura_alvo"),
                "prioridade": "alta" if it["status"] == "vencida" else "media"}, payload)
            geradas.append({"plano": it["plano_codigo"], "equipamento": it["eq_codigo"], "ot": (r or {}).get("numero")})
        except HTTPException as ex:
            puladas.append({"plano": it["plano_codigo"], "motivo": ex.detail})
    return {"geradas": geradas, "puladas": puladas, "total_geradas": len(geradas)}


async def _copiar_plano(p, eq_id, desc=None):
    """Cópia de uma FMP para um objecto: cabeçalho, período, TDM, custo, procedimento,
    mão de obra, peças, outros, ferramentas — próximo código do tipo no destino.
    Baseline (última execução) não é copiada."""
    eq = await ajard_query("SELECT id, codigo FROM operacional.equipamentos WHERE id=%s AND ativo=true", (eq_id,), fetch="one")
    if not eq:
        return None
    tt = p["tipo_trabalho"] or "PS"
    seq = await ajard_query(
        """SELECT COALESCE(MAX(CAST(NULLIF(split_part(codigo, '-', 2), '') AS INT)), 0) + 1 AS n
           FROM manutencao.planos WHERE equipamento_id=%s AND codigo LIKE %s""",
        (eq_id, f"{tt}-%"), fetch="one")
    codigo = f"{tt}-{int(seq['n']):02d}"
    row = await ajard_query(
        """INSERT INTO manutencao.planos
             (equipamento_id, codigo, descricao, tipo_trabalho, periodo_codigo, periodo_qtd,
              tempo_horas, hh_previsto, custo_previsto, procedimento, preparacao_codigo,
              mao_obra, pecas, outros, ferramentas)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           RETURNING id, codigo""",
        (eq_id, codigo, (desc or p["descricao"] or "").strip(), tt, p["periodo_codigo"], p["periodo_qtd"],
         p["tempo_horas"], p["hh_previsto"], p["custo_previsto"], p["procedimento"], p.get("preparacao_codigo"),
         p.get("mao_obra"), p.get("pecas"), p.get("outros"), p.get("ferramentas")), fetch="one")
    return {"equipamento": eq["codigo"], "codigo": row["codigo"], "id": row["id"], "origem": p["codigo"]}


@router.post("/manutencao/api/planos-repetir")
async def repetir_planos(request: Request, _auth=Depends(verificar_manutencao)):
    """(16/09/2026) "Repetir Planos Manutenção" (ManWinWin): N FMPs de origem × M objectos
    de destino, numa chamada. Body: {planos: [ids], equipamentos: [ids]}."""
    await _garantir_planos_cols()
    d = await request.json()
    planos = d.get("planos") or []
    destinos = d.get("equipamentos") or []
    if not planos or not destinos:
        raise HTTPException(status_code=400, detail="Informe as FMPs de origem e os objetos de destino")
    criados = []
    for pid in planos:
        p = await ajard_query("SELECT * FROM manutencao.planos WHERE id=%s", (pid,), fetch="one")
        if not p:
            continue
        for eq_id in destinos:
            r = await _copiar_plano(p, eq_id)
            if r:
                criados.append(r)
    return {"criados": criados, "total": len(criados)}


@router.patch("/manutencao/api/planos/{plano_id}/baseline")
async def plano_baseline(plano_id: str, request: Request, _auth=Depends(verificar_manutencao)):
    """Última execução manual da FMP (data + leitura) — baseline do ciclo quando não há OT concluída."""
    await _garantir_planos_cols()
    d = await request.json()
    dt = _data_ok(d.get("ultima_data"), "Última execução")
    le = d.get("ultima_leitura")
    le = float(str(le).replace(",", ".")) if le not in (None, "") else None
    # (16/09/2026) Coerência com o objeto: não pode ter executado antes de existir,
    # nem com leitura acima do horímetro/km atual.
    eq = await ajard_query(
        """SELECT e.codigo, e.medicao, e.data_aquisicao, e.horimetro_atual, e.km_atual
           FROM manutencao.planos p JOIN operacional.equipamentos e ON e.id = p.equipamento_id
           WHERE p.id=%s""", (plano_id,), fetch="one")
    if eq:
        if dt and eq.get("data_aquisicao") and str(dt) < str(eq["data_aquisicao"])[:10]:
            raise HTTPException(status_code=400,
                detail=f"Última execução ({dt[8:10]}/{dt[5:7]}/{dt[:4]}) anterior à aquisição do objeto ({str(eq['data_aquisicao'])[8:10]}/{str(eq['data_aquisicao'])[5:7]}/{str(eq['data_aquisicao'])[:4]})")
        vivo = float((eq.get("km_atual") if eq.get("medicao") == "km" else eq.get("horimetro_atual")) or 0)
        if le is not None and vivo > 0 and le > vivo:
            raise HTTPException(status_code=400,
                detail=f"Leitura da última execução ({le:g}) maior que a leitura atual do objeto ({vivo:g})")
    await ajard_query("UPDATE manutencao.planos SET ultima_data=%s, ultima_leitura=%s WHERE id=%s",
                      (dt, le, plano_id), fetch="none")
    ot = None
    try:
        ot = await _garantir_ot_programada(plano_id, _auth)
    except HTTPException:
        ot = None
    return {"ok": True, "ot": (ot or {}).get("numero")}


@router.post("/manutencao/api/ots/{ot_id}/novo-ciclo")
async def ot_novo_ciclo(ot_id: str, payload=Depends(verificar_manutencao)):
    """(28/08/2026) RNC do ManWinWin — Reprogramar OT para Novo Ciclo:
    rola a programação da OT (programada, ligada a um plano) um período à
    frente. Leitura: alvo += período. Calendário: data += período."""
    from datetime import date as _date, timedelta as _td
    await _garantir_colunas_ot()
    o = await ajard_query(
        """SELECT o.id, o.numero, o.status, o.plano_id, o.data_prevista, o.horimetro_previsto,
                  p.descricao, p.periodo_codigo, p.periodo_qtd,
                  per.nome AS periodo_nome
           FROM manutencao.ot o
           JOIN manutencao.planos p ON p.id = o.plano_id
           LEFT JOIN manutencao.periodos per ON per.codigo = p.periodo_codigo
           WHERE o.id=%s AND o.ativo=true""", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada ou sem plano vinculado")
    if o["status"] != "programada":
        raise HTTPException(status_code=400, detail="Só OT programada pode rolar de ciclo")
    modo, total, unidade = _resolver_plano(o["periodo_nome"] or o["periodo_codigo"], o["periodo_qtd"], o["descricao"])
    if not modo:
        raise HTTPException(status_code=400, detail="Período do plano indecifrável — complete a ficha da FMP")
    novo_hor, nova_data = o["horimetro_previsto"], o["data_prevista"]
    if modo == "leitura":
        if novo_hor is None:
            raise HTTPException(status_code=400, detail="OT sem horímetro previsto — defina antes de rolar o ciclo")
        novo_hor = float(novo_hor) + total
        if nova_data:
            fmd = (await _fmd_por_equipamento()).get(str((await ajard_query(
                "SELECT equipamento_id FROM manutencao.ot WHERE id=%s", (ot_id,), fetch="one"))["equipamento_id"]))
            if fmd:
                nova_data = _date.today() + _td(days=int(max(total, 0) / fmd))
    else:
        base = nova_data or _date.today()
        nova_data = base + _td(days=int(total))
    await ajard_query(
        "UPDATE manutencao.ot SET data_prevista=%s, horimetro_previsto=%s, prog_alterada=false WHERE id=%s",
        (nova_data, novo_hor, ot_id), fetch="none")
    uid = await _usuario_id(payload)
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,'programada','programada',%s,%s)""",
        (ot_id, f"Reprogramada para novo ciclo (+{int(total)} {unidade})", uid), fetch="none")
    return {"ok": True, "numero": o["numero"],
            "data_prevista": (nova_data.isoformat() if nova_data else None),
            "horimetro_previsto": novo_hor}


@router.get("/manutencao/api/planos/{pid}")
async def obter_plano(pid: str, _auth=Depends(verificar_manutencao)):
    """Plano completo (Planejado da OT herdado da FMP)."""
    await _garantir_planos_cols()
    await _garantir_biblioteca()
    p = await ajard_query(
        """SELECT id, codigo, descricao, tipo_trabalho, periodo_codigo, periodo_qtd,
                  tempo_horas, custo_previsto, procedimento,
                  mao_obra, pecas, outros, ferramentas, ultima_data, ultima_leitura, ativo
           FROM manutencao.planos WHERE id=%s""", (pid,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
    return dict(p)


# ════════════════════════════════════════════════════════════════════════
# (27/09/2026) PROJECTOS — consolidação: custo e prazo das OTs ligadas
# Custo da OT = custo do fechamento quando existe; senão o apurado nos registros
# (peças baixadas + mão de obra + outros), a mesma regra da aba Custos da OT.
# ════════════════════════════════════════════════════════════════════════
_SQL_OTS_PROJECTO = """
    SELECT o.id, o.numero, o.descricao, o.status, o.tipo_trabalho, o.projecto_id,
           o.data_abertura, o.data_conclusao, o.custo_total,
           e.codigo AS equipamento_codigo, fo.nome AS fornecedor_nome,
           COALESCE((SELECT SUM((CASE WHEN m.tipo = 'entrada' THEN -1 ELSE 1 END) * m.quantidade
                                * COALESCE(m.custo_unitario, p.custo_medio, 0))
                       FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id = m.peca_id
                      WHERE m.ot_id = o.id AND m.tipo IN ('saida','entrada','aplicacao_direta')), 0) AS pecas,
           COALESCE((SELECT SUM(x.horas * COALESCE(x.custo_hora, 0)) FROM manutencao.ot_mao_obra x
                      WHERE x.ot_id = o.id AND x.ativo = true), 0) AS mao_obra,
           COALESCE((SELECT SUM(x.valor) FROM manutencao.ot_outros x
                      WHERE x.ot_id = o.id AND x.ativo = true), 0) AS outros
      FROM manutencao.ot o
      LEFT JOIN operacional.equipamentos e ON e.id = o.equipamento_id
      LEFT JOIN public.fornecedores fo ON fo.id = o.fornecedor_id
     WHERE o.ativo = true AND o.projecto_id IS NOT NULL {filtro}
     ORDER BY o.data_abertura"""


async def _ots_de_projectos(projecto_id: str = None):
    await _garantir_mao_obra()
    await _garantir_tabela("ot-outros")
    await ajard_query("ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
                      fetch="none")
    sql = _SQL_OTS_PROJECTO.format(filtro="AND o.projecto_id = %s" if projecto_id else "")
    rows = (await ajard_query(sql, (projecto_id,)) if projecto_id else await ajard_query(sql)) or []
    out = []
    for r in rows:
        apurado = round(float(r["pecas"] or 0) + float(r["mao_obra"] or 0) + float(r["outros"] or 0), 2)
        fech = float(r["custo_total"]) if r["custo_total"] is not None else None
        out.append({
            "id": str(r["id"]), "projecto_id": str(r["projecto_id"]), "numero": r["numero"],
            "descricao": r["descricao"], "status": r["status"], "tipo_trabalho": r["tipo_trabalho"],
            "equipamento_codigo": r["equipamento_codigo"], "fornecedor_nome": r["fornecedor_nome"],
            "data_abertura": r["data_abertura"].isoformat() if r["data_abertura"] else None,
            "data_conclusao": r["data_conclusao"].isoformat() if r["data_conclusao"] else None,
            "pecas": round(float(r["pecas"] or 0), 2), "mao_obra": round(float(r["mao_obra"] or 0), 2),
            "outros": round(float(r["outros"] or 0), 2), "apurado": apurado, "fechamento": fech,
            "custo": fech if fech is not None else apurado})
    return out


def _resumo_projecto(pr, ots):
    """Totais de um projecto: OTs, concluídas, custo e dias (início→fim, ou até hoje)."""
    from datetime import date as _date
    datas_ini = [o["data_abertura"][:10] for o in ots if o["data_abertura"]]
    datas_fim = [o["data_conclusao"][:10] for o in ots if o["data_conclusao"]]
    ini = pr.get("data_inicio") or (min(datas_ini) if datas_ini else None)
    todas_fechadas = ots and all(o["status"] in ("concluida", "cancelada") for o in ots)
    fim = pr.get("data_fim") or (max(datas_fim) if todas_fechadas and datas_fim else None)
    d0 = _date.fromisoformat(str(ini)[:10]) if ini else None
    d1 = _date.fromisoformat(str(fim)[:10]) if fim else _date.today()
    return {"n_ots": len(ots), "n_concluidas": sum(1 for o in ots if o["status"] == "concluida"),
            "n_canceladas": sum(1 for o in ots if o["status"] == "cancelada"),
            "custo": round(sum(o["custo"] for o in ots if o["status"] != "cancelada"), 2),
            "custo_fechado": round(sum(o["custo"] for o in ots if o["status"] == "concluida"), 2),
            "externo": round(sum(o["custo"] for o in ots if o["fornecedor_nome"] and o["status"] != "cancelada"), 2),
            "inicio": str(ini)[:10] if ini else None, "fim": str(fim)[:10] if fim else None,
            "em_aberto": not fim, "dias": (d1 - d0).days + 1 if d0 else None}


@router.get("/manutencao/api/projectos-resumo")
async def projectos_resumo(_auth=Depends(verificar_manutencao)):
    """Lista de Projetos: custo, OTs e dias de cada um (colunas da tela)."""
    await _garantir_tabela("projectos")
    prs = await ajard_query(
        "SELECT id, data_inicio, data_fim FROM manutencao.projectos WHERE ativo = true") or []
    ots = await _ots_de_projectos()
    return {str(pr["id"]): _resumo_projecto(
        {"data_inicio": pr["data_inicio"], "data_fim": pr["data_fim"]},
        [o for o in ots if o["projecto_id"] == str(pr["id"])]) for pr in prs}


@router.get("/manutencao/api/projectos/{projecto_id}/ots")
async def projecto_ots(projecto_id: str, _auth=Depends(verificar_manutencao)):
    """Detalhe do Projeto: as OTs ligadas com o custo de cada uma + totais."""
    await _garantir_tabela("projectos")
    pr = await ajard_query("SELECT * FROM manutencao.projectos WHERE id = %s", (projecto_id,), fetch="one")
    if not pr:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    ots = await _ots_de_projectos(projecto_id)
    return {"projecto": {"id": str(pr["id"]), "codigo": pr["codigo"], "nome": pr["nome"], "status": pr["status"]},
            "ots": ots,
            "resumo": _resumo_projecto({"data_inicio": pr["data_inicio"], "data_fim": pr["data_fim"]}, ots)}


@router.get("/manutencao/api/ots/{ot_id}/consumos")
async def ot_consumos(ot_id: str, _auth=Depends(verificar_manutencao)):
    """(28/08/2026) Fatia 1 do Planejado × Realizado — o REALIZADO da OT:
    movimentações de estoque vinculadas (saída consome; entrada = devolução,
    abate). Custo da linha = snapshot gravado no movimento (fallback:
    custo médio atual do acervo)."""
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")
    rows = await ajard_query(
        """SELECT m.id, m.tipo, m.quantidade, m.custo_unitario, m.criado_em, m.observacao, m.rubrica,
                  m.documento_numero, m.documento_tipo, fo.nome AS fornecedor_nome,
                  p.codigo AS peca_codigo, p.descricao AS peca_descricao, p.unidade,
                  p.custo_medio,
                  ao.codigo AS almox_origem, ad.codigo AS almox_destino,
                  u.nome AS usuario
           FROM manutencao.movimentacoes m
           JOIN manutencao.pecas p ON p.id = m.peca_id
           LEFT JOIN manutencao.almoxarifados ao ON ao.id = m.almox_origem
           LEFT JOIN manutencao.almoxarifados ad ON ad.id = m.almox_destino
           LEFT JOIN public.fornecedores fo ON fo.id = m.fornecedor_id
           LEFT JOIN public.usuarios_garra u ON u.id = m.usuario_id
           WHERE m.ot_id = %s AND m.tipo IN ('saida', 'entrada', 'aplicacao_direta')
           ORDER BY m.criado_em""", (ot_id,))
    itens, total = [], 0.0
    for r in rows:
        custo = float(r["custo_unitario"]) if r["custo_unitario"] is not None \
            else (float(r["custo_medio"]) if r["custo_medio"] is not None else 0.0)
        qtd = float(r["quantidade"] or 0)
        sinal = -1 if r["tipo"] == "entrada" else 1
        linha = round(sinal * qtd * custo, 2)
        total += linha
        itens.append({
            "id": str(r["id"]), "tipo": r["tipo"],
            "data": (r["criado_em"].isoformat() if r["criado_em"] else None),
            "peca_codigo": r["peca_codigo"], "peca_descricao": r["peca_descricao"],
            "unidade": r["unidade"] or "UN", "quantidade": qtd,
            "custo_unitario": custo, "total": linha,
            "almox": r["almox_origem"] or r["almox_destino"], "rubrica": r["rubrica"],
            "documento": r.get("documento_numero"), "documento_tipo": r.get("documento_tipo"), "fornecedor_nome": r.get("fornecedor_nome"),
            "usuario": r["usuario"], "observacao": r["observacao"]})
    return {"itens": itens, "total": round(total, 2)}


@router.patch("/manutencao/api/ots/{ot_id}/consumos/{mov_id}")
async def ot_consumo_editar(ot_id: str, mov_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(19/10/2026) Editar um item baixado na OT (✏️ no Realizado): quantidade, custo unitário,
    documento, fornecedor e conta. Saída do armazém: a diferença de quantidade acerta o estoque."""
    await garantir_nucleo()
    d = await request.json()
    m = await ajard_query(
        "SELECT * FROM manutencao.movimentacoes WHERE id=%s AND ot_id=%s AND tipo IN ('saida','entrada','aplicacao_direta')",
        (mov_id, ot_id), fetch="one")
    if not m:
        raise HTTPException(status_code=404, detail="Item não encontrado nesta OT")
    sets, params = [], []
    def _num(v):
        try:
            return float(str(v).replace(",", ".")) if v not in (None, "") else None
        except ValueError:
            raise HTTPException(status_code=400, detail="Número inválido")
    # trocar a peça: saída do armazém devolve a antiga ao estoque e baixa a nova
    if (d.get("peca") or "").strip() and (d.get("peca") or "").strip() != (await ajard_query("SELECT codigo FROM manutencao.pecas WHERE id=%s", (m["peca_id"],), fetch="one") or {}).get("codigo"):
        nova = await ajard_query("SELECT id FROM manutencao.pecas WHERE codigo=%s AND ativo=true", ((d.get("peca") or "").strip(),), fetch="one")
        if not nova:
            raise HTTPException(status_code=404, detail="Peça não encontrada: " + (d.get("peca") or "").strip())
        q_old = float(m["quantidade"] or 0)
        if m["tipo"] == "saida" and m["almox_origem"]:
            q_new = _num(d.get("quantidade")) if d.get("quantidade") not in (None, "") else q_old
            s = await ajard_query("SELECT COALESCE(quantidade,0) AS q FROM manutencao.estoque WHERE peca_id=%s AND almoxarifado_id=%s", (nova["id"], m["almox_origem"]), fetch="one")
            if float((s or {}).get("q") or 0) < q_new:
                raise HTTPException(status_code=400, detail="Saldo insuficiente da nova peça no almoxarifado")
            await ajard_query("""INSERT INTO manutencao.estoque (peca_id, almoxarifado_id, quantidade) VALUES (%s,%s,%s)
                                 ON CONFLICT (peca_id, almoxarifado_id) DO UPDATE SET quantidade = manutencao.estoque.quantidade + EXCLUDED.quantidade""",
                              (m["peca_id"], m["almox_origem"], q_old), fetch="none")
            await ajard_query("UPDATE manutencao.estoque SET quantidade = quantidade - %s WHERE peca_id=%s AND almoxarifado_id=%s",
                              (q_new, nova["id"], m["almox_origem"]), fetch="none")
            sets.append("quantidade=%s"); params.append(q_new); d.pop("quantidade", None)
        sets.append("peca_id=%s"); params.append(nova["id"])
    if "quantidade" in d:
        q = _num(d["quantidade"])
        if q is None or q <= 0:
            raise HTTPException(status_code=400, detail="Quantidade deve ser positiva")
        delta = q - float(m["quantidade"] or 0)
        if m["tipo"] == "saida" and m["almox_origem"] and delta:
            if delta > 0:
                s = await ajard_query("SELECT COALESCE(quantidade,0) AS q FROM manutencao.estoque WHERE peca_id=%s AND almoxarifado_id=%s", (m["peca_id"], m["almox_origem"]), fetch="one")
                if float((s or {}).get("q") or 0) < delta:
                    raise HTTPException(status_code=400, detail="Saldo insuficiente no almoxarifado para aumentar a quantidade")
            await ajard_query("UPDATE manutencao.estoque SET quantidade = quantidade - %s WHERE peca_id=%s AND almoxarifado_id=%s",
                              (delta, m["peca_id"], m["almox_origem"]), fetch="none")
        sets.append("quantidade=%s"); params.append(q)
    if "custo_unitario" in d:
        sets.append("custo_unitario=%s"); params.append(_num(d["custo_unitario"]))
    if "documento_numero" in d:
        sets.append("documento_numero=%s"); params.append((d.get("documento_numero") or "").strip() or None)
    if "documento_tipo" in d and (d.get("documento_tipo") or "").strip():   # (21/10/2026) código do domínio Tipos de documento
        sets.append("documento_tipo=%s"); params.append(str(d["documento_tipo"]).strip())
    if "fornecedor_id" in d:
        sets.append("fornecedor_id=%s"); params.append((d.get("fornecedor_id") or "").strip() or None)
    if "rubrica" in d:
        sets.append("rubrica=%s"); params.append((d.get("rubrica") or "").strip() or None)
    if "observacao" in d:
        sets.append("observacao=%s"); params.append((d.get("observacao") or "").strip() or None)
    if not sets:
        return {"ok": True}
    params.append(mov_id)
    await ajard_query(f"UPDATE manutencao.movimentacoes SET {', '.join(sets)} WHERE id=%s", params, fetch="none")
    return {"ok": True}


@router.delete("/manutencao/api/ots/{ot_id}/consumos/{mov_id}")
async def ot_consumo_excluir(ot_id: str, mov_id: str, payload=Depends(verificar_manutencao)):
    """(19/10/2026) Excluir um item baixado na OT (padrão MWW ✕ no quadro Realizado).
    Saída do armazém → devolve a quantidade ao almoxarifado de origem; aplicação
    direta → só apaga; devolução (entrada) → retira do destino. Fica trilha no histórico."""
    await garantir_nucleo()
    o = await ajard_query("SELECT numero, status FROM manutencao.ot WHERE id=%s AND ativo=true", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    if o["status"] in ("cancelada",):
        raise HTTPException(status_code=400, detail="OT cancelada")
    m = await ajard_query(
        """SELECT m.*, p.codigo AS peca_codigo FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id=m.peca_id
           WHERE m.id=%s AND m.ot_id=%s AND m.tipo IN ('saida','entrada','aplicacao_direta')""", (mov_id, ot_id), fetch="one")
    if not m:
        raise HTTPException(status_code=404, detail="Item não encontrado nesta OT")
    qtd = float(m["quantidade"] or 0)
    if m["tipo"] == "saida" and m["almox_origem"]:
        await ajard_query(
            """INSERT INTO manutencao.estoque (peca_id, almoxarifado_id, quantidade) VALUES (%s,%s,%s)
               ON CONFLICT (peca_id, almoxarifado_id) DO UPDATE SET quantidade = manutencao.estoque.quantidade + EXCLUDED.quantidade""",
            (m["peca_id"], m["almox_origem"], qtd), fetch="none")
    elif m["tipo"] == "entrada" and m["almox_destino"]:
        await ajard_query(
            "UPDATE manutencao.estoque SET quantidade = quantidade - %s WHERE peca_id=%s AND almoxarifado_id=%s",
            (qtd, m["peca_id"], m["almox_destino"]), fetch="none")
    await ajard_query("DELETE FROM manutencao.movimentacoes WHERE id=%s", (mov_id,), fetch="none")
    uid = await _usuario_id(payload)
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, o["status"], o["status"], f"Item excluído da OT: {m['peca_codigo']} × {qtd:g} ({m['tipo']})", uid), fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/armazem")
async def lente_armazem(almox: str = None, busca: str = None, familia: str = None,
                        incluir_zerados: int = 0,
                        _auth=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    """(28/08/2026) Lente ARMAZÉM do módulo Materiais (padrão ManWinWin):
    existência física por peça×local — saldo, endereço C-P-N, mínimo e
    custo médio. Sem filtro de local = todas as existências."""
    await _garantir_estoque_cols()
    # (19/10/2026) cada condição carrega os próprios parâmetros e diz se só
    # depende da peça (vale também no modo "incluir zerados", que faz LEFT JOIN)
    conds = [("p.ativo = true", [], True), ("a.ativo = true", [], False)]
    if almox:
        conds.append(("a.codigo = %s", [almox], False))
    if busca:
        conds.append(("(" + " OR ".join(f"{_sem_acento_sql(c)} ILIKE %s" for c in ("p.codigo", "p.descricao", "p.codigo_externo", "p.codigo_fabricante")) + ")",
                      [f"%{_sem_acento(busca)}%"] * 4, True))
    if familia and familia.startswith("@"):
        conds.append((_classe_cond("p"), _classe_params(familia), True))
    elif familia:
        fe = _fam_efetiva("p")
        conds.append((f"({fe} = %s OR {fe} LIKE %s)", [familia, familia + ".%"], True))
    cond = [c for c, _, _ in conds]
    params = [x for _, ps, _ in conds for x in ps]
    if incluir_zerados:
        # catálogo inteiro com saldo 0 quando sem registro — para endereçar
        # C-P-N e definir mínimos antes da primeira entrada
        cond_p = [c for c, _, so_p in conds if so_p]
        params_p = [x for _, ps, so_p in conds if so_p for x in ps]
        rows = await ajard_query(
            f"""SELECT p.codigo, p.descricao, p.unidade, p.custo_medio, p.familia_codigo,
                       COALESCE(a.codigo, %s) AS almox,
                       COALESCE(e.quantidade, 0) AS quantidade, e.minimo, e.localizacao
                FROM manutencao.pecas p
                LEFT JOIN manutencao.estoque e ON e.peca_id = p.id
                LEFT JOIN manutencao.almoxarifados a
                  ON a.id = e.almoxarifado_id AND a.ativo = true
                  {'AND a.codigo = %s' if almox else ''}
                WHERE {' AND '.join(cond_p)}
                ORDER BY p.codigo LIMIT 800""",
            tuple([almox or '—'] + ([almox] if almox else []) + params_p))
        return {"itens": [dict(r) for r in rows]}
    rows = await ajard_query(
        f"""SELECT p.codigo, p.descricao, p.unidade, p.custo_medio, p.familia_codigo,
                   a.codigo AS almox, e.quantidade, e.minimo, e.localizacao
            FROM manutencao.estoque e
            JOIN manutencao.pecas p ON p.id = e.peca_id
            JOIN manutencao.almoxarifados a ON a.id = e.almoxarifado_id
            WHERE {' AND '.join(cond)}
            ORDER BY p.codigo, a.codigo
            LIMIT 800""", tuple(params))
    comp = await _comprometido_por_peca()   # (20/10/2026) coluna Comprometido / Disponível
    itens = []
    for r in rows:
        d = dict(r)
        d["comprometido"] = round(float(comp.get(d["codigo"], {}).get("qtd", 0.0)), 3)
        itens.append(d)
    return {"itens": itens}


@router.get("/manutencao/api/pecas-arvore")
async def pecas_arvore(_auth=Depends(verificar_manutencao)):
    """(28/08/2026) Estrutura do Catálogo de Peças: famílias com contagem
    do acervo — o front monta a árvore pela pontuação (MC → MC.010)."""
    await _garantir_peca_cols()
    rows = await ajard_query(
        f"""SELECT COALESCE({_fam_efetiva()},'—') AS familia, COALESCE(NULLIF(classe,''),'') AS classe, COUNT(*)::int AS n
           FROM manutencao.pecas WHERE ativo=true
           GROUP BY 1, 2 ORDER BY 1, 2""")
    return [dict(r) for r in rows]


@router.get("/manutencao/api/movimentacoes")
async def listar_movimentacoes(almox: str = None, limit: int = 120, de: str = None, ate: str = None,
                               documento: str = None, peca: str = None, tipo: str = None, ot: str = None,
                               _auth=Depends(verificar_manutencao)):
    """(28/08/2026) Extrato de movimentações do Almoxarifado — Entradas,
    Saídas, Transferências e Ajustes, com peça, locais, custo, OT e autor."""
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")
    for _col, _ddl in (("documento_tipo", "TEXT"), ("documento_numero", "TEXT"), ("fornecedor_id", "UUID"),
                       ("lote", "UUID"), ("estorno_de", "UUID"), ("estornado_em", "TIMESTAMPTZ")):
        await ajard_query(f"ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS {_col} {_ddl}", fetch="none")
    limit = min(max(int(limit or 120), 1), 2000)
    cond, params = ["1=1"], []
    if almox:
        cond.append("(ao.codigo = %s OR ad.codigo = %s)")
        params += [almox, almox]
    # (21/10/2026) filtros do extrato: período, documento, peça, tipo, OT
    if de:
        cond.append("m.criado_em >= %s::date"); params.append(de[:10])
    if ate:
        cond.append("m.criado_em < (%s::date + 1)"); params.append(ate[:10])
    if documento:
        cond.append("m.documento_numero ILIKE %s"); params.append(f"%{documento.strip()}%")
    if peca:
        cond.append(f"({_sem_acento_sql('p.codigo')} ILIKE %s OR {_sem_acento_sql('p.descricao')} ILIKE %s)")
        params += [f"%{_sem_acento(peca)}%"] * 2
    if tipo:
        cond.append("m.tipo = %s"); params.append(tipo)
    if ot:
        cond.append("o.numero ILIKE %s"); params.append(f"%{ot.strip()}%")
    rows = await ajard_query(
        f"""SELECT m.id, m.tipo, m.quantidade, m.custo_unitario, m.criado_em, m.observacao,
                   m.documento_tipo, m.documento_numero, m.estornado_em, m.estorno_de,
                   (SELECT td.nome FROM manutencao.tipos_documento td WHERE td.codigo = m.documento_tipo) AS documento_tipo_nome,
                   COALESCE(m.lote::text, COALESCE(m.documento_numero,'') || '|' || m.criado_em::date::text) AS lote_chave,
                   (SELECT nome FROM public.fornecedores f WHERE f.id = m.fornecedor_id) AS fornecedor_nome,
                   p.codigo AS peca_codigo, p.descricao AS peca_descricao, p.unidade,
                   ao.codigo AS origem, ad.codigo AS destino,
                   o.numero AS ot_numero, u.nome AS usuario
            FROM manutencao.movimentacoes m
            JOIN manutencao.pecas p ON p.id = m.peca_id
            LEFT JOIN manutencao.almoxarifados ao ON ao.id = m.almox_origem
            LEFT JOIN manutencao.almoxarifados ad ON ad.id = m.almox_destino
            LEFT JOIN manutencao.ot o ON o.id = m.ot_id
            LEFT JOIN public.usuarios_garra u ON u.id = m.usuario_id
            WHERE {' AND '.join(cond)}
            ORDER BY m.criado_em DESC LIMIT {limit}""", tuple(params))
    return {"itens": [dict(r) for r in rows]}


@router.post("/manutencao/api/entradas/estornar")
async def estornar_entrada(request: Request, payload=Depends(verificar_manutencao)):
    """(21/10/2026) ESTORNO DE DOCUMENTO (padrão MWW "anular"): desfaz uma entrada inteira.
    Regra: só se nenhum item já saiu (saldo atual no destino ≥ quantidade que entrou).
    Nada é apagado: cada item ganha um movimento contrário tipo 'estorno' (ligado ao original,
    motivo obrigatório), o saldo volta e o custo médio é recalculado. Permissão: Painel de Gestão."""
    from core.permissions import tem_modulo
    if not await tem_modulo(payload, {"admin_master"}):
        raise HTTPException(status_code=403, detail="Estornar entrada exige o Painel de Gestão")
    d = await request.json()
    chave = (d.get("lote") or "").strip()
    motivo = (d.get("motivo") or "").strip()
    if not chave:
        raise HTTPException(status_code=400, detail="Informe a entrada (lote)")
    if len(motivo) < 5:
        raise HTTPException(status_code=400, detail="Motivo do estorno é obrigatório (mínimo 5 caracteres)")
    for _col, _ddl in (("lote", "UUID"), ("estorno_de", "UUID"), ("estornado_em", "TIMESTAMPTZ")):
        await ajard_query(f"ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS {_col} {_ddl}", fetch="none")
    rows = await ajard_query(
        """SELECT m.id, m.peca_id, m.almox_destino, m.quantidade, m.custo_unitario, m.documento_numero,
                  p.codigo, p.descricao, p.custo_medio,
                  COALESCE((SELECT e.quantidade FROM manutencao.estoque e WHERE e.peca_id=m.peca_id AND e.almoxarifado_id=m.almox_destino),0) AS saldo_destino,
                  COALESCE((SELECT SUM(e.quantidade) FROM manutencao.estoque e WHERE e.peca_id=m.peca_id),0) AS saldo_total
           FROM manutencao.movimentacoes m JOIN manutencao.pecas p ON p.id=m.peca_id
           WHERE m.tipo='entrada' AND m.estornado_em IS NULL
             AND COALESCE(m.lote::text, COALESCE(m.documento_numero,'') || '|' || m.criado_em::date::text) = %s""", (chave,)) or []
    if not rows:
        raise HTTPException(status_code=404, detail="Entrada não encontrada ou já estornada")
    faltam = [f"{r['codigo']} (entrou {float(r['quantidade']):g}, saldo {float(r['saldo_destino']):g})"
              for r in rows if float(r["saldo_destino"]) < float(r["quantidade"])]
    if faltam:
        raise HTTPException(status_code=400, detail="Não dá para estornar: já houve saída de " + "; ".join(faltam) + ". Corrija as baixas primeiro.")
    uid = await _usuario_id(payload)
    doc = rows[0]["documento_numero"] or "sem nº"
    for r in rows:
        q = float(r["quantidade"]); custo = float(r["custo_unitario"]) if r["custo_unitario"] is not None else None
        await ajard_query("UPDATE manutencao.estoque SET quantidade = quantidade - %s WHERE peca_id=%s AND almoxarifado_id=%s",
                          (q, r["peca_id"], r["almox_destino"]), fetch="none")
        # custo médio: desfaz a ponderação desta entrada sobre o que resta
        if custo is not None and r["custo_medio"] is not None:
            st, cm = float(r["saldo_total"]), float(r["custo_medio"])
            resta = st - q
            if resta > 0:
                novo = (st * cm - q * custo) / resta
                if novo > 0:
                    await ajard_query("UPDATE manutencao.pecas SET custo_medio=%s WHERE id=%s", (round(novo, 4), r["peca_id"]), fetch="none")
        await ajard_query(
            """INSERT INTO manutencao.movimentacoes (tipo, peca_id, almox_origem, quantidade, usuario_id, observacao, custo_unitario,
                                                     documento_tipo, documento_numero, estorno_de)
               VALUES ('estorno',%s,%s,%s,%s,%s,%s,'estorno',%s,%s)""",
            (r["peca_id"], r["almox_destino"], q, uid, f"Estorno da entrada {doc}: {motivo}", custo, r["documento_numero"], r["id"]), fetch="none")
        await ajard_query("UPDATE manutencao.movimentacoes SET estornado_em=now() WHERE id=%s", (r["id"],), fetch="none")
    return {"ok": True, "itens": len(rows), "documento": doc}


@router.post("/manutencao/api/entradas")
async def entrada_documento(request: Request, payload=Depends(verificar_manutencao)):
    """Rota HTTP da entrada por documento — o trabalho vive em _entrada_core
    (reutilizado pela conferência de recebimento de OC)."""
    d = await request.json()
    if (d.get("oc_numero") or "").strip():
        # (01/09/2026) entrada manual com OC: se a OC existe no Compras, a entrada
        # vem pela conferência do recebimento — avisa antes de duplicar saldo
        oc = await _oc_por_numero((d.get("oc_numero") or "").strip())
        if oc and not d.get("forcar"):
            raise HTTPException(status_code=409, detail=f"A OC {oc['numero']} existe no Compras ({oc['status']}) — "
                                "o saldo entra pela conferência do recebimento (Materiais ▸ Recebimentos a conferir). "
                                "Para lançar mesmo assim, confirme na tela.")
    return await _entrada_core(d, payload)


async def _oc_por_numero(numero):
    try:
        return await ajard_query("SELECT id, numero, status FROM compras.ordens_compra WHERE numero=%s AND ativo=true", (numero,), fetch="one")
    except Exception:
        return None


async def _entrada_core(d, payload):
    """(28/08/2026) ENTRADA POR DOCUMENTO — multi-itens, três caminhos:
    · 'nf' / 'notinha': entrada normal → soma saldo no destino e recalcula
      custo médio ponderado da peça;
    · 'aplicacao_direta': material de oficina/compra que nem passa pela
      prateleira — cadastra (se preciso), NÃO mexe saldo e já aponta o
      consumo na OT (aparece no Realizado);
    · elo com Ordem de Compra por oc_numero (amarração forte na
      unificação Compras↔OT).
    Peça inexistente com descrição no item → cadastrada na hora."""
    # (21/10/2026) Tipo de documento vem do domínio Parametrizar › Custos › Tipos de documento (01 NF, 02 CUPOM…).
    # O DESTINO (almoxarifado × aplicação direta na OT) é outro campo. Valores antigos continuam aceitos:
    # 'nf'→01, 'notinha'→02, 'aplicacao_direta'→destino OT com documento 02.
    _LEGADO = {"nf": "01", "notinha": "02", "aplicacao_direta": None}   # OT sem tipo informado → 03 BALCAO
    bruto = (d.get("documento_tipo") or "").strip()
    destino_tipo = (d.get("destino_tipo") or "").strip() or ("ot" if bruto == "aplicacao_direta" else "almox")
    if destino_tipo not in ("almox", "ot"):
        raise HTTPException(status_code=400, detail="Destino inválido (almox | ot)")
    doc_cod = _LEGADO.get(bruto, bruto) if bruto else None
    if doc_cod:
        ok = await ajard_query("SELECT codigo FROM manutencao.tipos_documento WHERE codigo=%s AND COALESCE(ativo,true)", (doc_cod,), fetch="one")
        if not ok:
            raise HTTPException(status_code=400, detail=f"Tipo de documento '{doc_cod}' não está em Parametrizar › Custos › Tipos de documento")
    elif destino_tipo == "almox":
        raise HTTPException(status_code=400, detail="Informe o tipo de documento")
    doc_tipo = "aplicacao_direta" if destino_tipo == "ot" else "entrada"   # ramo interno (mantém a lógica abaixo)
    doc_num = (d.get("documento_numero") or "").strip()
    if destino_tipo == "almox" and not doc_num:
        raise HTTPException(status_code=400, detail="Informe o número do documento")
    itens = d.get("itens") or []
    itens = [i for i in itens if (i.get("peca") or "").strip()]
    if not itens:
        raise HTTPException(status_code=400, detail="Documento sem itens")
    for _col, _ddl in (("documento_tipo", "TEXT"), ("documento_numero", "TEXT"),
                       ("fornecedor_id", "UUID"), ("oc_numero", "TEXT"),
                       # (20/10/2026) fechamento com a NF: custo digitado × custo que entrou (com IPI/frete rateado)
                       ("custo_nf", "NUMERIC"), ("acrescimo_rateado", "NUMERIC"), ("total_nf", "NUMERIC"),
                       # (21/10/2026) estorno de documento: lote = uma entrada; estorno_de aponta o movimento original
                       ("lote", "UUID"), ("estorno_de", "UUID"), ("estornado_em", "TIMESTAMPTZ")):
        await ajard_query(
            f"ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS {_col} {_ddl}",
            fetch="none")
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")

    destino = None
    if doc_tipo != "aplicacao_direta":
        a = await ajard_query(
            "SELECT id FROM manutencao.almoxarifados WHERE codigo=%s AND ativo=true",
            ((d.get("destino") or "").strip(),), fetch="one")
        if not a:
            raise HTTPException(status_code=400, detail="Entrada exige almoxarifado de destino")
        destino = a["id"]
    ot_id = d.get("ot_id")
    if doc_tipo == "aplicacao_direta":
        if not ot_id:
            raise HTTPException(status_code=400, detail="Aplicação direta exige a OT que consumiu o material")
        ot = await ajard_query("SELECT id FROM manutencao.ot WHERE id=%s AND ativo=true", (ot_id,), fetch="one")
        if not ot:
            raise HTTPException(status_code=404, detail="OT não encontrada")

    uid = await _usuario_id(payload)
    def _num(v):
        try:
            return float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return None
    # ── (20/10/2026) FECHAMENTO COM A NF ──────────────────────────────────────
    # acrescimos = IPI + frete + outras despesas da nota; desconto; total_nf = valor da nota.
    # Como a Garra não recupera IPI, acréscimos são custo da peça: rateio proporcional ao
    # valor de cada item (ERP/ManWinWin). custo_unitario = custo que entrou no estoque;
    # custo_nf = o digitado da nota. Com total_nf informado, a entrada só passa se fechar.
    acrescimos = _num(d.get("acrescimos")) or 0.0
    desconto = _num(d.get("desconto")) or 0.0
    total_nf = _num(d.get("total_nf"))
    soma_itens = 0.0
    for it in itens:
        q_, c_ = _num(it.get("quantidade")) or 0, _num(it.get("custo_unitario"))
        soma_itens += q_ * (c_ or 0)
    if total_nf is not None:
        esperado = round(soma_itens + acrescimos - desconto, 2)
        if abs(esperado - total_nf) > 0.05:
            raise HTTPException(status_code=400, detail=(
                f"A entrada não fecha com a NF: itens R$ {soma_itens:.2f} + acréscimos R$ {acrescimos:.2f} "
                f"− desconto R$ {desconto:.2f} = R$ {esperado:.2f}, mas o total da NF é R$ {total_nf:.2f} "
                f"(diferença R$ {total_nf - esperado:+.2f})."))
    fator = ((acrescimos - desconto) / soma_itens) if soma_itens > 0 else 0.0
    rateio_acum, n_itens = 0.0, len(itens)
    import uuid as _uuid
    lote = str(_uuid.uuid4())

    lancados, total_doc = [], 0.0
    for idx, it in enumerate(itens):
        cod = (it.get("peca") or "").strip()
        qtd = _num(it.get("quantidade")) or 0
        if qtd <= 0:
            raise HTTPException(status_code=400, detail=f"Quantidade inválida em {cod}")
        p = await ajard_query(
            "SELECT id, custo_medio FROM manutencao.pecas WHERE codigo=%s",
            (cod,), fetch="one")
        desc_it = (it.get("descricao") or "").strip()
        if not p:
            # (21/10/2026) Regra: peça nova entra pelo CADASTRO COMPLETO (código, família, características) —
            # a entrada por NF/notinha não cria item "de passagem". Só a aplicação direta da OT ainda aceita
            # (o app do mecânico cadastra pela janela completa antes).
            if doc_tipo != "aplicacao_direta":
                raise HTTPException(status_code=400, detail=f"Peça {cod} não está no Catálogo — cadastre pelo ➕ da lista de sugestões (cadastro completo) e lance a entrada em seguida")
            desc = desc_it
            if not desc:
                raise HTTPException(status_code=404, detail=f"Peça {cod} não cadastrada — informe a descrição para cadastrar na hora")
            # (10/10/2026) peça cadastrada pela OT pode já vir com família (Catálogo organizado desde o nascimento)
            p = await ajard_query_id(
                """INSERT INTO manutencao.pecas (codigo, descricao, unidade, custo_medio, familia_codigo)
                   VALUES (%s,%s,%s,%s,%s)""",
                (cod, desc, (it.get("un") or "UN").strip().upper() or "UN", _num(it.get("custo_unitario")),
                 (it.get("familia_codigo") or "").strip() or None))
            p = {"id": p["id"], "custo_medio": _num(it.get("custo_unitario"))}
        custo_nf = _num(it.get("custo_unitario"))
        custo = custo_nf
        rateado = None
        if custo is not None and fator:
            # último item absorve o resíduo de arredondamento para a soma bater com a NF
            if idx == n_itens - 1:
                rateado = round((acrescimos - desconto) - rateio_acum, 2)
            else:
                rateado = round(qtd * custo * fator, 2)
                rateio_acum += rateado
            custo = round((qtd * custo + rateado) / qtd, 4)
        if custo is None:
            custo = float(p["custo_medio"]) if p["custo_medio"] is not None else None
        if doc_tipo != "aplicacao_direta":
            if custo is not None:
                s = await ajard_query(
                    "SELECT COALESCE(SUM(quantidade),0) AS s FROM manutencao.estoque WHERE peca_id=%s",
                    (p["id"],), fetch="one")
                saldo_antes = float(s["s"] or 0)
                cm_atual = float(p["custo_medio"]) if p["custo_medio"] is not None else None
                novo_cm = custo if (cm_atual is None or saldo_antes <= 0) \
                    else round((saldo_antes * cm_atual + qtd * custo) / (saldo_antes + qtd), 4)
                await ajard_query("UPDATE manutencao.pecas SET custo_medio=%s WHERE id=%s",
                                  (novo_cm, p["id"]), fetch="none")
            await ajard_query("""
                INSERT INTO manutencao.estoque (peca_id, almoxarifado_id, quantidade)
                VALUES (%s,%s,%s)
                ON CONFLICT (peca_id, almoxarifado_id)
                DO UPDATE SET quantidade = manutencao.estoque.quantidade + EXCLUDED.quantidade""",
                (p["id"], destino, qtd), fetch="none")
        tipo_mov = "entrada" if doc_tipo != "aplicacao_direta" else "aplicacao_direta"
        rub = None
        if tipo_mov == "aplicacao_direta":
            pf = await ajard_query("SELECT familia_codigo, descricao FROM manutencao.pecas WHERE id=%s", (p["id"],), fetch="one")
            rub = (it.get("rubrica") or "").strip() or _rubrica_material("aplicacao_direta", pf["familia_codigo"] if pf else None, pf["descricao"] if pf else desc_it)
        await ajard_query("""
            INSERT INTO manutencao.movimentacoes
                (tipo, peca_id, almox_destino, quantidade, ot_id, usuario_id, observacao,
                 custo_unitario, documento_tipo, documento_numero, fornecedor_id, oc_numero, rubrica,
                 custo_nf, acrescimo_rateado, total_nf, lote)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tipo_mov, p["id"], destino, qtd,
             (ot_id if doc_tipo == "aplicacao_direta" else None), uid,
             (d.get("observacao") or "").strip() or None, custo,
             (doc_cod or "03"), doc_num or None, d.get("fornecedor_id") or None,
             (d.get("oc_numero") or "").strip() or None, rub,
             (round(custo_nf, 4) if custo_nf is not None else None), rateado,
             (round(total_nf, 2) if total_nf is not None else None), lote), fetch="none")
        total_doc += qtd * (custo or 0)
        lancados.append(cod)
    # (21/10/2026) itens que entraram no almoxarifado sem endereço (C-P-N): o front obriga a endereçar
    # antes de fechar — peça nova precisa de lugar na prateleira — e já oferece a etiqueta.
    sem_local = []
    if destino is not None:
        rows = await ajard_query(
            """SELECT p.codigo, p.descricao FROM manutencao.estoque e JOIN manutencao.pecas p ON p.id=e.peca_id
               WHERE e.almoxarifado_id=%s AND p.codigo = ANY(%s) AND COALESCE(e.localizacao,'')=''
               ORDER BY p.codigo""", (destino, lancados)) or []
        sem_local = [dict(r) for r in rows]
    quantidades = {}
    for it in itens:
        c_ = (it.get("peca") or "").strip()
        try:
            quantidades[c_] = quantidades.get(c_, 0) + int(float(str(it.get("quantidade") or 0).replace(",", ".")))
        except ValueError:
            pass
    return {"ok": True, "documento": doc_num or (doc_cod or "aplicação direta"), "itens": len(lancados), "codigos": lancados, "quantidades": quantidades, "lote": lote,
            "destino": (d.get("destino") or "").strip() or None, "sem_local": sem_local,
            "total": round(total_doc, 2), "itens_nf": round(soma_itens, 2),
            "acrescimos": round(acrescimos, 2), "desconto": round(desconto, 2)}


# ══ (20/10/2026) ARTIGOS SUBSTITUTOS (padrão ManWinWin › Materiais › Substitutos) ══
# Tabela manutencao.peca_substitutos (peca_codigo → substituto_codigo + restrições). Lista enriquecida
# com descrição, saldo e custo do substituto; o reverso ("este item substitui…") viaja junto.
# A OT usa: peça sem saldo → sugere substituto com saldo.
async def _subst_enriquecer(rows, chave):
    out = []
    for r in rows:
        cod = r[chave]
        p = await ajard_query(
            """SELECT p.codigo, p.descricao, p.unidade, p.custo_medio,
                      COALESCE((SELECT SUM(e.quantidade) FROM manutencao.estoque e WHERE e.peca_id=p.id),0) AS saldo
               FROM manutencao.pecas p WHERE p.codigo=%s""", (cod,), fetch="one")
        d = dict(r)
        d["descricao"] = (p or {}).get("descricao") or "(item não cadastrado)"
        d["unidade"] = (p or {}).get("unidade")
        d["custo_medio"] = float(p["custo_medio"]) if p and p.get("custo_medio") is not None else None
        d["saldo"] = float(p["saldo"]) if p else 0.0
        out.append(d)
    return out


@router.get("/manutencao/api/pecas/{codigo}/substitutos")
async def peca_substitutos(codigo: str, _auth=Depends(verificar_manutencao)):
    tab, _ = await _garantir_tabela("substitutos")
    p = await ajard_query("SELECT codigo, descricao FROM manutencao.pecas WHERE codigo=%s", (codigo,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Item não encontrado")
    diretos = await ajard_query(
        f"SELECT id, peca_codigo, substituto_codigo, observacao, criado_em FROM {tab} WHERE ativo=true AND peca_codigo=%s ORDER BY criado_em", (codigo,)) or []
    reversos = await ajard_query(
        f"SELECT id, peca_codigo, substituto_codigo, observacao, criado_em FROM {tab} WHERE ativo=true AND substituto_codigo=%s ORDER BY criado_em", (codigo,)) or []
    return {"item": dict(p),
            "substitutos": await _subst_enriquecer(diretos, "substituto_codigo"),
            "substitui": await _subst_enriquecer(reversos, "peca_codigo")}


@router.post("/manutencao/api/pecas/{codigo}/substitutos")
async def peca_substituto_add(codigo: str, request: Request, payload=Depends(verificar_manutencao)):
    tab, _ = await _garantir_tabela("substitutos")
    d = await request.json()
    sub = (d.get("substituto_codigo") or "").strip()
    if not sub:
        raise HTTPException(status_code=400, detail="Informe o código do substituto")
    if sub == codigo:
        raise HTTPException(status_code=400, detail="Um item não substitui a si mesmo")
    for c in (codigo, sub):
        if not await ajard_query("SELECT 1 FROM manutencao.pecas WHERE codigo=%s", (c,), fetch="one"):
            raise HTTPException(status_code=404, detail=f"Item {c} não está no Catálogo")
    uid = await _usuario_id(payload)
    obs = (d.get("observacao") or "").strip() or None
    pares = [(codigo, sub)] + ([(sub, codigo)] if d.get("bidirecional") else [])
    for a, b in pares:
        ja = await ajard_query(f"SELECT id FROM {tab} WHERE ativo=true AND peca_codigo=%s AND substituto_codigo=%s", (a, b), fetch="one")
        if ja:
            continue
        await ajard_query(
            f"INSERT INTO {tab} (peca_codigo, substituto_codigo, observacao, criado_por) VALUES (%s,%s,%s,%s)",
            (a, b, obs, uid), fetch="none")
    return {"ok": True}


@router.delete("/manutencao/api/substitutos/{rid}")
async def peca_substituto_del(rid: str, _auth=Depends(verificar_manutencao)):
    tab, _ = await _garantir_tabela("substitutos")
    await ajard_query(f"UPDATE {tab} SET ativo=false, atualizado_em=now() WHERE id=%s", (rid,), fetch="none")
    return {"ok": True}


# ══ (11/10/2026) PONTOS DE LEITURA DO OBJETO — janela própria (menu de serviços › Pontos de leitura) ══
# Um ponto = contador/medição do objeto com limiares 🟡🟠🔴 (ex.: horas da bomba, km do pneu, temperatura).
# Leitura registrada aqui alimenta o semáforo (objetos sem FMP) e fica no histórico pontos_leituras.
def _ponto_status(leit, at, ur, mx):
    if leit is None:
        return "verde"
    leit = float(leit)
    if mx is not None and leit >= float(mx):
        return "vermelho"
    if ur is not None and leit >= float(ur):
        return "laranja"
    if at is not None and leit >= float(at):
        return "amarelo"
    return "verde"


def _num_ou_none(v):
    if v in (None, ""):
        return None
    try:
        if isinstance(v, str):
            v = v.strip()
            return float(v.replace(".", "").replace(",", ".")) if "," in v else float(v)
        return float(v)
    except (TypeError, ValueError):
        return None


@router.get("/manutencao/api/equipamentos/{eq_id}/pontos")
async def pontos_do_objeto(eq_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_biblioteca()
    eq = await ajard_query("SELECT id, codigo, descricao, medicao, horimetro_atual, km_atual FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Objeto não encontrado")
    rows = await ajard_query(
        """SELECT id, codigo, descricao, unidade, leitura_atual, data_leitura, limiar_atencao, limiar_urgente, limiar_maximo, ativo, observacao,
                  (SELECT COUNT(*)::int FROM manutencao.pontos_leituras l WHERE l.ponto_id = p.id) AS n_leituras
           FROM manutencao.pontos_controle p WHERE equipamento_id=%s ORDER BY ativo DESC, codigo""", (eq_id,))
    pontos = []
    for r in rows:
        d = dict(r)
        for k in ("leitura_atual", "limiar_atencao", "limiar_urgente", "limiar_maximo"):
            d[k] = float(d[k]) if d[k] is not None else None
        d["data_leitura"] = d["data_leitura"].isoformat() if d["data_leitura"] else None
        d["status"] = _ponto_status(d["leitura_atual"], d["limiar_atencao"], d["limiar_urgente"], d["limiar_maximo"])
        d["id"] = str(d["id"])
        pontos.append(d)
    tem_fmp = await ajard_query("SELECT COUNT(*)::int AS n FROM manutencao.planos WHERE equipamento_id=%s AND ativo=true", (eq_id,), fetch="one")
    return {"equipamento": eq["codigo"], "descricao": eq["descricao"], "medicao": eq["medicao"],
            "leitura_viva": float(eq["km_atual"] or 0) if eq["medicao"] == "km" else float(eq["horimetro_atual"] or 0),
            "com_fmp": bool(tem_fmp and tem_fmp["n"]), "pontos": pontos}


@router.post("/manutencao/api/equipamentos/{eq_id}/pontos")
async def ponto_criar(eq_id: str, request: Request, _auth=Depends(verificar_manutencao)):
    await _garantir_biblioteca()
    d = await request.json()
    cod = (d.get("codigo") or "").strip().upper()
    if not cod:
        raise HTTPException(status_code=400, detail="Informe o código do ponto")
    ja = await ajard_query("SELECT id, ativo FROM manutencao.pontos_controle WHERE equipamento_id=%s AND codigo=%s", (eq_id, cod), fetch="one")
    vals = ((d.get("descricao") or "").strip() or None, (d.get("unidade") or "").strip() or None,
            _num_ou_none(d.get("limiar_atencao")), _num_ou_none(d.get("limiar_urgente")), _num_ou_none(d.get("limiar_maximo")),
            (d.get("observacao") or "").strip() or None)
    if ja:
        await ajard_query("""UPDATE manutencao.pontos_controle SET descricao=%s, unidade=%s, limiar_atencao=%s, limiar_urgente=%s,
                             limiar_maximo=%s, observacao=%s, ativo=true WHERE id=%s""", vals + (ja["id"],), fetch="none")
        return {"ok": True, "id": str(ja["id"]), "reativado": not ja["ativo"]}
    r = await ajard_query("""INSERT INTO manutencao.pontos_controle (equipamento_id, codigo, descricao, unidade, limiar_atencao, limiar_urgente, limiar_maximo, observacao, leitura_atual)
                             VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                          (eq_id, cod) + vals + (_num_ou_none(d.get("leitura_atual")),), fetch="one")
    return {"ok": True, "id": str(r["id"])}


@router.patch("/manutencao/api/pontos/{ponto_id}")
async def ponto_editar(ponto_id: str, request: Request, _auth=Depends(verificar_manutencao)):
    d = await request.json()
    sets, params = [], []
    for c in ("descricao", "unidade", "observacao"):
        if c in d:
            sets.append(f"{c}=%s"); params.append((str(d[c] or "")).strip() or None)
    for c in ("limiar_atencao", "limiar_urgente", "limiar_maximo"):
        if c in d:
            sets.append(f"{c}=%s"); params.append(_num_ou_none(d[c]))
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(ponto_id)
    await ajard_query(f"UPDATE manutencao.pontos_controle SET {', '.join(sets)} WHERE id=%s", tuple(params), fetch="none")
    return {"ok": True}


@router.post("/manutencao/api/pontos/{ponto_id}/leitura")
async def ponto_leitura(ponto_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """Registra leitura do ponto: atualiza leitura_atual/data_leitura e guarda no histórico.
    Leitura menor que a atual só com forcar=true (contador trocado/zerado)."""
    d = await request.json()
    n = _num_ou_none(d.get("leitura"))
    if n is None:
        raise HTTPException(status_code=400, detail="Leitura inválida")
    p = await ajard_query("SELECT id, codigo, leitura_atual FROM manutencao.pontos_controle WHERE id=%s", (ponto_id,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Ponto não encontrado")
    if p["leitura_atual"] is not None and n < float(p["leitura_atual"]) and not d.get("forcar"):
        raise HTTPException(status_code=400, detail=f"Leitura {n:g} é menor que a atual ({float(p['leitura_atual']):g}). Confirme se o contador foi trocado/zerado.")
    dt = _data_ok(d.get("data"), "Data da leitura")
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    quando = _dt.fromisoformat(dt + "T12:00:00").replace(tzinfo=_tz(_td(hours=-3))) if dt else _dt.now(_tz.utc)
    uid = await _usuario_id(payload)
    await ajard_query("INSERT INTO manutencao.pontos_leituras (ponto_id, leitura, data, usuario_id, observacao) VALUES (%s,%s,%s,%s,%s)",
                      (ponto_id, n, quando, uid, (d.get("observacao") or "").strip() or None), fetch="none")
    await ajard_query("UPDATE manutencao.pontos_controle SET leitura_atual=%s, data_leitura=%s WHERE id=%s", (n, quando, ponto_id), fetch="none")
    return {"ok": True, "leitura": n}


@router.get("/manutencao/api/pontos/{ponto_id}/leituras")
async def ponto_leituras(ponto_id: str, _auth=Depends(verificar_manutencao)):
    rows = await ajard_query(
        """SELECT l.id, l.leitura, l.data, l.observacao, u.nome AS usuario_nome
           FROM manutencao.pontos_leituras l LEFT JOIN public.usuarios_garra u ON u.id = l.usuario_id
           WHERE l.ponto_id=%s ORDER BY l.data DESC LIMIT 200""", (ponto_id,))
    return [{"id": str(r["id"]), "leitura": float(r["leitura"]), "data": r["data"].isoformat() if r["data"] else None,
             "observacao": r["observacao"], "usuario_nome": r["usuario_nome"]} for r in rows]


@router.get("/manutencao/api/equipamentos/{eq_id}/funcionamento")
async def equip_funcionamento(eq_id: str, _auth=Depends(verificar_manutencao)):
    """(28/08/2026) Registo Funcionamento do ManWinWin: leituras das partes
    diárias (90 dias), último registo, FMD-R (regra dos ≥4 registos) e as
    projeções da calculadora — 'terá X em 30 dias' e alvo de próximo ponto."""
    from datetime import date as _date, timedelta as _td
    await _garantir_leituras()
    eq = await ajard_query(
        "SELECT codigo, descricao, horimetro_atual, km_atual, medicao FROM operacional.equipamentos WHERE id=%s",
        (eq_id,), fetch="one")
    if not eq:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    rows = await ajard_query(
        """SELECT data::date AS d, leitura, fonte, ref, leitura_original, corrigida
           FROM operacional.v_leituras
           WHERE equipamento_id=%s AND data >= now() - make_interval(days => %s)
           ORDER BY data DESC LIMIT 80""", (eq_id, JANELA_FMD_DIAS))
    regs = [{"data": r["d"].isoformat(), "leitura": float(r["leitura"] or 0), "fonte": r["fonte"],
             "ref": r["ref"], "corrigida": bool(r["corrigida"]),
             "leitura_original": float(r["leitura_original"] or 0)}
            for r in rows if float(r["leitura"] or 0) > 0]
    anuladas = await ajard_query(
        """SELECT data::date AS d, fonte, ref, leitura_original, motivo
           FROM manutencao.leituras_correcao
           WHERE equipamento_id=%s AND anulada AND data >= now() - make_interval(days => %s)
           ORDER BY data DESC""", (eq_id, JANELA_FMD_DIAS)) or []
    # FMD-R pela maior leitura de cada dia (regra dos ≥4 dias)
    dias = {}
    for x in regs:
        dias[x["data"]] = max(dias.get(x["data"], 0), x["leitura"])
    fmd = _fmd_serie([(_date.fromisoformat(k), v) for k, v in dias.items()])
    proj_30 = None
    vivo = eq["km_atual"] if eq.get("medicao") == "km" else eq["horimetro_atual"]
    atual = float(vivo or (regs[0]["leitura"] if regs else 0) or 0)
    if fmd:
        proj_30 = round(atual + fmd * 30, 1)
    return {"equipamento": eq["codigo"], "descricao": eq["descricao"],
            "medicao": eq["medicao"], "leitura_atual": atual,
            "registos": regs, "n_registos": len(regs),
            "anuladas": [{"data": r["d"].isoformat() if r["d"] else None, "fonte": r["fonte"], "ref": r["ref"],
                          "leitura_original": float(r["leitura_original"] or 0), "motivo": r["motivo"]} for r in anuladas],
            "fmd": fmd, "fmd_valido": fmd is not None,
            "projecao_30d": proj_30, "gerado_em": _date.today().isoformat()}


_FONTES_LEITURA = {"parte", "abastecimento", "checklist", "ot"}   # (19/10/2026) 'ot' = leitura no Encerrar da OT
_dmy = lambda v: v.strftime("%d/%m/%Y") if v else "—"


async def _leitura_resposta(eq_id: str):
    """Contador de manutenção × maior leitura válida — o front oferece ⚙ Acerto se o contador ficou acima."""
    eq = await ajard_query("SELECT medicao, horimetro_atual, km_atual FROM operacional.equipamentos WHERE id=%s",
                           (eq_id,), fetch="one") or {}
    mx = await ajard_query("SELECT MAX(leitura) AS mx FROM operacional.v_leituras WHERE equipamento_id=%s",
                           (eq_id,), fetch="one") or {}
    cont = eq.get("km_atual") if eq.get("medicao") == "km" else eq.get("horimetro_atual")
    return {"ok": True, "contador": float(cont or 0), "maior_leitura": float(mx.get("mx") or 0)}


async def _leitura_da_view(eq_id: str, fonte: str, ref: str):
    if fonte not in _FONTES_LEITURA:
        raise HTTPException(status_code=400, detail="Fonte de leitura inválida")
    await _garantir_leituras()
    r = await ajard_query(
        """SELECT data, leitura, leitura_original FROM operacional.v_leituras
           WHERE equipamento_id=%s AND fonte=%s AND ref=%s LIMIT 1""", (eq_id, fonte, ref), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Leitura não encontrada")
    return r


async def _leitura_gravar(eq_id, fonte, ref, r, nova, anulada, motivo, payload):
    uid = await _usuario_id(payload)
    await ajard_query(
        """INSERT INTO manutencao.leituras_correcao
               (equipamento_id, fonte, ref, data, leitura_original, leitura_nova, anulada, motivo, usuario_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (fonte, ref) DO UPDATE SET leitura_nova=EXCLUDED.leitura_nova, anulada=EXCLUDED.anulada,
               motivo=EXCLUDED.motivo, usuario_id=EXCLUDED.usuario_id, criado_em=now()""",
        (eq_id, fonte, ref, r["data"], r["leitura_original"], nova, anulada, motivo, uid), fetch="none")
    orig = float(r["leitura_original"] or 0)
    # (21/10/2026) contador do equipamento acompanha a correção: se ele era esta leitura, volta ao maior registo válido
    eqm = await ajard_query("SELECT medicao FROM operacional.equipamentos WHERE id=%s", (eq_id,), fetch="one")
    campo = "km_atual" if (eqm and (eqm.get("medicao") or "") == "km") else "horimetro_atual"
    await ajard_query(
        f"""UPDATE operacional.equipamentos e SET {campo} = sub.m, atualizado_em = now()
              FROM (SELECT MAX(leitura) AS m FROM operacional.v_leituras WHERE equipamento_id=%s) sub
             WHERE e.id=%s AND COALESCE(e.{campo},0) = %s AND sub.m IS NOT NULL AND sub.m <> COALESCE(e.{campo},0)""",
        (eq_id, eq_id, orig), fetch="none")
    txt = (f"✕ Leitura excluída ({fonte} {_dmy(r['data'])}): {orig:g}" if anulada
           else f"✏️ Leitura corrigida ({fonte} {_dmy(r['data'])}): {orig:g} → {nova:g}")
    await ajard_query(
        "INSERT INTO manutencao.equipamento_notas (equipamento_id, usuario_id, descricao) VALUES (%s,%s,%s)",
        (eq_id, uid, f"{txt}. Motivo: {motivo}"), fetch="none")


@router.patch("/manutencao/api/equipamentos/{eq_id}/leituras/{fonte}/{ref}")
async def leitura_corrigir(eq_id: str, fonte: str, ref: str, request: Request, payload=Depends(verificar_manutencao)):
    """(24/09/2026) Registro Funcionamento ▸ ✏️ — corrige o valor da leitura (documento de origem intacto)."""
    d = await request.json()
    try:
        nova = float(str(d.get("leitura")).replace(",", "."))
    except Exception:
        raise HTTPException(status_code=400, detail="Informe a leitura correta")
    if nova <= 0:
        raise HTTPException(status_code=400, detail="Leitura deve ser maior que zero")
    motivo = (d.get("motivo") or "").strip()
    if len(motivo) < 5:
        raise HTTPException(status_code=400, detail="Motivo da correção é obrigatório (mínimo 5 caracteres)")
    r = await _leitura_da_view(eq_id, fonte, ref)
    await _leitura_gravar(eq_id, fonte, ref, r, nova, False, motivo, payload)
    return await _leitura_resposta(eq_id)


@router.delete("/manutencao/api/equipamentos/{eq_id}/leituras/{fonte}/{ref}")
async def leitura_excluir(eq_id: str, fonte: str, ref: str, request: Request, payload=Depends(verificar_manutencao)):
    """(24/09/2026) Registro Funcionamento ▸ ✕ — tira a leitura do FMD/funcionamento (documento de origem intacto)."""
    d = await request.json()
    motivo = (d.get("motivo") or "").strip()
    if len(motivo) < 5:
        raise HTTPException(status_code=400, detail="Motivo da exclusão é obrigatório (mínimo 5 caracteres)")
    r = await _leitura_da_view(eq_id, fonte, ref)
    await _leitura_gravar(eq_id, fonte, ref, r, None, True, motivo, payload)
    return await _leitura_resposta(eq_id)


@router.delete("/manutencao/api/equipamentos/{eq_id}/leituras/{fonte}/{ref}/correcao")
async def leitura_restaurar(eq_id: str, fonte: str, ref: str, payload=Depends(verificar_manutencao)):
    """(24/09/2026) Desfaz correção/exclusão — volta a leitura original do documento."""
    await _garantir_leituras()
    c = await ajard_query(
        "DELETE FROM manutencao.leituras_correcao WHERE equipamento_id=%s AND fonte=%s AND ref=%s "
        "RETURNING data, leitura_original", (eq_id, fonte, ref), fetch="one")
    if not c:
        raise HTTPException(status_code=404, detail="Correção não encontrada")
    uid = await _usuario_id(payload)
    await ajard_query(
        "INSERT INTO manutencao.equipamento_notas (equipamento_id, usuario_id, descricao) VALUES (%s,%s,%s)",
        (eq_id, uid, f"↺ Leitura restaurada ({fonte} {_dmy(c['data'])}): {float(c['leitura_original'] or 0):g}"),
        fetch="none")
    return await _leitura_resposta(eq_id)


@router.get("/manutencao/api/equipamentos/{eq_id}/artigos-aplicados")
async def equip_artigos(eq_id: str, _auth=Depends(verificar_manutencao)):
    """Artigos Aplicados do ManWinWin: todo consumo de peça do equipamento
    (saídas e aplicações diretas das OTs dele; devoluções abatem)."""
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")
    rows = await ajard_query(
        """SELECT m.tipo, m.quantidade, m.custo_unitario, m.criado_em,
                  p.codigo AS peca_codigo, p.descricao AS peca_descricao, p.unidade, p.custo_medio,
                  o.numero AS ot_numero
           FROM manutencao.movimentacoes m
           JOIN manutencao.ot o ON o.id = m.ot_id AND o.equipamento_id = %s
           JOIN manutencao.pecas p ON p.id = m.peca_id
           WHERE m.tipo IN ('saida', 'entrada', 'aplicacao_direta')
           ORDER BY m.criado_em DESC LIMIT 200""", (eq_id,))
    itens, total = [], 0.0
    for r in rows:
        custo = float(r["custo_unitario"]) if r["custo_unitario"] is not None \
            else (float(r["custo_medio"]) if r["custo_medio"] is not None else 0.0)
        qtd = float(r["quantidade"] or 0)
        sinal = -1 if r["tipo"] == "entrada" else 1
        linha = round(sinal * qtd * custo, 2)
        total += linha
        itens.append({"tipo": r["tipo"], "data": (r["criado_em"].isoformat() if r["criado_em"] else None),
                      "peca_codigo": r["peca_codigo"], "peca_descricao": r["peca_descricao"],
                      "unidade": r["unidade"] or "UN", "quantidade": qtd,
                      "custo_unitario": custo, "total": linha, "ot": r["ot_numero"]})
    return {"itens": itens, "total": round(total, 2)}


@router.get("/manutencao/api/equipamentos-basico")
async def equipamentos_basico(_auth=Depends(verificar_pedir_ot)):
    """Frota enxuta para o mobile do mecânico (qualquer logado): só o
    necessário para escolher a máquina do pedido."""
    rows = await ajard_query(
        """SELECT id, codigo, descricao FROM operacional.equipamentos
           WHERE ativo=true AND COALESCE(categoria,'') <> 'apoio'
           ORDER BY codigo""")
    return [dict(r) for r in rows]


@router.post("/manutencao/api/pedidos/externo")
async def pedido_externo(request: Request, payload=Depends(verificar_pedir_ot)):
    """(28/08/2026) CANAL EXTERNO de Pedido de OT — mecânico/operador pelo
    mobile. Gate leve (verificar_token): quem pede não precisa do módulo;
    quem TRIA (Bruna) continua atrás do gate. Solicitante = o próprio
    usuário do token."""
    await _garantir_pedidos()
    d = await request.json()
    descricao = (d.get("descricao") or "").strip()
    if not descricao:
        raise HTTPException(status_code=400, detail="Descreva o problema")
    uid = await _usuario_id(payload)
    dados = {"via": "mobile", "origem": "mobile-mecanico",
             "grau_urgencia": (d.get("grau_urgencia") or "normal"),
             "equipamento_id": d.get("equipamento_id") or None,
             "solicitante_id": uid}
    row, numero = await _inserir_pedido(dados, descricao, uid)
    return {"ok": True, "numero": numero, "id": str(row["id"])}


@router.get("/manutencao/pedido", response_class=HTMLResponse)
async def pagina_pedido_mobile():
    """Página mobile do mecânico (canal externo de pedidos)."""
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "manutencao"))
    p = os.path.join(raiz, "static", "pedido-mobile.html")
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read()
    raise HTTPException(status_code=404, detail="pedido-mobile.html não encontrado")


@router.get("/manutencao/api/meus-pedidos")
async def meus_pedidos(payload=Depends(verificar_pedir_ot)):
    """Acompanhamento do mecânico: os pedidos DELE, com o destino de cada
    um — aberto na triagem, virou OT (nº + estado) ou recusado (motivo)."""
    await _garantir_pedidos()
    uid = await _usuario_id(payload)
    rows = await ajard_query(
        """SELECT p.numero, p.data_pedido, p.grau_urgencia, p.descricao, p.status,
                  p.motivo_recusa, p.via,
                  eq.codigo AS equipamento_codigo,
                  o.id AS ot_id, o.numero AS ot_numero, o.status AS ot_status
           FROM manutencao.pedidos p
           LEFT JOIN operacional.equipamentos eq ON eq.id = p.equipamento_id
           LEFT JOIN manutencao.ot o ON o.id = p.ot_id
           WHERE p.solicitante_id = %s AND p.ativo = true
             AND (p.status = 'aberto' OR p.data_pedido >= now() - interval '30 days')
           ORDER BY p.data_pedido DESC LIMIT 40""", (uid,))   # (20/10/2026) app: histórico curto (30 d); o completo fica na gestão
    return [dict(r) for r in rows]


_APONT_OK = False
async def _garantir_apontamentos():
    """(14/10/2026) Apontamentos do mecânico como REGISTRO próprio (antes era só
    uma linha de texto no histórico): permite ao mecânico conferir, editar e
    excluir o que registrou antes de a gestão encerrar a OT. A linha do
    histórico e a linha de mão de obra continuam existindo — ficam ligadas
    pelo id do apontamento e acompanham edição/exclusão."""
    global _APONT_OK
    if _APONT_OK:
        return
    await _garantir_mao_obra()
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.ot_apontamentos (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ot_id UUID NOT NULL,
            usuario_id UUID,
            relato TEXT NOT NULL,
            horas NUMERIC(8,2),
            horimetro NUMERIC(12,1),
            concluido BOOLEAN DEFAULT false,
            mao_obra_id UUID,
            historico_id UUID,
            ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now(),
            atualizado_em TIMESTAMPTZ)""", fetch="none")
    await ajard_query("CREATE INDEX IF NOT EXISTS ix_ot_apont_ot ON manutencao.ot_apontamentos (ot_id)", fetch="none")
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS ot_id UUID", fetch="none")
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS categoria TEXT", fetch="none")
    await ajard_query("ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS apontamento_id UUID", fetch="none")
    _APONT_OK = True


_OT_STATUS_APONTAVEL = ("em_andamento", "aguardando_peca", "aberta", "programada")


def _apont_validar(d):
    relato = (d.get("relato") or "").strip()
    if len(relato) < 10:
        raise HTTPException(status_code=400, detail="Descreva o que foi feito (pelo menos 10 letras)")
    horas = _mo_num(d.get("horas"))
    # (14/10/2026) Mesma regra do app: ao sinalizar concluído, horas obrigatórias
    # (custo de MO da OT); horas fora de 0–24 é digitação errada.
    if horas is not None and (horas <= 0 or horas > 24):
        raise HTTPException(status_code=400, detail="Horas trabalhadas: informe entre 0,25 e 24")
    concluido = bool(d.get("concluido"))
    if concluido and not horas:
        raise HTTPException(status_code=400, detail="Para sinalizar concluído, informe as horas trabalhadas")
    hor = _mo_num(d.get("horimetro"))
    if hor is not None and hor <= 0:
        raise HTTPException(status_code=400, detail="Horímetro/KM inválido")
    return relato, horas, hor, concluido


def _apont_texto(relato, horas, hor, concluido, editado=False):
    partes = [f"🔧 Execução (mecânico): {relato}"]
    if hor is not None:
        partes.append(f"Horímetro/KM no serviço: {hor:g}")
    if horas:
        partes.append(f"⏱ Mão de obra: {horas:g} h")
    if concluido:
        partes.append("✅ SINALIZOU SERVIÇO CONCLUÍDO — pronto para encerramento pela gestão")
    if editado:
        partes.append(f"(editado pelo mecânico em {datetime.now().strftime('%d/%m %H:%M')})")
    return " · ".join(partes)


async def _apont_do_usuario(ap_id, ot_id, payload):
    """Carrega o apontamento e garante que quem mexe é o autor (ou gestor) e a OT está aberta."""
    o, uid = await _ot_mobile_pode(ot_id, payload)
    if o["status"] not in _OT_STATUS_APONTAVEL:
        raise HTTPException(status_code=400, detail="OT já encerrada — apontamento indisponível")
    ap = await ajard_query("SELECT * FROM manutencao.ot_apontamentos WHERE id=%s AND ot_id=%s AND ativo=true",
                           (ap_id, ot_id), fetch="one")
    if not ap:
        raise HTTPException(status_code=404, detail="Apontamento não encontrado")
    gestor = await _tem_modulo(payload, {"manutencao"})   # (21/10/2026) gestão = módulo, não perfil
    if not gestor and str(ap["usuario_id"]) != str(uid):
        raise HTTPException(status_code=403, detail="Só quem registrou pode alterar este apontamento")
    return o, uid, ap


@router.post("/manutencao/api/ots/{ot_id}/apontamento")
async def ot_apontamento(ot_id: str, request: Request, payload=Depends(verificar_pedir_ot)):
    """(28/08/2026) Mecânico alimenta o FECHAMENTO sem fechar: relato do
    serviço + horímetro + sinal de concluído vão para o HISTÓRICO da OT.
    Quem pode: o solicitante do pedido de origem ou o responsável da OT.
    O estado NÃO muda — o fechamento real é do admin/gestão.
    (14/10/2026) Grava também em ot_apontamentos e devolve o id — o app usa
    para ligar as fotos e para editar/excluir depois."""
    await _garantir_apontamentos()
    d = await request.json()
    relato, horas, hor, concluido = _apont_validar(d)
    o, uid = await _ot_mobile_pode(ot_id, payload)
    if o["status"] not in _OT_STATUS_APONTAVEL:
        raise HTTPException(status_code=400, detail="OT já encerrada — apontamento indisponível")
    await _ot_assumir_se_vaga(o, uid, ot_id)
    mo_id = None
    if horas:
        sug = await ajard_query(
            """SELECT custo_hora FROM manutencao.ot_mao_obra
               WHERE usuario_id=%s AND ativo=true AND custo_hora IS NOT NULL
               ORDER BY criado_em DESC LIMIT 1""", (uid,), fetch="one")
        mo = await ajard_query_id(
            """INSERT INTO manutencao.ot_mao_obra (ot_id, usuario_id, data, horas, custo_hora, observacao, origem, criado_por)
               VALUES (%s,%s,now()::date,%s,%s,%s,'mobile',%s)""",
            (ot_id, uid, horas, (sug or {}).get("custo_hora"), relato[:200], uid))
        mo_id = mo["id"] if mo else None
    h = await ajard_query_id(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, o["status"], o["status"], _apont_texto(relato, horas, hor, concluido), uid))
    ap = await ajard_query_id(
        """INSERT INTO manutencao.ot_apontamentos (ot_id, usuario_id, relato, horas, horimetro, concluido, mao_obra_id, historico_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ot_id, uid, relato, horas, hor, concluido, mo_id, h["id"] if h else None))
    return {"ok": True, "numero": o["numero"], "id": str(ap["id"]) if ap else None}


@router.get("/manutencao/api/ots/{ot_id}/apontamentos")
async def ot_apontamentos_listar(ot_id: str, payload=Depends(verificar_pedir_ot)):
    """Tudo que já foi registrado na OT pelo app (de qualquer mecânico), com as
    fotos de cada registro. `meu` = pode editar/excluir."""
    await _garantir_apontamentos()
    o, uid = await _ot_mobile_pode(ot_id, payload)
    gestor = await _tem_modulo(payload, {"manutencao"})   # (21/10/2026) gestão = módulo, não perfil
    rows = await ajard_query(
        """SELECT a.*, u.nome AS usuario_nome
           FROM manutencao.ot_apontamentos a LEFT JOIN public.usuarios_garra u ON u.id = a.usuario_id
           WHERE a.ot_id=%s AND a.ativo=true ORDER BY a.criado_em DESC""", (ot_id,)) or []
    fotos = await ajard_query(
        """SELECT id, apontamento_id, categoria, path FROM manutencao.equipamento_documentos
           WHERE ot_id=%s AND ativo=true AND apontamento_id IS NOT NULL AND content_type LIKE 'image/%%'
           ORDER BY criado_em""", (ot_id,)) or []
    por_ap = {}
    for f in fotos:
        por_ap.setdefault(str(f["apontamento_id"]), []).append(
            {"id": str(f["id"]), "categoria": f["categoria"], "url": storage_url(f["path"])})
    return {"numero": o["numero"], "status": o["status"], "aberta": o["status"] in _OT_STATUS_APONTAVEL,
            "apontamentos": [{
                "id": str(r["id"]), "relato": r["relato"],
                "horas": float(r["horas"]) if r["horas"] is not None else None,
                "horimetro": float(r["horimetro"]) if r["horimetro"] is not None else None,
                "concluido": bool(r["concluido"]), "usuario_nome": r["usuario_nome"],
                "criado_em": r["criado_em"].isoformat() if r["criado_em"] else None,
                "atualizado_em": r["atualizado_em"].isoformat() if r["atualizado_em"] else None,
                "meu": gestor or str(r["usuario_id"]) == str(uid),
                "fotos": por_ap.get(str(r["id"]), [])} for r in rows]}


@router.put("/manutencao/api/ots/{ot_id}/apontamentos/{ap_id}")
async def ot_apontamento_editar(ot_id: str, ap_id: str, request: Request, payload=Depends(verificar_pedir_ot)):
    await _garantir_apontamentos()
    d = await request.json()
    relato, horas, hor, concluido = _apont_validar(d)
    o, uid, ap = await _apont_do_usuario(ap_id, ot_id, payload)
    mo_id = ap["mao_obra_id"]
    if horas:
        if mo_id:
            await ajard_query("UPDATE manutencao.ot_mao_obra SET horas=%s, observacao=%s WHERE id=%s",
                              (horas, relato[:200], mo_id), fetch="none")
        else:
            sug = await ajard_query(
                """SELECT custo_hora FROM manutencao.ot_mao_obra
                   WHERE usuario_id=%s AND ativo=true AND custo_hora IS NOT NULL
                   ORDER BY criado_em DESC LIMIT 1""", (ap["usuario_id"],), fetch="one")
            mo = await ajard_query_id(
                """INSERT INTO manutencao.ot_mao_obra (ot_id, usuario_id, data, horas, custo_hora, observacao, origem, criado_por)
                   VALUES (%s,%s,%s,%s,%s,%s,'mobile',%s)""",
                (ot_id, ap["usuario_id"], ap["criado_em"], horas, (sug or {}).get("custo_hora"), relato[:200], uid))
            mo_id = mo["id"] if mo else None
    elif mo_id:
        await ajard_query("UPDATE manutencao.ot_mao_obra SET ativo=false WHERE id=%s", (mo_id,), fetch="none")
        mo_id = None
    if ap["historico_id"]:
        await ajard_query("UPDATE manutencao.ot_historico SET observacao=%s WHERE id=%s",
                          (_apont_texto(relato, horas, hor, concluido, editado=True), ap["historico_id"]), fetch="none")
    await ajard_query(
        """UPDATE manutencao.ot_apontamentos SET relato=%s, horas=%s, horimetro=%s, concluido=%s,
                  mao_obra_id=%s, atualizado_em=now() WHERE id=%s""",
        (relato, horas, hor, concluido, mo_id, ap_id), fetch="none")
    return {"ok": True, "id": ap_id}


@router.delete("/manutencao/api/ots/{ot_id}/apontamentos/{ap_id}")
async def ot_apontamento_excluir(ot_id: str, ap_id: str, payload=Depends(verificar_pedir_ot)):
    """Exclusão lógica: some do app e das somas de MO; o histórico da OT guarda
    o rastro ("removido pelo mecânico") e as fotos vão junto."""
    await _garantir_apontamentos()
    o, uid, ap = await _apont_do_usuario(ap_id, ot_id, payload)
    if ap["mao_obra_id"]:
        await ajard_query("UPDATE manutencao.ot_mao_obra SET ativo=false WHERE id=%s", (ap["mao_obra_id"],), fetch="none")
    fotos = await ajard_query("SELECT id, path FROM manutencao.equipamento_documentos WHERE apontamento_id=%s AND ativo=true",
                              (ap_id,)) or []
    if fotos:
        await ajard_query("UPDATE manutencao.equipamento_documentos SET ativo=false WHERE apontamento_id=%s", (ap_id,), fetch="none")
        try:
            storage_delete([f["path"] for f in fotos])
        except Exception as e:
            print(f"[Apontamento] falha ao apagar fotos no Storage: {e}")
    if ap["historico_id"]:
        await ajard_query(
            "UPDATE manutencao.ot_historico SET observacao = '🗑 (removido pelo mecânico) ' || observacao WHERE id=%s",
            (ap["historico_id"],), fetch="none")
    await ajard_query("UPDATE manutencao.ot_apontamentos SET ativo=false, atualizado_em=now() WHERE id=%s", (ap_id,), fetch="none")
    return {"ok": True, "fotos_removidas": len(fotos)}


# (14/10/2026) FOTOS DA EXECUÇÃO pelo mobile do mecânico: notinhas (cupom/NF de peça ou
# serviço comprado na rua) e fotos do serviço. Guardadas no mesmo lugar dos documentos
# da OT (manutencao.equipamento_documentos com ot_id), com categoria, para aparecerem
# na aba Documentos da OT no desktop e na conferência financeira.
_FOTO_CATEGORIAS = {"nota": ("nota_fiscal", "Notinha / cupom"), "servico": ("foto_servico", "Foto do serviço")}


async def _ot_mobile_pode(ot_id, payload):
    uid = await _usuario_id(payload)
    o = await ajard_query(
        """SELECT o.id, o.numero, o.status, o.equipamento_id, o.responsavel_id, p.solicitante_id
           FROM manutencao.ot o LEFT JOIN manutencao.pedidos p ON p.id = o.pedido_id
           WHERE o.id=%s AND o.ativo=true""", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    # (14/10/2026) Quem EXECUTA (módulo ot_executar, ou perfil da manutenção) aponta
    # em qualquer OT aberta — na oficina um mecânico cobre o outro. Quem só PEDE
    # (pedir_ot) continua restrito às OTs dos próprios pedidos / onde é responsável.
    if await _e_executor(payload):
        return o, uid
    if str(uid) not in (str(o["responsavel_id"]), str(o["solicitante_id"])):
        raise HTTPException(status_code=403, detail="Esta OT não é sua — peça o módulo 'Executar OT' para apontar em OTs de outros")
    return o, uid


async def _e_executor(payload) -> bool:
    return await _tem_modulo(payload, {"ot_executar", "manutencao"})


async def _ot_assumir_se_vaga(o, uid, ot_id):
    if o.get("responsavel_id") or not uid:
        return
    await ajard_query("UPDATE manutencao.ot SET responsavel_id=%s, atualizado_em=now() WHERE id=%s AND responsavel_id IS NULL",
                      (uid, ot_id), fetch="none")
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, o["status"], o["status"], "👤 Assumiu a OT pelo app (primeiro apontamento)", uid), fetch="none")
    o["responsavel_id"] = uid


@router.get("/manutencao/api/ots-abertas-mobile")
async def ots_abertas_mobile(payload=Depends(verificar_pedir_ot)):
    """(14/10/2026) Todas as OTs em aberto para o app do mecânico — não só as
    dos pedidos dele. `minha` = ele é responsável ou solicitante. Quem não tem
    'Executar OT' recebe só as suas (e o app esconde a aba)."""
    await _garantir_pedidos(); await _garantir_apontamentos()
    uid = await _usuario_id(payload)
    executor = await _e_executor(payload)
    rows = await ajard_query(
        """SELECT o.id, o.numero, o.status, o.prioridade, o.tipo, o.descricao, o.data_abertura, o.data_prevista,
                  o.responsavel_id, o.solicitante_id,
                  eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                  r.nome AS responsavel_nome, p.solicitante_id AS pedido_solicitante_id,
                  (SELECT COUNT(*) FROM manutencao.ot_apontamentos a WHERE a.ot_id=o.id AND a.ativo=true)::int AS apontamentos,
                  (SELECT bool_or(a.concluido) FROM manutencao.ot_apontamentos a WHERE a.ot_id=o.id AND a.ativo=true) AS sinal_concluido
           FROM manutencao.ot o
           LEFT JOIN operacional.equipamentos eq ON eq.id = o.equipamento_id
           LEFT JOIN public.usuarios_garra r ON r.id = o.responsavel_id
           LEFT JOIN manutencao.pedidos p ON p.id = o.pedido_id
           WHERE o.ativo=true AND o.status IN ('programada','aberta','em_andamento','aguardando_peca')
           ORDER BY CASE o.status WHEN 'em_andamento' THEN 0 WHEN 'aguardando_peca' THEN 1 WHEN 'aberta' THEN 2 ELSE 3 END,
                    CASE o.prioridade WHEN 'urgente' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END,
                    o.data_abertura DESC LIMIT 200""") or []
    out = []
    for r in rows:
        d = dict(r)
        d["minha"] = str(uid) in (str(r["responsavel_id"]), str(r["solicitante_id"]), str(r["pedido_solicitante_id"]))
        for k in ("id", "responsavel_id", "solicitante_id", "pedido_solicitante_id"):
            d[k] = str(d[k]) if d[k] else None
        for k in ("data_abertura", "data_prevista"):
            d[k] = d[k].isoformat() if d[k] else None
        if executor or d["minha"]:
            out.append(d)
    return {"executor": executor, "ots": out}


@router.post("/manutencao/api/ots/{ot_id}/fotos-execucao")
async def ot_fotos_execucao(ot_id: str, fotos: list[UploadFile] = File(...), tipo: str = "nota",
                            descricao: str = None, apontamento_id: str = None, payload=Depends(verificar_pedir_ot)):
    """Até 10 fotos por envio, cada uma ≤ 8 MB (o app já comprime para ~1600 px / JPEG).
    `apontamento_id` liga a foto ao registro do mecânico (edição/exclusão acompanham)."""
    await _garantir_apontamentos()
    o, uid = await _ot_mobile_pode(ot_id, payload)
    if apontamento_id:
        ok = await ajard_query("SELECT 1 FROM manutencao.ot_apontamentos WHERE id=%s AND ot_id=%s AND ativo=true",
                               (apontamento_id, ot_id), fetch="one")
        if not ok:
            raise HTTPException(status_code=404, detail="Apontamento não encontrado")
    if o["status"] not in ("em_andamento", "aguardando_peca", "aberta", "programada"):
        raise HTTPException(status_code=400, detail="OT já encerrada — anexos indisponíveis")
    cat, rotulo = _FOTO_CATEGORIAS.get((tipo or "nota").lower(), _FOTO_CATEGORIAS["nota"])
    if not fotos or len(fotos) > 10:
        raise HTTPException(status_code=400, detail="Envie de 1 a 10 fotos")
    import uuid as _uuid
    salvas = []
    for f in fotos:
        conteudo = await f.read()
        if not conteudo:
            continue
        if len(conteudo) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail=f"{f.filename}: acima de 8 MB")
        ctype = f.content_type or "image/jpeg"
        if not ctype.startswith("image/"):
            raise HTTPException(status_code=400, detail=f"{f.filename}: só imagens")
        ext = "png" if "png" in ctype else "jpg"
        path = storage_upload(conteudo, f"manutencao/ots/{ot_id}/{cat}/{_uuid.uuid4().hex}.{ext}", content_type=ctype)
        nome = f"{rotulo} {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        row = await ajard_query_id(
            """INSERT INTO manutencao.equipamento_documentos
                 (equipamento_id, ot_id, nome, path, content_type, tamanho, descricao, categoria, usuario_id, apontamento_id)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (o["equipamento_id"], ot_id, nome, path, ctype, len(conteudo), (descricao or "").strip() or None, cat, uid, apontamento_id))
        salvas.append({"id": str(row["id"]) if row else None, "url": storage_url(path)})
    if salvas:
        await ajard_query(
            """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
               VALUES (%s,%s,%s,%s,%s)""",
            (ot_id, o["status"], o["status"], f"📎 {len(salvas)} foto(s) — {rotulo} (mobile)" + (f": {descricao.strip()}" if descricao and descricao.strip() else ""), uid),
            fetch="none")
    return {"ok": True, "numero": o["numero"], "salvas": len(salvas), "fotos": salvas}


@router.get("/manutencao/api/ots/{ot_id}/fotos-execucao")
async def ot_fotos_execucao_listar(ot_id: str, payload=Depends(verificar_pedir_ot)):
    await _garantir_apontamentos()
    await _ot_mobile_pode(ot_id, payload)
    rows = await ajard_query(
        """SELECT d.id, d.nome, d.path, d.categoria, d.descricao, d.criado_em, u.nome AS usuario_nome
           FROM manutencao.equipamento_documentos d LEFT JOIN public.usuarios_garra u ON u.id = d.usuario_id
           WHERE d.ot_id=%s AND d.ativo=true AND d.content_type LIKE 'image/%%'
           ORDER BY d.criado_em DESC""", (ot_id,)) or []
    return [{"id": str(r["id"]), "nome": r["nome"], "categoria": r["categoria"], "descricao": r["descricao"],
             "criado_em": r["criado_em"].isoformat() if r["criado_em"] else None, "usuario_nome": r["usuario_nome"],
             "url": storage_url(r["path"])} for r in rows]


@router.delete("/manutencao/api/ots/{ot_id}/fotos-execucao/{doc_id}")
async def ot_foto_execucao_excluir(ot_id: str, doc_id: str, payload=Depends(verificar_pedir_ot)):
    """Mecânico remove uma foto que ele mesmo subiu (OT ainda aberta)."""
    await _garantir_apontamentos()
    o, uid = await _ot_mobile_pode(ot_id, payload)
    if o["status"] not in _OT_STATUS_APONTAVEL:
        raise HTTPException(status_code=400, detail="OT já encerrada — anexos indisponíveis")
    f = await ajard_query("SELECT id, path, usuario_id FROM manutencao.equipamento_documentos WHERE id=%s AND ot_id=%s AND ativo=true",
                          (doc_id, ot_id), fetch="one")
    if not f:
        raise HTTPException(status_code=404, detail="Foto não encontrada")
    gestor = await _tem_modulo(payload, {"manutencao"})   # (21/10/2026) gestão = módulo, não perfil
    if not gestor and str(f["usuario_id"]) != str(uid):
        raise HTTPException(status_code=403, detail="Só quem enviou a foto pode removê-la")
    await ajard_query("UPDATE manutencao.equipamento_documentos SET ativo=false WHERE id=%s", (doc_id,), fetch="none")
    try:
        storage_delete([f["path"]])
    except Exception as e:
        print(f"[Apontamento] falha ao apagar foto no Storage: {e}")
    return {"ok": True}


async def _garantir_montagens():
    """(28/08/2026) Subsistemas/rotáveis (padrão ManWinWin): o vínculo VIVO
    mora em operacional.equipamentos (equipamento_pai + posicao — Onda 2);
    esta tabela guarda o HISTÓRICO que viaja com o componente entre pais."""
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.montagens (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          equipamento_id UUID NOT NULL,
          pai_id UUID NOT NULL,
          posicao TEXT,
          data_montagem TIMESTAMPTZ NOT NULL DEFAULT now(),
          data_desmontagem TIMESTAMPTZ,
          leitura_pai_montagem NUMERIC,
          leitura_pai_desmontagem NUMERIC,
          usuario_id UUID,
          observacao TEXT
        )""", fetch="none")


def _leitura_de(eq):
    v = eq.get("km_atual") if (eq.get("medicao") == "km") else eq.get("horimetro_atual")
    if v is None:
        v = eq.get("horimetro_atual") if eq.get("horimetro_atual") is not None else eq.get("km_atual")
    return float(v) if v is not None else None


@router.get("/manutencao/api/equipamentos/{eq_id}/subsistemas")
async def subsistemas(eq_id: str, _auth=Depends(verificar_manutencao)):
    """Janela de duas faces: PAI → filhos montados (posição, sistema, OTs
    abertas); COMPONENTE → onde está montado + vida acumulada."""
    await _garantir_montagens()
    eu = await ajard_query(
        """SELECT e.id, e.codigo, e.descricao, e.categoria, e.medicao,
                  e.horimetro_atual, e.km_atual, e.posicao,
                  p.id AS pai_id, p.codigo AS pai_codigo, p.descricao AS pai_desc
           FROM operacional.equipamentos e
           LEFT JOIN operacional.equipamentos p ON p.id = e.equipamento_pai
           WHERE e.id=%s""", (eq_id,), fetch="one")
    if not eu:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    filhos = await ajard_query(
        """SELECT f.id, f.codigo, f.descricao, f.posicao, f.categoria,
                  s.nome AS sistema_nome,
                  (SELECT COUNT(*) FROM manutencao.ot o
                    WHERE o.equipamento_id=f.id AND o.ativo=true
                      AND o.status NOT IN ('concluida','cancelada'))::int AS ots_abertas
           FROM operacional.equipamentos f
           LEFT JOIN manutencao.sistemas s ON s.codigo = f.sistema_codigo
           WHERE f.equipamento_pai=%s AND f.ativo=true
           ORDER BY f.posicao NULLS LAST, f.codigo""", (eq_id,))
    hist = await ajard_query(
        """SELECT m.posicao, m.data_montagem, m.data_desmontagem,
                  m.leitura_pai_montagem, m.leitura_pai_desmontagem,
                  p.codigo AS pai_codigo
           FROM manutencao.montagens m
           JOIN operacional.equipamentos p ON p.id = m.pai_id
           WHERE m.equipamento_id=%s
           ORDER BY m.data_montagem DESC LIMIT 30""", (eq_id,))
    vida = 0.0
    historico = []
    for h in hist:
        rodado = None
        if h["leitura_pai_montagem"] is not None and h["leitura_pai_desmontagem"] is not None:
            rodado = float(h["leitura_pai_desmontagem"]) - float(h["leitura_pai_montagem"])
            if rodado > 0:
                vida += rodado
        historico.append({
            "pai": h["pai_codigo"], "posicao": h["posicao"],
            "montagem": h["data_montagem"].isoformat() if h["data_montagem"] else None,
            "desmontagem": h["data_desmontagem"].isoformat() if h["data_desmontagem"] else None,
            "rodado": rodado})
    livres = await ajard_query(
        """SELECT id, codigo, descricao FROM operacional.equipamentos
           WHERE categoria='componente' AND ativo=true
             AND (equipamento_pai IS NULL OR equipamento_pai <> %s)
           ORDER BY codigo LIMIT 200""", (eq_id,))
    return {"eu": {"id": str(eu["id"]), "codigo": eu["codigo"], "descricao": eu["descricao"],
                   "categoria": eu["categoria"], "posicao": eu["posicao"],
                   "pai": ({"id": str(eu["pai_id"]), "codigo": eu["pai_codigo"],
                            "descricao": eu["pai_desc"]} if eu["pai_id"] else None)},
            "filhos": [dict(f, id=str(f["id"])) for f in filhos],
            "historico": historico, "vida_acumulada": round(vida, 1),
            "componentes_livres": [dict(c, id=str(c["id"])) for c in livres]}


@router.post("/manutencao/api/equipamentos/{pai_id}/montar")
async def montar(pai_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """Monta um componente no pai: fecha a montagem anterior (carimbando a
    leitura do pai antigo), grava o vínculo vivo e abre o novo registro."""
    await _garantir_montagens()
    d = await request.json()
    comp_id = d.get("componente_id")
    posicao = (d.get("posicao") or "").strip() or None
    pai = await ajard_query(
        "SELECT id, codigo, medicao, horimetro_atual, km_atual FROM operacional.equipamentos WHERE id=%s AND ativo=true",
        (pai_id,), fetch="one")
    comp = await ajard_query(
        "SELECT id, codigo, equipamento_pai FROM operacional.equipamentos WHERE id=%s AND ativo=true",
        (comp_id,), fetch="one")
    if not pai or not comp:
        raise HTTPException(status_code=404, detail="Pai ou componente não encontrado")
    if str(pai["id"]) == str(comp["id"]):
        raise HTTPException(status_code=400, detail="Um equipamento não monta nele mesmo")
    uid = await _usuario_id(payload)
    if comp["equipamento_pai"]:
        antigo = await ajard_query(
            "SELECT medicao, horimetro_atual, km_atual FROM operacional.equipamentos WHERE id=%s",
            (comp["equipamento_pai"],), fetch="one")
        await ajard_query(
            """UPDATE manutencao.montagens
               SET data_desmontagem=now(), leitura_pai_desmontagem=%s
               WHERE equipamento_id=%s AND data_desmontagem IS NULL""",
            (_leitura_de(dict(antigo)) if antigo else None, comp_id), fetch="none")
    await ajard_query(
        "UPDATE operacional.equipamentos SET equipamento_pai=%s, posicao=%s, atualizado_em=now() WHERE id=%s",
        (pai_id, posicao, comp_id), fetch="none")
    await ajard_query(
        """INSERT INTO manutencao.montagens
             (equipamento_id, pai_id, posicao, leitura_pai_montagem, usuario_id, observacao)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (comp_id, pai_id, posicao, _leitura_de(dict(pai)), uid,
         (d.get("observacao") or "").strip() or None), fetch="none")
    return {"ok": True, "componente": comp["codigo"], "pai": pai["codigo"]}


@router.post("/manutencao/api/equipamentos/{comp_id}/desmontar")
async def desmontar(comp_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """Desmonta: fecha o registro com a leitura do pai e solta o vínculo."""
    await _garantir_montagens()
    comp = await ajard_query(
        "SELECT id, codigo, equipamento_pai FROM operacional.equipamentos WHERE id=%s",
        (comp_id,), fetch="one")
    if not comp or not comp["equipamento_pai"]:
        raise HTTPException(status_code=400, detail="Componente não está montado em ninguém")
    pai = await ajard_query(
        "SELECT medicao, horimetro_atual, km_atual FROM operacional.equipamentos WHERE id=%s",
        (comp["equipamento_pai"],), fetch="one")
    await ajard_query(
        """UPDATE manutencao.montagens
           SET data_desmontagem=now(), leitura_pai_desmontagem=%s
           WHERE equipamento_id=%s AND data_desmontagem IS NULL""",
        (_leitura_de(dict(pai)) if pai else None, comp_id), fetch="none")
    await ajard_query(
        "UPDATE operacional.equipamentos SET equipamento_pai=NULL, posicao=NULL, atualizado_em=now() WHERE id=%s",
        (comp_id,), fetch="none")
    return {"ok": True, "componente": comp["codigo"]}


# ══ (20/10/2026) NOTAS DE MANUTENÇÃO — balcão financeiro das notinhas das OTs ══
# Mesmo padrão de /abastecimento/notas: a Luana vê a foto da notinha que o mecânico
# mandou pelo app (ou anexada na OT), DIGITA o valor ao conferir, marca "lançada na MAIS".
_NOTAS_OT_OK = False


async def _garantir_notas_ot():
    global _NOTAS_OT_OK
    if _NOTAS_OT_OK:
        return
    await _garantir_mao_obra()
    for col, tipo in (("ot_id", "UUID"), ("categoria", "TEXT"), ("apontamento_id", "UUID"),
                      ("valor_nota", "NUMERIC(14,2)"), ("numero_nota", "TEXT"),
                      ("lancada_mais", "BOOLEAN DEFAULT false"), ("lancada_por", "UUID"), ("lancada_em", "TIMESTAMPTZ")):
        await ajard_query(f"ALTER TABLE manutencao.equipamento_documentos ADD COLUMN IF NOT EXISTS {col} {tipo}", fetch="none")
    _NOTAS_OT_OK = True


async def verificar_notas_ot(payload=Depends(verificar_token)):
    """Gate do balcão: perfis livres + módulo manutencao_notas (ou manutencao / abastecimento_notas)."""
    if (payload.get("perfil") or "").lower() in {"admin", "gestor", "bruna", "luana"}:
        return payload
    if await _tem_modulo(payload, {"manutencao_notas", "manutencao", "abastecimento_notas"}):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para Notas de Manutenção")


@router.get("/manutencao/api/notas-financeiro")
async def notas_ot_listar(mes: str = None, fornecedor: str = None, pendentes: int = 0, payload=Depends(verificar_notas_ot)):
    await _garantir_notas_ot()
    from datetime import date as _date
    mes = (mes or _date.today().strftime("%Y-%m"))[:7]
    where, params = ["d.ativo=true", "d.categoria='nota_fiscal'", "d.ot_id IS NOT NULL",
                     "to_char(d.criado_em AT TIME ZONE 'America/Sao_Paulo','YYYY-MM')=%s"], [mes]
    if fornecedor:
        where.append("o.fornecedor_id=%s"); params.append(fornecedor)
    if pendentes:
        where.append("COALESCE(d.lancada_mais,false)=false")
    rows = await ajard_query(
        f"""SELECT d.id, d.criado_em, d.path, d.descricao, d.valor_nota, d.numero_nota, d.lancada_mais, d.lancada_em,
                   o.numero AS ot_numero, o.id AS ot_id, o.status AS ot_status, o.fornecedor_id,
                   eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                   fo.nome AS fornecedor_nome, u.nome AS usuario_nome,
                   (SELECT nome FROM public.usuarios_garra x WHERE x.id=d.lancada_por) AS lancada_por_nome,
                   (SELECT COALESCE(SUM(m.quantidade*COALESCE(m.custo_unitario,0)),0) FROM manutencao.movimentacoes m
                     WHERE m.ot_id=o.id AND m.tipo='aplicacao_direta') AS compras_ot,
                   (SELECT COALESCE(SUM(x.valor),0) FROM manutencao.ot_outros x WHERE x.ot_id=o.id AND x.ativo=true AND COALESCE(x.planejado,false)=false) AS outros_ot
            FROM manutencao.equipamento_documentos d
            JOIN manutencao.ot o ON o.id=d.ot_id
            JOIN operacional.equipamentos eq ON eq.id=o.equipamento_id
            LEFT JOIN public.fornecedores fo ON fo.id=o.fornecedor_id
            LEFT JOIN public.usuarios_garra u ON u.id=d.usuario_id
            WHERE {' AND '.join(where)}
            ORDER BY d.criado_em DESC""", params) or []
    urls = storage_urls([r["path"] for r in rows if r.get("path")])
    notas, totais = [], {}
    for r in rows:
        d = dict(r)
        d["id"] = str(d["id"]); d["ot_id"] = str(d["ot_id"]); d["fornecedor_id"] = str(d["fornecedor_id"]) if d.get("fornecedor_id") else None
        d["data"] = d["criado_em"].isoformat() if d.get("criado_em") else None
        d["lancada_em"] = d["lancada_em"].isoformat() if d.get("lancada_em") else None
        d["foto_url"] = urls.get(d.pop("path"), "")
        d.pop("criado_em", None)
        for k in ("valor_nota", "compras_ot", "outros_ot"):
            d[k] = float(d[k]) if d.get(k) is not None else None
        notas.append(d)
        k = d.get("fornecedor_nome") or "— sem fornecedor na OT"
        t = totais.setdefault(k, {"fornecedor": k, "notas": 0, "valor": 0.0, "pendentes": 0, "sem_valor": 0})
        t["notas"] += 1; t["valor"] += d["valor_nota"] or 0
        if not d.get("lancada_mais"): t["pendentes"] += 1
        if d.get("valor_nota") is None: t["sem_valor"] += 1
    return {"mes": mes, "notas": notas, "totais": sorted(totais.values(), key=lambda x: x["fornecedor"])}


@router.patch("/manutencao/api/notas-financeiro/{doc_id}")
async def notas_ot_conferir(doc_id: str, request: Request, payload=Depends(verificar_notas_ot)):
    """Conferência: valor digitado, nº da nota e carimbo 'lançada na MAIS' (quem/quando)."""
    await _garantir_notas_ot()
    d = await request.json()
    sets, params = [], []
    if "valor_nota" in d:
        v = d.get("valor_nota")
        try:
            sets.append("valor_nota=%s"); params.append(float(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) and "," in v else (float(v) if v not in (None, "") else None))
        except ValueError:
            raise HTTPException(status_code=400, detail="Valor inválido")
    if "numero_nota" in d:
        sets.append("numero_nota=%s"); params.append((d.get("numero_nota") or "").strip() or None)
    if "lancada" in d:
        uid = await _usuario_id(payload)
        if d.get("lancada"):
            sets += ["lancada_mais=true", "lancada_por=%s", "lancada_em=now()"]; params.append(uid)
        else:
            sets += ["lancada_mais=false", "lancada_por=NULL", "lancada_em=NULL"]
    if not sets:
        return {"ok": True}
    params.append(doc_id)
    await ajard_query(f"UPDATE manutencao.equipamento_documentos SET {', '.join(sets)} WHERE id=%s AND categoria='nota_fiscal'", params, fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/notas-financeiro/excel")
async def notas_ot_excel(mes: str = None, fornecedor: str = None, payload=Depends(verificar_notas_ot)):
    from fastapi.responses import Response
    import io
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    dados = await notas_ot_listar(mes=mes, fornecedor=fornecedor, pendentes=0, payload=payload)
    mes = dados["mes"]
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = f"Notas OT {mes}"
    azul = PatternFill("solid", fgColor="1A2A5E"); branco = Font(color="FFFFFF", bold=True)
    ws.append(["Data", "OT", "Equipamento", "Fornecedor", "Nº nota", "Descrição (mecânico)", "Valor (R$)", "Quem enviou", "Lançada na MAIS", "Foto (7 dias)"])
    for c in ws[1]:
        c.fill = azul; c.font = branco; c.alignment = Alignment(horizontal="center")
    brutos = await ajard_query("SELECT id, path FROM manutencao.equipamento_documentos WHERE categoria='nota_fiscal' AND ot_id IS NOT NULL AND ativo=true") or []
    caminho = {str(r["id"]): r["path"] for r in brutos}
    for n in dados["notas"]:
        raw = caminho.get(n["id"]); foto = storage_url(raw, 7 * 24 * 3600) if raw else ""
        ws.append([(n["data"] or "")[:16].replace("T", " "), n["ot_numero"], f"{n['equipamento_codigo']} — {n.get('equipamento_desc') or ''}",
                   n.get("fornecedor_nome") or "", n.get("numero_nota") or "", n.get("descricao") or "", n.get("valor_nota"),
                   n.get("usuario_nome") or "", "SIM" if n.get("lancada_mais") else "não", foto])
    ws.append([]); ws.append(["TOTAIS DO MÊS POR FORNECEDOR"]); ws[ws.max_row][0].font = Font(bold=True)
    ws.append(["Fornecedor", "Notas", "Valor (R$)", "Pendentes de lançar", "Sem valor conferido"])
    for t in dados["totais"]:
        ws.append([t["fornecedor"], t["notas"], t["valor"], t["pendentes"], t["sem_valor"]])
    for col, w in zip("ABCDEFGHIJ", (15, 14, 34, 26, 12, 40, 11, 16, 13, 60)):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO(); wb.save(buf)
    return Response(buf.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="notas-manutencao-{mes}.xlsx"'})


# ══ (20/10/2026) ETIQUETAS DE ITEM — molde da Edvania (Letter, 2 col × 5 lin, 10,2 × 5,05 cm, vão 0,5 cm) ══
# Conteúdo: código Garra · descrição · part number · compatível com (equipamentos das FMPs) · UN · local · QR.
# Sem saldo, sem custo, sem substituto (mudam; o QR leva à ficha onde isso vive).
@router.get("/manutencao/api/etiquetas-itens")
async def etiquetas_itens(codigos: str = None, familia: str = None, almox: str = None, request: Request = None, _auth=Depends(verificar_manutencao)):
    import json as _json
    try:
        import segno as _segno   # QR gerado no servidor (sem CDN); requirements: segno>=1.6
    except Exception:
        _segno = None
    base = (str(request.base_url).rstrip("/") if request else "") + "/manutencao?peca="
    await _garantir_peca_cols()
    cond, params = ["p.ativo=true"], []
    qtds = {}
    if codigos:
        # (21/10/2026) "CÓDIGO*3" = 3 etiquetas daquele item (uma por unidade quando faz sentido)
        lst = []
        for tok_ in codigos.split(","):
            tok_ = tok_.strip()
            if not tok_:
                continue
            cod, _, n = tok_.partition("*")
            cod = cod.strip()
            try:
                qtds[cod] = max(1, min(200, int(n))) if n else 1
            except ValueError:
                qtds[cod] = 1
            lst.append(cod)
        cond.append("p.codigo = ANY(%s)"); params.append(lst)
    if familia and familia.startswith("@"):
        cond.append(_classe_cond("p")); params += _classe_params(familia)
    elif familia:
        fe = _fam_efetiva("p")
        cond.append(f"({fe} = %s OR {fe} LIKE %s)"); params += [familia, familia + ".%"]
    if almox:
        cond.append("EXISTS (SELECT 1 FROM manutencao.estoque e JOIN manutencao.almoxarifados a ON a.id=e.almoxarifado_id WHERE e.peca_id=p.id AND a.codigo=%s)")
        params.append(almox)
    fe = _fam_efetiva("p")
    rows = await ajard_query(
        f"""SELECT p.id, p.codigo, p.descricao, p.unidade, p.codigo_fabricante, p.codigo_externo, p.caracteristicas,
                   {fe} AS familia_codigo,
                   (SELECT f.nome FROM manutencao.familias f WHERE f.codigo = split_part({fe},'.',1) LIMIT 1) AS familia_nome,
                   (SELECT f.nome FROM manutencao.familias f WHERE f.codigo = {fe} AND {fe} LIKE '%%.%%' LIMIT 1) AS subfamilia_nome,
                   upper(COALESCE(NULLIF(p.classe,''),
                         CASE WHEN p.codigo ~ '^[A-Za-z]\\.' THEN split_part(p.codigo,'.',1) ELSE NULL END,
                         (SELECT f.classe FROM manutencao.familias f WHERE f.codigo = split_part({fe},'.',1) LIMIT 1))) AS classe,
                   COALESCE((SELECT SUM(e.quantidade) FROM manutencao.estoque e WHERE e.peca_id=p.id),0) AS existencia,
                   (SELECT string_agg(CASE WHEN upper(COALESCE(e.localizacao,'')) LIKE upper(a.codigo)||' %%' THEN e.localizacao
                                           ELSE a.codigo || CASE WHEN COALESCE(e.localizacao,'')<>'' THEN ' ' || e.localizacao ELSE '' END END, ' · ' ORDER BY a.codigo)
                      FROM manutencao.estoque e JOIN manutencao.almoxarifados a ON a.id=e.almoxarifado_id
                     WHERE e.peca_id=p.id AND a.ativo=true) AS locais
            FROM manutencao.pecas p WHERE {' AND '.join(cond)} ORDER BY p.codigo LIMIT 300""", tuple(params)) or []
    classes = {r["codigo"]: r["nome"] for r in (await ajard_query("SELECT codigo, nome FROM manutencao.classes_materiais") or [])}
    # compatibilidade: equipamentos cujas FMPs preveem o item
    pl = await ajard_query(
        """SELECT eq.codigo AS equipamento, p.pecas FROM manutencao.planos p
           JOIN operacional.equipamentos eq ON eq.id = p.equipamento_id
           WHERE p.ativo=true AND p.pecas IS NOT NULL""") or []
    compat = {}
    for r in pl:
        itens = r["pecas"]
        if isinstance(itens, str):
            try:
                itens = _json.loads(itens)
            except Exception:
                itens = []
        for it in itens or []:
            cod = str(it.get("peca") or it.get("codigo") or "").strip()
            if cod:
                compat.setdefault(cod, set()).add(r["equipamento"])
    out = []
    for r in rows:
        car = r["caracteristicas"]
        if isinstance(car, str):
            try:
                car = _json.loads(car)
            except Exception:
                car = []
        aplic = [x.get("v") for x in (car or []) if str(x.get("r") or "").strip().upper().startswith("APLIC") and x.get("v")]
        eqs = sorted(compat.get(r["codigo"], set()))
        qr = None
        if _segno:
            try:
                qr = _segno.make(base + r["codigo"], error="m").svg_inline(scale=4, border=0, dark="#0F172A")
            except Exception:
                qr = None
        fam_txt = (r["familia_codigo"] or "") + (" — " + r["familia_nome"] if r.get("familia_nome") else "") \
                  + (" › " + r["subfamilia_nome"] if r.get("subfamilia_nome") else "")
        out.append({"codigo": r["codigo"], "descricao": r["descricao"] or "", "unidade": r["unidade"] or "UN",
                    "part_number": r["codigo_fabricante"] or r["codigo_externo"] or "",
                    "classe": r.get("classe") or "", "classe_nome": classes.get(r.get("classe") or "", ""),
                    "familia_codigo": r["familia_codigo"] or "", "familia": fam_txt,
                    "compativel": eqs, "aplicacao": aplic, "locais": r["locais"] or "", "qr_svg": qr,
                    "qtd": qtds.get(r["codigo"], 1), "existencia": int(float(r["existencia"] or 0))})
    return out


@router.get("/manutencao/etiquetas", response_class=HTMLResponse)
async def pagina_etiquetas():
    return HTMLResponse(r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Garra — Etiquetas de itens</title>
<style>
@page{size:Letter;margin:1.0cm 0.35cm 0.9cm 0.35cm}
*{box-sizing:border-box}body{margin:0;font-family:Arial,Helvetica,sans-serif;color:#0F172A;background:#E2E8F0}
.bar{position:sticky;top:0;background:#1A2A5E;color:#fff;padding:8px 14px;display:flex;gap:10px;align-items:center;font-size:13px}
.bar button{background:#F97316;color:#fff;border:0;border-radius:6px;padding:6px 14px;font-weight:700;cursor:pointer}
.folha{width:21.59cm;min-height:27.94cm;margin:10px auto;background:#fff;padding:1.0cm 0.35cm 0.9cm 0.35cm;display:grid;grid-template-columns:10.2cm 10.2cm;column-gap:0.5cm;grid-auto-rows:5.05cm;align-content:start}
.et{width:10.2cm;height:5.05cm;border:1px dashed #CBD5E1;padding:0.22cm 0.3cm 0.18cm;display:grid;grid-template-columns:1fr 2.6cm;column-gap:0.25cm;overflow:hidden;page-break-inside:avoid}
.et .esq{display:flex;flex-direction:column;min-width:0}
.et .fam{font-size:7.5pt;color:#64748B;letter-spacing:.3px;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.et .fam b{color:#1A2A5E}
.et .cod{font-size:20pt;font-weight:900;color:#1A2A5E;line-height:1.05;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.et .desc{font-size:10.5pt;font-weight:700;margin-top:2px;line-height:1.15;max-height:2.3em;overflow:hidden}
.et .pn{font-size:12.5pt;font-weight:900;color:#0F172A;margin-top:2px;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.et .pn small{font-size:7.5pt;font-weight:700;color:#64748B;letter-spacing:.3px;margin-right:4px}
.et .pn.vz{font-weight:400;color:#A0AEC0;font-size:8pt}
.et .lin{font-size:8pt;color:#334155;line-height:1.3;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.et .lin b{color:#0F172A}
.et .lin.vz{color:#A0AEC0}.et .lin.vz b{color:#A0AEC0;font-weight:400}
.et .rod{display:flex;justify-content:space-between;align-items:baseline;margin-top:auto;font-size:8.5pt;padding-top:3px;border-top:1px solid #E2E8F0}
.et .loc{font-weight:900;color:#F97316;font-size:11.5pt}
.et .loc.vz{color:#A0AEC0;font-weight:400;font-size:8.5pt}
.et .q{display:flex;flex-direction:column;align-items:center;justify-content:center}
.et .q svg,.et .q img,.et .q canvas{width:2.5cm;height:2.5cm}
.et .q small{font-size:6.5pt;color:#64748B;margin-top:1px}
body.semguia .et{border-color:transparent}
@media print{body{background:#fff}.bar{display:none}.folha{margin:0;padding:0;min-height:auto;page-break-after:always}.et{border-color:transparent}}
</style></head><body>
<div class="bar"><b>🏷 Etiquetas de itens</b> <span id="info"></span> <span style="flex:1"></span>
<label style="margin-right:12px">quantidade: <select id="modo" onchange="render()" style="font-size:12px;padding:3px 6px;border-radius:5px;border:0"><option value="url">como pedido (1 por item)</option><option value="exist">1 por unidade em estoque</option></select></label>
<label><input type="checkbox" id="guia" checked onchange="document.body.classList.toggle('semguia',!this.checked)"> guias (não imprimem)</label>
<button onclick="window.print()">🖨 Imprimir</button></div>
<div id="folhas"></div>
<script>
function tok(){return sessionStorage.getItem('garra_mnt_token')||localStorage.getItem('garra_adm_token')||'';}
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
(async()=>{
  const qs=location.search||'';
  const r=await fetch('/manutencao/api/etiquetas-itens'+qs,{headers:{Authorization:'Bearer '+tok()}});
  if(!r.ok){document.getElementById('info').textContent='Erro '+r.status+' — abra pela tela de Materiais (login)';return;}
  const base=await r.json();
  window._base=base;
  // (21/10/2026) quantidade: pela URL (CÓDIGO*n, padrão 1) ou 1 por unidade em estoque (lote pelo Estoque)
  if(base.some(it=>(it.qtd||1)>1)) document.getElementById('modo').options[0].text='como pedido na entrada';
  window.render=function(){
  const modo=document.getElementById('modo').value;
  const itens=[]; base.forEach(it=>{ const n=modo==='exist'?Math.max(1,Math.min(50,it.existencia||1)):(it.qtd||1); for(let k=0;k<n;k++) itens.push(it); });
  document.getElementById('info').textContent=itens.length+' etiqueta(s) ('+base.length+' item(ns)) · '+Math.ceil(itens.length/10)+' folha(s) · molde Letter 2×5 (10,2 × 5,05 cm)';
  let html='';
  for(let i=0;i<itens.length;i+=10){
    html+='<div class="folha">'+itens.slice(i,i+10).map((it,j)=>{
      const cmp=(it.compativel||[]).concat(it.aplicacao||[]).filter((v,k,a)=>a.indexOf(v)===k);
      const v = (x) => x ? '<b>'+esc(x)+'</b>' : '<b>—</b>';
      const cls = (x) => 'lin'+(x ? '' : ' vz');
      const pn = (it.part_number && it.part_number.replace(/\\s/g,'').toUpperCase()!==it.codigo.replace(/\\s/g,'').toUpperCase()) ? it.part_number : '';
      // PADRÃO FIXO: Classe · Família › Subfamília | Código | Descrição | Part number | Compatível | Local | UN — sempre presentes
      return '<div class="et"><div class="esq">'
        +'<div class="fam"><b>'+esc(it.familia_codigo||'—')+'</b>'+(it.familia&&it.familia.indexOf(' — ')>0?' · '+esc(it.familia.split(' — ')[1]):'')+'</div>'
        +'<div class="cod">'+esc(it.codigo)+'</div><div class="desc">'+esc(it.descricao||'—')+'</div>'
        +'<div class="pn'+(pn?'':' vz')+'"><small>P/N</small>'+(pn?esc(pn):'—')+'</div>'
        +'<div class="'+cls(cmp.length)+'">Compatível: '+v(cmp.join(' · '))+'</div>'
        +'<div class="rod"><span class="loc'+(it.locais?'':' vz')+'">'+(it.locais?esc(it.locais):'Local: —')+'</span><span>UN: <b>'+esc(it.unidade)+'</b></span></div></div>'
        +'<div class="q">'+(it.qr_svg||'<div style="font-size:7pt;color:#94A3B8">QR indisponível</div>')+'<small>'+esc(it.codigo)+'</small></div></div>';
    }).join('')+'</div>';
  }
  document.getElementById('folhas').innerHTML=html;
  };
  render();
})();
</script></body></html>""")


@router.get("/manutencao/notas", response_class=HTMLResponse)
async def pagina_notas_ot():
    """(20/10/2026) Balcão único da Luana: notinhas das OTs (manutenção) e dos abastecimentos
    na mesma lista, com filtro de ORIGEM (Todas · OTs · Abastecimento), competência, fornecedor,
    só pendentes. Valor da nota de OT é digitado ao conferir; abastecimento já vem calculado."""
    return HTMLResponse(r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Garra — Notas a lançar</title>
<style>
:root{--azul:#1A2A5E;--lar:#F97316}
*{box-sizing:border-box}body{font-family:system-ui,Segoe UI,Roboto,sans-serif;margin:0;background:#F1F5F9;color:#0F172A}
header{background:var(--azul);color:#fff;padding:10px 16px;display:flex;justify-content:space-between;align-items:center}
header b{font-size:15px}header .l{color:var(--lar);font-weight:900}
main{max-width:1300px;margin:14px auto;padding:0 12px}
.filtros{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;background:#fff;border:1px solid #E2E8F0;border-radius:10px;padding:10px}
.f{display:flex;flex-direction:column;font-size:11px;color:#475569;font-weight:700;gap:3px}
input,select,button{font:inherit;padding:6px 8px;border:1px solid #CBD5E1;border-radius:7px;background:#fff}
button{cursor:pointer}.p{background:var(--azul);color:#fff;border-color:var(--azul);font-weight:700}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}
.card{background:#fff;border:1px solid #E2E8F0;border-radius:10px;padding:10px 14px;min-width:200px}
.card b{color:var(--azul)}.card .v{font-size:17px;font-weight:900;color:var(--azul)}
.card .pend{color:#B91C1C;font-weight:800;font-size:11px}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #E2E8F0;border-radius:10px;overflow:hidden;font-size:12px}
th{background:#F8FAFC;text-align:left;padding:7px 8px;font-size:10px;text-transform:uppercase;color:#475569;border-bottom:2px solid #E2E8F0}
td{padding:6px 8px;border-bottom:1px solid #F1F5F9;vertical-align:middle}
tr.ok td{background:#F0FDF4}
.r{text-align:right;font-variant-numeric:tabular-nums}
.chip{font-size:10px;padding:1px 6px;border-radius:8px;background:#FEF3C7;color:#92400E;font-weight:700}
.org{font-size:10px;padding:1px 7px;border-radius:8px;font-weight:800;white-space:nowrap}
.org.ot{background:#DBEAFE;color:#1E40AF}.org.ab{background:#DCFCE7;color:#166534}
a{color:var(--azul);font-weight:700;text-decoration:none}
.muted{color:#64748B}
td input{width:95px;text-align:right;padding:4px 6px}
img.th{height:44px;border-radius:5px;border:1px solid #E2E8F0;vertical-align:middle;cursor:zoom-in}
#toast{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--azul);color:#fff;padding:10px 16px;border-radius:9px;display:none;font-weight:700;z-index:9}
</style></head><body>
<header>
 <span style="display:flex;align-items:center;gap:10px">
  <img src="/static/icons/logo.png" alt="Garra" style="height:34px;object-fit:contain;filter:brightness(0) invert(1)" onerror="this.style.display='none';document.getElementById('hd-fb').style.display='inline'">
  <b id="hd-fb" style="display:none"><span class="l">GARRA</span></b>
  <b>Notas a lançar — Manutenção e Abastecimento</b>
 </span>
 <span id="quem" style="font-size:12px"></span></header>
<main>
 <div class="filtros">
  <div class="f">Origem<select id="f-orig" onchange="carregar()"><option value="todas">Todas</option><option value="ot">Só OTs (manutenção)</option><option value="ab">Só abastecimentos</option></select></div>
  <div class="f">Competência<input type="month" id="f-mes"></div>
  <div class="f">Fornecedor / posto<select id="f-forn"><option value="">— todos —</option></select></div>
  <div class="f">&nbsp;<label style="font-weight:400"><input type="checkbox" id="f-pend" checked> só pendentes de lançar</label></div>
  <button class="p" onclick="carregar()">🔍 Buscar</button>
  <button onclick="excel()">📊 Excel da competência</button>
  <span class="muted" style="font-size:11px;flex:1;text-align:right">OT: confira a foto e <b>digite o valor</b> · Abastecimento: valor já calculado · marque ✔ ao lançar na MAIS</span>
 </div>
 <div class="cards" id="cards"></div>
 <div style="overflow:auto"><table><thead><tr>
  <th>Origem</th><th>Data</th><th>Referência</th><th>Equipamento</th><th>Fornecedor / posto</th><th>Foto</th><th>Descrição</th>
  <th>Nº nota / cupom</th><th class="r">Valor (R$)</th><th>Quem</th><th style="width:130px">Lançada na MAIS</th>
 </tr></thead><tbody id="corpo"><tr><td colspan="11" class="muted" style="padding:14px">Carregando…</td></tr></tbody></table></div>
</main>
<div id="toast"></div>
<script>
const $=i=>document.getElementById(i);
(function(){const u=new URL(location.href);const sso=u.searchParams.get('sso')||u.searchParams.get('token');
 if(sso&&sso.length>10){sessionStorage.setItem('garra_notas_token',sso);history.replaceState(null,'',u.pathname);}})();
function tok(){return sessionStorage.getItem('garra_notas_token')||sessionStorage.getItem('garra_mnt_token')||localStorage.getItem('garra_adm_token')||'';}
async function api(u,o){const r=await fetch(u,Object.assign({},o,{headers:Object.assign({'Content-Type':'application/json','Authorization':'Bearer '+tok()},(o&&o.headers)||{})}));
 if(r.status===401){document.body.innerHTML='<div style="padding:40px;text-align:center">🔒 Sessão expirada — abra o <a href="/admin">Admin</a> e clique em Notas de Manutenção</div>';throw new Error('401');}
 if(r.status===403){throw new Error('403');}
 const j=await r.json().catch(()=>({})); if(!r.ok)throw new Error(j.detail||('Erro '+r.status)); return j;}
function toast(m){const t=$('toast');t.textContent=m;t.style.display='block';clearTimeout(t._h);t._h=setTimeout(()=>t.style.display='none',3500);}
const esc=x=>String(x??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fN=(v,d)=>v==null?'—':Number(v).toLocaleString('pt-BR',{minimumFractionDigits:d??2,maximumFractionDigits:d??2});
let LINHAS=[];
async function carregar(){
 const mes=$('f-mes').value||new Date().toISOString().slice(0,7), orig=$('f-orig').value, pend=$('f-pend').checked;
 const forn=$('f-forn').value;   // "ot:<id>" ou "ab:<id>"
 LINHAS=[]; const erros=[];
 if(orig!=='ab'){ try{ const r=await api('/manutencao/api/notas-financeiro?mes='+mes+(pend?'&pendentes=1':'')+(forn.startsWith('ot:')?'&fornecedor='+forn.slice(3):''));
   (r.notas||[]).forEach(n=>LINHAS.push({orig:'ot',id:n.id,data:n.data,ref:n.ot_numero,eq:n.equipamento_codigo+' — '+(n.equipamento_desc||''),forn:n.fornecedor_nome||'',fornId:n.fornecedor_id?'ot:'+n.fornecedor_id:'',foto:n.foto_url,desc:n.descricao||'',num:n.numero_nota||'',valor:n.valor_nota,editavel:true,quem:n.usuario_nome||'',lanc:n.lancada_mais,lancPor:n.lancada_por_nome,lancEm:n.lancada_em,naOT:(n.compras_ot||0)+(n.outros_ot||0)}));
 }catch(e){ if(e.message==='401')return; erros.push('OTs: '+(e.message==='403'?'sem permissão':e.message)); } }
 if(orig!=='ot'){ try{ const r=await api('/operacional/api/abastecimentos/notas?mes='+mes+(pend?'&pendentes=1':'')+(forn.startsWith('ab:')?'&posto='+forn.slice(3):''));
   (r.notas||[]).forEach(n=>LINHAS.push({orig:'ab',id:n.id,data:n.data,ref:'Cupom '+(n.numero_cupom||'—'),eq:n.equipamentos||'',forn:n.posto||'',fornId:n.fornecedor_id?'ab:'+n.fornecedor_id:'',foto:n.foto_nota_url,desc:(n.combustivel||'')+' · '+fN(n.litros_total)+' L × '+fN(n.preco_litro,3),num:n.numero_cupom||'',valor:n.valor_total,editavel:false,quem:n.usuario_nome||'',lanc:n.lancada_mais,lancPor:n.lancada_por_nome,lancEm:n.lancada_em}));
 }catch(e){ if(e.message==='401')return; erros.push('Abastecimento: '+(e.message==='403'?'sem permissão':e.message)); } }
 LINHAS.sort((a,b)=>String(b.data||'').localeCompare(String(a.data||'')));
 if(!$('f-forn').value){ const m={}; LINHAS.forEach(l=>{ if(l.fornId) m[l.fornId]=(l.orig==='ab'?'⛽ ':'🔧 ')+l.forn; });
   $('f-forn').innerHTML='<option value="">— todos —</option>'+Object.entries(m).sort((a,b)=>a[1].localeCompare(b[1])).map(([id,n])=>'<option value="'+id+'">'+esc(n)+'</option>').join(''); }
 const tot={}; LINHAS.forEach(l=>{ const k=(l.orig==='ab'?'⛽ ':'🔧 ')+(l.forn||'— sem fornecedor'); const t=tot[k]=tot[k]||{k,n:0,v:0,p:0,s:0}; t.n++; t.v+=l.valor||0; if(!l.lanc)t.p++; if(l.editavel&&l.valor==null)t.s++; });
 $('cards').innerHTML=Object.values(tot).sort((a,b)=>a.k.localeCompare(b.k)).map(t=>'<div class="card"><b>'+esc(t.k)+'</b><div class="v">R$ '+fN(t.v)+'</div><div class="muted" style="font-size:11px">'+t.n+' nota(s)'+(t.p?' · <span class="pend">'+t.p+' pendente(s)</span>':'')+(t.s?' · '+t.s+' sem valor':'')+'</div></div>').join('')
   +(erros.length?'<div class="card" style="border-color:#FCA5A5;color:#B91C1C;font-size:11px">'+esc(erros.join(' · '))+'</div>':'');
 $('corpo').innerHTML=LINHAS.map(l=>'<tr class="'+(l.lanc?'ok':'')+'">'
  +'<td><span class="org '+l.orig+'">'+(l.orig==='ab'?'⛽ Abastec.':'🔧 OT')+'</span></td>'
  +'<td style="white-space:nowrap">'+(l.data||'').slice(0,16).replace('T',' ')+'</td>'
  +'<td><b>'+esc(l.ref)+'</b></td><td style="font-size:11px">'+esc(l.eq)+'</td>'
  +'<td>'+(l.forn?'<b>'+esc(l.forn)+'</b>':'<span class="chip">sem fornecedor</span>')+'</td>'
  +'<td>'+(l.foto?'<a href="'+esc(l.foto)+'" target="_blank" title="Abrir foto"><img class="th" src="'+esc(l.foto)+'"></a>':'<span class="chip">sem foto</span>')+'</td>'
  +'<td style="font-size:11px">'+esc(l.desc)+'</td>'
  +'<td>'+(l.editavel?'<input style="width:90px;text-align:left" value="'+esc(l.num)+'" placeholder="nº" onchange="salvar(\''+l.id+'\',{numero_nota:this.value})">':esc(l.num))+'</td>'
  +'<td class="r">'+(l.editavel?'<input inputmode="decimal" value="'+(l.valor!=null?fN(l.valor):'')+'" placeholder="0,00" onchange="salvar(\''+l.id+'\',{valor_nota:this.value})" title="Na OT já lançado: R$ '+fN(l.naOT)+'">':'<b>'+fN(l.valor)+'</b>')+'</td>'
  +'<td style="font-size:11px">'+esc(l.quem)+'</td>'
  +'<td><label style="cursor:pointer;font-size:11px;font-weight:700;color:'+(l.lanc?'#15803D':'#B91C1C')+'"><input type="checkbox" '+(l.lanc?'checked':'')+' data-id="'+l.id+'" data-orig="'+l.orig+'" onchange="marcar(this)"> '
    +(l.lanc?('✔ '+esc((l.lancPor||'').split(' ')[0])+' '+(l.lancEm?new Date(l.lancEm).toLocaleDateString('pt-BR'):'')):'lançar')+'</label></td></tr>').join('')
  ||'<tr><td colspan="11" class="muted" style="padding:14px">Nenhuma nota'+(pend?' pendente':'')+' na competência.</td></tr>';
}
async function salvar(id,body){
 try{await api('/manutencao/api/notas-financeiro/'+id,{method:'PATCH',body:JSON.stringify(body)});toast('✓ salvo');}
 catch(e){toast('❌ '+e.message);}
}
async function marcar(ck){
 const id=ck.dataset.id, orig=ck.dataset.orig; const tr=ck.closest('tr'); const v=tr.querySelector('td.r input');
 if(ck.checked && v && !v.value.trim()){ck.checked=false;toast('Digite o valor da nota antes de marcar como lançada');v.focus();return;}
 try{
  if(orig==='ab') await api('/operacional/api/abastecimentos/notas/'+id+'/lancada',{method:'PATCH',body:JSON.stringify({lancada:ck.checked})});
  else await api('/manutencao/api/notas-financeiro/'+id,{method:'PATCH',body:JSON.stringify({lancada:ck.checked})});
  toast(ck.checked?'✔ Marcada como lançada na MAIS':'Desmarcada');carregar();}
 catch(e){ck.checked=!ck.checked;toast('❌ '+e.message);}
}
function excel(){
 const mes=$('f-mes').value||new Date().toISOString().slice(0,7), orig=$('f-orig').value;
 const baixar=(u,nome)=>fetch(u,{headers:{'Authorization':'Bearer '+tok()}}).then(r=>{if(!r.ok)throw new Error('Erro '+r.status);return r.blob();})
  .then(b=>{const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=nome;a.click();}).catch(e=>toast('❌ '+e.message));
 if(orig!=='ab') baixar('/manutencao/api/notas-financeiro/excel?mes='+mes,'notas-manutencao-'+mes+'.xlsx');
 if(orig!=='ot') baixar('/operacional/api/abastecimentos/notas/excel?mes='+mes,'notas-abastecimento-'+mes+'.xlsx');
}
(function(){ $('f-mes').value=new Date().toISOString().slice(0,7);
 try{const p=JSON.parse(atob(tok().split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));$('quem').textContent=p.nome||p.login||'';}catch(e){}
 carregar(); })();
</script></body></html>""")
