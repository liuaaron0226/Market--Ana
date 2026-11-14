/* 首頁分類收合與搜尋面板（立體重設計 A 包第 4 片；本人 2026-11-14 定案）
   1. 四顆分類鍵：預設都不展開，點哪類開哪類，再點收回，一次只開一類；不記住上次開哪一類。
      開關只改 #workspace 的 data-open-cat 與按鈕的 aria-expanded，收起來的樣子由 home.css 決定。
   2. 右上角的放大鏡：用瀏覽器原生的 <dialog> 當浮起的搜尋面板（Esc 收回、焦點留在面板內都是原生行為）。
   搜尋本身的邏輯仍在 minimal.js，這裡不碰。 */
(function () {
  'use strict';

  const workspace = document.getElementById('workspace');
  const cats = document.getElementById('homeCats');
  if (workspace && cats) {
    const buttons = [...cats.querySelectorAll('button[data-cat]')];
    const open = cat => {
      if (cat) workspace.dataset.openCat = cat;
      else delete workspace.dataset.openCat;
      buttons.forEach(button => button.setAttribute('aria-expanded', String(button.dataset.cat === cat)));
    };
    cats.addEventListener('click', event => {
      const button = event.target.closest('button[data-cat]');
      if (button) open(button.getAttribute('aria-expanded') === 'true' ? '' : button.dataset.cat);
    });
  }

  const dialog = document.getElementById('stockSearchDialog');
  const toggle = document.getElementById('searchToggle');
  if (dialog && toggle && typeof dialog.showModal === 'function') {
    toggle.addEventListener('click', () => { if (!dialog.open) dialog.showModal(); });
    const close = document.getElementById('stockSearchClose');
    if (close) close.addEventListener('click', () => dialog.close());
    // 點面板以外的地方收回。點在背景上時事件目標是 dialog 本身，但點在面板的內距上也是，所以再用座標確認落在面板外。
    // 先比對目標，鍵盤按出來的點擊（座標是 0,0）才不會被當成點在外面。
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const box = dialog.getBoundingClientRect();
      const outside = event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom;
      if (outside) dialog.close();
    });
  }
})();
