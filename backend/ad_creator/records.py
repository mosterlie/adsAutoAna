# -*- coding: utf-8 -*-
"""广告活动记录（创建后入库，可修改，可批量导出）。

流程（v12 → v13）:
    以前: 点「创建」→ 直接下载 xlsx，关掉页面就没了
    现在: 点「创建」→ 生成 Bulk 行数据入库（ad_creation_records）
          → 列表里可「修改」（名称/预算/竞价/状态）
          → 勾选记录批量导出

为什么存「行数据」而不是只存参数:
    编号(CA_/GR_)按创建时刻的时间戳生成, 如果导出时重新生成, 编号会变,
    与已上传的表格对不上。所以入库的是生成好的行 JSON, 导出时原样拼装。

导出两种模式（决定 Operation 列）:
    create  整表 ``Create``：编号是我们自建的 CA_/GR_，导入亚马逊＝新建活动
    update  只导「活动/广告组」两行 ``Update``：必须先在记录里填亚马逊分配的
            真实 Campaign ID / Ad Group ID，否则亚马逊会报 EntityID does not exist.
    auto    逐条判断：填了真实 ID → Update，没填 → Create（批量导出可混合）
"""
from __future__ import annotations

import datetime
import io
import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend import database as db
from backend.ad_creator.bulk_generator import rows_to_excel

TABLE = "ad_creation_records"

#: 可修改字段（前端 patch 的键）
EDIT_FIELDS = (
    "campaign_name", "ad_group_name", "budget", "default_bid",
    "bid_close", "bid_loose", "bid_substitutes", "bid_complements",
    "state", "start_date", "amazon_campaign_id", "amazon_ad_group_id",
)

#: 「拓展商品投放编号」→ 对应可改的竞价字段
BID_FIELD_BY_PRED = {
    "close-match": "bid_close",
    "loose-match": "bid_loose",
    "substitutes": "bid_substitutes",
    "complements": "bid_complements",
}

VALID_STATES = {"enabled", "paused"}


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _split_by_campaign(rows: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """按 Campaign 行把整表切成「每个活动一块」。"""
    blocks: List[List[Dict[str, Any]]] = []
    for row in rows:
        if row.get("实体层级") == "Campaign" or not blocks:
            blocks.append([])
        blocks[-1].append(row)
    return blocks


def _doc(block: List[Dict[str, Any]]) -> Dict[str, Any]:
    """活动块 → 摘要字段。"""
    head = block[0] if block else {}
    group = next((r for r in block if r.get("实体层级") == "Ad Group"), {})
    return {
        "campaign_name": head.get("广告活动名称") or "",
        "campaign_code": head.get("广告活动编号") or "",
        "ad_group_code": group.get("广告组编号") or "",
        "budget": float(head.get("每日预算") or 0),
        "row_count": len(block),
    }


def create_records(products: List[Dict[str, Any]], settings: Dict[str, Any],
                   shop_name: str = "") -> Dict[str, Any]:
    """生成并入库一批广告活动记录（由 bulk_generator.build_rows 生成行）。"""
    from backend.ad_creator.bulk_generator import build_rows

    rows = build_rows(products, settings)          # 内含亚马逊规范自检
    blocks = _split_by_campaign(rows)
    # build_rows 会跳过没有子体的商品，这里同步过滤，保持与活动块一一对应
    used = [p for p in (products or []) if p.get("children")]
    if len(used) != len(blocks):
        used = [{} for _ in blocks]

    created = _now()
    settings_json = json.dumps(settings or {}, ensure_ascii=False)
    ids: List[int] = []
    records: List[Dict[str, Any]] = []

    with db._LOCK, db.get_conn() as conn:          # noqa: SLF001 - 与其他写入同一把锁
        for idx, block in enumerate(blocks):
            product = used[idx] or {}
            meta = _doc(block)
            children = product.get("children") or []
            cur = conn.execute(
                f"""INSERT INTO {TABLE}
                    (shop_name, parent_sku, product_name, campaign_name, campaign_code,
                     ad_group_code, budget, child_count, children_json, settings_json,
                     rows_json, status, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    shop_name or "",
                    product.get("parent_sku") or "",
                    product.get("product_name") or "",
                    meta["campaign_name"],
                    meta["campaign_code"],
                    meta["ad_group_code"],
                    meta["budget"],
                    len(children),
                    json.dumps(children, ensure_ascii=False),
                    settings_json,
                    json.dumps(block, ensure_ascii=False),
                    "created",
                    created,
                ),
            )
            rid = int(cur.lastrowid)
            ids.append(rid)
            records.append({"id": rid, **meta,
                            "parent_sku": product.get("parent_sku") or "",
                            "product_name": product.get("product_name") or "",
                            "child_count": len(children),
                            "created_at": created})

    return {"saved": len(ids), "ids": ids, "records": records}


def list_records(keyword: str = "", page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """分页列出记录（不含 rows_json，避免列表过大）。"""
    page = max(1, int(page or 1))
    page_size = max(1, min(200, int(page_size or 20)))
    where, args = "", []
    kw = (keyword or "").strip()
    if kw:
        like = f"%{kw}%"
        where = (" WHERE campaign_name LIKE ? OR campaign_code LIKE ? "
                 "OR parent_sku LIKE ? OR shop_name LIKE ?")
        args = [like, like, like, like]

    with db.get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM {TABLE}{where}", args).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT id, shop_name, parent_sku, product_name, campaign_name,
                       campaign_code, ad_group_code, budget, child_count,
                       status, created_at, updated_at, exported_at,
                       amazon_campaign_id, amazon_ad_group_id
                FROM {TABLE}{where}
                ORDER BY id DESC LIMIT ? OFFSET ?""",
            [*args, page_size, (page - 1) * page_size],
        ).fetchall()

    return {"rows": [dict(r) for r in rows], "total": int(total),
            "page": page, "page_size": page_size}


def get_record(rid: int) -> Dict[str, Any]:
    """单条记录详情（含可编辑字段与行数据，供「修改」弹窗回填）。"""
    with db.get_conn() as conn:
        r = conn.execute(f"SELECT * FROM {TABLE} WHERE id=?", [int(rid)]).fetchone()
    if not r:
        raise ValueError(f"记录不存在：{rid}")
    rec = dict(r)
    rows = json.loads(rec.pop("rows_json", "[]") or "[]")
    head = next((x for x in rows if x.get("实体层级") == "Campaign"), {})
    group = next((x for x in rows if x.get("实体层级") == "Ad Group"), {})
    bids = {pred: None for pred in BID_FIELD_BY_PRED}
    for x in rows:
        if x.get("实体层级") == "Product Targeting":
            pred = x.get("拓展商品投放编号")
            if pred in bids:
                bids[pred] = x.get("竞价")
    return {
        "id": rec["id"],
        "shop_name": rec.get("shop_name") or "",
        "parent_sku": rec.get("parent_sku") or "",
        "product_name": rec.get("product_name") or "",
        "campaign_name": head.get("广告活动名称") or rec.get("campaign_name") or "",
        "ad_group_name": group.get("广告组名称") or "",
        "campaign_code": rec.get("campaign_code") or "",
        "ad_group_code": rec.get("ad_group_code") or "",
        "budget": float(head.get("每日预算") or rec.get("budget") or 0),
        "default_bid": float(group.get("广告组默认竞价") or 0),
        "state": head.get("状态") or "enabled",
        "start_date": str(head.get("开始日期") or ""),
        "bids": bids,
        "amazon_campaign_id": rec.get("amazon_campaign_id") or "",
        "amazon_ad_group_id": rec.get("amazon_ad_group_id") or "",
        "amazon_ids": json.loads(rec.get("amazon_ids_json") or "null") or {},
        "status": rec.get("status") or "created",
        "created_at": rec.get("created_at") or "",
        "updated_at": rec.get("updated_at") or "",
        "row_count": len(rows),
    }


def _num(value: Any, field: str) -> float:
    try:
        out = round(float(value), 2)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是数字：{value!r}") from None
    if out <= 0:
        raise ValueError(f"{field} 必须大于 0")
    return out


def _apply_edits(rows: List[Dict[str, Any]], fields: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把修改应用到行数据（就地改并返回）。"""
    name = (fields.get("campaign_name") or "").strip() or None
    gname = (fields.get("ad_group_name") or "").strip() or None
    budget = fields.get("budget")
    default_bid = fields.get("default_bid")
    state = (fields.get("state") or "").strip() or None
    start = str(fields.get("start_date") or "").strip()

    for row in rows:
        entity = row.get("实体层级")
        if name:
            row["广告活动名称"] = name
        if state:
            row["状态"] = state
        if gname and row.get("广告组名称"):
            row["广告组名称"] = gname
        if entity == "Campaign":
            if budget is not None:
                row["每日预算"] = budget
            if start:
                row["开始日期"] = int(start.replace("-", "").replace("/", ""))
        elif entity == "Ad Group":
            if default_bid is not None:
                row["广告组默认竞价"] = default_bid
        elif entity == "Product Targeting":
            field = BID_FIELD_BY_PRED.get(str(row.get("拓展商品投放编号")))
            if field and fields.get(field) is not None:
                row["竞价"] = fields[field]
    return rows


def update_record(rid: int, fields: Dict[str, Any]) -> Dict[str, Any]:
    """修改单条记录（名称/预算/竞价/状态/亚马逊真实 ID）。"""
    cur = get_record(rid)                       # 不存在时抛错
    patch: Dict[str, Any] = {}

    for key in ("campaign_name", "ad_group_name"):
        if fields.get(key) is not None:
            val = str(fields[key]).strip()
            if not val:
                raise ValueError("活动名称 / 广告组名称不能为空")
            patch[key] = val
    if fields.get("budget") is not None:
        patch["budget"] = _num(fields["budget"], "每日预算")
    if fields.get("default_bid") is not None:
        patch["default_bid"] = _num(fields["default_bid"], "广告组默认竞价")
    for key in ("bid_close", "bid_loose", "bid_substitutes", "bid_complements"):
        if fields.get(key) is not None:
            patch[key] = _num(fields[key], key)
    if fields.get("state") is not None:
        st = str(fields["state"]).strip().lower()
        if st not in VALID_STATES:
            raise ValueError("状态只能是 enabled / paused")
        patch["state"] = st
    if fields.get("start_date"):
        raw = str(fields["start_date"]).replace("-", "").replace("/", "").strip()
        if not raw.isdigit() or len(raw) != 8:
            raise ValueError("开始日期格式应为 YYYY-MM-DD")
        patch["start_date"] = raw
    for key in ("amazon_campaign_id", "amazon_ad_group_id"):
        if fields.get(key) is not None:
            patch[key] = str(fields[key]).strip()

    # 一键回填的完整真实 ID（赛狐 lookup 的结构）
    incoming_ids = fields.get("amazon_ids")
    if isinstance(incoming_ids, dict) and incoming_ids:
        camp = incoming_ids.get("campaign") or {}
        groups = incoming_ids.get("ad_groups") or []
        patch["amazon_campaign_id"] = str(camp.get("campaign_id") or "").strip()
        patch["amazon_ad_group_id"] = str(
            (groups[0] or {}).get("ad_group_id") if groups else "").strip()

    with db._LOCK, db.get_conn() as conn:       # noqa: SLF001
        row = conn.execute(
            f"SELECT rows_json, status, amazon_ids_json, state_dirty FROM {TABLE} WHERE id=?",
            [int(rid)]).fetchone()
        if not row:
            raise ValueError(f"记录不存在：{rid}")
        rows = json.loads(row["rows_json"] or "[]")
        _apply_edits(rows, patch)
        meta = _doc(rows)
        # 改过就标记「已修改」，导出后会被置回 exported
        new_status = "modified"
        ids_json = row["amazon_ids_json"]
        if isinstance(incoming_ids, dict) and incoming_ids:
            ids_json = json.dumps(incoming_ids, ensure_ascii=False)
        state_dirty = 1 if "state" in patch else int(row["state_dirty"] or 0)
        conn.execute(
            f"""UPDATE {TABLE} SET rows_json=?, campaign_name=?, budget=?,
                   status=?, updated_at=?, amazon_campaign_id=?, amazon_ad_group_id=?,
                   amazon_ids_json=?, state_dirty=?
                WHERE id=?""",
            (
                json.dumps(rows, ensure_ascii=False),
                meta["campaign_name"],
                meta["budget"],
                new_status,
                _now(),
                patch.get("amazon_campaign_id", cur.get("amazon_campaign_id") or ""),
                patch.get("amazon_ad_group_id", cur.get("amazon_ad_group_id") or ""),
                ids_json,
                state_dirty,
                int(rid),
            ),
        )

    return {"ok": True, "id": int(rid), "status": new_status, **meta}


def delete_records(ids: Iterable[int]) -> int:
    id_list = [int(i) for i in (ids or [])]
    if not id_list:
        return 0
    marks = ",".join("?" * len(id_list))
    with db._LOCK, db.get_conn() as conn:
        cur = conn.execute(f"DELETE FROM {TABLE} WHERE id IN ({marks})", id_list)
        return int(cur.rowcount or 0)


def _update_rows(block: List[Dict[str, Any]], cid: str,
                 gid: Optional[str],
                 amazon_ids: Optional[Dict[str, Any]] = None,
                 state_dirty: bool = False) -> List[Dict[str, Any]]:
    """活动块 → 亚马逊 Update 行。

    Update 靠 ID 定位实体，所以「广告活动编号/广告组编号/商品投放 ID/关键词编号」
    必须填亚马逊分配的真实 ID；其余列填要改成的目标值。
    拿到赛狐回填的完整 ID 时，一并生成投放竞价、否定关键词、否定商品行。
    """
    head = next((x for x in block if x.get("实体层级") == "Campaign"), {})
    group = next((x for x in block if x.get("实体层级") == "Ad Group"), {})
    state = head.get("状态") or "enabled"
    camp_row: Dict[str, Any] = {
        "产品": "Sponsored Products",
        "实体层级": "Campaign",
        "操作": "Update",
        "广告活动编号": cid,
        "广告活动名称": head.get("广告活动名称") or "",
        "每日预算": head.get("每日预算"),
    }
    # 只有用户显式改过状态才写状态列，否则留空（避免把后台已暂停的活动重新启用）
    if state_dirty:
        camp_row["状态"] = state
    out: List[Dict[str, Any]] = [camp_row]

    ids = amazon_ids or {}
    # 广告组：赛狐回填的每一个组都出一行（不止第一个）
    group_entries = list(ids.get("ad_groups") or [])
    if not group_entries and gid:
        group_entries = [{"ad_group_id": gid,
                          "name": group.get("广告组名称") or "",
                          "default_bid": group.get("广告组默认竞价")}]
    for idx, g in enumerate(group_entries):
        real_gid = str(g.get("ad_group_id") or "").strip()
        if not real_gid:
            continue
        group_row: Dict[str, Any] = {
            "产品": "Sponsored Products",
            "实体层级": "Ad Group",
            "操作": "Update",
            "广告活动编号": cid,
            "广告组编号": real_gid,
            "广告活动名称": head.get("广告活动名称") or "",
            # 第一组用记录里改过的名称/竞价，其余组用赛狐真实值
            "广告组名称": (group.get("广告组名称") if idx == 0 else g.get("name"))
                          or g.get("name") or "",
            "广告组默认竞价": (group.get("广告组默认竞价") if idx == 0
                              else g.get("default_bid")),
        }
        if state_dirty:
            group_row["状态"] = group.get("状态") or "enabled"
        out.append(group_row)

    fallback_gid = (str(group_entries[0].get("ad_group_id") or "") if group_entries else "")
    bid_by_pred = {str(x.get("拓展商品投放编号") or ""): x.get("竞价")
                   for x in block if x.get("实体层级") == "Product Targeting"}

    # 投放（自动四组按表达式对应已改的竞价；手动关键词带关键词文本）
    for t in ids.get("targets") or []:
        tid = str(t.get("target_id") or "").strip()
        if not tid:
            continue
        expr = str(t.get("expression") or "")
        row: Dict[str, Any] = {
            "产品": "Sponsored Products",
            "实体层级": "Product Targeting",
            "操作": "Update",
            "广告活动编号": cid,
            "广告组编号": str(t.get("ad_group_id") or "") or fallback_gid,
            "商品投放 ID": tid,
        }
        if expr and expr in bid_by_pred:
            row["拓展商品投放编号"] = expr
            row["竞价"] = bid_by_pred[expr]
        elif t.get("keyword_text"):
            row["关键词文本"] = t["keyword_text"]
            row["匹配类型"] = t.get("match_type") or ""
            row["竞价"] = t.get("bid") or ""
        out.append(row)

    # 否定关键词
    for n in ids.get("neg_keywords") or []:
        nid = str(n.get("ne_target_id") or "").strip()
        if not nid:
            continue
        campaign_level = str(n.get("ne_level") or "campaign").lower() == "campaign"
        row = {
            "产品": "Sponsored Products",
            "实体层级": "Campaign Negative Keyword" if campaign_level else "Negative Keyword",
            "操作": "Update",
            "广告活动编号": cid,
            "关键词编号": nid,
        }
        if not campaign_level:
            agid = str(n.get("ad_group_id") or "") or fallback_gid
            if agid:
                row["广告组编号"] = agid
        out.append(row)

    # 否定商品
    for n in ids.get("neg_products") or []:
        nid = str(n.get("ne_target_id") or "").strip()
        if not nid:
            continue
        campaign_level = str(n.get("ne_level") or "campaign").lower() == "campaign"
        row = {
            "产品": "Sponsored Products",
            "实体层级": ("Campaign Negative Product Targeting" if campaign_level
                         else "Negative Product Targeting"),
            "操作": "Update",
            "广告活动编号": cid,
            "商品投放 ID": nid,
        }
        if not campaign_level:
            agid = str(n.get("ad_group_id") or "") or fallback_gid
            if agid:
                row["广告组编号"] = agid
        out.append(row)

    return out


def export_records(ids: Iterable[int],
                   mode: str = "auto") -> Tuple[io.BytesIO, str, int]:
    """多记录 → 一个 xlsx。

    mode: create | update | auto（逐条判断是否有亚马逊真实 ID）
    返回 (buffer, filename, campaign_count)
    """
    id_list = sorted({int(i) for i in (ids or [])})
    if not id_list:
        raise ValueError("请先勾选要导出的广告活动记录")
    mode = (mode or "auto").lower()
    if mode not in ("create", "update", "auto"):
        raise ValueError("导出模式只能是 create / update / auto")

    marks = ",".join("?" * len(id_list))
    with db._LOCK, db.get_conn() as conn:       # noqa: SLF001
        rows = conn.execute(
            f"""SELECT id, campaign_name, rows_json, amazon_campaign_id,
                       amazon_ad_group_id, amazon_ids_json, state_dirty
                FROM {TABLE} WHERE id IN ({marks}) ORDER BY id""",
            id_list,
        ).fetchall()
        if not rows:
            raise ValueError("勾选的记录不存在或已被删除")

        merged: List[Dict[str, Any]] = []
        count = 0
        need_id: List[str] = []
        used_update = False
        for r in rows:
            block = json.loads(r["rows_json"] or "[]")
            cid = (r["amazon_campaign_id"] or "").strip()
            gid = (r["amazon_ad_group_id"] or "").strip()
            want_update = mode == "update" or (mode == "auto" and bool(cid))
            if mode == "update" and not cid:
                need_id.append(r["campaign_name"] or f"#{r['id']}")
                continue
            if want_update:
                try:
                    full_ids = json.loads(r["amazon_ids_json"] or "null") or {}
                except (TypeError, ValueError):
                    full_ids = {}
                merged.extend(_update_rows(block, cid, gid or None, full_ids,
                                           state_dirty=bool(r["state_dirty"])))
                used_update = True
            else:
                merged.extend(block)
            count += 1

        if mode == "update" and need_id:
            raise ValueError(
                "以下记录没有填「亚马逊广告活动 ID」，无法生成 Update 表："
                + "、".join(need_id[:5]))
        if not merged:
            raise ValueError("没有可导出的行")

        buffer = rows_to_excel(merged)
        conn.executemany(
            f"UPDATE {TABLE} SET status='exported', exported_at=? WHERE id=?",
            [(_now(), int(r["id"])) for r in rows],
        )

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M")
    prefix = "ad_updates" if (mode == "update" or used_update) else "ad_creations"
    return buffer, f"{prefix}_{stamp}.xlsx", count
