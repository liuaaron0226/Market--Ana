"""個股分析入口：代號＋日期 → 24 招、五維、總分、價位結構（結果投影，不含公式與權重）。

總分五步（handoff §4.2，2TON-FIT-V1 預設 preset，本站自有）。本人定案最終要 24 招與五維總分全部到齊；
目前籌碼只有近 20 日原件、借券／基本面尚未接入，缺的維度回 None 並註明，總分在五維未到齊時標「未完整」，
不用中性分冒充有資料（ANALYSIS_PLAN 11-07 本人定案）。
"""
from __future__ import annotations

import hashlib
import json

from . import chips
from .conditions import chip_and_institutional, fundamentals_unavailable, trend_and_volume
from .data import load_bars
from .indicators import indicators
from .price_structure import structure
from .scores import chip_score, clamp, institutional_score, rf1, trend_score, volume_score

VERSION = 'MA-TWON-ANALYSIS-PV1'
FORMULA = '2TON-FIT-V1+price:2ton-research'
PRESET = {'T': .40, 'C': .10, 'V': .25, 'I': .15, 'F': .10, 'res': 6, 'oh_rsi': 6, 'oh_margin': 6, 'oh_hot': 4, 'risk': 6}
STATUS_LABEL = {'triggered': '成立', 'confirming': '醞釀中', 'off': '未成立', 'unavailable': '資料不足'}


def _inst_cost(code: str, bars, days: list[str]) -> float | None:
    by_date = {b.date: b for b in bars[-25:]}
    weighted = net_sum = 0.0
    for day, row in zip(days[-20:], chips.institutional(code, days[-20:])):
        bar = by_date.get(day)
        if not row or row['total'] <= 0 or not bar:
            continue
        vwap = (bar.high + bar.low + bar.close) / 3
        weighted += vwap * row['total']
        net_sum += row['total']
    return round(weighted / net_sum, 2) if net_sum > 0 else None


def trend_class(score: int, risk_flags: int, rsi: float | None) -> str:
    if score <= 35 or risk_flags >= 2 or (rsi is not None and rsi <= 25 and score < 45):
        return 'risk'
    return 'strong' if score >= 75 else 'weak' if score < 45 else 'watch'


def analyze(code: str, as_of: str) -> dict:
    bars, info = load_bars(code, as_of)
    if not bars:
        return {'code': code, 'as_of': as_of, 'version': VERSION, 'qa': {'blocking': True, 'reason': '查不到這個上市代號（只收上市普通股、特別股與 0050）', 'not_found': True}}
    if len(bars) < 20:
        return {'code': code, 'as_of': as_of, 'version': VERSION, 'qa': {'blocking': True, 'reason': f'有效日線只有 {len(bars)} 根，不足 20 根'}}
    x = indicators(bars)
    is_etf = info['kind'] == 'etf'
    days = [d for d in chips.available_days() if d <= x['date']]
    chg = info['chg']
    inst_cost = _inst_cost(code, bars, days) if days else None

    strategies = trend_and_volume(x, chg, len(bars))
    chip_results, derived = chip_and_institutional(chips.margin(code, days), chips.institutional(code, days), chg, x['close'], x['avg_vol20'], inst_cost)
    strategies += chip_results
    if not is_etf:
        strategies += fundamentals_unavailable()
    price = structure(bars, x, inst_cost)

    dims = {'T': trend_score(x), 'V': volume_score(x, chg), 'C': chip_score(derived, chg),
            'I': institutional_score(derived, x['avg_vol20']), 'F': None}
    # ETF 沒有基本面，F 不適用；依 handoff §3.5 ETF 模式總分與趨勢為 null（不是資料缺）
    missing = [k for k, v in dims.items() if v is None and not (is_etf and k == 'F')]

    avg = max(1, x['avg_vol20'] or 1)
    flags = []
    if rf1(x):
        flags.append({'id': 'RF1', 'text': '收盤在 MA20 之下，且也在 MA5 或 MA60 之下'})
    if 'foreign_d5' in derived:
        f5 = derived['foreign_d5'] / (5 * avg)
        if (f5 < -0.05 and derived['foreign_today'] < 0) or f5 < -0.12:
            flags.append({'id': 'RF2', 'text': '外資近五日賣超偏大'})
    if dims['C'] is not None and dims['C'] <= 35:
        flags.append({'id': 'RF3', 'text': '融資籌碼分數偏低'})

    score = None
    if not missing and not is_etf:
        base = sum(PRESET[k] * dims[k] for k in 'TCVIF')
        resonance = PRESET['res'] if dims['T'] >= 75 and dims['I'] >= 75 and chg >= 1 and (x['vol_ratio'] or 0) >= 1.3 else 0
        overheat = (PRESET['oh_rsi'] if (x['rsi14'] or 0) >= 70 else 0) + (PRESET['oh_margin'] if chg > 0 and derived.get('m5', 0) >= 20 and derived.get('margin_diff5', 0) > 1000 else 0)
        if price['range_position'] is not None and price['range_position'] >= 0.75 and derived.get('trust_today', 0) < 0 and dims['C'] > 35:
            overheat += PRESET['oh_hot']
        score = int(clamp(0, 100, round(base + resonance - overheat - PRESET['risk'] * len(flags))))

    counts = {s: sum(1 for r in strategies if r['status'] == s) for s in STATUS_LABEL}
    p1 = price['pressure']['first']
    s1 = price['support']['first']
    params_hash = hashlib.sha256(json.dumps(PRESET, sort_keys=True).encode()).hexdigest()[:8]
    return {
        'code': code, 'name': info['name'], 'kind': info['kind'], 'as_of': x['date'], 'requested_as_of': as_of,
        'version': VERSION, 'formula': FORMULA, 'params_hash': params_hash,
        'quote': {'close': x['close'], 'chg_pct': round(chg, 2), 'volume': x['volume'], 'change_sign': info['change_sign']},
        'qa': {'blocking': False, 'bars': len(bars), 'chip_days': len(days),
               'chip_range': f'{days[0]}～{days[-1]}' if days else None},
        'dimensions': dims, 'dimensions_missing': missing,
        'score': score, 'score_complete': not missing and not is_etf,
        'score_note': 'ETF 不適用五維總分' if is_etf else ('五維未到齊：' + '、'.join(missing) + ' 尚無資料' if missing else None),
        'trend': trend_class(score, len(flags), x['rsi14']) if score is not None else None,
        'risk_flags': flags,
        'strategies': [{**r, 'status_label': STATUS_LABEL[r['status']]} for r in strategies],
        'strategy_counts': counts,
        'levels': {
            'support1': s1['tick'] if s1 else None,
            'support2': price['support']['second']['tick'] if price['support']['second'] else None,
            'pressure1': p1['tick'] if p1 else price['pressure']['limit_up_ref'],
            'pressure2': price['pressure']['second']['tick'] if price['pressure']['second'] else None,
            'defense': price['defense'], 'sweet': price['sweet'], 'tolerance_pct': price['tolerance_pct'],
            'evidence_levels': price['evidence_levels'],
        },
        'bars': [{'date': b.date, 'open': b.open, 'high': b.high, 'low': b.low, 'close': b.close, 'volume': b.volume} for b in bars[-120:]],
        'labels': {'own_formula': '本站自有計分，非漲停寶寶原公式', 'not_advice': '分析數字不是買賣指令'},
    }
