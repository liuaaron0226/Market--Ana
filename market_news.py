"""首頁「消息」：台股、美股、日韓中的利多／利空，和每個人「我的自選」的新聞（本人 2026-11-19「共識達成」）。

本人原話：「主要是做市場的利多以及利空消息……想知道為什麼今天的股票會大漲或者是會大跌或是不怎麼動」。
定案（.scratch/market-news/consensus.md）：
- 交易日三輪（morning 08:00、midday 11:30、close 14:00，台北）；本人 2026-11-24 加開三班（evening 18:30、us_open 23:00、
  us_close 06:30；見 SLOTS）。每輪抓一次市場來源 → 站主的模型（預設 Flash 高，
  設定頁 AI 模型的「news」）一次判完：每則利多／利空／中性、影響大中小、中文標題、一句理由，再寫「今天為什麼這樣走」。
- 市場來源只用通訊社與國際大報（FEEDS；本人「希望你的消息來源更具考靠性，且可以考慮過外的大型新聞」）。
  路透沒有公開 RSS，經 Google News 搜尋並只收白名單媒體（watch_news.parse_gnews）。
- 「我的自選」：新聞在 watch_news（個股新聞，watch_news.py 每輪先抓）；站主的併在同一次呼叫；朋友用自己的金鑰
  （朋友 AI 解讀那一把）各叫一次；沒有金鑰的朋友網站只顯示標題（這裡不寫他的列）。
- 模型沒寫成：標題照樣存，標成 unclassified（網站不顯示利多利空與理由），不編內容。
- 只送標題、RSS 摘要、出處與時間給模型；不送連結、不抓全文。保留 KEEP_DAYS 天（日期鍵看近 1 個月）。
"""
from __future__ import annotations

import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import impact_text
import impact_watch
import impact_watch_news
import watch_news as wn

TAIPEI = timezone(timedelta(hours=8))
# 一天六班，照台北當天的先後排（本人 2026-11-24 加開晚間與清晨三班，.scratch/market-news-night 票 01）：
# us_close 美股收盤 06:30 → morning 盤前 08:00 → midday 午盤 11:30 → close 收盤後 14:00 → evening 盤後 18:30 → us_open 美股開盤 23:00。
# 06:30 那一班算當天的（排在盤前之前）。這個順序和 market_brief.NEWS_SLOT_ORDER、web/market-news.js 的時段標籤要一致；
# 網站的「最新一班」照 created_at（cloud/lib/market-news.js 的 pickRun）。
SLOTS = ("us_close", "morning", "midday", "close", "evening", "us_open")
SLOT_ORDER = {slot: index for index, slot in enumerate(SLOTS)}
DAY_SLOTS = ("morning", "midday", "close")            # 原本的三班
NIGHT_SLOTS = ("evening", "us_open", "us_close")      # 加開的三班：Google News 搜尋量壓低、盤勢解讀不跟著跑
US_SESSION_SLOTS = ("us_open", "us_close")            # 跟美股時段走：台股休市照跑（美股休市那天就是比較安靜的一班）
# 加開的三班只留這兩個 Google News 搜尋（台股、主管機關與政策）；日、韓那兩個白天三班已經搜過，晚上靠各家自己的 RSS。
NIGHT_GNEWS_FEEDS = ("gnews-tw", "gnews-regulators")
WINDOW_HOURS = 24          # 只送最近一天發布的
MAX_WINDOW_HOURS = 96      # 週一盤前、連假後：從上一輪開始，最多往回四天
PER_FEED = 30              # 每個來源先收幾則當候選（最新的）；送誰由 priority 決定，不是照時間
# 一輪送模型的名額怎麼分給各組（合計 MAX_ITEMS）；每組內照重要性取，不夠的名額讓給分數高的其他組。
QUOTA = {"tw": 26, "us": 14, "asia": 14, "global": 6}
DATE_ONLY_HOURS = 48       # 只給日期、沒有時刻的來源（金管會）：往回看兩天，不然當天晚上發的會被當成昨天的
MAX_ITEMS = 60             # 一次最多送幾則給模型（100 則加思考高超過 agy 的 300 秒，2026-11-19 實測）
GROUP_KEEP = 12            # 每一組最多存幾則（依影響大小）
STOCK_DAYS = 30            # 個股新聞往回判讀幾天（和回補的天數一樣）
OWNER_STOCK_MAX = 96       # 站主的自選一輪最多判幾則（第一次回補時多，之後只有新的）
FRIEND_STOCK_MAX = 24      # 朋友的自選一輪最多用他的金鑰判幾則
KEEP_DAYS = 35
SUMMARY_MAX = 320          # 總結最多幾個字；超過就退到最後一個句號（不留半句）
TITLE_MAX = 120
REASON_MAX = 80
# 新寫法（本人 2026-11-20「講影響＋該做的準備」，.scratch/impact-watch/spec.md）：每則與總結多三段。
EFFECT_MAX = 120           # 影響：影響到誰、多大
CONDITION_MAX = 60         # 接下來要看的條件（一個）
PREPARE_MAX = 90           # 先做好的準備（一句「如果…就…」）
POINTS_MAX = 2             # 條件、準備各最多幾個；多寫的不存（不整份作廢）
MY_STOCKS_MAX = 60         # 給模型看的站主自選＋庫存最多幾檔
# 盯項（票 03）：模型從程式給的可盯條件清單（impact_watch.candidates）用編號挑，系統之後每天替本人查。
WATCH_PICKS_MAX = 2        # 一則消息、總結各最多挑幾個
WATCH_WHY_MAX = 60         # 附的那一句說明
WATCH_MIN_IMPACT = 2       # 影響力不到這個的消息不建盯項
SUMMARY_WATCH_IMPACT = 3   # 總結挑的條件算影響力最高
RSS_SUMMARY_MAX = 200
GNEWS_PAUSE_SECONDS = wn.GNEWS_PAUSE_SECONDS
# agy 等模型的上限：一輪幾十則加思考高，300 秒不夠（2026-11-19 本機實測 60 則逾時）；其他任務照舊 300 秒。
MODEL_TIMEOUT_SECONDS = 600
BATCH_ITEMS = 12           # 市場新聞每批幾則（一批一次呼叫；20 則在 Actions 三批壞兩批，2026-11-19）
CALL_ATTEMPTS = 2          # 一次呼叫沒拿到結果就再試一次
FRIEND_BATCH = 8           # 朋友的自選每批幾則（朋友的金鑰走 API，一次太多會等到逾時）
# 這把金鑰唯一確定能用的模型：朋友存金鑰時的試呼叫（cloud/lib/friend-ai.js 的 TRIAL_URL）。主要模型回 5xx、
# 逾時或說模型不能用時退到它（2026-11-19：gemini-3.8-flash 一秒內回 503；朋友解讀也還沒有任何成功紀錄）。
FRIEND_FALLBACK_MODEL = "gemini-flash-latest"
FRIEND_ERROR_TEXT_MAX = 200
FRIEND_NEWS_EFFORT = "low"  # 朋友那份的思考程度：高會超過 300 秒（2026-11-19 GeminiTimeout），先求有結果
SUMMARY_TOP = 20           # 總結那一次給幾則（影響大的先）
PARALLEL_CALLS = 3         # 同時幾個呼叫
# 朋友的金鑰走 Gemini API：思考高時一次可能超過朋友解讀用的 120 秒（2026-11-19 正式跑 gemini_unavailable）
FRIEND_TIMEOUT_SECONDS = 120
GROUPS = ("tw", "us", "asia")
MARKETS = ("", "日", "韓", "中")
# 送給模型的結構描述（agy 轉成 Gemini function declaration）：enum 只能是非空字串（2026-11-19 乾跑 INVALID_ARGUMENT），
# 所以影響大小寫成 "1"～"3"、沒有市場寫 "none"，讀回來再換成數字與空字串。
WIRE_IMPACTS = ("1", "2", "3")
WIRE_MARKETS = ("none", "日", "韓", "中")


def _gnews(query: str, sites: Sequence[str], lang: str = "en") -> str:
    site = " OR ".join(f"site:{s}" for s in sites)
    locale = {"en": {"hl": "en-US", "gl": "US", "ceid": "US:en"}, "zh": {"hl": "zh-TW", "gl": "TW", "ceid": "TW:zh-Hant"}}[lang]
    return f"{wn.GNEWS_URL}?{urlencode({'q': f'({query}) ({site}) when:1d', **locale})}"


# 本人 11-19 選「照這份清單（推薦）」。group 是預設分組（global＝全球大事，由模型分到影響的市場）。
FEEDS: list[dict[str, str]] = [
    {"key": "cna-finance", "label": "中央通訊社", "group": "tw", "market": "", "kind": "rss",
     "url": "https://feeds.feedburner.com/rsscna/finance"},
    {"key": "wsj-markets", "label": "華爾街日報", "group": "us", "market": "", "kind": "rss",
     "url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"},
    {"key": "bloomberg-markets", "label": "Bloomberg", "group": "us", "market": "", "kind": "rss",
     "url": "https://feeds.bloomberg.com/markets/news.rss"},
    {"key": "ft-markets", "label": "金融時報", "group": "us", "market": "", "kind": "rss",
     "url": "https://www.ft.com/markets?format=rss"},
    {"key": "nikkei-zh", "label": "日經中文網", "group": "asia", "market": "日", "kind": "rss",
     "url": "https://money.udn.com/rssfeed/news/1001/5588/120769"},
    {"key": "yonhap-biz", "label": "韓聯社", "group": "asia", "market": "韓", "kind": "rss",
     "url": "https://en.yna.co.kr/RSS/biz.xml"},
    {"key": "koreaherald-biz", "label": "Korea Herald", "group": "asia", "market": "韓", "kind": "rss",
     "url": "https://www.koreaherald.com/rss/kh_Business"},
    {"key": "scmp-business", "label": "南華早報", "group": "asia", "market": "中", "kind": "rss",
     "url": "https://www.scmp.com/rss/92/feed"},
    {"key": "rthk-finance", "label": "香港電台", "group": "asia", "market": "中", "kind": "rss",
     "url": "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml"},
    {"key": "ft-asia", "label": "金融時報", "group": "asia", "market": "", "kind": "rss",
     "url": "https://www.ft.com/asia-pacific?format=rss"},
    {"key": "bloomberg-economics", "label": "Bloomberg", "group": "global", "market": "", "kind": "rss",
     "url": "https://feeds.bloomberg.com/economics/news.rss"},
    {"key": "bbc-business", "label": "BBC", "group": "global", "market": "", "kind": "rss",
     "url": "https://feeds.bbci.co.uk/news/business/rss.xml"},
    # Google News 只收白名單媒體；三次之間要停（watch_news.GNEWS_PAUSE_SECONDS）
    {"key": "gnews-tw", "label": "路透／Bloomberg", "group": "tw", "market": "", "kind": "gnews",
     "url": _gnews('"Taiwan stocks" OR "Taiwan shares" OR Taiex', ("reuters.com", "bloomberg.com"))},
    {"key": "gnews-jp", "label": "路透", "group": "asia", "market": "日", "kind": "gnews",
     "url": _gnews('"Nikkei share average" OR "Japanese stocks" OR "Tokyo stocks"', ("reuters.com",))},
    {"key": "gnews-kr", "label": "路透", "group": "asia", "market": "韓", "kind": "gnews",
     "url": _gnews('KOSPI OR "South Korean stocks" OR "Seoul stocks"', ("reuters.com",))},
    # 本人 2026-11-20「影響股票漲跌比較多的消息還是來自官方或者是政府的政策…明明就有出限空令2.0你都沒有寫出來」：
    # 主管機關與政策的報導另外搜一次（只收中央通訊社），不靠財經 RSS 最新那幾則剛好有沒有。
    {"key": "gnews-regulators", "label": "中央通訊社", "group": "tw", "market": "", "kind": "gnews",
     "url": _gnews("金管會 OR 證交所 OR 櫃買中心 OR 央行 OR 財政部 OR 經濟部 OR 國發會 OR 限空令 OR 融券 OR 關稅", ("cna.com.tw",), "zh")},
]
# 金管會新聞稿 RSS（官方；只有日期沒有時刻）。本人 2026-11-20：題目「要新增『金管會新聞稿』這個官方來源嗎？」答「要加（推薦）」。
# 和其他來源一樣：一輪抓一次、設逾時、失敗不重試，抓不到那一輪就不用它。
FSC_FEED = {"key": "fsc-news", "label": "金管會", "group": "tw", "market": "", "kind": "rss", "date_only": "1",
            "url": "https://www.fsc.gov.tw/RSS/Messages?serno=201202290009&language=chinese"}
FEEDS.insert(0, FSC_FEED)
# 來源的份量（選材用）：官方 ＞ 主管機關報導 ＞ 中央通訊社 ＞ 其他。
FEED_WEIGHT = {"fsc-news": 6, "gnews-regulators": 4, "cna-finance": 3, "gnews-tw": 2}
# 標題或摘要出現這幾類字眼就加分（每類只加一次）。本人：政策、財報、合作關係最會動股價；一般企業動態與活動稿往後排。
PRIORITY_WORDS = (
    ("policy", 6, r"金管會|證交所|櫃買|央行|財政部|經濟部|國發會|行政院|限空|放空|融券|借券|當沖|處置股|關稅|出口管制|制裁|升息|降息|利率決議"
                  r"|Fed\b|FOMC|Federal Reserve|tariff|export control|sanction|rate (?:cut|hike)|central bank|regulator|short[- ]sell"),
    ("earnings", 4, r"財報|營收|獲利|EPS|財測|法說|毛利率|earnings|revenue|profit|guidance|outlook|quarterly results"),
    ("deal", 3, r"訂單|合作|併購|收購|入股|擴產|投資案|IPO|掛牌|acquisi|merger|takeover|\bdeal\b|partnership|stake"),
    ("flows", 3, r"外資|投信|三大法人|買超|賣超|foreign investors|outflow|inflow"),
    ("market", 2, r"台股|加權指數|美股|道瓊|那斯達克|費半|標普|日經|KOSPI|恒生|上證|stocks|shares|Nasdaq|S&P 500|Wall Street"),
)
_PRIORITY = tuple((name, weight, re.compile(pattern, re.IGNORECASE)) for name, weight, pattern in PRIORITY_WORDS)
MARKET_KINDS = ("policy", "earnings", "deal", "flows", "macro", "other")   # 顯示的先後也是這個順序
STOCK_KINDS = ("earnings", "guidance", "deal", "policy", "rating", "incident", "other")

# 各市場指數（網站資料庫 overseas_quotes 的最後兩根日線；加權由富果另外給）。
INDICES = (("SOX.INDX", "費城半導體"), ("IXIC.INDX", "那斯達克"), ("DJI.INDX", "道瓊"), ("N225.INDX", "日經"), ("KS11.INDX", "韓綜"))

_TAGS = re.compile(r"<[^>]+>")
_CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")
_HAN = re.compile(r"[㐀-鿿]")
_WIRE_TAGS = re.compile(r"^(?:\((?:LEAD|URGENT|\d+(?:st|nd|rd|th) LD|LD|News Focus|Yonhap Interview)[^)]*\)\s*)+", re.IGNORECASE)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain(text: str | None, limit: int) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text or ""))).strip()[:limit]


def parse_rss(content: bytes) -> list[dict[str, str]]:
    """一般 RSS 2.0：[{title, summary, link, published_at}]。沒有標題、連結或時間的那一則略過；整份不是 RSS 就丟 FormatChanged。"""
    channel = wn._rss_channel(content)
    rows = []
    for node in channel.findall("item"):
        title, link = _plain(node.findtext("title"), TITLE_MAX * 2), html.unescape((node.findtext("link") or "").strip())
        title = _WIRE_TAGS.sub("", title).strip()   # 韓聯社的 (LEAD)／(URGENT)／(2nd LD)：同一則的更新稿
        try:
            published = wn._utc((node.findtext("pubDate") or "").strip())
        except wn.FormatChanged:
            continue
        if title and link.startswith("https://"):
            rows.append({"title": title, "summary": _plain(node.findtext("description"), RSS_SUMMARY_MAX),
                         "link": link, "published_at": published})
    return rows


def window_start(query: Callable[[str], list[dict]], now: str) -> str:
    """送多久以前的新聞：至少 WINDOW_HOURS 小時；上一輪更早（週一盤前、連假後）就從上一輪開始，最多 MAX_WINDOW_HOURS。"""
    moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    start, floor = moment - timedelta(hours=WINDOW_HOURS), moment - timedelta(hours=MAX_WINDOW_HOURS)
    try:
        rows = query(f"SELECT max(created_at) AS last FROM market_news_runs WHERE created_at < {_q(now)}")
        last = rows[0]["last"] if rows else None
        if last:
            previous = datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            start = max(min(start, previous), floor)
    except Exception:  # noqa: BLE001 — 表還沒建：照 24 小時
        pass
    return _iso(start)


def collect(*, get: Callable, now: str, sleep: Callable[[float], None],
            since: str | None = None, gnews_feeds: Sequence[str] | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """每個來源抓一次：失敗只缺那一家（sources 記錯誤種類，回 200 但 0 則記 empty）。Google News 回 429／503 時，
    這一輪其他 Google 查詢不再打（記 slowed）。回傳最近 WINDOW_HOURS 小時發布的，每家最多 PER_FEED 則，同連結或同標題只留一則。
    gnews_feeds：這一班只打哪幾個 Google News 搜尋（加開的三班壓低搜尋量）；其餘的不打、記 skipped。沒給就是全部。"""
    moment = datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    oldest = since or _iso(moment - timedelta(hours=WINDOW_HOURS))
    date_only_oldest = min(oldest, _iso(moment - timedelta(hours=DATE_ONLY_HOURS)))
    items: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    seen: set[str] = set()
    slowed, google_calls = False, 0
    for feed in FEEDS:
        if feed["kind"] == "gnews":
            if gnews_feeds is not None and feed["key"] not in gnews_feeds:
                sources[feed["key"]] = "skipped"
                continue
            if slowed:
                sources[feed["key"]] = "slowed"
                continue
            if google_calls:
                sleep(GNEWS_PAUSE_SECONDS)
            google_calls += 1
        try:
            response = get(feed["url"], None)
            if response.status != 200:
                slowed = slowed or (feed["kind"] == "gnews" and response.status in wn.SLOW_DOWN)
                sources[feed["key"]] = f"HTTP {response.status}"
                continue
            rows = wn.parse_gnews(response.content) if feed["kind"] == "gnews" else parse_rss(response.content)
        except wn.FormatChanged as error:
            sources[feed["key"]] = f"format: {str(error)[:80]}"
            continue
        except Exception as error:  # noqa: BLE001 — 一家壞掉只讓那一家缺
            sources[feed["key"]] = type(error).__name__
            continue
        if not rows:
            sources[feed["key"]] = "empty"
            continue
        sources[feed["key"]] = "ok"
        floor = date_only_oldest if feed.get("date_only") else oldest
        fresh = sorted((row for row in rows if floor <= row["published_at"] <= now), key=lambda row: row["published_at"], reverse=True)
        for row in fresh[:PER_FEED]:
            mark = row["title"].lower()
            if row["link"] in seen or mark in seen:
                continue
            seen.update({row["link"], mark})
            label = feed["label"]
            if feed["kind"] == "gnews":
                label = next((name for key, name in wn.GNEWS_OUTLETS.values() if key == row.get("source")), label)
            items.append({"source_key": feed["key"], "source": label, "group_hint": feed["group"],
                          "market_hint": feed["market"], "title": row["title"], "summary": row.get("summary") or "",
                          "link": row["link"], "published_at": row["published_at"]})
    items = select_by_importance(items, MAX_ITEMS)
    for index, row in enumerate(items, 1):
        row["id"] = f"n{index}"
    return items, sources


def priority(row: Mapping[str, Any]) -> int:
    """選材的分數：來源的份量＋標題與摘要裡出現的類別字眼（政策 6、財報 4、合作訂單 3、法人 3、盤勢 2；每類一次）。"""
    text = f"{row.get('title', '')} {row.get('summary', '')}"
    return FEED_WEIGHT.get(row.get("source_key"), 1) + sum(weight for _name, weight, pattern in _PRIORITY if pattern.search(text))


def select_by_importance(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """照重要性取 limit 則：每組先照分數取自己的名額（QUOTA），剩下的名額給其餘裡分數最高的；同分時新的先。
    回傳照「台股、美股、日韓中、全球」排，組內分數高的在前——同一組的會進同一批，模型才看得出哪幾則是同一件事。"""
    ordered = sorted(sorted(items, key=lambda row: row["published_at"], reverse=True), key=priority, reverse=True)
    picked: list[dict[str, Any]] = []
    total = sum(QUOTA.values())
    for group, quota in QUOTA.items():
        picked += [row for row in ordered if row["group_hint"] == group][:max(1, round(quota * limit / total))]
    chosen = {id(row) for row in picked}
    picked += [row for row in ordered if id(row) not in chosen][:max(0, limit - len(picked))]
    picked = picked[:limit]
    order = {group: index for index, group in enumerate(QUOTA)}
    return sorted(picked, key=lambda row: (order.get(row["group_hint"], 9), -priority(row)))


def missed_groups(sources: Mapping[str, str]) -> list[str]:
    """哪幾組的來源這一輪全部沒抓到（全球大事那兩家不算任何一組）。網站對這幾組標「這一輪沒抓到」並列上一輪的。"""
    out = []
    for group in GROUPS:
        keys = [feed["key"] for feed in FEEDS if feed["group"] == group]
        if keys and all(sources.get(key) != "ok" for key in keys):
            out.append(group)
    return out


def indices(query: Callable[[str], list[dict]], taiex: Callable[[], dict | None]) -> list[dict[str, Any]]:
    """加權（富果，可能是盤中）＋國外指數最後一根日線對前一根的漲跌（%，兩位小數）。拿不到的不列。"""
    out = []
    try:
        first = taiex()
        if first:
            out.append(first)
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = query("SELECT symbol, date, close FROM overseas_quotes WHERE symbol IN ("
                     + ",".join(f"'{symbol}'" for symbol, _ in INDICES) + ") ORDER BY symbol, date")
    except Exception:  # noqa: BLE001 — 表還沒建（國外指數沒上線）就不列
        rows = []
    for symbol, name in INDICES:
        series = [row for row in rows if row["symbol"] == symbol and row.get("close")]
        if len(series) >= 2 and series[-2]["close"]:
            change = round((float(series[-1]["close"]) / float(series[-2]["close"]) - 1) * 100, 2)
            out.append({"name": name, "change_pct": change, "date": series[-1]["date"]})
    return out


# ---- 模型 ----

_POINTS = {"type": "array", "items": {"type": "string"}}
_PICKS = {"type": "array", "items": {"type": "object", "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
                                     "required": ["id"]}}
# 站主自己的股票要盯的條件（票 06）：市場消息與總結用 code＋id 挑；個股新聞那一則本來就是某一檔，只寫 id（_PICKS）
_STOCK_PICKS = {"type": "array", "items": {"type": "object", "properties": {
    "code": {"type": "string"}, "id": {"type": "string"}, "why": {"type": "string"}}, "required": ["code", "id"]}}
_ITEM = {"type": "object", "properties": {
    "id": {"type": "string"}, "group": {"type": "string", "enum": [*GROUPS, "skip"]},
    "market": {"type": "string", "enum": list(WIRE_MARKETS)}, "tone": {"type": "string", "enum": ["up", "down", "flat"]},
    "kind": {"type": "string", "enum": list(MARKET_KINDS)},
    "impact": {"type": "string", "enum": list(WIRE_IMPACTS)}, "title": {"type": "string"}, "reason": {"type": "string"},
    "effect": {"type": "string"}, "owner_effect": {"type": "string"}, "conditions": _POINTS, "prepare": _POINTS,
    "track": _PICKS, "my_track": _STOCK_PICKS, "wait": impact_watch_news.WAIT_SCHEMA},
    "required": ["id", "group", "market", "kind", "tone", "impact", "title", "reason", "effect"]}
_WATCH = {"type": "object", "properties": {
    "id": {"type": "string"}, "relevant": {"type": "string", "enum": ["yes", "no"]},
    "kind": {"type": "string", "enum": list(STOCK_KINDS)}, "tone": {"type": "string", "enum": ["up", "down", "flat"]},
    "impact": {"type": "string", "enum": list(WIRE_IMPACTS)}, "title": {"type": "string"}, "reason": {"type": "string"},
    "effect": {"type": "string"}, "conditions": _POINTS, "prepare": _POINTS, "track": _PICKS},
    "required": ["id", "relevant", "kind", "tone", "impact", "title", "reason"]}
ITEMS_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": _ITEM}}, "required": ["items"]}
# 欄位不叫 summary：agy 的結構化輸出裡，模型會把 summary 當成「交代自己做了什麼」（2026-11-19 實測兩次）。
SUMMARY_SCHEMA = {"type": "object", "properties": {
    "market_commentary": {"type": "string", "description": "給投資人看的盤勢解說本文，1～2 句，例如「台股今天下跌，主要是外資賣超……」"},
    "effect": {"type": "string", "description": "這樣走影響到誰、多大"},
    "owner_effect": {"type": "string", "description": "點名 my_stocks 裡受影響的股票；沒有就不寫"},
    "conditions": {**_POINTS, "description": "接下來要看的條件，0～2 個"},
    "prepare": {**_POINTS, "description": "先做好的準備，0～2 句「如果…就…」"},
    "track": {**_PICKS, "description": "從 INPUT 的 watchable 用 id 挑要系統替讀者盯的條件，0～2 個"},
    "my_track": {**_STOCK_PICKS, "description": "從 INPUT 的 my_watchable 替 owner_effect 點名的股票挑條件；沒有就不寫"},
    "wait": {**impact_watch_news.WAIT_SCHEMA, "description": "在等什麼事被報導出來（一句＋關鍵詞）；沒有就不寫"}},
    "required": ["market_commentary", "effect"]}
WATCH_SCHEMA = {"type": "object", "properties": {"watch": {"type": "array", "items": _WATCH}}, "required": ["watch"]}

SLOT_TEXT = {"morning": "台股開盤前（台北 08:00）", "midday": "台股盤中（台北 11:30）", "close": "台股收盤後（台北 14:00）",
             "evening": "台股盤後（台北 18:30，今天已收盤）", "us_open": "美股開盤後（台北 23:00，台股已收盤）",
             "us_close": "美股收盤後（台北 06:30，台股尚未開盤）"}
assert set(SLOT_TEXT) == set(SLOTS)

# 2026-11-19 Actions（agy 1.5.3）：60 則一次送，回 SUCCESS 卻沒有結構化輸出。改成分批（每批 BATCH_ITEMS 則）、
# 自選一次、總結另外一次短呼叫；任何一批失敗只影響那一批。
# 台灣市場的說法（本人 11-20：明明有限空令卻寫成「收緊放空限制」，認不出來）。三種提示共用。
GLOSSARY = """用台灣股市投資人習慣的說法寫，不要直譯：限空令（限制放空、平盤下不得放空）、融資、融券、借券賣出、當沖、
處置股、注意股、法說會、財測、營收、毛利率、EPS、三大法人、外資、投信、自營商、買超、賣超、漲停、跌停、
加權指數、櫃買指數、權值股、除權息、庫藏股、現金增資、違約交割、金管會、證交所、櫃買中心、央行、聯準會（Fed）、
降息、升息、關稅、出口管制。公司與機構用台灣的譯名（輝達、台積電、聯發科、超微、博通、美光、三星、SK 海力士）。"""

# 新寫法的三段（本人 2026-11-20：「不要只說明事件，要講影響＋該做的準備」）。三種提示共用同一條界線：不寫買賣指令。
NO_ORDERS = """effect、conditions、prepare 都不能寫買賣指令或個人化投資建議：不要出現「建議買進」「可以加碼」「應該減碼」
「逢低布局」「停損出場」這類叫人下單的話；prepare 只寫情境與要先確認、檢查、想好的事。
這幾段裡的數字只能來自 INPUT，不要編。
"""
_POINT_LISTS = """- conditions：接下來要看的條件，0～2 個，每個一句（30 字內），寫「看什麼有沒有發生」
  （例如「外資是否連續賣超」「金管會是否公布正式名單」）；沒有值得看的就給空陣列。
- prepare：先做好的準備，0～2 句（每句 40 字內），每句用「如果…就…」寫
  （例如「如果外資連續賣超，就先檢查手上權值股佔多少」）；沒有就給空陣列。
"""
_OWNER_POINT = """- owner_effect：INPUT 的 my_stocks 是讀者自己的自選股與庫存。會直接影響其中哪幾檔時，寫一句點名
  （用 my_stocks 裡的名稱）並說怎麼影響；沒有直接關係就不要寫這個欄位，不要硬湊。
  其他欄位是所有人都看得到的：不要寫「你的」「你手上」，也不要因為某一檔在 my_stocks 裡才提它。
"""
_WATCH_PICKS = """- track：INPUT 的 watchable 是系統可以每天替讀者盯的大盤條件（id、name；now 是現況）。這件事接下來怎麼發展，
  和其中哪一個最有關就挑出來，0～2 個：id 照抄、why 用一句話（30 字內）說為什麼要盯它。
  只能挑 watchable 裡有的 id；沒有相關的就給空陣列，不要硬挑。INPUT 沒有 watchable 就不要寫這個欄位。
"""
ITEM_POINTS = """- effect：一句（60 字內）講影響——影響到誰（哪個市場、哪個族群或哪幾檔權值股）、多大，為什麼是利多或利空。
""" + _OWNER_POINT + _POINT_LISTS + _WATCH_PICKS + impact_watch_news.WAIT_RULE + """- impact 是 "1" 的消息可以只寫 effect，conditions、prepare 與 track 給空陣列。
""" + NO_ORDERS
WATCH_POINTS = """- effect：一句（60 字內）講這則對這檔股票的影響——影響營運或股價的哪一塊、多大；relevant 是 no 的不用寫。
""" + _POINT_LISTS + """- impact 是 "1" 或 relevant 是 no 的可以只寫 effect，conditions 與 prepare 給空陣列。
""" + NO_ORDERS
SUMMARY_POINTS = """effect：1～2 句（80 字內），這樣走影響到誰、多大（哪些族群、哪幾檔權值股受影響最多）。
""" + _OWNER_POINT + _POINT_LISTS + _WATCH_PICKS + impact_watch_news.WAIT_RULE + NO_ORDERS

ITEM_RULES = """你是台股投資人的新聞助理。讀 INPUT 的新聞標題與摘要，用繁體中文回答，只回 JSON。
items：每一則都要回（id 照抄）：
- group：這則主要影響哪個市場——tw 台股、us 美股、asia 日韓中；group_hint 是來源的預設，global 的要你分。
  明顯和經濟、產業、公司、股市都無關（生活、社會、體育、八卦、純政治儀式、活動與週年宣傳稿）的填 skip；
  有關但看不出影響方向的照樣分組，tone 填 flat、impact 填 "1"。
  同一件事有好幾則時只留一則，其他填 skip：優先留官方（金管會）或台灣媒體（中央通訊社）的那一則。
- market：asia 那組填「日」「韓」「中」其中一個（香港算中）；其他組填 none。
- kind：policy 政府與主管機關的政策法規（金管會、證交所、央行、財政部、關稅、出口管制、利率決議）；
  earnings 財報、營收、財測、法說；deal 重大訂單、合作、併購、擴產；flows 法人與資金動向（外資、投信買賣超）；
  macro 總體經濟數據與景氣；other 其他企業動態。
- tone：對該市場是利多 up、利空 down，或中性 flat（已在預期內、影響不明）。
- impact：影響大小 "3" 大、"2" 中、"1" 小。政策、財報財測、重大合作訂單、法人大幅買賣超，而且會左右整個市場或權值股的
  才是 "3"；一般企業動態、例行統計、活動稿最多 "1"。
- title：中文標題；原文是中文就照抄（可去掉多餘的來源字樣），外文就翻成自然的繁體中文，不加原文沒有的事實。
- reason：一句話（30 字內）講發生了什麼事；只能根據標題與摘要，不要編數字或內容。
""" + ITEM_POINTS + GLOSSARY + """
不要使用任何工具、不要上網查，只根據 INPUT 直接回答。"""

SUMMARY_RULES = """你是財經記者，替台股投資人寫一段盤勢解說，繁體中文。
market_commentary：1～2 句、合計 100 字以內、最後以句號結尾，回答「今天為什麼這樣走」——以台股為主、帶到美股與日韓中，
根據 INPUT 的指數漲跌與新聞（kind policy 政策、earnings 財報、deal 合作訂單、flows 法人；tone up 利多、down 利空；impact 越大越重要）。
一定要點名當天最關鍵的一兩件事，用新聞裡的名稱講（例如「金管會祭出限空令」「美國擴大晶片出口管制」「外資賣超逾 600 億」），
不能只講指數漲跌幾趴。可以引用 indices 的漲跌幅，其他數字只能來自新聞標題或理由。
slot 是開盤前時，講的是昨夜國際市場與今天可能的影響，不要假裝已經開盤。
slot_text 寫台股已收盤或尚未開盤時（盤後、美股開盤後、美股收盤後），講收盤後才出來的消息與美股、國際市場的變化，
以及對下一個台股交易日可能的影響；不要把它寫成台股正在交易，也不要重講白天已經講過的盤勢。
這個欄位的內容就是那段解說本身，第一個字就開始講市場（例如「台股今天下跌，主要是……」）；
不要描述你完成了什麼、不要提到指令、JSON、摘要或規則。
""" + SUMMARY_POINTS + GLOSSARY + """
不要使用任何工具、不要上網查。"""

_NOT_SUMMARY = re.compile(r"JSON|json|指令|已成功|已完成|已依照|以下是|規則|摘要如下|產生.{0,6}摘要")


def is_summary(text: str | None) -> bool:
    """是不是在講市場：模型有時回「已成功依照指令產生摘要…」這種交代工作的話（2026-11-19 實測），那不算。"""
    return bool(text) and not _NOT_SUMMARY.search(text)

WATCH_RULES = """你是台股投資人的新聞助理。讀 INPUT 的個股新聞與重大訊息，用繁體中文回答，只回 JSON。
watch：每一則都要回（id 照抄）：
- relevant：這則和 name 那家公司的營運、股價、產業有沒有關係——有填 yes；只是剛好出現公司名稱或字眼
  （活動贊助、地名、路跑、節慶、同名的普通詞），或只是大盤新聞裡順帶提到一次，填 no。
- kind：earnings 財報與月營收、獲利；guidance 法說會、財測、展望；deal 訂單、合作、客戶、併購、擴產；
  policy 政策法規對這家公司的影響；rating 法人與外資的評等、目標價、買賣超；
  incident 異常事件（違約交割、注意股、處置股、停工、事故、訴訟、重大人事）；other 其他。
- tone 對那一檔股票是利多 up、利空 down 或中性 flat；impact "3" 大、"2" 中、"1" 小；
- title 中文標題（外文翻成自然的繁體中文，不加原文沒有的事實）；reason 一句話（30 字內）講發生了什麼事，只能根據標題與摘要，不要編數字。
""" + WATCH_POINTS + GLOSSARY + """
不要使用任何工具、不要上網查，只根據 INPUT 直接回答。"""


MY_TRACK_RULES = """
另外，INPUT 的 my_watchable 是系統可以每天替讀者自己的股票盯的條件（code、name；now 是這一檔的現況；conditions 是可以挑的 id 與 name）。
- my_track：只有 owner_effect 點名了其中某一檔，而且這件事接下來對它的影響和某個條件最有關時才挑，0～2 個：
  code、id 照抄，why 用一句話（30 字內）說為什麼要盯它。只能挑那一檔 conditions 裡有的 id；沒有相關的就不要寫這個欄位，不要硬挑。
  why 不能寫買賣指令。"""
STOCK_TRACK_RULES = """
另外，INPUT 的 stock_watchable 是系統可以每天替這幾檔股票盯的條件（code；now 是那一檔的現況；conditions 是可以挑的 id 與 name）。
- track：relevant 是 yes、impact 不是 "1"，而且這則新聞接下來怎麼發展和那一檔（這一則的 code）的某個條件最有關時才挑，0～2 個：
  id 照抄、why 用一句話（30 字內）說為什麼要盯它。只能挑那一檔 conditions 裡有的 id；沒有相關的就給空陣列，不要硬挑。
  why 不能寫買賣指令。其他欄位照原本的規定寫，不要因為有這份清單而改變。"""


def _stock_watchable(offers: Mapping[str, Mapping[str, Any]] | None, codes: Sequence[str] | None = None) -> list[dict[str, Any]]:
    """給模型看的站主個股清單：每一檔的現況與可以挑的條件（只有 id 與名稱；參數留在程式這邊）。codes 給了就只帶那幾檔。
    最多 MY_STOCKS_MAX 檔（和 my_stocks 同一個上限）。"""
    return [{"code": code, "name": offer["name"], "now": offer["now"],
             "conditions": [{"id": row["id"], "name": row["name"]} for row in offer["conditions"]]}
            for code, offer in (offers or {}).items() if codes is None or code in codes][:MY_STOCKS_MAX]


def _my_stocks(names: Mapping[str, str] | None) -> list[dict[str, str]]:
    return [{"code": code, "name": name} for code, name in list((names or {}).items())[:MY_STOCKS_MAX]]


def _watchable(watchable: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    """給模型看的可盯條件清單：只有編號、名稱與現況（參數留在程式這邊）；沒有清單就不帶。"""
    return {"watchable": [{key: row[key] for key in ("id", "name", "now")} for row in watchable]} if watchable else {}


def items_prompt(items: Sequence[Mapping[str, Any]], my_stocks: Mapping[str, str] | None = None,
                 watchable: Sequence[Mapping[str, Any]] | None = None,
                 stock_offers: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """my_stocks：站主的自選＋庫存 {代號: 名稱}，給模型點名用（owner_effect）；沒有就不帶。
    watchable：可盯條件清單（impact_watch.offered），模型用編號挑；沒有就不帶。
    stock_offers：站主每一檔的可盯條件（impact_watch.stock_offered）；沒有就不帶，提示也不提。"""
    mine = _stock_watchable(stock_offers)
    payload = {"items": [{key: row[key] for key in ("id", "group_hint", "source", "title", "summary", "published_at")}
                         for row in items], **({"my_stocks": _my_stocks(my_stocks)} if my_stocks else {}), **_watchable(watchable),
               **({"my_watchable": mine} if mine else {})}
    return ITEM_RULES + (MY_TRACK_RULES if mine else "") + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def summary_prompt(slot: str, trade_date: str, rows: Sequence[Mapping[str, Any]], index_rows: Sequence[Mapping[str, Any]],
                   my_stocks: Mapping[str, str] | None = None,
                   watchable: Sequence[Mapping[str, Any]] | None = None,
                   stock_offers: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """rows：已判讀的（group、tone、impact、title、reason、effect）；沒有判讀成的時候給原標題。"""
    mine = _stock_watchable(stock_offers)
    picked = sorted(rows, key=lambda row: (-int(row.get("impact") or 1), _kind_rank(row.get("kind"))))[:SUMMARY_TOP]
    payload = {"slot": slot, "slot_text": SLOT_TEXT[slot], "trade_date": trade_date, "indices": list(index_rows),
               "news": [{key: row.get(key) for key in ("group", "kind", "tone", "impact", "title", "reason", "effect")
                         if row.get(key) not in (None, "")} for row in picked],
               **({"my_stocks": _my_stocks(my_stocks)} if my_stocks else {}), **_watchable(watchable),
               **({"my_watchable": mine} if mine else {})}
    return SUMMARY_RULES + (MY_TRACK_RULES if mine else "") + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def watch_prompt(watch: Sequence[Mapping[str, Any]], stock_offers: Mapping[str, Mapping[str, Any]] | None = None) -> str:
    """stock_offers：只在站主自己的那幾次呼叫裡給（朋友的金鑰那條路不給）；只帶這一批新聞講到的那幾檔。"""
    mine = _stock_watchable(stock_offers, [row["code"] for row in watch])
    payload = {"watch": [{key: row[key] for key in ("id", "code", "name", "source", "title", "summary", "published_at")
                          if row.get(key) not in (None, "")} for row in watch], **({"stock_watchable": mine} if mine else {})}
    return WATCH_RULES + (STOCK_TRACK_RULES if mine else "") + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] if value else None


# 買賣指令的界線（本人：「準備」不要寫成「現在買進／賣出」的指令）在 impact_text.py，和首頁白話分析共用。
# 站主的自選與庫存只有站主自己能看：大家都看得到的欄位裡不能對著讀者講「你的股票」（那一句只能在 owner_effect）。
_READER = re.compile(r"你|您|自選|讀者|my_stocks")
POINT_FIELDS = ("effect", "owner_effect", "conditions", "prepare")
POINT_LABELS = {"market_commentary": "總結", "reason": "發生什麼", "effect": "影響", "owner_effect": "對讀者股票的影響",
                "conditions": "要看的條件", "prepare": "準備", "track": "要盯的條件的說明", "wait": "在等的消息"}
Problem = tuple[str, str, str]   # (欄位, 退回的原因, 被退回的那一句)


def text_problem(field: str, text: str) -> str | None:
    """模型寫的一句話能不能上站：不能有買賣指令；prepare 還要是「如果…就…」的情境；
    owner_effect 以外的欄位（大家都看得到）不能對著讀者講他自己的股票。"""
    reason = impact_text.order_problem(text, prepare=field == "prepare")
    if reason == impact_text.NO_ORDERS:
        return reason
    if field != "owner_effect" and _READER.search(text):
        return "這一段所有人都看得到，不能提到讀者自己的股票（只能寫在 owner_effect）"
    if reason:
        return reason
    return None


def retry_note(problem: Problem) -> str:
    """被文字檢查退回後重問時接在提示後面（和 ledger_core.retry_note 同一個做法：不告訴模型錯在哪，它多半再錯一次）。"""
    field, reason, text = problem
    return ("\n\n【上一次的回答被退回，請整份重寫】"
            f"原因：{reason}。被退回的那一句（{POINT_LABELS.get(field, field)}）：「{text}」。"
            "重寫時不要出現買進、賣出、加碼、減碼、停損這類叫人下單的話或投資建議；prepare 每一句用「如果…就…」"
            "寫要先確認、檢查或想好的事。欄位與格式照原本的規定，不要多也不要少。")


def _mentions_stock(text: str, my_stocks: Mapping[str, str]) -> bool:
    return any(code in text or re.sub(r"-KY$", "", name) in text for code, name in my_stocks.items() if name)


def points(entry: Mapping[str, Any], problems: list[Problem] | None = None,
           my_stocks: Mapping[str, str] | None = None) -> dict[str, Any]:
    """新寫法的三段：影響、要看的條件、準備（＋點名站主股票的那一句）。少寫的就沒有、多寫的只取前 POINTS_MAX 個、
    被文字檢查退回的只丟那一句並記進 problems——哪一種都不讓整則作廢。回傳只有合格的欄位。"""
    out: dict[str, Any] = {}

    def passed(field: str, value: Any, limit: int) -> str | None:
        text = whole_sentences(_text(value, 2000), limit)   # 太長就退到最後一個句號，不留半句
        reason = text_problem(field, text) if text else None
        if reason and problems is not None:
            problems.append((field, reason, text))
        return None if reason else text

    effect = passed("effect", entry.get("effect"), EFFECT_MAX)
    if effect:
        out["effect"] = effect
    if my_stocks:
        named = _text(entry.get("owner_effect"), EFFECT_MAX)
        # 沒點到任何一檔的不存：這一段只給站主看，要真的在講他的股票
        if named and _mentions_stock(named, my_stocks) and passed("owner_effect", named, EFFECT_MAX):
            out["owner_effect"] = named
    for field, limit in (("conditions", CONDITION_MAX), ("prepare", PREPARE_MAX)):
        value = entry.get(field)
        value = [value] if isinstance(value, str) else value if isinstance(value, (list, tuple)) else []
        kept = [text for text in (passed(field, item, limit) for item in value) if text][:POINTS_MAX]
        if kept:
            out[field] = kept
    return out


def watch_picks(entry: Mapping[str, Any], watchable: Sequence[Mapping[str, Any]] | None,
                problems: list[Problem] | None = None, unknown: list[str] | None = None) -> list[dict[str, Any]]:
    """模型挑的要盯的條件 → [{condition_key, label, params, note}]。編號要是這一班清單裡有的：沒有的那一個丟掉
    （記進 unknown）、其餘照收；同一個編號只收一次；最多 WATCH_PICKS_MAX 個。附的那一句被文字檢查退回就只丟那一句。"""
    by_id = {row["id"]: row for row in watchable or []}
    value = entry.get("track")
    out: list[dict[str, Any]] = []
    for pick in value if isinstance(value, (list, tuple)) else []:
        key = pick.get("id") if isinstance(pick, Mapping) else pick
        if not isinstance(key, str) or key not in by_id:
            if unknown is not None and by_id:
                unknown.append(str(key)[:40])
            continue
        if any(row["condition_key"] == key for row in out):
            continue
        note = whole_sentences(_text(pick.get("why"), 2000), WATCH_WHY_MAX) if isinstance(pick, Mapping) else None
        reason = text_problem("conditions", note) if note else None
        if reason and problems is not None:
            problems.append(("track", reason, note))
        out.append({"condition_key": key, "label": by_id[key]["name"], "params": by_id[key]["params"],
                    "note": None if reason else note})
    return out[:WATCH_PICKS_MAX]


def stock_picks(value: Any, offers: Mapping[str, Mapping[str, Any]] | None, *, code: str | None = None, named: str | None = None,
                problems: list[Problem] | None = None, unknown: list[str] | None = None) -> list[dict[str, Any]]:
    """模型替站主的股票挑的條件 → [{subject, condition_key, label, params, note}]（票 06）。
    code 給了：這一則本來就是那一檔的新聞（個股判讀），pick 只帶 id。沒給：pick 自己帶 code（市場消息與總結），
    而且那一檔要真的在 named（存得下來的 owner_effect 那一句）裡被點到，沒點到的不收。
    編號要是那一檔清單裡有的，沒有的那一個丟掉（記進 unknown）；同一檔同一個編號只收一次；最多 WATCH_PICKS_MAX 個。
    這些只會寫成只給站主看的盯項，不進任何大家共用的欄位。"""
    out: list[dict[str, Any]] = []
    for pick in value if offers and isinstance(value, (list, tuple)) else []:
        subject = code or (pick.get("code") if isinstance(pick, Mapping) else None)
        key = pick.get("id") if isinstance(pick, Mapping) else None
        offer = offers.get(subject) if isinstance(subject, str) else None
        row = next((row for row in offer["conditions"] if row["id"] == key), None) if offer else None
        if not row:
            if unknown is not None:
                unknown.append(f"{subject}:{key}"[:40])
            continue
        if code is None and not _mentions_stock(named or "", {subject: offer["name"]}):
            continue
        if any(done["subject"] == subject and done["condition_key"] == key for done in out):
            continue
        note = whole_sentences(_text(pick.get("why"), 2000), WATCH_WHY_MAX)
        reason = text_problem("owner_effect", note) if note else None   # 只給站主看的一句：只查買賣指令
        if reason and problems is not None:
            problems.append(("track", reason, note))
        out.append({"subject": subject, "condition_key": key, "label": row["name"], "params": row["params"],
                    "note": None if reason else note})
    return out[:WATCH_PICKS_MAX]


def news_wait(entry: Mapping[str, Any], problems: list[Problem] | None = None) -> dict[str, Any]:
    """模型寫的「在等什麼被報導」（消息型盯項，票 05）→ {"wait": pick}；沒寫或不合格就是空的。"""
    waiting = impact_watch_news.news_pick(entry, problems, lambda text: text_problem("conditions", text))
    return {"wait": waiting} if waiting else {}


def _mark(entry: Any) -> dict[str, Any] | None:
    """一則的共同欄位檢查：tone、impact、title、reason；不合格回 None。"""
    if not isinstance(entry, Mapping):
        return None
    tone, impact = entry.get("tone"), entry.get("impact")
    title, reason = _text(entry.get("title"), TITLE_MAX), _text(entry.get("reason"), REASON_MAX)
    impact = int(impact) if impact in WIRE_IMPACTS else impact   # 結構描述送的是 "1"～"3"
    if tone not in ("up", "down", "flat") or impact not in (1, 2, 3) or isinstance(impact, bool) or not title or not reason:
        return None
    return {"tone": tone, "impact": impact, "title": title, "reason": reason}


def _translated(original: str) -> int:
    return 0 if _HAN.search(original or "") else 1


def coerce_output(output: Any) -> Any:
    """agy 版本不同時 structured_output 可能是 JSON 字串（2026-11-19 Actions 的 1.5.3 回了東西卻沒套上）：字串就解開。"""
    if isinstance(output, str):
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            return output
    return output


def validate_items(output: Any, items: Sequence[Mapping[str, Any]],
                   rejected: dict[str, int] | None = None, *, my_stocks: Mapping[str, str] | None = None,
                   problems: list[Problem] | None = None, watchable: Sequence[Mapping[str, Any]] | None = None,
                   unknown: list[str] | None = None,
                   stock_offers: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """模型回的 items → 要存的列：id 要是送出去的、欄位要合格、skip 的不存；每組依影響大小留 GROUP_KEEP 則。
    rejected 給了就記每一種被擋的原因各幾則（執行紀錄用）；problems 給了就記被文字檢查退回的句子（重問用）。"""
    by_id = {row["id"]: row for row in items}
    out, used = [], set()
    drop = (lambda reason: rejected.__setitem__(reason, rejected.get(reason, 0) + 1)) if rejected is not None else (lambda _r: None)
    for entry in (output or {}).get("items") or [] if isinstance(output, Mapping) else []:
        mark = _mark(entry)
        source = by_id.get(entry.get("id")) if isinstance(entry, Mapping) else None
        if not isinstance(entry, Mapping):
            drop("not_object")
            continue
        if entry.get("group") == "skip":
            drop("skip")
            continue
        if not source:
            drop("unknown_id")
            continue
        if entry["id"] in used:
            drop("duplicate_id")
            continue
        if entry.get("group") not in GROUPS:
            drop("bad_group")
            continue
        if not mark:
            missing = ",".join(sorted(key for key in ("tone", "impact", "title", "reason")
                                      if key not in entry or entry.get(key) in (None, "")))
            drop(f"bad_fields:{missing}" if missing else "bad_values")
            continue
        advice = text_problem("reason", mark["reason"])
        if advice:
            if problems is not None:
                problems.append(("reason", advice, mark["reason"]))
            drop("trade_advice")
            continue
        used.add(entry["id"])
        market = entry.get("market") if entry.get("group") == "asia" and entry.get("market") in MARKETS[1:] else ""
        kind = entry.get("kind") if entry.get("kind") in MARKET_KINDS else "other"
        picked = watch_picks(entry, watchable, problems, unknown)
        written = points(entry, problems, my_stocks)
        mine = stock_picks(entry.get("my_track"), stock_offers, named=written.get("owner_effect"), problems=problems, unknown=unknown)
        out.append({**mark, **written, **({"track": picked} if picked else {}), **({"my_track": mine} if mine else {}),
                    **news_wait(entry, problems),
                    "id": entry["id"], "group": entry["group"], "kind": kind,
                    "market": market or source["market_hint"] or "",
                    "translated": _translated(source["title"]), "source": source["source"], "link": source["link"],
                    "published_at": source["published_at"]})
    return _keep_per_group(out)


def _kind_rank(kind: Any) -> int:
    return MARKET_KINDS.index(kind) if kind in MARKET_KINDS else len(MARKET_KINDS)


def _keep_per_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每組留 GROUP_KEEP 則：影響大的在前；一樣大時照類別（政策＞財報＞合作訂單＞法人＞總經＞其他）；再一樣時新的在前。"""
    kept = []
    for group in GROUPS:
        newest = sorted((row for row in rows if row["group"] == group), key=lambda row: row["published_at"], reverse=True)
        ranked = sorted(newest, key=lambda row: (-row["impact"], _kind_rank(row.get("kind"))))
        kept += ranked[:GROUP_KEEP]
    return kept


def unclassified_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """模型沒寫成：標題照原文存、分在來源的預設組（全球大事放美股），不標利多利空、沒有理由。"""
    rows = [{"id": row["id"], "group": "us" if row["group_hint"] == "global" else row["group_hint"],
             "market": row["market_hint"] or "", "kind": None, "tone": "flat", "impact": 1, "title": row["title"][:TITLE_MAX],
             "reason": "", "translated": 0, "source": row["source"], "link": row["link"],
             "published_at": row["published_at"]} for row in items]
    return _keep_per_group(rows)


def validate_watch(output: Any, watch: Sequence[Mapping[str, Any]], counts: dict[str, int] | None = None,
                   problems: list[Problem] | None = None, *, stock_offers: Mapping[str, Mapping[str, Any]] | None = None,
                   picked: list[dict[str, Any]] | None = None, unknown: list[str] | None = None) -> list[dict[str, Any]]:
    """個股新聞的判讀 → 要存進 stock_news_marks 的列（每則一列，判成無關的也存、relevant 0，之後不再重判）。
    同一則從不同來源進來的（row 的 links）都記同一個結果；第二個以後記成無關，畫面才不會重複。
    counts 給了就記有關、無關各幾則。
    stock_offers（只有站主自己的呼叫才給）：模型替那一檔挑的條件另外收進 picked（帶那一則的影響力與準備），
    不放進回傳的列——判讀是大家共用的，站主要盯什麼不能跟著寫進去。"""
    by_id = {row["id"]: row for row in watch}
    out, used = [], set()
    for entry in (output or {}).get("watch") or [] if isinstance(output, Mapping) else []:
        source = by_id.get(entry.get("id")) if isinstance(entry, Mapping) else None
        if not source or entry["id"] in used:
            continue
        unrelated = entry.get("relevant") == "no"
        mark = _mark(entry)
        if not mark and not unrelated:
            continue
        advice = text_problem("reason", mark["reason"]) if mark and not unrelated else None
        if advice:   # 這一則先不記：下一輪會再判一次
            if problems is not None:
                problems.append(("reason", advice, mark["reason"]))
            continue
        used.add(entry["id"])
        base = {"code": source["code"], "translated": _translated(source["title"])}
        if unrelated:
            base.update(relevant=0, kind="other", tone="flat", impact=1, title=source["title"][:TITLE_MAX],
                        reason=(_text(entry.get("reason"), REASON_MAX) or "和公司無關"), translated=0)
        else:
            base.update(mark, relevant=1, kind=entry.get("kind") if entry.get("kind") in STOCK_KINDS else "other")
            base.update(points(entry, problems))
            mine = stock_picks(entry.get("track"), stock_offers, code=source["code"], problems=problems, unknown=unknown)
            if picked is not None:
                picked += [{**pick, "impact": base["impact"], "prepare": base.get("prepare")} for pick in mine]
        if counts is not None:
            key = "unrelated" if unrelated else "related"
            counts[key] = counts.get(key, 0) + 1
        links = source.get("links") or [source["link"]]
        out.append({**base, "link": links[0]})
        out += [{**{key: value for key, value in base.items() if key not in POINT_FIELDS}, "link": link, "relevant": 0,
                 "reason": "同一則"} for link in links[1:]]
    return out


def validate_summary(output: Any, my_stocks: Mapping[str, str] | None = None,
                     problems: list[Problem] | None = None, watchable: Sequence[Mapping[str, Any]] | None = None,
                     unknown: list[str] | None = None,
                     stock_offers: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any] | None:
    """總結的四段：{summary（發生什麼）, effect, owner_effect, conditions, prepare}；發生什麼那一段不成立就整份不放。"""
    if not isinstance(output, Mapping):
        return None
    text = whole_sentences(_text(unwrap_text(output.get("market_commentary") or output.get("summary")), 2000), SUMMARY_MAX)
    if not text:
        return None
    reason = text_problem("market_commentary", text) if is_summary(text) else "這不是盤勢解說，是在交代自己做了什麼"
    if reason:
        if problems is not None:
            problems.append(("market_commentary", reason, text))
        return None
    picked = watch_picks(output, watchable, problems, unknown)
    written = points(output, problems, my_stocks)
    mine = stock_picks(output.get("my_track"), stock_offers, named=written.get("owner_effect"), problems=problems, unknown=unknown)
    return {"summary": text, **written, **({"track": picked} if picked else {}), **({"my_track": mine} if mine else {}),
            **news_wait(output, problems)}


def whole_sentences(text: str | None, limit: int) -> str | None:
    """超過 limit 就退到 limit 以內最後一個句號（。！？）；一個句號都沒有才硬切。"""
    if not text or len(text) <= limit:
        return text
    head = text[:limit]
    end = max(head.rfind(mark) for mark in "。！？")
    return head[:end + 1] if end >= 0 else head


def unwrap_text(value: Any) -> Any:
    """模型把整段 JSON 塞進文字欄位（{"summary": "…"}）時，取裡面那段字。"""
    if isinstance(value, str) and value.lstrip().startswith("{"):
        inner = json_from_text(value)
        if isinstance(inner, Mapping):
            return next((text for text in inner.values() if isinstance(text, str) and text.strip()), value)
    return value


# ---- 資料庫 ----

_q = wn._q   # 和個股新聞同一個 SQL 字面值跳脫


def watchlist(query: Callable[[str], list[dict]], email: str) -> dict[str, str]:
    """這個人的自選：{代號: 名稱}。"""
    if not wn._EMAIL.fullmatch(email or ""):
        return {}
    return {row["code"]: row["name"] for row in query(
        "SELECT code, max(name) AS name FROM user_watchlist WHERE lower(email) = lower(" + _q(email) + ") GROUP BY code")}


def owner_stocks(query: Callable[[str], list[dict]], email: str) -> dict[str, str]:
    """站主的自選＋庫存：{代號: 名稱}。消息牽涉到這幾檔時請模型點名（owner_effect，只回給站主）。"""
    names = watchlist(query, email)
    try:
        held = query("SELECT code, max(name) AS name FROM user_holdings WHERE lower(email) = lower(" + _q(email) + ") GROUP BY code")
    except Exception:  # noqa: BLE001 — 讀不到庫存就只用自選
        held = []
    return {**{row["code"]: row["name"] for row in held}, **names}


def pending_stock_rows(query: Callable[[str], list[dict]], names: Mapping[str, str], now: str, *, limit: int,
                       skip: set[tuple[str, str]] | None = None) -> list[dict[str, Any]]:
    """這幾檔近 STOCK_DAYS 天、還沒人判讀過的個股新聞（watch_news 裡沒有對應的 stock_news_marks），新到舊、最多 limit 則。
    中央社整批對到的（cna-*）要標題點名公司才算（只在前言提到的是贊助、地名那一類）；
    同一檔同一個標題從不同來源進來的併成一則（links 記全部連結）。skip：這一輪已經有人判的。"""
    codes = [code for code in names if re.fullmatch(r"[0-9A-Za-z]{1,12}", code)]
    if not codes:
        return []
    since = _iso(datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(days=STOCK_DAYS))
    rows = query("SELECT n.code, n.link, n.source, n.title, n.summary, n.published_at FROM watch_news n "
                 "LEFT JOIN stock_news_marks m ON m.code = n.code AND m.link = n.link "
                 f"WHERE n.code IN ({','.join(_q(code) for code in codes)}) AND m.code IS NULL "
                 f"AND n.published_at >= {_q(since)} AND n.published_at <= {_q(now)} ORDER BY n.published_at DESC, n.link")
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if skip and (row["code"], row["link"]) in skip:
            continue
        stock = {"code": row["code"], "name": names[row["code"]]}
        if str(row["source"]).startswith("cna-") and not wn._mentions(row["title"], stock):
            continue
        if wn.is_noise(row["title"]):
            continue
        key = (row["code"], re.sub(r"\s+", "", row["title"]))
        if key in merged:
            merged[key]["links"].append(row["link"])
            continue
        if len(merged) >= limit:
            continue
        merged[key] = {"code": row["code"], "name": names[row["code"]], "link": row["link"], "links": [row["link"]],
                       "source": wn.SOURCE_LABELS.get(row["source"], row["source"]), "title": row["title"],
                       "summary": (row.get("summary") or "")[:120] if row["source"] == "mops" else "",
                       "published_at": row["published_at"]}
    out = list(merged.values())
    for index, row in enumerate(out, 1):
        row["id"] = f"w{index}"
    return out


def has_point_columns(query: Callable[[str], list[dict]]) -> bool:
    """0033（影響、條件、準備那幾欄）套了沒：沒套就照舊格式寫，這一輪照樣有消息，只是沒有新的三段。"""
    try:
        rows = query("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'market_news_items'")
    except Exception:  # noqa: BLE001 — 查不到就當沒套
        return False
    return bool(rows) and bool(re.search(r"\bowner_effect\b", str(rows[0].get("sql") or "")))


def _point_columns(row: Mapping[str, Any], fields: Sequence[str]) -> tuple[str, str]:
    """新三段的欄名與值：條件與準備存成 JSON 陣列，沒有的存 NULL。"""
    values = [(json.dumps(row[field], ensure_ascii=False) if field in ("conditions", "prepare") else row[field])
              if row.get(field) else None for field in fields]
    return "".join(f", {field}" for field in fields), "".join(f", {_q(value)}" for value in values)


def marks_sql(marks: Sequence[Mapping[str, Any]], *, judged_by: str, now: str, point_columns: bool = True) -> list[str]:
    fields = tuple(field for field in POINT_FIELDS if field != "owner_effect") if point_columns else ()   # 判讀是大家共用的
    extra = [_point_columns(row, fields) for row in marks]
    return ["INSERT OR REPLACE INTO stock_news_marks (code, link, relevant, kind, tone, impact, title, translated, reason, "
            f"judged_by, judged_at{names}) VALUES ("
            + ", ".join([_q(row["code"]), _q(row["link"]), str(int(row["relevant"])), _q(row["kind"]), _q(row["tone"]),
                         str(int(row["impact"])), _q(row["title"]), str(int(row["translated"])), _q(row["reason"]),
                         _q(judged_by), _q(now)]) + values + ");" for row, (names, values) in zip(marks, extra)]


def run_sql(run: Mapping[str, Any], items: Sequence[Mapping[str, Any]], marks: Mapping[str, Sequence[Mapping[str, Any]]],
            *, now: str, point_columns: bool = True) -> str:
    """同一輪重跑就整份換掉；個股新聞的判讀（marks：{'owner'／'friend': 列}）每則一列、判過不重判；
    超過 KEEP_DAYS 天的輪次清掉，對不到個股新聞（已過 90 天被清掉）的判讀也清掉。
    point_columns False（0033 還沒套）：不寫影響、條件、準備那幾欄。"""
    run_id = run["run_id"]
    cutoff = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    fields = POINT_FIELDS if point_columns else ()
    names, values = _point_columns(run, fields)
    lines = [f"DELETE FROM market_news_items WHERE run_id = {_q(run_id)};",
             f"DELETE FROM market_news_runs WHERE run_id = {_q(run_id)};",
             f"INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status, summary, indices, sources, model{names}) VALUES ("
             + ", ".join(_q(run[key]) for key in ("run_id", "trade_date", "slot", "created_at", "status", "summary"))
             + f", {_q(json.dumps(run['indices'], ensure_ascii=False))}, {_q(json.dumps(run['sources'], ensure_ascii=False))}, "
             + f"{_q(run.get('model'))}{values});"]
    for row in items:
        names, values = _point_columns(row, fields)
        lines.append("INSERT INTO market_news_items (run_id, item_id, grp, market, kind, tone, impact, title, translated, reason, "
                     f"source, link, published_at{names}) VALUES ("
                     + ", ".join([_q(run_id), _q(row["id"]), _q(row["group"]), _q(row["market"]), _q(row.get("kind")), _q(row["tone"]),
                                  str(int(row["impact"])), _q(row["title"]), str(int(row["translated"])), _q(row["reason"]),
                                  _q(row["source"]), _q(row["link"]), _q(row["published_at"])]) + values + ");")
    for judged_by, rows in marks.items():
        lines += marks_sql(rows, judged_by=judged_by, now=now, point_columns=point_columns)
    old = f"SELECT run_id FROM market_news_runs WHERE trade_date < {_q(cutoff)}"
    lines += [f"DELETE FROM market_news_items WHERE run_id IN ({old});",
              f"DELETE FROM market_news_user_items WHERE run_id IN ({old});",
              f"DELETE FROM market_news_runs WHERE trade_date < {_q(cutoff)};",
              "DELETE FROM stock_news_marks WHERE NOT EXISTS (SELECT 1 FROM watch_news n "
              "WHERE n.code = stock_news_marks.code AND n.link = stock_news_marks.link);"]
    return "\n".join(lines) + "\n"


Asker = Callable[[str, Mapping[str, Any]], tuple[str, Any, str]]
FriendAsker = Callable[[Mapping[str, Any], str, Mapping[str, Any]], tuple[str, Any, str]]


def run(slot: str, *, trade_date: str, now: str, query: Callable[[str], list[dict]], execute: Callable[[str], None],
        get: Callable, sleep: Callable[[float], None], ask_owner: Asker, owner_email: str,
        friend_keys: Sequence[Mapping[str, Any]], ask_friend: FriendAsker, taiex: Callable[[], dict | None],
        model: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """一輪：抓市場來源、照重要性選材 → 站主的模型分批判讀（市場新聞＋站主自選裡還沒判過的個股新聞）→ 寫總結 →
    每位有金鑰的朋友判他自選裡還沒人判過的 → 一份 SQL 寫回。"""
    if slot not in SLOTS:
        raise ValueError(f"slot 只能是 {SLOTS}")
    if not wn._EMAIL.fullmatch(owner_email or ""):
        raise ValueError("站主 email 格式不對")
    if not dry_run and not slot_storable(query, slot):
        # 0037 還沒套到網站資料庫：這一班寫不進去（舊的 CHECK 只認原本三班）。不抓、不叫模型，安靜跳過。
        return {"status": "skipped", "reason": "資料庫還沒套 0037（不認得這個時段）", "trade_date": trade_date, "slot": slot}
    since = window_start(query, now)
    items, sources = collect(get=get, now=now, sleep=sleep, since=since,
                             gnews_feeds=NIGHT_GNEWS_FEEDS if slot in NIGHT_SLOTS else None)
    index_rows = indices(query, taiex)
    owner_names = watchlist(query, owner_email)
    owner_watch = pending_stock_rows(query, owner_names, now, limit=OWNER_STOCK_MAX)
    my_stocks = owner_stocks(query, owner_email)
    point_columns = has_point_columns(query)
    # 盯項（票 03）：這一班可以交給模型挑的大盤條件；0034 還沒套、還沒有每日檢查的數字時是空的，這一班就不建盯項
    watchable = impact_watch.offered(query, trade_date)
    # 站主自選股與庫存的盯項（票 06）：每一檔可以挑的條件；只進站主自己的呼叫，挑到的只寫成只給站主看的盯項
    stock_offers = impact_watch.stock_offered(query, trade_date, my_stocks)
    owner_stock_picks: list[dict[str, Any]] = []
    unknown_picks: list[str] = []
    from concurrent.futures import ThreadPoolExecutor
    batches = [items[start:start + BATCH_ITEMS] for start in range(0, len(items), BATCH_ITEMS)]
    stock_batches = [owner_watch[start:start + BATCH_ITEMS] for start in range(0, len(owner_watch), BATCH_ITEMS)]

    def problems_of(validate, *args, **kwargs):
        """這一批回答裡被文字檢查退回的句子（買賣指令、準備不是「如果…就…」），和留得下來的有幾則。"""
        def check(output):
            found: list[Problem] = []
            kept = validate(output, *args, **kwargs, problems=found)
            return found, len(kept) if isinstance(kept, list) else int(bool(kept))
        return check

    jobs = [(f"items{index + 1}", items_prompt(batch, my_stocks, watchable, stock_offers), ITEMS_SCHEMA,
             problems_of(validate_items, batch, my_stocks=my_stocks, watchable=watchable, stock_offers=stock_offers))
            for index, batch in enumerate(batches)]
    jobs += [(f"watch{index + 1}", watch_prompt(batch, stock_offers), WATCH_SCHEMA,
              problems_of(validate_watch, batch, stock_offers=stock_offers))
             for index, batch in enumerate(stock_batches)]
    calls: dict[str, dict[str, Any]] = {}

    attempts: dict[str, int] = {}
    text_retries: dict[str, str] = {}

    def call(job):
        """一次呼叫沒拿到結果就再試一次；拿到了但有句子被文字檢查退回，也再問一次並把退回的原因告訴它。
        兩種合計 CALL_ATTEMPTS 次；最後一次還有被退回的句子，就只丟那幾句（validate_* 會處理）。
        重問的回答留得下來的比前一次少（少回幾則、整份壞掉）就用前一次的：一句不合格不該賠掉整批。"""
        name, text, schema, check = job
        suffix, usable, usable_size = "", None, -1
        for attempt in range(1, CALL_ATTEMPTS + 1):
            attempts[name] = attempt
            try:
                status, output, note = ask_owner(text + suffix, schema)
            except Exception as error:  # noqa: BLE001 — 一批出錯只影響那一批
                status, output, note = type(error).__name__, None, str(error)[:200]
            if status != "ok":
                continue
            found, size = check(coerce_output(output))
            if size >= usable_size:
                usable, usable_size = (status, output, note), size
            if not found or attempt == CALL_ATTEMPTS:
                break
            suffix = retry_note(found[0])
            text_retries[name] = f"{found[0][0]}：{found[0][1]}"
        if usable:   # 重問那一次壞了或變差：用前一次的回答（被退回的句子照樣只丟那幾句）
            status, output, note = usable
        return name, status, coerce_output(output), note

    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        results = list(pool.map(call, jobs))
    rejected: dict[str, int] = {}
    text_rejected: dict[str, int] = {}
    classified: list[dict[str, Any]] = []
    unmarked: list[Mapping[str, Any]] = []
    owner_marks: list[dict[str, Any]] = []

    def count_text(found: Sequence[Problem]) -> None:
        for field, _reason, _text_ in found:
            text_rejected[field] = text_rejected.get(field, 0) + 1

    for (name, status, output, note), batch in zip(results, [*batches, *stock_batches]):
        record = {"status": status, "attempts": attempts.get(name, 1),
                  **({"note": str(note)[:400]} if note and status != "ok" else {}),
                  **({"text_retry": text_retries[name]} if name in text_retries else {})}
        found: list[Problem] = []
        if name.startswith("watch"):
            if status == "ok":
                owner_marks += validate_watch(output, batch, record, found, stock_offers=stock_offers,
                                              picked=owner_stock_picks, unknown=unknown_picks)
            record["sent"] = len(batch)
        else:
            kept = validate_items(output, batch, rejected, my_stocks=my_stocks, problems=found, watchable=watchable,
                                  unknown=unknown_picks, stock_offers=stock_offers) if status == "ok" else []
            record.update(returned=len(output.get("items") or []) if isinstance(output, Mapping) else 0, kept=len(kept))
            classified += kept
            if not kept:
                unmarked += batch
        count_text(found)
        calls[name] = record
    summary: dict[str, Any] | None = None
    if classified:
        # 回成交代工作的話、或寫了買賣指令：再問一次並告訴它原因；還是不行就不放總結（其餘段落被退回的只丟那一句）
        status, output, note = call(("summary", summary_prompt(slot, trade_date, classified, index_rows, my_stocks, watchable, stock_offers),
                                     SUMMARY_SCHEMA,
                                     problems_of(validate_summary, my_stocks, watchable=watchable, stock_offers=stock_offers)))[1:]
        found = []
        summary = validate_summary(output, my_stocks, found, watchable, unknown_picks, stock_offers) if status == "ok" else None
        count_text(found)
        if status == "ok" and not summary and found and found[0][0] == "market_commentary":
            status, note = ("not_a_summary" if not is_summary(found[0][2]) else "summary_rejected"), found[0][2]
        calls["summary"] = {"status": status, "attempts": attempts.get("summary", 1),
                            **({"note": str(note)[:300]} if note and status != "ok" else {}),
                            **({"text_retry": text_retries["summary"]} if "summary" in text_retries else {})}
    # 有判讀就套上；整批沒判讀成的那幾則照原標題存（不標利多利空、沒有理由）；一則都沒有才當「只有標題」。
    if classified:
        stored = _keep_per_group(classified + unclassified_items(unmarked))
        run_status = "ok"
    else:
        stored, run_status, summary = unclassified_items(items), "unclassified", None
    marks: dict[str, list[dict[str, Any]]] = {"owner": owner_marks, "friend": []}
    status = "ok" if classified else next((r["status"] for r in calls.values() if r["status"] != "ok"), "no_items")
    note = ""
    def with_points(rows, field):
        return sum(1 for row in rows if row.get(field))

    diagnosis = {"calls": calls, "items_kept": len(classified), "rejected": rejected,
                 "summary_chars": len((summary or {}).get("summary") or ""),
                 # 新寫法寫成幾則（上線後對帳用）：有影響、有條件、有準備、有點名站主股票的各幾則；被文字檢查丟掉的句子各幾句
                 "points": {"columns": point_columns,
                            **{field: with_points(stored, field) + with_points(owner_marks, field) for field in POINT_FIELDS},
                            "summary": sorted(key for key in (summary or {}) if key in POINT_FIELDS),
                            "text_rejected": text_rejected}}
    friends: dict[str, str] = {}
    for key_row in friend_keys:
        email = str(key_row.get("email") or "")
        if not wn._EMAIL.fullmatch(email) or email.lower() == owner_email.lower():
            continue
        # 朋友自選裡還沒人判過的（站主這一輪剛判的不算）：用朋友自己的金鑰，判完大家共用。
        done_now = {(row["code"], row["link"]) for rows in marks.values() for row in rows}
        watch = pending_stock_rows(query, watchlist(query, email), now, limit=FRIEND_STOCK_MAX, skip=done_now)
        if not watch:
            friends[email] = "nothing_new"
            continue
        failures: list[str] = []
        notes: list[str] = []
        pieces = [watch[start:start + FRIEND_BATCH] for start in range(0, len(watch), FRIEND_BATCH)]
        for piece in pieces:
            try:
                friend_status, friend_output, friend_note = ask_friend(key_row, watch_prompt(piece), WATCH_SCHEMA)
            except Exception as error:  # noqa: BLE001 — 一位朋友失敗不影響別人
                friend_status, friend_output, friend_note = type(error).__name__, None, ""
            if friend_status == "ok":
                found = []   # 朋友的金鑰不重問（額度是朋友的）：被退回的句子只丟掉、照樣計數
                marks["friend"] += validate_watch(coerce_output(friend_output), piece, None, found)
                count_text(found)
                if friend_note:
                    notes.append(friend_note)
            else:
                failures.append(f"{friend_status} {friend_note}".strip())
                if friend_status in ("quota", "key_invalid", "key_revoked", "key_decrypt_failed", "region"):
                    break   # 金鑰或額度的問題：剩下的不用再試
        done = len(pieces) - len(failures)
        friends[email] = (" ".join(["ok", *notes]) if not failures else f"{done}/{len(pieces)} {failures[0]}")[:900]
    run_row = {"run_id": f"{trade_date}-{slot}", "trade_date": trade_date, "slot": slot, "created_at": now,
               "status": run_status, **{field: None for field in POINT_FIELDS}, **(summary or {"summary": None}),
               "indices": index_rows,
               "sources": {"feeds": sources, "missed_groups": missed_groups(sources)}, "model": model}
    script = run_sql(run_row, stored, marks, now=now, point_columns=point_columns)
    # 挑到的條件寫成盯項：影響力夠的消息與總結才建；去重、上限與到期日在 impact_watch。
    # 和消息分開寫：盯項寫不成不能賠掉這一班的消息。
    diagnosis["watch"] = {"offered": len(watchable), "unknown_ids": unknown_picks,
                          **({"stocks_offered": len(stock_offers)} if stock_offers else {})}
    watch_script = ""
    major = [row for row in stored if row["impact"] >= WATCH_MIN_IMPACT]

    def sourced(field: str) -> list[dict[str, Any]]:
        """消息與總結裡的 field（track：一串數字型條件；wait：一個消息型條件）→ 帶上影響力、原本寫的準備與出處。"""
        found = [(pick, row["impact"], row.get("prepare"), row["id"]) for row in major
                 for pick in ([row[field]] if isinstance(row.get(field), Mapping) else row.get(field) or [])]
        value = (summary or {}).get(field)
        found += [(pick, SUMMARY_WATCH_IMPACT, (summary or {}).get("prepare"), "summary")
                  for pick in ([value] if isinstance(value, Mapping) else value or [])]
        return [{**pick, "impact": impact, "prepare": prepare, "source_ref": f"{run_row['run_id']}#{ref}"}
                for pick, impact, prepare, ref in found]

    picks = sourced("track") if watchable else []
    # 站主個股的（票 06）：市場消息與總結點名那一檔時挑的、站主自選的個股新聞挑的。對象是股票代號，
    # creation_sql 會把它們標成只給站主看；和大盤的、消息型的一起排上限。
    if stock_offers:
        picks += sourced("my_track")
        picks += [{**pick, "source_ref": f"{run_row['run_id']}#stock:{pick['subject']}"}
                  for pick in owner_stock_picks if pick["impact"] >= WATCH_MIN_IMPACT]
    waits = sourced("wait")   # 消息型（票 05）：不靠每日檢查存下的數字，沒有數字型清單的那一班也可以寫
    if watchable or stock_offers or waits:
        try:
            if waits:
                # 和已經在盯的是不是同一件事（關鍵詞相同或高度重疊）在 impact_watch_news；上限與到期日和數字型一起算
                waits, same = impact_watch_news.fresh(waits, impact_watch_news.existing(query, trade_date), now=now)
                diagnosis["watch"]["news"] = {"written": len(waits) + same, "same_event": same}
            watch_script, watch_report = impact_watch.creation_sql([*picks, *waits], impact_watch.active_items(query, trade_date),
                                                                   trade_date=trade_date, now=now)
            diagnosis["watch"].update(watch_report)
        except Exception as error:  # noqa: BLE001
            watch_script = ""
            diagnosis["watch"]["error"] = type(error).__name__
    if not dry_run:
        execute(script)
        if watch_script:
            try:
                execute(watch_script)
            except Exception as error:  # noqa: BLE001
                diagnosis["watch"]["error"] = f"write: {type(error).__name__}"
    return {"status": "ok", "run_id": run_row["run_id"], "run_status": run_status, "items": len(stored),
            "sent": len(items), "model": status, **({"model_note": note[:200]} if status != "ok" and note else {}),
            "owner_watch_sent": len(owner_watch),
            "owner_watch": sum(1 for row in owner_marks if row["relevant"]),
            "owner_watch_unrelated": sum(1 for row in owner_marks if not row["relevant"]),
            "diagnosis": diagnosis,
            "friends": friends, "sources": sources,
            "dry_run": dry_run}


# ---- 排程用的接線（market-news.yml） ----

def _taiex_from_fugle() -> dict | None:
    import licensed_inputs
    fugle = licensed_inputs.fugle_from_env()
    if fugle is None:
        return None
    quote = fugle.quote(licensed_inputs.INDEX_SYMBOL)
    change = quote.get("changePercent")
    return {"name": "加權", "change_pct": round(float(change), 2), "date": quote.get("date")} if change is not None else None


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def json_from_text(text: str) -> Any:
    """文字回覆裡的 JSON：整段、```json 包著的、或第一個 { 到最後一個 } 之間；都不是就 None。"""
    candidates = [text, *(match.group(1) for match in _FENCE.finditer(text or ""))]
    if text and "{" in text and "}" in text:
        candidates.append(text[text.index("{"):text.rindex("}") + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(value, Mapping):
            return value
    return None


def interpret_envelope(envelope: Mapping[str, Any] | None) -> tuple[str, Any, str]:
    """agy 的 result 事件 → (狀態, 輸出, 說明)。
    新版有 structured_output；Actions 的 1.5.3 沒有這一欄，答案在 response（文字，可能包在 ```json 裡）。
    都拿不到就回 no_structured_output，說明寫 response 前 300 字、被擋下的動作與回合數（2026-11-19 run 44531113573）。"""
    if not envelope or envelope.get("status") != "SUCCESS":
        return "model_failed", None, str((envelope or {}).get("error") or "沒有 result 事件")[:300]
    structured = envelope.get("structured_output")
    if isinstance(structured, Mapping):
        return "ok", structured, ""
    for key in ("structured_output", "response", "result", "text", "output"):
        value = envelope.get(key)
        if isinstance(value, Mapping) and key != "structured_output":
            return "ok", value, f"答案在 {key}"
        parsed = json_from_text(value) if isinstance(value, str) else None
        if parsed is not None:
            return "ok", parsed, f"從 {key} 解出 JSON"
    response = next((envelope[key] for key in ("response", "result", "text", "output")
                     if isinstance(envelope.get(key), str) and envelope[key].strip()), "")
    denied = envelope.get("denied_actions")
    return "no_structured_output", None, (
        f"response={response[:300]!r} denied_actions={json.dumps(denied, ensure_ascii=False, default=str)[:120]} "
        f"num_turns={envelope.get('num_turns')} keys={sorted(envelope)}")


def _owner_asker(agy_bin: str | None, home: str | None, model: str, effort: str) -> Asker:
    import os
    import subprocess
    from pathlib import Path

    import ledger_core
    import plain_daily

    def ask(text: str, schema: Mapping[str, Any]) -> tuple[str, Any, str]:
        if not agy_bin or not Path(agy_bin).exists():
            return "agy_unavailable", None, "agy 沒有安裝成功"
        try:
            completed = subprocess.run(
                ledger_core._model_command(agy_bin, model, schema, effort, MODEL_TIMEOUT_SECONDS),
                input=ledger_core._stream_input(text), capture_output=True, timeout=MODEL_TIMEOUT_SECONDS + 60, check=False,
                env=plain_daily.agy_environment(os.environ, home))
        except subprocess.TimeoutExpired:
            return "model_timeout", None, f"超過 {MODEL_TIMEOUT_SECONDS + 60} 秒沒有回應"
        except OSError as error:
            return "model_failed", None, f"{type(error).__name__}: {error}"
        try:
            envelope = ledger_core._result_event(completed.stdout)
        except (UnicodeDecodeError, json.JSONDecodeError):
            envelope = None
        if completed.returncode != 0 and (envelope is None or envelope.get("status") != "SUCCESS"):
            return "model_failed", None, ledger_core._failure(completed.returncode, completed.stderr)
        return interpret_envelope(envelope)
    return ask


def friend_body(text: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    return {"contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": dict(schema),
                                 "thinkingConfig": {"thinkingLevel": FRIEND_NEWS_EFFORT}}}


class FriendHttpError(Exception):
    """Gemini API 回的錯誤：HTTP 狀態、Google 的 status、reasons 與訊息前 FRIEND_ERROR_TEXT_MAX 字（不含金鑰）。"""

    def __init__(self, status: int, error_status: str | None = None, reasons: tuple[str, ...] = (), message: str = ""):
        super().__init__(f"HTTP {status}")
        self.status, self.error_status, self.reasons = status, error_status, reasons
        self.message = re.sub(r"\s+", " ", message or "")[:FRIEND_ERROR_TEXT_MAX]


def _friend_post(url: str, body: Mapping[str, Any], api_key: str, timeout: float) -> dict[str, Any]:
    """和 friend_ai_digest.http_post 同一種送法（金鑰只放標頭、不跟轉址），差別是把錯誤訊息的開頭也帶出來。"""
    import http.client
    import urllib.error
    import urllib.request

    import friend_ai_digest as fad
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"content-type": "application/json", "x-goog-api-key": api_key})
    try:
        with fad._OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw = ""
        try:
            raw = error.read().decode("utf-8", errors="replace")
            detail = json.loads(raw).get("error") or {}
        except (ValueError, AttributeError, OSError, http.client.HTTPException):
            detail = {}
        message = str(detail.get("message") or raw)
        reasons = tuple(str(item.get("reason")) for item in detail.get("details") or [] if isinstance(item, dict))
        if "api key" in message.lower():
            reasons = (*reasons, "API_KEY_INVALID")
        raise FriendHttpError(error.code, detail.get("status"), reasons, message.replace(api_key, "***")) from None
    except TimeoutError:
        raise fad.GeminiTimeout("TimeoutError") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise fad.GeminiTimeout("TimeoutError") from None
        raise OSError(type(error).__name__) from None
    except (ValueError, http.client.HTTPException) as error:
        raise OSError(type(error).__name__) from None


def _friend_asker(model: str) -> FriendAsker:
    """朋友的金鑰走 Gemini API。依序試：主要模型帶結構描述 → 備援模型（FRIEND_FALLBACK_MODEL）帶結構描述 →
    備援模型不帶結構描述（JSON 從文字裡解）。備援模型不指定思考程度。金鑰或額度的問題不再往下試。
    每一次用哪個模型、花幾秒、結果與錯誤訊息開頭都寫進說明。"""
    import time

    import friend_ai_digest as fad

    def ask(key_row: Mapping[str, Any], text: str, schema: Mapping[str, Any]) -> tuple[str, Any, str]:
        try:
            key = fad.node_decrypt(dict(key_row))
        except Exception:  # noqa: BLE001
            return "key_decrypt_failed", None, ""
        with_schema = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                       "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": dict(schema)}}
        plain = {"contents": [{"role": "user", "parts": [{"text": text}]}],
                 "generationConfig": {"responseMimeType": "application/json"}}
        tries = ((model, "schema", friend_body(text, schema)), (FRIEND_FALLBACK_MODEL, "schema", with_schema),
                 (FRIEND_FALLBACK_MODEL, "plain", plain))
        trail: list[str] = []
        status = "gemini_failed"
        for name, mode, body in tries:
            started = time.monotonic()
            label = lambda: f"{name} {mode} {round(time.monotonic() - started)}s"  # noqa: E731
            try:
                reply = _friend_post(fad.GEMINI_URL.format(model=name), body, key, FRIEND_TIMEOUT_SECONDS)
                parts = reply["candidates"][0]["content"]["parts"]
                joined = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought"))
                output = json_from_text(joined)
                if output is not None:
                    trail.append(f"{label()} ok")
                    return "ok", output, "; ".join(trail)
                status = "gemini_failed"
                trail.append(f"{label()} no_json")
            except FriendHttpError as error:
                status = fad.classify_http_error(fad.GeminiHttpError(error.status, error.error_status, error.reasons))
                trail.append(f"{label()} HTTP {error.status} {error.error_status or ''} {error.message!r}".replace("  ", " "))
                if status in ("quota", "key_invalid", "key_revoked", "region"):
                    break
            except fad.GeminiTimeout:
                status = "gemini_unavailable"
                trail.append(f"{label()} timeout")
            except OSError as error:
                status = "gemini_unavailable"
                trail.append(f"{label()} {type(error).__name__}")
            except (KeyError, IndexError, TypeError):
                status = "gemini_failed"
                trail.append(f"{label()} bad_reply")
        return status, None, "; ".join(trail)
    return ask


def is_trading_day(day: str, holidays: set[str] | None = None) -> bool:
    """週一到週五、而且不在證交所休市日曆裡（颱風假這種臨時休市不在日曆裡，那天照跑）。"""
    if datetime.fromisoformat(day).weekday() >= 5:
        return False
    if holidays is None:
        from level_signals import ensure_holiday_calendar   # 證交所官方休市日曆（專案帶種子、工作集有快取）
        holidays = ensure_holiday_calendar(day)
    return day not in holidays


def slot_storable(query: Callable[[str], list[dict]], slot: str) -> bool:
    """網站資料庫的 market_news_runs 收不收這個時段：原本三班一定收；加開的三班要 0037 套了才收
    （0031 的 CHECK 只認原本三班）。讀不到表的定義就當成收（寫不進去時那一步自己會報錯）。"""
    if slot in DAY_SLOTS:
        return True
    try:
        rows = query("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'market_news_runs'")
    except Exception:  # noqa: BLE001
        return True
    text = str((rows[0].get("sql") if rows else "") or "")
    return not text or f"'{slot}'" in text or not re.search(r"slot\s+IN\s*\(", text, re.IGNORECASE)


def trade_date_for(slot: str, moment: datetime) -> str:
    """這一班算哪一天（台北日期）。美股開盤那一班（23:00）排程晚到、過了午夜才開始跑時，仍算前一天的最後一班，
    不然會變成隔天的 us_open、排到隔天所有班次的後面。其他班次不會跨日，就是台北今天。"""
    local = moment.astimezone(TAIPEI)
    if slot == "us_open" and local.hour < 12:
        local -= timedelta(days=1)
    return local.date().isoformat()


def closed_for(slot: str, trade_date: str, holidays: set[str] | None = None) -> bool:
    """--skip-closed 時這一班要不要跳過：跟台股走的四班（盤前、午盤、收盤後、盤後）台股休市就不跑；
    跟美股時段走的兩班不看台股休市日曆（哪幾天跑由排程的星期決定：23:00 週一到週五、06:30 週二到週六）。"""
    return slot not in US_SESSION_SLOTS and not is_trading_day(trade_date, holidays)


def main(argv: Sequence[str]) -> int:
    import argparse
    import os
    import time

    import gemini_models
    parser = argparse.ArgumentParser(description="首頁消息：一輪")
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--slot", required=True, choices=SLOTS)
    parser.add_argument("--date", help="台北交易日；不給用今天")
    parser.add_argument("--friend-keys", help="wrangler --json 讀出的朋友金鑰（friend-ai-schedule.mjs keys-sql）")
    parser.add_argument("--agy-bin")
    parser.add_argument("--agy-home")
    parser.add_argument("--dry-run", action="store_true", help="照樣抓、照樣叫模型，但不寫網站資料庫")
    parser.add_argument("--skip-closed", action="store_true",
                        help="台股休市日（證交所休市日曆、週末）不跑；美股開盤、美股收盤那兩班不看台股休市")
    parser.add_argument("--private-report", help="完整的報告另外寫到這個檔（不公開的地方）；印出來的那一份是遮過的")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    trade_date = args.date or trade_date_for(args.slot, datetime.now(TAIPEI))
    if args.skip_closed and closed_for(args.slot, trade_date):
        print(json.dumps({"status": "skipped", "reason": "台股休市", "trade_date": trade_date}, ensure_ascii=False))
        return 0
    model = os.environ.get("AI_NEWS_MODEL") or gemini_models.NEWS
    effort = os.environ.get("AI_NEWS_EFFORT") or gemini_models.NEWS_EFFORT
    keys = []
    if args.friend_keys and os.path.exists(args.friend_keys):
        import friend_ai_digest
        from pathlib import Path
        keys = friend_ai_digest.read_keys(Path(args.friend_keys))
    report = run(args.slot, trade_date=trade_date, now=now, query=wn._wrangler_query, execute=wn._wrangler_execute,
                 get=wn._http_get, sleep=time.sleep, ask_owner=_owner_asker(args.agy_bin, args.agy_home, model, effort),
                 owner_email=os.environ.get("OWNER_EMAIL", ""), friend_keys=keys,
                 ask_friend=_friend_asker(gemini_models.FRIEND_DIGEST), taiex=_taiex_from_fugle, model=model,
                 dry_run=args.dry_run)
    # 倉庫公開之後排程的輸出誰都看得到：印的是遮過的（朋友的 email、站主個股的盯項、模型回的字都不印），
    # 完整的只寫進 --private-report。
    import public_log
    full = {**report, "effort": effort}
    public_log.write_private(args.private_report, full)
    print(json.dumps(public_log.market_news(full), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
