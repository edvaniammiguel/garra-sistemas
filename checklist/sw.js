/* ═══════════════════════════════════════════════════
   sw.js — Garra Check List v6
   Cache First garantido para todos os arquivos do app
═══════════════════════════════════════════════════ */

const CACHE = 'garra-v6';
const APP_SHELL = [
  '/',
  '/index.html',
  '/css/style.css',
  '/js/db.js',
  '/js/data.js',
  '/js/app.js',
  '/js/logistics.js',
  '/icons/logo.png',
  '/manifest.json',
];

// ── INSTALL: cacheia tudo imediatamente ────────────
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(cache => {
      console.log('[SW] Cacheando app shell...');
      // Cacheia cada arquivo individualmente para não falhar tudo se um falhar
      return Promise.allSettled(
        APP_SHELL.map(url =>
          cache.add(url).catch(err => console.warn('[SW] Falhou ao cachear:', url, err))
        )
      );
    }).then(() => console.log('[SW] ✅ Cache completo'))
  );
});

// ── ACTIVATE: limpa caches antigos ─────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// ── FETCH ───────────────────────────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Ignora não-GET
  if (e.request.method !== 'GET') return;

  // API do Render (garra-sistemas) — nunca cacheia, retorna erro offline
  if (url.hostname === 'garra-sistemas.onrender.com') {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({ error: 'offline' }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' }
        })
      )
    );
    return;
  }

  // Google Fonts — cache com fallback vazio
  if (url.hostname.includes('fonts.')) {
    e.respondWith(
      caches.match(e.request).then(cached => cached ||
        fetch(e.request).then(res => {
          caches.open(CACHE).then(c => c.put(e.request, res.clone()));
          return res;
        }).catch(() => new Response('', { status: 200 }))
      )
    );
    return;
  }

  // TUDO MAIS (app shell) — Cache First com atualização em background
  e.respondWith(
    caches.match(e.request).then(cached => {
      // Atualiza em background
      const fetchAndUpdate = fetch(e.request)
        .then(res => {
          if (res && res.status === 200 && res.type !== 'opaque') {
            caches.open(CACHE).then(c => c.put(e.request, res.clone()));
          }
          return res;
        })
        .catch(() => null);

      // Retorna cache se disponível, senão aguarda rede
      if (cached) {
        fetchAndUpdate; // atualiza silenciosamente
        return cached;
      }
      // Sem cache — tenta rede, fallback para index.html
      return fetchAndUpdate.then(res => res || caches.match('/index.html'));
    })
  );
});

self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});
