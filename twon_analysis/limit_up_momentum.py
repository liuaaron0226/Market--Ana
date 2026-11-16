"""漲停動能：五塊資料的其中一塊（本人 2026-11-16 共識；規格 .scratch/stock-signals/spec.md）。

這一檔今天收盤後的八個條件組合，在 2017～2021 年所有熱門股的每一個交易日裡贏過百分之幾（0～99 分），
加上它所在的級距（1～6）與那個級距在 2022 年以後隔天真的漲停的比例。量的是「最近有多會漲停」，不是買進訊號。

算法是固定的：權重、門檻、八個條件用的固定數字、256 種組合的換算表、六個歷史比例與全部平均合成一個算法版本，
放在 limit_up_momentum_20261116.json 隨程式進版控（怎麼匯出的見 .scratch/stock-signals/limit-up-freeze/）。
這裡只判斷八個條件成不成立再查表，不在執行時重新訓練。
要換算法版本（重新訓練、改門檻、改歷史比例、改條件的算法）先問本人，並換一個新的版本名稱與檔案。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .indicators import Bar

ALGORITHM_FILE = Path(__file__).with_name('limit_up_momentum_20261116.json')
TIE_MARGIN = 1e-9   # 連乘出來的價位比大小時的餘裕（遠小於一個升降單位）


@lru_cache(maxsize=1)
def algorithm() -> dict:
    """凍結的算法版本（整個檔案的內容）。每次建置只讀一次。"""
    return json.loads(ALGORITHM_FILE.read_text(encoding='utf-8'))


def _chained_closes(moves: list[float]) -> list[float]:
    """把每天的漲跌幅連乘成一條收盤線（第一根當 1）。除權息、分割那種不比價的日子漲跌是 0，所以這條線沒有缺口；
    只看線上各點的比例，起點是多少不影響。"""
    line = [1.0]
    for move in moves[1:]:
        line.append(line[-1] * (1 + move))
    return line


def _above(value: float, other: float) -> bool:
    """嚴格大於。兩邊都是連乘出來的價位，留一點餘裕，讓「剛好等於」不會被浮點誤差算成高於。"""
    return value > other * (1 + TIE_MARGIN)


def conditions_met(*, bars: list[Bar], returns: list[float | None], turnovers: list[float | None],
                   trust_nets: dict[str, float | None], index_closes: dict[str, float]) -> tuple[bool, ...] | None:
    """八個條件各自成不成立（固定順序，和算法版本檔的 conditions 同一個順序）。日線不足（至少要 61 根：60 日線的
    60 根，加上算第一根漲跌用的前一根），或加權指數沒有對到資料日或 20 個交易日前那一天（條件 ⑥ 沒得比）時回 None。
    bars、returns、turnovers 都是這一檔由舊到新、逐根對齊，最後一筆是資料日。
    bars：實際成交價的日線（沒有還原）。
    returns：每天相對前一日參考價的漲跌幅（比率，漲跌 ÷（收盤 − 漲跌），和驗證時同一個算式，所以剛好踩在 5%、9.5%
    的日子和驗證時判得一樣）；不比價的日子（除權息、分割等）是 None，當 0 算——也和驗證時相同。
    turnovers：每天的成交金額。
    trust_nets：{日期: 投信買賣超股數}，沒有資料的日子不在裡面（那一天算沒有買超）。
    index_closes：{日期: 加權指數收盤}。"""
    fixed = algorithm()
    lookback = fixed['lookback_days']
    if len(bars) < fixed['min_bars']:
        return None
    index_now, index_base = index_closes.get(bars[-1].date), index_closes.get(bars[-1 - lookback].date)
    if not index_now or not index_base:
        return None
    limit = fixed['limit_up_threshold']
    moves = [r or 0.0 for r in returns]
    money = [value or 0.0 for value in turnovers]
    # 價位都放到「漲跌幅連乘的收盤線」上比（和驗證時相同）：除權息那天不算下跌，也不會被分割、減資的缺口騙到。
    line = _chained_closes(moves)
    short_line = sum(line[-fixed['short_line_days']:]) / fixed['short_line_days']
    long_line = sum(line[-fixed['long_line_days']:]) / fixed['long_line_days']
    prior = range(len(line) - 1 - lookback, len(line) - 1)      # 前 20 個交易日，不含今天
    # 每天的最高價換到線上：最高 ÷ 收盤 × 線上的收盤（沒有最高價的日子用收盤）。
    prior_high = max(line[i] * max((bars[i].high or bars[i].close) / bars[i].close, 1.0) for i in prior)
    limit_up_today = moves[-1] >= limit
    return (limit_up_today,
            moves[-1] >= fixed['up_short_of_limit_threshold'] and not limit_up_today,
            money[-1] >= fixed['turnover_multiple'] * sum(money[i] for i in prior) / lookback,
            _above(line[-1], prior_high),
            _above(line[-1], short_line) and _above(short_line, long_line),
            _above(line[-1] / line[-1 - lookback], index_now / index_base),
            all((trust_nets.get(bar.date) or 0) > 0 for bar in bars[-fixed['trust_streak_days']:]),
            any(moves[i] >= limit for i in prior))


def limit_up_momentum(*, market: str, kind: str, **daily) -> dict | None:
    """漲停動能這一塊：分數、級距、這個級距的歷史比例、全部平均、最高一級的歷史比例（頁面光條的滿格）、算法版本。
    八個條件各自成不成立不給到頁面。
    上市、上櫃普通股都給，同一個算法版本、同一組歷史比例（上櫃：本人 2026-11-16 看過 09 票驗證報告
    .scratch/stock-signals/otc-validation/REPORT.md，定「開，沿用上市的比例」）；ETF 與特別股不給。
    daily 是 conditions_met 的參數。"""
    if market not in ('上市', '上櫃') or kind != 'common':
        return None
    met = conditions_met(**daily)
    if met is None:
        return None
    fixed = algorithm()
    points, band = fixed['table'][''.join('1' if hit else '0' for hit in met)]
    return {'points': points, 'band': band, 'band_rate_pct': fixed['band_rates_pct'][band - 1],
            'average_rate_pct': fixed['average_rate_pct'], 'top_rate_pct': max(fixed['band_rates_pct']),
            'algorithm': fixed['version']}
