"""(26/10/2026) Atendimento WhatsApp — rotas: webhook da Evolution, Inbox das atendentes, configuração.

Página: /atendimento (atendimento/static/atendimento.html) — desktop (admin) e celular (aba do app, sso + embedded=1).
Núcleo e regras do bot: core/whatsapp_motor.py · Provedor: core/whatsapp_provedor.py.

PERMISSÕES (RF16 da especificação):
  módulo `atendimento`          → entra na tela; atende os setores em whatsapp.setor_atendentes
  módulo `atendimento_gestao`   → Gerência: vê e atende TODOS os setores, configura, libera acesso entre setores
  whatsapp.acessos_setor        → liberação por pessoa × setor: 'ver' (só lê) ou 'atender', com prazo opcional
A conversa é dividida em atendimentos; cada um é de um setor. Quem não pode ver o setor recebe só a linha 🔒.
O filtro é feito AQUI (servidor) — a tela nunca recebe o que a pessoa não pode ver.
"""
import os
import json
import asyncio
import secrets
from datetime import datetime, timezone
from typing import Optional

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from core.auth import verificar_token
from core.config import JWT_SECRET
from core.permissions import tem_modulo
from core import whatsapp_motor as M
from core import whatsapp_provedor as P

router = APIRouter()
LIMITE_UPLOAD = 16 * 1024 * 1024


# ══════════════════════════════════════════════════════════════
# QUEM É O USUÁRIO NO ATENDIMENTO
# ══════════════════════════════════════════════════════════════
async def _perfil(payload):
    """{id, nome, gestao, atender:set(setor_id), ver:set(setor_id), assinaturas:{setor_id: txt}}."""
    if not await tem_modulo(payload, {"atendimento", "atendimento_gestao"}):
        raise HTTPException(status_code=403, detail="Sem acesso ao módulo Atendimento")
    await M.garantir()
    u = await M.q("SELECT id, nome, login FROM public.usuarios_garra WHERE login=%s AND ativo=true",
                          (payload.get("sub") or "",), fetch="one")
    if not u:
        raise HTTPException(status_code=401, detail="Usuário não encontrado")
    gestao = await tem_modulo(payload, {"atendimento_gestao"})
    meus = await M.q("SELECT setor_id, assinatura FROM whatsapp.setor_atendentes WHERE usuario_id=%s AND ativo",
                             (u["id"],)) or []
    acessos = await M.q(
        """SELECT setor_id, nivel FROM whatsapp.acessos_setor WHERE usuario_id=%s AND revogado_em IS NULL
           AND (valido_ate IS NULL OR valido_ate > now())""", (u["id"],)) or []
    atender = {str(r["setor_id"]) for r in meus} | {str(a["setor_id"]) for a in acessos if a["nivel"] == "atender"}
    ver = {str(a["setor_id"]) for a in acessos if a["nivel"] == "ver"}
    if gestao:
        atender |= {str(s["id"]) for s in await M.setores_ativos()}
    return {"id": str(u["id"]), "nome": u["nome"], "gestao": gestao, "atender": atender, "ver": ver - atender,
            "assinaturas": {str(r["setor_id"]): r["assinatura"] for r in meus if r.get("assinatura")}}


async def _gestao(payload):
    p = await _perfil(payload)
    if not p["gestao"]:
        raise HTTPException(status_code=403, detail="Só a Gerência configura o atendimento")
    return p


def _pode_ver_setor(p, setor_id):
    return p["gestao"] or str(setor_id) in p["atender"] or str(setor_id) in p["ver"]


def _pode_atender(p, at):
    return at and at["status"] != "encerrado" and (p["gestao"] or str(at["setor_id"]) in p["atender"])


async def _limite_participacao(p, at_id):
    """Quem transferiu para fora do próprio setor continua lendo o que escreveu até a transferência."""
    r = await M.q(
        """SELECT max(criado_em) AS ate FROM whatsapp.atendimento_eventos
           WHERE atendimento_id=%s AND tipo='transferido' AND de_usuario_id=%s""", (at_id, p["id"]), fetch="one")
    return (r or {}).get("ate")


async def _at(at_id):
    at = await M.q(
        """SELECT a.*, s.nome AS setor_nome, s.nome_msg, s.restrito, u.nome AS atendente_nome
           FROM whatsapp.atendimentos a JOIN whatsapp.setores s ON s.id=a.setor_id
           LEFT JOIN public.usuarios_garra u ON u.id=a.atribuido_a WHERE a.id=%s""", (at_id,), fetch="one")
    if not at:
        raise HTTPException(status_code=404, detail="Atendimento não encontrado")
    return at


def _assinatura(p, setor_id):
    return p["assinaturas"].get(str(setor_id)) or next(iter(p["assinaturas"].values()), None) or p["nome"]


def _iso(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _limpo(row):
    return {k: (str(v) if k.endswith("_id") or k == "id" else _iso(v)) if v is not None else None for k, v in row.items()}


# ══════════════════════════════════════════════════════════════
# PÁGINA
# ══════════════════════════════════════════════════════════════
@router.get("/atendimento", response_class=HTMLResponse)
async def pagina_atendimento():
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "atendimento"))
    p = os.path.join(raiz, "static", "atendimento.html")
    if os.path.isfile(p):
        return HTMLResponse(open(p, encoding="utf-8").read(),
                            headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})
    raise HTTPException(status_code=404, detail="atendimento.html não encontrado")


# ══════════════════════════════════════════════════════════════
# WEBHOOK (Evolution → sistema)
# ══════════════════════════════════════════════════════════════
@router.post("/whatsapp/api/webhook/{token}")
async def webhook(token: str, request: Request, bg: BackgroundTasks):
    if not P.WEBHOOK_TOKEN or not secrets.compare_digest(token, P.WEBHOOK_TOKEN):
        raise HTTPException(status_code=401, detail="Token inválido")
    try:
        corpo = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")
    bg.add_task(M.processar_webhook, corpo)      # responde já; o motor roda depois
    return {"ok": True}


# ══════════════════════════════════════════════════════════════
# INBOX
# ══════════════════════════════════════════════════════════════
@router.get("/whatsapp/api/eu")
async def eu(payload=Depends(verificar_token)):
    p = await _perfil(payload)
    setores = await M.setores_ativos()
    return {"id": p["id"], "nome": p["nome"], "gestao": p["gestao"], "simulacao": P.simulacao(),
            "setores": [{"id": str(s["id"]), "nome": s["nome"], "restrito": s["restrito"],
                         "nivel": "atender" if str(s["id"]) in p["atender"] else ("ver" if str(s["id"]) in p["ver"] else None)}
                        for s in setores]}


_SQL_LISTA = """
SELECT a.id, a.numero, a.telefone, a.setor_id, a.status, a.atribuido_a, a.ultima_atividade_em, a.aberto_em,
       a.motivo_pendencia, s.nome AS setor_nome, s.codigo AS setor_codigo, u.nome AS atendente_nome,
       COALESCE(uc.nome, c.nome_whatsapp, a.telefone) AS contato_nome,
       (SELECT json_build_object('tipo', m.tipo, 'texto', left(m.texto, 120), 'origem', m.origem, 'em', m.criado_em)
          FROM whatsapp.mensagens m WHERE m.atendimento_id=a.id ORDER BY m.criado_em DESC LIMIT 1) AS ultima,
       (SELECT count(*) FROM whatsapp.mensagens m WHERE m.atendimento_id=a.id AND m.direcao='in' AND m.lida_em IS NULL)::int AS nao_lidas
FROM whatsapp.atendimentos a
JOIN whatsapp.setores s ON s.id=a.setor_id
LEFT JOIN whatsapp.contatos c ON c.telefone=a.telefone
LEFT JOIN public.usuarios_garra uc ON uc.id=c.usuario_id
LEFT JOIN public.usuarios_garra u ON u.id=a.atribuido_a
WHERE (%s OR a.setor_id::text = ANY(%s)
       OR EXISTS (SELECT 1 FROM whatsapp.atendimento_eventos e WHERE e.atendimento_id=a.id AND e.de_usuario_id::text=%s))
"""


@router.get("/whatsapp/api/conversas")
async def conversas(filtro: str = "minhas", q: str = "", setor: str = "", payload=Depends(verificar_token)):
    p = await _perfil(payload)
    visiveis = list(p["atender"] | p["ver"])
    sql, args = _SQL_LISTA, [p["gestao"], visiveis, p["id"]]
    if filtro == "minhas":
        sql += " AND a.status<>'encerrado' AND a.atribuido_a::text=%s"; args.append(p["id"])
    elif filtro == "setor":
        sql += " AND a.status<>'encerrado' AND a.setor_id::text = ANY(%s)"; args.append([setor] if setor else visiveis)
    elif filtro == "pend":
        sql += " AND a.status='pendente'"
    elif filtro == "enc":
        sql += " AND a.status='encerrado' AND a.encerrado_em > now() - interval '30 days'"
    else:
        sql += " AND a.status<>'encerrado'"
    if q.strip():
        sql += " AND (COALESCE(uc.nome, c.nome_whatsapp, '') ILIKE %s OR a.telefone LIKE %s)"
        args += [f"%{q.strip()}%", f"%{''.join(ch for ch in q if ch.isdigit()) or q.strip()}%"]
    sql += " ORDER BY a.ultima_atividade_em DESC LIMIT 150"
    rows = await M.q(sql, args) or []
    itens, vistos = [], set()
    for r in rows:
        if filtro == "enc":
            if r["telefone"] in vistos:
                continue
            vistos.add(r["telefone"])
        d = _limpo({k: v for k, v in r.items() if k != "ultima"})
        d["ultima"] = json.loads(r["ultima"]) if isinstance(r.get("ultima"), str) else r.get("ultima")
        d["pode_atender"] = p["gestao"] or str(r["setor_id"]) in p["atender"]
        d["so_leitura_participou"] = not _pode_ver_setor(p, r["setor_id"])
        itens.append(d)
    cont = await M.q(
        """SELECT
             count(*) FILTER (WHERE a.status<>'encerrado' AND a.atribuido_a::text=%s)::int AS minhas,
             count(*) FILTER (WHERE a.status<>'encerrado' AND (%s OR a.setor_id::text = ANY(%s)))::int AS todas,
             count(*) FILTER (WHERE a.status='pendente' AND (%s OR a.setor_id::text = ANY(%s)))::int AS pend
           FROM whatsapp.atendimentos a WHERE a.status<>'encerrado'""",
        (p["id"], p["gestao"], visiveis, p["gestao"], visiveis), fetch="one") or {}
    sem = 0
    if p["gestao"]:
        r = await M.q(
            """SELECT count(DISTINCT m.telefone)::int AS n FROM whatsapp.mensagens m
               WHERE m.atendimento_id IS NULL AND m.direcao='in' AND m.criado_em > now() - interval '3 days'
               AND NOT EXISTS (SELECT 1 FROM whatsapp.atendimentos a WHERE a.telefone=m.telefone AND a.status<>'encerrado')
               AND NOT EXISTS (SELECT 1 FROM whatsapp.atendimentos a WHERE a.telefone=m.telefone AND a.aberto_em > m.criado_em)""",
            fetch="one")
        sem = (r or {}).get("n") or 0
    return {"itens": itens, "contadores": dict(cont, sem_setor=sem)}


@router.get("/whatsapp/api/sem-setor")
async def sem_setor(payload=Depends(verificar_token)):
    """Gerência: quem escreveu e não escolheu setor (últimos 3 dias)."""
    await _gestao(payload)
    rows = await M.q(
        """SELECT m.telefone, max(m.criado_em) AS em, COALESCE(max(uc.nome), max(c.nome_whatsapp), m.telefone) AS contato_nome,
                  (array_agg(left(m.texto,120) ORDER BY m.criado_em DESC))[1] AS texto,
                  (array_agg(m.tipo ORDER BY m.criado_em DESC))[1] AS tipo
           FROM whatsapp.mensagens m LEFT JOIN whatsapp.contatos c ON c.telefone=m.telefone
           LEFT JOIN public.usuarios_garra uc ON uc.id=c.usuario_id
           WHERE m.atendimento_id IS NULL AND m.direcao='in' AND m.criado_em > now() - interval '3 days'
           AND NOT EXISTS (SELECT 1 FROM whatsapp.atendimentos a WHERE a.telefone=m.telefone AND a.status<>'encerrado')
           AND NOT EXISTS (SELECT 1 FROM whatsapp.atendimentos a WHERE a.telefone=m.telefone AND a.aberto_em > m.criado_em)
           GROUP BY m.telefone ORDER BY max(m.criado_em) DESC""") or []
    return [_limpo(r) for r in rows]


@router.get("/whatsapp/api/atendimento/{at_id}")
async def atendimento_por_id(at_id: str, payload=Depends(verificar_token)):
    """Link de notificação (/atendimento?at=ID) → telefone da conversa."""
    p = await _perfil(payload)
    at = await _at(at_id)
    if not _pode_ver_setor(p, at["setor_id"]) and not await _limite_participacao(p, at["id"]):
        raise HTTPException(status_code=403, detail="Sem acesso a este atendimento")
    return {"telefone": at["telefone"], "status": at["status"]}


@router.get("/whatsapp/api/conversa/{telefone}")
async def conversa(telefone: str, desde: Optional[str] = None, payload=Depends(verificar_token)):
    """Fio do contato: mensagens dos atendimentos que a pessoa pode ver + linha 🔒 dos outros.
    `desde` (ISO do maior atualizado_em já recebido) devolve só o que mudou — a tela consulta a cada 4 s."""
    p = await _perfil(payload)
    c = await M.q(
        """SELECT c.telefone, c.usuario_id, COALESCE(u.nome, c.nome_whatsapp, c.telefone) AS nome, c.nome_whatsapp
           FROM whatsapp.contatos c LEFT JOIN public.usuarios_garra u ON u.id=c.usuario_id WHERE c.telefone=%s""",
        (telefone,), fetch="one")
    if not c:
        raise HTTPException(status_code=404, detail="Contato não encontrado")
    ats = await M.q(
        """SELECT a.*, s.nome AS setor_nome, s.restrito, u.nome AS atendente_nome, ue.nome AS encerrado_por_nome
           FROM whatsapp.atendimentos a JOIN whatsapp.setores s ON s.id=a.setor_id
           LEFT JOIN public.usuarios_garra u ON u.id=a.atribuido_a LEFT JOIN public.usuarios_garra ue ON ue.id=a.encerrado_por
           WHERE a.telefone=%s AND a.aberto_em > now() - interval '120 days' ORDER BY a.aberto_em""", (telefone,)) or []
    janelas, bloqueados = {}, []
    for a in ats:
        if _pode_ver_setor(p, a["setor_id"]):
            janelas[str(a["id"])] = None
        else:
            ate = await _limite_participacao(p, a["id"])
            if ate:
                janelas[str(a["id"])] = ate
            else:
                bloqueados.append({"id": str(a["id"]), "setor_nome": a["setor_nome"], "aberto_em": _iso(a["aberto_em"])})
    if not janelas and not p["gestao"]:
        raise HTTPException(status_code=403, detail="Você não tem acesso a esta conversa")
    args = [telefone]
    sql = """SELECT m.*, u.nome AS usuario_nome FROM whatsapp.mensagens m LEFT JOIN public.usuarios_garra u ON u.id=m.usuario_id
             WHERE m.telefone=%s AND m.criado_em > now() - interval '120 days'"""
    if desde:
        sql += " AND m.atualizado_em > %s"; args.append(datetime.fromisoformat(desde.replace("Z", "+00:00")))
    sql += " ORDER BY m.criado_em LIMIT 600"
    msgs = []
    for m in await M.q(sql, args) or []:
        aid = str(m["atendimento_id"]) if m["atendimento_id"] else None
        if aid is None:
            if not p["gestao"]:
                continue
        elif aid not in janelas:
            continue
        elif janelas[aid] is not None and m["criado_em"] > janelas[aid]:
            continue
        d = _limpo(m)
        d.pop("midia_caminho", None)
        d["tem_midia"] = bool(m.get("midia_caminho"))
        msgs.append(d)
    # (26/10/2026) vários abertos (um por setor): padrão = o meu > um que eu atendo > o primeiro que eu vejo
    abertos = [dict(_limpo({k: a[k] for k in ("id", "numero", "setor_id", "setor_nome", "status", "atribuido_a",
                                              "atendente_nome", "motivo_pendencia")}),
                    pode_atender=_pode_atender(p, a), visivel=str(a["id"]) in janelas)
               for a in sorted((a for a in ats if a["status"] != "encerrado"), key=lambda a: a["ultima_atividade_em"], reverse=True)]
    aberto = (next((a for a in abertos if a["pode_atender"] and str(a["atribuido_a"]) == p["id"]), None)
              or next((a for a in abertos if a["pode_atender"]), None)
              or next((a for a in abertos if a["visivel"]), None))
    eventos = await M.q(
        """SELECT e.atendimento_id, e.tipo, e.criado_em, e.observacao, ud.nome AS de_nome, up.nome AS para_nome,
                  sp.nome AS para_setor
           FROM whatsapp.atendimento_eventos e LEFT JOIN public.usuarios_garra ud ON ud.id=e.de_usuario_id
           LEFT JOIN public.usuarios_garra up ON up.id=e.para_usuario_id LEFT JOIN whatsapp.setores sp ON sp.id=e.para_setor_id
           WHERE e.atendimento_id::text = ANY(%s) ORDER BY e.criado_em""", (list(janelas.keys()) or ["-"],)) or []
    return {
        "contato": _limpo(c),
        "atendimentos": [dict(_limpo({k: a[k] for k in ("id", "numero", "setor_id", "setor_nome", "status", "atribuido_a",
                                                        "atendente_nome", "aberto_em", "encerrado_em", "encerrado_por_nome",
                                                        "motivo_pendencia", "restrito")}),
                              visivel=str(a["id"]) in janelas) for a in ats],
        "bloqueados": bloqueados,
        "aberto": aberto,
        "abertos": abertos,
        "mensagens": msgs,
        "eventos": [_limpo(e) for e in eventos],
        "agora": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/whatsapp/api/conversa/{telefone}/lida")
async def marcar_lida(telefone: str, payload=Depends(verificar_token)):
    p = await _perfil(payload)
    meus = [str(a["id"]) for a in await M.atendimentos_abertos(telefone) if _pode_atender(p, a)]
    if not meus:
        return {"ok": True}
    rows = await M.q(
        """UPDATE whatsapp.mensagens SET lida_em=now(), atualizado_em=now()
           WHERE atendimento_id::text = ANY(%s) AND direcao='in' AND lida_em IS NULL RETURNING provider_msg_id""", (meus,)) or []
    asyncio.create_task(P.marcar_lidas(telefone, [r["provider_msg_id"] for r in rows if r["provider_msg_id"]]))
    return {"ok": True}


@router.post("/whatsapp/api/conversa/{telefone}/digitando")
async def digitando(telefone: str, payload=Depends(verificar_token)):
    await _perfil(payload)
    asyncio.create_task(P.digitando(telefone))
    return {"ok": True}


async def _aberto_para_atender(p, telefone, at_id=None):
    """Atendimento aberto em que a pessoa vai agir. Com vários abertos (um por setor), a tela manda `at_id`;
    sem ele vale o que está com ela ou o único que ela atende."""
    if at_id:
        at = await M.atendimento_por_id(at_id)
        if not at or at["telefone"] != telefone or at["status"] == "encerrado":
            raise HTTPException(status_code=409, detail="Este atendimento já foi encerrado")
        if not _pode_atender(p, at):
            raise HTTPException(status_code=403, detail=f"Atendimento do setor {at['setor_nome']} — você não pode responder")
        return at
    abertos = await M.atendimentos_abertos(telefone)
    if not abertos:
        raise HTTPException(status_code=409, detail="Não há atendimento aberto com este contato")
    meus = [a for a in abertos if _pode_atender(p, a)]
    if not meus:
        raise HTTPException(status_code=403, detail=f"Atendimento do setor {abertos[0]['setor_nome']} — você não pode responder")
    comigo = [a for a in meus if str(a.get("atribuido_a")) == p["id"]]
    if len(comigo) == 1 or len(meus) == 1:
        return (comigo or meus)[0]
    raise HTTPException(status_code=409, detail="Há mais de um atendimento aberto — escolha em qual responder")


async def _primeira_resposta(p, at):
    await M.q(
        """UPDATE whatsapp.atendimentos SET status=CASE WHEN status='aberto' THEN 'em_atendimento' ELSE status END,
               primeira_resposta_em=COALESCE(primeira_resposta_em, now()),
               atribuido_a=COALESCE(atribuido_a, %s) WHERE id=%s""", (p["id"], at["id"]), fetch="none")


@router.post("/whatsapp/api/conversa/{telefone}/enviar")
async def enviar(telefone: str, request: Request, payload=Depends(verificar_token)):
    p = await _perfil(payload)
    d = await request.json()
    texto = (d.get("texto") or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Mensagem vazia")
    at = await _aberto_para_atender(p, telefone, d.get("at_id"))
    ass = _assinatura(p, at["setor_id"])
    citado = None
    if d.get("citado_id"):
        cm = await M.q("SELECT provider_msg_id, direcao, texto FROM whatsapp.mensagens WHERE id=%s AND telefone=%s",
                               (d["citado_id"], telefone), fetch="one")
        if cm and cm["provider_msg_id"]:
            citado = {"id": cm["provider_msg_id"], "de_mim": cm["direcao"] == "out", "texto": cm["texto"] or ""}
    try:
        pid = await P.enviar_texto(telefone, f"*{ass}:*\n{texto}", citado)
        status, erro = "enviada", None
    except Exception as e:
        pid, status, erro = None, "erro", str(e)[:300]
    row = await M.registrar(telefone, "out", "atendente", texto=texto, provider_msg_id=pid, atendimento_id=at["id"],
                            usuario_id=p["id"], assinatura=ass, citado_provider_id=(citado or {}).get("id"),
                            status=status, erro=erro)
    await _primeira_resposta(p, at)
    await M.marcar_ativo(telefone, at["id"])        # a próxima mensagem do colaborador volta para quem respondeu
    if erro:
        raise HTTPException(status_code=502, detail=f"Não saiu para o WhatsApp: {erro}")
    return _limpo(row)


@router.post("/whatsapp/api/conversa/{telefone}/midia")
async def enviar_midia(telefone: str, arquivo: UploadFile = File(...), legenda: str = Form(""), voz: str = Form("0"),
                       at_id: str = Form(""), payload=Depends(verificar_token)):
    p = await _perfil(payload)
    at = await _aberto_para_atender(p, telefone, at_id or None)
    dados = await arquivo.read()
    if len(dados) > LIMITE_UPLOAD:
        raise HTTPException(status_code=413, detail="Arquivo maior que 16 MB")
    mime = (arquivo.content_type or "application/octet-stream").split(";")[0]
    tipo = "audio" if voz == "1" else ("imagem" if mime.startswith("image/") else "video" if mime.startswith("video/")
                                       else "audio" if mime.startswith("audio/") else "documento")
    ass = _assinatura(p, at["setor_id"])
    row = await M.registrar(telefone, "out", "atendente", tipo=tipo, texto=legenda.strip() or None, atendimento_id=at["id"],
                            usuario_id=p["id"], assinatura=ass, mimetype=mime, nome_arquivo=arquivo.filename,
                            status="pendente")
    try:
        from core.storage import storage_upload
        caminho = f"whatsapp/{M.agora().astimezone(M.FUSO):%Y-%m}/{telefone}/{row['id']}.{M.extensao(mime, arquivo.filename)}"
        salvo = await asyncio.to_thread(storage_upload, dados, caminho, mime)
    except Exception as e:
        salvo = None
        M.log.warning("upload atendimento: %s", e)
    leg = f"*{ass}:*\n{legenda.strip()}" if legenda.strip() else (f"*{ass}*" if tipo in ("imagem", "video", "documento") else None)
    try:
        pid = await P.enviar_midia(telefone, tipo, dados, mime, arquivo.filename, leg)
        status, erro = "enviada", None
    except Exception as e:
        pid, status, erro = None, "erro", str(e)[:300]
    await M.q("""UPDATE whatsapp.mensagens SET provider_msg_id=%s, midia_caminho=%s, status=%s, erro=%s, atualizado_em=now()
                         WHERE id=%s""", (pid, salvo, status, erro, row["id"]), fetch="none")
    await _primeira_resposta(p, at)
    await M.marcar_ativo(telefone, at["id"])
    if erro:
        raise HTTPException(status_code=502, detail=f"Não saiu para o WhatsApp: {erro}")
    return {"ok": True, "id": str(row["id"])}


@router.post("/whatsapp/api/mensagem/{msg_id}/reagir")
async def reagir(msg_id: str, request: Request, payload=Depends(verificar_token)):
    p = await _perfil(payload)
    d = await request.json()
    m = await M.q("SELECT * FROM whatsapp.mensagens WHERE id=%s", (msg_id,), fetch="one")
    if not m or not m["provider_msg_id"]:
        raise HTTPException(status_code=404, detail="Mensagem não encontrada")
    alvo = m["atendimento_id"] if m["atendimento_id"] and \
        (await M.q("SELECT 1 FROM whatsapp.atendimentos WHERE id=%s AND status<>'encerrado'", (m["atendimento_id"],), fetch="one")) else d.get("at_id")
    await _aberto_para_atender(p, m["telefone"], alvo)
    emoji = (d.get("emoji") or "")[:8]
    await P.reagir(m["telefone"], m["provider_msg_id"], m["direcao"] == "out", emoji)
    await M.q("UPDATE whatsapp.mensagens SET reacao=%s, atualizado_em=now() WHERE id=%s", (emoji or None, msg_id), fetch="none")
    return {"ok": True}


@router.get("/whatsapp/api/midia/{msg_id}")
async def midia(msg_id: str, t: str = ""):
    """Abre a mídia (img/audio/video usam src= sem cabeçalho — o token vem em ?t=). Link temporário de 10 min."""
    try:
        payload = pyjwt.decode(t, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        raise HTTPException(status_code=401, detail="Sessão expirada")
    p = await _perfil(payload)
    m = await M.q("SELECT telefone, atendimento_id, midia_caminho, criado_em FROM whatsapp.mensagens WHERE id=%s",
                          (msg_id,), fetch="one")
    if not m or not m["midia_caminho"]:
        raise HTTPException(status_code=404, detail="Mídia ainda não disponível")
    if m["atendimento_id"]:
        at = await _at(m["atendimento_id"])
        if not _pode_ver_setor(p, at["setor_id"]):
            ate = await _limite_participacao(p, at["id"])
            if not ate or m["criado_em"] > ate:
                raise HTTPException(status_code=403, detail="Sem acesso")
    elif not p["gestao"]:
        raise HTTPException(status_code=403, detail="Sem acesso")
    from core.storage import storage_url
    url = await asyncio.to_thread(storage_url, m["midia_caminho"], 600)
    if not url:
        raise HTTPException(status_code=404, detail="Arquivo indisponível")
    return RedirectResponse(url)


# ── ações do atendimento ──
@router.post("/whatsapp/api/conversa/{telefone}/transferir")
async def transferir(telefone: str, request: Request, payload=Depends(verificar_token)):
    p = await _perfil(payload)
    d = await request.json()
    at = await _aberto_para_atender(p, telefone, d.get("at_id"))
    s = await M.setor(d.get("setor_id"))
    if not s or not s["ativo"]:
        raise HTTPException(status_code=400, detail="Setor inválido")
    destino = d.get("usuario_id") or None
    if destino:
        ok = await M.q("SELECT 1 FROM whatsapp.setor_atendentes WHERE setor_id=%s AND usuario_id=%s AND ativo",
                               (s["id"], destino), fetch="one")
        if not ok and destino != p["id"]:
            raise HTTPException(status_code=400, detail="Essa pessoa não atende esse setor")
    await M.transferir(at, s["id"], destino, p["id"], bool(d.get("silencioso")))
    return {"ok": True}


@router.post("/whatsapp/api/conversa/{telefone}/assumir")
async def assumir(telefone: str, request: Request, payload=Depends(verificar_token)):
    """Gerência (ou quem atende o setor) puxa a conversa para si, sem mudar o setor."""
    p = await _perfil(payload)
    d = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    at = await _aberto_para_atender(p, telefone, d.get("at_id"))
    await M.q("UPDATE whatsapp.atendimentos SET atribuido_a=%s WHERE id=%s", (p["id"], at["id"]), fetch="none")
    await M.evento(at["id"], "assumido", de_usuario_id=at.get("atribuido_a"), para_usuario_id=p["id"])
    return {"ok": True}


@router.post("/whatsapp/api/conversa/{telefone}/encerrar")
async def encerrar(telefone: str, request: Request, payload=Depends(verificar_token)):
    p = await _perfil(payload)
    d = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    at = await _aberto_para_atender(p, telefone, d.get("at_id"))
    await M.encerrar(at, p["id"], avisar_colaborador=d.get("avisar", True) is not False)
    return {"ok": True}


@router.post("/whatsapp/api/conversa/{telefone}/pendente")
async def pendente(telefone: str, request: Request, payload=Depends(verificar_token)):
    p = await _perfil(payload)
    d = await request.json() if (request.headers.get("content-length") or "0") != "0" else {}
    at = await _aberto_para_atender(p, telefone, d.get("at_id"))
    motivo = (d.get("motivo") or "Aguardando o colaborador").strip()[:200]
    await M.q("UPDATE whatsapp.atendimentos SET status='pendente', motivo_pendencia=%s WHERE id=%s",
                      (motivo, at["id"]), fetch="none")
    await M.evento(at["id"], "pendente", de_usuario_id=p["id"], observacao=motivo)
    return {"ok": True}


@router.post("/whatsapp/api/conversas/nova")
async def nova_conversa(request: Request, payload=Depends(verificar_token)):
    """Atendente inicia a conversa (substitui os fluxos .XX INICIAR CONVERSA do BotConversa)."""
    p = await _perfil(payload)
    d = await request.json()
    tel = "".join(ch for ch in str(d.get("telefone") or "") if ch.isdigit())
    if len(tel) in (10, 11):
        tel = "55" + tel
    if len(tel) < 12:
        raise HTTPException(status_code=400, detail="Telefone inválido — use DDD + número")
    setor_id = d.get("setor_id")
    if not setor_id or (not p["gestao"] and str(setor_id) not in p["atender"]):
        raise HTTPException(status_code=403, detail="Escolha um setor que você atende")
    at = next((a for a in await M.atendimentos_abertos(tel) if str(a["setor_id"]) == str(setor_id)), None)
    if at:
        raise HTTPException(status_code=409, detail=f"Já existe atendimento aberto com este contato em {at['setor_nome']}"
                                                    + (f" ({M.primeiro_nome(at['atendente_nome'])})" if at.get("atendente_nome") else ""))
    c = await M.contato(tel)
    at = await M.abrir_atendimento(tel, setor_id, aberto_por="atendente", usuario_id=p["id"], anexar_desde_min=0)
    if d.get("saudar", True) is not False:
        nome = M.primeiro_nome(c["nome"]) if c["nome"] != tel else ""
        texto = M.preencher(await M.par("iniciar_conversa"), nome=nome, atendente=M.primeiro_nome(p["nome"]))
        await M.enviar_bot(tel, texto, at["id"])
    return {"ok": True, "telefone": tel}


@router.post("/whatsapp/api/conversa/{telefone}/abrir")
async def abrir_de_sem_setor(telefone: str, request: Request, payload=Depends(verificar_token)):
    """Gerência coloca num setor quem escreveu e não escolheu (lista Sem setor)."""
    await _gestao(payload)
    d = await request.json()
    if any(str(a["setor_id"]) == str(d.get("setor_id")) for a in await M.atendimentos_abertos(telefone)):
        raise HTTPException(status_code=409, detail="Já há atendimento aberto neste setor")
    await M.abrir_atendimento(telefone, d.get("setor_id"), aberto_por="gestao", anexar_desde_min=3 * 24 * 60)
    return {"ok": True}


@router.get("/whatsapp/api/respostas")
async def respostas(payload=Depends(verificar_token)):
    await _perfil(payload)
    rows = await M.q("SELECT id, atalho, texto, setor_id FROM whatsapp.respostas_rapidas WHERE deletado_em IS NULL ORDER BY ordem, atalho") or []
    return [_limpo(r) for r in rows]


@router.get("/whatsapp/api/contatos")
async def contatos(q: str = "", payload=Depends(verificar_token)):
    await _perfil(payload)
    qq = f"%{q.strip()}%"
    rows = await M.q(
        """SELECT c.telefone, COALESCE(u.nome, c.nome_whatsapp, c.telefone) AS nome, c.usuario_id
           FROM whatsapp.contatos c LEFT JOIN public.usuarios_garra u ON u.id=c.usuario_id
           WHERE COALESCE(u.nome, c.nome_whatsapp, '') ILIKE %s OR c.telefone LIKE %s ORDER BY 2 LIMIT 30""", (qq, qq)) or []
    return [_limpo(r) for r in rows]


# ══════════════════════════════════════════════════════════════
# CONFIGURAÇÃO (Gerência)
# ══════════════════════════════════════════════════════════════
@router.get("/whatsapp/api/config")
async def config(payload=Depends(verificar_token)):
    await _gestao(payload)
    setores = await M.setores_ativos()
    atend = await M.q(
        """SELECT sa.setor_id, sa.usuario_id, sa.assinatura, u.nome FROM whatsapp.setor_atendentes sa
           JOIN public.usuarios_garra u ON u.id=sa.usuario_id WHERE sa.ativo ORDER BY u.nome""") or []
    menu = await M.menu_principal()
    acessos = await M.q(
        """SELECT a.id, a.usuario_id, u.nome AS usuario_nome, a.setor_id, s.nome AS setor_nome, a.nivel, a.valido_ate,
                  a.motivo, a.concedido_em, uc.nome AS concedido_por_nome,
                  (a.valido_ate IS NOT NULL AND a.valido_ate <= now()) AS expirado
           FROM whatsapp.acessos_setor a JOIN public.usuarios_garra u ON u.id=a.usuario_id
           JOIN whatsapp.setores s ON s.id=a.setor_id LEFT JOIN public.usuarios_garra uc ON uc.id=a.concedido_por
           WHERE a.revogado_em IS NULL ORDER BY a.concedido_em DESC""") or []
    usuarios = await M.q("SELECT id, nome, login FROM public.usuarios_garra WHERE ativo=true ORDER BY nome") or []
    resp = await M.q("SELECT id, atalho, texto, setor_id, ordem FROM whatsapp.respostas_rapidas WHERE deletado_em IS NULL ORDER BY ordem, atalho") or []
    par = await M.parametros(forcar=True)
    return {
        "setores": [dict(_limpo(s), atendentes=[_limpo(a) for a in atend if a["setor_id"] == s["id"]]) for s in setores],
        "opcoes": [_limpo(o) for o in (await M.opcoes(menu["id"]) if menu else [])],
        "acessos": [_limpo(a) for a in acessos],
        "usuarios": [_limpo(u) for u in usuarios],
        "respostas": [_limpo(r) for r in resp],
        "parametros": {k: par.get(k, v) for k, v in M.PARAMETROS_PADRAO.items()},
        "simulacao": P.simulacao(),
    }


@router.put("/whatsapp/api/config/parametros")
async def config_parametros(request: Request, payload=Depends(verificar_token)):
    await _gestao(payload)
    d = await request.json()
    for k, v in (d or {}).items():
        if k not in M.PARAMETROS_PADRAO:
            continue
        if k == "horario":
            try:
                json.loads(v)
            except Exception:
                raise HTTPException(status_code=400, detail="Horário inválido")
        await M.q("""INSERT INTO whatsapp.parametros (chave, valor, atualizado_em) VALUES (%s,%s,now())
                             ON CONFLICT (chave) DO UPDATE SET valor=EXCLUDED.valor, atualizado_em=now()""", (k, str(v)), fetch="none")
    M.limpar_cache()
    return {"ok": True}


@router.post("/whatsapp/api/config/setores")
async def config_setor(request: Request, payload=Depends(verificar_token)):
    await _gestao(payload)
    d = await request.json()
    nome = (d.get("nome") or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Nome obrigatório")
    vals = (nome, (d.get("nome_msg") or nome).strip().upper(), bool(d.get("restrito")), d.get("responsavel_id") or None,
            int(d.get("ordem") or 0), d.get("ativo", True) is not False)
    if d.get("id"):
        await M.q("""UPDATE whatsapp.setores SET nome=%s, nome_msg=%s, restrito=%s, responsavel_id=%s, ordem=%s, ativo=%s
                             WHERE id=%s""", (*vals, d["id"]), fetch="none")
        return {"ok": True, "id": d["id"]}
    codigo = M.sem_acento(nome).replace(" ", "_")[:30] + "_" + secrets.token_hex(2)
    r = await M.q("""INSERT INTO whatsapp.setores (nome, nome_msg, restrito, responsavel_id, ordem, ativo, codigo)
                             VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""", (*vals, codigo), fetch="one")
    return {"ok": True, "id": str(r["id"])}


@router.put("/whatsapp/api/config/setores/{setor_id}/atendentes")
async def config_atendentes(setor_id: str, request: Request, payload=Depends(verificar_token)):
    """Lista completa de quem atende o setor: [{usuario_id, assinatura}]. Quem saiu da lista é desativado."""
    await _gestao(payload)
    lista = await request.json()
    ids = []
    for a in lista or []:
        if not a.get("usuario_id"):
            continue
        ids.append(str(a["usuario_id"]))
        await M.q("""INSERT INTO whatsapp.setor_atendentes (setor_id, usuario_id, assinatura, ativo) VALUES (%s,%s,%s,true)
                             ON CONFLICT (setor_id, usuario_id) DO UPDATE SET assinatura=EXCLUDED.assinatura, ativo=true""",
                          (setor_id, a["usuario_id"], (a.get("assinatura") or "").strip() or None), fetch="none")
    await M.q("UPDATE whatsapp.setor_atendentes SET ativo=false WHERE setor_id=%s AND NOT (usuario_id::text = ANY(%s))",
                      (setor_id, ids or ["-"]), fetch="none")
    return {"ok": True}


@router.post("/whatsapp/api/config/opcoes")
async def config_opcao(request: Request, payload=Depends(verificar_token)):
    await _gestao(payload)
    d = await request.json()
    menu = await M.menu_principal()
    tipo = d.get("tipo")
    if tipo not in ("texto", "setor"):
        raise HTTPException(status_code=400, detail="Tipo deve ser texto ou setor")
    if tipo == "setor" and not d.get("setor_id"):
        raise HTTPException(status_code=400, detail="Escolha o setor da opção")
    vals = (int(d.get("numero") or 0), (d.get("rotulo") or "").strip(), tipo, (d.get("resposta") or None),
            d.get("setor_id") if tipo == "setor" else None)
    if vals[0] < 1 or not vals[1]:
        raise HTTPException(status_code=400, detail="Número e texto da opção são obrigatórios")
    try:
        if d.get("id"):
            await M.q("UPDATE whatsapp.opcoes SET numero=%s, rotulo=%s, tipo=%s, resposta=%s, setor_id=%s WHERE id=%s",
                              (*vals, d["id"]), fetch="none")
        else:
            await M.q("INSERT INTO whatsapp.opcoes (numero, rotulo, tipo, resposta, setor_id, menu_id) VALUES (%s,%s,%s,%s,%s,%s)",
                              (*vals, menu["id"]), fetch="none")
    except Exception as e:
        if "ux_wa_opcao_numero" in str(e):
            raise HTTPException(status_code=409, detail=f"Já existe a opção {vals[0]}")
        raise
    return {"ok": True}


@router.delete("/whatsapp/api/config/opcoes/{opcao_id}")
async def config_opcao_excluir(opcao_id: str, payload=Depends(verificar_token)):
    await _gestao(payload)
    await M.q("UPDATE whatsapp.opcoes SET deletado_em=now() WHERE id=%s", (opcao_id,), fetch="none")
    return {"ok": True}


@router.post("/whatsapp/api/config/respostas")
async def config_resposta(request: Request, payload=Depends(verificar_token)):
    await _gestao(payload)
    d = await request.json()
    if not (d.get("atalho") or "").strip() or not (d.get("texto") or "").strip():
        raise HTTPException(status_code=400, detail="Atalho e texto obrigatórios")
    if d.get("id"):
        await M.q("UPDATE whatsapp.respostas_rapidas SET atalho=%s, texto=%s WHERE id=%s",
                          (d["atalho"].strip(), d["texto"].strip(), d["id"]), fetch="none")
    else:
        await M.q("INSERT INTO whatsapp.respostas_rapidas (atalho, texto, ordem) VALUES (%s,%s,99)",
                          (d["atalho"].strip(), d["texto"].strip()), fetch="none")
    return {"ok": True}


@router.delete("/whatsapp/api/config/respostas/{resp_id}")
async def config_resposta_excluir(resp_id: str, payload=Depends(verificar_token)):
    await _gestao(payload)
    await M.q("UPDATE whatsapp.respostas_rapidas SET deletado_em=now() WHERE id=%s", (resp_id,), fetch="none")
    return {"ok": True}


@router.post("/whatsapp/api/config/acessos")
async def config_acesso(request: Request, payload=Depends(verificar_token)):
    """Libera um setor para uma pessoa: nível ver|atender, prazo opcional. Setor restrito exige motivo."""
    p = await _gestao(payload)
    d = await request.json()
    s = await M.setor(d.get("setor_id"))
    if not s or not d.get("usuario_id") or d.get("nivel") not in ("ver", "atender"):
        raise HTTPException(status_code=400, detail="Pessoa, setor e nível são obrigatórios")
    motivo = (d.get("motivo") or "").strip()
    if s["restrito"] and not motivo:
        raise HTTPException(status_code=400, detail=f"{s['nome']} é restrito — informe o motivo da liberação")
    valido = None
    if d.get("valido_ate"):
        valido = datetime.fromisoformat(d["valido_ate"]).replace(hour=23, minute=59, tzinfo=M.FUSO)
    await M.q("""UPDATE whatsapp.acessos_setor SET revogado_em=now(), revogado_por=%s
                         WHERE usuario_id=%s AND setor_id=%s AND revogado_em IS NULL""", (p["id"], d["usuario_id"], s["id"]), fetch="none")
    await M.q("""INSERT INTO whatsapp.acessos_setor (usuario_id, setor_id, nivel, valido_ate, motivo, concedido_por)
                         VALUES (%s,%s,%s,%s,%s,%s)""", (d["usuario_id"], s["id"], d["nivel"], valido, motivo or None, p["id"]), fetch="none")
    return {"ok": True}


@router.delete("/whatsapp/api/config/acessos/{acesso_id}")
async def config_acesso_revogar(acesso_id: str, payload=Depends(verificar_token)):
    p = await _gestao(payload)
    await M.q("UPDATE whatsapp.acessos_setor SET revogado_em=now(), revogado_por=%s WHERE id=%s AND revogado_em IS NULL",
                      (p["id"], acesso_id), fetch="none")
    return {"ok": True}


@router.post("/whatsapp/api/contatos/{telefone}/vincular")
async def vincular_contato(telefone: str, request: Request, payload=Depends(verificar_token)):
    """Liga o número a um colaborador do cadastro (o nome do cadastro passa a aparecer no lugar do nome do WhatsApp)."""
    await _gestao(payload)
    d = await request.json()
    await M.q("UPDATE whatsapp.contatos SET usuario_id=%s, atualizado_em=now() WHERE telefone=%s",
                      (d.get("usuario_id") or None, telefone), fetch="none")
    return {"ok": True}


# ── conexão ──
def _url_webhook(request: Request):
    base = (os.environ.get("WHATSAPP_WEBHOOK_BASE") or str(request.base_url)).rstrip("/")
    if base.startswith("http://") and "localhost" not in base and "127.0.0.1" not in base:
        base = "https://" + base[7:]          # Render termina o TLS no proxy
    return f"{base}/whatsapp/api/webhook/{P.WEBHOOK_TOKEN}"


@router.get("/whatsapp/api/conexao")
async def conexao(payload=Depends(verificar_token)):
    await _gestao(payload)
    st = await P.estado()
    return {"estado": st, "simulacao": P.simulacao(), "instancia": P.INSTANCIA,
            "qrcode": M.QR_ATUAL["base64"] if st != "open" else None,
            "aviso": "Conexão WhatsApp via Evolution API (evolution-api, Apache 2.0 com condições — github.com/EvolutionAPI/evolution-api)"}


@router.post("/whatsapp/api/conexao/conectar")
async def conectar(request: Request, payload=Depends(verificar_token)):
    await _gestao(payload)
    if P.simulacao():
        return {"estado": "simulacao"}
    if not P.WEBHOOK_TOKEN:
        raise HTTPException(status_code=500, detail="Configure WHATSAPP_WEBHOOK_TOKEN no Render antes de conectar")
    try:
        await P.preparar(_url_webhook(request))
        qr = await P.qrcode()
    except P.ErroProvedor as e:
        raise HTTPException(status_code=502, detail=str(e))
    if qr:
        M.QR_ATUAL.update(base64=qr)
    return {"estado": await P.estado(), "qrcode": qr}


@router.post("/whatsapp/api/conexao/desconectar")
async def desconectar(payload=Depends(verificar_token)):
    await _gestao(payload)
    try:
        await P.desconectar()
    except P.ErroProvedor as e:
        raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True}


# ── simulador (laboratório sem chip: EVOLUTION_URL vazio) ──
@router.post("/whatsapp/api/simular")
async def simular(request: Request, payload=Depends(verificar_token)):
    """Gerência, só em modo simulação: injeta uma mensagem como se um colaborador tivesse escrito."""
    await _gestao(payload)
    if not P.simulacao():
        raise HTTPException(status_code=400, detail="Simulador só funciona sem EVOLUTION_URL (laboratório)")
    d = await request.json()
    tel = "".join(ch for ch in str(d.get("telefone") or "") if ch.isdigit())
    if not tel:
        raise HTTPException(status_code=400, detail="Telefone obrigatório")
    tipo = d.get("tipo") or "texto"
    mensagem = {"conversation": d.get("texto") or ""} if tipo == "texto" else \
        {"imageMessage": {"caption": d.get("texto") or "", "mimetype": "image/jpeg"}} if tipo == "imagem" else \
        {"audioMessage": {"mimetype": "audio/ogg; codecs=opus", "ptt": True}}
    corpo = {"event": "messages.upsert", "instance": P.INSTANCIA, "data": {
        "key": {"remoteJid": f"{tel}@s.whatsapp.net", "fromMe": bool(d.get("do_celular")), "id": "SIMIN" + secrets.token_hex(8).upper()},
        "pushName": d.get("nome") or None, "message": mensagem,
        "messageTimestamp": int(datetime.now(timezone.utc).timestamp())}}
    await M.processar_webhook(corpo)
    return {"ok": True}


@router.get("/whatsapp/api/simuladas")
async def simuladas(telefone: str = "", payload=Depends(verificar_token)):
    await _gestao(payload)
    return [s for s in P.SIMULADAS if not telefone or s["telefone"] == telefone][-60:]
