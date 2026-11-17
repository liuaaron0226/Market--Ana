/* 向左滑露出動作的列（本人 2026-11-16：刪除庫存改成把那一列向左滑、露出紅色的刪除，點一下就刪）。
   自選與庫存頁（portfolio.js）、均線設定的列（ma-settings.js）在用；共用樣式在 swipe-rows.css。

   頁面要畫出這樣的結構（類別名稱是約定）：
     .swipe-row                這一列
       .swipe-clip             上面那一條的框（裁掉滑出去的部分）
         .swipe-acts[inert]    墊在下面的動作，裡面是 button.swipe-act（幾顆都可以，寬度照實際量）
         .swipe-face           會滑動的那一條；裡面可以有 button.swipe-more（「⋯」：滑鼠、鍵盤與螢幕報讀用它把動作滑出來）
   用法：const swipe = SwipeRows.bind(host, { onAct(button, row) { … } })
     host 是清單的外層（列可以隨時重畫）。onAct：某個動作被按了（手指放開、滑鼠放開或鍵盤按下）。
     swipe.close() 把滑開的那一列收回去。
   手指、滑鼠按住往左拖都可以；鍵盤在那一列裡按 ← 滑出來並把焦點放到第一個動作，Esc 或 → 收回。
   滑開著的時候點那一列是收回去，不會觸發列上原本的點擊。

   手勢的判斷另外開出來給測試用（cloud/test/swipe-rows.test.js）：
     SwipeRows.follow(from, dx, width)  按下去時在 from（0 或 -width）、手指橫向走了 dx，現在該在哪
     SwipeRows.intent(dx, dy)           這一下是 'swipe' 滑、'scroll' 上下捲，還是 null 還看不出來
     SwipeRows.settle({ at, last, width, cancelled })  放開時要不要停在打開 */
(function (global) {
  'use strict';

  const START = 8;      // 橫向動超過這麼多才算開始滑（px）
  const OVER = 0.25;    // 滑過頭之後只跟這個比例
  const NUDGE = 18;     // 放開時還在往左、而且至少滑出這麼多，就算沒過一半也打開（px）
  const GHOST_MS = 350; // 滑完或按完動作之後，瀏覽器補發的那一下點擊最晚多久會到

  function follow(from, dx, width) {
    const x = from + dx;
    if (x >= 0) return 0;
    return x < -width ? -width + (x + width) * OVER : x;
  }

  function intent(dx, dy) {
    const across = Math.abs(dx), along = Math.abs(dy);
    if (along > 10 && along > across) return 'scroll';
    return across >= START && across >= along * 1.2 ? 'swipe' : null;
  }

  function settle({ at, last, width, cancelled }) {
    if (cancelled) return false;
    return at < -width / 2 || (last != null && at < last && at < -NUDGE);
  }

  function bind(host, { onAct }) {
    let swiped = null;    // 現在滑開的是哪一列
    let drag = null;      // 手指按著的這一下
    let pressed = null;   // 按下去的是哪一個動作
    let ghost = null;     // 剛滑完或按完動作的那一下：隨後補發的點擊不算（哪一根手指、什麼時候）。下一次按下去就解除
    let back = null;      // 用鍵盤滑開之前焦點在哪

    const rowOf = node => (node instanceof Element ? node.closest('.swipe-row') : null);
    // 滑動的範圍只有上面那一條（.swipe-clip）。同一列裡點開的內容不算：滑開著的時候按那裡，是收回去並照常按下去
    const onStrip = (node, row) => node instanceof Element && node.closest('.swipe-clip') === row.querySelector('.swipe-clip');
    const widthOf = row => row.querySelector('.swipe-acts').offsetWidth;

    function set(row, open) {
      if (!row) return;
      if (open && swiped && swiped !== row) set(swiped, false);
      row.querySelector('.swipe-face').style.transform = open ? `translateX(${-widthOf(row)}px)` : '';
      row.classList.toggle('is-swiped', open);
      row.querySelector('.swipe-acts').inert = !open;
      row.querySelector('.swipe-more')?.setAttribute('aria-expanded', String(open));
      if (open) swiped = row;
      else if (swiped === row) swiped = null;
    }
    const close = () => { if (swiped && !swiped.isConnected) swiped = null; set(swiped, false); };

    // 滑開著的時候按別的地方（別的列、這一列點開的內容，或清單外面）：收回去
    document.addEventListener('pointerdown', event => {
      ghost = null;   // 新的一下開始了：之後的點擊是這一下的，不是補發的
      if (swiped && !swiped.isConnected) swiped = null;
      if (swiped && !onStrip(event.target, swiped)) set(swiped, false);
    }, true);

    host.addEventListener('pointerdown', event => {
      if (drag && event.pointerId !== drag.id) return;   // 已經有一根手指按著：第二根不算，不然第一列會卡在半開
      pressed = event.target.closest('.swipe-act');
      const face = event.target.closest('.swipe-face');
      if (!face || (event.pointerType === 'mouse' && event.button !== 0) || event.target.closest('.swipe-more')) return;
      const row = rowOf(face), width = widthOf(row);
      drag = { id: event.pointerId, row, face, width, x: event.clientX, y: event.clientY, from: row === swiped ? -width : 0, at: null, last: null, moved: false };
    });

    host.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.id) return;
      const dx = event.clientX - drag.x, dy = event.clientY - drag.y;
      if (!drag.moved) {
        const kind = intent(dx, dy);
        if (kind === 'scroll') { drag = null; return; }
        if (kind !== 'swipe') return;
        drag.moved = true;
        drag.row.classList.add('is-dragging');
        try { drag.face.setPointerCapture(drag.id); } catch (_) { /* 沒有也照樣跟著手指 */ }
      }
      drag.last = drag.at;
      drag.at = follow(drag.from, dx, drag.width);
      drag.face.style.transform = `translateX(${drag.at}px)`;
    });

    function release(event, cancelled) {
      if (!drag || event.pointerId !== drag.id) return;
      const done = drag;
      drag = null;
      if (!done.row.isConnected) return;
      if (!done.moved) {
        // 滑開著的時候點那一列（沒有滑動）：收回去。在放開時就做，不等補發的點擊
        if (!cancelled && done.row === swiped) { set(done.row, false); ghost = { id: event.pointerId, at: event.timeStamp }; }
        return;
      }
      done.row.classList.remove('is-dragging');
      set(done.row, settle({ ...done, cancelled }));
      ghost = { id: event.pointerId, at: event.timeStamp };
    }
    // 放開與取消聽整份文件：在清單外面放開也收得到，按著的那一下才不會留著
    document.addEventListener('pointerup', event => {
      release(event, false);
      // 動作在手指放開時就做，不等瀏覽器補發的點擊：快速一撥之後，瀏覽器會把緊接著的那一下點擊吃掉（Chromium 實測）。
      // 做完頁面會重畫，隨後補發的點擊會落在換上來的東西上，所以那一下一併不算。
      const act = pressed;
      pressed = null;
      if (act && act.isConnected && event.target instanceof Element && event.target.closest('.swipe-act') === act) { ghost = { id: event.pointerId, at: event.timeStamp }; onAct(act, rowOf(act)); }
    });
    document.addEventListener('pointercancel', event => { pressed = null; release(event, true); });
    host.addEventListener('dragstart', event => { if (event.target.closest('.swipe-face')) event.preventDefault(); });

    // 補發的點擊不算（鍵盤按下去的 detail 是 0，照常）；滑開著的時候點那一列是收回去
    host.addEventListener('click', event => {
      const late = ghost;
      ghost = null;
      // 只擋同一根手指（或滑鼠）緊接著的那一下；瀏覽器沒說是哪一根（click 沒有 pointerId）時只看時間
      if (late && event.detail !== 0 && event.timeStamp - late.at < GHOST_MS && (!(event.pointerId >= 0) || event.pointerId === late.id)) { event.preventDefault(); event.stopPropagation(); return; }
      const row = rowOf(event.target);
      if (!row) return;
      const more = event.target.closest('.swipe-more');
      if (more) {
        event.stopPropagation();
        const open = row !== swiped;
        set(row, open);
        // 用鍵盤或螢幕報讀按的（不是滑鼠點的）：焦點直接放到第一個動作，不用倒著找
        if (open && event.detail === 0) { back = more; row.querySelector('.swipe-act').focus(); }
        return;
      }
      const act = event.target.closest('.swipe-act');
      if (act) { event.stopPropagation(); onAct(act, row); return; }
      if (row === swiped && onStrip(event.target, row)) { event.preventDefault(); event.stopPropagation(); set(row, false); }
    }, true);

    host.addEventListener('keydown', event => {
      const row = rowOf(event.target);
      if (!row) return;
      if (event.key === 'ArrowLeft' && event.target.closest('.swipe-face') && !event.target.matches('input, textarea, select')) {
        back = event.target;
        set(row, true);
        row.querySelector('.swipe-act').focus();
        event.preventDefault();
      } else if ((event.key === 'Escape' || event.key === 'ArrowRight') && event.target.matches('.swipe-act')) {
        set(row, false);
        (back && row.contains(back) ? back : row.querySelector('.swipe-face a, .swipe-face button')).focus();   // 只回到同一列裡原本的位置
        back = null;
        event.preventDefault();
      }
    });

    return { close };
  }

  global.SwipeRows = { follow, intent, settle, bind, GHOST_MS };
})(window);
