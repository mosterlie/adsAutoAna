# -*- coding: utf-8 -*-
"""领域动作处理器接口。

Handler 不负责生成确认令牌，也不直接写审计；它只负责：
prepare（校验、解析、组装请求） → execute（发写请求） → verify（读回）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.ads_ops.resolver import Resolver
from backend.ads_ops.transport import SellfoxTransport


@dataclass
class ActionContext:
    resolver: Resolver
    transport: SellfoxTransport


@dataclass
class PreparedAction:
    action: str
    level: str
    resolved: Dict[str, Any]
    before: Any
    after: Any
    request: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    rollback: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionOutcome:
    status: str
    response: Dict[str, Any]
    created: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


class ActionHandler:
    action = ""
    level = "L3"

    def prepare(self, ctx: ActionContext, selector: Dict[str, Any],
                params: Dict[str, Any]) -> PreparedAction:
        raise NotImplementedError

    def recheck(self, ctx: ActionContext, prepared: PreparedAction) -> None:
        """执行前再次校验业务前置条件；默认无操作。"""
        return None

    def execute(self, ctx: ActionContext, prepared: PreparedAction) -> ExecutionOutcome:
        raise NotImplementedError

    def verify(self, ctx: ActionContext, prepared: PreparedAction,
               outcome: ExecutionOutcome) -> Dict[str, Any]:
        raise NotImplementedError

    def rollback_plan(self, ctx: ActionContext, prepared: PreparedAction,
                      outcome: ExecutionOutcome) -> Dict[str, Any]:
        return prepared.rollback or {"available": False, "reason": "L3 动作不可自动回滚"}
