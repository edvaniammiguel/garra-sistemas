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

  conferencia: {
    titulo: 'Conferência de abastecimentos',
    pop: 'POP-MNT-04',
    passos: [
      { titulo: 'Abastecimentos do dia, conferidos em 5 minutos ⛽',
        texto: '<b>Bem-vinda à demonstração da conferência de abastecimentos.</b><br><br>Os operadores registram pelo app com foto; aqui você confere <b>toda a frota num dia só</b>: o que o sistema leu, os alertas (leitura fora da sequência, placa, cupom repetido, consumo), corrige o que precisa e marca ✔ conferida. Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: '#tn-frota', antes: () => showModule('frota'), titulo: 'Frota',
        texto: 'A conferência fica na guia <b>Frota</b>, porque cada abastecimento pertence a um equipamento.' },
      { alvo: () => [...document.querySelectorAll('#mwbar-frota button')].find(b => /Ferramentas/.test(b.textContent)), titulo: 'Ferramentas',
        texto: 'Em <b>Ferramentas › ⛽ Conferência de abastecimentos (toda a frota)</b> abre a tela do dia. Pelo botão direito num equipamento você abre só os abastecimentos dele.' },
      { alvo: () => [...document.querySelectorAll('#eq-ficha button')].find(b => /Ontem \+ hoje/.test(b.textContent)),
        antes: async () => { await abConferenciaJanela(); await new Promise(r => setTimeout(r, 500)); if (!document.querySelector('#eq-ficha table.tbl tbody tr')) { await abConferenciaJanela(_abcfDia(-30), _abcfDia(0), false); await new Promise(r => setTimeout(r, 500)); } },
        titulo: 'Período',
        texto: 'A tela abre em <b>ontem + hoje</b> com <b>só pendentes</b> marcado — é a rotina da manhã. Para fechar o mês ou procurar algo antigo, escolha 7 dias ou um período e desmarque "só pendentes".' },
      { alvo: () => document.querySelector('#eq-ficha .panel'), titulo: 'Resumo do período',
        texto: 'Quantas notas, litros e reais entraram, quantas ainda estão <b>pendentes</b> e quantas têm <b>alerta</b>. Meta do dia: pendentes = 0.' },
      { alvo: () => document.querySelector('#eq-ficha table.tbl tbody tr td:nth-child(9)'), titulo: 'Alertas da nota',
        texto: 'Cada nota mostra <b>ok</b> ou os alertas: <b>leitura fora da sequência</b> (horímetro menor que o anterior ou salto grande), <b>placa diferente</b> da nota, <b>consumo fora do normal</b>, <b>sem foto da nota</b>. Alerta não bloqueia — pede o seu olhar.' },
      { alvo: () => document.querySelector('#eq-ficha table.tbl tbody tr td:last-child'), titulo: 'Situação',
        texto: '<b>⏳ a conferir</b> → ainda não olhou. <b>✔ conferida</b> → quem conferiu e quando. <b>sem nota</b> → registro antigo (carga/legado), nada a conferir.' },
      { alvo: () => document.querySelector('#eq-ficha table.tbl tbody tr'), titulo: 'Abrir a nota',
        texto: 'Clique na linha: abre a janela de abastecimentos <b>do equipamento</b>, com a foto da nota e do painel, para conferir ou corrigir. Ao fechar, você volta a esta lista.' },
      { alvo: '#ab-posto', posicao: 'esquerda',
        antes: async () => { const it = (_abcf.itens || []).find(x => x.equipamento_id); if (it) { _abcfVoltar = true; window._gcfEq = it.equipamento_id; await equipAbastecimentos(it.equipamento_id); await new Promise(r => setTimeout(r, 700)); } },
        titulo: 'Corrigir ou lançar pelo computador',
        texto: 'Aqui você <b>corrige</b> o que a foto leu errado (posto, cupom, litros, leitura) ou <b>lança uma nota</b> que não veio pelo app. Posto novo: o botão ao lado renomeia e acerta o CNPJ. Lembre: o cupom é único por posto.' },
      { alvo: () => document.querySelector('#eq-ficha button[title^="Marcar como conferida"]') || document.querySelector('#eq-ficha [title="Voltar a pendente"]') || [...document.querySelectorAll('#eq-ficha th')].find(t => /CONFER/i.test(t.textContent)), titulo: '✔ Conferir',
        texto: 'Olhou a foto, bateu com os números? <b>✔ Conferir</b> na nota. Marcou errado? ↩ volta a pendente. Na lista da frota há também <b>"Conferir todos sem alerta"</b> para o dia tranquilo. E quando o alerta é de <b>horímetro/KM</b>? Dois casos, a seguir.' },
      { alvo: () => document.querySelector('#eq-ficha tr[ondblclick*="abEditar"]') || document.querySelector('#eq-ficha table tbody tr'), titulo: 'Conflito de horímetro / KM — caso 1: o erro está na nota',
        texto: 'Alerta <b>"leitura fora da sequência"</b>: a leitura da nota ficou <b>menor que a anterior</b> ou deu um salto grande. Primeiro olhe a <b>foto do painel</b>. Se o operador (ou a leitura automática) errou o número, <b>dê duplo clique no registro</b> — os campos de cima são preenchidos para editar.' },
      { alvo: '#ab-leitura', posicao: 'esquerda',
        antes: async () => { try { const i = (typeof _abLista !== 'undefined' ? _abLista : []).findIndex(a => a.destino_tipo !== 'galao' && a.origem !== 'galao'); if (i >= 0 && typeof abEditar === 'function') abEditar(i, window._gcfEq); } catch (e) { } },
        titulo: 'Digite a leitura certa e salve',
        texto: 'Corrija o campo <b>Leitura</b> (horímetro em horas; KM nos caminhões) com o número do painel e clique em <b>💾 Salvar alterações</b>. O sistema recalcula o consumo e, se a leitura voltou para a sequência, o alerta some. Litros, preço e cupom corrigem-se do mesmo jeito. Nota rateada entre vários equipamentos: posto e cupom só pelo app.' },
      { alvo: () => document.querySelector('#eq-ficha tr[style*="FEF2F2"]') || document.querySelector('#eq-ficha table tbody tr'), posicao: 'esquerda',
        antes: async () => { try { try { _abEdit = null; } catch (e2) { } await equipFuncionamento(window._gcfEq); await new Promise(r => setTimeout(r, 600)); } catch (e) { } },
        titulo: 'Caso 2: a nota está certa, a leitura anterior é que está errada',
        texto: 'Se a foto confirma a leitura da nota, o problema é um registro <b>anterior</b> absurdo (ex.: 19.000 em vez de 1.900) — ele vira "última leitura conhecida" e rejeita todas as corretas depois dele. Abra o <b>📈 Registro Funcionamento</b> (botão direito no equipamento): as linhas em <b>vermelho ⚠</b> têm salto ou recuo. <b>✏️</b> corrige só a leitura, <b>✕</b> exclui só a leitura (a parte, o checklist ou a nota continuam como estão). Motivo obrigatório — vai ao diário; <b>↺</b> desfaz. Depois volte à nota e marque ✔ conferida.' },
      { alvo: () => [...document.querySelectorAll('#eq-ficha .mono')].find(e => /FMD|h|km/.test(e.textContent)) || document.querySelector('#eq-ficha .muted'), titulo: 'A leitura vira preventiva',
        texto: 'O horímetro/KM da nota entra no <b>Registro de Funcionamento</b> e no <b>FMD-R</b> (média por dia) — é isso que move as revisões. Leitura divergente é guardada, mas <b>não atualiza</b> o equipamento até você conferir. Equipamento sem leitura há mais de 7 dias não gira preventiva: cobre a foto.' },
      { titulo: 'Pronto! ✅',
        texto: 'Rotina diária: <b>Frota › Ferramentas › Conferência</b> → ontem + hoje → olhar alertas → corrigir → ✔ conferir. Conflito de horímetro/KM: erro na nota → duplo clique e corrija; erro na leitura anterior → Registro Funcionamento, ✏️ ou ✕ com motivo. Dia 15 fecha a competência das notas de posto e elas vão para o balcão <b>Notas a lançar</b> (financeiro). O passo a passo completo está no <b>POP-MNT-04</b>.' },
    ],
  },
};

// Abertura: botão ▶ Como fazer na barra superior e deep link ?guia=<nome>
window.guiaAbrir = function (nome) {
  const g = window.GUIAS_MANUTENCAO[nome]; if (!g || !window.GarraGuia) return;
  g.aoSair = () => { try { _abEdit = null; } catch (e) { } try { document.getElementById('eq-ficha').classList.remove('show'); } catch (e) { } };
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
