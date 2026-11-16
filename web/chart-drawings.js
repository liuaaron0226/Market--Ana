/* K 線圖的畫線層（個股頁的日／週／月 K）。由 KChart 建立；頁面只透過 KChart 的 setTool／setDrawings／
   deleteSelectedDrawing／cancelDrawing 與 drawings.onChange／onTool／onSelect 往來，
   座標換算、命中判定、選取與拖動都封在這裡。
   種類：水平線（一個錨點，點一下）、趨勢線、射線、矩形與黃金分割（兩個錨點，點兩下；第一下之後有預覽；射線一律往右延伸）。
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
  const MIN_SPAN_PX = 4;                       // 兩點在畫面上至少要隔這麼遠才成一條線（連點兩下不會畫出看不見的線）
  const LABEL_H = 14;                          // 線旁標示（墊底色的那一塊）的高度
  const MAX_PRICE = 10000000;                  // 與儲存端相同的價格上限
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
  // 不管在不在畫面內的 y：斜線的端點可以在畫面外，畫的時候再裁到繪圖區。
  const yRaw = (price, g) => g.t + (g.hi - price) / (g.hi - g.lo) * g.ph;

  const dateOf = candle => String(candle.t).slice(0, 10);
  // 一根 K 棒涵蓋的第一天：週 K 是那一週的星期一，月 K 是那個月的一號（K 棒自己的日期是期間內最後一個交易日）。
  function periodStart(date, unit) {
    if (unit === '月') return `${date.slice(0, 7)}-01`;
    if (unit !== '週') return date;
    const day = new Date(`${date}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - (day.getUTCDay() || 7) + 1);
    return day.toISOString().slice(0, 10);
  }
  // 這個日期落在第幾根：包含它的那一根（日期不早於它的第一根）。比最後一根還晚就排在最後一根的下一格；
  // 比圖上第一根還早（五年歷史還沒接上）回 null——位置算不出來的線先不畫，不能畫成歪的。
  function indexOfDate(chart, date) {
    const candles = chart.candles;
    if (!candles.length || date < periodStart(dateOf(candles[0]), chart.unit)) return null;
    let low = 0, high = candles.length;
    while (low < high) {
      const mid = (low + high) >> 1;
      if (dateOf(candles[mid]) < date) low = mid + 1; else high = mid;
    }
    return low;
  }
  // 錨點在畫面座標上的位置 [x, y]（可以在繪圖區外）；x 是那一根的正中央。算不出來回 null。
  function pointOf(anchor, g, chart) {
    const index = indexOfDate(chart, anchor.date);
    return index === null ? null : [g.l + g.step * (index - g.start + .5), yRaw(anchor.price, g)];
  }

  const inPlot = ([x, y], g) => x >= g.l && x <= g.l + g.pw && y >= g.t && y <= g.t + g.ph;
  // 從 a 往 b 的直線落在繪圖區內的那一段（Liang–Barsky）；整段都在外面回 null。
  // extend 是沿著 a→b 最遠走到幾倍：1＝只到 b（線段），Infinity＝穿過 b 一直走到邊緣（射線）。
  function clipToPlot(a, b, g, extend) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    let from = 0, to = extend;
    for (const [p, q] of [[-dx, a[0] - g.l], [dx, g.l + g.pw - a[0]], [-dy, a[1] - g.t], [dy, g.t + g.ph - a[1]]]) {
      if (p === 0) { if (q < 0) return null; continue; }
      const r = q / p;
      if (p < 0) { if (r > to) return null; from = Math.max(from, r); }
      else { if (r < from) return null; to = Math.min(to, r); }
    }
    return Number.isFinite(to) ? [[a[0] + from * dx, a[1] + from * dy], [a[0] + to * dx, a[1] + to * dy]] : null;
  }

  // 點 (x, y) 到線段 a–b 的距離。
  function distanceToSegment(x, y, [ax, ay], [bx, by]) {
    const dx = bx - ax, dy = by - ay, length2 = dx * dx + dy * dy;
    const along = length2 ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / length2)) : 0;
    return Math.hypot(x - ax - along * dx, y - ay - along * dy);
  }
  const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
  const cents = price => Math.round(price * 100) / 100;
  // 畫面上 (x, y) 這個位置的錨點：所在那一根的日期＋取到分的價位。不在繪圖區內回 null。
  function anchorAt(x, y, g, chart) {
    if (!inPlot([x, y], g)) return null;
    const candle = chart.candles[Math.min(chart.view.end - 1, g.start + Math.floor((x - g.l) / g.step))];
    const date = candle ? dateOf(candle) : '';
    const price = priceAt(y, g);
    return DATE_RE.test(date) && price !== null ? { date, price } : null;
  }
  // 標在線的上方時，標示上下的正中央（太靠繪圖區頂端就改放線的下方）。
  const labelAbove = (y, g) => y - 16 < g.t ? y + 10 : y - 9;
  // 兩個錨點在目前畫面上是不是近到連不成一條看得到的線（位置算不出來也算）。
  function tooClose(a, b, g, chart) {
    const from = pointOf(a, g, chart), to = pointOf(b, g, chart);
    return !from || !to || Math.hypot(to[0] - from[0], to[1] - from[1]) < MIN_SPAN_PX;
  }

  // 兩點定位的畫線（趨勢線、射線；矩形沿用其中的整條平移，黃金分割沿用整條平移與兩端的把手）共用的部分：
  // 兩個錨點各自換算位置、兩端各一個可以單獨拖的把手、整條平移時兩端一起走。
  // extend：畫到第二點為止（1），還是穿過兩點一直往右延伸（Infinity）。
  const twoPoint = extend => ({
    anchors: 2,
    tooSmall: tooClose,
    // 兩個錨點各自的畫面位置 a、b（可以在畫面外），與畫面內要畫的那一段 seg。
    locate(drawing, g, chart) {
      const a = pointOf(drawing.anchors[0], g, chart), b = pointOf(drawing.anchors[1], g, chart);
      if (!a || !b) return null;
      // 兩端在這個週期落在同一點（日 K 上同一週內的短線切到週 K）：畫成一小段橫線，看得到也點得到。
      if (Math.hypot(b[0] - a[0], b[1] - a[1]) < 1) return inPlot(a, g) ? { a, b, seg: [[a[0] - 2, a[1]], [a[0] + 2, a[1]]] } : null;
      // 射線永遠往右（本人 2026-11-16 定案）：從畫面上靠左的那一點穿過靠右的那一點；兩點同一根時才照點的順序（直的線）。
      const [from, to] = extend === Infinity && b[0] < a[0] ? [b, a] : [a, b];
      const seg = clipToPlot(from, to, g, extend);
      return seg ? { a, b, seg } : null;
    },
    path: at => at.seg,
    distance: (at, x, y) => distanceToSegment(x, y, at.seg[0], at.seg[1]),
    // 把手在錨點上（在畫面內的才有）；第三個值是它拖的是第幾個錨點。
    handles: (at, g) => [[...at.a, 0], [...at.b, 1]].filter(point => inPlot(point, g)),
    // 整條平移：兩端移動同樣的根數與價差。碰到第一根或最後一根 K 棒就停，線的長度與斜率不變。
    dragged(anchors, dx, dy, g, chart) {
      const indexes = anchors.map(anchor => indexOfDate(chart, anchor.date)), last = chart.candles.length - 1;
      if (indexes.includes(null)) return null;
      const bars = clamp(Math.round(dx / g.step), -Math.min(...indexes), Math.max(0, last - Math.max(...indexes)));
      const prices = anchors.map(anchor => anchor.price);
      const shift = clamp(-dy / g.ph * (g.hi - g.lo), .01 - Math.min(...prices), MAX_PRICE - Math.max(...prices));
      // 左右沒有動就保留原本的日期：週、月 K 一根涵蓋好幾天，不能因為上下拖了一下就把日期換成那一根的最後一天。
      const next = anchors.map((anchor, i) => ({
        date: bars ? dateOf(chart.candles[Math.min(last, indexes[i] + bars)]) : anchor.date, price: cents(anchor.price + shift),
      }));
      return next[0].date === next[1].date && next[0].price === next[1].price ? null : next;   // 兩端會變成同一點的平移不成立
    },
    // 只拖其中一端：那一端移到「原本的位置＋拖的距離」所在的那一根與價位（不出繪圖區），另一端不動。
    // 拖到和另一端連不成線的位置時回 null（那一端留在上一個位置）。
    reshaped(anchors, index, dx, dy, g, chart) {
      const from = pointOf(anchors[index], g, chart);
      const moved = from && anchorAt(clamp(from[0] + dx, g.l, g.l + g.pw), clamp(from[1] + dy, g.t, g.t + g.ph), g, chart);
      if (!moved || this.tooSmall(moved, anchors[1 - index], g, chart)) return null;
      // 還在原本那一根裡就保留原本的日期（週、月 K 一根涵蓋好幾天）。
      if (indexOfDate(chart, moved.date) === indexOfDate(chart, anchors[index].date)) moved.date = anchors[index].date;
      return anchors.map((anchor, i) => i === index ? moved : anchor);
    },
  });

  // 矩形：兩個錨點是對角（畫完時整理成左上、右下；之後拖角落可以拖過對角，位置一律用兩點的最小最大值算）。
  // 四個角各一個把手，把手編號決定它動的是哪個錨點的日期與哪個錨點的價位。
  const RECT_CORNERS = [[0, 0], [1, 1], [0, 1], [1, 0]];   // [日期取第幾個錨點, 價位取第幾個錨點]
  const rectangle = {
    ...twoPoint(1),
    // 畫完時整理成「左上、右下」：不論先點哪一角、往哪個方向拉，存下來的都是同一個區間。
    normalize: ([a, b]) => [
      { date: a.date < b.date ? a.date : b.date, price: Math.max(a.price, b.price) },
      { date: a.date < b.date ? b.date : a.date, price: Math.min(a.price, b.price) },
    ],
    // box 是畫面內要填色的範圍；edges 是四個邊（上、右、下、左）落在畫面內的部分——在畫面外的邊不畫，也不沿著繪圖區邊緣補線。
    locate(drawing, g, chart) {
      const a = pointOf(drawing.anchors[0], g, chart), b = pointOf(drawing.anchors[1], g, chart);
      if (!a || !b) return null;
      const x0 = Math.min(a[0], b[0]), x1 = Math.max(a[0], b[0]), y0 = Math.min(a[1], b[1]), y1 = Math.max(a[1], b[1]);
      const left = Math.max(x0, g.l), right = Math.min(x1, g.l + g.pw), top = Math.max(y0, g.t), bottom = Math.min(y1, g.t + g.ph);
      if (left > right || top > bottom) return null;
      // 兩個角在這個週期落在同一點：和線一樣畫成一小段橫線，看得到也點得到。
      const edges = x1 - x0 < 1 && y1 - y0 < 1 ? [[[x0 - 2, y0], [x0 + 2, y0]]]
        : [[[x0, y0], [x1, y0]], [[x1, y0], [x1, y1]], [[x1, y1], [x0, y1]], [[x0, y1], [x0, y0]]].map(([from, to]) => clipToPlot(from, to, g, 1)).filter(Boolean);
      return { a, b, edges, box: [left, top, right - left, bottom - top] };
    },
    paths: at => at.edges,
    fill: at => at.box,
    // 離最近的邊線幾像素：只有點邊線才選取（本人 2026-11-16 定案），框裡的 K 棒照常可以點。
    distance: (at, x, y) => Math.min(Infinity, ...at.edges.map(([from, to]) => distanceToSegment(x, y, from, to))),
    // 已選取之後，從框的裡面也能拖動整個框。四個邊都在畫面外（框比畫面大）時不算：不然整張圖怎麼拖都是在拖它。
    contains(at, x, y) {
      const [left, top, width, height] = at.box;
      return at.edges.length > 0 && x >= left && x <= left + width && y >= top && y <= top + height;
    },
    // 要有寬也有高才是框：沒有寬或沒有高的框四個角兩兩重疊，角落抓不到。
    tooSmall(a, b, g, chart) {
      const from = pointOf(a, g, chart), to = pointOf(b, g, chart);
      return !from || !to || Math.abs(to[0] - from[0]) < MIN_SPAN_PX || Math.abs(to[1] - from[1]) < MIN_SPAN_PX;
    },
    handles: (at, g) => RECT_CORNERS.map(([d, p], corner) => [[at.a, at.b][d][0], [at.a, at.b][p][1], corner]).filter(point => inPlot(point, g)),
    reshaped(anchors, corner, dx, dy, g, chart) {
      const [d, p] = RECT_CORNERS[corner], dateFrom = pointOf(anchors[d], g, chart), priceFrom = pointOf(anchors[p], g, chart);
      const moved = dateFrom && priceFrom && anchorAt(clamp(dateFrom[0] + dx, g.l, g.l + g.pw), clamp(priceFrom[1] + dy, g.t, g.t + g.ph), g, chart);
      if (!moved) return null;
      const next = anchors.map(({ date, price }) => ({ date, price }));
      // 還在原本那一根裡就保留原本的日期（週、月 K 一根涵蓋好幾天）。
      if (indexOfDate(chart, moved.date) !== indexOfDate(chart, anchors[d].date)) next[d].date = moved.date;
      next[p].price = moved.price;
      return this.tooSmall(next[0], next[1], g, chart) ? null : next;
    },
  };

  // 黃金分割：第一個錨點是 1、第二個是 0（照點的順序存，不重排——比例的方向跟著拉的方向），其餘依比例落在兩者之間。
  // 每條橫線從靠左的那一點畫到繪圖區右緣，方便對照之後的走勢；兩點之間另有一條細虛線，看得出量的是哪一段。
  const FIB_RATIOS = [0, .236, .382, .5, .618, .786, 1];
  const FIB_LABEL_ORDER = [0, 1, .618, .382, .5, .236, .786];   // 線擠在一起、標示放不下時，先標排在前面的（頭尾，再來是黃金比例）
  const fibonacci = {
    ...twoPoint(1),
    // levels 是價位在畫面內的那幾條（比例、價位、y）；guide 是兩點連線落在畫面內的那一段。都不在畫面內就不畫。
    locate(drawing, g, chart) {
      const a = pointOf(drawing.anchors[0], g, chart), b = pointOf(drawing.anchors[1], g, chart);
      if (!a || !b) return null;
      const from = Math.max(g.l, Math.min(a[0], b[0])), right = g.l + g.pw;
      if (from > right) return null;
      const [one, zero] = drawing.anchors.map(anchor => anchor.price);
      const levels = FIB_RATIOS.map(ratio => ({ ratio, price: zero + ratio * (one - zero) }))
        .map(level => ({ ...level, y: yOf(level.price, g) })).filter(level => level.y !== null);
      const guide = clipToPlot(a, b, g, 1);
      return levels.length || guide ? { a, b, from, right, levels, guide } : null;
    },
    paths: at => at.levels.map(({ y }) => [[at.from, Math.round(y) + .5], [at.right, Math.round(y) + .5]]),
    guide: at => at.guide,
    // 每條線標「比例 (價位)」；和已經標的重疊就不標。標示都在線的左端或更左，不會進右側的價格軸。
    // 位置：貼在線的左端外側、上下對齊那條線；左邊放不下最寬的那個時，整組改貼繪圖區左緣、
    // 標在各自那條線的上方（和水平線的價位一樣），不壓住線頭與把手。
    labels(drawing, at, g, widthOf) {
      const wanted = FIB_LABEL_ORDER.map(ratio => at.levels.find(level => level.ratio === ratio)).filter(Boolean)
        .map(level => ({ y: level.y, text: `${level.ratio} (${priceText(level.price)})` }));
      const outside = at.from - 4 - Math.max(0, ...wanted.map(({ text }) => widthOf(text))) >= g.l + 2;
      const placed = [];
      for (const { y, text } of wanted) {
        const cy = outside ? clamp(y, g.t + LABEL_H / 2, g.t + g.ph - LABEL_H / 2) : labelAbove(y, g);
        if (!placed.some(label => Math.abs(label.cy - cy) < LABEL_H)) placed.push({ text, cy, x: outside ? at.from - 4 - widthOf(text) : g.l + 2 });
      }
      return placed;
    },
    distance: (at, x, y) => Math.min(Infinity, ...(at.guide ? [distanceToSegment(x, y, ...at.guide)] : []),
      ...at.levels.map(level => distanceToSegment(x, y, [at.from, level.y], [at.right, level.y]))),
    // 兩點要有價差才分得出七條線：同一價位（或近到疊在一起）不成立。
    tooSmall(a, b, g, chart) {
      const from = pointOf(a, g, chart), to = pointOf(b, g, chart);
      return !from || !to || Math.abs(to[1] - from[1]) < MIN_SPAN_PX;
    },
  };

  // 各種畫線的形狀。選取、命中判定、拖動、刪除的機制是共用的，依種類不同的幾何放在這裡；之後的工具在這裡加一項。
  //   anchors   錨點個數（兩個的：點兩下才畫完）
  //   locate    目前畫面上的位置；不在畫面內回 null（不畫，也點不到）
  //   path      要畫的折線各點；要畫好幾段的改給 paths（每段一條折線）
  //   guide     襯在後面的輔助線（細虛線；沒有就不畫）
  //   fill      要填底色的範圍 [x, y, 寬, 高]（沒有就不填）
  //   normalize 畫完時把兩個錨點整理成固定的順序（沒有就照點的順序）
  //   labels    線旁要標的文字 [{ text, x, cy }]：x 是墊底那一塊的左緣，cy 是上下的正中央（沒有就不標）；widthOf(text) 回墊底那一塊的寬度
  //   distance  指標離這條線幾像素
  //   contains  已選取時，指標在不在它裡面（在的話也能拖動整個；沒有就只能從線上拖）
  //   handles   選取時把手的位置 [x, y]；帶第三個值的把手可以單獨拖，那個值是把手編號（線、黃金分割：第幾個錨點；矩形：第幾個角）
  //   dragged   整條被拖了 (dx, dy) 像素之後的新錨點
  //   reshaped  只有某個把手被拖了 (dx, dy) 像素之後的新錨點（有可單獨拖的把手才需要）
  //   tooSmall  兩個錨點在目前畫面上是不是近到不成形（兩點定位的預設是看兩點的直線距離）；畫第二下與拖把手時都用它
  //   dragged、reshaped 回 null 表示這一步不成立，線留在上一個位置。
  const SHAPES = {
    trend: twoPoint(1),
    ray: twoPoint(Infinity),
    rect: rectangle,
    fib: fibonacci,
    hline: {
      anchors: 1,
      locate(drawing, g) { const y = yOf(drawing.anchors[0].price, g); return y === null ? null : { y }; },
      path: (at, g) => [[g.l, Math.round(at.y) + .5], [g.l + g.pw, Math.round(at.y) + .5]],
      // 價位標在線的左端上方（太靠頂就改放下方）。
      labels: (drawing, at, g) => [{ text: priceText(drawing.anchors[0].price), x: g.l + 2, cy: labelAbove(at.y, g) }],
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
    && drawing.anchors.every(anchor => anchor && DATE_RE.test(anchor.date) && Number.isFinite(anchor.price) && anchor.price > 0)
    // 兩個錨點完全相同的線在任何週期、任何縮放下都只是一個點：看不見也選不到，不收。
    && !(drawing.anchors.length === 2 && drawing.anchors[0].date === drawing.anchors[1].date && drawing.anchors[0].price === drawing.anchors[1].price);

  class ChartDrawings {
    constructor(chart, opts) {
      this.chart = chart; this.opts = opts || {};
      this.items = []; this.tool = null;
      this.pending = null;      // 兩點工具點了第一下、還沒點第二下時的起點錨點
      this.pointer = null;      // 游標或手指最後在主圖上的位置（預覽線的另一端）
      this.selectedId = null;   // 選取記的是識別碼：重畫、換週期、重新載入同一組線都不會選錯條
      this.move = null;         // 正在拖動的那一條：{ drawing, anchors（拖動前）, x, y（起點）, started, grip（只拖哪個把手；null＝整條） }
    }
    get enabled() { return UNITS.has(this.chart.unit); }
    get selected() { return this.items.find(drawing => drawing.id === this.selectedId) || null; }
    setTool(kind) {
      const next = this.enabled && Object.hasOwn(SHAPES, kind) ? kind : null;
      if (next === this.tool) return;
      this.tool = next; this.pending = null;   // 換工具或放下工具：點到一半的那條不留
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
      // 兩點定位：第一下只記起點，第二下才畫出來；第二下和起點落在同一位置時不算，等下一下。
      // 起點在目前的圖上已經算不出位置（資料換成不含那一天的範圍）：這一下重新當第一下。
      if (this.pending && !pointOf(this.pending, this.chart.geo, this.chart)) this.pending = null;
      if (SHAPES[this.tool].anchors === 2 && !this.pending) { this.pending = anchor; return true; }
      if (this.pending && this.tooClose(this.pending, anchor)) return true;
      const shape = SHAPES[this.tool], anchors = this.pending ? [this.pending, anchor] : [anchor];
      const drawing = { id: newId(), kind: this.tool, anchors: shape.normalize ? shape.normalize(anchors) : anchors };
      this.items.push(drawing);
      this.setTool(null);
      if (this.opts.onChange) this.opts.onChange({ type: 'add', drawing: copy(drawing) });
      return true;
    }
    // 按下的位置在已選取的線上：這次拖曳是移動那條線。回傳 true 時圖表不平移。
    // 按在可單獨拖的把手上是只拖那一端，按在線身上是整條平移。
    grab(x, y, pointerType) {
      const drawing = this.tool ? null : this.selected, reach = this.reach(pointerType);
      const at = drawing && this.locate(drawing), shape = drawing && SHAPES[drawing.kind];
      if (!at || !(this.distanceTo(drawing, x, y) <= reach || (shape.contains && shape.contains(at, x, y)))) return false;
      const grips = shape.handles(at, this.chart.geo).filter(handle => handle.length > 2);
      // 把手的可抓範圍不超過最近兩個把手間距的三分之一：再短的線、再小的框，中段也留給整條平移。
      const span = Math.min(Infinity, ...grips.flatMap((one, i) => grips.slice(i + 1).map(other => Math.hypot(other[0] - one[0], other[1] - one[1]))));
      const near = grips.map(([hx, hy, grip]) => ({ grip, distance: Math.hypot(x - hx, y - hy) }))
        .filter(grip => grip.distance <= Math.min(reach, span / 3)).sort((a, b) => a.distance - b.distance)[0];
      this.move = { drawing, anchors: drawing.anchors, x, y, started: false, grip: near ? near.grip : null };
      return true;
    }
    // 回傳這次按住到目前為止算不算拖動（超過門檻才算；沒超過的話放開時仍是一次點擊）。
    dragTo(x, y) {
      const move = this.move, g = this.chart.geo;
      if (!move) return false;
      if (!this.inView(g)) return move.started;
      // 手拖出繪圖區時當作停在邊緣：線不會被整條拖到看不見的地方。
      const dx = clamp(x, g.l, g.l + g.pw) - move.x, dy = clamp(y, g.t, g.t + g.ph) - move.y;
      if (!move.started && Math.hypot(dx, dy) <= DRAG_START_PX) return false;
      move.started = true;
      const shape = SHAPES[move.drawing.kind];
      const next = move.grip === null ? shape.dragged(move.anchors, dx, dy, g, this.chart)
        : shape.reshaped(move.anchors, move.grip, dx, dy, g, this.chart);
      if (next) move.drawing.anchors = next;
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
    // 記下游標或手指在主圖上的位置。回傳畫面上有沒有跟著它走的預覽線（有的話圖表要重畫）。
    trackPointer(x, y) { this.pointer = { x, y }; return Boolean(this.pending); }
    // 兩個錨點在目前畫面上是不是近到不成形（線：連不成看得到的線；矩形：沒有寬或沒有高；黃金分割：沒有價差）。
    tooClose(a, b) { return SHAPES[this.tool].tooSmall(a, b, this.chart.geo, this.chart); }
    // 點了第一下之後的預覽：從起點到游標所在的那一根與價位（就是第二下點下去會畫出來的樣子）。
    preview() {
      const g = this.chart.geo;
      if (!this.pending || !this.pointer || !this.inView(g)) return null;
      const anchor = this.anchorAt(this.pointer.x, this.pointer.y);
      if (!anchor || this.tooClose(this.pending, anchor)) return null;
      return SHAPES[this.tool].locate({ anchors: [this.pending, anchor] }, g, this.chart);
    }
    reach(pointerType) { return pointerType === 'mouse' ? HIT_PX.mouse : HIT_PX.touch; }
    inView(g) { return Boolean(this.enabled && g && g.hi > g.lo); }
    locate(drawing) { const g = this.chart.geo; return this.inView(g) ? SHAPES[drawing.kind].locate(drawing, g, this.chart) : null; }
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
    anchorAt(x, y) { const g = this.chart.geo; return g && g.hi > g.lo ? anchorAt(x, y, g, this.chart) : null; }
    visible() {
      return this.items.map(drawing => ({ drawing, at: this.locate(drawing) })).filter(item => item.at);
    }
    // 線本身：畫在布林通道、K 棒、均線之前，不蓋住它們；只畫在繪圖區內，不進價格軸。選取的那一條略粗。
    drawLines(x, g) {
      const shown = this.visible();
      // 選取的線移出畫面（平移、縮放後價位不在可見範圍）就解除選取：看不到的線不該還能被刪。
      if (this.selectedId !== null && !shown.some(({ drawing }) => drawing.id === this.selectedId)) this.select(null);
      const preview = this.preview();
      if (!shown.length && !preview) return;
      const stroke = paths => {
        x.beginPath();
        paths.forEach(points => points.forEach(([px, py], index) => { if (index) x.lineTo(px, py); else x.moveTo(px, py); }));
        x.stroke();
      };
      // 先填底色（很淡，疊在後面畫的布林通道、K 棒、均線底下），再畫輔助線（虛線、比主線細一點、淡一半），最後描線。
      // width、dash 是主線的粗細與虛實，opacity 是整個的濃淡。
      const paint = (kind, at, width, opacity, dash) => {
        const shape = SHAPES[kind], guide = shape.guide && shape.guide(at);
        if (shape.fill) { x.globalAlpha = opacity * .1; x.fillRect(...shape.fill(at, g)); }
        if (guide) { x.globalAlpha = opacity * .5; x.lineWidth = width - .4; x.setLineDash([3, 3]); stroke([guide]); }
        x.globalAlpha = opacity; x.lineWidth = width; x.setLineDash(dash);
        stroke(shape.paths ? shape.paths(at, g) : [shape.path(at, g)]);
      };
      x.save();
      x.strokeStyle = x.fillStyle = css('--draw', DRAW_FALLBACK);
      shown.forEach(({ drawing, at }) => paint(drawing.kind, at, drawing.id === this.selectedId ? 2.2 : 1.4, 1, []));
      // 預覽：虛線、淡一點，和已經畫好的分得開。
      if (preview) paint(this.tool, preview, 1.4, .85, [5, 4]);
      x.restore();
    }
    // 最後才畫、壓在 K 棒與現價虛線上面的部分：線旁的標示與選取把手。
    drawOnTop(x, g) {
      const shown = this.visible();
      // 兩點工具點了第一下：起點先標一個記號（手機上沒有游標，要看得出第一下有點到）。
      const first = this.pending && this.inView(g) ? pointOf(this.pending, g, this.chart) : null;
      const marks = first && inPlot(first, g) ? [first] : [];
      if (!shown.length && !marks.length) return;
      x.save();
      x.font = '500 11px "JetBrains Mono",Consolas,monospace'; x.textBaseline = 'middle'; x.textAlign = 'left';
      const color = css('--draw', DRAW_FALLBACK), paper = css('--bg', '#f3f1ec');
      // 線旁的標示（價位、比例）：墊一塊底色讓字壓在 K 棒上也讀得到。位置由各種畫線自己決定。
      const widthOf = text => x.measureText(text).width + 8;
      shown.forEach(({ drawing, at }) => {
        const shape = SHAPES[drawing.kind];
        if (!shape.labels) return;
        shape.labels(drawing, at, g, widthOf).forEach(({ text, x: left, cy }) => {
          x.globalAlpha = .86; x.fillStyle = paper; x.fillRect(left, cy - LABEL_H / 2, widthOf(text), LABEL_H);
          x.globalAlpha = 1; x.fillStyle = color; x.fillText(text, left + 4, cy + .5);
        });
      });
      // 選取把手與起點記號：底色實心、畫線色描邊的小圓。
      const picked = shown.find(({ drawing }) => drawing.id === this.selectedId);
      if (picked) marks.push(...SHAPES[picked.drawing.kind].handles(picked.at, g));
      x.globalAlpha = 1; x.setLineDash([]); x.lineWidth = 1.6; x.fillStyle = paper; x.strokeStyle = color;
      marks.forEach(([hx, hy]) => { x.beginPath(); x.arc(hx, hy, HANDLE_R, 0, Math.PI * 2); x.fill(); x.stroke(); });
      x.restore();
    }
  }
  window.ChartDrawings = ChartDrawings;
})();
