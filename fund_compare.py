"""基金對照 0050（2026-11-17 本人定案）：貝萊德世界科技 A10 美元（總報酬穩定配息）含息再投入，換成台幣和 0050 比。

本人定案（owner 轉述，記在 ANALYSIS_PLAN）：
- 【已被 11-18 更正取代】唯讀比較、固定入口——本人 11-18：基金當成一般個股（.scratch/fund-as-stock/spec.md）；
  比較改成資料包裡和股票同形狀的一筆（``compare_item``，05 票），期間＝股票的五個＋成立以來。
- 台幣含匯率（淨值 × 央行當天新臺幣對美元收盤匯率），另寫匯率影響幾個百分點。
- 期間：近 1 個月、3 個月、1 年、3 年＋成立以來（集保淨值從 2022-12-01 起）；05 票起另有近 5 年（不夠長＝資料不足）。
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


def _iso_or_none(text: Any) -> str | None:
    """配息基準日、發放日：集保較早的配息列有的寫 N/A（例如安聯收益成長 2021-11～2022-05 的發放日），照實記成沒有。"""
    clean = "" if text is None else str(text).strip()
    return None if clean in ("", "N/A", "-") else _iso(clean)


def _distribution(ex: Any, record: Any, pay: Any, amount: Any, income: Any, capital: Any) -> dict[str, Any]:
    """除息日與每單位金額一定要有（含息再投入靠它們）；基準日、發放日、來源比例沒公布就是 None。"""
    return {"ex_date": _iso(ex), "record_date": _iso_or_none(record), "pay_date": _iso_or_none(pay), "amount": float(amount),
            "income_pct": _pct(income), "capital_pct": _pct(capital)}


def parse_backfill_distributions(pages: Iterable[str]) -> list[dict[str, Any]]:
    """集保網頁「配息資訊」分頁的原始回應（每頁一份 JSON 文字）。"""
    out = []
    for text in pages:
        for row in json.loads(text)["list"]:
            out.append(_distribution(row["asiEnterDate"], row["asiBaseDate"], row["asiDate"], row["asiAmt"],
                                     row["asiRatio1"], row["asiRatio2"]))
    return sorted(out, key=lambda d: d["ex_date"])


def tdcc_code(code: str) -> str:
    """站上代碼 → 集保基金代碼（開放資料「基金代碼」欄、基金資訊觀測站網址的 key）。

    站上代碼要符合 stock_codes.CODE_PATTERN（4～12 碼大寫英數，路由、使用者資料、D1 主鍵都靠它），
    集保代碼可能帶連字號（安聯 AGIF-FVM），所以 FUNDS 另記 ``tdcc_code``；沒記的就是同一個。"""
    return FUNDS.get(code, {}).get("tdcc_code", code)


def parse_opendata_nav(rows: Iterable[Mapping[str, Any]], code: str) -> list[tuple[str, float]]:
    """集保開放資料 3-4（境外基金淨值）裡這檔的列；``code`` 是集保基金代碼。"""
    return sorted((_iso(_field(r, "日期")), float(_field(r, "基金淨值(金額)")))
                  for r in rows if _field(r, "基金代碼") == code)


def parse_opendata_distributions(rows: Iterable[Mapping[str, Any]], code: str) -> list[dict[str, Any]]:
    """集保開放資料 3-5（境外基金配息資訊）裡這檔的列；``code`` 是集保基金代碼；「單位數」是每單位配息金額（原幣）。"""
    out = [_distribution(_field(r, "除息日"), _field(r, "配息基準日"), _field(r, "配息發放日"), _field(r, "單位數"),
                         _field(r, "可分配淨利益/配息"), _field(r, "本金/配息"))
           for r in rows if _field(r, "基金代碼") == code]
    return sorted(out, key=lambda d: d["ex_date"])


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_seed_files(code: str, *, seed_dir: Path = SEED_DIR
                    ) -> tuple[list[tuple[list[tuple[str, float]], tuple[str, str]]], list[dict[str, Any]]]:
    """瀏覽器回補的原件（資料夾用站上代碼 ``seeds/funds/<代碼>/``）：逐檔核對清單上的 SHA-256，對不上就停（不拿被改過的檔算）。

    回傳 ([(這份淨值檔的列, 這份檔看過的區間), ...], 配息)，淨值檔照清單順序（第一份是最早的整段回補，後面是補缺口的檔）。
    看過的區間：清單那一筆有 first／last 就用它（人工補缺口時寫「我看了哪幾天」，中間沒有淨值＝基金休市），沒有就用檔裡第一天到最後一天。
    還沒有回補清單的基金（剛加進 FUNDS、原件還沒讀）回兩個空的：只用每日抽取檔，load_history 另記 seed-missing。"""
    folder = seed_dir / code
    if not (folder / "backfill-manifest.json").exists():
        return [], []
    manifest = json.loads((folder / "backfill-manifest.json").read_text(encoding="utf-8"))
    nav_files: list[tuple[list[tuple[str, float]], tuple[str, str]]] = []
    pages: list[str] = []
    for entry in manifest["files"]:
        data = (folder / entry["file"]).read_bytes()
        # 大小寫不分：Windows 的 Get-FileHash 印大寫，人工補缺口時直接貼上也對得起來
        if _sha256(data) != str(entry["sha256"]).strip().lower():
            raise ValueError(f"{code} 回補原件 {entry['file']} 的 SHA-256 和清單不符，停止")
        if entry["kind"] == "nav":
            rows = parse_nav_csv(data.decode("utf-8-sig"))
            if not rows:
                raise ValueError(f"{code} 回補原件 {entry['file']} 沒有任何淨值，停止")
            window = (_iso(entry.get("first") or rows[0][0]), _iso(entry.get("last") or rows[-1][0]))
            outside = [day for day, _ in rows if not window[0] <= day <= window[1]]
            if outside:
                raise ValueError(f"{code} 回補原件 {entry['file']} 有 {outside[0]} 在清單寫的 first～last 之外，停止")
            nav_files.append((rows, window))
        elif entry["kind"] == "distributions":
            pages.append(data.decode("utf-8"))
    return nav_files, parse_backfill_distributions(pages)


def load_seed(code: str, *, seed_dir: Path = SEED_DIR) -> tuple[list[tuple[str, float]], list[dict[str, Any]]]:
    """回補原件合併成一條淨值＋配息（同一天不同數字留先到的；衝突由 load_history 記成提醒）。"""
    nav_files, dists = load_seed_files(code, seed_dir=seed_dir)
    navs, _ = merge_navs([rows for rows, _ in nav_files])
    return navs, dists


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
                issues.append({"kind": "nav-conflict", "date": day, "kept": merged[day], "other": nav})
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
                issues.append({"kind": "dist-conflict", "ex_date": row["ex_date"], "kept": current["amount"], "other": row["amount"]})
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
TAIPEI = timezone(timedelta(hours=8))
FX_FROM = "2022-11-01"   # 集保淨值從 2022-12-01 起；匯率留到那之前一點就夠
# 鍵與 code 是站上代碼（stock_codes.CODE_PATTERN）；tdcc_code 是集保基金代碼（開放資料篩選、基金資訊觀測站網址用）。
# 回補原件放 seeds/funds/<站上代碼>/；還沒有原件的基金照樣建置，只用每日抽取檔（資料不足時名錄記 insufficient）。
FUNDS: dict[str, dict[str, str]] = {
    "BLKTENAU": {"code": "BLKTENAU", "tdcc_code": "BLKTENAU", "isin": "LU2533723974", "currency": "USD",
                 "class_launch": "2022-10-26",
                 "name": "貝萊德世界科技基金 A10美元(總報酬穩定配息)", "short_name": "貝萊德世界科技 A10"},
    # 級別成立日 2012-10-16：安聯投信 tw.allianzgi.com 基金頁「自成立日(2012/10/16)以來」（2026-11-24 查）；
    # 自由財經 ec.ltn.com.tw/fund_detail/foreign/TLZ64 同列成立日期 2012/10/16。
    "AGIFFVM": {"code": "AGIFFVM", "tdcc_code": "AGIF-FVM", "isin": "LU0820561818", "currency": "USD",
                "class_launch": "2012-10-16",
                "name": "安聯收益成長基金-AM穩定月收類股(美元)", "short_name": "安聯收益成長 AM 穩定月收"},
    # TODO 待核實 class_launch：2021-07-02 取自 Yahoo 奇摩基金 tw.stock.yahoo.com/fund/summary/F00001CI86:FO（晨星資料，非一手）；
    # 摩根投信與集保基金資訊觀測站頁面（JS 動態載入）2026-11-24 都沒讀到級別成立日，回補原件到手時請核對。
    # 2026-11-24 再查：cbonds、easybank（都非一手）同列 2021-07-02；一手來源應是摩根資產管理該級別的 KID／公開說明書，尚未讀到。
    "GLIACU": {"code": "GLIACU", "tdcc_code": "GLIACU", "isin": "LU2347655073", "currency": "USD",
               "class_launch": "2021-07-02",
               "name": "摩根投資基金 - 多重收益基金 - JPM多重收益(美元對沖) - A股(穩定月配)",
               "short_name": "摩根多重收益 A股穩定月配"},
}


def taipei_today() -> str:
    return datetime.now(TAIPEI).date().isoformat()


def _http_get(url: str) -> bytes:
    import urllib.request
    request = urllib.request.Request(url, headers={"User-Agent": "Market-Ana/1.0 (+private research)",
                                                   "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def _extract_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def _mark_name(today: str, key: str) -> str:
    """當天抓過的記號：fetched-<日期>-<來源>.json。檔名每天都不同，和抽取檔一樣只增不改，R2 照樣同步。"""
    return f"fetched-{today}-{key}.json"


def _save(raw_dir: Path, name: str, payload: dict[str, Any]) -> str:
    target = raw_dir / name
    if target.exists():
        return "unchanged"
    target.write_bytes(_extract_bytes(payload))
    return "saved"


def _extract_nav(code: str, content: bytes, fetched_at: str, raw_dir: Path) -> dict[str, Any]:
    """抽取檔以站上代碼命名（<代碼>-nav-<日期>.json），列照集保原樣保留（基金代碼欄是集保代碼）。"""
    source = tdcc_code(code)
    records = json.loads(content.decode("utf-8-sig"))
    mine = [r for r in records if _field(r, "基金代碼") == source]
    if not mine:
        raise ValueError(f"開放資料 3-4 沒有 {source} 的淨值")
    days = [d for d, _ in parse_opendata_nav(mine, source)]
    status = _save(raw_dir, f"{code}-nav-{days[-1]}.json", {
        "schema": "fund-extract/v1", "source": "集保結算所開放資料 3-4 境外基金淨值", "url": NAV_URL, "license": LICENSE,
        "fetched_at": fetched_at, "file_sha256": _sha256(content), "file_bytes": len(content), "file_rows": len(records),
        "code": code, "tdcc_code": source, "window": [days[0], days[-1]], "rows": mine})
    return {"status": status, "window": [days[0], days[-1]]}


def _extract_dist(code: str, content: bytes, fetched_at: str, raw_dir: Path) -> dict[str, Any]:
    source = tdcc_code(code)
    records = json.loads(content.decode("utf-8-sig"))
    mine = [r for r in records if _field(r, "基金代碼") == source]
    if not mine:
        raise ValueError(f"開放資料 3-5 沒有 {source} 的配息")
    parsed = parse_opendata_distributions(mine, source)
    data_day = max(_iso(_field(r, "資料日期")) for r in mine)
    status = _save(raw_dir, f"{code}-dist-{data_day}.json", {
        "schema": "fund-extract/v1", "source": "集保結算所開放資料 3-5 境外基金配息資訊", "url": DIST_URL, "license": LICENSE,
        "fetched_at": fetched_at, "file_sha256": _sha256(content), "file_bytes": len(content), "file_rows": len(records),
        "code": code, "tdcc_code": source, "data_date": data_day, "rows": mine})
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
    """補齊 R2 已有的抽取檔 → 每檔基金的每個來源一天最多抓一次 → 只留那檔的列存檔 → 上傳新的。

    開放資料淨值只留最近 4 個淨值日（2026-11-24 實測 3-4 只有 11-16～11-19）：連續 4 個營業日都沒抓成功才會留下缺口，
    漏太久由缺日檢查報出來。兩個抓取點共用 R2 上的記號：每日更新（週一到週五四個時段）與 announcements（每天 08:00、
    含週末連假）；先成功的那個留記號，另一個當天就不再抓。抓失敗（下載、解析或這檔不在檔裡）不留記號，下一個時段再試。
    一個來源失敗不擋其他來源，也不擋每日更新（工作流程裡這一步 continue-on-error）。
    回報的 sources 以當天記號的名字為鍵（nav-<代碼>、dist-<代碼>、fx），每檔各自一筆；
    同一個網址（3-4、3-5 是整份全部基金）一次執行只下載一次，幾檔基金共用。"""
    get = get or _http_get
    now = now or datetime.now(TAIPEI).isoformat(timespec="seconds")
    today = now[:10]
    downloaded = sync_down(store, raw_dir) if store is not None else 0
    raw_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Any] = {}
    jobs: list[tuple[str, str, Callable[[bytes], dict[str, Any]]]] = []
    for code in FUNDS:
        jobs.append((f"nav-{code}", NAV_URL, lambda c, code=code: _extract_nav(code, c, now, raw_dir)))
        jobs.append((f"dist-{code}", DIST_URL, lambda c, code=code: _extract_dist(code, c, now, raw_dir)))
    jobs.append(("fx", CBC_URL, lambda c: _extract_fx(c, now, raw_dir)))
    fetched: dict[str, bytes | Exception] = {}
    for mark, url, extract in jobs:
        mark_path = raw_dir / _mark_name(today, mark)
        if mark_path.exists():
            sources[mark] = {"status": "already-fetched-today"}
            continue
        if url not in fetched:
            try:
                fetched[url] = get(url)
            except Exception as error:  # noqa: BLE001 —— 下載失敗也只記下來；同一個網址這次不再重試
                fetched[url] = error
        try:
            content = fetched[url]
            if isinstance(content, Exception):
                raise content
            sources[mark] = extract(content)
        except Exception as error:  # noqa: BLE001 —— 任何一個來源壞掉都只記下來
            sources[mark] = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
            continue
        mark_path.write_bytes(_extract_bytes({"fetched_at": now, "url": url, **sources[mark]}))
    uploaded = sync_up(store, raw_dir) if (store is not None and upload) else 0
    return {"now": now, "downloaded": downloaded, "sources": sources, "uploaded": uploaded}


# ---- 組成歷史 ----

def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_history(code: str, *, raw_dir: Path = RAW_DIR, seed_dir: Path = SEED_DIR, as_of: str) -> dict[str, Any]:
    """回補原件（先）＋每日抽取檔（依檔名＝日期由舊到新）→ 淨值、配息、匯率與提醒（衝突、缺日、過期、沒有回補原件）。

    檔名用站上代碼；抽取檔裡的列照集保原樣，用集保代碼（tdcc_code）篩。"""
    source = tdcc_code(code)
    seed_files, seed_dists = load_seed_files(code, seed_dir=seed_dir)
    seed_navs = [row for rows, _ in seed_files for row in rows]
    # 每份回補檔各自一段「看過的區間」：補缺口的檔只蓋住它自己那幾天，漏補的日子照樣報缺；同一天不同數字記衝突、不覆蓋。
    nav_sources, dist_sources = [rows for rows, _ in seed_files], [seed_dists]
    windows = [window for _, window in seed_files]
    for path in sorted(raw_dir.glob(f"{code}-nav-*.json")) if raw_dir.exists() else []:
        body = _read(path)
        nav_sources.append(parse_opendata_nav(body["rows"], source))
        windows.append(tuple(body["window"]))
    for path in sorted(raw_dir.glob(f"{code}-dist-*.json")) if raw_dir.exists() else []:
        dist_sources.append(parse_opendata_distributions(_read(path)["rows"], source))
    navs, nav_issues = merge_navs(nav_sources)
    dists, dist_issues = merge_distributions(dist_sources)
    fx_files = sorted(raw_dir.glob("cbc-usdtwd-*.json")) if raw_dir.exists() else []
    fx = [(str(d), float(v)) for d, v in _read(fx_files[-1])["rows"]] if fx_files else []
    issues = [*nav_issues, *dist_issues, *find_gaps([d for d, _ in navs], windows=windows)]
    if not seed_navs:
        # 還沒從集保網頁回補歷史：只有每日抽取檔累積的幾天，報酬與策略照資料不足處理
        issues.insert(0, {"kind": "seed-missing", "code": code})
    stale = staleness(navs[-1][0], as_of=as_of) if navs else None
    if stale:
        issues.append(stale)
    if not fx:
        issues.append({"kind": "fx-missing"})
    return {"navs": navs, "distributions": dists, "fx": fx, "issues": issues}


# ---- 比較（基金當成一般個股 05 票）----
# 基金在比較資料包裡是和股票同形狀的一筆（benchmark_compare.Loader.item 的欄位、同一個 period_rows），
# 報酬是台幣、含息再投入；另帶基金專屬欄位：kind／currency、每個期間的原幣報酬與匯率影響、「成立以來」一列（本人 11-17 選的期間）。
# 舊的 fund-vs-0050/v1 整頁形狀（含配息摘要，06 票改由「配息」分類提供）與 /api/funds/<代碼>/vs-0050 退場。

NOTES = (
    "基金淨值換成台幣：每天淨值 × 央行當天新臺幣對美元收盤匯率（台灣休市日用前一個營業日）；「匯率影響」＝台幣報酬−原幣報酬。",
    "配息再投入：除息日用當天淨值買回同一檔，不計申購手續費與稅；0050 用本站既有算法（除息日收盤價再投入）。",
    "基金淨值通常比 0050 晚幾天公布，兩邊都比到雙方都有資料的同一天。成立以來從集保有淨值的第一天算起。",
    "資料來源：集保結算所基金資訊觀測站與開放資料、中央銀行（政府資料開放授權條款第 1 版）、臺灣證券交易所。",
)


def _upto(series: Sequence[tuple[str, float]], end: str) -> list[tuple[str, float]]:
    return [point for point in series if point[0] <= end]


def compare_item(code: str, *, navs: Sequence[tuple[str, float]], distributions: Sequence[Mapping[str, Any]],
                 fx: Sequence[tuple[str, float]], benchmark: Sequence[tuple[str, float]],
                 issues: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """基金（含息、台幣）和 0050（含息）同一期初、同一截止日（雙方都有資料的最後一天）的比較項目。

    ``benchmark`` 是 0050 的含息總報酬序列（benchmark_compare.Loader.series）。"""
    import benchmark_compare as bc

    fund = FUNDS[code]
    usd_all = total_return_series(navs, distributions)
    twd_all = to_twd(usd_all, fx)
    end = min(twd_all[-1][0], benchmark[-1][0]) if twd_all and benchmark else ""
    usd, twd, bench = _upto(usd_all, end), _upto(twd_all, end), _upto(benchmark, end)
    if not twd or not bench:
        raise ValueError("沒有雙方都有資料的日子（淨值、匯率或 0050）")
    periods = [*bc.period_rows(twd, bench, end, fund["class_launch"]),
               bc.compare_row({"period": "since", "label": "成立以來"}, twd, bench)]
    for row in periods:
        if row["status"] == "ok":
            # 匯率影響用沒四捨五入的兩個報酬相減再進位，不是兩個已進位的數字相減（差 0.1 個百分點）
            native_ratio = usd[-1][1] / bc._value_on_or_before(usd, row["from"])[1]
            twd_ratio = twd[-1][1] / bc._value_on_or_before(twd, row["from"])[1]
            row["stock_native_return_pct"] = round((native_ratio - 1) * 100, 1)
            row["fx_effect_pct"] = round((twd_ratio - native_ratio) * 100, 1)
    chart_from, chart = bc.chart_points(twd, twd[0][0])
    bench_start = bc._value_on_or_before(bench, chart_from) if chart_from else None
    _, bench_chart = bc.chart_points(bench, bench_start[0]) if bench_start else (None, [])
    return {"code": code, "name": fund["short_name"], "market": "境外基金", "kind": "fund", "currency": fund["currency"],
            "status": "ok", "dividends_included": True, "dividend_source": "TDCC 境外基金配息",
            "listed_date": fund["class_launch"],
            "history": {"from": twd[0][0], "to": twd[-1][0], "points": len(twd)}, "nav_date": navs[-1][0],
            "periods": periods, "chart_from": chart_from, "chart": chart, "benchmark_chart": bench_chart,
            "issues": [dict(i) for i in issues], "notes": list(NOTES)}


def precompute(loader, end: str, *, as_of: str, raw_dir: Path | None = None, seed_dir: Path | None = None,
               codes: Sequence[str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """build_compare_pack（和本機 live_server）用：{代碼: 預算好的比較項目 JSON 字串}，以及資料不齊而沒出的那幾檔與原因。

    loader 是 benchmark_compare.Loader（0050 含息序列只用它，和個股比較同一套）；end 是資料包的資料日。
    raw_dir／seed_dir 沒給就用呼叫當下的 RAW_DIR／SEED_DIR。"""
    raw_dir = RAW_DIR if raw_dir is None else raw_dir
    seed_dir = SEED_DIR if seed_dir is None else seed_dir
    wanted = list(FUNDS) if codes is None else [code for code in codes if code in FUNDS]
    funds: dict[str, str] = {}
    if not wanted:
        return {}, {}
    bench, _, _ = loader.series("0050", end)
    if not bench:
        return {}, {code: "沒有 0050 資料" for code in wanted}
    problems: dict[str, str] = {}
    for code in wanted:
        try:
            history = load_history(code, raw_dir=raw_dir, seed_dir=seed_dir, as_of=as_of)
        except (OSError, ValueError, KeyError) as error:
            problems[code] = f"讀不到資料：{type(error).__name__}: {error}"
            continue
        if not history["fx"]:
            problems[code] = "沒有匯率資料"
            continue
        try:
            item = compare_item(code, navs=history["navs"], distributions=history["distributions"], fx=history["fx"],
                                benchmark=bench, issues=[i for i in history["issues"] if i["kind"] != "fx-missing"])
        except ValueError as error:
            problems[code] = str(error)
            continue
        funds[code] = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
    return funds, problems


MANUAL_FIX = "seeds/funds/README.md"   # 缺口只能人工補時的步驟（本人照做，不需要助手）


def describe_issue(code: str, issue: Mapping[str, Any]) -> str:
    """一筆提醒 → 一行白話（執行摘要用）：哪一檔、哪幾天、怎麼補。"""
    name = f"{code}（{FUNDS[code]['short_name']}）" if code in FUNDS else code
    kind = issue.get("kind")
    if kind == "gap":
        return (f"{name} 缺淨值 {issue['from']}～{issue['to']}（{issue['weekdays']} 個平日沒有任何官方檔看過）。"
                f"集保開放資料只留最近 4 個淨值日，排程補不回來；照 {MANUAL_FIX} 補。")
    if kind == "stale":
        return (f"{name} 最新淨值停在 {issue['latest']}，已落後 {issue['weekdays_behind']} 個平日"
                f"（超過 {STALE_WEEKDAYS} 個就算過期）：先看上面哪個來源失敗；連續 4 個營業日沒抓到就會變成缺口。")
    if kind == "nav-conflict":
        return f"{name} {issue['date']} 的淨值兩份官方檔不同：留 {issue['kept']}、另一份是 {issue['other']}（沒有覆蓋，請人工核對）。"
    if kind == "dist-conflict":
        return f"{name} 除息日 {issue['ex_date']} 的配息兩份官方檔不同：留 {issue['kept']}、另一份是 {issue['other']}。"
    if kind == "seed-missing":
        return f"{name} 還沒有歷史回補原件（seeds/funds/{code}/），只有每日累積的幾天。"
    if kind == "fx-missing":
        return f"{name} 沒有央行匯率資料，台幣比較算不出來。"
    return f"{name} {json.dumps(dict(issue), ensure_ascii=False)}"


def summary_markdown(report: Mapping[str, Any]) -> str:
    """執行摘要（GITHUB_STEP_SUMMARY）：先講有沒有問題，再逐條列抓取失敗與每檔的缺日／衝突／過期。"""
    failed = {mark: s for mark, s in (report.get("sources") or {}).items() if s.get("status") == "failed"}
    checks = report.get("checks") or {}
    lines = ["### 基金淨值"]
    if not failed and not any(checks.values()):
        lines.append("正常：每檔都沒有缺日、衝突或過期。")
    for mark, source in failed.items():
        lines.append(f"- 抓取失敗 {mark}：{source.get('error')}（今天沒留記號，下一個排程時段會再試）")
    for code, issues in checks.items():
        lines.extend(f"- {describe_issue(code, issue)}" for issue in issues)
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    """python fund_compare.py daily [--r2] [--no-upload] [--summary 檔案]：每日抓取（工作流程用 --r2）。
    python fund_compare.py check [--as-of YYYY-MM-DD] [--summary 檔案]：只檢查目前資料的缺日／衝突／過期，不抓。
    --summary：把白話的結果（哪一檔、哪幾天）附加到這個檔案（工作流程給 $GITHUB_STEP_SUMMARY）。"""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="基金對照 0050：每日抓取與缺日檢查")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("daily")
    run.add_argument("--r2", action="store_true", help="先從 R2 funds/raw/ 補齊、抓完上傳新的")
    run.add_argument("--no-upload", action="store_true", help="試跑：不寫 R2")
    run.add_argument("--summary")
    check = sub.add_parser("check")
    check.add_argument("--as-of")
    check.add_argument("--summary")
    args = parser.parse_args(argv)
    today = taipei_today()
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
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as handle:
            handle.write(summary_markdown(report))
    failed = any(s.get("status") == "failed" for s in report.get("sources", {}).values())
    problems = any(report["checks"].values())
    return 1 if failed or problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
