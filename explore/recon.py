# -*- coding: utf-8 -*-
"""赛狐广告管理页侦察: 连接 9222 调试 Chrome, 打开目标页, 报告登录态/框架/DOM 概况"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
TARGET = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
          "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")


async def connect(pw):
    try:
        return await pw.chromium.connect_over_cdp(CDP, timeout=20000)
    except Exception as e:
        es = str(e)
        if "setDownloadBehavior" in es or "context management" in es:
            sys.path.insert(0, "/Users/gx/Desktop/mypro/browser_toolkit")
            from core.cdp_proxy import get_proxy
            proxy = get_proxy(CDP)
            return await pw.chromium.connect_over_cdp(proxy.ws_endpoint, timeout=20000)
        raise


async def main():
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "sellfox" in p.url), None)
    if page is None:
        page = await ctx.new_page()
    await page.goto(TARGET, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)

    print("URL:", page.url)
    print("TITLE:", await page.title())
    print("--- frames ---")
    for f in page.frames:
        print("  ", f.url[:160])
    print("--- body text head ---")
    try:
        txt = await page.evaluate("() => (document.body.innerText||'').slice(0, 1200)")
        print(txt)
    except Exception as e:
        print("evaluate fail:", e)

    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/recon_01.png", full_page=False)
    print("screenshot saved")
    await pw.stop()


asyncio.run(main())
