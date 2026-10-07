# -*- coding: utf-8 -*-
"""抓取各页签列配置(字段中文名), 供前端还原表头"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
KEYS = [
    "ad_manage_portfolio_comprehensive_field",
    "ad_manage_campaign_comprehensive_field",
    "ad_manage_group_comprehensive_field",
    "ad_manage_product_comprehensive_field",
    "ad_manage_target_comprehensive_field",
    "ad_manage_search_word_comprehensive_field",
    "ad_manage_ne_target_comprehensive_field",
    "ad_manage_placement_comprehensive_field",
    "ad_manage_log_comprehensive_field",
]


async def main():
    pw = await async_playwright().start()
    b = await pw.chromium.connect_over_cdp(CDP, timeout=20000)
    pg = next((p for p in b.contexts[0].pages if "cpc" in p.url and "sellfox" in p.url), None)
    await pg.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(8)
    out = {}
    for k in KEYS:
        try:
            txt = await pg.evaluate(
                "async (k)=>{const r=await fetch('/api/customize/chart/get.json?key='+k,{credentials:'include'});return await r.text();}", k)
            j = json.loads(txt)
            out[k] = j
            d = j.get("data")
            n = len(d) if isinstance(d, list) else (len(d.get("fields", [])) if isinstance(d, dict) else 0)
            print(f"{k}: code={j.get('code')} len={n}")
        except Exception as e:
            print(k, "ERR", str(e)[:60])
    json.dump(out, open("/Users/gx/Desktop/mypro/adsAutoAna/explore/column_configs.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    # 打印 campaign 结构
    c = out.get("ad_manage_campaign_comprehensive_field", {}).get("data")
    print("\ncampaign data type:", type(c).__name__)
    if isinstance(c, list) and c:
        print("sample:", json.dumps(c[0], ensure_ascii=False)[:400])
    elif isinstance(c, dict):
        print("keys:", list(c.keys())[:20])
    await pw.stop()


asyncio.run(main())
