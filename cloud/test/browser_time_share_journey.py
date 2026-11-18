"""分時、五檔、分析工具收合的瀏覽器旅程（本人 2026-11-18，01 票）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs）；即時行情換成這裡產生的假資料
（JOURNEY_LIVE_FIXTURE，盤中與盤後各一份），不連行情商、不碰正式站。先在 cloud/ 跑過 `node build.mjs`。
截圖放 .tmp/time-share-journey/。

    python cloud/test/browser_time_share_journey.py
"""
from __future__ import annotations

import json
import random
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/time-share-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
DAY, PREV = "2026-11-18", 2235.0  # 2330 在資料包的最後一天收盤 2,235（11-09）；假資料當成今天的昨收


def clock(i: int) -> str:
    m = 9 * 60 + i
    return f"{m // 60:02d}:{m % 60:02d}"


def stock_minutes(last: int) -> list[dict]:
    rnd = random.Random(2330)
    price, rows = 2250.0, []
    for i in range(last + 1):
        if 265 <= i <= 269:
            continue  # 收盤集合競價前五分鐘沒有成交
        price = min(2455, max(2015, round((price + rnd.gauss(0, 4)) / 5) * 5))
        lots = 900 if i == 0 else 1100 if i == 270 else int(40 + 160 * rnd.random() + 300 * (2.71828 ** (-i / 14)))
        rows.append({"t": f"{DAY}T{clock(i)}:00.000+08:00", "label": clock(i), "open": price, "high": price + 5, "low": price - 5,
                     "close": price, "volume": lots * 1000})
    return rows


def hourly(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        hour = row["t"][:13]
        if out and out[-1]["t"].startswith(hour):
            bar = out[-1]
            bar.update(high=max(bar["high"], row["high"]), low=min(bar["low"], row["low"]), close=row["close"], volume=bar["volume"] + row["volume"])
        else:
            out.append({**row, "t": f"{hour}:00:00.000+08:00", "label": f"{hour[11:13]}:00"})
    return out


def stock_payload(last: int, market_open: bool) -> dict:
    rows = stock_minutes(last)
    close = rows[-1]["close"]
    quote = {"ok": True, "code": "2330", "market": "tse", "price": close, "previous_close": PREV,
             "change": round(close - PREV, 2), "change_percent": round((close - PREV) / PREV * 100, 2),
             "open": rows[0]["open"], "high": max(r["high"] for r in rows), "low": min(r["low"] for r in rows),
             "volume_shares": sum(r["volume"] for r in rows), "data_time": f"{DAY}T{rows[-1]['label']}:05+08:00", "data_date": DAY}
    return {"ok": True, "market": "tse", "provider": "fugle", "market_open": market_open, "market_phase": "open" if market_open else "closed",
            "live": market_open, "close_pending": False, "retry_after_seconds": 0, "error": None, "delay_seconds": {"quote": 15, "chart": 60},
            "quote": quote, "candles": {"1": rows, "60": hourly(rows)},
            "book": {"source": "fugle", "time": quote["data_time"],
                     "bids": [[close - 5 * k, n] for k, n in zip(range(0, 5), [312, 845, 598, 1021, 467])],
                     "asks": [[close + 5 * k, n] for k, n in zip(range(1, 6), [523, 418, 946, 730, 622])],
                     "flow": {"at_ask": 18167, "at_bid": 15957}}}


def index_payload(last: int, market_open: bool) -> dict:
    rnd = random.Random(1)
    prev, price, series = 27980.5, 28010.0, []
    for i in range(last + 1):
        if 265 <= i <= 269:
            continue
        price = round(price + rnd.gauss(0, 9), 2)
        series.append({"timestamp": f"{DAY}T{clock(i)}:00.000+08:00", "time": clock(i), "price": price, "open": price, "high": price + 3,
                       "low": price - 3, "volume": None, "volume_raw": int(200 + 900 * rnd.random()), "volume_unit": "unknown"})
    data = {"price": price, "previous_close": prev, "change": round(price - prev, 2), "change_percent": round((price - prev) / prev * 100, 2),
            "open": series[0]["price"], "high": max(s["high"] for s in series), "low": min(s["low"] for s in series),
            "turnover_raw": None, "turnover_unit": "unknown", "turnover_billion": None}
    stamp = f"{DAY}T{series[-1]['time']}:05+08:00"
    return {"ok": True, "source": "fugle-index", "symbol": "IX0001", "served_at": stamp, "fetched_at": stamp, "data_time": stamp,
            "market_open": market_open, "market_phase": "open" if market_open else "closed", "phase": "open" if market_open else "closed",
            "live": False, "delay": "closed", "retry_after_seconds": 0, "retryable": False, "error": None, "warning": None,
            "data": data, "series": series}


def write_fixture(path: Path, last: int, market_open: bool) -> None:
    path.write_text(json.dumps({"stock": stock_payload(last, market_open), "index": index_payload(last, market_open)}), encoding="utf-8")


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console = base, [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def context(self, browser, email: str, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": self.base}])
        return context

    def stock(self, context):
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.goto(f"{self.base}/stock.html?code=2330")
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(900)
        return page


def choose(page, value: str) -> None:
    page.locator("#periodSelect").select_option(value)
    page.locator("#periodSelect").dispatch_event("change")
    page.wait_for_timeout(500)


def tools_journey(j: Journey, page, prefix: str) -> None:
    key, tray, trend = page.locator("#toolsKey"), page.locator("#toolsTray"), page.locator("#drawTools button[data-tool=trend]")
    j.check(f"{prefix}日 K：只有「分析工具」鍵，工具收著", key.is_visible() and key.get_attribute("aria-expanded") == "false"
            and tray.bounding_box()["height"] <= 3 and float(trend.evaluate("b => getComputedStyle(b).opacity")) == 0)
    keys = page.locator(".ts-bar-keys").bounding_box()
    period = page.locator(".hm-period .chart-chip").bounding_box()
    j.check(f"{prefix}分析工具鍵在週期鍵左邊", key.bounding_box()["x"] < period["x"], [key.bounding_box(), period, keys])
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-1-collapsed.png"))
    key.click()
    page.wait_for_timeout(110)
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-2-opening.png"))
    page.wait_for_timeout(500)
    opened = [float(b.evaluate("b => getComputedStyle(b).opacity")) for b in page.locator("#drawTools button[data-tool]").all()]
    j.check(f"{prefix}點了展開：aria-expanded=true、六顆都出來、鍵變深色", key.get_attribute("aria-expanded") == "true" and opened == [1.0] * 6
            and tray.bounding_box()["height"] > 40, opened)
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-3-expanded.png"))
    trend.click()
    j.check(f"{prefix}展開後工具照常可選", trend.get_attribute("aria-pressed") == "true")
    key.click()
    page.wait_for_timeout(120)
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-4-closing.png"))
    page.wait_for_timeout(500)
    j.check(f"{prefix}再點收回：選到一半的工具放下、托盤不可操作", key.get_attribute("aria-expanded") == "false"
            and trend.get_attribute("aria-pressed") == "false" and tray.evaluate("t => t.inert") and tray.bounding_box()["height"] <= 3)
    key.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    j.check(f"{prefix}鍵盤 Enter 也能展開", key.get_attribute("aria-expanded") == "true")
    page.keyboard.press("Space")
    page.wait_for_timeout(500)
    j.check(f"{prefix}鍵盤空白鍵收回", key.get_attribute("aria-expanded") == "false")


def stock_time_share(j: Journey, page, prefix: str, after_close: bool) -> None:
    choose(page, "ts")
    page.locator("#timeShare svg.ts-svg").wait_for(state="visible", timeout=10_000)
    page.locator("[data-ts-book]:not([hidden])").wait_for(state="visible", timeout=10_000)
    j.check(f"{prefix}分時：分析工具鍵不出現、K 線讓位", not page.locator("#toolsKey").is_visible()
            and not page.locator("#chartHost").is_visible() and not page.locator("#chartLegend").is_visible())
    chips = page.evaluate("() => [...document.querySelectorAll('#timeShare .ts-chip text')].map(t => t.textContent)")
    j.check(f"{prefix}縱軸是漲停 2455、參考 2235、跌停 2015", chips == ["2455", "2235", "2015"], chips)
    quote = page.locator("[data-ts-quote]").inner_text()
    j.check(f"{prefix}上方一行有時間、價、漲跌、均、量", all(word in quote for word in ["價", "均", "量"]) and ":" in quote, quote)
    j.check(f"{prefix}開高低總量一行", all(word in page.locator("[data-ts-ohlc]").inner_text() for word in ["開", "高", "低", "總量"]))
    j.check(f"{prefix}卡片寫「分時 · 日期」", page.locator("#stockChartContext").inner_text() == f"分時 · {DAY}")
    end = page.evaluate("() => { const p = document.querySelector('#timeShare .ts-line').getAttribute('points').trim().split(' ').at(-1).split(',')[0]; return Number(p) / document.querySelector('#timeShare svg').width.baseVal.value; }")
    j.check(f"{prefix}{'盤後畫到 13:30' if after_close else '盤中只畫到現在，後面留白'}", end > 0.99 if after_close else 0.5 < end < 0.9, end)
    flow = page.locator("[data-ts-flow]").inner_text()
    j.check(f"{prefix}內外盤比：外盤 53%（18,167）在左、內盤 47% 在右", "外盤53%18,167" in flow.replace("\n", "").replace(" ", "")
            and "15,957" in flow, flow)
    bids = page.locator("[data-book-bid] li:not(.ts-sum)").count()
    asks = page.locator("[data-book-ask] li:not(.ts-sum)").count()
    j.check(f"{prefix}五檔：買賣各五檔加合計", bids == 5 and asks == 5 and "3,243" in page.locator("[data-book-bid] .ts-sum").inner_text(), [bids, asks])
    j.check(f"{prefix}不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}stock-ts-{'after-close' if after_close else 'intraday'}.png"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    fixture, seed = OUT / "live-fixture.json", OUT / "seed.sql"
    write_fixture(fixture, 140, True)  # 盤中 11:20
    seed.write_text(f"INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', 'f', 'approved', '2026-11-10T00:00:00Z');", encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    env = {**__import__("os").environ, "JOURNEY_LIVE_FIXTURE": str(fixture), "JOURNEY_SEED_SQL": str(seed)}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urlopen(base, timeout=1); break
            except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
                if getattr(error, "code", None):
                    break
                time.sleep(0.2)
        j = Journey(base)
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for prefix, options in [("desktop-", {"viewport": {"width": 1280, "height": 900}}),
                                    ("mobile-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True})]:
                write_fixture(fixture, 140, True)
                context = j.context(browser, OWNER, **options)
                page = j.stock(context)
                tools_journey(j, page, prefix)
                page.locator(".hm-period .chart-chip").click()
                page.wait_for_timeout(200)
                names = page.locator("#periodMenu button:not([hidden])").all_inner_texts()
                j.check(f"{prefix}週期選單：分時、60分、日、週、月", names == ["分時", "60分", "日", "週", "月"], names)
                j.check(f"{prefix}週期小鍵寫「日K」", page.locator(".hm-period .chart-chip").inner_text() == "日K")
                page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}period-menu.png"))
                page.keyboard.press("Escape")
                page.locator("#periodMenu button", has_text="分時").click() if page.locator("#periodMenu").is_visible() else None
                stock_time_share(j, page, prefix, after_close=False)
                j.check(f"{prefix}週期小鍵寫「分時」（不補 K）", page.locator(".hm-period .chart-chip").inner_text() == "分時")
                choose(page, "60m")
                j.check(f"{prefix}60 分：K 線回來、仍不能畫線", page.locator("#chartHost").is_visible() and not page.locator("#toolsKey").is_visible())
                choose(page, "daily")
                j.check(f"{prefix}切回日 K：分析工具鍵回來（收著）", page.locator("#toolsKey").is_visible()
                        and page.locator("#toolsKey").get_attribute("aria-expanded") == "false")
                write_fixture(fixture, 270, False)  # 盤後
                page.reload()
                page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
                page.wait_for_timeout(900)
                stock_time_share(j, page, prefix, after_close=True)
                if prefix == "mobile-":
                    page.screenshot(path=str(OUT / "mobile-stock-full.png"), full_page=True)
                context.close()

                friend = j.context(browser, FRIEND, **options)
                page = j.stock(friend)
                choose(page, "ts")
                page.locator("[data-ts-book]:not([hidden])").wait_for(state="visible", timeout=10_000)
                empty = page.locator("#timeShareEmpty").inner_text()
                j.check(f"{prefix}核准使用者：五檔與內外盤比看得到，走勢寫明只限站主", "僅限站主" in empty
                        and page.locator("[data-ts-flow]").is_visible(), empty)
                page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}friend-ts.png"))
                friend.close()

                context = j.context(browser, OWNER, **options)
                page = context.new_page()
                page.on("pageerror", lambda e: j.console.append(f"pageerror: {e}"))
                page.goto(f"{base}/minimal.html")
                page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
                page.wait_for_timeout(1500)
                choose(page, "ts")
                page.locator("#timeShare svg.ts-svg").wait_for(state="visible", timeout=10_000)
                j.check(f"{prefix}首頁分時：標題寫「分時」、K 線讓位", page.locator("#chartTitle").inner_text() == "分時"
                        and not page.locator("#chartStage").is_visible())
                j.check(f"{prefix}首頁分時：沒有均價線", page.locator("#timeShare .ts-avg").count() == 0)
                j.check(f"{prefix}首頁不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}index-ts.png"))
                choose(page, "daily")
                j.check(f"{prefix}首頁切回日 K：標題回「主K線」、K 線回來", page.locator("#chartTitle").inner_text() == "主K線"
                        and page.locator("#chartStage").is_visible())
                context.close()
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
