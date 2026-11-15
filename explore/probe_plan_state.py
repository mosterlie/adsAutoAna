# -*- coding: utf-8 -*-
"""只读: 为「11-08 出单归因后的调整预案」采集当前真实值(不发任何写请求)"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from backend.sellfox_client import SellfoxClient

SHOP = [711811]
DAY_S, DAY_E = "2026-11-01", "2026-11-08"

WIN = {
    "90709676806138": "CA_排水沟_B0HJCLZPM4",
    "113908165179926": "CA_W型防撞角_B0HJ274S14",
}
ZERO = {
    "400": "CA_匹克球网_B0HJPDD8XV",
    "343": "CA_木制猫屋_B0HKN42H6R",
    "336": "CA_厨房燃气灶_B0HJYG85BK",
    "328": "CA_壁挂式收纳壁架_B0HLDP321N",
    "300": "CA_旅行用背包_B0HLD2DCST",
    "299": "CA_战术胸挂_B0HLKZ1DLM",
    "210": "CA_宠物车用软笼_B0HKLS2NBP",
    "210": "CA_纯钛盖杯_B0HJ276G99",
    "202": "CA_高尔夫练习网_B0HJFVVBHB",
    "148": "CA_焚火台_B0HKV72L28",
}

c = SellfoxClient(json.load(open(config.COOKIE_PATH)))
camp = c.fetch_list("campaign", SHOP, "2026-11-08", "2026-11-08", scope={})
by_name = {r.get("name"): r for r in camp}

out = {"windows": {}, "zero": {}, "groups": [], "placements": {}, "negatives": {}}

# 1) 两个出单活动: 内部 id / 预算 / 策略 / 广告位
for cid, nm in WIN.items():
    r = by_name.get(nm)
    if not r:
        print("!! 找不到活动", nm)
        continue
    out["windows"][cid] = {
        "name": nm, "dxmCampaignId": r.get("id"), "campaignId": cid,
        "dailyBudget": r.get("dailyBudget"), "strategy": r.get("strategy"),
        "targetingType": r.get("campaignTargetingType"),
        "placementTop": r.get("placementTop"),
        "placementProductPage": r.get("placementProductPage"),
        "placementRestOfSearch": r.get("placementRestOfSearch"),
        "portfolioName": r.get("portfolioName"), "portfolioId": r.get("portfolioId"),
        "state": r.get("state"), "servingStatus": r.get("servingStatusName"),
    }

# 2) top10 零转化活动
for nm in ZERO.values():
    r = by_name.get(nm)
    if not r:
        print("!! 找不到零转化活动", nm)
        continue
    out["zero"][r.get("campaignId")] = {
        "name": nm, "dxmCampaignId": r.get("id"), "campaignId": r.get("campaignId"),
        "adCost": r.get("adCost"), "clicks": r.get("clicks"),
        "dailyBudget": r.get("dailyBudget"), "strategy": r.get("strategy"),
        "state": r.get("state"),
    }

# 3) 广告组: 内部 id + Amazon id + 默认竞价
allc = list(WIN.keys()) + list(out["zero"].keys())
for i in range(0, len(allc), 8):
    rows = c.fetch_list("group", SHOP, "2026-11-08", "2026-11-08",
                        scope={"adType": "sp"}, page_size=200)
    kept = [r for r in rows if str(r.get("campaignId")) in allc]
    out["groups"] = [{"campaignId": r.get("campaignId"), "campaignName": r.get("campaignName"),
                      "dxmGroupId": r.get("id"), "adGroupId": r.get("adGroupId"),
                      "name": r.get("name"), "defaultBid": r.get("defaultBid"),
                      "state": r.get("state")} for r in kept]
    break

# 4) 广告位当前比例(两个出单活动)
for cid in WIN:
    rows = c.fetch_list("placement", SHOP, "2026-11-08", "2026-11-08", scope={"adType": "sp"})
    out["placements"][cid] = [
        {"predicate": r.get("predicate"), "percentage": r.get("percentage"),
         "id": r.get("id"), "strategy": r.get("strategy"),
         "adCost": r.get("adCost"), "clicks": r.get("clicks")}
        for r in rows if str(r.get("campaignId")) == cid]

json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "analysis", "plan_state.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

print("\n=== 出单活动当前值 ===")
for k, v in out["windows"].items():
    print(f"  {v['name']}")
    print(f"    dxmCampaignId={v['dxmCampaignId']} 预算={v['dailyBudget']} 策略={v['strategy']}")
    print(f"    广告位: 顶部={v['placementTop']} 商品页={v['placementProductPage']} 其余={v['placementRestOfSearch']}")

print("\n=== 广告组(默认竞价) ===")
for g in out["groups"]:
    tag = "出单" if str(g["campaignId"]) in WIN else "零转化"
    print(f"  [{tag}] {g['campaignName'][:26]:<28} {g['name'][:24]:<26}"
          f" dxmGroupId={g['dxmGroupId']} adGroupId={g['adGroupId']} bid={g['defaultBid']}")

print("\n=== 广告位明细 ===")
for cid, rows in out["placements"].items():
    print(" ", WIN[cid])
    for r in rows:
        print(f"    {r['predicate']:<24} percentage={r['percentage']} 花费={r['adCost']}")
