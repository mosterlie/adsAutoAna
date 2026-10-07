# -*- coding: utf-8 -*-
"""逐页签扫描赛狐广告管理页: 点击每个页签 -> 提取筛选/表头/工具栏/样例行, 并抓取 XHR"""
import asyncio
import json
import sys
import time

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
FRAME_KEY = "cpc-vue3/web/cpc-vue3/ads-management"
SERVER = "cpc-vue3/web/cpc-vue3/ads-management?openfrom=clickmenu"
TABS = ["广告组合", "广告活动", "广告组", "广告产品", "投放", "搜索词", "否定投放", "广告位", "广告日志"]

DUMP_JS = r"""
() => {
  const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
  const clean = s => (s||'').replace(/\s+/g,' ').trim();
  const hdr = [...document.querySelectorAll('.vxe-header--column, th')].filter(vis)
      .map(h => clean(h.innerText)).filter(Boolean);
  const labels = [...document.querySelectorAll('.el-form-item__label, label, .filter-label')].filter(vis)
      .map(l => clean(l.innerText)).filter(Boolean);
  const selects = [...document.querySelectorAll('.el-select input, .el-select__placeholder')].filter(vis)
      .map(s => clean(s.value || s.innerText)).filter(Boolean);
  const btns = [...document.querySelectorAll('button')].filter(vis)
      .map(b => clean(b.innerText)).filter(t => t && t.length < 16);
  const rows = [...document.querySelectorAll('.vxe-body--row, tbody tr')].filter(vis).slice(0, 3)
      .map(r => clean(r.innerText).slice(0, 260));
  const rowCount = [...document.querySelectorAll('.vxe-body--row, tbody tr')].filter(vis).length;
  // 侧栏 (组合树)
  const asideText = clean((document.querySelector('aside, [class*=aside], [class*=sidebar]')||{}).innerText||'').slice(0,300);
  return {hdr:[...new Set(hdr)], labels:[...new Set(labels)], selects:[...new Set(selects)],
          btns:[...new Set(btns)], rows, rowCount, asideText};
}
"""

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
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None)
    if page is None:
        print("no sellfox page")
        return

    xhr = []
    page.on("request", lambda r: xhr.append((r.method, r.url)) if any(
        k in r.url for k in ("/api/", "cpc", "campaign", "searchterm", "searchTerm",
                             "adgroup", "adGroup", "target", "keyword", "report", "query")) else None)

    frame = next((f for f in page.frames if FRAME_KEY in f.url), None)
    print("frame:", frame.url[:120] if frame else None)

    result = {}
    for tab in TABS:
        before = len(xhr)
        pos = None
        try:
            pos = await frame.evaluate(CLICK_JS, tab)
        except Exception as e:
            print(f"[{tab}] click err: {str(e)[:80]}")
        if pos:
            # 真实鼠标点击兜底 (Vue 有时不认合成 click)
            await page.mouse.click(pos["x"], pos["y"])
        await asyncio.sleep(4)
        try:
            data = await frame.evaluate(DUMP_JS)
        except Exception as e:
            data = {"error": str(e)[:120]}
        data["frame_url"] = frame.url
        data["new_xhr"] = [f"{m} {u[:150]}" for m, u in xhr[before:]]
        result[tab] = data
        print(f"\n########## {tab} (rows={data.get('rowCount')}) ##########")
        print("  url:", data.get("frame_url", "")[:110])
        print("  表头:", data.get("hdr"))
        print("  筛选标签:", data.get("labels"))
        print("  下拉值:", data.get("selects"))
        print("  按钮:", data.get("btns"))
        print("  侧栏:", data.get("asideText", "")[:160])
        print("  新XHR:", data.get("new_xhr")[:8])
        try:
            await frame.page.screenshot(path=f"/Users/gx/Desktop/mypro/adsAutoAna/explore/tab_{tab}.png")
        except Exception:
            pass

    with open("/Users/gx/Desktop/mypro/adsAutoAna/explore/tabs_scan.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\nsaved explore/tabs_scan.json")
    await pw.stop()


asyncio.run(main())
