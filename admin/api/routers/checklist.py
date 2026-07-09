"""routers.checklist — Checklist Digital (9), Frota (3) e Logística (9):
os três grupos do domínio checklist, 21 rotas.

Refatoração Fase 2 · Etapa 3 (04/07/2026). Corpos IDÊNTICOS aos do main.py.
Este grupo já é 100% asyncpg (get_db) — zero jard_query.
"""
import os, io, json, time, uuid, secrets
from datetime import datetime, timedelta, date
from typing import Optional
from fastapi import APIRouter, Request, HTTPException, Depends, Header, Body
from fastapi.responses import FileResponse, JSONResponse

from core.config import CHECKLIST_DIR
from core.db import get_db
from core.auth import verificar_token, verificar_gestor, verificar_admin
from core.storage import _checklist_extrair_fotos_para_storage, _checklist_assinar_fotos_para_leitura
from core.models import (
    EnvioCreate, FrotaItem, ChecklistModeloCreate,
    LogMotoristaCreate, LogVeiculoCreate, LogRegistroCreate,
)

router = APIRouter()

@router.get("/checklist/modelos")
async def listar_modelos(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.modelos WHERE ativo=TRUE ORDER BY label")
    result = []
    for r in rows:
        d = dict(r)
        d["questions"] = d["questions"] if isinstance(d["questions"],list) else json.loads(d["questions"] or "[]")
        d["steps"]     = d["steps"]     if isinstance(d["steps"],list)     else json.loads(d["steps"]     or "[]")
        result.append(d)
    return result

@router.post("/checklist/modelos")
async def salvar_modelo(cl: ChecklistModeloCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.modelos WHERE cl_id=$1", cl.cl_id)
    if existe:
        await db.execute(
            "UPDATE checklist.modelos SET label=$1,icon=$2,descricao=$3,vehicle_cat=$4,is_default=$5,score_full=$6,score_nc=$7,score_obs=$8,score_ontime=$9,questions=$10,steps=$11 WHERE cl_id=$12",
            cl.label,cl.icon,cl.descricao,cl.vehicle_cat,cl.is_default,cl.score_full,cl.score_nc,cl.score_obs,cl.score_ontime,json.dumps(cl.questions),json.dumps(cl.steps),cl.cl_id
        )
    else:
        await db.execute(
            "INSERT INTO checklist.modelos (cl_id,label,icon,descricao,vehicle_cat,is_default,score_full,score_nc,score_obs,score_ontime,questions,steps) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)",
            cl.cl_id,cl.label,cl.icon,cl.descricao,cl.vehicle_cat,cl.is_default,cl.score_full,cl.score_nc,cl.score_obs,cl.score_ontime,json.dumps(cl.questions),json.dumps(cl.steps)
        )
    return {"ok": True}

@router.delete("/checklist/modelos/{cl_id}")
async def remover_modelo(cl_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("UPDATE checklist.modelos SET ativo=FALSE WHERE cl_id=$1", cl_id)
    return {"ok": True}

@router.get("/checklist/envios")
async def listar_envios(usuario: Optional[str]=None, cl_id: Optional[str]=None, limit: int=100, db=Depends(get_db), _auth=Depends(verificar_token)):
    where, params = "WHERE arquivado=FALSE", []
    # Menor privilégio (05/07/2026): operador/motorista só vê os PRÓPRIOS envios,
    # independente do parâmetro. Gestor/admin filtram livremente.
    if not _eh_gestor_ck(_auth):
        usuario = _auth.get("sub", "")
    if usuario: params.append(usuario); where += f" AND usuario_login=${len(params)}"
    if cl_id:   params.append(cl_id);   where += f" AND cl_id=${len(params)}"
    params.append(limit)
    rows = await db.fetch(f"SELECT * FROM checklist.envios {where} ORDER BY enviado_em DESC LIMIT ${len(params)}", *params)
    result = []
    for r in rows:
        d = dict(r)
        d["meta"]      = d["meta"]      if isinstance(d["meta"],dict)      else json.loads(d["meta"]      or "{}")
        d["respostas"] = d["respostas"] if isinstance(d["respostas"],dict) else json.loads(d["respostas"] or "{}")
        d["respostas"] = _checklist_assinar_fotos_para_leitura(d["respostas"])
        result.append(d)
    return result

@router.post("/checklist/envios")
async def salvar_envio(e: EnvioCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.envios WHERE envio_id=$1", e.envio_id)
    if existe: return {"ok": True, "duplicado": True}
    data = datetime.fromisoformat(e.enviado_em) if e.enviado_em else datetime.now()
    respostas_processadas = _checklist_extrair_fotos_para_storage(e.envio_id, dict(e.respostas))
    await db.execute(
        "INSERT INTO checklist.envios (envio_id,usuario_login,usuario_nome,cl_id,cl_label,meta,respostas,pts,tem_nc,total_nc,enviado_em) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
        e.envio_id,e.usuario_login,e.usuario_nome,e.cl_id,e.cl_label,json.dumps(e.meta),json.dumps(respostas_processadas),e.pts,e.tem_nc,e.total_nc,data
    )
    await db.execute(
        "UPDATE public.usuarios_garra SET pts=pts+$1, total_envios=total_envios+1, atualizado_em=NOW() WHERE login=$2",
        e.pts, e.usuario_login
    )
    return {"ok": True}

@router.patch("/checklist/envios/{envio_id}/arquivar")
async def arquivar_envio(envio_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("UPDATE checklist.envios SET arquivado=TRUE WHERE envio_id=$1", envio_id)
    return {"ok": True}

@router.get("/frota")
async def listar_frota(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch(
        "SELECT DISTINCT ON (categoria, identificacao) * "
        "FROM checklist.frota WHERE ativo=TRUE "
        "ORDER BY categoria, identificacao, id"
    )
    return [dict(r) for r in rows]

@router.post("/frota")
async def salvar_frota(item: FrotaItem, db=Depends(get_db), _auth=Depends(verificar_token)):
    existe = await db.fetchval("SELECT id FROM checklist.frota WHERE categoria=$1 AND identificacao=$2", item.categoria, item.identificacao)
    if existe:
        await db.execute("UPDATE checklist.frota SET descricao=$1,ativo=TRUE WHERE categoria=$2 AND identificacao=$3", item.descricao,item.categoria,item.identificacao)
    else:
        await db.execute("INSERT INTO checklist.frota (categoria,identificacao,descricao) VALUES ($1,$2,$3)", item.categoria,item.identificacao,item.descricao)
    return {"ok": True}

@router.delete("/frota/{categoria}/{identificacao}")
async def remover_frota(categoria: str, identificacao: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await db.execute("UPDATE checklist.frota SET ativo=FALSE WHERE categoria=$1 AND identificacao=$2", categoria, identificacao)
    return {"ok": True}

@router.get("/logistica/motoristas")
async def listar_motoristas(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.log_motoristas ORDER BY nome")
    return [dict(r) for r in rows]

# ─── (09/07/2026) Menor privilégio: escrita na logística exige a permissão ───
async def _exigir_logistica(db, payload):
    perfil = (payload.get("perfil") or "").lower()
    if perfil in ("admin", "gestor"):
        return
    ident = payload.get("sub") or payload.get("login") or ""
    ok = await db.fetchval(
        """SELECT p.permitido FROM public.permissoes_colaborador p
           JOIN public.usuarios_garra u ON u.id = p.usuario_id
           WHERE (u.id::text = $1 OR u.login = $1) AND p.modulo = 'checklist_logistica'""",
        str(ident))
    if not ok:
        raise HTTPException(status_code=403, detail="Sem permissão de Logística")

@router.post("/logistica/motoristas")
async def salvar_motorista(m: LogMotoristaCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    await _exigir_logistica(db, _auth)
    existe = await db.fetchval("SELECT id FROM checklist.log_motoristas WHERE motor_id=$1", m.motor_id)
    if existe:
        await db.execute("UPDATE checklist.log_motoristas SET nome=$1,cpf=$2,cnh=$3,telefone=$4,status=$5,observacoes=$6,atualizado_em=NOW() WHERE motor_id=$7", m.nome,m.cpf,m.cnh,m.telefone,m.status,m.observacoes,m.motor_id)
    else:
        await db.execute("INSERT INTO checklist.log_motoristas (motor_id,nome,cpf,cnh,telefone,status,observacoes) VALUES ($1,$2,$3,$4,$5,$6,$7)", m.motor_id,m.nome,m.cpf,m.cnh,m.telefone,m.status,m.observacoes)
    return {"ok": True}

@router.delete("/logistica/motoristas/{motor_id}")
async def remover_motorista(motor_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await _exigir_logistica(db, _auth)
    await db.execute("DELETE FROM checklist.log_motoristas WHERE motor_id=$1", motor_id)
    return {"ok": True}

@router.get("/logistica/veiculos")
async def listar_veiculos(db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.log_veiculos ORDER BY car_id")
    result = []
    for r in rows:
        d = dict(r); d["extras"] = d["extras"] if isinstance(d["extras"],list) else json.loads(d["extras"] or "[]")
        result.append(d)
    return result

@router.post("/logistica/veiculos")
async def salvar_veiculo(v: LogVeiculoCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    await _exigir_logistica(db, _auth)
    existe = await db.fetchval("SELECT id FROM checklist.log_veiculos WHERE veiculo_id=$1", v.veiculo_id)
    if existe:
        # (09/07/2026) Trava otimista: se o cliente informou a versão que viu e
        # o servidor está mais novo, alguém editou antes → 409 (recarregue).
        if v.visto_em:
            atual = await db.fetchval("SELECT atualizado_em FROM checklist.log_veiculos WHERE veiculo_id=$1", v.veiculo_id)
            if atual and not str(atual).startswith(str(v.visto_em)[:19].replace("T", " ")):
                raise HTTPException(status_code=409, detail="Este veículo foi alterado por outra pessoa — recarregue a tela")
        await db.execute("UPDATE checklist.log_veiculos SET car_id=$1,placa=$2,modelo=$3,ano=$4,cor=$5,status=$6,extras=$7,observacoes=$8,atualizado_em=NOW() WHERE veiculo_id=$9", v.car_id,v.placa,v.modelo,v.ano,v.cor,v.status,json.dumps(v.extras),v.observacoes,v.veiculo_id)
    else:
        await db.execute("INSERT INTO checklist.log_veiculos (veiculo_id,car_id,placa,modelo,ano,cor,status,extras,observacoes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)", v.veiculo_id,v.car_id,v.placa,v.modelo,v.ano,v.cor,v.status,json.dumps(v.extras),v.observacoes)
    return {"ok": True}

@router.delete("/logistica/veiculos/{veiculo_id}")
async def remover_veiculo(veiculo_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await _exigir_logistica(db, _auth)
    await db.execute("DELETE FROM checklist.log_veiculos WHERE veiculo_id=$1", veiculo_id)
    return {"ok": True}

@router.get("/logistica/registros")
async def listar_registros(limit: int=50, db=Depends(get_db), _auth=Depends(verificar_token)):
    rows = await db.fetch("SELECT * FROM checklist.log_registros ORDER BY data_hora DESC LIMIT $1", limit)
    result = []
    for r in rows:
        d = dict(r); d["carros"] = d["carros"] if isinstance(d["carros"],list) else json.loads(d["carros"] or "[]")
        result.append(d)
    return result

@router.post("/logistica/registros")
async def salvar_registro(r: LogRegistroCreate, db=Depends(get_db), _auth=Depends(verificar_token)):
    await _exigir_logistica(db, _auth)
    existe = await db.fetchval("SELECT id FROM checklist.log_registros WHERE registro_id=$1", r.registro_id)
    if existe:
        await db.execute("UPDATE checklist.log_registros SET responsavel=$1,data_hora=$2,carros=$3 WHERE registro_id=$4", r.responsavel,datetime.fromisoformat(r.data_hora),json.dumps(r.carros),r.registro_id)
    else:
        await db.execute("INSERT INTO checklist.log_registros (registro_id,responsavel,data_hora,carros) VALUES ($1,$2,$3,$4)", r.registro_id,r.responsavel,datetime.fromisoformat(r.data_hora),json.dumps(r.carros))
    return {"ok": True}

@router.delete("/logistica/registros/{registro_id}")
async def remover_registro(registro_id: str, db=Depends(get_db), _auth=Depends(verificar_token)):
    await _exigir_logistica(db, _auth)
    await db.execute("DELETE FROM checklist.log_registros WHERE registro_id=$1", registro_id)
    return {"ok": True}

@router.get("/checklist")
async def checklist_app():
    path = os.path.join(CHECKLIST_DIR, "index.html")
    return FileResponse(path)

@router.get("/checklist/sw.js")
async def checklist_sw():
    path = os.path.join(CHECKLIST_DIR, "sw.js")
    return FileResponse(path, media_type="application/javascript")

@router.get("/checklist/manifest.json")
async def checklist_manifest():
    path = os.path.join(CHECKLIST_DIR, "manifest.json")
    return FileResponse(path)


# ══════════════════════════════════════════════════════════════
# RANKING SERVIDOR (05/07/2026) — fim da fragmentação local
# O ranking era montado do localStorage de cada aparelho: o gestor
# não via os envios dos operadores (que estão no SERVIDOR). Agora
# uma fonte única: agrega checklist.envios (com período opcional).
# ══════════════════════════════════════════════════════════════

def _eh_gestor_ck(payload) -> bool:
    """Gestão do checklist: perfil global admin/gestor OU papel manager do módulo."""
    return payload.get("perfil") in ("admin", "gestor") or payload.get("perfil_checklist") == "manager"


@router.get("/checklist/ranking")
async def checklist_ranking(inicio: str = None, fim: str = None,
                            _auth=Depends(verificar_token), db=Depends(get_db)):
    """Ranking de pontos agregado no servidor. inicio/fim (YYYY-MM-DD)
    delimitam a campanha; sem período = geral (todos os envios)."""
    where, args = ["1=1"], []
    from datetime import date as _date
    try:
        if inicio:
            args.append(_date.fromisoformat(inicio))
            where.append(f"e.enviado_em::date >= ${len(args)}")
        if fim:
            args.append(_date.fromisoformat(fim))
            where.append(f"e.enviado_em::date <= ${len(args)}")
    except ValueError:
        raise HTTPException(status_code=400, detail="Período inválido (use YYYY-MM-DD)")
    cond = ' AND '.join(where)
    cond_aj = cond.replace('e.enviado_em', 'a.criado_em')
    rows = await db.fetch(
        f"""WITH env AS (
              SELECT e.usuario_login AS login,
                     MAX(e.usuario_nome) AS nome,
                     COUNT(*)::int AS envios,
                     COALESCE(SUM(e.pts), 0)::int AS pts
              FROM checklist.envios e
              WHERE {cond}
              GROUP BY e.usuario_login
           ), aj AS (
              SELECT a.usuario_login AS login,
                     COALESCE(SUM(a.pts), 0)::int AS pts
              FROM checklist.ajustes_pontos a
              WHERE {cond_aj}
              GROUP BY a.usuario_login
           )
           SELECT COALESCE(env.login, aj.login) AS login,
                  COALESCE(MAX(u.nome), MAX(env.nome), COALESCE(env.login, aj.login)) AS nome,
                  COALESCE(MAX(env.envios), 0)::int AS envios,
                  (COALESCE(MAX(env.pts), 0) + COALESCE(MAX(aj.pts), 0))::int AS pts
           FROM env
           FULL OUTER JOIN aj ON aj.login = env.login
           LEFT JOIN public.usuarios_garra u ON u.login = COALESCE(env.login, aj.login)
           GROUP BY COALESCE(env.login, aj.login)
           ORDER BY pts DESC, envios DESC""",
        *args
    )
    result = [dict(r) for r in rows]
    # Menor privilégio (06/07/2026): ranking COMPARATIVO é ferramenta de gestão.
    # Colaborador recebe apenas a PRÓPRIA linha (pontos/envios dele).
    if not _eh_gestor_ck(_auth):
        eu = _auth.get("sub", "")
        result = [r for r in result if r["login"] == eu]
    return result


@router.post("/checklist/pontos-ajuste")
async def checklist_ajustar_pontos(request: Request, _auth=Depends(verificar_token), db=Depends(get_db)):
    """Gestor aplica penalidade (pts negativo) ou bônus a um colaborador."""
    if not _eh_gestor_ck(_auth):
        raise HTTPException(status_code=403, detail="Apenas gestores podem ajustar pontos.")
    d = await request.json()
    login = (d.get("login") or "").strip()
    motivo = (d.get("motivo") or "").strip()
    try:
        pts = int(d.get("pts"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Pontos inválidos.")
    if not login or not motivo or pts == 0:
        raise HTTPException(status_code=400, detail="Informe login, pontos (≠0) e motivo.")
    await db.execute(
        """INSERT INTO checklist.ajustes_pontos (usuario_login, pts, motivo, criado_por)
           VALUES ($1, $2, $3, $4)""",
        login, pts, motivo, _auth.get("sub", "")
    )
    return {"ok": True, "login": login, "pts": pts}


# ══════════════════════════════════════════════════════════════
# CONFIG DE PONTOS NO SERVIDOR (05/07/2026) — os campos de data
# não funcionavam (re-render matava o input) E a config era local
# por aparelho. Agora: fonte única, todos leem a mesma regra.
# ══════════════════════════════════════════════════════════════

@router.get("/checklist/pontos-config")
async def checklist_get_pontos_config(_auth=Depends(verificar_token), db=Depends(get_db)):
    row = await db.fetchrow("SELECT valor FROM checklist.config WHERE chave='pontos'")
    if not row:
        return {"ativo": False, "data_inicio": None, "data_fim": None}
    import json as _json
    v = row["valor"]
    return _json.loads(v) if isinstance(v, str) else v


@router.put("/checklist/pontos-config")
async def checklist_put_pontos_config(request: Request, _auth=Depends(verificar_token), db=Depends(get_db)):
    if not _eh_gestor_ck(_auth):
        raise HTTPException(status_code=403, detail="Apenas gestores.")
    d = await request.json()
    import json as _json
    cfg = {"ativo": bool(d.get("ativo")),
           "data_inicio": d.get("data_inicio") or None,
           "data_fim": d.get("data_fim") or None}
    await db.execute(
        """INSERT INTO checklist.config (chave, valor, atualizado_em)
           VALUES ('pontos', $1::jsonb, now())
           ON CONFLICT (chave) DO UPDATE SET valor=$1::jsonb, atualizado_em=now()""",
        _json.dumps(cfg)
    )
    return {"ok": True, **cfg}


@router.get("/checklist/pontos-ajustes")
async def checklist_listar_ajustes(_auth=Depends(verificar_token), db=Depends(get_db)):
    """Extrato dos ajustes manuais de pontos — só gestão."""
    if not _eh_gestor_ck(_auth):
        raise HTTPException(status_code=403, detail="Apenas gestores.")
    rows = await db.fetch(
        """SELECT a.usuario_login AS login, COALESCE(u.nome, a.usuario_login) AS nome,
                  a.pts, a.motivo, a.criado_por, a.criado_em
           FROM checklist.ajustes_pontos a
           LEFT JOIN public.usuarios_garra u ON u.login = a.usuario_login
           ORDER BY a.criado_em DESC LIMIT 100"""
    )
    return [dict(r) for r in rows]
