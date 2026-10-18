# -*- coding: utf-8 -*-
"""批量建广告 · 数据对接层。

把库里的三份数据拼成「一个父体 = 一个自动投放活动」的入参：

    在线产品 online_products   父/子 ASIN + 卖家 SKU + 变体关系
    在线产品 raw_json          子体 standardPrice / landedPrice（算售价中位数）
    广告产品 ad_records(product) 该父体下已有活动（判断是否已建过自动活动）
    ol_ai_labels               AI 品名（活动名里的中文品名，缺失时用标题兜底）

对外只有两个函数：``list_candidates()`` 与 ``build_products()``。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend import database as db

MIN_BUDGET = 100.0
DEFAULT_BASE_BUDGET = 100.0
MAX_CANDIDATES = 5000

_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
_ILLEGAL_NAME_CHARS = re.compile(r"[+\-*/\\^&%#@!~`=()\[\]{}|:;\"'<>,.?！？，。、\s\t\n\r]+")


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _median(values: List[float]) -> float:
    vals = sorted(v for v in values if v is not None and v > 0)
    if not vals:
        return 0.0
    mid = len(vals) // 2
    return float(vals[mid]) if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def natural_sort_key(text: Any) -> List[Any]:
    """自然排序：hlx002 排在 hlx10 之前。"""
    return [int(chunk) if chunk.isdigit() else chunk.lower()
            for chunk in re.split(r"(\d+)", str(text or ""))]


def clean_product_name(text: Any, max_len: int = 35) -> str:
    """清洗成可安全用于广告活动名的片段（去掉符号、控制长度）。"""
    s = str(text or "").strip()
    if not s:
        return ""
    s = _ILLEGAL_NAME_CHARS.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len]


def _key(shop_id: Any, parent_asin: str) -> str:
    return f"{int(shop_id) if str(shop_id).isdigit() else shop_id}:{parent_asin}"


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------
def _load_shops() -> Dict[int, Dict[str, Any]]:
    out: Dict[int, Dict[str, Any]] = {}
    for r in db.get_shops():
        try:
            out[int(r.get("shop_id"))] = r
        except (TypeError, ValueError):
            continue
    return out


def _load_auto_campaigns(conn) -> Dict[str, Dict[str, Any]]:
    """子 ASIN -> {all: {cid: name}, auto: {cid: name}}

    只看广告产品页签的**最新快照区间**，避免历史区间重复计入。
    """
    out: Dict[str, Dict[str, Any]] = {}
    try:
        rows = conn.execute(
            """SELECT json_extract(raw_json,'$.asin') AS a,
                      json_extract(raw_json,'$.campaignId') AS cid,
                      json_extract(raw_json,'$.campaignName') AS nm,
                      json_extract(raw_json,'$.campaignTargetingType') AS tt
                 FROM ad_records
                WHERE tab='product'
                  AND COALESCE(range_start,'')||'~'||COALESCE(range_end,'')
                      = (SELECT COALESCE(range_start,'')||'~'||COALESCE(range_end,'')
                           FROM ad_records WHERE tab='product'
                          ORDER BY updated_at DESC LIMIT 1)""").fetchall()
    except Exception:           # noqa: BLE001  未采集广告产品时不影响建活动
        return out

    for r in rows:
        asin = str(r["a"] or "").strip()
        cid = str(r["cid"] or "").strip()
        if not asin or not cid:
            continue
        bucket = out.setdefault(asin, {"all": {}, "auto": {}})
        name = str(r["nm"] or "")
        bucket["all"].setdefault(cid, name)
        if str(r["tt"] or "").strip().lower() == "auto":
            bucket["auto"].setdefault(cid, name)
    return out


def _load_parents(conn) -> Dict[tuple, Dict[str, Any]]:
    """(shop_id, 父ASIN) -> 父体自身信息（SKU / 标题 / 主图）"""
    out: Dict[tuple, Dict[str, Any]] = {}
    for r in conn.execute(
            "SELECT asin, sku, shop_id, marketplace_id, title, main_image, parent_asin "
            "FROM online_products WHERE is_variation='1'"):
        asin = str(r["asin"] or "").strip()
        if not asin:
            continue
        out[(r["shop_id"], asin)] = {
            "sku": str(r["sku"] or "").strip(),
            "title": str(r["title"] or "").strip(),
            "image_url": str(r["main_image"] or "").strip(),
            "marketplace_id": str(r["marketplace_id"] or "").strip(),
        }
    return out


def _load_children(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """SELECT asin, sku, shop_id, title, main_image, parent_asin,
                  json_extract(raw_json,'$.parentAsin') AS pa,
                  json_extract(raw_json,'$.standardPrice') AS sp,
                  json_extract(raw_json,'$.landedPrice') AS lp
             FROM online_products WHERE is_variation='2'""").fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        asin = str(r["asin"] or "").strip()
        sku = str(r["sku"] or "").strip()
        if not asin or not sku:
            continue          # 没有卖家 SKU 的商品无法挂到 Product Ad 行
        price = _to_float(r["sp"])
        if price <= 0:
            price = _to_float(r["lp"])
        out.append({
            "asin": asin,
            "sku": sku,
            "shop_id": r["shop_id"],
            "price": price,
            "parent_asin": str(r["pa"] or r["parent_asin"] or "").strip(),
            "title": str(r["title"] or "").strip(),
            "image_url": str(r["main_image"] or "").strip(),
        })
    return out


def _load_labels(conn) -> Dict[str, str]:
    try:
        return {str(r["asin"]): str(r["label"] or "").strip()
                for r in conn.execute("SELECT asin, label FROM ol_ai_labels")
                if r["asin"] and str(r["label"] or "").strip()}
    except Exception:           # noqa: BLE001
        return {}


def _suggest_name(title: str, label: str, parent_sku: str) -> str:
    """活动名里的品名片段：AI 品名 > 标题前段 > 父 SKU。"""
    for cand in (label, title):
        n = clean_product_name(cand)
        if n:
            return n
    return clean_product_name(parent_sku) or "商品"


# ---------------------------------------------------------------------------
# 对外：候选父体清单
# ---------------------------------------------------------------------------
def list_candidates(shop_id: Optional[int] = None, keyword: str = "",
                    only_new: bool = False,
                    base_budget: float = DEFAULT_BASE_BUDGET) -> Dict[str, Any]:
    """列出可作为「一个自动投放活动」的父体。

    only_new=True 时只返回**尚无自动投放活动**的父体（避免重复建）。
    """
    base_budget = max(MIN_BUDGET, _to_float(base_budget, DEFAULT_BASE_BUDGET))
    shops = _load_shops()

    with db.get_conn() as conn:
        parents = _load_parents(conn)
        children = _load_children(conn)
        auto_map = _load_auto_campaigns(conn)
        labels = _load_labels(conn)

    # 1) 子体按 (shop_id, 父 ASIN) 分组；无变体关系的单品 Listing 以自身为父
    groups: Dict[tuple, Dict[str, Any]] = {}
    for c in children:
        parent_asin = c["parent_asin"] or c["asin"]
        gkey = (c["shop_id"], parent_asin)
        g = groups.setdefault(gkey, {
            "shop_id": c["shop_id"], "parent_asin": parent_asin,
            "children": [], "_skus": set(), "_prices": [],
            "title": "", "image_url": "",
        })
        if c["sku"] in g["_skus"]:
            continue
        g["_skus"].add(c["sku"])
        g["children"].append({"asin": c["asin"], "sku": c["sku"],
                              "price": round(c["price"], 2)})
        if c["price"] > 0:
            g["_prices"].append(c["price"])
        if not g["title"] and c["title"]:
            g["title"] = c["title"]
        if not g["image_url"] and c["image_url"]:
            g["image_url"] = c["image_url"]

    # 2) 补齐父体信息
    rows: List[Dict[str, Any]] = []
    for (sid, pa), g in groups.items():
        pinfo = parents.get((sid, pa)) or {}
        parent_sku = pinfo.get("sku") or ""
        if not parent_sku:
            # 父体行未采集时，用第一个子体 SKU 的前缀兜底
            parent_sku = clean_product_name(g["children"][0]["sku"].split("-")[0], 24)
        title = pinfo.get("title") or g["title"]
        image_url = pinfo.get("image_url") or g["image_url"]
        label = labels.get(pa) or next(
            (labels[c["asin"]] for c in g["children"] if labels.get(c["asin"])), "")
        name = _suggest_name(title, label, parent_sku)

        auto_camps: Dict[str, str] = {}
        all_camps: Dict[str, str] = {}
        for c in g["children"]:
            m = auto_map.get(c["asin"]) or {}
            all_camps.update(m.get("all") or {})
            auto_camps.update(m.get("auto") or {})

        shop = shops.get(sid) or {}
        prices = g["_prices"]
        rows.append({
            "key": _key(sid, pa),
            "shop_id": sid,
            "shop_name": shop.get("shop_name") or "",
            "marketplace_id": pinfo.get("marketplace_id", ""),
            "parent_asin": pa,
            "parent_sku": parent_sku,
            "title": title,
            "image_url": image_url,
            "label": label,
            "product_name": name,
            "child_count": len(g["children"]),
            "children": g["children"],
            "price_median": round(_median(prices), 2),
            "existing_auto": bool(auto_camps),
            "existing_auto_count": len(auto_camps),
            "existing_campaign_count": len(all_camps),
            "existing_auto_names": sorted(n for n in auto_camps.values() if n)[:3],
            "existing_campaign_names": sorted(n for n in all_camps.values() if n)[:3],
        })

    # 3) 店铺内价格中位数 → 建议预算（等权/加权的“基准建议”，前端可改）
    shop_medians: Dict[Any, float] = {}
    for sid in {r["shop_id"] for r in rows}:
        shop_medians[sid] = _median([r["price_median"] for r in rows
                                     if r["shop_id"] == sid]) or 0.0
    for r in rows:
        med = shop_medians.get(r["shop_id"]) or 0.0
        ratio = (r["price_median"] / med) if (med > 0 and r["price_median"] > 0) else 1.0
        r["shop_price_median"] = round(med, 2)
        r["suggested_budget"] = float(max(MIN_BUDGET, round(base_budget * ratio)))

    # 4) 过滤
    kw = str(keyword or "").strip().lower()

    def _hit(r: Dict[str, Any]) -> bool:
        if not kw:
            return True
        hay = [r["parent_asin"], r["parent_sku"], r["title"], r["label"],
               r["shop_name"]]
        hay += [c["asin"] for c in r["children"]] + [c["sku"] for c in r["children"]]
        return any(kw in str(x).lower() for x in hay if x)

    rows = [r for r in rows
            if (shop_id is None or r["shop_id"] == shop_id)
            and (not only_new or not r["existing_auto"])
            and _hit(r)]
    rows.sort(key=lambda x: (natural_sort_key(x["parent_sku"]), x["parent_asin"]))
    rows = rows[:MAX_CANDIDATES]

    return {
        "rows": rows,
        "total": len(rows),
        "summary": {
            "parents": len(rows),
            "children": sum(r["child_count"] for r in rows),
            "with_auto": sum(1 for r in rows if r["existing_auto"]),
            "no_price": sum(1 for r in rows if not r["price_median"]),
        },
        "shops": [{"shop_id": k, "shop_name": v.get("shop_name") or "",
                   "site_name": v.get("site_name") or ""} for k, v in shops.items()],
        "online_updated_at": db.get_online_meta("updated_at") or "",
        "defaults": {
            "base_budget": base_budget,
            "min_budget": MIN_BUDGET,
        },
    }


# ---------------------------------------------------------------------------
# 对外：选中项 → 生成器入参
# ---------------------------------------------------------------------------
def build_products(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """items: [{key, budget?, product_name?, parent_sku?}] → generate_bulk_excel 的 products。

    子体 SKU/ASIN 一律以库里为准（不信任前端传的明细），前端只能改预算与品名。
    """
    wanted: Dict[str, Dict[str, Any]] = {}
    for it in items or []:
        k = str(it.get("key") or "").strip()
        if k:
            wanted[k] = it
    if not wanted:
        raise ValueError("请至少勾选一个父体")

    all_rows = list_candidates()["rows"]
    by_key = {r["key"]: r for r in all_rows}

    picked: List[Dict[str, Any]] = []
    missing: List[str] = []
    for k, it in wanted.items():
        r = by_key.get(k)
        if not r:
            missing.append(k)
            continue
        name = clean_product_name(it.get("product_name")) or r["product_name"]
        budget = it.get("budget")
        budget = float(_to_float(budget, r["suggested_budget"]) or r["suggested_budget"])
        picked.append({
            "parent_sku": str(it.get("parent_sku") or r["parent_sku"]).strip(),
            "product_name": name,
            "assigned_budget": max(MIN_BUDGET, round(budget, 2)),
            "shop_id": r["shop_id"],
            "parent_asin": r["parent_asin"],
            "children": [{"msku": c["sku"], "asin": c["asin"]} for c in r["children"]],
        })

    if not picked:
        raise ValueError("勾选的父体在库里已不存在（可能刚同步过数据），请刷新后重试")

    # 同名父 SKU 出现在多个店铺时，活动名/编号会撞车 → 追加店铺 ID 区分
    seen: Dict[str, int] = {}
    for p in picked:
        seen[p["parent_sku"]] = seen.get(p["parent_sku"], 0) + 1
    for p in picked:
        if seen[p["parent_sku"]] > 1 and p.get("shop_id") is not None:
            p["parent_sku"] = f"{p['parent_sku']}_{p['shop_id']}"
            p["product_name"] = p["product_name"] or "商品"

    for p in picked:
        p.pop("shop_id", None)
        p.pop("parent_asin", None)
    return picked
