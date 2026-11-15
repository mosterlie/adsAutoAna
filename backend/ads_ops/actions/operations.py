# -*- coding: utf-8 -*-
"""L1/L2 广告操作处理器。

所有动作都遵循同一生命周期：
    preview -> confirm_token -> execute -> readback verify -> audit

写接口仍然通过 Resolver 解析业务对象，LLM 不接触 URL、Cookie 和内部 ID。
"""
from __future__ import annotations

import json
import math
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from backend.ads_ops.actions.base import (ActionContext, ActionHandler,
                                          ExecutionOutcome, PreparedAction)
from backend.ads_ops.errors import (AlreadyInState, NameConflict, NotFound,
                                    SellerAPIError, ValidationFailed,
                                    VerificationFailed)
from backend.ads_ops.schema import (AddGroupParams, AddProductsParams,
                                    ArchiveNegativeParams, BidParams,
                                    BudgetParams, GroupBidParams,
                                    MovePortfolioParams, NegativeAsinParams,
                                    NegativeKeywordParams, PlacementParams,
                                    RenameParams, StateParams)


def _model(model, params: Dict[str, Any]):
    try:
        return model.model_validate(params or {})
    except ValidationError as exc:
        raise ValidationFailed("参数不合法", details=exc.errors()) from exc


def _campaign(ctx: ActionContext, selector: Dict[str, Any]) -> Dict[str, Any]:
    return ctx.resolver.find_campaign(selector or {})


def _group(ctx: ActionContext, campaign_id: str,
           selector: Dict[str, Any]) -> Dict[str, Any]:
    return ctx.resolver.find_group(campaign_id, selector or {})


def _target(ctx: ActionContext, campaign_id: str,
            selector: Dict[str, Any]) -> Dict[str, Any]:
    ref = dict(selector or {})
    if not ref.get("ad_group_id") and ref.get("group"):
        group = ctx.resolver.find_group(campaign_id, ref)
        ref["ad_group_id"] = group["ad_group_id"]
    return ctx.resolver.find_target(campaign_id, ref)


def _ad_product(ctx: ActionContext, campaign_id: str,
                selector: Dict[str, Any]) -> Dict[str, Any]:
    ref = dict(selector or {})
    if not ref.get("ad_group_id") and ref.get("group"):
        group = ctx.resolver.find_group(campaign_id, ref)
        ref["ad_group_id"] = group["ad_group_id"]
    return ctx.resolver.find_ad_product(campaign_id, ref)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _same(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) < 0.01
    except (TypeError, ValueError):
        return str(a) == str(b)


def _check_expected(actual: Any, expected: Any, label: str) -> None:
    if expected is not None and not _same(actual, expected):
        raise ValidationFailed(
            f"{label} 已变化，请重新 preview",
            details={"actual": actual, "expected": expected})


def _check_change(old: Any, new: Any, max_pct: float, label: str) -> None:
    try:
        oldf, newf = float(old), float(new)
    except (TypeError, ValueError):
        return
    if oldf <= 0:
        return
    pct = abs(newf - oldf) / oldf * 100
    if pct > max_pct + 1e-9:
        raise ValidationFailed(
            f"{label} 变动 {pct:.1f}% 超过允许的 {max_pct}%",
            details={"before": oldf, "after": newf, "max_change_pct": max_pct})


def _request(gateway: str, path: str, payload: Dict[str, Any],
             encoding: str = "json", retry: bool = True) -> Dict[str, Any]:
    return {"gateway": gateway, "path": path, "payload": payload,
            "encoding": encoding, "retry": retry}


class OperationHandler(ActionHandler):
    """统一发送请求；具体动作只负责 prepare/verify。"""

    def execute(self, ctx: ActionContext, prepared: PreparedAction) -> ExecutionOutcome:
        q = prepared.request
        resp = ctx.transport.request(
            q.get("gateway", "gw"), q.get("path", ""),
            q.get("payload") or {}, encoding=q.get("encoding", "json"),
            retry=bool(q.get("retry", True)))
        return ExecutionOutcome(status="applied", response=resp,
                                created=prepared.after or {})

    def rollback_plan(self, ctx: ActionContext, prepared: PreparedAction,
                      outcome: ExecutionOutcome) -> Dict[str, Any]:
        return prepared.rollback or {"available": False, "reason": "无自动回滚方案"}


# ---------------------------------------------------------------------------
# L1
# ---------------------------------------------------------------------------
class SetTargetBidHandler(OperationHandler):
    action = "set_target_bid"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(BidParams, params)
        camp = _campaign(ctx, selector)
        target = _target(ctx, camp["campaign_id"], selector)
        _check_expected(target.get("bid"), p.expected_before, "竞价")
        old = target.get("bid")
        _check_change(old, p.bid, 30, "竞价")
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": camp["campaign_internal_id"],
                      "target_internal_id": target["target_internal_id"],
                      "target_id": target["target_id"],
                      "target_text": target["target_text"]},
            before={"bid": old}, after={"bid": p.bid},
            request=_request("gw", "spTarget/updateBid",
                             {"shopId": ctx.resolver.shop_id,
                              "id": target["target_internal_id"],
                              "bid": p.bid}, "form"),
            warnings=[],
            rollback={"available": True, "action": "set_target_bid",
                      "selector": {"campaign_id": camp["campaign_id"],
                                   "target_internal_id": target["target_internal_id"]},
                      "params": {"bid": old}})

    def verify(self, ctx, prepared, outcome):
        target = ctx.resolver.find_target(
            prepared.resolved["campaign_id"],
            {"target_internal_id": prepared.resolved["target_internal_id"]})
        ok = _same(target.get("bid"), prepared.after["bid"])
        if not ok:
            raise VerificationFailed("竞价读回不一致",
                                     details={"actual": target.get("bid"),
                                              "expected": prepared.after["bid"]})
        return {"verified": True, "target": target}


class SetTargetStateHandler(OperationHandler):
    action = "set_target_state"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(StateParams, params)
        camp = _campaign(ctx, selector)
        target = _target(ctx, camp["campaign_id"], selector)
        _check_expected(target.get("state"), p.expected_before, "投放状态")
        old = target.get("state")
        if old == p.state:
            raise AlreadyInState("投放已经是目标状态", details={"state": old})
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": camp["campaign_internal_id"],
                      "target_internal_id": target["target_internal_id"]},
            before={"state": old}, after={"state": p.state},
            request=_request("gw", "spTarget/updateState",
                             {"shopId": ctx.resolver.shop_id,
                              "id": target["target_internal_id"],
                              "state": p.state}, "form"),
            warnings=[],
            rollback={"available": True, "action": "set_target_state",
                      "selector": {"campaign_id": camp["campaign_id"],
                                   "target_internal_id": target["target_internal_id"]},
                      "params": {"state": old}})

    def verify(self, ctx, prepared, outcome):
        target = ctx.resolver.find_target(
            prepared.resolved["campaign_id"],
            {"target_internal_id": prepared.resolved["target_internal_id"]})
        if target.get("state") != prepared.after["state"]:
            raise VerificationFailed("投放状态读回不一致",
                                     details={"actual": target.get("state"),
                                              "expected": prepared.after["state"]})
        return {"verified": True, "target": target}


class SetGroupBidHandler(OperationHandler):
    action = "set_group_bid"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(GroupBidParams, params)
        camp = _campaign(ctx, selector)
        group = _group(ctx, camp["campaign_id"], selector)
        _check_expected(group.get("default_bid"), p.expected_before, "组默认竞价")
        old = group.get("default_bid")
        _check_change(old, p.default_bid, 30, "组默认竞价")
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "group_internal_id": group["group_internal_id"],
                      "group_id": group["ad_group_id"],
                      "group_name": group["name"]},
            before={"default_bid": old}, after={"default_bid": p.default_bid},
            request=_request("old", "/api/cpcSPManage/group/update.json", {
                "shopId": ctx.resolver.shop_id,
                "dxmGroupId": int(group["group_internal_id"])
                if str(group["group_internal_id"]).isdigit()
                else group["group_internal_id"],
                "name": group["name"],
                "defaultBid": str(p.default_bid),
            }, "json"),
            warnings=[],
            rollback={"available": True, "action": "set_group_bid",
                      "selector": {"campaign_id": camp["campaign_id"],
                                   "group": group["name"]},
                      "params": {"default_bid": old}})

    def verify(self, ctx, prepared, outcome):
        group = ctx.resolver.find_group(
            prepared.resolved["campaign_id"],
            {"group": prepared.resolved["group_name"]})
        if not _same(group.get("default_bid"), prepared.after["default_bid"]):
            raise VerificationFailed("广告组默认竞价读回不一致",
                                     details={"actual": group.get("default_bid"),
                                              "expected": prepared.after["default_bid"]})
        return {"verified": True, "group": group}


class SetGroupStateHandler(OperationHandler):
    action = "set_group_state"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(StateParams, params)
        camp = _campaign(ctx, selector)
        group = _group(ctx, camp["campaign_id"], selector)
        _check_expected(group.get("state"), p.expected_before, "广告组状态")
        old = group.get("state")
        if old == p.state:
            raise AlreadyInState("广告组已经是目标状态", details={"state": old})
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "group_internal_id": group["group_internal_id"],
                      "group_name": group["name"]},
            before={"state": old}, after={"state": p.state},
            request=_request("old", "/api/cpcSPManage/group/updateState.json", {
                "shopId": ctx.resolver.shop_id,
                "id": group["group_internal_id"],
                "state": p.state,
            }, "form"),
            warnings=[],
            rollback={"available": True, "action": "set_group_state",
                      "selector": {"campaign_id": camp["campaign_id"],
                                   "group": group["name"]},
                      "params": {"state": old}})

    def verify(self, ctx, prepared, outcome):
        group = ctx.resolver.find_group(
            prepared.resolved["campaign_id"],
            {"group": prepared.resolved["group_name"]})
        if group.get("state") != prepared.after["state"]:
            raise VerificationFailed("广告组状态读回不一致",
                                     details={"actual": group.get("state"),
                                              "expected": prepared.after["state"]})
        return {"verified": True, "group": group}


class SetCampaignStateHandler(OperationHandler):
    action = "set_campaign_state"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(StateParams, params)
        camp = _campaign(ctx, selector)
        _check_expected(camp.get("state"), p.expected_before, "活动状态")
        old = camp.get("state")
        if old == p.state:
            raise AlreadyInState("活动已经是目标状态", details={"state": old})
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": camp["campaign_internal_id"],
                      "campaign_name": camp["name"]},
            before={"state": old}, after={"state": p.state},
            request=_request("gw", "spCampaign/updateState", {
                "shopId": ctx.resolver.shop_id,
                "id": camp["campaign_internal_id"],
                "state": p.state,
            }, "form"),
            warnings=[],
            rollback={"available": True, "action": "set_campaign_state",
                      "selector": {"campaign_id": camp["campaign_id"]},
                      "params": {"state": old}})

    def verify(self, ctx, prepared, outcome):
        camp = ctx.resolver.find_campaign(
            {"campaign_id": prepared.resolved["campaign_id"]},
            include_archived=True)
        if camp.get("state") != prepared.after["state"]:
            raise VerificationFailed("活动状态读回不一致",
                                     details={"actual": camp.get("state"),
                                              "expected": prepared.after["state"]})
        return {"verified": True, "campaign": camp}


class SetCampaignPlacementHandler(OperationHandler):
    action = "set_campaign_placement"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(PlacementParams, params)
        camp = _campaign(ctx, selector)
        placement = ctx.resolver.find_placement(camp["campaign_id"], p.placement)
        _check_expected(placement.get("percentage"), p.expected_before, "广告位比例")
        old = placement.get("percentage")
        if abs(float(p.value) - _num(old, 0)) > 50:
            raise ValidationFailed("广告位加价单次变动不能超过 50 个百分点",
                                   details={"before": old, "after": p.value})
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": camp["campaign_internal_id"],
                      "placement": p.placement},
            before={"percentage": old}, after={"percentage": p.value},
            request=_request("old",
                             "/api/cpcSPManage/campaign/updatePlacementAdjustment.json",
                             {"shopId": ctx.resolver.shop_id,
                              "id": camp["campaign_internal_id"],
                              "placement": p.placement,
                              "value": int(p.value)}, "form"),
            warnings=[],
            rollback={"available": True, "action": "set_campaign_placement",
                      "selector": {"campaign_id": camp["campaign_id"]},
                      "params": {"placement": p.placement,
                                 "value": int(round(_num(old, 0)))}})

    def verify(self, ctx, prepared, outcome):
        placement = ctx.resolver.find_placement(
            prepared.resolved["campaign_id"], prepared.resolved["placement"])
        if not _same(placement.get("percentage"), prepared.after["percentage"]):
            raise VerificationFailed("广告位比例读回不一致",
                                     details={"actual": placement.get("percentage"),
                                              "expected": prepared.after["percentage"]})
        return {"verified": True, "placement": placement}


class SetCampaignBudgetHandler(OperationHandler):
    action = "set_campaign_budget"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(BudgetParams, params)
        camp = _campaign(ctx, selector)
        info = ctx.resolver.campaign_config(camp["campaign_id"])
        if not info.get("id"):
            raise NotFound("未读取到活动配置", details={"campaign_id": camp["campaign_id"]})
        old = info.get("dailyBudget")
        _check_expected(old, p.expected_before, "活动预算")
        _check_change(old, p.daily_budget, 50, "活动预算")
        payload = {
            "shopId": ctx.resolver.shop_id,
            "dxmCampaignId": int(info["id"]),
            "name": info.get("campaignName") or camp["name"],
            "portfolioId": info.get("portfolioId") or "-1",
            "startDateStr": info.get("startDate") or "",
            "endDateStr": info.get("endDate") or "",
            "dailyBudget": str(p.daily_budget),
            "targetingType": info.get("targetingType") or "manual",
            "strategy": info.get("strategy") or "legacyForSales",
            "placementTop": _num(info.get("placementTop")),
            "placementProductPage": _num(info.get("placementProductPage")),
            "placementRestOfSearch": _num(info.get("placementRestOfSearch")),
            "placementSiteAmazonBusiness": _num(info.get("placementSiteAmazonBusiness")),
            "creativeBidPercentage": _num(info.get("creativeBidPercentage")),
            "audienceId": info.get("audienceId") or "",
            "audienceBidPercentage": _num(info.get("audienceBidPercentage")),
            "defaultAudience": bool(info.get("defaultAudience")),
            "campaignSite": info.get("campaignSite") or "amazonAll",
            "offAmazonBudgetControlStrategy": info.get("offAmazonBudgetControlStrategy"),
        }
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": str(info["id"]),
                      "campaign_name": payload["name"]},
            before={"daily_budget": old}, after={"daily_budget": p.daily_budget},
            request=_request("gw", "spCampaign/update", payload, "json"),
            warnings=[],
            rollback={"available": True, "action": "set_campaign_budget",
                      "selector": {"campaign_id": camp["campaign_id"]},
                      "params": {"daily_budget": float(old or 0)}})

    def verify(self, ctx, prepared, outcome):
        info = ctx.resolver.campaign_config(prepared.resolved["campaign_id"])
        if not _same(info.get("dailyBudget"), prepared.after["daily_budget"]):
            raise VerificationFailed("活动预算读回不一致",
                                     details={"actual": info.get("dailyBudget"),
                                              "expected": prepared.after["daily_budget"]})
        return {"verified": True, "campaign": {
            "campaign_id": prepared.resolved["campaign_id"],
            "daily_budget": info.get("dailyBudget")}}


class SetAdProductStateHandler(OperationHandler):
    action = "set_ad_product_state"
    level = "L1"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(StateParams, params)
        camp = _campaign(ctx, selector)
        prod = _ad_product(ctx, camp["campaign_id"], selector)
        _check_expected(prod.get("state"), p.expected_before, "广告产品状态")
        old = prod.get("state")
        if old == p.state:
            raise AlreadyInState("广告产品已经是目标状态", details={"state": old})
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "ad_product_internal_id": prod["ad_product_internal_id"],
                      "asin": prod["asin"]},
            before={"state": old}, after={"state": p.state},
            request=_request("old", "/api/cpcSPManage/product/updateState.json",
                             {"shopId": ctx.resolver.shop_id,
                              "id": prod["ad_product_internal_id"],
                              "state": p.state}, "form"),
            warnings=[],
            rollback={"available": True, "action": "set_ad_product_state",
                      "selector": {"campaign_id": camp["campaign_id"],
                                   "ad_product_internal_id": prod["ad_product_internal_id"]},
                      "params": {"state": old}})

    def verify(self, ctx, prepared, outcome):
        prod = ctx.resolver.find_ad_product(
            prepared.resolved["campaign_id"],
            {"ad_product_internal_id": prepared.resolved["ad_product_internal_id"]})
        if prod.get("state") != prepared.after["state"]:
            raise VerificationFailed("广告产品状态读回不一致",
                                     details={"actual": prod.get("state"),
                                              "expected": prepared.after["state"]})
        return {"verified": True, "ad_product": prod}


# ---------------------------------------------------------------------------
# L2
# ---------------------------------------------------------------------------
class AddNegativeKeywordHandler(OperationHandler):
    action = "add_negative_keyword"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(NegativeKeywordParams, params)
        camp = _campaign(ctx, selector)
        group = _group(ctx, camp["campaign_id"], selector)
        ne_keywords = [{"keywordText": x.keyword_text.strip(),
                        "matchType": x.match_type}
                       for x in p.keywords if x.keyword_text.strip()]
        if not ne_keywords:
            raise ValidationFailed("否定关键词不能为空")
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "group_id": group["ad_group_id"],
                      "group_name": group["name"]},
            before={"keywords": []},
            after={"keywords": ne_keywords, "ne_level": p.ne_level},
            request=_request("gw", "negative/createSpGroupNeKeywords", {
                "shopId": ctx.resolver.shop_id,
                "groupId": group["ad_group_id"],
                "neKeywords": ne_keywords,
            }, "json", retry=False),
            warnings=[],
            rollback={"available": False, "reason": "否定项创建后需按列表归档，暂不自动回滚"})

    def execute(self, ctx, prepared, outcome=None):
        resp = ctx.transport.request(
            "gw", "negative/createSpGroupNeKeywords", prepared.request["payload"],
            encoding="json", retry=False)
        data = resp.get("data") or {}
        errors = data.get("errMsgList") or [] if isinstance(data, dict) else []
        return ExecutionOutcome(
            status="partial_success" if errors else "applied",
            response=resp, created={"errors": errors},
            warnings=[f"否定关键词部分失败: {errors}"] if errors else [])

    def verify(self, ctx, prepared, outcome):
        rows = ctx.resolver.list_negatives(
            prepared.resolved["campaign_id"], "keyword",
            prepared.resolved["group_id"])
        existing = {str(x.get("keywordText") or "") for x in rows
                    if str(x.get("state") or "") == "enabled"}
        missing = [x["keywordText"] for x in prepared.after["keywords"]
                   if x["keywordText"] not in existing]
        if missing:
            raise VerificationFailed("否定关键词读回缺失", details={"missing": missing})
        return {"verified": True, "negatives": rows}


class AddNegativeAsinHandler(OperationHandler):
    action = "add_negative_asin"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(NegativeAsinParams, params)
        camp = _campaign(ctx, selector)
        group = _group(ctx, camp["campaign_id"], selector)
        targets = []
        for asin in p.asins:
            info = ctx.resolver.validate_target_asin(asin)
            targets.append({
                "asin": str(info.get("asin") or asin).upper(),
                "imgUrl": info.get("imgUrl") or "",
                "isAdd": True, "type": "asin",
            })
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "group_id": group["ad_group_id"],
                      "group_name": group["name"]},
            before={"asins": []},
            after={"asins": [x["asin"] for x in targets], "ne_level": p.ne_level},
            request=_request("gw", "spTarget/createNeTargeting", {
                "shopId": ctx.resolver.shop_id,
                "groupId": group["ad_group_id"],
                "neTargetings": targets,
            }, "json", retry=False),
            warnings=[],
            rollback={"available": False, "reason": "否定 ASIN 创建后可手工归档"})

    def verify(self, ctx, prepared, outcome):
        rows = ctx.resolver.list_negatives(
            prepared.resolved["campaign_id"], "target",
            prepared.resolved["group_id"])
        existing = {str(x.get("targetingValue") or "") for x in rows
                    if str(x.get("state") or "") == "enabled"}
        missing = [x for x in prepared.after["asins"] if x not in existing]
        if missing:
            raise VerificationFailed("否定 ASIN 读回缺失", details={"missing": missing})
        return {"verified": True, "negatives": rows}


class ArchiveNegativeHandler(OperationHandler):
    action = "archive_negative"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(ArchiveNegativeParams, params)
        camp = _campaign(ctx, selector)
        neg = ctx.resolver.find_negative(camp["campaign_id"], p.ne_type, selector)
        if neg.get("state") == "archived":
            raise AlreadyInState("否定项已经归档")
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "negative_internal_id": neg["negative_internal_id"],
                      "ne_target_id": neg["ne_target_id"],
                      "ne_type": p.ne_type},
            before={"state": neg.get("state")}, after={"state": "archived"},
            request=_request("gw", "multiple/neTarget/updateBatchMultiShop", {
                "vos": [{
                    "shopId": ctx.resolver.shop_id, "adType": "sp",
                    "neLevel": p.ne_level, "neType": p.ne_type,
                    "neTargetId": neg["ne_target_id"],
                    "id": neg["negative_internal_id"],
                }]
            }, "json", retry=True),
            warnings=[],
            rollback={"available": False, "reason": "否定项归档不可逆"})

    def verify(self, ctx, prepared, outcome):
        neg = ctx.resolver.find_negative(
            prepared.resolved["campaign_id"], prepared.resolved["ne_type"],
            {"negative_internal_id": prepared.resolved["negative_internal_id"]})
        if neg.get("state") != "archived":
            raise VerificationFailed("否定项归档状态读回不一致",
                                     details={"actual": neg.get("state")})
        return {"verified": True, "negative": neg}


class AddProductsToGroupHandler(OperationHandler):
    action = "add_products_to_group"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(AddProductsParams, params)
        camp = _campaign(ctx, selector)
        group = _group(ctx, camp["campaign_id"], selector)
        products, _ = ctx.resolver.resolve_live_products(p.products)
        payload_products = [{
            "id": int(x["id"]), "shopId": int(ctx.resolver.shop_id),
            "sku": x.get("sku") or "", "asin": x.get("asin") or "",
            "onlineStatus": "Active", "title": x.get("title") or "",
            "imgUrl": x.get("mainImage") or "",
            "domain": "www.amazon.co.jp", "isMeetConditions": True,
            "price": str(x.get("price") or "0"), "type": "sp",
            "isAdd": False, "variantAdd": False,
        } for x in products]
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "group_id": group["ad_group_id"],
                      "group_name": group["name"],
                      "asins": [x["asin"] for x in products]},
            before={"ad_products": []},
            after={"asins": [x["asin"] for x in products]},
            request=_request("old", "/api/cpcSPManage/product/v2/add.json", {
                "shopId": ctx.resolver.shop_id,
                "groupIds": [group["ad_group_id"]],
                "products": payload_products,
            }, "json", retry=False),
            warnings=[],
            rollback={"available": False, "reason": "需按新广告产品 id 归档"})

    def execute(self, ctx, prepared, outcome=None):
        resp = ctx.transport.request(
            "old", "/api/cpcSPManage/product/v2/add.json",
            prepared.request["payload"], encoding="json", retry=False)
        data = resp.get("data")
        errors = []
        if isinstance(data, str) and data.strip():
            try:
                errors = json.loads(data)
            except Exception:  # noqa: BLE001
                errors = [{"error": data}]
        elif isinstance(data, list):
            errors = data
        return ExecutionOutcome(
            status="partial_success" if errors else "applied",
            response=resp, created={"errors": errors},
            warnings=[f"部分产品添加失败: {errors}"] if errors else [])

    def verify(self, ctx, prepared, outcome):
        rows = ctx.resolver.list_ad_products(prepared.resolved["campaign_id"])
        group_asin = {str(x.get("asin") or "") for x in rows
                      if str(x.get("adGroupId") or "") == prepared.resolved["group_id"]
                      and str(x.get("state") or "") != "archived"}
        missing = [a for a in prepared.after["asins"] if a not in group_asin]
        if missing:
            raise VerificationFailed("广告产品读回缺失", details={"missing": missing})
        return {"verified": True, "ad_products": rows}


class ArchiveAdProductHandler(OperationHandler):
    action = "archive_ad_product"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        camp = _campaign(ctx, selector)
        prod = _ad_product(ctx, camp["campaign_id"], selector)
        if prod.get("state") == "archived":
            raise AlreadyInState("广告产品已经归档")
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "ad_product_internal_id": prod["ad_product_internal_id"],
                      "asin": prod["asin"]},
            before={"state": prod.get("state")}, after={"state": "archived"},
            request=_request("old", "/api/cpcSPManage/product/archive.json", {
                "shopId": ctx.resolver.shop_id,
                "id": prod["ad_product_internal_id"],
            }, "form", retry=True),
            warnings=[],
            rollback={"available": True, "action": "add_products_to_group",
                      "params": {"products": [{"asin": prod["asin"]}]}})

    def verify(self, ctx, prepared, outcome):
        prod = ctx.resolver.find_ad_product(
            prepared.resolved["campaign_id"],
            {"ad_product_internal_id": prepared.resolved["ad_product_internal_id"]})
        if prod.get("state") != "archived":
            raise VerificationFailed("广告产品归档状态读回不一致",
                                     details={"actual": prod.get("state")})
        return {"verified": True, "ad_product": prod}


class AddGroupHandler(OperationHandler):
    action = "add_group"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(AddGroupParams, params)
        camp = _campaign(ctx, selector)
        info = ctx.resolver.campaign_config(camp["campaign_id"])
        products, _ = ctx.resolver.resolve_live_products(p.products)
        # 复用 create 的组/投放构造逻辑
        from backend.ads_ops.actions.campaign import CreateCampaignHandler
        target = CreateCampaignHandler()._build_group_target(
            SimpleNamespace(targeting_type=p.targeting_type), p)
        group_info = {
            "groupId": "", "defaultBid": str(p.default_bid), "name": p.name,
            "products": [{"id": int(x["id"]), "sku": x.get("sku") or "",
                          "asin": x.get("asin") or "",
                          "mainImage": x.get("mainImage") or "",
                          "spotlightVideos": None} for x in products],
        }
        campaign_info = {
            "campaignId": camp["campaign_id"], "startDateStr": "", "endDateStr": "",
            "dailyBudget": str(info.get("dailyBudget") or camp.get("daily_budget") or ""),
            "targetingType": p.targeting_type,
            "strategy": info.get("strategy") or "legacyForSales",
            "placementTop": None, "placementRestOfSearch": None,
            "placementProductPage": None, "placementSiteAmazonBusiness": None,
            "creativeBidPercentage": None,
            "campaignSite": info.get("campaignSite") or "amazonAll",
            "name": "", "portfolioId": "",
            "audienceId": "", "audienceBidPercentage": "", "defaultAudience": False,
        }
        payload = {"shopId": ctx.resolver.shop_id,
                   "marketplaceId": ctx.resolver.marketplace_id,
                   "spCampaignInfo": campaign_info,
                   "spGroupInfo": group_info, "spTargetInfo": target}
        mode = ("auto" if p.targeting_type == "auto"
                else ("keyword" if p.keywords else "product"))
        expected = (4 if mode == "auto"
                    else (len(p.keywords) if mode == "keyword" else len(p.targetings)))
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_name": camp["name"],
                      "group_name": p.name, "mode": mode,
                      "expected_targets": expected,
                      "asins": [x["asin"] for x in products]},
            before={"groups": []},
            after={"group_name": p.name, "default_bid": p.default_bid,
                   "asins": [x["asin"] for x in products],
                   "mode": mode, "expected_targets": expected},
            request=_request("gw", "ad/sp/create", payload, "json", retry=False),
            warnings=[],
            rollback={"available": False,
                      "reason": "赛狐没有删除广告组接口；必要时归档组内产品"})

    def execute(self, ctx, prepared, outcome=None):
        resp = ctx.transport.request("gw", "ad/sp/create", prepared.request["payload"],
                                     encoding="json", retry=False)
        data = resp.get("data") or {}
        group = data.get("groupResp") or {}
        if int(group.get("code") or 0) != 0 or not group.get("adGroupId"):
            raise SellerAPIError("追加广告组失败", details={"groupResp": group})
        return ExecutionOutcome(
            status="applied", response=resp,
            created={"campaign_id": prepared.resolved["campaign_id"],
                     "ad_group_id": str(group.get("adGroupId") or "")})

    def verify(self, ctx, prepared, outcome):
        group = ctx.resolver.find_group(
            prepared.resolved["campaign_id"], {"group": prepared.resolved["group_name"]})
        rows = ctx.resolver.list_ad_products(prepared.resolved["campaign_id"])
        group_asin = {str(x.get("asin") or "") for x in rows
                      if str(x.get("adGroupId") or "") == group["ad_group_id"]
                      and str(x.get("state") or "") != "archived"}
        missing = [x for x in prepared.after["asins"] if x not in group_asin]
        targets = ctx.resolver.list_targets(
            prepared.resolved["campaign_id"], prepared.resolved["mode"])
        gtargets = [x for x in targets
                    if str(x.get("adGroupId") or "") == group["ad_group_id"]]
        ok = (not missing and len(gtargets) >= prepared.after["expected_targets"])
        if not ok:
            raise VerificationFailed("广告组读回不完整",
                                     details={"missing_asins": missing,
                                              "targets": len(gtargets),
                                              "expected_targets": prepared.after["expected_targets"]})
        return {"verified": True, "group": group,
                "ad_product_count": len(group_asin),
                "target_count": len(gtargets)}


class RenameCampaignHandler(OperationHandler):
    action = "rename_campaign"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(RenameParams, params)
        camp = _campaign(ctx, selector)
        old = camp["name"]
        if p.name == old:
            raise AlreadyInState("活动名称未变化")
        existing = ctx.resolver.campaign_name_exists(p.name)
        if existing and existing.get("campaign_id") != camp["campaign_id"]:
            raise NameConflict("活动名称已存在", details={
                "name": p.name, "campaign_id": existing.get("campaign_id")})
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": camp["campaign_internal_id"]},
            before={"name": old}, after={"name": p.name},
            request=_request("old", "/api/cpcSPManage/campaign/updateName.json", {
                "shopId": ctx.resolver.shop_id,
                "id": camp["campaign_internal_id"], "name": p.name,
            }, "form", retry=True),
            warnings=[],
            rollback={"available": True, "action": "rename_campaign",
                      "selector": {"campaign_id": camp["campaign_id"]},
                      "params": {"name": old}})

    def verify(self, ctx, prepared, outcome):
        camp = ctx.resolver.find_campaign(
            {"campaign_id": prepared.resolved["campaign_id"]})
        if camp.get("name") != prepared.after["name"]:
            raise VerificationFailed("活动名称读回不一致",
                                     details={"actual": camp.get("name"),
                                              "expected": prepared.after["name"]})
        return {"verified": True, "campaign": camp}


class MoveCampaignPortfolioHandler(OperationHandler):
    action = "move_campaign_portfolio"
    level = "L2"

    def prepare(self, ctx, selector, params) -> PreparedAction:
        p = _model(MovePortfolioParams, params)
        camp = _campaign(ctx, selector)
        portfolio_id = ctx.resolver.resolve_portfolio(p.portfolio)
        if camp.get("portfolio_id") == portfolio_id:
            raise AlreadyInState("活动已经在该组合中")
        return PreparedAction(
            self.action, self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_name": camp["name"],
                      "portfolio_id": portfolio_id,
                      "portfolio_name": p.portfolio},
            before={"portfolio_id": camp.get("portfolio_id")},
            after={"portfolio_id": portfolio_id, "portfolio_name": p.portfolio},
            request=_request("old", "/api/portfolio/movePortFolioData.json", {
                "shopId": ctx.resolver.shop_id,
                "portfolioId": portfolio_id,
                "campaignIds": camp["campaign_id"],
            }, "form", retry=True),
            warnings=[],
            rollback={"available": bool(camp.get("portfolio_id")
                                        and camp.get("portfolio_id") != "-1"),
                      "reason": "移回原组合" if camp.get("portfolio_id") else "原活动无组合，Amazon 不支持移出",
                      "action": "move_campaign_portfolio",
                      "selector": {"campaign_id": camp["campaign_id"]},
                      "params": {"portfolio": camp.get("portfolio_id")}})

    def verify(self, ctx, prepared, outcome):
        camp = ctx.resolver.find_campaign(
            {"campaign_id": prepared.resolved["campaign_id"]})
        if str(camp.get("portfolio_id") or "") != str(prepared.after["portfolio_id"]):
            raise VerificationFailed("活动组合归属读回不一致",
                                     details={"actual": camp.get("portfolio_id"),
                                              "expected": prepared.after["portfolio_id"]})
        return {"verified": True, "campaign": camp}
