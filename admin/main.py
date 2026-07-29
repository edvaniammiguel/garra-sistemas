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
from core.db import get_db, get_jard_db, jard_query, jard_query_id, ajard_query, ajard_query_id
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
            # Migration: "considerar X horas" padrão por OS (planilha da Luana)
            await conn.execute("""
                ALTER TABLE operacional.ordens_servico
                ADD COLUMN IF NOT EXISTS horas_padrao_dia NUMERIC(5,2)
            """)
            # Migration: flag de dia corrido (sem desconto de almoço)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS sem_almoco BOOLEAN DEFAULT false
            """)
            # Migration: VALOR por dia/parte (fechamento do Combinado — Edvania
            # define no Controle Mensal; base da comissão exata)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS valor NUMERIC(12,2)
            """)
            # Migration: nome do equipamento de terceiro (quando vínculo=terceiro)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS equipamento_terceiro TEXT
            """)
            # (08/07/2026) Blindagem de schema: TODAS as colunas usadas pelo
            # INSERT de partes garantidas no startup (existiam só por criação
            # manual — dev/prod sempre em paridade a partir daqui).
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS vinculo_operador TEXT DEFAULT 'proprio'
            """)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS operador_nome_avulso TEXT
            """)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS trajeto TEXT
            """)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS por_conta_de TEXT DEFAULT 'empresa'
            """)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS quantidade_diarias NUMERIC(8,1) DEFAULT 0
            """)
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS qtd_viagens NUMERIC(8,1) DEFAULT 0
            """)
            # (08/07/2026) Idempotência do registro de parte: client_id gerado
            # no celular + índice único parcial. Retry da fila offline não
            # duplica mais registro quando a resposta se perde na rede.
            await conn.execute("""
                ALTER TABLE operacional.partes_diarias
                ADD COLUMN IF NOT EXISTS client_id TEXT
            """)
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_partes_client_id
                ON operacional.partes_diarias (client_id)
                WHERE client_id IS NOT NULL
            """)
            # (13/07/2026) PREÇOS POR MEDIÇÃO na OS — prática real da Garra:
            # OS mista (ex.: metros + horas de concha) precisa de um preço por
            # tipo de medida. valor_combinado vira legado/espelho do regime.
            # (15/07/2026) Tabela de preços: valor padrão por regime — Nova OS
            # nasce precificada (snapshot na OS; reajuste não retroage)
            await conn.execute("""
                ALTER TABLE operacional.regimes_cobranca
                ADD COLUMN IF NOT EXISTS valor_padrao NUMERIC(12,2)
            """)
            for col in ("valor_hora", "valor_metro", "valor_diaria", "valor_km", "valor_viagem"):
                await conn.execute(f"""
                    ALTER TABLE operacional.ordens_servico
                    ADD COLUMN IF NOT EXISTS {col} NUMERIC(12,2)
                """)
            # Migração única: copia o valor_combinado para a coluna do regime
            await conn.execute("""
                UPDATE operacional.ordens_servico SET
                  valor_hora   = CASE WHEN lower(coalesce(regime_cobranca,'')) LIKE '%hora%'  AND valor_hora   IS NULL THEN valor_combinado ELSE valor_hora   END,
                  valor_metro  = CASE WHEN lower(coalesce(regime_cobranca,'')) LIKE '%metro%' AND valor_metro  IS NULL THEN valor_combinado ELSE valor_metro  END,
                  valor_diaria = CASE WHEN lower(coalesce(regime_cobranca,'')) LIKE '%diari%' AND valor_diaria IS NULL THEN valor_combinado ELSE valor_diaria END,
                  valor_km     = CASE WHEN lower(coalesce(regime_cobranca,'')) LIKE '%km%'    AND valor_km     IS NULL THEN valor_combinado ELSE valor_km     END
                WHERE valor_combinado IS NOT NULL
            """)
            # (13/07/2026) Idempotência também na CRIAÇÃO de OS avulsa —
            # duplo toque/retry da fila nunca mais cria duas OS.
            await conn.execute("""
                ALTER TABLE operacional.ordens_servico
                ADD COLUMN IF NOT EXISTS client_id TEXT
            """)
            await conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_os_client_id
                ON operacional.ordens_servico (client_id)
                WHERE client_id IS NOT NULL
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
        await ajard_query(WEBAUTHN_TABLES_SQL, fetch="none")
    except Exception as e:
        print(f"[WebAuthn] Falha ao criar tabelas: {e}")




































# ══════════════════════════════════════════════════════════════
# ROTAS JARDINAGEM — prefixo /jardinagem
# ══════════════════════════════════════════════════════════════

# ── PAGES ─────────────────────────────────────────────────────












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
        await ajard_query(PERFIS_TABLE_SQL, fetch="none")
        existe = await ajard_query("SELECT COUNT(*) as c FROM public.perfis_customizados", fetch="one")
        if existe and existe.get("c", 0) == 0:
            for nome, modulos in PERFIL_MODULOS_PADRAO.items():
                label = PERFIL_LABEL_SEED.get(nome, nome.capitalize())
                await ajard_query(
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
        await ajard_query(MURAL_TABLE_SQL, fetch="none")
        # Garantir coluna destinatario (tabela pode já existir sem ela)
        await ajard_query("ALTER TABLE public.mural_avisos ADD COLUMN IF NOT EXISTS destinatario TEXT DEFAULT ''", fetch="none")
    except Exception:
        pass






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
        await ajard_query(CARTILHA_TABLE_SQL, fetch="none")
        existe = await ajard_query("SELECT COUNT(*) as c FROM public.cartilha_blocos", fetch="one")
        if existe and existe.get("c", 0) == 0:
            for ordem, titulo, subtitulo, conteudo in CARTILHA_SEED:
                await ajard_query(
                    "INSERT INTO public.cartilha_blocos (ordem, titulo, subtitulo, conteudo) VALUES (%s, %s, %s, %s)",
                    (ordem, titulo, subtitulo, conteudo), fetch="none"
                )
    except Exception:
        pass






# FIM CARTILHA
# ═══════════════════════════════════════════════════════════════════════════








# ── FALLBACK — compatibilidade com browsers que cachearam URLs antigas ───────
from fastapi.responses import RedirectResponse




# Prefixos de API: erro sempre em JSON (os apps consomem {detail}/{error}).
# Qualquer outra rota (páginas) recebe página de erro amigável no padrão Garra.
_PREFIXOS_API = ("/jardinagem/api/", "/api/", "/auth/", "/usuarios",
                 "/checklist/", "/frota", "/logistica/", "/operacional/",
                 "/manutencao/api/", "/compras/api/")

def _eh_api(path: str) -> bool:
    return any(path.startswith(p) for p in _PREFIXOS_API)

def _pagina_erro(codigo: int, titulo: str, msg: str) -> str:
    return f"""<!DOCTYPE html><html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{codigo} · Garra Sistemas</title><style>
body{{margin:0;font-family:system-ui,-apple-system,sans-serif;background:#F0F4FF;color:#1E293B;
display:flex;align-items:center;justify-content:center;min-height:100dvh;padding:20px}}
.box{{background:#fff;border:1px solid #CBD5E1;border-top:4px solid #E8820C;border-radius:14px;
padding:34px 28px;max-width:420px;width:100%;text-align:center}}
.cod{{font-size:44px;font-weight:800;color:#1A2A5E}}
h1{{font-size:17px;color:#1A2A5E;margin:6px 0 8px}}
p{{font-size:13px;color:#64748B;line-height:1.5;margin:0 0 20px}}
.btns{{display:flex;gap:10px;justify-content:center;flex-wrap:wrap}}
a,button{{text-decoration:none;border:none;border-radius:8px;padding:11px 18px;font-size:14px;
font-weight:700;cursor:pointer}}
.b1{{background:#E8820C;color:#fff}}
.b2{{background:#fff;color:#1A2A5E;border:1px solid #CBD5E1}}
.rodape{{margin-top:18px;font-size:11px;color:#94A3B8}}
</style></head><body><div class="box">
<div class="cod">{codigo}</div>
<h1>{titulo}</h1>
<p>{msg}</p>
<div class="btns">
  <button class="b2" onclick="history.back()">← Voltar</button>
  <a class="b1" href="/mobile">Ir para o app</a>
  <a class="b2" href="/admin">Painel Admin</a>
</div>
<div class="rodape">Garra Sistemas · Se o problema continuar, avise a gestão.</div>
</div></body></html>"""

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    from fastapi.responses import JSONResponse
    path = request.url.path
    if _eh_api(path):
        return JSONResponse({"ok": False, "error": "Rota não encontrada",
                             "detail": "Rota não encontrada", "path": path}, status_code=404)
    return HTMLResponse(_pagina_erro(
        404, "Página não encontrada",
        "O endereço que você tentou abrir não existe ou mudou de lugar. "
        "Use os botões abaixo para voltar ao sistema."), status_code=404)

@app.exception_handler(500)
async def server_error_handler(request: Request, exc):
    from fastapi.responses import JSONResponse
    path = request.url.path
    print(f"[ERRO 500] {path}: {exc}")
    if _eh_api(path):
        return JSONResponse({"ok": False, "error": "Erro interno do servidor",
                             "detail": "Erro interno do servidor"}, status_code=500)
    return HTMLResponse(_pagina_erro(
        500, "Algo deu errado no servidor",
        "Ocorreu um erro inesperado ao processar sua solicitação. "
        "Tente novamente em instantes — o registro do problema já ficou salvo para análise."), status_code=500)

# ── HEALTH CHECK — mantém banco Neon acordado ──────────────────










# ══════════════════════════════════════════════════════════════
# ROUTERS — Refatoração Fase 2 (incluídos ao final: preserva a
# ordem de matching original, após todos os mounts estáticos)
# ══════════════════════════════════════════════════════════════
from routers.jardinagem import router as jardinagem_router
from routers.operacional import router as operacional_router
from routers.checklist import router as checklist_router
from routers.auth import router as auth_router
from routers.sistema import router as sistema_router
from routers.pages import router as pages_router
app.include_router(jardinagem_router)
app.include_router(operacional_router)
app.include_router(checklist_router)
app.include_router(auth_router)
app.include_router(sistema_router)
app.include_router(pages_router)
from routers.manutencao import router as manutencao_router  # v26
app.include_router(manutencao_router)
from routers.compras import router as compras_router  # v28 — Ordens de Compra
app.include_router(compras_router)

@app.on_event("startup")
async def criar_tabela_checklist_config():
    """Config compartilhada do checklist (visibilidade de pontos + período)
    — servidor = fonte única; todos os aparelhos leem a mesma regra."""
    try:
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS checklist.config (
                chave TEXT PRIMARY KEY,
                valor JSONB NOT NULL,
                atualizado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
    except Exception as e:
        print(f"[Startup] checklist.config: {e}")

@app.on_event("startup")
async def criar_tabela_ajustes_pontos():
    """Ajustes manuais de pontos do checklist (penalidade por má conduta
    ou bônus) — somados ao ranking dentro do período."""
    try:
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS checklist.ajustes_pontos (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                usuario_login TEXT NOT NULL,
                pts INT NOT NULL,
                motivo TEXT NOT NULL,
                criado_por TEXT,
                criado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
    except Exception as e:
        print(f"[Startup] ajustes_pontos: {e}")
    # (09/07/2026) Logística servidor-first: tabelas garantidas em qualquer
    # ambiente (existiam só por criação manual em produção).
    try:
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS checklist.log_motoristas (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                motor_id TEXT UNIQUE NOT NULL,
                nome TEXT NOT NULL,
                cpf TEXT, cnh TEXT, telefone TEXT,
                status TEXT DEFAULT 'ativo',
                observacoes TEXT,
                atualizado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS checklist.log_veiculos (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                veiculo_id TEXT UNIQUE NOT NULL,
                car_id TEXT NOT NULL,
                placa TEXT, modelo TEXT, ano INT, cor TEXT,
                status TEXT DEFAULT 'disponivel',
                extras JSONB DEFAULT '[]',
                observacoes TEXT,
                atualizado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS checklist.log_registros (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                registro_id TEXT UNIQUE NOT NULL,
                responsavel TEXT NOT NULL,
                data_hora TIMESTAMPTZ NOT NULL,
                carros JSONB DEFAULT '[]',
                criado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
        print("[Startup] tabelas logística OK")
    except Exception as e:
        print(f"[Startup] logística: {e}")
    # (09/07/2026) Badge do Mural — marco de leitura por usuário
    try:
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS public.mural_leituras (
                usuario_login TEXT PRIMARY KEY,
                lido_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
    except Exception as e:
        print(f"[Startup] mural_leituras: {e}")
    # (09/07/2026) Unicidade da checklist.frota: sem o índice único, o
    # ON CONFLICT do sync admin→checklist falhava silenciosamente na criação
    # de equipamentos. Dedup idempotente + índice garantidos em todo ambiente.
    try:
        await ajard_query("""
            DELETE FROM checklist.frota a
             USING checklist.frota b
             WHERE a.id > b.id
               AND a.categoria = b.categoria
               AND a.identificacao = b.identificacao
        """, fetch="none")
        await ajard_query("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_frota_cat_ident
                ON checklist.frota (categoria, identificacao)
        """, fetch="none")
        print("[Startup] unicidade frota OK")
    except Exception as e:
        print(f"[Startup] unicidade frota: {e}")
    # (09/07/2026) Espelho autocurativo REMOVIDO: o checklist lê direto do
    # cadastro único via /frota-checklist. checklist.frota fica dormente.

@app.on_event("startup")
async def seed_equipamento_combinado():
    """Equipamento padrão do sistema para apontamento de Combinados/Apoio.
    Categoria 'apoio': trabalho interno não motorizado (deslocamento, ajuda,
    combinados). A futura Manutenção EXCLUI categoria 'apoio' de OT/preventiva.
    Valor dos apontamentos é definido pela gestão no fechamento mensal."""
    try:
        await ajard_query(
            """INSERT INTO operacional.equipamentos (codigo, descricao, categoria, medicao, ativo)
               SELECT 'APOIO-01', 'Combinado / Apoio', 'apoio', 'hora', true
               WHERE NOT EXISTS (
                   SELECT 1 FROM operacional.equipamentos WHERE codigo = 'APOIO-01'
               )""", fetch="none"
        )
        print("[Seed] Equipamento Combinado/Apoio (APOIO-01) garantido")
    except Exception as e:
        print(f"[Seed] equipamento combinado: {e}")

@app.on_event("startup")
async def criar_schema_manutencao():
    """MÓDULO MANUTENÇÃO — fundação (v26, 06/07/2026).
    Referência ManWinWin. fornecedores é cadastro novo único (public);
    OTs referenciam operacional.equipamentos direto (categoria 'apoio'
    é bloqueada na rota de abertura)."""
    try:
        await ajard_query("CREATE SCHEMA IF NOT EXISTS manutencao", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS public.fornecedores (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                nome TEXT NOT NULL,
                cnpj TEXT, telefone TEXT, email TEXT,
                tipo TEXT DEFAULT 'pecas',
                observacao TEXT,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.ordens_trabalho (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                numero TEXT UNIQUE,
                ano INT, sequencia INT,
                equipamento_id UUID NOT NULL,
                tipo TEXT DEFAULT 'corretiva',
                prioridade TEXT DEFAULT 'media',
                status TEXT DEFAULT 'aberta',
                descricao TEXT NOT NULL,
                solicitante_id UUID, responsavel_id UUID,
                fornecedor_id UUID,
                horimetro_na_abertura NUMERIC,
                custo_total NUMERIC(12,2),
                data_abertura TIMESTAMPTZ DEFAULT now(),
                data_conclusao TIMESTAMPTZ,
                observacao_conclusao TEXT,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now(),
                atualizado_em TIMESTAMPTZ
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.ot_historico (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                ot_id UUID NOT NULL,
                status_de TEXT, status_para TEXT,
                observacao TEXT,
                usuario_id UUID,
                criado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
        # Aderência à spec v16 (ManWinWin): tabela chama-se manutencao.ot
        await ajard_query("""
            DO $$ BEGIN
              IF to_regclass('manutencao.ordens_trabalho') IS NOT NULL
                 AND to_regclass('manutencao.ot') IS NULL THEN
                ALTER TABLE manutencao.ordens_trabalho RENAME TO ot;
              END IF;
            END $$;""", fetch="none")
        # Colunas do fluxo completo (7.5): NF/financeiro (Luana), fechamento
        # técnico (Bruna), envio WhatsApp, encadeamento preventivo, pedido
        await ajard_query("""
            ALTER TABLE manutencao.ot
              ADD COLUMN IF NOT EXISTS numero_nf TEXT,
              ADD COLUMN IF NOT EXISTS valor_servico NUMERIC(12,2),
              ADD COLUMN IF NOT EXISTS data_nf DATE,
              ADD COLUMN IF NOT EXISTS horas_parada NUMERIC(8,1),
              ADD COLUMN IF NOT EXISTS data_retorno_operacao DATE,
              ADD COLUMN IF NOT EXISTS enviado_por UUID,
              ADD COLUMN IF NOT EXISTS enviado_em TIMESTAMPTZ,
              ADD COLUMN IF NOT EXISTS ot_origem_id UUID,
              ADD COLUMN IF NOT EXISTS ot_proxima_id UUID,
              ADD COLUMN IF NOT EXISTS pedido_id UUID
        """, fetch="none")
        # ══ ONDA 1+2 ManWinWin (06/07/2026) — domínios, peças, equipamentos ══
        # Nomenclatura PT-BR: Órgãos→componentes, Artigos→peças, Fichas→planos.
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.tipos_equipamento (
                sigla TEXT PRIMARY KEY, nome TEXT NOT NULL)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.sistemas (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.familias (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.sintomas (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.causas (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.pecas (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                codigo TEXT UNIQUE NOT NULL,
                descricao TEXT NOT NULL,
                unidade TEXT,
                familia_codigo TEXT,
                classe TEXT,
                custo_medio NUMERIC(12,2),
                codigo_fabricante TEXT,
                especificacao TEXT,
                observacao TEXT,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
        await ajard_query("""
            ALTER TABLE operacional.equipamentos
              ADD COLUMN IF NOT EXISTS marca TEXT,
              ADD COLUMN IF NOT EXISTS modelo TEXT,
              ADD COLUMN IF NOT EXISTS ano_fabricacao TEXT,
              ADD COLUMN IF NOT EXISTS num_serie TEXT,
              ADD COLUMN IF NOT EXISTS cor TEXT,
              ADD COLUMN IF NOT EXISTS tipo_sigla TEXT,
              ADD COLUMN IF NOT EXISTS sistema_codigo TEXT,
              ADD COLUMN IF NOT EXISTS centro_custo TEXT,
              ADD COLUMN IF NOT EXISTS equipamento_pai UUID,
              ADD COLUMN IF NOT EXISTS posicao TEXT,
              ADD COLUMN IF NOT EXISTS valor_aquisicao NUMERIC(14,2)
        """, fetch="none")
        await ajard_query("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_fornecedores_nome
              ON public.fornecedores (upper(nome))""", fetch="none")
        # ══ ONDA 3 (06/07/2026): planos preventivos + pontos de controle ══
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.planos (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                equipamento_id UUID NOT NULL,
                codigo TEXT NOT NULL,
                descricao TEXT NOT NULL,
                procedimento TEXT,
                tipo_trabalho TEXT,
                periodo_codigo TEXT,
                periodo_qtd NUMERIC,
                tempo_horas NUMERIC,
                hh_previsto NUMERIC,
                custo_previsto NUMERIC(12,2),
                plano_proximo_codigo TEXT,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now(),
                UNIQUE (equipamento_id, codigo)
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.pontos_controle (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                equipamento_id UUID NOT NULL,
                codigo TEXT NOT NULL,
                leitura_atual NUMERIC(12,1),
                data_leitura TIMESTAMPTZ,
                limiar_atencao NUMERIC(12,1),
                limiar_urgente NUMERIC(12,1),
                limiar_maximo NUMERIC(12,1),
                ativo BOOLEAN DEFAULT TRUE,
                UNIQUE (equipamento_id, codigo)
            )""", fetch="none")
        # ══ ALMOXARIFADOS + ESTOQUE (07/07/2026) — necessidade antiga da Garra ══
        # 2 almoxarifados reais: Escritório e Galpão. Estoque por peça×almoxarifado
        # com trilha completa de movimentações (entrada/saída/transferência/ajuste).
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.almoxarifados (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                codigo TEXT UNIQUE NOT NULL,
                nome TEXT NOT NULL,
                ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        await ajard_query("""
            INSERT INTO manutencao.almoxarifados (codigo, nome)
            SELECT 'A1', 'Escritório' WHERE NOT EXISTS
              (SELECT 1 FROM manutencao.almoxarifados WHERE codigo='A1')""", fetch="none")
        await ajard_query("""
            INSERT INTO manutencao.almoxarifados (codigo, nome)
            SELECT 'A2', 'Galpão' WHERE NOT EXISTS
              (SELECT 1 FROM manutencao.almoxarifados WHERE codigo='A2')""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.estoque (
                peca_id UUID NOT NULL,
                almoxarifado_id UUID NOT NULL,
                quantidade NUMERIC(12,2) DEFAULT 0,
                minimo NUMERIC(12,2),
                PRIMARY KEY (peca_id, almoxarifado_id))""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.movimentacoes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tipo TEXT NOT NULL,
                peca_id UUID NOT NULL,
                almox_origem UUID,
                almox_destino UUID,
                quantidade NUMERIC(12,2) NOT NULL,
                ot_id UUID,
                usuario_id UUID,
                observacao TEXT,
                criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
        # ══ BIBLIOTECA DE PREPARAÇÕES PADRÃO (07/07/2026) ══
        # Templates de manutenção POR TIPO de equipamento — a fábrica dos planos.
        # planos ganham vínculo com a preparação de origem.
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.preparacoes (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                codigo TEXT UNIQUE NOT NULL,
                descricao TEXT NOT NULL,
                tipo_sigla TEXT,
                tarefas TEXT,
                tdm_horas NUMERIC(8,2),
                criticidade INT DEFAULT 1,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now())""", fetch="none")
        await ajard_query("""
            ALTER TABLE manutencao.planos
              ADD COLUMN IF NOT EXISTS preparacao_codigo TEXT""", fetch="none")
        # ══ PARAMETRIZAÇÃO-FIRST (Regra 63, 07/07/2026) ══
        # Nenhum domínio hardcoded: tipos de manutenção/trabalho (árvore A/B/C/M
        # com A1/B1/C1/C2/M1), setores interventor e setores de atividade —
        # tudo tabela editável, seeds do ManWinWin real.
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.tipos_manutencao (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL,
                planejavel BOOLEAN DEFAULT FALSE, sistematico BOOLEAN DEFAULT FALSE)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.tipos_trabalho (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL,
                tipo_manutencao TEXT, ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.setores_interventor (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.setores_atividade (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        for cod, nome, pl, si in [("A","Preventivo Sistemático",True,True),
                                  ("B","Preventivo Condicionado",True,False),
                                  ("C","Corretivo",False,False),
                                  ("M","Melhoria",True,False)]:
            await ajard_query("""
                INSERT INTO manutencao.tipos_manutencao (codigo,nome,planejavel,sistematico)
                VALUES (%s,%s,%s,%s) ON CONFLICT (codigo) DO NOTHING""",
                (cod,nome,pl,si), fetch="none")
        for cod, nome, tm in [("A1","Sistemático","A"),("B1","Preventivo Condicional","B"),
                              ("C1","Reparo de Avaria","C"),("C2","Corretiva Deferida","C"),
                              ("M1","Melhoria","M"),("R1","Reforma CE","M")]:
            await ajard_query("""
                INSERT INTO manutencao.tipos_trabalho (codigo,nome,tipo_manutencao)
                VALUES (%s,%s,%s) ON CONFLICT (codigo) DO NOTHING""",
                (cod,nome,tm), fetch="none")
        for cod, nome in [("MAN","Manutenção"),("PRD","Produção"),
                          ("EXT","Externo"),("ADM","Administrativo")]:
            await ajard_query("""
                INSERT INTO manutencao.setores_interventor (codigo,nome)
                VALUES (%s,%s) ON CONFLICT (codigo) DO NOTHING""", (cod,nome), fetch="none")
        for cod, nome in [("MANUT","Manutenção"),("AQVEI","Aquisição de veículo"),("SERV","Serviço")]:
            await ajard_query("""
                INSERT INTO manutencao.setores_atividade (codigo,nome)
                VALUES (%s,%s) ON CONFLICT (codigo) DO NOTHING""", (cod,nome), fetch="none")
        await ajard_query("""
            ALTER TABLE manutencao.ot
              ADD COLUMN IF NOT EXISTS tipo_trabalho TEXT,
              ADD COLUMN IF NOT EXISTS interventor TEXT""", fetch="none")
        # ══ Parametrizar completo (07/07/2026): centros de custo + motivos ══
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.centros_custo (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.motivos_reprogramacao (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS manutencao.motivos_pendente (
                codigo TEXT PRIMARY KEY, nome TEXT NOT NULL, ativo BOOLEAN DEFAULT TRUE)""", fetch="none")
        for cod, nome in [("01","Centro Custo G"),("02","Centro Custo GM"),("9999","Centro custo Geral")]:
            await ajard_query("""INSERT INTO manutencao.centros_custo (codigo,nome) VALUES (%s,%s)
                ON CONFLICT (codigo) DO NOTHING""", (cod,nome), fetch="none")
        for cod, nome in [("01","Mudança de planos"),("02","Possibilidade de parada do equipamento"),
                          ("03","Alteração de KM"),("04","Alteração de horímetro"),("05","Peça em bom estado"),
                          ("06","Teste"),("07","Equipamento trocado"),("08","Falta de alinhamento entre equipe"),
                          ("09","Substituição do plano de manutenção"),("10","Equipamento não disponível"),
                          ("11","Peça em péssimo estado"),("12","Equipamento ocioso"),("13","OT em duplicidade")]:
            await ajard_query("""INSERT INTO manutencao.motivos_reprogramacao (codigo,nome) VALUES (%s,%s)
                ON CONFLICT (codigo) DO NOTHING""", (cod,nome), fetch="none")
        for cod, nome in [("01","Equipamento em atividade"),("02","Pressão por aquecimento")]:
            await ajard_query("""INSERT INTO manutencao.motivos_pendente (codigo,nome) VALUES (%s,%s)
                ON CONFLICT (codigo) DO NOTHING""", (cod,nome), fetch="none")
        print("[Startup] schema manutencao OK")
    except Exception as e:
        print(f"[Startup] manutencao: {e}")


@app.on_event("startup")
async def criar_schema_compras():
    """MÓDULO COMPRAS — Ordens de Compra (v28).
    OC abrange todo o negócio: cada OC tem SETOR (parametrizável).
    Alçadas por usuário; aprovação = assinatura digital; recebimento
    item a item; vínculo opcional com OT (soma custo) e, na fase 2,
    com manutencao.pecas/estoque (peca_id já previsto em oc_itens)."""
    try:
        await ajard_query("CREATE SCHEMA IF NOT EXISTS compras", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS compras.setores (
                codigo TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                cor TEXT,
                ativo BOOLEAN DEFAULT TRUE
            )""", fetch="none")
        for cod, nome, cor in [("MANUT","Manutenção","#E8820C"),
                               ("OPER","Operacional","#1E3A8A"),
                               ("JARD","Jardinagem","#16A34A"),
                               ("ADM","Administrativo","#64748B"),
                               ("COMB","Combustível","#DC2626"),
                               ("EPI","EPI","#7C3AED")]:
            await ajard_query("""
                INSERT INTO compras.setores (codigo,nome,cor)
                VALUES (%s,%s,%s) ON CONFLICT (codigo) DO NOTHING""",
                (cod,nome,cor), fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS compras.alcadas (
                usuario_id UUID PRIMARY KEY,
                valor_limite NUMERIC(12,2),
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now(),
                atualizado_em TIMESTAMPTZ
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS compras.ordens_compra (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                numero TEXT UNIQUE,
                ano INT, sequencia INT,
                setor_codigo TEXT NOT NULL,
                fornecedor_id UUID,
                ot_id UUID,
                equipamento_id UUID,
                status TEXT DEFAULT 'rascunho',
                prioridade TEXT DEFAULT 'normal',
                condicao_pagamento TEXT,
                observacao TEXT,
                valor_total NUMERIC(12,2) DEFAULT 0,
                nf_numero TEXT,
                solicitante_id UUID,
                aprovador_id UUID,
                data_aprovacao TIMESTAMPTZ,
                motivo_rejeicao TEXT,
                enviado_por UUID,
                enviado_em TIMESTAMPTZ,
                link_pdf TEXT,
                ativo BOOLEAN DEFAULT TRUE,
                criado_em TIMESTAMPTZ DEFAULT now(),
                atualizado_em TIMESTAMPTZ
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS compras.oc_itens (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                oc_id UUID NOT NULL,
                peca_id UUID,
                descricao TEXT NOT NULL,
                quantidade NUMERIC(12,3) DEFAULT 1,
                unidade TEXT DEFAULT 'UN',
                valor_unit NUMERIC(12,2) DEFAULT 0,
                qtd_recebida NUMERIC(12,3) DEFAULT 0,
                ordem INT DEFAULT 0,
                ativo BOOLEAN DEFAULT TRUE
            )""", fetch="none")
        await ajard_query("""
            CREATE TABLE IF NOT EXISTS compras.oc_historico (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                oc_id UUID NOT NULL,
                status_de TEXT, status_para TEXT,
                observacao TEXT,
                usuario_id UUID,
                criado_em TIMESTAMPTZ DEFAULT now()
            )""", fetch="none")
        await ajard_query("""
            CREATE INDEX IF NOT EXISTS ix_oc_status
              ON compras.ordens_compra (status)""", fetch="none")
        await ajard_query("""
            CREATE INDEX IF NOT EXISTS ix_oc_itens_oc
              ON compras.oc_itens (oc_id)""", fetch="none")
        print("[Startup] schema compras OK")
    except Exception as e:
        print(f"[Startup] compras: {e}")
