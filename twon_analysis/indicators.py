"""技術指標（handoff §2）。RSI、ATR 是簡單窗口平均，不是 Wilder；MACD 柱不乘 2。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: int


def ma(closes: list[float], n: int) -> float | None:
    return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None


def rsi14(closes: list[float]) -> float | None:
    if len(closes) < 15:
        return None
    moves = [b - a for a, b in zip(closes[-15:], closes[-14:])]
    up = sum(m for m in moves if m > 0) / 14
    down = -sum(m for m in moves if m < 0) / 14
    return 50.0 if up + down == 0 else round(100 * up / (up + down), 1)


def atr14(bars: list[Bar]) -> float | None:
    if len(bars) < 15:
        return None
    ranges = [max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close)) for a, b in zip(bars[-15:], bars[-14:])]
    return round(sum(ranges) / 14, 2)


def kd9(bars: list[Bar]) -> list[tuple[float, float]]:
    k = d = 50.0
    out = []
    for i in range(len(bars)):
        if i < 8:
            continue
        window = bars[i - 8:i + 1]
        high, low = max(b.high for b in window), min(b.low for b in window)
        rsv = 50.0 if high == low else (bars[i].close - low) / (high - low) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        out.append((round(k, 2), round(d, 2)))
    return out


def macd(closes: list[float], fast=12, slow=26, signal=9) -> list[tuple[float, float, float]]:
    out, ema_fast, ema_slow, dea = [], None, None, None
    for close in closes:
        ema_fast = close if ema_fast is None else ema_fast + (close - ema_fast) * 2 / (fast + 1)
        ema_slow = close if ema_slow is None else ema_slow + (close - ema_slow) * 2 / (slow + 1)
        dif = ema_fast - ema_slow
        dea = dif if dea is None else dea + (dif - dea) * 2 / (signal + 1)
        out.append((round(dif, 3), round(dea, 3), round(dif - dea, 3)))
    return out


def indicators(bars: list[Bar]) -> dict:
    """最後一根（as_of）的指標快照。bars 由舊到新、只含有效交易日（停牌日不入列）。"""
    closes = [b.close for b in bars]
    last = bars[-1]
    vols = [b.volume for b in bars[-20:]]
    avg_vol20 = round(sum(vols) / 20) if len(vols) >= 20 else None
    ma20_series = [ma(closes[:i + 1], 20) for i in range(len(closes) - 6, len(closes))] if len(closes) >= 25 else []
    kd = kd9(bars)
    m = macd(closes)
    prior = bars[-21:-1]
    return {
        'date': last.date, 'close': last.close, 'open': last.open, 'high': last.high, 'low': last.low, 'volume': last.volume,
        'prev_close': bars[-2].close if len(bars) >= 2 else None,
        **{f'ma{n}': ma(closes, n) for n in (5, 10, 20, 60, 120, 240)},
        'rsi14': rsi14(closes), 'atr14': atr14(bars),
        'atr_pct': round(atr14(bars) / last.close * 100, 2) if atr14(bars) else None,
        'k': kd[-1][0] if kd else None, 'd': kd[-1][1] if kd else None,
        'prev_k': kd[-2][0] if len(kd) >= 2 else None, 'prev_d': kd[-2][1] if len(kd) >= 2 else None,
        'dif': m[-1][0], 'dea': m[-1][1], 'hist': m[-1][2],
        'prev_hist': m[-2][2] if len(m) >= 2 else None, 'prev2_hist': m[-3][2] if len(m) >= 3 else None,
        'avg_vol20': avg_vol20, 'vol_ratio': round(last.volume / max(1, avg_vol20), 2) if avg_vol20 else None,
        'high20_close': max(closes[-20:]) if len(closes) >= 20 else None,
        'low20_close': min(closes[-20:]) if len(closes) >= 20 else None,
        'prior_high20': max(b.high for b in prior) if len(prior) >= 5 else None,
        'low20_intraday': min(b.low for b in bars[-20:]) if len(bars) >= 20 else None,
        'ma20_slope5': round((ma20_series[-1] - ma20_series[0]) / ma20_series[0] * 100, 2) if len(ma20_series) == 6 and ma20_series[0] else None,
    }
