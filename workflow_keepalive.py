"""有排程的 workflow 保活（2026-11-24 起；本人退訂 Claude 後沒有人會去 GitHub 手動重新啟用）。

GitHub 的規則（https://docs.github.com/en/actions/writing-workflows/choosing-when-your-workflow-runs/events-that-trigger-workflows#schedule）：
公開 repo 連續 60 天沒有 repository activity，有 schedule 的 workflow 會被自動停用（state = disabled_inactivity）；
停用後連 API 觸發也會被拒，Cloudflare cron-trigger 就送不進 daily-update，網站停更。

兩層（見 workflow-keepalive.yml 開頭的理由）：
1. 心跳 commit（主要）：每 25 天以上才一次，由 workflow 自己把 UTC 日期寫進 ops/heartbeat.txt 並 commit 到 main。
   commit 是 repository activity 最直接的形式，60 天的計時就不會走完。只改這一個檔，網站的建置不讀 ops/（cloud/build.mjs 採允許清單）。
2. 呼叫 enable（第二層）：被 60 天規則停用的直接重新啟用；有排程而且啟用中的也呼叫一次（GitHub 沒寫這樣會不會重算天數，只當保險）。
必要的 workflow（REQUIRED）只要不是啟用中（本人手動停用、GitHub 沒列出、啟用失敗），這一輪就失敗（紅色），
除非列在 EXEMPT（目前沒有）。暫時性的錯誤（5xx、429、網路）重試幾次。只用標準函式庫。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Iterable

API = "https://api.github.com"
ROOT = Path(__file__).resolve().parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"
HEARTBEAT = ROOT / "ops" / "heartbeat.txt"
HEARTBEAT_MIN_DAYS = 25     # 60 天規則：每 25 天一次心跳，中間漏掉一次也還有餘裕
JOB = "workflow-keepalive"  # 記在網站資料庫 ops_runs 的名稱（資料更新狀態「排程保活」那一列讀它）

# 一定要是啟用中的（各檔開頭的說明）：
# - daily-update：每日行情；Cloudflare cron-trigger 觸發的主要對象，停掉網站就停更。
# - level-daily：現金水位，每個交易日 07:30 的正式排程（資料更新狀態「現金水位」那一列）。
# - forecast-replay：歷史重播；它的開關是 repository variable MARKET_ANA_REPLAY_SCHEDULE，不是停用 workflow，
#   所以被停用一定是意外。
REQUIRED = (".github/workflows/daily-update.yml", ".github/workflows/level-daily.yml", ".github/workflows/forecast-replay.yml")
# 本人刻意停用、不要報錯的必要 workflow：路徑 → 原因。目前沒有。
EXEMPT: dict[str, str] = {}

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


def plan(workflows: Iterable[dict], scheduled: Iterable[str], *, required: Iterable[str] = REQUIRED,
         exempt: dict | None = None) -> list[dict]:
    """每一支要怎麼處理。

    enable：被 60 天規則停用（disabled_inactivity），重新啟用。
    refresh：有排程、目前啟用中，照樣呼叫 enable。
    leave：手動停用的、不是必要的，不碰。
    blocked：必要的卻是手動停用（沒有列在 EXEMPT）→ 這一輪失敗，請本人到 Actions 頁面按 Enable workflow。
    exempt：必要的、手動停用、列在 EXEMPT（本人刻意的）。
    missing：repo 裡有排程的檔，但 GitHub 沒列出來；必要的會讓這一輪失敗。
    """
    scheduled, required, exempt = set(scheduled), set(required), dict(EXEMPT if exempt is None else exempt)
    rows, seen = [], set()
    for wf in sorted(workflows, key=lambda w: str(w.get("path", ""))):
        path, state = str(wf.get("path", "")), str(wf.get("state", ""))
        seen.add(path)
        if state == "disabled_inactivity":
            action = "enable"
        elif path not in scheduled and path not in required:
            continue
        elif state == "active":
            action = "refresh"
        elif path in required:
            action = "exempt" if path in exempt else "blocked"
        elif state == "disabled_manually":
            action = "leave"
        else:
            continue   # deleted、disabled_fork：不是這個 repo 該管的
        rows.append({"id": wf.get("id"), "path": path, "state": state, "action": action, "required": path in required})
    for path in sorted((scheduled | required) - seen):
        rows.append({"id": None, "path": path, "state": "not-listed", "action": "missing", "required": path in required})
    return rows


def with_retries(request: Request, *, attempts: int = 4, sleep: Callable[[float], None] = time.sleep) -> Request:
    """暫時性的錯誤（網路、5xx、429）重試：2、4、8 秒（429 有 Retry-After 就照它，最多 60 秒）。"""
    def call(method: str, url: str):
        status, body = 0, None
        for attempt in range(attempts):
            try:
                status, body = request(method, url)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
                status, body = 0, {"error": type(err).__name__}
            if status and status < 500 and status != 429:
                return status, body
            if attempt + 1 < attempts:
                wait = 2 ** (attempt + 1)
                if status == 429 and isinstance(body, dict) and str(body.get("retry_after", "")).isdigit():
                    wait = min(60, int(body["retry_after"]))
                sleep(wait)
        return status, body
    return call


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
            return err.code, {"retry_after": err.headers.get("Retry-After", "")} if err.headers else None
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


LABEL = {"enable": "重新啟用（被 60 天規則停用）", "refresh": "照常呼叫啟用",
         "leave": "手動停用、不是必要的，不碰", "exempt": "手動停用（列在例外清單，本人刻意的）",
         "blocked": "必要的卻被手動停用：到 Actions 頁面左邊點這一支，按「Enable workflow」",
         "missing": "GitHub 沒列出這支（被刪掉或還沒推上 main？）"}


def run(repo: str, request: Request, *, dry_run: bool, folder: Path | str = WORKFLOWS_DIR,
        required: Iterable[str] = REQUIRED, exempt: dict | None = None) -> tuple[int, list[str]]:
    rows = plan(list_workflows(repo, request), scheduled_workflows(folder), required=required, exempt=exempt)
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
        elif row["action"] == "blocked" or (row["action"] == "missing" and row["required"]):
            failed += 1
        lines.append(f"- {row['path']}（目前 {row['state']}{'，必要' if row['required'] else ''}）：{result}")
    if not rows:
        lines.append("- 沒有需要保活的 workflow")
    return (1 if failed else 0), lines


# ---------------------------------------------------------------- 心跳

def heartbeat_due(path: Path | str, today: dt.date, *, min_days: int = HEARTBEAT_MIN_DAYS) -> bool:
    """上一次心跳是 min_days 天以前（或還沒有、讀不懂）就該再打一次。"""
    try:
        last = dt.date.fromisoformat(Path(path).read_text(encoding="utf-8").strip().splitlines()[0][:10])
    except (OSError, ValueError, IndexError):
        return True
    return (today - last).days >= min_days


def heartbeat_text(today: dt.date) -> str:
    return (f"{today.isoformat()}\n"
            "# workflow-keepalive 每 25 天以上寫一次 UTC 日期並 commit，讓 GitHub 的 60 天閒置規則不會停用排程。\n"
            "# 網站的建置不讀這個資料夾；請不要放其他東西進來。\n")


# ---------------------------------------------------------------- 記到網站資料庫（資料更新狀態讀）

def record_sql(*, ok: bool, now: dt.datetime, detail: str) -> str:
    """ops_runs（0041）的一列。成功才更新 last_ok_at；內容都是程式產生的固定字，沒有外來輸入。"""
    stamp = now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    detail = re.sub(r"[^0-9A-Za-z_ :=,.-]", "", detail)[:120]
    ok_value = f"'{stamp}'" if ok else "NULL"
    return (f"INSERT INTO ops_runs (job, last_ok_at, last_attempt_at, detail) VALUES ('{JOB}', {ok_value}, '{stamp}', '{detail}') "
            "ON CONFLICT (job) DO UPDATE SET last_attempt_at = excluded.last_attempt_at, "
            "last_ok_at = COALESCE(excluded.last_ok_at, ops_runs.last_ok_at), detail = excluded.detail;")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="有排程的 workflow 保活")
    sub = parser.add_subparsers(dest="command")
    beat = sub.add_parser("heartbeat", help="該打心跳就改寫 ops/heartbeat.txt（不 commit），輸出 due=true/false")
    beat.add_argument("--file", default=str(HEARTBEAT))
    beat.add_argument("--github-output")
    rec = sub.add_parser("record-sql", help="印出記到網站資料庫的那一句")
    rec.add_argument("--ok", choices=["true", "false"], required=True)
    rec.add_argument("--detail", default="")
    parser.add_argument("--dry-run", action="store_true", help="只列出會做什麼，不呼叫啟用")
    parser.add_argument("--summary", help="另外寫進這個檔（GitHub 的 $GITHUB_STEP_SUMMARY）")
    args = parser.parse_args(argv)

    if args.command == "heartbeat":
        today = dt.datetime.now(dt.timezone.utc).date()
        due = heartbeat_due(args.file, today)
        if due:
            Path(args.file).parent.mkdir(parents=True, exist_ok=True)
            Path(args.file).write_text(heartbeat_text(today), encoding="utf-8", newline="\n")
        print(f"心跳：{'該打（寫入 ' + today.isoformat() + '）' if due else '上一次未滿 ' + str(HEARTBEAT_MIN_DAYS) + ' 天，不用'}")
        if args.github_output:
            with open(args.github_output, "a", encoding="utf-8") as fh:
                fh.write(f"due={'true' if due else 'false'}\n")
        return 0
    if args.command == "record-sql":
        print(record_sql(ok=args.ok == "true", now=dt.datetime.now(dt.timezone.utc), detail=args.detail))
        return 0

    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("::error::缺少 GITHUB_TOKEN 或 GITHUB_REPOSITORY")
        return 1
    try:
        code, lines = run(repo, with_retries(_http(token)), dry_run=args.dry_run)
    except RuntimeError as err:
        code, lines = 1, [f"### 有排程的 workflow 保活：{err}"]
    text = "\n".join(lines) + "\n"
    print(text, end="")
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(text)
    if code:
        print("::error::保活沒有完成：有必要的 workflow 不是啟用中，或啟用失敗（見摘要）")
    return code


if __name__ == "__main__":
    sys.exit(main())
