/**
 * Service Worker v8 — Estratégia cache + network integrada com GarraDB
 * 
 * Escopo: /operacional/
 * 
 * Estratégias:
 * 1. HTML (pages): network-first, fallback para cache
 * 2. API: network-first, fallback para IndexedDB (GarraDB)
 * 3. Assets (JS/CSS/icons): cache-first
 * 4. Imagens: cache-first com limite de tamanho
 */

const CACHE_NAME = 'garra-operacional-v8';
const ASSETS_CACHE = 'garra-assets-v1';
const OFFLINE_PAGE = '/operacional/offline.html';

// Assets que devem sempre estar em cache (shell)
const PRECACHE_ASSETS = [
  '/operacional/static/mobile.html',
  '/operacional/static/sw.js',
  '/operacional/static/idb.js',
  '/operacional/manifest.json',
  '/operacional/static/css/style.css',
  '/operacional/static/js/app.js',
  '/static/icons/favicon.ico',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png'
];

// ============================================================
// INSTALL — Cachear assets críticos
// ============================================================

self.addEventListener('install', (e) => {
  console.log('[SW] Installing v8...');
  e.waitUntil(
    caches.open(ASSETS_CACHE)
      .then(cache => cache.addAll(PRECACHE_ASSETS.filter(url => url)))
      .then(() => self.skipWaiting())
  );
});

// ============================================================
// ACTIVATE — Limpar caches antigos
// ============================================================

self.addEventListener('activate', (e) => {
  console.log('[SW] Activating v8...');
  e.waitUntil(
    caches.keys().then(names =>
      Promise.all(
        names
          .filter(name => name !== ASSETS_CACHE && name !== CACHE_NAME)
          .map(name => {
            console.log(`[SW] Deletando cache antigo: ${name}`);
            return caches.delete(name);
          })
      )
    ).then(() => self.clients.claim())
  );
});

// ============================================================
// FETCH — Estratégias de cache por tipo de recurso
// ============================================================

self.addEventListener('fetch', (e) => {
  const { request } = e;
  const url = new URL(request.url);

  // 1. HTML pages — network-first
  if (request.headers.get('accept')?.includes('text/html')) {
    return e.respondWith(networkFirstPage(request));
  }

  // 2. API calls — network-first, fallback para IndexedDB
  if (url.pathname.includes('/api/')) {
    if (request.method === 'GET') {
      return e.respondWith(networkFirstAPI(request));
    }
    // POST/PATCH/DELETE — não cachear, deixar GarraDB.postWithQueue gerenciar
    return;
  }

  // 3. Assets (JS, CSS, icons) — cache-first
  if (
    url.pathname.endsWith('.js') ||
    url.pathname.endsWith('.css') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.includes('/icons/') ||
    url.pathname.endsWith('/manifest.json')
  ) {
    return e.respondWith(cacheFirstAssets(request));
  }

  // 4. Imagens — cache-first com limite
  if (request.destination === 'image') {
    return e.respondWith(cacheFirstImages(request));
  }

  // 5. Default — network-first
  return e.respondWith(networkFirst(request));
});

// ============================================================
// ESTRATÉGIAS DE CACHE
// ============================================================

async function networkFirstPage(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
      return response;
    }
  } catch (err) {
    console.log('[SW] Network falhou para page, tentando cache...');
  }

  const cached = await caches.match(request);
  if (cached) return cached;

  return caches.match(OFFLINE_PAGE) || new Response('Offline', { status: 503 });
}

async function networkFirstAPI(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
      return response;
    }
  } catch (err) {
    console.log(`[SW] Network falhou para ${request.url}, tentando cache...`);
  }

  // Fallback para cache
  const cached = await caches.match(request);
  if (cached) {
    console.log(`[SW] Cache hit: ${request.url}`);
    return cached;
  }

  // Sem cache, retornar erro
  return new Response(JSON.stringify({ error: 'Offline' }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' }
  });
}

async function cacheFirstAssets(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(ASSETS_CACHE);
      cache.put(request, response.clone());
      return response;
    }
  } catch (err) {
    console.warn(`[SW] Falha ao buscar asset: ${request.url}`);
  }

  return new Response('Asset não disponível', { status: 404 });
}

async function cacheFirstImages(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(ASSETS_CACHE);
      const size = response.headers.get('content-length');

      // Cachear apenas imagens < 5MB
      if (!size || size < 5242880) {
        cache.put(request, response.clone());
      }

      return response;
    }
  } catch (err) {
    console.log(`[SW] Imagem offline: ${request.url}`);
  }

  // Placeholder para imagem offline
  return new Response(
    '<svg width="200" height="200" xmlns="http://www.w3.org/2000/svg"><rect fill="#ddd" width="200" height="200"/><text x="50%" y="50%" text-anchor="middle" dy=".3em" fill="#666" font-size="14">Offline</text></svg>',
    { headers: { 'Content-Type': 'image/svg+xml' } }
  );
}

async function networkFirst(request) {
  try {
    return await fetch(request);
  } catch (err) {
    const cached = await caches.match(request);
    return cached || new Response('Offline', { status: 503 });
  }
}

// ============================================================
// BACKGROUND SYNC (futuro)
// ============================================================

// Quando voltar online, disparar sync da fila GarraDB
self.addEventListener('sync', (e) => {
  if (e.tag === 'garradb-sync') {
    e.waitUntil(
      self.clients.matchAll().then(clients => {
        clients.forEach(client => {
          client.postMessage({
            type: 'GARRADB_SYNC_REQUESTED'
          });
        });
      })
    );
  }
});

// ============================================================
// MESSAGE — Comunicação com frontend
// ============================================================

self.addEventListener('message', (e) => {
  if (e.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (e.data.type === 'GARRADB_CLEAR_CACHE') {
    caches.delete(CACHE_NAME).then(() => {
      e.ports[0].postMessage({ cleared: true });
    });
  }
});
