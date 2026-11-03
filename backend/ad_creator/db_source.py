# -*- coding: utf-8 -*-
"""广告创建的数据源: **本项目在线产品库** → 与 Excel 解析同构的结构

autoAds 原来是「上传赛狐导出的 Excel → parser 解析 → 按店铺聚合」。
平移到本项目后，商品数据直接取库里已采集的在线产品（销售 > 在线产品）:

  · 父体 = is_variation='1' 的行（父 ASIN / 父 SKU / 标题 / 主图）
  · 子体 = is_variation='2' 的行（子 ASIN / MSKU / 价格）
  · 价格 = standardPrice，缺失回退 landedPrice（与「价格中位数」列同口径）
  · 店铺 = raw_json.shopName（缺失退 shopId）

产出结构与 ``parser.parse_sellfox_excel_multi_shop`` 完全一致，
因此 ``budget_engine.build_shop_payload`` 与前端都可以零改动复用。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend import database as db
from backend.ad_creator.parser import clean_parent_sku, clean_product_name
from backend.ad_creator.translator import match_first_nouns_batch

DEFAULT_SHOP = "默认店铺"

# 子体/父体都要的几个字段（都从 raw_json 取，避免依赖列是否回填）
_ROW_SQL = """
SELECT json_extract(raw_json,'$.asin')         AS asin,
       json_extract(raw_json,'$.sku')          AS sku,
       json_extract(raw_json,'$.parentAsin')   AS parent_asin,
       json_extract(raw_json,'$.parentSku')    AS parent_sku,
       json_extract(raw_json,'$.title')        AS title,
       json_extract(raw_json,'$.mainBigImage') AS img,
       json_extract(raw_json,'$.standardPrice') AS std_price,
       json_extract(raw_json,'$.landedPrice')   AS landed_price,
       json_extract(raw_json,'$.shopName')      AS shop_name,
       json_extract(raw_json,'$.shopId')        AS shop_id
FROM online_products
WHERE is_variation = ?
"""


def _price(std_price: Any, landed_price: Any) -> float:
    for v in (std_price, landed_price):
        try:
            f = float(str(v).replace(",", "")) if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            f = 0.0
        if f > 0:
            return f
    return 0.0


def _shop_of(row: Dict[str, Any]) -> str:
    name = str(row.get("shop_name") or "").strip()
    if name:
        return name
    sid = row.get("shop_id")
    return f"店铺{sid}" if sid not in (None, "") else DEFAULT_SHOP


def _parent_key(row: Dict[str, Any]) -> str:
    """父体聚合键 = **父 ASIN**（父子行都有，最可靠）

    历史上按父 SKU 聚合会出问题：父体行的 parentSku 大多为空、子体行的 parentSku 有值，
    两边键不同 → 同一个父体会被拆成「子体组 + 父体自己单独一组」，条数翻倍。
    """
    pa = str(row.get("parent_asin") or "").strip()
    if pa:
        return pa.lower()
    pa = str(row.get("asin") or "").strip()
    return pa.lower()


def _display_sku(row: Dict[str, Any]) -> str:
    """展示用的父 SKU：父 SKU 归一化（abc001-A-40cm → abc001），没有就空"""
    sku = str(row.get("parent_sku") or row.get("sku") or "").strip()
    return clean_parent_sku(sku) if sku else ""


def build_parsed(mark: str = "", shops: Optional[List[str]] = None) -> Dict[str, Any]:
    """从在线产品库聚合 → {shops: {店铺: {groups, all_prices}}, ...}

    Args:
        mark: 只取打过某个标记的父体（``pass`` / ``fail``），空 = 全部。
              打标来自「在线产品」页操作列的通过 / 不通过（按父 ASIN 记）。
        shops: 只要这些店铺（空 = 全部）。
    """
    want_shops = {s for s in (shops or []) if s}
    with db.get_conn() as conn:
        parents = [dict(r) for r in conn.execute(_ROW_SQL, ("1",))]
        children = [dict(r) for r in conn.execute(_ROW_SQL, ("2",))]

    # 打标过滤（父 ASIN 维度）
    mark = str(mark or "").strip().lower()
    allowed: Optional[set] = None
    if mark in ("pass", "fail"):
        with db.get_conn() as conn:
            allowed = {r["parent_asin"] for r in conn.execute(
                "SELECT parent_asin FROM product_marks WHERE mark=?", (mark,))}
        if not allowed:
            return {"shops": {}, "source": "db", "mark": mark, "parent_count": 0}

    # 父体行索引: 父 ASIN -> 行
    parent_rows: Dict[str, Dict[str, Any]] = {}
    for r in parents:
        pa = str(r.get("asin") or "").strip()
        if pa:
            parent_rows[pa] = r

    raw_shops: Dict[str, Dict[str, Any]] = {}

    def bucket(shop: str) -> Dict[str, Any]:
        return raw_shops.setdefault(shop, {"groups": {}, "all_prices": []})

    def group_of(shop: str, key: str) -> Dict[str, Any]:
        g = bucket(shop)["groups"]
        return g.setdefault(key, {
            "parent_sku": "", "raw_name": "", "image_url": "",
            "children": [], "prices": [], "parent_asins": [],
        })

    # 1) 子体 → 归到 (店铺, 父 SKU)
    for r in children:
        asin = str(r.get("asin") or "").strip()
        msku = str(r.get("sku") or "").strip()
        if not asin and not msku:
            continue
        pa = str(r.get("parent_asin") or "").strip()
        if allowed is not None and pa not in allowed:
            continue
        shop = _shop_of(r)
        if want_shops and shop not in want_shops:
            continue
        key = _parent_key(r) or (pa.lower() if pa else "")
        if not key:
            continue
        g = group_of(shop, key)
        if not g["parent_sku"]:
            g["parent_sku"] = _display_sku(r)          # 父 SKU 只在展示用
        price = _price(r.get("std_price"), r.get("landed_price"))
        if price > 0:
            bucket(shop)["all_prices"].append(price)
            g["prices"].append(price)
        title = str(r.get("title") or "").strip()
        if title and (not g["raw_name"] or len(title) < len(g["raw_name"])):
            g["raw_name"] = title
        if not g["image_url"] and r.get("img"):
            g["image_url"] = str(r["img"])
        if pa and pa not in g["parent_asins"]:
            g["parent_asins"].append(pa)
        g["children"].append({
            "msku": msku or asin,
            "asin": asin or msku,
            "price": round(price, 2),
        })

    # 2) 父体行: 补标题/主图；没有子体的单品 Listing 自己当子体
    for pa, pr in parent_rows.items():
        if allowed is not None and pa not in allowed:
            continue
        shop = _shop_of(pr)
        if want_shops and shop not in want_shops:
            continue
        key = _parent_key(pr) or pa.lower()
        g = group_of(shop, key)
        if not g["parent_sku"]:
            g["parent_sku"] = _display_sku(pr)
        title = str(pr.get("title") or "").strip()
        if title and (not g["raw_name"] or len(title) < len(g["raw_name"])):
            g["raw_name"] = title
        if not g["image_url"] and pr.get("img"):
            g["image_url"] = str(pr["img"])
        if pa not in g["parent_asins"]:
            g["parent_asins"].append(pa)
        if not g["children"]:
            price = _price(pr.get("std_price"), pr.get("landed_price"))
            if price > 0:
                bucket(shop)["all_prices"].append(price)
                g["prices"].append(price)
            g["children"].append({
                "msku": str(pr.get("sku") or "").strip() or pa,
                "asin": pa,
                "price": round(price, 2),
            })

    if not raw_shops:
        return {"shops": {}, "source": "db", "mark": mark, "parent_count": 0}

    # 3) 离线名词词库 → 中文品名（与 autoAds 同一套匹配规则）
    targets: List[str] = []
    for b in raw_shops.values():
        for g in b["groups"].values():
            t = g["raw_name"] or g["parent_sku"]
            if t:
                targets.append(t)
    name_map = match_first_nouns_batch(targets)

    shops_out: Dict[str, Dict[str, Any]] = {}
    for shop, b in raw_shops.items():
        groups_out: Dict[str, Any] = {}
        for key, g in b["groups"].items():
            if not g["children"]:
                continue
            sku = g["parent_sku"] or key
            if sku in groups_out:      # 同店同 SKU 撞车 → 补父 ASIN 尾号, 避免前端 key 重复
                sku = f"{sku}-{key[-4:]}"
            target = g["raw_name"] or sku
            groups_out[sku] = {
                "parent_sku": sku,
                "clean_name": name_map.get(target) or clean_product_name(target),
                "raw_name": g["raw_name"],
                "image_url": g.get("image_url", ""),
                "children": g["children"],
                "prices": g["prices"],
                "parent_asins": g.get("parent_asins", []),
            }
        if groups_out:
            shops_out[shop] = {"groups": groups_out, "all_prices": b["all_prices"]}

    return {
        "shops": shops_out,
        "columns": [],
        "header_row": 0,
        "resolved_columns": {"shop": "店铺", "sku": "MSKU", "price": "售价(总价)",
                             "asin": "ASIN", "parent_asin": "父ASIN",
                             "title": "标题", "image": "图片"},
        "source": "db",
        "mark": mark,
        "parent_count": sum(len(v["groups"]) for v in shops_out.values()),
    }


def source_summary() -> Dict[str, Any]:
    """数据源概况（页面上显示「数据来自本地库 · x 个店铺 / y 个父体」）"""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM online_products WHERE is_variation='1'").fetchone()
        parents = int(row["c"] or 0) if row else 0
        row = conn.execute(
            "SELECT COUNT(*) c FROM online_products WHERE is_variation='2'").fetchone()
        children = int(row["c"] or 0) if row else 0
        shops = conn.execute(
            "SELECT COUNT(DISTINCT json_extract(raw_json,'$.shopName')) c "
            "FROM online_products").fetchone()
    marks = db.get_product_mark_counts()
    return {"parents": parents, "children": children,
            "shops": int(shops["c"] or 0) if shops else 0,
            "marks": marks}
