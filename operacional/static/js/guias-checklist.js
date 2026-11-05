/* (05/11/2026) Tour guiado da tela ✓ Checklist digital (operacional/checklist/index.html) — GarraGuia.
 * Três roteiros, um por papel: operador (POP-CHK-01: preencher e enviar o check list do equipamento),
 * gestor (POP-CHK-02: tratar os envios, ranking e pontos) e logistica (aba 🚗 Logística — carros de apoio).
 * Modo demonstração: mostra a tela real, não grava. Abre pelo link "▶ Como fazer" no topo de cada painel
 * ou por /checklist?guia=operador|gestor|logistica (no app: /mobile?abrir=/checklist?guia=operador). */
window.GUIAS_CHECKLIST = {

  operador: {
    titulo: 'Check list do equipamento',
    pop: 'POP-CHK-01',
    quem: () => document.getElementById('screen-driver') && document.getElementById('screen-driver').classList.contains('active'),
    passos: [
      { titulo: 'Seu equipamento, conferido todo dia ✓',
        texto: '<b>Bem-vindo à demonstração do Check list digital.</b><br><br>Você vai ver como escolher o check list, identificar o equipamento e o horímetro, marcar cada item como Conforme ou Não Conforme, fotografar o problema e enviar. É este envio que avisa a manutenção e conta os seus pontos.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: '#driver-cl-cards', antes: _ckOperadorInicio, titulo: '1º — Escolha o check list',
        texto: 'Um card por tipo: <b>🚜 Máquinas</b>, <b>🚗 Carro de Apoio</b> e <b>🚛 Caminhão – Semanal</b>. Toque no que corresponde ao equipamento que você vai operar hoje. Se a sua função limita os tipos, só os seus aparecem aqui.' },
      { alvo: '#meta-equipamento', antes: () => _ckAbrirForm(0), titulo: 'Identificação — Equipamento',
        texto: 'Escolha o equipamento na lista, que vem do <b>cadastro da frota</b> (Admin › Equipamentos). Se você tem equipamento fixo, ele já vem marcado. Seu nome como responsável também já vem preenchido. Sem equipamento o sistema <b>não deixa avançar</b>.' },
      { alvo: '#meta-horimetro', titulo: 'Horímetro atual (KM nos caminhões e carros)',
        texto: 'Digite o número do painel. Essa leitura vai para o <b>cadastro da frota</b> e é ela que move as revisões preventivas. Se o valor for <b>menor que o último</b> registrado, ou saltar demais (mais de 500 h / 5.000 km), o sistema avisa e <b>trava</b> até você corrigir.' },
      { alvo: '#meta-tipo', titulo: 'Local, data e tipo',
        texto: 'Informe o <b>local</b> da operação (a data já vem de hoje) e o <b>tipo</b>: Preventivo (rotina do dia), Corretivo, Entrega ou Assumindo Operação. Nos caminhões e carros o campo é <b>Situação</b>: fixo, pegando ou entregando o veículo.' },
      { alvo: '#btn-next', titulo: 'Próximo — uma etapa por vez',
        texto: 'O check list é dividido em etapas (<b>1 / 6</b>, barra de progresso no topo). <b>Próximo</b> avança; <b>Anterior</b> volta sem perder o que você marcou. O sistema não deixa avançar com item sem resposta — ele lista os que faltam e rola até o primeiro.' },
      { alvo: () => document.querySelector('#ci-lubrificacao .ci-options') || document.querySelector('#form-content .ci-options'), antes: () => _ckAbrirForm(1), titulo: 'Conforme · Não Conforme · N/A',
        texto: 'Para cada item: <b>✓ Conforme</b> está em ordem; <b>✗ Não Conforme</b> tem problema; <b>N/A</b> não se aplica a este equipamento. Olhe de verdade antes de marcar — a gestão compara os itens conformes de cada colaborador.' },
      { alvo: '#ci-pneu_rodas', antes: () => _ckAbrirForm(2, 'pneu_rodas'), titulo: 'Marcou Não Conforme? Descreva',
        texto: 'Ao marcar <b>✗ Não Conforme</b> abre o campo <b>"Descreva o problema"</b>. Escreva o que viu, direto: "esteira esquerda com elo frouxo". Esse texto vai junto no <b>pedido de manutenção</b> que o sistema abre sozinho com o seu envio.' },
      { alvo: () => document.querySelector('#ci-pneu_rodas .ci-photo-btn') || document.querySelector('#ci-pneu_rodas'), titulo: '📷 Foto da NC',
        texto: 'O botão de foto aparece <b>só no item Não Conforme</b>. Tire a foto do problema na hora: é a evidência que a gestão e o mecânico veem no detalhe do envio. A foto é comprimida no celular, não pesa no seu plano.' },
      { alvo: '#obs-observacoes', antes: () => _ckAbrirForm(5), titulo: 'Observações finais',
        texto: 'Última etapa: anote o que não coube nos itens e, se já existe, o <b>número da OT</b>. Observação detalhada (mais de 20 caracteres) vale <b>pontos extras</b>.' },
      { alvo: '#btn-next', titulo: 'Enviar ✓',
        texto: 'Na última etapa o botão vira <b>Enviar ✓</b>. O envio é salvo <b>primeiro no celular</b> e depois no servidor — sem sinal, ele fica na fila e sincroniza sozinho quando a internet voltar (banner "aguardando sincronização"). Você vê a tela "Check List Enviado!".' },
      { alvo: '#driver-history', antes: _ckOperadorInicio, titulo: 'Meus últimos envios — e o que acontece depois',
        texto: 'Cada envio mostra <b>✓ Conforme</b> ou <b>⚠ n NC</b>. Com NC, o sistema abre na hora um <b>Pedido de manutenção</b> (PED-ano-número, urgência alta) na triagem da gestão. Quando a gestão abrir o seu envio aparece <b>👁 Visto</b>; se responder, <b>💬</b> — toque para ler.' },
      { titulo: 'Pronto! ✅',
        texto: 'Rotina de sempre: <b>escolher o check list → equipamento e horímetro → itens ✓/✗ (foto na NC) → observações → Enviar</b>. NC vira pedido de manutenção sozinho. O passo a passo está no <b>POP-CHK-01</b>. Repita este tour quando quiser pelo ▶ Como fazer.' },
    ],
  },

  gestor: {
    titulo: 'Gestão dos envios',
    pop: 'POP-CHK-02',
    quem: () => document.getElementById('screen-manager') && (document.getElementById('screen-manager').classList.contains('active') || document.getElementById('screen-detail').classList.contains('active')),
    passos: [
      { titulo: 'Os check lists da frota, num lugar só 📋',
        texto: '<b>Bem-vinda à demonstração da Gestão do check list.</b><br><br>Este painel aparece para quem é Master, tem perfil manager no check list ou o módulo <b>checklist_gestao</b> na tela de Permissões. Você vai ver os indicadores, onde tratar cada envio (visto e retorno ao operador), o ranking de pontos e os modelos de check list.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: '#screen-manager .mgr-tabs', antes: () => _ckMgrAba('overview'), titulo: 'As abas da gestão',
        texto: '<b>Visão Geral</b> (indicadores), <b>Ranking</b> (pontos), <b>Envios</b> (tratar cada check list), <b>Logística</b> (só com a permissão checklist_logistica) e <b>Check Lists</b> (modelos). Deslize para ver todas.' },
      { alvo: '#tab-overview .kpi-grid', titulo: 'Indicadores',
        texto: '<b>Total de envios</b>, <b>esta semana</b>, <b>pendentes de sincronização</b> (ficaram no celular, ainda não chegaram) e <b>não conformes</b>. Fonte: o servidor — todos os aparelhos veem o mesmo número.' },
      { alvo: '#compliance-list', titulo: 'Conformidade por colaborador',
        texto: 'Conta <b>itens conformes sobre itens verificados</b> (N/A não entra): um NC entre 20 itens ✓ não zera o colaborador. Verde ≥ 80%, laranja ≥ 60%, vermelho abaixo. É daqui que sai a conversa de orientação.' },
      { alvo: '#filter-status', antes: () => _ckMgrAba('submissions'), titulo: 'Envios — comece pelos "Não vistos"',
        texto: 'Filtre por <b>tipo</b>, <b>colaborador</b> e <b>situação</b>: <b>🆕 Não vistos</b> (ninguém da gestão abriu), ✓ Conforme, ⚠ Não Conforme, 📦 Equip. removido. Rotina da manhã: situação = <b>Não vistos</b> e trate um por um.' },
      { alvo: () => document.querySelector('#submissions-list .sub-card'), titulo: 'O card do envio',
        texto: 'Colaborador, data, equipamento e local. O selo diz onde está: <b>🆕 Não visto</b>, <b>👁 Visto</b> ou <b>💬 n</b> (retornos enviados, lido ou não). À direita, <b>n NC</b> em vermelho ou ✓. Toque para abrir o detalhe.' },
      { alvo: () => document.querySelector('#detail-content .detail-section'), antes: _ckAbrirDetalheNC, titulo: 'Detalhe — abrir já registra o "visto"',
        texto: 'Ao abrir, o sistema grava <b>👁 Visto por você</b> e o operador passa a ver "Visto pela gestão" no celular. Aqui estão a identificação, cada item com ✓/✗/N/A, a observação e a <b>foto da NC</b>. Com NC, aparece <b>🔧 Pedido PED-… aberto para a manutenção</b> — já está na triagem.' },
      { alvo: () => document.getElementById('ret-texto') || document.getElementById('ret-box'), titulo: 'Retorno ao operador',
        texto: 'Responda com um <b>atalho</b> (frases prontas, editáveis em ✏️ Atalhos) ou escreva. O operador recebe <b>💬</b> no "Meus últimos envios" e, quando abrir, você vê <b>✓✓ lido</b> com a hora. É o recibo de que a orientação chegou.' },
      { alvo: '#podium', antes: () => { _ckFecharDetalhe(); return _ckMgrAba('ranking'); }, titulo: 'Ranking de pontos',
        texto: 'Pontos somados no <b>servidor</b> por colaborador (100 tudo conforme, 60 com NC reportada, +20 observação, +10 no dia). Toque no nome para <b>ajustar pontos</b>: bônus ou penalidade com motivo obrigatório — fica no extrato 📜 de auditoria.' },
      { alvo: '#pontos-config-panel', titulo: 'Visibilidade de pontos e ciclos',
        texto: 'O interruptor decide se o colaborador <b>vê</b> os próprios pontos (desligado: "Pontuação em breve"); o período limita a contagem. Abaixo, <b>ciclos</b>: iniciar um ciclo com prêmio, acompanhar os dias restantes e fechar — o pódio vai para o histórico.' },
      { alvo: '#tab-checklists .tab-action-bar', antes: () => _ckMgrAba('checklists'), titulo: 'Modelos de check list',
        texto: 'Os <b>3 padrão</b> (Máquinas, Carro de Apoio, Caminhão) não se editam. <b>+ Novo Check List</b> abre o construtor: perguntas de conformidade, texto, escala, foto, seções e regras de pontos — o modelo fica salvo no servidor e aparece para os colaboradores.' },
      { titulo: 'Pronto! ✅',
        texto: 'Rotina: <b>Envios › Não vistos → abrir (visto) → retorno ao operador</b>. NC já virou <b>Pedido</b> na triagem da Manutenção. Semanal: <b>Visão Geral</b> e <b>Ranking</b>. O passo a passo está no <b>POP-CHK-02</b>. Repita quando quiser pelo ▶ Como fazer.' },
    ],
  },

  logistica: {
    titulo: 'Logística dos carros de apoio',
    pop: 'POP-CHK-02 · Logística',
    quem: () => !!(typeof currentUser !== 'undefined' && currentUser && currentUser.temLogistica) && !!(document.querySelector('#screen-manager.active, #screen-superior.active')),
    passos: [
      { titulo: 'Quem está com qual carro 🚗',
        texto: '<b>Bem-vinda à demonstração da Logística.</b><br><br>A aba 🚗 Logística aparece para quem tem a permissão <b>checklist_logistica</b>. Ela registra, a cada dia, em que obra está cada carro de apoio e com qual motorista. Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: () => _ckLog('#tab-logistics .kpi-grid', '#sup-tab-logistics .kpi-grid'), antes: _ckAbrirLogistica, titulo: 'Situação de hoje',
        texto: '<b>Em campo</b>, <b>disponíveis</b>, <b>motoristas alocados</b> e <b>total de registros</b> — calculados do último registro de cada carro.' },
      { alvo: () => _ckLog('#log-current-fleet', '#sup-current-fleet'), titulo: 'Um card por carro',
        texto: 'Carro, status (🟠 Em campo, 🟢 Disponível, ⚫ Ocioso), motorista, destino e quem registrou. Vale sempre o <b>último registro</b> do carro. Toque no card para ver o registro completo.' },
      { alvo: '#log-car-rows', antes: _ckAbrirLogModal, titulo: '+ Novo Registro — um bloco por carro',
        texto: '<b>Responsável</b> e <b>data/hora</b> já vêm preenchidos. Para cada carro: <b>veículo</b> (lista do cadastro de equipamentos + motos), <b>status</b>, <b>destino/obra</b>, <b>motorista</b> (escolha na lista ou digite) e observação. <b>+ Adicionar carro</b> inclui o próximo.' },
      { alvo: () => document.querySelector('#log-modal .btn-primary'), titulo: 'Salvar registro',
        texto: 'Vai direto ao <b>servidor</b> — todo mundo com a aba vê a mesma situação. Sem internet, fica na fila e é reenviado sozinho. Precisa de pelo menos um carro.' },
      { alvo: () => _ckLog('#log-history-list', '#sup-log-history'), antes: () => { try { closeModal('log-modal'); } catch (e) { } }, titulo: 'Histórico e relatório',
        texto: 'Todos os registros, com filtro por carro, responsável e data (painel da gestão). ✎ edita e ✕ exclui. <b>🖨 Imprimir</b> gera o relatório de logística com a situação atual e o histórico.' },
      { titulo: 'Pronto! ✅',
        texto: 'Rotina: de manhã, <b>+ Novo Registro</b> com todos os carros que saíram; mudou algo no dia, novo registro. O card mostra sempre a última posição. Repita este tour pelo ▶ Como fazer.' },
    ],
  },
};

// ── helpers dos roteiros (só navegação de tela; nada grava) ───────────────────────────
function _ckEspera(ms) { return new Promise(r => setTimeout(r, ms || 500)); }
function _ckOperadorInicio() {
  try { if (!document.getElementById('screen-driver').classList.contains('active')) showDriver(); } catch (e) { }
}
// Abre o formulário de Máquinas na etapa pedida, com valores de exemplo (só em memória; aoSair limpa).
async function _ckAbrirForm(etapa, ncItem) {
  try {
    if (currentCLId !== 'maquinas' || !document.getElementById('screen-form').classList.contains('active')) startChecklist('maquinas');
    if (etapa === 0) {
      currentStep = 0; renderFormStep();
      await _ckEspera(200);
      const eq = document.getElementById('meta-equipamento'); if (eq && !eq.value && eq.options.length > 1) eq.selectedIndex = 1;
      const lo = document.getElementById('meta-local'); if (lo && !lo.value) lo.value = 'Florestal';
      const ho = document.getElementById('meta-horimetro'); if (ho && !ho.value) ho.value = '4321';
      const ti = document.getElementById('meta-tipo'); if (ti && !ti.value) ti.value = 'Preventivo';
      return;
    }
    currentStep = etapa; renderFormStep();
    if (ncItem) { setAnswer(ncItem, 'NC'); await _ckEspera(150); const o = document.getElementById('obs-' + ncItem); if (o && !o.value) o.value = 'Esteira esquerda com elo frouxo'; }
    await _ckEspera(300);
  } catch (e) { console.warn('guia checklist:', e); }
}
function _ckMgrAba(aba) {
  const b = document.querySelector('#screen-manager .tab[onclick*="\'' + aba + '\'"]');
  if (b) mgrTab(aba, b);
  return _ckEspera(500);
}
// Detalhe de um envio com NC (ou o primeiro). showSubmissionDetail registra o "visto" no servidor —
// durante o tour esse POST é pulado (apiFetch embrulhado em guiaAbrir), para o tour não gravar nada.
async function _ckAbrirDetalheNC() {
  try {
    const subs = DB.submissions().filter(s => s.synced !== false);
    const alvo = subs.find(s => countNC(s) > 0) || subs[0];
    if (!alvo) return;
    await showSubmissionDetail(alvo.id);
    await _ckEspera(700);
  } catch (e) { console.warn('guia checklist detalhe:', e); }
}
function _ckFecharDetalhe() { try { if (document.getElementById('screen-detail').classList.contains('active')) closeDetail(); } catch (e) { } }
function _ckLog(selMgr, selSup) {
  const m = document.getElementById('screen-manager').classList.contains('active');
  return document.querySelector(m ? selMgr : selSup) || document.querySelector(m ? selSup : selMgr);
}
async function _ckAbrirLogistica() {
  try {
    if (document.getElementById('screen-manager').classList.contains('active')) { await _ckMgrAba('logistics'); }
    else { const b = document.querySelector('#screen-superior .tab[onclick*="logistics"]'); if (b) supTab('logistics', b); await _ckEspera(500); }
  } catch (e) { }
}
async function _ckAbrirLogModal() {
  try {
    if (document.getElementById('log-modal').classList.contains('hidden')) openLogModal();
    await _ckEspera(400);
    const n = document.querySelector('#log-car-rows .log-car-row');
    if (n) { const c = n.querySelector('select[id$="-car"]'); if (c && !c.value && c.options.length > 1) c.selectedIndex = 1; const d = n.querySelector('input[id$="-dest"]'); if (d && !d.value) d.value = 'FLORESTAL'; }
  } catch (e) { }
}

// ── abertura: link ▶ Como fazer, menu e deep link ?guia=<nome> ────────────────────────
window.guiaAbrir = function (nome) {
  const g = window.GUIAS_CHECKLIST[nome]; if (!g || !window.GarraGuia) return;
  if (g.quem && !g.quem()) { console.warn('guia checklist: roteiro "' + nome + '" não se aplica a este painel'); return; }
  const apiOrig = window.apiFetch;
  if (nome === 'gestor') window.apiFetch = function (u, o) { return (/\/visto$/.test(String(u)) ? Promise.resolve({ ok: true, demo: true }) : apiOrig(u, o)); };
  g.aoSair = () => {
    try { window.apiFetch = apiOrig; } catch (e) { }
    try {
      if (nome === 'operador') { currentCLId = null; currentStep = 0; formAnswers = {}; formMeta = {}; showDriver(); }
      if (nome === 'gestor') { _ckFecharDetalhe(); _ckMgrAba('overview'); }
      if (nome === 'logistica') { closeModal('log-modal'); }
    } catch (e) { }
  };
  GarraGuia.iniciar(g);
};
window.guiaMenu = function (ev) {
  ev && ev.stopPropagation();
  let m = document.getElementById('guia-menu');
  if (m) { m.remove(); return; }
  const lista = Object.entries(window.GUIAS_CHECKLIST).filter(([k, g]) => !g.quem || g.quem());
  if (!lista.length) { const l = document.getElementById('guia-link-sup'); if (l) l.style.display = 'none'; return; }   // superior sem logística: nada a mostrar
  if (lista.length === 1) { guiaAbrir(lista[0][0]); return; }
  m = document.createElement('div'); m.id = 'guia-menu';
  m.style.cssText = 'position:fixed;top:48px;right:12px;z-index:9999;background:#fff;color:#0F172A;border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:6px;min-width:260px;font-size:13px';
  m.innerHTML = '<div style="padding:6px 10px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
    + lista.map(([k, g]) => '<div style="padding:8px 10px;cursor:pointer;border-radius:6px" onmouseover="this.style.background=\'#F1F5F9\'" onmouseout="this.style.background=\'\'" onclick="document.getElementById(\'guia-menu\').remove();guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8;font-size:11px">· ' + (g.passos.length - 2) + ' passos · ' + (g.pop || '') + '</span></div>').join('');
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
};
// ?guia=<nome> — espera o painel aparecer (SSO/login concluído) e começa
(function () {
  const q = window._GUIA_Q; if (!q) return;
  let n = 0; const t = setInterval(() => {
    n++;
    const pronto = document.querySelector('#screen-driver.active, #screen-manager.active, #screen-superior.active');
    // currentUser é `let` global do app.js (não existe window.currentUser)
    if (pronto && typeof currentUser !== 'undefined' && currentUser) { clearInterval(t); setTimeout(() => guiaAbrir(q), 900); } else if (n > 60) clearInterval(t);
  }, 500);
})();
