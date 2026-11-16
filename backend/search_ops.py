# -*- coding: utf-8 -*-
"""搜索词 / ASIN 处置指令任务。

流程：生成计划（只落任务，不改广告） -> 独立任务模块查询/预览 -> 人工确认后执行。
执行复用 backend.ads_ops 的 preview/execute（尤其是否定动作和创建活动/追加组）。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from backend import ads_store
from backend.ads_ops.campaign_groups import CampaignGroupsService
from backend.ads_ops.executor import Executor


def _ensure_table(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_op_tasks (
          task_id TEXT PRIMARY KEY,
          created_at TEXT NOT NULL,
          status TEXT NOT NULL,
          plan_json TEXT NOT NULL,
          result_json TEXT,
          confirmed_at TEXT,
          executed_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS search_op_status (
          campaign_id TEXT NOT NULL,
          group_id TEXT NOT NULL DEFAULT '',
          term TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'keyword',
          status TEXT NOT NULL,
          updated_at TEXT,
          PRIMARY KEY (campaign_id, group_id, term, kind)
        )
    """)
    conn.commit()


def _task_row(row) -> Dict[str, Any]:
    out = dict(row)
    for key in ("plan_json", "result_json"):
        try:
            out[key.replace("_json", "")] = json.loads(out.get(key) or "null")
        except Exception:  # noqa: BLE001
            out[key.replace("_json", "")] = None
        out.pop(key, None)
    return out


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:  # noqa: BLE001
        return 0.0


def _price(raw: Any) -> float:
    try:
        data = json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return 0.0
    for key in ("standardPrice", "landedPrice", "listingPrice", "saleSalePrice"):
        p = _num(data.get(key))
        if p > 0:
            return p
    return 0.0


def _source_product_rows(conn, campaign_id: str) -> List[Dict[str, Any]]:
    rows = [dict(r) for r in conn.execute("""
        SELECT DISTINCT fp.asin, dp.sku, dp.parent_asin
        FROM fact_product fp
        LEFT JOIN dim_product dp ON dp.asin=fp.asin
        WHERE fp.campaign_id=?
        UNION
        SELECT DISTINCT cp.asin, COALESCE(dp.sku, cp.sku),
               COALESCE(dp.parent_asin, cp.parent_asin)
        FROM dim_campaign_product cp
        LEFT JOIN dim_product dp ON dp.asin=cp.asin
        WHERE cp.campaign_id=?
    """, (campaign_id, campaign_id)).fetchall()]
    return [r for r in rows if r.get("asin")]


def _parent_asin(conn, campaign_id: str, item: Dict[str, Any]) -> str:
    for p in item.get("parent_asins") or []:
        if p:
            return str(p)
    rows = _source_product_rows(conn, campaign_id)
    for p in rows:
        if p.get("parent_asin"):
            return str(p["parent_asin"])
    row = conn.execute("SELECT name FROM dim_campaign WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    m = re.search(r"(B0[A-Z0-9]{8})", str(row["name"] if row else ""))
    return m.group(1) if m else ""


def _cheapest_children(conn, parent: str, limit: int = 5) -> List[Dict[str, Any]]:
    if not parent:
        return []
    rows = [dict(r) for r in conn.execute("""
        SELECT asin, sku, parent_asin, raw_json
        FROM online_products
        WHERE parent_asin=? OR asin=?
    """, (parent, parent)).fetchall()]
    # 本地产品表兜底
    if not rows:
        rows = [dict(r) for r in conn.execute("""
            SELECT asin, sku, parent_asin, NULL AS raw_json
            FROM dim_product WHERE parent_asin=? OR asin=?
        """, (parent, parent)).fetchall()]
    out = []
    for r in rows:
        asin = str(r.get("asin") or "").strip()
        if not asin or asin == parent:
            continue
        out.append({"asin": asin, "sku": str(r.get("sku") or ""),
                    "price": _price(r.get("raw_json"))})
    out.sort(key=lambda x: (x["price"] <= 0, x["price"], x["asin"]))
    return out[:limit]


def _manual_campaign(campaign_name: str) -> Optional[Dict[str, Any]]:
    """只有目标活动名称完全一致，才算已存在手工活动。"""
    target = str(campaign_name or "").strip()
    if not target:
        return None
    try:
        campaigns = CampaignGroupsService().load().get("campaigns") or []
    except Exception:  # noqa: BLE001
        campaigns = []
    for c in campaigns:
        name = str(c.get("campaign_name") or c.get("name") or "").strip()
        if (str(c.get("targeting_type") or "").lower() == "manual"
                and name == target):
            return c
    return None


def _product_label(conn, campaign_id: str, parent: str) -> str:
    """取品名用于活动/组命名；优先源活动名中的 CA_品名_父ASIN。"""
    row = conn.execute("SELECT name FROM dim_campaign WHERE campaign_id=?",
                       (campaign_id,)).fetchone()
    name = str(row["name"] if row else "")
    m = re.match(r"^CA_(.+?)_(B0[A-Z0-9]{8})", name)
    if m and m.group(1).strip():
        return m.group(1).strip()
    if parent:
        p = conn.execute("SELECT sku FROM dim_product WHERE asin=? OR parent_asin=? LIMIT 1",
                         (parent, parent)).fetchone()
        if p and p["sku"]:
            return str(p["sku"]).split("-")[0]
    return parent or "商品"


def _manual_campaign_name(label: str, parent: str) -> str:
    base = f"CA_{label}_{parent}_手动"
    return base.replace("/", "_").replace("\\", "_")[:120]


def _manual_group_name(label: str, parent: str) -> str:
    base = f"GR_{label}_{parent}_手动"
    return base.replace("/", "_").replace("\\", "_")[:120]


def _manual_target_params(kind: str, term: str, bid: float) -> Dict[str, Any]:
    if kind == "asin":
        return {"targetings": [{"type": "asin", "asin": term, "bid": bid}]}
    # 关键词搜索词：广泛 / 词组 / 精准三种匹配同时开启。
    return {"keywords": [
        {"keyword_text": term, "match_type": "broad", "bid": bid},
        {"keyword_text": term, "match_type": "phrase", "bid": bid},
        {"keyword_text": term, "match_type": "exact", "bid": bid},
    ]}


def _neg_instruction(item: Dict[str, Any], kind: str) -> Dict[str, Any]:
    cid = str(item.get("campaign_id") or "")
    group = str(item.get("group_name") or "")
    term = str(item.get("term") or "")
    action = "add_negative_asin" if kind == "asin" else "add_negative_keyword"
    selector = {"campaign_id": cid, "group": group}
    params = ({"asins": [term], "ne_level": "group"} if kind == "asin"
              else {"keywords": [{"keyword_text": term, "match_type": "negativeExact"}],
                    "ne_level": "group"})
    object_name = "ASIN" if kind == "asin" else "搜索词"
    match_detail = ("否定商品定位（按 ASIN 屏蔽）" if kind == "asin"
                    else "否定精准匹配（negativeExact）")
    detail = (
        f"在广告活动「{item.get('campaign_name') or cid}」（campaignId={cid}）"
        f"的广告组「{group or '-'}」中，添加否定{object_name}「{term}」；"
        f"匹配方式：{match_detail}；作用层级：广告组级。"
    )
    return {
        "id": "ins_" + uuid.uuid4().hex[:10],
        "action": action,
        "label": "否定 ASIN" if kind == "asin" else "否定关键词",
        "kind": kind,
        "campaign_id": cid,
        "campaign_name": item.get("campaign_name") or cid,
        "source_campaign_id": cid,
        "source_campaign_name": item.get("campaign_name") or cid,
        "group_id": item.get("group_id") or "",
        "source_group_id": item.get("group_id") or "",
        "group_name": group,
        "term": term,
        "selector": selector,
        "params": params,
        "sections": [
            ["操作", "否定 ASIN" if kind == "asin" else "否定关键词"],
            ["广告活动", item.get("campaign_name") or cid],
            ["campaignId", cid],
            ["广告组", group or "-"],
            ["否定对象", term],
            ["匹配方式", match_detail],
            ["作用层级", "广告组级"],
        ],
        "detail": detail,
        "status": "pending",
        "executed": False,
    }


def _manual_instructions(conn, item: Dict[str, Any], kind: str) -> List[Dict[str, Any]]:
    cid = str(item.get("campaign_id") or "")
    term = str(item.get("term") or "")
    parent = _parent_asin(conn, cid, item)
    children = _cheapest_children(conn, parent, 5)
    products = []
    for x in children:
        products.append({"asin": x["asin"]})
    if not products:
        # 没有在线子体价格时，退回自动活动实际投放的产品
        products = [{"asin": str(x.get("asin"))}
                    for x in _source_product_rows(conn, cid) if x.get("asin")][:5]
    suggested = round(_num(item.get("cpc"))) if _num(item.get("cpc")) > 0 else 30
    target_params = _manual_target_params(kind, term, suggested)
    product_label = _product_label(conn, cid, parent)
    group_name = _manual_group_name(product_label, parent or "UNKNOWN")
    campaign_name = _manual_campaign_name(product_label, parent or "UNKNOWN")
    # 只按目标活动名称完全匹配；模糊包含父 ASIN 的旧活动不算已存在。
    manual = _manual_campaign(campaign_name)
    detail_parts = []
    for x in products:
        asin = str(x.get("asin") or "")
        if not asin:
            continue
        child = next((c for c in children if str(c.get("asin")) == asin), {})
        sku = str(child.get("sku") or "")
        price = child.get("price") or 0
        detail_parts.append(f"{asin}"
                            + (f"（SKU {sku}" if sku else "（")
                            + (f"，价格 ¥{price:g}" if price else "")
                            + "）")
    detail_products = "、".join(detail_parts) or "未找到子 ASIN"
    target_detail = (f"商品定位 ASIN「{term}」，商品定位竞价 ¥{suggested}"
                     if kind == "asin" else
                     f"关键词「{term}」三种匹配全部开启：广泛（broad）、词组（phrase）、"
                     f"精准（exact），三个关键词竞价均为 ¥{suggested}")
    source_campaign = item.get("campaign_name") or cid
    if manual:
        cid2 = str(manual.get("campaign_id") or "")
        params = {
            "name": group_name,
            "default_bid": 30,
            "targeting_type": "manual",
            "products": products,
        }
        params.update(target_params)
        return [{
            "id": "ins_" + uuid.uuid4().hex[:10],
            "action": "add_group",
            "label": "手工活动追加组",
            "kind": kind,
            "campaign_id": cid2,
            "campaign_name": manual.get("campaign_name") or cid2,
            "source_campaign_id": cid,
            "source_campaign_name": item.get("campaign_name") or cid,
            "group_id": "",
            "source_group_id": item.get("group_id") or "",
            "group_name": group_name,
            "term": term,
            "selector": {"campaign_id": cid2},
            "params": params,
            "sections": [
                ["操作", "已有手工活动追加组"],
                ["来源自动活动", f"{source_campaign}（{cid}）"],
                ["已有手工活动", f"{manual.get('campaign_name') or cid2}（{cid2}）"],
                ["父 ASIN", parent or "未识别"],
                ["新广告组", group_name],
                ["组默认竞价", "¥30"],
                ["推广产品", detail_products],
                ["新增投放", target_detail],
            ],
            "detail": (
                f"来源自动活动：「{source_campaign}」（campaignId={cid}）；"
                f"已找到名称完全匹配的手工活动"
                f"「{manual.get('campaign_name') or cid2}」（campaignId={cid2}）；"
                f"在该手工活动下追加广告组「{group_name}」，"
                f"组默认竞价 ¥30；推广子 ASIN（最多 5 个）：{detail_products}；"
                f"新增{target_detail}。"
            ),
            "status": "pending",
            "executed": False,
        }]
    # 新手工活动：portfolio 固定“手工广告”
    source_budget = 300
    row = conn.execute("SELECT daily_budget FROM dim_campaign WHERE campaign_id=?",
                       (cid,)).fetchone()
    if row and _num(row["daily_budget"]) > 0:
        source_budget = _num(row["daily_budget"])
    params = {
        "name": campaign_name,
        "start_date": date.today().isoformat(),
        "daily_budget": source_budget,
        "targeting_type": "manual",
        "portfolio": "手工广告",
        "group_name": group_name,
        "default_bid": 30,
        "products": products,
    }
    params.update(target_params)
    return [{
        "id": "ins_" + uuid.uuid4().hex[:10],
        "action": "create_campaign",
        "label": "新建手工活动",
        "kind": kind,
        "campaign_id": cid,
        "campaign_name": campaign_name,
        "parent_asin": parent,
        "portfolio": "手工广告",
        "source_campaign_id": cid,
        "source_campaign_name": item.get("campaign_name") or cid,
        "group_id": "",
        "source_group_id": item.get("group_id") or "",
        "group_name": group_name,
        "term": term,
        "selector": {},
        "params": params,
        "sections": [
            ["操作", "新建手工活动"],
            ["来源自动活动", f"{source_campaign}（{cid}）"],
            ["父 ASIN", parent or "未识别"],
            ["广告组合", "手工广告"],
            ["新广告活动", campaign_name],
            ["开始日期", date.today().isoformat()],
            ["活动日预算", f"¥{source_budget:g}"],
            ["新广告组", group_name],
            ["组默认竞价", "¥30"],
            ["推广产品", detail_products],
            ["新增投放", target_detail],
        ],
        "detail": (
            f"来源自动活动：「{source_campaign}」（campaignId={cid}）；"
            f"父 ASIN {parent or '未识别'} 未发现名称完全匹配的手工活动；"
            f"在广告组合「手工广告」下新建广告活动「{campaign_name}」，"
            f"开始日期 {date.today().isoformat()}，活动日预算 ¥{source_budget:g}；"
            f"新建广告组「{group_name}」，组默认竞价 ¥30；"
            f"推广子 ASIN（按价格从低到高，最多 5 个）：{detail_products}；"
            f"新增{target_detail}。"
        ),
        "status": "pending",
        "executed": False,
    }]


def create_task(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        plan_items: List[Dict[str, Any]] = []
        warnings: List[str] = []
        for item in items or []:
            kind = "asin" if str(item.get("kind")) == "asin" else "keyword"
            action = "manual" if str(item.get("action")) == "manual" else "neg"
            cid = str(item.get("campaign_id") or "")
            term = str(item.get("term") or "")
            if not cid or not term:
                warnings.append("跳过缺少活动或词/ASIN 的记录")
                continue
            if action in ("neg", "manual"):
                plan_items.append(_neg_instruction(item, kind))
            if action == "manual":
                try:
                    plan_items.extend(_manual_instructions(conn, item, kind))
                except Exception as exc:  # noqa: BLE001
                    warnings.append(f"{term} 手工活动计划失败：{str(exc)[:160]}")
        task_id = "op_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
        plan = {
            "task_id": task_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "items": plan_items,
            "warnings": warnings,
        }
        conn.execute(
            "INSERT INTO search_op_tasks(task_id,created_at,status,plan_json) VALUES(?,?,?,?)",
            (task_id, plan["created_at"], "pending", json.dumps(plan, ensure_ascii=False)))
        conn.commit()
        return {"task_id": task_id, "status": "pending", "plan": plan}
    finally:
        conn.close()


def create_auto_campaign_task() -> Dict[str, Any]:
    """为尚无广告活动的父体生成「新建自动活动」指令（只预览，不执行）。"""
    import config
    from backend.cookies import load_cookies
    from backend.ad_creator.suggest_bid import fetch_bids

    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        parents = [dict(r) for r in conn.execute(
            "SELECT asin,sku,parent_asin,raw_json FROM online_products "
            "WHERE is_variation='1'").fetchall()]
        kids: Dict[str, List[Dict[str, Any]]] = {}
        for r in conn.execute(
                "SELECT asin,sku,parent_asin,online_status,raw_json FROM online_products "
                "WHERE is_variation='2'").fetchall():
            kids.setdefault(str(r["parent_asin"] or ""), []).append(dict(r))
        labels = {str(r["asin"]): str(r["label"] or "") for r in conn.execute(
            "SELECT asin,label FROM ol_ai_labels").fetchall()}
        try:
            live = CampaignGroupsService().load().get("campaigns") or []
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"读取赛狐活动失败：{str(exc)[:160]}") from exc
        campaign_names = {str(c.get("campaign_name") or "") for c in live}
        mapped = {str(r["asin"]) for r in conn.execute(
            "SELECT DISTINCT asin FROM dim_campaign_product").fetchall()}
        cookies = load_cookies()
        warnings: List[str] = []
        items: List[Dict[str, Any]] = []

        def has_campaign(parent_asin: str) -> bool:
            if any(parent_asin in n for n in campaign_names):
                return True
            if parent_asin in mapped:
                return True
            return any(str(k.get("asin") or "") in mapped
                       for k in kids.get(parent_asin, []))

        def clip(s: str, n: int = 120) -> str:
            return str(s or "")[:n]

        def mid(band: Any, fallback: float = 25) -> float:
            if isinstance(band, list) and len(band) >= 2:
                try:
                    v = float(band[1])
                    if v > 0:
                        return round(v, 2)
                except (TypeError, ValueError):
                    pass
            return fallback

        for p in parents:
            parent_asin = str(p.get("asin") or "")
            if not parent_asin or has_campaign(parent_asin):
                continue
            label = labels.get(parent_asin) or str(p.get("sku") or parent_asin)
            children = []
            for k in kids.get(parent_asin, []):
                price = _price(k.get("raw_json"))
                if price > 0 and str(k.get("online_status") or "").lower() == "active":
                    children.append((price, str(k.get("asin") or "")))
            children = [(price, asin) for price, asin in children if asin]
            children.sort(key=lambda x: (x[0], x[1]))
            child_asins = [asin for _, asin in children[:5]]
            if not child_asins:
                warnings.append(f"{parent_asin} 没有 Active 子 ASIN，无法创建广告，跳过")
                continue
            campaign_name = clip(f"CA_{label}_{parent_asin}")
            group_name = clip(f"GR_{label}_{parent_asin}")
            if campaign_name in campaign_names:
                warnings.append(f"{campaign_name} 已存在，跳过")
                continue
            try:
                raw = json.loads(p.get("raw_json") or "{}")
            except Exception:  # noqa: BLE001
                raw = {}
            shop_id = str(raw.get("shopId") or getattr(config, "DEFAULT_SHOP_ID", ""))
            try:
                bands = fetch_bids(cookies, shop_id, child_asins, "AUTO_FOR_SALES")
            except Exception as exc:  # noqa: BLE001
                bands = {}
                warnings.append(f"{parent_asin} 建议竞价读取失败，使用 25：{str(exc)[:80]}")
            auto_bids = {
                "queryHighRelMatches": mid(bands.get("close-match")),
                "queryBroadRelMatches": mid(bands.get("loose-match")),
                "asinSubstituteRelated": mid(bands.get("substitutes")),
                "asinAccessoryRelated": mid(bands.get("complements")),
            }
            params = {
                "name": campaign_name,
                "start_date": date.today().isoformat(),
                "end_date": "",
                "daily_budget": 300,
                "targeting_type": "auto",
                "strategy": "autoForSales",
                "portfolio": "自动生成指令缓冲区",
                "group_name": group_name,
                "default_bid": 25,
                "products": [{"asin": a} for a in child_asins],
                "auto_bids": auto_bids,
            }
            bid_text = " · ".join([
                f"紧密 ¥{auto_bids['queryHighRelMatches']}",
                f"宽泛 ¥{auto_bids['queryBroadRelMatches']}",
                f"同类 ¥{auto_bids['asinSubstituteRelated']}",
                f"关联 ¥{auto_bids['asinAccessoryRelated']}",
            ])
            sections = [
                ["活动名称", campaign_name],
                ["广告组合", "自动生成指令缓冲区"],
                ["每日预算", "¥300"],
                ["广告组", group_name],
                ["组默认竞价", "¥25"],
                ["推广子 ASIN", "、".join(child_asins)],
                ["投放竞价", bid_text],
            ]
            items.append({
                "id": "ins_" + uuid.uuid4().hex[:10],
                "action": "create_campaign",
                "label": "新建自动活动",
                "kind": "asin",
                "term": parent_asin,
                "campaign_id": "",
                "campaign_name": "未建活动",
                "source_campaign_id": "",
                "source_campaign_name": "未建活动",
                "group_id": "",
                "group_name": group_name,
                "parent_asin": parent_asin,
                "selector": {},
                "params": params,
                "sections": sections,
                "detail": f"为父体 {parent_asin} 新建自动活动 {campaign_name}，"
                          f"广告组 {group_name}，推广 {len(child_asins)} 个子 ASIN",
                "status": "pending",
                "executed": False,
            })
        task_id = "op_" + datetime.now().strftime("%Y%m%d%H%M%S") + "_" + uuid.uuid4().hex[:6]
        plan = {
            "task_id": task_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "auto_campaign",
            "items": items,
            "warnings": warnings,
        }
        conn.execute(
            "INSERT INTO search_op_tasks(task_id,created_at,status,plan_json) VALUES(?,?,?,?)",
            (task_id, plan["created_at"], "pending", json.dumps(plan, ensure_ascii=False)))
        conn.commit()
        return {"task_id": task_id, "status": "pending", "plan": plan}
    finally:
        conn.close()


def list_statuses(kind: str = "") -> List[Dict[str, Any]]:
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        if kind:
            rows = conn.execute(
                "SELECT campaign_id,group_id,term,kind,status,updated_at "
                "FROM search_op_status WHERE kind=? ORDER BY updated_at DESC",
                (kind,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT campaign_id,group_id,term,kind,status,updated_at "
                "FROM search_op_status ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_status(campaign_id: str, group_id: str, term: str, kind: str,
               status: str) -> Dict[str, Any]:
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        key = (str(campaign_id or ""), str(group_id or ""),
               str(term or ""), "asin" if kind == "asin" else "keyword")
        if not key[0] or not key[2]:
            return {"ok": False, "message": "campaign_id/term 不能为空"}
        if status:
            conn.execute(
                "INSERT INTO search_op_status(campaign_id,group_id,term,kind,status,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(campaign_id,group_id,term,kind) "
                "DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at",
                (*key, str(status), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        else:
            conn.execute(
                "DELETE FROM search_op_status WHERE campaign_id=? AND group_id=? "
                "AND term=? AND kind=?", key)
        conn.commit()
        return {"ok": True, "campaign_id": key[0], "group_id": key[1],
                "term": key[2], "kind": key[3], "status": str(status or "")}
    finally:
        conn.close()


def list_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        rows = conn.execute(
            "SELECT * FROM search_op_tasks ORDER BY created_at DESC LIMIT ?",
            (max(1, min(int(limit), 200)),)).fetchall()
        return [_task_row(r) for r in rows]
    finally:
        conn.close()


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        row = conn.execute("SELECT * FROM search_op_tasks WHERE task_id=?",
                           (task_id,)).fetchone()
        return _task_row(row) if row else None
    finally:
        conn.close()


def _execute_item(item: Dict[str, Any]) -> Dict[str, Any]:
    action = str(item.get("action") or "")
    selector = item.get("selector") or {}
    params = item.get("params") or {}
    ex = Executor()
    preview = ex.preview(action, selector, params)
    token = preview.get("confirm_token") or ""
    if not token:
        raise RuntimeError("preview 未返回 confirm_token")
    result = ex.execute(token, action=action, selector=selector, params=params)
    return {"ok": True, "preview": preview, "execute": result}


def delete_task(task_id: str) -> Dict[str, Any]:
    """删除未执行的任务；任一指令已执行或正在执行则拒绝。"""
    task = get_task(task_id)
    if not task:
        return {"ok": False, "reason": "not_found"}
    if task.get("status") == "executing":
        return {"ok": False, "reason": "executing", "message": "任务正在执行，不能删除"}
    items = (task.get("plan") or {}).get("items") or []
    executed = [x for x in items if x.get("executed")]
    if executed:
        return {"ok": False, "reason": "executed",
                "message": f"任务已有 {len(executed)} 条指令执行，不能删除"}
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        _ensure_table(conn)
        conn.execute("DELETE FROM search_op_tasks WHERE task_id=?", (task_id,))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "task_id": task_id, "plan": task.get("plan") or {}}


def retry_item(task_id: str, item_id: str) -> Dict[str, Any]:
    """只重试一条未执行的指令，不影响同任务其他已成功指令。"""
    task = get_task(task_id)
    if not task:
        return {"ok": False, "reason": "not_found", "message": "任务不存在"}
    if task.get("status") == "executing":
        return {"ok": False, "reason": "executing", "message": "任务正在执行，不能单独重试"}
    plan = task.get("plan") or {}
    item = next((x for x in (plan.get("items") or []) if x.get("id") == item_id), None)
    if not item:
        return {"ok": False, "reason": "item_not_found", "message": "指令不存在"}
    if item.get("executed"):
        return {"ok": False, "reason": "executed", "message": "该指令已经执行成功，不能重试"}

    item["status"] = "running"
    item.pop("error", None)
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        conn.execute("UPDATE search_op_tasks SET status=?, plan_json=? WHERE task_id=?",
                     ("executing", json.dumps(plan, ensure_ascii=False), task_id))
        conn.commit()
    finally:
        conn.close()

    try:
        result = _execute_item(item)
        item["executed"] = True
        item["status"] = "done"
        item["error"] = ""
        item["retry_result"] = result
        retry_ok = True
    except Exception as exc:  # noqa: BLE001
        item["status"] = "failed"
        item["error"] = str(exc)[:300]
        retry_ok = False

    items = plan.get("items") or []
    executed = [x for x in items if x.get("executed")]
    if len(executed) == len(items) and items:
        task_status = "done"
    elif executed:
        task_status = "partial"
    else:
        task_status = "failed"

    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        conn.execute("UPDATE search_op_tasks SET status=?, plan_json=?, executed_at=? WHERE task_id=?",
                     (task_status, json.dumps(plan, ensure_ascii=False),
                      datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id))
        conn.commit()
    finally:
        conn.close()
    return {"ok": retry_ok, "task_id": task_id, "item_id": item_id,
            "status": task_status, "message": "重试成功" if retry_ok else "重试失败",
            "task": get_task(task_id)}


def confirm_task(task_id: str) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None
    if task.get("status") not in ("pending", "partial", "failed"):
        return task
    plan = task.get("plan") or {}
    items = plan.get("items") or []
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        conn.execute("UPDATE search_op_tasks SET status=?, confirmed_at=? WHERE task_id=?",
                     ("executing", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id))
        conn.commit()
    finally:
        conn.close()
    results = []
    for item in items:
        if item.get("executed"):
            continue
        try:
            results.append({"id": item.get("id"), "ok": True,
                            "result": _execute_item(item)})
            item["executed"] = True
            item["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            item["status"] = "failed"
            item["error"] = str(exc)[:300]
            results.append({"id": item.get("id"), "ok": False,
                            "error": str(exc)[:300]})
    ok = sum(1 for x in results if x.get("ok"))
    failed = len(results) - ok
    status = "done" if failed == 0 else ("partial" if ok else "failed")
    conn = ads_store.get_conn()
    try:
        ads_store.init(conn)
        conn.execute(
            "UPDATE search_op_tasks SET status=?, plan_json=?, result_json=?, executed_at=? "
            "WHERE task_id=?",
            (status, json.dumps(plan, ensure_ascii=False),
             json.dumps(results, ensure_ascii=False),
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), task_id))
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id)
