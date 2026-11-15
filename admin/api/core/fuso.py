"""Fuso horário da Garra (14/11/2026).

O servidor (Render) e o banco (Neon) trabalham em UTC: das 21h à meia-noite de Brasília,
`date.today()` e `now()::date` já devolvem o dia seguinte. Toda data "de hoje" da
Manutenção, Compras e Relatórios passa por aqui.

- hoje()   → data de hoje em Brasília
- agora()  → datetime com fuso de Brasília (aware)
- SQL_HOJE → expressão SQL da data de hoje em Brasília
- sql_dia(expr) → expressão SQL que converte um timestamptz no DIA de Brasília
"""
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("America/Sao_Paulo")
TZ_SQL = "America/Sao_Paulo"
SQL_HOJE = f"(now() AT TIME ZONE '{TZ_SQL}')::date"


def agora() -> datetime:
    return datetime.now(FUSO)


def hoje() -> date:
    return agora().date()


def sql_dia(expr: str) -> str:
    return f"(({expr}) AT TIME ZONE '{TZ_SQL}')::date"


def inicio_dia(d: date) -> datetime:
    """00:00 do dia d em Brasília (para comparar com colunas timestamptz)."""
    return datetime.combine(d, time.min, FUSO)


def fim_dia(d: date) -> datetime:
    """23:59:59 do dia d em Brasília."""
    return datetime.combine(d, time(23, 59, 59), FUSO)


def dia_seguinte(d: date) -> datetime:
    """00:00 do dia seguinte a d em Brasília (limite exclusivo de período)."""
    return inicio_dia(d + timedelta(days=1))
