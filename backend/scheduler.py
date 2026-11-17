# -*- coding: utf-8 -*-
"""每日定时采集。

规则：
- 每天本地时间 02:00 触发一次「采集最新」。
- 服务启动时只计算下一次 02:00，不补跑当天已经错过的任务。
- 触发失败、任务正在运行、执行异常：只记录，不重试、不补偿。
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

_LOCK = threading.Lock()
_STOP = threading.Event()
_STARTED = False
_NEXT_RUN: Optional[datetime] = None
_LAST_TRIGGER: str = ""
_LAST_RESULT: str = ""


def _log(message: str) -> None:
    print(f"[scheduler] {message}", flush=True)


def next_run_at(now: Optional[datetime] = None) -> datetime:
    """严格下一次 02:00；如果已经过今天 02:00，则返回明天 02:00。"""
    now = now or datetime.now()
    target = now.replace(hour=2, minute=0, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


def _loop() -> None:
    global _NEXT_RUN, _LAST_TRIGGER, _LAST_RESULT
    while not _STOP.is_set():
        target = next_run_at()
        with _LOCK:
            _NEXT_RUN = target
        wait_seconds = max(0.0, (target - datetime.now()).total_seconds())
        _log(f"下一次采集：{target:%Y-%m-%d %H:%M:%S}")
        if _STOP.wait(wait_seconds):
            return

        now = datetime.now()
        # 进程休眠/卡顿导致错过触发窗口时，不补跑。
        if now > target + timedelta(minutes=5):
            _LAST_TRIGGER = now.strftime("%Y-%m-%d %H:%M:%S")
            _LAST_RESULT = f"missed：已错过 {target:%H:%M}，不补偿"
            _log(_LAST_RESULT)
            continue

        _LAST_TRIGGER = now.strftime("%Y-%m-%d %H:%M:%S")
        _log("触发每日采集最新")
        try:
            from backend.daily_probe_api import latest_start
            latest_start()
            _LAST_RESULT = "started"
            _log("每日采集最新已启动")
        except Exception as exc:  # noqa: BLE001
            # 不重试、不补偿。
            _LAST_RESULT = f"failed：{type(exc).__name__}: {str(exc)[:200]}"
            _log(f"每日采集启动失败（不补偿）：{_LAST_RESULT}")


def start_scheduler() -> None:
    """在 FastAPI 启动时调用；重复调用不会重复启动线程。"""
    global _STARTED
    if os.environ.get("LATEST_CRON_DISABLED", "").strip() in ("1", "true", "yes"):
        _log("LATEST_CRON_DISABLED=1，定时采集未启动")
        return
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
        _STOP.clear()
    threading.Thread(target=_loop, name="daily-latest-scheduler", daemon=True).start()


def scheduler_status() -> Dict[str, Any]:
    with _LOCK:
        return {
            "enabled": _STARTED,
            "next_run": _NEXT_RUN.strftime("%Y-%m-%d %H:%M:%S") if _NEXT_RUN else "",
            "last_trigger": _LAST_TRIGGER,
            "last_result": _LAST_RESULT,
            "timezone": datetime.now().astimezone().tzname() or "",
        }
