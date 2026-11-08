# -*- coding: utf-8 -*-
"""按天取数的落库层（SQLite）

表结构
    probe_run     一次取数任务（活动 + 区间 + 时间）
    probe_fact    长表事实：日期 × 层级 × 实体 × 维度 × 小时 × 指标
    probe_search  搜索词/搜索ASIN（日期 × 范围 × 组 × 词）

设计
    * 一行 = 一个「实体 × 维度 × 小时」的快照，另存 metrics_json 保留全部 72 项指标
    * `hour = -1` 表示当日汇总（便于直接取日级，不用 SUM）
    * 主键保证**重复落库幂等**（同一任务同一天可反复写）
    * 查询侧提供 overview()：还原成与 day_*.json 一致的结构，前端可直接复用渲染
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional

import config

DAY_JSON_DIR = os.path.join(config.DATA_DIR, "probe_daily")

CORE = ("impressions", "clicks", "adCost", "adOrderNum", "adSale",
        "addToCart", "detailPageViews")

SCHEMA = """
CREATE TABLE IF NOT EXISTS probe_run (
    task_id       TEXT PRIMARY KEY,
    campaign_id   TEXT,
    campaign_name TEXT,
    start_date    TEXT,
    end_date      TEXT,
    fetched_at    TEXT,
    options_json  TEXT,
    day_count     INTEGER,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS probe_fact (
    task_id      TEXT NOT NULL,
    campaign_id  TEXT,
    date         TEXT NOT NULL,
    level        TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    entity_name  TEXT,
    dim_type     TEXT NOT NULL,
    dim_code     TEXT NOT NULL DEFAULT '',
    hour         INTEGER NOT NULL DEFAULT -1,
    impressions  REAL DEFAULT 0,
    clicks       REAL DEFAULT 0,
    cost         REAL DEFAULT 0,
    orders       REAL DEFAULT 0,
    sales        REAL DEFAULT 0,
    add_to_cart  REAL DEFAULT 0,
    dpv          REAL DEFAULT 0,
    metrics_json TEXT,
    PRIMARY KEY (task_id, date, level, entity_id, dim_type, dim_code, hour)
);
CREATE INDEX IF NOT EXISTS idx_fact_q ON probe_fact (task_id, date, level, dim_type);

CREATE TABLE IF NOT EXISTS probe_search (
    task_id     TEXT NOT NULL,
    campaign_id TEXT,
    date        TEXT NOT NULL,
    scope       TEXT NOT NULL,
    group_id    TEXT NOT NULL DEFAULT '',
    query_type  TEXT NOT NULL,
    term        TEXT NOT NULL,
    match_type  TEXT,
    impressions REAL DEFAULT 0,
    clicks      REAL DEFAULT 0,
    cost        REAL DEFAULT 0,
    orders      REAL DEFAULT 0,
    add_to_cart REAL DEFAULT 0,
    extra_json  TEXT,
    PRIMARY KEY (task_id, date, scope, group_id, query_type, term)
);
CREATE INDEX IF NOT EXISTS idx_search_q ON probe_search (task_id, date, scope);

CREATE TABLE IF NOT EXISTS probe_competitor (
    task_id       TEXT NOT NULL,
    campaign_id   TEXT,
    term          TEXT NOT NULL,
    position      INTEGER,
    asin          TEXT NOT NULL,
    title         TEXT,
    price         REAL,
    list_price    REAL,
    rating        REAL,
    reviews       INTEGER,
    delivery      TEXT,
    free_shipping INTEGER,
    sponsored     INTEGER,
    fetched_at    TEXT,
    PRIMARY KEY (task_id, term, asin)
);
CREATE INDEX IF NOT EXISTS idx_comp_q ON probe_competitor (task_id, term);
"""

# 补充字段（老表通过 ALTER TABLE 增量迁移，不丢数据）
COMP_EXTRA_COLS = [
    ("image", "TEXT"),
    ("image_big", "TEXT"),
    ("url", "TEXT"),
    ("delivery_secondary", "TEXT"),
    ("badges", "TEXT"),
    ("bought_recently", "TEXT"),
    ("coupon", "TEXT"),
    ("discount", "TEXT"),
    ("points", "TEXT"),
    ("stock", "TEXT"),
    ("other_offers", "TEXT"),
    ("extras", "TEXT"),
    ("has_video", "INTEGER"),
    ("add_to_cart", "INTEGER"),
]


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:                       # noqa: BLE001
        return 0.0


def _core(metrics: Dict[str, Any]) -> Dict[str, float]:
    m = metrics or {}
    return {
        "impressions": _num(m.get("impressions")),
        "clicks": _num(m.get("clicks")),
        "cost": _num(m.get("adCost")),
        "orders": _num(m.get("adOrderNum")),
        "sales": _num(m.get("adSale")),
        "add_to_cart": _num(m.get("addToCart")),
        "dpv": _num(m.get("detailPageViews")),
    }


def _insert_fact(conn: sqlite3.Connection, task_id: str, cid: str, date: str,
                 level: str, entity_id: str, entity_name: str, dim_type: str,
                 dim_code: str, hour: int, metrics: Dict[str, Any]) -> None:
    c = _core(metrics)
    conn.execute(
        "INSERT OR REPLACE INTO probe_fact "
        "(task_id,campaign_id,date,level,entity_id,entity_name,dim_type,dim_code,hour,"
        " impressions,clicks,cost,orders,sales,add_to_cart,dpv,metrics_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (task_id, cid, date, level, str(entity_id), entity_name or "", dim_type,
         dim_code or "", hour, c["impressions"], c["clicks"], c["cost"], c["orders"],
         c["sales"], c["add_to_cart"], c["dpv"],
         json.dumps(metrics or {}, ensure_ascii=False)))


def _write_series(conn, task_id, cid, date, level, entity_id, entity_name,
                  dim_type, dim_code, hours: List[Dict[str, Any]],
                  sum_metrics: Dict[str, Any]) -> None:
    """写入 24 小时 + 当日汇总（hour=-1）"""
    for h in hours or []:
        raw = str(h.get("hour") or "0-1")
        try:
            hour = int(raw.split("-")[0])
        except Exception:                   # noqa: BLE001
            continue
        _insert_fact(conn, task_id, cid, date, level, entity_id, entity_name,
                     dim_type, dim_code, hour, h.get("metrics") or {})
    if sum_metrics:
        _insert_fact(conn, task_id, cid, date, level, entity_id, entity_name,
                     dim_type, dim_code, -1, sum_metrics)


def persist_day(conn: sqlite3.Connection, task_id: str, cid: str,
                data: Dict[str, Any]) -> int:
    """把一个 day_*.json 写入库，返回写入行数"""
    date = data.get("date")
    if not date:
        return 0
    n = 0

    def w(*args) -> None:
        nonlocal n
        _write_series(conn, task_id, cid, date, *args)
        n += 1

    camp = data.get("campaign") or {}
    w("campaign", cid, "广告活动", "total", "", camp.get("hours") or [], camp.get("sum") or {})
    for code, v in (camp.get("placements") or {}).items():
        w("campaign", cid, "广告活动", "placement", code, v.get("hours") or [], v.get("sum") or {})
    for tid, v in (camp.get("targets") or {}).items():
        w("campaign", cid, "广告活动", "target", tid,
          v.get("hours") or [], v.get("sum") or {})

    for g in data.get("groups") or []:
        gid = str(g.get("id") or "")
        gname = g.get("name") or gid
        w("group", gid, gname, "total", "", g.get("hours") or [], g.get("sum") or {})
        for code, v in (g.get("placements") or {}).items():
            w("group", gid, gname, "placement", code, v.get("hours") or [], v.get("sum") or {})
        for tid, v in (g.get("targets") or {}).items():
            w("group", gid, gname, "target", tid, v.get("hours") or [], v.get("sum") or {})

    for p in data.get("products") or []:
        asin = p.get("asin") or p.get("adId") or ""
        name = asin
        w("product", asin, name, "total", "", p.get("total") or [], p.get("sum") or {})
        for x in p.get("placements") or []:
            w("product", asin, name, "placement", x.get("code") or "",
              x.get("hours") or [], x.get("sum") or {})
        for x in p.get("match") or []:
            w("product", asin, name, "match", x.get("code") or "",
              x.get("hours") or [], x.get("sum") or {})

    # 搜索词 / 搜索ASIN
    s = data.get("search") or {}
    for qtype, key in (("keyword", "keyword"), ("asin", "asin")):
        for r in (s.get(key) or []):
            conn.execute(
                "INSERT OR REPLACE INTO probe_search "
                "(task_id,campaign_id,date,scope,group_id,query_type,term,match_type,"
                " impressions,clicks,cost,orders,add_to_cart,extra_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (task_id, cid, date, "campaign", "", qtype,
                 str(r.get("query") or ""), str(r.get("matchType") or ""),
                 _num(r.get("impressions")), _num(r.get("clicks")), _num(r.get("adCost")),
                 _num(r.get("adOrderNum")), _num(r.get("addToCart")),
                 json.dumps({k: r.get(k) for k in ("suggested", "price", "asinRating",
                                                   "ratingCount", "originalBid")},
                            ensure_ascii=False)))
        for gid, bucket in (s.get("byGroup") or {}).items():
            for r in (bucket.get(key) or []):
                conn.execute(
                    "INSERT OR REPLACE INTO probe_search "
                    "(task_id,campaign_id,date,scope,group_id,query_type,term,match_type,"
                    " impressions,clicks,cost,orders,add_to_cart,extra_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, cid, date, "group", str(gid), qtype,
                     str(r.get("query") or ""), str(r.get("matchType") or ""),
                     _num(r.get("impressions")), _num(r.get("clicks")), _num(r.get("adCost")),
                     _num(r.get("adOrderNum")), _num(r.get("addToCart")),
                     json.dumps({k: r.get(k) for k in ("suggested", "price", "asinRating",
                                                       "ratingCount", "originalBid")},
                                ensure_ascii=False)))
    return n


def persist_task(task_id: str) -> Dict[str, Any]:
    """把一个任务的全部 day_*.json 落库（幂等）"""
    day_dir = os.path.join(DAY_JSON_DIR, task_id)
    if not os.path.isdir(day_dir):
        raise FileNotFoundError(f"任务目录不存在: {day_dir}")

    meta: Dict[str, Any] = {}
    mp = os.path.join(day_dir, "meta.json")
    if os.path.exists(mp):
        meta = json.load(open(mp, encoding="utf-8")).get("meta") or {}

    # 只有 meta 缺失时才用目录名兜底
    cid = str(meta.get("campaignId") or "")
    days = sorted(f[4:-5] for f in os.listdir(day_dir)
                  if f.startswith("day_") and f.endswith(".json"))

    conn = get_conn()
    rows = 0
    try:
        init_db_tables(conn)
        conn.execute("DELETE FROM probe_fact WHERE task_id=?", (task_id,))
        conn.execute("DELETE FROM probe_search WHERE task_id=?", (task_id,))
        for d in days:
            data = json.load(open(os.path.join(day_dir, f"day_{d}.json"), encoding="utf-8"))
            if not cid:
                cid = str((meta.get("campaignId") or ""))
            rows += persist_day(conn, task_id, cid, data)
        conn.execute(
            "INSERT OR REPLACE INTO probe_run "
            "(task_id,campaign_id,campaign_name,start_date,end_date,fetched_at,"
            " options_json,day_count,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, cid, meta.get("campaignName") or "",
             meta.get("startDate") or (days[0] if days else ""),
             meta.get("endDate") or (days[-1] if days else ""),
             meta.get("fetchedAt") or "",
             json.dumps(meta.get("options") or {}, ensure_ascii=False),
             len(days), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    return {"taskId": task_id, "days": len(days), "seriesRows": rows}


def init_db_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrate_competitor(conn)


def _migrate_competitor(conn: sqlite3.Connection) -> None:
    """老表增量补列（ALTER TABLE，幂等）"""
    try:
        exist = {r[1] for r in conn.execute("PRAGMA table_info(probe_competitor)")}
    except Exception:                       # noqa: BLE001
        return
    for col, ctype in COMP_EXTRA_COLS:
        if col not in exist:
            try:
                conn.execute(f"ALTER TABLE probe_competitor ADD COLUMN {col} {ctype}")
            except Exception:               # noqa: BLE001
                pass
    conn.commit()


def list_runs() -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        init_db_tables(conn)
        rs = conn.execute(
            "SELECT * FROM probe_run ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rs]
    finally:
        conn.close()


def dates_of(task_id: str) -> List[str]:
    conn = get_conn()
    try:
        init_db_tables(conn)
        rs = conn.execute(
            "SELECT DISTINCT date FROM probe_fact WHERE task_id=? ORDER BY date",
            (task_id,)).fetchall()
        return [r["date"] for r in rs]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 查询：还原成 day_*.json 结构
# ---------------------------------------------------------------------------
def _row_metrics(r: sqlite3.Row) -> Dict[str, Any]:
    try:
        return json.loads(r["metrics_json"] or "{}")
    except Exception:                       # noqa: BLE001
        return {}


def _sum_of(conn, task_id, date, level, entity_id, dim_type, dim_code) -> Dict[str, Any]:
    r = conn.execute(
        "SELECT * FROM probe_fact WHERE task_id=? AND date=? AND level=? AND entity_id=?"
        " AND dim_type=? AND dim_code=? AND hour=-1",
        (task_id, date, level, entity_id, dim_type, dim_code)).fetchone()
    return _row_metrics(r) if r else {}


def _hours_of(conn, task_id, date, level, entity_id, dim_type, dim_code) -> List[Dict[str, Any]]:
    rs = conn.execute(
        "SELECT * FROM probe_fact WHERE task_id=? AND date=? AND level=? AND entity_id=?"
        " AND dim_type=? AND dim_code=? AND hour>=0 ORDER BY hour",
        (task_id, date, level, entity_id, dim_type, dim_code)).fetchall()
    return [{"hour": f"{r['hour']}-{r['hour'] + 1}", "metrics": _row_metrics(r)} for r in rs]


def overview(task_id: str, date: str) -> Dict[str, Any]:
    """还原成 {campaign:{hours,sum,placements,targets}, groups:[], products:[], search:{}}"""
    conn = get_conn()
    try:
        init_db_tables(conn)
        run = conn.execute("SELECT * FROM probe_run WHERE task_id=?", (task_id,)).fetchone()
        cid = run["campaign_id"] if run else ""

        def dims(level: str, entity_id: str) -> Dict[str, List[Dict[str, Any]]]:
            rs = conn.execute(
                "SELECT DISTINCT dim_type, dim_code FROM probe_fact"
                " WHERE task_id=? AND date=? AND level=? AND entity_id=? AND dim_type<>'total'",
                (task_id, date, level, entity_id)).fetchall()
            out: Dict[str, List[Dict[str, Any]]] = {"placement": [], "target": [], "match": []}
            for r in rs:
                dt, dc = r["dim_type"], r["dim_code"]
                out.setdefault(dt, []).append({"code": dc})
            return out

        camp = {
            "hours": _hours_of(conn, task_id, date, "campaign", cid, "total", ""),
            "sum": _sum_of(conn, task_id, date, "campaign", cid, "total", ""),
            "placements": {}, "targets": {},
        }
        for d in dims("campaign", cid).get("placement") or []:
            code = d["code"]
            camp["placements"][code] = {
                "label": code,
                "sum": _sum_of(conn, task_id, date, "campaign", cid, "placement", code),
                "hours": _hours_of(conn, task_id, date, "campaign", cid, "placement", code)}
        for d in dims("campaign", cid).get("target") or []:
            tid = d["code"]
            camp["targets"][tid] = {
                "label": tid,
                "sum": _sum_of(conn, task_id, date, "campaign", cid, "target", tid),
                "hours": _hours_of(conn, task_id, date, "campaign", cid, "target", tid)}

        groups = []
        for g in conn.execute(
                "SELECT DISTINCT entity_id, entity_name FROM probe_fact"
                " WHERE task_id=? AND date=? AND level='group'", (task_id, date)).fetchall():
            gid = g["entity_id"]
            item = {"id": gid, "name": g["entity_name"],
                    "hours": _hours_of(conn, task_id, date, "group", gid, "total", ""),
                    "sum": _sum_of(conn, task_id, date, "group", gid, "total", ""),
                    "placements": {}, "targets": {}}
            for d in dims("group", gid).get("placement") or []:
                code = d["code"]
                item["placements"][code] = {
                    "label": code,
                    "sum": _sum_of(conn, task_id, date, "group", gid, "placement", code),
                    "hours": _hours_of(conn, task_id, date, "group", gid, "placement", code)}
            for d in dims("group", gid).get("target") or []:
                tid = d["code"]
                item["targets"][tid] = {
                    "label": tid,
                    "sum": _sum_of(conn, task_id, date, "group", gid, "target", tid),
                    "hours": _hours_of(conn, task_id, date, "group", gid, "target", tid)}
            groups.append(item)

        products = []
        for p in conn.execute(
                "SELECT DISTINCT entity_id FROM probe_fact"
                " WHERE task_id=? AND date=? AND level='product'", (task_id, date)).fetchall():
            asin = p["entity_id"]
            item = {"asin": asin, "sku": "",
                    "total": _hours_of(conn, task_id, date, "product", asin, "total", ""),
                    "sum": _sum_of(conn, task_id, date, "product", asin, "total", ""),
                    "placements": [], "match": []}
            for d in dims("product", asin).get("placement") or []:
                code = d["code"]
                item["placements"].append({
                    "code": code, "label": code,
                    "sum": _sum_of(conn, task_id, date, "product", asin, "placement", code),
                    "hours": _hours_of(conn, task_id, date, "product", asin, "placement", code)})
            for d in dims("product", asin).get("match") or []:
                code = d["code"]
                item["match"].append({
                    "code": code, "label": code,
                    "sum": _sum_of(conn, task_id, date, "product", asin, "match", code),
                    "hours": _hours_of(conn, task_id, date, "product", asin, "match", code)})
            products.append(item)

        def search_rows(scope: str, gid: str, qtype: str) -> List[Dict[str, Any]]:
            rs = conn.execute(
                "SELECT * FROM probe_search WHERE task_id=? AND date=? AND scope=?"
                " AND group_id=? AND query_type=? ORDER BY cost DESC",
                (task_id, date, scope, gid, qtype)).fetchall()
            out = []
            for r in rs:
                extra = {}
                try:
                    extra = json.loads(r["extra_json"] or "{}")
                except Exception:           # noqa: BLE001
                    pass
                out.append({"query": r["term"], "matchType": r["match_type"],
                            "impressions": r["impressions"], "clicks": r["clicks"],
                            "adCost": r["cost"], "adOrderNum": r["orders"],
                            "addToCart": r["add_to_cart"], **extra})
            return out

        by_group: Dict[str, Any] = {}
        for g in conn.execute(
                "SELECT DISTINCT group_id FROM probe_search WHERE task_id=? AND date=?"
                " AND scope='group'", (task_id, date)).fetchall():
            gid = g["group_id"]
            by_group[gid] = {"keyword": search_rows("group", gid, "keyword"),
                             "asin": search_rows("group", gid, "asin")}

        return {
            "date": date,
            "campaign": camp,
            "groups": groups,
            "products": products,
            "search": {"keyword": search_rows("campaign", "", "keyword"),
                       "asin": search_rows("campaign", "", "asin"),
                       "byGroup": by_group},
        }
    finally:
        conn.close()


def daily_trend(task_id: str, level: str = "campaign", entity_id: str = "",
                dim_type: str = "total", dim_code: str = "") -> List[Dict[str, Any]]:
    """跨天趋势（hour=-1 的日汇总）"""
    conn = get_conn()
    try:
        init_db_tables(conn)
        sql = ("SELECT date, impressions, clicks, cost, orders, sales, add_to_cart "
               "FROM probe_fact WHERE task_id=? AND level=? AND dim_type=? AND hour=-1")
        args: List[Any] = [task_id, level, dim_type]
        if entity_id:
            sql += " AND entity_id=?"
            args.append(entity_id)
        if dim_code:
            sql += " AND dim_code=?"
            args.append(dim_code)
        sql += " ORDER BY date, entity_id"
        rs = conn.execute(sql, args).fetchall()
        agg: Dict[str, Dict[str, float]] = {}
        for r in rs:
            d = agg.setdefault(r["date"], {"impressions": 0, "clicks": 0, "cost": 0,
                                           "orders": 0, "sales": 0, "add_to_cart": 0})
            for k in ("impressions", "clicks", "cost", "orders", "sales", "add_to_cart"):
                d[k] += r[k] or 0
        return [{"date": d, **{k: round(v, 2) for k, v in m.items()}}
                for d, m in sorted(agg.items())]
    finally:
        conn.close()


def hourly_avg(task_id: str, level: str = "campaign", entity_id: str = "",
               dim_type: str = "total", dim_code: str = "") -> List[Dict[str, Any]]:
    """跨天按小时聚合（找最烧钱的时段）"""
    conn = get_conn()
    try:
        init_db_tables(conn)
        sql = ("SELECT hour, SUM(impressions) i, SUM(clicks) c, SUM(cost) s,"
               " SUM(orders) o, COUNT(DISTINCT date) d FROM probe_fact"
               " WHERE task_id=? AND level=? AND dim_type=? AND hour>=0")
        args: List[Any] = [task_id, level, dim_type]
        if entity_id:
            sql += " AND entity_id=?"
            args.append(entity_id)
        if dim_code:
            sql += " AND dim_code=?"
            args.append(dim_code)
        sql += " GROUP BY hour ORDER BY hour"
        rs = conn.execute(sql, args).fetchall()
        return [{"hour": f"{r['hour']}-{r['hour'] + 1}", "impressions": r["i"] or 0,
                 "clicks": r["c"] or 0, "cost": round(r["s"] or 0, 2),
                 "orders": r["o"] or 0, "days": r["d"] or 0} for r in rs]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 竞品（按词一次，全量记录）
# ---------------------------------------------------------------------------
def persist_competitor(task_id: str, cid: str, comp: Dict[str, List[Dict[str, Any]]],
                       fetched_at: str = "") -> Dict[str, Any]:
    """写入竞品全量记录（幂等：先删该词的旧记录再插）"""
    conn = get_conn()
    terms, rows = 0, 0
    try:
        init_db_tables(conn)
        for term, items in (comp or {}).items():
            if not items:
                continue
            conn.execute("DELETE FROM probe_competitor WHERE task_id=? AND term=?",
                         (task_id, term))
            terms += 1
            for x in items:
                asin = str(x.get("asin") or "")
                if not asin:
                    continue
                # 原始抓取字段是 price_value / rating_value / review_count / delivery_date，
                # 也兼容已归一化的 price / rating / reviews / delivery
                price = x.get("price_value", x.get("price"))
                rating = x.get("rating_value", x.get("rating"))
                reviews = x.get("review_count", x.get("reviews"))
                conn.execute(
                    "INSERT OR REPLACE INTO probe_competitor "
                    "(task_id,campaign_id,term,position,asin,title,price,list_price,"
                    " rating,reviews,delivery,free_shipping,sponsored,fetched_at,"
                    " image,image_big,url,delivery_secondary,badges,bought_recently,"
                    " coupon,discount,points,stock,other_offers,extras,has_video,add_to_cart) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (task_id, cid, term, x.get("position"), asin,
                     (x.get("title") or "")[:200],
                     _or_none(price), _or_none(x.get("list_price")),
                     _or_none(rating), int(reviews) if str(reviews or "").isdigit() else None,
                     (x.get("delivery_date") or x.get("delivery") or "")[:40],
                     1 if x.get("free_shipping") else 0,
                     1 if x.get("sponsored") else 0,
                     fetched_at or "",
                     _txt(x.get("image")), _txt(x.get("image_big")), _txt(x.get("url")),
                     _txt(x.get("delivery_secondary"))[:80],
                     _txt(x.get("badges"))[:120], _txt(x.get("bought_recently"))[:60],
                     _txt(x.get("coupon"))[:60], _txt(x.get("discount"))[:60],
                     _txt(x.get("points"))[:40], _txt(x.get("stock"))[:40],
                     _txt(x.get("other_offers"))[:60], _txt(x.get("extras"))[:200],
                     1 if x.get("has_video") else 0,
                     1 if x.get("add_to_cart") else 0))
                rows += 1
        conn.commit()
    finally:
        conn.close()
    return {"taskId": task_id, "terms": terms, "rows": rows}


def _txt(v: Any) -> str:
    """把 list/dict/None 统一成短文本（badges / extras 之类）"""
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        parts = []
        for x in v:
            if isinstance(x, dict):
                parts.append(str(x.get("text") or x.get("tag") or "").strip())
            else:
                parts.append(str(x).strip())
        return " | ".join([p for p in parts if p])
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)[:200]
    return str(v).strip()


def _or_none(v: Any) -> Optional[float]:
    try:
        return float(str(v).replace(",", "").replace("¥", "").replace("￥", ""))
    except Exception:                       # noqa: BLE001
        return None


def competitor_terms(task_id: str) -> List[Dict[str, Any]]:
    """每个词的概览：条数 / 价格中位 / 最低 / 最高 / 均评论"""
    conn = get_conn()
    try:
        init_db_tables(conn)
        rs = conn.execute(
            "SELECT term, COUNT(*) n, AVG(price) avg_price, MIN(price) min_price,"
            " MAX(price) max_price, AVG(reviews) avg_rev, MAX(fetched_at) fetched_at"
            " FROM probe_competitor WHERE task_id=? GROUP BY term ORDER BY n DESC",
            (task_id,)).fetchall()
        out = []
        for r in rs:
            prices = [x[0] for x in conn.execute(
                "SELECT price FROM probe_competitor WHERE task_id=? AND term=?"
                " AND price IS NOT NULL ORDER BY price", (task_id, r["term"])).fetchall()]
            med = prices[len(prices) // 2] if prices else None
            out.append({"term": r["term"], "count": r["n"],
                        "median": round(med, 2) if med else None,
                        "min": r["min_price"], "max": r["max_price"],
                        "avgReviews": round(r["avg_rev"] or 0, 1),
                        "fetchedAt": r["fetched_at"] or ""})
        return out
    finally:
        conn.close()


def competitor_items(task_id: str, term: str) -> List[Dict[str, Any]]:
    conn = get_conn()
    try:
        init_db_tables(conn)
        rs = conn.execute(
            "SELECT * FROM probe_competitor WHERE task_id=? AND term=?"
            " ORDER BY position", (task_id, term)).fetchall()
        return [dict(r) for r in rs]
    finally:
        conn.close()
