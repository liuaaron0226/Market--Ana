"""建立 0004 版本化上市＋上櫃普通股名錄與個股分析（契約 v2，每檔一筆）。

資料母體是 listed_catalog 的官方上市普通股名錄加 otc_catalog 的官方上櫃普通股名錄；
行情與分析只讀既有本機資料（上市讀原行情庫，上櫃讀 B 線上櫃日 K 庫）。
產物固定寫入呼叫者指定的隔離目錄，不覆寫 cloud/.stock-pack.json、cloud/dist 或行情 SQLite。

S2（2026-11-08）：本人定案「自創部分不保留」，改呼叫 twon_analysis.analyze_v2；三方案（preset）與總分不再產出。
契約：.scratch/chart-screening-workbench/stock-analysis-contract-v2.md。

V2（2026-11-09）：本人定案「整個網站都只做大量股」。完整建置時母體改為 liquid_universe 當日名單
（上市＋上櫃普通股 20 日均額前 300，跌出前 400 才移出），0004 只寫入名單內股票並附名次；0050 照舊為相容入口。
--codes 小樣本仍可指定名單外代號（測試用），名次欄為 NULL。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from listed_catalog import ListedSecurity, load_listed_catalog  # noqa: E402
from otc_catalog import OtcSecurity, load_otc_catalog  # noqa: E402
from twon_analysis.analyze_v2 import MIN_ANALYSIS_BARS, SCHEMA, analyze_v2  # noqa: E402

CATALOG_RAW = ROOT / "data/listed-catalog/raw/twse-isin-listed-20261108.html"
CATALOG_META = ROOT / "data/listed-catalog/raw/twse-isin-listed-20261108.meta.json"
HISTORY_DB = ROOT / "data/stock-history/stock-history.sqlite"
OTC_CATALOG_RAW = ROOT / "data/otc-catalog/raw/tpex-isin-otc-20261108.html"
OTC_CATALOG_META = ROOT / "data/otc-catalog/raw/tpex-isin-otc-20261108.meta.json"
OTC_HISTORY_DB = ROOT / "data/otc-history/otc_history.sqlite"
MIGRATION = ROOT / "cloud/migrations/0004_listed_stock_data.sql"
DEFAULT_OUT = ROOT / ".tmp/lane-A2/full"
# 契約 v2 §1／§9 已移除的欄位；seed 裡出現任何一個就拒絕，避免舊三方案或自創計分混進新版本。
RETIRED_KEYS = frozenset({
    "preset", "available_presets", "score", "score_complete", "score_note", "dimensions", "coverage",
    "strategies", "strategy_counts", "risk_flags", "trend", "formula", "params_hash", "sources",
})


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _read_only(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)


def _is_finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_qa(code: str, qa: object) -> None:
    if not isinstance(qa, dict) or qa.get("blocking") is not False:
        raise ValueError(f"payload QA 必須明確為 non-blocking：{code}")
    bars = qa.get("bars")
    if not isinstance(bars, int) or isinstance(bars, bool) or bars < MIN_ANALYSIS_BARS:
        raise ValueError(f"payload QA bars 不足或型別無效：{code}")


def _validate_strategy(
    code: str,
    strategy: object,
    *,
    institutional_day: Decimal | None,
    institutional_5d: Decimal | None,
) -> None:
    if not isinstance(strategy, dict):
        raise ValueError(f"payload strategy 必須是 object：{code}")
    if not isinstance(strategy.get("strategy_version"), str) or not strategy["strategy_version"]:
        raise ValueError(f"payload strategy 缺有效版本：{code}")
    if strategy.get("recommendation") not in {"持有", "分批進場", "觀望", "減碼"}:
        raise ValueError(f"payload strategy 建議無效：{code}")
    prices = strategy.get("prices")
    if not isinstance(prices, dict) or not {"entry", "add", "stop", "target"}.issubset(prices):
        raise ValueError(f"payload strategy 價位結構無效：{code}")
    if any(prices[key] is not None and not _is_finite_number(prices[key])
           for key in ("entry", "add", "stop", "target")):
        raise ValueError(f"payload strategy 價位必須是數字或 null：{code}")
    if not isinstance(strategy.get("reason"), str) or not strategy["reason"].strip():
        raise ValueError(f"payload strategy 缺明確理由：{code}")
    if strategy.get("evidence_strength") not in {"完整", "部分", "有限"}:
        raise ValueError(f"payload strategy 證據狀態無效：{code}")
    signals = strategy.get("signals")
    if not isinstance(signals, dict) or not {"positive", "risk", "institutional_day", "institutional_5d"}.issubset(signals):
        raise ValueError(f"payload strategy 訊號結構無效：{code}")
    for key in ("positive", "risk"):
        if not isinstance(signals[key], int) or isinstance(signals[key], bool) or signals[key] < 0:
            raise ValueError(f"payload strategy 訊號計數無效：{code}/{key}")
    for key in ("institutional_day", "institutional_5d"):
        if signals[key] is not None and not _is_finite_number(signals[key]):
            raise ValueError(f"payload strategy 法人訊號無效：{code}/{key}")
    expected = {
        "institutional_day": institutional_day,
        "institutional_5d": institutional_5d,
    }
    for key, value in expected.items():
        actual = signals[key]
        matches = actual is None and value is None
        if actual is not None and value is not None:
            matches = Decimal(str(actual)) == value
        if not matches:
            raise ValueError(f"payload strategy {key} 與 institutional 序列不一致：{code}")


def _parse_payload_date(code: str, value: object) -> date:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is None:
        raise ValueError(f"payload 資料日格式無效：{code}/{value}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"payload 資料日格式無效：{code}/{value}") from error


def _validate_institutional(code: str, rows: object, as_of: date) -> tuple[Decimal | None, Decimal | None]:
    if not isinstance(rows, list):
        raise ValueError(f"payload institutional 必須是 array：{code}")
    required = {"date", "foreign", "trust", "dealer", "total"}
    prior: date | None = None
    totals: list[Decimal] = []
    for row in rows:
        if not isinstance(row, dict) or not required.issubset(row):
            raise ValueError(f"payload institutional 列結構無效：{code}")
        if any(not _is_finite_number(row[key]) for key in required - {"date"}):
            raise ValueError(f"payload institutional 列型別無效：{code}")
        component_total = sum((Decimal(str(row[key])) for key in ("foreign", "trust", "dealer")), Decimal(0))
        reported_total = Decimal(str(row["total"]))
        if reported_total != component_total:
            raise ValueError(f"payload institutional total 與三分項不一致：{code}/{row.get('date')}")
        totals.append(reported_total)
        raw_date = row["date"]
        if not isinstance(raw_date, str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date) is None:
            raise ValueError(f"payload institutional 日期無效：{code}/{raw_date}")
        try:
            row_date = date.fromisoformat(raw_date)
        except ValueError as error:
            raise ValueError(f"payload institutional 日期無效：{code}/{raw_date}") from error
        if row_date > as_of:
            raise ValueError(f"payload institutional 日期超出資料日：{code}/{row.get('date')}")
        if prior is not None and row_date <= prior:
            raise ValueError(f"payload institutional 日期未嚴格遞增：{code}/{row['date']}")
        prior = row_date
    institutional_day = totals[-1] if totals else None
    institutional_5d = sum(totals[-5:], Decimal(0)) if len(totals) >= 5 else None
    return institutional_day, institutional_5d


def analysis_status(
    available_bars: int,
    latest_bar_date: str | None,
    latest_row_suspended: bool,
    data_date: str,
) -> str:
    """依既有分析最低根數與行情庫最新日分類，不補零也不猜測資料。"""
    if available_bars == 0 or latest_bar_date is None or latest_row_suspended:
        return "unavailable"
    if available_bars < MIN_ANALYSIS_BARS:
        return "insufficient"
    if latest_bar_date != data_date:
        return "stale"
    return "available"


def _check_payload(code: str, payload: object) -> None:
    if not isinstance(payload, dict) or payload.get("code") != code:
        raise ValueError(f"payload 與列鍵不一致：{code}")
    if payload.get("schema") != SCHEMA:
        raise ValueError(f"payload 不是個股分析契約 v2：{code}")
    retired = sorted(RETIRED_KEYS & payload.keys())
    if retired:
        raise ValueError(f"payload 含契約 v2 已移除的欄位：{code}/{','.join(retired)}")
    as_of = _parse_payload_date(code, payload.get("as_of"))
    _validate_qa(code, payload.get("qa"))
    for required in ("strategy", "institutional"):
        if required not in payload:
            raise ValueError(f"payload 缺少 {required}：{code}")
    institutional_day, institutional_5d = _validate_institutional(code, payload["institutional"], as_of)
    # strategy 只有兩種合法狀態：null（未驗證策略當日不可用），或完整通過驗證。
    if payload["strategy"] is not None:
        _validate_strategy(
            code,
            payload["strategy"],
            institutional_day=institutional_day,
            institutional_5d=institutional_5d,
        )


def _isolate_strategy(code: str, payload: dict) -> str | None:
    """未驗證策略不合格時把 strategy 改成 null 並回傳原因；核心欄位有問題照樣丟例外。

    每日核心發布不能被預設關閉的策略計算拖累（本人 11-12「先脫鉤再上線」）。
    """
    if not isinstance(payload, dict) or payload.get("strategy") is None:
        return None
    as_of = _parse_payload_date(code, payload.get("as_of"))
    institutional_day, institutional_5d = _validate_institutional(code, payload.get("institutional"), as_of)
    try:
        _validate_strategy(code, payload["strategy"],
                           institutional_day=institutional_day, institutional_5d=institutional_5d)
    except ValueError as error:
        payload["strategy"] = None
        return str(error)[:200]
    return None


def _validate_seed(manifest: dict, securities: Sequence[dict], analyses: Sequence[dict]) -> None:
    if manifest["catalog_count"] != len(securities):
        raise ValueError("catalog_count 與 staging 名錄列數不符")
    if manifest["analysis_count"] != len(analyses):
        raise ValueError("analysis_count 與 staging 分析列數不符")

    by_code: dict[str, dict] = {}
    for row in securities:
        code = row["code"]
        if code in by_code:
            raise ValueError(f"staging 名錄代號重複：{code}")
        by_code[code] = row

    compatibility_codes = set(manifest.get("compatibility_codes", []))
    found: set[str] = set()
    for row in analyses:
        code = row["code"]
        payload = row["payload"]
        if code in found:
            raise ValueError(f"分析列重複：{code}")
        found.add(code)
        security = by_code.get(code)
        is_compatibility = code in compatibility_codes
        if (security is None or security["analysis_status"] not in ("available", "stale")) and not is_compatibility:
            raise ValueError(f"分析列不屬於可分析名錄標的：{code}")
        _check_payload(code, payload)
        if is_compatibility and (security is not None or payload.get("kind") != "etf"):
            raise ValueError(f"相容入口不得混入普通股名錄且必須是 ETF：{code}")
        if security is not None and payload.get("market") != security["market"]:
            raise ValueError(f"payload 市場別與名錄不符：{code}")
        if payload.get("as_of") != row["as_of"] or payload.get("qa", {}).get("blocking"):
            raise ValueError(f"payload 資料日或 QA 不可用：{code}")
        if security is not None and payload["as_of"] != security["latest_bar_date"]:
            raise ValueError(f"payload 資料日與名錄最後行情日不一致：{code}")
        if is_compatibility and manifest.get("data_date") is not None and payload["as_of"] != manifest["data_date"]:
            raise ValueError(f"相容入口資料日與版本資料日不一致：{code}")

    for code, security in by_code.items():
        if security["analysis_status"] in ("available", "stale") and code not in found:
            raise ValueError(f"可分析標的缺分析列：{code}")
    for code in compatibility_codes:
        if code not in found:
            raise ValueError(f"相容入口缺分析列：{code}")


def install_staging_version(
    connection: sqlite3.Connection,
    manifest: dict,
    securities: Sequence[dict],
    analyses: Sequence[dict],
    outside: Sequence[dict] = (),
) -> None:
    """同一交易中完整寫入、核數，最後才切 active；失敗時保留原 active。``outside`` 是母體外標的的名次或原因。"""
    _validate_seed(manifest, securities, analyses)
    version = manifest["version"]
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT INTO stock_data_versions "
            "(version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count, universe_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                version,
                manifest["source_date"],
                manifest["source_url"],
                manifest["source_sha256"],
                manifest["built_at"],
                manifest["catalog_count"],
                manifest["analysis_count"],
                manifest.get("universe_rule"),
            ),
        )
        connection.executemany(
            "INSERT INTO listed_securities "
            "(version, code, name, market, security_type, isin, listed_date, industry, cfi_code, "
            "latest_bar_date, available_bars, analysis_status, universe_rank, universe_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    version,
                    row["code"],
                    row["name"],
                    row["market"],
                    row["security_type"],
                    row["isin"],
                    row["listed_date"],
                    row["industry"],
                    row["cfi_code"],
                    row["latest_bar_date"],
                    row["available_bars"],
                    row["analysis_status"],
                    row.get("universe_rank"),
                    row.get("universe_rule"),
                )
                for row in securities
            ],
        )
        connection.executemany(
            "INSERT INTO stock_analysis (version, code, as_of, payload) VALUES (?, ?, ?, ?)",
            [(version, row["code"], row["as_of"], _compact_json(row["payload"])) for row in analyses],
        )
        connection.executemany(
            "INSERT INTO universe_outside (version, code, name, market, universe_rank, reason, universe_rule) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(version, row["code"], row["name"], row["market"], row["universe_rank"], row["reason"], row["universe_rule"])
             for row in outside],
        )
        actual_catalog = connection.execute(
            "SELECT COUNT(*) FROM listed_securities WHERE version = ?", (version,)
        ).fetchone()[0]
        actual_analysis = connection.execute(
            "SELECT COUNT(*) FROM stock_analysis WHERE version = ?", (version,)
        ).fetchone()[0]
        if actual_catalog != manifest["catalog_count"]:
            raise ValueError("catalog_count 寫入後核數失敗")
        if actual_analysis != manifest["analysis_count"]:
            raise ValueError("analysis_count 寫入後核數失敗")
        old = connection.execute(
            "SELECT active_version FROM stock_data_state WHERE key = 'current'"
        ).fetchone()
        if old is None:
            connection.execute(
                "INSERT INTO stock_data_state (key, active_version, previous_version, updated_at) "
                "VALUES ('current', ?, NULL, ?)",
                (version, manifest["built_at"]),
            )
        else:
            connection.execute(
                "UPDATE stock_data_state SET previous_version = active_version, active_version = ?, updated_at = ? "
                "WHERE key = 'current'",
                (version, manifest["built_at"]),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def rollback_active_version(connection: sqlite3.Connection, updated_at: str) -> None:
    """原子交換 active／previous；不刪除任一版本。"""
    try:
        connection.execute("BEGIN IMMEDIATE")
        state = connection.execute(
            "SELECT active_version, previous_version FROM stock_data_state WHERE key = 'current'"
        ).fetchone()
        if state is None or state[1] is None:
            raise ValueError("沒有可回退的 previous_version")
        connection.execute(
            "UPDATE stock_data_state SET active_version = ?, previous_version = ?, updated_at = ? "
            "WHERE key = 'current'",
            (state[1], state[0], updated_at),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def _enumerate(catalog: object, label: str) -> list:
    # 目前官方普通股代號皆為四位數；這裡只把 0000..9999 當 lookup 的列舉空間，
    # 實際是否納入仍完全由官方名錄與 CFICode 決定。總數不符就拒絕，不能靜默漏股。
    items = [item for number in range(10_000) if (item := catalog.lookup(f"{number:04d}")) is not None]
    if len(items) != len(catalog):
        raise ValueError(f"{label}公開 lookup 列舉到 {len(items)} 檔，但官方名錄有 {len(catalog)} 檔")
    return items


def _all_catalog_securities() -> list[ListedSecurity]:
    return _enumerate(load_listed_catalog(CATALOG_RAW, CATALOG_META, HISTORY_DB), "上市")


def _otc_catalog_securities() -> list[OtcSecurity]:
    return _enumerate(load_otc_catalog(OTC_CATALOG_RAW, OTC_CATALOG_META, OTC_HISTORY_DB), "上櫃")


def catalog_securities() -> list[ListedSecurity | OtcSecurity]:
    """兩市場官方普通股母體；同一代號不得同時出現在兩市場。"""
    listed = _all_catalog_securities()
    otc = _otc_catalog_securities()
    overlap = sorted({item.code for item in listed} & {item.code for item in otc})
    if overlap:
        raise ValueError(f"代號同時出現在上市與上櫃名錄：{','.join(overlap)}")
    return listed + otc


def filter_to_universe(items: Sequence, universe: dict) -> tuple[list, dict[str, int]]:
    """只留 liquid_universe 普通股名單內的標的（ETF 名單只供籌碼排行，不進名錄）；回傳 (名單內標的, 名次)。"""
    ranks = {str(member["code"]): int(member["rank"]) for member in universe.get("members", ())}
    return [item for item in items if item.code in ranks], {code: rank for code, rank in ranks.items()
                                                            if any(item.code == code for item in items)}


def outside_rows(items: Sequence, universe: dict, ranked: Sequence[dict]) -> list[dict]:
    """母體外官方普通股：有名次的寫 below_rank＋名次；無法排名的寫 liquid_universe 的排除原因。"""
    members = {str(member["code"]) for member in universe.get("members", ())}
    rank_of = {str(item["code"]): int(item["rank"]) for item in ranked}
    excluded = universe.get("excluded") or {}
    rows = []
    for item in sorted(items, key=lambda security: security.code):
        if item.code in members:
            continue
        rank = rank_of.get(item.code)
        rows.append({"code": item.code, "name": item.name, "market": item.market, "universe_rank": rank,
                     "reason": "below_rank" if rank is not None else excluded.get(item.code, "no_trading_data"),
                     "universe_rule": universe["rule_version"]})
    return rows


def _load_universe(path: Path | None, data_date: str) -> dict:
    import liquid_universe

    if path is not None:
        universe = json.loads(path.read_text(encoding="utf-8"))
        universe.pop("sha256", None)
    else:
        universe = liquid_universe.rolling_universe(data_date, liquid_universe.default_sources())
    if universe.get("data_date") != data_date:
        raise ValueError(f"大量股母體資料日 {universe.get('data_date')} 與行情資料日 {data_date} 不一致")
    return universe


def _latest_rows(db_path: Path) -> tuple[str | None, dict[str, bool]]:
    connection = _read_only(db_path)
    try:
        data_date = connection.execute(
            "SELECT MAX(date) FROM daily WHERE open IS NOT NULL AND close IS NOT NULL"
        ).fetchone()[0]
        latest = connection.execute(
            "SELECT d.code, d.suspended FROM daily d "
            "JOIN (SELECT code, MAX(date) AS max_date FROM daily GROUP BY code) x "
            "ON x.code = d.code AND x.max_date = d.date"
        ).fetchall()
    finally:
        connection.close()
    return data_date, {code: bool(suspended) for code, suspended in latest}


def _history_state() -> tuple[str, dict[str, bool]]:
    """回傳上市行情庫的資料日與兩市場各代號最新列是否停牌。

    資料日以上市庫為準（live_server 共用，簽名不變）；兩庫日期一致性由 build_version 嚴格把關，
    這裡不拋錯，避免上櫃庫落後時連帶弄壞本機搜尋（落後的上櫃股自然被分類為 stale）。
    同一代號在上櫃庫有列時以上櫃庫為準，與 twon_analysis.data 的路由一致。
    """
    data_date, suspended = _latest_rows(HISTORY_DB)
    if not data_date:
        raise ValueError("行情庫沒有有效日 K")
    if OTC_HISTORY_DB.exists():
        _, otc_suspended = _latest_rows(OTC_HISTORY_DB)
        suspended = {**suspended, **otc_suspended}
    return data_date, suspended


def _otc_data_date() -> str | None:
    return _latest_rows(OTC_HISTORY_DB)[0] if OTC_HISTORY_DB.exists() else None


def _security_row(item: ListedSecurity | OtcSecurity, status: str) -> dict:
    return {
        "code": item.code,
        "name": item.name,
        "market": item.market,
        "security_type": item.security_type,
        "isin": item.isin,
        "listed_date": item.listed_date,
        "industry": item.industry,
        "cfi_code": item.cfi_code,
        "latest_bar_date": item.latest_bar_date,
        "available_bars": item.available_bars,
        "analysis_status": status,
    }


def _build_rows(
    items: Sequence[ListedSecurity | OtcSecurity],
    data_date: str,
    suspended: dict[str, bool],
    analyzer: Callable[..., dict] = analyze_v2,
) -> tuple[list[dict], list[dict], list[dict]]:
    securities: list[dict] = []
    analyses: list[dict] = []
    failures: list[dict] = []
    started = time.time()
    for index, item in enumerate(items, 1):
        status = analysis_status(
            item.available_bars,
            item.latest_bar_date,
            suspended.get(item.code, False),
            data_date,
        )
        if status in ("available", "stale"):
            try:
                payload = analyzer(item.code, "9999-12-31")
                isolated = _isolate_strategy(item.code, payload)
                if isolated or (isinstance(payload, dict) and "strategy" in payload and payload["strategy"] is None):
                    failures.append({"code": item.code, "scope": "strategy", "error": "StrategyUnavailable",
                                     "message": isolated or "strategy 計算失敗，已留空"})
                _check_payload(item.code, payload)
                if (
                    payload.get("as_of") != item.latest_bar_date
                    or payload.get("market") != item.market
                    or payload.get("qa", {}).get("blocking")
                ):
                    raise ValueError("分析 payload 的資料日、市場別或 QA 不符")
                analyses.append({"code": item.code, "as_of": payload["as_of"], "payload": payload})
            except Exception as error:
                status = "unavailable"
                failures.append({"code": item.code, "error": type(error).__name__, "message": str(error)[:200]})
        securities.append(_security_row(item, status))
        if index % 25 == 0 or index == len(items):
            print(f"{index}/{len(items)} 檔，分析列 {len(analyses)}，{time.time() - started:.0f} 秒", flush=True)
    return securities, analyses, failures


def _compatibility_rows(data_date: str, analyzer: Callable[..., dict]) -> list[dict]:
    """保留既有 0050 ETF 分析入口，但不把 ETF 放進普通股名錄。"""
    payload = analyzer("0050", "9999-12-31")
    _isolate_strategy("0050", payload)
    _check_payload("0050", payload)
    if payload.get("kind") != "etf" or payload.get("as_of") != data_date or payload.get("qa", {}).get("blocking"):
        raise ValueError("0050 ETF 相容入口分析不完整")
    return [{"code": "0050", "as_of": payload["as_of"], "payload": payload}]


def _write_seed_jsonl(path: Path, manifest: dict, securities: Iterable[dict], analyses: Iterable[dict],
                      outside: Iterable[dict] = ()) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as output:
        output.write(_compact_json({"type": "version", "data": manifest}) + "\n")
        for row in securities:
            output.write(_compact_json({"type": "listed_security", "data": row}) + "\n")
        for row in analyses:
            output.write(_compact_json({"type": "stock_analysis", "data": row}) + "\n")
        for row in outside:
            output.write(_compact_json({"type": "universe_outside", "data": row}) + "\n")


LEGACY_PACK = ROOT / "cloud/.stock-pack.json"


def _write_functions_pack(path: Path, data_date: str, analyses: Sequence[dict]) -> None:
    """產生 build.mjs 要的契約 v2 Functions 資料包（舊 309 檔子集合＋0050）。

    只寫到本次隔離輸出目錄；要不要複製成 cloud/.stock-pack.json 屬部署步驟，需另經本人授權。
    舊包只讀它的代號清單，不讀也不沿用舊 payload。
    """
    legacy_codes = set(json.loads(LEGACY_PACK.read_text(encoding="utf-8"))["results"]) if LEGACY_PACK.exists() else set()
    wanted = legacy_codes | {"0050"}
    results = {row["code"]: row["payload"] for row in analyses if row["code"] in wanted}
    pack = {"as_of": data_date, "analysis_schema": SCHEMA, "results": dict(sorted(results.items()))}
    path.write_text(_compact_json(pack), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def version_name(*, data_date: str, combined_sha256: str, liquid: bool,
                 selected_codes: Sequence[str] | None) -> str:
    """版本名用行情資料日：每天一個新版本。

    舊做法用名錄抓取日，名錄沒重抓時每天同名，D1 以 INSERT OR IGNORE 略過新資料、verify 失敗（11-10 Actions）。
    """
    suffix = combined_sha256[:12]
    if selected_codes:
        suffix += "-" + hashlib.sha256(",".join(sorted(selected_codes)).encode()).hexdigest()[:8]
    prefix = "liquid" if liquid else "twomarket"
    return f"{prefix}-{data_date.replace('-', '')}-{suffix}-v2"


def build_version(
    out_dir: Path,
    selected_codes: Sequence[str] | None = None,
    version: str | None = None,
    analyzer: Callable[..., dict] = analyze_v2,
    universe_path: Path | None = None,
    use_universe: bool | None = None,
) -> dict:
    """``use_universe`` 預設：完整建置時套用大量股母體，``selected_codes`` 小樣本時不套用（測試用）。"""
    if out_dir.exists():
        raise FileExistsError(f"輸出目錄已存在，為避免覆寫請改用新目錄：{out_dir}")
    partial = out_dir.with_name(f"{out_dir.name}.partial-{os.getpid()}")
    if partial.exists():
        raise FileExistsError(f"暫存輸出已存在，請人工確認後再處理：{partial}")
    data_date, suspended = _history_state()
    otc_data_date = _otc_data_date()
    if otc_data_date != data_date:
        # 同一版本不得混兩個資料日；上櫃庫若比上市庫新，上櫃股會被誤標 stale。在建暫存目錄前拒絕。
        raise ValueError(f"上櫃日 K 最新日 {otc_data_date} 與上市行情庫 {data_date} 不一致，請先補齊再建置")
    partial.mkdir(parents=True)

    metadata = json.loads(CATALOG_META.read_text(encoding="utf-8"))
    otc_metadata = json.loads(OTC_CATALOG_META.read_text(encoding="utf-8"))
    items = catalog_securities()
    full_catalog_count = len(items)
    if use_universe is None:
        use_universe = not selected_codes
    universe = _load_universe(universe_path, data_date) if use_universe else None
    ranks: dict[str, int] = {}
    outside: list[dict] = []
    if universe is not None:
        import liquid_universe

        ranked, _ = liquid_universe.ranking(data_date, liquid_universe.default_sources())
        outside = outside_rows(items, universe, ranked)
        items, ranks = filter_to_universe(items, universe)
    if selected_codes:
        wanted = set(selected_codes)
        by_code = {item.code: item for item in items}
        missing = sorted(wanted - by_code.keys())
        if missing:
            raise ValueError(f"不在官方上市／上櫃普通股名錄{'的大量股母體' if universe else ''}：{','.join(missing)}")
        items = [by_code[code] for code in sorted(wanted)]
    mode = ("sample" if selected_codes else "full") + ("-liquid" if universe else "")
    # 版本列只有一組來源欄位；兩市場原件 SHA 合成一個可重算的指紋，逐市場明細另存 manifest.sources。
    sources = [
        {"market": "上市", "source_url": metadata["source_url"], "source_sha256": metadata["sha256"],
         "source_date": metadata["retrieved_at_utc"][:10]},
        {"market": "上櫃", "source_url": otc_metadata["source_url"], "source_sha256": otc_metadata["sha256"],
         "source_date": otc_metadata.get("data_date", otc_metadata["retrieved_at_utc"][:10])},
    ]
    combined_sha256 = hashlib.sha256(
        "\n".join(f"{item['market']}:{item['source_sha256']}" for item in sources).encode("utf-8")
    ).hexdigest()
    if version is None:
        version = version_name(data_date=data_date, combined_sha256=combined_sha256,
                               liquid=universe is not None, selected_codes=selected_codes)
    built_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    securities, analyses, failures = _build_rows(items, data_date, suspended, analyzer)
    if universe is not None:
        for row in securities:
            row["universe_rank"] = ranks.get(row["code"])
            row["universe_rule"] = universe["rule_version"]
    analyses.extend(_compatibility_rows(data_date, analyzer))
    status_counts = dict(sorted(Counter(row["analysis_status"] for row in securities).items()))
    market_status_counts = {
        market: dict(sorted(Counter(row["analysis_status"] for row in securities if row["market"] == market).items()))
        for market in ("上市", "上櫃")
    }
    manifest = {
        "schema_version": "0004",
        "mode": mode,
        "version": version,
        "source_date": metadata["retrieved_at_utc"][:10],
        "source_url": " | ".join(item["source_url"] for item in sources),
        "source_sha256": combined_sha256,
        "sources": sources,
        "data_date": data_date,
        "built_at": built_at,
        "catalog_count": len(securities),
        "full_catalog_count": full_catalog_count,
        "analysis_count": len(analyses),
        "analysis_stock_count": status_counts.get("available", 0) + status_counts.get("stale", 0) + 1,
        "listed_analysis_stock_count": status_counts.get("available", 0) + status_counts.get("stale", 0),
        "analysis_status_counts": status_counts,
        "market_status_counts": market_status_counts,
        "compatibility_codes": ["0050"],
        "analysis_schema": SCHEMA,
        "failures": failures,
        "universe_rule": universe["rule_version"] if universe else None,
        "universe": None if universe is None else {
            "rule_version": universe["rule_version"],
            "data_date": universe["data_date"],
            "enter_rank": universe.get("enter_rank"),
            "exit_rank": universe.get("exit_rank"),
            "members": len(universe.get("members", ())),
            "turnover_share": universe.get("turnover_share"),
            "etf_members_excluded_from_catalog": len(universe.get("etf_members", ())),
            "outside_rows": len(outside),
            "outside_reasons": dict(sorted(Counter(row["reason"] for row in outside).items())),
        },
    }
    _validate_seed(manifest, securities, analyses)

    database = partial / "staging.sqlite"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(MIGRATION.read_text(encoding="utf-8"))
        install_staging_version(connection, manifest, securities, analyses, outside)
        connection.execute("VACUUM")
    finally:
        connection.close()

    seed = partial / "seed.jsonl"
    _write_seed_jsonl(seed, manifest, securities, analyses, outside)
    analysis_payload_bytes = sum(len(_compact_json(row["payload"]).encode("utf-8")) for row in analyses)
    largest_payload_bytes = max((len(_compact_json(row["payload"]).encode("utf-8")) for row in analyses), default=0)
    table_rows = 1 + len(securities) + len(analyses) + len(outside) + 1
    manifest["capacity"] = {
        "seed_bytes": seed.stat().st_size,
        "sqlite_bytes": database.stat().st_size,
        "analysis_payload_bytes": analysis_payload_bytes,
        "average_seed_bytes_per_catalog_stock": round(seed.stat().st_size / len(securities), 2) if securities else 0,
        "average_payload_bytes_per_analysis_stock": round(
            analysis_payload_bytes / (status_counts.get("available", 0) + status_counts.get("stale", 0) + 1), 2
        ),
        "largest_payload_bytes": largest_payload_bytes,
        "d1_table_rows": table_rows,
        "single_analysis_query_logical_rows": 2,
        "single_partial_search_candidate_rows": len(securities) + 2,
        "max_search_response_rows": 50,
    }
    functions_pack = partial / "stock-pack-v2.json"
    _write_functions_pack(functions_pack, data_date, analyses)
    manifest["files"] = {
        "seed.jsonl": {"bytes": seed.stat().st_size, "sha256": _sha256(seed)},
        "staging.sqlite": {"bytes": database.stat().st_size, "sha256": _sha256(database)},
        "stock-pack-v2.json": {"bytes": functions_pack.stat().st_size, "sha256": _sha256(functions_pack)},
    }
    (partial / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    os.replace(partial, out_dir)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--codes", help="逗號分隔的小樣本；省略時建置完整官方名錄")
    parser.add_argument("--version")
    parser.add_argument("--universe", type=Path, help="liquid_universe 快照 JSON；省略時依資料日重算")
    parser.add_argument("--all-securities", action="store_true", help="不套用大量股母體（V2 前的全名錄建置）")
    args = parser.parse_args()
    codes = tuple(code.strip() for code in args.codes.split(",") if code.strip()) if args.codes else None
    manifest = build_version(args.out.resolve(), codes, args.version, universe_path=args.universe,
                             use_universe=False if args.all_securities else None)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
