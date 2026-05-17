/* ═══════════════════════════════════════════════════
   sw.js — Service Worker Garra Check List v4
   Estratégia: Cache First para arquivos do app
   Network First para API
   Garante funcionamento 100% offline
═══════════════════════════════════════════════════ */

const CACHE_NAME = 'garra-app-v4';

// Todos os arquivos necessários para funcionar offline
const ARQUIVOS_CORE = [
  './',
  './index.html',
  './css/style.css',
  './js/db.js',
  './js/data.js',
  './js/app.js',
  './js/logistics.js',
  './icons/logo.png',
  './manifest.json',
  './sw.js',
];

// ── INSTALL: pré-cacheia TUDO imediatamente ─────────
self.addEventListener('install', event => {
  console.log('[SW] Instalando e cacheando arquivos...');
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('[SW] Cacheando', ARQUIVOS_CORE.length, 'arquivos');
        return cache.addAll(ARQUIVOS_CORE);
      })
      .then(() => {
        console.log('[SW] Cache completo ✅');
        return self.skipWaiting(); // Ativa imediatamente
      })
      .catch(err => console.error('[SW] Erro no cache:', err))
  );
});

// ── ACTIVATE: limpa caches antigos ─────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => {
          console.log('[SW] Removendo cache antigo:', k);
          return caches.delete(k);
        })
      )
    ).then(() => self.clients.claim()) // Assume controle imediato
  );
});

// ── FETCH: estratégia inteligente por tipo ──────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // 1. Requisições à API — Network Only (nunca cacheia)
  if (url.hostname.includes('onrender.com') &&
      !url.pathname.endsWith('.html') &&
      !url.pathname.endsWith('.js') &&
      !url.pathname.endsWith('.css') &&
      !url.pathname.endsWith('.jpg') &&
      !url.pathname.endsWith('.png')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({error:'offline'}), {
          status: 503,
          headers: {'Content-Type':'application/json'}
        })
      )
    );
    return;
  }

  // 2. Apenas GET
  if (event.request.method !== 'GET') return;

  // 3. Fontes externas (Google Fonts) — Network com fallback
  if (url.hostname.includes('fonts.google') || url.hostname.includes('fonts.gstatic')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(res => {
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          return res;
        }).catch(() => new Response('', {status: 200}));
      })
    );
    return;
  }

  // 4. Arquivos do app — Cache First, atualiza em background
  event.respondWith(
    caches.open(CACHE_NAME).then(cache =>
      cache.match(event.request).then(cached => {
        // Busca versão nova em background (stale-while-revalidate)
        const fetchPromise = fetch(event.request)
          .then(networkRes => {
            if (networkRes && networkRes.status === 200) {
              cache.put(event.request, networkRes.clone());
            }
            return networkRes;
          })
          .catch(() => null);

        // Retorna cache imediatamente se disponível
        return cached || fetchPromise || caches.match('./index.html');
      })
    )
  );
});

// ── MENSAGEM: força atualização ─────────────────────
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
