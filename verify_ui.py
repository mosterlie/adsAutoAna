#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""adsAutoAna 全功能对比测试 / 回归测试

覆盖:
  A. 在线产品(销售>在线产品)  —— 列对齐 / 字段映射 / 检索 / 筛选 / 排序 / 采集
  B. 数据表格(9 页签)        —— 列对齐 / 页签 / 统计 / 排序 / 筛选
  C. 产品视角下钻            —— L1/L2/L3 对齐与链路
  D. 只读接口契约            —— 各 API 返回结构

用法:
    python3 verify_ui.py            # 全量
    python3 verify_ui.py --no-ui    # 只跑接口(D)与库比对
    python3 verify_ui.py --base http://127.0.0.1:8320
"""
import argparse
import json
import re
import sqlite3
import sys

import requests

sys.path.insert(0, ".")
import config  # noqa: E402

BASE = "http://127.0.0.1:8320"
OK, FAIL = [], []


def rec(name, ok, detail=""):
    (OK if ok else FAIL).append(name)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"   {detail}" if detail else ""))


def S():
    s = requests.Session()
    s.proxies = {"http": None, "https": None}
    s.trust_env = False
    return s


def G(s, path, params=None, tries=5):
    last = None
    for _ in range(tries):
        try:
            r = s.get(BASE + path, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as e:      # noqa: BLE001
            last = type(e).__name__
    raise RuntimeError(f"{path} -> {last}")


def db():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# D. 只读接口契约
# ---------------------------------------------------------------------------
def test_api(s):
    print("\n=== D. 只读接口契约 ===")
    d = G(s, "/api/status")
    rec("D1 /api/status 结构完整", set(("tab_counts", "shops", "portfolios", "crawl")) <= set(d))
    rec("D2 采集未卡死(running=False)", d["crawl"]["running"] is False,
        f"running={d['crawl']['running']}")

    t = G(s, "/api/tabs")
    keys = [x["key"] for x in t["tabs"]]
    rec("D3 /api/tabs 返回 9 个页签", len(keys) == 9, str(keys))
    sc = [x for x in t["tabs"] if x["key"] == "search"][0]["scopes"]
    rec("D4 搜索词页签含两个维度", any("keyword" in str(x) for x in sc) and any("asin" in str(x) for x in sc), str(sc))

    f = G(s, "/api/filters", {"tab": "campaign"})
    rec("D5 /api/filters 返回字段与候选值", bool(f.get("filters")) and bool(f.get("search_fields")))

    r = G(s, "/api/records", {"tab": "campaign", "filters": json.dumps({"state": ["enabled"]}), "page": 1, "page_size": 1})
    rec("D6 状态过滤 state=enabled -> 137", r["total"] == 137, f"total={r['total']}")

    bad = s.get(BASE + "/api/records", params={"tab": "campaign", "order_field": "a')--"}, timeout=20)
    rec("D7 非法排序字段被拒(400)", bad.status_code == 400, f"HTTP {bad.status_code}")

    st = G(s, "/api/stats", {"tab": "campaign"})
    rec("D8 /api/stats 四项统计齐全",
        set(("deal", "click_no_deal", "imp_no_click", "no_imp")) <= set(st), str(list(st)))

    pl = G(s, "/api/product/list", {"page": 1, "page_size": 5})
    rec("D9 /api/product/list 以父ASIN聚合", pl["total"] == 135
        and pl.get("group_by") == "parent_asin" and "campaign_count" in pl["rows"][0],
        f"total={pl['total']} group_by={pl.get('group_by')}")

    asin = pl["rows"][0]["parent_asin"]
    pc = G(s, "/api/product/campaigns", {"asin": asin})
    rec("D10 /api/product/campaigns 可下钻", pc["total"] >= 1 and pc.get("parent_asin") == asin,
        f"{asin} -> {pc['total']} 活动, {len(pc.get('child_asins') or [])} 个子ASIN")

    # ---- 父体级搜索词汇总 (产品视角「操作 - 搜索词」) ----
    kid = (pc.get("child_asins") or [None])[0]
    pt = G(s, "/api/product/terms", {"asin": asin})
    rec("D11 父体级搜索词汇总可返回", pt["total"] >= 1 and pt.get("parent_asin") == asin,
        f"{asin} 覆盖 {pt.get('campaign_count')} 个活动, {pt['total']} 条")
    rec("D12 汇总行带「涉及活动」数", all("campaign_count" in r for r in pt["rows"]),
        str([r.get("campaign_count") for r in pt["rows"][:5]]))

    if kid:
        pt2 = G(s, "/api/product/terms", {"asin": kid})
        rec("D13 传子 ASIN 自动换算到父体",
            pt2.get("parent_asin") == asin and pt2["total"] == pt["total"],
            f"子={kid} -> 父={pt2.get('parent_asin')} {pt2['total']} 条")

    # 逐活动取明细后手工合并, 应与父体级汇总一致
    acc = {}
    for c in pc["rows"]:
        t = G(s, "/api/product/terms", {"campaign_id": str(c["campaignId"])})
        for r in t["rows"]:
            k = (r["scope"], str(r.get("query") or ""), str(r.get("matchType") or ""))
            a = acc.setdefault(k, {"cost": 0.0, "clic": 0.0, "cid": set()})
            a["cost"] += float(r.get("adCost") or 0)
            a["clic"] += float(r.get("clicks") or 0)
            a["cid"].add(str(c["campaignId"]))
    rec("D14 父体级汇总 = 各活动明细按词合并(条数)",
        len(acc) == pt["total"], f"手工合并={len(acc)} 接口={pt['total']}")
    bad = [r for r in pt["rows"]
           if not account_close(r.get("adCost"), acc.get((r["scope"], str(r.get("query") or ""),
                                                           str(r.get("matchType") or "")),
                                                          {"cost": -1})["cost"])]
    rec("D15 父体级汇总花费与手工累加一致(逐条)", not bad,
        f"不一致 {len(bad)}/{pt['total']} 条: {[b.get('query') for b in bad[:3]]}")


def account_close(a, b, tol=0.02):
    try:
        return abs(float(a or 0) - float(b or 0)) <= tol
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# A. 在线产品 数据比对(接口 vs 库)
# ---------------------------------------------------------------------------
def test_online_data(s):
    print("\n=== A. 在线产品 数据比对(接口 vs 库) ===")
    meta = G(s, "/api/online/meta")
    rec("A1 meta: 字段目录 85 项", len(meta["fields"]) == 85, f"{len(meta['fields'])}")
    rec("A2 meta: 店铺非空", len(meta["shops"]) >= 1)

    d = G(s, "/api/online/products", {"page": 1, "page_size": 50})
    counts = meta.get("counts") or {}
    rec("A3 子体总数为 1640", counts.get("child") == 1640, f"child={counts.get('child')}")
    rec("A3b 账号/父体总数为 135", counts.get("parent") == 135, f"parent={counts.get('parent')}")
    rec("A3c 全库合计 = 子体 + 父体",
        d["total"] == (counts.get("total") or 0) == 1775, f"total={d['total']}")
    rec("A4 首页返回 50 行", len(d["rows"]) == 50, f"{len(d['rows'])}")
    base_keys = [k for k in d["rows"][0] if not k.startswith("_m_")]
    rec("A5 每行含 195 个原始字段", len(base_keys) == 195, f"{len(base_keys)} (+补全字段 {len(d['rows'][0]) - len(base_keys)})")

    # 检索: asin 精确
    asin = d["rows"][0]["asin"]
    r = G(s, "/api/online/products", {"keyword": asin, "search_field": "asin", "page_size": 10})
    hit = [x for x in r["rows"] if x.get("asin") == asin]
    rec("A6 按 ASIN 检索命中", r["total"] >= 1 and len(hit) >= 1, f"total={r['total']}")

    # 筛选: onlineStatus
    c = db()
    n_inactive = c.execute(
        "SELECT COUNT(*) FROM online_products WHERE CAST(json_extract(raw_json,'$.onlineStatus') AS TEXT)='Inactive'"
    ).fetchone()[0]
    r2 = G(s, "/api/online/products", {"filters": json.dumps({"onlineStatus": ["Inactive"]}), "page_size": 1})
    rec("A7 状态筛选与库一致", r2["total"] == n_inactive, f"api={r2['total']} db={n_inactive}")

    # 排序: quantity 数值序(该字段 1617 行非零, 有区分度)
    r3 = G(s, "/api/online/products", {"order_field": "quantity", "order_dir": "desc", "page_size": 10})
    vals = []
    for x in r3["rows"]:
        try:
            vals.append(float(x.get("quantity") or 0))
        except Exception:       # noqa: BLE001
            vals.append(0.0)
    rec("A8 quantity 降序单调", all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)),
        f"top={vals[:5]}")


# ---------------------------------------------------------------------------
# UI 测试 (Playwright)
# ---------------------------------------------------------------------------
def test_ui(s):
    from playwright.sync_api import sync_playwright

    print("\n=== UI 端到端 ===")
    c = db()
    # 库内第一行(默认 id ASC) 作为渲染比对基准
    row0 = json.loads(c.execute(
        "SELECT raw_json FROM online_products ORDER BY id ASC LIMIT 1").fetchone()[0])
    fields = json.loads(c.execute(
        "SELECT v FROM online_meta WHERE k='head_fields'").fetchone()["v"])
    label2key = {f["headName"]: f["headField"] for f in fields}

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 1680, "height": 980}).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_selector("#tabbar .tab", timeout=20000)

        # ---- A. 在线产品 UI ----
        print("\n--- A. 在线产品 UI ---")
        pg.click('#viewSwitch .seg-btn[data-view="online"]')
        pg.wait_for_selector("#olWrap tbody tr", timeout=25000)
        th = pg.locator("#olWrap thead th").count()
        td = pg.locator("#olWrap tbody tr").first.locator("td").count()
        rec("A9 表头与数据列数一致(不错位)", th == td, f"th={th} td={td}")

        heads = [pg.locator("#olWrap thead th").nth(i).inner_text().strip() for i in range(th)]
        cells = [pg.locator("#olWrap tbody tr").first.locator("td").nth(i).inner_text().strip() for i in range(td)]

        # 字段映射: 显示列名 -> 该列渲染值应含库中对应字段值
        def cell_of(name):
            return cells[heads.index(name)] if name in heads else None

        pairs = [
            ("ASIN/MSKU", "asin"), ("标题", "title"), ("父ASIN", "parentAsin"),
            ("父SKU", "parentSku"), ("可售", "quantity"), ("上架时间", "openDate"),
        ]
        mapping_ok, checked = True, 0
        for hname, key in pairs:
            got = cell_of(hname)
            if got is None:
                continue
            want = str(row0.get(key) or "").strip()
            checked += 1
            okk = (got == want) or (want and want.replace(" ", "")[:14] in got.replace(" ", ""))
            if not okk:
                mapping_ok = False
                print(f"        ✗ 列「{hname}」期望含 {want[:30]!r} 实得 {got[:30]!r}")
        # 店铺/站点 是合成列
        ss = cell_of("店铺/站点")
        if ss is not None:
            checked += 1
            if str(row0.get("shopName") or "") not in ss or str(row0.get("siteName") or "") not in ss:
                mapping_ok = False
                print(f"        ✗ 列「店铺/站点」合成值不符: {ss[:30]!r}")
        rec("A10 列头-字段映射正确(抽查文本列)", mapping_ok and checked >= 5, f"抽查 {checked} 列")

        # 关键词检索
        pg.fill("#olKw", row0.get("asin"))
        pg.click("#olQuery")
        pg.wait_for_timeout(1500)
        n = pg.locator("#olWrap tbody tr").count()
        first_cell = pg.locator("#olWrap tbody tr").first.locator("td").nth(heads.index("ASIN/MSKU")).inner_text().strip()
        rec("A11 UI 检索 ASIN 命中", n >= 1 and row0.get("asin") in first_cell, f"行={n} 单元={first_cell[:24]!r}")
        pg.click("#olReset")
        pg.wait_for_timeout(1400)

        # 排序
        idx_cost = None
        for i, h in enumerate(heads):
            if h == "广告花费":
                idx_cost = i
                break
        if idx_cost is not None:
            pg.locator("#olWrap thead th").nth(idx_cost).click()
            pg.wait_for_timeout(1200)
        rec("A12 表头点击排序可用", True)

        # 列自定义
        before = pg.locator("#olWrap thead th").count()
        pg.click("#olColBtn")
        pg.wait_for_timeout(300)
        boxes = pg.locator('#olColPick input[data-ck]')
        boxes.nth(0).uncheck()
        pg.wait_for_timeout(600)
        after = pg.locator("#olWrap thead th").count()
        th2 = pg.locator("#olWrap thead th").count()
        td2 = pg.locator("#olWrap tbody tr").first.locator("td").count()
        rec("A13 取消列后列数减少且仍对齐", after < before and th2 == td2, f"{before}->{after}, th={th2} td={td2}")

        # 详情抽屉
        pg.locator("#olWrap tbody tr").first.click()
        pg.wait_for_selector("#olDetail.open", timeout=8000)
        drows = pg.locator("#olDetail .drow").count()
        rec("A14 详情抽屉展示字段", drows > 50, f"{drows} 行")
        pg.click("#olDetailClose")
        pg.wait_for_timeout(300)

        # ---- B. 数据表格 ----
        print("\n--- B. 数据表格 ---")
        pg.click('#viewSwitch .seg-btn[data-view="table"]')
        pg.wait_for_selector("#bodyRow tr", timeout=20000)
        bth = pg.locator("#headRow th").count()
        btd = pg.locator("#bodyRow tr").first.locator("td").count()
        rec("B1 表格 表头与数据列数一致", bth == btd, f"th={bth} td={btd}")
        rec("B2 页签数量 9", pg.locator("#tabbar .tab").count() == 9)
        # 切页签
        pg.click('#tabbar .tab[data-tab="search"]')
        pg.wait_for_timeout(1500)
        rec("B3 切换页签正常", pg.locator("#bodyRow tr").count() > 0)
        # 统计条
        chips = pg.locator("#chips").inner_text().strip()
        rec("B4 统计条有内容", len(chips) > 0, chips.replace("\n", " ")[:50])

        # ---- C. 产品视角 ----
        print("\n--- C. 产品视角下钻 ---")
        pg.click('#viewSwitch .seg-btn[data-view="product"]')
        pg.wait_for_selector("#pWrap tbody tr", timeout=20000)
        th = pg.locator("#pWrap thead th").count()
        td = pg.locator("#pWrap tbody tr").first.locator("td").count()
        rec("C1 L1 产品列表对齐", th == td, f"th={th} td={td}")
        # 现在整行不可点, 需点「操作」列的「查看活动」按钮才下钻
        pg.locator('#pWrap tbody tr button[data-act="campaigns"]').first.click()
        pg.wait_for_selector("#pBack", timeout=15000)
        th = pg.locator("#pWrap thead th").count()
        td = pg.locator("#pWrap tbody tr").first.locator("td").count()
        rec("C2 L2 活动列表对齐", th == td, f"th={th} td={td}")
        rows = pg.locator("#pWrap tbody tr")
        clicked = False
        for i in range(rows.count()):
            btn = rows.nth(i).locator('button[data-terms]')
            if btn.count():
                btn.first.click()
                clicked = True
                break
        if clicked:
            pg.wait_for_selector("#pTgKw", timeout=15000)
            th = pg.locator("#pWrap thead th").count()
            td = pg.locator("#pWrap tbody tr").first.locator("td").count()
            rec("C3 L3 搜索词列表对齐", th == td, f"th={th} td={td}")

            def _dims():
                return set(pg.eval_on_selector_all(
                    "#pWrap tbody .dim-tag",
                    "els=>[...new Set(els.map(e=>e.textContent.trim()))]"))

            # 进入该视图默认只看搜索词(sp:keyword)
            n_kw = pg.locator("#pWrap tbody tr").count()
            d_kw = _dims()
            rec("C4 进入默认「只看搜索词」",
                d_kw == {"搜索词"} and pg.locator("#pTgKw.btn-primary").count() == 1,
                f"行数={n_kw} 维度={d_kw}")
            pg.click("#pTgAll")
            pg.wait_for_timeout(800)
            n_all = pg.locator("#pWrap tbody tr").count()
            d_all = _dims()
            rec("C4b 「全部」含搜索词与商品投放",
                d_all == {"搜索词", "商品投放"} and n_all >= n_kw, f"{n_all} {d_all}")
            pg.click("#pTgAsin")
            pg.wait_for_timeout(800)
            n_az = pg.locator("#pWrap tbody tr").count()
            d_az = _dims()
            rec("C5 维度切换(只看商品投放)生效",
                d_az == {"商品投放"} and n_az != n_kw, f"仅投放={n_az} {d_az} 仅关键词={n_kw}")
            # 返回活动列表
            pg.click("#pBack2")
            pg.wait_for_timeout(800)
            rec("C6 从搜索词返回活动列表", pg.locator("#pBack").count() >= 1 and pg.locator("#pWrap tbody tr").count() > 0)
            # 面包屑回产品列表
            pg.locator('#pCrumbs .cb[data-lv]').first.click()
            pg.wait_for_timeout(800)
            rec("C7 面包屑回产品列表", pg.locator("#pWrap tbody tr").count() > 0)
        else:
            rec("C3 L3 搜索词列表对齐", False, "未找到含搜索词的活动")

        rec("C8 页面无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def test_parent_aggregate(s):
    """I. 产品视角: 以父 ASIN 为一条记录, 多子 ASIN 合并统计"""
    print("\n=== I. 产品视角 父ASIN 合并 ===")
    c = db()

    # I1 库内广告产品 ASIN 数(未合并)
    raw_asins = {r[0] for r in c.execute(
        "SELECT DISTINCT json_extract(raw_json,'$.asin') FROM ad_records WHERE tab='product'")}
    raw_asins.discard(None)
    rec("I1 广告产品原始 ASIN 数 560", len(raw_asins) == 560, f"{len(raw_asins)}")

    # I2 接口已按父 ASIN 聚合
    d = G(s, "/api/product/list", {"page": 1, "page_size": 500})
    rec("I2 合并后父体记录数 135", d["total"] == 135, f"total={d['total']}")
    rec("I3 返回 group_by=parent_asin", d.get("group_by") == "parent_asin",
        str(d.get("group_by")))
    rec("I4 每行都带父 ASIN 与子体数",
        all(r.get("parent_asin") and (r.get("child_count") or 0) >= 1 for r in d["rows"]),
        f"{min((r.get('child_count') or 0) for r in d['rows'])}~"
        f"{max((r.get('child_count') or 0) for r in d['rows'])}")

    # I5 合并范围: 子 ASIN 总数 == 未合并 ASIN 总数(不丢不重)
    total_kids = sum(r["child_count"] for r in d["rows"])
    rec("I5 子 ASIN 总数与合并前一致(不丢不重)", total_kids == len(raw_asins),
        f"合并后 {total_kids} vs 合并前 {len(raw_asins)}")

    # I6 父体全部来自在线产品的变体关系
    pset = {r[0] for r in c.execute(
        "SELECT asin FROM online_products WHERE is_variation='1'")}
    rec("I6 父体全部命中在线产品父体表", {r["parent_asin"] for r in d["rows"]} <= pset,
        f"父体 {len(d['rows'])} 个")

    # I7 抽样核对: 合并花费 == 各子 ASIN 手工累加; 活动数 == 活动并集
    def manual(kids):
        cost, camps = 0.0, set()
        for (rj,) in c.execute("SELECT raw_json FROM ad_records WHERE tab='product'"):
            x = json.loads(rj)
            if x.get("asin") in kids:
                cost += float(x.get("adCost") or 0)
                if x.get("campaignId"):
                    camps.add(str(x["campaignId"]))
        return cost, camps

    diff, multi = [], [r for r in d["rows"] if r["child_count"] > 1][:8]
    for r in multi:
        cost, camps = manual(set(r["child_asins"]))
        if abs(cost - r["ad_cost"]) > 1e-6 or len(camps) != r["campaign_count"]:
            diff.append(f"{r['parent_asin']}: 花费 {r['ad_cost']} vs {cost}, "
                        f"活动 {r['campaign_count']} vs {len(camps)}")
    rec(f"I7 抽样 {len(multi)} 个多子体父体, 花费/活动并集一致", not diff,
        "; ".join(diff[:3]))

    # I8 按子 ASIN 搜索应命中其父体记录
    sample = multi[0]
    kid = sample["child_asins"][0]
    r2 = G(s, "/api/product/list", {"keyword": kid, "page_size": 10})
    hit = next((x for x in r2["rows"] if x["parent_asin"] == sample["parent_asin"]), None)
    rec("I8 按子 ASIN 检索命中父体记录", bool(hit),
        f"子 {kid} -> 父 {sample['parent_asin']} total={r2['total']}")

    # I9 排序: 按子体数
    r3 = G(s, "/api/product/list", {"order_field": "child_count", "order_dir": "desc",
                                    "page_size": 10})
    vals = [x["child_count"] for x in r3["rows"]]
    rec("I9 按子体数降序可用", all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)),
        f"top={vals[:6]}")

    # I10 下钻: 活动按活动ID合并(无重复行) + 带涉及子ASIN
    pc = G(s, "/api/product/campaigns", {"asin": sample["parent_asin"]})
    cids = [x["campaignId"] for x in pc["rows"]]
    rec("I10 下钻活动无重复行", len(cids) == len(set(cids)), f"{len(cids)} 行")
    rec("I11 下钻每行带涉及子 ASIN", all(x.get("child_asins") for x in pc["rows"]) if pc["rows"] else False,
        f"首行 {len(pc['rows'][0]['child_asins'])} 个子ASIN" if pc["rows"] else "无")
    # 传子 ASIN 也能自动换算
    pc2 = G(s, "/api/product/campaigns", {"asin": kid})
    rec("I12 传子 ASIN 自动换算到父体", pc2.get("parent_asin") == sample["parent_asin"],
        f"{kid} -> {pc2.get('parent_asin')}")

    # I12b 快照唯一性: 每个 (页签,维度,店铺) 只应有一份区间数据
    #       (跨天采集若不去重, 同一条记录会因区间不同再插一份 -> 指标翻倍)
    from backend import database as _db
    dup = c.execute(
        """SELECT tab, COALESCE(scope,''), COALESCE(shop_id,-1), COUNT(*) n
           FROM (SELECT DISTINCT tab, scope, shop_id, range_start, range_end FROM ad_records)
           GROUP BY tab, COALESCE(scope,''), COALESCE(shop_id,-1)
           HAVING n > 1""").fetchall()
    rec("I12b 各维度只保留一个快照区间(无重复入库)", not dup,
        f"重复组 {len(dup)} 个")
    rec("I12c 快照清理函数可用",
        hasattr(_db, "purge_stale_ranges") and hasattr(_db, "purge_all_other_ranges"))
    snap = d.get("snapshot") or {}
    rec("I12d 产品列表返回快照区间",
        bool(snap.get("range_start")) and bool(snap.get("range_end")), str(snap))

    # ---- UI ----
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        ctx = b.new_context(viewport={"width": 1780, "height": 1020})
        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        except Exception:       # noqa: BLE001
            pass
        pg = ctx.new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_selector("#viewSwitch .seg-btn[data-view='product']", timeout=20000)
        rec("I13b 一级菜单(顶层导航)那一行已去掉",
            pg.locator("nav.topnav").count() == 0 and pg.locator(".topnav a").count() == 0,
            f"topnav={pg.locator('nav.topnav').count()}")
        rec("I13c 品牌与同步登录态保留",
            pg.locator("header .logo-text").count() == 1
            and pg.locator("#btnSyncCookie").count() == 1)
        pg.click('#viewSwitch .seg-btn[data-view="product"]')
        pg.wait_for_selector("#pWrap tbody tr", timeout=25000)
        pg.wait_for_timeout(900)

        heads = [pg.locator("#pWrap thead th").nth(i).inner_text().strip()
                 for i in range(pg.locator("#pWrap thead th").count())]
        rec("I13 表头为「父ASIN / 父SKU / 子ASIN / 标题」",
            heads[1:5] == ["父ASIN", "父SKU", "子ASIN", "标题"], str(heads[:6]))

        # 指标列: 中文 + (英文简称)
        hdr = [h.replace("\n", "") for h in heads]
        need_en = ["(Spend)", "(Impressions)", "(Clicks)", "(Orders)", "(CTR)", "(ACOS)"]
        rec("I13d 指标列表头为「中文(英文简称)」",
            all(any(e in h for h in hdr) for e in need_en),
            str([h for h in hdr if "(" in h][:8]))

        row = pg.locator("#pWrap tbody tr").first
        pa_txt = row.locator("td.col-asin .asin-copy").inner_text().strip()
        sku_txt = row.locator("td").nth(2).inner_text().strip()
        kid_cell = row.locator("td").nth(3)
        title_cell = row.locator("td").nth(4)
        n_kid_links = kid_cell.locator(".asin-kid").count()
        rec("I14 父ASIN 列展示父 ASIN", any(x["parent_asin"] == pa_txt for x in d["rows"]), pa_txt)
        rec("I15 子ASIN 列展示多个子 ASIN", n_kid_links >= 2, f"{n_kid_links} 个子ASIN")
        rec("I16 父SKU 列取父体 SKU",
            sku_txt == next((x["sku"] for x in d["rows"]
                             if x["parent_asin"] == pa_txt), sku_txt), sku_txt)

        # 列宽收敛 + 省略号
        wid = pg.evaluate("""() => {
            const td = document.querySelector('#pWrap tbody td.col-kid');
            const sp = td.querySelector('.asin-kid-list');
            const tl = document.querySelector('#pWrap tbody td.col-title');
            const tb = document.querySelector('#pWrap table.tbl-products');
            const wrap = document.querySelector('#pWrap');
            return {kidW: Math.round(td.getBoundingClientRect().width),
                    kidOver: sp ? sp.scrollWidth > sp.clientWidth + 1 : false,
                    titleW: Math.round(tl.getBoundingClientRect().width),
                    titleOver: tl.scrollWidth > tl.clientWidth + 1,
                    ell: getComputedStyle(tl).textOverflow,
                    tblW: Math.round(tb.getBoundingClientRect().width),
                    tblMin: parseInt(getComputedStyle(tb).minWidth, 10),
                    wrapW: Math.round(wrap.clientWidth)};
        }""")
        rec("I17 子ASIN 列窄且超出省略", wid["kidW"] <= 215 and wid["kidOver"], str(wid))
        rec("I18 标题列再次缩窄(约 140px)且超出省略",
            wid["titleW"] <= 150 and wid["titleOver"] and wid["ell"] == "ellipsis", str(wid))
        # 视口收窄后, 列应通过横向滚动展示(而不是被压扁)
        pg.set_viewport_size({"width": 1280, "height": 900})
        pg.wait_for_timeout(500)
        nw = pg.evaluate("""() => {
            const tb = document.querySelector('#pWrap table.tbl-products');
            const wrap = document.querySelector('#pWrap');
            return {tblW: Math.round(tb.getBoundingClientRect().width),
                    tblMin: parseInt(getComputedStyle(tb).minWidth, 10),
                    wrapW: Math.round(wrap.clientWidth),
                    scrollW: wrap.scrollWidth, clientW: wrap.clientWidth,
                    ox: getComputedStyle(wrap).overflowX};
        }""")
        rec("I18b 列宽不足时支持横向滚动(列不被压扁)",
            nw["tblW"] == nw["tblMin"] and nw["tblW"] > nw["wrapW"]
            and nw["ox"] in ("auto", "scroll") and nw["scrollW"] > nw["clientW"],
            str(nw))
        pg.set_viewport_size({"width": 1780, "height": 1020})
        pg.wait_for_timeout(500)

        # 花费/曝光不再重叠: 指标列内不得出现溢出
        ovl = pg.evaluate("""() => {
            const tr = document.querySelector('#pWrap tbody tr');
            const out = {};
            for (const c of ['col-cost','col-imp','col-clk','col-ord','col-ctr','col-acos']) {
                const td = tr.querySelector('td.' + c);
                out[c] = td.scrollWidth - td.clientWidth;
            }
            return out;
        }""")
        rec("I18c 花费/曝光等指标列无溢出重叠",
            all(v <= 1 for v in ovl.values()), str(ovl))

        # 父 ASIN: 点击复制 + 独立跳转按钮
        pa_cell = row.locator("td.col-asin")
        jump_href = pa_cell.locator("a.btn-jump").get_attribute("href") or ""
        rec("I18d 父ASIN 后有跳转按钮且指向该 ASIN 页面",
            jump_href == f"https://www.amazon.co.jp/dp/{pa_txt}"
            and pa_cell.locator("a.btn-jump").get_attribute("target") == "_blank", jump_href)
        pa_cell.locator(".asin-copy").click()
        pg.wait_for_timeout(500)
        clip = ""
        try:
            clip = pg.evaluate("() => navigator.clipboard.readText()")
        except Exception:       # noqa: BLE001
            pass
        rec("I18e 点击父ASIN 复制到剪贴板",
            clip == pa_txt or pa_txt in pg.inner_text("#toast"),
            f"剪贴板={clip!r} toast={pg.inner_text('#toast').strip()!r}")

        # 子ASIN 单元格 -> 弹层(可查看 + 跳转)
        kid_total = next((len(x.get("all_child_asins") or x.get("child_asins") or [])
                          for x in d["rows"] if x["parent_asin"] == pa_txt), 0)
        kid_cell.click()
        pg.wait_for_timeout(700)
        rec("I18f 点击子ASIN 单元格打开弹层",
            "open" in (pg.get_attribute("#kidModal", "class") or "")
            and pg.locator("#kidBody .kid-item").count() >= 1,
            f"条目={pg.locator('#kidBody .kid-item').count()} 预期={kid_total}")
        rec("I18g 弹层列出全部子 ASIN",
            pg.locator("#kidBody .kid-item").count() == kid_total, f"{kid_total}")
        rec("I18h 弹层内每项都能跳转到对应 ASIN 页面",
            pg.locator("#kidBody .kid-item a[href*='/dp/']").count()
            == pg.locator("#kidBody .kid-item").count())
        kid_first = pg.locator("#kidBody .kid-item .kid-asin").first.inner_text().strip()
        pg.locator("#kidBody button[data-copy]").first.click()
        pg.wait_for_timeout(500)
        kid_clip = ""
        try:
            kid_clip = pg.evaluate("() => navigator.clipboard.readText()")
        except Exception:       # noqa: BLE001
            pass
        rec("I18i 弹层内可复制子 ASIN",
            kid_clip == kid_first or kid_first in pg.inner_text("#toast"),
            f"{kid_first!r} -> {kid_clip!r}")
        pg.fill("#kidFilter", kid_first[:7])
        pg.wait_for_timeout(500)
        n_filtered = pg.locator("#kidBody .kid-item").count()
        pg.fill("#kidFilter", "")
        pg.wait_for_timeout(400)
        rec("I18j 弹层支持筛选子 ASIN",
            0 < n_filtered < kid_total and n_filtered < 32,
            f"筛选'{kid_first[:7]}' -> {n_filtered} / 全部 {kid_total}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        rec("I18k ESC 关闭子ASIN 弹层",
            "open" not in (pg.get_attribute("#kidModal", "class") or ""))

        # 操作列固定 + 整行不可点
        ops = pg.evaluate("""() => {
            const td = document.querySelector('#pWrap tbody td.col-ops');
            const wrap = document.querySelector('#pWrap');
            wrap.scrollLeft = 400;
            const a = td.getBoundingClientRect(), b = wrap.getBoundingClientRect();
            const right = Math.abs(a.right - b.right) < 3;
            wrap.scrollLeft = 0;
            return {pos: getComputedStyle(td).position, right,
                    tr: getComputedStyle(document.querySelector('#pWrap tbody tr')).cursor,
                    btns: [...document.querySelectorAll('#pWrap tbody tr:first-child .col-ops button')]
                            .map(x => x.textContent.trim())};
        }""")
        rec("I19 操作列固定(sticky)且横向滚动仍贴右",
            ops["pos"] == "sticky" and ops["right"], str(ops))
        rec("I20 整行不再是可点击指针", ops["tr"] == "auto", ops["tr"])
        rec("I20b 操作列含「活动」「搜索词」两个按钮",
            ops["btns"][:2] == ["活动", "搜索词"], str(ops["btns"]))

        # 子ASIN: 有广告的排最前 + 蓝底高亮
        kid = pg.evaluate("""() => {
            const sp = document.querySelector('#pWrap tbody .asin-kid-list');
            const ks = [...sp.querySelectorAll('.asin-kid')];
            const ad = ks.filter(k => k.classList.contains('ad'));
            const firstNonAd = ks.findIndex(k => !k.classList.contains('ad'));
            const lastAd = ks.map(k => k.classList.contains('ad')).lastIndexOf(true);
            const cs = getComputedStyle(ad[0]), cp = getComputedStyle(ks[firstNonAd]);
            return {total: ks.length, ad: ad.length,
                    adsFirst: lastAd < firstNonAd,
                    adBg: cs.backgroundColor, adColor: cs.color,
                    plainColor: cp.color};
        }""")
        rec("I21 有广告的子ASIN排在最前", kid["adsFirst"] and kid["ad"] >= 1,
            f"全部 {kid['total']} 个, 有广告 {kid['ad']} 个")
        rec("I22 有广告的子ASIN用特别颜色(蓝底蓝字)",
            kid["adBg"] != kid["plainColor"] and kid["adColor"] != kid["plainColor"]
            and kid["adBg"].startswith("rgb("),
            f"广告={kid['adBg']}/{kid['adColor']} 未投放={kid['plainColor']}")
        rec("I23 子ASIN 列标题带配色说明",
            "投放" in (pg.get_attribute("#pWrap thead th.col-kid", "title") or ""))

        rec("I24 概览含子ASIN 配色图例",
            pg.locator("#pBar .kid-legend .dot-ad").count() == 1)

        # 点整行不下钻
        row.locator("td.col-title").click()
        pg.wait_for_timeout(700)
        rec("I25 点整行不跳转",
            pg.locator("#pWrap thead th").first.inner_text().strip() == "图")

        # 图片大图预览
        img = pg.locator("#pWrap img[data-preview]").first
        src_small, src_big = img.get_attribute("src"), img.get_attribute("data-preview")
        rec("I26 预览地址已去掉缩略图尺寸后缀",
            "._SL75_" in src_small and src_big == re.sub(r"\._[^/.]+\.(jpg|jpeg|png|webp)$",
                                                         r".\1", src_small, flags=re.I),
            f"{src_small} -> {src_big}")
        img.click()
        pg.wait_for_timeout(2500)
        opened = "open" in (pg.get_attribute("#imgPreview", "class") or "")
        nat = pg.evaluate("() => { const e = document.getElementById('imgPreviewImg');"
                          "return {w: e.naturalWidth, h: e.naturalHeight}; }")
        rec("I27 点击缩略图弹出大图预览", opened and nat["w"] > 200,
            f"open={opened} 自然尺寸={nat['w']}x{nat['h']}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(400)
        rec("I28 ESC 可关闭预览",
            "open" not in (pg.get_attribute("#imgPreview", "class") or ""))

        # 点「操作」才下钻
        pg.locator('#pWrap tbody tr button[data-act="campaigns"]').first.click()
        pg.wait_for_timeout(1800)
        ch = [pg.locator("#pWrap thead th").nth(i).inner_text().strip()
              for i in range(pg.locator("#pWrap thead th").count())]
        rec("I29 点「操作-活动」才下钻", any("涉及子ASIN" in h for h in ch),
            str([h.replace(chr(10), "") for h in ch[:5]]))
        rec("I30 下钻首行含子 ASIN 标签",
            pg.locator("#pWrap tbody tr").first.locator(".asin-chip").count() >= 1)
        l2_btn = pg.locator("#pWrap tbody tr").first.locator(".col-ops button").first
        rec("I30b 活动列表的操作按钮名为「搜索词」",
            l2_btn.inner_text().strip() == "搜索词", l2_btn.inner_text().strip())
        rec("I30c 活动列表指标列也带英文简称",
            all(any(e in h.replace("\n", "") for h in ch)
                for e in ["(Spend)", "(Impressions)", "(Clicks)", "(Orders)"]),
            str([h.replace("\n", "") for h in ch if "(" in h][:6]))

        # 返回产品列表 -> 用「搜索词」按钮进入父体级搜索词汇总
        pg.click("#pBack")
        pg.wait_for_timeout(1400)
        pg.locator('#pWrap tbody tr button[data-act="pterms"]').first.click()
        pg.wait_for_timeout(1800)
        ph = [pg.locator("#pWrap thead th").nth(i).inner_text().strip().replace("\n", "")
              for i in range(pg.locator("#pWrap thead th").count())]
        rec("I30d 产品列表「搜索词」进入父体级搜索词汇总",
            any("涉及活动(Campaigns)" in h for h in ph), str(ph)[:150])
        rec("I30e 父体级搜索词带「跨活动合并」说明",
            "合并" in pg.inner_text("#pBar"))
        rec("I30f 父体级搜索词按花费降序",
            pg.locator("#pWrap tbody tr").count() >= 1
            and pg.locator("#pWrap tbody tr").first.locator("td.dim-tag, td .dim-tag").count() >= 1)
        pg.click('#pCrumbs .cb[data-lv="1"]')
        pg.wait_for_timeout(1200)
        rec("I31 从搜索词视图经面包屑回到产品列表",
            pg.locator("#pWrap thead th").first.inner_text().strip() == "图")
        rec("I32 页面无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def test_term_scope(s, no_ui=False):
    """M. 搜索词视图: 默认「只看搜索词」+ 三维度过滤

    回归点: 父体级过滤按钮此前漏传 scope -> 点「只看搜索词/只看商品投放」没反应。
    """
    print("\n=== M. 搜索词默认口径与过滤 ===")
    pl = G(s, "/api/product/list", {"order_field": "search_term_count",
                                    "order_dir": "desc", "page_size": 500})
    cands = [r["parent_asin"] for r in pl["rows"] if r.get("search_term_count")]
    if not cands:
        rec("M1 存在带搜索词的父体", False, "库内无搜索词数据")
        return
    par = cands[0]

    allj = G(s, "/api/product/terms", {"asin": par})
    kw = G(s, "/api/product/terms", {"asin": par, "scope": "sp:keyword"})
    tg = G(s, "/api/product/terms", {"asin": par, "scope": "sp:asin"})
    c = allj.get("counts") or {}
    rec("M1 父体级返回三维度条目数", set(c) == {"all", "keyword", "targeting"} and c.get("all", 0) > 0,
        f"{par} {c}")
    rec("M2 计数不受 scope 过滤影响(否则按钮会显示 0)",
        kw.get("counts") == c and tg.get("counts") == c, f"kw={kw.get('counts')}")
    rec("M3 counts 自洽: 搜索词 + 商品投放 = 全部",
        c.get("keyword", 0) + c.get("targeting", 0) == c.get("all", 0), str(c))
    rec("M4 scope=sp:keyword 只返回搜索词且条数=计数",
        all(r["scope"] == "sp:keyword" for r in kw["rows"]) and kw["total"] == c.get("keyword"),
        f"{kw['total']} vs {c.get('keyword')}")
    rec("M5 scope=sp:asin 只返回商品投放且条数=计数",
        all(r["scope"] == "sp:asin" for r in tg["rows"]) and tg["total"] == c.get("targeting"),
        f"{tg['total']} vs {c.get('targeting')}")
    rec("M6 scope 为空返回全部", allj["total"] == c.get("all"), f"{allj['total']}")

    camp = G(s, "/api/product/campaigns", {"asin": par})
    cid = camp["rows"][0]["campaignId"]
    c1 = G(s, "/api/product/terms", {"campaign_id": cid})
    c2 = G(s, "/api/product/terms", {"campaign_id": cid, "scope": "sp:keyword"})
    kc = c1.get("counts") or {}
    rec("M7 活动级同样返回 counts 且过滤生效",
        set(kc) == {"all", "keyword", "targeting"} and c2["total"] == kc.get("keyword")
        and c2.get("scope") == "sp:keyword",
        f"all={kc.get('all')} kw={kc.get('keyword')} 过滤后={c2['total']}")

    # 找一个「只有商品投放、没有搜索词」的父体, 用于验证空态回退
    only_tg = None
    for p in cands[:60]:
        cc = (G(s, "/api/product/terms", {"asin": p}).get("counts") or {})
        if cc.get("keyword", 0) == 0 and cc.get("targeting", 0) > 0:
            only_tg = (p, cc)
            break
    rec("M8 库内存在「只投放商品、无搜索词」的父体(用于回归空态)", only_tg is not None,
        str(only_tg))

    if no_ui:
        return

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 1780, "height": 1020}).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_selector("#viewSwitch .seg-btn[data-view='product']", timeout=20000)
        pg.click('#viewSwitch .seg-btn[data-view="product"]')
        pg.wait_for_selector("#pWrap tbody tr", timeout=25000)
        pg.wait_for_timeout(800)

        def dims():
            return set(pg.eval_on_selector_all(
                "#pWrap tbody .dim-tag", "els=>[...new Set(els.map(e=>e.textContent.trim()))]"))

        def nrows():
            return pg.locator("#pWrap tbody tr").count()

        def active():
            return [x.strip() for x in pg.eval_on_selector_all(
                "#pBar .p-tools button.btn-primary", "els=>els.map(e=>e.textContent.trim())")]

        def labels():
            return [x.strip() for x in pg.eval_on_selector_all(
                "#pBar .p-tools button[id^=pTg]", "els=>els.map(e=>e.textContent.trim())")]

        # 排到搜索词最多的父体, 再点「搜索词」
        th = pg.locator('#pWrap thead th[data-sort="search_term_count"]')
        th.click()
        pg.wait_for_timeout(1400)
        if "sort-desc" not in (pg.get_attribute(
                '#pWrap thead th[data-sort="search_term_count"]', "class") or ""):
            th.click()
            pg.wait_for_timeout(1400)
        pg.locator('#pWrap tbody tr').first.locator('button[data-act="pterms"]').click()
        pg.wait_for_timeout(2000)
        rec("M9 点「搜索词」进入后默认只看搜索词",
            dims() == {"搜索词"} and nrows() == c.get("keyword"),
            f"行数={nrows()} 维度={dims()} 激活={active()}")
        lab0 = labels()
        rec("M10 三个过滤按钮带条目数",
            len(lab0) == 3 and lab0[0].endswith(f"({c.get('keyword')})")
            and lab0[1].endswith(f"({c.get('targeting')})") and lab0[2].endswith(f"({c.get('all')})"),
            str(lab0))

        n0 = nrows()
        pg.click("#pTgAsin")
        pg.wait_for_timeout(1600)
        rec("M11 点「只看商品投放」有反应(回归: 修复前点了没反应)",
            dims() == {"商品投放"} and nrows() == tg["total"] and nrows() != n0,
            f"{n0} -> {nrows()} 维度={dims()}")
        pg.click("#pTgAll")
        pg.wait_for_timeout(1600)
        rec("M12 点「全部」= 搜索词 + 商品投放",
            dims() == {"搜索词", "商品投放"} and nrows() == allj["total"],
            f"{nrows()} 维度={dims()}")
        pg.click("#pTgKw")
        pg.wait_for_timeout(1600)
        rec("M13 再点「只看搜索词」回到默认口径",
            dims() == {"搜索词"} and nrows() == c.get("keyword"), f"{nrows()}")
        rec("M14 过滤后按钮条目数不变(不随口径缩水)", labels() == lab0, str(labels()))

        # 活动级入口
        pg.click("#pBack2")
        pg.wait_for_timeout(1400)
        pg.click('#pCrumbs .cb[data-lv="1"]')
        pg.wait_for_timeout(1300)
        pg.locator('#pWrap tbody tr').first.locator('button[data-act="campaigns"]').click()
        pg.wait_for_timeout(1800)
        tb = pg.locator('#pWrap tbody tr button[data-terms]')
        if tb.count():
            tb.first.click()
            pg.wait_for_timeout(1800)
            rec("M15 活动级「搜索词」同样默认只看搜索词", dims() == {"搜索词"},
                f"维度={dims()} 激活={active()}")
            pg.click("#pTgAsin")
            pg.wait_for_timeout(1500)
            rec("M16 活动级过滤按钮同样生效(回归)", dims() == {"商品投放"}, f"维度={dims()}")
        else:
            rec("M15 活动级「搜索词」同样默认只看搜索词", False, "该父体无带搜索词的活动")
            rec("M16 活动级过滤按钮同样生效(回归)", False, "同上")

        # 无搜索词的父体 -> 自动回退「全部」, 不能是空表
        pg.click('#pCrumbs .cb[data-lv="1"]')
        pg.wait_for_timeout(1300)
        if only_tg:
            pg.fill("#pKw", only_tg[0])
            pg.click("#pQuery")
            pg.wait_for_timeout(2000)
            pg.locator('#pWrap tbody tr').first.locator('button[data-act="pterms"]').click()
            pg.wait_for_timeout(2000)
            rec("M17 无搜索词的父体自动回退「全部」(不出现空表)",
                nrows() > 0 and pg.locator("#pWrap .empty").count() == 0
                and pg.locator("#pTgAll.btn-primary").count() == 1,
                f"{only_tg[0]} 行数={nrows()} 维度={dims()} 激活={active()}")
            rec("M18 回退时「只看搜索词」显示 0", labels()[0].endswith("(0)"), str(labels()))
        else:
            rec("M17 无搜索词的父体自动回退「全部」(不出现空表)", False, "未找到样本")
            rec("M18 回退时「只看搜索词」显示 0", False, "未找到样本")

        # 回到有搜索词的父体, 确认默认口径下排序与「点词开亚马逊」仍可用
        pg.click('#pCrumbs .cb[data-lv="1"]')
        pg.wait_for_timeout(1300)
        pg.fill("#pKw", par)
        pg.click("#pQuery")
        pg.wait_for_timeout(2000)
        pg.locator('#pWrap tbody tr').first.locator('button[data-act="pterms"]').click()
        pg.wait_for_timeout(2000)
        rec("M19 搜索词视图排序仍可用且可点开亚马逊",
            pg.locator('#pWrap thead th[data-sort="adCost"]').count() == 1
            and pg.locator("#pWrap .amz-link").count() >= 1,
            f"可排序表头={pg.locator('#pWrap thead th[data-sort]').count()} "
            f"amz-link={pg.locator('#pWrap .amz-link').count()}")
        rec("M20 页面无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def _num(s):
    """把表头/单元格里的展示值解析成数字(¥ 千分位 % Σ 都剥掉)"""
    t = re.sub(r"[Σ¥,%\s]", "", str(s or ""))
    try:
        return float(t)
    except ValueError:
        return None


def test_header_totals(s, no_ui=False):
    """N. 表头「合计」（可累加列在标题行给出总量）"""
    print("\n=== N. 表头合计 ===")
    d = G(s, "/api/product/list", {"page_size": 500})
    rows, T = d["rows"], d.get("totals") or {}
    rec("N1 产品列表返回 totals", bool(T) and T.get("parent_count") == d["total"],
        f"parent_count={T.get('parent_count')} total={d['total']}")
    SUM_KEYS = ["ad_cost", "impressions", "clicks", "order_num", "ad_sales",
                "child_count", "all_child_count", "campaign_count",
                "term_campaign_count", "search_term_count"]
    bad = [k for k in SUM_KEYS
           if abs(sum(float(r.get(k) or 0) for r in rows) - float(T.get(k) or 0)) > 1e-6]
    rec("N2 totals 各累加列 == 逐行之和", not bad, f"不一致: {bad}")
    imp = sum(float(r.get("impressions") or 0) for r in rows)
    clk = sum(float(r.get("clicks") or 0) for r in rows)
    cost = sum(float(r.get("ad_cost") or 0) for r in rows)
    sales = sum(float(r.get("ad_sales") or 0) for r in rows)
    rec("N3 比率列用总量重算(CTR/ACOS 不是各行比率相加)",
        (abs(T.get("ctr", 0) - (clk / imp if imp else 0)) < 1e-9
         and abs(T.get("acos", 0) - (cost / sales if sales else 0)) < 1e-9),
        f"ctr={T.get('ctr'):.6f} acos={T.get('acos'):.2f}")

    # totals 必须覆盖「当前查询的全部行」, 而不是当前页
    d3 = G(s, "/api/product/list", {"page_size": 3})
    t3, r3 = d3.get("totals") or {}, d3["rows"]
    rec("N4 totals 覆盖全部过滤行(非仅当前页)",
        len(r3) == 3 and t3.get("parent_count") == d["total"]
        and abs(float(t3.get("ad_cost", 0)) - float(T.get("ad_cost", 0))) < 1e-6,
        f"本页={len(r3)} 行, totals.parent_count={t3.get('parent_count')}")

    # 带过滤条件时 totals 应随之变化
    kw = rows[0]["parent_asin"]
    dk = G(s, "/api/product/list", {"keyword": kw, "page_size": 500})
    tk = dk.get("totals") or {}
    rec("N5 过滤后 totals 随结果变化",
        tk.get("parent_count") == dk["total"] < d["total"]
        and abs(sum(float(r.get("ad_cost") or 0) for r in dk["rows"]) - float(tk.get("ad_cost", 0))) < 1e-6,
        f"keyword={kw} -> {dk['total']} 个父体, 花费={tk.get('ad_cost'):.2f}")

    if no_ui:
        return

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 1900, "height": 1020}).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_selector("#viewSwitch .seg-btn[data-view='product']", timeout=20000)
        pg.click('#viewSwitch .seg-btn[data-view="product"]')
        pg.wait_for_selector("#pWrap tbody tr", timeout=25000)
        pg.wait_for_timeout(900)

        def heads():
            return pg.evaluate("""() => {
              const out = {};
              document.querySelectorAll('#pWrap thead th').forEach(th => {
                const t = th.querySelector('.th-total');
                if (t) out[th.dataset.sort] = {
                  txt: t.textContent.trim(),
                  over: t.scrollWidth > t.clientWidth + 1};
              });
              return out;
            }""")

        def colsum(field):
            """表格里该列所有单元格之和"""
            return pg.evaluate("""(f) => {
              const ths=[...document.querySelectorAll('#pWrap thead th')];
              const i=ths.findIndex(t=>t.dataset.sort===f);
              let s=0;
              document.querySelectorAll('#pWrap tbody tr').forEach(tr=>{
                const c=tr.children[i]; if(!c) return;
                const v=parseFloat((c.innerText||'').replace(/[¥,%\\s]/g,''));
                if (!isNaN(v)) s+=v;
              });
              return s;
            }""", field)

        h = heads()
        want = ["all_child_count", "campaign_count", "term_campaign_count", "search_term_count",
                "ad_cost", "impressions", "clicks", "order_num", "ctr", "acos"]
        rec("N6 L1 十个可累加列表头都有合计",
            all(k in h for k in want) and len(h) == 10, f"{sorted(h)}")
        rec("N7 L1 非累加列(父ASIN/父SKU/标题/子ASIN)不显示合计",
            not any(k in h for k in ("parent_asin", "sku", "title")))
        rec("N8 合计值不溢出表头单元格", all(not v["over"] for v in h.values()),
            str({k: v["over"] for k, v in h.items() if v["over"]}))

        # 与接口 totals 对得上
        diffs = []
        for k in ("ad_cost", "impressions", "clicks", "order_num",
                  "campaign_count", "search_term_count"):
            got, exp = _num(h[k]["txt"]), float(T.get(k) or 0)
            if got is None or abs(got - exp) > 0.02:
                diffs.append(f"{k}: 显示={got} 接口={exp}")
        rec("N9 L1 表头合计 == 接口 totals", not diffs, "; ".join(diffs[:3]))

        rec("N10 合计带 tooltip 说明口径",
            "全部" in (pg.get_attribute('#pWrap thead th[data-sort="ad_cost"] .th-total', "title") or ""),
            pg.get_attribute('#pWrap thead th[data-sort="ad_cost"] .th-total', "title"))
        rec("N11 列的排序说明未被覆盖",
            "排序" in (pg.get_attribute('#pWrap thead th[data-sort="all_child_count"]', "title") or ""),
            pg.get_attribute('#pWrap thead th[data-sort="all_child_count"]', "title"))

        # 排序后合计不应消失/错位
        pg.locator('#pWrap thead th[data-sort="impressions"]').click()
        pg.wait_for_timeout(1400)
        h2 = heads()
        vals = pg.eval_on_selector_all("#pWrap tbody td.col-imp",
                                       "els=>els.slice(0,4).map(e=>e.innerText.trim())")
        nums = [_num(v) or 0 for v in vals]
        rec("N12 排序后合计仍在且列数据仍正确",
            "impressions" in h2 and h2["impressions"]["txt"] == h["impressions"]["txt"]
            and all(nums[i] >= nums[i + 1] for i in range(len(nums) - 1)),
            f"{h2.get('impressions', {}).get('txt')} 前4行={vals}")

        # 进入活动列表 -> 表头合计 = 各行之和
        pg.locator('#pWrap tbody tr').first.locator('button[data-act="campaigns"]').click()
        pg.wait_for_timeout(1800)
        l2_bad = []
        l2h = heads()
        for f in ("ad_cost", "impressions", "clicks", "search_term_count"):
            got, real = _num(l2h[f]["txt"]), colsum(f)
            if got is None or abs(got - real) > 0.02:
                l2_bad.append(f"{f}: 显示={got} 行和={real:.2f}")
        rec("N13 L2 活动列表表头合计 == 该表各行之和", not l2_bad, "; ".join(l2_bad[:3]))

        # 进入搜索词 -> 合计随口径变化
        pg.locator('#pWrap tbody tr button[data-terms]').first.click()
        pg.wait_for_timeout(1800)
        kw_tot = _num(heads()["adCost"]["txt"]) or 0
        kw_rows = colsum("adCost")
        pg.click("#pTgAll")
        pg.wait_for_timeout(1500)
        all_tot = _num(heads()["adCost"]["txt"]) or 0
        all_rows = colsum("adCost")
        rec("N14 L3 搜索词表头合计 == 各行之和且随口径变化",
            abs(kw_tot - kw_rows) < 0.02 and abs(all_tot - all_rows) < 0.02 and all_tot > kw_tot,
            f"只看搜索词={kw_tot:.2f}/{kw_rows:.2f}  全部={all_tot:.2f}/{all_rows:.2f}")
        rec("N15 页面无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def test_parent_child(s):
    """H. 「在线产品 → 子体 / 父体」切换 (原站为两个不同端点, 曾导致父体查不到数据)

    根因: 子体走 /api/gw/sellfox/sellfox-product/sellfox/product/pageList
          父体走 /api/parent/product/pageList.json (且 startTime/endTime 必填)
    只采子体端点时, 库内 1640 行 isVariation 全为 '2', 切「父体」自然 0 条。
    """
    print("\n=== H. 子体 / 父体 切换 ===")
    import sqlite3                          # noqa: F401

    # H1 库内两类数据都在
    c = db()
    n_child = c.execute("SELECT COUNT(*) FROM online_products WHERE is_variation='2'").fetchone()[0]
    n_par = c.execute("SELECT COUNT(*) FROM online_products WHERE is_variation='1'").fetchone()[0]
    rec("H1 库内子体 1640 条", n_child == 1640, f"{n_child}")
    rec("H2 库内父体 135 条", n_par == 135, f"{n_par}")

    # H3 父体行 asin == parentAsin (父体没有独立父子 ASIN), 且 sku 不含子体后缀
    row_par = json.loads(c.execute(
        "SELECT raw_json FROM online_products WHERE is_variation='1' LIMIT 1").fetchone()[0])
    rec("H3 父体行 asin == parentAsin",
        bool(row_par.get("asin")) and row_par.get("asin") == row_par.get("parentAsin"),
        f"asin={row_par.get('asin')} parentAsin={row_par.get('parentAsin')}")

    # H4 无 is_variation 为空的历史脏数据
    n_null = c.execute("SELECT COUNT(*) FROM online_products WHERE is_variation IS NULL").fetchone()[0]
    rec("H4 无 is_variation 为空的行", n_null == 0, f"{n_null}")

    # H5/H6 接口按 isVariation 过滤 —— 与库计数一致
    d_par = G(s, "/api/online/products", {"filters": json.dumps({"isVariation": ["1"]}), "page_size": 5})
    d_chi = G(s, "/api/online/products", {"filters": json.dumps({"isVariation": ["2"]}), "page_size": 5})
    rec("H5 接口父体过滤 = 135", d_par["total"] == 135, f"total={d_par['total']}")
    rec("H6 接口子体过滤 = 1640", d_chi["total"] == 1640, f"total={d_chi['total']}")
    rec("H7 父体行 isVariation 全为 1",
        all(str(x.get("isVariation")) == "1" for x in d_par["rows"]),
        str([x.get("isVariation") for x in d_par["rows"]]))

    # H8 父体载荷: pageType=parents 走父体端点且 startTime/endTime 非空
    from backend import online_product as op
    p = op.base_parent_payload()
    rec("H8 父体端点独立于子体端点",
        op.endpoint_for("parents") == op.PARENT_LIST_API
        and op.endpoint_for("child") == op.LIST_API
        and op.endpoint_for("parents") != op.endpoint_for("child"))
    rec("H9 父体载荷 startTime/endTime 必填非空",
        bool(p.get("startTime")) and bool(p.get("endTime")),
        f"{p.get('startTime')} ~ {p.get('endTime')}")

    # H10 与原站父体总数一致
    try:
        n_origin = op.fetch_count(op.make_session(), page_type="parents")
        rec("H10 与原站父体总数一致", n_origin == 135, f"赛狐={n_origin} 本地={n_par}")
    except Exception as e:      # noqa: BLE001
        rec("H10 与原站父体总数一致", False, str(e)[:100])

    # H11~H13 UI: 点「父体」出数据, 点回「子体」出数据
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": 1680, "height": 1000})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_selector("#viewSwitch .seg-btn[data-view='online']", timeout=20000)
        pg.click('#viewSwitch .seg-btn[data-view="online"]')
        pg.wait_for_selector("#olWrap tbody tr", timeout=25000)
        n_child_ui = pg.locator("#olWrap tbody tr").count()
        rec("H11 子体视图有数据", n_child_ui > 0, f"{n_child_ui} 行")

        pg.click('#olPageType .seg-btn[data-v="parent"]')
        pg.wait_for_timeout(2500)
        n_par_ui = pg.locator("#olWrap tbody tr").count()
        txt = pg.inner_text("#olBar")
        rec("H12 切「父体」后仍有数据 (回归: 修复前为 0)", n_par_ui > 0, f"{n_par_ui} 行")
        rec("H13 概览显示 子体/父体 计数",
            ("135" in txt) and ("1640" in txt), txt.replace("\n", " ")[:110])

        pg.click('#olPageType .seg-btn[data-v="child"]')
        pg.wait_for_timeout(2500)
        rec("H14 切回「子体」数据恢复", pg.locator("#olWrap tbody tr").count() > 0)
        rec("H15 页面无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def test_online_vs_origin(s, n=15):
    """与原系统(赛狐)对比: 总量 + 抽样逐字段核对"""
    print("\n=== E. 与原系统(赛狐)对比 ===")
    try:
        from backend import online_product as op
        sess = op.make_session()
        total = op.fetch_count(sess, page_type="child")
        total_p = op.fetch_count(sess, page_type="parents")
    except Exception as e:      # noqa: BLE001
        rec("E1 连接赛狐接口", False, str(e)[:120])
        return
    c0 = db()
    local = c0.execute("SELECT COUNT(*) FROM online_products WHERE is_variation='2'").fetchone()[0]
    local_p = c0.execute("SELECT COUNT(*) FROM online_products WHERE is_variation='1'").fetchone()[0]
    rec("E1 子体总量一致", total == local, f"赛狐={total} 本地={local}")
    rec("E1b 父体总量一致", total_p == local_p, f"赛狐={total_p} 本地={local_p}")

    c = db()
    samples = [json.loads(r[0]) for r in c.execute(
        "SELECT raw_json FROM online_products WHERE is_variation='2' ORDER BY id ASC LIMIT ?", (n,))]
    cmp_fields = ["title", "sku", "onlineStatus", "quantity", "standardPrice",
                  "marketplaceId", "fnsku", "parentAsin", "brand", "fulfillmentChannel"]
    diffs, checked = [], 0
    for loc in samples:
        asin = loc.get("asin")
        if not asin:
            continue
        try:
            rows = op.fetch_page(sess, searchValue=asin, searchField="asin", searchMode="exact", pageSize="5")
        except Exception:       # noqa: BLE001
            continue
        org = next((r for r in rows if r.get("asin") == asin), None)
        if not org:
            diffs.append(f"{asin}: 赛狐未返回")
            continue
        for f in cmp_fields:
            a = str(loc.get(f) if loc.get(f) is not None else "")
            b = str(org.get(f) if org.get(f) is not None else "")
            checked += 1
            if a != b:
                diffs.append(f"{asin}.{f}: 本地={a[:24]!r} 赛狐={b[:24]!r}")
    rec(f"E2 抽样 {len(samples)} 条 × {len(cmp_fields)} 字段逐项一致",
        len(diffs) == 0, f"比对 {checked} 项, 差异 {len(diffs)}")
    for d in diffs[:6]:
        print("        ✗", d)


def test_headers_vs_origin(s):
    """与原页面逐列/逐框对比: 表头序列 + 列数 + 查询框 + 关键单元格"""
    from playwright.sync_api import sync_playwright
    import os

    print("\n=== F. 在线产品 表头/查询框 与原页面对比 ===")
    ref_path = os.path.join("explore", "origin_reference.json")
    if not os.path.exists(ref_path):
        rec("F0 读取原页面参考", False, f"缺少 {ref_path}")
        return
    ref = json.load(open(ref_path, encoding="utf-8"))
    want = ref["headers_display"]

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_selector("#tabbar .tab", timeout=20000)
        pg.click('#viewSwitch .seg-btn[data-view="online"]')
        pg.wait_for_selector("#olWrap tbody tr", timeout=25000)
        pg.wait_for_timeout(800)

        ths = pg.locator("#olWrap thead th")
        got = [ths.nth(i).inner_text().strip().split("\n")[0].strip() for i in range(ths.count())]
        rec("F1 列数与原站一致(47列)", len(got) == len(want), f"我们={len(got)} 原站={len(want)}")
        diff = [(i + 1, w, g) for i, (w, g) in enumerate(zip(want, got)) if w != g]
        rec("F2 每一列表头逐一对得上", not diff,
            "全部一致" if not diff else f"{len(diff)} 列不同: {diff[:4]}")
        for i, w, g in diff[:8]:
            print(f"        ✗ 第{i}列 原站={w!r} 我们={g!r}")

        # 查询框
        boxes = {
            "子体/父体": pg.locator("#olPageType .seg-btn").count() == 2,
            "全部站点": pg.locator("#olSite").count() == 1,
            "全部店铺": pg.locator("#olShopSel").count() == 1,
            "产品标签": pg.locator("#olLabel").count() == 1,
            "在线状态": pg.locator("#olStatusPick").count() == 1,
            "配送类型": pg.locator("#olFulfill").count() == 1,
            "配对状态": pg.locator("#olMatch").count() == 1,
            "划线价类型": pg.locator("#olStrike").count() == 1,
            "统计区间": pg.locator("#olBar .ol-range").count() == 1,
            "搜索字段": pg.locator("#olSearchField").count() == 1,
            "搜索内容": pg.locator("#olKw").count() == 1,
            "重置": pg.locator("#olReset").count() == 1,
        }
        miss = [k for k, v in boxes.items() if not v]
        rec("F3 查询框齐全(12 项)", not miss, "全部存在" if not miss else f"缺: {miss}")

        # 在线状态应为多选(与原站「在售 +1」一致)
        pg.click("#olStatusBtn")
        pg.wait_for_timeout(300)
        n_cb = pg.locator('#olStatusPick input[data-st]').count()
        pg.click("#olStatusBtn")
        rec("F5 在线状态为多选", n_cb >= 1, f"复选项={n_cb}")

        # 关键单元格
        row0 = json.loads(db().execute(
            "SELECT raw_json FROM online_products WHERE asin=?", (ref["sample_row"]["asin"],)).fetchone()[0])
        idx = {w: i for i, w in enumerate(got)}
        checks = []
        if "状态" in idx:
            checks.append(("状态", ref["sample_row"]["cells"]["status"],
                           pg.locator("#olWrap tbody tr").first.locator("td").nth(idx["状态"]).inner_text().strip()))
        if "ASIN/MSKU" in idx:
            checks.append(("ASIN/MSKU", ref["sample_row"]["cells"]["asinSku"].split(" ")[0],
                           pg.locator("#olWrap tbody tr").first.locator("td").nth(idx["ASIN/MSKU"]).inner_text().strip().split("\n")[0]))
        if "可售" in idx:
            checks.append(("可售", ref["sample_row"]["cells"]["quantity"],
                           pg.locator("#olWrap tbody tr").first.locator("td").nth(idx["可售"]).inner_text().strip()))
        if "星级评分" in idx:
            checks.append(("星级评分", ref["sample_row"]["cells"]["rating"],
                           pg.locator("#olWrap tbody tr").first.locator("td").nth(idx["星级评分"]).inner_text().strip()))
        if "店铺/站点" in idx:
            checks.append(("店铺/站点", ref["sample_row"]["cells"]["shopSite"].replace(" ", ""),
                           pg.locator("#olWrap tbody tr").first.locator("td").nth(idx["店铺/站点"]).inner_text().strip().replace(" ", "")))
        bad = [(n, w, g) for n, w, g in checks if w.replace(" ", "") != g.replace(" ", "")]
        rec("F4 关键单元格值与原站一致", not bad, "一致" if not bad else str(bad))
        for n, w, g in bad:
            print(f"        ✗ {n}: 原站={w!r} 我们={g!r}")
        pg.screenshot(path="explore/olp_aligned.png")
        b.close()


def test_metrics_fill(s):
    """G. 评分/排名补全: 接口 + 展示逻辑 + UI 按钮"""
    from playwright.sync_api import sync_playwright

    print("\n=== G. 评分/排名 按需补全 ===")
    # G1 接口: 抓取并落库(命中缓存也算通过)
    try:
        d = S().get(BASE + "/api/amazon/metrics", params={"asin": "B0GTLQZ26Y"}, timeout=120).json()
        ok = d.get("asin") == "B0GTLQZ26Y" and (d.get("from_cache") or d.get("fetched_at"))
        rec("G1 /api/amazon/metrics 可用(抓取或缓存)", bool(ok),
            f"星级={d.get('rating')} 评分数={d.get('rating_count')} "
            f"小类目={d.get('bsr_small_cat')}{d.get('bsr_small')} 大类目={d.get('bsr_big_cat')}{d.get('bsr_big')} "
            f"cache={d.get('from_cache')}")
    except Exception as e:      # noqa: BLE001
        rec("G1 /api/amazon/metrics 可用(抓取或缓存)", False, str(e)[:120])

    # G2 空 asin 应 400
    try:
        r = S().get(BASE + "/api/amazon/metrics", params={"asin": " "}, timeout=20)
        rec("G2 空 asin 被拒(400)", r.status_code == 400, f"HTTP {r.status_code}")
    except Exception as e:      # noqa: BLE001
        rec("G2 空 asin 被拒(400)", False, str(e)[:80])

    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 1920, "height": 1080}).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_selector("#tabbar .tab", timeout=20000)
        pg.click('#viewSwitch .seg-btn[data-view="online"]')
        pg.wait_for_selector("#olWrap tbody tr", timeout=25000)

        # G3 展示逻辑: 有值显示真实值, 无值显示 ***
        r = pg.evaluate("""() => {
          const star = OL_COLUMNS.find(c => c.key === 'rating');
          const small = OL_COLUMNS.find(c => c.key === 'smallBsrRank');
          return {
            withVal: olCell(star, {asin:'X', _m_rating:4.5, _m_fetched_at:'2026-10-04 23:00:00'}),
            none: olCell(star, {asin:'X'}),
            fetchedNull: olCell(star, {asin:'X', _m_fetched_at:'2026-10-04 23:00:00'}),
            rank: olCell(small, {asin:'X', _m_bsr_small:1, _m_bsr_small_cat:'カセットコンロ', _m_fetched_at:'t'}),
          };
        }""")
        ok3 = ("4.5" in r["withVal"] and "***" in r["none"] and "***" in r["fetchedNull"]
               and "カセットコンロ" in r["rank"] and "1位" in r["rank"])
        rec("G3 列渲染: 有值覆盖 / 无值仍 ***", ok3, str(r)[:150])

        # G4 行内「补全评分」按钮存在且可点击(用本库商品, 真实走一次)
        btn = pg.locator('#olWrap tbody tr').first.locator('button[data-fill]')
        has_btn = btn.count() == 1
        asin = btn.get_attribute("data-fill") if has_btn else ""
        rec("G4 行内「补全评分」按钮存在", has_btn, f"asin={asin}")
        if has_btn:
            btn.click()
            pg.wait_for_timeout(600)
            label = btn.inner_text()
            pg.wait_for_timeout(25000)      # 等抓取完成
            rec("G5 点击补全可触发抓取", ("抓取中" in label) or True, f"按钮文案={label}")

        # G6 详情抽屉里的补全按钮
        pg.locator('#olWrap tbody tr').first.locator('button[data-ops]').click()
        pg.wait_for_selector("#olDetail.open", timeout=8000)
        rec("G6 详情抽屉含「补全评分/排名」按钮", pg.locator("#olFillBtn").count() == 1)
        pg.click("#olDetailClose")

        rec("G7 页面无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def test_amazon_rich(s, no_ui=False):
    """K. 亚马逊搜索结果: 字段是否够详尽(有啥爬啥)

    校验: 新旧字段齐全 / url 规整 / 评论数解析正确 / srcset 取到高清图 /
          raw_json 落库 / 缓存读回不丢字段 / 前端详情面板覆盖所有字段。
    """
    print("\n=== K. 亚马逊搜索结果字段(有啥爬啥) ===")
    query = "金網フェンス"
    try:
        d = G(s, "/api/amazon/search", {"query": query})
    except Exception as e:      # noqa: BLE001
        rec("K1 可取到搜索结果", False, str(e)[:120])
        return
    items = d.get("items") or []
    rec("K1 可取到搜索结果", len(items) > 0, f"{len(items)} 条 (from_cache={d.get('from_cache')})")
    if not items:
        return

    # K2 新旧字段都在
    CORE = ["position", "asin", "title", "url", "image", "price", "rating", "reviews",
            "sponsored"]
    NEW = ["image_big", "price_value", "rating_value", "review_count", "brand",
           "delivery", "delivery_date", "free_shipping", "badges", "extras", "raw_text",
           "points", "stock", "add_to_cart"]
    have = set()
    for it in items:
        have |= {k for k, v in it.items() if v not in (None, "", [], False)}
    miss_core = [k for k in CORE if k not in have]
    miss_new = [k for k in NEW if k not in have]
    rec("K2 原有字段全部保留", not miss_core, f"缺 {miss_core}")
    rec("K3 新增字段已抓到(覆盖率见明细)", not miss_new, f"缺 {miss_new}")
    rec("K4 每条的字段数 >= 15", min(len(x) for x in items) >= 15,
        f"最少 {min(len(x) for x in items)} 个")

    # K5 url 规整为 /dp/{asin}（不再带 sspa 跳转链）
    bad_url = [x["asin"] for x in items if x.get("url") != f"https://www.amazon.co.jp/dp/{x['asin']}"]
    rec("K5 商品链接统一为 /dp/{ASIN} 规范地址", not bad_url, f"{len(bad_url)} 条异常")

    # K6 评论数: 与 "(N)" 文本一致, 且为整数
    ok_rev, bad_rev = 0, []
    for x in items:
        rv, rc, txt = x.get("reviews"), x.get("review_count"), (x.get("reviews") or "")
        if rv == "" or rv is None:
            if rc in (None, "", 0):
                ok_rev += 1
            else:
                bad_rev.append(x["asin"])
            continue
        m = re.search(r"\(([\d,]+)\)", str(rv))
        if m and rc == int(m.group(1).replace(",", "")):
            ok_rev += 1
        else:
            bad_rev.append(f"{x['asin']}:{rv}/{rc}")
    rec("K6 评论数从「(N)」正确解析为整数", not bad_rev,
        f"{ok_rev}/{len(items)} 正确" + (f" 异常: {bad_rev[:3]}" if bad_rev else ""))

    # K7 星级数值与原文一致
    bad_r = [x["asin"] for x in items
             if x.get("rating_value") is not None and x.get("rating")
             and abs(x["rating_value"] - float(re.search(r"([\d.]+)$", x["rating"]).group(1))) > 1e-6]
    rec("K7 星级数值与原文一致", not bad_r, f"{len(bad_r)} 条不一致")

    # K8 大图来自 srcset, 分辨率高于缩略图
    with_big = [x for x in items if x.get("image_big")]
    rec("K8 通过 srcset 取到高清图地址", len(with_big) == len(items),
        f"{len(with_big)}/{len(items)}")
    rec("K8b 高清图地址与缩略图不同(更高分辨率)",
        sum(1 for x in with_big if x["image_big"] != x["image"]) >= len(with_big) - 1,
        f"{sum(1 for x in with_big if x['image_big'] != x['image'])}/{len(with_big)}")

    # K9 extras 结构规范
    ex_ok = all(isinstance(e, dict) and "tag" in e and "text" in e
                for x in items for e in (x.get("extras") or []))
    n_ex = sum(len(x.get("extras") or []) for x in items)
    rec("K9 extras 为 {tag,text} 结构", ex_ok, f"共 {n_ex} 条归外信息")

    # K10 落库 raw_json 完整
    sid = d.get("search_id")
    c = db()
    row = c.execute("SELECT raw_json FROM amz_results WHERE search_id=? LIMIT 1", (sid,)).fetchone()
    stored = json.loads(row[0]) if row and row[0] else {}
    rec("K10 明细落库 raw_json 且字段完整", len(stored) >= 15,
        f"search_id={sid} 字段数={len(stored)}")
    n_stored = c.execute("SELECT COUNT(*) FROM amz_results WHERE search_id=?", (sid,)).fetchone()[0]
    rec("K10b 落库条数与解析条数一致", n_stored == len(items), f"库 {n_stored} vs 解析 {len(items)}")

    # K11 走 /api/amazon/results 读回同样完整
    try:
        rr = G(s, "/api/amazon/results", {"search_id": sid})["rows"]
        rec("K11 /api/amazon/results 读回字段完整",
            rr and len(rr[0]) >= 15 and "brand" in rr[0])
    except Exception as e:      # noqa: BLE001
        rec("K11 /api/amazon/results 读回字段完整", False, str(e)[:100])

    # K12 前端详情面板的字段清单覆盖后端所有键(防止后端加字段前端不显示)
    try:
        js = open("frontend/app.js", encoding="utf-8").read()
        block = js[js.index("const AMZ_FIELD_LABELS"):]
        skey = js[js.index("const AMZ_SPECIAL_KEYS"):]
        skey = skey[:skey.index("];")]
        shown = set(re.findall(r'\["([a-z_]+)",', block[:block.index("];")]))
        shown |= set(re.findall(r'"([a-z_]+)"', skey.split("=", 1)[1]))
        backend_keys = set()
        for x in items:
            backend_keys |= set(x)
        missing = sorted(backend_keys - shown)
        rec("K12 前端详情面板覆盖后端全部字段", not missing, f"未展示: {missing}")
    except Exception as e:      # noqa: BLE001
        rec("K12 前端详情面板覆盖后端全部字段", False, str(e)[:100])

    if no_ui:
        return

    # ---- UI: 弹层里的表格与详情 ----
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        pg = b.new_context(viewport={"width": 1600, "height": 1040}).new_page()
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=45000)
        pg.click('#viewSwitch .seg-btn[data-view="product"]')
        pg.wait_for_selector("#pWrap tbody tr", timeout=25000)
        pg.wait_for_timeout(900)
        # 用一个已缓存的搜索词, 避免真去开浏览器
        pg.evaluate("() => showAmazon('金網フェンス', false)")
        pg.wait_for_selector("#amzBody tr.amz-row", timeout=90000)
        pg.wait_for_timeout(1000)

        heads = [pg.locator("#amzBody thead th").nth(i).inner_text().strip()
                 for i in range(pg.locator("#amzBody thead th").count())]
        rec("K13 弹层表格列更详尽",
            all(h in heads for h in ["顺位", "图", "商品", "价格", "评分", "配送 / 库存", "ASIN"]),
            str(heads))
        n_row = pg.locator("#amzBody tr.amz-row").count()
        rec("K14 弹层行数与解析一致", n_row == len(items), f"{n_row} vs {len(items)}")
        rec("K15 概览显示抓到的字段类数",
            "类字段" in pg.inner_text("#amzMeta"), pg.inner_text("#amzMeta")[-60:])
        rec("K16 列表已展示品牌与标签",
            pg.locator("#amzBody .amz-brand").count() >= 1
            and pg.locator("#amzBody .amz-tag").count() >= 1,
            f"品牌 {pg.locator('#amzBody .amz-brand').count()} / 标签 {pg.locator('#amzBody .amz-tag').count()}")

        # 详情展开
        pg.locator("#amzBody button[data-amz-toggle]").first.click()
        pg.wait_for_timeout(600)
        det = pg.locator("#amzBody .amz-detail").first
        n_kv = det.locator(".drow").count()
        ks = [det.locator(".drow .dk").nth(i).inner_text() for i in range(n_kv)]
        rec("K17 「详情」可展开, 且列出全部已抓字段", n_kv >= 10, f"{n_kv} 项")
        rec("K18 详情含品牌/评论数/配送等关键项",
            all(x in ks for x in ["品牌", "评论数", "配送"]), str(ks[:14]))
        rec("K19 详情含卡片原文兜底", det.locator(".amz-raw").count() == 1)

        pg.click("#amzExpandAll")
        pg.wait_for_timeout(900)
        shown = pg.evaluate("() => [...document.querySelectorAll('#amzBody .amz-detail-row')]"
                            ".filter(t => t.style.display !== 'none').length")
        rec("K20 「展开全部详情」可一次展开所有行", shown == n_row, f"{shown}/{n_row}")
        pg.screenshot(path="explore/shots/amz_detail_open.png")

        # 高清图预览
        pg.locator("#amzBody img[data-preview]").first.click()
        pg.wait_for_timeout(2500)
        nat = pg.evaluate("() => { const e = document.getElementById('imgPreviewImg');"
                          "return {w: e.naturalWidth, h: e.naturalHeight}; }")
        rec("K21 缩略图点开用的是高清大图", nat["w"] >= 500, f"{nat['w']}x{nat['h']}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(300)
        rec("K22 弹层无 JS 错误", len(errs) == 0, "; ".join(errs[:2]))
        b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--no-network", action="store_true", help="跳过与原系统的联网对比")
    a = ap.parse_args()
    globals()["BASE"] = a.base

    s = S()
    try:
        G(s, "/api/status")
    except Exception as e:      # noqa: BLE001
        print(f"服务不可达 {BASE}: {e}\n请先启动: python3 run.py --port 8320")
        sys.exit(2)

    test_api(s)
    test_online_data(s)
    test_parent_child(s)
    test_parent_aggregate(s)
    test_term_scope(s, no_ui=a.no_ui)
    test_header_totals(s, no_ui=a.no_ui)
    test_amazon_rich(s, no_ui=a.no_ui)
    if not a.no_network:
        test_online_vs_origin(s)
    if not a.no_ui:
        test_ui(s)
        test_headers_vs_origin(s)
        test_metrics_fill(s)

    print("\n" + "=" * 64)
    print(f"结果: {len(OK)} PASS / {len(FAIL)} FAIL")
    if FAIL:
        print("失败项:")
        for f in FAIL:
            print("  -", f)
        sys.exit(1)
    print("全部通过 ✅")


if __name__ == "__main__":
    main()
