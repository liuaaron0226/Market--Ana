/* 可拖曳／縮放的K線圖（原生Canvas，無外部套件）。
   互動：滑鼠拖曳或單指左右平移；滾輪、雙指捏合或＋／−按鈕縮放；鍵盤左右鍵平移、+/- 縮放；點擊或移動顯示十字線。
   資料：candles=[{t,label,open,high,low,close,amount}] 由舊到新。overlays 可放判斷線。
   畫線（選用）：頁面先載入 chart-drawings.js 並傳 opts.drawings={onChange,onTool}，再用 setTool／setDrawings 操作。 */
(function () {
  'use strict';
  const DPR = () => Math.max(1, Math.min(3, window.devicePixelRatio || 1));
  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  // 價籤用：千元以上取整；以下保留到台股升降單位（最多兩位小數，去掉尾端 0）。
  function fmtTag(n) { return !Number.isFinite(n) ? '缺值' : n >= 1000 ? fmt(n, 0) : n.toLocaleString('en-US', { maximumFractionDigits: 2 }); }
  function fmt(n, d = 2) { return Number.isFinite(n) ? n.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }) : '缺值'; }
  const hasPrice = candle => ['open', 'high', 'low', 'close'].every(key => Number.isFinite(candle[key]));

  function ema(values, n) { const k = 2 / (n + 1); let prev = null; return values.map(v => (prev = prev === null ? v : v * k + prev * (1 - k))); }
  function sma(values, n) {
    let sum = 0, count = 0;
    return values.map((v, i) => {
      if (!Number.isFinite(v)) { sum = 0; count = 0; return null; }
      sum += v; count++;
      if (count > n) sum -= values[i - n];
      return count >= n ? sum / n : null;
    });
  }
  // 布林通道：中線＝n 期收盤均價，上下軌＝中線 ± k 個母體標準差。缺值會中斷累積，之後重新湊滿 n 期才有值。
  function bollinger(values, n = 20, k = 2) {
    let run = 0;
    return values.map((v, i) => {
      run = Number.isFinite(v) ? run + 1 : 0;
      if (run < n) return null;
      let sum = 0;
      for (let j = i - n + 1; j <= i; j++) sum += values[j];
      const mid = sum / n;
      let squares = 0;
      for (let j = i - n + 1; j <= i; j++) squares += (values[j] - mid) ** 2;
      const width = k * Math.sqrt(squares / n);
      return { mid, upper: mid + width, lower: mid - width };
    });
  }
  function normalizePeriods(values) {
    const periods = [];
    for (const value of values || []) {
      const numeric = Math.round(Number(value) || 0);
      if (numeric < 2) continue;
      const parsed = Math.min(240, numeric);
      if (!periods.includes(parsed)) periods.push(parsed);
      if (periods.length >= 4) break;
    }
    return periods.length ? periods : [18, 45];
  }
  function macd(closes) {
    let fast = null, slow = null, signal = null, count = 0;
    return closes.map(close => {
      if (!Number.isFinite(close)) {
        fast = slow = signal = null; count = 0;
        return { dif: null, dea: null, hist: null, ready: false };
      }
      fast = fast === null ? close : fast + (close - fast) * 2 / 13;
      slow = slow === null ? close : slow + (close - slow) * 2 / 27;
      const dif = fast - slow;
      signal = signal === null ? dif : signal + (dif - signal) * 2 / 10;
      return { dif, dea: signal, hist: dif - signal, ready: ++count >= 34 };
    });
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
      this.candles = []; this.view = { end: 0, count: 60 }; this.hover = -1; this.selection = -1; this.overlays = [];
      this.showMacd = this.opts.macd !== false;
      this.maPeriods = normalizePeriods(this.opts.maPeriods || [18, 45]);
      this.drawings = this.opts.drawings && window.ChartDrawings ? new window.ChartDrawings(this, this.opts.drawings) : null;
      this.bindCanvas(this.canvas);
      this.studies = this.opts.studyHost ? new window.ChartStudies(this, this.opts.studyHost, { volumeKind: this.opts.volumeKind || 'amount', studies: this.opts.studies }) : null;
      if (this.studies) document.addEventListener('pointerdown', event => {
        if (![this.host, this.opts.studyHost, ...(this.opts.selectionRoots || [])].some(root => root.contains(event.target)) && this.hover >= 0) {
          this.clearSelection();
        }
      });
      new ResizeObserver(() => this.draw()).observe(host);
    }
    setData(candles, opts = {}) {
      this.unit = opts.unit || '日';
      this.sourceMacd = opts.macd || null;
      this.macdNotice = opts.macdNotice || '';
      this.compute(candles);
      const count = Math.min(candles.length, opts.count || this.view.count || 60);
      this.view = { end: candles.length, count };
      this.hover = -1; this.selection = -1;
      this.overlays = opts.overlays || [];
      if (this.drawings) this.drawings.dataChanged();
      this.draw(); this.emit();
    }
    // 畫線：目前要用的工具（'hline'；null＝一般模式），以及這張圖上的整組畫線（[{id,kind,anchors:[{date,price}]}]）。
    setTool(kind) { if (this.drawings) this.drawings.setTool(kind); }
    setDrawings(list) { if (this.drawings) { this.drawings.load(list); this.draw(); } }
    compute(candles) {
      this.candles = candles; this.shownLength = candles.length;
      const closes = candles.map(c => hasPrice(c) ? c.close : null);
      this.ma = Object.fromEntries(this.maPeriods.map(period => [period, sma(closes, period)]));
      this.boll = bollinger(closes);
      const supplied = this.sourceMacd && new Map(this.sourceMacd.map(row => [row.date, row]));
      this.macd = supplied ? candles.map(c => {
        const row = supplied.get(c.t);
        return row && row.ready && ['dif', 'dea', 'hist'].every(k => Number.isFinite(row[k]))
          ? row : { dif: null, dea: null, hist: null, ready: false };
      }) : macd(closes);
      if (candles.length) this.canvas.setAttribute('aria-label', `加權指數${this.unit}K，共${candles.length}根，${candles[0].label}至${candles[candles.length - 1].label}。可拖曳平移、滾輪縮放。`);
    }
    update(candles, overlays) {
      const atEnd = this.view.end >= this.shownLength;
      this.compute(candles);
      if (overlays) this.overlays = overlays;
      if (atEnd) this.view.end = candles.length;
      this.clamp(); this.draw(); this.emit();
    }
    setMaPeriods(periods) {
      this.maPeriods = normalizePeriods(periods);
      this.compute(this.candles);
      this.draw();
    }
    clamp() {
      const n = this.candles.length;
      const requestedMin = Number(this.opts.minViewCount);
      const minCount = Math.min(Number.isFinite(requestedMin) ? Math.max(1, Math.round(requestedMin)) : 15, n);
      this.view.count = Math.max(minCount, Math.min(n, Math.round(this.view.count)));
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
    clearSelection() {
      this.hover = -1; this.selection = -1;
      if (this.opts.onClear) this.opts.onClear();
      this.draw();
    }
    bindCanvas(c) {
      let drag = null; const pts = new Map();
      const geo = () => this.geo;
      c.addEventListener('pointerdown', e => {
        c.setPointerCapture(e.pointerId); pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pts.size === 1) drag = { kind: 'single', x: e.clientX, y: e.clientY, end: this.view.end, moved: false, hadPinch: false, studySwipe: c !== this.canvas && !!this.studies };
        if (pts.size === 2) { const xs = [...pts.values()].map(point => point.x); drag = { kind: 'pinch', pinch: Math.max(20, Math.abs(xs[0] - xs[1])), count: this.view.count, end: this.view.end, hadPinch: true, studySwipe: c !== this.canvas && !!this.studies }; }
        c.style.cursor = 'grabbing';
        if (drag?.studySwipe) { this.hover = this.selection; this.draw(); }
      });
      c.addEventListener('pointermove', e => {
        const g = geo(); if (!g) return;
        if (pts.has(e.pointerId)) pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const rect = c.getBoundingClientRect(); const x = e.clientX - rect.left;
        if (drag?.kind === 'pinch' && pts.size === 2) {
          if (drag.studySwipe) return;
          const xs = [...pts.values()].map(point => point.x); const d = Math.max(20, Math.abs(xs[0] - xs[1]));
          this.view.count = drag.count * drag.pinch / d; this.view.end = drag.end; this.clamp(); this.draw(); this.emit(); return;
        }
        if (drag?.kind === 'single' && pts.size === 1) {
          const dx = e.clientX - drag.x; if ((drag.studySwipe ? Math.hypot(dx, e.clientY - drag.y) : Math.abs(dx)) > 3) drag.moved = true;
          if (drag.studySwipe) return;
          this.view.end = Math.round(drag.end - dx / g.step); this.clamp();
        }
        this.hover = x >= g.l && x <= g.l + g.pw ? g.start + Math.floor((x - g.l) / g.step) : -1;
        this.draw(); if (drag && drag.moved) this.emit();
      });
      const up = e => {
        if (e.type === 'pointerup' && drag?.kind === 'single' && drag.studySwipe && drag.moved && !drag.hadPinch) {
          const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
          if (Math.abs(dx) >= 40 && Math.abs(dx) > Math.abs(dy) * 1.3) this.studies.switchBy(dx < 0 ? 1 : -1);
        }
        if (e.type === 'pointerup' && drag?.kind === 'single' && !drag.moved && !drag.hadPinch && this.geo) {
          const g = this.geo, rect = c.getBoundingClientRect(), px = e.clientX - rect.left;
          // 選了畫線工具時，主圖上的這一下用來定位，不選 K 棒。
          if (!(c === this.canvas && this.drawings && this.drawings.tap(px, e.clientY - rect.top))) {
            this.hover = px >= g.l && px <= g.l + g.pw
              ? Math.min(this.view.end - 1, g.start + Math.floor((px - g.l) / g.step)) : -1;
            this.selection = this.hover;
            if (c !== this.canvas && this.hover >= 0 && this.opts.onSelect) this.opts.onSelect(this.hover, c);
          }
          this.draw();
        }
        pts.delete(e.pointerId);
        if (pts.size === 1 && drag?.kind === 'pinch') {
          const point = pts.values().next().value;
          drag = { kind: 'single', x: point.x, y: point.y, end: this.view.end, moved: false, hadPinch: true, studySwipe: drag.studySwipe };
        } else if (pts.size === 0) { drag = null; c.style.cursor = c === this.canvas && this.drawings ? this.drawings.idleCursor() : 'grab'; }
      };
      c.addEventListener('pointerup', up); c.addEventListener('pointercancel', up);
      c.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse' && !drag) { this.hover = this.selection; this.draw(); } });
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
        else if (e.key === 'Escape') { e.preventDefault(); this.clearSelection(); }
        else if (e.key === 'Enter' && c !== this.canvas) {
          e.preventDefault(); this.hover = this.selection = this.view.end - 1;
          if (this.opts.onSelect) this.opts.onSelect(this.hover, c);
          this.draw();
        }
      });
    }
    draw() {
      const c = this.canvas, n = this.candles.length;
      if (!n) {
        c.getContext('2d').clearRect(0, 0, c.width, c.height);
        c.setAttribute('aria-label', '尚無K線資料');
        this.tip.classList.remove('show'); this.geo = null;
        if (this.studies) this.studies.draw();
        return;
      }
      const W = this.host.clientWidth || 600, H = this.opts.height ? this.opts.height(W) : Math.max(300, Math.min(460, W * .56));
      const dpr = DPR(); c.width = W * dpr; c.height = H * dpr; c.style.height = H + 'px';
      const x = c.getContext('2d'); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, W, H);
      const stacked = this.opts.overlayLabels === 'stacked';
      const compactStacked = stacked && W < 520;
      const compactRail = Math.min(102, Math.max(92, Math.round(W * .33)));
      const requestedLeft = typeof this.opts.leftRail === 'function' ? this.opts.leftRail(W) : this.opts.leftRail;
      const l = Math.max(4, Number.isFinite(Number(requestedLeft)) ? Math.round(Number(requestedLeft)) : 4);
      const tags = this.opts.overlayLabels === 'tags';
      // 價籤寬度：五位數或帶兩位小數（例如 128.5、28.35）都要加寬，避免文字擠到短名上。
      const tagWide = tags && this.overlays.concat(this.candles.slice(-1)).some(o => {
        const v = o.value ?? o.close; return v >= 10000 || (v < 1000 && fmtTag(v).length >= 6);
      });
      const r = tags ? (W < 520 ? (tagWide ? 84 : 66) : (tagWide ? 100 : 78)) : stacked ? (compactStacked ? compactRail : 176) : 64, t = 10, b = 22, macdH = this.showMacd ? Math.round(H * .24) : 0, gapH = this.showMacd ? 10 : 0;
      const pw = W - l - r, ph = H - t - b - macdH - gapH;
      const start = this.view.end - this.view.count, end = this.view.end, step = pw / this.view.count;
      this.geo = { l, pw, step, start };
      if (this.selection >= 0 && (this.selection < start || this.selection >= end)) {
        this.selection = -1; this.hover = -1;
        if (this.opts.onClear) this.opts.onClear();
      }
      const vis = this.candles.slice(start, end);
      const prices = vis.filter(hasPrice);
      if (!prices.length) {
        x.fillStyle = css('--dim'); x.font = '13px "Noto Sans TC","Microsoft JhengHei",sans-serif';
        x.fillText('此區間OHLC資料不足', 12, 32);
        this.tip.classList.remove('show');
        if (this.studies) this.studies.draw();
        return;
      }
      let lo = Math.min(...prices.map(c => c.low)), hi = Math.max(...prices.map(c => c.high));
      this.overlays.forEach(o => { if (o.inView !== false) { lo = Math.min(lo, o.value); hi = Math.max(hi, o.value); } });
      // 布林通道常比 K 棒寬，價格座標要把可見範圍內的上下軌算進去，通道才不會畫到圖外。
      for (let i = start; i < end; i++) { const band = this.boll[i]; if (band) { lo = Math.min(lo, band.lower); hi = Math.max(hi, band.upper); } }
      const pad = (hi - lo) * .07 || 10; lo -= pad; hi += pad;
      Object.assign(this.geo, { t, ph, lo, hi });
      const y = v => t + (hi - v) / (hi - lo) * ph;
      const line = css('--line'), dim = css('--faint'), ink = css('--ink'), up = css('--up'), dn = css('--down');
      x.font = '11.5px "JetBrains Mono",Consolas,monospace'; x.textBaseline = 'middle';
      // 範例（unslop 11-09）：價位標籤釘在價格軸上；先算位置，刻度文字再避開。
      const tagH = 18, tagGap = 20, tagX = l + pw + 4, tagW = r - 6;
      const tagSlots = [];
      if (tags) {
        // 收盤也當成一枚價籤一起排序：先由上往下確保間距，再由下往上收回界內，順序永遠跟價位一致。
        const lc = this.view.end === n && hasPrice(this.candles[n - 1]) ? this.candles[n - 1].close : null;
        const items = this.overlays.map(o => ({ o, value: o.value }));
        if (lc !== null) items.push({ o: null, value: lc, close: true });
        items.sort((a2, b2) => b2.value - a2.value);
        const top0 = t + tagH / 2, bot = t + ph - tagH / 2;
        items.forEach((it, i) => {
          it.yy = y(it.value);
          it.slot = Math.max(it.yy, i ? items[i - 1].slot + tagGap : top0);
        });
        for (let i = items.length - 1; i >= 0; i--) {
          const lim = i === items.length - 1 ? bot : items[i + 1].slot - tagGap;
          if (items[i].slot > lim) items[i].slot = lim;
        }
        tagSlots.push(...items);
      }
      // 價格刻度
      const axisBoxes = [];
      const raw = (hi - lo) / 5, mag = Math.pow(10, Math.floor(Math.log10(raw))), tick = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw);
      // 現價標籤（最右一根在畫面內才畫）所占的高度區間：與它重疊的刻度只略過文字、保留格線（本人 11-07：現價不可壓字）。
      const lastClose = this.view.end === n && hasPrice(this.candles[n - 1]) ? this.candles[n - 1].close : null;
      const tagTop = lastClose === null ? null : y(lastClose) - 9, tagBottom = tagTop === null ? null : tagTop + 18;
      for (let v = Math.ceil(lo / tick) * tick; v <= hi; v += tick) {
        x.strokeStyle = line; x.lineWidth = 1; x.beginPath(); x.moveTo(l, Math.round(y(v)) + .5); x.lineTo(l + pw, Math.round(y(v)) + .5); x.stroke();
        const boxTop = y(v) - 7;
        if (tagTop !== null && boxTop < tagBottom + 1 && tagTop - 1 < boxTop + 14) continue;
        if (boxTop < 0 || boxTop + 14 > H) continue; // 最上／最下貼邊會被裁切的刻度不寫
        // 手機個股圖把有限的右側空間留給有語意的關鍵價位；格線仍保留刻度感。
        if (compactStacked) continue;
        if (tags && tagSlots.some(g => Math.abs(g.slot - y(v)) < tagH)) continue;
        x.fillStyle = dim; x.fillText(fmt(v, 0), l + pw + 8, y(v));
        axisBoxes.push({ y: boxTop, h: 14, v });
      }
      // 時間刻度
      x.textBaseline = 'alphabetic'; let lastKey = '';
      const bySecond = this.unit.endsWith('秒');
      const every = Math.max(1, Math.ceil((bySecond ? 84 : 70) / step));
      vis.forEach((c, i) => {
        const intraday = bySecond || this.unit !== '60分' && this.unit.endsWith('分');
        if (intraday) { if (i % every === 0) { x.fillStyle = dim; x.fillText(c.t.slice(11, bySecond ? 19 : 16), l + step * i, H - macdH - gapH - 6); } return; }
        const key = this.unit === '月' ? c.t.slice(0, 4) : this.unit === '週' ? c.t.slice(0, 7) : this.unit === '60分' ? c.t.slice(0, 10) : c.t.slice(0, 7);
        if (key !== lastKey && i % every === 0) { lastKey = key; x.fillStyle = dim; x.fillText(this.unit === '月' ? key : this.unit === '60分' ? c.t.slice(5, 10) : c.t.slice(2, 7).replace('-', '/'), l + step * i, H - macdH - gapH - 6); }
      });
      // 判斷線
      const sortedOv = [...this.overlays].sort((a, b2) => b2.value - a.value);
      if (tags) {
        const boxes = [];
        tagSlots.filter(g => !g.close).forEach(({ o, yy, slot }) => {
          const col = o.color || css('--gold');
          // 整條淡細實線＋右端一小段實色錨點
          x.strokeStyle = col; x.lineWidth = 1; x.globalAlpha = .38;
          x.beginPath(); x.moveTo(l, Math.round(yy) + .5); x.lineTo(l + pw, Math.round(yy) + .5); x.stroke();
          x.globalAlpha = 1; x.lineWidth = 2;
          x.beginPath(); x.moveTo(l + pw - 20, Math.round(yy) + .5); x.lineTo(l + pw, Math.round(yy) + .5); x.stroke();
          // 標籤被擠開時，才畫一小段折線接回
          if (Math.abs(slot - yy) > 1) {
            x.lineWidth = 1; x.beginPath(); x.moveTo(l + pw, yy); x.lineTo(l + pw + 2, yy); x.lineTo(tagX, slot); x.stroke();
          }
          x.fillStyle = col;
          x.beginPath(); x.roundRect ? x.roundRect(tagX, slot - tagH / 2, tagW, tagH, 4) : x.rect(tagX, slot - tagH / 2, tagW, tagH); x.fill();
          x.fillStyle = css('--bg'); x.textBaseline = 'middle'; x.textAlign = 'left';
          x.font = '500 11px "Noto Sans TC","Microsoft JhengHei",sans-serif';
          x.fillText(o.short || '', tagX + 6, slot + .5);
          x.font = '500 11.5px "JetBrains Mono",Consolas,monospace'; x.textAlign = 'right';
          x.fillText(fmtTag(o.value), tagX + tagW - 6, slot + .5);
          x.textAlign = 'left'; x.textBaseline = 'alphabetic';
          boxes.push({ y: slot - tagH / 2, h: tagH, label: o.label });
        });
        window.__kchartLabelBoxes = boxes;
        x.font = '11.5px "JetBrains Mono",Consolas,monospace';
      } else if (stacked) {
        // 個股頁用：標籤放右側價格欄外，依價位由上而下排；彼此至少隔 gap，且避開最新價框，用短引線連回實際價位。
        const gap = 19, top0 = t + 8, bottom = t + ph - 8;
        // 手機共用同一條價位欄，標籤中心須避開最新價框；桌面仍維持雙欄。
        const blocked = compactStacked && tagTop !== null ? { min: tagTop - 9, max: tagBottom + 9 } : null;
        const avoidPrice = (slot, direction) => !blocked || slot < blocked.min || slot > blocked.max
          ? slot : direction > 0 ? blocked.max : blocked.min;
        const slots = [];
        sortedOv.forEach(o => {
          let s = Math.max(y(o.value), slots.length ? slots[slots.length - 1] + gap : top0);
          s = avoidPrice(s, 1);
          slots.push(s);
        });
        // 超出底部就整串往上推（保持間距並再次避開最新價框）。
        for (let i = slots.length - 1; i >= 0; i--) {
          const limit = i === slots.length - 1 ? bottom : slots[i + 1] - gap;
          if (slots[i] > limit) slots[i] = avoidPrice(limit, -1);
        }
        for (let i = 1; i < slots.length; i++) {
          const limit = slots[i - 1] + gap;
          if (slots[i] < limit) slots[i] = avoidPrice(limit, 1);
        }
        const boxes = [];
        sortedOv.forEach((o, k) => {
          const yy = y(o.value), ly = slots[k];
          x.strokeStyle = o.color || css('--gold'); x.setLineDash([5, 4]); x.lineWidth = 1.2;
          x.beginPath(); x.moveTo(l, yy); x.lineTo(l + pw, yy); x.stroke(); x.setLineDash([]);
          const lx = l + pw + (compactStacked ? 3 : 52);
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
      if (this.drawings) this.drawings.drawLines(x, this.geo);
      // 布林通道 20：畫在 K 棒後面，淡色帶加上下軌細線。連續有值的每一段各自成帶，缺值處斷開。
      const bandColor = css('--dim');
      for (let i = start; i < end;) {
        if (!this.boll[i]) { i++; continue; }
        let stop = i;
        while (stop < end && this.boll[stop]) stop++;
        const px = index => l + step * (index - start + .5);
        if (stop - i > 1) {
          x.fillStyle = bandColor; x.globalAlpha = .08; x.beginPath();
          x.moveTo(px(i), y(this.boll[i].upper));
          for (let j = i + 1; j < stop; j++) x.lineTo(px(j), y(this.boll[j].upper));
          for (let j = stop - 1; j >= i; j--) x.lineTo(px(j), y(this.boll[j].lower));
          x.fill(); x.globalAlpha = 1;
        }
        for (const edge of ['upper', 'lower']) {
          x.strokeStyle = bandColor; x.globalAlpha = .55; x.lineWidth = 1; x.beginPath();
          x.moveTo(px(i), y(this.boll[i][edge]));
          for (let j = i + 1; j < stop; j++) x.lineTo(px(j), y(this.boll[j][edge]));
          x.stroke(); x.globalAlpha = 1;
        }
        i = stop;
      }
      // K棒
      const bw = Math.max(1, Math.min(14, step * .66));
      vis.forEach((c, i) => {
        if (!hasPrice(c)) return;
        const cx = l + step * (i + .5), col = c.close >= c.open ? up : dn;
        x.strokeStyle = col; x.fillStyle = col; x.lineWidth = 1;
        x.beginPath(); x.moveTo(cx, y(c.high)); x.lineTo(cx, y(c.low)); x.stroke();
        const top = y(Math.max(c.open, c.close)), h = Math.max(1, Math.abs(y(c.open) - y(c.close)));
        if (this.opts.liveLast && start + i === n - 1) {
          x.globalAlpha = .45; x.fillRect(cx - bw / 2, top, bw, h); x.globalAlpha = 1;
          x.lineWidth = 1.4; x.strokeRect(cx - bw / 2 - 1.5, top - 1.5, bw + 3, h + 3);
        } else x.fillRect(cx - bw / 2, top, bw, h);
      });
      // 使用者自訂均線（最多四條，預設 MA18／MA45）。
      const maColors = [css('--accent'), css('--gold'), css('--dim'), css('--faint')];
      this.maPeriods.forEach((p, index) => {
        x.strokeStyle = maColors[index]; x.lineWidth = index < 2 ? 1.8 : 1.2; x.beginPath(); let on = false;
        for (let i = start; i < end; i++) { const v = this.ma[p][i]; if (v == null) { on = false; continue; } const px = l + step * (i - start + .5); on ? x.lineTo(px, y(v)) : x.moveTo(px, y(v)); on = true; }
        x.stroke();
      });
      // 最新價標籤
      const last = this.candles[n - 1];
      if (tags && end === n && hasPrice(last)) {
        const real = y(last.close), cs = tagSlots.find(g => g.close), py = cs ? cs.slot : real;
        x.strokeStyle = ink; x.lineWidth = 1; x.setLineDash([2, 3]);
        x.beginPath(); x.moveTo(l, Math.round(real) + .5); x.lineTo(l + pw, Math.round(real) + .5); x.stroke(); x.setLineDash([]);
        if (Math.abs(py - real) > 1) { x.beginPath(); x.moveTo(l + pw, real); x.lineTo(l + pw + 2, real); x.lineTo(tagX, py); x.stroke(); }
        x.fillStyle = ink; x.beginPath(); x.roundRect ? x.roundRect(tagX, py - tagH / 2, tagW, tagH, 4) : x.rect(tagX, py - tagH / 2, tagW, tagH); x.fill();
        x.fillStyle = css('--bg'); x.textBaseline = 'middle';
        x.font = '500 11px "Noto Sans TC","Microsoft JhengHei",sans-serif'; x.textAlign = 'left'; x.fillText('收盤', tagX + 6, py + .5);
        x.font = '600 11.5px "JetBrains Mono",Consolas,monospace'; x.textAlign = 'right'; x.fillText(fmtTag(last.close), tagX + tagW - 6, py + .5);
        x.textAlign = 'left'; x.font = '11.5px "JetBrains Mono",Consolas,monospace';
      } else if (end === n && hasPrice(last)) { const pw2 = stacked ? 46 : r - 4; x.fillStyle = ink; x.textBaseline = 'middle'; x.fillRect(l + pw + 2, y(last.close) - 9, pw2, 18); x.fillStyle = css('--bg'); x.fillText(fmt(last.close, 0), l + pw + 7, y(last.close)); }
      // 畫線的價位標示最後才畫：壓在 K 棒與現價虛線上面，數字才讀得到。
      if (this.drawings) this.drawings.drawLabels(x, this.geo);
      // 驗收用：右側刻度文字框與現價框的位置。
      this.debugBoxes = { axis: axisBoxes, price: end === n && hasPrice(last) ? { y: y(last.close) - 9, h: 18 } : null, height: H };
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
        const prev = this.candles[i - 1], chg = prev && Number.isFinite(c.close) && Number.isFinite(prev.close) ? c.close - prev.close : NaN, m = this.macd[i];
        const maText = this.maPeriods.map(p => this.ma[p][i] != null ? `MA${p} ${fmt(this.ma[p][i], 0)}` : '').filter(Boolean).join('　');
        const band = this.boll[i], bandDigits = band && band.upper >= 1000 ? 0 : 2;
        const bandText = band ? `布林 ${fmt(band.upper, bandDigits)}／${fmt(band.lower, bandDigits)}` : '';
        this.tip.innerHTML = `<b>${c.label}</b><span class="${chg < 0 ? 'dn' : 'upc'}">收 ${fmt(c.close)}　${Number.isFinite(chg) ? `${chg >= 0 ? '+' : '−'}${fmt(Math.abs(chg))}` : '漲跌缺值'}</span><span>開 ${fmt(c.open)}　高 ${fmt(c.high)}　低 ${fmt(c.low)}</span>` + (this.opts.volumeKind === 'volume'
          ? `<span>成交量 ${Number.isFinite(c.volume) ? `${fmt(c.volume / 1000)} 張（${fmt(c.volume, 0)} 股）` : '缺資料'}</span>`
          : Number.isFinite(c.amount) ? `<span>成交金額 ${fmt(c.amount, 2)} 億元</span>` : '') + (m.ready ? `<span>MACD柱 ${fmt(m.hist)}　DIF ${fmt(m.dif)}</span>` : '') + (maText ? `<span>${maText}</span>` : '') + (bandText ? `<span>${bandText}</span>` : '');
        this.tip.classList.add('show');
        const tw = Math.min(this.tip.offsetWidth, W - 8); const want = cx + 14 + tw > W ? cx - tw - 14 : cx + 14; this.tip.style.left = Math.max(4, Math.min(W - tw - 4, want)) + 'px'; this.tip.style.top = '8px'; // 手機窄圖：左右都夾在圖表內，不出界
      } else this.tip.classList.remove('show');
      if (this.studies) this.studies.draw();
    }
  }
  window.KChart = KChart;
  // 5 年歷史日線（欄位式：d 為 YYYYMMDD 整數）裡，比 bars 第一根更早的 K 棒。
  // 歷史必須涵蓋 bars 的第一根且收盤一致才接：還原係數不同步或資料不是同一檔時回空陣列，圖維持原樣。
  function olderBars(bars, history) {
    if (!Array.isArray(bars) || !bars.length || !history || history.schema !== 'stock-history/v1') return [];
    const columns = ['d', 'o', 'h', 'l', 'c', 'v'].map(key => history[key]);
    if (columns.some(column => !Array.isArray(column) || column.length !== history.d.length)) return [];
    const first = bars[0];
    const join = history.d.indexOf(Number(String(first.date).replaceAll('-', '')));
    if (join < 0 || Math.abs(history.c[join] - first.close) > 0.011) return [];
    return history.d.slice(0, join).map((day, i) => {
      const text = String(day);
      return { date: `${text.slice(0, 4)}-${text.slice(4, 6)}-${text.slice(6, 8)}`, open: history.o[i], high: history.h[i],
        low: history.l[i], close: history.c[i], volume: history.v[i] };
    });
  }

  window.KChartMath = { sma, ema, macd, bollinger, normalizePeriods, olderBars };
})();
