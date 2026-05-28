/* ============================================================
   Garra Jardinagem — Service Worker v5
   CORREÇÃO: todos os paths com prefixo /jardinagem/
   Offline completo: fotos (bancada) + KM + histórico cacheado
   ============================================================ */

const CACHE_NAME  = "garra-jardinagem-v5";   // bump → invalida caches antigos
const DB_NAME     = "garra-offline-v2";
const STORE_FOTOS = "fila_fotos";
const STORE_KM    = "fila_km";
const STORE_PARES = "fila_pares";
const BASE        = "/jardinagem";            // prefixo de todas as rotas

// Assets essenciais para o app funcionar offline
const CACHE_URLS = [
  BASE + "/mobile",
  BASE + "/mobile-app",
  BASE + "/manifest.json",
  BASE + "/static/icons/logo-Garra-e-ca%C3%A7ambas.png",
  BASE + "/static/icons/favicon.ico",
  BASE + "/static/icons/icon-192.png",
  BASE + "/static/icons/icon-512.png",
];

// Rotas de API que ficam em cache para leitura offline
const API_CACHE_ROUTES = [
  BASE + "/api/semanas/ativa",
  BASE + "/api/historico/hoje",
];

// ── INSTALL ───────────────────────────────────────────────────
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        return Promise.allSettled(
          CACHE_URLS.map(url => cache.add(url).catch(e => console.warn("[SW] Não cacheou:", url, e)))
        );
      })
      .then(() => self.skipWaiting())
  );
});

// ── ACTIVATE ──────────────────────────────────────────────────
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE_NAME).map(k => {
          console.log("[SW] Removendo cache antigo:", k);
          return caches.delete(k);
        })
      ))
      .then(() => self.clients.claim())
  );
});

// ── FETCH ─────────────────────────────────────────────────────
self.addEventListener("fetch", event => {
  const url  = new URL(event.request.url);
  const path = url.pathname;

  // Ignorar requisições não-GET (POST de fotos, etc.)
  if (event.request.method !== "GET") return;

  // Rotas de API cacheáveis — Network First com fallback cache
  if (API_CACHE_ROUTES.some(r => path.startsWith(r))) {
    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          }
          return resp;
        })
        .catch(() => caches.match(event.request).then(cached =>
          cached || new Response(
            JSON.stringify({ erro: "Sem conexão", offline: true }),
            { headers: { "Content-Type": "application/json" } }
          )
        ))
    );
    return;
  }

  // Outras rotas de API — Network First, sem cache (POST/respostas dinâmicas)
  if (path.startsWith(BASE + "/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(
          JSON.stringify({ erro: "Sem conexão" }),
          { headers: { "Content-Type": "application/json" } }
        )
      )
    );
    return;
  }

  // Assets estáticos e páginas — Cache First com fallback network
  event.respondWith(
    caches.match(event.request).then(cached => {
      if (cached) return cached;
      return fetch(event.request).then(resp => {
        if (resp.ok && event.request.method === "GET") {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
        }
        return resp;
      }).catch(() => caches.match(BASE + "/mobile-app"));
    })
  );
});

// ── BACKGROUND SYNC ───────────────────────────────────────────
self.addEventListener("sync", event => {
  if (event.tag === "sync-garra") {
    event.waitUntil(processarFilaCompleta());
  }
});

// ── MENSAGENS do frontend ─────────────────────────────────────
self.addEventListener("message", event => {
  if (event.data?.tipo === "SYNC_AGORA") {
    processarFilaCompleta().then(resultado => {
      self.clients.matchAll().then(clients =>
        clients.forEach(c => c.postMessage({
          tipo:  "SYNC_CONCLUIDO",
          fotos: resultado.fotos,
          km:    resultado.km,
          erros: resultado.erros,
        }))
      );
    });
  }
  if (event.data?.tipo === "SKIP_WAITING") {
    self.skipWaiting();
  }
});

// ── INDEXEDDB ─────────────────────────────────────────────────
function abrirDB() {
  return new Promise((res, rej) => {
    const req = indexedDB.open(DB_NAME, 2);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_PARES)) {
        const s = db.createObjectStore(STORE_PARES, { keyPath: "offline_id" });
        s.createIndex("enviado", "enviado", { unique: false });
      }
      if (!db.objectStoreNames.contains(STORE_FOTOS)) {
        const s = db.createObjectStore(STORE_FOTOS, { keyPath: "offline_id" });
        s.createIndex("enviado", "enviado", { unique: false });
        s.createIndex("par_ref", "par_ref", { unique: false });
      }
      if (!db.objectStoreNames.contains(STORE_KM)) {
        const s = db.createObjectStore(STORE_KM, { keyPath: "offline_id" });
        s.createIndex("enviado", "enviado", { unique: false });
      }
    };
    req.onsuccess = e => res(e.target.result);
    req.onerror   = e => rej(e.target.error);
  });
}

function getAll(db, store, indexName, value) {
  return new Promise((res, rej) => {
    const tx  = db.transaction(store, "readonly");
    const s   = tx.objectStore(store);
    const req = indexName ? s.index(indexName).getAll(value) : s.getAll();
    req.onsuccess = e => res(e.target.result);
    req.onerror   = e => rej(e.target.error);
  });
}

function marcarEnviado(db, store, id) {
  return new Promise((res, rej) => {
    const tx = db.transaction(store, "readwrite");
    const s  = tx.objectStore(store);
    const r  = s.get(id);
    r.onsuccess = e => {
      const item = e.target.result;
      if (item) { item.enviado = true; s.put(item); }
      res();
    };
    r.onerror = e => rej(e.target.error);
  });
}

// ── PROCESSA FILA COMPLETA ────────────────────────────────────
async function processarFilaCompleta() {
  const resultado = { fotos: 0, km: 0, erros: 0 };
  let db;
  try { db = await abrirDB(); } catch(e) { console.error("[SW] Erro DB:", e); return resultado; }

  // 1. Pares pendentes
  const paresPend = await getAll(db, STORE_PARES, "enviado", false).catch(() => []);
  const parMap = {};
  for (const par of paresPend) {
    try {
      const resp = await fetch(BASE + "/api/pares", {
        method:  "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${par.token}` },
        body:    JSON.stringify({ semana_id: par.semana_id, local_nome: par.local_nome || "", ordem: 99 }),
      });
      if (resp.ok) {
        const data = await resp.json();
        parMap[par.offline_id] = data.id;
        await marcarEnviado(db, STORE_PARES, par.offline_id);
      }
    } catch(e) { resultado.erros++; }
  }

  // 2. Fotos pendentes
  const fotosPend = await getAll(db, STORE_FOTOS, "enviado", false).catch(() => []);
  for (const item of fotosPend) {
    try {
      let par_id = item.par_db_id || parMap[item.par_ref] || null;
      if (!par_id && item.semana_id) {
        const rp = await fetch(BASE + "/api/pares", {
          method:  "POST",
          headers: { "Content-Type": "application/json", "Authorization": `Bearer ${item.token}` },
          body:    JSON.stringify({ semana_id: item.semana_id, local_nome: item.local_nome || "", ordem: 99 }),
        });
        if (rp.ok) { const pd = await rp.json(); par_id = pd.id; }
      }
      if (!par_id) { resultado.erros++; continue; }

      const bytes = Uint8Array.from(atob(item.dados_b64), c => c.charCodeAt(0));
      const blob  = new Blob([bytes], { type: "image/jpeg" });
      const form  = new FormData();
      form.append("par_id", par_id);
      form.append("tipo",   item.tipo);
      form.append("foto",   blob, item.filename || "foto.jpg");

      const resp = await fetch(BASE + "/api/fotos/avulsa", {
        method:  "POST",
        headers: { "Authorization": `Bearer ${item.token}` },
        body:    form,
      });
      if (resp.ok) { await marcarEnviado(db, STORE_FOTOS, item.offline_id); resultado.fotos++; }
      else { resultado.erros++; }
    } catch(e) { resultado.erros++; console.warn("[SW] Erro foto:", item.offline_id, e); }
  }

  // 3. KM pendente
  const kmPend = await getAll(db, STORE_KM, "enviado", false).catch(() => []);
  for (const item of kmPend) {
    try {
      const resp = await fetch(BASE + "/api/relatorios/km", {
        method:  "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${item.token}` },
        body:    JSON.stringify(item.payload),
      });
      if (resp.ok) { await marcarEnviado(db, STORE_KM, item.offline_id); resultado.km++; }
      else { resultado.erros++; }
    } catch(e) { resultado.erros++; }
  }

  console.log(`[SW] Sync: ${resultado.fotos} fotos, ${resultado.km} km, ${resultado.erros} erros`);
  return resultado;
}
