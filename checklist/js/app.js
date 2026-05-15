/* ═══════════════════════════════════════════════════
   app.js — Garra Check List System v2
   Offline-first PWA | Gestão completa de frota,
   usuários, check lists customizados e pontuação
═══════════════════════════════════════════════════ */

// ─── ESTADO GLOBAL ─────────────────────────────────
let currentUser  = null;
let currentCLId  = null;   // ID do checklist em execução
let currentStep  = 0;
let formAnswers  = {};
let formMeta     = {};

// Temporário para remoções pendentes confirmação
let pendingRemoveFleetKey  = null;
let pendingRemoveUserLogin = null;
let pendingRemoveCLId      = null;

// ─── STORAGE ───────────────────────────────────────
const DB = {
  get: k => { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),

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
  removeUser(login) {
    this.set('garra_users', this.users().filter(u => u.login !== login));
  },
  saveSubmission(s) {
    const list = this.submissions();
    const dup = list.findIndex(x => x.id === s.id);
    if (dup >= 0) list[dup] = s; else list.unshift(s);
    this.set('garra_submissions', list);
  },
  addPending(s) {
    const p = this.pendingSync(); p.push(s); this.set('garra_pending', p);
  },
  clearPending() { this.set('garra_pending', []); },

  // Fleet helpers
  getFleetVehicles(cat) {
    return (this.fleet()[cat] || []).filter(v => v.active);
  },
  getAllFleetVehicles(cat) {
    return this.fleet()[cat] || [];
  },
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
    // Archive related submissions
    const subs = this.submissions().map(s => {
      const vid = s.meta?.veiculo || s.meta?.equipamento || '';
      if (vid === id) s.archived = true;
      return s;
    });
    this.set('garra_submissions', subs);
  },

  // Custom CL
  saveCustomCL(cl) {
    const list = this.customCLs();
    const idx = list.findIndex(x => x.id === cl.id);
    if (idx >= 0) list[idx] = cl; else list.push(cl);
    this.set('garra_custom_cls', list);
  },
  removeCustomCL(id) {
    this.set('garra_custom_cls', this.customCLs().filter(c => c.id !== id));
  },

  // All CLs (default + custom)
  allCLs() {
    const customs = this.customCLs();
    return { ...DEFAULT_CHECKLISTS, ...Object.fromEntries(customs.map(c => [c.id, c])) };
  },
};

function seedUsers() {
  const users = [
    { login:'admin',     name:'Administrador Garra', pass:'garra2024', role:'manager',  pts:0, submissions:0 },
    { login:'gestor',    name:'Gestor de Frota',     pass:'garra2024', role:'manager',  pts:0, submissions:0 },
    { login:'gilson',    name:'Gilson',              pass:'garra2024', role:'superior', pts:0, submissions:0 },
    { login:'marco',     name:'Marco Aurélio',       pass:'garra2024', role:'superior', pts:0, submissions:0 },
    { login:'andre',     name:'André',        pass:'123456', role:'driver', pts:580, submissions:18 },
    { login:'emerson',   name:'Emerson',      pass:'123456', role:'driver', pts:420, submissions:14 },
    { login:'samuel',    name:'Samuel',       pass:'123456', role:'driver', pts:390, submissions:13 },
    { login:'franciele', name:'Franciele',    pass:'123456', role:'driver', pts:350, submissions:12 },
    { login:'gilberto',  name:'Gilberto',     pass:'123456', role:'driver', pts:310, submissions:10 },
    { login:'geraldo',   name:'Geraldo',      pass:'123456', role:'driver', pts:280, submissions:9  },
    { login:'joao',      name:'João Pedro',   pass:'123456', role:'driver', pts:260, submissions:8  },
    { login:'marcio',    name:'Márcio',       pass:'123456', role:'driver', pts:240, submissions:8  },
    { login:'motorista', name:'Motorista Demo',pass:'123456',role:'driver', pts:0,   submissions:0  },
  ];
  DB.set('garra_users', users);
  return users;
}
function seedFleet() {
  DB.set('garra_fleet', DEFAULT_FLEET);
  return DEFAULT_FLEET;
}

// ─── CONNECTIVITY ──────────────────────────────────
let isOnline = navigator.onLine;
window.addEventListener('online',  () => { isOnline = true;  updateSyncUI(); syncNow(); });
window.addEventListener('offline', () => { isOnline = false; updateSyncUI(); });

function updateSyncUI() {
  const online = isOnline;
  ['sync-dot','mgr-sync-dot','sup-sync-dot'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.className = 'dot ' + (online ? 'online' : 'offline');
  });
  ['sync-label','mgr-sync-label','sup-sync-label'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.textContent = online ? 'Online' : 'Offline';
  });
  const badge = document.getElementById('offline-badge');
  if (badge) badge.style.display = online ? 'none' : 'flex';

  const pending = DB.pendingSync();
  const banner = document.getElementById('pending-banner');
  const cnt    = document.getElementById('pending-count');
  if (banner) banner.style.display = pending.length > 0 ? 'flex' : 'none';
  if (cnt) cnt.textContent = pending.length;
}

function syncNow() {
  if (!isOnline) return;
  const pending = DB.pendingSync();
  if (!pending.length) return;
  pending.forEach(s => { s.synced = true; DB.saveSubmission(s); });
  DB.clearPending();
  updateSyncUI();
  if (currentUser?.role === 'driver') renderDriverDashboard();
}

// ─── AUTH ───────────────────────────────────────────
function doLogin() {
  const login = (document.getElementById('login-user').value || '').trim().toLowerCase();
  const pass  =  document.getElementById('login-pass').value || '';
  const err   =  document.getElementById('login-error');
  const user  = DB.users().find(u => u.login === login && u.pass === pass);
  if (!user) { err.classList.remove('hidden'); return; }
  err.classList.add('hidden');
  currentUser = user;
  if (user.role === 'manager') showManager();
  else if (user.role === 'superior') showSuperior();
  else showDriver();
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
  renderSuperiorDashboard();
}
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
  const allCLs = DB.allCLs();
  el.innerHTML = Object.values(allCLs).map(cl => `
    <div class="cl-card" onclick="startChecklist('${cl.id}')">
      <div class="clc-icon">${cl.icon}</div>
      <div class="clc-body"><div class="clc-name">${cl.label}</div><div class="clc-desc">${cl.desc||''}</div></div>
      <div class="clc-arrow">›</div>
    </div>`).join('');
}
function showManager() {
  showScreen('screen-manager');
  document.getElementById('user-badge-mgr').textContent = currentUser.name.charAt(0).toUpperCase();
  renderManagerDashboard();
}
function goBack()        { if (currentUser?.role==='manager') showManager(); else if (currentUser?.role==='superior') showSuperior(); else showDriver(); }
function goToDashboard() { if (currentUser?.role==='manager') showManager(); else if (currentUser?.role==='superior') showSuperior(); else showDriver(); }
function closeDetail()   { if (currentUser?.role==='manager') showManager(); else if (currentUser?.role==='superior') showSuperior(); else showDriver(); }

// ─── DRIVER DASHBOARD ──────────────────────────────
function renderDriverDashboard() {
  const u = DB.users().find(u => u.login === currentUser.login);
  if (!u) return;
  currentUser = u;

  document.getElementById('driver-pts').textContent   = u.pts || 0;
  document.getElementById('driver-streak').textContent = `🔥 ${u.submissions || 0} envios`;

  const drivers = DB.users().filter(u => u.role === 'driver').sort((a,b) => (b.pts||0)-(a.pts||0));
  const rank = drivers.findIndex(d => d.login === u.login) + 1;
  document.getElementById('driver-rank').textContent = '#' + rank;

  const maxPts = Math.max(...drivers.map(d => d.pts||0), 1);
  document.getElementById('driver-bar').style.width = Math.round(((u.pts||0)/maxPts)*100) + '%';

  // CL cards – default + custom
  const allCLs = DB.allCLs();
  const cardsEl = document.getElementById('driver-cl-cards');
  cardsEl.innerHTML = Object.values(allCLs).map(cl => `
    <div class="cl-card" onclick="startChecklist('${cl.id}')">
      <div class="clc-icon">${cl.icon}</div>
      <div class="clc-body">
        <div class="clc-name">${cl.label}</div>
        <div class="clc-desc">${cl.desc || ''}</div>
      </div>
      <div class="clc-arrow">›</div>
    </div>`).join('');

  // History
  const subs  = DB.submissions().filter(s => s.user === u.login);
  const histEl = document.getElementById('driver-history');
  if (!subs.length) {
    histEl.innerHTML = '<div class="empty-state"><div class="es-icon">📋</div>Nenhum check list enviado ainda!</div>';
    return;
  }
  histEl.innerHTML = subs.slice(0,15).map(s => {
    const cl  = DB.allCLs()[s.type] || {};
    const nc  = countNC(s);
    const st  = s.archived ? 'archived' : (s.synced === false ? 'pending' : (nc > 0 ? 'nc' : 'ok'));
    const lbl = s.archived ? '📦 Equip. Removido' : st === 'pending' ? '⏳ Sync pendente' : nc > 0 ? `⚠ ${nc} NC` : '✓ Conforme';
    const veh = s.meta?.veiculo || s.meta?.equipamento || '';
    return `<div class="history-item" onclick="showSubmissionDetail('${s.id}')">
      <div class="hi-icon">${cl.icon||'📋'}</div>
      <div class="hi-body">
        <div class="hi-title">${cl.label||s.type}${veh ? ' – '+veh : ''}</div>
        <div class="hi-meta">${formatDate(s.date)}${s.meta?.local ? ' • '+s.meta.local : ''}</div>
      </div>
      <div class="badge ${st}">${lbl}</div>
    </div>`;
  }).join('');
}

// ─── CHECKLIST FORM ────────────────────────────────
function startChecklist(clId) {
  currentCLId  = clId;
  currentStep  = 0;
  formAnswers  = {};
  formMeta     = {};
  renderFormStep();
  showScreen('screen-form');
}

function getCL() { return DB.allCLs()[currentCLId]; }

function renderFormStep() {
  const cl   = getCL();
  const step = cl.steps[currentStep];
  const total = cl.steps.length;

  document.getElementById('form-title').textContent = `${cl.icon} ${cl.label}`;
  document.getElementById('form-step-label').textContent = `${currentStep + 1} / ${total}`;
  document.getElementById('form-prog-bar').style.width = Math.round((currentStep / total) * 100) + '%';
  document.getElementById('btn-prev').style.visibility = currentStep === 0 ? 'hidden' : 'visible';
  document.getElementById('btn-next').textContent = currentStep === total - 1 ? 'Enviar ✓' : 'Próximo';

  const content = document.getElementById('form-content');

  if (step.type === 'meta') {
    const vehicles = DB.getFleetVehicles(cl.vehicleCat || '').map(v => v.id);
    content.innerHTML = `<div class="form-step">
      <div class="form-step-title">${step.title}</div>
      <div class="form-step-sub">${step.sub}</div>
      ${step.fields.map(f => renderMetaField(f, vehicles)).join('')}
    </div>`;
    // defaults
    const dateEl = document.getElementById('meta-data');
    if (dateEl && !formMeta.data) dateEl.value = new Date().toISOString().slice(0,10);
    step.fields.forEach(f => {
      const el = document.getElementById('meta-'+f.id);
      if (el && formMeta[f.id]) el.value = formMeta[f.id];
    });
  } else if (step.type === 'checklist' || step.type === 'custom') {
    content.innerHTML = `<div class="form-step">
      <div class="form-step-title">${step.title}</div>
      <div class="form-step-sub">${step.sub||''}</div>
      ${step.items.map(item => renderCheckItem(item)).join('')}
    </div>`;
  } else if (step.type === 'obs') {
    content.innerHTML = `<div class="form-step">
      <div class="form-step-title">${step.title}</div>
      <div class="form-step-sub">${step.sub}</div>
      ${step.fields.map(f => renderObsField(f)).join('')}
    </div>`;
    step.fields.forEach(f => {
      const el = document.getElementById('obs-'+f.id);
      if (el && formMeta[f.id]) el.value = formMeta[f.id];
    });
  }
  content.scrollTop = 0;
}

function renderMetaField(f, vehicles) {
  if (f.type === 'select') {
    const opts = f.options === 'vehicles' ? vehicles : (f.options || []);
    return `<div class="form-meta-field"><label>${f.label}</label>
      <select id="meta-${f.id}">
        <option value="">Selecione...</option>
        ${opts.map(o => `<option value="${o}">${o}</option>`).join('')}
      </select></div>`;
  }
  return `<div class="form-meta-field"><label>${f.label}</label>
    <input type="${f.type}" id="meta-${f.id}" placeholder="${f.placeholder||''}" /></div>`;
}

function renderCheckItem(item) {
  const ans = formAnswers[item.id] || {};

  // Conditional visibility
  if (item.conditionalOn) {
    const depAns = formAnswers[item.conditionalOn];
    const depVal = depAns?.val ?? depAns?.text ?? depAns?.selected ?? '';
    if (depVal !== item.conditionalValue) return '';
  }

  const required = item.required ? '<span style="color:var(--danger);margin-left:3px">*</span>' : '';
  const pts = item.pts > 1 ? `<div class="ci-sub-label">⭐ Peso: ${item.pts} pts</div>` : '';
  const hint = item.hint ? `<div style="font-size:11px;color:var(--text-light);margin-bottom:6px">${item.hint}</div>` : '';

  let body = '';
  switch (item.type || 'checklist') {
    case 'checklist': {
      const selOk = ans.val==='C'  ? 'selected-ok' : '';
      const selNc = ans.val==='NC' ? 'selected-nc' : '';
      const selNa = ans.val==='NA' ? 'selected-na' : '';
      const obsVis = ans.val==='NC' ? 'visible' : '';

      // Photo logic based on photoMode setting
      const photoMode = item.photoMode || 'off';
      const showPhoto = photoMode === 'always' || (photoMode === 'nc_only' && ans.val === 'NC');

      const photoHtml = showPhoto ? `
        <div class="ci-photo-wrap" style="margin-top:8px">
          ${ans.photo ? `<img src="${ans.photo}" class="ci-photo-preview" alt="Foto" />` : ''}
          <label class="ci-photo-btn">
            📷 ${ans.photo ? 'Trocar foto' : photoMode === 'nc_only' ? 'Foto da não conformidade' : 'Adicionar foto (opcional)'}
            <input type="file" accept="image/*" capture="environment" style="display:none"
              onchange="setPhotoAnswer('${item.id}',this)" />
          </label>
          ${ans.photo ? `<button class="ci-photo-remove" onclick="clearPhotoAnswer('${item.id}')">✕ Remover foto</button>` : ''}
        </div>` : '';

      body = `<div class="ci-options">
        <button class="ci-btn ${selOk}" onclick="setAnswer('${item.id}','C')">✓ Conforme</button>
        <button class="ci-btn ${selNc}" onclick="setAnswer('${item.id}','NC')">✗ Não Conforme</button>
        <button class="ci-btn ${selNa}" onclick="setAnswer('${item.id}','NA')">N/A</button>
      </div>
      <div class="ci-obs-wrap ${obsVis}" id="obs-wrap-${item.id}">
        <textarea class="ci-obs" id="obs-${item.id}" placeholder="Descreva o problema..." rows="2"
          onchange="updateObsAnswer('${item.id}',this.value)">${ans.obs||''}</textarea>
      </div>
      ${photoHtml}`;
      break;
    }
    case 'text':
      body = `<input type="text" class="ci-text-input" id="ans-${item.id}"
        placeholder="Sua resposta..." value="${esc(ans.text||'')}"
        onchange="setTextAnswer('${item.id}',this.value)" />`;
      break;
    case 'textarea':
      body = `<textarea class="ci-obs" id="ans-${item.id}" rows="3" placeholder="Sua resposta..."
        style="width:100%" onchange="setTextAnswer('${item.id}',this.value)">${ans.text||''}</textarea>`;
      break;
    case 'date':
      body = `<input type="date" class="ci-text-input" id="ans-${item.id}"
        value="${ans.text||''}" onchange="setTextAnswer('${item.id}',this.value)" />`;
      break;
    case 'radio': {
      body = `<div class="ci-radio-group">
        ${(item.options||[]).map(o=>`
          <label class="ci-radio-label">
            <input type="radio" name="r_${item.id}" value="${esc(o.label)}"
              ${ans.selected===o.label?'checked':''}
              onchange="setSelectedAnswer('${item.id}','${esc(o.label)}')" />
            <span>${o.label}</span>
          </label>`).join('')}
      </div>`;
      break;
    }
    case 'checkbox': {
      const sel = ans.selected ? ans.selected.split('|||') : [];
      body = `<div class="ci-radio-group">
        ${(item.options||[]).map(o=>`
          <label class="ci-radio-label">
            <input type="checkbox" value="${esc(o.label)}"
              ${sel.includes(o.label)?'checked':''}
              onchange="toggleCheckboxAnswer('${item.id}','${esc(o.label)}',this.checked)" />
            <span>${o.label}</span>
          </label>`).join('')}
      </div>`;
      break;
    }
    case 'select': {
      body = `<select class="form-meta-field" style="width:100%;padding:11px 13px;border:1.5px solid var(--gray-light);border-radius:var(--radius-sm);font-size:13px"
        onchange="setSelectedAnswer('${item.id}',this.value)">
        <option value="">Selecione...</option>
        ${(item.options||[]).map(o=>`<option value="${esc(o.label)}" ${ans.selected===o.label?'selected':''}>${o.label}</option>`).join('')}
      </select>`;
      break;
    }
    case 'scale': {
      const min = item.scaleMin||1, max = item.scaleMax||5;
      const nums = Array.from({length: max-min+1}, (_,i)=>min+i);
      body = `<div class="ci-scale-wrap">
        <div class="ci-scale-nums">
          ${nums.map(n=>`<button class="ci-scale-btn ${ans.selected==n?'selected':''}"
            onclick="setSelectedAnswer('${item.id}','${n}')">${n}</button>`).join('')}
        </div>
        ${(item.scaleMinLabel||item.scaleMaxLabel)?`<div class="ci-scale-labels">
          <span>${item.scaleMinLabel||''}</span><span>${item.scaleMaxLabel||''}</span>
        </div>`:''}
      </div>`;
      break;
    }
    case 'photo': {
      body = `<div class="ci-photo-wrap">
        ${ans.photo ? `<img src="${ans.photo}" class="ci-photo-preview" alt="Foto" />` : ''}
        <label class="ci-photo-btn">
          📷 ${ans.photo ? 'Trocar foto' : 'Tirar / escolher foto'}
          <input type="file" accept="image/*" capture="environment" style="display:none"
            onchange="setPhotoAnswer('${item.id}',this)" />
        </label>
        ${ans.photo ? `<button class="ci-photo-remove" onclick="clearPhotoAnswer('${item.id}')">✕ Remover</button>` : ''}
      </div>`;
      break;
    }
    default: body = '';
  }

  return `<div class="checklist-item" id="ci-${item.id}">
    <div class="ci-label">${item.label}${required}</div>
    ${hint}${pts}${body}
  </div>`;
}

function renderObsField(f) {
  if (f.type === 'textarea') {
    return `<div class="form-meta-field"><label>${f.label}</label>
      <textarea id="obs-${f.id}" class="ci-obs" rows="5" style="width:100%" placeholder="${f.placeholder||''}"></textarea></div>`;
  }
  return `<div class="form-meta-field"><label>${f.label}</label>
    <input type="text" id="obs-${f.id}" placeholder="${f.placeholder||''}" /></div>`;
}

function setAnswer(itemId, val) {
  if (!formAnswers[itemId]) formAnswers[itemId] = {};
  formAnswers[itemId].val = val;
  renderFormStep();
}
function updateObsAnswer(itemId, val) {
  if (!formAnswers[itemId]) formAnswers[itemId] = {};
  formAnswers[itemId].obs = val;
}
function setTextAnswer(itemId, val) {
  if (!formAnswers[itemId]) formAnswers[itemId] = {};
  formAnswers[itemId].text = val;
  // Re-render to evaluate conditional questions
  renderFormStep();
}
function setSelectedAnswer(itemId, val) {
  if (!formAnswers[itemId]) formAnswers[itemId] = {};
  formAnswers[itemId].selected = val;
  renderFormStep();
}
function toggleCheckboxAnswer(itemId, val, checked) {
  if (!formAnswers[itemId]) formAnswers[itemId] = {};
  let sel = formAnswers[itemId].selected ? formAnswers[itemId].selected.split('|||') : [];
  if (checked) { if (!sel.includes(val)) sel.push(val); }
  else sel = sel.filter(v => v !== val);
  formAnswers[itemId].selected = sel.join('|||');
  renderFormStep();
}
function setPhotoAnswer(itemId, input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    if (!formAnswers[itemId]) formAnswers[itemId] = {};
    formAnswers[itemId].photo = e.target.result;
    renderFormStep();
  };
  reader.readAsDataURL(file);
}
function clearPhotoAnswer(itemId) {
  if (formAnswers[itemId]) formAnswers[itemId].photo = null;
  renderFormStep();
}

function saveCurrentStep() {
  const cl = getCL();
  const step = cl.steps[currentStep];
  if (step.type === 'meta') {
    step.fields.forEach(f => {
      const el = document.getElementById('meta-'+f.id);
      if (el) formMeta[f.id] = el.value;
    });
  } else if (step.type === 'obs') {
    step.fields.forEach(f => {
      const el = document.getElementById('obs-'+f.id);
      if (el) formMeta[f.id] = el.value;
    });
  }
}

function formNext() {
  saveCurrentStep();
  const cl = getCL();
  if (currentStep < cl.steps.length - 1) { currentStep++; renderFormStep(); }
  else submitChecklist();
}
function formPrev() {
  saveCurrentStep();
  if (currentStep > 0) { currentStep--; renderFormStep(); }
}

// ─── PONTUAÇÃO ─────────────────────────────────────
function countNC(submission) {
  return Object.values(submission.answers || {}).filter(a => a.val === 'NC').length;
}

function calculatePoints(submission, cl) {
  const rules = cl.scoreRules || { full:100, nc:60, obs:20, ontime:10 };
  const nc    = countNC(submission);

  // Peso por item
  let itemBonus = 0;
  if (nc === 0) {
    // Soma os pesos dos itens conformes
    cl.steps?.filter(s => s.type === 'checklist').forEach(step => {
      step.items?.forEach(item => {
        const ans = submission.answers?.[item.id];
        if (ans?.val === 'C') itemBonus += (item.pts || 1) - 1; // bonus acima de 1
      });
    });
  }

  let pts = nc === 0 ? rules.full : rules.nc;
  pts += itemBonus;
  if (submission.meta?.observacoes?.length > 20) pts += rules.obs;
  const today = new Date().toISOString().slice(0,10);
  if ((submission.date||'').slice(0,10) === today) pts += rules.ontime;

  return Math.max(0, pts);
}

// ─── SUBMIT ────────────────────────────────────────
function submitChecklist() {
  const cl = getCL();
  const id = 'sub_' + Date.now() + '_' + Math.random().toString(36).slice(2,7);
  const submission = {
    id, user: currentUser.login, userName: currentUser.name,
    type: currentCLId, clLabel: cl.label,
    date: new Date().toISOString(),
    meta: { ...formMeta }, answers: { ...formAnswers },
    synced: isOnline, archived: false,
  };
  submission.pts = calculatePoints(submission, cl);

  if (isOnline) DB.saveSubmission(submission);
  else { DB.addPending(submission); DB.saveSubmission({ ...submission, synced:false }); }

  // Atualiza score do usuário
  const users = DB.users();
  const idx   = users.findIndex(u => u.login === currentUser.login);
  if (idx >= 0) {
    users[idx].pts         = (users[idx].pts        || 0) + submission.pts;
    users[idx].submissions = (users[idx].submissions || 0) + 1;
    DB.set('garra_users', users);
    currentUser = users[idx];
  }

  showScreen('screen-success');
  document.getElementById('success-title').textContent = 'Check List Enviado! 🎉';
  document.getElementById('success-msg').textContent   = isOnline
    ? 'Salvo e sincronizado com sucesso.'
    : 'Salvo localmente. Será sincronizado quando online.';
  document.getElementById('pts-earned').textContent = `+${submission.pts} pts`;
}

// ─── MANAGER DASHBOARD ─────────────────────────────
function renderManagerDashboard() {
  renderOverview();
  renderRanking();
  renderSubmissions();
  renderFleet();
  if (typeof renderLogisticsTab === 'function') renderLogisticsTab();
  renderChecklistsTab();
  renderUsers();
  populateSubmissionFilters();
}

function mgrTab(tab, btn) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('tab-'+tab).classList.add('active');
}

// ── OVERVIEW ──
function renderOverview() {
  const subs    = DB.submissions();
  const weekAgo = new Date(Date.now() - 7*86400000);
  document.getElementById('kpi-total').textContent        = subs.length;
  document.getElementById('kpi-week').textContent         = subs.filter(s => new Date(s.date) > weekAgo).length;
  document.getElementById('kpi-pending-sync').textContent = DB.pendingSync().length;
  document.getElementById('kpi-nc').textContent           = subs.filter(s => countNC(s) > 0).length;

  // Bar chart by CL type
  const allCLs = DB.allCLs();
  const counts = {};
  Object.keys(allCLs).forEach(k => counts[k] = 0);
  subs.forEach(s => { if (counts[s.type] !== undefined) counts[s.type]++; });
  const maxC = Math.max(...Object.values(counts), 1);
  document.getElementById('chart-types').innerHTML = Object.entries(counts).map(([k, v]) => {
    const cl = allCLs[k];
    return `<div class="bc-row">
      <div class="bc-label">${cl?.icon||'📋'} ${cl?.label||k}</div>
      <div class="bc-bar-wrap"><div class="bc-bar-fill" style="width:${Math.round((v/maxC)*100)}%"></div></div>
      <div class="bc-count">${v}</div>
    </div>`;
  }).join('');

  // Compliance
  const drivers = DB.users().filter(u => u.role === 'driver').sort((a,b)=>(b.pts||0)-(a.pts||0));
  document.getElementById('compliance-list').innerHTML = drivers.map(d => {
    const ds = subs.filter(s => s.user === d.login);
    const total = ds.length, conf = ds.filter(s => countNC(s)===0).length;
    const pct = total > 0 ? Math.round((conf/total)*100) : 100;
    const color = pct>=80 ? 'var(--success)' : pct>=60 ? 'var(--warn)' : 'var(--danger)';
    return `<div class="compliance-item">
      <div class="ci-avatar">${d.name.charAt(0)}</div>
      <div class="ci-info"><div class="ci-name">${d.name}</div><div class="ci-pct">${total} envios • ${conf} conformes</div></div>
      <div class="ci-pct-val" style="color:${color}">${pct}%</div>
    </div>`;
  }).join('') || '<div class="empty-state">Nenhum motorista cadastrado</div>';
}

// ── RANKING ──
function renderRanking() {
  const drivers = DB.users().filter(u => u.role==='driver').sort((a,b)=>(b.pts||0)-(a.pts||0));
  const top3 = drivers.slice(0,3), rest = drivers.slice(3);
  const medals = ['🥇','🥈','🥉'], cls = ['p1','p2','p3'];
  document.getElementById('podium').innerHTML = top3.length
    ? top3.map((u,i) => `<div class="podium-place ${cls[i]}">
        <div class="pp-medal">${medals[i]}</div>
        <div class="pp-avatar">${u.name.charAt(0)}</div>
        <div class="pp-name">${u.name.split(' ')[0]}</div>
        <div class="pp-pts">${u.pts||0}</div>
      </div>`).join('')
    : '<div class="empty-state">Sem dados de ranking ainda</div>';

  document.getElementById('full-ranking').innerHTML = rest.map((u,i) => `
    <div class="rank-item">
      <div class="rank-pos">${i+4}</div>
      <div class="rank-avatar">${u.name.charAt(0)}</div>
      <div class="rank-info"><div class="rank-name">${u.name}</div><div class="rank-sub">${u.submissions||0} envios</div></div>
      <div class="rank-pts">${u.pts||0} pts</div>
    </div>`).join('') || '<div class="empty-state" style="padding:16px 0;font-size:13px">Apenas os 3 primeiros no pódio!</div>';
}

// ── SUBMISSIONS ──
function renderSubmissions() {
  const typeF   = document.getElementById('filter-type')?.value   || '';
  const userF   = document.getElementById('filter-user')?.value   || '';
  const statusF = document.getElementById('filter-status')?.value || '';

  let subs = DB.submissions();
  if (typeF)              subs = subs.filter(s => s.type === typeF);
  if (userF)              subs = subs.filter(s => s.user === userF);
  if (statusF === 'ok')   subs = subs.filter(s => countNC(s) === 0 && !s.archived);
  if (statusF === 'nc')   subs = subs.filter(s => countNC(s) > 0);
  if (statusF === 'archived') subs = subs.filter(s => s.archived);

  const el = document.getElementById('submissions-list');
  if (!subs.length) { el.innerHTML = '<div class="empty-state"><div class="es-icon">📭</div>Nenhum envio encontrado.</div>'; return; }
  const allCLs = DB.allCLs();
  el.innerHTML = subs.map(s => {
    const cl  = allCLs[s.type] || {};
    const nc  = countNC(s);
    const st  = s.archived ? 'archived' : s.synced===false ? 'pending' : nc>0 ? 'nc' : 'ok';
    const veh = s.meta?.veiculo || s.meta?.equipamento || '';
    return `<div class="sub-card ${st}" onclick="showSubmissionDetail('${s.id}')">
      <div class="sub-top">
        <div>
          <div class="sub-title">${cl.icon||'📋'} ${cl.label||s.type}${veh?' – '+veh:''}</div>
          <div class="sub-meta">${s.userName} • ${formatDate(s.date)}${s.meta?.local?' • '+s.meta.local:''}</div>
        </div>
        <div class="badge ${st}">${nc>0?nc+' NC':st==='pending'?'⏳':st==='archived'?'📦':'✓'}</div>
      </div>
      ${s.archived ? '<div class="archived-tag">📦 Equipamento removido da frota</div>' : ''}
      ${nc>0 && s.meta?.observacoes ? `<div class="sub-issues">⚠ ${s.meta.observacoes.slice(0,80)}${s.meta.observacoes.length>80?'...':''}</div>` : ''}
    </div>`;
  }).join('');
}

function populateSubmissionFilters() {
  const allCLs = DB.allCLs();
  const ft = document.getElementById('filter-type');
  if (ft) ft.innerHTML = '<option value="">Todos os tipos</option>' +
    Object.values(allCLs).map(cl => `<option value="${cl.id}">${cl.icon} ${cl.label}</option>`).join('');

  const fu = document.getElementById('filter-user');
  if (fu) fu.innerHTML = '<option value="">Todos</option>' +
    DB.users().filter(u=>u.role==='driver').map(u => `<option value="${u.login}">${u.name}</option>`).join('');
}

// ── FLEET ──
function renderFleet() {
  const fleet = DB.fleet();
  const cats  = { maquinas:'🚜 Máquinas', carro:'🚗 Carros de Apoio', caminhao:'🚛 Caminhões' };
  document.getElementById('fleet-groups').innerHTML = Object.entries(cats).map(([cat, label]) => {
    const items = (fleet[cat] || []);
    return `<div>
      <div class="fleet-group-title">${label}</div>
      <div class="fleet-list">
        ${items.map(v => `
          <div class="fleet-item ${v.active?'':'inactive'}">
            <div class="fleet-id">${v.id}</div>
            <div class="fleet-desc">${v.desc||''}${!v.active?' <span class="inactive-badge">Inativo</span>':''}</div>
            <div class="fleet-actions">
              ${v.active ? `
                <button class="fleet-btn edit" onclick="openFleetEdit('${cat}','${v.id}')">✎ Editar</button>
                <button class="fleet-btn remove" onclick="openFleetRemove('${cat}','${v.id}')">✕ Remover</button>
              ` : ''}
            </div>
          </div>`).join('')}
        ${!items.length ? '<div style="font-size:13px;color:var(--text-light);padding:8px">Nenhum equipamento cadastrado</div>' : ''}
      </div>
    </div>`;
  }).join('');
}

function openFleetModal() {
  document.getElementById('fleet-modal-title').textContent = 'Novo Equipamento';
  document.getElementById('fleet-edit-key').value  = '';
  document.getElementById('fleet-id-input').value  = '';
  document.getElementById('fleet-desc-input').value= '';
  document.getElementById('fleet-category').value  = 'maquinas';
  openModal('fleet-modal');
}
function openFleetEdit(cat, id) {
  const item = (DB.fleet()[cat]||[]).find(v => v.id === id);
  if (!item) return;
  document.getElementById('fleet-modal-title').textContent = 'Editar Equipamento';
  document.getElementById('fleet-edit-key').value   = cat + '|' + id;
  document.getElementById('fleet-category').value   = cat;
  document.getElementById('fleet-id-input').value   = item.id;
  document.getElementById('fleet-desc-input').value = item.desc || '';
  openModal('fleet-modal');
}
function saveFleetItem() {
  const editKey = document.getElementById('fleet-edit-key').value;
  const cat     = document.getElementById('fleet-category').value;
  const id      = document.getElementById('fleet-id-input').value.trim().toUpperCase();
  const desc    = document.getElementById('fleet-desc-input').value.trim();
  if (!id) { alert('Informe a identificação do equipamento.'); return; }

  if (editKey) {
    const [oldCat, oldId] = editKey.split('|');
    // Remove old entry if cat changed or id changed
    if (oldCat !== cat || oldId !== id) {
      const f = DB.fleet();
      f[oldCat] = (f[oldCat]||[]).filter(v => v.id !== oldId);
      DB.set('garra_fleet', f);
    }
  }
  DB.saveFleetItem(cat, { id, desc, active: true });
  closeModal('fleet-modal');
  renderFleet();
}
function openFleetRemove(cat, id) {
  pendingRemoveFleetKey = cat + '|' + id;
  document.getElementById('fleet-remove-info').textContent = `Equipamento: ${id}`;
  openModal('fleet-remove-modal');
}
function confirmRemoveFleet() {
  if (!pendingRemoveFleetKey) return;
  const [cat, id] = pendingRemoveFleetKey.split('|');
  DB.deactivateFleetItem(cat, id);
  pendingRemoveFleetKey = null;
  closeModal('fleet-remove-modal');
  renderFleet();
  renderSubmissions();
}

// ── CHECK LISTS TAB ──
function renderChecklistsTab() {
  // Padrão
  document.getElementById('default-cl-list').innerHTML = Object.values(DEFAULT_CHECKLISTS).map(cl => `
    <div class="ccl-item">
      <div class="ccl-icon">${cl.icon}</div>
      <div class="ccl-body">
        <div class="ccl-name">${cl.label}</div>
        <div class="ccl-meta">${cl.desc||''}</div>
        <div class="ccl-pts">⭐ Pontuação: ${cl.scoreRules?.full||100} pts (conforme) / ${cl.scoreRules?.nc||60} pts (com NC)</div>
      </div>
      <div class="ccl-actions"><span class="role-badge driver">Padrão</span></div>
    </div>`).join('');

  // Customizados
  const customs = DB.customCLs();
  const customEl = document.getElementById('custom-cl-list');
  if (!customs.length) {
    customEl.innerHTML = '<div class="empty-state"><div class="es-icon">📝</div>Nenhum check list personalizado criado ainda.</div>';
    return;
  }
  customEl.innerHTML = customs.map(cl => {
    const totalItems = (cl.sections||[]).reduce((acc,s) => acc + (s.items||[]).length, 0);
    const maxItemPts = (cl.sections||[]).reduce((acc,s) => acc + (s.items||[]).reduce((a,i) => a+(i.pts||1), 0), 0);
    return `<div class="ccl-item">
      <div class="ccl-icon">${cl.icon||'📋'}</div>
      <div class="ccl-body">
        <div class="ccl-name">${cl.label}</div>
        <div class="ccl-meta">${cl.desc||''} • ${totalItems} itens em ${(cl.sections||[]).length} seção(ões)</div>
        <div class="ccl-pts">⭐ Conforme: ${cl.scoreRules?.full||100}pts | NC: ${cl.scoreRules?.nc||60}pts | Peso máx. itens: ${maxItemPts}pts</div>
      </div>
      <div class="ccl-actions">
        <button class="fleet-btn edit" onclick="openBuilder('${cl.id}')">✎ Editar</button>
        <button class="fleet-btn remove" onclick="openCLRemove('${cl.id}')">✕ Excluir</button>
      </div>
    </div>`;
  }).join('');
}

function openCLRemove(id) {
  const cl = DB.customCLs().find(c => c.id === id);
  if (!cl) return;
  pendingRemoveCLId = id;
  document.getElementById('cl-remove-info').textContent = `Check List: "${cl.label}"`;
  openModal('cl-remove-modal');
}
function confirmRemoveCL() {
  if (!pendingRemoveCLId) return;
  DB.removeCustomCL(pendingRemoveCLId);
  pendingRemoveCLId = null;
  closeModal('cl-remove-modal');
  renderChecklistsTab();
}

// ── USERS ──
function renderUsers() {
  const users = DB.users();
  document.getElementById('users-list').innerHTML = users.map(u => {
    const roleLabel = u.role==='manager' ? 'Gestor' : u.role==='superior' ? 'Superior' : 'Motorista';
    const roleColor = u.role==='manager' ? 'var(--orange)' : u.role==='superior' ? 'var(--navy-light)' : 'var(--navy)';
    return `
    <div class="user-item">
      <div class="ui-avatar" style="background:${roleColor}">${u.name.charAt(0)}</div>
      <div class="ui-info">
        <div class="ui-name">${u.name}</div>
        <div class="ui-role">${u.login} • ${u.submissions||0} envios • ${u.pts||0} pts</div>
      </div>
      <div class="ui-actions">
        <span class="role-badge ${u.role}">${roleLabel}</span>
        <button class="fleet-btn edit" onclick="openUserEdit('${u.login}')">✎</button>
        ${u.login !== currentUser.login ? `<button class="fleet-btn remove" onclick="openUserRemove('${u.login}')">✕</button>` : ''}
      </div>
    </div>`}).join('');
}

let editingUserLogin = null;

function openUserEdit(login) {
  const u = DB.users().find(u => u.login === login);
  if (!u) return;
  editingUserLogin = login;
  document.getElementById('eu-name').value  = u.name;
  document.getElementById('eu-login').textContent = u.login;
  document.getElementById('eu-role').value  = u.role;
  document.getElementById('eu-pass').value  = '';
  openModal('user-edit-modal');
}

function saveEditUser() {
  const name = document.getElementById('eu-name').value.trim();
  const role = document.getElementById('eu-role').value;
  const pass = document.getElementById('eu-pass').value;
  if (!name) { alert('Informe o nome.'); return; }
  const users = DB.users();
  const idx = users.findIndex(u => u.login === editingUserLogin);
  if (idx < 0) return;
  users[idx].name = name;
  users[idx].role = role;
  if (pass) users[idx].pass = pass;
  DB.set('garra_users', users);
  // Update currentUser if editing self
  if (editingUserLogin === currentUser.login) {
    currentUser.name = name;
    currentUser.role = role;
  }
  editingUserLogin = null;
  closeModal('user-edit-modal');
  renderUsers();
  populateSubmissionFilters();
}

function saveNewUser() {
  const name  = document.getElementById('nu-name').value.trim();
  const login = document.getElementById('nu-user').value.trim().toLowerCase();
  const pass  = document.getElementById('nu-pass').value;
  const role  = document.getElementById('nu-role').value;
  if (!name || !login || !pass) { alert('Preencha todos os campos.'); return; }
  if (DB.users().find(u => u.login === login)) { alert('Login já existe.'); return; }
  DB.saveUser({ name, login, pass, role, pts:0, submissions:0 });
  document.getElementById('nu-name').value = '';
  document.getElementById('nu-user').value = '';
  document.getElementById('nu-pass').value = '';
  closeModal('user-modal');
  renderUsers();
  populateSubmissionFilters();
}
function openUserRemove(login) {
  const u = DB.users().find(u => u.login === login);
  if (!u) return;
  pendingRemoveUserLogin = login;
  document.getElementById('user-remove-info').textContent = `Usuário: ${u.name} (${u.login})`;
  openModal('user-remove-modal');
}
function confirmRemoveUser() {
  if (!pendingRemoveUserLogin) return;
  DB.removeUser(pendingRemoveUserLogin);
  pendingRemoveUserLogin = null;
  closeModal('user-remove-modal');
  renderUsers();
  populateSubmissionFilters();
}

// ─── BUILDER v2 — Google Forms style ───────────────
// Tipos: checklist | text | textarea | radio | checkbox | select | scale | date | photo | section

let builderQuestions = []; // array de question objects
let builderEditId    = null;
let builderFocusId   = null;

const Q_TYPES = {
  checklist: { label: '✅ Conformidade',    icon: '✅' },
  text:      { label: 'Tz Texto Curto',     icon: 'Tz' },
  textarea:  { label: '¶ Parágrafo',        icon: '¶'  },
  radio:     { label: '◉ Múltipla escolha', icon: '◉'  },
  checkbox:  { label: '☑ Caixas de seleção',icon: '☑'  },
  select:    { label: '▾ Lista suspensa',   icon: '▾'  },
  scale:     { label: '⭐ Escala',          icon: '⭐' },
  date:      { label: '📅 Data',            icon: '📅' },
  photo:     { label: '📷 Foto',            icon: '📷' },
  section:   { label: '── Divisor de seção',icon: '──' },
};

function qid() { return 'q_' + Date.now() + '_' + Math.random().toString(36).slice(2,5); }
function oid() { return 'o_' + Date.now() + '_' + Math.random().toString(36).slice(2,5); }

function defaultQuestion(type) {
  const base = { id: qid(), type, label: '', hint: '', required: false, pts: 1, conditionalOn: '', conditionalValue: '', photoMode: 'off' };
  if (['radio','checkbox','select'].includes(type)) base.options = [{ id: oid(), label: 'Opção 1', pts: 1 }, { id: oid(), label: 'Opção 2', pts: 1 }];
  if (type === 'scale') { base.scaleMin = 1; base.scaleMax = 5; base.scaleMinLabel = ''; base.scaleMaxLabel = ''; }
  if (type === 'section') { base.label = 'Nova Seção'; base.hint = ''; }
  return base;
}

function addQuestion(type) {
  const q = defaultQuestion(type);
  builderQuestions.push(q);
  renderBuilderQuestions();
  // scroll to new question
  setTimeout(() => { const el = document.getElementById('blq-'+q.id); if (el) el.scrollIntoView({behavior:'smooth', block:'center'}); }, 80);
  focusQuestion(q.id);
}

function removeBuilderQuestion(qId) {
  builderQuestions = builderQuestions.filter(q => q.id !== qId);
  renderBuilderQuestions();
}

function duplicateQuestion(qId) {
  const idx = builderQuestions.findIndex(q => q.id === qId);
  if (idx < 0) return;
  const clone = JSON.parse(JSON.stringify(builderQuestions[idx]));
  clone.id = qid();
  if (clone.options) clone.options = clone.options.map(o => ({...o, id: oid()}));
  builderQuestions.splice(idx+1, 0, clone);
  renderBuilderQuestions();
}

function moveQuestion(qId, dir) {
  const idx = builderQuestions.findIndex(q => q.id === qId);
  const newIdx = idx + dir;
  if (newIdx < 0 || newIdx >= builderQuestions.length) return;
  const tmp = builderQuestions[idx];
  builderQuestions[idx] = builderQuestions[newIdx];
  builderQuestions[newIdx] = tmp;
  renderBuilderQuestions();
}

function focusQuestion(qId) {
  builderFocusId = qId;
  document.querySelectorAll('.bl-question').forEach(el => el.classList.remove('focused'));
  const el = document.getElementById('blq-'+qId);
  if (el) el.classList.add('focused');
}

function getQ(qId) { return builderQuestions.find(q => q.id === qId); }

// Live-update helpers (called from oninput/onchange in rendered HTML)
function blUpdateLabel(qId, val)    { const q=getQ(qId); if(q) q.label=val; }
function blUpdateHint(qId, val)     { const q=getQ(qId); if(q) q.hint=val; }
function blUpdateRequired(qId, val) { const q=getQ(qId); if(q) q.required=val; }
function blUpdatePts(qId, val)      { const q=getQ(qId); if(q) q.pts=parseInt(val)||1; }
function blUpdateType(qId, val) {
  const q = getQ(qId); if (!q) return;
  q.type = val;
  if (['radio','checkbox','select'].includes(val) && !q.options) q.options = [{ id:oid(), label:'Opção 1', pts:1 },{ id:oid(), label:'Opção 2', pts:1 }];
  if (val === 'scale') { if (!q.scaleMin) q.scaleMin=1; if (!q.scaleMax) q.scaleMax=5; }
  renderBuilderQuestions();
  focusQuestion(qId);
}
function blUpdateOptLabel(qId, oId, val) { const q=getQ(qId); if(!q) return; const o=q.options?.find(o=>o.id===oId); if(o) o.label=val; }
function blUpdateOptPts(qId, oId, val)   { const q=getQ(qId); if(!q) return; const o=q.options?.find(o=>o.id===oId); if(o) o.pts=parseInt(val)||1; }
function blAddOption(qId) {
  const q = getQ(qId); if (!q||!q.options) return;
  q.options.push({ id:oid(), label:`Opção ${q.options.length+1}`, pts:1 });
  renderBuilderQuestions(); focusQuestion(qId);
}
function blRemoveOption(qId, oId) {
  const q = getQ(qId); if (!q||!q.options) return;
  if (q.options.length <= 1) return;
  q.options = q.options.filter(o => o.id !== oId);
  renderBuilderQuestions(); focusQuestion(qId);
}
function blUpdateScale(qId, field, val) {
  const q = getQ(qId); if (!q) return;
  if (field==='min') q.scaleMin = parseInt(val)||1;
  if (field==='max') q.scaleMax = parseInt(val)||10;
  if (field==='minLabel') q.scaleMinLabel = val;
  if (field==='maxLabel') q.scaleMaxLabel = val;
  renderBuilderQuestions(); focusQuestion(qId);
}
function blUpdateCond(qId, field, val) {
  const q = getQ(qId); if (!q) return;
  q[field] = val;
}
function blSetPhotoMode(qId, mode) {
  const q = getQ(qId); if (!q) return;
  q.photoMode = mode;
  renderBuilderQuestions();
  focusQuestion(qId);
}

function renderBuilderQuestions() {
  const el = document.getElementById('builder-questions');
  if (!el) return;
  if (!builderQuestions.length) {
    el.innerHTML = `<div style="text-align:center;padding:30px 20px;color:var(--text-light);font-size:13px;background:var(--white);border-radius:var(--radius);box-shadow:var(--shadow-sm)">
      <div style="font-size:32px;margin-bottom:8px">📋</div>
      Use os botões abaixo para adicionar perguntas ao seu check list.
    </div>`;
    return;
  }

  el.innerHTML = builderQuestions.map((q, idx) => {
    if (q.type === 'section') return renderSectionBlock(q, idx);
    return renderQuestionBlock(q, idx);
  }).join('');
}

function renderSectionBlock(q, idx) {
  return `<div class="bl-section-card" id="blq-${q.id}" onclick="focusQuestion('${q.id}')">
    <input class="bl-section-title-input" type="text" value="${esc(q.label)}" placeholder="Título da Seção"
      oninput="blUpdateLabel('${q.id}',this.value)" />
    <input class="bl-section-desc-input" type="text" value="${esc(q.hint)}" placeholder="Descrição da seção (opcional)"
      oninput="blUpdateHint('${q.id}',this.value)" />
    <div class="bl-section-footer">
      ${idx>0?`<button class="bl-section-btn" onclick="moveQuestion('${q.id}',-1)">↑</button>`:''}
      ${idx<builderQuestions.length-1?`<button class="bl-section-btn" onclick="moveQuestion('${q.id}',1)">↓</button>`:''}
      <button class="bl-section-btn" style="color:rgba(255,100,100,.7)" onclick="removeBuilderQuestion('${q.id}')">✕ Remover</button>
    </div>
  </div>`;
}

function renderQuestionBlock(q, idx) {
  const typeOpts = Object.entries(Q_TYPES).filter(([k])=>k!=='section').map(([k,v])=>
    `<option value="${k}" ${q.type===k?'selected':''}>${v.label}</option>`).join('');

  // Conditional logic: only show if there's a prior radio/checkbox/select/checklist question
  const prevChoiceQs = builderQuestions.slice(0, idx).filter(pq => ['radio','checkbox','select','checklist'].includes(pq.type) && pq.label);
  const condHtml = prevChoiceQs.length ? `
    <div class="bl-cond-wrap">
      <div class="bl-cond-title">🔀 Lógica condicional</div>
      <div class="bl-cond-row">
        <span>Mostrar se</span>
        <select class="bl-cond-sel" onchange="blUpdateCond('${q.id}','conditionalOn',this.value)">
          <option value="">Sempre exibir</option>
          ${prevChoiceQs.map(pq=>`<option value="${pq.id}" ${q.conditionalOn===pq.id?'selected':''}>${esc(pq.label.slice(0,40))}</option>`).join('')}
        </select>
        ${q.conditionalOn ? (() => {
          const pq = getQ(q.conditionalOn);
          if (!pq) return '';
          if (pq.type === 'checklist') return `
            <span>for</span>
            <select class="bl-cond-sel" onchange="blUpdateCond('${q.id}','conditionalValue',this.value)">
              <option value="NC" ${q.conditionalValue==='NC'?'selected':''}>Não Conforme</option>
              <option value="C"  ${q.conditionalValue==='C'?'selected':''}>Conforme</option>
              <option value="NA" ${q.conditionalValue==='NA'?'selected':''}>N/A</option>
            </select>`;
          if (pq.options) return `
            <span>for</span>
            <select class="bl-cond-sel" onchange="blUpdateCond('${q.id}','conditionalValue',this.value)">
              ${pq.options.map(o=>`<option value="${o.label}" ${q.conditionalValue===o.label?'selected':''}>${esc(o.label)}</option>`).join('')}
            </select>`;
          return '';
        })() : ''}
      </div>
    </div>` : '';

  return `<div class="bl-question ${builderFocusId===q.id?'focused':''}" id="blq-${q.id}" onclick="focusQuestion('${q.id}')">
    <div class="bl-q-header">
      <div class="bl-q-drag" title="Mover">⠿</div>
      <div class="bl-q-main">
        <div style="display:flex;gap:8px;align-items:flex-start;margin-bottom:6px">
          <input class="bl-q-label-input" style="flex:1" type="text" value="${esc(q.label)}"
            placeholder="Pergunta ${idx+1}"
            oninput="blUpdateLabel('${q.id}',this.value)" />
          <select class="bl-q-type-sel" onchange="blUpdateType('${q.id}',this.value)">${typeOpts}</select>
        </div>
        <input class="bl-q-hint-input" type="text" value="${esc(q.hint)}"
          placeholder="Texto de ajuda (opcional)"
          oninput="blUpdateHint('${q.id}',this.value)" />
      </div>
    </div>

    <div class="bl-q-body">
      ${renderQuestionBody(q)}
      ${condHtml}
    </div>

    <div class="bl-q-footer">
      <div class="bl-q-pts-wrap">
        <span>⭐ Peso</span>
        <input class="bl-q-pts" type="number" value="${q.pts||1}" min="0" max="99"
          oninput="blUpdatePts('${q.id}',this.value)" title="Peso na pontuação" />
        <span>pts</span>
      </div>
      <label class="bl-q-toggle">
        <input type="checkbox" ${q.required?'checked':''} onchange="blUpdateRequired('${q.id}',this.checked)" /> Obrigatória
      </label>
      ${idx>0?`<button class="bl-q-btn" onclick="moveQuestion('${q.id}',-1)" title="Mover para cima">↑</button>`:''}
      ${idx<builderQuestions.length-1?`<button class="bl-q-btn" onclick="moveQuestion('${q.id}',1)" title="Mover para baixo">↓</button>`:''}
      <button class="bl-q-btn dupe" onclick="duplicateQuestion('${q.id}')" title="Duplicar">⧉</button>
      <button class="bl-q-btn danger" onclick="removeBuilderQuestion('${q.id}')" title="Remover">🗑</button>
    </div>
  </div>`;
}

function renderQuestionBody(q) {
  switch (q.type) {
    case 'checklist':
      return `<div class="bl-conf-preview">
        <div class="bl-conf-btn ok">✓ Conforme</div>
        <div class="bl-conf-btn nc">✗ Não Conforme</div>
        <div class="bl-conf-btn na">N/A</div>
      </div>
      <div style="font-size:11px;color:var(--text-light);margin-top:8px;margin-bottom:10px">
        Campo de observação de texto aparece automaticamente ao marcar Não Conforme.
      </div>
      <div class="bl-photo-toggle-wrap">
        <div class="bl-photo-toggle-row">
          <div class="bl-photo-toggle-icon">📷</div>
          <div class="bl-photo-toggle-body">
            <div class="bl-photo-toggle-title">Upload de foto neste item</div>
            <div class="bl-photo-toggle-desc" id="bl-phototoggle-desc-${q.id}">
              ${q.photoMode === 'nc_only'
                ? 'Foto habilitada <strong>somente ao marcar Não Conforme</strong>'
                : q.photoMode === 'always'
                  ? 'Foto disponível <strong>sempre</strong> (opcional)'
                  : 'Foto <strong>desabilitada</strong> neste item'}
            </div>
          </div>
        </div>
        <div class="bl-photo-mode-btns">
          <button class="bl-photo-mode-btn ${q.photoMode==='off'||!q.photoMode?'active':''}"
            onclick="blSetPhotoMode('${q.id}','off')">🚫 Desabilitado</button>
          <button class="bl-photo-mode-btn ${q.photoMode==='always'?'active':''}"
            onclick="blSetPhotoMode('${q.id}','always')">✅ Sempre disponível</button>
          <button class="bl-photo-mode-btn ${q.photoMode==='nc_only'?'active accent':''}"
            onclick="blSetPhotoMode('${q.id}','nc_only')">⚠ Somente em NC</button>
        </div>
      </div>`;

    case 'text':
      return `<div style="border-bottom:1px solid var(--gray-light);padding:8px 0;font-size:13px;color:var(--gray)">Resposta de texto curto</div>`;

    case 'textarea':
      return `<div style="border:1px solid var(--gray-light);border-radius:6px;padding:8px;font-size:13px;color:var(--gray);min-height:52px">Resposta de parágrafo (texto longo)</div>`;

    case 'date':
      return `<div style="border:1.5px solid var(--gray-light);border-radius:6px;padding:8px 10px;font-size:13px;color:var(--gray);display:inline-flex;align-items:center;gap:6px">📅 dd/mm/aaaa</div>`;

    case 'photo':
      return `<div class="bl-photo-preview"><div class="pi">📷</div>Toque para tirar foto ou escolher da galeria</div>`;

    case 'radio':
    case 'checkbox':
    case 'select':
      const icon = q.type==='radio'?'◉':q.type==='checkbox'?'☐':'▾';
      return `<div class="bl-options-list">
        ${(q.options||[]).map(o=>`
          <div class="bl-option-row">
            <span class="bl-option-icon">${icon}</span>
            <input class="bl-option-input" type="text" value="${esc(o.label)}" placeholder="Opção..."
              oninput="blUpdateOptLabel('${q.id}','${o.id}',this.value)" />
            <div title="Pontos se escolhida" style="display:flex;align-items:center;gap:3px">
              <span style="font-size:10px;color:var(--gray)">pts</span>
              <input class="bl-option-pts" type="number" value="${o.pts||1}" min="0" max="99"
                oninput="blUpdateOptPts('${q.id}','${o.id}',this.value)" />
            </div>
            <button class="bl-q-btn danger" onclick="blRemoveOption('${q.id}','${o.id}')">✕</button>
          </div>`).join('')}
      </div>
      <button class="bl-add-option" onclick="blAddOption('${q.id}')">+ Adicionar opção</button>`;

    case 'scale':
      const min = q.scaleMin||1, max = q.scaleMax||5;
      const nums = Array.from({length: Math.min(max-min+1,10)}, (_,i)=>min+i);
      return `<div class="bl-scale-config">
        <div class="field-group"><label>Mínimo</label>
          <input type="number" value="${min}" min="0" max="9" style="padding:7px 10px"
            oninput="blUpdateScale('${q.id}','min',this.value)" />
        </div>
        <div class="field-group"><label>Máximo</label>
          <input type="number" value="${max}" min="2" max="10" style="padding:7px 10px"
            oninput="blUpdateScale('${q.id}','max',this.value)" />
        </div>
        <div class="field-group"><label>Label mínimo</label>
          <input type="text" value="${esc(q.scaleMinLabel||'')}" placeholder="Ex.: Péssimo"
            oninput="blUpdateScale('${q.id}','minLabel',this.value)" />
        </div>
        <div class="field-group"><label>Label máximo</label>
          <input type="text" value="${esc(q.scaleMaxLabel||'')}" placeholder="Ex.: Excelente"
            oninput="blUpdateScale('${q.id}','maxLabel',this.value)" />
        </div>
      </div>
      <div class="bl-scale-preview">
        ${nums.map(n=>`<div class="bl-scale-num">${n}</div>`).join('')}
      </div>
      <div class="bl-scale-label-row">
        <span>${q.scaleMinLabel||''}</span><span>${q.scaleMaxLabel||''}</span>
      </div>`;

    default: return '';
  }
}

// ─── OPEN / CLOSE BUILDER ──────────────────────────
function openBuilder(clId) {
  builderEditId     = clId;
  builderQuestions  = [];
  builderFocusId    = null;

  if (clId) {
    const cl = DB.customCLs().find(c => c.id === clId);
    if (cl) {
      document.getElementById('bl-name').value        = cl.label;
      document.getElementById('bl-icon').value        = cl.icon || '';
      document.getElementById('bl-vehicle-cat').value = cl.vehicleCat || 'maquinas';
      document.getElementById('bl-desc').value        = cl.desc || '';
      document.getElementById('pts-full').value       = cl.scoreRules?.full   ?? 100;
      document.getElementById('pts-nc-base').value    = cl.scoreRules?.nc     ?? 60;
      document.getElementById('pts-obs').value        = cl.scoreRules?.obs    ?? 20;
      document.getElementById('pts-ontime').value     = cl.scoreRules?.ontime ?? 10;
      builderQuestions = JSON.parse(JSON.stringify(cl.questions || []));
      document.getElementById('builder-screen-title').textContent = 'Editar Check List';
    }
  } else {
    document.getElementById('bl-name').value        = '';
    document.getElementById('bl-icon').value        = '📋';
    document.getElementById('bl-vehicle-cat').value = 'maquinas';
    document.getElementById('bl-desc').value        = '';
    document.getElementById('pts-full').value       = 100;
    document.getElementById('pts-nc-base').value    = 60;
    document.getElementById('pts-obs').value        = 20;
    document.getElementById('pts-ontime').value     = 10;
    builderQuestions = [];
    document.getElementById('builder-screen-title').textContent = 'Novo Check List';
  }
  renderBuilderQuestions();
  showScreen('screen-builder');
}

function closeBuilder() {
  if (currentUser?.role === 'superior') showSuperior();
  else showManager();
}

// ─── PREVIEW ──────────────────────────────────────
function previewChecklist() {
  const name = document.getElementById('bl-name').value.trim() || 'Preview';
  if (!builderQuestions.length) { alert('Adicione perguntas primeiro.'); return; }
  // Save temp and launch as driver would see
  const tempId = '__preview__';
  const tempCL = buildCLObject(tempId);
  DB.saveCustomCL(tempCL);
  startChecklist(tempId);
}

// ─── SAVE CHECKLIST ──────────────────────────────
function saveChecklist() {
  const name = document.getElementById('bl-name').value.trim();
  if (!name) { alert('Informe o título do check list.'); return; }
  const realQs = builderQuestions.filter(q => q.type !== 'section');
  if (!realQs.length) { alert('Adicione pelo menos uma pergunta.'); return; }

  const id = builderEditId && builderEditId !== '__preview__'
    ? builderEditId
    : ('cl_' + Date.now() + '_' + Math.random().toString(36).slice(2,5));

  DB.saveCustomCL(buildCLObject(id));

  // Remove preview if it exists
  DB.removeCustomCL('__preview__');

  if (currentUser?.role === 'superior') {
    showSuperior();
  } else {
    showManager();
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    const tabBtn = document.querySelector('[onclick*="checklists"]');
    if (tabBtn) tabBtn.classList.add('active');
    document.getElementById('tab-checklists').classList.add('active');
    renderChecklistsTab();
    renderManagerDashboard();
  }
}

function buildCLObject(id) {
  // Convert questions array → steps array compatible with form renderer
  const steps = [];

  // Always add meta step first
  const vehicleCat = document.getElementById('bl-vehicle-cat').value;
  const metaFields = [
    { id:'operador', label:'Responsável', type:'text', placeholder:'Seu nome' },
    { id:'local',    label:'Local',       type:'text', placeholder:'Local da verificação' },
    { id:'data',     label:'Data',        type:'date' },
  ];
  if (vehicleCat !== 'none') metaFields.push({ id:'veiculo', label:'Equipamento/Veículo', type:'select', options:'vehicles' });
  else metaFields.push({ id:'veiculo', label:'Equipamento/Veículo', type:'text', placeholder:'Identifique o equipamento' });
  steps.push({ title:'Identificação', sub:'Preencha os dados iniciais', type:'meta', fields: metaFields });

  // Group questions by section dividers
  let currentSectionTitle = 'Perguntas';
  let currentSectionItems = [];

  function flushSection() {
    if (!currentSectionItems.length) return;
    const hasChecklist = currentSectionItems.some(q => q.type === 'checklist');
    steps.push({
      title: currentSectionTitle,
      sub: '',
      type: hasChecklist ? 'checklist' : 'custom',
      items: currentSectionItems.map(q => {
        const base = { id: q.id, label: q.label, type: q.type, required: q.required, pts: q.pts, hint: q.hint, conditionalOn: q.conditionalOn, conditionalValue: q.conditionalValue };
        if (q.options)       base.options = q.options;
        if (q.scaleMin)      base.scaleMin = q.scaleMin;
        if (q.scaleMax)      base.scaleMax = q.scaleMax;
        if (q.scaleMinLabel) base.scaleMinLabel = q.scaleMinLabel;
        if (q.scaleMaxLabel) base.scaleMaxLabel = q.scaleMaxLabel;
        return base;
      })
    });
    currentSectionItems = [];
  }

  builderQuestions.forEach(q => {
    if (q.type === 'section') {
      flushSection();
      currentSectionTitle = q.label || 'Seção';
    } else {
      currentSectionItems.push(q);
    }
  });
  flushSection();

  // Obs step
  steps.push({ title:'Observações', sub:'Registre problemas ou observações gerais', type:'obs',
    fields:[{ id:'observacoes', label:'Observações / Comentários', type:'textarea', placeholder:'Descreva...' }] });

  return {
    id,
    isDefault: false,
    label: document.getElementById('bl-name').value.trim(),
    icon:  document.getElementById('bl-icon').value.trim() || '📋',
    desc:  document.getElementById('bl-desc').value.trim(),
    vehicleCat,
    scoreRules: {
      full:   parseInt(document.getElementById('pts-full').value)    || 100,
      nc:     parseInt(document.getElementById('pts-nc-base').value) || 60,
      obs:    parseInt(document.getElementById('pts-obs').value)     || 20,
      ontime: parseInt(document.getElementById('pts-ontime').value)  || 10,
    },
    questions: builderQuestions,
    steps,
  };
}


// ─── DETAIL ────────────────────────────────────────
function showSubmissionDetail(id) {
  const sub = DB.submissions().find(s => s.id === id);
  if (!sub) return;
  const cl = DB.allCLs()[sub.type] || {};
  const nc = countNC(sub);
  const st = sub.archived ? 'archived' : (sub.synced===false ? 'pending' : nc>0 ? 'nc' : 'ok');

  let html = `<div class="detail-section"><h4>Informações Gerais</h4>
    <div class="detail-row"><span class="dr-label">Check List</span><span class="dr-val">${cl.label||sub.type}</span></div>
    <div class="detail-row"><span class="dr-label">Colaborador</span><span class="dr-val">${sub.userName}</span></div>
    <div class="detail-row"><span class="dr-label">Data/Hora</span><span class="dr-val">${formatDateTime(sub.date)}</span></div>
    <div class="detail-row"><span class="dr-label">Status</span><span class="dr-val ${st}">
      ${st==='ok'?'✓ Conforme':st==='pending'?'⏳ Pendente sync':st==='archived'?'📦 Equip. Removido':'⚠ '+nc+' Não Conforme(s)'}
    </span></div>
    <div class="detail-row"><span class="dr-label">Pontuação</span><span class="dr-val" style="color:var(--orange)">+${sub.pts||0} pts</span></div>
  </div>`;

  if (sub.archived) {
    html += `<div class="detail-section" style="border-left:4px solid var(--gray)">
      <h4 style="color:var(--gray)">📦 Equipamento Removido da Frota</h4>
      <p style="font-size:13px;color:var(--text-light)">Este envio foi arquivado pois o equipamento foi inativado. O histórico é mantido para fins de auditoria.</p>
    </div>`;
  }

  // Meta
  const metaLabels = { operador:'Operador', local:'Local', data:'Data', equipamento:'Equipamento', veiculo:'Veículo', km:'KM', horimetro:'Horímetro', tipo:'Tipo', situacao:'Situação' };
  const metaEntries = Object.entries(sub.meta||{}).filter(([k,v]) => k!=='observacoes' && k!=='ot' && v);
  if (metaEntries.length) {
    html += `<div class="detail-section"><h4>Identificação</h4>
      ${metaEntries.map(([k,v]) => `<div class="detail-row"><span class="dr-label">${metaLabels[k]||k}</span><span class="dr-val">${v}</span></div>`).join('')}
    </div>`;
  }

  // Checklist items
  cl.steps?.filter(s => s.type==='checklist').forEach(step => {
    html += `<div class="detail-section"><h4>${step.title}</h4>
      ${step.items.map(item => {
        const ans = sub.answers?.[item.id];
        if (!ans) return '';
        const c = ans.val==='C'?'ok':ans.val==='NC'?'nc':'na';
        const l = ans.val==='C'?'✓ Conforme':ans.val==='NC'?'✗ Não Conforme':'N/A';
        return `<div class="detail-row">
          <span class="dr-label">${item.label} ${item.pts>1?`<small style="color:var(--orange)">⭐×${item.pts}</small>`:''}  </span>
          <span class="dr-val ${c}">${l}</span>
        </div>${ans.obs?`<div class="detail-obs">📝 ${ans.obs}</div>`:''}`;
      }).join('')}
    </div>`;
  });

  if (sub.meta?.observacoes) html += `<div class="detail-section"><h4>Observações Gerais</h4><div class="detail-obs">${sub.meta.observacoes}</div></div>`;
  if (sub.meta?.ot) html += `<div class="detail-section"><h4>Ordem de Trabalho</h4><div class="detail-row"><span class="dr-label">OT</span><span class="dr-val">${sub.meta.ot}</span></div></div>`;

  document.getElementById('detail-content').innerHTML = html;
  showScreen('screen-detail');
}

// ─── MODAIS ────────────────────────────────────────
function openModal(id)  { document.getElementById(id).classList.remove('hidden'); }
function closeModal(id) { document.getElementById(id).classList.add('hidden'); }

// ─── UTILS ─────────────────────────────────────────
function formatDate(iso) {
  if (!iso) return '–';
  return new Date(iso).toLocaleDateString('pt-BR', { day:'2-digit', month:'2-digit', year:'numeric' });
}
function formatDateTime(iso) {
  if (!iso) return '–';
  return new Date(iso).toLocaleString('pt-BR', { day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit' });
}

// ─── SERVICE WORKER ────────────────────────────────
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(()=>{});

// ─── INIT ──────────────────────────────────────────
updateSyncUI();

// Seed demo submissions
(function seedDemo() {
  if (DB.submissions().length > 0) return;
  const demos = [
    { user:'andre',     userName:'André',     type:'maquinas', meta:{operador:'André',local:'Florestal',data:'2026-05-10',equipamento:'EH-03',horimetro:'12208.9',tipo:'Preventivo'},
      answers:{ lubrificacao:{val:'C'}, abastecimento:{val:'NC',obs:'Bomba com defeito'}, limpeza:{val:'C'}, filtro_ar:{val:'C'}, filtro_oleo:{val:'C'}, nivel_oleo:{val:'C'}, radiador:{val:'C'}, pneu_rodas:{val:'C'}, suspensao:{val:'NC',obs:'Pistons merejando'}, farois:{val:'NC',obs:'Faróis quebrados'}, buzina:{val:'C'}, vidros:{val:'NC',obs:'Vidro dianteiro trincado'}, eletrico:{val:'NC',obs:'Selenoide com defeito'}, implementos:{val:'C'}, estado_geral:{val:'NC',obs:'Lataria amassada'} },
      pts:80, synced:true, archived:false, date:'2026-05-10T08:00:00Z' },
    { user:'samuel',    userName:'Samuel',    type:'caminhao', meta:{operador:'Samuel',local:'Obra Lev',data:'2026-05-08',veiculo:'CB-06',km:'441489',situacao:'Estou fixo no caminhão'},
      answers:{ lubrificacao:{val:'C'}, abastecimento:{val:'C'}, pneus:{val:'C'}, suspensao:{val:'C'}, luzes:{val:'C'}, alarmes:{val:'C'}, freios:{val:'C'}, painel:{val:'C'} },
      pts:110, synced:true, archived:false, date:'2026-05-08T09:00:00Z' },
    { user:'franciele', userName:'Franciele', type:'caminhao', meta:{operador:'Franciele',local:'Pedro Leopoldo',data:'2026-05-07',veiculo:'CB-037',km:'1384219'},
      answers:{ lubrificacao:{val:'C'}, pneus:{val:'NC',obs:'Pneu lado direito liso'}, suspensao:{val:'C'}, luzes:{val:'NC',obs:'Farolete traseiro'}, alarmes:{val:'C'}, freios:{val:'C'}, painel:{val:'C'} },
      pts:80, synced:true, archived:false, date:'2026-05-07T07:00:00Z' },
    { user:'emerson',   userName:'Emerson',   type:'carro', meta:{operador:'Emerson',local:'Pro Base Sete Lagoas',data:'2026-04-28',veiculo:'CA-12',km:'179498'},
      answers:{ crv:{val:'C'}, triangulo:{val:'C'}, extintor:{val:'C'}, lataria:{val:'C'}, pneus:{val:'C'}, limpeza:{val:'C'}, luzes:{val:'NC',obs:'Farolete queimado'}, freios:{val:'C'} },
      pts:80, synced:true, archived:false, date:'2026-04-28T08:00:00Z' },
  ];
  demos.forEach(s => { s.id = 'sub_demo_' + Math.random().toString(36).slice(2,9); DB.saveSubmission(s); });
})();

// ═══════════════════════════════════════════════════
// MÓDULO: FUNÇÕES / CARGOS
// ═══════════════════════════════════════════════════

// ── STORAGE ──
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

function fidgen() { return 'fc_' + Date.now() + '_' + Math.random().toString(36).slice(2,5); }

function seedFuncoes() {
  const funcoes = [
    { id:'fc_motorista',  nome:'Motorista',           desc:'Condução de caminhões e veículos de apoio', cor:'navy',   cls:['caminhao','carro'] },
    { id:'fc_operador',   nome:'Operador de Máquina',  desc:'Operação de escavadeiras, patrol e retroescavadeiras', cor:'orange', cls:['maquinas'] },
    { id:'fc_mecanico',   nome:'Mecânico',             desc:'Manutenção e reparos da frota', cor:'teal',   cls:[] },
    { id:'fc_encarregado',nome:'Encarregado de Obra',  desc:'Supervisão e controle das frentes de serviço', cor:'purple', cls:[] },
    { id:'fc_aux',        nome:'Auxiliar / Ajudante',  desc:'Apoio geral nas operações', cor:'gray',   cls:[] },
  ];
  DB.set('garra_funcoes', funcoes);
  return funcoes;
}

// ── COR → CSS ──
const COR_MAP = {
  navy:'#1a2158', orange:'#f07c1e', green:'#22c97c',
  purple:'#7c4dff', red:'#e8394d', teal:'#00897b',
  brown:'#795548', gray:'#8b95b8',
};

// ── SUB-TABS USUÁRIOS ──
function usersSubTab(tab, btn) {
  document.querySelectorAll('#tab-users .log-subtab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('#tab-users .log-subpanel').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('users-sub-'+tab).classList.add('active');
  if (tab === 'funcoes') renderFuncoes();
  if (tab === 'colaboradores') { populateFuncaoFilters(); renderUsers(); }
}

// ── ESTADO MODAL FUNÇÃO ──
let funcaoEditId    = null;
let funcaoCorSel    = 'navy';
let funcaoClsSel    = [];

function openFuncaoModal(id) {
  funcaoEditId = id;
  funcaoCorSel = 'navy';
  funcaoClsSel = [];

  const f = id ? FuncaoDB.byId(id) : null;
  document.getElementById('funcao-modal-title').textContent = id ? 'Editar Função' : 'Nova Função';
  document.getElementById('funcao-edit-id').value = id || '';
  document.getElementById('fc-nome').value = f?.nome || '';
  document.getElementById('fc-desc').value = f?.desc || '';

  if (f) {
    funcaoCorSel = f.cor || 'navy';
    funcaoClsSel = [...(f.cls || [])];
  }

  // Reset color picker
  document.querySelectorAll('.color-opt').forEach(b => {
    b.classList.toggle('active', b.dataset.color === funcaoCorSel);
  });

  // Build CL checkboxes
  const allCLs = DB.allCLs();
  const clsEl  = document.getElementById('fc-cls-list');
  clsEl.innerHTML = Object.values(allCLs).map(cl => `
    <label class="funcao-cl-item">
      <input type="checkbox" value="${cl.id}"
        ${funcaoClsSel.includes(cl.id) ? 'checked' : ''}
        onchange="toggleFuncaoCL('${cl.id}',this.checked)" />
      <div class="funcao-cl-icon">${cl.icon}</div>
      <div>
        <div class="funcao-cl-name">${cl.label}</div>
        <div class="funcao-cl-desc">${cl.desc || ''}</div>
      </div>
    </label>`).join('');

  openModal('funcao-modal');
}

function selectFuncaoColor(cor, btn) {
  funcaoCorSel = cor;
  document.querySelectorAll('.color-opt').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
}

function toggleFuncaoCL(clId, checked) {
  if (checked) { if (!funcaoClsSel.includes(clId)) funcaoClsSel.push(clId); }
  else funcaoClsSel = funcaoClsSel.filter(id => id !== clId);
}

function saveFuncao() {
  const nome = document.getElementById('fc-nome').value.trim();
  if (!nome) { alert('Informe o nome da função.'); return; }

  const f = {
    id:   funcaoEditId || fidgen(),
    nome,
    desc: document.getElementById('fc-desc').value.trim(),
    cor:  funcaoCorSel,
    cls:  funcaoClsSel,
  };
  FuncaoDB.add(f);
  funcaoEditId = null;
  closeModal('funcao-modal');
  renderFuncoes();
  populateFuncaoFilters();
  populateUserModalFuncoes();
}

function renderFuncoes() {
  const el = document.getElementById('funcoes-list');
  if (!el) return;
  const funcoes  = FuncaoDB.get();
  const allCLs   = DB.allCLs();
  const usuarios = DB.users();

  if (!funcoes.length) {
    el.innerHTML = '<div class="empty-state"><div class="es-icon">🏷</div>Nenhuma função cadastrada.</div>';
    return;
  }

  el.innerHTML = funcoes.map(f => {
    const cor     = COR_MAP[f.cor] || COR_MAP.navy;
    const count   = usuarios.filter(u => u.funcao === f.id).length;
    const clNames = (f.cls || []).map(id => allCLs[id]?.label).filter(Boolean);
    const clText  = clNames.length
      ? `✅ ${clNames.join(', ')}`
      : '✅ Todos os check lists';

    return `<div class="funcao-card">
      <div class="funcao-card-dot" style="background:${cor}"></div>
      <div class="funcao-card-body">
        <div class="funcao-card-name">${f.nome}</div>
        <div class="funcao-card-meta">${f.desc || ''}${count > 0 ? ' • '+count+' colaborador(es)' : ''}</div>
        <div class="funcao-card-cls">${clText}</div>
      </div>
      <div class="ui-actions">
        <button class="fleet-btn edit" onclick="openFuncaoModal('${f.id}')">✎</button>
        <button class="fleet-btn remove" onclick="removeFuncao('${f.id}')">✕</button>
      </div>
    </div>`;
  }).join('');
}

function removeFuncao(id) {
  const f = FuncaoDB.byId(id);
  const count = DB.users().filter(u => u.funcao === id).length;
  const msg = count > 0
    ? `Remover a função "${f?.nome}"? ${count} colaborador(es) ficarão sem função vinculada.`
    : `Remover a função "${f?.nome}"?`;
  if (!confirm(msg)) return;
  FuncaoDB.remove(id);
  renderFuncoes();
  populateFuncaoFilters();
  renderUsers();
}

// ── POPULAR SELECTS DE FUNÇÃO ──
function populateFuncaoFilters() {
  const funcoes = FuncaoDB.get();
  // Filter na aba colaboradores
  const ff = document.getElementById('filter-funcao');
  if (ff) ff.innerHTML = '<option value="">Todas as funções</option>' +
    funcoes.map(f => `<option value="${f.id}">${f.nome}</option>`).join('');
}

function populateUserModalFuncoes() {
  const funcoes = FuncaoDB.get();
  const opts = '<option value="">Selecione a função...</option>' +
    funcoes.map(f => `<option value="${f.id}">${f.nome}</option>`).join('');
  ['nu-funcao','eu-funcao'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = opts;
  });

  // Popular veículos (todos da frota)
  const fleet = DB.fleet();
  const veiculos = [
    ...((fleet.maquinas||[]).filter(v=>v.active).map(v=>`<option value="${v.id}">🚜 ${v.id}${v.desc?' – '+v.desc:''}</option>`)),
    ...((fleet.carro||[]).filter(v=>v.active).map(v=>`<option value="${v.id}">🚗 ${v.id}${v.desc?' – '+v.desc:''}</option>`)),
    ...((fleet.caminhao||[]).filter(v=>v.active).map(v=>`<option value="${v.id}">🚛 ${v.id}${v.desc?' – '+v.desc:''}</option>`)),
  ].join('');
  const veiculoOpts = '<option value="">Sem vínculo fixo</option>' + veiculos;
  ['nu-veiculo','eu-veiculo'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = veiculoOpts;
  });
}

// ═══════════════════════════════════════════════════
// RENDERIZAR USUÁRIOS — versão atualizada com função/veículo
// ═══════════════════════════════════════════════════

// Override da função renderUsers com versão completa
const _renderUsersOriginal = renderUsers;
window.renderUsers = function renderUsers() {
  // Popula selects de função sempre que renderiza
  populateUserModalFuncoes();
  populateFuncaoFilters();

  const filterFuncao = document.getElementById('filter-funcao')?.value || '';
  const filterRole   = document.getElementById('filter-role')?.value   || '';

  let users = DB.users();
  if (filterFuncao) users = users.filter(u => u.funcao === filterFuncao);
  if (filterRole)   users = users.filter(u => u.role   === filterRole);

  const el = document.getElementById('users-list');
  if (!el) return;

  const allCLs = DB.allCLs();

  el.innerHTML = users.map(u => {
    const perfil      = u.role==='manager'?'Gestor':u.role==='superior'?'Superior':'Operador';
    const perfilColor = u.role==='manager'?'var(--orange)':u.role==='superior'?'var(--navy-light)':'var(--navy)';
    const funcao      = u.funcao ? FuncaoDB.byId(u.funcao) : null;
    const cor         = funcao ? (COR_MAP[funcao.cor] || COR_MAP.navy) : 'var(--navy)';

    // Check lists visíveis da função
    const clsVisiveis = funcao?.cls?.length
      ? funcao.cls.map(id => allCLs[id]?.label).filter(Boolean).join(', ')
      : '';

    return `<div class="user-item">
      <div class="user-item-top">
        <div class="ui-avatar" style="background:${perfilColor}">${u.name.charAt(0)}</div>
        <div class="ui-info" style="flex:1">
          <div class="ui-name">${u.name}</div>
          <div class="ui-role">${u.login} • ${u.submissions||0} envios • ${u.pts||0} pts</div>
          ${clsVisiveis ? `<div style="font-size:10px;color:var(--orange);margin-top:2px">📋 ${clsVisiveis}</div>` : ''}
        </div>
        <div class="ui-actions">
          <button class="fleet-btn edit" onclick="openUserEdit('${u.login}')">✎</button>
          ${u.login !== currentUser.login ? `<button class="fleet-btn remove" onclick="openUserRemove('${u.login}')">✕</button>` : ''}
        </div>
      </div>
      <div class="user-item-tags">
        <span class="role-badge ${u.role}">${perfil}</span>
        ${funcao ? `<span class="funcao-tag ${funcao.cor}" style="border:1px solid ${cor}22">${funcao.nome}</span>` : ''}
        ${u.veiculo ? `<span class="veiculo-tag">🚗 ${u.veiculo}</span>` : ''}
      </div>
    </div>`;
  }).join('') || '<div class="empty-state"><div class="es-icon">👤</div>Nenhum colaborador encontrado.</div>';
};

// Override openUserEdit para incluir função e veículo
const _openUserEditOriginal = openUserEdit;
window.openUserEdit = function openUserEdit(login) {
  populateUserModalFuncoes();
  const u = DB.users().find(u => u.login === login);
  if (!u) return;
  editingUserLogin = login;
  document.getElementById('eu-name').value   = u.name;
  document.getElementById('eu-login').textContent = u.login;
  document.getElementById('eu-role').value   = u.role;
  document.getElementById('eu-pass').value   = '';
  if (document.getElementById('eu-funcao'))  document.getElementById('eu-funcao').value  = u.funcao  || '';
  if (document.getElementById('eu-veiculo')) document.getElementById('eu-veiculo').value = u.veiculo || '';
  openModal('user-edit-modal');
};



// Override renderDriverDashboard para filtrar CLs pela função
const _renderDriverOriginal = renderDriverDashboard;
window.renderDriverDashboard = function renderDriverDashboard() {
  const u = DB.users().find(u => u.login === currentUser.login);
  if (!u) return;
  currentUser = u;

  document.getElementById('driver-pts').textContent    = u.pts || 0;
  document.getElementById('driver-streak').textContent = `🔥 ${u.submissions || 0} envios`;

  const drivers = DB.users().filter(u => u.role === 'driver').sort((a,b) => (b.pts||0)-(a.pts||0));
  const rank    = drivers.findIndex(d => d.login === u.login) + 1;
  document.getElementById('driver-rank').textContent = '#' + rank;

  const maxPts = Math.max(...drivers.map(d => d.pts||0), 1);
  document.getElementById('driver-bar').style.width = Math.round(((u.pts||0)/maxPts)*100) + '%';

  // Filtra CLs pela função do colaborador
  const allCLs  = DB.allCLs();
  const funcao  = u.funcao ? FuncaoDB.byId(u.funcao) : null;
  const visivel = funcao?.cls?.length
    ? Object.fromEntries(Object.entries(allCLs).filter(([id]) => funcao.cls.includes(id)))
    : allCLs;

  const cardsEl = document.getElementById('driver-cl-cards');
  cardsEl.innerHTML = Object.values(visivel).map(cl => `
    <div class="cl-card" onclick="startChecklist('${cl.id}')">
      <div class="clc-icon">${cl.icon}</div>
      <div class="clc-body">
        <div class="clc-name">${cl.label}</div>
        <div class="clc-desc">${cl.desc || ''}</div>
      </div>
      <div class="clc-arrow">›</div>
    </div>`).join('');

  // Mostra veículo fixo se houver
  if (u.veiculo) {
    cardsEl.insertAdjacentHTML('beforebegin', `
      <div style="background:rgba(240,124,30,.08);border:1px solid rgba(240,124,30,.2);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:12px;font-size:12px;color:var(--orange-dk)">
        🚗 Veículo fixo vinculado: <strong>${u.veiculo}</strong>
      </div>`);
  }

  // Histórico
  const subs   = DB.submissions().filter(s => s.user === u.login);
  const histEl = document.getElementById('driver-history');
  if (!subs.length) {
    histEl.innerHTML = '<div class="empty-state"><div class="es-icon">📋</div>Nenhum check list enviado ainda!</div>';
    return;
  }
  histEl.innerHTML = subs.slice(0,15).map(s => {
    const cl  = allCLs[s.type] || {};
    const nc  = countNC(s);
    const st  = s.archived ? 'archived' : (s.synced === false ? 'pending' : (nc > 0 ? 'nc' : 'ok'));
    const lbl = s.archived ? '📦 Equip. Removido' : st==='pending' ? '⏳ Sync pendente' : nc>0 ? `⚠ ${nc} NC` : '✓ Conforme';
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
};

// Inicializa funções ao carregar gestor
const _renderManagerOriginal = renderManagerDashboard;
window.renderManagerDashboard = function renderManagerDashboard() {
  _renderManagerOriginal();
  populateFuncaoFilters();
  populateUserModalFuncoes();
};

// ═══════════════════════════════════════════════════
// INTEGRAÇÃO COM API — substitui localStorage para
// operações críticas (login, usuários, envios)
// ═══════════════════════════════════════════════════

// ── LOGIN VIA API ──────────────────────────────────
window.doLogin = async function doLogin() {
  const login = (document.getElementById('login-user').value || '').trim().toLowerCase();
  const pass  =  document.getElementById('login-pass').value || '';
  const err   =  document.getElementById('login-error');

  // Mostra loading no botão
  const btn = document.querySelector('#screen-login .btn-primary');
  if (btn) { btn.textContent = 'Entrando...'; btn.disabled = true; }

  try {
    // Tenta via API primeiro
    const user = await GarraDB.login(login, pass);
    err.classList.add('hidden');
    // Salva no cache local para uso offline
    localStorage.setItem('garra_current_user', JSON.stringify(user));
    currentUser = user;
    if (user.role === 'manager') showManager();
    else if (user.role === 'superior') showSuperior();
    else showDriver();
  } catch (e) {
    if (e.message === 'OFFLINE' || e.message.includes('fetch')) {
      // Fallback offline — tenta localStorage
      const cached = DB.users().find(u => u.login === login && u.pass === pass);
      if (cached) {
        err.classList.add('hidden');
        currentUser = cached;
        if (cached.role === 'manager') showManager();
        else if (cached.role === 'superior') showSuperior();
        else showDriver();
      } else {
        err.textContent = 'Offline e usuário não encontrado no cache local.';
        err.classList.remove('hidden');
      }
    } else {
      err.textContent = 'Usuário ou senha incorretos.';
      err.classList.remove('hidden');
    }
  } finally {
    if (btn) { btn.textContent = 'Entrar'; btn.disabled = false; }
  }
};

// ── CRIAR USUÁRIO VIA API ──────────────────────────
window.saveNewUser = async function saveNewUser() {
  const name    = document.getElementById('nu-name').value.trim();
  const login   = document.getElementById('nu-user').value.trim().toLowerCase();
  const pass    = document.getElementById('nu-pass').value;
  const role    = document.getElementById('nu-role').value;
  const funcao  = document.getElementById('nu-funcao')?.value  || '';
  const veiculo = document.getElementById('nu-veiculo')?.value || '';

  if (!name || !login || !pass) { alert('Preencha todos os campos.'); return; }

  const btn = document.querySelector('#user-modal .btn-primary');
  if (btn) { btn.textContent = 'Salvando...'; btn.disabled = true; }

  try {
    await GarraDB.criarUsuario({ login, nome: name, senha: pass, perfil: role });
    // Salva também no localStorage para uso offline e campos extras (funcao, veiculo)
    DB.saveUser({ name, login, pass, role, funcao, veiculo, pts:0, submissions:0 });
    ['nu-name','nu-user','nu-pass'].forEach(id => document.getElementById(id).value = '');
    closeModal('user-modal');
    renderUsers();
    populateSubmissionFilters();
    alert('✅ Colaborador cadastrado com sucesso!');
  } catch(e) {
    if (e.message === 'OFFLINE') {
      // Salva só local se offline
      DB.saveUser({ name, login, pass, role, funcao, veiculo, pts:0, submissions:0 });
      OfflineQueue.add({ path:'/usuarios', options:{ method:'POST', body: JSON.stringify({ login, nome:name, senha:pass, perfil:role }) }});
      ['nu-name','nu-user','nu-pass'].forEach(id => document.getElementById(id).value = '');
      closeModal('user-modal');
      renderUsers();
      alert('⚠️ Salvo localmente. Será sincronizado quando online.');
    } else {
      alert('Erro ao cadastrar: ' + e.message);
    }
  } finally {
    if (btn) { btn.textContent = 'Salvar'; btn.disabled = false; }
  }
};

// ── EDITAR USUÁRIO VIA API ─────────────────────────
window.saveEditUser = async function saveEditUser() {
  const name    = document.getElementById('eu-name').value.trim();
  const role    = document.getElementById('eu-role').value;
  const pass    = document.getElementById('eu-pass').value;
  const funcao  = document.getElementById('eu-funcao')?.value  || '';
  const veiculo = document.getElementById('eu-veiculo')?.value || '';
  if (!name) { alert('Informe o nome.'); return; }

  const btn = document.querySelector('#user-edit-modal .btn-primary');
  if (btn) { btn.textContent = 'Salvando...'; btn.disabled = true; }

  try {
    // Atualiza no banco (a API aceita PATCH para atualização)
    if (pass) {
      await apiFetch(`/usuarios/${editingUserLogin}/senha`, {
        method: 'PATCH',
        body: JSON.stringify({ senha: pass })
      }).catch(() => {}); // ignora se endpoint não existir ainda
    }
    // Atualiza local sempre
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
    if (editingUserLogin === currentUser.login) {
      currentUser.name = name; currentUser.role = role;
    }
    editingUserLogin = null;
    closeModal('user-edit-modal');
    renderUsers();
    populateSubmissionFilters();
  } catch(e) {
    alert('Erro ao salvar: ' + e.message);
  } finally {
    if (btn) { btn.textContent = 'Salvar Alterações'; btn.disabled = false; }
  }
};

// ── REMOVER USUÁRIO VIA API ────────────────────────
window.confirmRemoveUser = async function confirmRemoveUser() {
  if (!pendingRemoveUserLogin) return;
  try {
    await GarraDB.removerUsuario(pendingRemoveUserLogin);
  } catch(e) {
    console.warn('API remove falhou, removendo local:', e.message);
  }
  DB.removeUser(pendingRemoveUserLogin);
  pendingRemoveUserLogin = null;
  closeModal('user-remove-modal');
  renderUsers();
  populateSubmissionFilters();
};

// ── SALVAR ENVIO VIA API ───────────────────────────
const _submitOriginal = submitChecklist;
window.submitChecklist = async function submitChecklist() {
  const cl = getCL();
  const id = 'sub_' + Date.now() + '_' + Math.random().toString(36).slice(2,7);
  const submission = {
    id, user: currentUser.login, userName: currentUser.name,
    type: currentCLId, clLabel: cl.label,
    date: new Date().toISOString(),
    meta: { ...formMeta }, answers: { ...formAnswers },
    synced: false, archived: false,
  };
  submission.pts = calculatePoints(submission, cl);

  // Salva localmente primeiro (garante offline)
  DB.saveSubmission({ ...submission, synced: false });

  // Tenta enviar para API
  try {
    await GarraDB.salvarEnvio({
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
    });
    submission.synced = true;
    DB.saveSubmission(submission);
  } catch(e) {
    // Offline — fica na fila
    DB.addPending(submission);
  }

  // Atualiza pts do usuário
  const users = DB.users();
  const idx   = users.findIndex(u => u.login === currentUser.login);
  if (idx >= 0) {
    users[idx].pts         = (users[idx].pts        || 0) + submission.pts;
    users[idx].submissions = (users[idx].submissions || 0) + 1;
    DB.set('garra_users', users);
    currentUser = users[idx];
    // Atualiza pts na API
    GarraDB.salvarEnvio && apiFetch(`/usuarios/${currentUser.login}/pts?pts=${submission.pts}`, { method:'PATCH' }).catch(()=>{});
  }

  showScreen('screen-success');
  document.getElementById('success-title').textContent = 'Check List Enviado! 🎉';
  document.getElementById('success-msg').textContent   = submission.synced
    ? 'Salvo e sincronizado com sucesso.'
    : 'Salvo localmente. Será sincronizado quando online.';
  document.getElementById('pts-earned').textContent = `+${submission.pts} pts`;
};

// ── CARREGAR USUÁRIOS DA API NO STARTUP ────────────
async function syncUsersFromAPI() {
  try {
    const apiUsers = await GarraDB.getUsuarios();
    if (!apiUsers?.length) return;
    // Mescla com localStorage preservando campos locais (funcao, veiculo, pass)
    const local = DB.users();
    const merged = apiUsers.map(au => {
      const loc = local.find(l => l.login === au.login) || {};
      return {
        login:       au.login,
        name:        au.nome || loc.name || au.login,
        pass:        loc.pass || '***',
        role:        au.perfil || loc.role || 'driver',
        funcao:      loc.funcao  || '',
        veiculo:     loc.veiculo || '',
        pts:         au.pts         ?? loc.pts         ?? 0,
        submissions: au.total_envios ?? loc.submissions ?? 0,
      };
    });
    DB.set('garra_users', merged);
    console.log('✅ Usuários sincronizados da API:', merged.length);
  } catch(e) {
    console.warn('⚠️ Sincronização de usuários falhou (offline?):', e.message);
  }
}

// Executa ao iniciar
syncUsersFromAPI();
