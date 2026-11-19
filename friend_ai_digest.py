"""開放給朋友 02、03 票：用每位朋友自己的 Gemini 金鑰，產生他的大盤、籌碼解讀與個股判斷。

輸入只有證交所、櫃買的官方開放資料（``ai_digest_inputs.build_friend_input``），不含新聞、國外行情、任何人的持股。
驗證與失敗處理和站主那條線共用 ``ai_digest.digest_with_model``：驗證不過、金鑰失效、額度用完都標「未產生」，
不補猜、不沿用舊內容，也不改用站主的 agy。一位朋友失敗不影響其他人。
04 票：Gemini 的錯誤分四類——額度用完（當天未產生，隔天照常）、金鑰無效或被撤銷（當天未產生，金鑰暫停到朋友換新的）、
暫時錯誤（當次有限次重試）、其他（當天未產生）。
2026-11-19：主要模型太忙（503）、逾時或不收請求時，同一次排程退到備援模型（gemini_models.FRIEND_DIGEST_FALLBACK）；
兩個都暫時連不上才標「未產生」，當天稍後的班次只替還沒有結果的朋友補跑（cloud/friend-ai-schedule.mjs 的 pendingKeysSql）。
每一次呼叫的模型、秒數、HTTP 狀態印在排程輸出（try_lines，不含 email 與訊息原文）。
解開後的金鑰只留在記憶體，不寫檔、不印進記錄或稽核。

03 票：有自選股或庫存的朋友，提示再加上「他自己」的持股（``ai_digest_inputs.with_friend_portfolio``，欄位和站主那條線
一樣），同一次呼叫產生三份；提示、輸出格式、驗證都用站主那條線的。個股判斷每人每天存一列（friend_ai_stock_daily）。
自選股和庫存都空的朋友不叫個股判斷，當天那份標「沒有持股」（no_holdings），不算失敗。
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import gemini_models
from ai_digest_inputs import build_friend_input, load_stock_packs, with_friend_portfolio
from ai_digest import (
    EMPTY_USAGE, OUTPUT_SCHEMA, ModelCallFailed, _evidence_catalog, _failure_rows, _row, _sql_literal, _validated_rows,
    build_prompt, digest_with_model,
)

# 朋友自己的 Gemini API 金鑰，維持 Flash（本人 2026-11-17；名稱只在 gemini_models 改）。
MODEL = gemini_models.FRIEND_DIGEST
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SECTIONS = ("market", "flow")
# 單次呼叫上限：10 位朋友最壞也在 job 的 60 分鐘內跑完、寫得進資料庫。
GEMINI_TIMEOUT_SECONDS = 120
# 暫時錯誤（Google 5xx、408、連線中斷）在當次排程內最多呼叫幾次、每次重試前等幾秒。
# 等滿 120 秒的逾時不重試：十位朋友各重試一輪逾時，就會超過 job 的 60 分鐘。
GEMINI_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (10, 30)
# 主要模型太忙、逾時或不收這個請求時，同一次排程退到備援模型再試（2026-11-19：gemini-3.8-flash 連續回 503）。
# 最壞情況一位朋友約 5 分鐘（主要 10＋30 秒等待加一次 120 秒逾時，備援 10 秒等待加一次 120 秒逾時），十位仍在 60 分鐘內。
FALLBACK_MODEL = gemini_models.FRIEND_DIGEST_FALLBACK
FALLBACK_ATTEMPTS = 2
# 這幾類換模型也沒用（額度與金鑰是跟著金鑰走的）：不試備援。
NO_FALLBACK_FAILURES = frozenset({"quota", "key_invalid", "key_revoked", "region"})
# 兩個模型都暫時連不上：當天稍後的班次（每日更新的下一個時段）只替還沒有結果的朋友補跑。
# 排程挑人的查詢在 cloud/friend-ai-schedule.mjs（RETRY_LATER_CODES），兩邊要一致。
RETRY_LATER_CODES = frozenset({"gemini_unavailable"})
# 這幾類代表金鑰本身不能用：暫停這把金鑰，排程之後跳過，等朋友換一把試呼叫成功的（lib/friend-ai.js saveKey 改回 active）。
PAUSING_FAILURES = frozenset({"key_invalid", "key_revoked"})
# 寫進 friend_gemini_keys.last_failure_reason：設定頁與後台會原樣顯示，所以存中文說明；分類碼另外存在 friend_ai_daily.error_code。
FAILURE_TEXT = {
    "key_invalid": "金鑰無效：可能已刪除或過期，請到 Gemini 設定換一把",
    "key_revoked": "金鑰被撤銷或停用：Google 拒絕這把金鑰，請到 Gemini 設定換一把",
    "quota": "今天的免費額度用完了，明天會照常再試",
    "region": "Google 在你所在的地區不提供免費額度",
    "gemini_unavailable": "Google 的模型現在太忙或暫時連不上，解讀還沒產生；稍後的班次會再試，今天沒有下一班就等下一個交易日",
    "gemini_rejected": "Google 沒有接受今天的請求，解讀沒有產生，下一個交易日會再試",
    "key_decrypt_failed": "網站讀不到你的金鑰，請到 Gemini 設定重新貼一次",
    "validation_failed": "今天的解讀沒有通過數字與代號檢查，所以沒有顯示",
    "structured_output_invalid": "今天的解讀格式不正確，所以沒有顯示",
    "gemini_failed": "今天的解讀格式不正確，所以沒有顯示",
    "input_not_ready": "今天的盤後資料還沒齊，解讀沒有產生",
    "internal_error": "網站處理時出錯，今天的解讀沒有產生",
}


class GeminiHttpError(Exception):
    """假上游與真上游共用：HTTP 狀態碼加上 Google 錯誤內容裡的 status／reason（不含金鑰）。"""

    def __init__(self, status: int, error_status: str | None = None, reasons: tuple[str, ...] = ()):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.error_status = error_status
        self.reasons = reasons


class GeminiTimeout(OSError):
    """等滿單次上限仍沒有回應：算暫時錯誤，但不重試（見 GEMINI_ATTEMPTS）。"""


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
# 有持股的朋友：和站主同一份輸出格式（大盤、籌碼、個股）。
HOLDER_SCHEMA = _definitions_inlined(OUTPUT_SCHEMA, OUTPUT_SCHEMA["$defs"])


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
    """金鑰類與額度類和 01 票試呼叫同一套分類（cloud/lib/friend-ai.js 的 trialCall）；
    試呼叫失敗一律不保存，所以那邊不必再分「暫時」與「其他」。"""
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
    if error.status >= 500 or error.status == 408:
        return "gemini_unavailable"
    return "gemini_rejected"


def _post_with_retry(post: Callable, url: str, body: dict[str, Any], api_key: str, *, sleep: Callable[[float], None],
                     attempts: int = GEMINI_ATTEMPTS,
                     note: Callable[[str, float], None] = lambda outcome, seconds: None) -> dict[str, Any]:
    """只有暫時錯誤（5xx、408、連線中斷）重試；額度、金鑰、其他錯誤與逾時都只呼叫一次。
    每一次呼叫的結果與花的秒數交給 ``note``（只有 HTTP 狀態與 Google 的 status，不含訊息原文）。"""
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        took = lambda: round(time.monotonic() - started, 1)  # noqa: E731
        try:
            reply = post(url, body, api_key)
            note("ok", took())
            return reply
        except GeminiHttpError as error:
            note(f"HTTP {error.status} {error.error_status or ''}".strip(), took())
            code = classify_http_error(error)
            if code != "gemini_unavailable" or attempt == attempts:
                raise ModelCallFailed(code, None, {"http_status": error.status, "attempts": attempt}) from None
        except GeminiTimeout as error:
            note("timeout", took())
            raise ModelCallFailed("gemini_unavailable", None, {"error": type(error).__name__, "attempts": attempt}) from None
        except OSError as error:
            note(type(error).__name__, took())
            if attempt == attempts:
                raise ModelCallFailed("gemini_unavailable", None, {"error": type(error).__name__, "attempts": attempt}) from None
        sleep(RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS)) - 1])
    raise AssertionError("unreachable")


def _request_body(prompt: str, schema: dict[str, Any], effort: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"responseMimeType": "application/json", "responseJsonSchema": schema}
    if effort:
        config["thinkingConfig"] = {"thinkingLevel": effort}
    return {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": config}


def call_gemini(prompt: str, model: str, *, api_key: str, post: Callable, url_template: str = GEMINI_URL,
                schema: dict[str, Any] = FRIEND_SCHEMA, sleep: Callable[[float], None] = time.sleep,
                fallback_model: str | None = FALLBACK_MODEL,
                tries: list[dict[str, Any]] | None = None) -> tuple[Any, dict[str, int | None]]:
    """先用 ``model``（思考程度照本人定案的「高」，gemini_models.FRIEND_DIGEST_EFFORT）；太忙、逾時或不收這個請求時
    退到 ``fallback_model``（不帶思考程度）。每一次呼叫記進 ``tries``：模型、秒數、結果；最後一筆 ok 的就是實際產生的模型。"""
    tries = [] if tries is None else tries
    plans = [(model, gemini_models.FRIEND_DIGEST_EFFORT, GEMINI_ATTEMPTS)]
    if fallback_model and fallback_model != model:
        plans.append((fallback_model, None, FALLBACK_ATTEMPTS))
    failure: ModelCallFailed | None = None
    for name, effort, attempts in plans:
        note = lambda outcome, seconds, name=name: tries.append({"model": name, "seconds": seconds, "outcome": outcome})  # noqa: E731
        try:
            reply = _post_with_retry(post, url_template.format(model=name), _request_body(prompt, schema, effort), api_key,
                                     sleep=sleep, attempts=attempts, note=note)
        except ModelCallFailed as error:
            # 回報第一個模型的原因（備援只是多試一次）；備援查到額度或金鑰問題時那才是真正的原因。
            if failure is None or error.code in NO_FALLBACK_FAILURES:
                failure = error
            if error.code in NO_FALLBACK_FAILURES:
                break
            continue
        usage = _usage(reply)
        try:
            parts = reply["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought"))
            return json.loads(text), usage
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            tries[-1]["outcome"] = "no_structured_output"
            failure = failure or ModelCallFailed("gemini_failed", usage, {"error": "no_structured_output"})
    assert failure is not None
    raise ModelCallFailed(failure.code, failure.usage, {**failure.detail, "attempts": len(tries)}) from None


def _friend_failure_rows(payload: dict[str, Any], model: str, usage: dict[str, int | None], error_code: str) -> list[dict[str, Any]]:
    return [_row(payload, digest_type, "market", "unproduced", None, model, usage, error_code) for digest_type in SECTIONS]


def _no_holdings_row(payload: dict[str, Any], model: str) -> dict[str, Any]:
    # 沒有呼叫個股判斷：用量留空，不重複記大盤那次呼叫的 token 數。
    return _row(payload, "stock", "*", "no_holdings", None, model, dict(EMPTY_USAGE))


def _stock_row(payload: dict[str, Any], rows: list[dict[str, Any]], model: str, usage: dict[str, int | None]) -> dict[str, Any]:
    """把站主格式的個股列（每檔一列＋'*' 批次列）收成朋友的一列：每檔的結果放在 content.items。"""
    per_code = [row for row in rows if row["digest_type"] == "stock" and row["subject"] != "*"]
    batch = next(row for row in rows if row["digest_type"] == "stock" and row["subject"] == "*")
    status = batch["status"]
    items = [{**row["content"], "generation_status": "generated"} if row["status"] == "generated"
             else {"code": row["subject"], "generation_status": "unproduced"} for row in per_code]
    content = None if status == "unproduced" else {
        "items": items, "count": sum(row["status"] == "generated" for row in per_code), "requested": len(per_code)}
    # 失敗原因：每檔都同一個原因（額度用完、資料還沒齊）就用它，否則用批次列的。
    codes = {row["error_code"] for row in per_code if row["status"] != "generated"}
    error_code = None if status == "generated" else codes.pop() if len(codes) == 1 else batch["error_code"]
    return _row(payload, "stock", "*", status, content, model, usage, error_code)


def _holder_failure_rows(payload: dict[str, Any], model: str, usage: dict[str, int | None], error_code: str) -> list[dict[str, Any]]:
    rows = _failure_rows(payload, model, usage, error_code)
    return [*(row for row in rows if row["digest_type"] in SECTIONS), _stock_row(payload, rows, model, usage)]


def _holder_validated_rows(payload, structured, model, usage):
    rows, rejected = _validated_rows(payload, structured, model, usage)
    return [*(row for row in rows if row["digest_type"] in SECTIONS), _stock_row(payload, rows, model, usage)], rejected


def _holds_stocks(payload: dict[str, Any]) -> bool:
    return bool(payload.get("portfolio_codes"))


def _failure_rows_for(payload: dict[str, Any], model: str, usage: dict[str, int | None], error_code: str) -> list[dict[str, Any]]:
    if _holds_stocks(payload):
        return _holder_failure_rows(payload, model, usage, error_code)
    return [*_friend_failure_rows(payload, model, usage, error_code), _no_holdings_row(payload, model)]


def _friend_validated_rows(payload, structured, model, usage):
    if not isinstance(structured, dict) or set(structured) != {"data_date", "market", "flows"}:
        return _friend_failure_rows(payload, model, usage, "structured_output_invalid"), [
            {"section": "all", "reason": "structured_output 資料日、格式或允許欄位不正確"}
        ]
    rows, rejected = _validated_rows(payload, {**structured, "stocks": []}, model, usage)
    return [row for row in rows if row["digest_type"] in SECTIONS], rejected


def _digest_one(payload: dict[str, Any], api_key: str, *, post: Callable, model: str, url_template: str,
                sleep: Callable[[float], None]) -> dict[str, Any]:
    tries: list[dict[str, Any]] = []
    # 產生成功時記實際回答的那個模型（可能是備援）；沒產生就記主要模型。
    used = lambda: next((item["model"] for item in reversed(tries) if item["outcome"] == "ok"), model)  # noqa: E731
    holder = _holds_stocks(payload)
    # 有持股：提示、輸出格式、驗證都和站主那條線同一套，只是輸入是他自己的持股。
    validate = _holder_validated_rows if holder else _friend_validated_rows
    result = digest_with_model(
        payload, prompt=build_prompt(payload) if holder else build_friend_prompt(payload), model=model,
        crash_code="gemini_failed",
        call_model=lambda prompt, chosen: call_gemini(prompt, chosen, api_key=api_key, post=post, url_template=url_template,
                                                      schema=HOLDER_SCHEMA if holder else FRIEND_SCHEMA, sleep=sleep,
                                                      tries=tries),
        validate=lambda mine, structured, _requested, usage: validate(mine, structured, used(), usage),
        failure_rows=_holder_failure_rows if holder else _friend_failure_rows,
    )
    result = {**result, "audit": {**result["audit"], "tries": tries}}
    return result if holder else {**result, "rows": [*result["rows"], _no_holdings_row(payload, used())]}


def _failed(payload: dict[str, Any], model: str, code: str, error: Exception) -> dict[str, Any]:
    usage = dict(EMPTY_USAGE)
    return {"rows": _failure_rows_for(payload, model, usage, code), "usage": usage,
            "audit": {"failure": code, "error": type(error).__name__, "rejected": []}}


def _one_friend(payload: dict[str, Any], key_row: dict[str, Any], *, decrypt: Callable, post: Callable,
                model: str, url_template: str, sleep: Callable[[float], None]) -> dict[str, Any]:
    # 每位朋友各自收尾：任何例外都只讓他自己這天「未產生」，例外內容只記類別，不記訊息（可能帶上游原文）。
    try:
        api_key = decrypt(key_row)
    except Exception as error:  # noqa: BLE001 — 解不開的原因不重要，重要的是不影響下一位
        return _failed(payload, model, "key_decrypt_failed", error)
    try:
        return _digest_one(payload, api_key, post=post, model=model, url_template=url_template, sleep=sleep)
    except Exception as error:  # noqa: BLE001
        return _failed(payload, model, "internal_error", error)
    finally:
        del api_key


def run_friends(payload: dict[str, Any], keys: list[dict[str, Any]], *, decrypt: Callable, post: Callable,
                model: str = MODEL, now: Callable[[], str] | None = None, url_template: str = GEMINI_URL,
                payload_for: Callable[[str], dict[str, Any]] | None = None,
                sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """``payload_for(email)`` 給這位朋友自己的輸入（含他的持股）；沒給就所有人共用 ``payload``（沒有持股）。"""
    now = now or (lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    rows: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    audit: list[dict[str, Any]] = []
    ivs: dict[str, Any] = {}  # 這次用的是哪一把：寫回金鑰狀態時比對，不蓋到排程中途換上的新金鑰
    for key_row in keys:
        email = key_row["email"]
        mine = payload_for(email) if payload_for else payload
        result = _one_friend(mine, key_row, decrypt=decrypt, post=post, model=model, url_template=url_template, sleep=sleep)
        at = now()
        for row in result["rows"]:
            rows.append({**row, "email": email, "created_at": at})
        generated = all(row["status"] in ("generated", "no_holdings") for row in result["rows"])
        outcomes[email] = {"outcome": "success" if generated else "failure",
                           "reason": None if generated else result["audit"]["failure"] or "validation_failed"}
        ivs[email] = key_row.get("iv")
        audit.append({"email": email, **result["audit"], "usage": result["usage"]})
    return {"rows": rows, "keys": outcomes, "audit": audit, "ivs": ivs}


def friend_sql(result: dict[str, Any]) -> list[str]:
    """每位朋友一份 SQL（各自 BEGIN／COMMIT）：寫進 friend_ai_daily 當天的成功或未產生，並更新他金鑰的
    最後成功時間／最近失敗原因（中文說明）；金鑰無效或被撤銷時暫停這把金鑰。排程逐份寫入，一位寫失敗不擋其他人。
    更新金鑰那一列時比對這次用的 iv：排程跑到一半朋友換了新金鑰，舊金鑰的結果不會蓋到新金鑰上。"""
    scripts: list[str] = []
    for email, outcome in result["keys"].items():
        rows = [row for row in result["rows"] if row["email"] == email]
        statements = ["BEGIN;"]
        for row in rows:
            if row["digest_type"] == "stock":
                statements.append(_stock_sql(email, row))
                continue
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
        at = _sql_literal(rows[0]["created_at"] if rows else None)
        same_key = f"email={_sql_literal(email)} AND iv={_sql_literal(result['ivs'][email])}"
        if outcome["outcome"] == "success":
            statements.append(f"UPDATE friend_gemini_keys SET last_success_at={at},last_failure_reason=NULL,updated_at={at} WHERE {same_key};")
        else:
            text = _sql_literal(FAILURE_TEXT.get(outcome["reason"], FAILURE_TEXT["internal_error"]))
            pause = ",status='paused'" if outcome["reason"] in PAUSING_FAILURES else ""
            statements.append(f"UPDATE friend_gemini_keys SET last_failure_reason={text}{pause},updated_at={at} WHERE {same_key};")
        statements.append("COMMIT;")
        scripts.append("\n".join(statements) + "\n")
    return scripts


def _stock_sql(email: str, row: dict[str, Any]) -> str:
    """03 票：朋友的個股判斷每人每天一列；內容只放他自己持股、通過驗證的結果。"""
    values = [
        email, row["data_date"],
        None if row["content"] is None else json.dumps(row["content"], ensure_ascii=False, separators=(",", ":")),
        row["input_sha256"], row["model"], row["input_tokens"], row["output_tokens"], row["total_tokens"],
        row["status"], row["error_code"], row["created_at"], row["created_at"],
    ]
    return (
        "INSERT INTO friend_ai_stock_daily (email,data_date,content_json,input_sha256,model,input_tokens,output_tokens,"
        "total_tokens,status,error_code,created_at,updated_at) VALUES (" + ",".join(_sql_literal(v) for v in values) +
        ") ON CONFLICT(email,data_date) DO UPDATE SET content_json=excluded.content_json,input_sha256=excluded.input_sha256,"
        "model=excluded.model,input_tokens=excluded.input_tokens,output_tokens=excluded.output_tokens,"
        "total_tokens=excluded.total_tokens,status=excluded.status,error_code=excluded.error_code,updated_at=excluded.updated_at;"
    )


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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """標頭裡有金鑰：不跟著轉址把金鑰帶去別的網址。"""

    def redirect_request(self, *args, **kwargs):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def http_post(url: str, body: dict[str, Any], api_key: str, *, timeout: float = GEMINI_TIMEOUT_SECONDS) -> dict[str, Any]:
    """真的上游：金鑰只放 x-goog-api-key 標頭，不進網址。錯誤只取 HTTP 狀態與 Google 的 status／reason。"""
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail: dict[str, Any] = {}
        try:
            detail = json.loads(error.read().decode("utf-8")).get("error") or {}
        except (ValueError, AttributeError, OSError, http.client.HTTPException):
            pass
        reasons = tuple(str(item.get("reason")) for item in detail.get("details") or [] if isinstance(item, dict))
        # 和 01 票試呼叫一樣：400 的訊息提到 API key 也算金鑰無效。訊息本身不保存。
        if "api key" in str(detail.get("message") or "").lower():
            reasons = (*reasons, "API_KEY_INVALID")
        raise GeminiHttpError(error.code, detail.get("status"), reasons) from None
    except TimeoutError as error:
        raise GeminiTimeout(type(error).__name__) from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, TimeoutError):
            raise GeminiTimeout("TimeoutError") from None
        raise OSError(type(error).__name__) from None
    except (ValueError, http.client.HTTPException) as error:
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


def try_lines(result: dict[str, Any]) -> list[str]:
    """每一次呼叫一行，給排程的執行紀錄：第幾位朋友（不放 email）、模型、秒數、結果。"""
    return [f"friend-{index:02d} {item['model']} {item['seconds']}s {item['outcome']}"
            for index, entry in enumerate(result["audit"], start=1) for item in entry.get("tries") or []]


def _summary(result: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for outcome in result["keys"].values():
        label = outcome["outcome"] if outcome["outcome"] == "success" else outcome["reason"]
        counts[label] = counts.get(label, 0) + 1
    tries: dict[str, int] = {}
    for entry in result["audit"]:
        for item in entry.get("tries") or []:
            label = f"{item['model']} {item['outcome']}"
            tries[label] = tries.get(label, 0) + 1
    return {"friends": len(result["keys"]), "outcomes": counts, "tries": tries}


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
    # 03 票：個股判斷的輸入。持股匯出只有可用金鑰的朋友（friend-ai-schedule.mjs portfolios-sql），乾跑不讀。
    run.add_argument("--stock-pack", type=Path)
    run.add_argument("--compare-pack", type=Path)
    run.add_argument("--portfolios-json", type=Path)
    run.add_argument("--sql-dir", type=Path, required=True, help="每位朋友一份 SQL，檔名只有序號、不含 email")
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

    if not args.dry_run:
        # 少了任何一個就會把有持股的朋友誤標成「沒有持股」：先擋，不讀金鑰也不呼叫 Gemini。
        for flag, value in (("--keys-json", args.keys_json), ("--stock-pack", args.stock_pack),
                            ("--compare-pack", args.compare_pack), ("--portfolios-json", args.portfolios_json)):
            if value is None:
                parser.error(f"非乾跑必須提供 {flag}")
        if not os.environ.get("FRIEND_AI_MASTER_KEY"):
            parser.error("非乾跑必須設定 FRIEND_AI_MASTER_KEY")
    payload = build_friend_input(data_date=args.date, market_dir=args.market_dir, screens_root=args.screens_root)
    prompt = build_friend_prompt(payload)
    if args.prompt_output:
        args.prompt_output.write_text(prompt, encoding="utf-8")
    packs = load_stock_packs(args.stock_pack, args.compare_pack) if args.stock_pack and args.compare_pack else None
    if args.dry_run:
        audit = {"data_date": args.date, "input_sha256": payload["input_sha256"], "dry_run": True,
                 "availability": payload["availability"], "prompt_chars": len(prompt)}
        if packs:
            audit["stock_packs"] = {"stocks": packs[0].get("as_of") == args.date, "stock_analysis": packs[0].get("as_of"),
                                    "compare_0050": packs[1].get("data_date")}
        args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(audit, ensure_ascii=False))
        return 0
    stock_pack, compare_pack = packs
    result = run_friends(payload, read_keys(args.keys_json), decrypt=node_decrypt, model=args.model,
                         post=http_post, url_template=args.gemini_url,
                         payload_for=lambda email: with_friend_portfolio(
                             payload, email=email, portfolio_path=args.portfolios_json,
                             stock_pack=stock_pack, compare_pack=compare_pack))
    args.sql_dir.mkdir(parents=True, exist_ok=True)
    for index, script in enumerate(friend_sql(result), start=1):
        (args.sql_dir / f"friend-{index:02d}.sql").write_text(script, encoding="utf-8")
    audit = {"data_date": args.date, "input_sha256": payload["input_sha256"], "model": args.model,
             **_summary(result), "friends_detail": result["audit"]}
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    for line in try_lines(result):
        print(line)
    print(json.dumps({"data_date": args.date, "model": args.model, **_summary(result)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
