"""core.db — conexões: asyncpg (checklist/operacional) e psycopg2 (jardinagem)."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import asyncpg, threading
import psycopg2, psycopg2.extras
from .config import DATABASE_URL

# ── asyncpg (checklist) ───────────────────────────────────────
async def get_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

# ── psycopg2 (jardinagem) ─────────────────────────────────────
_local = threading.local()

def get_jard_db():
    """Conexão psycopg2 para rotas síncronas do jardinagem."""
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
        sslmode="require",
        connect_timeout=10
    )
    return conn

def jard_query(sql, params=None, fetch="all"):
    conn = get_jard_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
            if fetch == "one":  return cur.fetchone()
            if fetch == "all":  return cur.fetchall()
            if fetch == "none": return None
    finally:
        conn.close()

def jard_query_id(sql, params=None):
    conn = get_jard_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql + " RETURNING *", params or ())
            conn.commit()
            return cur.fetchone()
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════
# MOTOR ASSÍNCRONO (Fase 2 · Etapa 6.1) — ajard_query / ajard_query_id
# Semântica IDÊNTICA ao jard_query, sobre asyncpg (não bloqueia o event
# loop). A migração das chamadas é feita módulo a módulo na Etapa 6.2;
# o psycopg2 acima permanece até a última chamada migrar.
#
# Garantias de paridade (provadas em teste contra Postgres real):
#   • placeholders %s → $1..$n (e %% → % literal)
#   • linhas retornam como DICTs de verdade (row["x"] E row.get("x"))
#   • fetch="all" → list[dict] | "one" → dict|None | "none" → None
#   • ajard_query_id: anexa RETURNING * e devolve a linha como dict
#   • DDL multi-comando (vários ; num SQL) funciona com fetch="none"
#   • autocommit por chamada (mesmo comportamento do commit() atual)
# ══════════════════════════════════════════════════════════════
import asyncio as _asyncio

_apool = None
_apool_lock = _asyncio.Lock()

async def _get_apool():
    global _apool
    if _apool is None:
        async with _apool_lock:
            if _apool is None:
                _apool = await asyncpg.create_pool(
                    DATABASE_URL, min_size=1, max_size=10,
                    command_timeout=60,
                )
    return _apool

def _converter_placeholders(sql: str) -> str:
    """%s → $1..$n na ordem; %% → % literal (paridade com psycopg2)."""
    partes = sql.split("%%")           # protege os %% antes de numerar
    numerado = []
    n = 0
    for p in partes:
        segs = p.split("%s")
        out = segs[0]
        for s in segs[1:]:
            n += 1
            out += f"${n}" + s
        numerado.append(out)
    return "%".join(numerado)

async def ajard_query(sql, params=None, fetch="all"):
    pool = await _get_apool()
    args = list(params) if params else []
    # asyncpg é estrito com tipos: strings de data/datetime vindas do front
    # precisam virar objetos nativos (psycopg2 aceitava strings em silêncio).
    for i, v in enumerate(args):
        if isinstance(v, str) and len(v) == 10:
            try:
                from datetime import date as _date
                args[i] = _date.fromisoformat(v)
            except (ValueError, TypeError):
                pass
    async with pool.acquire() as conn:
        if fetch == "none" and not args:
            # protocolo simples: aceita SQL com múltiplos comandos (DDL)
            await conn.execute(sql)
            return None
        q = _converter_placeholders(sql)
        if fetch == "one":
            row = await conn.fetchrow(q, *args)
            return dict(row) if row is not None else None
        if fetch == "all":
            rows = await conn.fetch(q, *args)
            return [dict(r) for r in rows]
        await conn.execute(q, *args)   # fetch == "none" com params
        return None

async def ajard_query_id(sql, params=None):
    pool = await _get_apool()
    args = list(params) if params else []
    q = _converter_placeholders(sql + " RETURNING *")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(q, *args)
        return dict(row) if row is not None else None

