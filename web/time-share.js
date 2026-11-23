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

  // ---------- 海外大盤的分時（本人 2026-11-24）：/api/indices/<key>/intraday 的 5 分鐘線 ----------
  // 時間一律換成台北時間（UTC+8，沒有日光節約）
  const taipeiClock = epoch => new Date((epoch + 8 * 3600) * 1000).toISOString().slice(11, 16);
  // 某個時區「現在」的日期（YYYY-MM-DD）；時區名稱不認得就回 null
  function zoneDate(ms, timezone) {
    if (typeof timezone !== 'string' || !timezone) return null;
    try {
      return new Intl.DateTimeFormat('en-CA', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(ms));
    } catch (_) { return null; }
  }
  // 時間軸要寫的字：開盤、收盤，中間挑整點（台北時間），間隔看這一段有多長；太靠近頭尾的不寫
  function sessionTicks(start, end, step) {
    const n = Math.floor((end - start) / step) + 1;
    const ticks = [[0, taipeiClock(start), 'start']];
    const hours = (end - start) / 3600;
    const every = (hours <= 4 ? 1 : hours <= 9 ? 2 : hours <= 16 ? 4 : 6) * 3600;
    const margin = Math.max(2700, (end - start) * 0.14);
    for (let t = Math.ceil(start / 3600) * 3600; t < end; t += 3600) {
      if ((t + 8 * 3600) % every || t - start < margin || end - t < margin) continue;
      ticks.push([(t - start) / step, taipeiClock(t), 'middle']);
    }
    ticks.push([n - 1, taipeiClock(start + (n - 1) * step), 'end']);
    return ticks;
  }
  // 回應 → 分時圖要的 model（kind:'index'）。沒有量、沒有均價；缺的那幾格留白（不沿用上一個價），
  // 連續缺超過 15 分鐘（日經午休、期貨每日休息）線就斷開。拿不到、沒有前一日收盤時回 null。
  function sessionModel(payload, nowMs = Date.now()) {
    const ref = Number(payload?.previous_close);
    const step = finite(payload?.interval) && payload.interval > 0 ? payload.interval : 300;
    const points = (Array.isArray(payload?.points) ? payload.points : [])
      .filter(p => Array.isArray(p) && finite(p[0]) && finite(p[1]) && p[1] > 0).sort((a, b) => a[0] - b[0]);
    if (!payload || payload.available !== true || !points.length || !(ref > 0)) return null;
    const first = points[0][0], newest = points[points.length - 1][0];
    const start = finite(payload.session?.start) && payload.session.start <= first ? payload.session.start : first;
    const end = finite(payload.session?.end) && payload.session.end >= newest ? payload.session.end : newest;
    const n = Math.max(2, Math.floor((end - start) / step) + 1);
    const out = new Array(n).fill(null);
    let last = -1, high = null, low = null;
    points.forEach(([t, price]) => {
      const i = Math.min(n - 1, Math.floor((t - start) / step));
      out[i] = { price, volume: 0, avg: price };
      last = Math.max(last, i);
    });
    out.forEach((s, i) => {
      if (!s) return;
      if (!high || s.price > high.value) high = { value: s.price, index: i };
      if (!low || s.price < low.value) low = { value: s.price, index: i };
    });
    return { kind: 'index', day: { date: payload.date || null, slots: out, last, high, low }, ref,
      ...range({ kind: 'index', ref, high: high.value, low: low.value }),
      open: points[0][1], high: high.value, low: low.value,
      n, gap: Math.round(900 / step), noVolume: true, ticks: sessionTicks(start, end, step),
      clock: i => taipeiClock(start + i * step), label: `${payload.name || '指數'}分時走勢`,
      // 這份資料是不是那個交易所「今天」的（休市、假日時來源給的是最近一個交易日）
      today: zoneDate(nowMs, payload.timezone) };
  }

  // ---------- 畫面 ----------
  const TW_TICKS = [[0, '09:00', 'start'], [60, '10:00', 'middle'], [120, '11:00', 'middle'], [180, '12:00', 'middle'], [270, '13:30', 'end']];
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
  // 匯率（model.digits，例如 3）：照那個小數位
  const priceText = (model, v, fixed = false) => (model.kind === 'index' ? comma(v, model.digits ?? 2)
    : fixed ? Number(v).toFixed(2) : String(Math.round(v * 100) / 100));

  // model：{ kind:'stock'|'index', day: slots() 的結果, ref, top, bottom }；onIndex(i) 手指／滑鼠移到哪一分鐘
  // 海外大盤（sessionModel）另帶：n（格數）、gap（連續缺幾格以上就斷線）、noVolume（不畫量）、ticks（時間軸的字）、clock、label
  function renderChart(host, model, onIndex) {
    const { day, ref, top, bottom } = model;
    const isIndex = model.kind === 'index';
    const count = model.n || N, hasVolume = !model.noVolume;
    const W = Math.max(280, host.clientWidth);
    const priceH = W > 600 ? 300 : 214, gap = hasVolume ? 10 : 0, volH = !hasVolume ? 0 : W > 600 ? 72 : 54, axisH = 20;
    const H = priceH + gap + volH + axisH, padR = 2;
    const x = i => i / (count - 1) * (W - padR);
    const y = v => (top - v) / (top - bottom) * priceH;
    const refY = y(ref);
    const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, class: 'ts-svg', role: 'img',
      'aria-label': model.label || (isIndex ? '加權指數分時走勢' : '分時走勢') });
    [0, 0.25, 0.75, 1].forEach(t => {
      const yy = Math.round(t * priceH) + 0.5;
      svg.append(svgEl('line', { x1: 0, x2: W, y1: yy, y2: yy, class: 'ts-grid' }));
    });
    const drawn = day.slots.map((s, i) => (s ? i : -1)).filter(i => i >= 0);
    const pt = i => `${x(i).toFixed(2)},${y(day.slots[i].price).toFixed(2)}`;
    // 一段一段連續的線：台股的格子沒有洞（沒成交沿用上一個價），永遠只有一段；海外大盤午休那一段不連
    const runs = [];
    drawn.forEach((i, k) => {
      if (!k || i - drawn[k - 1] > (model.gap || Infinity)) runs.push([]);
      runs.at(-1).push(i);
    });
    if (drawn.length) {
      // 價格線與參考價之間的淡填色：上紅、下綠
      const area = runs.map(run => `M${x(run[0]).toFixed(2)},${refY.toFixed(2)} L${run.map(pt).join(' L')} L${x(run.at(-1)).toFixed(2)},${refY.toFixed(2)} Z`).join(' ');
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
    runs.forEach(run => svg.append(svgEl('polyline', { points: run.map(pt).join(' '), class: 'ts-line' })));

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
      pick[0] = Math.max(0, Math.min(pick[0], W - tw));   // 找不到空位時至少不超出圖的左右邊（最高點在最後一根時）
      const g = svgEl('g', { class: `ts-mark ts-mark-${kind}` });
      g.append(svgEl('circle', { cx: px, cy: py, r: 2.6 }), svgEl('text', { x: pick[0], y: pick[1] + 3.8 }, label));
      svg.append(g);
      taken.push([pick[0], pick[1] - th / 2, pick[0] + tw, pick[1] + th / 2]);
    };
    mark(day.high, 'hi');
    mark(day.low, 'lo');

    // 每分鐘的量（中性灰）。開盤、收盤兩根集合競價常常特別大，最高刻度不算它們、柱子頂到上緣為止。
    const vTop = priceH + gap;
    let vMax = 1;
    if (hasVolume) {
      const middle = drawn.filter(i => i > 0 && i < N - 1).map(i => day.slots[i].volume);
      vMax = Math.max(1, ...(middle.length ? middle : drawn.map(i => day.slots[i].volume))) * 1.05;
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
    }
    (model.ticks || TW_TICKS).forEach(([i, t, a]) => svg.append(svgEl('text', { x: x(i), y: H - 5, 'text-anchor': a, class: 'ts-time' }, t)));

    // 手指／滑鼠移到哪一分鐘，上方那一行就換成那一分鐘（只到最後一筆為止）
    if (drawn.length) {
      const cross = svgEl('g', { class: 'ts-cross', visibility: 'hidden' });
      const cl = svgEl('line', { y1: 0, y2: vTop + volH });
      const cd = svgEl('circle', { r: 3.2 });
      // 橫線（本人 11-24「除了垂直虛線，也要有水平虛線」）：跟著游標／手指的高度（不貼價格線），右邊標那個高度的價位；
      // 移到量的那一塊時標那個高度的量（個股，張；大盤的成交金額單位不定，只畫線）。兩塊之間的空隙與時間軸上不畫。
      const ch = svgEl('line', { x1: 0, x2: W - padR, class: 'ts-cross-h' });
      const tag = svgEl('g', { class: 'ts-cross-tag' });
      const tagBox = svgEl('rect', { height: 18, rx: 4 }), tagText = svgEl('text', { 'text-anchor': 'end' });
      tag.append(tagBox, tagText);
      cross.append(cl, ch, cd, tag);
      svg.append(cross);
      const level = py => {
        const inPrice = py >= 0 && py <= priceH, inVolume = hasVolume && py >= vTop && py <= vTop + volH;
        const label = inPrice ? priceText(model, top - py / priceH * (top - bottom), true)
          : inVolume && !isIndex ? lots((vTop + volH - py) / volH * vMax) : '';
        ch.setAttribute('visibility', inPrice || inVolume ? 'inherit' : 'hidden');
        tag.setAttribute('visibility', label ? 'inherit' : 'hidden');
        if (!inPrice && !inVolume) return;
        const lineY = Math.round(py) + 0.5;
        ch.setAttribute('y1', lineY); ch.setAttribute('y2', lineY);
        if (!label) return;
        const w = label.length * 6.7 + 10, paneTop = inPrice ? 0 : vTop, paneBottom = inPrice ? priceH : vTop + volH;
        const boxTop = Math.max(paneTop, Math.min(paneBottom - 18, py - 9));
        tagBox.setAttribute('x', W - padR - w); tagBox.setAttribute('y', boxTop); tagBox.setAttribute('width', w);
        tagText.setAttribute('x', W - padR - 5); tagText.setAttribute('y', boxTop + 12.6);
        tagText.textContent = label;
      };
      const at = ev => {
        const box = svg.getBoundingClientRect();
        const want = Math.round((ev.clientX - box.left) / (W - padR) * (count - 1));
        const i = drawn.reduce((best, k) => (Math.abs(k - want) < Math.abs(best - want) ? k : best), drawn[0]);
        cl.setAttribute('x1', x(i)); cl.setAttribute('x2', x(i));
        cd.setAttribute('cx', x(i)); cd.setAttribute('cy', y(day.slots[i].price));
        level((ev.clientY - box.top) * (box.height ? H / box.height : 1));
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
    // model.quoteLabel／changeText（匯率，market-index.js 的 fxModel）：名稱與漲跌那一段換成它給的寫法，顏色照數字的漲跌
    host.append(el('span', 'ts-q-time num', (model.clock || clock)(i)), pair(model.quoteLabel || (isIndex ? '指數' : '價'), priceText(model, s.price, true)),
      el('span', `num ts-q-chg is-${dir}`, model.changeText ? model.changeText(diff, pct)
        : `${diff > 0 ? '▲' : diff < 0 ? '▼' : ''}${isIndex ? comma(Math.abs(diff), 2) : Math.abs(diff).toFixed(2)}(${Math.abs(pct).toFixed(2)}%)`));
    if (!isIndex) host.append(pair('均', s.avg.toFixed(2), 'ts-q-avg'), pair('量', lots(s.volume)));
  }

  // 開 高 低 總量（個股，張）；大盤只寫開高低
  function ohlcLine(host, model) {
    const { open, high, low, total } = model;
    host.replaceChildren();
    const pair = (label, value) => {
      if (!finite(value)) return;
      const span = el('span');
      span.append(el('i', '', label), el('b', 'num', priceText(model, value)));
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

  window.TimeShare = { N, slots, range, flow, indexMinutes, sessionModel, sessionTicks, zoneDate, render, renderBook };
})();
