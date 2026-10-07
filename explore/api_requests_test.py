# -*- coding: utf-8 -*-
"""验证脱离浏览器: 用 Python requests + 从浏览器导出的 Cookie 直连接口"""
import asyncio
import json
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
WRAP = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
        "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fads-management%3Fopenfrom%3Dclickmenu")
API = "https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/campaign/getAllCampaignData"


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
    payload = contracts["广告活动"]["req"]

    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "cpc" in p.url and "sellfox" in p.url), None) or await ctx.new_page()
    await page.goto(WRAP, wait_until="domcontentloaded", timeout=60000)
    await asyncio.sleep(7)

    # 1) 导出 Cookie
    cookies = await ctx.cookies("https://www.sellfox.com")
    jar = {c["name"]: c["value"] for c in cookies}
    print("cookie 数量:", len(jar), "| 名称:", list(jar.keys())[:12])
    # 2) 抓一个真实请求的完整头做参考
    hdrs = {}

    def on_req(r):
        if "getAllCampaignData" in r.url:
            hdrs.update(r.headers)
    page.on("request", on_req)
    await asyncio.sleep(0.2)
    print("真实请求头:", {k: v[:60] for k, v in hdrs.items() if k.lower() in
                          ("content-type", "referer", "origin", "user-agent", "x-requested-with", "cookie")} if hdrs else "未捕获")
    await pw.stop()

    # 3) 用 requests 直连
    try:
        import requests
    except ImportError:
        print("requests 未安装, 跳过 requests 直连测试")
        return

    s = requests.Session()
    for c in cookies:
        s.cookies.set(c["name"], c["value"], domain=c.get("domain", "www.sellfox.com"), path=c.get("path", "/"))
    headers = {
        "Content-Type": "application/json",
        "Origin": "https://www.sellfox.com",
        "Referer": "https://www.sellfox.com/cpc-vue3/web/cpc-vue3/ads-management?openfrom=clickmenu",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    }
    try:
        r = s.post(API, json=payload, headers=headers, timeout=20)
        print("\n[requests] http:", r.status_code)
        j = r.json()
        page_ = (j.get("data") or {}).get("page") or {}
        print("[requests] code:", j.get("code"), "msg:", j.get("msg"),
              "totalSize:", page_.get("totalSize"), "rows:", len(page_.get("rows") or []))
        if page_.get("rows"):
            print("[requests] row0.name:", page_["rows"][0].get("name"))
    except Exception as e:
        print("\n[requests] 失败:", type(e).__name__, str(e)[:200])


asyncio.run(main())
