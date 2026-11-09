"""官方基本面歷史原件解析與 point-in-time 查詢。"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, NamedTuple


_MISSING = {"", "--", "---", "N/A", "不適用"}
MIN_INTERVAL_SECONDS = 5.0
MOPS_HOST = "https://mopsov.twse.com.tw"
MARKET_CODES = {"listed": "sii", "otc": "otc"}
# MOPS 月營收彙總分兩份：_0 為國內公司、_1 為國外公司（KY／DR）。兩表單位同為新台幣千元，
# 公司不重疊；以 source_scope 區分來源，不混成同一份原件。
MONTHLY_SCOPE_SUFFIX = {"domestic": "0", "foreign": "1"}
STATEMENT_ENDPOINTS = {
    "income": "ajax_t163sb04",
    "balance": "ajax_t163sb05",
    "cashflow": "ajax_t163sb20",
}


class HttpResponse(NamedTuple):
    status: int
    content_type: str
    content: bytes
    url: str


class FundamentalsHistory:
    """已解析序列的唯讀 point-in-time 查詢介面。"""

    def __init__(self, *, monthly: list[dict], quarterly: list[dict]) -> None:
        self._monthly = tuple(monthly)
        self._quarterly = tuple(quarterly)

    def as_of(self, code: str, as_of: str) -> dict[str, list[dict]]:
        date.fromisoformat(as_of)

        def visible(records: tuple[dict, ...]) -> list[dict]:
            return sorted(
                (dict(row) for row in records if row.get("code") == code and row.get("available_on", "9999-12-31") <= as_of),
                key=lambda row: (row.get("period", ""), row.get("available_on", "")),
            )

        return {"monthly": visible(self._monthly), "quarterly": visible(self._quarterly)}

    def to_dict(self) -> dict[str, list[dict]]:
        return {
            "monthly": [dict(row) for row in self._monthly],
            "quarterly": [dict(row) for row in self._quarterly],
        }


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append(_clean("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _header(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _integer(value: str) -> int | None:
    value = _clean(value)
    if value in _MISSING:
        return None
    return int(value.replace(",", ""))


def _number(value: str) -> float | None:
    value = _clean(value)
    if value in _MISSING:
        return None
    return float(value.replace(",", ""))


def _decode_html(content: bytes) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        # MOPS 歷史 Big5 原件偶有公司名稱的無效位元組；保留原始 bytes 與 SHA，
        # 只在文字解析層替換該字元，數字欄位與結構仍接受嚴格驗證。
        return content.decode("big5", errors="replace")


def _monthly_available_on(period: str) -> tuple[str, str]:
    year, month = map(int, period.split("-"))
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    # 2026 年起部分保險業可延至 15 日；來源彙總表沒有穩定產業鍵，
    # 對所有公司使用最晚適用截止日，避免一般化查詢偷看尚未申報資料。
    special_extension = (year, month) >= (2026, 1)
    deadline = date(next_year, next_month, 15 if special_extension else 10)
    if deadline.weekday() == 5:
        deadline += timedelta(days=2)
    elif deadline.weekday() == 6:
        deadline += timedelta(days=1)
    basis = "conservative_statutory_deadline" if special_extension else "statutory_deadline"
    return deadline.isoformat(), basis


def _quarter_available_on(period: str) -> tuple[str, str]:
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period)
    if not match:
        raise ValueError(f"無效季度：{period}")
    year, quarter = int(match.group(1)), int(match.group(2))
    if quarter == 4:
        deadline = date(year + 1, 3, 31)
    else:
        month = quarter * 3
        deadline = date(year, month, calendar.monthrange(year, month)[1])
        deadline += timedelta(days=45)
    if deadline.weekday() == 5:
        deadline += timedelta(days=2)
    elif deadline.weekday() == 6:
        deadline += timedelta(days=1)
    return deadline.isoformat(), "statutory_deadline"


def _statement_rows(content: bytes) -> dict[str, tuple[dict[str, str], set[str]]]:
    parser = _TableParser()
    parser.feed(_decode_html(content))
    records: dict[str, tuple[dict[str, str], set[str]]] = {}
    for table in parser.tables:
        headers: list[str] | None = None
        header_set: set[str] = set()
        for row in table:
            normalized = [_header(cell) for cell in row]
            if "公司代號" in normalized and "公司名稱" in normalized:
                headers, header_set = normalized, set(normalized)
                continue
            if headers is None:
                continue
            cells = dict(zip(headers, row))
            code = _clean(cells.get("公司代號", ""))
            if re.fullmatch(r"[0-9A-Z]{4,6}", code):
                records[code] = (cells, header_set)
    return records


def _validate_statement_kind(statement: str, content: bytes) -> None:
    rows = _statement_rows(content)
    if not rows:
        raise ValueError(f"非預期格式：{statement} 缺公司資料列")
    headers: set[str] = set()
    for _, row_headers in rows.values():
        headers.update(row_headers)
    expected = {
        "income": lambda: bool({"本期淨利（淨損）", "基本每股盈餘（元）"} & headers),
        "balance": lambda: {"資產總計", "負債總計"}.issubset(headers),
        "cashflow": lambda: "營業活動之淨現金流入（流出）" in headers,
    }
    if statement not in expected or not expected[statement]():
        raise ValueError(f"非預期格式：內容不是 {statement} 報表")


def _first(cells: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        if name in cells:
            return cells[name]
    return ""


def parse_quarterly_financials(
    income_content: bytes,
    balance_content: bytes,
    cashflow_content: bytes,
    *,
    market: str,
    period: str,
    sources: dict[str, dict],
) -> list[dict]:
    """合併 MOPS 季損益、資產負債與現金流彙總；損益及現金流維持年初至今口徑。"""
    missing_sources = {"income", "balance", "cashflow"} - set(sources)
    if missing_sources:
        raise ValueError(f"缺來源中繼資料：{sorted(missing_sources)}")
    # 三個位置各自驗報表種類，避免錯置的原件只因「有公司列」就被合併成錯誤欄位。
    for statement, content in (("income", income_content), ("balance", balance_content), ("cashflow", cashflow_content)):
        _validate_statement_kind(statement, content)
    income = _statement_rows(income_content)
    balance = _statement_rows(balance_content)
    cashflow = _statement_rows(cashflow_content)
    if not income or not balance or not cashflow:
        raise ValueError("季財務原件缺少可解析的公司資料列")
    available_on, basis = _quarter_available_on(period)
    records: list[dict] = []
    for code in sorted(set(income) | set(balance) | set(cashflow)):
        income_cells, income_headers = income.get(code, ({}, set()))
        balance_cells, balance_headers = balance.get(code, ({}, set()))
        cashflow_cells, cashflow_headers = cashflow.get(code, ({}, set()))
        name = _first(income_cells, ("公司名稱",)) or _first(balance_cells, ("公司名稱",)) or _first(cashflow_cells, ("公司名稱",))
        general_form = {"流動資產", "非流動資產", "資產總計", "負債總計"}.issubset(balance_headers)
        standard_income = {"營業收入", "營業利益（損失）"}.issubset(income_headers)
        total_assets = _integer(balance_cells.get("資產總計", ""))
        total_liabilities = _integer(balance_cells.get("負債總計", ""))
        debt_ratio = None
        if general_form and total_assets not in (None, 0) and total_liabilities is not None:
            debt_ratio = round(total_liabilities / total_assets * 100, 6)
        ocf_field = "營業活動之淨現金流入（流出）"
        records.append(
            {
                "code": code,
                "name": _clean(name),
                "market": market,
                "period": period,
                "available_on": available_on,
                "available_on_basis": basis,
                "value_basis": "fiscal_year_to_date",
                "statement_form": "general" if general_form else "sector_specific",
                "revenue_thousand_twd": _integer(income_cells.get("營業收入", "")),
                "gross_profit_thousand_twd": _integer(
                    _first(income_cells, ("營業毛利（毛損）淨額", "營業毛利（毛損）"))
                ),
                "operating_income_thousand_twd": _integer(income_cells.get("營業利益（損失）", "")),
                "net_income_thousand_twd": _integer(income_cells.get("本期淨利（淨損）", "")),
                "eps_twd": _number(income_cells.get("基本每股盈餘（元）", "")),
                "operating_cash_flow_thousand_twd": _integer(cashflow_cells.get(ocf_field, "")),
                "total_assets_thousand_twd": total_assets,
                "total_liabilities_thousand_twd": total_liabilities,
                "debt_ratio_pct": debt_ratio,
                "units": {
                    "revenue_thousand_twd": "TWD_thousand",
                    "gross_profit_thousand_twd": "TWD_thousand",
                    "operating_income_thousand_twd": "TWD_thousand",
                    "net_income_thousand_twd": "TWD_thousand",
                    "eps_twd": "TWD_per_share",
                    "operating_cash_flow_thousand_twd": "TWD_thousand",
                    "total_assets_thousand_twd": "TWD_thousand",
                    "total_liabilities_thousand_twd": "TWD_thousand",
                    "debt_ratio_pct": "percent",
                },
                "applicability": {
                    "standard_income_statement": {
                        "applicable": standard_income,
                        "reason": None if standard_income else "sector_specific_statement_form",
                    },
                    "operating_cash_flow_quality": {
                        "applicable": general_form and ocf_field in cashflow_headers,
                        "reason": None if general_form and ocf_field in cashflow_headers else "sector_specific_statement_form",
                    },
                    "debt_ratio": {
                        "applicable": general_form,
                        "reason": None if general_form else "sector_specific_statement_form",
                    },
                },
                "sources": {kind: dict(meta) for kind, meta in sources.items()},
            }
        )
    return records


def parse_monthly_revenue(
    content: bytes,
    *,
    market: str,
    period: str,
    source_file: str,
    fetched_at_utc: str,
    sha256: str,
    source_url: str | None = None,
    source_scope: str = "domestic",
) -> list[dict]:
    """解析 MOPS 月營收彙總原件；金額單位維持原表的仟元。外國公司表亦以新台幣千元列示。"""
    if source_scope not in MONTHLY_SCOPE_SUFFIX:
        raise ValueError(f"未知月營收來源範圍：{source_scope}")
    parser = _TableParser()
    parser.feed(_decode_html(content))
    available_on, basis = _monthly_available_on(period)
    records: list[dict] = []
    for table in parser.tables:
        header: list[str] | None = None
        for row in table:
            normalized = [_header(cell) for cell in row]
            if "公司代號" in normalized and "當月營收" in normalized:
                header = normalized
                continue
            if header is None or len(row) < 3:
                continue
            cells = dict(zip(header, row))
            code = _clean(cells.get("公司代號", ""))
            if not re.fullmatch(r"[0-9A-Z]{4,6}", code):
                continue
            records.append(
                {
                    "code": code,
                    "name": _clean(cells.get("公司名稱", "")),
                    "market": market,
                    "period": period,
                    "available_on": available_on,
                    "available_on_basis": basis,
                    "revenue_thousand_twd": _integer(cells.get("當月營收", "")),
                    "unit": "TWD_thousand",
                    "source_scope": source_scope,
                    "source_file": source_file,
                    "source_url": source_url,
                    "fetched_at_utc": fetched_at_utc,
                    "source_sha256": sha256,
                }
            )
    if not records:
        raise ValueError("月營收原件缺少可解析的公司資料列")
    return records


def _write_once(path: Path, content: bytes) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_bytes(content)
    os.replace(partial, path)


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, path)


def _default_http_clients() -> tuple[Callable[[str], HttpResponse], Callable[[str, dict], HttpResponse]]:
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (Market--Ana official-data fetcher)"})

    def get(url: str) -> HttpResponse:
        response = session.get(url, timeout=90)
        return HttpResponse(response.status_code, response.headers.get("content-type", ""), response.content, response.url)

    def post(url: str, data: dict) -> HttpResponse:
        response = session.post(url, data=data, timeout=90, headers={"Referer": url.replace("ajax_", "")})
        return HttpResponse(response.status_code, response.headers.get("content-type", ""), response.content, response.url)

    return get, post


def _monthly_target(out: Path, market: str, period: str, scope: str = "domestic") -> Path:
    folder = "monthly" if scope == "domestic" else f"monthly-{scope}"
    return out / "raw" / folder / market / f"{period}.html"


def _quarter_target(out: Path, market: str, period: str, statement: str) -> Path:
    return out / "raw" / "quarterly" / market / period / f"{statement}.html"


def _validate_response(response: HttpResponse) -> None:
    if response.status in {403, 429}:
        raise RuntimeError(f"HTTP {response.status}")
    if response.status != 200:
        raise RuntimeError(f"HTTP {response.status}")
    if "html" not in response.content_type.lower():
        raise RuntimeError(f"非預期格式（{response.content_type or '無 content-type'}）")


def fetch_history(
    out: Path,
    *,
    monthly_periods: list[str],
    quarterly_periods: list[str],
    monthly_scopes: tuple[str, ...] = ("domestic",),
    get: Callable[[str], HttpResponse] | None = None,
    post: Callable[[str, dict], HttpResponse] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], str] = lambda: datetime.now(timezone.utc).isoformat(),
) -> dict:
    """單一 Session、至少五秒間隔抓官方原件；任何拒絕或格式異常即停整批。"""
    if get is None or post is None:
        default_get, default_post = _default_http_clients()
        get = get or default_get
        post = post or default_post
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "meta.json"
    try:
        manifest = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {
            "schema_version": 1,
            "request_policy": {"single_connection": True, "minimum_interval_seconds": MIN_INTERVAL_SECONDS},
            "point_in_time_limit": "歷史彙總值可能含後續更正；available_on 防止使用法定申報日前資料，但不保證還原原始申報版本。",
            "files": [],
        }
    known = {item["file"]: item for item in manifest.get("files", [])}
    summary: dict = {"fetched": 0, "existing": 0, "stopped": None, "hosts": ["mopsov.twse.com.tw"]}
    requested = False

    def request(call: Callable, *args) -> HttpResponse:
        nonlocal requested
        if requested:
            sleep(MIN_INTERVAL_SECONDS)
        requested = True
        try:
            response = call(*args)
        except Exception as error:
            raise RuntimeError(f"連線錯誤：{type(error).__name__}: {error}") from error
        _validate_response(response)
        return response

    def save(target: Path, response: HttpResponse, item: dict) -> None:
        relative = target.relative_to(out).as_posix()
        digest = hashlib.sha256(response.content).hexdigest()
        item.update(
            file=relative,
            source_url=response.url,
            fetched_at_utc=now(),
            sha256=digest,
            bytes=len(response.content),
        )
        _write_once(target, response.content)
        if relative not in known:
            manifest["files"].append(item)
            known[relative] = item
        manifest["updated_at_utc"] = item["fetched_at_utc"]
        _write_json_atomic(meta_path, manifest)
        summary["fetched"] += 1

    def use_existing(target: Path) -> bool:
        if not target.exists():
            return False
        relative = target.relative_to(out).as_posix()
        item = known.get(relative)
        if item is None:
            raise RuntimeError(f"原件存在但 meta 缺紀錄：{relative}")
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if digest != item.get("sha256"):
            raise RuntimeError(f"原件存在但 SHA-256 與 meta 不符：{relative}")
        summary["existing"] += 1
        return True

    try:
        for scope in monthly_scopes:
            suffix = MONTHLY_SCOPE_SUFFIX[scope]
            for period in monthly_periods:
                year, month = map(int, period.split("-"))
                available_on, available_basis = _monthly_available_on(period)
                for market, market_code in MARKET_CODES.items():
                    target = _monthly_target(out, market, period, scope)
                    if use_existing(target):
                        continue
                    roc_year = year - 1911
                    url = f"{MOPS_HOST}/nas/t21/{market_code}/t21sc03_{roc_year}_{month}_{suffix}.html"
                    response = request(get, url)
                    parse_monthly_revenue(
                        response.content,
                        market=market,
                        period=period,
                        source_file=target.relative_to(out).as_posix(),
                        fetched_at_utc="validation",
                        sha256="validation",
                        source_scope=scope,
                    )
                    save(
                        target,
                        response,
                        {
                            "dataset": "monthly_revenue",
                            "source_scope": scope,
                            "market": market,
                            "period": period,
                            "available_on": available_on,
                            "available_on_basis": available_basis,
                            "unit": "TWD_thousand",
                            "request_method": "GET",
                        },
                    )
        for period in quarterly_periods:
            year, quarter = period.split("-Q")
            available_on, available_basis = _quarter_available_on(period)
            for market, market_code in MARKET_CODES.items():
                for statement, endpoint in STATEMENT_ENDPOINTS.items():
                    target = _quarter_target(out, market, period, statement)
                    if use_existing(target):
                        continue
                    url = f"{MOPS_HOST}/mops/web/{endpoint}"
                    params = {
                        "encodeURIComponent": "1",
                        "step": "1",
                        "firstin": "1",
                        "off": "1",
                        "TYPEK": market_code,
                        "year": str(int(year) - 1911),
                        "season": f"0{quarter}",
                    }
                    response = request(post, url, params)
                    _validate_statement_kind(statement, response.content)
                    save(
                        target,
                        response,
                        {
                            "dataset": "quarterly_financials",
                            "statement": statement,
                            "market": market,
                            "period": period,
                            "available_on": available_on,
                            "available_on_basis": available_basis,
                            "unit": "TWD_thousand; EPS=TWD_per_share",
                            "request_method": "POST",
                            "request_parameters": params,
                        },
                    )
    except Exception as error:
        summary["stopped"] = f"{type(error).__name__}: {error}"
    summary["manifest_files"] = len(manifest.get("files", []))
    return summary


def load_history(out: Path) -> FundamentalsHistory:
    """驗 SHA 後由官方原件建立可查序列；不完整的季度原件拒絕載入。"""
    manifest = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    # 原件與 meta 必須一對一：寫入中斷留下的 .partial、未登記原件或 meta 指向不存在的檔都拒絕。
    listed_files = {item["file"] for item in manifest.get("files", [])}
    raw_root = out / "raw"
    on_disk = {path.relative_to(out).as_posix() for path in raw_root.rglob("*") if path.is_file()} if raw_root.exists() else set()
    unregistered = sorted(on_disk - listed_files)
    if unregistered:
        raise ValueError(f"原件與 meta 不一致：meta 缺 {unregistered[:5]}")
    monthly: list[dict] = []
    quarter_groups: dict[tuple[str, str], dict[str, tuple[bytes, dict]]] = {}
    for item in manifest.get("files", []):
        path = out / item["file"]
        if not path.is_file():
            raise ValueError(f"原件不存在：{item['file']}")
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest != item["sha256"]:
            raise ValueError(f"原件 SHA-256 不符：{item['file']}")
        if item["dataset"] == "monthly_revenue":
            monthly.extend(
                parse_monthly_revenue(
                    content,
                    market=item["market"],
                    period=item["period"],
                    source_file=item["file"],
                    fetched_at_utc=item["fetched_at_utc"],
                    sha256=item["sha256"],
                    source_url=item.get("source_url"),
                    source_scope=item.get("source_scope", "domestic"),
                )
            )
        elif item["dataset"] == "quarterly_financials":
            key = (item["market"], item["period"])
            quarter_groups.setdefault(key, {})[item["statement"]] = (content, item)
    # 國內表與國外表不應有同公司同月份；若出現代表來源口徑改變，拒絕載入而非重複計入。
    seen: dict[tuple[str, str], str] = {}
    for row in monthly:
        key = (row["code"], row["period"])
        if key in seen:
            raise ValueError(f"月營收重複：{key[0]} {key[1]} 同時出現在 {seen[key]} 與 {row['source_file']}")
        seen[key] = row["source_file"]
    quarterly: list[dict] = []
    for (market, period), statements in sorted(quarter_groups.items()):
        missing = set(STATEMENT_ENDPOINTS) - set(statements)
        if missing:
            raise ValueError(f"{market} {period} 季財務原件不完整：缺 {sorted(missing)}")
        sources = {
            kind: {
                "source_file": item["file"],
                "source_url": item.get("source_url"),
                "fetched_at_utc": item["fetched_at_utc"],
                "sha256": item["sha256"],
                "unit": item.get("unit"),
            }
            for kind, (_, item) in statements.items()
        }
        quarterly.extend(
            parse_quarterly_financials(
                statements["income"][0],
                statements["balance"][0],
                statements["cashflow"][0],
                market=market,
                period=period,
                sources=sources,
            )
        )
    return FundamentalsHistory(monthly=monthly, quarterly=quarterly)


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = map(int, start.split("-"))
    end_year, end_month = map(int, end.split("-"))
    periods: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        periods.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return periods


def main() -> int:
    default_out = Path(__file__).resolve().parent / "data" / "fundamentals-history"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=default_out)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="抓取官方歷史原件")
    fetch.add_argument("--monthly-start", required=True)
    fetch.add_argument("--monthly-end", required=True)
    fetch.add_argument("--quarters", nargs="*", default=[])
    fetch.add_argument("--monthly-scopes", nargs="+", default=["domestic"], choices=sorted(MONTHLY_SCOPE_SUFFIX))
    sub.add_parser("build", help="驗 SHA 並輸出解析後序列")
    query = sub.add_parser("query", help="查某日以前可取得的資料")
    query.add_argument("code")
    query.add_argument("as_of")
    args = parser.parse_args()
    if args.command == "fetch":
        summary = fetch_history(
            args.out,
            monthly_periods=month_range(args.monthly_start, args.monthly_end),
            quarterly_periods=args.quarters,
            monthly_scopes=tuple(args.monthly_scopes),
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 2 if summary["stopped"] else 0
    history = load_history(args.out)
    if args.command == "build":
        payload = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "point_in_time_limit": "available_on 防止使用申報截止日前資料；MOPS 歷史彙總可能含後續更正，不能冒稱完整 as-filed 回放。",
            **history.to_dict(),
        }
        _write_json_atomic(args.out / "history.json", payload)
        print(json.dumps({"monthly": len(payload["monthly"]), "quarterly": len(payload["quarterly"])}, ensure_ascii=False))
        return 0
    print(json.dumps(history.as_of(args.code, args.as_of), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
