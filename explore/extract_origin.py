# -*- coding: utf-8 -*-
"""抓取赛狐「在线产品」页面的真实 DOM：表头 + 查询区控件（用于与本项目对比）"""
import json
import sys

sys.path.insert(0, ".")
from backend.cookies import load_cookies          # noqa: E402
from playwright.sync_api import sync_playwright    # noqa: E402

URL = "https://www.sellfox.com/amzup-web-main/web/product/index.html"

JS = r"""
() => {
  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const out = { headTables: [], dataTd: 0, bodyRows: 0, topbar: [] };

  // --- 表头 ---
  document.querySelectorAll("table").forEach((t, i) => {
    const cells = [...t.querySelectorAll("thead th, thead td")];
    if (cells.length >= 6) {
      out.headTables.push({ i, n: cells.length, txt: cells.map(c => clean(c.innerText)) });
    }
  });

  // --- 数据表 ---
  let best = null;
  document.querySelectorAll("table").forEach((t) => {
    const rows = t.querySelectorAll("tbody tr");
    if (rows.length > 5 && (!best || rows.length > best.rows)) {
      best = { rows: rows.length, td: rows[0].querySelectorAll("td").length };
    }
  });
  if (best) { out.dataTd = best.td; out.bodyRows = best.rows; }

  // --- 顶部查询区: 收集可见的输入/下拉/按钮文字 ---
  const seen = new Set();
  const push = (tag, label, extra) => {
    const k = tag + "|" + label + "|" + extra;
    if (seen.has(k) || !label) return;
    seen.add(k);
    out.topbar.push({ tag, label, extra });
  };
  document.querySelectorAll("input").forEach((el) => {
    if (!el.offsetParent) return;
    const ph = el.getAttribute("placeholder") || "";
    push("input", ph || el.value || "(无placeholder)", "type=" + (el.getAttribute("type") || "text"));
  });
  document.querySelectorAll(".el-select, .ant-select, [class*=select]").forEach((el) => {
    if (!el.offsetParent) return;
    const t = clean(el.innerText);
    if (t && t.length < 30) push("select", t.split(" ")[0], "");
  });
  document.querySelectorAll("button, .btn, [class*=btn]").forEach((el) => {
    if (!el.offsetParent) return;
    const t = clean(el.innerText);
    if (t && t.length <= 8) push("button", t, "");
  });

  // 顶部区域纯文本(前 30 行)
  out.pageTextHead = document.body.innerText.split("\n").map(clean).filter(Boolean).slice(0, 30);
  return out;
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
        pg.wait_for_timeout(15000)
        for t in ("我知道了", "知道了", "关闭"):
            try:
                pg.click(f"text={t}", timeout=2000)
                pg.wait_for_timeout(500)
                break
            except Exception:       # noqa: BLE001
                pass
        pg.wait_for_timeout(1000)
        res = pg.evaluate(JS)
        pg.screenshot(path="explore/origin_olp.png")
        b.close()

    json.dump(res, open("explore/origin_olp_dom.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print("数据行数:", res["bodyRows"], "| 每行 td 数:", res["dataTd"])
    print()
    print("=== 表头(各 table) ===")
    for h in res["headTables"]:
        print(f"table#{h['i']}: {h['n']} 列")
        print("   ", h["txt"])
    print()
    print("=== 顶部查询区控件 ===")
    for x in res["topbar"]:
        print(f"  [{x['tag']:<6}] {x['label']}  {x['extra']}")
    print()
    print("=== 页面顶部文本 ===")
    print(" | ".join(res["pageTextHead"]))


if __name__ == "__main__":
    main()
