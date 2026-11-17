"""基金對照 0050（2026-11-17 本人定案）：貝萊德世界科技 A10 美元（總報酬穩定配息）含息再投入，換成台幣和 0050 比。

本人定案（owner 轉述，記在 ANALYSIS_PLAN）：
- 唯讀比較：不進庫存、不算損益、不存進使用者清單；自選股頁有一個固定的比較入口。
- 台幣含匯率（淨值 × 央行當天新臺幣對美元收盤匯率），另寫匯率影響幾個百分點。
- 期間：近 1 個月、3 個月、1 年、3 年＋成立以來（集保淨值從 2022-12-01 起）。
- 配息再投入：除息日用當天（已除息）淨值買回同一檔，不計手續費與稅；
  0050 照 ``benchmark_compare`` 既有含息算法（除息日收盤再投入）。

資料：
- 歷史回補 ``seeds/funds/<代碼>/``：2026-11-17 用瀏覽器從集保「基金資訊觀測站」網頁讀一次的原件（清單含網址、讀取時間、SHA-256）。
- 之後每天：集保開放資料 3-4（境外基金淨值，只留約 4 個交易日、晚約 3 個營業日）與 3-5（配息），
  政府資料開放授權條款第 1 版；匯率是央行開放資料。每次只留這檔的列（加上整份檔的 SHA-256），存成 R2 ``funds/raw/``。
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seeds/funds"


def total_return_series(navs: Sequence[tuple[str, float]],
                        distributions: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    """含息再投入的每單位價值（原幣）：期初 1 單位；除息日以當天淨值把配息買回同一檔。

    除息日那天沒有淨值（休市）就在下一個有淨值的日子買回；第一筆淨值以前、最後一筆淨值以後的配息不計。
    """
    pending = sorted((str(d["ex_date"]), float(d["amount"])) for d in distributions)
    units, out, index = 1.0, [], 0
    first = navs[0][0] if navs else None
    while index < len(pending) and first is not None and pending[index][0] <= first:
        index += 1
    for day, nav in navs:
        while index < len(pending) and pending[index][0] <= day:
            units *= 1 + pending[index][1] / nav
            index += 1
        out.append((day, units * nav))
    return out


def to_twd(series: Sequence[tuple[str, float]], fx: Sequence[tuple[str, float]]) -> list[tuple[str, float]]:
    """原幣價值 × 當天（或之前最近一個營業日）的央行新臺幣對美元收盤匯率；比第一筆匯率早的日子不算。"""
    out, index, rate = [], 0, None
    for day, value in series:
        while index < len(fx) and fx[index][0] <= day:
            rate = fx[index][1]
            index += 1
        if rate is not None:
            out.append((day, value * rate))
    return out


# ---- 解析 ----

def _iso(text: str) -> str:
    """'2026/11/09'、'20261109'、'2026-11-09' → '2026-11-09'。"""
    clean = str(text).strip().replace("/", "-")
    if len(clean) == 8 and clean.isdigit():
        clean = f"{clean[:4]}-{clean[4:6]}-{clean[6:]}"
    if len(clean) != 10 or clean[4] != "-" or clean[7] != "-":
        raise ValueError(f"日期格式不對：{text!r}")
    return clean


def _pct(text: Any) -> float | None:
    clean = str(text).strip().rstrip("%")
    return None if clean in ("", "N/A", "-") else float(clean)


def _field(row: Mapping[str, Any], name: str) -> Any:
    """開放資料第一欄常帶 BOM（'\ufeff基金代碼'），兩種寫法都認。"""
    return row.get(name, row.get("\ufeff" + name))


def parse_nav_csv(text: str) -> list[tuple[str, float]]:
    rows = []
    for line in text.splitlines():
        if line.strip():
            day, nav = line.split(",")
            rows.append((_iso(day), float(nav)))
    return rows


def _distribution(ex: Any, record: Any, pay: Any, amount: Any, income: Any, capital: Any) -> dict[str, Any]:
    return {"ex_date": _iso(ex), "record_date": _iso(record), "pay_date": _iso(pay), "amount": float(amount),
            "income_pct": _pct(income), "capital_pct": _pct(capital)}


def parse_backfill_distributions(pages: Iterable[str]) -> list[dict[str, Any]]:
    """集保網頁「配息資訊」分頁的原始回應（每頁一份 JSON 文字）。"""
    out = []
    for text in pages:
        for row in json.loads(text)["list"]:
            out.append(_distribution(row["asiEnterDate"], row["asiBaseDate"], row["asiDate"], row["asiAmt"],
                                     row["asiRatio1"], row["asiRatio2"]))
    return sorted(out, key=lambda d: d["ex_date"])


def parse_opendata_nav(rows: Iterable[Mapping[str, Any]], code: str) -> list[tuple[str, float]]:
    """集保開放資料 3-4（境外基金淨值）裡這檔的列。"""
    return sorted((_iso(_field(r, "日期")), float(_field(r, "基金淨值(金額)")))
                  for r in rows if _field(r, "基金代碼") == code)


def parse_opendata_distributions(rows: Iterable[Mapping[str, Any]], code: str) -> list[dict[str, Any]]:
    """集保開放資料 3-5（境外基金配息資訊）裡這檔的列；「單位數」是每單位配息金額（原幣）。"""
    out = [_distribution(_field(r, "除息日"), _field(r, "配息基準日"), _field(r, "配息發放日"), _field(r, "單位數"),
                         _field(r, "可分配淨利益/配息"), _field(r, "本金/配息"))
           for r in rows if _field(r, "基金代碼") == code]
    return sorted(out, key=lambda d: d["ex_date"])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_seed(code: str, *, seed_dir: Path = SEED_DIR) -> tuple[list[tuple[str, float]], list[dict[str, Any]]]:
    """瀏覽器回補的原件：逐檔核對清單上的 SHA-256，對不上就停（不拿被改過的檔算）。"""
    folder = seed_dir / code
    manifest = json.loads((folder / "backfill-manifest.json").read_text(encoding="utf-8"))
    navs: list[tuple[str, float]] = []
    pages: list[str] = []
    for entry in manifest["files"]:
        data = (folder / entry["file"]).read_bytes()
        if _sha256(data) != entry["sha256"]:
            raise ValueError(f"{code} 回補原件 {entry['file']} 的 SHA-256 和清單不符，停止")
        if entry["kind"] == "nav":
            navs = parse_nav_csv(data.decode("utf-8"))
        elif entry["kind"] == "distributions":
            pages.append(data.decode("utf-8"))
    return navs, parse_backfill_distributions(pages)


# ---- 合併與缺日檢查 ----

def merge_navs(sources: Sequence[Sequence[tuple[str, float]]]) -> tuple[list[tuple[str, float]], list[dict[str, Any]]]:
    """依日期合併；同一天兩份數字不同時留先到的（回補原件在前），另記一筆 conflict。"""
    merged: dict[str, float] = {}
    issues: list[dict[str, Any]] = []
    for source in sources:
        for day, nav in source:
            if day not in merged:
                merged[day] = nav
            elif abs(merged[day] - nav) > 1e-9:
                issues.append({"kind": "conflict", "date": day, "kept": merged[day], "other": nav})
    return sorted(merged.items()), issues


def merge_distributions(sources: Sequence[Sequence[Mapping[str, Any]]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """依除息日合併；配息來源比例常晚幾天才公布，同一筆有比例的那份取代沒有比例的。金額不同記 conflict。"""
    merged: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, Any]] = []
    for source in sources:
        for row in source:
            current = merged.get(row["ex_date"])
            if current is None:
                merged[row["ex_date"]] = dict(row)
            elif abs(current["amount"] - row["amount"]) > 1e-12:
                issues.append({"kind": "conflict", "ex_date": row["ex_date"], "kept": current["amount"], "other": row["amount"]})
            elif current["capital_pct"] is None and row["capital_pct"] is not None:
                merged[row["ex_date"]] = dict(row)
    return [merged[day] for day in sorted(merged)], issues


def _weekdays_between(start: str, end: str) -> list[str]:
    """start 與 end 之間（都不含）的平日。"""
    day, last, out = date.fromisoformat(start) + timedelta(days=1), date.fromisoformat(end), []
    while day < last:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


def find_gaps(nav_dates: Sequence[str], *, windows: Sequence[tuple[str, str]]) -> list[dict[str, Any]]:
    """兩筆淨值之間的平日：落在任何一份官方檔涵蓋的區間內＝那天基金休市；都沒涵蓋到＝沒人看過，算缺。"""
    gaps: list[dict[str, Any]] = []
    for before, after in zip(nav_dates, nav_dates[1:]):
        run: list[str] = []
        for day in [*_weekdays_between(before, after), None]:
            if day is not None and not any(lo <= day <= hi for lo, hi in windows):
                run.append(day)
                continue
            if run:
                gaps.append({"kind": "gap", "from": run[0], "to": run[-1], "weekdays": len(run)})
                run = []
    return gaps


STALE_WEEKDAYS = 6   # 開放資料本來就晚約 3 個營業日；超過 6 個平日沒有新淨值才提醒


def staleness(latest: str, *, as_of: str) -> dict[str, Any] | None:
    behind = len(_weekdays_between(latest, (date.fromisoformat(as_of) + timedelta(days=1)).isoformat()))
    return {"kind": "stale", "latest": latest, "weekdays_behind": behind} if behind > STALE_WEEKDAYS else None


# ---- 每日抓取（集保開放資料＋央行匯率），只留這檔的列 ----

NAV_URL = "https://openapi.tdcc.com.tw/v1/opendata/3-4"
DIST_URL = "https://openapi.tdcc.com.tw/v1/opendata/3-5"
CBC_URL = "https://cpx.cbc.gov.tw/api/OpenData/FTDOpenData_Day"   # 同 level_signals.CBC_USDTWD_URL
LICENSE = "政府資料開放授權條款第 1 版"
RAW_DIR = ROOT / "data/funds/raw"
R2_PREFIX = "funds/raw/"
FX_FROM = "2022-11-01"   # 集保淨值從 2022-12-01 起；匯率留到那之前一點就夠
FUNDS: dict[str, dict[str, str]] = {
    "BLKTENAU": {"code": "BLKTENAU", "isin": "LU2533723974", "currency": "USD", "class_launch": "2022-10-26",
                 "name": "貝萊德世界科技基金 A10美元(總報酬穩定配息)", "short_name": "貝萊德世界科技 A10"},
}


def _http_get(url: str) -> bytes:
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Market-Ana/1.0 (+private research)",
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def _dump(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _fetched_today(raw_dir: Path, prefix: str, today: str) -> bool:
    for path in raw_dir.glob(prefix + "*.json"):
        try:
            if str(json.loads(path.read_text(encoding="utf-8")).get("fetched_at", ""))[:10] == today:
                return True
        except (OSError, ValueError):
            continue
    return False


def _save(raw_dir: Path, name: str, payload: dict[str, Any]) -> str:
    target = raw_dir / name
    if target.exists():
        return "unchanged"
    target.write_bytes(_dump(payload))
    return "saved"


def _extract_nav(code: str, content: bytes, fetched_at: str, raw_dir: Path) -> dict[str, Any]:
    records = json.loads(content.decode("utf-8-sig"))
    mine = [r for r in records if _field(r, "基金代碼") == code]
    if not mine:
        raise ValueError(f"開放資料 3-4 沒有 {code} 的淨值")
    days = [d for d, _ in parse_opendata_nav(mine, code)]
    status = _save(raw_dir, f"{code}-nav-{days[-1]}.json", {
        "schema": "fund-extract/v1", "source": "集保結算所開放資料 3-4 境外基金淨值", "url": NAV_URL, "license": LICENSE,
        "fetched_at": fetched_at, "file_sha256": _sha256(content), "file_bytes": len(content), "file_rows": len(records),
        "code": code, "window": [days[0], days[-1]], "rows": mine})
    return {"status": status, "window": [days[0], days[-1]]}


def _extract_dist(code: str, content: bytes, fetched_at: str, raw_dir: Path) -> dict[str, Any]:
    records = json.loads(content.decode("utf-8-sig"))
    mine = [r for r in records if _field(r, "基金代碼") == code]
    if not mine:
        raise ValueError(f"開放資料 3-5 沒有 {code} 的配息")
    parsed = parse_opendata_distributions(mine, code)
    data_day = max(_iso(_field(r, "資料日期")) for r in mine)
    status = _save(raw_dir, f"{code}-dist-{data_day}.json", {
        "schema": "fund-extract/v1", "source": "集保結算所開放資料 3-5 境外基金配息資訊", "url": DIST_URL, "license": LICENSE,
        "fetched_at": fetched_at, "file_sha256": _sha256(content), "file_bytes": len(content), "file_rows": len(records),
        "code": code, "data_date": data_day, "rows": mine})
    return {"status": status, "latest_ex_date": parsed[-1]["ex_date"]}


def _extract_fx(content: bytes, fetched_at: str, raw_dir: Path) -> dict[str, Any]:
    import level_signals
    rows = [(d, v) for d, v in level_signals.cbc_usdtwd_rows(content, fetched_at[:10]) if d >= FX_FROM]
    if not rows:
        raise ValueError("央行匯率沒有資料")
    status = _save(raw_dir, f"cbc-usdtwd-{rows[-1][0]}.json", {
        "schema": "fund-extract/v1", "source": "中央銀行 新臺幣對美元銀行間收盤匯率", "url": CBC_URL, "license": LICENSE,
        "fetched_at": fetched_at, "file_sha256": _sha256(content), "file_bytes": len(content),
        "from": FX_FROM, "rows": [[d, v] for d, v in rows]})
    return {"status": status, "latest": rows[-1][0]}


def sync_down(store, raw_dir: Path = RAW_DIR) -> int:
    raw_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for key in store.keys(R2_PREFIX):
        name = key[len(R2_PREFIX):]
        target = raw_dir / name
        if "/" in name or target.exists():
            continue
        data = store.get(key)
        if data is not None:
            target.write_bytes(data)
            count += 1
    return count


def sync_up(store, raw_dir: Path = RAW_DIR) -> int:
    existing = set(store.keys(R2_PREFIX))
    count = 0
    for path in sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []:
        if R2_PREFIX + path.name not in existing:
            store.put(R2_PREFIX + path.name, path.read_bytes())
            count += 1
    return count


def daily(*, store, get: Callable[[str], bytes] | None = None, now: str | None = None,
          raw_dir: Path = RAW_DIR, upload: bool = True) -> dict[str, Any]:
    """補齊 R2 已有的抽取檔 → 每個來源一天最多抓一次 → 只留這檔的列存檔 → 上傳新的。

    開放資料淨值只留約 4 個交易日：每個平日都跑，漏一次下一次仍涵蓋得到；漏太久由缺日檢查報出來。
    一個來源失敗不擋其他來源，也不擋每日更新（工作流程裡這一步 continue-on-error）。"""
    get = get or _http_get
    now = now or datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")
    today = now[:10]
    downloaded = sync_down(store, raw_dir) if store is not None else 0
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Any] = {}
    jobs: list[tuple[str, str, str, Callable[[bytes], dict[str, Any]]]] = []
    for code in FUNDS:
        jobs.append(("nav", f"{code}-nav-", NAV_URL, lambda c, code=code: _extract_nav(code, c, now, raw_dir)))
        jobs.append(("dist", f"{code}-dist-", DIST_URL, lambda c, code=code: _extract_dist(code, c, now, raw_dir)))
    jobs.append(("fx", "cbc-usdtwd-", CBC_URL, lambda c: _extract_fx(c, now, raw_dir)))
    for key, prefix, url, extract in jobs:
        if _fetched_today(raw_dir, prefix, today):
            sources[key] = {"status": "already-fetched-today"}
            continue
        try:
            sources[key] = extract(get(url))
        except Exception as error:  # noqa: BLE001 —— 任何一個來源壞掉都只記下來
            sources[key] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
    uploaded = sync_up(store, raw_dir) if (store is not None and upload) else 0
    return {"now": now, "downloaded": downloaded, "sources": sources, "uploaded": uploaded}


# ---- 組成歷史 ----

def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_history(code: str, *, raw_dir: Path = RAW_DIR, seed_dir: Path = SEED_DIR, as_of: str) -> dict[str, Any]:
    """回補原件（先）＋每日抽取檔（依檔名＝日期由舊到新）→ 淨值、配息、匯率與提醒（衝突、缺日、過期）。"""
    seed_navs, seed_dists = load_seed(code, seed_dir=seed_dir)
    nav_sources, dist_sources = [seed_navs], [seed_dists]
    windows = [(seed_navs[0][0], seed_navs[-1][0])] if seed_navs else []
    for path in sorted(raw_dir.glob(f"{code}-nav-*.json")) if raw_dir.exists() else []:
        body = _read(path)
        nav_sources.append(parse_opendata_nav(body["rows"], code))
        windows.append(tuple(body["window"]))
    for path in sorted(raw_dir.glob(f"{code}-dist-*.json")) if raw_dir.exists() else []:
        dist_sources.append(parse_opendata_distributions(_read(path)["rows"], code))
    navs, nav_issues = merge_navs(nav_sources)
    dists, dist_issues = merge_distributions(dist_sources)
    fx_files = sorted(raw_dir.glob("cbc-usdtwd-*.json")) if raw_dir.exists() else []
    fx = [(str(d), float(v)) for d, v in _read(fx_files[-1])["rows"]] if fx_files else []
    issues = [*nav_issues, *dist_issues, *find_gaps([d for d, _ in navs], windows=windows)]
    stale = staleness(navs[-1][0], as_of=as_of) if navs else None
    if stale:
        issues.append(stale)
    if not fx:
        issues.append({"kind": "fx-missing"})
    return {"navs": navs, "distributions": dists, "fx": fx, "issues": issues}


# ---- 和 0050 對照 ----

SCHEMA = "fund-vs-0050/v1"
PERIODS: tuple[tuple[str, str, int | None], ...] = (
    ("1m", "近 1 個月", 1), ("3m", "近 3 個月", 3), ("1y", "近 1 年", 12), ("3y", "近 3 年", 36), ("since", "成立以來", None),
)
NOTES = (
    "基金淨值換成台幣：每天淨值 × 央行當天新臺幣對美元收盤匯率（台灣休市日用前一個營業日）；「匯率影響」＝台幣報酬−美元報酬。",
    "配息再投入：除息日用當天淨值買回同一檔，不計申購手續費與稅；0050 用本站既有算法（除息日收盤價再投入）。",
    "兩邊的管理費都已內含在淨值裡。基金淨值通常比 0050 晚幾天公布，兩邊都比到雙方都有資料的同一天。",
    "成立以來從集保有淨值的第一天（2022-12-01）算起；級別成立日是 2022-10-26。過去表現不代表未來。",
    "資料來源：集保結算所基金資訊觀測站與開放資料、中央銀行（政府資料開放授權條款第 1 版）、臺灣證券交易所。",
)


def _upto(series: Sequence[tuple[str, float]], end: str) -> list[tuple[str, float]]:
    return [point for point in series if point[0] <= end]


def _capital_summary(dists: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    years: dict[str, dict[str, Any]] = {}
    for row in dists:
        year = years.setdefault(row["ex_date"][:4], {"year": row["ex_date"][:4], "count": 0, "known": 0,
                                                     "all_capital": 0, "some_capital": 0})
        year["count"] += 1
        capital = row.get("capital_pct")
        if capital is not None:
            year["known"] += 1
            year["all_capital"] += capital >= 99.995
            year["some_capital"] += capital > 0
    by_year = [years[y] for y in sorted(years)]
    parts = []
    for y in by_year:
        if y["some_capital"]:
            parts.append(f"{y['year']} 年 {y['count']} 次配息全部來自本金" if y["all_capital"] == y["count"]
                         else f"{y['year']} 年 {y['count']} 次配息中 {y['all_capital']} 次全部來自本金")
    known = [row for row in dists if row.get("capital_pct") is not None]
    since = None
    for row in reversed(known):
        if row["capital_pct"] > 0:
            break
        since = row["ex_date"]
    if since and since != (known[0]["ex_date"] if known else None):
        parts.append(f"{int(since[:4])} 年 {int(since[5:7])} 月起公布的配息都來自可分配淨利益")
    note = "這檔配息可能來自本金" + (f"：{'；'.join(parts)}。" if parts else "。")
    return by_year, note


def fund_payload(code: str, *, navs: Sequence[tuple[str, float]], distributions: Sequence[Mapping[str, Any]],
                 fx: Sequence[tuple[str, float]], benchmark: Sequence[tuple[str, float]], benchmark_date: str,
                 issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """基金（含息、台幣）和 0050（含息）同一期初、同一截止日的對照；benchmark 是 0050 的含息總報酬序列。"""
    import benchmark_compare as bc

    usd_all = total_return_series(navs, distributions)
    twd_all = to_twd(usd_all, fx)
    end = min(twd_all[-1][0], benchmark_date) if twd_all else benchmark_date
    usd, twd, bench = _upto(usd_all, end), _upto(twd_all, end), _upto(benchmark, end)
    periods = []
    for key, label, months in PERIODS:
        base = {"period": key, "label": label}
        found = (twd[0][0], twd) if (months is None and twd) else (bc._period_window(twd, end, months) if months else None)
        if found is None:
            periods.append({**base, "status": "insufficient_history", "reason": "since_launch"})
            continue
        start, window = found
        usd_first = bc._value_on_or_before(usd, start)
        bench_first = bc._value_on_or_before(bench, start)
        if usd_first is None or bench_first is None:
            periods.append({**base, "status": "insufficient_history", "reason": "benchmark_not_loaded"})
            continue
        bench_window = bc._window(bench, bench_first[0])
        fund_ret = bc._return_pct(window[0][1], window[-1][1])
        usd_ret = bc._return_pct(usd_first[1], usd[-1][1])
        bench_ret = bc._return_pct(bench_window[0][1], bench_window[-1][1])
        periods.append({**base, "status": "ok", "from": start, "to": window[-1][0],
                        "fund_return_pct": fund_ret, "fund_usd_return_pct": usd_ret,
                        "fx_effect_pct": round(fund_ret - usd_ret, 1),
                        "benchmark_return_pct": bench_ret, "excess_pct": round(fund_ret - bench_ret, 1),
                        "fund_max_drawdown_pct": bc._max_drawdown_pct(window),
                        "benchmark_max_drawdown_pct": bc._max_drawdown_pct(bench_window)})
    chart_from, chart = bc.chart_points(twd, twd[0][0]) if twd else (None, [])
    bench_start = bc._value_on_or_before(bench, chart_from) if chart_from else None
    _, bench_chart = bc.chart_points(bench, bench_start[0]) if bench_start else (None, [])
    by_year, note = _capital_summary(distributions)
    last_nav = _upto(navs, end)[-1] if navs else None
    last_fx = bc._value_on_or_before(fx, end) if fx else None
    latest_dist = distributions[-1] if distributions else None
    return {
        "schema": SCHEMA, "fund": dict(FUNDS[code]), "basis": "含息總報酬（台幣），期初＝1",
        "data_date": end, "fund_nav_date": navs[-1][0] if navs else None, "benchmark_date": benchmark_date,
        "latest": {"nav": last_nav[1] if last_nav else None, "nav_date": last_nav[0] if last_nav else None,
                   "fx": last_fx[1] if last_fx else None, "fx_date": last_fx[0] if last_fx else None},
        "periods": periods, "chart_from": chart_from, "chart": chart, "benchmark_chart": bench_chart,
        "distributions": {"count": len(distributions), "by_year": by_year,
                          "latest": {"ex_date": latest_dist["ex_date"], "amount": latest_dist["amount"]} if latest_dist else None},
        "capital_note": note, "issues": [dict(i) for i in issues], "notes": list(NOTES),
    }


def precompute(loader, end: str, *, as_of: str, raw_dir: Path = RAW_DIR,
               seed_dir: Path = SEED_DIR) -> tuple[dict[str, str], dict[str, str]]:
    """build_compare_pack 用：{代碼: 預算好的 JSON 字串}，以及資料不齊而沒出的那幾檔與原因。

    loader 是 benchmark_compare.Loader（0050 含息序列只用它，和個股頁對照同一套）；end 是資料包的資料日。"""
    funds: dict[str, str] = {}
    problems: dict[str, str] = {}
    bench, _, _ = loader.series("0050", end)
    for code in FUNDS:
        try:
            history = load_history(code, raw_dir=raw_dir, seed_dir=seed_dir, as_of=as_of)
        except (OSError, ValueError, KeyError) as error:
            problems[code] = f"讀不到資料：{type(error).__name__}: {error}"
            continue
        if not history["fx"]:
            problems[code] = "沒有匯率資料"
            continue
        if not bench:
            problems[code] = "沒有 0050 資料"
            continue
        payload = fund_payload(code, navs=history["navs"], distributions=history["distributions"], fx=history["fx"],
                               benchmark=bench, benchmark_date=bench[-1][0],
                               issues=[i for i in history["issues"] if i["kind"] != "fx-missing"])
        funds[code] = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return funds, problems


def main(argv: Sequence[str] | None = None) -> int:
    """python fund_compare.py daily [--r2] [--no-upload]：每日抓取（工作流程用 --r2）。
    python fund_compare.py check [--as-of YYYY-MM-DD]：只檢查目前資料的缺日／衝突／過期，不抓。"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="基金對照 0050：每日抓取與缺日檢查")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("daily")
    run.add_argument("--r2", action="store_true", help="先從 R2 funds/raw/ 補齊、抓完上傳新的")
    run.add_argument("--no-upload", action="store_true", help="試跑：不寫 R2")
    check = sub.add_parser("check")
    check.add_argument("--as-of")
    args = parser.parse_args(argv)
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    report: dict[str, Any] = {}
    if args.command == "daily":
        store = None
        if args.r2:
            import state_sync
            store = state_sync.R2Store.from_env()
        report = daily(store=store, upload=not args.no_upload)
    as_of = getattr(args, "as_of", None) or today
    report["checks"] = {code: load_history(code, as_of=as_of)["issues"] for code in FUNDS}
    sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    failed = any(s.get("status") == "failed" for s in report.get("sources", {}).values())
    problems = any(report["checks"].values())
    return 1 if failed or problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
