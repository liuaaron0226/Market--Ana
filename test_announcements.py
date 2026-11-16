"""公司重大訊息（個股頁事實區塊 D 票；規格 .scratch/stock-facts/spec.md）：抓官方當日快照、原件留存、整理成近 90 天。"""
import json
import tempfile
import unittest
from pathlib import Path

import announcements as an

HEADER = '出表日期,發言日期,發言時間,公司代號,公司名稱,主旨 ,符合條款,事實發生日,說明\n'


def csv_bytes(*rows: str, header: str = HEADER) -> bytes:
    return ('﻿' + header + ''.join(row + '\n' for row in rows)).encode('utf-8')


ROW_2330 = '1151116,1151115,174706,2330,台積電,"公告本公司董事會決議\r\n股利分派",第14款,1151115,"1.董事會決議日期:115/11/15\r\n2.內容:略"'
ROW_6488 = '1151116,1151115,70004,6488,環球晶,公告本公司受邀參加法說會,第12款,1151120,說明全文'


class Response:
    def __init__(self, status: int, content: bytes = b''):
        self.status, self.content = status, content


class ParseTests(unittest.TestCase):
    def test_rows_use_western_dates_and_clock_times(self):
        rows = an.parse(csv_bytes(ROW_2330, ROW_6488), market='上市')
        self.assertEqual(rows[0], {
            'market': '上市', 'code': '2330', 'name': '台積電', 'date': '2026-11-15', 'time': '17:47:06',
            'subject': '公告本公司董事會決議股利分派', 'clause': '第14款', 'fact_date': '2026-11-15',
            'body': '1.董事會決議日期:115/11/15\n2.內容:略'})
        self.assertEqual((rows[1]['time'], rows[1]['fact_date']), ('07:00:04', '2026-11-20'))

    def test_an_empty_day_is_no_rows_not_an_error(self):
        self.assertEqual(an.parse(csv_bytes(), market='上櫃'), [])

    def test_a_changed_format_stops_instead_of_guessing(self):
        with self.assertRaises(an.FormatChanged):
            an.parse(csv_bytes(ROW_2330, header='出表日期,發言日期,公司代號,主旨\n'), market='上市')
        with self.assertRaises(an.FormatChanged):
            an.parse(b'<html>maintenance</html>', market='上市')


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / 'raw'

    def fetch(self, responses):
        calls = []

        def get(url, params=None):
            calls.append(url)
            return responses[url]
        return an.fetch(self.raw, get=get, now='2026-11-16T03:16:19+00:00'), calls

    def test_saves_both_markets_as_raw_files_with_their_hash(self):
        result, calls = self.fetch({an.SOURCES['上市']: Response(200, csv_bytes(ROW_2330)),
                                    an.SOURCES['上櫃']: Response(200, csv_bytes(ROW_6488))})
        self.assertEqual(calls, [an.SOURCES['上市'], an.SOURCES['上櫃']])
        self.assertEqual({k: v['status'] for k, v in result.items()}, {'上市': 'saved', '上櫃': 'saved'})
        files = sorted(p.name for p in self.raw.glob('*.csv'))
        self.assertEqual(len(files), 2)
        meta = json.loads(next(self.raw.glob('listed-*.json')).read_text(encoding='utf-8'))
        self.assertEqual((meta['source_url'], meta['fetched_at_utc'], meta['rows']), (an.SOURCES['上市'], '2026-11-16T03:16:19+00:00', 1))
        self.assertEqual(len(meta['sha256']), 64)

    def test_the_same_snapshot_again_is_not_saved_twice(self):
        responses = {an.SOURCES['上市']: Response(200, csv_bytes(ROW_2330)), an.SOURCES['上櫃']: Response(200, csv_bytes())}
        self.fetch(responses)
        result, _ = self.fetch(responses)
        self.assertEqual({k: v['status'] for k, v in result.items()}, {'上市': 'already-present', '上櫃': 'already-present'})
        self.assertEqual(len(list(self.raw.glob('*.csv'))), 2)

    def test_one_market_failing_does_not_stop_the_other_and_writes_nothing_for_it(self):
        result, _ = self.fetch({an.SOURCES['上市']: Response(503), an.SOURCES['上櫃']: Response(200, b'<html>x</html>')})
        self.assertEqual(result['上市'], {'status': 'stopped', 'reason': 'HTTP 503'})
        self.assertEqual(result['上櫃']['status'], 'stopped')
        self.assertEqual(list(self.raw.glob('*')), [])


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name) / 'raw'

    def save(self, market, *rows):
        responses = {an.SOURCES[m]: Response(200, csv_bytes(*(rows if m == market else ()))) for m in an.SOURCES}
        an.fetch(self.raw, get=lambda url, params=None: responses[url], now='2026-11-16T03:00:00+00:00')

    def test_last_90_days_inside_the_universe_newest_first_without_duplicates(self):
        old = '1150818,1150817,90000,2330,台積電,太舊的公告,第51款,1150817,x'              # 2026-08-17：超過 90 天
        edge = '1150818,1150818,90000,2330,台積電,剛好 90 天前,第51款,1150818,x'           # 2026-08-18：剛好 90 天
        later = '1151116,1151115,180000,2330,台積電,同一天晚一點,第51款,1151115,x'
        outside = '1151116,1151115,90000,9999,不在範圍,範圍外的公告,第51款,1151115,x'
        self.save('上市', old, edge, ROW_2330, outside)
        self.save('上市', ROW_2330, later)          # 隔天的快照又出現同一則 → 只算一則
        self.save('上櫃', ROW_6488)
        rows = an.collect(self.raw, as_of='2026-11-16', codes={'2330', '6488'})
        self.assertEqual([(r['code'], r['date'], r['time'], r['subject']) for r in rows], [
            ('2330', '2026-11-15', '18:00:00', '同一天晚一點'),
            ('2330', '2026-11-15', '17:47:06', '公告本公司董事會決議股利分派'),
            ('6488', '2026-11-15', '07:00:04', '公告本公司受邀參加法說會'),
            ('2330', '2026-08-18', '09:00:00', '剛好 90 天前'),
        ])

    def test_a_correction_is_its_own_announcement(self):
        correction = '1151116,1151115,174706,2330,台積電,更正公告本公司董事會決議,第14款,1151115,x'
        self.save('上市', ROW_2330, correction)
        self.assertEqual(len(an.collect(self.raw, as_of='2026-11-16', codes={'2330'})), 2)

    def test_announcements_after_the_data_day_are_left_out(self):
        self.save('上市', ROW_2330)
        self.assertEqual(an.collect(self.raw, as_of='2026-11-14', codes={'2330'}), [])


if __name__ == '__main__':
    unittest.main()
