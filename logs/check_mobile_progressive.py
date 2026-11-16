"""手機漸進揭露驗收（本人 11-07「手機螢幕小，很多資訊要先最小化」）。WebKit 390×664，雲端旅程伺服器。

預設收合（看不到）且有入口（點了看得到完整內容）：
- 主圖 MACD：預設不畫；趨勢依據 → 結論細部「多週期趨勢」有各週期 MACD 柱。
- 直白分析 4 個重點與資料日：預設隱藏；點直白分析卡 → 細部有全部重點、要擔心、什麼時候比較安心。
- 籌碼卡小圖與資料日：預設隱藏；點卡 → 細部有目前前 10、每天明細長條圖。
- 籌碼卡細部：預設只一張表；賣超、每天明細用按鈕切。
- 歷史分布條：首頁不顯示；點防守線 → 細部有三區分布。
- K 線十字提示：點圖才出現、放開後收掉，不常駐。
2026-11-08票01：主圖不內嵌MACD，手機及桌面均在下方顯示量能＋MACD，教學預設收合。
桌面1440的直白分析重點與籌碼卡小圖仍顯示。結果 logs/mobile-progressive-check.json。
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
PORT = 8889
BASE = f"http://127.0.0.1:{PORT}/"
OWNER = "owner@example.com"


def wait_up() -> None:
    for _ in range(80):
        try:
            urllib.request.urlopen(BASE, timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.25)
    raise SystemExit("server did not start")


def visible(page, selector: str) -> bool:
    return page.evaluate("s => { const n = document.querySelector(s); if (!n) return false; const r = n.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(n).display !== 'none'; }", selector)


def open_panel(page, selector: str) -> None:
    page.locator(selector).first.click()
    page.wait_for_function("document.getElementById('detailPanel').classList.contains('is-open')")
    page.wait_for_timeout(350)


def close_panel(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_function("!document.getElementById('detailPanel').classList.contains('is-open')")
    page.wait_for_timeout(250)


def main() -> None:
    subprocess.run(["node", "build.mjs"], cwd=ROOT / "cloud", check=True, capture_output=True)
    server = subprocess.Popen(["node", "test/journey-server.mjs", str(PORT), "relaxed"], cwd=ROOT / "cloud", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out: dict = {}
    try:
        wait_up()
        with sync_playwright() as p:
            for engine, w, h in (("webkit", 390, 664), ("webkit", 375, 600), ("chromium", 1440, 900)):
                b = getattr(p, engine).launch()
                ctx = b.new_context(viewport={"width": w, "height": h}, has_touch=w < 761)
                ctx.add_cookies([{"name": "test_as", "value": OWNER, "url": BASE}])
                page = ctx.new_page()
                page.route("**/fonts.{googleapis,gstatic}.com/**", lambda r: r.abort())
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(BASE)
                page.wait_for_function("window.MinimalApp && window.MinimalApp.ready === true")
                page.wait_for_timeout(600)
                r: dict = {"default": {
                    "macd": page.evaluate("window.MinimalApp.chart.showMacd"),
                    "studies_visible": visible(page, '.kc-study-pane-volume canvas') and not visible(page, '.kc-study-pane-macd canvas') and page.locator('.kc-study-pane:visible').count() == 1,
                    "study_teaching_closed": page.locator('#chartStudies details[open]').count() == 0,
                    "read_list": visible(page, "#readList"),
                    "read_date": visible(page, "#readDate"),
                    "chip_spark": visible(page, ".chip-card .spark"),
                    "chip_date": visible(page, ".chip-card .chip-date"),
                    "distribution": visible(page, "#distribution"),
                    "tip_shown": page.evaluate("document.querySelector('#mainChart .kc-tip').classList.contains('show')"),
                    "page_height": page.evaluate("document.documentElement.scrollHeight"),
                }}
                # 入口
                # 頂列「下一交易日」的框與結論依據面板已拿掉（本人 2026-11-15），這個入口不再檢查
                open_panel(page, "#readCard")
                txt = page.locator("#panelBody").inner_text()
                r["entry_plain_full"] = "要擔心" in txt and "什麼時候比較安心" in txt and page.locator("#panelBody .plain-group").count() >= 4
                close_panel(page)
                open_panel(page, ".chip-card[data-chip='trust']")
                r["chip_default_one_table"] = page.locator("#nowBlock .now-table").count() == 1
                page.locator("#panelBody .chip-seg[data-seg=side] [data-value='1']").click()
                r["chip_sell_switch"] = "賣超前 10" in page.locator("#nowBlock").inner_text()
                page.locator("#panelBody .chip-seg[data-seg=view] [data-value=daily]").click()
                r["chip_daily_bars"] = page.locator("#panelBody .bar-hit").count() == 20
                close_panel(page)
                open_panel(page, "#lineLower")
                r["entry_distribution"] = page.locator("#panelBody [data-outcome-panel]").count() == 3
                close_panel(page)
                box = page.locator("#mainChart canvas").bounding_box()
                page.locator("#mainChart canvas").click(position={"x": box["width"] * .5, "y": box["height"] * .3})
                page.wait_for_timeout(200)
                shown = page.evaluate("document.querySelector('#mainChart .kc-tip').classList.contains('show')")
                page.mouse.click(5, 5)
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                r["tip_on_tap"] = shown
                # 現價標籤與右側刻度文字不重疊；最上／最下刻度不被裁切（在畫布高度內）。
                r["price_axis"] = page.evaluate("""() => { const b = window.MinimalApp.chart.debugBoxes; if (!b) return { missing: true };
                    const p = b.price; const overlaps = p ? b.axis.filter(a => a.y < p.y + p.h && p.y < a.y + a.h).map(a => a.v) : [];
                    const clipped = b.axis.filter(a => a.y < 0 || a.y + a.h > b.height).map(a => a.v);
                    return { overlaps, clipped, axis: b.axis.length }; }""")
                pa = r["price_axis"]
                errors_axis = bool(pa.get("missing")) or bool(pa.get("overlaps")) or bool(pa.get("clipped"))
                # 主K 減擠（截圖 8）：手機圖上沒有大塊浮動按鈕蓋住畫布；工具可展開縮放；價位與某日詳情仍可點開。
                r["chart_overlay_buttons_on_canvas"] = page.evaluate("""() => { const c = document.querySelector('#mainChart canvas').getBoundingClientRect();
                    return ['#lineUpper', '#lineLower', '#mobileCandleDetails'].filter(s => { const n = document.querySelector(s); if (!n || n.hidden) return false;
                      const r = n.getBoundingClientRect(); return r.width > 0 && r.bottom > c.top + 1 && r.top < c.bottom - 1; }); }""")
                if w < 761:
                    zoom_hidden = not visible(page, "#zoomIn")
                    page.locator("#chartToolsToggle").click()
                    zoom_open = visible(page, "#zoomIn") and page.locator("#zoomIn").bounding_box()["height"] >= 44
                    before = page.evaluate("window.MinimalApp.chart.view.count")
                    page.locator("#zoomIn").click()
                    zoom_works = page.evaluate("window.MinimalApp.chart.view.count") < before
                    r["tools"] = {"zoom_hidden_by_default": zoom_hidden, "zoom_shown_after_toggle": zoom_open, "zoom_works": zoom_works}
                    page.locator("#lineUpper").click()
                    page.wait_for_function("document.getElementById('detailPanel').classList.contains('is-open')")
                    r["line_button_opens"] = "收復線" in page.locator("#panelTitle").inner_text()
                    close_panel(page)
                    page.locator("#mainChart canvas").click(position={"x": box["width"] * .6, "y": box["height"] * .4})
                    page.wait_for_timeout(250)
                    day_btn = visible(page, "#mobileCandleDetails")
                    if day_btn:
                        page.locator("#mobileCandleDetails").click()
                        page.wait_for_function("document.getElementById('detailPanel').classList.contains('is-open')")
                        r["day_details_opens"] = page.locator("#detailPanel").get_attribute("data-kind") == "candle"
                        page.locator("#panelBack").click()
                        page.wait_for_timeout(300)
                        r["day_details_back_closes"] = not page.evaluate("document.getElementById('detailPanel').classList.contains('is-open')")
                    else:
                        r["day_details_opens"] = False
                    page.screenshot(path=str(ROOT / "logs" / "visual-preview" / f"mobile-chart-{engine}-{w}-tools.png"))
                r["errors"] = errors
                d = r["default"]
                if w < 761:
                    good = (not r["chart_overlay_buttons_on_canvas"] and all(r["tools"].values()) and r["line_button_opens"]
                            and r.get("day_details_opens") and r.get("day_details_back_closes", False))
                    good = good and (d["macd"] is False and not d["read_list"] and not d["read_date"] and not d["chip_spark"] and not d["chip_date"] and not d["distribution"] and not d["tip_shown"])
                else:
                    good = d["macd"] is False and d["read_list"] and d["chip_spark"]
                good = good and d['studies_visible'] and d['study_teaching_closed']
                good = good and not errors_axis and r["entry_plain_full"] and r["chip_default_one_table"] and r["chip_sell_switch"] and r["chip_daily_bars"] and r["entry_distribution"] and not errors
                r["ok"] = bool(good)
                out[f"{engine}-{w}"] = r
                ctx.close()
                b.close()
    finally:
        server.terminate()
    out["pass"] = all(v["ok"] for k, v in out.items() if isinstance(v, dict))
    path = ROOT / "logs" / "mobile-progressive-check.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("PASS" if out["pass"] else "FAIL", path)


if __name__ == "__main__":
    main()
