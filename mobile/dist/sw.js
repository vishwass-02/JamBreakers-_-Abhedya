// ABHEDYA phone app service worker: app shell cache-first; live data (/ws, /api) always from the laptop.
const CACHE = 'aegis-app-mwrcaj75';
const FILES = ["./","./abhedya_logo.png","./abhedya_logo_dark.png","./assets/index-6YAaRV8R.css","./assets/index-CjSKMkUH.js","./icons/apple-touch-icon.png","./icons/icon-192.png","./icons/icon-512.png","./icons/maskable-512.png","./index.html","./manifest.webmanifest"];
self.addEventListener('install', (e) => { e.waitUntil(caches.open(CACHE).then((c) => c.addAll(FILES)).then(() => self.skipWaiting())); });
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k.startsWith('aegis-app-') && k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || !u.pathname.startsWith('/app/')) return;      // /api and /ws go to the network
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).then((hit) => hit || fetch(e.request).catch(() => caches.match('./'))));
});
