"""個股頁五塊新功能的資料（本人 2026-11-16 共識；規格 .scratch/stock-signals/spec.md）。

每日更新時替每一檔算好，頁面只顯示、不做任何計算。算不出來或天生沒有的就少那一塊；
blocks 直接告訴頁面有哪幾塊，failed 記下哪幾塊是算的時候出錯（建置報告會統計）。
和事實資料（facts）並列、互不依賴；給誰看由網站入口的開關決定。
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from .indicators import Bar, ma

# 目前會算的幾塊（固定順序）；blocks 只列其中真的有的。建置報告用同一份名單統計「今天有幾檔有哪幾塊」。
BLOCK_NAMES = ('versus_market', 'ma_alignment', 'heavy_zones')
VERSUS_DAYS = (5, 20, 60)
# 均線排列的四個條件（固定順序）與畫面上四盞燈的短標籤。這一組名字只屬於均線排列這一塊。
MA_ALIGNMENT_CONDITIONS = (('close_above_ma20', '站上月線'), ('ma20_above_ma60', '月＞季'),
                           ('ma60_above_ma120', '季＞半年'), ('ma20_rising', '月線上彎'))
MA_ALIGNMENT_MIN_BARS = 125
MA20_RISING_LOOKBACK = 5
# 爆大量成交區。視窗 240 加均量 60 剛好是 data.load_bars 預設讀的 300 根：日線夠長的股票，240 天每一天都判斷得到。
HEAVY_WINDOW = 240       # 看最近幾個交易日
HEAVY_BASE_DAYS = 60     # 和之前幾個交易日的均量比
HEAVY_MULTIPLE = 3       # 達到均量的幾倍算爆量日


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


def _ma_alignment(bars: list[Bar], indicators: dict) -> dict | None:
    """均線排列：四個條件各自成不成立（都是嚴格大於）與一句結論。均線就是個股分析結果的指標（已還原、取到小數
    兩位，和畫面上均線表是同一套數字；兩條線取位後相同就不算成立），5 個交易日前的 20 日線用同一個算法往回算。
    日線不足 125 根時整塊不給。"""
    if len(bars) < MA_ALIGNMENT_MIN_BARS:
        return None
    closes = [bar.close for bar in bars]
    ma20, ma60, ma120 = indicators['ma20'], indicators['ma60'], indicators['ma120']
    met = (closes[-1] > ma20, ma20 > ma60, ma60 > ma120, ma20 > ma(closes[:-MA20_RISING_LOOKBACK], 20))
    count = sum(met)
    summary = '多頭排列' if count == len(met) else '空頭排列' if count == 0 else f'{len(met)} 項成立 {count} 項'
    return {'conditions': [{'key': key, 'label': label, 'met': hit} for (key, label), hit in zip(MA_ALIGNMENT_CONDITIONS, met)],
            'met_count': count, 'summary': summary}


def _whole_as_int(value: float) -> float | int:
    """小圖用的數字：剛好是整數的寫成整數（100.0 → 100），少佔一點資料量。"""
    return int(value) if value == int(value) else value


def _heavy_zones(bars: list[Bar]) -> dict | None:
    """爆大量成交區：最近 240 根裡，成交量 ≥ 之前 60 根均量 3 倍的日子是爆量日（之前不足 60 根的不判斷）；
    每個爆量日的最低到最高價是一段，由低到高排，低價落在前一段高價以內的併成一區。bars 是還原後的日線
    （價格和 K 線圖一致，成交量也已換算，分割不會被當成爆量）。沒有任何一區時整塊不給。"""
    start = max(0, len(bars) - HEAVY_WINDOW)
    heavy = []
    for index in range(max(start, HEAVY_BASE_DAYS), len(bars)):
        base = sum(bar.volume for bar in bars[index - HEAVY_BASE_DAYS:index]) / HEAVY_BASE_DAYS
        if base > 0 and bars[index].volume >= HEAVY_MULTIPLE * base:
            heavy.append(index)
    if not heavy:
        return None
    merged: list[dict] = []
    for index in sorted(heavy, key=lambda i: (bars[i].low, bars[i].high)):
        low, high, day = bars[index].low, bars[index].high, bars[index].date
        if merged and low <= merged[-1]['high']:
            merged[-1]['high'] = max(merged[-1]['high'], high)
            merged[-1]['days'].append(day)
        else:
            merged.append({'low': low, 'high': high, 'days': [day]})
    close = bars[-1].close
    zones = []
    for zone in reversed(merged):   # 由高到低，和畫面上的清單同一個順序
        if zone['low'] > close:
            position, edge = 'above', zone['low']
        elif zone['high'] < close:
            position, edge = 'below', zone['high']
        else:
            position, edge = 'inside', close
        zones.append({'low': zone['low'], 'high': zone['high'], 'position': position,
                      'distance_pct': _round((edge / close - 1) * 100, 1), 'days': sorted(zone['days'])})
    window = bars[start:]
    return {'close': close, 'zones': zones,
            'chart': {'closes': [_whole_as_int(bar.close) for bar in window], 'volumes': [int(_round(bar.volume / 1000, 0)) for bar in window],
                      'heavy': [index - start for index in heavy]}}


def build_signals(*, bars: list[Bar], day_returns: list[float], index_closes: dict[str, float], indicators: dict) -> dict:
    """bars：這一檔由舊到新的還原後日線，最後一根是資料日；day_returns：和 bars 逐根對齊的當日漲跌幅（比率）；
    index_closes：{日期: 加權指數收盤}；indicators：個股分析結果的指標快照。"""
    dates = [bar.date for bar in bars]
    builders = {'versus_market': lambda: _versus_market(dates, day_returns, index_closes),
                'ma_alignment': lambda: _ma_alignment(bars, indicators),
                'heavy_zones': lambda: _heavy_zones(bars)}
    found, failed = {}, []
    for name in BLOCK_NAMES:
        # 各算各的：某一塊出錯只少那一塊，名字記進 failed；其他幾塊照常。
        try:
            found[name] = builders[name]()
        except Exception:  # noqa: BLE001
            found[name] = None
            failed.append(name)
    return {'data_date': dates[-1], **found, 'blocks': [name for name, block in found.items() if block], 'failed': failed}
