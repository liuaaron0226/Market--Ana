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
            'undisclosed', 'provenance', 'bars', 'chart', 'labels', 'strategy', 'institutional', 'big_holders', 'facts', 'signals'}
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
BANNED_TEXT = ('2TON', '漲停寶寶', '甜甜價', '甜價', '私藏', 'CP值', '抄底', 'FIT-V1', 'MOMENTUM-V1', 'SWING-SAFE', '漲停分數')


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
                capital_actions=None, rows=None, n_days=60, credit=None, valuation=None, revenue=None):
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
        # 融資券、估值、月營收的讀取都換成替身（沒給就是沒有資料），只驗事實區塊怎麼整理。
        with unittest.mock.patch('twon_analysis.analyze_v2.institutional_series', return_value=rows), \
                unittest.mock.patch('twon_analysis.analyze_v2.credit_series', return_value=credit or {}), \
                unittest.mock.patch('twon_analysis.analyze_v2.valuation_snapshot', return_value=valuation), \
                unittest.mock.patch('twon_analysis.analyze_v2.revenue_series', return_value=revenue or []):
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


class StockFactsCreditTests(_FactsFixture, unittest.TestCase):
    """融資券（C 票）：融資、融券、借券的最新餘額（張）、相對 5 個交易日前的增減、近 20 日走勢；各自標資料日。"""

    def credit(self, margin_days, borrow_days):
        # 融資融券：餘額 100、101…（融資）與 10、11…（融券），單位張；借券：50,000 股起每天加 1,500 股。
        return {'margin': [{'date': d, 'margin': 100 + i, 'short': 10 + i} for i, d in enumerate(margin_days)],
                'borrow': [{'date': d, 'balance': 50_000 + 1_500 * i} for i, d in enumerate(borrow_days)]}

    def test_balances_five_day_changes_and_series(self):
        # 融資融券 8 天、到倒數第 2 天（比法人晚一天）：最新 107 張，5 個交易日前 102 → +5；融券 17、12 → +5。
        # 借券 8 天、到資料日：最新 60,500 股＝61 張（60.5 四捨五入），5 個交易日前 53,000 股 → 增 7,500 股＝8 張。
        # 借券走勢逐日換成張：50、51.5→52、53、54.5→55、56、57.5→58、59、60.5→61。
        facts = self.analyze(change=0.5, close=100.5, credit=self.credit(self.days[-9:-1], self.days[-8:]))['facts']
        self.assertEqual(facts['credit']['rows'], [
            {'name': '融資', 'balance': 107, 'change5': 5, 'series': [100, 101, 102, 103, 104, 105, 106, 107], 'data_date': self.days[-2]},
            {'name': '融券', 'balance': 17, 'change5': 5, 'series': [10, 11, 12, 13, 14, 15, 16, 17], 'data_date': self.days[-2]},
            {'name': '借券', 'balance': 61, 'change5': 8, 'series': [50, 52, 53, 55, 56, 58, 59, 61], 'data_date': self.days[-1]},
        ])
        self.assertEqual(facts['blocks'], ['summary', 'credit', 'technical'])

    def test_a_missing_day_five_days_back_gives_no_change(self):
        # 融資融券缺了 5 個交易日前那一天 → 增減不給；走勢照樣畫手上有的日子（缺的那天是 None）。
        days = [d for d in self.days[-8:] if d != self.days[-6]]
        rows = self.analyze(change=0.5, close=100.5, credit=self.credit(days, []))['facts']['credit']['rows']
        self.assertEqual([row['name'] for row in rows], ['融資', '融券'])
        self.assertIsNone(rows[0]['change5'])
        self.assertEqual(rows[0]['series'], [100, 101, None, 102, 103, 104, 105, 106])

    def test_fewer_than_six_days_gives_no_change(self):
        rows = self.analyze(change=0.5, close=100.5, credit=self.credit(self.days[-5:], []))['facts']['credit']['rows']
        self.assertEqual((rows[0]['balance'], rows[0]['change5'], rows[0]['series']), (104, None, [100, 101, 102, 103, 104]))

    def test_latest_credit_day_outside_the_price_days_still_shows_its_balance(self):
        # 例如資料日當天停牌：K 線沒有那一天，融資原件有。最新餘額照樣顯示，不能讓整份事實區塊消失。
        extra = '2025-03-07'   # 比 K 線最後一天（2025-03-06）晚一天
        facts = self.analyze(change=0.5, close=100.5, credit=self.credit(self.days[-6:] + [extra], []))['facts']
        self.assertEqual(facts['credit']['rows'][0]['balance'], 106)
        self.assertEqual(facts['credit']['rows'][0]['data_date'], extra)
        self.assertEqual([cell['key'] for cell in facts['today']], ['close', 'volume'])   # 其他內容照常（這則沒給法人）

    def test_borrow_change_is_the_difference_of_the_shown_balances(self):
        # 60,400 股顯示 60 張、5 個交易日前 53,600 股顯示 54 張 → 增減寫 +6（兩個顯示的數字相減），不是 6,800 股＝7 張。
        borrow = [{'date': d, 'balance': v} for d, v in zip(self.days[-6:], [53_600, 55_000, 56_000, 57_000, 58_000, 60_400])]
        row = self.analyze(change=0.5, close=100.5, credit={'margin': [], 'borrow': borrow})['facts']['credit']['rows'][0]
        self.assertEqual((row['name'], row['balance'], row['change5']), ('借券', 60, 6))

    def test_no_credit_data_means_no_block(self):
        facts = self.analyze(change=0.5, close=100.5)['facts']
        self.assertIsNone(facts['credit'])
        self.assertNotIn('credit', facts['blocks'])


class StockFactsFundamentalsTests(_FactsFixture, unittest.TestCase):
    """基本面（C 票）：本益比、淨值比、殖利率；最新月營收的月增年增；近 13 個月營收年增。重點補上第三句。"""

    VALUATION = {'date': '2025-03-06', 'pe': 25.91, 'pb': 9.01, 'dividend_yield_pct': 0.98}

    def revenue(self):
        # 2024-09～2025-08 每月 100；2025-09～2026-07 每月 110；2026-08＝130；2026-09＝150（千元）。
        periods = ([f'2024-{m:02d}' for m in range(9, 13)] + [f'2025-{m:02d}' for m in range(1, 13)]
                   + [f'2026-{m:02d}' for m in range(1, 10)])
        values = [100] * 12 + [110] * 11 + [130, 150]
        return [{'period': p, 'revenue': v} for p, v in zip(periods, values)]

    def test_valuation_latest_revenue_and_yoy_series(self):
        # 2026-09：年增＝150/110−1＝36.36% → 36.4；月增＝150/130−1＝15.38% → 15.4。
        # 近 13 個月（2025-09～2026-09）年增：前 11 個月 110/100 → 10.0；2026-08 130/100 → 30.0；2026-09 → 36.4。
        facts = self.analyze(change=0.5, close=100.5, valuation=self.VALUATION, revenue=self.revenue())['facts']
        self.assertEqual(facts['fundamentals'], {
            'valuation': {'pe': 25.91, 'pb': 9.01, 'yield': 0.98, 'data_date': '2025-03-06'},
            'revenue': {'period': '2026-09', 'mom': 15.4, 'yoy': 36.4},
            'revenue_yoy': [{'period': p, 'yoy': y} for p, y in zip(
                ['2025-09', '2025-10', '2025-11', '2025-12'] + [f'2026-{m:02d}' for m in range(1, 10)],
                [10.0] * 11 + [30.0, 36.4])],
        })
        self.assertEqual(facts['summary'][2], '9 月營收年增 +36.4%，本益比 25.91。')
        self.assertEqual(facts['blocks'], ['summary', 'fundamentals', 'technical'])

    def test_months_without_last_year_have_no_yoy(self):
        # 只有 2025-10 起的月營收：近 13 個月裡 2025-09～2026-09 都沒有去年同期 → 年增都不給；最新一期仍有月增。
        revenue = [row for row in self.revenue() if row['period'] >= '2025-10']
        block = self.analyze(change=0.5, close=100.5, revenue=revenue)['facts']['fundamentals']
        self.assertEqual(block['revenue'], {'period': '2026-09', 'mom': 15.4, 'yoy': None})
        self.assertTrue(all(item['yoy'] is None for item in block['revenue_yoy']))

    def test_a_tiny_decline_rounds_to_plain_zero_not_minus_zero(self):
        # 9,999 對 10,000：年增 −0.01% → 一位小數是 0.0（不能是 −0.0，頁面會印成「-0%」）。
        revenue = [{'period': '2025-09', 'revenue': 10_000}, {'period': '2026-09', 'revenue': 9_999}]
        facts = self.analyze(change=0.5, close=100.5, revenue=revenue)['facts']
        import math
        self.assertEqual(math.copysign(1, facts['fundamentals']['revenue']['yoy']), 1.0)
        self.assertEqual(facts['summary'][2], '9 月營收年增 0.0%。')

    def test_third_sentence_keeps_the_half_that_exists(self):
        only_valuation = self.analyze(change=0.5, close=100.5, valuation=self.VALUATION)['facts']
        self.assertEqual(only_valuation['summary'][2], '本益比 25.91。')
        self.assertIsNone(only_valuation['fundamentals']['revenue'])
        only_revenue = self.analyze(change=0.5, close=100.5, revenue=self.revenue())['facts']
        self.assertEqual(only_revenue['summary'][2], '9 月營收年增 +36.4%。')
        self.assertIsNone(only_revenue['fundamentals']['valuation'])

    def test_no_valuation_and_no_revenue_means_no_block_and_two_sentences(self):
        # 例如 ETF：沒有本益比也沒有月營收 → 沒有基本面這個按鈕，重點只有兩句。
        facts = self.analyze(change=0.5, close=100.5)['facts']
        self.assertIsNone(facts['fundamentals'])
        self.assertNotIn('fundamentals', facts['blocks'])
        self.assertEqual(len(facts['summary']), 2)


class StockFactsReaderTests(unittest.TestCase):
    """融資券讀取：從每日更新已經在抓的上市原件（融資融券 MI_MARGN、借券 TWT93U）讀出單一個股，由舊到新。"""

    def test_listed_credit_series_reads_the_daily_raw_files(self):
        from twon_analysis import analyze_v2 as module, chips
        with tempfile.TemporaryDirectory() as tmp:
            detail, fund = Path(tmp) / 'detail', Path(tmp) / 'fund'
            for folder in (detail / 'margin', detail / 't86', fund / 'twt93u'):
                folder.mkdir(parents=True)
            for day, margin, short, borrow in (('20260105', 300, 9, 70_000), ('20260106', 310, 7, 71_000)):
                (detail / 't86' / f't86-{day}.json').write_text('{"data": {"fields": [], "data": []}}', encoding='utf-8')
                row = ['9999', '測試', '0', '0', '0', '0', str(margin), '0', '0', '0', '0', '0', str(short), '0', '0', '']
                (detail / 'margin' / f'margin-{day}.json').write_text(json.dumps(
                    {'data': {'tables': [{'fields': ['代號'], 'data': [row]}]}}), encoding='utf-8')
                borrow_row = ['9999', '測試', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', str(borrow), '0', '']
                (fund / 'twt93u' / f'twt93u-{day}.json').write_text(json.dumps({'data': [borrow_row]}), encoding='utf-8')
            with unittest.mock.patch.object(chips, 'DETAIL', detail), unittest.mock.patch.object(chips, 'FUND', fund):
                for cached in (chips._margin, chips._borrow):
                    cached.cache_clear()
                try:
                    series = module.credit_series('9999', '上市', '2026-01-06')
                finally:
                    for cached in (chips._margin, chips._borrow):
                        cached.cache_clear()
        self.assertEqual(series, {
            'margin': [{'date': '2026-01-05', 'margin': 300, 'short': 9}, {'date': '2026-01-06', 'margin': 310, 'short': 7}],
            'borrow': [{'date': '2026-01-05', 'balance': 70_000}, {'date': '2026-01-06', 'balance': 71_000}],
        })

    def test_valuation_and_revenue_readers_use_the_daily_data(self):
        # 估值：資料日以前最近一筆；月營收：只用法定公布日已過的期別（2026-10 還沒到公布日，不算）。
        import valuation_history
        import fundamentals_history as fh
        from twon_analysis import analyze_v2 as module
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / 'valuation.sqlite'
            connection = sqlite3.connect(db)
            connection.execute('CREATE TABLE valuation (market TEXT, date TEXT, code TEXT, name TEXT, close REAL, dividend_yield_pct REAL, '
                               'dividend_year_roc INTEGER, pe REAL, pb REAL, fin_period_roc TEXT, schema TEXT)')
            for day, pe in (('2026-11-05', 24.0), ('2026-11-06', 25.91), ('2026-11-10', 30.0)):
                connection.execute("INSERT INTO valuation VALUES ('上市', ?, '9999', '測試', 1, 0.98, 114, ?, 9.01, '115/2', 'v8')", (day, pe))
            connection.commit()
            connection.close()
            (root / 'fundamentals-history').mkdir()
            files = [{'dataset': 'monthly_revenue', 'market': 'listed', 'period': period, 'available_on': available,
                      'file': f'{period}.html', 'fetched_at_utc': 'x', 'sha256': 'x'}
                     for period, available in (('2026-08', '2026-09-10'), ('2026-09', '2026-10-10'), ('2026-10', '2026-11-10'))]
            (root / 'fundamentals-history' / 'meta.json').write_text(json.dumps({'files': files}), encoding='utf-8')
            for item in files:
                (root / 'fundamentals-history' / item['file']).write_bytes(b'')
            values = {'2026-08': 130, '2026-09': 150, '2026-10': 999}

            def parse(content, *, market, period, **_):
                return [{'code': '9999', 'revenue_thousand_twd': values[period]}]
            with unittest.mock.patch.object(valuation_history, 'DEFAULT_DB', db), \
                    unittest.mock.patch.object(module, 'DEFAULT_DB', root / 'stock-history' / 'h.sqlite'), \
                    unittest.mock.patch.object(fh, 'parse_monthly_revenue', parse), \
                    unittest.mock.patch.dict(module._REVENUE_CACHE, clear=True):
                valuation = module.valuation_snapshot('9999', '2026-11-09')
                revenue = module.revenue_series('9999', '上市', '2026-11-09')
        self.assertEqual((valuation['date'], valuation['pe'], valuation['pb'], valuation['dividend_yield_pct']), ('2026-11-06', 25.91, 9.01, 0.98))
        self.assertEqual(revenue, [{'period': '2026-08', 'revenue': 130}, {'period': '2026-09', 'revenue': 150}])

    def test_otc_credit_series_reads_the_tpex_raw_files(self):
        # E 票：上櫃融資融券（張）在「資餘額」「券餘額」，借券（股）在第二個「當日餘額」；只讀資料日以前的。
        from twon_analysis import analyze_v2 as module
        margin_fields = ['代號', '名稱', '前資餘額(張)', '資買', '資賣', '現償', '資餘額', '資屬證金', '資使用率(%)', '資限額',
                         '前券餘額(張)', '券賣', '券買', '券償', '券餘額', '券屬證金']
        sbl_fields = ['股票代號', '股票名稱', '前日餘額', '賣出', '買進', '現券', '當日餘額', '限額', '前日餘額', '當日賣出',
                      '當日還券', '當日調整數額', '當日餘額', '次一營業日可借券賣出限額', '備註']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for kind in ('margin', 'sbl'):
                (root / 'otc-credit' / kind).mkdir(parents=True)
            for day, margin, short, borrow in (('20261110', '9,612', '202', '12,577,000'), ('20261111', '9,021', '198', '12,089,000'),
                                               ('20261112', '1', '1', '1')):
                margin_row = ['6488', '環球晶', '0', '0', '0', '0', margin, '0', '0', '0', '0', '0', '0', '0', short, '0']
                sbl_row = ['6488', '環球晶', '0', '0', '0', '0', '0', '0', '0', '0', '0', '0', borrow, '0', '']
                (root / 'otc-credit/margin' / f'{day}.json').write_text(json.dumps(
                    {'stat': 'ok', 'tables': [{'fields': margin_fields, 'data': [margin_row]}]}), encoding='utf-8')
                (root / 'otc-credit/sbl' / f'{day}.json').write_text(json.dumps(
                    {'stat': 'ok', 'tables': [{'fields': sbl_fields, 'data': [sbl_row]}]}), encoding='utf-8')
            with unittest.mock.patch.object(module, 'DEFAULT_DB', root / 'stock-history' / 'h.sqlite'):
                series = module.credit_series('6488', '上櫃', '2026-11-11')
        self.assertEqual(series, {
            'margin': [{'date': '2026-11-10', 'margin': 9612, 'short': 202}, {'date': '2026-11-11', 'margin': 9021, 'short': 198}],
            'borrow': [{'date': '2026-11-10', 'balance': 12_577_000}, {'date': '2026-11-11', 'balance': 12_089_000}],
        })


BROKEN = '這一天的個股融資原件讀不了'


def margin_day(day, trades, prices, official_thousand=1):
    """融資重播用的一天。trades：{代號: (買進, 賣出, 現金償還, 前日餘額, 今日餘額)}，單位張；
    prices：{代號: (收盤, 成交均價)}，沒有成交的那一檔不放；official_thousand：官方公布的全市場融資金額（仟元）。
    trades 給 None 是這一天沒有融資原件（只有行情），給 BROKEN 是原件在、但內容讀不了；prices 給 None 是這一天沒有行情摘要。"""
    return {'day': day, 'trades': trades, 'prices': prices, 'official_thousand': official_thousand}


def write_margin_store(root: Path, days) -> Path:
    """在資料根目錄 root 底下，把 margin_day 的清單寫成長期保存區（data/margin-history）的樣子：
    raw/ 是每日抓取存的個股融資原件，prices/ 是同一天的行情摘要（收盤、成交股數、成交金額）。回傳 root。"""
    store = root / 'data/margin-history'
    for item in days:
        compact = item['day'].replace('-', '')
        if item['prices'] is not None:
            # 成交 1,000 股、成交金額＝均價 × 1,000，所以成交均價就是給的那個數字。
            quotes = {code: [close, 1000, round(average * 1000)] for code, (close, average) in item['prices'].items()}
            price_file = store / 'prices' / f'prices-{compact}.json'
            price_file.parent.mkdir(parents=True, exist_ok=True)
            price_file.write_text(json.dumps({'date': compact, 'prices': quotes}), encoding='utf-8')
        if item['trades'] is None:
            continue
        raw = store / 'raw' / f'margin-{compact}.json'
        raw.parent.mkdir(parents=True, exist_ok=True)
        if item['trades'] == BROKEN:
            raw.write_text('讀不了的內容', encoding='utf-8')
            continue
        rows = [[code, '測試', *(f'{value:,}' for value in trade), '0'] for code, trade in item['trades'].items()]
        official = {'stat': 'OK', 'date': compact, 'tables': [
            {'fields': ['項目', '買進', '賣出', '現金(券)償還', '前日餘額', '今日餘額'],
             'data': [['融資(交易單位)', '1', '2', '3', '4', '5'],
                      ['融資金額(仟元)', '1', '2', '3', '4', f"{item['official_thousand']:,}"]]},
            {'fields': ['代號', '名稱', '買進', '賣出', '現金償還', '前日餘額', '今日餘額', '次一營業日限額'], 'data': rows}]}
        raw.write_text(json.dumps({'fetched_at_utc': 't', 'source_url': 'u', 'data': official}, ensure_ascii=False), encoding='utf-8')
    return root


def three_margin_days(held, official_thousand=6331, code='9999'):
    """融資測試常用的三天：買 100 張均價 100、再買 100 張均價 120（兩種成本都是 110）、第 3 天賣出 50 張加現金償還 50 張
    （按比例減 → 110；最近買的先減 → 100）。第 3 天收 132 → 維持率 200～220；估計的全市場融資金額 6,330,520 元。"""
    return [margin_day(held[0], {code: (100, 0, 0, 0, 100)}, {code: (100.0, 100.0)}),
            margin_day(held[1], {code: (100, 0, 0, 100, 200)}, {code: (120.0, 120.0)}),
            margin_day(held[2], {code: (0, 50, 50, 200, 100)}, {code: (132.0, 130.0)}, official_thousand=official_thousand)]


class _SignalsFixture:
    """五塊資料測試共用：行情庫每一根給（收盤、漲跌符號、漲跌），加權指數與其他讀取都用替身。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls = 0

    @staticmethod
    def days(count):
        from datetime import date, timedelta
        return [(date(2025, 1, 6) + timedelta(days=i)).isoformat() for i in range(count)]

    def analyze(self, rows, index, *, otc=False, code='9999', kind='common', days=None, ex_events=(), capital_actions=None,
                institutional=(), margin=()):
        """rows：由舊到新的（收盤、漲跌符號、漲跌），後面可以再接（最高、最低、成交股數、成交金額），不接就是收盤 ±1、
        1000 股、成交金額 1；
        index：{日期: 加權指數收盤}，給例外就是模擬加權指數讀取出錯；
        days：這一檔有交易的日子（不給就是連續的）；ex_events：這一檔帶官方參考價的除權息等事件（events.json 的形狀）；
        institutional：法人買賣超逐日的列（institutional_series 的形狀），給例外就是模擬法人資料讀取出錯；
        margin：融資重播用的每日資料（margin_day 的清單），不給就是手上沒有任何融資原件。"""
        days = days or self.days(len(rows))
        self.calls += 1
        db = Path(self.tmp.name) / f's{self.calls}.sqlite'
        connection = sqlite3.connect(db)
        connection.execute("""CREATE TABLE daily (date TEXT, code TEXT, name TEXT, kind TEXT, open REAL, high REAL, low REAL,
                              close REAL, change_sign TEXT, change REAL, volume_shares INTEGER, trades INTEGER, turnover INTEGER,
                              suspended INTEGER)""")
        for day, (close, sign, change, *rest) in zip(days, rows):
            high, low, volume, turnover = (*rest, 1)[:4] if rest else (close + 1, close - 1, 1000, 1)
            connection.execute("INSERT INTO daily VALUES (?, ?, '測試', ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0)",
                               (day, code, kind, close, high, low, close, sign, change, volume, turnover))
        connection.commit()
        connection.close()
        from twon_analysis.analyze_v2 import analyze_v2
        paths = {'db_path': Path(self.tmp.name) / 'none.sqlite', 'otc_db_path': db} if otc else {'db_path': db, 'otc_db_path': None}
        margin_root = write_margin_store(Path(self.tmp.name) / f'margin{self.calls}', margin)
        taiex = {'side_effect': index} if isinstance(index, Exception) else {'return_value': index}
        chips = {'side_effect': institutional} if isinstance(institutional, Exception) else {'return_value': list(institutional)}
        with unittest.mock.patch('twon_analysis.analyze_v2.institutional_series', **chips), \
                unittest.mock.patch('twon_analysis.analyze_v2.credit_series', return_value={}), \
                unittest.mock.patch('twon_analysis.analyze_v2.valuation_snapshot', return_value=None), \
                unittest.mock.patch('twon_analysis.analyze_v2.revenue_series', return_value=[]), \
                unittest.mock.patch('twon_analysis.margin_maintenance.ROOT', margin_root), \
                unittest.mock.patch('twon_analysis.analyze_v2.taiex_closes', **taiex):
            return analyze_v2(code, days[-1], capital_actions=capital_actions or {},
                              reference_events={code: list(ex_events)}, **paths)


FLAT = (100.0, '', 0.0)
# 70 根：第 50 根 100→125（+25%）、第 64 根 125→100（−20%）、第 69 根 100→110（+10%）、第 70 根 110→99（−10%）。
# 近 5 日＝1.1×0.9−1＝−1.00%；近 20 日＝0.8×1.1×0.9−1＝−20.80%；近 60 日＝1.25×0.8×1.1×0.9−1＝−1.00%。
SEVENTY = ([FLAT] * 49 + [(125.0, '+', 25.0)] + [(125.0, '', 0.0)] * 13 + [(100.0, '-', -25.0)] + [FLAT] * 4
           + [(110.0, '+', 10.0)] + [(99.0, '-', -11.0)])


class StockSignalsVersusMarketTests(_SignalsFixture, unittest.TestCase):
    """五塊資料的「和大盤比」（本人 2026-11-16 共識，規格 .scratch/stock-signals/spec.md）：
    近 5、20、60 個交易日，這一檔的漲跌幅減加權指數的漲跌幅（百分點）。"""

    def index(self, **closes):
        """加權指數收盤：用「倒數第幾根」指定（d0＝資料日、d5＝5 個交易日前…）。"""
        days = self.days(70)
        return {days[-1 - int(key[1:])]: value for key, value in closes.items()}

    def test_stronger_than_the_market(self):
        # 大盤近 20 日：19,602 ÷ 25,000 − 1＝−21.592% → 這一檔 −20.80% 減它＝+0.792 → 強於大盤 0.8。
        signals = self.analyze(SEVENTY, self.index(d0=19602.0, d20=25000.0))['signals']
        self.assertEqual(signals['data_date'], self.days(70)[-1])
        self.assertEqual(signals['versus_market']['rows'], [
            {'days': 20, 'stock_pct': -20.8, 'market_pct': -21.59, 'gap': 0.8, 'tone': 'up', 'label': '強於大盤'}])
        self.assertEqual(signals['blocks'], ['limit_up_momentum', 'versus_market'])

    def test_weaker_than_the_market(self):
        # 大盤近 60 日：19,602 ÷ 19,000 − 1＝+3.168% → 這一檔 −1.00% 減它＝−4.168 → 落後大盤 4.2。
        rows = self.analyze(SEVENTY, self.index(d0=19602.0, d60=19000.0))['signals']['versus_market']['rows']
        self.assertEqual(rows, [
            {'days': 60, 'stock_pct': -1.0, 'market_pct': 3.17, 'gap': -4.2, 'tone': 'down', 'label': '落後大盤'}])

    def test_zero_gap_names_neither_side(self):
        # 大盤近 5 日：19,602 ÷ 19,800 − 1＝−1.00%，和這一檔一樣 → 差距 0，不寫強弱。
        rows = self.analyze(SEVENTY, self.index(d0=19602.0, d5=19800.0))['signals']['versus_market']['rows']
        self.assertEqual(rows, [{'days': 5, 'stock_pct': -1.0, 'market_pct': -1.0, 'gap': 0.0, 'tone': None, 'label': None}])

    def test_three_rows_come_in_fixed_order(self):
        index = self.index(d0=19602.0, d5=19800.0, d20=25000.0, d60=19000.0)
        rows = self.analyze(SEVENTY, index)['signals']['versus_market']['rows']
        self.assertEqual([(row['days'], row['gap']) for row in rows], [(5, 0.0), (20, 0.8), (60, -4.2)])

    def test_ex_dividend_day_does_not_count_as_a_drop(self):
        # 倒數第 4 根除息：收盤 100→95，官方標不比價（X）、沒有漲跌。之後持平，資料日 95→96.9（漲 1.9＝+2%）。
        # 這一檔近 5 日＝+2.00%（不是收盤對收盤的 −3.1%）；大盤 20,200 ÷ 20,000 − 1＝+1.00% → 強於大盤 1.0。
        # 這裡沒有給官方參考價（事件表查不到這一天）：行情庫在不比價的日子沒有漲跌，沒有來源就整天記 0；
        # 就算原件帶了漲跌數字（−5）也不算進去。有官方參考價的情形見下一個測試。
        for ex_dividend_change in (None, -5.0):
            rows = [FLAT] * 66 + [(95.0, 'X', ex_dividend_change), (95.0, '', 0.0), (95.0, '', 0.0), (96.9, '+', 1.9)]
            result = self.analyze(rows, self.index(d0=20200.0, d5=20000.0))['signals']['versus_market']['rows']
            self.assertEqual(result, [
                {'days': 5, 'stock_pct': 2.0, 'market_pct': 1.0, 'gap': 1.0, 'tone': 'up', 'label': '強於大盤'}], ex_dividend_change)

    def test_ex_dividend_day_counts_its_move_against_the_official_reference_price(self):
        # 倒數第 4 根除息 4 元：前一日收 100，官方除息參考價 96，當天收 99.84 → 當天真實漲跌＝99.84 ÷ 96 − 1＝+4.00%。
        # 之後三根持平 → 這一檔近 5 日＝+4.00%；大盤 20,200 ÷ 20,000 − 1＝+1.00% → 強於大盤 3.0。
        # （整天記 0 的話會變成 0 − 1＝落後大盤 1.0，方向相反。）
        days = self.days(70)
        rows = [FLAT] * 66 + [(99.84, 'X', None)] + [(99.84, '', 0.0)] * 3
        event = {'date': days[66], 'code': '9999', 'type': '除息', 'close_before': 100.0, 'reference': 96.0, 'value': 4.0}
        result = self.analyze(rows, self.index(d0=20200.0, d5=20000.0), ex_events=[event])['signals']['versus_market']['rows']
        self.assertEqual(result, [{'days': 5, 'stock_pct': 4.0, 'market_pct': 1.0, 'gap': 3.0, 'tone': 'up', 'label': '強於大盤'}])

    def test_official_reference_that_does_not_fit_the_traded_prices_is_not_used(self):
        # 同上一個例子，但事件表對不上：事件前收盤寫 90（實際前一根收 100），或同一天有兩筆不同的參考價，
        # 或沒有參考價（只有預告）。都不猜，那一天記 0 → 這一檔近 5 日 0.00%，大盤 +1.00% → 落後大盤 1.0。
        days = self.days(70)
        rows = [FLAT] * 66 + [(99.84, 'X', None)] + [(99.84, '', 0.0)] * 3
        event = {'date': days[66], 'code': '9999', 'type': '除息', 'close_before': 100.0, 'reference': 96.0, 'value': 4.0}
        for ex_events in ([{**event, 'close_before': 90.0}], [event, {**event, 'type': '減資', 'reference': 120.0}],
                          [{**event, 'reference': None}]):
            result = self.analyze(rows, self.index(d0=20200.0, d5=20000.0), ex_events=ex_events)['signals']['versus_market']['rows']
            self.assertEqual(result, [
                {'days': 5, 'stock_pct': 0.0, 'market_pct': 1.0, 'gap': -1.0, 'tone': 'down', 'label': '落後大盤'}], ex_events)

    def test_split_day_counts_its_move_against_the_resumption_reference_price(self):
        # 倒數第 4 根一拆四恢復買賣：前一日收 100，參考價 25，當天收 26 → 當天真實漲跌＝26 ÷ 25 − 1＝+4.00%（不是 −74%）。
        # 之後持平 → 近 5 日 +4.00%，大盤 +1.00% → 強於大盤 3.0。價格還原（圖用）和這裡互不干擾。
        days = self.days(70)
        rows = [FLAT] * 66 + [(26.0, 'X', None)] + [(26.0, '', 0.0)] * 3
        split = {'date': days[66], 'code': '9999', 'type': '變更面額', 'close_before': 100.0, 'reference': 25.0}
        result = self.analyze(rows, self.index(d0=20200.0, d5=20000.0), ex_events=[split], capital_actions={'9999': [split]})
        self.assertEqual(result['signals']['versus_market']['rows'], [
            {'days': 5, 'stock_pct': 4.0, 'market_pct': 1.0, 'gap': 3.0, 'tone': 'up', 'label': '強於大盤'}])
        self.assertEqual(result['bars'][0]['close'], 25.0)   # 圖上的歷史價照常還原

    def test_too_few_bars_drops_only_the_rows_that_need_them(self):
        # 近 N 日要 N＋1 根：21 根有近 5、20 日，沒有近 60 日；20 根只剩近 5 日。
        for count, expected in ((21, [5, 20]), (20, [5])):
            days = self.days(count)
            signals = self.analyze([FLAT] * count, {day: 20000.0 for day in days})['signals']
            self.assertEqual([row['days'] for row in signals['versus_market']['rows']], expected, count)

    def test_no_index_on_the_data_day_means_no_block(self):
        # 加權指數沒有對到這一檔的資料日（或三段的起點都沒有）→ 整塊沒有，清單裡也沒有這一塊。
        for index in ({}, self.index(d1=20000.0, d5=20000.0, d20=20000.0, d60=20000.0), self.index(d0=20000.0)):
            signals = self.analyze(SEVENTY, index)['signals']
            self.assertIsNone(signals['versus_market'], index)
            self.assertEqual(signals['blocks'], [], index)

    def test_otc_stock_is_compared_with_the_same_weighted_index(self):
        result = self.analyze(SEVENTY, self.index(d0=19602.0, d20=25000.0), otc=True)
        self.assertEqual(result['market'], '上櫃')
        self.assertEqual(result['signals']['versus_market']['rows'], [
            {'days': 20, 'stock_pct': -20.8, 'market_pct': -21.59, 'gap': 0.8, 'tone': 'up', 'label': '強於大盤'}])

    def test_etf_is_compared_with_the_same_weighted_index(self):
        result = self.analyze(SEVENTY, self.index(d0=19602.0, d60=19000.0), code='0050', kind='etf')
        self.assertEqual(result['kind'], 'etf')
        self.assertEqual(result['signals']['versus_market']['rows'], [
            {'days': 60, 'stock_pct': -1.0, 'market_pct': 3.17, 'gap': -4.2, 'tone': 'down', 'label': '落後大盤'}])

    def test_a_stock_suspended_inside_the_window_is_compared_over_the_same_dates(self):
        # 72 個交易日裡這一檔停牌兩天（倒數第 3、4 個交易日），所以它最近 5 根的起點是 7 個交易日前。
        # 大盤跟著用同一段：19,602 ÷ 19,800（7 個交易日前）− 1＝−1.00% → 差距 0；
        # 不是拿大盤自己的 5 個交易日前（20,000）去比。
        market_days = self.days(72)
        stock_days = market_days[:68] + market_days[70:]
        index = {market_days[-1]: 19602.0, market_days[-8]: 19800.0, market_days[-6]: 20000.0}
        rows = self.analyze(SEVENTY, index, days=stock_days)['signals']['versus_market']['rows']
        self.assertEqual(rows, [{'days': 5, 'stock_pct': -1.0, 'market_pct': -1.0, 'gap': 0.0, 'tone': None, 'label': None}])

    def test_existing_content_is_untouched_and_a_failure_only_empties_this_block(self):
        normal = self.analyze(SEVENTY, self.index(d0=19602.0, d20=25000.0))
        broken = self.analyze(SEVENTY, RuntimeError('index reader bug'))
        self.assertIsNone(broken['signals'])
        self.assertEqual(set(normal), TOP_KEYS)
        for key in TOP_KEYS - {'signals'}:
            self.assertEqual(broken[key], normal[key], key)

    def test_text_carries_no_reference_site_or_method_wording(self):
        index = self.index(d0=19602.0, d5=19800.0, d20=25000.0, d60=19000.0)
        dumped = json.dumps(self.analyze(SEVENTY, index)['signals'], ensure_ascii=False)
        for word in BANNED_TEXT + ('反推',):
            self.assertNotIn(word, dumped)


def closes_only(closes):
    """只在意收盤的測試用：每一根的漲跌符號與漲跌隨便給（均線排列、爆大量成交區都不看它）。"""
    return [(float(close), '', 0.0) for close in closes]


class StockSignalsMaAlignmentTests(_SignalsFixture, unittest.TestCase):
    """五塊資料的「均線排列」：四個條件固定順序、都是嚴格大於——收盤在 20 日線之上、20 日線在 60 日線之上、
    60 日線在 120 日線之上、今天的 20 日線高於 5 個交易日前的 20 日線。"""

    def alignment(self, closes, **options):
        return self.analyze(closes_only(closes), {}, **options)['signals']

    def test_all_four_hold_is_a_bullish_alignment(self):
        # 130 根，每天漲 1 元（100…229）。收盤 229；20 日線＝(210…229) 平均 219.5；60 日線＝(170…229) 平均 199.5；
        # 120 日線＝(110…229) 平均 169.5；5 個交易日前的 20 日線＝(205…224) 平均 214.5。
        # 229 > 219.5 > 199.5 > 169.5，219.5 > 214.5 → 四個都成立。
        signals = self.alignment([100 + i for i in range(130)])
        self.assertEqual(signals['ma_alignment'], {
            'conditions': [{'key': 'close_above_ma20', 'label': '站上月線', 'met': True},
                           {'key': 'ma20_above_ma60', 'label': '月＞季', 'met': True},
                           {'key': 'ma60_above_ma120', 'label': '季＞半年', 'met': True},
                           {'key': 'ma20_rising', 'label': '月線上彎', 'met': True}],
            'met_count': 4, 'summary': '多頭排列'})
        self.assertEqual(signals['blocks'], ['ma_alignment'])

    def met(self, closes, **options):
        block = self.alignment(closes, **options)['ma_alignment']
        return [condition['met'] for condition in block['conditions']], block['met_count'], block['summary']

    def test_none_hold_is_a_bearish_alignment(self):
        # 130 根，每天跌 1 元（300…171）。收盤 171；20 日線 180.5；60 日線 200.5；120 日線 230.5；5 日前的 20 日線 185.5。
        self.assertEqual(self.met([300 - i for i in range(130)]), ([False, False, False, False], 0, '空頭排列'))

    def test_some_hold_reports_how_many_of_the_four(self):
        # 前 129 根每天漲 1 元（100…228），最後一根跌到 200：
        # 20 日線＝(210…228 的和 4,161＋200) ÷ 20＝218.05 → 收盤 200 不在其上；
        # 60 日線＝(170…228 的和 11,741＋200) ÷ 60＝199.02 → 218.05 > 199.02 成立；
        # 120 日線＝(110…228 的和 20,111＋200) ÷ 120＝169.26 → 成立；5 日前的 20 日線＝(205…224) 平均 214.5 → 成立。
        self.assertEqual(self.met([100 + i for i in range(129)] + [200]), ([False, True, True, True], 3, '4 項成立 3 項'))
        # 前 129 根每天跌 1 元（300…172），最後一根彈到 190：
        # 20 日線＝(172…190 的和 3,439＋190) ÷ 20＝181.45 → 收盤 190 在其上；
        # 60 日線＝(172…230 的和 11,859＋190) ÷ 60＝200.82、120 日線＝(172…290 的和 27,489＋190) ÷ 120＝230.66 → 都不成立；
        # 5 日前的 20 日線＝(176…195) 平均 185.5 → 181.45 沒有比它高。
        self.assertEqual(self.met([300 - i for i in range(129)] + [190]), ([True, False, False, False], 1, '4 項成立 1 項'))

    def test_exactly_equal_does_not_count(self):
        # 前 129 根 100…228，最後一根收 219：20 日線＝(4,161＋219) ÷ 20＝219.00，和收盤剛好相等 → 第一項不成立。
        # 其餘三項：219 > 60 日線 199.33 > 120 日線 169.42，219 > 5 日前的 214.5 → 成立。
        self.assertEqual(self.met([100 + i for i in range(129)] + [219]), ([False, True, True, True], 3, '4 項成立 3 項'))
        # 全部收 100：四條線和收盤都一樣，沒有一項是「嚴格大於」。
        self.assertEqual(self.met([100] * 130), ([False, False, False, False], 0, '空頭排列'))

    def test_rising_compares_with_the_line_five_trading_days_ago(self):
        # 130 根都收 100，只有倒數第 25 根收 110、最後一根收 101。
        # 今天的 20 日線＝(19×100＋101) ÷ 20＝100.05；5 個交易日前的 20 日線（倒數第 25～6 根）＝(19×100＋110) ÷ 20＝100.50
        # → 沒有比它高，「月線上彎」不成立。（和 4 個交易日前的 100.00 比的話會變成成立。）
        # 其餘：收盤 101 > 100.05 成立；60 日線＝6,011 ÷ 60＝100.18，100.05 不在其上；120 日線＝12,011 ÷ 120＝100.09 < 100.18 成立。
        closes = [100] * 105 + [110] + [100] * 23 + [101]
        self.assertEqual(self.met(closes), ([True, False, True, False], 2, '4 項成立 2 項'))

    def test_fewer_than_125_bars_means_no_block(self):
        short = self.alignment([100 + i for i in range(124)])
        self.assertIsNone(short['ma_alignment'])
        self.assertEqual(short['blocks'], [])
        self.assertEqual(self.met([100 + i for i in range(125)]), ([True, True, True, True], 4, '多頭排列'))

    def test_uses_the_same_adjusted_moving_averages_as_the_analysis(self):
        # 第 101 根一拆四：之前實際成交價是 400…796（每天 +4），之後是 200…229（每天 +1）。
        # 還原後整段就是 100…229 每天漲 1 元 → 和第一個測試相同，四個都成立。
        # 用實際成交價去算的話，120 日線（含 90 根四倍價）會遠高於 60 日線，就不會是多頭排列。
        days = self.days(130)
        closes = [4 * (100 + i) for i in range(100)] + [100 + i for i in range(100, 130)]
        split = {'date': days[100], 'code': '9999', 'type': '變更面額', 'close_before': 796.0, 'reference': 199.0}
        result = self.analyze(closes_only(closes), {}, capital_actions={'9999': [split]})
        self.assertEqual(result['signals']['ma_alignment']['summary'], '多頭排列')
        self.assertEqual((result['indicators']['ma20'], result['indicators']['ma60'], result['indicators']['ma120']),
                         (219.5, 199.5, 169.5))


def bar(close=100.0, high=None, low=None, volume=1_000_000):
    """爆大量成交區測試用的一根：平常日收 100、高低 ±1、成交 1,000 張。"""
    return (float(close), '', 0.0, float(close + 1 if high is None else high), float(close - 1 if low is None else low), volume)


class StockSignalsHeavyZonesTests(_SignalsFixture, unittest.TestCase):
    """五塊資料的「爆大量成交區」：最近 240 個交易日裡，成交量達到前 60 個交易日均量 3 倍的日子（爆量日），
    當天最低到最高價是一段，重疊的合併成一區；每一區標出在現價上方、下方或區內與離現價多遠。"""

    def zones(self, rows, **options):
        return self.analyze(rows, {}, **options)['signals']

    def test_a_heavy_volume_day_becomes_a_zone_below_the_price(self):
        # 70 根：前 60 根每天 1,000 張；第 61 根 3,000 張（剛好是前 60 日均量 1,000 張的 3 倍），當天低 80、高 90、收 85；
        # 之後 9 根回到 100、1,000 張（它們的前 60 日均量是 1,033 張，1,000 張不到 3 倍）。
        # 現價 100 在這一區上面 → 這一區在現價下方，距離＝區的高價 90 ÷ 100 − 1＝−10.0%。
        days = self.days(70)
        signals = self.zones([bar()] * 60 + [bar(85, high=90, low=80, volume=3_000_000)] + [bar()] * 9)
        self.assertEqual(signals['heavy_zones'], {
            'close': 100.0,
            'zones': [{'low': 80.0, 'high': 90.0, 'position': 'below', 'distance_pct': -10.0, 'days': [days[60]]}],
            # 小圖用：收盤、成交量（張）、爆量日在這兩串裡的位置。
            'chart': {'closes': [100] * 60 + [85] + [100] * 9, 'volumes': [1000] * 60 + [3000] + [1000] * 9, 'heavy': [60]}})
        self.assertEqual(signals['blocks'], ['heavy_zones'])

    def test_no_heavy_volume_day_means_no_block(self):
        spike = bar(85, high=90, low=80, volume=5_000_000)
        for label, rows in (
                ('每天都是 1,000 張', [bar()] * 70),
                # 第 61 根 2,999,999 股，差 1 股不到前 60 日均量（1,000,000 股）的 3 倍。
                ('差一點不到 3 倍', [bar()] * 60 + [bar(85, high=90, low=80, volume=2_999_999)] + [bar()] * 9),
                # 第 60 根放 5 倍量，但它之前只有 59 個交易日 → 不判斷。
                ('之前不足 60 個交易日', [bar()] * 59 + [spike] + [bar()] * 10)):
            signals = self.zones(rows)
            self.assertIsNone(signals['heavy_zones'], label)
            self.assertEqual(signals['blocks'], [], label)

    HEAVY = 10_000_000   # 10,000 張：就算前 60 日裡已經有三個這樣的爆量日（均量 1,450 張），仍超過 3 倍

    def heavy(self, close, low, high):
        return bar(close, high=high, low=low, volume=self.HEAVY)

    def test_overlapping_days_merge_and_separate_ones_do_not(self):
        # 四個爆量日（第 61、63、65、67 根）：95～97、88～95、80～90、97.5～99。
        # 由低到高排：80～90 → 88～95 的低價在 90 以內，併成 80～95 → 95～97 的低價剛好等於 95，也算以內，併成 80～97
        # → 97.5～99 的低價高過 97，不重疊，自己一區。
        # 現價 100：兩區都在下方，距離＝99 ÷ 100 − 1＝−1.0%、97 ÷ 100 − 1＝−3.0%。清單由高到低；形成的日子由舊到新。
        days = self.days(70)
        rows = ([bar()] * 60 + [self.heavy(96, 95, 97), bar(), self.heavy(90, 88, 95), bar(), self.heavy(85, 80, 90), bar(),
                                self.heavy(98, 97.5, 99)] + [bar()] * 3)
        block = self.zones(rows)['heavy_zones']
        self.assertEqual(block['zones'], [
            {'low': 97.5, 'high': 99.0, 'position': 'below', 'distance_pct': -1.0, 'days': [days[66]]},
            {'low': 80.0, 'high': 97.0, 'position': 'below', 'distance_pct': -3.0, 'days': [days[60], days[62], days[64]]}])
        self.assertEqual(block['chart']['heavy'], [60, 62, 64, 66])

    def test_zone_above_inside_and_below_the_price_with_distances(self):
        # 現價 100。上方那一區 113.37～120：距離＝區的低價 113.37 ÷ 100 − 1＝+13.37% → +13.4。
        # 99～105 把現價包在裡面 → 現價在區內，距離 0。下方那一區 90～97.24：距離＝區的高價 97.24 ÷ 100 − 1＝−2.76% → −2.8。
        rows = ([bar()] * 60 + [self.heavy(115, 113.37, 120), bar(), self.heavy(100, 99, 105), bar(), self.heavy(95, 90, 97.24)]
                + [bar()] * 5)
        self.assertEqual([(zone['low'], zone['high'], zone['position'], zone['distance_pct'])
                          for zone in self.zones(rows)['heavy_zones']['zones']],
                         [(113.37, 120.0, 'above', 13.4), (99.0, 105.0, 'inside', 0.0), (90.0, 97.24, 'below', -2.8)])
        # 現價剛好踩在區的邊上也算在區內。
        for low, high in ((100, 110), (90, 100)):
            zone = self.zones([bar()] * 60 + [self.heavy(100, low, high)] + [bar()] * 9)['heavy_zones']['zones'][0]
            self.assertEqual((zone['position'], zone['distance_pct']), ('inside', 0.0), (low, high))

    def test_only_the_latest_240_trading_days_are_looked_at(self):
        # 行情庫有 310 根。倒數第 241 根（60～70）與倒數第 240 根（80～90）都爆量：只有後者在最近 240 個交易日裡。
        # 小圖的兩串也是最近 240 筆，爆量日在第一筆。
        rows = [bar()] * 69 + [self.heavy(65, 60, 70), self.heavy(85, 80, 90)] + [bar()] * 239
        block = self.zones(rows)['heavy_zones']
        self.assertEqual([(zone['low'], zone['high']) for zone in block['zones']], [(80.0, 90.0)])
        self.assertEqual((len(block['chart']['closes']), len(block['chart']['volumes']), block['chart']['heavy']), (240, 240, [0]))
        self.assertEqual((block['chart']['closes'][:2], block['chart']['volumes'][:2]), ([85, 100], [10000, 1000]))

    def test_split_stock_uses_adjusted_prices_and_volumes(self):
        # 第 66 根一拆四。之前實際成交價是 400 上下、每天 250 張；之後是 100 上下、每天 1,000 張。
        # 還原後整段都是 100 上下、1,000 張，所以拆分那天的張數變四倍不是爆量。
        # 真正的爆量日是第 61 根：實際成交 750 張（還原後 3,000 張＝3 倍），實際低 320、高 360 → 還原後 80～90。
        days = self.days(70)
        before = (400.0, '', 0.0, 404.0, 396.0, 250_000)
        rows = ([before] * 60 + [(340.0, '', 0.0, 360.0, 320.0, 750_000)] + [before] * 4
                + [(100.0, 'X', None, 101.0, 99.0, 1_000_000)] + [bar()] * 4)
        split = {'date': days[65], 'code': '9999', 'type': '變更面額', 'close_before': 400.0, 'reference': 100.0}
        block = self.zones(rows, capital_actions={'9999': [split]})['heavy_zones']
        self.assertEqual(block['zones'], [{'low': 80.0, 'high': 90.0, 'position': 'below', 'distance_pct': -10.0, 'days': [days[60]]}])
        self.assertEqual(block['chart'], {'closes': [100] * 60 + [85] + [100] * 9, 'volumes': [1000] * 60 + [3000] + [1000] * 9,
                                          'heavy': [60]})


# 漲停動能的預期值（分數、級距）都抄自凍結的算法版本檔 twon_analysis/limit_up_momentum_20261116.json 的換算表；
# 每個例子成立哪幾個條件是手算的（寫在各測試的註解）。六個級距的歷史比例是 0.4、0.8、2.6、3.1、5.1、17.6，全部平均 1.7。
def momentum(points, band, rate):
    return {'points': points, 'band': band, 'band_rate_pct': rate, 'average_rate_pct': 1.7, 'algorithm': 'limit-up-momentum-20261116'}


class StockSignalsLimitUpMomentumTests(_SignalsFixture, unittest.TestCase):
    """五塊資料的「漲停動能」（本人 2026-11-16 共識，規格 .scratch/stock-signals/spec.md）：上市、上櫃普通股每天收盤後
    依八個條件查固定的換算表，給 0～99 分、級距（1～6）、這個級距的歷史比例、全部平均與算法版本。"""

    def flat_index(self, count=70):
        return {day: 20000.0 for day in self.days(count)}

    def test_nothing_happening_scores_from_the_frozen_table(self):
        # 70 根都收 100、每天最高 101、成交金額一樣、大盤不動、沒有投信買超：八個條件都不成立。
        # 換算表「全不成立」＝14 分、第 2 級（歷史比例 0.8%）。
        signals = self.analyze([FLAT] * 70, self.flat_index())['signals']
        self.assertEqual(signals['limit_up_momentum'], momentum(14, 2, 0.8))
        self.assertEqual(signals['blocks'], ['limit_up_momentum', 'versus_market'])

    @staticmethod
    def after_a_fall(today):
        """70 根：前 50 根收 150，倒數第 20 根跌到 100 之後持平，最後一根是 today（收盤、漲跌符號、漲跌）。
        除了「今天漲多少」以外的條件都不成立：前 20 日最高是 151（還在 150 那一根）、20 日線約 100 低於 60 日線約 133、
        近 20 日是下跌的（大盤不動）、前 20 日沒有漲停。"""
        return [(150.0, '', 0.0)] * 50 + [(100.0, '-', -50.0)] + [FLAT] * 18 + [today]

    def test_limit_up_today_means_a_rise_of_at_least_nine_and_a_half_percent(self):
        # 只有條件 ① 成立 → 換算表 97 分、第 5 級（5.1%）。剛好 +9.5%（100 → 109.5）就算漲停。
        for today in ((110.0, '+', 10.0), (109.5, '+', 9.5)):
            signals = self.analyze(self.after_a_fall(today), self.flat_index())['signals']
            self.assertEqual(signals['limit_up_momentum'], momentum(97, 5, 5.1), today)

    def test_a_rise_of_five_percent_short_of_limit_up_is_its_own_condition(self):
        # 只有條件 ② 成立 → 換算表 80 分、第 3 級（2.6%）。+9.4% 還不算漲停、剛好 +5% 就算；+4.9% 兩個都不成立（14 分）。
        for today, expected in (((109.4, '+', 9.4), momentum(80, 3, 2.6)), ((105.0, '+', 5.0), momentum(80, 3, 2.6)),
                                ((104.9, '+', 4.9), momentum(14, 2, 0.8))):
            signals = self.analyze(self.after_a_fall(today), self.flat_index())['signals']
            self.assertEqual(signals['limit_up_momentum'], expected, today)

    def test_turnover_twice_its_prior_twenty_day_average(self):
        # 只有條件 ③ 成立 → 換算表 77 分、第 2 級（0.8%）。比的是「前一日為止 20 日」的平均成交金額，不含今天。
        def rows(today, twenty_back=100, twenty_one_back=100):
            turnovers = [100] * 48 + [twenty_one_back, twenty_back] + [100] * 19 + [today]
            return [(100.0, '', 0.0, 101.0, 99.0, 1000, turnover) for turnover in turnovers]
        cases = ((rows(200), momentum(77, 2, 0.8)),                       # 均值 100，今天剛好 2 倍
                 (rows(199), momentum(14, 2, 0.8)),                       # 差一點
                 (rows(200, twenty_one_back=100_000), momentum(77, 2, 0.8)),   # 21 個交易日前的不算進均值
                 (rows(399, twenty_back=2100), momentum(14, 2, 0.8)),     # 20 個交易日前的算：均值 200，要 400
                 (rows(400, twenty_back=2100), momentum(77, 2, 0.8)))
        for index, (case, expected) in enumerate(cases):
            self.assertEqual(self.analyze(case, self.flat_index())['signals']['limit_up_momentum'], expected, index)

    def rising_index(self, count=70):
        """大盤近 20 個交易日漲 5%（20,000 → 21,000），比下面這些例子裡的個股都多：條件 ⑥ 不成立。"""
        days = self.days(count)
        return {day: 21000.0 if day == days[-1] else 20000.0 for day in days}

    def test_close_above_the_highest_high_of_the_prior_twenty_days(self):
        # 49 根收 150，20 個交易日前跌到 100 之後持平（每天最高 101），今天收 102（+2%）：只有條件 ④ 成立
        # → 換算表 53 分、第 2 級（0.8%）。21 個交易日前那根最高 151 不在範圍裡；20 日線約 100 低於 60 日線約 132。
        def rows(today, high_twenty_back=101.0):
            return ([(150.0, '', 0.0)] * 49 + [(100.0, '-', -50.0, high_twenty_back, 99.0, 1000)] + [FLAT] * 19 + [today])
        cases = ((rows((102.0, '+', 2.0)), momentum(53, 2, 0.8)),
                 (rows((101.0, '+', 1.0)), momentum(14, 2, 0.8)),                          # 剛好等於前高不算
                 (rows((102.0, '+', 2.0), high_twenty_back=103.0), momentum(14, 2, 0.8)))  # 20 個交易日前那根的最高要算
        for index, (case, expected) in enumerate(cases):
            self.assertEqual(self.analyze(case, self.rising_index())['signals']['limit_up_momentum'], expected, index)

    def test_close_above_the_twenty_day_line_which_is_above_the_sixty_day_line(self):
        # 48 根收 90，21 個交易日前漲到 100 之後持平，中間有一天最高 120（所以條件 ④ 不成立），今天收 101（+1%）。
        # 20 日線＝(19×100＋101)÷20＝100.05；60 日線＝(38×90＋21×100＋101)÷60＝93.68 → 101 > 100.05 > 93.68，
        # 只有條件 ⑤ 成立 → 換算表 70 分、第 2 級（0.8%）。今天收 100（沒動）就只是等於 20 日線，不算。
        def rows(today):
            return ([(90.0, '', 0.0)] * 48 + [(100.0, '+', 10.0)] + [FLAT] * 11 + [(100.0, '', 0.0, 120.0, 99.0, 1000)]
                    + [FLAT] * 8 + [today])
        cases = ((rows((101.0, '+', 1.0)), momentum(70, 2, 0.8)),
                 (rows(FLAT), momentum(14, 2, 0.8)),
                 # 收盤在 20 日線之上、但 20 日線（約 100）在 60 日線（約 133）之下：不算。
                 (self.after_a_fall((101.0, '+', 1.0)), momentum(14, 2, 0.8)))
        for index, (case, expected) in enumerate(cases):
            self.assertEqual(self.analyze(case, self.rising_index())['signals']['limit_up_momentum'], expected, index)

    def test_the_two_lines_are_exactly_twenty_and_sixty_days_long(self):
        # 四個例子都只有條件 ⑤ 成立（70 分），而且線的天數差一天結果就不同。大盤近 20 日 +5%，條件 ⑥ 都不成立。
        low, mid, high = (90.0, '', 0.0), (100.2, '', 0.0), (200.0, '', 0.0)
        cases = (
            # 20 日線含 19 個交易日前那根 96：(96＋18×100＋99.9)÷20＝99.795 < 收盤 99.9。只算 19 天的話是 99.995，不成立。
            [low] * 48 + [(100.0, '+', 10.0), FLAT, (96.0, '-', -4.0), (100.0, '+', 4.0)] + [FLAT] * 17 + [(99.9, '-', -0.1)],
            # 20 日線不含 20 個交易日前那根 104：(19×100＋100.1)÷20＝100.005 < 收盤 100.1。算 21 天的話是 100.195，不成立。
            [low] * 48 + [(104.0, '+', 14.0), (104.0, '', 0.0), (100.0, '-', -4.0)] + [FLAT] * 18 + [(100.1, '+', 0.1)],
            # 60 日線含 59 個交易日前那根 40：(40＋39×100.2＋19×100＋100.1)÷60＝99.13 < 20 日線 100.005。
            # 只算 59 天的話是 100.13，不成立。
            [mid] * 10 + [(40.0, '-', -60.2), (100.2, '+', 60.2)] + [mid] * 38 + [(100.0, '-', -0.2)] + [FLAT] * 18
            + [(100.1, '+', 0.1)],
            # 60 日線不含 60 個交易日前那根 200：(59×99＋100)÷60＝99.017 < 20 日線 (19×99＋100)÷20＝99.05。
            # 算 61 天的話是 100.67，不成立。（今天收 100 剛好等於前一天的最高 100，條件 ④ 不成立。）
            [high] * 10 + [(99.0, '-', -101.0)] + [(99.0, '', 0.0)] * 58 + [(100.0, '+', 1.0)],
        )
        for index, rows in enumerate(cases):
            self.assertEqual(len(rows), 70, index)
            self.assertEqual(self.analyze(rows, self.rising_index())['signals']['limit_up_momentum'], momentum(70, 2, 0.8), index)

    def test_twenty_day_gain_beats_the_index_over_the_same_days(self):
        # 這一檔 70 根都收 100（近 20 日 0%）。大盤只有「20 個交易日前」那一天是 21,000、其餘 20,000：大盤近 20 日是跌的，
        # 只有條件 ⑥ 成立 → 換算表 3 分、第 1 級（0.4%）。把 21,000 放在 19 或 21 個交易日前就不影響（大盤 0%，一樣不算贏）。
        days = self.days(70)
        for back, expected in ((20, momentum(3, 1, 0.4)), (19, momentum(14, 2, 0.8)), (21, momentum(14, 2, 0.8))):
            index = {**self.flat_index(), days[-1 - back]: 21000.0}
            self.assertEqual(self.analyze([FLAT] * 70, index)['signals']['limit_up_momentum'], expected, back)

    def test_investment_trusts_buying_three_trading_days_in_a_row(self):
        # 這一檔 70 根都收 100、大盤不動。投信最近三個交易日都買超：只有條件 ⑦ 成立 → 換算表 1 分、第 1 級（0.4%）。
        # 只連買兩天、中間一天賣超或沒有資料、今天的法人資料還沒有、法人資料讀取出錯：條件 ⑦ 都算不成立（14 分），這一塊照給。
        days = self.days(70)

        def trust(*nets):
            """最近幾個交易日的投信買賣超（由舊到新）；None 是那一天沒有這一檔的資料。"""
            return [{'date': day, 'foreign': 0, 'trust': net, 'dealer': 0, 'total': net}
                    for day, net in zip(days[-len(nets):], nets) if net is not None]
        cases = ((trust(1, 5000, 1000), momentum(1, 1, 0.4)),
                 (trust(-7000, 1000, 5000, 1000), momentum(1, 1, 0.4)),      # 更早的賣超不影響
                 (trust(0, 5000, 1000), momentum(14, 2, 0.8)),
                 (trust(1000, -1, 1000), momentum(14, 2, 0.8)),
                 (trust(1000, None, 1000), momentum(14, 2, 0.8)),
                 (trust(1000, 1000, 1000, None), momentum(14, 2, 0.8)),
                 (sqlite3.OperationalError('讀不到法人資料'), momentum(14, 2, 0.8)))
        for index, (rows, expected) in enumerate(cases):
            signals = self.analyze([FLAT] * 70, self.flat_index(), institutional=rows)['signals']
            self.assertEqual(signals['limit_up_momentum'], expected, index)
            self.assertEqual(signals['failed'], [], index)

    def test_a_limit_up_within_the_prior_twenty_days_not_counting_today(self):
        # 都收 100，其中一天漲停到 110、隔天跌回 100（大盤不動）。漲停那一天在前 20 個交易日內：只有條件 ⑧ 成立
        # → 換算表 87 分、第 4 級（3.1%）。在 21 個交易日前就不算（14 分）。今天自己漲停不算在這一項（見條件 ① 的測試）。
        def rows(back):
            spike = 69 - back
            return [FLAT] * spike + [(110.0, '+', 10.0), (100.0, '-', -10.0)] + [FLAT] * (68 - spike)
        for back, expected in ((20, momentum(87, 4, 3.1)), (1, momentum(87, 4, 3.1)), (21, momentum(14, 2, 0.8))):
            self.assertEqual(self.analyze(rows(back), self.flat_index())['signals']['limit_up_momentum'], expected, back)

    def test_only_common_stocks_with_sixty_one_bars_get_it(self):
        # 上市、上櫃普通股都有（上櫃：本人 2026-11-16 看過 09 票驗證報告，定「開，沿用上市的比例」）。
        # ETF、特別股、日線不足 61 根：整塊沒有，也不算「算不出來」。61 根就有。
        cases = (({}, 70, momentum(14, 2, 0.8)), ({'otc': True}, 70, momentum(14, 2, 0.8)),
                 ({'kind': 'etf', 'code': '0050'}, 70, None), ({'kind': 'etf', 'code': '006201', 'otc': True}, 70, None),
                 ({'kind': 'preferred'}, 70, None), ({}, 60, None), ({'otc': True}, 60, None), ({}, 61, momentum(14, 2, 0.8)))
        for options, count, expected in cases:
            signals = self.analyze([FLAT] * count, self.flat_index(count), **options)['signals']
            self.assertEqual(signals['limit_up_momentum'], expected, (options, count))
            self.assertEqual('limit_up_momentum' in signals['blocks'], expected is not None, (options, count))
            self.assertEqual(signals['failed'], [], (options, count))

    def test_otc_stocks_use_the_same_algorithm_and_the_listed_rates(self):
        # 上櫃沒有另外一套：同一個算法版本、同一張換算表、同樣和加權指數比，歷史比例與全部平均也是上市那一組
        # （本人 2026-11-16 選「沿用上市的比例」；上櫃自己的比例只在驗證報告裡）。今天漲停 → 97 分、第 5 級、5.1%、平均 1.7%。
        today = (110.0, '+', 10.0)
        listed = self.analyze(self.after_a_fall(today), self.flat_index())['signals']['limit_up_momentum']
        otc = self.analyze(self.after_a_fall(today), self.flat_index(), otc=True)['signals']['limit_up_momentum']
        self.assertEqual(otc, momentum(97, 5, 5.1))
        self.assertEqual(otc, listed)

    def test_without_the_index_on_the_data_date_or_twenty_days_back_there_is_no_block(self):
        # 加權指數沒有對到資料日，或沒有 20 個交易日前那一天（條件 ⑥ 沒得比）：整塊不給，不拿別的日子代替；不算「算不出來」。
        days = self.days(70)
        for missing in (days[-1], days[-21]):
            index = {day: 20000.0 for day in days if day != missing}
            signals = self.analyze([FLAT] * 70, index)['signals']
            self.assertIsNone(signals['limit_up_momentum'], missing)
            self.assertNotIn('limit_up_momentum', signals['blocks'])
            self.assertEqual(signals['failed'], [], missing)
        # 加權指數讀取出錯也一樣。
        signals = self.analyze([FLAT] * 70, OSError('讀不到加權指數'))['signals']
        self.assertEqual((signals['limit_up_momentum'], signals['failed']), (None, []))

    def test_days_without_a_comparable_price_count_as_no_move_and_leave_no_gap(self):
        # 倒數第 10 根除息 10 元：收盤 100 → 90，官方標不比價（X）。之後持平（每天最高 91），今天收 91.5（+1.5，+1.67%）。
        # 用漲跌幅連乘的收盤線看：除息那天不算下跌，所以今天高於前 20 日的最高（除息前的 101 換算後是 1.01 倍、
        # 除息後的 91 是 1.0111 倍，今天是 1.0167 倍）、也在 20 日線與 60 日線之上 → 條件 ④⑤ 成立，換算表 80 分、第 3 級。
        # （直接拿實際成交價比的話：91.5 低於除息前的 101、也低於 20 日均價 95.1，兩個都不成立，會變成 14 分。）
        rows = [FLAT] * 60 + [(90.0, 'X', None)] + [(90.0, '', 0.0)] * 8 + [(91.5, '+', 1.5)]
        self.assertEqual(self.analyze(rows, self.rising_index())['signals']['limit_up_momentum'], momentum(80, 3, 2.6))
        # 和驗證時相同：不比價的那一天漲跌一律算 0。就算那天相對官方除息參考價（90）其實漲了 10%（收 99），
        # 也不算「前 20 日內曾經漲停」——八個條件都不成立，14 分。
        days = self.days(70)
        rows = [FLAT] * 60 + [(99.0, 'X', None, 100.0, 98.0, 1000)] + [(99.0, '', 0.0, 100.0, 98.0, 1000)] * 9
        event = {'date': days[60], 'code': '9999', 'type': '除息', 'close_before': 100.0, 'reference': 90.0, 'value': 10.0}
        signals = self.analyze(rows, self.flat_index(), ex_events=[event])['signals']
        self.assertEqual(signals['limit_up_momentum'], momentum(14, 2, 0.8))

    def test_combinations_read_the_frozen_table(self):
        # 150 跌到 100 之後（條件 ④⑤⑥ 都不成立），前 20 日內有一天漲停又跌回，今天再漲停：條件 ①⑧ 成立 → 99 分、第 6 級（17.6%）。
        base = self.after_a_fall((110.0, '+', 10.0))
        both = base[:55] + [(110.0, '+', 10.0), (100.0, '-', -10.0)] + base[57:]
        self.assertEqual(self.analyze(both, self.flat_index())['signals']['limit_up_momentum'], momentum(99, 6, 17.6))
        # 再加上今天成交金額放大（條件 ①③⑧）：這個組合四捨五入後是 100，記 99。
        loud = both[:-1] + [(110.0, '+', 10.0, 111.0, 109.0, 1000, 2)]
        self.assertEqual(self.analyze(loud, self.flat_index())['signals']['limit_up_momentum'], momentum(99, 6, 17.6))
        # 今天漲停、投信連買三天、近 20 日贏大盤（條件 ①⑥⑦）：91 分、第 4 級（3.1%）——分數和級距各查各的。
        days = self.days(70)
        trust = [{'date': day, 'foreign': 0, 'trust': 1000, 'dealer': 0, 'total': 1000} for day in days[-3:]]
        falling_index = {**self.flat_index(), days[-1]: 10000.0}     # 大盤近 20 日 −50%，這一檔 −26.7%
        signals = self.analyze(base, falling_index, institutional=trust)['signals']
        self.assertEqual(signals['limit_up_momentum'], momentum(91, 4, 3.1))

    def test_a_block_that_cannot_be_computed_drops_only_itself_and_is_named(self):
        # 某一天的成交金額不是數字（資料壞掉）：只少漲停動能這一塊並記進 failed，和大盤比照常。
        rows = [(100.0, '', 0.0, 101.0, 99.0, 1000, '壞掉' if i == 60 else 100) for i in range(70)]
        signals = self.analyze(rows, self.flat_index())['signals']
        self.assertIsNone(signals['limit_up_momentum'])
        self.assertEqual((signals['blocks'], signals['failed']), (['versus_market'], ['limit_up_momentum']))


class StockSignalsMarginMaintenanceTests(_SignalsFixture, unittest.TestCase):
    """五塊資料的「融資維持率（估）」（本人 2026-11-16 共識，規格 .scratch/stock-signals/spec.md）：有融資餘額的上市股票，
    每天從起點（2025-11-03）把保留的個股融資原件重播一次，同時追兩種成本——賣出與現金償還時按比例減（成本維持平均），
    或最近買的先減——各算一個維持率＝收盤 ÷（成本 × 融資成數六成），取小的當下限、大的當上限。只給範圍與資料日。"""

    START = '2025-11-03'   # 起點日期是固定的；測試的融資原件都從這一天開始

    @staticmethod
    def dates(count):
        """行情 30 根，最後 count 根落在起點（2025-11-03）之後：回傳（這一檔的交易日、有融資原件的那幾天）。"""
        from datetime import date, timedelta
        first = date(2025, 11, 3)
        days = [(first + timedelta(days=i)).isoformat() for i in range(count - 30, count)]
        return days, days[-count:]

    def estimate(self, margin, *, closes=None, **options):
        days, _ = self.dates(len(margin))
        rows = [FLAT] * (30 - len(margin)) + [(close, '', 0.0) for close in (closes or [100.0] * len(margin))]
        return self.analyze(rows, {}, days=days, margin=margin, **options)['signals']

    def test_range_comes_from_two_ways_of_reducing_the_cost_when_holders_sell(self):
        # 第 1 天買 100 張、均價 100；第 2 天再買 100 張、均價 120 → 兩種成本都是 110。
        # 第 3 天賣出 50 張、現金償還 50 張（共減 100 張）：
        #   按比例減 → 成本仍是 110；最近買的先減 → 120 的那 100 張走了，剩 100 的 → 成本 100。
        # 第 3 天收 132：132 ÷（110 × 0.6）＝200%；132 ÷（100 × 0.6）＝220% → 範圍 200～220。
        # 全市場核對：混合成本 100＋10×0.5508671…＝105.5087，× 100 張 × 1,000 股 × 0.6＝6,330,520 元，官方給 6,331 仟元。
        _, held = self.dates(3)
        margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100)}, {'9999': (100.0, 100.0)}),
                  margin_day(held[1], {'9999': (100, 0, 0, 100, 200)}, {'9999': (120.0, 120.0)}),
                  margin_day(held[2], {'9999': (0, 50, 50, 200, 100)}, {'9999': (132.0, 130.0)}, official_thousand=6331)]
        signals = self.estimate(margin, closes=[100.0, 120.0, 132.0])
        self.assertEqual(signals['margin_maintenance'], {'low_pct': 200, 'high_pct': 220, 'data_date': held[2]})
        self.assertEqual(signals['blocks'], ['margin_maintenance'])
        self.assertEqual(signals['failed'], [])

    def test_lower_bound_stays_below_upper_bound_when_recent_buys_were_the_cheap_ones(self):
        # 先買 100 張均價 120、再買 100 張均價 100（平均 110），第 3 天賣 100 張：
        #   按比例減 → 110；最近買的先減 → 100 的走了，剩 120 的 → 這一次「最近買的先減」的成本反而比較高。
        # 收 99：99 ÷ 66＝150%；99 ÷ 72＝137.5% → 四捨五入 138。下限是小的那個：138～150。
        # 全市場核對：混合成本 0.5508671×110＋0.4491329×120＝114.4913，× 100,000 股 × 0.6＝6,869,480 元 → 6,869 仟元。
        _, held = self.dates(3)
        margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100)}, {'9999': (120.0, 120.0)}),
                  margin_day(held[1], {'9999': (100, 0, 0, 100, 200)}, {'9999': (100.0, 100.0)}),
                  margin_day(held[2], {'9999': (0, 100, 0, 200, 100)}, {'9999': (99.0, 99.0)}, official_thousand=6869)]
        signals = self.estimate(margin, closes=[120.0, 100.0, 99.0])
        self.assertEqual(signals['margin_maintenance'], {'low_pct': 138, 'high_pct': 150, 'data_date': held[2]})

    def test_balance_that_was_already_there_at_the_start_is_costed_with_the_fixed_start_factor(self):
        # 起點那天就有 100 張、當天沒有買進：這些餘額沒有來歷，成本＝當天均價 100 × 起點係數 0.9369126317402282＝93.69。
        # 收 100：100 ÷（93.69126 × 0.6）＝177.89% → 178～178。核對：100,000 股 × 93.69126 × 0.6＝5,621,476 元 → 5,621 仟元。
        _, held = self.dates(1)
        margin = [margin_day(held[0], {'9999': (0, 0, 0, 100, 100)}, {'9999': (100.0, 100.0)}, official_thousand=5621)]
        self.assertEqual(held[0], self.START)
        self.assertEqual(self.estimate(margin)['margin_maintenance'], {'low_pct': 178, 'high_pct': 178, 'data_date': held[0]})

    def test_no_block_while_the_balance_is_zero_and_a_fresh_start_after_everyone_sold(self):
        # 9999：第 1 天買 100 張均價 100，第 2 天全部賣光（餘額 0）→ 第 2 天沒有這一塊。
        # 8888 同一天還有 100 張（成本 100），所以當天的全市場核對是過的：100,000 股 × 100 × 0.6＝6,000 仟元。
        _, held = self.dates(3)
        other = {'8888': (0, 0, 0, 100, 100)}
        margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100), '8888': (100, 0, 0, 0, 100)},
                             {'9999': (100.0, 100.0), '8888': (100.0, 100.0)}),
                  margin_day(held[1], {'9999': (0, 100, 0, 100, 0), **other}, {'9999': (90.0, 90.0), '8888': (100.0, 100.0)},
                             official_thousand=6000)]
        sold_out = self.estimate(margin, closes=[100.0, 90.0])
        self.assertIsNone(sold_out['margin_maintenance'])
        self.assertEqual((sold_out['blocks'], sold_out['failed']), ([], []))
        # 同一份資料看 8888：100 ÷ 60＝166.67% → 167～167（核對有過，9999 沒有是因為餘額為零）。
        self.assertEqual(self.estimate(margin, code='8888')['margin_maintenance'],
                         {'low_pct': 167, 'high_pct': 167, 'data_date': held[1]})
        # 第 3 天 9999 重新買 100 張均價 80：之前的成本不留，兩種成本都是 80。收 96：96 ÷ 48＝200%。
        # 核對：（100×100＋100×80）× 1,000 × 0.6＝10,800 仟元。
        margin.append(margin_day(held[2], {'9999': (100, 0, 0, 0, 100), **other}, {'9999': (96.0, 80.0), '8888': (100.0, 100.0)},
                                 official_thousand=10800))
        self.assertEqual(self.estimate(margin, closes=[100.0, 90.0, 96.0])['margin_maintenance'],
                         {'low_pct': 200, 'high_pct': 200, 'data_date': held[2]})

    def test_official_zero_balance_clears_the_position_even_if_the_sales_do_not_add_up(self):
        # 第 1 天買 100 張均價 100；第 2 天只賣 40 張，但官方今日餘額是 0（帳對不上）：以官方為準，部位清空。
        # 第 3 天買 100 張均價 80、收 96：兩種成本都是 80 → 96 ÷ 48＝200%。
        # （沒有清空的話會留著 60 張成本 100，平均變 87.5，算出來是 183%。）核對：100,000 股 × 80 × 0.6＝4,800 仟元。
        _, held = self.dates(3)
        margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100)}, {'9999': (100.0, 100.0)}),
                  margin_day(held[1], {'9999': (0, 40, 0, 100, 0)}, {'9999': (90.0, 90.0)}),
                  margin_day(held[2], {'9999': (100, 0, 0, 0, 100)}, {'9999': (96.0, 80.0)}, official_thousand=4800)]
        self.assertEqual(self.estimate(margin, closes=[100.0, 90.0, 96.0])['margin_maintenance'],
                         {'low_pct': 200, 'high_pct': 200, 'data_date': held[2]})

    def test_estimated_position_is_scaled_to_the_official_balance_every_day(self):
        # 第 1 天買 100 張均價 100；第 2 天買 100 張均價 120，但官方今日餘額只有 100 張（不是 200）：
        # 估計的部位等比例對齊 → 50 張成本 100、50 張成本 120。第 3 天賣 50 張（官方餘額 50）：
        #   按比例減 → 110；最近買的先減 → 120 的 50 張走了，剩 100 的 → 100。收 132 → 200～220。
        # （沒有對齊的話，最近買的先減會剩 100 張 100、50 張 120，成本 106.67，上限會是 206。）
        # 核對：50 張 × 1,000 × 105.5087 × 0.6＝3,165,260 元 → 3,165 仟元。
        _, held = self.dates(3)
        margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100)}, {'9999': (100.0, 100.0)}),
                  margin_day(held[1], {'9999': (100, 0, 0, 100, 100)}, {'9999': (120.0, 120.0)}),
                  margin_day(held[2], {'9999': (0, 50, 0, 100, 50)}, {'9999': (132.0, 130.0)}, official_thousand=3165)]
        self.assertEqual(self.estimate(margin, closes=[100.0, 120.0, 132.0])['margin_maintenance'],
                         {'low_pct': 200, 'high_pct': 220, 'data_date': held[2]})

    def three_days(self, official_thousand=6331):
        return three_margin_days(self.dates(3)[1], official_thousand)

    THREE_CLOSES = [100.0, 120.0, 132.0]

    def test_only_listed_stocks_get_an_estimate(self):
        # 同樣三天的融資資料：上市普通股有 200～220；上櫃（等上櫃融資資料那一張票）與 ETF（第一版不顯示）整塊沒有。
        for options, expected in (({}, {'low_pct': 200, 'high_pct': 220}), ({'otc': True}, None), ({'kind': 'etf'}, None)):
            signals = self.estimate(self.three_days(), closes=self.THREE_CLOSES, **options)
            block = signals['margin_maintenance']
            self.assertEqual(block and {key: block[key] for key in ('low_pct', 'high_pct')}, expected, options)
            self.assertEqual('margin_maintenance' in signals['blocks'], expected is not None, options)
            self.assertEqual(signals['failed'], [], options)

    def test_no_block_when_the_share_capital_changed_after_the_start(self):
        # 起點之後這一檔有分割、減資、面額變更或配股：估出來的成本沒有跟著調整，整塊不給。
        # 起點以前的、起點當天的不影響（起點那天的成本是用當天——變動之後——的均價起算的）。
        days, held = self.dates(3)
        for event_day, expected in ((held[1], None), (held[2], None), (days[0], {'low_pct': 200, 'high_pct': 220}),
                                    (held[0], {'low_pct': 200, 'high_pct': 220})):
            event = {'code': '9999', 'date': event_day, 'type': '減資'}
            signals = self.estimate(self.three_days(), closes=self.THREE_CLOSES, capital_actions={'9999': [event]})
            block = signals['margin_maintenance']
            self.assertEqual(block and {key: block[key] for key in ('low_pct', 'high_pct')}, expected, event_day)
            self.assertEqual(signals['failed'], [], event_day)

    def test_no_block_when_a_day_with_margin_buying_has_no_average_price(self):
        # 第 2 天有融資買進 100 張，但行情摘要裡沒有這一檔（拿不到成交均價，新買的沒辦法入帳）：之後整塊不給。
        _, held = self.dates(3)
        margin = self.three_days()
        margin[1] = margin_day(held[1], {'9999': (100, 0, 0, 100, 200), '8888': (0, 0, 0, 100, 100)}, {'8888': (100.0, 100.0)})
        margin[0]['trades']['8888'], margin[0]['prices']['8888'] = (100, 0, 0, 0, 100), (100.0, 100.0)
        # 第 3 天 8888 還有 100 張（成本 100）；9999 的帳對不上，整筆用第 3 天均價 130 重建（高成本那一邊仍是 100）。
        # 核對：8888 的 6,000,000＋9999 的 100,000 股 ×（0.5508671×100＋0.4491329×130）× 0.6＝6,808,439 → 共 12,808 仟元。
        margin[2] = margin_day(held[2], {'9999': (0, 50, 50, 200, 100), '8888': (0, 0, 0, 100, 100)},
                               {'9999': (132.0, 130.0), '8888': (100.0, 100.0)}, official_thousand=12808)
        signals = self.estimate(margin, closes=self.THREE_CLOSES)
        self.assertIsNone(signals['margin_maintenance'])
        self.assertEqual((signals['blocks'], signals['failed']), ([], []))
        # 當天的全市場核對是過的、另一檔照常有（167～167），所以 9999 沒有是因為那一天拿不到成交均價。
        check = self.margin_check(margin, held[2])
        self.assertEqual((check['status'], check['stocks'], check['estimated']), ('ok', 1, 12808439))
        self.assertEqual(self.estimate(margin, closes=self.THREE_CLOSES, code='8888')['margin_maintenance'],
                         {'low_pct': 167, 'high_pct': 167, 'data_date': held[2]})

    def test_a_day_without_trading_and_without_any_margin_activity_does_not_drop_the_block(self):
        # 第 1 天買 100 張均價 100。第 2 天這一檔沒有成交（行情摘要沒有它），融資也沒有任何進出、餘額照舊 100 張：
        # 那一天沒有東西要入帳，估計不受影響。第 3 天收 132：132 ÷ 60＝220% → 220～220。
        # 核對：100 張 × 1,000 × 100 × 0.6＝6,000 仟元。
        _, held = self.dates(3)
        margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100)}, {'9999': (100.0, 100.0)}),
                  margin_day(held[1], {'9999': (0, 0, 0, 100, 100)}, {}),
                  margin_day(held[2], {'9999': (0, 0, 0, 100, 100)}, {'9999': (132.0, 130.0)}, official_thousand=6000)]
        self.assertEqual(self.estimate(margin, closes=self.THREE_CLOSES)['margin_maintenance'],
                         {'low_pct': 220, 'high_pct': 220, 'data_date': held[2]})

    def test_no_block_when_holders_sold_or_repaid_on_a_day_without_an_average_price(self):
        # 第 2 天這一檔沒有成交、卻有現金償還 20 張（或賣出 20 張）：那一天的減少沒有照「最近買的先減」入帳，之後整塊不給。
        # 8888 讓當天的全市場核對是過的：8888 的 6,000,000＋9999 的 80 張 × 1,000 × 100 × 0.6＝4,800,000 → 10,800 仟元。
        _, held = self.dates(3)
        for leaving in ((0, 0, 20), (0, 20, 0)):
            margin = [margin_day(held[0], {'9999': (100, 0, 0, 0, 100), '8888': (100, 0, 0, 0, 100)},
                                 {'9999': (100.0, 100.0), '8888': (100.0, 100.0)}),
                      margin_day(held[1], {'9999': (*leaving, 100, 80), '8888': (0, 0, 0, 100, 100)}, {'8888': (100.0, 100.0)}),
                      margin_day(held[2], {'9999': (0, 0, 0, 80, 80), '8888': (0, 0, 0, 100, 100)},
                                 {'9999': (132.0, 130.0), '8888': (100.0, 100.0)}, official_thousand=10800)]
            signals = self.estimate(margin, closes=self.THREE_CLOSES)
            self.assertIsNone(signals['margin_maintenance'], leaving)
            self.assertEqual((signals['blocks'], signals['failed']), ([], []), leaving)
            check = self.margin_check(margin, held[2])
            self.assertEqual((check['status'], check['stocks'], check['estimated']), ('ok', 1, 10800000), leaving)

    def margin_check(self, margin, as_of):
        """每日對帳的結果（建置報告與站台狀態用的那一份）。"""
        from twon_analysis import margin_maintenance
        self.calls += 1
        margin_root = write_margin_store(Path(self.tmp.name) / f'margin{self.calls}', margin)
        with unittest.mock.patch('twon_analysis.margin_maintenance.ROOT', margin_root):
            return margin_maintenance.daily_check(as_of)

    def test_daily_check_compares_the_estimated_market_total_with_the_official_figure(self):
        # 估計的全市場融資金額＝100 張 × 1,000 股 ×（0.5508671×110＋0.4491329×100＝105.5087）× 0.6＝6,330,520 元。
        # 官方 6,331 仟元 → 差 −0.01%，在門檻（8%）以內：照常給。
        _, held = self.dates(3)
        self.assertEqual(self.margin_check(self.three_days(6331), held[2]), {
            'status': 'ok', 'data_date': held[2], 'gap_pct': -0.01, 'threshold_pct': 8.0,
            'estimated': 6330520, 'official': 6331000, 'stocks': 1, 'reason': None})
        # 官方 5,862 仟元 → +7.99%：照常。官方 5,861 仟元 → +8.01%：超過門檻。
        self.assertEqual(self.margin_check(self.three_days(5862), held[2])['gap_pct'], 7.99)
        self.assertEqual(self.estimate(self.three_days(5862), closes=self.THREE_CLOSES)['margin_maintenance'],
                         {'low_pct': 200, 'high_pct': 220, 'data_date': held[2]})

    def test_no_stock_gets_the_block_on_a_day_the_market_total_is_too_far_off(self):
        # 官方 5,861 仟元 → 估計多 8.01%，超過門檻：當天所有股票都不給，對帳結果寫明原因。高估與低估都算。
        _, held = self.dates(3)
        for official_thousand, gap in ((5861, 8.01), (6882, -8.01)):
            signals = self.estimate(self.three_days(official_thousand), closes=self.THREE_CLOSES)
            self.assertIsNone(signals['margin_maintenance'], official_thousand)
            self.assertEqual((signals['blocks'], signals['failed']), ([], []), official_thousand)
            check = self.margin_check(self.three_days(official_thousand), held[2])
            self.assertEqual((check['status'], check['gap_pct'], check['stocks']), ('over_threshold', gap, 0), official_thousand)
            self.assertIn('8', check['reason'])
            self.assertIn(f'{abs(gap)}', check['reason'])

    def test_data_date_is_the_day_of_the_margin_data_even_when_prices_are_a_day_newer(self):
        # 行情到第 4 天，融資原件只到第 3 天（官方晚上才公布，正常晚一個交易日）：
        # 資料日標第 3 天，維持率用第 3 天的收盤 132 算（不拿第 4 天的收盤 150 去配第 3 天的成本）→ 200～220。
        days, held = self.dates(4)
        margin = self.three_days()
        for item, day in zip(margin, held):
            item['day'] = day
        margin.append(margin_day(held[3], None, {'9999': (150.0, 150.0)}))
        signals = self.analyze([FLAT] * 26 + [(close, '', 0.0) for close in (*self.THREE_CLOSES, 150.0)], {}, days=days,
                               margin=margin)['signals']
        self.assertEqual(signals['data_date'], held[3])
        self.assertEqual(signals['margin_maintenance'], {'low_pct': 200, 'high_pct': 220, 'data_date': held[2]})
        self.assertEqual(self.margin_check(margin, held[3])['status'], 'ok')

    def test_no_block_when_the_margin_data_is_more_than_a_day_behind(self):
        # 行情到第 5 天，融資原件停在第 3 天（落後兩個交易日）：不拿舊的範圍出來，所有股票都不給，對帳結果寫明停在哪一天。
        days, held = self.dates(5)
        margin = self.three_days()
        for item, day in zip(margin, held):
            item['day'] = day
        margin += [margin_day(held[3], None, {'9999': (150.0, 150.0)}), margin_day(held[4], None, {'9999': (150.0, 150.0)})]
        rows = [FLAT] * 25 + [(close, '', 0.0) for close in (*self.THREE_CLOSES, 150.0, 150.0)]
        signals = self.analyze(rows, {}, days=days, margin=margin)['signals']
        self.assertIsNone(signals['margin_maintenance'])
        self.assertEqual((signals['blocks'], signals['failed']), ([], []))
        check = self.margin_check(margin, held[4])
        self.assertEqual((check['status'], check['data_date'], check['stocks']), ('stale', held[2], 0))
        self.assertIn(held[2], check['reason'])

    def test_no_block_when_a_trading_day_of_margin_data_is_missing(self):
        # 第 2 天有行情、沒有融資原件（那天沒抓到）：那天的買賣接不起來，所有股票都不給，對帳結果列出缺哪幾天。
        _, held = self.dates(3)
        margin = self.three_days()
        margin[1] = margin_day(held[1], None, {'9999': (120.0, 120.0)})
        signals = self.estimate(margin, closes=self.THREE_CLOSES)
        self.assertIsNone(signals['margin_maintenance'])
        self.assertEqual((signals['blocks'], signals['failed']), ([], []))
        check = self.margin_check(margin, held[2])
        self.assertEqual((check['status'], check['missing_days'], check['stocks']), ('missing_days', [held[1]], 0))

    def test_no_block_when_a_day_of_margin_data_has_no_price_summary(self):
        # 第 2 天有融資原件、沒有行情摘要（整天的收盤與成交均價都拿不到）：所有股票都不給，對帳結果列出是哪幾天。
        _, held = self.dates(3)
        margin = self.three_days()
        margin[1]['prices'] = None
        self.assertIsNone(self.estimate(margin, closes=self.THREE_CLOSES)['margin_maintenance'])
        check = self.margin_check(margin, held[2])
        self.assertEqual((check['status'], check['missing_days'], check['stocks']), ('missing_prices', [held[1]], 0))

    def test_an_original_that_cannot_be_read_drops_only_this_block_and_is_named(self):
        # 第 2 天的個股融資原件內容壞了：這一塊算不出來，記進 failed 讓建置報告看得到；其他幾塊與整份分析照常。
        days, held = self.dates(3)
        margin = self.three_days()
        margin[1] = margin_day(held[1], BROKEN, {'9999': (120.0, 120.0)})
        rows = [FLAT] * 27 + [(close, '', 0.0) for close in self.THREE_CLOSES]
        index = {days[-1]: 20000.0, days[-6]: 20000.0}
        for _ in range(2):   # 同一批檔案再問一次（建置時每一檔股票都會問）也是同樣的結果
            signals = self.analyze(rows, index, days=days, margin=margin)['signals']
            self.assertIsNone(signals['margin_maintenance'])
            self.assertEqual((signals['blocks'], signals['failed']), (['versus_market'], ['margin_maintenance']))

    def test_no_block_without_the_fixed_start_day(self):
        # 起點（2025-11-03）那一份原件不在：起點係數是照那一天定的，從別的日子開始算不對，所有股票都不給。
        _, held = self.dates(3)
        margin = self.three_days()[1:]
        self.assertIsNone(self.estimate(margin, closes=self.THREE_CLOSES)['margin_maintenance'])
        check = self.margin_check(margin, held[2])
        self.assertEqual((check['status'], check['data_date'], check['stocks']), ('no_start', None, 0))
        self.assertEqual(self.margin_check([], held[2])['status'], 'no_start')

    def test_same_data_gives_the_same_result_every_time(self):
        first = self.estimate(self.three_days(), closes=self.THREE_CLOSES)
        second = self.estimate(self.three_days(), closes=self.THREE_CLOSES)
        self.assertEqual(first, second)
        _, held = self.dates(3)
        self.assertEqual(self.margin_check(self.three_days(), held[2]), self.margin_check(self.three_days(), held[2]))

    def test_fixed_numbers_of_the_estimate_have_not_changed(self):
        # 起點日期、起點係數、混合權重是策略驗證線 2026-11-16 求出後固定下來的；融資成數六成；改這幾個數字或門檻要先問本人。
        from twon_analysis import margin_maintenance as fixed
        self.assertEqual((fixed.START_DATE, fixed.SEED_SCALE, fixed.BLEND_WEIGHT, fixed.LOAN_RATIO),
                         ('2025-11-03', 0.9369126317402282, 0.5508671333927683, 0.6))
        self.assertEqual((fixed.CHECK_THRESHOLD_PCT, fixed.MAX_LAG_DAYS), (8.0, 1))


class StockSignalsAlgorithmVersionTests(unittest.TestCase):
    """漲停動能的算法版本是一個固定檔案。內容被改動（重新訓練、改門檻、改換算表、改歷史比例）時這裡會紅：
    八個條件用的固定數字（幾 %、幾倍、幾天）也在這個檔案裡。換算法版本要先問本人，然後換一個新的版本名稱與新的雜湊。"""

    def test_the_frozen_algorithm_file_has_not_changed(self):
        import hashlib
        from twon_analysis.limit_up_momentum import ALGORITHM_FILE
        frozen = json.loads(ALGORITHM_FILE.read_text(encoding='utf-8'))
        canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        self.assertEqual(hashlib.sha256(canonical.encode('utf-8')).hexdigest(), 'c6597725df40047f0f5e0600367e2b2468d99feda96e771c187e47512e0dd26f')
        self.assertEqual(frozen['version'], 'limit-up-momentum-20261116')
        self.assertEqual(frozen['band_rates_pct'], [0.4, 0.8, 2.6, 3.1, 5.1, 17.6])
        self.assertEqual(frozen['average_rate_pct'], 1.7)
        self.assertEqual((frozen['limit_up_threshold'], frozen['up_short_of_limit_threshold'], frozen['turnover_multiple']),
                         (0.095, 0.05, 2))
        self.assertEqual((frozen['lookback_days'], frozen['short_line_days'], frozen['long_line_days'],
                          frozen['trust_streak_days'], frozen['min_bars']), (20, 20, 60, 3, 61))
        # 版本名稱會出現在個股分析結果裡，不能撞到禁用詞（有的檢查不分大小寫）。
        self.assertFalse([word for word in BANNED_TEXT if word.lower() in frozen['version'].lower()])
        self.assertEqual(len(frozen['table']), 256)
        self.assertTrue(all(0 <= points <= 99 and 1 <= band <= 6 for points, band in frozen['table'].values()))


class StockSignalsBlockListTests(_SignalsFixture, unittest.TestCase):
    """「有哪幾塊」的清單由五塊資料直接給，頁面照著它決定出現哪幾張板。"""

    def test_block_list_names_exactly_the_blocks_that_exist_in_fixed_order(self):
        # 130 根每天漲 1 元（均線排列四項成立），倒數第 10 根爆量，加權指數資料日與 5 個交易日前都有。
        days = self.days(130)
        rows = [(100.0 + i, '+', 1.0, 101.0 + i, 99.0 + i, 10_000_000 if i == 120 else 1_000_000) for i in range(130)]
        signals = self.analyze(rows, {days[-1]: 20000.0, days[-6]: 20000.0})['signals']
        self.assertEqual(signals['blocks'], ['versus_market', 'ma_alignment', 'heavy_zones'])
        self.assertEqual(set(signals), {'data_date', 'limit_up_momentum', 'versus_market', 'ma_alignment', 'margin_maintenance',
                                        'heavy_zones', 'blocks', 'failed'})
        self.assertEqual(signals['failed'], [])
        # 這一檔近 5 日＝229 ÷ 224 − 1＝+2.23%（每天 +1 元連乘），大盤 0% → 強於大盤 2.2。
        self.assertEqual([(row['days'], row['gap']) for row in signals['versus_market']['rows']], [(5, 2.2)])
        self.assertEqual(signals['ma_alignment']['summary'], '多頭排列')
        # 爆量那天低 219、高 221，現價 229 → 在現價下方，221 ÷ 229 − 1＝−3.5%。
        self.assertEqual([(zone['low'], zone['high'], zone['distance_pct']) for zone in signals['heavy_zones']['zones']],
                         [(219.0, 221.0, -3.5)])
        dumped = json.dumps(signals, ensure_ascii=False)
        for word in BANNED_TEXT + ('反推',):
            self.assertNotIn(word, dumped)
        # 加權指數多給 20 個交易日前那一天：漲停動能也有，四塊照固定順序。
        # 近 20 日 229 ÷ 209 − 1＝+9.6% 贏大盤（0%）、收盤在 20 日線與 60 日線之上；今天收 229 剛好等於昨天的最高 229（不算高於）
        # → 條件 ⑤⑥ 成立，換算表 56 分、第 2 級。
        four = self.analyze(rows, {days[-1]: 20000.0, days[-6]: 20000.0, days[-21]: 20000.0})['signals']
        self.assertEqual(four['blocks'], ['limit_up_momentum', 'versus_market', 'ma_alignment', 'heavy_zones'])
        self.assertEqual(four['limit_up_momentum'], momentum(56, 2, 0.8))
        dumped = json.dumps(four, ensure_ascii=False)
        for word in BANNED_TEXT + ('反推',):
            self.assertNotIn(word, dumped)
        # 同樣 130 根改成最後三天落在融資重播的起點（2025-11-03）之後，並給那三天的融資原件：五塊都有，照固定順序
        # （漲停動能、和大盤比、均線排列、融資維持率（估）、爆大量成交區）。
        from datetime import date, timedelta
        late = [(date(2025, 11, 5) - timedelta(days=129 - i)).isoformat() for i in range(130)]
        five = self.analyze(rows, {late[-1]: 20000.0, late[-6]: 20000.0, late[-21]: 20000.0}, days=late,
                            margin=three_margin_days(late[-3:]))['signals']
        self.assertEqual(five['blocks'], ['limit_up_momentum', 'versus_market', 'ma_alignment', 'margin_maintenance', 'heavy_zones'])
        self.assertEqual(five['margin_maintenance'], {'low_pct': 200, 'high_pct': 220, 'data_date': late[-1]})
        self.assertEqual(five['failed'], [])
        dumped = json.dumps(five, ensure_ascii=False)
        for word in BANNED_TEXT + ('反推',):
            self.assertNotIn(word, dumped)
        self.assertFalse(REMOVED_KEYS & set(all_keys(five)))
        # 20 根平盤、沒有加權指數：每一塊都沒有，清單是空的，鍵都還在（值是 null）。
        empty = self.analyze([FLAT] * 20, {})['signals']
        self.assertEqual((empty['blocks'], empty['versus_market'], empty['ma_alignment'], empty['heavy_zones']), ([], None, None, None))
        self.assertIsNone(empty['limit_up_momentum'])
        self.assertIsNone(empty['margin_maintenance'])
        self.assertEqual(empty['failed'], [])   # 天生沒有不算算不出來

    def test_a_block_that_cannot_be_computed_drops_only_itself_and_is_named(self):
        # 同樣 130 根，但第 31 根的成交量是空的（資料有洞）：爆大量成交區的前 60 日均量算不出來。
        # 只少這一塊，並記在 failed 讓建置報告看得到；和大盤比、均線排列照常。
        days = self.days(130)
        rows = [(100.0 + i, '+', 1.0, 101.0 + i, 99.0 + i, None if i == 30 else 1_000_000) for i in range(130)]
        signals = self.analyze(rows, {days[-1]: 20000.0, days[-6]: 20000.0})['signals']
        self.assertEqual(signals['blocks'], ['versus_market', 'ma_alignment'])
        self.assertIsNone(signals['heavy_zones'])
        self.assertEqual(signals['failed'], ['heavy_zones'])
        self.assertEqual(signals['ma_alignment']['summary'], '多頭排列')


class StockSignalsReaderTests(unittest.TestCase):
    """加權指數收盤：從每日更新已經在抓的月檔讀，由 as_of 當月往回只讀最近幾個月。"""

    def test_reads_closes_from_the_monthly_files_up_to_the_data_month(self):
        import stock_history
        from twon_analysis import analyze_v2 as module
        with tempfile.TemporaryDirectory() as tmp:
            for month, rows in (('202609', [['115/09/30', '1', '2', '3', '40,100.50']]),
                                ('202610', [['115/10/01', '1', '2', '3', '40,200.25'], ['115/10/02', '1', '2', '3', '39,900.00']]),
                                ('202611', [['115/11/02', '1', '2', '3', '41,000.00']])):
                (Path(tmp) / f'taiex-{month}.json').write_text(json.dumps({'data': {'data': rows}}), encoding='utf-8')
            # 沒有資料列的月檔（官方回「沒有符合條件的資料」）不讓其他月份跟著讀不到。
            (Path(tmp) / 'taiex-202608.json').write_text(json.dumps({'data': {'stat': '很抱歉，沒有符合條件的資料!'}}), encoding='utf-8')
            with unittest.mock.patch.object(stock_history, 'DEFAULT_INDEX_DIR', Path(tmp)):
                closes = module.taiex_closes('2026-10-02', months=3)
        self.assertEqual(closes, {'2026-09-30': 40100.5, '2026-10-01': 40200.25, '2026-10-02': 39900.0})


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
