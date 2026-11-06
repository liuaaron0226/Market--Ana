"""公司行動資料：證交所三張官方表，依月份抓原件、整理成事件表，給回測調整用。

- TWT49U 除權除息計算結果表：除權息日、除權息前收盤、參考價、權值＋息值、權／息／權息。
- TWTB8U 變更股票面額恢復買賣參考價：面額變更（例 4763 2025-06-30 1 拆 10）。
- TWTAUU 股票減資恢復買賣參考價格：停止買賣前收盤、恢復買賣參考價、減資原因。
  （TWTAVU「減資預告表」不隨日期回傳歷史，只有當下預告，11-07 實核後不用。）
抓取規則同 stock_history：單路、間隔≥3 秒、遇 403／429／非 JSON 即停、原件存在就不重抓、不覆寫。

已知限制（11-07 實核）：ETF 分割（0050 2025-06-18 1 拆 4）不在這三張表內，個股價量的「X 不比價」
也有大量是「前一日無收盤價」而非公司行動；兩者都不能只靠 X 判斷，回測改用本表＋參考價差偵測。
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from stock_history import Response, http_get

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / 'data/corporate-actions'
MIN_INTERVAL_SECONDS = 3.0
SOURCES = {
    'TWT49U': 'https://www.twse.com.tw/rwd/zh/exRight/TWT49U',
    'TWTB8U': 'https://www.twse.com.tw/rwd/zh/change/TWTB8U',
    'TWTAUU': 'https://www.twse.com.tw/rwd/zh/reducation/TWTAUU',
}


def months(start: str, end: str) -> list[tuple[str, str]]:
    """[(YYYYMM01, YYYYMM末日)]，含頭尾月份。"""
    out = []
    year, month = int(start[:4]), int(start[4:6])
    while f'{year}{month:02d}' <= end[:6]:
        nxt = date(year + (month == 12), month % 12 + 1, 1)
        last = date.fromordinal(nxt.toordinal() - 1)
        out.append((f'{year}{month:02d}01', last.strftime('%Y%m%d')))
        year, month = nxt.year, nxt.month
    return out


def raw_file(out: Path, source: str, first_day: str) -> Path:
    return out / 'raw' / source / f'{source}-{first_day[:6]}.json.gz'


def fetch_all(out: Path, start: str, end: str, get: Callable = http_get, sleep: Callable = time.sleep) -> dict:
    summary = {'requested': 0, 'fetched': 0, 'existing': 0, 'empty_months': 0, 'stopped': None}
    first = True
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'fetch-log.jsonl').open('a', encoding='utf-8') as log:
        for source, url in SOURCES.items():
            for first_day, last_day in months(start, end):
                target = raw_file(out, source, first_day)
                if target.exists():
                    summary['existing'] += 1
                    continue
                if not first:
                    sleep(MIN_INTERVAL_SECONDS)
                first = False
                summary['requested'] += 1
                entry = {'source': source, 'month': first_day[:6], 'fetched_at_utc': datetime.now(timezone.utc).isoformat()}
                try:
                    response: Response = get(url, {'startDate': first_day, 'endDate': last_day, 'response': 'json'})
                except Exception as error:
                    entry.update(outcome='stop', reason=f'連線錯誤：{type(error).__name__}: {error}')
                    log.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    summary['stopped'] = entry
                    return summary
                entry.update(http_status=response.status, source_url=response.url)
                reason = None
                if response.status != 200:
                    reason = f'HTTP {response.status}'
                else:
                    try:
                        payload = json.loads(response.content.decode('utf-8'))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        reason = f'非 JSON 回應（{response.content_type}），可能是驗證頁'
                if reason:
                    entry.update(outcome='stop', reason=reason)
                    log.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    summary['stopped'] = entry
                    return summary
                # 該月沒有事件時官方回 stat 非 OK；仍存原件，表示「查過、沒有」
                summary['empty_months'] += payload.get('stat') != 'OK' or not payload.get('data')
                target.parent.mkdir(parents=True, exist_ok=True)
                partial = target.with_name(target.name + '.partial')
                partial.write_bytes(gzip.compress(response.content, 9))
                os.replace(partial, target)
                entry.update(outcome='saved', rows=len(payload.get('data') or []))
                log.write(json.dumps(entry, ensure_ascii=False) + '\n')
                log.flush()
                summary['fetched'] += 1
    return summary


def roc_date(text: str) -> str:
    """'105年06月02日' 或 '114/06/30' → '2016-06-02'。"""
    digits = text.replace('年', '/').replace('月', '/').replace('日', '').split('/')
    return f'{int(digits[0]) + 1911}-{int(digits[1]):02d}-{int(digits[2]):02d}'


def number(text: str) -> float | None:
    text = str(text).replace(',', '').strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_events(out: Path) -> list[dict]:
    """每筆事件：date（恢復／除權息交易日）、code、type、close_before、reference、cash_dividend、ratio（新股數／舊股數）。"""
    events = []
    for source in SOURCES:
        for path in sorted((out / 'raw' / source).glob('*.json.gz')):
            payload = json.loads(gzip.decompress(path.read_bytes()).decode('utf-8'))
            fields = payload.get('fields') or []
            for row in payload.get('data') or []:
                cell = dict(zip(fields, row))
                if source == 'TWT49U':
                    before, reference = number(cell['除權息前收盤價']), number(cell['除權息參考價'])
                    kind = cell['權/息']
                    events.append({'date': roc_date(cell['資料日期']), 'code': cell['股票代號'], 'type': f'除{kind}',
                                   'close_before': before, 'reference': reference,
                                   'value': number(cell['權值+息值']), 'limit_up': number(cell['漲停價格']),
                                   'limit_down': number(cell['跌停價格']), 'source': path.name})
                elif source == 'TWTB8U':
                    before, reference = number(cell['停止買賣前收盤價格']), number(cell['恢復買賣參考價'])
                    events.append({'date': roc_date(cell['恢復買賣日期']), 'code': cell['股票代號'], 'type': '變更面額',
                                   'close_before': before, 'reference': reference, 'value': None,
                                   'limit_up': number(cell['漲停價格']), 'limit_down': number(cell['跌停價格']), 'source': path.name})
                else:
                    events.append({'date': roc_date(cell['恢復買賣日期']), 'code': cell['股票代號'].strip(), 'type': '減資',
                                   'close_before': number(cell['停止買賣前收盤價格']), 'reference': number(cell['恢復買賣參考價']),
                                   'reason': cell.get('減資原因'), 'value': None,
                                   'limit_up': number(cell['漲停價格']), 'limit_down': number(cell['跌停價格']), 'source': path.name})
    events.sort(key=lambda e: (e['date'], e['code'], e['type']))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    sub = parser.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch')
    fetch.add_argument('--start', default='20160101')
    fetch.add_argument('--end', default='20261130')
    sub.add_parser('build')
    args = parser.parse_args()
    if args.command == 'fetch':
        started = time.time()
        summary = fetch_all(args.out, args.start, args.end)
        summary['seconds'] = round(time.time() - started)
        print(json.dumps(summary, ensure_ascii=False))
        return 2 if summary['stopped'] else 0
    events = parse_events(args.out)
    (args.out / 'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    counts = {}
    for event in events:
        counts[event['type']] = counts.get(event['type'], 0) + 1
    print(json.dumps({'events': len(events), 'by_type': counts}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
