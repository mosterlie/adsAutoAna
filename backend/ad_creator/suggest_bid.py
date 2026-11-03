# -*- coding: utf-8 -*-
"""建议竞价（suggestBid）—— 对齐赛狐原页面逻辑

赛狐前端（assets/use-ad-create-steps-anchor-CwQd5jW2.js 反编译）的实际做法:

  · 触发: watch [已选商品 ASIN 列表, 3 个广告位百分比, 竞价策略] 任一变化即重发
  · 入参: asins = **当前广告组里全部商品 ASIN 合并**，一次请求，不按品拆分
  · 策略: 页面 strategy 经映射后传大写
          (autoForSales→AUTO_FOR_SALES / legacyForSales→LEGACY_FOR_SALES / manual→MANUAL)
  · 响应: data.bidInfo[] = [{type, suggestedBids:[低,中,高]}]
          「应用」下拉把某一档批量写入各投放组的竞价输入框

实测补充:
  · 传父 ASIN → bidInfo 空；必须传子 ASIN
  · 多品合并时返回「这批 ASIN 作为一个整体」的建议值（非某品的值）
  · 单品任意子 ASIN 结果一致
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from backend import database as db
from backend.sellfox_client import SellfoxClient

BID_API = "multiple/target/suggestBid/recommendations"
# 否定商品搜索专用接口（老网关路径, 无 /gw 前缀）——赛狐否定商品「搜索」用这个
ASIN_TARGETING_API = "/api/cpcSPManage/product/searchAsinsForTargeting.json"
DEFAULT_STRATEGY = "AUTO_FOR_SALES"
VALID_STRATEGIES = {"LEGACY_FOR_SALES", "AUTO_FOR_SALES", "MANUAL"}

# 接口 type → 前端投放组的 pred（与 ad-creator.html 的 adTargetingRows 对齐）
TYPE_TO_PRED: Dict[str, str] = {
    "CLOSE_MATCH": "close-match",
    "LOOSE_MATCH": "loose-match",
    "SUBSTITUTES": "substitutes",
    "COMPLEMENTS": "complements",
}

_SHOP_CACHE: Dict[str, str] = {}


# ---------------------------------------------------------------------------
# 店铺名 → shopId（在线产品库里每一行都带 shopId / shopName）
# ---------------------------------------------------------------------------
def resolve_shop_id(shop: str) -> str:
    shop = str(shop or "").strip()
    if not shop:
        return ""
    if shop.isdigit():
        return shop
    if shop in _SHOP_CACHE:
        return _SHOP_CACHE[shop]
    sid = ""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT json_extract(raw_json,'$.shopId') AS sid FROM online_products "
                "WHERE json_extract(raw_json,'$.shopName') = ? "
                "  AND json_extract(raw_json,'$.shopId') IS NOT NULL LIMIT 1",
                (shop,),
            ).fetchone()
        if row and row["sid"] not in (None, ""):
            sid = str(row["sid"]).strip()
    except Exception:          # noqa: BLE001 - 库缺失/字段缺失时退回下面的兜底
        sid = ""
    if not sid:                # 「店铺711811」这类兜底命名
        m = re.search(r"(\d{3,})$", shop)
        sid = m.group(1) if m else ""
    if sid:
        _SHOP_CACHE[shop] = sid
    return sid


def shop_name_of(shop_id: str) -> str:
    """shopId → 店铺名（本地库反查；查不到用「店铺{id}」）"""
    shop_id = str(shop_id or "").strip()
    if not shop_id:
        return ""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT json_extract(raw_json,'$.shopName') AS n FROM online_products "
                "WHERE CAST(json_extract(raw_json,'$.shopId') AS TEXT) = ? LIMIT 1",
                (shop_id,),
            ).fetchone()
        if row and str(row["n"] or "").strip():
            return str(row["n"]).strip()
    except Exception:          # noqa: BLE001
        pass
    return f"店铺{shop_id}"


# ---------------------------------------------------------------------------
# 三档竞价解析（对多种返回形状都容忍）
# ---------------------------------------------------------------------------
def _nums(value: Any) -> List[Any]:
    out: List[Any] = []
    if isinstance(value, (list, tuple)):
        for v in value:
            out.extend(_nums(v))
    elif isinstance(value, dict):
        for v in value.values():
            out.extend(_nums(v))
    elif isinstance(value, str):
        out.extend(re.findall(r"\d+(?:\.\d+)?", value))
    elif isinstance(value, (int, float)):
        out.append(value)
    return out


def _band(value: Any) -> List[float]:
    """归一成 [低, 中, 高]（最多 3 个正数）"""
    out: List[float] = []
    for raw in _nums(value):
        try:
            f = float(raw)
        except (TypeError, ValueError):
            continue
        if f > 0:
            out.append(round(f, 2))
    return out[:3]


def _friendly(err: str) -> str:
    """把接口的原始报错翻成页面能看懂的提示"""
    if "code=2001" in err or "验证失败" in err:
        return "赛狐登录态已失效，请先在「同步登录态」里重新同步 Cookie"
    return err


# ---------------------------------------------------------------------------
# 合并查询（对齐赛狐：全部 ASIN 一次请求）
# ---------------------------------------------------------------------------
def fetch_bids(cookies: Dict[str, str], shop_id: str, asins: List[str],
               strategy: str = DEFAULT_STRATEGY,
               placement_top: Optional[float] = None,
               placement_rest: Optional[float] = None,
               placement_product: Optional[float] = None,
               campaign_id: str = "", group_id: str = "") -> Dict[str, List[float]]:
    """一次合并请求 → {pred: [低, 中, 高]}；asins 为空或全部取不到时返回 {}

    placement_*: 广告位加价百分比（赛狐会把表单当前值一并传给接口，0/空 → None）。
    """
    asins = [str(a).strip().upper() for a in (asins or []) if str(a).strip()]
    if not asins:
        return {}
    strategy = (strategy or DEFAULT_STRATEGY).strip().upper()
    if strategy not in VALID_STRATEGIES:
        strategy = DEFAULT_STRATEGY

    def _pct(v: Optional[float]) -> Optional[float]:
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f if f > 0 else None

    payload: Dict[str, Any] = {
        "shopId": str(shop_id),
        "biddingStrategy": strategy,
        "asins": asins,
        "targetingExpressions": [{"type": t, "value": ""} for t in TYPE_TO_PRED],
        "placementProductPage": _pct(placement_product),
        "placementTop": _pct(placement_top),
        "placementRestOfSearch": _pct(placement_rest),
        "campaignId": str(campaign_id or ""),
        "groupId": str(group_id or ""),
    }
    client = SellfoxClient(cookies)
    data = client.post(BID_API, payload).get("data") or {}
    out: Dict[str, List[float]] = {}
    for item in data.get("bidInfo") or []:
        if not isinstance(item, dict):
            continue
        pred = TYPE_TO_PRED.get(str(item.get("type") or "").upper())
        if not pred:
            continue
        band = _band(item.get("suggestedBids"))
        if band:
            out[pred] = band
    return out


# ---------------------------------------------------------------------------
# 否定商品搜索（对齐赛狐专用接口 searchAsinsForTargeting）
# ---------------------------------------------------------------------------
def search_asins_for_targeting(cookies: Dict[str, str], shop_id: str,
                               asins: List[str]) -> List[Dict[str, Any]]:
    """逐个 ASIN 调赛狐否定商品搜索接口

    实测: 入参 asins 是逗号分隔字符串, 但**只要混入一个无效 ASIN 整体返回空**,
    所以必须逐个请求。返回 [{asin, sku, title, img, found}]；无效 ASIN 标记 found=False。
    """
    client = SellfoxClient(cookies)
    out: List[Dict[str, Any]] = []
    for raw in asins:
        asin = str(raw).strip().upper()
        if not asin:
            continue
        try:
            data = client.post(
                ASIN_TARGETING_API,
                {"shopId": str(shop_id), "asins": asin},
            ).get("data") or []
        except Exception as exc:        # noqa: BLE001 - 单个失败不拖垮整批
            out.append({"asin": asin, "sku": None, "title": None, "img": "",
                        "found": False, "error": _friendly(str(exc))})
            continue
        hit = data[0] if data else None
        if hit:
            out.append({
                "asin": str(hit.get("asin") or asin).upper(),
                "sku": hit.get("sku"),
                "title": hit.get("title"),
                "img": hit.get("imgUrl") or "",
                "found": True,
            })
        else:
            out.append({"asin": asin, "sku": None, "title": None,
                        "img": "", "found": False})
    return out
