# -*- coding: utf-8 -*-
"""在 ad-create iframe 内定位 ABA 复选框并抓取勾选后行为。"""
import json

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"

FIND = """(text) => {
  const walk = (root, out) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) walk(el.shadowRoot, out);
      const t = (el.innerText || '').trim();
      if (t === text) out.push(el);
    }
    return out;
  };
  const hits = walk(document, []);
  if (!hits.length) return 'not found';
  let el = hits[hits.length - 1];
  const chain = [];
  for (let i = 0; i < 4 && el.parentElement; i++) {
    chain.push(el.tagName + '.' + (el.className || '') + ' :: ' + (el.outerHTML || '').slice(0, 260));
    el = el.parentElement;
  }
  return chain.join('\\n\\n');
}"""

CLICK = """(text) => {
  const walk = (root) => {
    for (const el of root.querySelectorAll('*')) {
      if (el.shadowRoot) { const r = walk(el.shadowRoot); if (r) return r; }
      if ((el.innerText || '').trim() === text) { el.click(); return el.tagName + '.' + el.className; }
    }
    return null;
  };
  return walk(document) || 'not found';
}"""

HEADERS = """() => {
  const ths = [...document.querySelectorAll('th')].map(t => (t.innerText || '').trim().replace(/\\s+/g, ' '));
  return [...new Set(ths)];
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
        pg.wait_for_timeout(18000)

        fr = None
        for f in pg.frames:
            if "/cpc-vue3/ad-create/index.html" in f.url:
                fr = f
                break
        print("frame:", fr.url[:100] if fr else None)
        print("body 文本片段:", fr.locator("body").inner_text()[:120].replace("\n", " | "))

        for t in ("手动投放", "关键词投放"):
            print(f"点 {t}:", fr.evaluate(CLICK, t))
            pg.wait_for_timeout(2500)
        print("点 建议:", fr.evaluate(CLICK, "建议"))
        pg.wait_for_timeout(3500)
        pg.screenshot(path=f"{OUT}/aba3_step1.png")

        print("\n== ABA 复选框 DOM ==")
        print(fr.evaluate(FIND, "ABA搜索词排名-按周")[:1500])

        print("\n== 勾选前表头 ==", fr.evaluate(HEADERS))
        before = len(hits)
        print("点击 ABA:", fr.evaluate(CLICK, "ABA搜索词排名-按周"))
        pg.wait_for_timeout(10000)
        pg.screenshot(path=f"{OUT}/aba3_checked.png")
        print("== 勾选后表头 ==", fr.evaluate(HEADERS))

        print("\n== 新请求 ==")
        for h in hits[before:]:
            print(f"- {h['method']} {h['url'][:150]}")
            if h["post"]:
                print("   post:", h["post"][:300])
            print("   resp:", h["resp"][:400].replace("\n", " "))
        json.dump(hits[before:], open(f"{OUT}/aba_hits3.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        b.close()


if __name__ == "__main__":
    main()
