# -*- coding: utf-8 -*-
"""按广告活动名称，从赛狐拉取亚马逊真实实体 ID（campaign / adGroup / target / 否定 / 广告）。

用途:
    我们生成的 Bulk 表用自建编号（CA_/GR_）做表内引用；要在亚马逊上
    「更新」已有活动，必须换成亚马逊分配的真实 ID。这里一次性把该活动
    下所有实体的 ID 拉齐，回填到记录（ad_creation_records.amazon_ids_json），
    Update 导出时直接用。

接口（实测可用，v14）:
    multiple/campaign/getAllCampaignData   searchField=name  → campaignId
    multiple/group/getAllGroupData         campaignIdList    → adGroupId（全部组）
    multiple/target/getAllTargetData       targetType=keyword|auto → targetId
    multiple/neTarget/getAllNeTargetData   neType=keyword    → 否定关键词 neTargetId
                                           neType=target     → 否定商品   neTargetId
    multiple/adProduct/getAdProductList    campaignIdList    → adId

注意:
    - 否定商品用 neType=target（不是 product），这是踩过的坑；
    - target 接口的 targetType 只有 keyword / auto（商品投放不在该枚举内）；
    - 名称模糊匹配可能命中多个活动，这里全部返回，由前端选择。
"""
from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from backend import sellfox_client as sf

#: 赛狐自动投放表达式 → 亚马逊 Bulk「拓展商品投放编号」
AUTO_EXPRESSION = {
    "queryHighRelMatches": "close-match",
    "queryBroadRelMatches": "loose-match",
    "asinSubstituteRelated": "substitutes",
    "asinAccessoryRelated": "complements",
}


def _fetch(client: "sf.SellfoxClient", tab: str, shops: List[int],
           scope: Optional[Dict[str, Any]] = None,
           extra: Optional[Dict[str, Any]] = None,
           page_size: int = 200, max_pages: int = 30,
           use_dates: bool = True) -> List[Dict[str, Any]]:
    end = datetime.date.today().strftime("%Y-%m-%d")
    start = (datetime.date.today() - datetime.timedelta(days=365)).strftime("%Y-%m-%d")
    rows: List[Dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        payload = sf.build_payload(tab, shops, start, end, scope,
                                   page_no=page, page_size=page_size)
        if not use_dates:                   # 否定投放接口按活动维度取，不需要日期
            payload["startDate"] = None
            payload["endDate"] = None
        payload.update(extra or {})
        j = client.post(sf.TAB_DEFS[tab]["list"], payload)
        pg = (j.get("data") or {}).get("page") or (j.get("data") or {})
        batch = pg.get("rows") or []
        rows.extend(batch)
        total = pg.get("totalSize") or 0
        if not batch or len(rows) >= total:
            break
    return rows


def _collect(client: "sf.SellfoxClient", shops: List[int], camp: Dict[str, Any],
             ad_type: str) -> Dict[str, Any]:
    """一个活动 → 它下面全部实体 ID。"""
    cid = str(camp.get("campaignId") or "")
    only = {"campaignIdList": [cid]}

    groups = _fetch(client, "group", shops, {"adType": ad_type}, only)

    targets: List[Dict[str, Any]] = []
    for ttype in ("keyword", "auto"):
        for t in _fetch(client, "target", shops,
                        {"adType": ad_type, "targetType": ttype}, only):
            targets.append({
                "target_id": str(t.get("targetId") or ""),
                "target_type": t.get("targetType") or ttype,
                "expression": AUTO_EXPRESSION.get(str(t.get("targetText") or ""),
                                                  t.get("targetText") or ""),
                "target_text": t.get("targetText") or "",
                "keyword_text": t.get("keywordText") or "",
                "match_type": t.get("matchType") or "",
                "bid": t.get("bid"),
                "state": t.get("state") or "",
                "ad_group_id": str(t.get("adGroupId") or ""),
            })

    neg_kw: List[Dict[str, Any]] = []
    for n in _fetch(client, "netarget", shops,
                    {"adType": ad_type, "neType": "keyword", "neLevels": []}, only,
                    use_dates=False):
        neg_kw.append({
            "ne_target_id": str(n.get("neTargetId") or ""),
            "keyword_text": n.get("keywordText") or "",
            "match_type": n.get("matchType") or "",
            "ne_level": n.get("neLevel") or "",
            "ad_group_id": str(n.get("adGroupId") or ""),
            "state": n.get("state") or "",
        })

    neg_prod: List[Dict[str, Any]] = []
    for n in _fetch(client, "netarget", shops,
                    {"adType": ad_type, "neType": "target", "neLevels": []}, only,
                    use_dates=False):
        neg_prod.append({
            "ne_target_id": str(n.get("neTargetId") or ""),
            "asin": n.get("targetingValue") or "",
            "ne_level": n.get("neLevel") or "",
            "ad_group_id": str(n.get("adGroupId") or ""),
            "state": n.get("state") or "",
        })

    products = _fetch(client, "product", shops, {"type": ad_type}, only)

    return {
        "campaign": {
            "campaign_id": cid,
            "name": camp.get("name") or "",
            "state": camp.get("state") or "",
            "budget": camp.get("dailyBudget") or camp.get("budget"),
            "portfolio_id": str(camp.get("portfolioId") or ""),
            "targeting_type": camp.get("campaignTargetingType") or "",
            "start_date": camp.get("startDate") or "",
        },
        "ad_groups": [{
            "ad_group_id": str(g.get("adGroupId") or ""),
            "name": g.get("name") or "",
            "state": g.get("state") or "",
            "default_bid": g.get("defaultBid") or g.get("bid"),
        } for g in groups],
        "targets": targets,
        "neg_keywords": neg_kw,
        "neg_products": neg_prod,
        "products": [{
            "ad_id": str(p.get("adId") or p.get("id") or ""),
            "sku": p.get("sku") or p.get("msku") or "",
            "asin": p.get("asin") or "",
            "state": p.get("state") or "",
            "ad_group_id": str(p.get("adGroupId") or ""),
        } for p in products],
    }


def _summary(node: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "campaign_id": (node.get("campaign") or {}).get("campaign_id") or "",
        "name": (node.get("campaign") or {}).get("name") or "",
        "state": (node.get("campaign") or {}).get("state") or "",
        "budget": (node.get("campaign") or {}).get("budget"),
        "ad_groups": len(node.get("ad_groups") or []),
        "targets": len(node.get("targets") or []),
        "neg_keywords": len(node.get("neg_keywords") or []),
        "neg_products": len(node.get("neg_products") or []),
        "products": len(node.get("products") or []),
    }


def lookup(name: str, shops: List[int], ad_type: str = "sp") -> Dict[str, Any]:
    """按活动名查全部真实 ID。

    返回:
        found: bool
        campaigns: [每个匹配活动一份完整 ID 树]
        summary:   [{campaign_id,name,各级数量}]
        以及兼容字段 campaign/ad_groups/... = 第一个活动的
    """
    from backend.cookies import load_cookies

    target = (name or "").strip()
    if not target:
        return {"found": False, "reason": "活动名不能为空"}
    if not shops:
        return {"found": False, "reason": "缺少店铺 ID"}

    cookies = load_cookies() or {}
    if not cookies:
        return {"found": False, "reason": "未找到赛狐登录 Cookie"}

    client = sf.SellfoxClient(cookies)
    camps = _fetch(client, "campaign", shops, {"adType": ad_type},
                   {"searchField": "name", "searchValue": target, "searchType": "blur"})

    exact = [c for c in camps if str(c.get("name") or "").strip() == target]
    fuzzy = [c for c in camps if target in str(c.get("name") or "")]
    # 精确命中优先；否则用模糊结果（可能多个）
    hits = exact or fuzzy
    if not hits:
        return {"found": False, "reason": f"赛狐里没找到名为「{target}」的广告活动"}

    nodes: List[Dict[str, Any]] = []
    for camp in hits:
        try:
            nodes.append(_collect(client, shops, camp, ad_type))
        except Exception as exc:                  # noqa: BLE001 - 单个活动失败不影响其他
            nodes.append({"campaign": {"campaign_id": str(camp.get("campaignId") or ""),
                                       "name": camp.get("name") or "",
                                       "error": str(exc)[:120]},
                          "ad_groups": [], "targets": [], "neg_keywords": [],
                          "neg_products": [], "products": []})

    first = nodes[0]
    return {
        "found": True,
        "match": "exact" if exact else "fuzzy",
        "campaigns": nodes,
        "summary": [_summary(n) for n in nodes],
        # 兼容旧调用方（前端默认用第一个，可再切换）
        **first,
    }
