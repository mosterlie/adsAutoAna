# -*- coding: utf-8 -*-
"""详细抓取 suggestBid/recommendations 接口: 完整载荷、请求头、响应。"""
import json
import os

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"
TARGET = "suggestBid/recommendations"


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = b.contexts[0].new_page()
        pg.set_viewport_size({"width": 1680, "height": 1050})
        found = []

        def on_resp(r):
            if TARGET not in r.url:
                return
            req = r.request
            try:
                body = r.text()
            except Exception:  # noqa: BLE001
                body = ""
            hdrs = req.all_headers()
            keep = {k: v for k, v in hdrs.items()
                    if k.lower() in ("cookie", "content-type", "x-token", "token",
                                     "authorization", "x-auth-token", "sf-token", "user-agent",
                                     "origin", "referer")}
            found.append({"url": r.url, "status": r.status,
                          "post": req.post_data or "", "headers": keep, "resp": body})

        pg.on("response", on_resp)
        pg.goto(URL, wait_until="commit", timeout=60000)
        pg.wait_for_timeout(16000)
        pg.mouse.move(500, 600)
        for _ in range(4):
            pg.mouse.wheel(0, 600)
            pg.wait_for_timeout(400)
        pg.evaluate("""(() => {
          const srs = [...document.querySelectorAll('*')].filter(e => e.shadowRoot).map(h => h.shadowRoot);
          for (const sr of srs) {
            const btns = [...sr.querySelectorAll('button')].filter(b => b.innerText.trim() === '添加变体');
            if (btns.length) { btns[0].click(); return; }
          }
        })()""")
        pg.wait_for_timeout(7000)

        print("抓到:", len(found))
        for f in found[:2]:
            print("\nURL:", f["url"])
            print("STATUS:", f["status"])
            print("HEADERS:", json.dumps(f["headers"], ensure_ascii=False)[:900])
            print("POST:", f["post"][:1400])
            print("RESP:", f["resp"][:1200])
        json.dump(found, open(f"{OUT}/suggestbid_detail.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        b.close()


if __name__ == "__main__":
    main()
