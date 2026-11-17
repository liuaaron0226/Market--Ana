// 自選股頁的「到價推播」（自選股關注 F 票）：站主在每一台裝置上自己打開或關掉、發一則測試通知、看已登記的裝置。
// 只有站主、而且關注開關打開時 /api/watch/push 才有內容；其他人這一塊一直藏著，不發任何推播請求以外的東西。
// 本人 2026-11-17「你把它放在我的庫存有點奇怪且占空間，我覺得你可以把他隱藏」→ 同日選設定樣品 A：
// 畫面收進三頁頁首齒輪的設定浮板「通知」那一籤（settings.js 呼叫這裡的 mount）。
(function () {
  const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const standalone = () => window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;
  const supported = () => 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;

  function deviceLabel() {
    const ua = navigator.userAgent;
    const os = /iPhone/.test(ua) ? 'iPhone' : /iPad/.test(ua) || isIos() ? 'iPad' : /Android/.test(ua) ? 'Android'
      : /Mac OS X/.test(ua) ? 'Mac' : /Windows/.test(ua) ? 'Windows' : '裝置';
    const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : '';
    return isIos() && standalone() ? `${os} 主畫面` : [os, browser].filter(Boolean).join(' ');
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      cache: 'no-store', method: options.method || 'GET',
      headers: options.body ? { 'content-type': 'application/json' } : undefined,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || payload.error || '暫時無法處理');
    return payload;
  }

  const keyBytes = (base64url) => {
    const text = atob(base64url.replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(text, c => c.charCodeAt(0));
  };
  // 這台瀏覽器的訂閱是不是綁現在的推播公開金鑰（本人 11-17 換過金鑰之後，舊訂閱送不到，要重新訂閱）。
  // 讀不到綁哪一把時（少數瀏覽器）當成相同，照原本的做法。
  const sameServerKey = (subscription, publicKey) => {
    const bound = subscription?.options?.applicationServerKey;
    if (!bound) return true;
    const want = keyBytes(publicKey);
    const have = new Uint8Array(bound);
    return have.length === want.length && have.every((byte, i) => byte === want[i]);
  };
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const day = (iso) => {
    const date = new Date(iso);
    return Number.isFinite(date.getTime()) ? `${date.getMonth() + 1}/${date.getDate()}` : '';
  };
  const clock = (iso) => new Date(iso).toLocaleTimeString('zh-TW', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Taipei' });

  // 雲端盯價最後一次的狀態：只講今天，用量接近額度或報價失敗時講明。
  function watcherText(watcher) {
    if (!watcher?.at) return '';
    const today = new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Taipei' });
    if (new Date(watcher.at).toLocaleDateString('en-CA', { timeZone: 'Asia/Taipei' }) !== today) return '';
    if (watcher.status === 'error') return `雲端盯價 ${clock(watcher.at)}：${watcher.error}`;
    if (watcher.closed_today) return '雲端盯價：今天休市';
    const parts = [`雲端盯價 ${clock(watcher.at)}：查了 ${watcher.checked} 檔`];
    if (watcher.pending) parts.push(`${watcher.pending} 檔下一分鐘接著查`);
    if (watcher.rate_limited) parts.push('報價用量到上限，放慢');
    if (watcher.errors) parts.push(`${watcher.errors} 檔報價沒拿到`);
    return parts.join('，');
  }

  // 畫出設定；推播可用時回 true（不是站主、開關關著或讀不到時回 false，什麼都不畫）。
  async function mount(host) {
    if (!host) return false;
    let info;
    try { info = await api('/api/watch/push'); } catch (_) { return false; }
    if (!info?.available) return false;
    host.hidden = false;
    host.replaceChildren();
    const status = el('p', 'wp-status');
    status.setAttribute('role', 'status');
    const body = el('div', 'wp-body');
    const devices = el('ul', 'wp-devices');
    const watcher = el('p', 'wp-watcher', watcherText(info.watcher));
    host.append(body, status, devices, watcher);
    const say = (message, kind = '') => { status.textContent = message; if (kind) status.dataset.state = kind; else status.removeAttribute('data-state'); };

    let registration = null;
    let mine = null;
    if (supported() && info.configured) {
      try {
        registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
        mine = await registration.pushManager.getSubscription();
        // 綁的是舊金鑰：先把舊的退訂、從清單拿掉，畫面顯示「沒打開」，勾回來就用新金鑰訂閱。
        if (mine && !sameServerKey(mine, info.public_key)) {
          const stale = mine.endpoint;
          await api('/api/watch/push', { method: 'DELETE', body: { endpoint: stale } }).catch(() => {});
          await mine.unsubscribe().catch(() => {});
          mine = null;
          info.devices = info.devices.filter(device => device.endpoint !== stale);
        }
      } catch (_) { registration = null; }
    }
    const registered = mine && info.devices.some(device => device.endpoint === mine.endpoint);

    if (!info.configured) {
      body.append(el('p', 'wp-note', '推播金鑰還沒設定，設定好之後這裡就能打開。'));
    } else if (isIos() && !standalone()) {
      body.append(el('p', 'wp-note', 'iPhone 要先用 Safari 的「分享 → 加入主畫面」，再從主畫面的圖示打開這一頁，才打得開推播。'));
    } else if (!supported() || !registration) {
      body.append(el('p', 'wp-note', '這個瀏覽器不支援網頁推播。'));
    } else {
      const toggle = el('label', 'pf-watch-toggle wp-toggle');
      const input = el('input');
      input.type = 'checkbox';
      input.checked = Boolean(registered);
      toggle.append(input, el('span', '', '這台裝置接收到價通知'));
      body.append(toggle);
      if (registered) {
        const test = el('button', 'wp-test', '發測試通知');
        test.type = 'button';
        test.addEventListener('click', async () => {
          test.disabled = true;
          try {
            await api('/api/watch/push/test', { method: 'POST', body: { endpoint: mine.endpoint } });
            say('已送出，幾秒內這台會收到一則測試通知。', 'success');
          } catch (error) {
            // 失效的訂閱已在雲端移除：先重畫成「沒打開」，再把原因顯示在新的畫面上。
            await mount(host);
            host.querySelector('.wp-status').textContent = error.message;
            host.querySelector('.wp-status').dataset.state = 'error';
          }
          finally { test.disabled = false; }
        });
        body.append(test);
      }
      input.addEventListener('change', async () => {
        input.disabled = true;
        try {
          if (input.checked) {
            if (await Notification.requestPermission() !== 'granted') throw new Error('沒有允許通知；要在系統設定裡打開這個網站的通知。');
            const subscription = mine || await registration.pushManager.subscribe({
              userVisibleOnly: true, applicationServerKey: keyBytes(info.public_key),
            });
            await api('/api/watch/push', { method: 'POST', body: { subscription: subscription.toJSON(), label: deviceLabel() } });
            say('這台裝置已打開推播。', 'success');
          } else {
            if (mine) {
              await api('/api/watch/push', { method: 'DELETE', body: { endpoint: mine.endpoint } });
              await mine.unsubscribe().catch(() => {});
            }
            say('這台裝置已關掉推播。', 'success');
          }
          await mount(host);
        } catch (error) {
          input.checked = !input.checked;
          input.disabled = false;
          say(error.message, 'error');
        }
      });
    }

    // 已登記的每一台裝置，可以逐台關掉（例如手機不在身邊時從電腦關）。
    for (const device of info.devices) {
      const item = el('li', 'wp-device');
      const self = mine && device.endpoint === mine.endpoint;
      const name = el('span', 'wp-device-name', `${device.label}${self ? '（這台）' : ''}`);
      const meta = el('span', 'wp-device-meta', device.last_error ? '上次沒送到' : `${day(device.created_at)} 打開`);
      const off = el('button', 'pf-delete', '關掉');
      off.type = 'button';
      off.setAttribute('aria-label', `關掉 ${device.label} 的推播`);
      off.addEventListener('click', async () => {
        off.disabled = true;
        try {
          await api('/api/watch/push', { method: 'DELETE', body: { endpoint: device.endpoint } });
          if (self) await mine.unsubscribe().catch(() => {});
          await mount(host);
        } catch (error) { off.disabled = false; say(error.message, 'error'); }
      });
      item.append(name, meta, off);
      devices.append(item);
    }
    // 在浮層裡重畫（切換開關、測試通知失敗、逐台關掉之後）：原本有焦點的鍵被換掉了，焦點放回浮層裡第一個能按的東西，鍵盤操作不中斷
    if (host.closest?.('dialog[open]') && (!document.activeElement || document.activeElement === document.body)) host.querySelector('input, button')?.focus();
    return true;
  }

  window.WatchPush = { mount, _logic: { watcherText } };
})();
