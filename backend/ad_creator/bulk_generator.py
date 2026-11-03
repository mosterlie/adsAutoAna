"""亚马逊 SP 批量操作（Bulk Operations）Excel 生成引擎。

输出符合亚马逊日本站 Sponsored Products 批量操作模板的单个工作表：
``Sponsored Products Campaigns``。

每个父体生成一个自动投放活动，行结构：
    Campaign -> [Bidding Adjustment(top) -> Bidding Adjustment(product page)]
    -> Ad Group -> Product Ad(每个子体一行) -> Product Targeting(4 组自动匹配)

表头严格对齐亚马逊官方模板 ``AdvertisingBulksheetTemplate-seller.xlsx``
中「商品推广活动」工作表的 31 列（列名、顺序完全一致），末尾额外追加一列
``ASIN`` 便于人工复核（亚马逊按表头精确匹配列，未知列会被忽略）。

⚠️ v9 修复（关键）
--------------------------------------------------------------------
v8 修复后第二次上传，19844 行 / 成功 0 行：

    5164 行 Product Targeting -> ``用户输入无效 / 以下列缺少值：拓展商品投放编号``
    14680 行（Campaign / Ad Group / Product Ad / Bidding Adjustment）
          -> ``由于其他行的错误，该行未得到处理``（级联，非自身错误）

根因：**我们把自动投放表达式写进了一列自己造的表头**。官方模板里承载
「Product Targeting Expression」的列，中文名是 **``拓展商品投放编号``**
（对照官方英文表头 ``Product Targeting Expression``，位于第 27 列，
紧跟 ``百分比`` 之后）。我们此前写的是 ``商品投放表达式``——亚马逊不认识
这个表头，于是整列被忽略，随后按模板规范报「该列缺少值」。

同时修正两处表头名称漂移（此前靠"值恰好为空"侥幸没报错）：

| 我们的旧表头   | 官方模板表头   | 官方英文                    |
|----------------|----------------|-----------------------------|
| 商品投放编号   | ``商品投放 ID``  | Product Targeting ID        |
| 关键词         | ``关键词文本``   | Keyword Text                |
| 商品投放表达式 | ``拓展商品投放编号`` | Product Targeting Expression |

并补齐官方模板中缺失的 6 列：``母语关键词`` ``母语区域`` ``受众编号``
``购物者群体占比`` ``购物者群体类型`` ``站点``（均留空）。

⚠️ v8 修复（对照亚马逊 Bulksheet 2.0 规范）
--------------------------------------------------------------------
上一版生成的表格被亚马逊后台整体拒绝（19853 行 / 成功 0 行），报错
``无效实体：“Targeting”`` 共 5168 行，其余 14685 行因级联而「未得到处理」。

根因是**自动投放四组的行结构写成了旧版格式**，共 4 处字段值不合规：

| 列           | 旧（错）                              | 新（对）                        |
|--------------|---------------------------------------|---------------------------------|
| 实体层级     | ``Targeting``                         | ``Product Targeting``           |
| 投放表达式   | ``targeting_expression = "close-match"`` | ``close-match``（裸值）      |
| 投放类型     | ``auto``                              | ``Auto``                        |
| 广告位       | ``placement top`` / ``placement product page`` | ``Placement Top`` / ``Placement Product Page`` |

要点：自动投放的四个投放组**不是**独立的 ``Targeting`` 实体，而是
``Product Targeting`` 实体下的四行，表达式取亚马逊受控值
``close-match`` / ``loose-match`` / ``substitutes`` / ``complements``，
每行可独立设置竞价与状态；``关键词文本`` 与 ``匹配类型`` 两列必须留空。
"""

from __future__ import annotations

import datetime
import io
from typing import Any, Dict, List

import pandas as pd

SHEET_NAME = "Sponsored Products Campaigns"

# ---------------------------------------------------------------------------
# 亚马逊 SP 批量操作模板列顺序
#   —— 与官方模板「商品推广活动」工作表 31 列表头逐字一致（v9 对齐）。
# ---------------------------------------------------------------------------
OFFICIAL_COLUMNS: List[str] = [
    "产品",              # 1  Product
    "实体层级",          # 2  Entity
    "操作",              # 3  Operation
    "广告活动编号",      # 4  Campaign ID
    "广告组编号",        # 5  Ad Group ID
    "广告组合编号",      # 6  Portfolio ID
    "广告编号",          # 7  Ad ID
    "关键词编号",        # 8  Keyword ID
    "商品投放 ID",       # 9  Product Targeting ID      （v9：原「商品投放编号」）
    "广告活动名称",      # 10 Campaign Name
    "广告组名称",        # 11 Ad Group Name
    "开始日期",          # 12 Start Date
    "结束日期",          # 13 End Date
    "投放类型",          # 14 Targeting Type
    "状态",              # 15 State
    "每日预算",          # 16 Daily Budget
    "SKU",               # 17 SKU
    "广告组默认竞价",    # 18 Ad Group Default Bid
    "竞价",              # 19 Bid
    "关键词文本",        # 20 Keyword Text             （v9：原「关键词」）
    "母语关键词",        # 21 Native Language Keyword   （v9 新增）
    "母语区域",          # 22 Native Language Locale    （v9 新增）
    "匹配类型",          # 23 Match Type
    "竞价方案",          # 24 Bidding Strategy
    "广告位",            # 25 Placement
    "百分比",            # 26 Percentage
    "拓展商品投放编号",  # 27 Product Targeting Expression ★ v9 关键列
    "受众编号",          # 28 Audience ID               （v9 新增）
    "购物者群体占比",    # 29 Shopper Cohort Percentage （v9 新增）
    "购物者群体类型",    # 30 Shopper Cohort Type       （v9 新增）
    "站点",              # 31 Sites                     （v9 新增）
]

# 非官方辅助列：仅供人工复核，亚马逊按表头精确匹配、会忽略未知列。
HELPER_COLUMNS: List[str] = ["ASIN"]

BULK_COLUMNS: List[str] = OFFICIAL_COLUMNS + HELPER_COLUMNS

# 承载自动投放表达式的官方列名（v9 关键）
TARGETING_EXPR_COLUMN = "拓展商品投放编号"

# ---------------------------------------------------------------------------
# 亚马逊 SP「实体层级」合法取值（Bulksheet 2.0）
#   注意：不存在 ``Targeting`` 这个实体；自动投放的四个投放组属于
#   ``Product Targeting`` 实体。生成后由 _validate_output() 自检。
# ---------------------------------------------------------------------------
VALID_ENTITIES = frozenset(
    {
        "Campaign",
        "Ad Group",
        "Product Ad",
        "Keyword",
        "Negative Keyword",
        "Product Targeting",
        "Negative Product Targeting",
        "Campaign Negative Keyword",
        "Campaign Negative Product Targeting",
        "Bidding Adjustment",
    }
)

# 自动投放的 4 组匹配类型（亚马逊受控值，写入「拓展商品投放编号」列的裸值）
AUTO_TARGETING = ["close-match", "loose-match", "substitutes", "complements"]

# 否定投放实体（活动级 / 广告组级）
NEG_KEYWORD_ENTITIES = frozenset({"Campaign Negative Keyword", "Negative Keyword"})
NEG_PT_ENTITIES = frozenset(
    {"Campaign Negative Product Targeting", "Negative Product Targeting"}
)
# 否定关键词匹配类型受控值（无 broad）
NEG_MATCH_TYPES = frozenset({"negativeExact", "negativePhrase"})

# 「投放类型」受控值
TARGETING_TYPE_AUTO = "Auto"
TARGETING_TYPE_MANUAL = "Manual"

# 「广告位」受控值
PLACEMENT_TOP = "Placement Top"
PLACEMENT_PRODUCT_PAGE = "Placement Product Page"
PLACEMENT_REST_OF_SEARCH = "Placement Rest Of Search"

# 「百分比」合法区间（亚马逊受控范围）
PERCENTAGE_MIN = 0
PERCENTAGE_MAX = 900


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def generate_bulk_excel(
    products: List[Dict[str, Any]], settings: Dict[str, Any]
) -> io.BytesIO:
    """按亚马逊官方格式组装 Sponsored Products 批量操作表格。"""
    rows: List[Dict[str, Any]] = []
    today_str = datetime.datetime.now().strftime("%Y%m%d")

    bidding_strategy = settings.get("bidding_strategy", "Fixed bid")
    default_bid = _to_float(settings.get("default_bid", 25.0), 25.0)
    top_adj = _to_float(settings.get("placement_top_pct", 0.0))
    rest_adj = _to_float(settings.get("placement_rest_pct", 0.0))
    prod_adj = _to_float(settings.get("placement_product_pct", 0.0))

    # 可选：自定义活动 / 广告组名称前缀（赛狐式表单）
    campaign_name_custom = str(settings.get("campaign_name") or "").strip()
    ad_group_name_custom = str(settings.get("ad_group_name") or "").strip()

    bid_close = _to_float(settings.get("bid_close", 25.0), 25.0)
    bid_loose = _to_float(settings.get("bid_loose", 20.0), 20.0)
    bid_sub = _to_float(settings.get("bid_substitutes", 25.0), 25.0)
    bid_comp = _to_float(settings.get("bid_complements", 20.0), 20.0)
    targeting_bids = {
        "close-match": bid_close,
        "loose-match": bid_loose,
        "substitutes": bid_sub,
        "complements": bid_comp,
    }
    # 是否显式生成 4 组自动匹配 Product Targeting 行（关闭则所有匹配类型继承
    # 广告组默认竞价，与不带 Targeting 行的历史 V3 文件保持一致）
    include_targeting = bool(settings.get("include_targeting", True))
    # 细到「哪几组匹配要生成」：页面 4 组各带一个启用勾选
    #   取值 close-match / loose-match / substitutes / complements；空 = 全部
    target_types = [
        str(t) for t in (settings.get("targeting_types") or []) if str(t).strip()
    ]
    include_placement = bool(settings.get("include_placement", True))

    portfolio_id = settings.get("portfolio_id", "") or ""

    # 否定投放：keywords = [{text, match}] 或 ["词1", ...]；asins = ["B0...", ...]
    neg_keywords = settings.get("negative_keywords") or []
    neg_asins = [
        str(a).strip() for a in (settings.get("negative_asins") or []) if str(a).strip()
    ]
    neg_level = str(settings.get("negative_level") or "campaign").lower()

    # 开始日期：亚马逊模板常见写法为 YYYYMMDD 数值
    # 支持表单传入（YYYY-MM-DD / YYYYMMDD），否则默认当天
    start_date_raw = str(settings.get("start_date") or "").strip()
    if start_date_raw:
        start_date = int(start_date_raw.replace("-", "").replace("/", ""))
    else:
        try:
            start_date = int(today_str)
        except ValueError:
            start_date = today_str

    for item in products:
        parent_sku = item["parent_sku"]
        prod_name = item.get("product_name", "商品") or "商品"
        budget = _to_float(
            item.get(
                "assigned_budget",
                item.get("custom_budget", item.get("suggested_budget", 100.0)),
            ),
            100.0,
        )
        children = item.get("children", [])
        if not children:
            continue

        campaign_id = parent_sku
        campaign_name = (
            f"{campaign_name_custom}_{parent_sku}"
            if campaign_name_custom
            else f"{parent_sku}_{prod_name}_自动_{today_str}"
        )
        ad_group_id = f"{parent_sku}_ag"
        ad_group_name = (
            f"{ad_group_name_custom}_{parent_sku}"
            if ad_group_name_custom
            else campaign_name
        )

        # 1. Campaign
        rows.append(
            {
                "产品": "Sponsored Products",
                "实体层级": "Campaign",
                "操作": "Create",
                "广告活动编号": campaign_id,
                "广告组合编号": portfolio_id,
                "广告活动名称": campaign_name,
                "开始日期": start_date,
                "投放类型": TARGETING_TYPE_AUTO,
                "状态": "enabled",
                "每日预算": budget,
                "竞价方案": bidding_strategy,
            }
        )

        # 2. 否定投放（活动级默认；negative_level=ad_group 时挂到广告组）
        #    活动级否定行必须留空「广告组编号」，否则会被判为非法实体层级组合。
        entity_neg_kw = (
            "Negative Keyword" if neg_level == "ad_group" else "Campaign Negative Keyword"
        )
        entity_neg_pt = (
            "Negative Product Targeting"
            if neg_level == "ad_group"
            else "Campaign Negative Product Targeting"
        )

        for kw in neg_keywords:
            if isinstance(kw, dict):
                kw_text = str(kw.get("text") or "").strip()
                kw_match = str(kw.get("match") or "negativeExact").strip()
            else:
                kw_text, kw_match = str(kw).strip(), "negativeExact"
            if not kw_text:
                continue
            neg_kw_row = {
                "产品": "Sponsored Products",
                "实体层级": entity_neg_kw,
                "操作": "Create",
                "广告活动编号": campaign_id,
                "状态": "enabled",
                "关键词文本": kw_text,
                "匹配类型": kw_match,
            }
            if neg_level == "ad_group":
                neg_kw_row["广告组编号"] = ad_group_id
            rows.append(neg_kw_row)

        for asin in neg_asins:
            asin_clean = asin.upper().replace('"', "").strip()
            if not asin_clean:
                continue
            neg_pt_row = {
                "产品": "Sponsored Products",
                "实体层级": entity_neg_pt,
                "操作": "Create",
                "广告活动编号": campaign_id,
                "状态": "enabled",
                TARGETING_EXPR_COLUMN: f'asin="{asin_clean}"',
            }
            if neg_level == "ad_group":
                neg_pt_row["广告组编号"] = ad_group_id
            rows.append(neg_pt_row)

        # 3 & 4. Bidding Adjustment - 搜索顶部 / 商品页面
        #    百分比为 0 等于「不做加成」，是一行空操作，只会平白增加被亚马逊
        #    校验的行数；因此 0 直接跳过，需要加成时填非 0 即可。
        if include_placement:
            for placement, pct in (
                (PLACEMENT_TOP, top_adj),
                (PLACEMENT_REST_OF_SEARCH, rest_adj),
                (PLACEMENT_PRODUCT_PAGE, prod_adj),
            ):
                if pct <= 0:
                    continue
                rows.append(
                    {
                        "产品": "Sponsored Products",
                        "实体层级": "Bidding Adjustment",
                        "操作": "Create",
                        "广告活动编号": campaign_id,
                        "竞价方案": bidding_strategy,
                        "广告位": placement,
                        "百分比": pct,
                    }
                )

        # 4. Ad Group
        rows.append(
            {
                "产品": "Sponsored Products",
                "实体层级": "Ad Group",
                "操作": "Create",
                "广告活动编号": campaign_id,
                "广告组编号": ad_group_id,
                "广告组名称": ad_group_name,
                "状态": "enabled",
                "广告组默认竞价": default_bid,
            }
        )

        # 5. Product Ad - 挂载该父体下所有子体
        for child in children:
            rows.append(
                {
                    "产品": "Sponsored Products",
                    "实体层级": "Product Ad",
                    "操作": "Create",
                    "广告活动编号": campaign_id,
                    "广告组编号": ad_group_id,
                    "SKU": child.get("msku", ""),
                    "ASIN": child.get("asin", ""),
                    "状态": "enabled",
                }
            )

        # 6. Product Targeting - 4 组自动投放（可选）
        #    ⚠️ 三个关键点（缺一即被整体拒绝）：
        #    1) 实体层级必须是 ``Product Targeting``（不是 ``Targeting``）；
        #    2) 表达式必须写在官方列 ``拓展商品投放编号`` 里，取值是亚马逊
        #       受控裸值 close-match/loose-match/substitutes/complements；
        #    3) ``关键词文本`` 与 ``匹配类型`` 必须留空（这是投放组，不是关键词）。
        if include_targeting:
            preds = [p for p in AUTO_TARGETING if (not target_types or p in target_types)]
            for pred in preds:
                rows.append(
                    {
                        "产品": "Sponsored Products",
                        "实体层级": "Product Targeting",
                        "操作": "Create",
                        "广告活动编号": campaign_id,
                        "广告组编号": ad_group_id,
                        "状态": "enabled",
                        "竞价": targeting_bids[pred],
                        TARGETING_EXPR_COLUMN: pred,
                    }
                )

    output_df = pd.DataFrame(rows)
    for col in BULK_COLUMNS:
        if col not in output_df.columns:
            output_df[col] = None
    output_df = output_df[BULK_COLUMNS]

    _validate_output(output_df)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        output_df.to_excel(writer, sheet_name=SHEET_NAME, index=False)
    buffer.seek(0)
    return buffer


def _validate_output(df: "pd.DataFrame") -> None:
    """上传前自检：把亚马逊会拒绝的字段值挡在本地。

    覆盖四类高频上传失败：
    1. 表头与官方模板不一致（列名漂移，例如把 ``拓展商品投放编号`` 写成
       ``商品投放表达式``）——v9 事故的直接原因；
    2. 实体层级不在 ``VALID_ENTITIES`` 内——v8 事故的直接原因；
    3. 自动投放表达式缺失、写错或写成了 ``targeting_expression = "..."``；
    4. 广告位 / 投放类型 / 百分比用了非受控值。
    """
    problems: List[str] = []

    # 1) 表头必须完整覆盖官方 31 列，且不得混入旧表头
    missing_cols = [c for c in OFFICIAL_COLUMNS if c not in df.columns]
    if missing_cols:
        problems.append(f"缺少官方模板列：{missing_cols}")
    legacy_cols = [c for c in ("商品投放表达式", "商品投放编号", "关键词") if c in df.columns]
    if legacy_cols:
        problems.append(f"存在已废弃的旧表头（应删除）：{legacy_cols}")

    if "实体层级" not in df.columns:
        problems.append("缺少「实体层级」列")
        raise ValueError(
            "生成的 Bulk 表格未通过亚马逊规范自检：\n  - " + "\n  - ".join(problems)
        )

    # 2) 实体层级合法
    entities = set(df["实体层级"].dropna().unique())
    bad_entities = sorted(entities - VALID_ENTITIES)
    if bad_entities:
        problems.append(f"非法实体层级：{bad_entities}")

    # 3) Product Targeting 行必须携带受控表达式
    if TARGETING_EXPR_COLUMN in df.columns:
        pt_mask = df["实体层级"] == "Product Targeting"
        expr = df.loc[pt_mask, TARGETING_EXPR_COLUMN]
        blank_rows = int(expr.isna().sum() + (expr.astype(str).str.strip() == "").sum())
        if blank_rows:
            problems.append(
                f"{blank_rows} 行 Product Targeting 的「{TARGETING_EXPR_COLUMN}」为空"
                "（亚马逊会报「以下列缺少值」）"
            )
        bad_expr = sorted(
            {str(v) for v in expr.dropna() if str(v).strip() and str(v) not in AUTO_TARGETING}
        )
        if bad_expr:
            problems.append(
                f"Product Targeting 的「{TARGETING_EXPR_COLUMN}」必须是裸受控值 "
                f"{AUTO_TARGETING}，发现：{bad_expr}"
            )
        # 投放行不得携带关键词字段
        if "关键词文本" in df.columns:
            kw_leak = int(
                df.loc[pt_mask, "关键词文本"].notna().sum()
                + df.loc[pt_mask, "匹配类型"].notna().sum()
            )
            if kw_leak:
                problems.append("Product Targeting 行的「关键词文本」「匹配类型」必须留空")

    # 3.1) 否定关键词：文本非空 + 匹配类型受控（无 broad）
    neg_kw_mask = df["实体层级"].isin(NEG_KEYWORD_ENTITIES)
    if neg_kw_mask.any():
        kw_texts = df.loc[neg_kw_mask, "关键词文本"]
        blank_kw = int(kw_texts.isna().sum() + (kw_texts.astype(str).str.strip() == "").sum())
        if blank_kw:
            problems.append(f"{blank_kw} 行否定关键词的「关键词文本」为空")
        bad_neg_match = sorted(
            {
                str(v)
                for v in df.loc[neg_kw_mask, "匹配类型"].dropna()
                if str(v).strip() and str(v) not in NEG_MATCH_TYPES
            }
        )
        if bad_neg_match:
            problems.append(
                f"否定关键词的「匹配类型」必须是 {sorted(NEG_MATCH_TYPES)}，发现：{bad_neg_match}"
            )

    # 3.2) 否定商品投放：表达式必须是 asin="B0..."，且不得携带关键词列
    neg_pt_mask = df["实体层级"].isin(NEG_PT_ENTITIES)
    if neg_pt_mask.any():
        exprs = df.loc[neg_pt_mask, TARGETING_EXPR_COLUMN]
        bad_neg_expr = sorted(
            {
                str(v)
                for v in exprs.dropna()
                if str(v).strip() and not str(v).strip().lower().startswith("asin=")
            }
        )
        blank_expr = int(exprs.isna().sum() + (exprs.astype(str).str.strip() == "").sum())
        if blank_expr or bad_neg_expr:
            problems.append(
                f"否定商品投放的「{TARGETING_EXPR_COLUMN}」必须是非空 asin=\"B0...\" 表达式，"
                f"空行 {blank_expr}，非法 {bad_neg_expr[:3]}"
            )
        leak = int(df.loc[neg_pt_mask, "关键词文本"].notna().sum())
        if leak:
            problems.append("否定商品投放行不得填写「关键词文本」")

    # 4) 广告位 / 投放类型 / 百分比受控
    allowed_placements = {PLACEMENT_TOP, PLACEMENT_PRODUCT_PAGE, PLACEMENT_REST_OF_SEARCH}
    if "广告位" in df.columns:
        placements = df.loc[df["实体层级"] == "Bidding Adjustment", "广告位"].dropna()
        bad_placements = sorted({str(v) for v in placements if str(v) not in allowed_placements})
        if bad_placements:
            problems.append(f"非法广告位：{bad_placements}（应为 {sorted(allowed_placements)}）")

    if "百分比" in df.columns:
        pcts = df.loc[df["实体层级"] == "Bidding Adjustment", "百分比"].dropna()
        bad_pcts = sorted(
            {
                v
                for v in pcts
                if not (PERCENTAGE_MIN <= _to_float(v, -1) <= PERCENTAGE_MAX)
            }
        )
        if bad_pcts:
            problems.append(
                f"非法百分比：{bad_pcts}（应为 {PERCENTAGE_MIN}-{PERCENTAGE_MAX} 的数字）"
            )

    if "投放类型" in df.columns:
        ttypes = df.loc[df["实体层级"] == "Campaign", "投放类型"].dropna()
        bad_tt = sorted(
            {
                str(v)
                for v in ttypes
                if str(v) not in {TARGETING_TYPE_AUTO, TARGETING_TYPE_MANUAL}
            }
        )
        if bad_tt:
            problems.append(
                f"非法投放类型：{bad_tt}（应为 {TARGETING_TYPE_AUTO} 或 {TARGETING_TYPE_MANUAL}）"
            )

    if problems:
        raise ValueError("生成的 Bulk 表格未通过亚马逊规范自检：\n  - " + "\n  - ".join(problems))
