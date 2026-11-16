// 推播用的 service worker（自選股關注 F 票）。只處理推播與點通知，不攔任何網路請求，所以不改變現有頁面與離線行為。
self.addEventListener('push', event => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = {}; }
  event.waitUntil(self.registration.showNotification(data.title || '台股決策鏡', {
    body: data.body || '', tag: data.tag || undefined, icon: '/icon-192.png', data: { url: data.url || '/' },
  }));
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  // 只開本站的頁面：通知裡的網址一律當成站內路徑。
  const path = String(event.notification.data?.url || '/');
  const url = new URL(path.startsWith('/') && !path.startsWith('//') ? path : '/', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(windows => {
    const open = windows.find(client => client.url === url && 'focus' in client);
    return open ? open.focus() : self.clients.openWindow(url);
  }));
});
