# -*- coding: utf-8 -*-
"""活动 / 广告组实时总览。

数据源：
  campaign/getAllCampaignData
  multiple/group/getAllGroupData

广告组接口按 adType（sp/sb/sd）分别拉取；接口结果做短时缓存，
避免总览页面反复打开时重复请求赛狐。
"""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Any, Dict, List

import config
from backend import database as db
from backend.cookies import load_cookies
from backend.sellfox_client import SellfoxClient


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:  # noqa: BLE001
        return 0.0


def _campaign_row(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "campaign_id": str(r.get("campaignId") or ""),
        "campaign_internal_id": str(r.get("id") or ""),
        "campaign_name": r.get("name") or r.get("campaignName") or "",
        "state": r.get("state") or r.get("campaignState") or "",
        "ad_type": r.get("adType") or r.get("campaignType") or "",
        "targeting_type": r.get("targetingType") or "",
        "daily_budget": _num(r.get("dailyBudget")),
        "strategy": r.get("strategy") or "",
        "portfolio_id": str(r.get("portfolioId") or ""),
        "portfolio_name": r.get("portfolioName") or "",
        "start_date": r.get("startDate") or "",
        "end_date": r.get("endDate") or "",
    }


def _group_row(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "group_id": str(r.get("id") or ""),
        "amazon_group_id": str(r.get("adGroupId") or ""),
        "campaign_id": str(r.get("campaignId") or ""),
        "group_name": r.get("name") or r.get("adGroupName") or "",
        "default_bid": _num(r.get("defaultBid")),
        "state": r.get("state") or r.get("adGroupState") or "",
        "ad_type": r.get("adType") or "",
    }


class CampaignGroupsService:
    _lock = threading.Lock()
    _cache: Dict[str, Any] = {"at": 0.0, "data": None}
    ttl = 300.0

    def load(self, refresh: bool = False) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            if not refresh and self._cache["data"] and now - self._cache["at"] < self.ttl:
                return self._cache["data"]

        shop_id = int(getattr(config, "DEFAULT_SHOP_ID", 711811))
        # 用足够长的起点，确保拿到历史活动和暂停活动。
        start = "2020-01-01"
        end = date.today().isoformat()
        client = SellfoxClient(load_cookies())

        campaigns_raw = client.fetch_list("campaign", [shop_id], start, end)
        campaigns = [_campaign_row(x) for x in campaigns_raw]
        campaigns = [x for x in campaigns if x["campaign_id"]]
        campaigns.sort(key=lambda x: (x["campaign_name"] or "", x["campaign_id"]))

        groups: List[Dict[str, Any]] = []
        seen = set()
        for ad_type in getattr(config, "AD_TYPES", ["sp", "sb", "sd"]):
            rows = client.fetch_list("group", [shop_id], start, end,
                                     scope={"adType": ad_type})
            for raw in rows:
                item = _group_row(raw)
                key = item["group_id"] or (item["campaign_id"], item["amazon_group_id"])
                if not item["group_id"] or key in seen:
                    continue
                seen.add(key)
                item["ad_type"] = item["ad_type"] or ad_type
                groups.append(item)

        names = {x["campaign_id"]: x["campaign_name"] for x in campaigns}
        for g in groups:
            g["campaign_name"] = names.get(g["campaign_id"], "")

        groups.sort(key=lambda x: (x["campaign_name"] or "", x["group_name"] or ""))
        data = {
            "campaigns": campaigns,
            "groups": groups,
            "counts": {"campaigns": len(campaigns), "groups": len(groups)},
            "fetched_at": db.now(),
        }
        with self._lock:
            self._cache = {"at": time.time(), "data": data}
        return data
