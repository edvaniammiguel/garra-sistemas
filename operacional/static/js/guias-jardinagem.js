/* (16/11/2026) Tours guiados do módulo Jardinagem — GarraGuia. Um arquivo, duas telas:
 *   campo      — app de campo (jardinagem/static/pwa-app.html): fotos antes/depois, pares, galeria, KM do serviço
 *   escritorio — tela do escritório (jardinagem/templates/desk-app.html): mês, bancada, pares por semana, relatórios
 * Modo demonstração: mostra a tela real, não grava. Abre pelo ▶ Como fazer ou por ?guia=campo | ?guia=escritorio. */
(function () {
  const dorme = (ms) => new Promise(r => setTimeout(r, ms));
  const EH_CAMPO = !!document.getElementById('tFotos');
  const botao = (re) => [...document.querySelectorAll('button')].find(b => re.test(b.textContent) && b.offsetParent);
  const vis = (el) => el && el.offsetParent ? el : null;
  const tela = async (n) => { try { fecharDrawer(); } catch (e) { } try { ir(n); } catch (e) { } await dorme(500); };

  const CAMPO = {
    titulo: 'Jardinagem no campo — fotos e KM', pop: 'JAR — Campo',
    passos: [
      { titulo: 'Fotos antes e depois, direto do serviço 🌿',
        texto: '<b>Bem-vindo à demonstração do app da Jardinagem.</b><br><br>Você vai ver como tirar as fotos, montar os <b>pares antes/depois</b> de cada local, conferir o que já foi salvo e registrar o <b>KM do serviço</b>.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: () => vis(document.getElementById('semCard')) || document.querySelector('header'), antes: () => tela('Fotos'),
        titulo: 'O mês e a semana',
        texto: 'As fotos são organizadas por <b>mês</b> e por <b>semana</b>. O botão <b>☰</b> no topo abre o histórico de meses; o <b>📅</b> troca a semana. Confira sempre se está na semana certa antes de começar.' },
      { alvo: '#bancadaCard', antes: () => tela('Fotos'),
        titulo: 'Bancada — onde as fotos chegam',
        texto: '<b>📷 Câmera</b> tira a foto na hora; <b>🖼 Fotos</b> pega da galeria do celular (pode escolher várias). Tudo cai primeiro na <b>bancada</b>, esperando ser encaixado num par.' },
      { alvo: () => botao(/Novo par/) || document.getElementById('paresWrap'), antes: () => tela('Fotos'),
        titulo: 'Criar um par',
        texto: 'Cada local atendido é um <b>par</b>: uma foto de <b>ANTES</b> e uma de <b>DEPOIS</b>. Toque em <b>+ Novo par</b> para cada local.' },
      { alvo: '#paresWrap', antes: () => tela('Fotos'),
        titulo: 'Encaixar as fotos',
        texto: 'Toque no espaço <b>ANTES</b> ou <b>DEPOIS</b> do par e escolha a foto da bancada. Escreva o <b>nome do local</b>. O par completo (antes + depois) é o que vale para o relatório do cliente.' },
      { alvo: () => vis(document.getElementById('filaCard')) || document.querySelector('.hdr-r') || document.querySelector('header'),
        titulo: 'Sem sinal? O app guarda',
        texto: 'No topo, o app mostra <b>Online</b> ou <b>Offline</b>. Sem internet, as fotos ficam numa <b>fila</b> no celular e sobem sozinhas quando o sinal volta — ou toque em <b>Sync</b>. Não feche o app antes de a fila zerar.' },
      { alvo: () => document.getElementById('galSel') || document.getElementById('tGaleria'), antes: () => tela('Galeria'),
        titulo: 'Galeria — o que já foi salvo',
        texto: 'Escolha a <b>semana</b> e veja os pares que já estão no sistema. É aqui que você confere se não ficou local sem a foto de depois.' },
      { alvo: () => document.querySelector('#tKm .card') || document.getElementById('tKm'), antes: () => tela('Km'),
        titulo: 'KM do serviço',
        texto: 'Um registro por serviço: <b>data</b>, <b>local</b>, <b>KM inicial e final</b> do veículo e <b>hora de início e de fim</b>. O app calcula os quilômetros rodados e o tempo.' },
      { alvo: () => botao(/Salvar Serviço/) || document.getElementById('tKm'),
        titulo: 'Salvar o serviço',
        texto: 'Confira os números e toque em <b>✅ Salvar Serviço</b>. KM final menor que o inicial é erro de digitação — o app avisa.' },
      { alvo: '#kmLista',
        titulo: 'Registros do mês',
        texto: 'Embaixo ficam os serviços já lançados no mês, para conferir e corrigir se preciso.' },
      { alvo: () => document.getElementById('tMais'), antes: () => tela('Mais'),
        titulo: 'Mais',
        texto: 'Mostra a <b>semana atual</b>. Para quem é da gestão aparece também o botão de <b>enviar o relatório</b> por e-mail.' },
      { titulo: 'Pronto! ✅',
        texto: 'Rotina de cada local: <b>foto de ANTES → serviço → foto de DEPOIS → encaixar no par com o nome do local</b>. No fim do serviço: <b>KM › Salvar Serviço</b>. Repita este tour pelo ▶ Como fazer.' },
    ],
    aoSair: () => { try { fecharDrawer(); } catch (e) { } try { ir('Fotos'); } catch (e) { } },
  };

  // abre o primeiro mês do histórico, se nenhum estiver aberto
  const abrirMesDemo = async () => {
    const mc = document.getElementById('mesContent');
    if (mc && mc.style.display !== 'none') return;
    const it = document.querySelector('#listaMeses .sb-item [onclick^="abrirMes"]');   // o mês mais recente (não o cabeçalho do ano)
    if (it) { it.click(); await dorme(1500); }
  };
  const ouMain = (el) => vis(el) || document.getElementById('mainArea');

  const ESCRITORIO = {
    titulo: 'Jardinagem no escritório — fotos e relatórios', pop: 'JAR — Escritório',
    passos: [
      { titulo: 'Do campo ao relatório do cliente 🌿',
        texto: '<b>Bem-vinda à demonstração da Jardinagem no escritório.</b><br><br>Você vai ver como abrir o mês, conferir e organizar os <b>pares antes/depois</b> que vêm do campo e gerar os <b>relatórios de fotos e de KM</b> para o cliente.<br><br>Nada é gravado durante a demonstração.',
        botao: 'Começar demonstração' },
      { alvo: '#sidebar',
        titulo: 'Histórico mensal',
        texto: 'Cada <b>mês</b> de contrato fica aqui. Clique num mês para abrir. As fotos que a equipe envia pelo celular entram no mês e na semana em que foram tiradas.' },
      { alvo: () => botao(/Novo Mês/),
        titulo: 'Novo mês',
        texto: 'No começo de cada mês, crie o mês novo (nome e período). As <b>semanas</b> são criadas dentro dele.' },
      { alvo: () => ouMain(document.querySelector('.mes-toolbar')), antes: abrirMesDemo,
        titulo: 'O mês aberto',
        texto: 'No topo: o nome do mês e os botões de trabalho — <b>Carregar Fotos</b>, <b>Organizar Pares</b>, <b>+ Semana</b>, <b>+ Par vazio</b> e, em verde e azul, os <b>relatórios</b>.' },
      { alvo: () => ouMain(document.querySelector('.kpis')), antes: abrirMesDemo,
        titulo: 'Os números do mês',
        texto: '<b>Fotos na bancada</b> (ainda sem par), <b>pares salvos</b>, <b>pares completos</b> (com antes e depois) e o <b>próximo código</b> sequencial. Par incompleto não vai bem no relatório — confira antes de enviar.' },
      { alvo: () => ouMain(botao(/Carregar Fotos/)), antes: abrirMesDemo,
        titulo: 'Carregar fotos pelo computador',
        texto: 'Recebeu fotos por outro caminho (WhatsApp, cartão)? <b>Carregar Fotos</b> manda para a bancada, igual ao celular.' },
      { alvo: () => ouMain(document.getElementById('bancada')), antes: abrirMesDemo, posicao: 'esquerda',
        titulo: 'Bancada',
        texto: 'Fotos esperando encaixe. <b>Arraste</b> cada foto para o espaço <b>Antes</b> ou <b>Depois</b> do par certo; <b>↩</b> devolve à bancada. <b>⚡ Gerar pares auto</b> monta os pares na ordem em que as fotos foram tiradas.' },
      { alvo: () => ouMain(document.getElementById('semanasWrap')), antes: abrirMesDemo, posicao: 'esquerda',
        titulo: 'Pares por semana',
        texto: 'Cada semana lista seus pares, com <b>código</b>, <b>nome do local</b> e as duas fotos. Clique na foto para ampliar. Corrija o nome do local aqui se o campo escreveu errado.' },
      { alvo: () => ouMain(botao(/Organizar Pares/)), antes: abrirMesDemo,
        titulo: 'Organizar pares',
        texto: 'Abre a tela grande de organização: bancada de um lado, pares do outro, por semana. É o jeito mais rápido quando há muitas fotos.' },
      { alvo: () => ouMain(vis(document.getElementById('salvarBar')) || botao(/Salvar no banco/)), antes: abrirMesDemo,
        titulo: 'Salvar',
        texto: 'Depois de arrastar e ajustar, <b>💾 Salvar no banco</b>. Sem salvar, o que você organizou aqui se perde ao fechar a página.' },
      { alvo: () => ouMain(botao(/Fotos \(mês\)/)), antes: abrirMesDemo,
        titulo: 'Relatórios em Excel',
        texto: '<b>📥 Fotos (mês)</b> gera a planilha com os pares antes/depois do mês inteiro; <b>📥 KM (mês)</b>, a planilha dos serviços e quilômetros rodados.' },
      { alvo: () => ouMain(botao(/Visualizar/)), antes: abrirMesDemo,
        titulo: 'Visualizar e enviar ao cliente',
        texto: '<b>👁 Visualizar</b> mostra o relatório como o cliente vai ver — por semana, fotos e KM — e de lá você <b>envia por e-mail</b>.' },
      { titulo: 'Pronto! ✅',
        texto: 'Rotina: <b>abrir o mês → conferir a bancada e os pares de cada semana → Salvar → Visualizar → enviar ao cliente</b>. No fim do mês: <b>📥 Fotos (mês)</b> e <b>📥 KM (mês)</b>. Repita este tour pelo ▶ Como fazer.' },
    ],
    aoSair: () => { try { document.querySelectorAll('.modal-bg.open').forEach(m => m.classList.remove('open')); } catch (e) { } },
  };

  window.GUIAS_JARDINAGEM = EH_CAMPO ? { campo: CAMPO } : { escritorio: ESCRITORIO };
  window.guiaAbrir = function (nome) {
    const g = window.GUIAS_JARDINAGEM[nome] || Object.values(window.GUIAS_JARDINAGEM)[0];
    if (!g || !window.GarraGuia) return;
    GarraGuia.iniciar(g);
  };
  window.guiaMenu = function (ev) { ev && ev.preventDefault && ev.preventDefault(); guiaAbrir(EH_CAMPO ? 'campo' : 'escritorio'); };
  // ?guia=campo | escritorio — espera a tela terminar de carregar e começa
  (function () {
    const q = window._GUIA_Q; if (!q || !window.GUIAS_JARDINAGEM[q]) return;
    let n = 0; const t = setInterval(() => {
      n++;
      const carregando = (document.getElementById('loadingOverlay') || {}).offsetParent;
      if (!carregando && n > 3) { clearInterval(t); setTimeout(() => guiaAbrir(q), 600); } else if (n > 40) clearInterval(t);
    }, 500);
  })();
})();
