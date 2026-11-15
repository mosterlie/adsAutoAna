# -*- coding: utf-8 -*-
"""给大模型 / MCP 使用的工具清单。

不是赛狐接口清单，而是稳定的业务工具：
    ads_action_catalog
    ads_query
    ads_preview
    ads_execute
"""
from __future__ import annotations

from typing import Any, Dict, List

from backend.ads_ops import registry

ACTION_LEVELS = {a["name"]: a["level"] for a in registry.ACTIONS}

CAMPAIGN_SELECTOR = {
    "type": "object",
    "description": "定位活动；campaign / campaign_id / campaign_internal_id 三选一",
    "properties": {
        "campaign": {"type": "string", "description": "活动名称"},
        "campaign_id": {"type": "string", "description": "Amazon campaignId"},
        "campaign_internal_id": {"type": "string", "description": "赛狐内部活动 ID"},
    },
    "additionalProperties": True,
}

GROUP_SELECTOR = {
    "type": "object",
    "description": "定位广告组；建议用 group 名称",
    "properties": {
        "campaign": {"type": "string"},
        "campaign_id": {"type": "string"},
        "campaign_internal_id": {"type": "string"},
        "group": {"type": "string", "description": "广告组名称"},
        "group_id": {"type": "string", "description": "Amazon adGroupId"},
    },
    "additionalProperties": True,
}

TARGET_SELECTOR = {
    "type": "object",
    "description": "定位投放；不要传内部 target id，执行器会解析",
    "properties": {
        "campaign": {"type": "string"},
        "campaign_id": {"type": "string"},
        "campaign_internal_id": {"type": "string"},
        "group": {"type": "string"},
        "group_id": {"type": "string"},
        "target_type": {"type": "string", "enum": ["auto", "keyword", "targeting"]},
        "target_text": {
            "type": "string",
            "description": "自动投放如 queryHighRelMatches；手动关键词用 keyword_text",
        },
        "keyword_text": {"type": "string"},
    },
    "additionalProperties": True,
}

PRODUCT_SELECTOR = {
    **GROUP_SELECTOR,
    "properties": {
        **GROUP_SELECTOR["properties"],
        "asin": {"type": "string"},
        "sku": {"type": "string"},
        "ad_product_internal_id": {"type": "string"},
    },
}

NEGATIVE_SELECTOR = {
    **GROUP_SELECTOR,
    "properties": {
        **GROUP_SELECTOR["properties"],
        "keyword_text": {"type": "string"},
        "asin": {"type": "string"},
    },
}

SELECTOR_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "set_target_bid": TARGET_SELECTOR,
    "set_target_state": TARGET_SELECTOR,
    "set_group_bid": GROUP_SELECTOR,
    "set_group_state": GROUP_SELECTOR,
    "set_campaign_state": CAMPAIGN_SELECTOR,
    "set_campaign_placement": CAMPAIGN_SELECTOR,
    "set_campaign_budget": CAMPAIGN_SELECTOR,
    "set_ad_product_state": PRODUCT_SELECTOR,
    "add_negative_keyword": GROUP_SELECTOR,
    "add_negative_asin": GROUP_SELECTOR,
    "archive_negative": NEGATIVE_SELECTOR,
    "add_products_to_group": GROUP_SELECTOR,
    "archive_ad_product": PRODUCT_SELECTOR,
    "add_group": CAMPAIGN_SELECTOR,
    "rename_campaign": CAMPAIGN_SELECTOR,
    "move_campaign_portfolio": CAMPAIGN_SELECTOR,
    "create_campaign": {"type": "object", "additionalProperties": False},
    "archive_campaign": CAMPAIGN_SELECTOR,
}

PARAM_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "set_target_bid": {
        "type": "object", "required": ["bid"],
        "properties": {
            "bid": {"type": "number", "minimum": 1, "maximum": 1000},
            "expected_before": {"type": "number"},
        },
    },
    "set_target_state": {
        "type": "object", "required": ["state"],
        "properties": {"state": {"type": "string", "enum": ["enabled", "paused"]}},
    },
    "set_group_bid": {
        "type": "object", "required": ["default_bid"],
        "properties": {"default_bid": {"type": "number", "minimum": 1, "maximum": 1000}},
    },
    "set_group_state": {
        "type": "object", "required": ["state"],
        "properties": {"state": {"type": "string", "enum": ["enabled", "paused"]}},
    },
    "set_campaign_state": {
        "type": "object", "required": ["state"],
        "properties": {"state": {"type": "string", "enum": ["enabled", "paused"]}},
    },
    "set_campaign_placement": {
        "type": "object", "required": ["placement", "value"],
        "properties": {
            "placement": {"type": "string", "enum": [
                "placementTop", "placementRestOfSearch",
                "placementProductPage", "placementSiteAmazonBusiness"]},
            "value": {"type": "integer", "minimum": 0, "maximum": 900},
        },
    },
    "set_campaign_budget": {
        "type": "object", "required": ["daily_budget"],
        "properties": {"daily_budget": {"type": "number", "exclusiveMinimum": 0}},
    },
    "set_ad_product_state": {
        "type": "object", "required": ["state"],
        "properties": {"state": {"type": "string", "enum": ["enabled", "paused"]}},
    },
    "add_negative_keyword": {
        "type": "object", "required": ["keywords"],
        "properties": {"keywords": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["keyword_text"],
            "properties": {
                "keyword_text": {"type": "string"},
                "match_type": {"type": "string", "enum": ["negativeExact", "negativePhrase"]},
            }}}},
    },
    "add_negative_asin": {
        "type": "object", "required": ["asins"],
        "properties": {"asins": {"type": "array", "minItems": 1,
                                 "items": {"type": "string"}}},
    },
    "archive_negative": {
        "type": "object", "required": ["ne_type"],
        "properties": {"ne_type": {"type": "string", "enum": ["keyword", "target"]}},
    },
    "add_products_to_group": {
        "type": "object", "required": ["products"],
        "properties": {"products": {"type": "array", "minItems": 1,
                                    "items": {"type": "object",
                                              "properties": {"asin": {"type": "string"},
                                                             "sku": {"type": "string"}}}}},
    },
    "archive_ad_product": {"type": "object", "properties": {}},
    "add_group": {
        "type": "object", "required": ["name", "default_bid", "products"],
        "properties": {
            "name": {"type": "string"}, "default_bid": {"type": "number"},
            "products": {"type": "array", "items": {"type": "object"}},
            "targeting_type": {"type": "string", "enum": ["auto", "manual"]},
        },
    },
    "rename_campaign": {
        "type": "object", "required": ["name"],
        "properties": {"name": {"type": "string"}},
    },
    "move_campaign_portfolio": {
        "type": "object", "required": ["portfolio"],
        "properties": {"portfolio": {"type": "string"}},
    },
    "create_campaign": {"type": "object"},
    "archive_campaign": {"type": "object"},
}


def _action_catalog() -> List[Dict[str, Any]]:
    out = []
    for a in registry.ACTIONS:
        name = a["name"]
        out.append({
            "name": name,
            "level": a["level"],
            "desc": a.get("desc", ""),
            "intent": a.get("intent", []),
            "selector_schema": SELECTOR_SCHEMAS.get(name, CAMPAIGN_SELECTOR),
            "params_schema": PARAM_SCHEMAS.get(name, {"type": "object"}),
            "limits": a.get("limits", {}),
            "idempotent": a.get("idempotent"),
            "rollback": a.get("rollback"),
        })
    return out


def build_manifest() -> Dict[str, Any]:
    actions = _action_catalog()
    action_names = [a["name"] for a in actions]
    return {
        "version": "1.0",
        "transport": "http",
        "base_url": "http://127.0.0.1:8320",
        "tools": [
            {
                "name": "ads_action_catalog",
                "endpoint": "GET /api/ads/tools/actions",
                "description": "查询广告动作目录；包含等级、selector_schema、params_schema、限制和回滚说明。",
                "input_schema": {
                    "type": "object",
                    "properties": {"action": {"type": "string", "enum": action_names}},
                    "additionalProperties": False,
                },
            },
            {
                "name": "ads_query",
                "endpoint": "POST /api/ads/query",
                "description": "只读查询活动、广告组、投放、广告产品、广告位、搜索词、搜索 ASIN 或数据覆盖。",
                "input_schema": {
                    "type": "object", "required": ["resource"],
                    "properties": {
                        "resource": {"type": "string", "enum": [
                            "campaigns", "groups", "targets", "products", "placements",
                            "search_terms", "search_asins", "data_coverage",
                            "campaign_analysis"]},
                        "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                        "date_to": {"type": "string", "description": "YYYY-MM-DD"},
                        "filters": {"type": "object"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                        "offset": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "ads_preview",
                "endpoint": "POST /api/ads/actions/preview",
                "description": "预览广告修改；不写广告，返回 before/after 和 confirm_token。",
                "input_schema": {
                    "type": "object", "required": ["action", "selector", "params"],
                    "properties": {
                        "action": {"type": "string", "enum": action_names},
                        "selector": {"type": "object"},
                        "params": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
            {
                "name": "ads_execute",
                "endpoint": "POST /api/ads/actions/execute",
                "description": "执行已确认的 preview；L2/L3 必须先获得人工确认。",
                "input_schema": {
                    "type": "object", "required": ["confirm_token"],
                    "properties": {
                        "confirm_token": {"type": "string"},
                        "action": {"type": "string"},
                        "selector": {"type": "object"},
                        "params": {"type": "object"},
                    },
                    "additionalProperties": False,
                },
            },
        ],
        "actions": actions,
        "levels": registry.levels(),
        "notes": [
            "大模型只提供 campaign/group/target_type 等业务选择器，不提供内部 ID。",
            "所有写操作必须 preview，再 execute。",
            "L3 创建/归档需要人工确认，Agent 不应自动执行。",
        ],
    }
