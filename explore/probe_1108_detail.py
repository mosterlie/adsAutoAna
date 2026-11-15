# -*- coding: utf-8 -*-
"""2026-11-08 出单归因详解: 活动链路 / 流量位 / 预算浪费"""
import json
import os
from collections import defaultdict

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis",
                    "2026-11-08_attribution")


def load(n):
    p = os.path.join(BASE, f"{n}.json")
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else []


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


camp = {r.get("campaignId"): r for r in load("campaign")}
prod = load("product_sp")
kw = load("search_keyword")
asz = load("search_asin")

WIN = {"90709676806138": "排水沟 B0HJCLZPM4", "113908165179926": "W型防撞角 B0HJ274S14"}

print("=" * 78)
print("一、出单活动的完整画像")
print("=" * 78)
for cid, tag in WIN.items():
    r = camp.get(cid) or {}
    print(f"\n【{tag}】{r.get('name')}  id={cid}")
    print(f"  投放类型={r.get('campaignTargetingType')}  状态={r.get('servingStatusName')}"
          f"  预算={r.get('dailyBudget')}  策略={r.get('strategy')}"
          f"  组合={r.get('portfolioName')}")
    print(f"  曝光={f(r.get('impressions')):,.0f} 点击={f(r.get('clicks')):,.0f}"
          f" 花费={f(r.get('adCost')):,.0f} 订单={f(r.get('adOrderNum')):.0f}"
          f" 销售额={f(r.get('adSale')):,.0f} ACOS={r.get('acos')}%"
          f" CTR={r.get('ctr')}% CVR={r.get('cvr')}%")
    print(f"  该活动下的广告产品:")
    for p in [x for x in prod if x.get("campaignId") == cid]:
        print(f"    - ASIN={p.get('asin')} SKU={p.get('sku')} 组={p.get('adGroupName')}"
              f" | 曝光{f(p.get('impressions')):,.0f} 点击{f(p.get('clicks')):,.0f}"
              f" 花费{f(p.get('adCost')):,.0f} 订单{f(p.get('adOrderNum')):.0f}"
              f" 销售{f(p.get('adSale')):,.0f}")
        print(f"      {(p.get('title') or '')[:78]}")
    print(f"  该活动的搜索词(全部):")
    rows = [x for x in kw if x.get("campaignId") == cid]
    for x in sorted(rows, key=lambda y: -f(y.get("clicks")))[:10]:
        print(f"    - {x.get('query')} | {x.get('matchType')}"
              f" | 曝光{f(x.get('impressions')):,.0f} 点击{f(x.get('clicks')):,.0f}"
              f" 花费{f(x.get('adCost')):,.0f} 订单{f(x.get('adOrderNum')):.0f}")
    if not rows:
        print("    (无搜索词记录 —— 该活动流量全部来自商品页面投放)")
    print(f"  该活动的搜索ASIN/商品页(全部):")
    rows = [x for x in asz if x.get("campaignId") == cid]
    for x in sorted(rows, key=lambda y: -f(y.get("clicks")))[:10]:
        print(f"    - {x.get('query')} | {x.get('matchType')}"
              f" | 曝光{f(x.get('impressions')):,.0f} 点击{f(x.get('clicks')):,.0f}"
              f" 花费{f(x.get('adCost')):,.0f} 订单{f(x.get('adOrderNum')):.0f}"
              f" 销售{f(x.get('adSale')):,.0f}")
    if not rows:
        print("    (无)")

print()
print("=" * 78)
print("二、花费 top10 活动(看钱花在哪、有没有出单)")
print("=" * 78)
top = sorted(camp.values(), key=lambda r: -f(r.get("adCost")))[:10]
print(f"{'活动名':<30}{'花费':>8}{'点击':>6}{'订单':>5}{'销售额':>9}{'ACOS':>8}{'CVR':>8}")
for r in top:
    print(f"{(r.get('name') or '')[:28]:<30}{f(r.get('adCost')):>8,.0f}"
          f"{f(r.get('clicks')):>6,.0f}{f(r.get('adOrderNum')):>5,.0f}"
          f"{f(r.get('adSale')):>9,.0f}{str(r.get('acos')):>8}{str(r.get('cvr')):>8}")

print()
print("=" * 78)
print("三、零转化花费(有花费无订单)")
print("=" * 78)
zero = [r for r in camp.values() if f(r.get("adCost")) > 0 and f(r.get("adOrderNum")) == 0]
zero.sort(key=lambda r: -f(r.get("adCost")))
print(f"零转化活动 {len(zero)} 个, 合计花费 {sum(f(r.get('adCost')) for r in zero):,.0f} JPY"
      f" / 全站 {sum(f(r.get('adCost')) for r in camp.values()):,.0f} JPY")
for r in zero[:10]:
    print(f"  {(r.get('name') or '')[:34]:<36}花费{f(r.get('adCost')):>8,.0f}"
          f" 点击{f(r.get('clicks')):>5,.0f} 曝光{f(r.get('impressions')):>7,.0f}"
          f" 加购{f(r.get('addToCart'))}")

print()
print("=" * 78)
print("四、有流量但零转化的搜索词/ASIN 位(否词候选)")
print("=" * 78)
cand = [x for x in kw + asz if f(x.get("clicks")) >= 2 and f(x.get("adOrderNum")) == 0]
cand.sort(key=lambda x: -f(x.get("adCost")))
print(f"点击≥2 且 0 单的投放位 {len(cand)} 条, 合计花费 {sum(f(x.get('adCost')) for x in cand):,.0f} JPY")
for x in cand[:15]:
    print(f"  {(x.get('query') or '')[:24]:<26}{x.get('matchType') or '':<14}"
          f" 点击{f(x.get('clicks')):>4,.0f} 花费{f(x.get('adCost')):>7,.0f}"
          f" | {(x.get('campaignName') or '')[:24]}")

print()
print("=" * 78)
print("五、全站结构与口径")
print("=" * 78)
live = [r for r in camp.values() if f(r.get("impressions")) > 0]
print(f"活动 {len(camp)} 个, 昨日有曝光 {len(live)} 个, 有花费 {len([r for r in camp.values() if f(r.get('adCost'))>0])} 个")
auto = len([r for r in live if r.get("campaignTargetingType") == "auto"])
print(f"有曝光活动中 自动={auto} 手动={len(live)-auto}")
print(f"花费 {sum(f(r.get('adCost')) for r in camp.values()):,.0f} JPY,"
      f" 销售额 {sum(f(r.get('adSale')) for r in camp.values()):,.0f} JPY,"
      f" ACOS {sum(f(r.get('adCost')) for r in camp.values())/max(sum(f(r.get('adSale')) for r in camp.values()),1)*100:.1f}%")
print(f"搜索词行 {len(kw)} / 搜索ASIN行 {len(asz)}")
kw_cost = sum(f(x.get('adCost')) for x in kw)
asz_cost = sum(f(x.get('adCost')) for x in asz)
total = sum(f(r.get('adCost')) for r in camp.values())
print(f"搜索词口径花费 {kw_cost:,.0f} ({kw_cost/total*100:.0f}%),"
      f" 商品页口径花费 {asz_cost:,.0f} ({asz_cost/total*100:.0f}%)")
