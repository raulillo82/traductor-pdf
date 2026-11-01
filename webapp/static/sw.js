// Service worker: solo recibe avisos push y abre la traducción al tocarlos.
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { /* aviso sin datos */ }
  event.waitUntil(self.registration.showNotification(data.title || 'Traductor de patrones', {
    body: data.body || '',
    data: { url: data.url || self.registration.scope },
    tag: 'traduccion',
  }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || self.registration.scope;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((wins) => {
    for (const w of wins) {
      if ('focus' in w) { w.navigate(url); return w.focus(); }
    }
    return self.clients.openWindow(url);
  }));
});
