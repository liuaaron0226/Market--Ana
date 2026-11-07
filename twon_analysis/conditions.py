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
    'C01': ('融資籌碼健康', 'chip', 'swing', 'bullish', 'modifier'),
    'C02': ('融資追價過熱', 'chip', 'short', 'bearish', 'modifier'),
    'C03': ('融券回補潛力', 'chip', 'short', 'bullish', 'modifier'),
    'C04': ('借券賣壓累積', 'chip', 'swing', 'bearish', 'modifier'),
    'I01': ('法人買超強度', 'institutional', 'swing', 'bullish', 'modifier'),
    'I02': ('法人連續買賣', 'institutional', 'swing', 'neutral', 'modifier'),
    'I03': ('法人買賣轉向', 'institutional', 'short', 'neutral', 'modifier'),
    'I04': ('法人成本結構', 'institutional', 'swing', 'bullish', 'both'),
    'F01': ('營收成長動能', 'fundamental', 'mid', 'bullish', 'none'),
    'F02': ('每股盈餘能力', 'fundamental', 'mid', 'bullish', 'none'),
    'F03': ('估值結構', 'fundamental', 'mid', 'neutral', 'none'),
    'F04': ('營收衰退風險', 'fundamental', 'mid', 'bearish', 'none'),
}


def streak(values: list) -> int:
    """由最新往回數連續同號天數；遇 None、0 或變號停止。正數＝連買、負數＝連賣。"""
    count, sign = 0, 0
    for value in reversed(values):
        if not value:
            break
        s = 1 if value > 0 else -1
        if sign and s != sign:
            break
        sign, count = s, count + 1
    return sign * count


def chip_and_institutional(margin_rows: list, inst_rows: list, chg: float, close: float, avg_vol: int | None,
                           inst_cost: float | None, borrow_rows: list | None = None) -> tuple[list[dict], dict]:
    """margin_rows／inst_rows：由舊到新、最後一筆是 as_of。缺資料的日子是 None。回傳 (8 招, 衍生數值)。"""
    out, derived = [], {}
    avg = max(1, avg_vol or 1)

    m = [r for r in margin_rows if r is not None]
    if not m or margin_rows[-1] is None:
        for sid in ('C01', 'C02', 'C03'):
            out.append(_result(sid, 'unavailable', {'reason': '融資融券原件只涵蓋 2026-10-07～11-06'}))
    else:
        today = m[-1]
        base = m[max(0, len(m) - 6)]
        margin_diff = today['margin'] - base['margin'] if len(m) > 1 else today['margin'] - today['margin_prev']
        day_change = today['margin'] - today['margin_prev']
        m5 = margin_diff / max(100, base['margin']) * 100
        derived.update(margin_diff5=margin_diff, m5=round(m5, 2), short=today['short'], margin=today['margin'])
        out.append(_result('C01', 'triggered' if chg >= 1.5 and day_change < 0 and margin_diff < 0 else
                           'confirming' if chg > 0 and margin_diff < 0 else 'off',
                           {'chg': round(chg, 2), 'margin_change_today': day_change, 'margin_diff5': margin_diff}))
        out.append(_result('C02', 'triggered' if chg > 0 and m5 >= 20 and margin_diff > 1000 else
                           'confirming' if m5 >= 10 and margin_diff > 500 else 'off',
                           {'m5_pct': round(m5, 2), 'margin_diff5': margin_diff}))
        out.append(_result('C03', 'triggered' if today['short'] > 0 else 'off', {'short_balance': today['short']}))
    b = [r for r in (borrow_rows or []) if r is not None]
    if not b or not borrow_rows or borrow_rows[-1] is None:
        out.append(_result('C04', 'unavailable', {'reason': '借券原件不涵蓋這一天'}))
    else:
        today_b = b[-1]
        base_b = b[max(0, len(b) - 6)]
        borrow_diff5 = today_b['balance'] - base_b['balance'] if len(b) > 1 else None
        derived.update(borrow_sold=today_b['sold'], borrow_balance=today_b['balance'], borrow_diff5=borrow_diff5 or 0)
        heavy = today_b['balance'] / avg >= 0.10 or (borrow_diff5 or 0) > 0
        out.append(_result('C04', 'triggered' if today_b['sold'] > 0 and avg_vol and heavy else 'off',
                           {'borrow_sold': today_b['sold'], 'borrow_balance': today_b['balance'], 'borrow_diff5': borrow_diff5}))

    rows = [r for r in inst_rows if r is not None]
    if not rows or inst_rows[-1] is None:
        for sid in ('I01', 'I02', 'I03', 'I04'):
            out.append(_result(sid, 'unavailable', {'reason': '三大法人原件只涵蓋 2026-10-07～11-06'}))
        return out, derived
    today = rows[-1]
    d5 = sum(r['total'] for r in rows[-5:])
    d10 = sum(r['total'] for r in rows[-10:])
    d20 = sum(r['total'] for r in rows[-20:])
    derived.update(inst_today=today['total'], inst_d5=d5, inst_d20=d20, inst_days=len(rows),
                   foreign_today=today['foreign'], foreign_d5=sum(r['foreign'] for r in rows[-5:]),
                   trust_today=today['trust'], trust_streak=streak([r['trust'] for r in rows]))
    out.append(_result('I01', 'triggered' if any(v > 0 for v in (today['total'], today['foreign'], today['trust'], d5, d20)) else 'off',
                       {'today': today['total'], 'foreign': today['foreign'], 'trust': today['trust'], 'd5': d5, 'd20': d20, 'days': len(rows)}))
    streaks = {k: streak([r[k] for r in rows]) for k in ('foreign', 'trust', 'dealer')}
    out.append(_result('I02', 'triggered' if any(abs(v) >= 1 for v in streaks.values()) else 'off', streaks))
    turned = []
    for key in ('foreign', 'trust'):
        prev5 = sum(r[key] for r in rows[-6:-1])
        if today[key] and prev5 and (today[key] > 0) != (prev5 > 0) and abs(today[key]) >= avg * 0.01:
            turned.append(key)
    out.append(_result('I03', 'triggered' if turned else 'off', {'turned': turned}))
    cost_ok = True if inst_cost is None else close >= inst_cost * 0.97
    out.append(_result('I04', 'triggered' if (d10 > 0 or d20 > 0) and cost_ok else 'off',
                       {'d10': d10, 'd20': d20, 'inst_cost': inst_cost, 'close': close}))
    return out, derived


def fundamentals(rev: dict | None, eps: dict | None, val: dict | None) -> list[dict]:
    """F01–F04（handoff §3.5）。月營收與 EPS 是 OpenAPI 最新一期，不一定等於分析日；證據附資料期別。"""
    out = []
    if rev is None or rev['yoy'] is None:
        out.append(_result('F01', 'unavailable', {'reason': '沒有這家公司的最新月營收'}))
        out.append(_result('F04', 'unavailable', {'reason': '沒有這家公司的最新月營收'}))
    else:
        out.append(_result('F01', 'triggered' if rev['yoy'] > 0 or (rev['cum_yoy'] or 0) > 0 else 'off',
                           {'yoy': rev['yoy'], 'cum_yoy': rev['cum_yoy'], 'period': rev['period']}))
    if eps is None:
        out.append(_result('F02', 'unavailable', {'reason': '最新一季 EPS 尚未公告或不在 OpenAPI 名單'}))
    else:
        out.append(_result('F02', 'triggered' if eps['eps'] > 0 else 'off', {'eps': eps['eps'], 'period': eps['period'], 'basis': eps.get('basis'),
                                                                             'estimated_available_at': eps.get('estimated_available_at')}))
    if val is None or (val['per'] is None and val['pbr'] is None):
        out.append(_result('F03', 'unavailable', {'reason': '沒有本益比／淨值比'}))
    else:
        per, pbr = val['per'], val['pbr']
        cheap = (per is not None and per <= 15) or (pbr is not None and pbr <= 1.1)
        rich = (per is not None and per >= 45) or (pbr is not None and pbr >= 10)
        out.append(_result('F03', 'triggered' if cheap or rich else 'off',
                           {'per': per, 'pbr': pbr, 'side': '偏低估' if cheap else '偏高估' if rich else '一般'}))
    if rev is not None and rev['yoy'] is not None:
        out.append(_result('F04', 'triggered' if rev['yoy'] <= -20 else 'off', {'yoy': rev['yoy'], 'period': rev['period']}))
    order = {'F01': 0, 'F02': 1, 'F03': 2, 'F04': 3}
    return sorted(out, key=lambda r: order[r['id']])


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
