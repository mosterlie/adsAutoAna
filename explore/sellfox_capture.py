# -*- coding: utf-8 -*-
"""Playwright 连接调试 Chrome(9223), 抓取赛狐「创建广告活动」页(含微前端 iframe)。"""
import json
import os

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp%26ts%3D1793686640990")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = b.contexts[0].new_page()
        pg.set_viewport_size({"width": 1680, "height": 1050})
        pg.goto(URL, wait_until="commit", timeout=60000)

        frame = None
        for _ in range(40):
            pg.wait_for_timeout(2000)
            for f in pg.frames:
                if "ad-create" in f.url:
                    try:
                        if "广告活动名称" in f.locator("body").inner_text():
                            frame = f
                            break
                    except Exception:  # noqa: BLE001
                        pass
            if frame:
                break
        print("找不到表单" if not frame else "表单已渲染")
        print("frames:", [f.url[:80] for f in pg.frames])

        pg.screenshot(path=f"{OUT}/adcreate_full.png", full_page=False)
        if frame:
            frame.locator("body").evaluate("() => window.scrollTo(0, 0)")
            pg.wait_for_timeout(500)
            pg.screenshot(path=f"{OUT}/adcreate_top.png")
            html = frame.content()
            open(f"{OUT}/adcreate_frame.html", "w", encoding="utf-8").write(html)
            print("frame HTML:", len(html))
            print("frame 正文:", frame.locator("body").inner_text()[:200].replace("\n", " | "))
        b.close()


if __name__ == "__main__":
    main()
