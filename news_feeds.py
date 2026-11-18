"""J 票：更多新聞來源（本人 2026-11-19「還有很多新聞來源，我記得有幾個網站是專門在整理不同產業的消息，另外還要加入
美國的前幾大報社，他們的新聞都很即時。」；本人同意新增這些對外抓取）。

寫預測、寫判斷之前直接抓官方 RSS，不另外存：抓到的就是寫的那一刻已經公布的；只拿標題、摘要（RSS 本身給的）與時間，
不抓全文、不跟連結。每個來源一次、設逾時、失敗不重試，那個來源就記缺。重播（舊日期）不抓。

- 大盤事前預測：美國的 CNBC 市場、紐約時報商業、華爾街日報市場（MARKET_FEEDS）。
- 自選股判斷：台灣的經濟日報產業、工商時報產業、鉅亨網台股（INDUSTRY_FEEDS），依公司名稱、代號與官方產業別對到關注股。
- 跳過（2026-11-19 實測）：美聯社商業（apnews.com 回 403）、路透（公開 RSS 已停，回 404）。
各家條款多半限個人、非商業使用；付費牆的（紐約時報、華爾街日報）只用 RSS 公開給的標題與摘要。
"""
from __future__ import annotations

import html
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

TAIPEI = timezone(timedelta(hours=8))
TIMEOUT_SECONDS = 15
PAUSE_SECONDS = 1.0
SUMMARY_MAX = 200
USER_AGENT = "Mozilla/5.0 (compatible; market-ana personal research)"

FEEDS: dict[str, dict[str, str]] = {
    "cnbc-markets": {"url": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "label": "CNBC 市場",
                     "region": "us"},
    "nyt-business": {"url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "label": "紐約時報 商業",
                     "region": "us"},
    "wsj-markets": {"url": "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "label": "華爾街日報 市場",
                    "region": "us"},
    "udn-industry": {"url": "https://money.udn.com/rssfeed/news/1001/5591?ch=money", "label": "經濟日報 產業",
                     "region": "tw"},
    "ctee-industry": {"url": "https://www.ctee.com.tw/rss_web/livenews/industry", "label": "工商時報 產業",
                      "region": "tw"},
    "cnyes-tw-stock": {"url": "https://news.cnyes.com/rss/v1/news/category/tw_stock", "label": "鉅亨網 台股",
                       "region": "tw"},
}
MARKET_FEEDS = ("cnbc-markets", "nyt-business", "wsj-markets")
INDUSTRY_FEEDS = ("udn-industry", "ctee-industry", "cnyes-tw-stock")
PARAMETERS: dict[str, Any] = {"feeds": {name: FEEDS[name]["url"] for name in FEEDS}, "market": list(MARKET_FEEDS),
                              "industry": list(INDUSTRY_FEEDS), "summary_max": SUMMARY_MAX,
                              "match": ["company_name_or_code", "official_industry_keyword"]}

_TAGS = re.compile(r"<[^>]+>")


def _plain(text: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAGS.sub(" ", text or ""))).strip()


def _utc(text: str | None) -> str | None:
    """RSS 的時間 → UTC（…Z）。RFC 822（pubDate）或 ISO；沒有時區的當成台北時間（工商時報那樣）。"""
    if not text:
        return None
    text = text.strip()
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TAIPEI)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_feed(content: bytes, source: str) -> list[dict[str, Any]]:
    root = ET.fromstring(content)
    rows = []
    for item in root.iter("item"):
        title = _plain(item.findtext("title"))
        published = _utc(item.findtext("pubDate"))
        if not title or not published:
            continue
        rows.append({"source": source, "link": (item.findtext("link") or "").strip(), "title": title,
                     "summary": _plain(item.findtext("description"))[:SUMMARY_MAX], "published_at": published})
    return rows


def fetch(names: Sequence[str], *, now: str, opener: Callable[..., Any] = urllib.request.urlopen,
          pause: Callable[[float], None] = time.sleep) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """每個來源抓一次（之間停一下）；失敗只記那個來源的錯誤種類，不重試。``fetched_at`` 是這一次抓的時間。"""
    fetched = datetime.fromisoformat(now).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for position, name in enumerate(names):
        if position:
            pause(PAUSE_SECONDS)
        request = urllib.request.Request(FEEDS[name]["url"], headers={
            "User-Agent": USER_AGENT, "Accept": "application/rss+xml,application/xml;q=0.9,*/*;q=0.1"})
        try:
            with opener(request, timeout=TIMEOUT_SECONDS) as response:
                rows.extend({**row, "fetched_at": fetched} for row in parse_feed(response.read(), name))
        except Exception as error:  # noqa: BLE001 — 一個來源壞掉只讓那個來源缺
            errors[name] = type(error).__name__
    return rows, errors


def industry_keywords(industry: str | None) -> list[str]:
    """官方產業別 → 比對用的詞：去掉結尾的「工業」「業」；四個字的再拆兩半、有「及」的拆開；「其他…」太籠統不比。"""
    word = re.sub(r"(工業|業)$", "", str(industry or "").strip())
    if not word or word.startswith("其他") or len(word) < 2:
        return []
    out = [word]
    if "及" in word:
        out += [part for part in word.split("及") if len(part) >= 2]
    elif len(word) == 4:
        out += [word[:2], word[2:]]
    return out


def match_stock(rows: Sequence[Mapping[str, Any]], *, code: str, name: str | None,
                industry: str | None) -> list[dict[str, Any]]:
    """對到這一檔的新聞：標題或摘要提到公司名稱或代號的算 company，提到官方產業別的算 industry；company 在前、新到舊。"""
    keywords = industry_keywords(industry)
    company, sector = [], []
    for row in rows:
        text = f"{row.get('title', '')} {row.get('summary', '')}"
        if (name and len(name) >= 2 and name in text) or re.search(rf"(?<!\d){re.escape(code)}(?!\d)", text):
            company.append({**row, "match": "company"})
        elif any(keyword in text for keyword in keywords):
            sector.append({**row, "match": "industry"})
    newest = lambda items: sorted(items, key=lambda row: row["published_at"], reverse=True)  # noqa: E731
    return newest(company) + newest(sector)
