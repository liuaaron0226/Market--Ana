"""量「首頁 → 自選股」要多久（本人 2026-11-20：「從主要切換到自選股會卡頓」）。

自己起預覽伺服器（隨機埠、樣本資料），用 Chromium 的手機尺寸 390×844，並把網路與處理器調成接近手機：
每個請求多 150 毫秒來回、下載 9 Mbps、處理器放慢 4 倍。伺服器照正式站的 `_headers` 送快取標頭。
先開一次首頁與自選股頁（等於「不是第一次來」），之後每一輪都是：回首頁 → 點右上角星星 → 量到清單第一列出現。

量的東西（都從點下去那一刻起算，毫秒）：
  ttfb       頁面本身開始回來
  fcp        畫面第一次有東西
  rows       清單第一列出現（這一頁可以用了）
  assets     這一趟重新向伺服器要的 css／js 檔數
  api_chain  自選股頁那幾支資料請求，最多排了幾層隊（1 表示全部同時發）

用法（工作目錄根目錄，先 node cloud/build.mjs）：
    python design-previews/ui-polish/measure-nav.py            # 印結果，超過門檻時結束碼 1
    python design-previews/ui-polish/measure-nav.py --runs 5
門檻：rows 中位數 ≤ 900 毫秒、assets = 0、api_chain ≤ 2。修之前這三項都不過。
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVER = ROOT / "design-previews" / "3d-redesign" / "preview-server.mjs"
BUDGET_ROWS_MS = 900

MARK = """
(() => {
  window.__firstRow = null;
  const seen = () => {
    if (window.__firstRow === null && document.querySelector('#holdingRows .hv-row')) window.__firstRow = performance.now();
  };
  new MutationObserver(seen).observe(document, { childList: true, subtree: true });
})();
"""

READ = """
() => {
  const nav = performance.getEntriesByType('navigation')[0];
  const res = performance.getEntriesByType('resource');
  const fcp = performance.getEntriesByType('paint').find(p => p.name === 'first-contentful-paint');
  const local = res.filter(e => e.name.startsWith(location.origin));
  const assets = local.filter(e => /\\.(css|js)(\\?|$)/.test(e.name));
  const api = local.filter(e => new URL(e.name).pathname.startsWith('/api/'))
    .map(e => ({ path: new URL(e.name).pathname, start: Math.round(e.startTime), end: Math.round(e.responseEnd) }));
  return {
    ttfb: Math.round(nav.responseStart), fcp: fcp ? Math.round(fcp.startTime) : null,
    rows: window.__firstRow === null ? null : Math.round(window.__firstRow),
    assets_fetched: assets.filter(e => e.transferSize > 0).length, assets_total: assets.length, api,
  };
}
"""

# 這一頁的清單要等哪幾支資料（其他是設定浮板自己要的，不擋清單）
LIST_APIS = ("/api/features", "/api/watchlist", "/api/holdings", "/api/watch", "/api/watch/judgments", "/api/friend-ai/stocks", "/api/ai-digest")


def chain_depth(api: list[dict]) -> int:
    """清單要等的那幾支請求排了幾層：一支在另一支回來之後才發，就多一層。"""
    rows = sorted((row for row in api if row["path"] in LIST_APIS), key=lambda row: row["start"])
    depth, level_end = 0, -1
    for row in rows:
        if row["start"] >= level_end:
            depth += 1
            level_end = row["end"]
        else:
            level_end = min(level_end, row["end"])
    return depth


def start_server() -> tuple[subprocess.Popen, str]:
    proc = subprocess.Popen(["node", "--no-warnings", str(SERVER), "--port", "0", "--owner-extras"],
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
    line = proc.stdout.readline()
    found = re.search(r"http://127\.0\.0\.1:\d+", line)
    if not found:
        rest = proc.stdout.read()
        proc.kill()
        raise SystemExit(f"preview server did not start:\n{line}{rest}")
    return proc, found.group(0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    proc, base = start_server()
    runs = []
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=3, is_mobile=True, has_touch=True)
            context.add_init_script(MARK)
            page = context.new_page()
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
                                                          "downloadThroughput": 9_000_000 // 8, "uploadThroughput": 3_000_000 // 8})
            cdp.send("Emulation.setCPUThrottlingRate", {"rate": 4})
            # 暖身：兩頁都開過一次，瀏覽器可以留的檔案都留下了
            page.goto(f"{base}/minimal.html")
            page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
            page.goto(f"{base}/portfolio.html")
            page.wait_for_selector("#holdingRows .hv-row")
            for _ in range(args.runs):
                page.goto(f"{base}/minimal.html")
                page.wait_for_function("window.MinimalApp && window.MinimalApp.ready")
                page.wait_for_load_state("networkidle")
                with page.expect_navigation(wait_until="commit"):
                    page.tap("#portfolioEntry")
                page.wait_for_function("window.__firstRow !== null", timeout=30_000)
                page.wait_for_load_state("networkidle")
                got = page.evaluate(READ)
                got["api_chain"] = chain_depth(got.pop("api"))
                runs.append(got)
            browser.close()
    finally:
        proc.kill()
    median = {key: statistics.median(run[key] for run in runs) for key in ("ttfb", "fcp", "rows", "assets_fetched", "api_chain")}
    failures = []
    if median["rows"] > BUDGET_ROWS_MS:
        failures.append(f"rows {median['rows']:.0f} ms > {BUDGET_ROWS_MS} ms")
    if median["assets_fetched"] > 0:
        failures.append(f"{median['assets_fetched']:.0f} css/js files fetched again on a repeat visit")
    if median["api_chain"] > 2:
        failures.append(f"list requests queue {median['api_chain']:.0f} deep")
    if args.json:
        print(json.dumps({"runs": runs, "median": median, "failures": failures}, ensure_ascii=False))
    else:
        for index, run in enumerate(runs, 1):
            print(f"run {index}: ttfb {run['ttfb']} fcp {run['fcp']} rows {run['rows']} assets {run['assets_fetched']}/{run['assets_total']} api_chain {run['api_chain']}")
        print(f"median: ttfb {median['ttfb']:.0f} fcp {median['fcp']:.0f} rows {median['rows']:.0f} assets {median['assets_fetched']:.0f} api_chain {median['api_chain']:.0f}")
        print("PASS" if not failures else "FAIL: " + "; ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
