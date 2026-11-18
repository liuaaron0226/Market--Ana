"""產生自選股頁「和網站其他頁同一個風格」的三個樣品（本人 2026-11-18）。

本人原話：「我想要你幫我更改一下我自選股頁面的設計，我覺得整體設計風格與我網站其他頁面風格不同，且我不太喜歡你做的金屬光澤，請你幫我重設計」。
（推翻 11-16 選的金屬材質；過程見 .scratch/3d-redesign/issues/O-portfolio-material.md）

網站其他頁（個股頁、首頁）的語言：米色桌面上放米白圓角的「板」（bold.css 的 --slab、--slab-shadow、--r-slab），
鍵是共用的淺色鍵（不是金屬），沒有環境光暈、沒有高光。三頁共同：拿掉金屬（列、篩選鍵、「＋」、總市值字）與背景光暈，
損益條照舊是平的實色。三頁都是「正式的自選股頁＋多一段樣式與程式」：要從預覽伺服器開（/samples/…，或短網址）。
  portfolio-desk-a.html（/qa）照個股頁排：上面一張板（標題＋總市值＋成本與損益，像個股頁的報價卡），
                                 下面一張板（篩選鍵、「＋」、清單；一檔一列，列與列之間一條細線）。助手推薦：和個股頁最像。
  portfolio-desk-b.html（/qb）上面同一張板；清單每一列各是一張小板（賺的浮起、賠的凹下，和原本的高低一樣），沒有外框的大板。
  portfolio-desk-c.html（/qc）全部放進一張板：標題、總市值、篩選鍵、清單都在同一張裡。最精簡（只有一張），少掉的：總數和清單沒有分開。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-portfolio-desk.py"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = (ROOT / "cloud" / "dist" / "portfolio.html").read_text(encoding="utf-8")

# 三頁共同：拿掉金屬與背景光暈，鍵回到網站共用的淺色鍵
COMMON_STYLE = """
  body.pf::before { display: none; }
  #holdingRows .hv-row[data-state] { background-image: none; }
  #listFilter button[aria-pressed="true"], #listFilter .slide-thumb, .pf-add:not([aria-expanded="true"]) { background-image: none; }
  #totalMarketValue.is-metal { background: none; -webkit-background-clip: border-box; background-clip: border-box; color: var(--ink); }
  #holdingRows .hv-name > span, #holdingRows .hv-name em, #holdingRows .pf-tag { color: var(--faint); }
  /* 板：和個股頁同一套 */
  .pf-card { border-radius: var(--r-slab); background: var(--slab); box-shadow: var(--slab-shadow); padding: 22px 18px 18px; margin: 0 0 18px; }
  @media (min-width: 761px) { .pf-card { padding: 26px 32px 24px; } }
  .pf-card .pf-title-row { margin: 0; padding: 0 0 14px; }
  .pf-card .pf-title-row h1 { margin: 0; color: var(--ink); }
  .pf-card .hv-total { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 14px; margin: 0; color: var(--dim); font: 13px var(--sans); }
  .pf-card .hv-total strong { font-size: 30px; }
  .pf-summary { border-bottom: 0; }
  .pf-card .pf-summary, .pf-card.pf-summary { padding-bottom: 0; }
  /* 頂端的鍵（大盤首頁、齒輪）：和個股頁一樣是白色浮起的鍵 */
  .pf-back, .pf-theme { border: 0; border-radius: var(--r-key); background: var(--key); box-shadow: var(--key-shadow); }
  @media (hover: hover) { .pf-back:hover, .pf-theme:hover { background: var(--key); transform: translateY(var(--key-lift)); box-shadow: var(--key-shadow-hover); } }
  .pf-bar { border-bottom: 0; }
"""

# A：兩張板；清單在下面那張，列是平的、列與列之間一條細線
A_STYLE = """
  .pf-list-card .pf-tools { margin-bottom: 6px; }
  #holdingRows { gap: 0; }
  #holdingRows .hv-row, #holdingRows .hv-row[data-state] { border: 0; border-radius: 0; background: transparent; box-shadow: none; transform: none; }
  #holdingRows .hv-row + .hv-row { border-top: 1px solid var(--hair); }
  #holdingRows .hv-top { padding-inline: 2px; }
  #holdingRows .swipe-face { --face: var(--slab); }
  .pf-list-card .pf-group-head { margin-top: 14px; }
"""
# B：上面一張板；清單每一列各一張小板（賺浮、賠凹照舊）
B_STYLE = """
  #holdingRows { gap: 10px; }
  #holdingRows .hv-row[data-state="bull"], #holdingRows .hv-row:is([data-state="flat"], [data-state="none"]) { border-color: transparent; background: var(--slab); box-shadow: var(--key-shadow); }
  #holdingRows .hv-row[data-state="bear"] { background: var(--desk); box-shadow: var(--well-shadow); }
  #holdingRows .swipe-face { --face: var(--slab); }
  #holdingRows .hv-row[data-state="bear"] .swipe-face { --face: var(--desk); }
"""
# C：一張板裝全部；列和 A 一樣是平的
C_STYLE = A_STYLE + """
  .pf-one-card .pf-summary { margin-top: 0; }
  .pf-one-card .hv-total { margin-bottom: 18px; }
"""

WRAP_TWO = """<script>
(function () {
  // 標題那一行與總市值包成上面那一張板；清單那一段成為下面那一張板
  const summary = document.querySelector('.pf-summary'), title = document.querySelector('.pf-title-row'), total = document.getElementById('totalLine');
  const top = document.createElement('section');
  top.className = 'pf-card pf-head-card';
  title.before(top);
  top.append(title, total);
  if (__LIST_CARD__) summary.classList.add('pf-card', 'pf-list-card');
})();
</script>"""
WRAP_ONE = """<script>
(function () {
  // 標題、總市值、篩選鍵、清單都放進同一張板
  const summary = document.querySelector('.pf-summary'), title = document.querySelector('.pf-title-row');
  const card = document.createElement('section');
  card.className = 'pf-card pf-one-card';
  title.before(card);
  card.append(title, summary);
})();
</script>"""

VARIANTS = {
    "a": ("兩張板：總數一張、清單一張（像個股頁）", COMMON_STYLE + A_STYLE, WRAP_TWO.replace("__LIST_CARD__", "true")),
    "b": ("總數一張板，清單每一列各一張小板", COMMON_STYLE + B_STYLE, WRAP_TWO.replace("__LIST_CARD__", "false")),
    "c": ("全部放進一張板", COMMON_STYLE + C_STYLE, WRAP_ONE),
}
for key, (what, style, script) in VARIANTS.items():
    html = PAGE
    assert html.count("<head>") == 1 and html.count("</head>") == 1 and html.count("</body>") == 1
    html = html.replace("<head>", f'<head>\n  <base href="/">\n  <!-- 可丟棄的樣品（由 build-portfolio-desk.py 產生，不要手改）：{what}。 -->', 1)
    html = html.replace("</head>", f"  <style>{style}</style>\n</head>", 1)
    html = html.replace("</body>", f"{script}\n</body>", 1)
    (HERE / f"portfolio-desk-{key}.html").write_text(html, encoding="utf-8", newline="\n")
print("portfolio-desk ok")
