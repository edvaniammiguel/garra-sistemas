// Service Worker — Garra Jardinagem PWA v2
// Arquivos PWA: pwa-login.html + pwa-app.html
const CACHE = "garra-jardinagem-v2";

const ASSETS = [
  "./pwa-login.html",
  "./pwa-app.html",
  "./manifest.json",
  "./icons/logo-Garra-e-ca%C3%A7ambas.png",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/favicon-32.png"
];

// Install — cachear assets
self.addEventListener("install", function(e){
  e.waitUntil(
    caches.open(CACHE).then(function(c){ return c.addAll(ASSETS); })
  );
  self.skipWaiting();
});

// Activate — limpar caches antigos
self.addEventListener("activate", function(e){
  e.waitUntil(
    caches.keys().then(function(keys){
      return Promise.all(
        keys.filter(function(k){ return k !== CACHE; })
            .map(function(k){ return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

// Fetch — cache first para assets, network first para API
self.addEventListener("fetch", function(e){
  var url = e.request.url;

  // API calls: sempre network, sem cache
  if(url.includes("garra-sistemas.onrender.com")){
    e.respondWith(
      fetch(e.request).catch(function(){
        return new Response(JSON.stringify({erro:"Sem conexão"}),
          {headers:{"Content-Type":"application/json"}});
      })
    );
    return;
  }

  // Assets estáticos: cache first
  e.respondWith(
    caches.match(e.request).then(function(cached){
      return cached || fetch(e.request).then(function(response){
        if(response.ok){
          var clone = response.clone();
          caches.open(CACHE).then(function(c){ c.put(e.request, clone); });
        }
        return response;
      });
    })
  );
});
