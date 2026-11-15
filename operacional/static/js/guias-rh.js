/* (15/11/2026) Tours guiados da tela RH (/rh) — GarraGuia. Três roteiros:
 *   fichas     — fichas dos colaboradores: lista, importar do MAIS, dados, função e custo da hora, logins das plataformas EAD
 *   nrs        — controle de NRs: catálogo e exigência por cargo, registrar treinamento, painel geral, NRs a partir dos certificados
 *   documentos — aba Documentos: categorias, anexar, sigilosos com biometria, importação da pasta do servidor (.zip)
 * Modo demonstração: mostra a tela real, não grava (alvos bloqueados ao clique; janelas fechadas ao sair).
 * Abre pelo botão ▶ Como fazer (cabeçalho) ou por /rh?guia=fichas | nrs | documentos.
 * Passos `soRh` só aparecem para quem tem RH — Completo; quem só vê NRs (rh_nr) tem apenas o roteiro `nrs`, reduzido. */
(function () {
  const dorme = (ms) => new Promise(r => setTimeout(r, ms));
  const rh = () => !!(typeof EU !== 'undefined' && EU && EU.pode_rh);
  const botao = (re, raiz) => [...(raiz || document).querySelectorAll('button')].find(b => re.test(b.textContent) && b.offsetParent);
  const abaBt = (re) => [...document.querySelectorAll('#ficha .abas button')].find(b => re.test(b.textContent));
  const irTela = async (t) => { try { fecharModal(); } catch (e) { } tela(t); await dorme(900); };
  // abre a ficha de demonstração (a primeira da lista) na aba pedida
  const fichaDemo = async (k) => {
    try { fecharModal(); } catch (e) { }
    if (!document.getElementById('t-col').classList.contains('on')) { tela('col'); await dorme(500); }
    const c = (typeof _lista !== 'undefined' ? _lista : [])[0];
    if (!c) return;
    if (_atual !== c.id || !document.getElementById('fic-corpo')) { await abrir(c.id, k); await dorme(700); }
    else if (k) { aba(k); await dorme(600); }
  };
  const modalEl = () => document.querySelector('#modal .mod');
  const corpo = () => document.getElementById('fic-corpo') || document.getElementById('ficha');

  window.GUIAS_RH = {
    fichas: {
      titulo: 'Fichas dos colaboradores', pop: 'RH — Cadastro', soRh: true,
      passos: [
        { titulo: 'O cadastro de quem trabalha na Garra 👥',
          texto: '<b>Bem-vinda à demonstração das fichas do RH.</b><br><br>Você vai ver como as fichas chegam do <b>MAIS</b>, o que fica em cada aba, onde se define a <b>função e o custo da hora</b> usados na Manutenção e onde ficam os <b>logins das plataformas de curso</b>.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: '.lista-top', antes: () => irTela('col'),
          titulo: 'A lista e os filtros',
          texto: 'Busque pelo nome ou filtre por <b>situação</b> (ativos, afastados, desligados) e por <b>cargo</b>. A etiqueta ao lado de cada nome mostra as NRs: <b>em dia</b>, <b>faltam N</b> ou <b>vencida</b>.' },
        { alvo: () => botao(/Importar do MAIS/),
          titulo: 'Importar do MAIS',
          texto: 'A base oficial é o <b>MAIS</b>. Exporte a planilha de colaboradores de lá e traga por aqui: o sistema mostra uma <b>prévia</b> (quem é novo, quem mudou de cargo, quem foi desligado) e só grava quando você confirma. Nome, CPF, cargo, admissão e desligamento vêm sempre do MAIS; salário e folha <b>não</b> vêm — continuam lá.' },
        { alvo: () => botao(/Importar logins/),
          titulo: 'Ficha × login',
          texto: 'Ficha é a <b>pessoa</b>; login é o <b>acesso ao sistema</b>. Nem todo colaborador tem login, e o RH nunca mexe no login de ninguém. Este botão só cria fichas para quem já entra no sistema e ainda não tem ficha. Na importação do MAIS o sistema sugere o login de cada pessoa pelo nome.' },
        { alvo: () => document.querySelector('#ficha .fic-top') || document.getElementById('ficha'), antes: () => fichaDemo('dados'),
          titulo: 'A ficha',
          texto: 'Clique em alguém na lista. No topo: nome, apelido, cargo, vínculo, situação e se tem login. As <b>abas</b> separam os assuntos; as que têm 🔒 só aparecem para quem tem <b>RH — Completo</b>.' },
        { alvo: corpo, antes: () => fichaDemo('dados'), posicao: 'esquerda',
          titulo: 'Aba Dados',
          texto: 'Dados de identificação, cargo, admissão, vínculo e o <b>login</b> ligado à pessoa. O que veio do MAIS é atualizado a cada importação; apelido, telefone e observações são da Garra e a importação não mexe.' },
        { alvo: corpo, antes: () => fichaDemo('funcao'), posicao: 'esquerda',
          titulo: 'Aba Função e HH',
          texto: 'Aqui fica a <b>função na manutenção</b> (mecânico, soldador…) e o <b>custo da hora</b> da pessoa. É este valor que a Manutenção usa para calcular a mão de obra das OTs. Só o RH completo vê e altera.' },
        { alvo: corpo, antes: () => fichaDemo('ead'), posicao: 'esquerda',
          titulo: 'Aba Plataformas EAD',
          texto: 'O <b>login e a senha</b> de cada pessoa nas plataformas de curso (Engehall, Prime Cursos, Previnir…). A senha fica <b>criptografada</b>: para ver, o sistema pede a sua <b>biometria</b> e registra quem viu e quando.' },
        { alvo: () => document.getElementById('bt-par'),
          titulo: 'Parametrizar',
          texto: 'O que vale para todos: catálogo de <b>NRs e treinamentos</b>, quais NRs cada <b>cargo</b> exige, as <b>plataformas EAD</b>, as <b>categorias de documentos</b> e a exigência de biometria. Veja os roteiros <b>Controle de NRs</b> e <b>Documentos</b> no ▶ Como fazer.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina: <b>exporte os colaboradores do MAIS → Importar do MAIS → confira a prévia → confirmar</b>. Depois ajuste na ficha o que é da Garra: apelido, login, função e custo da hora, logins das plataformas. Repita este tour pelo ▶ Como fazer.' },
      ],
    },

    nrs: {
      titulo: 'Controle de NRs e treinamentos', pop: 'RH — NRs',
      passos: [
        { titulo: 'Quem fez, quem falta, o que vence 🎓',
          texto: '<b>Bem-vinda à demonstração do controle de NRs.</b><br><br>Você vai ver onde se define quais NRs cada cargo exige, como registrar um treinamento com o certificado, e o <b>painel</b> que mostra de uma vez quem está em dia, quem falta e o que vence.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { soRh: true, alvo: '#par-cursos', antes: () => irTela('par'),
          titulo: 'O catálogo de NRs e treinamentos',
          texto: 'Em <b>Parametrizar</b>. Cada NR tem a <b>reciclagem em meses</b> — é ela que calcula o vencimento. Use <b>+ Adicionar NR ou treinamento</b> também para cursos que não são NR (Direção defensiva, Operador de escavadeira): escolha o tipo "Outro treinamento".' },
        { soRh: true, alvo: '#par-exig', antes: () => irTela('par'), posicao: 'esquerda',
          titulo: 'O que cada cargo exige',
          texto: 'Para cada <b>cargo</b> (vem do MAIS), marque as NRs obrigatórias. É isso que faz aparecer <b>"Falta"</b> no painel: motorista sem Direção defensiva, operador sem NR-11. Mudou aqui, vale para todos do cargo na hora.' },
        { alvo: corpo, antes: () => fichaDemo('nrs'), posicao: 'esquerda',
          titulo: 'As NRs de cada pessoa',
          texto: 'Na ficha, aba <b>NRs e treinamentos</b>. Em cima, a situação de cada NR: <b>válida</b>, <b>vence em até 30 dias</b>, <b>vencida</b>, <b>falta</b> ou <b>em andamento</b>, com a data e onde foi feita. Embaixo, o histórico de tudo que a pessoa já fez.' },
        { soRh: true, alvo: modalEl, antes: async () => { await fichaDemo('nrs'); treinForm(); await dorme(500); },
          titulo: 'Registrar um treinamento',
          texto: 'Escolha a <b>NR</b>, a situação (concluído, em andamento, inscrito), <b>onde foi feito</b> — presencial ou EAD, e em qual plataforma — e a <b>data de conclusão</b>. O vencimento é calculado sozinho pela reciclagem da NR. Anexe o <b>certificado</b> (PDF ou foto) no mesmo lugar.' },
        { alvo: '#p-kpis', antes: () => irTela('pnr'),
          titulo: 'Painel de NRs',
          texto: 'O resumo de todos: quantas <b>vencidas</b>, quantas <b>faltam</b> (exigidas pelo cargo e não feitas), quantas <b>vencem em 30 dias</b>, em andamento e válidas. Filtre por cargo e situação.' },
        { alvo: '#p-mat', posicao: 'esquerda',
          titulo: 'Colaborador × NR',
          texto: 'Uma linha por pessoa, uma coluna por NR. <b>Vermelho</b> = falta ou vencida · <b>amarelo</b> = vence em até 30 dias · <b>azul</b> = em andamento · <b>verde</b> = válida. Clique numa célula para abrir a ficha da pessoa já na aba de NRs.' },
        { soRh: true, alvo: '#bt-certpend',
          titulo: 'Registrar NRs dos certificados',
          texto: 'Guardou certificados em <b>Documentos</b> (um a um ou pelo .zip)? Guardar o arquivo <b>não</b> preenche o painel. Este botão lista os certificados que ainda não viraram NR: o sistema lê o nome do arquivo (ex.: <b>CERTIFICADO-NOME-NR-35</b>), sugere a NR, você confere a <b>data de conclusão</b> pelo certificado e confirma. O número entre parênteses é quantos estão esperando.' },
        { alvo: () => botao(/^Excel$/),
          titulo: 'Excel',
          texto: 'Baixa o painel como planilha — para a fiscalização, para o técnico de segurança ou para planejar as próximas turmas.' },
        { alvo: '#p-venc',
          titulo: 'Documentos que vencem',
          texto: 'Logo abaixo: <b>ASO</b>, CNH e outros documentos com vencimento que já venceram ou vencem nos próximos <b>60 dias</b>. Clique para abrir a ficha na aba Documentos.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina: <b>Parametrizar</b> uma vez (NRs e o que cada cargo exige) → a cada curso, <b>registrar o treinamento com o certificado</b> → acompanhar pelo <b>Painel de NRs</b>. Certificados já guardados: <b>Registrar NRs dos certificados</b>. Repita este tour pelo ▶ Como fazer.' },
      ],
    },

    documentos: {
      titulo: 'Documentos do colaborador', pop: 'RH — Documentos', soRh: true,
      passos: [
        { titulo: 'A pasta do colaborador, dentro do sistema 📁',
          texto: '<b>Bem-vinda à demonstração dos Documentos.</b><br><br>A pasta <b>Documentos-NOME</b> do servidor passa a ser a aba <b>Documentos</b> da ficha: ASO, certificados, contratos, ficha de EPI, atestados. Você vai ver como anexar, o que é <b>sigiloso</b> e como trazer as pastas em lote.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: '#doc-cats', antes: () => fichaDemo('docs'),
          titulo: 'As categorias são as pastas',
          texto: 'Cada categoria é uma "subpasta": <b>ASO</b>, <b>Certificados</b>, <b>Contratos</b>, <b>Fichas de EPI</b>… O número mostra quantos documentos há. As com 🔒 são <b>sigilosas</b> (atestados, documentos pessoais).' },
        { alvo: '#doc-lista', antes: () => fichaDemo('docs'), posicao: 'esquerda',
          titulo: 'Os documentos',
          texto: 'Nome, data e <b>vencimento</b> (verde, amarelo ou vermelho). Clique no arquivo para abrir: o link vale <b>5 minutos</b> e é gerado na hora — não fica um endereço permanente circulando. Certificados anexados na aba NRs aparecem aqui também.' },
        { alvo: modalEl, antes: async () => { await fichaDemo('docs'); docForm(); await dorme(500); },
          titulo: 'Anexar documento',
          texto: 'O título mostra <b>de quem é a ficha</b> — confira antes. Escolha a categoria, a data, o vencimento (quando a categoria tem) e um ou mais arquivos: PDF, foto, Word ou Excel, até 10 MB cada. Fotos são <b>comprimidas</b> ao entrar. Se o nome do arquivo citar <b>outro colaborador</b>, o sistema pergunta antes de gravar.' },
        { alvo: () => [...document.querySelectorAll('#doc-cats .it')].find(e => /🔒/.test(e.textContent)) || document.getElementById('doc-cats'), antes: () => fichaDemo('docs'),
          titulo: '🔒 Sigilosos pedem biometria',
          texto: 'Atestados e documentos pessoais só abrem para quem tem <b>RH — Completo</b> e depois de confirmar a identidade com a <b>biometria do computador</b> (Windows Hello: digital, rosto ou PIN). A confirmação vale 15 minutos. Na primeira vez em cada computador o sistema pede para cadastrar.' },
        { alvo: () => botao(/Registro de acessos/),
          titulo: 'Registro de acessos',
          texto: 'Toda abertura de documento sigiloso fica registrada: <b>quem</b> abriu, <b>qual</b> documento e <b>quando</b>. É a sua prova de controle para a LGPD.' },
        { alvo: () => botao(/Importar documentos/), antes: () => irTela('col'),
          titulo: 'Importar em lote (.zip)',
          texto: 'Para trazer as pastas do servidor: selecione algumas pastas <b>Documentos-NOME</b>, botão direito › <b>Enviar para › Pasta compactada</b> (até 60 MB por vez) e escolha o .zip aqui.' },
        { alvo: modalEl, antes: async () => { await irTela('col'); pastaAbrir(); await dorme(500); },
          titulo: 'A prévia antes de gravar',
          texto: 'O sistema lê o .zip e mostra uma <b>prévia</b>: a pessoa sugerida para cada pasta, a categoria de cada subpasta e o tamanho. O botão <b>Marcar só segurança</b> deixa só ASO, certificados e EPI. Nada é apagado do servidor e arquivo repetido não entra duas vezes. Se vierem certificados, o sistema já oferece <b>registrar as NRs</b>.' },
        { alvo: '#par-cats', antes: () => irTela('par'),
          titulo: 'Criar e ajustar categorias',
          texto: 'Em <b>Parametrizar</b>: crie categorias (ex.: CNH), marque se é <b>sigilosa</b>, se <b>tem vencimento</b> e se quem só vê NRs também enxerga. "Nomes de pasta" diz como a subpasta se chama no servidor, para a importação cair na categoria certa.' },
        { alvo: '#par-seg',
          titulo: 'Segurança e espaço',
          texto: 'Quanto os documentos do RH ocupam (o plano gratuito é de <b>1 GB</b>, dividido com as fotos do sistema), a exigência de <b>biometria</b> (só o Administrador desliga) e o botão para <b>cadastrar a biometria deste computador</b>.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina: <b>ficha › Documentos › Anexar documento</b> no dia a dia; <b>Importar documentos (.zip)</b> para trazer as pastas antigas aos poucos, começando por ASO, certificados e EPI. Vencimentos aparecem no Painel de NRs. Repita este tour pelo ▶ Como fazer.' },
      ],
    },
  };

  window.guiaAbrir = function (nome) {
    const base = window.GUIAS_RH[nome]; if (!base || !window.GarraGuia) return;
    if (base.soRh && !rh()) return;
    const g = Object.assign({}, base, { passos: base.passos.filter(p => !p.soRh || rh()) });
    g.aoSair = () => {
      try { fecharModal(); } catch (e) { }
      try { const m = document.getElementById('guia-menu'); if (m) m.remove(); } catch (e) { }
      try { tela('col'); } catch (e) { }
    };
    GarraGuia.iniciar(g);
  };
  window.guiaMenu = function (ev) {
    ev && ev.stopPropagation();
    let m = document.getElementById('guia-menu');
    if (m) { m.remove(); return; }
    const itens = Object.entries(window.GUIAS_RH).filter(([k, g]) => !g.soRh || rh());
    if (itens.length === 1) { guiaAbrir(itens[0][0]); return; }
    m = document.createElement('div'); m.id = 'guia-menu';
    m.style.cssText = 'position:fixed;top:48px;right:12px;z-index:9999;background:#fff;color:#0F172A;border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:6px;min-width:280px;max-width:calc(100vw - 24px);font-size:13px';
    m.innerHTML = '<div style="padding:6px 10px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
      + itens.map(([k, g]) => '<div style="padding:8px 10px;cursor:pointer;border-radius:6px" onmouseover="this.style.background=\'#F1F5F9\'" onmouseout="this.style.background=\'\'" onclick="document.getElementById(\'guia-menu\').remove();guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8">· ' + g.passos.filter(p => !p.soRh || rh()).length + ' passos</span></div>').join('');
    document.body.appendChild(m);
    setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
  };
  // ?guia=fichas | nrs | documentos — espera o login (EU) e a lista carregar e começa
  (function () {
    const q = window._GUIA_Q; if (!q || !window.GUIAS_RH[q]) return;
    let n = 0; const t = setInterval(() => {
      n++;
      const ok = typeof EU !== 'undefined' && EU && EU.nome !== undefined && typeof _lista !== 'undefined' && !/Carregando/.test(document.getElementById('lista').textContent);
      if (ok) { clearInterval(t); setTimeout(() => guiaAbrir(q), 700); } else if (n > 60) clearInterval(t);
    }, 500);
  })();
})();
