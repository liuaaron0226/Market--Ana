"""站主自選股與庫存的盯項：瀏覽器旅程（本人 2026-11-20；.scratch/impact-watch 票 06）。

看的是：
- 首頁「你在等的事」：個股盯項的對象寫股票名稱與代號，點了進個股頁；頁首那一行有結果時前面帶股票名稱；
- 個股頁：報價下面列這一檔的盯項（都先收著，有結果的那一句收著也看得到），點開看說明、準備與出處；沒有盯項的股票整塊不出現；
- 朋友（開給全部）：首頁清單沒有任何個股盯項，個股頁那一塊不出現，整頁找不到站主那幾筆的文字；
- 開關關著：個股頁連問都不問；
- 手機 390 與桌機 1440 都不橫向捲動；淺色與深色各拍一張。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），盯項是這裡種的假資料，
不叫模型、不碰正式站。先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/impact-stock-journey/。

    python cloud/test/browser_impact_stock_journey.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import browser_impact_watch_journey as base_journey  # noqa: E402 — 同一套假資料的寫法與首頁的讀法

ROOT = base_journey.ROOT
OUT = ROOT / ".tmp/impact-stock-journey"
OWNER, FRIEND, TODAY = base_journey.OWNER, base_journey.FRIEND, base_journey.TODAY
q = base_journey.q
NOTE = "法說後賣壓還沒消化完"
PREPARE = "如果跌破月線，就先確認能承受的跌幅"
RESULT = "成立：外資連續賣超 4 天，11/19 賣超 8,759 張。"
SECRETS = ("收盤跌破月線", "外資連續賣超達 4 天", "收盤站上壓力", NOTE, PREPARE, RESULT)


def stock_item(code, name, key, label, left, *, impact=2, status="watching", result_note=None, note=NOTE) -> str:
    done = status in ("met", "failed")
    values = [f"{code}:{key}:2026-11-18", "market_news", "2026-11-18", f"2026-11-18-close#stock:{code}", code, "numeric", key,
              json.dumps({"name": name, "as_of": "2026-11-18"}, ensure_ascii=False), label, note, impact,
              json.dumps([PREPARE], ensure_ascii=False), "2026-11-18", "2026-12-16", None if done else left, status,
              TODAY.isoformat() if done else None, result_note, (TODAY + timedelta(days=5)).isoformat() if done else None, "owner",
              "2026-11-18T06:05:00Z"]
    return ("INSERT INTO impact_watch_items (id, source_block, source_date, source_ref, subject, kind, condition_key, params, label, note, impact, "
            "prepare, created_date, expires_on, days_left, status, result_date, result_note, show_until, visibility, created_at) VALUES ("
            + ", ".join(q(value) for value in values) + ");")


def seed_sql() -> str:
    return "\n".join([
        f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', '朋友', 'approved', '2026-11-09T00:00:00Z');",
        *[base_journey.item_sql(*row) for row in base_journey.WATCHING],
        stock_item("2330", "台積電", "below_ma20", "收盤跌破月線", 14, impact=3),
        stock_item("2330", "台積電", "foreign_sell_streak", "外資連續賣超達 4 天", None, status="met", result_note=RESULT, note=None),
        stock_item("2454", "聯發科", "above_resist", "收盤站上壓力（3,785）", 9),
    ])


def serve(mode: str | None):
    OUT.mkdir(parents=True, exist_ok=True)
    seed = OUT / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
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


HOME_ROWS = """() => [...document.querySelectorAll('#impactWatchList .iw-item')].map(node => ({
  cond: node.querySelector('.iw-cond').textContent,
  subject: node.querySelector('.iw-meta')?.firstElementChild?.textContent.replace(/\\s+/g, ' ').trim() || null,
  href: node.querySelector('.iw-meta a.iw-stock')?.getAttribute('href') || null,
}))"""

STOCK = """() => {
  const box = document.getElementById('stockWatch');
  const visible = node => Boolean(node && node.offsetParent !== null && getComputedStyle(node).display !== 'none');
  const quote = document.querySelector('.bd-quote').getBoundingClientRect();
  const rect = box.getBoundingClientRect();
  return {
    shown: visible(box), height: Math.round(rect.height),
    belowQuote: rect.top >= quote.bottom - 1, insideBoard: rect.left >= 0 && rect.right <= innerWidth,
    rows: [...box.querySelectorAll('.iw-item')].map(node => ({
      cond: node.querySelector('.iw-cond').textContent, tag: node.querySelector('.iw-cond .iw-kind')?.textContent || null,
      left: node.querySelector('.iw-left')?.textContent || null, open: node.open, done: node.classList.contains('is-done'),
      result: node.querySelector('.iw-result')?.textContent || null, meta: node.querySelector('.iw-meta')?.textContent || null,
      body: node.open ? [...node.querySelectorAll('.iw-body p')].map(p => p.innerText.replace(/\\s+/g, ' ').trim()) : null,
    })),
    noScroll: document.documentElement.scrollWidth <= innerWidth,
  };
}"""


def open_stock(page, base: str, code: str) -> None:
    page.goto(f"{base}/stock.html?code={code}")
    page.locator("#content").wait_for(state="visible", timeout=60_000)
    page.wait_for_timeout(1500)   # 開關與盯項各一次請求


def owner(j, browser, base: str, prefix: str, options: dict, mobile: bool) -> None:
    context, page, _ = j.home(browser, base, OWNER, options)
    now = page.evaluate(base_journey.STATE)
    j.check(f"{prefix}首頁那一行：有結果的個股盯項前面帶股票名稱", now["shown"] and now["dot"] and now["text"].startswith("台積電 外資連續賣超達 4 天")
            and now["sameRow"] and now["insideHeader"] and now["noScroll"], now)
    j.check(f"{prefix}首頁那一行：報讀文字帶股票名稱", now["label"] == "你在等的事：台積電 外資連續賣超達 4 天有結果，還盯 5 件", now["label"])
    page.locator(".market-header").screenshot(path=str(OUT / f"{prefix}1-home-header.png"))
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(700)
    rows = page.evaluate(HOME_ROWS)
    by_cond = {row["cond"]: row for row in rows}
    j.check(f"{prefix}清單：個股盯項的對象寫名稱與代號", by_cond.get("收盤跌破月線", {}).get("subject") == "台積電 2330"
            and by_cond.get("收盤站上壓力（3,785）", {}).get("subject") == "聯發科 2454", rows)
    j.check(f"{prefix}清單：大盤的照舊寫「大盤」、沒有連結", by_cond.get("加權指數收盤跌破年線", {}).get("subject") == "大盤"
            and by_cond["加權指數收盤跌破年線"]["href"] is None, rows)
    j.check(f"{prefix}清單：六項，有結果的那一項排第一", len(rows) == 6 and rows[0]["cond"] == "外資連續賣超達 4 天", [row["cond"] for row in rows])
    j.check(f"{prefix}清單不超出畫面", page.evaluate(base_journey.STATE)["dialogInside"] and page.evaluate(base_journey.STATE)["noScroll"])
    page.screenshot(path=str(OUT / f"{prefix}2-home-list.png"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / f"{prefix}3-home-list-dark.png"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")

    page.locator("#impactWatchList a.iw-stock", has_text="聯發科").click()
    page.wait_for_url("**/stock.html?code=2454", timeout=30_000)
    page.locator("#content").wait_for(state="visible", timeout=60_000)
    page.wait_for_timeout(1500)
    there = page.evaluate(STOCK)
    j.check(f"{prefix}點股票名稱：進到那一檔的個股頁，看到它的盯項", there["shown"] and [row["cond"] for row in there["rows"]] == ["在盯收盤站上壓力（3,785）"],
            there)

    open_stock(page, base, "2330")
    now = page.evaluate(STOCK)
    conds = [(row["cond"], row["left"], row["open"]) for row in now["rows"]]
    j.check(f"{prefix}個股頁：只列這一檔的兩筆，有結果的在前；都先收著", now["shown"] and conds == [("外資連續賣超達 4 天", "有結果", False), ("在盯收盤跌破月線", "剩 14 日", False)], conds)
    j.check(f"{prefix}個股頁：有結果的收著也看得到帶數字的那一句", now["rows"][0]["result"] == RESULT, now["rows"][0])
    j.metrics[prefix + "stock-height"] = now["height"]
    j.check(f"{prefix}個股頁：在盯的前面一個「在盯」小標，不重複寫對象", now["rows"][1]["tag"] == "在盯" and not now["rows"][1]["meta"], now["rows"][1])
    j.check(f"{prefix}個股頁：接在報價下面、不超出板、不橫向捲動", now["belowQuote"] and now["insideBoard"] and now["noScroll"], now)
    j.check(f"{prefix}個股頁：不出現買賣指令字眼", not any(word in page.locator("#stockWatch").inner_text() for word in ("買進", "賣出", "加碼", "減碼", "停損")))
    page.screenshot(path=str(OUT / f"{prefix}4-stock.png"))
    page.locator("#stockWatch .iw-item summary").nth(0).click()
    page.locator("#stockWatch .iw-item summary").nth(1).click()
    page.wait_for_timeout(400)
    j.check(f"{prefix}個股頁：點開有結果的那一筆看原本寫的準備與出處",
            page.evaluate(STOCK)["rows"][0]["body"] == [f"原本寫的準備 {PREPARE}", "出自 11/18 消息"])
    body = page.evaluate(STOCK)["rows"][1]["body"]
    j.check(f"{prefix}個股頁：點開在盯的那一筆看說明、準備、出處", body == [NOTE, f"先做好的準備 {PREPARE}", "出自 11/18 消息"], body)
    page.locator(".st-top").screenshot(path=str(OUT / f"{prefix}5-stock-open.png"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    page.wait_for_timeout(300)
    page.locator(".st-top").screenshot(path=str(OUT / f"{prefix}6-stock-open-dark.png"))
    page.evaluate("() => { document.documentElement.dataset.theme = 'light'; }")

    # 在頁面上直接查另一檔（不重新載入）：換成那一檔的；沒有盯項的整塊不出現
    page.locator("#codeInput").fill("2317")
    page.locator("#searchForm button[type=submit]").click()
    page.wait_for_function("() => document.getElementById('stockCode').textContent.trim() === '2317'", timeout=60_000)
    page.wait_for_timeout(600)
    other = page.evaluate(STOCK)
    j.check(f"{prefix}換到沒有盯項的股票：整塊不出現、不佔高度", not other["shown"] and other["height"] == 0 and not other["rows"], other)
    page.screenshot(path=str(OUT / f"{prefix}7-stock-none.png"))
    context.close()


def friend(j, browser, base: str, prefix: str, options: dict) -> None:
    context, page, _ = j.home(browser, base, FRIEND, options)
    now = page.evaluate(base_journey.STATE)
    j.check(f"{prefix}朋友（開給全部）：首頁只有大盤的三件", now["shown"] and now["text"] == "在盯 3 件 ›", now["text"])
    page.locator("#impactWatchKey").click()
    page.wait_for_timeout(600)
    html = page.content()
    j.check(f"{prefix}朋友：首頁整頁找不到站主個股盯項的任何文字", not any(text in html for text in SECRETS) and "iw-stock" not in page.locator("#impactWatchList").inner_html())
    replies = []
    page.on("response", lambda r: replies.append(r.text()) if "/api/impact-watch" in r.url else None)
    open_stock(page, base, "2330")
    there = page.evaluate(STOCK)
    j.check(f"{prefix}朋友：個股頁那一塊不出現", not there["shown"] and not there["rows"], there)
    j.check(f"{prefix}朋友：個股頁整頁與拿到的回應都找不到站主那幾筆的文字", replies and not any(text in page.content() + "".join(replies) for text in SECRETS), replies)
    context.close()


def switched_off(j, browser, base: str, prefix: str, options: dict) -> None:
    context = browser.new_context(**options)
    context.add_cookies([{"name": "test_as", "value": OWNER, "url": base}])
    page = context.new_page()
    asked = []
    page.on("request", lambda r: asked.append(r.url) if "/api/impact-watch" in r.url else None)
    open_stock(page, base, "2330")
    there = page.evaluate(STOCK)
    j.check(f"{prefix}開關關著：個股頁不出現、連問都不問", not there["shown"] and not asked, asked)
    context.close()


def main() -> int:
    j = base_journey.Journey()
    sizes = [("desktop-1440-", {"viewport": {"width": 1440, "height": 900}}, False),
             ("mobile-390-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}, True)]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for mode in ("owner", "all", None):
            server, base = serve(mode)
            try:
                for prefix, options, mobile in sizes:
                    if mode == "owner":
                        owner(j, browser, base, prefix, options, mobile)
                    elif mode == "all":
                        friend(j, browser, base, prefix, options)
                    else:
                        switched_off(j, browser, base, prefix, options)
            finally:
                server.terminate()
        browser.close()
    errors = [text for text in j.console if "favicon" not in text]
    j.check("沒有程式錯誤", not errors, errors[:5])
    failed = [name for name, ok in j.results if not ok]
    print(f"\n{len(j.results) - len(failed)}/{len(j.results)} 通過；截圖在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
