# 五塊新功能 07 的樣品題：「技術」三張板在電腦寬度怎麼排。每個版本一頁、可以單獨打開、可以實際點。
# 頁面裡是正式的樣式與程式（web/ 底下的檔原封不動內嵌），資料是本機算的 11-09 個股分析（.tmp/3d-baseline/signals-preview.json）。
# 只有「排法」那幾行樣式各頁不同。用法：python -X utf8 design-previews/3d-redesign/pro-samples/build-signals-desktop.py
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = json.loads((ROOT / ".tmp/3d-baseline/signals-preview.json").read_text(encoding="utf-8"))
STOCKS = ["2603", "1802", "2317", "3017", "2330"]

LAYOUTS = {
    "1": ("三張並排", "電腦上三張一排，和手機上每一張的樣子一樣、頁面最短。（推薦）",
          ".ss[data-panel='technical'] { grid-template-columns: repeat(3, minmax(0, 1fr)); align-items: start; }"),
    "2": ("兩張並排＋小圖一整列", "和大盤比與均線排列並排；爆大量成交區獨占一列，小圖拉寬。",
          ".ss[data-panel='technical'] { grid-template-columns: repeat(2, minmax(0, 1fr)); align-items: start; }"
          " .ss[data-panel='technical'] .ss-heavy-zones { grid-column: 1 / -1; }"),
    "3": ("一欄、限寬", "和手機一樣一張接一張，寬度限在 560px、靠左（和價位階梯同一個寬度）。",
          ".ss[data-panel='technical'] { grid-template-columns: minmax(0, 560px); }"),
}

css = "\n".join((WEB / name).read_text(encoding="utf-8") for name in ("minimal.css", "bold.css", "stock-facts.css", "stock-signals.css"))
js = "\n".join((WEB / name).read_text(encoding="utf-8") for name in ("stock-signals.js", "stock-facts.js"))
payload = {code: {"code": code, "name": DATA[code]["name"], "facts": DATA[code]["facts"], "signals": DATA[code]["signals"]} for code in STOCKS}

PAGE = """<!doctype html>
<html lang="zh-Hant" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>技術三張板的電腦排法 {key}｜樣品</title>
<style>
{css}
/* ---- 樣品頁自己的外框（不是要做進網站的） ---- */
.sample-bar {{ position: sticky; top: 0; z-index: 9; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 10px 16px;
  background: var(--panel3); color: var(--dim); font: 12px var(--sans); }}
.sample-bar a, .sample-bar button {{ min-width: 36px; height: 32px; padding: 0 10px; display: inline-grid; place-items: center; border: 1px solid var(--line-strong);
  border-radius: 8px; background: transparent; color: var(--ink); font: 600 13px var(--sans); text-decoration: none; cursor: pointer; }}
.sample-bar a[aria-current="page"], .sample-bar button[aria-pressed="true"] {{ background: var(--ink); color: var(--bg); border-color: var(--ink); }}
.sample-bar .note {{ flex: 1 1 260px; line-height: 1.5; }}
.sample-wrap {{ max-width: 1280px; margin: 0 auto; padding: 24px 32px 80px; }}
.sample-wrap h1 {{ font: 600 26px var(--serif); margin: 8px 0 4px; }}
.sample-wrap .sub {{ color: var(--faint); font: 12px var(--mono); }}
/* ---- 這一版的排法 ---- */
@media (min-width: 861px) {{ {layout} }}
</style>
</head>
<body class="bd st">
<div class="sample-bar">
  <span>樣品・版本</span>{links}
  <span class="note">{key}｜{title}：{note}</span>
  <span>股票</span>{stocks}
  <button type="button" id="theme">淺色</button>
</div>
<div class="sample-wrap">
  <h1 id="name"></h1><p class="sub" id="sub"></p>
  <section class="sf-blocks" id="stockFactsBlocks" aria-label="事實整理" hidden></section>
</div>
<script>{js}</script>
<script>
const DATA = {data};
let code = "{first}";
function show() {{
  const r = DATA[code];
  document.getElementById('name').textContent = r.name;
  document.getElementById('sub').textContent = `${{r.code}}・資料日 ${{r.signals.data_date}}・本機資料`;
  document.querySelectorAll('[data-code]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.code === code)));
  window.StockFacts.render(r);
  const tab = [...document.querySelectorAll('.sf-tab')].find(b => b.textContent === '技術');
  if (tab) tab.click();
}}
document.querySelectorAll('[data-code]').forEach(b => b.addEventListener('click', () => {{ code = b.dataset.code; show(); }}));
document.getElementById('theme').addEventListener('click', e => {{
  const dark = document.documentElement.dataset.theme === 'dark';
  document.documentElement.dataset.theme = dark ? 'light' : 'dark';
  e.target.textContent = dark ? '深色' : '淺色';
  const r = DATA[code]; DATA[code] = JSON.parse(JSON.stringify(r)); show();
}});
show();
</script>
</body>
</html>
"""

for key, (title, note, layout) in LAYOUTS.items():
    current = ' aria-current="page"'
    links = "".join(f'<a href="signals-desktop-{k}.html"{current if k == key else ""}>{k}</a>' for k in LAYOUTS)
    stocks = "".join(f'<button type="button" data-code="{c}">{DATA[c]["name"]}</button>' for c in STOCKS)
    html = PAGE.format(key=key, title=title, note=note, layout=layout, css=css, js=js, links=links, stocks=stocks,
                       data=json.dumps(payload, ensure_ascii=False), first=STOCKS[0])
    (OUT / f"signals-desktop-{key}.html").write_text(html, encoding="utf-8")
    print(f"signals-desktop-{key}.html", len(html) // 1024, "KB")
