/* 個股頁「國外同類股」（自選股關注 B 票；規格 .scratch/watchlist-watch/spec.md）。
   只有站主、而且這一檔打開了關注時才出現；其他人拿到「沒有內容」，整塊不出現。
   數字都由 /api/watch-peers 算好（最近一天漲跌、近期收盤、狀態），這裡只排版。出錯只讓這一塊消失。
   移除：手機向左滑出「移除」；有滑鼠的裝置懸停或鍵盤聚焦時看得到同一顆。 */
(function () {
  'use strict';
  const MARKET = { US: '美股', KR: '韓股', INDEX: '指數', JP: '日股' };
  const SOURCE = { massive: 'Massive', eodhd: 'EODHD' };
  // 狀態標籤：正常（ok）不標。
  const TAG = { stale: '過期', no_source: '沒有來源', pending: '等每日更新' };
  const SWIPE_PX = 48;
  const state = { code: '', token: 0 };

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const pct = value => value === null || value === undefined ? '—'
    : value === 0 ? '0.00%' : `${value > 0 ? '+' : ''}${Number(value).toFixed(2)}%`;
  const shortDate = iso => iso ? `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}` : '';
  const host = () => document.getElementById('watchPeers');
  const hide = () => {
    const node = host();
    if (node) { node.hidden = true; node.replaceChildren(); }
  };

  // 近期收盤的折線：只畫形狀，最後一點用當天漲跌的顏色。
  function sparkline(closes, change) {
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('class', 'wp-spark');
    svg.setAttribute('viewBox', '0 0 100 30');
    svg.setAttribute('preserveAspectRatio', 'none');
    svg.setAttribute('aria-hidden', 'true');
    const values = closes.map(row => Number(row[1])).filter(Number.isFinite);
    if (values.length < 2) return svg;
    const min = Math.min(...values);
    const span = Math.max(...values) - min || 1;
    const points = values.map((value, i) => [i / (values.length - 1) * 100, 27 - (value - min) / span * 24]);
    const line = document.createElementNS(svg.namespaceURI, 'polyline');
    line.setAttribute('points', points.map(p => p.map(n => n.toFixed(2)).join(',')).join(' '));
    line.setAttribute('vector-effect', 'non-scaling-stroke');
    const [x, y] = points[points.length - 1];
    const dot = document.createElementNS(svg.namespaceURI, 'circle');
    dot.setAttribute('cx', x.toFixed(2));
    dot.setAttribute('cy', y.toFixed(2));
    dot.setAttribute('r', '2.2');
    dot.setAttribute('class', change > 0 ? 'up' : change < 0 ? 'down' : '');
    svg.append(line, dot);
    return svg;
  }

  async function send(method, path, body) {
    const response = await fetch(path, {
      method, cache: 'no-store', credentials: 'same-origin',
      headers: body ? { 'content-type': 'application/json', Accept: 'application/json' } : { Accept: 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.error || payload.message || '暫時無法處理，請稍後再試');
    return payload;
  }

  function row(item, code, message) {
    const li = el('li', 'wp-row');
    li.dataset.symbol = item.symbol;
    li.tabIndex = 0;
    const name = el('div', 'wp-name');
    name.append(el('b', '', item.name), el('small', 'num', `${item.symbol.replace(/\.(US|INDX)$/, '')} · ${MARKET[item.market] || ''}`));
    const change = item.latest?.change_pct ?? null;
    const value = el('div', 'wp-value');
    const tick = el('i', `wp-tick${change > 0 ? ' up' : change < 0 ? ' down' : ''}`);
    tick.setAttribute('aria-hidden', 'true');
    tick.hidden = !item.latest;
    value.append(tick, el('b', 'num', item.latest ? pct(change) : '—'));
    const meta = el('small', 'wp-meta num');
    if (item.latest) meta.append(document.createTextNode(`${shortDate(item.latest.date)} 收`));
    if (TAG[item.status]) meta.append(el('span', `wp-tag wp-tag-${item.status}`, TAG[item.status]));
    value.append(meta);
    const remove = el('button', 'wp-remove', '移除');
    remove.type = 'button';
    remove.setAttribute('aria-label', `從名單移除 ${item.name}`);
    remove.addEventListener('click', async () => {
      remove.disabled = true;
      try {
        await send('DELETE', `/api/watch-peers/${code}/${encodeURIComponent(item.symbol)}`);
        state.code = '';
        load(code);
      } catch (error) {
        remove.disabled = false;
        message.textContent = error.message;
      }
    });
    li.append(name, sparkline(item.closes || [], change), value, remove);
    li.addEventListener('keydown', event => {
      if (event.target === li && (event.key === 'Delete' || event.key === 'Backspace')) remove.click();
    });
    // 手機：向左滑出「移除」，向右滑收回。
    let startX = null;
    li.addEventListener('pointerdown', event => { if (event.pointerType !== 'mouse') startX = event.clientX; });
    li.addEventListener('pointerup', event => {
      if (startX === null) return;
      const dx = event.clientX - startX;
      startX = null;
      if (dx < -SWIPE_PX) li.dataset.reveal = 'true';
      else if (dx > SWIPE_PX) delete li.dataset.reveal;
    });
    li.addEventListener('pointercancel', () => { startX = null; });
    return li;
  }

  function render(payload, code) {
    const node = host();
    if (!node) return;
    const head = el('header', 'wp-head');
    head.append(el('h2', '', '國外同類股'));
    if (payload.limited_count) head.append(el('span', 'wp-count num', `韓股與指數 ${payload.limited_count}/${payload.limited_limit}`));
    const message = el('p', 'wp-message');
    message.setAttribute('role', 'status');
    const children = [head];
    if (payload.items.length) {
      const list = el('ul', 'wp-list');
      for (const item of payload.items) list.append(row(item, code, message));
      children.push(list);
      const used = [...new Set(payload.items.map(item => item.source).filter(Boolean))];
      if (used.length) children.push(el('p', 'wp-source', `出處 ${used.map(source => SOURCE[source]).join('、')}`));
    } else {
      children.push(el('p', 'wp-empty', '還沒有名單'));
    }
    const form = el('form', 'wp-add');
    const input = el('input');
    input.name = 'symbol';
    input.autocomplete = 'off';
    input.maxLength = 16;
    input.placeholder = 'NVDA、005930.KO';
    input.setAttribute('aria-label', '國外股票代號');
    const button = el('button', '', '加入');
    button.type = 'submit';
    form.append(input, button);
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const symbol = input.value.trim();
      if (!symbol) return;
      button.disabled = true;
      message.textContent = '';
      try {
        await send('POST', `/api/watch-peers/${code}`, { symbol });
        state.code = '';
        await load(code);
      } catch (error) {
        message.textContent = error.message;
        button.disabled = false;
      }
    });
    children.push(form, message);
    node.replaceChildren(...children);
    node.hidden = false;
  }

  // 同一檔只讀一次（個股頁盤中每幾秒重畫，不跟著重讀）。
  async function load(code) {
    if (!code || code === state.code) return;
    state.code = code;
    const token = ++state.token;
    if (host()?.dataset.code !== code) hide();   // 換了一檔：先收起上一檔的名單；同一檔重讀時不閃
    if (host()) host().dataset.code = code;
    try {
      const payload = await send('GET', `/api/watch-peers/${code}`);
      if (token !== state.token) return;
      if (!payload?.available || !payload.watching || !Array.isArray(payload.items)) return hide();
      render(payload, code);
    } catch (_) {
      if (token === state.token) hide();
    }
  }

  window.WatchPeers = { load };
})();
