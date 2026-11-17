"""設定浮板的瀏覽器旅程（本人 2026-11-17 選樣品 A：齒輪浮板＋三顆籤）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），Access 由 cookie 模擬。
先 `node cloud/build.mjs` 產生 dist，再：python cloud/test/browser_settings_journey.py
截圖與報告在 .scratch/settings-page/journey/。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import gemini_usage  # noqa: E402

OUT = ROOT / ".scratch/settings-page/journey"
OWNER = "owner@example.com"
FRIEND = "friend@example.com"
PAGES = {"home": "/minimal.html", "stock": "/stock.html?code=2330", "portfolio": "/portfolio.html"}


def seed_sql() -> str:
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    buckets = [
        {"id": "gemini-weekly", "group": "Gemini Models", "window": "weekly", "remaining": 0.9282, "reset_time": iso(now + timedelta(days=4))},
        {"id": "gemini-5h", "group": "Gemini Models", "window": "5h", "remaining": 0.1861, "reset_time": iso(now + timedelta(hours=2))},
        {"id": "gemini-argon-wtu-weekly", "group": "Gemini 4 Argon", "window": "weekly", "remaining": 0.9884, "reset_time": iso(now + timedelta(days=6))},
    ]
    sql = gemini_usage.snapshot_sql(buckets, task="watch-ledger", run_id="1", taken_at=iso(now - timedelta(minutes=5)), calls=38,
                                    model="gemini-3.8-flash-medium")
    sql += gemini_usage.snapshot_sql(buckets, task="forecast-ledger", run_id="2", taken_at=iso(now - timedelta(minutes=9)), calls=1,
                                     model="gemini-4-argon-medium")
    sql += ("INSERT INTO users (email, nickname, status, applied_at) VALUES "
            f"('{FRIEND}', 'F', 'approved', '2026-11-09T00:00:00Z'), ('new@example.com', 'N', 'pending', '2026-11-17T00:00:00Z');\n")
    return sql


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Journey:
    def __init__(self, base: str) -> None:
        self.base = base
        self.checks: list[dict[str, object]] = []
        self.errors: list[str] = []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("  ok  " if ok else "  FAIL") + f" {name}" + (f"｜{detail}" if detail != "" and not ok else ""))

    def page(self, browser, email: str, width: int, path: str):
        mobile = width < 700
        context = browser.new_context(viewport={"width": width, "height": 844 if mobile else 900}, is_mobile=mobile, has_touch=mobile,
                                      color_scheme="light")
        context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())
        context.add_cookies([{"name": "test_as", "value": quote(email), "url": self.base}])
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(f"{path}：{error}"))
        page.goto(self.base + path, wait_until="load")   # 個股頁一直在輪詢行情，等不到網路閒下來
        page.wait_for_timeout(800)
        return context, page


def state(page) -> dict:
    return page.evaluate("""() => {
      const d = document.getElementById('settingsDialog');
      const tabs = [...document.querySelectorAll('#settingsDialog [role=tab]')].map(t => [t.textContent, t.getAttribute('aria-selected')]);
      return { open: !!d?.open, tabs, tabBarHidden: d?.querySelector('.st-tabs')?.hidden,
        locked: getComputedStyle(document.body).position === 'fixed',
        expanded: document.querySelector('[data-settings-open]')?.getAttribute('aria-expanded'),
        desk: getComputedStyle(document.documentElement).getPropertyValue('--desk').trim(),
        theme: document.documentElement.dataset.theme, deskKey: document.documentElement.dataset.desk || '' };
    }""")


def owner_page(journey: Journey, browser, name: str, path: str, width: int) -> None:
    tag = f"站主 {name} {width}"
    context, page = journey.page(browser, OWNER, width, path)
    gear = page.locator("[data-settings-open]")
    journey.check(f"{tag}：頁首有齒輪、沒有舊的主題鍵、鈴鐺與右下浮動鈕",
                  gear.is_visible() and page.locator("#themeToggle, #portfolioTheme, #pushOpen, .owner-entry").count() == 0)
    page.wait_for_function("() => document.querySelector('.st-badge')")
    journey.check(f"{tag}：齒輪標出等待核准的人數", page.locator(".st-badge").inner_text() == "1")
    gear.click()
    page.wait_for_selector("#settingsDialog .st-meter")
    s = state(page)
    journey.check(f"{tag}：打開浮板、三顆籤、先看用量、底下的頁面鎖住",
                  s["open"] and [t[0] for t in s["tabs"]] == ["Gemini 用量", "通知", "外觀"] and s["tabs"][0][1] == "true"
                  and s["locked"] and s["expanded"] == "true", s)
    meters = page.eval_on_selector_all("#settingsDialog .st-meter", "ms => ms.map(m => [m.querySelector('.st-meter-name').textContent, m.querySelector('.st-meter-val').textContent, m.hasAttribute('data-low')])")
    journey.check(f"{tag}：Flash 兩條（5 小時偏低標金色）、Argon 一條",
                  meters == [["5 小時", "19%", True], ["本週", "93%", False], ["本週", "99%", False]], meters)
    today = page.eval_on_selector_all("#settingsDialog .st-today li", "ls => ls.map(l => l.innerText.replace(/\\s+/g, ' '))")
    journey.check(f"{tag}：今天各任務用中文名稱與模型", "自選股每日判斷 Flash 1" in today and "晚間事前預測 Argon 1" in today, today)
    journey.check(f"{tag}：最下面是帳號管理", page.locator("#settingsDialog .st-foot a").get_attribute("href") == "/admin/")
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-usage.png"))

    page.get_by_role("tab", name="通知").click()
    # 推播畫面先清空、等瀏覽器註冊完推播才畫出開關；等到開關或說明真的畫出來
    page.wait_for_function("() => { const p = document.querySelector('#settings-notify');"
                           " return p.querySelector('.wp-body')?.childElementCount || (p.querySelector('.st-note') && !p.textContent.includes('讀取中')); }")
    notify = page.locator("#settings-notify").inner_text()
    journey.check(f"{tag}：通知籤畫出到價推播的設定", "推播" in notify or "通知" in notify, notify[:60])
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-notify.png"))

    page.get_by_role("tab", name="外觀").click()
    page.locator("[data-desk-set='sand']").click()
    s = state(page)
    journey.check(f"{tag}：選暖沙", s["deskKey"] == "sand" and (name == "portfolio" or s["desk"] == "#e6ddcc"), s)
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-look.png"))
    page.locator("[data-theme-set='dark']").click()
    s = state(page)
    journey.check(f"{tag}：換深色，桌面背景那一列收起來", s["theme"] == "dark" and page.locator("[data-desk-set]").count() == 0, s)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)   # close 事件在下一個工作才發
    s = state(page)
    journey.check(f"{tag}：Esc 收回、頁面解鎖", not s["open"] and not s["locked"] and s["expanded"] == "false", s)
    page.reload(wait_until="load")
    page.wait_for_timeout(800)
    s = state(page)
    journey.check(f"{tag}：重新整理後還是深色、暖沙", s["theme"] == "dark" and s["deskKey"] == "sand", s)
    gear.click()
    page.wait_for_selector("#settingsDialog[open]")
    page.get_by_role("tab", name="外觀").click()
    page.locator("[data-theme-set='light']").click()
    page.locator("[data-desk-set='']").click()
    s = state(page)
    journey.check(f"{tag}：換回淺色、米白", s["theme"] == "light" and s["deskKey"] == "", s)
    # 點在浮板外面收回（手機是底下升起的板，點上方暗處；桌機點左邊）
    page.mouse.click(10, 10 if width < 700 else 400)
    page.wait_for_timeout(200)
    journey.check(f"{tag}：點外面收回", not state(page)["open"])
    context.close()


def friend_page(journey: Journey, browser, width: int) -> None:
    tag = f"朋友 home {width}"
    context, page = journey.page(browser, FRIEND, width, PAGES["home"])
    page.locator("[data-settings-open]").click()
    page.wait_for_selector("#settingsDialog[open]")
    page.wait_for_timeout(300)
    s = state(page)
    journey.check(f"{tag}：只有外觀，不顯示籤列，也沒有帳號管理",
                  s["tabBarHidden"] and page.locator("#settings-look").is_visible() and page.locator("#settingsDialog .st-foot a").count() == 0, s)
    page.screenshot(path=str(OUT / f"friend-home-{width}.png"))
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed = Path(tempfile.mkdtemp()) / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
    port = free_port()
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_WATCHLIST_WATCH": "owner", "JOURNEY_PUSH": "test"}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urlopen(base + "/", timeout=1)
        except OSError as error:
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
        else:
            break
    journey = Journey(base)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (390, 1440):
                for name, path in PAGES.items():
                    owner_page(journey, browser, name, path, width)
                friend_page(journey, browser, width)
            browser.close()
    finally:
        server.terminate()
    journey.check("頁面程式沒有丟出錯誤", not journey.errors, journey.errors[:5])
    failed = [c for c in journey.checks if not c["ok"]]
    (OUT / "report.json").write_text(json.dumps({"checks": journey.checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(journey.checks) - len(failed)}/{len(journey.checks)} 項通過；截圖與報告在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
