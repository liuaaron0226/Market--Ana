/* 資金浮雕的畫面（立體重設計 E，本人 2026-11-15 選的方向）：把 ReliefLayout 算好的軌道畫成一塊立體的浮雕。
   一項一條軌道、一天一格，最右邊是今天。有買有賣的項目是一條白色的槽：買超的日子槽填平、上面長出紅柱，
   賣超的日子槽底是綠的、賣得越多槽越深；只有高低的項目是站著的柱列，今天那一根上色。
   用瀏覽器原生的 <canvas> 畫等角的方塊（由遠到近一塊一塊畫），沒有用任何套件；不能旋轉，頁面可以在它上面照常捲動。

   用法：Relief.render(host, { lanes, onPick })
   - lanes：ReliefLayout.layout() 的結果；onPick(key, 元素)：點了某一條軌道（或它的名稱）時呼叫。
   - 會清掉 host 原本的內容重畫（上一次留下的觀察器會先收掉）。樣式在 home.css〈資金浮雕〉。 */
(function (global) {
  'use strict';

  // 尺寸（單位是「格」，畫的時候才換成像素）：一天的寬、兩天之間的空隙、軌道的前後深度、兩條軌道的間距。
  const DAY = 1, GAP = 0.32, DEEP = 1.6, PITCH = 9;
  // 高度：站著的柱最高多高、最矮多高；槽的台面多高、槽壁多厚、最深凹下去多少、紅柱最高長多少。
  const TALL = 4.2, SHORT = 0.5, DECK = 1.6, WALL = 0.34, SINK = 1.25, RISE = 3;
  // 視角：水平轉幾度、從正上方往前傾幾度（和 Blender 概念圖同一組）。
  const TURN = 10 * Math.PI / 180, TILT = 52 * Math.PI / 180;
  // 長出來的動畫：每一格長多久、一格接一格隔多久（毫秒）。
  const GROW_MS = 700, STAGGER_MS = 28;
  // 左邊留給名稱與數字的寬度（像素）：占整塊寬度的比例，並夾在上下限之間。
  const LABEL_SHARE = 0.27, LABEL_MIN = 92, LABEL_MAX = 150, EDGE = 8;
  // 一格最多畫多大（像素）：寬螢幕上不要整塊放到撐滿，放到這個大小就置中。
  const MAX_SCALE = 18;
  // 只有高低的項目，今天那一根用的顏色（對到樣式裡的變數）；其餘的柱是灰的。
  const TODAY_TONE = { futures: '--down', margin: '--gold', short: '--accent', volume: '--study-dif' };

  const cleanups = new WeakMap();
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const ease = t => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);

  // 把立體的一點換成畫面上的位置（還沒乘上像素比例）：x 向右、y 向遠處、z 向上。
  function project(x, y, z) {
    const across = x * Math.cos(TURN) - y * Math.sin(TURN), away = x * Math.sin(TURN) + y * Math.cos(TURN);
    return { x: across, y: -away * Math.cos(TILT) - z * Math.sin(TILT), away };
  }

  /* 把軌道拆成一塊一塊的方塊。grown(第幾格) 回傳那一格現在長到幾成（0 到 1）。
     每塊是 { x0, x1, y0, y1, z0, z1, tone }，tone 是顏色的名字（在 paint 裡換成實際的顏色）。 */
  function boxes(lanes, grown) {
    const list = [];
    lanes.forEach((lane, row) => {
      const y = -row * PITCH;
      lane.days.forEach((day, index) => {
        if (day.direction === 'none') return;
        const x0 = index * (DAY + GAP), x1 = x0 + DAY, g = grown(index);
        if (!lane.signed) {
          const tall = (SHORT + day.size * (TALL - SHORT)) * g;
          list.push({ x0, x1, y0: y - DEEP / 2, y1: y + DEEP / 2, z0: 0, z1: tall, tone: day.today ? TODAY_TONE[lane.key] || '--ink' : 'bar' });
          return;
        }
        // 槽：前後兩道槽壁到台面的高度，中間是那一天
        const slot0 = x0 - GAP / 2, slot1 = x1 + GAP / 2;
        list.push({ x0: slot0, x1: slot1, y0: y + DEEP / 2, y1: y + DEEP / 2 + WALL, z0: 0, z1: DECK, tone: 'deck' });
        if (day.direction === 'down') {
          list.push({ x0: slot0, x1: slot1, y0: y - DEEP / 2, y1: y + DEEP / 2, z0: 0, z1: DECK - (0.18 + day.size * (SINK - 0.18)) * g, tone: '--down' });
        } else {
          list.push({ x0: slot0, x1: slot1, y0: y - DEEP / 2, y1: y + DEEP / 2, z0: 0, z1: DECK, tone: 'deck' });
          if (day.direction === 'up') list.push({ x0, x1, y0: y - DEEP / 2 + 0.12, y1: y + DEEP / 2 - 0.12, z0: DECK, z1: DECK + Math.max(0.12, day.size * RISE) * g, tone: '--up' });
        }
        list.push({ x0: slot0, x1: slot1, y0: y - DEEP / 2 - WALL, y1: y - DEEP / 2, z0: 0, z1: DECK, tone: 'deck' });
      });
    });
    // 由遠到近畫；同一個位置疊在一起的，下面的先畫
    return list.sort((a, b) => project((b.x0 + b.x1) / 2, (b.y0 + b.y1) / 2, 0).away - project((a.x0 + a.x1) / 2, (a.y0 + a.y1) / 2, 0).away || a.z0 - b.z0);
  }

  // 整塊浮雕（全部長好的時候）在畫面上占的範圍
  function bounds(lanes) {
    const box = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
    boxes(lanes, () => 1).forEach(b => {
      [[b.x0, b.y0], [b.x1, b.y0], [b.x0, b.y1], [b.x1, b.y1]].forEach(([x, y]) => [b.z0, b.z1].forEach(z => {
        const p = project(x, y, z);
        box.left = Math.min(box.left, p.x); box.right = Math.max(box.right, p.x);
        box.top = Math.min(box.top, p.y); box.bottom = Math.max(box.bottom, p.y);
      }));
    });
    return box;
  }

  function render(host, { lanes, onPick }) {
    if (cleanups.has(host)) cleanups.get(host)();
    host.replaceChildren();
    host.classList.add('relief');
    if (!lanes || !lanes.length) return;

    const canvas = el('canvas', 'relief-canvas');
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', `${lanes.map(lane => `${lane.name} ${lane.value}`).join('、')}。每一項近 ${Math.max(...lanes.map(lane => lane.days.length))} 天的立體圖，最右邊是今天。`);
    const probe = el('span', 'relief-probe');
    probe.setAttribute('aria-hidden', 'true');
    const labels = lanes.map(lane => {
      const button = el('button', `relief-label tone-${lane.tone}`);
      button.type = 'button';
      button.dataset.key = lane.key;
      button.setAttribute('aria-label', `${lane.name}，${lane.value}，看明細`);
      button.append(el('span', '', lane.name), el('b', 'num', lane.value));
      button.addEventListener('click', () => onPick && onPick(lane.key, button));
      return button;
    });
    host.append(canvas, probe, ...labels);

    // 樣式裡的顏色（可能是變數或混色）換成 canvas 看得懂的顏色
    const colour = name => {
      probe.style.color = name.startsWith('--') ? `var(${name})` : `var(--relief-${name})`;
      return getComputedStyle(probe).color;
    };
    const extent = bounds(lanes);
    let view = null, progress = 0, frame = 0;
    const grownAt = index => ease((progress - index * STAGGER_MS) / GROW_MS);
    const whole = Math.max(...lanes.map(lane => lane.days.length)) * STAGGER_MS + GROW_MS;

    function measure() {
      const width = host.clientWidth;
      if (!width) return false;
      const label = Math.max(LABEL_MIN, Math.min(LABEL_MAX, width * LABEL_SHARE));
      const scale = Math.min(MAX_SCALE, (width - label - EDGE) / (extent.right - extent.left));
      const height = Math.ceil((extent.bottom - extent.top) * scale + 2 * EDGE);
      // 放到最大還有剩的寬度，左右平分（整塊置中）
      const spare = Math.max(0, width - label - EDGE - (extent.right - extent.left) * scale) / 2;
      const ratio = Math.min(3, global.devicePixelRatio || 1);
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
      canvas.style.height = `${height}px`;
      view = { ratio, scale, left: spare + label - extent.left * scale, top: EDGE - extent.top * scale };
      labels.forEach((button, row) => {
        const start = project(-GAP, -row * PITCH, 0);
        button.style.left = `${(view.left + start.x * scale - 4).toFixed(1)}px`;
        button.style.top = `${(view.top + start.y * scale).toFixed(1)}px`;
      });
      return true;
    }

    function paint() {
      if (!view) return;
      const ctx = canvas.getContext('2d'), tones = new Map();
      const at = (x, y, z) => { const p = project(x, y, z); return [view.left + p.x * view.scale, view.top + p.y * view.scale]; };
      const face = (points, tone, shade) => {
        ctx.beginPath();
        points.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fillStyle = tone;
        ctx.fill();
        if (shade) { ctx.fillStyle = shade; ctx.fill(); }
      };
      ctx.setTransform(view.ratio, 0, 0, view.ratio, 0, 0);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      boxes(lanes, grownAt).forEach(b => {
        if (!tones.has(b.tone)) tones.set(b.tone, colour(b.tone));
        const tone = tones.get(b.tone);
        // 看得到的三個面：左側（較暗）、正面、頂面（較亮）
        face([at(b.x0, b.y0, b.z0), at(b.x0, b.y1, b.z0), at(b.x0, b.y1, b.z1), at(b.x0, b.y0, b.z1)], tone, 'rgb(0 0 0 / 22%)');
        face([at(b.x0, b.y0, b.z0), at(b.x1, b.y0, b.z0), at(b.x1, b.y0, b.z1), at(b.x0, b.y0, b.z1)], tone, 'rgb(0 0 0 / 9%)');
        face([at(b.x0, b.y0, b.z1), at(b.x1, b.y0, b.z1), at(b.x1, b.y1, b.z1), at(b.x0, b.y1, b.z1)], tone, 'rgb(255 255 255 / 14%)');
      });
    }

    const redraw = () => { if (measure()) paint(); };
    const calm = Boolean(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
    function grow() {
      if (calm) { progress = whole; redraw(); return; }
      const began = global.performance.now();
      const step = now => {
        progress = now - began;
        paint();
        frame = progress < whole ? global.requestAnimationFrame(step) : 0;
      };
      frame = global.requestAnimationFrame(step);
    }

    // 點在圖上：離哪一條軌道最近就開哪一項
    canvas.addEventListener('click', event => {
      if (!view || !onPick) return;
      const box = canvas.getBoundingClientRect(), x = event.clientX - box.left, y = event.clientY - box.top;
      let best = 0, distance = Infinity;
      lanes.forEach((lane, row) => {
        // 這條軌道在點的那個水平位置上的高度（軌道在畫面上是斜的）
        const a = project(0, -row * PITCH, DECK / 2), b = project(lane.days.length * (DAY + GAP), -row * PITCH, DECK / 2);
        const t = Math.max(0, Math.min(1, (x - (view.left + a.x * view.scale)) / ((b.x - a.x) * view.scale || 1)));
        const laneY = view.top + (a.y + (b.y - a.y) * t) * view.scale;
        if (Math.abs(laneY - y) < distance) { distance = Math.abs(laneY - y); best = row; }
      });
      onPick(lanes[best].key, labels[best]);
    });

    const observers = [];
    if (typeof ResizeObserver === 'function') {
      const resized = new ResizeObserver(redraw);
      resized.observe(host);
      observers.push(resized);
    }
    // 換深色／淺色時顏色要重拿
    const themed = new MutationObserver(redraw);
    themed.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    observers.push(themed);
    redraw();
    if (typeof IntersectionObserver === 'function') {
      const seen = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) { grow(); seen.disconnect(); }
      }, { threshold: 0.2 });
      seen.observe(host);
      observers.push(seen);
    } else grow();
    cleanups.set(host, () => { observers.forEach(observer => observer.disconnect()); global.cancelAnimationFrame(frame); });
  }

  global.Relief = { render };
})(window);
