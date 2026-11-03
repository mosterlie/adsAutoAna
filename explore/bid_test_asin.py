# -*- coding: utf-8 -*-
"""指定 ASIN 的父子建议竞价对照测试。用法: python explore/bid_test_asin.py B0HHHFK7G7"""
import json
import sys

import requests
from playwright.sync_api import sync_playwright

ASIN = (sys.argv[1] if len(sys.argv) > 1 else "B0HHHFK7G7").strip().upper()
SHOP = "711811"
H = {"content-type": "application/json", "origin": "https://www.sellfox.com"}
GW = "https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox"
LIST = f"{GW}/product/productRightSpPageListSearch"
BID = f"{GW}/multiple/target/suggestBid/recommendations"


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ck = {c["name"]: c["value"] for c in b.contexts[0].cookies("https://www.sellfox.com")}
        b.close()

    # 1) 找这个 ASIN 所在行, 拿父 ASIN 与同父兄弟
    rows = []
    for field in ("asin", "parentAsin"):
        j = requests.post(LIST, json={"pageNo": 1, "pageSize": 50, "shopId": SHOP,
                                      "searchField": field, "searchValue": ASIN,
                                      "searchType": "exact", "onlineStatus": "",
                                      "orderType": "desc", "type": "sp"},
                          cookies=ck, headers=H, timeout=30).json()
        got = (j.get("data") or {}).get("rows") or []
        print(f"按 {field} 搜索 -> code={j.get('code')} 命中 {len(got)} 行")
        rows.extend(got)
        if got:
            break

    if not rows:
        print("该店铺商品库里没有这个 ASIN，无法定位父/子关系")
        return

    row = rows[0]
    kids = [c.get("asin") for c in (row.get("childList") or []) if c.get("asin")]
    parents = sorted({c.get("batchCreateParentAsin") for c in (row.get("childList") or [])
                      if c.get("batchCreateParentAsin")})
    print("\n行 ASIN:", row.get("asin"), "| SKU:", row.get("sku"))
    print("父 ASIN:", parents, "| 子 ASIN 数:", len(kids))
    print("子 ASIN 列表:", kids[:10], "..." if len(kids) > 10 else "")

    # 2) 逐个对象查建议竞价
    targets = [("输入的 ASIN", [ASIN])]
    if row.get("asin") and row["asin"] != ASIN:
        targets.append(("行 ASIN(父体行)", [row["asin"]]))
    for p_ in parents:
        targets.append((f"父 ASIN {p_}", [p_]))
    if kids:
        targets.append(("首个子 ASIN", [kids[0]]))
        targets.append(("全部子 ASIN", kids[:60]))
        targets.append(("父+子 一起", ([parents[0]] if parents else []) + kids[:10]))

    for name, asins in targets:
        asins = [a for a in asins if a]
        if not asins:
            continue
        body = {"shopId": SHOP, "biddingStrategy": "AUTO_FOR_SALES", "asins": asins,
                "targetingExpressions": [{"type": t, "value": ""} for t in
                                         ("CLOSE_MATCH", "LOOSE_MATCH", "SUBSTITUTES", "COMPLEMENTS")],
                "placementProductPage": None, "placementTop": None,
                "placementRestOfSearch": None, "campaignId": "", "groupId": ""}
        j = requests.post(BID, json=body, cookies=ck, headers=H, timeout=40).json()
        info = ((j.get("data") or {}).get("bidInfo") or [])
        print(f"\n[{name}] 请求 {len(asins)} 个 -> code={j.get('code')} msg={j.get('msg')!r} bidInfo={len(info)}")
        if info:
            print("   ", json.dumps(info[:6], ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
