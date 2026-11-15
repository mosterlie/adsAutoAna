# -*- coding: utf-8 -*-
"""统一只读查询层。

Agent / 大模型只通过 resource + filters 取数，不接触赛狐 URL、Cookie 和编码。
所有数据来自实时赛狐接口；data_coverage 额外报告本地库覆盖情况。
"""
from __future__ import annotations

import math
import re
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional

from backend.ads_ops.errors import ValidationFailed
from backend.ads_ops.transport import SellfoxTransport

_PARENT_RE = re.compile(r"(B0[A-Z0-9]{8})")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _parent_asin(*names: Any) -> str:
    for name in names:
        hit = _PARENT_RE.findall(str(name or ""))
        if hit:
            return hit[-1]
    return ""


def _date(value: Optional[str], fallback: date) -> str:
    raw = str(value or "").strip()
    if not raw:
        return fallback.isoformat()
    if not _DATE_RE.match(raw):
        raise ValidationFailed("日期必须是 YYYY-MM-DD", details={"value": raw})
    return raw


def _rows(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = resp.get("data") or {}
    if not isinstance(data, dict):
        return []
    page = data.get("page") if isinstance(data.get("page"), dict) else data
    return list(page.get("rows") or [])


def _page_info(resp: Dict[str, Any]) -> Dict[str, Any]:
    data = resp.get("data") or {}
    if not isinstance(data, dict):
        return {}
    page = data.get("page") if isinstance(data.get("page"), dict) else data
    return page if isinstance(page, dict) else {}


class AdsQueryService:
    """只读广告数据查询；不做任何写请求。"""

    RESOURCES = (
        "campaigns", "groups", "targets", "products", "placements",
        "search_terms", "search_asins", "data_coverage",
        "campaign_analysis",
    )

    def __init__(self, transport: Optional[SellfoxTransport] = None,
                 shop_id: str = "711811",
                 marketplace_id: str = "A1VC38T7YXB528"):
        self.transport = transport or SellfoxTransport()
        self.shop_id = str(shop_id)
        self.marketplace_id = marketplace_id

    # ------------------------------------------------------------------
    # 基础分页
    # ------------------------------------------------------------------
    def _gw(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.transport.request("gw", path, body, encoding="json", retry=True)

    def _paged(self, path: str, body: Dict[str, Any],
               page_size: int = 200,
               key_fields: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        fields = list(key_fields or (
            "campaignId", "adGroupId", "targetId", "id", "asin", "predicate"))
        for page in range(1, 30):
            req = dict(body)
            req.update({"pageNo": page, "pageSize": page_size, "fromSingle": True})
            resp = self._gw(path, req)
            rows = _rows(resp)
            new = 0
            for row in rows:
                key = "|".join(str(row.get(f) or "") for f in fields)
                key = key.strip("|") or repr(sorted(row.items()))
                if key not in seen:
                    seen.add(key)
                    out.append(row)
                    new += 1
            info = _page_info(resp)
            total_page = _int(info.get("totalPage"), 0)
            if not rows or new == 0 or len(rows) < page_size or (
                    total_page and page >= total_page):
                break
        return out

    # ------------------------------------------------------------------
    # 活动
    # ------------------------------------------------------------------
    def _all_campaign_rows(self, date_from: str, date_to: str,
                           status_list: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        return self._paged("campaign/getAllCampaignData", {
            "shopIdList": [self.shop_id], "adType": "sp",
            "statusList": status_list or ["enabled", "paused"],
            "startDate": date_from, "endDate": date_to,
            "orderField": "adCost", "orderType": "desc",
        })

    def _match_campaign(self, row: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        portfolio = str(filters.get("portfolio") or filters.get("portfolio_name") or "")
        if portfolio:
            if portfolio not in (str(row.get("portfolioId") or ""),
                                 str(row.get("portfolioName") or "")):
                return False
        campaign_id = str(filters.get("campaign_id") or "")
        if campaign_id and str(row.get("campaignId") or "") != campaign_id:
            return False
        campaign_name = str(filters.get("campaign") or "")
        if campaign_name and str(row.get("name") or "") != campaign_name:
            return False
        state = str(filters.get("state") or "")
        if state and str(row.get("state") or "") != state:
            return False
        parent = str(filters.get("parent_asin") or "")
        if parent and _parent_asin(row.get("name"), row.get("campaignName")) != parent:
            return False
        return True

    def campaigns(self, date_from: str, date_to: str,
                  filters: Optional[Dict[str, Any]] = None,
                  limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        filters = filters or {}
        rows = [x for x in self._all_campaign_rows(date_from, date_to)
                if self._match_campaign(x, filters)]
        normalized = [self._normalize_campaign(x) for x in rows]
        total = len(normalized)
        normalized = normalized[offset:offset + limit]
        return {"resource": "campaigns", "rows": normalized,
                "count": len(normalized), "total": total,
                "source": "sellfox_live", "warnings": []}

    @staticmethod
    def _normalize_campaign(row: Dict[str, Any]) -> Dict[str, Any]:
        impressions = _num(row.get("impressions"))
        clicks = _num(row.get("clicks"))
        cost = _num(row.get("adCost") or row.get("cost"))
        orders = _num(row.get("adOrderNum") or row.get("orders"))
        sales = _num(row.get("adSales") or row.get("sales"))
        return {
            "campaign_id": str(row.get("campaignId") or ""),
            "campaign_internal_id": str(row.get("id") or ""),
            "name": str(row.get("name") or row.get("campaignName") or ""),
            "parent_asin": _parent_asin(row.get("name"), row.get("campaignName")),
            "state": str(row.get("state") or ""),
            "serving_status": str(row.get("servingStatusName") or ""),
            "portfolio_id": str(row.get("portfolioId") or ""),
            "portfolio_name": str(row.get("portfolioName") or ""),
            "targeting_type": str(row.get("campaignTargetingType")
                                  or row.get("targetingType") or ""),
            "strategy": str(row.get("strategy") or ""),
            "daily_budget": _num(row.get("dailyBudget")),
            "impressions": impressions,
            "clicks": clicks,
            "cost": cost,
            "orders": orders,
            "sales": sales,
            "ctr": clicks / impressions * 100 if impressions else 0.0,
            "cpc": cost / clicks if clicks else 0.0,
            "cvr": orders / clicks * 100 if clicks else 0.0,
            "acos": cost / sales * 100 if sales else None,
            "roas": sales / cost if cost else 0.0,
        }

    # ------------------------------------------------------------------
    # 广告组 / 投放 / 广告产品 / 广告位
    # ------------------------------------------------------------------
    def _campaign_ids(self, date_from: str, date_to: str,
                      filters: Dict[str, Any]) -> List[str]:
        explicit = filters.get("campaign_ids")
        if explicit:
            return [str(x) for x in explicit if str(x)]
        one = str(filters.get("campaign_id") or "")
        if one:
            return [one]
        campaigns = self.campaigns(date_from, date_to, filters, limit=1000)
        return [x["campaign_id"] for x in campaigns["rows"]]

    def groups(self, date_from: str, date_to: str,
               filters: Optional[Dict[str, Any]] = None,
               limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        filters = filters or {}
        ids = self._campaign_ids(date_from, date_to, filters)
        if not ids:
            return {"resource": "groups", "rows": [], "count": 0, "total": 0,
                    "source": "sellfox_live", "warnings": ["没有匹配的活动"]}
        rows = self._paged("multiple/group/getAllGroupData", {
            "shopIdList": [self.shop_id], "campaignIdList": ids,
            "adType": "sp", "startDate": date_from, "endDate": date_to,
        })
        rows = [x for x in rows if not filters.get("campaign_id")
                or str(x.get("campaignId") or "") == str(filters["campaign_id"])]
        rows = [x for x in rows if not filters.get("group")
                or str(x.get("name") or "") == str(filters["group"])]
        out = [{
            "campaign_id": str(x.get("campaignId") or ""),
            "campaign_name": str(x.get("campaignName") or ""),
            "parent_asin": _parent_asin(x.get("campaignName"), x.get("name")),
            "ad_group_id": str(x.get("adGroupId") or ""),
            "group_internal_id": str(x.get("id") or ""),
            "name": str(x.get("name") or ""),
            "state": str(x.get("state") or ""),
            "default_bid": _num(x.get("defaultBid")),
            "ad_product_num": _int(x.get("adProductNum")),
            "impressions": _num(x.get("impressions")),
            "clicks": _num(x.get("clicks")),
            "cost": _num(x.get("adCost")),
            "orders": _num(x.get("adOrderNum")),
            "sales": _num(x.get("adSales")),
        } for x in rows]
        return {"resource": "groups", "rows": out[offset:offset + limit],
                "count": len(out[offset:offset + limit]), "total": len(out),
                "source": "sellfox_live", "warnings": []}

    def targets(self, date_from: str, date_to: str,
                filters: Optional[Dict[str, Any]] = None,
                limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        filters = filters or {}
        ids = self._campaign_ids(date_from, date_to, filters)
        if not ids:
            return {"resource": "targets", "rows": [], "count": 0,
                    "total": 0, "source": "sellfox_live",
                    "warnings": ["没有匹配的活动"]}
        types = [str(filters["target_type"])] if filters.get("target_type") \
            else ["auto", "keyword", "targeting"]
        rows: List[Dict[str, Any]] = []
        for target_type in types:
            batch = self._paged("multiple/target/getAllTargetData", {
                "orderField": "adCost", "orderType": "desc",
                "shopIdList": [self.shop_id], "campaignIdList": ids,
                "adType": "sp", "targetType": target_type, "matchTypes": [],
                "statusList": ["enabled", "paused"], "startDate": date_from,
                "endDate": date_to, "useAdvanced": False,
            })
            for x in batch:
                x = dict(x)
                x["_query_target_type"] = target_type
                rows.append(x)
        rows = [x for x in rows if not filters.get("campaign_id")
                or str(x.get("campaignId") or "") == str(filters["campaign_id"])]
        group = str(filters.get("group") or filters.get("group_id") or "")
        if group:
            rows = [x for x in rows if group in (
                str(x.get("adGroupId") or ""), str(x.get("adGroupName") or ""))]
        out = [{
            "campaign_id": str(x.get("campaignId") or ""),
            "campaign_name": str(x.get("campaignName") or ""),
            "parent_asin": _parent_asin(x.get("campaignName"), x.get("adGroupName")),
            "ad_group_id": str(x.get("adGroupId") or ""),
            "ad_group_name": str(x.get("adGroupName") or ""),
            "target_internal_id": str(x.get("id") or ""),
            "target_id": str(x.get("targetId") or ""),
            "target_type": str(x.get("_query_target_type") or ""),
            "target_text": str(x.get("targetText") or x.get("keywordText") or ""),
            "keyword_text": str(x.get("keywordText") or ""),
            "match_type": str(x.get("matchType") or ""),
            "bid": _num(x.get("bid")),
            "state": str(x.get("state") or ""),
            "impressions": _num(x.get("impressions")),
            "clicks": _num(x.get("clicks")),
            "cost": _num(x.get("adCost")),
            "orders": _num(x.get("adOrderNum")),
            "sales": _num(x.get("adSales")),
        } for x in rows]
        return {"resource": "targets", "rows": out[offset:offset + limit],
                "count": len(out[offset:offset + limit]), "total": len(out),
                "source": "sellfox_live", "warnings": []}

    def products(self, date_from: str, date_to: str,
                 filters: Optional[Dict[str, Any]] = None,
                 limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        filters = filters or {}
        ids = self._campaign_ids(date_from, date_to, filters)
        if not ids:
            return {"resource": "products", "rows": [], "count": 0,
                    "total": 0, "source": "sellfox_live",
                    "warnings": ["没有匹配的活动"]}
        rows = self._paged("multiple/adProduct/getAdProductList", {
            "type": "sp", "campaignIdList": ids, "shopIdList": [self.shop_id],
            "statusList": ["enabled", "paused", "archived"],
            "startDate": date_from, "endDate": date_to,
            "orderBy": "adCost", "desc": True,
            "searchContents": [], "searchMode": "exact", "searchType": "asin",
            "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": False,
        })
        rows = [x for x in rows if not filters.get("campaign_id")
                or str(x.get("campaignId") or "") == str(filters["campaign_id"])]
        group = str(filters.get("group") or filters.get("group_id") or "")
        if group:
            rows = [x for x in rows if group in (
                str(x.get("adGroupId") or ""), str(x.get("adGroupName") or ""))]
        out = [{
            "campaign_id": str(x.get("campaignId") or ""),
            "campaign_name": str(x.get("campaignName") or ""),
            "parent_asin": _parent_asin(x.get("campaignName"), x.get("adGroupName")),
            "ad_group_id": str(x.get("adGroupId") or ""),
            "ad_group_name": str(x.get("adGroupName") or ""),
            "ad_product_internal_id": str(x.get("id") or ""),
            "ad_id": str(x.get("adId") or ""),
            "asin": str(x.get("asin") or ""),
            "sku": str(x.get("sku") or ""),
            "state": str(x.get("state") or ""),
            "price": _num(x.get("price")),
            "impressions": _num(x.get("impressions")),
            "clicks": _num(x.get("clicks")),
            "cost": _num(x.get("adCost")),
            "orders": _num(x.get("adOrderNum")),
            "sales": _num(x.get("adSales")),
        } for x in rows]
        return {"resource": "products", "rows": out[offset:offset + limit],
                "count": len(out[offset:offset + limit]), "total": len(out),
                "source": "sellfox_live", "warnings": []}

    def placements(self, date_from: str, date_to: str,
                   filters: Optional[Dict[str, Any]] = None,
                   limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        filters = filters or {}
        ids = self._campaign_ids(date_from, date_to, filters)
        rows = self._paged("multiple/placement/getAllPlacementData", {
            "dxmCampaignId": "", "campaignSite": "", "orderField": "adCost",
            "orderType": "desc", "adType": "sp", "campaignIdList": ids,
            "devIds": [], "strategyType": "", "predicate": "",
            "startDate": date_from, "endDate": date_to,
            "isCompare": False, "compareStartDate": "", "compareEndDate": "",
            "adStrategyTypeList": [], "fromSingle": True,
        })
        out = [{
            "campaign_id": str(x.get("campaignId") or ""),
            "campaign_name": str(x.get("campaignName") or ""),
            "parent_asin": _parent_asin(x.get("campaignName")),
            "predicate": str(x.get("predicate") or ""),
            "percentage": _num(x.get("percentage")),
            "strategy": str(x.get("strategy") or ""),
            "impressions": _num(x.get("impressions")),
            "clicks": _num(x.get("clicks")),
            "cost": _num(x.get("adCost")),
            "orders": _num(x.get("adOrderNum")),
            "sales": _num(x.get("adSales")),
        } for x in rows]
        return {"resource": "placements", "rows": out[offset:offset + limit],
                "count": len(out[offset:offset + limit]), "total": len(out),
                "source": "sellfox_live", "warnings": []}

    # ------------------------------------------------------------------
    # 搜索词 / 搜索 ASIN
    # ------------------------------------------------------------------
    def search_terms(self, date_from: str, date_to: str,
                     filters: Optional[Dict[str, Any]] = None,
                     limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        return self._search(date_from, date_to, filters, limit, offset,
                            "searchKeyword", "search_terms")

    def search_asins(self, date_from: str, date_to: str,
                     filters: Optional[Dict[str, Any]] = None,
                     limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        return self._search(date_from, date_to, filters, limit, offset,
                            "searchAsin", "search_asins")

    def _search(self, date_from: str, date_to: str,
                filters: Optional[Dict[str, Any]], limit: int, offset: int,
                query_type: str, resource: str) -> Dict[str, Any]:
        filters = filters or {}
        rows = self._paged("multiple/search/getAllSearchData", {
            "orderField": "adCost", "orderType": "desc",
            "shopIdList": [self.shop_id], "adType": "sp",
            "searchQueryType": query_type,
            "statusList": ["enabled", "paused"], "servingStatusList": [],
            "startDate": date_from, "endDate": date_to,
            "useAdvanced": False,
        }, page_size=500,
            key_fields=("campaignId", "adGroupId", "query", "matchType", "queryType"))
        campaign_ids = set(str(x) for x in filters.get("campaign_ids") or [])
        if filters.get("campaign_id"):
            campaign_ids.add(str(filters["campaign_id"]))
        if campaign_ids:
            rows = [x for x in rows
                    if str(x.get("campaignId") or "") in campaign_ids]
        if filters.get("campaign"):
            rows = [x for x in rows
                    if str(x.get("campaignName") or "") == str(filters["campaign"])]
        if filters.get("parent_asin"):
            rows = [x for x in rows
                    if _parent_asin(x.get("campaignName"), x.get("adGroupName"))
                    == str(filters["parent_asin"])]
        if filters.get("group"):
            rows = [x for x in rows if str(filters["group"]) in (
                str(x.get("adGroupId") or ""), str(x.get("adGroupName") or ""))]
        if filters.get("min_clicks") is not None:
            rows = [x for x in rows
                    if _num(x.get("clicks")) >= _num(filters["min_clicks"])]
        out = [self._normalize_search(x, query_type) for x in rows]
        if filters.get("aggregate", True):
            merged: Dict[tuple, Dict[str, Any]] = {}
            for x in out:
                key = (x["campaign_id"], x["ad_group_id"], x["query"], x["query_type"])
                if key not in merged:
                    merged[key] = dict(
                        x, impressions=0.0, clicks=0.0, cost=0.0, orders=0.0,
                        sales=0.0, add_to_cart=0.0, match_types=[], row_count=0)
                row = merged[key]
                row["impressions"] += x["impressions"]
                row["clicks"] += x["clicks"]
                row["cost"] += x["cost"]
                row["orders"] += x["orders"]
                row["sales"] += x["sales"]
                row["add_to_cart"] += x["add_to_cart"]
                row["row_count"] += 1
                if x["match_type"] and x["match_type"] not in row["match_types"]:
                    row["match_types"].append(x["match_type"])
            out = list(merged.values())
        return {"resource": resource, "rows": out[offset:offset + limit],
                "count": len(out[offset:offset + limit]), "total": len(out),
                "source": "sellfox_live", "warnings": []}

    @staticmethod
    def _normalize_search(row: Dict[str, Any], query_type: str) -> Dict[str, Any]:
        return {
            "campaign_id": str(row.get("campaignId") or ""),
            "campaign_name": str(row.get("campaignName") or ""),
            "parent_asin": _parent_asin(row.get("campaignName"), row.get("adGroupName")),
            "ad_group_id": str(row.get("adGroupId") or ""),
            "ad_group_name": str(row.get("adGroupName") or ""),
            "query": str(row.get("query") or ""),
            "query_cn": str(row.get("queryCn") or ""),
            "query_type": str(row.get("queryType") or query_type),
            "match_type": str(row.get("matchType") or ""),
            "targeting_expression": str(row.get("targetingExpression") or ""),
            "impressions": _num(row.get("impressions")),
            "clicks": _num(row.get("clicks")),
            "cost": _num(row.get("adCost")),
            "orders": _num(row.get("adOrderNum")),
            "sales": _num(row.get("adSales")),
            "add_to_cart": _num(row.get("addToCart")),
            "search_frequency_rank": _int(row.get("searchFrequencyRank")),
        }

    # ------------------------------------------------------------------
    # 数据覆盖
    # ------------------------------------------------------------------
    def data_coverage(self, date_from: str, date_to: str,
                      filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        from backend import database as db
        with db.get_conn() as conn:
            campaign_rows = conn.execute(
                "SELECT date, COUNT(*) n FROM fact_campaign "
                "WHERE date BETWEEN ? AND ? AND hour=-1 AND placement='' "
                "GROUP BY date ORDER BY date", (date_from, date_to)).fetchall()
            search_rows = conn.execute(
                "SELECT date, COUNT(*) n FROM fact_search_term "
                "WHERE date BETWEEN ? AND ? GROUP BY date ORDER BY date",
                (date_from, date_to)).fetchall()
            term_campaigns = conn.execute(
                "SELECT COUNT(DISTINCT campaign_id) FROM fact_search_term "
                "WHERE date BETWEEN ? AND ?", (date_from, date_to)).fetchone()
        warnings = []
        if not campaign_rows:
            warnings.append("本地活动数据为空")
        if not search_rows:
            warnings.append("本地搜索词数据为空")
        if term_campaigns and int(term_campaigns[0] or 0) < 10:
            warnings.append(
                f"本地搜索词仅覆盖 {int(term_campaigns[0] or 0)} 个活动，不能代表全店")
        return {
            "resource": "data_coverage",
            "date_from": date_from, "date_to": date_to,
            "campaign_days": [{"date": r[0], "rows": r[1]} for r in campaign_rows],
            "search_days": [{"date": r[0], "rows": r[1]} for r in search_rows],
            "search_campaign_count": int(term_campaigns[0] or 0) if term_campaigns else 0,
            "warnings": warnings,
            "source": "local_db",
        }

    def query(self, resource: str, date_from: Optional[str] = None,
              date_to: Optional[str] = None, filters: Optional[Dict[str, Any]] = None,
              limit: int = 200, offset: int = 0) -> Dict[str, Any]:
        if resource not in self.RESOURCES:
            raise ValidationFailed("不支持的查询资源",
                                   details={"resource": resource,
                                            "available": list(self.RESOURCES)})
        today = date.today()
        # 数据覆盖检查：未显式指定日期时，返回本地库真实覆盖区间，
        # 而不是机械地取“最近 7 天”（采集库可能停在更早日期）。
        if resource == "data_coverage" and not date_from and not date_to:
            from backend import database as db
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT MIN(date) min_d, MAX(date) max_d FROM fact_campaign "
                    "WHERE hour=-1 AND placement=''").fetchone()
            if row and row["min_d"] and row["max_d"]:
                frm, to = row["min_d"], row["max_d"]
            else:
                frm = _date(None, today - timedelta(days=6))
                to = _date(None, today)
        else:
            frm = _date(date_from, today - timedelta(days=6))
            to = _date(date_to, today)
        if frm > to:
            raise ValidationFailed("date_from 不能晚于 date_to")
        limit = max(1, min(int(limit or 200), 1000))
        offset = max(0, int(offset or 0))
        if resource == "campaign_analysis":
            cid = str((filters or {}).get("campaign_id") or "").strip()
            if not cid:
                raise ValidationFailed("campaign_analysis 需要 filters.campaign_id")
            from backend.ads_ops.campaign_analysis import CampaignAnalysisService
            return CampaignAnalysisService().analyze(
                cid, frm, to,
                _num((filters or {}).get("target_acos"), 35.0))
        if resource == "campaigns":
            return self.campaigns(frm, to, filters, limit, offset)
        if resource == "groups":
            return self.groups(frm, to, filters, limit, offset)
        if resource == "targets":
            return self.targets(frm, to, filters, limit, offset)
        if resource == "products":
            return self.products(frm, to, filters, limit, offset)
        if resource == "placements":
            return self.placements(frm, to, filters, limit, offset)
        if resource == "search_terms":
            return self.search_terms(frm, to, filters, limit, offset)
        if resource == "search_asins":
            return self.search_asins(frm, to, filters, limit, offset)
        return self.data_coverage(frm, to, filters)
