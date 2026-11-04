/* (04/11/2026) Tour guiado da tela ⛽ Abastecer (app do celular) — GarraGuia. Texto segue o POP-MNT-03.
 * Modo demonstração: mostra a tela real, não grava. Abre pelo link "▶ Como fazer" no topo da tela
 * ou por /abastecimento?guia=abastecer (no app: /mobile?abrir=/abastecimento?guia=abastecer). */
window.GUIAS_ABASTECIMENTO = {
  abastecer: {
    titulo: 'Abastecimento com foto',
    pop: 'POP-MNT-03',
    passos: [
      { titulo: 'Abasteceu? Registre em 1 minuto ⛽',
        texto: '<b>Bem-vindo à demonstração do Abastecer.</b><br><br>Você vai ver a ordem certa: <b>foto da notinha</b>, <b>foto do horímetro</b>, conferir o que o sistema leu e registrar. É o registro que alimenta o custo do equipamento e as revisões.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: '#box-nota', titulo: '1º — Foto da notinha',
        texto: 'A <b>notinha do posto</b> é obrigatória: é por ela que o financeiro lança a despesa. Tire a foto <b>na hora</b>, com o cupom esticado e tudo legível — posto, data, litros e valor.' },
      { alvo: () => document.querySelector('#tela-form .fbtn .btn-o'), titulo: 'Tirar foto ou Galeria',
        texto: '<b>Tirar foto</b> abre a câmera. <b>Galeria</b> serve se você já fotografou antes. Qualquer uma das duas vale.' },
      { alvo: '#box-leitura', titulo: '2º — Foto do horímetro / KM',
        texto: 'Depois, a foto do <b>painel</b> com o horímetro (ou o KM, nos caminhões). É essa leitura que calcula as revisões — sem ela a manutenção fica no escuro. Se o frentista anotou na nota, pode pular.' },
      { alvo: '#f-texto', titulo: 'Escreva como mandaria no grupo',
        texto: 'Opcional: digite ou <b>dite</b> como você já fazia no WhatsApp — "CB-05 441545 km · EH-50 120 L". O sistema <b>lê as fotos e o texto</b> e preenche os campos sozinho.' },
      { alvo: '#lk-manual', titulo: 'Sem foto?',
        texto: 'Se não der para fotografar, <b>Preencher sem foto</b> abre os campos para digitar. Mas a foto é a sua garantia: use sempre que puder.' },
      { alvo: '#n-posto', antes: () => { if (typeof mostrarDados === 'function') mostrarDados(); }, titulo: 'Conferir o que foi lido — Posto',
        texto: 'O sistema preenche e marca em <b>laranja</b> o que leu da foto; confira. Posto que não está na lista: escolha <b>novo</b> e informe nome e CNPJ (vem na nota).' },
      { alvo: '#n-cupom', titulo: 'Nº do cupom',
        texto: 'O número do cupom evita registro em dobro: a mesma nota do mesmo posto <b>não entra duas vezes</b>.' },
      { alvo: '#n-litros', titulo: 'Litros, preço e valor',
        texto: '<b>Litros da nota</b> é obrigatório; preço por litro e valor total se calculam entre si. O que você digitar vale mais que o que a foto leu.' },
      { alvo: () => document.querySelector('#itens') && (document.querySelector('#itens .it, #itens > div') || document.querySelector('#itens').nextElementSibling), titulo: 'Para onde foi o combustível',
        texto: 'Um item por <b>equipamento ou galão</b>. Em cada um: litros e o <b>horímetro / KM</b> do painel. Abasteceu dois? Adicione o segundo — os litros dos itens têm de <b>fechar com a nota</b>.' },
      { alvo: '#soma', titulo: 'Soma dos itens',
        texto: 'Aqui você vê se a soma bate com os litros da nota. Diferença = algum item com litros errados.' },
      { alvo: '#btn-salvar', antes: () => { try { mostrar('rodape', true); } catch (e) { } }, titulo: 'Registrar abastecimento',
        texto: 'Toque em <b>Registrar</b>. Avisos em laranja (horímetro menor que o anterior, litros acima do tanque) <b>não bloqueiam</b> — corrija se for erro seu, ou registre assim mesmo que a manutenção confere.' },
      { alvo: '#tela-hist', titulo: 'Meus últimos registros',
        texto: 'Confirmação do que você já enviou. Antes de registrar uma nota, olhe aqui — se ela já estiver na lista, não mande de novo.' },
      { titulo: 'Pronto! ✅',
        texto: 'Ordem de sempre: <b>foto da notinha → foto do horímetro → conferir → Registrar</b>. Dúvida: o POP-MNT-03 tem o passo a passo. Repita este tour quando quiser pelo ▶ Como fazer.' },
    ],
  },
};
window.guiaAbrir = function (nome) {
  const g = window.GUIAS_ABASTECIMENTO[nome]; if (!g || !window.GarraGuia) return;
  g.aoSair = () => { try { if (!(window.FOTOS && FOTOS.nota) && !document.getElementById('n-litros').value) { mostrar('sec-dados', false); mostrar('rodape', false); const l = document.getElementById('lk-manual'); if (l) l.style.display = ''; } } catch (e) { } };
  GarraGuia.iniciar(g);
};
// ?guia=abastecer — espera a tela do formulário aparecer (login/contexto) e começa
(function () {
  const q = window._GUIA_Q; if (!q) return;
  let n = 0; const t = setInterval(() => { const f = document.getElementById('tela-form'); n++; if (f && !f.classList.contains('hide')) { clearInterval(t); setTimeout(() => guiaAbrir(q), 600); } else if (n > 60) clearInterval(t); }, 500);
})();
