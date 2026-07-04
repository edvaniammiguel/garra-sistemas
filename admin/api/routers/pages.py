"""routers.pages — rotas que servem as PÁGINAS do sistema (admin, mobile,
reset de senha, manifests e redirects de compatibilidade).

Refatoração Fase 2 · Etapa 5 (04/07/2026). Caminhos de arquivo 100%%
ancorados no core/config (regra pós-hotfix: nunca relativos a __file__).
"""
import os
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, HTMLResponse

from core.config import (
    ADMIN_DIR, API_DIR, OPERACIONAL_STATIC_DIR, ICONS_DIR,
)

router = APIRouter()

@router.get("/admin", response_class=HTMLResponse)
async def admin_page():
    path = os.path.join(ADMIN_DIR, "admin-app.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Admin app não encontrado")
    return open(path, encoding="utf-8").read()

@router.get("/mobile")
async def mobile_app():
    return FileResponse(os.path.join(OPERACIONAL_STATIC_DIR, "mobile.html"))

@router.get("/reset-senha.html")
async def reset_senha_page():
    """Página que o link de 'Esqueci minha senha' abre. Estava ausente do
    repositório — o email de reset era enviado mas o link dava 404."""
    return FileResponse(os.path.join(API_DIR, "reset-senha.html"))

@router.get("/mobile/sw.js")
async def mobile_sw():
    return FileResponse(
        os.path.join(OPERACIONAL_STATIC_DIR, "sw.js"),
        media_type="application/javascript"
    )

@router.get("/mobile/manifest.json")
async def mobile_manifest():
    return FileResponse(os.path.join(OPERACIONAL_STATIC_DIR, "mobile.manifest.json"))

@router.get("/manifest.json")
async def redirect_manifest():
    return RedirectResponse(url="/mobile/manifest.json")

@router.get("/sw.js")
async def redirect_sw():
    return RedirectResponse(url="/mobile/sw.js")

@router.get("/favicon.ico")
async def redirect_favicon():
    return RedirectResponse(url="/static/icons/favicon.ico")

@router.get("/")
async def root():
    return RedirectResponse(url="/admin")
