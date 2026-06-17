/**
 * GarraDB v2 — IndexedDB wrapper escalável
 * Padrão único para offline-first em todos os módulos
 * (Operacional, Checklist, Jardinagem)
 * 
 * Uso:
 *   await GarraDB.fetchWithFallback('/api/os')  // leitura com fallback
 *   await GarraDB.postWithQueue('/api/partes', data)  // escrita com fila
 */

class GarraDB {
  static db = null;
  static isOnline = navigator.onLine;
  static syncTimer = null;
  static isSyncing = false;

  // Nomes das tabelas IndexedDB
  static STORES = {
    QUEUE: 'queue',        // requisições pendentes
    CACHE: 'cache',        // leituras cacheadas
    METADATA: 'metadata'   // timestamps, versão, etc
  };

  static async init() {
    return new Promise((resolve, reject) => {
      if (GarraDB.db) return resolve(GarraDB.db);

      const request = indexedDB.open('GarraDB', 2);

      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        GarraDB.db = request.result;
        resolve(GarraDB.db);
      };

      request.onupgradeneeded = (e) => {
        const db = e.target.result;

        // Tabela: queue (requisições pendentes)
        if (!db.objectStoreNames.contains(GarraDB.STORES.QUEUE)) {
          const qStore = db.createObjectStore(GarraDB.STORES.QUEUE, { 
            keyPath: 'id', 
            autoIncrement: true 
          });
          qStore.createIndex('url', 'url', { unique: false });
          qStore.createIndex('createdAt', 'createdAt', { unique: false });
          qStore.createIndex('status', 'status', { unique: false });
        }

        // Tabela: cache (leituras cacheadas)
        if (!db.objectStoreNames.contains(GarraDB.STORES.CACHE)) {
          const cStore = db.createObjectStore(GarraDB.STORES.CACHE, { keyPath: 'url' });
          cStore.createIndex('expiresAt', 'expiresAt', { unique: false });
        }

        // Tabela: metadata
        if (!db.objectStoreNames.contains(GarraDB.STORES.METADATA)) {
          db.createObjectStore(GarraDB.STORES.METADATA, { keyPath: 'key' });
        }
      };
    });
  }

  /**
   * fetchWithFallback(url, options)
   * Tenta rede → cache → null
   * Opções: { cacheKey, cacheTTL: 3600 }
   */
  static async fetchWithFallback(url, options = {}) {
    const { cacheKey = url, cacheTTL = 1800 } = options;

    try {
      // 1. Tenta rede
      if (GarraDB.isOnline) {
        const response = await fetch(url, {
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('garra_token') || ''}`,
            'Content-Type': 'application/json'
          }
        });

        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();

        // Cacheia resposta
        await GarraDB._cacheSet(cacheKey, data, cacheTTL);

        return data;
      }
    } catch (err) {
      console.warn(`[GarraDB] Falha na rede para ${url}:`, err.message);
    }

    // 2. Fallback para cache
    try {
      const cached = await GarraDB._cacheGet(cacheKey);
      if (cached) {
        console.log(`[GarraDB] Cache hit: ${cacheKey}`);
        return cached;
      }
    } catch (err) {
      console.warn(`[GarraDB] Falha ao ler cache:`, err.message);
    }

    // 3. Sem cache, sem rede
    console.warn(`[GarraDB] Nenhum dado disponível para ${url}`);
    return null;
  }

  /**
   * postWithQueue(url, data, options)
   * Enfileira requisição + tenta POST imediatamente
   * Opções: { retries: 5, retryDelay: 30000, dedup: 'url+method' }
   */
  static async postWithQueue(url, data, options = {}) {
    const {
      retries = 5,
      retryDelay = 30000,  // 30s
      dedup = 'url+method',
      method = 'POST'
    } = options;

    await GarraDB.init();

    const queueItem = {
      url,
      method,
      data,
      status: 'pending',
      attempts: 0,
      maxRetries: retries,
      retryDelay,
      createdAt: Date.now(),
      dedup
    };

    // Verificar duplicata
    if (dedup === 'url+method') {
      const existing = await GarraDB._findQueueItem({ url, method, status: 'pending' });
      if (existing) {
        console.warn(`[GarraDB] Requisição duplicada descartada: ${method} ${url}`);
        return { success: false, reason: 'duplicate' };
      }
    }

    // Salvar na fila
    const qId = await GarraDB._queuePush(queueItem);
    queueItem.id = qId;

    // Tentar POST imediatamente
    if (GarraDB.isOnline) {
      const result = await GarraDB._attemptPost(queueItem);
      if (result.success) {
        await GarraDB._queueRemove(qId);
        return { success: true, data: result.data };
      }
    }

    // Disparar sync automático se estiver offline
    if (!GarraDB.isOnline) {
      console.log('[GarraDB] Offline - requisição enfileirada');
      GarraDB._scheduleSync();
    }

    return { success: false, queued: qId, reason: 'queued' };
  }

  /**
   * syncPendentes()
   * Sincroniza fila quando voltar online
   * Retry exponencial, máx tentativas, remove sucesso
   */
  static async syncPendentes() {
    if (GarraDB.isSyncing) return;
    GarraDB.isSyncing = true;

    try {
      await GarraDB.init();
      const pending = await GarraDB._getQueueByStatus('pending');

      if (pending.length === 0) {
        console.log('[GarraDB] Fila vazia - nada para sincronizar');
        GarraDB.isSyncing = false;
        return;
      }

      console.log(`[GarraDB] Sincronizando ${pending.length} itens...`);

      for (const item of pending) {
        if (!GarraDB.isOnline) break;

        item.attempts += 1;
        const result = await GarraDB._attemptPost(item);

        if (result.success) {
          await GarraDB._queueRemove(item.id);
          console.log(`[GarraDB] ✓ Sincronizado: ${item.method} ${item.url}`);
          
          // Disparar evento customizado para UI atualizar
          window.dispatchEvent(new CustomEvent('garradb:synced', {
            detail: { url: item.url, method: item.method }
          }));
        } else if (item.attempts >= item.maxRetries) {
          // Marcar como falha permanente
          item.status = 'failed';
          await GarraDB._queueUpdate(item);
          console.error(`[GarraDB] ✗ Falha permanente: ${item.method} ${item.url}`);

          // Disparar evento de erro
          window.dispatchEvent(new CustomEvent('garradb:failed', {
            detail: { url: item.url, method: item.method, attempts: item.attempts }
          }));
        } else {
          // Aguardar delay exponencial antes da próxima tentativa
          item.nextRetryAt = Date.now() + (item.retryDelay * Math.pow(2, item.attempts - 1));
          await GarraDB._queueUpdate(item);
          console.log(`[GarraDB] ⏳ Retry em ${item.retryDelay / 1000}s: ${item.method} ${item.url}`);
        }
      }
    } catch (err) {
      console.error('[GarraDB] Erro durante sync:', err);
    } finally {
      GarraDB.isSyncing = false;
    }
  }

  /**
   * getQueue()
   * Retorna lista de requisições pendentes (para debug/UI)
   */
  static async getQueue() {
    await GarraDB.init();
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.QUEUE], 'readonly');
      const store = tx.objectStore(GarraDB.STORES.QUEUE);
      const request = store.getAll();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result);
    });
  }

  /**
   * clearQueue()
   * Remove todos os itens da fila (use com cuidado)
   */
  static async clearQueue() {
    await GarraDB.init();
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.QUEUE], 'readwrite');
      const store = tx.objectStore(GarraDB.STORES.QUEUE);
      const request = store.clear();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  // ==================== PRIVADOS ====================

  static async _attemptPost(queueItem) {
    try {
      const token = localStorage.getItem('garra_token') || '';
      const response = await fetch(queueItem.url, {
        method: queueItem.method || 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(queueItem.data)
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const data = await response.json();
      return { success: true, data };
    } catch (err) {
      console.warn(`[GarraDB] Tentativa falhou (${queueItem.attempts}/${queueItem.maxRetries}):`, err.message);
      return { success: false, error: err.message };
    }
  }

  static async _queuePush(item) {
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.QUEUE], 'readwrite');
      const store = tx.objectStore(GarraDB.STORES.QUEUE);
      const request = store.add(item);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result);
    });
  }

  static async _queueRemove(id) {
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.QUEUE], 'readwrite');
      const store = tx.objectStore(GarraDB.STORES.QUEUE);
      const request = store.delete(id);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  static async _queueUpdate(item) {
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.QUEUE], 'readwrite');
      const store = tx.objectStore(GarraDB.STORES.QUEUE);
      const request = store.put(item);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  static async _getQueueByStatus(status) {
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.QUEUE], 'readonly');
      const store = tx.objectStore(GarraDB.STORES.QUEUE);
      const index = store.index('status');
      const request = index.getAll(status);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result);
    });
  }

  static async _findQueueItem(criteria) {
    const all = await GarraDB.getQueue();
    return all.find(item =>
      item.url === criteria.url &&
      item.method === (criteria.method || 'POST') &&
      item.status === (criteria.status || 'pending')
    );
  }

  static async _cacheSet(key, data, ttl) {
    const db = GarraDB.db;
    const expiresAt = Date.now() + (ttl * 1000);
    const item = { url: key, data, expiresAt };

    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.CACHE], 'readwrite');
      const store = tx.objectStore(GarraDB.STORES.CACHE);
      const request = store.put(item);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  static async _cacheGet(key) {
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.CACHE], 'readonly');
      const store = tx.objectStore(GarraDB.STORES.CACHE);
      const request = store.get(key);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => {
        const item = request.result;
        if (!item) return resolve(null);
        if (Date.now() > item.expiresAt) {
          // Cache expirado
          GarraDB._cacheDelete(key);
          return resolve(null);
        }
        resolve(item.data);
      };
    });
  }

  static async _cacheDelete(key) {
    const db = GarraDB.db;
    return new Promise((resolve, reject) => {
      const tx = db.transaction([GarraDB.STORES.CACHE], 'readwrite');
      const store = tx.objectStore(GarraDB.STORES.CACHE);
      const request = store.delete(key);
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve();
    });
  }

  static _scheduleSync() {
    if (GarraDB.syncTimer) clearInterval(GarraDB.syncTimer);
    GarraDB.syncTimer = setInterval(() => {
      if (GarraDB.isOnline && !GarraDB.isSyncing) {
        GarraDB.syncPendentes();
      }
    }, 30000); // A cada 30s
  }
}

// Event listeners globais
window.addEventListener('online', () => {
  console.log('[GarraDB] ✓ Online detectado');
  GarraDB.isOnline = true;
  GarraDB.syncPendentes();
});

window.addEventListener('offline', () => {
  console.log('[GarraDB] ✗ Offline detectado');
  GarraDB.isOnline = false;
});

// Inicializar ao carregar
GarraDB.init().catch(err => console.error('[GarraDB] Falha ao inicializar:', err));
