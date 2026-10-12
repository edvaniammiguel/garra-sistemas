"""core.storage — Supabase Storage: upload, URLs assinadas, fotos do checklist."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import os, io, time, uuid, json
import requests as req_lib
from .config import SUPABASE_URL, SUPABASE_SERVICE_KEY, BUCKET_ATUAL, BUCKET_NAME

# ── SUPABASE STORAGE ──────────────────────────────────────────
def storage_upload(dados: bytes, path: str, content_type: str = "image/jpeg") -> str:
    """content_type opcional (default image/jpeg — retrocompatível com todas as
    chamadas existentes). Anexos de OC usam application/pdf ou image/*."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise ValueError("Supabase não configurado")
    # Fotos novas vão para o bucket unificado 'garra-fotos'
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET_ATUAL}/{path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
        "Content-Type": content_type or "image/jpeg",
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

# (12/10/2026) Sessão HTTP reaproveitada: cada assinatura abria uma conexão TLS
# nova com o Supabase (~150–300 ms só de handshake). Com keep-alive, as
# chamadas seguintes reutilizam a conexão.
_sess = req_lib.Session()
_sess.headers.update({"apikey": SUPABASE_SERVICE_KEY or "",
                      "Authorization": f"Bearer {SUPABASE_SERVICE_KEY or ''}"})

def _bucket_e_path(path: str):
    # Resolve o bucket a partir do prefixo "bucket:path".
    # Fotos antigas (gravadas antes da unificação) não têm prefixo → bucket legado.
    if ":" in path and not path.startswith("http"):
        bucket, real_path = path.split(":", 1)
        return bucket, real_path
    return BUCKET_NAME, path

def storage_url(path: str, segundos: int = _URL_SUPABASE_EXPIRY) -> str:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY or not path:
        return ""
    bucket, real_path = _bucket_e_path(path)
    # Verificar cache
    agora = time.time()
    cached = _url_cache.get(path)
    if cached:
        url, expires_at = cached
        if agora < expires_at:
            return url
    # Gerar URL nova no Supabase
    try:
        r = _sess.post(
            f"{SUPABASE_URL}/storage/v1/object/sign/{bucket}/{real_path}",
            json={"expiresIn": segundos}, timeout=10
        )
        if r.status_code == 200:
            url = f"{SUPABASE_URL}/storage/v1{r.json().get('signedURL','')}"
            _url_cache[path] = (url, agora + _URL_TTL)
            return url
    except: pass
    return ""

def storage_urls(paths, segundos: int = _URL_SUPABASE_EXPIRY) -> dict:
    """(12/10/2026) Assina VÁRIOS caminhos de uma vez — 1 chamada por bucket
    (endpoint de lote do Supabase) em vez de 1 chamada por foto. O mês da
    Jardinagem tem dezenas de fotos: 60 round-trips viravam 3–6 s de espera.
    Devolve {path: url} ('' para o que falhar); o que já está no cache não
    volta ao Supabase. Se o lote falhar, cai na assinatura individual."""
    out = {}
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return {p: "" for p in paths if p}
    agora = time.time()
    por_bucket: dict = {}
    for p in paths:
        if not p or p in out:
            continue
        cached = _url_cache.get(p)
        if cached and agora < cached[1]:
            out[p] = cached[0]
            continue
        bucket, real = _bucket_e_path(p)
        por_bucket.setdefault(bucket, {})[real] = p
    for bucket, reais in por_bucket.items():
        lista = list(reais.keys())
        ok = False
        try:
            r = _sess.post(f"{SUPABASE_URL}/storage/v1/object/sign/{bucket}",
                           json={"expiresIn": segundos, "paths": lista}, timeout=15)
            if r.status_code == 200 and isinstance(r.json(), list):
                ok = True
                for item in r.json():
                    real = item.get("path") or ""
                    p = reais.get(real)
                    if p is None:
                        continue
                    su = item.get("signedURL") if not item.get("error") else None
                    if su:
                        url = f"{SUPABASE_URL}/storage/v1{su}"
                        _url_cache[p] = (url, agora + _URL_TTL)
                        out[p] = url
                    else:
                        out[p] = ""
        except Exception as e:
            print(f"[Storage] lote de assinaturas falhou ({bucket}): {e}")
        if not ok:
            for real, p in reais.items():
                out[p] = storage_url(p, segundos)
        for p in reais.values():
            out.setdefault(p, "")
    return out

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
            # (12/09/2026) Lição 3: guardar o RETORNO (com prefixo do bucket), nunca
            # o path montado — sem o prefixo a leitura procura no bucket legado,
            # a assinatura falha e a tela mostra "Offline".
            ans["photo"] = storage_upload(dados, path) or path
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
        # (12/09/2026) Fotos do checklist gravadas sem prefixo (bug do extrator)
        # moram no bucket unificado, não no legado: tenta o unificado primeiro,
        # cai no comportamento antigo se não achar. Nunca devolve caminho cru
        # como se fosse URL — sem assinatura válida, deixa vazio (tela mostra
        # "sem foto" em vez de pedir uma URL 404 ao próprio servidor).
        url = ""
        if ":" not in photo:
            url = storage_url(f"{BUCKET_ATUAL}:{photo}")
        if not url:
            url = storage_url(photo)
        ans["photo"] = url or None
    return respostas
