"""個股頁事實區塊 D 票：建置版本時把重大訊息整理成每檔一列、事實資料加上產業別與「消息」。

用法：PYTHONPATH=. python -m unittest cloud.test.test_stock_facts_build
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import announcements as an
from cloud import build_stock_pack as build
from twon_analysis.facts import with_news

HEADER = '出表日期,發言日期,發言時間,公司代號,公司名稱,主旨,符合條款,事實發生日,說明\n'


class Response:
    def __init__(self, content: bytes):
        self.status, self.content = 200, content


def save(raw: Path, *rows: str) -> None:
    body = ('﻿' + HEADER + ''.join(r + '\n' for r in rows)).encode('utf-8')
    empty = ('﻿' + HEADER).encode('utf-8')
    responses = {an.SOURCES['上市']: Response(body), an.SOURCES['上櫃']: Response(empty)}
    an.fetch(raw, get=lambda url, params=None: responses[url], now='2026-11-16T03:00:00+00:00')


def item(code: str, date: str, time: str, subject: str, body: str = '說明') -> dict:
    return {'market': '上市', 'code': code, 'name': '測試', 'date': date, 'time': time, 'subject': subject,
            'clause': '第51款', 'fact_date': date, 'body': body}


class SeedRowsTest(unittest.TestCase):
    def test_one_row_per_code_newest_first(self):
        rows = an.seed_rows([item('2330', '2026-11-15', '18:00:00', '晚'), item('1101', '2026-11-14', '09:00:00', '水泥'),
                             item('2330', '2026-11-15', '09:00:00', '早')], as_of='2026-11-16')
        self.assertEqual([row['code'] for row in rows], ['1101', '2330'])
        self.assertEqual(rows[1]['as_of'], '2026-11-16')
        self.assertEqual(rows[1]['payload']['schema'], 'stock-announcements/v1')
        self.assertEqual([i['subject'] for i in rows[1]['payload']['items']], ['晚', '早'])

    def test_still_too_big_without_bodies_drops_the_oldest_items_so_publishing_never_fails(self):
        # 說明全文都拿掉還放不下（例如替很多子公司發公告的金控）：再從最舊的整則拿掉，最新的一定留著。
        items = [item('2880', f'2026-11-{d:02d}', '09:00:00', '主旨' * 40, body=None) for d in range(30, 0, -1)]
        payload = an.seed_rows(items, as_of='2026-11-30', max_bytes=3_000)[0]['payload']
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')), 3_000)
        self.assertEqual(payload['items'][0]['date'], '2026-11-30')
        self.assertLess(len(payload['items']), 30)
        self.assertEqual([i['date'] for i in payload['items']], sorted((i['date'] for i in payload['items']), reverse=True))

    def test_a_list_too_big_for_one_row_drops_the_oldest_bodies_first_but_keeps_every_subject(self):
        # 匯入程式單列上限 90KB：清單太大時，從最舊的那則開始拿掉說明全文，主旨與時間都留著。
        items = [item('2330', f'2026-11-{d:02d}', '09:00:00', f'第{d}則', body='字' * 2000) for d in range(15, 5, -1)]
        payload = an.seed_rows(items, as_of='2026-11-16', max_bytes=12_000)[0]['payload']
        self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')), 12_000)
        self.assertEqual(len(payload['items']), 10)
        bodies = [i['body'] for i in payload['items']]
        self.assertIsNotNone(bodies[0])            # 最新的保留全文
        self.assertIsNone(bodies[-1])              # 最舊的先拿掉
        self.assertEqual(bodies, sorted(bodies, key=lambda b: b is None))   # 有全文的都在沒有的前面


class WithNewsTest(unittest.TestCase):
    def test_industry_and_the_news_button_before_technical(self):
        facts = {'blocks': ['summary', 'institutional', 'fundamentals', 'technical'], 'today': []}
        out = with_news(facts, industry='半導體業')
        self.assertEqual(out['industry'], '半導體業')
        self.assertEqual(out['blocks'], ['summary', 'institutional', 'fundamentals', 'news', 'technical'])

    def test_no_industry_means_no_news_button(self):
        facts = {'blocks': ['summary', 'technical']}
        self.assertEqual(with_news(facts, industry=None)['blocks'], ['summary', 'technical'])
        self.assertIsNone(with_news(None, industry='水泥工業'))


class AttachTest(unittest.TestCase):
    def test_announcements_inside_the_universe_and_news_on_every_stock_with_an_industry(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'raw'
            save(raw, '1151116,1151115,174706,2330,台積電,董事會決議,第14款,1151115,說明',
                 '1151116,1151115,90000,9999,範圍外,範圍外的公告,第51款,1151115,x')
            securities = [{'code': '2330', 'industry': '半導體業'}, {'code': '1101', 'industry': '水泥工業'}]
            analyses = [{'code': '2330', 'as_of': '2026-11-16', 'payload': {'facts': {'blocks': ['summary', 'technical']}}},
                        {'code': '1101', 'as_of': '2026-11-16', 'payload': {'facts': {'blocks': ['technical']}}},
                        {'code': '0050', 'as_of': '2026-11-16', 'payload': {'facts': {'blocks': ['technical']}}}]
            rows = build.attach_news(securities, analyses, data_date='2026-11-16', raw_dir=raw)
        self.assertEqual([row['code'] for row in rows], ['2330'])
        self.assertEqual(rows[0]['payload']['items'][0]['subject'], '董事會決議')
        self.assertEqual(analyses[0]['payload']['facts']['blocks'], ['summary', 'news', 'technical'])
        self.assertEqual(analyses[1]['payload']['facts']['industry'], '水泥工業')   # 沒有公告也有產業別與「消息」
        self.assertEqual(analyses[2]['payload']['facts']['blocks'], ['technical'])   # 0050 不在名錄、沒有產業別

    def test_unreadable_raw_files_leave_the_analyses_and_give_no_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp) / 'raw'
            raw.mkdir()
            (raw / 'listed-20261116-bad.json').write_text('{"market": "上市"}', encoding='utf-8')
            (raw / 'listed-20261116-bad.csv').write_bytes(b'<html>broken</html>')
            analyses = [{'code': '2330', 'as_of': '2026-11-16', 'payload': {'facts': {'blocks': ['technical']}}}]
            skipped = []
            rows = build.attach_news([{'code': '2330', 'industry': '半導體業'}], analyses, data_date='2026-11-16', raw_dir=raw,
                                     skipped=skipped)
        self.assertEqual(rows, [])
        self.assertEqual([name.split(':')[0] for name in skipped], ['listed-20261116-bad.csv'])
        self.assertEqual(analyses[0]['payload']['facts']['blocks'], ['news', 'technical'])


if __name__ == '__main__':
    unittest.main()
