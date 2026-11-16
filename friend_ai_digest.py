"""開放給朋友 02 票：用每位朋友自己的 Gemini 金鑰，產生他的大盤與籌碼解讀。

輸入只有證交所、櫃買的官方開放資料（``ai_digest_inputs.build_friend_input``），不含新聞、國外行情、任何人的持股。
驗證與失敗處理和站主那條線共用 ``ai_digest.digest_with_model``：驗證不過、金鑰失效、額度用完都標「未產生」，
不補猜、不沿用舊內容，也不改用站主的 agy。一位朋友失敗不影響其他人。
解開後的金鑰只留在記憶體，不寫檔、不印進記錄或稽核。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_digest_inputs import build_friend_input
from ai_digest import (
    EMPTY_USAGE, OUTPUT_SCHEMA, ModelCallFailed, _evidence_catalog, _row, _sql_literal, _validated_rows, digest_with_model,
)

MODEL = "gemini-3.8-flash"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SECTIONS = ("market", "flow")


class GeminiHttpError(Exception):
    """假上游與真上游共用：HTTP 狀態碼加上 Google 錯誤內容裡的 status／reason（不含金鑰）。"""

    def __init__(self, status: int, error_status: str | None = None, reasons: tuple[str, ...] = ()):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.error_status = error_status
        self.reasons = reasons


def _definitions_inlined(schema: Any, defs: dict[str, Any]) -> Any:
    if isinstance(schema, dict):
        if "$ref" in schema:
            return _definitions_inlined(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
        return {key: _definitions_inlined(value, defs) for key, value in schema.items() if key != "$defs"}
    if isinstance(schema, list):
        return [_definitions_inlined(item, defs) for item in schema]
    return schema


def _friend_schema() -> dict[str, Any]:
    properties = {key: OUTPUT_SCHEMA["properties"][key] for key in ("data_date", "market", "flows")}
    schema = {**OUTPUT_SCHEMA, "required": ["data_date", "market", "flows"], "properties": properties}
    return _definitions_inlined(schema, OUTPUT_SCHEMA["$defs"])


FRIEND_SCHEMA = _friend_schema()


def build_friend_prompt(payload: dict[str, Any]) -> str:
    catalog = list(_evidence_catalog(payload).values())
    return "\n".join([
        "你是台股盤後資料整理員。只可根據 INPUT_JSON 與 EVIDENCE_CATALOG 做質化判斷，不可連網、不可抓資料、不可使用工具、不可自創指標。",
        "所有自由文字欄位只能寫繁體中文質化判斷，不得出現阿拉伯／全形／中文數字、股票代號、日期、價格、數量或百分比。",
        "每個結論、重點、訊號都必須填入一至三個 exact refs。程式會核對 scope、股票代號與 exact path，並以固定標籤、值及單位補上事實。",
        "market 只能引用 scope=market；flows 只能引用 scope=flow，帶 code 的 ref 必須列在該訊號 codes。資料不足就不要補猜。",
        "不要寫模型名稱、提示詞、參考站、會員、反推、吻合率、方法說明或保證獲利。",
        "INPUT_JSON:",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "EVIDENCE_CATALOG:",
        json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ])


def _usage(reply: dict[str, Any]) -> dict[str, int | None]:
    meta = reply.get("usageMetadata") if isinstance(reply.get("usageMetadata"), dict) else {}
    pick = lambda name: int(meta[name]) if isinstance(meta.get(name), (int, float)) else None
    return {"input_tokens": pick("promptTokenCount"), "output_tokens": pick("candidatesTokenCount"),
            "total_tokens": pick("totalTokenCount")}


def classify_http_error(error: GeminiHttpError) -> str:
    """和 01 票試呼叫同一套分類（cloud/lib/friend-ai.js 的 trialCall）。"""
    if error.status in (429, 402):
        return "quota"
    if error.status == 401:
        return "key_invalid"
    if error.status == 403:
        return "key_revoked"
    if error.status == 400:
        if error.error_status == "FAILED_PRECONDITION":
            return "region"
        if "API_KEY_INVALID" in error.reasons:
            return "key_invalid"
    return "gemini_unavailable"


def call_gemini(prompt: str, model: str, *, api_key: str, post: Callable) -> tuple[Any, dict[str, int | None]]:
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": FRIEND_SCHEMA},
    }
    try:
        reply = post(GEMINI_URL.format(model=model), body, api_key)
    except GeminiHttpError as error:
        raise ModelCallFailed(classify_http_error(error), None, {"http_status": error.status}) from None
    except OSError as error:
        raise ModelCallFailed("gemini_unavailable", None, {"error": type(error).__name__}) from None
    usage = _usage(reply)
    try:
        parts = reply["candidates"][0]["content"]["parts"]
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought"))
        return json.loads(text), usage
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise ModelCallFailed("gemini_failed", usage, {"error": "no_structured_output"}) from None


def _friend_failure_rows(payload: dict[str, Any], model: str, usage: dict[str, int | None], error_code: str) -> list[dict[str, Any]]:
    return [_row(payload, digest_type, "market", "unproduced", None, model, usage, error_code) for digest_type in SECTIONS]


def _friend_validated_rows(payload, structured, model, usage):
    if not isinstance(structured, dict) or set(structured) != {"data_date", "market", "flows"}:
        return _friend_failure_rows(payload, model, usage, "structured_output_invalid"), [
            {"section": "all", "reason": "structured_output 資料日、格式或允許欄位不正確"}
        ]
    rows, rejected = _validated_rows(payload, {**structured, "stocks": []}, model, usage)
    return [row for row in rows if row["digest_type"] in SECTIONS], rejected


def _digest_one(payload: dict[str, Any], api_key: str, *, post: Callable, model: str) -> dict[str, Any]:
    return digest_with_model(
        payload, prompt=build_friend_prompt(payload), model=model, crash_code="gemini_failed",
        call_model=lambda prompt, chosen: call_gemini(prompt, chosen, api_key=api_key, post=post),
        validate=_friend_validated_rows, failure_rows=_friend_failure_rows,
    )


def _failed(payload: dict[str, Any], model: str, code: str, error: Exception) -> dict[str, Any]:
    usage = dict(EMPTY_USAGE)
    return {"rows": _friend_failure_rows(payload, model, usage, code), "usage": usage,
            "audit": {"failure": code, "error": type(error).__name__, "rejected": []}}


def _one_friend(payload: dict[str, Any], key_row: dict[str, Any], *, decrypt: Callable, post: Callable,
                model: str) -> dict[str, Any]:
    # 每位朋友各自收尾：任何例外都只讓他自己這天「未產生」，例外內容只記類別，不記訊息（可能帶上游原文）。
    try:
        api_key = decrypt(key_row)
    except Exception as error:  # noqa: BLE001 — 解不開的原因不重要，重要的是不影響下一位
        return _failed(payload, model, "key_decrypt_failed", error)
    try:
        return _digest_one(payload, api_key, post=post, model=model)
    except Exception as error:  # noqa: BLE001
        return _failed(payload, model, "internal_error", error)
    finally:
        del api_key


def run_friends(payload: dict[str, Any], keys: list[dict[str, Any]], *, decrypt: Callable, post: Callable,
                model: str = MODEL, now: Callable[[], str] | None = None) -> dict[str, Any]:
    now = now or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    rows: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    for key_row in keys:
        email = key_row["email"]
        result = _one_friend(payload, key_row, decrypt=decrypt, post=post, model=model)
        at = now()
        for row in result["rows"]:
            rows.append({**row, "email": email, "created_at": at})
        generated = all(row["status"] == "generated" for row in result["rows"])
        outcomes[email] = {"outcome": "success" if generated else "failure",
                           "reason": None if generated else result["audit"]["failure"] or "validation_failed"}
        audit.append({"email": email, **result["audit"], "usage": result["usage"]})
    return {"rows": rows, "keys": outcomes, "audit": audit}


def friend_sql(result: dict[str, Any]) -> str:
    """寫進 friend_ai_daily（每位朋友各自當天的成功或未產生），並更新他金鑰的最後成功時間／最近失敗原因。"""
    if not result["rows"]:
        return ""
    statements = ["BEGIN;"]
    for row in result["rows"]:
        values = [
            row["email"], row["data_date"], row["digest_type"],
            None if row["content"] is None else json.dumps(row["content"], ensure_ascii=False, separators=(",", ":")),
            row["input_sha256"], row["model"], row["input_tokens"], row["output_tokens"], row["total_tokens"],
            row["status"], row["error_code"], row["created_at"], row["created_at"],
        ]
        statements.append(
            "INSERT INTO friend_ai_daily (email,data_date,digest_type,content_json,input_sha256,model,input_tokens,output_tokens,"
            "total_tokens,status,error_code,created_at,updated_at) VALUES (" + ",".join(_sql_literal(v) for v in values) +
            ") ON CONFLICT(email,data_date,digest_type) DO UPDATE SET content_json=excluded.content_json,"
            "input_sha256=excluded.input_sha256,model=excluded.model,input_tokens=excluded.input_tokens,"
            "output_tokens=excluded.output_tokens,total_tokens=excluded.total_tokens,status=excluded.status,"
            "error_code=excluded.error_code,updated_at=excluded.updated_at;"
        )
    at_by_email = {row["email"]: row["created_at"] for row in result["rows"]}
    for email, outcome in result["keys"].items():
        at = _sql_literal(at_by_email.get(email))
        if outcome["outcome"] == "success":
            statements.append(f"UPDATE friend_gemini_keys SET last_success_at={at},last_failure_reason=NULL,updated_at={at} "
                              f"WHERE email={_sql_literal(email)};")
        else:
            statements.append(f"UPDATE friend_gemini_keys SET last_failure_reason={_sql_literal(outcome['reason'])},updated_at={at} "
                              f"WHERE email={_sql_literal(email)};")
    statements.append("COMMIT;")
    return "\n".join(statements) + "\n"


def workflow_policy(event: str, switch: str | None, dry_run_input: str | None) -> dict[str, bool]:
    """排程能不能跑、要不要乾跑。開關只認一字不差的 "on"；手動觸發沒明講 false 一律乾跑。"""
    enabled = switch == "on"
    if event == "workflow_dispatch":
        if dry_run_input in (None, "", "true"):
            return {"allowed": True, "dry_run": True}
        if dry_run_input == "false" and enabled:
            return {"allowed": True, "dry_run": False}
    elif event == "workflow_run" and enabled:
        return {"allowed": True, "dry_run": False}
    return {"allowed": False, "dry_run": True}


ROOT = Path(__file__).resolve().parent
SCHEDULE_JS = ROOT / "cloud" / "friend-ai-schedule.mjs"


def http_post(url: str, body: dict[str, Any], api_key: str, *, timeout: float = 300) -> dict[str, Any]:
    """真的上游：金鑰只放 x-goog-api-key 標頭，不進網址。錯誤只取 HTTP 狀態與 Google 的 status／reason。"""
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — 網址是程式常數
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail: dict[str, Any] = {}
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error") or {}
        except (ValueError, AttributeError, OSError):
            pass
        reasons = tuple(str(item.get("reason")) for item in detail.get("details") or [] if isinstance(item, dict))
        raise GeminiHttpError(error.code, detail.get("status"), reasons) from None
    except (ValueError, TimeoutError) as error:
        raise OSError(type(error).__name__) from None


def node_decrypt(key_row: dict[str, Any]) -> str:
    """用排程腳本解開一把金鑰；主金鑰走環境變數，原文只在記憶體（stdout 管線），不寫檔。"""
    if key_row.get("alg") != "AES-256-GCM":
        raise ValueError("unsupported alg")
    completed = subprocess.run(
        ["node", str(SCHEDULE_JS), "decrypt"], capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
        input=json.dumps({key: key_row[key] for key in ("email", "ciphertext", "iv")}),
    )
    if completed.returncode != 0 or not completed.stdout:
        raise ValueError("decrypt failed")
    return completed.stdout


def read_keys(path: Path) -> list[dict[str, Any]]:
    """wrangler d1 execute --json 的輸出：[{results: [...]}]。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    containers = raw if isinstance(raw, list) else [raw]
    rows = [row for container in containers if isinstance(container, dict) for row in container.get("results") or []]
    return [row for row in rows if isinstance(row, dict) and row.get("email")]


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for outcome in result["keys"].values():
        label = outcome["outcome"] if outcome["outcome"] == "success" else outcome["reason"]
        counts[label] = counts.get(label, 0) + 1
    return {"friends": len(result["keys"]), "outcomes": counts}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    auth = sub.add_parser("authorize", help="決定排程能不能跑、要不要乾跑")
    auth.add_argument("--event", required=True)
    auth.add_argument("--switch")
    auth.add_argument("--dry-run-input")
    auth.add_argument("--github-output", type=Path, required=True)
    run = sub.add_parser("run", help="整理輸入；非乾跑時逐位朋友呼叫 Gemini 並產生寫入 SQL")
    run.add_argument("--date", required=True)
    run.add_argument("--market-dir", type=Path, required=True)
    run.add_argument("--screens-root", type=Path, required=True)
    run.add_argument("--keys-json", type=Path)
    run.add_argument("--sql-output", type=Path, required=True)
    run.add_argument("--audit", type=Path, required=True)
    run.add_argument("--prompt-output", type=Path)
    run.add_argument("--model", default=MODEL)
    run.add_argument("--gemini-url", default=GEMINI_URL, help=argparse.SUPPRESS)  # 只給測試接假的上游
    run.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.command == "authorize":
        decision = workflow_policy(args.event, args.switch, args.dry_run_input)
        with args.github_output.open("a", encoding="utf-8") as handle:
            handle.write(f"allowed={str(decision['allowed']).lower()}\ndry_run={str(decision['dry_run']).lower()}\n")
        print(json.dumps(decision))
        return 0

    payload = build_friend_input(data_date=args.date, market_dir=args.market_dir, screens_root=args.screens_root)
    prompt = build_friend_prompt(payload)
    if args.prompt_output:
        args.prompt_output.write_text(prompt, encoding="utf-8")
    if args.dry_run:
        audit = {"data_date": args.date, "input_sha256": payload["input_sha256"], "dry_run": True,
                 "availability": payload["availability"], "prompt_chars": len(prompt)}
        args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(audit, ensure_ascii=False))
        return 0
    if args.keys_json is None:
        parser.error("非乾跑必須提供 --keys-json")
    if not os.environ.get("FRIEND_AI_MASTER_KEY"):
        parser.error("非乾跑必須設定 FRIEND_AI_MASTER_KEY")
    url = args.gemini_url
    result = run_friends(payload, read_keys(args.keys_json), decrypt=node_decrypt, model=args.model,
                         post=lambda target, body, api_key: http_post(url.format(model=args.model), body, api_key))
    sql = friend_sql(result)
    if sql:
        args.sql_output.write_text(sql, encoding="utf-8")
    audit = {"data_date": args.date, "input_sha256": payload["input_sha256"], "model": args.model,
             **_summary(result), "friends_detail": result["audit"]}
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"data_date": args.date, "model": args.model, **_summary(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
