/* ============================================================
   Garra Jardinagem — Service Worker
   Estratégia: Cache First para assets, Network First para API
   Fila offline: IndexedDB para fotos tiradas sem internet
   ============================================================ */

const CACHE_NAME   = "garra-jardinagem-v1";
const OFFLINE_URLS = ["/mobile", "/static/css/mobile.css", "/static/js/mobile.js", "/manifest.json"];
const DB_NAME      = "garra-offline-db";
const STORE_FOTOS  = "fila_fotos";

// ── INSTALL — cacheia assets essenciais ──────────────────────
self.addEventListener("install", event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(OFFLINE_URLS))
  );
  self.skipWaiting();
});

// ── ACTIVATE — limpa caches antigos ──────────────────────────
self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── FETCH ─────────────────────────────────────────────────────
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);

  // API: Network First, sem cache
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(JSON.stringify({ erro: "Sem conexão" }), {
          headers: { "Content-Type": "application/json" }
        })
      )
    );
    return;
  }

  // Assets: Cache First
  event.respondWith(
    caches.match(event.request).then(cached =>
      cached || fetch(event.request).then(resp => {
        // Cacheia novos assets
        if (resp.ok && event.request.method === "GET") {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
        }
        return resp;
      })
    )
  );
});

// ── SYNC — processa fila offline quando conexão volta ─────────
self.addEventListener("sync", event => {
  if (event.tag === "sync-fotos") {
    event.waitUntil(processarFila());
  }
});

// ── MENSAGENS do frontend ────────────────────────────────────
self.addEventListener("message", event => {
  if (event.data?.tipo === "SYNC_AGORA") {
    processarFila().then(() => {
      self.clients.matchAll().then(clients =>
        clients.forEach(c => c.postMessage({ tipo: "SYNC_CONCLUIDO" }))
      );
    });
  }
});

// ── IndexedDB helpers ─────────────────────────────────────────
function abrirDB() {
  return new Promise((res, rej) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = e => {
      const db = e.target.result;
      if (!db.objectStoreNames.contains(STORE_FOTOS)) {
        const store = db.createObjectStore(STORE_FOTOS, { keyPath: "offline_id" });
        store.createIndex("enviado", "enviado", { unique: false });
      }
    };
    req.onsuccess = e => res(e.target.result);
    req.onerror   = e => rej(e.target.error);
  });
}

function lerPendentes(db) {
  return new Promise((res, rej) => {
    const tx    = db.transaction(STORE_FOTOS, "readonly");
    const store = tx.objectStore(STORE_FOTOS);
    const idx   = store.index("enviado");
    const req   = idx.getAll(false);
    req.onsuccess = e => res(e.target.result);
    req.onerror   = e => rej(e.target.error);
  });
}

function marcarEnviado(db, offline_id) {
  return new Promise((res, rej) => {
    const tx    = db.transaction(STORE_FOTOS, "readwrite");
    const store = tx.objectStore(STORE_FOTOS);
    const req   = store.get(offline_id);
    req.onsuccess = e => {
      const item = e.target.result;
      if (item) { item.enviado = true; store.put(item); }
      res();
    };
    req.onerror = e => rej(e.target.error);
  });
}

// ── PROCESSA FILA ─────────────────────────────────────────────
async function processarFila() {
  let db;
  try {
    db = await abrirDB();
    const pendentes = await lerPendentes(db);
    if (!pendentes.length) return;

    // Lê token do cookie (não disponível no SW, usa header guardado)
    for (const item of pendentes) {
      try {
        const form = new FormData();
        // Reconstrói Blob a partir do base64 salvo
        const bytes = Uint8Array.from(atob(item.dados_b64), c => c.charCodeAt(0));
        const blob  = new Blob([bytes], { type: "image/jpeg" });
        form.append("foto",       blob, item.filename);
        form.append("semana_id",  item.semana_id || "ativa");
        form.append("local_nome", item.local_nome || "");
        form.append("tipo",       item.tipo);
        form.append("offline_id", item.offline_id);

        const resp = await fetch("/api/fotos/mobile", {
          method:  "POST",
          body:    form,
          headers: { "Authorization": `Bearer ${item.token}` }
        });

        if (resp.ok) {
          await marcarEnviado(db, item.offline_id);
          console.log(`[SW] Foto enviada: ${item.offline_id}`);
        }
      } catch (err) {
        console.warn(`[SW] Falha ao enviar ${item.offline_id}:`, err);
      }
    }
  } catch (err) {
    console.error("[SW] Erro ao processar fila:", err);
  }
}
