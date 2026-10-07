# -*- coding: utf-8 -*-
"""打开本地前端并截图, 校验渲染"""
import asyncio
import sys

from playwright.async_api import async_playwright


async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=20000)
    ctx = b.contexts[0]
    page = await ctx.new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)[:160]))
    await page.set_viewport_size({"width": 1440, "height": 900})
    await page.goto("http://127.0.0.1:8320/", wait_until="networkidle")
    await asyncio.sleep(3)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/ui_campaign.png")
    # 切到搜索词
    await page.click('.tab[data-tab="search"]')
    await asyncio.sleep(2.5)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/ui_search.png")
    # 切到广告组
    await page.click('.tab[data-tab="group"]')
    await asyncio.sleep(2.5)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/ui_group.png")
    print("表头首个:", await page.evaluate("() => document.querySelector('#headRow')?.innerText?.replace(/\\n/g,'|')"))
    print("行数:", await page.evaluate("() => document.querySelectorAll('#bodyRow tr').length"))
    print("chips:", await page.evaluate("() => document.getElementById('chips')?.innerText?.replace(/\\n/g,' ')"))
    print("控制台错误:", errs[:5] or "无")
    await page.close()
    await pw.stop()


asyncio.run(main())
