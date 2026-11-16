"""過去的經驗（E 票）：交給模型的兩層輸入——相似盤勢層與教訓層。

本人說這個工具的作用是「不要讓歷史重演，根據過去的經驗去輔助判斷」（ANALYSIS_PLAN〈2026-11-15 事前預測紀錄＋
到期對帳〉第 6 題）。這裡只算事實，由預測帳本在寫一份預測時呼叫、放進鎖定的輸入：

- ``similar_regimes(rows)``：把歷史上的日子依事先寫定的定義歸類，統計各類後來 1、5、20 個交易日怎麼走。
- ``lessons(forecasts, reconciliations, base_date)``：過去對帳的事實（猜中與沒中的次數、最近幾次沒中）。

相似盤勢的定義（本人 2026-11-16 看過白話版、跑任何統計之前選「照這樣」，〔出處：E 票 session 4e4243f9〕）：

- 只用加權指數日 K 收盤；每一天只看那一天和之前的收盤。月線＝近 20 日平均，季線＝近 60 日平均。
- 「高檔」＝一年（240 日）來的最高收盤出現在最近 60 個交易日內。
- 多頭中的回檔＝高檔＋收盤仍在季線之上＋收盤比近 60 日最高收盤低 5% 以上。
- 高檔轉弱＝高檔＋收盤跌破季線＋月線比 5 個交易日前低。兩類互斥，其他日子不歸類。
- 同類連續的日子算一段；兩段隔不到 20 個交易日併成一段；每段只算一次，起點＝第一天；
  看起點後第 1、5、20 個交易日收盤的漲跌，還沒滿那麼多天的那一格不算；現在進行中的這一段不算；
  少於 10 段標「樣本少」；另附所有交易日同期間的漲跌當對照。

``PARAMETERS`` 整組算進帳本的輸入版本，改任何一個數字都是新系列。教訓層的內容隨時間增長不換版本，
只有這一層「有或沒有」才算（帳本 ``PARAMETERS["layers"]``）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

PULLBACK = "多頭中的回檔"
WEAK = "高檔轉弱"
HORIZONS = (1, 5, 20)

PARAMETERS: dict[str, Any] = {
    "similar_regimes": {
        "classes": [PULLBACK, WEAK],
        "month_ma": 20,
        "quarter_ma": 60,
        "high_window": 60,
        "year_window": 240,
        "pullback_min_drop_pct": 5,
        "month_slope_lag": 5,
        "merge_gap_bars": 20,
        "min_episodes": 10,
    },
    "lessons": {"recent_misses": 5},
}

NOTES = (
    "高檔轉弱只代表和歷史上某些情況相似，不保證會大跌，也不保證能避開大跌：策略驗證線查過，"
    "2017 年以來 19 次大跌的高點當天，現有資料沒有訊號能事先看出來。",
    "今天屬於多頭中的回檔時，二十日那一格的轉弱線就是「跌破才算趨勢轉壞」的那條線；沒跌破之前，判斷是回檔、不是轉壞。",
    "low_sample 為 true 的類別樣本太少，只能參考，不能當成規律。",
)


# ---- 相似盤勢層 ----

def _classify(closes: list[float]) -> list[str | None]:
    """逐日歸類；第 t 天只用第 0..t 天的收盤。資料不足一年的日子不歸類。"""
    p = PARAMETERS["similar_regimes"]
    month, quarter, recent, year, lag = (p["month_ma"], p["quarter_ma"], p["high_window"], p["year_window"],
                                         p["month_slope_lag"])
    drop = p["pullback_min_drop_pct"] / 100
    out: list[str | None] = []
    for t, close in enumerate(closes):
        if t + 1 < max(year, month + lag):
            out.append(None)
            continue
        recent_high = max(closes[t - recent + 1:t + 1])
        high = recent_high == max(closes[t - year + 1:t + 1])
        quarter_ma = sum(closes[t - quarter + 1:t + 1]) / quarter
        month_now = sum(closes[t - month + 1:t + 1]) / month
        month_before = sum(closes[t - lag - month + 1:t - lag + 1]) / month
        if high and close > quarter_ma and close <= recent_high * (1 - drop):
            out.append(PULLBACK)
        elif high and close < quarter_ma and month_now < month_before:
            out.append(WEAK)
        else:
            out.append(None)
    return out


def _episodes(labels: list[str | None], regime: str) -> list[list[int]]:
    """同類連續的日子算一段（[起點, 終點]）；下一段起點離上一段終點不到併段天數就併成一段。"""
    gap = PARAMETERS["similar_regimes"]["merge_gap_bars"]
    episodes: list[list[int]] = []
    for t, label in enumerate(labels):
        if label != regime:
            continue
        if episodes and t - episodes[-1][1] < gap:
            episodes[-1][1] = t
        else:
            episodes.append([t, t])
    return episodes


def _pct(start: float, end: float) -> float:
    return round((end / start - 1) * 100, 2)


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def _distribution(moves: list[float], *, with_range: bool) -> dict[str, Any]:
    """moves 是未四捨五入的漲跌幅（小數）；百分比最後才四捨五入到小數兩位。"""
    out: dict[str, Any] = {
        "samples": len(moves),
        "up": sum(move > 0 for move in moves),
        "down": sum(move < 0 for move in moves),
        "median_pct": round(_median(moves) * 100, 2) if moves else None,
    }
    if with_range:
        out["min_pct"] = round(min(moves) * 100, 2) if moves else None
        out["max_pct"] = round(max(moves) * 100, 2) if moves else None
    return out


def similar_regimes(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """rows 是到基準日為止、已清理過的指數日 K（帳本的 ``clean_daily``）；收盤缺漏的日子不納入歸類。"""
    usable = [row for row in rows if row.get("close") not in (None, 0)]
    dates = [row["date"] for row in usable]
    closes = [float(row["close"]) for row in usable]
    labels = _classify(closes)
    last = len(closes) - 1
    minimum = PARAMETERS["similar_regimes"]["min_episodes"]

    today = {"regime": None, "since": None}
    classes: dict[str, Any] = {}
    for regime in PARAMETERS["similar_regimes"]["classes"]:
        spans = _episodes(labels, regime)
        if spans and labels[last] == regime and spans[-1][1] == last:
            today = {"regime": regime, "since": dates[spans[-1][0]]}
            spans = spans[:-1]  # 現在進行中的這一段不算樣本
        episodes, moves = [], {h: [] for h in HORIZONS}
        for start, _ in spans:
            returns: dict[str, float | None] = {}
            for horizon in HORIZONS:
                end = start + horizon
                if end <= last:
                    moves[horizon].append(closes[end] / closes[start] - 1)
                    returns[str(horizon)] = _pct(closes[start], closes[end])
                else:
                    returns[str(horizon)] = None  # 還沒滿那麼多天
            episodes.append({"start": dates[start], "returns_pct": returns})
        classes[regime] = {
            "episodes": episodes,
            "horizons": {str(h): {**_distribution(moves[h], with_range=True), "low_sample": len(moves[h]) < minimum}
                         for h in HORIZONS},
        }
    all_days = {str(h): _distribution([closes[t + h] / closes[t] - 1 for t in range(len(closes) - h)],
                                      with_range=False) for h in HORIZONS}
    return {
        "today": today,
        "classes": classes,
        "all_days": all_days,
        "min_episodes": minimum,
        "history": {"from": dates[0] if dates else None, "to": dates[-1] if dates else None, "bars": len(dates)},
        "notes": list(NOTES),
    }


# ---- 教訓層 ----

def lessons(forecasts: Sequence[Mapping[str, Any]], reconciliations: Sequence[Mapping[str, Any]],
            base_date: str) -> dict[str, Any]:
    """只用寫這一份時已經知道結果的對帳：到期日不晚於基準日、預測的基準日早於這一份。

    所以晨間版看不到同一基準日的晚間版（它最早的到期日也在基準日之後）；重跑同一份時，之後才對完的帳不會改變輸入。
    只放程式整理的事實與當時鎖定的原文，不放任何改寫的檢討。
    """
    by_id = {record["forecast_id"]: record for record in forecasts if record.get("kind") == "forecast"}
    counts = {str(h): {"hits": 0, "misses": 0} for h in HORIZONS}
    misses = []
    for outcome in reconciliations:
        forecast = by_id.get(outcome.get("forecast_id"))
        if forecast is None or outcome["expiry_date"] > base_date or forecast["base_date"] >= base_date:
            continue
        cell = forecast["cells"][str(outcome["horizon"])]
        hit = cell["zone"] == outcome["actual_zone"]
        counts[str(outcome["horizon"])]["hits" if hit else "misses"] += 1
        if not hit:
            misses.append({
                "base_date": forecast["base_date"], "edition": forecast["edition"], "horizon": outcome["horizon"],
                "regime": forecast["regime"], "zone": cell["zone"],
                "strong_line": dict(cell["strong_line"]), "weak_line": dict(cell["weak_line"]),
                "invalidation": cell["invalidation"],
                "expiry_date": outcome["expiry_date"], "expiry_close": outcome["expiry_close"],
                "actual_zone": outcome["actual_zone"],
            })
    misses.sort(key=lambda item: (item["expiry_date"], item["base_date"], item["edition"], item["horizon"]),
                reverse=True)
    return {"counts": counts, "recent_misses": misses[:PARAMETERS["lessons"]["recent_misses"]]}
