/* ═══════════════════════════════════════════════════
   sw.js — Service Worker Garra Check List
   Estratégia: Network First com fallback para cache
   Atualiza automaticamente quando há nova versão
═══════════════════════════════════════════════════ */

const CACHE_VERSION = 'garra-v3-' + '2026051501';
const CACHE_STATIC  = CACHE_VERSION + '-static';

// Arquivos essenciais para funcionar offline
const CORE_FILES = [
  './',
  './index.html',
  './css/style.css',
  './js/db.js',
  './js/data.js',
  './js/app.js',
  './js/logistics.js',
  './icons/logo.jpg',
  './manifest.json',
];

// ── INSTALL: cacheia arquivos core ─────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_STATIC).then(cache => {
      return cache.addAll(CORE_FILES);
    }).then(() => {
      // Força ativação imediata sem esperar fechar outras abas
      return self.skipWaiting();
    })
  );
});

// ── ACTIVATE: limpa caches antigos ─────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys
          .filter(key => key !== CACHE_STATIC)
          .map(key => {
            console.log('[SW] Removendo cache antigo:', key);
            return caches.delete(key);
          })
      );
    }).then(() => {
      // Assume controle imediato de todas as abas abertas
      return self.clients.claim();
    })
  );
});

// ── FETCH: Network First para JS/CSS, Cache First para imagens ──
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Ignora requisições para a API (sempre vai para a rede)
  if (url.hostname.includes('onrender.com') && !url.pathname.includes('garra-checklist-app')) {
    return;
  }

  // Ignora requisições não-GET
  if (event.request.method !== 'GET') return;

  // Arquivos JS e CSS: Network First (sempre tenta pegar versão mais nova)
  if (url.pathname.endsWith('.js') || url.pathname.endsWith('.css')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Atualiza cache com versão nova
          const responseClone = response.clone();
          caches.open(CACHE_STATIC).then(cache => cache.put(event.request, responseClone));
          return response;
        })
        .catch(() => {
          // Sem rede: usa cache
          return caches.match(event.request);
        })
    );
    return;
  }

  // HTML: Network First (garante sempre versão mais recente)
  if (url.pathname.endsWith('.html') || url.pathname === '/' || url.pathname.endsWith('/')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          const responseClone = response.clone();
          caches.open(CACHE_STATIC).then(cache => cache.put(event.request, responseClone));
          return response;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }

  // Imagens e outros: Cache First (economiza banda)
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(response => {
        const responseClone = response.clone();
        caches.open(CACHE_STATIC).then(cache => cache.put(event.request, responseClone));
        return response;
      });
    })
  );
});

// ── MENSAGEM: força atualização quando solicitado ──
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
