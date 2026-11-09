"""三方案公開接縫：分析投影與價位候選，不碰服務／交易／網路。

預設指紋於 2026-11-07 修改程式前，直接取本機既有原件的完整分析投影。
不含可更新 metadata；資料原件若更新，須先核對變動原因，不可盲目重錄指紋。
"""
import copy
from dataclasses import asdict, fields, replace
import hashlib
import json
import unittest
from unittest.mock import patch

from twon_analysis.analyze import analyze
from twon_analysis.data import DEFAULT_DB
from twon_analysis.indicators import Bar, indicators
from twon_analysis.price_structure import candidates, structure
from twon_analysis.profiles import get_preset

IDS = ('2TON-FIT-V1', 'MOMENTUM-V1', 'SWING-SAFE-V1')
PUBLIC_PRESET_KEYS = {'id', 'name', 'description', 'version'}


class PresetContractTests(unittest.TestCase):
    def test_unknown_preset_rejected_before_data_access(self):
        with patch('twon_analysis.analyze.load_bars') as loader:
            for invalid in ('unknown', '', 'momentum-v1', None, [], 1):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    analyze('2330', '2026-11-06', invalid)
            loader.assert_not_called()

    def test_hash_is_stable_and_covers_every_effective_parameter_and_version(self):
        preset = get_preset()
        self.assertEqual(preset.params_hash, get_preset().params_hash)
        for section in ('score', 'price'):
            config = getattr(preset, section)
            for field in fields(config):
                value = getattr(config, field.name)
                changed = not value if isinstance(value, bool) else value[:-1] if isinstance(value, tuple) else value + 1
                modified = replace(preset, **{section: replace(config, **{field.name: changed})})
                with self.subTest(section=section, field=field.name):
                    self.assertNotEqual(preset.params_hash, modified.params_hash)
        self.assertNotEqual(preset.params_hash, replace(preset, version='next-version').params_hash)
        self.assertEqual(len({get_preset(pid).params_hash for pid in IDS}), 3)

    def test_approved_preset_values_are_not_frontend_metadata(self):
        expected = (
            ((.40, .10, .25, .15, .10, 6, 6, 6, 4, 6), ((5, 10, 20, 60, 120, 240), 60, 120, 60, 40, 60, 9, True, True)),
            ((.40, .15, .30, .10, .05, 8, 5, 6, 3, 6), ((5, 10, 20), 20, 40, 20, 20, 20, 3, False, False)),
            ((.30, .15, .10, .25, .20, 5, 7, 7, 5, 7), ((20, 60, 120, 240), 120, 240, 120, 80, 120, 18, True, True)),
        )
        for pid, (score, price) in zip(IDS, expected):
            preset = get_preset(pid)
            self.assertEqual(tuple(asdict(preset.score).values()), score)
            self.assertEqual(tuple(asdict(preset.price).values()), price)
            self.assertEqual(set(preset.public_metadata()), PUBLIC_PRESET_KEYS)

    def test_short_history_keeps_selected_metadata_and_no_score(self):
        with patch('twon_analysis.analyze.load_bars', return_value=(rising_bars(19), {})):
            for pid in IDS:
                result = analyze('2330', '2026-11-06', pid)
                self.assertTrue(result['qa']['blocking'])
                self.assertIn('19', result['qa']['reason'])
                self.assertEqual(result['preset']['id'], pid)
                self.assertEqual(result['params_hash'], get_preset(pid).params_hash)
                self.assertNotIn('score', result)

    def test_blocked_results_identify_selected_preset_without_config(self):
        with patch('twon_analysis.analyze.load_bars', return_value=([], {})):
            for preset_id in IDS:
                with self.subTest(preset_id=preset_id):
                    result = analyze('missing', '2026-11-06', preset_id=preset_id)
                    self.assertTrue(result['qa']['blocking'])
                    self.assertEqual(result['preset']['id'], preset_id)
                    self.assertEqual(result['preset']['provenance'], '本站自有')
                    self.assertEqual(set(result['preset']), PUBLIC_PRESET_KEYS | {'provenance'})
                    self.assertEqual([p['id'] for p in result['available_presets']], list(IDS))
                    for preset in result['available_presets']:
                        self.assertEqual(set(preset), PUBLIC_PRESET_KEYS)
                    self.assertIn(preset_id, result['formula'])
                    self.assertEqual(result['version'], result['preset']['version'])
                    self.assertIn(preset_id, result['version'])
                    self.assertRegex(result['params_hash'], r'^[0-9a-f]{8}$')
                    self.assertNotIn('score', result)


def rising_bars(n=260):
    return [Bar(f'd{i:03d}', 100 + i, 101 + i, 99 + i, 100 + i, i + 1) for i in range(n)]


class PriceProfileTests(unittest.TestCase):
    def test_pivots_still_require_two_right_hand_bars_in_every_mode(self):
        bars = rising_bars()
        bars[257] = Bar('d257', 357, 500, 100, 357, 100)
        for pid in IDS:
            for length, confirmed in ((258, False), (259, False), (260, True)):
                subset = bars[:length]
                factors = candidates(subset, indicators(subset), profile=get_preset(pid).price)
                for kind in ('swing_high', 'swing_low'):
                    self.assertEqual(any(f['kind'] == kind and f['date'] == 'd257' for f in factors), confirmed)

    def test_structure_passes_selected_sources_and_labels_own_windows(self):
        bars = rising_bars()
        x = indicators(bars)
        self.assertEqual(structure(bars, x, 250, .7),
                         structure(bars, x, 250, .7, profile=get_preset().price))
        for preset_id in IDS:
            result = structure(bars, x, 250, .7, profile=get_preset(preset_id).price)
            self.assertEqual(result['evidence_levels']['profileWindows'], '本站自有（助手分析窗口組合，非原站重現）')
            sources = [source for side in ('pressure', 'support') for cluster in result[side]['all']
                       for source in cluster['sources']]
            kinds = {source['kind'] for source in sources}
            if preset_id == IDS[1]:
                self.assertTrue(kinds.isdisjoint({'ma60', 'ma120', 'ma240', 'inst_cost', 'high_60', 'low_60', 'intraday_high_60'}))
            if preset_id == IDS[2]:
                self.assertTrue(kinds.isdisjoint({'ma5', 'ma10'}))

    def test_swing_gap_breakout_and_platform_lookbacks(self):
        pivots = rising_bars()
        for i in (80, 180, 240):
            pivots[i] = Bar(f'd{i:03d}', 100 + i, 200 + i, i, 100 + i, 100)
        gaps = []
        for i in range(260):
            close = 100 + i + 100 * sum(i >= boundary for boundary in (80, 180, 240))
            gaps.append(Bar(f'd{i:03d}', close, close + 1, close - 1, close, 100))
        breakouts = rising_bars()
        for i in (160, 220, 250):
            breakouts[i] = Bar(f'd{i:03d}', 99 + i, 102 + i, 98 + i, 99 + i, 100)
        for preset_id, pivot_dates, breakout_dates in (
            (IDS[0], {'d180', 'd240'}, {'d220', 'd250'}),
            (IDS[1], {'d240'}, {'d250'}),
            (IDS[2], {'d080', 'd180', 'd240'}, {'d160', 'd220', 'd250'}),
        ):
            with self.subTest(preset_id=preset_id):
                profile = get_preset(preset_id).price
                for kind, bars, expected in (
                    ('swing_high', pivots, pivot_dates), ('swing_low', pivots, pivot_dates),
                    ('gap_up_upper', gaps, pivot_dates), ('breakout_failure', breakouts, breakout_dates),
                ):
                    factors = candidates(bars, indicators(bars), profile=profile)
                    self.assertEqual({f['date'] for f in factors if f['kind'] == kind}, expected, kind)
        for start, expected_modes in ((196, {IDS[2]}), (228, {IDS[0], IDS[2]})):
            bars = [Bar(f'd{i:03d}', 100, 201, 99, 100 if i % 2 else 200, 100) for i in range(260)]
            for i in range(start, start + 8):
                bars[i] = Bar(f'd{i:03d}', 150, 151, 149, 150, 100)
            for preset_id in IDS:
                factors = candidates(bars, indicators(bars), profile=get_preset(preset_id).price)
                platforms = [f for f in factors if f['kind'] == 'platform']
                self.assertEqual(bool(platforms), preset_id in expected_modes, (start, preset_id))
                if platforms:
                    self.assertEqual((platforms[0]['value'], platforms[0]['date']), (150, f'd{start + 7:03d}'))

    def test_volume_profile_and_heavy_volume_windows(self):
        bars = rising_bars()
        x = indicators(bars)
        # 線性上漲且量逐日增加，最大量箱必為最後一箱；三種窗口箱寬不同。
        for preset_id, expected_low, count in zip(IDS, (356.05, 358.05, 353.05), (9, 3, 18)):
            with self.subTest(preset_id=preset_id):
                factors = candidates(bars, x, profile=get_preset(preset_id).price)
                self.assertEqual(next(f['value'] for f in factors if f['kind'] == 'vp_low'), expected_low)
                self.assertEqual(len([f for f in factors if f['kind'] == 'heavy_volume']), count)
        bars[160] = Bar('d160', 260, 261, 259, 260, 100000)
        bars[220] = Bar('d220', 320, 321, 319, 320, 10000)
        for preset_id, old_dates in zip(IDS, ({'d220'}, set(), {'d160', 'd220'})):
            factors = candidates(bars, indicators(bars), profile=get_preset(preset_id).price)
            dates = {f['date'] for f in factors if f['kind'] == 'heavy_volume'}
            self.assertEqual(dates & {'d160', 'd220'}, old_dates)

    def test_candidate_source_selection_and_legacy_signature(self):
        bars = rising_bars()
        x = indicators(bars)
        old_call = candidates(bars, x, 250)
        self.assertEqual(old_call, candidates(bars, x, 250, profile=get_preset().price))
        for preset_id, ma_kinds, long_refs, inst in (
            (IDS[0], {'ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma240'}, True, True),
            (IDS[1], {'ma5', 'ma10', 'ma20'}, False, False),
            (IDS[2], {'ma20', 'ma60', 'ma120', 'ma240'}, True, True),
        ):
            with self.subTest(preset_id=preset_id):
                factors = candidates(bars, x, 250, profile=get_preset(preset_id).price)
                kinds = {f['kind'] for f in factors}
                self.assertEqual({k for k in kinds if k.startswith('ma')}, ma_kinds)
                self.assertEqual('inst_cost' in kinds, inst)
                for kind in ('high_60', 'low_60', 'intraday_high_60'):
                    self.assertEqual(kind in kinds, long_refs)
                self.assertTrue({'prior_high_20', 'high_20', 'low_20'} <= kinds)


@unittest.skipUnless(DEFAULT_DB.exists(), '本機行情資料庫不存在')
class LocalAnalysisProfileTests(unittest.TestCase):
    def test_missing_incomplete_and_etf_never_gain_a_total_in_any_mode(self):
        for pid in IDS:
            with self.subTest(preset_id=pid):
                with patch('twon_analysis.chips.revenue', return_value=None), patch('twon_analysis.chips.valuation', return_value=None):
                    missing = analyze('2330', '2026-11-06', pid)
                self.assertIn('F', missing['dimensions_missing'])
                with patch('twon_analysis.chips.borrow', return_value=[None] * 20):
                    incomplete = analyze('2330', '2026-11-06', pid)
                self.assertEqual(incomplete['coverage']['C']['missing_sources'], ['借券'])
                self.assertIn('C', incomplete['dimensions_incomplete'])
                etf = analyze('0050', '2026-11-06', pid)
                self.assertEqual(len(etf['strategies']), 20)
                self.assertEqual(etf['score_note'], 'ETF 不適用五維總分')
                self.assertNotIn('F', etf['dimensions_missing'])
                for result in (missing, incomplete, etf):
                    self.assertIsNone(result['score'])
                    self.assertFalse(result['score_complete'])
                    self.assertIsNone(result['trend'])
                    self.assertEqual(result['preset']['id'], pid)

    def test_public_response_whitelist_and_no_config_leak(self):
        expected_keys = {
            'code', 'name', 'kind', 'as_of', 'requested_as_of', 'version', 'formula', 'params_hash',
            'preset', 'available_presets', 'quote', 'sources', 'qa', 'dimensions', 'dimensions_missing',
            'dimensions_incomplete', 'coverage', 'score', 'score_complete', 'score_note', 'trend',
            'risk_flags', 'strategies', 'strategy_counts', 'levels', 'bars', 'labels', 'chart',
        }
        secrets = {'weight', 'weights', 'score_profile', 'price_profile', 'params', 'res', 'oh_rsi',
                   'oh_margin', 'oh_hot'} | set(asdict(get_preset().price))

        def check(value):
            if isinstance(value, dict):
                self.assertFalse(secrets.intersection(value))
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)

        for pid in IDS:
            result = analyze('2603', '2026-11-06', pid)
            self.assertEqual(set(result), expected_keys)
            self.assertEqual(set(result['preset']), PUBLIC_PRESET_KEYS | {'provenance'})
            for option in result['available_presets']:
                self.assertEqual(set(option), PUBLIC_PRESET_KEYS)
            check(result)
            self.assertEqual(result['params_hash'], analyze('2330', '2026-10-16', pid)['params_hash'])
            result['preset']['name'] = 'changed'
            result['available_presets'][0]['id'] = 'changed'
            self.assertEqual(analyze('2603', '2026-11-06', pid)['preset']['id'], pid)
            self.assertEqual(get_preset().public_metadata()['id'], IDS[0])

    def test_analyze_passes_selected_price_profile_to_real_structure(self):
        for preset_id in IDS:
            with patch('twon_analysis.analyze.structure', wraps=structure) as observed:
                result = analyze('2603', '2026-11-06', preset_id)
                self.assertEqual(observed.call_args.kwargs['profile'], get_preset(preset_id).price)
                self.assertIn('本站自有', result['levels']['evidence_levels']['profileWindows'])

    def test_selected_scoring_and_metadata_then_default_restores_original(self):
        # 修改前已知五維 20/48/46/11/72，三個風險旗標；無共振／過熱加扣分。
        original = analyze('2330', '2026-11-06')
        for preset_id, expected_score in zip(IDS, (15, 16, 14)):
            with self.subTest(preset_id=preset_id):
                result = analyze('2330', '2026-11-06', preset_id)
                self.assertEqual(result['score'], expected_score)
                self.assertEqual(result['preset']['id'], preset_id)
                self.assertEqual(result['version'], result['preset']['version'])
                self.assertIn(preset_id, result['formula'])
                for key in ('dimensions', 'coverage', 'strategies', 'risk_flags'):
                    self.assertEqual(result[key], original[key])
        self.assertEqual(analyze('2330', '2026-11-06', IDS[0]), original)

    def test_default_full_legacy_projection_is_unchanged(self):
        baselines = {
            ('2330', '2026-10-16'): '6fba31c5d1b68c6e309246fa59d6f9dd5aecab6ede75bf4e94781cd17a6aff20',
            ('2603', '2026-10-16'): '6c391c3d48a5525cc566af435b3ef2e579837fb26ff6faafe91b52f9acba0f74',
            ('2330', '2026-11-06'): '63ee6f18ccf6e30b82668e743c500d52af9b2167448717890d17ca194a529c1c',
            ('2603', '2026-11-06'): '5127fbb8b108b785954f832bb7d49c169f0729f6d88baa655a1cd113ccbd3300',
        }
        for (code, day), expected in baselines.items():
            with self.subTest(code=code, day=day):
                result = analyze(code, day)
                projection = copy.deepcopy({k: v for k, v in result.items() if k not in
                                            ('version', 'formula', 'params_hash', 'preset', 'available_presets', 'chart')})
                projection['levels']['evidence_levels'].pop('profileWindows', None)
                actual = hashlib.sha256(json.dumps(projection, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
                self.assertEqual(actual, expected)


if __name__ == '__main__':
    unittest.main()
