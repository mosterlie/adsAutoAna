# -*- coding: utf-8 -*-
"""验证 广告组多页分页 + 店铺列表接口内容"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
GROUP = "/api/gw/sellfox/sellfox-cpc/api/sellfox/multiple/group/getAllGroupData"

FETCH_JS = r"""
async ([url, payload]) => {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(payload), credentials:'include'});
  const j = await r.json();
  const p = (j.data||{}).page || {};
  return {code:j.code, totalSize:p.totalSize, totalPage:p.totalPage, pageNo:p.pageNo,
          rows:(p.rows||[]).length, first:(p.rows&&p.rows[0])?p.rows[0].campaignName:null};
}
"""

GET_JS = r"""
async (url) => {
  const r = await fetch(url, {credentials:'include'});
  const j = await r.json();
  return j;
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


async def main():
    contracts = json.load(open("/Users/gx/Desktop/mypro/adsAutoAna/explore/api_contracts.json", encoding="utf-8"))
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None) or await ctx.new_page()
    await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(9)
    frame = None
    for _ in range(40):
        for f in page.frames:
            if "cpc-vue3/web/cpc-vue3/ads-management" in f.url and "isPreload=true" not in f.url:
                try:
                    if await f.evaluate("() => !!document.body && document.body.innerText.includes('广告活动')"):
                        frame = f; break
                except Exception:
                    pass
        if frame:
            break
        await asyncio.sleep(0.4)

    print("=== 广告组分页 ===")
    g = contracts["广告组"]["req"]
    for pno in (1, 2, 3):
        pl = dict(g); pl["pageNo"] = pno
        print(f"  pageNo={pno} ->", json.dumps(await frame.evaluate(FETCH_JS, [GROUP, pl]), ensure_ascii=False))

    print("\n=== 店铺列表 ===")
    shops = await frame.evaluate(GET_JS, "/api/index/customShops.json")
    data = shops.get("data") if isinstance(shops, dict) else shops
    if isinstance(data, list):
        for s in data[:8]:
            print("  ", {k: s.get(k) for k in ("shopId", "shopName", "name", "id", "marketplace", "site") if k in s})
    else:
        print("  ", json.dumps(shops, ensure_ascii=False)[:400])
    await pw.stop()


asyncio.run(main())
