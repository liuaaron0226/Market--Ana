"""首頁「消息」：台股、美股、日韓中的利多／利空，和每個人「我的自選」的新聞（本人 2026-11-19「共識達成」）。

本人原話：「主要是做市場的利多以及利空消息……想知道為什麼今天的股票會大漲或者是會大跌或是不怎麼動」。
定案（.scratch/market-news/consensus.md）：
- 交易日三輪（morning 08:00、midday 11:30、close 14:00，台北）；每輪抓一次市場來源 → 站主的模型（預設 Flash 高，
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

import watch_news as wn

TAIPEI = timezone(timedelta(hours=8))
SLOTS = ("morning", "midday", "close")
WINDOW_HOURS = 24          # 只送最近一天發布的
MAX_WINDOW_HOURS = 96      # 週一盤前、連假後：從上一輪開始，最多往回四天
PER_FEED = 6               # 每個來源最多送幾則（最新的）
MAX_ITEMS = 60             # 一次最多送幾則給模型（100 則加思考高超過 agy 的 300 秒，2026-11-19 實測）
GROUP_KEEP = 12            # 每一組最多存幾則（依影響大小）
WATCH_MAX = 30             # 「我的自選」一個人最多送幾則
KEEP_DAYS = 35
SUMMARY_MAX = 200
TITLE_MAX = 120
REASON_MAX = 80
RSS_SUMMARY_MAX = 200
GNEWS_PAUSE_SECONDS = wn.GNEWS_PAUSE_SECONDS
# agy 等模型的上限：一輪幾十則加思考高，300 秒不夠（2026-11-19 本機實測 60 則逾時）；其他任務照舊 300 秒。
MODEL_TIMEOUT_SECONDS = 600
BATCH_ITEMS = 20           # 市場新聞每批幾則（一批一次呼叫）
SUMMARY_TOP = 20           # 總結那一次給幾則（影響大的先）
PARALLEL_CALLS = 3         # 同時幾個呼叫
# 朋友的金鑰走 Gemini API：思考高時一次可能超過朋友解讀用的 120 秒（2026-11-19 正式跑 gemini_unavailable）
FRIEND_TIMEOUT_SECONDS = 300
GROUPS = ("tw", "us", "asia")
MARKETS = ("", "日", "韓", "中")
# 送給模型的結構描述（agy 轉成 Gemini function declaration）：enum 只能是非空字串（2026-11-19 乾跑 INVALID_ARGUMENT），
# 所以影響大小寫成 "1"～"3"、沒有市場寫 "none"，讀回來再換成數字與空字串。
WIRE_IMPACTS = ("1", "2", "3")
WIRE_MARKETS = ("none", "日", "韓", "中")


def _gnews(query: str, sites: Sequence[str]) -> str:
    site = " OR ".join(f"site:{s}" for s in sites)
    return f"{wn.GNEWS_URL}?{urlencode({'q': f'({query}) ({site}) when:1d', 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"


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
]

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
        title, link = _plain(node.findtext("title"), TITLE_MAX * 2), (node.findtext("link") or "").strip()
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
            since: str | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """每個來源抓一次：失敗只缺那一家（sources 記錯誤種類，回 200 但 0 則記 empty）。Google News 回 429／503 時，
    這一輪其他 Google 查詢不再打（記 slowed）。回傳最近 WINDOW_HOURS 小時發布的，每家最多 PER_FEED 則，同連結或同標題只留一則。"""
    oldest = since or _iso(datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(hours=WINDOW_HOURS))
    items: list[dict[str, Any]] = []
    sources: dict[str, str] = {}
    seen: set[str] = set()
    slowed, google_calls = False, 0
    for feed in FEEDS:
        if feed["kind"] == "gnews":
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
        fresh = sorted((row for row in rows if row["published_at"] >= oldest), key=lambda row: row["published_at"], reverse=True)
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
    items = _fair_share(items, MAX_ITEMS)
    for index, row in enumerate(items, 1):
        row["id"] = f"n{index}"
    return items, sources


def _fair_share(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """超過 limit 則時，每家輪流各取最新的一則，直到滿（不讓量大的幾家把別家擠掉）；結果新到舊。"""
    queues: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(items, key=lambda row: row["published_at"], reverse=True):
        queues.setdefault(row["source_key"], []).append(row)
    picked: list[dict[str, Any]] = []
    while len(picked) < limit and any(queues.values()):
        for queue in queues.values():
            if queue and len(picked) < limit:
                picked.append(queue.pop(0))
    return sorted(picked, key=lambda row: row["published_at"], reverse=True)


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

_ITEM = {"type": "object", "properties": {
    "id": {"type": "string"}, "group": {"type": "string", "enum": [*GROUPS, "skip"]},
    "market": {"type": "string", "enum": list(WIRE_MARKETS)}, "tone": {"type": "string", "enum": ["up", "down", "flat"]},
    "impact": {"type": "string", "enum": list(WIRE_IMPACTS)}, "title": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["id", "group", "market", "tone", "impact", "title", "reason"]}
_WATCH = {"type": "object", "properties": {
    "id": {"type": "string"}, "tone": {"type": "string", "enum": ["up", "down", "flat"]},
    "impact": {"type": "string", "enum": list(WIRE_IMPACTS)}, "title": {"type": "string"}, "reason": {"type": "string"}},
    "required": ["id", "tone", "impact", "title", "reason"]}
ITEMS_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": _ITEM}}, "required": ["items"]}
# 欄位不叫 summary：agy 的結構化輸出裡，模型會把 summary 當成「交代自己做了什麼」（2026-11-19 實測兩次）。
SUMMARY_SCHEMA = {"type": "object", "properties": {"market_commentary": {
    "type": "string", "description": "給投資人看的盤勢解說本文，2～3 句，例如「台股今天下跌，主要是外資賣超……」"}},
    "required": ["market_commentary"]}
WATCH_SCHEMA = {"type": "object", "properties": {"watch": {"type": "array", "items": _WATCH}}, "required": ["watch"]}

SLOT_TEXT = {"morning": "台股開盤前（台北 08:00）", "midday": "台股盤中（台北 11:30）", "close": "台股收盤後（台北 14:00）"}

# 2026-11-19 Actions（agy 1.5.3）：60 則一次送，回 SUCCESS 卻沒有結構化輸出。改成分批（每批 BATCH_ITEMS 則）、
# 自選一次、總結另外一次短呼叫；任何一批失敗只影響那一批。
ITEM_RULES = """你是台股投資人的新聞助理。讀 INPUT 的新聞標題與摘要，用繁體中文回答，只回 JSON。
items：每一則都要回（id 照抄）：
- group：這則主要影響哪個市場——tw 台股、us 美股、asia 日韓中；group_hint 是來源的預設，global 的要你分；
  和股市無關（生活、社會、體育、八卦）或看不出影響的填 skip。
- market：asia 那組填「日」「韓」「中」其中一個（香港算中）；其他組填 none。
- tone：對該市場是利多 up、利空 down，或中性 flat（已在預期內、影響不明）。
- impact：影響大小 "3" 大、"2" 中、"1" 小（會左右整個市場或權值股的才是 "3"）。
- title：中文標題；原文是中文就照抄（可去掉多餘的來源字樣），外文就翻成自然的繁體中文，不加原文沒有的事實。
- reason：一句話（30 字內）說明為什麼是利多或利空、會怎麼影響；只能根據標題與摘要，不要編數字或內容。"""

SUMMARY_RULES = """你是財經記者，替台股投資人寫一段盤勢解說，繁體中文。
market_commentary：2～3 句，回答「今天為什麼這樣走」——以台股為主、帶到美股與日韓中，根據 INPUT 的指數漲跌與新聞
（tone up 利多、down 利空；impact 越大越重要）。可以引用 indices 的漲跌幅，其他數字只能來自新聞標題或理由。
slot 是開盤前時，講的是昨夜國際市場與今天可能的影響，不要假裝已經開盤。
這個欄位的內容就是那段解說本身，第一個字就開始講市場（例如「台股今天下跌，主要是……」）；
不要描述你完成了什麼、不要提到指令、JSON、摘要或規則。"""

_NOT_SUMMARY = re.compile(r"JSON|json|指令|已成功|已完成|已依照|以下是|規則|摘要如下|產生.{0,6}摘要")


def is_summary(text: str | None) -> bool:
    """是不是在講市場：模型有時回「已成功依照指令產生摘要…」這種交代工作的話（2026-11-19 實測），那不算。"""
    return bool(text) and not _NOT_SUMMARY.search(text)

WATCH_RULES = """你是台股投資人的新聞助理。讀 INPUT 的自選股新聞，用繁體中文回答，只回 JSON。
watch：每一則都要回（id 照抄）：tone 對那一檔股票是利多 up、利空 down 或中性 flat；impact "3" 大、"2" 中、"1" 小；
title 中文標題（外文翻成自然的繁體中文，不加原文沒有的事實）；reason 一句話（30 字內），只能根據標題，不要編數字。"""


def items_prompt(items: Sequence[Mapping[str, Any]]) -> str:
    payload = {"items": [{key: row[key] for key in ("id", "group_hint", "source", "title", "summary", "published_at")}
                         for row in items]}
    return ITEM_RULES + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def summary_prompt(slot: str, trade_date: str, rows: Sequence[Mapping[str, Any]], index_rows: Sequence[Mapping[str, Any]]) -> str:
    """rows：已判讀的（group、tone、impact、title、reason）；沒有判讀成的時候給原標題。"""
    picked = sorted(rows, key=lambda row: -int(row.get("impact") or 1))[:SUMMARY_TOP]
    payload = {"slot": slot, "slot_text": SLOT_TEXT[slot], "trade_date": trade_date, "indices": list(index_rows),
               "news": [{key: row.get(key) for key in ("group", "tone", "impact", "title", "reason") if row.get(key) not in (None, "")}
                        for row in picked]}
    return SUMMARY_RULES + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def watch_prompt(watch: Sequence[Mapping[str, Any]]) -> str:
    payload = {"watch": [{key: row[key] for key in ("id", "code", "name", "source", "title", "published_at")} for row in watch]}
    return WATCH_RULES + "\nINPUT:" + json.dumps(payload, ensure_ascii=False)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit] if value else None


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
                   rejected: dict[str, int] | None = None) -> list[dict[str, Any]]:
    """模型回的 items → 要存的列：id 要是送出去的、欄位要合格、skip 的不存；每組依影響大小留 GROUP_KEEP 則。
    rejected 給了就記每一種被擋的原因各幾則（執行紀錄用）。"""
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
        used.add(entry["id"])
        market = entry.get("market") if entry.get("group") == "asia" and entry.get("market") in MARKETS[1:] else ""
        out.append({**mark, "id": entry["id"], "group": entry["group"], "market": market or source["market_hint"] or "",
                    "translated": _translated(source["title"]), "source": source["source"], "link": source["link"],
                    "published_at": source["published_at"]})
    return _keep_per_group(out)


def _keep_per_group(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = []
    for group in GROUPS:
        newest = sorted((row for row in rows if row["group"] == group), key=lambda row: row["published_at"], reverse=True)
        ranked = sorted(newest, key=lambda row: -row["impact"])   # 影響大的在前；一樣大時新的在前
        kept += ranked[:GROUP_KEEP]
    return kept


def unclassified_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """模型沒寫成：標題照原文存、分在來源的預設組（全球大事放美股），不標利多利空、沒有理由。"""
    rows = [{"id": row["id"], "group": "us" if row["group_hint"] == "global" else row["group_hint"],
             "market": row["market_hint"] or "", "tone": "flat", "impact": 1, "title": row["title"][:TITLE_MAX],
             "reason": "", "translated": 0, "source": row["source"], "link": row["link"],
             "published_at": row["published_at"]} for row in items]
    return _keep_per_group(rows)


def validate_watch(output: Any, watch: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["id"]: row for row in watch}
    out, used = [], set()
    for entry in (output or {}).get("watch") or [] if isinstance(output, Mapping) else []:
        mark = _mark(entry)
        source = by_id.get(entry.get("id")) if isinstance(entry, Mapping) else None
        if not mark or not source or entry["id"] in used:
            continue
        used.add(entry["id"])
        out.append({**mark, "code": source["code"], "link": source["link"], "translated": _translated(source["title"])})
    return out


# ---- 資料庫 ----

_q = wn._q   # 和個股新聞同一個 SQL 字面值跳脫


def watch_rows(query: Callable[[str], list[dict]], email: str, now: str, since: str | None = None) -> list[dict[str, Any]]:
    """這個人自選股從 since（預設 WINDOW_HOURS 小時前）到 now 的個股新聞（watch_news；中央社對不到個股的 '' 不算）。"""
    if not wn._EMAIL.fullmatch(email or ""):
        return []
    since = since or _iso(datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) - timedelta(hours=WINDOW_HOURS))
    rows = query("SELECT n.code, w.name, n.link, n.source, n.title, n.published_at FROM watch_news n "
                 "JOIN user_watchlist w ON w.code = n.code AND lower(w.email) = lower(" + _q(email) + ") "
                 f"WHERE n.code <> '' AND n.published_at >= {_q(since)} AND n.published_at <= {_q(now)} "
                 "ORDER BY n.published_at DESC, n.link")
    out, seen = [], set()
    for row in rows:
        key = (row["code"], re.sub(r"\s+", "", row["title"]))   # 同一則從中央社 RSS 與 Google News 各來一次：只送一次
        if key in seen or len(out) >= WATCH_MAX:
            continue
        seen.add(key)
        out.append({"id": f"w{len(out) + 1}", "code": row["code"], "name": row["name"], "link": row["link"],
                    "source": wn.SOURCE_LABELS.get(row["source"], row["source"]), "title": row["title"],
                    "published_at": row["published_at"]})
    return out


def run_sql(run: Mapping[str, Any], items: Sequence[Mapping[str, Any]], user_rows: Mapping[str, Sequence[Mapping[str, Any]]],
            *, now: str) -> str:
    """同一輪重跑就整份換掉；超過 KEEP_DAYS 天的輪次連同它的新聞與自選標記一起清掉。"""
    run_id = run["run_id"]
    cutoff = (datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ") - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    lines = [f"DELETE FROM market_news_items WHERE run_id = {_q(run_id)};",
             f"DELETE FROM market_news_user_items WHERE run_id = {_q(run_id)};",
             f"DELETE FROM market_news_runs WHERE run_id = {_q(run_id)};",
             "INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status, summary, indices, sources, model) VALUES ("
             + ", ".join(_q(run[key]) for key in ("run_id", "trade_date", "slot", "created_at", "status", "summary"))
             + f", {_q(json.dumps(run['indices'], ensure_ascii=False))}, {_q(json.dumps(run['sources'], ensure_ascii=False))}, "
             + f"{_q(run.get('model'))});"]
    for row in items:
        lines.append("INSERT INTO market_news_items (run_id, item_id, grp, market, tone, impact, title, translated, reason, "
                     "source, link, published_at) VALUES ("
                     + ", ".join([_q(run_id), _q(row["id"]), _q(row["group"]), _q(row["market"]), _q(row["tone"]),
                                  str(int(row["impact"])), _q(row["title"]), str(int(row["translated"])), _q(row["reason"]),
                                  _q(row["source"]), _q(row["link"]), _q(row["published_at"])]) + ");")
    for email, rows in user_rows.items():
        for row in rows:
            lines.append("INSERT OR REPLACE INTO market_news_user_items (email, run_id, code, link, tone, impact, title, "
                         "translated, reason) VALUES ("
                         + ", ".join([_q(email), _q(run_id), _q(row["code"]), _q(row["link"]), _q(row["tone"]),
                                      str(int(row["impact"])), _q(row["title"]), str(int(row["translated"])),
                                      _q(row["reason"])]) + ");")
    old = f"SELECT run_id FROM market_news_runs WHERE trade_date < {_q(cutoff)}"
    lines += [f"DELETE FROM market_news_items WHERE run_id IN ({old});",
              f"DELETE FROM market_news_user_items WHERE run_id IN ({old});",
              f"DELETE FROM market_news_runs WHERE trade_date < {_q(cutoff)};"]
    return "\n".join(lines) + "\n"


Asker = Callable[[str, Mapping[str, Any]], tuple[str, Any, str]]
FriendAsker = Callable[[Mapping[str, Any], str, Mapping[str, Any]], tuple[str, Any, str]]


def run(slot: str, *, trade_date: str, now: str, query: Callable[[str], list[dict]], execute: Callable[[str], None],
        get: Callable, sleep: Callable[[float], None], ask_owner: Asker, owner_email: str,
        friend_keys: Sequence[Mapping[str, Any]], ask_friend: FriendAsker, taiex: Callable[[], dict | None],
        model: str | None = None, dry_run: bool = False) -> dict[str, Any]:
    """一輪：抓市場來源 → 站主的模型一次（市場＋站主自選）→ 每位有金鑰的朋友一次（只有他的自選）→ 一份 SQL 寫回。"""
    if slot not in SLOTS:
        raise ValueError(f"slot 只能是 {SLOTS}")
    if not wn._EMAIL.fullmatch(owner_email or ""):
        raise ValueError("站主 email 格式不對")
    since = window_start(query, now)
    items, sources = collect(get=get, now=now, sleep=sleep, since=since)
    index_rows = indices(query, taiex)
    owner_watch = watch_rows(query, owner_email, now, since)
    from concurrent.futures import ThreadPoolExecutor
    batches = [items[start:start + BATCH_ITEMS] for start in range(0, len(items), BATCH_ITEMS)]
    jobs = [(f"items{index + 1}", items_prompt(batch), ITEMS_SCHEMA) for index, batch in enumerate(batches)]
    if owner_watch:
        jobs.append(("watch", watch_prompt(owner_watch), WATCH_SCHEMA))
    calls: dict[str, dict[str, Any]] = {}

    def call(job):
        name, text, schema = job
        try:
            status, output, note = ask_owner(text, schema)
        except Exception as error:  # noqa: BLE001 — 一批出錯只影響那一批
            status, output, note = type(error).__name__, None, str(error)[:200]
        return name, status, coerce_output(output), note

    with ThreadPoolExecutor(max_workers=PARALLEL_CALLS) as pool:
        results = list(pool.map(call, jobs))
    rejected: dict[str, int] = {}
    classified: list[dict[str, Any]] = []
    unmarked: list[Mapping[str, Any]] = []
    owner_marks: list[dict[str, Any]] = []
    for (name, status, output, note), batch in zip(results, [*batches, owner_watch]):
        record = {"status": status, **({"note": str(note)[:300]} if note and status != "ok" else {})}
        if name == "watch":
            owner_marks = validate_watch(output, owner_watch) if status == "ok" else []
            record["kept"] = len(owner_marks)
        else:
            kept = validate_items(output, batch, rejected) if status == "ok" else []
            record.update(returned=len(output.get("items") or []) if isinstance(output, Mapping) else 0, kept=len(kept))
            classified += kept
            if not kept:
                unmarked += batch
        calls[name] = record
    summary = None
    if classified:
        for attempt in (1, 2):   # 回成交代工作的話就再問一次；還是不行就不放總結
            status, output, note = call(("summary", summary_prompt(slot, trade_date, classified, index_rows), SUMMARY_SCHEMA))[1:]
            summary = (_text(output.get("market_commentary") or output.get("summary"), SUMMARY_MAX)
                       if status == "ok" and isinstance(output, Mapping) else None)
            if status != "ok" or is_summary(summary):
                break
            status, note, summary = "not_a_summary", summary, None
        calls["summary"] = {"status": status, "attempts": attempt,
                            **({"note": str(note)[:300]} if note and status != "ok" else {})}
    # 有判讀就套上；整批沒判讀成的那幾則照原標題存（不標利多利空、沒有理由）；一則都沒有才當「只有標題」。
    if classified:
        stored = _keep_per_group(classified + unclassified_items(unmarked))
        run_status = "ok"
        user_rows = {owner_email: owner_marks}
    else:
        stored, run_status, summary, user_rows = unclassified_items(items), "unclassified", None, {}
    status = "ok" if classified else next((r["status"] for r in calls.values() if r["status"] != "ok"), "no_items")
    note = ""
    diagnosis = {"calls": calls, "items_kept": len(classified), "rejected": rejected, "summary_chars": len(summary or "")}
    friends: dict[str, str] = {}
    for key_row in friend_keys:
        email = str(key_row.get("email") or "")
        if not wn._EMAIL.fullmatch(email) or email.lower() == owner_email.lower():
            continue
        watch = watch_rows(query, email, now, since)
        if not watch:
            friends[email] = "no_news"
            continue
        try:
            friend_status, friend_output, friend_note = ask_friend(key_row, watch_prompt(watch), WATCH_SCHEMA)
        except Exception as error:  # noqa: BLE001 — 一位朋友失敗不影響別人
            friend_status, friend_output, friend_note = type(error).__name__, None, ""
        friends[email] = friend_status if friend_status == "ok" or not friend_note else f"{friend_status} {friend_note}"
        friend_output = coerce_output(friend_output)
        if friend_status == "ok":
            user_rows[email] = validate_watch(friend_output, watch)
    run_row = {"run_id": f"{trade_date}-{slot}", "trade_date": trade_date, "slot": slot, "created_at": now,
               "status": run_status, "summary": summary, "indices": index_rows,
               "sources": {"feeds": sources, "missed_groups": missed_groups(sources)}, "model": model}
    script = run_sql(run_row, stored, user_rows, now=now)
    if not dry_run:
        execute(script)
    return {"status": "ok", "run_id": run_row["run_id"], "run_status": run_status, "items": len(stored),
            "sent": len(items), "model": status, **({"model_note": note[:200]} if status != "ok" and note else {}),
            "owner_watch_sent": len(owner_watch), "owner_watch": len(user_rows.get(owner_email, [])),
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
    """agy 的 result 事件 → (狀態, 輸出, 說明)。SUCCESS 但 structured_output 是空的：先試文字回覆裡的 JSON；
    還是沒有就回 no_structured_output，說明裡寫事件有哪些欄位與文字前 300 字（2026-11-19 Actions 的 agy 1.5.3）。"""
    if not envelope or envelope.get("status") != "SUCCESS":
        return "model_failed", None, str((envelope or {}).get("error") or "沒有 result 事件")[:300]
    structured = envelope.get("structured_output")
    if isinstance(structured, Mapping):
        return "ok", structured, ""
    if isinstance(structured, str) and json_from_text(structured) is not None:
        return "ok", json_from_text(structured), "structured_output 是字串"
    texts = [value for key, value in envelope.items() if isinstance(value, str) and key not in ("status", "type", "event")]
    for text in texts:
        parsed = json_from_text(text)
        if parsed is not None:
            return "ok", parsed, "從文字回覆解出 JSON"
    preview = next((text for text in texts if text.strip()), "")
    return "no_structured_output", None, f"keys={sorted(envelope)} text={preview[:300]!r}"


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


def _friend_asker(model: str) -> FriendAsker:
    import friend_ai_digest as fad

    def ask(key_row: Mapping[str, Any], text: str, schema: Mapping[str, Any]) -> tuple[str, Any, str]:
        try:
            key = fad.node_decrypt(dict(key_row))
        except Exception:  # noqa: BLE001
            return "key_decrypt_failed", None, ""
        post = lambda url, body, api_key: fad.http_post(url, body, api_key, timeout=FRIEND_TIMEOUT_SECONDS)  # noqa: E731
        try:
            output, _usage = fad.call_gemini(text, model, api_key=key, post=post, schema=dict(schema))
            return "ok", output, ""
        except fad.ModelCallFailed as error:
            detail = getattr(error, "detail", None)
            return getattr(error, "code", "gemini_failed"), None, json.dumps(detail, ensure_ascii=False)[:200] if detail else ""
    return ask


def is_trading_day(day: str, holidays: set[str] | None = None) -> bool:
    """週一到週五、而且不在證交所休市日曆裡（颱風假這種臨時休市不在日曆裡，那天照跑）。"""
    if datetime.fromisoformat(day).weekday() >= 5:
        return False
    if holidays is None:
        from level_signals import ensure_holiday_calendar   # 證交所官方休市日曆（專案帶種子、工作集有快取）
        holidays = ensure_holiday_calendar(day)
    return day not in holidays


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
    parser.add_argument("--skip-closed", action="store_true", help="台股休市日（證交所休市日曆、週末）不跑")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    trade_date = args.date or datetime.now(TAIPEI).date().isoformat()
    if args.skip_closed and not is_trading_day(trade_date):
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
    print(json.dumps({**report, "effort": effort}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
