"""24 招條件（handoff §3，2TON-FIT-V1，本站自有門檻）。這裡先放價量可算的 T01–T06、V01–V06。

每招回傳 {'id','name','family','horizon','direction','price_role','status','evidence'}；
status：triggered／confirming／off；資料不足時 status='unavailable'（不能把缺資料當 off）。
"""
from __future__ import annotations

META = {
    'T01': ('均線多頭結構', 'trend', 'swing', 'bullish', 'both'),
    'T02': ('趨勢型態與均線失守', 'trend', 'swing', 'neutral', 'modifier'),
    'T03': ('MACD 動能翻多', 'trend', 'swing', 'bullish', 'modifier'),
    'T04': ('KD 黃金交叉', 'trend', 'short', 'bullish', 'modifier'),
    'T05': ('動能過熱鈍化', 'trend', 'short', 'bearish', 'modifier'),
    'T06': ('逼近前波高點', 'trend', 'short', 'neutral', 'pressure'),
    'V01': ('帶量突破', 'volume_price', 'short', 'bullish', 'pressure'),
    'V02': ('量能確認', 'volume_price', 'short', 'bullish', 'modifier'),
    'V03': ('爆量不漲調節', 'volume_price', 'short', 'bearish', 'pressure'),
    'V04': ('高檔爆量竭盡', 'volume_price', 'short', 'bearish', 'modifier'),
    'V05': ('整理平台與量縮', 'volume_price', 'swing', 'neutral', 'both'),
    'V06': ('成交量分布結構', 'volume_price', 'swing', 'neutral', 'pressure'),
}


def _result(sid, status, evidence):
    name, family, horizon, direction, role = META[sid]
    return {'id': sid, 'name': name, 'family': family, 'horizon': horizon, 'direction': direction,
            'price_role': role, 'status': status, 'evidence': evidence}


def trend_and_volume(x: dict, chg: float, valid_bars: int) -> list[dict]:
    c = x['close']
    out = []

    ma5, ma10, ma20, ma60 = x['ma5'], x['ma10'], x['ma20'], x['ma60']
    if ma5 is None:
        out.append(_result('T01', 'unavailable', {'reason': 'MA5 不足'}))
    else:
        hit = (c >= ma5 and (ma20 is None or c >= ma20)) or (None not in (ma10, ma20) and c >= ma20 and ma5 >= ma10)
        confirming = not hit and None not in (ma20, ma60) and ma20 >= ma60 * 0.98
        out.append(_result('T01', 'triggered' if hit else 'confirming' if confirming else 'off',
                           {'close': c, 'ma5': ma5, 'ma10': ma10, 'ma20': ma20, 'ma60': ma60}))

    if ma5 is None or ma20 is None:
        out.append(_result('T02', 'unavailable', {'reason': 'MA5／MA20 不足'}))
    else:
        weak = c < ma5 and c < ma20
        out.append(_result('T02', 'triggered', {'structure': '結構轉弱' if weak else '結構未破', 'ma20_slope5': x['ma20_slope5']}))

    hist, dif, dea, ph, pph = x['hist'], x['dif'], x['dea'], x['prev_hist'], x['prev2_hist']
    if ph is None or pph is None:
        out.append(_result('T03', 'unavailable', {'reason': 'MACD 歷史不足'}))
    else:
        status = 'triggered' if hist > 0 and dif > dea else 'confirming' if hist < 0 and hist > ph and ph >= pph else 'off'
        out.append(_result('T03', status, {'dif': dif, 'dea': dea, 'hist': hist, 'prev_hist': ph}))

    k, d, pk, pd = x['k'], x['d'], x['prev_k'], x['prev_d']
    if k is None or pk is None:
        out.append(_result('T04', 'unavailable', {'reason': 'KD 歷史不足'}))
    else:
        status = 'triggered' if k > d and (pk <= pd or (k > 50 and k - d >= 2)) else 'confirming' if k > d else 'off'
        out.append(_result('T04', status, {'k': k, 'd': d, 'prev_k': pk, 'prev_d': pd}))

    rsi = x['rsi14']
    if rsi is None or k is None:
        out.append(_result('T05', 'unavailable', {'reason': 'RSI／KD 不足'}))
    else:
        status = 'triggered' if rsi >= 75 or k >= 85 else 'confirming' if rsi >= 65 or k >= 80 else 'off'
        out.append(_result('T05', status, {'rsi14': rsi, 'k': k}))

    ref_high = x['high20_close'] if x['high20_close'] is not None else x['prior_high20']
    if ref_high is None:
        out.append(_result('T06', 'unavailable', {'reason': '前高不足'}))
    else:
        out.append(_result('T06', 'triggered' if c >= ref_high * 0.97 else 'off', {'close': c, 'ref_high': ref_high}))

    vr = x['vol_ratio']
    p_high = x['prior_high20'] if x['prior_high20'] is not None else x['high20_close']
    if vr is None or p_high is None:
        out.append(_result('V01', 'unavailable', {'reason': '量比或前高不足'}))
    else:
        out.append(_result('V01', 'triggered' if c > p_high and vr >= 1.50 else 'off', {'close': c, 'prior_high': p_high, 'vol_ratio': vr}))

    if vr is None:
        for sid in ('V02', 'V03', 'V04', 'V05'):
            out.append(_result(sid, 'unavailable', {'reason': '量比不足（少於 20 根）'}))
    else:
        out.append(_result('V02', 'triggered' if chg >= 1.0 and vr >= 1.30 else 'confirming' if vr >= 0.50 else 'off',
                           {'chg': round(chg, 2), 'vol_ratio': vr}))
        span = x['high'] - x['low']
        upper = (x['high'] - max(x['open'], c)) / span if span > 0 else 0.0
        out.append(_result('V03', 'triggered' if vr >= 2.0 and (chg <= 0 or upper >= 0.55) else 'off',
                           {'vol_ratio': vr, 'chg': round(chg, 2), 'upper_shadow': round(upper, 2)}))
        bias20 = (c - ma20) / ma20 if ma20 else None
        out.append(_result('V04', 'triggered' if bias20 is not None and bias20 >= 0.12 and vr >= 2.2 else 'off',
                           {'bias20': None if bias20 is None else round(bias20, 4), 'vol_ratio': vr}))
        out.append(_result('V05', 'triggered' if vr <= 0.65 else 'off', {'vol_ratio': vr}))

    out.append(_result('V06', 'triggered' if valid_bars >= 20 else 'off', {'valid_bars': valid_bars}))
    return out
