"""
setup.py — Cria usuários iniciais no Supabase
Execute UMA VEZ após rodar a migration SQL:
  python setup.py
"""
import os, bcrypt
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

USUARIOS = [
    {"nome": "Admin Garra",   "email": "admin@garra.local",  "perfil": "admin",  "senha": "garra@2026"},
    {"nome": "Luana",         "email": "luana@garra.local",   "perfil": "luana",  "senha": "luana@2026"},
    {"nome": "Arthur",        "email": "arthur@garra.local",  "perfil": "campo",  "senha": "arthur@2026"},
    {"nome": "Breno",         "email": "breno@garra.local",   "perfil": "campo",  "senha": "breno@2026"},
]

print("\n🔧 Criando usuários iniciais...\n")
for u in USUARIOS:
    senha_hash = bcrypt.hashpw(u["senha"].encode(), bcrypt.gensalt(12)).decode()
    try:
        sb.table("usuarios").insert({
            "nome":       u["nome"],
            "email":      u["email"],
            "perfil":     u["perfil"],
            "senha_hash": senha_hash,
            "ativo":      True
        }).execute()
        print(f"  ✅ {u['nome']} ({u['email']}) — senha: {u['senha']}")
    except Exception as e:
        print(f"  ⚠️  {u['nome']} já existe ou erro: {e}")

print("\n⚠️  TROQUE AS SENHAS APÓS O PRIMEIRO LOGIN!\n")
print("  Admin:  admin@garra.local  /  garra@2026")
print("  Luana:  luana@garra.local  /  luana@2026")
print("  Arthur: arthur@garra.local /  arthur@2026")
print("  Breno:  breno@garra.local  /  breno@2026\n")
