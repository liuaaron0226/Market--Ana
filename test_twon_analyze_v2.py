"""個股分析 v2 契約測試（S1）：只留原站重現項，未公開項用固定表示法，不含自創計分與原站命名。

契約：.scratch/chart-screening-workbench/stock-analysis-contract-v2.md
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from twon_analysis.data import DEFAULT_DB, DEFAULT_OTC_DB
from twon_analysis.indicators import Bar

TOP_KEYS = {'schema', 'analysis_version', 'code', 'name', 'kind', 'market', 'as_of', 'requested_as_of', 'quote', 'qa',
            'indicators', 'levels', 'defense', 'trend_classification', 'conditions', 'trading_rules', 'reference',
            'undisclosed', 'provenance', 'bars', 'chart', 'labels', 'strategy', 'institutional'}
BLOCKING_KEYS = {'schema', 'analysis_version', 'code', 'as_of', 'requested_as_of', 'qa', 'trading_rules', 'undisclosed',
                 'provenance', 'labels'}
INDICATOR_KEYS = {'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma240', 'rsi14', 'atr14', 'atr_pct', 'relative_volume',
                  'high20_close', 'low20_close', 'prior_high20', 'low20_intraday', 'k9', 'd9', 'macd_dif', 'macd_dea',
                  'macd_hist'}
LEVEL_KEYS = {'aggregation_tolerance_pct', 'observable_range_pct', 'resistance', 'support', 'resistance_all', 'support_all',
              'no_resistance_above', 'limit_up_reference'}
CLUSTER_KEYS = {'tick', 'level', 'low', 'high', 'distance_pct', 'tier', 'signal_count', 'structural', 'strength_score', 'sources'}
UNDISCLOSED_KEYS = {'observation_zone', 'composite_score', 'condition_thresholds'}
UNDISCLOSED = {'status': 'undisclosed', 'label': '原站未公開'}
CONFIRMED_CONDITIONS = {'ma_trend', 'kd_bullish', 'momentum_overheated', 'near_prior_high', 'volume_confirmed',
                        'heavy_volume_stall', 'revenue_decline', 'securities_lending_sale'}
# round3（360 檔）：V05 量縮整理的留出集降到 94.4%／93.6%，由可當規格改為近似。
APPROXIMATE_CONDITIONS = {'macd_bullish', 'margin_healthy', 'short_interest', 'institutional_reversal', 'valuation_low_pe',
                          'volume_contraction'}
PENDING_CONDITION_COUNT = 24 - len(CONFIRMED_CONDITIONS) - len(APPROXIMATE_CONDITIONS)
SHARED_KINDS = {'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma240', 'prior_high_20', 'high_20', 'high_60', 'intraday_high_60',
                'vp_center', 'vp_high', 'swing_high', 'breakout_failure', 'gap_down_upper', 'gap_down_lower', 'heavy_volume',
                'heavy_high'}
SUPPORT_ONLY_KINDS = {'low_20', 'low_60', 'vp_low', 'swing_low', 'gap_up_upper', 'gap_up_lower', 'heavy_low'}
REMOVED_KEYS = {'score', 'score_complete', 'score_note', 'preset', 'available_presets', 'params_hash', 'formula', 'version',
                'dimensions', 'dimensions_missing', 'dimensions_incomplete', 'coverage', 'trend', 'risk_flags', 'strategies',
                'strategy_counts', 'sweet', 'evidence_levels', 'support1', 'pressure1', 'own_formula', 'range_position',
                'limit_up_ref', 'strength', 'ma20_slope5', 'k', 'd'}
REMOVED_KINDS = {'trendline_resistance', 'trendline_support', 'platform', 'inst_cost'}
BANNED_TEXT = ('2TON', '漲停寶寶', '甜甜價', '甜價', '私藏', 'CP值', '抄底', 'FIT-V1', 'MOMENTUM-V1', 'SWING-SAFE')


def all_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from all_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from all_keys(child)


def clusters(levels):
    return levels['resistance'] + levels['support'] + levels['resistance_all'] + levels['support_all']


def make_db(path: Path, closes: list[float], code: str = '9999') -> Path:
    connection = sqlite3.connect(path)
    connection.execute("""CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, open REAL, high REAL, low REAL,
                          close REAL, change_sign TEXT, change REAL, volume_shares INTEGER, trades INTEGER, turnover INTEGER,
                          suspended INTEGER)""")
    for i, close in enumerate(closes):
        connection.execute("INSERT INTO daily VALUES (?, ?, '測試', 'common', ?, ?, ?, ?, '+', 0.5, 1000, 1, 1, 0)",
                           (f'2026-01-{i + 1:02d}', code, close, close + 1, close - 1, close))
    connection.commit()
    connection.close()
    return path


class StructureV2UnitTests(unittest.TestCase):
    def bars(self, closes):
        return [Bar(f'd{i:03d}', c, c + 1, c - 1, c, 1000 + i) for i, c in enumerate(closes)]

    def test_short_history_skips_sixty_day_sources_and_volume_profile(self):
        from twon_analysis.analyze_v2 import price_levels_v2
        from twon_analysis.indicators import indicators
        bars = self.bars([100 + (i % 7) for i in range(40)])
        levels = price_levels_v2(bars, indicators(bars))
        kinds = {s['kind'] for c in clusters(levels) for s in c['sources']}
        self.assertFalse(kinds & {'high_60', 'low_60', 'intraday_high_60', 'vp_center', 'vp_high', 'vp_low'})

    def test_new_high_with_nothing_above_shows_limit_up_reference_not_a_structure(self):
        from twon_analysis.analyze_v2 import price_levels_v2
        from twon_analysis.indicators import indicators
        from twon_analysis.price_structure import tick_floor
        # 一路創高且收在最高價：上方沒有任何結構價位 → 官方說明：改以明日漲停參考價標示（非結構價位）
        bars = [Bar(f'd{i:03d}', 50 + i - 0.5, 50 + i, 50 + i - 1, 50 + i, 1000 + i) for i in range(70)]
        levels = price_levels_v2(bars, indicators(bars))
        self.assertEqual(levels['resistance'], [])
        self.assertTrue(levels['no_resistance_above'])
        reference = levels['limit_up_reference']
        self.assertEqual(reference['value'], tick_floor(bars[-1].close * 1.10))
        self.assertIn('非結構價位', reference['note'])

    def test_limit_up_reference_is_absent_when_a_structure_exists_above(self):
        from twon_analysis.analyze_v2 import price_levels_v2
        from twon_analysis.indicators import indicators
        bars = self.bars([100 + (i % 9) for i in range(80)])
        levels = price_levels_v2(bars, indicators(bars))
        self.assertFalse(levels['no_resistance_above'])
        self.assertIsNone(levels['limit_up_reference'])

    def test_observable_range_is_four_atr_pct_clamped_eight_to_twenty(self):
        from twon_analysis.analyze_v2 import observable_range_pct
        self.assertEqual(observable_range_pct(2.61), 10.44)
        self.assertEqual(observable_range_pct(1.0), 8.0)
        self.assertEqual(observable_range_pct(11.51), 20.0)
        self.assertIsNone(observable_range_pct(None))

    def test_first_and_second_resistance_selection(self):
        from twon_analysis.analyze_v2 import select_resistances

        def c(level, signals, distance):
            return {'level': level, 'signal_count': signals, 'distance_pct': distance}
        # 第一壓力：可觀察範圍內、來源 ≥2 的最低群；單一來源不算
        # 第二壓力：之後第一個來源 ≥2、且與第一壓力相距 ≥ 2×容差的群；超出可觀察範圍的不選
        clusters = [c(101, 1, 1.0), c(102, 2, 2.0), c(104, 3, 4.0), c(108, 2, 8.0), c(130, 5, 30.0)]
        first, second = select_resistances(clusters, tolerance_pct=2.0, range_pct=20.0)
        self.assertEqual((first['level'], second['level']), (102, 108))
        first, second = select_resistances(clusters, tolerance_pct=2.0, range_pct=6.0)
        self.assertEqual(first['level'], 102)
        self.assertIsNone(second)
        self.assertEqual(select_resistances([c(101, 1, 1.0)], tolerance_pct=2.0, range_pct=20.0), (None, None))


BASE_INPUTS = {'close': 100.0, 'prev_close': 99.0, 'ma5': 99.5, 'ma20': 98.0, 'ma60': 97.0, 'macd_hist': 0.2, 'macd_dif': 1.0,
               'macd_dea': 0.8, 'k9': 60.0, 'd9': 55.0, 'rsi14': 60.0, 'high20_close': 101.0, 'relative_volume': 1.3,
               'revenue_yoy_pct': 12.0, 'borrow_sold': 0, 'margin_change5': -10, 'short_balance': 5, 'inst_total_net': 100,
               'inst_total_net5': 300, 'pe': 12.0}


class ConditionRuleTests(unittest.TestCase):
    def states(self, **override):
        from twon_analysis.analyze_v2 import condition_states
        result = condition_states({**BASE_INPUTS, **override})
        return {k: v['state'] for group in ('confirmed', 'approximate') for k, v in result[group].items()}

    def test_groups_and_counts(self):
        from twon_analysis.analyze_v2 import condition_states
        result = condition_states(BASE_INPUTS)
        self.assertEqual(set(result['confirmed']), CONFIRMED_CONDITIONS)
        self.assertEqual(set(result['approximate']), APPROXIMATE_CONDITIONS)
        self.assertEqual(result['pending']['count'], PENDING_CONDITION_COUNT)
        self.assertEqual(result['pending']['status'], 'pending_reverse_engineering')

    def test_baseline_states(self):
        self.assertEqual(self.states(), {
            'ma_trend': 'triggered', 'kd_bullish': 'triggered', 'momentum_overheated': 'confirming',
            'near_prior_high': 'triggered', 'volume_confirmed': 'triggered', 'heavy_volume_stall': 'off',
            'volume_contraction': 'off', 'revenue_decline': 'off', 'securities_lending_sale': 'off',
            'macd_bullish': 'triggered', 'margin_healthy': 'triggered', 'short_interest': 'triggered',
            'institutional_reversal': 'off', 'valuation_low_pe': 'triggered'})

    def test_ma_trend_three_states(self):
        self.assertEqual(self.states(close=99.0, ma5=99.5, ma20=98.5)['ma_trend'], 'triggered')       # 只要站上 MA20 即成立
        self.assertEqual(self.states(close=95.0, ma5=99.5, ma20=98.0, ma60=97.0)['ma_trend'], 'confirming')
        self.assertEqual(self.states(close=95.0, ma5=99.5, ma20=96.0, ma60=97.0)['ma_trend'], 'off')

    def test_near_prior_high_uses_098(self):
        self.assertEqual(self.states(close=98.98, high20_close=101.0)['near_prior_high'], 'triggered')
        self.assertEqual(self.states(close=98.97, high20_close=101.0)['near_prior_high'], 'off')

    def test_momentum_overheated_is_confirming_unless_hot(self):
        self.assertEqual(self.states(k9=85.0)['momentum_overheated'], 'triggered')
        self.assertEqual(self.states(rsi14=75.0)['momentum_overheated'], 'triggered')

    def test_volume_rules(self):
        self.assertEqual(self.states(close=98.0)['volume_confirmed'], 'confirming')                 # 下跌日
        self.assertEqual(self.states(relative_volume=1.0)['volume_confirmed'], 'confirming')        # 量比須 > 1
        self.assertEqual(self.states(relative_volume=2.0, close=99.0)['heavy_volume_stall'], 'triggered')
        self.assertEqual(self.states(relative_volume=2.0)['heavy_volume_stall'], 'off')             # 上漲日
        self.assertEqual(self.states(relative_volume=0.69)['volume_contraction'], 'triggered')

    def test_revenue_lending_and_approximates(self):
        self.assertEqual(self.states(revenue_yoy_pct=-20.0)['revenue_decline'], 'triggered')
        self.assertEqual(self.states(borrow_sold=1)['securities_lending_sale'], 'triggered')
        self.assertEqual(self.states(macd_hist=0.0)['macd_bullish'], 'off')
        self.assertEqual(self.states(close=98.0)['margin_healthy'], 'off')
        self.assertEqual(self.states(short_balance=0)['short_interest'], 'off')
        self.assertEqual(self.states(inst_total_net=-1)['institutional_reversal'], 'triggered')
        self.assertEqual(self.states(pe=0.0)['valuation_low_pe'], 'off')
        self.assertEqual(self.states(pe=15.01)['valuation_low_pe'], 'off')

    def test_missing_input_gives_null_state_not_off(self):
        from twon_analysis.analyze_v2 import condition_states
        result = condition_states({k: None for k in BASE_INPUTS})
        for group in ('confirmed', 'approximate'):
            self.assertTrue(all(v['state'] is None for v in result[group].values()), group)

    def test_each_condition_carries_definition_rule_and_match_rates(self):
        from twon_analysis.analyze_v2 import condition_states
        result = condition_states(BASE_INPUTS)
        for group in ('confirmed', 'approximate'):
            for key, value in result[group].items():
                self.assertTrue(value['name'] and value['definition'] and value['rule'], key)
                self.assertRegex(value['verified'], r'\d', key)
        self.assertIn('近似', result['approximate']['macd_bullish']['label'])

    def test_volume_contraction_is_approximate_with_round3_rate(self):
        from twon_analysis.analyze_v2 import condition_states
        rule = condition_states(BASE_INPUTS)['approximate']['volume_contraction']
        self.assertIn('近似', rule['label'])
        self.assertIn('94.4%', rule['verified'])
        self.assertIn('93.6%', rule['verified'])


class TrendAndDefenseTests(unittest.TestCase):
    def test_trend_classification_follows_reference_fallback_function(self):
        from twon_analysis.analyze_v2 import classify_trend
        self.assertEqual(classify_trend(35, 0, 50), 'risk')
        self.assertEqual(classify_trend(60, 2, 50), 'risk')
        self.assertEqual(classify_trend(44, 0, 25), 'risk')
        self.assertEqual(classify_trend(75, 0, 50), 'strong')
        self.assertEqual(classify_trend(44, 0, 26), 'weak')
        self.assertEqual(classify_trend(60, 1, 50), 'watch')

    def test_trend_classification_waits_for_composite_score(self):
        from twon_analysis.analyze_v2 import trend_classification_payload
        payload = trend_classification_payload()
        self.assertEqual(payload['status'], 'pending_composite_score')
        self.assertEqual(payload['label'], '待總分')
        self.assertIsNone(payload['value'])

    def test_defense_without_zone_is_twenty_day_intraday_low(self):
        from twon_analysis.analyze_v2 import defense_payload
        payload = defense_payload(low20_intraday=58.9)
        self.assertEqual(payload['value_if_zone_not_formed'], 58.9)
        self.assertEqual(payload['status'], 'pending_observation_zone')
        self.assertEqual(payload['label'], '待觀察區')
        self.assertIn('1.5', payload['rule'])
        self.assertIn('198/198', payload['verified'])

    def test_trading_rules_are_declared_not_provided_by_the_source(self):
        from twon_analysis.analyze_v2 import TRADING_RULES
        self.assertEqual(TRADING_RULES['status'], 'not_provided_by_source')
        self.assertIn('不是買賣建議', TRADING_RULES['quote'])


class LegacyUnitTests(unittest.TestCase):
    def bars(self, closes):
        return [Bar(f'd{i:03d}', c, c + 1, c - 1, c, 1000 + i) for i, c in enumerate(closes)]

    def test_missing_atr_returns_no_levels_instead_of_a_default(self):
        from twon_analysis.analyze_v2 import price_levels_v2
        bars = self.bars([100.0] * 25)
        levels = price_levels_v2(bars, {'atr_pct': None, 'close': 100.0, **{f'ma{n}': None for n in (5, 10, 20, 60, 120)},
                                        'prior_high20': None, 'low20_intraday': None})
        self.assertIsNone(levels['aggregation_tolerance_pct'])
        self.assertEqual(clusters(levels), [])

    def test_flat_window_rsi_is_null_not_fifty(self):
        from twon_analysis.analyze_v2 import indicators_v2
        bars = self.bars([100.0] * 30)
        self.assertIsNone(indicators_v2(bars)['rsi14'])

    def test_prior_high20_needs_twenty_prior_bars(self):
        from twon_analysis.analyze_v2 import indicators_v2
        self.assertIsNone(indicators_v2(self.bars([100 + i for i in range(20)]))['prior_high20'])
        self.assertIsNotNone(indicators_v2(self.bars([100 + i for i in range(21)]))['prior_high20'])

    def test_legacy_structure_keeps_its_keys_for_existing_callers(self):
        from twon_analysis.indicators import indicators
        from twon_analysis.price_structure import structure
        bars = self.bars([100 + (i % 9) for i in range(80)])
        legacy = structure(bars, indicators(bars))
        self.assertTrue({'pressure', 'support', 'sweet', 'defense', 'range_position', 'tolerance_pct'} <= set(legacy))


class BlockingTests(unittest.TestCase):
    def test_insufficient_history_blocks_with_minimal_payload(self):
        from twon_analysis.analyze_v2 import analyze_v2
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(Path(tmp) / 'h.sqlite', [100.0 + i for i in range(10)])
            result = analyze_v2('9999', '2026-01-31', db_path=db, otc_db_path=None)
        self.assertEqual(set(result), BLOCKING_KEYS)
        self.assertTrue(result['qa']['blocking'])
        self.assertEqual(result['undisclosed'], {k: UNDISCLOSED for k in UNDISCLOSED_KEYS})

    def test_unknown_code_is_not_found(self):
        from twon_analysis.analyze_v2 import analyze_v2
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(Path(tmp) / 'h.sqlite', [100.0] * 30)
            result = analyze_v2('8888', '2026-01-31', db_path=db, otc_db_path=None)
        self.assertTrue(result['qa']['not_found'])
        self.assertEqual(set(result), BLOCKING_KEYS)


@unittest.skipUnless(DEFAULT_DB.exists() and DEFAULT_OTC_DB.exists(), '本機行情庫不在')
class RealDataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from twon_analysis.analyze_v2 import analyze_v2
        cls.listed = analyze_v2('2330', '2026-11-06')
        cls.otc = analyze_v2('6488', '2026-11-06')
        cls.etf = analyze_v2('0050', '2026-11-06')

    def test_payload_matches_the_frozen_contract(self):
        for result in (self.listed, self.otc, self.etf):
            with self.subTest(result['code']):
                self.assertEqual(set(result), TOP_KEYS)
                self.assertEqual(result['schema'], 'stock-analysis/v2')
                self.assertEqual(set(result['indicators']), INDICATOR_KEYS)
                self.assertEqual(set(result['levels']), LEVEL_KEYS)
                self.assertEqual(result['undisclosed'], {k: UNDISCLOSED for k in UNDISCLOSED_KEYS})
                self.assertLessEqual(len(result['levels']['resistance']), 2)
                self.assertLessEqual(len(result['levels']['support']), 2)
                for cluster in clusters(result['levels']):
                    self.assertEqual(set(cluster), CLUSTER_KEYS)
                    self.assertIn(cluster['tier'], {'near', 'mid', 'far'})

    def test_markets_are_labelled(self):
        self.assertEqual(self.listed['market'], '上市')
        self.assertEqual(self.otc['market'], '上櫃')

    def test_no_removed_fields_or_own_scoring_survive(self):
        for result in (self.listed, self.otc, self.etf):
            with self.subTest(result['code']):
                # chart 是票01的 MACD 投影（parameters.version 是 MACD 序列版本，不是計分版本），單獨核對。
                body = {k: v for k, v in result.items() if k != 'chart'}
                self.assertEqual(set(all_keys(body)) & REMOVED_KEYS, set())
                self.assertEqual(set(result['chart']), {'parameters', 'macd'})

    def test_resistance_uses_only_disclosed_weight_sources_and_no_removed_kinds(self):
        for result in (self.listed, self.otc):
            levels = result['levels']
            for cluster in levels['resistance_all']:
                kinds = {s['kind'] for s in cluster['sources']}
                self.assertLessEqual(kinds, SHARED_KINDS, result['code'])
                self.assertIsNotNone(cluster['strength_score'])
            for cluster in levels['support_all']:
                kinds = {s['kind'] for s in cluster['sources']}
                self.assertLessEqual(kinds, SHARED_KINDS | SUPPORT_ONLY_KINDS)
                self.assertEqual(cluster['strength_score'] is None, bool(kinds & SUPPORT_ONLY_KINDS))
            all_kinds = {s['kind'] for c in clusters(levels) for s in c['sources']}
            self.assertEqual(all_kinds & REMOVED_KINDS, set())

    def test_support_ticks_stay_below_and_resistance_above_close(self):
        for result in (self.listed, self.otc):
            close = result['quote']['close']
            for cluster in result['levels']['resistance']:
                self.assertGreater(cluster['tick'], close)
            for cluster in result['levels']['support']:
                self.assertLess(cluster['tick'], close)

    def test_no_original_site_naming_in_any_text(self):
        import re
        for result in (self.listed, self.otc, self.etf):
            dumped = json.dumps(result, ensure_ascii=False)
            for word in BANNED_TEXT:
                self.assertNotIn(word, dumped, f'{result["code"]} 出現 {word}')
            self.assertIsNone(re.search(r'"[TCVIF]0[1-6]"', dumped), f'{result["code"]} 出現原站招式 ID')

    def test_chart_macd_projection_is_kept_for_ticket01(self):
        self.assertEqual(self.listed['chart']['parameters']['version'], 'macd-first-close-v1')
        self.assertEqual(len(self.listed['chart']['macd']), 120)
        self.assertEqual(self.listed['provenance']['chart']['level'], 'generic_indicator')

    def test_strategy_and_three_institutional_series_are_in_the_contract(self):
        for result in (self.listed, self.otc):
            with self.subTest(result['code']):
                self.assertIn(result['strategy']['recommendation'], {'持有', '分批進場', '觀望', '減碼'})
                self.assertEqual(set(result['strategy']['prices']), {'entry', 'add', 'stop', 'target'})
                self.assertEqual(set(result['institutional'][0]), {'date', 'foreign', 'trust', 'dealer', 'total'})
                self.assertEqual([row['date'] for row in result['institutional']], sorted(row['date'] for row in result['institutional']))
                price_ticks = {row['tick'] for row in result['levels']['support_all']} | {row['tick'] for row in result['levels']['resistance']}
                self.assertLessEqual({value for value in result['strategy']['prices'].values() if value is not None}, price_ticks)

    def test_unverified_strategy_failure_leaves_core_analysis_intact(self):
        from unittest import mock
        import twon_analysis.analyze_v2 as module
        with mock.patch.object(module, 'strategy_v1', side_effect=RuntimeError('strategy bug')):
            result = module.analyze_v2('2330', '2026-11-06')
        self.assertIsNone(result['strategy'])
        self.assertFalse(result['qa']['blocking'])
        for key in ('quote', 'indicators', 'levels', 'institutional', 'conditions', 'bars'):
            self.assertEqual(result[key], self.listed[key], key)

    def test_low20_reference_and_unformed_defense_agree(self):
        reference = self.listed['reference']['low20_intraday']
        self.assertEqual(reference['value'], self.listed['indicators']['low20_intraday'])
        self.assertEqual(self.listed['defense']['value_if_zone_not_formed'], reference['value'])

    def test_trading_rules_and_pending_fields_are_fixed(self):
        for result in (self.listed, self.otc):
            self.assertEqual(result['trading_rules']['status'], 'not_provided_by_source')
            self.assertEqual(result['trend_classification']['status'], 'pending_composite_score')
            self.assertEqual(set(result['conditions']['confirmed']), CONFIRMED_CONDITIONS)
            self.assertEqual(set(result['conditions']['approximate']), APPROXIMATE_CONDITIONS)
            self.assertEqual(result['conditions']['pending']['count'], PENDING_CONDITION_COUNT)

    def test_resistance_slots_respect_observable_range(self):
        for result in (self.listed, self.otc):
            levels = result['levels']
            for cluster in levels['resistance']:
                self.assertLessEqual(cluster['distance_pct'], levels['observable_range_pct'])
                self.assertGreaterEqual(cluster['signal_count'], 2)


SAMPLES = Path(__file__).resolve().parent / '.tmp/member-audit/samples-round2.json'
SITE_IDS = {'ma_trend': 'T01', 'kd_bullish': 'T04', 'momentum_overheated': 'T05', 'near_prior_high': 'T06',
            'volume_confirmed': 'V02', 'heavy_volume_stall': 'V03', 'volume_contraction': 'V05', 'macd_bullish': 'T03'}


@unittest.skipUnless(SAMPLES.exists() and DEFAULT_DB.exists() and DEFAULT_OTC_DB.exists(), '會員樣本或行情庫不在本機')
class MemberSampleTests(unittest.TestCase):
    """會員頁第二輪 198 檔樣本（2026-11-06）只用來核對規則，不進產品資料。"""

    @classmethod
    def setUpClass(cls):
        from twon_analysis.analyze_v2 import analyze_v2
        cls.samples = [s for s in json.loads(SAMPLES.read_text(encoding='utf-8'))['samples'] if s['site'].get('priceStructure')]
        cls.mine = {s['code']: analyze_v2(s['code'], '2026-11-06') for s in cls.samples}
        cls.samples = [s for s in cls.samples if not cls.mine[s['code']]['qa']['blocking']]

    def rate(self, key):
        hits = sum(self.mine[s['code']]['conditions'].get('confirmed', {}).get(key, self.mine[s['code']]['conditions']
                   .get('approximate', {}).get(key))['state'] == s['site']['strategies'][SITE_IDS[key]] for s in self.samples)
        return hits / len(self.samples)

    def test_price_technical_conditions_match_member_samples(self):
        # 用本機日K重算（與原站 details 略有差：原站 avgVol20 取法、資料日）；可當規格的門檻須 ≥ 95%
        for key in ('ma_trend', 'kd_bullish', 'momentum_overheated', 'near_prior_high', 'volume_confirmed',
                    'heavy_volume_stall', 'volume_contraction'):
            with self.subTest(key):
                self.assertGreaterEqual(self.rate(key), 0.95)
        self.assertGreaterEqual(self.rate('macd_bullish'), 0.90)

    def test_defense_and_observable_range_match_member_samples(self):
        unformed = [s for s in self.samples if not s['site']['sweet'].get('formed')]
        self.assertGreaterEqual(len(unformed), 150)
        for s in unformed:
            with self.subTest(s['code']):
                self.assertAlmostEqual(self.mine[s['code']]['defense']['value_if_zone_not_formed'],
                                       s['site']['levels']['defense'], places=2)
        from twon_analysis.analyze_v2 import observable_range_pct
        for s in self.samples:
            ps = s['site']['priceStructure']
            with self.subTest(s['code']):
                # 規則本身：用原站自己的 ATR% 必須完全重現
                self.assertAlmostEqual(observable_range_pct(ps['atrPct']), ps['pressure']['observableRangePct'], places=2)
                # 本機 ATR% 與原站偶有 0.01～0.02 的資料差（4 倍後 ≤0.1）
                self.assertLessEqual(abs(self.mine[s['code']]['levels']['observable_range_pct']
                                         - ps['pressure']['observableRangePct']), 0.1)


RESEARCH = Path('C:/Users/User/Desktop/claude/.scratch/2tonstock-research/2026-10-18/deep')


@unittest.skipUnless(RESEARCH.exists() and DEFAULT_DB.exists(), '研究樣本或行情庫不在本機')
class ResearchCompatibilityTests(unittest.TestCase):
    def test_evergreen_first_resistance_still_matches_the_original_screen(self):
        from twon_analysis.analyze_v2 import analyze_v2
        result = analyze_v2('2603', '2026-10-16')
        self.assertEqual(result['levels']['aggregation_tolerance_pct'], 1.84)
        self.assertEqual(result['levels']['resistance'][0]['tick'], 268.5)

    def test_first_resistance_within_two_percent_of_original_samples(self):
        import glob
        from twon_analysis.analyze_v2 import analyze_v2
        files = sorted(glob.glob(str(RESEARCH / 'training/*-decoded.json'))) + sorted(glob.glob(str(RESEARCH / 'holdout/*-decoded.json')))
        for path in files:
            analysis = json.loads(Path(path).read_text(encoding='utf-8'))['analysis']
            with self.subTest(analysis['symbol']):
                original = analysis['derived']['priceStructure']['pressure']['clusters'][0]['level']
                mine = analyze_v2(analysis['symbol'], '2026-10-16')['levels']['resistance_all'][0]['level']
                self.assertLess(abs(mine / original - 1), 0.02)


if __name__ == '__main__':
    unittest.main()
