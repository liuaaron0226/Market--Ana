"""有排程的 workflow 保活（2026-11-24，助手停用後不能靠人手去 GitHub 重新啟用）。

GitHub 的規則（https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule）：
公開 repo 連續 60 天沒有 repository activity，有 schedule 的 workflow 會被自動停用（state = disabled_inactivity）。
停用之後連 workflow_dispatch 也會被拒（API 回 422），所以 Cloudflare cron-trigger 送不進 daily-update，網站就停更。

做法：workflow-keepalive.yml 接在 announcements／market-news 後面（workflow_run），這兩支沒有 schedule、
由 Cloudflare 的 cron 用 API 觸發，不受 60 天規則影響；保活本身也沒有 schedule，所以不會自己被停用。
每一輪對「有 schedule 的 workflow」呼叫一次 enable（已啟用的也呼叫一次，第三方工具 gh-workflow-immortality 說這樣會重算閒置天數；
GitHub 文件沒寫明），被 60 天規則停用的直接重新啟用；本人手動停用的（disabled_manually）不碰。
token 用 workflow 自帶的 GITHUB_TOKEN（permissions: actions: write），不需要新的機密設定。只用標準函式庫。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

API = "https://api.github.com"
WORKFLOWS_DIR = Path(__file__).resolve().parent / ".github" / "workflows"
# on: 底下的 schedule（兩格縮排）；workflow 檔一律這樣寫，test_workflow_keepalive 用 YAML 解析對照。
_SCHEDULE = re.compile(r"^  schedule:\s*(#.*)?$", re.M)

Request = Callable[[str, str], "tuple[int, object]"]


def scheduled_workflows(folder: Path | str = WORKFLOWS_DIR) -> list[str]:
    """repo 裡有 schedule 觸發的 workflow（路徑和 API 回的 path 同格式）。"""
    found = []
    for path in sorted(Path(folder).glob("*.y*ml")):
        if _SCHEDULE.search(path.read_text(encoding="utf-8")):
            found.append(f".github/workflows/{path.name}")
    return found


def plan(workflows: Iterable[dict], scheduled: Iterable[str]) -> list[dict]:
    """每一支要怎麼處理。

    enable：被 60 天規則停用（disabled_inactivity），重新啟用。
    refresh：有排程、目前啟用中，照樣呼叫 enable 重算閒置天數。
    leave：本人手動停用的，不碰。
    missing：repo 裡有排程的檔，但 GitHub 沒列出來（剛加、還沒推上 main）。只提醒。
    """
    scheduled = set(scheduled)
    rows, seen = [], set()
    for wf in sorted(workflows, key=lambda w: str(w.get("path", ""))):
        path, state = str(wf.get("path", "")), str(wf.get("state", ""))
        seen.add(path)
        if state == "disabled_inactivity":
            action = "enable"
        elif path not in scheduled:
            continue
        elif state == "active":
            action = "refresh"
        elif state == "disabled_manually":
            action = "leave"
        else:
            continue   # deleted、disabled_fork：不是這個 repo 該管的
        rows.append({"id": wf.get("id"), "path": path, "name": wf.get("name", path), "state": state, "action": action})
    for path in sorted(scheduled - seen):
        rows.append({"id": None, "path": path, "name": path, "state": "not-listed", "action": "missing"})
    return rows


def _http(token: str) -> Request:
    def request(method: str, url: str):
        req = urllib.request.Request(url, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "market-ana-workflow-keepalive",
        }, data=b"" if method == "PUT" else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                return resp.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as err:
            return err.code, None
    return request


def list_workflows(repo: str, request: Request) -> list[dict]:
    out, page = [], 1
    while True:
        status, body = request("GET", f"{API}/repos/{repo}/actions/workflows?per_page=100&page={page}")
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"讀不到 workflow 清單（HTTP {status}）")
        batch = body.get("workflows") or []
        out.extend(batch)
        if len(out) >= int(body.get("total_count") or 0) or not batch:
            return out
        page += 1


LABEL = {"enable": "重新啟用（被 60 天規則停用）", "refresh": "照常呼叫啟用、重算閒置天數",
         "leave": "本人手動停用，不碰", "missing": "GitHub 沒列出這支（還沒推上 main？）"}


def run(repo: str, request: Request, *, dry_run: bool, folder: Path | str = WORKFLOWS_DIR) -> tuple[int, list[str]]:
    rows = plan(list_workflows(repo, request), scheduled_workflows(folder))
    lines = ["### 有排程的 workflow 保活" + ("（乾跑，沒有呼叫啟用）" if dry_run else "")]
    failed = 0
    for row in rows:
        result = LABEL[row["action"]]
        if row["action"] in ("enable", "refresh") and not dry_run:
            status, _ = request("PUT", f"{API}/repos/{repo}/actions/workflows/{row['id']}/enable")
            if status == 204:
                result += "：成功"
            else:
                failed += 1
                result += f"：失敗（HTTP {status}）"
        lines.append(f"- {row['path']}（目前 {row['state']}）：{result}")
    if not rows:
        lines.append("- 沒有需要保活的 workflow")
    return (1 if failed else 0), lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="只列出會做什麼，不呼叫啟用")
    parser.add_argument("--summary", help="另外寫進這個檔（GitHub 的 $GITHUB_STEP_SUMMARY）")
    args = parser.parse_args(argv)
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("::error::缺少 GITHUB_TOKEN 或 GITHUB_REPOSITORY")
        return 1
    code, lines = run(repo, _http(token), dry_run=args.dry_run)
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    if code:
        print("::error::有 workflow 啟用失敗；到 Actions 頁面左邊點那一支，按「Enable workflow」")
    return code


if __name__ == "__main__":
    sys.exit(main())
