"""用研究的 8 檔原站樣本（2026-10-16）對照本站重寫結果。

原站樣本只讀、不搬程式；本站門檻是 2TON-FIT-V1 自有規則，handoff 已記錄與原站不同之處：
T05 原站 8 檔全是 confirming（含 RSI 20.7）、V02 原站 5 檔都 confirming —— 這兩招的差異是預期內，其餘必須一致。
"""
import glob
import json
import unittest
from pathlib import Path

from twon_analysis.conditions import trend_and_volume
from twon_analysis.data import DEFAULT_DB, load_bars
from twon_analysis.indicators import indicators
from twon_analysis.scores import trend_score, volume_score

RESEARCH = Path('C:/Users/User/Desktop/claude/.scratch/2tonstock-research/2026-10-18/deep')
KNOWN_DIFFERENT = {'T05', 'V02'}
PRICE_VOLUME_IDS = ['T01', 'T02', 'T03', 'T04', 'T05', 'T06', 'V01', 'V02', 'V03', 'V04', 'V05', 'V06']


def samples():
    files = sorted(glob.glob(str(RESEARCH / 'training/*-decoded.json'))) + sorted(glob.glob(str(RESEARCH / 'holdout/*-decoded.json')))
    return [json.loads(Path(f).read_text(encoding='utf-8'))['analysis'] for f in files]


@unittest.skipUnless(RESEARCH.exists() and DEFAULT_DB.exists(), '研究樣本或票 01 資料庫不在本機')
class ResearchFixtureTests(unittest.TestCase):
    def test_eight_research_samples_exist(self):
        self.assertEqual(len(samples()), 8)

    def test_price_volume_statuses_match_except_documented_rules(self):
        for analysis in samples():
            with self.subTest(analysis['symbol']):
                bars, info = load_bars(analysis['symbol'], '2026-10-16')
                self.assertEqual(bars[-1].close, analysis['quote']['close'])
                original = {s['strategy_id']: s['status'] for s in analysis['derived']['strategies']}
                mine = {r['id']: r['status'] for r in trend_and_volume(indicators(bars), info['chg'], len(bars))}
                diff = {sid for sid in PRICE_VOLUME_IDS if original[sid] != mine[sid]}
                self.assertLessEqual(diff, KNOWN_DIFFERENT, f'{analysis["symbol"]} 非預期差異：{diff - KNOWN_DIFFERENT}')

    def test_trend_score_matches_and_volume_score_is_within_four_points(self):
        for analysis in samples():
            with self.subTest(analysis['symbol']):
                dims = {d['key']: d['score'] for d in analysis['derived']['dimensions']}
                bars, info = load_bars(analysis['symbol'], '2026-10-16')
                x = indicators(bars)
                self.assertEqual(trend_score(x), dims['trend'])
                self.assertLessEqual(abs(volume_score(x, info['chg']) - dims['volume']), 4)


@unittest.skipUnless(RESEARCH.exists() and DEFAULT_DB.exists(), '研究樣本或票 01 資料庫不在本機')
class PriceStructureFixtureTests(unittest.TestCase):
    def structure_of(self, code):
        from twon_analysis.price_structure import structure
        bars, _ = load_bars(code, '2026-10-16')
        return structure(bars, indicators(bars))

    def test_handoff_acceptance_targets(self):
        # handoff §8.7：長榮 2603 第一壓力 268.5、容差 1.84；台積電支撐 2415、2395
        evergreen = self.structure_of('2603')
        self.assertEqual(evergreen['tolerance_pct'], 1.84)
        self.assertEqual(evergreen['pressure']['first']['tick'], 268.5)
        tsmc = self.structure_of('2330')
        self.assertEqual((tsmc['support']['first']['tick'], tsmc['support']['second']['tick']), (2415.0, 2395.0))

    def test_first_pressure_cluster_is_within_two_percent_of_the_original(self):
        for analysis in samples():
            with self.subTest(analysis['symbol']):
                original = analysis['derived']['priceStructure']['pressure']['clusters'][0]['level']
                mine = self.structure_of(analysis['symbol'])['pressure']['all'][0]['level']
                self.assertLess(abs(mine / original - 1), 0.02)


if __name__ == '__main__':
    unittest.main()
