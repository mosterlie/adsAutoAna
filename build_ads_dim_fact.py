# -*- coding: utf-8 -*-
"""重建 fact_ads_total（广告总量，产品级）

广告位 / 关联方式的数据必须按产品从赛狐「小时维度指标分析」弹窗取，
由 collect_campaign_dims.py 负责写入，本脚本不再接管这两张表。

    广告总量 ← fact_ads_performance.grain='ad' → fact_ads_total (product_code)

用法：
    python3 build_ads_dim_fact.py            # 增量覆盖
    python3 build_ads_dim_fact.py --rebuild  # 清空事实表与产品编码后重建
    python3 build_ads_dim_fact.py --check    # 只对账
"""
import argparse
import sys
from datetime import datetime

from backend import ads_fact_db as fdb

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731

SQL_TOTAL = """
INSERT OR REPLACE INTO fact_ads_total
    (stat_date, hour, shop_id, product_code, currency, impressions, clicks,
     cost, orders, sales, metrics_json, source, fetched_at)
SELECT f.stat_date, f.hour, f.shop_id, p.product_code, MAX(f.currency),
       SUM(f.impressions), SUM(f.clicks), ROUND(SUM(f.cost), 4),
       SUM(f.orders), SUM(f.sales), NULL, 'sellfox', :now
  FROM fact_ads_performance f
  JOIN dim_product p
    ON p.shop_id = f.shop_id
   AND p.ad_group_id = f.ad_group_id
   AND p.product_key = COALESCE(NULLIF(f.sku, ''), f.asin)
 WHERE f.grain = 'ad'
 GROUP BY f.stat_date, f.hour, f.shop_id, p.product_code
"""


def sync_products(conn) -> int:
    """dim_ad + dim_campaign → dim_product，按 组合×活动×组×SKU 分配编码"""
    rows = conn.execute(
        "SELECT a.shop_id, a.asin, a.sku, a.title, a.ad_id, a.ad_group_id,"
        "       a.campaign_id, COALESCE(c.portfolio_name, '') AS portfolio_name"
        "  FROM dim_ad a"
        "  LEFT JOIN dim_campaign c ON c.campaign_id = a.campaign_id").fetchall()
    for r in rows:
        portfolio = (r["portfolio_name"] or "").strip()
        if portfolio in ("-", "None"):
            portfolio = ""                      # 赛狐无广告组合时返回 '-'
        fdb.product_code_for(
            conn, r["shop_id"], asin=r["asin"] or "", sku=r["sku"] or "",
            title=r["title"] or "", portfolio_name=portfolio,
            campaign_id=r["campaign_id"] or "", ad_group_id=r["ad_group_id"] or "",
            ad_id=r["ad_id"] or "")
    return len(rows)


def sync_total(conn, now: str = "", overwrite: bool = True) -> int:
    """按 fact_ads_performance 重建广告总量

    overwrite=False：只补缺，不覆盖已有行。三体系采集（collect_campaign_dims）
    写入的日汇总以 HOURLY 口径为准，不能被日指标聚合值盖掉。
    """
    sql = SQL_TOTAL
    if not overwrite:
        sql = sql.replace("INSERT OR REPLACE", "INSERT OR IGNORE")
    return conn.execute(sql, {"now": now or NOW()}).rowcount


def rebuild(conn, reset: bool = False) -> dict:
    now = NOW()
    conn.execute("PRAGMA foreign_keys=OFF")
    if reset:
        conn.execute("DELETE FROM fact_ads_total")
        conn.execute("DELETE FROM dim_product WHERE product_code > 0")
        conn.execute("DELETE FROM sqlite_sequence WHERE name = 'dim_product'")
    counts = {"产品实体数": sync_products(conn)}
    counts["广告总量"] = sync_total(conn, now)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    return counts


def check(conn) -> bool:
    """按广告活动逐一对账（各表覆盖的活动范围可能不同）"""
    sql = ("SELECT p.campaign_id, SUM(f.impressions) imp, SUM(f.clicks) cl,"
           "       ROUND(SUM(f.cost),2) cost"
           "  FROM {t} f JOIN dim_product p ON p.product_code = f.product_code"
           " WHERE f.hour = -1 GROUP BY p.campaign_id")
    data = {t: {r["campaign_id"]: (r["imp"], r["cl"], r["cost"])
                for r in conn.execute(sql.format(t=t))}
            for t in fdb.TABLES.values()}

    camps = sorted(set().union(*[set(v) for v in data.values()]))
    print("\n按活动对账（日汇总 hour=-1）：")
    ok = True
    for cid in camps:
        print(f"  活动 {cid}")
        for system, table in fdb.TABLES.items():
            v = data[table].get(cid)
            print(f"    体系{system} {table:<20}"
                  + (f" imp={v[0]} clicks={v[1]} cost={v[2]}" if v else " 无数据"))
        vals = {s: data[t][cid] for s, t in fdb.TABLES.items() if cid in data[t]}
        if len({v[0] for v in vals.values()}) > 1:
            print("    ✗ 曝光口径不一致")
            ok = False
        elif len(vals) == 3:
            print("    ✓ 三表一致")
    return ok


def main():
    ap = argparse.ArgumentParser(description="广告总量事实表重建")
    ap.add_argument("--rebuild", action="store_true", help="清空广告总量表与产品编码后重建")
    ap.add_argument("--check", action="store_true", help="只对账")
    a = ap.parse_args()

    conn = fdb.get_conn()
    dropped = fdb.drop_legacy(conn)
    if dropped:
        print(f"已移除旧结构表：{', '.join(dropped)}")
    fdb.init_db(conn)

    if not a.check:
        print("重建 fact_ads_total …")
        for k, v in rebuild(conn, reset=a.rebuild).items():
            print(f"  {k}: {v}")
    ok = check(conn)
    conn.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
