# -*- coding: utf-8 -*-
"""Agent 分析/策略编排 API。"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.agent.orchestrator import AgentOrchestrator

router = APIRouter(prefix="/api/agent", tags=["agent"])
_agent = AgentOrchestrator()


class RunRequest(BaseModel):
    scope: Dict[str, Any] = Field(default_factory=dict)
    date_from: str
    date_to: str
    objective: str = "降低无效花费，保留有效流量"


class ExecuteRunRequest(BaseModel):
    confirm: bool = False


@router.post("/runs")
def create_run(req: RunRequest):
    try:
        return {"ok": True, "run": _agent.run(
            req.scope, req.date_from, req.date_to, req.objective)}
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "agent_error", "message": str(exc)[:300]})


@router.get("/runs")
def list_runs(limit: int = 50):
    return {"ok": True, "runs": _agent.list(limit)}


@router.get("/runs/{run_id}")
def get_run(run_id: str):
    run = _agent.get(run_id)
    if not run:
        return JSONResponse(status_code=404, content={
            "ok": False, "status": "not_found", "message": "run 不存在"})
    return {"ok": True, "run": run}


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str):
    run = _agent.approve(run_id)
    if not run:
        return JSONResponse(status_code=404, content={
            "ok": False, "status": "not_found", "message": "run 不存在"})
    return {"ok": True, "run": run}


@router.post("/runs/{run_id}/preview")
def preview_run(run_id: str):
    run = _agent.preview_plan(run_id)
    if not run:
        return JSONResponse(status_code=404, content={
            "ok": False, "status": "not_found", "message": "run 不存在"})
    return {"ok": True, "run": run}


@router.post("/runs/{run_id}/execute")
def execute_run(run_id: str, req: ExecuteRunRequest):
    if not req.confirm:
        return JSONResponse(status_code=428, content={
            "ok": False, "status": "confirm_required",
            "message": "执行 Agent 计划必须传 confirm=true"})
    run = _agent.execute_plan(run_id)
    if not run:
        return JSONResponse(status_code=404, content={
            "ok": False, "status": "not_found", "message": "run 不存在"})
    return {"ok": True, "run": run}
