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

# ═══════════════════════════════════════════════════════════════════════════
# MURAL DE AVISOS
# (09/11/2026) Cards de segurança/orientação: imagem + legenda no mesmo aviso, botão "✔ Li e entendi"
# por aviso (registro login/data — igual à lista do DDS), lista de quem leu/não leu para a gestão,
# validade (some do painel, fica no histórico), lembrete 48 h a quem não confirmou e permissão
# "Mural — Publicar" (módulo mural_publicar) em vez de "só admin".
# ═══════════════════════════════════════════════════════════════════════════
from core.permissions import tem_modulo as _tem_modulo
from core.notificar import notificar as _notificar

_MURAL_V2_OK = False

async def _garantir_mural_v2():
    """Colunas novas + tabela de confirmações. Idempotente, roda 1x por processo."""
    global _MURAL_V2_OK
    if _MURAL_V2_OK:
        return
    for sql in (
        "ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS destinatario TEXT DEFAULT ''",
        "ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS imagem_path TEXT DEFAULT ''",
        "ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS exige_confirmacao BOOLEAN DEFAULT true",
        "ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS valido_ate DATE",
        "ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS bloqueia_checklist BOOLEAN DEFAULT false",
        "ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS audio_path TEXT DEFAULT ''",
        """CREATE TABLE IF NOT EXISTS public.mural_confirmacoes (
               id SERIAL PRIMARY KEY,
               aviso_id INTEGER NOT NULL REFERENCES public.mural_avisos(id) ON DELETE CASCADE,
               usuario_login TEXT NOT NULL,
               usuario_id UUID,
               confirmado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
               UNIQUE (aviso_id, usuario_login))""",
        "CREATE INDEX IF NOT EXISTS mural_confirmacoes_login_idx ON public.mural_confirmacoes (usuario_login)",
    ):
        try:
            await ajard_query(sql, fetch="none")
        except Exception as e:
            print(f"[mural v2] {e}")
    _MURAL_V2_OK = True


async def verificar_mural_publicar(payload=Depends(verificar_token)):
    """(09/11/2026) Quem publica no Mural: admin (Master) ou módulo mural_publicar (perfil/exceção)."""
    if not await _tem_modulo(payload, ["mural_publicar"]):
        raise HTTPException(status_code=403, detail="Sem permissão para publicar no Mural (módulo Mural — Publicar avisos)")
    return payload


def _login_de(payload):
    return payload.get("login") or payload.get("sub", "")


def _aviso_para(r, perfil, login):
    """Regra única de visibilidade: destinatário individual > perfis > todos."""
    dest = (r.get("destinatario") or "").strip()
    perfis_list = [p.strip() for p in (r.get("perfis") or "").split(",") if p.strip()]
    if dest:
        return dest == login
    if perfis_list:
        return perfil in perfis_list
    return True


async def _publico_do_aviso(r):
    """Usuários ativos que devem ver o aviso (para a lista de pendentes)."""
    dest = (r.get("destinatario") or "").strip()
    perfis_list = [p.strip() for p in (r.get("perfis") or "").split(",") if p.strip()]
    us = await ajard_query("SELECT id, login, nome, perfil, email FROM public.usuarios_garra WHERE ativo=true ORDER BY nome") or []
    if dest:
        return [u for u in us if u["login"] == dest or (u.get("email") or "") == dest]
    if perfis_list:
        return [u for u in us if (u.get("perfil") or "") in perfis_list]
    return list(us)


@router.get("/api/mural")
async def listar_mural(todos: int = 0, payload=Depends(verificar_token)):
    """Lista avisos ativos para o perfil/login do usuário.
    (09/07/2026) todos=1 → modo GESTÃO: lista completa sem filtro (tela de administração).
    (09/11/2026) modo gestão exige mural_publicar; traz contagem de confirmações e inclui vencidos."""
    ignorar = bool(todos) and await _tem_modulo(payload, ["mural_publicar"])
    return await _avisos_visiveis(payload, ignorar_filtros=ignorar)


async def _avisos_visiveis(payload, ignorar_filtros=False):
    """(09/07/2026) Filtro de visibilidade em FONTE ÚNICA — usado pela listagem
    e pelo contador de não-lidos (sem duplicar regra)."""
    await _garantir_mural_v2()
    perfil = payload.get("perfil", "")
    login = _login_de(payload)
    rows = await ajard_query(
        "SELECT a.id, a.titulo, a.mensagem, a.perfis, a.destinatario, a.criado_em, a.criado_por, "
        "       a.imagem_path, coalesce(a.exige_confirmacao,true) AS exige_confirmacao, a.valido_ate, "
        "       coalesce(a.bloqueia_checklist,false) AS bloqueia_checklist, a.audio_path, "
        "       (SELECT count(*) FROM public.mural_confirmacoes c WHERE c.aviso_id=a.id)::int AS confirmacoes, "
        "       (SELECT c.confirmado_em FROM public.mural_confirmacoes c WHERE c.aviso_id=a.id AND c.usuario_login=%s) AS confirmado_em "
        "FROM public.mural_avisos a WHERE a.ativo=true ORDER BY a.criado_em DESC",
        (login,), fetch="all") or []
    hoje = datetime.now().date()
    result = []
    for r in rows:
        vencido = bool(r.get("valido_ate")) and r["valido_ate"] < hoje
        if not ignorar_filtros:
            if vencido or not _aviso_para(r, perfil, login):
                continue
        img = r.get("imagem_path") or ""
        img_url = ""
        if img:
            try:
                from core.storage import storage_url
                img_url = storage_url(img)
            except Exception:
                img_url = ""
        aud = r.get("audio_path") or ""
        aud_url = ""
        if aud:
            try:
                from core.storage import storage_url
                aud_url = storage_url(aud)
            except Exception:
                aud_url = ""
        result.append({
            "id": r["id"],
            "bloqueia_checklist": bool(r.get("bloqueia_checklist")),
            "audio_path": aud,
            "audio_url": aud_url,
            "titulo": r["titulo"],
            "mensagem": r["mensagem"],
            "perfis": (r.get("perfis") or "").strip(),
            "destinatario": (r.get("destinatario") or "").strip(),
            "criado_em": r["criado_em"].isoformat() if r["criado_em"] else "",
            "criado_por": r.get("criado_por") or "",
            "imagem_path": img,
            "imagem_url": img_url,
            "exige_confirmacao": bool(r.get("exige_confirmacao")),
            "valido_ate": r["valido_ate"].isoformat() if r.get("valido_ate") else "",
            "vencido": vencido,
            "confirmado_em": r["confirmado_em"].isoformat() if r.get("confirmado_em") else "",
            "confirmacoes": int(r.get("confirmacoes") or 0),
        })
    return result


# ─── (09/07/2026) Badge de não-lidos do Mural — Fase 1 das notificações ──────
@router.get("/api/mural/nao-lidos")
async def mural_nao_lidos(payload=Depends(verificar_token)):
    """Quantos avisos visíveis o usuário ainda não leu.
    (09/11/2026) aviso com confirmação conta até o "Li e entendi"; os demais seguem o marco de leitura."""
    login = _login_de(payload)
    avisos = await _avisos_visiveis(payload)
    lido_em = await ajard_query(
        "SELECT lido_em FROM public.mural_leituras WHERE usuario_login=%s",
        (login,), fetch="one")
    marco = (lido_em or {}).get("lido_em")
    marco_iso = marco.isoformat() if marco else ""
    n = 0
    for a in avisos:
        if a["exige_confirmacao"]:
            n += 0 if a["confirmado_em"] else 1
        else:
            n += 1 if (not marco_iso or (a.get("criado_em") or "") > marco_iso) else 0
    return {"nao_lidos": n}

@router.post("/api/mural/lido")
async def mural_marcar_lido(payload=Depends(verificar_token)):
    """Marca o mural como lido AGORA para o usuário (abriu o painel) — vale para avisos sem confirmação."""
    login = _login_de(payload)
    await ajard_query(
        """INSERT INTO public.mural_leituras (usuario_login, lido_em)
           VALUES (%s, now())
           ON CONFLICT (usuario_login) DO UPDATE SET lido_em = now()""",
        (login,), fetch="none")
    return {"ok": True}


@router.post("/api/mural/{aviso_id}/confirmar")
async def mural_confirmar(aviso_id: int, payload=Depends(verificar_token)):
    """(09/11/2026) ✔ Li e entendi — registra login + data/hora. Só para aviso que a pessoa vê; idempotente."""
    await _garantir_mural_v2()
    login = _login_de(payload)
    r = await ajard_query("SELECT * FROM public.mural_avisos WHERE id=%s AND ativo=true", (aviso_id,), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Aviso não encontrado")
    if not _aviso_para(r, payload.get("perfil", ""), login):
        raise HTTPException(status_code=403, detail="Este aviso não é para o seu perfil")
    u = await ajard_query("SELECT id FROM public.usuarios_garra WHERE login=%s AND ativo=true", (login,), fetch="one")
    await ajard_query(
        """INSERT INTO public.mural_confirmacoes (aviso_id, usuario_login, usuario_id)
           VALUES (%s,%s,%s) ON CONFLICT (aviso_id, usuario_login) DO NOTHING""",
        (aviso_id, login, (u or {}).get("id")), fetch="none")
    c = await ajard_query("SELECT confirmado_em FROM public.mural_confirmacoes WHERE aviso_id=%s AND usuario_login=%s",
                          (aviso_id, login), fetch="one")
    return {"ok": True, "confirmado_em": c["confirmado_em"].isoformat() if c else ""}


@router.get("/api/mural/{aviso_id}/confirmacoes")
async def mural_confirmacoes(aviso_id: int, payload=Depends(verificar_mural_publicar)):
    """(09/11/2026) Quem confirmou (nome, login, quando) e quem ainda não (sobre o público do aviso)."""
    await _garantir_mural_v2()
    r = await ajard_query("SELECT * FROM public.mural_avisos WHERE id=%s", (aviso_id,), fetch="one")
    if not r:
        raise HTTPException(status_code=404, detail="Aviso não encontrado")
    publico = await _publico_do_aviso(r)
    conf = await ajard_query(
        "SELECT usuario_login, confirmado_em FROM public.mural_confirmacoes WHERE aviso_id=%s ORDER BY confirmado_em",
        (aviso_id,)) or []
    quando = {c["usuario_login"]: c["confirmado_em"] for c in conf}
    confirmaram, pendentes = [], []
    for u in publico:
        item = {"login": u["login"], "nome": u["nome"], "perfil": u.get("perfil") or ""}
        if u["login"] in quando:
            item["quando"] = quando[u["login"]].isoformat()
            confirmaram.append(item)
        else:
            pendentes.append(item)
    # confirmou mas já não está no público (mudou de perfil / desativado): entra no histórico mesmo assim
    vistos = {c["login"] for c in confirmaram}
    for c in conf:
        if c["usuario_login"] not in vistos:
            confirmaram.append({"login": c["usuario_login"], "nome": c["usuario_login"], "perfil": "", "quando": c["confirmado_em"].isoformat(), "fora_do_publico": True})
    confirmaram.sort(key=lambda x: x.get("quando") or "")
    return {"aviso_id": aviso_id, "titulo": r["titulo"], "total": len(publico),
            "confirmaram": confirmaram, "pendentes": pendentes}


@router.post("/api/mural/imagem")
async def mural_imagem(request: Request, payload=Depends(verificar_mural_publicar)):
    """(09/11/2026) Sobe a imagem do card para o Storage (garra-fotos:mural/…) e devolve o path.
    JPG/PNG/WebP até 8 MB (o Admin já reduz para ~1600 px antes de enviar)."""
    form = await request.form()
    f = form.get("imagem")
    if f is None:
        raise HTTPException(status_code=400, detail="Envie o campo 'imagem'")
    dados = await f.read()
    if not dados:
        raise HTTPException(status_code=400, detail="Imagem vazia")
    if len(dados) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Imagem acima de 8 MB")
    ct = (f.content_type or "image/jpeg").lower()
    if ct not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(status_code=400, detail="Formato não aceito — use JPG, PNG ou WebP")
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[ct]
    import uuid as _uuid
    from core.storage import storage_upload, storage_url
    path = f"mural/{datetime.now():%Y/%m}/{_uuid.uuid4().hex}.{ext}"
    try:
        sp = storage_upload(dados, path, content_type=ct)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não foi possível salvar a imagem no Storage: {e}")
    url = ""
    try:
        url = storage_url(sp)
    except Exception:
        pass
    return {"ok": True, "imagem_path": sp, "imagem_url": url}


@router.get("/api/mural/pendentes-checklist")
async def mural_pendentes_checklist(payload=Depends(verificar_token)):
    """(09/11/2026) Alertas marcados "exigir antes do checklist" que o usuário ainda não confirmou.
    O checklist mostra um por um em tela cheia e só carrega depois do ✔ Li e entendi."""
    avisos = await _avisos_visiveis(payload)
    return [a for a in avisos if a["bloqueia_checklist"] and a["exige_confirmacao"] and not a["confirmado_em"]]


@router.post("/api/mural/audio")
async def mural_audio(request: Request, payload=Depends(verificar_mural_publicar)):
    """(09/11/2026) Áudio curto (30–60 s) da gestão explicando o card. MP3/M4A/OGG/WebM/WAV até 15 MB."""
    form = await request.form()
    f = form.get("audio")
    if f is None:
        raise HTTPException(status_code=400, detail="Envie o campo 'audio'")
    dados = await f.read()
    if not dados:
        raise HTTPException(status_code=400, detail="Áudio vazio")
    if len(dados) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Áudio acima de 15 MB")
    ct = (f.content_type or "").lower().split(";")[0]
    exts = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a", "audio/aac": "aac",
            "audio/ogg": "ogg", "audio/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav", "video/webm": "webm"}
    if ct not in exts:
        raise HTTPException(status_code=400, detail=f"Formato de áudio não aceito ({ct or 'desconhecido'}) — use MP3, M4A, OGG ou WebM")
    import uuid as _uuid
    from core.storage import storage_upload, storage_url
    path = f"mural/audio/{datetime.now():%Y/%m}/{_uuid.uuid4().hex}.{exts[ct]}"
    try:
        sp = storage_upload(dados, path, content_type=ct)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Não foi possível salvar o áudio no Storage: {e}")
    url = ""
    try:
        url = storage_url(sp)
    except Exception:
        pass
    return {"ok": True, "audio_path": sp, "audio_url": url}


@router.get("/api/mural/relatorio")
async def mural_relatorio(mes: str = "", payload=Depends(verificar_mural_publicar)):
    """(09/11/2026) Relatório mensal "Alertas de Segurança Garra": por aviso publicado no mês (com confirmação
    exigida) → público, confirmados, %, pendentes; resumo (temas, média, colaboradores com 100%). Vai para o PGR."""
    await _garantir_mural_v2()
    try:
        ano, m = (mes or datetime.now().strftime("%Y-%m")).split("-")[:2]
        ano, m = int(ano), int(m)
    except Exception:
        raise HTTPException(status_code=400, detail="mes inválido — use AAAA-MM")
    ini = datetime(ano, m, 1)
    fim = datetime(ano + (m // 12), (m % 12) + 1, 1)
    rows = await ajard_query(
        """SELECT * FROM public.mural_avisos
            WHERE coalesce(exige_confirmacao,true) AND criado_em >= %s AND criado_em < %s
            ORDER BY criado_em""", (ini, fim)) or []
    avisos, por_pessoa = [], {}
    for r in rows:
        publico = await _publico_do_aviso(r)
        conf = await ajard_query("SELECT usuario_login, confirmado_em FROM public.mural_confirmacoes WHERE aviso_id=%s", (r["id"],)) or []
        quando = {c["usuario_login"]: c["confirmado_em"] for c in conf}
        confs, pend = [], []
        for u in publico:
            pp = por_pessoa.setdefault(u["login"], {"nome": u["nome"], "perfil": u.get("perfil") or "", "total": 0, "confirmou": 0})
            pp["total"] += 1
            if u["login"] in quando:
                pp["confirmou"] += 1
                confs.append({"nome": u["nome"], "quando": quando[u["login"]].isoformat()})
            else:
                pend.append({"nome": u["nome"], "perfil": u.get("perfil") or ""})
        avisos.append({
            "id": r["id"], "titulo": r["titulo"], "criado_em": r["criado_em"].isoformat(), "criado_por": r.get("criado_por") or "",
            "perfis": (r.get("perfis") or "").strip() or "todos", "destinatario": (r.get("destinatario") or "").strip(),
            "ativo": bool(r.get("ativo")), "bloqueia_checklist": bool(r.get("bloqueia_checklist")),
            "tem_imagem": bool(r.get("imagem_path")), "tem_audio": bool(r.get("audio_path")),
            "publico": len(publico), "confirmados": len(confs), "pct": round(100 * len(confs) / len(publico)) if publico else 0,
            "confirmaram": confs, "pendentes": pend,
        })
    pessoas = sorted(por_pessoa.values(), key=lambda x: x["nome"])
    for pp in pessoas:
        pp["pct"] = round(100 * pp["confirmou"] / pp["total"]) if pp["total"] else 0
    tot_pub = sum(a["publico"] for a in avisos); tot_conf = sum(a["confirmados"] for a in avisos)
    return {
        "mes": f"{ano:04d}-{m:02d}", "gerado_em": datetime.now().isoformat(), "gerado_por": payload.get("nome", ""),
        "resumo": {"temas": len(avisos), "publico_total": tot_pub, "confirmacoes": tot_conf,
                   "pct_geral": round(100 * tot_conf / tot_pub) if tot_pub else 0,
                   "colaboradores": len(pessoas), "colaboradores_100": sum(1 for pp in pessoas if pp["total"] and pp["confirmou"] == pp["total"])},
        "avisos": avisos, "pessoas": pessoas,
    }


def _valido_ate(dados):
    v = (dados.valido_ate or "").strip()
    if not v:
        return None
    try:
        return datetime.strptime(v[:10], "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="Validade inválida — use AAAA-MM-DD")


@router.post("/api/mural")
async def criar_aviso_mural(dados: MuralCreate, payload=Depends(verificar_mural_publicar)):
    """Publica aviso no mural (admin ou módulo mural_publicar)."""
    await _garantir_mural_v2()
    row = await ajard_query(
        """INSERT INTO public.mural_avisos (titulo, mensagem, perfis, destinatario, criado_por, imagem_path, exige_confirmacao, valido_ate,
                                            bloqueia_checklist, audio_path)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (dados.titulo, dados.mensagem, dados.perfis, dados.destinatario, payload.get("nome", ""),
         dados.imagem_path or "", bool(dados.exige_confirmacao), _valido_ate(dados),
         bool(dados.bloqueia_checklist), dados.audio_path or ""), fetch="one"
    )
    novo_id = (row or {}).get("id")
    # (09/11/2026) publicou → sino/push para o público (igual a qualquer notificação); abre Minha conta
    try:
        publico = await _publico_do_aviso({"destinatario": dados.destinatario, "perfis": dados.perfis})
        ids = [u["id"] for u in publico]
        if ids:
            pref = "🦺 Alerta de segurança" if dados.bloqueia_checklist else "📌 Novo aviso no Mural"
            corpo = dados.titulo + (" — confirme a leitura em Minha conta › Mural" if dados.exige_confirmacao else "")
            await _notificar("mural_novo", pref, corpo, "/mobile?conta=1", apenas=ids, chave=f"mural-novo:{novo_id}")
    except Exception as e:
        print(f"[mural novo] notificação falhou: {e}")
    return {"ok": True, "id": novo_id}

@router.put("/api/mural/{aviso_id}")
async def editar_aviso_mural(aviso_id: int, dados: MuralCreate, payload=Depends(verificar_mural_publicar)):
    """Edita um aviso existente (confirmações já feitas são mantidas)."""
    await _garantir_mural_v2()
    await ajard_query(
        """UPDATE public.mural_avisos SET titulo=%s, mensagem=%s, perfis=%s, destinatario=%s,
                  imagem_path=%s, exige_confirmacao=%s, valido_ate=%s, bloqueia_checklist=%s, audio_path=%s WHERE id=%s""",
        (dados.titulo, dados.mensagem, dados.perfis, dados.destinatario,
         dados.imagem_path or "", bool(dados.exige_confirmacao), _valido_ate(dados),
         bool(dados.bloqueia_checklist), dados.audio_path or "", aviso_id), fetch="none"
    )
    return {"ok": True}

@router.delete("/api/mural/{aviso_id}")
async def desativar_aviso_mural(aviso_id: int, payload=Depends(verificar_mural_publicar)):
    """Desativa aviso (soft delete) — confirmações ficam no histórico."""
    await ajard_query(
        "UPDATE public.mural_avisos SET ativo=false WHERE id=%s",
        (aviso_id,), fetch="none"
    )
    return {"ok": True}


# ─── (09/11/2026) Lembrete 48 h: quem não confirmou recebe aviso no sino/push; repete a cada 48 h ─────
async def mural_lembretes_pendentes():
    """Roda a cada hora (startup). Para cada aviso ativo, não vencido, com confirmação exigida e publicado
    há 48 h+: notifica cada pendente 1x por janela de 48 h (chave idempotente em notificacao_marcas)."""
    await _garantir_mural_v2()
    rows = await ajard_query(
        """SELECT * FROM public.mural_avisos
            WHERE ativo=true AND coalesce(exige_confirmacao,true)
              AND criado_em <= now() - interval '48 hours'
              AND (valido_ate IS NULL OR valido_ate >= current_date)""") or []
    n = 0
    for r in rows:
        publico = await _publico_do_aviso(r)
        conf = await ajard_query("SELECT usuario_login FROM public.mural_confirmacoes WHERE aviso_id=%s", (r["id"],)) or []
        ja = {c["usuario_login"] for c in conf}
        janela = int((datetime.now() - r["criado_em"].replace(tzinfo=None)).total_seconds() // (48 * 3600))
        for u in publico:
            if u["login"] in ja:
                continue
            n += await _notificar("mural_confirmar", f"📌 Aviso aguardando sua confirmação",
                                  f"{r['titulo']} — abra Minha conta › Mural e toque em ✔ Li e entendi",
                                  "/mobile?conta=1", apenas=[u["id"]], chave=f"mural:{r['id']}:{u['id']}:{janela}") or 0
    return n


async def _mural_lembretes_loop():
    import asyncio
    await asyncio.sleep(90)   # deixa o startup terminar
    while True:
        try:
            await mural_lembretes_pendentes()
        except Exception as e:
            print(f"[mural lembretes] {e}")
        await asyncio.sleep(3600)


@router.on_event("startup")
async def _mural_lembretes_startup():
    import asyncio
    asyncio.create_task(_mural_lembretes_loop())


@router.post("/api/mural/lembretes/rodar")
async def mural_lembretes_rodar(payload=Depends(verificar_mural_publicar)):
    """Dispara os lembretes agora (botão na tela de gestão / teste)."""
    return {"ok": True, "enviados": await mural_lembretes_pendentes()}


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
