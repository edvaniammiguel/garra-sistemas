/* (05/11/2026) Tour guiado da tela 💬 Atendimento WhatsApp (caixa de entrada do número "Interno - Garra") — GarraGuia.
 * Modo demonstração: mostra a tela real, não grava nada. Abre pelo botão "▶ Como fazer" (cabeçalho no computador,
 * ▶ ao lado da busca no celular) ou por /atendimento?guia=atender. Passos marcados `soGestao` só aparecem para quem
 * tem o módulo Atendimento — Gerência (EU.gestao); os demais veem o roteiro da atendente. */
window.GUIAS_ATENDIMENTO = {
  atender: {
    titulo: 'Atender pelo WhatsApp da Garra',
    pop: 'Atendimento WhatsApp',
    passos: [
      { titulo: 'Chegou mensagem no Interno - Garra 💬',
        texto: '<b>Bem-vinda à demonstração do Atendimento.</b><br><br>Você vai ver como as conversas do número da Garra chegam aqui divididas por <b>setor</b>, como abrir, responder com sua assinatura, transferir, encerrar e o que o <b>bot</b> faz sozinho antes de a conversa chegar a você.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },

      { alvo: '#filtros', antes: async () => { await _gaLista(); },
        titulo: 'Os filtros da lista',
        texto: '<b>Minhas</b> = conversas atribuídas a você. <b>Todas</b> = as abertas nos setores que você atende ou pode ver. <b>Pendentes</b> = aguardando o colaborador responder. <b>Encerradas</b> = últimos 30 dias. O número ao lado é a contagem de conversas abertas. A lista se atualiza sozinha a cada 5 s e toca um bipe quando entra mensagem nova.' },

      { alvo: () => document.querySelector('#itens .item'),
        antes: async () => { await _gaLista('todas'); },
        titulo: 'Cada linha é um colaborador',
        texto: 'Nome (do cadastro, se o número estiver vinculado), hora da última atividade, prévia da mensagem (🤖 = foi o bot) e a etiqueta do <b>setor</b> do atendimento. Bolinha verde = mensagens <b>não lidas</b>. <b>Leitura</b> = você só vê, não responde (setor de outra pessoa). <b>Pendente</b> = esperando o colaborador.' },

      { alvo: () => document.querySelector('#conversa .cv-head'),
        antes: async () => { await _gaAbrir(); },
        titulo: 'Abrindo a conversa',
        texto: 'Em cima: quem é, o <b>setor</b> do atendimento, quem está atendendo e o telefone. A mesma pessoa pode ter <b>um atendimento aberto por setor</b> (ex.: Manutenção e Financeiro ao mesmo tempo) — aí aparecem botões para escolher em qual você responde. Ao abrir, as mensagens dela ficam marcadas como lidas.' },

      { alvo: () => document.querySelector('#msgs .b.bot') || document.querySelector('#msgs'), posicao: 'esquerda',
        titulo: 'O fio e o que o bot já fez',
        texto: 'Balão <b>branco</b> à esquerda = o colaborador. <b>Tracejado 🤖</b> = o bot: ele mandou a saudação com o <b>menu numerado</b>, o colaborador digitou o número do setor e o atendimento abriu já com o responsável do setor — você recebeu o aviso no sino. <b>Azul</b> = resposta da atendente, com a assinatura. Linhas cinza no meio são eventos: ▶ aberto, ⇄ transferido, ✔ encerrado. Digitar <b>MENU</b> ou <b>0</b> mostra o menu de novo sem encerrar nada.' },

      { alvo: () => document.querySelector('#conversa .rapidas') || document.querySelector('#conversa .compor') || document.querySelector('#conversa .fechado'),
        titulo: 'Respostas rápidas',
        texto: 'Botões com textos prontos — <b>Link do app</b>, <b>Pedir foto</b>, <b>Vem ao escritório</b>. Toque em um e o texto cai no campo para você ajustar antes de enviar. A Gerência cadastra outros em Configurar › Respostas rápidas.' },

      { alvo: () => document.querySelector('#compor') || document.querySelector('#conversa .fechado'),
        titulo: 'Responder',
        texto: 'Escreva e envie (no computador, <b>Enter</b> envia). A mensagem sai no WhatsApp com a sua <b>assinatura</b> na frente — ex.: <b>Manutenção - BRUNA:</b> — para o colaborador saber com quem fala. 😊 emojis, 📎 foto/PDF/vídeo, 🎤 grava áudio (toque de novo para enviar). Toque numa mensagem do fio para <b>↩ Responder</b> citando ou reagir com 👍. Ao responder, a conversa passa a ser sua.' },

      { alvo: () => document.querySelector('#conversa [aria-label="Assumir"]') || document.querySelector('#conversa .cv-acoes') || document.querySelector('#conversa .cv-head'),
        titulo: '✋ Assumir',
        texto: 'A conversa chega para o <b>responsável do setor</b>. Se outra pessoa do setor vai cuidar, ela toca em <b>Assumir</b>: a conversa passa para a lista <b>Minhas</b> dela, sem mudar de setor e sem aviso ao colaborador. Se a conversa já está com você, o botão não aparece.' },

      { alvo: () => document.querySelector('#sh-transf .sheet'),
        antes: async () => { if (typeof abrirTransf === 'function' && typeof CONV !== 'undefined' && CONV && CONV.atual && CONV.atual.pode_atender) abrirTransf(); await _gaEsperar(400); },
        titulo: '⇄ Transferir para outro setor',
        texto: 'Mensagem do colaborador não é do seu setor? Escolha o setor certo: a conversa vai para o <b>responsável dele</b>, com todo o histórico. O bot avisa o colaborador ("Olá, você está falando com … da MANUTENÇÃO") — marque <b>sem avisar</b> se não quiser a mensagem. Setor 🔒 é restrito (Financeiro e RH): só quem tem liberação enxerga.' },

      { alvo: () => document.querySelector('#sh-enc .sheet'),
        antes: async () => { fechar(); if (typeof abrirEnc === 'function' && typeof CONV !== 'undefined' && CONV && CONV.atual && CONV.atual.pode_atender) abrirEnc(); await _gaEsperar(400); },
        titulo: '✔ Encerrar ou ⏳ deixar pendente',
        texto: 'Resolveu? <b>Encerrar</b> fecha o atendimento e manda ao colaborador "<i>Fulana encerrou atendimento. Se precisar de mais alguma coisa é só chamar</i>" (desmarque para encerrar em silêncio). A conversa vai para <b>Encerradas</b>; se ele escrever de novo, o bot mostra o menu. Está esperando ele mandar algo? <b>Deixar pendente</b> — a conversa ganha a etiqueta <b>Pendente</b> e entra no filtro Pendentes; quando ele responder, volta sozinha para "em atendimento".' },

      { alvo: () => document.querySelector('.topo-lista .btn-ic.lar'),
        antes: async () => { fechar(); _gaVoltarLista(); await _gaEsperar(300); },
        titulo: '＋ Nova conversa',
        texto: 'Você também pode <b>começar</b> a conversa: busque o colaborador pelo nome ou digite DDD + número, escolha o setor e o bot manda a saudação ("Olá …, aqui é …. Tudo bem?"). O atendimento já nasce com você. Isso substitui os fluxos "INICIAR CONVERSA" do BotConversa.' },

      { soGestao: true, alvo: '#f-sem',
        titulo: 'Sem setor (Gerência)',
        texto: 'Quem escreveu nos últimos 3 dias e <b>não digitou um número do menu</b> aparece aqui. Toque em <b>Colocar no setor</b> para abrir o atendimento no setor certo — as mensagens dele entram junto.' },

      { soGestao: true, alvo: () => { const a = document.getElementById('btn-cfg'), b = document.getElementById('btn-cfg-emb'); return (b && !b.classList.contains('oculto') && b.offsetWidth) ? b : a; },
        titulo: '⚙ Configurar (Gerência)',
        texto: '<b>Conexão</b> (QR Code do número — o estado aparece no cabeçalho), <b>Setores e atendentes</b> (quem atende cada setor e a assinatura), <b>Acesso entre setores</b> (liberar alguém para ver ou atender outro setor, com prazo e motivo), <b>Menu</b> (as opções numeradas que o bot mostra), <b>Textos do bot</b> e <b>Respostas rápidas</b>.' },

      { titulo: 'Pronto! ✅',
        texto: 'Rotina: <b>Minhas → abrir → responder</b> (assinatura automática). Não é seu setor? <b>⇄ Transferir</b>. Esperando o colaborador? <b>⏳ Pendente</b>. Resolveu? <b>✔ Encerrar</b>. O bot cuida do menu e abre a conversa no setor certo — você só entra quando a mensagem é para gente. Repita este tour quando quiser pelo ▶ Como fazer.' },
    ],
  },
};

/* utilitários do roteiro (só leitura da tela) */
function _gaEsperar(ms) { return new Promise(r => setTimeout(r, ms || 500)); }
async function _gaLista(filtro) {
  try {
    if (filtro && typeof FILTRO !== 'undefined' && FILTRO !== filtro) {
      const b = document.querySelector('#filtros .filtro[data-f="' + filtro + '"]'); if (b) b.click();   // mesmo caminho do usuário: muda FILTRO e recarrega
      await _gaEsperar(900);
    }
    if (typeof ITENS !== 'undefined' && !ITENS.length && filtro !== 'todas') { const b = document.querySelector('#filtros .filtro[data-f="todas"]'); if (b) b.click(); await _gaEsperar(900); }
  } catch (e) { }
}
async function _gaAbrir() {
  try {
    await _gaLista('todas');
    if (typeof ITENS === 'undefined' || !ITENS.length) return;
    // prefere uma conversa que eu posso atender e que NÃO está comigo (para o passo Assumir aparecer)
    const eu = (typeof EU !== 'undefined' && EU) ? EU.id : null;
    const it = ITENS.find(i => i.pode_atender && i.atribuido_a !== eu) || ITENS.find(i => i.pode_atender) || ITENS[0];
    if (typeof TEL === 'undefined' || TEL !== it.telefone) await abrirConversa(it.telefone, it.id);
    await _gaEsperar(700);
  } catch (e) { }
}
function _gaVoltarLista() {
  try { if (innerWidth < 900 && typeof voltar === 'function') voltar(); } catch (e) { }   // no celular a lista e a conversa são telas separadas
}

window.guiaAbrir = function (nome) {
  const base = window.GUIAS_ATENDIMENTO[nome]; if (!base || !window.GarraGuia) return;
  const gestao = !!(typeof EU !== 'undefined' && EU && EU.gestao);
  const g = Object.assign({}, base, { passos: base.passos.filter(p => !p.soGestao || gestao) });
  g.aoSair = () => {
    try { fechar(); } catch (e) { }
    try { const m = document.getElementById('guia-menu'); if (m) m.remove(); } catch (e) { }
  };
  GarraGuia.iniciar(g);
};
// ?guia=atender — espera o login (EU) e a lista carregar e começa
(function () {
  const q = window._GUIA_Q; if (!q) return;
  let n = 0; const t = setInterval(() => {
    n++;
    const ok = (typeof EU !== 'undefined' && EU) && document.getElementById('itens') && !/Carregando/.test(document.getElementById('itens').textContent);
    if (ok) { clearInterval(t); setTimeout(() => guiaAbrir(q), 700); } else if (n > 60) clearInterval(t);
  }, 500);
})();
