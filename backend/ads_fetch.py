# -*- coding: utf-8 -*-
"""广告指标统一获取入口（三体系一并取齐）

一次调用完成「指定广告产品 × 指定日期」的全部取数：

    1. 活动 / 广告组 / 投放 / 广告产品 的小时数据      → pull_ads_hourly
    2. 产品三体系分时：广告总量 / 投放 / 广告位         → collect_campaign_dims
    3. 产品编码与广告总量事实补齐                      → build_ads_dim_fact

页面「获取数据」按钮走 POST /api/ads-fact/fetch（后台线程 + 轮询进度），
CLI 一行等效：

    python3 -m backend.ads_fetch --dates 2026-11-02,2026-11-04 --sku abc027-直径31cm-37cm

注意：广告总量以「小时维度指标分析」弹窗(HOURLY)为准，所以第 3 步只用
OR IGNORE 补缺，不覆盖第 2 步写入的日汇总（两者口径会有小时和 ≠ 日汇总的差异）。
"""
from __future__ import annotations

import argparse
import threading
import time
import traceback
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from backend import ads_fact_db as fdb

SHOP_ID = "711811"
AD_TYPE = "sp"

_LOCK = threading.Lock()
_TASKS: Dict[str, Dict[str, Any]] = {}
MAX_TASKS = 20


# --------------------------------------------------------------------------
# 采集范围定位
# --------------------------------------------------------------------------
def resolve_targets(product_code: Optional[int] = None, sku: str = "") -> Dict[str, Any]:
    """product_code / sku → {skus, campaign_ids, ad_ids_by_campaign, sku}"""
    conn = fdb.get_conn()
    try:
        if product_code:
            rows = conn.execute(
                "SELECT * FROM dim_product WHERE product_code = ?", (product_code,)).fetchall()
        elif sku:
            rows = conn.execute(
                "SELECT * FROM dim_product WHERE lower(sku) LIKE ?",
                (f"%{sku.lower()}%",)).fetchall()
        else:
            raise ValueError("必须指定 product_code 或 sku")
    finally:
        conn.close()

    if not rows:
        raise ValueError(f"库中未找到该广告产品（product_code={product_code} sku={sku}）")

    skus: List[str] = []
    camps: List[str] = []
    ads_by_camp: Dict[str, List[str]] = {}
    for r in rows:
        s = (r["sku"] or "").strip()
        if s and s not in skus:
            skus.append(s)
        cid = (r["campaign_id"] or "").strip()
        if cid and cid not in camps:
            camps.append(cid)
        ad_id = (r["ad_id"] or "").strip()
        if cid and ad_id:
            ads_by_camp.setdefault(cid, [])
            if ad_id not in ads_by_camp[cid]:
                ads_by_camp[cid].append(ad_id)
    return {"skus": skus, "campaign_ids": camps, "ads_by_campaign": ads_by_camp,
            "product_codes": [int(r["product_code"]) for r in rows]}


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def fetch_metrics(dates: List[str], *, product_code: Optional[int] = None, sku: str = "",
                  with_cross: bool = False, shop_id: str = SHOP_ID, ad_type: str = AD_TYPE,
                  on_step: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """统一取数：日期 × 广告产品（同步执行，供 API 线程或 CLI 调用）"""
    import pull_ads_hourly as hourly
    import collect_campaign_dims as dims
    import build_ads_dim_fact as build

    def step(msg: str) -> None:
        print(f"[ads-fetch] {msg}", flush=True)
        if on_step:
            on_step(msg)

    days = sorted({d.strip() for d in dates if d and d.strip()})
    if not days:
        raise ValueError("缺少日期")

    target = resolve_targets(product_code, sku)
    skus, camps = target["skus"], target["campaign_ids"]
    step(f"广告产品 SKU={skus} 活动={len(camps)} 个 日期={len(days)} 天")

    hourly_rows = 0
    for day in days:
        step(f"{day} 采集活动/广告组/投放/广告产品小时数据…")
        hourly_rows += hourly.run(day, shop_id=shop_id, ad_type=ad_type, campaign_ids=camps,
                                  only_skus=skus or None, with_cross=with_cross,
                                  sleep_sec=0.3) or 0

        for cid in camps:
            ads = target["ads_by_campaign"].get(cid) or None
            step(f"{day} 采集产品三体系（活动 {cid}）…")
            dims.run(day, shop_id=shop_id, ad_type=ad_type, campaign_id=cid,
                     skus=skus or None, ad_ids=ads, sleep_sec=0.3)

    step("同步产品维度与广告总量事实…")
    conn = fdb.get_conn()
    try:
        build.sync_products(conn)
        build.sync_total(conn, overwrite=False)   # 不覆盖三体系写入的日汇总
        conn.commit()
        rows = _count_rows(conn, days)
    finally:
        conn.close()

    step("完成")
    return {
        "dates": days,
        "sku": skus,
        "campaign_ids": camps,
        "product_codes": target["product_codes"],
        "rows": rows,
        "hourly_rows": hourly_rows,
    }


def _count_rows(conn: Any, days: List[str]) -> int:
    marks = ",".join("?" * len(days))
    return sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE stat_date IN ({marks})",
                            days).fetchone()[0] for t in fdb.TABLES.values())


# --------------------------------------------------------------------------
# 任务（给页面轮询用）
# --------------------------------------------------------------------------
def start_fetch(dates: List[str], *, product_code: Optional[int] = None, sku: str = "",
                with_cross: bool = False) -> str:
    tid = uuid.uuid4().hex[:12]
    task = {"id": tid, "status": "running", "message": "准备中…", "result": None,
            "started": datetime.now().strftime("%H:%M:%S"), "finished": ""}
    with _LOCK:
        _TASKS[tid] = task
        for old in list(_TASKS)[:-MAX_TASKS]:
            _TASKS.pop(old, None)

    def _run() -> None:
        t0 = time.time()
        try:
            res = fetch_metrics(dates, product_code=product_code, sku=sku,
                                with_cross=with_cross,
                                on_step=lambda m: task.update(message=m))
            task.update(status="success", result=res,
                        message=f"完成：三体系 {res['rows']} 行 / 活动明细 "
                                f"{res['hourly_rows']} 行（{time.time() - t0:.1f}s）",
                        finished=datetime.now().strftime("%H:%M:%S"))
        except SystemExit as exc:                       # 脚本里走 sys.exit 的分支
            task.update(status="failed", finished=datetime.now().strftime("%H:%M:%S"),
                        message=f"{exc or '采集脚本退出'}")
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            task.update(status="failed", finished=datetime.now().strftime("%H:%M:%S"),
                        message=str(exc)[:300])

    threading.Thread(target=_run, name=f"ads-fetch-{tid}", daemon=True).start()
    return tid


def get_task(tid: str) -> Optional[Dict[str, Any]]:
    return _TASKS.get(tid)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="广告指标统一获取（活动小时 + 产品三体系）")
    ap.add_argument("--date", action="append", default=[], help="yyyy-mm-dd（可重复）")
    ap.add_argument("--dates", default="", help="yyyy-mm-dd,yyyy-mm-dd")
    ap.add_argument("--sku", default="", help="按 SKU 模糊匹配广告产品")
    ap.add_argument("--product-code", type=int, default=0, help="按 product_code 指定广告产品")
    ap.add_argument("--with-cross", action="store_true", help="额外拉 投放×广告位 / 投放×产品")
    ap.add_argument("--shop-id", default=SHOP_ID)
    ap.add_argument("--ad-type", default=AD_TYPE, choices=["sp", "sb", "sd"])
    a = ap.parse_args()

    days = list(a.date) + [d.strip() for d in a.dates.split(",") if d.strip()]
    res = fetch_metrics(days, product_code=a.product_code or None, sku=a.sku,
                        with_cross=a.with_cross, shop_id=a.shop_id, ad_type=a.ad_type)
    print("\n结果：")
    for k, v in res.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
