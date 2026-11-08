# -*- coding: utf-8 -*-
"""批量改名执行器（后台运行，逐条落盘进度）

用法:
    python3 -m backend.rename_batch --plan data/rename_plan_29811114365267.json --day 2026-11-07

行为:
    * 逐条执行 apply_one（内含：改活动名 → 读回校验 → 改组名 → 读回校验）
    * 单条失败自动回滚该条，继续下一条（--stop-on-error 可改为中止）
    * 每完成一条立即写 progress 文件，可随时查看
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from backend import rename_tool as rt

PROGRESS = "data/rename_progress.json"


def _save(state: Dict[str, Any]) -> None:
    tmp = PROGRESS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, PROGRESS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--day", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--stop-on-error", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 条（调试用）")
    ap.add_argument("--replan", action="store_true",
                    help="忽略计划文件里的新名，按当前标识重新计算")
    ap.add_argument("--workers", type=int, default=3, help="并发数(默认 3)")
    args = ap.parse_args()

    plan: List[Dict[str, Any]] = json.load(open(args.plan, encoding="utf-8"))
    todo = [p for p in plan if not p.get("skip")]
    if args.limit:
        todo = todo[:args.limit]

    state: Dict[str, Any] = {
        "startedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "finishedAt": "", "total": len(todo), "done": 0,
        "ok": 0, "failed": 0, "skipped": len(plan) - len(todo),
        "current": "", "results": [], "errors": [],
    }
    _save(state)
    print(f"[开始] 共 {len(todo)} 个活动（跳过 {state['skipped']} 个已符合规则）", flush=True)

    lock = __import__("threading").Lock()

    def work(item: Dict[str, Any]) -> Dict[str, Any]:
        cid = item["campaignId"]
        try:
            plan_now = rt.plan_one(cid, args.day) if args.replan else item
            rt.apply_one(plan_now, args.day)
            return {"campaignId": cid, "ok": True,
                    "campaignName": plan_now.get("newCampaignName"),
                    "groups": [g.get("newName") for g in plan_now.get("groups") or []]}
        except Exception as e:              # noqa: BLE001
            return {"campaignId": cid, "ok": False, "oldName": item.get("campaignName"),
                    "error": str(e)[:300]}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(work, it): it for it in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            res = fut.result()
            with lock:
                state["done"] = i
                state["current"] = res["campaignId"]
                if res["ok"]:
                    state["ok"] += 1
                    state["results"].append(res)
                    print(f"[{i}/{len(todo)}] ✓ {res['campaignId']} → {res['campaignName']}",
                          flush=True)
                else:
                    state["failed"] += 1
                    state["errors"].append({"campaignId": res["campaignId"],
                                            "oldName": res.get("oldName"),
                                            "error": res["error"]})
                    state["results"].append({"campaignId": res["campaignId"], "ok": False,
                                             "error": res["error"]})
                    print(f"[{i}/{len(todo)}] ✗ {res['campaignId']} 失败（已回滚）: "
                          f"{res['error'][:140]}", flush=True)
                _save(state)

    state["current"] = ""
    state["finishedAt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _save(state)
    print(f"[完成] 成功 {state['ok']} / 失败 {state['failed']} / 共 {len(todo)}", flush=True)


if __name__ == "__main__":
    main()
