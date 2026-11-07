"""籌碼卡「目前前 10＋連續天數」驗收（本人 11-07）：本機 8876 與雲端旅程伺服器都檢查。

每張卡細部有「目前前 10」區塊，表格列數、第一名代號、連續天數文字與 web/detail-data.js 的 current 一致；
期貨卡顯示空單連續增加天數；1440／390 無橫溢、JS 0。截圖 logs/visual-preview/chip-streaks-*.png。
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
PORT = 8893
TARGETS = {"local": "http://127.0.0.1:8876/minimal.html", "cloud": f"http://127.0.0.1:{PORT}/"}
OWNER = "owner@example.com"
SHOTS = ROOT / "logs" / "visual-preview"
text = (ROOT / "web/detail-data.js").read_text(encoding="utf-8").strip()
CURRENT = json.loads(text[text.index("=") + 1:].strip().rstrip(";"))["current"]
EXPECT = {
    "foreign": ["foreign_buy", "foreign_sell"], "trust": ["trust_buy", "trust_sell"], "margin": ["margin_up", "margin_down"],
    "short": ["short_up", "short_down"], "volume": ["turnover_top"], "futures": ["foreign_sell"],
}


def wait_up(url: str) -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(url, timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("server did not start")


def main() -> None:
    subprocess.run(["node", "build.mjs"], cwd=CLOUD, check=True, capture_output=True)
    server = subprocess.Popen(["node", "test/journey-server.mjs", str(PORT), "relaxed"], cwd=CLOUD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out: dict = {}
    ok = True
    try:
        wait_up(TARGETS["cloud"])
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for target, url in TARGETS.items():
                for width in (1440, 390):
                    ctx = browser.new_context(viewport={"width": width, "height": 900})
                    if target == "cloud":
                        ctx.add_cookies([{"name": "test_as", "value": OWNER, "url": url}])
                    page = ctx.new_page()
                    page.route("**/fonts.{googleapis,gstatic}.com/**", lambda r: r.abort())
                    page.route("**/api/live", lambda r: r.fulfill(status=503, body="{}", content_type="application/json"))
                    errors: list[str] = []
                    page.on("pageerror", lambda e: errors.append(str(e)))
                    page.goto(url, wait_until="domcontentloaded")
                    page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
                    for key, fields in EXPECT.items():
                        page.locator(f".chip-card[data-chip='{key}']").click()
                        page.wait_for_selector("#nowBlock")
                        got = []
                        one = page.locator("#nowBlock .now-table").count() == 1
                        got.append({"default_single_table": one})
                        ok &= one
                        for i, field in enumerate(fields):
                            if i:
                                page.locator(f"#panelBody .chip-seg[data-seg=side] [data-value='{i}']").click()
                            rows = page.locator("#nowBlock .now-table tbody tr")
                            exp = CURRENT[field]
                            first = rows.first.inner_text() if rows.count() else ""
                            streak_txt = f"{exp[0]['streak']} 天" if exp else ""
                            good = rows.count() == len(exp) and (not exp or (exp[0]["code"] in first and streak_txt in first))
                            got.append({"field": field, "rows": rows.count(), "expected": len(exp), "first": first.replace("\t", " ")[:60], "ok": good})
                            ok &= good
                        if key == "futures":
                            lede = page.locator("#nowBlock .now-lede").inner_text()
                            fgood = f"連續增加 {CURRENT['futures_short_growing_days']} 天" in lede
                            got.append({"futures_lede": lede[:60], "ok": fgood})
                            ok &= fgood
                        overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
                        ok &= not overflow
                        out[f"{target}/{width}/{key}"] = {"tables": got, "overflow": overflow}
                        if key == "trust":
                            page.locator("#detailPanel").screenshot(path=str(SHOTS / f"chip-streaks-{target}-{width}-trust.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(250)
                    out[f"{target}/{width}/errors"] = errors
                    ok &= not errors
                    ctx.close()
            browser.close()
    finally:
        server.terminate()
    out["pass"] = bool(ok)
    path = ROOT / "logs" / "chip-streaks-check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PASS" if ok else "FAIL", path)


if __name__ == "__main__":
    main()
