# -*- coding: utf-8 -*-
"""测试: 同一品, 父 ASIN 与子 ASIN 调 suggestBid 的返回是否一致。"""
import json

import requests
from playwright.sync_api import sync_playwright

H = {"content-type": "application/json", "origin": "https://www.sellfox.com",
     "referer": "https://www.sellfox.com/"}
API = ("https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox"
       "/multiple/target/suggestBid/recommendations")
LIST = ("https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox"
        "/product/productRightSpPageListSearch")
SHOP = "711811"


def post(url, body, ck):
    r = requests.post(url, json=body, cookies=ck, headers=H, timeout=30)
    try:
        return r.status_code, r.json()
    except Exception:  # noqa: BLE001
        return r.status_code, {"raw": r.text[:200]}


def bid(ck, asins, strategy="AUTO_FOR_SALES"):
    body = {"shopId": SHOP, "biddingStrategy": strategy, "asins": asins,
            "targetingExpressions": [{"type": t, "value": ""} for t in
                                     ("CLOSE_MATCH", "LOOSE_MATCH", "SUBSTITUTES", "COMPLEMENTS")],
            "placementProductPage": None, "placementTop": None,
            "placementRestOfSearch": None, "campaignId": "", "groupId": ""}
    return post(API, body, ck)


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ck = {c["name"]: c["value"] for c in b.contexts[0].cookies("https://www.sellfox.com")}
        b.close()

    code, d = post(LIST, {"pageNo": 1, "pageSize": 300, "shopId": SHOP, "searchField": "asin",
                          "searchValue": "", "searchType": "exact", "onlineStatus": "",
                          "orderType": "desc", "type": "sp"}, ck)
    rows = (d.get("data") or {}).get("rows", [])
    print("商品行:", len(rows), "| 字段:", sorted(rows[0].keys())[:18] if rows else "-")

    kids = [r.get("asin") for r in rows if r.get("asin")]
    parents = [r.get("parentAsin") or r.get("parent_asin") for r in rows]
    parents = [x for x in parents if x]
    print("子 ASIN 数:", len(kids), "| 父 ASIN 数:", len(parents))

    # 同一品: 取一个有父 ASIN 的样品
    sample = next((r for r in rows if (r.get("parentAsin") or r.get("parent_asin"))), None)
    print("\n样品行:", json.dumps({k: v for k, v in (sample or {}).items()
                                  if k in ("asin", "sku", "parentAsin", "parent_asin", "title")},
                                 ensure_ascii=False)[:300])

    tests = [
        ("子ASIN 单个", [sample["asin"]] if sample else []),
        ("父ASIN 单个", [sample.get("parentAsin") or sample.get("parent_asin")] if sample else []),
        ("父子一起", [sample["asin"], sample.get("parentAsin") or sample.get("parent_asin")] if sample else []),
        ("全部子ASIN(前60)", kids[:60]),
        ("全部父ASIN(前60)", parents[:60]),
    ]
    for name, asins in tests:
        asins = [a for a in asins if a]
        if not asins:
            print(f"\n{name}: 无可用 ASIN")
            continue
        code, j = bid(ck, asins)
        info = ((j or {}).get("data") or {}).get("bidInfo", [])
        print(f"\n{name} ({len(asins)} 个) -> HTTP {code} code={j.get('code')} "
              f"msg={j.get('msg')!r} bidInfo={len(info)}")
        if info:
            print("   样例:", json.dumps(info[:3], ensure_ascii=False)[:600])

    # 换竞价策略再试一次
    for strat in ("MANUAL", "AUTO_FOR_PAGE_VISIT"):
        code, j = bid(ck, kids[:20], strat)
        info = ((j or {}).get("data") or {}).get("bidInfo", [])
        print(f"\n策略 {strat} -> code={j.get('code')} bidInfo={len(info)}")


if __name__ == "__main__":
    main()
