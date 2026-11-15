# -*- coding: utf-8 -*-
"""L3 统一执行器：预览 → 确认令牌 → 执行 → 读回 → 审计。

安全边界
--------
* 执行前必须持有 preview 返回的 confirm_token；
* L3 默认关闭，需 ADS_L3_ENABLED=1；
* 创建活动不自动重试，避免重复创建；
* 同一进程内的 L3 执行串行化，避免并发查重通过后重复创建。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from backend.ads_ops import audit, policy
from backend.ads_ops.actions import get_handler
from backend.ads_ops.actions.base import ActionContext, PreparedAction
from backend.ads_ops.errors import (AdsActionError, AlreadyInState,
                                    ConfirmationExpired, ConfirmationInvalid,
                                    NameConflict, TransportError,
                                    ValidationFailed, VerificationFailed)
from backend.ads_ops.resolver import Resolver
from backend.ads_ops.transport import SellfoxTransport

_EXEC_LOCK = threading.Lock()


class Executor:
    def __init__(self, transport: Optional[SellfoxTransport] = None,
                 resolver: Optional[Resolver] = None,
                 shop_id: str = "711811",
                 marketplace_id: str = "A1VC38T7YXB528"):
        self._transport = transport
        self._resolver = resolver
        self.shop_id = shop_id
        self.marketplace_id = marketplace_id

    def _ctx(self) -> ActionContext:
        transport = self._transport or SellfoxTransport()
        resolver = self._resolver or Resolver(transport, self.shop_id,
                                               self.marketplace_id)
        return ActionContext(resolver=resolver, transport=transport)

    # ------------------------------------------------------------------
    # preview
    # ------------------------------------------------------------------
    def preview(self, action: str, selector: Dict[str, Any],
                params: Dict[str, Any], *, source: str = "api",
                actor: str = "") -> Dict[str, Any]:
        meta = policy.action_meta(action)
        handler = get_handler(action)
        ctx = self._ctx()
        prepared = handler.prepare(ctx, selector or {}, params or {})
        request_id = "act_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:8]
        token_info = audit.create_preview(
            request_id=request_id, action=action,
            level=str(meta.get("level") or "L3"),
            selector=selector or {}, params=params or {},
            payload_hash=audit.canonical_hash(action, selector or {}, params or {}),
            resolved=prepared.resolved, before=prepared.before,
            after=prepared.after, request_payload=prepared.request,
            warnings=prepared.warnings, rollback=prepared.rollback,
            source=source, actor=actor)
        return {
            "ok": True,
            "status": "preview",
            "request_id": request_id,
            "action": action,
            "level": meta.get("level"),
            "description": meta.get("desc"),
            "resolved": prepared.resolved,
            "before": prepared.before,
            "after": prepared.after,
            "warnings": prepared.warnings,
            "rollback": prepared.rollback,
            **token_info,
            "note": "L3 动作必须人工确认；execute 时携带 confirm_token",
        }

    # ------------------------------------------------------------------
    # execute
    # ------------------------------------------------------------------
    def execute(self, confirm_token: str, *, action: str = "",
                selector: Optional[Dict[str, Any]] = None,
                params: Optional[Dict[str, Any]] = None,
                source: str = "api", actor: str = "") -> Dict[str, Any]:
        record = audit.get_by_token(confirm_token)
        if not record:
            raise ConfirmationInvalid("confirm_token 不存在")
        action_name = str(record.get("action") or action or "")
        policy.require_execution_enabled(action_name)
        handler = get_handler(action_name)

        if record.get("status") != "preview" or record.get("used_at"):
            raise ConfirmationInvalid("confirm_token 已被使用或不是 preview 状态")
        expires_at = record.get("expires_at")
        try:
            if expires_at and datetime.fromisoformat(str(expires_at)) < datetime.now():
                raise ConfirmationExpired("confirm_token 已过期，请重新 preview")
        except ValueError:
            raise ConfirmationInvalid("confirm_token 过期时间格式异常")

        if action and action != action_name:
            raise ConfirmationInvalid("action 与 confirm_token 不匹配")
        if selector is not None or params is not None:
            expected = audit.canonical_hash(action_name, record.get("selector") or {},
                                            record.get("params") or {})
            got = audit.canonical_hash(action_name, selector or {}, params or {})
            if expected != got:
                raise ConfirmationInvalid("selector/params 与 preview 不一致")

        prepared = PreparedAction(
            action=action_name,
            level=str(record.get("level") or "L3"),
            resolved=record.get("resolved") or {},
            before=record.get("before"),
            after=record.get("after"),
            request=record.get("request") or {},
            warnings=record.get("warnings") or [],
            rollback=record.get("rollback") or {},
        )
        ctx = self._ctx()
        request_id = record["request_id"]

        # L3 串行化：避免两个 preview 同时通过查重后重复创建
        with _EXEC_LOCK:
            try:
                handler.recheck(ctx, prepared)
            except AdsActionError as exc:
                audit.finish(request_id, status=exc.code, verified=False,
                             error=exc.to_dict())
                raise
            if not audit.mark_started(request_id):
                raise ConfirmationInvalid("confirm_token 已被占用")

            try:
                outcome = handler.execute(ctx, prepared)
                # Amazon 读回存在短暂延迟：最多轮询 3 次，每次间隔 2 秒
                verification = None
                for attempt in range(3):
                    ctx.resolver.clear_cache()
                    try:
                        verification = handler.verify(ctx, prepared, outcome)
                        break
                    except VerificationFailed:
                        if attempt == 2:
                            raise
                        time.sleep(2)
                verified = bool(verification.get("verified"))
                status = outcome.status if verified else "verify_failed"
                if outcome.status == "partial_success":
                    status = "partial_success"
                rollback = handler.rollback_plan(ctx, prepared, outcome)
                audit.finish(request_id, status=status, verified=verified,
                             response=outcome.response, after=verification,
                             rollback=rollback)
                return {
                    "ok": bool(verified and outcome.status == "applied"),
                    "status": status,
                    "applied": True,
                    "verified": verified,
                    "request_id": request_id,
                    "action": action_name,
                    "before": prepared.before,
                    "after": verification,
                    "created": outcome.created,
                    "warnings": list(prepared.warnings) + list(outcome.warnings),
                    "rollback": rollback,
                    "response": outcome.response,
                }
            except AlreadyInState as exc:
                audit.finish(request_id, status="already_applied", verified=True,
                             error=exc.to_dict())
                return {
                    "ok": True, "status": "already_applied", "applied": False,
                    "verified": True, "request_id": request_id,
                    "action": action_name, "message": exc.message,
                    "details": exc.details,
                }
            except VerificationFailed as exc:
                audit.finish(request_id, status="verify_failed", verified=False,
                             error=exc.to_dict())
                raise
            except TransportError as exc:
                # 创建是非幂等动作，超时/连接中断后不能断定未发出
                status = "unknown" if action_name == "create_campaign" else "failed"
                audit.finish(request_id, status=status, verified=False,
                             error=exc.to_dict())
                raise AdsActionError(
                    exc.message,
                    details={**exc.details,
                             "retryable": action_name != "create_campaign",
                             "hint": ("不要自动重试；先按活动名查重，确认是否已创建"
                                      if action_name == "create_campaign" else "可重新 preview 后重试")},
                    http_status=exc.http_status) from exc
            except AdsActionError as exc:
                audit.finish(request_id, status=exc.code, verified=False,
                             error=exc.to_dict())
                raise
            except Exception as exc:  # noqa: BLE001
                audit.finish(request_id, status="failed", verified=False,
                             error={"message": str(exc)[:500]})
                raise AdsActionError(f"执行异常: {str(exc)[:300]}") from exc

    # ------------------------------------------------------------------
    # log
    # ------------------------------------------------------------------
    def log(self, request_id: str) -> Dict[str, Any]:
        record = audit.get_by_request(request_id)
        if not record:
            raise ValidationFailed("request_id 不存在")
        return audit.public_log(record)
