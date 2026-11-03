# -*- coding: utf-8 -*-
"""广告创建的商品数据源: **赛狐在线接口** productRightSpPageListSearch

对齐赛狐原页面: 广告创建页的商品列表就是走这个接口拉的
(POST multiple/product/productRightSpPageListSearch, 分页, rows 为父体行,
 childList 为子体)。本模块把接口行转成与 ``db_source.build_parsed`` 同构的
结构, 让 ``budget_engine.build_shop_payload`` 与前端零改动复用。

接口行字段（实测）:
  rows[]: {id, shopId, sku, asin, onlineStatus, title, imgUrl, domain, childList[]}
  childList[]: {asin, sku, price, standardPriceCurrency, batchCreateParentAsin, ...}
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend import database as db
from backend.ad_creator.parser import clean_parent_sku, clean_product_name
from backend.ad_creator.suggest_bid import shop_name_of
from backend.ad_creator.translator import match_first_nouns_batch
from backend.sellfox_client import SellfoxClient

LIST_API = "product/productRightSpPageListSearch"
PAGE_SIZE = 100          # 抓包页面用 20; 接口实际接受更大值, 减少翻页次数
MAX_PAGES = 60           # 保护: 1727 个品 / 100 ≈ 18 页


def fetch_rows(cookies: Dict[str, str], shop_id: str,
               search_value: str = "") -> List[Dict[str, Any]]:
    """拉取某店铺商品（父体行, 含 childList）；search_value 非空时按 ASIN 搜索"""
    client = SellfoxClient(cookies)
    out: List[Dict[str, Any]] = []
    page = 1
    while page <= MAX_PAGES:
        payload = {
            "pageNo": page, "pageSize": PAGE_SIZE, "shopId": str(shop_id),
            "searchField": "asin", "searchValue": str(search_value or "").strip(),
            "searchType": "exact" if search_value else "exact",
            "onlineStatus": "", "orderType": "desc", "type": "sp",
        }
        data = client.post(LIST_API, payload).get("data") or {}
        rows = data.get("rows") or []
        out.extend(rows)
        if not rows or page >= int(data.get("totalPage") or 1):
            break
        page += 1
    return out


def search_by_asins(cookies: Dict[str, str], shop_id: str,
                    keywords: List[str]) -> List[Dict[str, Any]]:
    """按 ASIN（可多个）在线搜索，返回去重后的父体行"""
    out: Dict[str, Dict[str, Any]] = {}
    for kw in keywords:
        kw = str(kw).strip()
        if not kw:
            continue
        try:
            for row in fetch_rows(cookies, shop_id, search_value=kw):
                pa = str(row.get("asin") or "").strip()
                if pa:
                    out[pa] = row
        except Exception:       # noqa: BLE001 - 单个关键词失败不影响其他
            continue
    return list(out.values())


def simplify_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """接口行 → 前端可渲染的精简结构"""
    items: List[Dict[str, Any]] = []
    for row in rows:
        pa = str(row.get("asin") or "").strip()
        if not pa:
            continue
        children = []
        for ch in row.get("childList") or []:
            ca = str(ch.get("asin") or "").strip()
            if not ca:
                continue
            children.append({
                "asin": ca,
                "msku": str(ch.get("sku") or "").strip() or ca,
                "price": _price(ch.get("price")),
            })
        items.append({
            "parent_asin": pa,
            "sku": str(row.get("sku") or "").strip(),
            "title": str(row.get("title") or "").strip(),
            "img": str(row.get("imgUrl") or "").strip(),
            "online_status": str(row.get("onlineStatus") or "").strip(),
            "children": children,
        })
    return items


def _price(raw: Any) -> float:
    try:
        f = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0
    return f if f > 0 else 0.0


def build_parsed(cookies: Dict[str, str], shop_ids: List[str],
                 mark: str = "") -> Dict[str, Any]:
    """拉取赛狐商品 → {shops: {店铺: {groups, all_prices}}, ...}（与 db_source 同构）"""
    want = [str(s).strip() for s in (shop_ids or []) if str(s).strip()]

    # 打标过滤（父 ASIN 维度, 打标数据在本地库）
    allowed: Optional[set] = None
    mark = str(mark or "").strip().lower()
    if mark in ("pass", "fail"):
        with db.get_conn() as conn:
            allowed = {r["parent_asin"] for r in conn.execute(
                "SELECT parent_asin FROM product_marks WHERE mark=?", (mark,))}
        if not allowed:
            return {"shops": {}, "source": "sellfox", "mark": mark, "parent_count": 0}

    raw_shops: Dict[str, Dict[str, Any]] = {}

    def group_of(shop: str, key: str) -> Dict[str, Any]:
        b = raw_shops.setdefault(shop, {"groups": {}, "all_prices": []})
        g = b["groups"].setdefault(key, {
            "parent_sku": "", "raw_name": "", "image_url": "",
            "children": [], "prices": [], "parent_asins": [],
        })
        return g

    for sid in want:
        shop = shop_name_of(sid)
        for row in fetch_rows(cookies, sid):
            pa = str(row.get("asin") or "").strip()          # 父 ASIN
            if not pa or (allowed is not None and pa not in allowed):
                continue
            g = group_of(shop, pa.lower())
            if not g["parent_sku"]:
                g["parent_sku"] = clean_parent_sku(str(row.get("sku") or "").strip()) or pa
            title = str(row.get("title") or "").strip()
            if title and (not g["raw_name"] or len(title) < len(g["raw_name"])):
                g["raw_name"] = title
            if not g["image_url"] and row.get("imgUrl"):
                g["image_url"] = str(row["imgUrl"])
            if pa not in g["parent_asins"]:
                g["parent_asins"].append(pa)
            for ch in row.get("childList") or []:
                ca = str(ch.get("asin") or "").strip()
                if not ca:
                    continue
                price = _price(ch.get("price"))
                if price > 0:
                    g["prices"].append(price)
                    raw_shops[shop]["all_prices"].append(price)
                g["children"].append({
                    "msku": str(ch.get("sku") or "").strip() or ca,
                    "asin": ca,
                    "price": round(price, 2),
                })
            if not g["children"]:                            # 单品 Listing 自己当子体
                g["children"].append({
                    "msku": str(row.get("sku") or "").strip() or pa,
                    "asin": pa,
                    "price": 0.0,
                })

    # 离线名词词库 → 中文品名（与 db_source 同一套匹配规则）
    targets: List[str] = []
    for b in raw_shops.values():
        for g in b["groups"].values():
            t = g["raw_name"] or g["parent_sku"]
            if t:
                targets.append(t)
    name_map = match_first_nouns_batch(targets)

    shops_out: Dict[str, Dict[str, Any]] = {}
    parents = 0
    children = 0
    for shop, b in raw_shops.items():
        groups_out: Dict[str, Any] = {}
        for key, g in b["groups"].items():
            if not g["children"]:
                continue
            sku = g["parent_sku"] or key
            if sku in groups_out:
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
            parents += 1
            children += len(g["children"])
        if groups_out:
            shops_out[shop] = {"groups": groups_out, "all_prices": b["all_prices"]}

    marks = {}
    try:
        from backend import database as dbm
        marks = dbm.get_product_mark_counts()
    except Exception:      # noqa: BLE001
        marks = {}

    return {
        "shops": shops_out,
        "columns": [],
        "header_row": 0,
        "resolved_columns": {"shop": "店铺", "sku": "MSKU", "price": "售价(总价)",
                             "asin": "ASIN", "parent_asin": "父ASIN",
                             "title": "标题", "image": "图片"},
        "source": "sellfox",
        "mark": mark,
        "parent_count": parents,
        "summary": {"parents": parents, "children": children,
                    "shops": len(shops_out), "marks": marks},
    }
