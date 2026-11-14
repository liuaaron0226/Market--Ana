/* 圖上的小鍵與浮出的選單（本人 11-15：參數與功能收起來，互動時才出現）。
   小鍵：<button class="chart-chip" aria-expanded="false" aria-controls="選單的 id">
   選單：<div class="chart-menu" id="…" hidden>；選單裡帶 data-menu-close 的按鈕按了就收回。
   一次只開一個；按 Esc 或點選單以外的地方收回。週期（period-switch.js）、指標（chart-studies.js）、個股頁的均線都用這一套。 */
(function () {
  'use strict';
  const menuOf = chip => document.getElementById(chip.getAttribute('aria-controls'));
  const openChip = () => document.querySelector('.chart-chip[aria-expanded="true"]');

  function set(chip, open) {
    const menu = menuOf(chip);
    if (!menu) return;
    chip.setAttribute('aria-expanded', String(open));
    menu.hidden = !open;
  }

  // 收回之後把鍵盤焦點放回小鍵。指標的小鍵每一種指標各有一枚，選完之後原本那枚會跟著舊的指標區隱藏，所以找現在看得到的那一枚。
  function close(chip, refocus) {
    set(chip, false);
    if (!refocus) return;
    const id = chip.getAttribute('aria-controls');
    const shown = [...document.querySelectorAll(`.chart-chip[aria-controls="${id}"]`)].find(item => item.getClientRects().length);
    if (shown) shown.focus();
  }

  document.addEventListener('click', event => {
    const chip = event.target.closest('.chart-chip[aria-controls]');
    const current = openChip();
    if (chip) {
      if (current && current !== chip) set(current, false);
      set(chip, chip.getAttribute('aria-expanded') !== 'true');
      return;
    }
    if (!current) return;
    // 用事件當下走過的路徑判斷是不是點在選單裡：均線的刪除鍵按了會重畫，等事件傳到這裡時那顆鍵已經不在頁面上了。
    const inside = event.composedPath().includes(menuOf(current));
    const picked = inside && event.target.closest('[data-menu-close]');
    if (!inside || picked) close(current, Boolean(picked));
  });

  document.addEventListener('keydown', event => {
    const current = event.key === 'Escape' && openChip();
    if (current) close(current, true);
  });

  // 個股頁的均線小鍵：寫著現在的期數（例如 18·45）。期數由 stock.js 畫進 #maControls，這裡只跟著讀。
  // 新增、刪除會重畫（MutationObserver 看得到）；直接改某一格的數字不一定重畫，所以也聽 change。
  const periods = document.getElementById('maControls'), value = document.getElementById('maChipValue');
  if (periods && value) {
    const sync = () => { value.textContent = [...periods.querySelectorAll('input')].map(input => input.value).filter(Boolean).join('·') || '未設定'; };
    new MutationObserver(sync).observe(periods, { childList: true, subtree: true });
    periods.addEventListener('change', sync);
    sync();
  }
})();
