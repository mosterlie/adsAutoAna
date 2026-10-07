# -*- coding: utf-8 -*-
"""在原页面抓取所有 XHR 响应，定位 评分/排名 数据来源"""
import json
import sys

sys.path.insert(0, ".")
from backend.cookies import load_cookies          # noqa: E402
from playwright.sync_api import sync_playwright    # noqa: E402

URL = "https://www.sellfox.com/amzup-web-main/web/product/index.html"
KEYS = ("rating", "ratingCount", "bsr", "smallBsr", "bigBsr", "reviewStar", "star")


def main():
    ck = load_cookies()
    hits = []
    all_api = []
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, proxy={"server": "http://127.0.0.1:56889"})
        ctx = b.new_context(locale="zh-CN", viewport={"width": 1920, "height": 1080})
        ctx.add_cookies([{"name": k, "value": v, "domain": ".sellfox.com", "path": "/"} for k, v in ck.items()])
        pg = ctx.new_page()

        def on_resp(r):
            u = r.url
            if "/api/" not in u:
                return
            all_api.append(u.split("?")[0])
            if not any(k in u.lower() for k in ("product", "page", "detail", "score", "rank", "bsr", "rating")):
                return
            try:
                txt = r.text()
            except Exception:       # noqa: BLE001
                return
            found = [k for k in KEYS if f'"{k}"' in txt]
            if found:
                hits.append({"url": u, "keys": found, "size": len(txt)})
                open(f"/tmp/origin_resp_{len(hits)}.json", "w", encoding="utf-8").write(txt)

        pg.on("response", on_resp)
        pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(18000)
        b.close()

    print("含评分/排名键的响应:")
    for h in hits:
        print("  ", h)
    print()
    print("全部 /api/ 端点(去重):")
    for u in sorted(set(all_api)):
        print("  ", u.replace("https://www.sellfox.com", ""))


if __name__ == "__main__":
    main()
