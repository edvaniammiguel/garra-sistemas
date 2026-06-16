-- ═══════════════════════════════════════════════════════════
-- GARRA CHECK LIST — Schema PostgreSQL
-- Executar no banco garra-checklist-db via Render Shell
-- ═══════════════════════════════════════════════════════════

-- ── EXTENSÕES ──────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ── USUÁRIOS ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS usuarios (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  login       VARCHAR(50) UNIQUE NOT NULL,
  nome        VARCHAR(150) NOT NULL,
  senha_hash  VARCHAR(255) NOT NULL,
  perfil      VARCHAR(20) NOT NULL CHECK (perfil IN ('manager','superior','driver','diarista')),
  ativo       BOOLEAN DEFAULT TRUE,
  pts         INTEGER DEFAULT 0,
  total_envios INTEGER DEFAULT 0,
  criado_em   TIMESTAMP DEFAULT NOW(),
  atualizado_em TIMESTAMP DEFAULT NOW()
);

-- ── FROTA ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS frota (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  categoria   VARCHAR(20) NOT NULL CHECK (categoria IN ('maquinas','carro','caminhao')),
  identificacao VARCHAR(30) NOT NULL,
  descricao   VARCHAR(150),
  ativo       BOOLEAN DEFAULT TRUE,
  criado_em   TIMESTAMP DEFAULT NOW()
);

-- ── MODELOS DE CHECK LIST ──────────────────────────────────
CREATE TABLE IF NOT EXISTS checklist_modelos (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cl_id       VARCHAR(50) UNIQUE NOT NULL,  -- ex: 'maquinas', 'cl_123_abc'
  label       VARCHAR(100) NOT NULL,
  icon        VARCHAR(10) DEFAULT '📋',
  descricao   VARCHAR(200),
  vehicle_cat VARCHAR(20),
  is_default  BOOLEAN DEFAULT FALSE,
  score_full  INTEGER DEFAULT 100,
  score_nc    INTEGER DEFAULT 60,
  score_obs   INTEGER DEFAULT 20,
  score_ontime INTEGER DEFAULT 10,
  questions   JSONB NOT NULL DEFAULT '[]',  -- array de perguntas do builder
  steps       JSONB NOT NULL DEFAULT '[]',  -- steps gerados para o form
  ativo       BOOLEAN DEFAULT TRUE,
  criado_por  UUID REFERENCES usuarios(id),
  criado_em   TIMESTAMP DEFAULT NOW(),
  atualizado_em TIMESTAMP DEFAULT NOW()
);

-- ── ENVIOS DE CHECK LIST ───────────────────────────────────
CREATE TABLE IF NOT EXISTS checklist_envios (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  envio_id    VARCHAR(50) UNIQUE NOT NULL,  -- id gerado no front
  usuario_id  UUID REFERENCES usuarios(id),
  usuario_login VARCHAR(50) NOT NULL,
  usuario_nome VARCHAR(150) NOT NULL,
  cl_id       VARCHAR(50) NOT NULL,
  cl_label    VARCHAR(100),
  meta        JSONB DEFAULT '{}',      -- dados de identificação (local, veículo, data...)
  respostas   JSONB DEFAULT '{}',      -- respostas de cada item
  pts         INTEGER DEFAULT 0,
  tem_nc      BOOLEAN DEFAULT FALSE,
  total_nc    INTEGER DEFAULT 0,
  arquivado   BOOLEAN DEFAULT FALSE,   -- equipamento removido
  synced      BOOLEAN DEFAULT TRUE,
  enviado_em  TIMESTAMP DEFAULT NOW()
);

-- ── LOGÍSTICA: MOTORISTAS ──────────────────────────────────
CREATE TABLE IF NOT EXISTS log_motoristas (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  motor_id    VARCHAR(30) UNIQUE NOT NULL,  -- ex: 'ld_001'
  nome        VARCHAR(150) NOT NULL,
  cpf         VARCHAR(20),
  cnh         VARCHAR(50),
  telefone    VARCHAR(20),
  status      VARCHAR(20) DEFAULT 'ativo' CHECK (status IN ('ativo','ferias','afastado','inativo')),
  observacoes TEXT,
  criado_em   TIMESTAMP DEFAULT NOW(),
  atualizado_em TIMESTAMP DEFAULT NOW()
);

-- ── LOGÍSTICA: VEÍCULOS DE APOIO ──────────────────────────
CREATE TABLE IF NOT EXISTS log_veiculos (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  veiculo_id  VARCHAR(30) UNIQUE NOT NULL,  -- ex: 'lc_001'
  car_id      VARCHAR(20) NOT NULL,          -- ex: 'CA-44'
  placa       VARCHAR(15),
  modelo      VARCHAR(100),
  ano         INTEGER,
  cor         VARCHAR(50),
  status      VARCHAR(20) DEFAULT 'disponivel' CHECK (status IN ('disponivel','em-campo','manutencao','inativo')),
  extras      JSONB DEFAULT '[]',            -- campos adicionais dinâmicos
  observacoes TEXT,
  criado_em   TIMESTAMP DEFAULT NOW(),
  atualizado_em TIMESTAMP DEFAULT NOW()
);

-- ── LOGÍSTICA: REGISTROS ───────────────────────────────────
CREATE TABLE IF NOT EXISTS log_registros (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  registro_id VARCHAR(30) UNIQUE NOT NULL,  -- id gerado no front
  responsavel VARCHAR(150) NOT NULL,
  data_hora   TIMESTAMP NOT NULL,
  carros      JSONB NOT NULL DEFAULT '[]',  -- [{id, model, dest, driver, status, obs}]
  synced      BOOLEAN DEFAULT TRUE,
  criado_em   TIMESTAMP DEFAULT NOW()
);

-- ═══════════════════════════════════════════════════════════
-- ÍNDICES para performance
-- ═══════════════════════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_envios_usuario    ON checklist_envios(usuario_login);
CREATE INDEX IF NOT EXISTS idx_envios_cl         ON checklist_envios(cl_id);
CREATE INDEX IF NOT EXISTS idx_envios_data       ON checklist_envios(enviado_em DESC);
CREATE INDEX IF NOT EXISTS idx_log_registros_data ON log_registros(data_hora DESC);
CREATE INDEX IF NOT EXISTS idx_frota_categoria   ON frota(categoria);

-- ═══════════════════════════════════════════════════════════
-- DADOS INICIAIS
-- ═══════════════════════════════════════════════════════════

-- Usuários padrão (senha: garra2024 para gestores, 123456 para motoristas)
-- As senhas abaixo são hashes bcrypt — geradas pela API no primeiro deploy
INSERT INTO usuarios (login, nome, senha_hash, perfil) VALUES
  ('admin',     'Administrador Garra',  'HASH_GERADO_NA_API', 'manager'),
  ('gestor',    'Gestor de Frota',      'HASH_GERADO_NA_API', 'manager'),
  ('gilson',    'Gilson',               'HASH_GERADO_NA_API', 'superior'),
  ('marco',     'Marco Aurélio',        'HASH_GERADO_NA_API', 'superior'),
  ('andre',     'André',                'HASH_GERADO_NA_API', 'driver'),
  ('emerson',   'Emerson',              'HASH_GERADO_NA_API', 'driver'),
  ('samuel',    'Samuel',               'HASH_GERADO_NA_API', 'driver'),
  ('franciele', 'Franciele',            'HASH_GERADO_NA_API', 'driver'),
  ('motorista', 'Motorista Demo',       'HASH_GERADO_NA_API', 'driver')
ON CONFLICT (login) DO NOTHING;

-- Frota padrão — máquinas
INSERT INTO frota (categoria, identificacao, descricao) VALUES
  ('maquinas','EH-02','Escavadeira Hidráulica'),
  ('maquinas','EH-03','Escavadeira Hidráulica'),
  ('maquinas','EH-39','Escavadeira Hidráulica'),
  ('maquinas','EH-50','Escavadeira Hidráulica'),
  ('maquinas','PC-43','Patrol / Motoniveladora'),
  ('maquinas','PC-49','Patrol / Motoniveladora'),
  ('maquinas','RE-29','Retroescavadeira'),
  ('maquinas','RE-45','Retroescavadeira'),
  ('carro','CA-12','Carro de Apoio – Gol'),
  ('carro','CA-21','Carro de Apoio'),
  ('carro','CA-32','Carro de Apoio – D20'),
  ('carro','CA-40','Carro de Apoio'),
  ('carro','CA-42','Carro de Apoio'),
  ('carro','CA-44','Carro de Apoio – Strada'),
  ('carro','CA-47','Carro de Apoio – Strada'),
  ('carro','CA-48','Carro de Apoio – Strada'),
  ('caminhao','CB-05','Caminhão Basculante'),
  ('caminhao','CB-06','Caminhão Basculante'),
  ('caminhao','CB-015','Caminhão Basculante'),
  ('caminhao','CB-016','Caminhão Basculante'),
  ('caminhao','CB-024','Caminhão Basculante'),
  ('caminhao','CB-030','Caminhão Basculante'),
  ('caminhao','CB-037','Caminhão Basculante'),
  ('caminhao','CP-019','Caminhão Pipa'),
  ('caminhao','CPO-022','Caminhão Pipa / Oficina'),
  ('caminhao','CPO-026','Caminhão Pipa / Oficina'),
  ('caminhao','CPO-036','Caminhão Pipa / Oficina'),
  ('caminhao','CA-023','Caminhão de Apoio'),
  ('caminhao','CA-040','Caminhão de Apoio'),
  ('caminhao','CR-07','Caminhão Reboque')
ON CONFLICT DO NOTHING;
