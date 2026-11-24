/* K 線板標題列的鍵收成最右邊一格，左右滑切換（站主 2026-11-24 指定的做法；首頁三顆、個股頁兩顆）。樣式在 key-slot.css。
   不改其他任何一支程式：鍵都在原本的位置，點下去做的事完全不變；這裡只決定「現在顯示哪一顆」與滑動時的位移。
   拿掉這兩個檔（與頁面上那兩行引用），那一排就回到原本並排的樣子。
   - 只在手機寬度（860px 以下）作用；電腦維持並排。每次進頁面都從週期鍵（日K）開始，不記住上次停在哪。
   - 手指在那一格上左右滑：現在這一顆跟著手指走並變淡，下一顆從另一邊進來；放手時滑過 26px 或速度夠快就換，否則彈回。
     可以一直往同一個方向滑（繞一圈）。在那一格上往上下滑照常捲頁面。
   - 點一下：照原本的（開選單、開預測）。滑的那一下不會被當成點；剛滑完緊接著點，瀏覽器沒有補發點擊時由這裡替它送。
   - chart-menus.js 原本「按下去就先打開選單」在這一格裡改成「放開才打開」（不然手指一碰到、還沒滑選單就跳出來）。
   - 鍵盤與自動化：鍵都還在，焦點走到哪一顆就顯示哪一顆；window.KeySlot.show('tools') 也可以直接換（檢查腳本要點一顆現在沒顯示的鍵之前先呼叫它）。
   - 想看的那一顆暫時不在（分時沒有分析工具）時先顯示最後一顆，它回來了再換回去。換到別顆時，原本那顆開著的選單會一起收起來。
   - 選了畫線工具時自動停在「分析工具」那一顆（它旁邊的離開鍵才看得到）。
   維護說明在 DESIGN_UPGRADE.md 第 11 節。 */
(() => {
  'use strict';

  const root = document.documentElement;
  const FIRST = 'period';   // 每次進頁面先顯示哪一顆：forecast／tools／period
  const narrow = matchMedia('(max-width: 860px)');
  const still = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const TRAVEL = 72, SLIDE = .6;   // 滑多遠算滑到底、鍵跟手的比例（鍵只走六成，看起來比較穩）
  // 順序、外層、會跟著位移的那一層。預測只動鍵本身：它的浮窗在手機上是釘在畫面上的，外層一有位移浮窗就會跟著跑掉
  const KEYS = [['forecast', '.forecast-control', '.forecast-chip'], ['tools', '.dt-ui', null], ['period', '.hm-period', null]];

  const host = document.querySelector('body.hm .chart-controls, body.st .ts-bar-keys');   // 首頁三顆、個股頁兩顆
  if (!host) return;
  // want＝想看的那一顆（使用者滑到的、或焦點走到的）；current＝現在真的顯示的那一顆。
  // 兩個分開記：想看的那一顆暫時不在（例如分時沒有分析工具）就先顯示最後一顆，它回來了再換回去。
  let want = null, current = null, drag = null, swallowUntil = 0, tidy = 0, pending = null, late = null, closing = false;
  const LABEL = host.getAttribute('aria-label') || 'K線控制';   // 電腦寬度（沒收成一格）時用原本的說明
  // 這裡自己改類別、改樣式也會被下面那個「看那一排有沒有變動」的觀察器當成變動（連「拿掉一個本來就沒有的類別」都算），
  // 不丟掉就會自己叫自己、沒完沒了（寫樣品時整頁因此卡死過）。所以每次自己動完手都呼叫 hush() 把剛剛那一批丟掉。
  let watch = null;
  const hush = () => { if (watch) watch.takeRecords(); };

  const live = () => narrow.matches;
  function keys() {
    return KEYS.map(([id, outer, inner]) => {
      const unit = host.querySelector(`:scope > ${outer}`);
      if (!unit || unit.hidden || getComputedStyle(unit).display === 'none') return null;   // 沒有的（個股頁沒有預測）、被收起來的不算
      return { id, unit, mover: inner ? unit.querySelector(inner) || unit : unit };
    }).filter(Boolean);
  }
  const clear = key => { key.mover.style.translate = ''; key.unit.style.opacity = ''; };

  // 顯示 id 那一顆；其他的收起來
  function show(id) {
    want = id;
    const list = keys();
    if (!list.length) return;
    if (!list.some(key => key.id === id)) id = list[list.length - 1].id;
    if (current && current !== id && !drag) closeOpen(list.find(key => key.id === id).unit);   // 換到別顆時，被收起來的那一顆如果選單還開著就一起收（不然選單會跟著那顆鍵一起變成看不到、卻還開著）
    current = id;
    const on = list.find(key => key.id === id).unit;
    list.forEach(clear);
    host.querySelectorAll(':scope > .ks-on').forEach(unit => { if (unit !== on) unit.classList.remove('ks-on'); });   // 連暫時被隱藏的那一顆也要拿掉記號（它不在 list 裡）
    on.classList.add('ks-on');
    host.dataset.ksAt = String(list.findIndex(key => key.id === id));
    host.dataset.ksCount = String(list.length);
    hush();
  }
  function apply() {
    const on = live();
    root.classList.toggle('ks-live', on);
    host.setAttribute('aria-label', on ? 'K線控制（左右滑切換）' : LABEL);
    if (!on) {
      keys().forEach(key => { clear(key); key.unit.classList.remove('ks-on'); });
      delete host.dataset.ksAt;
      delete host.dataset.ksCount;
      current = null;
      hush();
      return;
    }
    show(want || FIRST);
  }

  // 把這一格裡開著的選單或預測收起來（keep 那一顆的不動）。做法是替使用者再按一次那顆鍵，跟他自己收一樣。
  // 不送 Esc：Esc 在個股頁還會取消正在畫、正選著的線。
  function closeOpen(keep) {
    closing = true;   // 下面那個「滑完不要誤開選單」的擋板不要擋這幾下
    host.querySelectorAll(':is(.forecast-chip, .chart-chip)[aria-expanded="true"]').forEach(key => { if (!keep || !keep.contains(key)) key.click(); });
    closing = false;
    hush();
  }

  // 滑到一半：現在這一顆跟手並變淡，要進來的那一顆從另一邊靠近並變清楚
  function paint(dx) {
    const { list, at } = drag, count = list.length;
    const part = Math.max(-1, Math.min(1, dx / TRAVEL)), side = dx < 0 ? 1 : -1;
    const from = list[at], to = list[(at + side + count) % count];
    list.forEach(key => { if (key !== from && key !== to) { key.unit.style.opacity = '0'; key.mover.style.translate = ''; } });
    from.mover.style.translate = `${dx * SLIDE}px 0`;
    from.unit.style.opacity = String(1 - Math.abs(part));
    to.mover.style.translate = `${side * (1 - Math.abs(part)) * TRAVEL * SLIDE}px 0`;
    to.unit.style.opacity = String(Math.abs(part));
  }

  host.addEventListener('pointerdown', event => {
    if (!live() || !event.isPrimary || event.button) return;
    const list = keys();
    if (list.length < 2 || !event.target.closest('.forecast-control, .dt-ui, .hm-period')) return;
    event.stopPropagation();   // 不讓 chart-menus.js「按下去就打開選單」的那一段接到：這一格裡改成放開才打開（走原本的 click）
    swallowUntil = 0;   // 新的一下開始了：上一次滑完要擋的那一下 click 早該到了，不要連這一下的點擊也擋掉
    drag = { id: event.pointerId, x: event.clientX, y: event.clientY, lx: event.clientX, lt: event.timeStamp, v: 0, mode: null, list, at: list.findIndex(key => key.id === current),
      touch: event.pointerType !== 'mouse', target: event.target.closest('button, a') };
  }, true);
  addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
    if (!drag.mode) {
      if (Math.hypot(dx, dy) < 8) return;
      drag.mode = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y';
      if (drag.mode === 'x') { closeOpen(); clearTimeout(tidy); host.classList.add('ks-drag'); hush(); }
    }
    if (drag.mode !== 'x') return;
    const dt = event.timeStamp - drag.lt;
    if (dt > 0) drag.v = drag.v * .4 + ((event.clientX - drag.lx) / dt) * .6;
    drag.lx = event.clientX; drag.lt = event.timeStamp;
    paint(dx);
  }, true);
  function end(event) {
    if (!drag || event.pointerId !== drag.id) return;
    const done = drag;
    drag = null;
    if (done.mode !== 'x') {
      // 沒有滑：就是點一下，交給原本的 click。
      // 但瀏覽器在「剛滑完」之後的那一下點擊有時不補發 click（Chromium 會把它當成在停住剛剛的滑動；這一格沒收起來、鍵並排時也量得到）。
      // 這一格本來就是滑完接著點，所以保險起見：手指放開後 90 毫秒內沒等到 click，就自己替那顆鍵送一次；晚到的那一下丟掉。
      if (!done.mode && done.touch && done.target && event.type === 'pointerup') {
        const target = done.target;
        pending = { target, timer: setTimeout(() => { pending = null; late = { target, until: performance.now() + 500 }; target.click(); }, 90) };
      }
      return;
    }
    swallowUntil = performance.now() + 450;
    const dx = event.clientX - done.x, { list, at } = done, count = list.length;
    const go = event.type === 'pointerup' && (Math.abs(dx) > 26 || Math.abs(done.v) > .35), side = dx < 0 ? 1 : -1;
    void host.offsetWidth;   // 先讓跟手的位置生效，拿掉 ks-drag 之後才有過場可走
    host.classList.remove('ks-drag');
    hush();
    if (!go || still) { show(go ? list[(at + side + count) % count].id : list[at].id); return; }
    const from = list[at], to = list[(at + side + count) % count];
    // 換：舊的繼續往外走並淡掉，新的走到位。等過場走完再把舊的位移收掉（那時已經看不到了）
    want = current = to.id;
    from.unit.classList.remove('ks-on'); to.unit.classList.add('ks-on');
    from.unit.style.opacity = ''; to.unit.style.opacity = '';
    from.mover.style.translate = `${-side * TRAVEL * SLIDE}px 0`;
    to.mover.style.translate = '0px 0';
    host.dataset.ksAt = String(list.indexOf(to));
    tidy = setTimeout(() => show(to.id), 380);
    hush();
  }
  addEventListener('pointerup', end, true);
  addEventListener('pointercancel', end, true);
  // 滑完放手時瀏覽器可能補發一下 click：不要讓它打開選單
  host.addEventListener('click', event => {
    if (closing) return;
    if (event.isTrusted && pending && pending.target.contains(event.target)) { clearTimeout(pending.timer); pending = null; }   // 瀏覽器自己的 click 來了：不用替它送
    const dupe = event.isTrusted && late && performance.now() < late.until && late.target.contains(event.target);   // 已經替它送過了，這是晚到的那一下
    if (dupe) late = null;
    if (!dupe && performance.now() > swallowUntil) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  // 鍵盤走到哪一顆就顯示哪一顆
  host.addEventListener('focusin', event => {
    if (!live() || drag) return;
    const key = keys().find(one => one.unit.contains(event.target));
    if (key && key.id !== current) show(key.id);
  });
  // 鍵是別的程式放進來的（預測、分析工具），放進來之後再排一次；選了畫線工具就停在分析工具。
  // 只看三種事：那一排多了或少了東西、某一顆被隱藏或放出來、分析工具鍵的「使用中」記號。
  watch = new MutationObserver(list => {
    if (!live() || drag) return;
    const relevant = list.some(item => item.type === 'childList' || item.attributeName === 'hidden' || (item.attributeName === 'class' && item.target.id === 'toolsKey'));
    if (relevant) show(host.querySelector('#toolsKey.is-on') ? 'tools' : want || FIRST);
  });
  watch.observe(host, { childList: true, subtree: true, attributes: true, attributeFilter: ['hidden', 'class'] });
  narrow.addEventListener('change', apply);

  apply();
  window.KeySlot = { show: id => { if (live()) show(id); }, current: () => current };
})();
