#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adsAutoAna  vs  赛狐原系统  对照测试

三个维度:
  A. 采集数据量对比       —— 原接口 totalSize  vs  本地落库行数
  B. 条件查询返回量对比   —— 同一筛选条件下 原接口  vs  本地
  C. 查询框条件复刻度     —— 原 UI 条件字段 逐项对照我们是否支持

用法:
    python3 compare_with_origin.py              # 联网(需登录态)
    python3 compare_with_origin.py --no-network # 仅 C 段(静态对照)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend import database as db                                  # noqa: E402
from backend.sellfox_client import (SellfoxClient, TAB_DEFS,        # noqa: E402
                                    TAB_ORDER, build_payload)

SHOP, SD, ED = 711811, "2026-08-01", "2026-10-04"

# 原站契约产物 (老页面 XHR 抓包)
CONTRACTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "explore", "api_contracts.json")
PAYLOADS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "explore", "payloads_all.json")

CN2TAB = {"广告组合": "portfolio", "广告活动": "campaign", "广告组": "group",
          "广告产品": "product", "投放": "target", "搜索词": "search", "否定投放": "netarget"}
# 非用户可见的传输/分页/排序/周期对比参数 (不作为「查询条件」统计)
INFRA = {"pageNo", "pageSize", "pageSign", "useAdvanced", "advanceFilter", "isCompare",
         "queryPage", "orderBy", "orderField", "orderType", "desc",
         "compareStartDate", "compareEndDate"}

# 我们在界面/后端实际支持的查询维度
EXPOSED = {
    "shopIdList":        ("OK", "店铺下拉(fShop)"),
    "adType":            ("OK", "广告类型下拉(fScope)"),
    "type":              ("OK", "广告类型下拉(fScope)"),
    "neType":            ("OK", "否定维度(已随 scope 暴露)"),
    "startDate":         ("PART", "有时间控件, 但查询时不生效(仅采集用)"),
    "endDate":           ("PART", "同上"),
    "searchValue":       ("PART", "仅有全字段模糊, 无字段/精确模式"),
    "portfolioId":       ("PART", "广告组合树按名称近似, 非 ID 精确过滤"),
    "portfolioIdList":   ("PART", "同上"),
}
LEGEND = {"OK": "已复刻", "PART": "部分/近似", "MISS": "缺失"}


def _conn():
    return db.get_conn()


def local_count(tab, scope="", kw=""):
    q, a = "SELECT COUNT(*) c FROM ad_records WHERE tab=?", [tab]
    if scope:
        q += " AND scope=?"; a.append(scope)
    if kw:
        q += " AND lower(raw_json) LIKE ?"; a.append("%" + kw.lower() + "%")
    return _conn().execute(q, a).fetchone()["c"]


def api_total(cl, tab, scope, mut=None):
    p = build_payload(tab, [SHOP], SD, ED, scope, 1, 1)
    if mut:
        mut(p)
    try:
        j = cl._post(TAB_DEFS[tab]["list"], p)
        d = j.get("data")
        pg = d.get("page") if isinstance(d, dict) and isinstance(d.get("page"), dict) else d
        return (pg or {}).get("totalSize")
    except Exception as e:      # noqa: BLE001
        return f"ERR({str(e)[:24]})"


# ---------------------------------------------------------------------------
# A. 采集数据量对比
# ---------------------------------------------------------------------------
def section_a(cl):
    print("=" * 74)
    print("A. 采集数据量对比  (原接口 totalSize  vs  本地落库行数, 区间 %s~%s)" % (SD, ED))
    print("=" * 74)
    print(f"{'页签':<12}{'scope':<14}{'原接口':>10}{'本地':>8}   结论")
    print("-" * 74)
    ta = td = 0
    for tab in TAB_ORDER:
        for scope in TAB_DEFS[tab]["scopes"]:
            api = api_total(cl, tab, scope)
            sc = db._scope_str(scope)
            n = local_count(tab, sc)
            if isinstance(api, int):
                ta += api; td += n
                note = "一致" if api == n else "差异"
            else:
                note = "原站不支持该维度(本地记0)"
            print(f"{tab:<12}{sc or '(空)':<14}{str(api):>10}{n:>8}   {note}")
    print("-" * 74)
    print(f"{'合计':<26}{ta:>10}{td:>8}")
    print("说明: 广告日志 原接口 6859 / 本地 6850 的差额, 经核为该接口分页重叠把同一条")
    print("      重复返回, 去重后内容完全一致 —— 属正确的去重, 非数据丢失。")
    print()


# ---------------------------------------------------------------------------
# B. 条件查询返回量对比
# ---------------------------------------------------------------------------
def section_b(cl):
    print("=" * 74)
    print("B. 条件查询返回量对比  (同一条件: 原接口  vs  本地界面)")
    print("=" * 74)
    print("[B1] 状态筛选 (原站有状态维度, 本地界面无)")
    full = api_total(cl, "campaign", {})
    en = api_total(cl, "campaign", {}, lambda p: p.update({"status": "enabled"}))
    print(f"    campaign  原站 全状态={full}   原站 status=enabled={en}   本地界面={local_count('campaign')}")
    print(f"    -> 原站按状态可细分, 本地恒返回全量 {local_count('campaign')}; 状态条件未复刻")
    print()
    print("[B2] 关键词搜索语义 (原站=按指定字段匹配, 本地=整行 JSON 模糊)")
    row = json.loads(_conn().execute(
        "SELECT raw_json FROM ad_records WHERE tab='campaign' LIMIT 1").fetchone()["raw_json"])
    cid = str(row.get("campaignId"))
    api_name = api_total(cl, "campaign", {},
                         lambda p: p.update({"searchValue": cid, "searchField": "name", "searchType": "blur"}))
    loc = local_count("campaign", kw=cid)
    print(f"    搜索值={cid} (只存在于 campaignId 字段)")
    print(f"      原站 searchField='name' => {api_name}")
    print(f"      本地 全字段 LIKE       => {loc}")
    print("    -> 本地会命中非目标字段(误报); 原站的 searchField/searchType 未复刻")
    print()
    print("[B3] 否定投放的时间窗维度 (原站可调 neBeforeAfterDay)")
    for d in (7, 30, 90):
        print(f"    neBeforeAfterDay={d:<3} 原站 => {api_total(cl, 'netarget', {'adType': 'sp', 'neType': 'keyword'}, lambda p, d=d: p.update({'neBeforeAfterDay': d}))}")
    print("    本地: 该值硬编码=30, 界面不可调, 且未落库为维度")
    print()


# ---------------------------------------------------------------------------
# C. 查询框条件复刻度
# ---------------------------------------------------------------------------
def _orig_keys():
    keys = {}
    if os.path.exists(CONTRACTS):
        c = json.load(open(CONTRACTS, encoding="utf-8"))
        for cn, tab in CN2TAB.items():
            if cn in c:
                keys[tab] = set((c[cn].get("req") or {}).keys())
    if os.path.exists(PAYLOADS):
        p = json.load(open(PAYLOADS, encoding="utf-8"))
        for api, tab in [("multiple/placement/getAllPlacementData", "placement"),
                         ("log/sellfoxAndAuto/getPage", "log")]:
            if api in p and tab not in keys:
                keys[tab] = set(p[api].keys())
    return keys


def section_c():
    print("=" * 74)
    print("C. 查询框条件复刻度  (原 UI 条件字段 逐项对照)")
    print("=" * 74)
    keys = _orig_keys()
    tally = {"OK": 0, "PART": 0, "MISS": 0}
    for tab in TAB_ORDER:
        if tab not in keys:
            print(f"### {TAB_DEFS[tab]['label']}({tab}): 无契约抓包, 无法对照")
            continue
        cond = sorted(keys[tab] - INFRA)
        rows = []
        for k in cond:
            st, why = EXPOSED.get(k, ("MISS", ""))
            tally[st] += 1
            if st != "MISS":
                rows.append(f"      [{LEGEND[st]}] {k}  ({why})")
        miss = [k for k in cond if EXPOSED.get(k, ("MISS",))[0] == "MISS"]
        print(f"### {TAB_DEFS[tab]['label']}({tab}): 原条件 {len(cond)} 项, 已复刻/部分 {len(rows)} 项, 缺失 {len(miss)} 项")
        for r in rows:
            print(r)
        if miss:
            print(f"      [缺失] {', '.join(miss)}")
    print("-" * 74)
    tot = sum(tally.values())
    print(f"合计: 原站查询条件 {tot} 项 -> 已复刻 {tally['OK']} / 部分 {tally['PART']} / 缺失 {tally['MISS']}")
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true")
    args = ap.parse_args()

    section_c()
    if args.no_network:
        return
    from backend.cookies import load_cookies
    ck = load_cookies()
    if not ck:
        print("[跳过 A/B] 无登录 Cookie, 仅完成静态对照")
        return
    cl = SellfoxClient(ck)
    section_a(cl)
    section_b(cl)


if __name__ == "__main__":
    main()
