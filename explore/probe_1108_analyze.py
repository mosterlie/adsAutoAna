# -*- coding: utf-8 -*-
"""出单归因: 活动 -> 广告产品(ASIN) -> 搜索词"""
import json
import os
import sys
from collections import defaultdict

DAY = sys.argv[1] if len(sys.argv) > 1 else "2026-11-08"
BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis", f"{DAY}_attribution")


def load(n):
    p = os.path.join(BASE, f"{n}.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


camp = load("campaign")
prod = load("product_sp")
kw = load("search_keyword")
asz = load("search_asin")

print(f"=== {DAY} ===")
print(f"活动行 {len(camp)} / 去重后 {len({r.get('campaignId') for r in camp})} 个活动")
print(f"广告产品行 {len(prod)} / 去重后 {len({(r.get('campaignId'), r.get('asin')) for r in prod})} 组")
print(f"搜索词 {len(kw)} 行 / 搜索ASIN {len(asz)} 行")

# 活动去重(同 campaignId 可能因 adType 维度重复) —— 取订单/花费最大的一条
by_cid = {}
for r in camp:
    cid = r.get("campaignId")
    old = by_cid.get(cid)
    if old is None or f(r.get("adOrderNum")) > f(old.get("adOrderNum")):
        by_cid[cid] = r

print(f"\n合计: 曝光 {sum(f(r.get('impressions')) for r in by_cid.values()):,.0f}"
      f" 点击 {sum(f(r.get('clicks')) for r in by_cid.values()):,.0f}"
      f" 花费 {sum(f(r.get('adCost')) for r in by_cid.values()):,.0f} JPY"
      f" 订单 {sum(f(r.get('adOrderNum')) for r in by_cid.values()):,.0f}"
      f" 销售额 {sum(f(r.get('adSale')) for r in by_cid.values()):,.0f} JPY")

# 1) 出单活动
win = [r for r in by_cid.values() if f(r.get("adOrderNum")) > 0]
win.sort(key=lambda r: -f(r.get("adOrderNum")))
print(f"\n--- 出单活动 {len(win)} 个 ---")
for r in win:
    print(f"  {r.get('campaignName')}  | id={r.get('campaignId')}"
          f" | 类型={r.get('campaignTargetingType')}"
          f" | 订单={f(r.get('adOrderNum')):.0f} 销量={f(r.get('adSaleNum')):.0f}"
          f" 销售额={f(r.get('adSale')):,.0f} 花费={f(r.get('adCost')):,.0f}"
          f" ACOS={r.get('acos')} 点击={f(r.get('clicks')):.0f}"
          f" 曝光={f(r.get('impressions')):.0f} 状态={r.get('state') or r.get('campaignState')}")

# 2) 出单产品
wp = [r for r in prod if f(r.get("adOrderNum")) > 0]
wp.sort(key=lambda r: -f(r.get("adOrderNum")))
print(f"\n--- 出单广告产品 {len(wp)} 行 ---")
for r in wp:
    print(f"  ASIN={r.get('asin')} | SKU={r.get('sku')} | 活动={r.get('campaignName')}"
          f" | 组={r.get('adGroupName')}")
    print(f"      订单={f(r.get('adOrderNum')):.0f} 销量={f(r.get('adSaleNum')):.0f}"
          f" 销售额={f(r.get('adSale')):,.0f} 花费={f(r.get('adCost')):,.0f}"
          f" 点击={f(r.get('clicks')):.0f} 曝光={f(r.get('impressions')):.0f}"
          f" ACOS={r.get('acos')}")
    print(f"      标题: {(r.get('title') or r.get('productTitle') or '')[:70]}")

# 3) 出单搜索词
wk = [r for r in kw if f(r.get("adOrderNum")) > 0]
wa = [r for r in asz if f(r.get("adOrderNum")) > 0]
print(f"\n--- 出单搜索词 {len(wk)} 行 / 出单搜索ASIN {len(wa)} 行 ---")
for r in wk + wa:
    print(f"  [{r.get('searchQueryType') or 'kw'}] {r.get('query')}"
          f" | match={r.get('matchType')} | 活动={r.get('campaignName')}"
          f" | 订单={f(r.get('adOrderNum')):.0f} 销售额={f(r.get('adSale')):,.0f}"
          f" 花费={f(r.get('adCost')):,.0f} 点击={f(r.get('clicks')):.0f}")

# 4) 按出单活动看它的搜索词/产品明细(即使这些明细行本身 0 单, 也说明流量结构)
if win:
    ids = {r.get("campaignId") for r in win}
    print(f"\n--- 出单活动下的搜索词(全部, 看流量来源) ---")
    for r in sorted([x for x in kw if x.get("campaignId") in ids],
                    key=lambda x: -f(x.get("clicks")))[:15]:
        print(f"  {r.get('query')} | match={r.get('matchType')}"
              f" 点击={f(r.get('clicks')):.0f} 花费={f(r.get('adCost')):,.0f}"
              f" 订单={f(r.get('adOrderNum')):.0f} | {r.get('campaignName')}")
    print(f"\n--- 出单活动下的搜索ASIN(全部) ---")
    for r in sorted([x for x in asz if x.get("campaignId") in ids],
                    key=lambda x: -f(x.get("clicks")))[:15]:
        print(f"  {r.get('query')} | 点击={f(r.get('clicks')):.0f}"
              f" 花费={f(r.get('adCost')):,.0f} 订单={f(r.get('adOrderNum')):.0f}"
              f" | {r.get('campaignName')}")
