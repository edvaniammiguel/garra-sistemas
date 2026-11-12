/* (05/11/2026) Tour guiado da tela 🛒 Compras (ordens de compra) — GarraGuia. Mesma página no desktop e no
 * app do celular (aba 🛒 Compras). Dois roteiros: `solicitar` (criar uma OC e pedir aprovação) e `aprovar`
 * (fila de aprovação, detalhe, recebimento, impressão, relatório, alçadas — só para quem aprova).
 * Modo demonstração: mostra a tela real, não grava. O formulário de exemplo é limpo ao sair.
 * Abre pelo botão ▶ Como fazer (barra superior / link no celular) ou por /compras?guia=solicitar | ?guia=aprovar
 * (no app: /mobile?abrir=/compras?guia=aprovar). */
(function () {
  const esperar = ms => new Promise(r => setTimeout(r, ms));
  const $ = s => document.querySelector(s);
  // espera um seletor aparecer (tela montando / fetch da OC)
  async function ate(sel, max) { for (let n = 0; n < (max || 30); n++) { const e = $(sel); if (e) return e; await esperar(100); } return $(sel); }
  let _ocs = null, _livre = false, _abaInicial = 'lista';
  async function ocs() {
    if (_ocs) return _ocs;
    try { _ocs = await api('/compras/api/ocs'); } catch (e) { _ocs = []; }
    return _ocs;
  }
  // OC preferida por status (na ordem dada); prefere a que tem OT vinculada
  async function acharOC(status) {
    const l = await ocs();
    for (const st of status) {
      const c = l.filter(o => o.status === st);
      if (c.length) return c.find(o => o.ot_numero) || c[0];
    }
    return null;
  }
  function fecharModal() { try { document.getElementById('modal-det').classList.remove('on'); document.getElementById('wa-overlay').classList.remove('on'); } catch (e) { } }
  async function abrirOC(status) {
    const o = await acharOC(status);
    if (!o) return null;
    abrirDetalhe(o.id); await ate('#modal-det-body .acoes', 40); await esperar(300);
    // agrupa as linhas de cabeçalho (setor, fornecedor, solicitante, vínculo) para o destaque do passo
    try { const m = [...document.querySelectorAll('#modal-det-body > .oc-meta')]; if (m.length && !$('#gg-meta')) { const w = document.createElement('div'); w.id = 'gg-meta'; m[0].parentNode.insertBefore(w, m[0]); m.forEach(x => w.appendChild(x)); } } catch (e) { }
    return o;
  }
  function abaAtual() { const b = document.querySelector('.tabs button.on'); return b ? b.id.replace('tab-', '') : 'lista'; }
  const noModal = (sel, alt) => () => $('#modal-det.on ' + sel) || $(alt || '#modal-det-body');

  window.GUIAS_COMPRAS = {

    solicitar: {
      titulo: 'Solicitar uma compra (OC)',
      pop: 'Compras · fluxo da OC',
      passos: [
        { titulo: 'Precisa comprar? Faça a OC 🛒',
          texto: '<b>Bem-vindo(a) à demonstração de Solicitar uma compra.</b><br><br>Você vai ver como montar uma <b>Ordem de Compra</b>: setor, fornecedor, itens, orçamento anexado e o pedido de aprovação — e como acompanhar depois na lista. Nenhuma compra acontece sem OC aprovada.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: '#tab-nova', titulo: 'Aba ＋ Nova OC',
          antes: async () => { fecharModal(); _abaInicial = abaAtual(); _livre = !_editandoOC && !lerItens().length; if (_livre) limparNova(); irTab('nova'); await esperar(300); },
          texto: 'Tudo começa na aba <b>＋ Nova OC</b>. Aqui você monta a <b>cotação</b>: setor, fornecedor, itens e orçamento. Enquanto não for aprovada, é só uma cotação — não autoriza compra.' },
        { alvo: '#n-setores', titulo: 'Setor = centro de custo',
          antes: async () => { if (_livre) selSetor('MANUT'); await esperar(200); },
          texto: 'O <b>setor</b> é obrigatório: é ele que diz de onde sai o dinheiro (Manutenção, Operacional, Administrativo…) e é por ele que o relatório soma o gasto do mês. Escolhendo <b>Manutenção</b> aparecem dois campos a mais, logo abaixo.' },
        { alvo: () => ($('#n-ot-wrap').style.display !== 'none' ? $('#n-ot-wrap') : $('#n-setores')), titulo: 'Equipamento e vínculo com a OT',
          texto: 'Só em Manutenção: informe o <b>equipamento</b> (a sigla sai na lista e no relatório) e, se a compra é de uma <b>Ordem de Trabalho</b>, vincule a OT. Vinculou? Quando a mercadoria for recebida, o valor <b>soma no custo da OT</b> sozinho. OC gerada lá na OT (botão 🛒 Gerar OC da manutenção) já chega aqui com esse vínculo e com a prioridade da OT.' },
        { alvo: () => $('#forn-combo').closest('.fld'), titulo: 'Fornecedor',
          antes: async () => { if (_livre && FORNECEDORES.length) fornEscolher(FORNECEDORES[0].id); await esperar(150); },
          texto: 'Fornecedor é <b>obrigatório</b>. <b>📋 Cadastrado</b>: digite e escolha da lista — traz o WhatsApp e o histórico de preço. <b>✎ Avulso</b>: loja nova ou site — nome e, se tiver, o WhatsApp. Digitou um nome que não está no cadastro? O sistema salva como avulso: o nome nunca se perde.' },
        { alvo: () => $('#n-prio').closest('.fld').parentElement, titulo: 'Pagamento e prioridade',
          texto: 'A <b>condição de pagamento</b> vem da lista que a gestão cadastra em ⚙️ Alçadas. <b>🔥 Urgente</b> coloca a OC no <b>topo da fila</b> de quem aprova e sai marcada em vermelho — use só quando a máquina está parada.' },
        { alvo: '#n-itens', titulo: 'Itens da compra',
          antes: async () => {
            if (!_livre) return;
            const r = document.querySelector('#n-itens .item-row'); if (!r) return;
            r.querySelector('.i-desc').value = 'Filtro de óleo hidráulico'; r.querySelector('.i-qtd').value = 2; r.querySelector('.i-vlr').value = '185.50'; calcTotal();
            try { dicaPreco(r.querySelector('.i-desc'), r.id); } catch (e) { }
            await esperar(700);
          },
          texto: 'Um item por linha: <b>descrição, quantidade, unidade e valor unitário</b>. Ao digitar a descrição o sistema mostra o <b>último preço pago</b> e com quem — compare antes de fechar. <b>＋ Adicionar item</b> cria mais linhas; ✕ remove. Quantidade tem de ser maior que zero.' },
        // (11/11/2026) vínculo do item com o catálogo de peças da Manutenção
        { alvo: () => { const r = document.querySelector('#n-itens .item-row'); if (!r) return $('#n-itens'); const sg = document.getElementById(r.id + '-sug'); return (sg && sg.children.length) ? sg : (document.getElementById(r.id + '-peca') || $('#n-itens')); }, titulo: '🔗 Peça do catálogo → entra no estoque',
          antes: async () => { if (!_livre) return; const r = document.querySelector('#n-itens .item-row'); if (!r) return; try { const inp = r.querySelector('.i-desc'); pecaBuscar({ value: (inp.value || 'filtro').split(' ')[0] }, r.id); } catch (e) { } /* busca só pela 1ª palavra: mostra a lista do catálogo */ await esperar(1000); },
          texto: 'Setor <b>Manutenção</b>: ao digitar código ou descrição aparecem as <b>peças do catálogo</b> (com unidade, preço e fornecedor de referência). Escolheu uma? O item fica <b>vinculado</b> e, quando a OC for recebida e conferida em <b>Manutenção › Materiais › Receber</b>, a quantidade <b>entra no estoque</b> e o fornecedor vira a referência da peça. Sem vínculo = item só do Compras (não mexe no estoque). Peça que não existe: cadastre antes em Materiais › ＋ Nova peça.' },
        { alvo: '#tela-nova .tot-geral', titulo: 'Total da OC',
          antes: async () => { document.querySelectorAll('#n-itens .peca-sug').forEach(e => { e.innerHTML = ''; }); },
          texto: 'O total se calcula sozinho. É <b>esse valor</b> que o sistema compara com a <b>alçada</b> de quem solicita: cabe na alçada → aprova na hora; acima → vai para a fila de aprovação.' },
        { alvo: () => $('#n-anexos').closest('.fld'), titulo: 'Orçamento anexado',
          texto: 'Anexe a <b>cotação do fornecedor</b> (foto ou PDF, até 10 MB). Vira evidência com data, e quem aprova vê o orçamento real antes de decidir. Dá para anexar depois, pelo detalhe da OC. Logo abaixo, a <b>Observação</b> (entrega, referência) sai impressa na OC.' },
        { alvo: '#nova-acoes', titulo: 'Rascunho ou solicitar aprovação',
          antes: async () => { try { $('#nova-acoes').scrollIntoView({ block: 'center' }); } catch (e) { } },
          texto: 'Dois caminhos. <b>💾 Salvar cotação</b> guarda como <b>Rascunho</b>: não autoriza compra, você edita depois. <b>Solicitar aprovação →</b> manda para a fila: se o valor cabe na sua alçada, a OC é <b>aprovada em 1 passo</b>; senão fica <b>Solicitada</b> e os aprovadores recebem o aviso no sino 🔔.' },
        { alvo: '#tela-lista .filtros', titulo: 'Acompanhar na lista',
          antes: async () => { irTab('lista'); await esperar(600); },
          texto: 'Na aba <b>📋 OCs</b> estão as suas OCs — quem aprova ou tem <b>Visão geral</b> (financeiro, recebedor) vê as de todos. Filtre por setor, <b>status</b> e mês. O ciclo: Rascunho → Solicitada → <b>Aprovada</b> → Enviada → Recebida (ou Rejeitada / Cancelada).' },
        { alvo: '#resumo-cards', titulo: 'Resumo',
          texto: 'Os cartões mostram o que importa agora: <b>aguardando aprovação</b>, <b>aprovadas para enviar</b> ao fornecedor, <b>aguardando entrega</b> e o <b>comprado no mês</b>.' },
        { alvo: noModal('.mh', '#modal-det-body'), posicao: 'esquerda', titulo: 'Detalhe da OC',
          antes: async () => { await abrirOC(['solicitada', 'aprovada', 'enviada', 'recebida_parcial', 'recebida', 'rascunho']); },
          texto: 'Toque num cartão e abre o detalhe: fornecedor e condição, <b>solicitante</b>, quem aprovou e quando, o <b>vínculo com a OT e o equipamento</b>, os itens, os anexos e as ações. Em Rascunho ou Solicitada você ainda pode <b>✏️ Editar</b> ou <b>🗑 Excluir</b>; depois de aprovada, só a gestão cancela.' },
        { alvo: noModal('.trilha', '#modal-det-body'), titulo: 'Trilha',
          antes: async () => { const t = $('#modal-det.on .trilha'); if (t) t.scrollIntoView({ block: 'center' }); await esperar(200); },
          texto: 'A <b>trilha</b> registra cada passo com <b>nome e hora</b> — é a assinatura digital da OC. Foi <b>rejeitada</b>? O motivo aparece em vermelho acima das ações, e você pode refazer a OC corrigida.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina: <b>＋ Nova OC → setor → fornecedor → itens (peça do catálogo quando for estoque) → anexar orçamento → Solicitar aprovação</b>. Depois acompanhe o status na aba 📋 OCs; aprovada, ela vai ao fornecedor por WhatsApp ou PDF. OC que nasceu na Manutenção (mínimo, OT ou 🛒 Comprar) chega aqui em <b>Rascunho</b>, setor Manutenção, já com o fornecedor de referência — ou "a definir" para você cotar. Repita este tour quando quiser pelo ▶ Como fazer.' },
      ],
    },

    aprovar: {
      titulo: 'Aprovar, receber e acompanhar OCs',
      pop: 'Compras · aprovação e recebimento',
      passos: [
        { titulo: 'Tem OC esperando sua decisão ✅',
          texto: '<b>Bem-vindo(a) à demonstração de Aprovar e acompanhar compras.</b><br><br>Você vai ver a <b>fila de aprovação</b>, o detalhe da OC com orçamento e trilha, aprovar ou rejeitar com motivo, registrar o <b>recebimento</b> (e o que muda na OT), imprimir a OC e tirar o relatório. A aba ✅ Aprovar só aparece para quem tem a permissão <b>Compras — Aprovar</b> e <b>alçada cadastrada</b> (admin aprova sem limite).<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: '#tab-aprovar', titulo: 'Aba ✅ Aprovar',
          antes: async () => { fecharModal(); _abaInicial = abaAtual(); irTab('aprovar'); await esperar(800); },
          texto: 'O número entre parênteses (e o selo vermelho no topo) é quantas OCs <b>esperam por você</b>. Só entram OCs <b>Solicitadas</b> com valor dentro da sua <b>alçada por compra</b>; as <b>urgentes</b> vêm primeiro. Quem tem permissão de aprovar mas ainda não tem alçada não vê esta aba.' },
        { alvo: () => $('#fila-aprovar .oc-card') || $('#fila-aprovar'), titulo: 'A fila',
          texto: 'Cada cartão: número da OC, selo <b>URGENTE</b>, setor, <b>quem pediu</b>, fornecedor, <b>OT vinculada</b> e o valor. Dá para decidir aqui mesmo, mas o certo é <b>tocar no cartão</b> e conferir o detalhe antes.' },
        { alvo: () => $('#fila-aprovar .oc-card .acoes') || $('#fila-aprovar'), titulo: '✓ Aprovar · ✕ Rejeitar',
          texto: '<b>✓ Aprovar</b> grava seu nome e a hora na trilha — a <b>assinatura digital</b> — e a OC passa a <b>Aprovada</b>, pronta para ir ao fornecedor. <b>✕ Rejeitar</b> exige o <b>motivo</b>, que fica registrado e aparece para quem pediu. Valor acima da sua alçada ou do seu <b>teto mensal</b>? O sistema barra e explica — a OC segue para a gestão.' },
        { alvo: noModal('#gg-meta', '#modal-det-body'), posicao: 'esquerda', titulo: 'Detalhe: quem, de quem, para quê',
          antes: async () => { await abrirOC(['solicitada', 'aprovada', 'enviada', 'recebida_parcial', 'recebida']); },
          texto: 'No detalhe: setor, <b>fornecedor</b> e condição de pagamento, <b>solicitante</b>, e o <b>vínculo com a OT e o equipamento</b> quando a compra é da manutenção. Depois vêm os itens com quantidade, unidade, valor unitário e o <b>total</b> — é ele que conta na alçada.' },
        { alvo: noModal('#det-anexos', '#modal-det-body'), titulo: '📎 Orçamento anexado',
          antes: async () => { const t = $('#modal-det.on #det-anexos'); if (t) t.scrollIntoView({ block: 'center' }); await esperar(200); },
          texto: 'Aqui está a <b>cotação real</b> do fornecedor (PDF ou foto), com quem anexou e quando. Antes de aprovar, abra e compare com os itens. Sem anexo? Peça o orçamento ou anexe você pelo botão abaixo — fica na trilha.' },
        { alvo: noModal('.acoes', '#modal-det-body'), titulo: 'Ações conforme o status',
          antes: async () => { const t = $('#modal-det.on .acoes'); if (t) t.scrollIntoView({ block: 'center' }); await esperar(200); },
          texto: 'As ações mudam com o status. <b>Solicitada</b>: ✕ Rejeitar / ✓ Aprovar. <b>Aprovada</b>: 📲 Enviar por WhatsApp (já marca Enviada), 🖨 Imprimir/PDF, ✔ Marcar enviada. <b>Enviada</b>: 📦 Registrar recebimento. <b>Cancelar OC</b> é só da gestão e nunca de OC já recebida.' },
        { alvo: noModal('.trilha', '#modal-det-body'), titulo: 'Trilha',
          antes: async () => { const t = $('#modal-det.on .trilha'); if (t) t.scrollIntoView({ block: 'center' }); await esperar(200); },
          texto: 'Cada mudança de status com <b>nome e hora</b>: criada, solicitada, aprovada em 1 passo (dentro da alçada), enviada, recebida, complemento, devolução. É o que a gestão audita quando a conta chega.' },
        { alvo: noModal('.rec-grid', '#modal-det-body'), titulo: '📦 Registrar recebimento',
          antes: async () => {
            fecharModal();
            const o = await acharOC(['enviada', 'recebida_parcial']);
            if (o) { await abrirReceber(o.id); document.getElementById('modal-det').classList.add('on'); await esperar(400); }
            else { await abrirOC(['recebida', 'aprovada', 'solicitada']); }
          },
          texto: 'Chegou a mercadoria? Quem solicitou (ou a gestão / Visão geral) registra o <b>recebimento item a item</b>: a quantidade é o que chegou <b>agora</b> — acumula. Tudo completo → <b>Recebida</b>; faltou algo → <b>Recebida parcial</b>, e a OC continua aguardando o resto. Item que não está na OC: <b>não</b> receba. Itens <b>vinculados a peça do catálogo</b> seguem para <b>Manutenção › Materiais › Receber</b>: só na conferência de lá o saldo sobe e o fornecedor vira referência da peça.' },
        { alvo: () => $('#modal-det.on #rec-nf') ? $('#modal-det.on #rec-nf').closest('.fld') : ($('#modal-det.on .acoes') || $('#modal-det-body')), titulo: 'NF, complemento e o que muda na OT',
          antes: async () => { const t = $('#modal-det.on #rec-nf'); if (t) t.scrollIntoView({ block: 'center' }); await esperar(200); },
          texto: 'Informe o <b>nº da Nota Fiscal</b> e, se houver, a observação (desconto, brinde) — vai para a trilha. Veio algo fora da OC? Entra como <b>complemento</b> até R$ 100 ou 20% da OC; acima disso, OC nova. <b>Na OT vinculada</b>: o valor recebido <b>soma no custo da OT</b> e ela ganha a linha "📦 OC recebida" no histórico — o status da OT <b>não muda</b> sozinho.' },
        { alvo: () => [...document.querySelectorAll('#modal-det.on .acoes .btn')].find(b => /Imprimir/.test(b.textContent)) || $('#modal-det.on .acoes') || $('#modal-det-body'), titulo: '🖨 Imprimir / 📲 WhatsApp',
          antes: async () => { fecharModal(); await abrirOC(['aprovada', 'enviada', 'recebida_parcial', 'recebida']); const t = $('#modal-det.on .acoes'); if (t) t.scrollIntoView({ block: 'center' }); await esperar(200); },
          texto: '<b>🖨 Imprimir/PDF</b> gera a Ordem de Compra formal: dados da Garra, fornecedor, itens, <b>solicitante e aprovador com data</b>. É o documento que o fornecedor deve citar na NF. <b>📲 WhatsApp</b> monta a mensagem com o número da OC e, se ela está Aprovada, já a marca como <b>Enviada</b>.' },
        { alvo: '#f-status', titulo: 'Acompanhar por status',
          antes: async () => { fecharModal(); irTab('lista'); await esperar(600); },
          texto: 'Na aba <b>📋 OCs</b>, o filtro <b>Status</b> separa <b>Solicitada</b> (aguardando aprovação), <b>Aprovada</b>, Enviada, Recebida, <b>Rejeitada</b> e Cancelada; com setor e mês ao lado. Quem aprova ou tem Visão geral vê as OCs de todo mundo.' },
        { alvo: '#tela-rel .panel', titulo: '📊 Relatório de Compras',
          antes: async () => { irTab('rel'); await esperar(400); },
          texto: 'Período, status e setor. <b>👁 Visualizar</b> mostra o total, o resumo <b>por setor</b> e cada OC com NF; <b>⬇ Excel</b> e <b>🖨 PDF</b> para o financeiro e o fechamento do mês.' },
        { alvo: '#tela-config .panel', titulo: '⚙️ Alçadas',
          quando: () => typeof PODE_CONFIG!=='undefined' && !!PODE_CONFIG,
          antes: async () => { irTab('config'); await esperar(800); },
          texto: 'Só a gestão (permissão <b>Compras — Alçadas</b>): limite <b>por compra</b> e <b>teto mensal</b> de cada usuário, ∞ = sem limite, vazio = não aprova nada. OC dentro da alçada de quem cria é aprovada em 1 passo. Aqui também se cadastram os <b>Setores</b> e as <b>Condições de pagamento</b>.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina: <b>✅ Aprovar → abrir a OC → conferir itens e orçamento → ✓ Aprovar ou ✕ Rejeitar com motivo</b>. Aprovada vai ao fornecedor por 📲 WhatsApp ou 🖨 PDF; chegou, <b>📦 Registrar recebimento</b> com a NF — o custo cai na OT. Fechamento: 📊 Relatório. Repita este tour quando quiser pelo ▶ Como fazer.' },
      ],
    },
  };

  window.guiaAbrir = function (nome) {
    const g = window.GUIAS_COMPRAS[nome]; if (!g || !window.GarraGuia) return;
    if (nome === 'aprovar' && !(typeof PODE_APROVAR!=='undefined' && PODE_APROVAR)) { try { toast('Esta demonstração é para quem aprova OCs (permissão Compras — Aprovar + alçada). Veja "Solicitar uma compra".', 'warn'); } catch (e) { } return; }
    _ocs = null;
    const r = { titulo: g.titulo, pop: g.pop, passos: g.passos.filter(p => !p.quando || p.quando()) };
    r.aoSair = () => {
      fecharModal();
      try { document.querySelectorAll('#n-itens .peca-sug').forEach(e => { e.innerHTML = ''; }); } catch (e) { }   // (11/11/2026) sugestões do catálogo
      try { if (nome === 'solicitar' && _livre) limparNova(); } catch (e) { }
      try { if (abaAtual() !== _abaInicial) irTab(_abaInicial); } catch (e) { }
    };
    GarraGuia.iniciar(r);
  };
  window.guiaMenu = function (ev) {
    ev && ev.stopPropagation();
    let m = document.getElementById('guia-menu');
    if (m) { m.remove(); return; }
    m = document.createElement('div'); m.id = 'guia-menu';
    m.style.cssText = 'position:fixed;top:48px;right:12px;z-index:9999;background:#fff;color:#0F172A;border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:6px;min-width:260px;max-width:calc(100vw - 24px);font-size:13px';
    m.innerHTML = '<div style="padding:6px 10px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
      + Object.entries(window.GUIAS_COMPRAS).map(([k, g]) => {
        const bloq = k === 'aprovar' && !(typeof PODE_APROVAR!=='undefined' && PODE_APROVAR);
        return '<div style="padding:8px 10px;cursor:pointer;border-radius:6px' + (bloq ? ';opacity:.5' : '') + '" onmouseover="this.style.background=\'#F1F5F9\'" onmouseout="this.style.background=\'\'" onclick="document.getElementById(\'guia-menu\').remove();guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8;font-size:11px">· ' + (g.passos.length - 2) + ' passos' + (bloq ? ' · só quem aprova' : '') + '</span></div>';
      }).join('');
    document.body.appendChild(m);
    setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
  };
  // ?guia=solicitar | ?guia=aprovar — espera a tela carregar (SSO, permissões e lista) e começa
  (function () {
    const q = window._GUIA_Q; if (!q || !window.GUIAS_COMPRAS[q]) return;
    let n = 0; const t = setInterval(() => {
      n++;
      const pronto = typeof SETORES!=='undefined' && SETORES.length && !document.querySelector('#lista-ocs .spin') && document.querySelector('#resumo-cards .kcard');
      if (pronto) { clearInterval(t); setTimeout(() => guiaAbrir(q), 600); } else if (n > 60) clearInterval(t);
    }, 500);
  })();
})();
