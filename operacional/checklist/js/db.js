/* ═══════════════════════════════════════════════════════════
   db.js — Camada de dados Garra Check List v4
   
   ESTRATÉGIA:
   - Toda operação vai PRIMEIRO para a API (banco PostgreSQL)
   - Só após confirmação da API atualiza o localStorage
   - Se API falhar (offline), salva local e enqueue para sync
   - localStorage é apenas CACHE — banco é a fonte da verdade
═══════════════════════════════════════════════════════════ */

const API_URL = 'https://garra-sistemas.onrender.com';

// ─── TOKEN JWT ─────────────────────────────────────────────
const TokenStore = {
  get()      { return localStorage.getItem('garra_token') || null; },
  set(token) { localStorage.setItem('garra_token', token); },
  del()      { localStorage.removeItem('garra_token'); },
};

// ─── RENOVAÇÃO SILENCIOSA DO TOKEN ────────────────────────
// Chamado ao iniciar o app — se tem token válido, renova por mais 30 dias
// O usuário nunca precisa fazer login enquanto usar o app regularmente
async function renovarTokenSilencioso() {
  const token = TokenStore.get();
  if (!token) return; // sem token → não faz nada, usuário vai para login
  try {
    const res = await fetch(API_URL + '/auth/renovar', {
      method: 'POST',
      signal: AbortSignal.timeout(5000),
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token,
      },
    });
    if (res.ok) {
      const data = await res.json();
      if (data.token) {
        TokenStore.set(data.token);
        console.log('[Token] Renovado silenciosamente +' + (JWT_EXPIRY_HOURS||720/24) + ' dias');
      }
    } else if (res.status === 401) {
      // Token expirado — limpa, usuário faz login normal
      TokenStore.del();
      localStorage.removeItem('garra_current_user');
      console.log('[Token] Expirado — login necessário');
    }
  } catch(e) {
    // Offline ou timeout — não faz nada, token continua válido até expirar
    console.log('[Token] Renovação adiada (offline ou timeout)');
  }
}

// ─── FETCH COM TIMEOUT + JWT ───────────────────────────────
async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timeout    = setTimeout(() => controller.abort(), 8000); // 8s timeout
  // Injeta token em TODAS as chamadas automaticamente
  const token = TokenStore.get();
  const authHeader = token ? { 'Authorization': 'Bearer ' + token } : {};
  try {
    const res = await fetch(API_URL + path, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...authHeader,
        ...(options.headers || {}),
      },
    });
    clearTimeout(timeout);
    if (!res.ok) {
      // Token expirado — limpar e forçar novo login
      if (res.status === 401) {
        TokenStore.del();
        localStorage.removeItem('garra_current_user');
      }
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

// ─── ARMAZENAMENTO SEGURO (evita estourar a cota do navegador) ─────
// Usado por qualquer fila/cache do checklist que grave listas no localStorage.
// Nunca deixa o app travar: se a cota estourar, poda o mais antigo,
// depois remove fotos, e só como último recurso reduz ao mínimo.
const SafeStorage = {
  // Remove qualquer foto em base64 de dentro de um objeto/array, não importa
  // o quão aninhada esteja — funciona tanto para submissions quanto para
  // itens da fila offline (que guardam o envio serializado como string em options.body).
  _stripPhotosDeep(value) {
    if (typeof value === 'string') {
      if (value.startsWith('data:image')) return null;
      if (value.length > 200 && (value.trim()[0] === '{' || value.trim()[0] === '[')) {
        try { return JSON.stringify(this._stripPhotosDeep(JSON.parse(value))); }
        catch { return value; }
      }
      return value;
    }
    if (Array.isArray(value)) return value.map(v => this._stripPhotosDeep(v));
    if (value && typeof value === 'object') {
      const out = {};
      for (const k in value) out[k] = this._stripPhotosDeep(value[k]);
      return out;
    }
    return value;
  },

  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
      return true;
    } catch (e) {
      const quotaEstourada = e && (e.name === 'QuotaExceededError' || /quota/i.test(e.message || ''));
      if (!quotaEstourada) throw e;
      console.warn('[SafeStorage] Cota excedida ao salvar "' + key + '" — recuperando...');

      let recuperado = value;
      if (Array.isArray(value)) {
        const pendentes = value.filter(x => x && x.synced === false);
        const resto     = value.filter(x => !x || x.synced !== false).slice(0, 100);
        recuperado = [...pendentes, ...resto];
        try {
          localStorage.setItem(key, JSON.stringify(recuperado));
          console.warn('[SafeStorage] "' + key + '" podado para', recuperado.length, 'itens.');
          return true;
        } catch (e2) { /* segue para remoção de fotos */ }
      }

      const semFotos = this._stripPhotosDeep(recuperado);
      try {
        localStorage.setItem(key, JSON.stringify(semFotos));
        console.warn('[SafeStorage] Fotos removidas de "' + key + '" para liberar espaço.');
        return true;
      } catch (e3) { /* segue para último recurso */ }

      if (Array.isArray(semFotos)) {
        const minimo = semFotos.slice(0, 20);
        try {
          localStorage.setItem(key, JSON.stringify(minimo));
          console.error('[SafeStorage] "' + key + '" reduzido ao mínimo (20 itens) — espaço crítico.');
          return true;
        } catch (e4) {
          console.error('[SafeStorage] Não foi possível salvar "' + key + '":', e4);
          throw e4;
        }
      }
      throw e;
    }
  },
};

// ─── FILA OFFLINE (UNIFICADA) ──────────────────────────────
// Fila ÚNICA de escrita offline do checklist/logística/usuários.
// Item: { tipo: 'envio'|'logistica'|'usuario', ref_id?, path, options, ts }
//   tipo/ref_id permitem que o app.js atualize o estado local (ex: marcar
//   submission synced=true) quando o item é sincronizado com sucesso.
// Ao fim de um flush com sucessos, dispara o evento 'garra:fila-sincronizada'
// com detail.enviados = itens sincronizados.
const OfflineQueue = {
  flushing: false,
  get()     { try { return JSON.parse(localStorage.getItem('garra_offline_q') || '[]'); } catch { return []; } },
  add(item) { const q = this.get(); q.push({...item, ts: Date.now()}); SafeStorage.set('garra_offline_q', q); },
  clear()   { SafeStorage.set('garra_offline_q', []); },
  async flush() {
    if (!navigator.onLine) return;
    if (this.flushing) return;           // evita flush concorrente (online + timer)
    this.flushing = true;
    try {
      const queue = this.get();
      if (!queue.length) return;
      const failed = [], enviados = [];
      for (const item of queue) {
        try { await apiFetch(item.path, item.options); enviados.push(item); }
        catch { failed.push(item); }
      }
      SafeStorage.set('garra_offline_q', failed);
      if (enviados.length) {
        window.dispatchEvent(new CustomEvent('garra:fila-sincronizada', { detail: { enviados } }));
        console.log('✅ Fila offline:', enviados.length, 'sincronizado(s),', failed.length, 'restante(s)');
      }
    } finally {
      this.flushing = false;
    }
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
    // Guarda o token JWT para todas as chamadas seguintes
    if (user.token) {
      TokenStore.set(user.token);
    }
    // Normaliza campos
    return {
      login:       user.login,
      name:        user.nome || user.name || login,
      role:        user.role || user.perfil_checklist || ((user.perfil==='admin'||user.perfil==='gestor')?'manager':'driver'),
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
          tipo: 'envio',
          ref_id: envio.envio_id,
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

  // ── CHECK LISTS PERSONALIZADOS ─────────────────────────
  async getModelos() {
    try {
      const data = await apiFetch('/checklist/modelos');
      return (data || []).filter(m => !m.is_default);
    } catch { return []; }
  },

  async salvarModelo(cl) {
    return await apiFetch('/checklist/modelos', {
      method: 'POST',
      body: JSON.stringify({
        cl_id:       cl.id,
        label:       cl.label,
        icon:        cl.icon || '📋',
        descricao:   cl.desc || '',
        vehicle_cat: cl.vehicleCat || 'maquinas',
        is_default:  false,
        score_full:  cl.scoreRules?.full   || 100,
        score_nc:    cl.scoreRules?.nc     || 60,
        score_obs:   cl.scoreRules?.obs    || 20,
        score_ontime:cl.scoreRules?.ontime || 10,
        questions:   cl.questions || [],
        steps:       cl.steps     || [],
      })
    });
  },

  async removerModelo(cl_id) {
    return await apiFetch('/checklist/modelos/' + cl_id, { method: 'DELETE' });
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
          tipo: 'logistica',
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
