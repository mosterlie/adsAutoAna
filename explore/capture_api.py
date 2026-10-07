# -*- coding: utf-8 -*-
"""抓取各页签主列表接口的完整请求体与响应字段 (爬取契约)"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")

TARGETS = {
    "广告组合": "portfolio/getAllPortfolioData",
    "广告活动": "campaign/getAllCampaignData",
    "广告组": "multiple/group/getAllGroupData",
    "广告产品": "multiple/adProduct/getAdProductList",
    "投放": "multiple/target/getAllTargetData",
    "搜索词": "multiple/search/getAllSearchData",
    "否定投放": "multiple/neTarget/getAllNeTargetData",
}

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

    captured = {}
    pending = []

    def on_response(resp):
        u = resp.url
        for tab, key in TARGETS.items():
            if key in u:
                pending.append((tab, resp))

    async def drain():
        while pending:
            tab, resp = pending.pop(0)
            rec = captured.setdefault(tab, {})
            rec["url"] = resp.url
            try:
                rec["req"] = json.loads(resp.request.post_data) if resp.request.post_data else None
            except Exception:
                rec["req"] = resp.request.post_data
            try:
                body = await resp.json()
                data = body.get("data") if isinstance(body, dict) else body
                page_ = (data or {}).get("page") if isinstance(data, dict) else None
                rec["resp_sample"] = {
                    "page": {k: page_.get(k) for k in ("pageNo", "pageSize", "totalPage", "totalSize")} if page_ else None,
                    "row_keys": list(page_["rows"][0].keys()) if page_ and page_.get("rows") else None,
                    "row0": page_["rows"][0] if page_ and page_.get("rows") else None,
                }
            except Exception as e:
                rec["resp_sample"] = f"<err {str(e)[:60]}>"

    page.on("response", on_response)

    await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(9)

    for tab in TARGETS:
        f = await find_frame(page)
        if not f:
            print(f"[{tab}] no frame"); continue
        pos = await f.evaluate(CLICK_JS, tab)
        if pos:
            await page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(7)
        await drain()
        print(f"[{tab}] captured={tab in captured}")

    with open("/Users/gx/Desktop/mypro/adsAutoAna/explore/api_contracts.json", "w", encoding="utf-8") as fh:
        json.dump(captured, fh, ensure_ascii=False, indent=2)
    print("\nsaved api_contracts.json;", list(captured.keys()))
    await pw.stop()


asyncio.run(main())
