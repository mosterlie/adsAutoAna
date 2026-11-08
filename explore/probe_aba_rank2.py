# -*- coding: utf-8 -*-
"""探查赛狐「ABA搜索词排名-按周」勾选：DOM 结构 + 勾选后的新列与接口。"""
import json

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"

FIND_HTML = """(text) => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    if ((n.textContent || '').trim() === text) {
      let el = n.parentElement;
      for (let i = 0; i < 3 && el.parentElement; i++) el = el.parentElement;
      return el.outerHTML.slice(0, 3000);
    }
  }
  return 'not found';
}"""

CLICK_TEXT = """(text) => {
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n;
  while ((n = w.nextNode())) {
    if ((n.textContent || '').trim() === text) {
      let el = n.parentElement;
      for (let i = 0; i < 2 && el.parentElement; i++) el = el.parentElement;
      el.click();
      return 'clicked ' + el.tagName + '.' + el.className;
    }
  }
  return 'not found';
}"""

HEADERS = """() => {
  const ths = [...document.querySelectorAll('th')].map(t => (t.innerText || '').trim().replace(/\\s+/g, ' '));
  const heads = [...document.querySelectorAll('div[class*=header]')].map(d => (d.innerText || '').trim()).filter(t => t && t.length < 60);
  return {th: [...new Set(ths)], div: [...new Set(heads)]};
}"""


def main() -> None:
    hits = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        pg = b.contexts[0].new_page()
        pg.set_viewport_size({"width": 1680, "height": 1050})

        def on_resp(r):
            u = r.url
            if "sellfox" not in u or any(x in u for x in (".js", ".css", ".png", ".jpg", ".svg", ".woff")):
                return
            try:
                body = r.text()
            except Exception:  # noqa: BLE001
                body = ""
            hits.append({"method": r.request.method, "url": u,
                         "post": r.request.post_data or "", "status": r.status, "resp": body})

        pg.on("response", on_resp)
        pg.goto(URL, wait_until="commit", timeout=60000)
        pg.wait_for_timeout(15000)

        print("== 定位 ABA 复选框 ==")
        print(pg.evaluate(FIND_HTML, "ABA搜索词排名-按周")[:1200])

        print("\n== 勾选前表头 ==", json.dumps(pg.evaluate(HEADERS), ensure_ascii=False)[:600])

        before = len(hits)
        print("\n点击:", pg.evaluate(CLICK_TEXT, "ABA搜索词排名-按周"))
        pg.wait_for_timeout(9000)
        pg.screenshot(path=f"{OUT}/aba_checked.png")
        print("== 勾选后表头 ==", json.dumps(pg.evaluate(HEADERS), ensure_ascii=False)[:900])

        print("\n== 勾选后新请求 ==")
        for h in hits[before:]:
            u = h["url"]
            if any(k in u for k in ("suggestKeywords", "suggestBid", "rank", "aba", "Aba", "ABA")):
                print(f"--- {h['method']} {u[:170]}")
                print("   post:", h["post"][:400])
                print("   resp:", h["resp"][:600].replace("\n", " "))
        json.dump(hits[before:], open(f"{OUT}/aba_hits2.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        b.close()


if __name__ == "__main__":
    main()
