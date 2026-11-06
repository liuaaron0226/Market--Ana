"""票01驗收：以證交所另一個官方來源 STOCK_DAY（個股日成交資訊）逐欄核對資料庫抽樣。

單路、間隔 3 秒、遇 403/429 停止；原件存 data/stock-history/verify/，已存在就不重抓。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from stock_history import http_get, lookup  # noqa: E402

DB = ROOT / 'data/stock-history/stock-history.sqlite'
VERIFY = ROOT / 'data/stock-history/verify'
SAMPLES = {
    '2330': ['2016-01-05', '2016-06-27', '2016-11-30'],
    '2317': ['2016-03-30', '2016-03-31', '2016-09-02'],
    '1410': ['2016-06-03', '2016-09-12', '2016-09-13'],
}


def official_month(code: str, month: str, first: bool) -> dict:
    target = VERIFY / f'STOCK_DAY-{code}-{month}.json'
    if target.exists():
        return json.loads(target.read_text(encoding='utf-8'))
    if not first:
        time.sleep(3)
    response = http_get('https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY', {'date': f'{month}01', 'stockNo': code, 'response': 'json'})
    if response.status != 200:
        raise SystemExit(f'HTTP {response.status}，停止')
    payload = json.loads(response.content.decode('utf-8'))
    VERIFY.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'source_url': response.url, 'data': payload}, ensure_ascii=False) + '\n', encoding='utf-8')
    return {'source_url': response.url, 'data': payload}


def number(text: str):
    text = text.replace(',', '').replace('X', '').strip()
    return None if text in ('', '--') else float(text)


def main() -> int:
    results = []
    first = True
    for code, dates in SAMPLES.items():
        for date in dates:
            month = date[:7].replace('-', '')
            official = official_month(code, month, first)
            first = False
            fields = official['data']['fields']
            roc = f'{int(date[:4]) - 1911}/{date[5:7]}/{date[8:]}'
            row = next((dict(zip(fields, r)) for r in official['data']['data'] if r[0] == roc), None)
            ours = lookup(DB, date, code)
            if row is None or ours is None:
                results.append({'code': code, 'date': date, 'match': False, 'official': row, 'ours': ours})
                continue
            expected = {
                'open': number(row['開盤價']), 'high': number(row['最高價']), 'low': number(row['最低價']), 'close': number(row['收盤價']),
                'volume_shares': int(number(row['成交股數'])), 'turnover': int(number(row['成交金額'])), 'trades': int(number(row['成交筆數'])),
            }
            diffs = {k: [v, ours[k]] for k, v in expected.items() if v != ours[k]}
            results.append({'code': code, 'date': date, 'match': not diffs, 'diffs': diffs, 'official_change': row['漲跌價差'],
                            'ours': {k: ours[k] for k in ('open', 'high', 'low', 'close', 'volume_shares', 'change_sign', 'suspended')}})
    report = {'source': 'TWSE STOCK_DAY（獨立於 MI_INDEX）', 'all_match': all(r['match'] for r in results), 'results': results}
    (VERIFY / 'verify-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0 if report['all_match'] else 1


if __name__ == '__main__':
    sys.exit(main())
