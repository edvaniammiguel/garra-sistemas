"""routers.sistema — rotas de sistema: health, debug (protegidas por
DEBUG_KEY), Mural de Avisos e Manual do Colaborador (cartilha).

Refatoração Fase 2 · Etapa 5 (04/07/2026).
"""
import os, json, time
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends, Body, Header
from fastapi.responses import JSONResponse

import jwt as pyjwt
from core.config import DEBUG_KEY, _debug_autorizado, DATABASE_URL, JWT_SECRET
from core.db import get_db, ajard_query
from core.auth import verificar_token, verificar_admin, verificar_gestor
from core.models import MuralCreate, CartilhaBloco

router = APIRouter()

@router.get("/api/mural")
async def listar_mural(payload=Depends(verificar_token)):
    """Lista avisos ativos para o perfil/login do usuário."""
    perfil = payload.get("perfil", "")
    login = payload.get("login") or payload.get("sub", "")
    try:
        rows = await ajard_query(
            "SELECT id, titulo, mensagem, perfis, destinatario, criado_em, criado_por "
            "FROM public.mural_avisos WHERE ativo=true ORDER BY criado_em DESC",
            fetch="all"
        ) or []
    except Exception:
        # Coluna destinatario pode não existir ainda — migrar e tentar de novo
        try:
            await ajard_query("ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS destinatario TEXT DEFAULT ''", fetch="none")
        except Exception:
            pass
        rows = await ajard_query(
            "SELECT id, titulo, mensagem, perfis, destinatario, criado_em, criado_por "
            "FROM public.mural_avisos WHERE ativo=true ORDER BY criado_em DESC",
            fetch="all"
        ) or []
    result = []
    for r in rows:
        dest = (r.get("destinatario") or "").strip()
        perfis_str = (r.get("perfis") or "").strip()
        perfis_list = [p.strip() for p in perfis_str.split(",") if p.strip()]
        # Destinatário individual → só ele vê
        if dest:
            if dest != login:
                continue
        # Filtro por perfil → só os listados
        elif perfis_list:
            if perfil not in perfis_list:
                continue
        # Sem filtro → todos veem
        result.append({
            "id": r["id"],
            "titulo": r["titulo"],
            "mensagem": r["mensagem"],
            "perfis": perfis_str,
            "destinatario": dest,
            "criado_em": r["criado_em"].isoformat() if r["criado_em"] else "",
            "criado_por": r.get("criado_por") or "",
        })
    return result

@router.post("/api/mural")
async def criar_aviso_mural(dados: MuralCreate, payload=Depends(verificar_admin)):
    """Admin cria aviso no mural."""
    await ajard_query(
        "INSERT INTO public.mural_avisos (titulo, mensagem, perfis, destinatario, criado_por) VALUES (%s, %s, %s, %s, %s)",
        (dados.titulo, dados.mensagem, dados.perfis, dados.destinatario, payload.get("nome", "")), fetch="none"
    )
    return {"ok": True}

@router.put("/api/mural/{aviso_id}")
async def editar_aviso_mural(aviso_id: int, dados: MuralCreate, payload=Depends(verificar_admin)):
    """Admin edita um aviso existente."""
    await ajard_query(
        "UPDATE public.mural_avisos SET titulo=%s, mensagem=%s, perfis=%s, destinatario=%s WHERE id=%s",
        (dados.titulo, dados.mensagem, dados.perfis, dados.destinatario, aviso_id), fetch="none"
    )
    return {"ok": True}

@router.delete("/api/mural/{aviso_id}")
async def desativar_aviso_mural(aviso_id: int, payload=Depends(verificar_admin)):
    """Admin desativa aviso (soft delete)."""
    await ajard_query(
        "UPDATE public.mural_avisos SET ativo=false WHERE id=%s",
        (aviso_id,), fetch="none"
    )
    return {"ok": True}

@router.get("/api/cartilha")
async def listar_cartilha(payload=Depends(verificar_token)):
    """Lista blocos ativos do manual, em ordem — qualquer usuário logado pode ler."""
    rows = await ajard_query(
        "SELECT id, ordem, titulo, subtitulo, conteudo, atualizado_em "
        "FROM public.cartilha_blocos WHERE ativo=true ORDER BY ordem ASC, id ASC",
        fetch="all"
    ) or []
    return [
        {
            "id": r["id"], "ordem": r["ordem"], "titulo": r["titulo"],
            "subtitulo": r.get("subtitulo") or "", "conteudo": r["conteudo"],
        }
        for r in rows
    ]

@router.post("/api/cartilha")
async def criar_bloco_cartilha(dados: CartilhaBloco, payload=Depends(verificar_admin)):
    """Admin cria novo bloco no manual."""
    await ajard_query(
        "INSERT INTO public.cartilha_blocos (ordem, titulo, subtitulo, conteudo) VALUES (%s, %s, %s, %s)",
        (dados.ordem, dados.titulo, dados.subtitulo, dados.conteudo), fetch="none"
    )
    return {"ok": True}

@router.put("/api/cartilha/{bloco_id}")
async def editar_bloco_cartilha(bloco_id: int, dados: CartilhaBloco, payload=Depends(verificar_admin)):
    """Admin edita um bloco existente."""
    await ajard_query(
        "UPDATE public.cartilha_blocos SET ordem=%s, titulo=%s, subtitulo=%s, conteudo=%s, atualizado_em=NOW() WHERE id=%s",
        (dados.ordem, dados.titulo, dados.subtitulo, dados.conteudo, bloco_id), fetch="none"
    )
    return {"ok": True}

@router.delete("/api/cartilha/{bloco_id}")
async def excluir_bloco_cartilha(bloco_id: int, payload=Depends(verificar_admin)):
    """Admin remove um bloco definitivamente."""
    await ajard_query("DELETE FROM public.cartilha_blocos WHERE id=%s", (bloco_id,), fetch="none")
    return {"ok": True}

@router.get("/api/health")
async def health():
    """
    Health check LEVE — não toca no banco.
    Serve apenas para o Render saber que o processo está vivo.
    NÃO usar este endpoint em cron de keep-alive contra o banco:
    manter o Neon acordado 24/7 estoura a cota de compute do free tier.
    Para testar o banco use /api/health/db (manual).
    """
    return {"status": "ok", "sistema": "Garra Gestão API", "app": "vivo"}

@router.get("/api/health/db")
async def health_db():
    """Testa a conexão com o banco — uso manual de diagnóstico, NÃO em cron."""
    try:
        await ajard_query("SELECT 1", fetch="one")
        return {"status": "ok", "db": "conectado"}
    except Exception as e:
        return {"status": "erro", "db": str(e)}

@router.get("/api/debug/jardinagem-pares")
async def debug_jard_pares(mes: str = "", chave: str = ""):
    """Diagnóstico de pares: duplicados, vazios e sequência.
    Uso: ?chave=DEBUG_KEY (opcional &mes=ID_DO_MES)"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")
    filtro = "AND s.mes_id = %s" if mes else ""
    params = (mes,) if mes else ()
    pares = await ajard_query(
        f"""SELECT p.id, p.codigo_a, p.codigo_d, p.local_nome, p.semana_id,
                   s.label AS semana_label, s.mes_id,
                   (SELECT COUNT(*) FROM jardinagem.fotos f
                    WHERE f.par_id = p.id) AS num_fotos
            FROM jardinagem.pares p
            LEFT JOIN jardinagem.semanas s ON s.id = p.semana_id
            WHERE (p.ativo IS NULL OR p.ativo=true) {filtro}
            ORDER BY p.codigo_a, p.id""",
        params
    )
    pares = [dict(p) for p in (pares or [])]
    # Detectar duplicados de codigo_a
    vistos = {}
    duplicados = []
    vazios = []
    for p in pares:
        ca = p.get("codigo_a")
        if ca in vistos:
            duplicados.append({"codigo_a": ca, "ids": [vistos[ca], p["id"]]})
        else:
            vistos[ca] = p["id"]
        if not p.get("num_fotos"):
            vazios.append({"id": p["id"], "codigo_a": ca, "local": p.get("local_nome")})
    cfg = await ajard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    return {
        "total_pares": len(pares),
        "next_code_config": cfg["valor"] if cfg else None,
        "duplicados": duplicados,
        "vazios_sem_foto": vazios,
        "pares": pares
    }

@router.get("/api/debug/os")
async def debug_os(numero: str = "", chave: str = ""):
    """Diagnóstico de uma OS e suas partes. Uso: ?numero=OS-2026-0005&chave=DEBUG_KEY"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")
    os_row = await ajard_query(
        """SELECT id, numero, obra, regime_cobranca, valor_combinado, status,
                  equipamento_id, operador_id, tipo_servico_id, data_inicio
           FROM operacional.ordens_servico WHERE numero=%s""",
        (numero,), fetch="one"
    )
    if not os_row:
        return {"erro": "OS não encontrada", "numero": numero}
    partes = await ajard_query(
        """SELECT id, data, tipo_medicao,
                  horimetro_inicial, horimetro_final, horas_trabalhadas, horas_cobradas,
                  km_inicial, km_final, km_percorrido, qtd_viagens, qtd_metros,
                  hora_inicio, hora_fim, observacao, criado_em
           FROM operacional.partes_diarias
           WHERE os_id=%s AND ativo=true
           ORDER BY data, criado_em""",
        (os_row["id"],)
    )
    return {
        "os": dict(os_row),
        "total_partes": len(partes or []),
        "partes": [dict(p) for p in (partes or [])]
    }

@router.get("/api/debug/equipamentos")
async def debug_equipamentos(codigo: str = "", chave: str = ""):
    """Diagnóstico de equipamentos e responsável. Uso: ?chave=DEBUG_KEY (ou &codigo=CB-037)"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")
    filtro = "WHERE eq.codigo=%s" if codigo else ""
    params = (codigo,) if codigo else ()
    rows = await ajard_query(
        f"""SELECT eq.codigo, eq.descricao, eq.categoria, eq.medicao,
                   eq.operador_responsavel_id, resp.nome AS responsavel_nome,
                   eq.ativo
            FROM operacional.equipamentos eq
            LEFT JOIN public.usuarios_garra resp ON resp.id = eq.operador_responsavel_id
            {filtro}
            ORDER BY eq.codigo""",
        params
    )
    return {"total": len(rows or []), "equipamentos": [dict(r) for r in (rows or [])]}

@router.get("/api/debug/usuarios")
async def debug_usuarios(chave: str = "", authorization: Optional[str] = Header(None)):
    """Diagnóstico de usuários. Acesso: admin logado OU chave de diagnóstico."""
    # Permite acesso com chave de diagnóstico (para resolver problema de login)
    autorizado = _debug_autorizado(chave)
    if not autorizado:
        # Senão exige admin via token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Não autenticado")
        try:
            payload = pyjwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
            if payload.get("perfil") != "admin":
                raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
        except pyjwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Token inválido")
    rows = await ajard_query(
        """SELECT login, nome, email, perfil, perfil_checklist, ativo,
                  LEFT(senha_hash,7) AS hash_inicio,
                  CASE WHEN senha_hash = '$2b$12$y4jgMhNSKtoeBtad7lKEOev.tHk8S9OA1SpPHrowz5XT.AQJK.iZK'
                       THEN 'padrao_1234' ELSE 'outra' END AS senha_status
           FROM public.usuarios_garra
           ORDER BY ativo DESC, perfil, login""",
        fetch="all"
    )
    return [dict(r) for r in (rows or [])]

@router.get("/api/debug/sistema")
async def debug_sistema(chave: str = ""):
    """Diagnóstico completo do sistema — passo a passo de todas as áreas.
    Uso: /api/debug/sistema?chave=DEBUG_KEY"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")

    import datetime as _dt
    rel = {"timestamp": _dt.datetime.now().isoformat(), "checks": []}

    def add(area, item, ok, detalhe=""):
        rel["checks"].append({
            "area": area, "item": item,
            "status": "OK" if ok else "FALHA", "detalhe": str(detalhe)
        })

    # 1. BANCO — conexão
    try:
        r = await ajard_query("SELECT 1 AS ok", fetch="one")
        add("banco", "conexão Neon", bool(r), "conectado")
    except Exception as e:
        add("banco", "conexão Neon", False, e)

    # 2. TABELAS essenciais existem
    tabelas = [
        ("public", "usuarios_garra"),
        ("operacional", "ordens_servico"),
        ("operacional", "equipamentos"),
        ("operacional", "partes_diarias"),
        ("jardinagem", "meses"),
        ("jardinagem", "semanas"),
        ("jardinagem", "pares"),
        ("jardinagem", "fotos"),
    ]
    for sch, tab in tabelas:
        try:
            n = await ajard_query(
                f"SELECT COUNT(*) AS n FROM {sch}.{tab}", fetch="one"
            )
            add("tabelas", f"{sch}.{tab}", True, f"{n['n']} registros")
        except Exception as e:
            add("tabelas", f"{sch}.{tab}", False, e)

    # 3. USUÁRIOS — quantos ativos e perfis
    try:
        us = await ajard_query(
            "SELECT perfil, COUNT(*) AS n FROM public.usuarios_garra "
            "WHERE ativo=true GROUP BY perfil ORDER BY perfil",
            fetch="all"
        )
        perfis = {u["perfil"]: u["n"] for u in (us or [])}
        add("usuarios", "ativos por perfil", bool(perfis), perfis)
    except Exception as e:
        add("usuarios", "ativos por perfil", False, e)

    # 4. JARDINAGEM — integridade dos pares (duplicados/vazios)
    try:
        pares = await ajard_query(
            "SELECT codigo_a, codigo_d FROM jardinagem.pares "
            "WHERE (ativo IS NULL OR ativo=true) ORDER BY codigo_a",
            fetch="all"
        )
        pares = [dict(p) for p in (pares or [])]
        codigos = [p["codigo_a"] for p in pares if p["codigo_a"]]
        dups = [c for c in set(codigos) if codigos.count(c) > 1]
        vazios = [p for p in pares if not p.get("codigo_a")]
        # buracos na sequência
        nums = sorted(set(int(c) for c in codigos if str(c).isdigit()))
        buracos = []
        if nums:
            for x in range(nums[0], nums[-1] + 1):
                if x not in nums:
                    buracos.append(x)
        ok = (len(dups) == 0 and len(vazios) == 0 and len(buracos) == 0)
        add("jardinagem", "integridade pares", ok, {
            "total": len(pares),
            "duplicados": dups,
            "vazios": len(vazios),
            "buracos": buracos[:10],
            "faixa": f"{nums[0]}–{nums[-1]}" if nums else "—",
        })
    except Exception as e:
        add("jardinagem", "integridade pares", False, e)

    # 5. JARDINAGEM — next_code coerente
    try:
        cfg = await ajard_query(
            "SELECT valor FROM jardinagem.config WHERE chave='next_code'",
            fetch="one"
        )
        maxc = await ajard_query(
            "SELECT MAX(codigo_d) AS m FROM jardinagem.pares WHERE ativo",
            fetch="one"
        )
        nc = int(cfg["valor"]) if cfg else None
        mc = maxc["m"] if maxc else None
        ok = (nc is not None and mc is not None and nc > mc)
        add("jardinagem", "next_code", ok,
            f"next_code={nc}, max_codigo_d={mc}")
    except Exception as e:
        add("jardinagem", "next_code", False, e)

    # 6. JARDINAGEM — trava UNIQUE anti-duplicação ativa
    try:
        idx = await ajard_query(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='jardinagem' AND tablename='pares' "
            "AND indexname='uq_pares_codigo_a_ativo'",
            fetch="one"
        )
        add("jardinagem", "trava UNIQUE", bool(idx),
            "ativa" if idx else "AUSENTE — risco de duplicação")
    except Exception as e:
        add("jardinagem", "trava UNIQUE", False, e)

    # 7. OPERACIONAL — colunas novas existem
    try:
        cols = await ajard_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='operacional' AND table_name='partes_diarias' "
            "AND column_name IN ('sem_almoco','fornecedor','equipamento_terceiro')",
            fetch="all"
        )
        nomes = {c["column_name"] for c in (cols or [])}
        faltam = {"sem_almoco", "fornecedor", "equipamento_terceiro"} - nomes
        add("operacional", "colunas partes_diarias", not faltam,
            "todas presentes" if not faltam else f"faltam: {faltam}")
    except Exception as e:
        add("operacional", "colunas partes_diarias", False, e)

    # 8. OPERACIONAL — operador_responsavel em equipamentos
    try:
        col = await ajard_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='operacional' AND table_name='equipamentos' "
            "AND column_name='operador_responsavel_id'",
            fetch="one"
        )
        add("operacional", "operador_responsavel_id", bool(col),
            "presente" if col else "AUSENTE")
    except Exception as e:
        add("operacional", "operador_responsavel_id", False, e)

    # Resumo
    falhas = [c for c in rel["checks"] if c["status"] == "FALHA"]
    rel["resumo"] = {
        "total_checks": len(rel["checks"]),
        "ok": len(rel["checks"]) - len(falhas),
        "falhas": len(falhas),
        "areas_com_falha": sorted(set(c["area"] for c in falhas)),
    }
    return rel
