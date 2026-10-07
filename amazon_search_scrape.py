#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从「搜索词」页签取搜索词 -> 用真实浏览器打开亚马逊搜索结果页 -> 从 DOM 抽取结果

(抓取实现见 backend/amazon.py, 该脚本只做 CLI 封装)

用法:
    python3 amazon_search_scrape.py                 # 默认样例 ガスコンロ
    python3 amazon_search_scrape.py --list          # 列出库中搜索词候选(按花费降序)
    python3 amazon_search_scrape.py --query 地球儀
    python3 amazon_search_scrape.py --query 地球儀 --headless   # 不用现成Chrome
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import amazon                      # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "explore")


def pick_terms(limit=15):
    from backend import database as db
    rows = [json.loads(r["raw_json"]) for r in db.get_conn().execute(
        "SELECT raw_json FROM ad_records WHERE tab='search'")]
    rows = [r for r in rows if r.get("query")]
    rows.sort(key=lambda r: -float(r.get("adCost") or 0))
    return rows[:limit]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--query", default="ガスコンロ")
    ap.add_argument("--domain", default="co.jp")
    ap.add_argument("--cdp", default=None, help="调试 Chrome 的 CDP 地址, 默认取 config.CDP_URL")
    ap.add_argument("--headless", action="store_true", help="不使用现有 Chrome, 自起 Chromium")
    ap.add_argument("--scrolls", type=int, default=1)
    ap.add_argument("--refresh", action="store_true", help="忽略缓存, 强制重新抓取")
    ap.add_argument("--no-cache", action="store_true", help="不使用缓存(仍会写库)")
    ap.add_argument("--ttl", type=int, default=None, help="缓存有效期(秒), 默认取 config")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for r in pick_terms():
            print(f"  {r.get('query')}\t曝光={r.get('impressions')}\t花费={r.get('adCost')}")
        return

    cdp = None if args.headless else args.cdp
    res = amazon.fetch(args.query, domain=args.domain, cdp=cdp, scrolls=args.scrolls,
                       screenshot=True, refresh=args.refresh, ttl=args.ttl,
                       use_cache=not args.no_cache)

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"amazon_search_{args.query[:12]}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    src = "缓存" if res.get("from_cache") else "实时抓取"
    items = res.get("items") or []
    print("\n" + "=" * 78)
    print(f"搜索词: {res['query']}   页面标题: {res.get('page_title')}")
    print(f"结果条数: {res.get('item_count')}   受阻: {res.get('blocked')}   "
          f"来源: {src}({res.get('via')})   抓取时间: {res.get('fetched_at')}")
    covered = sorted({k for it in items for k, v in it.items()
                      if v not in (None, "", [], False, 0)})
    print(f"抓到 {len(covered)} 类字段: {', '.join(covered)}")
    print("=" * 78)
    for it in items[:12]:
        flag = "[广告]" if it["sponsored"] else "     "
        print(f"\n{it['position']:>2}. {flag} {it.get('brand') or ''} {it['title'][:60]}")
        bits = [f"价格={it.get('price') or '-'}"]
        if it.get("list_price"):
            bits.append(f"参考价={it['list_price']}")
        if it.get("rating_value"):
            bits.append(f"星级={it['rating_value']}({it.get('review_count') or 0})")
        for k, label in (("points", "积分"), ("coupon", "券"), ("discount", "优惠"),
                         ("delivery", "配送"), ("stock", "库存"),
                         ("bought_recently", "近期销量")):
            if it.get(k):
                bits.append(f"{label}={it[k]}")
        if it.get("badges"):
            bits.append("徽标=" + "/".join(it["badges"][:2]))
        print("    " + " · ".join(bits))
        if it.get("extras"):
            print("    其它: " + " | ".join(e["text"][:60] for e in it["extras"][:3]))
    print(f"\n已保存: {out}\n截图:   {res.get('screenshot')}")


if __name__ == "__main__":
    main()
