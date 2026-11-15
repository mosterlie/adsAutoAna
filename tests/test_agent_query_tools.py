# -*- coding: utf-8 -*-
from backend.ads_ops.query import AdsQueryService
from backend.ads_ops.tool_manifest import build_manifest
from backend.agent.orchestrator import AgentOrchestrator
from backend.agent.store import MemoryStore


class FakeQuery:
    def __init__(self):
        self.calls = []

    def query(self, resource, date_from=None, date_to=None, filters=None,
              limit=200, offset=0):
        self.calls.append((resource, filters or {}))
        if resource == "campaigns":
            return {
                "rows": [{
                    "campaign_id": "c1", "campaign_internal_id": "ci1",
                    "name": "CA_测试_B0TEST1234", "parent_asin": "B0TEST",
                    "state": "enabled", "portfolio_name": "测试组合",
                    "daily_budget": 100, "clicks": 20, "orders": 0,
                    "cost": 500, "sales": 0, "impressions": 2000,
                }],
                "count": 1, "total": 1,
            }
        if resource == "search_terms":
            return {
                "rows": [{
                    "campaign_id": "c1", "campaign_name": "CA_测试_B0TEST1234",
                    "parent_asin": "B0TEST", "ad_group_id": "g1",
                    "ad_group_name": "GR_测试_B0TEST1234", "query": "waste keyword",
                    "clicks": 15, "orders": 0, "cost": 300,
                }],
                "count": 1, "total": 1,
            }
        if resource == "groups":
            return {"rows": [{"ad_group_id": "g1", "name": "GR_测试_B0TEST1234"}],
                    "count": 1, "total": 1}
        if resource == "data_coverage":
            return {"warnings": ["搜索词只覆盖 2 个活动"], "search_campaign_count": 2}
        raise AssertionError(resource)


def test_tool_manifest_has_selector_schema():
    manifest = build_manifest()
    assert len(manifest["tools"]) == 4
    names = {x["name"] for x in manifest["tools"]}
    assert names == {"ads_action_catalog", "ads_query", "ads_preview", "ads_execute"}
    target = next(x for x in manifest["actions"] if x["name"] == "set_target_bid")
    assert target["selector_schema"]["properties"]["target_type"]["enum"] == [
        "auto", "keyword", "targeting"]
    assert "target_text" in target["selector_schema"]["properties"]
    assert "target_internal_id" not in target["selector_schema"]["properties"]


def test_query_normalizes_campaign_and_search():
    class T:
        def request(self, gateway, path, payload, **kwargs):
            if path == "campaign/getAllCampaignData":
                return {"data": {"page": {"rows": [{
                    "campaignId": "c1", "id": 11, "name": "CA_测试_B0TEST1234",
                    "state": "enabled", "dailyBudget": "100",
                    "impressions": "100", "clicks": "10", "adCost": "50",
                    "adOrderNum": "1", "adSales": "100",
                }], "totalPage": 1}}}
            if path == "multiple/search/getAllSearchData":
                return {"data": {"page": {"rows": [{
                    "campaignId": "c1", "campaignName": "CA_测试_B0TEST1234",
                    "adGroupId": "g1", "adGroupName": "GR_测试_B0TEST1234",
                    "query": "word", "queryType": "spQuery",
                    "matchType": "close_match", "impressions": "20",
                    "clicks": "12", "adCost": "30", "adOrderNum": "0",
                    "adSales": "0",
                }], "totalPage": 1}}}
            raise AssertionError(path)

    q = AdsQueryService(T())
    campaigns = q.query("campaigns", "2026-11-01", "2026-11-07")
    assert campaigns["rows"][0]["parent_asin"] == "B0TEST1234"
    assert campaigns["rows"][0]["ctr"] == 10.0
    terms = q.query("search_terms", "2026-11-01", "2026-11-07",
                    {"campaign_id": "c1"})
    assert terms["rows"][0]["query"] == "word"
    assert terms["rows"][0]["clicks"] == 12


def test_agent_run_generates_diagnosis_and_plan():
    agent = AgentOrchestrator(query=FakeQuery(), store=MemoryStore())
    run = agent.run({"portfolio": "测试组合"}, "2026-11-01", "2026-11-07",
                    "降低无效花费")
    assert run["status"] == "awaiting_approval"
    assert run["diagnosis"]["campaigns_zero_order"][0]["campaign_id"] == "c1"
    actions = run["plan"]["actions"]
    assert any(a["action"] == "set_campaign_budget" for a in actions)
    assert any(a["action"] == "add_negative_keyword" for a in actions)
    assert run["plan"]["requires_approval"] is True

    approved = agent.approve(run["run_id"])
    assert approved["status"] == "approved"


def test_agent_preview_and_execute_plan():
    class FakeExecutor:
        def __init__(self):
            self.preview_calls = []
            self.execute_calls = []

        def preview(self, action, selector, params):
            self.preview_calls.append(action)
            return {"ok": True, "confirm_token": f"token-{action}",
                    "before": {"x": 1}, "after": {"x": 2}}

        def execute(self, token):
            self.execute_calls.append(token)
            return {"ok": True, "status": "applied", "verified": True}

    ex = FakeExecutor()
    agent = AgentOrchestrator(query=FakeQuery(), store=MemoryStore(), executor=ex)
    run = agent.run({"portfolio": "测试组合"}, "2026-11-01", "2026-11-07")
    agent.approve(run["run_id"])
    previewed = agent.preview_plan(run["run_id"])
    assert previewed["status"] == "preview_ready"
    assert len(ex.preview_calls) == 2
    executed = agent.execute_plan(run["run_id"])
    assert executed["status"] == "done"
    assert len(ex.execute_calls) == 2


def test_agent_run_data_incomplete():
    class EmptyQuery(FakeQuery):
        def query(self, resource, **kwargs):
            if resource == "campaigns":
                return {"rows": [], "count": 0, "total": 0}
            if resource == "data_coverage":
                return {"warnings": ["无数据"], "search_campaign_count": 0}
            raise AssertionError(resource)

    agent = AgentOrchestrator(query=EmptyQuery(), store=MemoryStore())
    run = agent.run({}, "2026-11-01", "2026-11-07")
    assert run["status"] == "data_incomplete"
    assert run["plan"]["actions"] == []
