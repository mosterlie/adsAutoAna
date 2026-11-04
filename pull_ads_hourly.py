# -*- coding: utf-8 -*-
"""拉取赛狐广告数据（最细粒度 = 小时）落库

用法：
    # 单个活动某天（含广告组/投放/广告位/广告产品，小时级）
    python pull_ads_hourly.py --date 2026-11-03 --campaign-id 222563897245121

    # 整个店铺某天所有活动（自动枚举；默认跳过零曝光活动）
    python pull_ads_hourly.py --date 2026-11-03

    # 回补一段日期（逐天），断点续跑
    python pull_ads_hourly.py --start 2026-10-01 --end 2026-11-03 --resume --sleep 0.5

    # 先看计划，不发明细请求
    python pull_ads_hourly.py --date 2026-11-03 --dry-run

    # 追加「投放×广告位 / 投放×广告产品」交叉粒度（请求数 × 投放数）
    python pull_ads_hourly.py --date 2026-11-03 --campaign-id xxx --with-cross

说明：
    · 所有写入走 INSERT OR REPLACE，可重复跑、可断点续跑
    · 小时数据赛狐只保留最近 60 天，且 T+1
    · 同时落一份「日」粒度事实（period_type='day', hour=-1），便于与小时对账
    · 注意口径：日列表接口的 impressions 可能略高于小时累计（归因时点差异），
      clicks/cost 一致；对账以 clicks/cost 为准
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta

from backend import ads_metrics_db as mdb
from backend.cookies import load_cookies
from backend.sellfox_client import SellfoxClient, _base_payload

PLACEMENT_NAMES = {
    "placementTop": "搜索结果顶部（首页）",
    "placementRestOfSearch": "搜索结果其余位置",
    "placementProductPage": "商品详情页",
    "offAmazon": "Off Amazon",
}
DEFAULT_SHOP_ID = "711811"
DEFAULT_AD_TYPE = "sp"


# --------------------------------------------------------------------------
# 通用
# --------------------------------------------------------------------------
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def num(v, cast=float, default=0):
    try:
        if v is None or v == "" or v == "-":
            return default
        return cast(float(v))
    except (TypeError, ValueError):
        return default


def hour_payload(*, campaign_id, group_id="", target_id="", day, model,
                 ad_type=DEFAULT_AD_TYPE, shop_id=DEFAULT_SHOP_ID) -> dict:
    """分时统一入参（赛狐 target 分时接口，dateModel 决定维度）"""
    return {
        "orderField": None, "orderType": None,
        "shopIdList": [int(shop_id) if str(shop_id).isdigit() else shop_id],
        "shopId": str(shop_id), "adType": ad_type, "type": ad_type,
        "targetType": "auto", "matchTypes": [], "productTargetTypes": [],
        "campaignIdList": [campaign_id] if campaign_id else [],
        "groupIdList": [group_id] if group_id else [],
        "servingStatusList": [], "statusList": ["enabled", "paused"],
        "adStrategyTypeList": [], "devIds": [],
        "startDate": day, "endDate": day,
        "analyzeStartDate": day, "analyzeEndDate": day,
        "dateModel": model,
        "weekList": ["1", "2", "3", "4", "5", "6", "7"],
        "isCompare": 0, "isHide": True, "fromSingle": True,
        "searchType": "exact", "searchField": "name", "searchValue": None,
        "wordRoot": "", "useAdvanced": False,
        "pageNo": 1, "pageSize": 200, "advanceFilter": {},
        "targetId": target_id or "",
    }


def metrics_rows(hour_list, n=24):
    """hour[{name, records[]}] → [{metric:value}]*n（每小时一个 dict）"""
    cols = {x.get("name"): [r.get("value") for r in (x.get("records") or [])] for x in (hour_list or [])}
    out = []
    for i in range(n):
        out.append({k: (v[i] if i < len(v) else None) for k, v in cols.items()})
    return out


def page_rows(client, api, payload, *, page_size=200, max_pages=100):
    """按页拉全量（兼容 {page:{rows,total}} 与 {rows:[]} 两种返回）"""
    out, page_no = [], 1
    while page_no <= max_pages:
        data = client.post(api, {**payload, "pageNo": page_no,
                                 "pageSize": page_size}).get("data") or {}
        node = data.get("page") if isinstance(data, dict) else None
        if not isinstance(node, dict):
            node = data if isinstance(data, dict) else {}
        batch = node.get("rows") or []
        out += batch
        total = node.get("total") or node.get("totalCount") or node.get("totalSize") or 0
        if not batch or len(batch) < page_size:
            break
        if total and len(out) >= int(total):
            break
        page_no += 1
    return out


def fact_row(*, period_type, day, hour, grain, shop_id, m, currency="",
             campaign_id="", ad_group_id="", ad_id="", target_id="",
             placement="", asin="", sku=""):
    """把一小时（或一天）的指标 dict 转成事实行"""
    return {
        "period_type": period_type, "stat_date": day, "hour": hour, "grain": grain,
        "shop_id": str(shop_id), "campaign_id": campaign_id or "", "ad_group_id": ad_group_id or "",
        "ad_id": ad_id or "", "target_id": target_id or "", "placement": placement or "",
        "asin": asin or "", "sku": sku or "", "currency": currency,
        "impressions": num(m.get("impressions"), int),
        "clicks": num(m.get("clicks"), int),
        "cost": num(m.get("adCost")),
        "orders": num(m.get("adOrderNum"), int),
        "sales": num(m.get("adSale")),
        "metrics_json": json.dumps(m, ensure_ascii=False),
        "source": "sellfox", "fetched_at": now_str(),
    }


def append_hour_facts(facts, hour_list, *, day, grain, shop_id, currency="", **dims):
    """{name,records[]} 结构的小时明细；无数据则不写（避免 24 行全 0 垃圾行）"""
    if not hour_list:
        return 0
    for i, m in enumerate(metrics_rows(hour_list)):
        facts.append(fact_row(period_type="hour", day=day, hour=i, grain=grain,
                              shop_id=shop_id, m=m, currency=currency, **dims))
    return len(hour_list)


def append_detail_facts(facts, detail, *, day, grain, shop_id, currency="", **dims):
    """periodReportList[].detail 结构的小时明细（已是逐小时 metrics 列表）"""
    if not detail:
        return 0
    for i, m in enumerate(detail):
        facts.append(fact_row(period_type="hour", day=day, hour=i, grain=grain,
                              shop_id=shop_id, m=m or {}, currency=currency, **dims))
    return len(detail)


# --------------------------------------------------------------------------
# 抓取
# --------------------------------------------------------------------------
def pull_campaigns(client, shop_id, ad_type, day):
    """当天有数据的活动列表（含日指标）"""
    p = {**_base_payload("campaign"),
         "shopIdList": [int(shop_id) if str(shop_id).isdigit() else shop_id],
         "type": ad_type, "startDate": day, "endDate": day,
         "orderField": "impressions", "orderType": "desc"}
    return page_rows(client, "campaign/getAllCampaignData", p, page_size=500)


def pull_groups(client, shop_id, ad_type, campaign_id, day):
    p = {**_base_payload("group"), "shopIdList": [int(shop_id) if str(shop_id).isdigit() else shop_id],
         "campaignIdList": [campaign_id], "adType": ad_type,
         "statusList": ["enabled", "paused"],
         "startDate": day, "endDate": day}
    return page_rows(client, "multiple/group/getAllGroupData", p)


def pull_targets(client, shop_id, ad_type, campaign_id, group_id, day):
    p = {**_base_payload("target"), "shopIdList": [int(shop_id) if str(shop_id).isdigit() else shop_id],
         "campaignIdList": [campaign_id], "groupIdList": [group_id] if group_id else [],
         "adType": ad_type, "targetType": "auto",
         "statusList": ["enabled", "paused"], "shopId": str(shop_id), "fromSingle": True,
         "searchType": "exact", "searchField": "name",
         "startDate": day, "endDate": day}
    return page_rows(client, "multiple/target/getAllTargetData", p)


def pull_products(client, shop_id, ad_type, campaign_id, day):
    p = {**_base_payload("product"), "shopIdList": [int(shop_id) if str(shop_id).isdigit() else shop_id],
         "campaignIdList": [campaign_id], "type": ad_type,
         "statusList": ["enabled", "paused"],
         "startDate": day, "endDate": day}
    return page_rows(client, "multiple/adProduct/getAdProductList", p, page_size=500)


def pull_placements(client, shop_id, ad_type, campaign_id, day):
    p = {"shopIdList": [int(shop_id) if str(shop_id).isdigit() else shop_id],
         "type": ad_type, "adType": ad_type, "campaignIdList": [campaign_id],
         "startDate": day, "endDate": day,
         "searchType": "exact", "searchField": "name"}
    return page_rows(client, "multiple/placement/getAllPlacementData", p, page_size=100)


def pull_hour(client, campaign_id, day, model, *, group_id="", target_id="",
              shop_id=DEFAULT_SHOP_ID, ad_type=DEFAULT_AD_TYPE):
    p = hour_payload(campaign_id=campaign_id, group_id=group_id, target_id=target_id,
                     day=day, model=model, ad_type=ad_type, shop_id=shop_id)
    return client.post("multiple/target/getAllTargetHourData", p).get("data") or {}


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def pull_one_campaign(conn, client, *, shop_id, ad_type, campaign_id, day,
                      with_cross=False, camp=None):
    facts = []

    # 1) 活动
    if camp is None:
        campaigns = pull_campaigns(client, shop_id, ad_type, day)
        camp = next((c for c in campaigns if str(c.get("campaignId")) == str(campaign_id)), None)
    if not camp:
        print(f"  ! 活动 {campaign_id} 当天无数据，跳过")
        return 0
    mdb.upsert_dim(conn, "dim_campaign", {
        "campaign_id": str(camp.get("campaignId")), "shop_id": str(shop_id), "ad_type": ad_type,
        "name": camp.get("name"), "state": camp.get("state"),
        "serving_status": camp.get("servingStatus"),
        "targeting_type": camp.get("campaignTargetingType") or camp.get("targetingType"),
        "daily_budget": num(camp.get("dailyBudget")),
        "bidding_strategy": camp.get("strategy"), "portfolio_name": camp.get("portfolioName"),
        "currency": camp.get("currency"), "start_date": camp.get("startDate"),
        "updated_at": now_str()})
    facts.append(fact_row(period_type="day", day=day, hour=-1, grain="campaign",
                          shop_id=shop_id, m=camp, currency=camp.get("currency") or "",
                          campaign_id=str(campaign_id)))

    # 2) 活动小时（HOURLY）
    d = pull_hour(client, campaign_id, day, "HOURLY", shop_id=shop_id, ad_type=ad_type)
    append_hour_facts(facts, d.get("hour"), day=day, grain="campaign", shop_id=shop_id,
                      currency=d.get("currency") or "", campaign_id=str(campaign_id))

    # 3) 广告组
    groups = pull_groups(client, shop_id, ad_type, campaign_id, day)
    for g in groups:
        gid = str(g.get("adGroupId"))
        mdb.upsert_dim(conn, "dim_ad_group", {
            "ad_group_id": gid, "campaign_id": str(campaign_id), "shop_id": str(shop_id),
            "ad_type": ad_type, "name": g.get("name"), "group_type": g.get("groupType"),
            "default_bid": num(g.get("defaultBid")), "state": g.get("state"),
            "updated_at": now_str()})
        facts.append(fact_row(period_type="day", day=day, hour=-1, grain="ad_group",
                              shop_id=shop_id, m=g, campaign_id=str(campaign_id), ad_group_id=gid))
        gh = pull_hour(client, campaign_id, day, "HOURLY", group_id=gid,
                       shop_id=shop_id, ad_type=ad_type)
        append_hour_facts(facts, gh.get("hour"), day=day, grain="ad_group", shop_id=shop_id,
                          currency=gh.get("currency") or "",
                          campaign_id=str(campaign_id), ad_group_id=gid)

    # 4) 投放
    targets = []
    for g in groups:
        targets += pull_targets(client, shop_id, ad_type, campaign_id, str(g.get("adGroupId")), day)
    for t in targets:
        tid = str(t.get("targetId"))
        mdb.upsert_dim(conn, "dim_target", {
            "target_id": tid, "ad_group_id": str(t.get("adGroupId") or ""),
            "campaign_id": str(campaign_id), "shop_id": str(shop_id), "ad_type": ad_type,
            "target_type": t.get("targetType"), "target_text": t.get("targetText") or t.get("keywordText"),
            "match_type": t.get("matchType"), "bid": num(t.get("bid")),
            "state": t.get("state"), "serving_status": t.get("servingStatus"),
            "updated_at": now_str()})
        facts.append(fact_row(period_type="day", day=day, hour=-1, grain="target",
                              shop_id=shop_id, m=t, campaign_id=str(campaign_id),
                              ad_group_id=str(t.get("adGroupId") or ""), target_id=tid))
        th = pull_hour(client, campaign_id, day, "HOURLY", target_id=tid,
                       shop_id=shop_id, ad_type=ad_type)
        append_hour_facts(facts, th.get("hour"), day=day, grain="target", shop_id=shop_id,
                          currency=th.get("currency") or "",
                          campaign_id=str(campaign_id),
                          ad_group_id=str(t.get("adGroupId") or ""), target_id=tid)

    # 5) 广告产品（ad = 组 × SKU），一次 HOURLY_ASIN 拿全部
    products = pull_products(client, shop_id, ad_type, campaign_id, day)
    ad_index = {}
    for p in products:
        ad_id = str(p.get("adId") or "")
        gid = str(p.get("adGroupId") or "")
        asin, sku = str(p.get("asin") or ""), str(p.get("sku") or "")
        ad_index[asin] = (ad_id, gid, asin, sku)
        if ad_id:
            mdb.upsert_dim(conn, "dim_ad", {
                "ad_id": ad_id, "ad_group_id": gid, "campaign_id": str(campaign_id),
                "shop_id": str(shop_id), "ad_type": ad_type, "asin": asin, "sku": sku,
                "title": p.get("title"), "state": p.get("state"), "updated_at": now_str()})
        facts.append(fact_row(period_type="day", day=day, hour=-1, grain="ad",
                              shop_id=shop_id, m=p, campaign_id=str(campaign_id),
                              ad_group_id=gid, ad_id=ad_id, asin=asin, sku=sku))

    adh = pull_hour(client, campaign_id, day, "HOURLY_ASIN", shop_id=shop_id, ad_type=ad_type)
    for row in adh.get("periodReportList") or []:
        asin = str(row.get("title") or "")
        ad_id, gid, asin, sku = ad_index.get(asin, ("", "", asin, ""))
        append_detail_facts(facts, row.get("detail"), day=day, grain="ad", shop_id=shop_id,
                            currency=adh.get("currency") or "",
                            campaign_id=str(campaign_id), ad_group_id=gid,
                            ad_id=ad_id, asin=asin, sku=sku)

    # 6) 广告位，一次 HOURLY_PLACEMENT 拿全部
    placements = pull_placements(client, shop_id, ad_type, campaign_id, day)
    for p in placements:
        mdb.upsert_dim(conn, "dim_placement", {
            "placement": str(p.get("predicate") or ""),
            "name": PLACEMENT_NAMES.get(str(p.get("predicate") or ""), p.get("predicate")),
            "updated_at": now_str()})
        facts.append(fact_row(period_type="day", day=day, hour=-1, grain="placement",
                              shop_id=shop_id, m=p, campaign_id=str(campaign_id),
                              placement=str(p.get("predicate") or "")))

    plh = pull_hour(client, campaign_id, day, "HOURLY_PLACEMENT", shop_id=shop_id, ad_type=ad_type)
    for row in plh.get("periodReportList") or []:
        pred = str(row.get("predicate") or "")
        append_detail_facts(facts, row.get("detail"), day=day, grain="placement",
                            shop_id=shop_id, currency=plh.get("currency") or "",
                            campaign_id=str(campaign_id), placement=pred)

    # 7) 可选：投放 × 广告位 / 投放 × 广告产品
    if with_cross:
        for t in targets:
            tid = str(t.get("targetId"))
            gid = str(t.get("adGroupId") or "")
            x = pull_hour(client, campaign_id, day, "HOURLY_PLACEMENT", target_id=tid,
                          shop_id=shop_id, ad_type=ad_type)
            for row in x.get("periodReportList") or []:
                append_detail_facts(facts, row.get("detail"), day=day, grain="target_placement",
                                    shop_id=shop_id, currency=x.get("currency") or "",
                                    campaign_id=str(campaign_id), ad_group_id=gid,
                                    target_id=tid, placement=str(row.get("predicate") or ""))
            y = pull_hour(client, campaign_id, day, "HOURLY_ASIN", target_id=tid,
                          shop_id=shop_id, ad_type=ad_type)
            for row in y.get("periodReportList") or []:
                asin = str(row.get("title") or "")
                ad_id, agid, asin, sku = ad_index.get(asin, ("", gid, asin, ""))
                append_detail_facts(facts, row.get("detail"), day=day, grain="target_ad",
                                    shop_id=shop_id, currency=y.get("currency") or "",
                                    campaign_id=str(campaign_id), ad_group_id=agid,
                                    target_id=tid, ad_id=ad_id, asin=asin, sku=sku)

    n = mdb.upsert_facts(conn, facts)
    conn.commit()
    return n


def already_done(conn, day, cid, with_cross):
    """该活动×该天是否已成功落库（含交叉粒度口径区分）"""
    grain = "all_cross" if with_cross else "all"
    return conn.execute(
        "SELECT 1 FROM etl_pull_log WHERE period_type='hour' AND stat_date=?"
        " AND scope=? AND grain=? AND status='success' LIMIT 1",
        (day, f"campaign:{cid}", grain)).fetchone() is not None


def run(day, *, shop_id=DEFAULT_SHOP_ID, ad_type=DEFAULT_AD_TYPE,
        campaign_id="", with_cross=False, resume=False, sleep_sec=0.5,
        dry_run=False, include_zero=False, limit=0):
    cookies = load_cookies()
    if not cookies:
        sys.exit("未找到登录 Cookie，请先同步赛狐登录态")
    client = SellfoxClient(cookies)
    conn = mdb.get_conn()
    mdb.init_db(conn)

    campaigns = pull_campaigns(client, shop_id, ad_type, day)
    camp_index = {str(c.get("campaignId")): c for c in campaigns}
    if campaign_id and campaign_id not in camp_index:
        print(f"[{day}] 活动 {campaign_id} 当天无数据，跳过")
        conn.close()
        return 0

    ids = [campaign_id] if campaign_id else list(camp_index)
    empty = 0
    if not include_zero and not campaign_id:
        kept = []
        for cid in ids:
            c = camp_index.get(cid) or {}
            if num(c.get("impressions"), int) or num(c.get("adCost")):
                kept.append(cid)
            else:
                empty += 1
        ids = kept

    if limit and len(ids) > limit:
        ids = ids[:limit]

    print(f"[{day}] 待处理活动 {len(ids)} 个"
          f"（全店 {len(camp_index)}，跳过零曝光 {empty}；交叉粒度={with_cross}, resume={resume}）")
    if dry_run:
        for cid in ids[:20]:
            c = camp_index.get(cid) or {}
            print(f"  - {cid} {c.get('name') or ''} imp={num(c.get('impressions'), int)}"
                  f" cost={num(c.get('adCost'))}")
        if len(ids) > 20:
            print(f"  ... 其余 {len(ids) - 20} 个略")
        conn.close()
        return 0

    grain = "all_cross" if with_cross else "all"
    total = ok = fail = skip = 0
    for idx, cid in enumerate(ids, 1):
        if resume and already_done(conn, day, cid, with_cross):
            skip += 1
            continue
        log_id = mdb.log_pull(conn, period_type="hour", stat_date=day,
                              scope=f"campaign:{cid}", grain=grain, status="running")
        try:
            n = pull_one_campaign(conn, client, shop_id=shop_id, ad_type=ad_type,
                                  campaign_id=cid, day=day, with_cross=with_cross,
                                  camp=camp_index.get(cid))
            total += n
            ok += 1
            conn.execute("UPDATE etl_pull_log SET status='success', rows_written=?, finished_at=?"
                         " WHERE id=?", (n, now_str(), log_id))
            conn.commit()
            print(f"  [{idx}/{len(ids)}] ✓ {cid}: {n} 行")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            fail += 1
            conn.execute("UPDATE etl_pull_log SET status='failed', message=?, finished_at=?"
                         " WHERE id=?", (str(exc)[:500], now_str(), log_id))
            conn.commit()
            print(f"  [{idx}/{len(ids)}] ✗ {cid}: {exc}")
        if sleep_sec:
            time.sleep(sleep_sec)

    rows = mdb.summarize(conn, day)
    print(f"\n[{day}] 汇总（成功 {ok} / 失败 {fail} / 跳过 {skip}）：")
    for r in rows:
        print(f"  {r['grain']:<18} {r['period_type']:<5} rows={r['rows']:<5} "
              f"imp={r['impressions']} clicks={r['clicks']} cost={r['cost']}")
    print(f"总写入 {total} 行 → {mdb.DB_PATH}")
    conn.close()
    return total


def main():
    ap = argparse.ArgumentParser(description="赛狐广告分时数据落库")
    ap.add_argument("--date", help="yyyy-mm-dd（单天）")
    ap.add_argument("--start", help="起始日期 yyyy-mm-dd（与 --end 配合）")
    ap.add_argument("--end", help="结束日期 yyyy-mm-dd")
    ap.add_argument("--shop-id", default=DEFAULT_SHOP_ID)
    ap.add_argument("--ad-type", default=DEFAULT_AD_TYPE, choices=["sp", "sb", "sd"])
    ap.add_argument("--campaign-id", default="")
    ap.add_argument("--with-cross", action="store_true",
                    help="额外拉 投放×广告位 / 投放×广告产品（请求数 = 投放数 × 2）")
    ap.add_argument("--resume", action="store_true",
                    help="跳过 etl_pull_log 中已成功的 日期×活动")
    ap.add_argument("--sleep", type=float, default=0.5, help="每个活动之间的间隔秒数（防风控）")
    ap.add_argument("--dry-run", action="store_true", help="只列活动，不请求明细")
    ap.add_argument("--include-zero", action="store_true",
                    help="全店模式下拉取零曝光活动（默认跳过，可省 90%%+ 请求）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个活动（试跑用）")
    a = ap.parse_args()

    if a.date:
        days = [a.date]
    elif a.start and a.end:
        d0 = datetime.strptime(a.start, "%Y-%m-%d")
        d1 = datetime.strptime(a.end, "%Y-%m-%d")
        days = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d") for i in range((d1 - d0).days + 1)]
    else:
        ap.error("必须指定 --date 或 --start/--end")

    for day in days:
        run(day, shop_id=a.shop_id, ad_type=a.ad_type,
            campaign_id=a.campaign_id, with_cross=a.with_cross,
            resume=a.resume, sleep_sec=a.sleep, dry_run=a.dry_run,
            include_zero=a.include_zero, limit=a.limit)


if __name__ == "__main__":
    main()
