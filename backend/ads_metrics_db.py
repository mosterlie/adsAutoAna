# -*- coding: utf-8 -*-
"""广告分时指标库（独立 DB，避免与业务库 ads.db 争锁）

表结构（星型）：
    dim_campaign / dim_ad_group / dim_ad / dim_target / dim_placement   维度表
    fact_ads_performance                                                 事实表（小时 / 日 两种周期）
    etl_pull_log                                                         抓取任务日志（幂等 + 断点续跑）

事实表设计要点：
    · 一行 = 一个「周期 × 维度组合」，维度键用 '' 占位（SQLite 主键不允许 NULL）
    · grain 标识这一行属于哪个粒度：campaign / ad_group / ad / target / placement
      / target_placement / target_ad
    · 核心指标拆成列便于聚合，全部原始指标再原样存 metrics_json（72~75 个）
    · 主键覆盖全部维度键 + 周期 → 重复抓取直接覆盖，天然幂等
"""
import json
import os
import sqlite3
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import config

DB_PATH = os.path.join(config.DATA_DIR, "ads_metrics.db")

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS dim_campaign (
    campaign_id      TEXT PRIMARY KEY,
    shop_id          TEXT NOT NULL DEFAULT '',
    ad_type          TEXT NOT NULL DEFAULT '',
    name             TEXT,
    state            TEXT,
    serving_status   TEXT,
    targeting_type   TEXT,           -- auto / manual
    daily_budget     REAL,
    bidding_strategy TEXT,
    portfolio_name   TEXT,
    currency         TEXT,
    start_date       TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS dim_ad_group (
    ad_group_id      TEXT PRIMARY KEY,
    campaign_id      TEXT NOT NULL DEFAULT '',
    shop_id          TEXT NOT NULL DEFAULT '',
    ad_type          TEXT NOT NULL DEFAULT '',
    name             TEXT,
    group_type       TEXT,           -- auto / manual
    default_bid      REAL,
    state            TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS dim_ad (
    ad_id            TEXT PRIMARY KEY,
    ad_group_id      TEXT NOT NULL DEFAULT '',
    campaign_id      TEXT NOT NULL DEFAULT '',
    shop_id          TEXT NOT NULL DEFAULT '',
    ad_type          TEXT NOT NULL DEFAULT '',
    asin             TEXT,
    sku              TEXT,
    title            TEXT,
    state            TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS dim_target (
    target_id        TEXT PRIMARY KEY,
    ad_group_id      TEXT NOT NULL DEFAULT '',
    campaign_id      TEXT NOT NULL DEFAULT '',
    shop_id          TEXT NOT NULL DEFAULT '',
    ad_type          TEXT NOT NULL DEFAULT '',
    target_type      TEXT,           -- auto / keyword / product
    target_text      TEXT,           -- queryBroadRelMatches / 关键词 / ASIN
    match_type       TEXT,
    bid              REAL,
    state            TEXT,
    serving_status   TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS dim_placement (
    placement        TEXT PRIMARY KEY,
    name             TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS fact_ads_performance (
    period_type      TEXT NOT NULL,              -- 'hour' | 'day'
    stat_date        TEXT NOT NULL,              -- yyyy-mm-dd
    hour             INTEGER NOT NULL,           -- 0..23；日汇总 = -1
    grain            TEXT NOT NULL,              -- 见文件头
    shop_id          TEXT NOT NULL DEFAULT '',
    campaign_id      TEXT NOT NULL DEFAULT '',
    ad_group_id      TEXT NOT NULL DEFAULT '',
    ad_id            TEXT NOT NULL DEFAULT '',
    target_id        TEXT NOT NULL DEFAULT '',
    placement        TEXT NOT NULL DEFAULT '',
    asin             TEXT NOT NULL DEFAULT '',
    sku              TEXT NOT NULL DEFAULT '',
    currency         TEXT,
    impressions      INTEGER NOT NULL DEFAULT 0,
    clicks           INTEGER NOT NULL DEFAULT 0,
    cost             REAL    NOT NULL DEFAULT 0,
    orders           INTEGER NOT NULL DEFAULT 0,
    sales            REAL    NOT NULL DEFAULT 0,
    metrics_json     TEXT,                       -- 全量指标（含 ctr/cpc/acos/roas/cvr/加购…）
    source           TEXT NOT NULL DEFAULT 'sellfox',
    fetched_at       TEXT,
    PRIMARY KEY (period_type, stat_date, hour, grain, shop_id,
                 campaign_id, ad_group_id, ad_id, target_id, placement, asin, sku)
);

CREATE INDEX IF NOT EXISTS idx_fact_date_grain ON fact_ads_performance (stat_date, grain);
CREATE INDEX IF NOT EXISTS idx_fact_campaign   ON fact_ads_performance (campaign_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_fact_adgroup    ON fact_ads_performance (ad_group_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_fact_ad         ON fact_ads_performance (ad_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_fact_target     ON fact_ads_performance (target_id, stat_date);
CREATE INDEX IF NOT EXISTS idx_fact_asin       ON fact_ads_performance (asin, stat_date);

CREATE TABLE IF NOT EXISTS etl_pull_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    period_type  TEXT NOT NULL,          -- hour / day
    stat_date    TEXT NOT NULL,
    scope        TEXT NOT NULL,          -- campaign:<id> 或 shop:<id>
    grain        TEXT NOT NULL,
    status       TEXT NOT NULL,          -- running / success / failed
    rows_written INTEGER NOT NULL DEFAULT 0,
    message      TEXT,
    started_at   TEXT,
    finished_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_log_date ON etl_pull_log (stat_date, scope);
"""

DIM_COLUMNS = {
    "dim_campaign": ["campaign_id", "shop_id", "ad_type", "name", "state", "serving_status",
                     "targeting_type", "daily_budget", "bidding_strategy", "portfolio_name",
                     "currency", "start_date", "updated_at"],
    "dim_ad_group": ["ad_group_id", "campaign_id", "shop_id", "ad_type", "name", "group_type",
                     "default_bid", "state", "updated_at"],
    "dim_ad": ["ad_id", "ad_group_id", "campaign_id", "shop_id", "ad_type", "asin", "sku",
               "title", "state", "updated_at"],
    "dim_target": ["target_id", "ad_group_id", "campaign_id", "shop_id", "ad_type", "target_type",
                   "target_text", "match_type", "bid", "state", "serving_status", "updated_at"],
    "dim_placement": ["placement", "name", "updated_at"],
}

FACT_COLUMNS = ["period_type", "stat_date", "hour", "grain", "shop_id", "campaign_id",
                "ad_group_id", "ad_id", "target_id", "placement", "asin", "sku", "currency",
                "impressions", "clicks", "cost", "orders", "sales", "metrics_json", "source",
                "fetched_at"]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    own = conn is None
    conn = conn or get_conn()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


def upsert_dim(conn: sqlite3.Connection, table: str, row: Dict[str, Any]) -> None:
    cols = [c for c in DIM_COLUMNS[table] if c in row]
    if not cols:
        return
    sql = (f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) "
           f"VALUES ({','.join('?' * len(cols))})")
    conn.execute(sql, [row[c] for c in cols])


def upsert_facts(conn: sqlite3.Connection, rows: Iterable[Dict[str, Any]]) -> int:
    sql = (f"INSERT OR REPLACE INTO fact_ads_performance ({','.join(FACT_COLUMNS)}) "
           f"VALUES ({','.join('?' * len(FACT_COLUMNS))})")
    n = 0
    for r in rows:
        vals = []
        for c in FACT_COLUMNS:
            v = r.get(c)
            if c == "metrics_json" and not isinstance(v, (str, type(None))):
                v = json.dumps(v, ensure_ascii=False)
            if v is None and c not in ("currency", "metrics_json", "fetched_at"):
                v = "" if c in ("stat_date", "grain", "shop_id", "campaign_id", "ad_group_id",
                                "ad_id", "target_id", "placement", "asin", "sku", "period_type") else 0
            vals.append(v)
        vals[FACT_COLUMNS.index("hour")] = int(r.get("hour", -1))
        conn.execute(sql, vals)
        n += 1
    return n


def log_pull(conn: sqlite3.Connection, *, period_type: str, stat_date: str, scope: str,
             grain: str, status: str, rows: int = 0, message: str = "",
             started_at: Optional[str] = None) -> int:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        "INSERT INTO etl_pull_log (period_type, stat_date, scope, grain, status, rows_written,"
        " message, started_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (period_type, stat_date, scope, grain, status, rows, message,
         started_at or now, None if status == "running" else now))
    conn.commit()
    return cur.lastrowid


def summarize(conn: sqlite3.Connection, stat_date: str) -> List[sqlite3.Row]:
    return conn.execute(
        "SELECT grain, period_type, COUNT(*) AS rows,"
        "       SUM(impressions) AS impressions, SUM(clicks) AS clicks,"
        "       ROUND(SUM(cost), 2) AS cost"
        "  FROM fact_ads_performance WHERE stat_date = ?"
        " GROUP BY grain, period_type ORDER BY grain, period_type", (stat_date,)).fetchall()
