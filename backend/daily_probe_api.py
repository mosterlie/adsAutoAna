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
import random
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
            out[tid] = {"label": label, "type": t.get("_targetType"),
                        "match_type": t.get("matchType") or "",
                        "hours": _hours(rep),
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
    # 当日无点击：只保留活动汇总，跳过后续所有明细请求。
    campaign_sum = out["campaign"].get("sum") or {}
    if opts.get("skipNoClicks") and _to_num(campaign_sum.get("clicks")) <= 0:
        out["skippedNoClicks"] = True
        return out

    if opts.get("placement", True):
        out["campaign"]["placements"] = collect_placements(cid, day, "", errs)

    if opts.get("target", True):
        targets = list_targets(cid, day)
        out["campaign"]["targets"] = collect_targets_hour(cid, day, "", targets, errs)

    groups = list_groups(cid, day) if opts.get("group", True) else []
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


def competitor_pages(terms: List[str], limit: int = 8, on_log=None,
                     refresh: bool = False) -> Dict[str, Any]:
    """调亚马逊搜索抓取（带缓存）；撞人机校验立即停止，不继续消耗会话。"""
    out: Dict[str, Any] = {}
    try:
        from backend import amazon
    except Exception:                       # noqa: BLE001
        return out
    if amazon.blocked_remaining() > 0:
        if on_log:
            on_log("warn", "Amazon 抓取冷却中",
                   f"剩余 {int(amazon.blocked_remaining())} 秒，跳过竞品抓取")
        return out
    failures = 0
    total = min(len(terms), limit)
    for idx, term in enumerate(terms[:limit], 1):
        if on_log:
            on_log("info", f"竞品词 {idx}/{min(len(terms), limit)}：{term}", "开始抓取搜索结果页")
        try:
            r = amazon.fetch(term, domain="co.jp", scrolls=1,
                             refresh=refresh, use_cache=not refresh)
            items = (r or {}).get("items") or []
            if (r or {}).get("challenge_clicked") and on_log:
                on_log("info", f"已自动点击确认框：{term}",
                       "简单确认页已尝试，复检搜索结果")
            if (r or {}).get("blocked"):
                out[term] = []
                failures += 1
                if on_log:
                    on_log("warn", f"竞品词触发人机校验：{term}",
                           "已停止本批竞品抓取，进入全局冷却")
                break
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
            failures = 0
            if on_log:
                on_log("success", f"竞品词完成：{term}", f"{len(out[term])} 条结果")
        except Exception as exc:             # noqa: BLE001
            out[term] = []
            failures += 1
            if on_log:
                on_log("warn", f"竞品词失败：{term}", str(exc)[:200])
            if failures >= config.AMAZON_COMPETITOR_MAX_FAIL:
                if on_log:
                    on_log("error", "竞品抓取连续失败，提前停止",
                           f"连续 {failures} 次")
                break
        if idx < total:
            gap = random.uniform(config.AMAZON_COMPETITOR_GAP_MIN_SEC,
                                 config.AMAZON_COMPETITOR_GAP_MAX_SEC)
            if on_log:
                on_log("info", f"等待 {gap:.1f} 秒",
                       "降低连续访问搜索页的频率")
            time.sleep(gap)
            if idx % config.AMAZON_COMPETITOR_BATCH_SIZE == 0:
                if on_log:
                    on_log("info", "竞品批次休息",
                           f"{config.AMAZON_COMPETITOR_REST_SEC} 秒")
                time.sleep(config.AMAZON_COMPETITOR_REST_SEC)
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
    # 精简采集关闭广告组/投放/产品，只保留活动分时和搜索词
    group: bool = True
    # 精简采集可关闭完整维度补齐，避免每个活动额外请求组/投放/产品
    syncDims: bool = True
    # 默认跳过本地已有活动日汇总的日期；取消勾选可强制重采
    skipExisting: bool = True
    # 当日活动点击为 0 时，只保留活动汇总，不再采集组/投放/产品/搜索词明细
    skipNoClicks: bool = False


def _date_list(start: str, end: str) -> List[str]:
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    if e < s:
        raise ValueError("结束日期不能早于开始日期")
    return [(s + timedelta(days=i)).isoformat() for i in range((e - s).days + 1)]


def _existing_dates(campaign_id: str, days: List[str]) -> set:
    """返回本地已有活动日汇总的日期。失败时按“没有”处理，宁可重采。"""
    if not days:
        return set()
    try:
        from backend import ads_store
        conn = ads_store.get_conn()
        try:
            ads_store.init(conn)
            placeholders = ",".join("?" for _ in days)
            rows = conn.execute(
                f"SELECT DISTINCT date FROM fact_campaign "
                f"WHERE campaign_id=? AND date IN ({placeholders}) "
                f"AND hour=-1 AND placement=''",
                (str(campaign_id), *days)).fetchall()
            return {str(r["date"] if hasattr(r, "keys") else r[0]) for r in rows}
        finally:
            conn.close()
    except Exception:                       # noqa: BLE001
        return set()


def _remaining_days(campaign_id: str, days: List[str],
                    skip_existing: bool) -> tuple[List[str], List[str]]:
    if not skip_existing:
        return list(days), []
    existing = _existing_dates(campaign_id, days)
    skipped = [d for d in days if d in existing]
    return [d for d in days if d not in existing], skipped


def _task_log(task: Dict[str, Any], level: str, message: str,
              detail: str = "") -> None:
    logs = task.setdefault("logs", [])
    logs.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "level": level,
        "message": message,
        "detail": detail,
    })
    if len(logs) > 500:
        task["logs"] = logs[-500:]


def _day_summary_text(data: Dict[str, Any]) -> str:
    sm = (data.get("campaign") or {}).get("sum") or {}
    return (f"点击 {_to_num(sm.get('clicks')):g} · "
            f"花费 {_to_num(sm.get('adCost')):.2f} · "
            f"订单 {_to_num(sm.get('adOrderNum')):g} · "
            f"组 {len(data.get('groups') or [])} · "
            f"产品 {len(data.get('products') or [])} · "
            f"搜索词 {len(((data.get('search') or {}).get('keyword') or []))}")


def _run_task(tid: str, req: StartReq) -> None:
    task = TASKS[tid]
    try:
        cid = req.campaignId
        requested_days = _date_list(req.startDate, req.endDate)
        days, skipped_days = _remaining_days(cid, requested_days,
                                              req.skipExisting)
        opts = {"placement": req.placement, "target": req.target,
                "product": req.product, "search": req.search,
                "group": req.group, "skipNoClicks": req.skipNoClicks}

        task["status"] = "running"
        task["totalDays"] = len(days)
        task["skippedDays"] = len(skipped_days)
        task["skippedDates"] = skipped_days
        day_dir = os.path.join(OUT_DIR, tid)
        os.makedirs(day_dir, exist_ok=True)
        _task_log(task, "info", "开始采集",
                  f"活动 {cid}，请求区间 {req.startDate} ~ {req.endDate}")
        if skipped_days:
            _task_log(task, "warn", f"跳过已采日期 {len(skipped_days)} 天",
                      "、".join(skipped_days))

        if not days:
            task["status"] = "done"
            task["progress"] = 100
            task["doneDays"] = 0
            task["current"] = f"全部日期已存在，已跳过 {len(skipped_days)} 天"
            task["finishedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _task_log(task, "success", "任务完成", task["current"])
            return

        # 区间级：产品信息 / 建议竞价（只做一次）
        # 使用请求区间的最后一天，而不是剩余日期中的第一天；
        # 这样跳采前面的日期时，上下文仍取区间最新状态。
        ctx: Dict[str, Any] = {}
        context_day = req.endDate
        if req.productInfo or req.suggestedBid:
            _task_log(task, "info", f"读取区间上下文（{context_day}）",
                      "产品基础信息和建议竞价只采一次")
            try:
                prods = list_products(cid, context_day)
                asins = [p.get("asin") for p in prods if p.get("asin")]
                if req.productInfo:
                    task["current"] = f"产品基础信息（{context_day}）"
                    ctx["productInfo"] = product_basic_info(asins)
                    _task_log(task, "success", "产品基础信息读取完成",
                              f"{len(ctx['productInfo'] or {})} 条")
                if req.suggestedBid:
                    task["current"] = f"建议竞价（{context_day}）"
                    targets = list_targets(cid, context_day)
                    ctx["suggestedBid"] = suggested_bids(cid, context_day, targets)
                    _task_log(task, "success", "建议竞价读取完成",
                              f"{len(ctx['suggestedBid'] or {})} 条")
            except Exception as e:          # noqa: BLE001
                task["errors"].append({"layer": "context", "msg": str(e)[:150]})
                _task_log(task, "error", "区间上下文读取失败", str(e)[:200])

        # 维度同步：dim_campaign 的 daily_budget / status 是当前状态，不是历史事实。
        # 普通采集每次刷新一次；精简采集可关闭，避免 100+ 活动逐个补全维度太慢。
        if req.syncDims:
            try:
                from backend import ads_store
                dim_stat = ads_store.enrich_dims(campaign_id=cid, day=context_day)
                _task_log(task, "success", "活动维度已同步",
                          f"活动 {dim_stat.get('campaign', 0)} · 组 {dim_stat.get('group', 0)}"
                          f" · 投放 {dim_stat.get('target', 0)} · 产品 {dim_stat.get('product', 0)}")
            except Exception as e:          # noqa: BLE001
                task["errors"].append({"layer": "dim_sync", "msg": str(e)[:150]})
                _task_log(task, "warn", "活动维度同步失败", str(e)[:200])

        day_summaries = []
        for i, day in enumerate(days):
            task["current"] = f"{day} 采集中"
            _task_log(task, "info", f"开始采集 {day}",
                      f"进度 {i + 1}/{len(days)}")
            data = collect_one_day(cid, day, opts)
            path = os.path.join(day_dir, f"day_{day}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            # 同步写入新结构（实体中心化，UPSERT 幂等）
            try:
                from backend import ads_store
                ads_store.write_day(data, cid)
                _task_log(task, "success", f"{day} 已写入本地库", _day_summary_text(data))
            except Exception as e:          # noqa: BLE001
                task["errors"].append({"layer": f"store_write@{day}", "msg": str(e)[:150]})
                _task_log(task, "error", f"{day} 写入本地库失败", str(e)[:200])
            data_errors = data.get("errors") or []
            if data.get("skippedNoClicks"):
                task["skippedNoClickDays"] = int(task.get("skippedNoClickDays") or 0) + 1
                _task_log(task, "warn", f"{day} 当日无点击",
                          "已跳过组/投放/产品/搜索词明细")
            task["errors"].extend(data_errors)
            for err in data_errors[:3]:
                _task_log(task, "warn", f"{day} 子接口异常",
                          str(err.get("msg") if isinstance(err, dict) else err)[:200])
            day_summaries.append({
                "date": day,
                "campaignSum": (data.get("campaign") or {}).get("sum") or {},
                "products": len(data.get("products") or []),
                "groups": len(data.get("groups") or []),
                "searchKeyword": len(((data.get("search") or {}).get("keyword") or [])),
                "errors": len(data_errors),
            })
            task["doneDays"] = i + 1
            task["progress"] = round((i + 1) / len(days) * 100)
            time.sleep(0.2)

        # 精简采集后按需补维度：只对新活动/新组触发，完整活动不重复请求。
        if req.search and days:
            try:
                from backend import ads_store
                repair = ads_store.repair_search_dims(cid, days[-1])
                if repair.get("repaired"):
                    stat = repair.get("stat") or {}
                    _task_log(task, "success", "已自动补齐搜索数据维度",
                              f"活动 {stat.get('campaign', 0)} · 组 {stat.get('group', 0)}"
                              f" · 产品 {stat.get('product', 0)}")
            except Exception as e:          # noqa: BLE001
                _task_log(task, "warn", "搜索数据维度自动补齐失败", str(e)[:200])

        # 竞品抓取（区间一次）：按整个查询区间的广告花费全局排序，只取前 8。
        if req.competitor:
            task["current"] = "竞品页抓取"
            _task_log(task, "info", "开始竞品页抓取",
                      "按查询区间广告花费排序，最多前 8 个搜索词")
            terms: List[str] = []
            try:
                from backend import ads_store
                conn = ads_store.get_conn()
                try:
                    ads_store.init(conn)
                    ranked = conn.execute(
                        "SELECT term, SUM(cost) cost FROM fact_search_term "
                        "WHERE campaign_id=? AND date BETWEEN ? AND ? "
                        "AND scope='campaign' AND query_type='keyword' "
                        "GROUP BY term HAVING SUM(cost)>0 "
                        "ORDER BY SUM(cost) DESC, term LIMIT ?",
                        (cid, req.startDate, req.endDate,
                         min(max(1, req.competitorTerms), 8))).fetchall()
                    terms = [str(r["term"]) for r in ranked if r["term"]]
                finally:
                    conn.close()
            except Exception as exc:            # noqa: BLE001
                _task_log(task, "warn", "竞品词排名读取失败", str(exc)[:200])
            if not terms:
                # 兜底：从本次剩余 day JSON 里取花费最高的唯一词。
                seen = set()
                for day in days:
                    p = os.path.join(day_dir, f"day_{day}.json")
                    try:
                        d = json.load(open(p, encoding="utf-8"))
                    except Exception:       # noqa: BLE001
                        continue
                    rows = ((d.get("search") or {}).get("keyword") or [])
                    rows.sort(key=lambda r: -_to_num(r.get("adCost")))
                    for r in rows:
                        q = str(r.get("query") or "").strip()
                        if q and q not in seen:
                            seen.add(q)
                            terms.append(q)
                        if len(terms) >= min(max(1, req.competitorTerms), 8):
                            break
                    if len(terms) >= min(max(1, req.competitorTerms), 8):
                        break
            _task_log(task, "info", f"竞品词前 {len(terms)}",
                      "、".join(terms) if terms else "无")
            ctx["competitor"] = competitor_pages(
                terms, len(terms),
                on_log=lambda level, msg, detail="": _task_log(
                    task, level, msg, detail))
            _task_log(task, "success", "竞品页抓取结束",
                      f"尝试 {len(terms)} 个，成功 {sum(1 for v in ctx['competitor'].values() if v)} 个，"
                      f"结果 {sum(len(v or []) for v in ctx['competitor'].values())} 条")

        meta = {
            "taskId": tid, "campaignId": cid, "campaignName": req.campaignName,
            "startDate": req.startDate, "endDate": req.endDate,
            "days": days, "skippedDays": skipped_days,
            "contextDay": context_day,
            "fetchedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
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
        _task_log(task, "success", "任务完成",
                  f"采集 {task.get('doneDays') or 0} 天，跳过 {task.get('skippedDays') or 0} 天")
    except Exception as e:                  # noqa: BLE001
        task["status"] = "error"
        task["errors"].append({"layer": "task", "msg": str(e)[:200]})
        task["current"] = "失败"
        _task_log(task, "error", "任务失败", str(e)[:300])


LATEST_STATE: Dict[str, Any] = {
    "running": False, "stage": "", "day": "", "done": 0, "total": 0,
    "runId": 0, "current": "", "error": "", "progress": [],
    "startedAt": "", "finishedAt": "",
}
_LATEST_LOCK = threading.Lock()


def _latest_ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS latest_refresh_runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          started_at TEXT, finished_at TEXT, day TEXT,
          status TEXT, stage TEXT,
          online_count INTEGER DEFAULT 0,
          ad_data_status TEXT DEFAULT '',
          active_total INTEGER DEFAULT 0,
          search_done INTEGER DEFAULT 0,
          search_skipped INTEGER DEFAULT 0,
          errors_json TEXT
        )
    """)
    conn.commit()


def _latest_db_update(run_id: int, **fields: Any) -> None:
    if not run_id:
        return
    from backend import ads_store
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _latest_ensure_table(conn)
        allowed = ("finished_at", "day", "status", "stage", "online_count",
                   "ad_data_status", "active_total", "search_done",
                   "search_skipped", "errors_json")
        pairs = [(k, fields[k]) for k in allowed if k in fields]
        if pairs:
            sql = "UPDATE latest_refresh_runs SET " + ", ".join(f"{k}=?" for k, _ in pairs) + " WHERE id=?"
            conn.execute(sql, [v for _, v in pairs] + [run_id])
            conn.commit()
    finally:
        conn.close()


def _latest_log(level: str, message: str, detail: str = "") -> None:
    with _LATEST_LOCK:
        LATEST_STATE["progress"].append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level, "message": message, "detail": detail,
        })
        if len(LATEST_STATE["progress"]) > 500:
            LATEST_STATE["progress"] = LATEST_STATE["progress"][-300:]
        LATEST_STATE["current"] = message


def _run_latest_refresh(run_id: int = 0) -> None:
    """在线产品 + 昨日全量广告数据 + 昨日在投活动搜索词/ASIN。"""
    day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    LATEST_STATE.update({
        "running": True, "stage": "online", "day": day, "done": 0, "total": 0,
        "runId": run_id, "current": "", "error": "", "progress": [],
        "startedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "finishedAt": "",
    })
    errors: List[str] = []
    online_count = 0
    ad_data_status = ""
    active_total = 0
    search_done = 0
    search_skipped = 0
    try:
        # 1) 在线产品：保持原「采集最新」行为。
        _latest_log("info", "开始采集在线产品", "父体 + 子体全量")
        try:
            from backend.api import crawl_online_products
            online_count = crawl_online_products()
            _latest_log("success", "在线产品采集完成", f"{online_count} 条")
            _latest_db_update(run_id, online_count=online_count, stage="online")
        except Exception as e:          # noqa: BLE001
            errors.append(f"在线产品: {str(e)[:150]}")
            _latest_log("error", "在线产品采集失败", str(e)[:200])
            _latest_db_update(run_id, stage="online",
                              errors_json=json.dumps(errors, ensure_ascii=False))

        # 2) 昨日全量广告数据：复用原「同步数据」爬虫，只跑上一日。
        LATEST_STATE["stage"] = "ad_data"
        _latest_db_update(run_id, stage="ad_data")
        from backend import crawler, database as db
        if db.has_successful_run(day, day):
            ad_data_status = "skipped"
            _latest_log("info", f"{day} 全量广告数据已存在，跳过")
        else:
            _latest_log("info", f"开始同步 {day} 全量广告数据")
            try:
                crawler.start_crawl_async(start_date=day, end_date=day,
                                          keep_history=True)
                while crawler.get_state().get("running"):
                    time.sleep(2)
                st = crawler.get_state()
                if st.get("error"):
                    ad_data_status = "failed"
                    errors.append(f"全量广告数据: {st['error'][:150]}")
                    _latest_log("error", "全量广告数据失败", st["error"][:200])
                else:
                    ad_data_status = "success"
                    _latest_log("success", f"{day} 全量广告数据完成")
            except Exception as e:      # noqa: BLE001
                ad_data_status = "failed"
                errors.append(f"全量广告数据: {str(e)[:150]}")
                _latest_log("error", "全量广告数据启动失败", str(e)[:200])
        _latest_db_update(run_id, ad_data_status=ad_data_status,
                          errors_json=json.dumps(errors, ensure_ascii=False))

        # 3) 昨日精简采集：只处理 enabled 活动，已有当天数据则跳过。
        LATEST_STATE["stage"] = "search"
        from backend import ads_store
        from backend.ads_ops.campaign_groups import CampaignGroupsService
        campaigns = CampaignGroupsService().load().get("campaigns") or []
        active = [c for c in campaigns
                  if str(c.get("state") or c.get("status") or "").lower() == "enabled"]
        active_total = len(active)
        LATEST_STATE["total"] = active_total
        _latest_db_update(run_id, active_total=active_total, stage="search")
        _latest_log("info", f"{day} 在投活动 {active_total} 个，开始精简采集")
        opts = {"placement": False, "target": False, "product": False,
                "search": True, "group": False, "skipNoClicks": True}
        for i, c in enumerate(active, 1):
            cid = str(c.get("campaign_id") or "")
            name = c.get("campaign_name") or c.get("name") or cid
            LATEST_STATE["done"] = i
            if not cid:
                continue
            if ads_store.campaign_has_day(cid, day):
                search_skipped += 1
                _latest_log("info", f"{name} 已有 {day} 数据，跳过")
                _latest_db_update(run_id, search_skipped=search_skipped)
                continue
            try:
                data = collect_one_day(cid, day, opts)
                ads_store.write_day(data, cid)
                ads_store.repair_search_dims(cid, day)
                search_done += 1
                _latest_log("success", f"{name} 精简采集完成",
                            _day_summary_text(data))
                _latest_db_update(run_id, search_done=search_done)
            except Exception as e:      # noqa: BLE001
                errors.append(f"{name}: {str(e)[:120]}")
                _latest_log("error", f"{name} 精简采集失败", str(e)[:200])
                _latest_db_update(run_id, errors_json=json.dumps(errors, ensure_ascii=False))
    except Exception as e:              # noqa: BLE001
        errors.append(str(e)[:200])
        _latest_log("error", "最新采集失败", str(e)[:300])
    finally:
        final_status = "done" if not errors else ("partial" if (search_done or online_count) else "failed")
        LATEST_STATE.update({
            "running": False, "stage": "done",
            "error": "；".join(errors)[:500],
            "finishedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })
        _latest_db_update(run_id, finished_at=LATEST_STATE["finishedAt"],
                          status=final_status, stage="done",
                          online_count=online_count,
                          ad_data_status=ad_data_status,
                          active_total=active_total,
                          search_done=search_done,
                          search_skipped=search_skipped,
                          errors_json=json.dumps(errors, ensure_ascii=False))
        _latest_log("success" if not errors else "warn", "最新采集结束",
                    f"{day}，错误 {len(errors)} 个")


@router.post("/latest/start")
def latest_start() -> Dict[str, Any]:
    with _LATEST_LOCK:
        if LATEST_STATE.get("running"):
            raise HTTPException(status_code=409, detail="最新采集正在运行")
        from backend import ads_store
        day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = ads_store.get_conn()
        try:
            ads_store.init(conn)
            _latest_ensure_table(conn)
            cur = conn.execute(
                "INSERT INTO latest_refresh_runs(started_at,day,status,stage) "
                "VALUES(?,?,?,?)", (started, day, "running", "pending"))
            run_id = int(cur.lastrowid)
            conn.commit()
        finally:
            conn.close()
        LATEST_STATE.update({"running": True, "stage": "pending", "error": "",
                             "progress": [], "done": 0, "total": 0,
                             "runId": run_id, "day": day,
                             "startedAt": started, "finishedAt": ""})
    thread = threading.Thread(target=_run_latest_refresh, args=(run_id,), daemon=True)
    thread.start()
    time.sleep(0.1)
    return {"ok": True, "state": dict(LATEST_STATE)}


@router.get("/latest/status")
def latest_status() -> Dict[str, Any]:
    return dict(LATEST_STATE)


@router.get("/latest/history")
def latest_history(limit: int = 50) -> Dict[str, Any]:
    from backend import ads_store
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _latest_ensure_table(conn)
        rows = conn.execute(
            "SELECT * FROM latest_refresh_runs ORDER BY id DESC LIMIT ?",
            (max(1, min(int(limit), 200)),)).fetchall()
        return {"items": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.post("/start")
def start(req: StartReq) -> Dict[str, Any]:
    _cookie_header()
    try:
        days = _date_list(req.startDate, req.endDate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if len(days) > 31:
        raise HTTPException(status_code=400, detail="一次最多 31 天")
    days, skipped_days = _remaining_days(req.campaignId, days,
                                          req.skipExisting)
    tid = uuid.uuid4().hex[:12]
    TASKS[tid] = {
        "taskId": tid, "status": "pending", "progress": 0,
        "doneDays": 0, "totalDays": len(days),
        "skippedDays": len(skipped_days), "skippedDates": skipped_days,
        "current": "排队中", "logs": [],
        "errors": [], "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "campaignId": req.campaignId, "campaignName": req.campaignName,
        "startDate": req.startDate, "endDate": req.endDate,
    }
    _task_log(TASKS[tid], "info", "任务已创建",
              f"活动 {req.campaignName or req.campaignId}，"
              f"{req.startDate} ~ {req.endDate}，待采 {len(days)} 天，"
              f"跳过 {len(skipped_days)} 天")
    threading.Thread(target=_run_task, args=(tid, req), daemon=True).start()
    return {"taskId": tid, "totalDays": len(days),
            "skippedDays": len(skipped_days), "skippedDates": skipped_days}


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


@router.get("/store/search-terms")
def store_search_terms(campaignId: str = Query(...), start: str = Query(...),
                       end: str = Query(...)) -> Dict[str, Any]:
    from backend import ads_store
    return {"items": ads_store.search_term_overview(campaignId, start, end)}


@router.get("/store/search-terms/all")
def store_search_terms_all(start: str = Query(...), end: str = Query(...),
                           limit: int = 2000,
                           kind: str = "keyword") -> Dict[str, Any]:
    from backend import ads_store
    qtype = "asin" if kind == "asin" else "keyword"
    return {"items": ads_store.search_term_overview_all(start, end, limit, qtype)}


@router.post("/store/search-terms/refresh")
def store_search_term_refresh(campaignId: str = Query(...),
                              term: str = Query(...),
                              kind: str = "keyword",
                              start: str = Query(...),
                              end: str = Query(...)) -> Dict[str, Any]:
    """单行修复：补活动/组/产品维度与产品映射，再返回最新行。

    这是行级元数据刷新，不重采当天指标；指标仍以已入库事实为准。
    """
    from backend import ads_store
    warnings = []
    try:
        ads_store.enrich_dims(campaign_id=campaignId, day=end)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"维度刷新失败：{str(e)[:120]}")
    try:
        prods = list_products(campaignId, end) or []
        conn = ads_store.get_conn()
        try:
            for p in prods:
                asin = str(p.get("asin") or "")
                if not asin:
                    continue
                ads_store.upsert_campaign_product(
                    conn, campaignId, asin,
                    sku=p.get("sku") or "",
                    parent_asin=p.get("parentAsin") or p.get("parent_asin") or "")
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        warnings.append(f"产品刷新失败：{str(e)[:120]}")
    qtype = "asin" if kind == "asin" else "keyword"
    rows = ads_store.search_term_overview_all(start, end, limit=5000, query_type=qtype)
    matched = [r for r in rows
               if str(r.get("campaign_id")) == str(campaignId)
               and str(r.get("term")) == str(term)]
    return {"ok": True, "rows": matched, "warnings": warnings}


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


class SearchOpsTaskRequest(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)


class SearchOpStatusRequest(BaseModel):
    campaign_id: str
    group_id: str = ""
    term: str
    kind: str = "keyword"
    status: str = "已完成"


@router.get("/search-ops/statuses")
def list_search_op_statuses(kind: str = "") -> Dict[str, Any]:
    from backend import search_ops
    return {"items": search_ops.list_statuses(kind)}


@router.post("/search-ops/statuses")
def set_search_op_status(req: SearchOpStatusRequest) -> Dict[str, Any]:
    from backend import search_ops
    return search_ops.set_status(req.campaign_id, req.group_id, req.term,
                                 req.kind, req.status)


@router.delete("/search-ops/statuses")
def delete_search_op_status(campaign_id: str = Query(...),
                            group_id: str = "",
                            term: str = Query(...),
                            kind: str = "keyword") -> Dict[str, Any]:
    from backend import search_ops
    return search_ops.set_status(campaign_id, group_id, term, kind, "")


@router.post("/search-ops/tasks")
def create_search_ops_task(req: SearchOpsTaskRequest) -> Dict[str, Any]:
    from backend import search_ops
    return search_ops.create_task(req.items)


@router.post("/search-ops/auto-campaigns")
def create_auto_campaign_search_ops() -> Dict[str, Any]:
    from backend import search_ops
    try:
        return search_ops.create_auto_campaign_task()
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e)[:300])


@router.get("/search-ops/tasks")
def list_search_ops_tasks(limit: int = 50) -> Dict[str, Any]:
    from backend import search_ops
    return {"items": search_ops.list_tasks(limit)}


@router.get("/search-ops/tasks/{task_id}")
def get_search_ops_task(task_id: str) -> Dict[str, Any]:
    from backend import search_ops
    task = search_ops.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="指令任务不存在")
    return {"task": task}


@router.post("/search-ops/tasks/{task_id}/confirm")
def confirm_search_ops_task(task_id: str) -> Dict[str, Any]:
    from backend import search_ops
    task = search_ops.confirm_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="指令任务不存在")
    return {"task": task}


@router.delete("/search-ops/tasks/{task_id}")
def delete_search_ops_task(task_id: str) -> Dict[str, Any]:
    from backend import search_ops
    result = search_ops.delete_task(task_id)
    if result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="指令任务不存在")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message") or "任务不能删除")
    return result


@router.post("/search-ops/tasks/{task_id}/items/{item_id}/retry")
def retry_search_ops_item(task_id: str, item_id: str) -> Dict[str, Any]:
    from backend import search_ops
    result = search_ops.retry_item(task_id, item_id)
    if result.get("reason") == "not_found":
        raise HTTPException(status_code=404, detail="指令任务不存在")
    if result.get("reason") == "item_not_found":
        raise HTTPException(status_code=404, detail="指令不存在")
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message") or "重试失败")
    return result
