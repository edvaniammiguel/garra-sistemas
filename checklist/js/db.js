/* ═══════════════════════════════════════════════════════════
   db.js — Camada de dados Garra Check List
   Conecta ao PostgreSQL via API (Render Web Service)
   Mantém localStorage como fallback offline
   ═══════════════════════════════════════════════════════════

   CONFIGURAÇÃO: troque API_URL pela URL do seu Web Service no Render
   Exemplo: https://garra-checklist-api.onrender.com
═══════════════════════════════════════════════════════════ */

const API_URL = 'https://garra-sistemas.onrender.com'; // ← TROCAR APÓS DEPLOY

// ─── HELPER: fetch com fallback offline ───────────────────
async function apiFetch(path, options = {}) {
  try {
    const res = await fetch(API_URL + path, {
      ...options,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Erro ${res.status}`);
    }
    return await res.json();
  } catch (e) {
    if (!navigator.onLine) throw new Error('OFFLINE');
    throw e;
  }
}

// ─── FILA OFFLINE ────────────────────────────────────────
const OfflineQueue = {
  get()       { try { return JSON.parse(localStorage.getItem('garra_offline_queue') || '[]'); } catch { return []; } },
  add(item)   { const q = this.get(); q.push(item); localStorage.setItem('garra_offline_queue', JSON.stringify(q)); },
  clear()     { localStorage.setItem('garra_offline_queue', '[]'); },
  async flush() {
    if (!navigator.onLine) return;
    const queue = this.get();
    if (!queue.length) return;
    const failed = [];
    for (const item of queue) {
      try {
        await apiFetch(item.path, item.options);
      } catch {
        failed.push(item);
      }
    }
    localStorage.setItem('garra_offline_queue', JSON.stringify(failed));
    if (!failed.length) console.log('✅ Fila offline sincronizada');
  }
};

// Tenta sincronizar quando voltar online
window.addEventListener('online', () => OfflineQueue.flush());

// ─── LOCAL CACHE ─────────────────────────────────────────
const Cache = {
  set: (k, v) => localStorage.setItem('cache_' + k, JSON.stringify({ data: v, ts: Date.now() })),
  get: (k, maxAgeMs = 60000) => {
    try {
      const c = JSON.parse(localStorage.getItem('cache_' + k));
      if (c && Date.now() - c.ts < maxAgeMs) return c.data;
    } catch {}
    return null;
  },
  del: (k) => localStorage.removeItem('cache_' + k),
  clearAll: () => Object.keys(localStorage).filter(k => k.startsWith('cache_')).forEach(k => localStorage.removeItem(k)),
};

// ═══════════════════════════════════════════════════════════
// GarraDB — API principal usada pelo app.js e logistics.js
// ═══════════════════════════════════════════════════════════
const GarraDB = {

  // ─── AUTH ──────────────────────────────────────────────
  async login(login, senha) {
    return await apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ login, senha }),
    });
  },

  // ─── USUÁRIOS ──────────────────────────────────────────
  async getUsuarios() {
    const cached = Cache.get('usuarios', 30000);
    if (cached) return cached;
    const data = await apiFetch('/usuarios');
    Cache.set('usuarios', data);
    return data;
  },
  async criarUsuario(u) {
    const res = await apiFetch('/usuarios', { method: 'POST', body: JSON.stringify(u) });
    Cache.del('usuarios');
    return res;
  },
  async removerUsuario(login) {
    const res = await apiFetch(`/usuarios/${login}`, { method: 'DELETE' });
    Cache.del('usuarios');
    return res;
  },

  // ─── CHECKLIST MODELOS ─────────────────────────────────
  async getModelos() {
    const cached = Cache.get('cl_modelos', 60000);
    if (cached) return cached;
    const data = await apiFetch('/checklist/modelos');
    Cache.set('cl_modelos', data);
    return data;
  },
  async salvarModelo(modelo) {
    const res = await apiFetch('/checklist/modelos', { method: 'POST', body: JSON.stringify(modelo) });
    Cache.del('cl_modelos');
    return res;
  },
  async removerModelo(cl_id) {
    const res = await apiFetch(`/checklist/modelos/${cl_id}`, { method: 'DELETE' });
    Cache.del('cl_modelos');
    return res;
  },

  // ─── CHECKLIST ENVIOS ──────────────────────────────────
  async getEnvios(filtros = {}) {
    const params = new URLSearchParams();
    if (filtros.usuario) params.append('usuario', filtros.usuario);
    if (filtros.cl_id)   params.append('cl_id', filtros.cl_id);
    if (filtros.limit)   params.append('limit', filtros.limit);
    return await apiFetch('/checklist/envios?' + params.toString());
  },
  async salvarEnvio(envio) {
    try {
      return await apiFetch('/checklist/envios', { method: 'POST', body: JSON.stringify(envio) });
    } catch (e) {
      if (e.message === 'OFFLINE') {
        // Salva na fila offline
        OfflineQueue.add({ path: '/checklist/envios', options: { method: 'POST', body: JSON.stringify(envio) } });
        // Salva localmente para exibição imediata
        const local = JSON.parse(localStorage.getItem('garra_envios_local') || '[]');
        local.unshift({ ...envio, synced: false });
        localStorage.setItem('garra_envios_local', JSON.stringify(local));
        return { ok: true, offline: true };
      }
      throw e;
    }
  },

  // ─── FROTA ─────────────────────────────────────────────
  async getFrota() {
    const cached = Cache.get('frota', 120000);
    if (cached) return cached;
    const data = await apiFetch('/frota');
    Cache.set('frota', data);
    return data;
  },
  async salvarFrotaItem(item) {
    const res = await apiFetch('/frota', { method: 'POST', body: JSON.stringify(item) });
    Cache.del('frota');
    return res;
  },
  async removerFrotaItem(categoria, identificacao) {
    const res = await apiFetch(`/frota/${categoria}/${identificacao}`, { method: 'DELETE' });
    Cache.del('frota');
    return res;
  },

  // ─── LOGÍSTICA: MOTORISTAS ─────────────────────────────
  async getMotoristas() {
    const cached = Cache.get('log_motoristas', 60000);
    if (cached) return cached;
    const data = await apiFetch('/logistica/motoristas');
    Cache.set('log_motoristas', data);
    return data;
  },
  async salvarMotorista(m) {
    const res = await apiFetch('/logistica/motoristas', { method: 'POST', body: JSON.stringify(m) });
    Cache.del('log_motoristas');
    return res;
  },
  async removerMotorista(motor_id) {
    const res = await apiFetch(`/logistica/motoristas/${motor_id}`, { method: 'DELETE' });
    Cache.del('log_motoristas');
    return res;
  },

  // ─── LOGÍSTICA: VEÍCULOS ───────────────────────────────
  async getVeiculos() {
    const cached = Cache.get('log_veiculos', 60000);
    if (cached) return cached;
    const data = await apiFetch('/logistica/veiculos');
    Cache.set('log_veiculos', data);
    return data;
  },
  async salvarVeiculo(v) {
    const res = await apiFetch('/logistica/veiculos', { method: 'POST', body: JSON.stringify(v) });
    Cache.del('log_veiculos');
    return res;
  },
  async removerVeiculo(veiculo_id) {
    const res = await apiFetch(`/logistica/veiculos/${veiculo_id}`, { method: 'DELETE' });
    Cache.del('log_veiculos');
    return res;
  },

  // ─── LOGÍSTICA: REGISTROS ──────────────────────────────
  async getRegistros(limit = 50) {
    return await apiFetch(`/logistica/registros?limit=${limit}`);
  },
  async salvarRegistro(r) {
    try {
      return await apiFetch('/logistica/registros', { method: 'POST', body: JSON.stringify(r) });
    } catch (e) {
      if (e.message === 'OFFLINE') {
        OfflineQueue.add({ path: '/logistica/registros', options: { method: 'POST', body: JSON.stringify(r) } });
        return { ok: true, offline: true };
      }
      throw e;
    }
  },
  async removerRegistro(registro_id) {
    return await apiFetch(`/logistica/registros/${registro_id}`, { method: 'DELETE' });
  },
};
