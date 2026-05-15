/* ═══════════════════════════════════════════════════════════
   db.js — Camada de dados Garra Check List v4
   
   ESTRATÉGIA:
   - Toda operação vai PRIMEIRO para a API (banco PostgreSQL)
   - Só após confirmação da API atualiza o localStorage
   - Se API falhar (offline), salva local e enqueue para sync
   - localStorage é apenas CACHE — banco é a fonte da verdade
═══════════════════════════════════════════════════════════ */

const API_URL = 'https://garra-sistemas.onrender.com';

// ─── FETCH COM TIMEOUT ─────────────────────────────────────
async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timeout    = setTimeout(() => controller.abort(), 8000); // 8s timeout
  try {
    const res = await fetch(API_URL + path, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    });
    clearTimeout(timeout);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Erro ${res.status}`);
    }
    return await res.json();
  } catch(e) {
    clearTimeout(timeout);
    if (e.name === 'AbortError') throw new Error('TIMEOUT');
    if (!navigator.onLine)      throw new Error('OFFLINE');
    throw e;
  }
}

// ─── FILA OFFLINE ──────────────────────────────────────────
const OfflineQueue = {
  get()     { try { return JSON.parse(localStorage.getItem('garra_offline_q') || '[]'); } catch { return []; } },
  add(item) { const q = this.get(); q.push({...item, ts: Date.now()}); localStorage.setItem('garra_offline_q', JSON.stringify(q)); },
  clear()   { localStorage.setItem('garra_offline_q', '[]'); },
  async flush() {
    if (!navigator.onLine) return;
    const queue = this.get();
    if (!queue.length) return;
    const failed = [];
    for (const item of queue) {
      try { await apiFetch(item.path, item.options); }
      catch { failed.push(item); }
    }
    localStorage.setItem('garra_offline_q', JSON.stringify(failed));
    if (!failed.length) console.log('✅ Fila offline sincronizada');
  }
};
window.addEventListener('online', () => OfflineQueue.flush());

// ─── CACHE LOCAL ───────────────────────────────────────────
const Cache = {
  set(k, v, ttlMs = 30000) { localStorage.setItem('_c_'+k, JSON.stringify({v, exp: Date.now()+ttlMs})); },
  get(k) {
    try {
      const c = JSON.parse(localStorage.getItem('_c_'+k));
      if (c && Date.now() < c.exp) return c.v;
      localStorage.removeItem('_c_'+k);
    } catch {}
    return null;
  },
  del(k)    { localStorage.removeItem('_c_'+k); },
  delAll()  { Object.keys(localStorage).filter(k=>k.startsWith('_c_')).forEach(k=>localStorage.removeItem(k)); },
};

// ─── GARRA DB — API PRINCIPAL ──────────────────────────────
const GarraDB = {

  // ── AUTH ────────────────────────────────────────────────
  async login(login, senha) {
    const user = await apiFetch('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ login, senha }),
    });
    // Normaliza campos
    return {
      login:       user.login,
      name:        user.nome || user.name || login,
      role:        user.perfil || user.role || 'driver',
      pts:         user.pts || 0,
      submissions: user.total_envios || user.submissions || 0,
    };
  },

  // ── USUÁRIOS ────────────────────────────────────────────
  async getUsuarios() {
    const cached = Cache.get('usuarios');
    if (cached) return cached;
    const data = await apiFetch('/usuarios');
    Cache.set('usuarios', data, 15000); // cache 15s
    return data;
  },

  async criarUsuario(u) {
    const res = await apiFetch('/usuarios', {
      method: 'POST',
      body: JSON.stringify(u),
    });
    Cache.del('usuarios');
    return res;
  },

  async editarUsuario(login, dados) {
    const res = await apiFetch(`/usuarios/${login}/editar`, {
      method: 'POST',
      body: JSON.stringify(dados),
    });
    Cache.del('usuarios');
    return res;
  },

  async removerUsuario(login) {
    const res = await apiFetch(`/usuarios/${login}`, { method: 'DELETE' });
    Cache.del('usuarios');
    return res;
  },

  async atualizarPts(login, pts) {
    try {
      await apiFetch(`/usuarios/${login}/pts?pts=${pts}`, { method: 'PATCH' });
    } catch(e) {
      console.warn('Falha ao atualizar pts:', e.message);
    }
  },

  // ── CHECKLIST ENVIOS ────────────────────────────────────
  async getEnvios(filtros = {}) {
    const params = new URLSearchParams();
    if (filtros.usuario) params.append('usuario', filtros.usuario);
    if (filtros.cl_id)   params.append('cl_id',   filtros.cl_id);
    if (filtros.limit)   params.append('limit',   filtros.limit);
    return await apiFetch('/checklist/envios?' + params.toString());
  },

  async salvarEnvio(envio) {
    try {
      return await apiFetch('/checklist/envios', {
        method: 'POST',
        body: JSON.stringify(envio),
      });
    } catch(e) {
      if (e.message === 'OFFLINE' || e.message === 'TIMEOUT') {
        OfflineQueue.add({
          path: '/checklist/envios',
          options: { method: 'POST', body: JSON.stringify(envio) }
        });
        return { ok: true, offline: true };
      }
      throw e;
    }
  },

  // ── FROTA ───────────────────────────────────────────────
  async getFrota() {
    const cached = Cache.get('frota');
    if (cached) return cached;
    const data = await apiFetch('/frota');
    Cache.set('frota', data, 60000);
    return data;
  },

  async salvarFrotaItem(item) {
    const res = await apiFetch('/frota', {
      method: 'POST',
      body: JSON.stringify(item),
    });
    Cache.del('frota');
    return res;
  },

  async removerFrotaItem(categoria, identificacao) {
    const res = await apiFetch(`/frota/${categoria}/${identificacao}`, { method: 'DELETE' });
    Cache.del('frota');
    return res;
  },

  // ── LOGÍSTICA: MOTORISTAS ───────────────────────────────
  async getMotoristas() {
    const cached = Cache.get('log_motoristas');
    if (cached) return cached;
    const data = await apiFetch('/logistica/motoristas');
    Cache.set('log_motoristas', data, 30000);
    return data;
  },

  async salvarMotorista(m) {
    const res = await apiFetch('/logistica/motoristas', {
      method: 'POST',
      body: JSON.stringify(m),
    });
    Cache.del('log_motoristas');
    return res;
  },

  async removerMotorista(motor_id) {
    const res = await apiFetch(`/logistica/motoristas/${motor_id}`, { method: 'DELETE' });
    Cache.del('log_motoristas');
    return res;
  },

  // ── LOGÍSTICA: VEÍCULOS ─────────────────────────────────
  async getVeiculos() {
    const cached = Cache.get('log_veiculos');
    if (cached) return cached;
    const data = await apiFetch('/logistica/veiculos');
    Cache.set('log_veiculos', data, 30000);
    return data;
  },

  async salvarVeiculo(v) {
    const res = await apiFetch('/logistica/veiculos', {
      method: 'POST',
      body: JSON.stringify(v),
    });
    Cache.del('log_veiculos');
    return res;
  },

  async removerVeiculo(veiculo_id) {
    const res = await apiFetch(`/logistica/veiculos/${veiculo_id}`, { method: 'DELETE' });
    Cache.del('log_veiculos');
    return res;
  },

  // ── LOGÍSTICA: REGISTROS ────────────────────────────────
  async getRegistros(limit = 50) {
    return await apiFetch(`/logistica/registros?limit=${limit}`);
  },

  async salvarRegistro(r) {
    try {
      return await apiFetch('/logistica/registros', {
        method: 'POST',
        body: JSON.stringify(r),
      });
    } catch(e) {
      if (e.message === 'OFFLINE' || e.message === 'TIMEOUT') {
        OfflineQueue.add({
          path: '/logistica/registros',
          options: { method: 'POST', body: JSON.stringify(r) }
        });
        return { ok: true, offline: true };
      }
      throw e;
    }
  },

  async removerRegistro(registro_id) {
    return await apiFetch(`/logistica/registros/${registro_id}`, { method: 'DELETE' });
  },
};
