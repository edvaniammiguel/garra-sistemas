# 🌿 Garra — Sistema de Fotos Jardinagem v2
### Flask + Supabase · PWA Mobile · Offline-First

---

## O que mudou em relação ao sistema anterior

| Antes | Agora |
|---|---|
| SQLite local | PostgreSQL no Supabase (nuvem) |
| Fotos no disco do servidor | Fotos no Supabase Storage |
| Sem autenticação | Login com JWT por perfil |
| Só notebook (Luana) | Celular do Arthur + notebook da Luana |
| Sem suporte offline | Fila offline com IndexedDB |
| Semanas manuais | Semanas criadas automaticamente |
| Sem email | Email ao fechar semana (Fase 4) |

---

## Pré-requisitos

- Python 3.11+
- Conta no Supabase (gratuita): https://supabase.com
- (Opcional) Chave API Anthropic para análise IA

---

## Instalação

### 1. Criar projeto no Supabase
1. Acesse https://supabase.com → New Project
2. Nome: `garra-gestao`
3. Anote: **Project URL** e **service_role key** (Settings → API)
4. Crie bucket Storage: **Storage → New Bucket → Nome: `jardinagem-fotos` → Private**

### 2. Rodar a migration SQL
No Supabase: **SQL Editor → New Query** → cole o conteúdo de `migrations/001_jardinagem_schema.sql` → Run

### 3. Configurar variáveis de ambiente
```bash
cp .env.example .env
# Edite o .env com suas chaves do Supabase
```

### 4. Instalar dependências
```bash
pip install -r requirements.txt
```

### 5. Criar usuários iniciais
```bash
python setup.py
```
Senhas padrão (troque após o primeiro login!):
- Admin: `admin@garra.local` / `garra@2026`
- Luana: `luana@garra.local` / `luana@2026`
- Arthur: `arthur@garra.local` / `arthur@2026`
- Breno: `breno@garra.local` / `breno@2026`

### 6. Migrar dados do sistema antigo (opcional)
```bash
python migrar_dados.py caminho/para/garra.db
```

### 7. Iniciar o servidor
```bash
python app.py
```

Acesse:
- **Desktop (Luana):** http://localhost:5000
- **Mobile (Arthur):** http://localhost:5000/mobile

---

## Como o Arthur usa no celular

1. Arthur acessa `http://IP-DO-SERVIDOR:5000/mobile` no Chrome
2. Chrome pergunta "Adicionar à tela inicial" → clica em Adicionar
3. App aparece na tela como qualquer outro app
4. Abre o app → faz login → está pronto

### Sem internet no campo:
- Arthur tira a foto normalmente
- App salva no próprio celular automaticamente
- Quando Arthur chega no escritório ou em área com Wi-Fi:
  - App detecta a conexão e sincroniza tudo automaticamente
  - Aparece o indicador de fila pendente na tela

---

## Estrutura de pastas

```
garra-jardinagem/
├── app.py                  ← Backend Flask principal
├── setup.py                ← Cria usuários iniciais
├── migrar_dados.py         ← Migra SQLite → Supabase
├── requirements.txt
├── .env.example            ← Copie para .env e configure
├── migrations/
│   └── 001_jardinagem_schema.sql  ← Rodar no Supabase
├── templates/
│   ├── index.html          ← Interface desktop (Luana)
│   └── mobile.html         ← Interface mobile (Arthur/Breno)
└── static/
    ├── manifest.json       ← PWA
    └── js/
        └── sw.js           ← Service Worker (offline)
```

---

## Banco de dados — schema jardinagem

```
public.usuarios          → Login de todos os perfis
public.clientes          → Clientes (Águas de Pará de Minas etc.)
jardinagem.meses         → Meses de referência
jardinagem.semanas       → 4 semanas por mês (criadas automaticamente)
jardinagem.pares         → Par antes/depois por local
jardinagem.fotos         → Fotos vinculadas a cada par
jardinagem.fila_sync     → Controle de sincronização offline
jardinagem.emails_enviados → Histórico de relatórios enviados
jardinagem.config        → Configurações (next_code etc.)
```

---

## Perfis de acesso

| Perfil | Acesso |
|---|---|
| `admin` | Tudo |
| `luana` | Desktop + aprovação de semanas |
| `campo` | Mobile (Arthur, Breno) — envio de fotos |

---

## Próximas fases

- **Fase 4:** Email automático ao fechar semana (Flask-Mail + SMTP Gmail)
- **Fase 5:** Painel de status — fotos enviadas, semanas abertas, fila offline
- **Integração futura:** Módulo operacional (horímetros, OS) no mesmo Supabase

---

## Keep-alive do Supabase (plano gratuito)

Para evitar que o projeto seja pausado após 7 dias sem uso,
adicione o arquivo `.github/workflows/supabase-ping.yml` no repositório:

```yaml
name: Supabase keep-alive
on:
  schedule:
    - cron: "0 8 * * 1"  # toda segunda às 8h UTC
  workflow_dispatch:
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - name: Ping
        run: |
          curl -s -o /dev/null \
            "${{ secrets.SUPABASE_URL }}/rest/v1/clientes?select=id&limit=1" \
            -H "apikey: ${{ secrets.SUPABASE_ANON_KEY }}"
```

Adicione `SUPABASE_URL` e `SUPABASE_ANON_KEY` nos Secrets do GitHub.

---

*Desenvolvido para Garra Terraplenagem · Projeto Jardinagem Águas de Pará de Minas*
