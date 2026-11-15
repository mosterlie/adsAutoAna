# -*- coding: utf-8 -*-
"""只读查询与工具清单 API。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.ads_ops.campaign_analysis import CampaignAnalysisService
from backend.ads_ops.campaign_export import build_workbook
from backend.ads_ops.campaign_groups import CampaignGroupsService
from backend.ads_ops.errors import AdsActionError
from backend.ads_ops.query import AdsQueryService
from backend.ads_ops.tool_manifest import build_manifest

router = APIRouter(prefix="/api/ads", tags=["ads-query"])
_query = AdsQueryService()


class QueryRequest(BaseModel):
    resource: str
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class CampaignAnalysisRequest(BaseModel):
    campaign_id: str
    date_from: str
    date_to: str
    target_acos: float = Field(default=35.0, gt=0, le=500)


@router.get("/tools")
def tools() -> Dict[str, Any]:
    return build_manifest()


@router.get("/tools/actions")
def action_catalog(action: str = "") -> Dict[str, Any]:
    manifest = build_manifest()
    rows = manifest["actions"]
    if action:
        rows = [x for x in rows if x["name"] == action]
    return {"ok": True, "count": len(rows), "actions": rows}


@router.get("/campaigns")
def local_campaigns() -> Dict[str, Any]:
    rows = CampaignAnalysisService().campaigns()
    return {"ok": True, "count": len(rows), "rows": rows}


@router.get("/campaign-groups")
def live_campaign_groups(refresh: bool = False):
    """赛狐实时全部活动 + 广告组（用于总览，不依赖本地采集覆盖）。"""
    try:
        data = CampaignGroupsService().load(refresh=refresh)
        return {"ok": True, **data}
    except AdsActionError as exc:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "campaign_groups_error",
            "message": str(exc)[:300]})


@router.post("/campaign-analysis")
def campaign_analysis(req: CampaignAnalysisRequest):
    try:
        return CampaignAnalysisService().analyze(
            req.campaign_id, req.date_from, req.date_to, req.target_acos)
    except AdsActionError as exc:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "analysis_error", "message": str(exc)[:300]})


@router.post("/campaign-analysis/export")
def campaign_analysis_export(req: CampaignAnalysisRequest):
    try:
        analysis = CampaignAnalysisService().analyze(
            req.campaign_id, req.date_from, req.date_to, req.target_acos)
        stream = build_workbook(analysis)
        filename = f"campaign_analysis_{req.campaign_id}_{req.date_from}_{req.date_to}.xlsx"
        return StreamingResponse(
            stream,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    except AdsActionError as exc:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "export_error", "message": str(exc)[:300]})


@router.post("/query")
def query(req: QueryRequest):
    try:
        return _query.query(
            resource=req.resource, date_from=req.date_from, date_to=req.date_to,
            filters=req.filters, limit=req.limit, offset=req.offset)
    except AdsActionError as exc:
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=500, content={
            "ok": False, "status": "query_error", "message": str(exc)[:300]})
