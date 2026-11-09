import argparse
import csv
import io
import json
import statistics
from collections import defaultdict
from datetime import date, datetime, time as clock_time
from pathlib import Path
from typing import Any, Callable, Iterable

ROOT = Path("C:/Users/User/Desktop/Market--Ana")
SNAPSHOT_ROOT = ROOT / "data/market-snapshots/analysis-20261106"
BACKTEST_PATH = ROOT / "data/backtest/20261106-break-prior-low.json"
OUTPUT_PATH = ROOT / "web/analysis-data.js"
CUTOFF = date(2026, 11, 5)
MAX_OUTPUT_BYTES = 3 * 1024 * 1024

INDEX_URL = "https://www.twse.com.tw/indicesReport/MI_5MINS_HIST"
TURNOVER_URL = "https://www.twse.com.tw/exchangeReport/FMTQIK"
INTRADAY_INDEX_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_INDEX"
INSTITUTIONAL_URL = "https://www.twse.com.tw/fund/BFI82U"
SECTOR_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
FUTURES_DOWNLOAD_URL = "https://www.taifex.com.tw/cht/3/futContractsDateDown"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(value, dict), f"JSON 最外層不是物件：{path}")
    return value


def load_wrapper(path: Path) -> dict[str, Any]:
    wrapper = load_json(path)
    require("data" in wrapper, f"原件缺少 data：{path}")
    return wrapper


def relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def number(value: str) -> float:
    return float(value.replace(",", "").strip())


def integer(value: str) -> int:
    cleaned = value.replace(",", "").strip()
    return int(cleaned) if cleaned else 0


def roc_to_iso(value: str) -> str:
    roc_year, month, day = value.strip().split("/")
    return f"{int(roc_year) + 1911:04d}-{int(month):02d}-{int(day):02d}"


def parse_compact_date_from_name(path: Path) -> str:
    compact = path.stem.rsplit("-", 1)[-1]
    require(len(compact) == 8 and compact.isdigit(), f"檔名缺少 YYYYMMDD：{path}")
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"


def assert_ohlc(bars: list[dict[str, Any]], label: str) -> None:
    require(bool(bars), f"{label} 沒有資料")
    keys = [str(bar.get("timestamp") or bar.get("date") or bar.get("period")) for bar in bars]
    require(keys == sorted(keys), f"{label} 日期／時間未遞增")
    require(len(keys) == len(set(keys)), f"{label} 日期／時間重複")
    for bar in bars:
        open_value = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
        require(high >= max(open_value, close), f"{label} high 不合理：{bar}")
        require(low <= min(open_value, close), f"{label} low 不合理：{bar}")
        require(high >= low, f"{label} high < low：{bar}")
        if "amount" in bar:
            require(int(bar["amount"]) >= 0, f"{label} 成交金額為負：{bar}")


def load_daily() -> tuple[list[dict[str, Any]], list[Path]]:
    index_files = sorted((SNAPSHOT_ROOT / "index-monthly").glob("taiex-*.json"))
    turnover_files = sorted((SNAPSHOT_ROOT / "turnover-monthly").glob("fmtqik-*.json"))
    expected_months = (CUTOFF.year - 2016) * 12 + CUTOFF.month
    require(len(index_files) == expected_months, f"日K月份原件應為 {expected_months}，實際 {len(index_files)}")
    require(len(turnover_files) == expected_months, f"成交金額月份原件應為 {expected_months}，實際 {len(turnover_files)}")

    turnover_by_date: dict[str, int] = {}
    turnover_months: set[str] = set()
    for path in turnover_files:
        month = path.stem.rsplit("-", 1)[-1]
        turnover_months.add(month)
        payload = load_wrapper(path)["data"]
        require(payload.get("stat") == "OK", f"成交金額 stat 非 OK：{path}")
        rows = payload.get("data", [])
        require(bool(rows), f"成交金額月份無資料：{month}")
        for row in rows:
            iso_date = roc_to_iso(row[0])
            if date.fromisoformat(iso_date) <= CUTOFF:
                value = integer(row[2])
                if iso_date in turnover_by_date:
                    require(turnover_by_date[iso_date] == value, f"成交金額重複且衝突：{iso_date}")
                turnover_by_date[iso_date] = value

    daily_by_date: dict[str, dict[str, Any]] = {}
    index_months: set[str] = set()
    for path in index_files:
        month = path.stem.rsplit("-", 1)[-1]
        index_months.add(month)
        payload = load_wrapper(path)["data"]
        require(payload.get("stat") == "OK", f"日K stat 非 OK：{path}")
        rows = payload.get("data", [])
        require(bool(rows), f"日K月份無資料：{month}")
        for row in rows:
            iso_date = roc_to_iso(row[0])
            if date.fromisoformat(iso_date) > CUTOFF:
                continue
            require(iso_date in turnover_by_date, f"日K 缺成交金額：{iso_date}")
            bar = {
                "date": iso_date,
                "open": number(row[1]),
                "high": number(row[2]),
                "low": number(row[3]),
                "close": number(row[4]),
                "amount": turnover_by_date[iso_date],
            }
            if iso_date in daily_by_date:
                require(daily_by_date[iso_date] == bar, f"日K 重複且衝突：{iso_date}")
            daily_by_date[iso_date] = bar

    require(index_months == turnover_months, "日K與成交金額月份集合不一致")
    daily = [daily_by_date[key] for key in sorted(daily_by_date)]
    assert_ohlc(daily, "日K")
    require(daily[0]["date"].startswith("2016-01"), f"日K起日不在 2016-01：{daily[0]['date']}")
    require(daily[-1]["date"] == CUTOFF.isoformat(), f"日K截止日錯誤：{daily[-1]['date']}")
    require(len(daily) >= 2600, f"日K筆數過少：{len(daily)}")

    parsed_dates = [date.fromisoformat(bar["date"]) for bar in daily]
    gaps = [(parsed_dates[index] - parsed_dates[index - 1]).days for index in range(1, len(parsed_dates))]
    require(max(gaps) <= 15, f"日K疑似斷裂，最大日曆間隔 {max(gaps)} 天")

    by_date = {bar["date"]: bar for bar in daily}
    # 既有已核對的官方值：任何截止日重建都不得改寫歷史。
    require(by_date["2026-11-05"]["close"] == 41574.72, "2026-11-05 收盤不是 41574.72")
    require(by_date["2026-11-05"]["low"] == 41513.57, "2026-11-05 最低不是 41513.57")
    require(by_date["2026-11-04"]["low"] == 41855.82, "2026-11-04 最低不是 41855.82")
    return daily, index_files + turnover_files


def aggregate_bars(
    daily: list[dict[str, Any]],
    key_fn: Callable[[date], str],
    label: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for bar in daily:
        grouped[key_fn(date.fromisoformat(bar["date"]))].append(bar)

    result: list[dict[str, Any]] = []
    for period in sorted(grouped):
        rows = grouped[period]
        result.append(
            {
                "period": period,
                "date": rows[-1]["date"],
                "start": rows[0]["date"],
                "end": rows[-1]["date"],
                "open": rows[0]["open"],
                "high": max(row["high"] for row in rows),
                "low": min(row["low"] for row in rows),
                "close": rows[-1]["close"],
                "amount": sum(int(row["amount"]) for row in rows),
                "sessions": len(rows),
            }
        )
    assert_ohlc(result, label)
    return result


def weekly_key(value: date) -> str:
    iso_year, iso_week, _ = value.isocalendar()
    return f"{iso_year:04d}-W{iso_week:02d}"


def monthly_key(value: date) -> str:
    return value.strftime("%Y-%m")


def load_h60(daily: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[Path]]:
    paths = sorted((SNAPSHOT_ROOT / "intraday-5s").glob("taiex-*.json"))
    require(len(paths) == 30, f"每5秒指數原件應為 30 日，實際 {len(paths)}")
    daily_by_date = {bar["date"]: bar for bar in daily}
    daily_dates = [bar["date"] for bar in daily]
    previous_close: dict[str, float] = {
        daily_dates[index]: float(daily_by_date[daily_dates[index - 1]]["close"])
        for index in range(1, len(daily_dates))
    }

    result: list[dict[str, Any]] = []
    for path in paths:
        iso_date = parse_compact_date_from_name(path)
        wrapper = load_wrapper(path)
        payload = wrapper["data"]
        require(payload.get("stat") == "OK", f"每5秒指數 stat 非 OK：{path}")
        raw_rows = payload.get("data", [])
        require(len(raw_rows) >= 3000, f"每5秒指數筆數過少：{iso_date} {len(raw_rows)}")

        ticks: list[tuple[clock_time, float]] = []
        for row in raw_rows:
            if len(row) < 2 or not row[1] or row[1] == "--":
                continue
            timestamp = datetime.strptime(row[0], "%H:%M:%S").time()
            ticks.append((timestamp, number(row[1])))
        require(ticks == sorted(ticks, key=lambda item: item[0]), f"每5秒指數時間未遞增：{iso_date}")
        require(len({item[0] for item in ticks}) == len(ticks), f"每5秒指數時間重複：{iso_date}")

        if (
            ticks
            and ticks[0][0] == clock_time(9, 0)
            and iso_date in previous_close
            and abs(ticks[0][1] - previous_close[iso_date]) < 0.011
        ):
            ticks = ticks[1:]
        require(ticks and ticks[0][0] >= clock_time(9, 0, 5), f"盤初參考點未正確移除：{iso_date}")

        day_bars: list[dict[str, Any]] = []
        for hour in (9, 10, 11, 12, 13):
            start = clock_time(hour, 0)
            end = clock_time(13, 30) if hour == 13 else clock_time(hour + 1, 0)
            values = [value for tick_time, value in ticks if tick_time >= start and (tick_time <= end if hour == 13 else tick_time < end)]
            require(bool(values), f"60分K缺區間：{iso_date} {hour:02d}:00")
            day_bars.append(
                {
                    "timestamp": f"{iso_date}T{hour:02d}:00:00+08:00",
                    "date": iso_date,
                    "time": f"{hour:02d}:00",
                    "open": values[0],
                    "high": max(values),
                    "low": min(values),
                    "close": values[-1],
                    "ticks": len(values),
                }
            )
        require(len(day_bars) == 5, f"60分K每日不是5根：{iso_date}")
        require(abs(day_bars[-1]["close"] - daily_by_date[iso_date]["close"]) < 0.011, f"60分K收盤與日K不一致：{iso_date}")
        result.extend(day_bars)

    assert_ohlc(result, "60分K")
    require(len(result) == 150, f"60分K應為150根，實際 {len(result)}")
    return result, paths


def moving_averages(values: list[float], window: int) -> list[float | None]:
    result: list[float | None] = []
    rolling = 0.0
    for index, value in enumerate(values):
        rolling += value
        if index >= window:
            rolling -= values[index - window]
        result.append(round(rolling / window, 4) if index + 1 >= window else None)
    return result


def ema(values: list[float], period: int) -> list[float]:
    require(bool(values), "EMA 沒有輸入")
    alpha = 2.0 / (period + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1.0 - alpha) * result[-1])
    return result


def build_indicators(bars: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    closes = [float(bar["close"]) for bar in bars]
    ma_values = {window: moving_averages(closes, window) for window in (5, 10, 20, 60)}
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [fast - slow for fast, slow in zip(ema12, ema26)]
    dea = ema(dif, 9)

    result: list[dict[str, Any]] = []
    for index, bar in enumerate(bars):
        result.append(
            {
                key: bar[key],
                "ma5": ma_values[5][index],
                "ma10": ma_values[10][index],
                "ma20": ma_values[20][index],
                "ma60": ma_values[60][index],
                "dif": round(dif[index], 6),
                "dea": round(dea[index], 6),
                "hist": round(dif[index] - dea[index], 6),
            }
        )
    require(len(result) == len(bars), f"指標筆數與K線不一致：{key}")
    return result


def load_institutional() -> tuple[list[dict[str, Any]], list[Path]]:
    paths = sorted((SNAPSHOT_ROOT / "institutional").glob("bfi82u-*.json"))
    require(len(paths) == 120, f"三大法人原件應為120日，實際 {len(paths)}")
    result: list[dict[str, Any]] = []
    for path in paths:
        iso_date = parse_compact_date_from_name(path)
        payload = load_wrapper(path)["data"]
        require(payload.get("stat") == "OK", f"三大法人 stat 非 OK：{path}")
        rows = {row[0]: row for row in payload.get("data", [])}
        required_names = {
            "外資及陸資(不含外資自營商)",
            "投信",
            "自營商(自行買賣)",
            "自營商(避險)",
        }
        require(required_names.issubset(rows), f"三大法人缺欄位：{iso_date} {required_names - rows.keys()}")

        def amounts(name: str) -> dict[str, int]:
            row = rows[name]
            return {"buy": integer(row[1]), "sell": integer(row[2]), "net": integer(row[3])}

        foreign = amounts("外資及陸資(不含外資自營商)")
        trust = amounts("投信")
        dealer_self = amounts("自營商(自行買賣)")
        dealer_hedge = amounts("自營商(避險)")
        dealer = {key: dealer_self[key] + dealer_hedge[key] for key in ("buy", "sell", "net")}
        result.append({"date": iso_date, "foreign": foreign, "trust": trust, "dealer": dealer})

    require([row["date"] for row in result] == sorted(row["date"] for row in result), "三大法人日期未遞增")
    require(len({row["date"] for row in result}) == len(result), "三大法人日期重複")
    by_date = {row["date"]: row for row in result}
    require(by_date["2026-11-05"]["foreign"]["net"] == -102460621838, "2026-11-05 外資淨額不符斷言")
    require(result[-1]["date"] == CUTOFF.isoformat(), f"三大法人截止日錯誤：{result[-1]['date']}")
    return result, paths


def load_sectors() -> tuple[list[dict[str, Any]], list[Path]]:
    paths = sorted((SNAPSHOT_ROOT / "sectors").glob("mi-index-ind-*.json"))
    require(len(paths) == 60, f"類股原件應為60日，實際 {len(paths)}")
    result: list[dict[str, Any]] = []
    for path in paths:
        iso_date = parse_compact_date_from_name(path)
        payload = load_wrapper(path)["data"]
        tables = payload.get("tables", [])
        require(bool(tables), f"類股缺 tables：{iso_date}")
        rows = {row[0]: row for row in tables[0].get("data", [])}
        require("電子工業類指數" in rows, f"缺電子工業類指數：{iso_date}")
        require("金融保險類指數" in rows, f"缺金融保險類指數：{iso_date}")
        electronic = rows["電子工業類指數"]
        financial = rows["金融保險類指數"]
        result.append(
            {
                "date": iso_date,
                "electronic_close": number(electronic[1]),
                "electronic_change_pct": number(electronic[4]),
                "financial_close": number(financial[1]),
                "financial_change_pct": number(financial[4]),
            }
        )
    require([row["date"] for row in result] == sorted(row["date"] for row in result), "類股日期未遞增")
    require(len({row["date"] for row in result}) == len(result), "類股日期重複")
    require(result[-1]["date"] == CUTOFF.isoformat(), f"類股截止日錯誤：{result[-1]['date']}")
    return result, paths


def load_futures() -> tuple[list[dict[str, Any]], list[Path]]:
    csv_paths = sorted((SNAPSHOT_ROOT / "futures").glob("taifex-txf-*.csv"))
    require(len(csv_paths) == 1, f"期交所TXF CSV應為1份，實際 {len(csv_paths)}")
    csv_path = csv_paths[0]
    meta_path = csv_path.with_suffix(".meta.json")
    require(meta_path.exists(), f"期交所TXF缺 meta：{meta_path}")
    text = csv_path.read_bytes().decode("cp950")
    rows = list(csv.DictReader(io.StringIO(text)))
    result: list[dict[str, Any]] = []
    for row in rows:
        if row.get("商品名稱") != "臺股期貨" or row.get("身份別") not in {"外資", "外資及陸資"}:
            continue
        iso_date = row["日期"].replace("/", "-")
        result.append(
            {
                "date": iso_date,
                "foreign_oi_long_contracts": integer(row["多方未平倉口數"]),
                "foreign_oi_short_contracts": integer(row["空方未平倉口數"]),
                "foreign_oi_net_contracts": integer(row["多空未平倉口數淨額"]),
                "foreign_oi_long_amount_thousand_twd": integer(row["多方未平倉契約金額(千元)"]),
                "foreign_oi_short_amount_thousand_twd": integer(row["空方未平倉契約金額(千元)"]),
                "foreign_oi_net_amount_thousand_twd": integer(row["多空未平倉契約金額淨額(千元)"]),
            }
        )
    result.sort(key=lambda row: row["date"])
    require(len(result) >= 120, f"期交所TXF交易日不足120：{len(result)}")
    require(len({row["date"] for row in result}) == len(result), "期交所TXF日期重複")
    require(result[-1]["date"] == CUTOFF.isoformat(), f"期交所TXF截止日錯誤：{result[-1]['date']}")
    return result, [csv_path, meta_path]


def sample_label(count: int) -> str:
    return "樣本少，不可靠" if count < 30 else "樣本數至少30"


def return_summary(values: Iterable[float]) -> dict[str, Any]:
    data = list(values)
    if not data:
        return {
            "sample_count": 0,
            "average_pct": None,
            "median_pct": None,
            "positive_pct": None,
            "reliability": sample_label(0),
        }
    return {
        "sample_count": len(data),
        "average_pct": round(statistics.fmean(data) * 100, 4),
        "median_pct": round(statistics.median(data) * 100, 4),
        "positive_pct": round(sum(value > 0 for value in data) / len(data) * 100, 2),
        "reliability": sample_label(len(data)),
    }


def day_lines(daily: list[dict[str, Any]], index: int) -> tuple[float, float]:
    bar, previous = daily[index], daily[index - 1]
    if bar["close"] < previous["low"]:
        upper = min(previous["low"], bar["high"])
    else:
        upper = bar["high"]
    return upper, bar["low"]


def atr(daily: list[dict[str, Any]], index: int, window: int = 20) -> float | None:
    if index < window:
        return None
    ranges = []
    for i in range(index - window + 1, index + 1):
        bar, previous = daily[i], daily[i - 1]
        ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - previous["close"]), abs(bar["low"] - previous["close"])))
    return sum(ranges) / window


def day_features(daily: list[dict[str, Any]], index: int, foreign_net: int | None) -> dict[str, Any]:
    bar, previous = daily[index], daily[index - 1]
    span = bar["high"] - bar["low"]
    return {
        "break_prior_low": bar["close"] < previous["low"],
        "drop_pct": round((bar["close"] / previous["close"] - 1.0) * 100, 4),
        "gap_down_below_prior_low": bar["open"] < previous["low"],
        "close_position": round((bar["close"] - bar["low"]) / span, 4) if span else None,
        "lower_shadow_ratio": round((min(bar["open"], bar["close"]) - bar["low"]) / span, 4) if span else None,
        "close_within_0_3pct_of_low": (bar["close"] - bar["low"]) / bar["low"] < 0.003,
        "foreign_net": foreign_net,
    }


SAMPLES = [
    ("all_events", "全部跌破前日低", lambda f: f["break_prior_low"]),
    ("event_and_close_within_0_3pct_of_low", "跌破且收在低點附近", lambda f: f["break_prior_low"] and f["close_within_0_3pct_of_low"]),
    ("event_and_daily_drop_over_3pct", "跌破且單日跌逾3%", lambda f: f["break_prior_low"] and f["drop_pct"] < -3.0),
    ("event_and_long_lower_shadow", "跌破且長下影", lambda f: f["break_prior_low"] and f["lower_shadow_ratio"] is not None and f["lower_shadow_ratio"] >= 0.4),
    ("event_and_foreign_net_sell_over_50b", "跌破且外資賣超逾500億", lambda f: f["break_prior_low"] and f["foreign_net"] is not None and f["foreign_net"] < -50_000_000_000),
    ("not_event", "沒有跌破前日低", lambda f: not f["break_prior_low"]),
]


def event_statistics(daily: list[dict[str, Any]], event_indices: list[int]) -> dict[str, Any]:
    outcome_counts = {"stand_back": 0, "continue_break": 0, "between": 0}
    outcome_sample = 0
    next_returns: list[float] = []
    five_returns: list[float] = []
    years: dict[str, int] = defaultdict(int)
    for index in event_indices:
        event = daily[index]
        years[event["date"][:4]] += 1
        upper, lower = day_lines(daily, index)
        if index + 1 < len(daily):
            next_bar = daily[index + 1]
            outcome_sample += 1
            if next_bar["close"] >= upper:
                outcome_counts["stand_back"] += 1
            elif next_bar["close"] < lower:
                outcome_counts["continue_break"] += 1
            else:
                outcome_counts["between"] += 1
            next_returns.append(next_bar["close"] / event["close"] - 1.0)
        if index + 5 < len(daily):
            five_returns.append(daily[index + 5]["close"] / event["close"] - 1.0)

    outcome_pct = {
        key: (round(value / outcome_sample * 100, 2) if outcome_sample else None)
        for key, value in outcome_counts.items()
    }
    require(sum(outcome_counts.values()) == outcome_sample, "事件隔日分支未互斥完備")
    top_years = sorted(years.items(), key=lambda item: (-item[1], item[0]))[:3]
    return {
        "event_count": len(event_indices),
        "reliability": sample_label(len(event_indices)),
        "next_day_outcomes": {
            "sample_count": outcome_sample,
            "counts": outcome_counts,
            "percent": outcome_pct,
        },
        "next_day_return": return_summary(next_returns),
        "five_day_return": return_summary(five_returns),
        "events_by_year_top": [{"year": year, "count": count} for year, count in top_years],
        "recent_dates": [daily[index]["date"] for index in event_indices[-5:]],
    }


def build_backtest(daily: list[dict[str, Any]], institutional: list[dict[str, Any]]) -> dict[str, Any]:
    require(daily[-1]["date"] == CUTOFF.isoformat(), f"回測截止日不是{CUTOFF.isoformat()}")
    foreign_by_date = {row["date"]: row["foreign"]["net"] for row in institutional}
    features = {index: day_features(daily, index, foreign_by_date.get(daily[index]["date"])) for index in range(1, len(daily))}
    results = {}
    for key, _label, test in SAMPLES:
        indices = [index for index in range(1, len(daily)) if test(features[index])]
        results[key] = event_statistics(daily, indices)

    baseline_next = [daily[index + 1]["close"] / daily[index]["close"] - 1.0 for index in range(len(daily) - 1)]
    baseline_five = [daily[index + 5]["close"] / daily[index]["close"] - 1.0 for index in range(len(daily) - 5)]
    baseline = {"all_trading_days": {"next_day_return": return_summary(baseline_next), "five_day_return": return_summary(baseline_five)}}

    last = len(daily) - 1
    today = features[last]
    matched = [key for key, _label, test in SAMPLES if test(today)]
    if "event_and_daily_drop_over_3pct" in matched:
        default_sample = "event_and_daily_drop_over_3pct"
    elif "event_and_close_within_0_3pct_of_low" in matched:
        default_sample = "event_and_close_within_0_3pct_of_low"
    elif "all_events" in matched:
        default_sample = "all_events"
    else:
        default_sample = "not_event"
    upper, lower = day_lines(daily, last)
    today_atr = atr(daily, last)
    close = daily[last]["close"]

    primary = results[default_sample]
    base_next = baseline["all_trading_days"]["next_day_return"]["median_pct"]
    base_five = baseline["all_trading_days"]["five_day_return"]["median_pct"]
    weak_next = primary["next_day_return"]["median_pct"] is not None and primary["next_day_return"]["median_pct"] < base_next
    weak_five = primary["five_day_return"]["median_pct"] is not None and primary["five_day_return"]["median_pct"] < base_five
    if weak_next and weak_five:
        assessment = "同型態事件後隔日與5日報酬中位數都低於一般交易日，歷史上偏弱，但不是未來保證。"
    elif weak_next or weak_five:
        assessment = "同型態事件後只有部分期間報酬中位數低於一般交易日，歷史證據混合。"
    else:
        assessment = "同型態事件後隔日與5日報酬中位數都不低於一般交易日，歷史上不支持單憑此型態判定續弱。"

    cutoff_compact = CUTOFF.strftime("%Y%m%d")
    last_date = daily[last]["date"]
    return {
        "id": f"{cutoff_compact}-next-day-lines",
        "generated_at": datetime.now().astimezone().isoformat(),
        "data_range": {"start": daily[0]["date"], "end": daily[-1]["date"], "daily_bars": len(daily)},
        "method": {
            "lines": "交易日 t：若收盤 < t-1 最低價，收復線 = min(t-1 最低價, t 最高價)；否則收復線 = t 最高價。防守線 = t 最低價。每一天都用同一規則畫線，不為當天調參。",
            "stand_back": "t+1 收盤 >= 收復線。",
            "continue_break": "t+1 收盤 < 防守線。",
            "between": "防守線 <= t+1 收盤 < 收復線。",
            "next_day_return": "t+1 收盤 / t 收盤 - 1。",
            "five_day_return": "t+5 收盤 / t 收盤 - 1；以交易日計，各事件的5日窗可能重疊。",
            "subset_near_low": "跌破事件且 (收盤-當日最低)/當日最低 < 0.3%。",
            "subset_drop_over_3pct": "跌破事件且收盤相對前一日收盤跌幅嚴格大於3%。",
            "subset_long_lower_shadow": "跌破事件且下影線（min(開,收)-最低）佔當日振幅 >= 40%；門檻事前固定。",
            "subset_foreign_sell_over_50b": "跌破事件且外資及陸資(不含外資自營商)當日淨賣超嚴格超過500億元；法人資料只有最近120個交易日。",
            "default_sample_rule": "截止日符合『跌逾3%』就預設該樣本；否則依序為『收在低點附近』、『全部跌破』、『沒有跌破』。順序事前固定。",
            "parameter_selection": "未挑選參數美化結果；門檻事前固定。",
        },
        "samples": [{"key": key, "label": label} for key, label, _test in SAMPLES],
        "baseline": baseline,
        "results": results,
        "as_of": {
            "date": last_date,
            "close": close,
            "high": daily[last]["high"],
            "low": daily[last]["low"],
            "open": daily[last]["open"],
            "previous_low": daily[last - 1]["low"],
            "previous_close": daily[last - 1]["close"],
            "features": today,
            "matched_samples": matched,
            "default_sample": default_sample,
            "upper": upper,
            "lower": lower,
            "atr20": round(today_atr, 2) if today_atr else None,
            "upper_distance_atr": round((upper - close) / today_atr, 2) if today_atr else None,
            "lower_distance_atr": round((close - lower) / today_atr, 2) if today_atr else None,
            "forward_result_available": False,
            "historical_assessment": assessment,
        },
        "limitations": [
            "這是歷史條件統計，不是因果證明或未來報酬保證。",
            "三區衡量的是隔日收盤跨越哪條價位，不是漲跌方向；防守線離收盤很遠時，『沒有跌破』不代表止穩。",
            "加權指數本身不可直接交易，未納入追蹤誤差、手續費、稅與滑價。",
            last_date + "是資料截止日，尚無隔日與5日事後結果，不得拿未來資料補入本次判斷。",
            "法人資料只有最近120個交易日；樣本少於30標示為樣本少、不可靠，超過30也不保證可靠。",
            "MACD與均線未納入固定事件規則；本回測沒有做參數搜尋，也沒有做時間外驗證。",
        ],
    }


def data_range(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    return {"start": rows[0][key], "end": rows[-1][key], "count": len(rows)}


def collect_source_files(groups: list[list[Path]]) -> list[str]:
    paths = sorted({path.resolve() for group in groups for path in group})
    return [relative(path) for path in paths]


def main() -> None:
    global SNAPSHOT_ROOT, BACKTEST_PATH, CUTOFF
    parser = argparse.ArgumentParser(description="由官方原件建置多週期資料與回測")
    parser.add_argument("--cutoff", help="資料截止交易日 YYYY-MM-DD；預設 2026-11-05")
    parser.add_argument("--snapshot", help="快照資料夾（相對專案根目錄）")
    parser.add_argument("--backtest-out", help="回測輸出檔（相對專案根目錄）；不覆寫舊版")
    parser.add_argument("--output", help="analysis-data.js 輸出路徑（相對專案根目錄）")
    args = parser.parse_args()
    global OUTPUT_PATH
    if args.output:
        OUTPUT_PATH = ROOT / args.output
    if args.cutoff:
        CUTOFF = date.fromisoformat(args.cutoff)
    if args.snapshot:
        SNAPSHOT_ROOT = ROOT / args.snapshot
    if args.backtest_out:
        BACKTEST_PATH = ROOT / args.backtest_out
        require(not BACKTEST_PATH.exists(), f"回測輸出檔已存在，不覆寫：{BACKTEST_PATH}")
    daily, daily_sources = load_daily()
    weekly = aggregate_bars(daily, weekly_key, "週K")
    monthly = aggregate_bars(daily, monthly_key, "月K")
    h60, h60_sources = load_h60(daily)
    institutional, institutional_sources = load_institutional()
    futures, futures_sources = load_futures()
    sectors, sector_sources = load_sectors()

    indicators = {
        "parameters": {
            "moving_averages": [5, 10, 20, 60],
            "macd": {"fast": 12, "slow": 26, "signal": 9, "histogram": "DIF-DEA"},
            "note": "均線與MACD均為通用參數，不是分析師自訂；EMA以各序列第一個收盤值為種子，初期資料視為暖機。",
        },
        "daily": build_indicators(daily, "date"),
        "weekly": build_indicators(weekly, "date"),
        "monthly": build_indicators(monthly, "date"),
        "h60": build_indicators(h60, "timestamp"),
    }

    backtest = build_backtest(daily, institutional)
    BACKTEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    BACKTEST_PATH.write_text(json.dumps(backtest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    sources = collect_source_files(
        [daily_sources, h60_sources, institutional_sources, futures_sources, sector_sources]
    )
    fetch_report = SNAPSHOT_ROOT / "fetch-report.json"
    if fetch_report.exists():
        sources.append(relative(fetch_report))
        sources.sort()
    weekend_sessions = [
        bar["date"] for bar in daily if date.fromisoformat(bar["date"]).weekday() >= 5
    ]
    require(all(date.fromisoformat(value).weekday() == 5 for value in weekend_sessions), "歷史資料出現週日交易日")

    payload = {
        "version": f"market-analysis-{CUTOFF.strftime('%Y%m%d')}-v1",
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(),
            "cutoff_date": CUTOFF.isoformat(),
            "ranges": {
                "daily": data_range(daily, "date"),
                "weekly": data_range(weekly, "date"),
                "monthly": data_range(monthly, "date"),
                "h60": data_range(h60, "timestamp"),
                "institutional": data_range(institutional, "date"),
                "futures": data_range(futures, "date"),
                "sectors": data_range(sectors, "date"),
            },
            "source_files": sources,
            "calendar_exceptions": {
                "weekend_trading_sessions": weekend_sessions,
                "handling": "官方歷史中的週六補班交易日保留，併入其所在ISO週；週K不丟棄真實交易。",
            },
            "source_endpoints": {
                "daily_index": INDEX_URL,
                "turnover": TURNOVER_URL,
                "intraday_index": INTRADAY_INDEX_URL,
                "institutional": INSTITUTIONAL_URL,
                "futures": FUTURES_DOWNLOAD_URL,
                "sectors": SECTOR_URL,
            },
            "source_notes": [
                "使用者指定的 exchangeReport/MI_5MINS 實測為每5秒委託成交統計，不含指數；60分K改用證交所每5秒指數盤後統計 TAIEX/MI_5MINS_INDEX。",
                "60分K將09:00:00的前收參考點排除，使用09:00:05起的實際盤中指數；13:00一根涵蓋至13:30收盤。",
                f"週K以週一為起點的ISO週分組；官方歷史中的{len(weekend_sessions)}個週六補班交易日保留並併入所在週。月K以曆月分組；成交金額為各實際交易日元數合計。",
                "期交所契約金額單位沿官方CSV為千元。",
            ],
        },
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "h60": h60,
        "institutional": institutional,
        "futures": futures,
        "sectors": sectors,
        "indicators": indicators,
        "backtest": backtest,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    serialized = "window.MARKET_ANALYSIS = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    encoded = serialized.encode("utf-8")
    require(len(encoded) <= MAX_OUTPUT_BYTES, f"analysis-data.js 超過3MB：{len(encoded)} bytes")
    OUTPUT_PATH.write_bytes(encoded)

    summary = {
        "daily": data_range(daily, "date"),
        "weekly": data_range(weekly, "date"),
        "monthly": data_range(monthly, "date"),
        "h60": data_range(h60, "timestamp"),
        "institutional": data_range(institutional, "date"),
        "futures": data_range(futures, "date"),
        "sectors": data_range(sectors, "date"),
        "backtest_events": backtest["results"]["all_events"]["event_count"],
        "output_bytes": len(encoded),
        "output": relative(OUTPUT_PATH),
        "backtest": relative(BACKTEST_PATH),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
