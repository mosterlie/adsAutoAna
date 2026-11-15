# -*- coding: utf-8 -*-
"""L3 动作注册。

动作清单仍以 actions.json 为准；这里只建立 action name → handler 的编译映射。
后续新增动作时，注册表和 handler 必须同时存在，启动自检会拦下缺失。
"""
from __future__ import annotations

from typing import Dict

from backend.ads_ops.actions.base import ActionHandler
from backend.ads_ops.actions.campaign import (ArchiveCampaignHandler,
                                              CreateCampaignHandler)
from backend.ads_ops.actions.operations import (AddGroupHandler,
                                                AddNegativeAsinHandler,
                                                AddNegativeKeywordHandler,
                                                AddProductsToGroupHandler,
                                                ArchiveAdProductHandler,
                                                ArchiveNegativeHandler,
                                                MoveCampaignPortfolioHandler,
                                                RenameCampaignHandler,
                                                SetAdProductStateHandler,
                                                SetCampaignBudgetHandler,
                                                SetCampaignPlacementHandler,
                                                SetCampaignStateHandler,
                                                SetGroupBidHandler,
                                                SetGroupStateHandler,
                                                SetTargetBidHandler,
                                                SetTargetStateHandler)

HANDLERS: Dict[str, ActionHandler] = {
    h.action: h for h in (
        SetTargetBidHandler(), SetTargetStateHandler(),
        SetGroupBidHandler(), SetGroupStateHandler(),
        SetCampaignStateHandler(), SetCampaignPlacementHandler(),
        SetCampaignBudgetHandler(), SetAdProductStateHandler(),
        AddNegativeKeywordHandler(), AddNegativeAsinHandler(),
        ArchiveNegativeHandler(), AddProductsToGroupHandler(),
        ArchiveAdProductHandler(), AddGroupHandler(),
        RenameCampaignHandler(), MoveCampaignPortfolioHandler(),
        CreateCampaignHandler(), ArchiveCampaignHandler(),
    )
}


def get_handler(action: str) -> ActionHandler:
    if action not in HANDLERS:
        raise KeyError(action)
    return HANDLERS[action]
