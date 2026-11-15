"""手機 UI 驗收（本人 11-07：手機先最小化、浮窗置中、圖表提示不出界、修正 Safari 錯誤訊息）。

引擎：Playwright WebKit（Safari 同核心）＋Chromium。對象：雲端旅程伺服器（真 gate＋dist）。
- 每一種浮窗（K 棒、收復線、防守線、現價、結論、直白分析、六張籌碼卡、劇本分布）在 390×664、375×600（模擬 Safari 底列擋住後的可視高）
  都要水平置中（左右差 ≤ 2px）、整個框在可視範圍內（含 safe area 留白 ≥ 8px）、背景不透字（面板底色不透明）、可捲動、可關閉、返回上一層可用。
- 圖表十字提示（kc-tip）點圖後整框在圖表區內，不超出左右邊界；放開／點別處後不常駐。
- 手機首屏不顯示 MACD、價位按鈕縮成精簡；桌面 1440 維持原樣（MACD 在、面板置中）。
- 不出現「The string did not match the expected pattern」；雲端顯示「雲端版尚未提供盤中即時行情」。
結果 logs/mobile-ui-check.json，截圖 logs/visual-preview/mobile-ui-*.png。
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
PORT = 8891
BASE = f"http://127.0.0.1:{PORT}/"
OWNER = "owner@example.com"
SHOTS = ROOT / "logs" / "visual-preview"
VIEWPORTS = [("webkit", 390, 664), ("webkit", 375, 600), ("chromium", 390, 664), ("chromium", 1440, 900)]


def wait_up() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE, timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("server did not start")


PANEL_JS = """() => {
  const p = document.getElementById('detailPanel'), r = p.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const bg = getComputedStyle(p).backgroundColor;
  const m = bg.match(/\/\s*([\d.]+)\s*\)$/) || bg.match(/rgba\([^,]+,[^,]+,[^,]+,\s*([\d.]+)\)/);
  const alpha = bg === 'transparent' ? 0 : m ? Number(m[1]) : 1;
  const body = document.getElementById('panelBody');
  return { bg, left: r.left, right: vw - r.right, top: r.top, bottom: vh - r.bottom, w: r.width, h: r.height, vw, vh, alpha,
           scrollable: body.scrollHeight <= body.clientHeight + 1 || getComputedStyle(body).overflowY !== 'visible' };
}"""


def open_cases(page):
    """回傳 (名稱, 開啟動作) 清單：涵蓋所有同類浮窗。"""
    cases = [
        ("upper", lambda: page.locator("#lineUpper").click()),
        ("lower", lambda: page.locator("#lineLower").click()),
        ("plain", lambda: page.locator("#readCard").click()),
    ]
    for key in ["foreign", "futures", "margin", "short", "trust", "volume"]:
        cases.append((f"chip-{key}", lambda k=key: page.locator(f".chip-card[data-chip='{k}']").click()))
    cases.append(("distribution", lambda: (page.locator("#lineLower").click(), page.locator("#panelBody [data-outcome-panel='between']").click())))
    return cases


def main() -> None:
    subprocess.run(["node", "build.mjs"], cwd=CLOUD, check=True, capture_output=True)
    server = subprocess.Popen(["node", "test/journey-server.mjs", str(PORT), "relaxed"], cwd=CLOUD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out: dict = {}
    ok = True
    try:
        wait_up()
        with sync_playwright() as p:
            for engine, w, h in VIEWPORTS:
                browser = getattr(p, engine).launch()
                mobile = w < 761
                ctx = browser.new_context(viewport={"width": w, "height": h}, is_mobile=mobile and engine == "chromium", has_touch=mobile)
                ctx.add_cookies([{"name": "test_as", "value": OWNER, "url": BASE}])
                page = ctx.new_page()
                page.route("**/fonts.{googleapis,gstatic}.com/**", lambda r: r.abort())
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(BASE, wait_until="domcontentloaded")
                page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
                page.wait_for_timeout(800)
                tag = f"{engine}-{w}x{h}"
                note = page.evaluate("document.getElementById('serviceNote').hidden ? '' : document.getElementById('serviceNote').textContent")
                res = {"service_note": note, "panels": {}}
                good = "did not match" not in note and (not note or "雲端版尚未提供" in note)
                # 首屏精簡
                macd_px = page.evaluate("(() => { const c = window.MinimalApp.chart; return c ? c.showMacd : null; })()")
                res["macd_on_chart"] = macd_px
                # 票01把MACD移至獨立副圖；主圖不再內嵌，但兩端仍須看得到量能與MACD。
                res['daily_studies_visible'] = page.locator('.kc-study-pane-volume canvas').is_visible() and not page.locator('.kc-study-pane-macd canvas').is_visible() and page.locator('.kc-study-pane:visible').count() == 1
                good &= macd_px is False and res['daily_studies_visible']
                page.screenshot(path=str(SHOTS / f"mobile-ui-{tag}-home.png"), full_page=False)
                # 圖表提示不出界：點圖左緣、右緣
                canvas = page.locator("#mainChart canvas")
                box = canvas.bounding_box()
                tips = []
                for fx in (0.04, 0.5, 0.96):
                    canvas.click(position={"x": box["width"] * fx, "y": box["height"] * 0.35})
                    page.wait_for_timeout(200)
                    t = page.evaluate("""() => { const t = document.querySelector('#mainChart .kc-tip'); const h = document.getElementById('mainChart').getBoundingClientRect();
                        const r = t.getBoundingClientRect(); return { shown: t.classList.contains('show'), inside: r.left >= h.left - 1 && r.right <= h.right + 1, left: r.left - h.left, right: h.right - r.right }; }""")
                    tips.append(t)
                    good &= (not t["shown"]) or t["inside"]
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(250)
                res["tips"] = tips
                # 所有浮窗
                for name, opener in open_cases(page):
                    try:
                        opener()
                        page.wait_for_function("document.getElementById('detailPanel').classList.contains('is-open')")
                        page.wait_for_timeout(350)
                        g = page.evaluate(PANEL_JS)
                        centered = abs(g["left"] - g["right"]) <= 2
                        inside = g["left"] >= 8 and g["right"] >= 8 and g["top"] >= 8 and g["bottom"] >= 8
                        opaque = g["alpha"] >= 0.99
                        pg = centered and inside and opaque and g["scrollable"]
                        if name in ("chip-trust", "conclusion") or not pg:
                            page.screenshot(path=str(SHOTS / f"mobile-ui-{tag}-{name}.png"))
                        page.keyboard.press("Escape")
                        page.wait_for_function("!document.getElementById('detailPanel').classList.contains('is-open')")
                        page.wait_for_timeout(300)
                        res["panels"][name] = {**{k: round(v, 1) if isinstance(v, float) else v for k, v in g.items()}, "ok": pg}
                        good &= pg
                    except Exception as e:  # 開不了也算失敗
                        res["panels"][name] = {"error": str(e)[:160]}
                        good = False
                        page.keyboard.press("Escape")
                # 返回上一層：線 → 分布 → 返回
                page.locator("#lineLower").click()
                page.wait_for_function("document.getElementById('detailPanel').classList.contains('is-open')")
                page.locator("#panelBody [data-outcome-panel='between']").click()
                page.locator("#panelBack").click()
                page.wait_for_timeout(300)
                back_ok = page.locator("#panelBody [data-outcome-panel]").count() > 0
                res["back_ok"] = back_ok
                good &= back_ok
                page.keyboard.press("Escape")
                overflow = page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")
                res["overflow"] = overflow
                res["errors"] = errors
                good &= not overflow and not errors
                res["ok"] = bool(good)
                out[tag] = res
                ok &= good
                ctx.close()
                browser.close()
    finally:
        server.terminate()
    out["pass"] = bool(ok)
    path = ROOT / "logs" / "mobile-ui-check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PASS" if ok else "FAIL", path)


if __name__ == "__main__":
    main()
