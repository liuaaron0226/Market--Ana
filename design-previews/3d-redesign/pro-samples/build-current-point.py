"""產生首頁「現價落點」的三個樣品（本人 2026-11-17：「我想你在幫我設計首頁的現價落點」）。

現況：首頁點報價會開一張「現價落點」面板，全是字（這代表兩句、要注意兩句、四格數字、三列狀態），
和已經改好的收復線／防守線面板（一句＋價位階梯圖）在說同一件事。

三頁都是「正式的首頁＋多一段樣式與程式」：要從預覽伺服器開（/samples/…，或短網址）。數字是預覽的樣本（11-06 收盤）。
  current-point-a.html（/ca）一把直尺：點報價開的面板只有一句話＋一把照價位高低畫的直尺（收復線、現價、防守線），
                                        最下面一行小字寫行情狀態與時間。助手推薦：最精簡。
  current-point-b.html（/cb）同一張圖：點報價直接開收復線那張（一句＋五列的階梯圖，含過去隔天收在哪裡的比例），不另外一張。
  current-point-c.html（/cc）放在首頁上：報價下面直接一條細的橫尺（防守線—現價—收復線），不用點；點它開收復線那張。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-current-point.py"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = (ROOT / "cloud" / "dist" / "minimal.html").read_text(encoding="utf-8")   # 打包後的首頁（web/minimal.html 帶的是本機資料檔，預覽上讀不到資料）

# 預覽樣本的數字（11-06）
PRICE, CHANGE, PCT, UPPER, LOWER = 39701.64, -1873.08, -4.51, 40435.83, 37837.05

COMMON_STYLE = """
  /* 直尺：照價位高低畫，上面高、下面低（和 K 線一樣）。兩條線是細橫線加小價籤，現價是一顆墨色的點。只用網站現成的顏色 */
  .cp-point { margin: 0; font: 600 19px/1.55 var(--sans); color: var(--ink); text-wrap: pretty; }
  .cp-point b { font: 600 19px var(--mono); font-variant-numeric: tabular-nums; }
  .cp-ruler { position: relative; height: 220px; margin: 22px 0 6px; }
  .cp-ruler::before { content: ""; position: absolute; left: 92px; top: 0; bottom: 0; width: 2px; background: color-mix(in srgb, var(--ink) 9%, transparent); }
  .cp-band { position: absolute; left: 92px; width: 2px; background: var(--gold); opacity: .55; }
  .cp-mark { position: absolute; left: 0; right: 0; display: flex; align-items: center; gap: 10px; transform: translateY(-50%); font-size: 13px; color: var(--dim); }
  .cp-mark .tag { width: 78px; flex: none; text-align: right; }
  .cp-mark .tag i { padding: 2px 6px; border-radius: var(--r-tag); background: var(--tone); color: var(--bg); font: 600 12px var(--sans); font-style: normal; }
  .cp-mark .tick { flex: none; width: 16px; height: 0; margin-left: -1px; border-top: 2px solid var(--tone); }
  .cp-mark strong { font: 500 15px var(--mono); font-variant-numeric: tabular-nums; color: var(--ink); }
  .cp-mark span { margin-left: auto; font-size: 12px; color: var(--faint); white-space: nowrap; }
  .cp-mark.is-now .tag { font: 600 13px var(--sans); color: var(--ink); }
  .cp-mark.is-now .dot { flex: none; width: 14px; height: 14px; margin-left: -6px; border-radius: 50%; background: var(--ink); box-shadow: 0 0 0 4px var(--panel); }
  .cp-mark.is-now strong { font-size: 17px; font-weight: 600; }
  .cp-foot { margin: 14px 0 0; padding-top: 12px; border-top: 1px solid var(--hair); font-size: 12px; color: var(--faint); }
  .cp-foot b { font-family: var(--mono); font-weight: 500; color: var(--dim); }
"""

# 直尺：上下各留一點，把收復線、防守線、現價都放進去
RULER_JS = r"""
  const P = __P__;
  const fmt = (v, d = 0) => Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const zone = P.price >= P.upper ? '收復線上方' : P.price < P.lower ? '防守線下方' : '兩線之間';
  function sentence() {
    if (P.price >= P.upper) return `現在 <b>${fmt(P.price)}</b>，已經站回收復線上方 <b>${fmt(P.price - P.upper)}</b> 點。`;
    if (P.price < P.lower) return `現在 <b>${fmt(P.price)}</b>，已經跌破防守線 <b>${fmt(P.lower - P.price)}</b> 點。`;
    return `現在 <b>${fmt(P.price)}</b>，在兩線之間：往上還差 <b>${fmt(P.upper - P.price)}</b> 點到收復線。`;
  }
  function ruler() {
    const hi = Math.max(P.upper, P.price), lo = Math.min(P.lower, P.price), pad = (hi - lo) * .14;
    const top = hi + pad, bottom = lo - pad, y = v => ((top - v) / (top - bottom) * 100).toFixed(2) + '%';
    const mark = (cls, tone, tag, value, note) => `<div class="cp-mark ${cls}" style="top:${y(value)};--tone:${tone}"><span class="tag">${tag}</span>${cls === 'is-now' ? '<i class="dot"></i>' : '<i class="tick"></i>'}<strong class="num">${fmt(value)}</strong><span>${note}</span></div>`;
    return `<div class="cp-ruler" role="img" aria-label="收復線 ${fmt(P.upper)}，現價 ${fmt(P.price)}，防守線 ${fmt(P.lower)}">
      <i class="cp-band" style="top:${y(P.upper)};bottom:calc(100% - ${y(P.lower)})"></i>
      ${mark('', 'var(--gold)', '<i>收復線</i>', P.upper, P.price < P.upper ? `還差 ${fmt(P.upper - P.price)} 點` : '已站回')}
      ${mark('is-now', 'var(--ink)', '現價', P.price, zone)}
      ${mark('', 'var(--down)', '<i>防守線</i>', P.lower, P.price >= P.lower ? `還有 ${fmt(P.price - P.lower)} 點` : '已跌破')}
    </div>`;
  }
""".replace("__P__", f"{{ price: {PRICE}, upper: {UPPER}, lower: {LOWER} }}")

# A：點報價開的面板換成一句＋直尺＋一行小字
A_SCRIPT = """<script>
(function () {
  'use strict';
""" + RULER_JS + """
  const body = document.getElementById('panelBody'), title = document.getElementById('panelTitle');
  const dress = () => {
    if (title.textContent !== '現價落點' || body.querySelector('.cp-ruler')) return;
    const state = (document.getElementById('marketState') || {}).textContent || '延遲';
    body.innerHTML = `<p class="cp-point">${sentence()}</p>${ruler()}
      <p class="cp-foot">判斷看收盤；兩條線照上一個收盤畫，盤中不會跟著改。行情 <b>${state.trim() || '延遲'}</b>・<b>2026-11-06</b></p>`;
  };
  new MutationObserver(dress).observe(body, { childList: true, subtree: true });
})();
</script>"""

# B：點報價直接開收復線那張
B_SCRIPT = """<script>
(function () {
  'use strict';
  // 報價、行情狀態鍵、K 線上的現價點：都改成打開收復線那一張（不另外一張「現價落點」）
  ['priceSummary', 'statusTrigger', 'currentPoint'].forEach(id => {
    const node = document.getElementById(id);
    if (node) node.addEventListener('click', event => { event.stopImmediatePropagation(); document.getElementById('lineUpper').click(); }, true);
  });
})();
</script>"""

# C：報價下面一條細橫尺，不用點；點它開收復線那張
C_STYLE = """
  .cp-strip { grid-column: 1 / -1; justify-self: stretch; display: grid; gap: 4px; width: 100%; max-width: none; margin: 4px 0 0; padding: 0; border: 0; background: none; color: inherit; font: inherit; text-align: left; cursor: pointer; }
  .cp-strip-track { position: relative; height: 22px; }
  .cp-strip-track::before { content: ""; position: absolute; left: 0; right: 0; top: 50%; height: 2px; margin-top: -1px; background: color-mix(in srgb, var(--ink) 9%, transparent); }
  .cp-strip-band { position: absolute; top: 50%; height: 2px; margin-top: -1px; background: var(--gold); opacity: .55; }
  .cp-strip-line { position: absolute; top: 3px; bottom: 3px; width: 2px; margin-left: -1px; background: var(--tone); }
  .cp-strip-dot { position: absolute; top: 50%; width: 12px; height: 12px; margin: -6px 0 0 -6px; border-radius: 50%; background: var(--ink); box-shadow: 0 0 0 3px var(--bg); }
  .cp-strip-now, .cp-strip-labels { position: relative; height: 18px; font: 500 12px var(--mono); color: var(--faint); }
  .cp-strip-now span, .cp-strip-labels span { position: absolute; white-space: nowrap; }
  .cp-strip-now span { transform: translateX(-50%); color: var(--ink); font: 600 12px var(--sans); }
  .cp-strip-labels .is-right { transform: translateX(-100%); }
"""
C_SCRIPT = """<script>
(function () {
  'use strict';
""" + RULER_JS + """
  // 橫尺：左低右高。兩條線與現價都放進去，兩邊各留一點
  const hi = Math.max(P.upper, P.price), lo = Math.min(P.lower, P.price), pad = (hi - lo) * .06;
  const x = v => ((v - (lo - pad)) / ((hi + pad) - (lo - pad)) * 100).toFixed(2) + '%';
  const strip = document.createElement('button');
  strip.type = 'button';
  strip.className = 'cp-strip';
  strip.setAttribute('aria-label', `現價 ${fmt(P.price)}，${zone}；收復線 ${fmt(P.upper)}，防守線 ${fmt(P.lower)}。點開看收復線與防守線`);
  strip.innerHTML = `<span class="cp-strip-now" aria-hidden="true"><span style="left:${x(P.price)}">現價・${zone}</span></span>
    <span class="cp-strip-track" aria-hidden="true">
      <i class="cp-strip-band" style="left:${x(P.lower)};right:calc(100% - ${x(P.upper)})"></i>
      <i class="cp-strip-line" style="left:${x(P.lower)};--tone:var(--down)"></i>
      <i class="cp-strip-line" style="left:${x(P.upper)};--tone:var(--gold)"></i>
      <i class="cp-strip-dot" style="left:${x(P.price)}"></i></span>
    <span class="cp-strip-labels" aria-hidden="true"><span style="left:${x(P.lower)}">防守 ${fmt(P.lower)}</span><span class="is-right" style="left:${x(P.upper)}">收復 ${fmt(P.upper)}</span></span>`;
  strip.addEventListener('click', () => document.getElementById('lineUpper').click());
  const summary = document.getElementById('priceSummary');
  document.querySelector('.time-grid').insertAdjacentElement('afterend', strip);   // 行情狀態那一行下面、整條寬
  // 報價本身也改成開收復線那一張
  summary.addEventListener('click', event => { event.stopImmediatePropagation(); document.getElementById('lineUpper').click(); }, true);
})();
</script>"""

VARIANTS = {
    "a": ("一把直尺：點報價開的面板只有一句話＋直尺", COMMON_STYLE, A_SCRIPT),
    "b": ("同一張圖：點報價直接開收復線那張", "", B_SCRIPT),
    "c": ("放在首頁上：報價下面一條細橫尺", COMMON_STYLE + C_STYLE, C_SCRIPT),
}
for key, (what, style, script) in VARIANTS.items():
    html = PAGE
    assert html.count("<head>") == 1 and html.count("</head>") == 1 and html.count("</body>") == 1
    html = html.replace("<head>", f'<head>\n  <base href="/">\n  <!-- 可丟棄的樣品（由 build-current-point.py 產生，不要手改）：{what}。 -->', 1)
    html = html.replace("</head>", f"  <style>{style}</style>\n</head>", 1)
    html = html.replace("</body>", f"{script}\n</body>", 1)
    (HERE / f"current-point-{key}.html").write_text(html, encoding="utf-8", newline="\n")
print("current-point ok")
