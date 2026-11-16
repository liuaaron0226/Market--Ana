"""布林通道的位置描述：收盤在通道的哪一區、通道寬度窄不窄。

和圖上 ``web/kchart.js`` 的 ``bollinger()`` 同算法：20 日均價 ± 2 個母體標準差。區的切法與「窄」的定義照
布林通道研究（``.scratch/bollinger-study/boll.py`` 的 ``bollinger``、``zone_of``、``_squeezed``，已和圖上逐根比對），
產品這裡自己放一份，不匯入研究資料夾。

這只是位置描述：布林通道單獨當買賣規則，在 268 檔、約 10 年的歷史上贏不過買了放著
（``.scratch/bollinger-study/report.html``）。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

PERIOD = 20
DEVIATIONS = 2
# 通道寬度（上緣減下緣，除以中線）落在最近 SQUEEZE_WINDOW 根裡最窄的 SQUEEZE_SHARE，就算「窄」。
SQUEEZE_WINDOW = 120
SQUEEZE_SHARE = 0.2
ZONES = ("下緣以下", "下緣到中線", "中線到上緣", "上緣以上")

Band = tuple[float, float, float]   # （中線, 上緣, 下緣）


def bands(closes: Sequence[float]) -> list[Band | None]:
    """每一根的（中線, 上緣, 下緣）；不足 20 根的位置是 None。"""
    out: list[Band | None] = []
    for index in range(len(closes)):
        if index + 1 < PERIOD:
            out.append(None)
            continue
        window = closes[index - PERIOD + 1:index + 1]
        middle = sum(window) / PERIOD
        width = DEVIATIONS * math.sqrt(sum((value - middle) ** 2 for value in window) / PERIOD)
        out.append((middle, middle + width, middle - width))
    return out


def zone_of(close: float, band: Band) -> str:
    """收盤在通道的哪一區。剛好踩在線上：踩下緣算「下緣到中線」，踩中線或上緣算「中線到上緣」。"""
    middle, upper, lower = band
    if close < lower:
        return ZONES[0]
    if close > upper:
        return ZONES[3]
    return ZONES[1] if close < middle else ZONES[2]


def _width(band: Band | None) -> float | None:
    return None if band is None or band[0] <= 0 else (band[1] - band[2]) / band[0]


def _squeezed(all_bands: list[Band | None]) -> bool | None:
    """最後一根的寬度是否在最近 120 根裡最窄的兩成（比它窄的不到 24 根）；湊不滿 120 根是 None。"""
    if len(all_bands) < PERIOD - 1 + SQUEEZE_WINDOW:
        return None
    widths = [_width(band) for band in all_bands[-SQUEEZE_WINDOW:]]
    if None in widths:
        return None
    return sum(width < widths[-1] for width in widths) < SQUEEZE_WINDOW * SQUEEZE_SHARE


def position(dates: Sequence[str], closes: Sequence[float]) -> dict[str, Any] | None:
    """最後一根收盤在通道裡的位置；不足 20 根是 None。價位取到小數兩位、寬度取到小數四位。"""
    all_bands = bands(closes)
    if not all_bands or all_bands[-1] is None:
        return None
    middle, upper, lower = all_bands[-1]
    width = _width(all_bands[-1])
    return {
        "date": dates[-1], "close": closes[-1],
        "middle": round(middle, 2), "upper": round(upper, 2), "lower": round(lower, 2),
        "zone": zone_of(closes[-1], all_bands[-1]),
        "width_ratio": None if width is None else round(width, 4),
        "squeezed": _squeezed(all_bands),
    }
