"""core.storage — Supabase Storage: upload, URLs assinadas, fotos do checklist."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import os, io, time, uuid, json
import requests as req_lib
from .config import SUPABASE_URL, SUPABASE_SERVICE_KEY, BUCKET_ATUAL, BUCKET_NAME

# ── SUPABASE STORAGE ──────────────────────────────────────────
def storage_upload(dados: bytes, path: str) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("Supabase não configurado")
    # Fotos novas vão para o bucket unificado 'garra-fotos'
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_ATUAL}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": "image/jpeg",
        "x-upsert": "true"
    }
    r = req_lib.post(url, headers=headers, data=dados, timeout=30)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Storage upload falhou [{r.status_code}]: {r.text}")
    # Prefixa o path com o bucket para que a leitura saiba onde buscar.
    # Fotos antigas (sem prefixo) continuam sendo lidas do bucket legado.
    return f"{BUCKET_ATUAL}:{path}"

# Cache de URLs assinadas — evita chamadas repetidas ao Supabase
# TTL: 23h (URLs do Supabase expiram em 1h por padrão, mas geramos com 24h)
_url_cache: dict = {}  # {storage_path: (url, expires_at)}
_URL_TTL = 23 * 3600   # 23 horas em segundos
_URL_SUPABASE_EXPIRY = 24 * 3600  # 24h — URL válida no Supabase

def storage_url(path: str, segundos: int = _URL_SUPABASE_EXPIRY) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not path:
        return ""
    # Resolve o bucket a partir do prefixo "bucket:path".
    # Fotos antigas (gravadas antes da unificação) não têm prefixo → bucket legado.
    if ":" in path and not path.startswith("http"):
        bucket, real_path = path.split(":", 1)
    else:
        bucket, real_path = BUCKET_NAME, path
    # Verificar cache
    agora = time.time()
    cached = _url_cache.get(path)
    if cached:
        url, expires_at = cached
        if agora < expires_at:
            return url
    # Gerar URL nova no Supabase
    try:
        r = req_lib.post(
            f"{SUPABASE_URL}/storage/v1/object/sign/{bucket}/{real_path}",
            headers={
                "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                "apikey": SUPABASE_SERVICE_KEY
            },
            json={"expiresIn": segundos}, timeout=10
        )
        if r.status_code == 200:
            url = f"{SUPABASE_URL}/storage/v1{r.json().get('signedURL','')}"
            _url_cache[path] = (url, agora + _URL_TTL)
            return url
    except: pass
    return ""

def storage_delete(paths: list):
    if not paths or not SUPABASE_URL: return
    # Agrupa os paths por bucket (fotos novas têm prefixo "bucket:", antigas não).
    por_bucket = {}
    for p in paths:
        if ":" in p and not p.startswith("http"):
            bucket, real_path = p.split(":", 1)
        else:
            bucket, real_path = BUCKET_NAME, p
        por_bucket.setdefault(bucket, []).append(real_path)
    for bucket, lista in por_bucket.items():
        try:
            req_lib.delete(
                f"{SUPABASE_URL}/storage/v1/object/{bucket}",
                headers={
                    "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
                    "apikey": SUPABASE_SERVICE_KEY
                },
                json={"prefixes": lista}, timeout=10
            )
        except: pass

# ── FOTOS DO CHECKLIST → SUPABASE STORAGE ──────────────────────
# Reaproveita storage_upload/storage_url (mesmo bucket da jardinagem,
# path com prefixo "checklist/" para não colidir).
import base64 as _b64

def _checklist_extrair_fotos_para_storage(envio_id: str, respostas: dict) -> dict:
    """Percorre as respostas de um envio; troca cada foto em base64 por um
    upload real no Supabase Storage, guardando só o path no lugar do base64.
    Se o upload falhar por qualquer motivo, mantém o base64 original (nunca perde a foto)."""
    if not isinstance(respostas, dict):
        return respostas
    for item_id, ans in respostas.items():
        if not isinstance(ans, dict):
            continue
        photo = ans.get("photo")
        if not photo or not isinstance(photo, str) or not photo.startswith("data:image"):
            continue
        try:
            header, b64data = photo.split(",", 1)
            dados = _b64.b64decode(b64data)
            path = f"checklist/{envio_id}/{item_id}.jpg"
            storage_upload(dados, path)
            ans["photo"] = path  # guarda só o caminho, não mais a imagem inteira
        except Exception as e:
            print(f"[Checklist Storage] upload falhou para {item_id}: {e} — mantendo base64 como fallback")
    return respostas

def _checklist_assinar_fotos_para_leitura(respostas: dict) -> dict:
    """Percorre as respostas de um envio; troca cada path de foto salvo no Storage
    por uma URL assinada válida para exibição. Base64 antigo (dados pré-migração)
    passa direto, sem alteração."""
    if not isinstance(respostas, dict):
        return respostas
    for item_id, ans in respostas.items():
        if not isinstance(ans, dict):
            continue
        photo = ans.get("photo")
        if not photo or not isinstance(photo, str):
            continue
        if photo.startswith("data:image") or photo.startswith("http"):
            continue  # já é base64 antigo ou já é uma URL — não mexe
        ans["photo"] = storage_url(photo) or photo
    return respostas
