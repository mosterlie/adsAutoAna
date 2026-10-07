# -*- coding: utf-8 -*-
"""按真实视口截图: 搜索词列表页 / 搜索词详情页, 便于核对字段布局宽度"""
import asyncio
import sys

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8320"
ASIN = "B0HJ1MX7Y4"
TERM = "金網フェンス"


async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    ctx = await b.new_context(viewport={"width": 1440, "height": 900})
    page = await ctx.new_page()

    await page.goto(f"{BASE}/?page=terms&asin={ASIN}", wait_until="domcontentloaded")
    await page.wait_for_timeout(2500)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/layout_terms.png")
    # 各列实测宽度
    w = await page.evaluate("""() => {
      const th = [...document.querySelectorAll('#tWrap thead th')];
      return th.map(t => ({t: (t.innerText||'').split('\\n')[0], w: Math.round(t.getBoundingClientRect().width),
                           h: Math.round(t.getBoundingClientRect().height)}));
    }""")
    print("搜索词列表页 列宽/行高:")
    for x in w:
        print(f"   {x['t']:<16} w={x['w']:>4} h={x['h']}")
    print("表宽:", await page.evaluate("() => Math.round(document.querySelector('#tWrap table').getBoundingClientRect().width)"))
    print("容器宽:", await page.evaluate("() => Math.round(document.querySelector('#tWrap').clientWidth)"))

    await page.goto(f"{BASE}/?page=term&asin={ASIN}&term={TERM}", wait_until="domcontentloaded")
    await page.wait_for_timeout(16000)
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/layout_termdetail.png")
    await b.close(); await pw.stop()


asyncio.run(main())
