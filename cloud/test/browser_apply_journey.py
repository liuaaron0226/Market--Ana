"""註冊＋登入整合成一個入口的瀏覽器旅程（本人 2026-11-20）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），Access 由 cookie 模擬：
有 cookie＝這個信箱已經收過驗證碼、通過 Access。跑兩種 Access 狀態：
  single：申請頁那個 Access 應用已經移除（最後的樣子，整站只有主站的憑證）
  double：主站已改成人人可登入、申請頁那個應用還在（中間狀態，/apply 發的是申請頁的憑證）
先 `node cloud/build.mjs` 產生 dist，再：python cloud/test/browser_apply_journey.py
截圖與報告在 .scratch/unified-login/journey/。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / ".scratch/unified-login/journey"
OWNER = "owner@example.com"
SITE_READY = "[data-settings-open]"       # 主站頁首的設定齒輪：看得到＝真的進站了
BLOCKED = "帳號尚未核准或已停用"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, mode: str) -> None:
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.lines: list[str] = []
        env = {**os.environ, "JOURNEY_PUSH": "test", "JOURNEY_OWNER_DEVICE": "1"}
        if mode == "single":
            env["JOURNEY_ACCESS"] = "single"
        self.proc = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(self.port), "relaxed"], cwd=ROOT / "cloud",
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        threading.Thread(target=self._read, daemon=True).start()
        for _ in range(100):
            try:
                urlopen(self.base + "/", timeout=1)
            except OSError as error:
                if getattr(error, "code", None):
                    break
                time.sleep(0.2)
            else:
                break

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())

    def pushes(self) -> int:
        return sum(1 for line in self.lines if line.startswith("push "))

    def wait_pushes(self, count: int, seconds: float = 5.0) -> int:
        """等伺服器記下的推播數到達 count（stdout 由另一條執行緒讀，不能看一次就算）。"""
        deadline = time.time() + seconds
        while self.pushes() < count and time.time() < deadline:
            time.sleep(0.05)
        return self.pushes()

    def stop(self) -> None:
        self.proc.terminate()


class Journey:
    def __init__(self) -> None:
        self.checks: list[dict[str, object]] = []
        self.errors: list[str] = []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("  ok  " if ok else "  FAIL") + f" {name}" + (f"｜{detail}" if detail != "" and not ok else ""))

    def context(self, browser, base: str, email: str | None, width: int):
        mobile = width < 700
        context = browser.new_context(viewport={"width": width, "height": 844 if mobile else 900}, is_mobile=mobile, has_touch=mobile,
                                      color_scheme="light")
        context.route(lambda url: not url.startswith(base), lambda route: route.abort())
        if email:
            context.add_cookies([{"name": "test_as", "value": quote(email), "url": base}])
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(f"{email}：{error}"))
        return context, page


def fetch_status(page, path: str, method: str = "GET") -> list:
    """用這一頁的登入打一支請求，回 [狀態碼, 回應文字前 200 字]。"""
    return page.evaluate(
        """async ([path, method]) => {
          const r = await fetch(path, { method, credentials: 'same-origin', redirect: 'manual',
            headers: method === 'GET' ? {} : { 'content-type': 'application/json' }, body: method === 'GET' ? undefined : '{}' });
          return [r.status, r.type === 'opaqueredirect' ? 'redirect' : (await r.text()).slice(0, 200)];
        }""", [path, method])


def card(page) -> dict:
    return page.evaluate("""() => {
      const v = document.getElementById('view');
      const link = v.querySelector('.note a');
      return { title: v.querySelector('h1')?.textContent || '', tag: v.querySelector('.state')?.textContent || '',
        who: v.querySelector('.who')?.textContent || '', lead: v.querySelector('.lead')?.textContent || '',
        logout: link ? [link.textContent, link.getAttribute('href')] : null,
        buttons: [...v.querySelectorAll('button, a.btn')].map(b => b.textContent) };
    }""")


def wait_card(page, title: str) -> None:
    page.wait_for_function("t => document.querySelector('#view h1')?.textContent === t", arg=title)


def owner_decides(journey: Journey, browser, base: str, email: str, action: str, width: int, shot: str | None = None) -> None:
    """站主在帳號管理按核准／拒絕／停用（走真的後台畫面）。"""
    context, page = journey.context(browser, base, OWNER, width)
    page.goto(base + "/admin/", wait_until="load")
    page.on("dialog", lambda dialog: dialog.accept())      # 停用前後台會先問一次確定
    if action == "disable":
        page.locator("[data-f='approved']").click()
    row = page.locator(f"article.item[data-email='{email}']")
    row.wait_for()
    if shot:
        page.screenshot(path=str(OUT / shot))
    row.locator(f"[data-act='{action}']").click()
    page.wait_for_function("e => !document.querySelector(`article.item[data-email='${e}']`)", arg=email)
    context.close()


def outsider_gets_nothing(journey: Journey, page, tag: str) -> None:
    results = {path: fetch_status(page, path) for path in
               ["/api/me", "/api/features", "/api/market/overview", "/api/watchlist", "/api/stocks/2330/analysis", "/api/admin/users",
                "/minimal.js", "/minimal.css", "/stock.js", "/admin/admin.js"]}
    journey.check(f"{tag}：主站的 API 與程式檔全部 403「{BLOCKED}」",
                  all(status == 403 and BLOCKED in text for status, text in results.values()), results)
    pages = {path: fetch_status(page, path) for path in ["/", "/minimal.html", "/stock.html?code=2330", "/portfolio.html", "/admin/"]}
    journey.check(f"{tag}：主站每一個頁面都只會被轉走，拿不到頁面內容", all(text == "redirect" for _, text in pages.values()), pages)
    write = fetch_status(page, "/api/watchlist", "POST")
    journey.check(f"{tag}：寫入也是 403", write[0] == 403, write)
    stray = fetch_status(page, "/apply/minimal.html")
    journey.check(f"{tag}：申請頁底下亂打的路徑是 404，不是主站頁面", stray[0] == 404 and "<html" not in stray[1].lower(), stray)


def newcomer_flow(journey: Journey, browser, server: Server, mode: str, width: int) -> None:
    base = server.base
    tag = f"{mode} {width}"
    email = f"new-{mode}-{width}@example.com"
    context, page = journey.context(browser, base, email, width)
    status_reads: list[str] = []          # 這一頁查了幾次自己的狀態（開頁一次，之後每輪自動查一次）
    page.on("request", lambda request: status_reads.append(request.url) if request.url.endswith("/apply/api/me") else None)
    page.clock.install()
    page.goto(base + "/", wait_until="load")
    wait_card(page, "申請使用")
    c = card(page)
    journey.check(f"{tag}：打開首頁、登入後直接看到申請表（網址是 /apply/）", page.url == base + "/apply/" and email in c["who"], [page.url, c])
    journey.check(f"{tag}：申請表有「換一個信箱登入」，連到 Access 的登出",
                  bool(c["logout"]) and c["logout"][0] == "換一個信箱登入" and c["logout"][1].endswith("/cdn-cgi/access/logout"), c)
    overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
    journey.check(f"{tag}：申請表沒有橫向捲動", overflow <= 0, overflow)
    page.screenshot(path=str(OUT / f"{mode}-{width}-1-form.png"))
    outsider_gets_nothing(journey, page, f"{tag} 沒申請過")

    # 深連結（朋友給的是個股頁網址）也一樣落在申請表
    page.goto(base + "/stock.html?code=2330", wait_until="load")
    wait_card(page, "申請使用")
    journey.check(f"{tag}：拿到的是個股頁網址也一樣先到申請表", page.url == base + "/apply/")

    page.locator("#send").click()
    journey.check(f"{tag}：沒填稱呼不會送出", page.locator("#msg").inner_text() == "請填稱呼")
    pushes = server.pushes()
    page.locator("#nick").fill(f"旅程 {tag}")
    page.locator("#send").click()
    wait_card(page, "申請已送出，等待站主核准")
    c = card(page)
    journey.check(f"{tag}：送出後是等待核准，寫明核准後這一頁會自己進站", c["tag"] == "等待核准" and "自己進站" in c["lead"] and bool(c["logout"]), c)
    got = server.wait_pushes(pushes + 1)
    journey.check(f"{tag}：站主的裝置收到一則申請通知", got == pushes + 1, [pushes, got])
    page.screenshot(path=str(OUT / f"{mode}-{width}-2-pending.png"))
    outsider_gets_nothing(journey, page, f"{tag} 等待中")

    # 還沒核准：過了一輪自動查詢（真的有去查），畫面還是等待中，而且沒有重畫（報讀軟體不會每 30 秒重唸、焦點不會掉）
    page.evaluate("() => { document.querySelector('#view h1').dataset.kept = '1'; document.getElementById('again').focus(); }")
    reads = len(status_reads)
    page.clock.run_for(31_000)
    for _ in range(100):
        if len(status_reads) > reads:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(200)
    kept = page.evaluate("() => [document.querySelector('#view h1').dataset.kept || '', document.activeElement?.id || '']")
    journey.check(f"{tag}：還沒核准時自動查了一輪、仍是等待中、畫面沒有重畫",
                  len(status_reads) == reads + 1 and kept == ["1", "again"] and card(page)["tag"] == "等待核准" and page.url == base + "/apply/",
                  [reads, len(status_reads), kept])

    owner_decides(journey, browser, base, email, "approve", width, shot=f"{mode}-{width}-3-admin.png" if width == 390 else None)
    # 不重新登入、不按任何東西：下一輪自動查詢就進站
    page.clock.run_for(31_000)
    page.wait_for_url(base + "/")
    page.wait_for_selector(SITE_READY)
    journey.check(f"{tag}：核准後同一個登入、這一頁自己進站", page.url == base + "/" and page.locator(SITE_READY).is_visible())
    me = fetch_status(page, "/api/me")
    journey.check(f"{tag}：進站後 /api/me 是已核准", me[0] == 200 and '"approved"' in me[1], me)
    page.screenshot(path=str(OUT / f"{mode}-{width}-4-entered.png"))

    # 剛自動進站過又回到申請頁：不再自己跳（防止來回轉），給按鈕
    page.goto(base + "/apply/", wait_until="load")
    wait_card(page, "已經可以進站了")
    journey.check(f"{tag}：剛進站又回到申請頁時不會來回跳，給「進入網站」按鈕", card(page)["buttons"] == ["進入網站"] and page.url == base + "/apply/")
    # 過一陣子，已核准的人再打開舊的申請連結：不停在申請頁，直接進站
    page.clock.run_for(61_000)
    page.goto(base + "/apply/", wait_until="commit")
    page.wait_for_url(base + "/")
    page.wait_for_selector(SITE_READY)
    journey.check(f"{tag}：已核准的人打開舊的申請連結會直接進站", page.url == base + "/")

    # 站主停用：立刻回到申請頁、看到「已停用」，資料拿不到
    owner_decides(journey, browser, base, email, "disable", width)
    page.goto(base + "/", wait_until="load")
    wait_card(page, "這個帳號已被停用")
    journey.check(f"{tag}：被停用後打開首頁看到「已停用」", page.url == base + "/apply/" and card(page)["tag"] == "已停用")
    outsider_gets_nothing(journey, page, f"{tag} 停用後")
    if width == 390:
        page.screenshot(path=str(OUT / f"{mode}-{width}-5-disabled.png"))
    context.close()


def rejected_flow(journey: Journey, browser, server: Server, mode: str, width: int) -> None:
    base = server.base
    tag = f"{mode} {width} 被拒"
    email = f"no-{mode}-{width}@example.com"
    context, page = journey.context(browser, base, email, width)
    page.goto(base + "/", wait_until="load")
    wait_card(page, "申請使用")
    page.locator("#nick").fill("不認識的人")
    page.locator("#send").click()
    wait_card(page, "申請已送出，等待站主核准")
    owner_decides(journey, browser, base, email, "reject", width)
    page.locator("#again").click()
    wait_card(page, "這次的申請沒有通過")
    c = card(page)
    journey.check(f"{tag}：按「重新查看狀態」看到沒有通過，還是可以換信箱", c["tag"] == "未通過" and bool(c["logout"]), c)
    # 被拒的人再送一次申請（直接打 API）：狀態不會變回等待中
    again = page.evaluate("""async () => {
      const r = await fetch('/apply/api/apply', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ nickname: '再一次' }) });
      return [r.status, (await r.json()).status];
    }""")
    journey.check(f"{tag}：再送一次申請不會變回等待中", again == [200, "rejected"], again)
    outsider_gets_nothing(journey, page, tag)
    if width == 390:
        page.screenshot(path=str(OUT / f"{mode}-{width}-6-rejected.png"))
    context.close()


def others(journey: Journey, browser, server: Server, mode: str) -> None:
    base = server.base
    # 沒登入（沒通過 Access）：什麼都沒有
    context, page = journey.context(browser, base, None, 390)
    page.goto(base + "/apply/", wait_until="load")
    body = page.inner_text("body")
    journey.check(f"{mode}：沒登入打開申請頁是「需要登入」，不是申請表", "需要登入" in body and "申請使用" not in body, body[:80])
    context.close()
    # 站主打開申請頁：不用申請
    context, page = journey.context(browser, base, OWNER, 390)
    page.goto(base + "/apply/", wait_until="load")
    wait_card(page, "你是站主")
    c = card(page)
    journey.check(f"{mode}：站主打開申請頁看到「你是站主」與兩個入口", c["buttons"] == ["進入網站", "管理申請"], c)
    page.screenshot(path=str(OUT / f"{mode}-390-7-owner.png"))
    context.close()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    journey = Journey()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for mode in ("single", "double"):
            server = Server(mode)
            try:
                for width in (390, 1440):
                    newcomer_flow(journey, browser, server, mode, width)
                    rejected_flow(journey, browser, server, mode, width)
                others(journey, browser, server, mode)
            finally:
                server.stop()
        browser.close()
    journey.check("頁面程式沒有丟出錯誤", not journey.errors, journey.errors[:5])
    failed = [c for c in journey.checks if not c["ok"]]
    (OUT / "report.json").write_text(json.dumps({"checks": journey.checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(journey.checks) - len(failed)}/{len(journey.checks)} 項通過；截圖與報告在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
