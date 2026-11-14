/* K 線圖的畫線層（個股頁的日／週／月 K）。由 KChart 建立；頁面只透過 KChart 的 setTool／setDrawings
   與 drawings.onChange／drawings.onTool 往來，座標換算與點擊判定都封在這裡。
   每個錨點存「交易日日期＋價格」，畫的時候才依圖表目前的可見範圍換算成像素，
   所以平移、縮放、切換週期、歷史接到前面、即時重畫都不需要改資料。 */
(function () {
  'use strict';
  const UNITS = new Set(['日', '週', '月']);   // 秒 K、分 K 只有當天資料，不畫跨日的線
  const ANCHOR_COUNT = { hline: 1 };           // 種類 → 錨點個數；之後的工具在這裡加
  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const DRAW_FALLBACK = '#4f5fd6';             // 頁面沒定義 --draw 時用的同色
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const priceText = value => value.toLocaleString('en-US', { maximumFractionDigits: 2 });
  const newId = () => (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
    : `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`);
  const copy = drawing => ({ id: drawing.id, kind: drawing.kind, anchors: drawing.anchors.map(({ date, price }) => ({ date, price })) });
  const valid = drawing => Boolean(drawing) && typeof drawing.id === 'string' && Object.hasOwn(ANCHOR_COUNT, drawing.kind)
    && Array.isArray(drawing.anchors) && drawing.anchors.length === ANCHOR_COUNT[drawing.kind]
    && drawing.anchors.every(anchor => anchor && DATE_RE.test(anchor.date) && Number.isFinite(anchor.price) && anchor.price > 0);

  class ChartDrawings {
    constructor(chart, opts) {
      this.chart = chart; this.opts = opts || {};
      this.items = []; this.tool = null;
    }
    get enabled() { return UNITS.has(this.chart.unit); }
    setTool(kind) {
      const next = this.enabled && Object.hasOwn(ANCHOR_COUNT, kind) ? kind : null;
      if (next === this.tool) return;
      this.tool = next;
      this.chart.canvas.style.cursor = this.idleCursor();
      if (this.opts.onTool) this.opts.onTool(next);
    }
    idleCursor() { return this.tool ? 'crosshair' : 'grab'; }
    load(list) { this.items = (Array.isArray(list) ? list : []).filter(valid).map(copy); }
    // 圖表換了資料（週期可能跟著換）：切到不能畫的週期時，選到一半的工具一併放下。
    dataChanged() { if (!this.enabled) this.setTool(null); }
    // 主圖上的一次點擊。回傳 true 表示這一下被畫線用掉了，圖表不再拿去選 K 棒。
    tap(x, y) {
      if (!this.tool || !this.enabled) return false;
      const anchor = this.anchorAt(x, y);
      if (!anchor) return true;   // 點在價格軸或時間軸上：不定位，工具留著
      const drawing = { id: newId(), kind: this.tool, anchors: [anchor] };
      this.items.push(drawing);
      this.setTool(null);
      if (this.opts.onChange) this.opts.onChange({ type: 'add', drawing: copy(drawing) });
      return true;
    }
    anchorAt(x, y) {
      const g = this.chart.geo;
      if (!g || !(g.hi > g.lo) || x < g.l || x > g.l + g.pw || y < g.t || y > g.t + g.ph) return null;
      const candle = this.chart.candles[Math.min(this.chart.view.end - 1, g.start + Math.floor((x - g.l) / g.step))];
      const date = candle ? String(candle.t).slice(0, 10) : '';
      const price = Math.round((g.hi - (y - g.t) / g.ph * (g.hi - g.lo)) * 100) / 100;
      return DATE_RE.test(date) && price > 0 ? { date, price } : null;
    }
    // 價位在目前畫面內才有 y；畫面外回 null（不畫殘影）。
    yOf(price, g) {
      const y = g.t + (g.hi - price) / (g.hi - g.lo) * g.ph;
      return y >= g.t && y <= g.t + g.ph ? y : null;
    }
    visible(g) {
      if (!this.enabled || !g || !(g.hi > g.lo)) return [];
      return this.items.map(drawing => ({ drawing, y: this.yOf(drawing.anchors[0].price, g) })).filter(item => item.y !== null);
    }
    // 線本身：畫在布林通道、K 棒、均線之前，不蓋住它們；只畫在繪圖區內，不進價格軸。
    drawLines(x, g) {
      const shown = this.visible(g);
      if (!shown.length) return;
      x.save();
      x.strokeStyle = css('--draw', DRAW_FALLBACK); x.lineWidth = 1.4; x.globalAlpha = 1; x.setLineDash([]);
      shown.forEach(({ y }) => {
        x.beginPath(); x.moveTo(g.l, Math.round(y) + .5); x.lineTo(g.l + g.pw, Math.round(y) + .5); x.stroke();
      });
      x.restore();
    }
    // 價位標示：貼在線的左端上方（太靠頂就改放下方），墊一塊底色讓數字壓在 K 棒上也讀得到。
    drawLabels(x, g) {
      const shown = this.visible(g);
      if (!shown.length) return;
      x.save();
      x.font = '500 11px "JetBrains Mono",Consolas,monospace'; x.textBaseline = 'middle'; x.textAlign = 'left';
      const color = css('--draw', DRAW_FALLBACK), paper = css('--bg', '#f3f1ec');
      shown.forEach(({ drawing, y }) => {
        const text = priceText(drawing.anchors[0].price), width = x.measureText(text).width + 8;
        const cy = y - 16 < g.t ? y + 10 : y - 9;
        x.globalAlpha = .86; x.fillStyle = paper; x.fillRect(g.l + 2, cy - 7, width, 14);
        x.globalAlpha = 1; x.fillStyle = color; x.fillText(text, g.l + 6, cy + .5);
      });
      x.restore();
    }
  }
  window.ChartDrawings = ChartDrawings;
})();
