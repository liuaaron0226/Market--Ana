"""海外大盤分時的瀏覽器旅程（本人 2026-11-24，.scratch/index-intraday 票 01）。

大盤頁切到國外指數後，週期多一個「分時」（和加權指數同一個控制、同一套畫面）；切換清單多一項只有分時的那斯達克期貨。
時間軸是台北時間；日經午休不連線；來源給的不是交易所今天的資料時才在標題下寫是哪一天；開著分時時每分鐘更新、頁面在背景時停。
開關只開站主時，朋友的國外指數照舊只有日週月、清單沒有期貨。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs）。來源（Yahoo Finance）的回應換成這裡產生的
假資料（JOURNEY_INTRADAY_FIXTURE），台股即時行情也換成假資料（JOURNEY_LIVE_FIXTURE）：不連任何行情來源、不碰正式站。
先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/index-intraday-journey/。

    python cloud/test/browser_index_intraday_journey.py
"""
from __future__ import annotations

import json
import math
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_market_index_journey import seed_sql  # noqa: E402 國外日線的種子
from browser_time_share_journey import write_fixture  # noqa: E402 台股即時行情的假資料

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".tmp/index-intraday-journey"
OWNER, FRIEND = "owner@example.com", "friend@example.com"
STEP = 300


def ny_offset(now: datetime) -> int:
    try:
        from zoneinfo import ZoneInfo
        return int(now.astimezone(ZoneInfo("America/New_York")).utcoffset().total_seconds())
    except Exception:  # noqa: BLE001 Windows 沒裝 tzdata：11 月到 3 月是 -5
        return -5 * 3600


def local_midnight(now: datetime, offset: int) -> int:
    """那個時差的「今天」00:00 是幾秒（epoch）。"""
    local = now + timedelta(seconds=offset)
    return int(datetime(local.year, local.month, local.day, tzinfo=timezone.utc).timestamp()) - offset


def chart(tz: str, offset: int, bars: list[list], prev: float, period: tuple[int, int]) -> dict:
    """來源會回的那一份（只放用得到的欄位）。bars：[秒, 收盤或 None]。"""
    closes = [b[1] for b in bars]
    last = [c for c in closes if c is not None][-1]
    meta = {"exchangeTimezoneName": tz, "gmtoffset": offset, "regularMarketPrice": last, "chartPreviousClose": prev, "previousClose": prev,
            "regularMarketDayHigh": 0, "regularMarketDayLow": 0, "regularMarketTime": bars[-1][0],
            "currentTradingPeriod": {"regular": {"start": period[0], "end": period[1], "gmtoffset": offset, "timezone": "X"}}}
    return {"chart": {"result": [{"meta": meta, "timestamp": [b[0] for b in bars],
                                  "indicators": {"quote": [{"close": closes, "volume": [0 for _ in bars]}]}}], "error": None}}


def wave(start: int, count: int, base: float, seed: float, skip=lambda i: False) -> list[list]:
    return [[start + i * STEP, None if skip(i) else round(base * (1 + 0.006 * math.sin(i / 9 + seed) + 0.00004 * i), 2)] for i in range(count)]


def fixtures(now: datetime) -> tuple[dict, dict]:
    tokyo = local_midnight(now, 9 * 3600) + 9 * 3600            # 東京今天 09:00
    tokyo_period = (tokyo, tokyo + int(6.5 * 3600))
    seoul_old = tokyo - 4 * 86400                                # 四天前那一場（休市時來源給的最近一個交易日）
    ny_off = ny_offset(now)
    ny_midnight = local_midnight(now, ny_off)
    ny_open = ny_midnight + int(9.5 * 3600)
    data = {
        # 日經：11:30～12:25 午休沒有價
        "^N225": chart("Asia/Tokyo", 9 * 3600, wave(tokyo, 79, 50200, 0.4, lambda i: 30 <= i < 42), 50000, tokyo_period),
        # 韓綜：資料是四天前的，來源的「目前時段」是今天
        "^KS11": chart("Asia/Seoul", 9 * 3600, wave(seoul_old, 79, 4040, 1.7), 4055, tokyo_period),
        "^DJI": chart("America/New_York", ny_off, wave(ny_open, 79, 47150, 2.2), 47200, (ny_open, ny_open + int(6.5 * 3600))),
        "^IXIC": {"status": 429},                                # 來源擋掉、也沒有上一份
        # 期貨：紐約 00:00 起一整天，17:00～17:55 每日休息
        "NQ=F": chart("America/New_York", ny_off, wave(ny_midnight, 288, 25300, 0.9, lambda i: 204 <= i < 216), 25250, (ny_midnight, ny_midnight + 86340)),
    }
    # 美元／台幣（本人 2026-11-24）：倫敦 00:00 起一整天的 5 分鐘線（小數四位，伺服器取到三位）；日線另外給（"TWD=X@1d"，最近 260 個平日）
    london = local_midnight(now, 0)
    fx_bars = [[london + i * STEP, round(32.4 + 0.06 * math.sin(i / 23 + 1.3) + 0.0001 * i, 4)] for i in range(288)]
    data["TWD=X"] = chart("Europe/London", 0, fx_bars, 32.3801, (london, london + 86340))
    days, stamp = [], london
    while len(days) < 260:
        if datetime.fromtimestamp(stamp, timezone.utc).weekday() < 5:
            days.append(stamp)
        stamp -= 86400
    days.reverse()
    closes = [round(31.2 + 1.2 * i / 259 + 0.15 * math.sin(i / 11), 4) for i in range(260)]
    opens = [closes[0] - 0.02] + closes[:-1]
    data["TWD=X@1d"] = {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "Europe/London", "gmtoffset": 0,
                 "currentTradingPeriod": {"regular": {"start": london, "end": london + 86340, "gmtoffset": 0, "timezone": "X"}}},
        "timestamp": days,
        "indicators": {"quote": [{"open": opens, "close": closes, "high": [max(o, c) + 0.0312 for o, c in zip(opens, closes)],
                                  "low": [min(o, c) - 0.0288 for o, c in zip(opens, closes)], "volume": [0 for _ in days]}]}}], "error": None}}
    dates = {"seoul_old": (datetime.fromtimestamp(seoul_old, timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d"),
             "fx_word": "貶值" if fx_bars[-1][1] > 32.3805 else "升值",
             "fx_daily_last": datetime.fromtimestamp(days[-1], timezone.utc).strftime("%Y-%m-%d")}
    return data, dates


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console, self.intraday_requests = base, [], [], []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def home(self, browser, email: str, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": email, "url": self.base}])
        context.route("**/*", lambda route: route.abort() if "fonts.g" in route.request.url else route.continue_())
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.on("request", lambda r: self.intraday_requests.append(r.url) if r.url.endswith("/intraday") else None)
        page.goto(f"{self.base}/minimal.html", wait_until="domcontentloaded")
        page.locator("#mainChart canvas").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(1500)
        return context, page


def pick(page, name: str) -> None:
    page.locator("#indexChip").click()
    page.locator("#indexMenu").wait_for(state="visible", timeout=5_000)
    page.wait_for_timeout(450)
    page.locator("#indexMenu button", has_text=name).first.click() if name != "那指" else page.locator("#indexMenu button[data-index='ixic']").click()
    page.wait_for_function("() => !document.getElementById('chartContext').textContent.startsWith('讀取')", timeout=10_000)
    page.wait_for_timeout(400)


def choose_period(page, value: str) -> None:
    """照使用者的點法：點週期鍵、再點選單裡那一項。"""
    page.locator(".hm-period > .chart-chip").click()
    page.locator("#periodMenu").wait_for(state="visible", timeout=5_000)
    page.wait_for_timeout(450)
    page.locator(f"#periodMenu button[data-value='{value}']").click()
    page.wait_for_function("() => !document.getElementById('chartContext').textContent.startsWith('讀取')", timeout=10_000)
    page.wait_for_timeout(400)


STATE = """() => {
  const note = document.getElementById('chartContext');
  const panel = document.getElementById('timeShare');
  return {
    title: document.getElementById('chartTitle').innerText,
    note: note.textContent,
    noteShown: !note.classList.contains('sr-only'),
    price: document.getElementById('currentPrice').textContent,
    chip: document.querySelector('.hm-period > .chart-chip').textContent,
    periods: [...document.querySelectorAll('#periodMenu button')].filter(b => !b.hidden).map(b => b.textContent),
    disabled: [...document.querySelectorAll('#periodMenu button')].filter(b => !b.hidden && b.disabled).map(b => b.textContent),
    names: [...document.querySelectorAll('#indexMenu button')].map(b => b.textContent),
    index: window.MinimalApp.index,
    period: window.MinimalApp.period,
    polling: window.MinimalApp.intradayPolling,
    panel: !panel.hidden && panel.getBoundingClientRect().height > 0,
    stage: document.getElementById('chartStage').getBoundingClientRect().height > 0,
    lines: panel.querySelectorAll('.ts-line').length,
    volume: panel.querySelectorAll('.ts-vol').length,
    times: [...panel.querySelectorAll('.ts-time')].map(t => t.textContent),
    chips: [...panel.querySelectorAll('.ts-chip text')].map(t => t.textContent),
    quote: panel.querySelector('[data-ts-quote]').textContent.replace(/\\s+/g, ''),
    ohlc: panel.querySelector('[data-ts-ohlc]').textContent.replace(/\\s+/g, ''),
    label: panel.querySelector('svg') ? panel.querySelector('svg').getAttribute('aria-label') : null,
    svgWidth: panel.querySelector('svg') ? panel.querySelector('svg').getBoundingClientRect().width : 0,
    marks: [...panel.querySelectorAll('.ts-mark text')].map(t => t.textContent),
    studies: [...document.querySelectorAll('#studyMenu [data-study]')].filter(b => !b.hidden).map(b => b.textContent),
    candles: window.MinimalApp.chart ? window.MinimalApp.chart.candles.length : 0,
    trendShown: !document.getElementById('marketTrend').hidden,
    noScroll: document.documentElement.scrollWidth <= innerWidth,
  };
}"""

HIDE = """(hidden) => {
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  document.dispatchEvent(new Event('visibilitychange'));
}"""


def owner_run(j: Journey, browser, prefix: str, options: dict, dates: dict) -> None:
    context, page = j.home(browser, OWNER, **options)
    width = options["viewport"]["width"]
    start = page.evaluate(STATE)
    j.check(f"{prefix}切換清單多那斯達克期貨與美元／台幣", start["names"] == ["台股", "道瓊", "那指", "標普", "費半", "日經", "韓綜", "那指期", "美元／台幣"], start["names"])
    page.locator("#indexChip").click()
    page.locator("#indexMenu").wait_for(state="visible", timeout=5_000)
    page.wait_for_timeout(450)
    box = page.locator("#indexMenu").bounding_box()
    j.check(f"{prefix}清單不超出畫面", box["x"] >= 0 and box["x"] + box["width"] <= width, box)
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}0-menu.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)

    pick(page, "日經")
    daily = page.evaluate(STATE)
    j.check(f"{prefix}日經：週期是分時、日、週、月，分時可以選", daily["periods"] == ["分時", "日", "週", "月"] and not daily["disabled"]
            and daily["period"] == "daily", [daily["periods"], daily["disabled"], daily["period"]])
    before = len(j.intraday_requests)
    choose_period(page, "ts")
    nikkei = page.evaluate(STATE)
    j.check(f"{prefix}日經分時：分時圖取代 K 線，週期鍵寫分時", nikkei["period"] == "ts" and nikkei["panel"] and not nikkei["stage"] and nikkei["chip"] == "分時",
            [nikkei["period"], nikkei["panel"], nikkei["stage"], nikkei["chip"]])
    j.check(f"{prefix}日經分時：午休不連線（兩段線）、不畫量", nikkei["lines"] == 2 and nikkei["volume"] == 0, [nikkei["lines"], nikkei["volume"]])
    j.check(f"{prefix}日經分時：時間軸是台北時間 08:00～14:30", nikkei["times"] == ["08:00", "10:00", "12:00", "14:30"], nikkei["times"])
    j.check(f"{prefix}日經分時：基準是前一日收盤 50,000", "50,000.00" in nikkei["chips"] and nikkei["label"] == "日經 225分時走勢", [nikkei["chips"], nikkei["label"]])
    j.check(f"{prefix}日經分時：上方一行是最新那一根（台北 14:30）的指數與漲跌", nikkei["quote"].startswith("14:30指數") and "%" in nikkei["quote"]
            and nikkei["ohlc"].startswith("開"), [nikkei["quote"], nikkei["ohlc"]])
    j.check(f"{prefix}日經分時：資料是交易所今天的，標題下不多寫字", not nikkei["noteShown"], nikkei["note"])
    j.check(f"{prefix}日經分時：頁首報價維持台股", nikkei["price"] == start["price"], [nikkei["price"], start["price"]])
    j.check(f"{prefix}日經分時：圖不超出畫面、不造成橫向捲動", nikkei["noScroll"] and 0 < nikkei["svgWidth"] <= width, [nikkei["svgWidth"], width])
    j.check(f"{prefix}日經分時：開著時排了每分鐘更新", nikkei["polling"] is True and len(j.intraday_requests) == before + 1,
            [nikkei["polling"], len(j.intraday_requests) - before])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}1-nikkei-intraday.png"))
    if width <= 500:
        page.screenshot(path=str(OUT / f"{prefix}1-nikkei-intraday-page.png"), full_page=False)

    # 游標移到午休前（台北 10:00 附近）
    chart_box = page.locator("#timeShare svg").bounding_box()
    page.mouse.move(chart_box["x"] + chart_box["width"] * 24 / 78, chart_box["y"] + 60)
    page.wait_for_timeout(200)
    hover = page.evaluate(STATE)["quote"]
    j.check(f"{prefix}日經分時：游標那一行的時間是台北時間", hover.startswith("10:00指數"), hover)
    page.mouse.move(2, 2)

    # 頁面到背景就停，回到前景馬上讀一次再接著排
    page.evaluate(HIDE, True)
    page.wait_for_timeout(200)
    hidden_polling = page.evaluate("() => window.MinimalApp.intradayPolling")
    before = len(j.intraday_requests)
    page.evaluate(HIDE, False)
    page.wait_for_function("() => window.MinimalApp.intradayPolling === true", timeout=10_000)
    j.check(f"{prefix}頁面在背景時不更新；回到前景讀一次、再排下一次", hidden_polling is False and len(j.intraday_requests) == before + 1,
            [hidden_polling, len(j.intraday_requests) - before])

    pick(page, "韓綜")
    korea = page.evaluate(STATE)
    j.check(f"{prefix}韓綜：沿用分時；休市時寫出這是哪一天的資料", korea["period"] == "ts" and korea["noteShown"]
            and korea["note"] == f"最近交易日 {dates['seoul_old']}", [korea["period"], korea["noteShown"], korea["note"]])
    j.check(f"{prefix}韓綜：沒有午休，一段線", korea["lines"] == 1 and korea["times"][0] == "08:00", [korea["lines"], korea["times"]])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}2-korea-closed.png"))

    pick(page, "道瓊")
    dji = page.evaluate(STATE)
    j.check(f"{prefix}道瓊分時：台北時間跨過半夜（開盤、收盤各一個時間，中間是整點）", dji["period"] == "ts" and len(dji["times"]) == 5
            and dji["times"][1:4] == ["00:00", "02:00", "04:00"] and not dji["noteShown"], [dji["times"], dji["note"]])

    pick(page, "那指")
    nasdaq = page.evaluate(STATE)
    j.check(f"{prefix}那指：來源拿不到分時就退回日 K、說一聲，不出錯", nasdaq["period"] == "daily" and nasdaq["stage"] and not nasdaq["panel"]
            and nasdaq["noteShown"] and nasdaq["note"] == "那斯達克綜合分時暫時拿不到資料，請稍後再試", [nasdaq["period"], nasdaq["note"]])
    j.check(f"{prefix}那指：離開分時就不再更新", nasdaq["polling"] is False)

    pick(page, "那指期")
    futures = page.evaluate(STATE)
    j.check(f"{prefix}那斯達克期貨：直接是分時，週期只有分時", futures["title"] == "那斯達克期貨" and futures["period"] == "ts" and futures["panel"]
            and futures["periods"] == ["分時"], [futures["title"], futures["period"], futures["periods"]])
    j.check(f"{prefix}那斯達克期貨：每日休息那一段不連線、時間軸是台北時間", futures["lines"] == 2 and len(futures["times"]) >= 3
            and all(len(t) == 5 for t in futures["times"]) and futures["volume"] == 0, [futures["lines"], futures["times"]])
    j.check(f"{prefix}那斯達克期貨：不造成橫向捲動、排了更新", futures["noScroll"] and futures["polling"] is True)
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}3-futures.png"))
    if width <= 500:
        page.screenshot(path=str(OUT / f"{prefix}3-futures-page.png"), full_page=False)

    fx_run(j, page, prefix, width, dates, start)

    pick(page, "標普")
    gspc = page.evaluate(STATE)
    j.check(f"{prefix}從期貨換到標普：日週月回來；來源沒資料時退回日 K", gspc["periods"] == ["分時", "日", "週", "月"] and gspc["period"] == "daily" and gspc["stage"],
            [gspc["periods"], gspc["period"]])

    pick(page, "台股")
    back = page.evaluate(STATE)
    j.check(f"{prefix}切回台股：分時與 60 分都在、不再更新國外", back["title"] == "台股加權" and back["periods"][:2] == ["分時", "60分"] and back["polling"] is False,
            [back["periods"], back["polling"]])
    choose_period(page, "ts")
    taiwan = page.evaluate(STATE)
    j.check(f"{prefix}台股分時照舊：09:00～13:30、有成交金額", taiwan["period"] == "ts" and taiwan["times"][0] == "09:00" and taiwan["times"][-1] == "13:30"
            and taiwan["volume"] == 1 and taiwan["lines"] == 1 and taiwan["label"] == "加權指數分時走勢", [taiwan["times"], taiwan["volume"], taiwan["lines"]])
    pick(page, "日經")
    again = page.evaluate(STATE)
    j.check(f"{prefix}台股分時中換到日經：直接是日經分時", again["period"] == "ts" and again["times"][0] == "08:00" and again["lines"] == 2, [again["period"], again["times"]])
    context.close()


THREE = r"\d{2}\.\d{3}"


def fx_run(j: Journey, page, prefix: str, width: int, dates: dict, start: dict) -> None:
    """美元／台幣（本人 2026-11-24）：選了先是分時（小數三位、寫台幣升值／貶值），可以換日K（沒有量）。進來時在期貨的分時。"""
    import re
    pick(page, "美元／台幣")
    fx = page.evaluate(STATE)
    j.check(f"{prefix}美元／台幣：選了直接是分時，週期有分時、日、週、月", fx["title"] == "美元／台幣" and fx["index"] == "usdtwd" and fx["period"] == "ts"
            and fx["panel"] and not fx["stage"] and fx["periods"] == ["分時", "日", "週", "月"], [fx["title"], fx["period"], fx["periods"]])
    j.check(f"{prefix}美元／台幣分時：價位都是小數三位（基準 32.380、上下緣、最高最低）", "32.380" in fx["chips"] and len(fx["chips"]) == 3
            and all(re.fullmatch(THREE, text) for text in fx["chips"] + fx["marks"]) and len(fx["marks"]) == 2, [fx["chips"], fx["marks"]])
    j.check(f"{prefix}美元／台幣分時：上方一行是 時間、匯率（三位）、台幣升值或貶值幾元（三位）與幅度",
            re.fullmatch(rf"\d\d:\d\d匯率{THREE}台幣{dates['fx_word']}\d\.\d{{3}}元\(\d\.\d\d%\)", fx["quote"]) is not None, [fx["quote"], dates["fx_word"]])
    j.check(f"{prefix}美元／台幣分時：開高低也是三位", re.fullmatch(rf"開{THREE}高{THREE}低{THREE}", fx["ohlc"]) is not None, fx["ohlc"])
    j.check(f"{prefix}美元／台幣分時：連續一段線、不畫量、時間軸是台北時間、標題下不多寫字", fx["lines"] == 1 and fx["volume"] == 0
            and len(fx["times"]) >= 3 and all(len(t) == 5 for t in fx["times"]) and not fx["noteShown"] and fx["label"] == "美元／台幣分時走勢",
            [fx["lines"], fx["volume"], fx["times"], fx["note"]])
    j.check(f"{prefix}美元／台幣：不套短中長期走勢（偏多偏空會讓人看反）、頁首報價維持台股", not fx["trendShown"] and fx["price"] == start["price"],
            [fx["trendShown"], fx["price"]])
    j.check(f"{prefix}美元／台幣分時：不造成橫向捲動、圖不超出畫面、排了更新", fx["noScroll"] and 0 < fx["svgWidth"] <= width and fx["polling"] is True,
            [fx["noScroll"], fx["svgWidth"], fx["polling"]])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}4-usdtwd-intraday.png"))

    choose_period(page, "daily")
    daily = page.evaluate(STATE)
    j.check(f"{prefix}美元／台幣日K：K 線圖取代分時，一年的日線（260 根），不再每分鐘更新", daily["period"] == "daily" and daily["stage"] and not daily["panel"]
            and daily["candles"] == 260 and daily["polling"] is False, [daily["period"], daily["stage"], daily["candles"], daily["polling"]])
    j.check(f"{prefix}美元／台幣日K：沒有量，副圖只有 MACD、KD、RSI", daily["studies"] == ["MACD", "KD", "RSI"], daily["studies"])
    j.check(f"{prefix}美元／台幣日K：資料到來源最新那一天、標題下不多寫字", daily["note"] == f"日K · 資料截至 {dates['fx_daily_last']}" and not daily["noteShown"],
            [daily["note"], daily["noteShown"]])
    j.check(f"{prefix}美元／台幣日K：不造成橫向捲動、走勢那一塊照樣收著", daily["noScroll"] and not daily["trendShown"], [daily["noScroll"], daily["trendShown"]])
    page.locator(".chart-workspace").screenshot(path=str(OUT / f"{prefix}5-usdtwd-daily.png"))
    choose_period(page, "weekly")
    weekly = page.evaluate(STATE)
    j.check(f"{prefix}美元／台幣週K：由日線合出來", weekly["period"] == "weekly" and 50 <= weekly["candles"] <= 54, [weekly["period"], weekly["candles"]])
    choose_period(page, "ts")
    again = page.evaluate(STATE)
    j.check(f"{prefix}美元／台幣：換回分時", again["period"] == "ts" and again["panel"] and again["polling"] is True, [again["period"], again["polling"]])


def fx_daily_missing_run(j: Journey, browser, prefix: str, options: dict) -> None:
    """日線向來源要不到、也沒有上一份（伺服器回 candles 是空的）：只留分時，不出錯。"""
    context, page = j.home(browser, OWNER, **options)
    page.route("**/api/indices/usdtwd", lambda route: route.fulfill(status=200, content_type="application/json",
               body=json.dumps({"ok": True, "available": False, "key": "usdtwd", "symbol": "TWD=X", "name": "美元／台幣", "zone": None, "candles": [], "fetch": None})))
    pick(page, "美元／台幣")
    fx = page.evaluate(STATE)
    j.check(f"{prefix}美元／台幣日線拿不到：照樣進分時，週期只有分時，不出錯", fx["index"] == "usdtwd" and fx["period"] == "ts" and fx["panel"]
            and fx["periods"] == ["分時"] and not fx["noteShown"], [fx["index"], fx["period"], fx["periods"], fx["note"]])
    pick(page, "台股")
    back = page.evaluate(STATE)
    j.check(f"{prefix}美元／台幣日線拿不到：切回台股一切照舊", back["title"] == "台股加權" and back["trendShown"] and back["periods"][:2] == ["分時", "60分"],
            [back["title"], back["trendShown"], back["periods"]])
    context.close()


def friend_run(j: Journey, browser, prefix: str, options: dict) -> None:
    context, page = j.home(browser, FRIEND, **options)
    start = page.evaluate(STATE)
    j.check(f"{prefix}朋友（只開站主）：清單沒有那斯達克期貨", start["names"] == ["台股", "道瓊", "那指", "標普", "費半", "日經", "韓綜"], start["names"])
    pick(page, "日經")
    nikkei = page.evaluate(STATE)
    j.check(f"{prefix}朋友：日經照舊只有日週月", nikkei["periods"] == ["日", "週", "月"] and nikkei["period"] == "daily", nikkei["periods"])
    body = page.evaluate("() => fetch('/api/indices/n225/intraday').then(r => r.json())")
    j.check(f"{prefix}朋友：直接問入口也拿不到", body == {"available": False}, body)
    fx = page.evaluate("() => Promise.all([fetch('/api/indices/usdtwd').then(r => r.status), fetch('/api/indices/nope').then(r => r.status),"
                       " fetch('/api/indices/usdtwd/intraday').then(r => r.json())])")
    j.check(f"{prefix}朋友：美元／台幣的日線和不認得的 key 一樣查無、分時拿不到", fx[0] == 404 and fx[1] == 404 and fx[2] == {"available": False}, fx)
    j.check(f"{prefix}朋友：不造成橫向捲動", nikkei["noScroll"])
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed = OUT / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
    live = OUT / "live.json"
    write_fixture(live, 270, False)
    data, dates = fixtures(datetime.now(timezone.utc))
    intraday = OUT / "intraday.json"
    intraday.write_text(json.dumps(data), encoding="utf-8")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_ENABLED_FEATURES": "market_trend_v1", "MARKET_ANA_INDEX_INTRADAY": "owner",
           "JOURNEY_LIVE_FIXTURE": str(live), "JOURNEY_INTRADAY_FIXTURE": str(intraday)}
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
            for prefix, options in [("desktop-", {"viewport": {"width": 1440, "height": 900}}),
                                    ("mobile-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True})]:
                owner_run(j, browser, prefix, options, dates)
                fx_daily_missing_run(j, browser, prefix, options)
                friend_run(j, browser, f"{prefix}friend-", options)
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
