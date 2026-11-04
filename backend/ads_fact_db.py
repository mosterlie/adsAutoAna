# -*- coding: utf-8 -*-
"""广告事实库：三个体系各一张表

    体系 1 广告总量  → fact_ads_total      (日期 × 时段 × 广告产品)
    体系 2 广告位    → fact_ads_placement  (日期 × 时段 × 广告位)
    体系 3 关联方式  → fact_ads_match      (日期 × 时段 × 关联方式)

字典统一在 dim_dict 一张表（field_code 区分字典种类）：
    field_code = 1  体系      parent_code = 0
    field_code = 2  子分类    parent_code = 体系编码（联动）

体系编码 / 子分类编码
    1  广告总量        子分类 1 广告总量
    2  广告位          子分类 1 搜索结果顶部（首页） / 2 搜索结果其余位置
                              3 商品详情页 / 4 Off Amazon
    3  关联方式        子分类 1 宽泛匹配 / 2 紧密匹配 / 3 同类商品 / 4 关联产品

广告产品 product_code
    0    不区分产品（兜底）
    1..N 具体产品：由「广告组合 × 广告活动 × 广告组 × SKU」唯一确定，
         同一 SKU 挂在不同广告组下是不同产品实体
"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import config

DB_PATH = os.path.join(config.DATA_DIR, "ads_metrics.db")

FIELD_SYSTEM = 1
FIELD_SUBCAT = 2

SYSTEM_ALL = 1
SYSTEM_PLACEMENT = 2
SYSTEM_MATCH = 3

TABLES = {
    SYSTEM_ALL: "fact_ads_total",
    SYSTEM_PLACEMENT: "fact_ads_placement",
    SYSTEM_MATCH: "fact_ads_match",
}

PRODUCT_ALL = 0                    # 不区分产品

# (field_code, parent_code, item_code, item_name, source_key, remark)
DICT_ROWS = [
    (FIELD_SYSTEM, 0, SYSTEM_ALL, "广告总量", None, "对应 fact_ads_total"),
    (FIELD_SYSTEM, 0, SYSTEM_PLACEMENT, "广告位", None, "对应 fact_ads_placement"),
    (FIELD_SYSTEM, 0, SYSTEM_MATCH, "关联方式", None, "对应 fact_ads_match"),

    (FIELD_SUBCAT, SYSTEM_ALL, 1, "广告总量", None, ""),

    (FIELD_SUBCAT, SYSTEM_PLACEMENT, 1, "搜索结果顶部（首页）", "placementTop", ""),
    (FIELD_SUBCAT, SYSTEM_PLACEMENT, 2, "搜索结果其余位置", "placementRestOfSearch", ""),
    (FIELD_SUBCAT, SYSTEM_PLACEMENT, 3, "商品详情页", "placementProductPage", ""),
    (FIELD_SUBCAT, SYSTEM_PLACEMENT, 4, "Off Amazon", "offAmazon", ""),

    (FIELD_SUBCAT, SYSTEM_MATCH, 1, "宽泛匹配", "queryBroadRelMatches", ""),
    (FIELD_SUBCAT, SYSTEM_MATCH, 2, "紧密匹配", "queryHighRelMatches", ""),
    (FIELD_SUBCAT, SYSTEM_MATCH, 3, "同类商品", "asinSubstituteRelated", "substitutes"),
    (FIELD_SUBCAT, SYSTEM_MATCH, 4, "关联产品", "asinAccessoryRelated", "complements"),
]

METRIC_COLUMNS = ["currency", "impressions", "clicks", "cost", "orders", "sales",
                  "metrics_json", "source", "fetched_at"]

TOTAL_COLUMNS = ["stat_date", "hour", "shop_id", "product_code"] + METRIC_COLUMNS
PLACEMENT_COLUMNS = ["stat_date", "hour", "shop_id", "product_code",
                     "placement_code"] + METRIC_COLUMNS
MATCH_COLUMNS = ["stat_date", "hour", "shop_id", "product_code",
                 "match_code"] + METRIC_COLUMNS

TABLE_COLUMNS = {
    "fact_ads_total": TOTAL_COLUMNS,
    "fact_ads_placement": PLACEMENT_COLUMNS,
    "fact_ads_match": MATCH_COLUMNS,
}

SCHEMA = """
PRAGMA journal_mode=WAL;

-- 统一字段字典：field_code 区分子典，parent_code 表达上下级联动
CREATE TABLE IF NOT EXISTS dim_dict (
    field_code  INTEGER NOT NULL,            -- 1=体系 2=子分类
    parent_code INTEGER NOT NULL DEFAULT 0,  -- 子分类：所属体系
    item_code   INTEGER NOT NULL,            -- 字典项编码
    item_name   TEXT NOT NULL,
    source_key  TEXT,                        -- 赛狐原始枚举，None=无原始枚举
    remark      TEXT,
    PRIMARY KEY (field_code, parent_code, item_code)
);

-- 广告产品：广告组合 × 广告活动 × 广告组 × SKU 唯一
CREATE TABLE IF NOT EXISTS dim_product (
    product_code   INTEGER PRIMARY KEY AUTOINCREMENT,   -- 0 = 不区分产品
    shop_id        TEXT NOT NULL DEFAULT '',
    portfolio_name TEXT NOT NULL DEFAULT '',            -- 广告组合
    campaign_id    TEXT NOT NULL DEFAULT '',            -- 广告活动
    ad_group_id    TEXT NOT NULL DEFAULT '',            -- 广告组
    ad_id          TEXT NOT NULL DEFAULT '',
    product_key    TEXT NOT NULL,                       -- sku 优先，缺失用 asin
    asin           TEXT,
    sku            TEXT,
    title          TEXT,
    updated_at     TEXT,
    UNIQUE (shop_id, portfolio_name, campaign_id, ad_group_id, product_key)
);

-- 体系 1：广告总量（日期 × 时段 × 广告产品）
CREATE TABLE IF NOT EXISTS fact_ads_total (
    stat_date    TEXT    NOT NULL,            -- yyyy-mm-dd
    hour         INTEGER NOT NULL,            -- 0..23；日汇总 = -1
    shop_id      TEXT    NOT NULL DEFAULT '',
    product_code INTEGER NOT NULL DEFAULT 0,  -- 广告产品
    currency     TEXT,
    impressions  INTEGER NOT NULL DEFAULT 0,
    clicks       INTEGER NOT NULL DEFAULT 0,
    cost         REAL    NOT NULL DEFAULT 0,
    orders       INTEGER NOT NULL DEFAULT 0,
    sales        REAL    NOT NULL DEFAULT 0,
    metrics_json TEXT,
    source       TEXT NOT NULL DEFAULT 'sellfox',
    fetched_at   TEXT,
    PRIMARY KEY (stat_date, hour, shop_id, product_code),
    FOREIGN KEY (product_code) REFERENCES dim_product (product_code)
);

-- 体系 2：广告位（日期 × 时段 × 广告产品 × 广告位）
CREATE TABLE IF NOT EXISTS fact_ads_placement (
    stat_date      TEXT    NOT NULL,
    hour           INTEGER NOT NULL,
    shop_id        TEXT    NOT NULL DEFAULT '',
    product_code   INTEGER NOT NULL DEFAULT 0,    -- 广告产品
    placement_code INTEGER NOT NULL DEFAULT 0,    -- 子分类 1..4
    currency       TEXT,
    impressions    INTEGER NOT NULL DEFAULT 0,
    clicks         INTEGER NOT NULL DEFAULT 0,
    cost           REAL    NOT NULL DEFAULT 0,
    orders         INTEGER NOT NULL DEFAULT 0,
    sales          REAL    NOT NULL DEFAULT 0,
    metrics_json   TEXT,
    source         TEXT NOT NULL DEFAULT 'sellfox',
    fetched_at     TEXT,
    PRIMARY KEY (stat_date, hour, shop_id, product_code, placement_code),
    FOREIGN KEY (product_code) REFERENCES dim_product (product_code)
);

-- 体系 3：关联方式（日期 × 时段 × 广告产品 × 关联方式）
CREATE TABLE IF NOT EXISTS fact_ads_match (
    stat_date   TEXT    NOT NULL,
    hour        INTEGER NOT NULL,
    shop_id     TEXT    NOT NULL DEFAULT '',
    product_code INTEGER NOT NULL DEFAULT 0,      -- 广告产品
    match_code  INTEGER NOT NULL DEFAULT 0,       -- 子分类 1..4
    currency    TEXT,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks      INTEGER NOT NULL DEFAULT 0,
    cost        REAL    NOT NULL DEFAULT 0,
    orders      INTEGER NOT NULL DEFAULT 0,
    sales       REAL    NOT NULL DEFAULT 0,
    metrics_json TEXT,
    source      TEXT NOT NULL DEFAULT 'sellfox',
    fetched_at  TEXT,
    PRIMARY KEY (stat_date, hour, shop_id, product_code, match_code),
    FOREIGN KEY (product_code) REFERENCES dim_product (product_code)
);

CREATE INDEX IF NOT EXISTS idx_total_date_hour
    ON fact_ads_total (stat_date, hour);
CREATE INDEX IF NOT EXISTS idx_total_product
    ON fact_ads_total (product_code, stat_date);
CREATE INDEX IF NOT EXISTS idx_placement_date_hour
    ON fact_ads_placement (stat_date, hour);
CREATE INDEX IF NOT EXISTS idx_placement_product
    ON fact_ads_placement (product_code, stat_date);
CREATE INDEX IF NOT EXISTS idx_match_date_hour
    ON fact_ads_match (stat_date, hour);
CREATE INDEX IF NOT EXISTS idx_match_product
    ON fact_ads_match (product_code, stat_date);

-- 产品级采集日志：断点续跑 + 失败重试
CREATE TABLE IF NOT EXISTS etl_product_pull (
    stat_date    TEXT    NOT NULL,
    shop_id      TEXT    NOT NULL DEFAULT '',
    product_code INTEGER NOT NULL,
    ad_id        TEXT,
    status       TEXT    NOT NULL,            -- success / zero / failed
    rows_written INTEGER NOT NULL DEFAULT 0,
    message      TEXT,
    updated_at   TEXT,
    PRIMARY KEY (stat_date, shop_id, product_code)
);
"""

LEGACY_TABLES = ("dim_system", "dim_sub_category", "fact_ads_slice", "fact_ads_system")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _columns(conn: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.OperationalError:
        return []


def drop_legacy(conn: sqlite3.Connection) -> List[str]:
    """清理旧结构：分散字典表、缺 portfolio_name 的旧 dim_product、合并版事实表"""
    stale: List[str] = []
    prod_cols = _columns(conn, "dim_product")
    if prod_cols and "portfolio_name" not in prod_cols:
        stale += ["fact_ads_slice", "dim_product"]
    if _columns(conn, "dim_system"):
        stale += ["dim_system", "dim_sub_category"]
    stale += [t for t in ("fact_ads_slice",) if _columns(conn, t)]
    plc = _columns(conn, "fact_ads_placement")
    if plc and "product_code" not in plc:
        stale += ["fact_ads_total", "fact_ads_placement", "fact_ads_match"]
    if not stale:
        return []

    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    dropped = []
    for name in dict.fromkeys(stale):
        if _columns(conn, name):
            conn.execute(f"DROP TABLE {name}")
            dropped.append(name)
    conn.commit()
    conn.execute("PRAGMA foreign_keys=ON")
    return dropped


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.executescript(SCHEMA)
        seed_dicts(conn)
        conn.commit()
    finally:
        if own:
            conn.close()


def seed_dicts(conn: sqlite3.Connection) -> None:
    """字典幂等写入；产品 0 = 不区分产品"""
    conn.executemany(
        "INSERT OR REPLACE INTO dim_dict"
        " (field_code, parent_code, item_code, item_name, source_key, remark)"
        " VALUES (?,?,?,?,?,?)", DICT_ROWS)
    conn.execute(
        "INSERT OR IGNORE INTO dim_product"
        " (product_code, shop_id, portfolio_name, campaign_id, ad_group_id,"
        "  ad_id, product_key, asin, sku, title, updated_at)"
        " VALUES (0, '', '', '', '', '', '*', '', '', '不区分产品', ?)",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))


def subcat_map(conn: sqlite3.Connection) -> Dict[Any, int]:
    """{(system_code, source_key): sub_code}"""
    return {(int(r["parent_code"]), r["source_key"]): int(r["item_code"])
            for r in conn.execute(
                "SELECT parent_code, item_code, source_key FROM dim_dict"
                " WHERE field_code = ? AND source_key IS NOT NULL", (FIELD_SUBCAT,))}


def product_code_for(conn: sqlite3.Connection, shop_id: str, *, asin: str = "",
                     sku: str = "", title: str = "", portfolio_name: str = "",
                     campaign_id: str = "", ad_group_id: str = "",
                     ad_id: str = "") -> int:
    """广告组合 × 广告活动 × 广告组 × 产品 唯一；没有就新建"""
    key = (sku or asin or "").strip()
    if not key:
        return PRODUCT_ALL
    scope = (str(shop_id), portfolio_name or "", campaign_id or "", ad_group_id or "")
    row = conn.execute(
        "SELECT product_code FROM dim_product"
        " WHERE shop_id=? AND portfolio_name=? AND campaign_id=?"
        "   AND ad_group_id=? AND product_key=?", scope + (key,)).fetchone()
    if row:
        return int(row["product_code"])
    cur = conn.execute(
        "INSERT INTO dim_product"
        " (shop_id, portfolio_name, campaign_id, ad_group_id, ad_id,"
        "  product_key, asin, sku, title, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        scope + (ad_id or "", key, asin, sku, title,
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    return int(cur.lastrowid)


def upsert(conn: sqlite3.Connection, table: str, rows: Iterable[Dict[str, Any]]) -> int:
    """按表自带的列清单写入，主键冲突即覆盖（幂等）"""
    cols = TABLE_COLUMNS[table]
    sql = (f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")
    n = 0
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c)
            if c == "metrics_json" and not isinstance(v, (str, type(None))):
                v = json.dumps(v, ensure_ascii=False)
            if v is None:
                v = 0 if c in ("hour", "product_code", "placement_code", "match_code",
                               "impressions", "clicks", "cost", "orders", "sales") else ""
            vals.append(v)
        conn.execute(sql, vals)
        n += 1
    return n


def query(conn: sqlite3.Connection, system_code: int, stat_date: str, *,
          code: Optional[int] = None, hour: Optional[int] = None,
          campaign_id: Optional[str] = None,
          ad_group_id: Optional[str] = None) -> List[sqlite3.Row]:
    """按体系查对应表；code = 产品编码 / 广告位编码 / 关联方式编码"""
    table = TABLES[system_code]
    code_col = {SYSTEM_ALL: "product_code",
                SYSTEM_PLACEMENT: "placement_code",
                SYSTEM_MATCH: "match_code"}[system_code]

    if system_code == SYSTEM_ALL:
        sql = ("SELECT f.*, p.portfolio_name, p.campaign_id, p.ad_group_id,"
               "       p.asin, p.sku"
               "  FROM fact_ads_total f"
               "  LEFT JOIN dim_product p ON p.product_code = f.product_code"
               " WHERE f.stat_date = ?")
    else:
        sql = (f"SELECT f.*, d.item_name AS code_name,"
               f"       p.portfolio_name, p.campaign_id, p.ad_group_id,"
               f"       p.asin, p.sku"
               f"  FROM {table} f"
               f"  LEFT JOIN dim_dict d ON d.field_code = 2"
               f"                      AND d.parent_code = {system_code}"
               f"                      AND d.item_code = f.{code_col}"
               f"  LEFT JOIN dim_product p ON p.product_code = f.product_code"
               f" WHERE f.stat_date = ?")
    args: List[Any] = [stat_date]
    if code is not None:
        sql += f" AND f.{code_col} = ?"
        args.append(code)
    if hour is not None:
        sql += " AND f.hour = ?"
        args.append(hour)
    if campaign_id:
        sql += " AND p.campaign_id = ?"
        args.append(str(campaign_id))
    if ad_group_id:
        sql += " AND p.ad_group_id = ?"
        args.append(str(ad_group_id))
    sql += f" ORDER BY f.hour, f.{code_col}"
    return conn.execute(sql, args).fetchall()


def log_product_pull(conn: sqlite3.Connection, stat_date: str, shop_id: str,
                     product_code: int, ad_id: str, status: str,
                     rows: int = 0, message: str = "") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO etl_product_pull"
        " (stat_date, shop_id, product_code, ad_id, status, rows_written,"
        "  message, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (stat_date, str(shop_id), int(product_code), ad_id or "", status, rows,
         message[:500],
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))


def pulled_products(conn: sqlite3.Connection, stat_date: str,
                    shop_id: str) -> Dict[int, str]:
    """{product_code: status}，用于 --resume"""
    return {int(r["product_code"]): r["status"] for r in conn.execute(
        "SELECT product_code, status FROM etl_product_pull"
        " WHERE stat_date=? AND shop_id=?", (stat_date, str(shop_id)))}


def dump_dicts() -> Dict[str, List[tuple]]:
    """打印用：体系 / 子分类 / 产品"""
    conn = get_conn()
    try:
        systems = [tuple(r) for r in conn.execute(
            "SELECT item_code, item_name, remark FROM dim_dict"
            " WHERE field_code = ? AND parent_code = 0 ORDER BY item_code", (FIELD_SYSTEM,))]
        subs = [tuple(r) for r in conn.execute(
            "SELECT parent_code, item_code, item_name, source_key FROM dim_dict"
            " WHERE field_code = ? ORDER BY parent_code, item_code", (FIELD_SUBCAT,))]
        prods = [tuple(r) for r in conn.execute(
            "SELECT product_code, portfolio_name, campaign_id, ad_group_id, asin, sku"
            "  FROM dim_product ORDER BY product_code")]
    finally:
        conn.close()
    return {"system": systems, "sub": subs, "product": prods}
