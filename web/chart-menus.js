/* 圖上的小鍵與浮出的選單（本人 11-15：參數與功能收起來，互動時才出現）。
   小鍵：<button class="chart-chip" aria-haspopup="true" aria-expanded="false" aria-controls="選單的 id">
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

  document.addEventListener('click', event => {
    const chip = event.target.closest('.chart-chip[aria-controls]');
    const current = openChip();
    if (chip) {
      if (current && current !== chip) set(current, false);
      set(chip, chip.getAttribute('aria-expanded') !== 'true');
      return;
    }
    if (current && (!menuOf(current).contains(event.target) || event.target.closest('[data-menu-close]'))) set(current, false);
  });

  document.addEventListener('keydown', event => {
    const current = event.key === 'Escape' && openChip();
    if (!current) return;
    set(current, false);
    current.focus();
  });

  // 個股頁的均線小鍵：寫著現在的期數（例如 18·45）。期數由 stock.js 畫進 #maControls，這裡只跟著讀。
  const periods = document.getElementById('maControls'), value = document.getElementById('maChipValue');
  if (periods && value) {
    const sync = () => { value.textContent = [...periods.querySelectorAll('input')].map(input => input.value).filter(Boolean).join('·') || '未設定'; };
    new MutationObserver(sync).observe(periods, { childList: true, subtree: true });
    periods.addEventListener('change', sync);
    sync();
  }
})();
