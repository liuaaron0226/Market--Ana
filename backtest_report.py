"""把 backtest.py 的結果 JSON 做成摘要數字（含分年、三段）與 B 版淺色卡片風格的報告頁。

只讀結果檔，不重跑回測。「績效不可判定」之後的數字照列但明確標示，不和可比段混在一起講。
"""
from __future__ import annotations

import argparse
import html
import json
import math
from datetime import date, timedelta
import sys
from pathlib import Path

SEGMENTS = [('開發段', '2016-01-01', '2020-12-31'), ('驗證段', '2021-01-01', '2023-12-31'), ('保留測試段', '2024-01-01', '2099-12-31')]


def max_drawdown(values):
    peak, worst = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def period_stats(points, start, end, key):
    inside = [p for p in points if start <= p['date'] <= end]
    if len(inside) < 2:
        return None
    first, last = inside[0][key], inside[-1][key]
    days = len(inside)
    return {'start': inside[0]['date'], 'end': inside[-1]['date'], 'return': last / first - 1,
            'annualized': (last / first) ** (252 / max(days - 1, 1)) - 1, 'max_drawdown': max_drawdown([p[key] for p in inside])}


def split_date(result: dict) -> str | None:
    """0050 第一個未解決的公司行動日；之後買入持有市值不可比，截斷不畫。已處理（resolution 有值）的不截。"""
    return next((e['date'] for e in result['benchmarks'].get('0050_events', []) if not e.get('resolution')
                 and not e['type'].startswith('不比價')), None) or result['benchmarks'].get('0050_not_comparable_from')


def lines_of(result: dict) -> dict:
    cut = split_date(result)
    hold = [{'date': p['date'], 'v': p['value']} for p in result['benchmarks']['0050_buy_and_hold'] if cut is None or p['date'] < cut]
    lines = {
        '策略 v0.1': [{'date': p['date'], 'v': p['nav']} for p in result['equity']],
        '0050 買入持有': hold,
    }
    if result['benchmarks'].get('return_index'):
        lines['加權報酬指數（含息）'] = [{'date': p['date'], 'v': p['value']} for p in result['benchmarks']['return_index']]
    else:
        lines['加權指數（價格）'] = [{'date': p['date'], 'v': p['value']} for p in result['benchmarks']['index']]
    return lines


def summarize(result: dict) -> dict:
    lines = lines_of(result)
    equity = lines['策略 v0.1']
    years = sorted({p['date'][:4] for p in equity})
    periods = [(y, f'{y}-01-01', f'{y}-12-31') for y in years] + SEGMENTS + [('全期', '0000', '9999')]
    if result['verdict_from']:
        # 第一個不可判定日之前（不含當天）才是可比區間
        periods.append(('可比區間', '0000', (date.fromisoformat(result['verdict_from']) - timedelta(days=1)).isoformat()))
    table = []
    for label, start, end in periods:
        row = {'period': label}
        for name, points in lines.items():
            stats = period_stats(points, start, end, 'v')
            row[name] = stats
        table.append(row)
    closed = result['closed_trades']
    trades_by_year = {}
    for trade in closed:
        year = trade['close_date'][:4]
        bucket = trades_by_year.setdefault(year, {'trades': 0, 'wins': 0})
        bucket['trades'] += 1
        bucket['wins'] += trade['proceeds'] > trade['cost']
    holding_days = [(_days(t['open_date'], t['close_date'])) for t in closed]
    events = result['corporate_action_events']
    return {
        'summary': result['summary'], 'table': table, 'trades_by_year': trades_by_year,
        'avg_holding_calendar_days': sum(holding_days) / len(holding_days) if holding_days else None,
        'verdict_from': result['verdict_from'], 'events': events,
        'event_count': len(events), 'event_codes': len({e['code'] for e in events}),
        '0050_not_comparable_from': result['benchmarks']['0050_not_comparable_from'],
        '0050_cut_from': split_date(result),
        'exposure_avg': _avg_exposure(result['equity']),
    }


def _days(a, b):
    from datetime import date
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def _avg_exposure(equity):
    values = [1 - p['cash'] / p['nav'] for p in equity if p['nav']]
    return sum(values) / len(values) if values else None


def pct(value, digits=1):
    return '—' if value is None or (isinstance(value, float) and math.isnan(value)) else f'{value * 100:+.{digits}f}%'


def sparkline(points, width=900, height=220, colors=None):
    """三條線同一張 SVG，各自以起點=100 正規化；回傳 svg 字串。"""
    series = {name: pts for name, pts in points.items() if pts}
    all_dates = sorted({p['date'] for pts in series.values() for p in pts})
    position = {d: i for i, d in enumerate(all_dates)}
    normalized = {name: [(position[p['date']], p['v'] / pts[0]['v'] * 100) for p in pts] for name, pts in series.items()}
    lo = min(v for pts in normalized.values() for _, v in pts)
    hi = max(v for pts in normalized.values() for _, v in pts)
    span = max(hi - lo, 1)
    x = lambda i: 8 + i / max(len(all_dates) - 1, 1) * (width - 16)
    y = lambda v: height - 20 - (v - lo) / span * (height - 40)
    colors = colors or {'策略 v0.1': 'var(--accent)', '0050 買入持有': 'var(--down)', '加權指數（價格）': 'var(--faint)', '加權報酬指數（含息）': 'var(--faint)'}
    paths = []
    for name, pts in normalized.items():
        d = ' '.join(f'{"M" if k == 0 else "L"}{x(i):.1f},{y(v):.1f}' for k, (i, v) in enumerate(pts[::3] + [pts[-1]]))
        paths.append(f'<path d="{d}" fill="none" stroke="{colors[name]}" stroke-width="{2.4 if name.startswith("策略") else 1.6}"/>')
    base = y(100)
    ticks = []
    for year in sorted({d[:4] for d in all_dates}):
        first = next(i for i, d in enumerate(all_dates) if d.startswith(year))
        ticks.append(f'<text x="{x(first):.1f}" y="{height - 4}" class="tick">{year[2:]}</text>')
    return (f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{len(normalized)} 條績效線，起點皆為 100">'
            f'<line x1="8" x2="{width - 8}" y1="{base:.1f}" y2="{base:.1f}" class="base"/>{"".join(paths)}{"".join(ticks)}</svg>')


def render(result: dict, report: dict, source_name: str) -> str:
    s = report['summary']
    lines = lines_of(result)
    cut = report['0050_cut_from']
    hold_end = lines['0050 買入持有'][-1]['date']
    full = next(r for r in report['table'] if r['period'] == '全期')
    rows = []
    for row in report['table']:
        cells = ''.join(f'<td>{pct(row[n]["return"]) if row[n] else "—"}</td><td class="dd">{pct(row[n]["max_drawdown"]) if row[n] else "—"}</td>' for n in lines)
        tr = report['trades_by_year'].get(row['period'])
        trade_cell = f'{tr["trades"]}／{tr["wins"] / tr["trades"]:.0%}' if tr else '—'
        cls = ' class="seg"' if not row['period'].isdigit() else ''
        rows.append(f'<tr{cls}><th>{html.escape(row["period"])}</th>{cells}<td>{trade_cell}</td></tr>')
    verdict = report['verdict_from']
    adjusted = result.get('corporate_actions_handled', False)
    bench_name = next(n for n in lines if n.startswith('加權'))
    strategy, hold, index = full['策略 v0.1'], full['0050 買入持有'], full[bench_name]
    resolved = [e for e in report['events'] if e.get('resolution')]
    unresolved = [e for e in report['events'] if not e.get('resolution')]
    valid_end = verdict or s['end']
    window = next((r for r in report['table'] if r['period'] == '可比區間'), None)
    if adjusted and not verdict:
        status = f'股利入帳、除權／分割／減資調整股數後，整段 2016-04～{s["end"]} 都可比較。'
    elif adjusted:
        status = f'已處理 {len(resolved)} 次公司行動；{verdict} 起有官方表查不到的事件，之後的數字不可判定，只比到 {verdict} 之前。'
    else:
        status = f'未處理公司行動，{verdict} 起不可判定。'
    bullets_mean = [
        f'{status}',
        f'整段：策略 {pct(strategy["return"])}（最大回檔 {pct(strategy["max_drawdown"])}）；{bench_name} {pct(index["return"])}（{pct(index["max_drawdown"])}）；0050 買入持有到 {hold_end} {pct(hold["return"])}。' if not verdict else
        f'只看可比區間（到 {verdict} 之前）：見下表「可比區間」列；整段數字照列但不可判定。',
        f'行為統計：{s["closed_trades"]} 筆出入場、勝率 {s["win_rate"]:.0%}、平均持有約 {report["avg_holding_calendar_days"]:.0f} 天、平均持股比例約 {report["exposure_avg"]:.0%}；手續費 {s["fees"]:,} 元、證交稅 {s["taxes"]:,} 元。',
    ]
    bullets_warn = [
        f'股利依官方除權息表入帳，不扣二代健保補充保費與所得稅；配股、分割、面額變更、減資依官方參考價換算股數，零股保留但不賣。' if adjusted else '',
        f'公司行動事件 {report["event_count"]} 次（{report["event_codes"]} 檔），其中 {len(unresolved)} 次無法由官方表解決。' if report['event_count'] else '',
        f'其中 {sum(1 for e in resolved if "未拆分" in e["resolution"])} 次「除權息」同日發生，官方彙總表拆不出現金與配股，改以參考價換算股數（市值在除權息當刻不變）；遇到有償現金增資時等於假設照比例認購且不付款，屬近似。' if any('未拆分' in e['resolution'] for e in resolved) else '',
        f'0050 在 {cut} 起不可比，線只畫到 {hold_end}。' if cut else '',
        f'{bench_name}是證交所官方指數；含息報酬指數把現金股利再投入，跟實際持有大盤最接近。' if bench_name.endswith('（含息）') else '加權指數是價格指數，不含股息。',
        '回測用 v0.1 固定參數，沒有用 Gemini；所有門檻都是助手訂的待驗參數，沒有為了好看去調整。',
        f'未成交 {s["unfilled_orders"]} 筆、開盤跳空取消 {s["cancelled_buys"]} 筆；漲跌停一律用前收 ±10% 估算（除權息日也是，官方表的漲跌停尚未接入撮合），不保證真的排得到。',
    ]
    li = lambda items: ''.join(f'<li>{b}</li>' for b in items if b)
    legend = ''.join(f'<span class="key" style="--c:{c}">{n}</span>' for n, c in zip(lines, ('var(--accent)', 'var(--down)', 'var(--faint)')))
    head = ''.join(f'<th colspan="2">{n}</th>' for n in lines)
    sub = '<th></th>' + '<th>報酬</th><th>最大回檔</th>' * 3 + '<th>出場筆數／勝率</th>'
    return f'''<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>策略 v0.1 回測</title>
<style>
:root{{--bg:#f3f1ec;--panel:#fffdf9;--line:#ddd8cd;--ink:#1d1b20;--dim:#5b5762;--faint:#8d8995;--accent:#c24a16;--down:#0f8058;--up:#c8262c;
--sans:"Noto Sans TC","Microsoft JhengHei",system-ui,sans-serif;--mono:"JetBrains Mono",Consolas,monospace}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0b0b0e;--panel:#15151b;--line:#2a2a33;--ink:#f2f0ec;--dim:#b0adb6;--faint:#8d8995;--accent:#e8622c;--down:#2fd09a;--up:#ff6868}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.7 var(--sans)}}
main{{max-width:1080px;margin:0 auto;padding:40px 16px 80px}}
h1{{font-size:28px;margin:0 0 4px}}.meta{{color:var(--dim);font-size:14px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:20px;padding:24px;margin-top:24px}}
.big{{display:flex;flex-wrap:wrap;gap:28px}}.big div{{min-width:140px}}.big b{{display:block;font:600 28px var(--mono)}}.big span{{color:var(--dim);font-size:13px}}
.warn{{border-color:var(--accent)}}h2{{font-size:18px;margin:0 0 12px}}ul{{margin:0;padding-left:20px}}li{{margin:6px 0}}
svg{{width:100%;height:auto}}svg path{{vector-effect:non-scaling-stroke}}.base{{stroke:var(--line);stroke-dasharray:4 4}}.tick{{fill:var(--faint);font:11px var(--mono)}}
.key{{display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:13px;color:var(--dim)}}.key::before{{content:"";width:16px;height:3px;background:var(--c)}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font:13px var(--mono);white-space:nowrap}}
th,td{{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right}}th:first-child{{text-align:left}}.dd{{color:var(--faint)}}tr.seg{{background:color-mix(in srgb,var(--accent) 6%,transparent)}}
details summary{{cursor:pointer;color:var(--dim)}}
</style></head><body><main>
<h1>策略 v0.1 {"回測" if adjusted and not verdict else "未調整模擬" if not adjusted else "回測（部分不可判定）"}：2016-04 至 {html.escape(s["end"])}</h1>
<p class="meta">起始 100 萬元虛擬資金・隔日開盤成交・手續費 0.1425%（捨去、最低 20 元）＋賣出證交稅 0.3%・資料來源證交所每日收盤行情・結果檔 {html.escape(source_name)}</p>
<section class="card big">
<div><b>{pct(s["total_return"])}</b><span>策略總報酬{"" if adjusted and not verdict else "（不可判定）"}</span></div>
<div><b>{pct(hold["return"])}</b><span>0050 買入持有（至 {html.escape(hold_end)}）</span></div>
<div><b>{pct(index["return"])}</b><span>{bench_name}</span></div>
<div><b>{pct(s["max_drawdown"])}</b><span>策略最大回檔{"" if adjusted and not verdict else "（不可判定）"}</span></div>
<div><b>{s["closed_trades"]}</b><span>出入場筆數</span></div>
</section>
<section class="card"><h2>三條線，起點都換算成 100</h2><div>{legend}</div>{sparkline(lines)}</section>
<section class="card"><h2>這代表</h2><ul>{li(bullets_mean)}</ul></section>
<section class="card warn"><h2>要注意</h2><ul>{li(bullets_warn)}</ul></section>
<section class="card"><h2>分年與三段</h2><p class="meta">三段是事前切好的：2016–2020 開發、2021–2023 驗證、2024 起保留測試。底色列是分段。</p>
<div class="scroll"><table><thead><tr><th></th>{head}<th></th></tr><tr>{sub}</tr></thead><tbody>{"".join(rows)}</tbody></table></div></section>
<section class="card"><details><summary>公司行動事件（前 30 筆）</summary><ul>{"".join(f"<li>{html.escape(e['date'])} {html.escape(e['code'])}：{html.escape(e['type'])}</li>" for e in report["events"][:30])}</ul></details></section>
</main></body></html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--html', type=Path, required=True)
    args = parser.parse_args()
    result = json.loads(args.result.read_text(encoding='utf-8'))
    report = summarize(result)
    args.result.with_name(args.result.stem + '-summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    args.html.write_text(render(result, report, args.result.name), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('summary', 'verdict_from', 'event_count', 'event_codes', '0050_not_comparable_from', 'exposure_avg')}, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
