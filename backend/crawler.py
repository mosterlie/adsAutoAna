# -*- coding: utf-8 -*-
"""采集编排: 店铺/广告组合 → 各页签(全维度)分页拉取 → 落库

需求对齐:
  - 时间范围: 2026-08-01 ~ 今天
  - 其他筛选: 全状态 (status/servingStatus 全部置空)
  - 覆盖 广告管理 下全部 9 个子菜单
  - 采集走 requests 直连接口 (非浏览器 DOM 解析)
"""
import threading
from datetime import date
from typing import Any, Dict, List, Optional

import config
from backend import database as db
from backend.cookies import load_cookies, parse_cookie_string
from backend.sellfox_client import TAB_DEFS, TAB_ORDER, SellfoxClient

# 运行状态 (供前端轮询)
STATE: Dict[str, Any] = {"running": False, "run_id": None, "progress": [], "error": None}
_STATE_LOCK = threading.Lock()


def _log(line: str) -> None:
    with _STATE_LOCK:
        STATE["progress"].append(line)
        if len(STATE["progress"]) > 500:
            STATE["progress"] = STATE["progress"][-300:]
    print(line, flush=True)


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def run_crawl(start_date: Optional[str] = None, end_date: Optional[str] = None,
              tabs: Optional[List[str]] = None, shop_ids: Optional[List[int]] = None,
              cookie_string: Optional[str] = None, run_id: Optional[int] = None) -> int:
    """同步执行一次采集, 返回 run_id (供后台线程调用)

    健壮性: 从 create_run / init_db 到采集结束的全部逻辑都包在 try 内,
    并用 finally 兜底把 STATE["running"] 复位 —— 无论 Cookie 缺失、接口异常
    还是 DB 初始化失败, 都不会再把状态卡在 running=True (旧实现会在取 Cookie
    失败时于 try 之前 raise, 导致任务永久占位、后续采集全部被拒)。
    """
    start = start_date or config.DEFAULT_START_DATE
    end = end_date or config.DEFAULT_END_DATE or today_str()
    tabs = [t for t in (tabs or TAB_ORDER) if t in TAB_DEFS]

    stats: Dict[str, Any] = {}
    try:
        db.init_db()
        if run_id is None:
            run_id = db.create_run(start, end)
        with _STATE_LOCK:
            STATE.update({"running": True, "run_id": run_id, "progress": [], "error": None})

        cookies = parse_cookie_string(cookie_string) if cookie_string else load_cookies()
        if not cookies:
            raise RuntimeError("未找到登录 Cookie, 请先在「同步登录态」中从调试 Chrome 同步, 或手动粘贴 Cookie")

        client = SellfoxClient(cookies, verbose_logger=_log)
        _log(f"▶ 开始采集: 时间范围 {start} ~ {end}, 页签 {len(tabs)} 个")

        # 1) 店铺 + 广告组合
        sp = client.get_shops(shop_ids)
        rows = sp.get("rows") or []
        db.save_shops(rows)
        db.save_portfolios(rows)
        all_shop_ids = [r["shopId"] for r in rows if r.get("shopId")]
        use_shops = list(shop_ids) if shop_ids else sorted(set(all_shop_ids))
        if not use_shops:
            raise RuntimeError("未取到任何店铺, 请检查登录态或店铺权限")
        _log(f"  店铺 {use_shops} / 广告组合 {len(rows)} 条")

        # 2) 逐页签采集 (按店铺 × 维度)
        for tab in tabs:
            label = TAB_DEFS[tab]["label"]
            tab_total = 0
            for sid in use_shops:
                for scope in TAB_DEFS[tab]["scopes"]:
                    fetched_ok = False
                    try:
                        records = client.fetch_list(tab, [sid], start, end, scope)
                        fetched_ok = True
                        # 一次采集=一个快照: 先删掉该维度下其它时间区间的旧数据,
                        # 否则跨天采集(区间变化)会让同一条记录再插一份, 指标翻倍
                        purged = db.purge_stale_ranges(tab, scope, sid, start, end)
                        n = db.upsert_records(tab, scope, records, start, end)
                        tab_total += n
                        if purged:
                            _log(f"      · [{label}] {scope or {}} 清理旧区间 {purged} 条")
                    except Exception as e:      # noqa: BLE001
                        _log(f"    ⚠ [{label}] shop={sid} scope={scope}: {str(e)[:120]}")
                    # 汇总(可选)
                    try:
                        agg = client.fetch_aggregate(tab, [sid], start, end, scope)
                        if agg is not None:
                            db.save_aggregate(tab, scope, sid, start, end, agg)
                    except Exception:
                        pass
                    _ = fetched_ok
            stats[tab] = tab_total
            _log(f"  ✔ {label}: 落库 {tab_total} 条")

        # 3) 收尾: 库里只保留本次采集的快照区间
        #    (防止历史/失败维度残留的旧区间数据混进统计口径, 导致指标翻倍)
        leftover = db.purge_all_other_ranges(start, end)
        if leftover:
            _log(f"  🧹 清理其它区间的历史快照 {leftover} 条")

        db.finish_run(run_id, "success", "采集完成", stats)
        _log(f"✅ 采集完成: {stats}")
    except Exception as e:      # noqa: BLE001
        if run_id is not None:
            try:
                db.finish_run(run_id, "failed", str(e), stats)
            except Exception:       # noqa: BLE001  DB 不可用时不掩盖原始错误
                pass
        with _STATE_LOCK:
            STATE["error"] = str(e)
        _log(f"❌ 采集失败: {e}")
        raise
    finally:
        # 无论如何都复位 running, 避免任务永久占位
        with _STATE_LOCK:
            STATE["running"] = False
    return run_id


def start_crawl_async(**kwargs) -> int:
    """后台线程启动采集, 立即返回本次 run_id"""
    if STATE.get("running"):
        raise RuntimeError("已有采集任务在运行中")
    db.init_db()
    start = kwargs.get("start_date") or config.DEFAULT_START_DATE
    end = kwargs.get("end_date") or config.DEFAULT_END_DATE or today_str()
    # 立即占位阻止并发; run_crawl 内部会复用该 run_id
    run_id = db.create_run(start, end)
    with _STATE_LOCK:
        STATE.update({"running": True, "run_id": run_id, "progress": [], "error": None})

    def _work():
        try:
            run_crawl(run_id=run_id, **kwargs)
        except Exception:
            pass

    threading.Thread(target=_work, daemon=True).start()
    return run_id


def get_state() -> Dict[str, Any]:
    with _STATE_LOCK:
        return {"running": STATE["running"], "run_id": STATE["run_id"],
                "error": STATE["error"], "progress": list(STATE["progress"])}
