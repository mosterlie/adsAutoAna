# -*- coding: utf-8 -*-
"""直接带 forceRefreshTab 导航, 确认 广告位/广告日志 路由键"""
import asyncio
import sys
from urllib.parse import quote

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = "https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3?amzup-web-cpc-vue3="
INNER = "/cpc-vue3/web/cpc-vue3/ads-management?openfrom=clickmenu&tab={t}&forceRefreshTab=true"

DUMP = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const clean = s => (s||'').replace(/\s+/g,' ').trim();
  return {hdr:[...new Set([...document.querySelectorAll('.vxe-header--column, th')].filter(vis).map(h=>clean(h.innerText)).filter(Boolean))],
          selects:[...new Set([...document.querySelectorAll('.el-select input, .el-select__placeholder')].filter(vis).map(s=>clean(s.value||s.innerText)).filter(Boolean))],
          btns:[...new Set([...document.querySelectorAll('button')].filter(vis).map(b=>clean(b.innerText)).filter(t=>t&&t.length<16))],
          rows:[...document.querySelectorAll('.vxe-body--row, tbody tr')].filter(vis).length};
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


async def frame_for(page):
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
    for t in ["Placement", "AdLog", "Log", "Zone", "NeTarget", "Portfolio"]:
        await page.goto(WRAP + quote(INNER.format(t=t), safe=""), wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(8)
        f = await frame_for(page)
        if not f:
            print(f"[{t}] no frame"); continue
        try:
            d = await f.evaluate(DUMP)
        except Exception as e:
            print(f"[{t}] dump fail {str(e)[:50]}"); continue
        print(f"[{t}] rows={d['rows']} hdr={d['hdr'][:6]} sel={d['selects'][:6]}")
    await pw.stop()


asyncio.run(main())
