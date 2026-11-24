/* 浮層打開時把底下的頁面鎖住（本人 2026-11-17 用 iPhone 回報：明細浮板沒東西可以往下滑時，手指一滑就一直滑到底下的頁面）。
   全站的浮層共用這一個：首頁的明細板、搜尋與排行兩個對話框、手機貼底的大盤預測浮板、個股頁手機貼底的關注判斷浮板。

   用法：打開時 ScrollLock.lock(浮層元素)，關掉時 ScrollLock.unlock(同一個元素)。同一個元素重複鎖、沒鎖過就解都沒關係；
   幾個浮層同時開著時，最後一個關掉才解開。

   做法：把 body 釘住（position: fixed，往上挪現在捲到的距離，畫面看起來完全不動），關掉時再捲回原本的位置。
   iOS Safari 對 body 的 overflow: hidden 不理會，浮層的 overscroll-behavior: contain 也只在浮層內容真的比框高時才有效——
   內容不夠長、或手指放在標題列、暗底上時，捲動照樣會傳到底下的頁面，所以要用釘住的做法。
   浮層自己的內容照常可以捲（它們本來就是 position: fixed 或瀏覽器的頂層 dialog，不受 body 釘住影響）。
   桌機有捲軸的話，釘住時捲軸會消失、版面會變寬一點；body 右邊留出原本捲軸的寬度，版面寬度不變，K 線圖不用重畫。 */
(function () {
  'use strict';

  const holders = new Set();
  let saved = null;   // 鎖住當下的捲動位置與被改掉的樣式

  const PROPS = ['position', 'top', 'left', 'right', 'width'];

  function pin() {
    const root = document.documentElement, body = document.body;
    const x = window.scrollX, y = window.scrollY;
    const gap = Math.max(0, window.innerWidth - root.clientWidth);
    saved = {
      x, y,
      body: Object.fromEntries(PROPS.map(prop => [prop, body.style.getPropertyValue(prop)])),
      overscroll: root.style.getPropertyValue('overscroll-behavior'),
    };
    body.style.setProperty('position', 'fixed');
    body.style.setProperty('top', `${-y}px`);
    body.style.setProperty('left', `${-x}px`);
    body.style.setProperty('right', `${gap}px`);
    body.style.setProperty('width', 'auto');
    root.style.setProperty('overscroll-behavior', 'none');   // iOS 釘住之後整頁還會回彈，順便關掉
  }

  function release() {
    const root = document.documentElement, body = document.body;
    const { x, y } = saved;
    PROPS.forEach(prop => {
      if (saved.body[prop]) body.style.setProperty(prop, saved.body[prop]);
      else body.style.removeProperty(prop);
    });
    if (saved.overscroll) root.style.setProperty('overscroll-behavior', saved.overscroll);
    else root.style.removeProperty('overscroll-behavior');
    saved = null;
    window.scrollTo({ left: x, top: y, behavior: 'instant' });
  }

  // 浮層開著時點了連結換頁：離開前先放開、捲回原位，瀏覽器記下的捲動位置才不會是 0（按上一頁回來不會跳到頁首）。
  // 從瀏覽器的整頁快取回來時浮層還開著，再鎖回去。
  window.addEventListener('pagehide', () => { if (saved) release(); });
  window.addEventListener('pageshow', event => { if (event.persisted && holders.size && !saved) pin(); });

  window.ScrollLock = {
    // 整頁原本捲到哪：釘住時 window.scrollY 是 0，要用鎖住當下記的位置（nav-return.js 存「離開前的樣子」用）
    pageY() { return saved ? saved.y : window.scrollY; },
    lock(owner) {
      holders.add(owner);
      if (!saved) pin();
    },
    unlock(owner) {
      if (!holders.delete(owner)) return;
      if (!holders.size && saved) release();
    },
  };
})();
