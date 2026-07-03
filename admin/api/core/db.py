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

