# -*- coding: utf-8 -*-
"""亚马逊商品页: 抽取 星级评分 / 评分数 / 小类目-大类目排名(Bsr)

页面加载后从 DOM 读取(不走接口)。用法:
    python3 explore/probe_amazon_rating.py [ASIN ...]
"""
import json
import re
import sys

from playwright.sync_api import sync_playwright

DEFAULT = ["B0GTLQZ26Y", "B09LM31YK5", "B0HHF13X1G"]

JS = r"""
() => {
  const t = (s) => (s || "").replace(/\s+/g, " ").trim();
  const txtOf = (sel) => { const e = document.querySelector(sel); return e ? t(e.innerText || e.textContent) : ""; };
  const attrOf = (sel, a) => { const e = document.querySelector(sel); return e ? (e.getAttribute(a) || "") : ""; };

  // 1) 星级评分
  let star = attrOf("#acrPopover", "title") || attrOf("a[data-hook='acr-popover']", "title");
  if (!star) star = txtOf("#acrPopover span.a-icon-alt") || txtOf("[data-hook='rating-out-of-text']");
  if (!star) { const el = document.getElementById("averageCustomerReviews");
               const m = el ? (el.innerText.match(/[0-9.]+(?=\s*(?:つ星|out of 5))/) || []) : []; star = m[0] || ""; }
  // 星级数值: "5つ星のうち4.5" -> 4.5 ; "4.5 out of 5 stars" -> 4.5
  const mStar = star.match(/うち\s*([0-9]+(?:\.[0-9]+)?)/) || star.match(/^\s*([0-9]+(?:\.[0-9]+)?)\s*out of/);
  const starNum = mStar ? mStar[1] : ((star.match(/([0-9]+(?:\.[0-9]+)?)\s*$/) || [""])[0]);

  // 2) 评分数
  let cnt = txtOf("#acrCustomerReviewText") || txtOf("[data-hook='total-review-count']");
  const cntNum = (cnt.replace(/[^0-9]/g, "")) || "";

  // 3) 排名: 在 商品信息/商品详情 区里找 "売れ筋ランキング"/"Best Sellers Rank"
  const boxes = ["#detailBulletsWrapper_feature_div", "#productDetails_detailBullets_sections1",
                 "#productDetails_db_sections", "#prodDetails", "#detailBullets_feature_div",
                 "#productDetails_feature_div"];
  let rankText = "";
  for (const b of boxes) { const el = document.querySelector(b); if (el && /ランキング|Best Sellers Rank/.test(el.innerText)) { rankText = t(el.innerText); break; } }
  if (!rankText) {
    const m = t(document.body.innerText).match(/(?:Amazon\s*)?売れ筋ランキング[\s\S]{0,300}/);
    if (m) rankText = m[0];
  }
  // 排名名次 + 类目: 形如 "ホーム＆キッチン - 193位" / "カセットコンロ - 1位"
  const bsr = [];
  const DASH = "\\-–—−‐";   // 注意: 不能包含日文长音符「ー」, 否则「ホーム」等词会被误排除
  const re = new RegExp("(?:^|[\\s(（])([^\\s" + DASH + "][^\\s" + DASH + "]{1,40}?)\\s*[" + DASH + "]\\s*([0-9][0-9,]*)\\s*位", "g");
  for (const m of rankText.matchAll(re)) {
    const cat = t(m[1]).replace(/^[（(].*?[)）]\s*/, "");
    if (/位|を見る|ランキング/.test(cat)) continue;
    bsr.push({ category: cat, rank: m[2].replace(/,/g, "") });
  }
  const dedup = [];
  const seenCat = new Set();
  for (const x of bsr) { if (!seenCat.has(x.category)) { seenCat.add(x.category); dedup.push(x); } }
  bsr.length = 0; bsr.push(...dedup);

  return {
    title: txtOf("#productTitle"),
    star: star, starNum: starNum,
    review: cnt, reviewNum: cntNum,
    rankRaw: rankText.slice(0, 400),
    bsr: bsr.slice(0, 6),
    url: location.href,
  };
}
"""


def run(asins):
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(locale="ja-JP", viewport={"width": 1500, "height": 950},
                            user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36"))
        pg = ctx.new_page()
        out = []
        for asin in asins:
            rec = {"asin": asin}
            try:
                pg.goto(f"https://www.amazon.co.jp/dp/{asin}", wait_until="domcontentloaded", timeout=45000)
                try:
                    pg.wait_for_selector("#productTitle", timeout=20000)
                except Exception:       # noqa: BLE001
                    pass
                pg.wait_for_timeout(3000)
                rec.update(pg.evaluate(JS))
                rec["ok"] = True
            except Exception as e:      # noqa: BLE001
                rec["error"] = str(e)[:140]
            out.append(rec)
            print(f"--- {asin}")
            print(f"    标题      : {str(rec.get('title'))[:46]}")
            print(f"    星级评分  : {rec.get('starNum')!r}   (原文本 {rec.get('star')!r})")
            print(f"    评分数    : {rec.get('reviewNum')!r}   (原文本 {rec.get('review')!r})")
            print(f"    排名明细  : {rec.get('bsr')}")
            if rec.get("rankRaw"):
                print(f"    排名原文  : {rec['rankRaw'][:140]}")
            if rec.get("error"):
                print(f"    错误      : {rec['error']}")
            print()
        b.close()
    return out


if __name__ == "__main__":
    res = run(sys.argv[1:] or DEFAULT)
    json.dump(res, open("explore/amazon_rating_probe.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("已保存 explore/amazon_rating_probe.json")
