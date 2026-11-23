"""個股頁「融資券」融資使用率的瀏覽器旅程（本人 2026-11-24：「新增融資使用率在我的籌碼」）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），不連來源、不碰正式站。
本機內建的個股資料包比事實區塊還舊（沒有 facts），所以融資券那一塊在這裡現算：照 2330 實際的 K 棒日期，
用和每日建置同一段程式（twon_analysis.analyze_v2.credit_series → facts._credit_block）從本機的證交所融資原件算出來，
再接到個股分析的回應上。頁面拿到的形狀和正式資料包相同。
先在 cloud/ 跑過 `node build.mjs`。截圖放 .tmp/margin-use-journey/，390 寬的那一張另存 .scratch/margin-utilisation/。

    python -X utf8 cloud/test/browser_margin_use_journey.py

三種情況（手機 390、電腦 1440 各走一次）：
- 2330：有融資使用率——表格下面多一列「融資使用率」，數字是最新一天的餘額 ÷ 限額（這裡另外直接讀原件核對），右邊是走勢小圖。
- 2454：舊資料包（沒有這一項）——那一列不出現，也沒有替代文字。
- 2317：只有最新一天有值——只有數字，不畫走勢。
"""
from __future__ import annotations

import copy
import json
import os
import re
import socket
import subprocess
import sys
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / ".tmp/margin-use-journey"
KEEP = ROOT / ".scratch/margin-utilisation"
OWNER = "owner@example.com"
CODE, OLD_PACK_CODE, LATEST_ONLY_CODE = "2330", "2454", "2317"

STATE = """() => {
  const host = document.getElementById('stockFactsBlocks'), panel = host?.querySelector('.sf-panel'), table = host?.querySelector('.sf-table');
  const row = host?.querySelector('.sf-use');
  const box = e => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, w: b.width, h: b.height }; };
  const spark = row?.querySelector('.sf-spark');
  return {
    shown: Boolean(host) && !host.hidden, pressed: host?.querySelector('.sf-tab[aria-pressed="true"]')?.textContent || '',
    rows: table ? [...table.querySelectorAll('tbody th')].map(th => th.textContent) : [],
    present: Boolean(row), text: row?.textContent || '', name: row?.querySelector('.sf-use-name')?.textContent || '',
    value: row?.querySelector('b')?.textContent || '',
    nameBox: row ? box(row.querySelector('.sf-use-name')) : null, valueBox: row ? box(row.querySelector('b')) : null,
    row: row ? box(row) : null, table: table ? box(table) : null,
    spark: spark ? box(spark) : null, lines: spark ? spark.querySelectorAll('polyline, circle').length : 0,
    tableSparks: table ? [...table.querySelectorAll('.sf-spark')].map(box) : [],
    panelText: panel?.innerText || '',
    fits: document.documentElement.scrollWidth <= innerWidth, panelFits: panel ? panel.scrollWidth <= panel.clientWidth + 1 : false,
  };
}"""


def start_server(port: int) -> subprocess.Popen:
    env = {**os.environ, "MARKET_ANA_STOCK_FACTS": "owner"}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    for _ in range(150):
        try:
            urlopen(f"http://127.0.0.1:{port}", timeout=1); break
        except Exception as error:  # noqa: BLE001 伺服器還沒起來；401 也算起來了
            if getattr(error, "code", None):
                break
            time.sleep(0.2)
    return server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def bar_dates(base: str, code: str) -> list[str]:
    request = Request(f"{base}/api/stocks/{code}/analysis", headers={"Cookie": f"test_as={quote(OWNER)}"})
    with urlopen(request, timeout=60) as response:
        return [bar["date"] for bar in json.load(response)["bars"]]


def real_credit_block(code: str, dates: list[str]) -> dict:
    """和每日建置同一段程式：本機的融資原件 → 融資券那一塊（含融資使用率）。"""
    from twon_analysis import analyze_v2, facts
    return facts._credit_block(analyze_v2.credit_series(code, "上市", dates[-1]), dates)


def rate_from_raw_file(code: str, day: str) -> tuple[str, str]:
    """不經過上面那段程式，直接讀那一天的證交所原件：今日餘額 ÷ 次一營業日限額 →（沒捨入的值, 頁面上的字）。"""
    compact = day.replace("-", "")
    path = next(p for p in (ROOT / f"data/market-snapshots/stock-detail/margin/margin-{compact}.json",
                            ROOT / f"data/margin-history/raw/margin-{compact}.json") if p.exists())
    table = next(t for t in json.loads(path.read_text(encoding="utf-8"))["data"]["tables"] if t.get("fields") and t["fields"][0] == "代號")
    assert table["fields"][6] == "今日餘額" and "限額" in table["fields"][7], table["fields"]
    row = next(r for r in table["data"] if r[0].strip() == code)
    balance, limit = (int(str(r).replace(",", "")) for r in (row[6], row[7]))
    return f"{Decimal(balance) * 100 / Decimal(limit):.20f}", f"{(Decimal(balance) * 100 / Decimal(limit)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}%"


class Journey:
    def __init__(self, base: str) -> None:
        self.base, self.results, self.console, self.facts = base, [], [], {}

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.results.append((name, bool(ok)))
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f"  {detail}" if not ok and detail != "" else ""))

    def context(self, browser, **options):
        context = browser.new_context(**options)
        context.add_cookies([{"name": "test_as", "value": OWNER, "url": self.base}])
        # 外部字型（Google Fonts）在這裡連不到，不擋的話截圖會卡住
        context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())

        def with_facts(route) -> None:
            code = re.search(r"/api/stocks/(\w+)/analysis", route.request.url).group(1)
            response = route.fetch()
            body = response.json()
            if code in self.facts:
                body["facts"] = self.facts[code]
            route.fulfill(response=response, json=body)
        context.route(re.compile(r"/api/stocks/\w+/analysis"), with_facts)
        return context

    def stock(self, context, code: str):
        page = context.new_page()
        page.on("console", lambda m: self.console.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text
                and "ERR_FAILED" not in m.text else None)
        page.on("pageerror", lambda e: self.console.append(f"pageerror: {e}"))
        page.goto(f"{self.base}/stock.html?code={code}", wait_until="domcontentloaded")
        page.locator("#content:not([hidden])").wait_for(state="visible", timeout=60_000)
        page.wait_for_timeout(1200)
        return page


def open_credit(page, touch: bool) -> dict:
    tab = page.locator('#stockFactsBlocks .sf-tab[data-block="credit"]')
    tab.wait_for(state="visible", timeout=10_000)
    tab.scroll_into_view_if_needed()
    tab.tap() if touch else tab.click()
    page.wait_for_timeout(300)
    return page.evaluate(STATE)


def journey(j: Journey, browser, prefix: str, options: dict, expected: str, days: int) -> None:
    touch = bool(options.get("has_touch"))
    context = j.context(browser, **options)

    page = j.stock(context, CODE)
    state = open_credit(page, touch)
    j.check(f"{prefix}融資券那一塊打開了，表格三列照舊", state["shown"] and state["pressed"] == "融資券" and state["rows"][:2] == ["融資", "融券"], state["rows"])
    j.check(f"{prefix}表格下面多一列「融資使用率」，數字是最新一天的餘額 ÷ 限額（{expected}）", state["present"] and state["name"] == "融資使用率"
            and state["value"] == expected, [state["name"], state["value"], expected])
    j.check(f"{prefix}那一列只有名稱與數字，沒有其他說明文字", state["text"] == f"融資使用率{expected}", state["text"])
    j.check(f"{prefix}右邊有走勢小圖（{days} 天）", state["spark"] is not None and state["lines"] >= 1, state)
    if state["present"] and state["spark"] and state["tableSparks"]:
        above = state["tableSparks"][0]
        j.check(f"{prefix}走勢小圖和表格裡的走勢同一欄、同大小", abs(state["spark"]["r"] - above["r"]) <= 1 and abs(state["spark"]["w"] - above["w"]) <= 1
                and abs(state["spark"]["h"] - above["h"]) <= 1, [state["spark"], above])
        j.check(f"{prefix}那一列緊接在表格下面、和表格同寬", abs(state["row"]["t"] - state["table"]["b"]) <= 1 and abs(state["row"]["l"] - state["table"]["l"]) <= 1
                and abs(state["row"]["r"] - state["table"]["r"]) <= 1, [state["row"], state["table"]])
        j.check(f"{prefix}名稱、數字、走勢互不重疊", state["nameBox"]["r"] <= state["valueBox"]["l"] and state["valueBox"]["r"] <= state["spark"]["l"],
                [state["nameBox"], state["valueBox"], state["spark"]])
    else:
        j.check(f"{prefix}走勢小圖和表格裡的走勢同一欄、同大小", False, state)
    j.check(f"{prefix}整頁沒有橫向捲動，這一塊也不用左右捲", state["fits"] and state["panelFits"], state)
    host = page.locator("#stockFactsBlocks")
    host.scroll_into_view_if_needed()
    page.wait_for_timeout(200)
    host.screenshot(path=str(OUT / f"{prefix}margin-block.png"))
    if prefix == "phone-":
        host.screenshot(path=str(KEEP / "margin-block-390.png"))
    page.close()

    page = j.stock(context, OLD_PACK_CODE)
    state = open_credit(page, touch)
    j.check(f"{prefix}舊資料包（沒有這一項）：那一列不出現，也沒有替代文字，表格照常", not state["present"] and "使用率" not in state["panelText"]
            and state["rows"][:2] == ["融資", "融券"] and state["fits"], state["panelText"])
    page.close()

    page = j.stock(context, LATEST_ONLY_CODE)
    state = open_credit(page, touch)
    j.check(f"{prefix}只有最新一天有值：只寫數字，不畫走勢", state["present"] and state["value"] == expected and state["spark"] is None
            and state["text"] == f"融資使用率{expected}" and state["fits"] and state["panelFits"], state)
    host = page.locator("#stockFactsBlocks")
    host.scroll_into_view_if_needed()
    host.screenshot(path=str(OUT / f"{prefix}latest-only.png"))
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    KEEP.mkdir(parents=True, exist_ok=True)
    port = free_port()
    server = start_server(port)
    j = Journey(f"http://127.0.0.1:{port}")
    try:
        dates = bar_dates(j.base, CODE)
        credit = real_credit_block(CODE, dates)
        use = (credit or {}).get("margin_utilisation")
        j.check("本機原件算得出融資使用率（最新值、資料日、走勢）", bool(use) and isinstance(use["value"], float) and len(use["series"]) >= 2, credit)
        if not use:
            return 1
        exact, expected = rate_from_raw_file(CODE, use["data_date"])
        j.check(f"和直接讀 {use['data_date']} 原件算的一樣（{exact[:8]}% → {expected}）", f"{use['value']:.1f}%" == expected, [use["value"], expected])
        j.check("走勢和「融資」那一列同一段日子、最後一點就是最新值", len(use["series"]) == len(credit["rows"][0]["series"])
                and abs(use["series"][-1] - use["value"]) <= 0.05 and use["data_date"] == credit["rows"][0]["data_date"], use)
        j.check("走勢留得比一位小數細（大型股的線才不會是平的）", len(set(use["series"])) > 1, use["series"])
        print(f"     2330 融資使用率 {expected}（資料日 {use['data_date']}，走勢 {len(use['series'])} 天：{use['series'][0]} → {use['series'][-1]}）")
        facts = {"data_date": dates[-1], "today": [], "summary": [], "credit": credit, "blocks": ["credit"]}
        old = copy.deepcopy(facts)
        del old["credit"]["margin_utilisation"]
        latest_only = copy.deepcopy(facts)
        latest_only["credit"]["margin_utilisation"]["series"] = [use["value"]]
        j.facts = {CODE: facts, OLD_PACK_CODE: old, LATEST_ONLY_CODE: latest_only}
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for prefix, options in [("phone-", {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}),
                                    ("desktop-", {"viewport": {"width": 1440, "height": 900}})]:
                journey(j, browser, prefix, options, expected, len(use["series"]))
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
