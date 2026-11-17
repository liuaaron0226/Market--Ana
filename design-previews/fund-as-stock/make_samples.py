"""基金當成一般個股（本人 11-17）：個股頁對基金放哪些區塊的兩個樣品。可丟棄的原型，不進正式網站。

用真的回補淨值（seeds/funds/BLKTENAU/nav-backfill.csv）畫近一年淨值線，套網站自己的 CSS（從 cloud/dist 讀），
輸出 sample-a.html（精簡）與 sample-b.html（比照個股），再複製到 cloud/dist/samples-fund/ 讓本機預覽伺服器看得到。
用法：python design-previews/fund-as-stock/make_samples.py
"""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = ROOT / "cloud/dist/samples-fund"

rows = [(d, float(v)) for d, v in csv.reader((ROOT / "seeds/funds/BLKTENAU/nav-backfill.csv").open(encoding="utf-8"))]
year = rows[-250:]
navs = [v for _, v in year]
last, prev = rows[-1], rows[-2]
chg = last[1] - prev[1]
pct = chg / prev[1] * 100


def ma(values: list[float], n: int) -> list[float | None]:
    return [None if i + 1 < n else sum(values[i + 1 - n:i + 1]) / n for i in range(len(values))]


W, H, PAD_R, PAD_T, PAD_B = 330, 210, 44, 10, 22
lo, hi = min(navs) * 0.98, max(navs) * 1.02


def xy(i: int, v: float) -> str:
    x = i / (len(navs) - 1) * (W - PAD_R)
    y = PAD_T + (hi - v) / (hi - lo) * (H - PAD_T - PAD_B)
    return f"{x:.1f},{y:.1f}"


def path(values: list[float | None]) -> str:
    return " ".join(xy(i, v) for i, v in enumerate(values) if v is not None)


def chart(with_ma: bool) -> str:
    grid = []
    for k in range(4):
        v = lo + (hi - lo) * (k + 0.5) / 4
        y = PAD_T + (hi - v) / (hi - lo) * (H - PAD_T - PAD_B)
        grid.append(f'<line x1="0" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--hair)"/>'
                    f'<text x="{W - PAD_R + 6}" y="{y + 4:.1f}" font-size="10" fill="var(--faint)" font-family="var(--mono)">{v:.1f}</text>')
    months = []
    for i, (d, _) in enumerate(year):
        if i and d[5:7] != year[i - 1][0][5:7] and d[5:7] in ("01", "04", "07", "10"):
            x = i / (len(navs) - 1) * (W - PAD_R)
            months.append(f'<text x="{x:.1f}" y="{H - 6}" font-size="10" fill="var(--faint)" font-family="var(--mono)">{d[2:4]}/{d[5:7]}</text>')
    lines = [f'<polyline points="{path(navs)}" fill="none" stroke="var(--ink)" stroke-width="1.6"/>']
    if with_ma:
        lines.insert(0, f'<polyline points="{path(ma(navs, 20))}" fill="none" stroke="#c4501a" stroke-width="1.3"/>')
        lines.insert(0, f'<polyline points="{path(ma(navs, 60))}" fill="none" stroke="#8a6a12" stroke-width="1.3"/>')
    x_last, y_last = xy(len(navs) - 1, navs[-1]).split(",")
    tag = (f'<rect x="{W - PAD_R + 2}" y="{float(y_last) - 9:.1f}" width="{PAD_R - 2}" height="18" rx="3" fill="var(--ink)"/>'
           f'<text x="{W - PAD_R + 6}" y="{float(y_last) + 4:.1f}" font-size="10" fill="var(--bg)" font-family="var(--mono)">{navs[-1]:.2f}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="近一年淨值走勢">{"".join(grid)}{"".join(months)}'
            f'{"".join(lines)}<circle cx="{x_last}" cy="{y_last}" r="3" fill="var(--ink)"/>{tag}</svg>')


def page(variant: str) -> str:
    full = variant == "b"
    down = "down" if chg < 0 else "up"
    strategy = """
      <section class="bd-strategy" aria-labelledby="sc">
        <h2 id="sc">續抱｜淨值在季線下方，等站回季線再加碼（樣品示意文字）</h2>
        <details><summary>操作價位</summary></details>
      </section>""" if full else ""
    ma_chip = '<div class="chart-ma"><button type="button" class="chart-chip">均線 20 · 60</button></div>' if full else ""
    ma_legend = '<p class="sp-legend"><span style="color:#c4501a">MA20</span> <span style="color:#8a6a12">MA60</span></p>' if full else ""
    draw = """<div class="bd-draw-row"><div class="bd-draw" role="toolbar">
        <button type="button" aria-label="趨勢線"><svg viewBox="0 0 24 24"><path d="M7.4 16.2 16.6 7.8"/><circle cx="5.5" cy="18" r="2.5"/><circle cx="18.5" cy="6" r="2.5"/></svg></button>
        <button type="button" aria-label="水平線"><svg viewBox="0 0 24 24"><path d="M3 12h6.5m5 0H21"/><circle cx="12" cy="12" r="2.5"/></svg></button>
        <button type="button" aria-label="射線"><svg viewBox="0 0 24 24"><path d="M7.4 16.2 21 3.8"/><circle cx="5.5" cy="18" r="2.5"/></svg></button>
        <button type="button" aria-label="文字標註"><svg viewBox="0 0 24 24"><path d="M5.5 8V5.5h13V8M12 5.5v13M9.5 18.5h5"/></svg></button>
      </div></div>""" if full else ""
    cats = ["價位", "對照 0050", "配息"] if full else ["對照 0050"]
    cat_buttons = "".join(f'<button type="button" aria-expanded="{"true" if c == "對照 0050" else "false"}">{c}</button>' for c in cats)
    dist = "" if full else '<p class="sp-note">這檔配息可能來自本金：2023 年 12 次配息中 11 次全部來自本金；2024 年起來自可分配淨利益。最近除息 10/30，每單位 0.162 美元。</p>'
    return f"""<!doctype html>
<html lang="zh-Hant" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>樣品 {variant.upper()}｜基金當成個股</title>
<link rel="stylesheet" href="../minimal.css"><link rel="stylesheet" href="../chart-studies.css"><link rel="stylesheet" href="../bold.css"><link rel="stylesheet" href="../stock.css">
<style>
  .sp-tag {{ position: fixed; z-index: 99; left: 8px; bottom: 8px; padding: 4px 10px; border-radius: 99px; background: var(--ink); color: var(--bg); font: 600 12px var(--sans); }}
  .sp-chart {{ padding: 4px 0 0; }}
  .sp-legend {{ margin: 6px 0 0; font: 12px var(--mono); }}
  .sp-note {{ margin: 12px 0 0; color: var(--dim); font-size: 13px; line-height: 1.6; }}
  .sp-compare p {{ margin: 0; }}
</style></head>
<body class="bd st"><div class="bd-shell">
  <header class="bd-bar">
    <a class="bd-back" href="#"><span aria-hidden="true">‹</span> 大盤</a>
    <form class="bd-search" role="search"><input placeholder="代號或名稱，例如 2330、台積電"><button type="button">查詢</button></form>
    <button class="bd-theme" type="button" aria-label="設定"><svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.6"/></svg></button>
  </header>
  <main>
    <div class="st-top">
      <section class="bd-quote" aria-label="報價">
        <div class="bd-ident"><h1>貝萊德世界科技 A10</h1>
          <p><span class="num">BLKTENAU</span><span class="bd-dot">·</span>基金 · 美元<span class="bd-dot">·</span>淨值日 <span class="num">{last[0]}</span></p></div>
        <div class="bd-price">
          <div class="bd-price-line"><b class="num" id="close">{last[1]:.2f}</b></div>
          <b class="num {down}" id="chg">{chg:+.2f}（{pct:+.2f}%）</b>
          <p class="bd-live-status">淨值每天一個，約晚 3 個營業日公布</p>
        </div>
      </section>{strategy}
    </div>
    <div class="bd-stage">
      <section class="bd-chart sk-chart-card">
        <div class="bd-chart-bar"><div><p>淨值 · 日</p></div>
          <label class="bd-period"><select><option>日</option><option>週</option><option>月</option></select></label>{ma_chip}</div>
        {draw}
        {ma_legend}
        <div class="sp-chart">{chart(full)}</div>
      </section>
    </div>
    <div class="hm-cats" role="group">{cat_buttons}</div>
    <section class="bd-cat sp-compare" data-open><section class="bd-compare">
      <p class="bd-compare-conclusion">近 1 年落後 0050 41.0 個百分點（台幣、含息再投入）。</p>
      <p class="sp-note">（點開後是現在基金對照頁那一整塊：兩條報酬線、五個期間、匯率影響）</p>
      {dist}
    </section></section>
  </main>
</div><span class="sp-tag">樣品 {variant.upper()}</span></body></html>
"""


OUT.mkdir(parents=True, exist_ok=True)
for v in ("a", "b"):
    text = page(v)
    (HERE / f"sample-{v}.html").write_text(text, encoding="utf-8")
    shutil.copy(HERE / f"sample-{v}.html", OUT / f"sample-{v}.html")
print("ok", last, f"{chg:+.2f}", f"{pct:+.2f}%")
