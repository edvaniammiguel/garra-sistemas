-- ============================================================
-- GARRA TERRAPLENAGEM — Schema Jardinagem
-- Supabase / PostgreSQL
-- Rodar no SQL Editor do Supabase
-- ============================================================

-- Schema dedicado ao módulo de jardinagem
CREATE SCHEMA IF NOT EXISTS jardinagem;

-- ── USUÁRIOS (schema público — compartilhado com outros módulos futuros) ──
CREATE TABLE IF NOT EXISTS public.usuarios (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nome        VARCHAR(100) NOT NULL,
    email       VARCHAR(150) UNIQUE NOT NULL,
    senha_hash  TEXT NOT NULL,
    perfil      VARCHAR(30) NOT NULL DEFAULT 'campo',
    -- perfis: admin | luana | campo (arthur, breno)
    ativo       BOOLEAN DEFAULT TRUE,
    criado_em   TIMESTAMPTZ DEFAULT NOW()
);

-- ── CLIENTES (compartilhado futuramente com operacional) ──
CREATE TABLE IF NOT EXISTS public.clientes (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    nome      VARCHAR(150) NOT NULL,
    email     TEXT,           -- destinatário do relatório semanal
    email_cc  TEXT,           -- CC (separado por vírgula)
    ativo     BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMPTZ DEFAULT NOW()
);

-- Inserir cliente padrão
INSERT INTO public.clientes (nome, email, email_cc)
VALUES ('Águas de Pará de Minas', '', 'luana@garratp.com.br,admin@garratp.com.br')
ON CONFLICT DO NOTHING;

-- ── MESES ──
CREATE TABLE IF NOT EXISTS jardinagem.meses (
    id          SERIAL PRIMARY KEY,
    cliente_id  UUID REFERENCES public.clientes(id),
    ano         INTEGER NOT NULL,
    mes         INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
    label       TEXT NOT NULL,
    criado_em   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ano, mes)
);

-- ── SEMANAS ──
CREATE TABLE IF NOT EXISTS jardinagem.semanas (
    id        SERIAL PRIMARY KEY,
    mes_id    INTEGER NOT NULL REFERENCES jardinagem.meses(id) ON DELETE CASCADE,
    label     TEXT NOT NULL,
    data_ini  DATE,
    data_fim  DATE,
    ordem     INTEGER DEFAULT 0,
    status    VARCHAR(20) DEFAULT 'aberta',
    -- status: aberta | revisao | enviada
    enviado_em TIMESTAMPTZ,
    criado_em  TIMESTAMPTZ DEFAULT NOW()
);

-- ── PARES DE FOTOS ──
CREATE TABLE IF NOT EXISTS jardinagem.pares (
    id          SERIAL PRIMARY KEY,
    semana_id   INTEGER NOT NULL REFERENCES jardinagem.semanas(id) ON DELETE CASCADE,
    codigo_a    INTEGER,   -- código da foto "antes"
    codigo_d    INTEGER,   -- código da foto "depois"
    local_nome  TEXT DEFAULT '',
    data_label  TEXT DEFAULT '',
    ordem       INTEGER DEFAULT 0,
    criado_em   TIMESTAMPTZ DEFAULT NOW()
);

-- ── FOTOS ──
CREATE TABLE IF NOT EXISTS jardinagem.fotos (
    id            SERIAL PRIMARY KEY,
    par_id        INTEGER NOT NULL REFERENCES jardinagem.pares(id) ON DELETE CASCADE,
    tipo          VARCHAR(10) NOT NULL CHECK (tipo IN ('antes', 'depois')),
    -- origem: 'desktop' (Luana) | 'mobile' (Arthur/Breno)
    origem        VARCHAR(10) DEFAULT 'desktop',
    enviado_por   UUID REFERENCES public.usuarios(id),
    -- storage_path: caminho no Supabase Storage
    storage_path  TEXT NOT NULL,
    filename_orig TEXT,
    ia_descricao  TEXT DEFAULT '',
    ia_local      TEXT DEFAULT '',
    ia_estado     TEXT DEFAULT '',   -- 'antes' | 'depois' detectado pela IA
    ia_id_visual  TEXT DEFAULT '',   -- agrupamento por local visual
    sincronizado  BOOLEAN DEFAULT TRUE,  -- FALSE = veio da fila offline
    criado_em     TIMESTAMPTZ DEFAULT NOW()
);

-- ── FILA OFFLINE (controle de sincronização mobile) ──
CREATE TABLE IF NOT EXISTS jardinagem.fila_sync (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    usuario_id    UUID REFERENCES public.usuarios(id),
    semana_id     INTEGER REFERENCES jardinagem.semanas(id),
    local_nome    TEXT DEFAULT '',
    tipo          VARCHAR(10) CHECK (tipo IN ('antes', 'depois')),
    storage_path  TEXT NOT NULL,
    processado    BOOLEAN DEFAULT FALSE,
    criado_em     TIMESTAMPTZ DEFAULT NOW()
);

-- ── EMAILS ENVIADOS ──
CREATE TABLE IF NOT EXISTS jardinagem.emails_enviados (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    semana_id     INTEGER REFERENCES jardinagem.semanas(id),
    destinatario  TEXT NOT NULL,
    assunto       TEXT,
    status        VARCHAR(20) DEFAULT 'enviado',  -- enviado | erro
    erro_msg      TEXT,
    enviado_em    TIMESTAMPTZ DEFAULT NOW()
);

-- ── CONFIG SEQUENCIAL DE CÓDIGOS ──
CREATE TABLE IF NOT EXISTS jardinagem.config (
    chave  TEXT PRIMARY KEY,
    valor  TEXT
);
INSERT INTO jardinagem.config (chave, valor)
VALUES ('next_code', '6046')
ON CONFLICT (chave) DO NOTHING;

-- ── ÍNDICES ──
CREATE INDEX IF NOT EXISTS idx_semanas_mes    ON jardinagem.semanas(mes_id);
CREATE INDEX IF NOT EXISTS idx_pares_semana   ON jardinagem.pares(semana_id);
CREATE INDEX IF NOT EXISTS idx_fotos_par      ON jardinagem.fotos(par_id);
CREATE INDEX IF NOT EXISTS idx_fotos_enviado  ON jardinagem.fotos(enviado_por);
CREATE INDEX IF NOT EXISTS idx_fila_usuario   ON jardinagem.fila_sync(usuario_id);

-- ── ROW LEVEL SECURITY (básico — expandir conforme auth evolui) ──
ALTER TABLE public.usuarios   ENABLE ROW LEVEL SECURITY;
ALTER TABLE jardinagem.meses  ENABLE ROW LEVEL SECURITY;
ALTER TABLE jardinagem.semanas ENABLE ROW LEVEL SECURITY;
ALTER TABLE jardinagem.pares  ENABLE ROW LEVEL SECURITY;
ALTER TABLE jardinagem.fotos  ENABLE ROW LEVEL SECURITY;

-- Por ora: service_role tem acesso total (backend Flask usa service_role key)
-- Usuários finais acessam apenas via backend autenticado

-- ── STORAGE BUCKET ──
-- Rodar no painel Storage do Supabase:
-- Criar bucket "jardinagem-fotos" com acesso privado
-- O backend Flask faz upload com service_role key e gera URLs assinadas
