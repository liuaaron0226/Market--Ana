"""預測帳本的海外層（D 票第 9 片）：美日韓指數、美債殖利率、美元指數、美元兌台幣，以及外資讀數 1、4。

對外只有四件事：
- ``fetch_sources``：抓原件、原樣存檔，manifest 記抓取時間、雜湊、去掉金鑰的網址；一個來源失敗不影響其他。
- ``build_layer``：依版本（晚間／晨間）的資料截止規則，整理出交給模型的海外層、只存不交的部分、各項狀態。
- ``load_tw_series``：讀 0050 日 K 與上市外資買賣超金額（股數 × 收盤加總），給兩個外資讀數的歷史統計用。
- ``archive_sources``：原件壓縮後以內容雜湊命名放進帳本 ``sources/``，跟著帳本存回 R2（只新增）。

資料截止（不回填當時還不知道的資料，spec〈交給 Gemini 的輸入〉海外層）：
- 美國的資料（指數、美債、美元指數）用美國日期。晚間版在台北晚上寫，當天美股還沒開，只用基準日之前的收盤；
  晨間版在下一個開盤日早上寫，用開盤日之前的收盤（剛收盤那一晚）。
- 日韓與美元兌台幣：兩版都只用到基準日當天（晨間版寫的時候日韓還沒開盤）。

哪些來源可以交給模型，依各家條款（``LICENSES``）：Gemini 目前用的方案會拿輸入改進產品，EODHD 的條款只允許
本人私用，所以 EODHD 的指數數字只存、只給站主看，本人決定之前不交給模型（ANALYSIS_PLAN 2026-11-15
「先做能做的，付費晚點決定」）。官方與公有領域的資料（財政部、聯準會、央行、證交所）可以交。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = "overseas-sources/v1"
EODHD_ENDPOINT = "https://eodhd.com/api/eod/{symbol}"
TREASURY_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
                "?data=daily_treasury_yield_curve&field_tdr_date_value_month={month}")
FED_BROAD_URL = "https://www.federalreserve.gov/releases/h10/summary/jrxwtfb_nb.htm"
CBC_USDTWD_URL = "https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day"

# （key, 名稱, EODHD 代號, 收盤日期的時區：us＝美國日期、asia＝與台股同一天）
MARKETS: tuple[tuple[str, str, str, str], ...] = (
    ("us_sp500", "美國標普 500", "GSPC.INDX", "us"),
    ("us_nasdaq", "美國那斯達克綜合", "IXIC.INDX", "us"),
    ("japan_nikkei", "日本日經 225", "N225.INDX", "asia"),
    ("korea_kospi", "韓國綜合", "KS11.INDX", "asia"),
)
LICENSES: dict[str, dict[str, Any]] = {
    "eodhd": {"in_prompt": False, "credit": "EODHD",
              "terms": "個人使用：可私人儲存與分析，不得讓他人取得；Gemini 目前方案會拿輸入改進產品，本人決定前不交給模型"},
    "treasury": {"in_prompt": True, "credit": "美國財政部 Daily Treasury Par Yield Curve Rates",
                 "terms": "美國聯邦政府資料；catalog.data.gov 標 CC0，財政部網站未另寫條款"},
    "fed_h10": {"in_prompt": True, "credit": "美國聯準會 H.10 Nominal Broad Dollar Index",
                "terms": "聯準會網站內容屬公有領域，須註明出處（federalreserve.gov/disclaimer.htm）"},
    "cbc": {"in_prompt": True, "credit": "中央銀行 新臺幣對美元銀行間收盤匯率",
            "terms": "政府資料開放授權條款第 1 版，須註明出處"},
}
# 外資讀數 1 的五個區間（那斯達克漲跌 %）。
NASDAQ_BUCKETS: tuple[tuple[str, Callable[[float], bool]], ...] = (
    ("跌逾 1.5%", lambda c: c < -1.5),
    ("跌 0.5～1.5%", lambda c: -1.5 <= c < -0.5),
    ("±0.5% 內", lambda c: -0.5 <= c <= 0.5),
    ("漲 0.5～1.5%", lambda c: 0.5 < c <= 1.5),
    ("漲逾 1.5%", lambda c: c > 1.5),
)
# 0050 開盤跳空超過漲跌幅上限（10%）就是分割之類的事件，不算進統計。
MAX_GAP = 0.11
RECENT_SESSIONS = 5
CHANGE_WINDOW = 20
TREASURY_MONTHS = 3
EODHD_HISTORY_DAYS = 400  # 免費方案只給約一年，多要也拿不到
SMALL_SAMPLE = 10

# 參數整組算進預測帳本的輸入版本：改了就是新系列。
PARAMETERS: dict[str, Any] = {
    "markets": [symbol for _, _, symbol, _ in MARKETS],
    "prompt_sources": sorted(name for name, item in LICENSES.items() if item["in_prompt"]),
    "recent_sessions": RECENT_SESSIONS,
    "change_window": CHANGE_WINDOW,
    "nasdaq_buckets": [name for name, _ in NASDAQ_BUCKETS],
    "max_gap": MAX_GAP,
    "cutoff_rule": "美國日期：晚間版早於基準日、晨間版早於開盤日；日韓與美元兌台幣：到基準日為止",
}

NOTES = {
    "foreign": "外資的買賣解釋當天的漲跌，不代表之後會續漲或續跌。",
    "nasdaq": "開盤跳空與盤中漲跌都用 0050 的實際開盤價計算；統計只描述歷史平均，不是對下一個交易日的預告。",
    "dollar": "常態是美元走強、台幣走貶時外資偏向賣超（長期相關為負）；除息季（約七到九月）兩者常不同調。",
    "status": "status 不是 ok 的項目代表休市、沒抓到或不是預期那天的收盤，不得當成最新行情。",
}


# ---- 抓原件 ----

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_fetch() -> Callable[..., bytes]:
    from level_signals import HostPacer  # 同一 host 相鄰請求間隔一秒；錯誤直接拋出、不重試
    return HostPacer(1.0).fetch


def _months(reference: date, count: int) -> list[str]:
    year, month, out = reference.year, reference.month, []
    for _ in range(count):
        out.append(f"{year:04d}{month:02d}")
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return out


def _error_type(error: BaseException) -> str:
    # 不留例外訊息：網址裡有金鑰。
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    return type(error).__name__


def fetch_sources(out_dir: Path | str, *, reference_date: str, eodhd_token: str,
                  fetch: Callable[..., bytes] | None = None, now: Callable[[], str] | None = None) -> dict[str, Any]:
    """抓所有海外來源到 ``out_dir``：``<名稱>.raw`` 原件＋``manifest.json``。

    ``reference_date``：晚間版是基準日，晨間版是開盤日。沒有 EODHD 金鑰時不打 EODHD，記成 missing_key。
    """
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    fetch = fetch or _default_fetch()
    clock = now or _utc_now
    reference = date.fromisoformat(reference_date)
    start = (reference - timedelta(days=EODHD_HISTORY_DAYS)).isoformat()
    jobs: list[tuple[str, str, str | None, str]] = []
    for _, _, symbol, _ in MARKETS:
        base = EODHD_ENDPOINT.format(symbol=symbol)
        shown = f"{base}?api_token=REDACTED&fmt=json&period=d&from={start}&to={reference_date}"
        url = None if not eodhd_token else base + "?" + urllib.parse.urlencode(
            {"api_token": eodhd_token, "fmt": "json", "period": "d", "from": start, "to": reference_date})
        jobs.append((f"eodhd-{symbol}", "eodhd", url, shown))
    for month in _months(reference, TREASURY_MONTHS):
        url = TREASURY_URL.format(month=month)
        jobs.append((f"treasury-{month}", "treasury", url, url))
    jobs.append(("fed-h10-broad", "fed_h10", FED_BROAD_URL, FED_BROAD_URL))
    jobs.append(("cbc-usdtwd", "cbc", CBC_USDTWD_URL, CBC_USDTWD_URL))

    entries = []
    for name, source, url, shown in jobs:
        entry: dict[str, Any] = {"name": name, "source": source, "url": shown}
        if url is None:
            entries.append({**entry, "status": "error", "error_type": "missing_key"})
            continue
        try:
            content = fetch(url, timeout=60)
            if not content:
                raise ValueError("empty body")
        except Exception as error:  # noqa: BLE001 — 任何一個來源失敗都只記下來，不擋其他來源
            entries.append({**entry, "status": "error", "error_type": _error_type(error), "fetched_at": clock()})
            continue
        (folder / f"{name}.raw").write_bytes(content)
        entries.append({**entry, "status": "ok", "fetched_at": clock(), "bytes": len(content),
                        "sha256": hashlib.sha256(content).hexdigest()})
    manifest = {"schema": SCHEMA, "reference_date": reference_date, "sources": entries}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _read_manifest(folder: Path | None) -> list[dict[str, Any]]:
    if folder is None or not (folder / "manifest.json").exists():
        return []
    return list(json.loads((folder / "manifest.json").read_text(encoding="utf-8")).get("sources") or [])


def _raw(folder: Path, entry: Mapping[str, Any]) -> bytes | None:
    """manifest 說 ok 而且雜湊對得上的原件；對不上就當作沒有。"""
    if entry.get("status") != "ok":
        return None
    path = folder / f"{entry['name']}.raw"
    if not path.exists():
        return None
    content = path.read_bytes()
    return content if hashlib.sha256(content).hexdigest() == entry.get("sha256") else None


# ---- 解析 ----

def parse_eodhd(content: bytes) -> list[dict[str, Any]]:
    rows = json.loads(content.decode("utf-8"))
    if not isinstance(rows, list):
        raise ValueError("EODHD 回應不是日線清單")
    out = {}
    for row in rows:
        day, close = str(row.get("date") or ""), row.get("close")
        if isinstance(close, (int, float)) and math.isfinite(close) and close > 0:
            out[date.fromisoformat(day).isoformat()] = float(close)
    return [{"date": day, "close": close} for day, close in sorted(out.items())]


_ATOM_D = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"


def parse_treasury(content: bytes) -> list[dict[str, Any]]:
    """財政部 par yield curve XML：每一筆的 NEW_DATE、BC_2YEAR、BC_10YEAR（沒有值的欄位不會出現）。"""
    root = ET.fromstring(content)
    out = []
    for props in root.iter("{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties"):
        stamp = props.findtext(f"{_ATOM_D}NEW_DATE")
        if not stamp:
            continue

        def number(tag: str) -> float | None:
            text = props.findtext(f"{_ATOM_D}{tag}")
            try:
                return float(text) if text not in (None, "") else None
            except ValueError:
                return None
        out.append({"date": stamp[:10], "y2": number("BC_2YEAR"), "y10": number("BC_10YEAR")})
    return out


_FED_ROW = re.compile(r"<th[^>]*>\s*(\d{1,2}-[A-Z]{3}-\d{2})\s*</th>\s*<td[^>]*>\s*([0-9.]+|ND)\s*</td>")


def parse_fed_broad(content: bytes) -> list[dict[str, Any]]:
    """聯準會 H.10 廣義美元指數日資料頁；ND（沒有資料）略過。"""
    out = []
    for stamp, value in _FED_ROW.findall(content.decode("utf-8", "replace")):
        if value == "ND":
            continue
        day = datetime.strptime(stamp.title(), "%d-%b-%y").date().isoformat()
        out.append({"date": day, "value": float(value)})
    return sorted({row["date"]: row for row in out}.values(), key=lambda row: row["date"])


# ---- 小工具 ----

def _pct(value: float) -> float:
    return round(value * 100, 2)


def _last_weekday_before(day: str) -> str:
    current = date.fromisoformat(day) - timedelta(days=1)
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.isoformat()


def _change(values: Sequence[float], window: int) -> float | None:
    if len(values) <= window or values[-1 - window] == 0:
        return None
    return values[-1] / values[-1 - window] - 1


def _correlation(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    return round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy), 4)


def _as_of(rows: Sequence[Mapping[str, Any]], day: str, *, strict: bool, field: str) -> float | None:
    """最後一筆日期在 day 之前（strict）或當天以前的值。rows 已依日期排序。"""
    value = None
    for row in rows:
        if row["date"] < day or (not strict and row["date"] == day):
            value = row[field]
        else:
            break
    return value


# ---- 各項 ----

def _market_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closes = [row["close"] for row in rows]
    recent = rows[-(RECENT_SESSIONS + 1):]
    change_5d, change_20d = _change(closes, RECENT_SESSIONS), _change(closes, CHANGE_WINDOW)
    return {
        "latest_date": rows[-1]["date"], "latest_close": rows[-1]["close"],
        "recent": [{"date": b["date"], "close": b["close"], "change_pct": _pct(b["close"] / a["close"] - 1)}
                   for a, b in zip(recent, recent[1:])],
        "change_5d_pct": None if change_5d is None else _pct(change_5d),
        "change_20d_pct": None if change_20d is None else _pct(change_20d),
    }


def _treasury_block(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def change(field: str) -> float | None:
        values = [row[field] for row in rows if row[field] is not None]
        if len(values) <= CHANGE_WINDOW:
            return None
        return round((values[-1] - values[-1 - CHANGE_WINDOW]) * 100, 1)
    last = rows[-1]
    return {"date": last["date"], "yield_2y": last["y2"], "yield_10y": last["y10"], "unit": "%",
            "change_20d_bp": {"2y": change("y2"), "10y": change("y10")}}


def _direction(value: float | None, up: str = "上升", down: str = "下降") -> str | None:
    if value is None:
        return None
    return up if value > 0 else down if value < 0 else "持平"


def _dollar_reading(fed: list[dict[str, Any]], cbc: list[dict[str, Any]], tw: Mapping[str, Any] | None,
                    base_date: str) -> dict[str, Any]:
    """外資讀數 4：美元指數與美元兌台幣近 20 個交易日的方向，和外資近 20 日累計比；附長期相關。"""
    foreign = {day: value for day, value in ((tw or {}).get("foreign_net") or {}).items() if day <= base_date}
    days = sorted(foreign)
    f20 = sum(foreign[day] for day in days[-CHANGE_WINDOW:]) if len(days) >= CHANGE_WINDOW else None
    d20 = _change([row["value"] for row in fed], CHANGE_WINDOW)
    t20 = _change([row["value"] for row in cbc], CHANGE_WINDOW)

    def usual(change: float | None) -> bool | None:
        # 常態：美元（或美元兌台幣）上升時外資賣超、下降時買超。
        if change is None or f20 is None or change == 0 or f20 == 0:
            return None
        return (change > 0) == (f20 < 0)

    xs_dollar, xs_twd, ys, used = [], [], [], []
    for index in range(CHANGE_WINDOW, len(days)):  # 要有 20 個交易日之前的那一天才算得出 20 日變化
        day, earlier = days[index], days[index - CHANGE_WINDOW]
        total = sum(foreign[d] for d in days[index - CHANGE_WINDOW + 1:index + 1])
        now_d, then_d = _as_of(fed, day, strict=True, field="value"), _as_of(fed, earlier, strict=True, field="value")
        now_t, then_t = _as_of(cbc, day, strict=False, field="value"), _as_of(cbc, earlier, strict=False, field="value")
        if None in (now_d, then_d, now_t, then_t) or 0 in (then_d, then_t):
            continue
        xs_dollar.append(now_d / then_d - 1)
        xs_twd.append(now_t / then_t - 1)
        ys.append(total)
        used.append(day)
    return {
        "dollar_index": {"date": fed[-1]["date"] if fed else None, "value": fed[-1]["value"] if fed else None,
                         "change_20d_pct": None if d20 is None else _pct(d20), "direction": _direction(d20),
                         "matches_usual_pattern": usual(d20)},
        "usdtwd": {"date": cbc[-1]["date"] if cbc else None, "value": cbc[-1]["value"] if cbc else None,
                   "change_20d_pct": None if t20 is None else _pct(t20), "direction": _direction(t20),
                   "matches_usual_pattern": usual(t20)},
        "foreign_20d": {"date": days[-1] if days else None,
                        "total_billion": None if f20 is None else round(f20 / 1e8, 2),
                        "direction": _direction(f20, "買超", "賣超")},
        "history": {"correlation_dollar": _correlation(xs_dollar, ys), "correlation_usdtwd": _correlation(xs_twd, ys),
                    "samples": len(used), "from": used[0] if used else None, "to": used[-1] if used else None,
                    "small_sample": len(used) < SMALL_SAMPLE * 3},
        "notes": [NOTES["dollar"], NOTES["foreign"]],
    }


def _bucket(change_pct: float) -> str:
    return next(name for name, test in NASDAQ_BUCKETS if test(change_pct))


def _nasdaq_reading(rows: list[dict[str, Any]], tw: Mapping[str, Any] | None, base_date: str) -> dict[str, Any] | None:
    """外資讀數 1：最近一個美股交易日的那斯達克漲跌，以及歷史上同一區間隔天的外資與 0050 表現。"""
    if len(rows) < 2:
        return None
    changes = {b["date"]: _pct(b["close"] / a["close"] - 1) for a, b in zip(rows, rows[1:])}
    us_days = sorted(changes)
    etf = [row for row in ((tw or {}).get("etf0050") or []) if row["date"] <= base_date]
    foreign = (tw or {}).get("foreign_net") or {}
    samples: dict[str, list[tuple[bool, float, float]]] = {name: [] for name, _ in NASDAQ_BUCKETS}
    used = []
    for previous, current in zip(etf, etf[1:]):
        day = current["date"]
        prior_us = [d for d in us_days if d < day]
        # 前一個台股交易日收盤之後要有新的美股收盤，才算「前一晚」（美國休市的那幾天跳過）。
        if not prior_us or prior_us[-1] < previous["date"] or day not in foreign:
            continue
        if not previous["close"] or not current["open"]:
            continue
        gap = current["open"] / previous["close"] - 1
        if abs(gap) > MAX_GAP:
            continue
        samples[_bucket(changes[prior_us[-1]])].append((foreign[day] < 0, gap, current["close"] / current["open"] - 1))
        used.append(day)

    def summary(name: str) -> dict[str, Any]:
        items = samples[name]
        if not items:
            return {"bucket": name, "samples": 0, "foreign_sell_rate": None, "mean_open_gap_pct": None,
                    "mean_intraday_pct": None}
        n = len(items)
        return {"bucket": name, "samples": n, "foreign_sell_rate": round(sum(s for s, _, _ in items) / n, 4),
                "mean_open_gap_pct": _pct(sum(g for _, g, _ in items) / n),
                "mean_intraday_pct": _pct(sum(i for _, _, i in items) / n)}

    latest = us_days[-1]
    return {
        "date": latest, "change_pct": changes[latest], "bucket": _bucket(changes[latest]),
        "history": {"buckets": [summary(name) for name, _ in NASDAQ_BUCKETS], "from": used[0] if used else None,
                    "to": used[-1] if used else None, "small_sample_below": SMALL_SAMPLE},
        "notes": [NOTES["nasdaq"], NOTES["foreign"]],
    }


# ---- 組成海外層 ----

def _status(name: str, rows: list[dict[str, Any]] | None, expected: str | None, in_prompt: bool) -> dict[str, Any]:
    """expected 是預期的最新資料日；None 表示沒有固定的預期日（美元指數每週發布一次，另外判斷）。"""
    if not rows:
        return {"name": name, "status": "missing", "latest_date": None, "expected_date": expected,
                "in_prompt": in_prompt}
    latest = rows[-1]["date"]
    state = "ok" if expected is None or latest >= expected else "stale"
    return {"name": name, "status": state, "latest_date": latest, "expected_date": expected, "in_prompt": in_prompt}


def build_layer(source_dir: Path | str | None, *, edition: str, base_date: str, session_date: str | None = None,
                tw: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """整理海外層。回傳：
    - ``prompt``：交給模型的部分（只含條款允許的來源）；
    - ``withheld``：抓到了、但條款不允許交給模型的部分（只存在帳本原件）；
    - ``status``：每一項的狀態（ok／stale／missing）、資料日期、是否交給模型；
    - ``cutoffs``：交給模型的各項資料日期；``missing``：缺了哪幾項；``sources``：原件清單。
    """
    if edition not in ("evening", "morning"):
        raise ValueError("edition 必須是 evening 或 morning")
    if edition == "morning" and not session_date:
        raise ValueError("晨間版要給開盤日 session_date")
    us_cutoff = session_date if edition == "morning" else base_date
    folder = Path(source_dir) if source_dir is not None else None
    entries = _read_manifest(folder)
    by_name = {entry["name"]: entry for entry in entries}

    def parsed(name: str, parser: Callable[[bytes], list[dict[str, Any]]]) -> list[dict[str, Any]] | None:
        entry = by_name.get(name)
        content = _raw(folder, entry) if folder is not None and entry else None
        if content is None:
            return None
        try:
            return parser(content)
        except (ValueError, KeyError, TypeError, ET.ParseError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    status: dict[str, dict[str, Any]] = {}
    markets: dict[str, dict[str, Any]] = {}
    nasdaq_rows: list[dict[str, Any]] = []
    eodhd_ok = LICENSES["eodhd"]["in_prompt"]
    for key, name, symbol, zone in MARKETS:
        rows = parsed(f"eodhd-{symbol}", parse_eodhd)
        if rows is not None:
            rows = [row for row in rows if (row["date"] < us_cutoff if zone == "us" else row["date"] <= base_date)]
        expected = _last_weekday_before(us_cutoff) if zone == "us" else base_date
        status[key] = _status(name, rows, expected, eodhd_ok)
        if rows:
            markets[key] = {"name": name, "symbol": symbol, **_market_block(rows), "status": status[key]["status"]}
        if key == "us_nasdaq" and rows:
            nasdaq_rows = rows

    treasury_rows: dict[str, dict[str, Any]] = {}
    for month in _months(date.fromisoformat(us_cutoff), TREASURY_MONTHS):
        for row in parsed(f"treasury-{month}", parse_treasury) or []:
            if row["date"] < us_cutoff:
                treasury_rows[row["date"]] = row
    treasury = [treasury_rows[day] for day in sorted(treasury_rows)]
    status["treasury"] = _status("美債殖利率", treasury, _last_weekday_before(us_cutoff),
                                 LICENSES["treasury"]["in_prompt"])
    fed = [row for row in parsed("fed-h10-broad", parse_fed_broad) or [] if row["date"] < us_cutoff]
    status["dollar_index"] = _status("美元指數（聯準會廣義）", fed, None, LICENSES["fed_h10"]["in_prompt"])
    if fed and date.fromisoformat(us_cutoff) - date.fromisoformat(fed[-1]["date"]) > timedelta(days=14):
        status["dollar_index"]["status"] = "stale"
    from level_signals import cbc_usdtwd_rows
    cbc_entry = by_name.get("cbc-usdtwd")
    cbc_raw = _raw(folder, cbc_entry) if folder is not None and cbc_entry else None
    try:
        cbc = [{"date": d, "value": v} for d, v in cbc_usdtwd_rows(cbc_raw, base_date)] if cbc_raw else []
    except (ValueError, ET.ParseError, json.JSONDecodeError, UnicodeDecodeError):
        cbc = []
    status["usdtwd"] = _status("美元兌台幣", cbc, base_date, LICENSES["cbc"]["in_prompt"])

    prompt: dict[str, Any] = {"notes": [NOTES["status"], NOTES["foreign"]]}
    withheld: dict[str, Any] = {}
    (prompt if eodhd_ok else withheld)["markets"] = markets
    nasdaq = _nasdaq_reading(nasdaq_rows, tw, base_date)
    (prompt if eodhd_ok else withheld)["nasdaq_reading"] = nasdaq
    if not eodhd_ok:
        prompt["not_provided"] = [name for _, name, _, _ in MARKETS] + ["外資讀數一（那斯達克）"]
        if not withheld["markets"] and nasdaq is None:
            withheld = {}
    prompt["treasury"] = {**_treasury_block(treasury), "status": status["treasury"]["status"]} if treasury else None
    if fed or cbc:
        prompt["dollar_and_foreign"] = _dollar_reading(fed, cbc, tw, base_date)
    else:
        prompt["dollar_and_foreign"] = None

    cutoffs = {key: item["latest_date"] for key, item in status.items() if item["in_prompt"] and item["latest_date"]}
    missing = [{"key": key, "name": item["name"], "status": item["status"],
                "reason": "沒抓到或格式不對" if item["status"] == "missing" else "不是預期那天的收盤（可能休市或尚未更新）"}
               for key, item in status.items() if item["status"] != "ok"]
    sources = [{key: entry[key] for key in ("name", "source", "url", "status", "fetched_at", "sha256", "bytes",
                                            "error_type") if key in entry} for entry in entries]
    return {"prompt": prompt, "withheld": withheld, "status": status, "cutoffs": cutoffs, "missing": missing,
            "sources": sources}


# ---- 台股這邊的序列 ----

def load_tw_series(stock_db: Path | str | None, institutional_db: Path | str | None, *,
                   until: str) -> dict[str, Any] | None:
    """0050 的開收，與每天上市外資買賣超金額（股數 × 收盤，元）。檔案不在就回 None（讀數照寫、統計標缺）。"""
    if not stock_db or not institutional_db or not Path(stock_db).exists() or not Path(institutional_db).exists():
        return None
    con = sqlite3.connect(f"file:{Path(stock_db).as_posix()}?mode=ro", uri=True)
    try:
        con.execute("ATTACH DATABASE ? AS inst", (f"file:{Path(institutional_db).as_posix()}?mode=ro",))
        etf = [{"date": d, "open": o, "close": c} for d, o, c in con.execute(
            "SELECT date, open, close FROM daily WHERE code = '0050' AND date <= ? AND open IS NOT NULL "
            "AND close IS NOT NULL ORDER BY date", (until,))]
        foreign = {d: float(total) for d, total in con.execute(
            "SELECT i.date, SUM(i.foreign_net * d.close) FROM inst.institutional i "
            "JOIN daily d ON d.date = i.date AND d.code = i.code "
            "WHERE i.date <= ? AND d.close IS NOT NULL AND i.foreign_net IS NOT NULL GROUP BY i.date ORDER BY i.date",
            (until,))}
    finally:
        con.close()
    return {"etf0050": etf, "foreign_net": foreign}


# ---- 原件進帳本 ----

def archive_sources(source_dir: Path | str, ledger: Path | str, *, write: bool = True) -> list[dict[str, Any]]:
    """把 manifest 裡 ok 的原件壓縮後放進 ``<帳本>/sources/<壓縮檔雜湊>.raw.gz``（已存在就不動）。

    名字就是內容的雜湊，同名必定同內容：存回 R2 時不會和別的執行衝突，也不必把舊原件拉回本機。
    ``write=False`` 只算出名字、不寫檔（先放進紀錄，確定紀錄寫成了才寫原件）。
    """
    folder, target = Path(source_dir), Path(ledger) / "sources"
    out = []
    for entry in _read_manifest(folder):
        item = {key: entry[key] for key in ("name", "source", "url", "status", "fetched_at", "sha256", "bytes",
                                            "error_type") if key in entry}
        content = _raw(folder, entry)
        if content is not None:
            packed = gzip.compress(content, compresslevel=9, mtime=0)
            name = hashlib.sha256(packed).hexdigest() + ".raw.gz"
            if write:
                target.mkdir(parents=True, exist_ok=True)
            if write and not (target / name).exists():
                (target / name).write_bytes(packed)
            item["archived_as"] = f"sources/{name}"
        out.append(item)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="抓預測帳本海外層的原件（EODHD 金鑰讀環境變數 EODHD_API_KEY）")
    parser.add_argument("command", choices=["fetch"])
    parser.add_argument("--reference-date", help="晚間版＝基準日；晨間版＝開盤日")
    parser.add_argument("--analysis", type=Path, help="沒給 --reference-date 時，用這份已發布大盤資料的最後一天（晚間版）")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    reference = args.reference_date
    if not reference and args.analysis:
        from forecast_run import load_analysis
        daily = load_analysis(args.analysis).get("daily") or []
        reference = daily[-1]["date"] if daily else None
    if not reference:
        parser.error("要給 --reference-date 或內含日 K 的 --analysis")
    manifest = fetch_sources(args.out, reference_date=reference,
                             eodhd_token=os.environ.get("EODHD_API_KEY", "").strip())
    summary = {entry["name"]: entry["status"] if entry["status"] == "ok" else entry.get("error_type")
               for entry in manifest["sources"]}
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
