/* (05/11/2026) Tour guiado da tela 📨 Pedir OT / Minhas / OTs (app do celular, /manutencao/pedido) — GarraGuia.
 * Texto segue o POP-MNT-01 (Pedir OT e registrar execução pelo aplicativo). Modo demonstração: mostra a tela
 * real, não grava — os alvos ficam bloqueados ao clique. Abre pelo link "▶ Como fazer" no topo da aba Pedido
 * ou por /manutencao/pedido?guia=pedir (no app: /mobile?abrir=/manutencao/pedido?guia=pedir).
 * Um roteiro só por enquanto ("pedir"); o padrão (objeto + guiaAbrir + guiaMenu) já aceita outros.
 * O roteiro é montado na hora de abrir, porque a aba 🔧 OTs só existe para quem tem o módulo Executar OT
 * (mecânico/gestor com o módulo): operador vê um texto "só mecânico" no lugar e o tour não quebra. */
window.GUIAS_PEDIDO = {
  pedir: {
    titulo: 'Pedir OT e registrar execução',
    pop: 'POP-MNT-01',
    passos: [],              // preenchido por _gpPassos() em guiaAbrir (depende do perfil)
  },
};

// ── apoio ────────────────────────────────────────────────────────────────────────────────────
function _gpEspera(ms) { return new Promise(r => setTimeout(r, ms)); }
function _gpVisivel(id) { const e = document.getElementById(id); return e && e.offsetParent !== null ? e : null; }
let _gpPre = null;   // o que o usuário já tinha digitado no pedido antes do tour (devolvido ao sair)
let _gpDemoOT = false;
function _gpPreencherPedido() {
  try {
    const d = document.getElementById('pd-desc'), eq = document.getElementById('pd-eq');
    if (!_gpPre) _gpPre = { desc: d.value, eq: eq.value, urg: typeof URG !== 'undefined' ? URG : 'normal' };
    if (!d.value) d.value = 'Vazamento de óleo no comando, mangueira do braço. Equipamento trabalhando, mas pingando bastante.';
    if (!eq.value && eq.options.length > 1) eq.selectedIndex = 1;
    if (typeof urg === 'function') urg('alta');
  } catch (e) { }
}
function _gpDevolverPedido() {
  try {
    if (_gpPre) {
      document.getElementById('pd-desc').value = _gpPre.desc;
      document.getElementById('pd-eq').value = _gpPre.eq;
      if (typeof urg === 'function') urg(_gpPre.urg || 'normal');
    }
  } catch (e) { }
  _gpPre = null;
}
// Abre a tela "Registrar execução" numa OT real do usuário: mecânico → OT da aba OTs (prefere uma sua, com
// horas já registradas e ainda não sinalizada, para mostrar o "Só concluir"); quem só pede → OT de um pedido
// seu em "Minhas". Sem OT nenhuma, monta a tela de exemplo sem chamar o servidor.
async function _gpAbrirOT() {
  if (document.getElementById('tela-apont').style.display === 'block') return;
  let o = null;
  try {
    if (typeof EXECUTOR !== 'undefined' && EXECUTOR) {
      if (!(typeof OTS !== 'undefined' && OTS.length)) { const r = await api('/manutencao/api/ots-abertas-mobile'); OTS = r.ots || []; }
      o = OTS.find(x => x.minha && x.apontamentos && !x.sinal_concluido) || OTS.find(x => x.apontamentos && !x.sinal_concluido) || OTS.find(x => x.minha) || OTS[0];
      if (o) { apAbrir(o.id, o.numero, o.equipamento_codigo, o.descricao); return; }
    }
    const ps = await api('/manutencao/api/meus-pedidos');
    const p = (ps || []).find(x => x.ot_id && ['em_andamento', 'aguardando_peca', 'aberta'].includes(x.ot_status));
    if (p) { apAbrir(p.ot_id, p.ot_numero, p.equipamento_codigo, p.descricao); return; }
  } catch (e) { }
  // exemplo (sem OT em aberto para este usuário)
  _gpDemoOT = true; AP_OT = null; AP_EDIT = null; AP_ITENS = [];
  AP_FOTOS = { nota: [], servico: [] }; apFotosRender('nota'); apFotosRender('servico');
  document.getElementById('ap-ot').innerHTML = '<div style="font-size:16px;color:#1A2A5E;margin:4px 0"><b>OT-0000 (exemplo)</b> <span style="color:#E8730C;font-weight:800">· EH-50</span></div>'
    + '<div style="font-size:13px;color:#0F172A;margin-bottom:4px">Vazamento de óleo no comando</div><div>O relato vai para o histórico; o encerramento é da gestão.</div>';
  ['ap-relato', 'ap-horas', 'ap-hor'].forEach(i => document.getElementById(i).value = '');
  document.getElementById('ap-fim').checked = false;
  document.getElementById('ap-form').style.display = ''; document.getElementById('ap-btn').style.display = '';
  document.getElementById('ap-fechada').style.display = 'none'; document.getElementById('ap-so-concluir').style.display = 'none';
  document.getElementById('ap-cancel').style.display = 'none';
  document.getElementById('ap-lista').textContent = 'Nada registrado ainda (exemplo — você não tem OT em aberto).';
  tela('tela-apont');
}
// Alvo "união": um retângulo invisível (position:absolute, coordenadas do documento) cobrindo vários elementos,
// para o spotlight mostrar ✅ concluído + 💾 Registrar + ✅ Só concluir juntos. Removido ao sair.
function _gpUniao(els) {
  els = els.filter(e => e && e.offsetParent !== null); if (!els.length) return null;
  let a = document.getElementById('gp-alvo');
  if (!a) { a = document.createElement('div'); a.id = 'gp-alvo'; a.style.cssText = 'position:absolute;pointer-events:none;z-index:0'; document.body.appendChild(a); }
  const rs = els.map(e => e.getBoundingClientRect()), sx = window.scrollX, sy = window.scrollY;
  const l = Math.min(...rs.map(r => r.left)), t = Math.min(...rs.map(r => r.top)), r2 = Math.max(...rs.map(r => r.right)), b = Math.max(...rs.map(r => r.bottom));
  a.style.left = (l + sx) + 'px'; a.style.top = (t + sy) + 'px'; a.style.width = (r2 - l) + 'px'; a.style.height = (b - t) + 'px';
  return a;
}
// Os controles do motor (🔊 1x ▶) ficam fixos no canto superior direito e cobririam a aba 🔧 OTs:
// durante o tour o conteúdo desce um pouco. Volta ao normal ao sair.
function _gpEspaco(on) {
  let st = document.getElementById('gp-css');
  if (on && !st) { st = document.createElement('style'); st.id = 'gp-css'; st.textContent = 'main{padding-top:58px !important}'; document.head.appendChild(st); }
  if (!on && st) st.remove();
}
function _gpFecharOT() {
  try {
    AP_EDIT = null; AP_FOTOS = { nota: [], servico: [] }; apFotosRender('nota'); apFotosRender('servico');
    ['ap-relato', 'ap-horas', 'ap-hor'].forEach(i => document.getElementById(i).value = '');
    document.getElementById('ap-fim').checked = false;
    document.getElementById('ap-btn').textContent = '💾 Registrar na OT';
    document.getElementById('ap-cancel').style.display = 'none';
    if (_gpDemoOT) { AP_OT = null; document.getElementById('ap-lista').textContent = 'Carregando…'; }
  } catch (e) { }
  _gpDemoOT = false;
  const a = document.getElementById('gp-alvo'); if (a) a.remove();
}

// ── roteiro ──────────────────────────────────────────────────────────────────────────────────
function _gpPassos(executor) {
  const P = [];
  P.push({ titulo: 'Quebrou? Peça a OT e registre o serviço 🔧',
    texto: '<b>Bem-vindo à demonstração do Pedir OT.</b><br><br>Você vai ver como <b>abrir um pedido</b> de manutenção, <b>acompanhar</b> se virou OT e, para quem executa, <b>registrar a execução</b> com relato, horas, horímetro e notinhas. É esse registro que vira o histórico e o custo do equipamento.<br><br>Nada é gravado durante a demonstração.',
    botao: 'Começar demonstração' });
  P.push({ alvo: '#abas', antes: async () => { verAba('novo'); _gpPreencherPedido(); await _gpEspera(300); },
    titulo: 'As abas do aplicativo',
    texto: '<b>＋ Pedido</b> abre um pedido novo. <b>📋 Minhas</b> acompanha o que você pediu. <b>🔧 OTs</b> lista todas as OTs em aberto — e só aparece para quem tem o módulo <b>Executar OT</b> (mecânico). Operador vê só as duas primeiras.' });
  P.push({ alvo: '#pd-eq', titulo: 'Equipamento',
    texto: 'Escolha o <b>equipamento ou veículo</b> na lista. É ele que vai receber a OT e o custo do serviço — pedido no equipamento errado vira histórico errado.' });
  P.push({ alvo: '#pd-desc', titulo: 'O que está acontecendo?',
    texto: 'Descreva do jeito que se fala: "vazamento de óleo no comando", "freio baixo", "barulho na esteira". Pode usar o <b>🎤 Ditar</b>. Evite "problema" ou "ver equipamento" — a triagem precisa entender sem ligar para você.' });
  P.push({ alvo: () => document.querySelector('#tela-pedido .urg'), titulo: 'Urgência',
    texto: '<b>🟢 Normal</b>: o equipamento trabalha normal. <b>🟠 Alta</b>: trabalha, mas atrapalha ou pode piorar. <b>🔴 Urgente</b>: <b>equipamento parado</b>. Urgente entra na frente da fila da manutenção — use só quando for parado mesmo.' });
  P.push({ alvo: () => document.querySelector('#tela-pedido .btn.p'), titulo: 'Enviar pedido',
    texto: 'Toque em <b>📨 Enviar pedido</b>. Ele cai na <b>triagem da manutenção</b>, que decide se abre a Ordem de Trabalho ou recusa (com motivo). Sem internet o pedido não sai — procure sinal e envie no mesmo dia.' });
  P.push({ alvo: '#minhas-lista', antes: async () => { verAba('minhas'); await _gpEspera(900); }, titulo: 'Minhas — acompanhar o pedido',
    texto: 'Cada pedido mostra o estado: <b>⏳ Na triagem</b>, <b>🔧 Virou OT</b> (com o número e o estado da OT) ou <b>✖ Recusado</b> com o motivo. Aparecem os pedidos em aberto e os dos <b>últimos 30 dias</b>. Quando vira OT, o botão <b>🔧 Registrar execução</b> aparece aqui embaixo do pedido.' });
  if (executor) {
    P.push({ alvo: () => document.querySelector('#ots-lista > div') || document.getElementById('tela-ots'),
      antes: async () => { verAba('ots'); await _gpEspera(1000); }, titulo: 'OTs em aberto (mecânico)',
      texto: 'Todas as OTs em aberto da frota, com equipamento, serviço, urgência e responsável — na oficina um cobre o outro. <b>Busque</b> por equipamento, nº ou serviço; marque <b>só as minhas</b> para ver as suas. OT <b>sem responsável</b> (laranja): o primeiro que registrar execução assume. <b>🔧 Registrar execução</b> abre a OT.' });
  } else {
    P.push({ alvo: '#abas', antes: async () => { verAba('minhas'); await _gpEspera(300); }, titulo: 'Aba OTs — só mecânico',
      texto: 'Você <b>não vê a aba 🔧 OTs</b>: ela lista todas as OTs em aberto e é só de quem tem o módulo <b>Executar OT</b> (mecânico). Quem só pede registra execução apenas nas OTs dos <b>próprios pedidos</b>, pelo botão em Minhas. A seguir, a tela de registro.' });
  }
  P.push({ alvo: '#ap-relato', antes: async () => { await _gpAbrirOT(); await _gpEspera(900); }, titulo: 'Registrar execução — o que foi feito',
    texto: 'Antes de escrever, confira no cabeçalho o <b>número da OT e o equipamento em laranja</b> — com várias OTs abertas é fácil trocar. Depois descreva o serviço (mínimo 10 letras, pode ditar): "trocado filtro e óleo do comando; testado sem vazamento".' });
  P.push({ alvo: '#ap-horas', titulo: 'Horas trabalhadas',
    texto: 'As horas de <b>mão de obra deste serviço</b> (ex.: 2,5). Vão para a OT como custo de mão de obra interna. <b>Obrigatórias ao concluir</b> — sem elas a OT fecha sem custo. Registro parcial pode ir sem horas (o app pergunta antes).' });
  P.push({ alvo: '#ap-hor', titulo: 'Horímetro / KM no serviço',
    texto: 'O campo mostra <b>Horímetro</b> ou <b>KM</b> conforme a máquina. Não barra o registro, mas é <b>muito importante</b>: é com essa leitura que a gestão fecha a OT e que a próxima preventiva é calculada. Ao concluir sem ela, o app avisa. Só deixe em branco se o painel estiver sem leitura — e escreva isso no relato.' });
  P.push({ alvo: () => document.querySelector('#tela-apont .fbtn'), titulo: 'Notinhas × fotos do serviço',
    texto: 'Dois campos separados, não misture. <b>🧾 Notinhas</b>: cupom ou nota de peça/serviço comprado na rua — é por ela que o financeiro lança a despesa. <b>🔧 Fotos do serviço</b>: peça trocada, antes/depois. <b>📷 Tirar foto</b> abre a câmera; <b>🖼 Galeria</b> pega fotos já tiradas (até 10 por tipo).' });
  P.push({ alvo: () => _gpUniao([document.getElementById('ap-fim') && document.getElementById('ap-fim').closest('label'), document.getElementById('ap-btn'), _gpVisivel('ap-so-concluir')]) || document.getElementById('ap-btn'),
    titulo: 'Concluir e registrar',
    texto: 'Marque <b>✅ Serviço concluído</b> só quando o equipamento estiver liberado — isso avisa a gestão, que é quem <b>encerra</b> a OT no computador. Depois <b>💾 Registrar na OT</b>: o relato grava primeiro, as fotos sobem em seguida. A OT já tem horas registradas e você só voltou para avisar que terminou? Use o atalho <b>✅ Só concluir</b>.' });
  P.push({ alvo: '#ap-lista', titulo: 'Já registrado nesta OT',
    texto: 'Tudo que já foi registrado na OT, de qualquer mecânico: data, quem fez, horas, horímetro e fotos. No seu registro, <b>✏️</b> carrega de volta nos campos para corrigir e <b>🗑️</b> exclui (com horas e fotos). Depois que a gestão encerra, a tela vira <b>🔒 somente consulta</b>.' });
  P.push({ titulo: 'Pronto! ✅',
    texto: 'Rotina: <b>Pedido → Minhas (virou OT?) → Registrar execução: relato + horas + horímetro + notinhas → ✅ concluído</b>. Mecânico acha qualquer OT na aba <b>OTs</b>. O passo a passo completo está no <b>POP-MNT-01</b>. Repita este tour quando quiser pelo ▶ Como fazer.' });
  return P;
}

window.guiaAbrir = async function (nome) {
  const g = window.GUIAS_PEDIDO[nome]; if (!g || !window.GarraGuia) return;
  if (nome === 'pedir') {
    // o perfil (tem Executar OT?) chega por chamada assíncrona depois do login — confirma antes de montar
    try { const r = await api('/manutencao/api/ots-abertas-mobile'); EXECUTOR = !!r.executor; OTS = r.ots || []; abaOtsVisivel(); } catch (e) { }
    g.passos = _gpPassos(typeof EXECUTOR !== 'undefined' && !!EXECUTOR);
  }
  g.aoSair = () => { try { _gpFecharOT(); _gpDevolverPedido(); _gpEspaco(false); verAba('novo'); } catch (e) { } };
  _gpEspaco(true);
  GarraGuia.iniciar(g);
};
window.guiaMenu = function (ev) {   // um roteiro só hoje: abre direto; com mais de um vira lista
  ev && ev.stopPropagation();
  const ks = Object.keys(window.GUIAS_PEDIDO);
  if (ks.length === 1) { guiaAbrir(ks[0]); return; }
  let m = document.getElementById('guia-menu');
  if (m) { m.remove(); return; }
  m = document.createElement('div'); m.id = 'guia-menu';
  m.style.cssText = 'position:fixed;top:48px;right:12px;z-index:9999;background:#fff;color:#0F172A;border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:6px;min-width:260px;font-size:13px';
  m.innerHTML = '<div style="padding:6px 10px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
    + ks.map(k => { const g = window.GUIAS_PEDIDO[k]; return '<div style="padding:8px 10px;cursor:pointer;border-radius:6px" onclick="document.getElementById(\'guia-menu\').remove();guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8;font-size:11px">· ' + (g.pop || '') + '</span></div>'; }).join('');
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
};
// ?guia=pedir — espera as abas aparecerem (login/SSO concluído e frota carregada) e começa
(function () {
  const q = window._GUIA_Q; if (!q) return;
  let n = 0; const t = setInterval(() => {
    const a = document.getElementById('abas'); n++;
    if (a && a.style.display === 'flex') { clearInterval(t); setTimeout(() => guiaAbrir(q), 800); } else if (n > 60) clearInterval(t);
  }, 500);
})();
