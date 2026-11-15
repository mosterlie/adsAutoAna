# -*- coding: utf-8 -*-
"""L3 写策略：默认关闭，必须显式打开，并持有预览令牌。"""
from __future__ import annotations

import os
from typing import Any, Dict

from backend.ads_ops import registry
from backend.ads_ops.errors import ActionDisabled, ValidationFailed


def action_meta(action: str) -> Dict[str, Any]:
    try:
        return registry.get(action)
    except KeyError as exc:
        raise ValidationFailed(f"未注册的动作: {action}",
                               details={"available": [a["name"] for a in registry.ACTIONS]}) from exc


def level(action: str) -> str:
    return str(action_meta(action).get("level") or "")


def require_execution_enabled(action: str) -> None:
    """全局写开关 + L3 额外开关。

    L1/L2 默认允许，L3 仍需 ADS_L3_ENABLED=1。
    """
    lvl = level(action)
    if os.environ.get("ADS_WRITE_ENABLED", "1").strip().lower() not in (
            "1", "true", "yes", "on"):
        raise ActionDisabled("广告写操作已通过 ADS_WRITE_ENABLED=0 关闭")
    if lvl == "L3" and os.environ.get("ADS_L3_ENABLED", "0").strip().lower() not in (
            "1", "true", "yes", "on"):
        raise ActionDisabled(
            "L3 写操作默认关闭。确认代码和账号后设置 ADS_L3_ENABLED=1 再重启服务")
