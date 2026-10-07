# -*- coding: utf-8 -*-
"""测试: pageSign 是否必需 + 全状态 + 新时间范围(2026-08-01~2026-10-04)"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
BASE = "/api/gw/sellfox/sellfox-cpc/api/sellfox/"

FETCH_JS = r"""
async ([url, payload]) => {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
                              body: JSON.stringify(payload), credentials:'include'});
  const j = await r.json();
  const p = (j.data||{}).page || j.data || {};
  return {code:j.code, msg:j.msg, totalSize:(p&&p.totalSize)??null,
          rows:(p&&p.rows)?p.rows.length:null};
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

    S, E = "2026-08-01", "2026-10-04"
    tests = [
        ("广告活动(全状态)", "campaign/getAllCampaignData", {"servingStatus": "", "startDate": S, "endDate": E}),
        ("广告组(去pageSign)", "multiple/group/getAllGroupData", {"pageSign": None, "startDate": S, "endDate": E}),
        ("广告位(去pageSign)", "multiple/placement/getAllPlacementData", {"pageSign": None, "startDate": S, "endDate": E}),
        ("广告日志(去pageSign)", "log/sellfoxAndAuto/getPage", {"pageSign": None, "startDate": S, "endDate": E}),
        ("否定投放", "multiple/neTarget/getAllNeTargetData", {"startDate": S, "endDate": E}),
    ]
    for name, ep, override in tests:
        pl = dict(P[ep])
        for k, v in override.items():
            if v is None:
                pl.pop(k, None)
            else:
                pl[k] = v
        res = await frame.evaluate(FETCH_JS, [BASE + ep, pl])
        print(f"  {name:22s} -> {json.dumps(res, ensure_ascii=False)}")
    await pw.stop()


asyncio.run(main())
