# -*- coding: utf-8 -*-
"""从赛狐页面(wujie shadow DOM)提取四个区块的 DOM 结构 + 关键控件几何。"""
import json
import os

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"

JS_SECTION = r"""(() => {
  const host = [...document.querySelectorAll('*')].filter(e => e.shadowRoot)
      .map(h => h.shadowRoot).find(sr => (sr.textContent || '').includes('广告活动名称'));
  if (!host) return {err: 'no shadow'};
  const all = [...host.querySelectorAll('*')];
  const label = all.find(e => (e.textContent || '').trim() === '广告活动名称');
  let sec = label;
  for (let i = 0; i < 8 && sec; i++) {
    if ((sec.textContent || '').includes('根据广告位调整竞价')) break;
    sec = sec.parentElement;
  }
  const geo = el => {
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {w: Math.round(r.width), h: Math.round(r.height),
            fs: cs.fontSize, color: cs.color, bw: cs.borderWidth,
            br: cs.borderRadius, bg: cs.backgroundColor};
  };
  const controls = [...sec.querySelectorAll('input,select,textarea')].map(el => ({
    tag: el.tagName.toLowerCase(), type: el.type, ph: el.placeholder || '',
    cls: (el.className || '').slice(0, 90), geo: geo(el)
  }));
  const labels = [...sec.querySelectorAll('.el-form-item__label, label, .ad-label, [class*=label]')]
    .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 60);
  return {html: sec.outerHTML.slice(0, 120000), controls, labels,
          text: (sec.innerText || '').slice(0, 4000), secGeo: geo(sec)};
})()"""


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        pg = b.contexts[0].new_page()
        pg.set_viewport_size({"width": 1680, "height": 1050})
        pg.goto(URL, wait_until="commit", timeout=60000)
        pg.wait_for_timeout(14000)
        data = pg.evaluate(JS_SECTION)
        if data.get("err"):
            print("失败:", data)
        else:
            open(f"{OUT}/campaign_section.html", "w", encoding="utf-8").write(data["html"])
            open(f"{OUT}/campaign_section.json", "w", encoding="utf-8").write(
                json.dumps({k: data[k] for k in ("controls", "labels", "secGeo")},
                           ensure_ascii=False, indent=1))
            print("区块宽度:", data["secGeo"])
            print("标签:", data["labels"])
            print("控件数:", len(data["controls"]))
            for c in data["controls"]:
                print(f"  {c['tag']:8s} {c['type']:8s} w={c['geo']['w']:4d} ph={c['ph'][:18]:18s} cls={c['cls'][:40]}")
            print("--- 文本 ---")
            print(data["text"][:1500])
        print("整页截图")
        pg.screenshot(path=f"{OUT}/adcreate_top.png")
        b.close()


if __name__ == "__main__":
    main()
