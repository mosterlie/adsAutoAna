# -*- coding: utf-8 -*-
"""预抓取目标: 「点击量达标」的搜索词 → 它归属的父 ASIN

链路(全部来自已落库数据):
    搜索词(ad_records tab='search', scope='sp:keyword')
        --campaignId--> 广告产品(tab='product').asin
        --在线产品变体关系--> 父 ASIN (没有变体关系时回退为 ASIN 自身)

一个搜索词可能同时挂在多个广告活动下(也就归属多个父 ASIN), 这些父 ASIN
都会收到一份该搜索词的数据。
"""
import json
from typing import Any, Dict, List, Optional

from backend import database as db


def _search_rows(keywords_only: bool = True) -> List[Dict[str, Any]]:
    sql = "SELECT scope, raw_json FROM ad_records WHERE tab='search'"
    if keywords_only:
        sql += " AND scope LIKE '%keyword'"
    out: List[Dict[str, Any]] = []
    with db.get_conn() as conn:
        for r in conn.execute(sql):
            try:
                out.append(json.loads(r["raw_json"]))
            except Exception:       # noqa: BLE001
                continue
    return out


def _asin_parent_map() -> Dict[str, str]:
    """子 ASIN -> 父 ASIN (在线产品变体关系; 未采集时为空, 上层回退自身)"""
    try:
        return db.get_asin_parent_map()
    except Exception:       # noqa: BLE001
        return {}


def _campaign_asins() -> Dict[str, set]:
    """campaignId -> 该活动下的广告产品 ASIN 集合"""
    out: Dict[str, set] = {}
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT raw_json FROM ad_records WHERE tab='product'").fetchall()
    for r in rows:
        try:
            d = json.loads(r["raw_json"])
        except Exception:       # noqa: BLE001
            continue
        cid, asin = d.get("campaignId"), d.get("asin")
        if cid and asin:
            out.setdefault(cid, set()).add(asin)
    return out


def parents_for_asin(asin: str, parent_map: Optional[Dict[str, str]] = None) -> str:
    """单个 ASIN 的父 ASIN (无变体关系则自身)"""
    if not asin:
        return ""
    if parent_map is None:
        parent_map = _asin_parent_map()
    return parent_map.get(asin) or asin


def build(min_clicks: int = 2, keywords_only: bool = True,
          limit: int = 0) -> List[Dict[str, Any]]:
    """返回达标搜索词清单(按点击、花费降序)

    每项: {query, clicks, impressions, adCost, campaign_ids, match_types,
           asins, parents, parent_mapped}
        parents      归属的父 ASIN 列表(去重, 有序)
        parent_mapped 归属父 ASIN 是否来自真实变体关系(False = 回退了自身)
    """
    parent_map = _asin_parent_map()
    camp_asins = _campaign_asins()

    # 先按词汇总(同一词可能有多行: 不同活动 / 不同匹配方式), 汇总后再比阈值
    agg: Dict[str, Dict[str, Any]] = {}
    for d in _search_rows(keywords_only):
        q = (d.get("query") or d.get("keywordText") or "").strip()
        if not q:
            continue
        try:
            clicks = int(d.get("clicks") or 0)
        except (TypeError, ValueError):
            clicks = 0
        a = agg.setdefault(q, {"query": q, "clicks": 0, "impressions": 0,
                               "adCost": 0.0, "campaign_ids": set(),
                               "match_types": set()})
        a["clicks"] += clicks
        for k, caster in (("impressions", int), ("adCost", float)):
            try:
                a[k] += caster(d.get(k) or 0)
            except (TypeError, ValueError):
                pass
        if d.get("campaignId"):
            a["campaign_ids"].add(d["campaignId"])
        if d.get("matchType"):
            a["match_types"].add(d["matchType"])

    out: List[Dict[str, Any]] = []
    for q, a in agg.items():
        if a["clicks"] < max(0, min_clicks):
            continue
        asins: set = set()
        for cid in a["campaign_ids"]:
            asins |= camp_asins.get(cid, set())
        parents, unmapped = [], []
        for asin in sorted(asins):
            p = parent_map.get(asin)
            if p:
                if p not in parents:
                    parents.append(p)
            else:
                unmapped.append(asin)
                if asin not in parents:
                    parents.append(asin)
        a["asins"] = sorted(asins)
        a["parents"] = parents
        a["parent_mapped"] = bool(parents) and not unmapped
        a["unmapped_asins"] = unmapped
        a["campaign_ids"] = sorted(a["campaign_ids"])
        a["match_types"] = sorted(a["match_types"])
        out.append(a)

    out.sort(key=lambda x: (-x["clicks"], -x["adCost"]))
    return out[:limit] if limit and limit > 0 else out


def group_by_parent(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """把目标词按父 ASIN 分组: 一个父 ASIN 一组, 组内按点击降序

    抓取顺序 = 组顺序: 一个父 ASIN 下的词全部抓完, 再进入下一个父 ASIN。
    组按"该父体下所有词的点击合计"降序, 先把重要的父体抓完。
    (一个词挂在多个父体下时, 各组里都会出现; 由于抓一次会同时写入它所有的父目录,
     后续组遇到同一个词会直接跳过, 不会重复访问亚马逊。)
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for t in items:
        for p in (t.get("parents") or ["_unmapped"]):
            groups.setdefault(p, []).append(t)

    out: List[Dict[str, Any]] = []
    for parent, terms in groups.items():
        terms = sorted(terms, key=lambda x: (-x["clicks"], -x["adCost"]))
        out.append({
            "parent": parent,
            "terms": terms,
            "clicks": sum(t["clicks"] for t in terms),
            "adCost": round(sum(t["adCost"] for t in terms), 2),
        })
    out.sort(key=lambda g: (-g["clicks"], g["parent"]))
    return out


def parents_of(query: str, keywords_only: bool = True) -> List[str]:
    """单个搜索词的归属父 ASIN(网页查看时用它决定落哪个目录)"""
    for t in build(0, keywords_only):
        if t["query"] == query:
            return t["parents"]
    return []


def summary(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """汇总: 词数 / 父 ASIN 数 / 阿里没有变体映射的 ASIN 数"""
    parents = {p for t in items for p in t["parents"]}
    unmapped = {a for t in items for a in t.get("unmapped_asins") or []}
    return {"terms": len(items), "parents": len(parents),
            "unmapped_asins": len(unmapped),
            "clicks": sum(t["clicks"] for t in items),
            "adCost": round(sum(t["adCost"] for t in items), 2),
            "parent_list": sorted(parents)}
