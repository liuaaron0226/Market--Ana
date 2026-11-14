/* 資金地形的畫面（立體重設計 B 包）：把 TerrainLayout 算好的街區與樓畫成可以旋轉、可以點的立體街區。
   每個產業一棟樓，樓高＝今日成交額；四個街區＝四個資金狀態。瀏覽器原生 CSS 3D，沒有用任何套件。
   樓高、佔地、排在第幾格全部來自 terrain-layout.js，這裡只把「格」換成像素，不另外算一套。

   用法：Terrain.render(host, { bubbles, quadrants })
   - bubbles：產業資金流向 API 的 latest.bubbles；quadrants：同一份 API 的四個資金狀態（取 label）。
   - 會清掉 host 原本的內容重畫。樣式在 home.css〈資金地形〉。 */
(function (global) {
  'use strict';

  // 一格的邊長、街區與中央十字的距離、街區內緣的留白、街區外側放名稱的那一條（像素，縮放前）。
  const CELL = 56, STREET = 14, MARGIN = 16, LABEL_STRIP = 96;
  // 視角：往下看的傾角固定；水平旋轉預設從右前方看，街區名稱朝向觀看者，不能轉到背面。
  const TILT = 57, ANGLE_START = 34, ANGLE_MIN = -40, ANGLE_MAX = 75, DEGREES_PER_PIXEL = 0.32;
  // 位移小於這個距離算點選，不算拖曳；手指點不準時，取這個距離內最近的一棟。
  const TAP_SLOP = 6, NEAR_PICK = 28;
  // 名稱牌：桌機標成交額前六大，手機只留前三大（其餘用樣式藏起來）。
  const TAGGED = 6, TAGGED_NARROW = 3;
  // 透視：觀看距離（像素）與消失點在舞台上的位置（比例）。
  const PERSPECTIVE = 1700, VANISH_X = 0.5, VANISH_Y = 0.2;
  // 縮放：最多放大到幾倍；四周與名稱牌留的邊（像素）。
  const MAX_SCALE = 1.3, EDGE = 14, TAG_ROOM = 30;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const number = (value, digits) => (Number.isFinite(value)
    ? value.toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—');
  const signed = (value, digits) => (Number.isFinite(value) ? `${value > 0 ? '+' : value < 0 ? '−' : ''}${number(Math.abs(value), digits)}` : '—');
  const shortName = name => String(name).replace(/工?業$/, '');

  function render(host, payload) {
    const bubbles = (payload && payload.bubbles) || [];
    const quadrants = (payload && payload.quadrants) || {};
    const labelOf = quadrant => (quadrants[quadrant] && quadrants[quadrant].label) || '';
    const byName = new Map(bubbles.map(bubble => [String(bubble.name), bubble]));
    const plan = global.TerrainLayout.layout(bubbles);

    host.replaceChildren();
    host.classList.add('terrain');
    const stage = el('div', 'terrain-stage');
    const city = el('div', 'terrain-city');
    // 名稱牌放在街區上面的平面層，位置跟著樓頂在畫面上的位置走：永遠正對畫面，也不會被前面的樓擋住。
    const tagLayer = el('div', 'terrain-tags');
    const detail = el('div', 'terrain-detail');
    detail.setAttribute('aria-live', 'polite');
    stage.append(city, tagLayer);
    host.append(stage, detail);

    let order = 0;
    const towers = [], tags = [];
    // 街區與樓的角落，轉到預設視角之後的位置（縮放前；x 向右、y 向下、z 朝向觀看者）。
    // 用來算整片街區投影到畫面上佔多大，決定縮放比例與置中的位置。
    const radians = degrees => degrees * Math.PI / 180;
    const corners = [];
    const cover = (x, y, z) => {
      const turn = radians(ANGLE_START), tilt = radians(TILT);
      const turnedY = x * Math.sin(turn) + y * Math.cos(turn);
      corners.push({
        x: x * Math.cos(turn) - y * Math.sin(turn),
        y: turnedY * Math.cos(tilt) - z * Math.sin(tilt),
        z: turnedY * Math.sin(tilt) + z * Math.cos(tilt),
      });
    };
    const coverBox = (left, top, width, depth, z) => {
      [[left, top], [left + width, top], [left, top + depth], [left + width, top + depth]].forEach(([x, y]) => cover(x, y, z));
    };
    plan.blocks.forEach(block => {
      const width = block.cols * CELL + 2 * MARGIN + LABEL_STRIP, depth = block.rows * CELL + 2 * MARGIN;
      const left = block.side.x > 0 ? STREET : -(STREET + width), top = block.side.y > 0 ? -(STREET + depth) : STREET;
      coverBox(left, top, width, depth, 0);
      const ground = el('div', 'terrain-ground');
      ground.dataset.quadrant = block.quadrant;
      ground.style.cssText = `left:${left}px;top:${top}px;width:${width}px;height:${depth}px`;
      const label = el('div', 'terrain-ground-label', labelOf(block.quadrant));
      label.style.cssText = `${block.side.x > 0 ? 'right' : 'left'}:${MARGIN}px;${block.side.y > 0 ? 'bottom' : 'top'}:${MARGIN + 16}px`;
      ground.append(label);
      city.append(ground);

      block.towers.forEach(tower => {
        const node = el('div', 'terrain-tower');
        node.tabIndex = 0;
        node.setAttribute('role', 'button');
        node.setAttribute('aria-pressed', 'false');
        node.setAttribute('aria-label', `${tower.name}，${labelOf(tower.quadrant)}`);
        node.dataset.name = tower.name;
        node.dataset.quadrant = tower.quadrant;
        const x = block.side.x * (STREET + MARGIN + CELL * (tower.col + 0.5));
        const y = -block.side.y * (STREET + MARGIN + CELL * (tower.row + 0.5));
        const side = tower.footprint * CELL, tall = tower.height * CELL;
        coverBox(x - side / 2, y - side / 2, side, side, tall);
        node.style.cssText = `--x:${x}px;--y:${y}px;--w:${side.toFixed(1)}px;--h:${tall.toFixed(1)}px;--i:${order}`;
        order += 1;
        // 落影、四個側面、頂面
        ['shade', 'face north', 'face west', 'face east', 'face south', 'face top'].forEach(name => node.append(el('i', name)));
        if (tower.rank < TAGGED) {
          const tag = el('b', `terrain-tag${tower.rank >= TAGGED_NARROW ? ' minor' : ''}`, shortName(tower.name));
          tag.setAttribute('aria-hidden', 'true');
          tagLayer.append(tag);
          tags.push({ tag, roof: node.querySelector('.top') });
        }
        city.append(node);
        towers.push(node);
      });
    });

    // 名稱牌對到樓頂在畫面上的位置
    const placeTags = () => {
      const frame = stage.getBoundingClientRect();
      tags.forEach(({ tag, roof }) => {
        const box = roof.getBoundingClientRect();
        tag.style.transform = `translate(${(box.left + box.width / 2 - frame.left).toFixed(1)}px, ${(box.top - frame.top).toFixed(1)}px) translate(-50%, -120%)`;
      });
    };
    // 樓在長高、升起或街區在轉的時候，每一格畫面都重對一次
    let followUntil = 0, following = 0;
    const follow = duration => {
      followUntil = Math.max(followUntil, performance.now() + duration);
      if (following) return;
      const step = now => {
        placeTags();
        following = now < followUntil ? requestAnimationFrame(step) : 0;
      };
      following = requestAnimationFrame(step);
    };

    // 依可用的寬與高等比縮放並置中：整片街區連同名稱牌都留在框內。
    // 透視會把靠近觀看者的部分放大、往消失點的反方向推，所以縮放與位置互相影響，來回修正幾次就收斂。
    stage.style.perspective = `${PERSPECTIVE}px`;
    stage.style.perspectiveOrigin = `${VANISH_X * 100}% ${VANISH_Y * 100}%`;
    city.style.setProperty('--rx', `${TILT}deg`);
    city.style.setProperty('--rz', `${ANGLE_START}deg`);
    const fit = () => {
      const width = stage.clientWidth, height = stage.clientHeight;
      if (!width || !corners.length) return;
      const vanishX = width * VANISH_X, vanishY = height * VANISH_Y;
      let scale = 1, centerX = width / 2, centerY = height / 2;
      const projected = () => {
        const box = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
        corners.forEach(corner => {
          const near = PERSPECTIVE / (PERSPECTIVE - scale * corner.z);
          const x = vanishX + (centerX + scale * corner.x - vanishX) * near, y = vanishY + (centerY + scale * corner.y - vanishY) * near;
          box.left = Math.min(box.left, x); box.right = Math.max(box.right, x);
          box.top = Math.min(box.top, y); box.bottom = Math.max(box.bottom, y);
        });
        return box;
      };
      for (let pass = 0; pass < 4; pass += 1) {
        const before = projected();
        scale = Math.min(MAX_SCALE, scale * Math.min((width - 2 * EDGE) / (before.right - before.left), (height - 2 * EDGE - TAG_ROOM) / (before.bottom - before.top)));
        const after = projected();
        centerX += width / 2 - (after.left + after.right) / 2;
        centerY += (height + TAG_ROOM) / 2 - (after.top + after.bottom) / 2;
      }
      city.style.setProperty('--s', scale.toFixed(3));
      city.style.left = `${centerX.toFixed(1)}px`;
      city.style.top = `${centerY.toFixed(1)}px`;
      follow(0);
    };
    if (typeof ResizeObserver === 'function') new ResizeObserver(fit).observe(stage);
    fit();

    // 進到畫面才長出來；之後的互動用短的轉場
    const grow = () => {
      city.classList.add('is-grown');
      stage.classList.add('is-grown');
      follow(2400);
      global.setTimeout(() => city.classList.add('is-settled'), 1900);
    };
    if (typeof IntersectionObserver === 'function') {
      const seen = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) { grow(); seen.disconnect(); }
      }, { threshold: 0.25 });
      seen.observe(stage);
    } else grow();

    let selected = null;
    function select(node) {
      if (selected) { selected.classList.remove('is-selected'); selected.setAttribute('aria-pressed', 'false'); }
      selected = node || null;
      follow(500);
      city.classList.toggle('has-selection', Boolean(selected));
      host.classList.toggle('has-selection', Boolean(selected));
      if (!selected) { detail.replaceChildren(); return; }
      selected.classList.add('is-selected');
      selected.setAttribute('aria-pressed', 'true');
      const bubble = byName.get(selected.dataset.name) || {};
      const state = el('p', 'terrain-detail-state', labelOf(selected.dataset.quadrant));
      state.dataset.quadrant = selected.dataset.quadrant;
      const list = el('dl');
      [['今日成交額', `${number(bubble.amount_today, 1)} 億元`], ['檔數', number(bubble.member_count, 0)],
        ['成交額相對平均', `${signed(bubble.relative_pct, 0)}%`], ['近 5 日加速度', `${signed(bubble.y, 1)} 億元/日`]]
        .forEach(([term, value]) => { list.append(el('dt', '', term), el('dd', 'num', value)); });
      detail.replaceChildren(state, el('h4', '', selected.dataset.name), list);
    }
    const toggle = node => select(node && node !== selected ? node : null);

    // 手機上樓很小：沒點中時，取畫面上最近的一棟
    function nearest(event) {
      let best = null, bestDistance = NEAR_PICK;
      towers.forEach(node => {
        const box = node.querySelector('.top').getBoundingClientRect();
        const distance = Math.hypot(event.clientX - (box.left + box.width / 2), event.clientY - (box.top + box.height / 2));
        if (distance < bestDistance) { bestDistance = distance; best = node; }
      });
      return best;
    }

    // 水平拖曳旋轉；垂直的手勢還給頁面捲動；位移很小才算點選
    let angle = ANGLE_START, drag = null;
    stage.addEventListener('pointerdown', event => {
      drag = { x: event.clientX, y: event.clientY, angle, moved: false, tower: event.target.closest('.terrain-tower') || nearest(event) };
    });
    const onMove = event => {
      if (!drag) return;
      const dx = event.clientX - drag.x;
      if (!drag.moved && Math.abs(dx) < TAP_SLOP) return;
      if (!drag.moved && Math.abs(event.clientY - drag.y) > Math.abs(dx)) { drag = null; return; }
      if (!drag.moved) {
        // 確定是在轉了才把指標抓住，轉到街區外面放開也收得到
        drag.moved = true;
        stage.classList.add('is-dragging');
        if (stage.setPointerCapture) stage.setPointerCapture(event.pointerId);
      }
      angle = Math.max(ANGLE_MIN, Math.min(ANGLE_MAX, drag.angle + dx * DEGREES_PER_PIXEL));
      city.style.setProperty('--rz', `${angle}deg`);
      follow(0);
    };
    const onUp = () => {
      if (!drag) return;
      if (!drag.moved) toggle(drag.tower);
      drag = null;
      stage.classList.remove('is-dragging');
    };
    const onCancel = () => { drag = null; stage.classList.remove('is-dragging'); };
    stage.addEventListener('pointermove', onMove);
    stage.addEventListener('pointerup', onUp);
    stage.addEventListener('pointercancel', onCancel);
    city.addEventListener('keydown', event => {
      const node = event.target.closest('.terrain-tower');
      if (!node) return;
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(node); }
      else if (event.key === 'Escape' && selected) { event.preventDefault(); select(null); }
    });
  }

  global.Terrain = { render };
})(window);
