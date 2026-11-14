"""把首頁「籌碼與資金」六項近 20 天的真實數字取出來（從本機預覽的同一包資料），存成 JSON 給 Blender 的概念樣品用。
用法：python chips_data.py <工作目錄> <輸出 json>"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

root = Path(sys.argv[1])
out = Path(sys.argv[2])
sys.path.insert(0, str(root / "design-previews" / "3d-redesign"))
import shoot  # noqa: E402

EXTRACT = """async () => {
  const ov = await window.MarketData.overview(), ch = ov.chips, daily = ov.analysis.daily;
  const tail = rows => rows.slice(-20);
  return [
    { key: 'foreign', name: '外資現貨', unit: '億', signed: true, values: tail(ch.foreign).map(r => r.net_billion) },
    { key: 'trust', name: '投信', unit: '億', signed: true, values: tail(ch.foreign).map(r => r.trust_billion) },
    { key: 'futures', name: '外資期貨空單', unit: '口', signed: false, values: tail(ch.futures).map(r => Math.abs(r.net_contracts)) },
    { key: 'margin', name: '融資餘額', unit: '億', signed: false, values: tail(ch.margin).map(r => r.margin_balance_billion) },
    { key: 'short', name: '融券餘額', unit: '萬張', signed: false, values: tail(ch.margin).map(r => r.short_units / 10000) },
    { key: 'volume', name: '成交金額', unit: '億', signed: false, values: tail(daily).map(r => r.amount / 1e8) },
  ];
}"""
proc, base = shoot.start_server("--features", "core")
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.clock.set_fixed_time(shoot.FIXED_NOW)
        page.goto(base + "/", wait_until="domcontentloaded")
        shoot.ready_home(page)
        data = page.evaluate(EXTRACT)
        browser.close()
finally:
    proc.kill()
    proc.wait()
out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
for item in data:
    print(item["name"], len(item["values"]), round(item["values"][-1], 1), item["unit"])
