"""首頁「消息」的瀏覽器旅程（本人 2026-11-20：消息要講「影響＋要看的條件＋準備」；.scratch/impact-watch 票 01）。

這是第一條真的把消息區塊畫出來的旅程。看的是：
- 總結與每一則有「影響／要看／準備」三段；影響最小的那一則只有影響；
- 點名站主自選股／庫存的那一句只有站主看得到，朋友的畫面與回應裡都沒有；
- 沒有新欄位的舊消息（切到前一天）照舊顯示，沒有空白段落；
- 「我的自選」的判讀也有三段；手機 390 與桌機 1440 都不會橫向捲動。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），資料是這裡種的假消息，
不連任何新聞來源、不叫模型、不碰正式站。先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/market-news-journey/。

    python cloud/test/browser_market_news_journey.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/market-news-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
MINE_ITEM = "台積電、聯發科是外資賣超的重心，短線壓力最大。"
MINE_SUMMARY = "你的台積電與聯發科都在被賣的權值股裡。"


def q(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def lines(*texts: str) -> str:
    return json.dumps(list(texts), ensure_ascii=False)


def seed_sql() -> str:
    """兩天各一輪：11-20 是新寫法（含一則影響最小、只有影響的），11-19 是舊資料（沒有新欄位）。全部是假消息。"""
    out = [
        f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', '朋友', 'approved', '2026-11-09T00:00:00Z');",
        "INSERT INTO user_watchlist (email, code, name, market, horizon, in_universe, created_at, updated_at, watching) VALUES "
        + ", ".join(f"('{email}', '{code}', '{name}', '上市', 'none', 1, '2026-11-10T00:00:00Z', '2026-11-10T00:00:00Z', 0)"
                    for email, code, name in ((OWNER, "2330", "台積電"), (OWNER, "2454", "聯發科"), (FRIEND, "2330", "台積電"))) + ";",
    ]
    indices = json.dumps([{"name": "加權", "change_pct": -1.82, "date": "2026-11-20"}, {"name": "費城半導體", "change_pct": -3.1, "date": "2026-11-19"},
                          {"name": "日經", "change_pct": -1.15, "date": "2026-11-20"}], ensure_ascii=False)
    out.append("INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status, summary, indices, sources, model, "
               "effect, owner_effect, conditions, prepare) VALUES "
               f"('2026-11-19-close', '2026-11-19', 'close', '2026-11-19T06:05:00Z', 'ok', '台股小漲，電子權值股撐盤，傳產走弱。', {q(indices)}, '{{}}', 'fake', "
               "NULL, NULL, NULL, NULL), "
               f"('2026-11-20-close', '2026-11-20', 'close', '2026-11-20T06:05:00Z', 'ok', "
               f"'台股今天下跌，主要是昨夜費半重挫、外資開盤就賣半導體權值股；金管會擴大限空令後跌勢才收斂。', {q(indices)}, '{{}}', 'fake', "
               f"'半導體與 AI 伺服器族群跌最多，權值股把指數往下拉；傳產與金融相對抗跌。', {q(MINE_SUMMARY)}, "
               f"{q(lines('外資是否連續賣超', '費半是否止跌'))}, {q(lines('如果外資連續賣超，就先檢查手上權值股佔多少', '如果費半止跌回升，就留意權值股有沒有跟著回穩'))});")
    items = [
        # (輪次, 編號, 組, 市場, 類別, 方向, 影響, 標題, 發生什麼, 影響, 點名, 條件, 準備)
        ("2026-11-20-close", "n1", "tw", "", "flows", "down", 3, "外資單日賣超台股逾 600 億元，集中在半導體權值股", "外資大賣半導體權值股。",
         "權值股被賣會直接壓低加權指數，電子股短線籌碼轉弱。", MINE_ITEM,
         lines("外資是否連續賣超", "台積電是否守住月線"), lines("如果外資連續賣超，就先檢查手上權值股佔多少")),
        ("2026-11-20-close", "n2", "tw", "", "policy", "up", 3, "金管會擴大限空令，千餘檔股票平盤下不得放空", "金管會擴大限空令。",
         "放空的賣壓變小，對跌深的中小型股支撐最明顯。", None, lines("金管會是否公布解除時間"), lines("如果限空令解除，就留意先前跌深的股票賣壓有沒有回來")),
        ("2026-11-20-close", "n3", "tw", "", "other", "flat", 1, "某公司舉辦年度供應商大會", "例行的供應商大會。", "對股價影響很小。", None, None, None),
        ("2026-11-20-close", "n4", "us", "", "earnings", "down", 3, "輝達財報前夕，晶片股遭獲利了結，費半重挫 3.1%", "美國晶片股財報前被獲利了結。",
         "台股電子權值股隔天容易跟著開低。", None, lines("輝達財測是否優於預期"), lines("如果輝達財測不如預期，就先想好能承受的跌幅")),
        ("2026-11-20-close", "n5", "asia", "韓", "macro", "up", 2, "韓國 11 月前 20 天出口年增 9%，晶片出口創高", "韓國晶片出口創高。",
         "記憶體需求仍強，對台灣記憶體族群偏正面。", None, lines("記憶體報價是否續漲"), None),
        ("2026-11-19-close", "n1", "tw", "", "other", "up", 2, "電子權值股撐盤，台股小漲", "台積電、鴻海走強。", None, None, None, None),
        ("2026-11-19-close", "n2", "us", "", "policy", "down", 2, "聯準會會議紀要：多數官員認為降息不宜過快", "降息預期往後延，壓抑成長股。", None, None, None, None),
    ]
    for run_id, item_id, grp, market, kind, tone, impact, title, reason, effect, owner_effect, conditions, prepare in items:
        when = run_id[:10] + "T05:00:00Z"
        out.append("INSERT INTO market_news_items (run_id, item_id, grp, market, kind, tone, impact, title, translated, reason, source, link, "
                   "published_at, effect, owner_effect, conditions, prepare) VALUES ("
                   + ", ".join([q(run_id), q(item_id), q(grp), q(market), q(kind), q(tone), str(impact), q(title), "0", q(reason), q("中央通訊社"),
                                q("https://example.com/" + item_id), q(when), q(effect), q(owner_effect), q(conditions), q(prepare)]) + ");")
    out.append("INSERT INTO watch_news (code, link, source, title, summary, published_at, fetched_at) VALUES "
               "('2330', 'https://news.google.com/rss/articles/j1', 'gnews-reuters', 'TSMC ADR falls 4% as chip stocks slide', '', '2026-11-19T22:40:00Z', '2026-11-20T06:00:00Z'), "
               "('2454', 'https://news.google.com/rss/articles/j2', 'gnews-cna', '聯發科法說會登場', '', '2026-11-18T04:15:00Z', '2026-11-19T06:00:00Z');")
    out.append("INSERT INTO stock_news_marks (code, link, relevant, kind, tone, impact, title, translated, reason, judged_by, judged_at, effect, conditions, prepare) VALUES "
               "('2330', 'https://news.google.com/rss/articles/j1', 1, 'rating', 'down', 3, '台積電 ADR 昨夜跌 4%，晶片股走弱', 1, '台積電 ADR 跟著費半下跌。', 'owner', '2026-11-20T06:05:00Z', "
               f"'今天開盤容易開低，短線股價承壓。', {q(lines('費半是否止跌'))}, {q(lines('如果費半續跌，就先想好能承受的跌幅'))}), "
               "('2454', 'https://news.google.com/rss/articles/j2', 1, 'guidance', 'up', 2, '聯發科法說會登場', 0, '法說會釋出展望。', 'owner', '2026-11-19T06:05:00Z', NULL, NULL, NULL);")
    return "\n".join(out)


STATE = """() => {
  const block = document.getElementById('marketNews');
  const dl = node => node ? [...node.querySelectorAll('dt')].map(dt => [dt.textContent, [...dt.nextElementSibling.querySelectorAll('p')].map(p => p.textContent)]) : null;
  const rect = block.getBoundingClientRect();
  return {
    state: block.dataset.state,
    meta: document.getElementById('marketNewsMeta').textContent,
    summary: document.querySelector('#marketNewsSummary .news-summary-text')?.textContent || null,
    summaryPoints: dl(document.querySelector('#marketNewsSummary .news-points')),
    items: [...document.querySelectorAll('#marketNewsList .news-item')].map(li => ({
      title: li.querySelector('.news-title').textContent, reason: li.querySelector('.news-reason')?.textContent || null,
      points: dl(li.querySelector('.news-points')), mine: [...li.querySelectorAll('.news-mine')].map(p => p.textContent),
    })),
    empty: [...block.querySelectorAll('p, dd, dt, dl')].filter(node => node.offsetParent && !node.textContent.trim()).length,
    text: block.innerText,
    noScroll: document.documentElement.scrollWidth <= innerWidth,
    inside: [...block.querySelectorAll('.news-points')].every(node => node.getBoundingClientRect().right <= rect.right + 1),
    labelSizes: [...new Set([...block.querySelectorAll('.news-points dd')].map(node => getComputedStyle(node).fontSize))],
  };
}"""


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console = base, [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def news(self, browser, email: str, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": self.base}])
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        self.bodies = []
        page.on("response", lambda r: self.bodies.append(r.text()) if "/api/market-news" in r.url and r.status == 200 else None)
        page.goto(f"{self.base}/minimal.html", wait_until="domcontentloaded")   # 不等外部字型（卡住時 load 不會來）
        page.locator("#newsCatButton").wait_for(state="visible", timeout=60_000)
        page.locator("#newsCatButton").click()
        page.wait_for_function("() => document.getElementById('marketNews').dataset.state === 'ready'", timeout=20_000)
        page.locator("#marketNews").scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        return context, page


def group(page, label: str) -> None:
    page.locator("#marketNewsGroups button", has_text=label).click()
    page.wait_for_timeout(300)


def day(page, text: str) -> None:
    page.locator("#marketNewsDateChip").click()
    page.locator("#marketNewsDates button", has_text=text).click()
    page.wait_for_function("(d) => document.getElementById('marketNewsMeta').textContent.startsWith(d)", arg=text, timeout=10_000)
    page.wait_for_timeout(400)


def run(j: Journey, browser, prefix: str, email: str, options: dict) -> None:
    owner = email == OWNER
    context, page = j.news(browser, email, **options)
    now = page.evaluate(STATE)
    j.check(f"{prefix}打開消息：最新一輪、有總結", now["meta"].startswith("11-20") and (now["summary"] or "").startswith("台股今天下跌"), now["meta"])
    labels = [label for label, _ in now["summaryPoints"] or []]
    j.check(f"{prefix}總結有影響、觀察、因應三段", labels == ["影響", "觀察", "因應"], labels)
    j.check(f"{prefix}總結的條件與準備各兩句、準備是「如果…就…」",
            bool(now["summaryPoints"]) and len(now["summaryPoints"][1][1]) == 2 and all(t.startswith("如果") and "就" in t for t in now["summaryPoints"][2][1]),
            now["summaryPoints"])
    # 同樣影響時政策排在法人前面，所以用標題找那一則
    first = next((item for item in now["items"] if item["title"].startswith("外資單日賣超")), {"title": "", "reason": None, "points": None, "mine": []})
    minor = now["items"][-1]
    j.check(f"{prefix}外資那一則：標題、發生什麼、三段", first["title"].startswith("外資單日賣超") and first["reason"] == "外資大賣半導體權值股。"
            and [label for label, _ in first["points"] or []] == ["影響", "觀察", "因應"], first)
    j.check(f"{prefix}影響最小的那一則只有影響一段", minor["title"].startswith("某公司") and [label for label, _ in minor["points"] or []] == ["影響"], minor)
    if owner:
        j.check(f"{prefix}站主：點名自選股的那一句在影響那一段裡", first["mine"] == [MINE_ITEM] and MINE_SUMMARY in now["summaryPoints"][0][1], first["mine"])
    else:
        j.check(f"{prefix}朋友：畫面上沒有點名站主股票的那一句", MINE_ITEM not in now["text"] and MINE_SUMMARY not in now["text"]
                and not any(item["mine"] for item in now["items"]))
        j.check(f"{prefix}朋友：回應裡也沒有（不是只藏起來）", bool(j.bodies) and all("owner_effect" not in body and "外資賣超的重心" not in body
                                                                      and "都在被賣的權值股裡" not in body for body in j.bodies), len(j.bodies))
        j.check(f"{prefix}朋友：影響、要看、準備照樣看得到", [label for label, _ in first["points"] or []] == ["影響", "觀察", "因應"]
                and len(first["points"][0][1]) == 1, first["points"])
    j.check(f"{prefix}沒有空白段落、不橫向捲動、三段不超出板", now["empty"] == 0 and now["noScroll"] and now["inside"], [now["empty"], now["noScroll"], now["inside"]])
    j.check(f"{prefix}不出現買賣指令字眼", not any(word in now["text"] for word in ("買進", "賣出", "加碼", "減碼", "停損")))
    page.locator("#marketNews").screenshot(path=str(OUT / f"{prefix}1-new-tw.png"))
    page.screenshot(path=str(OUT / f"{prefix}1-new-viewport.png"), full_page=False)

    group(page, "美股")
    us = page.evaluate(STATE)
    j.check(f"{prefix}美股那一組也是新寫法", len(us["items"]) == 1 and [label for label, _ in us["items"][0]["points"] or []] == ["影響", "觀察", "因應"], us["items"])
    group(page, "亞洲股市")
    asia = page.evaluate(STATE)
    j.check(f"{prefix}沒寫準備的那一則只畫影響與觀察", [label for label, _ in asia["items"][0]["points"] or []] == ["影響", "觀察"], asia["items"])
    group(page, "自選股")
    mine = page.evaluate(STATE)
    titles = [item["title"] for item in mine["items"]]
    marked = next((item for item in mine["items"] if item["title"].startswith("台積電 ADR")), None)
    j.check(f"{prefix}我的自選：判讀有三段", bool(marked) and [label for label, _ in marked["points"] or []] == ["影響", "觀察", "因應"], titles)
    if owner:
        old = next((item for item in mine["items"] if item["title"] == "聯發科法說會登場"), None)
        j.check(f"{prefix}我的自選：沒有新欄位的舊判讀照舊（標題＋一句）", bool(old) and old["points"] is None and old["reason"] == "法說會釋出展望。", old)
    j.check(f"{prefix}我的自選：沒有空白段落", mine["empty"] == 0, mine["empty"])
    page.locator("#marketNews").screenshot(path=str(OUT / f"{prefix}2-mine.png"))

    group(page, "台股")
    day(page, "11-19")
    old = page.evaluate(STATE)
    j.check(f"{prefix}舊消息（11-19）：總結照舊一段、沒有三段", old["summary"] == "台股小漲，電子權值股撐盤，傳產走弱。" and old["summaryPoints"] is None, old["summaryPoints"])
    j.check(f"{prefix}舊消息：每則照舊（標題＋一句），沒有空白段落", len(old["items"]) == 1 and old["items"][0]["points"] is None
            and old["items"][0]["reason"] == "台積電、鴻海走強。" and old["empty"] == 0, old["items"])
    page.locator("#marketNews").screenshot(path=str(OUT / f"{prefix}3-old.png"))
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed = OUT / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_MARKET_NEWS": "all"}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    j = Journey(base)
    try:
        for _ in range(100):
            try:
                urlopen(base, timeout=1)
                break
            except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
                if getattr(error, "code", None):
                    break
                time.sleep(0.2)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for prefix, options in [("desktop-1440-", {"viewport": {"width": 1440, "height": 900}}),
                                    ("mobile-390-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True})]:
                run(j, browser, prefix, OWNER, options)
                run(j, browser, f"{prefix}friend-", FRIEND, options)
            browser.close()
        errors = [text for text in j.console if "favicon" not in text]
        j.check("沒有程式錯誤", not errors, errors[:5])
    finally:
        server.terminate()
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
