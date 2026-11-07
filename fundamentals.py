"""2TON 補資料（本人 11-07「1.抓」）：借券（TWT93U）、本益比／淨值比／殖利率（BWIBBU_d）逐日；
月營收（t187ap05_L）、季 EPS（t187ap14_L）是 OpenAPI 最新一期快照。

抓取規則同 stock_history：單路、間隔≥3 秒、遇 403／429／非 JSON 即停、原件存在就不重抓、不覆寫。
逐日資料只抓既有籌碼原件涵蓋的交易日（data/market-snapshots/stock-detail/t86）。
限制（11-07 實核）：OpenAPI 月營收與 EPS 只有最新一期，不能回推歷史日期；EPS 只含近期已公告的公司。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from stock_history import http_get

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'data/fundamentals'
DAILY = {
    'twt93u': ('https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U', lambda d: {'date': d, 'response': 'json'}),
    'bwibbu': ('https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d', lambda d: {'date': d, 'selectType': 'ALL', 'response': 'json'}),
}
SNAPSHOTS = {
    't187ap05_L': 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L',
    't187ap14_L': 'https://openapi.twse.com.tw/v1/opendata/t187ap14_L',
    # 最新交易日估值的官方替代來源（www.twse.com.tw 逾時時用；只有最新一天）
    'BWIBBU_ALL': 'https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL',
}


def chip_days() -> list[str]:
    return sorted(p.stem.split('-')[1] for p in (ROOT / 'data/market-snapshots/stock-detail/t86').glob('t86-*.json'))


def fetch(get=http_get, sleep=time.sleep, daily: bool = True) -> dict:
    summary = {'fetched': 0, 'existing': 0, 'empty': [], 'stopped': None}
    first = True

    def request(url, params):
        nonlocal first
        if not first:
            sleep(3.0)
        first = False
        response = get(url, params)
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}')
        try:
            return response, json.loads(response.content.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError(f'非 JSON 回應（{response.content_type}），可能是驗證頁')

    summary['stopped_sources'] = {}
    # 每個來源各自停：某一端點逾時只停那個來源，不擋其他來源（11-07 實核 TWT93U 逾時、BWIBBU 正常）
    for name, (url, params) in (DAILY.items() if daily else ()):
        try:
            for day in chip_days():
                target = OUT / name / f'{name}-{day}.json'
                if target.exists():
                    summary['existing'] += 1
                    continue
                response, payload = request(url, params(day))
                if payload.get('stat') != 'OK' or str(payload.get('date')) != day:
                    summary['empty'].append(f'{name}-{day}: {payload.get("stat")}')
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(response.content)
                summary['fetched'] += 1
        except Exception as error:
            summary['stopped_sources'][name] = f'{type(error).__name__}: {error}'
    try:
        for name, url in SNAPSHOTS.items():
            response, payload = request(url, {})
            issued = max((row.get('出表日期') or row.get('Date') or '' for row in payload), default='unknown')
            target = OUT / 'openapi' / f'{name}-{issued}.json'
            if target.exists():
                summary['existing'] += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(response.content)
            (target.with_suffix('.meta.json')).write_text(json.dumps(
                {'source_url': response.url, 'fetched_at_utc': datetime.now(timezone.utc).isoformat(), 'rows': len(payload)},
                ensure_ascii=False) + '\n', encoding='utf-8')
            summary['fetched'] += 1
    except Exception as error:
        summary['stopped_sources']['openapi'] = f'{type(error).__name__}: {error}'
    summary['stopped'] = '; '.join(f'{k}: {v}' for k, v in summary['stopped_sources'].items()) or None
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots-only', action='store_true', help='只抓 OpenAPI 月營收／EPS（www.twse.com.tw 不回應時用）')
    args = parser.parse_args()
    started = time.time()
    summary = fetch(daily=not args.snapshots_only)
    summary['seconds'] = round(time.time() - started)
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if summary['stopped'] else 0


if __name__ == '__main__':
    sys.exit(main())
