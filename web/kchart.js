/* 可拖曳／縮放的K線圖（原生Canvas，無外部套件）。
   互動：滑鼠拖曳或單指左右平移；滾輪、雙指捏合或＋／−按鈕縮放；鍵盤左右鍵平移、+/- 縮放；點擊或移動顯示十字線。
   資料：candles=[{t,label,open,high,low,close,amount}] 由舊到新。overlays 可放判斷線。 */
(function () {
  'use strict';
  const DPR = () => Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function fmt(n, d = 2) { return Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); }

  function ema(values, n) { const k = 2 / (n + 1); let prev = null; return values.map(v => (prev = prev === null ? v : v * k + prev * (1 - k))); }
  function sma(values, n) { let sum = 0; return values.map((v, i) => { sum += v; if (i >= n) sum -= values[i - n]; return i >= n - 1 ? sum / n : null; }); }
  function macd(closes) {
    const e12 = ema(closes, 12), e26 = ema(closes, 26);
    const dif = closes.map((_, i) => e12[i] - e26[i]);
    const dea = ema(dif, 9);
    return closes.map((_, i) => ({ dif: dif[i], dea: dea[i], hist: (dif[i] - dea[i]) * 2, ready: i >= 33 }));
  }

  class KChart {
    constructor(host, opts) {
      this.host = host; this.opts = opts || {};
      this.canvas = document.createElement('canvas');
      this.canvas.tabIndex = 0;
      this.canvas.setAttribute('role', 'img');
      this.canvas.style.cssText = 'display:block;width:100%;touch-action:none;cursor:grab;border-radius:10px';
      host.append(this.canvas);
      this.tip = document.createElement('div'); this.tip.className = 'kc-tip'; host.append(this.tip);
      this.candles = []; this.view = { end: 0, count: 60 }; this.hover = -1; this.overlays = [];
      this.showMacd = this.opts.macd !== false;
      this.bind();
      new ResizeObserver(() => this.draw()).observe(host);
    }
    setData(candles, opts = {}) {
      this.unit = opts.unit || '日';
      this.compute(candles);
      const count = Math.min(candles.length, opts.count || this.view.count || 60);
      this.view = { end: candles.length, count };
      this.hover = -1;
      this.overlays = opts.overlays || [];
      this.draw(); this.emit();
    }
    compute(candles) {
      this.candles = candles; this.shownLength = candles.length;
      const closes = candles.map(c => c.close);
      this.ma = { 5: sma(closes, 5), 20: sma(closes, 20), 60: sma(closes, 60) };
      this.macd = macd(closes);
      if (candles.length) this.canvas.setAttribute('aria-label', `加權指數${this.unit}K，共${candles.length}根，${candles[0].label}至${candles[candles.length - 1].label}。可拖曳平移、滾輪縮放。`);
    }
    update(candles, overlays) {
      const atEnd = this.view.end >= this.shownLength;
      this.compute(candles);
      if (overlays) this.overlays = overlays;
      if (atEnd) this.view.end = candles.length;
      this.clamp(); this.draw(); this.emit();
    }
    clamp() {
      const n = this.candles.length;
      this.view.count = Math.max(Math.min(15, n), Math.min(n, Math.round(this.view.count)));
      this.view.end = Math.max(this.view.count, Math.min(n, this.view.end));
    }
    zoom(factor, anchorRatio = 1) {
      const before = this.view.count, start = this.view.end - before;
      const anchor = start + before * anchorRatio;
      this.view.count = before * factor; this.clamp();
      this.view.end = Math.round(anchor + this.view.count * (1 - anchorRatio)); this.clamp();
      this.draw(); this.emit();
    }
    pan(delta) { this.view.end += delta; this.clamp(); this.draw(); this.emit(); }
    reset() { this.view.end = this.candles.length; this.view.count = Math.min(this.candles.length, 60); this.draw(); this.emit(); }
    emit() { if (this.opts.onView) { const s = this.view.end - this.view.count; this.opts.onView(this.candles[s], this.candles[this.view.end - 1], this.view.end === this.candles.length); } }
    bind() {
      const c = this.canvas; let drag = null; const pts = new Map();
      const geo = () => this.geo;
      c.addEventListener('pointerdown', e => {
        c.setPointerCapture(e.pointerId); pts.set(e.pointerId, e.clientX);
        if (pts.size === 1) drag = { x: e.clientX, end: this.view.end, moved: false };
        if (pts.size === 2) { const xs = [...pts.values()]; drag = { pinch: Math.abs(xs[0] - xs[1]), count: this.view.count, end: this.view.end }; }
        c.style.cursor = 'grabbing';
      });
      c.addEventListener('pointermove', e => {
        const g = geo(); if (!g) return;
        if (pts.has(e.pointerId)) pts.set(e.pointerId, e.clientX);
        const rect = c.getBoundingClientRect(); const x = e.clientX - rect.left;
        if (drag && pts.size === 2 && drag.pinch) {
          const xs = [...pts.values()]; const d = Math.max(20, Math.abs(xs[0] - xs[1]));
          this.view.count = drag.count * drag.pinch / d; this.view.end = drag.end; this.clamp(); this.draw(); this.emit(); return;
        }
        if (drag && pts.size === 1) {
          const dx = e.clientX - drag.x; if (Math.abs(dx) > 3) drag.moved = true;
          this.view.end = Math.round(drag.end - dx / g.step); this.clamp();
        }
        this.hover = x >= g.l && x <= g.l + g.pw ? g.start + Math.floor((x - g.l) / g.step) : -1;
        this.draw(); if (drag && drag.moved) this.emit();
      });
      const up = e => { pts.delete(e.pointerId); if (pts.size === 0) { drag = null; c.style.cursor = 'grab'; } };
      c.addEventListener('pointerup', up); c.addEventListener('pointercancel', up);
      c.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse' && !drag) { this.hover = -1; this.draw(); } });
      c.addEventListener('wheel', e => {
        const g = geo(); if (!g) return; e.preventDefault();
        const rect = c.getBoundingClientRect(); const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left - g.l) / g.pw));
        this.zoom(e.deltaY > 0 ? 1.15 : 1 / 1.15, ratio);
      }, { passive: false });
      c.addEventListener('keydown', e => {
        const step = Math.max(1, Math.round(this.view.count / 10));
        if (e.key === 'ArrowLeft') { e.preventDefault(); this.pan(-step); }
        else if (e.key === 'ArrowRight') { e.preventDefault(); this.pan(step); }
        else if (e.key === '+' || e.key === '=') { e.preventDefault(); this.zoom(1 / 1.25); }
        else if (e.key === '-') { e.preventDefault(); this.zoom(1.25); }
        else if (e.key === 'Home') { e.preventDefault(); this.view.end = this.view.count; this.clamp(); this.draw(); this.emit(); }
        else if (e.key === 'End') { e.preventDefault(); this.reset(); }
      });
    }
    draw() {
      const c = this.canvas, n = this.candles.length; if (!n) return;
      const W = this.host.clientWidth || 600, H = this.opts.height ? this.opts.height(W) : Math.max(300, Math.min(460, W * .56));
      const dpr = DPR(); c.width = W * dpr; c.height = H * dpr; c.style.height = H + 'px';
      const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, H);
      const stacked = this.opts.overlayLabels === 'stacked';
      const l = 4, r = stacked ? (W < 520 ? 150 : 176) : 64, t = 10, b = 22, macdH = this.showMacd ? Math.round(H * .24) : 0, gapH = this.showMacd ? 10 : 0;
      const pw = W - l - r, ph = H - t - b - macdH - gapH;
      const start = this.view.end - this.view.count, end = this.view.end, step = pw / this.view.count;
      this.geo = { l, pw, step, start };
      const vis = this.candles.slice(start, end);
      let lo = Math.min(...vis.map(c => c.low)), hi = Math.max(...vis.map(c => c.high));
      this.overlays.forEach(o => { if (o.inView !== false) { lo = Math.min(lo, o.value); hi = Math.max(hi, o.value); } });
      const pad = (hi - lo) * .07 || 10; lo -= pad; hi += pad;
      const y = v => t + (hi - v) / (hi - lo) * ph;
      const line = css('--line'), dim = css('--faint'), ink = css('--ink'), up = css('--up'), dn = css('--down');
      x.font = '11.5px "JetBrains Mono",Consolas,monospace'; x.textBaseline = 'middle';
      // 價格刻度
      const raw = (hi - lo) / 5, mag = Math.pow(10, Math.floor(Math.log10(raw))), tick = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
      for (let v = Math.ceil(lo / tick) * tick; v <= hi; v += tick) {
        x.strokeStyle = line; x.lineWidth = 1; x.beginPath(); x.moveTo(l, Math.round(y(v)) + .5); x.lineTo(l + pw, Math.round(y(v)) + .5); x.stroke();
        x.fillStyle = dim; x.fillText(fmt(v, 0), l + pw + 8, y(v));
      }
      // 時間刻度
      x.textBaseline = 'alphabetic'; let lastKey = '';
      const every = Math.max(1, Math.ceil(70 / step));
      vis.forEach((c, i) => {
        const intraday = this.unit !== '60分' && this.unit.endsWith('分');
        if (intraday) { if (i % every === 0) { x.fillStyle = dim; x.fillText(c.t.slice(11, 16), l + step * i, H - macdH - gapH - 6); } return; }
        const key = this.unit === '月' ? c.t.slice(0, 4) : this.unit === '週' ? c.t.slice(0, 7) : this.unit === '60分' ? c.t.slice(0, 10) : c.t.slice(0, 7);
        if (key !== lastKey && i % every === 0) { lastKey = key; x.fillStyle = dim; x.fillText(this.unit === '月' ? key : this.unit === '60分' ? c.t.slice(5, 10) : c.t.slice(2, 7).replace('-', '/'), l + step * i, H - macdH - gapH - 6); }
      });
      // 判斷線
      const sortedOv = [...this.overlays].sort((a, b2) => b2.value - a.value);
      if (stacked) {
        // 個股頁用：標籤放右側價格欄外，依價位由上而下排；彼此至少隔 gap，且避開最新價框，用短引線連回實際價位。
        const gap = 19, top0 = t + 8, bottom = t + ph - 8;
        const slots = [];
        sortedOv.forEach(o => {
          let s = Math.max(y(o.value), slots.length ? slots[slots.length - 1] + gap : top0);
          slots.push(s);
        });
        // 超出底部就整串往上推（保持間距）。
        for (let i = slots.length - 1; i >= 0; i--) {
          const limit = i === slots.length - 1 ? bottom : slots[i + 1] - gap;
          if (slots[i] > limit) slots[i] = limit;
        }
        for (let i = 1; i < slots.length; i++) if (slots[i] < slots[i - 1] + gap) slots[i] = slots[i - 1] + gap;
        const boxes = [];
        sortedOv.forEach((o, k) => {
          const yy = y(o.value), ly = slots[k];
          x.strokeStyle = o.color || css('--gold'); x.setLineDash([5, 4]); x.lineWidth = 1.2;
          x.beginPath(); x.moveTo(l, yy); x.lineTo(l + pw, yy); x.stroke(); x.setLineDash([]);
          const lx = l + pw + 52;
          x.lineWidth = 1; x.beginPath(); x.moveTo(l + pw, yy); x.lineTo(lx, ly); x.stroke();
          x.font = '600 11.5px "JetBrains Mono",Consolas,monospace';
          const w = x.measureText(o.label).width + 10;
          x.fillStyle = css('--panel') || '#fff'; x.fillRect(lx, ly - 8, w, 16);
          x.strokeStyle = o.color || css('--gold'); x.strokeRect(lx + .5, ly - 7.5, w - 1, 15);
          x.fillStyle = o.color || css('--gold'); x.textBaseline = 'middle'; x.textAlign = 'left';
          x.fillText(o.label, lx + 5, ly + .5);
          boxes.push({ y: ly - 8, h: 16, label: o.label });
          x.font = '11.5px "JetBrains Mono",Consolas,monospace'; x.textBaseline = 'alphabetic';
        });
        window.__kchartLabelBoxes = boxes; // 驗收用：標籤框位置（判斷是否重疊）
      } else sortedOv.forEach((o, k) => {
        x.strokeStyle = o.color || css('--gold'); x.setLineDash([5, 4]); x.lineWidth = 1.2;
        x.beginPath(); x.moveTo(l, y(o.value)); x.lineTo(l + pw, y(o.value)); x.stroke(); x.setLineDash([]);
        x.fillStyle = o.color || css('--gold'); x.textAlign = 'left';
        const above = o.pos ? o.pos === 'above' : k === 0; x.textBaseline = above ? 'bottom' : 'top';
        if (o.align === 'right') { x.textAlign = 'right'; x.fillText(o.label, l + pw - 6, y(o.value) + (above ? -3 : 3)); x.textAlign = 'left'; }
        else x.fillText(o.label, l + 6, y(o.value) + (above ? -3 : 3));
        x.textBaseline = 'alphabetic';
      });
      // K棒
      const bw = Math.max(1, Math.min(14, step * .66));
      vis.forEach((c, i) => {
        const cx = l + step * (i + .5), col = c.close >= c.open ? up : dn;
        x.strokeStyle = col; x.fillStyle = col; x.lineWidth = 1;
        x.beginPath(); x.moveTo(cx, y(c.high)); x.lineTo(cx, y(c.low)); x.stroke();
        const top = y(Math.max(c.open, c.close)), h = Math.max(1, Math.abs(y(c.open) - y(c.close)));
        if (this.opts.liveLast && start + i === n - 1) {
          x.globalAlpha = .45; x.fillRect(cx - bw / 2, top, bw, h); x.globalAlpha = 1;
          x.lineWidth = 1.4; x.strokeRect(cx - bw / 2 - 1.5, top - 1.5, bw + 3, h + 3);
        } else x.fillRect(cx - bw / 2, top, bw, h);
      });
      // 均線
      [[5, css('--dim'), 1.1], [20, css('--accent'), 1.8], [60, '#7d8cff', 1.4]].forEach(([p, col, w]) => {
        x.strokeStyle = col; x.lineWidth = w; x.beginPath(); let on = false;
        for (let i = start; i < end; i++) { const v = this.ma[p][i]; if (v == null) { on = false; continue; } const px = l + step * (i - start + .5); on ? x.lineTo(px, y(v)) : x.moveTo(px, y(v)); on = true; }
        x.stroke();
      });
      // 最新價標籤
      const last = this.candles[n - 1];
      if (end === n) { const pw2 = stacked ? 46 : r - 4; x.fillStyle = ink; x.textBaseline = 'middle'; x.fillRect(l + pw + 2, y(last.close) - 9, pw2, 18); x.fillStyle = css('--bg'); x.fillText(fmt(last.close, 0), l + pw + 7, y(last.close)); }
      // MACD
      if (this.showMacd) {
        const top = H - macdH, mv = this.macd.slice(start, end).filter(m => m.ready);
        const amp = Math.max(1, ...mv.map(m => Math.max(Math.abs(m.dif), Math.abs(m.dea), Math.abs(m.hist)))) * 1.1;
        const my = v => top + macdH / 2 - v / amp * (macdH / 2 - 4);
        x.strokeStyle = line; x.beginPath(); x.moveTo(l, my(0)); x.lineTo(l + pw, my(0)); x.stroke();
        x.fillStyle = dim; x.textBaseline = 'top'; x.fillText('MACD(12,26,9)', l + 4, top + 2);
        for (let i = start; i < end; i++) { const m = this.macd[i]; if (!m.ready) continue; const px = l + step * (i - start + .5); x.fillStyle = m.hist >= 0 ? up : dn; x.globalAlpha = .7; x.fillRect(px - bw / 2, Math.min(my(0), my(m.hist)), bw, Math.abs(my(m.hist) - my(0)) || 1); x.globalAlpha = 1; }
        [['dif', ink], ['dea', css('--gold')]].forEach(([k, col]) => { x.strokeStyle = col; x.lineWidth = 1.2; x.beginPath(); let on = false; for (let i = start; i < end; i++) { const m = this.macd[i]; if (!m.ready) { on = false; continue; } const px = l + step * (i - start + .5); on ? x.lineTo(px, my(m[k])) : x.moveTo(px, my(m[k])); on = true; } x.stroke(); });
      }
      // 十字線與提示
      if (this.hover >= start && this.hover < end) {
        const i = this.hover, c = this.candles[i], cx = l + step * (i - start + .5);
        x.strokeStyle = dim; x.setLineDash([3, 3]); x.beginPath(); x.moveTo(cx, t); x.lineTo(cx, H - 4); x.stroke(); x.setLineDash([]);
        const prev = this.candles[i - 1], chg = prev ? c.close - prev.close : 0, m = this.macd[i];
        this.tip.innerHTML = `<b>${c.label}</b><span class="${chg < 0 ? 'dn' : 'upc'}">收 ${fmt(c.close)}　${chg >= 0 ? '+' : '−'}${fmt(Math.abs(chg))}</span><span>開 ${fmt(c.open)}　高 ${fmt(c.high)}　低 ${fmt(c.low)}</span>` + (c.amount != null ? `<span>成交 ${fmt(c.amount, 0)} 億</span>` : '') + (m.ready ? `<span>MACD柱 ${fmt(m.hist)}　DIF ${fmt(m.dif)}</span>` : '') + ['5', '20', '60'].map(p => this.ma[p][i] != null ? `MA${p} ${fmt(this.ma[p][i], 0)}` : '').filter(Boolean).join('　').replace(/^/, '<span>') + '</span>';
        this.tip.classList.add('show');
        const tw = this.tip.offsetWidth; this.tip.style.left = (cx + 14 + tw > W ? cx - tw - 14 : cx + 14) + 'px'; this.tip.style.top = '8px';
      } else this.tip.classList.remove('show');
    }
  }
  window.KChart = KChart;
  window.KChartMath = { sma, ema, macd };
})();
