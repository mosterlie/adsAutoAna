# -*- coding: utf-8 -*-
"""近 7 日活动级订单趋势(仅取活动列表, 一天一次调用)"""
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from backend.sellfox_client import SellfoxClient

DAYS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05",
        "2026-11-06", "2026-11-07", "2026-11-08"]
SHOP = [711811]


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


c = SellfoxClient(json.load(open(config.COOKIE_PATH)))
out = {}
for d in DAYS:
    rows = c.fetch_list("campaign", SHOP, d, d, scope={})
    by = {}
    for r in rows:
        cid = r.get("campaignId")
        if cid not in by or f(r.get("adOrderNum")) > f(by[cid].get("adOrderNum")):
            by[cid] = r
    out[d] = by
    print(f"{d}  活动{len(by):4d}  曝光{sum(f(r.get('impressions')) for r in by.values()):8,.0f}"
          f"  点击{sum(f(r.get('clicks')) for r in by.values()):6,.0f}"
          f"  花费{sum(f(r.get('adCost')) for r in by.values()):8,.0f}"
          f"  订单{sum(f(r.get('adOrderNum')) for r in by.values()):5,.0f}"
          f"  销售额{sum(f(r.get('adSale')) for r in by.values()):9,.0f}")

p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analysis", "trend_7d.json")
json.dump({d: list(v.values()) for d, v in out.items()}, open(p, "w", encoding="utf-8"),
          ensure_ascii=False)
print("已存", p)

# 跨日: 哪些活动出过单
cnt = defaultdict(lambda: [0, 0, 0.0, ""])
for d, by in out.items():
    for cid, r in by.items():
        if f(r.get("adOrderNum")) > 0:
            cnt[cid][0] += f(r.get("adOrderNum"))
            cnt[cid][1] += 1
            cnt[cid][2] += f(r.get("adSale"))
            cnt[cid][3] = r.get("name")
print("\n--- 7 日内出过单的活动 ---")
for cid, (o, days, sale, name) in sorted(cnt.items(), key=lambda x: -x[1][0]):
    print(f"  {name} | 订单{o:.0f} 出单天数{days} 销售额{sale:,.0f} | {cid}")
