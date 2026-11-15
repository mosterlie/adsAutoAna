# -*- coding: utf-8 -*-
"""L3 活动动作：新建 SP 活动、归档活动。"""
from __future__ import annotations

import copy
import math
import time
from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from backend.ads_ops.actions.base import (ActionContext, ActionHandler,
                                          ExecutionOutcome, PreparedAction)
from backend.ads_ops.errors import (AlreadyInState, NameConflict, SellerAPIError,
                                    ValidationFailed, VerificationFailed)
from backend.ads_ops.schema import CampaignCreateParams, CampaignArchiveParams

AUTO_KEYS = (
    ("queryHighRelMatches", "queryHighRelMatchesState", "queryHighRelMatchesBid"),
    ("queryBroadRelMatches", "queryBroadRelMatchesState", "queryBroadRelMatchesBid"),
    ("asinSubstituteRelated", "asinSubstituteRelatedState", "asinSubstituteRelatedBid"),
    ("asinAccessoryRelated", "asinAccessoryRelatedState", "asinAccessoryRelatedBid"),
)


def _num_str(value: Any) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValidationFailed(f"不是有效数字: {value!r}")
    if not math.isfinite(f):
        raise ValidationFailed(f"不是有效数字: {value!r}")
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _date_str(value: str, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise ValidationFailed(f"{field} 必须是 YYYY-MM-DD", details={field: raw}) from exc


def _pct(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if not 0 <= float(value) <= 900:
        raise ValidationFailed("广告位加价必须在 0～900 之间")
    return _num_str(value)


def _child_ok(resp: Any, required: bool = False) -> bool:
    if not isinstance(resp, dict) or not resp:
        return not required
    return int(resp.get("code") or 0) == 0


def _target_rows_ok(target_resp: Dict[str, Any], expected: int) -> bool:
    if not _child_ok(target_resp, required=True):
        return False
    rows = target_resp.get("targetList") or []
    ok_rows = [r for r in rows if int(r.get("code") or 0) == 0]
    return len(ok_rows) >= expected


class CreateCampaignHandler(ActionHandler):
    action = "create_campaign"
    level = "L3"

    def prepare(self, ctx: ActionContext, selector: Dict[str, Any],
                params: Dict[str, Any]) -> PreparedAction:
        try:
            p = CampaignCreateParams.model_validate(params or {})
        except ValidationError as exc:
            raise ValidationFailed("创建活动参数不合法", details=exc.errors()) from exc
        warnings = self._validate(p)

        existing = ctx.resolver.campaign_name_exists(p.name)
        if existing:
            raise NameConflict(
                "活动名称已存在，赛狐不允许重名",
                details={"name": p.name, "existing": {
                    "campaign_id": existing["campaign_id"],
                    "campaign_internal_id": existing["campaign_internal_id"],
                    "state": existing["state"]}})

        if p.groups:
            return self._prepare_multi(ctx, p, warnings)

        if not p.group_name or not p.products:
            raise ValidationFailed("单广告组模式需要 group_name 和 products")
        products, product_warnings = ctx.resolver.resolve_live_products(p.products)
        warnings.extend(product_warnings)
        portfolio_id = ctx.resolver.resolve_portfolio(p.portfolio)
        payload = self._build_payload(p, products, portfolio_id, ctx)
        resolved = {
            "shop_id": ctx.resolver.shop_id,
            "marketplace_id": ctx.resolver.marketplace_id,
            "campaign_name": p.name,
            "group_name": p.group_name,
            "targeting_type": p.targeting_type,
            "portfolio_id": portfolio_id,
            "products": products,
        }
        after = {
            "campaign": {
                "name": p.name,
                "start_date": p.start_date,
                "end_date": p.end_date,
                "daily_budget": p.daily_budget,
                "targeting_type": p.targeting_type,
                "strategy": p.strategy,
                "portfolio_id": portfolio_id or None,
            },
            "group": {"name": p.group_name, "default_bid": p.default_bid,
                      "product_count": len(products)},
            "targeting": {
                "mode": ("auto" if p.targeting_type == "auto"
                         else ("keyword" if p.keywords else "product")),
                "keyword_count": len(p.keywords),
                "targeting_count": len(p.targetings),
            },
        }
        return PreparedAction(
            action=self.action, level=self.level, resolved=resolved,
            before=None, after=after, request=payload, warnings=warnings,
            rollback={"available": False,
                      "reason": "L3 新建活动不可自动回滚；如需撤销只能人工归档"})

    def _validate(self, p: CampaignCreateParams) -> List[str]:
        warnings: List[str] = []
        _date_str(p.start_date, "start_date")
        if p.end_date:
            _date_str(p.end_date, "end_date")
        if not p.end_date:
            warnings.append("未设置结束日期，活动将长期有效")
        if not p.name.startswith("CA_"):
            warnings.append("活动名未使用 CA_ 前缀，可能不符合项目命名规范")
        if p.daily_budget < 1:
            raise ValidationFailed("daily_budget 至少为 1")
        if abs(p.daily_budget - round(p.daily_budget)) > 1e-9:
            warnings.append("日本站每日预算通常为整数，提交值可能被系统取整")
        for v in (p.placement_top, p.placement_rest, p.placement_product,
                  p.placement_business):
            _pct(v)

        if p.groups:
            if p.group_name or p.products or p.keywords or p.targetings or p.auto_bids:
                raise ValidationFailed("groups 与单组字段不能同时使用")
            if len(p.groups) < 2:
                raise ValidationFailed("多广告组模式至少需要 2 个组")
            names = set()
            for g in p.groups:
                if g.name in names:
                    raise ValidationFailed("广告组名称不能重复", details={"name": g.name})
                names.add(g.name)
                if not g.name.startswith("GR_"):
                    warnings.append(f"{g.name} 未使用 GR_ 前缀")
                self._validate_group(p, g)
            return warnings

        if not p.group_name or not p.products:
            raise ValidationFailed("单组模式需要 group_name 和 products")
        if not p.group_name.startswith("GR_"):
            warnings.append("广告组名未使用 GR_ 前缀，可能不符合项目命名规范")
        if p.default_bid > 1000:
            raise ValidationFailed("default_bid 不能超过 1000")
        if p.targeting_type == "auto":
            if p.keywords or p.targetings:
                raise ValidationFailed("自动投放不能同时传 keywords/targetings")
            for key, _, _ in AUTO_KEYS:
                if key in p.auto_bids:
                    self._validate_bid(p.auto_bids[key], f"auto_bids.{key}")
        else:
            self._validate_targeting(p.targeting_type, p.keywords, p.targetings,
                                     p.auto_bids, p.default_bid)
        if len(p.negative_asins) > 100:
            warnings.append("否定 ASIN 过多，建议拆分提交")
        return warnings

    def _validate_group(self, p: CampaignCreateParams, g) -> None:
        if not 1 <= float(g.default_bid) <= 1000:
            raise ValidationFailed(f"{g.name} default_bid 必须在 1～1000")
        if not g.products:
            raise ValidationFailed(f"{g.name} 至少需要一个商品")
        self._validate_targeting(p.targeting_type, g.keywords, g.targetings,
                                 g.auto_bids, g.default_bid)

    def _validate_targeting(self, targeting_type: str, keywords, targetings,
                            auto_bids, default_bid: float) -> None:
        if targeting_type == "auto":
            if keywords or targetings:
                raise ValidationFailed("自动投放组不能传 keywords/targetings")
            for key, _, _ in AUTO_KEYS:
                if key in (auto_bids or {}):
                    self._validate_bid(auto_bids[key], f"auto_bids.{key}")
            return
        if bool(keywords) == bool(targetings):
            raise ValidationFailed(
                "手动投放必须且只能提供 keywords 或 targetings 其中一种")
        for kw in keywords:
            if not kw.keyword_text.strip():
                raise ValidationFailed("关键词不能为空")
            if kw.bid is not None:
                self._validate_bid(kw.bid, f"keyword:{kw.keyword_text}")
        for t in targetings:
            if t.type == "asin" and not t.asin.strip():
                raise ValidationFailed("商品定向 type=asin 时必须提供 asin")
            if t.bid is not None:
                self._validate_bid(t.bid, f"target:{t.asin or t.category}")

    @staticmethod
    def _validate_bid(value: float, field: str) -> None:
        try:
            v = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationFailed(f"{field} 不是有效竞价") from exc
        if not 1 <= v <= 1000:
            raise ValidationFailed(f"{field} 竞价必须在 1～1000 之间")

    def _build_payload(self, p: CampaignCreateParams,
                       products: List[Dict[str, Any]], portfolio_id: str,
                       ctx: ActionContext) -> Dict[str, Any]:
        """按 docs/sellfox_sp_create_api.md §2 / §21.2 组装三段请求体。"""
        campaign = {
            "campaignId": "",
            "startDateStr": p.start_date,
            "endDateStr": p.end_date,
            "dailyBudget": _num_str(p.daily_budget),
            "targetingType": p.targeting_type,
            "strategy": p.strategy,
            "placementTop": _pct(p.placement_top),
            "placementRestOfSearch": _pct(p.placement_rest),
            "placementProductPage": _pct(p.placement_product),
            "placementSiteAmazonBusiness": _pct(p.placement_business),
            "creativeBidPercentage": None,
            "campaignSite": p.campaign_site,
            "name": p.name,
            "portfolioId": portfolio_id,
            "audienceId": p.audience_id,
            "audienceBidPercentage": p.audience_bid_percentage,
            "defaultAudience": bool(p.default_audience),
        }
        group_products = []
        for item in products:
            group_products.append({
                "id": int(item["id"]),
                "sku": item.get("sku") or "",
                "asin": item.get("asin") or "",
                "mainImage": item.get("mainImage") or "",
                "spotlightVideos": None,
            })
        group = {
            "groupId": "",
            "defaultBid": _num_str(p.default_bid),
            "name": p.group_name,
            "products": group_products,
        }

        target: Dict[str, Any] = {
            "autoTarget": p.targeting_type == "auto",
            "keywordTarget": bool(p.targeting_type == "manual" and p.keywords),
            "keywords": [],
            "targetings": [],
            "neKeywords": [],
            "neTargetings": [],
        }
        if p.targeting_type == "auto":
            for key, state_key, bid_key in AUTO_KEYS:
                target[state_key] = True
                target[bid_key] = _num_str(p.auto_bids.get(key, p.default_bid))
        elif p.keywords:
            for kw in p.keywords:
                row = {
                    "keywordText": kw.keyword_text.strip(),
                    "matchType": kw.match_type,
                    "bid": _num_str(kw.bid if kw.bid is not None else p.default_bid),
                    "isAdd": True,
                }
                if kw.keyword_text_cn:
                    row["keywordTextCn"] = kw.keyword_text_cn
                target["keywords"].append(row)
        else:
            for t in p.targetings:
                row: Dict[str, Any] = {"type": t.type, "isAdd": True}
                for key, value in (("asin", t.asin), ("category", t.category),
                                   ("categoryId", t.category_id),
                                   ("keywordText", t.keyword_text),
                                   ("matchType", t.match_type)):
                    if value:
                        row[key] = value
                if t.bid is not None:
                    row["bid"] = _num_str(t.bid)
                target["targetings"].append(row)

        for i, nk in enumerate(p.negative_keywords):
            target["neKeywords"].append({
                "keywordText": nk.keyword_text.strip(),
                "matchType": nk.match_type,
                "index": i,
                "campaignId": "",
            })
        for i, asin in enumerate(p.negative_asins):
            target["neTargetings"].append({
                "asin": str(asin).strip(),
                "title": "",
                "imgUrl": "",
                "type": "asin",
                "index": i,
                "campaignId": "",
                "groupId": "",
            })
        return {
            "shopId": ctx.resolver.shop_id,
            "marketplaceId": ctx.resolver.marketplace_id,
            "spCampaignInfo": campaign,
            "spGroupInfo": group,
            "spTargetInfo": target,
        }

    def _prepare_multi(self, ctx: ActionContext, p: CampaignCreateParams,
                       warnings: List[str]) -> PreparedAction:
        portfolio_id = ctx.resolver.resolve_portfolio(p.portfolio)
        groups_resolved: List[Dict[str, Any]] = []
        group_payloads: List[Dict[str, Any]] = []
        for g in p.groups:
            products, _ = ctx.resolver.resolve_live_products(g.products)
            target = self._build_group_target(p, g)
            group_info = {
                "groupId": "",
                "defaultBid": _num_str(g.default_bid),
                "name": g.name,
                "products": [{
                    "id": int(x["id"]), "sku": x.get("sku") or "",
                    "asin": x.get("asin") or "",
                    "mainImage": x.get("mainImage") or "",
                    "spotlightVideos": None,
                } for x in products],
            }
            groups_resolved.append({
                "name": g.name, "default_bid": g.default_bid,
                "products": products,
                "targeting_mode": ("auto" if p.targeting_type == "auto"
                                   else ("keyword" if g.keywords else "product")),
                "keyword_count": len(g.keywords),
                "targeting_count": len(g.targetings),
            })
            group_payloads.append({"group": group_info, "target": target})

        steps = []
        for i, gp in enumerate(group_payloads):
            full = i == 0
            campaign_info = {
                "campaignId": "" if full else "__CAMPAIGN_ID__",
                "startDateStr": p.start_date if full else "",
                "endDateStr": p.end_date if full else "",
                "dailyBudget": _num_str(p.daily_budget),
                "targetingType": p.targeting_type,
                "strategy": p.strategy,
                "placementTop": _pct(p.placement_top) if full else None,
                "placementRestOfSearch": _pct(p.placement_rest) if full else None,
                "placementProductPage": _pct(p.placement_product) if full else None,
                "placementSiteAmazonBusiness": _pct(p.placement_business) if full else None,
                "creativeBidPercentage": None,
                "campaignSite": p.campaign_site,
                "name": p.name if full else "",
                "portfolioId": portfolio_id if full else "",
                "audienceId": p.audience_id,
                "audienceBidPercentage": p.audience_bid_percentage,
                "defaultAudience": bool(p.default_audience),
            }
            steps.append({
                "shopId": ctx.resolver.shop_id,
                "marketplaceId": ctx.resolver.marketplace_id,
                "spCampaignInfo": campaign_info,
                "spGroupInfo": gp["group"],
                "spTargetInfo": gp["target"],
            })

        after_groups = [{
            "name": g["name"], "default_bid": g["default_bid"],
            "product_count": len(g["products"]),
            "targeting_mode": g["targeting_mode"],
            "keyword_count": g["keyword_count"],
            "targeting_count": g["targeting_count"],
        } for g in groups_resolved]
        return PreparedAction(
            action=self.action, level=self.level,
            resolved={"shop_id": ctx.resolver.shop_id,
                      "marketplace_id": ctx.resolver.marketplace_id,
                      "campaign_name": p.name,
                      "targeting_type": p.targeting_type,
                      "portfolio_id": portfolio_id,
                      "groups": groups_resolved},
            before=None,
            after={"campaign": {"name": p.name, "start_date": p.start_date,
                                "end_date": p.end_date,
                                "daily_budget": p.daily_budget,
                                "targeting_type": p.targeting_type,
                                "strategy": p.strategy,
                                "portfolio_id": portfolio_id or None},
                   "groups": after_groups},
            request={"steps": steps},
            warnings=warnings,
            rollback={"available": False,
                      "reason": "L3 新建活动不可自动回滚；如需撤销只能人工归档"})

    def _build_group_target(self, p: CampaignCreateParams, g) -> Dict[str, Any]:
        target: Dict[str, Any] = {
            "autoTarget": p.targeting_type == "auto",
            "keywordTarget": bool(p.targeting_type == "manual" and g.keywords),
            "keywords": [], "targetings": [], "neKeywords": [], "neTargetings": [],
        }
        if p.targeting_type == "auto":
            for key, state_key, bid_key in AUTO_KEYS:
                target[state_key] = True
                target[bid_key] = _num_str(g.auto_bids.get(key, g.default_bid))
        elif g.keywords:
            for kw in g.keywords:
                row = {"keywordText": kw.keyword_text.strip(),
                       "matchType": kw.match_type,
                       "bid": _num_str(kw.bid if kw.bid is not None else g.default_bid),
                       "isAdd": True}
                if kw.keyword_text_cn:
                    row["keywordTextCn"] = kw.keyword_text_cn
                target["keywords"].append(row)
        else:
            for t in g.targetings:
                row: Dict[str, Any] = {"type": t.type, "isAdd": True}
                for key, value in (("asin", t.asin), ("category", t.category),
                                   ("categoryId", t.category_id),
                                   ("keywordText", t.keyword_text),
                                   ("matchType", t.match_type)):
                    if value:
                        row[key] = value
                if t.bid is not None:
                    row["bid"] = _num_str(t.bid)
                target["targetings"].append(row)
        for i, nk in enumerate(g.negative_keywords):
            target["neKeywords"].append({"keywordText": nk.keyword_text.strip(),
                                         "matchType": nk.match_type,
                                         "index": i, "campaignId": ""})
        for i, asin in enumerate(g.negative_asins):
            target["neTargetings"].append({"asin": str(asin).strip(), "title": "",
                                           "imgUrl": "", "type": "asin", "index": i,
                                           "campaignId": "", "groupId": ""})
        return target

    def recheck(self, ctx: ActionContext, prepared: PreparedAction) -> None:
        ctx.resolver.clear_cache()
        existing = ctx.resolver.campaign_name_exists(
            prepared.resolved.get("campaign_name") or "")
        if existing:
            raise NameConflict("执行前查重发现活动名已存在，已中止",
                               details={"name": prepared.resolved.get("campaign_name"),
                                        "existing_campaign_id": existing.get("campaign_id")})

    def execute(self, ctx: ActionContext, prepared: PreparedAction) -> ExecutionOutcome:
        if "steps" in prepared.request:
            return self._execute_multi(ctx, prepared)
        # 创建是非幂等动作：禁止传输层自动重试
        resp = ctx.transport.request("gw", "ad/sp/create", prepared.request,
                                     encoding="json", retry=False)
        data = resp.get("data") or {}
        camp = data.get("campaignResp") or {}
        group = data.get("groupResp") or {}
        target = data.get("targetResp") or {}
        if int(camp.get("code") or 0) != 0 or not camp.get("campaignId"):
            raise SellerAPIError(
                "创建活动子响应失败",
                details={"campaignResp": camp, "groupResp": group,
                         "targetResp": target})
        created = {
            "campaign_id": str(camp.get("campaignId") or ""),
            "ad_group_id": str(group.get("adGroupId") or ""),
            "target_type": target.get("targetType") or "",
        }
        warnings = []
        targeting_meta = prepared.after.get("targeting") or {}
        expected_targets = (4 if prepared.resolved.get("targeting_type") == "auto"
                            else int(targeting_meta.get("keyword_count")
                                     or targeting_meta.get("targeting_count") or 0))
        group_ok = _child_ok(group, required=True)
        target_ok = _target_rows_ok(target, expected_targets)
        required_ok = group_ok and target_ok
        if not group_ok:
            warnings.append("活动已创建，但广告组创建失败；可能留下空活动，需人工处理")
        if not target_ok:
            warnings.append(
                f"投放创建不完整（期望 {expected_targets} 条），需人工检查 targetList")
        for name, key in (("否定关键词", "neKeywordResp"), ("否定商品", "neTargetResp")):
            child = data.get(key)
            if isinstance(child, dict) and int(child.get("code") or 0) != 0:
                warnings.append(f"{name}创建失败，可稍后单独补加")
        return ExecutionOutcome(
            status="applied" if required_ok else "partial_success",
            response=resp, created=created, warnings=warnings)

    def _execute_multi(self, ctx: ActionContext,
                       prepared: PreparedAction) -> ExecutionOutcome:
        steps = prepared.request.get("steps") or []
        expected_groups = prepared.after.get("groups") or []
        responses: List[Dict[str, Any]] = []
        groups: List[Dict[str, Any]] = []
        warnings: List[str] = []
        campaign_id = ""
        all_ok = True
        for i, raw in enumerate(steps):
            payload = copy.deepcopy(raw)
            if i > 0:
                if not campaign_id:
                    warnings.append("活动尚未创建成功，后续广告组未提交")
                    all_ok = False
                    break
                payload["spCampaignInfo"]["campaignId"] = campaign_id
            try:
                resp = ctx.transport.request("gw", "ad/sp/create", payload,
                                             encoding="json", retry=False)
            except Exception as exc:  # noqa: BLE001
                if i == 0:
                    raise
                warnings.append(f"第 {i + 1} 个广告组请求失败: {str(exc)[:160]}")
                all_ok = False
                break
            responses.append(resp)
            data = resp.get("data") or {}
            camp_resp = data.get("campaignResp") or {}
            group_resp = data.get("groupResp") or {}
            target_resp = data.get("targetResp") or {}
            if i == 0:
                if int(camp_resp.get("code") or 0) != 0 or not camp_resp.get("campaignId"):
                    raise SellerAPIError(
                        "创建活动子响应失败",
                        details={"campaignResp": camp_resp, "groupResp": group_resp,
                                 "targetResp": target_resp})
                campaign_id = str(camp_resp.get("campaignId") or "")
            meta = expected_groups[i] if i < len(expected_groups) else {}
            expected_targets = (4 if prepared.resolved.get("targeting_type") == "auto"
                                else int(meta.get("keyword_count")
                                         or meta.get("targeting_count") or 0))
            group_ok = _child_ok(group_resp, required=True)
            target_ok = _target_rows_ok(target_resp, expected_targets)
            if not group_ok:
                warnings.append(f"第 {i + 1} 个广告组创建失败")
                all_ok = False
            if not target_ok:
                warnings.append(f"第 {i + 1} 个广告组投放不完整")
                all_ok = False
            groups.append({
                "name": meta.get("name") or payload["spGroupInfo"]["name"],
                "ad_group_id": str(group_resp.get("adGroupId") or ""),
                "target_count_expected": expected_targets,
                "target_count_returned": len(target_resp.get("targetList") or []),
                "ok": bool(group_ok and target_ok),
            })
            if i + 1 < len(steps):
                time.sleep(2)
        if len(groups) != len(steps):
            all_ok = False
        return ExecutionOutcome(
            status="applied" if all_ok else "partial_success",
            response={"steps": responses},
            created={"campaign_id": campaign_id, "groups": groups,
                     "target_type": "autoTarget"
                     if prepared.resolved.get("targeting_type") == "auto" else "multi"},
            warnings=warnings)

    def verify(self, ctx: ActionContext, prepared: PreparedAction,
               outcome: ExecutionOutcome) -> Dict[str, Any]:
        if prepared.resolved.get("groups"):
            return self._verify_multi(ctx, prepared, outcome)
        cid = str(outcome.created.get("campaign_id") or "")
        if not cid:
            raise VerificationFailed("创建响应没有 campaignId，无法读回校验")
        ctx.resolver.clear_cache()
        camp = ctx.resolver.find_campaign({"campaign_id": cid}, include_archived=True)
        groups = ctx.resolver.list_groups(cid)
        expected_group = str(prepared.resolved.get("group_name") or "")
        group = next((g for g in groups if str(g.get("name") or "") == expected_group), None)
        target_type = ("auto" if prepared.resolved.get("targeting_type") == "auto"
                       else ("keyword" if prepared.after.get("targeting", {}).get("mode") == "keyword"
                             else "targeting"))
        targets = ctx.resolver.list_targets(cid, target_type) if target_type else []
        ad_products = ctx.resolver.list_ad_products(cid)
        actual_asins = {str(x.get("asin") or "") for x in ad_products if x.get("asin")}
        expected_asins = {str(x.get("asin") or "") for x in
                          (prepared.resolved.get("products") or []) if x.get("asin")}
        missing_asins = sorted(expected_asins - actual_asins)
        checks = {
            "campaign_found": bool(camp.get("campaign_id")),
            "campaign_name": camp.get("name") == prepared.resolved.get("campaign_name"),
            "group_found": bool(group),
            "group_name": bool(group) and str(group.get("name") or "") == expected_group,
            "ad_products_found": bool(ad_products),
            "ad_products_match": not missing_asins,
            "target_count": len(targets),
        }
        verified = (all(v for k, v in checks.items() if k != "target_count")
                    and len(targets) > 0)
        return {
            "verified": bool(verified),
            "checks": checks,
            "campaign": {k: camp.get(k) for k in
                         ("campaign_id", "campaign_internal_id", "name", "state",
                          "daily_budget", "targeting_type")},
            "group": ({"ad_group_id": str(group.get("adGroupId") or ""),
                       "name": group.get("name"),
                       "default_bid": group.get("defaultBid")} if group else None),
            "ad_product_count": len(ad_products),
            "ad_products": [{"asin": x.get("asin"), "sku": x.get("sku"),
                             "state": x.get("state")} for x in ad_products[:20]],
            "missing_ad_product_asins": missing_asins,
            "target_count": len(targets),
        }

    def _verify_multi(self, ctx: ActionContext, prepared: PreparedAction,
                      outcome: ExecutionOutcome) -> Dict[str, Any]:
        cid = str(outcome.created.get("campaign_id") or "")
        if not cid:
            raise VerificationFailed("多组创建响应没有 campaignId，无法读回校验")
        ctx.resolver.clear_cache()
        camp = ctx.resolver.find_campaign({"campaign_id": cid}, include_archived=True)
        groups = ctx.resolver.list_groups(cid)
        ad_products = ctx.resolver.list_ad_products(cid)
        target_cache: Dict[str, List[Dict[str, Any]]] = {}
        group_map = {str(g.get("name") or ""): g for g in groups}
        checks: Dict[str, Any] = {}
        group_results = []
        all_ok = camp.get("name") == prepared.resolved.get("campaign_name")
        for meta in prepared.resolved.get("groups") or []:
            name = str(meta.get("name") or "")
            g = group_map.get(name)
            gid = str(g.get("adGroupId") or "") if g else ""
            group_aps = [x for x in ad_products
                         if str(x.get("adGroupId") or "") == gid]
            actual_asins = {str(x.get("asin") or "") for x in group_aps}
            expected_asins = {str(x.get("asin") or "") for x in meta.get("products") or []}
            missing = sorted(expected_asins - actual_asins)
            mode = str(meta.get("targeting_mode") or "keyword")
            if mode not in target_cache:
                target_cache[mode] = (ctx.resolver.list_targets(cid, mode)
                                      if mode in ("keyword", "targeting", "auto") else [])
            expected_targets = (4 if prepared.resolved.get("targeting_type") == "auto"
                                else int(meta.get("keyword_count")
                                         or meta.get("targeting_count") or 0))
            group_targets = [x for x in target_cache[mode]
                             if str(x.get("adGroupId") or "") == gid]
            ok = bool(g) and bool(group_aps) and not missing and len(group_targets) >= expected_targets
            all_ok = all_ok and ok
            checks[name] = {
                "group_found": bool(g),
                "ad_product_count": len(group_aps),
                "missing_ad_product_asins": missing,
                "target_count": len(group_targets),
                "target_count_expected": expected_targets,
                "ok": ok,
            }
            group_results.append({
                "name": name,
                "ad_group_id": gid,
                "default_bid": g.get("defaultBid") if g else None,
                "ad_product_count": len(group_aps),
                "target_count": len(group_targets),
            })
        return {
            "verified": bool(all_ok),
            "checks": checks,
            "campaign": {k: camp.get(k) for k in
                         ("campaign_id", "campaign_internal_id", "name", "state",
                          "daily_budget", "targeting_type")},
            "groups": group_results,
            "ad_product_count": len(ad_products),
        }

    def rollback_plan(self, ctx: ActionContext, prepared: PreparedAction,
                      outcome: ExecutionOutcome) -> Dict[str, Any]:
        cid = outcome.created.get("campaign_id") if outcome else ""
        return {
            "available": False,
            "reason": "L3 新建活动不可自动回滚",
            "possible_action": "archive_campaign",
            "selector": ({"campaign_id": cid} if cid else None),
            "note": "如确认不该创建，必须人工确认后调用 archive_campaign",
        }


class ArchiveCampaignHandler(ActionHandler):
    action = "archive_campaign"
    level = "L3"

    def prepare(self, ctx: ActionContext, selector: Dict[str, Any],
                params: Dict[str, Any]) -> PreparedAction:
        # 参数模型目前为空；保留校验入口，防止未来字段静默丢失
        try:
            CampaignArchiveParams.model_validate(params or {})
        except ValidationError as exc:
            raise ValidationFailed("归档参数不合法", details=exc.errors()) from exc
        camp = ctx.resolver.find_campaign(selector, include_archived=True)
        if camp.get("state") == "archived":
            raise AlreadyInState("活动已经是归档状态", details={
                "campaign_id": camp.get("campaign_id"),
                "campaign_internal_id": camp.get("campaign_internal_id")})
        request = {"shopId": ctx.resolver.shop_id,
                   "id": camp.get("campaign_internal_id")}
        return PreparedAction(
            action=self.action, level=self.level,
            resolved={"campaign_id": camp["campaign_id"],
                      "campaign_internal_id": camp["campaign_internal_id"],
                      "campaign_name": camp["name"]},
            before={"state": camp.get("state"), "name": camp.get("name")},
            after={"state": "archived"},
            request=request,
            warnings=[],
            rollback={"available": False, "reason": "归档不可逆，只能重建活动"})

    def execute(self, ctx: ActionContext, prepared: PreparedAction) -> ExecutionOutcome:
        resp = ctx.transport.request("gw", "spCampaign/archive", prepared.request,
                                     encoding="form", retry=True)
        return ExecutionOutcome(status="applied", response=resp,
                                created={"campaign_id": prepared.resolved["campaign_id"]})

    def verify(self, ctx: ActionContext, prepared: PreparedAction,
               outcome: ExecutionOutcome) -> Dict[str, Any]:
        cid = prepared.resolved["campaign_id"]
        ctx.resolver.clear_cache()
        camp = ctx.resolver.find_campaign({"campaign_id": cid}, include_archived=True)
        verified = str(camp.get("state") or "") == "archived"
        if not verified:
            raise VerificationFailed(
                "归档接口已返回，但读回状态仍不是 archived",
                details={"campaign_id": cid, "state": camp.get("state")})
        return {
            "verified": True,
            "checks": {"campaign_found": True, "state_archived": True},
            "campaign": {k: camp.get(k) for k in
                         ("campaign_id", "campaign_internal_id", "name", "state")},
        }

    def rollback_plan(self, ctx: ActionContext, prepared: PreparedAction,
                      outcome: ExecutionOutcome) -> Dict[str, Any]:
        return {"available": False, "reason": "赛狐归档不可逆，只能新建活动"}
