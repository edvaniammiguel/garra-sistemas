"""core.permissions — módulos disponíveis e módulos-padrão por perfil."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

from .db import ajard_query

# ═══════════════════════════════════════════════════════════════════════════

MODULOS_DISPONIVEIS = [
    {"id": "admin_master",        "label": "Admin Master",          "desc": "Painel de gestão"},
    {"id": "jardinagem_desktop",  "label": "Jardinagem Desktop",    "desc": "Relatórios e fotos"},
    {"id": "jardinagem_mobile",   "label": "Jardinagem Mobile",     "desc": "Campo — fotos e KM"},
    {"id": "operacional_mobile",  "label": "Operacional Mobile",    "desc": "OS e horímetro"},
    {"id": "checklist",           "label": "Checklist",             "desc": "Checklist de máquinas"},
    {"id": "checklist_logistica", "label": "Logística (Checklist)", "desc": "Aba de carros de apoio dentro do Checklist"},
    {"id": "manutencao",          "label": "Manutenção Desktop",    "desc": "Módulo completo — frota, OTs, materiais (gestão)"},
    {"id": "pedir_ot",            "label": "Manutenção — Pedir OT", "desc": "Solicitar OT pelo mobile e acompanhar (mecânicos)"},
    {"id": "abastecimento",       "label": "Abastecimento (mobile)", "desc": "Registrar abastecimento com fotos — leitura alimenta FMD/preventivas"},
    {"id": "abastecimento_notas", "label": "Notas de Abastecimento", "desc": "Conferência financeira das notinhas (foto, valores, lançado na MAIS) — sem acesso à Manutenção"},
]

PERFIL_MODULOS_PADRAO = {
    "admin":     ["admin_master","jardinagem_desktop","jardinagem_mobile","operacional_mobile","checklist","checklist_logistica","abastecimento","abastecimento_notas"],
    "gestor":    ["admin_master","jardinagem_desktop","operacional_mobile","abastecimento","abastecimento_notas"],
    "luana":     ["admin_master","jardinagem_desktop","operacional_mobile","abastecimento_notas"],
    "bruna":     ["admin_master","checklist","manutencao","pedir_ot","abastecimento"],
    "operador":  ["operacional_mobile","checklist","abastecimento"],
    "motorista": ["operacional_mobile","checklist","abastecimento"],
    "campo":     ["jardinagem_mobile"],
}

PERFIL_LABEL_SEED = {
    "admin": "Administrador", "gestor": "Gestor", "luana": "Comercial",
    "bruna": "Mecânica", "operador": "Operador", "motorista": "Motorista", "campo": "Campo",
}

PERFIS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.perfis_customizados (
    nome TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    modulos TEXT DEFAULT '',
    ativo BOOLEAN DEFAULT true,
    criado_em TIMESTAMP DEFAULT NOW(),
    atualizado_em TIMESTAMP DEFAULT NOW()
)
"""

async def perfil_modulos_padrao(perfil: str):
    """Fonte da verdade: banco. Se o perfil não existir lá (ex: banco fora do ar),
    cai no dict hardcoded como rede de segurança."""
    try:
        row = await ajard_query(
            "SELECT modulos FROM public.perfis_customizados WHERE nome=%s AND ativo=true",
            (perfil,), fetch="one"
        )
        if row is not None:
            modulos_str = row.get("modulos") or ""
            return [m.strip() for m in modulos_str.split(",") if m.strip()]
    except Exception:
        pass
    return PERFIL_MODULOS_PADRAO.get(perfil, [])
