# -*- coding: utf-8 -*-
"""验证: 进入搜索词详情页后自动抓取父体附图"""
import asyncio
import sys
import urllib.parse

from playwright.async_api import async_playwright

sys.path.insert(0, "/Users/gx/Desktop/mypro/adsAutoAna")
from backend import database as db  # noqa: E402

BASE = "http://127.0.0.1:8321"
ASIN = "B0HHH7T6GQ"


async def main():
    db.init_db()
    pt = db.get_parent_terms(ASIN)
    terms = [r for r in pt["rows"] if str(r.get("scope", "")).endswith("keyword")]
    if not terms:
        print("该父体无搜索词, 换 ASIN"); return
    term = terms[0]["query"]
    print("父:", ASIN, "词:", term, "| 抓前图片数:", db.get_parent_images(ASIN)["count"])

    pw = await async_playwright().start()
    b = await pw.chromium.launch(headless=True)
    page = await (await b.new_context(viewport={"width": 1500, "height": 950})).new_page()
    await page.goto(f"{BASE}/?page=term&asin={ASIN}&term={urllib.parse.quote(term)}",
                    wait_until="domcontentloaded", timeout=60000)
    # 等待自动抓取完成(最多 120s)
    for _ in range(24):
        await page.wait_for_timeout(5000)
        n = await page.evaluate("() => document.querySelectorAll('.tg-thumbs img').length")
        msg = await page.evaluate("() => (document.getElementById('tgImgMsg')||{}).textContent || ''")
        if n:
            break
    print("页面附图缩略图:", await page.evaluate("() => document.querySelectorAll('.tg-thumbs img').length"))
    print("提示文案:", await page.evaluate("() => (document.getElementById('tgImgMsg')||{}).textContent"))
    await page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/shots/term_auto_img.png")
    await b.close(); await pw.stop()

    print("抓后库中图片数:", db.get_parent_images(ASIN)["count"])


asyncio.run(main())
