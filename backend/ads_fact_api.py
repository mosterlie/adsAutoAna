# -*- coding: utf-8 -*-
"""广告产品级分时查询 API

    GET /api/ads-fact/dates                          有数据的日期
    GET /api/ads-fact/products?date=&keyword=        产品列表（按当日曝光降序）
    GET /api/ads-fact/detail?date=&product_code=     单产品三体系分时明细
"""
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from backend import ads_fact_db as fdb

router = APIRouter(prefix="/api/ads-fact", tags=["ads-fact"])

CORE = ["impressions", "clicks", "cost", "ctr", "cpc", "acos", "roas", "cvr",
        "orders", "sales", "addToCart", "detailPageViews"]

# (system_code, key, label, 子分类列, 固定子类名)
# 广告总量只有「产品」这一个维度，没有额外子分类列
SYSTEMS = [
    (fdb.SYSTEM_ALL, "total", "广告总量", None, "广告总量"),
    (fdb.SYSTEM_MATCH, "match", "广告产品-投放", "match_code", None),
    (fdb.SYSTEM_PLACEMENT, "placement", "广告产品-广告位", "placement_code", None),
]


def _row_metrics(row: Any) -> Dict[str, Any]:
    """核心指标，兼容 cost/adCost 命名"""
    if row is None:
        return {k: 0 for k in CORE}
    return {
        "impressions": int(row["impressions"] or 0),
        "clicks": int(row["clicks"] or 0),
        "cost": round(float(row["cost"] or 0), 2),
        "orders": int(row["orders"] or 0),
        "sales": round(float(row["sales"] or 0), 2),
        "ctr": round(float(row["clicks"] or 0) / row["impressions"] * 100, 2)
        if row["impressions"] else 0.0,
        "cpc": round(float(row["cost"] or 0) / row["clicks"], 2) if row["clicks"] else 0.0,
        "acos": round(float(row["cost"] or 0) / row["sales"] * 100, 2)
        if row["sales"] else 0.0,
        "roas": round(float(row["sales"] or 0) / float(row["cost"] or 0), 2)
        if row["cost"] else 0.0,
        "cvr": round(float(row["orders"] or 0) / row["clicks"] * 100, 2)
        if row["clicks"] else 0.0,
        "addToCart": 0,
        "detailPageViews": 0,
    }


@router.get("/dates")
def dates() -> Dict[str, Any]:
    conn = fdb.get_conn()
    try:
        rows = [r[0] for r in conn.execute(
            "SELECT DISTINCT stat_date FROM fact_ads_total ORDER BY stat_date DESC")]
    finally:
        conn.close()
    return {"dates": rows}


@router.get("/products")
def products(date: str = Query(...), keyword: str = "", limit: int = 500) -> Dict[str, Any]:
    conn = fdb.get_conn()
    try:
        sql = ("SELECT p.product_code, p.sku, p.asin, p.title, p.campaign_id,"
               "       p.ad_group_id, p.portfolio_name, p.ad_id,"
               "       COALESCE(t.impressions,0) imp, COALESCE(t.clicks,0) clk,"
               "       COALESCE(t.cost,0) cost"
               "  FROM dim_product p"
               "  LEFT JOIN fact_ads_total t ON t.product_code = p.product_code"
               "        AND t.stat_date = ? AND t.hour = -1"
               " WHERE p.product_code > 0")
        args: List[Any] = [date]
        if keyword:
            sql += " AND (p.sku LIKE ? OR p.asin LIKE ?)"
            args += [f"%{keyword}%", f"%{keyword}%"]
        sql += " ORDER BY imp DESC, p.product_code LIMIT ?"
        args.append(int(limit))
        rows = conn.execute(sql, args).fetchall()
    finally:
        conn.close()
    return {"date": date, "products": [dict(r) for r in rows]}


@router.get("/detail")
def detail(date: str = Query(...), product_code: int = Query(...)) -> Dict[str, Any]:
    conn = fdb.get_conn()
    try:
        prod = conn.execute(
            "SELECT * FROM dim_product WHERE product_code = ?", (product_code,)).fetchone()
        if not prod:
            raise HTTPException(404, "产品不存在")

        camp = conn.execute(
            "SELECT name, state FROM dim_campaign WHERE campaign_id = ?",
            (prod["campaign_id"],)).fetchone()
        grp = conn.execute(
            "SELECT name, state FROM dim_ad_group WHERE ad_group_id = ?",
            (prod["ad_group_id"],)).fetchone()

        systems = []
        for system_code, key, label, code_col, fixed_name in SYSTEMS:
            table = fdb.TABLES[system_code]
            names = {int(r["item_code"]): r["item_name"] for r in conn.execute(
                "SELECT item_code, item_name FROM dim_dict"
                " WHERE field_code = ? AND parent_code = ?", (fdb.FIELD_SUBCAT, system_code))}
            codes = []
            wanted = [1] if fixed_name else sorted(names)
            for code in wanted:
                cond = "stat_date=? AND product_code=?" + (
                    f" AND {code_col}=?" if code_col else "")
                args = (date, product_code, code) if code_col else (date, product_code)
                day = conn.execute(
                    f"SELECT * FROM {table} WHERE {cond} AND hour=-1", args).fetchone()
                if day is None and fixed_name:
                    continue
                hours_raw = {int(r["hour"]): r for r in conn.execute(
                    f"SELECT * FROM {table} WHERE {cond} AND hour>=0", args)}
                hours = [_row_metrics(hours_raw.get(h)) for h in range(24)]
                codes.append({
                    "code": code,
                    "name": names.get(code, fixed_name or str(code)),
                    "day": _row_metrics(day),
                    "hours": hours,
                })
            if not codes:
                continue
            total = {k: round(sum(c["day"][k] for c in codes), 2) for k in CORE}
            systems.append({"key": key, "label": label, "codes": codes, "total": total})

        overall = systems[0]["total"] if systems else _row_metrics(None)
    finally:
        conn.close()

    return {
        "date": date,
        "product": dict(prod),
        "campaign": dict(camp) if camp else {},
        "ad_group": dict(grp) if grp else {},
        "hour_axis": [f"{h:02d}" for h in range(24)],
        "systems": systems,
        "overall": overall,
    }
