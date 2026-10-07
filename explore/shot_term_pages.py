# -*- coding: utf-8 -*-
"""验证并截图: 搜索词列表页 / 搜索词详情页签"""
import asyncio
import sys
import urllib.parse

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8321"
ASIN = "B0HJ1MX7Y4"
TERM = "金網フェンス"


async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    ctx = await b.new_context(viewport={"width": 1500, "height": 950})

    # 预热亚马逊搜索结果(避免页面等待过久)
    warm = await ctx.new_page()
    await warm.goto(f"{BASE}/api/amazon/search?query={urllib.parse.quote(TERM)}",
                    wait_until="domcontentloaded", timeout=120000)
    await warm.wait_for_timeout(3000)
    await warm.close()

    errs = []
    page = await ctx.new_page()
    page.on("console", lambda m: errs.append(m.text[:180]) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)[:180]))
    await page.set_viewport_size({"width": 1500, "height": 950})

    # 1) 搜索词列表页
    await page.goto(f"{BASE}/?page=terms&asin={ASIN}", wait_until="networkidle")
    await page.wait_for_timeout(2500)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/term_list.png")
    print("列表页 行数:", await page.evaluate("() => document.querySelectorAll('#tWrap tr.t-row').length"))
    print("列表页 meta:", await page.evaluate("() => (document.getElementById('tMeta')||{}).innerText"))

    # 2) 搜索词详情页
    await page.goto(f"{BASE}/?page=term&asin={ASIN}&term={urllib.parse.quote(TERM)}",
                    wait_until="networkidle")
    await page.wait_for_timeout(18000)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/term_detail.png",
                          full_page=False)
    print("详情页 指标卡:", await page.evaluate("() => document.querySelectorAll('.tm-card').length"))
    print("详情页 附图缩略图:", await page.evaluate("() => document.querySelectorAll('.tg-thumbs img').length"))
    print("详情页 结果卡:", await page.evaluate("() => document.querySelectorAll('.amz-card2').length"))
    print("详情页 标题:", await page.evaluate("() => (document.querySelector('.tg-title')||{}).innerText"))
    print("详情页 价格:", await page.evaluate("() => (document.querySelector('.tg-price')||{}).innerText"))
    print("卡片meta:", await page.evaluate("() => (document.getElementById('amzCardsMeta')||{}).innerText"))
    print("控制台错误:", errs[:6] or "无")
    await page.close()
    await pw.stop()


asyncio.run(main())
