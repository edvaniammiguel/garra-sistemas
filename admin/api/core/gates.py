"""core.gates — (21/10/2026) dependências de rota por MÓDULO (tela de Permissões).

    @router.get("/x")
    async def x(payload=Depends(exigir_modulo("admin_master"))): ...

Admin (Master) passa sempre. Nenhuma rota deve liberar por lista de perfis:
isso fazia a exceção "Bloqueado" da tela de Permissões não valer por URL direta.
"""
from fastapi import Depends, HTTPException
from core.auth import verificar_token
from core.permissions import tem_modulo


def exigir_modulo(*modulos, detail: str = None):
    mods = set(modulos)

    async def _dep(payload=Depends(verificar_token)):
        if await tem_modulo(payload, mods):
            return payload
        raise HTTPException(status_code=403, detail=detail or ("Sem permissão — módulo necessário: " + " / ".join(sorted(mods))))
    _dep.__name__ = "exigir_" + "_".join(sorted(mods))
    return _dep


# Painel de Gestão do Admin (OS, Controle Mensal, Agenda, Cadastros)
exigir_painel_gestao = exigir_modulo("admin_master", detail="Sem permissão para o Painel de Gestão (módulo Painel de Gestão na tela de Permissões)")
