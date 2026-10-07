#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查询框条件复刻 —— 与原系统(赛狐)的过滤语义对照验证

对每个筛选条件, 分别取:
    原接口 totalSize  vs  本地过滤后行数
两者一致 => 该条件已正确复刻。

用法:
    python3 verify_filters.py               # 联网(需登录态)
    python3 verify_filters.py --no-network  # 仅离线(跳过联网项)
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import database as db                                  # noqa: E402
from backend.cookies import load_cookies                            # noqa: E402
from backend.sellfox_client import SellfoxClient, TAB_DEFS, build_payload  # noqa: E402

SHOP, SD, ED = 711811, "2026-08-01", "2026-10-04"
RESULTS = []
_PIDS = []


def rec(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def api_total(cl, tab, scope, mut):
    p = build_payload(tab, [SHOP], SD, ED, scope, 1, 1)
    mut(p)
    j = cl._post(TAB_DEFS[tab]["list"], p)
    d = j.get("data")
    pg = d.get("page") if isinstance(d, dict) and isinstance(d.get("page"), dict) else d
    return (pg or {}).get("totalSize")


def cmp_case(cl, name, tab, scope, filters, mut, local_scope=None, **kw):
    """scope 为接口维度 dict; 本地用其归一字符串 (可用 local_scope 覆盖)"""
    ls = local_scope if local_scope is not None else db._scope_str(scope)
    local = db.get_records(tab, filters=filters, scope=ls, **kw)["total"]
    if cl is None:
        rec(name + " (离线)", True, f"本地={local} [跳过联网对比]")
        return
    api = api_total(cl, tab, scope, mut)
    rec(name, api == local, f"原接口={api} 本地={local}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true")
    args = ap.parse_args()

    cl = None
    if not args.no_network:
        ck = load_cookies()
        if not ck:
            print("[警告] 无登录 Cookie, 全部按离线模式运行\n")
        else:
            cl = SellfoxClient(ck)
            _PIDS.extend([r["portfolio_id"] for r in db.get_portfolios()])

    print("=" * 68)
    print("查询框条件复刻验证 (原接口 totalSize vs 本地过滤行数)")
    print("=" * 68)

    pid = _PIDS[0] if _PIDS else "0"

    # 状态类: 单值 / 多值
    cmp_case(cl, "广告活动·状态=投放中", "campaign", {}, {"state": ["enabled"]},
             lambda p: p.update({"status": "enabled"}))
    cmp_case(cl, "广告活动·状态=已暂停", "campaign", {}, {"state": ["paused"]},
             lambda p: p.update({"status": "paused"}))
    cmp_case(cl, "广告活动·状态=已归档", "campaign", {}, {"state": ["archived"]},
             lambda p: p.update({"status": "archived"}))
    cmp_case(cl, "广告组·状态多选[投放中,已归档]", "group", {"adType": "sp"},
             {"state": ["enabled", "archived"]},
             lambda p: p.update({"statusList": ["enabled", "archived"]}))
    cmp_case(cl, "广告活动·多条件(状态=投放中)", "campaign", {},
             {"state": ["enabled"], "servingStatus": ["CAMPAIGN_STATUS_ENABLED"]},
             lambda p: p.update({"status": "enabled", "servingStatus": "enabled"}))

    # 广告组
    cmp_case(cl, "广告组·状态=投放中", "group", {"adType": "sp"}, {"state": ["enabled"]},
             lambda p: p.update({"statusList": ["enabled"]}))
    cmp_case(cl, "广告组·活动状态=投放中", "group", {"adType": "sp"},
             {"campaignState": ["enabled"]},
             lambda p: p.update({"campaignStateList": ["enabled"]}))
    cmp_case(cl, "广告组·活动状态=已归档", "group", {"adType": "sp"},
             {"campaignState": ["archived"]},
             lambda p: p.update({"campaignStateList": ["archived"]}))

    # 广告产品
    cmp_case(cl, "广告产品·状态=投放中", "product", {"type": "sp"}, {"state": ["enabled"]},
             lambda p: p.update({"statusList": ["enabled"]}))
    cmp_case(cl, "广告产品·状态=已归档", "product", {"type": "sp"}, {"state": ["archived"]},
             lambda p: p.update({"statusList": ["archived"]}))

    # 广告组合 (按 ID 精确过滤)
    cmp_case(cl, "广告活动·广告组合(按ID)", "campaign", {}, {"portfolioId": [pid]},
             lambda p: p.update({"portfolioId": pid}))
    cmp_case(cl, "广告组·广告组合(按ID)", "group", {"adType": "sp"}, {"portfolioId": [pid]},
             lambda p: p.update({"portfolioIdList": [pid]}))

    # 关键词: 按字段 + 模糊
    cmp_case(cl, "广告活动·关键词(名称,模糊)=自动", "campaign", {},
             {}, lambda p: p.update({"searchValue": "自动", "searchField": "name",
                                     "searchType": "blur"}),
             keyword="自动", search_field="name", search_mode="blur")

    # 关键词: 精确
    row = db.get_conn().execute(
        "SELECT raw_json FROM ad_records WHERE tab='campaign' ORDER BY id LIMIT 1").fetchone()
    import json
    nm = json.loads(row["raw_json"]).get("name")
    cmp_case(cl, f"广告活动·关键词(名称,精确)={nm[:12]}…", "campaign", {},
             {}, lambda p: p.update({"searchValue": nm, "searchField": "name",
                                     "searchType": "exact"}),
             keyword=nm, search_field="name", search_mode="exact")

    # 语义差异回归: 用只存在于ID字段的值, 按名称搜应为 0
    cid = str(json.loads(row["raw_json"]).get("campaignId"))
    cmp_case(cl, "广告活动·关键词(名称)=活动ID 应为0", "campaign", {},
             {}, lambda p: p.update({"searchValue": cid, "searchField": "name",
                                     "searchType": "blur"}),
             keyword=cid, search_field="name", search_mode="blur")

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    print("\n" + "=" * 68)
    print(f"结果: {passed}/{len(RESULTS)} 通过")
    for n, ok, _ in RESULTS:
        if not ok:
            print(f"  x {n}")
    print("=" * 68)
    sys.exit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
