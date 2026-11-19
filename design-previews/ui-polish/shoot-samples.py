"""把樣品頁拍成截幀圖（一個方向一張，橫向排幾格），先給本人看圖、再附可點的頁。

用法（工作目錄根目錄）：python design-previews/ui-polish/shoot-samples.py motion|dark [輸出資料夾]
自己起一個只讀的靜態伺服器（隨機埠），拍完就關。圖不入版控（.gitignore 的 design-previews/**/*.png）。
"""
from __future__ import annotations

import http.server
import socketserver
import sys
import threading
from functools import partial
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
WHAT = sys.argv[1] if len(sys.argv) > 1 else "motion"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / ".tmp" / "ui-polish" / "samples"
OUT.mkdir(parents=True, exist_ok=True)


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401
        pass


def serve() -> tuple[socketserver.TCPServer, str]:
    server = socketserver.TCPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def sheet(frames: list[Image.Image], labels: list[str], title: str, path: Path) -> None:
    gap, head = 16, 64
    width = sum(f.width for f in frames) + gap * (len(frames) + 1)
    height = max(f.height for f in frames) + head + gap + 40
    canvas = Image.new("RGB", (width, height), "#f3f1ec")
    draw = ImageDraw.Draw(canvas)
    try:
        font = ImageFont.truetype("msjh.ttc", 30)
        small = ImageFont.truetype("msjh.ttc", 22)
    except OSError:
        font = small = ImageFont.load_default()
    draw.text((gap, 16), title, fill="#1d1b20", font=font)
    x = gap
    for frame, label in zip(frames, labels):
        canvas.paste(frame, (x, head))
        draw.text((x, head + frame.height + 8), label, fill="#5b5762", font=small)
        x += frame.width + gap
    canvas.save(path)
    print("saved", path)


def motion(page, base: str) -> None:
    def grab() -> Image.Image:
        file = OUT / "_frame.png"
        page.screenshot(path=str(file))
        return Image.open(file).convert("RGB").copy()

    # A：首頁 → 點一檔 → 個股頁；再開浮層
    page.goto(f"{base}/design-previews/ui-polish/motion.html?d=a")
    page.wait_for_timeout(600)
    frames = [grab()]
    page.evaluate("() => document.querySelector('[data-name=\"大立光\"]').click()")
    for wait in (120, 110):
        page.wait_for_timeout(wait)
        frames.append(grab())
    page.wait_for_timeout(600)
    frames.append(grab())
    page.evaluate("() => { document.querySelector('[data-back]:not([hidden])'); [...document.querySelectorAll('[data-back]')].find(b => b.offsetParent).click(); }")
    page.wait_for_timeout(700)
    page.evaluate("() => document.querySelector('[data-sheet]').click()")
    page.wait_for_timeout(130)
    frames.append(grab())
    page.wait_for_timeout(500)
    frames.append(grab())
    sheet(frames, ["首頁", "點「大立光」：新頁推進來", "名稱長成標題", "個股頁", "點「明細」：浮層長出來", "浮層"], "A 接得上：換頁與浮層", OUT / "motion-a.png")

    for which, title, waits, labels in (
        ("b", "B 資料會動：數字滾到位、小圖自己畫出來", (150, 250, 350, 900), ["進場 0.15 秒", "0.4 秒", "0.75 秒", "到位", "模擬更新：數字滾動、細線掃過"]),
        ("c", "C 板有重量：板落到桌上、更新時一道光掃過", (120, 200, 300, 900), ["進場 0.12 秒", "0.3 秒", "0.6 秒", "到位", "模擬更新：光掃過板"]),
    ):
        page.goto(f"{base}/design-previews/ui-polish/motion.html?d={which}")
        frames = []
        page.evaluate(f"() => window.SampleMotion.build('{which}')")
        for wait in waits:
            page.wait_for_timeout(wait)
            frames.append(grab())
        page.evaluate(f"() => window.SampleMotion.update('{which}')")
        page.wait_for_timeout(380)
        frames.append(grab())
        sheet(frames, labels, title, OUT / f"motion-{which}.png")


def main() -> int:
    server, base = serve()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1.5, is_mobile=True, has_touch=True)
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("requestfailed", lambda request: errors.append(f"failed {request.url}"))
            if WHAT == "motion":
                motion(page, base)
            else:
                import importlib.util
                spec = importlib.util.spec_from_file_location("dark_shots", Path(__file__).with_name("shoot-dark.py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.run(page, base, OUT, sheet)
            for error in errors:
                print("page error:", error)
            browser.close()
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
