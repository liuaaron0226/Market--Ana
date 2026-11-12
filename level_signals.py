"""水位策略每日訊號收集、原件保存與 D1 版本化 SQL 產生器。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from level_strategy import RULE_VERSION, evaluate_day

ROOT = Path(__file__).resolve().parent
SIGNAL_ROOT = ROOT / "data" / "level-signals"
TAIEX_ROOT = ROOT / "data" / "market-snapshots" / "analysis-20261106" / "index-monthly"
STOCK_DB = ROOT / "data" / "stock-history" / "stock-history.sqlite"
HOLIDAY_CACHE = ROOT / "data" / "daily-update-log" / "holiday-schedule.json"
CBC_USDTWD_URL = "https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day"
TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"
OFR_FSI_URL = "https://www.financialresearch.gov/financial-stress-index/data/fsi.csv"
EODHD_SP500_ENDPOINT = "https://eodhd.com/api/eod/GSPC.INDX"
SOURCE_STATUS = {
    "taiex_trend": {"status": "active", "label": "加權指數趨勢", "source": "證交所既有大盤原件"},
    "sp500_trend": {"status": "active-requires-secret", "label": "S&P 500 趨勢", "source": "EODHD Free GSPC.INDX", "note": "使用未調整 close；有效收盤不足 200 筆算缺資料；尚未用本人 Key 實測"},
    "breadth": {"status": "active", "label": "上市股票強弱", "source": "證交所既有個股歷史"},
    "sox_5d": {"status": "blocked-terms", "label": "費城半導體 5 日跌幅", "reason": "Nasdaq 條款限制自動擷取與 store for subsequent use；待本人另行決定"},
    "vix": {"status": "blocked-terms", "label": "VIX", "reason": "Cboe 條款限制存入 electronic retrieval system；待本人另行決定"},
    "credit_stress": {"status": "active-pending-parameter", "label": "OFR FSI Credit 壓力變化", "source": "OFR 官方 FSI CSV", "note": "觸發門檻待本人確認"},
    "taiex_volatility": {"status": "disabled-pending-definition", "label": "台股 20 日波動", "reason": "v2.1 尚未定義精確統計式，正式未接、固定 0 分"},
    "foreign_flow": {"status": "disabled-insufficient-history", "label": "外資 5 日累計", "reason": "現有官方原件不足三年分位，正式未接、固定 0 分"},
    "twd_depreciation": {"status": "active", "label": "新台幣 20 日貶值", "source": "中央銀行統計資料庫，政府資料開放授權第 1 版"},
}


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class OriginalStore:
    def __init__(self, root: Path, data_date: str) -> None:
        self.directory = Path(root) / data_date
        self.data_date = data_date
        self.entries: list[dict[str, Any]] = []

    def write(self, name: str, content: bytes, source_url: str, fetched_at: str, *, observed_at: str | None = None) -> dict[str, Any]:
        if not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("source name contains unsupported characters")
        digest = hashlib.sha256(content).hexdigest()
        raw_name = f"{name}.raw"
        metadata_name = f"{name}.meta.json"
        entry = {
            "name": name,
            "path": raw_name,
            "source_url": source_url,
            "fetched_at": fetched_at,
            "observed_at": observed_at,
            "bytes": len(content),
            "sha256": digest,
        }
        atomic_write(self.directory / raw_name, content)
        atomic_write(self.directory / metadata_name, canonical_json(entry) + b"\n")
        self.entries.append(entry)
        return entry

    def finish(self) -> dict[str, Any]:
        manifest = {"schema": "level-signals-original/v1", "data_date": self.data_date, "files": sorted(self.entries, key=lambda item: item["name"])}
        atomic_write(self.directory / "manifest.json", canonical_json(manifest) + b"\n")
        return manifest


class HostPacer:
    """同一 host 相鄰請求至少相隔指定秒數；錯誤直接拋出，不重試。"""

    def __init__(self, interval_seconds: float = 5.0, clock=time.monotonic, sleeper=time.sleep) -> None:
        self.interval = interval_seconds
        self.clock = clock
        self.sleeper = sleeper
        self.last_by_host: dict[str, float] = {}

    def fetch(self, url: str, *, timeout: int = 30, accept: str = "application/xml,application/json;q=0.9,*/*;q=0.1") -> bytes:
        host = urllib.parse.urlsplit(url).hostname or ""
        previous = self.last_by_host.get(host)
        if previous is not None:
            wait = self.interval - (self.clock() - previous)
            if wait > 0:
                self.sleeper(wait)
        request = urllib.request.Request(url, headers={"User-Agent": "Market-Ana/1.0 (+private research)", "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                if response.status != 200:
                    raise RuntimeError(f"{host} returned HTTP {response.status}")
                content = response.read()
        finally:
            self.last_by_host[host] = self.clock()
        return content


def cached_source(
    path: Path,
    url: str,
    pacer: HostPacer,
    *,
    allow_network: bool,
    refresh: bool = False,
    accept: str = "*/*",
) -> tuple[bytes, str]:
    """取得官方原件並以 SHA sidecar 原子保存；離線時只接受已驗證快取。"""
    metadata_path = path.with_suffix(path.suffix + ".meta.json")
    legacy_metadata_path = path.with_name(path.stem + ".meta.json")
    if not metadata_path.exists() and legacy_metadata_path.exists():
        metadata_path = legacy_metadata_path
    if path.exists() and path.stat().st_size > 0 and not refresh:
        content = path.read_bytes()
        fetched_at = path.stat().st_mtime
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            expected = metadata.get("sha256")
            if expected and hashlib.sha256(content).hexdigest() != expected:
                raise ValueError(f"cached source SHA mismatch: {path}")
            return content, str(metadata.get("fetched_at") or datetime.fromtimestamp(fetched_at, timezone.utc).isoformat().replace("+00:00", "Z"))
        return content, datetime.fromtimestamp(fetched_at, timezone.utc).isoformat().replace("+00:00", "Z")
    if not allow_network:
        raise FileNotFoundError(f"offline source missing: {path}")
    content = pacer.fetch(url, timeout=90, accept=accept)
    if not content:
        raise RuntimeError(f"official source returned an empty body: {url}")
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    digest = hashlib.sha256(content).hexdigest()
    atomic_write(path, content)
    atomic_write(metadata_path, canonical_json({
        "source_url": url, "fetched_at": fetched_at, "bytes": len(content), "sha256": digest,
    }) + b"\n")
    return content, fetched_at


def percentile_rank(history: list[float], value: float, minimum: int = 600) -> float | None:
    if len(history) < minimum:
        return None
    return sum(item <= value for item in history) / len(history)


def parse_sox_xlsx(content: bytes, cutoff: str) -> list[dict[str, Any]]:
    """解析 Nasdaq ExportHistory XLSX 的 Trade Date 與 Index Value。"""
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                for node in shared_root.findall("x:si", namespace):
                    shared.append("".join(part.text or "" for part in node.iterfind(".//x:t", namespace)))
            sheet_names = sorted(name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
            if not sheet_names:
                raise ValueError("Nasdaq SOX XLSX has no worksheet")
            sheet = ET.fromstring(archive.read(sheet_names[0]))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError("Nasdaq SOX response is not a valid XLSX") from error

    table: list[dict[str, str]] = []
    for row in sheet.findall(".//x:sheetData/x:row", namespace):
        values: dict[str, str] = {}
        for cell in row.findall("x:c", namespace):
            reference = cell.attrib.get("r", "")
            column = "".join(character for character in reference if character.isalpha())
            value_node = cell.find("x:v", namespace)
            if not column or value_node is None or value_node.text is None:
                continue
            value = value_node.text
            if cell.attrib.get("t") == "s":
                try:
                    value = shared[int(value)]
                except (IndexError, ValueError):
                    continue
            values[column] = value
        if values:
            table.append(values)
    if not table:
        raise ValueError("Nasdaq SOX XLSX has no rows")
    header = {value.strip().lower(): column for column, value in table[0].items()}
    if "trade date" not in header or "index value" not in header:
        raise ValueError("Nasdaq SOX XLSX missing Trade Date or Index Value")
    date_column = header["trade date"]
    value_column = header["index value"]
    rows: dict[str, float] = {}
    for item in table[1:]:
        try:
            raw_date = item[date_column].strip()
            if raw_date.replace(".", "", 1).isdigit():
                observed = (date(1899, 12, 30) + timedelta(days=int(float(raw_date)))).isoformat()
            else:
                try:
                    observed = date.fromisoformat(raw_date).isoformat()
                except ValueError:
                    observed = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
            close = float(item[value_column].replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        if observed <= cutoff and math.isfinite(close) and close > 0:
            rows[observed] = close
    if not rows:
        raise ValueError("Nasdaq SOX XLSX has no valid observations before cutoff")
    return [{"date": observed, "close": rows[observed]} for observed in sorted(rows)]


def parse_vix_csv(content: bytes, cutoff: str) -> list[dict[str, Any]]:
    """解析 Cboe 官方 VIX 日資料，只保留截止日以前的收盤值。"""
    rows: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    required = {"DATE", "CLOSE"}
    if not reader.fieldnames or not required.issubset({name.strip().upper() for name in reader.fieldnames}):
        raise ValueError("Cboe VIX CSV missing DATE or CLOSE")
    for item in reader:
        normalized = {str(key).strip().upper(): value for key, value in item.items()}
        try:
            observed = datetime.strptime(str(normalized["DATE"]).strip(), "%m/%d/%Y").date().isoformat()
            close = float(str(normalized["CLOSE"]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if observed <= cutoff and math.isfinite(close) and close >= 0:
            rows[observed] = close
    if not rows:
        raise ValueError("Cboe VIX CSV has no valid observations before cutoff")
    return [{"date": observed, "close": rows[observed]} for observed in sorted(rows)]


def parse_eodhd_sp500(content: bytes, cutoff: str) -> dict[str, Any]:
    """解析 EODHD GSPC.INDX 一年日線；策略只用未調整 close。"""
    payload = json.loads(content.decode("utf-8-sig"))
    if not isinstance(payload, list):
        raise ValueError("EODHD GSPC.INDX response is not a row list")
    rows: dict[str, float] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            observed = date.fromisoformat(str(item.get("date", ""))).isoformat()
            close = float(item["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if observed <= cutoff and math.isfinite(close) and close > 0:
            rows[observed] = close
    ordered = sorted(rows.items())
    if len(ordered) < 200:
        raise ValueError(f"EODHD GSPC.INDX has only {len(ordered)} valid closes; 200 required")
    observed_at, close = ordered[-1]
    return {
        "observed_at": observed_at,
        "close": close,
        "ma200": sum(value for _, value in ordered[-200:]) / 200,
        "history_count": len(ordered),
    }


def parse_ofr_fsi_csv(content: bytes, cutoff: str) -> list[dict[str, Any]]:
    """解析 OFR FSI 的 Credit 分項；不得以 headline OFR FSI 冒充。"""
    rows: dict[str, float] = {}
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    field_map = {name.strip().lower(): name for name in (reader.fieldnames or [])}
    if "date" not in field_map or "credit" not in field_map:
        raise ValueError("OFR FSI CSV missing Date or Credit")
    for item in reader:
        try:
            raw_date = str(item[field_map["date"]]).strip()
            try:
                observed = date.fromisoformat(raw_date).isoformat()
            except ValueError:
                observed = datetime.strptime(raw_date, "%m/%d/%Y").date().isoformat()
            credit = float(str(item[field_map["credit"]]).replace(",", ""))
        except (TypeError, ValueError):
            continue
        if observed <= cutoff and math.isfinite(credit):
            rows[observed] = credit
    if not rows:
        raise ValueError("OFR FSI CSV has no valid Credit observations before cutoff")
    return [{"date": observed, "credit": rows[observed]} for observed in sorted(rows)]


def _three_year_start(observed: str) -> str:
    current = date.fromisoformat(observed)
    try:
        return current.replace(year=current.year - 3).isoformat()
    except ValueError:
        return current.replace(year=current.year - 3, day=28).isoformat()


def _lagged_percentile(series: list[tuple[str, float]], minimum_history: int) -> float | None:
    if not series:
        return None
    observed, current = series[-1]
    start = _three_year_start(observed)
    history = [value for day, value in series[:-1] if start <= day < observed]
    return percentile_rank(history, current, minimum=minimum_history)


def build_international_features(
    cutoff: str,
    vix_rows: list[dict[str, Any]],
    sox_rows: list[dict[str, Any]],
    credit_rows: list[dict[str, Any]],
    *,
    minimum_history: int = 600,
) -> dict[str, Any]:
    """由三個官方序列建立 8:30 前可用的分位訊號；比較窗不含當期值。"""
    vix = [item for item in vix_rows if item["date"] <= cutoff]
    sox = [item for item in sox_rows if item["date"] <= cutoff]
    credit = [item for item in credit_rows if item["date"] <= cutoff]
    result: dict[str, Any] = {"vix": {}, "sox": {}, "credit": {}}
    if vix:
        vix_series = [(str(item["date"]), float(item["close"])) for item in vix]
        result["vix"] = {
            "observed_at": vix_series[-1][0],
            "close": vix_series[-1][1],
            "high_10d": max(value for _, value in vix_series[-10:]),
            "percentile": _lagged_percentile(vix_series, minimum_history),
            "history_count": len(vix_series),
        }
    if len(sox) >= 6:
        sox_changes = [
            (str(sox[index]["date"]), float(sox[index]["close"]) / float(sox[index - 5]["close"]) - 1.0)
            for index in range(5, len(sox))
        ]
        result["sox"] = {
            "observed_at": sox_changes[-1][0],
            "close": float(sox[-1]["close"]),
            "return_5d": sox_changes[-1][1],
            "percentile": _lagged_percentile(sox_changes, minimum_history),
            "history_count": len(sox),
        }
    if len(credit) >= 21:
        credit_changes = [
            (str(credit[index]["date"]), float(credit[index]["credit"]) - float(credit[index - 20]["credit"]))
            for index in range(20, len(credit))
        ]
        result["credit"] = {
            "observed_at": credit_changes[-1][0],
            "value": float(credit[-1]["credit"]),
            "change_20d": credit_changes[-1][1],
            "percentile": _lagged_percentile(credit_changes, minimum_history),
            "history_count": len(credit),
        }
    return result


def validate_sox_years(
    sox_rows: list[dict[str, Any]],
    vix_rows: list[dict[str, Any]],
    *,
    start_year: int = 2005,
    end_year: int | None = None,
    minimum_coverage: float = 0.95,
    minimum_full_year_rows: int = 240,
    max_calendar_gap: int = 7,
) -> dict[str, Any]:
    """逐年核對 SOX 筆數、首尾與斷層；VIX 日集合只作交叉比對，因其含休市占位日。"""
    if end_year is None:
        end_year = max(int(str(item["date"])[:4]) for item in vix_rows)
    years: dict[str, Any] = {}
    for year in range(start_year, end_year + 1):
        label = str(year)
        expected = sorted({str(item["date"]) for item in vix_rows if str(item["date"]).startswith(label + "-")})
        actual_values = [str(item["date"]) for item in sox_rows if str(item["date"]).startswith(label + "-")]
        actual = sorted(set(actual_values))
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        coverage = len(set(expected) & set(actual)) / len(expected) if expected else 0.0
        gaps = [
            (date.fromisoformat(right) - date.fromisoformat(left)).days
            for left, right in zip(actual, actual[1:])
        ]
        largest_gap = max(gaps, default=0)
        first_ok = bool(actual) and actual[0] <= f"{year}-01-10"
        full_year = year < end_year
        last_ok = not full_year or (bool(actual) and actual[-1] >= f"{year}-12-20")
        count_ok = not full_year or len(actual) >= minimum_full_year_rows
        complete = (
            bool(expected) and coverage >= minimum_coverage and len(actual_values) == len(actual)
            and first_ok and last_ok and count_ok and largest_gap <= max_calendar_gap
        )
        years[label] = {
            "vix_calendar_count": len(expected),
            "actual_count": len(actual),
            "vix_overlap_ratio": round(coverage, 6),
            "first_date": actual[0] if actual else None,
            "last_date": actual[-1] if actual else None,
            "largest_calendar_gap_days": largest_gap,
            "missing_vix_dates": missing,
            "extra_dates": extra,
            "duplicate_count": len(actual_values) - len(actual),
            "complete": complete,
        }
    return {
        "reference": "annual row/span/gap checks; Cboe VIX dates are advisory because VIX contains market-holiday placeholders",
        "minimum_coverage": minimum_coverage,
        "minimum_full_year_rows": minimum_full_year_rows,
        "max_calendar_gap_days": max_calendar_gap,
        "start_year": start_year,
        "end_year": end_year,
        "complete": all(item["complete"] for item in years.values()),
        "years": years,
    }


def load_international_sources(
    data_date: str,
    store: OriginalStore,
    *,
    allow_network: bool,
    pacer: HostPacer,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """正式每日流程只接 EODHD S&P 500 與 OFR Credit；VIX／SOX 依條款決定固定未接。"""
    published_cutoff = (date.fromisoformat(data_date) - timedelta(days=1)).isoformat()
    international: dict[str, Any] = {"sp500": {}, "credit": {}}
    quality: dict[str, Any] = {
        "vix": {"status": "disabled-terms", "automatic_fetch": False},
        "sox": {"status": "disabled-terms", "automatic_fetch": False},
    }

    ofr_cache = store.directory / "ofr-fsi-credit.raw"
    try:
        ofr_content, ofr_fetched_at = cached_source(
            ofr_cache, OFR_FSI_URL, pacer, allow_network=allow_network,
            accept="text/csv,*/*;q=0.1",
        )
        credit_rows = parse_ofr_fsi_csv(ofr_content, published_cutoff)
        ofr_entry = store.write("ofr-fsi-credit", ofr_content, OFR_FSI_URL, ofr_fetched_at, observed_at=credit_rows[-1]["date"])
        atomic_write(store.directory / "ofr-fsi-credit.meta.json", canonical_json(ofr_entry) + b"\n")
        international["credit"] = build_international_features(published_cutoff, [], [], credit_rows)["credit"]
        quality["ofr_credit"] = {"status": "ok", "observed_at": international["credit"].get("observed_at")}
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
        quality["ofr_credit"] = {"status": "missing", "error_type": type(error).__name__}

    eodhd_cache = store.directory / "eodhd-sp500.raw"
    eodhd_content: bytes | None = eodhd_cache.read_bytes() if eodhd_cache.exists() and eodhd_cache.stat().st_size > 0 else None
    eodhd_fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    token = os.environ.get("EODHD_API_KEY", "").strip()
    if eodhd_content is None and allow_network and token:
        start = (date.fromisoformat(published_cutoff) - timedelta(days=400)).isoformat()
        query = urllib.parse.urlencode({"api_token": token, "fmt": "json", "period": "d", "from": start, "to": published_cutoff})
        try:
            eodhd_content = pacer.fetch(f"{EODHD_SP500_ENDPOINT}?{query}", timeout=90, accept="application/json,*/*;q=0.1")
        except Exception as error:
            quality["sp500"] = {"status": "missing", "error_type": type(error).__name__, "history_count": 0}
    if eodhd_content is not None:
        try:
            sp500 = parse_eodhd_sp500(eodhd_content, published_cutoff)
            sanitized_url = f"{EODHD_SP500_ENDPOINT}?api_token=REDACTED&fmt=json&period=d&from=REDACTED&to=REDACTED"
            entry = store.write("eodhd-sp500", eodhd_content, sanitized_url, eodhd_fetched_at, observed_at=sp500["observed_at"])
            atomic_write(store.directory / "eodhd-sp500.meta.json", canonical_json(entry) + b"\n")
            international["sp500"] = sp500
            quality["sp500"] = {"status": "ok", "observed_at": sp500["observed_at"], "history_count": sp500["history_count"]}
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            quality["sp500"] = {"status": "missing", "error_type": type(error).__name__, "history_count": 0}
    elif "sp500" not in quality:
        quality["sp500"] = {"status": "missing-secret-or-cache", "history_count": 0}
    return international, quality


def parse_cbc_usdtwd(content: bytes, cutoff: str) -> dict[str, Any]:
    rows: list[tuple[str, float]] = []
    stripped = content.lstrip()
    if stripped.startswith(b"["):
        records = json.loads(content.decode("utf-8-sig"))
        candidates = [(str(item.get("日期", "")), str(item.get("NTD_USD", ""))) for item in records]
    else:
        root = ET.fromstring(content)
        candidates = []
        for element in root.iter():
            children = {child.tag.split("}")[-1].upper(): (child.text or "").strip() for child in list(element)}
            candidates.append((
                next((children[key] for key in ("DATE", "DATADATE", "日期") if key in children), ""),
                next((children[key] for key in ("NTD_USD", "NTDUSD", "VALUE", "新臺幣") if key in children), ""),
            ))
    for date_text, value_text in candidates:
        try:
            clean_date = date_text.replace("/", "-")
            if len(clean_date) == 8 and clean_date.isdigit():
                clean_date = f"{clean_date[:4]}-{clean_date[4:6]}-{clean_date[6:]}"
            observed = date.fromisoformat(clean_date).isoformat()
            value = float(value_text.replace(",", ""))
        except (ValueError, TypeError):
            continue
        if observed <= cutoff:
            rows.append((observed, value))
    rows = sorted(dict(rows).items())
    if len(rows) < 21:
        raise ValueError("CBC USD/TWD XML has fewer than 21 observations")
    observed_at, current = rows[-1]
    depreciation = current / rows[-21][1] - 1.0
    cutoff_date = date.fromisoformat(cutoff)
    try:
        start_date = cutoff_date.replace(year=cutoff_date.year - 3)
    except ValueError:  # 2/29 對齊到三年前的 2/28。
        start_date = cutoff_date.replace(year=cutoff_date.year - 3, day=28)
    start = start_date.isoformat()
    comparisons: list[float] = []
    for index in range(20, len(rows) - 1):
        if rows[index][0] >= start:
            comparisons.append(rows[index][1] / rows[index - 20][1] - 1.0)
    return {
        "observed_at": observed_at,
        "close": current,
        "depreciation_20d": depreciation,
        "percentile": percentile_rank(comparisons, depreciation, minimum=500),
        "history_count": len(rows),
        "comparison_count": len(comparisons),
        "quote_direction": "NTD per USD",
    }


def _roc_date(text: str) -> str:
    year, month, day = (int(part) for part in text.split("/"))
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def load_taiex(cutoff: str) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for path in sorted(TAIEX_ROOT.glob("taiex-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for item in payload.get("data", {}).get("data", []):
            observed = _roc_date(item[0])
            if observed <= cutoff:
                rows.append({
                    "date": observed,
                    "open": float(item[1].replace(",", "")),
                    "high": float(item[2].replace(",", "")),
                    "low": float(item[3].replace(",", "")),
                    "close": float(item[4].replace(",", "")),
                })
    by_date = {str(row["date"]): row for row in rows}
    return [by_date[key] for key in sorted(by_date)]


def breadth_features(cutoff: str) -> tuple[float | None, float | None]:
    if not STOCK_DB.exists():
        return None, None
    with sqlite3.connect(STOCK_DB) as connection:
        dates = [row[0] for row in connection.execute(
            "SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date DESC LIMIT 220", (cutoff,)
        )]
        if len(dates) < 220:
            return None, None
        rows = connection.execute(
            f"SELECT date, code, close FROM daily WHERE kind = 'common' AND date IN ({','.join('?' for _ in dates)}) AND close IS NOT NULL",
            dates,
        ).fetchall()
    chronological = list(reversed(dates))
    values: dict[str, list[tuple[str, float]]] = {}
    for observed, code, close in rows:
        values.setdefault(code, []).append((observed, float(close)))
    by_date: dict[str, list[bool]] = {observed: [] for observed in chronological}
    for history in values.values():
        history.sort()
        closes: list[float] = []
        for observed, close in history:
            closes.append(close)
            if len(closes) >= 200:
                by_date[observed].append(close >= sum(closes[-200:]) / 200)
    latest_date = chronological[-1]
    prior_date = chronological[-21]
    if len(by_date[latest_date]) < 500 or len(by_date[prior_date]) < 500:
        return None, None
    latest = 100 * sum(by_date[latest_date]) / len(by_date[latest_date])
    prior = 100 * sum(by_date[prior_date]) / len(by_date[prior_date])
    return latest, max(0.0, prior - latest)


def ensure_holiday_calendar(data_date: str, *, allow_network: bool = True) -> set[str]:
    from daily_ingest import parse_holiday_schedule

    year = data_date[:4]
    holidays: set[str] = set()
    if HOLIDAY_CACHE.exists():
        try:
            holidays = parse_holiday_schedule(HOLIDAY_CACHE.read_bytes())
        except (ValueError, KeyError, UnicodeDecodeError):
            holidays = set()
    if any(day.startswith(year + "-") for day in holidays):
        return holidays
    if not allow_network:
        raise RuntimeError(f"缺少 {year} 年證交所官方休市日曆，不能猜測交易日")
    content = HostPacer(5.0).fetch(TWSE_HOLIDAY_URL)
    holidays = parse_holiday_schedule(content)
    if not any(day.startswith(year + "-") for day in holidays):
        raise RuntimeError(f"證交所休市日曆未涵蓋 {year} 年")
    atomic_write(HOLIDAY_CACHE, content)
    atomic_write(HOLIDAY_CACHE.with_suffix(".json.sha256"), (hashlib.sha256(content).hexdigest() + "\n").encode("ascii"))
    return holidays


def trading_day_status(data_date: str, holidays: set[str] | None = None) -> bool:
    day = date.fromisoformat(data_date)
    if day.weekday() >= 5:
        return False
    if holidays is None:
        holidays = ensure_holiday_calendar(data_date, allow_network=False)
    return data_date not in holidays


def build_values(
    data_date: str,
    usdtwd: dict[str, Any] | None = None,
    *,
    international: dict[str, Any] | None = None,
    holidays: set[str] | None = None,
) -> dict[str, Any]:
    market_cutoff = (date.fromisoformat(data_date) - timedelta(days=1)).isoformat()
    taiex = load_taiex(market_cutoff)
    latest = taiex[-1] if taiex else None
    closes = [float(item["close"]) for item in taiex]
    breadth, breadth_drop = breadth_features(market_cutoff)
    international = international or {}
    sp500 = international.get("sp500") or {}
    vix = international.get("vix") or {}
    sox = international.get("sox") or {}
    credit = international.get("credit") or {}
    values: dict[str, Any] = {
        "date": data_date,
        "unavailable_signals": [key for key, status in SOURCE_STATUS.items() if not status["status"].startswith("active")],
        # 盤前使用上一個已公布收盤；今天是否交易日由官方休市日快取判定，不能要求已有今天收盤。
        "is_tw_trading_day": trading_day_status(data_date, holidays),
        "taiex_close": float(latest["close"]) if latest else None,
        "taiex_observed_at": str(latest["date"]) if latest else None,
        "taiex_ma200": sum(closes[-200:]) / 200 if len(closes) >= 200 else None,
        "taiex_1y_high": max(closes[-252:]) if closes else None,
        "sp500_close": sp500.get("close"),
        "sp500_ma200": sp500.get("ma200"),
        "sp500_observed_at": sp500.get("observed_at"),
        "vix_percentile": vix.get("percentile"),
        "vix_close": vix.get("close"),
        "vix_10d_high": vix.get("high_10d"),
        "vix_observed_at": vix.get("observed_at"),
        # v2.1 未定義「20 日實際漲跌幅度」的精確統計式，正式核定前不自行選公式。
        "taiex_volatility_percentile": None,
        "breadth_drop_pp_20d": breadth_drop,
        "taiex_above_ma200": bool(latest and len(closes) >= 200 and closes[-1] >= sum(closes[-200:]) / 200),
        "breadth_above_ma200_pct": breadth,
        "sox_5d_percentile": sox.get("percentile"),
        "sox_5d_return": sox.get("return_5d"),
        "sox_observed_at": sox.get("observed_at"),
        "credit_stress_change_percentile": credit.get("percentile"),
        "credit_stress_change_20d": credit.get("change_20d"),
        "credit_stress_observed_at": credit.get("observed_at"),
        "foreign_sell_5d_percentile": None,
        "twd_depreciation_20d_percentile": usdtwd.get("percentile") if usdtwd else None,
        "twd_depreciation_20d": usdtwd.get("depreciation_20d") if usdtwd else None,
        "usdtwd_observed_at": usdtwd.get("observed_at") if usdtwd else None,
    }
    return values


class LevelStateMergeError(ValueError):
    """D1／R2 水位狀態無法安全判定時 fail closed。"""


def _state_date(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise LevelStateMergeError(f"{field} 必須是有效的 YYYY-MM-DD")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise LevelStateMergeError(f"{field} 必須是有效的 YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise LevelStateMergeError(f"{field} 必須是有效的 YYYY-MM-DD")
    return value


def _parse_level_state(payload: Any, source: str) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, dict):
        raise LevelStateMergeError(f"{source} state 必須是 JSON object")
    state_date = _state_date(payload.get("last_processed_date"), f"{source}.last_processed_date")
    return payload, state_date


def merge_level_state(d1_result: Path, r2_state: Path, *, run_date: str) -> dict[str, Any]:
    """Fail-closed 合併 D1 current row 與 R2 state；只有真空結果可視為尚未初始化。"""
    run_date = _state_date(run_date, "run_date")
    d1_result = Path(d1_result)
    r2_state = Path(r2_state)
    try:
        outer = json.loads(d1_result.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LevelStateMergeError(f"D1 查詢結果無法讀取：{type(error).__name__}") from error
    if not isinstance(outer, list) or len(outer) != 1 or not isinstance(outer[0], dict):
        raise LevelStateMergeError("D1 查詢結果格式不明")
    batch = outer[0]
    if batch.get("success") is not True:
        raise LevelStateMergeError("D1 查詢未明確回報 success=true")
    rows = batch.get("results")
    if not isinstance(rows, list):
        raise LevelStateMergeError("D1 results 不是 array")
    if len(rows) > 1:
        raise LevelStateMergeError("D1 current state 不唯一")

    remote_state: dict[str, Any] | None = None
    remote_date: str | None = None
    if rows:
        row = rows[0]
        if not isinstance(row, dict) or not isinstance(row.get("payload"), str):
            raise LevelStateMergeError("D1 row／payload 格式錯誤")
        _state_date(row.get("data_date"), "D1.data_date")
        try:
            remote_payload = json.loads(row["payload"])
        except json.JSONDecodeError as error:
            raise LevelStateMergeError("D1 payload 不是有效 JSON") from error
        remote_state, remote_date = _parse_level_state(remote_payload, "D1 payload")
        # level_strategy_state.data_date 是 D1 寫入日；狀態在休市／資料不足時可沿用較舊的 last_processed_date。
        # 合併與 R2 cursor 只比較 payload 的實際 last_processed_date，不把寫入日冒充狀態日。

    local_state: dict[str, Any] | None = None
    local_date: str | None = None
    if r2_state.exists():
        try:
            local_payload = json.loads(r2_state.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LevelStateMergeError(f"R2 state 無法讀取：{type(error).__name__}") from error
        local_state, local_date = _parse_level_state(local_payload, "R2 state")

    for source, state_date in (("D1", remote_date), ("R2", local_date)):
        if state_date is not None and state_date > run_date:
            raise LevelStateMergeError(f"{source} state {state_date} 晚於本次 run_date {run_date}，拒絕倒退處理")

    if remote_state is not None and (local_date is None or remote_date > local_date):
        atomic_write(r2_state, canonical_json(remote_state) + b"\n")
        return {"selected": "d1", "last_processed_date": remote_date}
    if local_state is not None:
        return {"selected": "r2", "last_processed_date": local_date}
    return {"selected": "empty", "last_processed_date": None}


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def write_daily_sql(path: Path, payload: dict[str, Any], input_sha256: str, source_cutoff: str, created_at: str) -> None:
    data_date = str(payload["data_date"])
    rule_version = str(payload.get("rule_version") or RULE_VERSION)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # 單一 INSERT；migration 的 AFTER INSERT trigger 在同一 SQLite statement 內切 current 並保存狀態。
    # 不寫 BEGIN/COMMIT，避免 Wrangler `d1 execute --file` 的 transaction wrapper 形成巢狀交易。
    sql = f"""INSERT INTO level_daily (data_date, version, rule_version, source_cutoff, input_sha256, payload, created_at, is_current)
SELECT {sql_quote(data_date)}, COALESCE(MAX(version), 0) + 1, {sql_quote(rule_version)}, {sql_quote(source_cutoff)}, {sql_quote(input_sha256)}, {sql_quote(body)}, {sql_quote(created_at)}, 0
FROM level_daily WHERE data_date = {sql_quote(data_date)}
HAVING NOT EXISTS (SELECT 1 FROM level_daily WHERE data_date = {sql_quote(data_date)} AND input_sha256 = {sql_quote(input_sha256)});
"""
    atomic_write(Path(path), sql.encode("utf-8"))


def run_daily(args: argparse.Namespace) -> dict[str, Any]:
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    store = OriginalStore(Path(args.signal_root), args.date)
    pacer = HostPacer(5.0)
    usdtwd = None
    source_quality: dict[str, Any] = {}
    cached = store.directory / "cbc-usdtwd.raw"
    if cached.exists() or not args.no_network:
        try:
            content, original_fetched_at = cached_source(
                cached, CBC_USDTWD_URL, pacer, allow_network=not args.no_network,
                accept="application/xml,application/json;q=0.9,*/*;q=0.1",
            )
            published_cutoff = (date.fromisoformat(args.date) - timedelta(days=1)).isoformat()
            parsed = parse_cbc_usdtwd(content, published_cutoff)
            entry = store.write("cbc-usdtwd", content, CBC_USDTWD_URL, original_fetched_at, observed_at=parsed["observed_at"])
            atomic_write(store.directory / "cbc-usdtwd.meta.json", canonical_json(entry) + b"\n")
            usdtwd = parsed
            source_quality["usdtwd"] = {"status": "ok", "observed_at": parsed["observed_at"]}
        except (OSError, RuntimeError, ValueError, ET.ParseError, json.JSONDecodeError, UnicodeDecodeError) as error:
            source_quality["usdtwd"] = {"status": "missing", "error_type": type(error).__name__}
    international, international_quality = load_international_sources(
        args.date, store, allow_network=not args.no_network, pacer=pacer,
    )
    source_quality.update(international_quality)
    manifest = store.finish()
    holidays = ensure_holiday_calendar(args.date, allow_network=not args.no_network)
    values = build_values(args.date, usdtwd, international=international, holidays=holidays)
    previous_path = Path(args.previous_state)
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else None
    result = evaluate_day(values, previous)
    payload = {key: value for key, value in result.items() if key != "state"}
    payload.update({
        "schema": "level-daily/v1", "data_date": args.date, "source_manifest": manifest,
        "source_status": SOURCE_STATUS,
        "data_quality": source_quality,
        "online_monitoring": {"status": "insufficient-history", "red": [], "yellow": [], "display_disabled": False},
    })
    payload["state"] = result["state"]
    strategy_sha = hashlib.sha256((ROOT / "level_strategy.py").read_bytes()).hexdigest()
    source_identity = [{key: item.get(key) for key in ("name", "source_url", "observed_at", "sha256")} for item in manifest["files"]]
    digest = hashlib.sha256(canonical_json({"values": values, "sources": source_identity, "rule_version": RULE_VERSION, "strategy_sha256": strategy_sha})).hexdigest()
    atomic_write(Path(args.output), canonical_json(payload) + b"\n")
    atomic_write(previous_path, canonical_json(result["state"]) + b"\n")
    write_daily_sql(Path(args.sql_output), payload, digest, f"{args.date}T08:30:00+08:00", fetched_at)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    daily = sub.add_parser("daily")
    daily.add_argument("--date", required=True)
    daily.add_argument("--signal-root", default=str(SIGNAL_ROOT))
    daily.add_argument("--previous-state", default=str(SIGNAL_ROOT / "state.json"))
    daily.add_argument("--output", default=str(SIGNAL_ROOT / "latest.json"))
    daily.add_argument("--sql-output", default=str(SIGNAL_ROOT / "level-daily.sql"))
    daily.add_argument("--no-network", action="store_true")
    merge = sub.add_parser("merge-state")
    merge.add_argument("--d1-result", type=Path, required=True)
    merge.add_argument("--r2-state", type=Path, default=SIGNAL_ROOT / "state.json")
    merge.add_argument("--run-date", required=True)
    args = parser.parse_args(argv)
    if args.command == "daily":
        payload = run_daily(args)
        print(json.dumps({"data_date": payload["data_date"], "recommended_level": payload.get("recommended_level"), "missing_signals": payload.get("missing_signals")}, ensure_ascii=False))
    else:
        result = merge_level_state(args.d1_result, args.r2_state, run_date=args.run_date)
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
