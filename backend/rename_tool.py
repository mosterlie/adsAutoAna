# -*- coding: utf-8 -*-
"""活动 / 广告组改名工具

命名规则
    活动  CA_<商品标识>_<父ASIN>
    广告组 GR_<商品标识>_<父ASIN>

商品标识来源（按优先级）
    1. ol_ai_labels 表（AI 解析标题得到的中文短语）
    2. 「未识别」兜底

接口
    活动改名  POST /api/cpcSPManage/campaign/updateName.json   (form-urlencoded) {shopId,id,name}
    组改名    POST /api/cpcSPManage/group/update.json          (JSON) {shopId,dxmGroupId,name,defaultBid}

用法
    python -m backend.rename_tool --campaign 88247362850188 --dry-run
    python -m backend.rename_tool --campaign 88247362850188
    python -m backend.rename_tool --portfolio 29811114365267 --dry-run   # 组合下全部活动
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

import config
from backend.cookies import load_cookies

ORIGIN = "https://www.sellfox.com"
GW = ORIGIN + "/api/gw/sellfox/sellfox-cpc/api/sellfox"
SHOP_ID = "711811"
HDR_JSON = {
    "Content-Type": "application/json",
    "Origin": ORIGIN,
    "Referer": f"{ORIGIN}/cpc-single/web/cpcManage/manage/single.html",
    "X-Language": "zh-CN",
    "User-Agent": config.USER_AGENT,
}
HDR_FORM = dict(HDR_JSON, **{"Content-Type": "application/x-www-form-urlencoded"})


def _ck() -> Dict[str, str]:
    ck = load_cookies()
    if not ck:
        raise RuntimeError("未找到登录 Cookie（data/cookies.json）")
    return ck


def post_json(url: str, body: Dict[str, Any], timeout: int = 40) -> Dict[str, Any]:
    r = requests.post(url, json=body, headers=HDR_JSON, cookies=_ck(), timeout=timeout)
    return r.json()


def post_form(url: str, form: Dict[str, Any], timeout: int = 40) -> Dict[str, Any]:
    r = requests.post(url, data=form, headers=HDR_FORM, cookies=_ck(), timeout=timeout)
    return r.json()


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
def list_campaigns(day: str, portfolio_id: str = "") -> List[Dict[str, Any]]:
    body = {
        "shopIdList": [SHOP_ID], "adType": "sp",
        "statusList": ["enabled", "paused"],
        "startDate": day, "endDate": day,
        "orderField": "adCost", "orderType": "desc",
        "pageNo": 1, "pageSize": 500, "fromSingle": True,
    }
    if portfolio_id:
        body["portfolioIdList"] = [portfolio_id]
    d = post_json(f"{GW}/campaign/getAllCampaignData", body)
    page = (d.get("data") or {}).get("page") or {}
    rows = page.get("rows") or []
    if portfolio_id:   # 该接口 portfolioIdList 可能不严格生效，二次过滤
        rows = [r for r in rows if str(r.get("portfolioId") or "") == str(portfolio_id)]
    return rows


def campaign_name_of(campaign_id: str) -> str:
    """精确读单个活动名（0.16s，36 字段），替代全店列表校验"""
    try:
        d = post_json(f"{GW}/adCampaign/getInfo",
                      {"shopId": SHOP_ID, "campaignId": str(campaign_id), "type": "sp"})
        data = d.get("data") or {}
        return str(data.get("campaignName") or data.get("name") or "")
    except Exception:                       # noqa: BLE001
        return ""


def list_groups(campaign_id: str, day: str) -> List[Dict[str, Any]]:
    d = post_json(f"{GW}/multiple/group/getAllGroupData", {
        "shopIdList": [SHOP_ID], "campaignIdList": [campaign_id],
        "adType": "sp", "startDate": day, "endDate": day})
    return ((d.get("data") or {}).get("page") or {}).get("rows") or []


def campaign_products(campaign_id: str, day: str) -> List[Dict[str, Any]]:
    d = post_json(f"{GW}/multiple/adProduct/getAdProductList", {
        "type": "sp", "campaignIdList": [campaign_id], "shopIdList": [SHOP_ID],
        "statusList": ["enabled", "paused"], "startDate": day, "endDate": day,
        "pageNo": 1, "pageSize": 200, "orderBy": "adCost", "desc": True,
        "searchContents": [], "searchMode": "exact", "searchType": "asin",
        "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": False})
    return ((d.get("data") or {}).get("page") or {}).get("rows") or []


def parent_asin_of(asins: List[str]) -> str:
    """ASIN → 父 ASIN（优先 online_products，其次产品接口）"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        for a in asins:
            r = conn.execute("SELECT parent_asin FROM online_products WHERE asin=?",
                             (a,)).fetchone()
            if r and r["parent_asin"]:
                return str(r["parent_asin"])
    finally:
        conn.close()
    return ""


def brand_label(asin: str) -> str:
    """商品标识：ol_ai_labels 里该 ASIN 的标签"""
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        r = conn.execute("SELECT label FROM ol_ai_labels WHERE asin=?", (asin,)).fetchone()
        return str(r["label"]).strip() if r and r["label"] else ""
    finally:
        conn.close()


def sync_local_name(campaign_id: str, campaign_name: str,
                    group_rows: List[Dict[str, Any]]) -> None:
    """把新名字同步到本地 dim_campaign / dim_group"""
    conn = sqlite3.connect(config.DB_PATH)
    try:
        conn.execute("UPDATE dim_campaign SET name=?, updated_at=? WHERE campaign_id=?",
                     (campaign_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                      campaign_id))
        for g in group_rows:
            conn.execute("UPDATE dim_group SET name=?, updated_at=? WHERE group_id=?",
                         (g["name"], datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                          str(g["id"])))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 改名
# ---------------------------------------------------------------------------
def rename_campaign(inner_id: str, new_name: str) -> Dict[str, Any]:
    """活动改名（form-urlencoded，id 用赛狐内部 id）"""
    return post_form(f"{ORIGIN}/api/cpcSPManage/campaign/updateName.json",
                     {"shopId": SHOP_ID, "id": str(inner_id), "name": new_name})


def rename_group(dxm_group_id: str, new_name: str, default_bid: Any) -> Dict[str, Any]:
    """组改名（JSON，必须带 defaultBid 保持原值）"""
    return post_json(f"{ORIGIN}/api/cpcSPManage/group/update.json",
                     {"shopId": SHOP_ID, "dxmGroupId": int(dxm_group_id),
                      "name": new_name, "defaultBid": f"{float(default_bid):.2f}"})


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def plan_one(campaign_id: str, day: str) -> Dict[str, Any]:
    """生成该活动的改名计划（不改动）"""
    camps = list_campaigns(day)
    camp = next((c for c in camps if str(c.get("campaignId")) == str(campaign_id)), None)
    if not camp:
        raise RuntimeError(f"活动 {campaign_id} 未找到")

    prods = campaign_products(campaign_id, day)
    asins = [p.get("asin") for p in prods if p.get("asin")]
    parent = parent_asin_of(asins)
    label = brand_label(parent) or "未识别"

    groups = list_groups(campaign_id, day)
    return {
        "campaignId": str(campaign_id),
        "campaignInnerId": str(camp.get("id") or ""),
        "campaignName": str(camp.get("name") or ""),
        "newCampaignName": f"CA_{label}_{parent}",
        "parentAsin": parent,
        "label": label,
        "asins": asins,
        "groups": [{
            "id": str(g.get("id") or ""),
            "adGroupId": str(g.get("adGroupId") or ""),
            "name": str(g.get("name") or ""),
            "defaultBid": g.get("defaultBid"),
            "newName": f"GR_{label}_{parent}",
        } for g in groups],
    }


def apply_one(plan: Dict[str, Any], verify_day: str) -> Dict[str, Any]:
    """执行改名 + 读回校验 + 失败的自动回滚"""
    log: Dict[str, Any] = {"plan": plan, "steps": []}
    backup = {
        "campaignName": plan["campaignName"],
        "groups": [{"id": g["id"], "name": g["name"], "defaultBid": g["defaultBid"]}
                   for g in plan["groups"]],
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    log["backup"] = backup

    # --- 活动改名 ---
    r = rename_campaign(plan["campaignInnerId"], plan["newCampaignName"])
    log["steps"].append({"step": "rename_campaign", "resp": r})
    if r.get("code") != 0:
        raise RuntimeError(f"活动改名失败: {r}")
    time.sleep(0.25)
    got_name = campaign_name_of(plan["campaignId"])
    ok = got_name == plan["newCampaignName"]
    log["steps"].append({"step": "verify_campaign", "name": got_name, "ok": bool(ok)})
    if not ok:
        rename_campaign(plan["campaignInnerId"], backup["campaignName"])   # 回滚
        raise RuntimeError(f"活动改名校验失败，已回滚。读回={got_name}")

    # --- 组改名 ---
    for g, b in zip(plan["groups"], backup["groups"]):
        r = rename_group(g["id"], g["newName"], g["defaultBid"])
        log["steps"].append({"step": f"rename_group {g['id']}", "resp": r})
        if r.get("code") != 0:
            raise RuntimeError(f"组改名失败 {g['id']}: {r}")
        time.sleep(0.2)

    # --- 组读回校验 ---
    gs = list_groups(plan["campaignId"], verify_day)
    check = []
    for g in plan["groups"]:
        now = next((x for x in gs if str(x.get("id")) == g["id"]), None)
        check.append({
            "id": g["id"], "name": now.get("name") if now else None,
            "defaultBid": now.get("defaultBid") if now else None,
            "ok": bool(now) and str(now.get("name")) == g["newName"]
                  and abs(float(now.get("defaultBid") or 0) - float(g["defaultBid"] or 0)) < 0.01,
        })
    log["steps"].append({"step": "verify_groups", "check": check})

    failed = [c for c in check if not c["ok"]]
    if failed:
        # 回滚：组名 + 活动名
        for g, b in zip(plan["groups"], backup["groups"]):
            rename_group(g["id"], b["name"], b["defaultBid"])
        rename_campaign(plan["campaignInnerId"], backup["campaignName"])
        raise RuntimeError(f"组改名校验失败，已全部回滚: {failed}")

    # --- 同步本地库 ---
    sync_local_name(plan["campaignId"], plan["newCampaignName"], gs)
    log["result"] = "ok"
    return log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", default="", help="单个 Amazon campaignId")
    ap.add_argument("--portfolio", default="", help="广告组合 id（批量）")
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = ap.parse_args()

    if not args.campaign and not args.portfolio:
        raise SystemExit("需要 --campaign 或 --portfolio")

    cids = [args.campaign] if args.campaign else [
        str(c.get("campaignId")) for c in list_campaigns(args.day, args.portfolio)]

    print(f"待处理活动 {len(cids)} 个")
    for cid in cids:
        plan = plan_one(cid, args.day)
        print(json.dumps(plan, ensure_ascii=False, indent=1))
        if args.dry_run:
            continue
        log = apply_one(plan, args.day)
        print("✓ 已改:", plan["newCampaignName"])
        for g in plan["groups"]:
            print("  ✓", g["newName"], "| bid", g["defaultBid"])
        with open(f"/tmp/rename_{cid}.json", "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
