// 本人 2026-11-18：頁面不要讓使用者縮放（手機上有時會自己放大，滑動時還得再調回來）。
// - viewport 的 maximum-scale=1：擋住 iPhone 點輸入框時的自動放大。
// - minimal.css 的 html { touch-action: pan-x pan-y }：擋住雙擊放大，只留捲動；K 線等自己處理手勢的區塊不受影響。
// - iOS Safari 不理 user-scalable=no，雙指縮放要在這裡擋（gesture 事件只有 Safari 有，K 線的雙指用 pointer 事件，不受影響）。
document.addEventListener('gesturestart', event => event.preventDefault(), { passive: false });
