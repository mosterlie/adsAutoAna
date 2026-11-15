# -*- coding: utf-8 -*-
"""L1/L2 广告动作离线测试。

不访问赛狐、不写真实广告；用状态化 fake resolver + transport 验证：
prepare -> execute -> readback verify。
"""
from copy import deepcopy

import pytest

from backend.ads_ops.actions import HANDLERS
from backend.ads_ops.actions.base import ActionContext
from backend.ads_ops.errors import ValidationFailed
from backend.ads_ops.actions.operations import (
    AddGroupHandler, AddNegativeAsinHandler, AddNegativeKeywordHandler,
    AddProductsToGroupHandler, ArchiveAdProductHandler, ArchiveNegativeHandler,
    MoveCampaignPortfolioHandler, RenameCampaignHandler,
    SetAdProductStateHandler, SetCampaignBudgetHandler,
    SetCampaignPlacementHandler, SetCampaignStateHandler, SetGroupBidHandler,
    SetGroupStateHandler, SetTargetBidHandler, SetTargetStateHandler,
)


class FakeResolver:
    shop_id = "711811"
    marketplace_id = "A1VC38T7YXB528"

    def __init__(self):
        self.campaign = {
            "campaign_id": "c1", "campaign_internal_id": "ci1",
            "name": "CA_测试_B0TEST", "state": "enabled",
            "daily_budget": "100.0000", "portfolio_id": "p1",
            "portfolio_name": "测试组合", "targeting_type": "auto",
            "raw": {},
        }
        self.config = {
            "id": 123, "campaignName": self.campaign["name"],
            "portfolioId": "p1", "startDate": "2026-11-14",
            "endDate": "2026-12-14", "dailyBudget": "100.00",
            "targetingType": "auto", "strategy": "legacyForSales",
            "placementTop": "10.0", "placementProductPage": "20.0",
            "placementRestOfSearch": "5.0", "placementSiteAmazonBusiness": "0",
            "campaignSite": "amazonAll", "audienceId": "",
            "audienceBidPercentage": 0, "defaultAudience": False,
        }
        self.groups = [{
            "ad_group_id": "g1", "id": "gi1", "name": "GR_测试_B0TEST",
            "state": "enabled", "defaultBid": 10.0, "adProductNum": 1,
        }]
        self.targets = [{
            "id": "ti1", "targetId": "t1", "targetText": "queryHighRelMatches",
            "targetType": "auto", "bid": "10.00", "state": "enabled",
            "adGroupId": "g1", "keywordText": "", "matchType": "",
        }]
        self.ad_products = [{
            "id": "api1", "adId": "a1", "asin": "B0TEST", "sku": "sku1",
            "state": "enabled", "adGroupId": "g1",
        }]
        self.placements = [{
            "predicate": "placementTop", "percentage": "10.0",
            "strategy": "legacyForSales",
        }]
        self.neg_keywords = [{
            "id": "nki1", "neTargetId": "ntk1", "adGroupId": "g1",
            "keywordText": "old", "state": "enabled",
        }]
        self.neg_targets = [{
            "id": "nti1", "neTargetId": "ntt1", "adGroupId": "g1",
            "targetingValue": "B0NEG", "state": "enabled",
        }]

    def clear_cache(self):
        pass

    def _campaign(self):
        return deepcopy(self.campaign)

    def find_campaign(self, ref, **kwargs):
        return self._campaign()

    def campaign_name_exists(self, name):
        return None

    def find_group(self, campaign_id, ref):
        for g in self.groups:
            if ref.get("group") and g["name"] != ref["group"]:
                continue
            if ref.get("group_id") and g["ad_group_id"] != ref["group_id"]:
                continue
            return {
                "ad_group_id": g["ad_group_id"],
                "group_internal_id": g["id"],
                "name": g["name"],
                "default_bid": g["defaultBid"],
                "state": g["state"],
                "ad_product_num": g["adProductNum"],
                "raw": dict(g),
            }
        raise AssertionError("group not found")

    def list_groups(self, campaign_id):
        return [dict(x) for x in self.groups]

    def find_target(self, campaign_id, ref):
        for t in self.targets:
            if ref.get("target_internal_id") and t["id"] != ref["target_internal_id"]:
                continue
            if ref.get("target_text") and t["targetText"] != ref["target_text"]:
                continue
            if ref.get("ad_group_id") and t["adGroupId"] != ref["ad_group_id"]:
                continue
            return {
                "target_internal_id": t["id"], "target_id": t["targetId"],
                "target_type": "auto", "target_text": t["targetText"],
                "keyword_text": t.get("keywordText", ""),
                "match_type": t.get("matchType", ""),
                "bid": t["bid"], "state": t["state"],
                "ad_group_id": t["adGroupId"], "raw": dict(t),
            }
        raise AssertionError("target not found")

    def list_targets(self, campaign_id, target_type="auto"):
        return [dict(t) for t in self.targets]

    def find_ad_product(self, campaign_id, ref):
        for p in self.ad_products:
            if ref.get("ad_product_internal_id") and p["id"] != ref["ad_product_internal_id"]:
                continue
            if ref.get("asin") and p["asin"] != ref["asin"]:
                continue
            if ref.get("ad_group_id") and p["adGroupId"] != ref["ad_group_id"]:
                continue
            return {
                "ad_product_internal_id": p["id"], "ad_id": p["adId"],
                "asin": p["asin"], "sku": p["sku"], "state": p["state"],
                "ad_group_id": p["adGroupId"], "raw": dict(p),
            }
        raise AssertionError("ad product not found")

    def list_ad_products(self, campaign_id):
        return [dict(p) for p in self.ad_products]

    def find_placement(self, campaign_id, predicate):
        for p in self.placements:
            if p["predicate"] == predicate:
                return dict(p)
        raise AssertionError("placement not found")

    def list_placements(self, campaign_id):
        return [dict(p) for p in self.placements]

    def campaign_config(self, campaign_id):
        return dict(self.config)

    def resolve_live_products(self, refs):
        return [{
            "id": 999, "asin": "B0NEW", "sku": "new-sku",
            "parent_asin": "B0NEW", "mainImage": "",
            "online_status": "Active", "title": "新品", "price": "100",
        }], []

    def resolve_portfolio(self, value):
        return "p2"

    def validate_target_asin(self, asin):
        return {"asin": asin, "imgUrl": "https://example/asin.jpg"}

    def list_negatives(self, campaign_id, ne_type, group_id=""):
        rows = self.neg_keywords if ne_type == "keyword" else self.neg_targets
        return [dict(x) for x in rows if not group_id or x["adGroupId"] == group_id]

    def find_negative(self, campaign_id, ne_type, ref):
        rows = self.neg_keywords if ne_type == "keyword" else self.neg_targets
        for x in rows:
            if ref.get("keyword_text") and x.get("keywordText") != ref["keyword_text"]:
                continue
            if ref.get("negative_internal_id") and x["id"] != ref["negative_internal_id"]:
                continue
            return {
                "negative_internal_id": x["id"],
                "ne_target_id": x["neTargetId"],
                "ad_group_id": x["adGroupId"],
                "keyword_text": x.get("keywordText", ""),
                "asin": x.get("targetingValue", ""),
                "state": x["state"], "raw": dict(x),
            }
        raise AssertionError("negative not found")


class FakeTransport:
    def __init__(self, resolver):
        self.r = resolver
        self.calls = []

    def request(self, gateway, path, payload, **kwargs):
        self.calls.append((gateway, path, payload))
        r = self.r
        if path == "spTarget/updateBid":
            next(x for x in r.targets if x["id"] == payload["id"])["bid"] = str(payload["bid"])
        elif path == "spTarget/updateState":
            next(x for x in r.targets if x["id"] == payload["id"])["state"] = payload["state"]
        elif path == "/api/cpcSPManage/group/update.json":
            next(x for x in r.groups if x["id"] == str(payload["dxmGroupId"]))["defaultBid"] = float(payload["defaultBid"])
        elif path == "/api/cpcSPManage/group/updateState.json":
            next(x for x in r.groups if x["id"] == payload["id"])["state"] = payload["state"]
        elif path == "spCampaign/updateState":
            r.campaign["state"] = payload["state"]
        elif path == "/api/cpcSPManage/campaign/updatePlacementAdjustment.json":
            next(x for x in r.placements if x["predicate"] == payload["placement"])["percentage"] = str(payload["value"])
        elif path == "spCampaign/update":
            r.campaign["daily_budget"] = f"{float(payload['dailyBudget']):.4f}"
            r.config["dailyBudget"] = str(payload["dailyBudget"])
        elif path == "/api/cpcSPManage/product/updateState.json":
            next(x for x in r.ad_products if x["id"] == payload["id"])["state"] = payload["state"]
        elif path == "negative/createSpGroupNeKeywords":
            for i, k in enumerate(payload["neKeywords"]):
                r.neg_keywords.append({
                    "id": f"nki-new-{i}", "neTargetId": f"ntk-new-{i}",
                    "adGroupId": payload["groupId"], "keywordText": k["keywordText"],
                    "state": "enabled",
                })
            return {"code": 0, "data": {"successList": payload["neKeywords"], "errMsgList": []}}
        elif path == "spTarget/createNeTargeting":
            for i, x in enumerate(payload["neTargetings"]):
                r.neg_targets.append({
                    "id": f"nti-new-{i}", "neTargetId": f"ntt-new-{i}",
                    "adGroupId": payload["groupId"], "targetingValue": x["asin"],
                    "state": "enabled",
                })
        elif path == "multiple/neTarget/updateBatchMultiShop":
            for x in payload["vos"]:
                row = next(y for y in r.neg_keywords if y["id"] == x["id"])
                row["state"] = "archived"
        elif path == "/api/cpcSPManage/product/v2/add.json":
            r.ad_products.append({
                "id": "api-new", "adId": "a-new", "asin": "B0NEW", "sku": "new-sku",
                "state": "enabled", "adGroupId": payload["groupIds"][0],
            })
            return {"code": 0, "data": ""}
        elif path == "/api/cpcSPManage/product/archive.json":
            next(x for x in r.ad_products if x["id"] == payload["id"])["state"] = "archived"
        elif path == "ad/sp/create":
            gid = "g-new"
            r.groups.append({"ad_group_id": gid, "id": "gi-new", "name": payload["spGroupInfo"]["name"],
                             "state": "enabled", "defaultBid": 10.0, "adProductNum": 1})
            for i in range(4):
                r.targets.append({"id": f"ti-new-{i}", "targetId": f"t-new-{i}",
                                  "targetText": f"query{i}", "targetType": "auto",
                                  "bid": "10.00", "state": "enabled", "adGroupId": gid,
                                  "keywordText": "", "matchType": ""})
            r.ad_products.append({"id": "api-new", "adId": "a-new", "asin": "B0NEW",
                                  "sku": "new-sku", "state": "enabled", "adGroupId": gid})
            return {"code": 0, "data": {
                "campaignResp": {"code": 0, "campaignId": "c1"},
                "groupResp": {"code": 0, "adGroupId": gid},
                "targetResp": {"code": 0, "targetList": [{"code": 0}] * 4},
            }}
        elif path == "/api/cpcSPManage/campaign/updateName.json":
            r.campaign["name"] = payload["name"]
            r.config["campaignName"] = payload["name"]
        elif path == "/api/portfolio/movePortFolioData.json":
            r.campaign["portfolio_id"] = payload["portfolioId"]
        return {"code": 0, "data": None}


@pytest.fixture
def ctx():
    r = FakeResolver()
    return ActionContext(resolver=r, transport=FakeTransport(r))


def run(handler, ctx, selector, params):
    prepared = handler.prepare(ctx, selector, params)
    outcome = handler.execute(ctx, prepared)
    verified = handler.verify(ctx, prepared, outcome)
    assert verified["verified"] is True
    return prepared


def test_set_target_bid(ctx):
    p = run(SetTargetBidHandler(), ctx,
            {"campaign_id": "c1", "target_internal_id": "ti1"}, {"bid": 12})
    assert p.before["bid"] == "10.00"
    assert float(ctx.resolver.targets[0]["bid"]) == 12


def test_set_target_bid_by_business_selector(ctx):
    """大模型只传组名和投放类型时，写请求仍必须使用内部 id。"""
    run(SetTargetBidHandler(), ctx, {
        "campaign_id": "c1",
        "group": "GR_测试_B0TEST",
        "target_type": "auto",
        "target_text": "queryHighRelMatches",
    }, {"bid": 12})
    call = next(c for c in ctx.transport.calls if c[1] == "spTarget/updateBid")
    assert call[2]["id"] == "ti1"
    assert call[2]["id"] != "t1"


def test_set_target_bid_rejects_large_change(ctx):
    with pytest.raises(ValidationFailed):
        SetTargetBidHandler().prepare(ctx, {
            "campaign_id": "c1", "target_internal_id": "ti1"
        }, {"bid": 20})


def test_every_registered_action_has_handler():
    from backend.ads_ops import registry
    actions = {a["name"] for a in registry.list_actions()}
    assert actions == set(HANDLERS)


def test_set_target_state(ctx):
    p = run(SetTargetStateHandler(), ctx,
            {"campaign_id": "c1", "target_internal_id": "ti1"}, {"state": "paused"})
    assert p.before["state"] == "enabled"
    assert ctx.resolver.targets[0]["state"] == "paused"


def test_set_group_bid(ctx):
    run(SetGroupBidHandler(), ctx, {"campaign_id": "c1", "group": "GR_测试_B0TEST"},
        {"default_bid": 11})
    assert ctx.resolver.groups[0]["defaultBid"] == 11


def test_set_group_state(ctx):
    run(SetGroupStateHandler(), ctx, {"campaign_id": "c1", "group": "GR_测试_B0TEST"},
        {"state": "paused"})
    assert ctx.resolver.groups[0]["state"] == "paused"


def test_set_campaign_state(ctx):
    run(SetCampaignStateHandler(), ctx, {"campaign_id": "c1"}, {"state": "paused"})
    assert ctx.resolver.campaign["state"] == "paused"


def test_set_campaign_placement(ctx):
    run(SetCampaignPlacementHandler(), ctx, {"campaign_id": "c1"},
        {"placement": "placementTop", "value": 20})
    assert ctx.resolver.placements[0]["percentage"] == "20"


def test_set_campaign_budget(ctx):
    run(SetCampaignBudgetHandler(), ctx, {"campaign_id": "c1"}, {"daily_budget": 120})
    assert ctx.resolver.campaign["daily_budget"] == "120.0000"


def test_set_ad_product_state(ctx):
    run(SetAdProductStateHandler(), ctx,
        {"campaign_id": "c1", "ad_product_internal_id": "api1"}, {"state": "paused"})
    assert ctx.resolver.ad_products[0]["state"] == "paused"


def test_add_negative_keyword(ctx):
    run(AddNegativeKeywordHandler(), ctx,
        {"campaign_id": "c1", "group": "GR_测试_B0TEST"},
        {"keywords": [{"keyword_text": "new", "match_type": "negativeExact"}]})
    assert any(x["keywordText"] == "new" for x in ctx.resolver.neg_keywords)


def test_add_negative_asin(ctx):
    run(AddNegativeAsinHandler(), ctx,
        {"campaign_id": "c1", "group": "GR_测试_B0TEST"},
        {"asins": ["B0NEG2"]})
    assert any(x["targetingValue"] == "B0NEG2" for x in ctx.resolver.neg_targets)


def test_archive_negative(ctx):
    run(ArchiveNegativeHandler(), ctx,
        {"campaign_id": "c1", "group": "GR_测试_B0TEST", "keyword_text": "old"},
        {"ne_type": "keyword"})
    assert ctx.resolver.neg_keywords[0]["state"] == "archived"


def test_add_products_to_group(ctx):
    run(AddProductsToGroupHandler(), ctx,
        {"campaign_id": "c1", "group": "GR_测试_B0TEST"},
        {"products": [{"asin": "B0NEW"}]})
    assert any(x["asin"] == "B0NEW" for x in ctx.resolver.ad_products)


def test_archive_ad_product(ctx):
    run(ArchiveAdProductHandler(), ctx,
        {"campaign_id": "c1", "ad_product_internal_id": "api1"}, {})
    assert ctx.resolver.ad_products[0]["state"] == "archived"


def test_add_group(ctx):
    run(AddGroupHandler(), ctx, {"campaign_id": "c1"},
        {"name": "GR_新组", "default_bid": 10,
         "products": [{"asin": "B0NEW"}], "targeting_type": "auto"})
    assert any(x["name"] == "GR_新组" for x in ctx.resolver.groups)


def test_rename_campaign(ctx):
    run(RenameCampaignHandler(), ctx, {"campaign_id": "c1"}, {"name": "CA_新名_B0TEST"})
    assert ctx.resolver.campaign["name"] == "CA_新名_B0TEST"


def test_move_campaign_portfolio(ctx):
    run(MoveCampaignPortfolioHandler(), ctx, {"campaign_id": "c1"},
        {"portfolio": "新组合"})
    assert ctx.resolver.campaign["portfolio_id"] == "p2"
