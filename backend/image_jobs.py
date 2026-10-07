# -*- coding: utf-8 -*-
"""父 ASIN 附图抓取 —— 后台任务

背景: 一个父体可能有几十个子 ASIN, 每个都要真实打开一次亚马逊商品页
(受全局最小间隔节拍约束, 单页约 3~10 秒), 同步 HTTP 请求会超时。
因此放到后台线程逐个抓取, 前端轮询进度, 抓完再刷新。

覆盖策略: 抓取「父体自身 + 全部子体」, 逐个记录到 amz_image_crawls,
因此重复进入页面不会重复抓取(除非显式 refresh)。
"""
import threading
from typing import Any, Dict, List

import config
from backend import amazon_product, database as db

_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}


def _key(asin: str, domain: str) -> str:
    return f"{asin}|{domain}"


def _public(job: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in job.items() if k not in ("pending",)}


def get_status(asin: str, domain: str = "") -> Dict[str, Any]:
    dm = domain or config.AMAZON_DOMAIN
    with _LOCK:
        job = JOBS.get(_key(asin, dm))
        if not job:
            return {"running": False, "total": 0, "done": 0, "current": "",
                    "ok": 0, "failed": 0, "images": 0, "started": False}
        return _public(job)


def start(asin: str, domain: str = "", refresh: bool = False,
          max_children: int = 0) -> Dict[str, Any]:
    """启动后台抓取; 已在跑则直接返回当前进度

    max_children: 0/负数 = 全部子体; 正数 = 最多抓这么多个(含父体自身)。
    refresh=True 时即使已抓过也重新抓。
    """
    dm = domain or config.AMAZON_DOMAIN
    k = _key(asin, dm)
    with _LOCK:
        cur = JOBS.get(k)
        if cur and cur.get("running"):
            return {"started": False, "reason": "running", **_public(cur)}

    prof = db.get_parent_profile(asin, dm)
    parent = prof["parent_asin"]
    if refresh:
        targets: List[str] = [parent] + [c for c in prof["child_asins"] if c != parent]
    else:
        # 只抓未抓过的 (父体 + 子体)
        targets = [a for a in prof.get("pending_asins") or []]
        if parent not in (prof.get("crawled") or []) and parent not in targets:
            targets.insert(0, parent)
    if max_children and max_children > 0:
        targets = targets[:max_children]

    with _LOCK:
        JOBS[k] = {"running": bool(targets), "total": len(targets), "done": 0,
                   "current": "", "ok": 0, "failed": 0,
                   "images": prof.get("image_count") or 0,
                   "parent_asin": parent, "refresh": bool(refresh),
                   "error": "", "started": True}
    if targets:
        threading.Thread(target=_work, args=(k, parent, targets, dm, bool(refresh)),
                         daemon=True).start()
    return {"started": True, "total": len(targets), "parent_asin": parent,
            "targets": targets[:20]}


def _work(job_key: str, parent: str, targets: List[str], dm: str, refresh: bool) -> None:
    for i, asin in enumerate(targets, 1):
        with _LOCK:
            job = JOBS.get(job_key)
            if not job:
                return
            job["current"] = asin
        try:
            amazon_product.fetch_images(asin, domain=dm, refresh=refresh)
            with _LOCK:
                JOBS[job_key]["ok"] += 1
        except Exception as e:      # noqa: BLE001
            with _LOCK:
                JOBS[job_key]["failed"] += 1
                JOBS[job_key]["error"] = f"{asin}: {str(e)[:140]}"
        with _LOCK:
            JOBS[job_key]["done"] = i
            try:
                JOBS[job_key]["images"] = db.get_parent_images(parent, dm)["count"]
            except Exception:       # noqa: BLE001
                pass
    with _LOCK:
        job = JOBS.get(job_key)
        if job:
            job["running"] = False
            job["current"] = ""
            try:
                job["images"] = db.get_parent_images(parent, dm)["count"]
            except Exception:       # noqa: BLE001
                pass
