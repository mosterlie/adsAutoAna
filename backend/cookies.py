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
    """从 9222 调试 Chrome 导出 www.sellfox.com 的 Cookie (需已登录赛狐)

    依赖 playwright; 只做 Cookie 导出, 数据采集本身走 requests。
    """
    import asyncio
    from playwright.async_api import async_playwright

    async def _run():
        pw = await async_playwright().start()
        try:
            try:
                browser = await pw.chromium.connect_over_cdp(cdp_url, timeout=20000)
            except Exception as e:      # Chrome 154+ 握手兼容
                if "setDownloadBehavior" in str(e) or "context management" in str(e):
                    import sys
                    sys.path.insert(0, os.path.join(os.path.dirname(config.BASE_DIR), "browser_toolkit"))
                    from core.cdp_proxy import get_proxy   # noqa
                    browser = await pw.chromium.connect_over_cdp(
                        get_proxy(cdp_url).ws_endpoint, timeout=20000)
                else:
                    raise
            ctx = browser.contexts[0]
            cookies = await ctx.cookies("https://www.sellfox.com")
            return {c["name"]: c["value"] for c in cookies if c.get("name")}
        finally:
            await pw.stop()

    cookies = asyncio.run(_run())
    if not cookies:
        raise RuntimeError("未能从调试浏览器读取到 Cookie, 请确认已启动 9222 并登录赛狐")
    save_cookies(cookies)
    return cookies
