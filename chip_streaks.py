"""籌碼卡「目前前 10＋連續天數」（本人 11-07 定案）。

連續天數：從最新一天往回數，同方向（買超／賣超、增加／減少）連續幾天；遇到反向、0 或缺資料就停。
前 10 依最新一天的數值排序。資料只有 N 個交易日，連續天數等於 N 時標 capped（畫面顯示「N 天以上」）。
"""
from __future__ import annotations


def _streak(by_day: dict, days: list[str], sign: int) -> int:
    n = 0
    for day in reversed(days):
        v = by_day.get(day)
        if v is None or v * sign <= 0:
            break
        n += 1
    return n


def current_top(series: dict[str, dict], days: list[str], top: int = 10) -> tuple[list[dict], list[dict]]:
    """series: 代號 → {日期: 數值}。回傳（最新一天正值前 top，負值前 top），每列帶 value、streak、capped。"""
    latest = days[-1]
    rows = [(code, by_day[latest]) for code, by_day in series.items() if by_day.get(latest)]
    def pack(code, value, sign):
        s = _streak(series[code], days, sign)
        return {'code': code, 'value': value, 'streak': s, 'capped': s == len(days)}
    buy = [pack(c, v, 1) for c, v in sorted((r for r in rows if r[1] > 0), key=lambda r: -r[1])[:top]]
    sell = [pack(c, v, -1) for c, v in sorted((r for r in rows if r[1] < 0), key=lambda r: r[1])[:top]]
    return buy, sell


def rank_streak_top(values: dict[str, dict], days: list[str], top: int = 10, member_top: int = 20) -> list[dict]:
    """成交金額這類沒有方向的數值：最新一天前 top 名，streak＝連續幾天名列當天前 member_top。"""
    members = {}
    for day in days:
        ranked = sorted((c for c, by in values.items() if by.get(day) is not None), key=lambda c: -values[c][day])[:member_top]
        members[day] = set(ranked)
    latest = days[-1]
    best = sorted((c for c, by in values.items() if by.get(latest) is not None), key=lambda c: -values[c][latest])[:top]
    out = []
    for code in best:
        s = 0
        for day in reversed(days):
            if code not in members[day]:
                break
            s += 1
        out.append({'code': code, 'value': values[code][latest], 'streak': s, 'capped': s == len(days)})
    return out
