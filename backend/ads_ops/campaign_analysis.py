# -*- coding: utf-8 -*-
"""广告活动级精细分析。

数据全部来自本地采集库 ads.db：
  fact_campaign / fact_group / fact_target / fact_product / fact_search_term
  dim_campaign / dim_group / dim_target / dim_product

输出：
  汇总指标、日趋势、广告组、投放、产品、广告位、分时、搜索词、
  诊断问题、可执行策略动作。
"""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Any, Dict, List, Optional

from backend import database as db
from backend.ads_ops.errors import NotFound, ValidationFailed

_PARENT_RE = re.compile(r"(B0[A-Z0-9]{8})")
_PLACEMENT_LABEL = {
    "placementTop": "搜索结果顶部",
    "placementRestOfSearch": "搜索结果其余",
    "placementProductPage": "商品详情页",
    "placementSiteAmazonBusiness": "企业购",
    "offAmazon": "站外",
}
_ACTION_LABELS = {
    "set_campaign_budget": "调整活动每日预算",
    "set_group_bid": "调整广告组默认竞价",
    "set_target_bid": "调整投放竞价",
    "set_target_state": "暂停或启用投放",
    "set_group_state": "暂停或启用广告组",
    "set_campaign_state": "暂停或启用活动",
    "set_campaign_placement": "调整广告位加价",
    "set_ad_product_state": "暂停或启用广告产品",
    "add_negative_keyword": "添加否定关键词",
    "add_negative_asin": "添加否定 ASIN",
    "archive_negative": "归档否定项",
    "add_products_to_group": "添加广告产品",
    "archive_ad_product": "归档广告产品",
    "add_group": "追加广告组",
    "rename_campaign": "活动改名",
    "move_campaign_portfolio": "移动广告组合",
}
_PARAM_LABELS = {
    "daily_budget": "修改后的每日预算（日元）",
    "expected_before": "修改前的值，用于防止并发覆盖",
    "default_bid": "修改后的广告组默认竞价（日元）",
    "bid": "修改后的投放竞价（日元）",
    "placement": "广告位",
    "value": "广告位加价比例（%）",
    "keywords": "需要否定的搜索词及匹配方式",
    "asins": "需要否定的 ASIN",
    "state": "目标状态",
    "portfolio": "目标广告组合",
    "name": "新名称",
}


def _live_campaign_info(campaign_id: str) -> Optional[Dict[str, Any]]:
    """读取赛狐实时活动配置，避免用本地维表里的过期预算生成策略。"""
    cid = str(campaign_id or "").strip()
    if not cid.isdigit() or len(cid) < 6:
        return None
    try:
        from backend.ads_ops.transport import SellfoxTransport
        from backend.campaign_probe_api import DEFAULT_SHOP_ID
        tr = SellfoxTransport(timeout=4, retries=0)
        resp = tr.request("gw", "adCampaign/getInfo", {
            "shopId": DEFAULT_SHOP_ID, "campaignId": cid, "type": "sp",
        }, encoding="json", retry=False)
        data = resp.get("data") or {}
        return data if isinstance(data, dict) and data else None
    except Exception:       # noqa: BLE001
        return None


def _sync_live_campaign(campaign: Dict[str, Any]) -> Dict[str, Any]:
    """用实时活动配置覆盖本地预算；成功时顺手修正 dim_campaign。"""
    cid = str(campaign.get("campaign_id") or "")
    live = _live_campaign_info(cid)
    if not live:
        campaign["budget_source"] = "local"
        return campaign
    live_budget = _f(live.get("dailyBudget"))
    if live_budget > 0:
        old = _f(campaign.get("daily_budget"))
        campaign["daily_budget"] = live_budget
        campaign["budget_source"] = "live"
        if abs(old - live_budget) > 0.009:
            try:
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE dim_campaign SET daily_budget=?, updated_at=? "
                        "WHERE campaign_id=?",
                        (live_budget, date.today().isoformat(), cid))
                    conn.commit()
            except Exception:   # noqa: BLE001
                pass
    live_state = str(live.get("state") or "")
    if live_state:
        campaign["status"] = live_state
    return campaign


def _fmt_short(value: Any) -> str:
    number = _f(value)
    return str(int(round(number))) if abs(number - round(number)) < 1e-9 else f"{number:.2f}"


def _pct_change(before: Any, after: Any) -> Optional[float]:
    old, new = _f(before), _f(after)
    if old <= 0:
        return None
    return round((new - old) / old * 100, 1)


def _annotate_strategy(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """给动作补中文名称、解释和参数说明。执行仍使用原始 action/params。"""
    for a in actions:
        action = str(a.get("action") or "")
        params = a.get("params") or {}
        label = _ACTION_LABELS.get(action, action)
        explanation = ""
        if action == "set_campaign_budget":
            before = params.get("expected_before")
            after = params.get("daily_budget")
            change = _pct_change(before, after)
            explanation = (
                f"将活动每日预算从 {_fmt_short(before)} 调整为 {_fmt_short(after)} 日元"
                + (f"（{change:+.1f}%）" if change is not None else "")
                + "，先控制无单或高花费，观察 2～3 天后再决定是否继续调整。")
        elif action == "set_group_bid":
            before = params.get("expected_before") or params.get("default_bid")
            after = params.get("default_bid")
            change = _pct_change(before, after) if params.get("expected_before") else None
            explanation = (
                f"将广告组默认竞价从 {_fmt_short(before)} 调整为 {_fmt_short(after)} 日元"
                + (f"（{change:+.1f}%）" if change is not None else "")
                + "，降低该组持续无单时的点击成本。")
        elif action == "add_negative_keyword":
            words = [str(x.get("keyword_text") or "") for x in params.get("keywords") or []]
            explanation = (
                "将 " + "、".join(x for x in words if x)
                + " 加入否定关键词，后续广告不再匹配这些搜索词，减少无效点击。")
        elif action == "add_negative_asin":
            asins = [str(x) for x in params.get("asins") or []]
            explanation = (
                "将 " + "、".join(asins)
                + " 加入否定 ASIN，减少在这些商品详情页上的无效展示和点击。")
        elif action == "set_campaign_placement":
            pred = str(params.get("placement") or "")
            placement = _PLACEMENT_LABEL.get(pred, pred)
            before = params.get("expected_before")
            after = params.get("value")
            explanation = (
                f"将{placement}广告位加价从 {before}% 调整为 {after}%"
                "，降低该广告位持续无单时的流量成本。" if before is not None
                else f"将{placement}广告位加价调整为 {after}%，降低无效流量成本。")
        elif action == "set_target_bid":
            explanation = (
                f"将投放 {a.get('selector', {}).get('target_text') or ''} 的竞价"
                f"从 {params.get('expected_before', '-')} 调整为 {params.get('bid', '-')}，"
                "降低高点击零单投放的 CPC。")
        elif action == "set_target_state":
            explanation = f"将投放状态调整为 {params.get('state')}，控制无效流量继续消耗。"
        elif action == "set_group_state":
            explanation = f"将广告组状态调整为 {params.get('state')}，控制该组整体花费。"
        elif action == "set_campaign_state":
            explanation = f"将活动状态调整为 {params.get('state')}，暂停或恢复整个活动。"
        elif action == "set_ad_product_state":
            explanation = f"将广告产品状态调整为 {params.get('state')}，控制单个 ASIN 的投放。"
        elif action == "archive_negative":
            explanation = "归档指定否定项，使其不再生效。"
        elif action == "add_products_to_group":
            explanation = "将指定 Active 商品加入广告组，扩大可投放 SKU。"
        elif action == "archive_ad_product":
            explanation = "归档广告组内的指定商品，停止该 ASIN 继续消耗广告预算。"
        elif action == "add_group":
            explanation = "在现有活动下追加广告组，用于拆分关键词、商品或投放策略。"
        elif action == "rename_campaign":
            explanation = "修改活动名称，便于后续识别和管理。"
        elif action == "move_campaign_portfolio":
            explanation = "将活动移动到目标广告组合，只改变归属，不改变投放。"
        else:
            explanation = a.get("reason") or "按当前策略调整广告设置。"

        a["action_label"] = label
        a["explanation"] = explanation
        a["param_explanation"] = {
            k: _PARAM_LABELS.get(k, k) for k in params
        }
    return actions


def _f(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def _parent(*names: Any) -> str:
    for name in names:
        hit = _PARENT_RE.findall(str(name or ""))
        if hit:
            return hit[-1]
    return ""


def _metrics(row: Dict[str, Any]) -> Dict[str, Any]:
    impressions = _f(row.get("impressions"))
    clicks = _f(row.get("clicks"))
    cost = _f(row.get("cost"))
    orders = _f(row.get("orders"))
    sales = _f(row.get("sales"))
    return {
        "impressions": round(impressions, 2),
        "clicks": round(clicks, 2),
        "cost": round(cost, 2),
        "orders": round(orders, 2),
        "sales": round(sales, 2),
        "add_to_cart": round(_f(row.get("add_to_cart")), 2),
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
        "cpc": round(cost / clicks, 2) if clicks else 0.0,
        "cvr": round(orders / clicks * 100, 2) if clicks else 0.0,
        "acos": round(cost / sales * 100, 2) if sales else None,
        "roas": round(sales / cost, 2) if cost else 0.0,
    }


def _row_dict(row: Any) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


class CampaignAnalysisService:
    # ------------------------------------------------------------------
    # 活动列表
    # ------------------------------------------------------------------
    def campaigns(self) -> List[Dict[str, Any]]:
        with db.get_conn() as conn:
            rows = conn.execute("""
                SELECT c.campaign_id, c.inner_id, c.name, c.targeting_type, c.status,
                       c.daily_budget, c.strategy, c.portfolio_id, c.portfolio_name,
                       COUNT(DISTINCT f.date) data_days,
                       MAX(f.date) last_date
                FROM dim_campaign c
                LEFT JOIN fact_campaign f
                  ON f.campaign_id=c.campaign_id AND f.hour=-1 AND f.placement=''
                GROUP BY c.campaign_id, c.inner_id, c.name, c.targeting_type,
                         c.status, c.daily_budget, c.strategy, c.portfolio_id,
                         c.portfolio_name
                ORDER BY data_days DESC, c.name
            """).fetchall()
        return [{
            "campaign_id": r["campaign_id"], "campaign_internal_id": r["inner_id"],
            "name": r["name"], "parent_asin": _parent(r["name"]),
            "targeting_type": r["targeting_type"], "state": r["status"],
            "daily_budget": _f(r["daily_budget"]), "strategy": r["strategy"],
            "portfolio_id": r["portfolio_id"], "portfolio_name": r["portfolio_name"],
            "data_days": int(r["data_days"] or 0),
            "has_data": bool(r["data_days"]),
            "last_date": r["last_date"],
        } for r in rows]

    # ------------------------------------------------------------------
    # 活动分析
    # ------------------------------------------------------------------
    def analyze(self, campaign_id: str, date_from: str, date_to: str,
                target_acos: float = 35.0) -> Dict[str, Any]:
        campaign_id = str(campaign_id or "").strip()
        if not campaign_id:
            raise ValidationFailed("campaign_id 不能为空")
        if not date_from or not date_to:
            raise ValidationFailed("date_from/date_to 不能为空")
        if date_from > date_to:
            raise ValidationFailed("date_from 不能晚于 date_to")
        with db.get_conn() as conn:
            campaign = conn.execute(
                "SELECT * FROM dim_campaign WHERE campaign_id=?",
                (campaign_id,)).fetchone()
            if not campaign:
                raise NotFound("本地库没有该活动", details={"campaign_id": campaign_id})
            campaign = _row_dict(campaign)
            # 本地维表可能停留在上次采集时的预算；策略参数必须以实时值为基线。
            campaign = _sync_live_campaign(campaign)
            summary_row = conn.execute("""
                SELECT COALESCE(SUM(impressions),0) impressions,
                       COALESCE(SUM(clicks),0) clicks,
                       COALESCE(SUM(cost),0) cost,
                       COALESCE(SUM(orders),0) orders,
                       COALESCE(SUM(sales),0) sales,
                       COALESCE(SUM(add_to_cart),0) add_to_cart
                FROM fact_campaign
                WHERE campaign_id=? AND date BETWEEN ? AND ?
                  AND hour=-1 AND placement=''
            """, (campaign_id, date_from, date_to)).fetchone()
            summary = _metrics(_row_dict(summary_row))

            coverage_row = conn.execute("""
                SELECT COUNT(DISTINCT date) days, MIN(date) min_date, MAX(date) max_date
                FROM fact_campaign
                WHERE campaign_id=? AND date BETWEEN ? AND ? AND hour=-1 AND placement=''
            """, (campaign_id, date_from, date_to)).fetchone()

            trend_rows = conn.execute("""
                SELECT date, COALESCE(SUM(impressions),0) impressions,
                       COALESCE(SUM(clicks),0) clicks, COALESCE(SUM(cost),0) cost,
                       COALESCE(SUM(orders),0) orders, COALESCE(SUM(sales),0) sales,
                       COALESCE(SUM(add_to_cart),0) add_to_cart
                FROM fact_campaign
                WHERE campaign_id=? AND date BETWEEN ? AND ?
                  AND hour=-1 AND placement=''
                GROUP BY date ORDER BY date
            """, (campaign_id, date_from, date_to)).fetchall()

            group_rows = conn.execute("""
                SELECT g.group_id, g.name, g.default_bid,
                       COALESCE(SUM(f.impressions),0) impressions,
                       COALESCE(SUM(f.clicks),0) clicks,
                       COALESCE(SUM(f.cost),0) cost,
                       COALESCE(SUM(f.orders),0) orders,
                       COALESCE(SUM(f.sales),0) sales
                FROM fact_group f
                LEFT JOIN dim_group g ON g.group_id=f.group_id
                WHERE f.campaign_id=? AND f.date BETWEEN ? AND ?
                  AND f.hour=-1 AND f.placement=''
                GROUP BY g.group_id, g.amazon_group_id, g.name, g.default_bid
                ORDER BY cost DESC
            """, (campaign_id, date_from, date_to)).fetchall()

            target_rows = conn.execute("""
                SELECT t.target_id, t.group_id, dt.target_type, dt.target_text,
                       dt.match_type, dg.name group_name,
                       COALESCE(SUM(t.impressions),0) impressions,
                       COALESCE(SUM(t.clicks),0) clicks,
                       COALESCE(SUM(t.cost),0) cost,
                       COALESCE(SUM(t.orders),0) orders,
                       COALESCE(SUM(t.sales),0) sales
                FROM fact_target t
                LEFT JOIN dim_target dt ON dt.target_id=t.target_id
                LEFT JOIN dim_group dg ON dg.amazon_group_id=dt.group_id
                WHERE t.campaign_id=? AND t.date BETWEEN ? AND ? AND t.hour=-1
                GROUP BY t.target_id, t.group_id, dt.target_type, dt.target_text,
                         dt.match_type, dg.name
                ORDER BY cost DESC
            """, (campaign_id, date_from, date_to)).fetchall()

            product_rows = conn.execute("""
                SELECT p.asin, dp.parent_asin, dp.title,
                       COALESCE(SUM(p.impressions),0) impressions,
                       COALESCE(SUM(p.clicks),0) clicks,
                       COALESCE(SUM(p.cost),0) cost,
                       COALESCE(SUM(p.orders),0) orders,
                       COALESCE(SUM(p.sales),0) sales
                FROM fact_product p
                LEFT JOIN dim_product dp ON dp.asin=p.asin
                WHERE p.campaign_id=? AND p.date BETWEEN ? AND ? AND p.hour=-1
                GROUP BY p.asin, dp.parent_asin, dp.title
                ORDER BY cost DESC
            """, (campaign_id, date_from, date_to)).fetchall()

            placement_rows = conn.execute("""
                SELECT placement, COALESCE(SUM(impressions),0) impressions,
                       COALESCE(SUM(clicks),0) clicks,
                       COALESCE(SUM(cost),0) cost,
                       COALESCE(SUM(orders),0) orders,
                       COALESCE(SUM(sales),0) sales
                FROM fact_campaign
                WHERE campaign_id=? AND date BETWEEN ? AND ?
                  AND hour=-1 AND placement<>''
                GROUP BY placement ORDER BY cost DESC
            """, (campaign_id, date_from, date_to)).fetchall()

            hourly_rows = conn.execute("""
                SELECT hour, COALESCE(SUM(impressions),0) impressions,
                       COALESCE(SUM(clicks),0) clicks,
                       COALESCE(SUM(cost),0) cost,
                       COALESCE(SUM(orders),0) orders,
                       COALESCE(SUM(sales),0) sales
                FROM fact_campaign
                WHERE campaign_id=? AND date BETWEEN ? AND ?
                  AND hour>=0 AND placement=''
                GROUP BY hour ORDER BY hour
            """, (campaign_id, date_from, date_to)).fetchall()

            search_rows = conn.execute("""
                SELECT s.query_type, s.term, s.match_type,
                       MAX(s.owner_id) owner_id,
                       COALESCE(SUM(s.impressions),0) impressions,
                       COALESCE(SUM(s.clicks),0) clicks,
                       COALESCE(SUM(s.cost),0) cost,
                       COALESCE(SUM(s.orders),0) orders,
                       COALESCE(SUM(s.add_to_cart),0) add_to_cart
                FROM fact_search_term s
                WHERE s.campaign_id=? AND s.date BETWEEN ? AND ?
                  AND s.scope='campaign'
                GROUP BY s.query_type, s.term, s.match_type
                ORDER BY clicks DESC
                LIMIT 1000
            """, (campaign_id, date_from, date_to)).fetchall()
            group_names = {str(r["group_id"]): r["name"] for r in group_rows}

        trend = []
        for r in trend_rows:
            d = _row_dict(r)
            trend.append({"date": d["date"], **_metrics(d)})
        groups = []
        for r in group_rows:
            d = _row_dict(r)
            groups.append({
                "group_id": d.get("group_id"),
                "name": d.get("name") or d.get("group_id"),
                "default_bid": _f(d.get("default_bid")),
                **_metrics(d),
            })
        targets = []
        for r in target_rows:
            d = _row_dict(r)
            targets.append({
                "target_id": d.get("target_id"),
                "group_id": d.get("group_id"),
                "group_name": d.get("group_name"),
                "target_type": d.get("target_type") or "",
                "target_text": d.get("target_text") or "",
                "match_type": d.get("match_type") or "",
                **_metrics(d),
            })
        products = []
        for r in product_rows:
            d = _row_dict(r)
            products.append({
                "asin": d.get("asin"),
                "parent_asin": d.get("parent_asin") or _parent(campaign.get("name")),
                "title": d.get("title") or "",
                **_metrics(d),
            })
        placements = []
        for r in placement_rows:
            d = _row_dict(r)
            placements.append({
                "placement": d.get("placement"),
                "label": _PLACEMENT_LABEL.get(d.get("placement"), d.get("placement")),
                **_metrics(d),
            })
        hourly = []
        for r in hourly_rows:
            d = _row_dict(r)
            hourly.append({"hour": int(d.get("hour") or 0), **_metrics(d)})
        # 同一搜索词可能同时出现在宽泛/紧密等多个匹配方式中：
        # 保留总指标，同时保留各匹配方式的指标明细，供页面/Excel 括号展示。
        term_map: Dict[tuple, Dict[str, Any]] = {}
        for r in search_rows:
            d = _row_dict(r)
            key = (d.get("query_type"), d.get("term"))
            if key not in term_map:
                term_map[key] = {
                    "query_type": d.get("query_type"),
                    "term": d.get("term"),
                    "parent_asin": _parent(campaign.get("name")),
                    "match_types": [],
                    "group_id": d.get("owner_id"),
                    "group_name": group_names.get(str(d.get("owner_id") or "")),
                    "match_breakdown": {},
                    "impressions": 0.0, "clicks": 0.0, "cost": 0.0,
                    "orders": 0.0, "sales": 0.0, "add_to_cart": 0.0,
                }
            row = term_map[key]
            mt = str(d.get("match_type") or "")
            if mt and mt not in row["match_types"]:
                row["match_types"].append(mt)
            if mt:
                row["match_breakdown"][mt] = _metrics(d)
            for field in ("impressions", "clicks", "cost", "orders", "sales",
                          "add_to_cart"):
                row[field] += _f(d.get(field))
        terms = []
        for row in term_map.values():
            item = {**row}
            item.update(_metrics(row))
            terms.append(item)
        terms.sort(key=lambda x: x["clicks"], reverse=True)
        terms = terms[:200]

        # 竞品：用本活动命中的搜索词匹配本地竞品表。
        competitor_terms = sorted({x["term"] for x in terms
                                   if x["query_type"] == "keyword" and x["term"]})
        competitors: List[Dict[str, Any]] = []
        if competitor_terms:
            placeholders = ",".join("?" for _ in competitor_terms)
            with db.get_conn() as conn:
                rows = conn.execute(f"""
                    SELECT term, asin, position, title, price, rating, reviews,
                           delivery, coupon, stock, sponsored, url, fetched_at
                    FROM fact_competitor
                    WHERE term IN ({placeholders})
                    ORDER BY term, position
                    LIMIT 2000
                """, tuple(competitor_terms)).fetchall()
            competitors = [_row_dict(x) for x in rows]

        diagnostics: List[Dict[str, Any]] = []
        strategy: List[Dict[str, Any]] = []
        campaign_parent = _parent(campaign.get("name"))
        budget = _f(campaign.get("daily_budget"))

        if summary["clicks"] >= 10 and summary["orders"] == 0 and summary["cost"] > 0:
            diagnostics.append({
                "type": "campaign_zero_order", "severity": "high",
                "title": "活动有足够点击但零订单",
                "evidence": {"clicks": summary["clicks"], "cost": summary["cost"],
                             "orders": 0},
            })
            if budget > 0:
                new_budget = max(1, round(budget * 0.8, 2))
                strategy.append({
                    "action": "set_campaign_budget", "level": "L1",
                    "selector": {"campaign_id": campaign_id},
                    "params": {"daily_budget": new_budget, "expected_before": budget},
                    "reason": f"活动累计 {int(summary['clicks'])} 次点击、0 单，先降预算观察",
                    "requires_approval": False,
                })

        if summary["clicks"] >= 10 and summary["orders"] > 0 and summary["acos"] is not None \
                and summary["acos"] > target_acos:
            diagnostics.append({
                "type": "campaign_high_acos", "severity": "medium",
                "title": f"活动 ACOS {summary['acos']:.1f}% 高于目标 {target_acos:.0f}%",
                "evidence": {"acos": summary["acos"], "target_acos": target_acos},
            })

        for g in groups:
            if g["clicks"] >= 10 and g["orders"] == 0 and g["cost"] > 0:
                diagnostics.append({
                    "type": "group_zero_order", "severity": "high",
                    "title": f"广告组 {g['name']} 高点击零单",
                    "evidence": {"group_id": g["group_id"], "clicks": g["clicks"],
                                 "cost": g["cost"]},
                })
                if g["default_bid"] > 0:
                    new_bid = max(1, round(g["default_bid"] * 0.8, 2))
                    strategy.append({
                        "action": "set_group_bid", "level": "L1",
                        "selector": {"campaign_id": campaign_id, "group": g["name"]},
                        "params": {"default_bid": new_bid,
                                   "expected_before": g["default_bid"]},
                        "reason": f"{g['name']} {int(g['clicks'])} 点击、0 单，降低默认竞价",
                        "requires_approval": False,
                    })

        for t in targets:
            if t["clicks"] >= 8 and t["orders"] == 0 and t["cost"] > 0:
                diagnostics.append({
                    "type": "target_zero_order", "severity": "medium",
                    "title": f"投放 {t['target_text'] or t['target_id']} 高点击零单",
                    "evidence": {"target_id": t["target_id"], "clicks": t["clicks"],
                                 "cost": t["cost"], "group_name": t["group_name"]},
                    # 本地事实表不保存当前竞价，不能生成可直接执行的 bid 参数。
                    "action_hint": {
                        "action": "set_target_bid",
                        "selector": {
                            "campaign_id": campaign_id,
                            "group": t["group_name"] or t["group_id"],
                            "target_type": t["target_type"] or "auto",
                            "target_text": t["target_text"],
                        },
                        "needs_live_bid": True,
                        "suggested_change_pct": -20,
                    },
                })

        for term in terms:
            if term["query_type"] == "keyword" and term["clicks"] > 10 and term["orders"] == 0:
                group_name = term.get("group_name")
                selector = {"campaign_id": campaign_id}
                selector["group" if group_name else "group_id"] = group_name or term.get("group_id")
                strategy.append({
                    "action": "add_negative_keyword", "level": "L2",
                    "selector": selector,
                    "params": {"keywords": [{
                        "keyword_text": term["term"],
                        "match_type": "negativeExact",
                    }]},
                    "reason": f"搜索词 {term['term']} 点击 {int(term['clicks'])}、0 单",
                    "requires_approval": True,
                })

        placement_value = {
            "placementTop": _f(campaign.get("placement_top")),
            "placementRestOfSearch": _f(campaign.get("placement_rest")),
            "placementProductPage": _f(campaign.get("placement_product")),
        }
        for p in placements:
            pred = p["placement"]
            if pred in placement_value and p["clicks"] >= 8 and p["orders"] == 0 and p["cost"] > 0:
                current_pct = placement_value[pred]
                new_pct = max(0, int(round(current_pct - 10)))
                diagnostics.append({
                    "type": "placement_zero_order", "severity": "medium",
                    "title": f"{p['label']} 高点击零单",
                    "evidence": {"placement": pred, "clicks": p["clicks"],
                                 "cost": p["cost"], "percentage": current_pct},
                })
                # 当前加价已经是 0 时，不再生成无变化的策略。
                if new_pct == int(round(current_pct)):
                    continue
                strategy.append({
                    "action": "set_campaign_placement", "level": "L1",
                    "selector": {"campaign_id": campaign_id},
                    "params": {"placement": pred, "value": new_pct,
                               "expected_before": current_pct},
                    "reason": f"{p['label']} 花费 {p['cost']:.2f} 且 0 单，降低加价",
                    "requires_approval": False,
                })

        if not coverage_row or int(coverage_row["days"] or 0) == 0:
            diagnostics.append({
                "type": "data_incomplete", "severity": "high",
                "title": "本地没有该日期范围内的活动数据",
                "evidence": {"campaign_id": campaign_id},
            })

        strategy = _annotate_strategy(strategy)

        return {
            "campaign": {
                "campaign_id": campaign_id,
                "campaign_internal_id": campaign.get("inner_id"),
                "name": campaign.get("name"),
                "parent_asin": campaign_parent,
                "targeting_type": campaign.get("targeting_type"),
                "state": campaign.get("status"),
                "daily_budget": budget,
                "budget_source": campaign.get("budget_source", "local"),
                "strategy": campaign.get("strategy"),
                "portfolio_name": campaign.get("portfolio_name"),
            },
            "date_from": date_from, "date_to": date_to,
            "coverage": {
                "days": int(coverage_row["days"] or 0) if coverage_row else 0,
                "min_date": coverage_row["min_date"] if coverage_row else None,
                "max_date": coverage_row["max_date"] if coverage_row else None,
                "warnings": [] if coverage_row and int(coverage_row["days"] or 0) else
                            ["本地没有该活动的日期数据，请先采集"],
            },
            "summary": summary,
            "trend": trend,
            "groups": groups,
            "targets": targets,
            "products": products,
            "placements": placements,
            "hourly": hourly,
            "search_terms": terms,
            "competitors": competitors,
            "diagnostics": diagnostics,
            "strategy": {
                "summary": f"识别 {len(diagnostics)} 个诊断问题，生成 {len(strategy)} 个动作建议",
                "actions": strategy,
                "count": len(strategy),
            },
            "source": "local_db",
        }
