/* ═══════════════════════════════════════════════════
   sw.js — Garra Check List v11 — 20260705 (ranking servidor)
═══════════════════════════════════════════════════ */

const CACHE = 'garra-v42-20260716c';

const APP_SHELL = [
  '/index.html',
  '/css/style.css',
  '/js/db.js',
  '/js/data.js',
  '/js/app.js',
  '/js/logistics.js',
  '/icons/logo.png',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/favicon.ico',
  '/icons/favicon-32.png',
  '/icons/favicon-16.png',
  '/manifest.json',
];

self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(
    caches.open(CACHE).then(cache => {
      console.log('[SW] Cacheando app shell v9...');
      return Promise.allSettled(
        APP_SHELL.map(url => cache.add(url))
      ).then(results => {
        const ok  = results.filter(r => r.status==='fulfilled').length;
        const err = results.filter(r => r.status==='rejected').length;
        console.log(`[SW] Cache: ${ok} OK, ${err} falhas`);
        if (err > 0) {
          results.forEach((r,i) => {
            if (r.status==='rejected') console.warn('[SW] Falhou:', APP_SHELL[i], r.reason?.message);
          });
        }
      });
    })
  );
});

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

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET') return;

  // API — sempre rede
  if (url.hostname === 'garra-sistemas.onrender.com') {
    e.respondWith(
      fetch(e.request).catch(() =>
        new Response(JSON.stringify({error:'offline'}),
          {status:503, headers:{'Content-Type':'application/json'}})
      )
    );
    return;
  }

  // Fontes Google — cache com fallback
  if (url.hostname.includes('fonts.')) {
    e.respondWith(
      caches.match(e.request).then(cached => {
        if (cached) return cached;
        return fetch(e.request).then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        }).catch(() => new Response('', {status:200}));
      })
    );
    return;
  }

  // JS e CSS — Network First: sempre tenta a versão mais nova da rede,
  // usa o cache só se estiver offline. Evita servir código velho (ex: fixes
  // que não chegavam ao dispositivo por causa do Cache First).
  if (url.pathname.endsWith('.js') || url.pathname.endsWith('.css')) {
    e.respondWith(
      fetch(e.request).then(res => {
        if (res && res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(() => caches.match(e.request))
    );
    return;
  }

  // App shell — Cache First
  e.respondWith(
    caches.match(e.request).then(cached => {
      if (cached) {
        // Atualiza em background
        fetch(e.request).then(res => {
          if (res && res.ok) {
            caches.open(CACHE).then(c => c.put(e.request, res));
          }
        }).catch(() => {});
        return cached;
      }
      // Sem cache — busca da rede
      return fetch(e.request).then(res => {
        if (res && res.ok) {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      }).catch(async () => {
        const fallback = await caches.match('/index.html');
        return fallback || new Response(
          '<html><body style="font-family:sans-serif;text-align:center;padding:40px"><h2>📶 Offline</h2><p>Abra com internet primeiro.</p></body></html>',
          {headers:{'Content-Type':'text/html'}}
        );
      });
    })
  );
});

self.addEventListener('message', e => {
  if (e.data === 'SKIP_WAITING') self.skipWaiting();
});
