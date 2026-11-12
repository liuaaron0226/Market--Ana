"""live_server 可接入的本機唯讀 ai_daily 查詢；不啟動服務、不處理身分驗證。"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

CODE_RE = re.compile(r"^[0-9A-Z]{4,6}$")


class LocalAiDigestError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _content(row: sqlite3.Row | None) -> Any:
    if row is None or not row["content_json"]:
        return None
    try:
        return json.loads(row["content_json"])
    except (TypeError, ValueError):
        return None


def _digest(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {"status": "unproduced", "content": None}
    return {
        "status": row["status"],
        "content": _content(row) if row["status"] == "generated" else None,
        "input_sha256": row["input_sha256"],
        "updated_at": row["updated_at"],
    }


class LocalAiDigestStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _empty(self, scope: str) -> dict[str, Any]:
        body: dict[str, Any] = {"data_date": None, "market": _digest(None), "flow": _digest(None)}
        if scope == "portfolio":
            body["stocks"] = {"status": "unproduced", "items": []}
        return body

    def get(self, *, scope: str = "home", owner: bool = False, codes: list[str] | None = None) -> dict[str, Any]:
        if scope not in {"home", "portfolio"}:
            raise LocalAiDigestError(400, "scope 不正確")
        if scope == "portfolio" and not owner:
            raise LocalAiDigestError(403, "只有本人可查看個股判斷")
        requested = [str(code).strip().upper() for code in (codes or [])]
        if len(requested) > 100 or any(not CODE_RE.fullmatch(code) for code in requested):
            raise LocalAiDigestError(400, "股票代號格式不正確")
        if not self.path.exists():
            return self._empty(scope)

        try:
            connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            try:
                latest = connection.execute("SELECT MAX(data_date) AS data_date FROM ai_daily").fetchone()
                if latest is None or not latest["data_date"]:
                    return self._empty(scope)
                data_date = latest["data_date"]
                rows = connection.execute(
                    "SELECT digest_type,subject,content_json,input_sha256,status,updated_at FROM ai_daily WHERE data_date=? ORDER BY digest_type,subject",
                    (data_date,),
                ).fetchall()
            finally:
                connection.close()
        except sqlite3.Error:
            return self._empty(scope)

        market = next((row for row in rows if row["digest_type"] == "market" and row["subject"] == "market"), None)
        flow = next((row for row in rows if row["digest_type"] == "flow" and row["subject"] == "market"), None)
        body: dict[str, Any] = {"data_date": data_date, "market": _digest(market), "flow": _digest(flow)}
        if scope == "portfolio":
            allowed = set(requested)
            batch = next((row for row in rows if row["digest_type"] == "stock" and row["subject"] == "*"), None)
            items = []
            for row in rows:
                if row["digest_type"] != "stock" or row["subject"] == "*" or (allowed and row["subject"] not in allowed):
                    continue
                content = _content(row) if row["status"] == "generated" else None
                items.append(
                    {**content, "generation_status": row["status"]}
                    if isinstance(content, dict)
                    else {"code": row["subject"], "generation_status": row["status"], "content": None}
                )
            body["stocks"] = {"status": batch["status"] if batch else "unproduced", "items": items}
        return body
