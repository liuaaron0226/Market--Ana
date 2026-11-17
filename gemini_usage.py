"""Gemini 剩餘用量（本人 2026-11-17：「在設定加入一個 gemini 剩餘用量」）。

每支用 agy 的排程跑完後讀一次 `agy -p /usage --output-format json`，寫一筆到 D1，設定頁（只給站主）讀最新一筆。
`/usage` 由 CLI 自己回答：0 token、不呼叫模型、不耗額度（2026-11-17 用本人帳號實測）。
Pro 方案的 Argon 是另一組額度（回傳原文「For paid Pro plans, Argon quota is separate」），所以每一格原樣留下，
由設定頁依 id 分組顯示，這裡不寫死哪幾格。

用法（排程裡，清除登入檔之前）：
  python gemini_usage.py --agy-bin "$RUNNER_TEMP/agy/antigravity" --task watch-ledger --run-id "$GITHUB_RUN_ID" \
      --sql-output "$RUNNER_TEMP/gemini-usage.sql"
模型名稱不在排程檔另寫：照 gemini_models.py（各任務用哪個模型的唯一設定處）查；查不到就留空。
讀不到用量也照樣寫一筆（用量欄空著），且一律以 0 結束：用量只是附帶資訊，不能讓原本的排程失敗。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# 任務（排程檔名）→ gemini_models.py 裡的名稱。自選股那支排程同時跑每日判斷與提名同類股，兩者都是 Flash。
TASK_MODELS = {
    "forecast-ledger": "FORECAST", "forecast-morning": "FORECAST", "forecast-replay": "FORECAST",
    "watch-ledger": "WATCH_JUDGE", "ai-digest": "FLASH", "daily-update": "FLASH",
}


def model_for_task(task: str) -> str | None:
    try:
        import gemini_models
    except ImportError:
        return None
    name = TASK_MODELS.get(task)
    return getattr(gemini_models, name, None) if name else None


class UsageUnavailable(Exception):
    """輸出不是 /usage 指令的回傳（例如被當成一般提問送給模型、agy 出錯、登入過期）。"""


def parse_usage(text: str) -> list[dict[str, Any]]:
    start = text.find("{")
    if start < 0:
        raise UsageUnavailable("沒有 JSON")
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError as error:
        raise UsageUnavailable(f"JSON 讀不懂：{error}") from None
    command = payload.get("command") if isinstance(payload, dict) else None
    if payload.get("status") != "SUCCESS" or not isinstance(command, dict) or command.get("name") != "usage":
        raise UsageUnavailable("不是 /usage 的回傳")
    groups = (command.get("data") or {}).get("groups") or []
    buckets = []
    for group in groups:
        for bucket in group.get("buckets") or []:
            fraction = bucket.get("remaining_fraction")
            buckets.append({
                "id": str(bucket.get("id") or ""),
                "group": str(group.get("name") or ""),
                "window": str(bucket.get("window") or ""),
                "remaining": round(float(fraction), 4) if isinstance(fraction, (int, float)) else None,
                "reset_time": bucket.get("reset_time"),
            })
    return buckets


def _literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def snapshot_sql(buckets: Iterable[dict[str, Any]] | None, *, task: str, run_id: str, taken_at: str,
                 model: str | None = None) -> str:
    buckets_json = None if buckets is None else json.dumps(list(buckets), ensure_ascii=False, separators=(",", ":"))
    values = ", ".join(_literal(v) for v in (task, str(run_id), taken_at, model or None, buckets_json))
    return ("INSERT INTO gemini_usage_snapshots (task, run_id, taken_at, model, buckets_json) VALUES "
            f"({values}) ON CONFLICT (task, run_id) DO UPDATE SET taken_at = excluded.taken_at, "
            "model = excluded.model, buckets_json = excluded.buckets_json;\n")


def read_usage(agy_bin: str, timeout: int = 90) -> list[dict[str, Any]]:
    # 和 ai_digest 同一個慣例：.py 結尾的假程式（測試用）用目前的 Python 跑
    prefix = [sys.executable, agy_bin] if agy_bin.lower().endswith(".py") else [agy_bin]
    try:
        done = subprocess.run([*prefix, "-p", "/usage", "--output-format", "json", "--print-timeout", "60s"],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UsageUnavailable(f"agy 沒有跑完：{type(error).__name__}") from None
    return parse_usage(done.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agy-bin", required=True)
    parser.add_argument("--task", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--model", help="這次用的模型；省略時照 gemini_models.py 查這個任務的模型")
    parser.add_argument("--sql-output", required=True)
    args = parser.parse_args(argv)
    try:
        buckets: list[dict[str, Any]] | None = read_usage(args.agy_bin)
        print(f"讀到 {len(buckets)} 格用量")
    except UsageUnavailable as error:
        buckets = None
        print(f"::warning::讀不到 Gemini 用量（{error}）；只記下這次任務")
    taken_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    Path(args.sql_output).write_text(snapshot_sql(buckets, task=args.task, run_id=args.run_id, taken_at=taken_at,
                                                 model=args.model or model_for_task(args.task)),
                                     encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
