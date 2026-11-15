"""產生自選與庫存頁「液態玻璃＋金屬」材質的三個樣品（本人 2026-11-16：
「篇外觀設計還有那持股損益那條我覺得顏色跟質感我覺得可以加入液態玻璃加上金屬材質你可以設計一下」）。

材質沿用個股頁那條線本人已經定案的做法（主檢出 `.scratch/stock-signals/samples/q4n-2.html`，規格在同一個資料夾的 spec.md）：
  液態玻璃＝半透明的有色玻璃、邊緣一圈亮、裡面一顆慢慢漂的高光、底下透出一點同色的光；
  金屬＝一塊柔和的窗光落在上面、下半有一點環境的反射、明暗交界是柔的，沒有紋路；
  每一條的高光位置、亮度、漂移的速度各自不同、不同步。
這裡不另創一種，只是把它用到損益條上：成本那一段是金屬，賺的（或還差的）那一段是紅（綠）的液態玻璃。

三頁都是「正式的自選與庫存頁＋多一段樣式」：資料、向左滑、群組、「＋」全部是真的（讀預覽伺服器的資料），只有材質不同。
所以要從預覽伺服器開（/samples/…，或短網址），不能直接用檔案開。
  portfolio-material-a.html（/ma）：只改損益條——凹槽裡，成本是金屬、賺的是紅色的液態玻璃。其他照舊。
  portfolio-material-b.html（/mb）：a ＋ 槽換成玻璃管、選中的篩選鍵與「＋」是金屬鍵、總市值是金屬字。
  portfolio-material-c.html（/mc）：b ＋ 每一列是一片玻璃、背景有很淡的環境光。
陰影只用網站現成的陰影色；漸層、光暈與會漂的高光是這一頁原本沒有的效果。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-portfolio-material.py"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = (ROOT / "web" / "portfolio.html").read_text(encoding="utf-8")

# ---- a：損益條 ----
BAR = """
  @property --ex { syntax: "<percentage>"; inherits: false; initial-value: 30%; }
  @keyframes pf-drift { from { --ex: var(--e1, 10%); } to { --ex: var(--e2, 80%); } }
  /* 損益條：一道凹槽（整條的長度是全部庫存的市值），裡面成本那一段是金屬、賺的那一段是紅色的液態玻璃 */
  .hv-bar { height: 24px; padding: 0 3px; overflow: visible; }
  .hv-bar::before { top: 50%; height: 16px; margin-top: -8px; border-radius: 8px; background: var(--well); box-shadow: var(--well-shadow); }
  .hv-bar i { height: 10px; min-width: 8px; overflow: hidden; background-color: transparent; animation: pf-drift var(--dt, 8s) ease-in-out var(--dd, 0s) infinite alternate; }
  .hv-bar i:first-of-type { border-top-left-radius: 5px; border-bottom-left-radius: 5px; }
  .hv-bar i:last-of-type { border-top-right-radius: 5px; border-bottom-right-radius: 5px; }
  /* 成本：自然的金屬。顏色從墨色與底色調出來（深色背景是亮的鋼色，淺色背景是深的石墨色） */
  .hv-bar .base {
    --base: linear-gradient(90deg, color-mix(in srgb, var(--ink) 76%, var(--bg)), color-mix(in srgb, var(--ink) 46%, var(--bg)));
    background-image:
      radial-gradient(42% 130% at var(--ex) -20%, rgb(255 255 255 / var(--hi, .7)) 0%, transparent 70%),
      radial-gradient(30% 90% at calc(var(--ex) + 34%) 125%, rgb(255 255 255 / .2) 0%, transparent 70%),
      linear-gradient(180deg, rgb(255 255 255 / .2) 0%, transparent var(--hl, 18%), transparent var(--hz, 52%), rgb(0 0 0 / var(--hs, .3)) calc(var(--hz, 52%) + 16%), rgb(0 0 0 / .08) 100%),
      var(--base);
    box-shadow: inset 0 0 0 .5px rgb(0 0 0 / 30%);
  }
  /* 賺的那一段：紅色的液態玻璃；還差成本的那一段：綠色的液態玻璃 */
  .hv-bar .gain { --a: #ef6a55; --b: #c52c30; }
  .hv-bar .lack { --a: #3fd0a0; --b: #118a62; }
  .hv-bar :is(.gain, .lack) {
    background-image:
      radial-gradient(16% 62% at var(--ex) 24%, rgb(255 255 255 / .8) 0%, transparent 72%),
      radial-gradient(38% 80% at calc(var(--ex) + 30%) 118%, rgb(255 255 255 / .34) 0%, transparent 72%),
      linear-gradient(180deg, rgb(255 255 255 / .36) 0%, rgb(255 255 255 / .05) 40%, transparent 62%, rgb(255 255 255 / .2) 100%),
      linear-gradient(90deg, color-mix(in srgb, var(--a) 62%, transparent), color-mix(in srgb, var(--b) 82%, transparent));
    box-shadow: inset 0 0 0 .5px rgb(255 255 255 / 38%), inset 0 1px 0 rgb(255 255 255 / 55%), inset 0 -3px 6px -1px color-mix(in srgb, var(--a) 60%, transparent);
    -webkit-backdrop-filter: blur(4px) saturate(1.5); backdrop-filter: blur(4px) saturate(1.5);
    filter: drop-shadow(0 3px 5px color-mix(in srgb, var(--b) 50%, transparent));
  }
  @media (prefers-reduced-motion: reduce) { .hv-bar i { animation: none; } }
"""

# ---- b：玻璃管、金屬鍵、金屬字 ----
METAL = """
  /* 槽換成一支玻璃管：上緣一道亮邊、管壁一層淡淡的反光 */
  .hv-bar::before {
    background-color: color-mix(in srgb, var(--well) 72%, transparent);
    background-image: linear-gradient(180deg, rgb(255 255 255 / .16) 0%, rgb(255 255 255 / .02) 38%, transparent 62%, rgb(255 255 255 / .09) 100%);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / 30%), inset 0 0 0 .5px rgb(255 255 255 / 16%), inset 0 -4px 6px -3px rgb(255 255 255 / 12%), inset 0 0 0 1px color-mix(in srgb, var(--ink) 10%, transparent);
  }
  /* 選中的那一顆篩選鍵與「＋」：金屬鍵。同一種金屬的做法，光落的位置各自不同；字照舊是墨色 */
  #listFilter button[aria-pressed="true"], #listFilter .slide-thumb, .pf-add:not([aria-expanded="true"]) {
    background-image:
      radial-gradient(46% 120% at var(--mx, 28%) -30%, rgb(255 255 255 / .22) 0%, transparent 70%),
      radial-gradient(34% 80% at calc(var(--mx, 28%) + 36%) 130%, rgb(255 255 255 / .08) 0%, transparent 70%),
      linear-gradient(180deg, rgb(255 255 255 / .08) 0%, transparent 22%, transparent 54%, rgb(0 0 0 / .16) 72%, rgb(0 0 0 / .05) 100%),
      linear-gradient(90deg, color-mix(in srgb, var(--ink) 20%, var(--panel)), color-mix(in srgb, var(--ink) 9%, var(--panel)));
  }
  .pf-add { --mx: 62%; }
  /* 總市值：金屬字。只有這一個數字，亮暗的範圍收窄，字不會糊 */
  #totalMarketValue {
    background: linear-gradient(180deg, var(--ink) 0%, var(--ink) 30%, color-mix(in srgb, var(--ink) 70%, var(--bg)) 58%, color-mix(in srgb, var(--ink) 96%, var(--bg)) 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
  }
"""

# ---- c：每一列是玻璃、背景有環境光 ----
SURFACE = """
  /* 很淡的環境光：玻璃後面要有東西可以透。畫在頁面自己的底上、不跟著捲動 */
  body.bd.pf {
    background-image:
      radial-gradient(70% 34% at 12% 30%, color-mix(in srgb, var(--gold) 26%, transparent), transparent 72%),
      radial-gradient(60% 30% at 100% 46%, color-mix(in srgb, var(--up) 17%, transparent), transparent 72%),
      radial-gradient(80% 40% at 40% 78%, color-mix(in srgb, var(--ink) 11%, transparent), transparent 72%);
    background-attachment: fixed;
  }
  /* 一列一片玻璃：半透明、後面的東西糊掉、上緣一道亮邊。陰影用網站現成的陰影色，不加重 */
  #holdingRows .hv-row[data-state] {
    border-color: transparent;
    background: linear-gradient(155deg, color-mix(in srgb, var(--panel3) 62%, transparent), color-mix(in srgb, var(--panel) 34%, transparent));
    -webkit-backdrop-filter: blur(18px) saturate(170%); backdrop-filter: blur(18px) saturate(170%);
    box-shadow: inset 0 1px 0 rgb(255 255 255 / 26%), inset 0 0 0 1px color-mix(in srgb, var(--ink) 11%, transparent), 0 var(--key-thick) 0 var(--edge), 0 6px 16px rgb(var(--shade) / calc(.10 * var(--shade-k)));
  }
  /* 只在自選的那幾列（沒有庫存）是比較薄的玻璃，不浮起來 */
  #holdingRows .hv-row[data-state="none"] { background: linear-gradient(155deg, color-mix(in srgb, var(--panel) 40%, transparent), color-mix(in srgb, var(--panel) 20%, transparent)); box-shadow: inset 0 1px 0 rgb(255 255 255 / 16%), inset 0 0 0 1px color-mix(in srgb, var(--ink) 10%, transparent); }
  #holdingRows .swipe-face { --face: var(--panel); }
  #listFilter { background: color-mix(in srgb, var(--panel) 36%, transparent); -webkit-backdrop-filter: blur(14px) saturate(160%); backdrop-filter: blur(14px) saturate(160%); }
"""

# 每一條的高光位置、亮度、明暗交界、漂移的速度各自不同、不同步。清單會重畫，所以照代號固定下來，重畫之後還是同一個樣子
SCRIPT = """<script>
(function () {
  'use strict';
  const host = document.getElementById('holdingRows');
  const seeded = text => { let h = 2166136261; for (const c of text) h = Math.imul(h ^ c.charCodeAt(0), 16777619); return () => { h = Math.imul(h ^ (h >>> 15), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909); h ^= h >>> 16; return (h >>> 0) / 4294967296; }; };
  const dress = () => host.querySelectorAll('.hv-bar i:not([data-mat])').forEach(bar => {
    bar.dataset.mat = '1';
    const next = seeded(bar.closest('.hv-row').dataset.code + bar.className), rnd = (low, high) => low + next() * (high - low);
    const from = rnd(6, 38), to = rnd(58, 94), flip = next() > .5, set = (name, value) => bar.style.setProperty(name, value);
    set('--e1', (flip ? to : from).toFixed(1) + '%'); set('--e2', (flip ? from : to).toFixed(1) + '%');
    set('--hi', rnd(.45, .85).toFixed(2)); set('--hl', rnd(12, 26).toFixed(0) + '%'); set('--hz', rnd(46, 60).toFixed(0) + '%'); set('--hs', rnd(.18, .36).toFixed(2));
    set('--dt', rnd(5.5, 11).toFixed(1) + 's'); set('--dd', (-rnd(0, 9)).toFixed(1) + 's');
  });
  new MutationObserver(dress).observe(host, { childList: true, subtree: true });
  dress();
})();
</script>"""

VARIANTS = {
    "a": ("只改損益條：凹槽裡金屬的成本、紅色液態玻璃的賺", BAR),
    "b": ("損益條＋玻璃管、金屬鍵、總市值金屬字", BAR + METAL),
    "c": ("b＋每一列是玻璃、背景有環境光", BAR + METAL + SURFACE),
}

for key, (what, style) in VARIANTS.items():
    html = PAGE
    assert html.count("<head>") == 1 and html.count("</head>") == 1 and html.count("</body>") == 1
    # 樣品放在 /samples/ 底下：<base> 讓頁面裡的樣式、程式與連結照正式頁面的位置去找
    html = html.replace("<head>", f'<head>\n  <base href="/">\n  <!-- 可丟棄的樣品（由 build-portfolio-material.py 產生，不要手改）：{what}。正式的頁面加一段樣式而已。 -->', 1)
    html = html.replace("</head>", f"  <style>{style}</style>\n</head>", 1)
    html = html.replace("</body>", f"{SCRIPT}\n</body>", 1)
    html = html.replace("<title>自選股與庫存｜台股決策鏡</title>", f"<title>樣品 {key}：{what}</title>", 1)
    (HERE / f"portfolio-material-{key}.html").write_text(html, encoding="utf-8", newline="\n")
print("material ok")
