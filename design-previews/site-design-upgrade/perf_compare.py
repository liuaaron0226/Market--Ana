"""效能前後對照：同一組情境，改版前（本機預覽 5310）對改版後（樣品 5312，升級層全開）。

量法照 11-24 卡頓修正那一次（.scratch/perf/fix-2026-11-24.md）：手機 390×844、DPR 3、觸控、CDP 把 CPU 降速 4 倍；
「主執行緒 ms/s」＝這段時間裡主執行緒每秒忙幾毫秒（Performance.getMetrics 的 TaskDuration），fps 用 requestAnimationFrame 數。
無頭 Chromium 單次量測有 ±15% 左右的起伏，所以每個情境量兩輪、兩個數字都列出來。不是真機。

本人 11-24 定的底線：放著不動時不能比改版前多；操作中的動畫不低於每秒 50 格。

用法：python design-previews/site-design-upgrade/perf_compare.py [輪數，預設 2]
輸出：.scratch/site-design-upgrade/perf.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch" / "site-design-upgrade" / "perf.json"
ONLY = len(sys.argv) > 2   # 第二個參數有給：只量分類鍵與 K 線換週期兩項（找原因用）
SITES = {"before": "http://127.0.0.1:5310", "after": "http://127.0.0.1:5312"}
READY = """async () => { for (let i = 0; i < 100; i++) { if (window.MinimalApp && window.MinimalApp.ready && document.querySelector('#mainChart canvas')) return true; await new Promise(r => setTimeout(r, 150)); } return false; }"""
METER = r"""
(() => {
  const raf = window.requestAnimationFrame.bind(window);
  window.__meter = {
    start() { this.frames = []; const run = this.run = (this.run || 0) + 1; const tick = t => { if (this.run !== run) return; this.frames.push(t); raf(tick); }; raf(tick); },
    stop() { this.run = (this.run || 0) + 1; const f = this.frames || [], gaps = f.slice(1).map((t, i) => t - f[i]), dur = f.length > 1 ? f[f.length - 1] - f[0] : 0;
      return { fps: dur ? +(1000 * (f.length - 1) / dur).toFixed(1) : 0, over33: gaps.filter(g => g > 33.4).length, over50: gaps.filter(g => g > 50).length, maxGap: Math.round(Math.max(0, ...gaps)) }; },
  };
})();
"""


PARTS = {"TaskDuration": "main_ms_per_s", "ScriptDuration": "script_ms_per_s", "LayoutDuration": "layout_ms_per_s", "RecalcStyleDuration": "style_ms_per_s"}


def task_ms(cdp) -> dict:
    """主執行緒到目前為止忙了幾毫秒：全部、其中跑程式、排版面、算樣式各多少。"""
    values = {m["name"]: m["value"] * 1000 for m in cdp.send("Performance.getMetrics")["metrics"]}
    return {name: values.get(name, 0) for name in PARTS}


def window(page, cdp, act) -> dict:
    """跑一段動作，量這段時間主執行緒每秒忙幾毫秒與 fps。"""
    page.evaluate("() => window.__meter.start()")
    before, started = task_ms(cdp), time.perf_counter()
    act()
    seconds = time.perf_counter() - started
    after = task_ms(cdp)
    meter = page.evaluate("() => window.__meter.stop()")
    return {**{label: round((after[name] - before[name]) / seconds) for name, label in PARTS.items()}, "seconds": round(seconds, 1), **meter}


def tap(page, selector: str, pause: int) -> None:
    box = page.locator(selector).first.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.up()
    page.wait_for_timeout(pause)


def run_once(browser, base: str, upgraded: bool) -> dict:
    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True, color_scheme="light", locale="zh-TW", timezone_id="Asia/Taipei")
    context.add_init_script(METER)
    page = context.new_page()
    # 字型不抓（兩邊一樣），免得網路快慢混進數字
    page.route("**/fonts.googleapis.com/**", lambda route: route.abort())
    page.route("**/fonts.gstatic.com/**", lambda route: route.abort())
    cdp = context.new_cdp_session(page)
    cdp.send("Performance.enable")
    cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
    page.goto(base + "/", wait_until="domcontentloaded")
    page.evaluate(READY)
    page.wait_for_timeout(4000)
    result: dict[str, object] = {"upgrade_on": page.evaluate("() => document.documentElement.classList.contains('upg')")}
    assert result["upgrade_on"] == upgraded, "升級層的開關和預期不同"

    if not ONLY:
        result["閒置（面板都收著）5 秒"] = window(page, cdp, lambda: page.wait_for_timeout(5000))

    cat = lambda name: f"#homeCats button[data-cat='{name}']"
    page.locator(cat("chips")).evaluate("el => window.scrollBy(0, el.getBoundingClientRect().top - innerHeight * .2)")
    page.wait_for_timeout(1500)

    def cats():
        for name in ("chips", "screens", "news", "chips", "screens", "news"):
            tap(page, cat(name), 650)
        tap(page, cat("news"), 650)   # 最後收回
    result["分類鍵連點 7 次（開、換、關）"] = window(page, cdp, cats)

    page.evaluate("() => scrollTo(0, 0)")
    page.wait_for_timeout(800)

    def menus():
        for _ in range(3):
            tap(page, "#toolsKey", 600)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
    if not ONLY:
        result["分析工具選單開關 3 次"] = window(page, cdp, menus)

    def settings():
        for _ in range(3):
            tap(page, "#settingsOpen", 700)
            page.evaluate("() => document.querySelector('#settingsDialog .st-close').click()")
            page.wait_for_timeout(600)
    if not ONLY:
        result["設定開關 3 次"] = window(page, cdp, settings)

    def panel():
        for _ in range(3):
            tap(page, "#priceSummary", 800)
            page.evaluate("() => document.querySelector('#detailPanel .panel-close').click()")
            page.wait_for_timeout(600)
    if not ONLY:
        result["明細板開關 3 次"] = window(page, cdp, panel)

    def period():
        for value in ("weekly", "daily", "monthly", "daily"):
            page.evaluate("v => { const el = document.querySelector('#periodSelect'); el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }", value)
            page.wait_for_timeout(900)
    result["K 線換週期 4 次"] = window(page, cdp, period)

    if not ONLY:
        result["操作完再閒置 5 秒"] = window(page, cdp, lambda: page.wait_for_timeout(5000))
    result["還在跑的動畫"] = page.evaluate("() => { const out = {}; document.getAnimations().forEach(a => { if (a.playState !== 'running') return; const k = a.animationName || a.transitionProperty || 'waapi'; out[k] = (out[k] || 0) + 1; }); return out; }")
    context.close()
    return result


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    report: dict[str, list] = {"before": [], "after": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for index in range(rounds):
            for tag, base in SITES.items():
                report[tag].append(run_once(browser, base, tag == "after"))
                print(f"第 {index + 1} 輪 {tag} 完成", flush=True)
        browser.close()
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    keys = [k for k in report["before"][0] if isinstance(report["before"][0][k], dict) and "fps" in report["before"][0][k]]
    print(f"{'情境':<22} | 改版前 ms/s | 改版後 ms/s | 改版前 fps | 改版後 fps")
    for key in keys:
        col = lambda tag, field: "、".join(str(run[key][field]) for run in report[tag])
        print(f"{key:<22} | {col('before', 'main_ms_per_s'):>10} | {col('after', 'main_ms_per_s'):>10} | {col('before', 'fps'):>9} | {col('after', 'fps'):>9}")
        for part in ("script_ms_per_s", "layout_ms_per_s", "style_ms_per_s"):
            print(f"    {part:<18} | {col('before', part):>10} | {col('after', part):>10}")
    print("wrote", OUT)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
