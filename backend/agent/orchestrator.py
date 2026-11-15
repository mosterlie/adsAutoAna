# -*- coding: utf-8 -*-
"""Agent 编排器：观察 → 分析 → 策略 → 等待审批。

第一版是确定性分析/策略核心，不直接写广告。
后续可把 LLM 接到 generate_plan，但执行仍复用 ads_ops。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.ads_ops.query import AdsQueryService
from backend.agent.store import AgentStore


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class AgentOrchestrator:
    def __init__(self, query: Optional[AdsQueryService] = None,
                 store: Optional[Any] = None, executor: Optional[Any] = None):
        self.query = query or AdsQueryService()
        self.store = store or AgentStore()
        self.executor = executor

    def run(self, scope: Dict[str, Any], date_from: str, date_to: str,
            objective: str = "") -> Dict[str, Any]:
        run_id = "agent_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
        run: Dict[str, Any] = {
            "run_id": run_id, "status": "observing",
            "created_at": _now(), "updated_at": _now(),
            "scope": scope or {}, "date_from": date_from, "date_to": date_to,
            "objective": objective or "降低无效花费，保留有效流量",
            "diagnosis": {}, "plan": {}, "coverage": {},
        }
        self.store.save(run)

        campaigns = self.query.query(
            "campaigns", date_from=date_from, date_to=date_to,
            filters=scope or {}, limit=1000)
        coverage = self.query.query(
            "data_coverage", date_from=date_from, date_to=date_to)
        run["coverage"] = coverage

        if not campaigns.get("rows"):
            run.update({
                "status": "data_incomplete",
                "diagnosis": {
                    "summary": "没有匹配活动或数据不完整",
                    "warnings": coverage.get("warnings") or [],
                },
                "plan": {"summary": "暂不生成策略", "actions": []},
                "updated_at": _now(),
            })
            self.store.save(run)
            return run

        run["status"] = "analyzing"
        self.store.save(run)

        campaign_rows = campaigns["rows"]
        campaign_ids = [x["campaign_id"] for x in campaign_rows if x.get("campaign_id")]
        terms = self.query.query(
            "search_terms", date_from=date_from, date_to=date_to,
            filters={"campaign_ids": campaign_ids}, limit=1000)
        term_rows = terms.get("rows") or []

        groups = self.query.query(
            "groups", date_from=date_from, date_to=date_to,
            filters={"campaign_ids": campaign_ids}, limit=1000)
        group_name = {x.get("ad_group_id"): x.get("name") for x in groups.get("rows") or []}

        totals = {
            "campaigns": len(campaign_rows),
            "impressions": sum(_f(x.get("impressions")) for x in campaign_rows),
            "clicks": sum(_f(x.get("clicks")) for x in campaign_rows),
            "cost": sum(_f(x.get("cost")) for x in campaign_rows),
            "orders": sum(_f(x.get("orders")) for x in campaign_rows),
            "sales": sum(_f(x.get("sales")) for x in campaign_rows),
        }
        totals["acos"] = (totals["cost"] / totals["sales"] * 100
                         if totals["sales"] else None)

        diagnosis = {
            "summary": (
                f"共 {totals['campaigns']} 个活动，"
                f"{int(totals['clicks'])} 次点击，"
                f"{int(totals['orders'])} 单，花费 {totals['cost']:.2f}"),
            "totals": totals,
            "warnings": coverage.get("warnings") or [],
            "campaigns_zero_order": [],
            "search_terms_zero_order": [],
        }
        actions: List[Dict[str, Any]] = []

        # 1) 活动级：有足够点击、零单、有花费 -> 建议降每日预算 30%
        for c in sorted(campaign_rows, key=lambda x: _f(x.get("cost")), reverse=True):
            clicks = _f(c.get("clicks"))
            orders = _f(c.get("orders"))
            cost = _f(c.get("cost"))
            budget = _f(c.get("daily_budget"))
            if clicks < 10 or orders > 0 or cost <= 0:
                continue
            diagnosis["campaigns_zero_order"].append({
                "campaign_id": c.get("campaign_id"),
                "campaign_name": c.get("name"),
                "parent_asin": c.get("parent_asin"),
                "clicks": clicks, "cost": cost, "orders": orders,
                "daily_budget": budget,
            })
            if budget > 0 and len(actions) < 20:
                new_budget = max(1, round(budget * 0.7, 2))
                actions.append({
                    "id": f"A{len(actions) + 1}",
                    "action": "set_campaign_budget",
                    "level": "L1",
                    "selector": {"campaign_id": c.get("campaign_id")},
                    "params": {"daily_budget": new_budget,
                               "expected_before": budget},
                    "reason": f"{int(clicks)} 次点击、0 单，花费 {cost:.2f}，先降预算观察",
                    "evidence": {"clicks": clicks, "orders": orders, "cost": cost,
                                 "current_budget": budget},
                    "requires_approval": False,
                    "expected_impact": "降低无效花费",
                })

        # 2) 搜索词级：点击 >10、零单 -> 加否定关键词
        seen_terms = set()
        for t in sorted(term_rows, key=lambda x: _f(x.get("cost")), reverse=True):
            clicks = _f(t.get("clicks"))
            orders = _f(t.get("orders"))
            term = str(t.get("query") or "").strip()
            if clicks <= 10 or orders > 0 or not term:
                continue
            key = (t.get("campaign_id"), t.get("ad_group_id"), term)
            if key in seen_terms:
                continue
            seen_terms.add(key)
            diagnosis["search_terms_zero_order"].append({
                "campaign_id": t.get("campaign_id"),
                "campaign_name": t.get("campaign_name"),
                "parent_asin": t.get("parent_asin"),
                "ad_group_id": t.get("ad_group_id"),
                "query": term,
                "clicks": clicks, "cost": _f(t.get("cost")), "orders": orders,
            })
            if len(actions) < 20:
                gname = group_name.get(t.get("ad_group_id"))
                group_ref = ({"group": gname} if gname
                             else {"group_id": t.get("ad_group_id")})
                actions.append({
                    "id": f"A{len(actions) + 1}",
                    "action": "add_negative_keyword",
                    "level": "L2",
                    "selector": {"campaign_id": t.get("campaign_id"), **group_ref},
                    "params": {"keywords": [{
                        "keyword_text": term,
                        "match_type": "negativeExact",
                    }]},
                    "reason": f"{int(clicks)} 次点击、0 单，建议否定",
                    "evidence": {"clicks": clicks, "orders": orders,
                                 "cost": _f(t.get("cost"))},
                    "requires_approval": True,
                    "expected_impact": "减少无效搜索流量",
                })

        run["diagnosis"] = diagnosis
        run["status"] = "planning"
        self.store.save(run)

        plan = {
            "summary": (
                "先处理高点击零单活动和搜索词；"
                "L2 否定词需要人工确认，L1 预算动作也先展示预览。"
            ),
            "actions": actions,
            "action_count": len(actions),
            "requires_approval": any(a["requires_approval"] for a in actions),
        }
        run["plan"] = plan
        run["status"] = "awaiting_approval" if actions else "done"
        run["updated_at"] = _now()
        self.store.save(run)
        return run

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self.store.get(run_id)

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.store.list(limit)

    def approve(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.get(run_id)
        if not run:
            return None
        if run["status"] not in ("awaiting_approval", "approved"):
            return run
        run["status"] = "approved"
        run["updated_at"] = _now()
        self.store.save(run)
        return run

    # ------------------------------------------------------------------
    # 批准后的预览/执行编排
    # ------------------------------------------------------------------
    def preview_plan(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.get(run_id)
        if not run:
            return None
        if run["status"] not in ("approved", "preview_ready"):
            return run
        from backend.ads_ops.executor import Executor
        ex = self.executor or Executor()
        previews = []
        for action in (run.get("plan") or {}).get("actions") or []:
            try:
                p = ex.preview(action["action"], action.get("selector") or {},
                               action.get("params") or {})
                previews.append({
                    "plan_action_id": action.get("id"),
                    "ok": True,
                    "preview": p,
                })
            except Exception as exc:  # noqa: BLE001
                previews.append({
                    "plan_action_id": action.get("id"),
                    "ok": False,
                    "error": str(exc)[:300],
                })
        run.setdefault("plan", {})["previews"] = previews
        run["status"] = "preview_ready" if previews else "done"
        run["updated_at"] = _now()
        self.store.save(run)
        return run

    def execute_plan(self, run_id: str) -> Optional[Dict[str, Any]]:
        run = self.get(run_id)
        if not run:
            return None
        if run["status"] != "preview_ready":
            return run
        from backend.ads_ops.executor import Executor
        ex = self.executor or Executor()
        results = []
        all_ok = True
        for item in (run.get("plan") or {}).get("previews") or []:
            if not item.get("ok"):
                all_ok = False
                results.append({"plan_action_id": item.get("plan_action_id"),
                                "ok": False, "error": item.get("error")})
                continue
            token = ((item.get("preview") or {}).get("confirm_token") or "")
            try:
                res = ex.execute(token)
                results.append({
                    "plan_action_id": item.get("plan_action_id"),
                    "ok": bool(res.get("ok")),
                    "result": res,
                })
                all_ok = all_ok and bool(res.get("ok"))
            except Exception as exc:  # noqa: BLE001
                all_ok = False
                results.append({
                    "plan_action_id": item.get("plan_action_id"),
                    "ok": False, "error": str(exc)[:300],
                })
        run.setdefault("plan", {})["execution"] = results
        run["status"] = "done" if all_ok else "partial_execution"
        run["updated_at"] = _now()
        self.store.save(run)
        return run
