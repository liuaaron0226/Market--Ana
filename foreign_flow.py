"""外資讀數（預測帳本 G 票）：交給 Gemini 的「外資層」，每個讀數連同「歷史上這種情況後來怎樣」由程式算好。

需求：``.scratch/foreign-flow-20261115/FORECAST-INPUTS.md``（本人 2026-11-15 兩次定案）；研究依據同目錄
``FOREIGN-FLOW-LOGIC.md``。研究腳本的數字只當核對參考，這裡重寫。

對外：
- ``spot_readings``：讀數 2（外資連買／連賣第幾天、近 5／20 日累計占成交金額）與讀數 3（台積電與其餘、買賣超檔數）。
- ``trend_reading``：讀數 5（加權指數在年線 200 日、季線 60 日之上或之下）。
- ``futures_reading``：讀數 6（外資台指期淨部位：相對近 60 日平均、近 5 日增減、當日增減；結算日的增減不算）。
- ``settlement_days``：台指期結算日（到期月份第三個星期三，休市就順延到下一個交易日）。
- ``build_layer``：把上面幾個讀數組成一層，附統計期間、版本與交給 Gemini 的說明。
- ``daily_flow_from_sources``：從上市法人資料庫與個股日線算每天的外資金額彙總（股數 × 收盤，單位億元）。

統計都只用基準日（含）以前的資料，不偷看未來。單位：金額億元、期貨口（大台＋小台÷4）、漲跌與比例 %。
"""
from __future__ import annotations

import gzip
import hashlib
import json
import math
import sqlite3
from contextlib import closing
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SCHEMA = "foreign-flow-layer/v1"

# 讀數與統計的參數：整組算進這一層的版本，改了就是新的輸入版本。
PARAMETERS: dict[str, Any] = {
    "spot": {"cum_windows": [5, 20], "streak_min_samples": 20, "tsmc_code": "2330"},
    "trend": {"ma_long": 200, "ma_short": 60},
    # 讀數 1：那斯達克前一個美股交易日漲跌分五區；台股這邊用 0050 的實際開盤價（不是加權指數開盤值）。
    "nasdaq": {"edges_pct": [-1.5, -0.5, 0.5, 1.5], "proxy_code": "0050"},
    # 讀數 4：美元指數、美元兌台幣近 20 個交易日變化，對照外資近 20 日累計。
    "dollar": {"window": 20, "dividend_months": [7, 8, 9]},
    # 淨部位（大台＋小台÷4）在 foreign_futures 算好才進來，這裡只管增減與統計。
    "futures": {"rel_window": 60, "chg_window": 5, "forward": [5, 20],
                "cuts": {"rel_mean": [20, 80], "chg_window": [20, 80], "chg1": [10, 30, 70, 90]}},
}

NOTES = {
    "spot": "外資的買賣解釋當天的漲跌，不代表之後會續漲或續跌：歷史上外資大賣之後二十、六十個交易日的大盤漲跌和平常差不多。"
            "單日金額雜訊大（有一半是短線與避險資金），近五日、二十日累計比較有意義；外資金額裡台積電一檔占四成上下。"
            "「隔天續賣（買）的機率」講的是外資自己明天的方向，不是大盤明天的漲跌：歷史上外資單日大賣的隔天，"
            "大盤平均只再跌約 0.15%。這幾個外資統計也沒有講均線、壓力或反彈，不能把別的判斷說成是外資統計的結論。",
    "futures": "外資台指期淨空單的水準幾乎是投信（槓桿型 ETF）多單的反面，空單多不代表看空，要看的是增減；"
               "不能用「空單很多、空單壓力未解」當理由。"
               "這個讀數的歷史統計只有近三年、全在多頭期間，加空之後是漲得比較少而不是下跌，可信度比其他外資讀數低。",
    "nasdaq": "統計表是「這種那斯達克收盤之後的那一個台股交易日」；晚間版看的是前一晚美股，對應的就是基準日當天"
              "（已經收盤），要預測的下一個交易日還要看今晚美股。開盤跳空與盤中漲跌是用 0050 的實際開盤價算的"
              "（加權指數的開盤值只反映約一半跳空，會算出不存在的盤中續跌）；照附上的平均讀，不要自己推論大跌隔天盤中會續跌。",
    "dollar": "美元走強、台幣走貶時外資通常在賣，是同時發生的確認，不是預告；除息季（約七到九月）股匯常不同調，"
              "這段期間外資數字要打折看。",
    "trend": "加權指數在年線、季線之上或之下，決定一段下跌比較像回檔還是轉弱；外資賣多少不能代替這個判斷。",
}


# ---- 小工具 ----

def _pct(value: float) -> float:
    return round(value * 100, 2)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _nearest_rank(sorted_values: Sequence[float], percentile: float) -> float:
    """最近秩分位數：第 ceil(p/100 × n) 小的值（手算得出來）。"""
    rank = max(1, math.ceil(percentile / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def _percentile_rank(history: Sequence[float], value: float) -> int:
    """歷史裡小於或等於這個值的比例（%，四捨五入到整數）。"""
    return round(100 * sum(1 for item in history if item <= value) / len(history))


def _bucket_label(cuts: Sequence[int], index: int) -> str:
    if index == 0:
        return f"最低 {cuts[0]}%"
    if index == len(cuts):
        return f"最高 {100 - cuts[-1]}%"
    return f"{cuts[index - 1]}～{cuts[index]}% 之間"


def _bucket_of(thresholds: Sequence[float], value: float) -> int:
    for index, threshold in enumerate(thresholds):
        if value <= threshold:
            return index
    return len(thresholds)


def _closes(index_daily: Iterable[Mapping[str, Any]], base_date: str) -> list[tuple[str, float]]:
    return [(str(row["date"]), float(row["close"])) for row in index_daily
            if row.get("close") is not None and str(row["date"]) <= base_date]


def _series(rows: Iterable[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """[{date, close}] → 依日期排好的 (日期, 收盤)，收盤空白的略過。"""
    return sorted((str(row["date"]), float(row["close"])) for row in rows if row.get("close") is not None)


def _cumulative_share(foreign: Sequence[float], turnover: Sequence[float], end: int, window: int) -> float | None:
    """到第 end 天為止 window 天的外資累計淨額 ÷ 同期成交金額（讀數 2 與讀數 4 共用這一套）。"""
    start = end - window + 1
    if start < 0:
        return None
    amount = sum(turnover[start:end + 1])
    return sum(foreign[start:end + 1]) / amount if amount else None


def _forward(closes: Sequence[float], position: int, horizon: int) -> float | None:
    end = position + horizon
    return None if end >= len(closes) else closes[end] / closes[position] - 1


def _period(dates: Sequence[str]) -> dict[str, Any]:
    return {"from": dates[0], "to": dates[-1], "days": len(dates)} if dates else {"from": None, "to": None, "days": 0}


def _missing(reason: str) -> dict[str, Any]:
    return {"status": "missing", "reason": reason}


# ---- 讀數 2、3：現貨 ----

def _sign(value: float) -> int:
    return (value > 0) - (value < 0)


def _streaks(values: Sequence[float]) -> list[int]:
    """每天到當天為止同方向的連續天數（買超為正、賣超為負，淨額為 0 是 0）。"""
    out: list[int] = []
    for i, value in enumerate(values):
        side = _sign(value)
        if side == 0:
            out.append(0)
        elif i and _sign(out[-1]) == side:
            out.append(out[-1] + side)
        else:
            out.append(side)
    return out


def spot_readings(daily_flow: Sequence[Mapping[str, Any]], base_date: str) -> dict[str, Any]:
    """讀數 2 與 3。``daily_flow`` 每列：date、foreign、foreign_tsmc、turnover（億元）、n_buy、n_sell。"""
    rows = [row for row in daily_flow if str(row["date"]) <= base_date]
    if not rows or str(rows[-1]["date"]) != base_date:
        return {"streak_and_cumulative": _missing("外資買賣超截止日不是基準日"),
                "tsmc_split": _missing("外資買賣超截止日不是基準日")}
    params = PARAMETERS["spot"]
    dates = [str(row["date"]) for row in rows]
    foreign = [float(row["foreign"]) for row in rows]
    turnover = [float(row["turnover"]) for row in rows]
    streaks = _streaks(foreign)
    last = len(rows) - 1

    def ratio(end: int, window: int) -> float | None:
        return _cumulative_share(foreign, turnover, end, window)

    today_streak = streaks[last]
    side = "buy" if today_streak > 0 else "sell" if today_streak < 0 else "flat"
    continuation: dict[str, Any] | None = None
    if today_streak:
        exact = [i for i in range(last) if streaks[i] == today_streak]
        at_least = [i for i in range(last) if _sign(streaks[i]) == _sign(today_streak)
                    and abs(streaks[i]) >= abs(today_streak)]

        def rate(days: list[int]) -> dict[str, Any]:
            hits = sum(1 for i in days if _sign(foreign[i + 1]) == _sign(today_streak))
            return {"probability_pct": _pct(hits / len(days)) if days else None, "samples": len(days)}

        continuation = {"exactly_n_days": rate(exact), "n_days_or_more": rate(at_least),
                        "small_sample": len(exact) < params["streak_min_samples"]}
    cumulative = {}
    for window in params["cum_windows"]:
        value = ratio(last, window)
        cumulative[str(window)] = {
            "net_billion": round(sum(foreign[last - window + 1:]), 2) if last + 1 >= window else None,
            "share_of_turnover_pct": None if value is None else _pct(value)}
    history20 = [r for r in (ratio(i, 20) for i in range(len(rows))) if r is not None]
    today20 = ratio(last, 20)
    streak_reading = {
        "status": "ok",
        "today_net_billion": round(foreign[last], 2),
        "streak": {"side": side, "days": abs(today_streak)},
        "cumulative": cumulative,
        "history": {
            "period": _period(dates),
            "next_day_same_side": continuation,
            "cum20_percentile": None if today20 is None else _percentile_rank(history20, today20),
        },
    }

    tsmc = [float(row["foreign_tsmc"]) for row in rows]
    rest_today = foreign[last] - tsmc[last]
    gross = sum(abs(value) for value in foreign)
    sell_days = [i for i in range(len(rows)) if foreign[i] < 0]
    split_reading = {
        "status": "ok",
        "foreign_billion": round(foreign[last], 2),
        "tsmc_billion": round(tsmc[last], 2),
        "rest_billion": round(rest_today, 2),
        "stocks_bought": int(rows[last]["n_buy"]),
        "stocks_sold": int(rows[last]["n_sell"]),
        "history": {
            "period": _period(dates),
            "tsmc_share_of_foreign_pct": _pct(sum(abs(value) for value in tsmc) / gross) if gross else None,
            "sell_days_but_rest_bought_pct":
                _pct(sum(1 for i in sell_days if foreign[i] - tsmc[i] > 0) / len(sell_days)) if sell_days else None,
            "sell_days": len(sell_days),
        },
    }
    return {"streak_and_cumulative": streak_reading, "tsmc_split": split_reading}


# ---- 讀數 5：年線、季線 ----

def trend_reading(index_daily: Sequence[Mapping[str, Any]], base_date: str) -> dict[str, Any]:
    closes = _closes(index_daily, base_date)
    if not closes or closes[-1][0] != base_date:
        return _missing("加權指數截止日不是基準日")
    values = [close for _, close in closes]
    close = values[-1]
    out: dict[str, Any] = {"status": "ok", "close": close}
    for key, label in (("ma_long", "year_line"), ("ma_short", "quarter_line")):
        period = PARAMETERS["trend"][key]
        if len(values) < period:
            out[label] = {"days": period, "value": None, "position": None}
            continue
        average = sum(values[-period:]) / period
        out[label] = {"days": period, "value": round(average, 2),
                      "position": "above" if close > average else "below" if close < average else "on",
                      "distance_pct": _pct(close / average - 1)}
    return out


# ---- 讀數 6：外資台指期淨部位 ----

def _third_wednesday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(2 - first.weekday()) % 7 + 14)


def settlement_days(trading_days: Sequence[str]) -> set[str]:
    """每個月第三個星期三；那天休市就是之後的第一個交易日（一週內）。

    日曆在第三個星期三之後才開始的那個月不判斷（看不出那天之前是不是已經結算過）。"""
    days = sorted(trading_days)
    out: set[str] = set()
    if not days:
        return out
    months = sorted({(int(day[:4]), int(day[5:7])) for day in days})
    for year, month in months:
        target = _third_wednesday(year, month)
        if date.fromisoformat(days[0]) > target:
            continue
        for day in days:
            current = date.fromisoformat(day)
            if current >= target:
                if (current - target).days <= 6:
                    out.add(day)
                break
    return out


def _metric_stats(values: Mapping[int, float], current: float | None, cuts: Sequence[int],
                  forwards: Mapping[int, Mapping[int, float]], overall: Mapping[int, float | None]) -> dict[str, Any] | None:
    if current is None or not values:
        return None
    ordered = sorted(values.values())
    thresholds = [_nearest_rank(ordered, cut) for cut in cuts]
    bucket = _bucket_of(thresholds, current)
    members = [t for t, value in values.items() if _bucket_of(thresholds, value) == bucket]
    after = {}
    for horizon, by_day in forwards.items():
        returns = [by_day[t] for t in members if t in by_day]
        after[str(horizon)] = {"mean_pct": None if not returns else _pct(_mean(returns)), "samples": len(returns),
                               "all_days_mean_pct": None if overall[horizon] is None else _pct(overall[horizon])}
    return {"percentile": _percentile_rank(ordered, current), "bucket": _bucket_label(cuts, bucket),
            "bucket_days": len(members), "after": after}


def futures_reading(positions: Sequence[Mapping[str, Any]], index_daily: Sequence[Mapping[str, Any]],
                    base_date: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``positions`` 每列：date、net（大台＋小台÷4，口）。交易日曆用加權指數日 K。

    ``params`` 預設是 ``PARAMETERS["futures"]``（交給模型的就是這一組）；測試可以換小視窗手算。"""
    params = params or PARAMETERS["futures"]
    closes = _closes(index_daily, base_date)
    if not closes or closes[-1][0] != base_date:
        return _missing("加權指數截止日不是基準日")
    net_by_day = {str(row["date"]): float(row["net"]) for row in positions if str(row["date"]) <= base_date}
    if base_date not in net_by_day:
        return _missing("期貨部位截止日不是基準日")
    calendar = [day for day, _ in closes]
    settle = settlement_days(calendar)
    # 只看期貨有資料的那一段；中間缺一天就從缺的那天之後重新起算，不把兩天前的部位當成昨天。
    start = len(calendar) - 1
    while start > 0 and calendar[start - 1] in net_by_day:
        start -= 1
    days = calendar[start:]
    net = [net_by_day[day] for day in days]
    index_closes = [close for _, close in closes[start:]]
    last = len(days) - 1
    rel_window, chg_window = params["rel_window"], params["chg_window"]

    def rel(t: int) -> float | None:
        return None if t < rel_window else net[t] - sum(net[t - rel_window:t]) / rel_window

    def chg1(t: int) -> float | None:
        return None if t < 1 or days[t] in settle else net[t] - net[t - 1]

    def chg5(t: int) -> float | None:
        if t < chg_window or any(days[k] in settle for k in range(t - chg_window + 1, t + 1)):
            return None
        return net[t] - net[t - chg_window]

    forwards = {h: {t: r for t in range(len(days)) if (r := _forward(index_closes, t, h)) is not None}
                for h in params["forward"]}
    overall = {h: _mean(list(by_day.values())) for h, by_day in forwards.items()}
    metrics = {}
    for key, fn, na_reason in (
            ("rel_mean", rel, f"期貨部位不到 {rel_window + 1} 個交易日"),
            ("chg_window", chg5, f"近 {chg_window} 日含結算日（近月到期會讓口數跳動）"),
            ("chg1", chg1, "基準日是結算日（近月到期會讓口數跳動）")):
        history = {t: value for t in range(len(days)) if (value := fn(t)) is not None}
        current = fn(last)
        entry: dict[str, Any] = {"contracts": None if current is None else round(current)}
        if current is None:
            entry["not_applicable"] = na_reason if key != "chg1" or last >= 1 else "期貨部位只有一天"
        else:
            entry["history"] = _metric_stats(history, current, params["cuts"][key], forwards, overall)
        metrics[key] = entry
    return {
        "status": "ok",
        "net_contracts": round(net[last]),
        "settlement_day_today": base_date in settle,
        "relative_to_recent_mean": {"window_days": rel_window, **metrics["rel_mean"]},
        "change_over_window": {"window_days": chg_window, **metrics["chg_window"]},
        "change_1d": metrics["chg1"],
        "history_period": _period(days),
    }


# ---- 讀數 1：那斯達克 ----

def _us_before(base_date: str, edition: str, us_before: str | None) -> str:
    """看得到「美國日期早於這一天」的收盤。晚間版是基準日；晨間版是下一個台股開盤日（呼叫端給，
    例如週四基準、週五休市、週一開盤時看得到美股週五收盤）；沒給就當成基準日隔天。"""
    if us_before:
        return us_before
    if edition == "morning":
        return (date.fromisoformat(base_date) + timedelta(days=1)).isoformat()
    return base_date


def _known_us(series: Sequence[Mapping[str, Any]], before: str) -> list[tuple[str, float]]:
    return [row for row in _series(series) if row[0] < before]


_NASDAQ_LABELS = ("跌逾 1.5%", "跌 0.5～1.5%", "±0.5% 內", "漲 0.5～1.5%", "漲逾 1.5%")


def _nasdaq_bucket(change_pct: float) -> int:
    low2, low1, high1, high2 = PARAMETERS["nasdaq"]["edges_pct"]
    if change_pct < low2:
        return 0
    if change_pct < low1:
        return 1
    if change_pct <= high1:
        return 2
    if change_pct <= high2:
        return 3
    return 4


def nasdaq_reading(nasdaq: Sequence[Mapping[str, Any]], proxy_daily: Sequence[Mapping[str, Any]],
                   daily_flow: Sequence[Mapping[str, Any]], base_date: str, edition: str = "evening",
                   us_before: str | None = None) -> dict[str, Any]:
    """讀數 1。``nasdaq``：美國日期與收盤；``proxy_daily``：0050 的 date、open、close、reference（參考價）。

    歷史統計：每個台股交易日配上它前一個美股交易日的那斯達克漲跌，分五區算那個台股交易日的外資賣超機率、
    開盤跳空（開盤 ÷ 參考價）與盤中漲跌（收盤 ÷ 開盤）。參考價已經扣掉除權息與分割，這些日子不會算出假的跳空。"""
    known = _known_us(nasdaq, _us_before(base_date, edition, us_before))
    if len(known) < 2:
        return _missing("那斯達克收盤還沒接上或不足兩天（來源屬 D 票）")
    (_, prev_close), (last_day, last_close) = known[-2], known[-1]
    change = (last_close / prev_close - 1) * 100
    all_us = _series(nasdaq)
    us_change = {all_us[i][0]: (all_us[i][1] / all_us[i - 1][1] - 1) * 100 for i in range(1, len(all_us))}
    us_days = sorted(us_change)
    proxy = {str(row["date"]): row for row in proxy_daily}
    foreign = {str(row["date"]): float(row["foreign"]) for row in daily_flow}
    buckets: list[dict[str, list[float]]] = [{"sell": [], "gap": [], "intraday": []} for _ in _NASDAQ_LABELS]
    cursor = 0
    for day in sorted(set(proxy) | set(foreign)):
        if day > base_date:
            break
        while cursor < len(us_days) and us_days[cursor] < day:
            cursor += 1
        if cursor == 0:
            continue
        slot = buckets[_nasdaq_bucket(us_change[us_days[cursor - 1]])]
        if foreign.get(day):
            slot["sell"].append(1.0 if foreign[day] < 0 else 0.0)
        bar = proxy.get(day)
        if bar and bar.get("open") and bar.get("reference") and bar.get("close"):
            slot["gap"].append(bar["open"] / bar["reference"] - 1)
            slot["intraday"].append(bar["close"] / bar["open"] - 1)
    table = []
    for label, slot in zip(_NASDAQ_LABELS, buckets):
        table.append({"range": label,
                      "foreign_sell_pct": None if not slot["sell"] else _pct(_mean(slot["sell"])),
                      "foreign_days": len(slot["sell"]),
                      "open_gap_mean_pct": None if not slot["gap"] else _pct(_mean(slot["gap"])),
                      "intraday_mean_pct": None if not slot["intraday"] else _pct(_mean(slot["intraday"])),
                      "price_days": len(slot["gap"])})
    proxy_days = [day for day in sorted(proxy) if day <= base_date]
    # 統計表是「這種美股收盤之後的那一個台股交易日」：晚間版那一天就是基準日（已經收盤，用來解釋今天），
    # 晨間版才是下一個交易日（要預測的那天）。
    return {"status": "ok", "us_close_date": last_day, "change_pct": round(change, 2),
            "range": _NASDAQ_LABELS[_nasdaq_bucket(change)],
            "taiwan_day_it_applies_to": "基準日（已收盤）" if edition != "morning" else "基準日的下一個交易日",
            "history": {"next_taiwan_day_by_range": table, "proxy": PARAMETERS["nasdaq"]["proxy_code"],
                        "period": _period(proxy_days)}}


# ---- 讀數 4：美元 ----

def _corr(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return None if not sxx or not syy else round(sxy / math.sqrt(sxx * syy), 2)


def dollar_reading(dollar_index: Sequence[Mapping[str, Any]], usdtwd: Sequence[Mapping[str, Any]],
                   daily_flow: Sequence[Mapping[str, Any]], base_date: str, edition: str = "evening",
                   us_before: str | None = None) -> dict[str, Any]:
    """讀數 4。美元指數是美國日期（晚間版看前一晚）；美元兌台幣是台灣日期（看得到基準日當天）。

    「和外資同向」指照常見的關係走：美元（或美元兌台幣）近 20 日上升、外資近 20 日累計賣超，或反過來。
    外資近 20 日累計和讀數 2 同一套算法（股數 × 收盤的加總）。"""
    window = PARAMETERS["dollar"]["window"]
    flow = [row for row in daily_flow if str(row["date"]) <= base_date]
    if not flow or str(flow[-1]["date"]) != base_date or len(flow) < window:
        return _missing("外資買賣超截止日不是基準日或不足二十天")
    dates = [str(row["date"]) for row in flow]
    foreign = [float(row["foreign"]) for row in flow]
    turnover = [float(row["turnover"]) for row in flow]
    f20 = {dates[t]: share for t in range(len(flow))
           if (share := _cumulative_share(foreign, turnover, t, window)) is not None}
    today_f20 = sum(foreign[-window:])
    out: dict[str, Any] = {"status": "ok", "foreign_20d_net_billion": round(today_f20, 2),
                           "dividend_season": int(base_date[5:7]) in PARAMETERS["dollar"]["dividend_months"]}
    for key, series, us_dated in (("dollar_index", dollar_index, True), ("usd_twd", usdtwd, False)):
        rows = _series(series)
        known = (_known_us(series, _us_before(base_date, edition, us_before)) if us_dated
                 else [row for row in rows if row[0] <= base_date])
        if len(known) <= window:
            out[key] = {"status": "missing", "reason": "資料還沒接上或不足二十一天（來源屬 D 票）"}
            continue
        change = known[-1][1] / known[-1 - window][1] - 1
        direction = "up" if change > 0 else "down" if change < 0 else "flat"
        # 長期相關：每個台股交易日，當時已知的近 20 日變化 對 外資近 20 日累計占成交金額（美國日期要早於台股日）
        changes = {rows[i][0]: rows[i][1] / rows[i - window][1] - 1 for i in range(window, len(rows))}
        change_days = sorted(changes)
        xs, ys, cursor = [], [], 0
        for day in sorted(f20):
            while cursor < len(change_days) and (change_days[cursor] < day if us_dated else change_days[cursor] <= day):
                cursor += 1
            if cursor:
                xs.append(changes[change_days[cursor - 1]])
                ys.append(f20[day])
        out[key] = {"status": "ok", "as_of": known[-1][0], "change_20d_pct": _pct(change), "direction": direction,
                    "matches_usual_relation": None if direction == "flat" or not today_f20
                    else (change > 0) == (today_f20 < 0),
                    "history": {"correlation_with_foreign_20d": _corr(xs, ys), "samples": len(xs)}}
    if all(out[key].get("status") != "ok" for key in ("dollar_index", "usd_twd")):
        return _missing("美元指數與美元兌台幣都還沒接上（來源屬 D 票）")
    out["history_period"] = _period(dates)
    return out


# ---- 組成一層 ----

def _version() -> str:
    """這一層的版本只由參數與說明決定（每天的數字不同，版本不變）。"""
    text = json.dumps({"parameters": PARAMETERS, "notes": NOTES, "schema": SCHEMA}, ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"))
    return "ff-v1-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


# 這一層的版本：交給模型的外資層參數或說明改了，帳本的輸入版本就跟著換（forecast_ledger 把它算進去）。
VERSION = _version()


def build_layer(*, base_date: str, index_daily: Sequence[Mapping[str, Any]],
                daily_flow: Sequence[Mapping[str, Any]], futures_positions: Sequence[Mapping[str, Any]],
                proxy_daily: Sequence[Mapping[str, Any]] = (), nasdaq: Sequence[Mapping[str, Any]] = (),
                dollar_index: Sequence[Mapping[str, Any]] = (), usdtwd: Sequence[Mapping[str, Any]] = (),
                edition: str = "evening", us_before: str | None = None, nasdaq_in_prompt: bool = True,
                sources: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """交給 Gemini 的外資層（六個讀數）。缺資料的讀數標 missing 並寫原因，其他照給；預測照樣寫。

    那斯達克、美元指數、美元兌台幣的來源屬 D 票，接上之前讀數 1、4 是 missing。
    那斯達克來源 EODHD 條款只允許私用；本人 2026-11-16 知道這點後決定「交」（D 票第 8 片），所以預設交給模型。
    ``nasdaq_in_prompt`` 改成 False（跟 D 票 overseas_inputs.LICENSES["eodhd"]["in_prompt"] 同步）時，讀數 1 在這一層標
    withheld，算好的內容放在 ``record_only``：**呼叫端要把它拿出來，只存進紀錄、不放進模型輸入**。"""
    spot = spot_readings(daily_flow, base_date)
    nasdaq_full = nasdaq_reading(nasdaq, proxy_daily, daily_flow, base_date, edition, us_before)
    record_only: dict[str, Any] = {}
    nasdaq_shown = nasdaq_full
    if not nasdaq_in_prompt and nasdaq_full.get("status") == "ok":
        record_only["nasdaq_last_session"] = nasdaq_full
        nasdaq_shown = {"status": "withheld", "reason": "來源條款只允許私用，本人決定前不交給模型"}
    layer = {
        "schema": SCHEMA,
        "version": VERSION,
        "base_date": base_date,
        "edition": edition,
        "readings": {
            "nasdaq_last_session": nasdaq_shown,
            "streak_and_cumulative": spot["streak_and_cumulative"],
            "tsmc_split": spot["tsmc_split"],
            "dollar_and_twd_vs_foreign": dollar_reading(dollar_index, usdtwd, daily_flow, base_date, edition,
                                                        us_before),
            "index_vs_year_and_quarter_lines": trend_reading(index_daily, base_date),
            "futures_net_position": futures_reading(futures_positions, index_daily, base_date),
        },
        "notes": None,
        "units": {"amount": "億元（上市個股外資買賣超股數 × 當日收盤的加總，近似值）", "futures": "口（大台＋小台÷4）",
                  "percent": "%"},
        "sources": dict(sources or {}),
    }
    layer["missing"] = [key for key, value in layer["readings"].items() if value.get("status") == "missing"]
    layer["withheld"] = [key for key, value in layer["readings"].items() if value.get("status") == "withheld"]
    # 讀數缺的時候不給它的說明（例如海外來源還沒接上時不提那斯達克、美元，免得模型去引用不存在的資料）。
    note_of = {"nasdaq_last_session": "nasdaq", "streak_and_cumulative": "spot", "tsmc_split": "spot",
               "dollar_and_twd_vs_foreign": "dollar", "index_vs_year_and_quarter_lines": "trend",
               "futures_net_position": "futures"}
    present = {note_of[key] for key, value in layer["readings"].items() if value.get("status") == "ok"}
    layer["notes"] = {key: text for key, text in NOTES.items() if key in present}
    layer["record_only"] = record_only
    return layer


# ---- 現貨每日彙總：上市法人資料庫 × 個股日線 ----

def aggregate_day(foreign_shares: Mapping[str, float], quotes: Mapping[str, tuple[float | None, float]],
                  tsmc_code: str = PARAMETERS["spot"]["tsmc_code"]) -> dict[str, Any]:
    """一天的外資金額彙總。``foreign_shares``：代號 → 外資買賣超股數；``quotes``：代號 → (收盤, 成交金額元)。

    沒有收盤價的股票不計（金額算不出來），成交金額也只加有收盤價的，兩邊同一批股票。"""
    amounts = {code: shares * quotes[code][0] for code, shares in foreign_shares.items()
               if code in quotes and quotes[code][0] is not None and shares}
    return {
        "foreign": round(sum(amounts.values()) / 1e8, 4),
        "foreign_tsmc": round(amounts.get(tsmc_code, 0.0) / 1e8, 4),
        "turnover": round(sum(turnover for close, turnover in quotes.values() if close is not None) / 1e8, 4),
        "n_buy": sum(1 for value in amounts.values() if value > 0),
        "n_sell": sum(1 for value in amounts.values() if value < 0),
    }


def _connect_ro(path: Path | str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)


def _quotes_of(con: sqlite3.Connection, day: str) -> dict[str, tuple[float | None, float]]:
    return {code: (close, float(turnover or 0)) for code, close, turnover in
            con.execute("SELECT code, close, turnover FROM daily WHERE date = ?", (day,))}


def proxy_daily_from_stock_db(stock_db: Path | str, code: str = PARAMETERS["nasdaq"]["proxy_code"]) -> list[dict[str, Any]]:
    """讀數 1 用的 0050 日線：開盤、收盤、參考價（收盤 − 漲跌；漲跌空白的日子沒有參考價）。"""
    with closing(_connect_ro(stock_db)) as con:
        rows = con.execute("SELECT date, open, close, change FROM daily WHERE code = ? ORDER BY date", (code,)).fetchall()
    return [{"date": day, "open": open_, "close": close,
             "reference": None if change is None or close is None else round(close - change, 4)}
            for day, open_, close, change in rows]


def daily_flow_from_sources(institutional_db: Path | str, stock_db: Path | str, *, since: str | None = None,
                            until: str | None = None) -> list[dict[str, Any]]:
    """從上市法人資料庫（股數）與個股日線（收盤、成交金額）算每天的外資彙總；兩邊都有的日子才算。

    一天一天查（一次只放一天的個股在記憶體裡）。"""
    out: list[dict[str, Any]] = []
    with closing(_connect_ro(institutional_db)) as inst, closing(_connect_ro(stock_db)) as stock:
        days = [day for (day,) in inst.execute("SELECT DISTINCT date FROM institutional ORDER BY date")
                if (not since or day >= since) and (not until or day <= until)]
        for day in days:
            quotes = _quotes_of(stock, day)
            if not quotes:
                continue
            shares = {code: float(value or 0) for code, value in
                      inst.execute("SELECT code, foreign_net FROM institutional WHERE date = ?", (day,))}
            out.append({"date": day, **aggregate_day(shares, quotes)})
    return out


# ---- 起始資料：2016 起的外資彙總（產品的法人資料庫 2025-10-27 才開始） ----

FLOW_SEED = Path(__file__).resolve().parent / "seeds/foreign-flow-daily.json"
FLOW_SEED_SCHEMA = "foreign-flow-daily-seed/v1"


def _number(text: Any) -> float:
    value = str(text).replace(",", "").strip()
    return 0.0 if value in ("", "--", "---", "N/A", "-") else float(value)


def t86_foreign_shares(payload: Mapping[str, Any]) -> dict[str, float]:
    """證交所 T86 原件的外資買賣超股數（2017-12 以前只有一欄「外資買賣超股數」，之後拆成外陸資＋外資自營商）。"""
    if payload.get("stat") != "OK":
        raise ValueError(f"T86 stat 不是 OK：{payload.get('stat')}")
    fields = [str(field).strip() for field in payload["fields"]]
    code_at = fields.index("證券代號")
    if "外資買賣超股數" in fields:
        columns = [fields.index("外資買賣超股數")]
    else:
        columns = [fields.index("外陸資買賣超股數(不含外資自營商)"), fields.index("外資自營商買賣超股數")]
    return {str(row[code_at]).strip(): sum(_number(row[k]) for k in columns) for row in payload["data"]}


def build_flow_seed(t86_dir: Path | str, stock_db: Path | str, out: Path | str = FLOW_SEED) -> dict[str, Any]:
    """把下載好的 T86 原件（``YYYYMMDD.json.gz``）× 個股日線算成每天的外資彙總，寫成起始資料。"""
    rows, sources, skipped = [], [], []
    with closing(_connect_ro(stock_db)) as stock:
        for path in sorted(Path(t86_dir).glob("*.json.gz")):
            day = f"{path.name[:4]}-{path.name[4:6]}-{path.name[6:8]}"
            data = path.read_bytes()
            t86 = json.loads(gzip.decompress(data).decode("utf-8"))
            quotes = _quotes_of(stock, day)
            if not quotes:
                skipped.append(path.name)
                continue
            rows.append({"date": day, **aggregate_day(t86_foreign_shares(t86), quotes)})
            sources.append([path.name, hashlib.sha256(data).hexdigest()])
    seed = {"schema": FLOW_SEED_SCHEMA, "unit": "億元（外資買賣超股數 × 當日收盤，只含上市）",
            "source_url": "https://www.twse.com.tw/rwd/zh/fund/T86（selectType=ALLBUT0999）",
            "sources": sources, "rows": rows}
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(seed, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"days": len(rows), "from": rows[0]["date"] if rows else None, "to": rows[-1]["date"] if rows else None,
            "skipped": skipped}


def load_daily_flow(institutional_db: Path | str, stock_db: Path | str, *, seed: Path | str = FLOW_SEED,
                    until: str | None = None) -> list[dict[str, Any]]:
    """起始資料＋起始資料之後由資料庫算的每一天；兩段接在一起，日期遞增。"""
    rows: list[dict[str, Any]] = []
    if Path(seed).exists():
        payload = json.loads(Path(seed).read_text(encoding="utf-8"))
        if payload.get("schema") != FLOW_SEED_SCHEMA:
            raise ValueError(f"起始資料格式不是 {FLOW_SEED_SCHEMA}")
        rows = [row for row in payload["rows"] if not until or row["date"] <= until]
    since = None
    if rows:
        last = date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)
        since = last.isoformat()
    if Path(institutional_db).exists() and Path(stock_db).exists():
        rows += daily_flow_from_sources(institutional_db, stock_db, since=since, until=until)
    return rows
