/* 首頁把東西收起來的兩個開關（立體重設計 A 包第 4 片；本人 2026-11-14 定案）
   1. 四顆分類鍵：預設都不展開，點哪類開哪類，再點收回，一次只開一類；不記住上次開哪一類。
      每顆鍵用 aria-controls 指到自己的區塊；展開時在區塊上加 data-open，收起來的樣子由 home.css 決定。
      要加一類，只要在 minimal.html 多一顆鍵與一個帶 data-cat-panel 的區塊，這裡與樣式都不用改。
   2. 右上角的放大鏡：用瀏覽器原生的 <dialog> 當浮起的搜尋面板（Esc 收回、焦點留在面板內都是原生行為）。
   搜尋本身的邏輯仍在 minimal.js，這裡不碰。 */
(function () {
  'use strict';

  const bar = document.getElementById('homeCats');
  if (bar) {
    const keys = [...bar.querySelectorAll('button[aria-controls]')];
    const showOnly = chosen => keys.forEach(key => {
      const on = key === chosen;
      key.setAttribute('aria-expanded', String(on));
      const panel = document.getElementById(key.getAttribute('aria-controls'));
      if (panel) panel.toggleAttribute('data-open', on);
    });
    bar.addEventListener('click', event => {
      const key = event.target.closest('button[aria-controls]');
      if (key) showOnly(key.getAttribute('aria-expanded') === 'true' ? null : key);
    });
  }

  const dialog = document.getElementById('stockSearchDialog');
  const toggle = document.getElementById('searchToggle');
  if (dialog && toggle && typeof dialog.showModal === 'function') {
    toggle.addEventListener('click', () => { if (!dialog.open) dialog.showModal(); });
    const closeKey = document.getElementById('stockSearchClose');
    if (closeKey) closeKey.addEventListener('click', () => dialog.close());

    // 點面板以外的地方收回。點在背景上時事件目標是 dialog 本身，但點在面板的內距上也是，所以再用座標確認落在面板外。
    // 先比對目標，鍵盤按出來的點擊（座標是 0,0）才不會被當成點在外面；
    // 按下與放開都要在外面才算，在輸入框裡拖曳選字、放開時滑到面板外不會誤關。
    const isOutside = event => {
      if (event.target !== dialog) return false;
      const box = dialog.getBoundingClientRect();
      return event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
    };
    let pressedOutside = false;
    dialog.addEventListener('pointerdown', event => { pressedOutside = isOutside(event); });
    dialog.addEventListener('click', event => {
      if (pressedOutside && isOutside(event)) dialog.close();
      pressedOutside = false;
    });
  }
})();
