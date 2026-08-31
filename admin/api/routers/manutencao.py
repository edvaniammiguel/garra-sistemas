# ══════════════════════════════════════════════════════════════
# MÓDULO MANUTENÇÃO — fundação v26 (06/07/2026)
# Referência: ManWinWin Express. Lifecycle da OT (Bruna):
#   aberta → em_andamento → aguardando_peca → concluida | cancelada
# Categoria 'apoio' (Combinado) é EXCLUÍDA de OT por decisão de projeto.
# ══════════════════════════════════════════════════════════════
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import HTMLResponse
import os

from core.auth import verificar_token, verificar_gestor
from core.db import ajard_query, ajard_query_id
from core.storage import storage_upload, storage_url, storage_delete
from core.helpers import comprimir_imagem
from core.permissions import perfil_modulos_padrao

router = APIRouter()

_PERFIS_MANUTENCAO = {"admin", "gestor", "luana", "bruna"}

async def _tem_modulo(payload, modulos: set) -> bool:
    """(29/08/2026) Resolução OFICIAL de permissão, igual ao Admin Master:
    exceção individual (permissoes_colaborador) SOBREPÕE o padrão do perfil
    (perfis_customizados via matriz Por Perfil). Qualquer módulo do conjunto
    libera; negativa individual explícita bloqueia aquele módulo."""
    u = await ajard_query(
        "SELECT id, perfil FROM public.usuarios_garra WHERE login=%s AND ativo=true",
        (payload.get("sub", ""),), fetch="one")
    if not u:
        return False
    rows = await ajard_query(
        "SELECT modulo, permitido FROM public.permissoes_colaborador WHERE usuario_id=%s",
        (u["id"],))
    individuais = {r["modulo"]: r["permitido"] for r in (rows or [])}
    padrao = set(await perfil_modulos_padrao(u["perfil"] or (payload.get("perfil") or "").lower()))
    for m in modulos:
        if m in individuais:
            if individuais[m]:
                return True
        elif m in padrao:
            return True
    return False


async def verificar_manutencao(payload=Depends(verificar_token)):
    """Gate do módulo Manutenção: perfil liberado OU módulo 'manutencao'
    (matriz Por Perfil do Admin Master + exceções individuais)."""
    if (payload.get("perfil") or "").lower() in _PERFIS_MANUTENCAO:
        return payload
    if await _tem_modulo(payload, {"manutencao"}):
        return payload
    raise HTTPException(status_code=403, detail="Sem permissão para o módulo Manutenção")

async def verificar_pedir_ot(payload=Depends(verificar_token)):
    """(28/08/2026) Gate do CANAL EXTERNO (mobile do mecânico): perfil da
    manutenção OU permissão 'pedir_ot' (ou 'manutencao') na
    permissoes_colaborador. Regra do cadastro: função MECÂNICO nasce com
    pedir_ot + checklist marcados (auto-default aplicado no Admin Master)."""
    if (payload.get("perfil") or "").lower() in _PERFIS_MANUTENCAO:
        return payload
    if await _tem_modulo(payload, {"pedir_ot", "manutencao"}):
        return payload
    raise HTTPException(status_code=403,
                        detail="Sem permissão para pedir OT — marque o módulo na matriz de Permissões (perfil Mecânica já vem com ele)")

_TRANSICOES = {
    "programada":      {"aberta", "em_andamento", "cancelada"},
    "aberta":          {"em_andamento", "cancelada"},
    "em_andamento":    {"aguardando_peca", "concluida", "cancelada"},
    "aguardando_peca": {"em_andamento", "concluida", "cancelada"},
    "concluida":       set(),
    "cancelada":       set(),
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
    _sql_completa = """
        CREATE OR REPLACE VIEW operacional.v_leituras AS
        SELECT equipamento_id, data,
               GREATEST(COALESCE(horimetro_final,0), COALESCE(km_final,0)) AS leitura,
               'parte'::text AS fonte
          FROM operacional.partes_diarias
         WHERE COALESCE(ativo, true) = true
        UNION ALL
        SELECT equipamento_id, data, leitura, 'abastecimento'::text AS fonte
          FROM operacional.abastecimentos
         WHERE ativo = true AND leitura IS NOT NULL
           AND COALESCE(divergencia_leitura, false) = false
           AND COALESCE(destino_tipo,'equipamento') = 'equipamento'
        UNION ALL
        SELECT e.id AS equipamento_id, c.enviado_em AS data,
               operacional.f_num_br(COALESCE(m.j->>'km', m.j->>'horimetro')) AS leitura,
               'checklist'::text AS fonte
          FROM checklist.envios c
          CROSS JOIN LATERAL (SELECT c.meta::jsonb AS j) m
          JOIN operacional.equipamentos e
            ON upper(trim(e.codigo)) = upper(trim(COALESCE(m.j->>'veiculo', m.j->>'identificacao', m.j->>'equipamento')))
         WHERE operacional.f_num_br(COALESCE(m.j->>'km', m.j->>'horimetro')) > 0
    """
    _sql_base = """
        CREATE OR REPLACE VIEW operacional.v_leituras AS
        SELECT equipamento_id, data,
               GREATEST(COALESCE(horimetro_final,0), COALESCE(km_final,0)) AS leitura,
               'parte'::text AS fonte
          FROM operacional.partes_diarias
         WHERE COALESCE(ativo, true) = true
        UNION ALL
        SELECT equipamento_id, data, leitura, 'abastecimento'::text AS fonte
          FROM operacional.abastecimentos
         WHERE ativo = true AND leitura IS NOT NULL
           AND COALESCE(divergencia_leitura, false) = false
           AND COALESCE(destino_tipo,'equipamento') = 'equipamento'
    """
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
    ):
        await ajard_query(ddl, fetch="none")
    _OT_COLS_OK = True


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
    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.ot
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    from datetime import date as _date
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"
    mapa_pri = {"normal": "media", "urgente": "urgente", "alta": "alta", "baixa": "baixa"}
    corpo = {
        "equipamento_id": str(eq_id),
        "descricao": d.get("descricao") or ped["descricao"],
        "prioridade": d.get("prioridade") or mapa_pri.get(ped["grau_urgencia"], "media"),
        "tipo_trabalho": d.get("tipo_trabalho"),
        "fornecedor_id": d.get("fornecedor_id"),
        "responsavel_id": d.get("responsavel_id"),
        "data_prevista": d.get("data_prevista"),
        "horimetro_previsto": d.get("horimetro_previsto"),
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

    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.ot
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    from datetime import date as _date
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"

    uid = await _usuario_id(payload)
    return await _inserir_ot(d, eq, uid, numero, ano, seq)


async def _inserir_ot(d, eq, uid, numero, ano, seq):
    """Miolo compartilhado: abertura manual e conversão de pedido."""
    eq_id = d.get("equipamento_id")
    descricao = (d.get("descricao") or "").strip()
    data_prev = (d.get("data_prevista") or "").strip() or None
    hor_prev = d.get("horimetro_previsto")
    try:
        hor_prev = float(str(hor_prev).replace(",", ".")) if hor_prev not in (None, "") else None
    except ValueError:
        hor_prev = None
    programada = bool(data_prev or hor_prev is not None)
    status_ini = "programada" if programada else "aberta"
    tt = (d.get("tipo_trabalho") or "").strip().upper() or None
    _classe = {"A": "preventiva", "B": "preventiva", "C": "corretiva",
               "M": "melhoria", "R": "reforma"}
    tipo_padrao = _classe.get((tt or " ")[0], "preventiva" if programada else "corretiva")
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
async def listar_ots(status: str = None, equipamento_id: str = None,
                     _auth=Depends(verificar_manutencao)):
    await _garantir_colunas_ot()
    where, params = ["ot.ativo=true"], []
    if status:
        params.append(status)
        where.append("ot.status=%s")
    if equipamento_id:
        params.append(equipamento_id)
        where.append("ot.equipamento_id=%s")
    rows = await ajard_query(
        f"""SELECT ot.*, eq.codigo AS equipamento_codigo, eq.descricao AS equipamento_desc,
                  eq.horimetro_atual AS equipamento_horimetro, eq.medicao AS equipamento_medicao,
                  us.nome AS solicitante_nome, ur.nome AS responsavel_nome,
                  fo.nome AS fornecedor_nome, pd.via AS pedido_via
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
        saida.append(d)
    return saida


@router.get("/manutencao/api/ots/proximo-numero")
async def proximo_numero_ot(_auth=Depends(verificar_manutencao)):
    """(24/08/2026) Prévia do próximo número para exibir no modal Nova OT
    (padrão ManWinWin). Prévia informativa — quem reserva de verdade é o
    POST, pela mesma sequência, à prova de corrida."""
    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.ot
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    from datetime import date as _date
    return {"numero": f"OT-{_date.today().year}-{int(seq['n']):04d}"}


@router.get("/manutencao/api/ots/{ot_id}")
async def detalhe_ot(ot_id: str, _auth=Depends(verificar_manutencao)):
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
    ultima = await ajard_query(
        """SELECT numero, data_conclusao, horimetro_na_abertura
           FROM manutencao.ot
           WHERE equipamento_id=%s AND status='concluida' AND ativo=true AND id<>%s
           ORDER BY data_conclusao DESC LIMIT 1""",
        (ot["equipamento_id"], ot_id), fetch="one")
    d = dict(ot)
    d["historico"] = [dict(h) for h in hist]
    d["ultima_ot"] = dict(ultima) if ultima else None
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
    uid = await _usuario_id(payload)
    extras, params = "", [novo]
    if novo == "concluida":
        extras = ", data_conclusao=now(), data_retorno_operacao=now()::date, observacao_conclusao=%s, custo_total=%s"
        params += [(d.get("observacao") or "").strip() or None, d.get("custo_total")]
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
        (ot_id, atual, novo, (d.get("observacao") or "").strip() or None, uid), fetch="none")
    return {"ok": True, "status": novo}


@router.post("/manutencao/api/ots/{ot_id}/reprogramar")
async def reprogramar_ot(ot_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """(24/08/2026) Remanejo de manutenção (padrão ManWinWin 'Programação OT'):
    nova data e/ou horímetro previsto, com MOTIVO obrigatório (domínio
    motivos-reprogramacao do Parametrizar) e trilha completa no histórico."""
    await _garantir_colunas_ot()
    d = await request.json()
    motivo = (d.get("motivo_codigo") or "").strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="Motivo da reprogramação é obrigatório")
    ot = await ajard_query(
        "SELECT id, status, data_prevista, horimetro_previsto FROM manutencao.ot WHERE id=%s AND ativo=true",
        (ot_id,), fetch="one")
    if not ot:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    if ot["status"] not in ("programada", "aberta", "em_andamento", "aguardando_peca"):
        raise HTTPException(status_code=400, detail="OT encerrada não se reprograma")
    data_n = (d.get("data_prevista") or "").strip() or None
    hor_n = d.get("horimetro_previsto")
    try:
        hor_n = float(str(hor_n).replace(",", ".")) if hor_n not in (None, "") else None
    except ValueError:
        hor_n = None
    if not data_n and hor_n is None:
        raise HTTPException(status_code=400, detail="Informe nova data e/ou novo horímetro")
    await ajard_query(
        "UPDATE manutencao.ot SET data_prevista=%s, horimetro_previsto=%s, atualizado_em=now() WHERE id=%s",
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
    for c in ("data_prevista", "horimetro_previsto"):
        if c in d and d[c] == "":
            d[c] = None
    for c in ("sintoma_codigo", "causa_codigo"):
        if c in d and d[c] == "":
            d[c] = None
    campos = ["tipo", "prioridade", "descricao", "responsavel_id",
              "fornecedor_id", "custo_total", "data_prevista", "horimetro_previsto",
              "sintoma_codigo", "causa_codigo", "tipo_trabalho"]
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
    rows = await ajard_query(
        """SELECT e.id, e.codigo, e.descricao, e.horimetro_atual, e.medicao,
                  e.categoria, e.equipamento_pai, e.posicao,
                  (SELECT COUNT(*)::int FROM operacional.equipamentos f
                    WHERE f.equipamento_pai = e.id AND f.ativo = true) AS n_filhos,
                  e.sistema_codigo, e.tipo_sigla,
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
        vivo = float(r["horimetro_atual"] or 0)
        eq = equipes.setdefault(str(r["id"]), {
            "id": str(r["id"]), "codigo": r["codigo"], "descricao": r["descricao"],
            "horimetro": vivo, "medicao": r["medicao"],
            "sistema_codigo": r.get("sistema_codigo"), "tipo_sigla": r.get("tipo_sigla"),
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
    for eq in equipes.values():
        if eq["proxima"] is not None:
            eq["proxima"] = eq["proxima"][1]
    lista = sorted(equipes.values(), key=lambda x: (ordem[x["status"]], x["codigo"]))
    return lista


# ── ALMOXARIFADO: peças reais (Onda 1) com busca ──
@router.get("/manutencao/api/pecas")
async def listar_pecas(busca: str = None, familia: str = None, limit: int = 100,
                       _auth=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    limit = min(max(int(limit or 100), 1), 300)
    where, params = ["ativo=true"], []
    if busca and busca.strip():
        b = f"%{busca.strip()}%"
        where.append("(codigo ILIKE %s OR descricao ILIKE %s OR codigo_externo ILIKE %s)"); params += [b, b, b]
    if familia and familia.strip():
        # subfamílias por pontuação (MC pega MC e MC.010…)
        where.append("(familia_codigo = %s OR familia_codigo LIKE %s)")
        params += [familia.strip(), familia.strip() + ".%"]
    rows = await ajard_query(
        f"""SELECT codigo, descricao, unidade, familia_codigo, custo_medio
            FROM manutencao.pecas WHERE {' AND '.join(where)}
            ORDER BY codigo LIMIT {limit}""", params)
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
    if "caracteristicas" in d:
        sets.append("caracteristicas=%s"); params.append(_json.dumps(d["caracteristicas"] or []))
    if "garantia" in d:
        sets.append("garantia=%s"); params.append(_json.dumps(d["garantia"] or {}))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(eq_id)
    r = await ajard_query(
        f"UPDATE operacional.equipamentos SET {', '.join(sets)} WHERE id=%s AND ativo=true RETURNING codigo",
        tuple(params), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Equipamento não encontrado")
    return {"ok": True, "codigo": r["codigo"]}


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
    """(24/08/2026) Ritmo médio de uso — motor da reprogramação (o 'Avançado'
    do ManWinWin): converte horímetro↔data. Média por DIA CORRIDO das partes
    diárias do Operacional nos últimos 60 dias (dia-calendário é o que
    interessa para prever data)."""
    r = await ajard_query(
        """SELECT COALESCE(SUM(p.horas_trabalhadas),0) AS total,
                  COUNT(DISTINCT p.data) AS dias_com_uso,
                  MIN(p.data) AS ini, MAX(p.data) AS fim
           FROM operacional.partes_diarias p
           WHERE p.equipamento_id = %s AND p.ativo = true
             AND p.data >= (now()::date - 60)""",
        (eq_id,), fetch="one")
    total = float(r["total"] or 0)
    dias_corridos = ((r["fim"] - r["ini"]).days + 1) if r["ini"] and r["fim"] else 0
    media = round(total / dias_corridos, 2) if dias_corridos > 0 and total > 0 else None
    return {"media_h_dia": media, "total_horas_60d": round(total, 1),
            "dias_com_uso": int(r["dias_com_uso"] or 0), "dias_corridos": dias_corridos}


@router.get("/manutencao/api/equipamentos/{eq_id}/detalhe")
async def detalhe_equipamento(eq_id: str, _auth=Depends(verificar_manutencao)):
    await _garantir_ficha_cols()
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
    await _garantir_biblioteca()
    planos = await ajard_query(
        """SELECT id, codigo, descricao, tipo_trabalho, periodo_codigo, periodo_qtd, tempo_horas, hh_previsto, custo_previsto, procedimento, plano_proximo_codigo,
                  mao_obra, pecas, outros, ferramentas
           FROM manutencao.planos WHERE equipamento_id=%s AND ativo=true ORDER BY codigo""", (eq_id,))
    ots = await ajard_query(
        """SELECT numero, tipo, prioridade, status, descricao, data_abertura, data_conclusao, custo_total
           FROM manutencao.ot WHERE equipamento_id=%s AND ativo=true
           ORDER BY data_abertura DESC LIMIT 25""", (eq_id,))
    filhos = await ajard_query(
        """SELECT codigo, descricao, posicao FROM operacional.equipamentos
           WHERE equipamento_pai=%s ORDER BY codigo""", (eq_id,))
    notas = await ajard_query(
        """SELECT n.id, n.data_nota, n.descricao, u.nome AS usuario_nome
           FROM manutencao.equipamento_notas n
           LEFT JOIN public.usuarios_garra u ON u.id = n.usuario_id
           WHERE n.equipamento_id=%s AND n.ativo=true
           ORDER BY n.data_nota DESC""", (eq_id,))
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
    d["notas"] = [dict(n) for n in notas]
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
          ADD COLUMN IF NOT EXISTS foto_path TEXT""", fetch="none")
    await ajard_query("""
        CREATE TABLE IF NOT EXISTS manutencao.equipamento_notas (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            equipamento_id UUID NOT NULL,
            data_nota TIMESTAMPTZ DEFAULT now(),
            usuario_id UUID,
            descricao TEXT NOT NULL,
            ativo BOOLEAN DEFAULT true,
            criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
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
          ADD COLUMN IF NOT EXISTS espec_compra TEXT""", fetch="none")
    _PECA_COLS_OK = True


@router.post("/manutencao/api/pecas")
async def criar_peca(request: Request, payload=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    d = await request.json()
    cod = (d.get("codigo") or "").strip()
    desc = (d.get("descricao") or "").strip()
    if not cod or not desc:
        raise HTTPException(status_code=400, detail="Informe código (do fabricante) e descrição")
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
              codigo_externo, classe, espec_compra, caracteristicas, observacoes)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (cod, desc, (d.get("unidade") or "UN").strip().upper(),
         (d.get("familia_codigo") or "").strip() or None, custo,
         (d.get("codigo_externo") or "").strip() or None,
         (d.get("classe") or "").strip() or None,
         (d.get("espec_compra") or "").strip() or None,
         _json.dumps(d.get("caracteristicas") or []),
         (d.get("observacoes") or "").strip() or None))
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
    for c in ["descricao", "unidade", "familia_codigo", "codigo_externo", "observacoes", "classe", "espec_compra"]:
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
                  e.minimo, e.localizacao
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
    if "minimo" in d:
        try:
            sets.append("minimo=%s")
            params.append(float(str(d["minimo"]).replace(",", ".")) if d["minimo"] not in (None, "") else None)
        except ValueError:
            sets.pop()
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params += [peca["id"], alm["id"]]
    await ajard_query(
        f"UPDATE manutencao.estoque SET {', '.join(sets)} WHERE peca_id=%s AND almoxarifado_id=%s",
        tuple(params), fetch="none")
    return {"ok": True}


@router.get("/manutencao/api/estoque-baixo")
async def estoque_baixo(_auth=Depends(verificar_manutencao)):
    """Peças com saldo abaixo do mínimo — semente do pedido de compra sugerido."""
    await _garantir_estoque_cols()
    rows = await ajard_query(
        """SELECT p.codigo, p.descricao, p.unidade, a.codigo AS almox,
                  e.quantidade, e.minimo, e.localizacao
           FROM manutencao.estoque e
           JOIN manutencao.pecas p ON p.id = e.peca_id AND p.ativo=true
           JOIN manutencao.almoxarifados a ON a.id = e.almoxarifado_id AND a.ativo=true
           WHERE e.minimo IS NOT NULL AND e.quantidade < e.minimo
           ORDER BY (e.minimo - e.quantidade) DESC""")
    return [dict(r) for r in rows]


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
        "SELECT id, custo_medio FROM manutencao.pecas WHERE codigo=%s OR id::text=%s",
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
        INSERT INTO manutencao.movimentacoes (tipo, peca_id, almox_origem, almox_destino, quantidade, ot_id, usuario_id, observacao, custo_unitario)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (tipo, pid, origem, destino, qtd, d.get("ot_id"), uid, (d.get("observacao") or "").strip() or None, custo_unit), fetch="none")
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
    "centros-custo": "manutencao.centros_custo",
    "motivos-reprogramacao": "manutencao.motivos_reprogramacao",
    "motivos-pendente": "manutencao.motivos_pendente",
    "periodos": "manutencao.periodos",
    "tipos-ferramenta": "manutencao.tipos_ferramenta",
    "rubricas": "manutencao.rubricas",
    "combustiveis": "manutencao.combustiveis",
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
          ('2.01','Saída armazém - mater.consumo'), ('2.02','Saída armazém - sobressalentes'), ('2.03','Saída armazém - lubrificantes'),
          ('2.04','Aplicação directa - mat.consumo'), ('2.05','Aplica.directa - sobressalente'), ('2.06','Aplica.directa - lubrificantes'),
          ('3','Serviços aplicados'), ('3.01','Aquisição serviços'), ('3.02','Contratos manutenção'),
          ('4','Estrutura depart. manutenção'),
          ('4.01','Salá. + encargos pess.directo'), ('4.02','Salá. + encarg. pess.indirecto'), ('4.03','Ferramentas'),
          ('4.04','Energia e fluídos p/ depto.'), ('4.05','Funcionamento (geral)'),
          ('5','Aquisição materiais p/ armazém'),
          ('5.01','Entradas armazém mat.consumo'), ('5.02','Entradas armaz.sobressalentes'), ('5.03','Entradas armz. lubrificantes'),
          ('6','Combustível, energia e fluídos'),
          ('6.01','Electricidade'), ('6.02','Gás'), ('6.03','Água'), ('6.04','Combustíveis'),
          ('6.04.01','Óleo Diesel'), ('6.04.02','Gasolina'), ('6.04.03','Etanol'),
          ('7','Custo padrão de referência'), ('7.01','Custos indisponibilidade'),
          ('8','Funcionário'), ('8.01','Funcionário ativo'), ('8.02','Funcionário inativo')
        ON CONFLICT DO NOTHING""", fetch="none")
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
    if "ativo" in d:
        sets.append("ativo=%s"); params.append(bool(d["ativo"]))
    if not sets:
        raise HTTPException(status_code=400, detail="Nada a alterar")
    params.append(pid)
    r = await ajard_query(
        f"UPDATE manutencao.planos SET {', '.join(sets)} WHERE id=%s RETURNING codigo",
        tuple(params), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
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
    return [dict(r) for r in rows]


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


async def _fmd_por_equipamento():
    """FMD-R por equipamento a partir das partes diárias (últimos 120 dias).
    Regra ManWinWin: só vale com >= 4 registros; senão None (sem projeção
    de data — o vencimento por leitura continua funcionando)."""
    await _garantir_leituras()
    rows = await ajard_query(
        """SELECT equipamento_id, data::date AS d, MAX(leitura) AS leitura
           FROM operacional.v_leituras
           WHERE data >= now() - interval '120 days'
           GROUP BY equipamento_id, data::date
           ORDER BY equipamento_id, d""")
    por_eq = {}
    for r in rows:
        if float(r["leitura"] or 0) > 0:
            por_eq.setdefault(str(r["equipamento_id"]), []).append((r["d"], float(r["leitura"])))
    fmd = {}
    for eq_id, regs in por_eq.items():
        if len(regs) < 4:
            continue
        (d0, l0), (d1, l1) = regs[0], regs[-1]
        dias = max(1, (d1 - d0).days)
        if l1 > l0:
            fmd[eq_id] = round((l1 - l0) / dias, 2)
    return fmd


@router.get("/manutencao/api/previsoes")
async def previsoes_frota(_auth=Depends(verificar_manutencao)):
    """Trabalhos previstos: um item por plano ativo, com próxima manutenção
    calculada, data projetada pelo FMD e status (vencida / a_vencer /
    programada / ok / sem_base / sem_periodo)."""
    from datetime import date as _date, timedelta as _td
    await _garantir_colunas_ot()
    await _garantir_dominios()
    for _cod, _nom in (("1", "Semanas"), ("2", "Meses"), ("6", "Registos (horímetro/km)")):
        await ajard_query(
            """INSERT INTO manutencao.periodos (codigo, nome, ativo)
               VALUES (%s, %s, true) ON CONFLICT (codigo) DO NOTHING""",
            (_cod, _nom), fetch="none")
    hoje = _date.today()
    planos = await ajard_query(
        """SELECT p.id, p.equipamento_id, p.codigo, p.descricao, p.tipo_trabalho,
                  p.periodo_codigo, p.periodo_qtd, p.custo_previsto, p.criado_em,
                  e.codigo AS eq_codigo, e.descricao AS eq_desc,
                  e.horimetro_atual, e.medicao,
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
    progs = await ajard_query(
        """SELECT DISTINCT ON (plano_id) plano_id, id, numero, data_prevista, horimetro_previsto
           FROM manutencao.ot
           WHERE status = 'programada' AND ativo = true AND plano_id IS NOT NULL
           ORDER BY plano_id, data_prevista NULLS LAST""")
    prog = {str(o["plano_id"]): o for o in progs}
    fmd = await _fmd_por_equipamento()

    itens = []
    for p in planos:
        pid = str(p["id"])
        modo, total, unidade = _resolver_plano(p["periodo_nome"] or p["periodo_codigo"], p["periodo_qtd"], p["descricao"])
        u = ult.get(pid)
        base_dt = (u["dt"] if u and u["dt"] else (p["criado_em"].date() if p["criado_em"] else hoje))
        base_leitura = (float(u["leitura"]) if u and u["leitura"] is not None else None)
        atual = float(p["horimetro_atual"] or 0)
        eq_fmd = fmd.get(str(p["equipamento_id"]))
        item = {"plano_id": pid, "plano_codigo": p["codigo"], "plano_descricao": p["descricao"],
                "equipamento_id": str(p["equipamento_id"]), "eq_codigo": p["eq_codigo"],
                "eq_descricao": p["eq_desc"], "tipo_trabalho": p["tipo_trabalho"],
                "periodo": p["periodo_nome"] or p["periodo_codigo"], "periodo_qtd": (float(p["periodo_qtd"]) if p["periodo_qtd"] is not None else None),
                "modo": modo, "unidade": unidade, "custo_previsto": (float(p["custo_previsto"]) if p["custo_previsto"] is not None else None),
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
                if eq_fmd and rest > 0:
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


@router.post("/manutencao/api/previsoes/{plano_id}/gerar-ot")
async def previsao_gerar_ot(plano_id: str, request: Request, payload=Depends(verificar_manutencao)):
    """Transforma previsão em OT PROGRAMADA herdando a FMP (plano_id,
    tipo de trabalho, descrição) com data/leitura previstas. Recusa se o
    plano já tem OT programada ativa (respeito ao agendamento existente)."""
    from datetime import date as _date
    await _garantir_colunas_ot()
    d = {}
    try:
        d = await request.json()
    except Exception:
        d = {}
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
    seq = await ajard_query(
        """SELECT COALESCE(MAX(sequencia),0)+1 AS n FROM manutencao.ot
           WHERE ano = EXTRACT(YEAR FROM now())::int""", fetch="one")
    ano = _date.today().year
    numero = f"OT-{ano}-{int(seq['n']):04d}"
    uid = await _usuario_id(payload)
    corpo = {"equipamento_id": str(p["equipamento_id"]),
             "descricao": f"{p['codigo']} — {p['descricao'] or ''}".strip(" —"),
             "tipo_trabalho": p["tipo_trabalho"],
             "plano_id": plano_id,
             "data_prevista": (d.get("data_prevista") or "").strip() or None,
             "horimetro_previsto": d.get("horimetro_previsto"),
             "prioridade": d.get("prioridade", "media")}
    return await _inserir_ot(corpo, eq, uid, numero, ano, seq)


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
        "UPDATE manutencao.ot SET data_prevista=%s, horimetro_previsto=%s WHERE id=%s",
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
    """Plano completo (Planeado da OT herdado da FMP)."""
    await _garantir_biblioteca()
    p = await ajard_query(
        """SELECT id, codigo, descricao, tipo_trabalho, periodo_codigo, periodo_qtd,
                  tempo_horas, custo_previsto, procedimento,
                  mao_obra, pecas, outros, ferramentas
           FROM manutencao.planos WHERE id=%s""", (pid,), fetch="one")
    if not p:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
    return dict(p)


@router.get("/manutencao/api/ots/{ot_id}/consumos")
async def ot_consumos(ot_id: str, _auth=Depends(verificar_manutencao)):
    """(28/08/2026) Fatia 1 do Planeado × Realizado — o REALIZADO da OT:
    movimentações de estoque vinculadas (saída consome; entrada = devolução,
    abate). Custo da linha = snapshot gravado no movimento (fallback:
    custo médio atual do acervo)."""
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")
    rows = await ajard_query(
        """SELECT m.id, m.tipo, m.quantidade, m.custo_unitario, m.criado_em, m.observacao,
                  p.codigo AS peca_codigo, p.descricao AS peca_descricao, p.unidade,
                  p.custo_medio,
                  ao.codigo AS almox_origem, ad.codigo AS almox_destino,
                  u.nome AS usuario
           FROM manutencao.movimentacoes m
           JOIN manutencao.pecas p ON p.id = m.peca_id
           LEFT JOIN manutencao.almoxarifados ao ON ao.id = m.almox_origem
           LEFT JOIN manutencao.almoxarifados ad ON ad.id = m.almox_destino
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
            "almox": r["almox_origem"] or r["almox_destino"],
            "usuario": r["usuario"], "observacao": r["observacao"]})
    return {"itens": itens, "total": round(total, 2)}


@router.get("/manutencao/api/armazem")
async def lente_armazem(almox: str = None, busca: str = None, familia: str = None,
                        incluir_zerados: int = 0,
                        _auth=Depends(verificar_manutencao)):
    await _garantir_peca_cols()
    """(28/08/2026) Lente ARMAZÉM do módulo Materiais (padrão ManWinWin):
    existência física por peça×local — saldo, endereço C-P-N, mínimo e
    custo médio. Sem filtro de local = todas as existências."""
    await _garantir_estoque_cols()
    cond, params = ["p.ativo = true", "a.ativo = true"], []
    if almox:
        cond.append("a.codigo = %s"); params.append(almox)
    if busca:
        cond.append("(p.codigo ILIKE %s OR p.descricao ILIKE %s OR p.codigo_externo ILIKE %s)")
        params += [f"%{busca}%", f"%{busca}%", f"%{busca}%"]
    if familia:
        cond.append("p.familia_codigo = %s"); params.append(familia)
    if incluir_zerados:
        # catálogo inteiro com saldo 0 quando sem registro — para endereçar
        # C-P-N e definir mínimos antes da primeira entrada
        cond_p = [c for c in cond if c.startswith("p.")]
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
            tuple(([almox or '—'] + ([almox] if almox else []))
                  + [p for p, c in zip(params, cond) if c.startswith("p.")]))
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
    return {"itens": [dict(r) for r in rows]}


@router.get("/manutencao/api/pecas-arvore")
async def pecas_arvore(_auth=Depends(verificar_manutencao)):
    """(28/08/2026) Estrutura do Catálogo de Peças: famílias com contagem
    do acervo — o front monta a árvore pela pontuação (MC → MC.010)."""
    rows = await ajard_query(
        """SELECT COALESCE(familia_codigo,'—') AS familia, COUNT(*)::int AS n
           FROM manutencao.pecas WHERE ativo=true
           GROUP BY 1 ORDER BY 1""")
    return [dict(r) for r in rows]


@router.get("/manutencao/api/movimentacoes")
async def listar_movimentacoes(almox: str = None, limit: int = 120,
                               _auth=Depends(verificar_manutencao)):
    """(28/08/2026) Extrato de movimentações do Almoxarifado — Entradas,
    Saídas, Transferências e Ajustes, com peça, locais, custo, OT e autor."""
    await ajard_query(
        "ALTER TABLE manutencao.movimentacoes ADD COLUMN IF NOT EXISTS custo_unitario NUMERIC",
        fetch="none")
    limit = min(max(int(limit or 120), 1), 300)
    cond, params = ["1=1"], []
    if almox:
        cond.append("(ao.codigo = %s OR ad.codigo = %s)")
        params += [almox, almox]
    rows = await ajard_query(
        f"""SELECT m.id, m.tipo, m.quantidade, m.custo_unitario, m.criado_em, m.observacao,
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


@router.post("/manutencao/api/entradas")
async def entrada_documento(request: Request, payload=Depends(verificar_manutencao)):
    """(28/08/2026) ENTRADA POR DOCUMENTO — multi-itens, três caminhos:
    · 'nf' / 'notinha': entrada normal → soma saldo no destino e recalcula
      custo médio ponderado da peça;
    · 'aplicacao_direta': material de oficina/compra que nem passa pela
      prateleira — cadastra (se preciso), NÃO mexe saldo e já aponta o
      consumo na OT (aparece no Realizado);
    · elo com Ordem de Compra por oc_numero (amarração forte na
      unificação Compras↔OT).
    Peça inexistente com descrição no item → cadastrada na hora."""
    d = await request.json()
    doc_tipo = (d.get("documento_tipo") or "").strip()
    if doc_tipo not in ("nf", "notinha", "aplicacao_direta"):
        raise HTTPException(status_code=400, detail="Tipo de documento inválido")
    doc_num = (d.get("documento_numero") or "").strip()
    if doc_tipo in ("nf", "notinha") and not doc_num:
        raise HTTPException(status_code=400, detail="Informe o número da NF/notinha")
    itens = d.get("itens") or []
    itens = [i for i in itens if (i.get("peca") or "").strip()]
    if not itens:
        raise HTTPException(status_code=400, detail="Documento sem itens")
    for _col, _ddl in (("documento_tipo", "TEXT"), ("documento_numero", "TEXT"),
                       ("fornecedor_id", "UUID"), ("oc_numero", "TEXT")):
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
    lancados, total_doc = [], 0.0
    for it in itens:
        cod = (it.get("peca") or "").strip()
        qtd = _num(it.get("quantidade")) or 0
        if qtd <= 0:
            raise HTTPException(status_code=400, detail=f"Quantidade inválida em {cod}")
        p = await ajard_query(
            "SELECT id, custo_medio FROM manutencao.pecas WHERE codigo=%s",
            (cod,), fetch="one")
        if not p:
            desc = (it.get("descricao") or "").strip()
            if not desc:
                raise HTTPException(status_code=404, detail=f"Peça {cod} não cadastrada — informe a descrição para cadastrar na hora")
            p = await ajard_query_id(
                """INSERT INTO manutencao.pecas (codigo, descricao, unidade, custo_medio)
                   VALUES (%s,%s,%s,%s)""",
                (cod, desc, (it.get("un") or "UN").strip().upper() or "UN", _num(it.get("custo_unitario"))))
            p = {"id": p["id"], "custo_medio": _num(it.get("custo_unitario"))}
        custo = _num(it.get("custo_unitario"))
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
        await ajard_query("""
            INSERT INTO manutencao.movimentacoes
                (tipo, peca_id, almox_destino, quantidade, ot_id, usuario_id, observacao,
                 custo_unitario, documento_tipo, documento_numero, fornecedor_id, oc_numero)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (tipo_mov, p["id"], destino, qtd,
             (ot_id if doc_tipo == "aplicacao_direta" else None), uid,
             (d.get("observacao") or "").strip() or None, custo,
             doc_tipo, doc_num or None, d.get("fornecedor_id") or None,
             (d.get("oc_numero") or "").strip() or None), fetch="none")
        total_doc += qtd * (custo or 0)
        lancados.append(cod)
    return {"ok": True, "documento": doc_num or doc_tipo, "itens": len(lancados),
            "total": round(total_doc, 2)}


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
        """SELECT data::date AS d, MAX(leitura) AS leitura,
                  string_agg(DISTINCT fonte, '+') AS fonte
           FROM operacional.v_leituras
           WHERE equipamento_id=%s AND data >= now() - interval '90 days'
           GROUP BY data::date ORDER BY d DESC LIMIT 40""", (eq_id,))
    regs = [{"data": r["d"].isoformat(), "leitura": float(r["leitura"] or 0), "fonte": r["fonte"]}
            for r in rows if float(r["leitura"] or 0) > 0]
    fmd = None
    if len(regs) >= 4:
        d1, l1 = regs[0]["data"], regs[0]["leitura"]
        d0, l0 = regs[-1]["data"], regs[-1]["leitura"]
        dias = max(1, (_date.fromisoformat(d1) - _date.fromisoformat(d0)).days)
        if l1 > l0:
            fmd = round((l1 - l0) / dias, 2)
    proj_30 = None
    vivo = eq["km_atual"] if eq.get("medicao") == "km" else eq["horimetro_atual"]
    atual = float(vivo or (regs[0]["leitura"] if regs else 0) or 0)
    if fmd:
        proj_30 = round(atual + fmd * 30, 1)
    return {"equipamento": eq["codigo"], "descricao": eq["descricao"],
            "medicao": eq["medicao"], "leitura_atual": atual,
            "registos": regs, "n_registos": len(regs),
            "fmd": fmd, "fmd_valido": fmd is not None,
            "projecao_30d": proj_30, "gerado_em": _date.today().isoformat()}


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
           ORDER BY p.data_pedido DESC LIMIT 60""", (uid,))
    return [dict(r) for r in rows]


@router.post("/manutencao/api/ots/{ot_id}/apontamento")
async def ot_apontamento(ot_id: str, request: Request, payload=Depends(verificar_pedir_ot)):
    """(28/08/2026) Mecânico alimenta o FECHAMENTO sem fechar: relato do
    serviço + horímetro + sinal de concluído vão para o HISTÓRICO da OT.
    Quem pode: o solicitante do pedido de origem ou o responsável da OT.
    O estado NÃO muda — o fechamento real é do admin/gestão."""
    d = await request.json()
    relato = (d.get("relato") or "").strip()
    if not relato:
        raise HTTPException(status_code=400, detail="Descreva o que foi feito")
    uid = await _usuario_id(payload)
    o = await ajard_query(
        """SELECT o.numero, o.status, o.responsavel_id, p.solicitante_id
           FROM manutencao.ot o
           LEFT JOIN manutencao.pedidos p ON p.id = o.pedido_id
           WHERE o.id=%s AND o.ativo=true""", (ot_id,), fetch="one")
    if not o:
        raise HTTPException(status_code=404, detail="OT não encontrada")
    perfil_gestor = (payload.get("perfil") or "").lower() in _PERFIS_MANUTENCAO
    if not perfil_gestor and str(uid) not in (str(o["responsavel_id"]), str(o["solicitante_id"])):
        raise HTTPException(status_code=403,
                            detail="Só o solicitante do pedido ou o responsável da OT apontam nela")
    if o["status"] not in ("em_andamento", "aguardando_peca", "aberta", "programada"):
        raise HTTPException(status_code=400, detail="OT já encerrada — apontamento indisponível")
    partes = [f"🔧 Execução (mecânico): {relato}"]
    if d.get("horimetro") not in (None, ""):
        partes.append(f"Horímetro/KM no serviço: {d.get('horimetro')}")
    if d.get("concluido"):
        partes.append("✅ SINALIZOU SERVIÇO CONCLUÍDO — pronto para encerramento pela gestão")
    await ajard_query(
        """INSERT INTO manutencao.ot_historico (ot_id, status_de, status_para, observacao, usuario_id)
           VALUES (%s,%s,%s,%s,%s)""",
        (ot_id, o["status"], o["status"], " · ".join(partes), uid), fetch="none")
    return {"ok": True, "numero": o["numero"]}


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
