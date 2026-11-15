# -*- coding: utf-8 -*-
"""临时取证: 拉 2026-11-08(昨天) 的活动 / 广告产品 / 搜索词，用于出单归因分析"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from backend.sellfox_client import SellfoxClient

DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-11-08"
SHOP = [711811]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis", f"{DAY}_attribution")
os.makedirs(OUT, exist_ok=True)


def dump(name, rows):
    p = os.path.join(OUT, f"{name}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"[{name}] {len(rows)} 行 -> {p}")
    return rows


def main():
    cookies = json.load(open(config.COOKIE_PATH))
    c = SellfoxClient(cookies)

    shops = c.get_shops(SHOP)
    print("店铺:", [(s.get("shopId"), s.get("shopName"), s.get("siteName"))
                    for s in (shops.get("rows") or [])][:5])

    camp = c.fetch_list("campaign", SHOP, DAY, DAY, scope={})
    dump("campaign", camp)

    prod = c.fetch_list("product", SHOP, DAY, DAY, scope={"type": "sp"})
    dump("product_sp", prod)

    kw = c.fetch_list("search", SHOP, DAY, DAY,
                      scope={"adType": "sp", "searchQueryType": "searchKeyword"})
    dump("search_keyword", kw)

    asin = c.fetch_list("search", SHOP, DAY, DAY,
                        scope={"adType": "sp", "searchQueryType": "searchAsin"})
    dump("search_asin", asin)

    print("\n有订单活动数:",
          sum(1 for r in camp if float(r.get("adOrderNum") or 0) > 0))
    print("活动总订单:", sum(float(r.get("adOrderNum") or 0) for r in camp))
    print("有订单产品数:",
          sum(1 for r in prod if float(r.get("adOrderNum") or 0) > 0))
    print("有订单搜索词数:",
          sum(1 for r in kw if float(r.get("adOrderNum") or 0) > 0))

    if camp:
        print("\n活动行字段样例:")
        print(json.dumps({k: camp[0].get(k) for k in sorted(camp[0].keys())[:60]},
                         ensure_ascii=False, indent=1))
    if kw:
        print("\n搜索词行字段样例:")
        print(json.dumps({k: kw[0].get(k) for k in sorted(kw[0].keys())[:60]},
                         ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
