const CACHE_NAME = 'plan-reminder-v1';
const ASSETS_TO_CACHE = [
    '/',
    '/index.html',
    '/style.css',
    '/app.js',
    '/api.js', 
    '/ai-chat.js'
];

// Install Event - cache static files
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[Service Worker] Caching app shell');
                // Use addAll, but gracefully handle if some files fail
                return Promise.allSettled(
                    ASSETS_TO_CACHE.map(url => cache.add(url).catch(err => console.log(`Failed to cache ${url}:`, err)))
                );
            })
            .then(() => self.skipWaiting())
    );
});

// Activate Event - cleanup old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('[Service Worker] Deleting old cache', cacheName);
                        return caches.delete(cacheName);
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch Event - Network First, fallback to cache
self.addEventListener('fetch', (event) => {
    // Only handle GET requests
    if (event.request.method !== 'GET') return;

    event.respondWith(
        fetch(event.request)
            .then((networkResponse) => {
                // Return fresh data from network
                
                // If it's a valid response from our origin (not an API call), cache it for future offline use
                if (networkResponse && networkResponse.status === 200 && networkResponse.type === 'basic') {
                    const responseClone = networkResponse.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseClone);
                    });
                }
                return networkResponse;
            })
            .catch(async () => {
                // Network failed (offline), notify clients and fallback to Cache
                const clients = await self.clients.matchAll();
                clients.forEach(client => {
                    client.postMessage({ type: 'OFFLINE_MODE', url: event.request.url });
                });

                return caches.match(event.request).then((cachedResponse) => {
                    if (cachedResponse) {
                        return cachedResponse;
                    }
                    // If not in cache and offline, we could return a custom offline page here.
                    // But for the mini-app, the cached index.html works.
                    return caches.match('/index.html');
                });
            })
    );
});
