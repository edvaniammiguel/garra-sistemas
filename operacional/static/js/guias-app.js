/* (05/11/2026) Tour guiado do app do celular (/mobile) — GarraGuia. Dois roteiros:
 *   parte : Parte diária / OS — a aba ⚙️ OS: escolher a OS, Registrar, preencher o dia e salvar.
 *   app   : Conhecendo o app — barra de abas, ⟳/online, avatar com avisos, painel Minha conta.
 * Só anota a casca do app e a aba OS (as outras abas rodam em iframes com tours próprios).
 * Modo demonstração: mostra a tela real, não grava. Abre pelo link "▶ Como fazer" no topo da aba OS,
 * pelo botão no painel Minha conta, ou por /mobile?guia=parte | /mobile?guia=app. */
(function () {
  const esperar = ms => new Promise(r => setTimeout(r, ms));
  const vis = el => !!(el && el.offsetWidth > 0 && el.offsetHeight > 0);
  const navs = () => [...document.querySelectorAll('.bottom-nav .nav-btn')];
  const navVis = i => { const b = navs()[i]; return vis(b) ? b : null; };
  const secaoConta = id => { const e = document.getElementById(id); return e && e.parentElement; };
  const btnTexto = (raiz, re) => [...document.querySelectorAll(raiz + ' button')].find(b => re.test(b.textContent));

  // abre a aba OS (lista "Minhas OS") sem passar por iframe
  async function abaOS() {
    try { if (!document.getElementById('sec-op').classList.contains('active')) ir('Op'); } catch (e) { }
    try { if (!/orange/.test(document.getElementById('op-tab-os').style.color)) opTab('os'); } catch (e) { }
    for (let n = 0; n < 12; n++) { if (!document.querySelector('#os-lista .loading')) break; await esperar(250); }
  }
  // primeiro card "Em andamento" (tem o botão Registrar); senão o primeiro card; senão a lista
  const cardOS = () => { const b = document.querySelector('#os-lista .os-btn[onclick^="abrirModalParte"]'); return (b && b.closest('.os-card')) || document.querySelector('#os-lista .os-card') || document.getElementById('os-lista'); };
  async function abrirFormParte() {
    const m = document.getElementById('modal-parte');
    if (m && m.style.display === 'flex') return;
    const b = document.querySelector('#os-lista .os-btn[onclick^="abrirModalParte"]');
    const id = b ? (b.getAttribute('onclick').match(/'([^']+)'/) || [])[1] : '00000000-0000-0000-0000-000000000000';
    try { await abrirModalParte(id); } catch (e) { }
    await esperar(600);
  }
  async function fecharForm() { try { fecharModalDireto(); } catch (e) { } }
  async function painelConta() {
    if (document.getElementById('modal-conta')) return;
    try { abrirPainelConta(); } catch (e) { }
    await esperar(1200);
  }
  function fecharConta() { const m = document.getElementById('modal-conta'); if (m) m.remove(); }
  // os controles do motor (🔊 1x ▶) ficam no canto superior direito, em cima do ⟳ e do avatar: nesses passos desce-os
  const ctl = baixo => { const c = document.querySelector('.gg-ctl'); if (c) c.style.top = baixo ? (window.innerHeight - 150) + 'px' : ''; };

  window.GUIAS_APP = {
    parte: {
      titulo: 'Parte diária (OS)',
      pop: 'Manual do colaborador',
      passos: [
        { titulo: 'Seu dia de trabalho, registrado em 1 minuto 📝',
          texto: '<b>Bem-vindo à demonstração da parte diária.</b><br><br>Você vai ver onde ficam as suas OS, como abrir o <b>Registrar</b>, o que preencher (equipamento, horímetro, medição do dia) e como salvar. É esse registro que vira a sua comissão, a cobrança do cliente e o controle de manutenção da máquina.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: '#op-tab-os', antes: abaOS, titulo: 'Minhas OS',
          texto: 'A aba <b>⚙️ OS</b> abre em <b>Minhas OS</b>: só as ordens de serviço que a gestão abriu <b>no seu nome</b>. Cada cartão é uma obra. Não achou a sua? O <b>+</b> laranja no canto abre uma OS avulsa — informe a obra e a gestão completa o resto no escritório.' },
        { alvo: cardOS, titulo: 'O cartão da OS',
          texto: 'Número da OS, situação (<b>Em andamento</b> ou <b>Aguardando fechamento</b>), cliente, <b>equipamento</b> e <b>regime</b> de cobrança. Se a gestão cadastrou o endereço, aparece o 📍 com <b>🧭 Navegar</b>; se deixou instruções, aparece a 💬 <b>Observação</b>. Leia antes de começar o dia.' },
        { alvo: () => document.querySelector('#os-lista .os-card .os-btns') || cardOS(), titulo: 'Registrar · Ver · Concluir',
          texto: '<b>📝 Registrar</b> abre o formulário do dia. <b>👁 Ver</b> mostra os seus registros nesta OS (e o ✎ para corrigir). <b>✅ Concluir</b> é só quando a obra acabou: a OS vai para "aguardando fechamento" pela administração. Mudou de obra ou de máquina? <b>🔄 Passar OS</b> tira a OS da sua lista e avisa a gestão.' },
        { alvo: '#m-data', antes: abrirFormParte, titulo: 'Data do registro',
          texto: 'O formulário <b>Registrar Dia</b> já nasce com a data de <b>hoje</b>. Pode voltar para um dia esquecido, mas o sistema não aceita data futura e pede confirmação se passar de 2 meses.' },
        { alvo: '#m-vinculo', titulo: 'Vínculo',
          texto: '<b>Próprio (Garra)</b> é o normal: máquina da Garra, operador da Garra. <b>Equipamento de terceiro</b> troca a lista por um campo livre e pede o dono. <b>Diarista</b> é alguém de fora operando a nossa máquina — informe o nome. Nesses dois, o campo Operador some: as horas não caem em colaborador interno.' },
        { alvo: '#m-equipamento', titulo: 'Equipamento',
          texto: 'Vem <b>pré-selecionado com a máquina da OS</b>. Trocou de máquina no dia? Escolha a certa: ao trocar, o horímetro e o KM do equipamento anterior são apagados e o sistema puxa a última leitura do novo. Se o mesmo equipamento já tem registro nesta data, você recebe um alerta antes de salvar.' },
        { alvo: '#m-operador', titulo: 'Operador / Motorista',
          texto: 'Quem operou. O sistema preenche sozinho: o operador da OS, senão o responsável do equipamento, senão <b>você</b>. É este nome que recebe as horas na comissão — confira.' },
        { alvo: '#bloco-horimetro', titulo: 'Horímetro da máquina',
          texto: 'Controle interno, sempre visível. O <b>Inicial</b> vem com a <b>última leitura</b> conhecida da máquina — confira no painel. Digite o <b>Final</b> e o app calcula as horas de máquina. É o horímetro que move as revisões. De manhã pode salvar só o inicial: o registro fica <b>em aberto</b> e você completa o final no fim do dia pelo ✎ em Ver.' },
        { alvo: () => document.getElementById('btn-med-horimetro') && document.getElementById('btn-med-horimetro').parentElement.parentElement, titulo: 'Medição de cobrança',
          texto: 'Como <b>este dia é cobrado do cliente</b>: ⏱️ Hora, 📅 Diária, 🚛 Viagem, 📏 Metros ou 🛣️ KM. O app <b>sugere pelo regime da OS</b> (ou pela máquina) e abre o bloco certo: quantidade de diárias, de viagens, metros executados ou KM inicial/final. Toque em outro botão se o combinado do dia for diferente.' },
        { alvo: '#m-bloco-relogio', antes: async () => { try { window._medicaoAtual = null; selecionarMedicao('horimetro'); } catch (e) { } await esperar(300); }, titulo: 'Hora início / fim (só na medição Hora)',
          texto: 'Na cobrança por <b>hora</b> você pode informar o relógio: hora início, hora fim e <b>dia corrido</b>. O app desconta 1h de almoço quando a jornada cruza o meio-dia. Preencheu o relógio? <b>Ele vale para a cobrança</b>; o horímetro fica como controle da máquina. Deixe vazio se cobrar pelo horímetro.' },
        { alvo: '#m-por-conta', titulo: 'Por conta de · Observação',
          texto: '<b>Cliente</b> é o padrão: o dia é cobrado dele. Marque <b>Empresa (Garra)</b> quando o serviço é nosso (deslocamento interno, retrabalho). Na <b>Observação</b> logo abaixo, escreva o que a gestão precisa saber: chuva, máquina parada, frente trocada.' },
        { alvo: '#btn-salvar-parte', titulo: 'Salvar registro',
          texto: '<b>✅ Salvar registro</b> grava e fecha. <b>+ Salvar e novo</b> grava e mantém a OS, o equipamento e o operador para o próximo dia. Avisos em ⚠️ (horímetro muito longe da última leitura, mais de 24h no dia) pedem <b>confirmação</b> — corrija se foi erro de digitação. Sem sinal? O registro fica guardado no aparelho e sobe sozinho quando a internet voltar.' },
        { alvo: () => document.querySelector('#os-lista > div') || document.getElementById('op-tab-hist'), antes: async () => { await fecharForm(); try { opTab('hist'); } catch (e) { } for (let n = 0; n < 12; n++) { if (!document.querySelector('#os-lista .loading')) break; await esperar(250); } await esperar(200); }, titulo: 'Histórico',
          texto: 'A aba <b>🕐 Histórico</b> mostra <b>suas horas do mês</b> (dias, apontamentos, OS) e as OS já concluídas em que você registrou neste mês. Toque numa OS para ver as partes registradas. É aqui que você confere se o mês fechou certo antes da comissão.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina de todo dia: <b>Minhas OS → 📝 Registrar → conferir equipamento e horímetro → medição do dia → Salvar</b>. Acabou a obra: ✅ Concluir. Mudou de obra: 🔄 Passar OS. O passo a passo completo está no <b>Manual do colaborador</b> (avatar › Minha conta). Repita este tour quando quiser pelo ▶ Como fazer.' },
      ],
    },

    app: {
      titulo: 'Conhecendo o app',
      pop: 'Manual do colaborador',
      passos: [],   // montado em guiaAbrir conforme as abas liberadas para o perfil
    },
  };

  // Roteiro "app": só as abas visíveis para o usuário entram (cada perfil vê um conjunto diferente)
  async function montarApp() {
    const p = [];
    p.push({ titulo: 'Bem-vindo ao Garra Sistemas 📱',
      texto: '<b>Bem-vindo à demonstração do app.</b><br><br>Em 2 minutos você vai conhecer a <b>barra de abas</b> (o que cada uma faz), o botão de sincronizar, o seu <b>avatar</b> com os avisos e o painel <b>Minha conta</b>: notificações no aparelho, manual, senha e saída.<br><br>Nada é gravado durante a demonstração.',
      botao: 'Começar demonstração' });
    p.push({ alvo: '.bottom-nav', antes: async () => { ctl(false); fecharConta(); await abaOS(); }, titulo: 'A barra de abas',
      texto: 'Tudo fica na barra de baixo. Só aparecem as abas <b>liberadas para o seu perfil</b> — operador vê OS, Checklist e Abastecer; a gestão libera mais módulos no painel de permissões. A aba laranja é a aberta; arraste a barra se houver mais abas. O app lembra a última aba usada e abre nela da próxima vez.' + (navVis(0) ? ' A aba <b>🌿 Jardinagem</b> é um salto para o app de fotos e KM da jardinagem.' : '') });
    const abas = [
      [1, '⚙️ OS', 'Suas ordens de serviço e a <b>parte diária</b>: o registro de cada dia de trabalho (equipamento, horímetro, medição). Tem um tour só para ela: <b>▶ Como fazer › Parte diária</b>.'],
      [2, '✓ Checklist', 'A <b>inspeção do equipamento</b> antes de operar, com fotos. Não conformidade marcada aqui vira pedido para a manutenção. Quem tem <b>🚗 Logística</b> faz pelo mesmo caminho o checklist de deslocamento.'],
      [4, '🛒 Compras', 'As <b>ordens de compra</b>: criar a OC e <b>solicitar aprovação</b>; quem aprova, aprova ou rejeita; depois a OC vai ao fornecedor por WhatsApp e o recebimento é registrado aqui. Cada um vê o que a sua permissão libera.'],
      [5, '📨 Pedir OT', 'Avisar a manutenção de um defeito: equipamento, urgência, descrição e foto. O pedido entra na triagem e você recebe aviso quando virar OT ou for recusado.'],
      [6, '🔧 Manutenção', 'A gestão da manutenção no celular: triagem dos pedidos, ordens de trabalho, frota e conferência de abastecimentos. Para quem tem o módulo liberado.'],
      [7, '⛽ Abastecer', 'Registrar o abastecimento com <b>foto da notinha</b> e do horímetro. Tem demonstração própria dentro da aba.'],
    ];
    for (const [i, t, tx] of abas) { if (navVis(i)) p.push({ alvo: () => navVis(i) || document.querySelector('.bottom-nav'), antes: () => { ctl(false); fecharConta(); }, titulo: t, texto: tx }); }
    p.push({ alvo: '.btn-sync', antes: () => { ctl(true); fecharConta(); }, titulo: '⟳ Sincronizar',
      texto: 'Recarrega o app e busca o que mudou no servidor. Ao lado aparece <b>● Online / ● Offline</b> quando a conexão muda. Sem internet, os registros ficam guardados no aparelho e sobem sozinhos quando o sinal volta — você não precisa refazer nada.' });
    p.push({ alvo: '#user-avatar', antes: () => { ctl(true); fecharConta(); }, titulo: 'Seu avatar e os avisos',
      texto: 'A bolinha laranja com a sua inicial abre <b>Minha conta</b>. Quando há aviso novo ou mural não lido, aparece um <b>número vermelho</b> em cima dela (e no ícone do app, se instalado). Vamos abrir.' });
    p.push({ alvo: () => document.querySelector('#notif-lista-mob > div[onclick]') || secaoConta('notif-lista-mob'), antes: async () => { ctl(false); await painelConta(); }, titulo: 'Avisos',
      texto: 'Os avisos do sistema para você: OS aberta no seu nome, pedido que virou OT, abastecimento divergente… Os <b>não lidos</b> ficam com a faixa laranja. Toque num aviso para abrir a tela certa; o <b>✕</b> apaga um; <b>Marcar todos como lidos</b> e <b>Limpar lidos</b> organizam a lista.' });
    p.push({ alvo: () => secaoConta('mural-avisos-mob'), titulo: 'Mural e Manual do colaborador',
      texto: 'O <b>Mural</b> traz os comunicados da empresa para todos. O botão <b>📖 Manual do colaborador</b> abre o manual com os procedimentos (POPs) — é a referência quando bater dúvida fora do tour. No topo do manual dá para escolher o idioma: <b>PT · ES · EN</b>.' });
    p.push({ alvo: () => { const s = document.getElementById('push-switch'); return s && s.parentElement; }, titulo: 'Notificações neste aparelho',
      texto: 'Ligue <b>Receber avisos neste aparelho</b> para o celular tocar quando chegar aviso, mesmo com o app fechado. Em <b>O que avisar</b> você escolhe os tipos. No iPhone só funciona com o app <b>adicionado à tela de início</b>; "Bloqueado" = permissão negada nas configurações do navegador.' });
    const bio = await (async () => { try { return await bioSuportada(); } catch (e) { return false; } })();
    if (bio) p.push({ alvo: '#sec-bio-conta', titulo: 'Entrar com a digital',
      texto: 'Este aparelho tem leitor de digital ou Face ID: ligue <b>Entrar com a digital</b> e, nas próximas vezes, o app entra sem senha (a senha continua valendo). Logo abaixo ficam <b>Alterar senha</b> e <b>↩ Sair</b> — saia só se for trocar de usuário no mesmo celular.' });
    else p.push({ alvo: () => { const b = btnTexto('#modal-conta', /Alterar senha/); return b && b.parentElement; }, titulo: 'Alterar senha · Sair',
      texto: 'Troque a sua senha aqui quando precisar (senha atual, nova e confirmação). <b>↩ Sair</b> fecha a sessão — use só para trocar de usuário no mesmo celular; no dia a dia deixe logado, o app abre direto.' });
    p.push({ titulo: 'Pronto! ✅',
      texto: 'Resumo: <b>barra de baixo</b> = módulos liberados para você; <b>⟳</b> sincroniza; <b>avatar</b> = avisos, mural, manual, notificações e senha. Cada aba tem o seu ▶ Como fazer. Dúvida: <b>Manual do colaborador</b> em Minha conta.' });
    return p;
  }

  window.guiaAbrir = async function (nome) {
    const g = window.GUIAS_APP[nome]; if (!g || !window.GarraGuia) return;
    const menu = document.getElementById('guia-menu'); if (menu) menu.remove();
    if (nome === 'app') g.passos = await montarApp();
    g.aoSair = () => { try { fecharModalDireto(); } catch (e) { } fecharConta(); try { if (document.getElementById('sec-op').classList.contains('active')) opTab('os'); } catch (e) { } };
    GarraGuia.iniciar(g);
  };
  // Menu com os dois roteiros (link ▶ Como fazer na aba OS e botão em Minha conta)
  window.guiaMenu = function (ev) {
    ev && ev.stopPropagation(); ev && ev.preventDefault();
    let m = document.getElementById('guia-menu');
    if (m) { m.remove(); return; }
    m = document.createElement('div'); m.id = 'guia-menu';
    m.style.cssText = 'position:fixed;left:12px;right:12px;bottom:84px;z-index:9999;background:#fff;color:#0F172A;border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.3);padding:6px;font-size:14px;font-family:system-ui,sans-serif';
    const n = k => k === 'app' ? 13 : window.GUIAS_APP[k].passos.length - 2;
    m.innerHTML = '<div style="padding:8px 12px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
      + Object.entries(window.GUIAS_APP).map(([k, g]) => '<div style="padding:12px;cursor:pointer;border-radius:8px;border-top:1px solid #F1F5F9" onclick="guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8;font-size:11px">· ~' + n(k) + ' passos</span></div>').join('');
    document.body.appendChild(m);
    setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
  };
  // ?guia=parte | ?guia=app — espera o app (login concluído e aba OS montada) e começa
  (function () {
    const q = window._GUIA_Q; if (!q || !window.GUIAS_APP[q]) return;
    let n = 0; const t = setInterval(() => {
      const app = document.getElementById('screen-app'); n++;
      let logado = false; try { logado = !!USUARIO; } catch (e) { }   // USUARIO é `let` do mobile.html (não está em window)
      if (app && app.style.display === 'flex' && logado && document.querySelector('#os-lista .os-card, #os-lista .empty')) { clearInterval(t); setTimeout(() => guiaAbrir(q), 800); }
      else if (n > 60) clearInterval(t);
    }, 500);
  })();
})();
