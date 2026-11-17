/* 資金地形的畫面（立體重設計 B 包）：把 TerrainLayout 算好的街區與樓畫成可以旋轉、可以點的立體街區。
   每個產業一棟樓，樓高＝今日成交額；四個街區＝四個資金狀態。瀏覽器原生 CSS 3D，沒有用任何套件。
   樓高、佔地、排在第幾格全部來自 terrain-layout.js，這裡只把「格」換成像素，不另外算一套。

   用法：Terrain.render(host, { bubbles, quadrants })
   - bubbles：產業資金流向 API 的 latest.bubbles；quadrants：同一份 API 的四個資金狀態（取 label）。
   - 會清掉 host 原本的內容重畫（上一次留下的觀察器會先收掉）。樣式在 home.css〈資金地形〉。 */
(function (global) {
  'use strict';

  // 一格的邊長、街區與中央十字的距離、街區內緣的留白、街區外側放名稱的那一條（像素，縮放前）。
  const CELL = 56, STREET = 14, MARGIN = 16, LABEL_STRIP = 96;
  // 視角：往下看的傾角固定；水平旋轉預設從右前方看。可以一直轉（本人 11-15：原本只能轉 115 度、滑很長才轉一點，很難操作）：
  // 手機橫向滑過半個螢幕約轉 120 度；放手後帶一點慣性再滑一下——每 16 毫秒速度剩下 GLIDE_KEEP，慢到每毫秒 GLIDE_STOP 度以下就停。
  const TILT = 57, ANGLE_START = 34, DEGREES_PER_PIXEL = 0.64, GLIDE_KEEP = 0.92, GLIDE_STOP = 0.01;
  // 透視：觀看距離（像素）與消失點在舞台上的位置（比例）。
  const PERSPECTIVE = 1700, VANISH_X = 0.5, VANISH_Y = 0.2;
  // 縮放：最多放大到幾倍；四周與名稱牌留的邊（像素）。
  const MAX_SCALE = 1.3, EDGE = 14, TAG_ROOM = 30;
  // 長出來的動畫：每棟樓長多久、一棟接一棟隔多久（毫秒）。樣式從這裡拿，不另外寫一份。
  const GROW_MS = 900, STAGGER_MS = 26;
  // 位移小於這個距離算點選，不算拖曳；手指點不準時，取這個距離內最近的一棟。
  const TAP_SLOP = 6, NEAR_PICK = 28;
  // 名稱牌：桌機標成交額前六大，手機只留前三大（其餘用樣式藏起來）。
  const TAGGED = 6, TAGGED_NARROW = 3;
  // 名稱牌的下緣比樓頂高出自己高度的幾成（原本樣式的 translate(-50%, -120%)）；互相讓開時上下留的空隙（像素）。
  const TAG_LIFT = 1.2, TAG_GAP = 3;

  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  // 數字的寫法要和首頁表格（minimal.js 的 fmtNum、signed）一樣，明細和表格同一筆才會長得一樣。
  const number = (value, digits) => (Number.isFinite(value)
    ? value.toLocaleString('zh-TW', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—');
  const signed = (value, digits) => (Number.isFinite(value) ? `${value > 0 ? '+' : ''}${number(value, digits)}` : '—');
  const shortName = name => String(name).replace(/工?業$/, '');
  const radians = degrees => degrees * Math.PI / 180;

  // 上一次畫在同一個容器上留下的觀察器，重畫前要收掉
  const cleanups = new WeakMap();

  /* 建出街區、樓與名稱牌。回傳的 corners 是每個角落在街區上的位置與高度（縮放前、還沒轉），
     fitCity 用它算整片街區轉到某個角度時，投影到畫面上佔多大。 */
  function buildCity(plan, labelOf) {
    const city = el('div', 'terrain-city');
    const tagLayer = el('div', 'terrain-tags');
    const towers = [], tags = [], corners = [];
    const coverBox = (left, top, width, depth, height) => {
      [[left, top], [left + width, top], [left, top + depth], [left + width, top + depth]].forEach(([x, y]) => corners.push({ x, y, height }));
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
        node.dataset.rank = tower.rank;
        const x = block.side.x * (STREET + MARGIN + CELL * (tower.col + 0.5));
        const y = -block.side.y * (STREET + MARGIN + CELL * (tower.row + 0.5));
        const side = tower.footprint * CELL, tall = tower.height * CELL;
        coverBox(x - side / 2, y - side / 2, side, side, tall);
        node.style.cssText = `--x:${x}px;--y:${y}px;--w:${side.toFixed(1)}px;--h:${tall.toFixed(1)}px;--i:${towers.length}`;
        // 落影、四個側面、頂面
        ['shade', 'face north', 'face west', 'face east', 'face south', 'face top'].forEach(name => node.append(el('i', name)));
        if (tower.rank < TAGGED) {
          const tag = el('b', `terrain-tag${tower.rank >= TAGGED_NARROW ? ' minor' : ''}`, shortName(tower.name));
          tag.setAttribute('aria-hidden', 'true');
          tagLayer.append(tag);
          tags.push({ tag, roof: node.querySelector('.top'), rank: tower.rank });
        }
        city.append(node);
        towers.push(node);
      });
    });
    // 名稱牌照成交額名次排（疊到時名次後面的讓開）；街區是一區一區建的，這裡重排一次。
    tags.sort((a, b) => a.rank - b.rank);
    return { city, tagLayer, towers, tags, corners };
  }

  /* 名稱牌對到樓頂在畫面上的位置。樓在長高、升起或街區在轉的時候位置一直變，
     follow() 會每一格畫面重對一次，直到位置不再變為止（最多 4 秒），不必知道動畫有多長。 */
  function tagFollower(stage, tags) {
    let running = 0, still = 0, deadline = 0, last = '';
    const place = () => {
      const frame = stage.getBoundingClientRect();
      // 名稱牌放在樓頂正上方。兩個疊到時名次後面的往上讓開一格；一格不夠或會高過框頂，這一刻先淡出（轉開了就回來）。
      // tags 已照名次排；手機用樣式藏起來的寬是 0，不參與。
      const boxes = tags.map(({ tag, roof }) => {
        const box = roof.getBoundingClientRect(), width = tag.offsetWidth, height = tag.offsetHeight;
        return { left: box.left + box.width / 2 - frame.left - width / 2, top: box.top - frame.top - height * TAG_LIFT, width, height };
      });
      const tops = global.TerrainLayout.stackTags(boxes, { gap: TAG_GAP, ceiling: 0 });
      const spots = tags.map(({ tag }, index) => {
        const crowded = tops[index] === null;
        const spot = `translate(${boxes[index].left.toFixed(1)}px, ${(crowded ? boxes[index].top : tops[index]).toFixed(1)}px)`;
        tag.style.transform = spot;
        tag.classList.toggle('is-crowded', crowded);
        return crowded ? `${spot}~` : spot;
      }).join('|');
      still = spots === last ? still + 1 : 0;
      last = spots;
    };
    return function follow() {
      still = 0;
      deadline = performance.now() + 4000;
      if (running) return;
      const step = now => {
        place();
        running = still < 12 && now < deadline ? requestAnimationFrame(step) : 0;
      };
      running = requestAnimationFrame(step);
    };
  }

  /* 依可用的寬與高等比縮放並置中：整片街區連同名稱牌都留在框內。
     透視會把靠近觀看者的部分放大、往消失點的反方向推，所以縮放與位置互相影響，來回修正幾次就收斂。 */
  // 照目前轉到的角度（degrees）取景：整片街區放大到剛好放進框裡並置中。街區可以轉一整圈，每個角度佔的範圍不一樣，
  // 所以轉的時候跟著重算，不是用一個固定的大小。
  function fitCity(stage, city, corners, degrees) {
    const width = stage.clientWidth, height = stage.clientHeight;
    if (!width || !corners.length) return;
    const vanishX = width * VANISH_X, vanishY = height * VANISH_Y, tilt = radians(TILT), turn = radians(degrees);
    // 每個角落轉到這個視角之後的位置（x 向右、y 向下、z 朝向觀看者）
    const turned = corners.map(({ x, y, height: tall }) => {
      const turnedY = x * Math.sin(turn) + y * Math.cos(turn);
      return { x: x * Math.cos(turn) - y * Math.sin(turn), y: turnedY * Math.cos(tilt) - tall * Math.sin(tilt), z: turnedY * Math.sin(tilt) + tall * Math.cos(tilt) };
    });
    let scale = 1, centerX = width / 2, centerY = height / 2;
    const projected = () => {
      const box = { left: Infinity, right: -Infinity, top: Infinity, bottom: -Infinity };
      turned.forEach(corner => {
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
  }

  /* 選一棟樓：凸顯它、其他變淡，明細出現。回傳 { toggle, clear }：toggle 再點同一棟就取消，clear 一律取消。 */
  function selection(host, city, detail, byName, labelOf, follow) {
    let selected = null;
    const select = node => {
      if (selected) { selected.classList.remove('is-selected'); selected.setAttribute('aria-pressed', 'false'); }
      selected = node || null;
      follow();
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
      // 手機上明細排在街區下方，可能在畫面外：捲到看得見（桌機的明細浮在地形角落，不用捲）
      if (getComputedStyle(detail).position === 'static') detail.scrollIntoView({ block: 'nearest' });
    };
    return { toggle: node => select(node && node !== selected ? node : null), clear: () => { if (selected) select(null); } };
  }

  /* 指標與鍵盤：在地形上拖就是旋轉（不分方向），位移很小才算點選；Enter／空白鍵選取，Esc 取消。回傳一個收尾函式（停掉還在滑的慣性）。 */
  // onTurn(degrees)：轉到新的角度時通知外面重新取景、把名稱牌移到樓頂上
  function bindControls(stage, city, towers, picker, onTurn) {
    // 手機上樓很小：沒點中時，取畫面上最近的一棟
    const nearest = event => {
      let best = null, bestDistance = NEAR_PICK;
      towers.forEach(node => {
        const box = node.querySelector('.top').getBoundingClientRect();
        const distance = Math.hypot(event.clientX - (box.left + box.width / 2), event.clientY - (box.top + box.height / 2));
        if (distance < bestDistance) { bestDistance = distance; best = node; }
      });
      return best;
    };
    let angle = ANGLE_START, drag = null, glide = 0;
    const calm = Boolean(global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches);
    const turnTo = degrees => {
      angle = degrees;
      city.style.setProperty('--rz', `${angle}deg`);
      // 轉到背面時，寫在地上的街區名稱跟著倒過來，字才不會是顛倒的
      city.classList.toggle('is-turned', Math.cos(radians(angle - ANGLE_START)) < 0);
      onTurn(angle);
    };
    const stop = () => { drag = null; stage.classList.remove('is-dragging'); };
    // 放手後照放手那一刻的速度（每毫秒幾度）再滑一下，越滑越慢
    const coast = speed => {
      let last = global.performance.now();
      const step = now => {
        const elapsed = now - last;
        last = now;
        speed *= Math.pow(GLIDE_KEEP, elapsed / 16);
        if (Math.abs(speed) < GLIDE_STOP) { glide = 0; return; }
        turnTo(angle + speed * elapsed);
        glide = global.requestAnimationFrame(step);
      };
      glide = global.requestAnimationFrame(step);
    };
    stage.addEventListener('pointerdown', event => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      // 還在慣性滑的時候點一下：只是讓它停下來，不算點選樓
      const gliding = glide !== 0;
      global.cancelAnimationFrame(glide);
      glide = 0;
      drag = { id: event.pointerId, x: event.clientX, y: event.clientY, angle, turning: false, at: event.timeStamp, lastX: event.clientX, speed: 0,
        tower: gliding ? null : event.target.closest('.terrain-tower') || nearest(event), halted: gliding };
    });
    stage.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      // 滑鼠在舞台外面放開的話這裡收不到放開，下次移回來時按鍵已經沒按著：當成取消
      if (event.pointerType === 'mouse' && event.buttons === 0) { stop(); return; }
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.turning) {
        // 動得比點選多了就是在轉：不管方向，斜著拖、偏上下也算（頁面要捲動，手指放在地形以外）
        if (Math.hypot(dx, dy) < TAP_SLOP) return;
        drag.turning = true;
        stage.classList.add('is-dragging');
        // 確定是在轉了才把指標抓住，轉到街區外面放開也收得到
        if (stage.setPointerCapture) stage.setPointerCapture(event.pointerId);
      }
      if (event.timeStamp > drag.at) drag.speed = (event.clientX - drag.lastX) * DEGREES_PER_PIXEL / (event.timeStamp - drag.at);
      drag.at = event.timeStamp;
      drag.lastX = event.clientX;
      turnTo(drag.angle + dx * DEGREES_PER_PIXEL);
    });
    stage.addEventListener('pointerup', event => {
      if (!drag || event.pointerId !== drag.id) return;
      if (!drag.turning) { if (!drag.halted) picker.toggle(drag.tower); }
      // 手指停住一下才放開就不滑；開了「減少動態效果」也不滑
      else if (!calm && event.timeStamp - drag.at < 80 && Math.abs(drag.speed) >= GLIDE_STOP) coast(drag.speed);
      stop();
    });
    stage.addEventListener('pointercancel', stop);
    stage.addEventListener('pointerleave', () => { if (drag && !drag.turning) stop(); });
    city.addEventListener('keydown', event => {
      const node = event.target.closest('.terrain-tower');
      if (!node) return;
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); picker.toggle(node); }
      else if (event.key === 'Escape') { event.preventDefault(); picker.clear(); }
    });
    return () => global.cancelAnimationFrame(glide);
  }

  function render(host, payload) {
    const bubbles = (payload && payload.bubbles) || [];
    const quadrants = (payload && payload.quadrants) || {};
    const labelOf = quadrant => (quadrants[quadrant] && quadrants[quadrant].label) || '';
    const byName = new Map(bubbles.map(bubble => [String(bubble.name), bubble]));

    if (cleanups.has(host)) cleanups.get(host)();
    host.replaceChildren();
    host.classList.add('terrain');
    host.classList.remove('has-selection');

    const { city, tagLayer, towers, tags, corners } = buildCity(global.TerrainLayout.layout(bubbles), labelOf);
    const stage = el('div', 'terrain-stage');
    const detail = el('div', 'terrain-detail');
    detail.setAttribute('aria-live', 'polite');
    stage.style.perspective = `${PERSPECTIVE}px`;
    stage.style.perspectiveOrigin = `${VANISH_X * 100}% ${VANISH_Y * 100}%`;
    city.style.setProperty('--rx', `${TILT}deg`);
    city.style.setProperty('--rz', `${ANGLE_START}deg`);
    city.style.setProperty('--grow-ms', `${GROW_MS}ms`);
    city.style.setProperty('--stagger-ms', `${STAGGER_MS}ms`);
    stage.append(city, tagLayer);
    host.append(stage, detail);

    const follow = tagFollower(stage, tags);
    let view = ANGLE_START;
    const fit = () => { fitCity(stage, city, corners, view); follow(); };
    const observers = [];
    if (typeof ResizeObserver === 'function') {
      const resized = new ResizeObserver(fit);
      resized.observe(stage);
      observers.push(resized);
    }
    fit();

    // 進到畫面才長出來；全部長好之後，互動改用短的轉場
    let settle = 0;
    const grow = () => {
      city.classList.add('is-grown');
      stage.classList.add('is-grown');
      follow();
      settle = global.setTimeout(() => city.classList.add('is-settled'), GROW_MS + STAGGER_MS * towers.length + 100);
    };
    if (typeof IntersectionObserver === 'function') {
      const seen = new IntersectionObserver(entries => {
        if (entries.some(entry => entry.isIntersecting)) { grow(); seen.disconnect(); }
      }, { threshold: 0.25 });
      seen.observe(stage);
      observers.push(seen);
    } else grow();

    const stopGlide = bindControls(stage, city, towers, selection(host, city, detail, byName, labelOf, follow), degrees => { view = degrees; fit(); });
    cleanups.set(host, () => { observers.forEach(observer => observer.disconnect()); global.clearTimeout(settle); stopGlide(); });
  }

  global.Terrain = { render };
})(window);
