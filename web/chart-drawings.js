/* K 線圖的畫線層（個股頁的日／週／月 K）。由 KChart 建立；頁面只透過 KChart 的 setTool／setDrawings／
   deleteSelectedDrawing／cancelDrawing 與 drawings.onChange／onTool／onSelect 往來，
   座標換算、命中判定、選取與拖動都封在這裡。
   每個錨點存「交易日日期＋價格」，畫的時候才依圖表目前的可見範圍換算成像素，
   所以平移、縮放、切換週期、歷史接到前面、即時重畫都不需要改資料。 */
(function () {
  'use strict';
  const UNITS = new Set(['日', '週', '月']);   // 秒 K、分 K 只有當天資料，不畫跨日的線
  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  const DRAW_FALLBACK = '#4f5fd6';             // 頁面沒定義 --draw 時用的同色
  const HIT_PX = { mouse: 6, touch: 14 };      // 可點選範圍：線的兩側各幾像素。手指比滑鼠寬
  const DRAG_START_PX = 3;                     // 按住後移動超過這個距離才算拖動（與圖表平移的門檻相同）
  const HANDLE_R = 4.5;
  const css = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const priceText = value => value.toLocaleString('en-US', { maximumFractionDigits: 2 });
  const newId = () => (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID()
    : `d${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`);
  const copy = drawing => ({ id: drawing.id, kind: drawing.kind, anchors: drawing.anchors.map(({ date, price }) => ({ date, price })) });
  const sameAnchors = (a, b) => a.length === b.length && a.every((anchor, i) => anchor.date === b[i].date && anchor.price === b[i].price);

  // g 是圖表目前的版面（KChart 的 geo）：繪圖區 l、t、pw、ph 與價格座標 lo～hi。
  // 價位在目前畫面內才有 y；畫面外回 null（不畫殘影）。
  function yOf(price, g) {
    const y = g.t + (g.hi - price) / (g.hi - g.lo) * g.ph;
    return y >= g.t - .5 && y <= g.t + g.ph + .5 ? y : null;
  }
  // 取到分，並收在畫面內最靠邊的那一分：價格座標的上下緣通常不在 0.01 的格點上，
  // 直接四捨五入會讓畫在邊緣或拖到邊緣的線落到畫面外而消失。畫面容不下任何一分時回 null。
  function priceInView(price, g) {
    const top = Math.floor(g.hi * 100 + 1e-6) / 100, bottom = Math.max(.01, Math.ceil(g.lo * 100 - 1e-6) / 100);
    return top >= bottom ? Math.min(top, Math.max(bottom, Math.round(price * 100) / 100)) : null;
  }
  const priceAt = (y, g) => priceInView(g.hi - (y - g.t) / g.ph * (g.hi - g.lo), g);

  // 各種畫線的形狀。選取、命中判定、拖動、刪除的機制是共用的，依種類不同的幾何放在這裡；之後的工具在這裡加一項。
  // （線與價位標示的畫法目前只有水平線一種，還寫在 drawLines／drawOnTop 裡。）
  //   anchors   錨點個數
  //   locate    目前畫面上的位置；不在畫面內回 null（不畫，也點不到）
  //   distance  指標離這條線幾像素
  //   handles   選取時把手的位置
  //   dragged   整條被拖了 (dx, dy) 像素之後的新錨點
  const SHAPES = {
    hline: {
      anchors: 1,
      locate(drawing, g) { const y = yOf(drawing.anchors[0].price, g); return y === null ? null : { y }; },
      distance: (at, x, y) => Math.abs(y - at.y),
      handles: (at, g) => [[g.l + g.pw / 2, Math.round(at.y) + .5]],
      // 只有價位跟著走；拖出繪圖區時停在邊緣。
      dragged(anchors, dx, dy, g) {
        const price = priceInView(anchors[0].price - dy / g.ph * (g.hi - g.lo), g);
        return [{ date: anchors[0].date, price: price === null ? anchors[0].price : price }];
      },
    },
  };
  const valid = drawing => Boolean(drawing) && typeof drawing.id === 'string' && Object.hasOwn(SHAPES, drawing.kind)
    && Array.isArray(drawing.anchors) && drawing.anchors.length === SHAPES[drawing.kind].anchors
    && drawing.anchors.every(anchor => anchor && DATE_RE.test(anchor.date) && Number.isFinite(anchor.price) && anchor.price > 0);

  class ChartDrawings {
    constructor(chart, opts) {
      this.chart = chart; this.opts = opts || {};
      this.items = []; this.tool = null;
      this.selectedId = null;   // 選取記的是識別碼：重畫、換週期、重新載入同一組線都不會選錯條
      this.move = null;         // 正在拖動的那一條：{ drawing, anchors（拖動前）, x, y（起點）, started }
    }
    get enabled() { return UNITS.has(this.chart.unit); }
    get selected() { return this.items.find(drawing => drawing.id === this.selectedId) || null; }
    setTool(kind) {
      const next = this.enabled && Object.hasOwn(SHAPES, kind) ? kind : null;
      if (next === this.tool) return;
      this.tool = next;
      if (next) this.select(null);   // 要畫新的線：放下原本選取的那一條
      this.chart.canvas.style.cursor = this.idleCursor();
      if (this.opts.onTool) this.opts.onTool(next);
    }
    idleCursor() { return this.tool ? 'crosshair' : 'grab'; }
    // 滑鼠停在線上時的游標：已選取的那一條可以拖，其他的可以點。
    cursorAt(x, y) {
      const hit = this.tool ? null : this.hit(x, y, 'mouse');
      return !hit ? this.idleCursor() : hit.id === this.selectedId ? 'move' : 'pointer';
    }
    select(id) {
      if (id === this.selectedId) return;
      this.selectedId = id;
      if (this.opts.onSelect) this.opts.onSelect(id === null ? null : copy(this.selected));
    }
    load(list) {
      const moving = this.move;
      this.items = (Array.isArray(list) ? list : []).filter(valid).map(copy);
      if (moving) {
        // 拖到一半時換了一組線（雲端那份晚一步讀回來）：同一條還在就接著拖，不在了這次拖動作廢。
        const again = this.items.find(drawing => drawing.id === moving.drawing.id);
        if (again) { again.anchors = moving.drawing.anchors; moving.drawing = again; } else this.move = null;
      }
      if (!this.selected) this.select(null);
    }
    // 圖表換了資料（週期可能跟著換）：切到不能畫的週期時，選到一半的工具、拖到一半與選取的線一併放下。
    dataChanged() { if (!this.enabled) this.cancel(); }
    // 放下工具並取消選取（Esc）；拖到一半的線回到原位。
    cancel() { this.drop(false); this.setTool(null); this.select(null); }
    // 刪除目前選取的那一條；沒有選取、或它不在畫面上（看不到的線不刪）時不做事。回傳有沒有刪。
    remove() {
      const drawing = this.selected;
      if (!drawing || !this.shown(drawing)) return false;
      this.move = null;
      this.items = this.items.filter(item => item !== drawing);
      this.select(null);
      if (this.opts.onChange) this.opts.onChange({ type: 'remove', drawing: copy(drawing) });
      return true;
    }
    // 主圖上的一次點擊。回傳 true 表示這一下被畫線用掉了，圖表不再拿去選 K 棒。
    tap(x, y, pointerType) {
      if (!this.enabled) return false;
      if (!this.tool) {
        // 一般模式：先看有沒有點到線。點到就選取；沒點到就取消選取，這一下還給圖表。
        const hit = this.hit(x, y, pointerType);
        this.select(hit ? hit.id : null);
        return Boolean(hit);
      }
      const anchor = this.anchorAt(x, y);
      if (!anchor) return true;   // 點在價格軸或時間軸上：不定位，工具留著
      const drawing = { id: newId(), kind: this.tool, anchors: [anchor] };
      this.items.push(drawing);
      this.setTool(null);
      if (this.opts.onChange) this.opts.onChange({ type: 'add', drawing: copy(drawing) });
      return true;
    }
    // 按下的位置在已選取的線上：這次拖曳是移動那條線。回傳 true 時圖表不平移。
    grab(x, y, pointerType) {
      const drawing = this.tool ? null : this.selected;
      if (!drawing || this.distanceTo(drawing, x, y) > this.reach(pointerType)) return false;
      this.move = { drawing, anchors: drawing.anchors, x, y, started: false };
      return true;
    }
    // 回傳這次按住到目前為止算不算拖動（超過門檻才算；沒超過的話放開時仍是一次點擊）。
    dragTo(x, y) {
      const move = this.move, g = this.chart.geo;
      if (!move) return false;
      if (!this.inView(g)) return move.started;
      const dx = x - move.x, dy = y - move.y;
      if (!move.started && Math.hypot(dx, dy) <= DRAG_START_PX) return false;
      move.started = true;
      move.drawing.anchors = SHAPES[move.drawing.kind].dragged(move.anchors, dx, dy, g);
      return true;
    }
    // 放開：commit 為 false（手勢被取消、變成雙指縮放、按了 Esc）時回到拖動前的位置。
    drop(commit) {
      const move = this.move;
      if (!move) return;
      this.move = null;
      if (!commit) { move.drawing.anchors = move.anchors; return; }
      if (!sameAnchors(move.drawing.anchors, move.anchors) && this.opts.onChange) {
        this.opts.onChange({ type: 'update', drawing: copy(move.drawing) });
      }
    }
    reach(pointerType) { return pointerType === 'mouse' ? HIT_PX.mouse : HIT_PX.touch; }
    inView(g) { return Boolean(this.enabled && g && g.hi > g.lo); }
    locate(drawing) { const g = this.chart.geo; return this.inView(g) ? SHAPES[drawing.kind].locate(drawing, g) : null; }
    shown(drawing) { return Boolean(this.locate(drawing)); }
    distanceTo(drawing, x, y) {
      const g = this.chart.geo, at = this.locate(drawing);
      return at && x >= g.l && x <= g.l + g.pw ? SHAPES[drawing.kind].distance(at, x, y) : Infinity;
    }
    // 指標位置點到哪一條線：可點選範圍內最近的一條；一樣近時取後畫的（疊在上面的那一條）。
    hit(x, y, pointerType) {
      let best = null, bestDistance = this.reach(pointerType);
      for (const drawing of this.items) {
        const distance = this.distanceTo(drawing, x, y);
        if (distance <= bestDistance) { best = drawing; bestDistance = distance; }
      }
      return best;
    }
    anchorAt(x, y) {
      const g = this.chart.geo;
      if (!g || !(g.hi > g.lo) || x < g.l || x > g.l + g.pw || y < g.t || y > g.t + g.ph) return null;
      const candle = this.chart.candles[Math.min(this.chart.view.end - 1, g.start + Math.floor((x - g.l) / g.step))];
      const date = candle ? String(candle.t).slice(0, 10) : '';
      const price = priceAt(y, g);
      return DATE_RE.test(date) && price !== null ? { date, price } : null;
    }
    visible() {
      return this.items.map(drawing => ({ drawing, at: this.locate(drawing) })).filter(item => item.at);
    }
    // 線本身：畫在布林通道、K 棒、均線之前，不蓋住它們；只畫在繪圖區內，不進價格軸。選取的那一條略粗。
    drawLines(x, g) {
      const shown = this.visible();
      // 選取的線移出畫面（平移、縮放後價位不在可見範圍）就解除選取：看不到的線不該還能被刪。
      if (this.selectedId !== null && !shown.some(({ drawing }) => drawing.id === this.selectedId)) this.select(null);
      if (!shown.length) return;
      x.save();
      x.strokeStyle = css('--draw', DRAW_FALLBACK); x.globalAlpha = 1; x.setLineDash([]);
      shown.forEach(({ drawing, at }) => {
        x.lineWidth = drawing.id === this.selectedId ? 2.2 : 1.4;
        x.beginPath(); x.moveTo(g.l, Math.round(at.y) + .5); x.lineTo(g.l + g.pw, Math.round(at.y) + .5); x.stroke();
      });
      x.restore();
    }
    // 最後才畫、壓在 K 棒與現價虛線上面的部分：價位標示與選取把手。
    drawOnTop(x, g) {
      const shown = this.visible();
      if (!shown.length) return;
      x.save();
      x.font = '500 11px "JetBrains Mono",Consolas,monospace'; x.textBaseline = 'middle'; x.textAlign = 'left';
      const color = css('--draw', DRAW_FALLBACK), paper = css('--bg', '#f3f1ec');
      // 價位標示：貼在線的左端上方（太靠頂就改放下方），墊一塊底色讓數字壓在 K 棒上也讀得到。
      shown.forEach(({ drawing, at }) => {
        const text = priceText(drawing.anchors[0].price), width = x.measureText(text).width + 8;
        const cy = at.y - 16 < g.t ? at.y + 10 : at.y - 9;
        x.globalAlpha = .86; x.fillStyle = paper; x.fillRect(g.l + 2, cy - 7, width, 14);
        x.globalAlpha = 1; x.fillStyle = color; x.fillText(text, g.l + 6, cy + .5);
      });
      // 選取把手：底色實心、畫線色描邊的小圓。
      const picked = shown.find(({ drawing }) => drawing.id === this.selectedId);
      if (picked) {
        x.globalAlpha = 1; x.setLineDash([]); x.lineWidth = 1.6; x.fillStyle = paper; x.strokeStyle = color;
        SHAPES[picked.drawing.kind].handles(picked.at, g).forEach(([hx, hy]) => {
          x.beginPath(); x.arc(hx, hy, HANDLE_R, 0, Math.PI * 2); x.fill(); x.stroke();
        });
      }
      x.restore();
    }
  }
  window.ChartDrawings = ChartDrawings;
})();
