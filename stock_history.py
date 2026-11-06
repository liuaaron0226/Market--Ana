"""個股歷史資料庫：證交所每日收盤行情（MI_INDEX ALLBUT0999）原件＋可依「日期＋代號」查詢的價量。

抓取：單路、每次請求間隔至少 3 秒；遇 403／429、非 JSON（驗證頁）或連線錯誤即停止；
原件以 gzip 包住官方原始位元組，已存在就跳過、不覆寫，所以重跑即從中斷處續抓。
整理：只收上市個股（普通股 4 碼、特別股 4 碼＋英文字尾；不含存託憑證）與 0050；不還原權值。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, NamedTuple

ROOT = Path(__file__).resolve().parent
DEFAULT_OUT = ROOT / 'data/stock-history'
DEFAULT_INDEX_DIR = ROOT / 'data/market-snapshots/analysis-20261106-close/index-monthly'
MI_INDEX_URL = 'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX'
MIN_INTERVAL_SECONDS = 3.0
QUOTE_TABLE_MARK = '每日收盤行情'


class Response(NamedTuple):
    status: int
    content_type: str
    content: bytes
    url: str


def http_get(url: str, params: dict) -> Response:
    import requests

    response = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0'}, timeout=90)
    return Response(response.status_code, response.headers.get('content-type', ''), response.content, response.url)


def raw_file(out: Path, day: str) -> Path:
    return out / 'raw' / day[:4] / f'MI_INDEX-{day}.json.gz'


def quote_table(payload: dict) -> dict | None:
    for table in payload.get('tables') or []:
        if QUOTE_TABLE_MARK in (table.get('title') or '') and table.get('fields') and '證券代號' in table['fields']:
            return table
    return None


def iso_date(day: str) -> str:
    return f'{day[:4]}-{day[4:6]}-{day[6:]}'


def classify(response: Response, day: str) -> tuple[str, str]:
    """回傳 (結果, 原因)：saved／gap／stop。"""
    if response.status in (403, 429):
        return 'stop', f'HTTP {response.status}'
    if response.status != 200:
        return 'stop', f'HTTP {response.status}'
    try:
        payload = json.loads(response.content.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 'stop', f'非 JSON 回應（{response.content_type}），可能是驗證頁'
    if payload.get('stat') != 'OK':
        return 'gap', str(payload.get('stat') or '回應沒有 stat')
    if quote_table(payload) is None:
        return 'gap', '回應缺每日收盤行情表'
    if payload.get('date') != day:
        return 'stop', f'回應日期 {payload.get("date")} 不是請求日 {day}'
    return 'saved', ''


def write_once(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + '.partial')
    partial.write_bytes(data)
    os.replace(partial, target)


def fetch_history(out: Path, days: list[str], get: Callable = http_get, sleep: Callable = time.sleep) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    gaps_path = out / 'gaps.json'
    try:
        previous_gaps = {g['date']: g['reason'] for g in json.loads(gaps_path.read_text(encoding='utf-8'))['gaps']}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):  # 舊版直接寫入時可能留下半份：本次重新產生
        previous_gaps = {}
    summary: dict = {'requested': 0, 'fetched': 0, 'existing': 0, 'gaps': [], 'stopped': None}
    attempted: set[str] = set()
    first_request = True
    with (out / 'fetch-log.jsonl').open('a', encoding='utf-8') as log:
        for day in days:
            if raw_file(out, day).exists():
                summary['existing'] += 1
                continue
            if not first_request:
                sleep(MIN_INTERVAL_SECONDS)
            first_request = False
            attempted.add(day)
            summary['requested'] += 1
            entry = {'date': day, 'fetched_at_utc': datetime.now(timezone.utc).isoformat()}
            try:
                response = get(MI_INDEX_URL, {'date': day, 'type': 'ALLBUT0999', 'response': 'json'})
            except Exception as error:  # 連線錯誤一律停止，等人看過再續抓
                outcome, reason, response = 'stop', f'連線錯誤：{type(error).__name__}: {error}', None
            else:
                outcome, reason = classify(response, day)
                entry.update(http_status=response.status, source_url=response.url, sha256=hashlib.sha256(response.content).hexdigest())
            entry.update(outcome=outcome, reason=reason)
            log.write(json.dumps(entry, ensure_ascii=False) + '\n')
            log.flush()
            if outcome == 'saved':
                write_once(raw_file(out, day), gzip.compress(response.content, 9))
                summary['fetched'] += 1
            elif outcome == 'gap':
                summary['gaps'].append({'date': day, 'reason': reason})
            else:
                summary['stopped'] = {'date': day, 'reason': reason}
                break
    gaps = {g['date']: g['reason'] for g in summary['gaps']}
    for day, reason in previous_gaps.items():
        if day not in attempted and not raw_file(out, day).exists():
            gaps[day] = reason
    write_once(gaps_path, (json.dumps({'updated_at_utc': datetime.now(timezone.utc).isoformat(),
                                       'gaps': [{'date': d, 'reason': gaps[d]} for d in sorted(gaps)]}, ensure_ascii=False, indent=1) + '\n').encode('utf-8'))
    return summary


def trading_days_from_index(index_dir: Path) -> list[str]:
    """交易日曆取自證交所加權指數月檔（MI_5MINS_HIST）：有指數的日子就是交易日。"""
    days = []
    for path in sorted(index_dir.glob('taiex-*.json')):
        for row in json.loads(path.read_text(encoding='utf-8'))['data']['data']:
            year, month, day = row[0].split('/')
            days.append(f'{int(year) + 1911}{month}{day}')
    return sorted(set(days))


def security_kind(code: str, name: str) -> str | None:
    """etf（只收 0050）／common／preferred；其他（ETF、ETN、受益證券、存託憑證）回 None。"""
    if code == '0050':
        return 'etf'
    if 'DR' in name:
        return None
    if re.fullmatch(r'[1-9]\d{3}', code):
        return 'common'
    if re.fullmatch(r'[1-9]\d{3}[A-Z]\d?', code):
        return 'preferred'
    return None


def number(text: str) -> float | None:
    text = text.replace(',', '').strip()
    return None if text in ('', '--', '---') else float(text)


def change_sign(markup: str) -> str:
    """'<p style= color:green>-</p>' → '-'；'<p>X</p>' → 'X'（不比價，常見於除權息日）；空白 → ''。"""
    return re.sub(r'<[^>]*>', '', markup).strip()


def parse_row(day: str, fields: list[str], row: list[str]) -> dict:
    cell = dict(zip(fields, row))
    sign = change_sign(cell['漲跌(+/-)'])
    close = number(cell['收盤價'])
    spread = number(cell['漲跌價差'])
    if sign == '-':
        change = -spread
    elif sign == '+':
        change = spread
    elif sign == '' and close is not None:
        change = 0.0
    else:
        change = None
    volume = int(cell['成交股數'].replace(',', ''))
    return {
        'date': iso_date(day),
        'code': cell['證券代號'],
        'name': cell['證券名稱'],
        'kind': security_kind(cell['證券代號'], cell['證券名稱']),
        'open': number(cell['開盤價']),
        'high': number(cell['最高價']),
        'low': number(cell['最低價']),
        'close': close,
        'change_sign': sign,
        'change': change,
        'volume_shares': volume,
        'trades': int(cell['成交筆數'].replace(',', '')),
        'turnover': int(cell['成交金額'].replace(',', '')),
        # 成交股數含零股；只有零股成交時沒有開收盤價，不算停牌
        'suspended': volume == 0 and close is None,
    }


COLUMNS = ['date', 'code', 'name', 'kind', 'open', 'high', 'low', 'close', 'change_sign', 'change', 'volume_shares', 'trades', 'turnover', 'suspended']


def build_database(out: Path, db_path: Path) -> dict:
    """從原件重建 SQLite；先寫暫存檔再換上，重建中斷不會留下半份資料庫。"""
    partial = db_path.with_name(db_path.name + '.partial')
    partial.unlink(missing_ok=True)
    connection = sqlite3.connect(partial)
    connection.executescript('''
        CREATE TABLE daily (
            date TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL,
            change_sign TEXT NOT NULL, change REAL,
            volume_shares INTEGER NOT NULL, trades INTEGER NOT NULL, turnover INTEGER NOT NULL,
            suspended INTEGER NOT NULL,
            PRIMARY KEY (date, code)
        );
        CREATE INDEX daily_code_date ON daily (code, date);
        CREATE TABLE source_days (date TEXT PRIMARY KEY, raw_file TEXT NOT NULL, sha256 TEXT NOT NULL, rows INTEGER NOT NULL);
    ''')
    summary = {'days': 0, 'rows': 0, 'suspended_rows': 0, 'not_comparable_rows': 0, 'first_date': None, 'last_date': None}
    for path in sorted((out / 'raw').glob('*/MI_INDEX-*.json.gz')):
        day = path.name[len('MI_INDEX-'):-len('.json.gz')]
        content = gzip.decompress(path.read_bytes())
        payload = json.loads(content.decode('utf-8'))
        if payload.get('date') != day:
            connection.close()
            raise ValueError(f'{path} 內容日期 {payload.get("date")} 與檔名不符')
        table = quote_table(payload)
        rows = [parse_row(day, table['fields'], row) for row in table['data'] if security_kind(row[0], row[1])]
        date = iso_date(day)
        connection.executemany(f'INSERT INTO daily VALUES ({",".join("?" * len(COLUMNS))})', [[r[c] for c in COLUMNS] for r in rows])
        connection.execute('INSERT INTO source_days VALUES (?, ?, ?, ?)', (date, str(path.relative_to(out)), hashlib.sha256(content).hexdigest(), len(rows)))
        summary['days'] += 1
        summary['rows'] += len(rows)
        summary['suspended_rows'] += sum(r['suspended'] for r in rows)
        summary['not_comparable_rows'] += sum(r['change_sign'] == 'X' for r in rows)
        summary['first_date'] = summary['first_date'] or date
        summary['last_date'] = date
    connection.commit()
    connection.close()
    os.replace(partial, db_path)
    return summary


def lookup(db_path: Path, date: str, code: str) -> dict | None:
    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(f'SELECT {",".join(COLUMNS)} FROM daily WHERE date = ? AND code = ?', (date, code)).fetchone()
    finally:
        connection.close()
    if row is None:
        return None
    record = dict(zip(COLUMNS, row))
    record['suspended'] = bool(record['suspended'])
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    sub = parser.add_subparsers(dest='command', required=True)
    fetch = sub.add_parser('fetch', help='抓官方原件（可重跑續抓）')
    fetch.add_argument('--index-dir', type=Path, default=DEFAULT_INDEX_DIR, help='加權指數月檔目錄，當作交易日曆')
    fetch.add_argument('--start', default='20160101')
    fetch.add_argument('--end', default='99991231')
    sub.add_parser('build', help='由原件重建 SQLite')
    query = sub.add_parser('lookup', help='查某日某代號')
    query.add_argument('date')
    query.add_argument('code')
    args = parser.parse_args()
    db_path = args.out / 'stock-history.sqlite'
    if args.command == 'fetch':
        days = [d for d in trading_days_from_index(args.index_dir) if args.start <= d <= args.end]
        started = time.time()
        summary = fetch_history(args.out, days, get=http_get)
        summary.update(calendar_days=len(days), first=days[0] if days else None, last=days[-1] if days else None, seconds=round(time.time() - started))
        print(json.dumps(summary, ensure_ascii=False))
        return 2 if summary['stopped'] else 0
    if args.command == 'build':
        print(json.dumps(build_database(args.out, db_path), ensure_ascii=False))
        return 0
    print(json.dumps(lookup(db_path, args.date, args.code), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
