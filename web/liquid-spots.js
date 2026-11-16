/* 液態玻璃與金屬的高光：每一條各自不同、不同步（本人 2026-11-16 在個股頁那條線定的材質：
   「不要每一個呈現的色澤還有反光的樣子都一樣，很不自然」）。目前自選與庫存頁的損益條在用（portfolio.js、portfolio.css「材質」那一段）。

   用法：LiquidSpots.of(key) → 一組樣式設定（直接 setProperty 到那一條上）
     --e1、--e2：高光來回漂的兩個位置（一端在左邊 6～38%，一端在右邊 58～94%；先往哪邊不一定）
     --hi：金屬上那塊窗光的亮度；--hl、--hz、--hs：金屬上緣的亮帶到哪裡、明暗交界在哪裡、交界多深
     --dt：來回漂一趟多久（5.5～11 秒）；--dd：從這一趟的哪裡開始（負的秒數），所以每一條不同步
   key 是認得出這一條的字（例如「2330 base」）：同一個 key 每次算出來都一樣，清單重畫之後高光不會換樣子。 */
(function (global) {
  'use strict';

  // 照文字固定下來的亂數（不用 Math.random：重畫要一樣）
  function seeded(text) {
    let state = 2166136261;
    for (const char of String(text)) state = Math.imul(state ^ char.charCodeAt(0), 16777619);
    return () => {
      state = Math.imul(state ^ (state >>> 15), 2246822507);
      state = Math.imul(state ^ (state >>> 13), 3266489909);
      state ^= state >>> 16;
      return (state >>> 0) / 4294967296;
    };
  }

  function of(key) {
    const next = seeded(key), between = (low, high) => low + next() * (high - low);
    const left = between(6, 38), right = between(58, 94), leftFirst = next() > 0.5;
    return {
      '--e1': `${(leftFirst ? left : right).toFixed(1)}%`,
      '--e2': `${(leftFirst ? right : left).toFixed(1)}%`,
      '--hi': between(0.45, 0.85).toFixed(2),
      '--hl': `${between(12, 26).toFixed(0)}%`,
      '--hz': `${between(46, 60).toFixed(0)}%`,
      '--hs': between(0.18, 0.36).toFixed(2),
      '--dt': `${between(5.5, 11).toFixed(1)}s`,
      '--dd': `${(-between(0, 9)).toFixed(1)}s`,
    };
  }

  global.LiquidSpots = { of };
})(window);
