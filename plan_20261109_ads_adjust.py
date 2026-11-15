#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""11-09 广告调整执行器（依据 docs/plan_2026-11-09_ads_adjust.md）

默认 dry-run：只做「读当前值 + 打印将要发出的请求」，不会发任何写请求。
确认无误后再加 --apply 真执行。

用法
    python3 plan_20261109_ads_adjust.py                  # 预演(默认)
    python3 plan_20261109_ads_adjust.py --only A,C       # 只预演 A、C 组
    python3 plan_20261109_ads_adjust.py --apply --only A,C
    python3 plan_20261109_ads_adjust.py --rollback data/adjust_logs/xxx.json

安全设计
    1) 执行前逐个核对「前值」，与预案不符立即中止（防重复执行/覆盖人工改动）
    2) 每步单独打印请求体，--apply 时逐条落盘日志（含前值，可回滚）
    3) 写请求间隔 2s，不做重试风暴；单个失败即停后续步骤
    4) code != 0 视为失败并抛出，不吞错误
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import requests

import config

ORIGIN = "https://www.sellfox.com"
GW = "/api/gw/sellfox/sellfox-cpc/api/sellfox/"
SHOP = "711811"
MARKETPLACE = "A1VC38T7YXB528"
LOG_DIR = os.path.join(config.DATA_DIR, "adjust_logs")

# 这些列表接口的 startDate/endDate 不能为空（空则 360000）；出价/比例与区间无关，
# 取当天即可
_DATE = datetime.now().strftime("%Y-%m-%d")

HDR_JSON = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": ORIGIN,
    "Referer": config.REFERER,
    "User-Agent": config.USER_AGENT,
}
HDR_FORM = dict(HDR_JSON, **{"Content-Type": "application/x-www-form-urlencoded"})

# ---------------------------------------------------------------------------
# 预案（与 docs/plan_2026-11-09_ads_adjust.md 一一对应）
# ---------------------------------------------------------------------------
PLAN = {
    "A": [
        {"step": "A1", "type": "group_bid", "amazonCid": "90709676806138",
         "dxmGroupId": 11818153634, "groupName": "GR_排水沟_B0HJCLZPM4",
         "before": 20.0, "after": 26.0},
        {"step": "A2", "type": "target_bid", "amazonCid": "90709676806138",
         "ids": [14313810921, 14313810920, 14313810919, 14313810916],
         "before": 20.0, "after": 26.0},
        {"step": "A3", "type": "placement", "amazonCid": "90709676806138",
         "dxmCampaignId": 13804946433, "predicate": "placementProductPage",
         "before": 0, "after": 20},
        {"step": "A4", "type": "group_bid", "amazonCid": "113908165179926",
         "dxmGroupId": 11817682331, "groupName": "GR_W型防撞角_B0HJ274S14",
         "before": 30.0, "after": 39.0},
        {"step": "A5", "type": "target_bid", "amazonCid": "113908165179926",
         "ids": [14313809589, 14313809569, 14313809588, 14313809590],
         "before": 30.0, "after": 39.0},
        {"step": "A6", "type": "placement", "amazonCid": "113908165179926",
         "dxmCampaignId": 13804825329, "predicate": "placementProductPage",
         "before": 0, "after": 20},
    ],
    "B": [
        {"step": "B1", "type": "create", "name": "CA_排水沟_B0HJCLZPM4_竞投B0GZJR2TVF",
         "groupName": "GR_竞投B0GZJR2TVF", "budget": "100", "bid": "35",
         "product": {"id": 57615613, "sku": "abc033-e-a", "asin": "B0HJCCY8MS",
                     "mainImage": "https://m.media-amazon.com/images/I/71uXjOugB7L.jpg"},
         "targetAsin": "B0GZJR2TVF", "start": "2026-11-10", "end": "2026-12-10"},
        {"step": "B2", "type": "create", "name": "CA_W型防撞角_B0HJ274S14_竞投B0HFNS6DS9",
         "groupName": "GR_竞投B0HFNS6DS9", "budget": "100", "bid": "40",
         "product": {"id": 56739636, "sku": "abc010-長さ5m-ピンク", "asin": "B0HJ2476BX",
                     "mainImage": "https://m.media-amazon.com/images/I/41mu9upuF4L.jpg"},
         "targetAsin": "B0HFNS6DS9", "start": "2026-11-10", "end": "2026-12-10"},
    ],
    "C": [
        {"step": "C1", "type": "target_bid", "amazonCid": "57452546140024",
         "name": "CA_匹克球网_B0HJPDD8XV",
         "ids": [14313810906, 14313810903, 14313810904, 14313810902],
         "before": 20.0, "after": 15.0},
        {"step": "C2", "type": "target_bid", "amazonCid": "169999519387603",
         "name": "CA_木制猫屋_B0HKN42H6R",
         "ids": [14313811041, 14313811037, 14313811040, 14313811039],
         "before": 20.0, "after": 15.0},
        {"step": "C3", "type": "target_bid", "amazonCid": "234162204808225",
         "name": "CA_厨房燃气灶_B0HJYG85BK",
         "ids": [14313810895, 14313810893, 14313810907, 14313810892],
         "before": 20.0, "after": 15.0},
        {"step": "C4", "type": "target_bid", "amazonCid": "200747849489161",
         "name": "CA_壁挂式收纳壁架_B0HLDP321N",
         "ids": [14313812087, 14313812088, 14313812077, 14313812086],
         "before": 30.0, "after": 22.0},
        {"step": "C5", "type": "target_bid", "amazonCid": "276956384537792",
         "name": "CA_旅行用背包_B0HLD2DCST",
         "ids": [14313812075, 14313812051, 14313812074, 14313812049],
         "before": 30.0, "after": 22.0},
        {"step": "C6", "type": "target_bid", "amazonCid": "88247362850188",
         "name": "CA_战术胸挂_B0HLKZ1DLM",
         "ids": [14313812143, 14313812144, 14313812141, 14313812145],
         "before": 30.0, "after": 22.0},
        {"step": "C7", "type": "target_bid", "amazonCid": "251746535103294",
         "name": "CA_宠物车用软笼_B0HKLS2NBP",
         "ids": [14313810571, 14313810570, 14313810572, 14313810553],
         "before": 30.0, "after": 22.0},
        {"step": "C8", "type": "target_bid", "amazonCid": "135554617647734",
         "name": "CA_纯钛盖杯_B0HJ276G99",
         "ids": [14313809743, 14313809731, 14313809734, 14313809733],
         "before": 30.0, "after": 22.0},
        {"step": "C9", "type": "target_bid", "amazonCid": "69875152037809",
         "name": "CA_高尔夫练习网_B0HJFVVBHB",
         "ids": [14313809741, 14313809739, 14313809737, 14313809742],
         "before": 30.0, "after": 22.0},
        {"step": "C10", "type": "target_bid", "amazonCid": "253177867055866",
         "name": "CA_焚火台_B0HKV72L28",
         "ids": [14313810700, 14313810681, 14313810699, 14313810705],
         "before": 30.0, "after": 22.0},
    ],
}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Client:
    def __init__(self):
        self.s = requests.Session()
        cookies = json.load(open(config.COOKIE_PATH, encoding="utf-8"))
        for k, v in cookies.items():
            self.s.cookies.set(k, v, domain="www.sellfox.com", path="/")

    @staticmethod
    def _url(path):
        if path.startswith("http"):
            return path
        if path.startswith("/"):
            return ORIGIN + path
        return ORIGIN + GW + path

    def post_json(self, path, body, timeout=60):
        r = self.s.post(self._url(path), json=body, headers=HDR_JSON, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def post_form(self, path, body, timeout=60):
        r = self.s.post(self._url(path), data=body, headers=HDR_FORM, timeout=timeout)
        r.raise_for_status()
        return r.json()

    def get_json(self, path, params=None, timeout=60):
        r = self.s.get(self._url(path), params=params, headers=HDR_JSON, timeout=timeout)
        r.raise_for_status()
        return r.json()


def check_code(j, what):
    if not isinstance(j, dict) or j.get("code") != 0:
        raise RuntimeError(f"{what} 失败: {json.dumps(j, ensure_ascii=False)[:300]}")
    return j


# ---------------------------------------------------------------------------
# 读当前值
# ---------------------------------------------------------------------------
def fetch_targets(c, campaign_ids):
    """返回 {内部target id: bid}, 只看自动投放的 4 个匹配位"""
    out = {}
    j = check_code(c.post_json("multiple/target/getAllTargetData", {
        "orderField": "adCost", "orderType": "desc", "shopIdList": [SHOP],
        "campaignIdList": list(campaign_ids), "adType": "sp", "targetType": "auto",
        "matchTypes": [], "statusList": ["enabled", "paused"],
        "startDate": _DATE, "endDate": _DATE, "pageNo": 1, "pageSize": 200,
        "fromSingle": True, "useAdvanced": False}), "读投放")
    node = (j.get("data") or {})
    node = node.get("page") if isinstance(node.get("page"), dict) else node
    for r in node.get("rows") or []:
        out[str(r.get("id"))] = float(r.get("bid") or 0)
    return out


def fetch_groups(c, campaign_ids):
    out = {}
    j = check_code(c.post_json("multiple/group/getAllGroupData", {
        "shopIdList": [SHOP], "campaignIdList": list(campaign_ids), "adType": "sp",
        "startDate": _DATE, "endDate": _DATE, "pageNo": 1, "pageSize": 200}),
        "读广告组")
    node = (j.get("data") or {})
    node = node.get("page") if isinstance(node.get("page"), dict) else node
    for r in node.get("rows") or []:
        out[str(r.get("id"))] = float(r.get("defaultBid") or 0)
    return out


def fetch_placements(c, campaign_ids):
    """返回 {(campaignId, predicate): percentage}"""
    out = {}
    j = check_code(c.post_json("multiple/placement/getAllPlacementData", {
        "dxmCampaignId": "", "campaignSite": "", "orderField": "adCost",
        "orderType": "desc", "adType": "sp", "campaignIdList": list(campaign_ids),
        "devIds": [], "strategyType": "", "predicate": "", "startDate": _DATE,
        "endDate": _DATE, "isCompare": False, "compareStartDate": "",
        "compareEndDate": "", "adStrategyTypeList": [], "pageNo": 1, "pageSize": 50,
        "fromSingle": True}), "读广告位")
    node = (j.get("data") or {})
    node = node.get("page") if isinstance(node.get("page"), dict) else node
    for r in node.get("rows") or []:
        out[(str(r.get("campaignId")), r.get("predicate"))] = float(r.get("percentage") or 0)
    return out


# ---------------------------------------------------------------------------
# 构造请求
# ---------------------------------------------------------------------------
def build_requests(step):
    """把一步预案翻译成 [(method, path, body_kind, body, desc)]"""
    t = step["type"]
    if t == "target_bid":
        return [("POST", "spTarget/updateBid", "form",
                 {"shopId": SHOP, "id": i, "bid": step["after"]},
                 f"匹配位 {i}: {step['before']:g} → {step['after']:g}")
                for i in step["ids"]]

    if t == "group_bid":
        return [("POST", "/api/cpcSPManage/group/update.json", "json",
                 {"shopId": SHOP, "dxmGroupId": step["dxmGroupId"],
                  "name": step["groupName"], "defaultBid": f"{step['after']:.2f}"},
                 f"组 {step['groupName']} 默认竞价 {step['before']:g} → {step['after']:g}")]

    if t == "placement":
        return [("POST", "/api/cpcSPManage/campaign/updatePlacementAdjustment.json", "form",
                 {"shopId": SHOP, "id": step["dxmCampaignId"],
                  "placement": step["predicate"], "value": int(step["after"])},
                 f"广告位 {step['predicate']} 加价 {step['before']:g} → {step['after']:g}")]

    if t == "create":
        p = step["product"]
        return [("POST", "ad/sp/create", "json", {
            "shopId": SHOP, "marketplaceId": MARKETPLACE,
            "spCampaignInfo": {
                "campaignId": "", "startDateStr": step["start"], "endDateStr": step["end"],
                "dailyBudget": step["budget"], "targetingType": "manual",
                "strategy": "legacyForSales",
                "placementProductPage": None, "placementTop": None,
                "placementRestOfSearch": None, "placementSiteAmazonBusiness": None,
                "creativeBidPercentage": None, "campaignSite": "amazonAll",
                "name": step["name"], "portfolioId": step.get("portfolioId", "-1"),
                "audienceId": "", "audienceBidPercentage": "", "defaultAudience": False},
            "spGroupInfo": {
                "groupId": "", "defaultBid": step["bid"], "name": step["groupName"],
                "products": [{"id": p["id"], "sku": p["sku"], "asin": p["asin"],
                              "mainImage": p["mainImage"], "spotlightVideos": None}]},
            "spTargetInfo": {
                "autoTarget": False, "keywordTarget": False, "keywords": [],
                "targetings": [{"type": "asin", "asin": step["targetAsin"],
                                "bid": step["bid"], "isAdd": True}],
                "neKeywords": [], "neTargetings": []},
        }, f"新建活动 {step['name']}（竞投 {step['targetAsin']}，预算 {step['budget']}，"
           f"出价 {step['bid']}）")]

    raise ValueError(f"未知 type: {t}")


def precheck(c, steps):
    """核对前值；返回问题列表（空 = 可以执行）"""
    problems = []
    cids = sorted({s["amazonCid"] for s in steps if s.get("amazonCid")})
    targets = fetch_targets(c, cids) if cids else {}
    groups = fetch_groups(c, cids) if cids else {}
    places = fetch_placements(c, cids) if cids else {}

    for s in steps:
        t = s["type"]
        if t == "target_bid":
            for i in s["ids"]:
                cur = targets.get(str(i))
                if cur is None:
                    problems.append(f"{s['step']}: 匹配位 {i} 在接口里不存在")
                elif abs(cur - s["before"]) > 1e-6:
                    problems.append(f"{s['step']}: 匹配位 {i} 当前 {cur:g} ≠ 预案前值 "
                                    f"{s['before']:g}（可能已被人改过）")
        elif t == "group_bid":
            cur = groups.get(str(s["dxmGroupId"]))
            if cur is None:
                problems.append(f"{s['step']}: 组 {s['dxmGroupId']} 不存在")
            elif abs(cur - s["before"]) > 1e-6:
                problems.append(f"{s['step']}: 组默认竞价当前 {cur:g} ≠ 预案前值 "
                                f"{s['before']:g}")
        elif t == "placement":
            cur = places.get((s["amazonCid"], s["predicate"]))
            if cur is None:
                problems.append(f"{s['step']}: 广告位 {s['predicate']} 读不到")
            elif abs(cur - s["before"]) > 1e-6:
                problems.append(f"{s['step']}: {s['predicate']} 当前 {cur:g} ≠ 预案前值 "
                                f"{s['before']:g}")
    return problems


def verify(c, steps):
    """执行后读回校验；返回不一致项"""
    bad = []
    cids = sorted({s["amazonCid"] for s in steps if s.get("amazonCid")})
    targets = fetch_targets(c, cids) if cids else {}
    groups = fetch_groups(c, cids) if cids else {}
    places = fetch_placements(c, cids) if cids else {}

    for s in steps:
        if s["type"] == "target_bid":
            for i in s["ids"]:
                cur = targets.get(str(i))
                if cur is None or abs(cur - s["after"]) > 1e-6:
                    bad.append(f"{s['step']}: 匹配位 {i} 读回 {cur} ≠ {s['after']:g}")
        elif s["type"] == "group_bid":
            cur = groups.get(str(s["dxmGroupId"]))
            if cur is None or abs(cur - s["after"]) > 1e-6:
                bad.append(f"{s['step']}: 组默认竞价读回 {cur} ≠ {s['after']:g}")
        elif s["type"] == "placement":
            cur = places.get((s["amazonCid"], s["predicate"]))
            if cur is None or abs(cur - s["after"]) > 1e-6:
                bad.append(f"{s['step']}: {s['predicate']} 读回 {cur} ≠ {s['after']:g}")
    return bad


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真的执行；不加则只预演")
    ap.add_argument("--only", default="", help="只跑指定组，如 A,C")
    ap.add_argument("--gap", type=float, default=2.0, help="写请求间隔秒数")
    ap.add_argument("--portfolio", default="-1",
                    help="B 组新建活动放入的组合 id；默认 -1(无组合)")
    ap.add_argument("--rollback", default="", help="按日志回滚")
    args = ap.parse_args()

    groups = [g.strip().upper() for g in args.only.split(",") if g.strip()] or list(PLAN)
    bad = [g for g in groups if g not in PLAN]
    if bad:
        sys.exit(f"未知分组: {bad}，可选 {list(PLAN)}")

    steps = []
    for g in groups:
        for s in PLAN[g]:
            if s["type"] == "create":
                s = dict(s, portfolioId=args.portfolio)
            steps.append(s)

    c = Client()

    if args.rollback:
        return rollback(c, args.rollback, args.gap)

    print(f"店铺 {SHOP}｜分组 {','.join(groups)}｜共 {len(steps)} 步"
          f"｜模式 {'执行' if args.apply else '预演(dry-run)'}")
    print("=" * 78)

    print("[1/3] 核对当前值是否与预案一致 ...")
    problems = precheck(c, steps)
    if problems:
        print("✗ 前置校验未通过，已中止（未发出任何写请求）：")
        for p in problems:
            print("   -", p)
        sys.exit(1)
    print("    ✓ 前值全部一致")

    print("\n[2/3] 将要发出的请求：")
    total = 0
    for s in steps:
        for method, path, kind, body, desc in build_requests(s):
            total += 1
            print(f"\n  [{s['step']}] {desc}")
            print(f"    {method} {Client._url(path)}")
            print(f"    Content-Type: {'application/json' if kind == 'json' else 'application/x-www-form-urlencoded'}")
            print(f"    {json.dumps(body, ensure_ascii=False)}")
    print(f"\n  合计 {total} 个写请求")

    if not args.apply:
        print("\n[3/3] 预演结束。未发出任何写请求。")
        print("      确认无误后执行：python3 plan_20261109_ads_adjust.py --apply --only "
              + ",".join(groups))
        return

    print(f"\n[3/3] 开始执行（间隔 {args.gap}s）...")
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, datetime.now().strftime("adjust_%Y%m%d_%H%M%S.json"))
    log = {"shop": SHOP, "at": datetime.now().isoformat(timespec="seconds"),
           "groups": groups, "steps": []}

    sent = 0
    for s in steps:
        rec = {"step": s["step"], "type": s["type"], "requests": []}
        for method, path, kind, body, desc in build_requests(s):
            print(f"  → [{s['step']}] {desc} ... ", end="", flush=True)
            try:
                j = c.post_json(path, body) if kind == "json" else c.post_form(path, body)
                check_code(j, desc)
                print("ok")
                rec["requests"].append({"path": path, "body": body, "resp": j})
                sent += 1
            except Exception as e:      # noqa: BLE001
                print("FAIL")
                rec["error"] = str(e)
                log["steps"].append(rec)
                json.dump(log, open(log_path, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
                print(f"\n✗ 已中止：{e}")
                print(f"  日志: {log_path}")
                print(f"  回滚: python3 plan_20261109_ads_adjust.py --rollback {log_path}")
                sys.exit(1)
            if args.gap:
                time.sleep(args.gap)
        log["steps"].append(rec)

    json.dump(log, open(log_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n  已发出 {sent} 个写请求，日志 {log_path}")

    print("\n[校验] 读回确认 ...")
    time.sleep(3)
    mism = verify(c, [s for s in steps if s["type"] != "create"])
    if mism:
        print("  ✗ 以下项读回与预期不一致：")
        for m in mism:
            print("   -", m)
        print(f"  如需回滚: --rollback {log_path}")
        sys.exit(1)
    print("  ✓ 全部读回一致")
    print("\n完成。B 组（新建活动）请在 3 天后看是否出单再决定保留。")


def rollback(c, log_path, gap):
    log = json.load(open(log_path, encoding="utf-8"))
    plan_before = {}
    for g in log["groups"]:
        for s in PLAN[g]:
            plan_before[s["step"]] = s

    print(f"回滚 {log_path}")
    done = 0
    for rec in log["steps"]:
        s = plan_before.get(rec["step"])
        if not s or "error" in rec:
            continue
        t = s["type"]
        if t == "target_bid":
            for i in s["ids"]:
                c.post_form("spTarget/updateBid",
                            {"shopId": SHOP, "id": i, "bid": s["before"]})
                print(f"  ← 匹配位 {i} 恢复 {s['before']:g}")
                done += 1
                time.sleep(gap)
        elif t == "group_bid":
            c.post_json("/api/cpcSPManage/group/update.json",
                        {"shopId": SHOP, "dxmGroupId": s["dxmGroupId"],
                         "name": s["groupName"], "defaultBid": f"{s['before']:.2f}"})
            print(f"  ← 组 {s['groupName']} 默认竞价恢复 {s['before']:g}")
            done += 1
            time.sleep(gap)
        elif t == "placement":
            c.post_form("/api/cpcSPManage/campaign/updatePlacementAdjustment.json",
                        {"shopId": SHOP, "id": s["dxmCampaignId"],
                         "placement": s["predicate"], "value": int(s["before"])})
            print(f"  ← {s['predicate']} 加价恢复 {s['before']:g}")
            done += 1
            time.sleep(gap)
        elif t == "create":
            print(f"  ! {s['name']} 是新建活动，需手工归档（或用 spCampaign/archive + 内部 id）")
    print(f"\n回滚完成 {done} 项。")
    print("提示：新建活动请到赛狐列表按名字搜索后手工归档。")


if __name__ == "__main__":
    main()
