"""價位結構：支撐、壓力、觀察區、防守價（handoff §5，採 2ton-research ruleset）。

與 current ruleset 的差異（handoff §5 表）：成交密集區用 60 日「收盤」高低、端點保留 2 位；缺口嚴格不等式不加緩衝；
支撐代表價取群組上緣；突破失敗價位取前高 pHigh（研究結論）。法人成本來源需法人資料，沒有就不加。
觀察區中心與寬度是本站暫用（原站未知），防守價依原站結構式＋本站觀察區。
"""
from __future__ import annotations

import math

from .indicators import Bar

# handoff §5.3：原站重現的權重＋本站暫用（研究沒看到的來源）
WEIGHTS = {
    'vp_center': 1.5, 'prior_high_20': 1.4, 'swing_high': 1.2, 'inst_cost': 1.2, 'vp_high': 1.1,
    'breakout_failure': 1.1, 'gap_down_upper': 1.1, 'intraday_high_60': 1.0, 'gap_down_lower': 1.0,
    'trendline_resistance': 1.0, 'ma60': 1.0, 'ma120': 1.0, 'heavy_volume': 0.9, 'ma20': 0.9, 'heavy_high': 0.8,
    'ma5': 0.7, 'ma10': 0.7,
    # 本站暫用
    'high_20': 1.1, 'high_60': 1.1, 'low_20': 1.1, 'low_60': 1.1, 'vp_low': 1.1, 'swing_low': 1.2,
    'gap_up_upper': 1.1, 'gap_up_lower': 1.0, 'trendline_support': 1.0, 'platform': 1.0, 'ma240': 1.0, 'heavy_low': 0.8,
}
EVIDENCE = {
    'tolerance': '原站重現', 'swing': '原站重現', 'volumeProfile': '原站重現', 'defense': '原站重現（依賴本站觀察區）',
    'distanceTiers': '原站重現', 'identifiedWeights': '原站重現', 'sweetBand': '本站自有', 'atrFallback': '本站自有',
    'platform': '本站自有', 'trendline': '本站自有', 'unlistedWeights': '本站自有', 'strengthTiers': '本站自有',
}
STRUCTURAL = {'prior_high_20', 'high_20', 'low_20', 'high_60', 'low_60', 'intraday_high_60', 'vp_center', 'vp_high', 'vp_low',
              'swing_high', 'swing_low', 'trendline_resistance', 'trendline_support', 'inst_cost', 'breakout_failure',
              'gap_up_upper', 'gap_up_lower', 'gap_down_upper', 'gap_down_lower', 'platform', 'ma60', 'ma120', 'ma240'}


def tick_step(price: float) -> float:
    for limit, step in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price < limit:
            return step
    return 5.0


def _tick_step_down(price: float) -> float:
    for limit, step in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1000, 1.0)):
        if price <= limit:
            return step
    return 5.0


def tick_floor(price: float) -> float:
    step = tick_step(price)
    return round(max(0.01, math.floor((price + 1e-9) / step) * step), 2)


def tick_ceil(price: float) -> float:
    step = tick_step(price)
    return round(max(0.01, math.ceil((price - 1e-9) / step) * step), 2)


def tick_round(price: float) -> float:
    low, high = tick_floor(price), tick_ceil(price)
    return low if price - low < high - price else high


def support_tick(price: float, close: float) -> float:
    p = tick_round(price)
    if p >= close:
        p = tick_floor(close - _tick_step_down(close))
    return round(max(0.01, p), 2)


def resistance_tick(price: float, close: float) -> float:
    p = tick_round(price)
    if p <= close:
        p = tick_ceil(close + tick_step(close))
    return round(p, 2)


def candidates(bars: list[Bar], x: dict, inst_cost: float | None = None) -> list[dict]:
    close = bars[-1].close
    out = []

    def add(kind, value, label, date=None):
        if value is not None and value > 0:
            out.append({'kind': kind, 'value': round(value, 2), 'weight': WEIGHTS[kind], 'label': label, 'date': date,
                        'structural': kind in STRUCTURAL})

    for n in (5, 10, 20, 60, 120, 240):
        add(f'ma{n}', x[f'ma{n}'], f'MA{n}')
    closes = [b.close for b in bars]
    add('prior_high_20', x['prior_high20'], '前 20 日盤中高點')
    add('high_20', max(closes[-20:]) if len(closes) >= 20 else None, '近 20 日收盤高點')
    add('low_20', min(b.low for b in bars[-20:]) if len(bars) >= 20 else None, '近 20 日盤中低點')
    add('high_60', max(closes[-60:]) if len(closes) >= 20 else None, '近 60 日收盤高點')
    add('low_60', min(closes[-60:]) if len(closes) >= 20 else None, '近 60 日收盤低點')
    add('intraday_high_60', max(b.high for b in bars[-60:]), '近 60 日盤中高點')

    bars60 = bars[-60:]
    if len(bars60) >= 10:
        low, high = min(b.close for b in bars60), max(b.close for b in bars60)
        if high > low:
            width = (high - low) / 20
            volumes = [0] * 20
            for b in bars60:
                volumes[min(19, max(0, math.floor((b.close - low) / width)))] += b.volume
            j = max(range(20), key=lambda i: (volumes[i], -i))
            add('vp_low', low + j * width, '成交密集區下緣')
            add('vp_center', low + (j + 0.5) * width, '成交密集區中心')
            add('vp_high', low + (j + 1) * width, '成交密集區上緣')

    bars120 = bars[-120:]
    highs, lows = [], []
    for i in range(2, len(bars120) - 2):
        cur = bars120[i]
        around = bars120[i - 2:i] + bars120[i + 1:i + 3]
        if all(cur.high >= b.high for b in around):
            highs.append((i, cur))
            add('swing_high', cur.high, '前高轉支撐' if cur.high < close * 0.999 else '前波高點', cur.date)
        if all(cur.low <= b.low for b in around):
            lows.append((i, cur))
            add('swing_low', cur.low, '波段低點', cur.date)
    last = len(bars120) - 1
    for points, kind, attr, label in ((highs, 'trendline_resistance', 'high', '下降趨勢線'), (lows, 'trendline_support', 'low', '上升趨勢線')):
        if len(points) >= 2:
            (i1, b1), (i2, b2) = points[-2], points[-1]
            projected = getattr(b2, attr) + (getattr(b2, attr) - getattr(b1, attr)) / (i2 - i1) * (last - i2)
            if close * 0.65 <= projected <= close * 1.35:
                add(kind, projected, label, b2.date)

    if inst_cost:
        add('inst_cost', inst_cost, '法人估計成本區')

    for i in range(max(20, len(bars) - 60), len(bars)):
        p_high = max(b.high for b in bars[i - 20:i])
        if bars[i].high > p_high and bars[i].close < p_high:
            add('breakout_failure', p_high, '突破失敗位', bars[i].date)

    for i in range(1, len(bars120)):
        prev, cur = bars120[i - 1], bars120[i]
        later = bars120[i + 1:]
        if cur.low > prev.high and not any(b.low <= prev.high for b in later):
            add('gap_up_upper', cur.low, '向上缺口上緣', cur.date)
            add('gap_up_lower', prev.high, '向上缺口下緣', cur.date)
        elif cur.high < prev.low and not any(b.high >= prev.low for b in later):
            add('gap_down_upper', prev.low, '向下缺口上緣', cur.date)
            add('gap_down_lower', cur.high, '向下缺口下緣', cur.date)

    bars40 = bars[-40:]
    if len(bars40) >= 12:
        for i in range(len(bars40) - 8, -1, -4):
            seg = [b.close for b in bars40[i:i + 8]]
            if min(seg) > 0 and (max(seg) - min(seg)) / min(seg) <= 0.045:
                add('platform', (max(seg) + min(seg)) / 2, '整理平台中軸', bars40[i + 7].date)
                break

    for b in sorted([b for b in bars60 if b.volume > 0], key=lambda b: -b.volume)[:9]:
        add('heavy_volume', b.close, '大量成交日收盤', b.date)
        add('heavy_high', b.high, '大量成交日高點', b.date)
        add('heavy_low', b.low, '大量成交日低點', b.date)
    return out


def _tier(distance_pct: float) -> str:
    distance = abs(distance_pct)
    return 'near' if distance <= 10 else 'mid' if distance <= 20 else 'long_term'


def _strength(score: float) -> str:
    return '極強' if score >= 4.5 else '強' if score >= 3.0 else '中' if score >= 1.8 else '弱'


def _cluster(group, close, side, modifier_bonus=0.0):
    weight = sum(f['weight'] for f in group)
    low, high = min(f['value'] for f in group), max(f['value'] for f in group)
    families = {f['kind'].split('_')[0] for f in group}
    if side == 'resistance':
        level = round(sum(f['weight'] * f['value'] for f in group) / weight, 2)
        tick = resistance_tick(level, close)
    else:
        level = high                              # 研究版顯示支撐：群組上緣
        tick = support_tick(level, close)
    strength = round(weight + 0.6 * max(0, len(families) - 1) + modifier_bonus, 2)
    distance = round((level - close) / close * 100, 2)
    return {'level': level, 'tick': tick, 'low': round(low, 2), 'high': round(high, 2), 'weight': round(weight, 2),
            'signal_count': len(group), 'structural': any(f['structural'] for f in group), 'strength': _strength(strength),
            'structure_strength': strength, 'distance_pct': distance, 'tier': _tier(distance),
            'sources': [{k: f[k] for k in ('kind', 'label', 'value', 'date')} for f in group]}


def structure(bars: list[Bar], x: dict, inst_cost: float | None = None, modifier_bonus: float = 0.0) -> dict:
    close = bars[-1].close
    atr_pct = x['atr_pct'] if x['atr_pct'] else 2.5
    tolerance = round(min(3.0, max(1.0, 0.6 * atr_pct)), 2)
    factors = candidates(bars, x, inst_cost)

    above = sorted((f for f in factors if f['value'] > close * 1.001), key=lambda f: f['value'])
    groups: list[list[dict]] = []
    for f in above:
        if groups and (f['value'] - groups[-1][0]['value']) / groups[-1][0]['value'] * 100 <= tolerance:
            groups[-1].append(f)
        else:
            groups.append([f])
    pressures = [_cluster(g, close, 'resistance', modifier_bonus) for g in groups]
    actionable = [c for c in pressures if c['tier'] != 'long_term']
    limit_up = tick_floor(close * 1.10) if not above else None

    below = sorted((f for f in factors if f['value'] < close * 0.999), key=lambda f: -f['value'])
    support_groups: list[list[dict]] = []
    half = tolerance / 2
    for f in below:
        if support_groups:
            top = support_groups[-1][0]['value']
            bottom = min(g['value'] for g in support_groups[-1])
            if (top - f['value']) / top * 100 <= half:
                support_groups[-1].append(f)
                continue
            if (bottom - f['value']) / bottom * 100 <= half:
                support_groups[-1].append({**f, 'value': bottom})   # 只吸收標籤，不下修群組下緣
                continue
        support_groups.append([f])
    supports_all = [_cluster(g, close, 'support') for g in support_groups]
    qualified = [c for c in supports_all if c['structural'] or c['signal_count'] >= 2] or supports_all
    supports = [c for c in qualified if c['tier'] != 'long_term']

    atr = x['atr14'] if x['atr14'] else close * 0.02
    anchor = next((c for c in supports if (c['structural'] and (c['signal_count'] >= 2 or c['weight'] >= 1.4)) or c['weight'] >= 2.2), None)
    low20 = x['low20_intraday'] if x['low20_intraday'] is not None else min(b.low for b in bars)
    sweet = {'formed': False, 'low': None, 'high': None, 'position': None, 'confidence': None}
    if anchor and len(bars) >= 15:
        s_low, s_high = anchor['low'] - 0.5 * atr, anchor['high'] + 0.5 * atr
        if s_high - s_low < anchor['level'] * 0.012:
            s_low, s_high = anchor['level'] * 0.994, anchor['level'] * 1.006
        s_low, s_high = round(max(0.01, s_low), 2), round(max(s_low + 0.01, s_high), 2)
        defense = round(max(0.01, min(low20, s_low - 1.5 * atr)), 2)
        sweet = {'formed': True, 'low': tick_floor(s_low), 'high': max(tick_floor(s_low), tick_ceil(s_high)),
                 'position': round((close - s_low) / max(0.01, s_high - s_low), 4),
                 'confidence': 'high' if anchor['weight'] >= 3.0 else 'medium' if anchor['weight'] >= 2.0 else 'low'}
    else:
        defense = round(max(0.01, low20), 2)
    first = actionable[0] if actionable else None
    top = first['tick'] if first else limit_up
    range_position = None
    if sweet['formed'] and top is not None and top > sweet['low']:
        range_position = round((close - sweet['low']) / max(0.01, top - sweet['low']), 4)
    return {
        'close': close, 'atr_pct': x['atr_pct'], 'tolerance_pct': tolerance,
        'pressure': {'first': first, 'second': actionable[1] if len(actionable) > 1 else None, 'all': pressures, 'limit_up_ref': limit_up},
        'support': {'first': supports[0] if supports else None, 'second': supports[1] if len(supports) > 1 else None, 'all': qualified},
        'sweet': sweet, 'defense': support_tick(defense, close), 'range_position': range_position,
        'evidence_levels': EVIDENCE,
    }
