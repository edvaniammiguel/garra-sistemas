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
