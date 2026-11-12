/* ═══════════════════════════════════════════════════════════════════════════
   GarraCartilha — leitor do Manual do Colaborador (11/11/2026)
   Um só leitor para o app operacional, jardinagem e admin (antes eram 3 cópias).
   Novidade: o colaborador escolhe o idioma (PT · ES · EN) no topo do manual;
   a escolha fica no aparelho e no cadastro dele (segue em qualquer celular).
   Bloco sem tradução aparece em português — nunca fica vazio.

   Uso: GarraCartilha.abrir({ token, aoFechar, cores:{azul,laranja,texto,mudo} })
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  const CHAVE = 'garra_idioma_manual';
  const IDIOMAS = ['pt', 'es', 'en'];
  const T = {
    pt: { titulo: '📖 Manual do Colaborador', carregando: 'Carregando...', vazio: 'Nenhum conteúdo publicado ainda.',
          erro: 'Erro ao carregar o manual.', rodape: 'Garra Terraplenagem e Caçambas', semTrad: 'Este trecho ainda não foi traduzido — exibido em português.',
          dica: 'Prefere ler em outro idioma? Toque em ES ou EN.' },
    es: { titulo: '📖 Manual del Colaborador', carregando: 'Cargando...', vazio: 'Aún no hay contenido publicado.',
          erro: 'Error al cargar el manual.', rodape: 'Garra Terraplenagem e Caçambas', semTrad: 'Este tramo aún no fue traducido — se muestra en portugués.',
          dica: '' },
    en: { titulo: '📖 Employee Handbook', carregando: 'Loading...', vazio: 'No content published yet.',
          erro: 'Could not load the handbook.', rodape: 'Garra Terraplenagem e Caçambas', semTrad: 'This section has not been translated yet — shown in Portuguese.',
          dica: '' },
  };

  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
  function negrito(s, cor) { return esc(s).replace(/\*\*(.+?)\*\*/g, '<strong style="color:' + cor + '">$1</strong>'); }

  function renderConteudo(texto, cores) {
    const linhas = String(texto || '').split('\n');
    let html = '', listaAberta = false, par = [];
    const fecharPar = () => { if (par.length) { html += '<p style="margin:0 0 12px 0">' + par.join('<br>') + '</p>'; par = []; } };
    const fecharLista = () => { if (listaAberta) { html += '</ul>'; listaAberta = false; } };
    linhas.forEach(l => {
      const t = l.trim();
      if (t === '') { fecharPar(); fecharLista(); return; }
      if (t.startsWith('- ')) {
        fecharPar();
        if (!listaAberta) { html += '<ul style="margin:0 0 12px 0;padding-left:20px">'; listaAberta = true; }
        html += '<li style="margin-bottom:4px">' + negrito(t.slice(2), cores.azul) + '</li>';
        return;
      }
      fecharLista();
      par.push(negrito(t, cores.azul));
    });
    fecharPar(); fecharLista();
    return html;
  }

  function idiomaLocal() { try { const v = localStorage.getItem(CHAVE); return IDIOMAS.includes(v) ? v : null; } catch (e) { return null; } }
  function gravarLocal(i) { try { localStorage.setItem(CHAVE, i); } catch (e) { } }

  async function idiomaDoCadastro(token) {
    try {
      const r = await fetch('/api/cartilha/idioma', { headers: { 'Authorization': 'Bearer ' + token } });
      if (!r.ok) return null;
      const j = await r.json();
      return IDIOMAS.includes(j.idioma) ? j.idioma : null;
    } catch (e) { return null; }
  }
  function gravarNoCadastro(token, idioma) {
    try { fetch('/api/cartilha/idioma', { method: 'PUT', headers: { 'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify({ idioma }) }).catch(() => { }); } catch (e) { }
  }

  async function abrir(op) {
    op = op || {};
    const token = op.token || localStorage.getItem('garra_token') || '';
    const cores = Object.assign({ azul: 'var(--blue)', laranja: 'var(--orange)', texto: 'var(--text)', mudo: 'var(--muted)' }, op.cores || {});
    const ID = 'modal-cartilha-g';
    const velho = document.getElementById(ID); if (velho) velho.remove();

    let idioma = idiomaLocal() || 'pt';
    const m = document.createElement('div');
    m.id = ID;
    m.style.cssText = 'position:fixed;inset:0;background:#fff;z-index:500;display:flex;flex-direction:column';
    m.innerHTML =
      '<div style="background:' + cores.azul + ';border-bottom:3px solid ' + cores.laranja + ';padding:10px 12px 10px 16px;display:flex;align-items:center;justify-content:space-between;gap:8px;flex-shrink:0">'
      + '<div id="cart-g-titulo" style="color:#fff;font-size:15px;font-weight:800;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div>'
      + '<div id="cart-g-idiomas" style="display:flex;gap:4px;flex-shrink:0;background:rgba(255,255,255,.12);border-radius:8px;padding:3px"></div>'
      + '<button id="cart-g-fechar" style="background:none;border:none;color:#fff;font-size:22px;cursor:pointer;padding:0 4px;flex-shrink:0">✕</button></div>'
      + '<div id="cart-g-dica" style="display:none;background:#FFF7ED;color:#9A3412;font-size:12px;padding:8px 16px;border-bottom:1px solid #FED7AA"></div>'
      + '<div id="cart-g-body" style="flex:1;overflow-y:auto;padding:20px 16px;font-size:13px;color:' + cores.texto + ';line-height:1.6"></div>';
    document.body.appendChild(m);
    document.getElementById('cart-g-fechar').onclick = () => { m.remove(); if (typeof op.aoFechar === 'function') op.aoFechar(); };

    const botoes = document.getElementById('cart-g-idiomas');
    function pintarBotoes() {
      botoes.innerHTML = IDIOMAS.map(i =>
        '<button data-i="' + i + '" style="border:none;border-radius:6px;padding:4px 9px;font-size:12px;font-weight:800;cursor:pointer;letter-spacing:.5px;'
        + (i === idioma ? 'background:#fff;color:' + cores.azul : 'background:transparent;color:rgba(255,255,255,.85)') + '">' + i.toUpperCase() + '</button>').join('');
      botoes.querySelectorAll('button').forEach(b => b.onclick = () => trocar(b.dataset.i, true));
    }

    async function carregar() {
      const t = T[idioma];
      document.getElementById('cart-g-titulo').textContent = t.titulo;
      const dica = document.getElementById('cart-g-dica');
      const dicaVista = (() => { try { return localStorage.getItem(CHAVE + '_dica') === '1'; } catch (e) { return false; } })();
      dica.style.display = (idioma === 'pt' && !dicaVista && !idiomaLocal()) ? 'block' : 'none';
      dica.textContent = t.dica;
      const body = document.getElementById('cart-g-body');
      body.innerHTML = '<div style="text-align:center;color:' + cores.mudo + ';padding:40px 0">' + t.carregando + '</div>';
      try {
        const r = await fetch('/api/cartilha?idioma=' + idioma, { headers: { 'Authorization': 'Bearer ' + token } });
        if (!r.ok) throw new Error(r.status);
        const blocos = await r.json();
        if (!blocos.length) { body.innerHTML = '<div style="text-align:center;color:' + cores.mudo + ';padding:40px 0">' + t.vazio + '</div>'; return; }
        body.innerHTML = blocos.map(b =>
          '<div style="margin-top:8px;margin-bottom:12px;border-bottom:2px solid ' + cores.laranja + ';padding-bottom:6px">'
          + '<div style="font-size:16px;font-weight:800;color:' + cores.azul + '">' + esc(b.titulo) + '</div>'
          + (b.subtitulo ? '<div style="font-size:11px;color:' + cores.mudo + '">' + esc(b.subtitulo) + '</div>' : '')
          + '</div>'
          + (b.traduzido === false ? '<div style="font-size:11px;color:' + cores.mudo + ';font-style:italic;margin-bottom:8px">' + t.semTrad + '</div>' : '')
          + renderConteudo(b.conteudo, cores)
        ).join('') + '<div style="text-align:center;padding:20px 0;font-size:11px;color:' + cores.mudo + '">' + t.rodape + '</div>';
        body.scrollTop = 0;
      } catch (e) {
        body.innerHTML = '<div style="text-align:center;color:' + cores.mudo + ';padding:40px 0">' + t.erro + '</div>';
      }
    }

    function trocar(novo, pelaMao) {
      if (!IDIOMAS.includes(novo) || novo === idioma) return;
      idioma = novo;
      if (pelaMao) { gravarLocal(novo); gravarNoCadastro(token, novo); try { localStorage.setItem(CHAVE + '_dica', '1'); } catch (e) { } }
      pintarBotoes(); carregar();
    }

    pintarBotoes();
    carregar();
    // Sem escolha neste aparelho? Usa a do cadastro (escolhida em outro celular).
    if (!idiomaLocal()) {
      idiomaDoCadastro(token).then(i => { if (i && i !== idioma && document.getElementById(ID)) { gravarLocal(i); trocar(i, false); } });
    }
  }

  window.GarraCartilha = { abrir, renderConteudo, IDIOMAS, textos: T };
})();
