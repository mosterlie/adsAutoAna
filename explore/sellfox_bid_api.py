# -*- coding: utf-8 -*-
"""抓取赛狐「添加变体 -> 自动获取建议竞价」的接口: URL / 载荷 / 响应。"""
import json
import os

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"

KEY = ("bid", "suggest", "bidding", "target", "adgroup", "ad_group", "product", "asin")


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = b.contexts[0].new_page()
        pg.set_viewport_size({"width": 1680, "height": 1050})
        hits = []

        def on_resp(r):
            u = r.url
            if "sellfox" not in u or any(x in u for x in (".js", ".css", ".png", ".jpg", ".svg", ".woff")):
                return
            if any(k in u.lower() for k in KEY):
                try:
                    body = r.text()[:1500]
                except Exception:  # noqa: BLE001
                    body = "<no body>"
                req = r.request
                hits.append({
                    "method": req.method, "url": u,
                    "post": (req.post_data or "")[:600],
                    "status": r.status, "resp": body,
                })

        pg.on("response", on_resp)
        pg.goto(URL, wait_until="commit", timeout=60000)
        pg.wait_for_timeout(16000)

        # 打开 添加推广的广告产品 区域并添加一个变体
        pg.mouse.move(500, 600)
        for _ in range(4):
            pg.mouse.wheel(0, 600)
            pg.wait_for_timeout(400)

        # 通过 shadow DOM 点击第一个「添加变体」
        clicked = pg.evaluate("""(() => {
          const srs = [...document.querySelectorAll('*')].filter(e => e.shadowRoot).map(h => h.shadowRoot);
          for (const sr of srs) {
            const btns = [...sr.querySelectorAll('button')].filter(b => b.innerText.trim() === '添加变体');
            if (btns.length) { btns[0].click(); return 'clicked 添加变体'; }
          }
          return 'not found';
        })()""")
        print("点击结果:", clicked)
        pg.wait_for_timeout(6000)

        # 再点「添加」
        pg.evaluate("""(() => {
          const srs = [...document.querySelectorAll('*')].filter(e => e.shadowRoot).map(h => h.shadowRoot);
          for (const sr of srs) {
            const btns = [...sr.querySelectorAll('button')].filter(b => b.innerText.trim() === '添加');
            if (btns.length) { btns[0].click(); return true; }
          }
        })()""")
        pg.wait_for_timeout(6000)

        print("命中接口数:", len(hits))
        for h in hits:
            print("\n---", h["method"], h["status"], h["url"][:150])
            if h["post"]:
                print("  post:", h["post"][:300])
            print("  resp:", h["resp"][:400].replace("\n", " "))

        json.dump(hits, open(f"{OUT}/bid_api_hits.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        pg.screenshot(path=f"{OUT}/bid_after_add.png")
        b.close()


if __name__ == "__main__":
    main()
