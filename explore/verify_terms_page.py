# -*- coding: utf-8 -*-
"""验证搜索词列表页: ①指标列可排序 ②仅标题/详情按钮跳转"""
import asyncio

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8320"
ASIN = "B0HJ1MX7Y4"


async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    ctx = await b.new_context(viewport={"width": 1500, "height": 950})
    errs = []
    page = await ctx.new_page()
    page.on("console", lambda m: errs.append(m.text[:160]) if m.type == "error" else None)
    page.on("pageerror", lambda e: errs.append("PAGEERROR " + str(e)[:160]))

    await page.goto(f"{BASE}/?page=terms&asin={ASIN}", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)

    def col(idx):
        return f"#tWrap tbody tr:nth-child({idx}) td:nth-child(5)"

    print("行数:", await page.evaluate("() => document.querySelectorAll('#tWrap tr.t-row').length"))
    print("可排序表头数:", await page.evaluate("() => document.querySelectorAll('#tWrap th[data-sort]').length"))

    # ① 点「曝光」表头 -> 倒序
    await page.click('#tWrap th[data-sort="impressions"]')
    await page.wait_for_timeout(400)
    imp = await page.evaluate(
        "() => [...document.querySelectorAll('#tWrap tbody tr')].map(tr=>Number(tr.children[4].innerText.replace(/,/g,'')))")
    print("点曝光表头(desc): 前5 =", imp[:5], "| 递减?", all(imp[i] >= imp[i + 1] for i in range(len(imp) - 1)))
    await page.click('#tWrap th[data-sort="impressions"]')
    await page.wait_for_timeout(400)
    imp2 = await page.evaluate(
        "() => [...document.querySelectorAll('#tWrap tbody tr')].map(tr=>Number(tr.children[4].innerText.replace(/,/g,'')))")
    print("再点一次(asc): 前5 =", imp2[:5], "| 递增?", all(imp2[i] <= imp2[i + 1] for i in range(len(imp2) - 1)))

    # ① 文本列排序(搜索词)
    await page.click('#tWrap th[data-sort="query"]')
    await page.wait_for_timeout(400)
    q = await page.evaluate("() => [...document.querySelectorAll('#tWrap tbody tr')].slice(0,4).map(tr=>tr.children[1].innerText.trim())")
    print("按搜索词排序:", q)

    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/terms_sorted.png")

    # ② 点普通单元格 -> 不应跳转
    n0 = len(ctx.pages)
    await page.click("#tWrap tbody tr:nth-child(3) td:nth-child(3)")
    await page.wait_for_timeout(1800)
    print(f"点普通单元格: 页签数 {n0} -> {len(ctx.pages)} (应不变)")

    # ② 点「详情」按钮 -> 跳转
    async with ctx.expect_page() as p1:
        await page.click("#tWrap tbody tr:nth-child(1) td:nth-child(10) button")
    np = await p1.value
    await np.wait_for_load_state("domcontentloaded")
    print("点详情按钮 ->", np.url[:100])
    await np.close()

    # ② 点搜索词文字 -> 跳转
    async with ctx.expect_page() as p2:
        await page.click("#tWrap tbody tr:nth-child(2) td:nth-child(2) .kw-link")
    np2 = await p2.value
    await np2.wait_for_load_state("domcontentloaded")
    print("点搜索词文字 ->", np2.url[:100])
    await np2.close()

    print("控制台错误:", errs[:5] or "无")
    await b.close(); await pw.stop()


asyncio.run(main())
