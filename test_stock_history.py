import gzip
import json
import tempfile
import unittest
from pathlib import Path

from stock_history import Response, build_database, fetch_history, lookup, trading_days_from_index

FIELDS = ['證券代號', '證券名稱', '成交股數', '成交筆數', '成交金額', '開盤價', '最高價', '最低價', '收盤價', '漲跌(+/-)', '漲跌價差', '最後揭示買價', '最後揭示買量', '最後揭示賣價', '最後揭示賣量', '本益比']
# 以下各列逐字取自證交所 MI_INDEX 2016-01-04 官方回應
ROW_0050 = ['0050', '元大台灣50', '16,928,063', '5,730', '1,009,176,452', '60.80', '60.95', '59.05', '59.55', '<p style= color:green>-</p>', '1.20', '59.55', '46', '59.60', '213', '0.00']
ROW_2330 = ['2330', '台積電', '43,800,291', '14,188', '6,137,797,502', '142.50', '143.50', '139.00', '139.50', '<p style= color:green>-</p>', '3.50', '139.50', '4', '140.00', '403', '11.53']
ROW_1469_SUSPENDED = ['1469', '理隆', '0', '0', '0', '--', '--', '--', '--', '<p> </p>', '0.00', '12.45', '2', '12.60', '1', '0.00']
ROW_1475_NOT_COMPARABLE = ['1475', '業旺', '1,000', '1', '9,160', '9.16', '9.16', '9.16', '9.16', '<p>X</p>', '0.00', '8.81', '1', '9.15', '2', '0.00']
ROW_0052_ETF = ['0052', '富邦科技', '1,000', '1', '37,190', '37.19', '37.19', '37.19', '37.19', '<p>X</p>', '0.00', '37.05', '2', '37.19', '9', '0.00']
# 以下取自 2026-11-06 官方回應：特別股、存託憑證、只有零股成交而沒有收盤價
ROW_2881A_PREFERRED = ['2881A', '富邦特', '18,331', '43', '1,149,546', '62.70', '62.80', '62.60', '62.70', '<p style= color:green>-</p>', '0.10', '62.70', '5', '62.80', '13', '0.00']
ROW_910861_DR = ['910861', '神州-DR', '0', '0', '0', '--', '--', '--', '--', '<p> </p>', '0.00', '0.00', '0', '0.00', '0', '0.00']
ROW_1538_ODD_LOT_ONLY = ['1538', '正峰', '386', '3', '5,983', '--', '--', '--', '--', '<p> </p>', '0.00', '15.40', '3', '15.55', '1', '0.00']


def official_payload(day: str, rows: list[list[str]]) -> bytes:
    payload = {
        'tables': [
            {'title': '價格指數(臺灣證券交易所)', 'fields': ['指數', '收盤指數'], 'data': [['發行量加權股價指數', '8,114.26']]},
            {},
            {'title': f'{day} 每日收盤行情(全部(不含權證、牛熊證、可展延牛熊證))', 'fields': FIELDS, 'data': rows},
        ],
        'type': 'ALLBUT0999', 'stat': 'OK', 'date': day,
    }
    return json.dumps(payload, ensure_ascii=False).encode('utf-8')


class FakeTwse:
    """依日期回應的假證交所；記下每次請求與 sleep。"""

    def __init__(self, responses: dict[str, Response]):
        self.responses = responses
        self.requested: list[str] = []
        self.events: list[str] = []

    def get(self, url, params):
        day = params['date']
        self.requested.append(day)
        self.events.append(f'get:{day}')
        return self.responses[day]

    def sleep(self, seconds):
        self.events.append(f'sleep:{seconds}')


def ok(day: str, rows=None) -> Response:
    return Response(200, 'application/json;charset=UTF-8', official_payload(day, rows or [ROW_2330]), f'https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={day}')


def raw_path(out: Path, day: str) -> Path:
    return out / 'raw' / day[:4] / f'MI_INDEX-{day}.json.gz'


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_trading_day_is_saved_as_the_exact_official_bytes(self):
        twse = FakeTwse({'20160104': ok('20160104'), '20160105': ok('20160105')})
        summary = fetch_history(self.out, ['20160104', '20160105'], get=twse.get, sleep=twse.sleep)
        self.assertEqual(summary['fetched'], 2)
        for day in ['20160104', '20160105']:
            self.assertEqual(gzip.decompress(raw_path(self.out, day).read_bytes()), twse.responses[day].content)

    def test_existing_raw_files_are_neither_refetched_nor_overwritten(self):
        target = raw_path(self.out, '20160104')
        target.parent.mkdir(parents=True)
        target.write_bytes(b'original')
        twse = FakeTwse({'20160104': ok('20160104'), '20160105': ok('20160105')})
        summary = fetch_history(self.out, ['20160104', '20160105'], get=twse.get, sleep=twse.sleep)
        self.assertEqual(twse.requested, ['20160105'])
        self.assertEqual(target.read_bytes(), b'original')
        self.assertEqual(summary['existing'], 1)

    def test_requests_are_spaced_at_least_three_seconds_apart(self):
        twse = FakeTwse({d: ok(d) for d in ['20160104', '20160105', '20160106']})
        fetch_history(self.out, ['20160104', '20160105', '20160106'], get=twse.get, sleep=twse.sleep)
        gets = [i for i, e in enumerate(twse.events) if e.startswith('get:')]
        for before, after in zip(gets, gets[1:]):
            waits = [float(e.split(':')[1]) for e in twse.events[before + 1:after] if e.startswith('sleep:')]
            self.assertGreaterEqual(sum(waits), 3.0)

    def test_stops_on_rate_limit_and_resumes_from_the_interrupted_day(self):
        for status in (403, 429):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                blocked = FakeTwse({'20160104': ok('20160104'), '20160105': Response(status, 'text/html', b'<html>blocked</html>', 'u'), '20160106': ok('20160106')})
                summary = fetch_history(out, ['20160104', '20160105', '20160106'], get=blocked.get, sleep=blocked.sleep)
                self.assertEqual(blocked.requested, ['20160104', '20160105'])
                self.assertEqual(summary['stopped']['date'], '20160105')
                self.assertIn(str(status), summary['stopped']['reason'])
                self.assertFalse(raw_path(out, '20160105').exists())

                resumed = FakeTwse({'20160105': ok('20160105'), '20160106': ok('20160106')})
                summary = fetch_history(out, ['20160104', '20160105', '20160106'], get=resumed.get, sleep=resumed.sleep)
                self.assertEqual(resumed.requested, ['20160105', '20160106'])
                self.assertIsNone(summary['stopped'])

    def test_stops_on_verification_page_served_with_status_200(self):
        twse = FakeTwse({'20160104': Response(200, 'text/html', '<html>請輸入驗證碼</html>'.encode(), 'u'), '20160105': ok('20160105')})
        summary = fetch_history(self.out, ['20160104', '20160105'], get=twse.get, sleep=twse.sleep)
        self.assertEqual(twse.requested, ['20160104'])
        self.assertEqual(summary['stopped']['date'], '20160104')
        self.assertFalse(raw_path(self.out, '20160104').exists())

    def test_trading_day_without_quote_table_is_listed_as_gap_with_reason_and_retried_later(self):
        no_data = Response(200, 'application/json', '{"stat":"很抱歉，沒有符合條件的資料!"}'.encode(), 'u')
        twse = FakeTwse({'20160104': no_data, '20160105': ok('20160105')})
        summary = fetch_history(self.out, ['20160104', '20160105'], get=twse.get, sleep=twse.sleep)
        self.assertEqual(summary['gaps'], [{'date': '20160104', 'reason': '很抱歉，沒有符合條件的資料!'}])
        self.assertFalse(raw_path(self.out, '20160104').exists())
        gaps = json.loads((self.out / 'gaps.json').read_text(encoding='utf-8'))
        self.assertEqual([g['date'] for g in gaps['gaps']], ['20160104'])

        retry = FakeTwse({'20160104': ok('20160104')})
        summary = fetch_history(self.out, ['20160104', '20160105'], get=retry.get, sleep=retry.sleep)
        self.assertEqual(retry.requested, ['20160104'])
        self.assertEqual(json.loads((self.out / 'gaps.json').read_text(encoding='utf-8'))['gaps'], [])

    def test_response_for_another_date_stops_instead_of_being_saved_under_the_requested_date(self):
        twse = FakeTwse({'20160104': ok('20160105'), '20160105': ok('20160105')})
        summary = fetch_history(self.out, ['20160104', '20160105'], get=twse.get, sleep=twse.sleep)
        self.assertEqual(summary['stopped']['date'], '20160104')
        self.assertIn('20160105', summary['stopped']['reason'])
        self.assertFalse(raw_path(self.out, '20160104').exists())

    def test_corrupt_gap_list_from_an_interrupted_run_does_not_block_resuming(self):
        (self.out / 'gaps.json').write_text('{"gaps": [', encoding='utf-8')
        twse = FakeTwse({'20160104': ok('20160104')})
        summary = fetch_history(self.out, ['20160104'], get=twse.get, sleep=twse.sleep)
        self.assertEqual(summary['fetched'], 1)

    def test_every_request_is_logged(self):
        twse = FakeTwse({'20160104': ok('20160104')})
        fetch_history(self.out, ['20160104'], get=twse.get, sleep=twse.sleep)
        lines = [json.loads(line) for line in (self.out / 'fetch-log.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(lines[0]['date'], '20160104')
        self.assertEqual(lines[0]['http_status'], 200)
        self.assertEqual(lines[0]['outcome'], 'saved')
        self.assertIn('fetched_at_utc', lines[0])
        self.assertEqual(len(lines[0]['sha256']), 64)


class TradingCalendarTests(unittest.TestCase):
    def test_trading_days_come_from_official_index_history_files(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for name, rows in {'taiex-201601.json': [['105/01/04', '1', '1', '1', '1'], ['105/01/05', '1', '1', '1', '1']],
                               'taiex-201602.json': [['105/02/01', '1', '1', '1', '1']]}.items():
                (folder / name).write_text(json.dumps({'data': {'stat': 'OK', 'data': rows}}), encoding='utf-8')
            self.assertEqual(trading_days_from_index(folder), ['20160104', '20160105', '20160201'])


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        rows = [ROW_0050, ROW_2330, ROW_1469_SUSPENDED, ROW_1475_NOT_COMPARABLE, ROW_0052_ETF, ROW_2881A_PREFERRED, ROW_910861_DR, ROW_1538_ODD_LOT_ONLY]
        twse = FakeTwse({'20160104': ok('20160104', rows)})
        fetch_history(self.out, ['20160104'], get=twse.get, sleep=twse.sleep)
        self.db = self.out / 'stock-history.sqlite'
        self.summary = build_database(self.out, self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_price_and_volume_can_be_looked_up_by_date_and_code(self):
        record = lookup(self.db, '2016-01-04', '2330')
        self.assertEqual(record['name'], '台積電')
        self.assertEqual((record['open'], record['high'], record['low'], record['close']), (142.5, 143.5, 139.0, 139.5))
        self.assertEqual(record['volume_shares'], 43800291)
        self.assertEqual(record['turnover'], 6137797502)
        self.assertEqual(record['change'], -3.5)
        self.assertFalse(record['suspended'])

    def test_0050_is_included_but_other_etfs_are_not(self):
        self.assertEqual(lookup(self.db, '2016-01-04', '0050')['close'], 59.55)
        self.assertIsNone(lookup(self.db, '2016-01-04', '0052'))

    def test_day_without_trade_or_close_is_marked_suspended_without_invented_prices(self):
        record = lookup(self.db, '2016-01-04', '1469')
        self.assertTrue(record['suspended'])
        self.assertEqual(record['volume_shares'], 0)
        self.assertEqual((record['open'], record['high'], record['low'], record['close'], record['change']), (None, None, None, None, None))

    def test_not_comparable_marker_used_on_ex_rights_days_is_kept_and_prices_are_not_adjusted(self):
        record = lookup(self.db, '2016-01-04', '1475')
        self.assertEqual(record['change_sign'], 'X')
        self.assertEqual(record['close'], 9.16)
        self.assertEqual(lookup(self.db, '2016-01-04', '2330')['change_sign'], '-')

    def test_listed_preferred_shares_are_kept_and_labelled_while_depositary_receipts_are_not(self):
        self.assertEqual(lookup(self.db, '2016-01-04', '2881A')['kind'], 'preferred')
        self.assertEqual(lookup(self.db, '2016-01-04', '2330')['kind'], 'common')
        self.assertEqual(lookup(self.db, '2016-01-04', '0050')['kind'], 'etf')
        self.assertIsNone(lookup(self.db, '2016-01-04', '910861'))

    def test_day_with_only_odd_lot_trades_has_no_close_but_is_not_called_suspended(self):
        record = lookup(self.db, '2016-01-04', '1538')
        self.assertFalse(record['suspended'])
        self.assertEqual(record['volume_shares'], 386)
        self.assertEqual((record['open'], record['close']), (None, None))

    def test_raw_file_whose_content_is_for_another_date_fails_the_build(self):
        target = raw_path(self.out, '20160105')
        target.write_bytes(gzip.compress(official_payload('20160106', [ROW_2330])))
        with self.assertRaisesRegex(ValueError, '20160106'):
            build_database(self.out, self.db)

    def test_unknown_date_or_code_returns_none(self):
        self.assertIsNone(lookup(self.db, '2016-01-05', '2330'))
        self.assertIsNone(lookup(self.db, '2016-01-04', '9999'))

    def test_build_reports_days_and_rows(self):
        self.assertEqual(self.summary['days'], 1)
        self.assertEqual(self.summary['rows'], 6)
        self.assertEqual(self.summary['first_date'], '2016-01-04')

    def test_rebuild_is_repeatable(self):
        again = build_database(self.out, self.db)
        self.assertEqual(again['rows'], 6)
        self.assertEqual(lookup(self.db, '2016-01-04', '2330')['close'], 139.5)


if __name__ == '__main__':
    unittest.main()
