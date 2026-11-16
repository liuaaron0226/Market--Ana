"""個股頁五塊新功能的資料（本人 2026-11-16 共識；規格 .scratch/stock-signals/spec.md）。

每日更新時替每一檔算好，頁面只顯示、不做任何計算。算不出來或天生沒有的就少那一塊；
blocks 直接告訴頁面有哪幾塊。和事實資料（facts）並列、互不依賴；給誰看由網站入口的開關決定。
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

VERSUS_DAYS = (5, 20, 60)


def _round(value: float, places: int) -> float:
    """四捨五入（不是銀行家捨入），和事實區塊同一種捨入；加 0.0 讓很小的負數捨入後是平常的 0。"""
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)) + 0.0


def _versus_market(dates: list[str], day_returns: list[float], index_closes: dict[str, float]) -> dict | None:
    """和大盤比：這一檔最近 N 根的每日漲跌幅連乘，減加權指數同一段期間（這一檔 N 根之前那一天收盤到資料日收盤）
    的漲跌幅；差距單位是百分點。中間停牌過的股票，兩邊比的仍是同一段日期。
    加權指數沒有對到資料日就整塊不給；個股不足 N＋1 根或指數缺起點那一天，就少那一列。"""
    index_close = index_closes.get(dates[-1])
    if not index_close:
        return None
    rows = []
    for n in VERSUS_DAYS:
        index_base = index_closes.get(dates[-1 - n]) if len(dates) >= n + 1 else None
        if not index_base:
            continue
        stock_pct = (math.prod(1 + r for r in day_returns[-n:]) - 1) * 100
        market_pct = (index_close / index_base - 1) * 100
        gap = _round(stock_pct - market_pct, 1)
        tone, label = ('up', '強於大盤') if gap > 0 else ('down', '落後大盤') if gap < 0 else (None, None)
        rows.append({'days': n, 'stock_pct': _round(stock_pct, 2), 'market_pct': _round(market_pct, 2), 'gap': gap,
                     'tone': tone, 'label': label})
    return {'rows': rows} if rows else None


def build_signals(*, dates: list[str], day_returns: list[float], index_closes: dict[str, float]) -> dict:
    """dates、day_returns：這一檔由舊到新的交易日與當日漲跌幅（比率，不比價的日子是 0），最後一筆是資料日；
    index_closes：{日期: 加權指數收盤}。"""
    versus = _versus_market(dates, day_returns, index_closes)
    blocks = [name for name, block in (('versus_market', versus),) if block]
    return {'data_date': dates[-1], 'versus_market': versus, 'blocks': blocks}
