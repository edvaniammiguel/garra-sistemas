# ═══════════════════════════════════════════════════════════
# main.py — API Garra Gestão v6 — UNIFICADO
# FastAPI + asyncpg + Neon PostgreSQL
# Módulos: Checklist + Jardinagem
# ═══════════════════════════════════════════════════════════

from fastapi import FastAPI, HTTPException, Depends, Header, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import asyncpg, bcrypt, os, json, time, secrets, smtplib, uuid, io, calendar
from dotenv import load_dotenv
load_dotenv()
import psycopg2, psycopg2.extras
import jwt as pyjwt
import requests as req_lib
from datetime import datetime, timedelta, date
from collections import defaultdict
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from PIL import Image

app = FastAPI(title="Garra Gestão API", version="6.0.0")  # main em admin/api/main.py

# ══════════════════════════════════════════════════════════════
# CORE — Refatoração Fase 1 (03/07/2026)
# Config, banco, auth, storage, helpers, models e permissões vivem
# em admin/api/core/. As rotas permanecem TODAS neste arquivo até a
# Fase 2 (routers). Golden test: auditoria_rotas.py (150 rotas).
# No Render o app sobe como 'admin.api.main:app' (raiz do repo);
# localmente como 'main:app' (de dentro de admin/api). A linha abaixo
# coloca admin/api no sys.path para 'core' resolver nos DOIS modos.
# ══════════════════════════════════════════════════════════════
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.config import (
    DATABASE_URL, FRONTEND_URL,
    SUPABASE_URL, SUPABASE_SERVICE_KEY, BUCKET_ATUAL,
    MAIL_HOST, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_CC, MAIL_DESTINO,
    JWT_SECRET, JWT_EXPIRY_HOURS, DEBUG_KEY, _debug_autorizado,
    JARD_DIR, STATIC_DIR, TEMPLATES_DIR, JARD_ICONS_DIR,
    ICONS_DIR, OPERACIONAL_STATIC_DIR, OPERACIONAL_DIR, CHECKLIST_DIR,
)
from core.db import get_db, get_jard_db, jard_query, jard_query_id
from core.storage import (
    storage_upload, storage_url, storage_delete, _URL_SUPABASE_EXPIRY,
    _checklist_extrair_fotos_para_storage, _checklist_assinar_fotos_para_leitura,
)
from core.auth import (
    check_rate_limit, _login_attempts,
    gerar_token_jard, verificar_token_jard, exigir_acesso_jardinagem,
    verificar_token, verificar_admin, verificar_gestor, validar_senha,
)
from core.helpers import comprimir_imagem, next_code, semanas_do_mes, enviar_email_smtp
from core.models import (
    LoginRequest, UsuarioCreate, UsuarioEdit, SenhaChange,
    SenhaResetRequest, SenhaResetConfirm, EnvioCreate, FrotaItem,
    ChecklistModeloCreate, LogMotoristaCreate, LogVeiculoCreate, LogRegistroCreate,
)
from core.webauthn import (
    WEBAUTHN_TABLES_SQL, gerar_opcoes_registro, verificar_registro,
    gerar_opcoes_login, verificar_login,
)
from core.permissions import (
    MODULOS_DISPONIVEIS, PERFIL_MODULOS_PADRAO, PERFIL_LABEL_SEED,
    PERFIS_TABLE_SQL, perfil_modulos_padrao,
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://garra-checklist-app.onrender.com",
        "https://garra-sistemas.onrender.com",
        "https://garra-jardinagem-app.onrender.com",  # Static Site PWA mobile
        "http://localhost:8000",   # Dev local
        "http://127.0.0.1:8000",  # Dev local alternativo
        "http://localhost:3000",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type","Authorization"],
)

# ── SECURITY HEADERS ──────────────────────────────────────────
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"]  = "nosniff"
    response.headers["X-Frame-Options"]         = "SAMEORIGIN"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]         = "strict-origin-when-cross-origin"
    return response

# ══════════════════════════════════════════════════════════════
# BANCO — DOIS MODOS
# asyncpg (async) → checklist
# psycopg2 (sync) → jardinagem (mantém compatibilidade)
# ══════════════════════════════════════════════════════════════

# ── STARTUP ───────────────────────────────────────────────────
@app.on_event("startup")
async def startup():
    """
    Startup resiliente: tenta conectar e rodar migrations, mas NUNCA derruba
    o app se o banco estiver dormindo/acordando ou com cota excedida.
    As migrations rodam na primeira conexão bem-sucedida; se falharem aqui,
    o app sobe mesmo assim (as tabelas já existem em produção) e o banco é
    acessado sob demanda nas rotas.
    """
    import asyncio
    conn = None
    # Tenta conectar com retry curto — banco pode estar "acordando"
    for tentativa in range(1, 4):
        try:
            conn = await asyncio.wait_for(asyncpg.connect(DATABASE_URL), timeout=10)
            break
        except Exception as e:
            print(f"[Startup] tentativa {tentativa}/3 de conexão falhou: {type(e).__name__}: {e}")
            if tentativa < 3:
                await asyncio.sleep(2 * tentativa)

    if conn is None:
        # Banco indisponível (dormindo, cota, rede). App sobe assim mesmo.
        # As rotas que precisam do banco vão tentar conectar sob demanda.
        print("[Startup] ⚠️ Banco indisponível no boot — app subindo sem migrations. "
              "Serão aplicadas na próxima vez que o banco responder.")
        return

    try:
        await conn.execute("SET search_path TO public, checklist, jardinagem")
        print("Garra Gestao - banco conectado")
        print("JARD_DIR:", JARD_DIR)
        print("TEMPLATES exists:", os.path.exists(TEMPLATES_DIR))
        print("STATIC exists:", os.path.exists(STATIC_DIR))

        # Migration: adicionar coluna medicao em operacional.tipos_servico
        try:
            await conn.execute("""
                ALTER TABLE operacional.tipos_servico
                ADD COLUMN IF NOT EXISTS medicao TEXT DEFAULT 'horimetro'
            """)
            await conn.execute("""
                ALTER TABLE operacional.tipos_servico
                ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE
            """)
            # Migration: adicionar qtd_metros em partes_diarias
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS qtd_metros NUMERIC(10,2)
            """)
            # Migration: coluna de diárias cobradas (ajuste pela Luana)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS quantidade_diarias_cobradas NUMERIC(8,1)
            """)
            # Migration: coluna fornecedor (terceiro/diarista/frete)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS fornecedor TEXT
            """)
            # Migration: flag de dia corrido (sem desconto de almoço)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS sem_almoco BOOLEAN DEFAULT false
            """)
            # Migration: nome do equipamento de terceiro (quando vínculo=terceiro)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS equipamento_terceiro TEXT
            """)
            # Migration: criar tabela regimes_cobranca
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS operacional.regimes_cobranca (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    nome TEXT NOT NULL UNIQUE,
                    descricao TEXT,
                    ativo BOOLEAN DEFAULT TRUE,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            # Inserir regimes padrão se tabela vazia
            await conn.execute("""
                INSERT INTO operacional.regimes_cobranca (nome, descricao)
                VALUES ('hora', 'Cobrança por hora trabalhada'),
                       ('diaria', 'Cobrança por diária'),
                       ('empreito', 'Valor fechado por empreita'),
                       ('metro', 'Cobrança por metro executado')
                ON CONFLICT (nome) DO NOTHING
            """)
            # Migration: log de ajuste de pontos
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS public.log_ajuste_pontos (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    usuario_login TEXT NOT NULL,
                    usuario_nome TEXT,
                    pts_antes INTEGER,
                    ajuste INTEGER NOT NULL,
                    pts_depois INTEGER,
                    motivo TEXT NOT NULL,
                    ajustado_por TEXT,
                    criado_em TIMESTAMP DEFAULT NOW()
                )
            """)
            # Migration: clientes_garra — garantir colunas de cadastro
            await conn.execute("""
                ALTER TABLE public.clientes_garra
                ADD COLUMN IF NOT EXISTS cnpj_cpf TEXT,
                ADD COLUMN IF NOT EXISTS telefone TEXT,
                ADD COLUMN IF NOT EXISTS email TEXT,
                ADD COLUMN IF NOT EXISTS endereco TEXT,
                ADD COLUMN IF NOT EXISTS cidade TEXT,
                ADD COLUMN IF NOT EXISTS uf TEXT,
                ADD COLUMN IF NOT EXISTS contato TEXT,
                ADD COLUMN IF NOT EXISTS observacao TEXT,
                ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP DEFAULT NOW()
            """)
            # Migration: equipamentos — garantir colunas para CRUD
            await conn.execute("""
                ALTER TABLE operacional.equipamentos
                ADD COLUMN IF NOT EXISTS marca TEXT,
                ADD COLUMN IF NOT EXISTS modelo TEXT,
                ADD COLUMN IF NOT EXISTS ano INTEGER,
                ADD COLUMN IF NOT EXISTS placa TEXT,
                ADD COLUMN IF NOT EXISTS operador_responsavel_id UUID REFERENCES public.usuarios_garra(id),
                ADD COLUMN IF NOT EXISTS ativo BOOLEAN DEFAULT TRUE,
                ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP DEFAULT NOW()
            """)
            print("[Migration] colunas medicao/ativo/qtd_metros/clientes/equipamentos OK")
        except Exception as me:
            print(f"[Migration] aviso (não-fatal): {me}")
    except Exception as e:
        print("Erro no startup (não-fatal):", e)
    finally:
        try:
            await conn.close()
        except Exception:
            pass

# ── STATIC FILES — JARDINAGEM ─────────────────────────────────
# Caminhos do repositório (JARD_DIR, STATIC_DIR, TEMPLATES_DIR, ICONS_DIR,
# OPERACIONAL_STATIC_DIR...) agora vivem em core/config.py — Fase 2.

if os.path.exists(STATIC_DIR):
    app.mount("/jardinagem/static", StaticFiles(directory=STATIC_DIR), name="jard_static")

# Ícones da jardinagem — o pwa-app.html referencia ./icons/ (= /jardinagem/icons/),
# mas os arquivos vivem em jardinagem/static/icons/. Sem este mount: 404 no ícone
# do PWA, favicons e logo do header.
if os.path.exists(JARD_ICONS_DIR):
    app.mount("/jardinagem/icons", StaticFiles(directory=JARD_ICONS_DIR), name="jard_icons")

# Ícones globais — servidos como /static/icons/ para todos os módulos
if os.path.exists(ICONS_DIR):
    app.mount("/static/icons", StaticFiles(directory=ICONS_DIR), name="static_icons")
    print(f"ICONS_DIR: {ICONS_DIR} (exists: True)")

# Operacional static files (idb.js, sw.js, offline-ui.js, etc)
if os.path.exists(OPERACIONAL_STATIC_DIR):
    # Rota dedicada para sw.js com header Service-Worker-Allowed: /
    # Permite o SW ter scope na raiz do site mesmo sendo servido de subpasta
    @app.get("/operacional/static/sw.js")
    async def serve_sw_js():
        return FileResponse(
            os.path.join(OPERACIONAL_STATIC_DIR, "sw.js"),
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/"}
        )
    app.mount("/operacional/static", StaticFiles(directory=OPERACIONAL_STATIC_DIR), name="operacional_static")
    print(f"OPERACIONAL_STATIC_DIR: {OPERACIONAL_STATIC_DIR} (exists: True)")

# Assets do checklist (css, js)
if os.path.exists(CHECKLIST_DIR):
    app.mount("/checklist/css",      StaticFiles(directory=os.path.join(CHECKLIST_DIR, "css")), name="checklist_css")
    app.mount("/checklist/js",       StaticFiles(directory=os.path.join(CHECKLIST_DIR, "js")),  name="checklist_js")
    app.mount("/checklist/icons",    StaticFiles(directory=os.path.join(CHECKLIST_DIR, "icons")), name="checklist_icons_sub")
    # Caminhos relativos do checklist quando carregado no iframe
    if os.path.exists(os.path.join(CHECKLIST_DIR, "css")):
        app.mount("/css", StaticFiles(directory=os.path.join(CHECKLIST_DIR, "css")), name="checklist_css_rel")
    if os.path.exists(os.path.join(CHECKLIST_DIR, "js")):
        app.mount("/js",  StaticFiles(directory=os.path.join(CHECKLIST_DIR, "js")),  name="checklist_js_rel")
    if os.path.exists(os.path.join(CHECKLIST_DIR, "icons")):
        app.mount("/icons", StaticFiles(directory=os.path.join(CHECKLIST_DIR, "icons")), name="checklist_icons_rel")







# ══════════════════════════════════════════════════════════════
# BIOMETRIA (WebAuthn) — login por digital/Face ID
# Sessão 1 (03/07/2026). Senha permanece SEMPRE como fallback.
# Helpers em core/webauthn.py; desafios persistidos no banco
# (WEB_CONCURRENCY=2 — memória não sobrevive entre workers).
# ══════════════════════════════════════════════════════════════

@app.on_event("startup")
async def criar_tabelas_webauthn():
    try:
        jard_query(WEBAUTHN_TABLES_SQL, fetch="none")
    except Exception as e:
        print(f"[WebAuthn] Falha ao criar tabelas: {e}")




































# ══════════════════════════════════════════════════════════════
# ROTAS JARDINAGEM — prefixo /jardinagem
# ══════════════════════════════════════════════════════════════

# ── PAGES ─────────────────────────────────────────────────────


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "admin", "admin-app.html")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Admin app não encontrado")
    return open(path, encoding="utf-8").read()










# ── AUTH JARDINAGEM ───────────────────────────────────────────



# ── MESES ─────────────────────────────────────────────────────





# ── SEMANAS ───────────────────────────────────────────────────






# ── PARES ─────────────────────────────────────────────────────




# ── FOTOS ─────────────────────────────────────────────────────





# ── RELATÓRIO DIÁRIO ──────────────────────────────────────────




# ── CONFIG ────────────────────────────────────────────────────



# ── PREVIEW RELATÓRIO ─────────────────────────────────────────




# ── DOWNLOAD EXCEL ────────────────────────────────────────────



# ═══════════════════════════════════════════════════════════════════════════
# MÓDULO OPERACIONAL — Ordens de Serviço, Partes Diárias, Comissões
# Adicionado em 09/06/2026 — Fase B
# Schema: operacional.* no Neon
# ═══════════════════════════════════════════════════════════════════════════

# ── HELPERS DE QUERY (usa jard_query — mesmo padrão da jardinagem) ──────────

# ── LISTAS BÁSICAS (para popular selects) ───────────────────────────────────






# ── REGIMES DE COBRANÇA ──────────────────────────────────────────────────────
















# ── NUMERAÇÃO DE OS ─────────────────────────────────────────────────────────



# ── ORDENS DE SERVIÇO ───────────────────────────────────────────────────────











# ── PARTES DIÁRIAS ──────────────────────────────────────────────────────────
























# ── CONTROLE MENSAL ───────────────────────────────────────────────────────────







# ═══════════════════════════════════════════════════════════════════════════
# FIM MÓDULO OPERACIONAL
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# MÓDULO PERMISSÕES — controle por colaborador
# ═══════════════════════════════════════════════════════════════════════════
# PERFIS CUSTOMIZADOS (persistência real — antes só existia em memória JS)
# ═══════════════════════════════════════════════════════════════════════════


@app.on_event("startup")
async def criar_tabela_perfis():
    try:
        jard_query(PERFIS_TABLE_SQL, fetch="none")
        existe = jard_query("SELECT COUNT(*) as c FROM public.perfis_customizados", fetch="one")
        if existe and existe.get("c", 0) == 0:
            for nome, modulos in PERFIL_MODULOS_PADRAO.items():
                label = PERFIL_LABEL_SEED.get(nome, nome.capitalize())
                jard_query(
                    "INSERT INTO public.perfis_customizados (nome, label, modulos) VALUES (%s, %s, %s)",
                    (nome, label, ",".join(modulos)), fetch="none"
                )
    except Exception:
        pass








# FIM PERFIS CUSTOMIZADOS
# ═══════════════════════════════════════════════════════════════════════════





# FIM MÓDULO PERMISSÕES
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# MURAL DE AVISOS
# ═══════════════════════════════════════════════════════════════════════════

# Tabela criada automaticamente no startup (ver evento startup)
MURAL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.mural_avisos (
    id SERIAL PRIMARY KEY,
    titulo TEXT NOT NULL,
    mensagem TEXT NOT NULL,
    perfis TEXT DEFAULT '',
    destinatario TEXT DEFAULT '',
    ativo BOOLEAN DEFAULT true,
    criado_em TIMESTAMP DEFAULT NOW(),
    criado_por TEXT DEFAULT ''
)
"""

@app.on_event("startup")
async def criar_tabela_mural():
    try:
        jard_query(MURAL_TABLE_SQL, fetch="none")
        # Garantir coluna destinatario (tabela pode já existir sem ela)
        jard_query("ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS destinatario TEXT DEFAULT ''", fetch="none")
    except Exception:
        pass

@app.get("/api/mural")
async def listar_mural(payload=Depends(verificar_token)):
    """Lista avisos ativos para o perfil/login do usuário."""
    perfil = payload.get("perfil", "")
    login = payload.get("login") or payload.get("sub", "")
    try:
        rows = jard_query(
            "SELECT id, titulo, mensagem, perfis, destinatario, criado_em, criado_por "
            "FROM public.mural_avisos WHERE ativo=true ORDER BY criado_em DESC",
            fetch="all"
        ) or []
    except Exception:
        # Coluna destinatario pode não existir ainda — migrar e tentar de novo
        try:
            jard_query("ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS destinatario TEXT DEFAULT ''", fetch="none")
        except Exception:
            pass
        rows = jard_query(
            "SELECT id, titulo, mensagem, perfis, destinatario, criado_em, criado_por "
            "FROM public.mural_avisos WHERE ativo=true ORDER BY criado_em DESC",
            fetch="all"
        ) or []
    result = []
    for r in rows:
        dest = (r.get("destinatario") or "").strip()
        perfis_str = (r.get("perfis") or "").strip()
        perfis_list = [p.strip() for p in perfis_str.split(",") if p.strip()]
        # Destinatário individual → só ele vê
        if dest:
            if dest != login:
                continue
        # Filtro por perfil → só os listados
        elif perfis_list:
            if perfil not in perfis_list:
                continue
        # Sem filtro → todos veem
        result.append({
            "id": r["id"],
            "titulo": r["titulo"],
            "mensagem": r["mensagem"],
            "perfis": perfis_str,
            "destinatario": dest,
            "criado_em": r["criado_em"].isoformat() if r["criado_em"] else "",
            "criado_por": r.get("criado_por") or "",
        })
    return result

class MuralCreate(BaseModel):
    titulo: str
    mensagem: str
    perfis: str = ""  # "" = todos, ou "operador,campo,motorista"
    destinatario: str = ""  # "" = usa perfis, ou "gilson@garra.local" = só ele

@app.post("/api/mural")
async def criar_aviso_mural(dados: MuralCreate, payload=Depends(verificar_admin)):
    """Admin cria aviso no mural."""
    jard_query(
        "INSERT INTO public.mural_avisos (titulo, mensagem, perfis, destinatario, criado_por) VALUES (%s, %s, %s, %s, %s)",
        (dados.titulo, dados.mensagem, dados.perfis, dados.destinatario, payload.get("nome", "")), fetch="none"
    )
    return {"ok": True}

@app.put("/api/mural/{aviso_id}")
async def editar_aviso_mural(aviso_id: int, dados: MuralCreate, payload=Depends(verificar_admin)):
    """Admin edita um aviso existente."""
    jard_query(
        "UPDATE public.mural_avisos SET titulo=%s, mensagem=%s, perfis=%s, destinatario=%s WHERE id=%s",
        (dados.titulo, dados.mensagem, dados.perfis, dados.destinatario, aviso_id), fetch="none"
    )
    return {"ok": True}

@app.delete("/api/mural/{aviso_id}")
async def desativar_aviso_mural(aviso_id: int, payload=Depends(verificar_admin)):
    """Admin desativa aviso (soft delete)."""
    jard_query(
        "UPDATE public.mural_avisos SET ativo=false WHERE id=%s",
        (aviso_id,), fetch="none"
    )
    return {"ok": True}

# FIM MURAL
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# MANUAL DO COLABORADOR (CARTILHA)
# ═══════════════════════════════════════════════════════════════════════════

CARTILHA_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS public.cartilha_blocos (
    id SERIAL PRIMARY KEY,
    ordem INTEGER NOT NULL DEFAULT 0,
    titulo TEXT NOT NULL,
    subtitulo TEXT DEFAULT '',
    conteudo TEXT NOT NULL DEFAULT '',
    ativo BOOLEAN DEFAULT true,
    atualizado_em TIMESTAMP DEFAULT NOW()
)
"""

CARTILHA_SEED = [
    (1, "Boas-vindas", "", "É com entusiasmo que nós da Garra Terraplenagem recebemos você em nossa casa. Você está ingressando em uma empresa sólida, que atua com excelência no setor da construção civil e é reconhecida pela qualidade de seus serviços, comprometimento com clientes e respeito às pessoas e ao meio ambiente.\n\nSe chegou até aqui é porque confiamos na sua capacidade em fazer a diferença. Conte conosco no seu desenvolvimento profissional."),
    (2, "Quem somos", "Nossa história", "A Garra Terraplenagem é uma empresa especializada em soluções para o setor da construção civil, com sede em Pará de Minas – MG e atuação em todo o território nacional. Fundada em 2016, construiu sua trajetória com base na excelência operacional, segurança e compromisso socioambiental.\n\nCom uma frota moderna, equipe qualificada e processos alinhados às melhores práticas do mercado, já executamos dezenas de projetos de grande porte.\n\nEm 2020, ampliamos nosso portfólio com a licença ambiental para operação de área de bota-fora, reforçando nosso papel na gestão responsável de resíduos da construção civil."),
    (3, "Missão, Visão e Valores", "", "**Missão**\nGerar valor para nossos clientes.\n\n**Visão**\nPreparamos terrenos para gerar valor ao nosso cliente e nos tornar referência no segmento.\n\n**Valores**\nRespeito, senso colaborativo, equipe, cuidado com o meio ambiente começando por nossa empresa."),
    (4, "Nossos Serviços", "", "- Terraplenagem\n- Demolição\n- Disk Entulho\n- Escavação\n- Destoca de Eucalipto\n- Furação para Tubulões\n- Limpeza de Lagoas\n- Biodigestores"),
    (5, "Deveres do Colaborador", "", "**1. Assiduidade e Pontualidade**\nComparecer pontualmente ao local de trabalho é obrigação fundamental, sendo critério relevante de desempenho e conduta profissional.\n\n**2. Registro de Ponto**\nÉ obrigatório registrar o ponto no início da jornada, no início e término do intervalo para refeição e ao final do expediente, respeitando a tolerância de até 5 minutos.\n\n**3. Envio de Atestado Médico**\nEm caso de ausência por motivo de saúde, o atestado deve ser enviado imediatamente via WhatsApp ao Departamento Pessoal e entregue fisicamente no retorno.\n\n**4. Uso e Higiene do Uniforme**\nO uso do uniforme fornecido pela empresa é obrigatório. O colaborador é responsável por mantê-lo em boas condições de higiene e conservação.\n\n**5. Cumprimento de Ordens**\nExecutar com zelo as tarefas designadas, observando as ordens da liderança, as normas internas e os padrões de qualidade exigidos.\n\n**6. Uso de Celular Pessoal**\nO colaborador poderá utilizar seu celular pessoal para fins profissionais durante a jornada, somente para uso profissional, visando agilidade e eficiência dos processos.\n\n**7. Segurança do Trabalho e Trânsito**\nCumprir todas as normas de segurança, incluindo o uso obrigatório de EPIs, bem como obedecer às leis de trânsito nas atividades externas.\n\n**8. Acidentes de Trânsito**\nComunicar imediatamente à empresa, podendo acionar o setor administrativo, liderança e as autoridades competentes (Bombeiro, PM e afins).\n\n**9. Multas de Trânsito**\nMultas por infrações do condutor serão de responsabilidade do colaborador, salvo falha mecânica previamente reportada por escrito no checklist.\n\n**10. Atualização de Dados**\nManter sempre atualizadas junto ao Departamento Pessoal as informações de endereço, telefone, estado civil e dependentes.\n\n**11. Limpeza e Organização**\nZelar pela higiene e conservação dos ambientes, especialmente banheiros, refeitórios e áreas operacionais.\n\n**12. Saídas fora de Férias**\nAusências por motivos particulares devem ser previamente autorizadas, sob pena de serem consideradas faltas injustificadas.\n\n**13. Comunicação de Faltas**\nFaltas devem ser comunicadas antecipadamente ao superior imediato, salvo emergências.\n\n**14. Exames Ocupacionais**\nSubmeter-se aos exames admissionais, periódicos, de retorno, de mudança de função ou demissionais.\n\n**15. Respeito Interpessoal**\nTratar colegas, superiores, clientes e terceiros com respeito, cordialidade e profissionalismo.\n\n**16. Devolução de Materiais**\nNo desligamento, devolver EPIs, uniformes, ferramentas e demais equipamentos. A não devolução poderá implicar desconto na rescisão."),
    (6, "Direitos do Colaborador", "", "**1. Pagamento de Salário**\nEfetuado até o 5º dia útil de cada mês. 13º em duas parcelas: 1ª até 30/nov, 2ª até 20/dez (com descontos de INSS e IRRF).\n\n**2. Adiantamento Salarial**\nAté 30% do salário, realizado no dia 20 de cada mês, mediante comunicação prévia ao DP.\n\n**3. Uniforme Fornecido**\nA empresa fornece os uniformes necessários ao exercício das funções.\n\n**4. Acesso a Dados Cadastrais**\nDireito de acesso e atualização dos dados mantidos pela empresa, conforme a LGPD.\n\n**5. Exames Gratuitos**\nTodos os exames ocupacionais exigidos por lei serão realizados sem custo para o colaborador.\n\n**6. Faltas Legais**\n2 dias por falecimento (avós, pais, filhos, esposa) · 3 dias por casamento · 5 dias por nascimento de filho(a) · 1 dia/ano para doação de sangue · Dias de prova vestibular · Dobro de dias de convocação eleitoral · 1 dia/ano para levar filho ao médico · 2 dias para pré-natal da esposa · Gestante: mínimo 6 consultas. Todos mediante documento comprobatório.\n\n**7. Licença Maternidade**\n120 dias, podendo a mãe escolher se tira tempo antes do parto, sem prejuízo do emprego e salário.\n\n**8. Licença Paternidade**\n5 dias consecutivos a partir do dia útil ao da data de nascimento, sem prejuízo de remuneração.\n\n**9. Férias**\nA cada 12 meses, podendo ser dividida em até 3 períodos (um ≥ 14 dias, demais ≥ 5 dias cada). Afastamento INSS > 6 meses = perda de férias proporcionais."),
    (7, "Práticas não permitidas", "", "**1. Informações Confidenciais**\nProibido divulgar dados de clientes, fornecedores, contratos, valores, processos ou qualquer conteúdo obtido em razão do vínculo.\n\n**2. Comportamento na Área de Alimentação**\nManter silêncio e respeito. Vedado consumo de bebida alcoólica e uso de cigarro nas áreas de alimentação.\n\n**3. Saída sem Autorização**\nVedado ausentar-se durante o expediente sem autorização prévia do superior imediato.\n\n**4. Uso de Equipamentos fora da Empresa**\nSó com autorização formal da liderança ou diretoria. Uso indevido = penalidades.\n\n**5. Serviços Particulares com Recursos da Empresa**\nProibido executar serviços pessoais utilizando máquinas, materiais ou tempo de trabalho da empresa. Pode ensejar justa causa.\n\n**6. Pessoas não Autorizadas**\nProibido trazer familiares, amigos ou crianças ao ambiente de trabalho ou obras sem autorização.\n\n**7. Lavagem de Veículos Particulares**\nNão permitida nas dependências da empresa, salvo com autorização escrita da direção.\n\n**8. Veículos da Empresa para Uso Pessoal**\nProibido caronas a não-funcionários, transportar menores ou usar para fins pessoais.\n\n**9. Uso Indevido de Conhecimentos**\nVedado utilizar conhecimento técnico, metodologia ou informação da empresa para fins pessoais ou repassar a terceiros.\n\n**10. Comercialização**\nNão é permitida a comercialização de quaisquer produtos ou serviços durante a jornada."),
    (8, "Punições", "", "Colaboradores que infringirem as normas estão sujeitos a: **advertência verbal**, **advertência escrita**, **suspensão** ou **dispensa por justa causa**. A ordem da penalidade pode ser aplicada de acordo com a gravidade do fato."),
    (9, "Conduta Ética", "", "- Banheiro: pense no próximo. Dê a descarga, cuide do assento, da pia e do chão.\n- Jogue o papel higiênico no lixo, não no vaso.\n- Ao sair, não deixe torneira aberta ou pingando, nem a luz acesa.\n- Limpe a mesa ao acabar a refeição. Higienize e guarde seus utensílios.\n- Não jogue restos de comida na pia. A cozinha é de uso coletivo.\n- Tenha sua garrafinha reutilizável. Um copo descartável leva até 450 anos para se decompor.\n- Respeite as diferenças e os espaços das outras pessoas.\n\nA empresa não se responsabiliza por objetos ou valores deixados nas dependências."),
    (10, "Canais de comunicação", "", "**Comunicação interna:** (37) 99153-1090\n**Comunicação externa:** (37) 3236-8625 | (37) 99971-8000\n**Site:** garraterraplenagem.com.br\n**Instagram:** @garraterraplenagem\n**Endereço:** Av. Santos Dumont, 346 – São Cristóvão, Pará de Minas - MG"),
]

@app.on_event("startup")
async def criar_tabela_cartilha():
    try:
        jard_query(CARTILHA_TABLE_SQL, fetch="none")
        existe = jard_query("SELECT COUNT(*) as c FROM public.cartilha_blocos", fetch="one")
        if existe and existe.get("c", 0) == 0:
            for ordem, titulo, subtitulo, conteudo in CARTILHA_SEED:
                jard_query(
                    "INSERT INTO public.cartilha_blocos (ordem, titulo, subtitulo, conteudo) VALUES (%s, %s, %s, %s)",
                    (ordem, titulo, subtitulo, conteudo), fetch="none"
                )
    except Exception:
        pass

@app.get("/api/cartilha")
async def listar_cartilha(payload=Depends(verificar_token)):
    """Lista blocos ativos do manual, em ordem — qualquer usuário logado pode ler."""
    rows = jard_query(
        "SELECT id, ordem, titulo, subtitulo, conteudo, atualizado_em "
        "FROM public.cartilha_blocos WHERE ativo=true ORDER BY ordem ASC, id ASC",
        fetch="all"
    ) or []
    return [
        {
            "id": r["id"], "ordem": r["ordem"], "titulo": r["titulo"],
            "subtitulo": r.get("subtitulo") or "", "conteudo": r["conteudo"],
        }
        for r in rows
    ]

class CartilhaBloco(BaseModel):
    ordem: int = 0
    titulo: str
    subtitulo: str = ""
    conteudo: str

@app.post("/api/cartilha")
async def criar_bloco_cartilha(dados: CartilhaBloco, payload=Depends(verificar_admin)):
    """Admin cria novo bloco no manual."""
    jard_query(
        "INSERT INTO public.cartilha_blocos (ordem, titulo, subtitulo, conteudo) VALUES (%s, %s, %s, %s)",
        (dados.ordem, dados.titulo, dados.subtitulo, dados.conteudo), fetch="none"
    )
    return {"ok": True}

@app.put("/api/cartilha/{bloco_id}")
async def editar_bloco_cartilha(bloco_id: int, dados: CartilhaBloco, payload=Depends(verificar_admin)):
    """Admin edita um bloco existente."""
    jard_query(
        "UPDATE public.cartilha_blocos SET ordem=%s, titulo=%s, subtitulo=%s, conteudo=%s, atualizado_em=NOW() WHERE id=%s",
        (dados.ordem, dados.titulo, dados.subtitulo, dados.conteudo, bloco_id), fetch="none"
    )
    return {"ok": True}

@app.delete("/api/cartilha/{bloco_id}")
async def excluir_bloco_cartilha(bloco_id: int, payload=Depends(verificar_admin)):
    """Admin remove um bloco definitivamente."""
    jard_query("DELETE FROM public.cartilha_blocos WHERE id=%s", (bloco_id,), fetch="none")
    return {"ok": True}

# FIM CARTILHA
# ═══════════════════════════════════════════════════════════════════════════




@app.get("/mobile")
async def mobile_app():
    return FileResponse(os.path.join(os.path.dirname(__file__), "../../operacional/static/mobile.html"))

@app.get("/reset-senha.html")
async def reset_senha_page():
    """Página que o link de 'Esqueci minha senha' abre. Estava ausente do
    repositório — o email de reset era enviado mas o link dava 404."""
    return FileResponse(os.path.join(os.path.dirname(__file__), "reset-senha.html"))

@app.get("/mobile/sw.js")
async def mobile_sw():
    return FileResponse(
        os.path.join(os.path.dirname(__file__), "../../operacional/static/sw.js"),
        media_type="application/javascript"
    )

@app.get("/mobile/manifest.json")
async def mobile_manifest():
    return FileResponse(os.path.join(os.path.dirname(__file__), "../../operacional/static/mobile.manifest.json"))

# ── FALLBACK — compatibilidade com browsers que cachearam URLs antigas ───────
from fastapi.responses import RedirectResponse

@app.get("/manifest.json")
async def redirect_manifest():
    return RedirectResponse(url="/mobile/manifest.json")

@app.get("/sw.js")
async def redirect_sw():
    return RedirectResponse(url="/mobile/sw.js")

@app.get("/favicon.ico")
async def redirect_favicon():
    return RedirectResponse(url="/static/icons/favicon.ico")

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    from fastapi.responses import JSONResponse, Response
    path = request.url.path
    if path.startswith("/jardinagem/api/") or path.startswith("/api/") or path.startswith("/auth/") or path.startswith("/usuarios") or path.startswith("/checklist/") or path.startswith("/frota") or path.startswith("/logistica/") or path.startswith("/operacional/"):
        return JSONResponse({"ok": False, "error": "Rota não encontrada", "path": path}, status_code=404)
    return Response(status_code=404)

@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    from fastapi.responses import JSONResponse, Response
    path = request.url.path
    if path.startswith("/jardinagem/api/") or path.startswith("/api/") or path.startswith("/auth/") or path.startswith("/usuarios") or path.startswith("/checklist/") or path.startswith("/frota") or path.startswith("/logistica/") or path.startswith("/operacional/"):
        return JSONResponse({"ok": False, "error": "Erro interno do servidor"}, status_code=500)
    return Response(status_code=500)
    raise exc

# ── HEALTH CHECK — mantém banco Neon acordado ──────────────────
@app.get("/api/health")
async def health():
    """
    Health check LEVE — não toca no banco.
    Serve apenas para o Render saber que o processo está vivo.
    NÃO usar este endpoint em cron de keep-alive contra o banco:
    manter o Neon acordado 24/7 estoura a cota de compute do free tier.
    Para testar o banco use /api/health/db (manual).
    """
    return {"status": "ok", "sistema": "Garra Gestão API", "app": "vivo"}

@app.get("/api/health/db")
async def health_db():
    """Testa a conexão com o banco — uso manual de diagnóstico, NÃO em cron."""
    try:
        jard_query("SELECT 1", fetch="one")
        return {"status": "ok", "db": "conectado"}
    except Exception as e:
        return {"status": "erro", "db": str(e)}

@app.get("/api/debug/jardinagem-pares")
async def debug_jard_pares(mes: str = "", chave: str = ""):
    """Diagnóstico de pares: duplicados, vazios e sequência.
    Uso: ?chave=DEBUG_KEY (opcional &mes=ID_DO_MES)"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")
    filtro = "AND s.mes_id = %s" if mes else ""
    params = (mes,) if mes else ()
    pares = jard_query(
        f"""SELECT p.id, p.codigo_a, p.codigo_d, p.local_nome, p.semana_id,
                   s.label AS semana_label, s.mes_id,
                   (SELECT COUNT(*) FROM jardinagem.fotos f
                    WHERE f.par_id = p.id) AS num_fotos
            FROM jardinagem.pares p
            LEFT JOIN jardinagem.semanas s ON s.id = p.semana_id
            WHERE (p.ativo IS NULL OR p.ativo=true) {filtro}
            ORDER BY p.codigo_a, p.id""",
        params
    )
    pares = [dict(p) for p in (pares or [])]
    # Detectar duplicados de codigo_a
    vistos = {}
    duplicados = []
    vazios = []
    for p in pares:
        ca = p.get("codigo_a")
        if ca in vistos:
            duplicados.append({"codigo_a": ca, "ids": [vistos[ca], p["id"]]})
        else:
            vistos[ca] = p["id"]
        if not p.get("num_fotos"):
            vazios.append({"id": p["id"], "codigo_a": ca, "local": p.get("local_nome")})
    cfg = jard_query("SELECT valor FROM jardinagem.config WHERE chave='next_code'", fetch="one")
    return {
        "total_pares": len(pares),
        "next_code_config": cfg["valor"] if cfg else None,
        "duplicados": duplicados,
        "vazios_sem_foto": vazios,
        "pares": pares
    }

@app.get("/api/debug/os")
async def debug_os(numero: str = "", chave: str = ""):
    """Diagnóstico de uma OS e suas partes. Uso: ?numero=OS-2026-0005&chave=DEBUG_KEY"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")
    os_row = jard_query(
        """SELECT id, numero, obra, regime_cobranca, valor_combinado, status,
                  equipamento_id, operador_id, tipo_servico_id, data_inicio
           FROM operacional.ordens_servico WHERE numero=%s""",
        (numero,), fetch="one"
    )
    if not os_row:
        return {"erro": "OS não encontrada", "numero": numero}
    partes = jard_query(
        """SELECT id, data, tipo_medicao,
                  horimetro_inicial, horimetro_final, horas_trabalhadas, horas_cobradas,
                  km_inicial, km_final, km_percorrido, qtd_viagens, qtd_metros,
                  hora_inicio, hora_fim, observacao, criado_em
           FROM operacional.partes_diarias
           WHERE os_id=%s AND ativo=true
           ORDER BY data, criado_em""",
        (os_row["id"],)
    )
    return {
        "os": dict(os_row),
        "total_partes": len(partes or []),
        "partes": [dict(p) for p in (partes or [])]
    }

@app.get("/api/debug/equipamentos")
async def debug_equipamentos(codigo: str = "", chave: str = ""):
    """Diagnóstico de equipamentos e responsável. Uso: ?chave=DEBUG_KEY (ou &codigo=CB-037)"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")
    filtro = "WHERE eq.codigo=%s" if codigo else ""
    params = (codigo,) if codigo else ()
    rows = jard_query(
        f"""SELECT eq.codigo, eq.descricao, eq.categoria, eq.medicao,
                   eq.operador_responsavel_id, resp.nome AS responsavel_nome,
                   eq.ativo
            FROM operacional.equipamentos eq
            LEFT JOIN public.usuarios_garra resp ON resp.id = eq.operador_responsavel_id
            {filtro}
            ORDER BY eq.codigo""",
        params
    )
    return {"total": len(rows or []), "equipamentos": [dict(r) for r in (rows or [])]}

@app.get("/api/debug/usuarios")
async def debug_usuarios(chave: str = "", authorization: Optional[str] = Header(None)):
    """Diagnóstico de usuários. Acesso: admin logado OU chave de diagnóstico."""
    # Permite acesso com chave de diagnóstico (para resolver problema de login)
    autorizado = _debug_autorizado(chave)
    if not autorizado:
        # Senão exige admin via token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Não autenticado")
        try:
            payload = pyjwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
            if payload.get("perfil") != "admin":
                raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
        except pyjwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Token inválido")
    rows = jard_query(
        """SELECT login, nome, email, perfil, perfil_checklist, ativo,
                  LEFT(senha_hash,7) AS hash_inicio,
                  CASE WHEN senha_hash = '$2b$12$y4jgMhNSKtoeBtad7lKEOev.tHk8S9OA1SpPHrowz5XT.AQJK.iZK'
                       THEN 'padrao_1234' ELSE 'outra' END AS senha_status
           FROM public.usuarios_garra
           ORDER BY ativo DESC, perfil, login""",
        fetch="all"
    )
    return [dict(r) for r in (rows or [])]

@app.get("/api/debug/sistema")
async def debug_sistema(chave: str = ""):
    """Diagnóstico completo do sistema — passo a passo de todas as áreas.
    Uso: /api/debug/sistema?chave=DEBUG_KEY"""
    if not _debug_autorizado(chave):
        raise HTTPException(status_code=403, detail="Chave inválida")

    import datetime as _dt
    rel = {"timestamp": _dt.datetime.now().isoformat(), "checks": []}

    def add(area, item, ok, detalhe=""):
        rel["checks"].append({
            "area": area, "item": item,
            "status": "OK" if ok else "FALHA", "detalhe": str(detalhe)
        })

    # 1. BANCO — conexão
    try:
        r = jard_query("SELECT 1 AS ok", fetch="one")
        add("banco", "conexão Neon", bool(r), "conectado")
    except Exception as e:
        add("banco", "conexão Neon", False, e)

    # 2. TABELAS essenciais existem
    tabelas = [
        ("public", "usuarios_garra"),
        ("operacional", "ordens_servico"),
        ("operacional", "equipamentos"),
        ("operacional", "partes_diarias"),
        ("jardinagem", "meses"),
        ("jardinagem", "semanas"),
        ("jardinagem", "pares"),
        ("jardinagem", "fotos"),
    ]
    for sch, tab in tabelas:
        try:
            n = jard_query(
                f"SELECT COUNT(*) AS n FROM {sch}.{tab}", fetch="one"
            )
            add("tabelas", f"{sch}.{tab}", True, f"{n['n']} registros")
        except Exception as e:
            add("tabelas", f"{sch}.{tab}", False, e)

    # 3. USUÁRIOS — quantos ativos e perfis
    try:
        us = jard_query(
            "SELECT perfil, COUNT(*) AS n FROM public.usuarios_garra "
            "WHERE ativo=true GROUP BY perfil ORDER BY perfil",
            fetch="all"
        )
        perfis = {u["perfil"]: u["n"] for u in (us or [])}
        add("usuarios", "ativos por perfil", bool(perfis), perfis)
    except Exception as e:
        add("usuarios", "ativos por perfil", False, e)

    # 4. JARDINAGEM — integridade dos pares (duplicados/vazios)
    try:
        pares = jard_query(
            "SELECT codigo_a, codigo_d FROM jardinagem.pares "
            "WHERE (ativo IS NULL OR ativo=true) ORDER BY codigo_a",
            fetch="all"
        )
        pares = [dict(p) for p in (pares or [])]
        codigos = [p["codigo_a"] for p in pares if p["codigo_a"]]
        dups = [c for c in set(codigos) if codigos.count(c) > 1]
        vazios = [p for p in pares if not p.get("codigo_a")]
        # buracos na sequência
        nums = sorted(set(int(c) for c in codigos if str(c).isdigit()))
        buracos = []
        if nums:
            for x in range(nums[0], nums[-1] + 1):
                if x not in nums:
                    buracos.append(x)
        ok = (len(dups) == 0 and len(vazios) == 0 and len(buracos) == 0)
        add("jardinagem", "integridade pares", ok, {
            "total": len(pares),
            "duplicados": dups,
            "vazios": len(vazios),
            "buracos": buracos[:10],
            "faixa": f"{nums[0]}–{nums[-1]}" if nums else "—",
        })
    except Exception as e:
        add("jardinagem", "integridade pares", False, e)

    # 5. JARDINAGEM — next_code coerente
    try:
        cfg = jard_query(
            "SELECT valor FROM jardinagem.config WHERE chave='next_code'",
            fetch="one"
        )
        maxc = jard_query(
            "SELECT MAX(codigo_d) AS m FROM jardinagem.pares WHERE ativo",
            fetch="one"
        )
        nc = int(cfg["valor"]) if cfg else None
        mc = maxc["m"] if maxc else None
        ok = (nc is not None and mc is not None and nc > mc)
        add("jardinagem", "next_code", ok,
            f"next_code={nc}, max_codigo_d={mc}")
    except Exception as e:
        add("jardinagem", "next_code", False, e)

    # 6. JARDINAGEM — trava UNIQUE anti-duplicação ativa
    try:
        idx = jard_query(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname='jardinagem' AND tablename='pares' "
            "AND indexname='uq_pares_codigo_a_ativo'",
            fetch="one"
        )
        add("jardinagem", "trava UNIQUE", bool(idx),
            "ativa" if idx else "AUSENTE — risco de duplicação")
    except Exception as e:
        add("jardinagem", "trava UNIQUE", False, e)

    # 7. OPERACIONAL — colunas novas existem
    try:
        cols = jard_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='operacional' AND table_name='partes_diarias' "
            "AND column_name IN ('sem_almoco','fornecedor','equipamento_terceiro')",
            fetch="all"
        )
        nomes = {c["column_name"] for c in (cols or [])}
        faltam = {"sem_almoco", "fornecedor", "equipamento_terceiro"} - nomes
        add("operacional", "colunas partes_diarias", not faltam,
            "todas presentes" if not faltam else f"faltam: {faltam}")
    except Exception as e:
        add("operacional", "colunas partes_diarias", False, e)

    # 8. OPERACIONAL — operador_responsavel em equipamentos
    try:
        col = jard_query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='operacional' AND table_name='equipamentos' "
            "AND column_name='operador_responsavel_id'",
            fetch="one"
        )
        add("operacional", "operador_responsavel_id", bool(col),
            "presente" if col else "AUSENTE")
    except Exception as e:
        add("operacional", "operador_responsavel_id", False, e)

    # Resumo
    falhas = [c for c in rel["checks"] if c["status"] == "FALHA"]
    rel["resumo"] = {
        "total_checks": len(rel["checks"]),
        "ok": len(rel["checks"]) - len(falhas),
        "falhas": len(falhas),
        "areas_com_falha": sorted(set(c["area"] for c in falhas)),
    }
    return rel

@app.get("/")
async def root():
    return RedirectResponse(url="/admin")



# ══════════════════════════════════════════════════════════════
# ROUTERS — Refatoração Fase 2 (incluídos ao final: preserva a
# ordem de matching original, após todos os mounts estáticos)
# ══════════════════════════════════════════════════════════════
from routers.jardinagem import router as jardinagem_router
from routers.operacional import router as operacional_router
from routers.checklist import router as checklist_router
from routers.auth import router as auth_router
app.include_router(jardinagem_router)
app.include_router(operacional_router)
app.include_router(checklist_router)
app.include_router(auth_router)
