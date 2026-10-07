# -*- coding: utf-8 -*-
"""定位真正返回店铺列表的接口: 抓所有 API 响应, 找含店铺名的那个"""
import asyncio
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
SHOP_NAME = "金梧汇辰"


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
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None) or await ctx.new_page()

    hits = []

    async def on_resp(resp):
        if "/api/" not in resp.url:
            return
        try:
            txt = await resp.text()
        except Exception:
            return
        if SHOP_NAME in txt:
            hits.append((resp.url, txt[:800]))

    page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
    await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(12)

    print(f"=== 含「{SHOP_NAME}」的接口响应 ===")
    for u, t in hits:
        print("\nURL:", u[:130])
        print("  ", t[:500])
    if not hits:
        print("未找到 (可能店铺名在别处)")

    # 顺带: 抓 localStorage 里的店铺信息
    try:
        ls = await page.evaluate("""() => {
            const o = {};
            for (let i=0;i<localStorage.length;i++){const k=localStorage.key(i);
                if(/shop|store|site|user/i.test(k)) o[k]=(localStorage.getItem(k)||'').slice(0,400);}
            return o;
        }""")
        print("\n=== localStorage 店铺相关键 ===")
        for k, v in (ls or {}).items():
            print(" ", k, "=", v[:250])
    except Exception as e:
        print("ls err", e)
    await pw.stop()


asyncio.run(main())
