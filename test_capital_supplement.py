"""還原價需要、但共同 events.json 沒有的官方資料：ETF 分割、櫃買減資與面額變更、上市除權息的現金股利。

樣本列逐字取自 2026-11-14 的官方回應（證交所 TWTCAU／TWT49UDetail、櫃買 revivt／pvChgRslt）。
"""
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from capital_supplement import load, parse_capital_events, parse_detail_cash, refresh
from stock_history import Response


def twse(fields, rows):
    return json.dumps({'stat': 'OK', 'fields': fields, 'data': rows}, ensure_ascii=False).encode('utf-8')


TWTCAU_FIELDS = ['恢復買賣日期', 'ETF代號', '名稱', '分割(反分割)', '停止買賣前收盤價格', '恢復買賣參考價', '漲停價格', '跌停價格', '開盤競價基準']
ROW_0050 = ['114/06/18', '0050', '元大台灣50', '分割', '188.65', '47.16', '51.85', '42.45', '47.16']
ROW_00632R = ['113/12/11', '00632R', '元大台灣50反1', '反分割', '3.28', '22.96', '25.25', '20.67', '22.96']


def tpex(fields, rows):
    return json.dumps({'date': '20210801~20261114', 'stat': 'ok',
                       'tables': [{'title': '', 'totalCount': len(rows), 'fields': fields, 'data': rows}]},
                      ensure_ascii=False).encode('utf-8')


REVIVT_FIELDS = ['恢復買賣日期', '股票代號', '名稱', '最後交易日之收盤價格', '減資恢復買賣開始日參考價格', '漲停價格', '跌停價格', '開始交易基準價', '除權參考價', '減資原因', '詳細資料']
ROW_3555 = ['1101006', '3555', '重鵬', '30.10', '41.53', '45.65', '37.40', '41.55', '0.00', '彌補虧損', '<table></table>']
PVCHG_FIELDS = ['恢復買賣日期', '證券代號', '證券名稱', '最後交易日之收盤價格', '恢復買賣開始參考價', '漲停價格', '跌停價格', '開始交易基準價', '詳細資料']
ROW_6613 = ['1110829', '6613', '朋億*           ', '169.00', '84.50', '92.90', '76.10', '84.50', '<table></table>']
DETAIL_FIELDS = ['股票代號', '股票名稱', '(每股配發現金股利)除息', '(增資配股) 除權', 'A. 按普通股股東持股比例每千股無償配股', 'B. 員工紅利轉增資', 'C. (有償) 現金增資', '每股認購金額', 'a. 公開承銷', 'b. 員工認購', ' c. 原股東認購', '按股東持股比例每千股認購']
ROW_2464_DETAIL = ['2464  ', '盟立            ', '0.471403 元／股', '', '0 股', '0 股', '8,500,000 股', '138 元／股', '850,000 股', '850,000 股', '6,800,000 股', '31.06882903 股']


def detail(rows):
    return json.dumps({'stat': 'ok', 'fields': DETAIL_FIELDS, 'data': rows}, ensure_ascii=False).encode('utf-8')


class ParseCapitalEventsTest(unittest.TestCase):
    def test_twse_etf_splits_become_events_with_the_official_reference_price(self):
        events = parse_capital_events('TWSE-TWTCAU', twse(TWTCAU_FIELDS, [ROW_00632R, ROW_0050]))
        self.assertEqual(events, [
            {'date': '2024-12-11', 'code': '00632R', 'type': '分割', 'close_before': 3.28, 'reference': 22.96,
             'source': 'TWSE-TWTCAU'},
            {'date': '2025-06-18', 'code': '0050', 'type': '分割', 'close_before': 188.65, 'reference': 47.16,
             'source': 'TWSE-TWTCAU'},
        ])

    def test_tpex_reductions_and_par_changes_read_the_tables_inside_the_response(self):
        reductions = parse_capital_events('TPEx-revivt', tpex(REVIVT_FIELDS, [ROW_3555]))
        self.assertEqual(reductions, [
            {'date': '2021-10-06', 'code': '3555', 'type': '減資', 'close_before': 30.10, 'reference': 41.53,
             'source': 'TPEx-revivt'}])
        changes = parse_capital_events('TPEx-pvChgRslt', tpex(PVCHG_FIELDS, [ROW_6613]))
        self.assertEqual(changes, [
            {'date': '2022-08-29', 'code': '6613', 'type': '變更面額', 'close_before': 169.0, 'reference': 84.5,
             'source': 'TPEx-pvChgRslt'}])
        self.assertEqual(parse_capital_events('TPEx-etfSplitRslt', tpex(PVCHG_FIELDS, [])), [])

    def test_no_events_in_range_is_empty_but_an_unrecognised_table_is_an_error(self):
        nothing = json.dumps({'stat': '很抱歉，沒有符合條件的資料!'}, ensure_ascii=False).encode('utf-8')
        self.assertEqual(parse_capital_events('TWSE-TWTCAU', nothing), [])
        renamed = twse(['恢復買賣日期', 'ETF代號', '名稱', '參考價'], [['114/06/18', '0050', '元大台灣50', '47.16']])
        with self.assertRaises(ValueError):
            parse_capital_events('TWSE-TWTCAU', renamed)
        with self.assertRaises(ValueError):
            parse_capital_events('TPEx-revivt', b'<html>please verify</html>')


class ParseDetailCashTest(unittest.TestCase):
    def test_cash_dividend_per_share_is_read_from_the_ex_rights_detail(self):
        # 盟立 2464 2026-09-02 除權息：現金股利 0.471403 元，另有現金增資（不是配股）。
        self.assertEqual(parse_detail_cash(detail([ROW_2464_DETAIL])), 0.471403)

    def test_detail_without_the_row_is_an_error_not_zero(self):
        missing = json.dumps({'stat': '無相關資料'}, ensure_ascii=False).encode('utf-8')
        with self.assertRaises(ValueError):
            parse_detail_cash(missing)


EMPTY_TWSE = json.dumps({'stat': '很抱歉，沒有符合條件的資料!'}, ensure_ascii=False).encode('utf-8')


class Fake:
    """假的官方站：依網址最後一段（與明細的代號）回預先準備的內容，並記下請求與等待。"""

    def __init__(self, responses=None):
        self.responses = {'TWTCAU': twse(TWTCAU_FIELDS, [ROW_0050]), 'revivt': tpex(REVIVT_FIELDS, [ROW_3555]),
                          'pvChgRslt': tpex(PVCHG_FIELDS, [ROW_6613]), 'etfSplitRslt': tpex(PVCHG_FIELDS, []),
                          **(responses or {})}
        self.requested, self.timeline = [], []

    def get(self, url, params):
        name = url.rsplit('/', 1)[-1]
        key = f"{name}:{params['STK_NO']}:{params['T1']}" if name == 'TWT49UDetail' else name
        self.requested.append((key, dict(params)))
        self.timeline.append(key)
        content = self.responses.get(key)
        if isinstance(content, Response):
            return content
        if content is None:
            raise AssertionError(f'未預期的請求：{key}')
        return Response(200, 'application/json', content, url)

    def sleep(self, seconds):
        self.timeline.append(seconds)


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'capital-supplement.json'
        self.seed = Path(self.tmp.name) / 'no-seed.json'

    def refresh(self, fake, listed=(), start='2021-08-01', end='2026-11-14'):
        return refresh(self.path, list(listed), start, end, seed=self.seed, get=fake.get, sleep=fake.sleep)

    def test_fetches_the_four_reference_price_tables_politely_and_saves_their_events(self):
        fake = Fake()
        summary = self.refresh(fake)
        self.assertIsNone(summary['stopped'])
        self.assertEqual(fake.timeline, ['TWTCAU', 5.0, 'revivt', 5.0, 'pvChgRslt', 5.0, 'etfSplitRslt'])
        self.assertEqual(fake.requested[0][1], {'startDate': '20210801', 'endDate': '20261114', 'response': 'json'})
        self.assertEqual(fake.requested[1][1], {'startDate': '2021/08/01', 'endDate': '2026/11/14', 'response': 'json'})
        saved = load(self.path, self.seed)
        self.assertEqual([(e['code'], e['date'], e['type']) for e in saved['events']],
                         [('3555', '2021-10-06', '減資'), ('6613', '2022-08-29', '變更面額'), ('0050', '2025-06-18', '分割')])
        self.assertEqual(saved['cash_dividends'], {})

    def test_cash_dividends_are_fetched_once_for_listed_ex_rights_with_cash_inside_the_window(self):
        listed = [
            {'date': '2026-09-02', 'code': '2464', 'type': '除權息', 'source': 'TWT49U-202609.json.gz'},
            {'date': '2026-09-02', 'code': '6669', 'type': '除權', 'source': 'TWT49U-202609.json.gz'},      # 純配股：不用拆
            {'date': '2026-09-02', 'code': '6753', 'type': '除息', 'source': 'TWT49U-202609.json.gz'},      # 現金股利：不還原
            {'date': '2025-07-01', 'code': '2230', 'type': '除權息', 'source': 'TPEx-exDailyQ-2025.json'},  # 櫃買原件自帶
            {'date': '2026-12-01', 'code': '1101', 'type': '除權息', 'source': 'TWT49U-202612.json.gz'},    # 視窗之後
        ]
        fake = Fake({'TWT49UDetail:2464:20260902': detail([ROW_2464_DETAIL])})
        summary = self.refresh(fake, listed)
        # 證交所的先做完（分割表、現金股利明細），再打櫃買：櫃買那邊出狀況不會拖住上市的現金股利。
        self.assertEqual(fake.timeline, ['TWTCAU', 3.0, 'TWT49UDetail:2464:20260902',
                                         5.0, 'revivt', 5.0, 'pvChgRslt', 5.0, 'etfSplitRslt'])
        self.assertEqual(fake.requested[1][1], {'STK_NO': '2464', 'T1': '20260902', 'response': 'json'})
        self.assertEqual(load(self.path, self.seed)['cash_dividends'], {'2464|2026-09-02': 0.471403})
        self.assertEqual((summary['requested'], summary['cash_dividends'], summary['missing_details']), (5, 1, []))

        again = Fake()  # 第二次：現金股利已經有了，不再請求明細
        self.assertEqual(self.refresh(again, listed)['requested'], 4)

    def test_a_missing_detail_is_recorded_and_retried_next_time_without_stopping_the_rest(self):
        listed = [{'date': '2026-09-01', 'code': '2889', 'type': '除權息', 'source': 'TWT49U-202609.json.gz'},
                  {'date': '2026-09-02', 'code': '2464', 'type': '除權息', 'source': 'TWT49U-202609.json.gz'}]
        missing = json.dumps({'stat': '無相關資料'}, ensure_ascii=False).encode('utf-8')
        fake = Fake({'TWT49UDetail:2889:20260901': missing, 'TWT49UDetail:2464:20260902': detail([ROW_2464_DETAIL])})
        summary = self.refresh(fake, listed)
        self.assertIsNone(summary['stopped'])
        self.assertEqual(summary['missing_details'], ['2889|2026-09-01'])
        self.assertEqual(load(self.path, self.seed)['cash_dividends'], {'2464|2026-09-02': 0.471403})

    def test_a_refused_request_stops_at_once_and_keeps_what_was_already_gathered(self):
        # 官方站回 403／驗證頁時不再往下打；已取得的部分存檔，上一版其餘內容原樣保留。
        self.refresh(Fake())
        listed = [{'date': '2026-09-02', 'code': '2464', 'type': '除權息', 'source': 'TWT49U-202609.json.gz'}]
        blocked = Fake({'TWTCAU': twse(TWTCAU_FIELDS, [ROW_0050, ROW_00632R]),
                        'TWT49UDetail:2464:20260902': detail([ROW_2464_DETAIL]),
                        'revivt': Response(403, 'text/html', b'<html>forbidden</html>', 'https://www.tpex.org.tw/')})
        summary = self.refresh(blocked, listed)
        self.assertIn('HTTP 403', summary['stopped'])
        self.assertEqual(blocked.timeline, ['TWTCAU', 3.0, 'TWT49UDetail:2464:20260902', 5.0, 'revivt'])
        saved = load(self.path, self.seed)
        self.assertEqual([(e['code'], e['source']) for e in saved['events']],
                         [('3555', 'TPEx-revivt'), ('6613', 'TPEx-pvChgRslt'), ('00632R', 'TWSE-TWTCAU'), ('0050', 'TWSE-TWTCAU')])
        self.assertEqual(saved['cash_dividends'], {'2464|2026-09-02': 0.471403})

    def test_an_empty_answer_for_a_table_we_already_have_events_from_is_not_trusted(self):
        # 官方站異常時也會回「沒有符合條件的資料」。拿它清掉已有的事件，斷崖會無聲地回來。
        self.refresh(Fake())
        emptied = Fake({'TWTCAU': EMPTY_TWSE})
        summary = self.refresh(emptied)
        self.assertIn('TWSE-TWTCAU', summary['stopped'])
        self.assertEqual(emptied.timeline, ['TWTCAU'])
        self.assertIn('0050', [e['code'] for e in load(self.path, self.seed)['events']])

    def test_progress_survives_being_killed_midway_through_a_long_backfill(self):
        listed = [{'date': f'2026-09-0{n}', 'code': '2464', 'type': '除權息', 'source': 'TWT49U-202609.json.gz'}
                  for n in (1, 2, 3)]

        class Killed(BaseException):
            pass

        fake = Fake({f'TWT49UDetail:2464:2026090{n}': detail([ROW_2464_DETAIL]) for n in (1, 2)})
        fake.responses['TWT49UDetail:2464:20260903'] = None

        def get(url, params):
            if params.get('T1') == '20260903':
                raise Killed()
            return fake.get(url, params)

        with unittest.mock.patch('capital_supplement.SAVE_EVERY', 2), self.assertRaises(Killed):
            refresh(self.path, listed, '2021-08-01', '2026-11-14', seed=self.seed, get=get, sleep=fake.sleep)
        self.assertEqual(sorted(load(self.path, self.seed)['cash_dividends']), ['2464|2026-09-01', '2464|2026-09-02'])

    def test_tpex_cash_dividends_are_taken_from_the_raw_files_already_in_the_working_set(self):
        import otc_corporate_actions as oca
        from test_otc_corporate_actions import COMMON_BOTH, _Response, payload
        raw = Path(self.tmp.name) / 'TPEx-exDailyQ'
        oca.fetch_periods(raw, [('2025', '2025-01-01', '2025-12-31')],
                          get=lambda *_: _Response(payload(COMMON_BOTH)), sleep=lambda _: None)
        fake = Fake()
        summary = refresh(self.path, [], '2026-06-01', '2026-11-14', seed=self.seed, get=fake.get, sleep=fake.sleep,
                          otc_raw_dir=raw)
        self.assertEqual(summary['requested'], 4)  # 不為櫃買現金股利多打任何請求
        self.assertEqual(load(self.path, self.seed)['cash_dividends'], {'6488|2025-07-01': 10.0})

    def test_only_the_requested_window_of_each_table_is_replaced(self):
        self.refresh(Fake())  # 先有 2021～2026 的 0050、3555、6613
        later = Fake({'TWTCAU': twse(TWTCAU_FIELDS, [['115/07/07', '00685L', '群益臺灣加權正2', '分割', '306.00', '12.75', '15.30', '10.20', '12.75']]),
                      'revivt': tpex(REVIVT_FIELDS, []), 'pvChgRslt': tpex(PVCHG_FIELDS, [])})
        self.refresh(later, start='2026-06-01', end='2026-11-14')
        self.assertEqual([(e['code'], e['date']) for e in load(self.path, self.seed)['events']],
                         [('3555', '2021-10-06'), ('6613', '2022-08-29'), ('0050', '2025-06-18'), ('00685L', '2026-07-07')])

    def test_first_run_starts_from_the_seed_shipped_with_the_code(self):
        self.seed.write_text(json.dumps({'schema': 'capital-supplement/v1', 'events': [
            {'date': '2020-01-02', 'code': '9999', 'type': '分割', 'close_before': 100.0, 'reference': 50.0, 'source': 'TWSE-TWTCAU'}],
            'cash_dividends': {'1101|2022-07-21': 1.0}}), encoding='utf-8')
        self.refresh(Fake(), start='2026-06-01', end='2026-11-14')
        saved = load(self.path, self.seed)
        self.assertIn(('9999', '2020-01-02'), [(e['code'], e['date']) for e in saved['events']])
        self.assertEqual(saved['cash_dividends'], {'1101|2022-07-21': 1.0})


if __name__ == '__main__':
    unittest.main()
