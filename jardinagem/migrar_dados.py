"""
migrar_dados.py — Migra dados do SQLite (sistema antigo) para o Supabase
Execute após o setup.py:
  python migrar_dados.py caminho/para/garra.db
"""
import sys, sqlite3, uuid, os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else "garra.db"
if not os.path.exists(DB_PATH):
    print(f"Banco não encontrado: {DB_PATH}")
    sys.exit(1)

sb  = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
old = sqlite3.connect(DB_PATH)
old.row_factory = sqlite3.Row

print(f"\n📦 Migrando {DB_PATH} → Supabase\n")

# ── MESES ──────────────────────────────────────────────────────
meses_map = {}  # old_id → new_id
meses = old.execute("SELECT * FROM meses ORDER BY id").fetchall()
print(f"  Meses: {len(meses)}")
for m in meses:
    try:
        res = sb.schema("jardinagem").table("meses").insert({
            "ano":   m["ano"],
            "mes":   m["mes"],
            "label": m["label"]
        }).execute()
        meses_map[m["id"]] = res.data[0]["id"]
        print(f"    ✅ {m['label']}")
    except Exception as e:
        # Já existe — busca
        ex = sb.schema("jardinagem").table("meses")\
            .select("id").eq("ano", m["ano"]).eq("mes", m["mes"]).execute()
        if ex.data:
            meses_map[m["id"]] = ex.data[0]["id"]
            print(f"    ↩  {m['label']} (já existia)")

# ── SEMANAS ────────────────────────────────────────────────────
semanas_map = {}
semanas = old.execute("SELECT * FROM semanas ORDER BY id").fetchall()
print(f"\n  Semanas: {len(semanas)}")
for s in semanas:
    novo_mes_id = meses_map.get(s["mes_id"])
    if not novo_mes_id:
        print(f"    ⚠️  Semana {s['id']} sem mês — ignorada")
        continue
    try:
        res = sb.schema("jardinagem").table("semanas").insert({
            "mes_id": novo_mes_id,
            "label":  s["label"],
            "ordem":  s["ordem"] or 0,
            "status": "aberta"
        }).execute()
        semanas_map[s["id"]] = res.data[0]["id"]
        print(f"    ✅ {s['label']}")
    except Exception as e:
        print(f"    ❌ {s['label']}: {e}")

# ── PARES ──────────────────────────────────────────────────────
pares_map = {}
pares = old.execute("SELECT * FROM pares ORDER BY id").fetchall()
print(f"\n  Pares: {len(pares)}")
for p in pares:
    novo_sem_id = semanas_map.get(p["semana_id"])
    if not novo_sem_id:
        print(f"    ⚠️  Par {p['id']} sem semana — ignorado")
        continue
    try:
        res = sb.schema("jardinagem").table("pares").insert({
            "semana_id":  novo_sem_id,
            "codigo_a":   p["codigo_a"],
            "codigo_d":   p["codigo_d"],
            "local_nome": p["local_nome"] or "",
            "data_label": p["data_label"] or "",
            "ordem":      p["ordem"] or 0
        }).execute()
        pares_map[p["id"]] = res.data[0]["id"]
    except Exception as e:
        print(f"    ❌ Par {p['id']}: {e}")

print(f"    ✅ {len(pares_map)} pares migrados")

# ── FOTOS — registros sem arquivo (storage precisa ser migrado à parte) ──
fotos = old.execute("SELECT * FROM fotos ORDER BY id").fetchall()
print(f"\n  Fotos (registros): {len(fotos)}")
print("  ⚠️  Arquivos físicos precisam ser enviados para o Supabase Storage.")
print("     Use o script migrar_storage.py separado para isso.\n")

for f in fotos:
    novo_par_id = pares_map.get(f["par_id"])
    if not novo_par_id:
        continue
    try:
        sb.schema("jardinagem").table("fotos").insert({
            "par_id":        novo_par_id,
            "tipo":          f["tipo"],
            "origem":        "desktop",
            "storage_path":  f"migrado/{f['filename']}",
            "filename_orig": f["original"] or f["filename"],
            "ia_descricao":  f.get("ia_descricao") or "",
            "ia_local":      f.get("ia_local") or "",
            "sincronizado":  True
        }).execute()
    except Exception:
        pass

print(f"  ✅ Registros de fotos criados (storage_path = 'migrado/filename')")
print("     Após subir os arquivos no Storage, atualize os storage_path.\n")

# ── ATUALIZA next_code ──────────────────────────────────────────
try:
    cfg = old.execute("SELECT valor FROM cfg WHERE chave='next_code'").fetchone()
    if not cfg:
        cfg = old.execute("SELECT valor FROM config WHERE chave='next_code'").fetchone()
    if cfg:
        sb.schema("jardinagem").table("config").update({"valor": cfg["valor"]}).eq("chave", "next_code").execute()
        print(f"  ✅ next_code atualizado para {cfg['valor']}")
except Exception as e:
    print(f"  ⚠️  next_code: {e}")

print("\n✅ Migração concluída!\n")
old.close()
