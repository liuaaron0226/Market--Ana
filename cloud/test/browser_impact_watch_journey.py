"""盯項的瀏覽器旅程（本人 2026-11-20 選樣品 A：收在頁首那張板裡、和行情狀態同一行；.scratch/impact-watch 票 03）。

看的是：
- 都還在盯：頁首同一行寫「在盯 N 件」，沒有金色圓點；點了浮出清單，每項有條件、對象、「數字」、剩幾個交易日，
  點一項看準備與出處；關閉鈕、Esc、點面板外面都收得回去；
- 有結果：金色圓點＋等到的那件事變粗；清單第一項直接攤開結果（帶數字）與原本寫的準備；
- 沒有盯項、或開關關著：整塊不出現，頁首高度和圖表位置與有盯項時一樣（不多佔高度）；
- 開關只開站主時朋友看不到；開給全部時朋友看得到大盤的盯項、看不到只給站主的；
- 消息型（票 05）：清單裡小標是「AI 讀新聞」，點開有「由 AI 讀後續的新聞判斷，可能漏看或看錯。」；
  有結果的附依據的新聞（標題連到原文、另開分頁）；
- 手機 390 與桌機 1440 都不橫向捲動；淺色與深色各拍一張。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），盯項是這裡種的假資料，
不叫模型、不碰正式站。先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/impact-watch-journey/。

    python cloud/test/browser_impact_watch_journey.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/impact-watch-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
TODAY = datetime.now(timezone(timedelta(hours=8))).date()
RESULT_NOTE = "成立：外資連續賣超 9 天，11/19 賣超 912 億。"
FAILED_NOTE = "否定：11/19 外資買超 52 億，連續賣超沒有延續到 9 天。"
PREPARE = "如果外資連續賣超，就先檢查手上權值股佔多少"
AI_NOTE = "由 AI 讀後續的新聞判斷，可能漏看或看錯。"
NEWS_WAITING, NEWS_MET = "台積電法說會給出明年展望", "美方公布正式的出口管制清單"
NEWS_NOTE = "成立：美國商務部公布清單，納入多家晶片廠。"
NEWS_PROOF = {"title": "美國公布晶片出口管制清單，輝達在列", "link": "https://example.com/list", "source": "路透"}
WATCHING = [
    # (條件編號, 人話名稱, 模型的說明, 影響力, 剩幾個交易日)
    ("below_ma240", "加權指數收盤跌破年線", "年線是長線多空的分界，跌破代表修正還沒結束", 3, 18),
    ("above_ma20", "加權指數收盤站回月線", "站回月線才算短線止跌", 2, 14),
    ("below_low", "加權指數收盤跌破近 20 日低點（37,837）", None, 2, 9),
]


def q(value) -> str:
    return "NULL" if value is None else str(value) if isinstance(value, int) else "'" + str(value).replace("'", "''") + "'"


def item_sql(key, label, note, impact, left, *, status="watching", result_note=None, visibility="all", subject="market", prepare=PREPARE,
             kind="numeric", params=None) -> str:
    done = status in ("met", "failed")
    values = [f"{subject}:{key}:2026-11-18", "market_news", "2026-11-18", "2026-11-18-close#n1", subject, kind, key,
              json.dumps(params or {}, ensure_ascii=False), label, note,
              impact, json.dumps([prepare], ensure_ascii=False) if prepare else None, "2026-11-18", "2026-12-16", None if done else left, status,
              TODAY.isoformat() if done else None, result_note, (TODAY + timedelta(days=5)).isoformat() if done else None, visibility,
              "2026-11-18T06:05:00Z"]
    return ("INSERT INTO impact_watch_items (id, source_block, source_date, source_ref, subject, kind, condition_key, params, label, note, impact, "
            "prepare, created_date, expires_on, days_left, status, result_date, result_note, show_until, visibility, created_at) VALUES ("
            + ", ".join(q(value) for value in values) + ");")


def seed_sql(scenario: str) -> str:
    out = [f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', '朋友', 'approved', '2026-11-09T00:00:00Z');"]
    if scenario == "none":
        return "\n".join(out)
    out += [item_sql(*row) for row in WATCHING]
    out.append(item_sql("stock_2330", "台積電收盤站上月線", None, 1, 6, visibility="owner", subject="2330"))
    if scenario == "result":
        out.append(item_sql("foreign_sell_streak", "外資連續賣超達 9 天", "賣超延續才是真的轉弱", 3, None, status="met", result_note=RESULT_NOTE))
    if scenario == "news":
        out.append(item_sql("news:aaa", NEWS_WAITING, None, 2, 12, kind="news", params={"keywords": ["台積電", "法說會"], "since": "2026-11-18T06:05:00Z"},
                            prepare="如果展望下修，就先檢查半導體佔持股多少"))
        out.append(item_sql("news:bbb", NEWS_MET, None, 3, None, kind="news", status="met", result_note=NEWS_NOTE,
                            params={"keywords": ["出口管制", "清單"], "since": "2026-11-18T06:05:00Z", "evidence": NEWS_PROOF},
                            prepare="如果清單比預期嚴，就先檢查半導體佔持股多少"))
    if scenario == "failed":
        out.append(item_sql("foreign_sell_streak", "外資連續賣超達 9 天", None, 3, None, status="failed", result_note=FAILED_NOTE))
    return "\n".join(out)


STATE = """() => {
  const key = document.getElementById('impactWatchKey');
  const dialog = document.getElementById('impactWatchDialog');
  const header = document.querySelector('.market-header').getBoundingClientRect();
  const box = key.getBoundingClientRect();
  const visible = node => Boolean(node && node.offsetParent !== null && getComputedStyle(node).display !== 'none');
  const count = key.querySelector('.iw-count');
  return {
    shown: visible(key), text: key.innerText.replace(/\\s+/g, ' ').trim(), label: key.getAttribute('aria-label'),
    news: key.classList.contains('is-news'), dot: Boolean(key.querySelector('.iw-dot')),
    bold: key.querySelector('.iw-text') ? getComputedStyle(key.querySelector('.iw-text')).fontWeight : null,
    countShown: visible(count),
    sameRow: visible(key) ? Math.abs((box.top + box.bottom) / 2 - (document.getElementById('statusTrigger').getBoundingClientRect().top
      + document.getElementById('statusTrigger').getBoundingClientRect().bottom) / 2) < 6 : null,
    insideHeader: visible(key) ? box.left >= header.left - 1 && box.right <= header.right + 1 : null,
    headerHeight: Math.round(header.height), chartTop: Math.round(document.getElementById('chartStage').getBoundingClientRect().top + scrollY),
    open: dialog.open,
    items: [...dialog.querySelectorAll('.iw-item')].map(node => ({
      cond: node.querySelector('.iw-cond').textContent, left: node.querySelector('.iw-left')?.textContent || null,
      meta: [...node.querySelectorAll('.iw-meta span')].map(span => span.textContent), open: node.open,
      done: node.classList.contains('is-done'), result: node.querySelector('.iw-result')?.textContent || null,
      body: node.open ? [...node.querySelectorAll('.iw-body p')].map(p => p.innerText.replace(/\\s+/g, ' ').trim()) : null,
      links: [...node.querySelectorAll('.iw-body a')].map(a => ({ text: a.textContent, href: a.getAttribute('href'), target: a.target, rel: a.rel })),
    })),
    dialogInside: !dialog.open || (dialog.getBoundingClientRect().left >= 0 && dialog.getBoundingClientRect().right <= innerWidth),
    noScroll: document.documentElement.scrollWidth <= innerWidth,
  };
}"""


class Journey:
    def __init__(self) -> None:
        self.results, self.console, self.metrics = [], [], {}

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def home(self, browser, base: str, email: str, options: dict):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": base}])
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        asked = []
        page.on("request", lambda r: asked.append(r.url) if "/api/impact-watch" in r.url else None)
        # 不等整頁 load：外部字型（Noto Serif TC）下載卡住時 load 永遠不來（2026-11-23 本機連外不順，三次都逾時）；
        # 下面本來就等報價出現。
        page.goto(f"{base}/minimal.html", wait_until="domcontentloaded")
        page.locator("#currentPrice").wait_for(state="visible", timeout=60_000)
        page.wait_for_function("() => document.getElementById('currentPrice').textContent.trim() !== '—'", timeout=60_000)
        page.wait_for_timeout(1500)   # 開關與盯項各一次請求
        return context, page, asked


def serve(scenario: str, mode: str | None):
    OUT.mkdir(parents=True, exist_ok=True)
    seed = OUT / f"seed-{scenario}.sql"
    seed.write_text(seed_sql(scenario), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed)}
    env.pop("MARKET_ANA_IMPACT_WATCH", None)
    if mode:
        env["MARKET_ANA_IMPACT_WATCH"] = mode
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urlopen(base, timeout=1)
            break
        except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
    return server, base


def watching(j: Journey, browser, base: str, prefix: str, options: dict, mobile: bool) -> None:
    context, page, _ = j.home(browser, base, OWNER, options)
    now = page.evaluate(STATE)
    j.metrics[prefix + "watch"] = (now["headerHeight"], now["chartTop"])
    j.check(f"{prefix}在盯：頁首同一行寫「在盯 4 件」", now["shown"] and now["text"] == "在盯 4 件 ›" and now["sameRow"] and now["insideHeader"], now)
    j.check(f"{prefix}在盯：沒有金色圓點、不變粗", not now["news"] and not now["dot"], now)
    j.check(f"{prefix}在盯：報讀文字講得出件數", now["label"] == "你在等的事：在盯 4 件", now["label"])
    j.check(f"{prefix}在盯：不橫向捲動", now["noScroll"])
    page.locator(".market-header").screenshot(path=str(OUT / f"{prefix}1-watch-header.png"))
    page.screenshot(path=str(OUT / f"{prefix}1-watch.png"))

    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(700)
    opened = page.evaluate(STATE)
    j.check(f"{prefix}點了浮出清單", opened["open"] and opened["dialogInside"], opened["open"])
    conds = [item["cond"] for item in opened["items"]]
    j.check(f"{prefix}清單依影響力排：四項", conds == ["加權指數收盤跌破年線", "加權指數收盤站回月線", "加權指數收盤跌破近 20 日低點（37,837）", "台積電收盤站上月線"], conds)
    first = opened["items"][0]
    j.check(f"{prefix}每項有對象、「數字」、剩幾個交易日", first["meta"] == ["大盤", "數字"] and first["left"] == "剩 18 日"
            and opened["items"][3]["meta"] == ["2330", "數字"], first)
    j.check(f"{prefix}還在盯的先收著", not any(item["open"] for item in opened["items"]))
    page.screenshot(path=str(OUT / f"{prefix}2-watch-open.png"))
    page.locator("#impactWatchList .iw-item summary").first.click()
    page.wait_for_timeout(400)
    body = page.evaluate(STATE)["items"][0]["body"]
    j.check(f"{prefix}點一項：說明、先做好的準備、出處", body == ["年線是長線多空的分界，跌破代表修正還沒結束", f"先做好的準備 {PREPARE}", "出自 11/18 消息"], body)
    page.screenshot(path=str(OUT / f"{prefix}3-watch-item.png"))

    page.locator("#impactWatchDialog [data-dialog-close]").click()
    page.wait_for_timeout(300)
    j.check(f"{prefix}關閉鈕收得回去", not page.evaluate(STATE)["open"])
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(500)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    j.check(f"{prefix}Esc 收得回去", not page.evaluate(STATE)["open"])
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(500)
    size = page.viewport_size
    page.mouse.click(size["width"] - 4, size["height"] - 4)
    page.wait_for_timeout(300)
    after = page.evaluate(STATE)
    j.check(f"{prefix}點面板外面收得回去，頁面沒有被鎖住", not after["open"] and page.evaluate("() => getComputedStyle(document.body).position !== 'fixed'"), after["open"])

    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(700)
    page.screenshot(path=str(OUT / f"{prefix}4-watch-open-dark.png"))
    context.close()

    context, page, _ = j.home(browser, base, FRIEND, options)
    friend = page.evaluate(STATE)
    j.check(f"{prefix}朋友（開給全部）：看得到大盤的三件，沒有只給站主的那一件", friend["shown"] and friend["text"] == "在盯 3 件 ›"
            and "台積電" not in page.locator("#impactWatchDialog").inner_html(), friend["text"])
    context.close()


def result(j: Journey, browser, base: str, prefix: str, options: dict, mobile: bool) -> None:
    context, page, _ = j.home(browser, base, OWNER, options)
    now = page.evaluate(STATE)
    j.metrics[prefix + "result"] = (now["headerHeight"], now["chartTop"])
    j.check(f"{prefix}有結果：金色圓點＋等到的那件事變粗", now["shown"] and now["news"] and now["dot"] and now["text"].startswith("外資連續賣超達 9 天")
            and int(now["bold"] or 0) >= 600 and now["sameRow"] and now["insideHeader"], now)
    j.check(f"{prefix}有結果：{'手機不寫還盯幾件' if mobile else '後面寫還盯 4 件'}", now["countShown"] != mobile and (mobile or "還盯 4 件" in now["text"]), now["text"])
    j.check(f"{prefix}有結果：報讀文字", now["label"] == "你在等的事：外資連續賣超達 9 天有結果，還盯 4 件", now["label"])
    j.check(f"{prefix}有結果：不橫向捲動", now["noScroll"])
    page.locator(".market-header").screenshot(path=str(OUT / f"{prefix}5-result-header.png"))
    page.screenshot(path=str(OUT / f"{prefix}5-result.png"))
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(700)
    opened = page.evaluate(STATE)
    first = opened["items"][0]
    j.check(f"{prefix}有結果的排第一、直接攤開：結果帶數字", first["cond"] == "外資連續賣超達 9 天" and first["done"] and first["open"]
            and first["left"] == "有結果" and first["result"] == RESULT_NOTE, first)
    j.check(f"{prefix}攤開的是原本寫的準備與出處", first["body"] == ["賣超延續才是真的轉弱", f"原本寫的準備 {PREPARE}", "出自 11/18 消息"], first["body"])
    j.check(f"{prefix}其餘還在盯的照舊收著", len(opened["items"]) == 5 and not any(item["open"] for item in opened["items"][1:]))
    j.check(f"{prefix}清單不超出畫面", opened["dialogInside"] and opened["noScroll"])
    j.check(f"{prefix}不出現買賣指令字眼", not any(word in page.locator("#impactWatchDialog").inner_text() for word in ("買進", "賣出", "加碼", "減碼", "停損")))
    page.screenshot(path=str(OUT / f"{prefix}6-result-open.png"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"{prefix}7-result-open-dark.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    page.locator(".market-header").screenshot(path=str(OUT / f"{prefix}8-result-header-dark.png"))
    context.close()


def missed(j: Journey, browser, base: str, prefix: str, options: dict) -> None:
    """條件被否定：頁首那一行要講明「落空」，不能只寫條件（會被當成發生了）。"""
    context, page, _ = j.home(browser, base, OWNER, options)
    now = page.evaluate(STATE)
    j.check(f"{prefix}否定：頁首寫「落空：」＋那件事，照樣金色圓點", now["shown"] and now["dot"] and now["text"].startswith("落空：外資連續賣超達 9 天")
            and now["sameRow"] and now["insideHeader"] and now["noScroll"], now)
    j.check(f"{prefix}否定：報讀文字講落空", now["label"] == "你在等的事：外資連續賣超達 9 天落空，還盯 4 件", now["label"])
    page.locator(".market-header").screenshot(path=str(OUT / f"{prefix}10-failed-header.png"))
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(700)
    first = page.evaluate(STATE)["items"][0]
    j.check(f"{prefix}否定：清單那一項標「落空」、攤開結果", first["left"] == "落空" and first["open"] and first["result"] == FAILED_NOTE, first)
    page.screenshot(path=str(OUT / f"{prefix}10-failed-open.png"))
    context.close()


def news_type(j: Journey, browser, base: str, prefix: str, options: dict) -> None:
    """消息型：小標「AI 讀新聞」、點開有那句提醒；有結果的附依據的新聞。"""
    context, page, _ = j.home(browser, base, OWNER, options)
    now = page.evaluate(STATE)
    j.check(f"{prefix}消息型有結果：頁首照樣金色圓點＋等到的那件事", now["shown"] and now["dot"] and now["text"].startswith(NEWS_MET)
            and now["sameRow"] and now["insideHeader"] and now["noScroll"], now)
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(700)
    opened = page.evaluate(STATE)
    items = {item["cond"]: item for item in opened["items"]}
    met, waiting = items[NEWS_MET], items[NEWS_WAITING]
    j.check(f"{prefix}消息型：小標是「AI 讀新聞」，數字型照舊是「數字」", met["meta"] == ["大盤", "AI 讀新聞"] and waiting["meta"] == ["大盤", "AI 讀新聞"]
            and items["加權指數收盤跌破年線"]["meta"] == ["大盤", "數字"], [met["meta"], waiting["meta"]])
    j.check(f"{prefix}消息型有結果：攤開結果與依據的新聞、原本寫的準備、提醒、出處", met["open"] and met["result"] == NEWS_NOTE and met["body"] == [
        f"依據的新聞 {NEWS_PROOF['title']}路透", "原本寫的準備 如果清單比預期嚴，就先檢查半導體佔持股多少", AI_NOTE, "出自 11/18 消息"], met["body"])
    j.check(f"{prefix}依據的新聞連到原文、另開分頁", met["links"] == [{"text": NEWS_PROOF["title"], "href": NEWS_PROOF["link"], "target": "_blank",
                                                                 "rel": "noopener noreferrer"}], met["links"])
    j.check(f"{prefix}清單不超出畫面", opened["dialogInside"] and opened["noScroll"])
    page.screenshot(path=str(OUT / f"{prefix}11-news-result-open.png"))
    page.locator("#impactWatchList .iw-item", has_text=NEWS_WAITING).locator("summary").click()
    page.wait_for_timeout(400)
    body = {item["cond"]: item for item in page.evaluate(STATE)["items"]}[NEWS_WAITING]
    j.check(f"{prefix}消息型還在盯：點開有準備、那句提醒與出處，沒有連結", body["body"] == [
        "先做好的準備 如果展望下修，就先檢查半導體佔持股多少", AI_NOTE, "出自 11/18 消息"] and body["links"] == [] and body["left"] == "剩 12 日", body)
    page.screenshot(path=str(OUT / f"{prefix}12-news-waiting-open.png"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"{prefix}13-news-open-dark.png"))
    context.close()


def deep_link(j: Journey, browser, base: str, prefix: str, options: dict) -> None:
    """網址帶 #impact-watch：進首頁就打開清單（之後的推播點進來用）。"""
    context = browser.new_context(**options)
    context.add_cookies([{"name": "test_as", "value": OWNER, "url": base}])
    page = context.new_page()
    page.goto(f"{base}/minimal.html#impact-watch", wait_until="domcontentloaded")
    try:
        page.wait_for_function("() => document.getElementById('impactWatchDialog').open", timeout=30_000)
        opened = True
    except Exception:  # noqa: BLE001
        opened = False
    j.check(f"{prefix}網址帶 #impact-watch：直接打開清單", opened and len(page.evaluate(STATE)["items"]) == 5)
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    j.check(f"{prefix}網址帶 #impact-watch：關得掉、頁面沒有被鎖住",
            not page.evaluate(STATE)["open"] and page.evaluate("() => getComputedStyle(document.body).position !== 'fixed'"))
    # 這一頁已經開著時點推播（票 04）：瀏覽器只改錨點、不重新載入
    page.evaluate("() => { history.replaceState(null, '', location.pathname); location.hash = '#impact-watch'; }")
    try:
        page.wait_for_function("() => document.getElementById('impactWatchDialog').open", timeout=5_000)
        reopened = True
    except Exception:  # noqa: BLE001
        reopened = False
    j.check(f"{prefix}頁面開著時錨點變成 #impact-watch：也打開清單", reopened)
    context.close()


def absent(j: Journey, browser, base: str, prefix: str, options: dict, name: str, label: str, email: str = OWNER, asks: bool = True) -> None:
    context, page, asked = j.home(browser, base, email, options)
    now = page.evaluate(STATE)
    j.metrics[prefix + name] = (now["headerHeight"], now["chartTop"])
    j.check(f"{prefix}{label}：整塊不出現", not now["shown"] and now["text"] == "" and now["noScroll"], now)
    if not asks:
        j.check(f"{prefix}{label}：連問都不問", not asked, asked)
    if name == "none":
        page.locator(".market-header").screenshot(path=str(OUT / f"{prefix}9-none-header.png"))
    context.close()


def main() -> int:
    j = Journey()
    sizes = [("desktop-1440-", {"viewport": {"width": 1440, "height": 900}}, False),
             ("mobile-390-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}, True)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for scenario, mode in (("watch", "all"), ("result", "all"), ("failed", "all"), ("news", "all"), ("none", "all"), ("off", None),
                               ("owner", "owner")):
            server, base = serve(scenario if scenario in ("result", "failed", "news", "none") else "watch", mode)
            try:
                for prefix, options, mobile in sizes:
                    if scenario == "watch":
                        watching(j, browser, base, prefix, options, mobile)
                    elif scenario == "result":
                        result(j, browser, base, prefix, options, mobile)
                        deep_link(j, browser, base, prefix, options)
                    elif scenario == "failed":
                        missed(j, browser, base, prefix, options)
                    elif scenario == "news":
                        news_type(j, browser, base, prefix, options)
                    elif scenario == "none":
                        absent(j, browser, base, prefix, options, "none", "沒有盯項")
                    elif scenario == "off":
                        absent(j, browser, base, prefix, options, "off", "開關關著（資料庫裡有盯項）", asks=False)
                    else:
                        absent(j, browser, base, prefix, options, "friend", "開關只開站主時的朋友", email=FRIEND, asks=False)
                        context, page, _ = j.home(browser, base, OWNER, options)
                        j.check(f"{prefix}開關只開站主：站主看得到", page.evaluate(STATE)["text"] == "在盯 4 件 ›")
                        context.close()
            finally:
                server.terminate()
        browser.close()
    for prefix, _, _ in sizes:
        heights = {name: j.metrics.get(prefix + name) for name in ("watch", "result", "none", "off")}
        j.check(f"{prefix}不多佔高度：頁首高度與圖表位置四種狀態都一樣", len(set(heights.values())) == 1 and None not in heights.values(), heights)
    errors = [text for text in j.console if "favicon" not in text]
    j.check("沒有程式錯誤", not errors, errors[:5])
    (OUT / "heights.json").write_text(json.dumps(j.metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
