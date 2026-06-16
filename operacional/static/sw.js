/* ═══════════════════════════════════════════════════════
   Garra Mobile — Service Worker v1
   Offline-first PWA unificado
   Módulos: Jardinagem · Operacional · Checklist
═══════════════════════════════════════════════════════ */

const SW_VERSION   = 'garra-mobile-v1';
const CACHE_STATIC = SW_VERSION + '-static';
const CACHE_API    = SW_VERSION + '-api';
const SYNC_QUEUE   = 'garra-sync-queue';

/* ── Assets que sempre ficam em cache ── */
const STATIC_ASSETS = [
  '/mobile',
  '/mobile/manifest.json',
];

/* ── APIs que fazem cache para fallback offline ── */
const CACHE_API_PATTERNS = [
  '/operacional/api/minhas-os',
  '/operacional/api/equipamentos',
  '/operacional/api/operadores',
  '/operacional/api/clientes',
  '/jardinagem/api/inicio',
  '/jardinagem/api/meses',
  '/checklist/modelos',
  '/permissoes/usuario/',
];

/* ── APIs que NUNCA fazem cache ── */
const NO_CACHE_PATTERNS = [
  '/auth/',
  '/auth/login',
  '/auth/renovar',
];

/* ════════════════════════════════════════
   INSTALL — pré-cache dos assets estáticos
════════════════════════════════════════ */
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_STATIC)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

/* ════════════════════════════════════════
   ACTIVATE — limpar caches antigos
════════════════════════════════════════ */
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('garra-mobile-') && k !== CACHE_STATIC && k !== CACHE_API)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

/* ════════════════════════════════════════
   FETCH — estratégias por tipo de request
════════════════════════════════════════ */
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const path = url.pathname;

  /* Ignorar requests não-GET que não são POST de sync */
  if (event.request.method !== 'GET' && event.request.method !== 'POST') return;

  /* Ignorar auth — sempre online */
  if (NO_CACHE_PATTERNS.some(p => path.includes(p))) return;

  /* POST — interceptar para queue offline */
  if (event.request.method === 'POST') {
    event.respondWith(handlePost(event.request));
    return;
  }

  /* Assets estáticos — Cache First */
  if (isStaticAsset(path)) {
    event.respondWith(cacheFirst(event.request, CACHE_STATIC));
    return;
  }

  /* APIs com cache — Network First */
  if (CACHE_API_PATTERNS.some(p => path.includes(p))) {
    event.respondWith(networkFirstWithCache(event.request));
    return;
  }

  /* Demais — Network Only */
});

/* ════════════════════════════════════════
   ESTRATÉGIAS
════════════════════════════════════════ */

function isStaticAsset(path) {
  return path === '/mobile' ||
         path.endsWith('.html') ||
         path.endsWith('.js') ||
         path.endsWith('.css') ||
         path.endsWith('.json') ||
         path.endsWith('.png') ||
         path.endsWith('.ico') ||
         path.endsWith('.jpg');
}

/* Cache First — assets estáticos */
async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response('Offline — recurso não disponível', { status: 503 });
  }
}

/* Network First — APIs com fallback cache */
async function networkFirstWithCache(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_API);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    const cached = await caches.match(request);
    if (cached) {
      return new Response(
        JSON.stringify({ _offline: true, _cached: true,
          data: await cached.json().catch(() => null) }),
        { status: 200, headers: { 'Content-Type': 'application/json',
          'X-Garra-Offline': 'true' }}
      );
    }
    return new Response(
      JSON.stringify({ error: 'Sem conexão e sem cache disponível' }),
      { status: 503, headers: { 'Content-Type': 'application/json' }}
    );
  }
}

/* POST offline — salva na fila para sync posterior */
async function handlePost(request) {
  try {
    const response = await fetch(request.clone());
    return response;
  } catch {
    /* Offline — salvar na fila */
    const url  = request.url;
    const body = await request.text().catch(() => '{}');

    /* Só salva na fila endpoints conhecidos */
    const syncableEndpoints = [
      '/operacional/api/os/',
      '/jardinagem/api/relatorios/km',
      '/jardinagem/api/pares',
      '/checklist/envios',
    ];

    const isSyncable = syncableEndpoints.some(ep => url.includes(ep));

    if (isSyncable) {
      const queue = await getQueue();
      queue.push({
        id:        Date.now() + '_' + Math.random().toString(36).slice(2,6),
        url,
        method:    'POST',
        body,
        headers:   Object.fromEntries(request.headers.entries()),
        timestamp: new Date().toISOString(),
        tentativas: 0,
      });
      await saveQueue(queue);

      /* Registrar Background Sync se disponível */
      try {
        await self.registration.sync.register('garra-sync-pendentes');
      } catch {}

      return new Response(
        JSON.stringify({ ok: true, offline: true, queued: true }),
        { status: 202, headers: { 'Content-Type': 'application/json',
          'X-Garra-Queued': 'true' }}
      );
    }

    return new Response(
      JSON.stringify({ error: 'Sem conexão' }),
      { status: 503, headers: { 'Content-Type': 'application/json' }}
    );
  }
}

/* ════════════════════════════════════════
   BACKGROUND SYNC — processar fila
════════════════════════════════════════ */
self.addEventListener('sync', event => {
  if (event.tag === 'garra-sync-pendentes') {
    event.waitUntil(processQueue());
  }
});

async function processQueue() {
  const queue = await getQueue();
  if (!queue.length) return;

  const failed = [];
  for (const item of queue) {
    try {
      const res = await fetch(item.url, {
        method:  item.method,
        headers: item.headers,
        body:    item.body,
      });
      if (res.ok) {
        /* Notificar cliente do sucesso */
        self.clients.matchAll().then(clients => {
          clients.forEach(c => c.postMessage({
            type:    'SYNC_SUCCESS',
            item_id: item.id,
            url:     item.url,
          }));
        });
      } else {
        item.tentativas++;
        if (item.tentativas < 5) failed.push(item);
      }
    } catch {
      item.tentativas++;
      if (item.tentativas < 5) failed.push(item);
    }
  }
  await saveQueue(failed);
}

/* ════════════════════════════════════════
   FILA OFFLINE — IndexedDB simples via Cache API
════════════════════════════════════════ */
async function getQueue() {
  try {
    const cache = await caches.open(SYNC_QUEUE);
    const res   = await cache.match('/sw-queue');
    if (!res) return [];
    return await res.json();
  } catch { return []; }
}

async function saveQueue(queue) {
  try {
    const cache = await caches.open(SYNC_QUEUE);
    await cache.put('/sw-queue', new Response(
      JSON.stringify(queue),
      { headers: { 'Content-Type': 'application/json' }}
    ));
  } catch {}
}

/* ════════════════════════════════════════
   MENSAGENS DO CLIENTE
════════════════════════════════════════ */
self.addEventListener('message', async event => {
  const { type } = event.data || {};

  /* Cliente pede para processar fila manualmente */
  if (type === 'SYNC_NOW') {
    await processQueue();
    const queue = await getQueue();
    event.source?.postMessage({ type: 'QUEUE_STATUS', pending: queue.length });
  }

  /* Cliente pede status da fila */
  if (type === 'QUEUE_COUNT') {
    const queue = await getQueue();
    event.source?.postMessage({ type: 'QUEUE_STATUS', pending: queue.length });
  }

  /* Forçar update do SW */
  if (type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

/* ════════════════════════════════════════
   PUSH NOTIFICATIONS (futuro)
════════════════════════════════════════ */
self.addEventListener('push', event => {
  if (!event.data) return;
  const data = event.data.json();
  event.waitUntil(
    self.registration.showNotification(data.title || 'Garra', {
      body: data.body || '',
      icon: '/static/icons/icon-192.png',
      badge: '/static/icons/icon-192.png',
    })
  );
});
