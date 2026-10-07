# -*- coding: utf-8 -*-
"""直接以 tab 参数导航各页签, 验证路由键名并取列名/筛选"""
import asyncio
import json
import sys
from urllib.parse import quote

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
INNER = "/cpc-vue3/web/cpc-vue3/ads-management?openfrom=clickmenu&tab="
WRAP = "https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3?amzup-web-cpc-vue3="


def url_for(tab):
    return WRAP + quote(INNER + tab, safe="")


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

    out = {}
    # tab 键名候选: 否定投放/广告位/广告日志
    for tab in ["NeTarget", "NegTarget", "Placement", "Zone", "AdZone", "Log", "AdLog", "CampaignLog"]:
        await page.goto(url_for(tab), wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(6)
        f = await find_frame(page)
        if not f:
            print(f"[{tab}] no frame"); continue
        try:
            d = await f.evaluate(DUMP_JS)
        except Exception as e:
            print(f"[{tab}] dump fail {str(e)[:60]}"); continue
        print(f"\n[{tab}] rows={d['rowCount']} url={f.url[-36:]}")
        print("  表头:", d["hdr"][:26])
        print("  下拉:", d["selects"])
        print("  按钮:", d["btns"])
        out[tab] = d
    with open("/Users/gx/Desktop/mypro/adsAutoAna/explore/tabs_scan3.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    await pw.stop()


asyncio.run(main())
