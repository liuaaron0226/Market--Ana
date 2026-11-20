"""台股交易日的加減（盯項的到期日、有結果後多留幾天用；.scratch/impact-watch 票 03）。

單日是不是交易日原本各寫各的（market_news.is_trading_day、level_signals.trading_day_status）；這裡補上
「往後數 N 個交易日」與「兩天之間隔幾個交易日」。休市日用證交所官方休市日曆（專案帶種子、工作集有快取）；
颱風假這種臨時休市不在日曆裡，照算交易日。日曆還沒涵蓋的年度（年底往後數跨到隔年）只扣週末。
"""
from __future__ import annotations

from datetime import date, timedelta


def known_holidays(day: str) -> set[str]:
    """這一天所在年度的官方休市日（連同種子與快取裡其他年度的）；讀不到就回空集合（只扣週末）。"""
    try:
        from level_signals import ensure_holiday_calendar
        return ensure_holiday_calendar(day)
    except Exception:  # noqa: BLE001 — 沒有日曆也要能算，不擋排程
        return set()


def is_trading_day(day: str, holidays: set[str]) -> bool:
    return date.fromisoformat(day).weekday() < 5 and day not in holidays


def add_trading_days(day: str, count: int, holidays: set[str] | None = None) -> str:
    """day 之後第 count 個交易日（day 自己不算，也不必是交易日）。count 是 0 就回 day。"""
    if count < 0:
        raise ValueError("count 不能是負的")
    holidays = known_holidays(day) if holidays is None else holidays
    current = date.fromisoformat(day)
    while count:
        current += timedelta(days=1)
        if is_trading_day(current.isoformat(), holidays):
            count -= 1
    return current.isoformat()


def trading_days_between(start: str, end: str, holidays: set[str] | None = None) -> int:
    """start 之後到 end（含）有幾個交易日；end 不比 start 晚就是 0。"""
    holidays = known_holidays(start) if holidays is None else holidays
    current, last, count = date.fromisoformat(start), date.fromisoformat(end), 0
    while current < last:
        current += timedelta(days=1)
        count += is_trading_day(current.isoformat(), holidays)
    return count
