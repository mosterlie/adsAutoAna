# -*- coding: utf-8 -*-
"""验证完整链路: 产品视角 → 点「搜索词」开新页面 → 点词开新页签"""
import asyncio

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8321"


async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    ctx = await b.new_context(viewport={"width": 1500, "height": 950})
    errs = []
    page = await ctx.new_page()
    page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)[:160]))

    await page.goto(BASE + "/", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    # 切到产品视角
    await page.click('#viewSwitch .seg-btn[data-view="product"]')
    await page.wait_for_timeout(3500)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/products_view.png")
    n = await page.evaluate("() => document.querySelectorAll('#pWrap tbody tr').length")
    print("产品视角行数:", n)
    print("搜索词按钮数:", await page.evaluate("() => document.querySelectorAll('[data-act=\"pterms\"]').length"))

    # 点第一个「搜索词」→ 应弹出新页面
    async with ctx.expect_page() as pinfo:
        await page.click('[data-act="pterms"]')
    newp = await pinfo.value
    await newp.wait_for_load_state("domcontentloaded")
    await newp.wait_for_timeout(3000)
    print("新页面 URL:", newp.url)
    print("新页面行数:", await newp.evaluate("() => document.querySelectorAll('#tWrap tr.t-row').length"))

    # 在新页面点第一个词 → 应弹出新页签
    async with ctx.expect_page() as pinfo2:
        await newp.click("#tWrap tr.t-row")
    tp = await pinfo2.value
    await tp.wait_for_load_state("domcontentloaded")
    await tp.wait_for_timeout(20000)
    print("详情页 URL:", tp.url[:120])
    print("详情页 指标卡:", await tp.evaluate("() => document.querySelectorAll('.tm-card').length"))
    print("详情页 附图:", await tp.evaluate("() => document.querySelectorAll('.tg-thumbs img').length"))
    print("详情页 结果卡:", await tp.evaluate("() => document.querySelectorAll('.amz-card2').length"))
    await tp.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/term_detail_from_flow.png")
    print("控制台错误:", errs[:5] or "无")
    await b.close(); await pw.stop()


asyncio.run(main())
