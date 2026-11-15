/* 首頁把東西收起來的開關（立體重設計 A 包第 4 片、B 包；本人 2026-11-14 定案）
   1. 四顆分類鍵：預設都不展開，點哪類開哪類，再點收回，一次只開一類；不記住上次開哪一類。
      換類讓頁面變短時，會先在最下面墊高度，免得整頁被拉回去跳一下（見下面 showOnly 上方的說明）。
      每顆鍵用 aria-controls 指到自己的區塊；展開時在區塊上加 data-open，收起來的樣子由 home.css 決定。
      要加一類，只要在 minimal.html 多一顆鍵與一個帶 data-cat-panel 的區塊，這裡與樣式都不用改。
   2. 浮起的面板：用瀏覽器原生的 <dialog>（Esc 收回、焦點留在面板內都是原生行為）。
      任何帶 aria-haspopup="dialog" 與 aria-controls 的按鈕都會打開它指到的面板；面板裡帶 data-dialog-close 的按鈕會關掉它。
      目前有兩個：搜尋（右上角的放大鏡）與產業資金流向的排行、回溫訊號、表格（標題列的「排行與表格」）。
   搜尋與各區塊本身的邏輯仍在 minimal.js，這裡不碰。 */
(function () {
  'use strict';

  const bar = document.getElementById('homeCats');
  if (bar) {
    const keys = [...bar.querySelectorAll('button[aria-controls]')];
    // 換到比較矮的一類（或收回）時頁面會變短。如果這時畫面已經捲到新的頁尾以下，瀏覽器會把整頁往下拉回來——
    // 看起來就是整頁跳一下（本人 11-15 用 iPhone 回報）。做法：換類的當下在主要內容的最下面墊上剛好夠的高度，
    // 讓捲動位置不用動；之後使用者往上捲、用不到了再收掉。收的永遠是畫面下緣以下的部分，所以收的時候不會跳。
    const page = document.documentElement, main = bar.closest('main') || document.body;
    const hold = document.createElement('div');
    hold.className = 'hm-cat-hold';
    hold.setAttribute('aria-hidden', 'true');
    main.append(hold);
    let held = 0;   // 現在墊了多高
    const setHold = height => {
      held = Math.max(0, Math.ceil(height));
      hold.style.height = held ? `${held}px` : '';
    };
    // 不墊的話，頁尾比畫面下緣短多少（負的表示不用墊）
    const shortBy = bottom => bottom - (page.scrollHeight - held);
    // 用不到的部分收掉：往上捲的時候；視窗改大小、手機轉向之後也要重算，不然頁尾會多出一段捲得到的空白
    const trimHold = () => { if (held) setHold(Math.min(held, shortBy(window.scrollY + window.innerHeight))); };
    window.addEventListener('scroll', trimHold, { passive: true });
    window.addEventListener('resize', trimHold);

    const showOnly = chosen => {
      const bottom = window.scrollY + window.innerHeight;
      setHold(held + window.innerHeight * 3);   // 先墊很多：換類的過程中頁面不會短到把捲動位置拉回去
      keys.forEach(key => {
        const on = key === chosen;
        key.setAttribute('aria-expanded', String(on));
        const panel = document.getElementById(key.getAttribute('aria-controls'));
        if (panel) panel.toggleAttribute('data-open', on);
      });
      setHold(shortBy(bottom));                 // 再縮成剛好夠的高度
    };
    bar.addEventListener('click', event => {
      const key = event.target.closest('button[aria-controls]');
      if (key) showOnly(key.getAttribute('aria-expanded') === 'true' ? null : key);
    });
  }

  document.querySelectorAll('button[aria-haspopup="dialog"][aria-controls]').forEach(opener => {
    const dialog = document.getElementById(opener.getAttribute('aria-controls'));
    if (!dialog || typeof dialog.showModal !== 'function') return;
    opener.addEventListener('click', () => { if (!dialog.open) dialog.showModal(); });
  });

  document.querySelectorAll('dialog.float-dialog').forEach(dialog => {
    dialog.querySelectorAll('[data-dialog-close]').forEach(closeKey => closeKey.addEventListener('click', () => dialog.close()));
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
  });
})();
