/* 手機鍵盤彈出時，告訴樣式「現在看得到的範圍」（本人 2026-11-20 用 iPhone 回報：搜尋的頁面最下面幾檔股票會被鍵盤擋住）。

   iPhone 的鍵盤不會讓頁面變矮（100dvh、position: fixed 的浮層都還是照整個畫面算），只有 visualViewport 會變矮，
   而且 iOS 為了讓輸入框露出來，有時還會把整個畫面往上推一段（visualViewport.offsetTop）。
   浮層開著時底下的頁面是釘住的（scroll-lock.js），沒辦法靠捲頁面把被擋住的部分捲出來，所以浮層要自己縮到鍵盤上緣以上。

   鍵盤開著時在 <html> 上寫：
     data-keyboard            有這個屬性表示鍵盤開著
     --vv-top / --vv-height   看得到的範圍：離畫面上緣多遠、多高（px）
     --kb-inset               畫面下方被鍵盤蓋住多高（px）
   鍵盤收起來就全部拿掉，樣式裡的後備值（var(--vv-height, 100dvh) 之類）照原本的算，沒有鍵盤的裝置完全不受影響。
   數字一變就在 window 上發 keyboardinset 事件（樣式已經套好之後）。
   用的地方：首頁的浮起面板（home.css dialog.float-dialog）、頁尾留白（minimal.css body::after）、選股下拉（stock-picker.js 聽 keyboardinset）。 */
(function () {
  'use strict';

  const viewport = window.visualViewport;
  if (!viewport) return;
  const root = document.documentElement;
  const KEYBOARD_MIN = 120;   // 被蓋住超過這麼高才算鍵盤（網址列收合、橫向捲軸之類的小變化不算）
  let frame = 0;

  let shown = '';   // 現在寫在 <html> 上的是哪一組數字；一樣就不重寫

  function apply() {
    frame = 0;
    const covered = Math.round(window.innerHeight - viewport.height - viewport.offsetTop);
    const open = covered >= KEYBOARD_MIN;
    const values = open ? [Math.round(viewport.offsetTop), Math.round(viewport.height), covered] : [];
    if (values.join() === shown) return;
    shown = values.join();
    root.toggleAttribute('data-keyboard', open);
    ['--vv-top', '--vv-height', '--kb-inset'].forEach((name, index) => {
      if (open) root.style.setProperty(name, `${values[index]}px`);
      else root.style.removeProperty(name);
    });
    // 樣式已經照新的範圍排好了；要自己算位置的（選股下拉）聽這個事件再算，才不會算在頁尾留白出現之前
    window.dispatchEvent(new Event('keyboardinset'));
  }
  const schedule = () => { if (!frame) frame = requestAnimationFrame(apply); };

  viewport.addEventListener('resize', schedule);
  viewport.addEventListener('scroll', schedule);
  apply();
})();
