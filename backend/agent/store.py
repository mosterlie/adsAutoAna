# -*- coding: utf-8 -*-
"""Agent run 持久化；生产用 SQLite，测试可用内存实现。"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend import database as db

_LOCK = threading.Lock()


class AgentStore:
    def __init__(self):
        self._ready = False

    def _ensure(self):
        if self._ready:
            return
        with _LOCK:
            if self._ready:
                return
            with db.get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS agent_runs (
                        run_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        scope_json TEXT,
                        date_from TEXT,
                        date_to TEXT,
                        objective TEXT,
                        diagnosis_json TEXT,
                        plan_json TEXT,
                        coverage_json TEXT
                    )
                """)
                conn.commit()
            self._ready = True

    def save(self, run: Dict[str, Any]) -> None:
        self._ensure()
        with db.get_conn() as conn:
            conn.execute("""
                INSERT INTO agent_runs
                (run_id,status,created_at,updated_at,scope_json,date_from,date_to,
                 objective,diagnosis_json,plan_json,coverage_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    diagnosis_json=excluded.diagnosis_json,
                    plan_json=excluded.plan_json,
                    coverage_json=excluded.coverage_json
            """, (
                run["run_id"], run["status"], run["created_at"],
                run.get("updated_at") or datetime.now().isoformat(timespec="seconds"),
                json.dumps(run.get("scope") or {}, ensure_ascii=False),
                run.get("date_from") or "", run.get("date_to") or "",
                run.get("objective") or "",
                json.dumps(run.get("diagnosis") or {}, ensure_ascii=False),
                json.dumps(run.get("plan") or {}, ensure_ascii=False),
                json.dumps(run.get("coverage") or {}, ensure_ascii=False),
            ))
            conn.commit()

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        self._ensure()
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM agent_runs WHERE run_id=?",
                               (run_id,)).fetchone()
        if not row:
            return None
        return {
            "run_id": row["run_id"], "status": row["status"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "scope": json.loads(row["scope_json"] or "{}"),
            "date_from": row["date_from"], "date_to": row["date_to"],
            "objective": row["objective"],
            "diagnosis": json.loads(row["diagnosis_json"] or "{}"),
            "plan": json.loads(row["plan_json"] or "{}"),
            "coverage": json.loads(row["coverage_json"] or "{}"),
        }

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        self._ensure()
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT run_id,status,created_at,updated_at,objective "
                "FROM agent_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


class MemoryStore:
    """测试用。"""

    def __init__(self):
        self.data: Dict[str, Dict[str, Any]] = {}

    def save(self, run: Dict[str, Any]) -> None:
        self.data[run["run_id"]] = json.loads(json.dumps(run, ensure_ascii=False))

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        return json.loads(json.dumps(self.data.get(run_id), ensure_ascii=False)) \
            if run_id in self.data else None

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(self.data.values())[-limit:]
