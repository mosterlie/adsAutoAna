# -*- coding: utf-8 -*-
"""针对 否定投放/广告位/广告日志: 重载+点击, 以 XHR 为准捕获数据接口"""
import asyncio
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")

CLICK_JS = r"""
(tab) => {
  const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const els = [...document.querySelectorAll('*')].filter(
      e => e.children.length === 0 && (e.textContent||'').trim() === tab && vis(e));
  if (!els.length) return null;
  const el = els[0]; el.scrollIntoView({block:'center'});
  const r = el.getBoundingClientRect(); el.click();
  return {x: r.x + r.width/2, y: r.y + r.height/2};
}
"""


async def connect(pw):
    try:
        return await pw.chromium.connect_over_cdp(CDP, timeout=20000)
    except Exception as e:
        if "setDownloadBehavior" in str(e) or "context management" in str(e):
            sys.path.insert(0, "/Users/gx/Desktop/mypro/browser_toolkit")
            from core.cdp_proxy import get_proxy
            return await pw.chromium.connect_over_cdp(get_proxy(CDP).ws_endpoint, timeout=20000)
        raise


async def find_frame(page):
    for _ in range(50):
        for f in page.frames:
            if "cpc-vue3/web/cpc-vue3/ads-management" in f.url and "isPreload=true" not in f.url:
                try:
                    if await f.evaluate("() => !!document.body && document.body.innerText.includes('广告活动')"):
                        return f
                except Exception:
                    pass
        await asyncio.sleep(0.4)
    return None


async def main():
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None) or await ctx.new_page()

    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if "sellfox-cpc" in r.url or "/cpcManage/" in r.url else None)

    for tab in ["否定投放", "广告位", "广告日志"]:
        reqs.clear()
        await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(9)
        f = await find_frame(page)
        if not f:
            print(f"[{tab}] no frame"); continue
        pos = await f.evaluate(CLICK_JS, tab)
        if pos:
            await page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(12)
        print(f"\n##### {tab} #####")
        for u in dict.fromkeys(reqs):
            print("   ", u.split("sellfox/")[-1][:80])
    await pw.stop()


asyncio.run(main())
