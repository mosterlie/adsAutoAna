# -*- coding: utf-8 -*-
"""对比「子体」「父体」两个视图的服务端响应 (含响应体)"""
import json
import sys

sys.path.insert(0, "/Users/gx/Desktop/mypro/adsAutoAna")

from playwright.sync_api import sync_playwright   # noqa: E402

import config                                      # noqa: E402
from backend.cookies import load_cookies            # noqa: E402

URL = "https://www.sellfox.com/amzup-web-main/web/product/index.html"
KEY = "product/pageList"
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/parent_response_probe.json"


def summarize(d):
    rows = (d or {}).get("rows") or []
    return {
        "totalSize": (d or {}).get("totalSize"),
        "rows": len(rows),
        "first": [{k: r.get(k) for k in ("isVariation", "parent", "parentAsin", "asin", "sku")}
                  for r in rows[:3]],
        "allKeys": sorted(rows[0].keys()) if rows else [],
    }


def main():
    cookies = load_cookies() or {}
    caps = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(locale="zh-CN", user_agent=config.USER_AGENT,
                                  viewport={"width": 1600, "height": 1000})
        ctx.add_cookies([{"name": k, "value": v, "domain": ".sellfox.com", "path": "/"}
                         for k, v in cookies.items()])
        page = ctx.new_page()

        def on_resp(r):
            if KEY in r.url and r.request.method == "POST":
                try:
                    j = r.json()
                except Exception:
                    return
                d = j.get("data") or {}
                item = {"req": r.request.post_data, "code": j.get("code"),
                        "sum": summarize(d)}
                caps.append(item)
                print(f"  [RESP] pageType={(r.request.post_data or '')[:0]} "
                      f"totalSize={item['sum']['totalSize']} rows={item['sum']['rows']} "
                      f"first={item['sum']['first']}")

        page.on("response", on_resp)
        page.goto(URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(12000)

        hit = page.evaluate(r"""
        () => {
          const vis = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
          const els = [...document.querySelectorAll('*')].filter(
            e => e.children.length === 0 && (e.textContent||'').trim() === '父体' && vis(e));
          if (!els.length) return null;
          const el = els[0]; const r = el.getBoundingClientRect();
          return {x: r.x + r.width/2, y: r.y + r.height/2};
        }
        """)
        print("父体按钮:", hit)
        if hit:
            n0 = len(caps)
            page.mouse.click(hit["x"], hit["y"])
            page.wait_for_timeout(12000)
            print(f"点击后新增响应 {len(caps) - n0} 条")

        browser.close()

    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(caps, fh, ensure_ascii=False, indent=2)
    print(f"共 {len(caps)} 条 -> {OUT}")


main()
