# -*- coding: utf-8 -*-
"""SQLite 落库层: 店铺/广告组合/广告记录/汇总/采集批次"""
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import config
from backend.sellfox_client import scope_str

_LOCK = threading.Lock()

# 排序字段白名单: 仅允许 JSON 键名形态 (字母/数字/下划线), 杜绝 sql 注入
_FIELD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# 数值型指标字段: 这些字段在 raw_json 中以字符串存储(如 adCost="1936.57"),
# 排序时必须 CAST 成数值, 否则按字典序 ("93" > "1936") 结果错误。
# 与前端 frontend/app.js 的 NUM_KEYS 保持一致。
NUMERIC_FIELDS = {
    "impressions", "clicks", "ctr", "cvr", "adCost", "cpm", "vcpm", "adCostPerClick",
    "cpc", "adOrderNum", "adSaleNum", "adSales", "adSale", "orderNum", "sales",
    "cpa", "acos", "roas", "acots", "asots", "acoas", "asoas",
    "adOrderNumPercentage", "adSalePercentage", "adSalesPercentage", "clickPercentage",
    "impressionsPercentage", "orderNumPercentage", "adSelfSaleNum", "adOtherSaleNum",
    "adSelfOrderNum", "adOtherOrderNum", "adSelfSales", "adOtherSales", "searchFrequencyRank",
    "weekRatio", "impressionRank", "dailyBudget", "defaultBid", "bid", "originalBid",
    "budgetUsage", "overBudgetCount", "viewImpressions", "addToCart", "addToCartRate",
    "brandedSearches", "detailPageViews", "cumulativeReach", "impressionsFrequencyAverage",
    "video5SecondViews", "video5SecondViewRate", "videoCompleteViews", "videoUnmutes",
    "viewabilityRate", "viewClickThroughRate", "topImpressionShare", "impressionShare",
    "maxTopIs", "suggestedBid", "budget", "spend",
}


def is_safe_field(field: str) -> bool:
    """排序字段是否合法 (仅 JSON 键名形态, 防注入)"""
    return bool(field) and bool(_FIELD_RE.match(field))


def build_order_clause(order_field: str, order_dir: str = "desc") -> str:
    """构造安全的 ORDER BY 子句。

    - 字段名必须通过白名单正则, 否则抛 ValueError (防 sql 注入)
    - 数值指标 CAST AS REAL 按数值排序, 其余按文本(NOCASE)排序
    """
    if not is_safe_field(order_field):
        raise ValueError(f"非法排序字段: {order_field!r}")
    direction = "DESC" if str(order_dir).lower() == "desc" else "ASC"
    expr = f"json_extract(raw_json, '$.{order_field}')"
    if order_field in NUMERIC_FIELDS:
        expr = f"CAST({expr} AS REAL)"
    else:
        expr = f"{expr} COLLATE NOCASE"
    return f" ORDER BY {expr} {direction}"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_shops (
    shop_id       INTEGER PRIMARY KEY,
    shop_name     TEXT,
    site_name     TEXT,
    marketplace_id TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS ad_portfolios (
    portfolio_id  TEXT PRIMARY KEY,
    shop_id       INTEGER,
    shop_name     TEXT,
    portfolio_name TEXT,
    state         TEXT,
    is_hidden     INTEGER,
    marketplace_id TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS ad_records (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tab          TEXT NOT NULL,
    scope        TEXT,
    shop_id      INTEGER,
    shop_name    TEXT,
    biz_key      TEXT NOT NULL,
    range_start  TEXT,
    range_end    TEXT,
    raw_json     TEXT NOT NULL,
    updated_at   TEXT,
    UNIQUE(tab, scope, biz_key, range_start, range_end)
);
CREATE INDEX IF NOT EXISTS idx_records_tab ON ad_records(tab);
CREATE INDEX IF NOT EXISTS idx_records_shop ON ad_records(shop_id);
CREATE INDEX IF NOT EXISTS idx_records_key ON ad_records(biz_key);

CREATE TABLE IF NOT EXISTS ad_stats (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    tab          TEXT NOT NULL,
    scope        TEXT,
    shop_id      INTEGER,
    range_start  TEXT,
    range_end    TEXT,
    payload_json TEXT,
    updated_at   TEXT,
    UNIQUE(tab, scope, shop_id, range_start, range_end)
);

CREATE TABLE IF NOT EXISTS crawl_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT,
    finished_at  TEXT,
    range_start  TEXT,
    range_end    TEXT,
    status       TEXT,
    message      TEXT,
    stats_json   TEXT
);

-- 亚马逊搜索结果抓取 (浏览器加载页面后抽取, 非接口)
CREATE TABLE IF NOT EXISTS amz_searches (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    query        TEXT NOT NULL,
    domain       TEXT NOT NULL,
    url          TEXT,
    page_title   TEXT,
    item_count   INTEGER,
    blocked      INTEGER,
    via          TEXT,
    fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_amz_searches_q ON amz_searches(query, domain, id);

CREATE TABLE IF NOT EXISTS amz_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id    INTEGER NOT NULL,
    position     INTEGER,
    asin         TEXT,
    title        TEXT,
    url          TEXT,
    image        TEXT,
    price        TEXT,
    rating       TEXT,
    reviews      TEXT,
    sponsored    INTEGER,
    fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_amz_results_sid ON amz_results(search_id);

-- 在线产品 (销售 > 在线产品)
-- 子体 / 父体 走**两个不同端点**, 落同一张表, 用 is_variation 区分 (子体=2, 父体=1)
CREATE TABLE IF NOT EXISTS online_products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    biz_key       TEXT NOT NULL UNIQUE,
    asin          TEXT,
    sku           TEXT,
    shop_id       INTEGER,
    marketplace_id TEXT,
    title         TEXT,
    main_image    TEXT,
    online_status TEXT,
    is_variation  TEXT,
    parent_asin   TEXT,
    raw_json      TEXT NOT NULL,
    updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_olp_asin  ON online_products(asin);
CREATE INDEX IF NOT EXISTS idx_olp_sku   ON online_products(sku);
CREATE INDEX IF NOT EXISTS idx_olp_shop  ON online_products(shop_id);
-- 注意: idx_olp_var(is_variation) 在 _migrate 里建 (老库需先 ALTER 加列)

CREATE TABLE IF NOT EXISTS online_meta (
    k            TEXT PRIMARY KEY,
    v            TEXT,
    updated_at   TEXT
);

-- 应用级设置 (键值对, 值为 JSON): 目前用于「品名解析走本地 Ollama 还是 workbuddy」
CREATE TABLE IF NOT EXISTS app_settings (
    k            TEXT PRIMARY KEY,
    v            TEXT,
    updated_at   TEXT
);

-- 在线产品「品名」AI 解析结果 (本地 Ollama): 按 ASIN 缓存, 标题变化后重算
CREATE TABLE IF NOT EXISTS ol_ai_labels (
    asin         TEXT PRIMARY KEY,
    label        TEXT,
    model        TEXT,
    title_hash   TEXT,
    updated_at   TEXT
);

-- 搜索词 × SKU 标题 相关性语义判断 (本地 Ollama): 按 (词,标题哈希) 缓存
CREATE TABLE IF NOT EXISTS term_relevance (
    term         TEXT NOT NULL,
    title_hash   TEXT NOT NULL,
    title        TEXT,
    score        INTEGER,        -- 语义相关性 0~10
    reason       TEXT,           -- 一句话理由
    text_score   INTEGER,        -- 文本匹配分 0~100 (冗余, 便于核对)
    model        TEXT,
    updated_at   TEXT,
    PRIMARY KEY(term, title_hash)
);

-- 相关性「比较过程」流水: 每次比较(含缓存命中)都记一条, 重算结果覆盖 term_relevance
CREATE TABLE IF NOT EXISTS relevance_compare_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    term         TEXT,
    asin         TEXT,
    title_hash   TEXT,
    title        TEXT,
    text_score   INTEGER,
    full_match   INTEGER,        -- 词整词出现在标题
    ai_score     INTEGER,        -- 未调模型(缓存命中/跳过)为 NULL
    ai_reason    TEXT,
    model        TEXT,
    source       TEXT,           -- pair / batch / analyze
    cached       INTEGER,        -- 1=命中旧缓存未新调模型
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_rclog_term ON relevance_compare_log(term);

-- 深度检测: 标题同类判断 (我的标题 × 竞品标题): 按 (父ASIN, 词, 对方ASIN) 覆盖更新
CREATE TABLE IF NOT EXISTS title_similarity (
    asin         TEXT NOT NULL,
    term         TEXT NOT NULL,
    other_asin   TEXT NOT NULL,
    position     INTEGER,
    score        INTEGER,        -- 同类度 0~100
    reason       TEXT,
    core_a       TEXT,           -- 我的核心品类词
    core_b       TEXT,           -- 竞品核心品类词
    model        TEXT,
    updated_at   TEXT,
    PRIMARY KEY(asin, term, other_asin)
);

-- 词级综合结论 (结果集评估 + 是否否定建议), 按 (父ASIN, 词) 覆盖更新
CREATE TABLE IF NOT EXISTS term_verdict (
    asin         TEXT NOT NULL,
    term         TEXT NOT NULL,
    my_title     TEXT,
    score        INTEGER,        -- 词级: 搜索意图 vs 我的品 0~100
    same_category INTEGER,
    term_intent  TEXT,
    reason       TEXT,
    model        TEXT,
    updated_at   TEXT,
    PRIMARY KEY(asin, term)
);

-- 深度检测结果 (SKU 主图 × 搜索结果主图): 按 (父ASIN, 词, 对方ASIN) 缓存并覆盖更新
CREATE TABLE IF NOT EXISTS image_similarity (
    asin         TEXT NOT NULL,
    term         TEXT NOT NULL,
    other_asin   TEXT NOT NULL,
    position     INTEGER,
    score        INTEGER,        -- 最终相似度 0~100
    via          TEXT,           -- llm / phash
    reason       TEXT,
    model        TEXT,
    other_image  TEXT,
    updated_at   TEXT,
    PRIMARY KEY(asin, term, other_asin)
);

-- 竞品整体相似度 (我的品[标题+主图] × 竞品[标题+主图], 一次多模态判断):
-- 按 (父ASIN, 词, 对方ASIN) 覆盖更新; 取代 title_similarity + image_similarity 的拆分口径
CREATE TABLE IF NOT EXISTS sku_similarity (
    asin          TEXT NOT NULL,      -- 我的父 ASIN
    term          TEXT NOT NULL,
    other_asin    TEXT NOT NULL,      -- 竞品 ASIN
    position      INTEGER,
    score         INTEGER,            -- 整体相似度 0~100
    same_category INTEGER,
    reason        TEXT,
    model         TEXT,
    other_title   TEXT,               -- 送模型时的竞品标题
    other_image   TEXT,               -- 竞品主图 URL
    my_title      TEXT,               -- 送模型时的我的标题
    my_image      TEXT,               -- 我的主图 URL
    updated_at    TEXT,
    PRIMARY KEY(asin, term, other_asin)
);
CREATE INDEX IF NOT EXISTS idx_sku_sim_term ON sku_similarity(asin, term);

-- 图片感知特征缓存 (dHash + 直方图): 按 URL 缓存, 深度匹配不重复下载
CREATE TABLE IF NOT EXISTS image_phash (
    url_hash     TEXT PRIMARY KEY,
    url          TEXT,
    dhash        TEXT,           -- 64 位差分哈希 ("0"/"1" 串)
    hist         TEXT,           -- RGB 各 8 bin 归一化直方图 (JSON)
    updated_at   TEXT
);
-- 模型翻译缓存(统一落库, 所有走模型的翻译都在这儿):
--   kind=search_term   source=搜索词原文 → 搜索词中文翻译 (translate.py)
--   kind=product_name  source=ASIN       → 父SKU「品名」中文短语 (ai_label.py 同步镜像)
CREATE TABLE IF NOT EXISTS translations (
    kind       TEXT NOT NULL,
    source     TEXT NOT NULL,
    zh         TEXT NOT NULL,
    model      TEXT,
    updated_at TEXT,
    PRIMARY KEY(kind, source)
);
CREATE INDEX IF NOT EXISTS idx_trans_kind ON translations(kind, updated_at);

-- 搜索词打标: 「父 ASIN + 搜索词」对该品的相关程度 (紧密 / 宽泛 / 不太相关 / 不相关)
CREATE TABLE IF NOT EXISTS term_labels (
    parent_asin TEXT NOT NULL,
    term        TEXT NOT NULL,
    label       TEXT NOT NULL,
    updated_at  TEXT,
    PRIMARY KEY(parent_asin, term)
);
CREATE INDEX IF NOT EXISTS idx_term_labels_asin ON term_labels(parent_asin);

-- 亚马逊商品页补全指标 (赛狐 pageList 里 rating/bsr 恒为 null, 改从商品页抓)
CREATE TABLE IF NOT EXISTS amz_product_metrics (
    asin          TEXT NOT NULL,
    domain        TEXT NOT NULL,
    rating        REAL,        -- 星级评分
    rating_count  INTEGER,     -- 评分数
    bsr_small     INTEGER,     -- 小类目排名
    bsr_small_cat TEXT,        -- 小类目名
    bsr_big       INTEGER,     -- 大类目排名
    bsr_big_cat   TEXT,        -- 大类目名
    title         TEXT,
    price         TEXT,        -- 主价格(如 "￥3,980")
    blocked       INTEGER DEFAULT 0,
    via           TEXT,
    fetched_at    TEXT,
    PRIMARY KEY(asin, domain)
);

-- 商品页图片(主图 + 附图图廊), 按 ASIN 粒度缓存;
-- 父 ASIN 的附图 = 其子 ASIN 图片的合集(上层聚合)
CREATE TABLE IF NOT EXISTS amz_product_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    asin         TEXT NOT NULL,
    domain       TEXT NOT NULL,
    position     INTEGER,
    thumb        TEXT,        -- 页面原始缩略图地址
    large        TEXT,        -- 去尺寸后缀的原始大图地址
    source       TEXT,        -- main / alt
    updated_at   TEXT,
    UNIQUE(asin, domain, large)
);
CREATE INDEX IF NOT EXISTS idx_amz_img_asin ON amz_product_images(asin);

-- 附图抓取记录: 记录某 ASIN 的商品页"已抓过"(即使抓到 0 张), 用于判断
-- 某父体的全部子体是否都已抓取, 避免重复抓取
CREATE TABLE IF NOT EXISTS amz_image_crawls (
    asin         TEXT NOT NULL,
    domain       TEXT NOT NULL,
    image_count  INTEGER,
    crawled_at   TEXT,
    PRIMARY KEY(asin, domain)
);
CREATE INDEX IF NOT EXISTS idx_amz_imgcrawl_asin ON amz_image_crawls(asin);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _LOCK, get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# 轻量迁移: 老库缺列时补上 (CREATE TABLE IF NOT EXISTS 不会加列)
_MIGRATIONS = [
    ("amz_results", "image", "TEXT"),
    # 亚马逊搜索结果卡的全部字段(品牌/积分/券/配送/库存/徽标/extras...)都塞进 raw_json,
    # 不用随字段增减改表结构
    ("amz_results", "raw_json", "TEXT"),
    ("online_products", "is_variation", "TEXT"),
    ("online_products", "parent_asin", "TEXT"),
    ("amz_product_metrics", "price", "TEXT"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, col, coltype in _MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if cols and col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    # 老库 online_products 全部是子体, 回填 is_variation 便于按父子体切换
    conn.execute("CREATE INDEX IF NOT EXISTS idx_olp_var ON online_products(is_variation)")
    # 已有图片的 ASIN 回填「已抓取」记录, 避免附图抓取时重复打开这些商品页
    conn.execute(
        """INSERT OR IGNORE INTO amz_image_crawls(asin, domain, image_count, crawled_at)
           SELECT asin, domain, COUNT(*), MAX(updated_at)
           FROM amz_product_images GROUP BY asin, domain""")
    conn.execute(
        """UPDATE online_products SET is_variation='2'
           WHERE is_variation IS NULL
             AND CAST(json_extract(raw_json,'$.isVariation') AS TEXT)='2'""")
    # 历史已生成的「父SKU品名」回填进统一翻译表 (幂等, 已有则跳过)
    conn.execute(
        """INSERT OR IGNORE INTO translations(kind, source, zh, model, updated_at)
           SELECT 'product_name', asin, label, model, updated_at
           FROM ol_ai_labels WHERE label IS NOT NULL AND label <> ''""")
    # 打标改名: 旧「紧密相关」并到新「紧密」(幂等, 跑第二次没有行可改)
    # 注意: 只改名不删值 —— 万一库里还有别的历史标签, 宁可留着让用户自己处理
    for old, new in TERM_LABEL_ALIASES.items():
        conn.execute("UPDATE term_labels SET label=? WHERE label=?", (new, old))


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def save_shops(rows: List[Dict[str, Any]]) -> int:
    with _LOCK, get_conn() as conn:
        n = 0
        for r in rows:
            conn.execute(
                """INSERT INTO ad_shops(shop_id, shop_name, site_name, marketplace_id, updated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(shop_id) DO UPDATE SET
                     shop_name=excluded.shop_name, site_name=excluded.site_name,
                     marketplace_id=excluded.marketplace_id, updated_at=excluded.updated_at""",
                (r.get("shopId"), r.get("shopName"), r.get("siteName"),
                 r.get("marketplaceId"), now()))
            n += 1
        return n


def save_portfolios(rows: List[Dict[str, Any]]) -> int:
    with _LOCK, get_conn() as conn:
        n = 0
        for r in rows:
            if not r.get("portfolioId"):
                continue
            conn.execute(
                """INSERT INTO ad_portfolios(portfolio_id, shop_id, shop_name, portfolio_name,
                                             state, is_hidden, marketplace_id, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(portfolio_id) DO UPDATE SET
                     portfolio_name=excluded.portfolio_name, state=excluded.state,
                     is_hidden=excluded.is_hidden, updated_at=excluded.updated_at""",
                (str(r.get("portfolioId")), r.get("shopId"), r.get("shopName"),
                 r.get("portfolioName"), r.get("state"), r.get("isHidden"),
                 r.get("marketplaceId"), now()))
            n += 1
        return n


def _scope_str(scope: Optional[Dict[str, Any]]) -> str:
    """scope 维度归一 (统一由 sellfox_client.scope_str 实现, 避免各处不一致)"""
    return scope_str(scope)


def purge_stale_ranges(tab: str, scope: Optional[Dict[str, Any]],
                       shop_id: Optional[int], range_start: str, range_end: str) -> int:
    """清掉同一 (页签, 维度, 店铺) 下**其它时间区间**的历史快照

    库里 ad_records 的唯一键是 (tab, scope, biz_key, range_start, range_end)。
    问题: 区间一变(比如跨天采集, endDate 从 10-04 变成 10-05), 同一条记录会因为
    区间不同而**再插一份**, 于是数据翻倍、指标翻倍。
    「一次采集就是一个快照」, 所以新快照落库前要把该维度下的旧区间数据删掉。

    返回删除行数。
    """
    scope_s = _scope_str(scope)
    with _LOCK, get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM ad_records
               WHERE tab=? AND COALESCE(scope,'')=? AND COALESCE(shop_id,-1)=COALESCE(?,-1)
                 AND (COALESCE(range_start,'')<>? OR COALESCE(range_end,'')<>?)""",
            (tab, scope_s, shop_id, range_start or "", range_end or ""))
        n = cur.rowcount or 0
        conn.execute(
            """DELETE FROM ad_stats
               WHERE tab=? AND COALESCE(scope,'')=? AND COALESCE(shop_id,-1)=COALESCE(?,-1)
                 AND (COALESCE(range_start,'')<>? OR COALESCE(range_end,'')<>?)""",
            (tab, scope_s, shop_id, range_start or "", range_end or ""))
        return n


def purge_all_other_ranges(range_start: str, range_end: str) -> int:
    """采集收尾清理: 删掉**所有**与本次区间不同的历史快照

    比 purge_stale_ranges 更彻底 —— 连那些本次采集失败的维度一起清掉,
    保证库里只剩「这一次采集」的快照, 统计口径不会被旧数据污染。
    """
    with _LOCK, get_conn() as conn:
        cur = conn.execute(
            """DELETE FROM ad_records
               WHERE COALESCE(range_start,'')<>? OR COALESCE(range_end,'')<>?""",
            (range_start or "", range_end or ""))
        n = cur.rowcount or 0
        conn.execute(
            """DELETE FROM ad_stats
               WHERE COALESCE(range_start,'')<>? OR COALESCE(range_end,'')<>?""",
            (range_start or "", range_end or ""))
        return n


def dedupe_stale_ranges() -> int:
    """一次性清理: 每个 (页签, 维度, 店铺) 只保留「最新那一次采集区间」的数据

    用于修掉历史上因为跨天重复采集而堆积的多份快照。返回删除行数。
    """
    removed = 0
    with _LOCK, get_conn() as conn:
        groups = conn.execute(
            """SELECT tab, COALESCE(scope,'') sc, COALESCE(shop_id,-1) sid,
                      COALESCE(range_start,'') rs, COALESCE(range_end,'') re,
                      MAX(updated_at) mx
               FROM ad_records
               GROUP BY tab, sc, sid
               HAVING COUNT(DISTINCT COALESCE(range_start,'')||'~'||COALESCE(range_end,'')) > 1"""
        ).fetchall()
        for g in groups:
            latest = conn.execute(
                """SELECT range_start, range_end, MAX(updated_at) mx FROM ad_records
                   WHERE tab=? AND COALESCE(scope,'')=? AND COALESCE(shop_id,-1)=?
                   GROUP BY COALESCE(range_start,'')||'~'||COALESCE(range_end,'')
                   ORDER BY mx DESC LIMIT 1""",
                (g["tab"], g["sc"], g["sid"])).fetchone()
            if not latest:
                continue
            cur = conn.execute(
                """DELETE FROM ad_records
                   WHERE tab=? AND COALESCE(scope,'')=? AND COALESCE(shop_id,-1)=?
                     AND (COALESCE(range_start,'')<>? OR COALESCE(range_end,'')<>?)""",
                (g["tab"], g["sc"], g["sid"], latest["range_start"] or "",
                 latest["range_end"] or ""))
            removed += cur.rowcount or 0
    return removed


def upsert_records(tab: str, scope: Optional[Dict[str, Any]], rows: List[Dict[str, Any]],
                   range_start: str, range_end: str) -> int:
    """按唯一键 upsert; 返回写入/更新条数"""
    if not rows:
        return 0
    scope_s = _scope_str(scope)
    ts = now()
    with _LOCK, get_conn() as conn:
        n = 0
        for r in rows:
            biz = _biz_key(tab, r)
            conn.execute(
                """INSERT INTO ad_records(tab, scope, shop_id, shop_name, biz_key,
                                          range_start, range_end, raw_json, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(tab, scope, biz_key, range_start, range_end) DO UPDATE SET
                     raw_json=excluded.raw_json, shop_id=excluded.shop_id,
                     shop_name=excluded.shop_name, updated_at=excluded.updated_at""",
                (tab, scope_s, r.get("shopId"), r.get("shopName"), biz,
                 range_start, range_end, json.dumps(r, ensure_ascii=False), ts))
            n += 1
        return n


def _biz_key(tab: str, row: Dict[str, Any]) -> str:
    from backend.sellfox_client import TAB_DEFS
    for k in TAB_DEFS.get(tab, {}).get("keys", ["id"]):
        v = row.get(k)
        if v not in (None, "", 0, "0"):
            return str(v)
    # 回落: 关键维度组合
    parts = [str(row.get(k) or "") for k in
             ("shopId", "campaignId", "adGroupId", "query", "matchType", "targetId", "name")]
    import hashlib
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def save_aggregate(tab: str, scope: Optional[Dict[str, Any]], shop_id: Optional[int],
                   range_start: str, range_end: str, data: Any) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO ad_stats(tab, scope, shop_id, range_start, range_end,
                                    payload_json, updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(tab, scope, shop_id, range_start, range_end) DO UPDATE SET
                 payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
            (tab, _scope_str(scope), shop_id, range_start, range_end,
             json.dumps(data, ensure_ascii=False), now()))


# ---------------------------------------------------------------------------
# 采集批次
# ---------------------------------------------------------------------------
def create_run(range_start: str, range_end: str) -> int:
    with _LOCK, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO crawl_runs(started_at, range_start, range_end, status, message, stats_json)
               VALUES(?,?,?,?,?,?)""",
            (now(), range_start, range_end, "running", "", "{}"))
        return cur.lastrowid


def finish_run(run_id: int, status: str, message: str, stats: Dict[str, Any]) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """UPDATE crawl_runs SET finished_at=?, status=?, message=?, stats_json=? WHERE id=?""",
            (now(), status, message, json.dumps(stats, ensure_ascii=False), run_id))


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM crawl_runs WHERE id=?", (run_id,)).fetchone()
        return dict(row) if row else None


def last_run() -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def get_status() -> Dict[str, Any]:
    with get_conn() as conn:
        counts = {r["tab"]: r["c"] for r in conn.execute(
            "SELECT tab, COUNT(*) c FROM ad_records GROUP BY tab")}
        shops = conn.execute("SELECT COUNT(*) c FROM ad_shops").fetchone()["c"]
        portfolios = conn.execute("SELECT COUNT(*) c FROM ad_portfolios").fetchone()["c"]
        last = conn.execute("SELECT MAX(updated_at) u FROM ad_records").fetchone()["u"]
    return {"tab_counts": counts, "shops": shops, "portfolios": portfolios,
            "last_updated": last, "last_run": last_run()}


def get_shops() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ad_shops ORDER BY shop_id")]


def get_portfolios() -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM ad_portfolios ORDER BY shop_id, portfolio_name")]


# ---------------------------------------------------------------------------
# 本地筛选框架: 可过滤字段 / 可搜索字段 / 枚举值中文名
# ---------------------------------------------------------------------------
FILTERABLE_FIELDS: Dict[str, List[str]] = {
    "portfolio": ["marketplaceId", "portfolioId", "state", "servingStatus"],
    "campaign":  ["marketplaceId", "portfolioId", "state", "servingStatus", "devNames"],
    "group":     ["marketplaceId", "portfolioId", "state", "servingStatus",
                  "campaignState", "devNames"],
    "product":   ["marketplaceId", "portfolioId", "state", "servingStatus",
                  "campaignState", "adGroupState", "devNames"],
    "target":    ["marketplaceId", "portfolioId", "state", "servingStatus",
                  "campaignState", "adGroupState", "devNames"],
    "search":    ["marketplaceId", "portfolioId", "campaignState", "adGroupState",
                  "targetState", "devNames"],
    "netarget":  ["marketplaceId", "state", "servingStatus"],
    "placement": ["marketplaceId", "servingStatus", "campaignState", "devNames"],
    "log":       ["marketplaceId", "action", "logType"],
}

SEARCH_FIELD_CANDIDATES: Dict[str, List[str]] = {
    "portfolio": ["portfolioName", "name"],
    "campaign":  ["name", "campaignId", "portfolioName"],
    "group":     ["name", "adGroupId", "campaignName", "portfolioName"],
    "product":   ["asin", "sku", "campaignName", "adGroupName", "portfolioName"],
    "target":    ["keywordText", "campaignName", "adGroupName", "portfolioName"],
    "search":    ["query", "keywordText", "campaignName", "adGroupName", "portfolioName"],
    "netarget":  ["keywordText", "name", "campaignName", "adGroupName"],
    "placement": ["campaignName", "portfolioName"],
    "log":       ["operationContents", "campaignName", "adGroupName", "operatorName"],
}

VALUE_LABELS: Dict[str, str] = {
    "enabled": "投放中", "paused": "已暂停", "archived": "已归档",
    "CAMPAIGN_STATUS_ENABLED": "投放中", "CAMPAIGN_PAUSED": "广告活动已暂停",
    "CAMPAIGN_ARCHIVED": "已归档",
    "AD_GROUP_STATUS_ENABLED": "投放中", "AD_GROUP_PAUSED": "广告组已暂停",
    "AD_GROUP_ARCHIVED": "已归档",
    "TARGET_STATUS_ENABLED": "投放中", "TARGET_PAUSED": "投放已暂停",
    "TARGET_ARCHIVED": "已归档",
    "PORTFOLIO_STATUS_ENABLED": "投放中", "PORTFOLIO_PAUSED": "广告组合已暂停",
    "PORTFOLIO_ARCHIVED": "已归档",
    "NOT_BUYABLE": "不可购买", "ACCOUNT_OUT_OF_BUDGET": "账户超预算",
    "OUT_OF_BUDGET": "超预算",
}


def _jf(field: str) -> str:
    return f"json_extract(raw_json, '$.{field}')"


def _build_where(tab: str, shop_id: Optional[int] = None, scope: str = "",
                 range_start: str = "", range_end: str = "",
                 filters: Optional[Dict[str, List[str]]] = None,
                 keyword: str = "", search_field: str = "",
                 search_mode: str = "blur") -> "tuple[str, list]":
    """构造 WHERE 子句 (records / stats 共用)。

    filters: {字段: [值,...]} 多选等值过滤, 字段须在 FILTERABLE_FIELDS[tab] 白名单内。
    keyword + search_field + search_mode: 指定字段(留空=整行)的 精确/模糊 匹配。
    """
    where, args = ["tab = ?"], [tab]
    if shop_id:
        where.append("shop_id = ?"); args.append(shop_id)
    if scope:
        where.append("scope = ?"); args.append(scope)
    if range_start:
        where.append("range_start = ?"); args.append(range_start)
    if range_end:
        where.append("range_end = ?"); args.append(range_end)

    allowed = set(FILTERABLE_FIELDS.get(tab, []))
    for field, values in (filters or {}).items():
        if field not in allowed or not is_safe_field(field):
            continue        # 白名单外的字段直接忽略 (防注入/防误用)
        vals = [str(v) for v in (values or []) if v not in (None, "")]
        if not vals:
            continue
        where.append(f"{_jf(field)} IN ({','.join('?' * len(vals))})")
        args.extend(vals)

    kw = (keyword or "").strip()
    if kw:
        field = search_field if is_safe_field(search_field or "") else ""
        expr = f"lower({_jf(field)})" if field else "lower(raw_json)"
        if str(search_mode).lower() == "exact":
            where.append(f"{expr} = ?"); args.append(kw.lower())
        else:
            where.append(f"{expr} LIKE ?"); args.append(f"%{kw.lower()}%")
    return " WHERE " + " AND ".join(where), args


def get_records(tab: str, page: int = 1, page_size: int = 50,
                keyword: str = "", shop_id: Optional[int] = None,
                scope: str = "", order_field: str = "", order_dir: str = "desc",
                range_start: str = "", range_end: str = "",
                filters: Optional[Dict[str, List[str]]] = None,
                search_field: str = "", search_mode: str = "blur") -> Dict[str, Any]:
    sql_where, args = _build_where(tab, shop_id, scope, range_start, range_end,
                                   filters, keyword, search_field, search_mode)

    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM ad_records{sql_where}", args).fetchone()["c"]
        if order_field:
            order_sql = build_order_clause(order_field, order_dir)
        else:
            order_sql = " ORDER BY id ASC"
        rows = conn.execute(
            f"SELECT raw_json FROM ad_records{sql_where}{order_sql} LIMIT ? OFFSET ?",
            args + [page_size, (page - 1) * page_size]).fetchall()

    data = [json.loads(r["raw_json"]) for r in rows]
    cols = _collect_columns(tab, data)
    return {"tab": tab, "total": total, "page": page, "page_size": page_size,
            "columns": cols, "rows": data}


def get_filter_options(tab: str, shop_id: Optional[int] = None) -> Dict[str, Any]:
    """返回某页签「数据中实际存在」的筛选字段与可选值(含计数), 供前端动态渲染查询框。"""
    from backend.sellfox_client import label_of
    where, args = ["tab = ?"], [tab]
    if shop_id:
        where.append("shop_id = ?"); args.append(shop_id)
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT raw_json FROM ad_records WHERE " + " AND ".join(where), args).fetchall()

    data = [json.loads(r["raw_json"]) for r in rows]
    present: set = set()
    for d in data:
        present.update(d.keys())

    filters: List[Dict[str, Any]] = []
    for f in FILTERABLE_FIELDS.get(tab, []):
        if f not in present:
            continue
        counts: Dict[str, int] = {}
        for d in data:
            v = d.get(f)
            if v in (None, "", [], {}):
                continue
            counts[str(v)] = counts.get(str(v), 0) + 1
        if not counts:
            continue
        opts = [{"value": k, "label": VALUE_LABELS.get(k, k), "count": c}
                for k, c in sorted(counts.items(), key=lambda x: -x[1])]
        filters.append({"key": f, "label": label_of(f), "options": opts})

    search_fields = [{"key": f, "label": label_of(f)}
                     for f in SEARCH_FIELD_CANDIDATES.get(tab, []) if f in present]
    return {"tab": tab, "filters": filters, "search_fields": search_fields}


_ORDERED_PREFERRED = [
    # 优先展示的业务列 (存在才显示)
    "name", "campaignName", "adGroupName", "query", "keywordText", "portfolioName",
    "shopName", "asin", "adType", "state", "servingStatus", "campaignState",
    "dailyBudget", "defaultBid", "bid", "matchType",
    "impressions", "clicks", "ctr", "adCost", "cpm", "adCostPerClick",
    "adOrderNum", "adSaleNum", "adSales", "cvr", "cpa", "acos", "roas",
    "searchFrequencyRank", "weekRatio", "impressionRank",
]


def _collect_columns(tab: str, rows: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    from backend.sellfox_client import label_of
    primary = {"campaign": "广告活动", "group": "广告组", "product": "广告产品",
               "target": "关键词", "search": "用户搜索词", "portfolio": "广告组合",
               "placement": "广告位", "log": "操作内容", "netarget": "否定投放"}
    seen: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.append(k)
    ordered = [k for k in _ORDERED_PREFERRED if k in seen]
    ordered += [k for k in seen if k not in ordered]

    def _label(k: str) -> str:
        if k == "name" and tab in primary:
            return primary[tab]
        return label_of(k)

    return [{"key": k, "label": _label(k)} for k in ordered]


def get_stats(tab: str, shop_id: Optional[int] = None, scope: str = "",
              range_start: str = "", range_end: str = "",
              filters: Optional[Dict[str, List[str]]] = None,
              keyword: str = "", search_field: str = "",
              search_mode: str = "blur") -> Dict[str, int]:
    """按当前筛选条件计算统计条: 有成交/有点击无成交/有曝光无点击/无曝光"""
    sql_where, args = _build_where(tab, shop_id, scope, range_start, range_end,
                                   filters, keyword, search_field, search_mode)
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT raw_json FROM ad_records{sql_where}", args).fetchall()
    deal = click_no_deal = imp_no_click = no_imp = 0
    for r in rows:
        d = json.loads(r["raw_json"])
        try:
            orders = float(d.get("adOrderNum") or 0)
            clicks = float(d.get("clicks") or 0)
            imps = float(d.get("impressions") or 0)
        except (TypeError, ValueError):
            continue
        if orders > 0:
            deal += 1
        elif clicks > 0:
            click_no_deal += 1
        elif imps > 0:
            imp_no_click += 1
        else:
            no_imp += 1
    return {"deal": deal, "click_no_deal": click_no_deal,
            "imp_no_click": imp_no_click, "no_imp": no_imp, "total": len(rows)}


# ---------------------------------------------------------------------------
# 亚马逊搜索结果 落库 / 缓存读取
# ---------------------------------------------------------------------------
def save_amazon_search(query: str, domain: str, result: Dict[str, Any]) -> int:
    """把一次抓取(元信息 + 明细)落库, 返回 search_id"""
    ts = now()
    items = list(result.get("items") or [])
    with _LOCK, get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO amz_searches(query, domain, url, page_title, item_count,
                                        blocked, via, fetched_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (query, domain, result.get("url"), result.get("page_title"),
             int(result.get("item_count") or len(items)), 1 if result.get("blocked") else 0,
             result.get("via"), ts))
        sid = cur.lastrowid
        if items:
            conn.executemany(
                """INSERT INTO amz_results(search_id, position, asin, title, url, image, price,
                                           rating, reviews, sponsored, raw_json, fetched_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                [(sid, it.get("position"), it.get("asin"), it.get("title"), it.get("url"),
                  it.get("image"), it.get("price"), it.get("rating"), it.get("reviews"),
                  1 if it.get("sponsored") else 0,
                  json.dumps(it, ensure_ascii=False), ts) for it in items])
    return sid


def _amz_item(row: sqlite3.Row) -> Dict[str, Any]:
    """把 amz_results 一行还原成明细 dict

    优先以 raw_json(完整字段) 为准, 再用独立的列覆盖, 保证老数据(无 raw_json)也能用。
    """
    d: Dict[str, Any] = {}
    raw = row["raw_json"] if "raw_json" in row.keys() else None
    if raw:
        try:
            d = json.loads(raw)
        except Exception:       # noqa: BLE001
            d = {}
    base = {
        "position": row["position"], "asin": row["asin"], "title": row["title"],
        "url": row["url"], "image": row["image"], "price": row["price"],
        "rating": row["rating"], "reviews": row["reviews"],
        "sponsored": bool(row["sponsored"]),
    }
    base.update({k: v for k, v in d.items() if v not in (None, "")})
    d.update(base)
    d["sponsored"] = bool(row["sponsored"])
    d.setdefault("extras", [])
    d.setdefault("badges", [])
    return d


def get_cached_amazon_search(query: str, domain: str,
                             ttl_sec: int) -> Optional[Dict[str, Any]]:
    """取 TTL 内最近一次抓取结果; 无则返回 None"""
    cutoff = (datetime.now() - timedelta(seconds=max(0, ttl_sec))).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        head = conn.execute(
            """SELECT * FROM amz_searches
               WHERE query=? AND domain=? AND fetched_at>=?
               ORDER BY id DESC LIMIT 1""",
            (query, domain, cutoff)).fetchone()
        if not head:
            return None
        rows = conn.execute(
            "SELECT * FROM amz_results WHERE search_id=? ORDER BY position", (head["id"],)
        ).fetchall()
    return {
        "search_id": head["id"], "query": head["query"], "domain": head["domain"],
        "url": head["url"], "page_title": head["page_title"],
        "item_count": head["item_count"], "blocked": bool(head["blocked"]),
        "via": head["via"], "fetched_at": head["fetched_at"], "from_cache": True,
        "items": [_amz_item(r) for r in rows],
    }


def get_amazon_searches(limit: int = 50, query: str = "") -> List[Dict[str, Any]]:
    """抓取历史 (元信息)"""
    sql = "SELECT * FROM amz_searches"
    args: List[Any] = []
    if query:
        sql += " WHERE query LIKE ?"; args.append(f"%{query}%")
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def get_amazon_results(search_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        return [_amz_item(r) for r in conn.execute(
            "SELECT * FROM amz_results WHERE search_id=? ORDER BY position", (search_id,))]


# ---------------------------------------------------------------------------
# 在线产品 (销售 > 在线产品)
# ---------------------------------------------------------------------------
def save_online_products(rows: List[Dict[str, Any]], progress=None) -> int:
    """按 biz_key 唯一键 upsert 在线产品; 返回写入条数

    子体 / 父体同表存储, 以 is_variation 区分 (子体=2, 父体=1)。
    """
    if not rows:
        return 0
    ts = now()
    n = 0
    with _LOCK, get_conn() as conn:
        for r in rows:
            biz = str(r.get("id") or r.get("puid") or r.get("asin") or "")
            if not biz:
                continue
            iv = r.get("isVariation")
            iv = None if iv in (None, "") else str(iv)
            conn.execute(
                """INSERT INTO online_products(biz_key, asin, sku, shop_id, marketplace_id,
                                               title, main_image, online_status,
                                               is_variation, parent_asin, raw_json, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(biz_key) DO UPDATE SET
                     asin=excluded.asin, sku=excluded.sku, shop_id=excluded.shop_id,
                     marketplace_id=excluded.marketplace_id, title=excluded.title,
                     main_image=excluded.main_image, online_status=excluded.online_status,
                     is_variation=excluded.is_variation, parent_asin=excluded.parent_asin,
                     raw_json=excluded.raw_json, updated_at=excluded.updated_at""",
                (biz, r.get("asin"), r.get("sku"), r.get("shopId"), r.get("marketplaceId"),
                 r.get("title"), r.get("mainImage"), r.get("onlineStatus"),
                 iv, r.get("parentAsin"),
                 json.dumps(r, ensure_ascii=False), ts))
            n += 1
        if progress and n:
            progress(f"    已写入 {n} 条")
    return n


def get_online_count(page_type: str = "") -> int:
    """在线产品条数; page_type: ""(全部) / child(子体) / parents(父体)"""
    with get_conn() as conn:
        if str(page_type).lower() in ("parents", "parent"):
            return conn.execute(
                "SELECT COUNT(*) c FROM online_products WHERE is_variation='1'"
            ).fetchone()["c"]
        if str(page_type).lower() == "child":
            return conn.execute(
                "SELECT COUNT(*) c FROM online_products WHERE is_variation='2'"
            ).fetchone()["c"]
        return conn.execute("SELECT COUNT(*) c FROM online_products").fetchone()["c"]


def get_setting(k: str, default: Any = None) -> Any:
    """读应用设置 (JSON 值); 没有则返回 default"""
    with get_conn() as conn:
        r = conn.execute("SELECT v FROM app_settings WHERE k=?", (k,)).fetchone()
    if not r or r["v"] is None:
        return default
    try:
        return json.loads(r["v"])
    except Exception:       # noqa: BLE001  老数据/手工改坏 → 用默认值
        return default


def set_setting(k: str, v: Any) -> None:
    """写应用设置 (JSON 值)"""
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO app_settings(k, v, updated_at) VALUES(?,?,?)
               ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at""",
            (k, json.dumps(v, ensure_ascii=False), now()))


def get_online_counts() -> Dict[str, int]:
    """子体 / 父体 / 合计 条数"""
    return {"child": get_online_count("child"),
            "parent": get_online_count("parents"),
            "total": get_online_count()}


def get_asin_parent_map() -> Dict[str, str]:
    """子 ASIN -> 父 ASIN 映射 (来自在线产品的变体关系)

    没有变体关系的商品不在映射里, 调用方自行回退为「父=自身」。
    在线产品未采集时返回空字典, 不影响广告数据。
    """
    with get_conn() as conn:
        return _asin_parent_map(conn)


def save_online_meta(k: str, v: Any) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO online_meta(k, v, updated_at) VALUES(?,?,?)
               ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_at=excluded.updated_at""",
            (k, json.dumps(v, ensure_ascii=False), now()))


def get_online_meta(k: str) -> Any:
    with get_conn() as conn:
        row = conn.execute("SELECT v FROM online_meta WHERE k=?", (k,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["v"])
    except Exception:       # noqa: BLE001
        return row["v"]


def get_online_titles(asins: List[str]) -> Dict[str, Dict[str, str]]:
    """ASIN -> {title, title_hash} (供 AI 品名解析判断是否需要重算)"""
    from backend.ai_label import title_hash
    vals = [str(a) for a in (asins or []) if a]
    if not vals:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with get_conn() as conn:
        for i in range(0, len(vals), 400):
            chunk = vals[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT asin, title FROM online_products WHERE asin IN ({ph})", chunk):
                if r["asin"] and r["asin"] not in out:
                    out[r["asin"]] = {"title": r["title"] or "",
                                      "title_hash": title_hash(r["title"])}
    return out


def get_ai_labels(asins: List[str]) -> Dict[str, Dict[str, str]]:
    vals = [str(a) for a in (asins or []) if a]
    if not vals:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with get_conn() as conn:
        for i in range(0, len(vals), 400):
            chunk = vals[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT asin, label, model, title_hash, updated_at "
                    f"FROM ol_ai_labels WHERE asin IN ({ph})", chunk):
                out[r["asin"]] = {"label": r["label"], "model": r["model"],
                                  "title_hash": r["title_hash"], "updated_at": r["updated_at"]}
    return out


def save_ai_label(asin: str, label: str, model: str, th: str) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO ol_ai_labels(asin, label, model, title_hash, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(asin) DO UPDATE SET
                 label=excluded.label, model=excluded.model,
                 title_hash=excluded.title_hash, updated_at=excluded.updated_at""",
            (asin, label, model, th, now()))


# ---------------------------------------------------------------------------
# 搜索词 × SKU 标题 相关性
# ---------------------------------------------------------------------------
def get_relevance(term: str, title_hash: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT score, reason, text_score, model, updated_at "
            "FROM term_relevance WHERE term=? AND title_hash=?",
            (term, title_hash)).fetchone()
    return dict(r) if r else None


def save_relevance(term: str, title_hash: str, title: str, score: int,
                   reason: str, text_score: int, model: str) -> None:
    """语义判断结果落库 (重算后覆盖更新同一行)"""
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO term_relevance(term, title_hash, title, score, reason,
                                          text_score, model, updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(term, title_hash) DO UPDATE SET
                 title=excluded.title, score=excluded.score, reason=excluded.reason,
                 text_score=excluded.text_score, model=excluded.model,
                 updated_at=excluded.updated_at""",
            (term, title_hash, title, score, reason, text_score, model, now()))


def log_compare(term: str, asin: str, title_hash: str, title: str,
                text_score: int, full_match: bool, ai_score: Optional[int],
                ai_reason: str, model: str, source: str, cached: bool) -> None:
    """比较过程流水: 每次比较(含缓存命中)记一条"""
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO relevance_compare_log
               (term, asin, title_hash, title, text_score, full_match,
                ai_score, ai_reason, model, source, cached, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (term, asin, title_hash, title, text_score, int(bool(full_match)),
             ai_score, ai_reason, model, source, int(bool(cached)), now()))


# ---------------------------------------------------------------------------
# 图片感知特征缓存 + 深度匹配数据源
# ---------------------------------------------------------------------------
def save_image_phash(url: str, dhash: str, hist: List[float]) -> None:
    import hashlib
    uh = hashlib.sha1(url.encode("utf-8")).hexdigest()
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO image_phash(url_hash, url, dhash, hist, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(url_hash) DO UPDATE SET
                 dhash=excluded.dhash, hist=excluded.hist, updated_at=excluded.updated_at""",
            (uh, url, dhash, json.dumps(hist), now()))


def save_image_sim(asin: str, term: str, other_asin: str, position: Any,
                   score: Optional[int], via: str, reason: str, model: str,
                   other_image: str) -> None:
    """深度检测结果落库 (重算覆盖同一行)"""
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO image_similarity(asin, term, other_asin, position, score,
                                            via, reason, model, other_image, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asin, term, other_asin) DO UPDATE SET
                 position=excluded.position, score=excluded.score, via=excluded.via,
                 reason=excluded.reason, model=excluded.model,
                 other_image=excluded.other_image, updated_at=excluded.updated_at""",
            (asin, term, str(other_asin or ""), position, score, via, reason,
             model, other_image, now()))


def get_image_sims(asin: str, term: str) -> Dict[str, Dict[str, Any]]:
    """某 (父ASIN, 词) 已缓存的深度检测结果, 以对方 ASIN 为键"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT other_asin, position, score, via, reason, model, updated_at "
            "FROM image_similarity WHERE asin=? AND term=?", (asin, term)).fetchall()
    return {r["other_asin"]: dict(r) for r in rows}


# ---- 深度检测: 竞品整体相似度 (我的品[标题+主图] × 竞品[标题+主图]) ----
def save_sku_sim(asin: str, term: str, other_asin: str, position: Any,
                 score: Optional[int], same: Any, reason: str, model: str,
                 other_title: str, other_image: str, my_title: str,
                 my_image: str) -> None:
    """整体相似度落库 (同一 (父ASIN, 词, 对方ASIN) 重算覆盖)"""
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO sku_similarity(asin, term, other_asin, position, score,
                                          same_category, reason, model, other_title,
                                          other_image, my_title, my_image, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asin, term, other_asin) DO UPDATE SET
                 position=excluded.position, score=excluded.score,
                 same_category=excluded.same_category, reason=excluded.reason,
                 model=excluded.model, other_title=excluded.other_title,
                 other_image=excluded.other_image, my_title=excluded.my_title,
                 my_image=excluded.my_image, updated_at=excluded.updated_at""",
            (asin, term, str(other_asin or ""), position, score,
             1 if same else 0, reason, model, other_title, other_image,
             my_title, my_image, now()))


def get_sku_sims(asin: str, term: str) -> Dict[str, Dict[str, Any]]:
    """某 (父ASIN, 词) 已缓存的整体相似度结果, 以对方 ASIN 为键"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT other_asin, position, score, same_category, reason, model, "
            "updated_at FROM sku_similarity WHERE asin=? AND term=?",
            (asin, term)).fetchall()
    return {r["other_asin"]: dict(r) for r in rows}


# ---- 深度检测: 标题同类 / 词级结论 ----
def save_title_sim(asin: str, term: str, other_asin: str, position: Any,
                   score: Optional[int], reason: str, core_a: str, core_b: str,
                   model: str) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO title_similarity(asin, term, other_asin, position, score,
                                            reason, core_a, core_b, model, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asin, term, other_asin) DO UPDATE SET
                 position=excluded.position, score=excluded.score, reason=excluded.reason,
                 core_a=excluded.core_a, core_b=excluded.core_b,
                 model=excluded.model, updated_at=excluded.updated_at""",
            (asin, term, str(other_asin or ""), position, score, reason,
             core_a, core_b, model, now()))


def get_title_sims(asin: str, term: str) -> Dict[str, Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT other_asin, position, score, reason, core_a, core_b, model "
            "FROM title_similarity WHERE asin=? AND term=?", (asin, term)).fetchall()
    return {r["other_asin"]: dict(r) for r in rows}


def save_term_verdict(asin: str, term: str, my_title: str, score: int,
                      same: bool, intent: str, reason: str, model: str) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO term_verdict(asin, term, my_title, score, same_category,
                                        term_intent, reason, model, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asin, term) DO UPDATE SET
                 my_title=excluded.my_title, score=excluded.score,
                 same_category=excluded.same_category, term_intent=excluded.term_intent,
                 reason=excluded.reason, model=excluded.model,
                 updated_at=excluded.updated_at""",
            (asin, term, my_title, score, int(bool(same)), intent, reason, model, now()))


def get_term_verdict(asin: str, term: str) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        r = conn.execute(
            "SELECT score, same_category, term_intent, reason, model, updated_at "
            "FROM term_verdict WHERE asin=? AND term=?", (asin, term)).fetchone()
    if not r:
        return None
    return {"score": r["score"], "same": bool(r["same_category"]),
            "intent": r["term_intent"], "reason": r["reason"],
            "model": r["model"], "updated_at": r["updated_at"]}


def get_term_metrics(asin: str, term: str) -> Dict[str, float]:
    """某父 ASIN 下某个搜索词的合并指标(花费/订单/曝光/点击)"""
    out = {"adCost": 0.0, "orderNum": 0.0, "impressions": 0.0, "clicks": 0.0}
    try:
        pt = get_parent_terms(asin)
    except Exception:                           # noqa: BLE001
        return out
    q = str(term or "").strip()
    for r in pt.get("rows") or []:
        if str(r.get("query") or "").strip() == q:
            out["adCost"] += _to_float(r.get("adCost"))
            out["orderNum"] += _to_float(r.get("orderNum"))
            out["impressions"] += _to_float(r.get("impressions"))
            out["clicks"] += _to_float(r.get("clicks"))
    return {k: round(v, 2) for k, v in out.items()}


def get_image_phash(url: str) -> Optional[Dict[str, Any]]:
    import hashlib
    uh = hashlib.sha1(url.encode("utf-8")).hexdigest()
    with get_conn() as conn:
        r = conn.execute("SELECT dhash, hist FROM image_phash WHERE url_hash=?",
                         (uh,)).fetchone()
    if not r:
        return None
    try:
        return {"dhash": r["dhash"], "hist": json.loads(r["hist"])}
    except Exception:                           # noqa: BLE001
        return None


def get_parent_main_image(asin: str) -> str:
    """父 SKU 主图 (赛狐商品行, 取最大可用尺寸由调用方处理)"""
    with get_conn() as conn:
        child2parent = _asin_parent_map(conn)
        parent = child2parent.get(asin, asin)
        kids = [parent] + sorted(a for a, p in child2parent.items() if p == parent)
        try:
            pf, pa_ = _snapshot_filter(conn, "product")
        except Exception:                       # noqa: BLE001
            pf, pa_ = "", []
        for r in conn.execute(
                "SELECT raw_json FROM ad_records WHERE tab='product' "
                "AND json_extract(raw_json,'$.asin') IN (" +
                ",".join("?" * len(kids)) + ")" + pf, kids + pa_):
            d = json.loads(r["raw_json"])
            u = d.get("imgUrl") or d.get("main_image") or d.get("image")
            if u:
                return str(u)
    return ""


def latest_amazon_results(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """某搜索词最近一次亚马逊抓取的结果明细 (缓存只读, 不触发抓取)"""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT id FROM amz_searches WHERE query=? ORDER BY id DESC LIMIT 1",
            (str(query or "").strip(),)).fetchone()
        if not r:
            return []
        sid = r["id"]
        rows = conn.execute(
            "SELECT * FROM amz_results WHERE search_id=? ORDER BY position LIMIT ?",
            (sid, max(1, min(limit, 50)))).fetchall()
    out: List[Dict[str, Any]] = []
    for row in rows:
        d = _amz_item(row)
        if d.get("image"):
            out.append(d)
    return out


# ---- 模型翻译缓存 (搜索词 / 父SKU品名) ----
def get_translations(kind: str, sources: List[str]) -> Dict[str, str]:
    """source -> 中文翻译 (只返回已有的)"""
    vals = [str(s) for s in (sources or []) if s]
    if not vals:
        return {}
    out: Dict[str, str] = {}
    with get_conn() as conn:
        for i in range(0, len(vals), 400):
            chunk = vals[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT source, zh FROM translations "
                    f"WHERE kind=? AND source IN ({ph})", [kind] + chunk):
                out[r["source"]] = r["zh"]
    return out


def save_translation(kind: str, source: str, zh: str, model: str = "") -> None:
    """写一条翻译(落库); 空结果不写, 留给下次重试"""
    if not source or not zh:
        return
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO translations(kind, source, zh, model, updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(kind, source) DO UPDATE SET
                 zh=excluded.zh, model=excluded.model, updated_at=excluded.updated_at""",
            (kind, source, zh, model, now()))


def get_all_translations(kind: str, limit: int = 5000) -> List[Dict[str, Any]]:
    """某类翻译的全部清单(按时间倒序)"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT kind, source, zh, model, updated_at FROM translations WHERE kind=? "
            "ORDER BY updated_at DESC LIMIT ?",
            (kind, max(1, min(limit, 20000)))).fetchall()
    return [dict(r) for r in rows]


def translation_counts() -> Dict[str, int]:
    """各类翻译的条数"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT kind, COUNT(*) c FROM translations GROUP BY kind").fetchall()
    return {r["kind"]: r["c"] for r in rows}


# ---- 搜索词打标 (父ASIN + 搜索词 → 相关程度) ----
# 顺序即界面上的按钮顺序: 从最相关到最不相关
TERM_LABELS = ("紧密", "宽泛", "不太相关", "不相关")
# 老标签名 → 新标签名 (迁移用); 历史的「紧密相关」统一并到「紧密」
TERM_LABEL_ALIASES = {"紧密相关": "紧密"}


def get_term_label(parent_asin: str, term: str) -> str:
    """单个词的打标; 没标过返回空串"""
    p, t = str(parent_asin or "").strip(), str(term or "").strip()
    if not p or not t:
        return ""
    with get_conn() as conn:
        r = conn.execute(
            "SELECT label FROM term_labels WHERE parent_asin=? AND term=?",
            (p, t)).fetchone()
    return (r["label"] if r else "") or ""


def get_term_labels(parent_asin: str) -> Dict[str, Dict[str, str]]:
    """某父体的全部打标: term -> {label, updated_at}"""
    p = str(parent_asin or "").strip()
    if not p:
        return {}
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT term, label, updated_at FROM term_labels WHERE parent_asin=?",
            (p,)).fetchall()
    return {r["term"]: {"label": r["label"], "updated_at": r["updated_at"]} for r in rows}


def get_term_labels_multi(parents: List[str]) -> Dict[str, Dict[str, str]]:
    """多个父体一次性取打标(列表页批量用): {父ASIN: {搜索词: 标签}}"""
    vals = [str(p).strip() for p in (parents or []) if str(p or "").strip()]
    if not vals:
        return {}
    out: Dict[str, Dict[str, str]] = {}
    with get_conn() as conn:
        for i in range(0, len(vals), 400):
            chunk = vals[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT parent_asin, term, label FROM term_labels "
                    f"WHERE parent_asin IN ({ph})", chunk):
                if r["label"]:
                    out.setdefault(r["parent_asin"], {})[r["term"]] = r["label"]
    return out


def save_term_label(parent_asin: str, term: str, label: str) -> str:
    """写/改打标; label 为空表示清除该词的打标"""
    p, t = str(parent_asin or "").strip(), str(term or "").strip()
    lb = str(label or "").strip()
    if not p or not t:
        raise ValueError("父 ASIN 与搜索词不能为空")
    with _LOCK, get_conn() as conn:
        if not lb:
            conn.execute("DELETE FROM term_labels WHERE parent_asin=? AND term=?", (p, t))
            return ""
        conn.execute(
            """INSERT INTO term_labels(parent_asin, term, label, updated_at)
               VALUES(?,?,?,?)
               ON CONFLICT(parent_asin, term) DO UPDATE SET
                 label=excluded.label, updated_at=excluded.updated_at""",
            (p, t, lb, now()))
    return lb


def term_label_counts() -> Dict[str, int]:
    """各标签的条数"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT label, COUNT(*) c FROM term_labels GROUP BY label").fetchall()
    return {r["label"]: r["c"] for r in rows}


def _child_price_median_map(parent_asins: List[str]) -> Dict[str, Dict[str, Any]]:
    """父 ASIN -> {price_median, child_price_count}

    价格取子体行的 standardPrice(无则 landedPrice), 再取中位数。
    """
    vals = [str(p) for p in (parent_asins or []) if p]
    if not vals:
        return {}
    buckets: Dict[str, List[float]] = {}
    with get_conn() as conn:
        for i in range(0, len(vals), 400):
            chunk = vals[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"""SELECT json_extract(raw_json,'$.parentAsin') pa,
                               json_extract(raw_json,'$.standardPrice') sp,
                               json_extract(raw_json,'$.landedPrice') lp
                        FROM online_products
                        WHERE is_variation='2'
                          AND json_extract(raw_json,'$.parentAsin') IN ({ph})""", chunk):
                pa = r["pa"]
                if not pa:
                    continue
                v = _to_float(r["sp"])
                if v <= 0:
                    v = _to_float(r["lp"])
                if v > 0:
                    buckets.setdefault(str(pa), []).append(v)
    return {p: {"price_median": _median(v), "child_price_count": len(v)}
            for p, v in buckets.items()}


def get_online_products(page: int = 1, page_size: int = 50, keyword: str = "",
                        search_field: str = "", filters: Optional[Dict[str, List[str]]] = None,
                        order_field: str = "", order_dir: str = "desc") -> Dict[str, Any]:
    """在线产品查询: 支持按任意字段过滤 / 关键词(指定字段或整行) / 排序 / 分页"""
    where, args = [], []
    for k, vals in (filters or {}).items():
        vals = [v for v in (vals or []) if v not in (None, "")]
        if not vals or not is_safe_field(k):
            continue
        ph = ",".join("?" * len(vals))
        where.append(f"CAST(json_extract(raw_json,'$.{k}') AS TEXT) IN ({ph})")
        args += [str(v) for v in vals]
    if keyword:
        if search_field and is_safe_field(search_field):
            where.append(f"CAST(json_extract(raw_json,'$.{search_field}') AS TEXT) LIKE ?")
        else:
            where.append("lower(raw_json) LIKE ?")
        args.append(f"%{keyword.lower()}%")
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""

    # 父体视角: 需要补「子体价格中位数」; 该值不在 raw_json 里, 排序要在 Python 侧做
    parent_mode = str(((filters or {}).get("isVariation") or [""])[0]) == "1"
    sort_by_price = parent_mode and order_field == "priceMedian"
    reverse = str(order_dir).lower() != "asc"

    if order_field and is_safe_field(order_field) and not sort_by_price:
        direction = "DESC" if str(order_dir).lower() == "desc" else "ASC"
        if order_field in NUMERIC_FIELDS:
            order_sql = f" ORDER BY CAST(json_extract(raw_json,'$.{order_field}') AS REAL) {direction}"
        else:
            order_sql = f" ORDER BY json_extract(raw_json,'$.{order_field}') COLLATE NOCASE {direction}"
    else:
        order_sql = " ORDER BY id ASC"

    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM online_products{sql_where}", args).fetchone()["c"]
        if sort_by_price:
            raw = conn.execute(
                f"SELECT raw_json FROM online_products{sql_where}", args).fetchall()
        else:
            raw = conn.execute(
                f"SELECT raw_json FROM online_products{sql_where}{order_sql} LIMIT ? OFFSET ?",
                args + [page_size, max(0, (page - 1) * page_size)]).fetchall()
    data = [json.loads(r["raw_json"]) for r in raw]

    # 父体: 补「子体价格中位数」
    if parent_mode and data:
        pm = _child_price_median_map([d.get("asin") for d in data])
        for d in data:
            hit = pm.get(str(d.get("asin") or ""))
            d["_price_median"] = hit["price_median"] if hit else 0.0
            d["_price_child_count"] = hit["child_price_count"] if hit else 0
        if sort_by_price:
            data.sort(key=lambda x: _to_float(x.get("_price_median")), reverse=reverse)
            start = max(0, (page - 1) * page_size)
            data = data[start:start + page_size]

    # 合并「亚马逊商品页补全指标」(赛狐接口里 rating/bsr 恒为 null)
    mm = get_amz_product_metrics_map([d.get("asin") for d in data])
    for d in data:
        m = mm.get(d.get("asin"))
        if not m:
            continue
        d["_m_rating"] = m.get("rating")
        d["_m_rating_count"] = m.get("rating_count")
        d["_m_bsr_small"] = m.get("bsr_small")
        d["_m_bsr_small_cat"] = m.get("bsr_small_cat")
        d["_m_bsr_big"] = m.get("bsr_big")
        d["_m_bsr_big_cat"] = m.get("bsr_big_cat")
        d["_m_fetched_at"] = m.get("fetched_at")
    return {"total": total, "page": page, "page_size": page_size, "rows": data}


# 在线产品查询条件 —— 与原页面(销售>在线产品)的查询框逐一对齐
# (原页面实测: 全部站点 / 全部店铺 / 产品标签 / 在线状态 / 配送类型 / 配对状态 / 划线价类型)
ONLINE_QUERY_SPEC: List[tuple] = [
    ("marketplaceId", "站点"),
    ("shopId", "店铺"),
    ("labelName", "产品标签"),
    ("onlineStatus", "在线状态"),
    ("switchFulfillmentTo", "配送类型"),
    ("match", "配对状态"),
    ("crawlerStrikethroughType", "划线价类型"),
]
# 在线状态取值 -> 原站显示文案
ONLINE_STATUS_LABEL = {"Active": "在售", "active": "在售",
                       "Inactive": "不可售", "inActive": "不可售",
                       "Incomplete": "信息不完整"}


# 亚马逊商品页补全指标 (online_products 里 rating/bsr 恒为 null 时的替代来源)
# 字段名用 _m_ 前缀注入到行记录里, 避免覆盖原始 raw_json 的字段
def save_amz_product_metrics(m: Dict[str, Any]) -> None:
    with _LOCK, get_conn() as conn:
        conn.execute(
            """INSERT INTO amz_product_metrics(asin, domain, rating, rating_count, bsr_small,
                     bsr_small_cat, bsr_big, bsr_big_cat, title, price, blocked, via, fetched_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(asin, domain) DO UPDATE SET
                 rating=excluded.rating, rating_count=excluded.rating_count,
                 bsr_small=excluded.bsr_small, bsr_small_cat=excluded.bsr_small_cat,
                 bsr_big=excluded.bsr_big, bsr_big_cat=excluded.bsr_big_cat,
                 title=COALESCE(NULLIF(excluded.title,''), amz_product_metrics.title),
                 price=COALESCE(NULLIF(excluded.price,''), amz_product_metrics.price),
                 blocked=excluded.blocked, via=excluded.via,
                 fetched_at=excluded.fetched_at""",
            (m.get("asin"), m.get("domain") or "co.jp", m.get("rating"), m.get("rating_count"),
             m.get("bsr_small"), m.get("bsr_small_cat"), m.get("bsr_big"), m.get("bsr_big_cat"),
             m.get("title"), m.get("price"), 1 if m.get("blocked") else 0, m.get("via"), now()))


# ---------------------------------------------------------------------------
# 商品页图片 (主图 + 附图)
# ---------------------------------------------------------------------------
def save_amz_product_images(asin: str, domain: str, images: List[Dict[str, Any]]) -> int:
    """整体替换某 ASIN 的图片集合(先删后插, 保证与最新页面一致)"""
    asin = str(asin or "").strip()
    if not asin:
        return 0
    domain = domain or "co.jp"
    ts = now()
    clean: List[Dict[str, Any]] = []
    seen = set()
    for i, im in enumerate(images or []):
        large = str((im or {}).get("large") or "").strip()
        if not large or large in seen:
            continue
        seen.add(large)
        clean.append({"thumb": (im or {}).get("thumb") or large,
                      "large": large, "source": (im or {}).get("source") or "",
                      "position": i})
    with _LOCK, get_conn() as conn:
        conn.execute("DELETE FROM amz_product_images WHERE asin=? AND domain=?", (asin, domain))
        for c in clean:
            conn.execute(
                """INSERT INTO amz_product_images(asin, domain, position, thumb, large, source,
                                                  updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (asin, domain, c["position"], c["thumb"], c["large"], c["source"], ts))
        # 记录"已抓过"(含 0 张), 供判断父体是否已全量抓取
        conn.execute(
            """INSERT INTO amz_image_crawls(asin, domain, image_count, crawled_at)
               VALUES(?,?,?,?)
               ON CONFLICT(asin, domain) DO UPDATE SET
                 image_count=excluded.image_count, crawled_at=excluded.crawled_at""",
            (asin, domain, len(clean), ts))
    return len(clean)


def get_amz_product_images(asin: str, domain: str = "co.jp") -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT position, thumb, large, source FROM amz_product_images "
            "WHERE asin=? AND domain=? ORDER BY position", (asin, domain or "co.jp")).fetchall()
    return [dict(r) for r in rows]


def get_parent_images(asin: str, domain: str = "co.jp") -> Dict[str, Any]:
    """父 ASIN 的附图 = **某一个子 ASIN** 商品页的**全部**图片

    需求: 只取一个子 ASIN 的详情页附图作为父 ASIN 的附图, 但要把它取全。
    因此这里固定挑一个「图片源子 ASIN」:
      子体按顺序取第一个已抓到图片的; 都没抓到则取第一个子体作为待抓源;
      没有子体时回退父体自身。
    """
    domain = domain or "co.jp"
    with get_conn() as conn:
        child2parent = _asin_parent_map(conn)
        parent = child2parent.get(asin, asin)
        kids = sorted(a for a, p in child2parent.items() if p == parent)
        if parent not in kids:
            kids.append(parent)

    # 候选顺序: 子体优先(需求要求取子 ASIN), 父体兜底
    candidates = [a for a in kids if a != parent] or [parent]

    with get_conn() as conn:
        counts = {r["asin"]: r["c"] for r in conn.execute(
            f"SELECT asin, COUNT(*) c FROM amz_product_images "
            f"WHERE domain=? AND asin IN ({','.join('?' * len(candidates))}) GROUP BY asin",
            [domain] + candidates)}
        crawled_set = {r["asin"] for r in conn.execute(
            f"SELECT asin FROM amz_image_crawls "
            f"WHERE domain=? AND asin IN ({','.join('?' * len(candidates))})",
            [domain] + candidates)}

    source = next((a for a in candidates if counts.get(a)), None) or candidates[0]
    rows = []
    with get_conn() as conn:
        if source:
            rows = [dict(r) for r in conn.execute(
                "SELECT asin, position, thumb, large, source FROM amz_product_images "
                "WHERE domain=? AND asin=? ORDER BY position", (domain, source)).fetchall()]

    # 「取全」: 同源页面内按原图 URL 去重(不同尺寸后缀视为同一张)
    seen = set()
    images: List[Dict[str, Any]] = []
    for d in rows:
        key = str(d.get("large") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        images.append(d)

    pending = [source] if source and source not in crawled_set else []
    return {"asin": asin, "parent_asin": parent, "domain": domain,
            "child_asins": kids, "count": len(images),
            "images": images, "by_asin": ({source: images} if images else {}),
            "sources": ([source] if images else []),
            "source_asin": source,
            "crawled": sorted(crawled_set), "crawled_count": len(crawled_set),
            "pending": pending, "pending_count": len(pending)}


def get_amz_product_metrics(asin: str, domain: str = "co.jp",
                            ttl: Optional[int] = None) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM amz_product_metrics WHERE asin=? AND domain=?",
                           (asin, domain)).fetchone()
    if not row:
        return None
    d = dict(row)
    if ttl is not None and ttl > 0:
        try:
            age = (datetime.now() - datetime.strptime(d.get("fetched_at") or "", "%Y-%m-%d %H:%M:%S")).total_seconds()
            if age > ttl:
                return None
        except Exception:       # noqa: BLE001
            pass
    return d


def get_amz_product_metrics_map(asins: List[str], domain: str = "co.jp") -> Dict[str, Dict[str, Any]]:
    vals = [a for a in (asins or []) if a]
    if not vals:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    with get_conn() as conn:
        for i in range(0, len(vals), 400):
            chunk = vals[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for r in conn.execute(
                    f"SELECT * FROM amz_product_metrics WHERE domain=? AND asin IN ({ph})",
                    [domain] + chunk):
                out[r["asin"]] = dict(r)
    return out


def get_online_filters() -> List[Dict[str, Any]]:
    """返回与原页面一致的筛选框(含库内候选值)；无数据的框也保留(与原站一致)"""
    shops = get_online_meta("shops") or []
    shop_name = {str(s.get("shopId")): s.get("shopName") for s in shops if s.get("shopId")}
    out: List[Dict[str, Any]] = []
    with get_conn() as conn:
        for key, label in ONLINE_QUERY_SPEC:
            rows = conn.execute(
                f"""SELECT CAST(json_extract(raw_json,'$.{key}') AS TEXT) v, COUNT(*) n
                    FROM online_products
                    WHERE json_extract(raw_json,'$.{key}') IS NOT NULL
                      AND CAST(json_extract(raw_json,'$.{key}') AS TEXT) NOT IN ('', 'null')
                    GROUP BY v ORDER BY n DESC LIMIT 100""").fetchall()
            opts = []
            for r in rows:
                v = r["v"]
                if key == "onlineStatus":
                    lab = ONLINE_STATUS_LABEL.get(v, v)
                elif key == "shopId":
                    lab = shop_name.get(str(v), v)
                else:
                    lab = v
                opts.append({"value": v, "label": lab, "count": r["n"]})
            out.append({"key": key, "label": label, "options": opts})
    return out


# ---------------------------------------------------------------------------
# 产品视角: 产品(ASIN) -> 广告活动 -> 搜索词 逐级下钻
#
# 关系说明(实测):
#   产品(ASIN) <-多对多-> 广告活动   (product 页签; 一活动含 1~5 个 ASIN)
#   广告活动 -1对1-> 广告组 -> 一对多 搜索词 (search 页签)
#   ⚠ 搜索词是「活动/广告组级」口径, 接口不支持按 ASIN 拆分
#     (实测 productSearchContents=[ASIN] 对同活动下不同 ASIN 返回完全相同的结果)
# ---------------------------------------------------------------------------
def _to_float(v: Any) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _median(vals: List[float]) -> float:
    """中位数 (空列表返回 0)"""
    xs = sorted(float(v) for v in (vals or []))
    n = len(xs)
    if not n:
        return 0.0
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def _term_counts_by_campaign(conn, tab: str = "search") -> Dict[str, Dict[str, int]]:
    """每个活动的搜索词条数, 按维度拆分: {"sp:keyword": n, "sp:asin": n}"""
    rng = _latest_range(conn, tab)
    sql = ("SELECT json_extract(raw_json,'$.campaignId') cid, scope, COUNT(*) n "
           "FROM ad_records WHERE tab=?")
    args: List[Any] = [tab]
    if rng:
        sql += " AND COALESCE(range_start,'')=? AND COALESCE(range_end,'')=?"
        args += [rng[0], rng[1]]
    sql += " GROUP BY cid, scope"
    out: Dict[str, Dict[str, int]] = {}
    for r in conn.execute(sql, args):
        if r["cid"] is None:
            continue
        out.setdefault(str(r["cid"]), {})[r["scope"]] = r["n"]
    return out


def _latest_range(conn, tab: str) -> Optional[tuple]:
    """某页签**最后一次采集**的时间区间 (快照口径)

    库里可能残留多个区间的数据(历史遗留或采集异常), 按「一次采集=一个快照」,
    统计口径应只看最后一次采集的区间, 否则指标会翻倍。
    """
    row = conn.execute(
        """SELECT range_start, range_end, MAX(updated_at) mx FROM ad_records
           WHERE tab=?
           GROUP BY COALESCE(range_start,'')||'~'||COALESCE(range_end,'')
           ORDER BY mx DESC LIMIT 1""", (tab,)).fetchone()
    if not row:
        return None
    return (row["range_start"] or "", row["range_end"] or "")


def _snapshot_filter(conn, tab: str, col: str = "raw_json") -> tuple:
    """返回 (sql 片段, 参数) —— 把查询限制在该页签最新快照区间内"""
    rng = _latest_range(conn, tab)
    if not rng:
        return "", []
    return (" AND COALESCE(range_start,'')=? AND COALESCE(range_end,'')=?", [rng[0], rng[1]])


def _asin_parent_map(conn) -> Dict[str, str]:
    """子 ASIN -> 父 ASIN 映射 (来源: 在线产品的变体关系)

    在线产品里子体行的 asin=子 ASIN、parentAsin=父 ASIN。没有变体关系的商品
    不会出现在这张表里, 调用方需自行回退为「父=自身」。
    """
    out: Dict[str, str] = {}
    try:
        for r in conn.execute(
                "SELECT asin, parent_asin, raw_json FROM online_products "
                "WHERE is_variation='2'"):
            a = r["asin"]
            p = r["parent_asin"]
            if not p:
                try:
                    p = json.loads(r["raw_json"] or "{}").get("parentAsin")
                except Exception:       # noqa: BLE001
                    p = None
            if a and p:
                out[a] = p
    except Exception:       # noqa: BLE001  未采集在线产品时不影响广告数据
        return {}
    return out


def _parent_meta_map(conn) -> Dict[str, Dict[str, Any]]:
    """父 ASIN 自身的信息 (SKU/标题/主图), 用于合并记录的展示"""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        for r in conn.execute(
                "SELECT asin, sku, title, main_image FROM online_products "
                "WHERE is_variation='1'"):
            if r["asin"]:
                out[r["asin"]] = {"sku": r["sku"], "title": r["title"],
                                  "img_url": r["main_image"]}
    except Exception:       # noqa: BLE001
        return {}
    return out


def _asin_group_map(conn) -> Dict[str, List[str]]:
    """父 ASIN -> 该父体下的全部子 ASIN (来自在线产品, 不受广告数据覆盖度影响)"""
    out: Dict[str, List[str]] = {}
    try:
        for r in conn.execute(
                "SELECT asin, parent_asin, raw_json FROM online_products "
                "WHERE is_variation='2'"):
            a = r["asin"]
            p = r["parent_asin"]
            if not p:
                try:
                    p = json.loads(r["raw_json"] or "{}").get("parentAsin")
                except Exception:       # noqa: BLE001
                    p = None
            if a and p:
                out.setdefault(p, []).append(a)
    except Exception:       # noqa: BLE001
        return {}
    return out


def get_products(keyword: str = "", order_field: str = "ad_cost",
                 order_dir: str = "desc", page: int = 1,
                 page_size: int = 50) -> Dict[str, Any]:
    """产品列表 —— **以父 ASIN 为一条记录**

    同一父 ASIN 下的多个子 ASIN 合并统计:
      指标(花费/曝光/点击/销量/销售额)按活动累加;
      关联活动数取「这些子 ASIN 参与过的活动」的并集(去重);
      搜索词条目按并集活动的活动级口径累加。
    没有变体关系(在线产品里查不到父体)的 ASIN 退化为「父 = 自身」。
    """
    with get_conn() as conn:
        pf, pa_ = _snapshot_filter(conn, "product")      # 只看 product 页签最新快照
        prow = [json.loads(r["raw_json"]) for r in conn.execute(
            "SELECT raw_json FROM ad_records WHERE tab='product'" + pf, pa_)]
        term_map = _term_counts_by_campaign(conn)
        child2parent = _asin_parent_map(conn)
        parent_meta = _parent_meta_map(conn)
        group_map = _asin_group_map(conn)

    agg: Dict[str, Dict[str, Any]] = {}
    for r in prow:
        a = r.get("asin")
        if not a:
            continue
        parent = child2parent.get(a) or a          # 归到父 ASIN
        d = agg.setdefault(parent, {
            "asin": parent, "parent_asin": parent, "title": "", "img_url": "",
            "sku": "", "price": "",
            "_kids": set(), "_camps": set(), "_prices": [],
            "ad_cost": 0.0, "impressions": 0.0, "clicks": 0.0,
            "ad_sales": 0.0, "order_num": 0.0, "active_campaign_count": 0,
        })
        d["_kids"].add(a)
        if not d["title"] and r.get("title"):
            d["title"] = r["title"]
        if not d["img_url"] and r.get("imgUrl"):
            d["img_url"] = r["imgUrl"]
        if not d["sku"] and r.get("sku"):
            d["sku"] = r["sku"]
        if not d["price"] and r.get("price"):
            d["price"] = r["price"]
        # 收集各子体的售价, 供父 ASIN 取中位数
        try:
            pv = float(str(r.get("price")).replace(",", ""))
            if pv > 0:
                d["_prices"].append(pv)
        except (TypeError, ValueError):
            pass
        cid = str(r.get("campaignId") or "")
        if cid:
            d["_camps"].add(cid)
        d["ad_cost"] += _to_float(r.get("adCost"))
        d["impressions"] += _to_float(r.get("impressions"))
        d["clicks"] += _to_float(r.get("clicks"))
        d["ad_sales"] += _to_float(r.get("adSales"))
        d["order_num"] += _to_float(r.get("orderNum"))
        if str(r.get("state")) == "enabled":
            d["active_campaign_count"] += 1

    rows: List[Dict[str, Any]] = []
    for p, d in agg.items():
        camps = d.pop("_camps")
        kids = sorted(d.pop("_kids"))
        d["child_asins"] = kids
        d["child_count"] = len(kids)
        # 全部子 ASIN(含未投广告的), 用于界面展示完整变体列表
        all_kids = sorted(set(group_map.get(p, [])) | set(kids))
        if all_kids:
            d["all_child_asins"] = all_kids
        # 全部子 ASIN 数(含未投放广告的变体), 供「子体数」列排序
        d["all_child_count"] = len(all_kids) or len(kids)
        # 父体自身的 SKU/标题/主图优先(合并记录的可读性更好)
        pm = parent_meta.get(p) or {}
        d["parent_sku"] = pm.get("sku") or ""
        if pm.get("sku"):
            d["sku"] = pm["sku"]
        if pm.get("title") and not d["title"]:
            d["title"] = pm["title"]
        if pm.get("img_url") and not d["img_url"]:
            d["img_url"] = pm["img_url"]
        d["campaign_count"] = len(camps)
        d["term_campaign_count"] = sum(1 for c in camps if c in term_map)
        d["keyword_term_count"] = sum(term_map.get(c, {}).get("sp:keyword", 0) for c in camps)
        d["targeting_term_count"] = sum(term_map.get(c, {}).get("sp:asin", 0) for c in camps)
        d["search_term_count"] = d["keyword_term_count"] + d["targeting_term_count"]
        d["ctr"] = (d["clicks"] / d["impressions"]) if d["impressions"] else 0.0
        d["acos"] = (d["ad_cost"] / d["ad_sales"]) if d["ad_sales"] else 0.0
        d["cvr"] = (d["order_num"] / d["clicks"]) if d["clicks"] else 0.0
        # 父 ASIN 售价 = 该父体下全部子体售价的**中位数**
        prices = d.pop("_prices", [])
        d["price_median"] = _median(prices)
        d["price_child_count"] = len(prices)
        if not d["price"] and d["price_median"]:
            d["price"] = f"{d['price_median']:.2f}"
        rows.append(d)

    if keyword:
        kw = keyword.lower()

        def _hit(r: Dict[str, Any]) -> bool:
            hay = [str(r["parent_asin"]), str(r["title"]), str(r["sku"])]
            hay += [str(x) for x in (r["child_asins"] or [])]
            hay += [str(x) for x in (r.get("all_child_asins") or [])]
            return any(kw in h.lower() for h in hay)

        rows = [r for r in rows if _hit(r)]

    reverse = str(order_dir).lower() != "asc"
    # 可排序字段白名单: 数值列 + 文本列(文本走 lower() 比较, 数值走 _to_float)
    NUM_KEYS = {
        "ad_cost", "impressions", "clicks", "ad_sales", "order_num",
        "campaign_count", "active_campaign_count",
        "term_campaign_count", "search_term_count",
        "keyword_term_count", "targeting_term_count",
        "child_count", "all_child_count",
        "ctr", "acos", "cvr", "price_median", "price_child_count",
    }
    TEXT_KEYS = {"asin", "parent_asin", "sku", "title"}
    key = order_field if order_field in (NUM_KEYS | TEXT_KEYS) else "ad_cost"
    if key in TEXT_KEYS:
        rows.sort(key=lambda r: str(r.get(key) or "").lower(), reverse=reverse)
    else:
        rows.sort(key=lambda r: _to_float(r.get(key)), reverse=reverse)

    total = len(rows)
    start = (page - 1) * page_size
    return {"total": total, "page": page, "page_size": page_size,
            "group_by": "parent_asin", "snapshot": _snapshot_info(),
            "totals": _sum_product_rows(rows),
            "rows": rows[start:start + page_size]}


def _sum_product_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """产品列表表头「合计」：对**当前查询的全部行**(非仅当前页)求和。

    可累加的直接求和；比率类(CTR/ACOS/CVR)用总量重算，不能把各行的比率相加。
    """
    def s(k: str) -> float:
        return sum(_to_float(r.get(k)) for r in rows)

    n_parent = len(rows)
    cost, imp, clk = s("ad_cost"), s("impressions"), s("clicks")
    sales, orders = s("ad_sales"), s("order_num")
    return {
        "parent_count": n_parent,
        "child_count": int(s("child_count")),
        "all_child_count": int(s("all_child_count")),
        "campaign_count": int(s("campaign_count")),
        "term_campaign_count": int(s("term_campaign_count")),
        "search_term_count": int(s("search_term_count")),
        "ad_cost": cost, "impressions": imp, "clicks": clk,
        "ad_sales": sales, "order_num": orders,
        "ctr": (clk / imp) if imp else 0.0,
        "acos": (cost / sales) if sales else 0.0,
        "cvr": (orders / clk) if clk else 0.0,
        # 售价列不给"合计"(无意义), 表头显示各父体售价中位数
        "price_median": _median([r.get("price_median") for r in rows
                                 if r.get("price_median")]) if rows else 0.0,
    }


def _snapshot_info() -> Dict[str, str]:
    """当前统计口径所用的快照区间 (取 product 页签最新一次采集的区间)"""
    with get_conn() as conn:
        rng = _latest_range(conn, "product")
    return {"range_start": rng[0], "range_end": rng[1]} if rng else {}


def get_product_campaigns(asin: str) -> Dict[str, Any]:
    """某父 ASIN 下**全部子 ASIN** 参与的活动 —— 同活动合并为一行

    asin 参数既接受父 ASIN, 也接受子 ASIN(会自动换算到其父体)。
    """
    with get_conn() as conn:
        child2parent = _asin_parent_map(conn)
        parent = child2parent.get(asin, asin)
        kids = sorted(a for a, p in child2parent.items() if p == parent)
        if parent not in kids:
            kids.append(parent)
        ph = ",".join("?" * len(kids))
        pf, pargs = _snapshot_filter(conn, "product")
        prow = [json.loads(r["raw_json"]) for r in conn.execute(
            f"SELECT raw_json FROM ad_records WHERE tab='product' "
            f"AND json_extract(raw_json,'$.asin') IN ({ph})" + pf, kids + pargs)]
        cf, cargs = _snapshot_filter(conn, "campaign")
        camps = {str(json.loads(r["raw_json"]).get("campaignId")): json.loads(r["raw_json"])
                 for r in conn.execute(
                     "SELECT raw_json FROM ad_records WHERE tab='campaign'" + cf, cargs)}
        term_map = _term_counts_by_campaign(conn)

    merged: Dict[str, Dict[str, Any]] = {}
    for p in prow:
        cid = str(p.get("campaignId") or "")
        c = camps.get(cid, {})
        d = merged.setdefault(cid, {
            "campaignId": cid,
            "campaignName": c.get("name") or p.get("campaignName"),
            "adGroupId": str(p.get("adGroupId") or ""),
            "adGroupName": p.get("adGroupName"),
            "adType": c.get("type") or p.get("type"),
            "state": c.get("state") or p.get("campaignState"),
            "servingStatus": c.get("servingStatus"),
            "startDate": c.get("startDate"),
            "dailyBudget": c.get("dailyBudget"),
            "strategy": c.get("strategy") or c.get("campaignTargetingType"),
            "portfolioName": c.get("portfolioName"),
            "devNames": c.get("devNames"),
            "ad_cost": 0.0, "impressions": 0.0, "clicks": 0.0,
            "ad_sales": 0.0, "order_num": 0.0,
            "ad_state": p.get("state"),
            "title": p.get("title"), "img_url": p.get("imgUrl"),
            "_asins": set(),
        })
        d["_asins"].add(p.get("asin"))
        d["ad_cost"] += _to_float(p.get("adCost"))
        d["impressions"] += _to_float(p.get("impressions"))
        d["clicks"] += _to_float(p.get("clicks"))
        d["ad_sales"] += _to_float(p.get("adSales"))
        d["order_num"] += _to_float(p.get("orderNum"))
        if not d["img_url"] and p.get("imgUrl"):
            d["img_url"] = p.get("imgUrl")

    rows: List[Dict[str, Any]] = []
    for cid, d in merged.items():
        tm = term_map.get(cid, {})
        d["child_asins"] = sorted(x for x in d.pop("_asins") if x)
        d["keyword_term_count"] = tm.get("sp:keyword", 0)
        d["targeting_term_count"] = tm.get("sp:asin", 0)
        d["search_term_count"] = sum(tm.values())
        rows.append(d)
    rows.sort(key=lambda r: -r["ad_cost"])
    return {"parent_asin": parent, "asin": parent,
            "child_asins": kids, "total": len(rows), "rows": rows}


def get_parent_terms(asin: str, scope: str = "", limit: int = 2000) -> Dict[str, Any]:
    """某父 ASIN 下**全部子 ASIN 参与的全部活动**的搜索词/商品投放汇总

    同一 (维度, 搜索词, 匹配方式) 跨多个活动时做合并累加, 并记录涉及活动数。
    asin 传父 ASIN 或子 ASIN 均可(自动换算)。
    """
    camp = get_product_campaigns(asin)
    parent = camp["parent_asin"]
    kids = camp["child_asins"]
    cids = [str(r["campaignId"]) for r in camp["rows"] if r.get("campaignId")]
    if not cids:
        return {"parent_asin": parent, "child_asins": kids, "campaign_count": 0,
                "scope": scope, "counts": {"all": 0, "keyword": 0, "targeting": 0},
                "total": 0, "rows": []}

    ph = ",".join("?" * len(cids))
    sql = ("SELECT scope, raw_json FROM ad_records WHERE tab='search' "
           "AND json_extract(raw_json,'$.campaignId') IN (" + ph + ")")
    with get_conn() as conn:
        raw = conn.execute(sql, list(cids)).fetchall()
        # ABA 搜索词排名: 部分活动/行不带该字段, 用「全库同名词」的排名回填, 提升覆盖率
        rank_map: Dict[str, Any] = {}
        for r in conn.execute("SELECT raw_json FROM ad_records WHERE tab='search'"):
            try:
                dd = json.loads(r["raw_json"])
            except Exception:       # noqa: BLE001
                continue
            q = str(dd.get("query") or "")
            rk = dd.get("searchFrequencyRank")
            if q and rk not in (None, "", 0, "0") and q not in rank_map:
                rank_map[q] = rk

    merged: Dict[tuple, Dict[str, Any]] = {}
    for r in raw:
        d = json.loads(r["raw_json"])
        is_kw = r["scope"].endswith("keyword")
        key = (r["scope"], str(d.get("query") or ""), str(d.get("matchType") or ""))
        m = merged.setdefault(key, {
            "scope": r["scope"],
            "dimension": "搜索词" if is_kw else "商品投放",
            "query": d.get("query"),
            "matchType": d.get("matchType"),
            "impressions": 0.0, "clicks": 0.0, "adCost": 0.0,
            "adSales": 0.0, "orderNum": 0.0,
            "searchFrequencyRank": d.get("searchFrequencyRank"),
            "adGroupNames": set(), "_cids": set(),
        })
        m["impressions"] += _to_float(d.get("impressions"))
        m["clicks"] += _to_float(d.get("clicks"))
        m["adCost"] += _to_float(d.get("adCost"))
        m["adSales"] += _to_float(d.get("adSales"))
        m["orderNum"] += _to_float(d.get("orderNum"))
        if d.get("adGroupName"):
            m["adGroupNames"].add(str(d.get("adGroupName")))
        m["_cids"].add(str(d.get("campaignId") or ""))
        if m["searchFrequencyRank"] in (None, "", 0, "0"):
            m["searchFrequencyRank"] = d.get("searchFrequencyRank")

    all_rows: List[Dict[str, Any]] = []
    for m in merged.values():
        m["campaign_count"] = len(m.pop("_cids"))
        m["adGroupNames"] = sorted(m["adGroupNames"])
        # ABA 排名缺失时用全库同名词回填
        if m["searchFrequencyRank"] in (None, "", 0, "0"):
            m["searchFrequencyRank"] = rank_map.get(str(m.get("query") or ""))
        all_rows.append(m)
    all_rows.sort(key=lambda r: -r["adCost"])

    # 计数按**合并后**的行数算(与表格一致), 且不受当前 scope 过滤影响
    counts = {
        "all": len(all_rows),
        "keyword": sum(1 for m in all_rows if m["scope"].endswith("keyword")),
        "targeting": sum(1 for m in all_rows if not m["scope"].endswith("keyword")),
    }
    rows = [m for m in all_rows if not scope or m["scope"] == scope]
    return {"parent_asin": parent, "child_asins": kids, "scope": scope, "counts": counts,
            "campaign_count": len(cids), "total": len(rows), "rows": rows[:limit]}


def get_parent_profile(asin: str, domain: str = "co.jp") -> Dict[str, Any]:
    """父 ASIN 的资料: 标题 / 价格 / 主图 + 全部附图(来自子体商品页)

    价格优先取亚马逊商品页, 回退赛狐商品行; 图片取「父体 + 全部子体」商品页附图合集。
    """
    domain = domain or "co.jp"
    with get_conn() as conn:
        child2parent = _asin_parent_map(conn)
        parent = child2parent.get(asin, asin)
        kids = sorted(a for a, p in child2parent.items() if p == parent)
        if parent not in kids:
            kids.append(parent)
        meta = _parent_meta_map(conn).get(parent) or {}

        # 赛狐商品行兜底(标题/图片/价格)
        prod = {}
        try:
            pf, pa_ = _snapshot_filter(conn, "product")
            for r in conn.execute(
                    "SELECT raw_json FROM ad_records WHERE tab='product' "
                    "AND json_extract(raw_json,'$.asin') IN (" +
                    ",".join("?" * len(kids)) + ")" + pf, kids + pa_):
                d = json.loads(r["raw_json"])
                if not prod.get("title") and d.get("title"):
                    prod["title"] = d.get("title")
                if not prod.get("img_url") and d.get("imgUrl"):
                    prod["img_url"] = d.get("imgUrl")
                if not prod.get("price") and d.get("price"):
                    prod["price"] = d.get("price")
                prod.setdefault("skus", [])
                if d.get("sku") and d["sku"] not in prod["skus"]:
                    prod["skus"].append(d["sku"])
        except Exception:       # noqa: BLE001
            pass

    # 亚马逊商品页指标(价格 / 星级 / 评论 / BSR)
    amz = {}
    try:
        amz_map = get_amz_product_metrics_map([parent] + kids, domain)
        for a in [parent] + kids:
            m = amz_map.get(a)
            if not m:
                continue
            if not amz.get("title") and m.get("title"):
                amz["title"] = m["title"]
            if not amz.get("price") and m.get("price"):
                amz["price"] = m["price"]
            if not amz.get("rating") and m.get("rating"):
                amz["rating"] = m["rating"]
                amz["rating_count"] = m.get("rating_count")
            if not amz.get("bsr_small") and m.get("bsr_small"):
                amz["bsr_small"] = m["bsr_small"]
                amz["bsr_small_cat"] = m.get("bsr_small_cat")
            amz.setdefault("sources", []).append(a)
    except Exception:       # noqa: BLE001
        pass

    imgs = get_parent_images(parent, domain)
    source_asin = imgs.get("source_asin") or ""
    all_imgs = imgs.get("images") or []
    # 主图 = 图片来源子 ASIN 的主图; 其余为「附图」
    main = ""
    for im in all_imgs:
        if im.get("source") == "main":
            main = im["large"]
            break
    if not main and all_imgs:
        main = all_imgs[0]["large"]
    extra = [im for im in all_imgs if im["large"] != main]
    fallback_img = prod.get("img_url") or meta.get("img_url") or ""
    return {
        "asin": asin, "parent_asin": parent, "child_asins": kids,
        "sku": meta.get("sku") or (prod.get("skus") or [""])[0],
        "title": amz.get("title") or meta.get("title") or prod.get("title") or "",
        "price": amz.get("price") or prod.get("price") or "",
        "price_from": "amazon" if amz.get("price") else ("sellfox" if prod.get("price") else ""),
        "main_image": main or fallback_img,
        "images": extra,                       # 附图 = 来源子体的其余图片(不含主图)
        "image_count": len(extra),             # 附图张数
        "image_total": len(all_imgs),          # 来源子体商品页图片总数
        "images_by_asin": imgs.get("by_asin") or {},
        "image_asins": imgs.get("sources") or [],
        "image_source_asin": source_asin,      # 图片来源子 ASIN
        "image_source_crawled": source_asin in (imgs.get("crawled") or []),
        "child_count": len(kids),
        "crawled_count": imgs.get("crawled_count") or 0,
        "pending_asins": imgs.get("pending") or [],
        "amz": amz,
        "img_url": main or fallback_img,       # 兼容旧字段
    }


def get_term_detail(asin: str, query: str, match_type: str = "",
                    domain: str = "co.jp") -> Dict[str, Any]:
    """单个搜索词在「某父 ASIN 全部活动」口径下的指标 + 父体商品资料

    说明: 搜索词是活动级口径, 这里把该父体下所有活动里**同一个词**的指标合并累加。
    """
    q = str(query or "").strip()
    pt = get_parent_terms(asin)
    parent = pt["parent_asin"]
    matched = [r for r in pt["rows"] if str(r.get("query") or "") == q]
    if match_type:
        mt = [r for r in matched if str(r.get("matchType") or "") == str(match_type)]
        if mt:
            matched = mt

    agg = {
        "query": q, "scope": "", "dimension": "", "matchType": match_type or "",
        "impressions": 0.0, "clicks": 0.0, "adCost": 0.0, "adSales": 0.0,
        "orderNum": 0.0, "adSaleNum": 0.0, "searchFrequencyRank": None,
        "campaign_count": 0, "adGroupNames": [], "rows": len(matched),
    }
    cids, ags = set(), set()
    for r in matched:
        agg["impressions"] += _to_float(r.get("impressions"))
        agg["clicks"] += _to_float(r.get("clicks"))
        agg["adCost"] += _to_float(r.get("adCost"))
        agg["adSales"] += _to_float(r.get("adSales"))
        agg["orderNum"] += _to_float(r.get("orderNum"))
        if not agg["scope"]:
            agg["scope"] = r.get("scope") or ""
            agg["dimension"] = r.get("dimension") or ""
            agg["matchType"] = agg["matchType"] or r.get("matchType") or ""
        if not agg["searchFrequencyRank"] and r.get("searchFrequencyRank"):
            agg["searchFrequencyRank"] = r.get("searchFrequencyRank")
        if r.get("campaign_count"):
            cids.add(r.get("campaign_count"))
        for n in (r.get("adGroupNames") or []):
            ags.add(n)
    agg["adGroupNames"] = sorted(ags)
    # 涉及活动数: 汇总行里同词可能来自多个活动 -> 累加其 campaign_count
    agg["campaign_count"] = sum(int(r.get("campaign_count") or 0) for r in matched)

    imp, clk, cost = agg["impressions"], agg["clicks"], agg["adCost"]
    sales, orders = agg["adSales"], agg["orderNum"]
    agg["ctr"] = ratio(clk, imp) * 100
    agg["cvr"] = ratio(orders, clk) * 100
    agg["cpc"] = ratio(cost, clk)
    agg["cpa"] = ratio(cost, orders)
    agg["acos"] = ratio(cost, sales) * 100
    agg["roas"] = ratio(sales, cost)
    agg["aov"] = ratio(sales, orders)
    agg["cpm"] = ratio(cost, imp) * 1000
    agg["found"] = bool(matched)

    return {
        "parent_asin": parent, "query": q, "child_asins": pt["child_asins"],
        "product": get_parent_profile(parent, domain),
        "term": agg,
        "variants": matched,
    }


def ratio(a: float, b: float) -> float:
    return (a / b) if b else 0.0


def get_parent_title(asin: str) -> str:
    """父 ASIN 的标题(轻量版: 只读赛狐商品行, 不碰亚马逊)

    asin 传父/子 ASIN 均可; 优先父体自身商品行, 回退其子体任一标题。
    """
    with get_conn() as conn:
        child2parent = _asin_parent_map(conn)
        parent = child2parent.get(asin, asin)
        kids = [parent] + sorted(a for a, p in child2parent.items() if p == parent)
        try:
            pf, pa_ = _snapshot_filter(conn, "product")
        except Exception:       # noqa: BLE001
            pf, pa_ = "", []
        for r in conn.execute(
                "SELECT raw_json FROM ad_records WHERE tab='product' "
                "AND json_extract(raw_json,'$.asin') IN (" +
                ",".join("?" * len(kids)) + ")" + pf, kids + pa_):
            t = (json.loads(r["raw_json"]).get("title") or "").strip()
            if t:
                return t
    return ""


def get_campaign_terms(campaign_id: str, scope: str = "") -> Dict[str, Any]:
    """某广告活动的搜索词/商品投放明细 (活动级口径)

    返回 counts 为该活动**不受 scope 过滤影响**的条目数:
      {"all": n, "keyword": n, "targeting": n}
    —— 前端用三个过滤按钮的计数不能随当前过滤而变, 否则会出现「只看商品投放 (0)」。
    """
    sql = ("SELECT scope, raw_json FROM ad_records WHERE tab='search' "
           "AND json_extract(raw_json,'$.campaignId')=?")
    with get_conn() as conn:
        raw = conn.execute(sql, (str(campaign_id),)).fetchall()
    counts = {"all": 0, "keyword": 0, "targeting": 0}
    rows: List[Dict[str, Any]] = []
    for r in raw:
        is_kw = r["scope"].endswith("keyword")
        counts["all"] += 1
        counts["keyword" if is_kw else "targeting"] += 1
        if scope and r["scope"] != scope:
            continue
        d = json.loads(r["raw_json"])
        rows.append({
            "scope": r["scope"],
            "dimension": "搜索词" if is_kw else "商品投放",
            "query": d.get("query"),
            "matchType": d.get("matchType"),
            "impressions": _to_float(d.get("impressions")),
            "clicks": _to_float(d.get("clicks")),
            "adCost": _to_float(d.get("adCost")),
            "adSales": _to_float(d.get("adSales")),
            "orderNum": _to_float(d.get("orderNum")),
            "searchFrequencyRank": d.get("searchFrequencyRank"),
            "adGroupName": d.get("adGroupName"),
        })
    rows.sort(key=lambda r: -r["adCost"])
    return {"campaign_id": str(campaign_id), "scope": scope, "counts": counts,
            "total": len(rows), "rows": rows}
