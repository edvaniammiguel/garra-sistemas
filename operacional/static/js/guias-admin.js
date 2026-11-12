/* (05/11/2026) Tour guiado da tela Admin Master (desktop, /admin) — GarraGuia. Dois roteiros:
 *   usuarios — cadastrar colaborador (login, senha inicial/reset, perfil, desativar/reativar) e Permissões
 *              (Por Perfil × Exceções Individuais, o que cada módulo libera, regra módulo da tela × módulo da rota).
 *   painel   — o que mais o Admin oferece: módulos, OS e partes diárias, Controle Mensal, Agenda, Cadastros, Mural.
 * Modo demonstração: mostra a tela real, não grava (alvos bloqueados ao clique; formulários limpos ao sair).
 * Abre pelo botão ▶ Como fazer (cabeçalho) ou por /admin?guia=usuarios | /admin?guia=painel. */
(function () {
  const dorme = (ms) => new Promise(r => setTimeout(r, ms));
  // item do menu lateral pelo texto (o menu é montado pelo JS após o login)
  const sbItem = (re) => [...document.querySelectorAll('#sidebar .sb-item')].find(e => re.test(e.textContent));
  const ir = async (sec, re) => { navTo(sec, re ? sbItem(re) : null); await dorme(600); };
  const set = (id, v) => { const e = document.getElementById(id); if (e) e.value = v; };
  // linha de módulo na ficha de exceções (Permissões › Exceções Individuais › colaborador)
  const linhasModulo = () => [...document.querySelectorAll('#perm-detail-content > div[style*="flex-direction:column"] > div')];
  const linhaModulo = (re) => linhasModulo().find(d => re.test(d.textContent));
  // colaborador para a demonstração de exceções: um operador, senão o primeiro que não for admin
  const colabDemo = () => { const l = (typeof _usuarios !== 'undefined' ? _usuarios : []).filter(u => u.ativo && u.perfil !== 'admin'); return l.find(u => u.perfil === 'operador') || l[0]; };

  window.GUIAS_ADMIN = {

    usuarios: {
      titulo: 'Usuários e Permissões',
      pop: 'Admin · Usuários',
      soAdmin: true,
      passos: [
        { titulo: 'Quem entra no sistema, e no que entra 🔑',
          texto: '<b>Bem-vinda à demonstração de Usuários e Permissões.</b><br><br>Você vai ver como cadastrar um colaborador (login, senha inicial, perfil), como desativar e reativar, e como as <b>Permissões</b> decidem o que cada módulo libera — por perfil e por pessoa.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: () => sbItem(/Usuários/), antes: () => ir('usuarios', /Usuários/),
          titulo: 'Usuários — só o Administrador vê',
          texto: 'A guia <b>Usuários</b> (e a <b>Permissões</b>) aparece só para o perfil Administrador. Gestor e Comercial entram no Admin, mas não veem estas duas. Aqui está todo mundo que faz login: app do celular, checklist, manutenção, compras.' },
        { alvo: '#users-table',
          titulo: 'A lista de colaboradores',
          texto: 'Cada linha: <b>nome</b>, <b>login</b> (o que a pessoa digita no app), <b>perfil</b> (o que ela enxerga), pontos e status. Busque por nome ou login na barra de cima; o filtro abre em <b>● Ativos</b>.' },
        { alvo: () => document.querySelector('#sec-usuarios .card-title button'),
          titulo: '+ Novo colaborador',
          texto: 'Cadastre <b>antes</b> da pessoa começar: sem login ela não registra abastecimento, checklist nem parte diária — e o custo do equipamento fica sem dono. Vamos abrir o formulário.' },
        { alvo: () => document.getElementById('m-nome').closest('.field-row'),
          antes: async () => { abrirModalUsuario(); await dorme(300); set('m-nome', 'Zeca Operador'); set('m-login', 'zeca.operador'); },
          titulo: 'Nome e login',
          texto: '<b>Nome completo</b> como deve sair nos relatórios. <b>Login</b> curto, minúsculo, sem espaço — padrão <b>nome.sobrenome</b>. Depois de criado o login <b>não muda</b> (fica travado na edição); nome, e-mail e perfil mudam quando quiser.' },
        { alvo: '#m-senha', antes: () => set('m-senha', 'garra2026'),
          titulo: 'Senha inicial — e o reset',
          texto: 'Mínimo de <b>6 caracteres</b>. Passe a senha para a pessoa e peça para trocar em <b>Minha conta</b> (avatar). <b>Esqueceu a senha?</b> Abra ✎ Editar, digite uma nova aqui e salve — é o reset. Na edição, em branco = mantém a atual.' },
        { alvo: '#m-perfil', antes: () => set('m-perfil', 'operador'),
          titulo: 'Perfil de acesso',
          texto: 'O perfil dá o <b>padrão de acesso</b>: <b>Operador/Motorista</b> = app do celular (OS e horímetro, Checklist, Abastecimento); <b>Mecânica</b> = manutenção e OTs; <b>Gestor</b> = painel de gestão + manutenção; <b>Comercial</b> = painel de gestão e notas; <b>Administrador</b> = tudo (Master). O <b>Perfil Checklist</b> ao lado só muda o papel dentro do checklist — normalmente fica "Mesmo perfil principal".' },
        { alvo: '#btn-salvar-user',
          titulo: 'Salvar',
          texto: 'Salvar cria o colaborador e <b>aplica as permissões padrão do perfil</b>. Ele já aparece nos selects de operador das OS e pode entrar no app na mesma hora. Na demonstração não salvamos.' },
        { alvo: () => document.querySelector('#users-table .btn-sm.danger') || document.querySelector('#users-table .btn-sm'),
          antes: async () => { fecharModalUsuario(); await dorme(200); },
          titulo: '✕ Desativar · ↺ Reativar',
          texto: 'Saiu da empresa? <b>Desativar</b>: perde o acesso na hora, mas o histórico (partes, abastecimentos, checklists) fica. Para voltar, filtre <b>○ Inativos</b> e use <b>↺ Reativar</b>. Nunca apague — desativar basta.' },
        { alvo: () => sbItem(/Permissões/), antes: () => ir('permissoes', /Permissões/),
          titulo: 'Permissões',
          texto: 'Aqui se decide o que cada <b>módulo</b> libera. Dois níveis: <b>Por Perfil</b> (padrão de todos do perfil) e <b>Exceções Individuais</b> (uma pessoa). Não é só o botão sumir da tela: o <b>servidor</b> checa o módulo em cada gravação.' },
        { alvo: '#perm-perfis-content', antes: async () => { switchPermTab('perfis'); await dorme(400); },
          titulo: 'Por Perfil — matriz perfil × módulo',
          texto: 'Marcar aqui vale <b>para todos do perfil</b>, na hora. <b>Administrador</b> é Master: tudo marcado e travado. Leitura dos módulos: <b>Painel de Gestão</b> = OS, Controle Mensal, Agenda e Cadastros deste Admin; <b>Manutenção Desktop</b> = módulo completo da oficina; <b>Pedir OT</b> × <b>Executar OT</b> separa quem pede de quem executa; <b>Abastecimento (mobile)</b> registra; <b>Notas de Abastecimento / de Manutenção</b> só conferem as notinhas; <b>Compras</b> tem 4 níveis. <b>+ Novo Perfil</b> cria um perfil só com os módulos que você marcar.' },
        { alvo: () => document.querySelector('#perm-excecoes-content > div > div'),
          antes: async () => { switchPermTab('excecoes'); await dorme(500); },
          titulo: 'Exceções Individuais',
          texto: 'Clique no colaborador. Serve para <b>liberar algo a mais</b> (um operador que também vai conferir abastecimentos) ou <b>bloquear algo do perfil</b> só para ele. O filtro de perfil acha a pessoa mais rápido.' },
        { alvo: () => linhaModulo(/Operacional Mobile/) || linhasModulo()[0],
          antes: async () => { const u = colabDemo(); if (u) { await abrirPermColaborador(u.id, u.perfil); await dorme(500); } },
          titulo: 'Liberado × Bloqueado',
          texto: 'Cada módulo mostra <b>Liberado</b> ou <b>Bloqueado</b>. <b>↑ herdado</b> = veio do perfil; <b>✎ exceção</b> = ajuste só desta pessoa, com <b>↺</b> para voltar ao padrão. "Resetar todas" desfaz as exceções de uma vez. Fundo verde = tem acesso.' },
        { alvo: () => linhaModulo(/Painel de Gestão/) || linhaModulo(/Manutenção Desktop/) || linhasModulo()[0],
          titulo: 'Regra: módulo da tela × módulo da rota',
          texto: 'A tela aparece pelo módulo — e o servidor <b>exige o mesmo módulo na gravação</b>, por URL direta inclusive. Exemplos: <b>cadastrar equipamento</b> (aqui ou na Manutenção) exige <b>Painel de Gestão</b> ou <b>Manutenção Desktop</b>; OS, Controle Mensal e Cadastros exigem <b>Painel de Gestão</b>; aprovar OC exige <b>Compras — Aprovar</b>. Alguém recebeu "Sem permissão"? É aqui que se resolve.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina: <b>Usuários › + Novo colaborador → nome, login, senha inicial, perfil → Salvar</b>. Reset de senha pelo ✎ Editar. Saiu: <b>Desativar</b> (nunca apagar). Acesso diferente do padrão: <b>Permissões › Exceções Individuais</b>. Referência: descrição de cada módulo na própria tela de Permissões. Repita este tour pelo ▶ Como fazer.' },
      ],
    },

    painel: {
      titulo: 'Painel do Admin — OS, Controle Mensal, Cadastros',
      pop: 'Admin · Painel de Gestão',
      passos: [
        { titulo: 'O que o Admin faz pela gestão 🗂️',
          texto: '<b>Bem-vinda à demonstração do Painel do Admin.</b><br><br>Você vai ver os módulos do sistema, as <b>Ordens de Serviço</b> e as partes diárias, o <b>Controle Mensal</b>, a <b>Agenda</b>, os <b>Cadastros</b> (equipamentos, clientes, regimes) e o <b>Mural</b>.<br><br>Nada é gravado durante a demonstração.',
          botao: 'Começar demonstração' },
        { alvo: '#dash-modulos', antes: () => ir('dashboard', /Dashboard/),
          titulo: 'Módulos do sistema',
          texto: 'Cada cartão abre um módulo <b>com o seu login</b>, em nova aba — sem entrar de novo. Etiqueta <b>Produção</b>, <b>Em teste</b> ou <b>Planejado</b>. O Administrador vê todos; os demais só o que o perfil libera (cartão 🔒 "sem permissão" aparece só para o admin).' },
        { alvo: () => document.querySelectorAll('#sidebar .sb-section')[1] || document.getElementById('sidebar'),
          titulo: 'Gestão Administrativa',
          texto: 'O menu: <b>Ordens de Serviço</b>, <b>Controle Mensal</b>, <b>Agenda</b>, e os atalhos <b>Compras (OC)</b>, <b>Atendimento WhatsApp</b> e <b>Notas a lançar</b>, que abrem a tela própria em nova aba. Esta seção é o módulo <b>Painel de Gestão</b> da tela de Permissões.' },
        { alvo: '#os-table', antes: () => ir('os', /Ordens de Serviço/),
          titulo: 'Ordens de Serviço',
          texto: 'A OS é a <b>obra/contrato</b> que a máquina está atendendo. <b>Cód interno</b> automático + <b>OS do cliente</b> (número do ERP MAIS, pode vir depois). Status: aberta (sem/com cód ERP), em execução, <b>aguardando fechamento</b>, concluída. Origem <b>📍 Campo</b> = o operador abriu pelo app e você só completa.' },
        { alvo: () => document.querySelector('#sec-os .card-title button'),
          titulo: '+ Nova OS',
          texto: 'Nova OS: cliente (cadastrado ou avulso), obra, equipamento, operador responsável, <b>regime de cobrança</b> e data de início. OS sem responsável vira <b>⚠ vermelho</b> no badge do menu — é a primeira coisa a resolver.' },
        { alvo: '#partes-totais',
          antes: async () => { const l = (typeof _osListaCompleta !== 'undefined' ? _osListaCompleta : []); const o = l.find(x => x.status === 'aguardando_fechamento') || l[0]; if (o) { await verPartesOS(o.id, o.numero); await dorme(700); } },
          titulo: 'Partes diárias da OS',
          texto: 'Clicou na OS: cada linha é um dia que o operador apontou pelo app — <b>horímetro, hora, km, diária ou metros</b>, conforme a medição. Os totais ficam aqui em cima; ⚠ marca divergência de horas paradas para você olhar.' },
        { alvo: () => document.getElementById('btn-fechar-os').parentElement,
          titulo: 'Fechar, lote, medição',
          texto: '<b>+ Adicionar registro</b> lança um dia pelo computador. <b>📅 Diárias em lote</b> cria 1 diária por dia (locação seca, sem duplicar). <b>📤 Enviar medição</b> gera a imagem para o cliente no WhatsApp e registra o envio. <b>✓ Fechar OS</b> congela as partes e manda para o faturamento; fechou errado, <b>↺ Reabrir</b>.' },
        { alvo: () => document.getElementById('cm-exportar').parentElement, antes: async () => { await ir('controle-mensal', /Controle Mensal/); try { cmTab('equipamento'); } catch (e) { } await dorme(800); },
          titulo: 'Controle Mensal',
          texto: 'O mês inteiro agrupado por <b>Equipamento</b>, <b>Colaborador</b>, Cód interno ou OS (ERP). Dia em que a máquina não rodou pede <b>motivo</b> (parada). <b>Excel mês</b>, <b>Excel ano</b> e <b>Imprimir</b> — é daqui que sai o fechamento para o financeiro.' },
        { alvo: '#btn-sync-agenda', antes: () => ir('agenda', /Agenda/),
          titulo: 'Agenda — a confirmar',
          texto: 'Eventos das <b>agendas Google dos equipamentos</b> (URL ICS no cadastro). Sincronizar traz os eventos; <b>nada vira OS sem a sua confirmação</b>; evento longo = 1 OS por mês.' },
        { alvo: '#eq-lista', antes: () => ir('equipamentos', /Equipamentos/),
          titulo: 'Cadastro de Equipamentos',
          texto: 'Um cadastro só para tudo: OS, checklist (frota), abastecimento e manutenção. <b>Código</b> (EH-039, CB-05), categoria e medição. Cadastrar exige o módulo <b>Painel de Gestão</b> ou <b>Manutenção Desktop</b>.' },
        { alvo: '#eq-responsavel', antes: async () => { abrirModalEquipamento(); await dorme(400); },
          titulo: 'Medição, placa e responsável',
          texto: '<b>Medição</b> define como o operador aponta: horímetro (máquinas), KM/viagem (caminhões), diária (locação seca), metros. <b>Placa</b> bate com a nota de abastecimento. <b>Operador responsável</b> pré-preenche o app. <b>URL ICS</b> liga a agenda Google do equipamento.' },
        { alvo: '#reg-lista', antes: async () => { if (typeof fecharModalEq === 'function') fecharModalEq(); await ir('regimes', /Regimes/); },
          titulo: 'Regimes, Tipos de Serviço e Clientes',
          texto: '<b>Regimes de Cobrança</b> (hora, diária, km, empreito): o regime da OS <b>sugere a medição</b> da parte e manda no fechamento. <b>Tipos de Serviço</b> classificam o trabalho. <b>Clientes</b>: nome, CNPJ/CPF e contato — OS com cliente cadastrado entra no relatório por cliente.' },
        { alvo: () => document.querySelector('#sec-mural .card-title button'), antes: () => ir('mural', /Mural/),
          titulo: 'Mural e Manual (só Administrador)',
          texto: '<b>Mural de Avisos</b>: publica para todos, por perfil ou para uma pessoa — aparece no sino/avatar de cada colaborador, no app e no desktop. <b>Manual do Colaborador</b>: blocos de texto que todos leem pelo avatar; revise ao menos uma vez por ano. Cada bloco pode ter versão em <b>espanhol e inglês</b> (abas ao editar) — o colaborador escolhe o idioma no topo do manual.' },
        { titulo: 'Pronto! ✅',
          texto: 'Rotina da gestão: <b>OS</b> (resolver ⚠ sem responsável e aguardando fechamento) → <b>Partes diárias</b> → <b>Fechar OS</b> → <b>Controle Mensal</b> → Excel para o financeiro. Cadastros sempre em dia: equipamento novo entra aqui antes de rodar. Repita este tour pelo ▶ Como fazer.' },
      ],
    },
  };

  function limpar() {
    try { fecharModalUsuario(); } catch (e) { }
    try { ['m-nome', 'm-login', 'm-email', 'm-senha'].forEach(id => set(id, '')); } catch (e) { }
    try { if (document.getElementById('modal-eq')) fecharModalEq(); } catch (e) { }
    try { const d = document.getElementById('perm-detail-content'); if (d) d.innerHTML = ''; } catch (e) { }
    try { if (!document.getElementById('sec-permissoes').classList.contains('hidden')) switchPermTab('perfis'); } catch (e) { }
    try { if (!document.getElementById('sec-partes').classList.contains('hidden')) navTo('os', sbItem(/Ordens de Serviço/)); } catch (e) { }
  }

  window.guiaAbrir = function (nome) {
    const g = window.GUIAS_ADMIN[nome]; if (!g || !window.GarraGuia) return;
    if (g.soAdmin && window._PERFIL !== 'admin') return;   // Usuários/Permissões só existem para o Administrador
    g.aoSair = limpar;
    GarraGuia.iniciar(g);
  };
  window.guiaMenu = function (ev) {
    ev && ev.stopPropagation();
    let m = document.getElementById('guia-menu');
    if (m) { m.remove(); return; }
    m = document.createElement('div'); m.id = 'guia-menu';
    m.style.cssText = 'position:fixed;top:60px;right:120px;z-index:9999;background:#fff;color:#0F172A;border-radius:10px;box-shadow:0 10px 30px rgba(15,23,42,.25);padding:6px;min-width:300px;font-size:13px';
    m.innerHTML = '<div style="padding:6px 10px;font-weight:800;color:#1A2A5E">▶ Como fazer — demonstrações</div>'
      + Object.entries(window.GUIAS_ADMIN).filter(([k, g]) => !g.soAdmin || window._PERFIL === 'admin').map(([k, g]) => '<div style="padding:8px 10px;cursor:pointer;border-radius:6px" onmouseover="this.style.background=\'#F1F5F9\'" onmouseout="this.style.background=\'\'" onclick="document.getElementById(\'guia-menu\').remove();guiaAbrir(\'' + k + '\')">' + g.titulo + ' <span style="color:#94A3B8;font-size:11px">· ' + (g.passos.length - 2) + ' passos</span></div>').join('');
    document.body.appendChild(m);
    setTimeout(() => document.addEventListener('click', function f() { const x = document.getElementById('guia-menu'); if (x) x.remove(); document.removeEventListener('click', f); }), 0);
  };
  // ?guia=usuarios | ?guia=painel — espera o app aparecer (login feito e menu lateral montado) e começa
  (function () {
    const q = window._GUIA_Q; if (!q) return;
    let n = 0; const t = setInterval(() => {
      const app = document.getElementById('app'); n++;
      if (app && !app.classList.contains('hidden') && document.querySelector('#sidebar .sb-item')) { clearInterval(t); setTimeout(() => guiaAbrir(q), 800); }
      else if (n > 120) clearInterval(t);
    }, 500);
  })();
})();
