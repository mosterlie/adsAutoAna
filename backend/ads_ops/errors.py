# -*- coding: utf-8 -*-
"""广告写操作统一错误类型。

对外只暴露稳定的 code / message / details，不把 requests 或赛狐原始错误
直接抛给调用方。API 层根据 http_status 转成对应状态码。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


class AdsActionError(RuntimeError):
    code = "action_error"
    http_status = 400

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None,
                 http_status: Optional[int] = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if http_status is not None:
            self.http_status = http_status

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "status": self.code,
            "error": self.code,
            "message": self.message,
            "details": self.details,
        }


class ValidationFailed(AdsActionError):
    code = "validation_error"
    http_status = 422


class NotFound(AdsActionError):
    code = "not_found"
    http_status = 404


class AmbiguousSelector(AdsActionError):
    code = "ambiguous_selector"
    http_status = 409


class NameConflict(AdsActionError):
    code = "name_conflict"
    http_status = 409


class AlreadyInState(AdsActionError):
    code = "already_in_state"
    http_status = 409


class ConfirmationRequired(AdsActionError):
    code = "confirm_required"
    http_status = 428


class ConfirmationInvalid(AdsActionError):
    code = "confirm_invalid"
    http_status = 403


class ConfirmationExpired(AdsActionError):
    code = "confirm_expired"
    http_status = 410


class ActionDisabled(AdsActionError):
    code = "action_disabled"
    http_status = 403


class AuthError(AdsActionError):
    code = "auth_error"
    http_status = 401


class TransportError(AdsActionError):
    code = "transport_error"
    http_status = 502


class SellerAPIError(AdsActionError):
    code = "seller_api_error"
    http_status = 502


class VerificationFailed(AdsActionError):
    code = "verify_failed"
    http_status = 502
