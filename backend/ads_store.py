# -*- coding: utf-8 -*-
"""广告数据仓库 v2 —— 实体中心化存储

设计目标
    不再以「取数任务」为主键，而是按
        **实体（活动/组/产品/投放）× 日期 × 小时 × 维度**
    存一份；重复取同一天用 UPSERT 覆盖，天然幂等。

表
    维度  dim_campaign / dim_group / dim_product / dim_target / dim_search_term
    事实  fact_campaign / fact_group / fact_product / fact_target /
          fact_search_term（按日）/ fact_competitor（按词，全局一份）
    视图  v_campaign_daily / v_group_daily / v_product_daily / v_campaign_hourly
    日志  fetch_log（只审计，不参与查询）

约定
    hour = -1        当日汇总
    hour 0~23        分时
    placement ''     合计（非广告位拆分）
    system           total / placement / match（产品三体系）
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config

NOW = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")   # noqa: E731

DEFAULT_SHOP_ID = "711811"

METRIC_COLS = ["impressions", "clicks", "cost", "orders", "sales",
               "add_to_cart", "dpv", "metrics_json", "updated_at"]

DDL = """
-- ============ 维度 ============
CREATE TABLE IF NOT EXISTS dim_campaign (
  campaign_id TEXT PRIMARY KEY,
  inner_id TEXT, name TEXT, shop_id TEXT, marketplace_id TEXT,
  targeting_type TEXT, portfolio_id TEXT, portfolio_name TEXT,
  status TEXT, daily_budget REAL, strategy TEXT,
  placement_top REAL, placement_rest REAL, placement_product REAL,
  start_date TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS dim_group (
  group_id TEXT PRIMARY KEY,          -- 赛狐内部 id（分时接口用）
  amazon_group_id TEXT,               -- Amazon adGroupId（投放/搜索词用）
  campaign_id TEXT, name TEXT, default_bid REAL, status TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_g_camp ON dim_group (campaign_id);

CREATE TABLE IF NOT EXISTS dim_product (
  asin TEXT PRIMARY KEY, sku TEXT, parent_asin TEXT, title TEXT,
  price REAL, landed_price REAL, fulfillment TEXT, online_status TEXT,
  rating REAL, rating_count INTEGER, quantity INTEGER, updated_at TEXT
);

-- 活动投放产品映射：产品事实未采集时，仍可用于搜索数据的 ASIN/SKU 展示。
CREATE TABLE IF NOT EXISTS dim_campaign_product (
  campaign_id TEXT NOT NULL, asin TEXT NOT NULL, sku TEXT, parent_asin TEXT,
  updated_at TEXT, PRIMARY KEY (campaign_id, asin)
);
CREATE INDEX IF NOT EXISTS idx_cp_camp ON dim_campaign_product (campaign_id);

CREATE TABLE IF NOT EXISTS dim_target (
  target_id TEXT PRIMARY KEY,
  group_id TEXT, campaign_id TEXT,
  target_type TEXT, target_text TEXT, match_type TEXT, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_t_camp ON dim_target (campaign_id);

CREATE TABLE IF NOT EXISTS dim_search_term (
  term TEXT PRIMARY KEY, term_cn TEXT,
  first_seen TEXT, last_seen TEXT, seen_days INTEGER DEFAULT 0,
  aba_week_rank INTEGER, aba_month_rank INTEGER, updated_at TEXT
);

-- ============ 事实 ============
CREATE TABLE IF NOT EXISTS fact_campaign (
  campaign_id TEXT NOT NULL, date TEXT NOT NULL,
  hour INTEGER NOT NULL, placement TEXT NOT NULL DEFAULT '',
  impressions REAL, clicks REAL, cost REAL, orders REAL, sales REAL,
  add_to_cart REAL, dpv REAL, metrics_json TEXT, updated_at TEXT,
  PRIMARY KEY (campaign_id, date, hour, placement)
);

CREATE TABLE IF NOT EXISTS fact_group (
  group_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
  date TEXT NOT NULL, hour INTEGER NOT NULL, placement TEXT NOT NULL DEFAULT '',
  impressions REAL, clicks REAL, cost REAL, orders REAL, sales REAL,
  add_to_cart REAL, dpv REAL, metrics_json TEXT, updated_at TEXT,
  PRIMARY KEY (group_id, date, hour, placement)
);
CREATE INDEX IF NOT EXISTS idx_fg_camp ON fact_group (campaign_id, date);

CREATE TABLE IF NOT EXISTS fact_product (
  asin TEXT NOT NULL, campaign_id TEXT NOT NULL, group_id TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL, hour INTEGER NOT NULL,
  system TEXT NOT NULL, code TEXT NOT NULL DEFAULT '',
  impressions REAL, clicks REAL, cost REAL, orders REAL, sales REAL,
  add_to_cart REAL, dpv REAL, metrics_json TEXT, updated_at TEXT,
  PRIMARY KEY (asin, campaign_id, date, hour, system, code)
);
CREATE INDEX IF NOT EXISTS idx_fp_camp ON fact_product (campaign_id, date);
CREATE INDEX IF NOT EXISTS idx_fp_asin ON fact_product (asin, date);

CREATE TABLE IF NOT EXISTS fact_target (
  target_id TEXT NOT NULL, campaign_id TEXT NOT NULL, group_id TEXT,
  date TEXT NOT NULL, hour INTEGER NOT NULL,
  impressions REAL, clicks REAL, cost REAL, orders REAL, sales REAL,
  add_to_cart REAL, dpv REAL, metrics_json TEXT, updated_at TEXT,
  PRIMARY KEY (target_id, date, hour)
);
CREATE INDEX IF NOT EXISTS idx_ft_camp ON fact_target (campaign_id, date);
CREATE INDEX IF NOT EXISTS idx_ft_group ON fact_target (group_id, date);

CREATE TABLE IF NOT EXISTS fact_search_term (
  date TEXT NOT NULL, scope TEXT NOT NULL, owner_id TEXT NOT NULL,
  campaign_id TEXT NOT NULL DEFAULT '',
  query_type TEXT NOT NULL, term TEXT NOT NULL, match_type TEXT NOT NULL DEFAULT '',
  impressions REAL, clicks REAL, cost REAL, orders REAL, add_to_cart REAL,
  extra_json TEXT, updated_at TEXT,
  PRIMARY KEY (date, scope, owner_id, query_type, term, match_type)
);
CREATE INDEX IF NOT EXISTS idx_st_owner ON fact_search_term (owner_id, date);
CREATE INDEX IF NOT EXISTS idx_st_camp ON fact_search_term (campaign_id, date);
CREATE INDEX IF NOT EXISTS idx_st_term ON fact_search_term (term);

CREATE TABLE IF NOT EXISTS fact_competitor (
  term TEXT NOT NULL, asin TEXT NOT NULL,
  position INTEGER, title TEXT,
  price REAL, list_price REAL, rating REAL, reviews INTEGER,
  delivery TEXT, fastest_delivery TEXT,
  badges TEXT, coupon TEXT, discount TEXT, points TEXT, stock TEXT,
  bought_recently TEXT, image TEXT, url TEXT,
  sponsored INTEGER, has_video INTEGER, fetched_at TEXT,
  PRIMARY KEY (term, asin)
);

CREATE TABLE IF NOT EXISTS fetch_log (
  batch_id TEXT PRIMARY KEY, entity_type TEXT, entity_id TEXT,
  date TEXT, started_at TEXT, finished_at TEXT,
  status TEXT, requests INTEGER, errors_json TEXT
);

-- ============ 视图 ============
CREATE VIEW IF NOT EXISTS v_campaign_daily AS
SELECT * FROM fact_campaign WHERE hour = -1 AND placement = '';

CREATE VIEW IF NOT EXISTS v_group_daily AS
SELECT * FROM fact_group WHERE hour = -1 AND placement = '';

CREATE VIEW IF NOT EXISTS v_product_daily AS
SELECT * FROM fact_product WHERE hour = -1;

CREATE VIEW IF NOT EXISTS v_campaign_hourly AS
SELECT * FROM fact_campaign WHERE hour >= 0 AND placement = '';
"""

FACT_COLS = ["impressions", "clicks", "cost", "orders", "sales",
             "add_to_cart", "dpv", "metrics_json", "updated_at"]


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def init(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.executescript(DDL)
        conn.commit()
    finally:
        if own:
            conn.close()


_COLS_CACHE: Dict[Tuple[int, str], set] = {}


def _table_cols(conn: sqlite3.Connection, table: str) -> set:
    key = (id(conn), table)
    if key not in _COLS_CACHE:
        _COLS_CACHE[key] = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return _COLS_CACHE[key]


def upsert(conn: sqlite3.Connection, table: str, data: Dict[str, Any],
           pk: Sequence[str]) -> None:
    # 只保留目标表真实存在的列（迁移时源表可能有多余字段）
    valid = _table_cols(conn, table)
    data = {k: v for k, v in data.items() if k in valid}
    cols = [c for c in data if data[c] is not None or c not in pk]
    placeholders = ",".join("?" * len(cols))
    sets = ",".join(f"{c}=excluded.{c}" for c in cols if c not in pk)
    sql = (f"INSERT INTO {table} ({','.join(cols)}) VALUES ({placeholders}) "
           f"ON CONFLICT({','.join(pk)}) DO UPDATE SET {sets}"
           if sets else
           f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})")
    conn.execute(sql, [data[c] for c in cols])


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", "").replace("%", ""))
    except Exception:                       # noqa: BLE001
        return 0.0


def fact_row(metrics: Dict[str, Any], **keys: Any) -> Dict[str, Any]:
    m = metrics or {}
    row = dict(keys)
    row.update({
        "impressions": _num(m.get("impressions")),
        "clicks": _num(m.get("clicks")),
        "cost": _num(m.get("adCost")),
        "orders": _num(m.get("adOrderNum")),
        "sales": _num(m.get("adSale")),
        "add_to_cart": _num(m.get("addToCart")),
        "dpv": _num(m.get("detailPageViews")),
        "metrics_json": json.dumps(m, ensure_ascii=False) if m else "",
        "updated_at": NOW(),
    })
    return row


def _hour_of(raw: Any) -> int:
    try:
        return int(str(raw).split("-")[0])
    except Exception:                       # noqa: BLE001
        return -1


# ---------------------------------------------------------------------------
# 写入（供采集流程调用）
# ---------------------------------------------------------------------------
def write_campaign(conn, campaign_id: str, date: str,
                   hours: List[Dict[str, Any]], sums: Dict[str, Any],
                   placements: Optional[Dict[str, Any]] = None,
                   updated_at: str = "") -> None:
    """活动：24 小时 + 当日汇总 + 各广告位"""
    for h in hours or []:
        r = fact_row(h.get("metrics") or {}, campaign_id=campaign_id, date=date,
                     hour=_hour_of(h.get("hour")), placement="")
        upsert(conn, "fact_campaign", r, ("campaign_id", "date", "hour", "placement"))
    if sums:
        r = fact_row(sums, campaign_id=campaign_id, date=date, hour=-1, placement="")
        upsert(conn, "fact_campaign", r, ("campaign_id", "date", "hour", "placement"))
    for code, v in (placements or {}).items():
        for h in v.get("hours") or []:
            r = fact_row(h.get("metrics") or {}, campaign_id=campaign_id, date=date,
                         hour=_hour_of(h.get("hour")), placement=code)
            upsert(conn, "fact_campaign", r, ("campaign_id", "date", "hour", "placement"))
        if v.get("sum"):
            r = fact_row(v["sum"], campaign_id=campaign_id, date=date,
                         hour=-1, placement=code)
            upsert(conn, "fact_campaign", r, ("campaign_id", "date", "hour", "placement"))


def write_group(conn, group_id: str, campaign_id: str, date: str,
                hours: List[Dict[str, Any]], sums: Dict[str, Any],
                placements: Optional[Dict[str, Any]] = None) -> None:
    for h in hours or []:
        r = fact_row(h.get("metrics") or {}, group_id=group_id,
                     campaign_id=campaign_id, date=date,
                     hour=_hour_of(h.get("hour")), placement="")
        upsert(conn, "fact_group", r, ("group_id", "date", "hour", "placement"))
    if sums:
        r = fact_row(sums, group_id=group_id, campaign_id=campaign_id,
                     date=date, hour=-1, placement="")
        upsert(conn, "fact_group", r, ("group_id", "date", "hour", "placement"))
    for code, v in (placements or {}).items():
        for h in v.get("hours") or []:
            r = fact_row(h.get("metrics") or {}, group_id=group_id,
                         campaign_id=campaign_id, date=date,
                         hour=_hour_of(h.get("hour")), placement=code)
            upsert(conn, "fact_group", r, ("group_id", "date", "hour", "placement"))
        if v.get("sum"):
            r = fact_row(v["sum"], group_id=group_id, campaign_id=campaign_id,
                         date=date, hour=-1, placement=code)
            upsert(conn, "fact_group", r, ("group_id", "date", "hour", "placement"))


def write_product(conn, asin: str, campaign_id: str, group_id: str, date: str,
                  total_hours: List[Dict[str, Any]], total_sum: Dict[str, Any],
                  placements: List[Dict[str, Any]],
                  matches: List[Dict[str, Any]]) -> None:
    for h in total_hours or []:
        r = fact_row(h.get("metrics") or {}, asin=asin, campaign_id=campaign_id,
                     group_id=group_id, date=date, hour=_hour_of(h.get("hour")),
                     system="total", code="")
        upsert(conn, "fact_product", r,
               ("asin", "campaign_id", "date", "hour", "system", "code"))
    if total_sum:
        r = fact_row(total_sum, asin=asin, campaign_id=campaign_id, group_id=group_id,
                     date=date, hour=-1, system="total", code="")
        upsert(conn, "fact_product", r,
               ("asin", "campaign_id", "date", "hour", "system", "code"))
    for system, rows in (("placement", placements or []), ("match", matches or [])):
        for x in rows:
            code = x.get("code") or ""
            for h in x.get("hours") or []:
                r = fact_row(h.get("metrics") or {}, asin=asin, campaign_id=campaign_id,
                             group_id=group_id, date=date, hour=_hour_of(h.get("hour")),
                             system=system, code=code)
                upsert(conn, "fact_product", r,
                       ("asin", "campaign_id", "date", "hour", "system", "code"))
            if x.get("sum"):
                r = fact_row(x["sum"], asin=asin, campaign_id=campaign_id,
                             group_id=group_id, date=date, hour=-1,
                             system=system, code=code)
                upsert(conn, "fact_product", r,
                       ("asin", "campaign_id", "date", "hour", "system", "code"))


def write_targets(conn, campaign_id: str, group_id: str, date: str,
                  targets: Dict[str, Any]) -> None:
    for tid, v in (targets or {}).items():
        # 采集到投放文本时同步维护维度表，避免后续只显示 targetId。
        label = str(v.get("label") or "")
        if label and not label.isdigit():
            upsert_target(
                conn, str(tid), campaign_id=campaign_id, group_id=group_id,
                target_type=str(v.get("type") or ""), target_text=label,
                match_type=str(v.get("match_type") or ""))
        for h in v.get("hours") or []:
            r = fact_row(h.get("metrics") or {}, target_id=tid,
                         campaign_id=campaign_id, group_id=group_id,
                         date=date, hour=_hour_of(h.get("hour")))
            upsert(conn, "fact_target", r, ("target_id", "date", "hour"))
        if v.get("sum"):
            r = fact_row(v["sum"], target_id=tid, campaign_id=campaign_id,
                         group_id=group_id, date=date, hour=-1)
            upsert(conn, "fact_target", r, ("target_id", "date", "hour"))


def write_search_terms(conn, campaign_id: str, date: str,
                       campaign_rows: List[Dict[str, Any]],
                       by_group: Dict[str, Any]) -> None:
    def put(rows, scope: str, owner: str, qtype: str) -> None:
        for r in rows or []:
            term = str(r.get("query") or "").strip()
            if not term:
                continue
            row = {
                "date": date, "scope": scope, "owner_id": owner,
                "campaign_id": campaign_id, "query_type": qtype, "term": term,
                "match_type": str(r.get("matchType") or ""),
                "impressions": _num(r.get("impressions")),
                "clicks": _num(r.get("clicks")),
                "cost": _num(r.get("adCost")),
                "orders": _num(r.get("adOrderNum")),
                "add_to_cart": _num(r.get("addToCart")),
                "extra_json": json.dumps({k: r.get(k) for k in
                                          ("suggested", "price", "asinRating",
                                           "ratingCount", "originalBid")},
                                         ensure_ascii=False),
                "updated_at": NOW(),
            }
            upsert(conn, "fact_search_term", row,
                   ("date", "scope", "owner_id", "query_type", "term", "match_type"))
            touch_term(conn, term, date)

    # campaign 维度的搜索词在调用方按维度拆开传，这里只处理通配：rows 已经是过滤好的
    put(campaign_rows, "campaign", campaign_id, "keyword")
    for gid, bucket in (by_group or {}).items():
        put(bucket.get("keyword") or [], "group", str(gid), "keyword")


def touch_term(conn, term: str, date: str, cn: str = "") -> None:
    """维护广告词主表：首末见日期 + 累计天数"""
    row = conn.execute("SELECT first_seen,last_seen,seen_days FROM dim_search_term"
                       " WHERE term=?", (term,)).fetchone()
    if not row:
        conn.execute("INSERT INTO dim_search_term"
                     "(term,term_cn,first_seen,last_seen,seen_days,updated_at)"
                     " VALUES (?,?,?,?,1,?)", (term, cn, date, date, NOW()))
    else:
        first = min(row["first_seen"] or date, date)
        last = max(row["last_seen"] or date, date)
        # seen_days 用「出现过的不同日期数」更准，这里增量 +1 仅当新日期
        days = row["seen_days"] or 0
        if date not in (row["first_seen"], row["last_seen"]):
            days += 1
        conn.execute("UPDATE dim_search_term SET first_seen=?,last_seen=?,seen_days=?,"
                     "updated_at=? WHERE term=?", (first, last, days, NOW(), term))


def write_competitor(conn, comp: Dict[str, List[Dict[str, Any]]]) -> int:
    n = 0
    for term, items in (comp or {}).items():
        for x in items or []:
            asin = str(x.get("asin") or "")
            if not asin:
                continue
            upsert(conn, "fact_competitor", {
                "term": term, "asin": asin,
                "position": x.get("position"), "title": (x.get("title") or "")[:200],
                "price": _num(x.get("price_value", x.get("price"))) or None,
                "list_price": _num(x.get("list_price")) or None,
                "rating": _num(x.get("rating_value", x.get("rating"))) or None,
                "reviews": x.get("review_count"),
                "delivery": (x.get("delivery_date") or "")[:40],
                "fastest_delivery": (x.get("delivery_secondary") or "")[:80],
                "badges": _join(x.get("badges")),
                "coupon": (x.get("coupon") or "")[:60],
                "discount": (x.get("discount") or "")[:60],
                "points": (x.get("points") or "")[:40],
                "stock": (x.get("stock") or "")[:40],
                "bought_recently": (x.get("bought_recently") or "")[:60],
                "image": x.get("image") or "",
                "url": x.get("url") or "",
                "sponsored": 1 if x.get("sponsored") else 0,
                "has_video": 1 if x.get("has_video") else 0,
                "fetched_at": NOW(),
            }, ("term", "asin"))
            n += 1
    return n


def _join(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " | ".join(str(x) for x in v if x)
    return str(v or "")[:120]


# ---------------------------------------------------------------------------
# 维度维护
# ---------------------------------------------------------------------------
def upsert_campaign(conn, **kw) -> None:
    kw.setdefault("updated_at", NOW())
    kw = {k: v for k, v in kw.items() if v is not None}
    if "campaign_id" not in kw:
        return
    upsert(conn, "dim_campaign", kw, ("campaign_id",))


def upsert_group(conn, group_id: str, **kw) -> None:
    kw.update({"group_id": group_id, "updated_at": NOW()})
    upsert(conn, "dim_group", {k: v for k, v in kw.items() if v is not None},
           ("group_id",))


def upsert_product(conn, asin: str, **kw) -> None:
    kw.update({"asin": asin, "updated_at": NOW()})
    upsert(conn, "dim_product", {k: v for k, v in kw.items() if v is not None},
           ("asin",))


def upsert_campaign_product(conn, campaign_id: str, asin: str, **kw) -> None:
    """记录活动实际投放的产品；不依赖产品事实是否已采集。"""
    if not campaign_id or not asin:
        return
    kw.update({"campaign_id": str(campaign_id), "asin": str(asin), "updated_at": NOW()})
    upsert(conn, "dim_campaign_product",
           {k: v for k, v in kw.items() if v is not None},
           ("campaign_id", "asin"))


def upsert_target(conn, target_id: str, **kw) -> None:
    kw.update({"target_id": target_id, "updated_at": NOW()})
    upsert(conn, "dim_target", {k: v for k, v in kw.items() if v is not None},
           ("target_id",))


# ---------------------------------------------------------------------------
# 迁移：probe_* → v2
# ---------------------------------------------------------------------------
def migrate_from_probe(conn: Optional[sqlite3.Connection] = None,
                       verbose: bool = True) -> Dict[str, Any]:
    """把旧的 task 中心数据搬到新结构（幂等，可重复执行）"""
    own = conn is None
    conn = conn or get_conn()
    stats = {"fact_campaign": 0, "fact_group": 0, "fact_product": 0,
             "fact_target": 0, "fact_search_term": 0, "fact_competitor": 0,
             "dim_search_term": 0}
    try:
        init(conn)
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "probe_fact" not in names:
            return stats

        # ---- 事实：probe_fact ----
        rows = conn.execute("SELECT * FROM probe_fact").fetchall()
        for r in rows:
            metrics = json.loads(r["metrics_json"] or "{}")
            level, dim_type, code = r["level"], r["dim_type"], r["dim_code"] or ""
            cid, eid, date, hour = (r["campaign_id"] or "", r["entity_id"],
                                    r["date"], int(r["hour"]))
            if level == "campaign":
                if dim_type in ("total", "placement"):
                    data = fact_row(metrics, campaign_id=cid, date=date, hour=hour,
                                    placement="" if dim_type == "total" else code)
                    upsert(conn, "fact_campaign", data,
                           ("campaign_id", "date", "hour", "placement"))
                elif dim_type == "target":
                    data = fact_row(metrics, target_id=code, campaign_id=cid,
                                    group_id="", date=date, hour=hour)
                    upsert(conn, "fact_target", data, ("target_id", "date", "hour"))
                    stats["fact_target"] += 1
            elif level == "group":
                gid = eid
                if dim_type in ("total", "placement"):
                    data = fact_row(metrics, group_id=gid, campaign_id=cid, date=date,
                                    hour=hour,
                                    placement="" if dim_type == "total" else code)
                    upsert(conn, "fact_group", data,
                           ("group_id", "date", "hour", "placement"))
                elif dim_type == "target":
                    data = fact_row(metrics, target_id=code, campaign_id=cid,
                                    group_id=gid, date=date, hour=hour)
                    upsert(conn, "fact_target", data, ("target_id", "date", "hour"))
                    stats["fact_target"] += 1
            elif level == "product":
                system = {"total": "total", "placement": "placement",
                          "match": "match"}.get(dim_type)
                if not system:
                    continue
                data = fact_row(metrics, asin=eid, campaign_id=cid, group_id="",
                                date=date, hour=hour, system=system,
                                code="" if system == "total" else code)
                upsert(conn, "fact_product", data,
                       ("asin", "campaign_id", "date", "hour", "system", "code"))

        for t in ("fact_campaign", "fact_group", "fact_product", "fact_target"):
            stats[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]

        # ---- 事实：probe_search ----
        if "probe_search" in names:
            for r in conn.execute("SELECT * FROM probe_search"):
                scope, gid = r["scope"], (r["group_id"] or "")
                if not r["campaign_id"]:
                    continue
                row = {
                    "date": r["date"], "scope": scope,
                    "owner_id": gid if scope == "group" else r["campaign_id"],
                    "campaign_id": r["campaign_id"],
                    "query_type": r["query_type"], "term": r["term"],
                    "match_type": r["match_type"] or "",
                    "impressions": r["impressions"], "clicks": r["clicks"],
                    "cost": r["cost"], "orders": r["orders"],
                    "add_to_cart": r["add_to_cart"],
                    "extra_json": r["extra_json"] or "", "updated_at": NOW(),
                }
                upsert(conn, "fact_search_term", row,
                       ("date", "scope", "owner_id", "query_type", "term", "match_type"))
                if scope == "campaign":
                    touch_term(conn, r["term"], r["date"])
            stats["fact_search_term"] = conn.execute(
                "SELECT COUNT(*) FROM fact_search_term").fetchone()[0]
            stats["dim_search_term"] = conn.execute(
                "SELECT COUNT(*) FROM dim_search_term").fetchone()[0]

        # ---- 事实：probe_competitor ----
        if "probe_competitor" in names:
            for r in conn.execute("SELECT * FROM probe_competitor"):
                d = dict(r)
                d.pop("task_id", None)
                d.pop("campaign_id", None)
                d["fastest_delivery"] = d.pop("delivery_secondary", "")
                upsert(conn, "fact_competitor", d, ("term", "asin"))
            stats["fact_competitor"] = conn.execute(
                "SELECT COUNT(*) FROM fact_competitor").fetchone()[0]

        # ---- 维度：从事实回填基础信息 ----
        for r in conn.execute("SELECT DISTINCT campaign_id FROM fact_campaign"):
            upsert_campaign(conn, campaign_id=r["campaign_id"])
        for r in conn.execute("SELECT DISTINCT campaign_id, group_id FROM fact_group"):
            gid = str(r["group_id"])
            row = conn.execute("SELECT * FROM dim_group WHERE group_id=?", (gid,)).fetchone()
            name = None
            if "probe_fact" in names:
                x = conn.execute("SELECT entity_name FROM probe_fact WHERE level='group'"
                                 " AND entity_id=? LIMIT 1", (gid,)).fetchone()
                name = x["entity_name"] if x else None
            upsert_group(conn, gid, campaign_id=r["campaign_id"], name=name)
        for r in conn.execute("SELECT DISTINCT asin FROM fact_product"):
            upsert_product(conn, r["asin"])
        for r in conn.execute("SELECT DISTINCT target_id, campaign_id FROM fact_target"):
            upsert_target(conn, str(r["target_id"]), campaign_id=r["campaign_id"])

        conn.commit()
    finally:
        if own:
            conn.close()
    if verbose:
        print("迁移完成:", stats)
    return stats


# ---------------------------------------------------------------------------
# 校验：新旧逐日逐层对账
# ---------------------------------------------------------------------------
def validate(conn: Optional[sqlite3.Connection] = None,
             tolerance: float = 0.01) -> List[Dict[str, Any]]:
    """按 (date, level) 对比旧 probe_fact 与新 fact_* 的 cost/clicks/orders"""
    own = conn is None
    conn = conn or get_conn()
    out: List[Dict[str, Any]] = []
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "probe_fact" not in names:
            return out

        pairs = [
            ("campaign", "fact_campaign",
             "FROM fact_campaign WHERE hour=-1 AND placement=''"),
            ("group", "fact_group",
             "FROM fact_group WHERE hour=-1 AND placement=''"),
            ("product", "fact_product",
             "FROM fact_product WHERE hour=-1 AND system='total' AND code=''"),
            ("target", "fact_target", "FROM fact_target WHERE hour=-1"),
        ]
        for level, table, where in pairs:
            if level == "target":
                # 旧结构里投放挂在 campaign/group 下（dim_type='target'）
                old = {r["date"]: r for r in conn.execute(
                    "SELECT date, SUM(cost) cost, SUM(clicks) clicks, SUM(orders) orders"
                    " FROM probe_fact WHERE level IN ('campaign','group')"
                    " AND dim_type='target' AND hour=-1 GROUP BY date")}
            else:
                old = {r["date"]: r for r in conn.execute(
                    "SELECT date, SUM(cost) cost, SUM(clicks) clicks, SUM(orders) orders"
                    " FROM probe_fact WHERE level=? AND dim_type='total' AND hour=-1"
                    " GROUP BY date", (level,))}
            new = {r["date"]: r for r in conn.execute(
                f"SELECT date, SUM(cost) cost, SUM(clicks) clicks, SUM(orders) orders {where}"
                " GROUP BY date")}
            for d in sorted(set(old) | set(new)):
                o, n = old.get(d), new.get(d)
                oc, nc = (o["cost"] if o else 0) or 0, (n["cost"] if n else 0) or 0
                ok_ = abs(oc - nc) <= tolerance
                out.append({
                    "date": d, "level": level, "old_cost": round(oc, 2),
                    "new_cost": round(nc, 2), "diff": round(nc - oc, 2),
                    "old_clicks": (o["clicks"] if o else 0) or 0,
                    "new_clicks": (n["clicks"] if n else 0) or 0,
                    "ok": ok_,
                })
        if "probe_search" in names:
            old = {r["date"]: r for r in conn.execute(
                "SELECT date, SUM(cost) cost, COUNT(*) n FROM probe_search"
                " WHERE scope='campaign' GROUP BY date")}
            new = {r["date"]: r for r in conn.execute(
                "SELECT date, SUM(cost) cost, COUNT(*) n FROM fact_search_term"
                " WHERE scope='campaign' GROUP BY date")}
            for d in sorted(set(old) | set(new)):
                o, n = old.get(d), new.get(d)
                oc, nc = (o["cost"] if o else 0) or 0, (n["cost"] if n else 0) or 0
                out.append({"date": d, "level": "search(campaign)",
                            "old_cost": round(oc, 2), "new_cost": round(nc, 2),
                            "diff": round(nc - oc, 2),
                            "old_clicks": (o["n"] if o else 0) or 0,
                            "new_clicks": (n["n"] if n else 0) or 0,
                            "ok": abs(oc - nc) <= tolerance})
    finally:
        if own:
            conn.close()
    return out


# ---------------------------------------------------------------------------
# 校验 v2：以每日 JSON 为基准（旧表存在多任务重复，不可直接比）
# ---------------------------------------------------------------------------
def validate_vs_json(conn: Optional[sqlite3.Connection] = None,
                     tolerance: float = 0.01) -> List[Dict[str, Any]]:
    """逐日对比「采集 JSON」与「新事实表」，每个日期取最新任务作为基准"""
    import glob
    own = conn is None
    conn = conn or get_conn()
    out: List[Dict[str, Any]] = []
    try:
        init(conn)
        best: Dict[str, Tuple[str, Dict[str, Any]]] = {}
        for mp in glob.glob(os.path.join(config.DATA_DIR, "probe_daily", "*", "meta.json")):
            tid_dir = os.path.dirname(mp)
            try:
                meta = (json.load(open(mp, encoding="utf-8")).get("meta") or {})
            except Exception:               # noqa: BLE001
                continue
            ft = meta.get("fetchedAt") or ""
            for day in meta.get("days") or []:
                p = os.path.join(tid_dir, f"day_{day}.json")
                if not os.path.exists(p):
                    continue
                cur = best.get(day)
                if cur is None or ft > cur[0]:
                    try:
                        best[day] = (ft, json.load(open(p, encoding="utf-8")))
                    except Exception:       # noqa: BLE001
                        pass

        def actual(table: str, date: str) -> float:
            if table == "campaign":
                r = conn.execute("SELECT SUM(cost) c FROM fact_campaign"
                                 " WHERE date=? AND hour=-1 AND placement=''", (date,)).fetchone()
            elif table == "group":
                r = conn.execute("SELECT SUM(cost) c FROM fact_group"
                                 " WHERE date=? AND hour=-1 AND placement=''", (date,)).fetchone()
            elif table == "product":
                r = conn.execute("SELECT SUM(cost) c FROM fact_product"
                                 " WHERE date=? AND hour=-1 AND system='total' AND code=''",
                                 (date,)).fetchone()
            elif table == "target":
                r = conn.execute("SELECT SUM(cost) c FROM fact_target"
                                 " WHERE date=? AND hour=-1", (date,)).fetchone()
            else:
                r = conn.execute("SELECT SUM(cost) c FROM fact_search_term"
                                 " WHERE date=? AND scope='campaign' AND query_type='keyword'",
                                 (date,)).fetchone()
            return round((r["c"] or 0) if r else 0, 2)

        for date in sorted(best):
            data = best[date][1]
            camp = data.get("campaign") or {}
            exp = {
                "campaign": round(_num((camp.get("sum") or {}).get("adCost")), 2),
                "group": round(sum(_num((g.get("sum") or {}).get("adCost"))
                                   for g in (data.get("groups") or [])), 2),
                "product": round(sum(_num((p.get("sum") or {}).get("adCost"))
                                     for p in (data.get("products") or [])), 2),
                "target": round(sum(_num((t.get("sum") or {}).get("adCost"))
                                    for t in (camp.get("targets") or {}).values()), 2),
                "search": round(sum(_num(r.get("adCost"))
                                    for r in ((data.get("search") or {}).get("keyword") or [])), 2),
            }
            for level, want in exp.items():
                got = actual("search" if level == "search" else level, date)
                out.append({"date": date, "level": level, "json_cost": want,
                            "db_cost": got, "diff": round(got - want, 2),
                            "ok": abs(got - want) <= tolerance})
    finally:
        if own:
            conn.close()
    return out


def migrate_search_from_json(conn: Optional[sqlite3.Connection] = None,
                             rebuild: bool = True) -> Dict[str, Any]:
    """搜索词以**每日 JSON** 为权威来源重建（旧 probe_search 主键漏 match_type，已丢行）"""
    import glob
    own = conn is None
    conn = conn or get_conn()
    stat = {"campaign": 0, "group": 0, "asin": 0, "terms": 0}
    try:
        init(conn)
        if rebuild:
            conn.execute("DELETE FROM fact_search_term")
            conn.execute("DELETE FROM dim_search_term")
        for mp in sorted(glob.glob(os.path.join(config.DATA_DIR, "probe_daily", "*", "meta.json"))):
            tid_dir = os.path.dirname(mp)
            try:
                meta = json.load(open(mp, encoding="utf-8")).get("meta") or {}
            except Exception:               # noqa: BLE001
                continue
            cid = str(meta.get("campaignId") or "")
            if not cid:
                continue
            for day in meta.get("days") or []:
                p = os.path.join(tid_dir, f"day_{day}.json")
                if not os.path.exists(p):
                    continue
                try:
                    data = json.load(open(p, encoding="utf-8"))
                except Exception:           # noqa: BLE001
                    continue
                search = data.get("search") or {}

                def put(rows, scope: str, owner: str, qtype: str) -> None:
                    for r in rows or []:
                        term = str(r.get("query") or "").strip()
                        if not term:
                            continue
                        row = {
                            "date": day, "scope": scope, "owner_id": owner,
                            "campaign_id": cid, "query_type": qtype, "term": term,
                            "match_type": str(r.get("matchType") or ""),
                            "impressions": _num(r.get("impressions")),
                            "clicks": _num(r.get("clicks")),
                            "cost": _num(r.get("adCost")),
                            "orders": _num(r.get("adOrderNum")),
                            "add_to_cart": _num(r.get("addToCart")),
                            "extra_json": json.dumps(
                                {k: r.get(k) for k in ("suggested", "price", "asinRating",
                                                       "ratingCount", "originalBid")},
                                ensure_ascii=False),
                            "updated_at": NOW(),
                        }
                        upsert(conn, "fact_search_term", row,
                               ("date", "scope", "owner_id", "query_type", "term", "match_type"))
                        if qtype == "keyword":
                            touch_term(conn, term, day)
                            stat["campaign" if scope == "campaign" else "group"] += 1
                        else:
                            stat["asin"] += 1

                put(search.get("keyword"), "campaign", cid, "keyword")
                put(search.get("asin"), "campaign", cid, "asin")
                for gid, bucket in (search.get("byGroup") or {}).items():
                    put(bucket.get("keyword"), "group", str(gid), "keyword")
                    put(bucket.get("asin"), "group", str(gid), "asin")
        conn.commit()
        stat["terms"] = conn.execute("SELECT COUNT(*) FROM dim_search_term").fetchone()[0]
    finally:
        if own:
            conn.close()
    return stat


def enrich_dims(conn: Optional[sqlite3.Connection] = None,
                campaign_id: str = "", day: str = "") -> Dict[str, int]:
    """从赛狐接口补齐维度表的名称/属性（活动/组/投放/产品）"""
    own = conn is None
    conn = conn or get_conn()
    stat = {"campaign": 0, "group": 0, "target": 0, "product": 0}
    try:
        init(conn)
        from backend import campaign_probe_api as cpa
        from backend import daily_probe_api as dpa
        cid = campaign_id or (conn.execute(
            "SELECT campaign_id FROM fact_campaign LIMIT 1").fetchone() or [""])[0]
        day = day or (conn.execute(
            "SELECT MIN(date) FROM fact_campaign").fetchone() or [""])[0]
        if not cid or not day:
            return stat

        info: Dict[str, Any] = {}
        try:
            info = cpa.layer_campaign_info(str(cid)) or {}
        except Exception:                   # noqa: BLE001
            info = {}
        if not info:
            # 兜底：从活动列表里按 campaignId 找
            try:
                for r in cpa.layer_campaign_list(str(DEFAULT_SHOP_ID), day, day) or []:
                    if str(r.get("campaignId")) == str(cid):
                        info = r
                        break
            except Exception:               # noqa: BLE001
                info = {}
        if info:
            try:
                upsert_campaign(conn, campaign_id=str(cid),
                                inner_id=str(info.get("id") or ""),
                                name=info.get("campaignName") or info.get("name") or "",
                                shop_id=str(info.get("shopId") or DEFAULT_SHOP_ID),
                                marketplace_id=info.get("marketplaceId") or "",
                                targeting_type=info.get("targetingType")
                                or info.get("campaignTargetingType") or "",
                                portfolio_id=str(info.get("portfolioId") or ""),
                                portfolio_name=info.get("portfolioName") or "",
                                status=info.get("state") or "",
                                daily_budget=_num(info.get("dailyBudget")) or None,
                                strategy=info.get("strategy") or "",
                                placement_top=_num(info.get("placementTop")) or None,
                                placement_rest=_num(info.get("placementRestOfSearch")) or None,
                                placement_product=_num(info.get("placementProductPage")) or None)
                stat["campaign"] = 1
            except Exception:               # noqa: BLE001
                pass

        try:
            for g in dpa.list_groups(str(cid), day) or []:
                upsert_group(conn, str(g.get("id")),
                             amazon_group_id=str(g.get("adGroupId") or ""),
                             campaign_id=str(cid), name=g.get("name") or "",
                             default_bid=_num(g.get("defaultBid")) or None,
                             status=g.get("state") or "")
                stat["group"] += 1
        except Exception:                   # noqa: BLE001
            pass

        try:
            for t in dpa.list_targets(str(cid), day) or []:
                upsert_target(conn, str(t.get("targetId")),
                              group_id=str(t.get("adGroupId") or ""),
                              campaign_id=str(cid),
                              target_type=t.get("_targetType") or "",
                              target_text=t.get("targetText") or t.get("targetName") or "",
                              match_type=t.get("matchType") or "")
                stat["target"] += 1
        except Exception:                   # noqa: BLE001
            pass

        try:
            prods = dpa.list_products(str(cid), day) or []
            asins = [p.get("asin") for p in prods if p.get("asin")]
            basic = dpa.product_basic_info(asins) if asins else {}
            for p in prods:
                asin = p.get("asin")
                if not asin:
                    continue
                b = basic.get(asin) or {}
                upsert_product(conn, asin,
                               sku=p.get("sku") or b.get("sku") or "",
                               parent_asin=b.get("parentAsin") or "",
                               title=b.get("title") or "",
                               price=_num(b.get("standardPrice")) or None,
                               landed_price=_num(b.get("landedPrice")) or None,
                               fulfillment=b.get("fulfillmentChannel") or "",
                               online_status=b.get("onlineStatus") or "",
                               rating=_num(b.get("rating")) or None,
                               rating_count=b.get("ratingCount"),
                               quantity=b.get("quantity"))
                stat["product"] += 1
        except Exception:                   # noqa: BLE001
            pass

        # 组 id 归一：搜索词里存的是 Amazon adGroupId，统一映射成内部 group_id
        conn.execute(
            "UPDATE fact_search_term SET owner_id=("
            "  SELECT group_id FROM dim_group WHERE amazon_group_id=fact_search_term.owner_id)"
            " WHERE scope='group' AND EXISTS (SELECT 1 FROM dim_group"
            "   WHERE amazon_group_id=fact_search_term.owner_id)")
        # 投放记录补 group_id（旧数据空缺）
        conn.execute(
            "UPDATE fact_target SET group_id=("
            "  SELECT group_id FROM dim_target WHERE target_id=fact_target.target_id)"
            " WHERE (group_id IS NULL OR group_id='') AND EXISTS ("
            "  SELECT 1 FROM dim_target WHERE target_id=fact_target.target_id)")
        conn.commit()
    finally:
        if own:
            conn.close()
    return stat


# ---------------------------------------------------------------------------
# 写入：一份 day JSON → 新结构（UPSERT，幂等）
# ---------------------------------------------------------------------------
def write_day(data: Dict[str, Any], campaign_id: str,
              conn: Optional[sqlite3.Connection] = None) -> Dict[str, int]:
    """把 daily_probe 采集到的单日数据写入新表（data = day_<date>.json）"""
    own = conn is None
    conn = conn or get_conn()
    stat = {"campaign": 0, "group": 0, "product": 0, "target": 0, "search": 0, "term": 0}
    try:
        init(conn)
        date = data.get("date") or ""
        if not date:
            return stat
        camp = data.get("campaign") or {}

        write_campaign(conn, campaign_id, date,
                       camp.get("hours") or [], camp.get("sum") or {},
                       camp.get("placements") or {})
        stat["campaign"] = 1
        write_targets(conn, campaign_id, "", date, camp.get("targets") or {})

        for g in data.get("groups") or []:
            gid = str(g.get("id") or "")
            if not gid:
                continue
            write_group(conn, gid, campaign_id, date,
                        g.get("hours") or [], g.get("sum") or {},
                        g.get("placements") or {})
            write_targets(conn, campaign_id, gid, date, g.get("targets") or {})
            upsert_group(conn, gid, campaign_id=campaign_id,
                         amazon_group_id=str(g.get("adGroupId") or ""),
                         name=g.get("name") or "",
                         default_bid=_num(g.get("defaultBid")) or None)
            stat["group"] += 1

        for p in data.get("products") or []:
            asin = p.get("asin") or ""
            if not asin:
                continue
            write_product(conn, asin, campaign_id, "", date,
                          p.get("total") or [], p.get("sum") or {},
                          p.get("placements") or [], p.get("match") or [])
            upsert_product(conn, asin, sku=p.get("sku") or "")
            stat["product"] += 1

        search = data.get("search") or {}
        _write_search(conn, campaign_id, date, search)
        stat["search"] = (len(search.get("keyword") or []) + len(search.get("asin") or []))
        stat["term"] = conn.execute("SELECT COUNT(*) FROM dim_search_term").fetchone()[0]

        upsert_campaign(conn, campaign_id=campaign_id)
        # 组级搜索词 owner_id 归一（避免 Amazon 组 id 与内部 id 并存导致翻倍）
        normalize_search_owner(conn)
        conn.commit()
    finally:
        if own:
            conn.close()
    return stat


def _write_search(conn: sqlite3.Connection, campaign_id: str, date: str,
                  search: Dict[str, Any]) -> None:
    def put(rows, scope: str, owner: str, qtype: str) -> None:
        for r in rows or []:
            term = str(r.get("query") or "").strip()
            if not term:
                continue
            row = {
                "date": date, "scope": scope, "owner_id": owner,
                "campaign_id": campaign_id, "query_type": qtype, "term": term,
                "match_type": str(r.get("matchType") or ""),
                "impressions": _num(r.get("impressions")),
                "clicks": _num(r.get("clicks")),
                "cost": _num(r.get("adCost")),
                "orders": _num(r.get("adOrderNum")),
                "add_to_cart": _num(r.get("addToCart")),
                "extra_json": json.dumps(
                    {k: r.get(k) for k in ("suggested", "price", "asinRating",
                                           "ratingCount", "originalBid")},
                    ensure_ascii=False),
                "updated_at": NOW(),
            }
            upsert(conn, "fact_search_term", row,
                   ("date", "scope", "owner_id", "query_type", "term", "match_type"))
            if qtype == "keyword":
                touch_term(conn, term, date)

    put(search.get("keyword"), "campaign", campaign_id, "keyword")
    put(search.get("asin"), "campaign", campaign_id, "asin")
    for gid, bucket in (search.get("byGroup") or {}).items():
        real = _group_internal_id(conn, str(gid)) or str(gid)
        put(bucket.get("keyword"), "group", real, "keyword")
        put(bucket.get("asin"), "group", real, "asin")


def normalize_search_owner(conn: sqlite3.Connection) -> Dict[str, int]:
    """把组级搜索词的 owner_id 统一成内部 group_id，并清掉重复行

    历史原因：某些采集批次在 dim_group 还没写入时，owner_id 落成了 Amazon
    adGroupId，导致同一 (date, term, match_type) 存在两条，SUM 会翻倍。
    """
    stat = {"deleted": 0, "updated": 0}
    try:
        # ① 已有内部 id 对应行的，删掉 Amazon id 的那条
        cur = conn.execute("""
            DELETE FROM fact_search_term
            WHERE scope='group'
              AND owner_id IN (SELECT amazon_group_id FROM dim_group
                               WHERE amazon_group_id IS NOT NULL AND amazon_group_id<>'')
              AND EXISTS (
                SELECT 1 FROM fact_search_term f2
                JOIN dim_group g ON g.group_id = f2.owner_id
                WHERE f2.scope='group'
                  AND f2.date = fact_search_term.date
                  AND f2.term = fact_search_term.term
                  AND f2.match_type = fact_search_term.match_type
                  AND f2.query_type = fact_search_term.query_type
                  AND f2.campaign_id = fact_search_term.campaign_id
                  AND g.amazon_group_id = fact_search_term.owner_id
              )
        """)
        stat["deleted"] = cur.rowcount or 0
        # ② 没有内部 id 对应行的，直接改写成内部 id
        cur2 = conn.execute("""
            UPDATE fact_search_term
            SET owner_id = (SELECT g.group_id FROM dim_group g
                            WHERE g.amazon_group_id = fact_search_term.owner_id)
            WHERE scope='group'
              AND EXISTS (SELECT 1 FROM dim_group g
                          WHERE g.amazon_group_id = fact_search_term.owner_id)
        """)
        stat["updated"] = cur2.rowcount or 0
        conn.commit()
    except Exception:                       # noqa: BLE001
        pass
    return stat


def _group_internal_id(conn: sqlite3.Connection, amazon_gid: str) -> str:
    try:
        r = conn.execute("SELECT group_id FROM dim_group WHERE amazon_group_id=?",
                         (amazon_gid,)).fetchone()
        return str(r["group_id"]) if r else ""
    except Exception:                       # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# 查询：按「活动 + 日期区间」
# ---------------------------------------------------------------------------
def list_campaigns(conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        rows = conn.execute("""
            SELECT c.campaign_id, c.name, c.targeting_type, c.daily_budget, c.status,
                   c.portfolio_id, c.portfolio_name,
                   COUNT(DISTINCT f.date) AS days,
                   MIN(f.date) AS first_date, MAX(f.date) AS last_date,
                   ROUND(SUM(CASE WHEN f.hour=-1 AND f.placement='' THEN f.cost END),2) AS total_cost,
                   SUM(CASE WHEN f.hour=-1 AND f.placement='' THEN f.clicks END) AS total_clicks
            FROM dim_campaign c
            LEFT JOIN fact_campaign f ON f.campaign_id = c.campaign_id
            GROUP BY c.campaign_id, c.portfolio_id, c.portfolio_name
            ORDER BY days DESC, total_cost DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        if own:
            conn.close()


def dates_of(campaign_id: str, conn: Optional[sqlite3.Connection] = None) -> List[str]:
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        rs = conn.execute("SELECT DISTINCT date FROM fact_campaign WHERE campaign_id=?"
                          " ORDER BY date", (campaign_id,)).fetchall()
        return [r["date"] for r in rs]
    finally:
        if own:
            conn.close()


def _metrics(row) -> Dict[str, Any]:
    if row is None:
        return {}
    try:
        return json.loads(row["metrics_json"] or "{}")
    except Exception:                       # noqa: BLE001
        return {}


def _hours(conn, table: str, where: str, args: List[Any]) -> List[Dict[str, Any]]:
    rows = conn.execute(f"SELECT hour, metrics_json, impressions, clicks, cost "
                        f"FROM {table} WHERE {where} AND hour>=0 ORDER BY hour",
                        args).fetchall()
    out = []
    for r in rows:
        m = _metrics(r)
        if not m:                            # metrics_json 缺失时用列拼
            m = {"impressions": r["impressions"], "clicks": r["clicks"],
                 "adCost": r["cost"]}
        out.append({"hour": f"{r['hour']}-{r['hour'] + 1}", "metrics": m})
    return out


def _sum(conn, table: str, where: str, args: List[Any]) -> Dict[str, Any]:
    r = conn.execute(f"SELECT metrics_json, impressions, clicks, cost, orders, sales,"
                     f" add_to_cart FROM {table} WHERE {where} AND hour=-1 LIMIT 1",
                     args).fetchone()
    if not r:
        return {}
    m = _metrics(r)
    if not m:
        m = {"impressions": r["impressions"], "clicks": r["clicks"], "adCost": r["cost"],
             "adOrderNum": r["orders"], "adSale": r["sales"],
             "addToCart": r["add_to_cart"]}
    return m


def day_overview(campaign_id: str, date: str,
                 conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """还原成前端可直接渲染的结构（与 day_*.json 一致）"""
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        camp = {
            "hours": _hours(conn, "fact_campaign",
                            "campaign_id=? AND date=? AND placement=''",
                            [campaign_id, date]),
            "sum": _sum(conn, "fact_campaign",
                        "campaign_id=? AND date=? AND placement=''",
                        [campaign_id, date]),
            "placements": {}, "targets": {},
        }
        for r in conn.execute("SELECT DISTINCT placement FROM fact_campaign"
                              " WHERE campaign_id=? AND date=? AND placement<>''",
                              (campaign_id, date)).fetchall():
            code = r["placement"]
            camp["placements"][code] = {
                "label": code,
                "sum": _sum(conn, "fact_campaign",
                            "campaign_id=? AND date=? AND placement=?",
                            [campaign_id, date, code]),
                "hours": _hours(conn, "fact_campaign",
                                "campaign_id=? AND date=? AND placement=?",
                                [campaign_id, date, code])}
        for r in conn.execute("SELECT DISTINCT target_id FROM fact_target"
                              " WHERE campaign_id=? AND date=?",
                              (campaign_id, date)).fetchall():
            tid = r["target_id"]
            nm = conn.execute("SELECT target_text FROM dim_target WHERE target_id=?",
                              (tid,)).fetchone()
            camp["targets"][tid] = {
                "label": (nm["target_text"] if nm and nm["target_text"] else tid),
                "sum": _sum(conn, "fact_target", "target_id=? AND date=?", [tid, date]),
                "hours": _hours(conn, "fact_target", "target_id=? AND date=?", [tid, date])}

        groups = []
        for g in conn.execute("SELECT DISTINCT group_id FROM fact_group"
                              " WHERE campaign_id=? AND date=?",
                              (campaign_id, date)).fetchall():
            gid = g["group_id"]
            info = conn.execute("SELECT name FROM dim_group WHERE group_id=?", (gid,)).fetchone()
            item = {"id": gid, "name": (info["name"] if info and info["name"] else gid),
                    "hours": _hours(conn, "fact_group",
                                    "group_id=? AND date=? AND placement=''", [gid, date]),
                    "sum": _sum(conn, "fact_group",
                                "group_id=? AND date=? AND placement=''", [gid, date]),
                    "placements": {}, "targets": {}}
            for r in conn.execute("SELECT DISTINCT placement FROM fact_group"
                                  " WHERE group_id=? AND date=? AND placement<>''",
                                  (gid, date)).fetchall():
                code = r["placement"]
                item["placements"][code] = {
                    "label": code,
                    "sum": _sum(conn, "fact_group",
                                "group_id=? AND date=? AND placement=?",
                                [gid, date, code]),
                    "hours": _hours(conn, "fact_group",
                                    "group_id=? AND date=? AND placement=?",
                                    [gid, date, code])}
            for r in conn.execute("SELECT DISTINCT target_id FROM fact_target"
                                  " WHERE group_id=? AND date=?", (gid, date)).fetchall():
                tid = r["target_id"]
                nm = conn.execute("SELECT target_text FROM dim_target WHERE target_id=?",
                                  (tid,)).fetchone()
                item["targets"][tid] = {
                    "label": (nm["target_text"] if nm and nm["target_text"] else tid),
                    "sum": _sum(conn, "fact_target", "target_id=? AND date=?", [tid, date]),
                    "hours": _hours(conn, "fact_target",
                                    "target_id=? AND date=?", [tid, date])}
            groups.append(item)

        products = []
        for p in conn.execute("SELECT DISTINCT asin FROM fact_product"
                              " WHERE campaign_id=? AND date=?", (campaign_id, date)).fetchall():
            asin = p["asin"]
            d = conn.execute("SELECT sku FROM dim_product WHERE asin=?", (asin,)).fetchone()
            item = {"asin": asin, "sku": (d["sku"] if d else "") or "",
                    "total": _hours(conn, "fact_product",
                                    "asin=? AND campaign_id=? AND date=?"
                                    " AND system='total' AND code=''",
                                    [asin, campaign_id, date]),
                    "sum": _sum(conn, "fact_product",
                                "asin=? AND campaign_id=? AND date=?"
                                " AND system='total' AND code=''",
                                [asin, campaign_id, date]),
                    "placements": [], "match": []}
            for r in conn.execute("SELECT DISTINCT code FROM fact_product"
                                  " WHERE asin=? AND campaign_id=? AND date=?"
                                  " AND system='placement'",
                                  (asin, campaign_id, date)).fetchall():
                code = r["code"]
                item["placements"].append({
                    "code": code, "label": code,
                    "sum": _sum(conn, "fact_product",
                                "asin=? AND campaign_id=? AND date=?"
                                " AND system='placement' AND code=?",
                                [asin, campaign_id, date, code]),
                    "hours": _hours(conn, "fact_product",
                                    "asin=? AND campaign_id=? AND date=?"
                                    " AND system='placement' AND code=?",
                                    [asin, campaign_id, date, code])})
            for r in conn.execute("SELECT DISTINCT code FROM fact_product"
                                  " WHERE asin=? AND campaign_id=? AND date=?"
                                  " AND system='match'",
                                  (asin, campaign_id, date)).fetchall():
                code = r["code"]
                item["match"].append({
                    "code": code, "label": code,
                    "sum": _sum(conn, "fact_product",
                                "asin=? AND campaign_id=? AND date=?"
                                " AND system='match' AND code=?",
                                [asin, campaign_id, date, code]),
                    "hours": _hours(conn, "fact_product",
                                    "asin=? AND campaign_id=? AND date=?"
                                    " AND system='match' AND code=?",
                                    [asin, campaign_id, date, code])})
            products.append(item)

        def srows(scope: str, owner: str, qtype: str) -> List[Dict[str, Any]]:
            rs = conn.execute(
                "SELECT term, match_type, impressions, clicks, cost, orders,"
                " add_to_cart, extra_json FROM fact_search_term"
                " WHERE campaign_id=? AND date=? AND scope=? AND owner_id=? AND query_type=?"
                " ORDER BY cost DESC", (campaign_id, date, scope, owner, qtype)).fetchall()
            out = []
            for r in rs:
                try:
                    extra = json.loads(r["extra_json"] or "{}")
                except Exception:           # noqa: BLE001
                    extra = {}
                out.append({"query": r["term"], "matchType": r["match_type"],
                            "impressions": r["impressions"], "clicks": r["clicks"],
                            "adCost": r["cost"], "adOrderNum": r["orders"],
                            "addToCart": r["add_to_cart"], **extra})
            return out

        by_group = {}
        for g in conn.execute("SELECT DISTINCT owner_id FROM fact_search_term"
                              " WHERE campaign_id=? AND date=? AND scope='group'",
                              (campaign_id, date)).fetchall():
            gid = g["owner_id"]
            by_group[gid] = {"keyword": srows("group", gid, "keyword"),
                             "asin": srows("group", gid, "asin")}

        return {"date": date, "campaign": camp, "groups": groups, "products": products,
                "search": {"keyword": srows("campaign", campaign_id, "keyword"),
                           "asin": srows("campaign", campaign_id, "asin"),
                           "byGroup": by_group}}
    finally:
        if own:
            conn.close()


def trend(campaign_id: str, conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        rs = conn.execute(
            "SELECT date, impressions, clicks, cost, orders, sales, add_to_cart"
            " FROM fact_campaign WHERE campaign_id=? AND hour=-1 AND placement=''"
            " ORDER BY date", (campaign_id,)).fetchall()
        return [{"date": r["date"], "impressions": r["impressions"] or 0,
                 "clicks": r["clicks"] or 0, "cost": round(r["cost"] or 0, 2),
                 "orders": r["orders"] or 0, "sales": r["sales"] or 0,
                 "add_to_cart": r["add_to_cart"] or 0} for r in rs]
    finally:
        if own:
            conn.close()


def hourly_avg(campaign_id: str,
               conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        rs = conn.execute(
            "SELECT hour, SUM(impressions) i, SUM(clicks) c, SUM(cost) s,"
            " SUM(orders) o, COUNT(DISTINCT date) d FROM fact_campaign"
            " WHERE campaign_id=? AND hour>=0 AND placement=''"
            " GROUP BY hour ORDER BY hour", (campaign_id,)).fetchall()
        return [{"hour": f"{r['hour']}-{r['hour'] + 1}", "impressions": r["i"] or 0,
                 "clicks": r["c"] or 0, "cost": round(r["s"] or 0, 2),
                 "orders": r["o"] or 0, "days": r["d"] or 0} for r in rs]
    finally:
        if own:
            conn.close()


def competitor_terms(campaign_id: str, start: str = "", end: str = "",
                     conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """该活动（可选区间）出现过的词 → 竞品概览（竞品按词共享）"""
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        where = "campaign_id=? AND query_type='keyword'"
        args: List[Any] = [campaign_id]
        if start and end:
            where += " AND date BETWEEN ? AND ?"
            args += [start, end]
        rs = conn.execute(f"""
            SELECT t.term, t.ad_cost, t.clicks,
                   COUNT(c.asin) n, MIN(c.price) min_price,
                   MAX(c.price) max_price, AVG(c.reviews) avg_rev,
                   MAX(c.fetched_at) fetched_at
            FROM (
                SELECT term, SUM(cost) ad_cost, SUM(clicks) clicks
                FROM fact_search_term
                WHERE {where} AND scope='campaign'
                GROUP BY term
            ) t
            LEFT JOIN fact_competitor c ON c.term = t.term
            GROUP BY t.term, t.ad_cost, t.clicks
            ORDER BY t.ad_cost DESC, n DESC, t.term
        """, args).fetchall()
        out = []
        for r in rs:
            prices = [x[0] for x in conn.execute(
                "SELECT price FROM fact_competitor WHERE term=? AND price IS NOT NULL"
                " ORDER BY price", (r["term"],)).fetchall()]
            med = prices[len(prices) // 2] if prices else None
            out.append({"term": r["term"], "count": r["n"] or 0,
                        "adCost": round(r["ad_cost"] or 0, 2),
                        "clicks": r["clicks"] or 0,
                        "median": round(med, 2) if med else None,
                        "min": r["min_price"], "max": r["max_price"],
                        "avgReviews": round(r["avg_rev"] or 0, 1),
                        "fetchedAt": r["fetched_at"] or ""})
        return out
    finally:
        if own:
            conn.close()


def competitor_items(term: str,
                     conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        rs = conn.execute("SELECT * FROM fact_competitor WHERE term=?"
                          " ORDER BY position", (term,)).fetchall()
        return [dict(r) for r in rs]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 历史回填：把所有 probe_daily/*/day_*.json 导入新表（幂等）
# ---------------------------------------------------------------------------
def import_from_dirs(conn: Optional[sqlite3.Connection] = None,
                     verbose: bool = False) -> Dict[str, Any]:
    import glob
    own = conn is None
    conn = conn or get_conn()
    total = {"files": 0, "campaign": 0, "group": 0, "product": 0, "search": 0}
    try:
        init(conn)
        base = os.path.join(config.DATA_DIR, "probe_daily")
        for mp in sorted(glob.glob(os.path.join(base, "*", "meta.json"))):
            tid_dir = os.path.dirname(mp)
            try:
                meta = json.load(open(mp, encoding="utf-8")).get("meta") or {}
            except Exception:               # noqa: BLE001
                continue
            cid = str(meta.get("campaignId") or "")
            if not cid:
                continue
            name = meta.get("campaignName") or ""
            for day in meta.get("days") or []:
                p = os.path.join(tid_dir, f"day_{day}.json")
                if not os.path.exists(p):
                    continue
                try:
                    data = json.load(open(p, encoding="utf-8"))
                except Exception:           # noqa: BLE001
                    continue
                st = write_day(data, cid, conn)
                total["files"] += 1
                total["campaign"] += st["campaign"]
                total["group"] += st["group"]
                total["product"] += st["product"]
                total["search"] += st["search"]
            if name:
                upsert_campaign(conn, campaign_id=cid, name=name)
        conn.commit()
    finally:
        if own:
            conn.close()
    if verbose:
        print("导入完成:", total)
    return total


def products_of(campaign_id: str,
                conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """该活动涉及的产品的维度信息（价格/库存/状态）"""
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        rs = conn.execute("""
            SELECT p.* FROM dim_product p
            WHERE p.asin IN (SELECT DISTINCT asin FROM fact_product WHERE campaign_id=?)
            ORDER BY p.asin
        """, (campaign_id,)).fetchall()
        return [dict(r) for r in rs]
    finally:
        if own:
            conn.close()


# ---------------------------------------------------------------------------
# 区间汇总（活动 + 起止日期 → 汇总指标与维度明细）
# ---------------------------------------------------------------------------
AGG_COLS = ("impressions", "clicks", "cost", "orders", "sales", "add_to_cart", "dpv")


def _agg_metrics(imp, clk, cost, orders, sales, cart, dpv) -> Dict[str, Any]:
    """拼成与原始 metrics 同名的字典，前端可直接复用"""
    return {
        "impressions": imp or 0, "clicks": clk or 0, "adCost": round(cost or 0, 2),
        "adOrderNum": orders or 0, "adSale": round(sales or 0, 2),
        "addToCart": cart or 0, "detailPageViews": dpv or 0,
        "ctr": round((clk or 0) / imp * 100, 2) if imp else 0,
        "adCostPerClick": round((cost or 0) / clk, 2) if clk else 0,
        "acos": round((cost or 0) / sales * 100, 2) if sales else 0,
    }


def _sum_sql(where: str) -> str:
    cols = ", ".join(f"SUM({c}) {c}" for c in AGG_COLS)
    return f"SELECT {cols} FROM {{table}} WHERE {where} AND hour=-1"


def range_overview(campaign_id: str, start: str, end: str,
                   conn: Optional[sqlite3.Connection] = None) -> Dict[str, Any]:
    """按区间汇总，返回与 day_overview 同构的数据（hours 为区间内按小时累计）"""
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        args = [campaign_id, start, end]

        def one_sum(table: str, extra_where: str = "", extra_args: List[Any] = ()):
            sql = _sum_sql("campaign_id=? AND date BETWEEN ? AND ?" + extra_where)
            r = conn.execute(sql.format(table=table), args + list(extra_args)).fetchone()
            return _agg_metrics(*[r[c] for c in AGG_COLS]) if r else _agg_metrics(*[0] * 7)

        def hours(table: str, extra_where: str = "", extra_args: List[Any] = ()):
            sql = (f"SELECT hour, " + ", ".join(f"SUM({c}) {c}" for c in AGG_COLS)
                   + f" FROM {table} WHERE campaign_id=? AND date BETWEEN ? AND ?"
                   + extra_where + " AND hour>=0 GROUP BY hour ORDER BY hour")
            rows = conn.execute(sql, args + list(extra_args)).fetchall()
            return [{"hour": f"{r['hour']}-{r['hour'] + 1}",
                     "metrics": _agg_metrics(*[r[c] for c in AGG_COLS])} for r in rows]

        camp = {
            "hours": hours("fact_campaign", " AND placement=''"),
            "sum": one_sum("fact_campaign", " AND placement=''"),
            "placements": {}, "targets": {},
        }
        for r in conn.execute("SELECT DISTINCT placement FROM fact_campaign"
                              " WHERE campaign_id=? AND date BETWEEN ? AND ?"
                              " AND placement<>''", args).fetchall():
            code = r["placement"]
            camp["placements"][code] = {
                "label": code,
                "sum": one_sum("fact_campaign", " AND placement=?", [code]),
                "hours": hours("fact_campaign", " AND placement=?", [code])}
        for r in conn.execute("SELECT DISTINCT target_id FROM fact_target"
                              " WHERE campaign_id=? AND date BETWEEN ? AND ?", args).fetchall():
            tid = r["target_id"]
            nm = conn.execute("SELECT target_text FROM dim_target WHERE target_id=?",
                              (tid,)).fetchone()
            camp["targets"][tid] = {
                "label": (nm["target_text"] if nm and nm["target_text"] else tid),
                "sum": one_sum("fact_target", " AND target_id=?", [tid]),
                "hours": hours("fact_target", " AND target_id=?", [tid])}

        groups = []
        for g in conn.execute("SELECT DISTINCT group_id FROM fact_group"
                              " WHERE campaign_id=? AND date BETWEEN ? AND ?", args).fetchall():
            gid = g["group_id"]
            info = conn.execute("SELECT name FROM dim_group WHERE group_id=?", (gid,)).fetchone()
            gargs = [gid, start, end]

            def g_sum(extra="", eargs=()):
                r = conn.execute(
                    _sum_sql("group_id=? AND date BETWEEN ? AND ?" + extra)
                    .format(table="fact_group"), gargs + list(eargs)).fetchone()
                return _agg_metrics(*[r[c] for c in AGG_COLS]) if r else _agg_metrics(*[0] * 7)

            def g_hours(extra="", eargs=()):
                sql = ("SELECT hour, " + ", ".join(f"SUM({c}) {c}" for c in AGG_COLS)
                       + " FROM fact_group WHERE group_id=? AND date BETWEEN ? AND ?"
                       + extra + " AND hour>=0 GROUP BY hour ORDER BY hour")
                rs = conn.execute(sql, gargs + list(eargs)).fetchall()
                return [{"hour": f"{r['hour']}-{r['hour'] + 1}",
                         "metrics": _agg_metrics(*[r[c] for c in AGG_COLS])} for r in rs]

            item = {"id": gid, "name": (info["name"] if info and info["name"] else gid),
                    "hours": g_hours(" AND placement=''"), "sum": g_sum(" AND placement=''"),
                    "placements": {}, "targets": {}}
            for r in conn.execute("SELECT DISTINCT placement FROM fact_group"
                                  " WHERE group_id=? AND date BETWEEN ? AND ?"
                                  " AND placement<>''", gargs).fetchall():
                code = r["placement"]
                item["placements"][code] = {"label": code,
                                            "sum": g_sum(" AND placement=?", [code]),
                                            "hours": g_hours(" AND placement=?", [code])}
            for r in conn.execute("SELECT DISTINCT target_id FROM fact_target"
                                  " WHERE group_id=? AND date BETWEEN ? AND ?",
                                  gargs).fetchall():
                tid = r["target_id"]
                nm = conn.execute("SELECT target_text FROM dim_target WHERE target_id=?",
                                  (tid,)).fetchone()
                r2 = conn.execute(_sum_sql("target_id=? AND date BETWEEN ? AND ?")
                                  .format(table="fact_target"), [tid, start, end]).fetchone()
                h2 = conn.execute(
                    "SELECT hour, " + ", ".join(f"SUM({c}) {c}" for c in AGG_COLS)
                    + " FROM fact_target WHERE target_id=? AND date BETWEEN ? AND ?"
                    " AND hour>=0 GROUP BY hour ORDER BY hour",
                    [tid, start, end]).fetchall()
                item["targets"][tid] = {
                    "label": (nm["target_text"] if nm and nm["target_text"] else tid),
                    "sum": _agg_metrics(*[r2[c] for c in AGG_COLS]) if r2 else _agg_metrics(*[0] * 7),
                    "hours": [{"hour": f"{r['hour']}-{r['hour'] + 1}",
                               "metrics": _agg_metrics(*[r[c] for c in AGG_COLS])} for r in h2]}
            groups.append(item)

        products = []
        for p in conn.execute("SELECT DISTINCT asin FROM fact_product"
                              " WHERE campaign_id=? AND date BETWEEN ? AND ?", args).fetchall():
            asin = p["asin"]
            d = conn.execute("SELECT sku FROM dim_product WHERE asin=?", (asin,)).fetchone()
            pargs = [asin, campaign_id, start, end]

            def p_sum(system: str, code: str = ""):
                extra = " AND code=?" if system != "total" else ""
                eargs = [code] if extra else []
                r = conn.execute(_sum_sql("asin=? AND campaign_id=? AND date BETWEEN ? AND ?"
                                          " AND system=? AND code" + ("=?" if extra else "=''"))
                                 .format(table="fact_product"),
                                 pargs + [system] + eargs).fetchone()
                return _agg_metrics(*[r[c] for c in AGG_COLS]) if r else _agg_metrics(*[0] * 7)

            def p_hours(system: str, code: str = ""):
                extra = " AND code=?" if system != "total" else " AND code=''"
                eargs = [code] if code else []
                sql = ("SELECT hour, " + ", ".join(f"SUM({c}) {c}" for c in AGG_COLS)
                       + " FROM fact_product WHERE asin=? AND campaign_id=?"
                         " AND date BETWEEN ? AND ? AND system=?" + extra
                       + " AND hour>=0 GROUP BY hour ORDER BY hour")
                rs = conn.execute(sql, pargs + [system] + eargs).fetchall()
                return [{"hour": f"{r['hour']}-{r['hour'] + 1}",
                         "metrics": _agg_metrics(*[r[c] for c in AGG_COLS])} for r in rs]

            item = {"asin": asin, "sku": (d["sku"] if d else "") or "",
                    "total": p_hours("total"), "sum": p_sum("total"),
                    "placements": [], "match": []}
            for r in conn.execute("SELECT DISTINCT code FROM fact_product"
                                  " WHERE asin=? AND campaign_id=? AND date BETWEEN ? AND ?"
                                  " AND system='placement'", pargs).fetchall():
                code = r["code"]
                item["placements"].append({"code": code, "label": code,
                                           "sum": p_sum("placement", code),
                                           "hours": p_hours("placement", code)})
            for r in conn.execute("SELECT DISTINCT code FROM fact_product"
                                  " WHERE asin=? AND campaign_id=? AND date BETWEEN ? AND ?"
                                  " AND system='match'", pargs).fetchall():
                code = r["code"]
                item["match"].append({"code": code, "label": code,
                                      "sum": p_sum("match", code),
                                      "hours": p_hours("match", code)})
            products.append(item)

        def srows(scope: str, owner: str, qtype: str) -> List[Dict[str, Any]]:
            rs = conn.execute(
                "SELECT term, match_type, SUM(impressions) imp, SUM(clicks) clk,"
                " SUM(cost) cost, SUM(orders) ord, SUM(add_to_cart) cart,"
                " MAX(extra_json) extra FROM fact_search_term"
                " WHERE campaign_id=? AND date BETWEEN ? AND ? AND scope=? AND owner_id=?"
                " AND query_type=? GROUP BY term, match_type ORDER BY cost DESC",
                [campaign_id, start, end, scope, owner, qtype]).fetchall()
            out = []
            for r in rs:
                try:
                    extra = json.loads(r["extra"] or "{}")
                except Exception:           # noqa: BLE001
                    extra = {}
                out.append({"query": r["term"], "matchType": r["match_type"],
                            "impressions": r["imp"] or 0, "clicks": r["clk"] or 0,
                            "adCost": round(r["cost"] or 0, 2),
                            "adOrderNum": r["ord"] or 0,
                            "addToCart": r["cart"] or 0, **extra})
            return out

        by_group = {}
        for g in conn.execute("SELECT DISTINCT owner_id FROM fact_search_term"
                              " WHERE campaign_id=? AND date BETWEEN ? AND ?"
                              " AND scope='group'", args).fetchall():
            gid = g["owner_id"]
            by_group[gid] = {"keyword": srows("group", gid, "keyword"),
                             "asin": srows("group", gid, "asin")}

        return {"date": f"{start} ~ {end}", "isRange": True,
                "campaign": camp, "groups": groups, "products": products,
                "search": {"keyword": srows("campaign", campaign_id, "keyword"),
                           "asin": srows("campaign", campaign_id, "asin"),
                           "byGroup": by_group}}
    finally:
        if own:
            conn.close()


def search_term_overview(campaign_id: str, start: str, end: str,
                          limit: int = 2000) -> List[Dict[str, Any]]:
    """搜索词数据总览：搜索词 + 活动/组/产品/SKU + 聚合指标。

    搜索词本身只有活动级和组级归因，没有产品级归因；产品/SKU 列展示
    该活动（或所在组能确定时）实际投放的产品集合，指标仍是搜索词自身指标。
    """
    own = False
    conn = get_conn()
    try:
        init(conn)
        camp = conn.execute(
            "SELECT name FROM dim_campaign WHERE campaign_id=?", (campaign_id,)).fetchone()
        campaign_name = str(camp["name"] if camp else "") or campaign_id

        product_rows = conn.execute("""
            SELECT DISTINCT fp.asin, dp.sku, dp.parent_asin, dp.title
            FROM fact_product fp
            LEFT JOIN dim_product dp ON dp.asin=fp.asin
            WHERE fp.campaign_id=? AND fp.date BETWEEN ? AND ?
            UNION
            SELECT cp.asin, COALESCE(dp.sku, cp.sku),
                   COALESCE(dp.parent_asin, cp.parent_asin), dp.title
            FROM dim_campaign_product cp
            LEFT JOIN dim_product dp ON dp.asin=cp.asin
            WHERE cp.campaign_id=?
        """, (campaign_id, start, end, campaign_id)).fetchall()
        products = [dict(r) for r in product_rows]
        parent_skus = {}
        for p in products:
            pa = str(p.get("parent_asin") or "")
            if pa:
                r = conn.execute("SELECT sku FROM dim_product WHERE asin=?", (pa,)).fetchone()
                if r and r["sku"]:
                    parent_skus[pa] = str(r["sku"])
        for p in products:
            sku = str(p.get("sku") or "")
            pa = str(p.get("parent_asin") or "")
            p["parent_sku"] = parent_skus.get(pa) or (sku.split("-")[0] if "-" in sku else sku)
        product_asins = sorted({str(p["asin"]) for p in products if p.get("asin")})
        child_skus = sorted({str(p["sku"]) for p in products if p.get("sku")})
        parent_sku_list = sorted({str(p["parent_sku"]) for p in products if p.get("parent_sku")})
        parent_asins = sorted({str(p["parent_asin"]) for p in products if p.get("parent_asin")})

        def query_scope(scope: str) -> List[Dict[str, Any]]:
            return [dict(r) for r in conn.execute("""
                SELECT term, match_type, owner_id, scope,
                       SUM(impressions) impressions, SUM(clicks) clicks,
                       SUM(cost) cost, SUM(orders) orders,
                       SUM(add_to_cart) add_to_cart,
                       COUNT(DISTINCT date) days,
                       MIN(date) first_date, MAX(date) last_date
                FROM fact_search_term
                WHERE campaign_id=? AND date BETWEEN ? AND ?
                  AND query_type='keyword' AND scope=?
                GROUP BY term, match_type, owner_id, scope
                ORDER BY SUM(cost) DESC, SUM(clicks) DESC, term
                LIMIT ?
            """, (campaign_id, start, end, scope, limit)).fetchall()]

        rows = query_scope("group")
        scope_used = "group"
        if not rows:
            rows = query_scope("campaign")
            scope_used = "campaign"

        group_names = {}
        for r in conn.execute("SELECT group_id, name FROM dim_group").fetchall():
            group_names[str(r["group_id"])] = str(r["name"] or "")

        out: List[Dict[str, Any]] = []
        for r in rows:
            imp = _num(r.get("impressions"))
            clicks = _num(r.get("clicks"))
            cost = _num(r.get("cost"))
            orders = _num(r.get("orders"))
            carts = _num(r.get("add_to_cart"))
            gid = str(r.get("owner_id") or "")
            out.append({
                "term": r.get("term") or "",
                "match_type": r.get("match_type") or "",
                "scope": scope_used,
                "campaign_id": campaign_id,
                "campaign_name": campaign_name,
                "group_id": gid if scope_used == "group" else "",
                "group_name": (group_names.get(gid) if scope_used == "group" else "") or "活动级",
                "product_asins": product_asins,
                "child_skus": child_skus,
                "parent_skus": parent_sku_list,
                "parent_asins": parent_asins,
                "impressions": imp,
                "clicks": clicks,
                "cost": round(cost, 2),
                "orders": orders,
                "add_to_cart": carts,
                "days": int(r.get("days") or 0),
                "first_date": r.get("first_date") or "",
                "last_date": r.get("last_date") or "",
                "ctr": round(clicks / imp * 100, 2) if imp else None,
                "cpc": round(cost / clicks, 2) if clicks else None,
                "cpa": round(cost / orders, 2) if orders else None,
            })
        return out
    finally:
        conn.close()


def search_term_overview_all(start: str, end: str,
                             limit: int = 2000,
                             query_type: str = "keyword") -> List[Dict[str, Any]]:
    """跨所有广告活动的搜索数据，按花费从高到低。

    query_type=keyword 为搜索词，query_type=asin 为搜索 ASIN。
    """
    conn = get_conn()
    try:
        init(conn)
        campaign_names = {
            str(r["campaign_id"]): str(r["name"] or "")
            for r in conn.execute("SELECT campaign_id, name FROM dim_campaign").fetchall()
        }
        group_names = {}
        for r in conn.execute("SELECT group_id, amazon_group_id, name FROM dim_group").fetchall():
            nm = str(r["name"] or "")
            if r["group_id"]:
                group_names[str(r["group_id"])] = nm
            # 历史搜索词 owner_id 可能存的是 Amazon 组 id，两个键都映射。
            if r["amazon_group_id"]:
                group_names[str(r["amazon_group_id"])] = nm

        # 产品集合按活动汇总；搜索词本身只有活动/组级归因。
        prod_rows = conn.execute("""
            SELECT DISTINCT fp.campaign_id, fp.asin, dp.sku, dp.parent_asin
            FROM fact_product fp
            LEFT JOIN dim_product dp ON dp.asin=fp.asin
            WHERE fp.date BETWEEN ? AND ?
            UNION
            SELECT cp.campaign_id, cp.asin, COALESCE(dp.sku, cp.sku),
                   COALESCE(dp.parent_asin, cp.parent_asin)
            FROM dim_campaign_product cp
            LEFT JOIN dim_product dp ON dp.asin=cp.asin
        """, (start, end)).fetchall()
        parent_sku = {}
        for r in conn.execute("SELECT asin, sku FROM dim_product WHERE sku IS NOT NULL"):
            if r["asin"] and r["sku"]:
                parent_sku[str(r["asin"])] = str(r["sku"])
        products_by_campaign: Dict[str, List[Dict[str, Any]]] = {}
        for r in prod_rows:
            cid = str(r["campaign_id"] or "")
            if not cid:
                continue
            sku = str(r["sku"] or "")
            pa = str(r["parent_asin"] or "")
            products_by_campaign.setdefault(cid, []).append({
                "asin": str(r["asin"] or ""),
                "sku": sku,
                "parent_asin": pa,
                "parent_sku": parent_sku.get(pa) or (sku.split("-")[0] if "-" in sku else sku),
            })

        def grouped(scope: str) -> List[Dict[str, Any]]:
            return [dict(r) for r in conn.execute("""
                SELECT campaign_id, term, owner_id,
                       GROUP_CONCAT(DISTINCT match_type) match_types,
                       SUM(impressions) impressions, SUM(clicks) clicks,
                       SUM(cost) cost, SUM(orders) orders,
                       SUM(add_to_cart) add_to_cart,
                       COUNT(DISTINCT date) days,
                       MIN(date) first_date, MAX(date) last_date
                FROM fact_search_term
                WHERE date BETWEEN ? AND ? AND query_type=? AND scope=?
                GROUP BY campaign_id, term, owner_id
                ORDER BY SUM(cost) DESC, SUM(clicks) DESC, term
                LIMIT ?
            """, (start, end, query_type, scope, limit)).fetchall()]

        rows = grouped("group")
        scope_used = "group"
        if not rows:
            rows = grouped("campaign")
            scope_used = "campaign"

        out: List[Dict[str, Any]] = []
        for r in rows:
            cid = str(r["campaign_id"] or "")
            gid = str(r["owner_id"] or "") if scope_used == "group" else ""
            prods = products_by_campaign.get(cid, [])
            imp = _num(r.get("impressions"))
            clicks = _num(r.get("clicks"))
            cost = _num(r.get("cost"))
            orders = _num(r.get("orders"))
            out.append({
                "campaign_id": cid,
                "campaign_name": campaign_names.get(cid, cid),
                "group_id": gid,
                "group_name": (group_names.get(gid) if gid else "") or "活动级",
                "scope": scope_used,
                "term": r.get("term") or "",
                "match_type": r.get("match_types") or "",
                "product_asins": sorted({p["asin"] for p in prods if p["asin"]}),
                "child_skus": sorted({p["sku"] for p in prods if p["sku"]}),
                "parent_skus": sorted({p["parent_sku"] for p in prods if p["parent_sku"]}),
                "parent_asins": sorted({p["parent_asin"] for p in prods if p["parent_asin"]}),
                "impressions": imp,
                "clicks": clicks,
                "cost": round(cost, 2),
                "orders": orders,
                "add_to_cart": _num(r.get("add_to_cart")),
                "days": int(r.get("days") or 0),
                "first_date": r.get("first_date") or "",
                "last_date": r.get("last_date") or "",
                "ctr": round(clicks / imp * 100, 2) if imp else None,
                "cpc": round(cost / clicks, 2) if clicks else None,
                "cpa": round(cost / orders, 2) if orders else None,
            })
        return out
    finally:
        conn.close()


def parents_of(campaign_id: str,
               conn: Optional[sqlite3.Connection] = None) -> List[Dict[str, Any]]:
    """该活动涉及的「父体」信息（图片 / 标题 / 子体数 / 价格区间 / 商品标识）"""
    own = conn is None
    conn = conn or get_conn()
    try:
        init(conn)
        asins = [r["asin"] for r in conn.execute(
            "SELECT DISTINCT asin FROM fact_product WHERE campaign_id=?", (campaign_id,))]
        if not asins:
            # 没有该活动的产品事实时，不能把全库产品当成该活动产品。
            # 只用活动名中的父 ASIN 作为兜底；没有就返回空。
            row = conn.execute(
                "SELECT name FROM dim_campaign WHERE campaign_id=?", (campaign_id,)).fetchone()
            m = re.search(r"(B0[A-Z0-9]{8})", str(row["name"] if row else ""))
            if m:
                asins = [m.group(1)]

        parents: List[str] = []
        for a in asins:
            p = ""
            r = conn.execute("SELECT parent_asin FROM online_products WHERE asin=?",
                             (a,)).fetchone()
            if r and r["parent_asin"]:
                p = str(r["parent_asin"])
            if not p:
                r2 = conn.execute("SELECT parent_asin FROM dim_product WHERE asin=?",
                                  (a,)).fetchone()
                if r2 and r2["parent_asin"]:
                    p = str(r2["parent_asin"])
            p = p or a
            if p not in parents:
                parents.append(p)

        out: List[Dict[str, Any]] = []
        for p in parents:
            row = conn.execute(
                "SELECT asin, title, sku, main_image, raw_json FROM online_products"
                " WHERE asin=? LIMIT 1", (p,)).fetchone()
            title, img, big, sku = "", "", "", ""
            if row:
                sku = str(row["sku"] or "")
                title = str(row["title"] or "")
                img = str(row["main_image"] or "")
                try:
                    raw = json.loads(row["raw_json"] or "{}")
                    img = raw.get("mainImage") or img
                    big = raw.get("mainBigImage") or ""
                except Exception:           # noqa: BLE001
                    pass
            # 子体清单（同一父体）
            kids = conn.execute(
                "SELECT asin, main_image, raw_json FROM online_products"
                " WHERE parent_asin=? AND asin<>?", (p, p)).fetchall()
            if not kids:
                kids = conn.execute(
                    "SELECT asin, main_image, raw_json FROM online_products"
                    " WHERE asin IN (%s)" % ",".join("?" * len(asins)),
                    asins).fetchall() if asins else []
            prices = []
            alt_img = ""
            for k in kids:
                if not alt_img and k["main_image"]:
                    alt_img = str(k["main_image"])
                try:
                    raw = json.loads(k["raw_json"] or "{}")
                    v = raw.get("standardPrice") or raw.get("landedPrice")
                    if v:
                        prices.append(float(v))
                except Exception:           # noqa: BLE001
                    pass
            lab = conn.execute("SELECT label FROM ol_ai_labels WHERE asin=?",
                               (p,)).fetchone()
            label = str(lab["label"]) if lab and lab["label"] else ""
            cents = sorted({round(x, 2) for x in prices})
            out.append({
                "parentAsin": p,
                "title": title,
                "image": img or alt_img,
                "bigImage": big or img or alt_img,
                "label": label, "sku": sku,
                "childCount": len(kids) if kids else 1,
                "priceMin": cents[0] if cents else None,
                "priceMax": cents[-1] if cents else None,
                "asinCount": sum(1 for a in asins
                                 if a == p or a in {k["asin"] for k in kids}),
            })
        return out
    finally:
        if own:
            conn.close()
