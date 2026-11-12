"""以同一份盤後資料重現大盤短／中／長期走勢判讀。

公開 seam 只有 ``assess_market_trends(daily, institutional)``：呼叫者交入由舊到新的日 K
與法人序列，取得可直接呈現的三期一句結論、依據數字與規則版本。KD(9,3,3)、RSI14
及 MACD(12,26,9) 的暖機與缺值語意對齊 ``web/chart-studies.js``／``web/kchart.js``。
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

SCHEMA = "market-trend/v1"
RULE_VERSION = "market-trend-v1"
KD_READY_BARS = 49
RSI_PERIOD = 14


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def sma(values: Sequence[float | None], period: int) -> list[float | None]:
    out: list[float | None] = []
    window: list[float] = []
    for value in values:
        if value is None:
            window = []
            out.append(None)
            continue
        window.append(value)
        if len(window) > period:
            window.pop(0)
        out.append(sum(window) / period if len(window) == period else None)
    return out


def macd_series(values: Sequence[float | None]) -> list[dict[str, float | bool | None]]:
    fast = slow = signal = None
    count = 0
    out = []
    for close in values:
        if close is None:
            fast = slow = signal = None
            count = 0
            out.append({"dif": None, "dea": None, "hist": None, "ready": False})
            continue
        fast = close if fast is None else fast + (close - fast) * 2 / 13
        slow = close if slow is None else slow + (close - slow) * 2 / 27
        dif = fast - slow
        signal = dif if signal is None else signal + (dif - signal) * 2 / 10
        count += 1
        out.append({"dif": dif, "dea": signal, "hist": dif - signal, "ready": count >= 34})
    return out


def kd_series(candles: Sequence[Mapping[str, Any]]) -> list[dict[str, float | bool | None]]:
    k = d = 50.0
    count = 0
    valid_window: list[tuple[float, float, float]] = []
    out = []
    for candle in candles:
        high, low, close = (_number(candle.get(key)) for key in ("high", "low", "close"))
        if high is None or low is None or close is None:
            k = d = 50.0
            count = 0
            valid_window = []
            out.append({"k": None, "d": None, "ready": False})
            continue
        count += 1
        valid_window.append((high, low, close))
        if len(valid_window) > 9:
            valid_window.pop(0)
        if count < 9:
            out.append({"k": None, "d": None, "ready": False})
            continue
        highest = max(row[0] for row in valid_window)
        lowest = min(row[1] for row in valid_window)
        rsv = 50.0 if highest == lowest else (close - lowest) / (highest - lowest) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        out.append({"k": k, "d": d, "ready": count >= KD_READY_BARS})
    return out


def rsi_series(values: Sequence[float | None]) -> list[dict[str, float | bool | None]]:
    count = 0
    out = []
    valid: list[float] = []
    for close in values:
        if close is None:
            count = 0
            valid = []
            out.append({"rsi": None, "ready": False})
            continue
        count += 1
        valid.append(close)
        if count <= RSI_PERIOD:
            out.append({"rsi": None, "ready": False})
            continue
        moves = [valid[index] - valid[index - 1] for index in range(len(valid) - RSI_PERIOD, len(valid))]
        up = sum(move for move in moves if move > 0)
        down = -sum(move for move in moves if move < 0)
        out.append({"rsi": None if up + down == 0 else 100 * up / (up + down), "ready": True})
    return out


def _signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    sign = "+" if value > 0 else "−" if value < 0 else ""
    return f"{sign}{abs(value):,.{digits}f}"


def _fmt(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _state(score: int) -> tuple[str, str]:
    if score >= 2:
        return "bullish", "偏多"
    if score <= -2:
        return "bearish", "偏空"
    return "neutral", "盤整"


def _side(left: float | None, right: float | None) -> int:
    if left is None or right is None or math.isclose(left, right, rel_tol=0, abs_tol=1e-9):
        return 0
    return 1 if left > right else -1


def _institutional_sum(rows: Sequence[Mapping[str, Any]], count: int) -> float | None:
    values = []
    for row in rows:
        foreign = _number((row.get("foreign") or {}).get("net"))
        trust = _number((row.get("trust") or {}).get("net"))
        if foreign is not None or trust is not None:
            values.append((foreign or 0.0) + (trust or 0.0))
    if len(values) < count:
        return None
    return sum(values[-count:]) / 100_000_000


def _return(values: Sequence[float | None], bars: int) -> float | None:
    if len(values) <= bars or values[-1] is None or values[-bars - 1] in (None, 0):
        return None
    return (values[-1] / values[-bars - 1] - 1) * 100


def _insufficient(key: str, label: str, data_date: str | None, need: int, have: int) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "state": "insufficient",
        "conclusion": "資料不足",
        "sentence": f"資料不足：需要至少 {need} 根完整日 K，目前只有 {have} 根。",
        "metrics": {},
        "data_date": data_date,
    }


def assess_market_trends(daily: Sequence[Mapping[str, Any]],
                         institutional: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = list(daily or [])
    dates = [str(row.get("date") or "") for row in rows]
    if any(not value for value in dates) or dates != sorted(dates) or len(set(dates)) != len(dates):
        raise ValueError("daily 必須依日期遞增且日期不可重複")
    data_date = dates[-1] if dates else None
    institutions = [row for row in institutional or () if str(row.get("date") or "") <= (data_date or "")]
    institution_dates = [str(row.get("date") or "") for row in institutions]
    if institution_dates != sorted(institution_dates) or len(set(institution_dates)) != len(institution_dates):
        raise ValueError("institutional 必須依日期遞增且日期不可重複")
    closes = [_number(row.get("close")) for row in rows]
    amounts = [_number(row.get("amount")) for row in rows]
    complete = sum(value is not None for value in closes)
    ma = {period: sma(closes, period) for period in (5, 10, 20, 60, 120, 240)}
    macd = macd_series(closes)
    kd = kd_series(rows)
    rsi = rsi_series(closes)
    latest_close = closes[-1] if closes else None

    periods: list[dict[str, Any]] = []
    if len(rows) < KD_READY_BARS or latest_close is None or not macd or not macd[-1]["ready"]:
        periods.append(_insufficient("short", "短期", data_date, KD_READY_BARS, complete))
    else:
        ma5, ma10 = ma[5][-1], ma[10][-1]
        hist = _number(macd[-1]["hist"])
        k, d = _number(kd[-1]["k"]), _number(kd[-1]["d"])
        rsi14 = _number(rsi[-1]["rsi"])
        amount_window = [value for value in amounts[-20:] if value is not None]
        volume_ratio = (amounts[-1] / (sum(amount_window) / 20)
                        if len(amount_window) == 20 and amounts[-1] is not None and sum(amount_window) > 0 else None)
        inst5 = _institutional_sum(institutions, 5)
        score = sum((_side(latest_close, ma5), _side(ma5, ma10), _side(hist, 0), _side(k, d), _side(rsi14, 50)))
        state, conclusion = _state(score)
        rsi_text = f"RSI {_fmt(rsi14)}" if rsi14 is not None else "RSI 無方向"
        sentence = (f"{conclusion}：收盤 {_fmt(latest_close, 2)} {'高於' if _side(latest_close, ma5) > 0 else '低於' if _side(latest_close, ma5) < 0 else '等於'} "
                    f"MA5 {_fmt(ma5, 2)}，MACD 柱 {_signed(hist, 2)}，KD {_fmt(k)}／{_fmt(d)}，{rsi_text}；"
                    f"量比 {_fmt(volume_ratio, 2)} 倍，5 日法人 {_signed(inst5)} 億。")
        periods.append({"key": "short", "label": "短期", "state": state, "conclusion": conclusion,
                        "sentence": sentence, "data_date": data_date,
                        "metrics": {"close": latest_close, "ma5": ma5, "ma10": ma10, "macd_hist": hist,
                                    "kd_k": k, "kd_d": d, "rsi14": rsi14, "volume_ratio_20d": volume_ratio,
                                    "institutional_5d_billion": inst5}})

    if len(rows) < 60 or ma[20][-1] is None or ma[60][-1] is None:
        periods.append(_insufficient("medium", "中期", data_date, 60, complete))
    else:
        ma20, ma60 = ma[20][-1], ma[60][-1]
        ret20 = _return(closes, 20)
        inst20 = _institutional_sum(institutions, 20)
        score = sum((_side(latest_close, ma20), _side(ma20, ma60), _side(ret20, 0), _side(inst20, 0)))
        state, conclusion = _state(score)
        sentence = (f"{conclusion}：收盤 {_fmt(latest_close, 2)} {'高於' if _side(latest_close, ma20) > 0 else '低於' if _side(latest_close, ma20) < 0 else '等於'} "
                    f"MA20 {_fmt(ma20, 2)}，MA20 {'高於' if _side(ma20, ma60) > 0 else '低於' if _side(ma20, ma60) < 0 else '等於'} "
                    f"MA60 {_fmt(ma60, 2)}，近 20 日 {_signed(ret20)}%；20 日法人 {_signed(inst20)} 億。")
        periods.append({"key": "medium", "label": "中期", "state": state, "conclusion": conclusion,
                        "sentence": sentence, "data_date": data_date,
                        "metrics": {"close": latest_close, "ma20": ma20, "ma60": ma60,
                                    "return_20d_pct": ret20, "institutional_20d_billion": inst20}})

    if len(rows) < 240 or ma[60][-1] is None or ma[120][-1] is None or ma[240][-1] is None:
        periods.append(_insufficient("long", "長期", data_date, 240, complete))
    else:
        ma60, ma120, ma240 = ma[60][-1], ma[120][-1], ma[240][-1]
        ret60 = _return(closes, 60)
        score = sum((_side(latest_close, ma120), _side(ma60, ma120), _side(ma120, ma240), _side(ret60, 0)))
        state, conclusion = _state(score)
        sentence = (f"{conclusion}：收盤 {_fmt(latest_close, 2)} {'高於' if _side(latest_close, ma120) > 0 else '低於' if _side(latest_close, ma120) < 0 else '等於'} "
                    f"MA120 {_fmt(ma120, 2)}，MA60 {_fmt(ma60, 2)}／MA120 {_fmt(ma120, 2)}／MA240 {_fmt(ma240, 2)}，"
                    f"近 60 日 {_signed(ret60)}%。")
        periods.append({"key": "long", "label": "長期", "state": state, "conclusion": conclusion,
                        "sentence": sentence, "data_date": data_date,
                        "metrics": {"close": latest_close, "ma60": ma60, "ma120": ma120,
                                    "ma240": ma240, "return_60d_pct": ret60}})

    return {"schema": SCHEMA, "rule_version": RULE_VERSION, "data_date": data_date, "periods": periods}
