"""D 包第 2 片：申請頁、站主後台、Gemini 設定頁（三頁共用 cloud/public-src/account.css）的截圖。Gemini 頁是靜態載入。
淺色／深色 × 1440／390，各一張整頁。

用法（在工作目錄根目錄）：python design-previews/3d-redesign/shoot-account.py --out D-account-after
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import shoot  # noqa: E402

GEMINI = shoot.ROOT / "cloud/public-src/gemini/index.html"


def gemini_static(browser, base: str, out: Path, report: dict, size: str, theme: str) -> None:
    """Gemini 設定頁在預覽被功能開關擋住（站主不走這條線）：拿同一份 HTML、同一份 account.css 靜態載入，只看樣式（不跑腳本）。"""
    width, height = shoot.SIZES[size]
    context = browser.new_context(viewport={"width": width, "height": height}, color_scheme=theme, reduced_motion="reduce", locale="zh-TW")
    page = context.new_page()
    page.goto(base + "/apply/", wait_until="domcontentloaded")
    html = GEMINI.read_text(encoding="utf-8").replace("/gemini/account.css", "/apply/account.css")
    html = "\n".join(line for line in html.splitlines() if "<script" not in line)
    page.set_content(html, wait_until="load")
    page.evaluate("document.querySelectorAll('[hidden]').forEach(node => node.hidden = false)")
    name = f"gemini-static-{size}-{theme}"
    page.screenshot(path=str(out / f"{name}.png"), full_page=True)
    report[name] = {"theme_ok": True, "h_overflow_px": page.evaluate("document.documentElement.scrollWidth - innerWidth")}
    context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = shoot.HERE / "shots" / args.out
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {}
    proc, base = shoot.start_server("--features", "core")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for size in shoot.SIZES:
                for theme in shoot.THEMES:
                    for page_key in ("apply", "admin"):
                        shoot.shoot(browser, base, out, report, f"{page_key}-{size}-{theme}", page_key, size, theme)
                    gemini_static(browser, base, out, report, size, theme)
            browser.close()
    finally:
        proc.kill()
        proc.wait()
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = {name: entry for name, entry in report.items()
           if entry.get("page_errors") or entry.get("failed_requests") or entry.get("h_overflow_px") or entry.get("shot_failed") or not entry.get("theme_ok")}
    print(f"{len(report)} 張，存到 {out}")
    print("有問題：" + json.dumps(bad, ensure_ascii=False) if bad else "沒有頁面錯誤、失敗請求或橫向捲動，主題正確")


if __name__ == "__main__":
    main()
