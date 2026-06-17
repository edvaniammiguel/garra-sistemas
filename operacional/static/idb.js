/* ═══════════════════════════════════════════════════════════════
   idb.js — Garra Sistemas — Camada de persistência offline unificada
   Usado por: Operacional, Checklist, Jardinagem

   PRINCÍPIO:
   - Toda leitura: tenta rede → se sucesso, grava cópia local → retorna
                   se falha, lê do IndexedDB local → retorna com flag _offline
   - Toda escrita: tenta rede → se sucesso, grava local → retorna
                   se falha, grava como PENDENTE local (UI otimista) → entra na fila
   - Fila de pendentes é resolvida sozinha quando a rede volta (evento 'online'
     + tentativa periódica), nunca exige ação manual do usuário.
   - Cada módulo declara seus próprios "stores" (tabelas) e endpoints
     sincronizáveis; este arquivo só fornece os mecanismos genéricos.
═══════════════════════════════════════════════════════════════ */

const GarraDB = (function () {
  const DB_NAME    = 'garra_offline_db';
  const DB_VERSION = 1;

  // Stores fixos — um por tipo de dado, compartilhados entre módulos
  const STORES = {
    // Cache de leitura (dados vindos do servidor, para uso offline)
    cache_os:            'cache_os',            // operacional: OS do operador
    cache_partes:        'cache_partes',        // operacional: histórico de partes
    cache_equipamentos:  'cache_equipamentos',  // operacional: lista de equipamentos
    cache_operadores:    'cache_operadores',    // operacional: lista de operadores
    cache_checklist_mod: 'cache_checklist_mod', // checklist: modelos
    cache_checklist_env: 'cache_checklist_env', // checklist: últimos envios
    cache_frota:         'cache_frota',         // checklist: frota
    cache_usuarios:      'cache_usuarios',      // checklist/admin: usuários
    cache_jard_meses:    'cache_jard_meses',    // jardinagem: meses/semanas
    cache_jard_pares:    'cache_jard_pares',    // jardinagem: pares de fotos
    cache_jard_km:       'cache_jard_km',       // jardinagem: relatórios KM
    cache_generic:       'cache_generic',       // fallback genérico por chave

    // Fila de escrita pendente (ainda não confirmada pelo servidor)
    fila_pendentes:      'fila_pendentes',
    // Fotos pendentes de upload (blob separado por ser pesado)
    fotos_pendentes:     'fotos_pendentes',
  };

  let _dbPromise = null;

  function open() {
    if (_dbPromise) return _dbPromise;
    _dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);

      req.onupgradeneeded = (ev) => {
        const db = ev.target.result;
        Object.values(STORES).forEach((storeName) => {
          if (!db.objectStoreNames.contains(storeName)) {
            const store = db.createObjectStore(storeName, { keyPath: 'id' });
            store.createIndex('updated_at', 'updated_at', { unique: false });
          }
        });
      };

      req.onsuccess = (ev) => resolve(ev.target.result);
      req.onerror   = (ev) => reject(ev.target.error);
      req.onblocked = ()   => reject(new Error('IndexedDB bloqueado — outra aba pode estar em versão antiga'));
    });
    return _dbPromise;
  }

  function tx(storeName, mode = 'readonly') {
    return open().then((db) => db.transaction(storeName, mode).objectStore(storeName));
  }

  // ── CRUD genérico por store ──────────────────────────────────
  async function getAll(storeName) {
    const store = await tx(storeName);
    return new Promise((resolve, reject) => {
      const req = store.getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror   = () => reject(req.error);
    });
  }

  async function get(storeName, id) {
    const store = await tx(storeName);
    return new Promise((resolve, reject) => {
      const req = store.get(id);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror   = () => reject(req.error);
    });
  }

  async function put(storeName, item) {
    if (!item.id) throw new Error('idb.put: item precisa de campo "id"');
    item.updated_at = item.updated_at || new Date().toISOString();
    const store = await tx(storeName, 'readwrite');
    return new Promise((resolve, reject) => {
      const req = store.put(item);
      req.onsuccess = () => resolve(item);
      req.onerror   = () => reject(req.error);
    });
  }

  async function putMany(storeName, items) {
    if (!items || !items.length) return [];
    const store = await tx(storeName, 'readwrite');
    return new Promise((resolve, reject) => {
      let count = 0;
      items.forEach((item) => {
        item.updated_at = item.updated_at || new Date().toISOString();
        const req = store.put(item);
        req.onsuccess = () => { count++; if (count === items.length) resolve(items); };
        req.onerror   = () => reject(req.error);
      });
    });
  }

  async function del(storeName, id) {
    const store = await tx(storeName, 'readwrite');
    return new Promise((resolve, reject) => {
      const req = store.delete(id);
      req.onsuccess = () => resolve(true);
      req.onerror   = () => reject(req.error);
    });
  }

  async function clearStore(storeName) {
    const store = await tx(storeName, 'readwrite');
    return new Promise((resolve, reject) => {
      const req = store.clear();
      req.onsuccess = () => resolve(true);
      req.onerror   = () => reject(req.error);
    });
  }

  // ── Cache genérico por chave única (substitui localStorage de objetos) ──
  async function cacheSet(key, value) {
    return put(STORES.cache_generic, { id: key, value });
  }
  async function cacheGet(key) {
    const row = await get(STORES.cache_generic, key);
    return row ? row.value : null;
  }

  // ── FETCH inteligente: rede primeiro, IndexedDB como fallback ──
  // store: nome do store de cache (ex: STORES.cache_os)
  // listKey: se a resposta é uma lista, cada item precisa de "id" único
  async function fetchWithFallback(url, options, storeName, opts = {}) {
    const { isList = true, idField = 'id' } = opts;
    try {
      const res = await fetch(url, options);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();

      // Gravar cópia local para uso offline futuro
      try {
        if (storeName) {
          if (isList && Array.isArray(data)) {
            const withIds = data.map((d) => ({ ...d, id: d[idField] }));
            await clearStore(storeName);
            await putMany(storeName, withIds);
          } else if (!isList && data && data[idField] !== undefined) {
            await put(storeName, { ...data, id: data[idField] });
          } else if (!isList) {
            await cacheSet(url, data);
          }
        }
      } catch (e) {
        console.warn('[GarraDB] falha ao gravar cache local:', e.message);
      }

      return { data, _offline: false };
    } catch (networkError) {
      // Sem rede ou erro — tentar IndexedDB
      try {
        if (storeName && isList) {
          const cached = await getAll(storeName);
          return { data: cached, _offline: true, _error: networkError.message };
        }
        if (storeName && !isList) {
          const cached = await cacheGet(url);
          return { data: cached, _offline: true, _error: networkError.message };
        }
      } catch (dbError) {
        console.error('[GarraDB] fallback IndexedDB também falhou:', dbError);
      }
      return { data: isList ? [] : null, _offline: true, _error: networkError.message };
    }
  }

  // ── ESCRITA inteligente: rede primeiro, fila pendente como fallback ──
  // localItem: objeto já com id local provisório, usado para UI otimista
  async function postWithQueue(url, body, storeName, localItem, opts = {}) {
    const { method = 'POST', headers = {} } = opts;
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...headers },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const data = await res.json();

      // Sucesso — gravar resultado real no cache local (substitui o otimista)
      if (storeName && data && data.id) {
        await put(storeName, { ...data, id: data.id, _pending: false });
        if (localItem && localItem.id !== data.id) {
          await del(storeName, localItem.id).catch(() => {});
        }
      }
      return { data, _offline: false, _queued: false };
    } catch (networkError) {
      // Falhou — gravar localmente como pendente + UI otimista
      const pendingId = (localItem && localItem.id) || ('local_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8));
      const optimistic = { ...(localItem || body), id: pendingId, _pending: true, updated_at: new Date().toISOString() };

      if (storeName) {
        await put(storeName, optimistic).catch((e) => console.error('[GarraDB] erro ao salvar otimista:', e));
      }

      await addToQueue({
        id: pendingId,
        url, method, body,
        headers,
        storeName,
        tentativas: 0,
        criado_em: new Date().toISOString(),
      });

      registerBackgroundSync();

      return { data: optimistic, _offline: true, _queued: true, _error: networkError.message };
    }
  }

  // ── FILA DE PENDENTES ────────────────────────────────────────
  async function addToQueue(item) {
    return put(STORES.fila_pendentes, item);
  }

  async function getQueue() {
    return getAll(STORES.fila_pendentes);
  }

  async function removeFromQueue(id) {
    return del(STORES.fila_pendentes, id);
  }

  async function countPending() {
    const q = await getQueue();
    return q.length;
  }

  // Tenta reenviar tudo que está na fila. Chamado ao voltar online
  // e periodicamente como rede de segurança.
  let _syncing = false;
  async function syncPendentes(onProgress) {
    if (_syncing) return { synced: 0, failed: 0 };
    _syncing = true;
    let synced = 0, failed = 0;

    try {
      const queue = await getQueue();
      for (const item of queue) {
        try {
          const res = await fetch(item.url, {
            method: item.method,
            headers: { 'Content-Type': 'application/json', ...(item.headers || {}) },
            body: JSON.stringify(item.body),
          });
          if (!res.ok) throw new Error('HTTP ' + res.status);
          const data = await res.json();

          // Substituir registro otimista pelo real
          if (item.storeName && data && data.id) {
            await put(item.storeName, { ...data, id: data.id, _pending: false });
            if (item.id !== data.id) await del(item.storeName, item.id).catch(() => {});
          }
          await removeFromQueue(item.id);
          synced++;
          if (onProgress) onProgress({ type: 'success', item });
        } catch (e) {
          item.tentativas = (item.tentativas || 0) + 1;
          if (item.tentativas >= 8) {
            // Desiste após muitas tentativas — mantém local mas marca erro
            item._erro_final = e.message;
            await put(STORES.fila_pendentes, item);
            if (onProgress) onProgress({ type: 'gave_up', item, error: e.message });
          } else {
            await put(STORES.fila_pendentes, item);
            if (onProgress) onProgress({ type: 'retry', item, error: e.message });
          }
          failed++;
        }
      }
    } finally {
      _syncing = false;
    }
    return { synced, failed };
  }

  function registerBackgroundSync() {
    if ('serviceWorker' in navigator && 'SyncManager' in window) {
      navigator.serviceWorker.ready
        .then((reg) => reg.sync.register('garra-sync-pendentes'))
        .catch(() => {});
    }
  }

  // ── Monitoramento de conectividade ──────────────────────────
  let _onlineHandlers = [];
  function onBackOnline(handler) {
    _onlineHandlers.push(handler);
  }

  window.addEventListener('online', async () => {
    const result = await syncPendentes();
    _onlineHandlers.forEach((h) => {
      try { h(result); } catch (e) { console.error(e); }
    });
  });

  // Tentativa periódica mesmo sem evento 'online' confiável (alguns Android)
  setInterval(async () => {
    if (navigator.onLine) {
      const pending = await countPending();
      if (pending > 0) {
        const result = await syncPendentes();
        if (result.synced > 0) {
          _onlineHandlers.forEach((h) => {
            try { h(result); } catch (e) { console.error(e); }
          });
        }
      }
    }
  }, 30000); // a cada 30s

  // ── Fotos pendentes (blobs maiores, store separado) ─────────
  async function savePendingPhoto(localId, blob, meta) {
    return put(STORES.fotos_pendentes, {
      id: localId,
      blob,
      meta,
      criado_em: new Date().toISOString(),
    });
  }
  async function getPendingPhotos() {
    return getAll(STORES.fotos_pendentes);
  }
  async function removePendingPhoto(localId) {
    return del(STORES.fotos_pendentes, localId);
  }

  return {
    STORES,
    open, get, getAll, put, putMany, del, clearStore,
    cacheGet, cacheSet,
    fetchWithFallback, postWithQueue,
    getQueue, countPending, syncPendentes, onBackOnline,
    savePendingPhoto, getPendingPhotos, removePendingPhoto,
  };
})();
