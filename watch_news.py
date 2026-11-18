"""自選股關注 C 票：關注股的新聞標題，每天抓一次、寫進雲端資料庫，只給站主看（規格 .scratch/watchlist-watch/spec.md）。

來源與條款（2026-11-15 查證，見 ANALYSIS_PLAN.md 自選股關注那一節）：
- Yahoo股市個股新聞 RSS，依代號抓（上市 .TW、上櫃 .TWO）：個人非營利私人用途、須標示出處。
  按個股篩選的 ?s= 參數沒有官方文件，失效時那一檔標「沒抓到」，其餘照常。
- 中央通訊社產經證券、國際、科技 RSS，整批抓，以公司名稱與代號對到關注股；對不到的存成大環境消息（code 是 ''）。
  個人非商業、引用要標「中央通訊社」、保留發稿訊頭、不得引用全文。
每則只存標題、前言、連結、出處、發布時間、取得時間。本人 2026-11-19 00:1x「富果、新聞來源、國外同類股都交給 Gemini
做詳細分析」推翻 11-15「新聞不進 Gemini 的提示」：大盤預測與自選股判斷讀這裡的標題與前言（不給全文、不給連結，
licensed_inputs.headline_items）。
一天只抓一次（隨每日更新），遠低於各家的 ttl（Yahoo max-age 30 秒、中央社 no-cache），所以不另做條件請求：
條件請求拿到 304 時，新打開關注的股票就對不到中央社已經發過的新聞。來源回 429／503 時這一輪不再打它。
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
YAHOO_URL = "https://tw.stock.yahoo.com/rss?s={code}.{suffix}"
YAHOO_ALL = "https://tw.stock.yahoo.com/rss"   # 不篩個股的那一份：拿來比對篩選有沒有失效
SAME_FEED_MIN = 3   # 兩份至少這麼多則、而且一模一樣，才算同一份（只共用一兩則合報新聞不算）
CNA_FEEDS = {
    "cna-finance": "https://feeds.feedburner.com/rsscna/finance",
    "cna-intworld": "https://feeds.feedburner.com/rsscna/intworld",
    "cna-technology": "https://feeds.feedburner.com/rsscna/technology",
}
LINK_HOSTS = {"yahoo": {"tw.stock.yahoo.com"}, **{source: {"www.cna.com.tw"} for source in CNA_FEEDS}}
SLOW_DOWN = {429, 503}
YAHOO_PAUSE_SECONDS = 1.0
USER_AGENT = "Mozilla/5.0 (compatible; market-ana personal watchlist)"
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
        return parse(response.content, source=source), None, False
    except FormatChanged as error:
        return None, str(error), False
    except Exception as error:  # noqa: BLE001 — 一份失敗只記錄，不影響其他來源
        return None, f"{type(error).__name__}: {error}"[:200], False


def _mentions(text: str, stock: dict) -> bool:
    name = (stock.get("name") or "").strip()
    return (len(name) >= 2 and name in text) or re.search(rf"(?<!\d){re.escape(stock['code'])}(?!\d)", text) is not None


def gather(stocks: list[dict], *, get: Callable, now: str, sleep: Callable[[float], None]) -> dict:
    """抓 Yahoo（每檔一次）與中央社（三個分類各一次）。回傳要寫的新聞與每個來源這一次的結果。"""
    news: list[dict] = []
    fetches: list[dict] = []

    def record(source, code, error):
        fetches.append({"source": source, "code": code, "error": error})

    yahoo: dict[str, list[dict]] = {}
    slowed = False
    for index, stock in enumerate(stocks):
        if slowed:
            record("yahoo", stock["code"], "來源要求放慢，這一輪沒抓")
            continue
        if index:
            sleep(YAHOO_PAUSE_SECONDS)
        url = YAHOO_URL.format(code=stock["code"], suffix="TWO" if stock.get("market") == "上櫃" else "TW")
        items, error, slowed = _fetch(get, url, "yahoo")
        if items is None:
            record("yahoo", stock["code"], error)
        else:
            yahoo[stock["code"]] = items
    # 按個股篩選失效時 Yahoo 會給每一檔同一份大盤新聞：某一檔和不篩個股的那一份、或和另一檔一模一樣，
    # 就當成篩選失效，不存成個股新聞（只關注一檔時靠前者；那一份抓不到時退回只比各檔之間）。
    feeds = {code: frozenset(item["link"] for item in items) for code, items in yahoo.items()}
    if yahoo and not slowed:
        sleep(YAHOO_PAUSE_SECONDS)
        general, _, _ = _fetch(get, YAHOO_ALL, "yahoo")
        if general is not None:
            feeds[None] = frozenset(item["link"] for item in general)
    same = [feed for feed in feeds.values() if len(feed) >= SAME_FEED_MIN]
    for code, items in yahoo.items():
        if same.count(feeds[code]) > 1:
            record("yahoo", code, "按個股篩選失效：每一檔拿到同一份新聞")
            continue
        record("yahoo", code, None)
        news += [{**item, "source": "yahoo", "code": code} for item in items]

    for source, url in CNA_FEEDS.items():
        items, error, _ = _fetch(get, url, source)
        record(source, "", error)
        for item in items or []:
            text = f"{item['title']} {item['summary']}"
            codes = [stock["code"] for stock in stocks if _mentions(text, stock)] or [""]
            news += [{**item, "source": source, "code": code} for code in codes]
    return {"news": news, "fetches": fetches, "fetched_at": now}


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
    """讀站主正在關注的股票 → 抓 → 一次寫回。資料表還沒建立（變更檔未套）就什麼都不抓。"""
    if not _EMAIL.fullmatch(owner_email or ""):
        raise ValueError("站主 email 格式不對")
    import time
    get, sleep = get or _http_get, sleep or time.sleep
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tables = query("SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name IN ('watch_news', 'watch_news_fetches')")
    if int(tables[0]["n"]) < 2:
        return {"status": "not-migrated"}
    stocks = query("SELECT code, name, market FROM user_watchlist "
                   f"WHERE lower(email) = lower({_q(owner_email)}) AND watching = 1 ORDER BY watch_since, code")
    result = gather(stocks, get=get, now=now, sleep=sleep)
    execute(sql(result))
    after = int(query("SELECT COUNT(*) AS n FROM watch_news WHERE fetched_at = " + _q(now))[0]["n"])
    sources = {f"{f['source']}:{f['code']}" if f["code"] else f["source"]:
               {"status": "error" if f["error"] else "ok", **({"reason": f["error"]} if f["error"] else {})}
               for f in result["fetches"]}
    return {"status": "ok", "watched": len(stocks), "stored": after, "sources": sources}


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
    elif sys.argv[1:2] == ["probe"]:
        import time   # 用真的網路抓一次、不寫資料庫：python watch_news.py probe 2330:台積電:上市 6488:環球晶:上櫃
        stocks = [dict(zip(("code", "name", "market"), arg.split(":"))) for arg in sys.argv[2:]]
        result = gather(stocks, get=_http_get, now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        sleep=time.sleep)
        print(json.dumps({"fetches": result["fetches"], "news": result["news"]}, ensure_ascii=False, indent=1))
    else:
        print("用法：python watch_news.py daily --d1 ｜ python watch_news.py probe 代號:名稱:市場 ...", file=sys.stderr)
        sys.exit(2)
