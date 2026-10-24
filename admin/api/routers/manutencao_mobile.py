"""(24/10/2026) Manutenção no celular — gestão (triagem de pedidos, OTs, frota, conferência de
abastecimentos). Só serve a página `manutencao/static/manutencao-mobile.html`, embutida no app pelo
shell (sso + embedded=1). As regras e o gate (módulo manutencao) continuam nas APIs de routers/manutencao.py."""
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()


@router.get("/manutencao/mobile", response_class=HTMLResponse)
async def pagina_manutencao_mobile():
    raiz = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "manutencao"))
    p = os.path.join(raiz, "static", "manutencao-mobile.html")
    if os.path.isfile(p):
        return open(p, encoding="utf-8").read()
    raise HTTPException(status_code=404, detail="manutencao-mobile.html não encontrado")
