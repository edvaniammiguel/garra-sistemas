"""routers.jardinagem — as 46 rotas do módulo Jardinagem.

Refatoração Fase 2 · Etapa 1 (04/07/2026). Corpos das rotas IDÊNTICOS aos
que viviam no main.py — mudou apenas @app→@router e a origem dos imports
(core/). Golden test: auditoria_rotas.py.
"""
import os, io, json, time, uuid, calendar, secrets
import base64
import bcrypt
from typing import Optional
from datetime import datetime, timedelta, date
from fastapi import APIRouter, Request, HTTPException, Depends, Header, UploadFile, File, Form, Body
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse, Response, StreamingResponse

from core.config import (
    JARD_DIR, STATIC_DIR, TEMPLATES_DIR,
    MAIL_DESTINO, MAIL_CC, MAIL_USERNAME, MAIL_PASSWORD,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
)
from core.db import ajard_query, ajard_query_id
from core.auth import gerar_token_jard, verificar_token_jard, exigir_acesso_jardinagem
from core.storage import storage_upload, storage_url, storage_delete
from core.helpers import comprimir_imagem, next_code, semanas_do_mes, enviar_email_smtp

router = APIRouter()


_MESES_PT = {1:"Jan",2:"Fev",3:"Mar",4:"Abr",5:"Mai",6:"Jun",
             7:"Jul",8:"Ago",9:"Set",10:"Out",11:"Nov",12:"Dez"}
def nome_arquivo_semana(sem, tipo: str) -> str:
    """Gera nome legível: relatorio-fotos-jun2026-semana1.xlsx"""
    import re
    # Extrair número da semana do label
    num = "?"
    try:
        m = re.search(r'Semana\s*(\d+)', sem.get("label") or "", re.IGNORECASE)
        if m: num = m.group(1)
    except: pass
    # Extrair mês e ano — tenta data_ini primeiro, depois extrai do label
    mes_abr, ano = "???", "????"
    try:
        di = sem.get("data_ini")
        if di and hasattr(di, "month"):
            mes_abr = _MESES_PT.get(di.month, f"{di.month:02d}").lower()
            ano = str(di.year)
        else:
            # Extrai do label: "Semana 1 — 01/06/2026 ..."
            dm = re.search(r'(\d{2})/(\d{2})/(\d{4})', sem.get("label") or "")
            if dm:
                mes_abr = _MESES_PT.get(int(dm.group(2)), dm.group(2)).lower()
                ano = dm.group(3)
    except: pass
    return f"relatorio-{tipo.lower()}-{mes_abr}{ano}-semana{num}.xlsx"

@router.get("/jardinagem", response_class=HTMLResponse)
@router.get("/jardinagem/", response_class=HTMLResponse)
async def jard_index():
    path = os.path.join(TEMPLATES_DIR, "desk-admin.html")
    return open(path, encoding="utf-8").read()

@router.get("/jardinagem/desktop")
async def jard_desktop_login():
    # Login unificado — redireciona para o admin (SSO abre desk-app direto)
    return RedirectResponse(url="/admin")

@router.get("/jardinagem/desktop-app", response_class=HTMLResponse)
async def jard_desktop_app():
    # Desktop app para Luana/Admin
    path = os.path.join(TEMPLATES_DIR, "desk-app.html")
    return open(path, encoding="utf-8").read()

@router.get("/jardinagem/mobile")
async def jard_mobile():
    # Login unificado — redireciona para o mobile (SSO abre pwa-app direto)
    return RedirectResponse(url="/mobile")

@router.get("/jardinagem/mobile-app", response_class=HTMLResponse)
async def jard_mobile_app():
    # Mobile PWA app para Arthur/Breno
    path = os.path.join(STATIC_DIR, "pwa-app.html")
    return open(path, encoding="utf-8").read()

@router.get("/jardinagem/pwa-login.html")
async def jard_pwa_login_html():
    # Login unificado — redireciona para o mobile
    return RedirectResponse(url="/mobile")

@router.get("/jardinagem/pwa-app.html", response_class=HTMLResponse)
async def jard_pwa_app_html():
    # Resolve redirect "./pwa-app.html" do pwa-login.html
    path = os.path.join(STATIC_DIR, "pwa-app.html")
    return open(path, encoding="utf-8").read()

@router.get("/jardinagem/manifest.json")
async def jard_manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"))

@router.get("/jardinagem/sw.js")
async def jard_sw():
    # sw.js na raiz de static/ (não em static/js/)
    return FileResponse(os.path.join(STATIC_DIR, "sw.js"),
                        headers={"Service-Worker-Allowed": "/jardinagem", "Cache-Control": "no-cache"})

@router.post("/jardinagem/api/login")
async def jard_login(request: Request):
    d = await request.json()
    email = (d.get("email") or "").strip().lower()
    senha = (d.get("senha") or "").encode()
    usuario = await ajard_query(
        "SELECT * FROM public.usuarios_garra WHERE (email=%s OR login=%s) AND ativo=true LIMIT 1",
        (email, email), fetch="one"
    )
    if not usuario or not bcrypt.checkpw(senha, usuario["senha_hash"].encode()):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    token = gerar_token_jard(dict(usuario))
    return {"token": token, "nome": usuario["nome"], "perfil": usuario["perfil"]}

@router.get("/jardinagem/api/me")
async def jard_me(payload=Depends(verificar_token_jard)):
    return payload

@router.post("/jardinagem/api/logout")
async def jard_logout():
    return {"ok": True}

@router.get("/jardinagem/api/meses")
async def jard_list_meses(payload=Depends(exigir_acesso_jardinagem)):
    meses = await ajard_query("""
        SELECT m.*, COUNT(DISTINCT s.id) as total_semanas
        FROM jardinagem.meses m
        LEFT JOIN jardinagem.semanas s ON s.mes_id=m.id
        GROUP BY m.id ORDER BY m.ano DESC, m.mes DESC
    """)
    return [dict(r) for r in meses]

@router.delete("/jardinagem/api/meses/{mid}")
async def jard_del_mes(mid: int, payload=Depends(verificar_token_jard)):
    mes = await ajard_query("SELECT id FROM jardinagem.meses WHERE id=%s", (mid,), fetch="one")
    if not mes:
        raise HTTPException(status_code=404, detail="Mês não encontrado")
    semanas = await ajard_query("SELECT id FROM jardinagem.semanas WHERE mes_id=%s", (mid,))
    for s in semanas:
        pares = await ajard_query("SELECT id FROM jardinagem.pares WHERE semana_id=%s", (s["id"],))
        for p in pares:
            fotos = await ajard_query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
            paths = [f["storage_path"] for f in fotos if f["storage_path"]]
            if paths: storage_delete(paths)
            await ajard_query("DELETE FROM jardinagem.fotos WHERE par_id=%s", (p["id"],), fetch="none")
        await ajard_query("DELETE FROM jardinagem.pares WHERE semana_id=%s", (s["id"],), fetch="none")
        await ajard_query("DELETE FROM jardinagem.fila_sync WHERE semana_id=%s", (s["id"],), fetch="none")
        await ajard_query("DELETE FROM jardinagem.emails_enviados WHERE semana_id=%s", (s["id"],), fetch="none")
        await ajard_query("DELETE FROM jardinagem.relatorios_diarios WHERE semana_id=%s", (s["id"],), fetch="none")
    await ajard_query("DELETE FROM jardinagem.semanas WHERE mes_id=%s", (mid,), fetch="none")
    await ajard_query("DELETE FROM jardinagem.meses WHERE id=%s", (mid,), fetch="none")
    return {"ok": True}

@router.post("/jardinagem/api/meses")
async def jard_criar_mes(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    ano, mes = int(d["ano"]), int(d["mes"])
    nomes = ["","Janeiro","Fevereiro","Março","Abril","Maio","Junho","Julho","Agosto","Setembro","Outubro","Novembro","Dezembro"]
    label = d.get("label") or f"{nomes[mes]}/{ano}"
    exist = await ajard_query("SELECT id FROM jardinagem.meses WHERE ano=%s AND mes=%s", (ano,mes), fetch="one")
    ja_existia = False
    if exist:
        mes_id = exist["id"]
        ja_existia = True
    else:
        row = await ajard_query_id("INSERT INTO jardinagem.meses(ano,mes,label) VALUES(%s,%s,%s)", (ano,mes,label))
        mes_id = row["id"]
    sem_exist = await ajard_query("SELECT id FROM jardinagem.semanas WHERE mes_id=%s LIMIT 1", (mes_id,), fetch="one")
    if not sem_exist:
        await semanas_do_mes(ano, mes, mes_id)
    mes_data = await ajard_query("SELECT * FROM jardinagem.meses WHERE id=%s", (mes_id,), fetch="one")
    result = dict(mes_data)
    result["ja_existia"] = ja_existia
    return result

@router.patch("/jardinagem/api/meses/{mid}")
async def jard_patch_mes(mid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    if "label" in d:
        await ajard_query("UPDATE jardinagem.meses SET label=%s WHERE id=%s", (d["label"], mid), fetch="none")
    return {"ok": True}

@router.get("/jardinagem/api/meses/{mid}")
async def jard_get_mes(mid: int, payload=Depends(verificar_token_jard)):
    from concurrent.futures import ThreadPoolExecutor
    m = await ajard_query("SELECT * FROM jardinagem.meses WHERE id=%s", (mid,), fetch="one")
    if not m: raise HTTPException(status_code=404, detail="Não encontrado")
    result = dict(m)
    # 1 query semanas
    sems = await ajard_query("SELECT * FROM jardinagem.semanas WHERE mes_id=%s ORDER BY ordem", (mid,))
    if not sems:
        result["semanas"] = []
        return result
    sem_ids = [s["id"] for s in sems]
    # 1 query todos os pares do mês (elimina N+1)
    placeholders = ",".join(["%s"] * len(sem_ids))
    pares_raw = await ajard_query(
        f"SELECT * FROM jardinagem.pares WHERE semana_id IN ({placeholders}) AND (ativo IS NULL OR ativo=true) ORDER BY semana_id, codigo_a",
        tuple(sem_ids)
    )
    par_ids = [p["id"] for p in pares_raw] if pares_raw else []
    # 1 query todas as fotos do mês (elimina N+1)
    fotos_raw = []
    if par_ids:
        ph2 = ",".join(["%s"] * len(par_ids))
        fotos_raw = await ajard_query(
            f"SELECT * FROM jardinagem.fotos WHERE par_id IN ({ph2})",
            tuple(par_ids)
        )
    # Gerar todas as URLs em paralelo
    paths_map = {f["id"]: f["storage_path"] for f in fotos_raw if f.get("storage_path")}
    urls = {}
    if paths_map:
        with ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(storage_url, path): fid for fid, path in paths_map.items()}
            for future, fid in futures.items():
                try: urls[fid] = future.result(timeout=10)
                except: urls[fid] = ""
    # Montar estrutura por par
    fotos_por_par = {}
    for f in fotos_raw:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = []
        fd = dict(f)
        fd["url"] = urls.get(f["id"], "")
        fotos_por_par[pid].append(fd)
    # Montar estrutura por semana
    pares_por_sem = {}
    for p in pares_raw:
        sid = p["semana_id"]
        if sid not in pares_por_sem: pares_por_sem[sid] = []
        pd = dict(p)
        pd["fotos"] = fotos_por_par.get(p["id"], [])
        pares_por_sem[sid].append(pd)
    # Montar resultado final
    result["semanas"] = []
    for s in sems:
        sd = dict(s)
        sd["mes_id"] = mid
        sd["pares"] = pares_por_sem.get(s["id"], [])
        result["semanas"].append(sd)
    return result

@router.get("/jardinagem/api/semanas")
async def jard_listar_semanas(mes_id: int = None, payload=Depends(verificar_token_jard)):
    if not mes_id:
        hoje = date.today()
        row = await ajard_query("""SELECT m.id FROM jardinagem.meses m
                           JOIN jardinagem.semanas s ON s.mes_id=m.id
                           WHERE s.data_ini<=%s AND s.data_fim>=%s LIMIT 1""", (hoje,hoje), fetch="one")
        if not row:
            row = await ajard_query("SELECT id FROM jardinagem.meses ORDER BY ano DESC, mes DESC LIMIT 1", fetch="one")
        mes_id = row["id"] if row else None
    if not mes_id:
        return {"ok": True, "semanas": []}
    rows = await ajard_query(
        "SELECT * FROM jardinagem.semanas WHERE mes_id=%s ORDER BY ordem",
        (mes_id,)
    )
    semanas = []
    for r in rows:
        s = dict(r)
        s["data_inicio"] = r["data_ini"].isoformat() if r["data_ini"] else ""
        s["data_fim"]    = r["data_fim"].isoformat() if r["data_fim"] else ""
        semanas.append(s)
    return {"ok": True, "semanas": semanas}

@router.get("/jardinagem/api/semanas/ativa")
async def jard_semana_ativa(payload=Depends(verificar_token_jard)):
    hoje = date.today()
    row = await ajard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                        FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                        WHERE s.data_ini::date<=%s AND s.data_fim::date>=%s
                        AND s.status='aberta' LIMIT 1""", (hoje,hoje), fetch="one")
    if not row:
        row = await ajard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                            FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                            WHERE s.status='aberta'
                            ORDER BY s.id DESC LIMIT 1""", fetch="one")
    if not row:
        raise HTTPException(status_code=404, detail="Sem semana ativa")
    return dict(row)

@router.post("/jardinagem/api/semanas")
async def jard_criar_semana(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    mes_id = d.get("mes_id"); label = (d.get("label") or "").strip(); ordem = d.get("ordem",0)
    if not mes_id or not label: raise HTTPException(status_code=400, detail="mes_id e label obrigatórios")
    row = await ajard_query_id("INSERT INTO jardinagem.semanas (mes_id,label,ordem,status) VALUES (%s,%s,%s,'aberta')", (mes_id,label,ordem))
    return dict(row)

@router.patch("/jardinagem/api/semanas/{sid}")
async def jard_patch_semana(sid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    for col in ["label","status","enviado_em"]:
        if col in d:
            await ajard_query(f"UPDATE jardinagem.semanas SET {col}=%s WHERE id=%s", (d[col],sid), fetch="none")
    return {"ok": True}

@router.delete("/jardinagem/api/semanas/{sid}")
async def jard_del_semana(sid: int, payload=Depends(verificar_token_jard)):
    pares = await ajard_query("SELECT id FROM jardinagem.pares WHERE semana_id=%s", (sid,))
    for p in pares:
        fotos = await ajard_query("SELECT storage_path FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        paths = [f["storage_path"] for f in fotos if f["storage_path"]]
        if paths: storage_delete(paths)
        await ajard_query("DELETE FROM jardinagem.fotos WHERE par_id=%s", (p["id"],), fetch="none")
    await ajard_query("DELETE FROM jardinagem.pares WHERE semana_id=%s", (sid,), fetch="none")
    await ajard_query("DELETE FROM jardinagem.fila_sync WHERE semana_id=%s", (sid,), fetch="none")
    await ajard_query("DELETE FROM jardinagem.emails_enviados WHERE semana_id=%s", (sid,), fetch="none")
    await ajard_query("DELETE FROM jardinagem.relatorios_diarios WHERE semana_id=%s", (sid,), fetch="none")
    await ajard_query("DELETE FROM jardinagem.semanas WHERE id=%s", (sid,), fetch="none")
    return {"ok": True}

@router.get("/jardinagem/api/semanas/{sid}/status")
async def jard_status_semana(sid: int, payload=Depends(verificar_token_jard)):
    sem = await ajard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (sid,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Não encontrada")
    emails = await ajard_query("SELECT * FROM jardinagem.emails_enviados WHERE semana_id=%s ORDER BY enviado_em DESC", (sid,))
    tp = await ajard_query("SELECT COUNT(*) as n FROM jardinagem.pares WHERE semana_id=%s", (sid,), fetch="one")
    tf = await ajard_query("SELECT COUNT(*) as n FROM jardinagem.fotos f JOIN jardinagem.pares p ON p.id=f.par_id WHERE p.semana_id=%s", (sid,), fetch="one")
    tr = await ajard_query("SELECT COUNT(*) as n FROM jardinagem.relatorios_diarios WHERE semana_id=%s", (sid,), fetch="one")
    return {"semana":dict(sem),"total_pares":tp["n"],"total_fotos":tf["n"],"total_relatorios":tr["n"],"emails":[dict(e) for e in emails]}

@router.get("/jardinagem/api/pares")
async def jard_listar_pares(semana_id: int = None, payload=Depends(verificar_token_jard)):
    if not semana_id:
        return {"ok": False, "error": "semana_id obrigatório"}
    pares_raw = await ajard_query(
        "SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a",
        (semana_id,)
    )
    pares = []
    for p in pares_raw:
        pd = dict(p)
        fotos = await ajard_query("SELECT * FROM jardinagem.fotos WHERE par_id=%s", (p["id"],))
        pd["fotos"] = []
        for f in fotos:
            fd = dict(f)
            fd["url"] = storage_url(f["storage_path"]) if f["storage_path"] else ""
            pd["fotos"].append(fd)
        pares.append(pd)
    return {"ok": True, "pares": pares}

@router.post("/jardinagem/api/pares")
async def jard_criar_par(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    # ── Reserva ATÔMICA do próximo código (evita duplicação em salvamento paralelo) ──
    # Uma única UPDATE ... RETURNING: o config.next_code é elevado ao maior entre
    # (ele mesmo) e (MAX(codigo_d ativo)+1), depois avançado +2, e retorna o código
    # reservado. Por ser atômico no Postgres, dois pares simultâneos pegam números
    # diferentes — elimina a race condition que duplicava códigos.
    reserva = await ajard_query(
        """
        UPDATE jardinagem.config c
        SET valor = (
            GREATEST(
                (c.valor)::int,
                COALESCE((SELECT MAX(codigo_d)+1 FROM jardinagem.pares
                          WHERE (ativo IS NULL OR ativo=true)), 6050)
            ) + 2
        )::text
        WHERE c.chave = 'next_code'
        RETURNING (c.valor)::int - 2 AS cod
        """,
        fetch="one"
    )
    if reserva and reserva.get("cod") is not None:
        cod = int(reserva["cod"])
    else:
        # Fallback: config inexistente — calcula direto e cria a chave
        ultimo = await ajard_query(
            "SELECT MAX(codigo_d) as max_cod FROM jardinagem.pares WHERE (ativo IS NULL OR ativo=true)",
            fetch="one"
        )
        cod = max(int(ultimo.get("max_cod") or 6049), 6049) + 1
        await ajard_query("INSERT INTO jardinagem.config (chave,valor) VALUES ('next_code',%s) ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor",
                   (str(cod + 2),), fetch="none")

    row = await ajard_query_id("INSERT INTO jardinagem.pares (semana_id,codigo_a,codigo_d,local_nome,data_label,ordem,ativo) VALUES (%s,%s,%s,%s,%s,%s,true)",
                        (d["semana_id"],cod,cod+1,d.get("local_nome",""),d.get("data_label",""),d.get("ordem",0)))
    return dict(row)

@router.patch("/jardinagem/api/pares/{pid}")
async def jard_patch_par(pid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    for col in ["local_nome","ordem","semana_id","data_label"]:
        if col in d:
            await ajard_query(f"UPDATE jardinagem.pares SET {col}=%s WHERE id=%s", (d[col],pid), fetch="none")
    return {"ok": True}

@router.delete("/jardinagem/api/pares/{pid}")
async def jard_del_par(pid: int, payload=Depends(verificar_token_jard)):
    # Soft delete: marcar par como inativo (campo ativo=false)
    # Fotos não são deletadas — seguem vinculadas mas não aparecem
    # next_code NÃO é alterado — sequência de códigos é imutável
    await ajard_query("UPDATE jardinagem.pares SET ativo=false WHERE id=%s", (pid,), fetch="none")
    return {"ok": True}

@router.patch("/jardinagem/api/fotos/{fid}")
async def jard_patch_foto(fid: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    if "tipo" in d and d["tipo"] in ("antes", "depois"):
        await ajard_query("UPDATE jardinagem.fotos SET tipo=%s WHERE id=%s", (d["tipo"], fid), fetch="none")
    return {"ok": True}

@router.post("/jardinagem/api/fotos/avulsa")
async def jard_foto_avulsa(
    par_id: int = Form(...), tipo: str = Form(...),
    foto: UploadFile = File(...), payload=Depends(verificar_token_jard)
):
    import logging
    log = logging.getLogger(__name__)
    try:
        conteudo = await foto.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail="Arquivo vazio")
        dados = comprimir_imagem(conteudo)
        path  = storage_upload(dados, f"jardinagem/{datetime.now().strftime('%Y/%m')}/{uuid.uuid4().hex}.jpg")
        antiga = await ajard_query("SELECT id,storage_path FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s", (par_id,tipo), fetch="one")
        if antiga:
            if antiga.get("storage_path"):
                storage_delete([antiga["storage_path"]])
            await ajard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (antiga["id"],), fetch="none")
        row = await ajard_query_id(
            "INSERT INTO jardinagem.fotos (par_id,tipo,origem,enviado_por,storage_path,filename_orig,sincronizado) VALUES (%s,%s,'desktop',%s,%s,%s,true)",
            (par_id,tipo,str(payload["sub"]),path,foto.filename or "foto.jpg")
        )
        fd = dict(row); fd["url"] = storage_url(path)
        return fd
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"fotos/avulsa erro: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar foto: {str(e)}")

@router.post("/jardinagem/api/fotos/mobile")
async def jard_foto_mobile(
    semana_id: str = Form(...),
    local_nome: str = Form(""),
    tipo: str = Form("antes"),
    par_id: Optional[str] = Form(None),
    offline_id: Optional[str] = Form(None),
    foto: UploadFile = File(...),
    payload=Depends(verificar_token_jard)
):
    """Upload de foto pelo mobile (câmera ou galeria)."""
    import logging
    log = logging.getLogger(__name__)
    try:
        sid = None
        if semana_id and semana_id != "ativa":
            try: sid = int(semana_id)
            except: pass

        if not sid:
            import datetime as dt
            hoje = dt.date.today()
            row = await ajard_query("""
                SELECT id FROM jardinagem.semanas
                WHERE data_ini <= %s AND data_fim >= %s LIMIT 1
            """, (hoje, hoje), fetch="one")
            if row: sid = row["id"]

        if not sid:
            raise HTTPException(status_code=400, detail="Sem semana ativa. Crie um mês primeiro.")

        pid = None
        if par_id and par_id not in ("null","undefined",""):
            try: pid = int(par_id)
            except: pass

        if not pid:
            row = await ajard_query_id(
                "INSERT INTO jardinagem.pares (semana_id,codigo_a,codigo_d,local_nome,data_label,ordem) VALUES (%s,%s,%s,%s,%s,%s)",
                (sid, 0, 0, local_nome or "", "", 99)
            )
            pid = row["id"]
            cod = await next_code(2)
            await ajard_query("UPDATE jardinagem.pares SET codigo_a=%s, codigo_d=%s WHERE id=%s",
                      (cod, cod+1, pid), fetch="none")

        if offline_id:
            exist = await ajard_query(
                "SELECT id FROM jardinagem.fotos WHERE offline_id=%s", (offline_id,), fetch="one"
            )
            if exist:
                return {"ok": True, "foto_id": exist["id"], "duplicado": True}

        conteudo = await foto.read()
        if not conteudo:
            raise HTTPException(status_code=400, detail="Arquivo vazio")

        dados = comprimir_imagem(conteudo)
        path  = storage_upload(dados, f"jardinagem/{uuid.uuid4().hex}.jpg")

        antiga = await ajard_query(
            "SELECT id, storage_path FROM jardinagem.fotos WHERE par_id=%s AND tipo=%s",
            (pid, tipo), fetch="one"
        )
        if antiga:
            if antiga.get("storage_path"): storage_delete([antiga["storage_path"]])
            await ajard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (antiga["id"],), fetch="none")

        row = await ajard_query_id("""
            INSERT INTO jardinagem.fotos
            (par_id, tipo, origem, enviado_por, storage_path, filename_orig, sincronizado, offline_id)
            VALUES (%s, %s, 'mobile', %s, %s, %s, true, %s)
        """, (pid, tipo, str(payload["sub"]), path, foto.filename or "foto.jpg", offline_id))

        fd = dict(row)
        fd["url"] = storage_url(path)
        fd["par_id"] = pid
        return {"ok": True, "foto_id": fd["id"], "storage_path": path, "url": fd["url"], "par_id": pid}

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"fotos/mobile erro: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro: {str(e)}")

@router.delete("/jardinagem/api/fotos/{fid}")
async def jard_del_foto(fid: int, payload=Depends(verificar_token_jard)):
    f = await ajard_query("SELECT storage_path FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="one")
    if f: storage_delete([f["storage_path"]]); await ajard_query("DELETE FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="none")
    return {"ok": True}

@router.get("/jardinagem/api/fotos/{fid}/url")
async def jard_url_foto(fid: int, payload=Depends(verificar_token_jard)):
    f = await ajard_query("SELECT storage_path FROM jardinagem.fotos WHERE id=%s", (fid,), fetch="one")
    if not f: raise HTTPException(status_code=404, detail="Não encontrado")
    return {"url": storage_url(f["storage_path"])}

@router.post("/jardinagem/api/relatorios/km")
async def jard_criar_km(request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    semana_id = d.get("semana_id")
    if not semana_id:
        hoje = date.today()
        row = await ajard_query("SELECT id FROM jardinagem.semanas WHERE data_ini<=%s AND data_fim>=%s LIMIT 1", (hoje,hoje), fetch="one")
        if not row: raise HTTPException(status_code=404, detail="Sem semana ativa")
        semana_id = row["id"]
    local_nome  = (d.get("local_nome") or "").strip()
    km_ini      = d.get("km_inicial"); km_fin = d.get("km_final")
    if not local_nome: raise HTTPException(status_code=400, detail="local_nome obrigatório")
    if km_ini is None or km_fin is None: raise HTTPException(status_code=400, detail="km_inicial e km_final obrigatórios")
    if float(km_fin) < float(km_ini): raise HTTPException(status_code=400, detail="km_final não pode ser menor que km_inicial")
    offline_id = d.get("offline_id")
    if offline_id:
        exist = await ajard_query("SELECT id FROM jardinagem.relatorios_diarios WHERE offline_id=%s", (offline_id,), fetch="one")
        if exist: return {"ok": True, "duplicado": True, "id": exist["id"]}
    row = await ajard_query_id("""INSERT INTO jardinagem.relatorios_diarios
        (semana_id,usuario_id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao,offline_id)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (semana_id,payload["sub"],d.get("data",date.today().isoformat()),local_nome,
         float(km_ini),float(km_fin),d.get("hora_inicio"),d.get("hora_fim"),d.get("observacao",""),offline_id))
    return {"ok": True, "id": row["id"]}

@router.patch("/jardinagem/api/relatorios/{km_id}")
async def jard_editar_km(km_id: int, request: Request, payload=Depends(verificar_token_jard)):
    d = await request.json()
    local_nome  = (d.get("local_nome") or "").strip()
    km_ini      = d.get("km_inicial"); km_fin = d.get("km_final")
    if not local_nome: raise HTTPException(status_code=400, detail="local_nome obrigatório")
    if km_ini is None or km_fin is None: raise HTTPException(status_code=400, detail="km_inicial e km_final obrigatórios")
    if float(km_fin) < float(km_ini): raise HTTPException(status_code=400, detail="km_final não pode ser menor que km_inicial")
    
    # Atualiza o registro
    await ajard_query("""UPDATE jardinagem.relatorios_diarios 
        SET data=%s, local_nome=%s, km_inicial=%s, km_final=%s, 
            hora_inicio=%s, hora_fim=%s, observacao=%s
        WHERE id=%s""",
        (d.get("data",date.today().isoformat()), local_nome,
         float(km_ini), float(km_fin),
         d.get("hora_inicio"), d.get("hora_fim"), d.get("observacao",""),
         km_id), fetch="none")
    return {"ok": True, "id": km_id}

@router.delete("/jardinagem/api/relatorios/{km_id}")
async def jard_deletar_km(km_id: int, payload=Depends(verificar_token_jard)):
    await ajard_query("DELETE FROM jardinagem.relatorios_diarios WHERE id=%s", (km_id,), fetch="none")
    return {"ok": True, "id": km_id}

@router.get("/jardinagem/api/historico/hoje")
async def jard_historico_hoje(semana_id: Optional[int]=None, payload=Depends(verificar_token_jard)):
    hoje = date.today()
    if semana_id:
        fotos_raw = await ajard_query("""SELECT f.id,f.tipo,f.storage_path,f.filename_orig,p.local_nome,f.criado_em
            FROM jardinagem.fotos f JOIN jardinagem.pares p ON p.id=f.par_id
            WHERE p.semana_id=%s AND f.enviado_por=%s AND DATE(f.criado_em)=%s ORDER BY f.criado_em DESC""",
            (semana_id,payload["sub"],hoje))
        km_raw = await ajard_query("""SELECT id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao
            FROM jardinagem.relatorios_diarios WHERE semana_id=%s AND usuario_id=%s AND data=%s ORDER BY criado_em DESC""",
            (semana_id,payload["sub"],hoje))
    else:
        fotos_raw = await ajard_query("""SELECT f.id,f.tipo,f.storage_path,f.filename_orig,p.local_nome,f.criado_em
            FROM jardinagem.fotos f JOIN jardinagem.pares p ON p.id=f.par_id
            WHERE f.enviado_por=%s AND DATE(f.criado_em)=%s ORDER BY f.criado_em DESC""",
            (payload["sub"],hoje))
        km_raw = await ajard_query("""SELECT id,data,local_nome,km_inicial,km_final,hora_inicio,hora_fim,observacao
            FROM jardinagem.relatorios_diarios WHERE usuario_id=%s AND data=%s ORDER BY criado_em DESC""",
            (payload["sub"],hoje))
    # Gerar URLs das fotos em paralelo
    from concurrent.futures import ThreadPoolExecutor
    fotos_list = [dict(f) for f in fotos_raw]
    paths_hoje = [(i, f["storage_path"]) for i, f in enumerate(fotos_list) if f.get("storage_path")]
    urls_hoje = {}
    if paths_hoje:
        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = {ex.submit(storage_url, path): i for i, path in paths_hoje}
            for future, i in futures.items():
                try: urls_hoje[i] = future.result(timeout=10)
                except: urls_hoje[i] = ""
    fotos = []
    for i, f in enumerate(fotos_list):
        f["url"] = urls_hoje.get(i, "") if f.get("storage_path") else ""
        f["criado_em"] = f["criado_em"].isoformat() if f.get("criado_em") else ""
        fotos.append(f)
    km_list = []; km_total = 0.0
    for r in km_raw:
        rd = dict(r)
        ini = float(r["km_inicial"] or 0); fin = float(r["km_final"] or 0)
        rd["km_percorrido"] = round(fin-ini,1); rd["km_inicial"] = ini; rd["km_final"] = fin
        rd["hora_inicio"] = str(r["hora_inicio"]) if r["hora_inicio"] else ""
        rd["hora_fim"]    = str(r["hora_fim"])    if r["hora_fim"]    else ""
        km_total += rd["km_percorrido"]; km_list.append(rd)
    return {"data":hoje.isoformat(),"fotos":fotos,"km":km_list,"km_total":round(km_total,1)}

@router.get("/jardinagem/api/inicio")
async def jard_inicio(payload=Depends(verificar_token_jard)):
    """Rota de carregamento rápido — retorna semana ativa + pares + config em 1 chamada."""
    hoje = date.today()
    # 1. Semana ativa
    semana = await ajard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                        FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                        WHERE s.data_ini::date<=%s AND s.data_fim::date>=%s
                        AND s.status='aberta' LIMIT 1""", (hoje,hoje), fetch="one")
    if not semana:
        semana = await ajard_query("""SELECT s.*,m.id as mes_id,m.ano,m.mes,m.label as mes_label
                            FROM jardinagem.semanas s JOIN jardinagem.meses m ON m.id=s.mes_id
                            WHERE s.status='aberta'
                            ORDER BY s.id DESC LIMIT 1""", fetch="one")
    if not semana:
        return {"semana": None, "pares": [], "next_code": 6050}
    sid = semana["id"]
    # 2. Pares da semana (com fotos)
    pares = await ajard_query("""SELECT p.id,p.codigo_a,p.codigo_d,p.local_nome,p.ordem
                          FROM jardinagem.pares p
                          WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)
                          ORDER BY p.codigo_a""", (sid,))
    fotos = await ajard_query("""SELECT f.id,f.par_id,f.tipo,f.storage_path
                          FROM jardinagem.fotos f
                          JOIN jardinagem.pares p ON p.id=f.par_id
                          WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (sid,))
    fotos_por_par = {}
    for f in fotos:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = []
        fotos_por_par[pid].append({"id":f["id"],"tipo":f["tipo"],"storage_path":f["storage_path"]})
    pares_com_fotos = []
    for p in pares:
        pd = dict(p)
        pd["fotos"] = fotos_por_par.get(p["id"], [])
        pares_com_fotos.append(pd)
    # 3. Config (next_code)
    cfg = await ajard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    next_code = int(cfg["valor"]) if cfg else 6050
    return {"semana": dict(semana), "pares": pares_com_fotos, "next_code": next_code}

@router.get("/jardinagem/api/config")
async def jard_config(payload=Depends(verificar_token_jard)):
    rows = await ajard_query("SELECT * FROM jardinagem.config")
    return {r["chave"]: r["valor"] for r in rows}

@router.get("/jardinagem/api/clientes")
async def jard_clientes(payload=Depends(verificar_token_jard)):
    rows = await ajard_query("SELECT id,nome FROM public.clientes_garra WHERE ativo=true")
    return [dict(r) for r in rows]

@router.get("/jardinagem/api/km/mes/{mes_id}")
async def jard_km_mes(mes_id: int, payload=Depends(verificar_token_jard)):
    """Retorna todos os KMs do mês em 1 chamada — evita N chamadas /preview."""
    kms_raw = await ajard_query("""
        SELECT r.id, r.data, r.local_nome, r.km_inicial, r.km_final,
               r.hora_inicio, r.hora_fim, r.observacao, r.responsavel,
               u.nome as responsavel_nome
        FROM jardinagem.relatorios_diarios r
        JOIN jardinagem.semanas s ON s.id = r.semana_id
        JOIN public.usuarios_garra u ON u.id = r.usuario_id
        WHERE s.mes_id = %s
        ORDER BY r.data, r.criado_em
    """, (mes_id,))
    kms = [{"id": r["id"],
            "data": r["data"].strftime("%d/%m/%Y") if r["data"] else "",
            "local_nome": r["local_nome"] or "",
            "km_inicial": float(r["km_inicial"] or 0),
            "km_final": float(r["km_final"] or 0),
            "hora_inicio": str(r["hora_inicio"]) if r["hora_inicio"] else "",
            "hora_fim": str(r["hora_fim"]) if r["hora_fim"] else "",
            "observacao": r["observacao"] or "",
            "responsavel": r["responsavel_nome"] or r["responsavel"] or ""
            } for r in kms_raw]
    return {"mes_id": mes_id, "relatorios": kms}

@router.get("/jardinagem/api/relatorios/{semana_id}/preview")
async def jard_preview(semana_id: int, payload=Depends(verificar_token_jard)):
    from concurrent.futures import ThreadPoolExecutor
    sem = await ajard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    # 1 query pares + 1 query fotos (elimina N+1)
    pares_raw = await ajard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a", (semana_id,))
    fotos_raw = await ajard_query("""SELECT f.* FROM jardinagem.fotos f
        JOIN jardinagem.pares p ON p.id=f.par_id
        WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (semana_id,))
    fotos_por_par = {}
    for f in fotos_raw:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = {}
        fotos_por_par[pid][f["tipo"]] = f
    # Coletar paths para gerar URLs em paralelo
    paths_map = {}
    for p in pares_raw:
        pid = p["id"]
        fp = fotos_por_par.get(pid, {})
        fa = fp.get("antes"); fd = fp.get("depois")
        if fa and fa.get("storage_path"): paths_map[f"{pid}_antes"] = fa["storage_path"]
        if fd and fd.get("storage_path"): paths_map[f"{pid}_depois"] = fd["storage_path"]
    # Gerar todas as URLs em paralelo
    urls = {}
    if paths_map:
        with ThreadPoolExecutor(max_workers=12) as ex:
            futures = {ex.submit(storage_url, path): key for key, path in paths_map.items()}
            for future, key in futures.items():
                try: urls[key] = future.result(timeout=10)
                except: urls[key] = ""
    pares = []
    for p in pares_raw:
        pid = p["id"]
        fp = fotos_por_par.get(pid, {})
        fa = fp.get("antes"); fd = fp.get("depois")
        pares.append({"id":pid,"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":bool(fa),"foto_depois":bool(fd),
                      "url_antes":urls.get(f"{pid}_antes",""),
                      "url_depois":urls.get(f"{pid}_depois","")})
    kms_raw = await ajard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.semana_id=%s ORDER BY r.data,r.criado_em""", (semana_id,))
    kms = [{"id":r["id"],"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local_nome":r["local_nome"] or "",
            "km_inicial":float(r["km_inicial"] or 0),"km_final":float(r["km_final"] or 0),
            "hora_inicio":str(r["hora_inicio"]) if r["hora_inicio"] else "",
            "hora_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
            "observacao":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    return {"semana_id":semana_id,"label":sem["label"],"pares":pares,"relatorios":kms,
            "total_pares":len(pares),"pares_completos":sum(1 for p in pares if p["foto_antes"] and p["foto_depois"]),"total_km":len(kms)}

@router.get("/jardinagem/api/relatorios/{semana_id}/fotos")
async def jard_excel_fotos(semana_id: int, payload=Depends(verificar_token_jard)):
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_fotos
    sem = await ajard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    pares_raw = await ajard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a", (semana_id,))
    # 1 query para todas as fotos (elimina N+1)
    fotos_raw = await ajard_query("""SELECT f.* FROM jardinagem.fotos f
        JOIN jardinagem.pares p ON p.id=f.par_id
        WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (semana_id,))
    fotos_por_par = {}
    for f in fotos_raw:
        pid = f["par_id"]
        if pid not in fotos_por_par: fotos_por_par[pid] = {}
        fotos_por_par[pid][f["tipo"]] = dict(f)
    pares = []
    for p in pares_raw:
        fp = fotos_por_par.get(p["id"], {})
        pares.append({"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":fp.get("antes"), "foto_depois":fp.get("depois")})
    buf = gerar_relatorio_fotos(semana_dict, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition":f'attachment; filename="{nome_arquivo_semana(sem, "Fotos")}"'})

@router.get("/jardinagem/api/relatorios/{semana_id}/km")
async def jard_excel_km(semana_id: int, payload=Depends(verificar_token_jard)):
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_km
    sem = await ajard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    kms_raw = await ajard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.semana_id=%s ORDER BY r.data,r.criado_em""", (semana_id,))
    relatorios = [{"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local":r["local_nome"] or "",
                   "km_ini":float(r["km_inicial"] or 0),"km_fin":float(r["km_final"] or 0),
                   "hr_ini":str(r["hora_inicio"]) if r["hora_inicio"] else "","hr_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
                   "obs":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    buf = gerar_relatorio_km(semana_dict, relatorios)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition":f'attachment; filename="{nome_arquivo_semana(sem, "KM")}"'})

@router.post("/jardinagem/api/relatorios/{semana_id}/enviar")
async def jard_enviar_email(semana_id: int, payload=Depends(verificar_token_jard)):
    if not all([MAIL_USERNAME, MAIL_PASSWORD, MAIL_DESTINO]):
        raise HTTPException(status_code=400, detail="Email não configurado")
    import sys; sys.path.insert(0, os.path.join(JARD_DIR))
    from gerar_relatorio import gerar_relatorio_fotos, gerar_relatorio_km
    sem = await ajard_query("SELECT * FROM jardinagem.semanas WHERE id=%s", (semana_id,), fetch="one")
    if not sem: raise HTTPException(status_code=404, detail="Semana não encontrada")
    semana_dict = {"label":sem["label"],"data_ini":sem["data_ini"].strftime("%d/%m/%Y") if sem["data_ini"] else "","data_fim":sem["data_fim"].strftime("%d/%m/%Y") if sem["data_fim"] else ""}
    pares_raw = await ajard_query("SELECT * FROM jardinagem.pares WHERE semana_id=%s AND (ativo IS NULL OR ativo=true) ORDER BY codigo_a", (semana_id,))
    # 1 query para todas as fotos (elimina N+1)
    fotos_raw_email = await ajard_query("""SELECT f.* FROM jardinagem.fotos f
        JOIN jardinagem.pares p ON p.id=f.par_id
        WHERE p.semana_id=%s AND (p.ativo IS NULL OR p.ativo=true)""", (semana_id,))
    fotos_por_par_email = {}
    for f in fotos_raw_email:
        pid = f["par_id"]
        if pid not in fotos_por_par_email: fotos_por_par_email[pid] = {}
        fotos_por_par_email[pid][f["tipo"]] = dict(f)
    pares = []
    for p in pares_raw:
        fp = fotos_por_par_email.get(p["id"], {})
        pares.append({"codigo_a":p["codigo_a"],"codigo_d":p["codigo_d"],"local_nome":p["local_nome"] or "",
                      "foto_antes":fp.get("antes"), "foto_depois":fp.get("depois")})
    kms_raw = await ajard_query("""SELECT r.*,u.nome as responsavel_nome FROM jardinagem.relatorios_diarios r
        JOIN public.usuarios_garra u ON u.id=r.usuario_id WHERE r.semana_id=%s ORDER BY r.data,r.criado_em""", (semana_id,))
    relatorios = [{"data":r["data"].strftime("%d/%m/%Y") if r["data"] else "","local":r["local_nome"] or "",
                   "km_ini":float(r["km_inicial"] or 0),"km_fin":float(r["km_final"] or 0),
                   "hr_ini":str(r["hora_inicio"]) if r["hora_inicio"] else "","hr_fim":str(r["hora_fim"]) if r["hora_fim"] else "",
                   "obs":r["observacao"] or "","responsavel":r["responsavel_nome"] or ""} for r in kms_raw]
    try:
        buf_fotos = gerar_relatorio_fotos(semana_dict, pares, SUPABASE_URL, SUPABASE_SERVICE_KEY)
        buf_km    = gerar_relatorio_km(semana_dict, relatorios)
        corpo = f"""<div style="font-family:Arial;padding:20px;">
            <h2 style="color:#1A2A5E;">Relatório Jardinagem — {semana_dict['label']}</h2>
            <p>Segue em anexo os relatórios fotográfico e de KM da semana.</p>
            <p style="color:#64748B;font-size:12px;">Garra Terraplenagem e Caçambas</p></div>"""
        enviar_email_smtp(MAIL_DESTINO, f"Relatório Jardinagem — {semana_dict['label']}", corpo,
                          [(nome_arquivo_semana(sem, 'Fotos'), buf_fotos.getvalue()),
                           (nome_arquivo_semana(sem, 'KM'),    buf_km.getvalue())])
        await ajard_query("INSERT INTO jardinagem.emails_enviados (semana_id,destinatario,assunto,status) VALUES (%s,%s,%s,'enviado')",
                   (semana_id,MAIL_DESTINO,f"Relatório {semana_dict['label']}"), fetch="none")
        await ajard_query("UPDATE jardinagem.semanas SET status='enviada',enviado_em=NOW() WHERE id=%s", (semana_id,), fetch="none")
        return {"ok": True, "mensagem": f"Relatórios enviados para {MAIL_DESTINO} (cc: {MAIL_CC})"}
    except Exception as e:
        await ajard_query("INSERT INTO jardinagem.emails_enviados (semana_id,destinatario,assunto,status,erro_msg) VALUES (%s,%s,%s,'erro',%s)",
                   (semana_id,MAIL_DESTINO,f"Relatório {semana_dict['label']}",str(e)), fetch="none")
        raise HTTPException(status_code=500, detail=f"Falha no envio: {str(e)}")

@router.get("/jardinagem/api/health")
async def jard_health():
    try:
        await ajard_query("SELECT 1", fetch="one")
        return {"status":"ok","db":"conectado","modulo":"jardinagem"}
    except Exception as e:
        return {"status":"erro","db":str(e)}
