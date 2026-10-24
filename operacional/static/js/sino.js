/* (24/10/2026) Sino de notificações — módulos desktop (Admin, Manutenção, Compras).
 * Uso: <script src="/operacional/static/js/sino.js"></script> e, depois do login,
 *   GarraSino.init({ token: () => '…jwt…', alvo: elementoOuSeletor, antes: true|false });
 * Faz: contador no sino + "(n)" no título da aba, lista com link, marcar lido, e "Receber neste computador"
 * (Web Push pelo mesmo service worker do app). Consulta /api/notificacoes a cada 90 s e ao voltar o foco. */
(function () {
  const CSS = `
  .gs-sino{position:relative;display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:50%;cursor:pointer;color:inherit;font-size:18px;user-select:none}
  .gs-sino:hover{background:rgba(255,255,255,.12)}
  .gs-sino .n{position:absolute;top:1px;right:0;min-width:17px;height:17px;padding:0 4px;border-radius:9px;background:#DC2626;color:#fff;font-size:10px;font-weight:800;line-height:17px;text-align:center;display:none}
  .gs-painel{position:fixed;top:54px;right:12px;width:360px;max-width:calc(100vw - 24px);max-height:70vh;overflow:auto;background:#fff;color:#0F172A;border-radius:12px;box-shadow:0 10px 30px rgba(15,23,42,.25);z-index:9999;display:none;font-family:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;font-size:13px}
  .gs-painel.on{display:block}
  .gs-painel .cab{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #E2E8F0;font-weight:800;color:#1A2A5E}
  .gs-painel .cab button{margin-left:auto;background:none;border:1px solid #CBD5E1;border-radius:6px;padding:4px 8px;font-size:11px;cursor:pointer;color:#475569}
  .gs-painel .it{display:flex;gap:10px;padding:10px 14px;border-bottom:1px solid #F1F5F9;cursor:pointer}
  .gs-painel .it:hover{background:#F8FAFC}
  .gs-painel .it.nl{background:#FFF7ED}
  .gs-painel .it .t{font-weight:800;color:#1A2A5E}
  .gs-painel .it .x{color:#334155;margin-top:2px}
  .gs-painel .it .q{color:#64748B;font-size:11px;margin-top:3px}
  .gs-painel .vazio{padding:24px;text-align:center;color:#64748B}
  .gs-painel .rodape{padding:10px 14px;border-top:1px solid #E2E8F0;display:flex;align-items:center;gap:8px;color:#475569;font-size:12px}
  .gs-painel .rodape label{display:flex;align-items:center;gap:6px;cursor:pointer}`;
  const ICO = { pedido_novo: '📨', pedido_urgente: '🔴', ot_urgente: '🔴', ot_concluida: '✔', abast_divergente: '⛽', preventiva: '🟡', peca_chegou: '📦', oc_aprovar: '🛒', ot_atribuida: '🔧', nota_lancar: '🧾', teste: '🔔' };
  let cfg = null, pend = 0, tituloBase = document.title, timer = null, painel = null, sino = null;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function tit(t) { return String(t || '').replace(/^[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B50}\u{2705}\u{274C}\u{FE0F}]+\s*/u, ''); }
  function quando(iso) { if (!iso) return ''; const d = new Date(iso), m = (Date.now() - d.getTime()) / 60000; if (m < 1) return 'agora'; if (m < 60) return Math.round(m) + ' min'; if (m < 1440) return Math.round(m / 60) + ' h'; return d.toLocaleDateString('pt-BR'); }
  async function api(path, opts) {
    const t = cfg.token(); if (!t) throw new Error('sem token');
    opts = opts || {}; opts.headers = Object.assign({ 'Content-Type': 'application/json', Authorization: 'Bearer ' + t }, opts.headers || {});
    const r = await fetch(path, opts); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || ('Erro ' + r.status)); return j;
  }
  function pintar(n) {
    pend = n || 0;
    const b = sino.querySelector('.n'); b.textContent = pend > 9 ? '9+' : pend; b.style.display = pend > 0 ? 'block' : 'none';
    document.title = (pend > 0 ? '(' + pend + ') ' : '') + tituloBase;
  }
  async function atualizar(abrirLista) {
    try {
      const d = await api('/api/notificacoes?limite=' + (abrirLista ? 40 : 1));
      pintar(d.pendentes);
      if (abrirLista) render(d.itens || []);
    } catch (e) { /* sem rede ou sem token: fica como está */ }
  }
  function render(itens) {
    const lista = painel.querySelector('.lista');
    if (!itens.length) { lista.innerHTML = '<div class="vazio">Nenhum aviso para você.</div>'; return; }
    lista.innerHTML = itens.map(a => `<div class="it ${a.lida ? '' : 'nl'}" data-id="${a.id}" data-link="${esc(a.link || '')}"><span style="font-size:18px">${ICO[a.tipo] || '🔔'}</span><div style="flex:1;min-width:0"><div class="t">${esc(tit(a.titulo))}</div>${a.texto ? '<div class="x">' + esc(a.texto) + '</div>' : ''}<div class="q">${quando(a.criado_em)}</div></div></div>`).join('');
    lista.querySelectorAll('.it').forEach(el => el.onclick = () => abrir(el.dataset.id, el.dataset.link));
  }
  async function abrir(id, link) {
    try { const d = await api('/api/notificacoes/lidas', { method: 'POST', body: JSON.stringify({ ids: [id] }) }); pintar(d.pendentes); } catch (e) { }
    painel.classList.remove('on');
    if (link) { if (cfg.abrir && cfg.abrir(link) === true) return; window.open(link, '_blank'); }
  }
  async function lerTodas() { try { const d = await api('/api/notificacoes/lidas', { method: 'POST', body: '{}' }); pintar(d.pendentes); atualizar(true); } catch (e) { } }

  // ── push neste computador (mesmo SW do app, escopo /) ──
  function b64(b) { const p = '='.repeat((4 - b.length % 4) % 4); const s = (b + p).replace(/-/g, '+').replace(/_/g, '/'); const raw = atob(s); return Uint8Array.from([].map.call(raw, c => c.charCodeAt(0))); }
  function suportado() { return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window; }
  async function reg() { return navigator.serviceWorker.register('/operacional/static/sw.js', { scope: '/' }).then(() => navigator.serviceWorker.ready); }
  async function estadoPush() { if (!suportado()) return 'nao'; if (Notification.permission === 'denied') return 'negado'; try { const r = await navigator.serviceWorker.getRegistration('/'); const s = r && await r.pushManager.getSubscription(); return s ? 'ativo' : 'inativo'; } catch (e) { return 'inativo'; } }
  async function ligarPush(on) {
    const st = painel.querySelector('.push-st');
    try {
      if (on) {
        if (!suportado()) { st.textContent = 'navegador sem suporte'; return; }
        const perm = await Notification.requestPermission(); if (perm !== 'granted') { st.textContent = 'permissão negada'; return; }
        const v = await api('/api/notificacoes/vapid'); if (!v.publica) { st.textContent = 'push indisponível'; return; }
        const r = await reg(); let s = await r.pushManager.getSubscription();
        if (!s) s = await r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(v.publica) });
        const j = s.toJSON();
        await api('/api/notificacoes/assinar', { method: 'POST', body: JSON.stringify({ endpoint: j.endpoint, keys: j.keys, aparelho: 'desktop · ' + navigator.userAgent.slice(0, 160) }) });
        st.textContent = 'ativo neste computador';
      } else {
        const r = await navigator.serviceWorker.getRegistration('/'); const s = r && await r.pushManager.getSubscription();
        if (s) { await api('/api/notificacoes/assinar', { method: 'DELETE', body: JSON.stringify({ endpoint: s.endpoint }) }); await s.unsubscribe(); }
        st.textContent = 'desativado';
      }
    } catch (e) { st.textContent = 'falhou: ' + e.message; painel.querySelector('.push-cb').checked = !on; }
  }
  async function montarRodape() {
    const cb = painel.querySelector('.push-cb'), st = painel.querySelector('.push-st');
    const e = await estadoPush();
    cb.checked = e === 'ativo'; cb.disabled = e === 'nao' || e === 'negado';
    st.textContent = { ativo: 'ativo neste computador', inativo: 'desativado', negado: 'bloqueado no navegador', nao: 'navegador sem suporte' }[e];
  }

  function init(c) {
    cfg = c; tituloBase = document.title;
    if (!document.getElementById('gs-sino-css')) { const st = document.createElement('style'); st.id = 'gs-sino-css'; st.textContent = CSS; document.head.appendChild(st); }
    const alvo = typeof c.alvo === 'string' ? document.querySelector(c.alvo) : c.alvo; if (!alvo) return;
    sino = document.createElement('span'); sino.className = 'gs-sino'; sino.title = 'Avisos'; sino.innerHTML = '🔔<span class="n"></span>';
    c.antes ? alvo.parentNode.insertBefore(sino, alvo) : alvo.appendChild(sino);
    painel = document.createElement('div'); painel.className = 'gs-painel';
    painel.innerHTML = `<div class="cab">🔔 Avisos <button class="todas">marcar todos lidos</button></div><div class="lista"><div class="vazio">…</div></div>
      <div class="rodape"><label><input type="checkbox" class="push-cb"> Receber neste computador</label><span class="push-st" style="margin-left:auto"></span></div>`;
    document.body.appendChild(painel);
    painel.querySelector('.todas').onclick = lerTodas;
    painel.querySelector('.push-cb').onchange = e => ligarPush(e.target.checked);
    sino.onclick = e => { e.stopPropagation(); const on = painel.classList.toggle('on'); if (on) { atualizar(true); montarRodape(); } };
    document.addEventListener('click', e => { if (painel.classList.contains('on') && !painel.contains(e.target)) painel.classList.remove('on'); });
    atualizar(false);
    clearInterval(timer); timer = setInterval(() => atualizar(painel.classList.contains('on')), 90000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) atualizar(painel.classList.contains('on')); });
  }
  window.GarraSino = { init, atualizar: () => atualizar(painel && painel.classList.contains('on')) };
})();
