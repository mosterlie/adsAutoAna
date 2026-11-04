# -*- coding: utf-8 -*-
"""广告产品级分时采集（只采产品级，活动/广告组维度由明细自行汇总）

数据源：赛狐「小时维度指标分析」弹窗接口
    multiple/adProduct/getAllProductHourData + adIds

三个页签 → 三张表（都是一行一个「产品 × 子分类 × 时段」）
    HOURLY            广告产品        → fact_ads_total      (product_code)
    HOURLY_KEYWORD    广告产品-投放    → fact_ads_match      (product_code, match_code)
    HOURLY_PLACEMENT  广告产品-广告位  → fact_ads_placement  (product_code, placement_code)

请求策略（省请求）：每个产品先只请求 HOURLY，
    全天曝光=0 且花费=0 → 标记 zero 并跳过，不再请求另外两个页签。

用法：
    # 全店某天
    python3 collect_campaign_dims.py --date 2026-11-03

    # 单个活动
    python3 collect_campaign_dims.py --date 2026-11-03 --campaign-id 222563897245121

    # 断点续跑 + 节流
    python3 collect_campaign_dims.py --date 2026-11-03 --resume --sleep 0.2

    # 导出宽表
    python3 collect_campaign_dims.py --date 2026-11-03 --csv data/exports
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

from backend import ads_fact_db as fdb
from backend.cookies import load_cookies
from backend.sellfox_client import SellfoxClient, _base_payload

API = "multiple/adProduct/getAllProductHourData"
LIST_API = "multiple/adProduct/getAdProductList"
MODELS = {fdb.SYSTEM_ALL: "HOURLY",
          fdb.SYSTEM_MATCH: "HOURLY_KEYWORD",
          fdb.SYSTEM_PLACEMENT: "HOURLY_PLACEMENT"}
SYSTEM_NAMES = {1: "广告总量", 2: "广告位", 3: "关联方式"}

SUM_FIELDS = [
    "impressions", "clicks", "adCost", "adOrderNum", "adSale", "adSaleNum",
    "orderNum", "adOtherOrderNum", "adSales", "adOtherSales", "adSelfSaleNum",
    "adOtherSaleNum", "addToCart", "detailPageViews", "viewImpressions",
    "brandedSearches", "newToBrandDetailPageViews", "ordersNewToBrandFTD",
    "salesNewToBrandFTD", "unitsOrderedNewToBrandFTD", "cumulativeReach",
    "video5SecondViews", "videoFirstQuartileViews", "videoMidpointViews",
    "videoThirdQuartileViews", "videoCompleteViews", "videoUnmutes",
    "eCPAddToCart",
]

LABEL_FIELDS = ["campaignName", "adGroupName", "asin", "sku", "title", "currency",
                "shopId", "shopName", "marketplaceId", "portfolioName", "type"]

NON_METRIC = {"title", "order", "predicate", "detail", "sfCampaignId", "id", "adId",
              "targetId", "name", "children"}

ZERO_METRICS = {"impressions": 0, "clicks": 0, "adCost": 0, "adOrderNum": 0,
                "adSale": 0, "adSaleNum": 0, "orderNum": 0, "ctr": 0,
                "acos": 0, "roas": 0, "cvr": 0}


# --------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------
def to_num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", ""))
        except ValueError:
            return None
    return None


def clean_metrics(row):
    return {k: v for k, v in row.items() if k not in NON_METRIC}


def merge_metrics(items):
    items = [m for m in items if isinstance(m, dict)]
    if not items:
        return {}
    if len(items) == 1:
        return dict(items[0])
    out = {}
    for f in SUM_FIELDS:
        vals = [v for v in (to_num(m.get(f)) for m in items) if v is not None]
        if vals:
            out[f] = round(sum(vals), 4)
    imp = out.get("impressions") or 0
    clk = out.get("clicks") or 0
    cost = out.get("adCost") or 0
    sale = out.get("adSale") or 0
    orders = out.get("adOrderNum") or 0
    if imp:
        out["ctr"] = round(clk / imp * 100, 4)
    if clk:
        out["cvr"] = round(orders / clk * 100, 4)
        out["adCostPerClick"] = round(cost / clk, 4)
    if sale:
        out["acos"] = round(cost / sale * 100, 4)
        if cost:
            out["roas"] = round(sale / cost, 4)
    if orders:
        out["cpa"] = round(cost / orders, 4)
    for f in LABEL_FIELDS:
        for m in items:
            if m.get(f) not in (None, "", "-"):
                out[f] = m[f]
                break
    return out


# --------------------------------------------------------------------------
# 产品枚举
# --------------------------------------------------------------------------
def list_products(client, shop_id, day, campaign_id="", ad_type="sp"):
    p = {**_base_payload("product"),
         "shopIdList": [str(shop_id)], "type": ad_type, "adType": ad_type,
         "statusList": ["enabled", "paused"],
         "startDate": day, "endDate": day}
    if campaign_id:
        p["campaignIdList"] = [str(campaign_id)]
        p["groupIdList"] = []
    return client.fetch_pages(LIST_API, p, page_size=500)


def register_product(conn, shop_id, row) -> int:
    """产品唯一性：广告组合 × 广告活动 × 广告组 × SKU"""
    portfolio = (row.get("portfolioName") or "").strip()
    if portfolio in ("-", "None"):
        portfolio = ""
    return fdb.product_code_for(
        conn, shop_id,
        asin=str(row.get("asin") or ""), sku=str(row.get("sku") or ""),
        title=str(row.get("title") or ""), portfolio_name=portfolio,
        campaign_id=str(row.get("campaignId") or ""),
        ad_group_id=str(row.get("adGroupId") or ""),
        ad_id=str(row.get("adId") or row.get("id") or ""))


# --------------------------------------------------------------------------
# 分时请求
# --------------------------------------------------------------------------
def build_payload(ad_id, day, model, shop_id, campaign_id, group_id, ad_type="sp"):
    prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    return {
        "type": ad_type, "adStrategyTypeList": [],
        "startDate": day, "endDate": day,
        "isCompare": False, "compareStartDate": prev, "compareEndDate": prev,
        "useAdvanced": False, "pageNo": 1, "pageSize": 200,
        "advanceFilter": {},
        "campaignIdList": [str(campaign_id)] if campaign_id else [],
        "groupIdList": [str(group_id)] if group_id else [],
        "searchContents": [], "searchMode": "exact", "searchType": "asin",
        "servingStatusList": [], "shopIdList": [str(shop_id)],
        "orderBy": None, "desc": False, "statusList": ["enabled", "paused"],
        "analyzeStartDate": day, "analyzeEndDate": day,
        "dateModel": model,
        "weekList": ["1", "2", "3", "4", "5", "6", "7"],
        "isHide": True, "adIds": [str(ad_id)], "fromSingle": True,
    }


def fetch(client, ad_id, day, model, shop_id, campaign_id, group_id, ad_type):
    p = build_payload(ad_id, day, model, shop_id, campaign_id, group_id, ad_type)
    return client.post(API, p).get("data") or {}


def code_maps(conn):
    by_src, by_name = {}, {}
    for r in conn.execute(
            "SELECT parent_code, item_code, item_name, source_key FROM dim_dict"
            " WHERE field_code = ?", (fdb.FIELD_SUBCAT,)):
        by_src[(int(r["parent_code"]), r["source_key"])] = int(r["item_code"])
        by_name[(int(r["parent_code"]), r["item_name"])] = int(r["item_code"])
    return by_src, by_name


def strip_prefix(title):
    return re.sub(r"^\s*(自动投放|手动投放|关键词|商品投放)\s*[:：]\s*", "", title or "").strip()


# --------------------------------------------------------------------------
# 单产品采集
# --------------------------------------------------------------------------
def collect_product(conn, client, product, day, shop_id, maps, ad_type="sp"):
    """返回 (data, requests)；data = {(system, code, hour): metrics}"""
    by_src, by_name = maps
    ad_id = product["ad_id"]
    cid, gid = product["campaign_id"], product["ad_group_id"]
    data, requests = {}, 0

    # 1) 广告产品：24 个小时行
    d = fetch(client, ad_id, day, MODELS[1], shop_id, cid, gid, ad_type)
    requests += 1
    rows = d.get("periodReportList") or []
    total_imp = sum(int(to_num(r.get("impressions")) or 0) for r in rows)
    total_cost = sum(to_num(r.get("adCost")) or 0 for r in rows)
    if not rows or (total_imp == 0 and total_cost == 0):
        return {}, requests, "zero"

    for row in rows:
        hour = int(str(row.get("title") or "0-1").split("-")[0])
        data[(fdb.SYSTEM_ALL, 1, hour)] = clean_metrics(row)

    # 2) 广告位
    d = fetch(client, ad_id, day, MODELS[2], shop_id, cid, gid, ad_type)
    requests += 1
    for row in d.get("periodReportList") or []:
        code = by_src.get((fdb.SYSTEM_PLACEMENT, row.get("predicate")))
        if code is None:
            code = by_name.get((fdb.SYSTEM_PLACEMENT, row.get("title")))
        if code is None:
            continue
        for i, m in enumerate(row.get("detail") or []):
            data[(fdb.SYSTEM_PLACEMENT, code, i)] = m

    # 3) 关联方式
    d = fetch(client, ad_id, day, MODELS[3], shop_id, cid, gid, ad_type)
    requests += 1
    for row in d.get("periodReportList") or []:
        code = by_name.get((fdb.SYSTEM_MATCH, strip_prefix(row.get("title"))))
        if code is None:
            continue
        for i, m in enumerate(row.get("detail") or []):
            data[(fdb.SYSTEM_MATCH, code, i)] = m

    # 4) 子类补零：广告位固定 4 个、关联方式固定 4 个，接口不返回的空子类补 0
    for code in (1, 2, 3, 4):
        for hour in range(24):
            data.setdefault((fdb.SYSTEM_PLACEMENT, code, hour), dict(ZERO_METRICS))
            data.setdefault((fdb.SYSTEM_MATCH, code, hour), dict(ZERO_METRICS))

    # 5) 日汇总
    for system in (1, 2, 3):
        for code in {c for (s, c, h) in data if s == system}:
            items = [m for (s, c, h), m in data.items() if s == system and c == code]
            data[(system, code, -1)] = merge_metrics(items)

    return data, requests, "success"


def write_rows(conn, day, shop_id, product_code, data):
    rows = {1: [], 2: [], 3: []}
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for (system, code, hour), m in data.items():
        base = {
            "stat_date": day, "hour": hour, "shop_id": str(shop_id),
            "currency": m.get("currency") or "JPY",
            "impressions": int(to_num(m.get("impressions")) or 0),
            "clicks": int(to_num(m.get("clicks")) or 0),
            "cost": round(to_num(m.get("adCost")) or 0, 4),
            "orders": int(to_num(m.get("adOrderNum")) or 0),
            "sales": round(to_num(m.get("adSale")) or 0, 4),
            "metrics_json": json.dumps(m, ensure_ascii=False),
            "source": "sellfox", "fetched_at": now,
        }
        if system == 1:
            rows[1].append({**base, "product_code": product_code})
        elif system == 2:
            rows[2].append({**base, "product_code": product_code, "placement_code": code})
        else:
            rows[3].append({**base, "product_code": product_code, "match_code": code})
    return sum(fdb.upsert(conn, fdb.TABLES[s], v) for s, v in rows.items())


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run(day, *, shop_id="711811", ad_type="sp", campaign_id="", resume=False,
        sleep_sec=0.2, csv_dir="", dry_run=False, rebuild=False, ad_ids=None):
    cookies = load_cookies()
    if not cookies:
        sys.exit("未找到登录 Cookie，请先同步赛狐登录态")
    client = SellfoxClient(cookies)

    conn = fdb.get_conn()
    dropped = fdb.drop_legacy(conn)
    if dropped:
        print(f"已移除旧结构表：{', '.join(dropped)}")
    fdb.init_db(conn)

    if rebuild:
        conn.execute("PRAGMA foreign_keys=OFF")
        for t in fdb.TABLES.values():
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM etl_product_pull")
        conn.execute("DELETE FROM dim_product WHERE product_code > 0")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'dim_product'")
        conn.commit()
        conn.execute("PRAGMA foreign_keys=ON")
        print("已清空三张事实表与产品编码")

    raw = list_products(client, shop_id, day, campaign_id, ad_type)
    if ad_ids:
        wanted = {str(x) for x in ad_ids}
        raw = [r for r in raw if str(r.get("adId") or r.get("id") or "") in wanted]
    products = []
    for r in raw:
        pcode = register_product(conn, shop_id, r)
        products.append({
            "product_code": pcode,
            "ad_id": str(r.get("adId") or r.get("id") or ""),
            "campaign_id": str(r.get("campaignId") or ""),
            "ad_group_id": str(r.get("adGroupId") or ""),
            "asin": r.get("asin"), "sku": r.get("sku"),
        })
    conn.commit()
    print(f"[{day}] 产品 {len(products)} 个"
          + (f"（活动 {campaign_id}）" if campaign_id else "（全店）"))

    if dry_run:
        for p in products[:20]:
            print(f"  - {p['sku']} / {p['asin']} adId={p['ad_id']}")
        if len(products) > 20:
            print(f"  ... 其余 {len(products) - 20} 个略")
        conn.close()
        return

    done = fdb.pulled_products(conn, day, shop_id) if resume else {}
    maps = code_maps(conn)
    ok = zero = fail = skip = 0
    requests = 0
    written = 0
    all_data = {}
    t0 = time.time()

    for idx, p in enumerate(products, 1):
        pcode = p["product_code"]
        if resume and done.get(pcode) in ("success", "zero"):
            skip += 1
            continue
        try:
            data, req, status = collect_product(conn, client, p, day, shop_id, maps, ad_type)
            requests += req
            if status == "zero":
                zero += 1
                fdb.log_product_pull(conn, day, shop_id, pcode, p["ad_id"], "zero", 0)
                conn.commit()
            else:
                n = write_rows(conn, day, shop_id, pcode, data)
                fdb.log_product_pull(conn, day, shop_id, pcode, p["ad_id"], "success", n)
                conn.commit()
                written += n
                ok += 1
                for (system, code, hour), m in data.items():
                    all_data[(system, code, hour, pcode)] = m
        except Exception as exc:  # noqa: BLE001
            fail += 1
            conn.rollback()
            fdb.log_product_pull(conn, day, shop_id, pcode, p["ad_id"], "failed",
                                 0, str(exc))
            conn.commit()
            print(f"  [{idx}/{len(products)}] ✗ {p['sku']}: {str(exc)[:120]}")
        if sleep_sec:
            time.sleep(sleep_sec)

    elapsed = time.time() - t0

    # 汇总
    row = conn.execute(
        "SELECT (SELECT SUM(impressions) FROM fact_ads_total WHERE stat_date=? AND hour=-1),"
        "       (SELECT SUM(impressions) FROM fact_ads_placement WHERE stat_date=? AND hour=-1),"
        "       (SELECT SUM(impressions) FROM fact_ads_match WHERE stat_date=? AND hour=-1)",
        (day, day, day)).fetchone()
    print(f"\n[{day}] 成功 {ok} / 零曝光 {zero} / 失败 {fail} / 跳过 {skip}")
    print(f"  请求 {requests} 次，耗时 {elapsed / 60:.1f} 分钟，写入 {written} 行")
    print(f"  日曝光汇总：广告总量={row[0]} 广告位={row[1]} 关联方式={row[2]}")

    if csv_dir and all_data:
        path = os.path.join(csv_dir, f"{campaign_id or 'all'}_{day.replace('-', '')}"
                                     f"_product_all_metrics.csv")
        export_csv(path, all_data)
        print(f"  CSV: {path}")
    conn.close()


def export_csv(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    keys = sorted({k for m in data.values() for k in m})
    head = ["system_code", "system_name", "code", "code_name", "product", "hour"] + keys
    conn = fdb.get_conn()
    code_names = {(int(r["parent_code"]), int(r["item_code"])): r["item_name"]
                  for r in conn.execute("SELECT parent_code, item_code, item_name"
                                        " FROM dim_dict WHERE field_code = 2")}
    prods = {r["product_code"]: f"{r['sku']} / {r['asin']}"
             for r in conn.execute("SELECT product_code, sku, asin FROM dim_product")}
    conn.close()
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        for (system, code, hour, pcode), m in sorted(data.items()):
            w.writerow([system, SYSTEM_NAMES[system], code,
                        code_names.get((system, code), ""), prods.get(pcode, ""),
                        hour] + [m.get(k, "") for k in keys])


def main():
    ap = argparse.ArgumentParser(description="广告产品级分时采集（三体系）")
    ap.add_argument("--date", required=True, help="yyyy-mm-dd")
    ap.add_argument("--shop-id", default="711811")
    ap.add_argument("--ad-type", default="sp", choices=["sp", "sb", "sd"])
    ap.add_argument("--campaign-id", default="", help="只采某个活动")
    ap.add_argument("--ad-id", action="append", default=[],
                    help="只采指定广告产品（可重复），如 --ad-id 362907926691507")
    ap.add_argument("--resume", action="store_true", help="跳过已成功的产品")
    ap.add_argument("--sleep", type=float, default=0.2, help="每个产品之间的间隔秒数")
    ap.add_argument("--csv", default="", help="导出宽表目录")
    ap.add_argument("--dry-run", action="store_true", help="只列产品，不请求明细")
    ap.add_argument("--rebuild", action="store_true",
                    help="先清空三张事实表、产品编码与采集日志")
    a = ap.parse_args()

    run(a.date, shop_id=a.shop_id, ad_type=a.ad_type, campaign_id=a.campaign_id,
        resume=a.resume, sleep_sec=a.sleep, csv_dir=a.csv, dry_run=a.dry_run,
        rebuild=a.rebuild, ad_ids=a.ad_id)


if __name__ == "__main__":
    main()
