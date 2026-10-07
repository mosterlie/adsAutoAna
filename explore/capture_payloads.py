# -*- coding: utf-8 -*-
"""抓取 9 个页签所有数据接口的请求体 (全状态基准载荷)"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
KEY = "sellfox-cpc/api/sellfox/"
TABS = ["广告组合", "广告活动", "广告组", "广告产品", "投放", "搜索词", "否定投放", "广告位", "广告日志"]

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

    payloads = {}

    def on_req(r):
        if KEY in r.url and r.method == "POST" and r.post_data:
            ep = r.url.split(KEY)[-1]
            try:
                payloads[ep] = json.loads(r.post_data)
            except Exception:
                payloads[ep] = r.post_data

    page.on("request", on_req)
    await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(9)

    for tab in TABS:
        f = await find_frame(page)
        if not f:
            print(f"[{tab}] no frame"); continue
        pos = await f.evaluate(CLICK_JS, tab)
        if pos:
            await page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(7)
        print(f"[{tab}] done")

    with open("/Users/gx/Desktop/mypro/adsAutoAna/explore/payloads.json", "w", encoding="utf-8") as fh:
        json.dump(payloads, fh, ensure_ascii=False, indent=2)
    print("\n=== 抓到端点 ===")
    for ep in payloads:
        print("  ", ep)
    await pw.stop()


asyncio.run(main())
