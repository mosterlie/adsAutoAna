# -*- coding: utf-8 -*-
"""调用 browser_toolkit 的履约同步服务。

独立进程执行，避免两个项目的 config / server 包和数据库连接互相污染。
默认同步妙手最近 30 天订单，并写入 browser_toolkit 的履约表。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

DEFAULT_DAYS = 30
DEFAULT_BACKFILL_LIMIT = 20
MARKER = "__FULFILLMENT_RESULT__"


def toolkit_dir() -> Path:
    # adsAutoAna/backend -> adsAutoAna -> mypro -> browser_toolkit
    return Path(__file__).resolve().parents[2] / "browser_toolkit"


def sync_fulfillment(days: int = DEFAULT_DAYS,
                     backfill_limit: int = DEFAULT_BACKFILL_LIMIT,
                     timeout: int = 1800) -> Dict[str, Any]:
    root = toolkit_dir()
    if not root.is_dir():
        raise RuntimeError(f"browser_toolkit 不存在: {root}")
    script = (
        "import json\n"
        "from server.services.fulfillment_service import FulfillmentService\n"
        f"r = FulfillmentService.sync_from_miaoshou(days={int(days)}, pull=True, "
        "username='adsAutoAna-daily')\n"
        "try:\n"
        "    from server.services.order_service import OrderService\n"
        "    before = int(r.get('labeled') or 0)\n"
        "    r['product_labels'] = OrderService.tag_missing_product_labels(limit=300)\n"
        "    r['labeled'] = before + int((r['product_labels'] or {}).get('tagged') or 0)\n"
        "    if r['labeled']:\n"
        "        r.pop('label_error', None)\n"
        "except Exception as exc:\n"
        "    r['label_error'] = str(exc)[:300]\n"
        "try:\n"
        "    from server.services.dxm_order_service import DxmOrderService\n"
        f"    r['tracking_backfill'] = DxmOrderService.backfill_missing_tracking("
        f"limit={int(backfill_limit)})\n"
        "except Exception as exc:\n"
        "    r['tracking_backfill'] = {'checked': 0, 'filled': 0, 'not_found': 0, "
        "'failed': 0, 'samples': [], 'error': str(exc)[:300]}\n"
        f"print({MARKER!r} + json.dumps(r, ensure_ascii=False))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
    env["DB_PATH"] = str(root / "data" / "products.db")
    env.setdefault("PYTHONUNBUFFERED", "1")
    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(root), env=env, capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"履约同步超时（>{timeout}s）") from exc
    output = (proc.stdout or "").strip()
    marker_at = output.rfind(MARKER)
    if marker_at < 0:
        detail = (proc.stderr or output or "无输出")[-500:]
        raise RuntimeError(f"履约同步失败: {detail}")
    payload = output[marker_at + len(MARKER):].strip().splitlines()[0]
    try:
        data = json.loads(payload)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"履约同步结果解析失败: {payload[:300]}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"履约同步进程退出码 {proc.returncode}: {data}")
    return data
