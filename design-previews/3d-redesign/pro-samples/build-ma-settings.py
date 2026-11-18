"""產生首頁大盤 K 線「自己設定均線」的三個樣品（本人 2026-11-17）。

本人原話：「五日線不就是ma5?，另外我希望我可以自己設定均線，不要有上限,上方那一行」；問設定入口放哪裡時：「請你給我樣品我再決定」。

三頁都是「正式的首頁＋多一段樣式與程式」：要從預覽伺服器開（/samples/…，或短網址）。數字是預覽的樣本（11-06 收盤）。
三頁共同：均線條數不設上限、天數不設上限（只受資料長度限制）；改了立刻畫在圖上；
預測線是均線時名稱改成 MA 寫法（五日線 → MA5，前面一小段虛線，和自設的均線分得出來）。
  ma-settings-a.html（/ga）點上方那一行的均線名稱打開：浮出一小塊，每條一列（天數可改、向左滑刪除），最下面「＋ 加一條均線」。
                              畫面上不多任何鍵。助手推薦：最精簡。少掉的：沒有看得到的「設定」字樣，第一次要知道名稱可以點。
  ma-settings-b.html（/gb）上方那一行最後多一顆「＋」：點它打開同一塊（名稱本身也可以點）。比較好發現，多一顆鍵。
  ma-settings-c.html（/gc）跟個股頁一樣放在圖下方：一列天數輸入框，每個後面一顆 ×，最後「＋」。多佔一列位置。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/build-ma-settings.py"""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PAGE = (ROOT / "cloud" / "dist" / "minimal.html").read_text(encoding="utf-8")   # 打包後的首頁（web/minimal.html 帶的是本機資料檔）

# 在 kchart.js 執行前先放一個陷阱：首頁建出 K 線圖時把那一張記下來（樣品要直接改它的均線）
HEAD_SCRIPT = """<script>
(function () {
  let Real;
  Object.defineProperty(window, 'KChart', { configurable: true, get: () => Real, set(value) {
    Real = class extends value { constructor(...args) { super(...args); if (!window.__homeK) window.__homeK = this; } };
  } });
})();
</script>"""

COMMON_STYLE = """
  /* 均線的顏色：先用網站現成的四個，再接四個樣品用的（正式做時再驗色差） */
  :root { --ma5: #7b5cc4; --ma6: #2a9a8c; --ma7: #a8642a; --ma8: #c0577f; }
  /* 浮出那一塊：米白卡片＋網站現成的浮出卡陰影 */
  .ma-pop { position: absolute; z-index: 30; top: var(--legend-h, 80px); left: 0; width: min(300px, calc(100% - 8px)); padding: 10px 10px 8px;
    border: 1px solid var(--line-strong); border-radius: 12px; background: var(--panel); box-shadow: 0 14px 36px rgb(0 0 0 / 18%); }
  .ma-pop[hidden] { display: none; }
  .ma-pop-head { display: flex; align-items: center; justify-content: space-between; margin: 0 0 6px; padding: 0 2px; }
  .ma-pop-head b { font: 600 14px var(--sans); color: var(--ink); }
  .ma-pop-head span { font-size: 12px; color: var(--faint); }
  .ma-pop-close { width: 36px; height: 36px; border: 1px solid var(--line); border-radius: 9px; background: transparent; color: var(--ink); font-size: 18px; }
  .ma-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
  .ma-list .swipe-row { border-radius: 9px; }
  .ma-list .swipe-face { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 0 10px; background: var(--bg); border-radius: 9px; }
  .ma-list .swatch { flex: none; width: 18px; height: 3px; border-radius: 2px; background: var(--tone); }
  .ma-list .ma-name { font: 600 13px var(--sans); color: var(--tone); min-width: 46px; }
  .ma-list input { width: 64px; height: 34px; padding: 0 8px; border: 1px solid var(--line); border-radius: 8px; background: var(--panel); color: var(--ink); font: 500 15px var(--mono); }
  .ma-list .unit { font-size: 13px; color: var(--dim); }
  .ma-list .hint { margin-left: auto; font-size: 11px; color: var(--faint); }
  .ma-add { width: 100%; min-height: 44px; margin-top: 6px; border: 1px dashed var(--line-strong); border-radius: 9px; background: transparent; color: var(--ink); font: 500 14px var(--sans); }
  /* 向左滑露出刪除（和自選庫存頁同一套，樣式從 portfolio.css 抄過來） */
  .swipe-clip { position: relative; overflow: hidden; border-radius: inherit; }
  .swipe-face { touch-action: pan-y; -webkit-user-select: none; user-select: none; transition: transform 320ms var(--ease-depth, ease); }
  .swipe-row.is-dragging .swipe-face { transition: none; }
  .swipe-acts { position: absolute; inset: 0 0 0 auto; display: flex; visibility: hidden; transition: visibility 0s linear 320ms; }
  .swipe-row:is(.is-dragging, .is-swiped) .swipe-acts { visibility: visible; transition: none; }
  .swipe-act { width: 64px; padding: 0 4px; border: 0; background: var(--up); color: var(--bg); font: 700 13px var(--sans); }
  body.hm .legend-name.is-ma { cursor: pointer; }
"""

COMMON_JS = r"""
  const MA_NAMES = { '五日線': 5, '十日線': 10, '月線': 20, '季線': 60, '半年線': 120, '年線': 240 };
  const PALETTE = ['--accent', '--gold', '--study-dif', '--dim', '--ma5', '--ma6', '--ma7', '--ma8'];
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  let periods = [18, 45];
  const nextPeriod = () => [5, 10, 20, 60, 120, 240].find(p => !periods.includes(p)) || Math.max(...periods) * 2;
  function chart() { return window.__homeK && window.__homeK.candles.length ? window.__homeK : null; }
  function apply(list) {
    const k = chart(); if (!k) return;
    periods = [...new Set(list.map(v => Math.round(Number(v))).filter(v => v >= 2 && v < k.candles.length))];
    k.maColors = () => periods.map((_, i) => css(PALETTE[i % PALETTE.length]));
    k.maPeriods = periods; k.compute(k.candles); k.draw();
    if (window.__maRender) window.__maRender();
  }
  // 預測線是均線時名稱改成 MA 寫法
  function patchNames() {
    const k = chart(); if (!k || k.__named) return;
    k.__named = true;
    const original = k.legendItems.bind(k);
    k.legendItems = () => original().map(item => item.mark && MA_NAMES[item.name] ? { ...item, name: `MA${MA_NAMES[item.name]}` } : item);
    k.draw();
  }
  // 圖上方那一行的均線名稱（不是判斷線、不是布林）標成可以點
  function markNames() {
    document.querySelectorAll('#legendNames .legend-name').forEach(row => {
      const name = (row.querySelector('b') || row).textContent;
      row.classList.toggle('is-ma', /^MA\d+$/.test(name) && !row.classList.contains('is-line'));
    });
  }
"""

POP_JS = r"""
  // 浮出那一塊：每條一列（顏色、名稱、天數可改、向左滑刪除），最下面「＋ 加一條均線」
  const stage = document.getElementById('chartStage');
  const pop = document.createElement('div');
  pop.className = 'ma-pop'; pop.hidden = true; pop.setAttribute('role', 'dialog'); pop.setAttribute('aria-label', '設定均線');
  pop.innerHTML = '<div class="ma-pop-head"><b>均線</b><span>向左滑刪除</span><button class="ma-pop-close" type="button" aria-label="關閉">×</button></div><ul class="ma-list"></ul><button class="ma-add" type="button">＋ 加一條均線</button>';
  stage.append(pop);
  const list = pop.querySelector('.ma-list');
  window.__maRender = () => {
    list.replaceChildren(...periods.map((period, index) => {
      const row = document.createElement('li');
      row.className = 'swipe-row'; row.dataset.index = String(index);
      row.style.setProperty('--tone', css(PALETTE[index % PALETTE.length]));
      row.innerHTML = `<div class="swipe-clip"><div class="swipe-acts" inert><button class="swipe-act" type="button">刪除</button></div>
        <div class="swipe-face"><i class="swatch"></i><span class="ma-name">MA${period}</span><input type="number" inputmode="numeric" min="2" value="${period}" aria-label="MA${period} 的天數"><span class="unit">日</span></div></div>`;
      row.querySelector('input').addEventListener('change', event => apply(periods.map((v, i) => i === index ? event.target.value : v)));
      return row;
    }));
  };
  if (window.SwipeRows) window.SwipeRows.bind(list, { onAct(_button, row) { apply(periods.filter((_, i) => i !== Number(row.dataset.index))); } });
  pop.querySelector('.ma-add').addEventListener('click', () => apply([...periods, nextPeriod()]));
  pop.querySelector('.ma-pop-close').addEventListener('click', () => { pop.hidden = true; });
  window.__maOpen = () => { window.__maRender(); pop.hidden = false; };
  document.getElementById('legendNames').addEventListener('click', event => { if (event.target.closest('.legend-name.is-ma')) window.__maOpen(); });
"""

A_SCRIPT = """<script src="/swipe-rows.js"></script>
<script>
(function () {
  'use strict';
""" + COMMON_JS + POP_JS + """
  const ready = setInterval(() => { if (!chart()) return; clearInterval(ready); patchNames(); apply(periods); }, 100);
  new MutationObserver(markNames).observe(document.getElementById('legendNames'), { childList: true, subtree: true });
})();
</script>"""

B_STYLE = """
  .ma-plus { flex: none; width: 26px; height: 26px; margin: -4px 0; border: 1px solid var(--line-strong); border-radius: 50%; background: var(--panel); color: var(--ink); font: 500 18px/1 var(--sans); }
"""
B_SCRIPT = """<script src="/swipe-rows.js"></script>
<script>
(function () {
  'use strict';
""" + COMMON_JS + POP_JS + """
  // 上方那一行最後一顆「＋」
  const plus = document.createElement('button');
  plus.type = 'button'; plus.className = 'ma-plus'; plus.textContent = '＋'; plus.setAttribute('aria-label', '設定均線');
  plus.addEventListener('click', () => window.__maOpen());
  // 接在名稱最後面（同一行）；名稱每次重寫都會清掉它，再接回去
  const names = document.getElementById('legendNames');
  const keep = () => { if (names.lastElementChild !== plus) names.append(plus); };
  new MutationObserver(keep).observe(names, { childList: true });
  keep();
  const ready = setInterval(() => { if (!chart()) return; clearInterval(ready); patchNames(); apply(periods); }, 100);
  new MutationObserver(markNames).observe(document.getElementById('legendNames'), { childList: true, subtree: true });
})();
</script>"""

C_STYLE = """
  .ma-row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin: 8px 0 4px; }
  .ma-row > b { font: 600 13px var(--sans); color: var(--dim); margin-right: 2px; }
  .ma-chip { display: inline-flex; align-items: center; gap: 4px; height: 36px; padding: 0 2px 0 8px; border: 1px solid var(--line); border-radius: 9px; background: var(--bg); }
  .ma-chip .swatch { width: 12px; height: 3px; border-radius: 2px; background: var(--tone); }
  .ma-chip input { width: 52px; height: 28px; padding: 0 4px; border: 0; background: transparent; color: var(--ink); font: 500 14px var(--mono); }
  .ma-chip button { width: 28px; height: 28px; border: 0; background: transparent; color: var(--faint); font-size: 16px; }
  .ma-row .ma-plus { height: 36px; padding: 0 12px; border: 1px dashed var(--line-strong); border-radius: 9px; background: transparent; color: var(--ink); font: 500 13px var(--sans); }
"""
C_SCRIPT = """<script>
(function () {
  'use strict';
""" + COMMON_JS + """
  // 圖下方一列：每條一格天數，後面一顆 ×，最後「＋ 均線」（個股頁現在的做法）
  const row = document.createElement('div');
  row.className = 'ma-row'; row.setAttribute('role', 'group'); row.setAttribute('aria-label', '均線');
  document.getElementById('chartStudies').insertAdjacentElement('beforebegin', row);
  window.__maRender = () => {
    const chips = periods.map((period, index) => {
      const chip = document.createElement('label');
      chip.className = 'ma-chip'; chip.style.setProperty('--tone', css(PALETTE[index % PALETTE.length]));
      chip.innerHTML = `<i class="swatch"></i><span class="num" style="font-size:12px;color:var(--dim)">MA</span><input type="number" inputmode="numeric" min="2" value="${period}" aria-label="MA${period} 的天數"><button type="button" aria-label="移除 MA${period}">×</button>`;
      chip.querySelector('input').addEventListener('change', event => apply(periods.map((v, i) => i === index ? event.target.value : v)));
      chip.querySelector('button').addEventListener('click', () => apply(periods.filter((_, i) => i !== index)));
      return chip;
    });
    const plus = document.createElement('button');
    plus.type = 'button'; plus.className = 'ma-plus'; plus.textContent = '＋ 均線';
    plus.addEventListener('click', () => apply([...periods, nextPeriod()]));
    const label = document.createElement('b'); label.textContent = '均線';
    row.replaceChildren(label, ...chips, plus);
  };
  const ready = setInterval(() => { if (!chart()) return; clearInterval(ready); patchNames(); apply(periods); }, 100);
})();
</script>"""

VARIANTS = {
    "a": ("點上方那一行的均線名稱打開設定", COMMON_STYLE, A_SCRIPT),
    "b": ("上方那一行最後一顆＋打開設定", COMMON_STYLE + B_STYLE, B_SCRIPT),
    "c": ("跟個股頁一樣放在圖下方", COMMON_STYLE + C_STYLE, C_SCRIPT),
}
for key, (what, style, script) in VARIANTS.items():
    html = PAGE
    assert html.count("<head>") == 1 and html.count("</head>") == 1 and html.count("</body>") == 1
    html = html.replace("<head>", f'<head>\n  <base href="/">\n  <!-- 可丟棄的樣品（由 build-ma-settings.py 產生，不要手改）：{what}。 -->\n{HEAD_SCRIPT}', 1)
    html = html.replace("</head>", f"  <style>{style}</style>\n</head>", 1)
    html = html.replace("</body>", f"{script}\n</body>", 1)
    (HERE / f"ma-settings-{key}.html").write_text(html, encoding="utf-8", newline="\n")
print("ma-settings ok")
