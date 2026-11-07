// 雲端資料來源（票 06）：登入後從後端 API 取資料，介面與本機 market-data-local.js 相同。
// 登入過期時 Access 會回轉址或 HTML，不是 JSON；一律先檢查再解析，不無限重試。
(() => {
  'use strict';
  class MarketDataError extends Error {
    constructor(message, kind) { super(message); this.kind = kind; }
  }
  let version = '';
  const inflight = new Map();
  const cache = new Map();

  async function getJson(url) {
    let response;
    try {
      response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, redirect: 'manual' });
    } catch {
      throw new MarketDataError('連線失敗，請稍後再試。', 'network');
    }
    const type = response.headers.get('content-type') || '';
    if (response.type === 'opaqueredirect' || response.status === 401 || (response.ok && !type.includes('application/json'))) {
      throw new MarketDataError('登入已失效，請重新整理頁面再登入。', 'auth');
    }
    if (response.status === 403) throw new MarketDataError('帳號尚未核准或已停用。', 'forbidden');
    if (response.status === 409) throw new MarketDataError('資料已更新，請重新整理頁面。', 'stale');
    if (response.status === 429) throw new MarketDataError('查詢太頻繁，請稍等一分鐘再試。', 'rate');
    if (!response.ok) throw new MarketDataError('暫時無法取得資料，請稍後再試。', 'server');
    return response.json();
  }

  window.MarketData = {
    source: 'cloud',
    MarketDataError,
    async overview() {
      const data = await getJson('/api/market/overview');
      version = data.version;
      return data;
    },
    // 同一張卡同一天：成功才進快取；並行請求合併成一個。
    detail(kind, date) {
      const key = `${kind}|${date}`;
      if (cache.has(key)) return Promise.resolve(cache.get(key));
      if (inflight.has(key)) return inflight.get(key);
      const url = `/api/market/detail?kind=${encodeURIComponent(kind)}&date=${encodeURIComponent(date)}&v=${encodeURIComponent(version)}`;
      const job = getJson(url)
        .then(body => { cache.set(key, body.tables); return body.tables; })
        .finally(() => inflight.delete(key));
      inflight.set(key, job);
      return job;
    }
  };
})();
