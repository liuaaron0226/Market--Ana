"""J 票：大盤委買委賣（本人 2026-11-19 題 1「要找」；同意新增這一個對外抓取）。

來源：證交所每 5 秒委託成交統計（``/rwd/zh/afterTrading/MI_5MINS?date=YYYYMMDD``，官方公開、可依日期查歷史）。
每一列是到那一刻為止的累積委託買進／賣出筆數與張數、累積成交筆數、張數與金額。

由程式整理成大盤的 ``order_flow`` 層：
- 基準日（收盤那一刻）的委買／委賣張數比、筆數比；
- 早盤（到 10:00）、盤中（到 12:00）、尾盤（到 13:30）三段結束那一刻的比值，和比上一段的變化，以及各段成交張數。
  累積委託張數盤中會變少（撤單、改單扣掉），所以三段比的是那一刻的比值，不把委託張數相減；成交張數只增不減，相減；
- 和前幾個交易日收盤那一刻的比值比。

按日期抓，晨間版只抓基準日（開盤日的前一個交易日）與更早的日子，看不到開盤日的盤中；重播用官方歷史。
每一天抓一次、設逾時、失敗不重試（那一天記缺），同一次執行同一天只抓一次；兩次之間停一下，不要打太快。
"""
from __future__ import annotations

import json
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any

SCHEMA = "order-flow/v1"
URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_5MINS?date={day}&response=json"
TIMEOUT_SECONDS = 20
PAUSE_SECONDS = 3.0
EARLIER_DAYS = 3
SEGMENTS = (("早盤", "10:00:00"), ("盤中", "12:00:00"), ("尾盤", "13:30:00"))
USER_AGENT = "Mozilla/5.0 (compatible; market-ana personal research)"
NOTE = ("order_flow 是證交所每 5 秒委託成交統計整理的全市場委買委賣：qty_ratio 是累積委買張數 ÷ 累積委賣張數，"
        "count_ratio 是筆數的比；segments 是早盤、盤中、尾盤三段結束那一刻的比值與比上一段的變化（早盤是和 09:00 開盤前那一刻比，"
        "開盤前的掛單比通常偏高，早盤下降是常態），以及各段成交張數；"
        "earlier 與 vs_earlier 是和前幾個交易日比。委託隨時可以撤單、改單，掛單多寡只描述當天買賣雙方的掛單意願，"
        "不代表之後的方向，不能單獨決定方向，也不能單獨當成轉強或轉弱的理由；理由裡不得寫出張數、筆數、比值或百分比。")
PARAMETERS: dict[str, Any] = {"schema": SCHEMA, "source": "TWSE MI_5MINS", "segments": [list(item) for item in SEGMENTS],
                              "earlier_days": EARLIER_DAYS, "note": NOTE}


def _number(text: Any) -> int | None:
    try:
        return int(str(text).replace(",", "").strip())
    except ValueError:
        return None


def parse(payload: Mapping[str, Any] | None, day: str) -> list[dict[str, Any]] | None:
    """官方回應 → 那一天的每一列（時間、累積委買／委賣筆與張、累積成交筆與張）；不是那一天或沒有資料是 None。"""
    if not payload or payload.get("stat") != "OK" or str(payload.get("date")) != day.replace("-", ""):
        return None
    rows = []
    for row in payload.get("data") or []:
        if len(row) < 7:
            continue
        values = [_number(value) for value in row[1:7]]
        if None in values:
            continue
        rows.append({"time": str(row[0]), "buy_count": values[0], "buy_qty": values[1], "sell_count": values[2],
                     "sell_qty": values[3], "trade_count": values[4], "traded_lots": values[5]})
    return sorted(rows, key=lambda row: row["time"]) or None


def _ratio(top: int, bottom: int) -> float | None:
    return round(top / bottom, 2) if bottom else None


def _at(rows: Sequence[Mapping[str, Any]], until: str) -> Mapping[str, Any] | None:
    before = [row for row in rows if row["time"] <= until]
    return before[-1] if before else None


def day_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    last = rows[-1]
    segments, previous = [], rows[0]
    for name, until in SEGMENTS:
        row = _at(rows, until)
        if row is None:
            continue
        qty_ratio, before = _ratio(row["buy_qty"], row["sell_qty"]), _ratio(previous["buy_qty"], previous["sell_qty"])
        segments.append({"segment": name, "until": until[:5], "qty_ratio": qty_ratio,
                         "qty_ratio_change": round(qty_ratio - before, 2) if None not in (qty_ratio, before) else None,
                         "count_ratio": _ratio(row["buy_count"], row["sell_count"]),
                         "traded_lots": row["traded_lots"] - previous["traded_lots"]})
        previous = row
    return {"qty_ratio": _ratio(last["buy_qty"], last["sell_qty"]),
            "count_ratio": _ratio(last["buy_count"], last["sell_count"]),
            "traded_lots": last["traded_lots"], "segments": segments}


def order_flow_layer(base_rows: Sequence[Mapping[str, Any]] | None,
                     earlier: Sequence[tuple[str, Sequence[Mapping[str, Any]] | None]], base_date: str) -> dict[str, Any]:
    if not base_rows:
        return {"status": "missing", "reason": "證交所沒有基準日的每 5 秒委託成交統計"}
    summary = day_summary(base_rows)
    kept = [{"date": day, "qty_ratio": (s := day_summary(rows))["qty_ratio"], "count_ratio": s["count_ratio"]}
            for day, rows in earlier if rows]
    missing = [day for day, rows in earlier if not rows]
    vs = None
    qty = [item["qty_ratio"] for item in kept if item["qty_ratio"] is not None]
    count = [item["count_ratio"] for item in kept if item["count_ratio"] is not None]
    if qty and count and summary["qty_ratio"] is not None and summary["count_ratio"] is not None:
        vs = {"days": len(kept), "qty_ratio_change": round(summary["qty_ratio"] - sum(qty) / len(qty), 2),
              "count_ratio_change": round(summary["count_ratio"] - sum(count) / len(count), 2)}
    return {"status": "ok", "schema": SCHEMA, "data_date": base_date, "source": "臺灣證券交易所 每5秒委託成交統計",
            **summary, "earlier": kept, "earlier_missing": missing, "vs_earlier": vs, "note": NOTE}


class TwseOrderStats:
    """按日期抓、同一次執行同一天只抓一次（成功或失敗都記住，不重試）；兩次請求之間停一下。"""

    def __init__(self, *, opener: Callable[..., Any] = urllib.request.urlopen,
                 pause: Callable[[float], None] = time.sleep):
        self.opener, self.pause = opener, pause
        self.days: dict[str, list[dict[str, Any]] | None] = {}
        self.errors: dict[str, str] = {}
        self.requests = 0

    def day(self, day: str) -> list[dict[str, Any]] | None:
        if day in self.days:
            return self.days[day]
        if self.requests:
            self.pause(PAUSE_SECONDS)
        self.requests += 1
        request = urllib.request.Request(URL.format(day=day.replace("-", "")),
                                         headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self.opener(request, timeout=TIMEOUT_SECONDS) as response:
                self.days[day] = parse(json.loads(response.read().decode("utf-8")), day)
        except Exception as error:  # noqa: BLE001 — 那一天抓不到只記缺
            self.days[day] = None
            self.errors[day] = type(error).__name__
        return self.days[day]

    def layer(self, base_date: str, earlier_dates: Sequence[str]) -> tuple[dict[str, Any], dict[str, str]]:
        """基準日與它之前的幾個交易日（呼叫端從日 K 取，不含開盤日）。"""
        base = self.day(base_date)
        earlier = [(day, self.day(day)) for day in earlier_dates] if base else []
        layer = order_flow_layer(base, earlier, base_date)
        wanted = {base_date, *earlier_dates}
        return layer, {day: kind for day, kind in self.errors.items() if day in wanted}
