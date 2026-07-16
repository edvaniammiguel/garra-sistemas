// ── TOKEN DE SESSÃO DO CHECKLIST (06/07/2026) ──
// localStorage é por ORIGEM: um login no /mobile sobrescrevia garra_token e a
// aba do gestor passava a agir com o token do operador (403 fantasma).
// O SSO da URL vira sessão própria desta aba/app.
(function(){
  try {
    const sso = new URLSearchParams(location.search).get('sso');
    if (sso && sso.length > 10) sessionStorage.setItem('garra_ck_token', sso);
  } catch(e) {}
})();
function ckToken() {
  // (09/07/2026) FIX CRÍTICO: a versão anterior chamava a si mesma no fallback
  // (recursão infinita → stack overflow sempre que o sessionStorage estava
  // vazio). Fallback correto: token persistente do mobile na mesma origem.
  return sessionStorage.getItem('garra_ck_token') || localStorage.getItem('garra_token') || '';
}

/* ═══════════════════════════════════════════════════
   app.js — Garra Check List System v3
   Garra Terraplenagem e Caçambas
   Offline-first PWA | PostgreSQL via API
═══════════════════════════════════════════════════ */

// ─── API FETCH COM TOKEN ───────────────────────────
async function apiFetch(url, options = {}) {
  const token = ckToken();
  const headers = {
    'Authorization': 'Bearer ' + token,
    ...(options.headers || {})
  };
  if (options.body && typeof options.body === 'string') {
    headers['Content-Type'] = 'application/json';
  }
  const r = await fetch(url, { ...options, headers });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.detail || `Erro HTTP ${r.status}`);
  }
  const text = await r.text();
  try { return JSON.parse(text); } catch(e) { return text; }
}

// ─── ESTADO GLOBAL ─────────────────────────────────
let currentUser  = null;

// (07/07/2026) GUARD DE IDENTIDADE: o checklist roda dentro do /mobile.
// Se o usuário do shell (token) mudou, o currentUser cacheado do usuário
// anterior é descartado — mata a personificação (ex.: UI de gestor do
// admin aparecendo para o Gilson na mesma máquina).
(function(){
  // (07/07/2026 v2) IDENTIDADE ÚNICA: quando há token (sso do gestor ou
  // shell /mobile), o checklist ADOTA o usuário do token — papel vindo do
  // servidor (perfil_checklist). Sem segundo login, sem personificação.
  try {
    const tok = sessionStorage.getItem('garra_ck_token') || localStorage.getItem('garra_token') || '';
    if (!tok) return;
    const pl = JSON.parse(atob(tok.split('.')[1].replace(/-/g,'+').replace(/_/g,'/')));
    if (!pl.login) return;
    let role = pl.perfil_checklist || '';
    if (!role) role = (pl.perfil === 'admin' || pl.perfil === 'gestor') ? 'manager' : 'driver';
    if (role === 'admin' || role === 'gestor') role = 'manager';
    const cached = JSON.parse(localStorage.getItem('garra_current_user') || 'null');
    const adotado = {
      login: pl.login, name: pl.nome || pl.login, role: role,
      pts: (cached && cached.login === pl.login ? cached.pts : 0) || 0,
      submissions: (cached && cached.login === pl.login ? cached.submissions : 0) || 0,
    };
    localStorage.setItem('garra_current_user', JSON.stringify(adotado));
    if (cached && cached.login !== pl.login)
      console.warn('[Identidade] Shell = ' + pl.login + ' (' + role + ') — usuário anterior descartado');
  } catch(e) {}
})();
let currentCLId  = null;
let currentStep  = 0;
let formAnswers  = {};
let formMeta     = {};
let editingUserLogin      = null;
let pendingRemoveFleetKey = null;
let pendingRemoveUserLogin= null;
let pendingRemoveCLId     = null;
let funcaoEditId = null;
let funcaoCorSel = 'navy';
let funcaoClsSel = [];
let builderQuestions = [];
let builderEditId    = null;
let builderFocusId   = null;

// ─── STORAGE LOCAL ─────────────────────────────────
const DB = {
  get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  // Grava com proteção contra estouro de cota — lógica compartilhada com a fila
  // offline em db.js (SafeStorage), evitando duas implementações paralelas.
  set: (k, v) => SafeStorage.set(k, v),
  users()       { return this.get('garra_users')       || seedUsers(); },
  submissions() { return this.get('garra_submissions') || []; },
  pendingSync() { return this.get('garra_pending')     || []; },
  fleet()       { return this.get('garra_fleet')       || seedFleet(); },
  customCLs()   { return this.get('garra_custom_cls')  || []; },
  saveUser(u) {
    const list = this.users();
    const idx = list.findIndex(x => x.login === u.login);
    if (idx >= 0) list[idx] = u; else list.push(u);
    this.set('garra_users', list);
  },
  removeUser(login) { this.set('garra_users', this.users().filter(u => u.login !== login)); },
  saveSubmission(s) {
    const list = this.submissions();
    const dup = list.findIndex(x => x.id === s.id);
    if (dup >= 0) list[dup] = s; else list.unshift(s);
    // Poda proativa: mantém TODAS as pendentes de sync + as 150 sincronizadas mais recentes.
    // Evita que o histórico local cresça sem limite até estourar a cota do navegador.
    const pendentes     = list.filter(x => x.synced === false);
    const sincronizadas = list.filter(x => x.synced !== false).slice(0, 150);
    this.set('garra_submissions', [...pendentes, ...sincronizadas]);
  },
  addPending(s)  { const p = this.pendingSync(); p.push(s); this.set('garra_pending', p); },
  clearPending() { this.set('garra_pending', []); },
  getFleetVehicles(cat) { return (this.fleet()[cat] || []).filter(v => v.active); },
  saveFleetItem(cat, item) {
    const f = this.fleet();
    if (!f[cat]) f[cat] = [];
    const idx = f[cat].findIndex(v => v.id === item.id);
    if (idx >= 0) f[cat][idx] = item; else f[cat].push(item);
    this.set('garra_fleet', f);
  },
  deactivateFleetItem(cat, id) {
    const f = this.fleet();
    const idx = (f[cat] || []).findIndex(v => v.id === id);
    if (idx >= 0) { f[cat][idx].active = false; this.set('garra_fleet', f); }
    const subs = this.submissions().map(s => {
      if ((s.meta?.veiculo || s.meta?.equipamento || '') === id) s.archived = true;
      return s;
    });
    this.set('garra_submissions', subs);
  },
  saveCustomCL(cl) {
    const list = this.customCLs();
    const idx = list.findIndex(x => x.id === cl.id);
    if (idx >= 0) list[idx] = cl; else list.push(cl);
    this.set('garra_custom_cls', list);
  },
  removeCustomCL(id) { this.set('garra_custom_cls', this.customCLs().filter(c => c.id !== id)); },
  allCLs() {
    const customs = this.customCLs();
    return { ...DEFAULT_CHECKLISTS, ...Object.fromEntries(customs.map(c => [c.id, c])) };
  },
};

// ─── CONFIGURAÇÃO DE PONTOS ────────────────────────────────
const PontosConfig = {
  get() {
    return DB.get('garra_pontos_config') || { ativo:false, data_inicio:null, data_fim:null };
  },
  save(cfg) { DB.set('garra_pontos_config', cfg); },
  visivel() {
    if (typeof currentUser !== 'undefined' && currentUser?.role === 'manager') return true;
    const cfg = this.get();
    if (!cfg.ativo) return false;
    const hoje = new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
    if (cfg.data_inicio && hoje < cfg.data_inicio) return false;
    return true;
  },
  pontosNoPeriodo(userLogin) {
    const cfg  = this.get();
    const subs = DB.submissions().filter(s => s.user === userLogin);
    return subs.filter(s => {
      const data = (s.date||'').slice(0,10);
      if (cfg.data_inicio && data < cfg.data_inicio) return false;
      if (cfg.data_fim    && data > cfg.data_fim)    return false;
      return true;
    }).reduce((acc,s) => acc+(s.pts||0), 0);
  },
};

// ─── FUNÇÕES DB ────────────────────────────────────
const FuncaoDB = {
  get()      { return DB.get('garra_funcoes') || seedFuncoes(); },
  save(list) { DB.set('garra_funcoes', list); },
  add(f) {
    const list = this.get();
    const idx = list.findIndex(x => x.id === f.id);
    if (idx >= 0) list[idx] = f; else list.push(f);
    this.save(list);
  },
  remove(id) { this.save(this.get().filter(f => f.id !== id)); },
  byId(id)   { return this.get().find(f => f.id === id); },
};

const COR_MAP = {
  navy:'#1a2158', orange:'#f07c1e', green:'#22c97c',
  purple:'#7c4dff', red:'#e8394d', teal:'#00897b',
  brown:'#795548', gray:'#8b95b8',
};

function fidgen() { return 'fc_' + Date.now() + '_' + Math.random().toString(36).slice(2,5); }
function qid()    { return 'q_'  + Date.now() + '_' + Math.random().toString(36).slice(2,5); }
function oid()    { return 'o_'  + Date.now() + '_' + Math.random().toString(36).slice(2,5); }
// Sanitiza texto para uso em innerHTML — previne XSS
// Hash simples para armazenamento offline (não criptográfico — apenas ofusca)
// Senhas reais são validadas pelo banco via bcrypt
async function hashPass(pass) {
  try {
    const buf  = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(pass + 'garra_salt_2026'));
    return Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,'0')).join('').slice(0,16);
  } catch { return btoa(pass).slice(0,16); }
}

function sanitize(s) {
  const d = document.createElement('div');
  d.textContent = String(s || '');
  return d.innerHTML;
}
function esc(s)   { return String(s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }

// ─── SEEDS ─────────────────────────────────────────
function seedUsers() {
  // Auth central — sem senhas hardcoded
  // Usuários autenticados via garra_token (admin/api/main.py)
  return [];
}

function seedFleet() {
  DB.set('garra_fleet', DEFAULT_FLEET);
  return DEFAULT_FLEET;
}

function seedFuncoes() {
  const funcoes = [
    { id:'fc_motorista',   nome:'Motorista',           desc:'Condução de caminhões e veículos de apoio',              cor:'navy',   cls:['caminhao','carro'] },
    { id:'fc_operador',    nome:'Operador de Máquina',  desc:'Operação de escavadeiras, patrol e retroescavadeiras',   cor:'orange', cls:['maquinas'] },
    { id:'fc_mecanico',    nome:'Mecânico',             desc:'Manutenção e reparos da frota',                          cor:'teal',   cls:[] },
    { id:'fc_encarregado', nome:'Encarregado de Obra',  desc:'Supervisão e controle das frentes de serviço',           cor:'purple', cls:[] },
    { id:'fc_aux',         nome:'Auxiliar / Ajudante',  desc:'Apoio geral nas operações',                              cor:'gray',   cls:[] },
  ];
  DB.set('garra_funcoes', funcoes);
  return funcoes;
}

// ─── CONNECTIVITY ──────────────────────────────────
let isOnline = navigator.onLine;
window.addEventListener('online',  () => { isOnline = true;  updateSyncUI(); syncNow(); });
window.addEventListener('offline', () => { isOnline = false; updateSyncUI(); });

// Timer periódico: tenta sync a cada 60s (cobre Android que não dispara 'online' sempre)
setInterval(() => {
  if (navigator.onLine) {
    isOnline = true;
    syncNow();
  }
}, 60000);

// Na abertura do app: migra a fila legada (garra_pending → garra_offline_q)
// e tenta um sync inicial se estiver online. Idempotente.
window.addEventListener('load', () => {
  migrarFilaAntiga();
  if (navigator.onLine) syncNow();
  updateSyncUI();
});

function updateSyncUI() {
  ['sync-dot','mgr-sync-dot','sup-sync-dot'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = 'dot ' + (isOnline ? 'online' : 'offline');
  });
  ['sync-label','mgr-sync-label','sup-sync-label'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = isOnline ? 'Online' : 'Offline';
  });
  const badge = document.getElementById('offline-badge');
  if (badge) badge.style.display = isOnline ? 'none' : 'flex';
  // Fila UNIFICADA (garra_offline_q) + residual da fila antiga (garra_pending,
  // zerada pela migração na abertura — mantida na soma por segurança)
  const pendentes = OfflineQueue.get().length + DB.pendingSync().length;
  const banner  = document.getElementById('pending-banner');
  const cnt     = document.getElementById('pending-count');
  if (banner) banner.style.display = pendentes > 0 ? 'flex' : 'none';
  if (cnt)    cnt.textContent = pendentes;
}

// ─── MIGRAÇÃO: fila antiga (garra_pending) → fila unificada (garra_offline_q) ───
// Roda na abertura do app. Converte submissions pendentes da fila legada em
// itens tipados da fila unificada e limpa a chave antiga. Idempotente.
function migrarFilaAntiga() {
  const legado = DB.pendingSync();
  if (!legado.length) return;
  const jaEnfileirados = new Set(OfflineQueue.get().map(i => i.ref_id).filter(Boolean));
  let migrados = 0;
  for (const s of legado) {
    if (jaEnfileirados.has(s.id)) continue; // já está na fila unificada — não duplicar
    OfflineQueue.add({
      tipo: 'envio',
      ref_id: s.id,
      path: '/checklist/envios',
      options: { method: 'POST', body: JSON.stringify({
        envio_id:      s.id,
        usuario_login: s.user,
        usuario_nome:  s.userName,
        cl_id:         s.type,
        cl_label:      s.clLabel,
        meta:          s.meta,
        respostas:     s.answers,
        pts:           s.pts,
        tem_nc:        (s.meta?.totalNC || 0) > 0,
        total_nc:      s.meta?.totalNC || 0,
        enviado_em:    s.date,
      })}
    });
    migrados++;
  }
  DB.clearPending();
  if (migrados) console.log('[Migração] ', migrados, 'item(ns) da fila antiga movido(s) para a fila unificada');
}

async function syncNow() {
  if (!isOnline) return;
  migrarFilaAntiga();            // garante que nada ficou na fila legada
  await OfflineQueue.flush();    // fila ÚNICA: envios + logística + usuários
  updateSyncUI();
}

// Quando a fila unificada sincroniza envios de checklist, marcar as
// submissions locais como synced (o backend deduplica por envio_id).
window.addEventListener('garra:fila-sincronizada', (ev) => {
  const enviados = (ev.detail && ev.detail.enviados) || [];
  const idsEnvio = enviados.filter(i => i.tipo === 'envio' && i.ref_id).map(i => i.ref_id);
  if (idsEnvio.length) {
    const subs = DB.submissions();
    let mudou = false;
    for (const s of subs) {
      if (idsEnvio.includes(s.id) && s.synced === false) { s.synced = true; mudou = true; }
    }
    if (mudou) DB.set('garra_submissions', subs);
    console.log('[Sync] ✅', idsEnvio.length, 'checklist(s) sincronizado(s) da fila unificada');
  }
  updateSyncUI();
  if (currentUser?.role === 'driver') renderDriverDashboard();
});

// ─── AUTH ───────────────────────────────────────────
async function doLogin() {
  const login = (document.getElementById('login-user').value || '').trim().toLowerCase();
  const pass  =  document.getElementById('login-pass').value || '';
  const err   =  document.getElementById('login-error');
  const btn   =  document.querySelector('#screen-login .btn-primary');
  if (btn) { btn.textContent = 'Entrando...'; btn.disabled = true; }
  err.classList.add('hidden');

  // ── ESTRATÉGIA: tenta API com timeout curto, fallback offline ──
  let apiOk = false;

  try {
    // Tenta API com timeout de 5s
    const apiUser = await Promise.race([
      GarraDB.login(login, pass),
      new Promise((_, reject) => setTimeout(() => reject(new Error('TIMEOUT')), 5000))
    ]);

    // Sucesso na API — atualiza cache local
    const loc = DB.users().find(u => u.login === apiUser.login) || {};
    currentUser = {
      login:       apiUser.login,
      name:        apiUser.name,
      role:        apiUser.role,
      pts:         apiUser.pts || loc.pts || 0,
      submissions: apiUser.submissions || loc.submissions || 0,
      funcao:      loc.funcao  || '',
      veiculo:     loc.veiculo || '',
      pass:        pass, // salva para login offline futuro
    };
    DB.saveUser(currentUser);
    localStorage.setItem('garra_current_user', JSON.stringify(currentUser));
    apiOk = true;
    _navigate();

  } catch(apiErr) {
    console.warn('[Login] API falhou:', apiErr.message);

    // ── FALLBACK OFFLINE ──────────────────────────
    // Verifica se tem sessão ativa cacheada (sem checar senha — já foi validada antes)
    const cached = DB.users().find(u => u.login === login);

    if (cached && (!isOnline || apiErr.message === 'TIMEOUT')) {
      // Usuário já logou antes — permite entrar offline sem senha
      currentUser = cached;
      localStorage.setItem('garra_current_user', JSON.stringify(cached));
      showOfflineBanner();
      _navigate();
      return;
    }

    // Não encontrou em lugar nenhum
    if (!isOnline || apiErr.message === 'TIMEOUT') {
      err.textContent = '📶 Sem conexão. Faça login online ao menos uma vez para usar offline.';
    } else {
      err.textContent = 'Usuário ou senha incorretos.';
    }
    err.classList.remove('hidden');

  } finally {
    if (btn) { btn.textContent = 'Entrar'; btn.disabled = false; }
  }
}

function showOfflineBanner() {
  const existing = document.getElementById('offline-mode-banner');
  if (existing) return;
  const b = document.createElement('div');
  b.id = 'offline-mode-banner';
  b.style.cssText = 'position:fixed;top:0;left:0;right:0;background:var(--warn,#f5a623);color:#333;padding:8px 16px;font-size:12px;font-weight:600;text-align:center;z-index:9999;';
  b.innerHTML = '📶 Modo Offline — Check lists serão sincronizados quando conectar <button onclick="this.parentElement.remove()" style="margin-left:12px;background:none;border:none;cursor:pointer;font-size:14px">✕</button>';
  document.body.prepend(b);
}

function _navigate() {
  // Quem tem permissão de Logística vê o painel que a contém (superior),
  // mesmo sendo driver — a permissão manda, não o role.
  if (currentUser.role === 'manager') showManager();
  else if (currentUser.role === 'superior') showSuperior();
  else if (currentUser.temLogistica) showSuperior(); // driver com logística
  else showDriver(); // driver e diarista sem logística → painel padrão
}

document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && document.getElementById('screen-login').classList.contains('active')) doLogin();
});

function doLogout() {
  currentUser = null;
  showScreen('screen-login');
  document.getElementById('login-user').value = '';
  document.getElementById('login-pass').value = '';
}

// ─── SCREENS ───────────────────────────────────────
function showScreen(id) {
  document.querySelectorAll('.screen').forEach(s => s.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  updateSyncUI();
}
function showDriver() {
  showScreen('screen-driver');
  document.getElementById('user-badge-driver').textContent = currentUser.name.charAt(0).toUpperCase();
  renderDriverDashboard();
}
function showSuperior() {
  showScreen('screen-superior');
  document.getElementById('user-badge-sup').textContent = currentUser.name.charAt(0).toUpperCase();

  // Aba Logística é controlada por permissão específica, não pelo role
  const tabLog  = document.querySelector('#screen-superior .tab[onclick*="logistics"]');
  const tabChk  = document.querySelector('#screen-superior .tab[onclick*="checklists"]');
  const tabRep  = document.querySelector('#screen-superior .tab[onclick*="report"]');
  if (tabLog) tabLog.style.display = currentUser.temLogistica ? '' : 'none';
  // Relatório é conteúdo de Logística — segue a MESMA permissão (checklist_logistica)
  if (tabRep) tabRep.style.display = currentUser.temLogistica ? '' : 'none';

  // Aba inicial conforme de onde o usuário veio no app shell
  // (ícone Checklist → checklists; ícone Logística → logistics, só se tiver permissão)
  const querLogistica = currentUser.tabInicial === 'logistics' && currentUser.temLogistica;
  if (querLogistica && tabLog) {
    supTab('logistics', tabLog);
  } else if (tabChk) {
    supTab('checklists', tabChk);
  }

  renderSuperiorDashboard();
  // Busca envios do servidor e re-renderiza (mesma hidratação do manager)
  sincronizarEnviosDoServidor().then(ok => { if (ok) renderSuperiorDashboard(); });

  // Botão "Novo Check List" só para admin/gestor/luana (não operador comum)
  const criarBar = document.getElementById('sup-criar-bar');
  if (criarBar) {
    criarBar.style.display = currentUser.podeEditar ? '' : 'none';
  }
}
function showManager() {
  showScreen('screen-manager');
  document.getElementById('user-badge-mgr').textContent = currentUser.name.charAt(0).toUpperCase();

  // Aba Logística controlada por permissão específica (igual ao painel superior)
  const tabLog = document.querySelector('#screen-manager .tab[onclick*="logistics"]');
  if (tabLog) tabLog.style.display = currentUser.temLogistica ? '' : 'none';

  // Se veio pela aba Logística do app shell e tem permissão → abre direto nela
  const querLogistica = currentUser.tabInicial === 'logistics' && currentUser.temLogistica;
  if (querLogistica && tabLog) {
    mgrTab('logistics', tabLog);
  }

  renderManagerDashboard();
  // Renderiza primeiro do cache local (rápido), depois busca do servidor
  // e re-renderiza — o painel do gestor mostra TODOS os envios do banco.
  sincronizarEnviosDoServidor().then(ok => { if (ok) renderManagerDashboard(); });
}
function goBack()        { if (currentUser?.role==='manager') showManager(); else if (currentUser?.role==='superior') showSuperior(); else showDriver(); }
function goToDashboard() { goBack(); }
function closeDetail()   { goBack(); }

// ─── SUPERIOR ──────────────────────────────────────
function renderSuperiorDashboard() {
  if (typeof renderLogisticsKPIs === 'function') {
    renderLogisticsKPIs('sup-log-active','sup-log-idle','sup-log-drivers','sup-log-total');
    renderCurrentFleet('sup-current-fleet');
    renderLogHistory('sup-log-history', 10);
  }
  renderSupChecklistCards();
}
function renderSupChecklistCards() {
  const el = document.getElementById('sup-cl-cards');
  if (!el) return;
  // (08/07/2026) Meus Últimos Envios também no painel superior — mesma
  // função do driver (renderMeusEnvios), fonte servidor, sem duplicação.
  if (currentUser?.login) renderMeusEnvios('sup-my-envios', currentUser.login);
  // ── Pontuação do colaborador (servidor) — 06/07/2026: o papel "superior"
  // não tinha NENHUM lugar para pontos; injeta card + mini-pódio aqui.
  (async () => {
    try {
      if (!PontosConfig.visivel()) { document.getElementById('sup-pts-card')?.remove(); return; }
      const cfg = PontosConfig.get();
      const ini = cfg.data_inicio || '', fim = cfg.data_fim || '';
      const qs = [ini?('inicio='+ini):'', fim?('fim='+fim):''].filter(Boolean).join('&');
      const token = ckToken();
      const r = await fetch('/checklist/ranking' + (qs?'?'+qs:''), { headers: { 'Authorization': 'Bearer ' + token } });
      if (!r.ok) return;
      const rk = await r.json();
      const eu = rk.find(x => x.login === currentUser?.login) || rk[0];
      // Últimos envios DELE (menor privilégio no backend garante o filtro)
      let envios3 = [];
      try {
        const re = await fetch('/checklist/envios?limit=3', { headers: { 'Authorization': 'Bearer ' + token } });
        if (re.ok) envios3 = await re.json();
      } catch(e) {}
      let card = document.getElementById('sup-pts-card');
      if (!card) { card = document.createElement('div'); card.id = 'sup-pts-card';
        card.style.cssText = 'background:linear-gradient(135deg,#1A2A5E,#2A3F7E);border-radius:12px;padding:16px 18px;margin:0 0 14px;color:#fff';
        el.parentElement.insertBefore(card, el); }
      card.innerHTML = `
        <div style="display:flex;justify-content:space-between;align-items:baseline">
          <div><div style="font-size:11px;opacity:.7;text-transform:uppercase;letter-spacing:.05em">Seus pontos</div>
          <div style="font-size:30px;font-weight:800;color:#E8820C">${eu ? eu.pts : 0}</div>
          <div style="font-size:12px;opacity:.8">🔥 ${eu ? eu.envios : 0} envios</div></div>
          <div style="text-align:right;font-size:12px;max-width:55%">
            <div style="font-weight:800;font-size:11px;letter-spacing:.05em;opacity:.7;margin-bottom:4px">📋 ÚLTIMOS ENVIOS</div>
            ${envios3.length ? envios3.map(e => {
              const nc = e.total_nc || 0;
              let dt = ''; try { dt = new Date(e.enviado_em).toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit'}); } catch(_){}
              return `<div style="opacity:.9">${dt} · ${sanitize((e.cl_label||'').split(' ')[0])} ${nc>0?`<span style=\"color:#FCA5A5\">⚠ ${nc} NC</span>`:'<span style=\"color:#86EFAC\">✓</span>'}</div>`;
            }).join('') : '<div style="opacity:.6">Nenhum envio ainda</div>'}
          </div>
        </div>`;
    } catch(e) { console.error('[sup-pts]', e); }
  })();
  el.innerHTML = Object.values(DB.allCLs()).map(cl => `
    <div class="cl-card" onclick="startChecklist('${cl.id}')">
      <div class="clc-icon">${cl.icon}</div>
      <div class="clc-body"><div class="clc-name">${sanitize(cl.label)}</div><div class="clc-desc">${cl.desc||''}</div></div>
      <div class="clc-arrow">›</div>
    </div>`).join('');
}
function supTab(tab, btn) {
  if (tab === 'logistics' && typeof LogSync !== 'undefined') LogSync.ensure();
  // Logística e Relatório exigem permissão checklist_logistica (defesa em profundidade)
  if ((tab === 'logistics' || tab === 'report') && !(currentUser && currentUser.temLogistica)) return;
  document.querySelectorAll('#screen-superior .tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('#screen-superior .tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('sup-tab-'+tab).classList.add('active');
  if (tab === 'report') renderSupReportPreview();
  if (tab === 'checklists') renderSupChecklistCards();
}
function renderSupReportPreview() {
  const el = document.getElementById('sup-report-preview');
  if (el && typeof buildReportHTML === 'function') el.innerHTML = buildReportHTML();
}

// ─── DRIVER DASHBOARD ──────────────────────────────
function renderDriverDashboard() {
  const u = DB.users().find(u => u.login === currentUser.login);
  if (!u) return;
  currentUser = { ...currentUser, ...u };

  // Pontos visíveis apenas se gestor ativou
  // Pontos — exibe baseado na configuração do gestor
  const cfg         = PontosConfig.get();
  const ptsVisiveis = PontosConfig.visivel();
  // (08/07/2026) Visibilidade OFF → o card de ranking some por completo:
  // colaborador vê apenas a seleção de checklists e "Meus Últimos Envios".
  const scoreCard = document.querySelector('#screen-driver .score-card');
  if (scoreCard) scoreCard.style.display = ptsVisiveis ? '' : 'none';
  const ptsExibir   = ptsVisiveis ? PontosConfig.pontosNoPeriodo(u.login) : '–';
  const ptsMsg      = !ptsVisiveis ? (cfg.data_inicio ? 'Disponível em ' + formatDate(cfg.data_inicio) : 'Pontuação em breve') : '';

  document.getElementById('driver-pts').textContent    = ptsExibir;
  document.getElementById('driver-streak').textContent = `🔥 ${u.submissions || 0} envios`;

  // Atualiza com o valor OFICIAL do servidor (assíncrono; local é só placeholder)
  if (ptsVisiveis) {
    (async () => {
      try {
        const ini = cfg.data_inicio || '', fim = cfg.data_fim || '';
        const qs = [ini?('inicio='+ini):'', fim?('fim='+fim):''].filter(Boolean).join('&');
        const token = ckToken();
        const r = await fetch('/checklist/ranking' + (qs?'?'+qs:''), {
          headers: { 'Authorization': 'Bearer ' + token }
        });
        if (r.ok) {
          const rk = await r.json();
          const eu = rk.find(x => x.login === u.login);
          const elP = document.getElementById('driver-pts');
          const elS = document.getElementById('driver-streak');
          if (eu && elP) elP.textContent = eu.pts;
          if (eu && elS) elS.textContent = `🔥 ${eu.envios} envios`;
          // (06/07/2026) Mini-pódio removido: ranking comparativo é só da gestão.
          document.getElementById('driver-podium')?.remove();
        }
      } catch(e) { /* offline: mantém local */ }
    })();
  }

  // Mostra mensagem sutil abaixo dos pontos se desativado
  const existingMsg = document.getElementById('pts-soon-msg');
  if (existingMsg) existingMsg.remove();
  if (!ptsVisiveis && ptsMsg) {
    const ptsEl = document.getElementById('driver-pts');
    if (ptsEl) {
      const msg = document.createElement('div');
      msg.id = 'pts-soon-msg';
      msg.style.cssText = 'font-size:10px;color:rgba(255,255,255,.5);margin-top:2px;text-align:center';
      msg.textContent = ptsMsg;
      ptsEl.parentNode.insertBefore(msg, ptsEl.nextSibling);
    }
  }

  const drivers = DB.users().filter(u => u.role === 'driver' || u.role === 'diarista').sort((a,b) => (b.pts||0)-(a.pts||0));
  const rank    = drivers.findIndex(d => d.login === u.login) + 1;
  document.getElementById('driver-rank').textContent = '#' + rank;
  const maxPts = Math.max(...drivers.map(d => d.pts||0), 1);
  document.getElementById('driver-bar').style.width = Math.round(((u.pts||0)/maxPts)*100) + '%';

  // Filtra CLs pela função
  // Sempre usa DEFAULT_CHECKLISTS + customizados do localStorage
  const allCLs = DB.allCLs();
  const funcao  = u.funcao ? FuncaoDB.byId(u.funcao) : null;

  let visivel;
  if (funcao?.cls?.length) {
    visivel = Object.fromEntries(
      Object.entries(allCLs).filter(([id]) => funcao.cls.includes(id))
    );
    if (!Object.keys(visivel).length) visivel = allCLs;
  } else {
    visivel = allCLs;
  }

  // Garante que pelo menos os padrão aparecem
  if (!Object.keys(visivel).length && typeof DEFAULT_CHECKLISTS !== 'undefined') {
    visivel = DEFAULT_CHECKLISTS;
  }
  console.log('[Driver] CLs visíveis:', Object.keys(visivel).length);

  const cardsEl = document.getElementById('driver-cl-cards');
  // Veículo fixo
  const veiculoBanner = u.veiculo
    ? `<div style="background:rgba(240,124,30,.08);border:1px solid rgba(240,124,30,.2);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:12px;font-size:12px;color:var(--orange-dk)">🚗 Veículo fixo: <strong>${u.veiculo}</strong></div>`
    : '';
  cardsEl.innerHTML = veiculoBanner + Object.values(visivel).map(cl => `
    <div class="cl-card" onclick="startChecklist('${cl.id}')">
      <div class="clc-icon">${cl.icon}</div>
      <div class="clc-body"><div class="clc-name">${sanitize(cl.label)}</div><div class="clc-desc">${cl.desc||''}</div></div>
      <div class="clc-arrow">›</div>
    </div>`).join('');

  // Histórico — fonte: SERVIDOR (05/07/2026; localStorage só como fallback offline)
  // (08/07/2026) Extraído em renderMeusEnvios() — compartilhado com o painel superior.
  renderMeusEnvios('driver-history', u.login);
}

// ─── MEUS ÚLTIMOS ENVIOS (compartilhado: driver + superior) ─────────────────
function renderMeusEnvios(targetId, login) {
  const allCLs = DB.allCLs();
  const histEl = document.getElementById(targetId);
  if (!histEl) return;
  histEl.innerHTML = '<div class="empty-state" style="opacity:.6">Carregando…</div>';
  (async () => {
    try {
      const token = ckToken();
      const r = await fetch('/checklist/envios?usuario=' + encodeURIComponent(login) + '&limit=15',
                            { headers: { 'Authorization': 'Bearer ' + token } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const envios = await r.json();
      if (!envios.length) { histEl.innerHTML = '<div class="empty-state"><div class="es-icon">📋</div>Nenhum check list enviado ainda!</div>'; return; }
      histEl.innerHTML = envios.map(e => {
        const nc  = e.total_nc || 0;
        const lbl = nc > 0 ? `⚠ ${nc} NC` : '✓ Conforme';
        const st  = nc > 0 ? 'nc' : 'ok';
        const veh = e.meta?.veiculo || e.meta?.equipamento || '';
        return `<div class="history-item">
          <div class="hi-icon">📋</div>
          <div class="hi-body">
            <div class="hi-title">${sanitize(e.cl_label || e.cl_id || 'Check list')}${veh ? ' – ' + sanitize(veh) : ''}</div>
            <div class="hi-meta">${formatDateTime(e.enviado_em)}${e.meta?.local ? ' • ' + sanitize(e.meta.local) : ''}</div>
          </div>
          <div class="badge ${st}">${lbl}</div>
        </div>`;
      }).join('');
      return;
    } catch (err) { console.error('[historico driver] fallback local:', err); }
    const subs = DB.submissions().filter(s => s.user === login);
    if (!subs.length) { histEl.innerHTML = '<div class="empty-state"><div class="es-icon">📋</div>Nenhum check list enviado ainda!</div>'; return; }
    histEl.innerHTML = subs.slice(0,15).map(s => {
    const cl  = allCLs[s.type] || {};
    const nc  = countNC(s);
    const st  = s.archived ? 'archived' : (s.synced===false ? 'pending' : nc>0 ? 'nc' : 'ok');
    const lbl = s.archived ? '📦 Removido' : st==='pending' ? '⏳ Sync' : nc>0 ? `⚠ ${nc} NC` : '✓ Conforme';
    const veh = s.meta?.veiculo || s.meta?.equipamento || '';
    return `<div class="history-item" onclick="showSubmissionDetail('${s.id}')">
      <div class="hi-icon">${cl.icon||'📋'}</div>
      <div class="hi-body">
        <div class="hi-title">${cl.label||s.type}${veh?' – '+veh:''}</div>
        <div class="hi-meta">${formatDate(s.date)}${s.meta?.local?' • '+s.meta.local:''}</div>
      </div>
      <div class="badge ${st}">${lbl}</div>
    </div>`;
  }).join('');
  })();
}

// ─── CHECKLIST FORM ────────────────────────────────
function startChecklist(clId) {
  currentCLId = clId; currentStep = 0; formAnswers = {}; formMeta = {};
  renderFormStep(); showScreen('screen-form');
}
function getCL() { return DB.allCLs()[currentCLId]; }

function renderFormStep(manterScroll) {
  const content = document.getElementById('form-content');
  const scrollAnterior = manterScroll && content ? content.scrollTop : 0;

  // Tirar foco do botão ANTES de destruir o DOM —
  // sem isso o browser tenta relocalizar o elemento focado após o innerHTML
  // e reseta o scroll para o topo
  if (manterScroll && document.activeElement) {
    document.activeElement.blur();
  }

  const cl = getCL(), step = cl.steps[currentStep], total = cl.steps.length;
  document.getElementById('form-title').textContent       = `${cl.icon} ${cl.label}`;
  document.getElementById('form-step-label').textContent  = `${currentStep+1} / ${total}`;
  document.getElementById('form-prog-bar').style.width    = Math.round((currentStep/total)*100)+'%';
  document.getElementById('btn-prev').style.visibility    = currentStep===0 ? 'hidden' : 'visible';
  document.getElementById('btn-next').textContent         = currentStep===total-1 ? 'Enviar ✓' : 'Próximo';
  if (step.type === 'meta') {
    // Get vehicles for the CL category; if none specified, offer all active fleet
    let vehicles = DB.getFleetVehicles(cl.vehicleCat||'').map(v=>v.id);
    if(!vehicles.length) {
      const f=DB.fleet();
      vehicles=[...((f.maquinas||[]).filter(v=>v.active).map(v=>v.id)),...((f.carro||[]).filter(v=>v.active).map(v=>v.id)),...((f.caminhao||[]).filter(v=>v.active).map(v=>v.id))];
    }
    content.innerHTML = `<div class="form-step"><div class="form-step-title">${step.title}</div><div class="form-step-sub">${step.sub}</div>${step.fields.map(f=>renderMetaField(f,vehicles)).join('')}</div>`;
    const dateEl = document.getElementById('meta-data');
    if (dateEl && !formMeta.data) dateEl.value = new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
    step.fields.forEach(f => { const el=document.getElementById('meta-'+f.id); if(el&&formMeta[f.id]) el.value=formMeta[f.id]; });
  } else if (step.type==='checklist'||step.type==='custom') {
    content.innerHTML = `<div class="form-step"><div class="form-step-title">${step.title}</div><div class="form-step-sub">${step.sub||''}</div>${step.items.map(item=>renderCheckItem(item)).join('')}</div>`;
  } else if (step.type==='obs') {
    content.innerHTML = `<div class="form-step"><div class="form-step-title">${step.title}</div><div class="form-step-sub">${step.sub}</div>${step.fields.map(f=>renderObsField(f)).join('')}</div>`;
    step.fields.forEach(f => { const el=document.getElementById('obs-'+f.id); if(el&&formMeta[f.id]) el.value=formMeta[f.id]; });
  }
  // Ao responder uma pergunta (manterScroll=true) preserva a posição;
  // ao mudar de step vai ao topo.
  if (manterScroll && scrollAnterior > 0) {
    content.scrollTop = scrollAnterior;
    // Garantia: rAF sobrevive a qualquer reflow assíncrono do browser
    requestAnimationFrame(() => { content.scrollTop = scrollAnterior; });
  } else {
    content.scrollTop = 0;
  }
}

function renderMetaField(f, vehicles) {
  if (f.type === 'select') {
    let opts = f.options === 'vehicles' ? vehicles : (f.options || []);

    // Para campo de veículo: sempre mostra TODA a frota ativa organizada
    if (f.id === 'veiculo' || f.id === 'equipamento') {
      const fleet = DB.fleet();
      const todaFrota = [
        ...((fleet.maquinas  ||[]).filter(v=>v.active).map(v=>({id:v.id, label:`🚜 ${v.id}${v.desc?' — '+v.desc:''}`, cat:'Máquinas'}))),
        ...((fleet.carro     ||[]).filter(v=>v.active).map(v=>({id:v.id, label:`🚗 ${v.id}${v.desc?' — '+v.desc:''}`, cat:'Carros de Apoio'}))),
        ...((fleet.caminhao  ||[]).filter(v=>v.active).map(v=>({id:v.id, label:`🚛 ${v.id}${v.desc?' — '+v.desc:''}`, cat:'Caminhões'}))),
      ];

      const fixedV = currentUser?.veiculo || '';
      const hint   = fixedV
        ? `<div style="font-size:11px;color:var(--orange);margin-top:4px">
             🔗 Equipamento fixo: <strong>${fixedV}</strong> — pré-selecionado. Troque se necessário.
           </div>`
        : '';

      // Agrupa por categoria com optgroup
      const cats = ['Máquinas','Carros de Apoio','Caminhões'];
      const optsHtml = cats.map(cat => {
        const itens = todaFrota.filter(v => v.cat === cat);
        if (!itens.length) return '';
        return `<optgroup label="${cat}">
          ${itens.map(v => `<option value="${v.id}" ${v.id===fixedV?'selected':''}>${v.label}</option>`).join('')}
        </optgroup>`;
      }).join('');

      return `<div class="form-meta-field">
        <label>${f.label}</label>
        <select id="meta-${f.id}" style="font-size:16px;padding:12px 14px;width:100%;border:1.5px solid var(--gray-light);border-radius:var(--radius-sm);-webkit-appearance:none;appearance:none">
          <option value="">Selecione o equipamento...</option>
          ${optsHtml}
        </select>
        ${hint}
      </div>`;
    }

    // Outros selects normais
    const opts2 = opts;
    return `<div class="form-meta-field"><label>${f.label}</label>
      <select id="meta-${f.id}" style="font-size:16px;padding:12px 14px;width:100%;border:1.5px solid var(--gray-light);border-radius:var(--radius-sm);background:var(--white);color:var(--text);appearance:none;-webkit-appearance:none;background-image:url('data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 24 24%22 fill=%22none%22 stroke=%22%231a2158%22 stroke-width=%222%22><polyline points=%226 9 12 15 18 9%22/></svg>');background-repeat:no-repeat;background-position:right 12px center;background-size:18px;padding-right:38px">
        <option value="">Selecione...</option>
        ${opts2.map(o=>`<option value="${o}">${o}</option>`).join('')}
      </select>
    </div>`;
  }

  // Campo de texto — pré-preenche operador com nome do colaborador
  const defaultVal = (f.id === 'operador' && currentUser?.name) ? currentUser.name : '';
  return `<div class="form-meta-field">
    <label>${f.label}</label>
    <input type="${f.type}" id="meta-${f.id}" placeholder="${f.placeholder||''}" value="${defaultVal}"
      style="font-size:16px" />
  </div>`;
}

function renderCheckItem(item) {
  const ans = formAnswers[item.id] || {};
  if (item.conditionalOn) {
    const depAns = formAnswers[item.conditionalOn];
    const depVal = depAns?.val ?? depAns?.text ?? depAns?.selected ?? '';
    if (depVal !== item.conditionalValue) return '';
  }
  const required = item.required ? '<span style="color:var(--danger);margin-left:3px">*</span>' : '';
  const pts  = ''; // Pontuação não exibida para o colaborador
  const hint = item.hint ? `<div style="font-size:11px;color:var(--text-light);margin-bottom:6px">${item.hint}</div>` : '';
  let body = '';
  switch(item.type||'checklist') {
    case 'checklist': {
      const selOk=ans.val==='C'?'selected-ok':'', selNc=ans.val==='NC'?'selected-nc':'', selNa=ans.val==='NA'?'selected-na':'';
      const obsVis = ans.val==='NC'?'visible':'';
      // photoMode padrão: nc_only para itens de check list
      const photoMode=item.photoMode||'nc_only', showPhoto=photoMode==='always'||(photoMode==='nc_only'&&ans.val==='NC');
      const photoHtml = showPhoto?`<div class="ci-photo-wrap" style="margin-top:8px">${ans.photo?`<img src="${ans.photo}" class="ci-photo-preview" alt="Foto" />`:''}
        <label class="ci-photo-btn">📷 ${ans.photo?'Trocar foto':photoMode==='nc_only'?'Foto da NC':'Adicionar foto'}<input type="file" accept="image/*" capture="environment" style="display:none" onchange="setPhotoAnswer('${item.id}',this)" /></label>
        ${ans.photo?`<button class="ci-photo-remove" onclick="clearPhotoAnswer('${item.id}')">✕ Remover</button>`:''}</div>`:'';
      body=`<div class="ci-options"><button class="ci-btn ${selOk}" onclick="setAnswer('${item.id}','C')">✓ Conforme</button><button class="ci-btn ${selNc}" onclick="setAnswer('${item.id}','NC')">✗ Não Conforme</button><button class="ci-btn ${selNa}" onclick="setAnswer('${item.id}','NA')">N/A</button></div>
      <div class="ci-obs-wrap ${obsVis}" id="obs-wrap-${item.id}"><textarea class="ci-obs" id="obs-${item.id}" placeholder="Descreva o problema..." rows="2" onchange="updateObsAnswer('${item.id}',this.value)">${ans.obs||''}</textarea></div>${photoHtml}`;
      break;
    }
    case 'text': body=`<input type="text" class="ci-text-input" id="ans-${item.id}" placeholder="Sua resposta..." value="${esc(ans.text||'')}" onchange="setTextAnswer('${item.id}',this.value)" />`; break;
    case 'textarea': body=`<textarea class="ci-obs" id="ans-${item.id}" rows="3" placeholder="Sua resposta..." style="width:100%" onchange="setTextAnswer('${item.id}',this.value)">${ans.text||''}</textarea>`; break;
    case 'date': body=`<input type="date" class="ci-text-input" id="ans-${item.id}" value="${ans.text||''}" onchange="setTextAnswer('${item.id}',this.value)" />`; break;
    case 'radio': body=`<div class="ci-radio-group">${(item.options||[]).map(o=>`<label class="ci-radio-label"><input type="radio" name="r_${item.id}" value="${esc(o.label)}" ${ans.selected===o.label?'checked':''} onchange="setSelectedAnswer('${item.id}','${esc(o.label)}')" /><span>${o.label}</span></label>`).join('')}</div>`; break;
    case 'checkbox': {
      const sel=ans.selected?ans.selected.split('|||'):[];
      body=`<div class="ci-radio-group">${(item.options||[]).map(o=>`<label class="ci-radio-label"><input type="checkbox" value="${esc(o.label)}" ${sel.includes(o.label)?'checked':''} onchange="toggleCheckboxAnswer('${item.id}','${esc(o.label)}',this.checked)" /><span>${o.label}</span></label>`).join('')}</div>`;
      break;
    }
    case 'select': body=`<select class="form-meta-field" style="width:100%;padding:11px 13px;border:1.5px solid var(--gray-light);border-radius:var(--radius-sm);font-size:13px" onchange="setSelectedAnswer('${item.id}',this.value)"><option value="">Selecione...</option>${(item.options||[]).map(o=>`<option value="${esc(o.label)}" ${ans.selected===o.label?'selected':''}>${o.label}</option>`).join('')}</select>`; break;
    case 'scale': {
      const min=item.scaleMin||1,max=item.scaleMax||5,nums=Array.from({length:max-min+1},(_,i)=>min+i);
      body=`<div class="ci-scale-wrap"><div class="ci-scale-nums">${nums.map(n=>`<button class="ci-scale-btn ${ans.selected==n?'selected':''}" onclick="setSelectedAnswer('${item.id}','${n}')">${n}</button>`).join('')}</div>${(item.scaleMinLabel||item.scaleMaxLabel)?`<div class="ci-scale-labels"><span>${item.scaleMinLabel||''}</span><span>${item.scaleMaxLabel||''}</span></div>`:''}</div>`;
      break;
    }
    case 'photo': body=`<div class="ci-photo-wrap">${ans.photo?`<img src="${ans.photo}" class="ci-photo-preview" alt="Foto" />`:''}
      <label class="ci-photo-btn">📷 ${ans.photo?'Trocar foto':'Tirar / escolher foto'}<input type="file" accept="image/*" capture="environment" style="display:none" onchange="setPhotoAnswer('${item.id}',this)" /></label>
      ${ans.photo?`<button class="ci-photo-remove" onclick="clearPhotoAnswer('${item.id}')">✕ Remover</button>`:''}</div>`; break;
    default: body='';
  }
  return `<div class="checklist-item" id="ci-${item.id}"><div class="ci-label">${item.label}${required}</div>${hint}${pts}${body}</div>`;
}

function renderObsField(f) {
  if (f.type==='textarea') return `<div class="form-meta-field"><label>${f.label}</label><textarea id="obs-${f.id}" class="ci-obs" rows="5" style="width:100%" placeholder="${f.placeholder||''}"></textarea></div>`;
  return `<div class="form-meta-field"><label>${f.label}</label><input type="text" id="obs-${f.id}" placeholder="${f.placeholder||''}" /></div>`;
}

function setAnswer(itemId,val)        { if(!formAnswers[itemId])formAnswers[itemId]={}; formAnswers[itemId].val=val; renderFormStep(true); }
function updateObsAnswer(itemId,val)  { if(!formAnswers[itemId])formAnswers[itemId]={}; formAnswers[itemId].obs=val; }
function setTextAnswer(itemId,val)    { if(!formAnswers[itemId])formAnswers[itemId]={}; formAnswers[itemId].text=val; renderFormStep(true); }
function setSelectedAnswer(itemId,val){ if(!formAnswers[itemId])formAnswers[itemId]={}; formAnswers[itemId].selected=val; renderFormStep(true); }
function toggleCheckboxAnswer(itemId,val,checked) {
  if(!formAnswers[itemId])formAnswers[itemId]={};
  let sel=formAnswers[itemId].selected?formAnswers[itemId].selected.split('|||'):[];
  if(checked){if(!sel.includes(val))sel.push(val);}else sel=sel.filter(v=>v!==val);
  formAnswers[itemId].selected=sel.join('|||'); renderFormStep(true);
}
function setPhotoAnswer(itemId,input) {
  const file=input.files[0]; if(!file)return;
  comprimirImagem(file, 1280, 0.7).then(dataUrl => {
    if(!formAnswers[itemId])formAnswers[itemId]={};
    formAnswers[itemId].photo=dataUrl;
    renderFormStep(true);
  }).catch(() => {
    // Se a compressão falhar por qualquer motivo, usa a foto original como fallback
    const reader=new FileReader();
    reader.onload=e=>{if(!formAnswers[itemId])formAnswers[itemId]={}; formAnswers[itemId].photo=e.target.result; renderFormStep(true);};
    reader.readAsDataURL(file);
  });
}
// Redimensiona a foto (lado maior = maxLado) e recomprime em JPEG — uma foto de
// câmera (3-8MB) vira tipicamente 150-400KB, sem perda visual perceptível para
// documentação de checklist. Isso é o que evita a cota do navegador estourar.
function comprimirImagem(file, maxLado, qualidade) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = reject;
    reader.onload = e => {
      const img = new Image();
      img.onerror = reject;
      img.onload = () => {
        let { width, height } = img;
        if (width > maxLado || height > maxLado) {
          if (width > height) { height = Math.round(height * maxLado / width); width = maxLado; }
          else { width = Math.round(width * maxLado / height); height = maxLado; }
        }
        const canvas = document.createElement('canvas');
        canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, width, height);
        resolve(canvas.toDataURL('image/jpeg', qualidade));
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });
}
function clearPhotoAnswer(itemId) { if(formAnswers[itemId])formAnswers[itemId].photo=null; renderFormStep(true); }

function saveCurrentStep() {
  const step=getCL().steps[currentStep];
  if(step.type==='meta')step.fields.forEach(f=>{const el=document.getElementById('meta-'+f.id);if(el)formMeta[f.id]=el.value;});
  else if(step.type==='obs')step.fields.forEach(f=>{const el=document.getElementById('obs-'+f.id);if(el)formMeta[f.id]=el.value;});
}
function formNext() {
  saveCurrentStep();
  const cl   = getCL();
  const step = cl.steps[currentStep];

  // Obs e seções customizadas sem itens — avança sempre
  if (step.type === 'obs' || step.type === 'section') {
    if (currentStep < cl.steps.length - 1) { currentStep++; renderFormStep(); }
    else submitChecklist();
    return;
  }

  // ── VALIDAÇÃO META ──────────────────────────────
  if (step.type === 'meta') {
    const faltando = [];
    step.fields.forEach(f => {
      const el  = document.getElementById('meta-'+f.id);
      const val = el?.value?.trim() || '';
      if (!val && (f.id === 'veiculo' || f.id === 'equipamento' || f.id === 'operador')) {
        faltando.push(f.label);
      }
    });
    if (faltando.length) {
      alert('⚠️ Preencha os campos obrigatórios:\n• ' + faltando.join('\n• '));
      return;
    }
    if (currentStep < cl.steps.length - 1) { currentStep++; renderFormStep(); }
    else submitChecklist();
    return;
  }

  // ── VALIDAÇÃO CHECKLIST ─────────────────────────
  if (step.type === 'checklist' || step.type === 'custom') {
    const naoRespondidos = [];
    step.items.forEach(item => {
      // Pula condicionais ocultos
      if (item.conditionalOn) {
        const dep = formAnswers[item.conditionalOn];
        const depVal = dep?.val ?? dep?.text ?? dep?.selected ?? '';
        if (depVal !== item.conditionalValue) return;
      }
      const tipo = item.type || 'checklist';
      const ans  = formAnswers[item.id];
      if (tipo === 'checklist') {
        if (!ans?.val) naoRespondidos.push(item.label);
      }
    });

    if (naoRespondidos.length) {
      const lista = naoRespondidos.slice(0, 5).join('\n• ');
      const extra = naoRespondidos.length > 5 ? ('\n...e mais ' + (naoRespondidos.length - 5) + ' itens') : '';
      alert('⚠️ Responda todos os itens antes de avançar:\n• ' + lista + extra);
      // Destaca itens não respondidos visualmente
      step.items.forEach(item => {
        const el = document.getElementById('ci-' + item.id);
        if (!el) return;
        const ans = formAnswers[item.id];
        const tipo = item.type || 'checklist';
        if (tipo === 'checklist' && !ans?.val) {
          el.style.borderLeft = '4px solid var(--danger)';
          el.style.background = 'rgba(232,57,77,.04)';
        } else {
          el.style.borderLeft = '';
          el.style.background = '';
        }
      });
      // Rola até o primeiro não respondido
      const primeiro = step.items.find(item => {
        const tipo = item.type || 'checklist';
        return tipo === 'checklist' && !formAnswers[item.id]?.val;
      });
      if (primeiro) {
        const el = document.getElementById('ci-' + primeiro.id);
        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      return;
    }
  }

  // Avança
  if (currentStep < cl.steps.length - 1) { currentStep++; renderFormStep(); }
  else submitChecklist();
}
function formPrev() { saveCurrentStep(); if(currentStep>0){currentStep--;renderFormStep();} }

// ─── PONTUAÇÃO ─────────────────────────────────────
function countNC(s) { return Object.values(s.answers||{}).filter(a=>a.val==='NC').length; }

// ─── HIDRATAÇÃO DO SERVIDOR (banco é a fonte da verdade) ────────────
// O painel do gestor/admin renderizava SÓ do localStorage do próprio
// dispositivo — envios feitos no celular do operador nunca apareciam no
// desktop. Agora: busca do servidor e mescla com o cache local.
function mesclarEnviosServidor(serverRows) {
  const locais = DB.submissions();
  const doServidor = (serverRows || []).map(r => ({
    id:       r.envio_id,
    user:     r.usuario_login,
    userName: r.usuario_nome,
    type:     r.cl_id,
    clLabel:  r.cl_label,
    meta:     r.meta || {},
    answers:  r.respostas || {},
    pts:      r.pts || 0,
    date:     r.enviado_em,
    synced:   true,
    archived: false, // o GET só retorna arquivado=FALSE
  }));
  const idsServidor = new Set(doServidor.map(s => s.id));
  // (08/07/2026) Servidor é a fonte da verdade: preserva APENAS locais ainda
  // não sincronizados (fila offline). Itens com synced=true que o servidor
  // não retornou foram excluídos/arquivados no banco — não devem ressuscitar.
  const soLocais = locais.filter(s => !idsServidor.has(s.id) && s.synced !== true);
  const lista = [...doServidor, ...soLocais]
    .sort((a, b) => new Date(b.date) - new Date(a.date));
  DB.set('garra_submissions', lista);
}

async function sincronizarEnviosDoServidor() {
  if (!navigator.onLine) return false;
  try {
    const rows = await GarraDB.getEnvios({ limit: 200 });
    mesclarEnviosServidor(rows);
    console.log('[Envios] Hidratado do servidor:', (rows || []).length, 'envio(s)');
    return true;
  } catch (e) {
    console.warn('[Envios] Falha ao buscar do servidor:', e.message);
    return false;
  }
}
function calculatePoints(s, cl) {
  const rules=cl.scoreRules||{full:100,nc:60,obs:20,ontime:10};
  const nc=countNC(s);
  let itemBonus=0;
  if(nc===0) cl.steps?.filter(st=>st.type==='checklist').forEach(step=>step.items?.forEach(item=>{const ans=s.answers?.[item.id];if(ans?.val==='C')itemBonus+=(item.pts||1)-1;}));
  let pts=nc===0?rules.full:rules.nc; pts+=itemBonus;
  if(s.meta?.observacoes?.length>20)pts+=rules.obs;
  if((s.date||'').slice(0,10)===new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,10))pts+=rules.ontime;
  return Math.max(0,pts);
}

// ─── SUBMIT ────────────────────────────────────────
async function submitChecklist() {
  // Mostra loading no botão Enviar
  const btnNext = document.getElementById('btn-next');
  if (btnNext) { btnNext.textContent = 'Enviando...'; btnNext.disabled = true; }

  try {
    // Garante que usuário está logado
    if (!currentUser?.login) throw new Error('Usuário não autenticado');

    const cl  = getCL();
    if (!cl) throw new Error('Check list não encontrado: ' + currentCLId);

    const id  = 'sub_' + Date.now() + '_' + Math.random().toString(36).slice(2,7);
    const submission = {
      id,
      user:       currentUser.login,
      userName:   currentUser.name,
      type:       currentCLId,
      clLabel:    cl.label,
      date:       new Date().toISOString(),
      meta:       { ...formMeta },
      answers:    { ...formAnswers },
      synced:     false,
      archived:   false,
    };
    submission.pts = calculatePoints(submission, cl);

    // 1. Salva localmente PRIMEIRO — garante que não perde mesmo se API falhar
    DB.saveSubmission({ ...submission, synced: false });

    // 2. Tenta enviar para o banco
    let sincronizado = false;
    const payloadEnvio = {
      envio_id:      submission.id,
      usuario_login: submission.user,
      usuario_nome:  submission.userName,
      cl_id:         submission.type,
      cl_label:      submission.clLabel,
      meta:          submission.meta,
      respostas:     submission.answers,
      pts:           submission.pts,
      tem_nc:        countNC(submission) > 0,
      total_nc:      countNC(submission),
      enviado_em:    submission.date,
    };
    try {
      const res = await GarraDB.salvarEnvio(payloadEnvio);
      if (res && res.offline) {
        // Offline: o salvarEnvio JÁ enfileirou na fila unificada (garra_offline_q).
        // NÃO marcar como sincronizado — o evento garra:fila-sincronizada marca depois.
        console.warn('[Submit] Offline — envio na fila unificada:', submission.id);
      } else {
        sincronizado = true;
        submission.synced = true;
        DB.saveSubmission(submission);
      }
    } catch(apiErr) {
      // Erro não-offline (ex: 500) — enfileira na MESMA fila unificada
      console.warn('[Submit] API falhou, salvando na fila:', apiErr.message);
      OfflineQueue.add({
        tipo: 'envio',
        ref_id: submission.id,
        path: '/checklist/envios',
        options: { method: 'POST', body: JSON.stringify(payloadEnvio) }
      });
    }

    // 3. Atualiza pontos do colaborador localmente
    const users = DB.users();
    const idx   = users.findIndex(u => u.login === currentUser.login);
    if (idx >= 0) {
      users[idx].pts         = (users[idx].pts         || 0) + submission.pts;
      users[idx].submissions = (users[idx].submissions  || 0) + 1;
      DB.set('garra_users', users);
      currentUser = { ...currentUser, ...users[idx] };
    }

    // 4. Mostra tela de sucesso SEMPRE — independente da API
    showScreen('screen-success');
    document.getElementById('success-title').textContent = 'Check List Enviado! 🎉';
    document.getElementById('success-msg').textContent   = sincronizado
      ? '✅ Salvo e sincronizado com sucesso.'
      : '📦 Salvo localmente. Será sincronizado quando online.';
    const ptsEl2 = document.getElementById('pts-earned');
  if (ptsEl2) {
    if (PontosConfig.visivel()) {
      ptsEl2.textContent = `+${submission.pts} pts`;
      ptsEl2.style.display = '';
    } else {
      ptsEl2.style.display = 'none';
    }
  }

  } catch(fatalErr) {
    // Erro fatal — loga detalhes e salva mesmo assim
    console.error('[Submit] Erro fatal:', fatalErr.message, fatalErr.stack);

    // Tenta salvar localmente mesmo com erro
    try {
      const cl  = getCL();
      const id  = 'sub_emergency_' + Date.now();
      const sub = {
        id, user: currentUser?.login || 'unknown',
        userName: currentUser?.name  || 'unknown',
        type: currentCLId, clLabel: cl?.label || '',
        date: new Date().toISOString(),
        meta: { ...formMeta }, answers: { ...formAnswers },
        synced: false, archived: false, pts: 0,
      };
      DB.saveSubmission(sub);
      showScreen('screen-success');
      document.getElementById('success-title').textContent = 'Check List Salvo!';
      document.getElementById('success-msg').textContent   = '⚠️ Salvo localmente com erro técnico. Informe o gestor.';
      document.getElementById('pts-earned').textContent    = '+0 pts';
      console.log('[Submit] Salvo em modo emergência:', id);
    } catch(e2) {
      // Última rede de segurança: o histórico local (garra_submissions) é só
      // cache de exibição — quem controla o reenvio de verdade é garra_pending/
      // garra_offline_q, que NÃO são tocados aqui. Então é seguro zerar o
      // histórico para liberar espaço e tentar salvar mais uma vez.
      try {
        console.warn('[Submit] Espaço esgotado mesmo após poda — limpando histórico local e tentando novamente...');
        localStorage.removeItem('garra_submissions');
        const cl  = getCL();
        const id  = 'sub_emergency_' + Date.now();
        const sub = {
          id, user: currentUser?.login || 'unknown',
          userName: currentUser?.name  || 'unknown',
          type: currentCLId, clLabel: cl?.label || '',
          date: new Date().toISOString(),
          meta: { ...formMeta }, answers: { ...formAnswers },
          synced: false, archived: false, pts: 0,
        };
        DB.saveSubmission(sub);
        showScreen('screen-success');
        document.getElementById('success-title').textContent = 'Check List Salvo!';
        document.getElementById('success-msg').textContent   = '⚠️ Espaço do celular estava cheio — histórico local foi limpo automaticamente. Seu checklist foi salvo.';
        document.getElementById('pts-earned').textContent    = '+0 pts';
        console.log('[Submit] Salvo após limpeza de emergência:', id);
      } catch(e3) {
        alert('Erro crítico ao salvar. Erro: ' + fatalErr.message + '\n\nPor favor, informe o gestor: o armazenamento do celular está cheio e não foi possível liberar espaço automaticamente.');
        if (btnNext) { btnNext.textContent = 'Enviar ✓'; btnNext.disabled = false; }
      }
    }
  }
}

// ─── MANAGER DASHBOARD ─────────────────────────────
function renderManagerDashboard() {
  renderOverview(); renderRanking(); renderSubmissions(); renderFleet();
  if(typeof renderLogisticsTab==='function')renderLogisticsTab();
  renderChecklistsTab(); renderUsers(); populateSubmissionFilters();
  populateFuncaoFilters(); populateUserModalFuncoes();
}

function mgrTab(tab,btn) {
  if (tab === 'logistics' && typeof LogSync !== 'undefined') LogSync.ensure();
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-'+tab).classList.add('active');
  if (tab === 'ranking') renderRankingTab();
}

// ── OVERVIEW ──
async function renderOverview() {
  // Preload
  const _cl0 = document.getElementById('compliance-list');
  if (_cl0 && !_cl0.children.length) _cl0.innerHTML = '<div class="empty-state" style="opacity:.6">⏳ Carregando…</div>';
  const weekAgo = new Date(Date.now() - 7*86400000);
  let subs = null;
  try {
    const token = ckToken();
    const r = await fetch('/checklist/envios?limit=500', { headers: { 'Authorization': 'Bearer ' + token } });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const envios = await r.json();
    // Adaptador: envio do servidor → formato usado pelas contas abaixo
    subs = envios.map(e => ({
      user: e.usuario_login, userName: e.usuario_nome, type: e.cl_id,
      date: e.enviado_em, archived: false, _nc: (e.total_nc || 0),
      answers: e.respostas || {}   // (16/07/2026) itens p/ conformidade por item
    }));
  } catch (err) {
    console.error('[visão geral] fallback local:', err);
    subs = DB.submissions();
  }
  const _ncDe = s => (s._nc !== undefined ? s._nc : countNC(s));
  document.getElementById('kpi-total').textContent        = subs.length;
  document.getElementById('kpi-week').textContent         = subs.filter(s=>new Date(s.date)>weekAgo).length;
  document.getElementById('kpi-pending-sync').textContent = OfflineQueue.get().length + DB.pendingSync().length;
  document.getElementById('kpi-nc').textContent           = subs.filter(s=>_ncDe(s)>0).length;
  const allCLs=DB.allCLs(),counts={};
  Object.keys(allCLs).forEach(k=>counts[k]=0);
  subs.forEach(s=>{if(counts[s.type]!==undefined)counts[s.type]++;});
  const maxC=Math.max(...Object.values(counts),1);
  document.getElementById('chart-types').innerHTML=Object.entries(counts).map(([k,v])=>{const cl=allCLs[k];return `<div class="bc-row"><div class="bc-label">${cl?.icon||'📋'} ${cl?.label||k}</div><div class="bc-bar-wrap"><div class="bc-bar-fill" style="width:${Math.round((v/maxC)*100)}%"></div></div><div class="bc-count">${v}</div></div>`;}).join('');
  // (16/07/2026) Conformidade por ITENS verificados — um NC entre 20 ✓ não
  // zera o colaborador (antes: "conformes" = envios com zero NC → 1 NC = 0%).
  const porUser = {};
  subs.forEach(s => {
    const k = s.user || '?';
    porUser[k] = porUser[k] || { d: { login: k, name: s.userName || k }, envios: 0, ncs: 0, itensC: 0, itensTot: 0 };
    const u = porUser[k];
    u.envios++;
    const respostas = Object.values(s.answers || {});
    const verificados = respostas.filter(a => a && (a.val === 'C' || a.val === 'NC'));
    if (verificados.length) {
      u.itensC   += verificados.filter(a => a.val === 'C').length;
      u.itensTot += verificados.length;
      u.ncs      += verificados.filter(a => a.val === 'NC').length;
    } else {
      // Envio compacto (sem respostas no snapshot): conta no nível do envio
      const nc = _ncDe(s);
      u.ncs += nc;
      u.itensC += nc === 0 ? 1 : 0;
      u.itensTot += 1;
    }
  });
  const driversComEnvios = Object.values(porUser)
    .map(x => ({ ...x, pct: x.itensTot ? Math.round((x.itensC / x.itensTot) * 100) : 0 }))
    .sort((a,b) => (b.pct||0)-(a.pct||0));

  document.getElementById('compliance-list').innerHTML = driversComEnvios.map(({d,envios,ncs,itensC,itensTot,pct})=>{
    const color=pct>=80?'var(--success)':pct>=60?'var(--warn)':'var(--danger)';
    const funcao=d.funcao?FuncaoDB.byId(d.funcao):null;
    const tagHtml=funcao?`<span class="funcao-tag ${funcao.cor}" style="margin-left:6px">${funcao.nome}</span>`:'';
    const ncTxt = ncs > 0 ? ` • <span style="color:var(--danger);font-weight:700">${ncs} NC</span>` : ' • sem NC';
    return `<div class="compliance-item"><div class="ci-avatar">${d.name.charAt(0)}</div><div class="ci-info"><div class="ci-name">${d.name}${tagHtml}</div><div class="ci-pct">${envios} envio${envios>1?'s':''} • ${itensC}/${itensTot} itens conformes${ncTxt}</div></div><div class="ci-pct-val" style="color:${color}">${pct}%</div></div>`;
  }).join('')||'<div class="empty-state" style="font-size:13px;color:var(--text-light);padding:12px">Nenhum envio registrado ainda.</div>';
}

// ── RANKING ──
async function ajustarPontos(login, nome) {
  // Penalidade por má conduta (negativo) ou bônus (positivo) — só gestor.
  const v = prompt(`Ajustar pontos de ${nome}\n\nUse NEGATIVO para penalidade (ex: -20)\nou positivo para bônus:`, '-10');
  if (v === null) return;
  const pts = parseInt(v, 10);
  if (isNaN(pts) || pts === 0) { alert('Valor inválido.'); return; }
  const motivo = prompt('Motivo do ajuste (obrigatório):');
  if (!motivo || !motivo.trim()) return;
  try {
    const token = ckToken();
    const r = await fetch('/checklist/pontos-ajuste', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
      body: JSON.stringify({ login, pts, motivo: motivo.trim() })
    });
    const resp = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(resp.detail || 'Erro ao ajustar');
    if (typeof toast === 'function') toast(`✅ ${pts > 0 ? '+' : ''}${pts} pts — ${nome}`, 'success');
    renderRanking();
  } catch (e) { alert('❌ ' + e.message); }
}

async function renderRanking() {
  // Preload: indica busca no servidor
  const _pd0 = document.getElementById('podium');
  if (_pd0 && !_pd0.querySelector('.podium-place')) _pd0.innerHTML = '<div class="empty-state" style="opacity:.6">⏳ Carregando ranking…</div>';
  // Fonte única: SERVIDOR (checklist.envios) — 05/07/2026.
  // Período: ciclo ativo → config de pontos → geral. Offline: cai no local.
  try {
    const ciclo = (typeof CicloDB !== 'undefined' && CicloDB.atual && CicloDB.atual()) || null;
    const cfg   = PontosConfig.get();
    const ini = ciclo?.inicio || cfg.data_inicio || '';
    const fim = ciclo?.fim    || cfg.data_fim    || '';
    const qs = [ini?('inicio='+ini):'', fim?('fim='+fim):''].filter(Boolean).join('&');
    const token = ckToken();
    const r = await fetch('/checklist/ranking' + (qs?'?'+qs:''), {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' no /checklist/ranking');
    {
      let rk = await r.json();
      if (!rk.length && qs) {
        // Período do ciclo/config filtrou tudo — mostra o GERAL para não ficar vazio
        const r2 = await fetch('/checklist/ranking', { headers: { 'Authorization': 'Bearer ' + token } });
        if (r2.ok) rk = await r2.json();
      }
      const top3=rk.slice(0,3),rest=rk.slice(3);
      const medals=['🥇','🥈','🥉'],cls=['p1','p2','p3'];
      document.getElementById('podium').innerHTML=top3.length?top3.map((u,i)=>`<div class="podium-place ${cls[i]}" style="cursor:pointer" onclick="ajustarPontos('${u.login}','${sanitize(u.nome||u.login)}')" title="Clique para ajustar pontos"><div class="pp-medal">${medals[i]}</div><div class="pp-avatar">${sanitize(u.nome||u.login).charAt(0)}</div><div class="pp-name">${sanitize((u.nome||u.login).split(' ')[0])}</div><div class="pp-pts">${u.pts||0}</div></div>`).join(''):'<div class="empty-state">Sem envios no período ainda</div>';
      document.getElementById('full-ranking').innerHTML=rest.map((u,i)=>`<div class="rank-item" style="cursor:pointer" onclick="ajustarPontos('${u.login}','${sanitize(u.nome||u.login)}')" title="Clique para ajustar pontos"><div class="rank-pos">${i+4}</div><div class="rank-avatar">${sanitize(u.nome||u.login).charAt(0)}</div><div class="rank-info"><div class="rank-name">${sanitize(u.nome||u.login)}</div><div class="rank-sub">${u.envios||0} envios</div></div><div class="rank-pts">${u.pts||0} pts</div></div>`).join('')||'<div class="empty-state" style="padding:16px 0;font-size:13px">Apenas os 3 primeiros no pódio!</div>';
      // 📜 Extrato de ajustes (auditoria)
      try {
        const ra = await fetch('/checklist/pontos-ajustes', { headers: { 'Authorization': 'Bearer ' + token } });
        if (ra.ok) {
          const ajs = await ra.json();
          let sec = document.getElementById('ajustes-extrato');
          const fr = document.getElementById('full-ranking');
          if (!sec && fr) { sec = document.createElement('div'); sec.id = 'ajustes-extrato';
            sec.style.cssText = 'margin-top:18px'; fr.parentElement.appendChild(sec); }
          if (sec) sec.innerHTML = ajs.length ? `
            <div style="font-weight:800;font-size:13px;letter-spacing:.04em;margin-bottom:8px">📜 AJUSTES DE PONTOS (${ajs.length})</div>
            ${ajs.map(x => { let dt=''; try{dt=new Date(x.criado_em).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});}catch(_){}
              return `<div style="display:flex;gap:10px;align-items:baseline;padding:7px 10px;border-bottom:1px solid rgba(0,0,0,.06);font-size:13px">
                <span style="min-width:88px;color:#64748B;font-size:11px">${dt}</span>
                <strong style="min-width:110px">${sanitize(x.nome)}</strong>
                <span style="font-weight:800;color:${x.pts<0?'#DC2626':'#16A34A'}">${x.pts>0?'+':''}${x.pts} pts</span>
                <span style="color:#475569;flex:1">${sanitize(x.motivo)}</span>
              </div>`;}).join('')}` : '';
        }
      } catch(e) { console.error('[extrato]', e); }
      return;
    }
  } catch(e) {
    console.error('[ranking] caiu no local:', e);
    const pd = document.getElementById('podium');
    if (pd) pd.innerHTML = '<div class="empty-state" style="color:#DC2626">⚠️ Ranking do servidor indisponível: ' + (e.message||e) + '<br><small>Envie este texto para o suporte</small></div>';
    const fr = document.getElementById('full-ranking'); if (fr) fr.innerHTML = '';
    return;
  }
  const drivers=DB.users().filter(u=>u.role==='driver'||u.role==='diarista').sort((a,b)=>(b.pts||0)-(a.pts||0));
  const top3=drivers.slice(0,3),rest=drivers.slice(3);
  const medals=['🥇','🥈','🥉'],cls=['p1','p2','p3'];
  document.getElementById('podium').innerHTML=top3.length?top3.map((u,i)=>`<div class="podium-place ${cls[i]}" style="cursor:pointer" onclick="ajustarPontos('${u.login}','${sanitize(u.nome||u.login)}')" title="Clique para ajustar pontos"><div class="pp-medal">${medals[i]}</div><div class="pp-avatar">${sanitize(u.name).charAt(0)}</div><div class="pp-name">${sanitize(u.name.split(' ')[0])}</div><div class="pp-pts">${u.pts||0}</div></div>`).join(''):'<div class="empty-state">Sem dados de ranking ainda</div>';
  document.getElementById('full-ranking').innerHTML=rest.map((u,i)=>`<div class="rank-item" style="cursor:pointer" onclick="ajustarPontos('${u.login}','${sanitize(u.nome||u.login)}')" title="Clique para ajustar pontos"><div class="rank-pos">${i+4}</div><div class="rank-avatar">${sanitize(u.name).charAt(0)}</div><div class="rank-info"><div class="rank-name">${sanitize(u.name)}</div><div class="rank-sub">${u.submissions||0} envios</div></div><div class="rank-pts">${u.pts||0} pts</div></div>`).join('')||'<div class="empty-state" style="padding:16px 0;font-size:13px">Apenas os 3 primeiros no pódio!</div>';
}

// ── SUBMISSIONS ──
function renderSubmissions() {
  const typeF=document.getElementById('filter-type')?.value||'',userF=document.getElementById('filter-user')?.value||'',statusF=document.getElementById('filter-status')?.value||'';
  let subs=DB.submissions();
  if(typeF)subs=subs.filter(s=>s.type===typeF);
  if(userF)subs=subs.filter(s=>s.user===userF);
  if(statusF==='ok')subs=subs.filter(s=>countNC(s)===0&&!s.archived);
  if(statusF==='nc')subs=subs.filter(s=>countNC(s)>0);
  if(statusF==='archived')subs=subs.filter(s=>s.archived);
  const el=document.getElementById('submissions-list');
  if(!subs.length){el.innerHTML='<div class="empty-state"><div class="es-icon">📭</div>Nenhum envio encontrado.</div>';return;}
  const allCLs=DB.allCLs();
  el.innerHTML=subs.map(s=>{const cl=allCLs[s.type]||{},nc=countNC(s),st=s.archived?'archived':s.synced===false?'pending':nc>0?'nc':'ok',veh=s.meta?.veiculo||s.meta?.equipamento||'';
    return `<div class="sub-card ${st}" onclick="showSubmissionDetail('${s.id}')">
      <div class="sub-top">
        <div>
          <div class="sub-title">${cl.icon||'📋'} ${cl.label||s.type}</div>
          <div class="sub-meta">${s.userName} • ${formatDate(s.date)}</div>
          ${veh ? `<div class="sub-equip">🚜 ${veh}${s.meta?.local?' • 📍 '+s.meta.local:''}</div>` : (s.meta?.local?`<div class="sub-equip">📍 ${s.meta.local}</div>`:'')}
        </div>
        <div class="badge ${st}">${nc>0?nc+' NC':st==='pending'?'⏳':st==='archived'?'📦':'✓'}</div>
      </div>
      ${s.archived?'<div class="archived-tag">📦 Equipamento removido da frota</div>':''}
      ${nc>0&&s.meta?.observacoes?`<div class="sub-issues">⚠ ${s.meta.observacoes.slice(0,80)}${s.meta.observacoes.length>80?'...':''}</div>`:''}
    </div>`;
  }).join('');
}

function populateSubmissionFilters() {
  const allCLs=DB.allCLs();
  const ft=document.getElementById('filter-type');
  if(ft)ft.innerHTML='<option value="">Todos os tipos</option>'+Object.values(allCLs).map(cl=>`<option value="${cl.id}">${cl.icon} ${cl.label}</option>`).join('');
  const fu=document.getElementById('filter-user');
  if(fu)fu.innerHTML='<option value="">Todos</option>'+DB.users().filter(u=>u.role==='driver').map(u=>`<option value="${u.login}">${sanitize(u.name)}</option>`).join('');
}

// ── FLEET ──
function renderFleet() {
  const fleet=DB.fleet(),cats={maquinas:'🚜 Máquinas',carro:'🚗 Carros de Apoio',caminhao:'🚛 Caminhões'};
  document.getElementById('fleet-groups').innerHTML=Object.entries(cats).map(([cat,label])=>{
    const items=fleet[cat]||[];
    return `<div><div class="fleet-group-title">${label}</div><div class="fleet-list">${items.map(v=>`<div class="fleet-item ${v.active?'':'inactive'}"><div class="fleet-id">${v.id}</div><div class="fleet-desc">${v.desc||''}${!v.active?' <span class="inactive-badge">Inativo</span>':''}</div><div class="fleet-actions">${v.active?`<button class="fleet-btn edit" onclick="openFleetEdit('${cat}','${v.id}')">✎ Editar</button><button class="fleet-btn remove" onclick="openFleetRemove('${cat}','${v.id}')">✕ Remover</button>`:''}</div></div>`).join('')}${!items.length?'<div style="font-size:13px;color:var(--text-light);padding:8px">Nenhum equipamento cadastrado</div>':''}</div></div>`;
  }).join('');
}
function openFleetModal() {
  document.getElementById('fleet-modal-title').textContent='Novo Equipamento';
  document.getElementById('fleet-edit-key').value='';document.getElementById('fleet-id-input').value='';document.getElementById('fleet-desc-input').value='';document.getElementById('fleet-category').value='maquinas';
  openModal('fleet-modal');
}
function openFleetEdit(cat,id) {
  const item=(DB.fleet()[cat]||[]).find(v=>v.id===id); if(!item)return;
  document.getElementById('fleet-modal-title').textContent='Editar Equipamento';
  document.getElementById('fleet-edit-key').value=cat+'|'+id;document.getElementById('fleet-category').value=cat;document.getElementById('fleet-id-input').value=item.id;document.getElementById('fleet-desc-input').value=item.desc||'';
  openModal('fleet-modal');
}
async function saveFleetItem() {
  // (09/07/2026) Fonte única: equipamento se cadastra no Admin → Equipamentos
  alert('🚜 O cadastro de equipamentos é único: Admin → Cadastros → Equipamentos.\nCadastrou lá, aparece aqui na hora.');
  closeModal('fleet-modal');
}
function openFleetRemove(cat,id) {
  pendingRemoveFleetKey=cat+'|'+id;document.getElementById('fleet-remove-info').textContent=`Equipamento: ${id}`;openModal('fleet-remove-modal');
}
function confirmRemoveFleet() {
  alert('🚜 Para remover um equipamento, desative-o no Admin → Cadastros → Equipamentos.');
  pendingRemoveFleetKey=null;closeModal('fleet-remove-modal');
}

// ── CHECK LISTS TAB ──
function renderChecklistsTab() {
  document.getElementById('default-cl-list').innerHTML=Object.values(DEFAULT_CHECKLISTS).map(cl=>`<div class="ccl-item"><div class="ccl-icon">${cl.icon}</div><div class="ccl-body"><div class="ccl-name">${sanitize(cl.label)}</div><div class="ccl-meta">${cl.desc||''}</div><div class="ccl-pts">⭐ ${cl.scoreRules?.full||100} pts conforme / ${cl.scoreRules?.nc||60} pts com NC</div></div><div class="ccl-actions"><span class="role-badge driver">Padrão</span></div></div>`).join('');
  const customs=DB.customCLs(),customEl=document.getElementById('custom-cl-list');
  if(!customs.length){customEl.innerHTML='<div class="empty-state"><div class="es-icon">📝</div>Nenhum check list personalizado criado ainda.</div>';return;}
  customEl.innerHTML=customs.map(cl=>{
    const totalItems=(cl.questions||[]).filter(q=>q.type!=='section').length;
    return `<div class="ccl-item"><div class="ccl-icon">${cl.icon||'📋'}</div><div class="ccl-body"><div class="ccl-name">${sanitize(cl.label)}</div><div class="ccl-meta">${cl.desc||''} • ${totalItems} perguntas</div><div class="ccl-pts">⭐ Conforme: ${cl.scoreRules?.full||100}pts | NC: ${cl.scoreRules?.nc||60}pts</div></div><div class="ccl-actions"><button class="fleet-btn edit" onclick="openBuilder('${cl.id}')">✎ Editar</button><button class="fleet-btn remove" onclick="openCLRemove('${cl.id}')">✕ Excluir</button></div></div>`;
  }).join('');
}
function openCLRemove(id) {
  const cl=DB.customCLs().find(c=>c.id===id); if(!cl)return;
  pendingRemoveCLId=id;document.getElementById('cl-remove-info').textContent=`Check List: "${cl.label}"`;openModal('cl-remove-modal');
}
async function confirmRemoveCL() {
  if(!pendingRemoveCLId)return;
  try { await apiFetch('/checklist/modelos/'+pendingRemoveCLId, {method:'DELETE'}); }
  catch(e){ console.warn('Remove CL API falhou:', e.message); }
  DB.removeCustomCL(pendingRemoveCLId);
  pendingRemoveCLId=null;closeModal('cl-remove-modal');renderChecklistsTab();
}

// ── USUÁRIOS ──────────────────────────────────────
function usersSubTab(tab, btn) {
  document.querySelectorAll('#tab-users .log-subtab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('#tab-users .log-subpanel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('users-sub-'+tab).classList.add('active');
  if(tab==='funcoes') renderFuncoes();
  if(tab==='colaboradores'){populateFuncaoFilters();renderUsers();}
}

function renderUsers() {
  populateUserModalFuncoes();
  populateFuncaoFilters();
  const filterFuncao=document.getElementById('filter-funcao')?.value||'';
  const filterRole  =document.getElementById('filter-role')?.value||'';
  let users=DB.users();
  if(filterFuncao)users=users.filter(u=>u.funcao===filterFuncao);
  if(filterRole)  users=users.filter(u=>u.role===filterRole);
  const el=document.getElementById('users-list'); if(!el)return;
  const allCLs=DB.allCLs();
  el.innerHTML=users.map(u=>{
    const perfil=u.role==='manager'?'Gestor':u.role==='superior'?'Superior':u.role==='diarista'?'Diarista':'Operacional';
    const perfilColor=u.role==='manager'?'var(--orange)':u.role==='superior'?'var(--navy-light)':u.role==='diarista'?'var(--teal, #00897b)':'var(--navy)';
    const funcao=u.funcao?FuncaoDB.byId(u.funcao):null;
    const cor=funcao?(COR_MAP[funcao.cor]||COR_MAP.navy):'var(--navy)';
    const clsVisiveis=funcao?.cls?.length?funcao.cls.map(id=>allCLs[id]?.label).filter(Boolean).join(', '):'';
    return `<div class="user-item">
      <div class="user-item-top">
        <div class="ui-avatar" style="background:${perfilColor}">${sanitize(u.name).charAt(0)}</div>
        <div class="ui-info" style="flex:1">
          <div class="ui-name">${sanitize(u.name)}</div>
          <div class="ui-role">${u.login} • ${u.submissions||0} envios • ${u.pts||0} pts</div>
          ${clsVisiveis?`<div style="font-size:10px;color:var(--orange);margin-top:2px">📋 ${clsVisiveis}</div>`:''}
        </div>
        <div class="ui-actions">
          <button class="fleet-btn edit" onclick="openUserEdit('${u.login}')">✎</button>
          ${u.login!==currentUser.login?`<button class="fleet-btn remove" onclick="openUserRemove('${u.login}')">✕</button>`:''}
        </div>
      </div>
      <div class="user-item-tags">
        <span class="role-badge ${u.role}">${perfil}</span>
        ${funcao?`<span class="funcao-tag ${funcao.cor}" style="border:1px solid ${cor}22">${funcao.nome}</span>`:''}
        ${u.veiculo?`<span class="veiculo-tag">🚗 ${u.veiculo}</span>`:''}
      </div>
    </div>`;
  }).join('')||'<div class="empty-state"><div class="es-icon">👤</div>Nenhum colaborador encontrado.</div>';
}

function openUserEdit(login) {
  const u = DB.users().find(u => u.login === login);
  if (!u) return;
  editingUserLogin = login;

  // Popula funções e veículos PRIMEIRO
  populateUserModalFuncoes();

  // Preenche campos básicos
  document.getElementById('eu-name').value = u.name;
  document.getElementById('eu-login').textContent = u.login;
  document.getElementById('eu-role').value = u.role;
  document.getElementById('eu-pass').value = '';

  // Preenche função e veículo após render dos selects
  setTimeout(() => {
    const ef = document.getElementById('eu-funcao');
    const ev = document.getElementById('eu-veiculo');
    if (ef && u.funcao) ef.value = u.funcao;
    if (ev && u.veiculo) ev.value = u.veiculo;
  }, 100);

  openModal('user-edit-modal');
}

async function saveEditUser() {
  const name   = document.getElementById('eu-name').value.trim();
  const role   = document.getElementById('eu-role').value;
  const pass   = document.getElementById('eu-pass').value;
  const funcao = document.getElementById('eu-funcao')?.value  || '';
  const veiculo= document.getElementById('eu-veiculo')?.value || '';
  if (!name) { alert('Informe o nome.'); return; }

  const btn = document.querySelector('#user-edit-modal .btn-primary');
  if (btn) { btn.textContent = 'Salvando...'; btn.disabled = true; }

  try {
    // 1. SALVA NO BANCO PRIMEIRO
    await GarraDB.editarUsuario(editingUserLogin, {
      nome:   name,
      perfil: role,
      senha:  pass || null,
    });

    // 2. Atualiza localStorage com dados consistentes
    const users = DB.users();
    const idx   = users.findIndex(u => u.login === editingUserLogin);
    if (idx >= 0) {
      users[idx].name    = name;
      users[idx].role    = role;
      users[idx].funcao  = funcao;
      users[idx].veiculo = veiculo;
      if (pass) users[idx].pass = pass;
      DB.set('garra_users', users);
    }

    // 3. Atualiza currentUser se for o próprio usuário logado
    if (editingUserLogin === currentUser.login) {
      currentUser.name   = name;
      currentUser.role   = role;
      currentUser.funcao = funcao;
    }

    editingUserLogin = null;
    closeModal('user-edit-modal');
    renderUsers();
    populateSubmissionFilters();
    console.log('✅ Colaborador atualizado no banco:', name, role);

  } catch(e) {
    if (e.message === 'OFFLINE' || e.message === 'TIMEOUT') {
      // Offline: salva local e enfileira para sync
      const users = DB.users();
      const idx   = users.findIndex(u => u.login === editingUserLogin);
      if (idx >= 0) {
        users[idx].name=name; users[idx].role=role;
        users[idx].funcao=funcao; users[idx].veiculo=veiculo;
        if(pass) users[idx].pass=pass;
        DB.set('garra_users', users);
      }
      OfflineQueue.add({
        tipo: 'usuario',
        path: `/usuarios/${editingUserLogin}/editar`,
        options: { method:'POST', body: JSON.stringify({nome:name, perfil:role, senha:pass||null}) }
      });
      editingUserLogin = null;
      closeModal('user-edit-modal');
      renderUsers();
      alert('⚠️ Salvo localmente. Sincronizará quando online.');
    } else {
      alert('Erro ao salvar: ' + e.message);
    }
  } finally {
    if (btn) { btn.textContent = 'Salvar Alterações'; btn.disabled = false; }
  }
}

async function saveNewUser() {
  const name   = document.getElementById('nu-name').value.trim();
  const login  = document.getElementById('nu-user').value.trim().toLowerCase();
  const pass   = document.getElementById('nu-pass').value;
  const role   = document.getElementById('nu-role').value;
  const funcao = document.getElementById('nu-funcao')?.value  || '';
  const veiculo= document.getElementById('nu-veiculo')?.value || '';
  if (!name || !login || !pass) { alert('Preencha todos os campos.'); return; }
  const btn = document.querySelector('#user-modal .btn-primary');
  if (btn) { btn.textContent = 'Salvando...'; btn.disabled = true; }
  try {
    await GarraDB.criarUsuario({login,nome:name,senha:pass,perfil:role});
    DB.saveUser({name,login,pass,role,funcao,veiculo,pts:0,submissions:0});
    ['nu-name','nu-user','nu-pass'].forEach(id=>document.getElementById(id).value='');
    closeModal('user-modal');renderUsers();populateSubmissionFilters();
    alert('✅ Colaborador cadastrado com sucesso!');
  } catch(e) {
    if(e.message==='OFFLINE'){
      DB.saveUser({name,login,pass,role,funcao,veiculo,pts:0,submissions:0});
      OfflineQueue.add({tipo:'usuario',path:'/usuarios',options:{method:'POST',body:JSON.stringify({login,nome:name,senha:pass,perfil:role})}});
      ['nu-name','nu-user','nu-pass'].forEach(id=>document.getElementById(id).value='');
      closeModal('user-modal');renderUsers();
      alert('⚠️ Salvo localmente. Sincronizará quando online.');
    } else { alert('Erro: '+e.message); }
  } finally{if(btn){btn.textContent='Salvar';btn.disabled=false;}}
}

function openUserRemove(login) {
  const u=DB.users().find(u=>u.login===login); if(!u)return;
  pendingRemoveUserLogin=login;document.getElementById('user-remove-info').textContent=`Usuário: ${u.name} (${u.login})`;openModal('user-remove-modal');
}
async function confirmRemoveUser() {
  if(!pendingRemoveUserLogin)return;
  const loginParaRemover = pendingRemoveUserLogin;
  
  // 1. Remove do banco via API
  try {
    await GarraDB.removerUsuario(loginParaRemover);
    console.log('✅ Removido do banco:', loginParaRemover);
  } catch(e) {
    console.warn('⚠️ API remove falhou:', e.message);
  }
  
  // 2. Remove do localStorage imediatamente
  DB.removeUser(loginParaRemover);
  
  // 3. Marca na lista de deletados para não voltar na sync
  const deleted = DB.get('garra_deleted_users') || [];
  if (!deleted.includes(loginParaRemover)) {
    deleted.push(loginParaRemover);
    DB.set('garra_deleted_users', deleted);
  }
  
  // 4. Fecha e sincroniza
  pendingRemoveUserLogin = null;
  closeModal('user-remove-modal');
  await syncAllFromAPI();
  renderUsers();
  populateSubmissionFilters();
  console.log('✅ Usuário removido:', loginParaRemover);
}

// ── FUNÇÕES ──────────────────────────────────────
function openFuncaoModal(id) {
  funcaoEditId=id; funcaoCorSel='navy'; funcaoClsSel=[];
  const f=id?FuncaoDB.byId(id):null;
  document.getElementById('funcao-modal-title').textContent=id?'Editar Função':'Nova Função';
  document.getElementById('funcao-edit-id').value=id||'';
  document.getElementById('fc-nome').value=f?.nome||'';
  document.getElementById('fc-desc').value=f?.desc||'';
  if(f){funcaoCorSel=f.cor||'navy';funcaoClsSel=[...(f.cls||[])];}
  document.querySelectorAll('.color-opt').forEach(b=>b.classList.toggle('active',b.dataset.color===funcaoCorSel));
  const allCLs=DB.allCLs();
  document.getElementById('fc-cls-list').innerHTML=Object.values(allCLs).map(cl=>`
    <label class="funcao-cl-item">
      <input type="checkbox" value="${cl.id}" ${funcaoClsSel.includes(cl.id)?'checked':''} onchange="toggleFuncaoCL('${cl.id}',this.checked)" />
      <div class="funcao-cl-icon">${cl.icon}</div>
      <div><div class="funcao-cl-name">${sanitize(cl.label)}</div><div class="funcao-cl-desc">${cl.desc||''}</div></div>
    </label>`).join('');
  openModal('funcao-modal');
}
function selectFuncaoColor(cor,btn) {
  funcaoCorSel=cor;document.querySelectorAll('.color-opt').forEach(b=>b.classList.remove('active'));btn.classList.add('active');
}
function toggleFuncaoCL(clId,checked) {
  if(checked){if(!funcaoClsSel.includes(clId))funcaoClsSel.push(clId);}else funcaoClsSel=funcaoClsSel.filter(id=>id!==clId);
}
function saveFuncao() {
  const nome=document.getElementById('fc-nome').value.trim(); if(!nome){alert('Informe o nome da função.');return;}
  FuncaoDB.add({id:funcaoEditId||fidgen(),nome,desc:document.getElementById('fc-desc').value.trim(),cor:funcaoCorSel,cls:funcaoClsSel});
  funcaoEditId=null;closeModal('funcao-modal');renderFuncoes();populateFuncaoFilters();populateUserModalFuncoes();
}
function renderFuncoes() {
  const el=document.getElementById('funcoes-list'); if(!el)return;
  const funcoes=FuncaoDB.get(),allCLs=DB.allCLs(),usuarios=DB.users();
  if(!funcoes.length){el.innerHTML='<div class="empty-state"><div class="es-icon">🏷</div>Nenhuma função cadastrada.</div>';return;}
  el.innerHTML=funcoes.map(f=>{
    const cor=COR_MAP[f.cor]||COR_MAP.navy,count=usuarios.filter(u=>u.funcao===f.id).length;
    const clNames=(f.cls||[]).map(id=>allCLs[id]?.label).filter(Boolean);
    const clText=clNames.length?`✅ ${clNames.join(', ')}`:'✅ Todos os check lists';
    return `<div class="funcao-card"><div class="funcao-card-dot" style="background:${cor}"></div><div class="funcao-card-body"><div class="funcao-card-name">${sanitize(f.nome)}</div><div class="funcao-card-meta">${f.desc||''}${count>0?' • '+count+' colaborador(es)':''}</div><div class="funcao-card-cls">${clText}</div></div><div class="ui-actions"><button class="fleet-btn edit" onclick="openFuncaoModal('${f.id}')">✎</button><button class="fleet-btn remove" onclick="removeFuncao('${f.id}')">✕</button></div></div>`;
  }).join('');
}
function removeFuncao(id) {
  const f=FuncaoDB.byId(id),count=DB.users().filter(u=>u.funcao===id).length;
  if(!confirm(`Remover "${f?.nome}"?${count>0?' '+count+' colaborador(es) ficarão sem função.':''}`))return;
  FuncaoDB.remove(id);renderFuncoes();populateFuncaoFilters();renderUsers();
}
function populateFuncaoFilters() {
  const funcoes=FuncaoDB.get();
  const ff=document.getElementById('filter-funcao');
  if(ff)ff.innerHTML='<option value="">Todas as funções</option>'+funcoes.map(f=>`<option value="${f.id}">${sanitize(f.nome)}</option>`).join('');
}
function populateUserModalFuncoes() {
  const funcoes=FuncaoDB.get();
  const opts='<option value="">Selecione a função...</option>'+funcoes.map(f=>`<option value="${f.id}">${sanitize(f.nome)}</option>`).join('');
  ['nu-funcao','eu-funcao'].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML=opts;});
  const fleet=DB.fleet();
  const veiculos=[
    ...((fleet.maquinas||[]).filter(v=>v.active).map(v=>`<option value="${v.id}">🚜 ${v.id}${v.desc?' – '+v.desc:''}</option>`)),
    ...((fleet.carro   ||[]).filter(v=>v.active).map(v=>`<option value="${v.id}">🚗 ${v.id}${v.desc?' – '+v.desc:''}</option>`)),
    ...((fleet.caminhao||[]).filter(v=>v.active).map(v=>`<option value="${v.id}">🚛 ${v.id}${v.desc?' – '+v.desc:''}</option>`)),
  ].join('');
  const veiculoOpts='<option value="">Sem vínculo fixo</option>'+veiculos;
  ['nu-veiculo','eu-veiculo'].forEach(id=>{const el=document.getElementById(id);if(el)el.innerHTML=veiculoOpts;});
}

// ─── BUILDER ───────────────────────────────────────
const Q_TYPES = {
  checklist:{label:'✅ Conformidade',icon:'✅'},text:{label:'Tz Texto Curto',icon:'Tz'},textarea:{label:'¶ Parágrafo',icon:'¶'},
  radio:{label:'◉ Múltipla escolha',icon:'◉'},checkbox:{label:'☑ Caixas de seleção',icon:'☑'},select:{label:'▾ Lista suspensa',icon:'▾'},
  scale:{label:'⭐ Escala',icon:'⭐'},date:{label:'📅 Data',icon:'📅'},photo:{label:'📷 Foto',icon:'📷'},section:{label:'── Divisor de seção',icon:'──'},
};
function defaultQuestion(type) {
  const base={id:qid(),type,label:'',hint:'',required:false,pts:1,conditionalOn:'',conditionalValue:'',photoMode:'off'};
  if(['radio','checkbox','select'].includes(type))base.options=[{id:oid(),label:'Opção 1',pts:1},{id:oid(),label:'Opção 2',pts:1}];
  if(type==='scale'){base.scaleMin=1;base.scaleMax=5;base.scaleMinLabel='';base.scaleMaxLabel='';}
  if(type==='section'){base.label='Nova Seção';base.hint='';}
  return base;
}
function addQuestion(type) {
  const q=defaultQuestion(type);builderQuestions.push(q);renderBuilderQuestions();
  setTimeout(()=>{const el=document.getElementById('blq-'+q.id);if(el)el.scrollIntoView({behavior:'smooth',block:'center'});},80);
  focusQuestion(q.id);
}
function removeBuilderQuestion(qId) { builderQuestions=builderQuestions.filter(q=>q.id!==qId);renderBuilderQuestions(); }
function duplicateQuestion(qId) {
  const idx=builderQuestions.findIndex(q=>q.id===qId); if(idx<0)return;
  const clone=JSON.parse(JSON.stringify(builderQuestions[idx]));clone.id=qid();
  if(clone.options)clone.options=clone.options.map(o=>({...o,id:oid()}));
  builderQuestions.splice(idx+1,0,clone);renderBuilderQuestions();
}
function moveQuestion(qId,dir) {
  const idx=builderQuestions.findIndex(q=>q.id===qId),newIdx=idx+dir;
  if(newIdx<0||newIdx>=builderQuestions.length)return;
  [builderQuestions[idx],builderQuestions[newIdx]]=[builderQuestions[newIdx],builderQuestions[idx]];
  renderBuilderQuestions();
}
function focusQuestion(qId) {
  builderFocusId=qId;document.querySelectorAll('.bl-question').forEach(el=>el.classList.remove('focused'));
  const el=document.getElementById('blq-'+qId);if(el)el.classList.add('focused');
}
function getQ(qId) { return builderQuestions.find(q=>q.id===qId); }
function blUpdateLabel(qId,val)    { const q=getQ(qId);if(q)q.label=val; }
function blUpdateHint(qId,val)     { const q=getQ(qId);if(q)q.hint=val; }
function blUpdateRequired(qId,val) { const q=getQ(qId);if(q)q.required=val; }
function blUpdatePts(qId,val)      { const q=getQ(qId);if(q)q.pts=parseInt(val)||1; }
function blUpdateType(qId,val) {
  const q=getQ(qId);if(!q)return;q.type=val;
  if(['radio','checkbox','select'].includes(val)&&!q.options)q.options=[{id:oid(),label:'Opção 1',pts:1},{id:oid(),label:'Opção 2',pts:1}];
  if(val==='scale'){if(!q.scaleMin)q.scaleMin=1;if(!q.scaleMax)q.scaleMax=5;}
  renderBuilderQuestions();focusQuestion(qId);
}
function blUpdateOptLabel(qId,oId,val) { const q=getQ(qId);if(!q)return;const o=q.options?.find(o=>o.id===oId);if(o)o.label=val; }
function blUpdateOptPts(qId,oId,val)   { const q=getQ(qId);if(!q)return;const o=q.options?.find(o=>o.id===oId);if(o)o.pts=parseInt(val)||1; }
function blAddOption(qId) { const q=getQ(qId);if(!q||!q.options)return;q.options.push({id:oid(),label:`Opção ${q.options.length+1}`,pts:1});renderBuilderQuestions();focusQuestion(qId); }
function blRemoveOption(qId,oId) { const q=getQ(qId);if(!q||!q.options||q.options.length<=1)return;q.options=q.options.filter(o=>o.id!==oId);renderBuilderQuestions();focusQuestion(qId); }
function blUpdateScale(qId,field,val) {
  const q=getQ(qId);if(!q)return;
  if(field==='min')q.scaleMin=parseInt(val)||1;if(field==='max')q.scaleMax=parseInt(val)||10;
  if(field==='minLabel')q.scaleMinLabel=val;if(field==='maxLabel')q.scaleMaxLabel=val;
  renderBuilderQuestions();focusQuestion(qId);
}
function blUpdateCond(qId,field,val) { const q=getQ(qId);if(q)q[field]=val; }
function blSetPhotoMode(qId,mode) { const q=getQ(qId);if(q)q.photoMode=mode;renderBuilderQuestions();focusQuestion(qId); }

function renderBuilderQuestions() {
  const el=document.getElementById('builder-questions');if(!el)return;
  if(!builderQuestions.length){el.innerHTML=`<div style="text-align:center;padding:30px 20px;color:var(--text-light);font-size:13px;background:var(--white);border-radius:var(--radius);box-shadow:var(--shadow-sm)"><div style="font-size:32px;margin-bottom:8px">📋</div>Use os botões abaixo para adicionar perguntas.</div>`;return;}
  el.innerHTML=builderQuestions.map((q,idx)=>q.type==='section'?renderSectionBlock(q,idx):renderQuestionBlock(q,idx)).join('');
}

function renderSectionBlock(q,idx) {
  return `<div class="bl-section-card" id="blq-${q.id}" onclick="focusQuestion('${q.id}')">
    <input class="bl-section-title-input" type="text" value="${esc(q.label)}" placeholder="Título da Seção" oninput="blUpdateLabel('${q.id}',this.value)" />
    <input class="bl-section-desc-input" type="text" value="${esc(q.hint)}" placeholder="Descrição da seção (opcional)" oninput="blUpdateHint('${q.id}',this.value)" />
    <div class="bl-section-footer">${idx>0?`<button class="bl-section-btn" onclick="moveQuestion('${q.id}',-1)">↑</button>`:''}${idx<builderQuestions.length-1?`<button class="bl-section-btn" onclick="moveQuestion('${q.id}',1)">↓</button>`:''}<button class="bl-section-btn" style="color:rgba(255,100,100,.7)" onclick="removeBuilderQuestion('${q.id}')">✕ Remover</button></div>
  </div>`;
}

function renderQuestionBlock(q,idx) {
  const typeOpts=Object.entries(Q_TYPES).filter(([k])=>k!=='section').map(([k,v])=>`<option value="${k}" ${q.type===k?'selected':''}>${v.label}</option>`).join('');
  const prevChoiceQs=builderQuestions.slice(0,idx).filter(pq=>['radio','checkbox','select','checklist'].includes(pq.type)&&pq.label);
  const condHtml=prevChoiceQs.length?`<div class="bl-cond-wrap"><div class="bl-cond-title">🔀 Lógica condicional</div><div class="bl-cond-row"><span>Mostrar se</span><select class="bl-cond-sel" onchange="blUpdateCond('${q.id}','conditionalOn',this.value)"><option value="">Sempre exibir</option>${prevChoiceQs.map(pq=>`<option value="${pq.id}" ${q.conditionalOn===pq.id?'selected':''}>${esc(pq.label.slice(0,40))}</option>`).join('')}</select>${q.conditionalOn?(()=>{const pq=getQ(q.conditionalOn);if(!pq)return'';if(pq.type==='checklist')return`<span>for</span><select class="bl-cond-sel" onchange="blUpdateCond('${q.id}','conditionalValue',this.value)"><option value="NC" ${q.conditionalValue==='NC'?'selected':''}>Não Conforme</option><option value="C" ${q.conditionalValue==='C'?'selected':''}>Conforme</option><option value="NA" ${q.conditionalValue==='NA'?'selected':''}>N/A</option></select>`;if(pq.options)return`<span>for</span><select class="bl-cond-sel" onchange="blUpdateCond('${q.id}','conditionalValue',this.value)">${pq.options.map(o=>`<option value="${o.label}" ${q.conditionalValue===o.label?'selected':''}>${esc(o.label)}</option>`).join('')}</select>`;return'';})():''}</div></div>`:'';
  return `<div class="bl-question ${builderFocusId===q.id?'focused':''}" id="blq-${q.id}" onclick="focusQuestion('${q.id}')">
    <div class="bl-q-header"><div class="bl-q-drag" title="Mover">⠿</div><div class="bl-q-main">
      <div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:6px">
        <input class="bl-q-label-input" style="flex:1" type="text" value="${esc(q.label)}" placeholder="Pergunta ${idx+1}" oninput="blUpdateLabel('${q.id}',this.value)" />
        <select class="bl-q-type-sel" onchange="blUpdateType('${q.id}',this.value)">${typeOpts}</select>
      </div>
      <input class="bl-q-hint-input" type="text" value="${esc(q.hint)}" placeholder="Texto de ajuda (opcional)" oninput="blUpdateHint('${q.id}',this.value)" />
    </div></div>
    <div class="bl-q-body">${renderQuestionBody(q)}${condHtml}</div>
    <div class="bl-q-footer">
      <div class="bl-q-pts-wrap"><span>⭐ Peso</span><input class="bl-q-pts" type="number" value="${q.pts||1}" min="0" max="99" oninput="blUpdatePts('${q.id}',this.value)" /><span>pts</span></div>
      <label class="bl-q-toggle"><input type="checkbox" ${q.required?'checked':''} onchange="blUpdateRequired('${q.id}',this.checked)" /> Obrigatória</label>
      ${idx>0?`<button class="bl-q-btn" onclick="moveQuestion('${q.id}',-1)">↑</button>`:''}
      ${idx<builderQuestions.length-1?`<button class="bl-q-btn" onclick="moveQuestion('${q.id}',1)">↓</button>`:''}
      <button class="bl-q-btn dupe" onclick="duplicateQuestion('${q.id}')">⧉</button>
      <button class="bl-q-btn danger" onclick="removeBuilderQuestion('${q.id}')">🗑</button>
    </div>
  </div>`;
}

function renderQuestionBody(q) {
  switch(q.type) {
    case 'checklist':
      return `<div class="bl-conf-preview"><div class="bl-conf-btn ok">✓ Conforme</div><div class="bl-conf-btn nc">✗ Não Conforme</div><div class="bl-conf-btn na">N/A</div></div>
      <div style="font-size:11px;color:var(--text-light);margin-top:8px;margin-bottom:10px">Campo de observação aparece automaticamente ao marcar Não Conforme.</div>
      <div class="bl-photo-toggle-wrap"><div class="bl-photo-toggle-row"><div class="bl-photo-toggle-icon">📷</div><div class="bl-photo-toggle-body"><div class="bl-photo-toggle-title">Upload de foto</div><div class="bl-photo-toggle-desc">${q.photoMode==='nc_only'?'Somente em NC':q.photoMode==='always'?'Sempre disponível':'Desabilitado'}</div></div></div>
      <div class="bl-photo-mode-btns"><button class="bl-photo-mode-btn ${q.photoMode==='off'||!q.photoMode?'active':''}" onclick="blSetPhotoMode('${q.id}','off')">🚫 Off</button><button class="bl-photo-mode-btn ${q.photoMode==='always'?'active':''}" onclick="blSetPhotoMode('${q.id}','always')">✅ Sempre</button><button class="bl-photo-mode-btn ${q.photoMode==='nc_only'?'active accent':''}" onclick="blSetPhotoMode('${q.id}','nc_only')">⚠ Só em NC</button></div></div>`;
    case 'text':     return `<div style="border-bottom:1px solid var(--gray-light);padding:8px 0;font-size:13px;color:var(--gray)">Resposta de texto curto</div>`;
    case 'textarea': return `<div style="border:1px solid var(--gray-light);border-radius:6px;padding:8px;font-size:13px;color:var(--gray);min-height:52px">Resposta de parágrafo</div>`;
    case 'date':     return `<div style="border:1.5px solid var(--gray-light);border-radius:6px;padding:8px 10px;font-size:13px;color:var(--gray);display:inline-flex;align-items:center;gap:6px">📅 dd/mm/aaaa</div>`;
    case 'photo':    return `<div class="bl-photo-preview"><div class="pi">📷</div>Foto da galeria ou câmera</div>`;
    case 'radio': case 'checkbox': case 'select': {
      const icon=q.type==='radio'?'◉':q.type==='checkbox'?'☐':'▾';
      return `<div class="bl-options-list">${(q.options||[]).map(o=>`<div class="bl-option-row"><span class="bl-option-icon">${icon}</span><input class="bl-option-input" type="text" value="${esc(o.label)}" placeholder="Opção..." oninput="blUpdateOptLabel('${q.id}','${o.id}',this.value)" /><div title="pts" style="display:flex;align-items:center;gap:3px"><span style="font-size:10px;color:var(--gray)">pts</span><input class="bl-option-pts" type="number" value="${o.pts||1}" min="0" max="99" oninput="blUpdateOptPts('${q.id}','${o.id}',this.value)" /></div><button class="bl-q-btn danger" onclick="blRemoveOption('${q.id}','${o.id}')">✕</button></div>`).join('')}</div><button class="bl-add-option" onclick="blAddOption('${q.id}')">+ Adicionar opção</button>`;
    }
    case 'scale': {
      const min=q.scaleMin||1,max=q.scaleMax||5,nums=Array.from({length:Math.min(max-min+1,10)},(_,i)=>min+i);
      return `<div class="bl-scale-config"><div class="field-group"><label>Mínimo</label><input type="number" value="${min}" min="0" max="9" style="padding:7px 10px" oninput="blUpdateScale('${q.id}','min',this.value)" /></div><div class="field-group"><label>Máximo</label><input type="number" value="${max}" min="2" max="10" style="padding:7px 10px" oninput="blUpdateScale('${q.id}','max',this.value)" /></div><div class="field-group"><label>Label mín.</label><input type="text" value="${esc(q.scaleMinLabel||'')}" placeholder="Ex.: Péssimo" oninput="blUpdateScale('${q.id}','minLabel',this.value)" /></div><div class="field-group"><label>Label máx.</label><input type="text" value="${esc(q.scaleMaxLabel||'')}" placeholder="Ex.: Excelente" oninput="blUpdateScale('${q.id}','maxLabel',this.value)" /></div></div><div class="bl-scale-preview">${nums.map(n=>`<div class="bl-scale-num">${n}</div>`).join('')}</div><div class="bl-scale-label-row"><span>${q.scaleMinLabel||''}</span><span>${q.scaleMaxLabel||''}</span></div>`;
    }
    default: return '';
  }
}

function openBuilder(clId) {
  builderEditId=clId;builderQuestions=[];builderFocusId=null;
  if(clId){const cl=DB.customCLs().find(c=>c.id===clId);if(cl){document.getElementById('bl-name').value=cl.label;document.getElementById('bl-icon').value=cl.icon||'';document.getElementById('bl-vehicle-cat').value=cl.vehicleCat||'maquinas';document.getElementById('bl-desc').value=cl.desc||'';document.getElementById('pts-full').value=cl.scoreRules?.full??100;document.getElementById('pts-nc-base').value=cl.scoreRules?.nc??60;document.getElementById('pts-obs').value=cl.scoreRules?.obs??20;document.getElementById('pts-ontime').value=cl.scoreRules?.ontime??10;builderQuestions=JSON.parse(JSON.stringify(cl.questions||[]));document.getElementById('builder-screen-title').textContent='Editar Check List';}}
  else{document.getElementById('bl-name').value='';document.getElementById('bl-icon').value='📋';document.getElementById('bl-vehicle-cat').value='maquinas';document.getElementById('bl-desc').value='';document.getElementById('pts-full').value=100;document.getElementById('pts-nc-base').value=60;document.getElementById('pts-obs').value=20;document.getElementById('pts-ontime').value=10;builderQuestions=[];document.getElementById('builder-screen-title').textContent='Novo Check List';}
  renderBuilderQuestions();showScreen('screen-builder');
}
function closeBuilder() { if(currentUser?.role==='superior')showSuperior();else showManager(); }

function previewChecklist() {
  if(!builderQuestions.length){alert('Adicione perguntas primeiro.');return;}
  DB.saveCustomCL(buildCLObject('__preview__'));startChecklist('__preview__');
}

async function saveChecklist() {
  const name=document.getElementById('bl-name').value.trim(); if(!name){alert('Informe o título.');return;}
  if(!builderQuestions.filter(q=>q.type!=='section').length){alert('Adicione pelo menos uma pergunta.');return;}
  const id=builderEditId&&builderEditId!=='__preview__'?builderEditId:('cl_'+Date.now()+'_'+Math.random().toString(36).slice(2,5));
  const clObj = buildCLObject(id);

  // Salva local
  DB.saveCustomCL(clObj);
  DB.removeCustomCL('__preview__');

  // Salva no banco
  try {
    await apiFetch('/checklist/modelos', {
      method: 'POST',
      body: JSON.stringify({
        cl_id:       clObj.id,
        label:       clObj.label,
        icon:        clObj.icon        || '📋',
        descricao:   clObj.desc        || '',
        vehicle_cat: clObj.vehicleCat  || 'none',
        is_default:  false,
        score_full:  clObj.scoreRules?.full   || 100,
        score_nc:    clObj.scoreRules?.nc     || 60,
        score_obs:   clObj.scoreRules?.obs    || 20,
        score_ontime:clObj.scoreRules?.ontime || 10,
        questions:   clObj.questions   || [],
        steps:       clObj.steps       || [],
      })
    });
    console.log('✅ Check list salvo no banco:', clObj.label);
  } catch(e) {
    console.warn('⚠️ Check list salvo local, banco falhou:', e.message);
  }

  if(currentUser?.role==='superior'){showSuperior();}
  else{showManager();document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.tab-panel').forEach(p=>p.classList.remove('active'));const tabBtn=document.querySelector('[onclick*="checklists"]');if(tabBtn)tabBtn.classList.add('active');document.getElementById('tab-checklists').classList.add('active');renderChecklistsTab();}
}

function buildCLObject(id) {
  const steps=[],vehicleCat=document.getElementById('bl-vehicle-cat').value;
  const metaFields=[{id:'operador',label:'Responsável',type:'text',placeholder:'Seu nome'},{id:'local',label:'Local',type:'text',placeholder:'Local da verificação'},{id:'data',label:'Data',type:'date'}];
  if(vehicleCat!=='none')metaFields.push({id:'veiculo',label:'Equipamento/Veículo',type:'select',options:'vehicles'});
  else metaFields.push({id:'veiculo',label:'Equipamento/Veículo',type:'text',placeholder:'Identifique o equipamento'});
  steps.push({title:'Identificação',sub:'Preencha os dados iniciais',type:'meta',fields:metaFields});
  let curTitle='Perguntas',curItems=[];
  function flush(){if(!curItems.length)return;steps.push({title:curTitle,sub:'',type:curItems.some(q=>q.type==='checklist')?'checklist':'custom',items:curItems.map(q=>{const b={id:q.id,label:q.label,type:q.type,required:q.required,pts:q.pts,hint:q.hint,conditionalOn:q.conditionalOn,conditionalValue:q.conditionalValue,photoMode:q.photoMode};if(q.options)b.options=q.options;if(q.scaleMin)b.scaleMin=q.scaleMin;if(q.scaleMax)b.scaleMax=q.scaleMax;if(q.scaleMinLabel)b.scaleMinLabel=q.scaleMinLabel;if(q.scaleMaxLabel)b.scaleMaxLabel=q.scaleMaxLabel;return b;})});curItems=[];}
  builderQuestions.forEach(q=>{if(q.type==='section'){flush();curTitle=q.label||'Seção';}else curItems.push(q);});flush();
  steps.push({title:'Observações',sub:'Registre problemas ou observações gerais',type:'obs',fields:[{id:'observacoes',label:'Observações / Comentários',type:'textarea',placeholder:'Descreva...'}]});
  return{id,isDefault:false,label:document.getElementById('bl-name').value.trim(),icon:document.getElementById('bl-icon').value.trim()||'📋',desc:document.getElementById('bl-desc').value.trim(),vehicleCat,scoreRules:{full:parseInt(document.getElementById('pts-full').value)||100,nc:parseInt(document.getElementById('pts-nc-base').value)||60,obs:parseInt(document.getElementById('pts-obs').value)||20,ontime:parseInt(document.getElementById('pts-ontime').value)||10},questions:builderQuestions,steps};
}

// ─── DETALHE ───────────────────────────────────────
function showSubmissionDetail(id) {
  const sub=DB.submissions().find(s=>s.id===id); if(!sub)return;
  const cl=DB.allCLs()[sub.type]||{},nc=countNC(sub),st=sub.archived?'archived':sub.synced===false?'pending':nc>0?'nc':'ok';
  const veiculo = sub.meta?.veiculo || sub.meta?.equipamento || '';
  const local   = sub.meta?.local   || '';
  let html=`<div class="detail-section">
    <h4>Informações Gerais</h4>
    <div class="detail-row"><span class="dr-label">Check List</span><span class="dr-val">${cl.label||sub.type}</span></div>
    <div class="detail-row"><span class="dr-label">Colaborador</span><span class="dr-val">${sanitize(sub.userName)}</span></div>
    ${veiculo ? `<div class="detail-row"><span class="dr-label">Equipamento</span><span class="dr-val" style="font-weight:700;color:var(--navy)">🚜 ${veiculo}</span></div>` : ''}
    ${local   ? `<div class="detail-row"><span class="dr-label">Local</span><span class="dr-val">📍 ${local}</span></div>` : ''}
    <div class="detail-row"><span class="dr-label">Data/Hora</span><span class="dr-val">${formatDateTime(sub.date)}</span></div>
    <div class="detail-row"><span class="dr-label">Status</span><span class="dr-val ${st}">${st==='ok'?'✓ Conforme':st==='pending'?'⏳ Sync':st==='archived'?'📦 Removido':'⚠ '+nc+' NC'}</span></div>
    <div class="detail-row"><span class="dr-label">Pontuação</span><span class="dr-val" style="color:var(--orange)">+${sub.pts||0} pts</span></div>
  </div>`;
  if(sub.archived)html+=`<div class="detail-section" style="border-left:4px solid var(--gray)"><h4 style="color:var(--gray)">📦 Equipamento Removido</h4><p style="font-size:13px;color:var(--text-light)">Histórico mantido para auditoria.</p></div>`;
  const metaLabels={operador:'Operador',local:'Local',data:'Data',equipamento:'Equipamento',veiculo:'Veículo',km:'KM',horimetro:'Horímetro',tipo:'Tipo',situacao:'Situação'};
  const metaEntries=Object.entries(sub.meta||{}).filter(([k,v])=>k!=='observacoes'&&k!=='ot'&&v);
  if(metaEntries.length)html+=`<div class="detail-section"><h4>Identificação</h4>${metaEntries.map(([k,v])=>`<div class="detail-row"><span class="dr-label">${metaLabels[k]||k}</span><span class="dr-val">${v}</span></div>`).join('')}</div>`;
  cl.steps?.filter(s=>s.type==='checklist').forEach(step=>{html+=`<div class="detail-section"><h4>${step.title}</h4>${step.items.map(item=>{const ans=sub.answers?.[item.id];if(!ans)return'';const c=ans.val==='C'?'ok':ans.val==='NC'?'nc':'na',l=ans.val==='C'?'✓ Conforme':ans.val==='NC'?'✗ Não Conforme':'N/A';return `<div class="detail-row"><span class="dr-label">${item.label}${item.pts>1?` <small style="color:var(--orange)">⭐×${item.pts}</small>`:''}</span><span class="dr-val ${c}">${l}</span></div>${ans.obs?`<div class="detail-obs">📝 ${ans.obs}</div>`:''}`}).join('')}</div>`;});
  if(sub.meta?.observacoes)html+=`<div class="detail-section"><h4>Observações Gerais</h4><div class="detail-obs">${sub.meta.observacoes}</div></div>`;
  document.getElementById('detail-content').innerHTML=html;showScreen('screen-detail');
}

// ─── MODAIS / UTILS ────────────────────────────────
function openModal(id)  { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }
function formatDate(iso)     { if(!iso)return'–';const s=String(iso);const d=/^\d{4}-\d{2}-\d{2}$/.test(s)?new Date(s+'T00:00'):new Date(s);return d.toLocaleDateString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric'}); }
function formatDateTime(iso) { if(!iso)return'–';return new Date(iso).toLocaleString('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'}); }

// ─── SERVICE WORKER ────────────────────────────────
// Não registrar SW quando em iframe embedded (o app shell pai já tem)
const _isEmbedded = new URLSearchParams(window.location.search).get('embedded') === '1';
if ('serviceWorker' in navigator && !_isEmbedded) {
  navigator.serviceWorker.register('./sw.js', {scope: './'}).then(reg => {
    console.log('[App] Service Worker registrado ✅');

    // Verifica atualização a cada 5 minutos
    setInterval(() => reg.update(), 5 * 60 * 1000);

    // Nova versão disponível
    reg.addEventListener('updatefound', () => {
      const newSW = reg.installing;
      newSW.addEventListener('statechange', () => {
        if (newSW.state === 'installed' && navigator.serviceWorker.controller) {
          showUpdateBanner();
        }
      });
    });

    // Quando SW ativa novo, recarrega
    navigator.serviceWorker.addEventListener('controllerchange', () => {
      window.location.reload();
    });

  }).catch(err => console.warn('[App] SW não registrado:', err));
} else {
  console.warn('[App] Service Worker não suportado neste navegador');
}

function showUpdateBanner() {
  if (document.getElementById('sw-update-banner')) return;
  const b = document.createElement('div');
  b.id = 'sw-update-banner';
  b.style.cssText = 'position:fixed;bottom:70px;left:50%;transform:translateX(-50%);background:var(--navy);color:var(--white);padding:12px 18px;border-radius:10px;font-size:13px;font-family:var(--fb);box-shadow:0 4px 20px rgba(0,0,0,.3);z-index:9999;display:flex;align-items:center;gap:12px;max-width:90vw;';
  b.innerHTML = '<span>🔄 Nova versão disponível!</span><button onclick="atualizarApp()" style="background:var(--orange);color:white;border:none;padding:6px 12px;border-radius:6px;font-size:12px;font-weight:700;cursor:pointer">Atualizar agora</button><button onclick="this.parentElement.remove()" style="background:none;border:none;color:rgba(255,255,255,.6);cursor:pointer;font-size:16px">✕</button>';
  document.body.appendChild(b);
}

function atualizarApp() {
  if (navigator.serviceWorker.controller) navigator.serviceWorker.controller.postMessage('SKIP_WAITING');
  window.location.reload(true);
}

// ─── SINCRONIZA USUÁRIOS DA API ────────────────────
async function syncCustomCLsFromAPI() {
  try {
    const modelos = await apiFetch('/checklist/modelos');
    if (!modelos?.length) return;
    // Filtra só os personalizados (não padrão)
    const customs = modelos.filter(m => !m.is_default);
    if (!customs.length) return;
    // Converte formato da API para formato local
    const cls = customs.map(m => ({
      id:          m.cl_id,
      label:       m.label,
      icon:        m.icon        || '📋',
      desc:        m.descricao   || '',
      vehicleCat:  m.vehicle_cat || 'none',
      isDefault:   false,
      scoreRules:  { full: m.score_full, nc: m.score_nc, obs: m.score_obs, ontime: m.score_ontime },
      questions:   m.questions   || [],
      steps:       m.steps       || [],
    }));
    // Salva no localStorage
    DB.set('garra_custom_cls', cls);
    console.log('✅ Check lists personalizados sincronizados:', cls.length);
  } catch(e) {
    console.warn('⚠️ Sync CLs falhou:', e.message);
  }
}

async function syncAllFromAPI() {
  // Em PARALELO — antes era sequencial (um await esperando o outro),
  // o que triplicava o tempo de boot em conexões móveis.
  await Promise.all([
    syncUsersFromAPI(),
    syncFrotaFromAPI(),
    syncCustomCLsFromAPI(),
  ]);
}

async function syncFrotaFromAPI() {
  try {
    const data = await GarraDB.getFrota();
    if (!data?.length) return;
    // Mescla com dados locais para não perder equipamentos
    const apiFleet   = { maquinas: [], carro: [], caminhao: [] };
    // (09/07/2026) Dedup GLOBAL: a mesma identificação não entra em duas
    // categorias (dados antigos tinham CAs também como caminhão).
    const vistosGlobal = new Set();
    const ordem = ['maquinas','carro','caminhao'];
    ordem.forEach(cat => {
      data.filter(item => item.categoria === cat).forEach(item => {
        if (vistosGlobal.has(item.identificacao)) return;
        vistosGlobal.add(item.identificacao);
        apiFleet[cat].push({
          id:     item.identificacao,
          desc:   item.descricao || '',
          active: item.ativo !== false,
        });
      });
    });
    // (09/07/2026) SERVIDOR É A FONTE: nada de mesclar sobras do localStorage
    // — equipamento excluído no Admin ressuscitava do cache local (CB-030 etc.).
    // O snapshot local serve só para leitura offline e é sobrescrito aqui.
    DB.set('garra_fleet', apiFleet);
    console.log('✅ Frota sincronizada:', data.length, 'itens do banco');
  } catch(e) { console.warn('⚠️ Sync frota falhou (offline?):', e.message); }
}

async function syncUsersFromAPI() {
  try {
    Cache.del('usuarios'); // Sempre busca dados frescos
    const apiUsers = await GarraDB.getUsuarios();
    if (!apiUsers?.length) return;

    const local   = DB.users();
    const deleted = DB.get('garra_deleted_users') || [];

    const merged = apiUsers
      .filter(au => !deleted.includes(au.login))
      .map(au => {
        const loc = local.find(l => l.login === au.login) || {};
        return {
          login:       au.login,
          name:        au.nome   || loc.name || au.login,
          role:        au.perfil || loc.role || 'driver',  // banco tem prioridade
          pass:        loc.pass  || '***',
          funcao:      loc.funcao  || '',
          veiculo:     loc.veiculo || '',
          // Banco é sempre a fonte da verdade para pts
          pts:         au.pts         || 0,
          submissions: au.total_envios || 0,
        };
      });

    // Só mantém usuários locais com senha real (criados offline)
    // Não re-adiciona usuários do seed ou deletados
    local.forEach(lu => {
      const isDeleted  = deleted.includes(lu.login);
      const inMerged   = merged.find(m => m.login === lu.login);
      const hasRealPass= lu.pass && lu.pass !== '***' && lu.pass.length >= 4;
      if (!isDeleted && !inMerged && hasRealPass) {
        merged.push(lu);
      }
    });

    DB.set('garra_users', merged);
    console.log('✅ Sync concluída:', merged.length, 'usuários');
  } catch(e) {
    console.warn('⚠️ Sync falhou (offline?):', e.message);
  }
}

// ─── SYNC CHECK LISTS PERSONALIZADOS DO BANCO ─────

async function saveCustomCLToAPI(cl) {
  try {
    await GarraDB.salvarModelo(cl);
    console.log('✅ CL salvo no banco:', cl.label);
  } catch(e) {
    console.warn('⚠️ CL salvo só local:', e.message);
  }
}

// ─── INIT ──────────────────────────────────────────
updateSyncUI();
// syncAllFromAPI já inclui usuários + frota + modelos (em paralelo).
// As 2 chamadas extras de syncCustomCLsFromAPI foram removidas — o
// /checklist/modelos era buscado 3x a cada abertura do app.
syncAllFromAPI();



// ═══════════════════════════════════════════════════
// MÓDULO: CICLOS DE PONTUAÇÃO
// Gestor define intervalo, fecha ciclo, histórico completo
// ═══════════════════════════════════════════════════

const CicloDB = {
  get()         { return DB.get('garra_ciclos') || []; },
  save(list)    { DB.set('garra_ciclos', list); },
  atual()       { return DB.get('garra_ciclo_atual') || null; },
  saveAtual(c)  { DB.set('garra_ciclo_atual', c); },
  addCiclo(c)   { const list=this.get(); list.unshift(c); this.save(list); },
};

function cidgen() { return 'cic_'+Date.now()+'_'+Math.random().toString(36).slice(2,5); }

// ── INICIAR NOVO CICLO ──────────────────────────────
function openNovoCiclo() {
  const atual = CicloDB.atual();
  if (atual) {
    if (!confirm(`Já existe um ciclo ativo: "${atual.nome}".\nPara iniciar um novo, feche o ciclo atual primeiro.`)) return;
    return;
  }
  document.getElementById('ciclo-nome').value = '';
  document.getElementById('ciclo-inicio').value = new Date(Date.now() - new Date().getTimezoneOffset()*60000).toISOString().slice(0,10);
  document.getElementById('ciclo-fim').value = '';
  document.getElementById('ciclo-descricao').value = '';
  // Sugestão de datas
  const hoje = new Date();
  const fim30 = new Date(hoje.getTime() + 30*86400000);
  document.getElementById('ciclo-fim').value = new Date(fim30.getTime() - fim30.getTimezoneOffset()*60000).toISOString().slice(0,10);
  openModal('ciclo-modal');
}

function salvarNovoCiclo() {
  const nome  = document.getElementById('ciclo-nome').value.trim();
  const inicio= document.getElementById('ciclo-inicio').value;
  const fim   = document.getElementById('ciclo-fim').value;
  const desc  = document.getElementById('ciclo-descricao').value.trim();
  if (!nome)   { alert('Informe o nome do ciclo.'); return; }
  if (!inicio) { alert('Informe a data de início.'); return; }
  if (!fim)    { alert('Informe a data de término.'); return; }
  if (new Date(fim) <= new Date(inicio)) { alert('A data de término deve ser após o início.'); return; }

  // Snapshot dos pontos atuais (pontos no início do ciclo = 0 para todos)
  const users = DB.users();
  const snapshot = users.map(u => ({ login: u.login, name: u.name, funcao: u.funcao || '', pts_inicio: 0, submissions_inicio: u.submissions || 0 }));

  const ciclo = {
    id: cidgen(), nome, inicio, fim, desc,
    snapshot,
    criado_em: new Date().toISOString(),
    status: 'ativo',
  };
  CicloDB.saveAtual(ciclo);
  closeModal('ciclo-modal');
  renderRankingTab();
  alert(`✅ Ciclo "${nome}" iniciado!\nPeríodo: ${formatDate(inicio)} até ${formatDate(fim)}`);
}

// ── FECHAR CICLO ────────────────────────────────────
function fecharCiclo() {
  const ciclo = CicloDB.atual();
  if (!ciclo) { alert('Nenhum ciclo ativo no momento.'); return; }
  if (!confirm(`Fechar o ciclo "${ciclo.nome}"?\n\nIsso irá:\n✅ Registrar o ranking final no histórico\n✅ Zerar os pontos de todos os colaboradores\n\nEssa ação não pode ser desfeita.`)) return;

  const users = DB.users();
  const drivers = users.filter(u => u.role === 'driver');

  // Calcula pontos conquistados no ciclo
  const ranking = drivers.map(u => {
    const snap = ciclo.snapshot?.find(s => s.login === u.login);
    const ptsNoCiclo = (u.pts || 0) - (snap?.pts_inicio || 0);
    const subsNoCiclo = (u.submissions || 0) - (snap?.submissions_inicio || 0);
    return { login: u.login, name: u.name, funcao: u.funcao || '', pts: Math.max(0, ptsNoCiclo), submissions: Math.max(0, subsNoCiclo) };
  }).sort((a,b) => b.pts - a.pts);

  // Salva ciclo fechado com ranking final
  const cicloFechado = {
    ...ciclo,
    status: 'fechado',
    fechado_em: new Date().toISOString(),
    ranking,
    vencedor: ranking[0] || null,
    podio: ranking.slice(0,3),
  };
  CicloDB.addCiclo(cicloFechado);
  CicloDB.saveAtual(null);

  // Zera pontos de todos os colaboradores
  const usersAtualizados = users.map(u => ({ ...u, pts: 0, submissions: 0 }));
  DB.set('garra_users', usersAtualizados);

  closeModal('ciclo-fechar-modal');
  renderRankingTab();
  renderOverview();

  // Mostra tela de encerramento
  showCicloEncerrado(cicloFechado);
}

function openFecharCiclo() {
  const ciclo = CicloDB.atual();
  if (!ciclo) return;
  document.getElementById('ciclo-fechar-nome').textContent = ciclo.nome;
  document.getElementById('ciclo-fechar-periodo').textContent = `${formatDate(ciclo.inicio)} → ${formatDate(ciclo.fim)}`;
  const drivers = DB.users().filter(u => u.role === 'driver' || u.role === 'diarista').sort((a,b)=>(b.pts||0)-(a.pts||0));
  document.getElementById('ciclo-fechar-preview').innerHTML = drivers.slice(0,5).map((u,i) => {
    const snap = ciclo.snapshot?.find(s => s.login === u.login);
    const pts = Math.max(0, (u.pts||0) - (snap?.pts_inicio||0));
    const medal = i===0?'🥇':i===1?'🥈':i===2?'🥉':'';
    return `<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--gray-light)">
      <span style="font-size:16px;min-width:22px">${medal||'#'+(i+1)}</span>
      <span style="flex:1;font-size:13px;font-weight:600">${sanitize(u.name)}</span>
      <span style="font-size:13px;color:var(--orange);font-weight:700">${pts} pts</span>
    </div>`;
  }).join('') || '<div style="font-size:13px;color:var(--text-light)">Nenhum colaborador com envios neste ciclo.</div>';
  openModal('ciclo-fechar-modal');
}

function showCicloEncerrado(ciclo) {
  const vencedor = ciclo.vencedor;
  const podio    = ciclo.podio || [];
  const medals   = ['🥇','🥈','🥉'];
  document.getElementById('ciclo-enc-nome').textContent    = ciclo.nome;
  document.getElementById('ciclo-enc-periodo').textContent = `${formatDate(ciclo.inicio)} → ${formatDate(ciclo.fechado_em)}`;
  document.getElementById('ciclo-enc-podio').innerHTML = podio.map((u,i) => `
    <div class="podium-place ${['p1','p2','p3'][i]}">
      <div class="pp-medal">${medals[i]}</div>
      <div class="pp-avatar">${sanitize(u.name).charAt(0)}</div>
      <div class="pp-name">${sanitize(u.name.split(' ')[0])}</div>
      <div class="pp-pts">${u.pts} pts</div>
    </div>`).join('') || '<div style="color:var(--text-light);font-size:13px">Sem pontuações registradas.</div>';
  openModal('ciclo-encerrado-modal');
}

// ── RENDERIZAR ABA RANKING COM CICLOS ──────────────
function renderRankingTab() {
  const cicloAtual = CicloDB.atual();
  const hoje = new Date();

  // Atualiza visibilidade dos botões
  const btnNovo   = document.getElementById('btn-novo-ciclo');
  const btnFechar = document.getElementById('btn-fechar-ciclo');
  if (btnNovo)   btnNovo.style.display   = cicloAtual ? 'none' : '';
  if (btnFechar) btnFechar.style.display = cicloAtual ? '' : 'none';

  // Banner do ciclo ativo
  const bannerEl = document.getElementById('ciclo-banner');
  if (bannerEl) {
    if (cicloAtual) {
      const diasRestantes = Math.max(0, Math.ceil((new Date(cicloAtual.fim) - hoje) / 86400000));
      const urgente = diasRestantes <= 3;
      bannerEl.innerHTML = `
        <div class="ciclo-banner ${urgente ? 'urgente' : 'ativo'}">
          <div class="ciclo-banner-info">
            <div class="ciclo-banner-nome">🏆 ${cicloAtual.nome}</div>
            <div class="ciclo-banner-periodo">${formatDate(cicloAtual.inicio)} → ${formatDate(cicloAtual.fim)}</div>
            ${cicloAtual.desc ? `<div class="ciclo-banner-desc">${cicloAtual.desc}</div>` : ''}
          </div>
          <div class="ciclo-banner-right">
            <div class="ciclo-dias ${urgente ? 'urgente' : ''}">${diasRestantes}</div>
            <div class="ciclo-dias-label">dias restantes</div>
            <button class="btn-danger ciclo-fechar-btn" onclick="openFecharCiclo()">Fechar Ciclo</button>
          </div>
        </div>`;
    } else {
      bannerEl.innerHTML = `
        <div class="ciclo-banner inativo">
          <div class="ciclo-banner-info">
            <div class="ciclo-banner-nome">⏸ Nenhum ciclo ativo</div>
            <div class="ciclo-banner-periodo">Inicie um ciclo para acompanhar a pontuação por período</div>
          </div>
          <button class="btn-primary" onclick="openNovoCiclo()" style="white-space:nowrap;flex-shrink:0">🏆 + Novo Ciclo</button>
        </div>`;
    }
  }

  // Ranking (05/07/2026): fonte ÚNICA = SERVIDOR via renderRanking().
  // O bloco local que existia aqui era o 3º escritor de #podium e causava a
  // race condition do "ranking que some" — o ciclo agora só delega.
  renderRanking();

  // Histórico de ciclos fechados
  renderHistoricoCiclos();
}

// ── HISTÓRICO DE CICLOS ─────────────────────────────
function renderHistoricoCiclos() {
  const el = document.getElementById('ciclos-historico');
  if (!el) return;
  const ciclos = CicloDB.get();
  if (!ciclos.length) {
    el.innerHTML = '<div class="empty-state"><div class="es-icon">📅</div>Nenhum ciclo fechado ainda.</div>';
    return;
  }
  el.innerHTML = ciclos.map(c => {
    const podio = c.podio || [];
    const medals = ['🥇','🥈','🥉'];
    return `
      <div class="ciclo-card" onclick="toggleCicloDetalhes('${c.id}')">
        <div class="ciclo-card-header">
          <div class="ciclo-card-info">
            <div class="ciclo-card-nome">🏆 ${c.nome}</div>
            <div class="ciclo-card-periodo">${formatDate(c.inicio)} → ${formatDate(c.fechado_em)}</div>
          </div>
          <div class="ciclo-card-vencedor">
            ${podio[0] ? `🥇 ${podio[0].name.split(' ')[0]} — ${podio[0].pts} pts` : 'Sem vencedor'}
          </div>
          <div class="ciclo-card-toggle" id="toggle-${c.id}">▼</div>
        </div>
        <div class="ciclo-card-detalhes hidden" id="detalhes-${c.id}">
          <div class="ciclo-podio-mini">
            ${podio.map((u,i) => `<div class="ciclo-podio-item"><span>${medals[i]}</span><span>${sanitize(u.name)}</span><span style="color:var(--orange);font-weight:700">${u.pts} pts</span></div>`).join('')}
          </div>
          <div class="ciclo-ranking-completo">
            <div style="font-size:11px;font-weight:700;color:var(--text-light);text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">Ranking Completo</div>
            ${(c.ranking||[]).map((u,i) => `
              <div class="ciclo-rank-row">
                <span class="ciclo-rank-pos">${i+1}º</span>
                <span class="ciclo-rank-name">${sanitize(u.name)}</span>
                <span class="ciclo-rank-sub">${u.submissions} envios</span>
                <span class="ciclo-rank-pts">${u.pts} pts</span>
              </div>`).join('')}
          </div>
          ${c.desc ? `<div style="font-size:12px;color:var(--text-light);margin-top:10px;font-style:italic">${c.desc}</div>` : ''}
        </div>
      </div>`;
  }).join('');
}

function toggleCicloDetalhes(id) {
  const el  = document.getElementById('detalhes-'+id);
  const tog = document.getElementById('toggle-'+id);
  if (!el) return;
  el.classList.toggle('hidden');
  if (tog) tog.textContent = el.classList.contains('hidden') ? '▼' : '▲';
}

// ── HELPER: preencher datas do ciclo rapidamente ──
function preencherPeriodo(dias) {
  const hoje = new Date();
  const fim  = new Date(hoje.getTime() + dias*86400000);
  document.getElementById('ciclo-inicio').value = new Date(hoje.getTime() - hoje.getTimezoneOffset()*60000).toISOString().slice(0,10);
  document.getElementById('ciclo-fim').value    = new Date(fim.getTime() - fim.getTimezoneOffset()*60000).toISOString().slice(0,10);
  const nomes = {15:'Quinzenal',30:'Mensal',60:'Bimestral',365:'Anual'};
  const mes   = hoje.toLocaleString('pt-BR',{month:'long',year:'numeric'});
  document.getElementById('ciclo-nome').value   = `${nomes[dias]||dias+'d'} — ${mes}`;
}

// renderRankingTab é chamado diretamente no mgrTab via renderManagerDashboard

// ── TOGGLE SENHA ────────────────────────────────────
function toggleSenha(inputId, btn) {
  const input = document.getElementById(inputId);
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    btn.textContent = '🔒';
    btn.title = 'Ocultar senha';
  } else {
    input.type = 'password';
    btn.textContent = '👁';
    btn.title = 'Mostrar senha';
  }
}

// ═══════════════════════════════════════════════════
// MÓDULO: CONFIGURAÇÃO DE VISIBILIDADE DE PONTOS
// ═══════════════════════════════════════════════════

function renderPontosConfigPanel() {
  const el = document.getElementById('pontos-config-panel');
  if (!el || currentUser?.role !== 'manager') return;
  // Não redesenhar enquanto o gestor está DIGITANDO nas datas —
  // o refresh automático da aba apagava o campo no meio da escolha.
  const ae = document.activeElement;
  if (ae && (ae.id === 'pontos-inicio' || ae.id === 'pontos-fim')) return;

  const cfg    = PontosConfig.get();
  const ativo  = cfg.ativo;
  // Re-render PRESERVA o que está na tela (o picker nativo tira o foco do
  // input e o auto-refresh apagava a data escolhida antes do Salvar — 06/07/2026)
  const _exI = document.getElementById('pontos-inicio');
  const _exF = document.getElementById('pontos-fim');
  const inicio = _exI ? _exI.value : (cfg.data_inicio || '');
  const fim    = _exF ? _exF.value : (cfg.data_fim    || '');

  el.innerHTML = `
    <div class="pontos-config-card">
      <div class="pontos-config-header">
        <div>
          <div class="pontos-config-title">🏅 Visibilidade de Pontos</div>
          <div class="pontos-config-sub">Controle o que os colaboradores veem</div>
        </div>
        <label class="toggle-switch">
          <input type="checkbox" id="pontos-ativo" ${ativo ? 'checked' : ''}
            onchange="salvarPontosConfig()" />
          <span class="toggle-slider"></span>
        </label>
      </div>

      <div class="pontos-config-body ${ativo ? '' : 'disabled'}">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px">
          <div class="field-group" style="margin:0">
            <label style="font-size:11px;font-weight:700;color:var(--text-light);display:block;margin-bottom:4px">📅 Contar a partir de</label>
            <input type="date" id="pontos-inicio" value="${inicio}"
              style="padding:8px 10px;border:1.5px solid var(--gray-light);border-radius:var(--radius-sm);font-size:13px;width:100%;background:var(--white);color:var(--navy,#1A2A5E);color-scheme:light" />
          </div>
          <div class="field-group" style="margin:0">
            <label style="font-size:11px;font-weight:700;color:var(--text-light);display:block;margin-bottom:4px">📅 Até (opcional)</label>
            <input type="date" id="pontos-fim" value="${fim}"
              style="padding:8px 10px;border:1.5px solid var(--gray-light);border-radius:var(--radius-sm);font-size:13px;width:100%;background:var(--white);color:var(--navy,#1A2A5E);color-scheme:light" />
          </div>
        </div>
        <button onclick="salvarPontosConfig(false)"
          style="margin-top:10px;padding:9px 18px;border:none;border-radius:8px;background:var(--orange,#E8820C);color:#fff;font-weight:700;font-size:13px;cursor:pointer">💾 Salvar período</button>
        <div class="pontos-config-preview" id="pontos-preview">
          ${ativo
            ? `✅ Colaboradores <strong>veem</strong> seus pontos${inicio ? ' a partir de <strong>' + formatDate(inicio) + '</strong>' : ''}${fim ? ' até <strong>' + formatDate(fim) + '</strong>' : ''}`
            : `⏸ Colaboradores <strong>não veem</strong> pontos — exibe "Pontuação em breve"`
          }
        </div>
      </div>
    </div>`;
}

function salvarPontosConfig(rerender = true) {
  const ativo  = document.getElementById('pontos-ativo')?.checked || false;
  const inicio = document.getElementById('pontos-inicio')?.value  || null;
  const fim    = document.getElementById('pontos-fim')?.value     || null;
  const cfg = { ativo, data_inicio: inicio, data_fim: fim };
  PontosConfig.save(cfg);

  // Fonte única: grava no SERVIDOR (todos os aparelhos leem a mesma regra)
  try {
    const token = ckToken();
    fetch('/checklist/pontos-config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
      body: JSON.stringify(cfg)
    }).then(r => { if (r.ok && typeof toast === 'function') toast('✅ Configuração salva', 'success'); })
      .catch(() => {});
  } catch(e) {}

  if (rerender) {
    // Só o toggle re-renderiza (habilita/desabilita o corpo).
    // As DATAS não re-renderizam — era isso que matava o campo no meio da escolha.
    renderPontosConfigPanel();
    renderRankingTab();
  } else {
    const pv = document.getElementById('pontos-preview');
    if (pv) pv.innerHTML = ativo
      ? `✅ Colaboradores <strong>veem</strong> seus pontos${inicio ? ' a partir de <strong>' + formatDate(inicio) + '</strong>' : ''}${fim ? ' até <strong>' + formatDate(fim) + '</strong>' : ''}`
      : `⏸ Colaboradores <strong>não veem</strong> pontos — exibe "Pontuação em breve"`;
  }
}

// Carrega a config OFICIAL do servidor no boot (cache local = fallback offline)
(async function carregarPontosConfigServidor() {
  try {
    const token = ckToken();
    if (!token) return;
    const r = await fetch('/checklist/pontos-config', {
      headers: { 'Authorization': 'Bearer ' + token }
    });
    if (r.ok) {
      const cfg = await r.json();
      DB.set('garra_pontos_config', cfg);
      try { if (typeof renderPontosConfigPanel === 'function') renderPontosConfigPanel(); } catch(e) {}
    }
  } catch(e) { /* offline: usa cache local */ }
})();

// Hook no renderRankingTab para incluir painel de config
const _renderRankingTabOrig = renderRankingTab;
window.renderRankingTab = function() {
  _renderRankingTabOrig();
  renderPontosConfigPanel();
};
