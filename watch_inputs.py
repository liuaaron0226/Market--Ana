"""自選股關注 I 票：交給 Gemini 的融資融券、同產業其他股票、匯率與美債利率（布林通道由關注帳本從日 K 自己算）。

每一樣都從本機已存的原件讀，回傳一塊附資料日期與出處的輸入，直接放進 ``watch_ledger.write_judgment`` 的
``margin``、``sector``、``environment`` 欄位；讀不到就回 None（帳本把那一層標成缺漏，判斷照寫）。這裡不連網：

- ``margin_input``：上市讀 ``data/margin-history/raw/``（每日抓取已在存，``margin_history.py``），
  上櫃讀 ``data/otc-margin/raw/``（``otc_margin.py``，從開始抓那天起累積）。
- ``sector_input``：官方產業別相同、在分析範圍內的其他個股當天與近 5 日漲跌；產業別用 ``catalog_industries``
  從本機名錄原件讀，分析範圍與日 K 由呼叫端給。
- ``environment_input``：央行每日收盤匯率（水位訊號每日存的 ``cbc-usdtwd`` 原件）與財政部公債殖利率
  （``treasury_yields.py`` 的保存區）。

``SOURCES`` 是每一個來源要在網站上標的出處文字；每一塊輸入都帶著它，網站照著顯示。
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import otc_margin
import treasury_yields
from level_signals import parse_cbc_usdtwd
from listed_catalog import load_listed_catalog
from otc_catalog import load_otc_catalog

SOURCES = {
    "twse_margin": "臺灣證券交易所 融資融券餘額（MI_MARGN）；政府資料開放授權條款第 1 版",
    "tpex_margin": "證券櫃檯買賣中心 上櫃股票融資融券餘額（OpenAPI）",
    "catalog": "臺灣證券交易所／證券櫃檯買賣中心 有價證券名錄的產業別；漲跌由本站從官方收盤行情計算",
    "cbc_usdtwd": "中央銀行 新臺幣對美元銀行間收盤匯率；政府資料開放授權條款第 1 版",
    "us_treasury": "U.S. Department of the Treasury, Daily Treasury Par Yield Curve Rates",
}
MARGIN_DIR = "data/margin-history/raw"
LEVEL_SIGNALS_DIR = "data/level-signals"
CATALOG_FILES = (("data/listed-catalog/raw", "twse-isin-listed-*.html", load_listed_catalog),
                 ("data/otc-catalog/raw", "tpex-isin-otc-*.html", load_otc_catalog))
CHANGE_DAYS = 5          # 融資融券、同產業：近 5 個交易日
ENVIRONMENT_DAYS = 20    # 匯率、美債：近 20 個交易日
_LISTED_FILE = re.compile(r"margin-(\d{8})\.json", re.ASCII)


def _iso(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _lots(text: Any) -> int:
    return int(str(text).replace(",", "").strip())


# ---- 融資融券 ----

def _listed_balances(path: Path, code: str) -> tuple[int, int] | None:
    """證交所 MI_MARGN 原件裡這一檔的（融資今日餘額, 融券今日餘額），單位張。"""
    payload = json.loads(path.read_text(encoding="utf-8")).get("data") or {}
    for table in payload.get("tables") or []:
        fields = [str(field).strip() for field in table.get("fields") or []]
        if "代號" not in fields:
            continue
        balances = [index for index, field in enumerate(fields) if field == "今日餘額"]
        for row in table.get("data") or []:
            if str(row[fields.index("代號")]).strip() == code:
                return _lots(row[balances[0]]), _lots(row[balances[1]])
    return None


def _otc_balances(path: Path, code: str) -> tuple[int, int] | None:
    for row in json.loads(path.read_text(encoding="utf-8")).get("data") or []:
        if str(row.get("SecuritiesCompanyCode")).strip() == code:
            return _lots(row["MarginPurchaseBalance"]), _lots(row["ShortSaleBalance"])
    return None


def _listed_days(root: Path) -> dict[str, Path]:
    folder = root / MARGIN_DIR
    if not folder.is_dir():
        return {}
    return {match.group(1): path for path in folder.iterdir() if (match := _LISTED_FILE.fullmatch(path.name))}


def _margin_from(days: Mapping[str, Path], read, code: str, base_date: str) -> dict[str, Any] | None:
    held = sorted(day for day in days if _iso(day) <= base_date)
    for position in range(len(held) - 1, -1, -1):
        today = read(days[held[position]], code)
        if today is None:
            continue
        earlier_day = held[position - CHANGE_DAYS] if position >= CHANGE_DAYS else None
        earlier = read(days[earlier_day], code) if earlier_day else None
        return {
            "data_date": _iso(held[position]), "unit": "張",
            "margin_balance": today[0], "margin_change_5d": today[0] - earlier[0] if earlier else None,
            "short_balance": today[1], "short_change_5d": today[1] - earlier[1] if earlier else None,
            "compared_with": _iso(earlier_day) if earlier else None,
        }
    return None


def margin_input(root: Path | str, code: str, base_date: str) -> dict[str, Any] | None:
    """這一檔在基準日（或之前最近一個有原件的交易日）的融資、融券餘額，與往前第 5 個有原件的交易日相比的變化。

    上市先找、找不到再找上櫃；``source`` 是 ``twse`` 或 ``tpex``（上櫃那一份依條款不放進提示，由帳本把關）。
    """
    root = Path(root)
    for source, days, read in (("twse", _listed_days(root), _listed_balances),
                               ("tpex", otc_margin.stored_days(root), _otc_balances)):
        found = _margin_from(days, read, code, base_date)
        if found:
            return {"source": source, "attribution": SOURCES[f"{source}_margin"], **found}
    return None


# ---- 同產業其他股票 ----

def catalog_industries(root: Path | str) -> dict[str, tuple[str, str]]:
    """本機最新一份上市、上櫃名錄原件裡每一檔普通股的（名稱, 官方產業別）。"""
    out: dict[str, tuple[str, str]] = {}
    for folder, pattern, loader in CATALOG_FILES:
        found = sorted((Path(root) / folder).glob(pattern))
        if found:
            raw = found[-1]
            out.update(loader(raw, raw.with_suffix(".meta.json"), Path(".")).industries())
    return out


def _pct(now: float, then: float) -> float:
    return (now / then - 1) * 100


def sector_input(code: str, base_date: str, members: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    """官方產業別和這一檔相同的其他個股，在基準日當天與近 5 個交易日的漲跌（百分比），加上全組平均。

    ``members`` 是分析範圍內每一檔的 ``{"name", "industry", "daily": [{"date", "close"}]}``（含這一檔自己，
    日 K 用還原後的收盤）。基準日沒有日 K 的同產業個股列在 ``no_data``；不足 5 日的那一檔近 5 日寫 None、不算進平均。
    """
    industry = (members.get(code) or {}).get("industry")
    if not industry:
        return None
    peers, no_data = [], []
    for other in sorted(members):
        entry = members[other]
        if other == code or entry.get("industry") != industry:
            continue
        rows = sorted((row for row in entry.get("daily") or [] if row.get("close")), key=lambda row: row["date"])
        dates = [row["date"] for row in rows]
        if base_date not in dates:
            no_data.append(other)
            continue
        at = dates.index(base_date)
        close = rows[at]["close"]
        peers.append({"code": other, "name": entry.get("name"),
                      "change_1d": _pct(close, rows[at - 1]["close"]) if at >= 1 else None,
                      "change_5d": _pct(close, rows[at - CHANGE_DAYS]["close"]) if at >= CHANGE_DAYS else None})

    def average(key: str) -> float | None:
        values = [peer[key] for peer in peers if peer[key] is not None]
        return round(sum(values) / len(values), 2) if values else None

    return {
        "industry": industry, "source": SOURCES["catalog"], "data_date": base_date,
        "peers": [{"code": peer["code"], "name": peer["name"],
                   "change_1d_pct": None if peer["change_1d"] is None else round(peer["change_1d"], 2),
                   "change_5d_pct": None if peer["change_5d"] is None else round(peer["change_5d"], 2)}
                  for peer in peers],
        "average_1d_pct": average("change_1d"), "average_5d_pct": average("change_5d"), "no_data": no_data,
    }


# ---- 匯率與美債利率 ----

def _usdtwd(root: Path, as_of: str) -> dict[str, Any] | None:
    """水位訊號每日存的央行原件裡、資料日期不晚於 ``as_of`` 的最新一份。"""
    folder = root / LEVEL_SIGNALS_DIR
    days = sorted(path.parent.name for path in folder.glob("*/cbc-usdtwd.raw")
                  if re.fullmatch(r"\d{4}-\d{2}-\d{2}", path.parent.name) and path.parent.name <= as_of) \
        if folder.is_dir() else []
    if not days:
        return None
    try:
        parsed = parse_cbc_usdtwd((folder / days[-1] / "cbc-usdtwd.raw").read_bytes(), as_of)
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return {"date": parsed["observed_at"], "close": parsed["close"],
            "change_20d_pct": round(parsed["depreciation_20d"] * 100, 2), "quote": "每 1 美元兌新臺幣",
            "source": SOURCES["cbc_usdtwd"]}


def _treasury(root: Path, as_of: str) -> dict[str, Any] | None:
    rows = [row for row in treasury_yields.load(root) if row["date"] <= as_of]
    if not rows:
        return None
    last = rows[-1]
    earlier = rows[-1 - ENVIRONMENT_DAYS] if len(rows) > ENVIRONMENT_DAYS else None

    def change(key: str) -> float | None:
        if earlier is None or last[key] is None or earlier[key] is None:
            return None
        return round(last[key] - earlier[key], 2)

    return {"date": last["date"], "yield_2y_pct": last["yield_2y"], "yield_10y_pct": last["yield_10y"],
            "change_20d_2y_pt": change("yield_2y"), "change_20d_10y_pt": change("yield_10y"),
            "source": SOURCES["us_treasury"]}


def environment_input(root: Path | str, as_of: str) -> dict[str, Any] | None:
    """新臺幣對美元收盤匯率與美債 2 年、10 年殖利率，各取資料日期不晚於 ``as_of`` 的最新一筆，附近 20 個交易日的變化。

    匯率變化是百分比（正數是新臺幣貶值）；殖利率變化是百分點。某一項讀不到就是 None 並列在 ``missing``；
    兩項都讀不到回 None（整層缺漏）。
    """
    root = Path(root)
    found = {"usdtwd": _usdtwd(root, as_of), "us_treasury": _treasury(root, as_of)}
    if not any(found.values()):
        return None
    return {**found, "missing": [name for name, value in found.items() if value is None]}
