"""票 06 本機驗收：雲端版（cloud/test/journey-server.mjs＋真正的 gate.handle＋cloud/dist）與本機 8876 結果一致。

- 1440／820／390：標題、結論、門檻、劇本三區比例與每組樣本的次數、直白分析、六張籌碼卡每一天的下鑽兩張表前 20 名、
  日K根數與首末根 OHLC、週／月／60分根數、無橫溢、JS 錯誤 0。
- 雲端頁的網路請求只有頁面程式與 /api/market/*，沒有任何 *-data.js；資料檔路徑 404。
- 未登入 401、未核准 403 拿不到市場資料；明細超過限速回 429 時畫面顯示提示；登入失效顯示提示。
結果寫 logs/cloud-market-api-check.json，截圖 logs/visual-preview/cloud-api-*.png。
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CLOUD = ROOT / "cloud"
PORT = 8896
CLOUD_URL = f"http://127.0.0.1:{PORT}"
LOCAL_URL = "http://127.0.0.1:8876/minimal.html"
OWNER = "owner@example.com"
SHOTS = ROOT / "logs" / "visual-preview"
FONTS = "**/fonts.{googleapis,gstatic}.com/**"
KINDS = ["foreign", "trust", "margin", "short", "volume", "futures"]


def wait_up() -> None:
    for _ in range(60):
        try:
            urllib.request.urlopen(CLOUD_URL + "/", timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("journey server did not start")


def status(path: str, email: str | None) -> int:
    req = urllib.request.Request(CLOUD_URL + path)
    if email:
        req.add_header("Cookie", f"test_as={email}")
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code


def open_page(browser, url: str, width: int, email: str | None = None, requests: list[str] | None = None):
    ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme="light")
    if email:
        ctx.add_cookies([{"name": "test_as", "value": email, "url": CLOUD_URL}])
    page = ctx.new_page()
    page.route(FONTS, lambda r: r.abort())
    # 本機 8876 的即時行情與雲端無關（票 07），兩邊都固定成「沒有即時資料」才能比對。
    page.route("**/api/live", lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if requests is not None:
        page.on("request", lambda r: requests.append(r.url))
    page.goto(url, wait_until="domcontentloaded")
    try:
        page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true", timeout=20_000)
    except Exception:
        note = page.evaluate("document.getElementById('serviceNote')?.textContent")
        raise SystemExit(f"{url} 未就緒：serviceNote={note!r} errors={errors}")
    return ctx, page, errors


SNAPSHOT_JS = """() => {
  const t = id => (document.getElementById(id) || {}).textContent || '';
  const c = window.MinimalApp.chart;
  const pick = k => k && { t: k.t, open: k.open, high: k.high, low: k.low, close: k.close, amount: k.amount };
  return {
    conclusion: t('conclusionText'),
    upper: t('lineUpper'),
    lower: t('lineLower'),
    distribution: [...document.querySelectorAll('[data-outcome] [data-share]')].map(n => n.textContent),
    sampleCount: document.getElementById('sampleCount')?.dataset.count,
    readLine: t('readLine'), readList: t('readList'), readDate: t('readDate'),
    chips: [...document.querySelectorAll('.chip-card')].map(n => n.innerText),
    candles: c.candles.length, first: pick(c.candles[0]), last: pick(c.candles.at(-1)),
  };
}"""


def snapshot(page) -> dict:
    return page.evaluate(SNAPSHOT_JS)


def period_counts(page) -> dict:
    out = {}
    for p in ["weekly", "monthly", "h60", "daily"]:
        page.select_option("#periodSelect", p)
        page.wait_for_timeout(150)
        out[p] = page.evaluate("() => { const c = window.MinimalApp.chart; return [c.candles.length, c.candles[0].t, c.candles.at(-1).close]; }")
    return out


def sample_counts(page) -> dict:
    """每組樣本：在面板切換後讀頁面上的次數（本機與雲端都用同一個操作）。"""
    page.locator("#lineLower").click()
    page.locator("#panelBody .more-detail:has(.deep-links) > summary").first.click()
    page.locator("#panelBody [data-deep='samples']").click()
    keys = page.eval_on_selector_all("#panelBody [data-sample]", "ns => ns.map(n => n.dataset.sample)")
    out = {}
    for key in keys:
        page.locator(f"#panelBody [data-sample='{key}']").click()
        out[key] = [page.locator("#sampleCount").get_attribute("data-count"),
                    page.eval_on_selector_all("[data-outcome] [data-share]", "ns => ns.map(n => n.textContent)")]
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    return out


def drill_all(page, settle) -> dict:
    """六張卡 × 每一天：讀明細區文字（含兩張表）。"""
    out = {}
    for key in KINDS:
        page.locator(f".chip-card[data-chip='{key}']").click()
        # 面板開啟動畫期間 visibility 仍是 hidden，inner_text 會讀到空字串；等開好再讀。
        page.wait_for_function("document.getElementById('detailPanel').classList.contains('is-open') && document.getElementById('chipBody') && document.getElementById('chipBody').innerText.trim().length > 0")
        days = [page.locator("#chipBody").inner_text()]
        sides = page.locator("#panelBody .chip-seg[data-seg=side] button")
        for s in range(max(1, sides.count())):
            if sides.count():
                sides.nth(s).click()
                days.append(page.locator("#chipBody").inner_text())
            page.locator("#panelBody .chip-seg[data-seg=view] [data-value=daily]").click()
            bars = page.locator("#panelBody .bar-hit")
            for i in range(bars.count()):
                bars.nth(i).click()
                day = bars.nth(i).get_attribute("data-day")
                md = day[5:].replace("-", "/")
                # 等到明細區換成這一天、且不在載入中（雲端要打 API，只等「沒有載入中」會讀到上一天的舊內容）。
                page.wait_for_function("md => { const d = document.querySelector('#drillDetail'); return d && !d.querySelector('[data-loading]') && (d.querySelector('.drill-day b') || {}).textContent === md; }", arg=md)
                days.append(page.locator("#drillDetail").inner_text())
            page.locator("#panelBody .chip-seg[data-seg=view] [data-value=now]").click()
        out[key] = days
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)
    return out


def local_settle(page) -> None:
    page.wait_for_function("!document.querySelector('#drillDetail [data-loading]')")


def main() -> None:
    subprocess.run(["node", "build.mjs"], cwd=CLOUD, check=True, capture_output=True)
    server = subprocess.Popen(["node", "test/journey-server.mjs", str(PORT), "relaxed"], cwd=CLOUD, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    results: dict = {"widths": {}}
    ok = True
    try:
        wait_up()
        # 擋法：未登入 401；資料檔 404；未核准 403（旅程伺服器的 D1 是空的，非站主即未核准）。
        results["anon_api"] = status("/api/market/overview", None)
        results["unapproved_api"] = status("/api/market/overview", "stranger@example.com")
        results["unapproved_detail"] = status("/api/market/detail?kind=trust&date=2026-11-06", "stranger@example.com")
        results["retired_files"] = {f: status("/" + f, OWNER) for f in ["analysis-data.js", "detail-data.js", "chips-data.js", "dashboard-data.js", "plain-data.js", "market-data-local.js"]}
        ok &= results["anon_api"] == 401 and results["unapproved_api"] == 403 and results["unapproved_detail"] == 403
        ok &= all(v == 404 for v in results["retired_files"].values())

        with sync_playwright() as p:
            browser = p.chromium.launch()
            for width in (1440, 820, 390):
                reqs: list[str] = []
                cctx, cloud, cerr = open_page(browser, CLOUD_URL + "/", width, OWNER, reqs)
                lctx, local, lerr = open_page(browser, LOCAL_URL, width)
                cs, ls = snapshot(cloud), snapshot(local)
                w = {"snapshot_equal": cs == ls, "cloud_js_errors": cerr, "local_js_errors": lerr,
                     "overflow": cloud.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")}
                if cs != ls:
                    w["diff"] = {k: [cs[k], ls[k]] for k in cs if cs[k] != ls.get(k)}
                cloud.screenshot(path=str(SHOTS / f"cloud-api-{width}.png"), full_page=True)
                if width == 1440:
                    w["periods_equal"] = period_counts(cloud) == period_counts(local)
                    w["samples_equal"] = sample_counts(cloud) == sample_counts(local)
                    cd, ld = drill_all(cloud, local_settle), drill_all(local, local_settle)
                    w["drilldown_days"] = {k: len(v) for k, v in cd.items()}
                    w["drilldown_equal"] = cd == ld
                    if cd != ld:
                        w["drilldown_diff"] = [k for k in KINDS if cd[k] != ld[k]]
                        w["drilldown_diff_sample"] = [{"kind": k, "index": i, "cloud": a[:200], "local": b[:200]}
                                                      for k in KINDS for i, (a, b) in enumerate(zip(cd[k], ld[k])) if a != b][:3]
                data_files = [u for u in reqs if u.endswith("-data.js") or "-data.js?" in u]
                w["cloud_data_file_requests"] = data_files
                w["cloud_api_requests"] = sorted({u.split("?")[0].replace(CLOUD_URL, "") for u in reqs if "/api/" in u})
                ok &= w["snapshot_equal"] and not cerr and not lerr and not w["overflow"] and not data_files
                ok &= w.get("periods_equal", True) and w.get("samples_equal", True) and w.get("drilldown_equal", True)
                results["widths"][width] = w
                cctx.close()
                lctx.close()

            # 明細被限速：畫面顯示提示、不當成「沒有資料」。
            ctx, page, errs = open_page(browser, CLOUD_URL + "/", 1440, OWNER)
            page.route("**/api/market/detail**", lambda r: r.fulfill(status=429, body='{"error":"x"}', content_type="application/json"))
            page.locator(".chip-card[data-chip='trust']").click()
            page.locator("#panelBody .chip-seg[data-seg=view] [data-value=daily]").click()
            page.wait_for_selector("#drillDetail [role=alert]")
            results["rate_limited_message"] = page.locator("#drillDetail [role=alert]").inner_text()
            ok &= "太頻繁" in results["rate_limited_message"]
            # 登入失效：Access 回 HTML 登入頁，不是 JSON。
            page.route("**/api/market/detail**", lambda r: r.fulfill(status=200, body="<html>login</html>", content_type="text/html"))
            page.locator("#panelBody .bar-hit").first.click()
            page.wait_for_function("document.querySelector('#drillDetail [role=alert]')?.textContent.includes('登入')")
            results["auth_expired_message"] = page.locator("#drillDetail [role=alert]").inner_text()
            ctx.close()
            # 首頁資料就被擋時（例如被停用）：顯示原因，不出 JS 錯誤。
            ctx = browser.new_context(viewport={"width": 820, "height": 900})
            ctx.add_cookies([{"name": "test_as", "value": OWNER, "url": CLOUD_URL}])
            page = ctx.new_page()
            page.route(FONTS, lambda r: r.abort())
            page.route("**/api/market/overview", lambda r: r.fulfill(status=403, body='{"error":"x"}', content_type="application/json"))
            errs2: list[str] = []
            page.on("pageerror", lambda e: errs2.append(str(e)))
            page.goto(CLOUD_URL + "/")
            page.wait_for_selector("#serviceNote:not([hidden])")
            results["overview_forbidden_message"] = page.locator("#serviceNote").inner_text()
            ok &= "核准" in results["overview_forbidden_message"] and not errs2
            ctx.close()
            browser.close()
    finally:
        server.terminate()
    results["pass"] = bool(ok)
    out = ROOT / "logs" / "cloud-market-api-check.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PASS" if ok else "FAIL", out)


if __name__ == "__main__":
    main()
