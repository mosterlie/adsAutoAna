# -*- coding: utf-8 -*-
"""赛狐广告管理页深度扫描: 健壮切换页签 + 抓取核心列表接口的请求体/响应结构"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
FRAME_KEY = "cpc-vue3/web/cpc-vue3/ads-management"
FULL_URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
            "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
TABS = ["否定投放", "广告位", "广告日志"]

# 各页签主列表接口关键字
LIST_API = {
    "广告组合": "portfolio/getAllPortfolioData",
    "广告活动": "campaign/getAllCampaignData",
    "广告组": "multiple/group/getAllGroupData",
    "广告产品": "multiple/adProduct/getAdProductList",
    "投放": "multiple/target/getAllTargetData",
    "搜索词": "multiple/search/getAllSearchData",
    "否定投放": "multiple/neTarget/getAllNeTargetData",
    "广告位": "zone",
    "广告日志": "log",
}

CLICK_JS = r"""
(tab) => {
  const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const els = [...document.querySelectorAll('*')].filter(
      e => e.children.length === 0 && (e.textContent||'').trim() === tab && vis(e));
  if (!els.length) return null;
  const el = els[0];
  el.scrollIntoView({block:'center'});
  const r = el.getBoundingClientRect();
  el.click();
  return {x: r.x + r.width/2, y: r.y + r.height/2};
}
"""

DUMP_JS = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const clean = s => (s||'').replace(/\s+/g,' ').trim();
  const hdr = [...document.querySelectorAll('.vxe-header--column, th')].filter(vis).map(h=>clean(h.innerText)).filter(Boolean);
  const labels = [...document.querySelectorAll('.el-form-item__label, label, .filter-label')].filter(vis).map(l=>clean(l.innerText)).filter(Boolean);
  const selects = [...document.querySelectorAll('.el-select input, .el-select__placeholder')].filter(vis).map(s=>clean(s.value||s.innerText)).filter(Boolean);
  const btns = [...document.querySelectorAll('button')].filter(vis).map(b=>clean(b.innerText)).filter(t=>t&&t.length<16);
  const rowCount = [...document.querySelectorAll('.vxe-body--row, tbody tr')].filter(vis).length;
  return {hdr:[...new Set(hdr)], labels:[...new Set(labels)], selects:[...new Set(selects)],
          btns:[...new Set(btns)], rowCount};
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
        # 排除预载帧 (tab=Preload&isPreload=true), 只取真正的业务帧
        cands = [f for f in page.frames if FRAME_KEY in f.url and "isPreload=true" not in f.url]
        for f in cands:
            try:
                ok = await f.evaluate("() => !!document.body && document.body.innerText.includes('广告活动')")
                if ok:
                    return f
            except Exception:
                pass
        await asyncio.sleep(0.5)
    return None


async def main():
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None)
    if page is None:
        page = await ctx.new_page()
    await page.goto(FULL_URL, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)

    api_data = {}

    async def on_response(resp):
        u = resp.url
        for tab, key in LIST_API.items():
            if key in u:
                rec = api_data.setdefault(tab, {"url": u, "req": None, "resp": None})
                try:
                    rec["req"] = json.loads(resp.request.post_data) if resp.request.post_data else None
                except Exception:
                    rec["req"] = resp.request.post_data
                try:
                    body = await resp.json()
                    rec["resp"] = body
                except Exception as e:
                    rec["resp"] = f"<non-json: {str(e)[:60]}>"

    page.on("response", lambda r: asyncio.ensure_future(on_response(r)))

    result = {}
    for tab in TABS:
        f = await find_frame(page)
        if not f:
            print(f"[{tab}] no frame"); continue
        pos = None
        try:
            pos = await f.evaluate(CLICK_JS, tab)
        except Exception as e:
            print(f"[{tab}] click err {str(e)[:60]}")
        if pos:
            await page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(5)
        f = await find_frame(page)
        try:
            data = await f.evaluate(DUMP_JS)
            data["frame_url"] = f.url
        except Exception as e:
            data = {"error": str(e)[:80]}
        result[tab] = data
        print(f"\n##### {tab} | rows={data.get('rowCount')} | url={data.get('frame_url','')[-40:]}")
        print("  表头:", data.get("hdr"))
        print("  筛选:", data.get("labels"))
        print("  下拉:", data.get("selects"))
        print("  按钮:", data.get("btns"))

    with open("/Users/gx/Desktop/mypro/adsAutoAna/explore/tabs_scan2.json", "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    with open("/Users/gx/Desktop/mypro/adsAutoAna/explore/api_samples.json", "w", encoding="utf-8") as fh:
        json.dump(api_data, fh, ensure_ascii=False, indent=2)
    print("\n=== API 抓取情况 ===")
    for tab, rec in api_data.items():
        rb = rec.get("resp")
        keys = list(rb.keys())[:8] if isinstance(rb, dict) else type(rb).__name__
        print(f"  {tab}: {rec['url'].split('sellfox/')[-1][:60]} | resp_keys={keys}")
    print("saved tabs_scan2.json / api_samples.json")
    await pw.stop()


asyncio.run(main())
