# -*- coding: utf-8 -*-
"""探针: 打开亚马逊搜索结果页, 把前 N 个结果卡的 DOM 结构 dump 出来,
用于设计「有啥爬啥」的字段抽取规则。

只读不落库。用法:
    python3 explore/probe_amz_card.py "金網フェンス" [卡片数]
输出:
    explore/amz_card_dump.json   每个卡片的 outerHTML + 关键节点清单
"""
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import amazon as az          # noqa: E402

DUMP_JS = r"""
(n) => {
  const nodes = [...document.querySelectorAll(
    'div.s-main-slot div[data-component-type="s-search-result"]')].slice(0, n);
  const grab = (el) => {
    // 收集卡片内所有带 class 的节点摘要, 便于人工判断哪些字段可用
    const cls = [];
    el.querySelectorAll('*').forEach((n) => {
      const c = (n.className && typeof n.className === 'string') ? n.className : '';
      if (!c) return;
      const key = n.tagName.toLowerCase() + '.' + c.trim().split(/\s+/).slice(0, 3).join('.');
      const t = (n.textContent || '').trim().replace(/\s+/g, ' ');
      cls.push(key + ' :: ' + t.slice(0, 90));
    });
    return cls;
  };
  return nodes.map((el, i) => ({
    i: i + 1,
    asin: el.getAttribute('data-asin') || '',
    html: el.outerHTML.slice(0, 9000),
    nodes: grab(el),
    attrs: [...el.attributes].map((a) => a.name + '=' + a.value),
  }));
}
"""


def main() -> None:
    query = sys.argv[1] if len(sys.argv) > 1 else "金網フェンス"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    url = az.search_url(query, "co.jp")
    print("URL:", url)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(az.config.CDP_URL, timeout=8000)
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            via = "cdp"
        except Exception:       # noqa: BLE001
            browser = pw.chromium.launch(headless=True)
            ctx = browser.new_context(locale="ja-JP", user_agent=az.UA,
                                      viewport={"width": 1440, "height": 900})
            via = "headless"
        print("via:", via)
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_selector(
                'div.s-main-slot div[data-component-type="s-search-result"]', timeout=20000)
        except Exception as e:      # noqa: BLE001
            print("等待结果卡超时:", e)
        for _ in range(2):
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(800)
        data = page.evaluate(DUMP_JS, n)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "amz_card_dump.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        print(f"卡片数 {len(data)} -> {out}")
        for c in data:
            print(f"\n--- 卡片 {c['i']} asin={c['asin']} 节点数={len(c['nodes'])}")
            for line in c["nodes"][:40]:
                print("   ", line[:150])
        page.close()


if __name__ == "__main__":
    main()
