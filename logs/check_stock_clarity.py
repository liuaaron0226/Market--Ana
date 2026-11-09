"""個股頁標示與策略切換改版（本人 11-07 回饋）驗收：旅程伺服器（雲端版）＋截圖。

- 三顆策略按鈕可見、點了會切換（aria-checked）且網址帶 preset；非預設策略會顯示和綜合觀察的差異。
- K 線價位標籤（疊放在右側）彼此不重疊：用 canvas 外部量測不到文字，改由 kchart 記錄的標籤框判斷。
- 1440／390 無橫溢、JS 0；canvas 寬度等於容器（保留先顯示再畫圖的修正）。
截圖 logs/visual-preview/stock-clarity-{寬}-{方案}.png，結果 logs/stock-clarity-check.json。
"""
from __future__ import annotations

# 【已退役 2026-11-08，S3】本腳本驗的是個股頁 v1（三方案、總分、24 招或近期操作卡）。
# 本人定案「自創部分不保留」，個股頁已改用契約 v2；v2 驗收見 logs/check_search_journey.py --case all。
# 檔案保留不刪（刪除需本人確認），執行時直接說明並以非零碼結束，避免被誤當成通過。
raise SystemExit("RETIRED: v1 個股頁檢查已退役，改用 logs/check_search_journey.py --case all")

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CLOUD = ROOT / "cloud"
PORT = 8894
BASE = f"http://127.0.0.1:{PORT}"
OWNER = "owner@example.com"
SHOTS = ROOT / "logs" / "visual-preview"
PRESETS = ["2TON-FIT-V1", "MOMENTUM-V1", "SWING-SAFE-V1"]
CODES = ["3231", "2330"]


def wait_up() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("journey server did not start")


def main() -> None:
    subprocess.run(["node", "build.mjs"], cwd=CLOUD, check=True, capture_output=True)
    server = subprocess.Popen(["node", "test/journey-server.mjs", str(PORT), "relaxed"], cwd=CLOUD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out: dict = {}
    ok = True
    try:
        wait_up()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for width in (1440, 390):
                ctx = browser.new_context(viewport={"width": width, "height": 1000})
                ctx.add_cookies([{"name": "test_as", "value": OWNER, "url": BASE}])
                page = ctx.new_page()
                page.route("**/fonts.{googleapis,gstatic}.com/**", lambda r: r.abort())
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                for code in CODES:
                    page.goto(f"{BASE}/stock.html?code={code}", wait_until="domcontentloaded")
                    page.wait_for_function("!document.getElementById('content').hidden")
                    for preset in PRESETS:
                        page.locator(f"#presetTabs [data-preset='{preset}']").click()
                        page.wait_for_function(f"document.getElementById('content').dataset.preset === '{preset}' && !document.getElementById('content').hidden")
                        page.wait_for_timeout(250)
                        st = page.evaluate("""() => {
                          const c = window.__kchartLabelBoxes || [];
                          const overlap = c.some((a, i) => c.some((b, j) => j > i && a.y < b.y + b.h && b.y < a.y + a.h));
                          const canvas = document.querySelector('#chartHost canvas'), host = document.getElementById('chartHost');
                          return {
                            checked: document.querySelector('#presetTabs [aria-checked=true]')?.dataset.preset,
                            url: location.search, compare: document.getElementById('compareLine').hidden ? '' : document.getElementById('compareLine').textContent,
                            diffs: ['pressure1Diff', 'support1Diff', 'defenseDiff', 'scoreDiff'].map(id => document.getElementById(id).textContent),
                            labels: c.length, overlap, canvasMatches: Math.abs(canvas.getBoundingClientRect().width - host.clientWidth) < 2,
                            overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 };
                        }""")
                        key = f"{width}/{code}/{preset}"
                        out[key] = st
                        # 一進頁就是預設策略，點已選的按鈕不會改網址，所以預設策略允許網址不帶 preset。
                        url_ok = f"preset={preset}" in st["url"] or (preset == "2TON-FIT-V1" and "preset=" not in st["url"])
                        good = st["checked"] == preset and url_ok and not st["overlap"] and st["canvasMatches"] and not st["overflow"] and st["labels"] >= 3
                        if preset != "2TON-FIT-V1":
                            good &= bool(st["compare"])
                        ok &= good
                        if code == CODES[0]:
                            page.locator(".sk-chart-card").screenshot(path=str(SHOTS / f"stock-clarity-{width}-{preset}-chart.png"))
                            if preset == "MOMENTUM-V1":
                                page.screenshot(path=str(SHOTS / f"stock-clarity-{width}-{preset}.png"), full_page=True)
                out[f"{width}/errors"] = errors
                ok &= not errors
                ctx.close()
            browser.close()
    finally:
        server.terminate()
    out["pass"] = bool(ok)
    path = ROOT / "logs" / "stock-clarity-check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PASS" if ok else "FAIL", path)


if __name__ == "__main__":
    main()
