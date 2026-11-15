# -*- coding: utf-8 -*-
"""业务对象解析层：名称/ASIN → 可执行的赛狐 ID。

关键约束
--------
* 写接口使用赛狐内部 id；Amazon id 只用于读回校验。
* 不猜测“最像”的对象；0 个 = NotFound，多个 = AmbiguousSelector。
* 本地库只做商品/组合索引；活动和广告组以实时接口为准。
"""
from __future__ import annotations

import json
import time
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Tuple

import config
from backend import database as db
from backend.ads_ops.errors import AmbiguousSelector, NotFound, ValidationFailed
from backend.ads_ops.transport import SellfoxTransport


class Resolver:
    def __init__(self, transport: SellfoxTransport, shop_id: str = "711811",
                 marketplace_id: str = "A1VC38T7YXB528"):
        self.transport = transport
        self.shop_id = str(shop_id)
        self.marketplace_id = marketplace_id
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # 活动
    # ------------------------------------------------------------------
    def _campaign_body(self, page: int, status: str) -> Dict[str, Any]:
        archived = status == "archived"
        return {
            "shopIdList": [self.shop_id], "adType": "sp",
            "status": status,
            "statusList": ["archived"] if archived else ["enabled", "paused"],
            "startDate": "2020-01-01",
            "endDate": date.today().isoformat(),
            "orderField": "adCost", "orderType": "desc",
            "pageNo": page, "pageSize": 200, "fromSingle": True,
            "searchField": "name", "searchType": "contains", "searchValue": "",
            "advanceFilter": {},
        }

    def list_campaigns(self, status: str = "", refresh: bool = False) -> List[Dict[str, Any]]:
        cache_key = status or "all"
        hit = self._cache.get(cache_key)
        if hit and not refresh and time.time() - hit[0] < 10:
            return list(hit[1])
        rows: List[Dict[str, Any]] = []
        seen = set()
        for page in range(1, 21):
            body = self._campaign_body(page, status)
            resp = self.transport.request("gw", "campaign/getAllCampaignData",
                                          body, encoding="json", retry=True)
            node = ((resp.get("data") or {}).get("page")
                    if isinstance(resp.get("data"), dict) else None) or {}
            batch = node.get("rows") or []
            new = 0
            for row in batch:
                cid = str(row.get("campaignId") or "")
                key = cid or id(row)
                if key not in seen:
                    seen.add(key)
                    rows.append(row)
                    new += 1
            total_page = int(node.get("totalPage") or 0)
            if not batch or len(batch) < 200 or (total_page and page >= total_page):
                break
            if page > 1 and new == 0:
                break
        self._cache[cache_key] = (time.time(), rows)
        return list(rows)

    @staticmethod
    def _campaign_summary(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "campaign_id": str(row.get("campaignId") or ""),
            "campaign_internal_id": str(row.get("id") or ""),
            "name": str(row.get("name") or row.get("campaignName") or ""),
            "state": str(row.get("state") or ""),
            "serving_status": str(row.get("servingStatusName") or row.get("servingStatus") or ""),
            "targeting_type": str(row.get("campaignTargetingType")
                                  or row.get("targetingType") or ""),
            "daily_budget": row.get("dailyBudget"),
            "portfolio_id": str(row.get("portfolioId") or ""),
            "portfolio_name": str(row.get("portfolioName") or ""),
            "raw": row,
        }

    def find_campaign(self, ref: Optional[Dict[str, Any]],
                      *, include_archived: bool = True) -> Dict[str, Any]:
        ref = ref or {}
        name = str(ref.get("campaign") or ref.get("campaign_name") or "").strip()
        amazon_id = str(ref.get("campaign_id") or ref.get("amazon_campaign_id") or "").strip()
        internal_id = str(ref.get("campaign_internal_id") or ref.get("internal_id") or "").strip()
        provided = [x for x in (name, amazon_id, internal_id) if x]
        if len(provided) != 1:
            raise ValidationFailed(
                "selector 需要且只能提供 campaign / campaign_id / campaign_internal_id 之一",
                details={"selector": ref})

        rows = self.list_campaigns("")
        if include_archived:
            rows += self.list_campaigns("archived")

        matched = []
        seen = set()
        for row in rows:
            cid = str(row.get("campaignId") or "")
            if not cid or cid in seen:
                continue
            if name and str(row.get("name") or row.get("campaignName") or "").strip() != name:
                continue
            if amazon_id and cid != amazon_id:
                continue
            if internal_id and str(row.get("id") or "") != internal_id:
                continue
            seen.add(cid)
            matched.append(self._campaign_summary(row))

        if not matched:
            if include_archived:
                info = self._campaign_info(amazon_id or "")
                if info:
                    matched = [info]
            if not matched:
                raise NotFound("未找到匹配的广告活动", details={"selector": ref})
        if len(matched) > 1:
            raise AmbiguousSelector(
                "匹配到多个广告活动，请补充 campaign_id 或内部 id",
                details={"selector": ref,
                         "candidates": [{k: x[k] for k in
                                         ("campaign_id", "campaign_internal_id", "name", "state")}
                                        for x in matched[:20]]})
        return matched[0]

    def _campaign_info(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        """归档后列表可能不回显，兜底读活动信息。"""
        if not campaign_id:
            return None
        try:
            resp = self.transport.request("gw", "adCampaign/getInfo", {
                "shopId": self.shop_id, "campaignId": str(campaign_id), "type": "sp",
            }, encoding="json", retry=True)
        except Exception:  # noqa: BLE001
            return None
        data = resp.get("data") or {}
        if not isinstance(data, dict) or not data:
            return None
        data.setdefault("campaignId", campaign_id)
        data.setdefault("name", data.get("campaignName") or data.get("name") or "")
        return self._campaign_summary(data)

    def campaign_name_exists(self, name: str) -> Optional[Dict[str, Any]]:
        target = (name or "").strip()
        if not target:
            return None
        rows = self.list_campaigns("") + self.list_campaigns("archived")
        seen = set()
        for row in rows:
            cid = str(row.get("campaignId") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            if str(row.get("name") or row.get("campaignName") or "").strip() == target:
                return self._campaign_summary(row)
        return None

    def campaign_config(self, campaign_id: str) -> Dict[str, Any]:
        """活动设置页完整配置，供全量覆盖更新（预算/策略）使用。"""
        resp = self.transport.request("gw", "adCampaign/getInfo", {
            "shopId": self.shop_id, "campaignId": str(campaign_id), "type": "sp",
        }, encoding="json", retry=True)
        return resp.get("data") or {}

    def find_group(self, campaign_id: str,
                   ref: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        ref = ref or {}
        rows = self.list_groups(campaign_id)
        matched = []
        for row in rows:
            if ref.get("group_id") and str(row.get("adGroupId") or "") != str(ref["group_id"]):
                continue
            if ref.get("group_internal_id") and str(row.get("id") or "") != str(ref["group_internal_id"]):
                continue
            if ref.get("group") and str(row.get("name") or "") != str(ref["group"]):
                continue
            matched.append({
                "ad_group_id": str(row.get("adGroupId") or ""),
                "group_internal_id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "default_bid": row.get("defaultBid"),
                "state": str(row.get("state") or ""),
                "ad_product_num": row.get("adProductNum"),
                "raw": row,
            })
        if not matched:
            raise NotFound("未找到匹配的广告组", details={"campaign_id": campaign_id,
                                                          "selector": ref})
        if len(matched) > 1:
            raise AmbiguousSelector("广告组选择不唯一", details={
                "selector": ref, "candidates": [x["name"] for x in matched[:20]]})
        return matched[0]

    def find_target(self, campaign_id: str,
                    ref: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        ref = ref or {}
        types = [ref["target_type"]] if ref.get("target_type") else ["auto", "keyword", "targeting"]
        matched = []
        for ttype in types:
            for row in self.list_targets(campaign_id, ttype):
                if ref.get("ad_group_id") and str(row.get("adGroupId") or "") != str(ref["ad_group_id"]):
                    continue
                if ref.get("target_internal_id") and str(row.get("id") or "") != str(ref["target_internal_id"]):
                    continue
                if ref.get("target_id") and str(row.get("targetId") or "") != str(ref["target_id"]):
                    continue
                if ref.get("target_text"):
                    text = str(row.get("targetText") or row.get("keywordText") or "")
                    if text != str(ref["target_text"]):
                        continue
                if ref.get("keyword_text"):
                    if str(row.get("keywordText") or "") != str(ref["keyword_text"]):
                        continue
                matched.append({
                    "target_internal_id": str(row.get("id") or ""),
                    "target_id": str(row.get("targetId") or ""),
                    "target_type": ttype,
                    "target_text": str(row.get("targetText") or row.get("keywordText") or ""),
                    "keyword_text": str(row.get("keywordText") or ""),
                    "match_type": str(row.get("matchType") or ""),
                    "bid": row.get("bid"),
                    "state": str(row.get("state") or ""),
                    "ad_group_id": str(row.get("adGroupId") or ""),
                    "raw": row,
                })
        if not matched:
            raise NotFound("未找到匹配的投放", details={"campaign_id": campaign_id,
                                                        "selector": ref})
        if len(matched) > 1:
            raise AmbiguousSelector("投放选择不唯一，请补充 target_internal_id 或 target_type",
                                    details={"selector": ref,
                                             "candidates": [x["target_internal_id"] for x in matched[:20]]})
        return matched[0]

    def find_ad_product(self, campaign_id: str,
                        ref: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        ref = ref or {}
        rows = self.list_ad_products(campaign_id)
        matched = []
        for row in rows:
            if ref.get("ad_product_internal_id") and str(row.get("id") or "") != str(ref["ad_product_internal_id"]):
                continue
            if ref.get("ad_id") and str(row.get("adId") or "") != str(ref["ad_id"]):
                continue
            if ref.get("ad_group_id") and str(row.get("adGroupId") or "") != str(ref["ad_group_id"]):
                continue
            if ref.get("asin") and str(row.get("asin") or "") != str(ref["asin"]):
                continue
            if ref.get("sku") and str(row.get("sku") or "") != str(ref["sku"]):
                continue
            matched.append({
                "ad_product_internal_id": str(row.get("id") or ""),
                "ad_id": str(row.get("adId") or ""),
                "asin": str(row.get("asin") or ""),
                "sku": str(row.get("sku") or ""),
                "state": str(row.get("state") or ""),
                "ad_group_id": str(row.get("adGroupId") or ""),
                "raw": row,
            })
        if not matched:
            raise NotFound("未找到匹配的广告产品", details={"campaign_id": campaign_id,
                                                            "selector": ref})
        if len(matched) > 1:
            raise AmbiguousSelector("广告产品选择不唯一", details={
                "selector": ref, "candidates": [x["ad_id"] for x in matched[:20]]})
        return matched[0]

    def list_placements(self, campaign_id: str) -> List[Dict[str, Any]]:
        body = {
            "dxmCampaignId": "", "campaignSite": "", "orderField": "adCost",
            "orderType": "desc", "adType": "sp", "campaignIdList": [str(campaign_id)],
            "devIds": [], "strategyType": "", "predicate": "",
            "startDate": date.today().isoformat(), "endDate": date.today().isoformat(),
            "isCompare": False, "compareStartDate": "", "compareEndDate": "",
            "adStrategyTypeList": [], "pageNo": 1, "pageSize": 20, "fromSingle": True,
        }
        resp = self.transport.request("gw", "multiple/placement/getAllPlacementData",
                                      body, encoding="json", retry=True)
        return list(((resp.get("data") or {}).get("page") or {}).get("rows") or [])

    def find_placement(self, campaign_id: str, predicate: str) -> Dict[str, Any]:
        for row in self.list_placements(campaign_id):
            if str(row.get("predicate") or "") == predicate:
                return {
                    "predicate": predicate,
                    "percentage": row.get("percentage"),
                    "strategy": row.get("strategy"),
                    "raw": row,
                }
        raise NotFound("未找到广告位", details={"campaign_id": campaign_id,
                                                "predicate": predicate})

    def list_negatives(self, campaign_id: str, ne_type: str,
                       group_id: str = "") -> List[Dict[str, Any]]:
        body = {
            "orderField": "adCost", "orderType": "desc", "shopIdList": [self.shop_id],
            "campaignIdList": [str(campaign_id)], "groupIdList": [str(group_id)] if group_id else [],
            "adType": "sp", "neType": ne_type, "neLevels": ["group"],
            "matchType": "", "servingStatusList": [], "startDate": None, "endDate": None,
            "neBeforeAfterDay": 30, "dataFrom": 2, "searchType": "exact",
            "searchField": "name", "searchValue": "", "devIds": [], "useAdvanced": False,
            "pageNo": 1, "pageSize": 200, "onlyShowImpressions": False,
        }
        resp = self.transport.request("gw", "multiple/neTarget/getAllNeTargetData",
                                      body, encoding="json", retry=True)
        return list(((resp.get("data") or {}).get("page") or {}).get("rows") or [])

    def find_negative(self, campaign_id: str, ne_type: str,
                      ref: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        ref = ref or {}
        rows = self.list_negatives(campaign_id, ne_type)
        matched = []
        for row in rows:
            if ref.get("negative_internal_id") and str(row.get("id") or "") != str(ref["negative_internal_id"]):
                continue
            if ref.get("ne_target_id") and str(row.get("neTargetId") or "") != str(ref["ne_target_id"]):
                continue
            if ref.get("ad_group_id") and str(row.get("adGroupId") or "") != str(ref["ad_group_id"]):
                continue
            if ref.get("keyword_text") and str(row.get("keywordText") or "") != str(ref["keyword_text"]):
                continue
            if ref.get("asin") and str(row.get("targetingValue") or "") != str(ref["asin"]):
                continue
            matched.append({
                "negative_internal_id": str(row.get("id") or ""),
                "ne_target_id": str(row.get("neTargetId") or ""),
                "ad_group_id": str(row.get("adGroupId") or ""),
                "keyword_text": str(row.get("keywordText") or ""),
                "asin": str(row.get("targetingValue") or ""),
                "state": str(row.get("state") or ""),
                "raw": row,
            })
        if not matched:
            raise NotFound("未找到匹配的否定项", details={"campaign_id": campaign_id,
                                                           "selector": ref})
        if len(matched) > 1:
            raise AmbiguousSelector("否定项选择不唯一", details={"selector": ref})
        return matched[0]

    def validate_target_asin(self, asin: str) -> Dict[str, Any]:
        asin = str(asin or "").strip().upper()
        if not asin:
            raise ValidationFailed("ASIN 不能为空")
        resp = self.transport.request(
            "old", "/api/cpcSPManage/product/searchAsinsForTargeting.json",
            {"shopId": self.shop_id, "asins": asin}, encoding="json", retry=True)
        rows = resp.get("data") or []
        if not rows:
            raise NotFound("ASIN 不存在或不可用于定向", details={"asin": asin})
        return rows[0]

    def list_groups(self, campaign_id: str) -> List[Dict[str, Any]]:
        body = {
            "shopIdList": [self.shop_id], "campaignIdList": [str(campaign_id)],
            "adType": "sp", "startDate": "2020-01-01",
            "endDate": date.today().isoformat(), "statusList": [],
            "pageNo": 1, "pageSize": 200, "fromSingle": True,
        }
        resp = self.transport.request("gw", "multiple/group/getAllGroupData",
                                      body, encoding="json", retry=True)
        node = ((resp.get("data") or {}).get("page") or {})
        return list(node.get("rows") or [])

    def list_ad_products(self, campaign_id: str) -> List[Dict[str, Any]]:
        body = {
            "type": "sp", "campaignIdList": [str(campaign_id)],
            "shopIdList": [self.shop_id],
            "statusList": ["enabled", "paused", "archived"],
            "startDate": "2020-01-01", "endDate": date.today().isoformat(),
            "pageNo": 1, "pageSize": 200, "orderBy": "adCost", "desc": True,
            "searchContents": [], "searchMode": "exact", "searchType": "asin",
            "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": False,
        }
        resp = self.transport.request("gw", "multiple/adProduct/getAdProductList",
                                      body, encoding="json", retry=True)
        node = ((resp.get("data") or {}).get("page") or {})
        return list(node.get("rows") or [])

    def list_targets(self, campaign_id: str, target_type: str = "auto") -> List[Dict[str, Any]]:
        body = {
            "orderField": "adCost", "orderType": "desc",
            "shopIdList": [self.shop_id], "campaignIdList": [str(campaign_id)],
            "adType": "sp", "targetType": target_type,
            "matchTypes": [], "statusList": ["enabled", "paused"],
            "startDate": "2020-01-01", "endDate": date.today().isoformat(),
            "pageNo": 1, "pageSize": 200, "fromSingle": True,
            "useAdvanced": False,
        }
        resp = self.transport.request("gw", "multiple/target/getAllTargetData",
                                      body, encoding="json", retry=True)
        node = ((resp.get("data") or {}).get("page") or {})
        return list(node.get("rows") or [])

    # ------------------------------------------------------------------
    # 组合
    # ------------------------------------------------------------------
    def resolve_portfolio(self, ref: str) -> str:
        ref = str(ref or "").strip()
        if not ref:
            return ""
        rows: List[Dict[str, str]] = []
        with db.get_conn() as conn:
            for r in conn.execute(
                    "SELECT portfolio_id, portfolio_name FROM ad_portfolios "
                    "WHERE shop_id=? OR shop_id IS NULL", (self.shop_id,)):
                rows.append({"portfolio_id": str(r["portfolio_id"]),
                             "portfolio_name": str(r["portfolio_name"] or "")})
        if not rows:
            try:
                from backend.ad_creator.portfolios import get_portfolios
                rows = get_portfolios(getattr(self.transport, "cookies", None),
                                      self.shop_id).get("rows") or []
            except Exception:  # noqa: BLE001
                rows = []
        matched = [x for x in rows
                   if ref in (x.get("portfolio_id"), x.get("portfolio_name"))]
        if not matched:
            # 本地只有部分组合，最后尝试实时组合接口
            try:
                from backend.ad_creator.portfolios import get_portfolios
                rows = get_portfolios(getattr(self.transport, "cookies", None),
                                      self.shop_id, force=True).get("rows") or []
                matched = [x for x in rows
                           if ref in (x.get("portfolio_id"), x.get("portfolio_name"))]
            except Exception:  # noqa: BLE001
                pass
        if not matched:
            raise NotFound("未找到匹配的广告组合", details={"portfolio": ref})
        if len(matched) > 1:
            raise AmbiguousSelector("广告组合名称不唯一", details={"portfolio": ref,
                                                                  "candidates": matched})
        return str(matched[0]["portfolio_id"])

    # ------------------------------------------------------------------
    # 商品（实时赛狐状态；创建广告必须走这里）
    # ------------------------------------------------------------------
    def live_products(self, refresh: bool = False) -> List[Dict[str, Any]]:
        hit = self._cache.get("live_products")
        if hit and not refresh and time.time() - hit[0] < 60:
            return list(hit[1])
        out: List[Dict[str, Any]] = []
        seen = set()
        for page in range(1, 30):
            body = {
                "pageNo": page, "pageSize": 200, "shopId": self.shop_id,
                "searchField": "asin", "searchValue": "", "searchType": "exact",
                "onlineStatus": "Active", "orderType": "desc", "type": "sp",
            }
            resp = self.transport.request("gw", "product/productRightSpPageListSearch",
                                          body, encoding="json", retry=True)
            data = resp.get("data") or {}
            rows = data.get("rows") or []
            for parent in rows:
                children = parent.get("childList") or []
                if children:
                    for child in children:
                        asin = str(child.get("asin") or "").strip()
                        if not asin or asin in seen:
                            continue
                        seen.add(asin)
                        out.append({
                            "id": child.get("id"),
                            "asin": asin,
                            "sku": str(child.get("sku") or ""),
                            "online_status": str(child.get("onlineStatus")
                                                 or parent.get("onlineStatus") or ""),
                            "title": str(child.get("title") or parent.get("title") or ""),
                            "mainImage": str(child.get("imgUrl") or parent.get("imgUrl") or ""),
                            "price": str(child.get("price") or parent.get("price") or "0"),
                            "parent_asin": str(parent.get("asin") or ""),
                            "is_child": True,
                        })
                else:
                    asin = str(parent.get("asin") or "").strip()
                    if not asin or asin in seen:
                        continue
                    seen.add(asin)
                    out.append({
                        "id": parent.get("id"), "asin": asin,
                        "sku": str(parent.get("sku") or ""),
                        "online_status": str(parent.get("onlineStatus") or ""),
                        "title": str(parent.get("title") or ""),
                        "mainImage": str(parent.get("imgUrl") or ""),
                        "price": str(parent.get("price") or "0"),
                        "parent_asin": asin, "is_child": False,
                    })
            if not rows or page >= int(data.get("totalPage") or 1):
                break
        self._cache["live_products"] = (time.time(), out)
        return list(out)

    def resolve_live_products(self, refs: Iterable[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
        rows = self.live_products()
        resolved: List[Dict[str, Any]] = []
        warnings: List[str] = []
        seen_asins = set()
        for item in refs or []:
            if hasattr(item, "model_dump"):
                item = item.model_dump()
            asin = str((item or {}).get("asin") or "").strip()
            sku = str((item or {}).get("sku") or "").strip()
            parent = str((item or {}).get("parent_asin") or "").strip()
            if not (asin or sku or parent):
                raise ValidationFailed("products 每项需要 asin / sku / parent_asin 之一")
            matched = []
            for row in rows:
                if asin and row["asin"] != asin:
                    continue
                if sku and row["sku"] != sku:
                    continue
                if parent and row["parent_asin"] != parent:
                    continue
                matched.append(row)
            if not matched:
                raise NotFound("赛狐实时商品库未找到可投放商品",
                               details={"asin": asin, "sku": sku, "parent_asin": parent})
            for row in matched:
                if row["asin"] in seen_asins:
                    continue
                if str(row.get("online_status") or "").lower() != "active":
                    raise ValidationFailed(
                        "商品当前不是 Active，不能创建广告",
                        details={"asin": row["asin"], "status": row["online_status"]})
                lib_id = row.get("id")
                if lib_id is None or not str(lib_id).strip().isdigit():
                    raise ValidationFailed(
                        "商品缺少可用的产品库 id，不能创建广告",
                        details={"asin": row["asin"]})
                seen_asins.add(row["asin"])
                resolved.append({
                    "id": int(str(lib_id)), "sku": row["sku"], "asin": row["asin"],
                    "parent_asin": row["parent_asin"],
                    "mainImage": row["mainImage"], "online_status": row["online_status"],
                    "title": row["title"], "price": row.get("price") or "0",
                })
        if not resolved:
            raise NotFound("没有解析到可投放的广告产品")
        return resolved, warnings

    def resolve_products(self, refs: Iterable[Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
        items = []
        for x in refs or []:
            if hasattr(x, "model_dump"):
                x = x.model_dump()
            if not isinstance(x, dict):
                raise ValidationFailed("products 必须是对象数组")
            asin = str(x.get("asin") or "").strip()
            sku = str(x.get("sku") or "").strip()
            parent = str(x.get("parent_asin") or "").strip()
            if not (asin or sku or parent):
                raise ValidationFailed("products 每项需要 asin / sku / parent_asin 之一")
            items.append((asin, sku, parent))

        resolved: List[Dict[str, Any]] = []
        warnings: List[str] = []
        seen_asins = set()
        with db.get_conn() as conn:
            for asin, sku, parent in items:
                if parent:
                    rows = list(conn.execute(
                        "SELECT asin,sku,parent_asin,online_status,main_image,title,raw_json "
                        "FROM online_products WHERE parent_asin=?", (parent,)))
                    if not rows:
                        raise NotFound("父 ASIN 下没有可用子体", details={"parent_asin": parent})
                elif asin and sku:
                    rows = list(conn.execute(
                        "SELECT asin,sku,parent_asin,online_status,main_image,title,raw_json "
                        "FROM online_products WHERE asin=? AND sku=?", (asin, sku)))
                elif asin:
                    rows = list(conn.execute(
                        "SELECT asin,sku,parent_asin,online_status,main_image,title,raw_json "
                        "FROM online_products WHERE asin=?", (asin,)))
                else:
                    rows = list(conn.execute(
                        "SELECT asin,sku,parent_asin,online_status,main_image,title,raw_json "
                        "FROM online_products WHERE sku=?", (sku,)))
                if not rows:
                    raise NotFound("产品库中未找到商品", details={"asin": asin, "sku": sku,
                                                                 "parent_asin": parent})
                for row in rows:
                    child_asin = str(row["asin"] or "").strip()
                    if not child_asin or child_asin in seen_asins:
                        continue
                    lib_id = None
                    try:
                        lib_id = (json.loads(row["raw_json"] or "{}") or {}).get("id")
                    except Exception:  # noqa: BLE001
                        lib_id = None
                    if lib_id is None or not str(lib_id).strip().isdigit():
                        raise ValidationFailed(
                            "商品缺少可用的产品库 id（8 位数字），不能创建广告",
                            details={"asin": child_asin, "sku": row["sku"],
                                     "hint": "请先同步在线产品库，确保 raw_json.id 存在"})
                    status = str(row["online_status"] or "")
                    if status.lower() == "incomplete":
                        raise ValidationFailed(
                            "商品状态为 Incomplete，不能创建广告",
                            details={"asin": child_asin, "sku": row["sku"],
                                     "hint": "请先在赛狐确认 Listing 完整且可售"})
                    if status and status.lower() != "active":
                        warnings.append(f"{child_asin} 的本地状态为 {status}，请先确认可售")
                    seen_asins.add(child_asin)
                    resolved.append({
                        "id": int(str(lib_id)),
                        "sku": str(row["sku"] or ""),
                        "asin": child_asin,
                        "parent_asin": str(row["parent_asin"] or ""),
                        "mainImage": str(row["main_image"] or ""),
                        "online_status": status,
                        "title": str(row["title"] or ""),
                    })
        if not resolved:
            raise NotFound("没有解析到可推广的广告产品")
        return resolved, warnings
