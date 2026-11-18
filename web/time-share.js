/* 分時圖（本人 2026-11-18：1 秒～15 分 K 換成券商 App 的分時，個股頁與首頁大盤共用；照 design-previews/time-share 樣品）。
   今日價格線、均價線（個股）、昨收參考虛線、與參考線之間淡淡的上紅下綠、當日最高／最低標價、每分鐘灰色量柱；
   時間軸 09:00～13:30 固定 271 格，還沒走到的留白。縱軸：個股照漲停到跌停（Intraday.limitPrices），大盤照當日高低加邊界。
   個股頁另有內外盤比與五檔（renderBook）。純函式（slots、range、flow）在 cloud/test/time-share.test.js 測。 */
(function () {
  'use strict';
  const N = 271; // 09:00 … 13:30
  const OPEN = 9 * 60;
  const finite = value => typeof value === 'number' && Number.isFinite(value);

  // ---------- 純函式 ----------
  // minutes：每分鐘一根 { t: 'YYYY-MM-DDTHH:MM…+08:00'（那一分鐘開始的時間）, close, volume（股；大盤是成交金額，單位不定）, high?, low? }。
  // 只取最後一天；沒成交的分鐘沿用上一個價、量 0；最後一筆之後留白（null）。均價＝累計（價×量）÷累計量。
  function slots(minutes) {
    const rows = (Array.isArray(minutes) ? minutes : [])
      .map(row => {
        const m = String(row?.t || '').match(/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})/);
        const close = Number(row?.close);
        if (!m || !Number.isFinite(close) || close <= 0) return null;
        return { day: m[1], index: Number(m[2]) * 60 + Number(m[3]) - OPEN, close,
          volume: Number.isFinite(Number(row.volume)) && row.volume !== null ? Math.max(0, Number(row.volume)) : 0,
          high: finite(row.high) ? row.high : close, low: finite(row.low) ? row.low : close,
          average: finite(row.average) && row.average > 0 ? row.average : null };
      })
      .filter(Boolean);
    const date = rows.length ? rows.reduce((last, row) => (row.day > last ? row.day : last), rows[0].day) : null;
    const byIndex = new Map();
    rows.filter(row => row.day === date && row.index >= 0 && row.index < N).forEach(row => byIndex.set(row.index, row));
    const last = byIndex.size ? Math.max(...byIndex.keys()) : -1;
    const out = new Array(N).fill(null);
    let price = null, pv = 0, vv = 0, high = null, low = null, given = null;
    for (let i = 0; i <= last; i += 1) {
      const row = byIndex.get(i);
      if (row) {
        price = row.close;
        pv += row.close * row.volume; vv += row.volume;
        given = row.average;
        if (!high || row.high > high.value) high = { value: row.high, index: i };
        if (!low || row.low < low.value) low = { value: row.low, index: i };
      }
      if (price === null) continue; // 開盤後第一筆之前（例如 09:00 沒成交）：不畫
      // 均價：行情商給的累計成交均價優先（沒成交的分鐘沿用上一個）；沒有才用累計（收盤×量）÷累計量近似
      out[i] = { price, volume: row ? row.volume : 0, avg: given ?? (vv ? pv / vv : price) };
    }
    return { date, slots: out, last, high, low };
  }

  // 縱軸上下緣。個股：漲停到跌停（本人 11-18 選「像券商」）；大盤：離參考價最遠的高低再多 18%，上下對稱，至少參考價的 0.5%。
  function range({ kind, ref, high, low, etf = false }) {
    if (kind === 'stock') {
      const { up, down } = window.Intraday.limitPrices(ref, { etf });
      return { top: up, bottom: down };
    }
    const far = Math.max(finite(high) ? high - ref : 0, finite(low) ? ref - low : 0);
    const span = Math.max(far * 1.18, ref * 0.005);
    return { top: Math.round((ref + span) * 100) / 100, bottom: Math.round((ref - span) * 100) / 100 };
  }

  // 內外盤：外盤＝在賣價成交（買方主動）、內盤＝在買價成交。兩邊都沒有就不出現。
  function flow(book) {
    const out = Number(book?.at_ask), inside = Number(book?.at_bid);
    if (!Number.isFinite(out) || !Number.isFinite(inside) || out + inside <= 0) return null;
    const outPct = Math.round(out / (out + inside) * 100);
    return { out, in: inside, outPct, inPct: 100 - outPct };
  }

  // 首頁大盤：即時服務（/api/live）的 series 是 { timestamp, price, open, high, low, volume_raw（成交金額，單位不定）}
  function indexMinutes(series) {
    return (Array.isArray(series) ? series : []).map(row => ({
      t: row?.timestamp, close: row?.price, high: row?.high, low: row?.low,
      volume: Number.isFinite(Number(row?.volume_raw)) && row?.volume_raw !== null ? Number(row.volume_raw) : 0,
    }));
  }

  // ---------- 畫面 ----------
  const clock = i => { const m = OPEN + i; return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`; };
  const comma = (x, d = 0) => Number(x).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const lots = shares => comma(Math.round(shares / 1000));
  const NS = 'http://www.w3.org/2000/svg';
  const svgEl = (name, attrs, text) => {
    const e = document.createElementNS(NS, name);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    return e;
  };
  const el = (tag, className, text) => {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text != null) e.textContent = text;
    return e;
  };
  // 個股價：不加千分位、最多兩位小數（和券商 App 一樣）；大盤：千分位、兩位小數
  const priceText = (model, v, fixed = false) => (model.kind === 'index' ? comma(v, 2)
    : fixed ? Number(v).toFixed(2) : String(Math.round(v * 100) / 100));

  // model：{ kind:'stock'|'index', day: slots() 的結果, ref, top, bottom }；onIndex(i) 手指／滑鼠移到哪一分鐘
  function renderChart(host, model, onIndex) {
    const { day, ref, top, bottom } = model;
    const isIndex = model.kind === 'index';
    const W = Math.max(280, host.clientWidth);
    const priceH = W > 600 ? 300 : 214, gap = 10, volH = W > 600 ? 72 : 54, axisH = 20;
    const H = priceH + gap + volH + axisH, padR = 2;
    const x = i => i / (N - 1) * (W - padR);
    const y = v => (top - v) / (top - bottom) * priceH;
    const refY = y(ref);
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'ts-svg', role: 'img',
      'aria-label': isIndex ? '加權指數分時走勢' : '分時走勢' });
    [0, 0.25, 0.75, 1].forEach(t => {
      const yy = Math.round(t * priceH) + 0.5;
      svg.append(svgEl('line', { x1: 0, x2: W, y1: yy, y2: yy, class: 'ts-grid' }));
    });
    const drawn = day.slots.map((s, i) => (s ? i : -1)).filter(i => i >= 0);
    const pts = drawn.map(i => `${x(i).toFixed(2)},${y(day.slots[i].price).toFixed(2)}`);
    if (drawn.length) {
      // 價格線與參考價之間的淡填色：上紅、下綠
      const area = `M${x(drawn[0]).toFixed(2)},${refY.toFixed(2)} L${pts.join(' L')} L${x(drawn.at(-1)).toFixed(2)},${refY.toFixed(2)} Z`;
      const uid = Math.random().toString(36).slice(2, 7);
      const defs = svgEl('defs');
      const clipUp = svgEl('clipPath', { id: `tsu-${uid}` });
      clipUp.append(svgEl('rect', { x: 0, y: 0, width: W, height: Math.max(0, refY) }));
      const clipDn = svgEl('clipPath', { id: `tsd-${uid}` });
      clipDn.append(svgEl('rect', { x: 0, y: refY, width: W, height: Math.max(0, priceH - refY) }));
      defs.append(clipUp, clipDn);
      svg.append(defs,
        svgEl('path', { d: area, class: 'ts-fill-up', 'clip-path': `url(#tsu-${uid})` }),
        svgEl('path', { d: area, class: 'ts-fill-dn', 'clip-path': `url(#tsd-${uid})` }));
    }
    svg.append(svgEl('line', { x1: 0, x2: W, y1: refY, y2: refY, class: 'ts-ref' }));
    if (!isIndex && drawn.length) svg.append(svgEl('polyline', { points: drawn.map(i => `${x(i).toFixed(2)},${y(day.slots[i].avg).toFixed(2)}`).join(' '), class: 'ts-avg' }));
    if (drawn.length) svg.append(svgEl('polyline', { points: pts.join(' '), class: 'ts-line' }));

    const taken = []; // 已經佔用的方塊，最高最低的價位不要壓到
    const chip = (val, cls, anchor) => {
      const label = priceText(model, val);
      const w = label.length * 6.7 + 10, h = 18;
      const yTop = anchor === 'top' ? 1 : anchor === 'bottom' ? priceH - h - 1 : refY - h / 2;
      const g = svgEl('g', { class: `ts-chip ${cls}` });
      g.append(svgEl('rect', { x: 0, y: yTop, width: w, height: h, rx: 4 }), svgEl('text', { x: 5, y: yTop + 12.6 }, label));
      svg.append(g);
      taken.push([0, yTop, w, yTop + h]);
    };
    chip(top, 'is-up', 'top');
    chip(ref, 'is-ref', 'mid');
    chip(bottom, 'is-dn', 'bottom');
    const pct = v => `${v >= ref ? '+' : ''}${((v - ref) / ref * 100).toFixed(2)}%`;
    svg.append(svgEl('text', { x: W - padR, y: 12, 'text-anchor': 'end', class: 'ts-pct is-up' }, pct(top)),
      svgEl('text', { x: W - padR, y: priceH - 5, 'text-anchor': 'end', class: 'ts-pct is-dn' }, pct(bottom)));
    taken.push([W - 56, 0, W, 16], [W - 56, priceH - 16, W, priceH]);

    // 最高、最低：一小點＋價位，價位找不壓到價格線與價格籤的位置
    const hits = b => taken.some(t => b[0] < t[2] && b[2] > t[0] && b[1] < t[3] && b[3] > t[1]) ||
      drawn.some(i => { const px = x(i), py = y(day.slots[i].price); return px >= b[0] - 1 && px <= b[2] + 1 && py >= b[1] - 2 && py <= b[3] + 2; }) ||
      b[0] < 0 || b[2] > W || b[1] < 0 || b[3] > priceH;
    const mark = (point, kind) => {
      if (!point) return;
      const px = x(point.index), py = y(point.value);
      const label = priceText(model, point.value);
      const tw = label.length * 6.6, th = 12, v = kind === 'hi' ? -1 : 1, cy = py + v * 13;
      const tries = [[px - tw / 2, cy], [px + 8, py], [px - 8 - tw, py], [px + 4, cy], [px - 4 - tw, cy], [px + 18, cy], [px - 18 - tw, cy],
        [px - tw / 2, py - v * 13], [px + 8, py - v * 13], [px - 8 - tw, py - v * 13]];
      // 貼著價格籤（點就在籤旁邊）的話，往籤的右邊讓；離得遠的籤不拿來當退路，免得價位跑離那一點
      taken.filter(t => px - t[2] < 40 && px >= t[0]).forEach(t => { tries.push([t[2] + 6, py], [t[2] + 6, py - v * 6], [t[2] + 6, cy]); });
      const pick = tries.find(([lx, ly]) => !hits([lx, ly - th / 2, lx + tw, ly + th / 2])) || tries[0];
      const g = svgEl('g', { class: `ts-mark ts-mark-${kind}` });
      g.append(svgEl('circle', { cx: px, cy: py, r: 2.6 }), svgEl('text', { x: pick[0], y: pick[1] + 3.8 }, label));
      svg.append(g);
      taken.push([pick[0], pick[1] - th / 2, pick[0] + tw, pick[1] + th / 2]);
    };
    mark(day.high, 'hi');
    mark(day.low, 'lo');

    // 每分鐘的量（中性灰）。開盤、收盤兩根集合競價常常特別大，最高刻度不算它們、柱子頂到上緣為止。
    const vTop = priceH + gap;
    const middle = drawn.filter(i => i > 0 && i < N - 1).map(i => day.slots[i].volume);
    const vMax = Math.max(1, ...(middle.length ? middle : drawn.map(i => day.slots[i].volume))) * 1.05;
    const bw = Math.max(1, (W - padR) / N * 0.62);
    const bars = svgEl('g', { class: 'ts-vol' });
    drawn.forEach(i => {
      const v = day.slots[i].volume;
      if (!v) return;
      const h = Math.max(1, Math.min(1, v / vMax) * volH);
      bars.append(svgEl('rect', { x: (x(i) - bw / 2).toFixed(2), y: (vTop + volH - h).toFixed(2), width: bw.toFixed(2), height: h.toFixed(2) }));
    });
    svg.append(bars,
      svgEl('line', { x1: 0, x2: W, y1: vTop + volH + 0.5, y2: vTop + volH + 0.5, class: 'ts-grid' }),
      svgEl('text', { x: 0, y: vTop + 9, class: 'ts-vlabel' }, isIndex ? '成交金額' : '量（張）'));
    [[0, '09:00', 'start'], [60, '10:00', 'middle'], [120, '11:00', 'middle'], [180, '12:00', 'middle'], [270, '13:30', 'end']]
      .forEach(([i, t, a]) => svg.append(svgEl('text', { x: x(i), y: H - 5, 'text-anchor': a, class: 'ts-time' }, t)));

    // 手指／滑鼠移到哪一分鐘，上方那一行就換成那一分鐘（只到最後一筆為止）
    if (drawn.length) {
      const cross = svgEl('g', { class: 'ts-cross', visibility: 'hidden' });
      const cl = svgEl('line', { y1: 0, y2: vTop + volH });
      const cd = svgEl('circle', { r: 3.2 });
      cross.append(cl, cd);
      svg.append(cross);
      const at = ev => {
        const box = svg.getBoundingClientRect();
        const want = Math.round((ev.clientX - box.left) / (W - padR) * (N - 1));
        const i = drawn.reduce((best, k) => (Math.abs(k - want) < Math.abs(best - want) ? k : best), drawn[0]);
        cl.setAttribute('x1', x(i)); cl.setAttribute('x2', x(i));
        cd.setAttribute('cx', x(i)); cd.setAttribute('cy', y(day.slots[i].price));
        cross.setAttribute('visibility', 'visible');
        onIndex(i);
      };
      svg.addEventListener('pointermove', at);
      svg.addEventListener('pointerdown', at);
      svg.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); onIndex(day.last); });
    }
    host.replaceChildren(svg);
  }

  // 上方一行：時間 價 漲跌 均 量（大盤：時間 指數 漲跌；成交金額的單位不確定，不寫每分鐘的數字）
  function quoteLine(host, model, i) {
    const s = model.day.slots[i];
    host.replaceChildren();
    if (!s) return;
    const isIndex = model.kind === 'index';
    const diff = s.price - model.ref, pct = diff / model.ref * 100;
    const dir = diff > 0 ? 'up' : diff < 0 ? 'down' : 'flat';
    const pair = (label, value, labelClass) => {
      const span = el('span');
      span.append(el('i', labelClass || '', label), el('b', 'num', value));
      return span;
    };
    host.append(el('span', 'ts-q-time num', clock(i)), pair(isIndex ? '指數' : '價', priceText(model, s.price, true)),
      el('span', `num ts-q-chg is-${dir}`, `${diff > 0 ? '▲' : diff < 0 ? '▼' : ''}${isIndex ? comma(Math.abs(diff), 2) : Math.abs(diff).toFixed(2)}(${Math.abs(pct).toFixed(2)}%)`));
    if (!isIndex) host.append(pair('均', s.avg.toFixed(2), 'ts-q-avg'), pair('量', lots(s.volume)));
  }

  // 開 高 低 總量（個股，張）；大盤只寫開高低
  function ohlcLine(host, model) {
    const { open, high, low, total } = model;
    host.replaceChildren();
    const pair = (label, value) => {
      if (!finite(value)) return;
      const span = el('span');
      span.append(el('i', '', label), el('b', 'num', model.kind === 'index' ? comma(value, 2) : priceText(model, value)));
      host.append(span);
    };
    pair('開', open); pair('高', high); pair('低', low);
    if (model.kind !== 'index' && finite(total)) {
      const span = el('span');
      span.append(el('i', '', '總量'), el('b', 'num', lots(total)));
      host.append(span);
    }
  }

  // 掛到一個容器上：[data-ts-quote]、[data-ts-ohlc]、[data-ts-chart]。寬度變了就重畫。
  const mounted = new WeakMap();
  function render(root, model) {
    const host = root.querySelector('[data-ts-chart]');
    const quote = root.querySelector('[data-ts-quote]');
    const ohlc = root.querySelector('[data-ts-ohlc]');
    const draw = () => renderChart(host, model, i => quoteLine(quote, model, i));
    quoteLine(quote, model, model.day.last);
    ohlcLine(ohlc, model);
    draw();
    let entry = mounted.get(root);
    if (!entry && typeof ResizeObserver === 'function') {
      entry = { width: host.clientWidth, draw };
      entry.observer = new ResizeObserver(() => { if (host.clientWidth && host.clientWidth !== entry.width) { entry.width = host.clientWidth; entry.draw(); } });
      entry.observer.observe(host);
      mounted.set(root, entry);
    }
    if (entry) { entry.draw = draw; entry.width = host.clientWidth; }
  }

  // ---------- 內外盤比、五檔（只有個股頁） ----------
  // book：{ time, bids:[[價, 張]…], asks:[[價, 張]…], flow:{ at_ask, at_bid } | null }
  function renderBook(root, book) {
    const flowBox = root.querySelector('[data-ts-flow]');
    const bookBox = root.querySelector('[data-ts-book]');
    const f = flow(book?.flow);
    flowBox.hidden = !f;
    if (f) {
      flowBox.querySelector('[data-flow-out]').replaceChildren('外盤', el('b', '', `${f.outPct}%`), el('small', 'num', comma(f.out)));
      flowBox.querySelector('[data-flow-in]').replaceChildren(el('small', 'num', comma(f.in)), el('b', '', `${f.inPct}%`), '內盤');
      const bar = flowBox.querySelector('[data-flow-bar]');
      bar.setAttribute('aria-label', `外盤 ${f.outPct}%，內盤 ${f.inPct}%`);
      bar.children[0].style.flex = String(f.outPct);
      bar.children[1].style.flex = String(f.inPct);
    }
    const bids = (book?.bids || []).slice(0, 5), asks = (book?.asks || []).slice(0, 5);
    bookBox.hidden = !bids.length && !asks.length;
    if (bookBox.hidden) return;
    bookBox.querySelector('[data-book-time]').textContent = book.time ? String(book.time).slice(11, 16) : '';
    const max = Math.max(1, ...bids.map(b => b[1]), ...asks.map(a => a[1]));
    const sum = list => list.reduce((s, [, v]) => s + v, 0);
    const px = p => priceText({ kind: 'stock' }, p);
    const row = (p, v, side) => {
      const li = el('li');
      const bar = el('i', 'ts-bar');
      bar.style.setProperty('--w', (v / max).toFixed(3));
      li.append(bar);
      if (side === 'bid') li.append(el('span', 'num ts-vol-n', comma(v)), el('span', 'num ts-px', px(p)));
      else li.append(el('span', 'num ts-px', px(p)), el('span', 'num ts-vol-n', comma(v)));
      return li;
    };
    const total = (list, side) => {
      const li = el('li', 'ts-sum');
      const n = el('span', 'num ts-vol-n', comma(sum(list)));
      if (side === 'bid') li.append(n, el('span', '', '合計')); else li.append(el('span', '', '合計'), n);
      return li;
    };
    bookBox.querySelector('[data-book-bid]').replaceChildren(...bids.map(([p, v]) => row(p, v, 'bid')), total(bids, 'bid'));
    bookBox.querySelector('[data-book-ask]').replaceChildren(...asks.map(([p, v]) => row(p, v, 'ask')), total(asks, 'ask'));
  }

  window.TimeShare = { N, slots, range, flow, indexMinutes, render, renderBook };
})();
