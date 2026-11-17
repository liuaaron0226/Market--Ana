"""基金當成一般個股（本人 2026-11-18，規格 .scratch/fund-as-stock/spec.md，03 票）：基金的名錄列、個股分析與淨值長日線。

每日建置（cloud/build_stock_pack.py）用 ``fund_rows`` 把 ``fund_compare.FUNDS`` 裡的每一檔寫成
- 基金名錄列（D1 ``fund_securities``，0026）：代碼、短名、全名、幣別、ISIN、淨值資料日、淨值筆數、分析狀態；
- 個股分析列（D1 ``stock_analysis``）：分析契約 v2 的 ``kind: "fund"``——報價是淨值（原幣），bars 只有日期與收盤（淨值）。
  操作策略（strategy）與價位（levels）要用只看收盤的規則另算（04 票），這張先回資料不足，不填假數字。
發布後（daily_update._publish_stock_history）用 ``history_payloads`` 把整段淨值寫進 D1 ``stock_history``，個股頁的圖往前拉用。
本機 live_server 用 ``fund_analysis``／``fund_history`` 給同一個契約。

淨值與配息資料沿用基金對照分支的 ``fund_compare.load_history``（瀏覽器回補原件＋每日抽取檔，逐檔核 SHA-256）。
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

import fund_compare
import fund_distributions

ANALYSIS_SCHEMA = "stock-analysis/v2"
HISTORY_SCHEMA = "stock-history/v1"
ANALYSIS_VERSION = "fund-nav-v1"
MARKET = "境外基金"
SECURITY_TYPE = "基金"
# 和個股分析的最低根數（twon_analysis.analyze_v2.MIN_ANALYSIS_BARS）相同；少於這麼多筆淨值就是資料不足、不給分析。
MIN_NAVS = 20
BARS_IN_ANALYSIS = 120   # 和個股分析一樣只帶最近 120 筆，更早的由長日線（history_payloads）補
NAV_LAG_NOTE = "淨值每天一個，約晚 3 個營業日公布"
Loader = Callable[..., dict[str, Any]]


def _navs_upto(history: dict[str, Any], as_of: str) -> list[tuple[str, float]]:
    """資料日之後的淨值不用（和個股一樣，一個版本只看到資料日為止）。"""
    return [(day, float(nav)) for day, nav in history["navs"] if day <= as_of]


def _status(navs: Sequence[tuple[str, float]], as_of: str) -> str:
    if not navs:
        return "unavailable"
    if len(navs) < MIN_NAVS:
        return "insufficient"
    # 開放資料本來就晚約 3 個營業日；超過 6 個平日沒有新淨值才算過期（fund_compare.STALE_WEEKDAYS）
    return "stale" if fund_compare.staleness(navs[-1][0], as_of=as_of) else "available"


def _quote(navs: Sequence[tuple[str, float]], currency: str) -> dict[str, Any]:
    day, nav = navs[-1]
    previous = navs[-2][1] if len(navs) >= 2 else None
    change = round(nav - previous, 4) if previous is not None else None
    chg_pct = round((nav / previous - 1) * 100, 2) if previous else None
    sign = "" if not change else "+" if change > 0 else "-"
    return {"close": nav, "previous_close": previous, "change": change, "chg_pct": chg_pct, "change_sign": sign,
            "nav_date": day, "currency": currency}


def fund_analysis(code: str, navs: Sequence[tuple[str, float]], status: str, *,
                  distributions: Sequence[dict[str, Any]], as_of: str) -> dict[str, Any]:
    """分析契約 v2 的基金版。``navs`` 已截到資料日、至少 MIN_NAVS 筆；``status`` 是 available 或 stale。
    ``distributions`` 是合併好的配息列，``as_of`` 是資料日：除息日在它之後的配息不用（06 票「配息」分類）。"""
    fund = fund_compare.FUNDS[code]
    return {
        "schema": ANALYSIS_SCHEMA, "analysis_version": ANALYSIS_VERSION,
        "code": code, "name": fund["short_name"], "full_name": fund["name"], "kind": "fund", "market": MARKET,
        "currency": fund["currency"], "isin": fund["isin"],
        "as_of": navs[-1][0], "requested_as_of": "9999-12-31", "analysis_status": status,
        "quote": _quote(navs, fund["currency"]),
        "qa": {"blocking": False, "bars": len(navs)},
        # 04 票：只看收盤的操作策略與壓力／支撐；在那之前回資料不足（個股頁不顯示策略、價位階梯寫「無」）。
        "strategy": None,
        "levels": {"status": "insufficient", "reason": "基金的操作價位尚未提供", "resistance": [], "support": []},
        "institutional": [],
        "bars": [{"date": day, "close": nav} for day, nav in navs[-BARS_IN_ANALYSIS:]],
        # 基金專屬：個股頁「配息」分類（06 票）；股票的分析沒有這一欄
        "distributions": fund_distributions.summary(distributions, as_of=as_of, currency=fund["currency"]),
        "notes": {"nav_lag": NAV_LAG_NOTE},
        "labels": {"not_advice": "分析數字是參考，不是買賣指令", "price_basis": f"基金淨值（{fund['currency']}），不含配息"},
    }


def fund_rows(as_of: str, *, loader: Loader = fund_compare.load_history,
              codes: Sequence[str] | None = None) -> tuple[list[dict], list[dict], list[dict]]:
    """每檔基金 → (名錄列, 分析列, 失敗)。讀不到資料的那一檔記成 unavailable 並寫進失敗清單，其他照常。"""
    directory: list[dict] = []
    analyses: list[dict] = []
    failures: list[dict] = []
    for code in codes or sorted(fund_compare.FUNDS):
        fund = fund_compare.FUNDS[code]
        try:
            history = loader(code, as_of=as_of)
            navs = _navs_upto(history, as_of)
        except (OSError, ValueError, KeyError) as error:
            history, navs = {}, []
            failures.append({"code": code, "scope": "fund", "error": type(error).__name__, "message": str(error)[:200]})
        status = _status(navs, as_of)
        if status in ("available", "stale"):
            analyses.append({"code": code, "as_of": navs[-1][0], "payload": fund_analysis(
                code, navs, status, distributions=history["distributions"], as_of=as_of)})
        directory.append({
            "code": code, "name": fund["short_name"], "full_name": fund["name"], "currency": fund["currency"],
            "isin": fund["isin"], "latest_bar_date": navs[-1][0] if navs else None, "available_bars": len(navs),
            "analysis_status": status,
        })
    return directory, analyses, failures


def _squash(text: str) -> str:
    return "".join(str(text).split()).upper()


def search_items(term: str, *, as_of: str, loader: Loader = fund_compare.load_history,
                 source_date: str | None = None) -> list[dict[str, Any]]:
    """本機 live_server 的搜尋：和雲端 gate 的 SQL 同一組規則——代碼不分大小寫完全相同，或名稱／全名去掉空白後含打的字。
    回傳和雲端搜尋 items 同形狀的列（類別「基金」、市場「境外基金」、沒有名次）。"""
    needle = _squash(term)
    if not needle:
        return []
    codes = [code for code, fund in sorted(fund_compare.FUNDS.items())
             if code == needle or needle in _squash(fund["short_name"]) or needle in _squash(fund["name"])]
    if not codes:
        return []
    rows, _, _ = fund_rows(as_of, loader=loader, codes=codes)
    return [{"code": row["code"], "name": row["name"], "market": MARKET, "security_type": SECURITY_TYPE,
             "latest_bar_date": row["latest_bar_date"], "available_bars": row["available_bars"],
             "analysis_status": row["analysis_status"], "universe_rank": None, "universe_rule": None,
             "data_date": row["latest_bar_date"], "analysis_available": row["analysis_status"] in ("available", "stale"),
             "source_date": source_date} for row in rows]


def fund_history(code: str, navs: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """長日線（欄位式，和 cloud/build_stock_history.history_payload 同格式）；基金只有日期（d）與淨值（c）。"""
    return {"schema": HISTORY_SCHEMA, "code": code, "as_of": navs[-1][0],
            "d": [int(day.replace("-", "")) for day, _ in navs], "c": [nav for _, nav in navs]}


def history_payloads(as_of: str, *, loader: Loader = fund_compare.load_history) -> dict[str, dict[str, Any]]:
    """每檔基金到資料日為止的整段淨值；讀不到或沒有淨值的那一檔略過（圖維持分析帶的最近 120 筆）。"""
    built: dict[str, dict[str, Any]] = {}
    for code in sorted(fund_compare.FUNDS):
        try:
            navs = _navs_upto(loader(code, as_of=as_of), as_of)
        except (OSError, ValueError, KeyError):
            continue
        if navs:
            built[code] = fund_history(code, navs)
    return built
