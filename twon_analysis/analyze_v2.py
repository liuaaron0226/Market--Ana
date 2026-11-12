"""個股分析 v2：只輸出研究確認過（與原站算法相容）的指標、價位結構與條件；尚未反推出的項目固定標示，不給數字。

本人定案（2026-11-08）：「那個個股分析我覺得不準確阿，不如用別人已經確認過的」「自創部分不保留」。
契約：.scratch/chart-screening-workbench/stock-analysis-contract-v2.md；刪減依據：stock-analysis-trim-plan.md。
R1（2026-11-09）依外部搜查（reference-rules-external-research.md §3）與會員第二輪反推
（reverse-engineering-round2.md，198 檔）回填：可觀察範圍、第一／第二壓力選取、漲停參考價、MA240、
防守價（未形成觀察區時）、9 項可當規格與 5 項近似條件、趨勢分類函式（待總分）、交易規則聲明。
自創的五維、總分、方案、觀察區錨點、強度分級仍不輸出。
"""
from __future__ import annotations

import math

from .data import DEFAULT_DB, DEFAULT_OTC_DB, load_bars
from .indicators import Bar, atr14, kd9, ma, macd
from .price_structure import WEIGHTS, resistance_tick, support_tick, tick_floor
from .strategy_v1 import strategy_v1

SCHEMA = 'stock-analysis/v2'
ANALYSIS_VERSION = 'price-structure-rea-v2'
MIN_ANALYSIS_BARS = 20
UNDISCLOSED = {'status': 'undisclosed', 'label': '原站未公開'}
# 觀察區錨點、綜合評分與 24 項條件的門檻總表仍在反推中（round2 §4）；交易規則改由 TRADING_RULES 聲明。
UNDISCLOSED_KEYS = ('observation_zone', 'composite_score', 'condition_thresholds')
PENDING = {'status': 'pending_reverse_engineering', 'label': '待反推'}

# 外部搜查 §3：原站前端明文（觀察區與價位不是買賣建議）；round2 §1：167 份回應都沒有交易規則欄位。
TRADING_RULES = {
    'status': 'not_provided_by_source',
    'label': '原站明示不提供買賣建議',
    'quote': '結構觀察區是依價格結構整理出的相對位置區間，不是買賣建議，也不代表價格漲到區間上緣前都適合進場。上緣不是目標價。',
}

# §5.3 已識別的來源權重；high_20／high_60 依研究「同群同價合計 2.2」各取 1.1（個別權重未公開，記在 provenance）。
# MA240：會員第二輪壓力群反推 9 群權重皆≈1.0（0.99～1.04），且本機 MA240 與原站 18/18 相同。
SHARED_KINDS = ('ma5', 'ma10', 'ma20', 'ma60', 'ma120', 'ma240', 'prior_high_20', 'high_20', 'high_60', 'intraday_high_60',
                'vp_center', 'vp_high', 'swing_high', 'breakout_failure', 'gap_down_upper', 'gap_down_lower',
                'heavy_volume', 'heavy_high')
# 權重原站未公開的來源：只參與支撐（支撐代表價取群組上緣、不用權重），不進壓力加權。
SUPPORT_ONLY_KINDS = ('low_20', 'low_60', 'vp_low', 'swing_low', 'gap_up_upper', 'gap_up_lower', 'heavy_low')
STRUCTURAL = {'prior_high_20', 'high_20', 'low_20', 'high_60', 'low_60', 'intraday_high_60', 'vp_center', 'vp_high',
              'vp_low', 'swing_high', 'swing_low', 'breakout_failure', 'gap_up_upper', 'gap_up_lower', 'gap_down_upper',
              'gap_down_lower', 'ma60', 'ma120', 'ma240'}

PROVENANCE = {
    'indicators': {'level': 'reference_reproduced', 'note': '均線（含 MA240）、RSI14、ATR14 與 20 日高低與原站算法相容'},
    'relative_volume': {'level': 'reference_reproduced', 'note': '均量窗口的取法依研究樣本相容'},
    'levels': {'level': 'reference_reproduced',
               'note': '來源、聚合容差、分群、距離分級與申報價位單位與原站算法相容；可觀察範圍與第一／第二壓力選取依會員回應 198/198'},
    'limit_up_reference': {'level': 'reference_text', 'note': '原站說明：創新高且上方無歷史結構時，以 10% 漲跌幅推算的明日漲停參考價標示（非結構價位）'},
    'defense': {'level': 'reference_reproduced', 'note': '會員第二輪 198/198；觀察區已形成時依賴觀察區下緣，錨點仍在反推'},
    'conditions': {'level': 'reference_reproduced', 'note': '條件文字取自原站會員頁；門檻依會員樣本反推，逐條附吻合率'},
    'trend_classification': {'level': 'reference_reproduced', 'note': '原站前端分類函式 198/198，但須有綜合評分，評分仍在反推'},
    'high_20_high_60_weights': {'level': 'partially_undisclosed', 'note': '近 20／60 日收盤高點的個別權重原站未公開，同價合計與原站一致'},
    'gap_candidates': {'level': 'partially_undisclosed', 'note': '缺口判定方式相容，完整候選母集合原站未公開'},
    'chart': {'level': 'generic_indicator', 'note': 'MACD 為通用指標，非原站規則'},
    'reference': {'level': 'fact', 'note': '事實資料'},
}
LABELS = {'not_advice': '分析數字是參考，不是買賣指令', 'price_basis': '價格未還原權值'}

# 條件：鍵＝本站中性名稱；site_id 只留在內部 provenance。rule 為反推出的判斷式；verified 為
# 第二輪會員樣本吻合率（大量股 擬合／留出；全部 擬合／留出），出處 reverse-engineering-round2.md §2。
CONFIRMED_RULES = {
    'ma_trend': ('T01', '均線多頭結構', '收盤價站上 MA5／MA20，且短均在長均之上、中期均線多頭排列',
                 '收盤>MA5 或 收盤>MA20 → 成立；否則 MA20>MA60 → 確認中；否則 未成立', '100%／100%；100%／100%'),
    'kd_bullish': ('T04', 'KD 黃金交叉', 'K 值向上穿越 D 值', 'K9>D9（初值 50）', '100%／100%；100%／100%'),
    'momentum_overheated': ('T05', '動能過熱鈍化', 'RSI 進入過熱鈍化區或 KD 位於高檔',
                            'RSI14（簡單平均）≥75 或 K9≥85 → 成立；否則 確認中', '100%／100%；100%／100%'),
    'near_prior_high': ('T06', '逼近前波高點', '收盤價逼近近 20 日高點區', '收盤 ≥ 0.98 × 近 20 日收盤最高', '100%／100%；100%／100%'),
    'volume_confirmed': ('V02', '量能確認', '上漲日成交量高於均量，價量同步',
                         '當日上漲 且 量比>1 → 成立；否則 確認中', '100%／97.6%；100%／97.9%'),
    'heavy_volume_stall': ('V03', '爆量未推升', '爆量但價未推進，或留下明顯長上影線', '量比≥2 且 當日漲跌幅≤0', '100%／100%；100%／100%'),
    'revenue_decline': ('F04', '營收衰退風險', '月營收出現重大衰退', '月營收年增≤−20%', '100%／100%；100%／100%'),
    'securities_lending_sale': ('C04', '借券賣壓', '借券餘額大幅增加或借券賣出量放大', '前一交易日借券賣出>0',
                                '100%／100%；100%／100%'),
}
APPROXIMATE_RULES = {
    'macd_bullish': ('T03', 'MACD 動能翻多', 'MACD 柱狀體翻正且 MACD 在訊號線之上', 'hist>0 且 DIF>DEA（12/26/9，柱＝DIF−DEA）',
                     '97.9%／95.2%；98.0%／93.6%'),
    'margin_healthy': ('C01', '融資籌碼健康', '股價上漲同時融資減少，或融資變化未同步放大',
                       '當日上漲 且 5 日融資增減<0（融資取前一交易日）', '96.6%／95.2%；96.7%／93.6%'),
    'short_interest': ('C03', '融券回補潛力', '融券餘額增加，形成潛在回補力道', '融券餘額>0（前一交易日）',
                       '92.5%／92.9%；92.1%／93.6%'),
    'institutional_reversal': ('I03', '法人買賣轉向', '由賣轉買（轉正）或由買轉賣（轉負）', '當日三大法人合計與 5 日合計反號',
                               '89.7%／90.5%；88.7%／91.5%'),
    'valuation_low_pe': ('F03', '估值結構', '本益比偏低為優勢；高估值且無成長支撐或股價淨值比偏高為風險',
                         '0<本益比≤15（本益比取前一交易日）', '80.8%／85.7%；81.5%／87.2%'),
    # round3（360 檔）留出集跌破 95%，由可當規格改為近似；吻合率出處 reverse-engineering-round3.md §1。
    'volume_contraction': ('V05', '量縮整理', '回檔量縮整理形成平台；高檔量縮則視為承接轉弱', '量比<0.7',
                           '98.2%／94.4%；98.2%／93.6%（第三輪 360 檔）'),
}


def _state(trigger: bool | None, confirm: bool = False) -> str | None:
    if trigger is None:
        return None
    return 'triggered' if trigger else ('confirming' if confirm else 'off')


def _all(*values) -> bool:
    return all(v is not None for v in values)


def condition_states(x: dict) -> dict:
    """依反推規則計算條件狀態；輸入缺值時狀態為 None（不當成未成立）。"""
    c, prev = x.get('close'), x.get('prev_close')
    up = c > prev if _all(c, prev) else None
    change_le_zero = c <= prev if _all(c, prev) else None
    vr = x.get('relative_volume')
    ma5, ma20, ma60 = x.get('ma5'), x.get('ma20'), x.get('ma60')

    def ma_trend():
        if not _all(c, ma5, ma20):
            return None
        if c > ma5 or c > ma20:
            return 'triggered'
        if ma60 is None:
            return None
        return 'confirming' if ma20 > ma60 else 'off'

    def overheated():
        rsi, k = x.get('rsi14'), x.get('k9')
        if rsi is None and k is None:
            return None
        return _state((rsi is not None and rsi >= 75) or (k is not None and k >= 85), True)

    def volume_confirmed():
        return None if up is None or vr is None else _state(up and vr > 1, True)

    confirmed = {
        'ma_trend': ma_trend(),
        'kd_bullish': _state(x['k9'] > x['d9']) if _all(x.get('k9'), x.get('d9')) else None,
        'momentum_overheated': overheated(),
        'near_prior_high': _state(c >= 0.98 * x['high20_close']) if _all(c, x.get('high20_close')) else None,
        'volume_confirmed': volume_confirmed(),
        'heavy_volume_stall': _state(vr >= 2 and change_le_zero) if _all(vr, change_le_zero) else None,
        'revenue_decline': _state(x['revenue_yoy_pct'] <= -20) if x.get('revenue_yoy_pct') is not None else None,
        'securities_lending_sale': _state(x['borrow_sold'] > 0) if x.get('borrow_sold') is not None else None,
    }
    total, total5 = x.get('inst_total_net'), x.get('inst_total_net5')
    pe = x.get('pe')
    approximate = {
        'macd_bullish': _state(x['macd_hist'] > 0 and x['macd_dif'] > x['macd_dea'])
        if _all(x.get('macd_hist'), x.get('macd_dif'), x.get('macd_dea')) else None,
        'margin_healthy': _state(up and x['margin_change5'] < 0) if _all(up, x.get('margin_change5')) else None,
        'short_interest': _state(x['short_balance'] > 0) if x.get('short_balance') is not None else None,
        'institutional_reversal': _state((total > 0 > total5) or (total < 0 < total5)) if _all(total, total5) else None,
        'valuation_low_pe': _state(0 < pe <= 15) if pe is not None else None,
        'volume_contraction': _state(vr < 0.7) if vr is not None else None,
    }

    def pack(rules, states, label):
        return {key: {'state': states[key], 'name': name, 'definition': definition, 'rule': rule, 'verified': verified,
                      'label': label} for key, (_, name, definition, rule, verified) in rules.items()}

    pending = 24 - len(CONFIRMED_RULES) - len(APPROXIMATE_RULES)
    return {
        'confirmed': pack(CONFIRMED_RULES, confirmed, '與原站樣本相符'),
        'approximate': pack(APPROXIMATE_RULES, approximate, '近似（吻合率 80～95%）'),
        'pending': {**PENDING, 'count': pending},
        # 原站 ID 只留在模組常數（內部 provenance），不出現在 payload。
        'sample': {'size': 198, 'source': '參考站會員樣本第二輪（2026-11-06）'},
    }


def classify_trend(score: float, risk_flags: int, rsi14: float | None) -> str:
    """原站前端分類函式（交接 §4.3；第二輪 198/198）。須由綜合評分驅動。"""
    if score <= 35 or risk_flags >= 2 or (rsi14 is not None and rsi14 <= 25 and score < 45):
        return 'risk'
    if score >= 75:
        return 'strong'
    if score < 45:
        return 'weak'
    return 'watch'


def trend_classification_payload(score: float | None = None, risk_flags: int = 0, rsi14: float | None = None) -> dict:
    if score is None:
        return {'status': 'pending_composite_score', 'label': '待總分', 'value': None,
                'rule': '評分≤35 或 (RSI14≤25 且 評分<45) → 風險；否則 評分≥75 → 強勢；評分<45 → 偏弱；其餘 觀察'}
    return {'status': 'computed', 'value': classify_trend(score, risk_flags, rsi14)}


def defense_payload(low20_intraday: float | None) -> dict:
    """防守價（第二輪 198/198）。未形成觀察區時＝近 20 日盤中最低；已形成時依賴觀察區下緣，錨點仍在反推。"""
    return {
        'status': 'pending_observation_zone', 'label': '待觀察區',
        'value_if_zone_not_formed': low20_intraday,
        'rule': '觀察區已形成：min(近 20 日盤中最低, 觀察區下緣 − 1.5×ATR14)；未形成：近 20 日盤中最低',
        'verified': '198/198（含已形成 36、未形成 162）',
    }


def observable_range_pct(atr_pct: float | None) -> float | None:
    """可觀察範圍：round(clamp(4×ATR%, 8, 20), 2)；超出者為長期結構（會員回應 198/198）。"""
    return None if atr_pct is None else round(min(20.0, max(8.0, 4 * atr_pct)), 2)


def select_resistances(clusters: list[dict], tolerance_pct: float, range_pct: float) -> tuple[dict | None, dict | None]:
    """第一壓力：可觀察範圍內、來源≥2 的最低群；第二壓力：其後第一個來源≥2、且與第一壓力相距≥2×容差的群（198/198）。"""
    usable = [c for c in clusters if c['distance_pct'] <= range_pct and c['signal_count'] >= 2]
    if not usable:
        return None, None
    first = usable[0]
    second = next((c for c in usable[1:] if (c['level'] - first['level']) / first['level'] * 100 >= 2 * tolerance_pct), None)
    return first, second


def _rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    moves = [b - a for a, b in zip(closes[-15:], closes[-14:])]
    up = sum(m for m in moves if m > 0) / 14
    down = -sum(m for m in moves if m < 0) / 14
    return None if up + down == 0 else round(100 * up / (up + down), 1)


def indicators_v2(bars: list[Bar]) -> dict:
    closes = [b.close for b in bars]
    last = bars[-1]
    vols = [b.volume for b in bars[-20:]]
    avg_vol20 = round(sum(vols) / 20) if len(vols) >= 20 else None
    atr = atr14(bars)
    prior = bars[-21:-1]
    kd = kd9(bars)
    m = macd(closes)
    return {
        **{f'ma{n}': ma(closes, n) for n in (5, 10, 20, 60, 120, 240)},
        'rsi14': _rsi14(closes), 'atr14': atr,
        'atr_pct': round(atr / last.close * 100, 2) if atr else None,
        'relative_volume': round(last.volume / max(1, avg_vol20), 2) if avg_vol20 else None,
        'high20_close': max(closes[-20:]) if len(closes) >= 20 else None,
        'low20_close': min(closes[-20:]) if len(closes) >= 20 else None,
        'prior_high20': max(b.high for b in prior) if len(prior) >= 20 else None,
        'low20_intraday': min(b.low for b in bars[-20:]) if len(bars) >= 20 else None,
        # 條件判斷用的通用指標快照（T03／T04 的輸入）；序列仍在 chart。
        'k9': kd[-1][0] if kd else None, 'd9': kd[-1][1] if kd else None,
        'macd_dif': m[-1][0] if len(closes) >= 34 else None, 'macd_dea': m[-1][1] if len(closes) >= 34 else None,
        'macd_hist': m[-1][2] if len(closes) >= 34 else None,
    }


def _candidates(bars: list[Bar], x: dict) -> list[dict]:
    close = bars[-1].close
    out = []

    def add(kind, value, label, date=None):
        if value is not None and value > 0:
            out.append({'kind': kind, 'value': round(value, 2), 'label': label, 'date': date})

    for n in (5, 10, 20, 60, 120, 240):
        add(f'ma{n}', x.get(f'ma{n}'), f'MA{n}')
    closes = [b.close for b in bars]
    add('prior_high_20', x.get('prior_high20'), '前 20 日盤中高點')
    if len(bars) >= 20:
        add('high_20', max(closes[-20:]), '近 20 日收盤高點')
        add('low_20', min(b.low for b in bars[-20:]), '近 20 日盤中低點')
    if len(bars) >= 60:
        add('high_60', max(closes[-60:]), '近 60 日收盤高點')
        add('low_60', min(closes[-60:]), '近 60 日收盤低點')
        add('intraday_high_60', max(b.high for b in bars[-60:]), '近 60 日盤中高點')
        vp_bars = bars[-60:]
        low, high = min(b.close for b in vp_bars), max(b.close for b in vp_bars)
        if high > low:
            width = (high - low) / 20
            volumes = [0] * 20
            for b in vp_bars:
                volumes[min(19, max(0, math.floor((b.close - low) / width)))] += b.volume
            j = max(range(20), key=lambda i: (volumes[i], -i))
            add('vp_low', low + j * width, '成交量分布下界')
            add('vp_center', low + (j + 0.5) * width, '成交量分布密集價')
            add('vp_high', low + (j + 1) * width, '成交量分布上界')

    swing_bars = bars[-120:]
    for i in range(2, len(swing_bars) - 2):
        cur = swing_bars[i]
        around = swing_bars[i - 2:i] + swing_bars[i + 1:i + 3]
        if all(cur.high >= b.high for b in around):
            add('swing_high', cur.high, '前波高點轉支撐' if cur.high < close * 0.999 else '前波高點', cur.date)
        if all(cur.low <= b.low for b in around):
            add('swing_low', cur.low, '波段低點', cur.date)

    for i in range(max(20, len(bars) - 60), len(bars)):
        p_high = max(b.high for b in bars[i - 20:i])
        if bars[i].high > p_high and bars[i].close < p_high:
            add('breakout_failure', p_high, '假突破價位', bars[i].date)

    for i in range(1, len(swing_bars)):
        prev, cur = swing_bars[i - 1], swing_bars[i]
        later = swing_bars[i + 1:]
        if cur.low > prev.high and not any(b.low <= prev.high for b in later):
            add('gap_up_upper', cur.low, '未回補向上缺口上緣', cur.date)
            add('gap_up_lower', prev.high, '未回補向上缺口下緣', cur.date)
        elif cur.high < prev.low and not any(b.high >= prev.low for b in later):
            add('gap_down_upper', prev.low, '未回補向下缺口上緣', cur.date)
            add('gap_down_lower', cur.high, '未回補向下缺口下緣', cur.date)

    for b in sorted([b for b in bars[-60:] if b.volume > 0], key=lambda b: -b.volume)[:9]:
        add('heavy_volume', b.close, '大量成交日收盤', b.date)
        add('heavy_high', b.high, '大量成交日高點', b.date)
        add('heavy_low', b.low, '大量成交日低點', b.date)
    return out


def _tier(distance_pct: float) -> str:
    distance = abs(distance_pct)
    return 'near' if distance <= 10 else 'mid' if distance <= 20 else 'far'


def _weight(kind: str) -> float | None:
    if kind in ('high_20', 'high_60'):
        return 1.1
    if kind == 'ma240':
        return 1.0
    return WEIGHTS[kind] if kind in SHARED_KINDS else None


def _cluster(group: list[dict], close: float, side: str) -> dict:
    low, high = min(f['value'] for f in group), max(f['value'] for f in group)
    weights = [_weight(f['kind']) for f in group]
    known = all(w is not None for w in weights)
    if side == 'resistance':
        level = round(sum(w * f['value'] for w, f in zip(weights, group)) / sum(weights), 2)
        tick = resistance_tick(level, close)
    else:
        level = high
        tick = support_tick(level, close)
    families = {f['kind'].split('_')[0] for f in group}
    strength = round(sum(weights) + 0.6 * max(0, len(families) - 1), 2) if known else None
    distance = round((level - close) / close * 100, 2)
    return {'tick': tick, 'level': level, 'low': round(low, 2), 'high': round(high, 2), 'distance_pct': distance,
            'tier': _tier(distance), 'signal_count': len(group), 'structural': any(f['kind'] in STRUCTURAL for f in group),
            'strength_score': strength, 'sources': [{k: f[k] for k in ('kind', 'label', 'value', 'date')} for f in group]}


def price_levels_v2(bars: list[Bar], x: dict) -> dict:
    empty = {'aggregation_tolerance_pct': None, 'observable_range_pct': None, 'resistance': [], 'support': [],
             'resistance_all': [], 'support_all': [], 'no_resistance_above': False, 'limit_up_reference': None}
    if not x.get('atr_pct'):
        return empty
    close = bars[-1].close
    tolerance = round(min(3.0, max(1.0, 0.6 * x['atr_pct'])), 2)
    observable = observable_range_pct(x['atr_pct'])
    factors = _candidates(bars, x)

    above = sorted((f for f in factors if f['kind'] in SHARED_KINDS and f['value'] > close * 1.001), key=lambda f: f['value'])
    groups: list[list[dict]] = []
    for f in above:
        if groups and (f['value'] - groups[-1][0]['value']) / groups[-1][0]['value'] * 100 <= tolerance:
            groups[-1].append(f)
        else:
            groups.append([f])
    resistance_all = [_cluster(g, close, 'resistance') for g in groups]

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
    supports = [_cluster(g, close, 'support') for g in support_groups]
    support_all = [c for c in supports if c['structural'] or c['signal_count'] >= 2] or supports
    first, second = select_resistances(resistance_all, tolerance, observable)
    # 原站說明：創新高且上方無歷史結構時，以 10% 漲跌幅限制推算的明日漲停參考價標示（非結構價位）。
    limit_up = None if above else {'value': tick_floor(close * 1.10), 'label': '明日漲停參考價',
                                   'note': '上方無歷史結構，依 10% 漲跌幅推算；非結構價位'}
    return {
        'aggregation_tolerance_pct': tolerance,
        'observable_range_pct': observable,
        'resistance': [c for c in (first, second) if c is not None],
        'support': [c for c in support_all if c['tier'] != 'far'][:2],
        'resistance_all': resistance_all, 'support_all': support_all,
        'no_resistance_above': not above,
        'limit_up_reference': limit_up,
    }


def _blocking(code: str, as_of: str, qa: dict) -> dict:
    return {'schema': SCHEMA, 'analysis_version': ANALYSIS_VERSION, 'code': code, 'as_of': as_of, 'requested_as_of': as_of,
            'qa': {'blocking': True, **qa}, 'trading_rules': dict(TRADING_RULES),
            'undisclosed': {k: dict(UNDISCLOSED) for k in UNDISCLOSED_KEYS}, 'provenance': PROVENANCE, 'labels': LABELS}


# ---- 條件輸入：既有本機原件唯讀（不新增抓取）；查不到就給 None，條件狀態跟著為 None ----

def _safe(fn):
    try:
        return fn()
    except (OSError, ValueError, KeyError, IndexError, TypeError, __import__('sqlite3').Error):
        return None


def _listed_chip_inputs(code: str, as_of: str) -> dict:
    from . import chips
    days = [d for d in chips.available_days() if d <= as_of]
    if len(days) < 2:
        return {}
    # 原站融資融券與借券取「前一交易日」（會員回應 marginShort.dataDate＝T−1，198/198）；法人取當日。
    credit_day = days[-2]
    margin_today = chips.margin(code, [credit_day])[0]
    margin_base = chips.margin(code, [days[-7]])[0] if len(days) >= 7 else None
    inst = chips.institutional(code, days[-5:]) if len(days) >= 5 else []
    borrow = chips.borrow(code, [credit_day])[0]
    totals = [row['total'] for row in inst if row is not None]
    return {
        'borrow_sold': borrow['sold'] if borrow else None,
        'margin_change5': margin_today['margin'] - margin_base['margin'] if margin_today and margin_base else None,
        'short_balance': margin_today['short'] if margin_today else None,
        'inst_total_net': inst[-1]['total'] if inst and inst[-1] is not None else None,
        'inst_total_net5': sum(totals) if len(totals) == 5 else None,
    }


def _otc_chip_inputs(code: str, as_of: str) -> dict:
    import sqlite3
    path = DEFAULT_OTC_DB.parent / 'otc_market.sqlite'
    connection = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    try:
        rows = connection.execute('SELECT date, total_net FROM institutional WHERE code = ? AND date <= ? ORDER BY date DESC LIMIT 5',
                                  (code, as_of)).fetchall()
    finally:
        connection.close()
    totals = [r[1] for r in rows if r[1] is not None]
    # 上櫃融資融券與借券原件本機沒有：C01／C03／C04 狀態為 None，不當成 0。
    return {'inst_total_net': rows[0][1] if rows else None, 'inst_total_net5': sum(totals) if len(totals) == 5 else None}


_REVENUE_CACHE: dict[tuple, dict] = {}


def institutional_series(code: str, market: str, dates: list[str]) -> list[dict]:
    """回傳與 K 線日期對齊的三大法人個別買賣超；缺日省略，不補成零。"""
    if not dates:
        return []
    import sqlite3
    data_root = DEFAULT_DB.parents[1]
    path = (data_root / 'listed-institutional' / 'listed_institutional.sqlite'
            if market == '上市' else data_root / 'otc-history' / 'otc_market.sqlite')
    if not path.exists():
        return []
    connection = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True)
    try:
        rows = connection.execute(
            'SELECT date, foreign_net, trust_net, dealer_net, total_net FROM institutional '
            'WHERE code = ? AND date >= ? AND date <= ? ORDER BY date',
            (code, dates[0], dates[-1]),
        ).fetchall()
    finally:
        connection.close()
    allowed = set(dates)
    return [
        {'date': date, 'foreign': foreign, 'trust': trust, 'dealer': dealer, 'total': total}
        for date, foreign, trust, dealer, total in rows if date in allowed
    ]


def _revenue_yoy(code: str, market: str, as_of: str) -> float | None:
    import json
    from datetime import date
    import fundamentals_history as fh
    root = DEFAULT_DB.parents[1] / 'fundamentals-history'
    meta = _REVENUE_CACHE.get(('meta',))
    if meta is None:
        meta = json.loads((root / 'meta.json').read_text(encoding='utf-8'))
        _REVENUE_CACHE[('meta',)] = meta
    key_market = 'listed' if market == '上市' else 'otc'
    files = [f for f in meta['files'] if f['dataset'] == 'monthly_revenue' and f['market'] == key_market
             and f['available_on'] <= as_of]
    if not files:
        return None
    latest = max(f['period'] for f in files)
    year, month = latest.split('-')
    prior = f'{int(year) - 1}-{month}'

    def revenue(period: str) -> int | None:
        for item in (f for f in meta['files'] if f['dataset'] == 'monthly_revenue' and f['market'] == key_market
                     and f['period'] == period):
            cache_key = ('file', item['file'])
            if cache_key not in _REVENUE_CACHE:
                content = (root / item['file']).read_bytes()
                rows = fh.parse_monthly_revenue(content, market=key_market, period=period, source_file=item['file'],
                                                fetched_at_utc=item['fetched_at_utc'], sha256=item['sha256'],
                                                source_scope=item.get('source_scope', 'domestic'))
                _REVENUE_CACHE[cache_key] = {r['code']: r['revenue_thousand_twd'] for r in rows}
            value = _REVENUE_CACHE[cache_key].get(code)
            if value is not None:
                return value
        return None

    date.fromisoformat(as_of)
    now, then = revenue(latest), revenue(prior)
    return round((now - then) / then * 100, 2) if now is not None and then else None


def _pe(code: str, as_of: str) -> float | None:
    import valuation_history
    row = valuation_history.as_of(valuation_history.DEFAULT_DB, as_of, code=code)
    return row['pe'] if row else None


def condition_inputs(code: str, market: str, as_of: str, bars: list[Bar], x: dict) -> dict:
    chips_part = _safe(lambda: _listed_chip_inputs(code, as_of) if market == '上市' else _otc_chip_inputs(code, as_of)) or {}
    # 原站估值取前一交易日（會員回應 fundamentals.dataDate＝T−1）。
    prior_day = bars[-2].date if len(bars) >= 2 else as_of
    return {
        'close': bars[-1].close, 'prev_close': bars[-2].close if len(bars) >= 2 else None,
        **{k: x.get(k) for k in ('ma5', 'ma20', 'ma60', 'macd_hist', 'macd_dif', 'macd_dea', 'k9', 'd9', 'rsi14',
                                 'high20_close', 'relative_volume')},
        'revenue_yoy_pct': _safe(lambda: _revenue_yoy(code, market, as_of)),
        'pe': _safe(lambda: _pe(code, prior_day)),
        **{k: chips_part.get(k) for k in ('borrow_sold', 'margin_change5', 'short_balance', 'inst_total_net', 'inst_total_net5')},
    }


def analyze_v2(code: str, as_of: str, db_path=DEFAULT_DB, otc_db_path=DEFAULT_OTC_DB) -> dict:
    bars, info = load_bars(code, as_of, db_path=db_path, otc_db_path=otc_db_path)
    if not bars:
        return _blocking(code, as_of, {'reason': '查不到這個代號的有效日 K', 'not_found': True})
    if len(bars) < MIN_ANALYSIS_BARS:
        return _blocking(code, as_of, {'bars': len(bars), 'reason': f'有效日 K 只有 {len(bars)} 根，不足 {MIN_ANALYSIS_BARS} 根'})
    x = indicators_v2(bars)
    last = bars[-1]
    inputs = condition_inputs(code, info['market'], last.date, bars, x)
    levels = price_levels_v2(bars, x)
    conditions = condition_states(inputs)
    chart_bars = bars[-120:]
    institutional = _safe(lambda: institutional_series(code, info['market'], [bar.date for bar in chart_bars])) or []
    # strategy_v1 尚未通過驗證、對外預設關閉（本人 11-11／11-12 定案）：它出錯只讓 strategy 留空，
    # 不能讓這檔的行情、指標、價位、法人等核心資料跟著消失。
    try:
        strategy = strategy_v1(close=last.close, indicators=x, levels=levels, conditions=conditions,
                               institutional=institutional)
    except Exception:
        strategy = None
    return {
        'schema': SCHEMA, 'analysis_version': ANALYSIS_VERSION,
        'code': code, 'name': info['name'], 'kind': info['kind'], 'market': info['market'],
        'as_of': last.date, 'requested_as_of': as_of,
        'quote': {'close': last.close, 'chg_pct': round(info['chg'], 2), 'volume': last.volume, 'change_sign': info['change_sign']},
        'qa': {'blocking': False, 'bars': len(bars)},
        'indicators': x,
        'levels': levels,
        'strategy': strategy,
        'institutional': institutional,
        'defense': defense_payload(x['low20_intraday']),
        'trend_classification': trend_classification_payload(),
        'conditions': conditions,
        'trading_rules': dict(TRADING_RULES),
        'reference': {'low20_intraday': {'value': x['low20_intraday'], 'label': '近 20 日盤中低點',
                                         'note': '事實資料；觀察區未形成時即為防守價'}},
        'undisclosed': {k: dict(UNDISCLOSED) for k in UNDISCLOSED_KEYS},
        'provenance': PROVENANCE,
        'bars': [{'date': b.date, 'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close, 'volume': b.volume} for b in chart_bars],
        'chart': {
            'parameters': {'fast': 12, 'slow': 26, 'signal': 9, 'histogram_multiplier': 1,
                           'seed': 'first_close', 'warmup_bars': 34, 'precision': 3,
                           'missing_policy': 'omit_missing_price_rows', 'version': 'macd-first-close-v1'},
            'macd': [{'date': b.date, 'dif': m[0], 'dea': m[1], 'hist': m[2], 'ready': i >= 33}
                     for i, (b, m) in enumerate(zip(bars, macd([b.close for b in bars])))][-120:],
        },
        'labels': LABELS,
    }
