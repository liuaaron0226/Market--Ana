"""G3：籌碼選股與產業資金流向的發布封存，以及長期核心持有（0050／2330）對照。

本人 2026-11-08 定案：自創候選規則退役，首頁改為依金融獵場 REA 實作的籌碼選股與產業資金流向；
長期組顯示本人觀點「核心持有 0050，附 2330 對照」。對外名稱本站自訂，不沿用原站命名。

- 發布：在指定資料日對 ``finhunt_screens`` 每個分頁的每組 REA 天數／方向、以及
  ``sector_rotation`` 時間軸各封存一份 JSON：``<root>/<kind>/<data_date>.json``。
  正規化 SHA-256 封存與原子寫入沿用 ``candidate_snapshots`` 的做法；unknown／unavailable／partial
  照實留存；同批重跑不動，內容不同拒絕覆寫。資料日當天收盤前不得發布。
- 核心持有：0050、2330 自 2016 起的含息總報酬（現金股利除息日以收盤再投入、分割依參考價），
  演算與 long-v2 研究腳本相同，唯讀本機日 K 與官方除權息表，未扣交易成本與稅。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Mapping, Sequence
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import finhunt_screens as fs

ROOT = Path(__file__).resolve().parent
SCHEMA_VERSION = "market-screen-snapshot-v1"
ROTATION_KIND = "rotation"
UNIVERSE_KIND = "universe"  # V2：當日大量股母體（liquid_universe）快照，發布第一步封存
DEFAULT_SNAPSHOT_ROOT = ROOT / "data/market-screen-snapshots"
STOCK_HISTORY_DB = ROOT / "data/stock-history/stock-history.sqlite"
LISTED_INSTITUTIONAL_DB = ROOT / "data/listed-institutional/listed_institutional.sqlite"
CORPORATE_EVENTS = ROOT / "data/corporate-actions/events.json"
TAIPEI = timezone(timedelta(hours=8))
MARKET_CLOSE_HOUR_MINUTE = (13, 30)

CORE_CODES = ("0050", "2330")
CORE_START = "2016-01-04"
CORE_ROLE = {"0050": "核心持有", "2330": "對照"}
CORE_NOTES = (
    "含息總報酬：現金股利於除息日以收盤價再投入；分割依官方表或參考價換算股數。",
    "未扣交易成本與稅；0050 淨值已含內扣費用。",
    "過去表現不代表未來；2330 為單一個股對照，集中風險高於 0050。",
)
CHART_POINTS = 160

# 對外顯示的條件文字（本站用語）。finhunt_screens 的 conditions 保留原樣供稽核，但其中兩頁仍含原站用詞，
# 畫面與 API 一律改用這份；key 沒列到的分頁沿用引擎文字。
DISPLAY_CONDITIONS = {
    # R1／V2：位階規則已由會員研究確認（127 檔全對），原站條件文字的象限限制實際不起作用。
    "rotation_relative_value": ("所屬產業（官方產業別）的成分股", "個股 20 日乖離低於所屬市場指數的 20 日乖離"),
    "rotation_etf_constituent": ("所屬產業近 5 日資金加速流入（Y≥0）的成分股", "主動式或被動式 ETF 成分股（範圍未公開）"),
}


def display_conditions(key: str, conditions: Sequence[str]) -> list[str]:
    return list(DISPLAY_CONDITIONS.get(key, conditions))


class ScreenSnapshotConflict(Exception):
    """同一 kind／資料日已有內容不同的封存；舊結果不得覆寫。"""


class ScreenSnapshotIntegrityError(Exception):
    """封存雜湊不符（內容被改動或未經本模組封存）。"""


# ---- 發布計畫 ----

def screen_kind(key: str, days: int, side: str) -> str:
    return f"screen-{key}-{days}d-{side}"


def publish_plan() -> list[dict[str, Any]]:
    """每個分頁 × 其 REA 天數選項 × 買／賣；排行與連續各自依原站天數，其他狀態頁只留一份。"""
    plan: list[dict[str, Any]] = []
    for tab in fs.TABS:
        if tab.kind in ("rank", "streak"):
            for days in tab.day_options:
                for side in ("buy", "sell"):
                    plan.append({"key": tab.key, "days": days, "side": side, "kind": screen_kind(tab.key, days, side)})
        else:
            plan.append({"key": tab.key, "days": tab.day_options[0], "side": "buy",
                         "kind": screen_kind(tab.key, tab.day_options[0], "buy")})
    return plan


# ---- 封存（正規化 JSON 的 SHA-256，與 candidate_snapshots 相同做法） ----

def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def seal_hash(snapshot: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical({k: v for k, v in snapshot.items() if k != "snapshot_sha256"})).hexdigest()


def _check_publish_time(data_date: str, published_at: str) -> None:
    moment = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError("published_at 必須帶時區")
    hour, minute = MARKET_CLOSE_HOUR_MINUTE
    close = datetime.fromisoformat(data_date).replace(hour=hour, minute=minute, tzinfo=TAIPEI)
    if moment.astimezone(TAIPEI) < close:
        raise ValueError("發布時間早於資料日收盤，不能發布以該日為資料日的批次")


def build_batch(kind: str, data_date: str, result: Mapping[str, Any], *, published_at: str,
                input_fingerprints: Mapping[str, str], variant: Mapping[str, Any] | None = None) -> dict[str, Any]:
    _check_publish_time(data_date, published_at)
    batch = {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "data_date": data_date,
        "published_at": published_at,
        "engine_version": {ROTATION_KIND: result.get("method"), UNIVERSE_KIND: result.get("rule_version")}.get(
            kind, fs.ENGINE_VERSION),
        "variant": dict(variant or {}),
        "result": json.loads(json.dumps(result, ensure_ascii=False)),
        "input_fingerprints": dict(input_fingerprints),
    }
    batch["snapshot_sha256"] = seal_hash(batch)
    return batch


def _path(root: Path, kind: str, data_date: str) -> Path:
    if not kind or "/" in kind or "\\" in kind or kind.startswith("."):
        raise ValueError("kind 不正確")
    date.fromisoformat(data_date)
    return root / kind / f"{data_date}.json"


def read_batch(root: Path, kind: str, data_date: str) -> dict[str, Any]:
    batch = json.loads(_path(root, kind, data_date).read_text(encoding="utf-8"))
    if batch.get("snapshot_sha256") != seal_hash(batch):
        raise ScreenSnapshotIntegrityError("封存雜湊不符：內容被改動或未經封存")
    return batch


def write_batch(root: Path, batch: Mapping[str, Any]) -> str:
    """原子寫入；同內容重試回 unchanged，不同內容丟 ScreenSnapshotConflict。"""
    if batch.get("snapshot_sha256") != seal_hash(batch):
        raise ScreenSnapshotIntegrityError("未經封存的批次不能寫入")
    target = _path(root, batch["kind"], batch["data_date"])
    if target.exists():
        if read_batch(root, batch["kind"], batch["data_date"])["snapshot_sha256"] == batch["snapshot_sha256"]:
            return "unchanged"
        raise ScreenSnapshotConflict(f"{target} 已有不同內容；不覆寫舊結果")
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{os.getpid()}.partial")
    partial.write_bytes(json.dumps(batch, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8") + b"\n")
    try:
        os.link(partial, target)  # 目標已存在時失敗，避免兩個發布者互相覆寫
    except FileExistsError:
        if read_batch(root, batch["kind"], batch["data_date"])["snapshot_sha256"] != batch["snapshot_sha256"]:
            raise ScreenSnapshotConflict(f"{target} 已被另一次發布寫入不同內容") from None
        return "unchanged"
    finally:
        partial.unlink(missing_ok=True)
    return "written"


def latest_batch(root: Path, kind: str) -> dict[str, Any] | None:
    folder = _path(root, kind, "2000-01-01").parent
    dates = sorted(p.stem for p in folder.glob("*.json") if not p.name.startswith(".")) if folder.is_dir() else []
    return read_batch(root, kind, dates[-1]) if dates else None


# ---- 發布 ----

def _rotation_for_screens(timeline: Mapping[str, Any], groups: Mapping[str, Sequence[str]]) -> list[dict[str, Any]]:
    """把資金流向時間軸轉成 run_screen 需要的 [{date, groups:[{name,y,codes}]}]（Y≥0 判斷在 finhunt_screens）。"""
    return [{"date": snap["date"],
             "groups": [{"name": b["name"], "y": b.get("y"), "codes": list(groups.get(b["name"], ()))}
                        for b in snap["bubbles"]]}
            for snap in timeline.get("snapshots", [])]


def publish(root: Path, *, data_date: str, published_at: str, sources: Iterable[fs.MarketSource],
            universe: Iterable[fs.Security], rotation: Mapping[str, Any] | None,
            groups: Mapping[str, Sequence[str]], input_fingerprints: Mapping[str, str],
            liquid_universe: Mapping[str, Any] | None = None, bias: Mapping[str, Any] | None = None,
            strong_stocks: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """``universe`` 是普通股候選；有 ``liquid_universe`` 時，排行母體改為大量股名單（含名單內 ETF），
    並先封存該名單。``bias`` 給相對低位階頁（族群＝``groups``）。資金流向的族群成交額不受母體影響。
    ``strong_stocks``（sector_rotation.strong_stocks 的結果）跟產業資金流向封存在同一批。"""
    if not input_fingerprints:
        raise ValueError("缺少輸入資料指紋，無法重現本批結果")
    if strong_stocks is not None and strong_stocks.get("date") != data_date:
        raise ValueError("各產業強勢股的資料日與發布資料日不一致")
    _check_publish_time(data_date, published_at)
    sources, universe = list(sources), list(universe)
    batches = []
    if liquid_universe is not None:
        if liquid_universe.get("data_date") != data_date:
            raise ValueError("大量股母體的資料日與發布資料日不一致")
        batches.append(build_batch(UNIVERSE_KIND, data_date, liquid_universe, published_at=published_at,
                                   input_fingerprints=input_fingerprints))
        universe = fs.liquid_universe_securities(liquid_universe, universe)
    rotation_rows = _rotation_for_screens(rotation, groups) if rotation else None
    for variant in publish_plan():
        result = fs.run_screen(variant["key"], sources, universe, direction=variant["side"],
                               days=variant["days"], rotation=rotation_rows, groups=groups, bias=bias)
        result["display_conditions"] = display_conditions(variant["key"], result.get("conditions", ()))
        batches.append(build_batch(variant["kind"], data_date, result, published_at=published_at,
                                   input_fingerprints=input_fingerprints, variant=variant))
    if rotation is not None:
        rotation_result = {**rotation, "groups": {name: list(codes) for name, codes in groups.items()}}
        if strong_stocks is not None:
            rotation_result["strong_stocks"] = strong_stocks
        batches.append(build_batch(ROTATION_KIND, data_date, rotation_result, published_at=published_at,
                                   input_fingerprints=input_fingerprints))
    # 先全部封存成功再寫，任何一份衝突就整批停（已寫入的同批檔案內容相同，不受影響）。
    outcomes: dict[str, str] = {}
    for batch in batches:
        outcomes[batch["kind"]] = write_batch(root, batch)
    counts = {"written": sum(v == "written" for v in outcomes.values()),
              "unchanged": sum(v == "unchanged" for v in outcomes.values())}
    return {"data_date": data_date, "counts": counts, "outcomes": outcomes}


# ---- 核心持有：0050／2330 含息總報酬 ----

def _resolve_action(event: Mapping[str, Any] | None, row: Mapping[str, Any], previous_close: float):
    import backtest  # 同一份公司行動規則（研究腳本也用它）

    return backtest.resolve_action(event, row, previous_close)


def total_return_series(code: str, rows: Iterable[Mapping[str, Any]],
                        events: Mapping[tuple[str, str], Mapping[str, Any]]) -> list[tuple[str, float]]:
    shares, prev, first = Decimal("1"), None, None
    series: list[tuple[str, float]] = []
    for row in rows:
        if row["close"] is None:
            continue
        if prev is not None:
            resolved = _resolve_action(events.get((code, row["date"])), row, prev)
            if resolved:
                per_share_cash, ratio, _ = resolved
                cash = shares * per_share_cash
                shares *= ratio
                if cash:
                    shares += cash / Decimal(str(row["close"]))
        value = float(shares) * row["close"]
        if first is None:
            first = value
        series.append((row["date"], value / first))
        prev = row["close"]
    return series


def _value_on_or_before(series: Sequence[tuple[str, float]], day: str) -> float | None:
    found = None
    for d, value in series:
        if d > day:
            break
        found = value
    return found


def series_stats(series: Sequence[tuple[str, float]]) -> dict[str, Any]:
    start, end = series[0][0], series[-1][0]
    years = (date.fromisoformat(end) - date.fromisoformat(start)).days / 365.25
    total = series[-1][1] / series[0][1]
    peak, mdd = 0.0, 0.0
    for _, value in series:
        peak = max(peak, value)
        mdd = min(mdd, value / peak - 1)
    trailing: dict[str, float | None] = {}
    end_date = date.fromisoformat(end)
    for label, n in (("1y", 1), ("3y", 3), ("5y", 5)):
        anchor = end_date.replace(year=end_date.year - n).isoformat()
        base = _value_on_or_before(series, anchor) if start <= anchor else None
        trailing[label] = None if base is None else round((series[-1][1] / base - 1) * 100, 1)
    return {
        "start": start, "end": end,
        "total_multiple": round(total, 2),
        "cagr_pct": round((total ** (1 / years) - 1) * 100, 1) if years > 0 else None,
        "max_drawdown_pct": round(mdd * 100, 1),
        "trailing_pct": trailing,
    }


def _downsample(series: Sequence[tuple[str, float]], points: int = CHART_POINTS) -> list[list[Any]]:
    if len(series) <= points:
        picked = list(series)
    else:
        step = (len(series) - 1) / (points - 1)
        picked = [series[round(i * step)] for i in range(points)]
    return [[d, round(v, 4)] for d, v in picked]


_CORE_CACHE: tuple[tuple[float, float, str | None], dict[str, Any]] | None = None
_CORE_LOCK = threading.Lock()


def core_holdings(history_db: Path = STOCK_HISTORY_DB, events_path: Path = CORPORATE_EVENTS,
                  as_of: str | None = None) -> dict[str, Any]:
    """唯讀計算並依資料檔 mtime 快取。

    ``as_of``（YYYY-MM-DD）只計到該日為止；省略時照舊算到行情庫最新一天（預設行為不變）。
    """
    global _CORE_CACHE
    key = (history_db.stat().st_mtime, events_path.stat().st_mtime, as_of)
    with _CORE_LOCK:
        if _CORE_CACHE is not None and _CORE_CACHE[0] == key:
            return _CORE_CACHE[1]
        events = {(e["code"], e["date"]): e for e in json.loads(events_path.read_text(encoding="utf-8"))
                  if e["code"] in CORE_CODES}
        holdings = []
        with closing(sqlite3.connect(f"file:{history_db.as_posix()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            for code in CORE_CODES:
                rows = connection.execute(
                    "SELECT date, close, change_sign, change FROM daily WHERE code = ? AND date >= ? AND date <= ? "
                    "ORDER BY date",
                    (code, CORE_START, as_of or "9999-12-31")).fetchall()
                name = connection.execute("SELECT name FROM daily WHERE code = ? ORDER BY date DESC LIMIT 1",
                                          (code,)).fetchone()
                series = total_return_series(code, rows, events)
                holdings.append({"code": code, "name": name[0] if name else code, "role": CORE_ROLE[code],
                                 "stats": series_stats(series), "chart": _downsample(series)})
        report = {"title": "長期核心持有", "role": dict(CORE_ROLE), "holdings": holdings,
                  "basis": "含息總報酬，起點 2016-01-04＝1", "notes": list(CORE_NOTES)}
        _CORE_CACHE = (key, report)
        return report


# ---- 本機資料來源 ----

class ListedSqliteSource:
    """上市法人（listed_institutional.sqlite）＋日行情（stock-history.sqlite）唯讀轉接，介面同 fs.MarketSource。"""

    market = "上市"

    def __init__(self, inst_db: Path = LISTED_INSTITUTIONAL_DB, history_db: Path = STOCK_HISTORY_DB) -> None:
        self._inst_db, self._history_db = inst_db, history_db
        self._cache: dict[str, dict] = {}

    @staticmethod
    def _connect(path: Path) -> closing:
        return closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True))

    def trading_days(self) -> list[str]:
        with self._connect(self._inst_db) as connection:
            return [r[0] for r in connection.execute("SELECT DISTINCT date FROM institutional ORDER BY date")]

    def institutional(self, day: str) -> dict[str, dict]:
        if day not in self._cache:
            with self._connect(self._inst_db) as connection:
                self._cache[day] = {code: {"foreign": f, "trust": t} for code, f, t in connection.execute(
                    "SELECT code, foreign_net, trust_net FROM institutional WHERE date = ?", (day,))}
        return self._cache[day]

    def quotes(self, day: str) -> dict[str, dict]:
        with self._connect(self._history_db) as connection:
            return {code: {"close": close, "change": change, "suspended": bool(suspended)}
                    for code, close, change, suspended in connection.execute(
                        "SELECT code, close, change, suspended FROM daily WHERE date = ?", (day,))}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _WithEtfQuotes:
    """行情庫只收 0050；大量股 ETF 的收盤與漲跌改由已驗 SHA 的原件補上，法人資料照原來源。"""

    def __init__(self, base: Any, etfs: Any) -> None:
        self.market, self._base, self._etfs = base.market, base, etfs

    def trading_days(self) -> list[str]:
        return self._base.trading_days()

    def institutional(self, day: str) -> dict[str, dict]:
        return self._base.institutional(day)

    def quotes(self, day: str) -> dict[str, dict]:
        merged = {code: quote for code, quote in self._etfs.quotes(day).items()}
        merged.update(self._base.quotes(day))
        return merged


def local_inputs(as_of: str, universe_root: Path | None = None) -> dict[str, Any]:
    import liquid_universe as lu
    import sector_rotation as sr

    sources_lu = lu.default_sources(include_etfs=True)
    liquid = lu.rolling_universe(as_of, sources_lu)
    if universe_root is not None:
        lu.save_snapshot(liquid, universe_root)
    official = sr.load_official_universe()
    groups = sr.official_industry_groups(official)
    dates, turnover = sr.load_turnover([c for c, _, _ in official], as_of, sr.TIMELINE_DAYS + sr.WINDOW_LONG - 1)
    rotation = sr.compute_timeline(groups, turnover, dates)
    strong = sr.strong_stocks(groups, sr.load_quotes([c for c, _, _ in official], dates[-sr.STRONG_WINDOW:]), dates)
    names: dict[str, str] = {}
    with closing(sqlite3.connect(f"file:{STOCK_HISTORY_DB.as_posix()}?mode=ro", uri=True)) as connection:
        for code, name in connection.execute(
                "SELECT code, name FROM daily WHERE kind = 'common' AND date = (SELECT MAX(date) FROM daily)"):
            names[code] = name
    listed = [fs.Security(code, names.get(code, code), "上市") for code, _, market in official if market == "上市"]
    universe = listed + fs.otc_universe()
    member_codes = {member["code"] for member in liquid["members"]}
    bias = fs.load_bias20(member_codes, as_of)
    fingerprints = {
        "stock_history": _sha256(STOCK_HISTORY_DB)[:16],
        "listed_institutional": _sha256(LISTED_INSTITUTIONAL_DB)[:16],
        "otc_market": _sha256(ROOT / "data/otc-history/otc_market.sqlite")[:16],
        "otc_history": _sha256(ROOT / "data/otc-history/otc_history.sqlite")[:16],
        "liquid_universe": lu._digest(liquid)[:16],
    }
    etfs = sources_lu.etfs
    return {"sources": [_WithEtfQuotes(ListedSqliteSource(), etfs), _WithEtfQuotes(fs.OtcSqliteSource(), etfs)],
            "universe": universe, "rotation": rotation, "groups": groups, "input_fingerprints": fingerprints,
            "liquid_universe": liquid, "bias": bias, "strong_stocks": strong}


def main() -> int:
    parser = argparse.ArgumentParser(description="發布籌碼選股與產業資金流向封存（唯讀本機資料）")
    parser.add_argument("--as-of", required=True, help="資料日 YYYY-MM-DD")
    parser.add_argument("--published-at", required=True, help="發布時間（ISO，帶時區）")
    parser.add_argument("--snapshot-root", required=True, help="封存根目錄；驗證時請指向 .tmp")
    parser.add_argument("--universe-root", help="另存 liquid_universe 日快照的目錄（預設不另存，封存批次內已含）")
    args = parser.parse_args()
    report = publish(Path(args.snapshot_root), data_date=args.as_of, published_at=args.published_at,
                     **local_inputs(args.as_of, Path(args.universe_root) if args.universe_root else None))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
