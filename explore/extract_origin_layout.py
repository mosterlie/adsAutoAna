# -*- coding: utf-8 -*-
"""读取赛狐「在线产品」页面: 表头(固定+滚动) + 首行数据单元格, 用于字段映射核对"""
import json
import sys

sys.path.insert(0, ".")
from backend.cookies import load_cookies          # noqa: E402
from playwright.sync_api import sync_playwright    # noqa: E402

URL = "https://www.sellfox.com/amzup-web-main/web/product/index.html"

JS = r"""
() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const tables = [...document.querySelectorAll("table")];

  // 表头: 分别找固定表和滚动表(均 48 格, 互为补集)
  let fixedHead = null, scrollHead = null;
  tables.forEach((t) => {
    const cells = [...t.querySelectorAll("thead th, thead td")];
    if (cells.length !== 48) return;
    const txt = cells.map(c => clean(c.innerText));
    const named = txt.filter(x => x).length;
    // 固定表头: 前 8 格有名字, 后 40 空
    if (!fixedHead && txt[1] && !txt[9]) fixedHead = txt;
    else if (!scrollHead && txt[8] && !txt[1]) scrollHead = txt;
  });

  // 数据行: 固定表(8 td) 与 滚动表(48 td)
  let fixedRow = null, scrollRow = null;
  tables.forEach((t) => {
    const tr = t.querySelector("tbody tr");
    if (!tr) return;
    const tds = [...tr.querySelectorAll("td")];
    if (tds.length === 8 && !fixedRow) fixedRow = tds.map(c => clean(c.innerText));
    if (tds.length === 48 && !scrollRow) scrollRow = tds.map(c => clean(c.innerText));
  });

  // 合并成 48 列
  const headers = [], cells = [];
  for (let i = 0; i < 48; i++) {
    if (i < 8) { headers.push(fixedHead ? fixedHead[i] : ""); cells.push(fixedRow ? fixedRow[i] : ""); }
    else { headers.push(scrollHead ? scrollHead[i] : ""); cells.push(scrollRow ? scrollRow[i] : ""); }
  }

  // 顶部查询框
  const q = [];
  document.querySelectorAll("input").forEach((el) => {
    if (!el.offsetParent) return;
    const ph = el.getAttribute("placeholder") || "";
    const tp = el.getAttribute("type") || "text";
    if (tp === "radio") q.push({ type: "radio", label: (el.parentElement.innerText || "").trim().slice(0, 12) });
    else q.push({ type: "input", label: ph || el.value || "" });
  });
  const dds = [];
  document.querySelectorAll("[class*=select], [class*=dropdown], [class*=cascader]").forEach((el) => {
    if (!el.offsetParent) return;
    const t = clean(el.innerText);
    if (t && t.length <= 24) dds.push(t.replace(/\s+/g, " "));
  });

  return { headers, cells, inputs: q, dropdowns: [...new Set(dds)].slice(0, 40) };
}
"""


def main():
    ck = load_cookies()
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True, proxy={"server": "http://127.0.0.1:56889"})
        ctx = b.new_context(locale="zh-CN", viewport={"width": 1920, "height": 1080})
        ctx.add_cookies([{"name": k, "value": v, "domain": ".sellfox.com", "path": "/"} for k, v in ck.items()])
        pg = ctx.new_page()
        pg.goto(URL, wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(16000)
        for t in ("我知道了", "知道了"):
            try:
                pg.click(f"text={t}", timeout=2000)
                pg.wait_for_timeout(500)
                break
            except Exception:       # noqa: BLE001
                pass
        pg.wait_for_timeout(1000)
        res = pg.evaluate(JS)
        b.close()

    json.dump(res, open("explore/origin_olp_layout.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("=== 表头 / 首行数据 (48 列) ===")
    for i, (h, c) in enumerate(zip(res["headers"], res["cells"])):
        print(f"  {i:>2} {h:<18} | {c[:46]}")
    print()
    print("=== 查询区 input ===")
    for x in res["inputs"]:
        print(f"  [{x['type']:<6}] {x['label']}")
    print()
    print("=== 下拉控件 ===")
    print("  ", res["dropdowns"])


if __name__ == "__main__":
    main()
