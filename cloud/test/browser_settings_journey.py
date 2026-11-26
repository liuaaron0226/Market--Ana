"""設定浮板的瀏覽器旅程（本人 2026-11-17 選樣品 A：齒輪浮板＋三顆籤）。

用真正的 gate.handle＋cloud/dist＋記憶體資料庫（cloud/test/journey-server.mjs），Access 由 cookie 模擬。
先 `node cloud/build.mjs` 產生 dist，再：python cloud/test/browser_settings_journey.py
截圖與報告在 .scratch/settings-page/journey/。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import gemini_usage  # noqa: E402

OUT = ROOT / ".scratch/settings-page/journey"
OWNER = "owner@example.com"
FRIEND = "friend@example.com"
PAGES = {"home": "/minimal.html", "stock": "/stock.html?code=2330", "portfolio": "/portfolio.html"}


def seed_sql() -> str:
    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    buckets = [
        {"id": "gemini-weekly", "group": "Gemini Models", "window": "weekly", "remaining": 0.9282, "reset_time": iso(now + timedelta(days=4))},
        {"id": "gemini-5h", "group": "Gemini Models", "window": "5h", "remaining": 0.1861, "reset_time": iso(now + timedelta(hours=2))},
        {"id": "gemini-argon-wtu-weekly", "group": "Gemini 4 Argon", "window": "weekly", "remaining": 0.9884, "reset_time": iso(now + timedelta(days=6))},
    ]
    sql = gemini_usage.snapshot_sql(buckets, task="watch-ledger", run_id="1", taken_at=iso(now - timedelta(minutes=5)),
                                    model="gemini-3.8-flash")
    sql += gemini_usage.snapshot_sql(buckets, task="forecast-ledger", run_id="2", taken_at=iso(now - timedelta(minutes=9)),
                                     model="gemini-4-argon")
    # 本人 11-19：籌碼與資金白話（每日更新那支排程）記思考程度、失敗、乾跑（遷移 0029）
    for run_id, outcome, dry in (("3", "failed", False), ("4", "ok", True)):
        sql += gemini_usage.snapshot_sql(buckets, task="daily-update", run_id=run_id, taken_at=iso(now - timedelta(minutes=20)),
                                         model="gemini-4-argon", call={"effort": "medium", "outcome": outcome, "dry_run": dry})
    # 本人 11-19 晚（設定頁 AI 模型）：晨間預測那一班沒讀到設定，設定頁要標出來
    sql += ("INSERT INTO ai_model_reads (task, run_id, read_at, source) VALUES "
            f"('forecast-morning', '5', '{iso(now - timedelta(minutes=30))}', 'unreadable');\n")
    sql += ("INSERT INTO users (email, nickname, status, applied_at) VALUES "
            f"('{FRIEND}', 'F', 'approved', '2026-11-09T00:00:00Z'), ('new@example.com', 'N', 'pending', '2026-11-17T00:00:00Z');\n")
    return sql


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Journey:
    def __init__(self, base: str) -> None:
        self.base = base
        self.checks: list[dict[str, object]] = []
        self.errors: list[str] = []

    def check(self, name: str, ok: bool, detail: object = "") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        print(("  ok  " if ok else "  FAIL") + f" {name}" + (f"｜{detail}" if detail != "" and not ok else ""))

    def page(self, browser, email: str, width: int, path: str):
        mobile = width < 700
        context = browser.new_context(viewport={"width": width, "height": 844 if mobile else 900}, is_mobile=mobile, has_touch=mobile,
                                      color_scheme="light")
        context.route(lambda url: not url.startswith(self.base), lambda route: route.abort())
        context.add_cookies([{"name": "test_as", "value": quote(email), "url": self.base}])
        page = context.new_page()
        page.on("pageerror", lambda error: self.errors.append(f"{path}：{error}"))
        page.goto(self.base + path, wait_until="load")   # 個股頁一直在輪詢行情，等不到網路閒下來
        page.wait_for_timeout(800)
        return context, page


def state(page) -> dict:
    return page.evaluate("""() => {
      const d = document.getElementById('settingsDialog');
      const tabs = [...document.querySelectorAll('#settingsDialog [role=tab]')].map(t => [t.textContent, t.getAttribute('aria-selected')]);
      return { open: !!d?.open, tabs, tabBarHidden: d?.querySelector('.st-tabs')?.hidden,
        locked: getComputedStyle(document.body).position === 'fixed',
        expanded: document.querySelector('[data-settings-open]')?.getAttribute('aria-expanded'),
        desk: getComputedStyle(document.documentElement).getPropertyValue('--desk').trim(),
        theme: document.documentElement.dataset.theme, deskKey: document.documentElement.dataset.desk || '' };
    }""")


def owner_page(journey: Journey, browser, name: str, path: str, width: int) -> None:
    tag = f"站主 {name} {width}"
    context, page = journey.page(browser, OWNER, width, path)
    gear = page.locator("[data-settings-open]")
    journey.check(f"{tag}：頁首有齒輪、沒有舊的主題鍵、鈴鐺與右下浮動鈕",
                  gear.is_visible() and page.locator("#themeToggle, #portfolioTheme, #pushOpen, .owner-entry").count() == 0)
    page.wait_for_function("() => document.querySelector('.st-badge')")
    journey.check(f"{tag}：齒輪標出等待核准的人數", page.locator(".st-badge").inner_text() == "1")
    gear.click()
    page.wait_for_selector("#settingsDialog .st-meter")
    s = state(page)
    journey.check(f"{tag}：打開浮板、四顆籤（多了 AI 模型）、先看用量、底下的頁面鎖住",
                  s["open"] and [t[0] for t in s["tabs"]] == ["Gemini 用量", "AI 模型", "通知", "外觀"] and s["tabs"][0][1] == "true"
                  and s["locked"] and s["expanded"] == "true", s)
    meters = page.eval_on_selector_all("#settingsDialog .st-meter", "ms => ms.map(m => [m.querySelector('.st-meter-name').textContent, m.querySelector('.st-meter-val').textContent, m.hasAttribute('data-low')])")
    journey.check(f"{tag}：Flash 兩條（5 小時偏低標金色）、Argon 一條",
                  meters == [["5 小時", "19%", True], ["本週", "93%", False], ["本週", "99%", False]], meters)
    today = page.eval_on_selector_all("#settingsDialog .st-today li", "ls => ls.map(l => l.innerText.replace(/\\s+/g, ' '))")
    journey.check(f"{tag}：今天各任務用中文名稱與模型", "自選股判斷與提名 Flash 1" in today and "晚間事前預測 Argon 1" in today, today)
    journey.check(f"{tag}：籌碼卡解讀標思考程度、失敗與試跑（試跑不計）",
                  "籌碼卡解讀 失敗 1・試跑 1（不計） Argon・思考中 1" in today, today)
    foot = page.locator("#settingsDialog .st-foot a")
    journey.check(f"{tag}：最下面是帳號管理，再下面是登出（換帳號，本人 11-18）",
                  foot.count() == 2 and foot.nth(0).get_attribute("href") == "/admin/"
                  and foot.nth(1).get_attribute("href").endswith("/cdn-cgi/access/logout") and "登出" in foot.nth(1).inner_text())
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-usage.png"))

    page.get_by_role("tab", name="通知").click()
    focused = page.evaluate("() => document.activeElement?.textContent")
    journey.check(f"{tag}：點了籤之後焦點還在那顆籤上", focused == "通知", focused)
    # 推播畫面先清空、等瀏覽器註冊完推播才畫出開關；等到開關或說明真的畫出來
    page.wait_for_function("() => { const p = document.querySelector('#settings-notify');"
                           " return p.querySelector('.wp-body')?.childElementCount || (p.querySelector('.st-note') && !p.textContent.includes('讀取中')); }")
    notify = page.locator("#settings-notify").inner_text()
    journey.check(f"{tag}：通知籤畫出到價推播的設定", "推播" in notify or "通知" in notify, notify[:60])
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-notify.png"))

    page.get_by_role("tab", name="外觀").click()
    page.locator("[data-desk-set='sand']").click()
    s = state(page)
    journey.check(f"{tag}：選暖沙", s["deskKey"] == "sand" and (name == "portfolio" or s["desk"] == "#e6ddcc"), s)
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-look.png"))
    page.locator("[data-theme-set='dark']").click()
    s = state(page)
    # 本人 2026-11-20：深色也有桌面背景（墨黑、深藍灰、炭褐、石墨），和淺色各記各的
    names = page.locator("[data-desk-set]").all_inner_texts()
    journey.check(f"{tag}：換深色，桌面背景換成深色那四種、選的是墨黑", s["theme"] == "dark" and names == ["墨黑", "深藍灰", "炭褐", "石墨"] and s["deskKey"] == "", (s, names))
    page.locator("[data-desk-set='slate']").click()
    slab = page.evaluate("() => getComputedStyle(document.documentElement).getPropertyValue('--panel').trim()")
    ink = page.evaluate("() => ['--up', '--down', '--accent', '--gold', '--ink'].map(name => getComputedStyle(document.documentElement).getPropertyValue(name).trim())")
    journey.check(f"{tag}：選深藍灰，板的底色換了、K 線的顏色沒動", state(page)["deskKey"] == "slate" and slab == "#151c26" and ink == ["#ff6868", "#2fd09a", "#e8622c", "#d9a94b", "#f2f0ec"], (slab, ink))
    page.screenshot(path=str(OUT / f"owner-{name}-{width}-look-dark.png"))
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)   # close 事件在下一個工作才發
    s = state(page)
    journey.check(f"{tag}：Esc 收回、頁面解鎖", not s["open"] and not s["locked"] and s["expanded"] == "false", s)
    page.reload(wait_until="load")
    page.wait_for_timeout(800)
    s = state(page)
    journey.check(f"{tag}：重新整理後還是深色、深藍灰", s["theme"] == "dark" and s["deskKey"] == "slate", s)
    gear.click()
    page.wait_for_selector("#settingsDialog[open]")
    page.get_by_role("tab", name="外觀").click()
    page.locator("[data-theme-set='light']").click()
    journey.check(f"{tag}：換回淺色，背景回到淺色選的暖沙（各記各的）", state(page)["deskKey"] == "sand", state(page))
    page.locator("[data-theme-set='dark']").click()
    page.locator("[data-desk-set='']").click()   # 深色改回墨黑，免得影響後面的檢查
    page.locator("[data-theme-set='light']").click()
    page.locator("[data-desk-set='']").click()
    s = state(page)
    journey.check(f"{tag}：換回淺色、米白", s["theme"] == "light" and s["deskKey"] == "", s)
    # 點在浮板外面收回（手機是底下升起的板，點上方暗處；桌機點左邊）
    page.mouse.click(10, 10 if width < 700 else 400)
    page.wait_for_timeout(200)
    journey.check(f"{tag}：點外面收回", not state(page)["open"])
    context.close()


def models_rows(page) -> list[str]:
    return page.eval_on_selector_all("#settings-models .st-mrow", "rs => rs.map(r => r.innerText.replace(/\\s+/g, ' ').replace(' ›', ''))")


def owner_models(journey: Journey, browser, width: int, first: bool) -> None:
    """設定頁「AI 模型」（本人 11-19 晚，樣品 C）：每列寫現值、點開才選；改到大盤預測或每日判斷存檔前先問。
    first：第一次走（還沒存過）；第二次（另一個寬度）看上一次存的值重新整理後還在。"""
    tag = f"站主 AI 模型 {width}"
    context, page = journey.page(browser, OWNER, width, PAGES["home"])
    page.locator("[data-settings-open]").click()
    page.wait_for_selector("#settingsDialog[open]")
    page.get_by_role("tab", name="AI 模型").click()
    page.wait_for_selector("#settings-models .st-mrow")
    quota = page.locator("#settings-models .st-quota").inner_text().replace("\n", " ")
    journey.check(f"{tag}：頂端一行 Argon、Flash 本週剩多少", "Argon 本週剩 99%" in quota and "Flash 本週剩 93%" in quota, quota)
    warn = page.locator("#settings-models .st-warn").all_inner_texts()
    journey.check(f"{tag}：晨間預測上一時段沒讀到設定有標出來", any("晨間事前預測上一時段" in w and "沒讀到你的設定" in w for w in warn), warn)
    rows = models_rows(page)
    if not first:
        journey.check(f"{tag}：上次存的值重新整理後還在（大盤預測 Flash 3.8・高、白話 Argon 4・低）",
                      rows[0].endswith("Flash 3.8・高") and rows[3].endswith("Argon 4・低"), rows)
        page.screenshot(path=str(OUT / f"owner-models-{width}.png"))
        context.close()
        return
    journey.check(f"{tag}：六列都是預設值（含首頁消息，本人 11-19）",
                  page.locator('#settings-models .st-mchoice').all_inner_texts() == ["Argon 4・中", "Flash 3.8・高", "Flash 3.8・高", "Argon 4・中", "Argon 4・中", "Flash 3.8・高"]
                  and rows[0].startswith("大盤預測 晚間・晨間・重播一起"), rows)
    journey.check(f"{tag}：還沒改不出現存檔鍵", page.locator("[data-maction]").count() == 0)
    page.screenshot(path=str(OUT / f"owner-models-{width}-start.png"))

    page.locator("[data-mrow='forecast']").click()
    journey.check(f"{tag}：點大盤預測那一列展開兩組鍵",
                  page.locator("#settings-model-forecast-choices").is_visible()
                  and page.locator("[data-mrow='forecast']").get_attribute("aria-expanded") == "true")
    page.locator("[data-mkey='forecast'][data-model='gemini-3.8-flash']").click()
    journey.check(f"{tag}：選 Flash 那一列換成新值（強度自動帶高）、出現還原與存檔、焦點留在 Flash",
                  models_rows(page)[0].endswith("Flash 3.8・高") and page.locator("[data-maction='save']").is_visible()
                  and page.evaluate("() => document.activeElement?.dataset.model") == "gemini-3.8-flash")
    page.locator("[data-maction='save']").click()
    confirm = page.locator("#settings-models .st-confirm").inner_text()
    journey.check(f"{tag}：存檔前先問（成績另開一組、正式起算重數）", "大盤預測改成 Flash 3.8・高" in confirm and "連續 5 天" in confirm, confirm)
    page.screenshot(path=str(OUT / f"owner-models-{width}-confirm.png"))
    page.locator("[data-maction='cancel']").click()
    journey.check(f"{tag}：取消回到還沒存的樣子", page.locator(".st-confirm").count() == 0 and page.locator("[data-maction='save']").is_visible())
    page.locator("[data-maction='save']").click()
    page.locator("[data-maction='confirm']").click()
    page.wait_for_selector("#settings-models .st-saved")
    journey.check(f"{tag}：確認變更之後存好", "已存檔" in page.locator(".st-saved").inner_text()
                  and page.locator("[data-maction]").count() == 0 and models_rows(page)[0].endswith("Flash 3.8・高"))

    page.locator("[data-mrow='plain']").click()
    page.locator("[data-mkey='plain'][data-effort='low']").click()
    page.locator("[data-maction='save']").click()
    page.wait_for_function("() => document.querySelector('#settings-models .st-saved')?.textContent.includes('已存檔')"
                           " && !document.querySelector('[data-maction]')")
    journey.check(f"{tag}：白話改思考低，不用確認直接存", page.locator(".st-confirm").count() == 0 and models_rows(page)[3].endswith("Argon 4・低"))
    page.screenshot(path=str(OUT / f"owner-models-{width}-saved.png"))
    context.close()


def dynamic_models(journey: Journey, browser, width: int, state_file: Path) -> None:
    tag = f'動態模型 {width}'
    context, page = journey.page(browser, OWNER, width, '/minimal.html#ai-models')
    page.wait_for_selector('#settings-models .st-mrow')
    page.locator('[data-mrow="digest"]').click()
    choices = page.locator('#settings-model-digest-choices')
    general = choices.get_by_role('group', name='今日 AI 解讀的模型', exact=True)
    journey.check(f'{tag}：主清單只有五款一般模型並在名稱後標版本',
                  general.get_by_role('button').all_inner_texts() == ['Neon 4', 'Carbon 4', 'Argon 4', 'Hydrogen 4', 'Flash 3.8'])
    advanced = choices.locator('details')
    journey.check(f'{tag}：進階選項預設收合', advanced.count() == 1 and not advanced.evaluate('(d) => d.open'))
    assert advanced.count() == 1, 'Orchestrator 需要獨立的進階選項'
    before = page.locator('[data-mrow="digest"]').inner_text()
    summary = advanced.locator('summary')
    summary.focus()
    page.keyboard.press('Enter')
    journey.check(f'{tag}：鍵盤展開進階後才看得到三個Orchestrator變體',
                  advanced.evaluate('(d) => d.open') and advanced.get_by_role('button').all_inner_texts()
                  == ['Orchestrator (Neon)', 'Orchestrator (Carbon)', 'Orchestrator (Argon)'])
    journey.check(f'{tag}：展開進階不改原模型或產生待存變更',
                  page.locator('[data-mrow="digest"]').inner_text() == before and page.locator('[data-maction="save"]').count() == 0)
    page.keyboard.press('Space')
    journey.check(f'{tag}：鍵盤可收回進階而且三個變體不在主清單',
                  not advanced.evaluate('(d) => d.open') and not advanced.get_by_role('button').first.is_visible())
    page.screenshot(path=str(OUT / f'models-primary-{width}.png'))
    page.locator('[data-mkey="digest"][data-model="gemini-4-neon"]').click()
    page.locator('[data-mkey="digest"][data-effort="xhigh"]').click()
    journey.check(f'{tag}：新模型可選極高，未知用量不捏造', '未知' in page.locator('#settings-model-digest-choices').inner_text())
    page.locator('[data-maction="save"]').click()
    page.wait_for_selector('#settings-models .st-saved')
    page.reload(wait_until='load')
    page.wait_for_selector('#settings-models .st-mrow')
    journey.check(f'{tag}：Neon極高手動選擇重新整理後保留', 'Neon 4・極高' in page.locator('[data-mrow="digest"]').inner_text())
    page.locator('[data-mrow="digest"]').click()
    advanced = page.locator('#settings-model-digest-choices details')
    advanced.locator('summary').click()
    page.locator('[data-mkey="digest"][data-effort="high"]').click()
    journey.check(f'{tag}：調思考強度後進階展開狀態仍保留', advanced.evaluate('(d) => d.open'))
    page.locator('[data-mkey="digest"][data-model="gemini-pro-orchestrator-neon"]').click()
    efforts = page.locator('[data-mkey="digest"][data-effort]').all_inner_texts()
    journey.check(f'{tag}：Orchestrator只顯示官方自動強度', efforts == ['模型自動'], efforts)
    page.locator('[data-maction="save"]').click()
    page.wait_for_selector('#settings-models .st-saved')
    page.reload(wait_until='load')
    page.wait_for_selector('#settings-models .st-mrow')
    journey.check(f'{tag}：已存Orchestrator重新整理後仍是原選擇',
                  page.locator('[data-mrow="digest"]').inner_text().endswith('Orchestrator (Neon)'))
    page.locator('[data-mrow="digest"]').click()
    advanced = page.locator('#settings-model-digest-choices details')
    journey.check(f'{tag}：既有進階選擇自動展開且選取鍵可見',
                  advanced.evaluate('(d) => d.open')
                  and advanced.locator('[data-model="gemini-pro-orchestrator-neon"][aria-pressed="true"]').is_visible()
                  and page.locator('[data-maction="save"]').count() == 0)
    at = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    state_file.write_text(json.dumps({'ai-models/resolutions/digest.json': {
        'requested': {'model':'gemini-pro-orchestrator-neon','effort':'default'},
        'actual': {'model':'gemini-4-neon','effort':'medium'}, 'state':'replaced',
        'reason':'officially_unavailable', 'checked_at':at}}), encoding='utf-8')
    page.reload(wait_until='load')
    page.wait_for_selector('#settings-models .st-mrow')
    warning = page.locator('#settings-models .st-warn').all_inner_texts()
    journey.check(f'{tag}：原選擇保留且看得到替代原因與實際模型',
                  'Orchestrator' in page.locator('[data-mrow="digest"]').inner_text()
                  and any('已停用' in w and 'Neon 4・中' in w for w in warning), warning)
    page.locator('[data-mrow="digest"]').click()
    page.screenshot(path=str(OUT / f'dynamic-models-{width}.png'))
    page.wait_for_timeout(300)  # Existing pane entrance animation must finish before measuring layout.
    size = page.evaluate('() => { const d=document.querySelector("#settings-models"); return {scroll:d.scrollWidth, client:d.clientWidth, wide:[...d.querySelectorAll("*")].filter(e=>e.getBoundingClientRect().right>d.getBoundingClientRect().right+1).map(e=>[e.className,e.tagName,e.getBoundingClientRect().width])}; }')
    journey.check(f'{tag}：動態選項不橫向溢出', size['scroll'] <= size['client'] + 1, size)
    page.locator('[data-mkey="digest"][data-model="gemini-4-argon"]').click()
    page.locator('[data-maction="save"]').click()
    page.wait_for_selector('#settings-models .st-saved')
    journey.check(f'{tag}：替代之後仍能手動改選並清除舊告警',
                  'Argon 4・中' in page.locator('[data-mrow="digest"]').inner_text()
                  and not any('已停用' in w for w in page.locator('#settings-models .st-warn').all_inner_texts()))
    state_file.write_text('{}', encoding='utf-8')
    context.close()


def friend_page(journey: Journey, browser, width: int) -> None:
    tag = f"朋友 home {width}"
    context, page = journey.page(browser, FRIEND, width, PAGES["home"])
    page.locator("[data-settings-open]").click()
    page.wait_for_selector("#settingsDialog[open]")
    page.wait_for_timeout(300)
    s = state(page)
    foot = page.locator("#settingsDialog .st-foot a")
    journey.check(f"{tag}：只有外觀，不顯示籤列，也沒有帳號管理；最下面只有登出（換帳號，本人 11-18）",
                  s["tabBarHidden"] and page.locator("#settings-look").is_visible() and foot.count() == 1
                  and foot.first.get_attribute("href").endswith("/cdn-cgi/access/logout"), s)
    page.screenshot(path=str(OUT / f"friend-home-{width}.png"))
    context.close()


def wait_for(base: str) -> None:
    for _ in range(100):
        try:
            urlopen(base + "/", timeout=1)
        except OSError as error:
            if getattr(error, "code", None):
                return
            time.sleep(0.2)
        else:
            return


# ---------- 資料更新狀態（2026-11-24）：伺服器的「現在」固定在週二 11-24 22:00（台北），三種樣子各起一次伺服器 ----------
HEALTH_NOW = "2026-11-24T22:00:00+08:00"
HEALTH_OUT = ROOT / "design-previews/data-health"
SHA = "a" * 64


def health_seed(scenario: str) -> str:
    """ok：全部正常；late：每日行情 11-24 那一班過了 21:30 還沒更新；degraded：盤勢解讀改用程式套句。"""
    day = "2026-11-23" if scenario == "late" else "2026-11-24"
    updated = "2026-11-23T10:12:00Z" if scenario == "late" else "2026-11-24T10:12:00Z"
    brief_source = "template" if scenario == "degraded" else "ai"
    # 和正式資料庫同一個樣子：資料日在版本名裡，source_date 是名錄抓取日（不是資料日）
    version = f"liquid-{day.replace('-', '')}-237d4b8dd208-v2"
    return f"""
INSERT INTO users (email, nickname, status, applied_at) VALUES ('{FRIEND}', 'F', 'approved', '2026-11-09T00:00:00Z');
INSERT INTO stock_data_versions (version, source_date, source_url, source_sha256, built_at, catalog_count, analysis_count)
  VALUES ('{version}', '2026-11-08', 'x', '{SHA}', '{updated}', 1, 1);
INSERT INTO stock_data_state (key, active_version, updated_at) VALUES ('current', '{version}', '{updated}');
INSERT INTO fund_securities (version, code, name, full_name, currency, latest_bar_date, available_bars, analysis_status) VALUES
  ('{version}', 'BLKTENAU', 'n', 'n', 'USD', '2026-11-21', 10, 'available'), ('{version}', 'GLIACU', 'n', 'n', 'USD', '2026-11-21', 10, 'available');
INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, outcome, dry_run) VALUES ('daily-update', '9', '{updated[:11]}10:05:00Z', 'ok', 0);
INSERT INTO market_news_runs (run_id, trade_date, slot, created_at, status) VALUES ('2026-11-24-close', '2026-11-24', 'close', '2026-11-24T06:08:00Z', 'ok');
INSERT INTO market_brief_runs (trade_date, slot, generated_at, source, payload) VALUES ('2026-11-24', 'close', '2026-11-24T06:20:00Z', 'ai', '{{}}'),
  ('2026-11-24', 'evening', '2026-11-24T10:40:00Z', '{brief_source}', '{{}}');
INSERT INTO forecast_records (forecast_id, base_date, edition, method_version, input_version, run_mode, kind, record_json, record_sha256, published_at)
  VALUES ('f1', '2026-11-24', 'evening', 'm', 'i', 'official', 'forecast', '{{}}', '{SHA}', '2026-11-24T10:35:00Z');
INSERT INTO watch_judgments (judgment_id, code, base_date, edition, method_version, input_version, run_mode, kind, status, record_json, record_sha256, published_at)
  VALUES ('j1', '2330', '2026-11-24', 'evening', 'm', 'i', 'official', 'judgment', '還沒到', '{{}}', '{SHA}', '2026-11-24T10:50:00Z');
INSERT INTO impact_watch_facts (subject, data_date, payload, updated_at) VALUES ('market', '{day}', '{{}}', '{updated}');
INSERT INTO broker_branch_stocks (code, updated_through, last_attempt_at) VALUES ('2330', '{day}', '2026-11-24T11:20:00Z');
INSERT INTO watch_news_fetches (source, code, last_attempt_at, last_success_at) VALUES ('google', '2330', '2026-11-24T10:10:00Z', '2026-11-24T10:10:00Z');
INSERT INTO overseas_fetch_status (symbol, source, attempted_at, ok, succeeded_at) VALUES ('NVDA', 'massive', '2026-11-24T10:15:00Z', 1, '2026-11-24T10:15:00Z');
"""


def health_rows(page) -> list[list[str]]:
    return page.eval_on_selector_all("#settings-health li", "ls => ls.map(l => [l.dataset.health, l.dataset.state,"
                                     " l.querySelector('.st-htime').textContent, l.querySelector('.st-hstate').textContent,"
                                     " l.querySelector('.st-hname small')?.textContent || ''])")


def health_journey(browser_type) -> list[dict]:
    """設定頁「資料更新」那一籤：三種樣子、手機 390×844 與桌機 1280×800，截圖放 design-previews/data-health/。"""
    HEALTH_OUT.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []
    expected = {"ok": None, "late": ("daily", "late", "延遲", "應於 11-24 21:30 前更新"),
                "degraded": ("market_brief", "degraded", "部分異常", "改用程式套句")}
    for scenario, problem in expected.items():
        seed = Path(tempfile.mkdtemp()) / "health.sql"
        seed.write_text(health_seed(scenario), encoding="utf-8")
        port = free_port()
        env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_DATA_HEALTH": "owner", "JOURNEY_NOW": HEALTH_NOW}
        server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
        base = f"http://127.0.0.1:{port}"
        wait_for(base)
        journey = Journey(base)
        try:
            browser = browser_type.launch()
            for width, height in ((390, 844), (1280, 800)):
                tag = f"資料更新 {scenario} {width}"
                mobile = width < 700
                context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile, color_scheme="light")
                context.route(lambda url: not url.startswith(base), lambda route: route.abort())
                context.add_cookies([{"name": "test_as", "value": quote(OWNER), "url": base}])
                page = context.new_page()
                page.on("pageerror", lambda error: journey.errors.append(str(error)))
                page.goto(base + "/minimal.html#data-health", wait_until="load")
                page.wait_for_selector("#settings-health li", timeout=10_000)
                tabs = page.locator("#settingsDialog [role=tab]").all_inner_texts()
                journey.check(f"{tag}：推播帶的 #data-health 直接打開「資料更新」那一籤（排在 Gemini 用量後面）",
                              tabs[:3] == ["Gemini 用量", "資料更新", "AI 模型"] and tabs[-1] == "外觀"
                              and page.locator("#settings-tab-health").get_attribute("aria-selected") == "true", tabs)
                rows = health_rows(page)
                keys = [row[0] for row in rows]
                journey.check(f"{tag}：一份資料一列（沒有紀錄的不列）", keys == ["daily", "plain", "market_news", "market_brief", "forecast", "watch",
                                                                   "impact_watch", "broker_branches", "headlines", "overseas", "fund_nav"], keys)
                bad = [row for row in rows if row[1] != "ok"]
                if problem is None:
                    journey.check(f"{tag}：全部正常，沒有說明小字", not bad and all(row[3] == "正常" and row[4] == "" for row in rows), bad)
                else:
                    journey.check(f"{tag}：只有那一列有問題，寫出狀態與怎麼了", [row[0:2] + row[3:] for row in bad] == [list(problem[0:2]) + list(problem[2:])], bad)
                daily = next(row for row in rows if row[0] == "daily")
                journey.check(f"{tag}：每日行情的最後更新時間（台北）", daily[2].endswith("18:12"), daily)
                fund = next(row for row in rows if row[0] == "fund_nav")
                journey.check(f"{tag}：基金淨值只有淨值日", fund[2] == "11-21", fund)
                overflow = page.evaluate("() => document.querySelector('#settingsDialog').scrollWidth > document.querySelector('#settingsDialog').clientWidth")
                journey.check(f"{tag}：浮板沒有左右捲動", not overflow)
                page.screenshot(path=str(HEALTH_OUT / f"{scenario}-{width}x{height}.png"))
                context.close()
            # 朋友：沒有這一籤，入口也拿不到
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_cookies([{"name": "test_as", "value": quote(FRIEND), "url": base}])
            page = context.new_page()
            body = page.goto(base + "/api/settings/data-health").json()
            journey.check(f"資料更新 {scenario}：朋友拿不到", body == {"available": False}, body)
            context.close()
            browser.close()
        finally:
            server.terminate()
        journey.check(f"資料更新 {scenario}：頁面程式沒有丟出錯誤", not journey.errors, journey.errors[:3])
        checks.extend(journey.checks)
    return checks


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seed = Path(tempfile.mkdtemp()) / "seed.sql"
    seed.write_text(seed_sql(), encoding="utf-8")
    port = free_port()
    state_file = seed.with_name('model-state.json')
    state_file.write_text('{}', encoding='utf-8')
    env = {**os.environ, "JOURNEY_SEED_SQL": str(seed), "MARKET_ANA_WATCHLIST_WATCH": "owner", "JOURNEY_PUSH": "test", 'JOURNEY_MODEL_STATE': str(state_file)}
    server = subprocess.Popen(["node", str(ROOT / "cloud/test/journey-server.mjs"), str(port), "relaxed"], cwd=ROOT / "cloud", env=env)
    base = f"http://127.0.0.1:{port}"
    wait_for(base)
    if os.environ.get("JOURNEY_ONLY") == "health":   # 只跑資料更新那一段
        server.terminate()
        with sync_playwright() as playwright:
            checks = health_journey(playwright.chromium)
        failed = [c for c in checks if not c["ok"]]
        print(f"{len(checks) - len(failed)}/{len(checks)} 項通過；截圖在 {HEALTH_OUT}")
        return 1 if failed else 0
    journey = Journey(base)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width in (390, 1440):
                for name, path in PAGES.items():
                    owner_page(journey, browser, name, path, width)
                owner_models(journey, browser, width, first=width == 390)
                dynamic_models(journey, browser, width, state_file)
                friend_page(journey, browser, width)
            browser.close()
    finally:
        server.terminate()
    with sync_playwright() as playwright:
        journey.checks.extend(health_journey(playwright.chromium))
    journey.check("頁面程式沒有丟出錯誤", not journey.errors, journey.errors[:5])
    failed = [c for c in journey.checks if not c["ok"]]
    (OUT / "report.json").write_text(json.dumps({"checks": journey.checks}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(journey.checks) - len(failed)}/{len(journey.checks)} 項通過；截圖與報告在 {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
