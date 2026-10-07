# -*- coding: utf-8 -*-
"""定位「在线产品 → 父体」真实请求参数

做法: 用真实浏览器打开在线产品页, 监听 pageList 请求,
      抓「子体」载荷 -> 点击「父体」-> 再抓一次载荷, 对比差异。
"""
import json
import sys

sys.path.insert(0, "/Users/gx/Desktop/mypro/adsAutoAna")

from playwright.sync_api import sync_playwright   # noqa: E402

import config                                      # noqa: E402
from backend.cookies import load_cookies            # noqa: E402

URL = "https://www.sellfox.com/amzup-web-main/web/product/index.html"
KEY = "product/pageList"
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/parent_payload_probe.json"


def main():
    cookies = load_cookies() or {}
    print(f"cookies: {len(cookies)} 个")
    caps = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="zh-CN", user_agent=config.USER_AGENT,
                                  viewport={"width": 1600, "height": 1000})
        ctx.add_cookies([{"name": k, "value": v, "domain": ".sellfox.com", "path": "/"}
                         for k, v in cookies.items()])
        page = ctx.new_page()

        def on_req(r):
            if KEY in r.url and r.method == "POST":
                caps.append({"url": r.url, "data": r.post_data})
                try:
                    d = dict(x.split("=", 1) for x in (r.post_data or "").split("&") if "=" in x)
                except Exception:
                    d = {}
                print("  [REQ] pageType=%r isVariation=%r defaultPageType=%r totalSize?" % (
                    d.get("pageType"), d.get("isVariation"), d.get("defaultPageType")))

        page.on("request", on_req)
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(12000)
        print("\n=== 载入完成, 截图 ===")
        page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/probe_parent_1.png",
                        full_page=False)

        # 找「父体」可点元素
        hit = page.evaluate(r"""
        () => {
          const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
          const els = [...document.querySelectorAll('*')].filter(
            e => e.children.length === 0 && (e.textContent||'').trim() === '父体' && vis(e));
          if (!els.length) return null;
          const el = els[0]; const r = el.getBoundingClientRect();
          return {x: r.x + r.width/2, y: r.y + r.height/2, tag: el.tagName};
        }
        """)
        print("父体按钮:", hit)
        if hit:
            n0 = len(caps)
            page.mouse.click(hit["x"], hit["y"])
            page.wait_for_timeout(10000)
            print(f"点击后新增请求 {len(caps) - n0} 条")
            page.screenshot(path="/Users/gx/Desktop/mypro/adsAutoAna/explore/probe_parent_2.png",
                            full_page=False)

        browser.close()

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(caps, fh, ensure_ascii=False, indent=2)
    print(f"\n共捕获 {len(caps)} 条 pageList 请求 -> {OUT}")
    for i, c in enumerate(caps[-6:]):
        try:
            d = dict(x.split("=", 1) for x in (c["data"] or "").split("&") if "=" in x)
        except Exception:
            d = {}
        print(f"  #{i}: pageType={d.get('pageType')!r} isVariation={d.get('isVariation')!r} "
              f"searchField={d.get('searchField')!r}")


main()
