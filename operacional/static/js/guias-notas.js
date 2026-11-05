/* (05/11/2026) Tour guiado da tela 🧾 Notas a lançar (balcão do financeiro: notinhas das OTs + cupons de abastecimento)
 * — GarraGuia. Texto segue o POP-FIN-01. Modo demonstração: mostra a tela real, não grava.
 * Abre pelo botão "▶ Como fazer" no cabeçalho ou por /manutencao/notas?guia=lancar. */
window.GUIAS_NOTAS = {
  lancar: {
    titulo: 'Notas a lançar na MAIS',
    pop: 'POP-FIN-01',
    passos: [
      { titulo: 'As notinhas do campo chegam aqui 🧾',
        texto: '<b>Bem-vinda à demonstração do balcão Notas a lançar.</b><br><br>Tudo que o pessoal fotografa — a <b>nota fiscal anexada na OT</b> pelo mecânico e o <b>cupom do posto</b> registrado no app de abastecimento — cai nesta lista. Você vai ver como filtrar a competência, conferir a foto, digitar o valor, marcar o que já foi lançado na MAIS e tirar o Excel.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },

      { alvo: '#f-orig', titulo: 'Origem',
        texto: '<b>Todas</b> mistura as duas origens numa lista só. <b>Só OTs</b> = notas fiscais de peças e serviços anexadas nas Ordens de Trabalho (🔧). <b>Só abastecimentos</b> = cupons de combustível que o operador registrou com foto (⛽). O Excel também respeita esta escolha.' },

      { alvo: '#f-mes', titulo: 'Competência',
        texto: 'Mês da <b>data da nota</b>. A tela abre no mês atual. A fatura dos postos <b>fecha dia 15</b>: nessa data confira se o mês anterior já está todo lançado — mude a competência para trás e busque de novo.' },

      { alvo: '#f-forn', titulo: 'Fornecedor / posto',
        texto: 'Filtra por um fornecedor (🔧, vem da OT) ou um posto (⛽, vem do cupom). A lista se monta com o que apareceu na busca — serve para fechar a fatura de um posto de cada vez.' },

      { alvo: () => document.getElementById('f-pend') && document.getElementById('f-pend').closest('label'), titulo: 'Só pendentes de lançar',
        texto: 'Marcado por padrão: a lista mostra só o que <b>ainda não foi lançado na MAIS</b>. Desmarque para ver o mês inteiro, inclusive o já lançado (linhas verdes). Ao lado, <b>ver removidas da visão</b> lista as fotos que você tirou do balcão por não serem nota.' },

      { alvo: () => [...document.querySelectorAll('.filtros button')].find(b => /Buscar/.test(b.textContent)), titulo: '🔍 Buscar',
        texto: 'Mudou competência, fornecedor ou "só pendentes"? Clique em <b>Buscar</b> para recarregar. Origem e "ver removidas" já recarregam sozinhos.' },

      { alvo: '#cards', antes: async () => { if (!document.querySelector('#cards .card')) { await carregar(); await new Promise(r => setTimeout(r, 500)); } },
        titulo: 'Totais por fornecedor',
        texto: 'Um cartão por fornecedor/posto: <b>R$</b> somado, quantas notas, quantas <b>pendentes</b> de lançar e quantas <b>sem valor</b> (nota de OT que ainda não conferiu). Use para bater com a fatura antes de lançar. Erro de permissão em uma das origens aparece aqui em vermelho.' },

      { alvo: () => document.querySelector('#corpo tr'), titulo: 'Cada linha é uma nota',
        texto: '<b>Origem</b> (🔧 OT ou ⛽ Abastec.), data, <b>referência</b> (número da OT ou do cupom), equipamento, fornecedor/posto e quem registrou. No abastecimento a descrição já traz combustível, litros e preço por litro; na OT é o que o mecânico escreveu.' },

      { alvo: () => document.querySelector('#corpo tr td:nth-child(6)') || [...document.querySelectorAll('th')].find(t => /Foto/.test(t.textContent)), titulo: 'A foto da nota',
        texto: 'Miniatura da foto; clique para abrir em tamanho real e <b>conferir</b> fornecedor, número, data e valor. <b>sem foto</b> = o registro veio sem imagem — cobre a foto antes de lançar. <b>sem fornecedor</b> = a OT está sem fornecedor; a manutenção acerta na OT.' },

      { alvo: () => document.querySelector('#corpo td.r input') || document.querySelector('#corpo tr td:nth-child(9)') || document.querySelector('th.r'), titulo: 'Nº da nota e valor',
        texto: 'Nota de <b>OT</b>: você <b>digita</b> o número da nota e o <b>valor</b> lendo a foto — salva ao sair do campo. Passe o mouse no valor para ver quanto a OT já tem lançado em peças e outros. Nota de <b>abastecimento</b>: valor já vem calculado do app (litros × preço), não se edita aqui.' },

      { alvo: () => document.querySelector('#corpo tr td:nth-child(11) span[title]') || document.querySelector('th[title^="Abastecimento"]'), titulo: 'Conf. manutenção',
        texto: 'Só para abastecimento: a <b>Bruna confere</b> cada cupom (foto, litros, horímetro) na Conferência de abastecimentos — POP-MNT-04. <b>✔ conferida</b> = pode lançar. <b>⏳ não conferida</b> = espere a manutenção, ou avise. Nota de OT mostra —.' },

      { alvo: () => document.querySelector('#corpo tr td:nth-child(12) label') || [...document.querySelectorAll('th')].find(t => /MAIS/.test(t.textContent)), titulo: '✔ Lançada na MAIS',
        texto: 'Lançou no ERP? Marque a caixa: fica registrado <b>quem e quando</b>, a linha vira verde e, com "só pendentes" marcado, some na próxima busca. Nota de OT <b>sem valor</b> não deixa marcar. Marcou errado? Desmarque. É esta marca que concilia o Garra Sistemas com a MAIS.' },

      { alvo: () => document.querySelector('#corpo td span[title^="Não é nota"]') || document.querySelector('#corpo tr td:last-child') || document.querySelector('thead th:last-child'), titulo: '🚫 Não é nota',
        texto: 'Às vezes o mecânico anexa a foto do serviço no campo de nota fiscal. Clique em 🚫, informe o motivo e ela <b>sai deste balcão</b> — continua na OT, nada é apagado. Aparece em "ver removidas da visão" e ↩ devolve ao balcão. Só para nota de OT ainda não lançada.' },

      { alvo: () => [...document.querySelectorAll('.filtros button')].find(b => /Excel/.test(b.textContent)), titulo: '📊 Excel da competência',
        texto: 'Baixa a planilha do mês filtrado — uma linha por nota, com link da foto (válido por 7 dias) e a coluna "lançada". Com Origem = Todas vêm <b>dois arquivos</b>: um das OTs e um dos abastecimentos.' },

      { titulo: 'Pronto! ✅',
        texto: 'Rotina: <b>competência → Buscar → abrir a foto → nº e valor (OT) → lançar na MAIS → marcar ✔</b>. Dia 15: feche o mês anterior dos postos e confira os cartões contra a fatura. Foto que não é nota: 🚫. O passo a passo completo está no <b>POP-FIN-01</b>. Repita este tour quando quiser pelo ▶ Como fazer.' },
    ],
  },
};
window.guiaAbrir = function (nome) {
  const g = window.GUIAS_NOTAS[nome]; if (!g || !window.GarraGuia) return;
  g.aoSair = () => { };   // o tour não muda filtros nem campos: nada a desfazer
  GarraGuia.iniciar(g);
};
// ?guia=lancar — espera a primeira busca terminar (lista montada) e começa
(function () {
  const q = window._GUIA_Q; if (!q) return;
  let n = 0; const t = setInterval(() => {
    n++; const c = document.getElementById('corpo');
    if (c && !/Carregando/.test(c.textContent)) { clearInterval(t); setTimeout(() => guiaAbrir(q), 600); } else if (n > 60) clearInterval(t);
  }, 500);
})();
