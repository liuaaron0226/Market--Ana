"""個股分析 v2 契約測試（S1）：只留原站重現項，未公開項用固定表示法，不含自創計分與原站命名。

契約：.scratch/chart-screening-workbench/stock-analysis-contract-v2.md
"""
import json
import sqlite3
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from twon_analysis.data import DEFAULT_DB, DEFAULT_OTC_DB
from twon_analysis.indicators import Bar

TOP_KEYS = {'schema', 'analysis_version', 'code', 'name', 'kind', 'market', 'as_of', 'requested_as_of', 'quote', 'qa',
            'indicators', 'levels', 'defense', 'trend_classification', 'conditions', 'trading_rules', 'reference',
            'undisclosed', 'provenance', 'bars', 'chart', 'labels', 'strategy', 'institutional', 'big_holders', 'facts'}
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


class AdjustedPriceTests(unittest.TestCase):
    """本人 2026-11-14「圖和判讀都用」：個股圖與均線、關卡、走勢、策略共用同一套還原價。"""

    def setUp(self):
        from datetime import date, timedelta
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'h.sqlite'
        # 60 根：前 30 根在 400 元附近；第 31 根起一拆四，在 100 元附近。
        self.days = [(date(2026, 1, 5) + timedelta(days=i)).isoformat() for i in range(60)]
        self.closes = [400.0 + (i % 3) * 4 for i in range(30)] + [100.0 + (i % 3) for i in range(30)]
        connection = sqlite3.connect(self.db)
        connection.execute("""CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, open REAL, high REAL, low REAL,
                              close REAL, change_sign TEXT, change REAL, volume_shares INTEGER, trades INTEGER, turnover INTEGER,
                              suspended INTEGER)""")
        for day, close in zip(self.days, self.closes):
            connection.execute("INSERT INTO daily VALUES (?, '9999', '測試', 'common', ?, ?, ?, ?, '+', 0.5, 1000, 1, 1, 0)",
                               (day, close, close * 1.01, close * 0.99, close))
        connection.commit()
        connection.close()
        self.split = {'date': self.days[30], 'code': '9999', 'type': '變更面額', 'close_before': self.closes[29],
                      'reference': self.closes[29] / 4, 'source': 'TWTB8U-202602.json.gz'}

    def analyze(self, capital_actions):
        from twon_analysis.analyze_v2 import analyze_v2
        return analyze_v2('9999', self.days[-1], db_path=self.db, otc_db_path=None, capital_actions=capital_actions)

    def test_chart_bars_and_indicators_use_split_adjusted_prices(self):
        result = self.analyze({'9999': [self.split]})
        closes = [bar['close'] for bar in result['bars']]
        self.assertEqual(len(closes), 60)
        self.assertTrue(all(99 <= close <= 103 for close in closes), closes[:5])   # 圖上沒有 400 → 100 的斷崖
        self.assertEqual(result['bars'][0]['volume'], 4000)                        # 成交量跟著還原
        self.assertTrue(100 <= result['indicators']['ma60'] <= 102, result['indicators']['ma60'])  # 判讀用同一套價格
        self.assertEqual(result['quote']['close'], self.closes[-1])                # 最新一根是實際成交價
        self.assertEqual(result['qa'].get('unresolved_capital_events'), [])
        self.assertEqual(result['labels']['price_basis'], '分割、減資、配股已還原；現金股利未還原')

    def test_event_that_cannot_be_resolved_is_reported_and_prices_stay_as_traded(self):
        announced = {**self.split, 'reference': None}
        result = self.analyze({'9999': [announced]})
        self.assertEqual(result['bars'][0]['close'], 400.0)
        self.assertEqual(result['qa']['unresolved_capital_events'],
                         [{'date': self.days[30], 'type': '變更面額', 'reason': '缺官方參考價或現金股利'}])

    def test_institutional_share_counts_follow_the_same_adjustment_as_volume(self):
        # 成交量還原成拆分後的股數，法人買賣超（同樣是股數）不跟著換算的話，事件日前後單位就混在一起。
        rows = [{'date': self.days[29], 'foreign': 1000, 'trust': -301, 'dealer': 50, 'total': 749},
                {'date': self.days[30], 'foreign': 4000, 'trust': -1200, 'dealer': 200, 'total': 3000}]
        with unittest.mock.patch('twon_analysis.analyze_v2.institutional_series', return_value=rows):
            result = self.analyze({'9999': [self.split]})
        self.assertEqual(result['institutional'], [
            {'date': self.days[29], 'foreign': 4000, 'trust': -1204, 'dealer': 200, 'total': 2996},
            {'date': self.days[30], 'foreign': 4000, 'trust': -1200, 'dealer': 200, 'total': 3000}])

    def test_weekly_big_holder_shares_come_with_the_analysis_up_to_its_data_day(self):
        # 本人 2026-11-14「可以，用集保」：主力買賣超沒有合法免費來源，改看集保的大戶持股變化。
        asked = []

        def weekly(out, code, as_of):
            asked.append((code, as_of))
            return [{'date': '2026-02-27', 'pct_1000': 60.1, 'holders_1000': 40, 'pct_400': 70.0, 'holders_400': 90, 'holders': 5000}]
        with unittest.mock.patch('twon_analysis.analyze_v2.big_holder_series', weekly):
            result = self.analyze({})
        self.assertEqual(asked, [('9999', self.days[-1])])
        self.assertEqual(result['big_holders'][0]['pct_1000'], 60.1)

    def test_missing_big_holder_data_is_an_empty_list_not_a_failed_analysis(self):
        def broken(out, code, as_of):
            raise OSError('unreadable')
        with unittest.mock.patch('twon_analysis.analyze_v2.big_holder_series', broken):
            self.assertEqual(self.analyze({})['big_holders'], [])

    def test_big_holders_include_the_backfilled_history_file(self):
        import tdcc_holders
        seen = {}
        from twon_analysis import analyze_v2 as module
        with unittest.mock.patch.object(tdcc_holders, 'series', lambda out, code, as_of, history=None: seen.update(history=history) or []):
            module.big_holder_series(Path('x'), '2330', '2026-11-13')
        self.assertEqual(seen['history'], tdcc_holders.DEFAULT_HISTORY)
        self.assertEqual(tdcc_holders.DEFAULT_HISTORY.name, 'tdcc-holders-history.json')

    def test_default_reads_the_capital_events_of_the_data_root(self):
        with unittest.mock.patch('twon_analysis.analyze_v2.default_capital_events', return_value={'9999': [self.split]}):
            result = self.analyze(None)
        self.assertEqual(result['bars'][0]['close'], 100.0)


class _FactsFixture:
    """事實區塊測試共用的行情庫：前面都收 100，最後一根依參數；法人資料用替身。"""

    def setUp(self):
        from datetime import date, timedelta
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # 和 analyze() 預設的 60 個交易日相同，測試可以先用這些日期組法人資料。
        self.days = [(date(2025, 1, 6) + timedelta(days=i)).isoformat() for i in range(60)]

    def analyze(self, *, change, close=100.0, last_volume=1000, volume=1000, foreign=None, foreign_day=None, change_sign='+',
                capital_actions=None, rows=None, n_days=60):
        from datetime import date, timedelta
        self.days = [(date(2025, 1, 6) + timedelta(days=i)).isoformat() for i in range(n_days)]
        self.calls = getattr(self, 'calls', 0) + 1
        db = Path(self.tmp.name) / f'h{self.calls}.sqlite'
        connection = sqlite3.connect(db)
        connection.execute("""CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, open REAL, high REAL, low REAL,
                              close REAL, change_sign TEXT, change REAL, volume_shares INTEGER, trades INTEGER, turnover INTEGER,
                              suspended INTEGER)""")
        for i, day in enumerate(self.days):
            last = i == len(self.days) - 1
            price = close if last else 100.0
            connection.execute("INSERT INTO daily VALUES (?, '9999', '測試', 'common', ?, ?, ?, ?, ?, ?, ?, 1, 1, 0)",
                               (day, price, price + 1, price - 1, price, change_sign if last else '+', change if last else 0.5,
                                last_volume if last else volume))
        connection.commit()
        connection.close()
        rows = list(rows or [])
        if foreign is not None:
            rows.append({'date': foreign_day or self.days[-1], 'foreign': foreign, 'trust': 0, 'dealer': 0, 'total': foreign})
        from twon_analysis.analyze_v2 import analyze_v2
        with unittest.mock.patch('twon_analysis.analyze_v2.institutional_series', return_value=rows):
            return analyze_v2('9999', self.days[-1], db_path=db, otc_db_path=None, capital_actions=capital_actions or {})



class StockFactsTodayTests(_FactsFixture, unittest.TestCase):
    """個股頁事實區塊（本人 2026-11-15 共識，規格 .scratch/stock-facts/spec.md）：當日三格只報數字，缺什麼就少什麼。"""

    def test_red_close_volume_ratio_and_foreign_net_sell(self):
        # 收盤 100.5、漲 0.5 → 0.50%；均量＝(19×1000＋2300)/20＝1065 → 2300/1065＝2.16 倍；外資 −8,758,880 股＝賣超 8,759 張。
        facts = self.analyze(change=0.5, close=100.5, last_volume=2300, foreign=-8_758_880)['facts']
        self.assertEqual(facts['data_date'], self.days[-1])
        self.assertEqual(facts['today'], [
            {'key': 'close', 'label': '收盤', 'tone': 'up', 'text': '收紅 +0.50%'},
            {'key': 'volume', 'label': '成交量', 'tone': None, 'text': '均量的 2.16 倍'},
            {'key': 'foreign', 'label': '外資', 'tone': 'down', 'text': '賣超 8,759 張'},
        ])

    def test_black_close_and_foreign_net_buy_rounds_half_up(self):
        # 收盤 98、跌 2 → 前一日 100 → −2.00%；外資 2,500 股＝2.5 張 → 3 張（四捨五入，不是銀行家捨入）。
        facts = self.analyze(change=-2.0, close=98.0, foreign=2_500)['facts']
        self.assertEqual(facts['today'][0], {'key': 'close', 'label': '收盤', 'tone': 'down', 'text': '收黑 -2.00%'})
        self.assertEqual(facts['today'][2], {'key': 'foreign', 'label': '外資', 'tone': 'up', 'text': '買超 3 張'})

    def test_flat_close_and_zero_foreign(self):
        facts = self.analyze(change=0.0, foreign=0)['facts']
        self.assertEqual(facts['today'][0], {'key': 'close', 'label': '收盤', 'tone': None, 'text': '平盤 0.00%'})
        self.assertEqual(facts['today'][2], {'key': 'foreign', 'label': '外資', 'tone': None, 'text': '買賣超 0 張'})

    def test_missing_volume_ratio_or_same_day_foreign_drops_that_cell(self):
        # 近 20 日量全是 0 → 量比算不出來；法人最新一筆不是資料日 → 外資那格不給（不拿前一天冒充）。
        facts = self.analyze(change=0.5, close=100.5, last_volume=0, volume=0, foreign=5_000, foreign_day=self.days[-2])['facts']
        self.assertEqual([cell['key'] for cell in facts['today']], ['close'])

    def test_day_without_a_price_comparison_has_no_close_cell_instead_of_a_fake_flat(self):
        # 官方標 X（不比價，例如除權息或新上市）時沒有可信的漲跌幅；不能寫成「平盤 0.00%」。
        facts = self.analyze(change=0.0, change_sign='X', foreign=1_000)['facts']
        self.assertEqual([cell['key'] for cell in facts['today']], ['volume', 'foreign'])

    def test_volume_multiple_drops_trailing_zeros(self):
        # 均量＝(19×1000＋1106)/20＝1005.3 → 1005；1106/1005＝1.1005 → 1.10 → 寫「1.1」。
        facts = self.analyze(change=0.5, close=100.5, last_volume=1106)['facts']
        self.assertEqual(facts['today'][1]['text'], '均量的 1.1 倍')

    def test_foreign_lots_on_the_data_day_are_unaffected_by_an_earlier_split(self):
        # 第 31 天一拆四：之前的法人股數乘 4 換算，資料日當天的股數不變 → 外資 3,000 股＝買超 3 張。
        split = {'date': self.days[30], 'code': '9999', 'type': '變更面額', 'close_before': 100.0,
                 'reference': 25.0, 'source': 'TWTB8U-202602.json.gz'}
        earlier = [{'date': self.days[29], 'foreign': 1_000, 'trust': 0, 'dealer': 0, 'total': 1_000}]
        result = self.analyze(change=0.5, close=100.5, foreign=3_000, capital_actions={'9999': [split]}, rows=earlier)
        self.assertEqual(result['institutional'][0]['foreign'], 4_000)
        self.assertEqual(result['facts']['today'][2]['text'], '買超 3 張')


class StockFactsSummaryTests(_FactsFixture, unittest.TestCase):
    """重點三句（B 票先兩句）：固定句型、只報數字；缺一項只寫另一半，兩項都缺整句不寫。"""

    def test_position_and_volume_sentences(self):
        # 60 根：59 根收 100、最後一根 110 → 月線＝(19×100＋110)/20＝100.5；只有 60 根所以沒有年線那一半。
        # RSI：近 14 個變動只有 +10 → 100；量都 1000 → 均量的 1 倍。
        facts = self.analyze(change=10.0, close=110.0)['facts']
        self.assertEqual(facts['summary'], ['收盤 110，在月線 100.5 之上。', '成交量是均量的 1 倍，RSI 100。'])
        self.assertIn('summary', facts['blocks'])

    def test_position_sentence_with_year_line_below_both(self):
        # 250 根：249 根收 100、最後一根 90 → 月線＝(19×100＋90)/20＝99.5；年線＝(239×100＋90)/240＝99.958… → 99.96。
        facts = self.analyze(change=-10.0, close=90.0, n_days=250)['facts']
        self.assertEqual(facts['summary'][0], '收盤 90，在月線 99.5 之下、年線 99.96 之下。')

    def test_volume_sentence_keeps_the_half_that_exists(self):
        # 全部收 100 → RSI 算不出來（不是 50）；量也全是 0 → 兩項都缺，整句不寫。
        self.assertEqual(self.analyze(change=0.0, volume=0, last_volume=0)['facts']['summary'], ['收盤 100，在月線 100 之上。'])
        self.assertEqual(self.analyze(change=0.0)['facts']['summary'][1], '成交量是均量的 1 倍。')
        self.assertEqual(self.analyze(change=10.0, close=110.0, volume=0, last_volume=0)['facts']['summary'][1], 'RSI 100。')


class StockFactsInstitutionalTests(_FactsFixture, unittest.TestCase):
    """法人：連買或連賣天數（從資料日往回數同方向），近 5／10／20 日累計（張）。"""

    def rows(self, foreign, trust, dealer):
        days = self.days[-len(foreign):]
        return [{'date': d, 'foreign': f, 'trust': t, 'dealer': x, 'total': f + t + x}
                for d, f, t, x in zip(days, foreign, trust, dealer)]

    def test_streaks_and_cumulative_lots(self):
        # 22 個交易日（舊→新）：
        # 外資：前 2 天各 +5,000 股，後 20 天各 −1,000 股 → 連賣 20 天（數到 +5,000 停）；5／10／20 日＝−5／−10／−20 張。
        # 投信：22 天都 +1,500 股 → 連買 22 天、數到最早一天仍同方向＝「以上」；7,500→8 張、15,000→15、30,000→30。
        # 自營商：前 21 天各 +100、資料日 0 → 不寫連續；400→0 張、900→1、1,900→2。
        rows = self.rows([5_000] * 2 + [-1_000] * 20, [1_500] * 22, [100] * 21 + [0])
        facts = self.analyze(change=0.5, close=100.5, rows=rows)['facts']
        self.assertEqual(facts['institutional'], {'data_date': self.days[-1], 'rows': [
            {'name': '外資', 'side': 'sell', 'days': 20, 'at_least': False, 'sum5': -5, 'sum10': -10, 'sum20': -20},
            {'name': '投信', 'side': 'buy', 'days': 22, 'at_least': True, 'sum5': 8, 'sum10': 15, 'sum20': 30},
            {'name': '自營商', 'side': None, 'days': None, 'at_least': False, 'sum5': 0, 'sum10': 1, 'sum20': 2},
        ]})
        self.assertEqual(facts['blocks'], ['summary', 'institutional', 'technical'])

    def test_short_series_leaves_out_the_longer_sums(self):
        # 只有 7 天：5 日累計有，10、20 日沒有。
        rows = self.rows([-2_000] * 7, [0] * 7, [0] * 7)
        foreign = self.analyze(change=0.5, close=100.5, rows=rows)['facts']['institutional']['rows'][0]
        self.assertEqual((foreign['days'], foreign['at_least'], foreign['sum5'], foreign['sum10'], foreign['sum20']),
                         (7, True, -10, None, None))

    def test_a_missing_trading_day_stops_the_streak_and_voids_sums_that_span_it(self):
        # 最後 6 個交易日外資都賣 1,000 股，但倒數第 3 天沒有資料：只能確定最近 2 天 → 「賣 2 天以上」；
        # 5、10、20 日的時間窗都跨過那個缺日 → 不給。
        rows = [row for row in self.rows([-1_000] * 6, [0] * 6, [0] * 6) if row['date'] != self.days[-3]]
        foreign = self.analyze(change=0.5, close=100.5, rows=rows)['facts']['institutional']['rows'][0]
        self.assertEqual((foreign['side'], foreign['days'], foreign['at_least'], foreign['sum5'], foreign['sum10'], foreign['sum20']),
                         ('sell', 2, True, None, None, None))

    def test_cumulative_lots_follow_the_split_adjustment(self):
        # 倒數第 3 天一拆四：之前兩天的 −1,000 股換算成 −4,000 股 → 5 日累計＝2×(−4,000)＋3×(−1,000)＝−11,000 股＝−11 張。
        split = {'date': self.days[-3], 'code': '9999', 'type': '變更面額', 'close_before': 100.0,
                 'reference': 25.0, 'source': 'TWTB8U-202602.json.gz'}
        rows = self.rows([-1_000] * 5, [0] * 5, [0] * 5)
        foreign = self.analyze(change=0.5, close=100.5, rows=rows, capital_actions={'9999': [split]})['facts']['institutional']['rows'][0]
        self.assertEqual((foreign['days'], foreign['sum5']), (5, -11))

    def test_no_institutional_rows_means_no_block(self):
        facts = self.analyze(change=0.5, close=100.5)['facts']
        self.assertIsNone(facts['institutional'])
        self.assertNotIn('institutional', facts['blocks'])


class StockFactsTechnicalTests(_FactsFixture, unittest.TestCase):
    """技術：均線與近 20 日收盤高低的價位、收盤距離（%，一位小數），與 RSI、量比、KD。"""

    def test_price_rows_and_readings(self):
        # 60 根：59 根收 100（高 101、低 99）、最後一根 110（高 111、低 109）。
        # 5 日線＝(4×100＋110)/5＝102 → 110/102−1＝7.84% → 7.8；10 日線＝101 → 8.9；月線＝100.5 → 9.5；
        # 季線＝(59×100＋110)/60＝100.1666… → 100.17 → 9.8；20 日收盤最高 110 → 0.0；最低 100 → 10.0。
        # KD：持平時 RSV＝50、K＝D＝50；最後一天 RSV＝(110−99)/(111−99)＝91.67 → K＝63.89、D＝54.63。
        technical = self.analyze(change=10.0, close=110.0)['facts']['technical']
        self.assertEqual(technical['rows'], [
            {'name': '5 日線', 'value': 102.0, 'distance_pct': 7.8},
            {'name': '10 日線', 'value': 101.0, 'distance_pct': 8.9},
            {'name': '月線', 'value': 100.5, 'distance_pct': 9.5},
            {'name': '季線', 'value': 100.17, 'distance_pct': 9.8},
            {'name': '20 日高', 'value': 110.0, 'distance_pct': 0.0},
            {'name': '20 日低', 'value': 100.0, 'distance_pct': 10.0},
        ])
        self.assertEqual((technical['rsi'], technical['volume_ratio'], technical['kd_k'], technical['kd_d']), (100.0, 1.0, 63.89, 54.63))

    def test_values_are_the_same_numbers_as_the_analysis_indicators(self):
        # 和圖上的線同一套數字：技術表不另外算。
        result = self.analyze(change=10.0, close=110.0)
        x = result['indicators']
        self.assertEqual([row['value'] for row in result['facts']['technical']['rows']],
                         [x['ma5'], x['ma10'], x['ma20'], x['ma60'], x['high20_close'], x['low20_close']])

    def test_half_year_and_year_lines_appear_with_enough_history(self):
        # 250 根、最後收 90：半年線＝(119×100＋90)/120＝99.92 → −9.9%；年線＝99.96 → −10.0%。
        rows = self.analyze(change=-10.0, close=90.0, n_days=250)['facts']['technical']['rows']
        self.assertEqual([row['name'] for row in rows], ['5 日線', '10 日線', '月線', '季線', '半年線', '年線', '20 日高', '20 日低'])
        self.assertEqual(rows[4:6], [{'name': '半年線', 'value': 99.92, 'distance_pct': -9.9},
                                     {'name': '年線', 'value': 99.96, 'distance_pct': -10.0}])

    def test_block_list_follows_what_exists(self):
        facts = self.analyze(change=10.0, close=110.0)['facts']
        self.assertEqual(facts['blocks'], ['summary', 'technical'])


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
