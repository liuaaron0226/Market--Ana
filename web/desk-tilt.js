/* 桌面隨游標微傾（立體重設計 D 包第 1 片；本人 2026-11-14 定案的「大膽」：桌機上整張桌面隨游標微微傾斜）。
   角度照原型：上下合計 1.6 度、左右合計 2.2 度（游標在畫面正中間時是平的）。

   ── 怎麼整個關掉 ──
   把下面的 ENABLED 改成 false（或頁面上拿掉 desk-tilt.js 那一行）。其他外觀都不受影響：
   這支只在游標移動時替 [data-desk-tilt] 那一塊寫 transform，停下來回平就把 transform 拿掉。

   哪些時候不傾：
   - 觸控裝置、手機（不是「可懸停且精細指標」）、系統開了「減少動態效果」。
   - 游標在 K 線、圖表、資金地形這類要精準對位或拖曳的地方（canvas、svg、.terrain-stage、[data-tilt-flat]、輸入框），
     或按著滑鼠鍵拖曳時：盡快回平，讀價、畫線、旋轉地形都和沒有傾斜時一樣準。
   - 游標不在桌面上（浮板、下鑽面板、對話框都在桌面外面）：回平。
   固定在畫面上的元件（下鑽面板、遮罩、對話框）都不在 [data-desk-tilt] 裡面，所以不會因為傾斜跑位。
   游標不動時不跑動畫迴圈。 */
(function (global) {
  'use strict';

  const ENABLED = true;
  const RANGE = { rx: 1.6, ry: 2.2 };     // 合計角度（度）
  const EASE_IN = 0.08, EASE_OUT = 0.25;  // 每一格往目標靠近的比例；回平比較快
  const SETTLE = 0.002;                   // 和目標差不到這麼多就停下來
  const PERSPECTIVE = 1900;
  const FLAT = 'canvas, svg, input, textarea, select, .terrain-stage, [data-tilt-flat]';   // 資金地形是 div 做的 3D，另外列

  function allowed({ fineHover, reduceMotion, enabled = ENABLED }) {
    return Boolean(enabled && fineHover && !reduceMotion);
  }

  const clamp = (value) => Math.max(0, Math.min(1, value));

  // 游標在視窗裡的位置 → 目標角度。上面往後仰（rx 正）、右邊往右轉（ry 正）。
  function target({ width, height, x, y, flat = false }) {
    if (flat || !(width > 0) || !(height > 0)) return { rx: 0, ry: 0 };
    const rx = (clamp(y / height) - 0.5) * -RANGE.rx;
    const ry = (clamp(x / width) - 0.5) * RANGE.ry;
    return { rx: rx + 0, ry: ry + 0 };   // + 0 把 -0 變成 0
  }

  function step(now, goal) {
    const out = goal.rx === 0 && goal.ry === 0 && (now.rx !== 0 || now.ry !== 0);
    const ease = out ? EASE_OUT : EASE_IN;
    const rx = now.rx + (goal.rx - now.rx) * ease, ry = now.ry + (goal.ry - now.ry) * ease;
    if (Math.abs(goal.rx - rx) + Math.abs(goal.ry - ry) <= SETTLE) return { rx: goal.rx, ry: goal.ry, settled: true };
    return { rx, ry, settled: false };
  }

  function atRest(state) {
    return Boolean(state.settled && state.rx === 0 && state.ry === 0);
  }

  function transform({ rx, ry }) {
    return `perspective(${PERSPECTIVE}px) rotateX(${rx.toFixed(3)}deg) rotateY(${ry.toFixed(3)}deg)`;
  }

  global.DeskTilt = { ENABLED, allowed, target, step, atRest, transform };

  // ---------- 畫面 ----------
  const doc = global.document;
  if (!doc || typeof global.matchMedia !== 'function') return;
  const desk = doc.querySelector('[data-desk-tilt]');
  if (!desk) return;
  const fine = global.matchMedia('(hover: hover) and (pointer: fine)');
  const reduce = global.matchMedia('(prefers-reduced-motion: reduce)');
  let now = { rx: 0, ry: 0, settled: true }, goal = { rx: 0, ry: 0 }, frame = 0, top = null;

  // 繞著「畫面正中間對到桌面上的那一點」轉：捲到哪裡，眼前那一塊都穩穩的，不會因為轉軸在很遠的地方被放大或推走。
  function origin() {
    if (top === null) {
      const box = desk.getBoundingClientRect();   // 只在平的時候量（transform 已拿掉），量到的是真正的位置
      top = { y: box.top + global.scrollY, x: box.left + global.scrollX };
    }
    desk.style.transformOrigin = `${global.innerWidth / 2 + global.scrollX - top.x}px ${global.innerHeight / 2 + global.scrollY - top.y}px`;
  }

  function paint() {
    frame = 0;
    now = step(now, goal);
    if (atRest(now)) {
      desk.style.transform = '';
      desk.style.transformOrigin = '';
      top = null;
      return;
    }
    origin();
    desk.style.transform = transform(now);
    if (!now.settled) frame = global.requestAnimationFrame(paint);
  }

  function aim(next) {
    if (next.rx === goal.rx && next.ry === goal.ry) return;
    goal = next;
    now = { ...now, settled: false };
    if (!frame) frame = global.requestAnimationFrame(paint);
  }

  function flatAt(event) {
    const node = event.target;
    if (event.buttons) return true;   // 按著拖曳（畫線、轉地形、選取文字）
    if (!(node instanceof global.Element) || !desk.contains(node)) return true;
    return Boolean(node.closest(FLAT));
  }

  doc.addEventListener('pointermove', (event) => {
    if (event.pointerType !== 'mouse' || !allowed({ fineHover: fine.matches, reduceMotion: reduce.matches })) {
      aim({ rx: 0, ry: 0 });
      return;
    }
    aim(target({ width: global.innerWidth, height: global.innerHeight, x: event.clientX, y: event.clientY, flat: flatAt(event) }));
  }, { passive: true });
  doc.addEventListener('pointerdown', () => aim({ rx: 0, ry: 0 }), { passive: true });
  doc.documentElement.addEventListener('mouseleave', () => aim({ rx: 0, ry: 0 }));
  global.addEventListener('blur', () => aim({ rx: 0, ry: 0 }));
  // 傾斜中捲動：轉軸跟著畫面正中間走（游標沒動也要重算一次）
  global.addEventListener('scroll', () => {
    if (!atRest(now) && !frame) { origin(); desk.style.transform = transform(now); }
  }, { passive: true });
  global.addEventListener('resize', () => { if (atRest(now)) top = null; }, { passive: true });
  reduce.addEventListener?.('change', () => { if (reduce.matches) aim({ rx: 0, ry: 0 }); });
})(window);
