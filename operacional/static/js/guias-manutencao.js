/* (04/11/2026) Roteiros dos tours guiados do módulo Manutenção (GarraGuia). Um roteiro por assunto;
 * o texto segue o POP correspondente. Modo demonstração: o tour mostra, não grava — os elementos
 * destacados ficam bloqueados ao clique. Para abrir: botão ▶ Como fazer (barra superior) ou ?guia=triagem */
window.GUIAS_MANUTENCAO = {

  triagem: {
    titulo: 'Triagem de pedidos',
    pop: 'POP-MNT-02 · 4.2',
    passos: [
      { titulo: 'Tem pedido chegando!',
        texto: '<b>Bem-vinda à demonstração da triagem de pedidos.</b><br><br>Nesta demonstração você vai ver como os pedidos do aplicativo e do checklist chegam à manutenção, como converter em Ordem de Trabalho ou recusar, definir o responsável e emitir a OT para o mecânico.<br><br>Mostramos o caminho na tela real — nada é gravado durante o tour. 🔧📋',
        botao: 'Começar demonstração' },
      { alvo: '#tn-ots', antes: () => showModule('ots'),
        titulo: 'Ordens de Trabalho',
        texto: 'Tudo começa aqui. A guia <b>Ordens de Trabalho</b> reúne as OTs e a triagem dos pedidos que ainda não viraram OT.' },
      { alvo: () => document.querySelector('#ped-badge') && document.querySelector('#ped-badge').closest('button'),
        titulo: 'Pedidos (n)',
        texto: 'O botão laranja <b>Pedidos</b> mostra quantos pedidos aguardam triagem. É o primeiro lugar a olhar de manhã. Vamos abrir.' },
      { alvo: () => document.querySelector('#eq-ficha table tbody tr'), antes: async () => { await abrirPedidos(); await new Promise(r => setTimeout(r, 500)); },
        titulo: 'A fila de pedidos',
        texto: 'Cada linha é um pedido: número, data, <b>canal</b> (app, checklist, telefone…), <b>urgência</b>, equipamento, quem pediu e a descrição. Pedido <b>urgente</b> = equipamento parado — trate primeiro.' },
      { alvo: () => document.querySelector('#eq-ficha select[title="Canal de origem do pedido"]'),
        titulo: 'Filtrar por canal',
        texto: 'Aqui você filtra a fila por canal de origem. Os pedidos vindos do <b>checklist</b> trazem a não conformidade e as fotos do operador.' },
      { alvo: () => document.querySelector('#eq-ficha button[title="Converter em OT"]'),
        titulo: '▶ Converter em OT',
        texto: 'Converter cria a OT com número próprio, tipo <b>C1 — Reparação de avaria</b> e Prog. Início = hoje. O solicitante vê <b>"Virou OT"</b> no aplicativo na mesma hora.' },
      { alvo: () => document.querySelector('#eq-ficha button[title="Recusar"]'),
        titulo: '✕ Recusar',
        texto: 'Recusar pede o <b>motivo</b>, que vai para o aplicativo de quem pediu. Use quando for duplicado, já resolvido ou não for manutenção.' },
      { posicao: 'direita',
        antes: async () => {
          document.getElementById('eq-ficha').classList.remove('show');
          let ots = [];
          try { const r = await mntFetch('/manutencao/api/ots'); const l = Array.isArray(r) ? r : (r.itens || []);
            const ordem = ['aberta', 'programada', 'em_andamento', 'aguardando_peca'];   // prefere uma OT ainda não emitida, para o passo Emitir
            ots = l.filter(o => ordem.includes(o.status)).sort((a, b) => ordem.indexOf(a.status) - ordem.indexOf(b.status)); } catch (e) { }
          if (ots.length) { await verOTReal(ots[0].id); await new Promise(r => setTimeout(r, 700)); const t = document.querySelector('#eq-ficha .mtab'); if (t) t.click(); await new Promise(r => setTimeout(r, 300)); }
        },
        titulo: 'Tipo de trabalho',
        texto: 'Na ficha da OT, aba <b>Origem</b>: o <b>Tipo Trab.</b> é um campo só — <b>C1</b> reparação de avaria, <b>C2</b> corretiva deferida, <b>A1</b> preventiva sistemática, <b>B1</b> condicionada, <b>M1</b> melhoria. A classe Corretiva/Preventiva ao lado é derivada dele. Pedido convertido já nasce C1.',
        alvo: '#oe-tt' },
      { alvo: '#oe-resp', posicao: 'esquerda',
        titulo: 'Definir o responsável',
        texto: '<b>Responsável</b> — o mecânico que vai executar (Intervenção interna). Se ficar vazio, o primeiro mecânico que registrar pelo app assume a OT. Intervenção <b>Externa</b> troca este campo pelo Fornecedor.' },
      { alvo: () => [...document.querySelectorAll('#eq-ficha button')].find(b => /Emitir/.test(b.textContent)) || [...document.querySelectorAll('#eq-ficha button')].find(b => /Encerrar/.test(b.textContent)),
        titulo: '🖨 Emitir',
        texto: 'Emitir passa a OT para <b>Em curso</b>, grava o Início real e a OT aparece para o mecânico na aba OTs do aplicativo. Atenção: <b>✖ Cancelar OT</b> cancela a ordem — para fechar a ficha sem gravar use o ✕ do canto.' },
      { alvo: () => document.querySelector('#ots-f-sinal') && document.querySelector('#ots-f-sinal').closest('label'),
        antes: () => { document.getElementById('eq-ficha').classList.remove('show'); },
        titulo: 'Durante o dia',
        texto: 'Quando o mecânico marca <b>Serviço concluído</b> no app, a OT entra no filtro <b>✅ Sinalizadas p/ encerrar</b>. Essas são as que a gestão precisa encerrar na aba Gestão (Fim + horímetro).' },
      { titulo: 'Pronto! 🎉',
        texto: 'Rotina da manhã: <b>Pedidos → Converter ou Recusar → Responsável → Emitir</b>. Durante o dia: <b>Sinalizadas p/ encerrar</b>.<br><br>O passo a passo completo está no <b>POP-MNT-02</b>. Você pode repetir este tour quando quiser pelo botão ▶ Como fazer.' },
    ],
  },
};

// Abertura: botão ▶ Como fazer na barra superior e deep link ?guia=<nome>
window.guiaAbrir = function (nome) {
  const g = window.GUIAS_MANUTENCAO[nome]; if (!g || !window.GarraGuia) return;
  g.aoSair = () => { try { document.getElementById('eq-ficha').classList.remove('show'); } catch (e) { } };
  GarraGuia.iniciar(g);
};
window.guiaMenu = function (ev) {
  ev && ev.stopPropagation();
  let m = document.getElementById('guia-menu');
  if (m) { m.remove(); return; }
  m = document.createElement('div'); m.id = 'guia-menu';
  m.style.cssText = 'position:fixed;top:48px;right:12px;z-index:9999;background:#fff;color:#0F172A;border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:6px;min-width:260px;font-size:13px';
  m.innerHTML = '<div style="padding:6px 10px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
    + Object.entries(window.GUIAS_MANUTENCAO).map(([k, g]) => '<div style="padding:8px 10px;cursor:pointer;border-radius:6px" onmouseover="this.style.background=\'#F1F5F9\'" onmouseout="this.style.background=\'\'" onclick="document.getElementById(\'guia-menu\').remove();guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8;font-size:11px">· ' + (g.passos.length - 2) + ' passos · ' + (g.pop || '') + '</span></div>').join('');
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
};
