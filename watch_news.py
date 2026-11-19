"""個股新聞標題：寫進雲端資料庫 watch_news（表名沿用自選股關注 C 票）。

範圍（本人 2026-11-19「股票池中有進入到量大前50名或者是我有搜尋過或者是加入自選的你都要」）：
股票池前一交易日成交金額前 50（stock_news_pool，每日更新寫）∪ 每位使用者的自選 ∪ 每位使用者最近 10 筆搜尋；
太多時先自選、再前 50、再搜尋（MAX_STOCKS）。抓取隨首頁消息一天三輪（market_news.py）與每日更新。

來源與條款：
- ⚑ 本人 2026-11-19「希望你的消息來源更具考靠性，且可以考慮過外的大型新聞」→ Yahoo 逐檔停用，改用 Google News RSS 搜尋，
  只收白名單大媒體（GNEWS_OUTLETS：路透、Bloomberg、中央社、金融時報、華爾街日報、日經）。中文名稱只搜中央社，
  英文簡稱（證交所／櫃買公開資料的英文簡稱，stock_news_names）搜其他大媒體；約 BATCH 檔合成一次搜尋、每次間隔、
  回 429／503 就停掉這一輪剩下的（那幾檔記「沒抓到」）。標題要點名公司才算這一檔的新聞。Google News 沒有摘要、連結是轉址。
- 中央通訊社產經證券、國際、科技 RSS，整批抓，以公司名稱與代號對到名單上的股票；對不到的存成大環境消息（code 是 ''）。
  個人非商業、引用要標「中央通訊社」、保留發稿訊頭、不得引用全文。
每則只存標題、前言、連結、出處、發布時間、取得時間。本人 2026-11-19 00:1x「富果、新聞來源、國外同類股都交給 Gemini
做詳細分析」推翻 11-15「新聞不進 Gemini 的提示」：大盤預測與自選股判斷讀這裡的標題與前言（不給全文、不給連結，
licensed_inputs.headline_items）。
一天抓幾輪，遠低於各家的 ttl（中央社 no-cache），所以不另做條件請求：
條件請求拿到 304 時，新加進名單的股票就對不到中央社已經發過的新聞。來源回 429／503 時這一輪不再打它。
保留 90 天和 cloud/lib/watch.js 的 NEWS_KEEP_DAYS 是同一個數字，要調整得兩邊一起改。
"""
from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit

KEEP_DAYS = 90
SUMMARY_MAX = 200     # 前言上限（字）：RSS 本身給的就是前言，這裡只是保證不會存成全文
CNA_FEEDS = {
    "cna-finance": "https://feeds.feedburner.com/rsscna/finance",
    "cna-intworld": "https://feeds.feedburner.com/rsscna/intworld",
    "cna-technology": "https://feeds.feedburner.com/rsscna/technology",
}
LINK_HOSTS = {source: {"www.cna.com.tw"} for source in CNA_FEEDS}
SLOW_DOWN = {429, 503}
USER_AGENT = "Mozilla/5.0 (compatible; market-ana personal watchlist)"

# ---- Google News 大媒體（本人 2026-11-19） ----
GNEWS_URL = "https://news.google.com/rss/search"
GNEWS_LINK_HOST = "news.google.com"
GNEWS_OUTLETS = {   # 媒體網址的主機（去掉 www.）→ 存進 source 的鍵與畫面上的名稱
    "reuters.com": ("gnews-reuters", "路透"), "bloomberg.com": ("gnews-bloomberg", "Bloomberg"),
    "cna.com.tw": ("gnews-cna", "中央社"), "focustaiwan.tw": ("gnews-cna", "中央社"),
    "ft.com": ("gnews-ft", "金融時報"), "wsj.com": ("gnews-wsj", "華爾街日報"), "asia.nikkei.com": ("gnews-nikkei", "日經"),
}
GNEWS_EN_SITES = ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "asia.nikkei.com")
GNEWS_ZH_SITES = ("cna.com.tw",)
GNEWS_WINDOW = "2d"          # Google 的 when:；用 OR 時不完全有效，所以自己再用 GNEWS_KEEP_DAYS 過濾一次
GNEWS_KEEP_DAYS = 3
BATCH = 10                   # 幾檔合成一次搜尋
GNEWS_PAUSE_SECONDS = 6.0    # 實測連續約 7 次就回 429：每次之間停一下
MAX_STOCKS = 150             # 一輪最多抓幾檔（先自選、再前 50、再搜尋）
SHORT_NAME_EN = 3            # 英文簡稱這麼短（VIS、GWC、UMC）時大小寫要一樣才算點名
STOCK_NEWS_PARAMETERS = {"method": "google-news-whitelist-v1", "outlets": sorted({key for key, _ in GNEWS_OUTLETS.values()}),
                         "zh_sites": list(GNEWS_ZH_SITES), "en_sites": list(GNEWS_EN_SITES), "window": GNEWS_WINDOW,
                         "keep_days": GNEWS_KEEP_DAYS, "title_must_name_company": True, "cna_feeds": sorted(CNA_FEEDS)}
SOURCE_LABELS = {**{key: label for key, label in GNEWS_OUTLETS.values()}, **{source: "中央社" for source in CNA_FEEDS}}
_EMAIL = re.compile(r"[^@\s'\"\\;]+@[^@\s'\"\\;]+")


class FormatChanged(ValueError):
    """回應不是預期的 RSS：停下來、這一份一則都不寫。"""


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _plain(text: str) -> str:
    """前言去掉標籤與多餘空白，再截到 SUMMARY_MAX 字。"""
    stripped = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    stripped = re.sub(r"\s+", " ", stripped).strip()
    return stripped[:SUMMARY_MAX]


def _utc(text: str) -> str:
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError) as error:
        raise FormatChanged(f"發布時間看不懂：{text!r}") from error
    if moment is None or moment.tzinfo is None:
        raise FormatChanged(f"發布時間沒有時區：{text!r}")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(content: bytes, *, source: str) -> list[dict]:
    """RSS 2.0 → [{title, summary, link, published_at}]。任何一則缺欄位、時間看不懂、連結不是那一家的網址，整份停下。"""
    if b"<!DOCTYPE" in content[:2048].upper() or b"<!ENTITY" in content.upper():
        raise FormatChanged("格式不對：含有 DOCTYPE／ENTITY")   # 外部 XML 不接受實體宣告
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise FormatChanged(f"格式不對：不是 XML（{error}）") from error
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FormatChanged(f"格式不對：根節點是 {root.tag}")
    items = []
    for node in channel.findall("item"):
        title, link = _text(node, "title"), _text(node, "link")
        if not title or not link:
            raise FormatChanged("格式不對：有一則沒有標題或連結")
        parts = urlsplit(link)
        if parts.scheme != "https" or parts.hostname not in LINK_HOSTS[source]:
            raise FormatChanged(f"格式不對：連結不是 {source} 的網址")
        items.append({"title": re.sub(r"\s+", " ", title), "summary": _plain(_text(node, "description")),
                      "link": link, "published_at": _utc(_text(node, "pubDate"))})
    return items


def _rss_channel(content: bytes):
    if b"<!DOCTYPE" in content[:2048].upper() or b"<!ENTITY" in content.upper():
        raise FormatChanged("格式不對：含有 DOCTYPE／ENTITY")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as error:
        raise FormatChanged(f"格式不對：不是 XML（{error}）") from error
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FormatChanged(f"格式不對：根節點是 {root.tag}")
    return channel


def _outlet(url: str) -> tuple[str, str] | None:
    host = (urlsplit(url or "").hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return GNEWS_OUTLETS.get(host)


def parse_gnews(content: bytes) -> list[dict]:
    """Google News 搜尋 RSS → 白名單媒體的 [{title, summary:'', link, published_at, source}]；其他媒體丟掉。
    標題結尾的「 - 媒體名」拿掉；連結必須是 news.google.com 的轉址。"""
    items = []
    for node in _rss_channel(content).findall("item"):
        title, link = _text(node, "title"), _text(node, "link")
        source = node.find("source")
        outlet = _outlet(source.get("url") if source is not None else "")
        if not outlet or not title or not link:
            continue
        parts = urlsplit(link)
        if parts.scheme != "https" or parts.hostname != GNEWS_LINK_HOST:
            raise FormatChanged("格式不對：連結不是 Google News 的轉址")
        title = re.sub(r"\s+", " ", title)
        title = re.sub(r"\s+-\s+[^-]{1,40}$", "", title).strip()
        title = re.sub(r"\s*[|｜]\s*[^|｜]{1,6}$", "", title).strip()   # 中央社標題尾巴的「| 證券」分類
        items.append({"title": title, "summary": "", "link": link, "published_at": _utc(_text(node, "pubDate")),
                      "source": outlet[0]})
    return items


def _name_en(stock: dict) -> str:
    return re.sub(r"[\s　]+", " ", str(stock.get("name_en") or "")).strip()


def _names_company(title: str, stock: dict) -> bool:
    if _mentions(title, stock):
        return True
    english = _name_en(stock)
    if not english:
        return False
    flags = 0 if len(english) <= SHORT_NAME_EN else re.IGNORECASE
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(english)}(?![A-Za-z0-9])", title, flags) is not None


def match_gnews(rows: list[dict], stocks: list[dict]) -> list[dict]:
    """標題點名公司（中文簡稱、代號或英文簡稱）才算那一檔；一則點名幾檔就存幾份，誰都沒點名就丟掉。"""
    return [{**row, "code": stock["code"]} for row in rows for stock in stocks if _names_company(row["title"], stock)]


def _gnews_url(terms: list[str], sites: tuple[str, ...], lang: str) -> str:
    from urllib.parse import urlencode
    names = " OR ".join(terms)
    site = " OR ".join(f"site:{s}" for s in sites)
    query = f"({names}) ({site}) when:{GNEWS_WINDOW}" if len(sites) > 1 else f"({names}) site:{sites[0]} when:{GNEWS_WINDOW}"
    locale = {"zh": {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"}}[lang]
    return f"{GNEWS_URL}?{urlencode({'q': query, **locale})}"


def gnews_queries(stocks: list[dict], *, batch: int = BATCH) -> list[dict]:
    """中文簡稱一批一次（只搜中央社）、英文簡稱一批一次（搜其他大媒體）；先全部中文、再全部英文。"""
    out = []
    zh = [stock for stock in stocks if len(str(stock.get("name") or "")) >= 2]
    for start in range(0, len(zh), batch):
        group = zh[start:start + batch]
        out.append({"lang": "zh", "stocks": group, "url": _gnews_url([s["name"] for s in group], GNEWS_ZH_SITES, "zh")})
    en = [stock for stock in stocks if _name_en(stock)]
    for start in range(0, len(en), batch):
        group = en[start:start + batch]
        out.append({"lang": "en", "stocks": group,
                    "url": _gnews_url([f'"{_name_en(s)}"' for s in group], GNEWS_EN_SITES, "en")})
    return out


def _http_get(url: str, params=None):
    from types import SimpleNamespace

    import requests
    response = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    return SimpleNamespace(status=response.status_code, content=response.content)


def _fetch(get: Callable, url: str, source: str) -> tuple[list[dict] | None, str | None, bool]:
    """抓一份。回傳 (items, error, 來源要求放慢)；items 是 None 代表這一份沒抓到。"""
    try:
        response = get(url, None)
        if response.status != 200:
            return None, f"HTTP {response.status}", response.status in SLOW_DOWN
        return (parse_gnews(response.content) if source == "gnews" else parse(response.content, source=source)), None, False
    except FormatChanged as error:
        return None, str(error), False
    except Exception as error:  # noqa: BLE001 — 一份失敗只記錄，不影響其他來源
        return None, f"{type(error).__name__}: {error}"[:200], False


def _mentions(text: str, stock: dict) -> bool:
    name = (stock.get("name") or "").strip()
    return (len(name) >= 2 and name in text) or re.search(rf"(?<!\d){re.escape(stock['code'])}(?!\d)", text) is not None


def gather(stocks: list[dict], *, get: Callable, now: str, sleep: Callable[[float], None]) -> dict:
    """抓 Google News 大媒體（約 BATCH 檔一次）與中央社（三個分類各一次）。回傳要寫的新聞與每個來源這一次的結果。
    Google News 的結果按股票記（source 'gnews'）：那一檔的任何一次搜尋失敗就記那個錯。"""
    news: list[dict] = []
    fetches: list[dict] = []

    def record(source, code, error):
        fetches.append({"source": source, "code": code, "error": error})

    oldest = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=GNEWS_KEEP_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    errors: dict[str, str | None] = {stock["code"]: None for stock in stocks}
    slowed = False
    seen: set[tuple[str, str]] = set()
    for index, query in enumerate(gnews_queries(stocks)):
        if slowed:
            for stock in query["stocks"]:
                errors[stock["code"]] = errors[stock["code"]] or "來源要求放慢，這一輪沒抓"
            continue
        if index:
            sleep(GNEWS_PAUSE_SECONDS)
        items, error, slowed = _fetch(get, query["url"], "gnews")
        if items is None:
            for stock in query["stocks"]:
                errors[stock["code"]] = errors[stock["code"]] or error
            continue
        for row in match_gnews([item for item in items if item["published_at"] >= oldest], query["stocks"]):
            if (row["code"], row["link"]) not in seen:
                seen.add((row["code"], row["link"]))
                news.append(row)
    for code, error in errors.items():
        record("gnews", code, error)

    for source, url in CNA_FEEDS.items():
        items, error, _ = _fetch(get, url, source)
        record(source, "", error)
        for item in items or []:
            text = f"{item['title']} {item['summary']}"
            codes = [stock["code"] for stock in stocks if _mentions(text, stock)] or [""]
            news += [{**item, "source": source, "code": code} for code in codes]
    return {"news": news, "fetches": fetches, "fetched_at": now}


def select_stocks(watch: list[dict], pool: list[dict], searches: list[dict], names_en: dict[str, str], *,
                  cap: int = MAX_STOCKS) -> list[dict]:
    """抓取名單：自選 → 股票池前 50 → 搜尋，同一檔只留第一次；超過 cap 的捨去。附上英文簡稱（沒有就 None）。"""
    out, seen = [], set()
    for row in [*watch, *pool, *searches]:
        code = str(row.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        out.append({"code": code, "name": row.get("name") or "", "market": row.get("market") or "",
                    "name_en": names_en.get(code) or None})
    return out[:cap]


def pool_top(listed, otc, universe: dict, *, top: int = 50) -> list[dict]:
    """股票池（liquid_universe 的 members）裡，最新一個交易日成交金額前 top 名。listed／otc 是行情庫的 sqlite 連線。"""
    members = {str(m["code"]): m.get("market") for m in universe.get("members") or []}
    rows = []
    for connection, market in ((listed, "上市"), (otc, "上櫃")):
        latest = connection.execute("SELECT max(date) FROM daily WHERE kind = 'common'").fetchone()[0]
        if not latest:
            continue
        for code, name, turnover in connection.execute(
                "SELECT code, name, turnover FROM daily WHERE date = ? AND kind = 'common' AND turnover IS NOT NULL", (latest,)):
            if code in members:
                rows.append({"code": code, "name": name, "market": members[code] or market, "turnover": turnover,
                             "data_date": latest})
    rows.sort(key=lambda row: (-row["turnover"], row["code"]))
    return [{**{k: v for k, v in row.items() if k != "turnover"}, "rank": rank} for rank, row in enumerate(rows[:top], 1)]


def english_names(twse_rows: list[dict], tpex_rows: list[dict]) -> dict[str, str]:
    """證交所（英文簡稱）與櫃買（Symbol）的公司基本資料 → {代號: 英文簡稱}；全形空白去掉，空的不收。"""
    out = {}
    for rows, code_key, name_key in ((twse_rows, "公司代號", "英文簡稱"), (tpex_rows, "SecuritiesCompanyCode", "Symbol")):
        for row in rows:
            name = _name_en({"name_en": row.get(name_key)})
            if row.get(code_key) and name:
                out[str(row[code_key]).strip()] = name
    return out


def _q(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("\x00", "").replace("'", "''") + "'"


def sql(result: dict, *, keep_days: int = KEEP_DAYS) -> str:
    """寫入雲端資料庫的指令檔：新聞同一則不重複存 → 更新每個來源的結果 → 清掉超過保留期限的。"""
    now = result["fetched_at"]
    cutoff = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=keep_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = []
    for item in result["news"]:
        lines.append("INSERT OR IGNORE INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES ("
                     + ", ".join(_q(item[key]) for key in ("code", "link", "source", "title", "summary", "published_at"))
                     + f", {_q(now)});")
    for fetch in result["fetches"]:
        success = "NULL" if fetch["error"] else _q(now)
        lines.append("INSERT INTO watch_news_fetches (source, code, last_attempt_at, last_success_at, last_error) VALUES ("
                     f"{_q(fetch['source'])}, {_q(fetch['code'])}, {_q(now)}, {success}, {_q(fetch['error'])}) "
                     "ON CONFLICT (source, code) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
                     "last_success_at = COALESCE(excluded.last_success_at, watch_news_fetches.last_success_at), "
                     "last_error = excluded.last_error;")
    lines.append(f"DELETE FROM watch_news WHERE published_at < {_q(cutoff)};")
    return "\n".join(lines) + "\n"


def daily(owner_email: str, *, query: Callable[[str], list[dict]], execute: Callable[[str], None],
          get: Callable | None = None, now: str | None = None, sleep: Callable[[float], None] | None = None) -> dict:
    """讀抓取名單（每個人的自選 → 股票池前 50 → 每個人最近的搜尋）→ 抓 → 一次寫回。
    watch_news 還沒建立就什麼都不抓；0031 還沒套時只抓自選與搜尋（沒有前 50、沒有英文簡稱）。
    owner_email 只用來先確認排程設定沒填錯（名單不再只限站主）。"""
    if not _EMAIL.fullmatch(owner_email or ""):
        raise ValueError("站主 email 格式不對")
    import time
    get, sleep = get or _http_get, sleep or time.sleep
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    present = {row["name"] for row in query(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN "
        "('watch_news', 'watch_news_fetches', 'stock_news_pool', 'stock_news_names')")}
    if not {"watch_news", "watch_news_fetches"} <= present:
        return {"status": "not-migrated"}
    watch = query("SELECT code, max(name) AS name, max(market) AS market FROM user_watchlist "
                  "GROUP BY code ORDER BY min(created_at), code")
    searches = query("SELECT code, max(name) AS name, max(market) AS market FROM user_search_history "
                     "GROUP BY code ORDER BY max(viewed_at) DESC, code")
    pool = query("SELECT code, name, market FROM stock_news_pool ORDER BY rank") if "stock_news_pool" in present else []
    names = ({row["code"]: row["name_en"] for row in query("SELECT code, name_en FROM stock_news_names")}
             if "stock_news_names" in present else {})
    stocks = select_stocks(watch, pool, searches, names)
    result = gather(stocks, get=get, now=now, sleep=sleep)
    execute(sql(result))
    after = int(query("SELECT COUNT(*) AS n FROM watch_news WHERE fetched_at = " + _q(now))[0]["n"])
    sources = {f"{f['source']}:{f['code']}" if f["code"] else f["source"]:
               {"status": "error" if f["error"] else "ok", **({"reason": f["error"]} if f["error"] else {})}
               for f in result["fetches"]}
    return {"status": "ok", "stocks": len(stocks), "stored": after, "sources": sources}


TWSE_PROFILE = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"        # 上市公司基本資料（英文簡稱）
TPEX_PROFILE = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"      # 上櫃公司基本資料（Symbol）


def pool_sql(pool: list[dict], names: dict[str, str], *, now: str) -> str:
    """前 50 整份換掉；英文簡稱有拿到才更新（拿不到時沿用上一次的）。"""
    lines = ["DELETE FROM stock_news_pool;"]
    lines += [f"INSERT INTO stock_news_pool (code, name, market, rank, data_date) VALUES ({_q(row['code'])}, "
              f"{_q(row['name'])}, {_q(row['market'])}, {int(row['rank'])}, {_q(row['data_date'])});" for row in pool]
    lines += [f"INSERT INTO stock_news_names (code, name_en, updated_at) VALUES ({_q(code)}, {_q(name)}, {_q(now)}) "
              "ON CONFLICT (code) DO UPDATE SET name_en = excluded.name_en, updated_at = excluded.updated_at;"
              for code, name in sorted(names.items())]
    return "\n".join(lines) + "\n"


def build_pool(root, *, get: Callable | None = None, now: str | None = None) -> tuple[str, dict]:
    """每日更新發布後：最新的股票池快照＋行情庫 → 前 50；證交所與櫃買公開資料 → 英文簡稱。回傳 (SQL, 摘要)。"""
    import sqlite3
    from pathlib import Path
    root = Path(root)
    get = get or _http_get
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshots = sorted((root / "data/liquid-universe").glob("????-??-??.json"))
    if not snapshots:
        raise FileNotFoundError("沒有股票池快照 data/liquid-universe/*.json")
    universe = json.loads(snapshots[-1].read_text(encoding="utf-8"))
    connect = lambda path: sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)  # noqa: E731
    listed, otc = connect(root / "data/stock-history/stock-history.sqlite"), connect(root / "data/otc-history/otc_history.sqlite")
    try:
        pool = pool_top(listed, otc, universe)
    finally:
        listed.close()
        otc.close()
    rows: dict[str, list] = {}
    errors = {}
    for key, url in (("twse", TWSE_PROFILE), ("tpex", TPEX_PROFILE)):
        try:
            response = get(url, None)
            if response.status != 200:
                raise ValueError(f"HTTP {response.status}")
            rows[key] = json.loads(response.content.decode("utf-8-sig"))
        except Exception as error:  # noqa: BLE001 — 拿不到英文簡稱只少了英文搜尋，沿用上一次的
            rows[key], errors[key] = [], f"{type(error).__name__}: {error}"[:200]
    names = english_names(rows["twse"], rows["tpex"])
    summary = {"pool": len(pool), "data_date": pool[0]["data_date"] if pool else None, "names_en": len(names),
               **({"errors": errors} if errors else {})}
    return pool_sql(pool, names, now=now), summary


# ---- 雲端資料庫：每日更新的工作流程用 wrangler 讀寫（和每日更新的其他 D1 步驟同一個做法） ----

def _wrangler_query(sql_text: str) -> list[dict]:
    import daily_update as du
    result = du._sh(du._npx("d1", "execute", du.D1_DATABASE, "--remote", "--json", "--command", sql_text), cwd=du.CLOUD)
    text = result.get("_stdout") or ""
    return json.loads(text[text.index("["):])[0]["results"]


def _wrangler_execute(script: str) -> None:
    import tempfile
    from pathlib import Path

    import daily_update as du
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "watch-news.sql"
        path.write_text(script, encoding="utf-8", newline="\n")
        du._sh(du._npx("d1", "execute", du.D1_DATABASE, "--remote", "--file", str(path.resolve())), cwd=du.CLOUD)


if __name__ == "__main__":
    import os
    if sys.argv[1:] == ["daily", "--d1"]:
        print(json.dumps(daily(os.environ.get("OWNER_EMAIL", ""), query=_wrangler_query, execute=_wrangler_execute),
                         ensure_ascii=False))
    elif sys.argv[1:] == ["pool", "--d1"]:
        # 每日更新發布後：寫前 50 與英文簡稱（0031 還沒套時 wrangler 會失敗，排程那一步 continue-on-error）
        from pathlib import Path
        script, summary = build_pool(Path(__file__).resolve().parent)
        _wrangler_execute(script)
        print(json.dumps(summary, ensure_ascii=False))
    elif sys.argv[1:2] == ["probe"]:
        import time   # 用真的網路抓一次、不寫資料庫：python watch_news.py probe 2330:台積電:上市:TSMC 6488:環球晶:上櫃:GWC
        stocks = [dict(zip(("code", "name", "market", "name_en"), arg.split(":"))) for arg in sys.argv[2:]]
        result = gather(stocks, get=_http_get, now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        sleep=time.sleep)
        print(json.dumps({"fetches": result["fetches"], "news": result["news"]}, ensure_ascii=False, indent=1))
    else:
        print("用法：python watch_news.py daily --d1 ｜ pool --d1 ｜ probe 代號:名稱:市場:英文簡稱 ...", file=sys.stderr)
        sys.exit(2)
