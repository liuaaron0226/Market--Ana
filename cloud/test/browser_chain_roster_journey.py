"""本機真實版圖、維護頁、Access 閘門及 D1 交易，全程使用人工行情與提名。"""

import json
from datetime import datetime, timedelta, timezone

from playwright.sync_api import expect, sync_playwright

import browser_chain_map_journey as j


def fixture_sql():
    flows, _ = j.fixture()
    days = ["2026-11-19", "2026-11-20", "2026-11-23"]
    flows["roster_health"] = {
        "gaps": {"tw:2330": {"days": days, "lastGood": "2026-11-18"}},
        "through": dict.fromkeys(["listed", "otc", "us"], days[-1]),
    }
    nodes = j.DEFINITION["nodes"]
    now = datetime.now(timezone(timedelta(hours=8)))
    quarter = f"{now.year}-Q{(now.month-1)//3+1}"
    proposals = [
        {
            "id": "tw:" + code,
            "kind": "add",
            "market": "tw",
            "code": code,
            "name": name,
            "from": "",
            "to": nodes[0]["id"],
            "role": "零組件供應",
            "reason": "本機測試用提名。",
            "beforeNodes": [],
            "beforePrimary": "",
            "otc": False,
        }
        for code, name in [("9998", "樣本甲公司"), ("9999", "樣本乙公司")]
    ]

    def sql_json(value):
        return "'" + json.dumps(value, ensure_ascii=False).replace("'", "''") + "'"

    return (
        f"INSERT INTO chain_map_flows VALUES ('v','2026-10',{sql_json(flows)},'2026-11-23');\n"
        f"INSERT INTO chain_roster_rounds VALUES ('{quarter}','file',{sql_json(proposals)},'test','2026-11-23');"
    )


def visit(browser, options, label):
    server, base = j.start_server(fixture_sql())
    try:
        context = j.Journey().context(browser, base, j.OWNER, options)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(base + "/chain-map.html")
        page.locator("#rosterEntry").click()
        expect(page.locator("#detail h2")).to_have_text("樣本甲公司")
        expect(page.get_by_role("button", name="同意新增")).to_be_visible()
        page.screenshot(path=str(j.OUT / (label + "-roster.png")))
        page.get_by_role("button", name="同意新增").click()
        expect(page.locator("#detail .state")).to_have_text("已同意")
        page.locator('[data-select="tw:9999"]').click()
        page.get_by_role("button", name="不採用", exact=True).click()
        expect(page.locator("#detail .state")).to_have_text("不採用")
        page.reload()
        page.locator("nav [data-page=members]").click()
        page.get_by_role("searchbox").fill("樣本")
        expect(page.locator(".record")).to_have_count(1)
        expect(page.locator(".record")).to_contain_text("樣本甲公司")
        page.locator("nav [data-page=health]").click()
        expect(page.locator(".healthrow")).to_contain_text("缺資料 · 3 個交易日")
        expect(page.locator(".healthrow")).to_contain_text("2026-11-18")
        assert j.no_hscroll(page)
        page.get_by_role("link", name="查看資金版圖").click()
        page.wait_for_function("() => window.ChainMap?.snapshot().ready")
        if options.get("is_mobile"):
            page.locator("#searchKey").click()
        page.locator("#q").fill("9998")
        expect(page.locator("#results button").first).to_contain_text("樣本甲公司")
        page.locator("#q").fill("2330")
        page.locator("#results button").first.click()
        expect(page.locator("#dMore .roster-gap")).to_have_text("缺資料 · 3 個交易日")
        assert not errors, errors
        context.close()
        print(label, "通過：核准、拒絕、重新載入、生效名單、版圖搜尋及缺行情。")
    finally:
        server.terminate()
        server.wait(timeout=10)


if __name__ == "__main__":
    j.OUT.mkdir(exist_ok=True, parents=True)
    with sync_playwright() as playwright:
        for engine, options, label in [
            (playwright.chromium, j.DESKTOP, "desktop"),
            (playwright.webkit, j.PHONE, "phone"),
        ]:
            browser = engine.launch()
            try:
                visit(browser, options, label)
            finally:
                browser.close()
