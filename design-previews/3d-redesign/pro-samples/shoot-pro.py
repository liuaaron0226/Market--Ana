"""拍專業圖表樣品（⑥ 大戶持股、③ 庫存總覽這一類）：手機深色與淺色、桌機深色，各拍收合與展開；另外一張「進場 → 點開 → 點一顆」的動圖。
樣品頁自己用 window.SAMPLE = { open: 點哪裡展開, pick: 展開後點哪一顆（沒有就 null） } 告訴這支腳本要點什麼。
用法（在工作目錄根目錄）：python design-previews/3d-redesign/pro-samples/shoot-pro.py <樣品 html 的檔名> <輸出資料夾>"""
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

here = Path(__file__).resolve().parent
sample, out = here / sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
name = sample.stem
STEP = 60  # 動圖一格是幾毫秒
# 把動畫停在某個時間點：一格一格拍，接成動圖（不靠真的時間，才不會每次拍到不一樣）。只動「還沒記下來的」動畫
FREEZE = """ms => document.getAnimations().forEach(a => {
  if (window.__done && window.__done.has(a)) return;
  a.pause(); a.currentTime = ms;
})"""
MARK_DONE = """() => {
  document.getAnimations().forEach(a => { try { a.finish(); } catch (e) {} });
  window.__done = new Set(document.getAnimations().filter(a => a.playState === 'finished'));
}"""
OVERFLOW = "document.documentElement.scrollWidth > document.documentElement.clientWidth"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    def page_for(width, height, theme, motion, frozen=False):
        ctx = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=2, reduced_motion=motion, color_scheme=theme, locale="zh-TW", has_touch=width < 720)
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # 拍動圖時時間是一格一格推的；樣品裡「播完把進場的記號拿掉」的計時器照真的時間走會提早拿掉，所以先讓它不要跑
        if frozen:
            page.add_init_script("window.setTimeout = () => 0;")
        page.goto(sample.as_uri(), wait_until="networkidle")
        page.evaluate("theme => document.documentElement.setAttribute('data-theme', theme)", theme)
        page.wait_for_timeout(300)
        return ctx, page, errors

    for label, width, height, theme in (("phone-dark", 390, 760, "dark"), ("phone-light", 390, 760, "light"), ("desktop-dark", 1100, 600, "dark")):
        ctx, page, errors = page_for(width, height, theme, "reduce")
        spec = page.evaluate("window.SAMPLE")
        box = page.locator("#sample")
        box.screenshot(path=str(out / f"{name}-{label}.png"))
        print(label, "收合高度", box.bounding_box()["height"], "橫向捲動", page.evaluate(OVERFLOW))
        page.locator(spec["open"]).click()
        page.wait_for_timeout(600)
        if spec.get("pick"):
            page.locator(spec["pick"]).click()
            page.wait_for_timeout(500)
        box.screenshot(path=str(out / f"{name}-{label}-open.png"))
        print(label, "展開高度", box.bounding_box()["height"], "橫向捲動", page.evaluate(OVERFLOW), "errors", errors)
        ctx.close()

    # 動圖：進場 → 停一下 → 點開 → 停一下 → 點一顆（有的話）→ 停住
    ctx, page, errors = page_for(390, 760, "dark", "no-preference", frozen=True)
    spec = page.evaluate("window.SAMPLE")
    frames = out / f"{name}-frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("f*.png"):
        old.unlink()
    page.locator(spec["open"]).click()
    page.evaluate(MARK_DONE)
    page.wait_for_timeout(700)
    tall = int(page.locator("#sample").bounding_box()["height"]) + 60   # 動圖的高度照展開之後的高度定
    page.reload(wait_until="networkidle")
    page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    clip = {"x": 0, "y": 0, "width": 390, "height": tall}
    count = 0

    def shot(repeat=1):
        global count
        first = frames / f"f{count:03d}.png"
        page.screenshot(path=str(first), clip=clip)
        count += 1
        for _ in range(repeat - 1):
            shutil.copyfile(first, frames / f"f{count:03d}.png")
            count += 1

    def play(ms):
        for index in range(ms // STEP + 1):
            page.evaluate(FREEZE, index * STEP)
            shot()

    entry = page.evaluate("Math.max(0, ...document.getAnimations().map(a => { const t = a.effect.getComputedTiming(); return Number.isFinite(t.endTime) ? t.endTime : 0; }))")
    print("進場的動畫數", page.evaluate("document.getAnimations().length"), "進場長度（毫秒）", round(entry))
    play(int(entry) + 120)
    page.evaluate(MARK_DONE)
    page.evaluate("document.querySelectorAll('.is-entering').forEach(node => node.classList.remove('is-entering'))")
    shot(repeat=10)
    page.locator(spec["open"]).click()
    page.evaluate(FREEZE, 0)
    play(900)
    page.evaluate(MARK_DONE)
    shot(repeat=18)
    if spec.get("pick"):
        page.locator(spec["pick"]).click()
        page.evaluate(FREEZE, 0)
        play(600)
        page.evaluate(MARK_DONE)
        shot(repeat=24)
    print("errors", errors)
    ctx.close()
    browser.close()

if shutil.which("ffmpeg"):
    first = Image.open(frames / "f000.png")
    width = first.width - first.width % 2
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", f"{1000 / STEP:.3f}", "-i", str(frames / "f%03d.png"), "-vf",
                    f"scale={width}:-2,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4",
                    "-loop", "0", str(out / f"{name}-motion.gif")], check=True)
    print(f"{name}-motion.gif", (out / f"{name}-motion.gif").stat().st_size // 1024, "KB", count, "格")
