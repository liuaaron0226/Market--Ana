import gzip
import json
import tempfile
import unittest
from pathlib import Path

from corporate_actions import fetch_all, months, parse_events, raw_file
from stock_history import Response

# 逐字取自證交所 TWT49U 2016-06／07 官方回應
TWT49U_FIELDS = ['資料日期', '股票代號', '股票名稱', '除權息前收盤價', '除權息參考價', '權值+息值', '權/息', '漲停價格', '跌停價格', '開盤競價基準', '減除股利參考價', '詳細資料', '最近一次申報資料 季別/日期', '最近一次申報每股 (單位)淨值', '最近一次申報每股 (單位)盈餘']
ROW_2330 = ['105年06月27日', '2330', '台積電', '159.00', '153.00', '6.000000', '息', '168.00', '138.00', '153.00', '153.00', '2330,20160627', '', '248.05', '49.33']
ROW_6443 = ['105年06月06日', '6443', '元晶', '14.20', '13.81', '0.386549', '權', '15.60', '12.45', '14.20', '14.20', '6443,20160606', '', '6.50', '-0.60']
# 逐字取自 TWTB8U 2025 官方回應
TWTB8U_FIELDS = ['恢復買賣日期', '股票代號', '名稱', '停止買賣前收盤價格', '恢復買賣參考價', '漲停價格', '跌停價格', '開盤競價基準', '詳細資料']
ROW_4763 = ['114/06/30', '4763', '材料-KY', '885.00', '88.50', '97.30', '79.70', '88.50', '4763,20250619,20250630']


def payload(fields, rows):
    return json.dumps({'stat': 'OK', 'fields': fields, 'data': rows}, ensure_ascii=False).encode('utf-8')


class Fake:
    def __init__(self, responses):
        self.responses, self.requested, self.events = responses, [], []

    def get(self, url, params):
        key = (url.rsplit('/', 1)[-1], params['startDate'][:6])
        self.requested.append(key)
        self.events.append('get')
        return self.responses.get(key, Response(200, 'application/json', b'{"stat":"\\u5f88\\u62b1\\u6b49"}', url))

    def sleep(self, seconds):
        self.events.append(seconds)


class CorporateActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_months_cover_both_ends_with_real_month_lengths(self):
        self.assertEqual(months('20160115', '20160310'), [('20160101', '20160131'), ('20160201', '20160229'), ('20160301', '20160331')])

    def test_each_month_of_each_table_is_saved_once_three_seconds_apart(self):
        fake = Fake({('TWT49U', '201606'): Response(200, 'application/json', payload(TWT49U_FIELDS, [ROW_2330]), 'u')})
        summary = fetch_all(self.out, '20160601', '20160701', get=fake.get, sleep=fake.sleep)
        self.assertEqual(summary['fetched'], 6)          # 3 張表 × 2 個月；沒有事件的月份也存原件
        self.assertEqual(fake.events.count('get'), 6)
        self.assertEqual([e for e in fake.events if e != 'get'], [3.0] * 5)
        again = Fake({})
        fetch_all(self.out, '20160601', '20160701', get=again.get, sleep=again.sleep)
        self.assertEqual(again.requested, [])

    def test_stops_on_rate_limit_and_does_not_save(self):
        fake = Fake({('TWT49U', '201606'): Response(429, 'text/html', b'', 'u')})
        summary = fetch_all(self.out, '20160601', '20160601', get=fake.get, sleep=fake.sleep)
        self.assertIn('429', summary['stopped']['reason'])
        self.assertFalse(raw_file(self.out, 'TWT49U', '20160601').exists())

    def test_events_parse_dividends_rights_and_par_value_changes(self):
        fake = Fake({('TWT49U', '201606'): Response(200, 'application/json', payload(TWT49U_FIELDS, [ROW_2330, ROW_6443]), 'u'),
                     ('TWTB8U', '202506'): Response(200, 'application/json', payload(TWTB8U_FIELDS, [ROW_4763]), 'u')})
        fetch_all(self.out, '20160601', '20160601', get=fake.get, sleep=fake.sleep)
        fetch_all(self.out, '20250601', '20250601', get=fake.get, sleep=fake.sleep)
        events = {(e['code'], e['type']): e for e in parse_events(self.out)}
        tsmc = events[('2330', '除息')]
        self.assertEqual((tsmc['date'], tsmc['close_before'], tsmc['reference'], tsmc['value']), ('2016-06-27', 159.0, 153.0, 6.0))
        self.assertEqual((tsmc['limit_up'], tsmc['limit_down']), (168.0, 138.0))
        self.assertEqual(events[('6443', '除權')]['reference'], 13.81)
        self.assertNotIn('TWTAVU', ''.join(e['source'] for e in events.values()))
        material = events[('4763', '變更面額')]
        self.assertEqual((material['date'], material['close_before'], material['reference']), ('2025-06-30', 885.0, 88.5))


    def test_capital_reduction_history_uses_resume_reference_prices(self):
        # 逐字取自證交所 TWTAUU 2020 官方回應
        fields = ['恢復買賣日期', '股票代號', '名稱', '停止買賣前收盤價格', '恢復買賣參考價', '漲停價格', '跌停價格', '開盤競價基準', '除權參考價', '減資原因', '詳細資料']
        row = ['109/01/20', '3557', '嘉威', '38.05', '48.57', '53.40', '43.75', '48.55', '--', '彌補虧損', '3557  ,20200108']
        fake = Fake({('TWTAUU', '202001'): Response(200, 'application/json', payload(fields, [row]), 'u')})
        fetch_all(self.out, '20200101', '20200101', get=fake.get, sleep=fake.sleep)
        [event] = [e for e in parse_events(self.out) if e['type'] == '減資']
        self.assertEqual((event['date'], event['code'], event['close_before'], event['reference']), ('2020-01-20', '3557', 38.05, 48.57))

if __name__ == '__main__':
    unittest.main()
