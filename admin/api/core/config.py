"""core.config — variáveis de ambiente e constantes globais."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import os, secrets
from dotenv import load_dotenv
load_dotenv()

DATABASE_URL         = os.environ.get("DATABASE_URL", "")
SUPABASE_URL         = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
JWT_SECRET           = os.environ.get("JWT_SECRET", "")
if not JWT_SECRET:
    # F-06: sem fallback — segredo previsível permitiria forjar tokens.
    # Defina JWT_SECRET no Render (e no ambiente local) antes de iniciar.
    raise RuntimeError("JWT_SECRET não configurado — defina a variável de ambiente antes de iniciar.")

# F-01: chave das rotas de diagnóstico sai do código-fonte.
# Sem DEBUG_KEY definida no ambiente, as rotas de debug ficam DESATIVADAS.
DEBUG_KEY            = os.environ.get("DEBUG_KEY", "")

# ── WEBAUTHN (biometria) ──────────────────────────────────────
# RP ID = domínio (sem esquema/porta). Em localhost o navegador aceita
# 'localhost' automaticamente — as envs permitem sobrescrever em dev.
WEBAUTHN_RP_ID   = os.environ.get("WEBAUTHN_RP_ID", "garra-sistemas.onrender.com")
WEBAUTHN_RP_NAME = os.environ.get("WEBAUTHN_RP_NAME", "Garra Sistemas")
WEBAUTHN_ORIGIN  = os.environ.get("WEBAUTHN_ORIGIN", "https://garra-sistemas.onrender.com")

# ── CAMINHOS DO REPOSITÓRIO (Fase 2 — antes viviam no main.py) ─────────
# Este arquivo está em admin/api/core/ → a raiz do repo fica 3 níveis acima.
_CORE_FILE = os.path.abspath(__file__)
_API_DIR   = os.path.dirname(os.path.dirname(_CORE_FILE))          # admin/api
possible_paths = [
    os.path.join(_API_DIR, "..", "..", "jardinagem"),  # relativo ao código
    "/app/jardinagem",                                  # Render default antigo
    os.path.join(os.getcwd(), "jardinagem"),            # Render: cwd = raiz do repo
]

JARD_DIR = None
for path in possible_paths:
    if os.path.exists(os.path.join(path, "templates")):
        JARD_DIR = os.path.abspath(path)
        break

if not JARD_DIR:
    JARD_DIR = os.path.abspath(possible_paths[0])  # Fallback

STATIC_DIR    = os.path.join(JARD_DIR, "static")
TEMPLATES_DIR = os.path.join(JARD_DIR, "templates")
JARD_ICONS_DIR = os.path.join(STATIC_DIR, "icons")

ICONS_DIR = os.path.join(_API_DIR, "..", "..", "operacional", "checklist", "icons")
OPERACIONAL_STATIC_DIR = os.path.join(_API_DIR, "..", "..", "operacional", "static")
OPERACIONAL_DIR = os.path.join(_API_DIR, "..", "..", "operacional")
CHECKLIST_DIR   = os.path.join(OPERACIONAL_DIR, "checklist")
API_DIR   = _API_DIR                              # admin/api (reset-senha.html mora aqui)
ADMIN_DIR = os.path.join(_API_DIR, "..")          # admin/ (admin-app.html mora aqui)

print(f"JARD_DIR: {JARD_DIR}")
print(f"TEMPLATES_DIR: {TEMPLATES_DIR} (exists: {os.path.exists(TEMPLATES_DIR)})")
print(f"STATIC_DIR: {STATIC_DIR} (exists: {os.path.exists(STATIC_DIR)})")

def _debug_autorizado(chave: str) -> bool:
    """Compara em tempo constante; se DEBUG_KEY não está no ambiente, nega tudo."""
    return bool(DEBUG_KEY) and secrets.compare_digest(chave or "", DEBUG_KEY)
JWT_EXPIRY_HOURS     = int(os.environ.get("JWT_EXPIRY_HOURS", "8"))
MAIL_USERNAME        = os.environ.get("MAIL_USERNAME", "")
MAIL_PASSWORD        = os.environ.get("MAIL_PASSWORD", "")
MAIL_HOST            = os.environ.get("MAIL_HOST", "smtp.hostinger.com")
MAIL_PORT            = int(os.environ.get("MAIL_PORT", "587"))
MAIL_DESTINO         = os.environ.get("MAIL_DESTINO", "")
MAIL_CC              = os.environ.get("MAIL_CC", "")
FRONTEND_URL         = os.environ.get("FRONTEND_URL", "https://garra-checklist-app.onrender.com")
BUCKET_NAME          = "jardinagem-fotos"      # legado — fotos antigas continuam aqui
BUCKET_ATUAL         = "garra-fotos"           # bucket unificado — todas as fotos novas (jardinagem + checklist)

