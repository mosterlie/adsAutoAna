# -*- coding: utf-8 -*-
"""广告组合下拉数据 —— 对齐赛狐「新建广告」页的取数方式。

赛狐原页面（ad-create/index.html）在进入时按当前店铺请求:

    POST https://www.sellfox.com/api/portfolio/getAllPortfolioDataProductRight.json
    body {"shopId":"711811","isHidden":false}
    resp {"code":0,"data":[{"portfolioId":"69195939170607","portfolioName":"手工广告"}, ...]}

这里原样复用该接口（走 SellfoxClient 的 Cookie 会话），并做两点工程化:
  1. 进程内缓存（默认 10 分钟）——切店铺/来回切视图不重复打接口；
  2. 接口不可用（未登录赛狐 / code!=0 / 超时）时回退本地 ad_portfolios 表，
     返回里用 ``source`` 标明数据来自哪里，前端可提示。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional, Tuple

#: 赛狐「按店铺取广告组合」接口（绝对路径，SellfoxClient 会补 ORIGIN）
PORTFOLIO_API = "/api/portfolio/getAllPortfolioDataProductRight.json"

#: 缓存有效期（秒）
CACHE_TTL = 600

_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}


def _normalize(data: Any) -> List[Dict[str, str]]:
    """赛狐 data → [{portfolio_id, portfolio_name}]，按名称排序。"""
    rows: List[Dict[str, str]] = []
    if isinstance(data, dict):               # 兼容 data:{rows:[...]}
        data = data.get("rows") or data.get("list") or []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("portfolioId") or "").strip()
        if not pid:
            continue
        rows.append({"portfolio_id": pid,
                     "portfolio_name": str(item.get("portfolioName") or "").strip()})
    rows.sort(key=lambda r: (r["portfolio_name"], r["portfolio_id"]))
    return rows


def fetch_from_sellfox(cookies: Dict[str, str], shop_id: str) -> List[Dict[str, str]]:
    """实时请求赛狐接口（失败抛异常，由上层决定回退）。"""
    from backend.sellfox_client import SellfoxClient

    client = SellfoxClient(cookies)
    resp = client.post(PORTFOLIO_API, {"shopId": str(shop_id), "isHidden": False})
    return _normalize(resp.get("data"))


def get_portfolios(cookies: Optional[Dict[str, str]], shop_id: str,
                   shop_name: str = "", force: bool = False) -> Dict[str, Any]:
    """返回 {rows, source, note}

    rows: [{"portfolio_id","portfolio_name"}]
    source: sellfox(实时接口) / db(本地库回退) / empty
    """
    sid = str(shop_id or "").strip()
    if not sid:
        return {"rows": [], "source": "empty", "note": "未选择店铺"}

    if not force:
        with _LOCK:
            hit = _CACHE.get(sid)
            if hit and time.time() - hit[0] < CACHE_TTL:
                return {"rows": hit[1], "source": "sellfox", "note": ""}

    note = ""
    if cookies:
        try:
            rows = fetch_from_sellfox(cookies, sid)
            with _LOCK:
                _CACHE[sid] = (time.time(), rows)
            return {"rows": rows, "source": "sellfox", "note": ""}
        except Exception as exc:            # noqa: BLE001 - 统一回退本地库
            note = f"赛狐接口不可用，已回退本地组合表：{exc}"
    else:
        note = "未找到赛狐登录 Cookie，已回退本地组合表"

    from backend import database as db

    rows = [
        {"portfolio_id": str(p.get("portfolio_id") or ""),
         "portfolio_name": str(p.get("portfolio_name") or "")}
        for p in (db.get_portfolios() or [])
        if str(p.get("portfolio_id") or "") and (
            not sid or str(p.get("shop_id") or "") == sid
            or not p.get("shop_id"))
    ]
    rows.sort(key=lambda r: (r["portfolio_name"], r["portfolio_id"]))
    return {"rows": rows, "source": "db" if rows else "empty", "note": note}
