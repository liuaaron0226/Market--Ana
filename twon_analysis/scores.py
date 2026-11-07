"""五維分數（handoff §4.1，本站自有計分，非原站公式）。這裡先放價量可算的 T、V 與風險旗標 RF1。"""
from __future__ import annotations


def clamp(low, high, value):
    return max(low, min(high, value))


def trend_score(x: dict) -> int:
    c, ma5, ma10, ma20, ma60 = x['close'], x['ma5'], x['ma10'], x['ma20'], x['ma60']
    if ma20 is None:
        if ma5 is None:
            return 50
        return 65 if c >= ma5 else 35
    ma60 = ma60 if ma60 is not None else ma20
    ma5 = ma5 if ma5 is not None else c
    ma10 = ma10 if ma10 is not None else ma5
    if c >= ma20 and ma20 >= ma60 and (c >= ma5 or ma5 >= ma10):
        return 100
    if c >= ma20 and ma20 < ma60:
        return 68
    if c < ma20 and c >= ma60:
        return 38
    if c < ma60 and c >= ma5:
        return 26
    return 20 if x['rsi14'] is not None and x['rsi14'] <= 25.5 else 24


def volume_score(x: dict, chg: float) -> int:
    v = 50.0
    vr = x['vol_ratio'] if x['vol_ratio'] is not None else 1
    if chg > 0.8:
        v += min(14, (vr - 0.9) * 15 + chg * 1.1)
    if chg < -0.5 and vr > 1.3:
        v -= 4
    return int(clamp(0, 100, round(v)))


def rf1(x: dict) -> bool:
    c, ma5, ma20, ma60 = x['close'], x['ma5'], x['ma20'], x['ma60']
    return ma20 is not None and c < ma20 and ((ma5 is not None and c < ma5) or (ma60 is not None and c < ma60))


def chip_score(derived: dict, chg: float) -> int | None:
    """籌碼 C：沒有融資資料回 None（不再用中性 50 冒充有資料）。借券沒有資料，相關項以 0 計並在畫面標示。"""
    if 'm5' not in derived:
        return None
    m5, margin_diff = derived['m5'], derived['margin_diff5']
    margin_term = 0.90 * m5 if m5 >= 0 else 0.35 * m5
    raw = round(52 - margin_term + (8 if chg > 0 and margin_diff < 0 else 0))
    return int(clamp(0, 100, raw))


def institutional_score(derived: dict, avg_vol: int | None) -> int | None:
    if 'inst_today' not in derived:
        return None
    avg = max(1, avg_vol or 1)
    days = derived['inst_days']
    d20 = derived['inst_d20'] / (20 * avg) if days >= 20 else 0
    value = 48 + 52 * derived['inst_today'] / avg + 110 * derived['inst_d5'] / (5 * avg) + 55 * d20 + (8 if derived['trust_streak'] >= 3 else 0)
    return int(clamp(11, 100, round(value)))
