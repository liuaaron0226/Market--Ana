"""還原價：把股本變動造成的價格斷層接回去，現金股利不動（本人 2026-11-14 定案）。

係數＝官方參考價 ÷ 事件前收盤，乘在事件日「之前」的價格上。事件來自 data/corporate-actions/events.json。
"""
from __future__ import annotations

import functools
import json
from dataclasses import replace
from pathlib import Path

from .indicators import Bar

ROOT = Path(__file__).resolve().parents[1]


# 事件表的「事件前收盤」要和實際前一根收盤相符才套用；容忍停牌前後報價的小誤差。
CLOSE_TOLERANCE = 0.005


class UnresolvedEvent(ValueError):
    """事件該還原，但官方數字不齊：不猜，交給呼叫端回報。"""


def capital_factor(event: dict) -> float | None:
    """事件日之前的價格要乘上的係數；不需要還原（現金股利）時回 None。"""
    if event['type'] == '除息':
        return None
    before, reference = event.get('close_before'), event.get('reference')
    cash = event.get('cash_dividend') if event['type'] == '除權息' else 0.0
    if not before or not reference or cash is None or before <= cash:
        raise UnresolvedEvent(f"{event['code']} {event['date']} {event['type']} 缺參考價或現金股利")
    return reference / (before - cash)


def capital_events(shared: list[dict], supplement: dict) -> dict[str, list[dict]]:
    """每檔代號要還原的事件：共同 events.json 的股本變動，加上補充資料（capital_supplement）。

    純現金股利不列；「除權息」帶上補充資料裡的每股現金股利（沒有就留給 adjust_bars 回報未解）。
    """
    cash = supplement.get('cash_dividends') or {}
    by_code: dict[str, list[dict]] = {}
    for event in shared:
        if event['type'] == '除息':
            continue
        key = f"{event['code']}|{event['date']}"
        if event['type'] == '除權息' and key in cash:
            event = {**event, 'cash_dividend': cash[key]}
        by_code.setdefault(event['code'], []).append(event)
    for event in supplement.get('events') or []:
        by_code.setdefault(event['code'], []).append(event)
    for events in by_code.values():
        events.sort(key=lambda event: event['date'])
    return by_code


@functools.lru_cache(maxsize=4)
def _load_capital_events(events_path: Path, supplement_path: Path, seed_path: Path, stamps: tuple) -> dict[str, list[dict]]:
    import capital_supplement

    shared = json.loads(events_path.read_text(encoding='utf-8')) if events_path.exists() else []
    return capital_events(shared, capital_supplement.load(supplement_path, seed_path))


def default_capital_events(root: Path = ROOT) -> dict[str, list[dict]]:
    """資料根目錄下的還原事件（每檔代號一串）；檔案沒變就用快取，建置時 355 檔只讀一次。"""
    paths = (root / 'data/corporate-actions/events.json', root / 'data/corporate-actions/capital-supplement.json',
             root / 'seeds/capital-supplement.json')
    stamps = tuple(path.stat().st_mtime_ns if path.exists() else None for path in paths)
    return _load_capital_events(*paths, stamps)


def adjust_bars(bars: list[Bar], events: list[dict]) -> tuple[list[Bar], list[dict]]:
    """回傳（還原後的日線，該還原卻沒還原的事件）。bars 舊到新；事件日當天與之後的 K 棒不動。

    沒還原的事件原樣帶回，另加 unresolved_reason；那一段價格維持實際成交價，不猜係數。
    """
    factors, unresolved = price_factors(bars, events)
    return apply_factors(bars, factors), unresolved


def apply_factors(bars: list[Bar], factors: list[float]) -> list[Bar]:
    """價格乘係數、成交量除以係數；係數 1.0 的 K 棒原物件回傳。"""
    return [bar if factor == 1.0 else replace(
        bar, open=round(bar.open * factor, 2), high=round(bar.high * factor, 2), low=round(bar.low * factor, 2),
        close=round(bar.close * factor, 2), volume=round(bar.volume / factor))
        for bar, factor in zip(bars, factors)]


def price_factors(bars: list[Bar], events: list[dict]) -> tuple[list[float], list[dict]]:
    """每根 K 棒的價格係數（1.0＝不動；股數類的量要除以它），以及沒還原的事件。"""
    if not bars:
        return [], []
    steps, unresolved = [], []

    def give_up(event: dict, reason: str) -> None:
        unresolved.append({**event, 'unresolved_reason': reason})

    by_day: dict[str, list[dict]] = {}
    for event in events:
        # 視窗內沒有要還原的 K 棒（事件更早）或事件還沒發生的，都不相干；純現金股利不還原。
        if bars[0].date < event['date'] <= bars[-1].date and event['type'] != '除息':
            by_day.setdefault(event['date'], []).append(event)
    for day, same_day in sorted(by_day.items()):
        distinct: dict[tuple, dict] = {}
        for event in same_day:  # 兩個來源各報一次的同一件事只算一次
            distinct.setdefault((event['type'], event.get('close_before'), event.get('reference'),
                                 event.get('cash_dividend')), event)
        if len(distinct) > 1:
            # 各自的參考價都從同一個前收盤算起，相乘會重複計算；不猜哪一筆才對。
            for event in distinct.values():
                give_up(event, '同一天有多筆不同的股本變動')
            continue
        event = next(iter(distinct.values()))
        try:
            step = capital_factor(event)
        except UnresolvedEvent:
            give_up(event, '缺官方參考價或現金股利')
            continue
        traded = next(bar.close for bar in reversed(bars) if bar.date < day)
        if abs(traded - event['close_before']) > max(0.011, traded * CLOSE_TOLERANCE):
            give_up(event, f"事件前收盤 {event['close_before']} 與實際前一根收盤 {traded} 不符")
            continue
        steps.append((day, step))
    factors = []
    for bar in bars:
        factor = 1.0
        for day, step in steps:
            if bar.date < day:
                factor *= step
        factors.append(factor)
    return factors, unresolved
