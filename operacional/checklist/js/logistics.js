/* ═══════════════════════════════════════════════════════
   logistics.js — Módulo de Logística de Carros de Apoio
   Garra Terraplenagem e Caçambas v3

   ► Cadastro de Motoristas (CRUD completo)
   ► Cadastro de Veículos de Apoio (CRUD + campos extras dinâmicos)
   ► Registros de logística (quem está com qual carro / onde)
   ► Relatório imprimível com logo
═══════════════════════════════════════════════════════ */

// ─── STORAGE ───────────────────────────────────────────
// (09/07/2026) SERVIDOR-FIRST: o localStorage vira SNAPSHOT do servidor
// (leitura offline). Toda escrita vai ao servidor; sem rede, entra na outbox
// e é reenviada sozinha (registro_id/veiculo_id/motor_id = idempotência).
const LogSync = {
  _timer: null,
  _hdr() { return { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (typeof ckToken === 'function' ? ckToken() : '') }; },

  async pull() {
    try {
      const h = { 'Authorization': this._hdr().Authorization };
      const [rs, vs, ms] = await Promise.all([
        fetch('/logistica/registros?limit=500', { headers: h }),
        // (09/07/2026) FONTE ÚNICA: carros de apoio + motos do
        // Cadastros→Equipamentos do Admin (operacional.equipamentos)
        fetch('/logistica/frota-apoio', { headers: h }),
        fetch('/logistica/motoristas',{ headers: h })
      ]);
      if (!rs.ok || !vs.ok || !ms.ok) return false;
      const [regs, veics, mots] = await Promise.all([rs.json(), vs.json(), ms.json()]);
      DB.set('garra_logistics', regs.map(r => ({
        id: r.registro_id, resp: r.responsavel,
        date: String(r.data_hora || '').replace(' ', 'T'),
        cars: r.carros || []
      })));
      DB.set('garra_log_cars', veics.map(v => ({
        id: v.codigo, carId: v.codigo, plate: v.placa || '',
        model: v.modelo || v.descricao || '',
        year: v.ano || null, color: '', status: 'disponivel',
        extras: [], obs: (v.marca || ''), _vistoEm: null
      })));
      DB.set('garra_log_drivers', mots.map(m => ({
        id: m.motor_id, name: m.nome, cnh: m.cnh || '', tel: m.telefone || '',
        status: m.status || 'ativo', obs: m.observacoes || ''
      })));
      // Semeadura única: só MOTORISTAS (carros vêm do Cadastros→Equipamentos)
      if (!mots.length && !DB.get('garra_log_seeded')) {
        DB.set('garra_log_seeded', true);
        for (const d of seedDriversPadrao()) await this.send('/logistica/motoristas','POST', LogSync._driverPayload(d), true);
        return this.pull();
      }
      return true;
    } catch (e) { return false; }
  },

  _carPayload(c) {
    return { veiculo_id: c.id, car_id: c.carId, placa: c.plate || '', modelo: c.model || '',
             ano: c.year || null, cor: c.color || '', status: c.status || 'disponivel',
             extras: c.extras || [], observacoes: c.obs || '', visto_em: c._vistoEm || null };
  },
  _driverPayload(d) {
    return { motor_id: d.id, nome: d.name, cpf: '', cnh: d.cnh || '', telefone: d.tel || '',
             status: d.status || 'ativo', observacoes: d.obs || '' };
  },
  _entryPayload(e) {
    return { registro_id: e.id, responsavel: e.resp, data_hora: e.date, carros: e.cars || [] };
  },

  outbox() { return DB.get('garra_log_outbox') || []; },
  async flush() {
    const jobs = this.outbox();
    if (!jobs.length) return;
    const restantes = [];
    for (const j of jobs) {
      try {
        const r = await fetch(j.path, { method: j.method, headers: this._hdr(),
                                        body: j.body ? JSON.stringify(j.body) : undefined });
        if (!r.ok && r.status >= 500) restantes.push(j); // 5xx: tenta de novo depois
        // 4xx: descarta (inválido não se cura com retry)
      } catch (e) { restantes.push(j); }
    }
    DB.set('garra_log_outbox', restantes);
  },

  async send(path, method, body, silencioso) {
    try {
      const r = await fetch(path, { method, headers: this._hdr(),
                                    body: body ? JSON.stringify(body) : undefined });
      if (r.status === 409) {
        alert('⚠️ Este item foi alterado por outra pessoa — a tela será atualizada.');
        await this.pull(); refreshLogUI();
        return false;
      }
      if (!r.ok && r.status < 500) {
        const d = await r.json().catch(() => ({}));
        if (!silencioso) alert('Erro ao salvar: ' + (d.detail || r.status));
        return false;
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return true;
    } catch (e) {
      // Sem rede / 5xx → outbox (idempotente pelos ids fixos)
      const o = this.outbox(); o.push({ path, method, body }); DB.set('garra_log_outbox', o);
      if (!silencioso && typeof alert === 'function') console.log('[Logística] offline — enfileirado');
      return true;
    }
  },

  ensure() {
    this.flush().then(() => this.pull()).then(ok => { if (ok) refreshLogUI(); });
    if (this._timer) clearInterval(this._timer);
    // Auto-refresh: todos os dispositivos convergem em até 60s
    this._timer = setInterval(() => {
      this.flush().then(() => this.pull()).then(ok => { if (ok) refreshLogUI(); });
    }, 60000);
  }
};
window.addEventListener('online', () => LogSync.ensure());

function refreshLogUI() {
  const ativo = document.querySelector('.log-subpanel.active');
  if (!ativo) return;
  const sub = ativo.id.replace('log-sub-', '');
  if (sub === 'motoristas') renderLogDrivers();
  else if (sub === 'veiculos') renderLogCars();
  else if (sub === 'registros') {
    renderLogisticsKPIs('log-kpi-active','log-kpi-idle','log-kpi-drivers','log-kpi-total');
    renderCurrentFleet('log-current-fleet'); populateLogFilters(); renderLogFiltered();
  }
}

const LDB = {
  get: k => DB.get(k),
  set: (k,v) => DB.set(k,v),

  // Registros
  entries()  { return this.get('garra_logistics') || []; },
  saveEntry(e) {
    const list = this.entries();
    const idx = list.findIndex(x => x.id === e.id);
    if (idx>=0) list[idx]=e; else list.unshift(e);
    this.set('garra_logistics', list);
    LogSync.send('/logistica/registros', 'POST', LogSync._entryPayload(e));
  },
  removeEntry(id) {
    this.set('garra_logistics', this.entries().filter(e=>e.id!==id));
    LogSync.send('/logistica/registros/' + encodeURIComponent(id), 'DELETE', null);
  },

  // Motoristas
  drivers()  { return this.get('garra_log_drivers') || seedDriversPadrao(); },
  saveDriver(d) {
    const list = this.drivers();
    const idx = list.findIndex(x=>x.id===d.id);
    if (idx>=0) list[idx]=d; else list.push(d);
    this.set('garra_log_drivers', list);
    LogSync.send('/logistica/motoristas', 'POST', LogSync._driverPayload(d));
  },
  removeDriver(id) {
    this.set('garra_log_drivers', this.drivers().filter(d=>d.id!==id));
    LogSync.send('/logistica/motoristas/' + encodeURIComponent(id), 'DELETE', null);
  },

  // Veículos de apoio
  logCars()  { return this.get('garra_log_cars') || seedCarsPadrao(); },
  saveLogCar(c) {
    // (09/07/2026) Fonte única: veículo se cadastra no Admin → Equipamentos
    alert('🚗 O cadastro de veículos agora é único: Admin → Cadastros → Equipamentos.\nCadastrou lá, aparece aqui na Logística automaticamente.');
  },
  removeLogCar(id) {
    alert('🚗 Para remover um veículo, desative-o no Admin → Cadastros → Equipamentos.');
  },

  currentStatus() {
    const map = {};
    [...this.entries()].reverse().forEach(entry => {
      (entry.cars||[]).forEach(car => { map[car.id] = {...car, date:entry.date, resp:entry.resp, entryId:entry.id}; });
    });
    return map;
  },
};

function ldid() { return 'ld_'+Date.now()+'_'+Math.random().toString(36).slice(2,5); }

function seedDriversPadrao() {
  const drivers = [
    {id:'ld_001', name:'ITALO AUGUSTO APARECIDO LINHARES', cnh:'B', tel:'', status:'ativo', obs:''},
    {id:'ld_002', name:'ELTON JOSE DE LIMA',               cnh:'B', tel:'', status:'ativo', obs:''},
    {id:'ld_003', name:'EMERSON GONCALVES TEIXEIRA',       cnh:'B', tel:'', status:'ativo', obs:''},
    {id:'ld_004', name:'JOAO PEDRO NUNES BARROS',          cnh:'B', tel:'', status:'ativo', obs:''},
    {id:'ld_005', name:'RICARDO DE OLIVEIRA SEVERINO',     cnh:'B', tel:'', status:'ativo', obs:''},
    {id:'ld_006', name:'BRUNA BARBOSA DOS SANTOS',         cnh:'B', tel:'', status:'ativo', obs:''},
    {id:'ld_007', name:'GERALDO APARECIDO NUNES',          cnh:'B', tel:'', status:'ativo', obs:''},
  ];
  return drivers;
}

function seedCarsPadrao() {
  const cars = [
    {id:'lc_001', carId:'CA-12', plate:'', model:'Gol',    year:2018, color:'Branco',  status:'disponivel', extras:[], obs:''},
    {id:'lc_002', carId:'CA-21', plate:'', model:'Strada', year:2020, color:'Prata',   status:'disponivel', extras:[], obs:''},
    {id:'lc_003', carId:'CA-32', plate:'', model:'D20',    year:2015, color:'Branco',  status:'disponivel', extras:[], obs:''},
    {id:'lc_004', carId:'CA-40', plate:'', model:'Strada', year:2021, color:'Vermelho',status:'disponivel', extras:[], obs:''},
    {id:'lc_005', carId:'CA-42', plate:'', model:'Strada', year:2021, color:'Prata',   status:'disponivel', extras:[], obs:''},
    {id:'lc_006', carId:'CA-44', plate:'', model:'Strada', year:2022, color:'Branco',  status:'disponivel', extras:[], obs:''},
    {id:'lc_007', carId:'CA-47', plate:'', model:'Strada', year:2022, color:'Cinza',   status:'disponivel', extras:[], obs:''},
    {id:'lc_008', carId:'CA-48', plate:'', model:'Strada', year:2023, color:'Branco',  status:'disponivel', extras:[], obs:''},
  ];
  return cars;
}

// (09/07/2026) seedLogDemos removido — registros nascem do servidor, zerados.

// ─── SUB-TAB SWITCH ────────────────────────────────────
function logSubTab(tab, btn) {
  document.querySelectorAll('.log-subtab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.log-subpanel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('log-sub-'+tab).classList.add('active');
  if (tab==='motoristas') renderLogDrivers();
  if (tab==='veiculos')   renderLogCars();
  if (tab==='registros')  { renderLogisticsKPIs('log-kpi-active','log-kpi-idle','log-kpi-drivers','log-kpi-total'); renderCurrentFleet('log-current-fleet'); populateLogFilters(); renderLogFiltered(); }
}

// ─── KPIs ──────────────────────────────────────────────
function renderLogisticsKPIs(activeId, idleId, driversId, totalId) {
  const status = LDB.currentStatus();
  const vals   = Object.values(status);
  const set = (id,val) => { const el=document.getElementById(id); if(el) el.textContent=val; };
  set(activeId,  vals.filter(v=>v.status==='em-campo').length);
  set(idleId,    vals.filter(v=>v.status!=='em-campo').length);
  set(driversId, vals.filter(v=>v.status==='em-campo'&&v.driver&&v.driver!=='OCIOSO / PARADO').length);
  set(totalId,   LDB.entries().length);
}

// ─── FLEET CARDS ──────────────────────────────────────
function renderCurrentFleet(containerId) {
  const el = document.getElementById(containerId);
  if (!el) return;
  const status = LDB.currentStatus();
  if (!Object.keys(status).length) {
    el.innerHTML='<div class="empty-state"><div class="es-icon">🚗</div>Nenhum registro ainda.</div>';
    return;
  }
  el.innerHTML = Object.entries(status).sort((a,b)=>a[0].localeCompare(b[0])).map(([carId,info])=>{
    const st=info.status||'parado';
    const stLabel=st==='em-campo'?'Em Campo':st==='disponivel'?'Disponível':'Ocioso';
    const driver=info.driver&&info.driver!=='OCIOSO / PARADO'?info.driver:'—';
    return `<div class="log-fleet-card ${st}" onclick="showLogEntryDetail('${info.entryId}')">
      <div class="lfc-id">${carId}</div>
      <div class="lfc-model">${info.model||''}</div>
      <span class="lfc-status-badge status-${st}">${stLabel}</span>
      <div class="lfc-driver">👤 ${driver}</div>
      <div class="lfc-dest">📍 ${info.dest||'—'}</div>
      <div class="lfc-time">${formatDate(info.date)}</div>
      <div class="lfc-resp">📋 ${info.resp}</div>
    </div>`;
  }).join('');
}

// ─── HISTORY ──────────────────────────────────────────
function renderLogHistory(containerId, limit, filterCar, filterResp, filterDate) {
  const el = document.getElementById(containerId);
  if (!el) return;
  let entries = LDB.entries();
  if (filterCar)  entries = entries.filter(e=>e.cars?.some(c=>c.id===filterCar));
  if (filterResp) entries = entries.filter(e=>e.resp===filterResp);
  if (filterDate) entries = entries.filter(e=>e.date?.slice(0,10)===filterDate);
  if (limit)      entries = entries.slice(0,limit);
  if (!entries.length) {
    el.innerHTML='<div class="empty-state"><div class="es-icon">📭</div>Nenhum registro encontrado.</div>';
    return;
  }
  const canEdit = currentUser?.role==='manager'||currentUser?.role==='superior';
  el.innerHTML = entries.map(entry=>`
    <div class="log-entry-card">
      <div class="lec-header">
        <div>
          <div class="lec-resp">📋 ${entry.resp}</div>
          <div class="lec-date">${formatDateTime(entry.date)}</div>
        </div>
        ${canEdit?`<div style="display:flex;gap:6px">
          <button class="fleet-btn edit" onclick="openLogModal('${entry.id}')">✎</button>
          <button class="fleet-btn remove" onclick="openLogEntryDelete('${entry.id}')">✕</button>
        </div>`:''}
      </div>
      <table class="lec-table">
        <thead><tr><th>Veículo</th><th>Modelo</th><th>Destino</th><th>Motorista</th><th>Status</th></tr></thead>
        <tbody>${(entry.cars||[]).map(car=>`<tr>
          <td>${car.id}</td><td>${car.model||'—'}</td><td>${car.dest||'—'}</td><td>${car.driver||'—'}</td>
          <td><span class="lec-status-dot ${car.status||'parado'}"></span>${car.status==='em-campo'?'Em Campo':car.status==='disponivel'?'Disponível':'Ocioso'}</td>
        </tr>`).join('')}</tbody>
      </table>
    </div>`).join('');
}

// ─── FILTERS ──────────────────────────────────────────
function populateLogFilters() {
  const entries = LDB.entries();
  const cars  = [...new Set(entries.flatMap(e=>(e.cars||[]).map(c=>c.id)))].sort();
  const resps = [...new Set(entries.map(e=>e.resp).filter(Boolean))];
  const fcEl = document.getElementById('log-filter-car');
  if (fcEl) fcEl.innerHTML='<option value="">Todos os carros</option>'+cars.map(c=>`<option value="${c}">${c}</option>`).join('');
  const frEl = document.getElementById('log-filter-resp');
  if (frEl) frEl.innerHTML='<option value="">Todos responsáveis</option>'+resps.map(r=>`<option value="${r}">${r}</option>`).join('');
}

function renderLogFiltered() {
  const car  = document.getElementById('log-filter-car')?.value  || '';
  const resp = document.getElementById('log-filter-resp')?.value || '';
  const date = document.getElementById('log-filter-date')?.value || '';
  renderLogHistory('log-history-list', 100, car, resp, date);
}
window.onLogFilterChange = function() { renderLogFiltered(); };

// ─── REGISTRO MODAL ────────────────────────────────────
let logCarRowCount = 0;
let logEditId = null;

function openLogModal(editId) {
  logEditId = editId||null;
  logCarRowCount = 0;
  document.getElementById('log-edit-id').value = logEditId||'';
  document.getElementById('log-modal-title').textContent = logEditId?'Editar Registro':'Novo Registro de Logística';
  document.getElementById('log-resp').value = logEditId?'':(currentUser?.name||'');
  const now=new Date(), pad=n=>String(n).padStart(2,'0');
  document.getElementById('log-datetime').value = `${now.getFullYear()}-${pad(now.getMonth()+1)}-${pad(now.getDate())}T${pad(now.getHours())}:${pad(now.getMinutes())}`;
  document.getElementById('log-car-rows').innerHTML='';
  if (logEditId) {
    const entry=LDB.entries().find(e=>e.id===logEditId);
    if (entry) {
      document.getElementById('log-resp').value=entry.resp;
      document.getElementById('log-datetime').value=entry.date?.slice(0,16)||'';
      (entry.cars||[]).forEach(car=>addLogCarRow(car));
    }
  }
  if (!logCarRowCount) addLogCarRow();
  openModal('log-modal');
}

function addLogCarRow(prefill) {
  logCarRowCount++;
  const rowId='lcr_'+logCarRowCount;

  // Build car options from LDB.logCars() + fallback list
  const logCars = LDB.logCars();
  const carOpts = logCars.map(c=>`<option value="${c.carId}" ${prefill?.id===c.carId?'selected':''}>${c.carId}${c.model?' – '+c.model:''}</option>`).join('');

  // Drivers from LDB.drivers()
  const driverOpts = LDB.drivers().filter(d=>d.status==='ativo').map(d=>`<option value="${d.name}" ${prefill?.driver===d.name?'selected':''}>${d.name}</option>`).join('');

  const statuses=[
    {val:'em-campo',   label:'🟠 Em Campo / Obra'},
    {val:'disponivel', label:'🟢 Disponível'},
    {val:'parado',     label:'⚫ Ocioso / Parado'},
  ];

  const rowsEl=document.getElementById('log-car-rows');
  const div=document.createElement('div');
  div.className='log-car-row'; div.id=rowId;
  div.innerHTML=`
    <button class="btn-rm-item" onclick="removeLogCarRow('${rowId}')" title="Remover">✕</button>
    <div class="field-group log-car-row-full">
      <label>Veículo</label>
      <select id="${rowId}-car">
        <option value="">Selecione o carro...</option>
        ${carOpts}
        <option value="MA-33 MOTO AZUL" ${prefill?.id==='MA-33 MOTO AZUL'?'selected':''}>MA-33 MOTO AZUL</option>
        <option value="MA-41 MOTO VERMELHA" ${prefill?.id==='MA-41 MOTO VERMELHA'?'selected':''}>MA-41 MOTO VERMELHA</option>
        <option value="Outro">Outro</option>
      </select>
    </div>
    <div class="field-group">
      <label>Status</label>
      <select id="${rowId}-status">
        ${statuses.map(s=>`<option value="${s.val}" ${prefill?.status===s.val?'selected':''}>${s.label}</option>`).join('')}
      </select>
    </div>
    <div class="field-group">
      <label>Destino / Obra</label>
      <input type="text" id="${rowId}-dest" placeholder="Ex.: FLORESTAL, PROBASE..." value="${prefill?.dest||''}" />
    </div>
    <div class="field-group log-car-row-full">
      <label>Motorista</label>
      <select id="${rowId}-driver-sel" onchange="syncDriverInput('${rowId}',this.value)">
        <option value="">Selecione ou digite abaixo...</option>
        ${driverOpts}
        <option value="OCIOSO / PARADO">OCIOSO / PARADO</option>
        <option value="__outro__">Outro (digitar)</option>
      </select>
    </div>
    <div class="field-group log-car-row-full">
      <label>Nome do motorista (confirme ou edite)</label>
      <input type="text" id="${rowId}-driver" placeholder="Nome completo do motorista" value="${prefill?.driver||''}" />
    </div>
    <div class="field-group log-car-row-full">
      <label>Observação (opcional)</label>
      <input type="text" id="${rowId}-obs" placeholder="Ex.: levando equipamento, retornando..." value="${prefill?.obs||''}" />
    </div>`;
  rowsEl.appendChild(div);
}

function syncDriverInput(rowId, val) {
  const inp = document.getElementById(rowId+'-driver');
  if (!inp) return;
  if (val && val !== '__outro__') inp.value = val;
  else if (val === '__outro__') inp.value = '';
}

function removeLogCarRow(rowId) {
  const el=document.getElementById(rowId); if(el) el.remove();
}

function saveLogEntry() {
  const resp = document.getElementById('log-resp').value.trim();
  const date = document.getElementById('log-datetime').value;
  if (!resp) { alert('Informe o responsável.'); return; }
  if (!date) { alert('Informe a data e hora.'); return; }
  const rows = document.querySelectorAll('#log-car-rows .log-car-row');
  const cars=[];
  rows.forEach(row=>{
    const rowId=row.id;
    const id     = document.getElementById(rowId+'-car')?.value;
    const status = document.getElementById(rowId+'-status')?.value;
    const dest   = document.getElementById(rowId+'-dest')?.value.trim();
    const driver = document.getElementById(rowId+'-driver')?.value.trim();
    // Modelo vem do CADASTRO do carro (fonte única) — campo removido do form 05/07/2026
    const model  = ((LDB.logCars().find(c => (c.carId || c.id) === id) || {}).model || '').trim();
    const obs    = document.getElementById(rowId+'-obs')?.value.trim();
    if (id) cars.push({id, status, dest, driver, model, obs});
  });
  if (!cars.length) { alert('Adicione ao menos um veículo.'); return; }
  const entry={
    id: logEditId||('log_'+Date.now()+'_'+Math.random().toString(36).slice(2,6)),
    resp, date:new Date(date).toISOString(), cars, synced:isOnline,
  };
  LDB.saveEntry(entry);
  closeModal('log-modal');
  refreshLogisticsViews();
}

// ─── DELETE REGISTRO ──────────────────────────────────
let pendingLogDeleteId=null, pendingLogDeleteType=null;

function openLogEntryDelete(id) {
  pendingLogDeleteId=id; pendingLogDeleteType='entry';
  document.getElementById('log-delete-msg').textContent='Este registro de logística será excluído permanentemente.';
  openModal('log-delete-modal');
}
function confirmLogDelete() {
  if (pendingLogDeleteType==='entry') { LDB.removeEntry(pendingLogDeleteId); refreshLogisticsViews(); }
  if (pendingLogDeleteType==='driver'){ LDB.removeDriver(pendingLogDeleteId); renderLogDrivers(); }
  if (pendingLogDeleteType==='car')   { LDB.removeLogCar(pendingLogDeleteId); renderLogCars(); }
  pendingLogDeleteId=null; pendingLogDeleteType=null;
  closeModal('log-delete-modal');
}

// ─── MOTORISTAS CRUD ──────────────────────────────────
let editingDriverId = null;

function openLogDriverModal(id) {
  editingDriverId = id||null;
  const d = id ? LDB.drivers().find(x=>x.id===id) : null;
  document.getElementById('log-driver-modal-title').textContent = id?'Editar Motorista':'Novo Motorista';
  document.getElementById('log-driver-edit-id').value = id||'';
  document.getElementById('ld-name').value   = d?.name  ||'';
  document.getElementById('ld-cpf').value    = d?.cpf   ||'';
  document.getElementById('ld-cnh').value    = d?.cnh   ||'';
  document.getElementById('ld-tel').value    = d?.tel   ||'';
  document.getElementById('ld-status').value = d?.status||'ativo';
  document.getElementById('ld-obs').value    = d?.obs   ||'';
  openModal('log-driver-modal');
}

function saveLogDriver() {
  const name=document.getElementById('ld-name').value.trim();
  if (!name) { alert('Informe o nome do motorista.'); return; }
  const d = {
    id:     editingDriverId||ldid(),
    name,
    cpf:    document.getElementById('ld-cpf').value.trim(),
    cnh:    document.getElementById('ld-cnh').value.trim(),
    tel:    document.getElementById('ld-tel').value.trim(),
    status: document.getElementById('ld-status').value,
    obs:    document.getElementById('ld-obs').value.trim(),
  };
  LDB.saveDriver(d);
  editingDriverId=null;
  closeModal('log-driver-modal');
  renderLogDrivers();
}

function renderLogDrivers() {
  const el=document.getElementById('log-drivers-list');
  if (!el) return;
  const drivers=LDB.drivers();
  if (!drivers.length) { el.innerHTML='<div class="empty-state"><div class="es-icon">👤</div>Nenhum motorista cadastrado.</div>'; return; }
  el.innerHTML=drivers.map(d=>`
    <div class="log-cadastro-card">
      <div class="lcc-avatar driver">${d.name.charAt(0)}</div>
      <div class="lcc-body">
        <div class="lcc-name">${d.name}</div>
        <div class="lcc-meta">${d.cnh?'CNH: '+d.cnh:''}${d.tel?' • '+d.tel:''}</div>
        ${d.obs?`<div class="lcc-obs">${d.obs}</div>`:''}
        <span class="lcc-status-badge ${d.status}">${{ativo:'✅ Ativo',ferias:'🏖 Férias',afastado:'⚠ Afastado',inativo:'❌ Inativo'}[d.status]||d.status}</span>
      </div>
      <div class="lcc-actions">
        <button class="fleet-btn edit" onclick="openLogDriverModal('${d.id}')">✎</button>
        <button class="fleet-btn remove" onclick="openLogDriverDelete('${d.id}','${escJS(d.name)}')">✕</button>
      </div>
    </div>`).join('');
}

function openLogDriverDelete(id, name) {
  pendingLogDeleteId=id; pendingLogDeleteType='driver';
  document.getElementById('log-delete-msg').textContent=`Remover motorista "${name}"? Os registros de logística existentes serão mantidos.`;
  openModal('log-delete-modal');
}

// ─── VEÍCULOS CRUD ────────────────────────────────────
let editingCarId=null;
let logCarExtraFields=[];

function openLogCarModal(id) {
  editingCarId=id||null;
  logCarExtraFields=[];
  const c = id ? LDB.logCars().find(x=>x.id===id) : null;
  document.getElementById('log-car-modal-title').textContent = id?'Editar Veículo':'Novo Veículo de Apoio';
  document.getElementById('lc-edit-id').value    = id||'';
  document.getElementById('lc-id').value         = c?.carId ||'';
  document.getElementById('lc-plate').value      = c?.plate ||'';
  document.getElementById('lc-model').value      = c?.model ||'';
  document.getElementById('lc-year').value       = c?.year  ||'';
  document.getElementById('lc-color').value      = c?.color ||'';
  document.getElementById('lc-status').value     = c?.status||'disponivel';
  document.getElementById('lc-obs').value        = c?.obs   ||'';
  logCarExtraFields = c?.extras ? JSON.parse(JSON.stringify(c.extras)) : [];
  renderLogCarExtras();
  openModal('log-car-modal');
}

function addLogCarExtraField() {
  logCarExtraFields.push({id:'ef_'+Date.now(), type:'text', label:''});
  renderLogCarExtras();
}

function renderLogCarExtras() {
  const el=document.getElementById('lc-extra-fields');
  if (!el) return;
  el.innerHTML=logCarExtraFields.map(f=>`
    <div class="lc-extra-row" id="ef-${f.id}">
      <select class="lc-field-type" onchange="updateExtraType('${f.id}',this.value)">
        <option value="text"    ${f.type==='text'   ?'selected':''}>Texto</option>
        <option value="number"  ${f.type==='number' ?'selected':''}>Número</option>
        <option value="date"    ${f.type==='date'   ?'selected':''}>Data</option>
        <option value="textarea"${f.type==='textarea'?'selected':''}>Parágrafo</option>
      </select>
      <input class="lc-field-label" type="text" value="${escAttr(f.label)}"
        placeholder="Nome do campo (ex.: Revisão, CRLV...)"
        oninput="updateExtraLabel('${f.id}',this.value)" />
      <button class="btn-rm-item" onclick="removeExtraField('${f.id}')">✕</button>
    </div>`).join('');
}

function updateExtraType(id,val)  { const f=logCarExtraFields.find(x=>x.id===id); if(f) f.type=val; }
function updateExtraLabel(id,val) { const f=logCarExtraFields.find(x=>x.id===id); if(f) f.label=val; }
function removeExtraField(id) {
  logCarExtraFields=logCarExtraFields.filter(f=>f.id!==id);
  renderLogCarExtras();
}

function saveLogCar() {
  const carId=document.getElementById('lc-id').value.trim().toUpperCase();
  if (!carId) { alert('Informe a identificação do veículo (ex.: CA-50).'); return; }
  const c={
    id:     editingCarId||ldid(),
    carId,
    plate:  document.getElementById('lc-plate').value.trim(),
    model:  document.getElementById('lc-model').value.trim(),
    year:   parseInt(document.getElementById('lc-year').value)||null,
    color:  document.getElementById('lc-color').value.trim(),
    status: document.getElementById('lc-status').value,
    obs:    document.getElementById('lc-obs').value.trim(),
    extras: logCarExtraFields.filter(f=>f.label.trim()),
  };
  LDB.saveLogCar(c);
  // (05/07/2026) Removido sync p/ frota do checklist: carro de APOIO da
  // logística não é item de checklist de máquinas.
  editingCarId=null;
  closeModal('log-car-modal');
  renderLogCars();
}

function renderLogCars() {
  const el=document.getElementById('log-cars-list');
  if (!el) return;
  const cars=LDB.logCars();
  if (!cars.length) { el.innerHTML='<div class="empty-state"><div class="es-icon">🚗</div>Nenhum veículo cadastrado.</div>'; return; }
  const statusLabel={disponivel:'✅ Disponível','em-campo':'🟠 Em Campo',manutencao:'🔧 Manutenção',inativo:'❌ Inativo'};
  el.innerHTML=cars.map(c=>`
    <div class="log-cadastro-card">
      <div class="lcc-avatar car">🚗</div>
      <div class="lcc-body">
        <div class="lcc-name">${c.carId}${c.model?' — '+c.model:''}</div>
        <div class="lcc-meta">${c.year||''}${c.color?' • '+c.color:''}${c.plate?' • '+c.plate:''}</div>
        ${c.extras?.length?`<div class="lcc-extra">${c.extras.map(f=>`${f.label}: <em>${f.type}</em>`).join(' • ')}</div>`:''}
        ${c.obs?`<div class="lcc-obs">${c.obs}</div>`:''}
        <span class="lcc-status-badge ${c.status}">${statusLabel[c.status]||c.status}</span>
      </div>
      <div class="lcc-actions">
        <button class="fleet-btn edit" onclick="openLogCarModal('${c.id}')">✎</button>
        <button class="fleet-btn remove" onclick="openLogCarDelete('${c.id}','${escJS(c.carId)}')">✕</button>
      </div>
    </div>`).join('');
}

function openLogCarDelete(id, carId) {
  pendingLogDeleteId=id; pendingLogDeleteType='car';
  document.getElementById('log-delete-msg').textContent=`Remover veículo "${carId}"? Os registros existentes serão mantidos.`;
  openModal('log-delete-modal');
}

// ─── DETAIL ───────────────────────────────────────────
function showLogEntryDetail(entryId) {
  const entry=LDB.entries().find(e=>e.id===entryId);
  if (!entry) return;
  const rows=(entry.cars||[]).map(c=>`${c.id} – ${c.driver||'?'} → ${c.dest||'?'} [${c.status}]${c.obs?' ('+c.obs+')':''}`).join('\n');
  alert(`📋 ${entry.resp}\n${formatDateTime(entry.date)}\n\n${rows}`);
}

// ─── SUPERIOR DASHBOARD ───────────────────────────────
function refreshLogisticsViews() {
  if (currentUser?.role==='manager')  renderLogisticsTab();
  if (currentUser?.role==='superior') renderSuperiorDashboard();
}

function renderLogisticsTab() {
  renderLogisticsKPIs('log-kpi-active','log-kpi-idle','log-kpi-drivers','log-kpi-total');
  renderCurrentFleet('log-current-fleet');
  populateLogFilters();
  renderLogFiltered();
}

function supTab(tab, btn) {
  document.querySelectorAll('#screen-superior .tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('#screen-superior .tab-panel').forEach(p=>p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('sup-tab-'+tab).classList.add('active');
  if (tab==='report')     renderSupReportPreview();
  if (tab==='checklists') renderSupChecklistCards();
}

function renderSupReportPreview() {
  const el=document.getElementById('sup-report-preview');
  if (el) el.innerHTML=buildReportHTML(true);
}

// ─── RELATÓRIO ────────────────────────────────────────
function printLogReport() {
  const el=document.getElementById('log-report-content');
  if (!el) return;
  el.innerHTML=buildReportHTML(false);
  showScreen('screen-log-report');
}

function closeLogReport() {
  if (currentUser?.role==='superior') showSuperior();
  else showManager();
}

function buildReportHTML() {
  const entries=LDB.entries();
  const status =LDB.currentStatus();
  const now    =new Date();
  const vals   =Object.values(status);
  const active =vals.filter(v=>v.status==='em-campo').length;
  const idle   =vals.filter(v=>v.status!=='em-campo').length;
  const drivers=vals.filter(v=>v.status==='em-campo'&&v.driver&&v.driver!=='OCIOSO / PARADO').length;

  const currentRows=Object.entries(status).sort((a,b)=>a[0].localeCompare(b[0])).map(([carId,info])=>{
    const st=info.status||'parado';
    const cls=st==='em-campo'?'rt-campo':st==='disponivel'?'rt-ok':'rt-parado';
    const lbl=st==='em-campo'?'Em Campo':st==='disponivel'?'Disponível':'Ocioso';
    return `<tr><td><strong>${carId}</strong></td><td>${info.model||'—'}</td><td class="${cls}">${lbl}</td><td>${info.dest||'—'}</td><td>${info.driver||'—'}</td><td>${info.resp||'—'}</td><td>${formatDate(info.date)}</td></tr>`;
  }).join('');

  const histRows=entries.slice(0,30).flatMap(entry=>(entry.cars||[]).map(car=>{
    const st=car.status||'parado';
    const cls=st==='em-campo'?'rt-campo':st==='disponivel'?'rt-ok':'rt-parado';
    const lbl=st==='em-campo'?'Em Campo':st==='disponivel'?'Disponível':'Ocioso';
    return `<tr><td>${formatDate(entry.date)}</td><td><strong>${car.id}</strong>${car.model?' <small>('+car.model+')</small>':''}</td><td class="${cls}">${lbl}</td><td>${car.dest||'—'}</td><td>${car.driver||'—'}</td><td>${entry.resp||'—'}</td>${car.obs?`<td>${car.obs}</td>`:'<td>—</td>'}</tr>`;
  })).join('');

  return `<div class="report-page">
    <div class="report-header">
      <div class="report-logo-block">
        <img src="icons/logo.jpg" alt="Garra" style="height:60px;width:auto;object-fit:contain" />
      </div>
      <div class="report-title-block">
        <h2>Relatório de Logística</h2>
        <p>Controle de Carros de Apoio</p>
        <p style="margin-top:4px;font-size:11px;color:var(--gray)">Emitido em ${now.toLocaleString('pt-BR')}</p>
      </div>
    </div>
    <div class="report-summary-grid">
      <div class="rskg accent"><div class="rskg-val">${active}</div><div class="rskg-label">Em Campo</div></div>
      <div class="rskg ok"><div class="rskg-val">${idle}</div><div class="rskg-label">Disponíveis/Ociosos</div></div>
      <div class="rskg"><div class="rskg-val">${drivers}</div><div class="rskg-label">Motoristas Alocados</div></div>
      <div class="rskg"><div class="rskg-val">${entries.length}</div><div class="rskg-label">Total de Registros</div></div>
    </div>
    <div class="report-section-title">🚗 Situação Atual da Frota</div>
    <div class="rt-scroll"><table class="report-table">
      <thead><tr><th>Veículo</th><th>Modelo</th><th>Status</th><th>Destino</th><th>Motorista</th><th>Responsável</th><th>Última Atualização</th></tr></thead>
      <tbody>${currentRows}</tbody>
    </table></div>
    <div class="report-section-title">📋 Histórico (últimos 30)</div>
    <div class="rt-scroll"><table class="report-table">
      <thead><tr><th>Data</th><th>Veículo</th><th>Status</th><th>Destino</th><th>Motorista</th><th>Responsável</th><th>Obs.</th></tr></thead>
      <tbody>${histRows}</tbody>
    </table></div>
    <div class="report-sign-block">
      <div class="sign-line">Responsável – Logística</div>
      <div class="sign-line">Gestora de Frota</div>
    </div>
    <div class="report-footer">
      <span>Garra Terraplenagem e Caçambas – Pará de Minas, MG</span>
      <span>garraterraplenagem.com.br</span>
    </div>
  </div>`;
}

// ─── HELPERS ──────────────────────────────────────────
function escJS(s)   { return (s||'').replace(/'/g,"\\'").replace(/"/g,'\\"'); }
function escAttr(s) { return (s||'').replace(/"/g,'&quot;').replace(/</g,'&lt;'); }
