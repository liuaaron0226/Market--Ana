"""以官方 agy -p 產生每日 AI 解讀，驗證後輸出 D1 upsert 資料。

只讀 agy JSON envelope 的 ``structured_output``；不解析 ``response`` 自由文字。
模型自由文字禁止數字；正式數值只由 exact evidence ref 核對 scope／code／field 後固定渲染。
agy、schema 或驗證失敗時，當天寫成 ``unproduced``，不補猜、不沿用舊內容冒充今日結果。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import gemini_models

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["data_date", "market", "flows", "stocks"],
    "properties": {
        "data_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
        "market": {
            "type": "object", "additionalProperties": False,
            "required": ["stance", "conclusion", "conclusion_refs", "points"],
            "properties": {
                "stance": {"type": "string", "enum": ["偏多", "偏空", "觀望"]},
                "conclusion": {"type": "string", "minLength": 1, "maxLength": 120},
                "conclusion_refs": {"$ref": "#/$defs/references"},
                "points": {"type": "array", "minItems": 3, "maxItems": 4, "items": {"$ref": "#/$defs/evidence"}},
            },
        },
        "flows": {
            "type": "object", "additionalProperties": False, "required": ["signals"],
            "properties": {"signals": {"type": "array", "minItems": 3, "maxItems": 5, "items": {"$ref": "#/$defs/evidence"}}},
        },
        "stocks": {
            "type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["code", "name", "status", "status_refs", "action", "reason", "reason_refs"],
                "properties": {
                    "code": {"type": "string"}, "name": {"type": "string"},
                    "status": {"type": "string", "minLength": 1, "maxLength": 100},
                    "status_refs": {"$ref": "#/$defs/references"},
                    "action": {"type": "string", "enum": ["續抱", "觀察", "留意風險"]},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 120},
                    "reason_refs": {"$ref": "#/$defs/references"},
                },
            },
        },
    },
    "$defs": {
        "evidence": {
            "type": "object", "additionalProperties": False, "required": ["text", "codes", "refs"],
            "properties": {
                "text": {"type": "string", "minLength": 1, "maxLength": 160},
                "codes": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
                "refs": {"$ref": "#/$defs/references"},
            },
        },
        "references": {
            "type": "array", "minItems": 1, "maxItems": 3,
            "items": {"type": "string", "pattern": "^/"}, "uniqueItems": True,
        },
    },
}

# 本人 2026-11-19 晚：站主 AI 解讀的模型與思考強度由設定頁「AI 模型」選，排程帶 --model／--effort 進來；這兩個是預設。
MODEL_DEFAULT = gemini_models.DIGEST
EFFORT_DEFAULT = gemini_models.DIGEST_EFFORT
BANNED_TEXT = ("Gemini", "Antigravity", "模型", "提示詞", "參考站", "參考網站", "原站", "會員", "反推", "吻合率", "方法")
CHINESE_NUMERAL_CHARS = frozenset("零〇一二三四五六七八九十百千萬万億亿兆兩两壹貳貮參参肆伍陸陆柒捌玖拾佰仟")

_FIELD_LABELS = {
    "date": "資料日", "as_of": "資料日", "price_as_of": "價格資料日",
    "close": "收盤", "change_percent": "漲跌幅", "chg_pct": "漲跌幅",
    "foreign": "外資", "trust": "投信", "dealer": "自營商",
    "foreign_oi_net_contracts": "外資期貨淨部位", "net_contracts": "期貨淨部位",
    "margin_balance_billion": "融資餘額", "net_billion": "外資買賣超",
    "trust_billion": "投信買賣超", "metric_lots": "籌碼張數", "score": "效率分數",
    "support": "支撐", "resistance": "壓力", "volume": "成交量", "latest_price": "最新價",
    "last_price": "前次價格", "average_cost": "平均成本", "shares": "股數",
    "market_value": "市值", "cost_basis": "成本", "unrealized_pnl": "未實現損益",
    "return_pct": "報酬率", "ma5": "五日均線", "ma10": "十日均線",
    "ma20": "二十日均線", "ma60": "六十日均線", "ma120": "一百二十日均線",
    "ma240": "二百四十日均線", "macd": "MACD", "macd_dea": "MACD 訊號線",
    "macd_dif": "MACD 差離值", "k9": "KD K值", "d9": "KD D值",
    "capital_side": "資金方向",
}
_PERCENT_FIELDS = {"change_percent", "chg_pct", "return_pct", "relative_pct", "atr_pct"}
_PRICE_FIELDS = {"close", "support", "resistance", "latest_price", "last_price", "average_cost", "ma5", "ma10", "ma20", "ma60", "ma120", "ma240", "high20_close", "low20_close", "low20_intraday"}


def _pointer(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


def _fact_meta(path: tuple[str, ...], scope: str) -> tuple[str, str] | None:
    field = path[-1]
    if field == "status" and len(path) > 1:
        if path[-2] == "trend_classification":
            return "趨勢", ""
        if path[-2] == "defense":
            return "防守狀態", ""
    label = _FIELD_LABELS.get(field)
    if label is None:
        return None
    if field in _PERCENT_FIELDS:
        unit = "%"
    elif field == "metric_lots":
        unit = "張"
    elif field in {"foreign_oi_net_contracts", "net_contracts"}:
        unit = "口"
    elif field in {"margin_balance_billion", "net_billion", "trust_billion"}:
        unit = "億元"
    elif field in {"shares", "volume"}:
        unit = "股"
    elif field in {"market_value", "cost_basis", "unrealized_pnl"}:
        unit = "元"
    elif field == "score":
        unit = "分"
    elif field in _PRICE_FIELDS:
        unit = "點" if scope == "market" else "元"
    else:
        unit = ""
    return label, unit


def _collect_facts(value: Any, path: tuple[str, ...], scope: str, code: str | None,
                   output: dict[str, dict[str, Any]]) -> None:
    if isinstance(value, dict):
        nested_code = str(value.get("code")) if value.get("code") is not None else code
        for key, item in value.items():
            _collect_facts(item, (*path, str(key)), scope, nested_code, output)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _collect_facts(item, (*path, str(index)), scope, code, output)
        return
    if value is None or not isinstance(value, (str, int, float, bool)):
        return
    meta = _fact_meta(path, scope)
    if meta is None:
        return
    ref = _pointer(path)
    output[ref] = {
        "ref": ref, "scope": scope, "code": code, "field": path[-1],
        "label": meta[0], "value": value, "unit": meta[1],
    }


def _evidence_catalog(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    _collect_facts(payload.get("market") or {}, ("market",), "market", None, catalog)
    _collect_facts(payload.get("flows") or {}, ("flows",), "flow", None, catalog)
    for code, item in (payload.get("stocks") or {}).items():
        _collect_facts(item, ("stocks", str(code)), "stock", str(code), catalog)
    for group in ("watchlist", "holdings"):
        for index, item in enumerate((payload.get("portfolio") or {}).get(group) or []):
            code = str(item.get("code") or "")
            if code:
                _collect_facts(item, ("portfolio", group, str(index)), "stock", code, catalog)
    for code, item in ((payload.get("compare_0050") or {}).get("stocks") or {}).items():
        _collect_facts(item, ("compare_0050", "stocks", str(code)), "stock", str(code), catalog)
    return catalog


def build_prompt(payload: dict[str, Any]) -> str:
    catalog = list(_evidence_catalog(payload).values())
    return "\n".join([
        "你是台股盤後資料整理員。只可根據 INPUT_JSON 與 EVIDENCE_CATALOG 做質化判斷，不可連網、不可抓資料、不可使用工具、不可自創指標。",
        "所有自由文字欄位只能寫繁體中文質化判斷，不得出現阿拉伯／全形／中文數字、股票代號、日期、價格、數量或百分比。",
        "每個結論、重點、訊號、個股狀態與理由都必須填入一至三個 exact refs。程式會核對 scope、股票代號與 exact path，並以固定標籤、值及單位補上事實。",
        "market 只能引用 scope=market；flows 只能引用 scope=flow，帶 code 的 ref 必須列在該訊號 codes；stocks 只能引用 scope=stock 且 code 完全相同。資料不足就不要補猜。",
        "stocks：portfolio_codes 每檔一筆，action 只可用續抱／觀察／留意風險。不要寫模型名稱、提示詞、參考站、會員、反推、吻合率、方法說明或保證獲利。",
        "INPUT_JSON:",
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "EVIDENCE_CATALOG:",
        json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    ])


def _parse_envelope(text: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            continue
    raise ValueError("agy 沒有輸出 JSON envelope")


def _agy_command(agy_bin: Path | str, prompt: str, model: str, effort: str = EFFORT_DEFAULT) -> list[str]:
    path = Path(agy_bin)
    prefix = [sys.executable, str(path)] if path.suffix.lower() == ".py" else [str(path)]
    return [
        *prefix, "-p", prompt, "--model", model, "--effort", effort, "--output-format", "json",
        "--json-schema", json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, separators=(",", ":")),
        "--print-timeout", "300s", "--sandbox",
    ]


def _token_usage(envelope: dict[str, Any]) -> dict[str, int | None]:
    aliases = {
        "input_tokens": {"input_tokens", "prompt_tokens", "inputtokencount", "input_tokens_count"},
        "output_tokens": {"output_tokens", "completion_tokens", "outputtokencount", "output_tokens_count"},
        "total_tokens": {"total_tokens", "totaltokencount", "total_tokens_count"},
    }
    found: dict[str, int | None] = {key: None for key in aliases}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = re.sub(r"[^a-z0-9_]", "", str(key).lower())
                for target, names in aliases.items():
                    if normalized in names and isinstance(item, (int, float)):
                        found[target] = int(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk({key: value for key, value in envelope.items() if key not in {"response", "structured_output"}})
    if found["total_tokens"] is None and found["input_tokens"] is not None and found["output_tokens"] is not None:
        found["total_tokens"] = found["input_tokens"] + found["output_tokens"]
    return found


def _validate_text(text: Any) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return "文字為空"
    if any(term.lower() in text.lower() for term in BANNED_TEXT):
        return "包含正式頁禁止字樣"
    if any(character.isnumeric() or character in CHINESE_NUMERAL_CHARS for character in text):
        return "自由文字不得包含數字；請改用來源 ref"
    return None


def _validate_refs(refs: Any, catalog: dict[str, dict[str, Any]], *, scope: str,
                   code: str | None = None, codes: set[str] | None = None) -> str | None:
    if not isinstance(refs, list) or not 1 <= len(refs) <= 3 or any(not isinstance(ref, str) for ref in refs):
        return "來源 refs 必須是一至三個 exact path"
    if len(set(refs)) != len(refs):
        return "來源 refs 不得重複"
    for ref in refs:
        fact = catalog.get(ref)
        if fact is None:
            return f"來源 ref 不存在：{ref}"
        if fact["scope"] != scope:
            return f"來源 ref scope 不符：{ref}"
        if scope == "stock" and fact.get("code") != code:
            return f"來源 ref 股票代號不符：{ref}"
        if scope == "flow" and fact.get("code") is not None and fact["code"] not in (codes or set()):
            return f"來源 ref 股票代號未綁定：{ref}"
    return None


def _format_number(value: int | float) -> str:
    try:
        rendered = format(Decimal(str(value)), ",f")
    except InvalidOperation:
        return str(value)
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _render_fact(fact: dict[str, Any]) -> str:
    value = fact["value"]
    if isinstance(value, bool):
        rendered = "是" if value else "否"
    elif isinstance(value, (int, float)):
        rendered = _format_number(value)
    else:
        rendered = str(value)
    unit = str(fact.get("unit") or "")
    suffix = unit if unit == "%" else f" {unit}" if unit else ""
    return f"{fact['label']} {rendered}{suffix}"


def _render_text(text: str, refs: list[str], catalog: dict[str, dict[str, Any]]) -> str:
    facts = "；".join(_render_fact(catalog[ref]) for ref in refs)
    return f"{text.strip()}【資料：{facts}】"


def _validate_evidence(item: Any, allowed_codes: set[str], catalog: dict[str, dict[str, Any]],
                       *, scope: str) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(item, dict):
        return None, "項目格式不正確"
    if set(item) != {"text", "codes", "refs"}:
        return None, "項目含 schema 未允許欄位"
    codes = item.get("codes")
    if not isinstance(codes, list) or any(not isinstance(code, str) for code in codes):
        return None, "代號欄位格式不正確"
    unknown = sorted(set(codes) - allowed_codes)
    if unknown:
        return None, f"代號不在輸入名單：{', '.join(unknown)}"
    text_reason = _validate_text(item.get("text"))
    if text_reason:
        return None, text_reason
    refs = item.get("refs")
    ref_reason = _validate_refs(refs, catalog, scope=scope, codes=set(codes))
    if ref_reason:
        return None, ref_reason
    return {"text": _render_text(item["text"], refs, catalog), "codes": codes, "refs": refs}, None


def _row(payload: dict[str, Any], digest_type: str, subject: str, status: str, content: Any,
         model: str, usage: dict[str, int | None], error_code: str | None = None) -> dict[str, Any]:
    return {
        "data_date": payload["data_date"], "digest_type": digest_type, "subject": subject,
        "content": content, "input_sha256": payload["input_sha256"], "model": model,
        "input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"], "status": status, "error_code": error_code,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _failure_rows(payload: dict[str, Any], model: str, usage: dict[str, int | None], error_code: str) -> list[dict[str, Any]]:
    rows = [
        _row(payload, "market", "market", "unproduced", None, model, usage, error_code),
        _row(payload, "flow", "market", "unproduced", None, model, usage, error_code),
        _row(payload, "stock", "*", "unproduced", None, model, usage, error_code),
    ]
    rows.extend(_row(payload, "stock", code, "unproduced", None, model, usage, error_code) for code in payload.get("portfolio_codes", payload.get("allowed_codes", [])))
    return rows


def _validated_rows(payload: dict[str, Any], structured: Any, model: str,
                    usage: dict[str, int | None]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rejected: list[dict[str, Any]] = []
    expected_root = {"data_date", "market", "flows", "stocks"}
    if (not isinstance(structured, dict) or structured.get("data_date") != payload.get("data_date")
            or set(structured) != expected_root):
        return _failure_rows(payload, model, usage, "structured_output_invalid"), [
            {"section": "all", "reason": "structured_output 資料日、格式或允許欄位不正確"}
        ]

    allowed_codes = set(payload.get("allowed_codes") or [])
    portfolio_codes = set(payload.get("portfolio_codes", payload.get("allowed_codes", [])) or [])
    catalog = _evidence_catalog(payload)
    availability = payload.get("availability") or {}
    rows: list[dict[str, Any]] = []

    market = structured.get("market") if isinstance(structured.get("market"), dict) else {}
    market_shape_ok = set(market) == {"stance", "conclusion", "conclusion_refs", "points"}
    market_reason = None if market_shape_ok else "market 含 schema 未允許或缺少欄位"
    if market_reason is None:
        market_reason = _validate_text(market.get("conclusion"))
    if market_reason is None:
        market_reason = _validate_refs(market.get("conclusion_refs"), catalog, scope="market")
    market_points = market.get("points") if isinstance(market.get("points"), list) else []
    if market_reason is None and not isinstance(market.get("points"), list):
        market_reason = "market points 格式不正確"
    valid_points = []
    for index, item in enumerate(market_points):
        rendered, reason = _validate_evidence(item, allowed_codes, catalog, scope="market")
        if reason:
            rejected.append({"section": "market.points", "index": index, "reason": reason})
        else:
            valid_points.append(rendered)
    stance = market.get("stance")
    market_ok = (availability.get("market") is True and stance in {"偏多", "偏空", "觀望"}
                 and market_reason is None and len(valid_points) == len(market_points)
                 and 3 <= len(valid_points) <= 4)
    if market_reason:
        rejected.append({"section": "market.conclusion", "reason": market_reason})
    market_content = None
    if market_ok:
        market_content = {
            "stance": stance,
            "conclusion": _render_text(market["conclusion"], market["conclusion_refs"], catalog),
            "conclusion_refs": market["conclusion_refs"],
            "points": valid_points,
        }
    rows.append(_row(payload, "market", "market", "generated" if market_ok else "unproduced", market_content,
                     model, usage, None if market_ok else ("input_not_ready" if not availability.get("market") else "validation_failed")))

    flows = structured.get("flows") if isinstance(structured.get("flows"), dict) else {}
    flow_shape_ok = set(flows) == {"signals"} and isinstance(flows.get("signals"), list)
    flow_signals = flows.get("signals") if isinstance(flows.get("signals"), list) else []
    valid_signals = []
    for index, item in enumerate(flow_signals):
        rendered, reason = _validate_evidence(item, allowed_codes, catalog, scope="flow")
        if reason:
            rejected.append({"section": "flows.signals", "index": index, "reason": reason})
        else:
            valid_signals.append(rendered)
    if not flow_shape_ok:
        rejected.append({"section": "flows", "reason": "flows 含 schema 未允許或缺少欄位"})
    flow_ok = (availability.get("flow") is True and flow_shape_ok
               and len(valid_signals) == len(flow_signals) and 3 <= len(valid_signals) <= 5)
    rows.append(_row(payload, "flow", "market", "generated" if flow_ok else "unproduced",
                     {"signals": valid_signals} if flow_ok else None, model, usage,
                     None if flow_ok else ("input_not_ready" if not availability.get("flow") else "validation_failed")))

    output_stocks: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(structured.get("stocks") or []):
        if not isinstance(item, dict):
            rejected.append({"section": "stocks", "index": index, "reason": "項目格式不正確"})
            continue
        if set(item) != {"code", "name", "status", "status_refs", "action", "reason", "reason_refs"}:
            rejected.append({"section": "stocks", "index": index, "reason": "項目含 schema 未允許欄位"})
            continue
        code = item.get("code")
        if code not in portfolio_codes:
            rejected.append({"section": "stocks", "index": index, "reason": f"代號不在個人清單：{code}"})
            continue
        expected_name = ((payload.get("stocks") or {}).get(code) or {}).get("name")
        if expected_name and item.get("name") != expected_name:
            rejected.append({"section": "stocks", "code": code, "reason": "名稱與輸入不一致"})
            continue
        if item.get("action") not in {"續抱", "觀察", "留意風險"}:
            rejected.append({"section": "stocks", "code": code, "reason": "動作不在允許值"})
            continue
        validation_reason = _validate_text(item.get("status")) or _validate_text(item.get("reason"))
        if validation_reason is None:
            validation_reason = _validate_refs(item.get("status_refs"), catalog, scope="stock", code=code)
        if validation_reason is None:
            validation_reason = _validate_refs(item.get("reason_refs"), catalog, scope="stock", code=code)
        if validation_reason:
            rejected.append({"section": "stocks", "code": code, "reason": validation_reason})
            continue
        output_stocks.setdefault(code, {
            "code": code, "name": item.get("name"),
            "status": _render_text(item["status"], item["status_refs"], catalog),
            "status_refs": item["status_refs"], "action": item["action"],
            "reason": _render_text(item["reason"], item["reason_refs"], catalog),
            "reason_refs": item["reason_refs"],
        })

    generated_count = 0
    for code in sorted(portfolio_codes):
        item = output_stocks.get(code)
        ok = availability.get("stocks") is True and item is not None
        generated_count += int(ok)
        rows.append(_row(payload, "stock", code, "generated" if ok else "unproduced", item if ok else None,
                         model, usage, None if ok else ("input_not_ready" if not availability.get("stocks") else "validation_failed")))
    total = len(portfolio_codes)
    batch_status = "generated" if generated_count == total else "partial" if generated_count else "unproduced"
    rows.append(_row(payload, "stock", "*", batch_status, {"count": generated_count, "requested": total}, model, usage,
                     None if batch_status == "generated" else "validation_failed"))
    return rows, rejected


EMPTY_USAGE: dict[str, int | None] = {"input_tokens": None, "output_tokens": None, "total_tokens": None}


class ModelCallFailed(Exception):
    """模型沒有回來可驗證的結構化輸出；``code`` 會寫進每一列的 error_code。

    ``detail`` 只放可以進稽核紀錄的資訊（結束碼、例外類別），不放金鑰、提示或上游原文。
    """

    def __init__(self, code: str, usage: dict[str, int | None] | None = None, detail: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.usage = usage or dict(EMPTY_USAGE)
        self.detail = detail or {}


def call_agy(prompt: str, model: str, *, agy_bin: Path | str,
             effort: str = EFFORT_DEFAULT) -> tuple[Any, dict[str, int | None]]:
    """站主那條線：用官方 agy -p 取 structured_output。失敗一律丟 ModelCallFailed("agy_failed")。"""
    envelope: dict[str, Any] = {}
    try:
        completed = subprocess.run(
            _agy_command(agy_bin, prompt, model, effort), input="", text=True, encoding="utf-8",
            capture_output=True, timeout=360, check=False,
        )
        # 輸出不是 UTF-8 時 stdout 會是 None；當成沒有 envelope，走 agy_failed。
        envelope = _parse_envelope(completed.stdout or "")
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise ModelCallFailed("agy_failed", _token_usage(envelope), {"error": type(error).__name__}) from None
    usage = _token_usage(envelope)
    if completed.returncode != 0 or envelope.get("status") != "SUCCESS":
        raise ModelCallFailed("agy_failed", usage, {"returncode": completed.returncode})
    # 明確只讀 structured_output；response 即使混入狀態欄也完全不使用。
    return envelope.get("structured_output"), usage


def digest_with_model(payload: dict[str, Any], *, prompt: str, call_model, model: str,
                      validate=None, failure_rows=None, crash_code: str = "model_failed") -> dict[str, Any]:
    """共用的驗證與失敗處理：agy 與 Gemini API 都走這裡。

    ``call_model(prompt, model)`` 回傳 (structured, usage)，失敗丟 ModelCallFailed。
    失敗時當天每一類都標「未產生」，不補猜、不沿用舊內容。
    """
    validate = validate or _validated_rows
    failure_rows = failure_rows or _failure_rows
    usage = dict(EMPTY_USAGE)
    try:
        structured, usage = call_model(prompt, model)
        rows, rejected = validate(payload, structured, model, usage)
    except ModelCallFailed as failure:
        return {"rows": failure_rows(payload, model, failure.usage, failure.code), "usage": failure.usage,
                "audit": {"failure": failure.code, **failure.detail, "rejected": []}}
    except ValueError as error:
        return {"rows": failure_rows(payload, model, usage, crash_code), "usage": usage,
                "audit": {"failure": crash_code, "error": type(error).__name__, "rejected": []}}
    return {"rows": rows, "usage": usage, "audit": {"failure": None, "rejected": rejected}}


def run_digest(payload: dict[str, Any], *, agy_bin: Path | str, model: str = MODEL_DEFAULT,
               effort: str = EFFORT_DEFAULT) -> dict[str, Any]:
    return digest_with_model(
        payload, prompt=build_prompt(payload), model=model, crash_code="agy_failed",
        call_model=lambda prompt, chosen: call_agy(prompt, chosen, agy_bin=agy_bin, effort=effort),
    )


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(int(value))
    return "'" + str(value).replace("'", "''") + "'"


def d1_sql(rows: list[dict[str, Any]]) -> str:
    # 不寫 BEGIN／COMMIT：wrangler 執行檔案時自己包成一個交易，D1 遇到 BEGIN 會整份拒絕（2026-11-20 朋友那一份因此寫不進去）。
    statements = []
    for row in rows:
        values = [
            row["data_date"], row["digest_type"], row["subject"],
            None if row["content"] is None else json.dumps(row["content"], ensure_ascii=False, separators=(",", ":")),
            row["input_sha256"], row["model"], row["input_tokens"], row["output_tokens"], row["total_tokens"],
            row["status"], row["error_code"], row["created_at"], row["created_at"],
        ]
        statements.append(
            "INSERT INTO ai_daily (data_date,digest_type,subject,content_json,input_sha256,model,input_tokens,output_tokens,total_tokens,status,error_code,created_at,updated_at) VALUES (" +
            ",".join(_sql_literal(value) for value in values) + ") ON CONFLICT(data_date,digest_type,subject) DO UPDATE SET "
            "content_json=excluded.content_json,input_sha256=excluded.input_sha256,model=excluded.model,input_tokens=excluded.input_tokens,"
            "output_tokens=excluded.output_tokens,total_tokens=excluded.total_tokens,status=excluded.status,error_code=excluded.error_code,updated_at=excluded.updated_at;"
        )
    return "\n".join(statements) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--agy-bin", type=Path)
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--effort", default=EFFORT_DEFAULT, help="思考強度 low／medium／high（設定頁 AI 模型）")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--sql-output", type=Path)
    parser.add_argument("--prompt-output", type=Path)
    parser.add_argument("--schema-output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--mark-failure", help="不呼叫 agy，直接把當日三類標成未產生（例如 agy_setup_failed）")
    args = parser.parse_args(argv)
    # 排程讀設定那一步萬一沒寫進環境變數會帶空字串：照預設，不把空的名稱送給 agy
    args.model, args.effort = args.model or MODEL_DEFAULT, args.effort or EFFORT_DEFAULT

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if args.prompt_output:
        args.prompt_output.write_text(build_prompt(payload), encoding="utf-8")
    if args.schema_output:
        args.schema_output.write_text(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.dry_run:
        result = {"rows": [], "usage": {"input_tokens": None, "output_tokens": None, "total_tokens": None},
                  "audit": {"dry_run": True, "failure": None, "rejected": []}}
    elif args.mark_failure:
        usage = dict(EMPTY_USAGE)
        result = {"rows": _failure_rows(payload, args.model, usage, args.mark_failure), "usage": usage,
                  "audit": {"failure": args.mark_failure, "rejected": []}}
    else:
        if args.agy_bin is None:
            parser.error("非 dry-run 必須提供 --agy-bin")
        result = run_digest(payload, agy_bin=args.agy_bin, model=args.model, effort=args.effort)
    args.output.write_text(json.dumps({"rows": result["rows"], "usage": result["usage"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = {"data_date": payload.get("data_date"), "input_sha256": payload.get("input_sha256"), "model": args.model,
             "effort": args.effort, **result["audit"]}
    args.audit.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.sql_output and result["rows"]:
        args.sql_output.write_text(d1_sql(result["rows"]), encoding="utf-8")
    print(json.dumps({"data_date": payload.get("data_date"), "rows": len(result["rows"]),
                      "failure": result["audit"].get("failure"), "rejected": len(result["audit"].get("rejected", [])),
                      "usage": result["usage"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
