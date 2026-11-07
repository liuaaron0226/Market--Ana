"""多策略比較頁：讀數個 backtest.py 結果檔（都需已處理公司行動），同一張圖、同一張表並列。

只讀結果檔，不重跑回測。比較線取第一個結果檔的加權報酬指數（含息）與 0050 買入持有。
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

from backtest_report import SEGMENTS, pct, period_stats, sparkline

COLORS = ['var(--accent)', 'var(--gold)', 'var(--up)', 'var(--down)', 'var(--faint)']


def series(result):
    return [{'date': p['date'], 'v': p['nav']} for p in result['equity']]


def exposure(result):
    return sum(1 - p['cash'] / p['nav'] for p in result['equity']) / len(result['equity'])


def win_rate(stat) -> str:
    return '—' if stat['win_rate'] is None else f'{stat["win_rate"]:.0%}'


def build(results: list[dict]) -> tuple[dict, list[dict], list[dict]]:
    for result in results:
        if not result.get('corporate_actions_handled') or result.get('verdict_from'):
            raise SystemExit(f'{result.get("strategy_version")} 未完整處理公司行動，不能放進比較頁')
    lines = {f'策略 {r["strategy_version"]}': series(r) for r in results}
    bench = results[0]['benchmarks']
    lines['0050 買入持有'] = [{'date': p['date'], 'v': p['value']} for p in bench['0050_buy_and_hold']]
    lines['加權報酬指數（含息）'] = [{'date': p['date'], 'v': p['value']} for p in bench['return_index']]
    years = sorted({p['date'][:4] for p in lines[next(iter(lines))]})
    table = []
    for label, start, end in [(y, f'{y}-01-01', f'{y}-12-31') for y in years] + SEGMENTS + [('全期', '0000', '9999')]:
        table.append({'period': label, **{name: period_stats(points, start, end, 'v') for name, points in lines.items()}})
    stats = []
    for r in results:
        s = r['summary']
        stats.append({'name': f'策略 {r["strategy_version"]}', 'return': s['total_return'], 'max_drawdown': s['max_drawdown'],
                      'exposure': exposure(r), 'filled': s['filled_orders'], 'costs': s['fees'] + s['taxes'],
                      'unfilled': s['unfilled_orders'], 'closed': s['closed_trades'], 'win_rate': s['win_rate']})
    return lines, table, stats


def render(lines, table, stats, sources) -> str:
    names = list(lines)
    full = next(r for r in table if r['period'] == '全期')
    cards = ''.join(f'<div><b>{pct(full[n]["return"])}</b><span>{html.escape(n)}<br>回檔 {pct(full[n]["max_drawdown"])}</span></div>' for n in names)
    legend = ''.join(f'<span class="key" style="--c:{c}">{html.escape(n)}</span>' for n, c in zip(names, COLORS))
    head = ''.join(f'<th colspan="2">{html.escape(n)}</th>' for n in names)
    sub = '<th></th>' + '<th>報酬</th><th>回檔</th>' * len(names)
    rows = ''.join(
        f'<tr{"" if r["period"].isdigit() else " class=seg"}><th>{html.escape(r["period"])}</th>'
        + ''.join(f'<td>{pct(r[n]["return"]) if r[n] else "—"}</td><td class="dd">{pct(r[n]["max_drawdown"]) if r[n] else "—"}</td>' for n in names)
        + '</tr>' for r in table)
    stat_rows = ''.join(
        f'<tr><th>{html.escape(s["name"])}</th><td>{s["exposure"]:.0%}</td><td>{s["filled"]}</td><td>{s["costs"]:,}</td><td>{s["unfilled"]}</td>'
        f'<td>{s["closed"]}／{win_rate(s)}</td></tr>' for s in stats)
    svg = sparkline(lines, colors=dict(zip(names, COLORS)))
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>策略比較</title>
<style>
:root{{--bg:#f3f1ec;--panel:#fffdf9;--line:#ddd8cd;--ink:#1d1b20;--dim:#5b5762;--faint:#8d8995;--accent:#c24a16;--gold:#94681a;--down:#0f8058;--up:#2f5fb3;
--sans:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;--mono:"JetBrains Mono",Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0b0e;--panel:#15151b;--line:#2a2a33;--ink:#f2f0ec;--dim:#b0adb6;--faint:#8d8995;--accent:#e8622c;--gold:#d9a94b;--down:#2fd09a;--up:#7aa7ff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 var(--sans)}}
main{{max-width:1080px;margin:0 auto;padding:40px 16px 80px}}h1{{font-size:28px;margin:0 0 4px}}.meta{{color:var(--dim);font-size:14px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:24px;margin-top:24px}}
.big{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:20px}}.big b{{display:block;font:600 26px var(--mono)}}.big span{{color:var(--dim);font-size:13px}}
.warn{{border-color:var(--accent)}}h2{{font-size:18px;margin:0 0 12px}}ul{{margin:0;padding-left:20px}}li{{margin:6px 0}}
svg{{width:100%;height:auto}}svg path{{vector-effect:non-scaling-stroke}}.base{{stroke:var(--line);stroke-dasharray:4 4}}.tick{{fill:var(--faint);font:11px var(--mono)}}
.key{{display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:13px;color:var(--dim)}}.key::before{{content:"";width:16px;height:3px;background:var(--c)}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font:13px var(--mono);white-space:nowrap}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right}}th:first-child{{text-align:left}}.dd{{color:var(--faint)}}tr.seg{{background:color-mix(in srgb,var(--accent) 6%,transparent)}}
</style></head><body><main>
<h1>策略比較：v0.1、v0.2a、v0.2b</h1>
<p class="meta">2016-04 至 2026-11-06・起始 100 萬元虛擬資金・隔日開盤成交・手續費 0.1425%（捨去、最低 20 元）＋賣出證交稅（普通股 0.3%、0050 等 ETF 0.1%）・股利入帳、配股／分割／減資依官方表調整・結果檔 {html.escape(", ".join(sources))}</p>
<section class="card big">{cards}</section>
<section class="card"><h2>績效線，起點都換算成 100</h2><div>{legend}</div>{svg}</section>
<section class="card warn"><h2>要注意</h2><ul>
<li><b>這是已看過資料的探索性回測</b>：v0.2 是看完 v0.1 全期結果才設計的，三段（開發／驗證／保留）都被看過，不能說通過樣本外檢驗。真正的檢驗是參數凍結後新增的資料。</li>
<li>v0.2a、v0.2b 大部分資金放在 0050，等於大半是「看大盤狀態決定持有多少 0050」；與 0050 買入持有接近是預期內的。</li>
<li>證交稅依本人 11-07 定案：0050 等 ETF 賣出 0.1%、普通股 0.3%（先前版本一律 0.3%，結果檔另存保留對照）。</li>
<li>股利不扣二代健保補充保費與所得稅；漲跌停一律前收 ±10% 估算；8 次左右除權息同日事件以參考價換算股數。</li>
<li>回測用固定參數，沒有用 Gemini，也沒有用結果挑參數。</li>
</ul></section>
<section class="card"><h2>行為比較</h2><div class="scroll"><table><thead><tr><th></th><th>平均持股</th><th>成交筆數</th><th>費＋稅（元）</th><th>未成交</th><th>個股出場／勝率</th></tr></thead><tbody>{stat_rows}</tbody></table></div></section>
<section class="card"><h2>分年與三段</h2><div class="scroll"><table><thead><tr><th></th>{head}</tr><tr>{sub}</tr></thead><tbody>{rows}</tbody></table></div></section>
</main></body></html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path, nargs='+')
    parser.add_argument('--html', type=Path, required=True)
    args = parser.parse_args()
    results = [json.loads(p.read_text(encoding='utf-8')) for p in args.results]
    lines, table, stats = build(results)
    args.html.write_text(render(lines, table, stats, [f'{p.parent.name}/{p.name}' for p in args.results]), encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
