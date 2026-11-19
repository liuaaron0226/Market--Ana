"""大盤頁切換國外指數的瀏覽器旅程（本人 2026-11-19）。

點圖卡左上角的名稱（平常寫「台股加權」，後面一個淡淡的小箭頭）浮出六個指數：台股、道瓊、那指、標普、日經、韓綜。
國外只有日週月；收復／防守線、預測線、法人副圖收起；量能寫「成交量」不寫單位（來源沒有量就不給選）；
短中長期走勢改用那個指數算（沒有法人那一票）；頁首報價維持台股；每次打開都先是台股；核准的朋友也看得到。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），國外日線由這裡產生種進
overseas_quotes，不連 EODHD、不碰正式站。先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/market-index-journey/。

    python cloud/test/browser_market_index_journey.py
"""
from __future__ import annotations

import math
import os
import socket
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/market-index-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
LAST = date(2026, 11, 18)
# （代號, 起點, 量）：日經的量是 0（EODHD 的日經沒有量），韓綜最近一次沒抓到
SPECS = [("DJI.INDX", 46800, 3.2e8), ("IXIC.INDX", 22900, 8e9), ("GSPC.INDX", 6750, 4e9), ("SOX.INDX", 7200, 0),
         ("N225.INDX", 49500, 0), ("KS11.INDX", 4050, 5e8)]


def seed_sql() -> str:
    rows = [f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', 'f', 'approved', '2026-11-10T00:00:00Z');"]
    for n, (symbol, base, volume) in enumerate(SPECS):
        day, close, i = date(2025, 10, 1), base * 0.85, 0
        while day <= LAST:
            if day.weekday() < 5:
                open_ = close
                close = close * (1 + 0.004 * math.sin(i / 7 + n) + 0.0007)
                rows.append("INSERT INTO overseas_quotes (symbol, date, open, high, low, close, volume, source, fetched_at) VALUES "
                            f"('{symbol}', '{day}', {open_:.2f}, {max(open_, close) * 1.004:.2f}, {min(open_, close) * 0.996:.2f}, {close:.2f}, "
                            f"{volume * (1 + 0.3 * math.cos(i / 3)):.0f}, 'eodhd', '2026-11-18T23:20:00Z');")
                i += 1
            day += timedelta(days=1)
        ok = symbol != "KS11.INDX"
        rows.append("INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at, problem) VALUES "
                    f"('{symbol}', 'eodhd', '2026-11-19T08:00:00Z', {int(ok)}, '2026-11-18T23:20:00Z', {'NULL' if ok else chr(39) + 'quota' + chr(39)});")
    return "\n".join(rows)


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console = base, [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def home(self, browser, email: str, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": self.base}])
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.goto(f"{self.base}/minimal.html")
        page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(1500)
        return context, page


def pick(page, name: str) -> None:
    page.locator("#indexChip").click()
    page.locator("#indexMenu").wait_for(state="visible", timeout=5_000)
    page.wait_for_timeout(450)
    page.locator("#indexMenu button", has_text=name).click()
    page.wait_for_function("() => !document.getElementById('chartContext').textContent.startsWith('讀取')", timeout=10_000)
    page.wait_for_timeout(400)


def period(page, value: str) -> None:
    page.locator("#periodSelect").select_option(value)
    page.locator("#periodSelect").dispatch_event("change")
    page.wait_for_timeout(400)


STATE = """() => ({
  title: document.getElementById('chartTitle').innerText,
  context: document.getElementById('chartContext').textContent,
  price: document.getElementById('currentPrice').textContent,
  periods: [...document.querySelectorAll('#periodMenu button')].filter(b => !b.hidden).map(b => b.textContent),
  studies: [...document.querySelectorAll('#studyMenu [data-study]')].filter(b => !b.hidden).map(b => b.textContent),
  linePair: !document.querySelector('#chartLegend .line-pair').hidden,
  trend: document.getElementById('marketTrend').hidden ? null : document.getElementById('marketTrend').innerText,
  candles: window.MinimalApp.chart.candles.length,
  overlays: window.MinimalApp.chart.overlays ? window.MinimalApp.chart.overlays.length : null,
  index: window.MinimalApp.index,
  noScroll: document.documentElement.scrollWidth <= innerWidth,
})"""


def run(j: Journey, browser, prefix: str, email: str, options: dict) -> None:
    context, page = j.home(browser, email, **options)
    start = page.evaluate(STATE)
    caret = page.evaluate("() => getComputedStyle(document.getElementById('indexChip'), '::after').opacity")
    j.check(f"{prefix}一打開是台股：標題「台股加權」、後面淡淡的小箭頭", start["title"] == "台股加權" and start["index"] == "twii"
            and 0 < float(caret) < 0.5, [start["title"], caret])
    j.check(f"{prefix}台股的週期照舊有分時與 60 分、量能是成交金額", start["periods"][:2] == ["分時", "60分"] and start["studies"][0] == "成交金額",
            [start["periods"], start["studies"]])
    page.locator("#indexChip").click()
    page.locator("#indexMenu").wait_for(state="visible", timeout=5_000)
    page.wait_for_timeout(450)
    names = page.locator("#indexMenu button").all_inner_texts()
    j.check(f"{prefix}點標題浮出七個指數（11-19 下午加費半）", names == ["台股", "道瓊", "那指", "標普", "費半", "日經", "韓綜"], names)
    j.check(f"{prefix}選單不超出畫面", page.locator("#indexMenu").bounding_box()["x"] + page.locator("#indexMenu").bounding_box()["width"]
            <= options["viewport"]["width"])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}1-menu.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    pick(page, "道瓊")
    dji = page.evaluate(STATE)
    j.check(f"{prefix}道瓊：標題、副標寫美東日期", dji["title"] == "道瓊工業" and dji["context"] == "日K · 資料截至 2026-11-18（美東）", dji["context"])
    j.check(f"{prefix}道瓊：頁首報價維持台股", dji["price"] == start["price"], [dji["price"], start["price"]])
    j.check(f"{prefix}道瓊：週期只剩日週月", dji["periods"] == ["日", "週", "月"], dji["periods"])
    j.check(f"{prefix}道瓊：收復／防守鍵收起、圖上沒有判斷線", not dji["linePair"] and dji["overlays"] in (0, None), [dji["linePair"], dji["overlays"]])
    j.check(f"{prefix}道瓊：量能寫「成交量」、沒有法人副圖", dji["studies"] == ["成交量", "MACD", "KD", "RSI"], dji["studies"])
    j.check(f"{prefix}道瓊：短中長期走勢用道瓊算、沒有法人", dji["trend"] is not None and "道瓊工業 · 資料日 2026-11-18" in dji["trend"]
            and "法人" not in dji["trend"], (dji["trend"] or "")[:160])
    j.check(f"{prefix}道瓊：不造成橫向捲動", dji["noScroll"])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}2-dji.png"))
    if prefix.startswith("desktop"):
        box = page.locator("#mainChart canvas").bounding_box()
        page.mouse.click(box["x"] + box["width"] * 0.7, box["y"] + box["height"] * 0.5)
        page.wait_for_timeout(500)
        j.check(f"{prefix}道瓊：點 K 棒不會開台股那一天的明細", page.locator("#detailPanel").get_attribute("aria-hidden") == "true")
        page.keyboard.press("Escape")

    period(page, "weekly")
    week = page.evaluate(STATE)
    j.check(f"{prefix}道瓊週 K", week["context"].startswith("週K · 資料截至") and 50 <= week["candles"] <= 62, [week["context"], week["candles"]])
    pick(page, "日經")
    nikkei = page.evaluate(STATE)
    j.check(f"{prefix}日經：沿用週 K、日期是當地（不寫美東）", nikkei["context"] == "週K · 資料截至 2026-11-18", nikkei["context"])
    j.check(f"{prefix}日經：來源沒有量，量能不給選", nikkei["studies"] == ["MACD", "KD", "RSI"], nikkei["studies"])
    pick(page, "韓綜")
    korea = page.evaluate(STATE)
    j.check(f"{prefix}韓綜：最近一次沒抓到時照樣給舊日線並註明", "最近一次沒更新到" in korea["context"] and korea["candles"] > 0, korea["context"])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}3-korea-weekly.png"))

    pick(page, "台股")
    back = page.evaluate(STATE)
    j.check(f"{prefix}切回台股：標題、分時與 60 分、成交金額、法人副圖都回來", back["title"] == "台股加權" and back["periods"][:2] == ["分時", "60分"]
            and back["studies"][0] == "成交金額" and "外資" in back["studies"], [back["periods"], back["studies"]])
    j.check(f"{prefix}切回台股：走勢不再寫道瓊或韓綜", back["trend"] is not None and "韓國綜合" not in back["trend"] and "道瓊" not in back["trend"])
    page.reload()
    page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
    page.wait_for_timeout(1200)
    j.check(f"{prefix}重新整理：先回到台股", page.evaluate(STATE)["title"] == "台股加權")
    if prefix.startswith("mobile"):
        pick(page, "那指")
        page.screenshot(path=str(OUT / f"{prefix}4-nasdaq-page.png"), full_page=False)
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed = OUT / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_ENABLED_FEATURES": "market_trend_v1"}
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
            for prefix, options in [("desktop-", {"viewport": {"width": 1280, "height": 900}}),
                                    ("mobile-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True})]:
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
