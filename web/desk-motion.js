/* 看盤桌的動態層（2026-11-24 全站動態與元件設計升級，第一片：首頁）。樣式在 desk-motion.css。
   不改其他任何一支程式：只在旁邊看著頁面的狀態（哪一顆被選中、哪個面板開著）補上動態，所以拿掉這兩個檔，頁面就回到原本的樣子。
   1. 選中的框：每一排「選一個」的鍵有一個框（那一排的 ::before），選中哪一顆就滑到哪一顆。
   2. 分類區塊：關、換類時先讓舊的照方向退場（140 毫秒），再放行原本的點擊（home-fold.js 的開合照常跑）。
   3. 手機的底部板：按住標題列往下拖，放手時看速度與距離決定關掉或彈回。
   4. K 線換資料：新的圖畫好時由淡到實（只動透明度）。
   系統設「減少動態」時 2、4 不做，樣式的過場縮到幾乎沒有。維護說明在 DESIGN_UPGRADE.md。 */
(() => {
  'use strict';

  const root = document.documentElement;
  const state = { on: true, sheet: true, tabs: true, kfade: true };   // 四樣都開；要關掉其中一樣，改這裡並拿掉下面對應的類別
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  // 關的時候也有過場，要瀏覽器支援 @starting-style 與 allow-discrete（見 desk-motion.css 第 3 段）
  const SUPPORTS_EXIT = typeof CSSStartingStyleRule !== 'undefined' && CSS.supports('transition-behavior', 'allow-discrete');

  // ---------- 1. 選中的框 ----------
  // 只在三種時候動手：某一排的選中換了、那一排的大小變了、頁面上多了一排新的。其他時候完全不碰版面
  // （一開始寫成「頁面有任何變動就全部重量一次」，量到操作時主執行緒多忙了三成，改掉）。
  const GROUPS = '.hm-cats, .chip-seg, .st-track, .sf-tabs';
  const PICKED = ':scope > button:is([aria-expanded="true"], [aria-pressed="true"], [aria-selected="true"], [aria-checked="true"])';
  const known = new WeakSet(), spot = new WeakMap();   // spot：每一排的框現在放在哪裡
  const sized = 'ResizeObserver' in window ? new ResizeObserver(entries => { if (state.on) entries.forEach(entry => place(entry.target, entry.target.querySelector(PICKED), false)); }) : null;
  // 按住拖完放手（網站原本那個跟手的框收掉）時，這一個直接到位
  const slid = new MutationObserver(list => { if (state.on) list.forEach(({ target }) => { if (!target.classList.contains('is-sliding')) place(target, target.querySelector(PICKED), false); }); });

  // 把框放到 key 上；key 是空的＝沒有選中的，框淡掉。animate＝滑過去，否則直接到位。
  // 框是那一排的 ::before，這裡只寫四個位置變數與兩個標記在那一排自己身上，不往裡面塞元素。
  function place(group, key, animate) {
    if (!key || !key.offsetWidth) { if (group.dataset.upgOn !== '0') group.dataset.upgOn = '0'; spot.delete(group); return; }
    const now = `${key.offsetLeft},${key.offsetTop},${key.offsetWidth},${key.offsetHeight}`;
    if (spot.get(group) === now && group.dataset.upgOn === '1') return;
    const jump = !animate || group.dataset.upgOn !== '1';
    if (jump) group.dataset.upgStill = '';
    group.style.setProperty('--upg-tx', `${key.offsetLeft}px`);
    group.style.setProperty('--upg-ty', `${key.offsetTop}px`);
    group.style.setProperty('--upg-tw', `${key.offsetWidth}px`);
    group.style.setProperty('--upg-th', `${key.offsetHeight}px`);
    if (jump) { void group.offsetWidth; delete group.dataset.upgStill; }
    group.dataset.upgOn = '1';
    spot.set(group, now);
  }
  function adopt(group) {
    if (known.has(group)) return;
    known.add(group);
    sized?.observe(group);
    slid.observe(group, { attributes: true, attributeFilter: ['class'] });
    if (state.on) place(group, group.querySelector(PICKED), false);
  }
  function syncAll(animate) {
    if (!state.on) return;
    document.querySelectorAll(GROUPS).forEach(group => { adopt(group); if (!group.classList.contains('is-sliding')) place(group, group.querySelector(PICKED), animate); });
  }
  new MutationObserver(list => {
    for (const item of list) {
      if (item.type === 'attributes') {
        const group = item.target.parentElement;
        if (!group || !group.matches(GROUPS)) continue;
        adopt(group);
        if (state.on && !group.classList.contains('is-sliding')) place(group, group.querySelector(PICKED), true);
        continue;
      }
      for (const node of item.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.matches(GROUPS)) adopt(node);
        else if (node.firstElementChild) node.querySelectorAll(GROUPS).forEach(adopt);
      }
    }
  }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['aria-expanded', 'aria-pressed', 'aria-selected', 'aria-checked'] });
  document.fonts?.ready.then(() => syncAll(false));

  // ---------- 2. 分類區塊：照方向退、照方向進 ----------
  let leaving = null;   // 正在退場的那一塊：{ panel, timer }
  document.addEventListener('click', event => {
    if (!state.on || still) return;
    const key = event.target.closest?.('.hm-cats > button[aria-controls]');
    if (!key || key.dataset.upgPass) return;
    const bar = key.parentElement, keys = [...bar.querySelectorAll(':scope > button[aria-controls]')];
    const openKey = keys.find(one => one.getAttribute('aria-expanded') === 'true');
    const target = document.getElementById(key.getAttribute('aria-controls'));
    // 不是手指直接點的（按住拖放手時網站自己送出的點擊）：不插手，只設定新的一塊從哪個方向進來
    const direct = event.isTrusted && !bar.classList.contains('is-sliding');
    if (!openKey || !direct) {
      const dir = openKey && openKey !== key ? Math.sign(keys.indexOf(key) - keys.indexOf(openKey)) : 0;
      target?.style.setProperty('--upg-dx', `${dir * 18}px`);
      target?.style.setProperty('--upg-dy', dir ? '0px' : '-10px');
      return;
    }
    // 關掉或換類：舊的先退場，再放行這一下點擊
    event.preventDefault();
    event.stopImmediatePropagation();
    const from = document.getElementById(openKey.getAttribute('aria-controls'));
    const closing = openKey === key;
    const dir = closing ? 0 : Math.sign(keys.indexOf(key) - keys.indexOf(openKey));
    place(bar, closing ? null : key, true);   // 框立刻開始滑，不等內容
    if (!closing) { target?.style.setProperty('--upg-dx', `${dir * 18}px`); target?.style.setProperty('--upg-dy', '0px'); }
    const pass = () => {
      leaving = null;
      from?.classList.remove('upg-out');
      key.dataset.upgPass = '1';
      key.click();
      delete key.dataset.upgPass;
    };
    if (leaving) { clearTimeout(leaving.timer); leaving.timer = setTimeout(pass, 70); return; }   // 退到一半又點了別顆：不重播，接著走
    from?.style.setProperty('--upg-ox', `${-dir * 18}px`);
    from?.style.setProperty('--upg-oy', closing ? '-8px' : '0px');
    from?.classList.add('upg-out');
    leaving = { panel: from, timer: setTimeout(pass, 140) };
  }, true);

  // ---------- 3. 手機的底部板：往下拖 ----------
  const closeOf = sheet => sheet.querySelector('.panel-close, .st-close, [data-dialog-close]');
  document.addEventListener('pointerdown', event => {
    if (!state.on || !state.sheet || innerWidth > 640 || event.button) return;
    const head = event.target.closest?.('#detailPanel .panel-header, #settingsDialog .st-head');
    if (!head || event.target.closest('button, a, input, select, textarea')) return;
    const sheet = head.closest('#detailPanel, #settingsDialog');
    const shade = sheet.id === 'detailPanel' ? document.getElementById('panelBackdrop') : null;
    const drag = { id: event.pointerId, y0: event.clientY, y: event.clientY, t: event.timeStamp, v: 0, moved: false, height: sheet.offsetHeight };
    try { head.setPointerCapture(event.pointerId); } catch (_) { /* 舊瀏覽器 */ }
    const move = ev => {
      if (ev.pointerId !== drag.id) return;
      const dy = Math.max(0, ev.clientY - drag.y0);
      if (!drag.moved && dy < 6) return;
      drag.moved = true;
      sheet.classList.add('upg-dragging');
      sheet.style.setProperty('--upg-drag', `${dy}px`);
      if (shade) { shade.classList.add('upg-dragging'); shade.style.opacity = String(Math.max(0, 1 - dy / drag.height)); }
      const dt = ev.timeStamp - drag.t;
      if (dt > 0) drag.v = drag.v * .4 + ((ev.clientY - drag.y) / dt) * .6;
      drag.y = ev.clientY; drag.t = ev.timeStamp;
    };
    const end = ev => {
      if (ev.pointerId !== drag.id) return;
      head.removeEventListener('pointermove', move);
      head.removeEventListener('pointerup', end);
      head.removeEventListener('pointercancel', end);
      sheet.classList.remove('upg-dragging');
      if (shade) { shade.classList.remove('upg-dragging'); shade.style.opacity = ''; }
      const dy = Math.max(0, ev.clientY - drag.y0);
      const shut = drag.moved && ev.type === 'pointerup' && (drag.v > .45 || dy > drag.height * .3);
      if (shut) {
        closeOf(sheet)?.click();   // 關的程式是網站原本的；板從手放開的位置接著往下退
        setTimeout(() => sheet.style.removeProperty('--upg-drag'), 420);
      } else sheet.style.removeProperty('--upg-drag');
    };
    head.addEventListener('pointermove', move);
    head.addEventListener('pointerup', end);
    head.addEventListener('pointercancel', end);
  });

  // ---------- 4. K 線換資料：新圖由淡到實 ----------
  function softSwap() {
    if (!state.on || !state.kfade || still) return;
    const live = [...document.querySelectorAll('#mainChart canvas, #chartHost canvas, #chartStudies canvas')].filter(canvas => canvas.offsetWidth && canvas.offsetHeight);
    if (!live.length) return;
    live.forEach(canvas => canvas.classList.add('upg-dip'));
    void getComputedStyle(live[0]).opacity;   // 先讓「淡」的樣子生效，等一下拿掉時才有過場
    let done = false;
    const rise = () => {
      if (done) return;
      done = true;
      watch.disconnect();
      requestAnimationFrame(() => live.forEach(canvas => canvas.classList.remove('upg-dip')));
    };
    // 新的圖畫好時，圖的文字說明（給報讀用的那一句）會跟著換：看到它換了就開始變實
    const watch = new MutationObserver(rise);
    live.forEach(canvas => watch.observe(canvas, { attributes: true, attributeFilter: ['aria-label', 'width', 'height'] }));
    setTimeout(rise, 700);
  }
  document.addEventListener('change', event => { if (event.target.matches?.('#periodSelect')) softSwap(); }, true);
  document.addEventListener('click', event => { if (event.target.closest?.('#periodMenu button, #indexMenu button')) softSwap(); }, true);

  // iPhone 的 Safari 要有人聽 touchstart，「按住」的樣式才會出現
  document.addEventListener('touchstart', () => {}, { passive: true });

  root.classList.add('upg', 'upg-sheet', 'upg-tabs', 'upg-kfade');
  if (SUPPORTS_EXIT) root.classList.add('upg-ss');
  syncAll(false);
})();
