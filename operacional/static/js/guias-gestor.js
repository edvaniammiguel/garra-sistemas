/* (05/11/2026) Tour guiado da tela 🔧 Manutenção no celular (gestor) — GarraGuia. Texto segue o POP-MNT-02.
 * Modo demonstração: mostra a tela real (pedidos → OTs → frota → conferir), não grava nada.
 * Abre pelo link "▶ Como fazer" no topo da tela ou por /manutencao/mobile?guia=gestor
 * (no app: /mobile?abrir=/manutencao/mobile?guia=gestor). */
// fallback dos alvos: se a folha (sheet) não estiver aberta (sem pedido/OT no momento), aponta para a lista da aba
const _gsSheet = (sel) => (document.getElementById('sheet-bg') && document.getElementById('sheet-bg').classList.contains('on')) ? document.querySelector(sel) : null;
window.GUIAS_GESTOR = {
  gestor: {
    titulo: 'Rotina do gestor pelo celular',
    pop: 'POP-MNT-02',
    passos: [
      { titulo: 'Manutenção na palma da mão 🔧',
        texto: '<b>Bem-vindo(a) à demonstração da Manutenção no celular.</b><br><br>Esta tela é do <b>gestor</b> (o mecânico usa o app Pedir OT). Você vai ver a rotina do dia: triar os <b>pedidos</b> que chegam, acompanhar e mudar o status das <b>OTs</b>, consultar a ficha rápida da <b>frota</b> e <b>conferir</b> as notas de abastecimento.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },

      { alvo: '#abas', antes: async () => { try { if (typeof sheetFechar === 'function') sheetFechar(); } catch (e) { } },
        titulo: 'As quatro abas',
        texto: '<b>📨 Pedidos</b>, <b>🔧 OTs</b>, <b>🚜 Frota</b> e <b>⛽ Conferir</b>. O número vermelho é o que espera você: pedidos sem triagem, OTs vivas (aberta, em curso ou aguardando peça) e notas de abastecimento não conferidas. Comece pela aba que estiver com número.' },

      { alvo: () => document.querySelector('#ped-lista .card') || document.querySelector('#tela-ped .filtros'),
        antes: async () => { await aba('ped'); await new Promise(r => setTimeout(r, 500)); },
        titulo: 'Pedidos aguardando triagem',
        texto: 'Cada cartão é um pedido: número, <b>urgência</b> (🔴 urgente = máquina parada, trate primeiro), equipamento, o que foi relatado, por onde veio (<b>📱 app do mecânico</b>, <b>✓ checklist</b> ou <b>🖥 desktop</b>), quem pediu e quando. O filtro <b>Tratados</b> mostra os que já viraram OT ou foram recusados. Toque no cartão para abrir.' },

      { alvo: () => _gsSheet('#pc-tipo') || _gsSheet('#sheet h2') || document.getElementById('ped-lista'),
        antes: async () => { if (typeof PEDIDOS !== 'undefined' && PEDIDOS.length) { await pedAbrir(PEDIDOS[0].id); await new Promise(r => setTimeout(r, 500)); } },
        titulo: 'Virar OT — conferir antes',
        texto: 'Antes de converter, confira o <b>Equipamento</b> (pedido sem equipamento exige escolher um), o <b>Tipo de trabalho</b> (vem <b>C1 — Reparação de avaria</b>, porque pedido é defeito), a <b>Prioridade</b> (vem da urgência do pedido) e o <b>Prog. Início</b> (vem hoje — mude se vai programar para outro dia). Pedido do checklist mostra aqui os itens não conformes com as fotos do operador.' },

      { alvo: () => _gsSheet('#pc-ok') || _gsSheet('#sheet h2') || document.getElementById('ped-lista'),
        titulo: '✔ Virar OT',
        texto: 'Toque em <b>✔ Virar OT</b>: nasce uma OT com número próprio, status <b>Aberta</b>, tipo C1 e Prog. Início na data escolhida, já ligada ao pedido. O pedido sai dos Abertos e aparece em <b>Tratados</b> com "→ OT-…". Quem pediu vê no app que virou OT. Início real só no Emitir; Fim só no Encerrar.' },

      { alvo: () => _gsSheet('#sheet .acoes .btn.no') || _gsSheet('#pc-ok') || _gsSheet('#sheet h2') || document.getElementById('ped-lista'),
        titulo: '✕ Recusar',
        texto: 'Não é manutenção, está duplicado ou já foi resolvido? <b>✕ Recusar</b> pede o <b>motivo</b> (obrigatório) e ele volta para quem pediu. O pedido fica em Tratados como "Recusado", com o motivo guardado.' },

      { alvo: '#tela-ots .filtros',
        antes: async () => { sheetFechar(); await aba('ots'); await new Promise(r => setTimeout(r, 600)); },
        titulo: 'Aba OTs — filtros e busca',
        texto: '<b>Em aberto</b> junta tudo que está vivo (aberta, em curso, aguardando peça e programada). Os outros filtros isolam um status; <b>Encerradas</b> é o histórico. A busca 🔍 acha por número da OT, equipamento, descrição ou responsável.' },

      { alvo: () => document.querySelector('#ot-lista .card .sem') || document.querySelector('#ot-lista .card'),
        titulo: 'O círculo da esquerda é o estado',
        texto: '<b>▶ azul</b> = aberta ou em curso · <b>⏸ marrom</b> = aguardando peça/fornecedor · <b>✓ cinza</b> = encerrada. Nas <b>programadas</b> (preventivas) a cor é o prazo: <b>🔴 vencida</b>, <b>🟡 vence em até 7 dias ou 50 h/km</b>, <b>🟢 em dia</b>. O texto "vence em…", "vencida há…" ou "limite:…" aparece no rodapé do cartão, junto com Prog., Início, responsável e quantos registros o mecânico já fez.' },

      { alvo: () => _gsSheet('#sheet .kv') || _gsSheet('#sheet h2') || document.getElementById('ot-lista'),
        antes: async () => {
          const pref = ['em_andamento', 'aguardando_peca', 'aberta', 'programada'];
          let l = (typeof OTS !== 'undefined' ? OTS : []).filter(o => pref.includes(o.status)).sort((a, b) => pref.indexOf(a.status) - pref.indexOf(b.status));
          if (l.length) { await otAbrir(l[0].id); await new Promise(r => setTimeout(r, 700)); }
        },
        titulo: 'Ficha da OT',
        texto: 'Toque no cartão e a OT abre inteira: <b>Origem</b> (tipo de trabalho, prioridade, interventor interno ou fornecedor, responsável, solicitante e o pedido que gerou, contador na abertura, Prog. Início, Início real, Data limite), <b>Tarefas</b>, o <b>Realizado</b> que o mecânico registrou pelo app (relato, horas, horímetro, fotos), <b>Peças aplicadas</b> com o total, horas de mão de obra, ordens de compra, pausas e o histórico de status.' },

      { alvo: () => _gsSheet('#ot-acoes') || _gsSheet('#sheet .kv') || document.getElementById('ot-lista'),
        titulo: 'Mudar o status',
        texto: 'Os botões do fim mudam conforme o status. <b>▶ Emitir</b> (OT aberta/programada) passa para <b>Em curso</b>, grava o Início real e avisa o responsável no app. Em curso: <b>⏸ Aguardando</b> e <b>✔ Encerrar</b>. Aguardando peça: <b>▶ Retomar</b> fecha a pausa e volta para Em curso. Cancelar OT só pelo computador.' },

      { alvo: () => (_gsSheet('#ot-form') && document.getElementById('ot-form').firstElementChild) ? document.getElementById('ot-form') : (_gsSheet('#ot-acoes') || _gsSheet('#sheet .kv') || document.getElementById('ot-lista')),
        antes: async () => { try { if (typeof OT_ATUAL !== 'undefined' && OT_ATUAL && OT_ATUAL.status === 'em_andamento') { otPausar(); await new Promise(r => setTimeout(r, 400)); } } catch (e) { } },
        titulo: '⏸ Aguardando — sempre com motivo',
        texto: 'Faltou peça ou o fornecedor não veio? Escolha o <b>motivo</b> da lista (Motivos de OT Pendente) e descreva o que falta, OC ou fornecedor. Confirmar abre uma <b>pausa</b> com hora de início; ela fecha sozinha no Retomar ou no Encerrar e fica no histórico da OT. Assim a gestão sabe quanto tempo a máquina esperou e por quê.' },

      { alvo: () => (_gsSheet('#ot-form') && document.getElementById('ot-form').firstElementChild) ? document.getElementById('ot-form') : (_gsSheet('#ot-acoes') || _gsSheet('#sheet .kv') || document.getElementById('ot-lista')),
        antes: async () => { try { if (typeof OT_ATUAL !== 'undefined' && OT_ATUAL && ['em_andamento', 'aguardando_peca'].includes(OT_ATUAL.status)) { otEncerrarForm(); await new Promise(r => setTimeout(r, 400)); } } catch (e) { } },
        titulo: '✔ Encerrar OT',
        texto: 'Informe o <b>contador da máquina no fim</b> (vem sugerido do último registro do mecânico) e uma observação. Encerrar grava <b>Fim</b> e <b>Contador no fim</b>, calcula as horas paradas (do Emitir até o Fim) e, se a OT é preventiva, reinicia o ciclo do plano. O fechamento de custo da OT (mão de obra, peças e outros) e cancelamentos ficam para a gestão completa no computador.' },

      { alvo: () => _gsSheet('#sheet .kv') || document.getElementById('eq-lista'),
        antes: async () => { try { const f = document.getElementById('ot-form'); if (f) f.innerHTML = ''; } catch (e) { } sheetFechar(); await aba('eq'); await new Promise(r => setTimeout(r, 600)); if (typeof EQS !== 'undefined' && EQS.length) { await eqAbrir(EQS[0].id); await new Promise(r => setTimeout(r, 700)); } },
        titulo: 'Frota — ficha rápida',
        texto: 'Na aba <b>🚜 Frota</b> cada equipamento mostra se tem preventiva <b>vencida</b> ou <b>a vencer</b>. Tocando, abre a ficha rápida: <b>contador atual</b>, média de uso por dia (FMD), última leitura e dias com registro nos últimos 90 dias; embaixo, os planos de <b>preventiva</b> com o que falta ou passou, e as <b>OTs</b> do equipamento (toque para abrir).' },

      { alvo: () => document.querySelector('#ab-lista .card') || document.getElementById('tela-ab'),
        antes: async () => { sheetFechar(); await aba('ab'); await new Promise(r => setTimeout(r, 700)); },
        titulo: '⛽ Conferir abastecimentos',
        texto: 'Aqui ficam as <b>notas de abastecimento ainda não conferidas</b> que os operadores registraram pelo app, agrupadas por equipamento (quantas notas, litros, período). Tocando, você vê cada nota com a <b>foto do cupom e do painel</b>, leitura, posto e cupom, e os alertas <b>leitura ÷</b> ou <b>consumo</b>. Marque <b>✔ Conferir</b>, <b>⚠ Divergente</b> ou <b>✔ Conferir todas</b>. Correção de valores é pelo computador.' },

      { titulo: 'Pronto! ✅',
        texto: 'Rotina pelo celular: <b>Pedidos → Virar OT ou Recusar com motivo</b> → <b>OTs: Emitir, Aguardando com motivo, Retomar, Encerrar com contador</b> → olhar a <b>Frota</b> → <b>Conferir</b> as notas. O passo a passo é o <b>POP-MNT-02</b>. A gestão completa (responsável, tarefas, custos, cancelamento, parametrização) continua no computador. Repita este tour quando quiser pelo ▶ Como fazer.' },
    ],
  },
};

window.guiaAbrir = function (nome) {
  const g = window.GUIAS_GESTOR[nome]; if (!g || !window.GarraGuia) return;
  const abaInicial = (typeof ABA !== 'undefined') ? ABA : 'ped';
  g.aoSair = () => {
    try { const f = document.getElementById('ot-form'); if (f) f.innerHTML = ''; } catch (e) { }
    try { sheetFechar(); } catch (e) { }
    try { if (typeof ABA !== 'undefined' && ABA !== abaInicial) aba(abaInicial); } catch (e) { }
  };
  GarraGuia.iniciar(g);
};
// ?guia=gestor — espera o login/SSO concluir (abas visíveis e primeira lista carregada) e começa
(function () {
  const q = window._GUIA_Q; if (!q) return;
  let n = 0; const t = setInterval(() => {
    const abas = document.getElementById('abas'); n++;
    const pronto = abas && abas.style.display !== 'none' && !document.querySelector('.tela.on .spin');
    if (pronto) { clearInterval(t); setTimeout(() => guiaAbrir(q), 600); } else if (n > 60) clearInterval(t);
  }, 500);
})();
