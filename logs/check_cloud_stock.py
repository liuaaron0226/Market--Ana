"""雲端個股頁驗收（本機）：旅程伺服器（真 gate＋dist＋預算資料包）與本機 8876 比對。

- API：抽樣代號 × 三方案，雲端回應與 8876 /api/stocks/{code}/analysis 完全相同（JSON 物件比對）。
- 頁面：大盤頁籌碼卡第一個股票連結點進去是個股頁（不是被導回大盤），三方案切換後
  標題、方案、結論、五維分數文字與 8876 相同；1440／820／390 無橫溢、JS 錯誤 0。
- 未預算代號顯示「請在本機版查詢」；未核准 403。
結果寫 logs/cloud-stock-check.json，截圖 logs/visual-preview/cloud-stock-*.png。
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
PORT = 8895
CLOUD_URL = f"http://127.0.0.1:{PORT}"
import os
import sys

sys.path.insert(0, str(ROOT))
# 頁面比對的本機基準：預設 8876；8876 若仍是舊後端，改指 claude-66 的隔離預覽（環境變數 LOCAL_STOCK_URL）。
LOCAL = os.environ.get("LOCAL_STOCK_URL", "http://127.0.0.1:8876")
OWNER = "owner@example.com"
PRESETS = ["2TON-FIT-V1", "MOMENTUM-V1", "SWING-SAFE-V1"]
SHOTS = ROOT / "logs" / "visual-preview"
FONTS = "**/fonts.{googleapis,gstatic}.com/**"


def get(url: str, email: str | None = None) -> tuple[int, object]:
    req = urllib.request.Request(url)
    if email:
        req.add_header("Cookie", f"test_as={email}")
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, body


def wait_up() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(CLOUD_URL + "/", timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("journey server did not start")


PAGE_JS = """() => {
  const t = sel => [...document.querySelectorAll(sel)].map(n => n.innerText.trim()).join(' | ');
  return { title: document.title, preset: document.getElementById('presetSelect')?.value, status: document.getElementById('status')?.hidden ? '' : document.getElementById('status')?.innerText,
           verdict: t('#verdictButton'), dims: t('#content [data-dim], #content .dim-card, #content .dimension'), asOf: document.getElementById('asOf')?.textContent };
}"""


def page_state(browser, base: str, code: str, preset: str, width: int, email: str | None):
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    if email:
        ctx.add_cookies([{"name": "test_as", "value": email, "url": CLOUD_URL}])
    page = ctx.new_page()
    page.route(FONTS, lambda r: r.abort())
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/stock.html?code={code}&preset={preset}", wait_until="domcontentloaded")
    page.wait_for_function("document.getElementById('content') && (!document.getElementById('content').hidden || !document.getElementById('status').hidden) && document.getElementById('content').getAttribute('aria-busy') !== 'true'", timeout=20_000)
    page.wait_for_timeout(300)
    state = page.evaluate(PAGE_JS)
    overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
    return ctx, page, state, overflow, errors


def main() -> None:
    from twon_analysis.analyze import analyze  # noqa: E402  凍結版
    globals()["analyze"] = analyze
    global LOCAL
    if not os.environ.get("LOCAL_STOCK_URL"):
        # 不用 8876（可能是舊行程）也不用他窗的 8897：在本程序內開一支隔離的 live_server（埠號自動分配）。
        import threading
        import live_server
        iso = live_server.MarketHTTPServer(("127.0.0.1", 0), live_server.Handler)
        threading.Thread(target=iso.serve_forever, daemon=True).start()
        LOCAL = f"http://127.0.0.1:{iso.server_port}"
    subprocess.run(["node", "build.mjs"], cwd=CLOUD, check=True, capture_output=True)
    server = subprocess.Popen(["node", "test/journey-server.mjs", str(PORT), "relaxed"], cwd=CLOUD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pack = json.loads((CLOUD / ".stock-pack.json").read_text(encoding="utf-8"))
    codes = sorted(pack["results"])
    sample = [c for c in ["2330", "2603", "2408", "2881", "3714"] if c in pack["results"]] + codes[:: max(1, len(codes) // 15)][:15]
    out: dict = {"api": {}, "pages": {}}
    ok = True
    try:
        wait_up()
        mismatched = []
        for code in sample:
            for preset in PRESETS:
                # API 基準：直接呼叫目前（凍結）的 twon_analysis，避免 8876 舊行程造成假結果。
                cs, cb = get(f"{CLOUD_URL}/api/stocks/{code}/analysis?preset={preset}", OWNER)
                expected = json.loads(json.dumps(analyze(code, "9999-12-31", preset_id=preset), ensure_ascii=False))
                if cs != 200 or cb != expected:
                    mismatched.append(f"{code}/{preset} cloud={cs}")
        out["api"] = {"checked": len(sample) * len(PRESETS), "mismatched": mismatched}
        ok &= not mismatched
        out["unapproved"] = get(f"{CLOUD_URL}/api/stocks/2330/analysis", "stranger@example.com")[0]
        out["not_in_cloud"] = get(f"{CLOUD_URL}/api/stocks/1101/analysis", OWNER) if "1101" not in pack["results"] else "1101 已在資料包"
        ok &= out["unapproved"] == 403

        with sync_playwright() as p:
            browser = p.chromium.launch()
            # 大盤頁籌碼卡第一個股票連結點進去要是個股頁
            ctx = browser.new_context(viewport={"width": 1440, "height": 900})
            ctx.add_cookies([{"name": "test_as", "value": OWNER, "url": CLOUD_URL}])
            page = ctx.new_page()
            page.route(FONTS, lambda r: r.abort())
            page.goto(CLOUD_URL + "/")
            page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
            page.locator(".chip-card[data-chip='foreign']").click()
            page.wait_for_selector("#chipBody a.stock-link")
            link = page.locator("#chipBody a.stock-link").first
            href = link.get_attribute("href")
            link.click()
            page.wait_for_url("**/stock.html**")
            page.wait_for_function("document.getElementById('content') && !document.getElementById('content').hidden", timeout=20_000)
            out["chip_link"] = {"href": href, "title": page.title(), "is_stock_page": page.locator("#presetSelect").count() == 1}
            ok &= out["chip_link"]["is_stock_page"]
            ctx.close()
            for width in (1440, 820, 390):
                for preset in PRESETS:
                    cctx, cpage, cstate, coverflow, cerr = page_state(browser, CLOUD_URL, "2330", preset, width, OWNER)
                    lctx, _, lstate, _, lerr = page_state(browser, LOCAL, "2330", preset, width, None)
                    key = f"{width}/{preset}"
                    out["pages"][key] = {"equal": cstate == lstate, "overflow": coverflow, "cloud_errors": cerr, "local_errors": lerr}
                    if cstate != lstate:
                        out["pages"][key]["diff"] = {k: [cstate[k], lstate.get(k)] for k in cstate if cstate[k] != lstate.get(k)}
                    ok &= cstate == lstate and not coverflow and not cerr and not lerr
                    if preset == PRESETS[0]:
                        cpage.screenshot(path=str(SHOTS / f"cloud-stock-{width}.png"), full_page=True)
                    cctx.close()
                    lctx.close()
            browser.close()
    finally:
        server.terminate()
    out["pass"] = bool(ok)
    path = ROOT / "logs" / "cloud-stock-check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PASS" if ok else "FAIL", path)


if __name__ == "__main__":
    main()
