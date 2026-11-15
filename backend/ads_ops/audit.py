# -*- coding: utf-8 -*-
"""L3 动作审计、预览令牌与幂等状态。

这里用 SQLite 持久化，不放在进程内存：
* 服务重启后，未过期的确认令牌仍然有效；
* 执行中的请求可追溯；
* request_id / token_hash 可用于防止重复执行。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from backend import database as db

_LOCK = threading.Lock()
_ENSURED = False

DDL = """
CREATE TABLE IF NOT EXISTS ad_action_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT NOT NULL UNIQUE,
  action TEXT NOT NULL,
  level TEXT NOT NULL,
  status TEXT NOT NULL,
  selector_json TEXT,
  params_json TEXT,
  payload_hash TEXT NOT NULL,
  resolved_json TEXT,
  before_json TEXT,
  after_json TEXT,
  request_json TEXT,
  response_json TEXT,
  error_json TEXT,
  warnings_json TEXT,
  rollback_json TEXT,
  token_hash TEXT UNIQUE,
  expires_at TEXT,
  used_at TEXT,
  idempotency_key TEXT,
  source TEXT,
  actor TEXT,
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ad_action_log_action
  ON ad_action_log(action, created_at);
CREATE INDEX IF NOT EXISTS idx_ad_action_log_status
  ON ad_action_log(status, created_at);
"""


def _now() -> datetime:
    return datetime.now()


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def canonical_hash(action: str, selector: Dict[str, Any],
                   params: Dict[str, Any]) -> str:
    raw = json.dumps({"action": action, "selector": selector, "params": params},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def token_hash(token: str) -> str:
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def ensure_schema() -> None:
    global _ENSURED
    if _ENSURED:
        return
    with _LOCK:
        if _ENSURED:
            return
        with db.get_conn() as conn:
            conn.executescript(DDL)
        _ENSURED = True


def _dump(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)


def _load(value: Any, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return default


def create_preview(*, request_id: str, action: str, level: str,
                   selector: Dict[str, Any], params: Dict[str, Any],
                   payload_hash: str, resolved: Dict[str, Any],
                   before: Any, after: Any, request_payload: Dict[str, Any],
                   warnings: Optional[list] = None,
                   rollback: Optional[Dict[str, Any]] = None,
                   source: str = "api", actor: str = "",
                   ttl_sec: int = 900) -> Dict[str, Any]:
    ensure_schema()
    token = "actv1_" + secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(seconds=max(60, int(ttl_sec)))
    with db.get_conn() as conn:
        conn.execute(
            """INSERT INTO ad_action_log(
                 request_id, action, level, status, selector_json, params_json,
                 payload_hash, resolved_json, before_json, after_json, request_json,
                 warnings_json, rollback_json, token_hash, expires_at, source,
                 actor, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (request_id, action, level, "preview",
             _dump(selector), _dump(params), payload_hash,
             _dump(resolved), _dump(before), _dump(after), _dump(request_payload),
             _dump(warnings or []), _dump(rollback), token_hash(token),
             _iso(expires), source, actor, _iso(now)),
        )
    return {"confirm_token": token, "expires_at": _iso(expires)}


def _row(conn, **where) -> Optional[Dict[str, Any]]:
    if not where:
        return None
    key, value = next(iter(where.items()))
    row = conn.execute(f"SELECT * FROM ad_action_log WHERE {key}=?", (value,)).fetchone()
    if not row:
        return None
    out = dict(row)
    for key in ("selector_json", "params_json", "resolved_json", "before_json",
                "after_json", "request_json", "response_json", "error_json",
                "warnings_json", "rollback_json"):
        out[key.replace("_json", "")] = _load(out.get(key))
    return out


def get_by_token(token: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with db.get_conn() as conn:
        return _row(conn, token_hash=token_hash(token))


def get_by_request(request_id: str) -> Optional[Dict[str, Any]]:
    ensure_schema()
    with db.get_conn() as conn:
        return _row(conn, request_id=request_id)


def mark_started(request_id: str) -> bool:
    """原子占用预览令牌；返回 False 表示已被使用/状态不对。"""
    ensure_schema()
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE ad_action_log
                  SET status='in_progress', started_at=?, used_at=?
                WHERE request_id=? AND status='preview' AND used_at IS NULL""",
            (_iso(_now()), _iso(_now()), request_id))
        return cur.rowcount == 1


def finish(request_id: str, *, status: str, verified: bool = False,
           response: Any = None, after: Any = None,
           error: Optional[Dict[str, Any]] = None,
           rollback: Optional[Dict[str, Any]] = None) -> None:
    ensure_schema()
    with db.get_conn() as conn:
        conn.execute(
            """UPDATE ad_action_log
                  SET status=?, verified=?, response_json=?, after_json=?,
                      error_json=?, rollback_json=?, finished_at=?
                WHERE request_id=?""",
            (status, 1 if verified else 0, _dump(response),
             _dump(after), _dump(error), _dump(rollback), _iso(_now()),
             request_id))


def public_log(record: Dict[str, Any]) -> Dict[str, Any]:
    """去掉 token_hash；保留调用方可审计的业务字段。"""
    if not record:
        return {}
    out = dict(record)
    out.pop("token_hash", None)
    return out
