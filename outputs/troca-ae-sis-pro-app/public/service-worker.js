const CACHE_NAME = 'troca-ae-sis-pro-v40';
const ASSETS = ['/', '/index.html', '/customer-portal.html', '/manifest.json', '/troca-ae-logo.jpg'];

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS)));
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/uploads/')) return;
  if (event.request.mode === 'navigate') {
    const fallback = url.pathname.startsWith('/portal/') ? '/customer-portal.html' : '/index.html';
    event.respondWith(fetch(event.request).catch(() => caches.match(fallback)));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request)));
});
