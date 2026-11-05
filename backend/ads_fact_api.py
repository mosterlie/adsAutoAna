# -*- coding: utf-8 -*-
"""广告产品级分时查询 API

    GET /api/ads-fact/dates                          有数据的日期
    GET /api/ads-fact/products?date=&keyword=        产品列表（按当日曝光降序）
    GET /api/ads-fact/detail?date=&product_code=     单产品三体系分时明细
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from backend import ads_fact_db as fdb
from backend import ads_fetch

router = APIRouter(prefix="/api/ads-fact", tags=["ads-fact"])

CORE = ["impressions", "clicks", "cost", "ctr", "cpc", "acos", "roas", "cvr",
        "orders", "sales", "addToCart", "detailPageViews"]

# metrics_json 里的非指标键（维度/序号）与核心指标别名（避免重复出列）
NON_METRIC_KEYS = {"title", "order", "predicate", "detail", "id", "adId",
                   "targetId", "name", "children", "sfCampaignId",
                   "adCost", "adSale", "adOrderNum", "adCostPerClick"}

# (system_code, key, label, 子分类列, 固定子类名)
# 广告总量只有「产品」这一个维度，没有额外子分类列
SYSTEMS = [
    (fdb.SYSTEM_ALL, "total", "广告总量", None, "广告总量"),
    (fdb.SYSTEM_MATCH, "match", "广告产品-投放", "match_code", None),
    (fdb.SYSTEM_PLACEMENT, "placement", "广告产品-广告位", "placement_code", None),
]


def _to_num(v: Any) -> Optional[float]:
    """赛狐指标多为字符串（"-" / "0.00~0.00" 等占位），能转数字就转"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _row_metrics(row: Any) -> Dict[str, Any]:
    """核心指标（按原始值重算）+ 赛狐原始指标全量透传

    赛狐「小时维度指标分析」页面展示的列都在 metrics_json 里，
    这里一并返回，页面可任选指标（曝光/点击/CTR/CPC/加购/曝光份额…）。
    """
    if row is None:
        return {k: 0 for k in CORE}
    imp = int(row["impressions"] or 0)
    clk = int(row["clicks"] or 0)
    cost = float(row["cost"] or 0)
    orders = int(row["orders"] or 0)
    sales = float(row["sales"] or 0)
    try:
        raw: Dict[str, Any] = json.loads(row["metrics_json"] or "{}")
    except (ValueError, TypeError):
        raw = {}
    out: Dict[str, Any] = {
        "impressions": imp,
        "clicks": clk,
        "cost": round(cost, 2),
        "orders": orders,
        "sales": round(sales, 2),
        "ctr": round(clk / imp * 100, 2) if imp else 0.0,
        "cpc": round(cost / clk, 2) if clk else 0.0,
        "acos": round(cost / sales * 100, 2) if sales else 0.0,
        "roas": round(sales / cost, 2) if cost else 0.0,
        "cvr": round(orders / clk * 100, 2) if clk else 0.0,
        "addToCart": int(_to_num(raw.get("addToCart")) or 0),
        "detailPageViews": int(_to_num(raw.get("detailPageViews")) or 0),
    }
    # 其余赛狐指标原样带上（含 0，保证可选项与赛狐页面列一致）
    for k, v in raw.items():
        if k in out or k in NON_METRIC_KEYS:
            continue
        num = _to_num(v)
        if num is None:                 # "-" / "0.00~0.00" 这类占位直接跳过
            continue
        out[k] = int(num) if float(num).is_integer() else round(num, 4)
    return out


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


# 合计时不能相加的比率/排名类字段（含这些片段的跳过）
RATE_HINTS = ("Rate", "Percentage", "Percent", "Share", "Rank", "Is")
EXTRA_RATIO = {"cpm", "vcpm", "cpa", "acoas", "asoas", "acots", "asots",
               "adCostPerClick", "cpc", "ctr", "cvr", "acos", "roas",
               "advertisingUnitPrice", "selfAdvertisingUnitPrice",
               "otherAdvertisingUnitPrice", "impressionsFrequencyAverage"}


def _sum_metrics(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """合计：原始指标相加，比率指标重算（不能把各路 CTR/ACOS 直接相加）"""
    imp = sum(int(m.get("impressions") or 0) for m in items)
    clk = sum(int(m.get("clicks") or 0) for m in items)
    cost = sum(float(m.get("cost") or 0) for m in items)
    orders = sum(int(m.get("orders") or 0) for m in items)
    sales = sum(float(m.get("sales") or 0) for m in items)
    out: Dict[str, Any] = {
        "impressions": imp, "clicks": clk, "cost": round(cost, 2),
        "orders": orders, "sales": round(sales, 2),
        "ctr": round(clk / imp * 100, 2) if imp else 0.0,
        "cpc": round(cost / clk, 2) if clk else 0.0,
        "acos": round(cost / sales * 100, 2) if sales else 0.0,
        "roas": round(sales / cost, 2) if cost else 0.0,
        "cvr": round(orders / clk * 100, 2) if clk else 0.0,
        "addToCart": sum(int(m.get("addToCart") or 0) for m in items),
        "detailPageViews": sum(int(m.get("detailPageViews") or 0) for m in items),
    }
    # 其余赛狐指标按可累加的做求和（比率类保持 0，留给前端不展示）
    keys = {k for m in items for k in m}
    for k in keys:
        if k in out or k in EXTRA_RATIO or any(h in k for h in RATE_HINTS):
            continue
        nums = [m[k] for m in items if isinstance(m.get(k), (int, float))]
        if not nums:
            continue
        total = sum(nums)
        out[k] = int(total) if float(total).is_integer() else round(total, 4)
    return out


def _detail_one(conn: Any, date: str, product_code: int) -> Dict[str, Any]:
    """单日三体系明细（hour 长度 24）"""
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
        systems.append({"key": key, "label": label, "codes": codes,
                        "total": _sum_metrics([c["day"] for c in codes])})

    return {
        "product": dict(prod),
        "campaign": dict(camp) if camp else {},
        "ad_group": dict(grp) if grp else {},
        "systems": systems,
        "overall": systems[0]["total"] if systems else _row_metrics(None),
    }


@router.get("/detail")
def detail(date: str = "", dates: str = "", product_code: int = Query(...)) -> Dict[str, Any]:
    """单日：date=2026-11-03；多日：dates=2026-11-02,2026-11-03（连续小时轴）"""
    day_list = sorted({d.strip() for d in (dates or date).split(",") if d.strip()})
    if not day_list:
        raise HTTPException(400, "缺少日期参数 date 或 dates")

    conn = fdb.get_conn()
    try:
        per_day = [_detail_one(conn, d, product_code) for d in day_list]
    finally:
        conn.close()

    axis = [{"date": d, "hour": h, "label": f"{h:02d}"} for d in day_list for h in range(24)]
    for i, a in enumerate(axis):
        a["idx"] = i

    systems: List[Dict[str, Any]] = []
    base_keys = [s["key"] for s in per_day[0]["systems"]]
    for key in base_keys:
        label = next(s["label"] for s in per_day[0]["systems"] if s["key"] == key)
        codes_by_day = []
        for one in per_day:
            sys_one = next((s for s in one["systems"] if s["key"] == key), None)
            codes_by_day.append({c["code"]: c for c in (sys_one["codes"] if sys_one else [])})

        codes = []
        for code in sorted({c for m in codes_by_day for c in m}):
            name, hours, days = "", [], []
            for m in codes_by_day:
                c = m.get(code)
                if c:
                    name = name or c["name"]
                    hours += c["hours"]
                    days.append(c["day"])
                else:
                    hours += [_row_metrics(None) for _ in range(24)]
                    days.append(_row_metrics(None))
            codes.append({"code": code, "name": name,
                          "day": _sum_metrics(days), "hours": hours})
        if not codes:
            continue
        systems.append({"key": key, "label": label, "codes": codes,
                        "total": _sum_metrics([c["day"] for c in codes])})

    first = per_day[0]
    return {
        "dates": day_list,
        "date": day_list[-1],
        "product": first["product"],
        "campaign": first["campaign"],
        "ad_group": first["ad_group"],
        "axis": axis,
        "hour_axis": [f"{h:02d}" for h in range(24)],
        "systems": systems,
        "overall": systems[0]["total"] if systems else _row_metrics(None),
    }


# --------------------------------------------------------------------------
# 取数（页面「获取数据」按钮）
# --------------------------------------------------------------------------
class FetchRequest(BaseModel):
    dates: List[str] = Field(default_factory=list)
    date: str = ""
    product_code: Optional[int] = None
    sku: str = ""
    with_cross: bool = False


@router.post("/fetch")
def fetch_start(req: FetchRequest) -> Dict[str, Any]:
    """按「日期 × 广告产品」统一取数（后台任务，返回 task_id 供轮询）"""
    days = sorted({d.strip() for d in (list(req.dates) + ([req.date] if req.date else []))
                   if d.strip()})
    if not days:
        raise HTTPException(400, "缺少日期")
    if not req.product_code and not (req.sku or "").strip():
        raise HTTPException(400, "请先选择广告产品")
    tid = ads_fetch.start_fetch(days, product_code=req.product_code,
                                sku=(req.sku or "").strip(), with_cross=req.with_cross)
    return {"task_id": tid, "dates": days}


@router.get("/fetch/{task_id}")
def fetch_state(task_id: str) -> Dict[str, Any]:
    st = ads_fetch.get_task(task_id)
    if not st:
        raise HTTPException(404, "任务不存在")
    return st
