🚜 Garra Check List — Sistema Digital de Gestão de Frota
<p align="center">
  <img src="icons/logo.jpg" alt="Garra Terraplenagem e Caçambas" width="260" />
</p>
<p align="center">
  <strong>Garra Terraplenagem e Caçambas</strong> — Pará de Minas, MG<br/>
  Sistema offline-first de check list, logística de frota e gestão operacional
</p>
---
📋 O que o sistema faz
Módulo	Descrição
✅ Check List Digital	3 modelos padrão (Máquinas, Carro de Apoio, Caminhão) + personalizados
📴 Offline-first	Funciona sem internet — salva localmente e sincroniza ao reconectar
🏆 Ranking de Pontuação	Motoristas ganham pontos por envio — pódio e ranking completo
🚗 Logística	Cadastro de motoristas, veículos, controle de destinos em tempo real
📊 Dashboard	KPIs, conformidade por colaborador, histórico filtrado
🖨 Relatório Imprimível	Logística com logo, tabelas e linhas de assinatura
📱 PWA Instalável	Funciona como app nativo no celular (Android e iPhone)
---
👤 Perfis de Acesso
Perfil	O que vê
Gestor	Tudo: check lists, logística, frota, usuários, ranking, relatórios, builder
Superior Direto	Logística de carros de apoio + preencher e criar check lists
Motorista / Operador	Preencher check lists + ver próprio ranking
---
🔑 Logins de Demonstração
Login	Senha	Perfil
`admin`	`garra2024`	Gestor
`gestor`	`garra2024`	Gestor
`gilson`	`garra2024`	Superior Direto
`marco`	`garra2024`	Superior Direto
`andre`	`123456`	Motorista
`emerson`	`123456`	Motorista
`samuel`	`123456`	Motorista
`franciele`	`123456`	Motorista
`motorista`	`123456`	Motorista (demo)
> ⚠️ **Antes de ir para produção:** troque todas as senhas pelo painel Usuários (Gestor → aba Usuários).
---
🏆 Sistema de Pontuação
Situação	Pontos padrão
Todos os itens conformes	+100 pts
Tem não conformes mas reportou	+60 pts
Observações detalhadas preenchidas	+20 pts
Envio no dia correto	+10 pts
Peso extra por item crítico	+N pts (configurado no builder)
O gestor pode alterar esses valores em cada check list personalizado.
---
🗂 Estrutura de Arquivos
```
garra-checklist/
├── index.html          ← App completa (Single Page App)
├── sw.js               ← Service Worker (cache offline)
├── manifest.json       ← PWA manifest (instalação no celular)
├── icons/
│   └── logo.jpg        ← Logotipo Garra Terraplenagem
├── css/
│   └── style.css       ← Design system completo
└── js/
    ├── data.js         ← Check lists padrão e frota padrão
    ├── app.js          ← Lógica principal (auth, forms, builder, dashboard)
    └── logistics.js    ← Módulo logística (cadastros, registros, relatório)
```
---
🚀 Como Publicar — Passo a Passo
Passo 1 — Criar conta no GitHub
Acesse github.com e crie uma conta gratuita
Clique em "New repository"
Nome: `garra-checklist`
Deixe marcado como Public
Clique em "Create repository"
Passo 2 — Fazer upload dos arquivos
Na página do repositório recém criado:
Clique em "uploading an existing file"
Arraste a pasta `garra-checklist` inteira para a área indicada
Aguarde o upload de todos os arquivos
Clique em "Commit changes"
Passo 3 — Publicar no Render
> O Render é necessário porque câmera e PWA exigem **HTTPS**.
Acesse render.com
Crie conta clicando em "Continue with GitHub"
Clique em "New +" → "Static Site"
Selecione o repositório `garra-checklist`
Configure:
Publish directory: `/`
Clique em "Create Static Site"
Aguarde ~2 minutos — URL gerada:
```
https://garra-checklist.onrender.com
```
✅ Pronto! O sistema está no ar.
---
🔄 Como Atualizar os Arquivos
Toda atualização no GitHub republica automaticamente no Render em ~60 segundos.
Opção A — Editor no próprio navegador (mais fácil, sem instalar nada)
Acesse o repositório no GitHub
Pressione a tecla `.` (ponto) no teclado
Abre um VS Code direto no navegador
Edite o arquivo desejado (ex.: `js/app.js`)
Clique no ícone Source Control (ramificação) na barra lateral esquerda
Escreva uma mensagem curta (ex.: "atualização check list")
Clique em "Commit & Push"
O Render atualiza sozinho em ~1 minuto ✅
Opção B — Substituir um arquivo pelo GitHub
No repositório, clique no arquivo (ex.: `js/logistics.js`)
Clique no lápis ✏ (Edit this file) no canto superior direito
Apague o conteúdo e cole o novo
Clique em "Commit changes"
Opção C — Arrastar arquivo novo
No repositório, entre na pasta desejada (ex.: `js/`)
Clique em "Add file" → "Upload files"
Arraste o arquivo atualizado (substitui o de mesmo nome)
Clique em "Commit changes"
---
📱 Instalar como App no Celular
Após publicar no Render com HTTPS:
Android — Chrome:
Abra a URL no Chrome
Menu ⋮ → "Adicionar à tela inicial"
Confirme — fica como ícone na tela inicial
iPhone — Safari:
Abra a URL no Safari
Toque em Compartilhar (ícone de caixa com seta para cima)
Role e toque em "Adicionar à Tela de Início"
Confirme — fica como ícone na tela inicial
> Após instalar, o app abre sem barra do navegador (tela cheia) e **funciona completamente offline**.
---
📴 Como Funciona o Modo Offline
```
Operador no campo SEM internet
         ↓
Preenche o check list normalmente
         ↓
Sistema salva no armazenamento local do celular
         ↓
Banner amarelo: "N check lists aguardando sincronização"
         ↓
Celular entra em área com sinal
         ↓
Sincronização automática ✅ — nenhum dado é perdido
```
---
🔮 Próximas Implementações Previstas
[ ] Backend com banco de dados real (Node.js + PostgreSQL) para sincronização entre dispositivos
[ ] Controle de pneus — vida útil, calibragem, alertas de troca
[ ] Ordens de Trabalho (OT) integradas ao check list
[ ] Manutenção preventiva com agendamentos e alertas
[ ] Notificações push para não conformidades críticas
[ ] Exportação de relatórios em PDF
[ ] Módulo comercial — orçamentos e contratos
[ ] Módulo financeiro — custo por equipamento e centro de custo
---
🛠 Tecnologias
Tecnologia	Uso
HTML5 / CSS3 / JavaScript puro	App sem frameworks externos
Service Worker API	Cache offline e sincronização automática
localStorage	Banco de dados local no dispositivo
PWA Manifest	Instalação como app nativo
Google Fonts — Barlow	Tipografia da identidade visual
Sem dependências de terceiros além das fontes. Funciona em qualquer navegador moderno.
---
📞 Contato
Garra Terraplenagem e Caçambas
📍 Pará de Minas – MG
🌐 garraterraplenagem.com.br
---
<p align="center">Sistema desenvolvido para uso interno da <strong>Garra Terraplenagem e Caçambas</strong></p>
