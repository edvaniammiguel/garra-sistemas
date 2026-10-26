"""(26/10/2026) Atendimento WhatsApp — adaptador do provedor (Evolution API v2).

ÚNICO arquivo que conhece a Evolution. Trocar de provedor (outra Evolution, API oficial da Meta)
mexe só aqui; motor, rotas e tela não mudam.

Variáveis de ambiente (Render / .env do laboratório):
  EVOLUTION_URL         https://wa.garraterraplenagem.com.br   (vazio = MODO SIMULAÇÃO)
  EVOLUTION_APIKEY      chave AUTHENTICATION_API_KEY do serviço Evolution
  EVOLUTION_INSTANCIA   garra-interno
  WHATSAPP_WEBHOOK_TOKEN token aleatório que vai na URL do webhook

MODO SIMULAÇÃO (EVOLUTION_URL vazio): nada sai para o WhatsApp; as mensagens enviadas ficam em
SIMULADAS (memória) e a tela de configuração mostra o simulador. É o modo do laboratório sem chip.
"""
import os
import time
import uuid
import base64
import asyncio
import logging
import requests

log = logging.getLogger("whatsapp")

EVOLUTION_URL = (os.environ.get("EVOLUTION_URL") or "").rstrip("/")
EVOLUTION_APIKEY = os.environ.get("EVOLUTION_APIKEY") or ""
INSTANCIA = os.environ.get("EVOLUTION_INSTANCIA") or "garra-interno"
WEBHOOK_TOKEN = os.environ.get("WHATSAPP_WEBHOOK_TOKEN") or ""

EVENTOS_WEBHOOK = ["MESSAGES_UPSERT", "MESSAGES_UPDATE", "CONNECTION_UPDATE", "QRCODE_UPDATED"]
MSG_LIGACAO = "Este número não recebe ligações. Mande sua mensagem por escrito ou em áudio, por favor."

SIMULADAS = []          # modo simulação: o que "saiu" para o WhatsApp (mais recente no fim)
_sess = requests.Session()


def simulacao():
    return not EVOLUTION_URL


def jid(telefone):
    return f"{telefone}@s.whatsapp.net"


class ErroProvedor(Exception):
    pass


def _req(metodo, caminho, corpo=None, timeout=30):
    r = _sess.request(metodo, f"{EVOLUTION_URL}{caminho}", json=corpo, timeout=timeout,
                      headers={"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"})
    try:
        dados = r.json()
    except Exception:
        dados = {"texto": r.text[:300]}
    if r.status_code >= 400:
        raise ErroProvedor(f"Evolution {r.status_code}: {str(dados)[:300]}")
    return dados


async def _chamar(metodo, caminho, corpo=None, timeout=30):
    return await asyncio.to_thread(_req, metodo, caminho, corpo, timeout)


def _simular(tipo, telefone, **extra):
    msg_id = "SIM" + uuid.uuid4().hex[:16].upper()
    SIMULADAS.append(dict(extra, id=msg_id, tipo=tipo, telefone=telefone, em=time.time()))
    del SIMULADAS[:-300]
    return msg_id


def _id_resposta(dados):
    return ((dados or {}).get("key") or {}).get("id")


# ── envio ──────────────────────────────────────────────────────
async def enviar_texto(telefone, texto, citado=None):
    """citado = {'id': provider_msg_id, 'de_mim': bool, 'texto': str} → sai como "responder em cima"."""
    if simulacao():
        return _simular("texto", telefone, texto=texto, citado=(citado or {}).get("id"))
    corpo = {"number": telefone, "text": texto, "delay": 1200}
    if citado and citado.get("id"):
        corpo["quoted"] = {"key": {"id": citado["id"], "fromMe": bool(citado.get("de_mim")), "remoteJid": jid(telefone)},
                           "message": {"conversation": citado.get("texto") or ""}}
    return _id_resposta(await _chamar("POST", f"/message/sendText/{INSTANCIA}", corpo))


async def enviar_midia(telefone, tipo, dados, mimetype, nome_arquivo=None, legenda=None):
    """tipo: imagem | video | documento | audio (audio sai como áudio de voz)."""
    if simulacao():
        return _simular(tipo, telefone, texto=legenda or nome_arquivo or tipo, mimetype=mimetype)
    b64 = base64.b64encode(dados).decode()
    if tipo == "audio":
        return _id_resposta(await _chamar("POST", f"/message/sendWhatsAppAudio/{INSTANCIA}",
                                          {"number": telefone, "audio": b64, "delay": 800}, timeout=90))
    corpo = {"number": telefone, "mediatype": {"imagem": "image", "video": "video"}.get(tipo, "document"),
             "mimetype": mimetype, "media": b64, "delay": 800}
    if legenda:
        corpo["caption"] = legenda
    if nome_arquivo:
        corpo["fileName"] = nome_arquivo
    return _id_resposta(await _chamar("POST", f"/message/sendMedia/{INSTANCIA}", corpo, timeout=90))


async def reagir(telefone, provider_msg_id, de_mim, emoji):
    if simulacao():
        return _simular("reacao", telefone, texto=emoji, alvo=provider_msg_id)
    await _chamar("POST", f"/message/sendReaction/{INSTANCIA}",
                  {"key": {"remoteJid": jid(telefone), "fromMe": bool(de_mim), "id": provider_msg_id}, "reaction": emoji})


async def marcar_lidas(telefone, provider_ids):
    if simulacao() or not provider_ids:
        return
    try:
        await _chamar("POST", f"/chat/markMessageAsRead/{INSTANCIA}",
                      {"readMessages": [{"remoteJid": jid(telefone), "fromMe": False, "id": i} for i in provider_ids]})
    except Exception as e:
        log.warning("marcar lidas: %s", e)


async def digitando(telefone):
    if simulacao():
        return
    try:
        await _chamar("POST", f"/chat/sendPresence/{INSTANCIA}", {"number": telefone, "delay": 3000, "presence": "composing"})
    except Exception:
        pass


async def baixar_midia(provider_msg_id):
    """Mídia recebida → (bytes, mimetype). A Evolution descriptografa e devolve em base64."""
    if simulacao():
        return None, None
    d = await _chamar("POST", f"/chat/getBase64FromMediaMessage/{INSTANCIA}",
                      {"message": {"key": {"id": provider_msg_id}}, "convertToMp4": False}, timeout=90)
    b64 = (d or {}).get("base64") or ""
    if "," in b64[:100]:
        b64 = b64.split(",", 1)[1]
    return (base64.b64decode(b64) if b64 else None), (d or {}).get("mimetype")


# ── conexão ────────────────────────────────────────────────────
async def estado():
    """'open' | 'connecting' | 'close' | 'simulacao' | 'erro: …'"""
    if simulacao():
        return "simulacao"
    try:
        d = await _chamar("GET", f"/instance/connectionState/{INSTANCIA}", timeout=10)
        return ((d or {}).get("instance") or {}).get("state") or (d or {}).get("state") or "close"
    except ErroProvedor as e:
        return "inexistente" if " 404" in str(e) else f"erro: {e}"
    except Exception as e:
        return f"erro: {e}"


def _cfg_instancia(url_webhook):
    return {
        "webhook": {"enabled": True, "url": url_webhook, "byEvents": False, "base64": False, "events": EVENTOS_WEBHOOK},
        "rejectCall": True, "msgCall": MSG_LIGACAO, "groupsIgnore": True, "alwaysOnline": False,
        "readMessages": False, "readStatus": False, "syncFullHistory": False,
    }


async def preparar(url_webhook):
    """Cria a instância se não existir e (re)aponta o webhook e as regras (rejeitar ligação, ignorar grupos).
    Idempotente — o botão "Conectar" da tela chama sempre."""
    if simulacao():
        return
    cfg = _cfg_instancia(url_webhook)
    if await estado() == "inexistente":
        await _chamar("POST", "/instance/create", dict(cfg, instanceName=INSTANCIA, qrcode=True, integration="WHATSAPP-BAILEYS"))
        return
    await _chamar("POST", f"/webhook/set/{INSTANCIA}", {"webhook": cfg["webhook"]})
    await _chamar("POST", f"/settings/set/{INSTANCIA}", {k: v for k, v in cfg.items() if k != "webhook"})


async def qrcode():
    """QR para ler no celular do número (data URL de imagem) ou None se já conectado."""
    if simulacao():
        return None
    d = await _chamar("GET", f"/instance/connect/{INSTANCIA}", timeout=20)
    return (d or {}).get("base64")


async def desconectar():
    if simulacao():
        return
    await _chamar("DELETE", f"/instance/logout/{INSTANCIA}")
