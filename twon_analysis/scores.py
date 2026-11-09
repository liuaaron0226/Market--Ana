"""【已退役 2026-11-08】本人定案「自創部分不保留」；個股分析改由 analyze_v2.py 輸出（契約 .scratch/chart-screening-workbench/stock-analysis-contract-v2.md）。本檔僅為既有呼叫端相容而保留，不用於新產品輸出。

五維分數（handoff §4.1，本站自有計分，非原站公式）。這裡先放價量可算的 T、V 與風險旗標 RF1。"""
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
    avg = max(1, derived.get('avg_vol') or 1)
    borrow5d = derived.get('borrow_diff5', 0) / avg
    borrow_sale = derived.get('borrow_sold', 0) / avg * 100
    margin_term = 0.90 * m5 if m5 >= 0 else 0.35 * m5
    raw = round(52 - margin_term - 14 * borrow5d - 0.45 * borrow_sale + (8 if chg > 0 and margin_diff < 0 else 0))
    return int(clamp(0, 100, raw - (18 if m5 > 4 and borrow_sale > 15 and raw > 35 else 0)))


def fundamental_score(rev: dict | None, val: dict | None) -> int | None:
    """基本面 F（handoff §4.1）；估值與營收都沒有時回 None。EPS 不進 F。"""
    if rev is None and val is None:
        return None
    f = 52.0
    yoy = rev['yoy'] if rev and rev['yoy'] is not None else None
    mom = rev['mom'] if rev and rev['mom'] is not None else None
    if yoy is not None:
        f += clamp(-18, 20, yoy * 0.38)
    if mom is not None and mom < -5:
        f += max(-8, mom * 0.45)
    elif mom is not None and mom > 0 and (yoy or 0) > 0:
        f += min(5, mom * 0.2)
    per = val['per'] if val else None
    pbr = val['pbr'] if val else None
    dividend_yield = val['yield'] if val else None
    if per is not None:
        f += 5 if per <= 16 else -10 if per >= 55 else -6 if per >= 40 else 0
    if pbr is not None:
        f += 3 if pbr <= 1.2 else -6 if pbr >= 12 else 0
    if dividend_yield is not None and dividend_yield >= 4:
        f += 4
    return int(clamp(0, 100, round(f)))


def institutional_score(derived: dict, avg_vol: int | None) -> int | None:
    if 'inst_today' not in derived:
        return None
    avg = max(1, avg_vol or 1)
    days = derived['inst_days']
    d20 = derived['inst_d20'] / (20 * avg) if days >= 20 else 0
    value = 48 + 52 * derived['inst_today'] / avg + 110 * derived['inst_d5'] / (5 * avg) + 55 * d20 + (8 if derived['trust_streak'] >= 3 else 0)
    return int(clamp(11, 100, round(value)))
