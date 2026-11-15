# -*- coding: utf-8 -*-
"""广告写动作 FastAPI 入口。

只暴露业务动作，不暴露任意赛狐 URL。
"""
from __future__ import annotations

import os
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from backend.ads_ops import audit, registry
from backend.ads_ops.actions import HANDLERS
from backend.ads_ops.errors import AdsActionError
from backend.ads_ops.executor import Executor
from backend.ads_ops.schema import ExecuteRequest, PreviewRequest

router = APIRouter(prefix="/api/ads/actions", tags=["ads-actions"])
_executor = Executor()


def _source(request: Request) -> tuple[str, str]:
    return (request.headers.get("x-ads-source") or "api",
            request.headers.get("x-ads-actor") or "")


def _error(exc: AdsActionError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


@router.get("")
def list_actions() -> Dict[str, Any]:
    data = registry.describe()
    for action in data["actions"]:
        action["implemented"] = action["name"] in HANDLERS
        # 所有动作都走 preview -> confirm_token -> execute
        action["confirm_required"] = True
    data["write_enabled"] = os.environ.get("ADS_WRITE_ENABLED", "1").lower() in (
        "1", "true", "yes", "on")
    data["l3_enabled"] = os.environ.get("ADS_L3_ENABLED", "0").lower() in (
        "1", "true", "yes", "on")
    return data


@router.post("/preview")
def preview(req: PreviewRequest, request: Request):
    source, actor = _source(request)
    try:
        return _executor.preview(req.action, req.selector, req.params,
                                 source=source, actor=actor)
    except AdsActionError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "internal_error",
            "message": str(exc)[:300]})


@router.post("/execute")
def execute(req: ExecuteRequest, request: Request):
    source, actor = _source(request)
    try:
        return _executor.execute(req.confirm_token, action=req.action,
                                 selector=req.selector, params=req.params,
                                 source=source, actor=actor)
    except AdsActionError as exc:
        return _error(exc)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "internal_error",
            "message": str(exc)[:300]})


@router.get("/logs/{request_id}")
def action_log(request_id: str):
    try:
        return {"ok": True, "log": _executor.log(request_id)}
    except AdsActionError as exc:
        return _error(exc)
