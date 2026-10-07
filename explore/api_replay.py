# -*- coding: utf-8 -*-
"""接口重放验证: 在页面 frame 内用 fetch 直接 POST 数据接口, 验证脱离 UI 也能取数"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
BASE = "/api/gw/sellfox/sellfox-cpc/api/sellfox/"

# 从 api_contracts.json 复用已抓到的真实请求体
CFG = {
    "广告活动": (BASE + "campaign/getAllCampaignData", "ad"),
    "广告组": (BASE + "multiple/group/getAllGroupData", "ad"),
    "搜索词": (BASE + "multiple/search/getAllSearchData", "ad"),
}

FETCH_JS = r"""
async ([url, payload]) => {
  try {
    const r = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
      credentials: 'include'
    });
    const txt = await r.text();
    let j = null;
    try { j = JSON.parse(txt); } catch(e) {}
    const page_ = j && j.data && j.data.page;
    return {
      http: r.status,
      code: j ? j.code : null,
      msg: j ? j.msg : txt.slice(0, 200),
      totalSize: page_ ? page_.totalSize : null,
      rows: page_ && page_.rows ? page_.rows.length : null,
      row0_keys: page_ && page_.rows && page_.rows[0] ? Object.keys(page_.rows[0]).length : null,
      sample: page_ && page_.rows && page_.rows[0] ? {
        name: page_.rows[0].name || page_.rows[0].campaignName || page_.rows[0].adGroupName,
        query: page_.rows[0].query || page_.rows[0].keywordText,
        adCost: page_.rows[0].adCost, adSale: page_.rows[0].adSale,
      } : null,
    };
  } catch (e) {
    return {error: String(e).slice(0, 200)};
  }
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
    if not frame:
        print("no frame"); return
    print("frame:", frame.url[-50:])

    for tab, (url, _) in CFG.items():
        payload = contracts[tab]["req"]
        res = await frame.evaluate(FETCH_JS, [url, payload])
        print(f"\n### {tab} -> {url.split('sellfox/')[-1]}")
        print("   ", json.dumps(res, ensure_ascii=False))
    await pw.stop()


asyncio.run(main())
