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


def hourly_history() -> list[dict]:
    """富果歷史 60 分K 的假資料：2026-05-18 到昨天（11-17）每個平日五根，新到舊（和富果一樣），量是張。"""
    import datetime as dt
    rnd, price, rows = random.Random(60), 1900.0, []
    day = dt.date(2026, 5, 18)
    while day < dt.date(2026, 11, 18):
        if day.weekday() < 5:
            for hh in ["09", "10", "11", "12", "13"]:
                close = round((price + rnd.gauss(1.2, 14)) / 5) * 5
                rows.append({"date": f"{day.isoformat()}T{hh}:00:00.000+08:00", "open": price, "high": max(price, close) + 5,
                             "low": min(price, close) - 5, "close": close, "volume": int(2000 + 6000 * rnd.random())})
                price = close
        day += dt.timedelta(days=1)
    shift = PREV - rows[-1]["close"]  # 最後一根收在昨收，接今天的盤中不會跳一大段
    for row in rows:
        for key in ("open", "high", "low", "close"):
            row[key] = round(row[key] + shift, 2)
    return rows[::-1]


def write_fixture(path: Path, last: int, market_open: bool) -> None:
    path.write_text(json.dumps({"stock": stock_payload(last, market_open), "index": index_payload(last, market_open),
                                "hourly": hourly_history()}), encoding="utf-8")


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
    # 本人 11-24 選樣品 A：沒有工具列，「分析工具」一枚小鍵，點了浮出分三組的選單（draw-tools-ui.js）
    key, menu, trend = page.locator("#toolsKey"), page.locator("#drawTools"), page.locator("#drawTools button[data-tool=trend]")
    j.check(f"{prefix}日 K：只有「分析工具」鍵，選單收著", key.is_visible() and key.get_attribute("aria-expanded") == "false"
            and not menu.is_visible())
    keys = page.locator(".ts-bar-keys").bounding_box()
    period = page.locator(".hm-period .chart-chip").bounding_box()
    j.check(f"{prefix}分析工具鍵在週期鍵左邊", key.bounding_box()["x"] < period["x"], [key.bounding_box(), period, keys])
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-1-collapsed.png"))
    key.click()
    page.wait_for_timeout(450)
    names = page.evaluate("() => [...document.querySelectorAll('#drawTools button[data-tool]')].map(b => b.textContent.trim())")
    # 本人 11-24 加到十種（量測、平行通道、到價提醒、區間成交量分布），分線條、區間、標記三組
    j.check(f"{prefix}點了浮出選單：aria-expanded=true、十種工具都有名稱、分三組", key.get_attribute("aria-expanded") == "true" and menu.is_visible()
            and len(names) == 10 and all(names) and page.locator("#drawTools .dt-group").count() == 3, names)
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-2-menu.png"))
    trend.click()
    page.wait_for_timeout(200)
    j.check(f"{prefix}選了趨勢線：選單收起、鍵寫著「趨勢線」、旁邊出現離開鍵", trend.get_attribute("aria-pressed") == "true"
            and not menu.is_visible() and "趨勢線" in key.inner_text() and page.locator("#toolsExit").is_visible())
    page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}tools-3-drawing.png"))
    page.locator("#toolsExit").click()
    page.wait_for_timeout(200)
    j.check(f"{prefix}按離開：工具放下、鍵回到「分析工具」", trend.get_attribute("aria-pressed") == "false"
            and "分析工具" in key.inner_text() and not page.locator("#toolsExit").is_visible())
    key.focus()
    page.keyboard.press("Enter")
    page.wait_for_timeout(500)
    j.check(f"{prefix}鍵盤 Enter 也能打開選單", key.get_attribute("aria-expanded") == "true" and menu.is_visible())
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    j.check(f"{prefix}Esc 收回選單", key.get_attribute("aria-expanded") == "false" and not menu.is_visible())


# 分時圖十字線的狀態：直線、橫線、右邊的價位籤看不看得到，兩條線是不是虛線，橫線的高度與籤上的字。
CROSS_JS = """() => { const svg = document.querySelector('#timeShare svg.ts-svg'), g = svg.querySelector('.ts-cross');
  const v = g.querySelector('line:not(.ts-cross-h)'), h = g.querySelector('.ts-cross-h'), tag = g.querySelector('.ts-cross-tag');
  const shown = e => getComputedStyle(e).visibility === 'visible';
  const box = svg.getBoundingClientRect(), rect = tag.querySelector('rect').getBoundingClientRect();
  return { on: shown(g), v: shown(v), h: shown(h), tag: shown(tag), dash: [getComputedStyle(v).strokeDasharray, getComputedStyle(h).strokeDasharray],
    hy: Number(h.getAttribute('y1')), cy: Number(g.querySelector('circle').getAttribute('cy')),
    text: tag.querySelector('text').textContent, gap: box.right - rect.right, top: rect.top - box.top, height: box.height }; }"""
NUMBER = __import__("re").compile(r"^[\d,]+(\.\d+)?$")


def hold(cdp, page, x: float, y: float, end: bool = False) -> None:
    """手指按在 (x, y) 不放（Playwright 的觸控只有「點一下」，放開十字線就收掉了）；end＝放開。按下與放開要走同一個 CDP 連線。"""
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd" if end else "touchStart", "touchPoints": [] if end else [{"x": round(x), "y": round(y), "id": 0}]})
    page.wait_for_timeout(120)


# 本人 2026-11-24「除了垂直虛線，也要有水平虛線」，同日更正「水平虛線是要直接對準我點到那天的收盤價」：
# 分時的橫線對準選到那一分鐘的價格（價格線上那個點的高度），和游標／手指的高度無關；右邊標那個價位。
# volume：這張圖有沒有量的那一塊（手指移到量那一塊，橫線照樣在那一分鐘的價格）；lots 保留給舊呼叫。
def cross_check(j: Journey, context, page, prefix: str, name: str, volume: bool, lots: bool) -> None:
    touch = prefix == "mobile-"
    svg = page.locator("#timeShare svg.ts-svg")
    svg.scroll_into_view_if_needed()
    box = svg.bounding_box()
    cdp = context.new_cdp_session(page) if touch else None

    def at(dy: float) -> dict:
        x, y = box["x"] + box["width"] * 0.3, box["y"] + dy
        if touch:
            hold(cdp, page, x, y)
        else:
            page.mouse.move(x, y)
            page.wait_for_timeout(80)
        state = page.evaluate(CROSS_JS)
        if touch:
            hold(cdp, page, x, y, end=True)
        return state

    price = at(70)
    j.check(f"{prefix}{name}分時十字線：直線之外多一條橫的虛線，對準那一分鐘的價格（價格線上的點），右邊標那個價位",
            price["on"] and price["v"] and price["h"] and price["tag"] and price["dash"] == ["3px, 3px", "3px, 3px"] and abs(price["hy"] - price["cy"]) <= 1
            and bool(NUMBER.match(price["text"])) and -0.5 <= price["gap"] <= 3, price)
    page.locator("#timeShare").screenshot(path=str(OUT / f"{prefix}{'index' if name == '大盤' else 'stock'}-ts-crosshair.png"))
    other = at(120)
    j.check(f"{prefix}{name}分時十字線：同一分鐘換個高度，橫線與價位都不動（不跟著游標）", other["hy"] == price["hy"] and other["text"] == price["text"], other)
    if volume:
        below = at(box["height"] - 20 - 20)   # 量的那一塊（時間軸上面 20px 處）
        j.check(f"{prefix}{name}分時十字線：手指移到量的那一塊，橫線仍對準那一分鐘的價格", below["h"] and below["hy"] == price["hy"], below)
    gone = page.evaluate(CROSS_JS) if touch else (page.mouse.move(5, 5), page.wait_for_timeout(80), page.evaluate(CROSS_JS))[2]
    j.check(f"{prefix}{name}分時十字線：游標／手指離開就收掉", not gone["on"], gone)
    j.check(f"{prefix}{name}分時十字線：不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))


def stock_time_share(j: Journey, page, prefix: str, after_close: bool) -> None:
    # 報價數字看得到（本人 2026-11-20 回報「個股頁目前的價位數字不見了」）：第一次即時更新會把報價那一格換成複本，
    # 複本不能停在「數字滾動中（字透明）」卻沒有滾動的那一層。等滾完再量。
    page.wait_for_timeout(1500)
    price = page.evaluate("() => { const e = document.getElementById('close'); return [e.textContent, getComputedStyle(e).color, !!e.querySelector('.fx-odo')]; }")
    j.check(f"{prefix}報價數字看得到（字不是透明，或正在滾動）", price[0].strip() not in ("", "—") and (price[1] != "rgba(0, 0, 0, 0)" or price[2]), price)
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
                page.wait_for_timeout(350)   # 選單收起有 0.2 秒的退場（web/desk-motion.css 第 3 段）：等它退完再看還在不在
                page.locator("#periodMenu button", has_text="分時").click() if page.locator("#periodMenu").is_visible() else None
                stock_time_share(j, page, prefix, after_close=False)
                j.check(f"{prefix}週期小鍵寫「分時」（不補 K）", page.locator(".hm-period .chart-chip").inner_text() == "分時")
                cross_check(j, context, page, prefix, "個股", volume=True, lots=True)
                choose(page, "60m")
                page.wait_for_function("() => /60分K · \d{3,} 根/.test(document.getElementById('stockChartContext').textContent)", timeout=10_000)
                # 本人 11-18：60 分也能畫線（各畫各的，畫線旅程另外驗）
                j.check(f"{prefix}60 分：K 線回來、「分析工具」出現", page.locator("#chartHost").is_visible() and page.locator("#toolsKey").is_visible())
                hourly = page.evaluate("() => { const c = window.StockView.chart().candles; return [c.length, c[0].label, c.at(-1).label, c.at(-1).close]; }")
                # 6 個月的起點跟著執行當天的日期走（11-18 跑是 05/18、11-19 跑是 05/19），只鎖「從 5 月開始、09:00 開盤那根」
                j.check(f"{prefix}60 分：近 6 個月的歷史（5 月起）接上今天盤中那幾根", hourly[0] > 600
                        and hourly[1].startswith("05/") and hourly[1].endswith(" 09:00")
                        and hourly[2] == "11/18 11:00", hourly)
                page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}stock-60m.png"))
                page.mouse.move(5, 5)
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
                page.locator("#timeShare svg.ts-svg").wait_for(state="visible", timeout=10_000)
                # 本人 11-18「開放給所有核准的人」：走勢線、均價線、五檔、內外盤比都和站主一樣
                same = page.evaluate("() => [document.querySelectorAll('#timeShare .ts-line, #timeShare .ts-avg').length, document.getElementById('stockChartContext').textContent]")
                j.check(f"{prefix}核准使用者：分時走勢、均價線、五檔與內外盤比都看得到", same == [2, f"分時 · {DAY}"]
                        and page.locator("[data-ts-flow]").is_visible() and not page.locator("#timeShareEmpty").is_visible(), same)
                choose(page, "60m")
                page.wait_for_function("() => /60分K · \d{3,} 根/.test(document.getElementById('stockChartContext').textContent)", timeout=10_000)
                j.check(f"{prefix}核准使用者：60 分 K 的歷史也看得到", page.locator("#chartHost").is_visible())
                live = page.evaluate("async () => (await fetch('/api/stocks/2330/live?market=tse')).status")
                j.check(f"{prefix}核准使用者：整份即時報價（/live）照舊只給站主", live == 403, live)
                page.locator(".bd-chart").screenshot(path=str(OUT / f"{prefix}friend-ts.png"))
                friend.close()

                context = j.context(browser, OWNER, **options)
                page = context.new_page()
                page.on("pageerror", lambda e: j.console.append(f"pageerror: {e}"))
                page.goto(f"{base}/minimal.html", wait_until="domcontentloaded")   # 不等外部字型（卡住時 load 不會來）
                page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
                page.wait_for_timeout(1500)
                choose(page, "ts")
                page.locator("#timeShare svg.ts-svg").wait_for(state="visible", timeout=10_000)
                # 本人 11-19 大盤切換：圖卡標題固定是指數名稱（點了換指數），「分時」寫在副標
                j.check(f"{prefix}首頁分時：標題是「台股加權」、副標寫分時、K 線讓位", page.locator("#chartTitle").inner_text() == "台股加權"
                        and page.locator("#chartContext").inner_text().startswith("分時 · ") and not page.locator("#chartStage").is_visible())
                j.check(f"{prefix}首頁分時：沒有均價線", page.locator("#timeShare .ts-avg").count() == 0)
                j.check(f"{prefix}首頁不造成橫向捲動", page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"))
                page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}index-ts.png"))
                cross_check(j, context, page, prefix, "大盤", volume=True, lots=False)
                choose(page, "daily")
                # 首頁大盤的 K 線十字線也有橫的虛線（本人 11-24；個股頁的在畫線旅程驗）：那一列左右相鄰像素的顏色變化比上面幾像素多很多
                canvas = page.locator("#mainChart canvas").first
                canvas.scroll_into_view_if_needed()
                cbox = canvas.bounding_box()
                kx, ky = cbox["x"] + cbox["width"] * 0.4, cbox["y"] + cbox["height"] * 0.35
                if prefix == "mobile-":
                    page.touchscreen.tap(kx, ky)
                else:
                    page.mouse.move(kx, ky)
                page.wait_for_timeout(200)
                # 本人 11-24 更正：橫線對準選到那一天的收盤（不是游標的高度）。讀圖表實際畫的高度，再看那一列是不是虛線
                rows = page.evaluate("""() => { const chart = window.HomeView.chart(), cross = chart.debugBoxes.cross, picked = chart.debugBoxes.picked;
                  if (!cross) return { cross: null };
                  const canvas = chart.canvas, scale = canvas.height / canvas.getBoundingClientRect().height;
                  const row = at => { const d = canvas.getContext('2d').getImageData(0, Math.floor(at * scale), canvas.width, 1).data; let n = 0;
                    for (let i = 4; i < d.length; i += 4) if (Math.abs(d[i] - d[i - 4]) + Math.abs(d[i + 1] - d[i - 3]) + Math.abs(d[i + 2] - d[i - 2]) > 20) n++; return n; };
                  const close = (picked && picked.tags.find(tag => tag.name === '收') || {}).text;
                  return { cross, close, rows: [row(cross.y), row(cross.y - 5)] }; }""")
                j.check(f"{prefix}首頁日 K 十字線：多一條橫的虛線，對準那一天的收盤", bool(rows.get("cross")) and rows["cross"]["text"] == rows["close"]
                        and rows["rows"][0] - rows["rows"][1] >= 30, rows)
                page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}index-k-crosshair.png"))
                page.mouse.move(5, 5)
                j.check(f"{prefix}首頁切回日 K：標題照舊「台股加權」、K 線回來", page.locator("#chartTitle").inner_text() == "台股加權"
                        and page.locator("#chartContext").inner_text().startswith("日K") and page.locator("#chartStage").is_visible())
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
