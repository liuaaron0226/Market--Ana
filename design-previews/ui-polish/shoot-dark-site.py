"""真正的頁面在四種深色桌面背景下的並排截圖（墨黑、深藍灰、炭褐、石墨）。
自己起預覽伺服器，進頁面前先把主題與背景寫進這台瀏覽器的儲存（和設定浮板存的同一組鍵）。
用法（工作目錄根目錄，先 node cloud/build.mjs）：python design-previews/ui-polish/shoot-dark-site.py [輸出資料夾]"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "3d-redesign"))
import shoot  # noqa: E402

spec = importlib.util.spec_from_file_location("samples", HERE / "shoot-samples.py")
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE.parents[1] / ".tmp" / "ui-polish" / "samples"
OUT.mkdir(parents=True, exist_ok=True)
DESKS = [("", "墨黑（原本）"), ("slate", "深藍灰"), ("umber", "炭褐"), ("graphite", "石墨")]
PAGES = [("stock", f"/stock.html?code={shoot.STOCK}", shoot.ready_stock, "個股頁"), ("home", "/", shoot.ready_home, "首頁"), ("portfolio", "/portfolio.html", shoot.ready_portfolio, "自選股頁")]


def main() -> int:
    sys.argv = [sys.argv[0], "none", str(OUT)]
    samples = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(samples)
    proc, base = shoot.start_server("--owner-extras")
    errors: list[str] = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for name, path, ready, title in PAGES:
                frames = []
                for key, _ in DESKS:
                    context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1.5, is_mobile=True, has_touch=True, reduced_motion="reduce")
                    context.add_init_script(f"try {{ localStorage.setItem('market-theme', 'dark'); localStorage.setItem('market-desk-dark', '{key}'); }} catch (_) {{}}")
                    page = context.new_page()
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(base + path)
                    ready(page)
                    shoot.settle(page)
                    applied = page.evaluate("() => [document.documentElement.dataset.theme, document.documentElement.dataset.desk || '']")
                    if applied != ["dark", key]:
                        errors.append(f"{name} {key}: applied {applied}")
                    if name == "stock":
                        page.evaluate("() => window.scrollTo(0, 260)")
                        page.wait_for_timeout(300)
                    file = OUT / "_frame.png"
                    page.screenshot(path=str(file))
                    frames.append(Image.open(file).convert("RGB").copy())
                    context.close()
                samples.sheet(frames, [label for _, label in DESKS], f"深色桌面背景：{title}（K 線顏色不變，只換底）", OUT / f"dark-site-{name}.png")
            browser.close()
    finally:
        proc.kill()
    for error in errors:
        print("problem:", error)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
