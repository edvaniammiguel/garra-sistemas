"""core.db — conexões: asyncpg (checklist/operacional) e psycopg2 (jardinagem)."""
# Extraído do main.py na Refatoração Fase 1 (03/07/2026) — código idêntico ao original.

import asyncpg, threading
import os as _os
import psycopg2, psycopg2.extras, psycopg2.extensions
from .config import DATABASE_URL

# ── asyncpg (checklist) ───────────────────────────────────────
async def get_db():
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        await conn.close()

# ── psycopg2 (jardinagem · auth · usuários · operacional síncrono) ─────
# (12/10/2026) LENTIDÃO: cada jard_query abria uma conexão TLS NOVA com o Neon
# (DNS + handshake + autenticação ≈ 0,5–1,5 s) e fechava em seguida — 814
# pontos de chamada pagavam isso a cada consulta (login 2,3 s, /usuarios 1,4 s).
# Agora: pool de conexões reutilizadas; a conexão volta ao pool em vez de
# fechar. Conexão quebrada (Neon dormiu / rede caiu) é descartada e a
# consulta tenta UMA vez de novo com conexão nova. Mesma assinatura, mesma
# semântica (autocommit por chamada) — nenhum chamador muda.
import psycopg2.pool as _pgpool
_local = threading.local()
_jpool = None
_jpool_lock = threading.Lock()

def _get_jpool():
    global _jpool
    if _jpool is None:
        with _jpool_lock:
            if _jpool is None:
                _jpool = _pgpool.ThreadedConnectionPool(
                    1, 8, DATABASE_URL,
                    cursor_factory=psycopg2.extras.RealDictCursor,
                    sslmode=_os.environ.get("PGSSLMODE", "require"), connect_timeout=10,
                    keepalives=1, keepalives_idle=60, keepalives_interval=20, keepalives_count=3,
                    application_name="garra-sync")
    return _jpool

def get_jard_db():
    """Conexão psycopg2 do pool (compatibilidade: quem chamar direto deve
    devolver com _jard_release; jard_query/jard_query_id já fazem isso)."""
    return _get_jpool().getconn()

def _jard_release(conn, quebrada=False):
    try:
        if quebrada or conn.closed:
            _get_jpool().putconn(conn, close=True)
        else:
            if conn.status != psycopg2.extensions.STATUS_READY:
                conn.rollback()
            _get_jpool().putconn(conn)
    except Exception:
        pass

def _jard_exec(sql, params, fetch):
    conn = get_jard_db()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            conn.commit()
            if fetch == "one":  return cur.fetchone()
            if fetch == "all":  return cur.fetchall()
            return None
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        _jard_release(conn, quebrada=True); conn = None
        raise
    except Exception:
        try: conn.rollback()
        except Exception: pass
        raise
    finally:
        if conn is not None:
            _jard_release(conn)

def jard_query(sql, params=None, fetch="all"):
    try:
        return _jard_exec(sql, params, fetch)
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        return _jard_exec(sql, params, fetch)     # conexão caída: uma nova tentativa

def jard_query_id(sql, params=None):
    return jard_query(sql + " RETURNING *", params, fetch="one")


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
                    DATABASE_URL, min_size=2, max_size=10,
                    command_timeout=60,
                    statement_cache_size=0,
                    # (12/10/2026) padrão era fechar conexão parada há 5 min → cada tela depois
                    # de uma pausa pagava handshake TLS novo com o Neon
                    max_inactive_connection_lifetime=1800,
                    server_settings={"application_name": "garra-async"},
                )
                _asyncio.get_event_loop().create_task(_keepalive())
    return _apool

async def _keepalive():
    """(12/10/2026) SELECT 1 a cada 60 s: mantém as conexões vivas e o compute do Neon
    acordado (o autosuspend padrão dorme após 5 min e a 1ª consulta leva 5–15 s)."""
    try:
        while True:
            await _asyncio.sleep(60)
            try:
                async with _apool.acquire() as conn:
                    await conn.execute("SELECT 1")
            except Exception:
                pass
    except _asyncio.CancelledError:
        return

_ERROS_CONEXAO = (asyncpg.exceptions.ConnectionDoesNotExistError, asyncpg.exceptions.InterfaceError,
                  asyncpg.exceptions.InternalClientError, ConnectionResetError, OSError)

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

def _coagir_datas(args):
    """asyncpg é estrito com tipos: strings de data e hora (JSON universal)
    viram objetos nativos. Fonte única — usada por ajard_query E ajard_query_id.
    (Regra 56 — psycopg2 aceitava strings; asyncpg exige date/time nativos.)"""
    import re as _re
    from datetime import date as _date, time as _time
    for i, v in enumerate(args):
        if isinstance(v, str):
            if len(v) == 10 and v[4:5] == '-':
                try:
                    args[i] = _date.fromisoformat(v)
                    continue
                except (ValueError, TypeError):
                    pass
            if _re.fullmatch(r'\d{2}:\d{2}(:\d{2})?', v):
                try:
                    args[i] = _time.fromisoformat(v)
                except (ValueError, TypeError):
                    pass
    return args

async def ajard_query(sql, params=None, fetch="all"):
    try:
        return await _ajard_exec(sql, params, fetch)
    except _ERROS_CONEXAO:
        return await _ajard_exec(sql, params, fetch)   # conexão caída (Neon dormiu): tenta uma vez com outra

async def _ajard_exec(sql, params, fetch):
    pool = await _get_apool()
    args = _coagir_datas(list(params) if params else [])
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
    args = _coagir_datas(list(params) if params else [])
    q = _converter_placeholders(sql + " RETURNING *")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(q, *args)
        return dict(row) if row is not None else None

