# -*- coding: utf-8 -*-
"""登录 Cookie 管理: 落盘 / 读取 / 从调试 Chrome 同步"""
import json
import os
from typing import Dict, Optional

import config


def save_cookies(cookies: Dict[str, str]) -> None:
    with open(config.COOKIE_PATH, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)


def load_cookies() -> Optional[Dict[str, str]]:
    if not os.path.exists(config.COOKIE_PATH):
        return None
    try:
        with open(config.COOKIE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data else None
    except Exception:
        return None


def parse_cookie_string(raw: str) -> Dict[str, str]:
    """解析浏览器里复制的 'a=1; b=2' 形式 Cookie"""
    out: Dict[str, str] = {}
    for part in (raw or "").split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def sync_from_browser(cdp_url: str = config.CDP_URL) -> Dict[str, str]:
    """从 9222 调试 Chrome 导出 www.sellfox.com 的 Cookie（需已登录赛狐）。

    不经过 Playwright 的 browser context：Chrome 157 已不支持
    Browser.setDownloadBehavior，改用页面级 CDP Network.getAllCookies。
    """
    import json
    import urllib.request

    import websocket

    base = cdp_url.rstrip("/")
    pages = json.load(urllib.request.urlopen(f"{base}/json", timeout=5))
    target = next((p for p in pages if p.get("type") == "page"
                   and "sellfox.com" in (p.get("url") or "")), None) \
        or next((p for p in pages if p.get("type") == "page"
                 and p.get("webSocketDebuggerUrl")), None)
    temp_id = ""
    if not target:
        req = urllib.request.Request(f"{base}/json/new?about:blank", method="PUT")
        target = json.load(urllib.request.urlopen(req, timeout=5))
        temp_id = target.get("id") or ""
    ws_url = (target or {}).get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("调试 Chrome 没有可用页面，请启动 9222 并登录赛狐")
    cookies = []
    try:
        ws = websocket.create_connection(ws_url, max_size=16 * 1024 * 1024, timeout=10)
        try:
            ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
            for _ in range(30):
                msg = json.loads(ws.recv())
                if msg.get("id") != 1:
                    continue
                if msg.get("error"):
                    raise RuntimeError(f"读取 Cookie 失败: {msg['error']}")
                cookies = ((msg.get("result") or {}).get("cookies")) or []
                break
        finally:
            try:
                ws.close()
            except Exception:
                pass
    finally:
        if temp_id:
            try:
                urllib.request.urlopen(f"{base}/json/close/{temp_id}", timeout=5).read()
            except Exception:
                pass

    out = {str(c.get("name")): str(c.get("value") or "")
           for c in cookies
           if c.get("name") and "sellfox.com" in str(c.get("domain") or "")}
    if not out:
        raise RuntimeError("调试 Chrome 未读取到赛狐 Cookie，请先在调试 Chrome 登录赛狐")
    if not out.get("sf_u"):
        raise RuntimeError("调试 Chrome 当前未登录赛狐（缺少 sf_u），请先在调试 Chrome 登录赛狐")
    save_cookies(out)
    return out
