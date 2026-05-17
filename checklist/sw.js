/* ═══════════════════════════════════════════════════
   sw.js — Garra Check List v7
   Offline-first robusto
═══════════════════════════════════════════════════ */

const CACHE = 'garra-v7';

const APP_SHELL = [
  '/index.html',
  '/css/style.css',
  '/js/db.js',
  '/js/data.js',
  '/js/app.js',
  '/js/logistics.js',
  '/icons/logo.png',
  '/manifest.json',
];

// ── INSTALL ─────────────────────────────────────────
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(async cache => {
      for (const url of APP_SHELL) {
        try {
          await cache.add(url);
          console.log('[SW] Cacheado:', url);
        } catch(err) {
          console.warn('[SW] Falhou:', url, err.message);
        }
      }
      console.log('[SW] ✅ Install completo');
    })
  );
});

// ── ACTIVATE ────────────────────────────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => {
          console.log('[SW] Removendo cache antigo:', k);
          return caches.delete(k);
        })
      ))
      .then(() => self.clients.claim())
      .then(() => console.log('[SW] ✅ Ativo e no controle'))
  );
});

// ── FETCH ───────────────────────────────────────────
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Só GET
  if (e.request.method !== 'GET') return;

  // API garra-sistemas — network only, sem cache
  if (url.hostname === 'garra-sistemas.onrender.com') {
    e.respondWith(
      fetch(e.request, { signal: AbortSignal.timeout(8000) })
        .catch(() => new Response(
          JSON.stringify({ error: 'offline' }),
          { status: 503, headers: { 'Content-Type': 'application/json' } }
        ))
    );
    return;
  }

  // Google Fonts — stale-while-revalidate
  if (url.hostname.includes('fonts.googleapis') || url.hostname.includes('fonts.gstatic')) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        const fresh = fetch(e.request).then(res => {
          caches.open(CACHE).then(c => c.put(e.request, res.clone()));
          return res;
        }).catch(() => cached);
        return cached || fresh;
      })
    );
    return;
  }

  // App shell — Cache First, atualiza em background
  e.respondWith(
    caches.open(CACHE).then(async cache => {
      const cached = await cache.match(e.request);

      // Atualiza cache em background sem bloquear resposta
      fetch(e.request).then(res => {
        if (res && res.status === 200) cache.put(e.request, res.clone());
      }).catch(() => {});

      if (cached) return cached;

      // Sem cache — tenta rede
      try {
        const res = await fetch(e.request);
        if (res && res.status === 200) cache.put(e.request, res.clone());
        return res;
      } catch {
        // Sem rede e sem cache — serve index.html como fallback
        const fallback = await cache.match('/index.html');
        if (fallback) return fallback;
        return new Response('<h1>Offline</h1><p>Abra o sistema com internet primeiro.</p>',
          { headers: { 'Content-Type': 'text/html' } });
      }
    })
  );
});

self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});
