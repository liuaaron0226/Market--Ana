"""大量股母體（liquid-v1）：本人 2026-11-09「整個網站都只做大量股」。

規則：上市＋上櫃普通股合併，以含資料日在內最近 20 個交易日的平均成交金額（元）排名。
前一日不在名單的股票要進入前 ENTER_RANK 名才納入；已在名單的股票跌出 EXIT_RANK 名才移出（遲滯，減少每日進出）。
窗口內任一天缺資料或成交金額為 NULL 就不排名（不補 0）；資料日停牌也不排名。
0050 為長期核心持有，固定可見，不參與排名。依據與證據見 .scratch/chart-screening-workbench/logs/lane-V1-slice1.md。

V2（2026-11-09）：籌碼排行的母體依 REA 包含 ETF（協調窗裁決），但只收「與普通股同口徑排名也在前
ENTER_RANK 名」的 ETF，遲滯同樣適用；ETF 不擠掉普通股名額（普通股名單與有沒有 ETF 無關）。
ETF 只進籌碼排行；搜尋、個股分析、資金流向成分股仍只限普通股加 0050。
ETF 行情不在行情庫（stock_history 只收 0050），改由 ``RawEtfSource`` 讀已驗 SHA 的原件。
"""
from __future__ import annotations

import argparse
import bisect
import contextlib
import gzip
import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

ROOT = Path(__file__).resolve().parent
RULE_VERSION = "liquid-v1"
WINDOW_DAYS = 20
ENTER_RANK = 300
EXIT_RANK = 400
CORE_HOLDINGS = ("0050",)
DEFAULT_SNAPSHOT_ROOT = ROOT / "data/liquid-universe"


class SnapshotConflict(Exception):
    pass


ETF_CODE = re.compile(r"00\d{2,4}[A-Z]?")


def is_etf_code(code: str) -> bool:
    """台股 ETF 代號：00 開頭 4～6 碼，可帶一個英文字尾（B 債券、L／R 槓反、A 主動式等）。"""
    return bool(ETF_CODE.fullmatch(code))


class EtfSource(Protocol):
    def series(self, days: list[str]) -> dict[str, dict]: ...


@dataclass(frozen=True)
class Sources:
    listed_db: Path
    otc_db: Path
    listed_codes: frozenset[str]
    otc_codes: frozenset[str]
    etfs: EtfSource | None = None


def _ro(db: Path) -> contextlib.closing:
    # closing 才會真的關檔；sqlite3 連線本身的 with 只管交易
    return contextlib.closing(sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True))


def _calendar(db: Path, as_of: str) -> list[str]:
    with _ro(db) as connection:
        return [row[0] for row in connection.execute(
            "SELECT DISTINCT date FROM daily WHERE date <= ? ORDER BY date", (as_of,))]


def _window(sources: Sources, as_of: str) -> list[str]:
    days = sorted(set(_calendar(sources.listed_db, as_of)) | set(_calendar(sources.otc_db, as_of)))
    if not days or days[-1] != as_of:
        raise ValueError(f"資料日 {as_of} 不在行情資料中")
    if len(days) < WINDOW_DAYS:
        raise ValueError(f"資料日 {as_of} 之前不足 {WINDOW_DAYS} 個交易日")
    return days[-WINDOW_DAYS:]


def _rows(db: Path, days: list[str], codes: frozenset[str]) -> dict[str, dict[str, tuple[int | None, int]]]:
    marks = ",".join("?" for _ in days)
    out: dict[str, dict[str, tuple[int | None, int]]] = {}
    with _ro(db) as connection:
        for code, date, turnover, suspended in connection.execute(
            f"SELECT code, date, turnover, suspended FROM daily WHERE date IN ({marks}) AND kind = 'common'", days
        ):
            if code in codes:
                out.setdefault(code, {})[date] = (turnover, suspended)
    return out


def _avg_or_reason(values: list[int | None], suspended_on_as_of: bool) -> float | str:
    if any(value is None for value in values):
        return "insufficient_window"
    if suspended_on_as_of:
        return "suspended_on_data_date"
    return sum(values) / WINDOW_DAYS


def _round_avg(avg: float) -> int | float:
    return int(avg) if float(avg).is_integer() else round(avg, 2)


def ranking(as_of: str, sources: Sources) -> tuple[list[dict], dict[str, str]]:
    """普通股排名（含不在名單內者的名次），供「不在涵蓋範圍，目前第 N 名」使用。"""
    ranked, excluded, _ = _ranking(as_of, sources)
    return ranked, excluded


def _ranking(as_of: str, sources: Sources) -> tuple[list[dict], dict[str, str], list[str]]:
    days = _window(sources, as_of)
    data = {code: ("上市", rows) for code, rows in _rows(sources.listed_db, days, sources.listed_codes).items()}
    for code, rows in _rows(sources.otc_db, days, sources.otc_codes).items():
        data.setdefault(code, ("上櫃", rows))
    ranked, excluded = [], {}
    for code, (market, rows) in data.items():
        values = [rows.get(day, (None, 0))[0] for day in days]
        if any(value is None for value in values):
            excluded[code] = "insufficient_window"
            continue
        if rows[as_of][1]:
            excluded[code] = "suspended_on_data_date"
            continue
        ranked.append({"code": code, "market": market, "avg_turnover_20d": sum(values) / WINDOW_DAYS})
    for code in sorted((sources.listed_codes | sources.otc_codes) - set(data)):
        excluded[code] = "no_trading_data"
    ranked.sort(key=lambda item: (-item["avg_turnover_20d"], item["code"]))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item["avg_turnover_20d"] = _round_avg(item["avg_turnover_20d"])
    return ranked, excluded, days


def _etf_members(ranked: list[dict], days: list[str], sources: Sources, previous: set[str],
                 enter_rank: int, exit_rank: int) -> tuple[list[dict], dict[str, str]]:
    """ETF 的「同口徑名次」＝插進普通股排名後的位置（同額以代號排序）；不改動普通股名次。"""
    if sources.etfs is None:
        return [], {}
    keys = [(-item["avg_turnover_20d"], item["code"]) for item in ranked]
    members, excluded = [], {}
    for code, item in sorted(sources.etfs.series(days).items()):
        rows = item["rows"]
        value = _avg_or_reason([rows.get(day, (None, 0))[0] for day in days], bool(rows.get(days[-1], (None, 0))[1]))
        if isinstance(value, str):
            excluded[code] = value
            continue
        avg = _round_avg(value)
        rank = bisect.bisect_left(keys, (-avg, code)) + 1
        if rank <= enter_rank or (code in previous and rank <= exit_rank):
            members.append({"code": code, "name": item.get("name"), "market": item["market"], "avg_turnover_20d": avg,
                            "equivalent_rank": rank, "retained_by_hysteresis": rank > enter_rank})
    members.sort(key=lambda member: (member["equivalent_rank"], member["code"]))
    return members, dict(sorted(excluded.items()))


def build_universe(as_of: str, sources: Sources, *, enter_rank: int = ENTER_RANK, exit_rank: int = EXIT_RANK,
                   previous: set[str] | None = None, previous_data_date: str | None = None) -> dict:
    ranked, excluded, days = _ranking(as_of, sources)
    previous = set(previous or ())
    members, dropped = [], []
    for item in ranked:
        if item["rank"] <= enter_rank:
            members.append({**item, "retained_by_hysteresis": False})
        elif item["code"] in previous and item["rank"] <= exit_rank:
            members.append({**item, "retained_by_hysteresis": True})
    etf_members, etf_excluded = _etf_members(ranked, days, sources, previous, enter_rank, exit_rank)
    kept = {member["code"] for member in members} | {member["code"] for member in etf_members}
    dropped = sorted(previous - kept)
    total = sum(item["avg_turnover_20d"] for item in ranked)
    share = sum(member["avg_turnover_20d"] for member in members) / total if total else None
    return {
        "rule_version": RULE_VERSION,
        "data_date": as_of,
        "window": {"start": days[0], "end": days[-1], "days": WINDOW_DAYS},
        "enter_rank": enter_rank,
        "exit_rank": exit_rank,
        "previous_data_date": previous_data_date,
        "members": members,
        "dropped": dropped,
        "excluded": dict(sorted(excluded.items())),
        "ranked_count": len(ranked),
        "turnover_share": round(share, 4) if share is not None else None,
        "core_holdings": list(CORE_HOLDINGS),
        "etf_members": etf_members,
        "etf_excluded": etf_excluded,
    }


def rolling_universe(as_of: str, sources: Sources, *, warmup: int = 60, enter_rank: int = ENTER_RANK,
                     exit_rank: int = EXIT_RANK) -> dict:
    """從固定的暖機起點逐日套遲滯，讓任何人在同一資料上重算都得到同一份名單。"""
    calendar = sorted(set(_calendar(sources.listed_db, as_of)) | set(_calendar(sources.otc_db, as_of)))
    eligible = calendar[WINDOW_DAYS - 1:]
    path = eligible[-warmup:] if len(eligible) >= warmup else eligible
    previous: set[str] | None = None
    previous_date = None
    result: dict = {}
    for day in path:
        result = build_universe(day, sources, enter_rank=enter_rank, exit_rank=exit_rank,
                                previous=previous, previous_data_date=previous_date)
        previous = {member["code"] for member in result["members"] + result["etf_members"]}
        previous_date = day
    result["warmup"] = {"start": path[0], "days": len(path)}
    return result


def is_member(universe: dict, code: str) -> bool:
    """搜尋與個股分析的涵蓋範圍：普通股名單加核心持有 0050（ETF 名單不算）。"""
    return code in universe["core_holdings"] or any(member["code"] == code for member in universe["members"])


def is_etf_member(universe: dict, code: str) -> bool:
    """籌碼排行用的 ETF 名單（同口徑前 ENTER_RANK 名，遲滯 EXIT_RANK）。"""
    return any(member["code"] == code for member in universe.get("etf_members", ()))


class RawEtfSource:
    """從已驗 SHA 的官方原件讀 ETF 每日成交金額與收盤（行情庫只收 0050，其餘 ETF 不在庫內）。

    上市：data/stock-history/raw/YYYY/MI_INDEX-YYYYMMDD.json.gz，解壓後內容 SHA 須與行情庫 source_days 相同。
    上櫃：data/otc-history/raw/YYYYMMDD.csv，SHA 須與同名 .sha256 相同。原件缺日或 SHA 不符的那天視為缺資料，不補 0。
    """

    def __init__(self, listed_db: Path | None = None, listed_raw: Path | None = None, otc_raw: Path | None = None) -> None:
        self._listed_db = listed_db or ROOT / "data/stock-history/stock-history.sqlite"
        self._listed_raw = listed_raw or ROOT / "data/stock-history"
        self._otc_raw = otc_raw or ROOT / "data/otc-history/raw"
        self._cache: dict[str, dict[str, dict[str, Any]]] = {}
        self._listed_sha: dict[str, str] | None = None

    def _listed_day(self, day: str) -> dict[str, dict[str, Any]]:
        import stock_history

        if self._listed_sha is None:
            with _ro(self._listed_db) as connection:
                self._listed_sha = dict(connection.execute("SELECT date, sha256 FROM source_days"))
        compact = day.replace("-", "")
        path = stock_history.raw_file(self._listed_raw, compact)
        if not path.exists():
            return {}
        content = gzip.decompress(path.read_bytes())
        if hashlib.sha256(content).hexdigest() != self._listed_sha.get(day):
            return {}
        table = stock_history.quote_table(json.loads(content.decode("utf-8")))
        if table is None:
            return {}
        out = {}
        for row in table["data"]:
            if is_etf_code(row[0]):
                parsed = stock_history.parse_row(compact, table["fields"], row)
                out[row[0]] = {"market": "上市", "name": parsed["name"], "turnover": parsed["turnover"],
                               "suspended": parsed["suspended"], "close": parsed["close"], "change": parsed["change"]}
        return out

    def _otc_day(self, day: str) -> dict[str, dict[str, Any]]:
        import otc_history

        compact = day.replace("-", "")
        path = self._otc_raw / f"{compact}.csv"
        sidecar = path.with_name(path.name + ".sha256")
        if not path.exists() or not sidecar.exists():
            return {}
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != sidecar.read_text(encoding="utf-8").split()[0]:
            return {}
        return {row["code"]: {"market": "上櫃", "name": row["name"], "turnover": row["turnover"],
                              "suspended": row["suspended"], "close": row["close"], "change": row["change"]}
                for row in otc_history.parse_csv(content, compact) if is_etf_code(row["code"])}

    def day(self, day: str) -> dict[str, dict[str, Any]]:
        if day not in self._cache:
            merged = self._otc_day(day)
            merged.update(self._listed_day(day))  # 同代號以上市原件為準（實務上不重疊）
            self._cache[day] = merged
        return self._cache[day]

    def series(self, days: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for day in days:
            for code, row in self.day(day).items():
                item = out.setdefault(code, {"market": row["market"], "name": row["name"], "rows": {}})
                item["rows"][day] = (row["turnover"], int(bool(row["suspended"])))
        return out

    def quotes(self, day: str) -> dict[str, dict]:
        return {code: {"close": row["close"], "change": row["change"], "suspended": bool(row["suspended"])}
                for code, row in self.day(day).items()}


def _digest(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key != "sha256"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def save_snapshot(universe: dict, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{universe['data_date']}.json"
    payload = {**universe, "sha256": _digest(universe)}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("sha256") != payload["sha256"]:
            raise SnapshotConflict(f"{target.name} 已存在且內容不同，不覆寫")
        return target
    partial = target.with_suffix(".json.partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(partial, target)
    return target


def load_snapshot(root: Path, data_date: str) -> dict:
    payload = json.loads((root / f"{data_date}.json").read_text(encoding="utf-8"))
    if payload.get("sha256") != _digest(payload):
        raise SnapshotConflict(f"{data_date}.json 指紋不符，內容可能被改過")
    return {key: value for key, value in payload.items() if key != "sha256"}


def default_sources(include_etfs: bool = False) -> Sources:
    from listed_catalog import load_listed_catalog
    from otc_catalog import load_otc_catalog

    listed_db = ROOT / "data/stock-history/stock-history.sqlite"
    listed_raw = sorted((ROOT / "data/listed-catalog/raw").glob("twse-isin-listed-*.html"))[-1]
    otc_raw = sorted((ROOT / "data/otc-catalog/raw").glob("tpex-isin-otc-*.html"))[-1]
    listed = load_listed_catalog(listed_raw, listed_raw.with_suffix(".meta.json"), listed_db)
    otc = load_otc_catalog(otc_raw, otc_raw.with_suffix(".meta.json"), listed_db)
    with _ro(listed_db) as connection:
        listed_codes = frozenset(row[0] for row in connection.execute(
            "SELECT DISTINCT code FROM daily WHERE kind = 'common'") if listed.lookup(row[0]) is not None)
    otc_db = ROOT / "data/otc-history/otc_history.sqlite"
    with _ro(otc_db) as connection:
        otc_codes = frozenset(row[0] for row in connection.execute(
            "SELECT DISTINCT code FROM daily") if otc.lookup(row[0]) is not None)
    return Sources(listed_db, otc_db, listed_codes, otc_codes - listed_codes,
                   RawEtfSource(listed_db) if include_etfs else None)


def main() -> int:
    parser = argparse.ArgumentParser(description="建立大量股母體快照（liquid-v1）")
    parser.add_argument("--as-of", required=True, help="資料日 YYYY-MM-DD")
    parser.add_argument("--snapshot-root", type=Path, required=True, help="快照目錄；驗證時請指向 .tmp")
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--no-etfs", action="store_true", help="不計算籌碼排行用的 ETF 名單")
    args = parser.parse_args()
    universe = rolling_universe(args.as_of, default_sources(include_etfs=not args.no_etfs), warmup=args.warmup)
    path = save_snapshot(universe, args.snapshot_root)
    print(json.dumps({"snapshot": str(path), "members": len(universe["members"]),
                      "etf_members": len(universe["etf_members"]),
                      "turnover_share": universe["turnover_share"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
