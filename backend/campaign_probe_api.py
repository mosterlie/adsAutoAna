# -*- coding: utf-8 -*-
"""活动全量取数（Campaign Probe）

给一个广告活动 + 时间区间，把该活动下**各层级**的指标一次性拉回来，供人工核对。

接口：
    GET  /api/probe/status      登录态自检
    GET  /api/probe/campaigns   活动候选（供选择器用）
    POST /api/probe/fetch       全量取数（核心）

覆盖层级（与 docs/ads_data_fetch_guide.md 一致）：
    活动 → 广告组 → 投放(自动/关键词/商品定向) → 广告产品 → 广告位
    + 搜索词 / 搜索 ASIN / ABA 排名 / 否定项 / 建议竞价 / 操作日志

设计要点：
    * 每个层级独立 try/except，单层失败不影响整体，错误汇总到 errors
    * 全部返回原始字段（不裁剪），前端按需展示，便于人工核对
    * 分时按天循环，天数上限可配（默认 7 天），避免一次请求过多
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import config
from backend.cookies import load_cookies

router = APIRouter(prefix="/api/probe", tags=["probe"])

GW = "https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox"
ORIGIN = "https://www.sellfox.com"
ABA_TREND_API = "https://www.sellfox.com/api/search/terms/analysis/getTrend.json"
PRODUCT_API = ("https://www.sellfox.com/api/gw/sellfox/sellfox-product/"
               "sellfox/product/pageList")

DEFAULT_SHOP_ID = "711811"
DEFAULT_MARKETPLACE = "A1VC38T7YXB528"
SITE_DOMAIN = "Amazon.co.jp"

MAX_RANGE_DAYS = 92
MAX_HOURLY_DAYS = 10
MAX_ABA_TERMS = 20


# ---------------------------------------------------------------------------
# 基础请求
# ---------------------------------------------------------------------------
def _headers(ctype: str = "application/json") -> Dict[str, str]:
    return {
        "Content-Type": ctype,
        "Accept": "application/json, text/plain, */*",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/cpc-vue3/web/cpc-vue3/ad-create/index.html",
        "X-Language": "zh-CN",
        "User-Agent": config.USER_AGENT,
    }


def _cookie_header() -> Dict[str, str]:
    ck = load_cookies()
    if not ck:
        raise HTTPException(
            status_code=401,
            detail="未找到赛狐登录 Cookie，请先点「同步登录态」或在 9222 调试 Chrome 登录赛狐")
    return ck


def safe_post(path: str, body: Dict[str, Any], *, base: str = GW,
              timeout: int = 60) -> Any:
    """POST JSON，返回 data（已反序列化）。失败抛异常，由调用方兜住。"""
    if path.startswith("http"):
        url = path
    else:
        url = base.rstrip("/") + "/" + path.lstrip("/")
    r = requests.post(url, json=body, headers=_headers(), cookies=_cookie_header(),
                      timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"code={j.get('code')} msg={j.get('msg')}")
    data = j.get("data")
    if isinstance(data, str):
        import json as _json
        try:
            data = _json.loads(data)
        except Exception:                       # noqa: BLE001
            pass
    return data


def safe_post_form(url: str, form: Dict[str, str], *, timeout: int = 60) -> Any:
    r = requests.post(url, data=form,
                      headers=_headers("application/x-www-form-urlencoded"),
                      cookies=_cookie_header(), timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}")
    j = r.json()
    if j.get("code") != 0:
        raise RuntimeError(f"code={j.get('code')} msg={j.get('msg')}")
    data = j.get("data")
    if isinstance(data, str):
        import json as _json
        try:
            data = _json.loads(data)
        except Exception:                       # noqa: BLE001
            pass
    return data


def _rows(data: Any, *keys: str) -> List[Dict[str, Any]]:
    """兼容 {page:{rows}} / {rows} / 裸 list 三种返回形态"""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    page = data.get("page") if isinstance(data.get("page"), dict) else data
    for k in ("rows",) + keys:
        v = page.get(k)
        if isinstance(v, list):
            return v
    return []


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _date_list(start: str, end: str) -> List[str]:
    s, e = _d(start), _d(end)
    if e < s:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
    n = (e - s).days + 1
    if n > MAX_RANGE_DAYS:
        raise HTTPException(status_code=400, detail=f"区间最多 {MAX_RANGE_DAYS} 天")
    return [(s + timedelta(days=i)).isoformat() for i in range(n)]


# ---------------------------------------------------------------------------
# 各层取数
# ---------------------------------------------------------------------------
def layer_campaign_list(shop_id: str, start: str, end: str,
                        search_value: str = "", page_size: int = 200) -> List[Dict[str, Any]]:
    body = {
        "shopIdList": [shop_id], "adType": "sp",
        "statusList": ["enabled", "paused"],
        "startDate": start, "endDate": end,
        "orderField": "adCost", "orderType": "desc",
        "pageNo": 1, "pageSize": page_size, "fromSingle": True,
    }
    if search_value:
        body.update({"searchType": "contains", "searchField": "name",
                     "searchValue": search_value})
    return _rows(safe_post("campaign/getAllCampaignData", body))


def layer_campaign_info(campaign_id: str) -> Dict[str, Any]:
    return safe_post("adCampaign/getInfo",
                     {"shopId": DEFAULT_SHOP_ID, "campaignId": campaign_id,
                      "type": "sp"}) or {}


def layer_campaign_range(campaign_id: str, start: str, end: str) -> Dict[str, Any]:
    rows = layer_campaign_list(DEFAULT_SHOP_ID, start, end)
    return next((r for r in rows if str(r.get("campaignId")) == str(campaign_id)), {})


def layer_campaign_daily(campaign_id: str, days: List[str]) -> List[Dict[str, Any]]:
    out = []
    for day in days:
        try:
            rows = layer_campaign_list(DEFAULT_SHOP_ID, day, day)
            c = next((r for r in rows if str(r.get("campaignId")) == str(campaign_id)), None)
            if c:
                out.append({"date": day, "row": c})
        except Exception as e:                  # noqa: BLE001
            out.append({"date": day, "error": str(e)[:120]})
        time.sleep(0.15)
    return out


def layer_hourly(campaign_id: str, days: List[str]) -> List[Dict[str, Any]]:
    """活动级 24 小时（dateModel=HOURLY），逐天取，天数有上限"""
    out = []
    for day in days[-MAX_HOURLY_DAYS:]:
        body = {
            "orderField": None, "orderType": None,
            "shopIdList": [int(DEFAULT_SHOP_ID)], "shopId": DEFAULT_SHOP_ID,
            "adType": "sp", "type": "sp", "targetType": "auto",
            "matchTypes": [], "productTargetTypes": [],
            "campaignIdList": [campaign_id], "groupIdList": [],
            "servingStatusList": [], "statusList": ["enabled", "paused"],
            "adStrategyTypeList": [], "devIds": [],
            "startDate": day, "endDate": day,
            "analyzeStartDate": day, "analyzeEndDate": day,
            "dateModel": "HOURLY",
            "weekList": ["1", "2", "3", "4", "5", "6", "7"],
            "isCompare": 0, "isHide": True, "fromSingle": True,
            "searchType": "exact", "searchField": "name", "searchValue": None,
            "wordRoot": "", "useAdvanced": False,
            "pageNo": 1, "pageSize": 200, "advanceFilter": {}, "targetId": "",
        }
        try:
            data = safe_post("multiple/target/getAllTargetHourData", body) or {}
            cols = {}
            for m in (data.get("hour") or []):
                cols[m.get("name")] = [x.get("value") for x in (m.get("records") or [])]
            out.append({"date": day, "metrics": cols,
                        "periodReportList": data.get("periodReportList") or [],
                        "aggregate": data.get("aggregateDataVo") or {}})
        except Exception as e:                  # noqa: BLE001
            out.append({"date": day, "error": str(e)[:120]})
        time.sleep(0.2)
    return out


def layer_groups(campaign_id: str, start: str, end: str) -> List[Dict[str, Any]]:
    return _rows(safe_post("multiple/group/getAllGroupData", {
        "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [campaign_id],
        "adType": "sp", "startDate": start, "endDate": end}))


def layer_targets(campaign_id: str, start: str, end: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for tt in ("auto", "keyword", "targeting"):
        try:
            out[tt] = _rows(safe_post("multiple/target/getAllTargetData", {
                "orderField": "adCost", "orderType": "desc",
                "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [campaign_id],
                "adType": "sp", "targetType": tt, "matchTypes": [],
                "statusList": ["enabled", "paused"],
                "startDate": start, "endDate": end,
                "pageNo": 1, "pageSize": 500, "fromSingle": True,
                "useAdvanced": False}))
        except Exception as e:                  # noqa: BLE001
            out[tt] = []
            out.setdefault("_errors", []).append(f"targets[{tt}]: {str(e)[:100]}")
        time.sleep(0.15)
    return out


def layer_products(campaign_id: str, start: str, end: str) -> List[Dict[str, Any]]:
    return _rows(safe_post("multiple/adProduct/getAdProductList", {
        "type": "sp", "campaignIdList": [campaign_id],
        "shopIdList": [DEFAULT_SHOP_ID],
        "statusList": ["enabled", "paused"],
        "startDate": start, "endDate": end,
        "pageNo": 1, "pageSize": 500, "orderBy": "adCost", "desc": True,
        "searchContents": [], "searchMode": "exact", "searchType": "asin",
        "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": False}))


def layer_placements(campaign_id: str, start: str, end: str) -> List[Dict[str, Any]]:
    return _rows(safe_post("multiple/placement/getAllPlacementData", {
        "dxmCampaignId": "", "campaignSite": "", "orderField": "adCost",
        "orderType": "desc", "adType": "sp", "campaignIdList": [campaign_id],
        "devIds": [], "strategyType": "", "predicate": "",
        "startDate": start, "endDate": end, "isCompare": False,
        "compareStartDate": "", "compareEndDate": "",
        "adStrategyTypeList": [], "pageNo": 1, "pageSize": 20, "fromSingle": True}))


def layer_search(campaign_id: str, start: str, end: str,
                 qtype: str) -> List[Dict[str, Any]]:
    return _rows(safe_post("multiple/search/getAllSearchData", {
        "orderField": "adCost", "orderType": "desc",
        "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [campaign_id],
        "adType": "sp", "searchQueryType": qtype,
        "statusList": ["enabled", "paused"], "servingStatusList": [],
        "startDate": start, "endDate": end,
        "pageNo": 1, "pageSize": 500, "fromSingle": True, "useAdvanced": False}))


def layer_negatives(campaign_id: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for nt in ("keyword", "target"):
        try:
            out[nt] = _rows(safe_post("multiple/neTarget/getAllNeTargetData", {
                "orderField": "adCost", "orderType": "desc",
                "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [campaign_id],
                "adType": "sp", "neType": nt, "neLevels": ["group"],
                "matchType": "", "servingStatusList": [],
                "startDate": None, "endDate": None,
                "neBeforeAfterDay": 30, "dataFrom": 2,
                "searchType": "exact", "searchField": "name", "searchValue": "",
                "devIds": [], "useAdvanced": False,
                "pageNo": 1, "pageSize": 500, "onlyShowImpressions": False}))
        except Exception as e:                  # noqa: BLE001
            out[nt] = []
            out.setdefault("_errors", []).append(f"ne[{nt}]: {str(e)[:100]}")
        time.sleep(0.15)
    return out


def layer_logs(campaign_id: str, start: str, end: str) -> List[Dict[str, Any]]:
    rows = _rows(safe_post("log/sellfoxAndAuto/getPage", {
        "types": ["sp"], "shopIdList": [DEFAULT_SHOP_ID],
        "startDate": start, "endDate": end,
        "pageNo": 1, "pageSize": 200}))
    return [r for r in rows if str(r.get("campaignId")) == str(campaign_id)]


def layer_aba(terms: List[str], period: str = "WEEK") -> List[Dict[str, Any]]:
    """ABA 搜索词排名（多词 ###### 连接，departmentNames 必须一一对应）"""
    terms = [t for t in terms if t][:MAX_ABA_TERMS]
    if not terms:
        return []
    body = {
        "marketplaceId": DEFAULT_MARKETPLACE,
        "searchTerms": "######".join(terms),
        "periodType": period,
        "startDate": "", "endDate": "",
        "departmentNames": "######".join([SITE_DOMAIN] * len(terms)),
    }
    data = safe_post(ABA_TREND_API, body) or []
    return data if isinstance(data, list) else []


def layer_suggested_bid(target_rows: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    ids: List[str] = []
    for tt in ("auto", "keyword", "targeting"):
        for r in (target_rows.get(tt) or []):
            tid = r.get("targetId")
            if tid and str(tid) not in ids:
                ids.append(str(tid))
    out: List[Dict[str, Any]] = []
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            data = safe_post("spTarget/getSuggestedBid",
                             {"shopId": DEFAULT_SHOP_ID,
                              "targetIds": ",".join(chunk)}) or []
            out.extend(data if isinstance(data, list) else [])
        except Exception:                       # noqa: BLE001
            pass
        time.sleep(0.15)
    return out


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@router.get("/status")
def status() -> Dict[str, Any]:
    ck = load_cookies() or {}
    return {
        "has_cookie": bool(ck),
        "cookie_count": len(ck),
        "cookie_path": config.COOKIE_PATH,
        "cdp_url": config.CDP_URL,
        "default_shop_id": DEFAULT_SHOP_ID,
        "default_marketplace": DEFAULT_MARKETPLACE,
    }


@router.get("/campaigns")
def campaigns(q: str = Query("", description="活动名关键词"),
              start: str = Query(..., description="开始日期 YYYY-MM-DD"),
              end: str = Query(..., description="结束日期 YYYY-MM-DD"),
              limit: int = Query(60, le=300)) -> Dict[str, Any]:
    """活动候选列表（供选择器）"""
    _cookie_header()
    rows = layer_campaign_list(DEFAULT_SHOP_ID, start, end, q)
    out = [{
        "campaignId": r.get("campaignId"),
        "id": r.get("id"),
        "name": r.get("name"),
        "state": r.get("state"),
        "servingStatusName": r.get("servingStatusName"),
        "targetingType": r.get("campaignTargetingType") or r.get("targetingType"),
        "dailyBudget": r.get("dailyBudget"),
        "strategy": r.get("strategy"),
        "portfolioName": r.get("portfolioName"),
        "impressions": r.get("impressions"),
        "clicks": r.get("clicks"),
        "adCost": r.get("adCost"),
        "adOrderNum": r.get("adOrderNum"),
        "adSale": r.get("adSale"),
    } for r in rows[:limit]]
    return {"total": len(rows), "items": out}


class FetchReq(BaseModel):
    campaignId: str = Field(..., description="Amazon campaignId")
    startDate: str
    endDate: str
    hourly: bool = True
    aba: bool = True
    search: bool = True
    negatives: bool = True
    logs: bool = True
    suggestedBid: bool = True


@router.post("/fetch")
def fetch(req: FetchReq) -> Dict[str, Any]:
    """一次拉全该活动所有层级数据"""
    _cookie_header()
    t0 = time.time()
    days = _date_list(req.startDate, req.endDate)
    cid = str(req.campaignId)
    errors: List[Dict[str, str]] = []

    def guard(name: str, fn, default):
        try:
            return fn()
        except Exception as e:                  # noqa: BLE001
            errors.append({"layer": name, "msg": str(e)[:200]})
            return default

    info = guard("campaign_info", lambda: layer_campaign_info(cid), {})
    if not info:
        raise HTTPException(status_code=404,
                            detail=f"未取到活动 {cid} 详情，可能 campaignId 有误或登录态失效")

    range_row = guard("campaign_range",
                      lambda: layer_campaign_range(cid, req.startDate, req.endDate), {})
    daily = guard("campaign_daily", lambda: layer_campaign_daily(cid, days), [])
    groups = guard("groups", lambda: layer_groups(cid, req.startDate, req.endDate), [])
    targets = guard("targets",
                    lambda: layer_targets(cid, req.startDate, req.endDate),
                    {"auto": [], "keyword": [], "targeting": []})
    products = guard("products",
                     lambda: layer_products(cid, req.startDate, req.endDate), [])
    placements = guard("placements",
                       lambda: layer_placements(cid, req.startDate, req.endDate), [])

    search_kw: List[Dict[str, Any]] = []
    search_asin: List[Dict[str, Any]] = []
    if req.search:
        search_kw = guard("search_keyword",
                          lambda: layer_search(cid, req.startDate, req.endDate,
                                               "searchKeyword"), [])
        search_asin = guard("search_asin",
                            lambda: layer_search(cid, req.startDate, req.endDate,
                                                 "searchAsin"), [])

    hourly = guard("hourly", lambda: layer_hourly(cid, days), []) if req.hourly else []

    negatives = (guard("negatives", lambda: layer_negatives(cid),
                       {"keyword": [], "target": []}) if req.negatives
                 else {"keyword": [], "target": []})

    logs = (guard("logs", lambda: layer_logs(cid, req.startDate, req.endDate), [])
            if req.logs else [])

    # ABA：拿花费最高的前 N 个搜索词
    aba_week: List[Dict[str, Any]] = []
    aba_month: List[Dict[str, Any]] = []
    if req.aba and search_kw:
        top = sorted(search_kw, key=lambda r: -float(r.get("adCost") or 0))
        terms = [str(r.get("query")).strip() for r in top if r.get("query")][:MAX_ABA_TERMS]
        aba_week = guard("aba_week", lambda: layer_aba(terms, "WEEK"), [])
        aba_month = guard("aba_month", lambda: layer_aba(terms, "MONTH"), [])

    suggested = (guard("suggested_bid", lambda: layer_suggested_bid(targets), [])
                 if req.suggestedBid else [])

    counts = {
        "groups": len(groups),
        "targets_auto": len(targets.get("auto") or []),
        "targets_keyword": len(targets.get("keyword") or []),
        "targets_targeting": len(targets.get("targeting") or []),
        "products": len(products),
        "placements": len(placements),
        "search_keyword": len(search_kw),
        "search_asin": len(search_asin),
        "aba_week": len(aba_week),
        "aba_month": len(aba_month),
        "negatives_keyword": len(negatives.get("keyword") or []),
        "negatives_target": len(negatives.get("target") or []),
        "logs": len(logs),
        "suggested_bid": len(suggested),
        "days": len(days),
    }

    return {
        "ok": True,
        "meta": {
            "campaignId": cid,
            "name": info.get("campaignName") or info.get("name"),
            "innerId": info.get("id"),
            "shopId": DEFAULT_SHOP_ID,
            "startDate": req.startDate,
            "endDate": req.endDate,
            "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "elapsedSec": round(time.time() - t0, 1),
        },
        "campaign": {"info": info, "range": range_row, "daily": daily},
        "groups": groups,
        "targets": targets,
        "products": products,
        "placements": placements,
        "search": {"keyword": search_kw, "asin": search_asin},
        "aba": {"week": aba_week, "month": aba_month},
        "negatives": negatives,
        "logs": logs,
        "suggestedBid": suggested,
        "hourly": hourly,
        "counts": counts,
        "errors": errors,
    }
