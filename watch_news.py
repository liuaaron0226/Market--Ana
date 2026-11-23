"""個股新聞標題：寫進雲端資料庫 watch_news（表名沿用自選股關注 C 票）。

⚑ 2026-11-20 第二輪（本人「我的自選股不可能都沒新聞…影響股票漲跌比較多的消息還是來自官方或者是政府的政策
還有合作關係跟財報」；題目「個股新聞要加回鉅亨網、經濟日報、工商時報、MoneyDJ 嗎？」答「加回（推薦）」）：
個股搜尋改一檔一次、加回台灣財經媒體四家、第一次進名單回補 30 天、濾掉自動稿、重大訊息也排進來。
下面 11-19 的說明裡「只收大媒體」「十檔合一次」已被這一段取代。

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
GNEWS_OUTLETS = {   # 媒體網址的主機（去掉 www.；子網域也算）→ 存進 source 的鍵與畫面上的名稱
    "reuters.com": ("gnews-reuters", "路透"), "bloomberg.com": ("gnews-bloomberg", "Bloomberg"),
    "cna.com.tw": ("gnews-cna", "中央通訊社"), "focustaiwan.tw": ("gnews-cna", "中央通訊社"),
    "ft.com": ("gnews-ft", "金融時報"), "wsj.com": ("gnews-wsj", "華爾街日報"), "asia.nikkei.com": ("gnews-nikkei", "日經"),
    # 本人 2026-11-20「加回（推薦）」：大媒體不寫中型股（站主自選 8 檔有 7 檔 0 則），個股新聞加回台灣財經媒體四家。
    # 只用在個股新聞；首頁市場三組不用（market_news.FEEDS）。
    "cnyes.com": ("gnews-cnyes", "鉅亨網"), "money.udn.com": ("gnews-udn", "經濟日報"),
    "ctee.com.tw": ("gnews-ctee", "工商時報"), "moneydj.com": ("gnews-moneydj", "MoneyDJ"),
}
GNEWS_EN_SITES = ("reuters.com", "bloomberg.com", "ft.com", "wsj.com", "asia.nikkei.com")
GNEWS_ZH_SITES = ("cna.com.tw", "cnyes.com", "money.udn.com", "ctee.com.tw", "moneydj.com")
# 個股搜尋一檔一次：「"公司名" (代號 OR 股 OR 營收 OR 法說 OR 財報)」。十檔合一次時大公司把小公司擠掉、
# 兩個字的公司名撞到普通詞（「創意」風箏），2026-11-20 改。
STOCK_QUALIFIERS = ("股", "營收", "法說", "財報")
GNEWS_WINDOW = "2d"          # 平常每輪往回看幾天（Google 的 when:；自己再用發布時間過濾一次）
GNEWS_KEEP_DAYS = 3
BACKFILL_WINDOW = "30d"      # 一檔第一次進名單：回補 30 天（本人「你應該要去看前陣子有什麼相關消息」）
BACKFILL_DAYS = 30
PER_QUERY_KEEP = 40          # 一次搜尋一檔最多留幾則（最新的）
BATCH = 10                   # 英文簡稱幾檔合成一次搜尋
POOL_BATCH = 5               # 股票池前 50（不在任何人自選或搜尋裡的）幾檔合一次
MAX_QUERIES = 28             # 一輪最多幾次 Google News 搜尋（每次間隔 GNEWS_PAUSE_SECONDS；剩下的下一輪接著抓）
GNEWS_PAUSE_SECONDS = 7.0    # 實測連續約 7 次就回 429：每次之間停一下
MAX_STOCKS = 150             # 名單最多幾檔（先自選、再搜尋、再前 50）
SHORT_NAME_EN = 3            # 英文簡稱這麼短（VIS、GWC、UMC）時大小寫要一樣才算點名
# 自動稿（漲跌停快報、機構預估彙整）不是新聞，不存。
NOISE_TITLE = re.compile(r"盤中速報|鉅亨速報|Factset\s*最新調查|股價(?:殺至|拉至|觸及|打入|亮燈)?(?:跌停|漲停)，")
MOPS_LINK = "https://mops.twse.com.tw/mops/#/web/t05st01"   # 公開資訊觀測站 重大訊息查詢
# 關注股每日判斷（J 系列）讀的個股新聞：維持 11-19 那一組來源與參數，輸入版本不變；
# 要不要讓判斷也讀台灣財經媒體與重大訊息，另外問本人。這個常數寫死，不跟著 GNEWS_OUTLETS 變。
STOCK_NEWS_PARAMETERS = {"method": "google-news-whitelist-v1",
                         "outlets": ["gnews-bloomberg", "gnews-cna", "gnews-ft", "gnews-nikkei", "gnews-reuters", "gnews-wsj"],
                         "zh_sites": ["cna.com.tw"], "en_sites": list(GNEWS_EN_SITES), "window": "2d",
                         "keep_days": 3, "title_must_name_company": True, "cna_feeds": sorted(CNA_FEEDS)}
JUDGMENT_SOURCES = ("yahoo", *sorted(CNA_FEEDS), *STOCK_NEWS_PARAMETERS["outlets"])
SOURCE_LABELS = {**{key: label for key, label in GNEWS_OUTLETS.values()}, **{source: "中央通訊社" for source in CNA_FEEDS},
                 "mops": "公開資訊觀測站"}
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
    return next((outlet for site, outlet in GNEWS_OUTLETS.items() if host == site or host.endswith("." + site)), None)


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
        # 標題尾巴的版面名稱：中央社「| 證券」、經濟日報「| 集中市場 | 證券」、工商與 MoneyDJ「- 日報」「- 證券」「- 新聞」
        for _ in range(3):
            title = re.sub(r"\s*[|｜]\s*[^|｜]{1,6}$", "", title).strip()
            title = re.sub(r"\s*-\s*(?:日報|證券|新聞|產業|科技|財經|國際|期貨|基金|焦點|即時)$", "", title).strip()
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


def search_name(stock: dict) -> str:
    """搜尋與比對用的公司名：去掉「-KY」「-DR」「*」這類尾巴（世芯-KY → 世芯）。"""
    return re.sub(r"(?:-KY|-DR|\*)+$", "", str(stock.get("name") or "").strip())


def is_noise(title: str) -> bool:
    return NOISE_TITLE.search(title or "") is not None


def _gnews_url(query: str, lang: str) -> str:
    from urllib.parse import urlencode
    locale = {"zh": {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}, "en": {"hl": "en-US", "gl": "US", "ceid": "US:en"}}[lang]
    return f"{GNEWS_URL}?{urlencode({'q': query, **locale})}"


def _sites(sites: tuple[str, ...]) -> str:
    return "(" + " OR ".join(f"site:{site}" for site in sites) + ")"


def stock_query(stock: dict, window: str) -> str:
    """一檔的中文搜尋字串：公司名加引號，再加「代號或股、營收、法說、財報」其中之一。"""
    qualifiers = " OR ".join((stock["code"], *STOCK_QUALIFIERS))
    return f'"{search_name(stock)}" ({qualifiers}) {_sites(GNEWS_ZH_SITES)} when:{window}'


def gnews_plan(stocks: list[dict], state: dict[tuple[str, str], dict] | None = None, *, budget: int = MAX_QUERIES) -> list[dict]:
    """這一輪要發哪些搜尋（最多 budget 次）。
    - 自選與搜尋過的（tier 不是 pool）：一檔一次。還沒回補過的排最前面、往回 30 天；其餘照上次抓的時間，最久沒抓的先。
    - 股票池前 50 裡其他的：POOL_BATCH 檔合一次，只看近幾天。
    - 有英文簡稱的：BATCH 檔合一次搜英文大媒體。
    state：watch_news_fetches 的 {(source, code): 列}，用來知道誰回補過、誰最久沒抓。"""
    state = state or {}
    named = [stock for stock in stocks if len(search_name(stock)) >= 2]
    own = [stock for stock in named if stock.get("tier") != "pool"]
    done = lambda stock: bool((state.get(("gnews-backfill", stock["code"])) or {}).get("last_success_at"))  # noqa: E731
    last = lambda stock: (state.get(("gnews", stock["code"])) or {}).get("last_attempt_at") or ""  # noqa: E731
    own.sort(key=lambda stock: (done(stock), last(stock)))
    plan = []
    for stock in own:
        backfill = not done(stock)
        window = BACKFILL_WINDOW if backfill else GNEWS_WINDOW
        plan.append({"lang": "zh", "stocks": [stock], "backfill": backfill,
                     "keep_days": BACKFILL_DAYS if backfill else GNEWS_KEEP_DAYS, "url": _gnews_url(stock_query(stock, window), "zh")})
    pool = sorted((stock for stock in named if stock.get("tier") == "pool"), key=last)
    for start in range(0, len(pool), POOL_BATCH):
        group = pool[start:start + POOL_BATCH]
        names = " OR ".join(f'"{search_name(stock)}"' for stock in group)
        plan.append({"lang": "zh", "stocks": group, "backfill": False, "keep_days": GNEWS_KEEP_DAYS,
                     "url": _gnews_url(f"({names}) ({' OR '.join(STOCK_QUALIFIERS)}) {_sites(GNEWS_ZH_SITES)} when:{GNEWS_WINDOW}", "zh")})
    english = [stock for stock in stocks if _name_en(stock)]
    for start in range(0, len(english), BATCH):
        group = english[start:start + BATCH]
        names = " OR ".join(f'"{_name_en(stock)}"' for stock in group)
        plan.append({"lang": "en", "stocks": group, "backfill": False, "keep_days": GNEWS_KEEP_DAYS,
                     "url": _gnews_url(f"({names}) {_sites(GNEWS_EN_SITES)} when:{GNEWS_WINDOW}", "en")})
    return plan[:budget]


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


# 公司簡稱只有兩個字時常常同時是一般用語（創意、世界、統一、國產、大同…）：光是標題裡出現那兩個字不算點名公司，
# 同一段文字還要有股市／公司營運的用語才算（本人 2026-11-23：創意的消息混進「有創意」「創意料理」這種完全無關的新聞）。
# 三個字以上的簡稱照舊；標題有股票代號的一律算。這份用語表和 cloud/lib/news-relevance.js 的要一致。
MARKET_CONTEXT = (
    "股", "證交所", "櫃買", "金管會", "上市", "上櫃", "興櫃", "營收", "財報", "財測", "法說", "獲利", "業績", "毛利", "EPS", "本益比", "市值",
    "法人", "外資", "投信", "自營", "漲停", "跌停", "違約交割", "處置", "目標價", "評等", "訂單", "出貨", "擴產", "產能", "併購", "增資", "減資",
    "除息", "除權", "配息", "股利", "庫藏", "董事會", "董事長", "總經理", "半導體", "晶片", "晶圓", "ASIC", "IC設計", "ETF", "供應鏈",
)
SHORT_NAME = 2


def _mentions(text: str, stock: dict) -> bool:
    name = search_name(stock)
    if re.search(rf"(?<!\d){re.escape(stock['code'])}(?!\d)", text) is not None:
        return True
    if len(name) < 2 or name not in text:
        return False
    if len(name) > SHORT_NAME:
        return True
    rest = text.replace(name, " ")   # 公司名本身帶的字不算（例如簡稱裡就有「股」）
    return any(word in rest for word in MARKET_CONTEXT)


def gather(stocks: list[dict], *, get: Callable, now: str, sleep: Callable[[float], None],
           market_wide_unless: set[str] | None = None, state: dict[tuple[str, str], dict] | None = None,
           budget: int = MAX_QUERIES) -> dict:
    """照 gnews_plan 發 Google News 搜尋，再抓中央社三個分類。回傳要寫的新聞與每個來源這一次的結果。
    Google News 的結果按股票記（source 'gnews'）：有試過的才記，那一檔的任何一次搜尋失敗就記那個錯；
    回補成功另記一列（source 'gnews-backfill'）。來源回 429／503 就停掉這一輪剩下的搜尋（那幾檔記「這一輪沒抓」）。
    中央社：對到名單上的哪幾檔就存給哪幾檔；另外，沒對到 market_wide_unless（站主關注中的股票）的那幾則存成大環境消息
    （code ''，大盤預測讀這裡）——名單變大不影響大盤預測讀到的範圍。沒給就是「誰都沒對到才算」。"""
    news: list[dict] = []
    fetches: list[dict] = []

    def record(source, code, error):
        fetches.append({"source": source, "code": code, "error": error})

    moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
    errors: dict[str, str | None] = {}
    backfilled: list[str] = []
    slowed = False
    seen: set[tuple[str, str]] = set()
    for index, query in enumerate(gnews_plan(stocks, state, budget=budget)):
        codes = [stock["code"] for stock in query["stocks"]]
        if slowed:
            for code in codes:
                errors[code] = errors.get(code) or "來源要求放慢，這一輪沒抓"
            continue
        if index:
            sleep(GNEWS_PAUSE_SECONDS)
        items, error, slowed = _fetch(get, query["url"], "gnews")
        for code in codes:
            errors[code] = errors.get(code) or error
        if items is None:
            continue
        oldest = (moment - timedelta(days=query["keep_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = [item for item in items if item["published_at"] >= oldest and not is_noise(item["title"])]
        kept: dict[str, int] = {}
        for row in sorted(match_gnews(fresh, query["stocks"]), key=lambda row: row["published_at"], reverse=True):
            if (row["code"], row["link"]) in seen or kept.get(row["code"], 0) >= PER_QUERY_KEEP:
                continue
            seen.add((row["code"], row["link"]))
            kept[row["code"]] = kept.get(row["code"], 0) + 1
            news.append(row)
        if query["backfill"]:
            backfilled += codes
    for code, error in errors.items():
        record("gnews", code, error)
    for code in backfilled:
        record("gnews-backfill", code, None)

    for source, url in CNA_FEEDS.items():
        items, error, _ = _fetch(get, url, source)
        record(source, "", error)
        for item in items or []:
            text = f"{item['title']} {item['summary']}"
            codes = [stock["code"] for stock in stocks if _mentions(text, stock)]
            wide = not codes if market_wide_unless is None else not any(code in market_wide_unless for code in codes)
            news += [{**item, "source": source, "code": code} for code in codes + ([""] if wide else [])]
    return {"news": news, "fetches": fetches, "fetched_at": now}


def announcement_rows(payloads: list[dict]) -> list[dict]:
    """公開資訊觀測站重大訊息（網站資料庫 stock_announcements 的 payload）→ 個股新聞的列（source 'mops'）。
    標題是主旨、前言是說明的開頭；連結是重大訊息查詢頁（後面帶代號與時間，只為了讓每則不重複）。"""
    rows = []
    for payload in payloads:
        try:
            items = json.loads(payload["payload"]).get("items") or []
        except (KeyError, TypeError, ValueError):
            continue
        for item in items:
            try:
                local = datetime.strptime(f"{item['date']} {item.get('time') or '00:00:00'}", "%Y-%m-%d %H:%M:%S")
                code, subject = str(item["code"]), re.sub(r"\s+", " ", str(item["subject"])).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not subject:
                continue
            published = (local - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
            rows.append({"code": code, "source": "mops", "title": subject[:200],
                         "summary": re.sub(r"\s+", " ", str(item.get("body") or ""))[:SUMMARY_MAX],
                         "link": f"{MOPS_LINK}?co={code}&t={local.strftime('%Y%m%d%H%M%S')}", "published_at": published})
    return rows


def select_stocks(watch: list[dict], pool: list[dict], searches: list[dict], names_en: dict[str, str], *,
                  cap: int = MAX_STOCKS) -> list[dict]:
    """抓取名單：自選 → 搜尋 → 股票池前 50，同一檔只留第一次（tier 記它是從哪一份來的）；超過 cap 的捨去。
    附上英文簡稱（沒有就 None）。"""
    out, seen = [], set()
    for tier, rows in (("watch", watch), ("search", searches), ("pool", pool)):
        for row in rows:
            code = str(row.get("code") or "")
            if not code or code in seen:
                continue
            seen.add(code)
            out.append({"code": code, "name": row.get("name") or "", "market": row.get("market") or "",
                        "name_en": names_en.get(code) or None, "tier": tier})
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
    watching = {row["code"] for row in query("SELECT code FROM user_watchlist "
                                              f"WHERE lower(email) = lower({_q(owner_email)}) AND watching = 1")}
    state = {(row["source"], row["code"]): row for row in query(
        "SELECT source, code, last_attempt_at, last_success_at FROM watch_news_fetches WHERE source IN ('gnews', 'gnews-backfill')")}
    result = gather(stocks, get=get, now=now, sleep=sleep, market_wide_unless=watching, state=state)
    # 重大訊息（公開資訊觀測站）：名單上的股票，網站資料庫目前那一版裡有的都放進個股新聞。表還沒建就略過。
    try:
        listed = ",".join(_q(stock["code"]) for stock in stocks)
        payloads = query("SELECT a.code, a.payload FROM stock_data_state s JOIN stock_announcements a ON a.version = s.active_version "
                         f"WHERE s.key = 'current' AND a.code IN ({listed})") if stocks else []
        result["news"] += announcement_rows(payloads)
        result["fetches"].append({"source": "mops", "code": "", "error": None})
    except Exception as error:  # noqa: BLE001 — 重大訊息讀不到只少這一類
        result["fetches"].append({"source": "mops", "code": "", "error": f"{type(error).__name__}"[:200]})
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
        # 倉庫公開之後排程的輸出誰都看得到：來源不逐檔印（gnews:代號 看得出誰的自選有哪幾檔），只印各來源成功、失敗幾檔；
        # 逐檔的結果本來就寫在資料庫的 watch_news_fetches。
        import public_log
        print(json.dumps(public_log.watch_news_daily(
            daily(os.environ.get("OWNER_EMAIL", ""), query=_wrangler_query, execute=_wrangler_execute)), ensure_ascii=False))
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
