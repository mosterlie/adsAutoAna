# -*- coding: utf-8 -*-
"""按天全量取数（Daily Probe）

输入：广告活动 + 时间区间
输出：**每一天**的

    活动级   24 小时 / 4 个广告位 × 24 小时 / 各投放 × 24 小时
    广告组级 24 小时 / 4 个广告位 × 24 小时 / 各投放 × 24 小时
    广告产品 三体系（HOURLY / HOURLY_PLACEMENT / HOURLY_KEYWORD）
    搜索词   活动级 + 组级（日级，按 adGroupId 本地分组）

外加（整区间一次）：
    产品基础信息（在线产品接口）、产品建议竞价、搜索词的亚马逊竞品页

接口：
    POST /api/daily/start          启动任务 → {taskId}
    GET  /api/daily/task/{tid}     进度 / 状态 / 错误
    GET  /api/daily/result/{tid}   全量结果（可分日期/层级过滤）
    GET  /api/daily/tasks          最近任务

设计要点：
    * 后台线程执行，前端轮询进度（一次全量约 1-3 分钟）
    * **每天一个 JSON 落盘**，中断可续，不会整批重来
    * 每个层级独立 try/except，错误汇总到 errors，不中断整体
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import config
from backend.campaign_probe_api import (
    DEFAULT_MARKETPLACE, DEFAULT_SHOP_ID, GW, PRODUCT_API, SITE_DOMAIN,
    _cookie_header, _rows, safe_post, safe_post_form,
)

router = APIRouter(prefix="/api/daily", tags=["daily"])

OUT_DIR = os.path.join(config.DATA_DIR, "probe_daily")
PLACEMENTS = [
    ("placementTop", "搜索结果顶部"),
    ("placementRestOfSearch", "搜索结果其余位置"),
    ("placementProductPage", "商品详情页"),
    ("offAmazon", "Off Amazon"),
]
BASE_SLEEP = 0.15

TASKS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
def _hour_payload(day: str, **over: Any) -> Dict[str, Any]:
    p = {
        "orderField": None, "orderType": None,
        "shopIdList": [int(DEFAULT_SHOP_ID)], "shopId": DEFAULT_SHOP_ID,
        "adType": "sp", "type": "sp",
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
    p.update(over)
    return p


def _to_num(v: Any) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:                       # noqa: BLE001
        return 0.0


def _hours(rep: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """periodReportList(24 行) → 精简的小时数组（保留全部指标）"""
    out = []
    for r in rep or []:
        title = str(r.get("title") or "")
        out.append({"hour": title, "metrics": r})
    return out


def _detail24(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """HOURLY_PLACEMENT / HOURLY_KEYWORD 行内的 detail[24]"""
    det = row.get("detail") or []
    return [{"hour": f"{i}-{i+1}", "metrics": m} for i, m in enumerate(det)]


def _sum_metrics(rows: List[Dict[str, Any]]) -> Dict[str, float]:
    keys = ("impressions", "clicks", "adCost", "adOrderNum", "adSale",
            "addToCart", "detailPageViews")
    acc = {k: 0.0 for k in keys}
    for r in rows or []:
        m = r.get("metrics") if isinstance(r.get("metrics"), dict) else r
        for k in keys:
            acc[k] += _to_num(m.get(k)) if isinstance(m, dict) else 0.0
    return {k: round(v, 2) for k, v in acc.items()}


# ---------------------------------------------------------------------------
# 基础清单（每天都要，但轻量）
# ---------------------------------------------------------------------------
def list_groups(cid: str, day: str) -> List[Dict[str, Any]]:
    data = safe_post("multiple/group/getAllGroupData", {
        "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [cid],
        "adType": "sp", "startDate": day, "endDate": day})
    return _rows(data)


def list_products(cid: str, day: str) -> List[Dict[str, Any]]:
    data = safe_post("multiple/adProduct/getAdProductList", {
        "type": "sp", "campaignIdList": [cid], "shopIdList": [DEFAULT_SHOP_ID],
        "statusList": ["enabled", "paused"], "startDate": day, "endDate": day,
        "pageNo": 1, "pageSize": 500, "orderBy": "adCost", "desc": True,
        "searchContents": [], "searchMode": "exact", "searchType": "asin",
        "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": False})
    return _rows(data)


def list_targets(cid: str, day: str, gid: str = "") -> List[Dict[str, Any]]:
    """投放清单

    注意：`groupIdList` 传给 getAllTargetData 会让结果变空（实测），
    所以一律按活动取全量，再按行内 `adGroupId`（Amazon 组 id）本地过滤。
    参数 gid 传的是 **Amazon adGroupId**。
    """
    out: List[Dict[str, Any]] = []
    for tt in ("auto", "keyword", "targeting"):
        try:
            body = {
                "orderField": "adCost", "orderType": "desc",
                "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [cid],
                "adType": "sp", "targetType": tt, "matchTypes": [],
                "statusList": ["enabled", "paused"],
                "startDate": day, "endDate": day,
                "pageNo": 1, "pageSize": 500, "fromSingle": True,
                "useAdvanced": False}
            rows = _rows(safe_post("multiple/target/getAllTargetData", body))
            for r in rows:
                r["_targetType"] = tt
            out.extend(rows)
        except Exception:                   # noqa: BLE001
            continue
        time.sleep(BASE_SLEEP)
    if gid:
        out = [r for r in out if str(r.get("adGroupId") or "") == str(gid)]
    return out


# ---------------------------------------------------------------------------
# 单日采集
# ---------------------------------------------------------------------------
def collect_campaign_hour(cid: str, day: str, errs: List[Dict[str, str]]) -> Dict[str, Any]:
    """活动级 24 小时

    注意：这个接口 **只认 `campaignId`（单值字符串）**，
    传 `campaignIdList` 会退化成全店数据（实测 3681 vs 实际 390）。
    """
    try:
        d = safe_post("campaign/getAllCampaignHourData",
                      _hour_payload(day, campaignId=cid)) or {}
        rep = d.get("periodReportList") or []
        return {"hours": _hours(rep), "aggregate": d.get("aggregateDataVo") or {},
                "sum": _sum_metrics(_hours(rep))}
    except Exception as e:                  # noqa: BLE001
        errs.append({"layer": f"campaign_hour@{day}", "msg": str(e)[:150]})
        return {"hours": [], "sum": {}}


def collect_placements(cid: str, day: str, gid: str,
                       errs: List[Dict[str, str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for code, label in PLACEMENTS:
        try:
            body = _hour_payload(day, campaignIdList=[cid], predicate=code)
            if gid:
                body["groupIdList"] = [gid]
            d = safe_post("multiple/placement/getAllPlacementHourData", body) or {}
            rep = d.get("periodReportList") or []
            out[code] = {"label": label, "hours": _hours(rep),
                         "sum": _sum_metrics(_hours(rep))}
        except Exception as e:              # noqa: BLE001
            errs.append({"layer": f"placement[{code}]@{day}", "msg": str(e)[:120]})
            out[code] = {"label": label, "hours": [], "sum": {}}
        time.sleep(BASE_SLEEP)
    return out


def collect_targets_hour(cid: str, day: str, gid: str, targets: List[Dict[str, Any]],
                         errs: List[Dict[str, str]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for t in targets:
        tid = str(t.get("targetId") or "")
        if not tid:
            continue
        label = t.get("targetText") or t.get("targetName") or tid
        try:
            body = _hour_payload(day, campaignIdList=[cid], targetId=tid,
                                 targetType=t.get("_targetType") or "auto",
                                 matchTypes=[], productTargetTypes=[])
            if gid:
                body["groupIdList"] = [gid]
            d = safe_post("multiple/target/getAllTargetHourData", body) or {}
            rep = d.get("periodReportList") or []
            out[tid] = {"label": label, "type": t.get("_targetType"), "hours": _hours(rep),
                        "sum": _sum_metrics(_hours(rep))}
        except Exception as e:              # noqa: BLE001
            errs.append({"layer": f"target[{tid}]@{day}", "msg": str(e)[:120]})
        time.sleep(BASE_SLEEP)
    return out


def collect_products(cid: str, day: str, products: List[Dict[str, Any]],
                     errs: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """产品三体系：HOURLY / HOURLY_PLACEMENT(4) / HOURLY_KEYWORD(N)"""
    out: List[Dict[str, Any]] = []
    for p in products:
        ad_id = str(p.get("adId") or p.get("id") or "")
        if not ad_id:
            continue
        item = {"asin": p.get("asin"), "sku": p.get("sku"), "adId": ad_id,
                "total": [], "placements": [], "match": [], "sum": {}}
        for dm, key in (("HOURLY", "total"),
                        ("HOURLY_PLACEMENT", "placements"),
                        ("HOURLY_KEYWORD", "match")):
            try:
                d = safe_post("multiple/adProduct/getAllProductHourData", {
                    "type": "sp", "adStrategyTypeList": [],
                    "startDate": day, "endDate": day,
                    "isCompare": False, "useAdvanced": False,
                    "pageNo": 1, "pageSize": 200, "advanceFilter": {},
                    "campaignIdList": [cid], "groupIdList": [],
                    "searchContents": [], "searchMode": "exact", "searchType": "asin",
                    "servingStatusList": [], "shopIdList": [DEFAULT_SHOP_ID],
                    "statusList": ["enabled", "paused"],
                    "analyzeStartDate": day, "analyzeEndDate": day,
                    "dateModel": dm, "weekList": ["1", "2", "3", "4", "5", "6", "7"],
                    "isHide": True, "adIds": [ad_id], "fromSingle": True}) or {}
                rep = d.get("periodReportList") or []
                if key == "total":
                    item["total"] = _hours(rep)
                    item["sum"] = _sum_metrics(item["total"])
                else:
                    rows = []
                    for r in rep:
                        code = r.get("predicate") or r.get("title") or "-"
                        label = dict(PLACEMENTS).get(code, r.get("title") or code)
                        rows.append({"code": code, "label": label,
                                     "hours": _detail24(r),
                                     "sum": _sum_metrics(_detail24(r))})
                    item[key] = rows
            except Exception as e:          # noqa: BLE001
                errs.append({"layer": f"product[{ad_id}].{dm}@{day}", "msg": str(e)[:120]})
            time.sleep(BASE_SLEEP)
        out.append(item)
    return out


def collect_search(cid: str, day: str, errs: List[Dict[str, str]]) -> Dict[str, Any]:
    """搜索词 / 搜索 ASIN（日级），按 adGroupId 本地分组"""
    result: Dict[str, Any] = {"keyword": [], "asin": [], "byGroup": {}}
    for qtype, key in (("searchKeyword", "keyword"), ("searchAsin", "asin")):
        try:
            rows = _rows(safe_post("multiple/search/getAllSearchData", {
                "orderField": "adCost", "orderType": "desc",
                "shopIdList": [DEFAULT_SHOP_ID], "campaignIdList": [cid],
                "adType": "sp", "searchQueryType": qtype,
                "statusList": ["enabled", "paused"], "servingStatusList": [],
                "startDate": day, "endDate": day,
                "pageNo": 1, "pageSize": 500, "fromSingle": True,
                "useAdvanced": False}))
            result[key] = rows
            for r in rows:
                gid = str(r.get("adGroupId") or "")
                if not gid:
                    continue
                bucket = result["byGroup"].setdefault(gid, {"keyword": [], "asin": []})
                bucket[key].append(r)
        except Exception as e:              # noqa: BLE001
            errs.append({"layer": f"search_{key}@{day}", "msg": str(e)[:120]})
        time.sleep(BASE_SLEEP)
    return result


def collect_one_day(cid: str, day: str, opts: Dict[str, bool],
                    progress_cb=None) -> Dict[str, Any]:
    errs: List[Dict[str, str]] = []
    out: Dict[str, Any] = {"date": day, "errors": errs}

    out["campaign"] = collect_campaign_hour(cid, day, errs)
    if progress_cb:
        progress_cb("campaign_hour")

    if opts.get("placement", True):
        out["campaign"]["placements"] = collect_placements(cid, day, "", errs)

    targets = list_targets(cid, day)
    if opts.get("target", True):
        out["campaign"]["targets"] = collect_targets_hour(cid, day, "", targets, errs)

    groups = list_groups(cid, day)
    out["groups"] = []
    for g in groups:
        gid = str(g.get("id") or "")
        gid_amz = str(g.get("adGroupId") or "")
        gitem: Dict[str, Any] = {"id": gid, "adGroupId": gid_amz,
                                 "name": g.get("name"),
                                 "defaultBid": g.get("defaultBid")}
        try:
            body = _hour_payload(day, campaignIdList=[cid], groupIdList=[gid])
            d = safe_post("multiple/group/getAllGroupHourData", body) or {}
            rep = d.get("periodReportList") or []
            gitem["hours"] = _hours(rep)
            gitem["sum"] = _sum_metrics(gitem["hours"])
        except Exception as e:              # noqa: BLE001
            errs.append({"layer": f"group_hour[{gid}]@{day}", "msg": str(e)[:120]})
            gitem["hours"], gitem["sum"] = [], {}
        if opts.get("placement", True):
            gitem["placements"] = collect_placements(cid, day, gid, errs)
        if opts.get("target", True):
            gtargets = list_targets(cid, day, gid_amz)
            gitem["targets"] = collect_targets_hour(cid, day, gid, gtargets, errs)
        out["groups"].append(gitem)
    if progress_cb:
        progress_cb("groups")

    if opts.get("product", True):
        products = list_products(cid, day)
        out["products"] = collect_products(cid, day, products, errs)
    if progress_cb:
        progress_cb("products")

    if opts.get("search", True):
        out["search"] = collect_search(cid, day, errs)
    if progress_cb:
        progress_cb("search")

    return out


# ---------------------------------------------------------------------------
# 区间级：产品基础信息 / 建议竞价 / 竞品抓取
# ---------------------------------------------------------------------------
def product_basic_info(asins: List[str]) -> Dict[str, Any]:
    """在线产品接口（form-urlencoded）拿售价/标题/状态"""
    base = {
        "currency": "", "pageType": "child", "asinType": "asin",
        "shopIds": DEFAULT_SHOP_ID, "marketplaceId": "", "labelIds": "",
        "labelQuery": "0", "onlineStatus": "active,inActive",
        "switchFulfillmentTo": "", "isVariation": "", "match": "",
        "lowCostStore": "", "searchField": "asin", "searchValue": "",
        "searchMode": "exact", "pageSize": "200", "pageNo": "1", "fullCid": "",
        "organiser": "", "startTime": "", "endTime": "", "buyBoxWinners": "",
        "defaultFilterType": "1", "productBundle": "", "useAdvanced": "1",
        "devIds": "", "advancedSearchItem": "", "defaultPageType": "1",
        "complianceStatus": "",
    }
    out: Dict[str, Any] = {}
    for asin in asins:
        try:
            body = dict(base, searchValue=asin)
            data = safe_post_form(PRODUCT_API, body) or {}
            # 该接口返回的是 data.rows（不是 data.page.rows），两种都兼容
            rows = _rows(data)
            hit = next((r for r in rows if r.get("asin") == asin), None)
            if hit:
                out[asin] = {k: hit.get(k) for k in
                             ("asin", "sku", "title", "standardPrice", "landedPrice",
                              "dxmState", "onlineStatus", "parentAsin", "isVariation",
                              "rating", "ratingCount", "quantity", "fulfillmentChannel",
                              "openDate", "amazonBrand", "complianceReason")}
        except Exception:                   # noqa: BLE001
            pass
        time.sleep(BASE_SLEEP)
    # 接口没拿到时，回落到库里已同步的在线产品快照
    missing = [a for a in asins if a not in out]
    if missing:
        out.update(_product_info_from_db(missing))
    return out


def _product_info_from_db(asins: List[str]) -> Dict[str, Any]:
    """从 online_products 表兜底（raw_json 里有完整价格/评分/库存）"""
    import json as _json
    import sqlite3
    res: Dict[str, Any] = {}
    try:
        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        for asin in asins:
            r = conn.execute("SELECT raw_json FROM online_products WHERE asin=? LIMIT 1",
                             (asin,)).fetchone()
            if not r:
                continue
            try:
                raw = _json.loads(r["raw_json"])
            except Exception:               # noqa: BLE001
                continue
            res[asin] = {k: raw.get(k) for k in
                         ("asin", "sku", "title", "standardPrice", "landedPrice",
                          "dxmState", "onlineStatus", "parentAsin", "isVariation",
                          "rating", "ratingCount", "quantity", "fulfillmentChannel",
                          "openDate", "amazonBrand")}
            res[asin]["_source"] = "db"
        conn.close()
    except Exception:                       # noqa: BLE001
        pass
    return res


def suggested_bids(cid: str, day: str, targets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ids = [str(t.get("targetId")) for t in targets if t.get("targetId")]
    out: List[Dict[str, Any]] = []
    for i in range(0, len(ids), 50):
        try:
            data = safe_post("spTarget/getSuggestedBid", {
                "shopId": DEFAULT_SHOP_ID, "targetIds": ",".join(ids[i:i + 50])}) or []
            if isinstance(data, list):
                out.extend(data)
        except Exception:                   # noqa: BLE001
            pass
        time.sleep(BASE_SLEEP)
    return out


def competitor_pages(terms: List[str], limit: int = 8) -> Dict[str, Any]:
    """调亚马逊搜索抓取（带缓存），失败不影响主流程"""
    out: Dict[str, Any] = {}
    try:
        from backend import amazon
    except Exception:                       # noqa: BLE001
        return out
    for term in terms[:limit]:
        try:
            r = amazon.fetch(term, domain="co.jp", scrolls=1)
            items = (r or {}).get("items") or []
            # 保存搜索结果页的**全部**记录（不是只取前 12 条）
            out[term] = [{
                "position": x.get("position"), "asin": x.get("asin"),
                "title": (x.get("title") or "")[:120],
                "price_value": x.get("price_value"),
                "list_price": x.get("list_price"),
                "rating_value": x.get("rating_value"),
                "review_count": x.get("review_count"),
                "delivery_date": x.get("delivery_date"),
                "free_shipping": x.get("free_shipping"),
                "sponsored": x.get("sponsored"),
            } for x in items]
        except Exception:                   # noqa: BLE001
            out[term] = []
        time.sleep(1.0)
    return out


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
class StartReq(BaseModel):
    campaignId: str
    campaignName: str = ""
    startDate: str
    endDate: str
    placement: bool = True
    target: bool = True
    product: bool = True
    search: bool = True
    productInfo: bool = True
    suggestedBid: bool = True
    competitor: bool = False
    competitorTerms: int = 8


def _date_list(start: str, end: str) -> List[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        raise ValueError("结束日期不能早于开始日期")
    return [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def _run_task(tid: str, req: StartReq) -> None:
    task = TASKS[tid]
    try:
        cid = req.campaignId
        days = _date_list(req.startDate, req.endDate)
        opts = {"placement": req.placement, "target": req.target,
                "product": req.product, "search": req.search}

        task["status"] = "running"
        task["totalDays"] = len(days)
        day_dir = os.path.join(OUT_DIR, tid)
        os.makedirs(day_dir, exist_ok=True)

        # 区间级：产品信息 / 建议竞价（只做一次）
        ctx: Dict[str, Any] = {}
        if req.productInfo or req.suggestedBid:
            first = days[0]
            try:
                prods = list_products(cid, first)
                asins = [p.get("asin") for p in prods if p.get("asin")]
                if req.productInfo:
                    task["current"] = "产品基础信息"
                    ctx["productInfo"] = product_basic_info(asins)
                if req.suggestedBid:
                    task["current"] = "建议竞价"
                    targets = list_targets(cid, first)
                    ctx["suggestedBid"] = suggested_bids(cid, first, targets)
            except Exception as e:          # noqa: BLE001
                task["errors"].append({"layer": "context", "msg": str(e)[:150]})

        day_summaries = []
        for i, day in enumerate(days):
            task["current"] = f"{day} 采集中"
            data = collect_one_day(cid, day, opts)
            path = os.path.join(day_dir, f"day_{day}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            # 同步写入新结构（实体中心化，UPSERT 幂等）
            try:
                from backend import ads_store
                ads_store.write_day(data, cid)
            except Exception as e:          # noqa: BLE001
                task["errors"].append({"layer": f"store_write@{day}", "msg": str(e)[:150]})
            task["errors"].extend(data.get("errors") or [])
            day_summaries.append({
                "date": day,
                "campaignSum": (data.get("campaign") or {}).get("sum") or {},
                "products": len(data.get("products") or []),
                "groups": len(data.get("groups") or []),
                "searchKeyword": len(((data.get("search") or {}).get("keyword") or [])),
                "errors": len(data.get("errors") or []),
            })
            task["doneDays"] = i + 1
            task["progress"] = round((i + 1) / len(days) * 100)
            time.sleep(0.2)

        # 竞品抓取（区间一次，取花费最高的词）
        if req.competitor:
            task["current"] = "竞品页抓取"
            terms: List[str] = []
            seen = set()
            for day in days:
                p = os.path.join(day_dir, f"day_{day}.json")
                try:
                    d = json.load(open(p, encoding="utf-8"))
                except Exception:           # noqa: BLE001
                    continue
                rows = ((d.get("search") or {}).get("keyword") or [])
                rows.sort(key=lambda r: -_to_num(r.get("adCost")))
                for r in rows:
                    q = str(r.get("query") or "").strip()
                    if q and q not in seen:
                        seen.add(q)
                        terms.append(q)
                    if len(terms) >= req.competitorTerms:
                        break
                if len(terms) >= req.competitorTerms:
                    break
            ctx["competitor"] = competitor_pages(terms, req.competitorTerms)

        meta = {
            "taskId": tid, "campaignId": cid, "campaignName": req.campaignName,
            "startDate": req.startDate, "endDate": req.endDate,
            "days": days, "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "options": req.model_dump(),
        }
        with open(os.path.join(day_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"meta": meta, "context": ctx, "summaries": day_summaries},
                      f, ensure_ascii=False)
        # 竞品顺带落库（按词一次，全量）
        if req.competitor and ctx.get("competitor"):
            try:
                from backend import ads_store, daily_db
                ads_store.init()
                c2 = ads_store.get_conn()
                try:
                    ads_store.write_competitor(c2, ctx["competitor"])
                    c2.commit()
                finally:
                    c2.close()
                daily_db.persist_competitor(
                    tid, cid, ctx["competitor"],
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception:               # noqa: BLE001
                pass
        task["meta"] = meta
        task["context"] = ctx
        task["summaries"] = day_summaries
        task["status"] = "done"
        task["progress"] = 100
        task["current"] = "完成"
        task["finishedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:                  # noqa: BLE001
        task["status"] = "error"
        task["errors"].append({"layer": "task", "msg": str(e)[:200]})
        task["current"] = "失败"


@router.post("/start")
def start(req: StartReq) -> Dict[str, Any]:
    _cookie_header()
    try:
        days = _date_list(req.startDate, req.endDate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if len(days) > 31:
        raise HTTPException(status_code=400, detail="一次最多 31 天")
    tid = uuid.uuid4().hex[:12]
    TASKS[tid] = {
        "taskId": tid, "status": "pending", "progress": 0,
        "doneDays": 0, "totalDays": len(days), "current": "排队中",
        "errors": [], "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "campaignId": req.campaignId, "campaignName": req.campaignName,
        "startDate": req.startDate, "endDate": req.endDate,
    }
    threading.Thread(target=_run_task, args=(tid, req), daemon=True).start()
    return {"taskId": tid, "totalDays": len(days)}


@router.get("/task/{tid}")
def task(tid: str) -> Dict[str, Any]:
    t = TASKS.get(tid)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    return t


@router.get("/tasks")
def tasks() -> Dict[str, Any]:
    items = sorted(TASKS.values(), key=lambda x: x.get("createdAt", ""), reverse=True)
    return {"items": items[:20]}


@router.get("/result/{tid}")
def result(tid: str, date: str = Query("", description="只看某一天"),
           level: str = Query("", description="campaign/groups/products/search")) -> Dict[str, Any]:
    day_dir = os.path.join(OUT_DIR, tid)
    if not os.path.isdir(day_dir):
        raise HTTPException(status_code=404, detail="结果不存在")
    if date:
        p = os.path.join(day_dir, f"day_{date}.json")
        if not os.path.exists(p):
            raise HTTPException(status_code=404, detail=f"{date} 无数据")
        d = json.load(open(p, encoding="utf-8"))
        if level and level in d:
            return {"date": date, level: d[level]}
        return d
    # 无 date：返回 meta + 每天摘要
    meta_p = os.path.join(day_dir, "meta.json")
    meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else {}
    days = []
    for fn in sorted(os.listdir(day_dir)):
        if fn.startswith("day_") and fn.endswith(".json"):
            days.append(fn[4:-5])
    return {"meta": meta.get("meta") or {}, "context": meta.get("context") or {},
            "summaries": meta.get("summaries") or [], "days": days}


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------
from backend import daily_db  # noqa: E402


@router.post("/db/persist/{tid}")
def db_persist(tid: str) -> Dict[str, Any]:
    """把某个任务的 day_*.json 全部写入 SQLite（幂等）"""
    try:
        return daily_db.persist_task(tid)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/db/runs")
def db_runs() -> Dict[str, Any]:
    return {"items": daily_db.list_runs()}


@router.get("/db/dates")
def db_dates(task: str = Query(..., alias="task")) -> Dict[str, Any]:
    return {"dates": daily_db.dates_of(task)}


@router.get("/db/overview")
def db_overview(task: str = Query(..., alias="task"),
                date: str = Query(...)) -> Dict[str, Any]:
    return daily_db.overview(task, date)


@router.get("/db/trend")
def db_trend(task: str = Query(..., alias="task"), level: str = "campaign",
             entityId: str = "", dimType: str = "total",
             dimCode: str = "") -> Dict[str, Any]:
    return {"items": daily_db.daily_trend(task, level, entityId, dimType, dimCode)}


@router.get("/db/hourly-avg")
def db_hourly_avg(task: str = Query(..., alias="task"), level: str = "campaign",
                  entityId: str = "", dimType: str = "total",
                  dimCode: str = "") -> Dict[str, Any]:
    return {"items": daily_db.hourly_avg(task, level, entityId, dimType, dimCode)}


@router.get("/context/{tid}")
def context(tid: str) -> Dict[str, Any]:
    """读取任务的区间级上下文（产品信息 / 建议竞价 / 竞品页）"""
    mp = os.path.join(OUT_DIR, tid, "meta.json")
    if not os.path.exists(mp):
        raise HTTPException(status_code=404, detail="任务不存在")
    d = json.load(open(mp, encoding="utf-8"))
    return {"taskId": tid, "context": d.get("context") or {},
            "meta": d.get("meta") or {}}


# ---------------------------------------------------------------------------
# 竞品：按词一次、全量记录
# ---------------------------------------------------------------------------
EXPLORE_DIR = os.path.join(config.BASE_DIR, "explore")


def _explore_index() -> Dict[str, List[Dict[str, Any]]]:
    """把 explore/amazon_search_*.json 索引成 {搜索词: 全量 items}"""
    import glob
    idx: Dict[str, List[Dict[str, Any]]] = {}
    for f in glob.glob(os.path.join(EXPLORE_DIR, "amazon_search_*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:                   # noqa: BLE001
            continue
        q = str(d.get("query") or "").strip()
        items = d.get("items") or []
        if q and items:
            idx[q] = items
    return idx


def collect_competitor(terms: List[str], refresh: bool = False) -> Dict[str, List[Dict[str, Any]]]:
    """按词取竞品**全量**记录

    * 优先用 explore/ 本地缓存的完整结果（不是只取 12 条）
    * refresh=True 且本地缺失时才真去亚马逊抓（有 6s 限流）
    """
    idx = _explore_index()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for t in terms:
        items = idx.get(t)
        if not items and refresh:
            try:
                from backend import amazon
                r = amazon.fetch(t, domain="co.jp", scrolls=1) or {}
                items = r.get("items") or []
            except Exception:               # noqa: BLE001
                items = []
        if items:
            out[t] = items
    return out


def task_terms(tid: str) -> List[str]:
    """该任务涉及的所有搜索词（去重，按花费降序）"""
    import sqlite3
    conn = sqlite3.connect(config.DB_PATH)
    try:
        rs = conn.execute(
            "SELECT term, SUM(cost) s FROM probe_search"
            " WHERE task_id=? AND query_type='keyword' GROUP BY term ORDER BY s DESC",
            (tid,)).fetchall()
        return [r[0] for r in rs if r[0]]
    except Exception:                       # noqa: BLE001
        return []
    finally:
        conn.close()


@router.post("/db/competitor/{tid}")
def db_competitor_persist(tid: str, refresh: bool = False,
                          terms: str = "") -> Dict[str, Any]:
    """把该任务所有搜索词的竞品**全量**落库（每个词只存一份，不按天重复）"""
    tlist = [x for x in terms.split("||") if x] if terms else task_terms(tid)
    if not tlist:
        raise HTTPException(status_code=404, detail="该任务没有搜索词记录")
    comp = collect_competitor(tlist, refresh=refresh)
    if not comp:
        return {"taskId": tid, "terms": 0, "rows": 0,
                "missing": tlist, "msg": "本地无缓存且未开启刷新"}
    from backend import daily_db
    conn_meta = os.path.join(OUT_DIR, tid, "meta.json")
    cid = ""
    if os.path.exists(conn_meta):
        cid = ((json.load(open(conn_meta, encoding="utf-8")).get("meta") or {})
               .get("campaignId") or "")
    stats = daily_db.persist_competitor(tid, cid, comp, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    stats["missing"] = [t for t in tlist if t not in comp]
    return stats


@router.get("/db/competitor/terms")
def db_competitor_terms(task: str = Query(..., alias="task")) -> Dict[str, Any]:
    from backend import daily_db
    return {"items": daily_db.competitor_terms(task)}


@router.get("/db/competitor/items")
def db_competitor_items(task: str = Query(..., alias="task"),
                        term: str = Query(...)) -> Dict[str, Any]:
    from backend import daily_db
    return {"term": term, "items": daily_db.competitor_items(task, term)}


# ---------------------------------------------------------------------------
# 新结构查询（活动 + 日期区间，不依赖 task_id）
# ---------------------------------------------------------------------------
@router.get("/store/campaigns")
def store_campaigns() -> Dict[str, Any]:
    from backend import ads_store
    return {"items": ads_store.list_campaigns()}


@router.get("/store/dates")
def store_dates(campaignId: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return {"dates": ads_store.dates_of(campaignId)}


@router.get("/store/day")
def store_day(campaignId: str = Query(...), date: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return ads_store.day_overview(campaignId, date)


@router.get("/store/trend")
def store_trend(campaignId: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return {"items": ads_store.trend(campaignId)}


@router.get("/store/hourly")
def store_hourly(campaignId: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return {"items": ads_store.hourly_avg(campaignId)}


@router.get("/store/competitor/terms")
def store_comp_terms(campaignId: str = Query(...), start: str = "",
                     end: str = "") -> Dict[str, Any]:
    from backend import ads_store
    return {"items": ads_store.competitor_terms(campaignId, start, end)}


@router.get("/store/competitor/items")
def store_comp_items(term: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return {"term": term, "items": ads_store.competitor_items(term)}


@router.post("/store/import")
def store_import() -> Dict[str, Any]:
    """把 data/probe_daily 下所有 day_*.json 回填到新表（幂等）"""
    from backend import ads_store
    return ads_store.import_from_dirs()


@router.get("/store/products")
def store_products(campaignId: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return {"items": ads_store.products_of(campaignId)}


@router.get("/store/range")
def store_range(campaignId: str = Query(...), start: str = Query(...),
                end: str = Query(...)) -> Dict[str, Any]:
    """区间汇总：只返回 [start, end] 内的数据"""
    from backend import ads_store
    if end < start:
        raise HTTPException(status_code=400, detail="结束日期不能早于开始日期")
    return ads_store.range_overview(campaignId, start, end)


@router.get("/store/parents")
def store_parents(campaignId: str = Query(...)) -> Dict[str, Any]:
    """该活动的父体信息（图片/标题/子体数/价格/商品标识）"""
    from backend import ads_store
    return {"items": ads_store.parents_of(campaignId)}
