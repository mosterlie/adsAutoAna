# -*- coding: utf-8 -*-
"""探查赛狐广告创建页「手动投放-建议」勾选 ABA搜索词排名-按周 后的接口与列结构。
分步执行，先打印页面关键文本以便定位。
"""
import json
import sys

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"

CLICK_SHADOW = """(text) => {
  const roots = [document, ...[...document.querySelectorAll('*')]
      .filter(e => e.shadowRoot).map(e => e.shadowRoot)];
  const queue = [...roots];
  while (queue.length) {
    const r = queue.shift();
    for (const el of r.querySelectorAll('button, .el-button, [role="tab"], label, span')) {
      if ((el.innerText || '').trim() === text) { el.click(); return 'ok:' + el.tagName; }
    }
    for (const el of r.querySelectorAll('*')) if (el.shadowRoot) queue.push(el.shadowRoot);
  }
  return 'not found: ' + text;
}"""

CHECKBOX = """(text) => {
  const roots = [document, ...[...document.querySelectorAll('*')]
      .filter(e => e.shadowRoot).map(e => e.shadowRoot)];
  const queue = [...roots];
  while (queue.length) {
    const r = queue.shift();
    for (const lab of r.querySelectorAll('label')) {
      if ((lab.innerText || '').includes(text)) {
        const cb = lab.querySelector('input[type=checkbox]');
        if (cb) { if (!cb.checked) cb.click(); return 'ok checked=' + cb.checked; }
      }
    }
    for (const el of r.querySelectorAll('*')) if (el.shadowRoot) queue.push(el.shadowRoot);
  }
  return 'not found';
}"""

DUMP = """() => {
  const roots = [document, ...[...document.querySelectorAll('*')]
      .filter(e => e.shadowRoot).map(e => e.shadowRoot)];
  const out = [];
  for (const r of roots) {
    for (const el of r.querySelectorAll('label, button, [role=tab], th')) {
      const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
      if (t && t.length < 40) out.push(el.tagName + '|' + t);
    }
  }
  return [...new Set(out)];
}"""


def main() -> None:
    hits = []
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9222")
        pg = b.contexts[0].new_page()
        pg.set_viewport_size({"width": 1680, "height": 1050})

        def on_resp(r):
            u = r.url
            if any(x in u for x in (".js", ".css", ".png", ".jpg", ".svg", ".woff", ".json?")):
                return
            if "sellfox" not in u:
                return
            body = ""
            try:
                body = r.text()
            except Exception:  # noqa: BLE001
                pass
            hits.append({"method": r.request.method, "url": u,
                         "post": (r.request.post_data or ""), "status": r.status,
                         "resp": body})

        pg.on("response", on_resp)
        pg.goto(URL, wait_until="commit", timeout=60000)
        pg.wait_for_timeout(16000)
        pg.mouse.move(500, 600)
        for _ in range(4):
            pg.mouse.wheel(0, 600)
            pg.wait_for_timeout(400)

        print("添加变体:", pg.evaluate(CLICK_SHADOW, "添加变体"))
        pg.wait_for_timeout(5000)
        print("添加:", pg.evaluate(CLICK_SHADOW, "添加"))
        pg.wait_for_timeout(6000)
        pg.screenshot(path=f"{OUT}/aba_step1.png")

        labels = pg.evaluate(DUMP)
        print("页面元素数:", len(labels))
        for t in labels:
            if any(k in t for k in ("投放", "关键词", "建议", "手动", "自动", "ABA", "广告组")):
                print("  ", t)

        for t in ("手动投放", "关键词投放"):
            print(f"点击 {t}:", pg.evaluate(CLICK_SHADOW, t))
            pg.wait_for_timeout(2500)
        pg.screenshot(path=f"{OUT}/aba_step2.png")
        print("勾选 ABA:", pg.evaluate(CHECKBOX, "ABA搜索词排名"))
        pg.wait_for_timeout(9000)
        pg.screenshot(path=f"{OUT}/aba_step3.png")

        print("抓包数:", len(hits))
        json.dump(hits, open(f"{OUT}/aba_hits.json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        b.close()


if __name__ == "__main__":
    main()
