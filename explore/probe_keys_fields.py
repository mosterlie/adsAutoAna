# -*- coding: utf-8 -*-
"""获取各页签首个记录的字段名, 用于确定落库唯一键"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
BASE = "/api/gw/sellfox/sellfox-cpc/api/sellfox/"

FETCH = r"""
async ([url, payload]) => {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(payload), credentials:'include'});
  const j = await r.json();
  const p = (j.data||{}).page || {};
  const row = (p.rows||[])[0];
  return {totalSize:p.totalSize, keys: row?Object.keys(row):null};
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
    P = json.load(open("/Users/gx/Desktop/mypro/adsAutoAna/explore/payloads_all.json", encoding="utf-8"))
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

    eps = {
        "portfolio": "multiple/portfolio/getAllPortfolioData",
        "product": "multiple/adProduct/getAdProductList",
        "placement": "multiple/placement/getAllPlacementData",
        "log": "log/sellfoxAndAuto/getPage",
        "netarget": "multiple/neTarget/getAllNeTargetData",
    }
    S, E = "2026-08-01", "2026-10-04"
    for name, ep in eps.items():
        pl = dict(P[ep])
        pl["startDate"] = S; pl["endDate"] = E
        pl.pop("pageSign", None)
        r = await frame.evaluate(FETCH, [BASE + ep, pl])
        keys = r.get("keys")
        # 只打印可能是ID/名称的字段
        cand = [k for k in (keys or []) if k.lower().endswith(("id", "name", "key")) or k in ("state", "adType", "neType")]
        print(f"\n[{name}] total={r.get('totalSize')}")
        print("  ID候选:", cand)
    await pw.stop()


asyncio.run(main())
