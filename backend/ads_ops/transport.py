# -*- coding: utf-8 -*-
"""赛狐写接口传输层。

只负责：URL 选择、JSON/form 编码、Cookie、超时、可选的幂等重试。
业务校验、确认令牌、读回校验都在上层完成。
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

import requests

import config
from backend.cookies import load_cookies
from backend.ads_ops.errors import AuthError, SellerAPIError, TransportError

ORIGIN = "https://www.sellfox.com"
GW = ORIGIN + "/api/gw/sellfox/sellfox-cpc/api/sellfox"


def _headers(content_type: str) -> Dict[str, str]:
    return {
        "Content-Type": content_type,
        "Accept": "application/json, text/plain, */*",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/cpc-vue3/web/cpc-vue3/ad-create/index.html",
        "X-Language": "zh-CN",
        "User-Agent": config.USER_AGENT,
    }


class SellfoxTransport:
    def __init__(self, cookies: Optional[Dict[str, str]] = None,
                 timeout: int = 60, retries: int = 1):
        self.cookies = cookies if cookies is not None else load_cookies()
        self.timeout = timeout
        self.retries = max(0, int(retries))
        self.session = requests.Session()
        if not self.cookies:
            raise AuthError("未找到赛狐登录 Cookie，请先同步登录态")

    def _url(self, gateway: str, path: str) -> str:
        if str(path).startswith("http"):
            return str(path)
        if gateway == "old" or str(path).startswith("/"):
            return ORIGIN + ("" if str(path).startswith("/") else "/") + str(path)
        return GW + "/" + str(path).lstrip("/")

    def request(self, gateway: str, path: str, payload: Dict[str, Any],
                *, encoding: str = "json", retry: bool = True,
                timeout: Optional[int] = None) -> Dict[str, Any]:
        url = self._url(gateway, path)
        headers = _headers("application/x-www-form-urlencoded"
                           if encoding == "form" else "application/json")
        attempts = 1 + (self.retries if retry else 0)
        last: Optional[Exception] = None

        for attempt in range(attempts):
            try:
                kwargs: Dict[str, Any] = {
                    "headers": headers,
                    "cookies": self.cookies,
                    "timeout": timeout or self.timeout,
                }
                if encoding == "form":
                    kwargs["data"] = {k: _form_value(v) for k, v in payload.items()
                                      if v is not None}
                else:
                    kwargs["json"] = payload
                r = self.session.post(url, **kwargs)
                if r.status_code != 200:
                    last = TransportError(f"赛狐 HTTP {r.status_code}")
                    if r.status_code >= 500 and attempt + 1 < attempts:
                        time.sleep(1.0 * (attempt + 1))
                        continue
                    raise last
                try:
                    data = r.json()
                except json.JSONDecodeError as exc:
                    raise TransportError("赛狐返回的不是 JSON") from exc
                code = data.get("code")
                if code == 2001:
                    raise AuthError("赛狐登录态失效（code=2001），请重新同步 Cookie")
                if code != 0:
                    raise SellerAPIError(
                        f"赛狐业务错误 code={code}: {data.get('msg') or ''}",
                        details={"seller_code": code, "msg": data.get("msg")})
                return data
            except AuthError:
                raise
            except SellerAPIError:
                raise
            except TransportError as exc:
                last = exc
                if attempt + 1 >= attempts:
                    raise
                time.sleep(1.0 * (attempt + 1))
            except requests.RequestException as exc:
                last = exc
                if attempt + 1 >= attempts:
                    raise TransportError(f"赛狐请求失败: {str(exc)[:240]}") from exc
                time.sleep(1.0 * (attempt + 1))
        raise TransportError(f"赛狐请求失败: {str(last)[:240]}")

    def post(self, path: str, payload: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return self.request("gw", path, payload, **kwargs)


def _form_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
