# -*- coding: utf-8 -*-
"""点击后 3 个页签, 打印真实路由键名与帧内容"""
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

DUMP_JS = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const clean = s => (s||'').replace(/\s+/g,' ').trim();
  return {
    hdr:[...new Set([...document.querySelectorAll('.vxe-header--column, th')].filter(vis).map(h=>clean(h.innerText)).filter(Boolean))],
    selects:[...new Set([...document.querySelectorAll('.el-select input, .el-select__placeholder')].filter(vis).map(s=>clean(s.value||s.innerText)).filter(Boolean))],
    btns:[...new Set([...document.querySelectorAll('button')].filter(vis).map(b=>clean(b.innerText)).filter(t=>t&&t.length<16))],
    rowCount:[...document.querySelectorAll('.vxe-body--row, tbody tr')].filter(vis).length,
  };
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
    for _ in range(40):
        for f in page.frames:
            if "cpc-vue3/web/cpc-vue3/ads-management" in f.url and "isPreload=true" not in f.url:
                try:
                    if await f.evaluate("() => !!document.body && document.body.innerText.includes('广告活动')"):
                        return f
                except Exception:
                    pass
        await asyncio.sleep(0.5)
    return None


async def main():
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None) or await ctx.new_page()
    await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)

    for tab in ["否定投放", "广告位", "广告日志"]:
        f = await find_frame(page)
        pos = await f.evaluate(CLICK_JS, tab) if f else None
        if pos:
            await page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(9)
        print(f"\n##### {tab} #####")
        for fr in page.frames:
            if "cpc-vue3" in fr.url and "isPreload" not in fr.url:
                print("  URL:", fr.url[-70:])
        f2 = await find_frame(page)
        if f2:
            try:
                print("  DUMP:", await f2.evaluate(DUMP_JS))
            except Exception as e:
                print("  dump fail", str(e)[:60])
    await pw.stop()


asyncio.run(main())
