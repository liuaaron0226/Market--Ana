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
