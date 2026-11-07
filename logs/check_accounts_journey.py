"""票 05 瀏覽器旅程（本機）：申請 → 站主核准 → 進站 → 站主停用 → 立即被擋。

用 cloud/test/journey-server.mjs（真正的 gate.handle＋cloud/dist），Access 登入由 cookie 模擬。
三種寬度、封鎖外部字型，結果寫 logs/accounts-journey-check.json，截圖 logs/visual-preview/accounts-*.png。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CLOUD = ROOT / "cloud"
PORT = 8897
BASE = f"http://127.0.0.1:{PORT}"
OWNER = "owner@example.com"
SHOTS = ROOT / "logs" / "visual-preview"
FONT_PATTERN = "**/fonts.{googleapis,gstatic}.com/**"


def wait_up() -> None:
    for _ in range(50):
        try:
            urllib.request.urlopen(BASE + "/", timeout=1)
        except urllib.error.HTTPError:
            return
        except Exception:
            time.sleep(0.2)
    raise SystemExit("journey server did not start")


def as_user(ctx, email: str | None) -> None:
    ctx.clear_cookies()
    if email:
        ctx.add_cookies([{"name": "test_as", "value": email, "url": BASE}])


def overflow(page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth + 1")


def run_width(browser, width: int) -> dict:
    applicant = f"w{width}@example.com"
    res: dict = {"width": width, "errors": []}
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    page.route(FONT_PATTERN, lambda r: r.abort())
    js_errors: list[str] = []
    page.on("pageerror", lambda e: js_errors.append(str(e)))

    as_user(ctx, None)
    r = page.goto(BASE + "/")
    res["anon_home_blocked"] = r.status == 401
    r = page.goto(BASE + "/analysis-data.js")
    res["anon_data_blocked"] = r.status == 401

    as_user(ctx, applicant)
    page.goto(BASE + "/")
    res["unapproved_redirected_to_apply"] = page.url.rstrip("/").endswith("/apply")
    page.wait_for_selector("#nick")
    res["apply_form_shown"] = page.locator("h1").inner_text() == "申請使用"
    res["apply_no_overflow"] = not overflow(page)
    page.screenshot(path=str(SHOTS / f"accounts-apply-{width}.png"), full_page=True)
    page.fill("#nick", f"測試{width}")
    page.click("#send")
    page.wait_for_selector(".state.pending")
    res["pending_shown"] = "等待站主核准" in page.locator("h1").inner_text()
    page.screenshot(path=str(SHOTS / f"accounts-pending-{width}.png"), full_page=True)
    r = page.goto(BASE + "/detail-data.js")
    res["pending_data_blocked"] = r.status == 403
    r = page.goto(BASE + "/admin/")
    res["pending_admin_blocked"] = page.url.rstrip("/").endswith("/apply")

    as_user(ctx, OWNER)
    page.goto(BASE + "/")
    entry = page.locator(".owner-entry")
    entry.wait_for()
    res["owner_entry_on_main_page"] = entry.get_attribute("href") == "/admin/"
    res["owner_entry_shows_pending"] = entry.locator("b").inner_text() == "1"
    page.screenshot(path=str(SHOTS / f"accounts-owner-entry-{width}.png"))
    entry.click()
    page.wait_for_selector(f'[data-email="{applicant}"]')
    res["owner_entry_opens_admin"] = page.url.endswith("/admin/")
    res["admin_lists_applicant"] = True
    res["admin_no_overflow"] = not overflow(page)
    page.screenshot(path=str(SHOTS / f"accounts-admin-{width}.png"), full_page=True)
    page.click(f'[data-email="{applicant}"] [data-act="approve"]')
    page.wait_for_selector(f'[data-email="{applicant}"]', state="detached")
    page.click('[data-f="approved"]')
    res["admin_shows_approved"] = page.locator(f'[data-email="{applicant}"] .state.approved').count() == 1

    as_user(ctx, applicant)
    r = page.goto(BASE + "/")
    page.wait_for_load_state("networkidle")
    res["approved_enters_site"] = r.status == 200 and page.title() == "台股決策鏡"
    page.wait_for_timeout(500)
    res["approved_no_owner_entry"] = page.locator(".owner-entry").count() == 0
    r = page.goto(BASE + "/analysis-data.js")
    res["approved_gets_data"] = r.status == 200
    r = page.goto(BASE + "/api/admin/users")
    res["approved_not_admin"] = r.status == 403

    as_user(ctx, OWNER)
    page.goto(BASE + "/admin/")
    page.click('[data-f="approved"]')
    page.once("dialog", lambda d: d.accept())
    page.click(f'[data-email="{applicant}"] [data-act="disable"]')
    page.wait_for_selector(f'[data-email="{applicant}"]', state="detached")

    as_user(ctx, applicant)
    r = page.goto(BASE + "/analysis-data.js")
    res["disabled_blocked_immediately"] = r.status == 403
    page.goto(BASE + "/")
    page.wait_for_selector(".state.disabled")
    res["disabled_page_shown"] = "停用" in page.locator("h1").inner_text()

    res["errors"] = js_errors
    ctx.close()
    return res


def main() -> int:
    subprocess.run(["node", "build.mjs"], cwd=CLOUD, check=True, capture_output=True)
    SHOTS.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen(["node", "--no-warnings", "test/journey-server.mjs", str(PORT)], cwd=CLOUD)
    try:
        wait_up()
        with sync_playwright() as p:
            browser = p.chromium.launch()
            results = [run_width(browser, w) for w in (1440, 820, 390)]
            browser.close()
    finally:
        server.terminate()
    out = ROOT / "logs" / "accounts-journey-check.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [(r["width"], k) for r in results for k, v in r.items() if v is False or (k == "errors" and v)]
    print(json.dumps(results, ensure_ascii=False))
    print("FAIL" if bad else "PASS", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
