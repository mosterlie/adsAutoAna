#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""修复验证脚本 (离线自测 + 与赛狐原系统对比验证)

覆盖 4 项修复:
  T1 数值排序失效      -> 本地按数值排序, 应与赛狐接口自身的降序结果一致
  T2 采集状态卡死      -> Cookie 缺失时不再永久占用 running
  T3 SQL 注入面        -> order_field 非法即拒绝
  T4 netarget 维度丢失 -> keyword/product 否定投放 scope 可区分

用法:
    python3 verify_fixes.py               # 含联网对比验证
    python3 verify_fixes.py --no-network  # 仅离线自测
"""
import argparse
import json
import os
import sys
import tempfile
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config                                    # noqa: E402
from backend import crawler                      # noqa: E402
from backend import database as db               # noqa: E402
from backend.sellfox_client import scope_str, SellfoxClient, TAB_DEFS  # noqa: E402

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}" + (f"  -- {detail}" if detail else ""))


# ---------------------------------------------------------------------------
# T1 数值排序失效
# ---------------------------------------------------------------------------
def test_numeric_order(offline_ok):
    """本地 adCost 降序应严格按数值; 并与赛狐接口的排序结果对比"""
    rows = db.get_records("product", page=1, page_size=20,
                          order_field="adCost", order_dir="desc")["rows"]
    vals = [float(r.get("adCost") or 0) for r in rows]
    offline_ok = offline_ok and all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)) \
        and vals and vals[0] > 100
    record("T1a 本地数值排序严格单调递减且首位为真实最大值", bool(offline_ok),
           f"top1={vals[0] if vals else None} top5={vals[:5]}")

    # 文本序对照: 旧实现会给出 >100 被 "9x" 压到后面的错误结果
    import sqlite3
    with db.get_conn() as c:
        text_top = c.execute(
            "SELECT json_extract(raw_json,'$.adCost') v FROM ad_records "
            "WHERE tab='product' ORDER BY json_extract(raw_json,'$.adCost') DESC LIMIT 1"
        ).fetchone()["v"]
    record("T1b 对照: 旧文本序首位≠真实最大(证明 bug 真实存在)",
           float(text_top) < vals[0], f"文本序首位={text_top} vs 数值序首位={vals[0]}")

    # 文本字段排序需正常(NOCASE 升序), 且字段真实存在
    trows = db.get_records("campaign", page=1, page_size=20,
                           order_field="name", order_dir="asc")["rows"]
    names = [r.get("name") for r in trows]
    ok_txt = all(n for n in names) and names == sorted(names, key=lambda s: (s or "").lower())
    record("T1d 文本字段(name)升序正常", ok_txt, f"top3={names[:3]}")


def test_numeric_order_vs_original():
    """对比验证: 赛狐 interface 自身按 adCost desc 排序结果(区间一致)"""
    cookies = crawler.load_cookies()
    if not cookies:
        record("T1c 对比赛狐原接口排序(需登录态)", False, "无 Cookie, 跳过")
        return
    cl = SellfoxClient(cookies)
    from backend.sellfox_client import build_payload
    p = build_payload("campaign", [711811], "2026-08-01", "2026-10-04", {}, 1, 200)
    p["orderField"], p["orderType"] = "adCost", "desc"
    j = cl._post(TAB_DEFS["campaign"]["list"], p)
    api_rows = (j.get("data") or {}).get("page", {}).get("rows") or []
    api_top = [round(float(r.get("adCost") or 0), 2) for r in api_rows[:20]]

    loc_rows = db.get_records("campaign", page=1, page_size=20,
                              order_field="adCost", order_dir="desc")["rows"]
    loc_top = [round(float(r.get("adCost") or 0), 2) for r in loc_rows[:20]]

    same = api_top == loc_top
    record("T1c 对比赛狐原接口: 本地Top20花费序列 == 原站Top20", same,
           f"api={api_top[:5]} local={loc_top[:5]}")


# ---------------------------------------------------------------------------
# T2 采集状态卡死
# ---------------------------------------------------------------------------
def test_state_not_stuck():
    tmpdb = tempfile.mktemp(suffix=".db")
    real_db = config.DB_PATH
    real_loader = crawler.load_cookies
    try:
        config.DB_PATH = tmpdb
        crawler.load_cookies = lambda: None          # 模拟 Cookie 缺失
        with crawler._STATE_LOCK:
            crawler.STATE.update({"running": False, "run_id": None,
                                  "progress": [], "error": None})
        raised = False
        try:
            crawler.run_crawl(tabs=["campaign"])
        except Exception:
            raised = True
        st = crawler.get_state()
        run = db.last_run()
        ok = raised and st["running"] is False and bool(st["error"]) \
            and run is not None and run["status"] == "failed"
        record("T2a Cookie 缺失时: running 复位 + 批次标记 failed", ok,
               f"raised={raised} running={st['running']} run_status={run['status'] if run else None}")

        # 关键回归: 后续采集不应再被 "已有任务运行中" 拒绝
        blocked = crawler.STATE.get("running")
        record("T2b 失败后不再永久占位(新任务可启动)", not blocked,
               "running=False => start_crawl_async 不会抛 '已有采集任务在运行中'")
    finally:
        config.DB_PATH = real_db
        crawler.load_cookies = real_loader
        try:
            os.remove(tmpdb)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# T3 SQL 注入面
# ---------------------------------------------------------------------------
def test_injection_guard():
    from backend.database import is_safe_field, build_order_clause
    bad = ["a') DROP TABLE ad_records--", "adCost;DROP", "1=1", "a b", ""]
    rejected = all(not is_safe_field(b) for b in bad if b != "")
    record("T3a 非法排序字段被 is_safe_field 拒绝", rejected, f"bad={bad}")

    try:
        build_order_clause("a')--", "desc")
        record("T3b build_order_clause 对注入字段抛 ValueError", False, "未抛异常")
    except ValueError:
        record("T3b build_order_clause 对注入字段抛 ValueError", True)

    # 真实攻击串不应对 DB 造成影响
    before = db.get_status()["tab_counts"].get("product", 0)
    try:
        db.get_records("product", order_field="x') UNION SELECT 1--")
        api_blocked = False
    except ValueError:
        api_blocked = True
    after = db.get_status()["tab_counts"].get("product", 0)
    record("T3c 注入串被拦截且 ad_records 未被破坏", api_blocked and before == after,
           f"blocked={api_blocked} product_before={before} after={after}")

    # api 层返回 400
    try:
        from backend.api import records as api_records
        from fastapi import HTTPException
        code = None
        try:
            api_records(tab="product", order_field="a')--")
        except HTTPException as e:
            code = e.status_code
        record("T3d API 层返回 400(非法排序字段)", code == 400, f"status={code}")
    except Exception as e:      # noqa: BLE001
        record("T3d API 层返回 400(非法排序字段)", False, f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# T4 netarget scope 维度
# ---------------------------------------------------------------------------
def test_scope_dimension():
    kw = scope_str({"adType": "sp", "neType": "keyword"})
    pd = scope_str({"adType": "sp", "neType": "product"})
    record("T4a 否定关键词/否定商品 scope 可区分", kw == "sp:keyword" and pd == "sp:product",
           f"keyword={kw} product={pd}")
    # 回归: 其它页签 scope 结果不变
    unchanged = (scope_str({"adType": "sp"}) == "sp"
                 and scope_str({"type": "sp"}) == "sp"
                 and scope_str({"types": ["sp"]}) == "sp"
                 and scope_str({}) == "")
    record("T4b 其它页签 scope 归一结果保持不变(向后兼容)", unchanged)

    # 落库端到端: 两个维度写入应产生两条不同 scope 的记录
    tmpdb = tempfile.mktemp(suffix=".db")
    real_db = config.DB_PATH
    try:
        config.DB_PATH = tmpdb
        db.init_db()
        row = {"id": "1", "shopId": 1, "name": "n"}
        db.upsert_records("netarget", {"adType": "sp", "neType": "keyword"}, [dict(row)], "a", "b")
        row2 = {"id": "1", "shopId": 1, "name": "n"}
        db.upsert_records("netarget", {"adType": "sp", "neType": "product"}, [row2], "a", "b")
        with db.get_conn() as c:
            scopes = sorted(r["scope"] for r in
                            c.execute("SELECT DISTINCT scope FROM ad_records WHERE tab='netarget'"))
        record("T4c 落库后 keyword/product 并存为两个 scope", scopes == ["sp:keyword", "sp:product"],
               f"scopes={scopes}")
    finally:
        config.DB_PATH = real_db
        try:
            os.remove(tmpdb)
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true")
    args = ap.parse_args()

    test_numeric_order(True)
    if not args.no_network:
        try:
            test_numeric_order_vs_original()
        except Exception as e:      # noqa: BLE001
            record("T1c 对比赛狐原接口排序(需登录态)", False, f"{type(e).__name__}: {e}")
    test_state_not_stuck()
    test_injection_guard()
    test_scope_dimension()

    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{total} 通过")
    for name, ok, _ in RESULTS:
        if not ok:
            print(f"  ✗ {name}")
    print("=" * 60)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
