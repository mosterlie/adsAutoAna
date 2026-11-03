# -*- coding: utf-8 -*-
"""站点图标(favicon): 给「采购源链接」列显示对应网站图标

为什么不直接 <img src="https://detail.1688.com/favicon.ico">:
  子域常常没有 favicon、还带防盗链/Referer 校验, 浏览器里直接请求多是一张破图。
所以统一走本服务代取并缓存:
    GET /api/favicon?u=<页面URL>
      ① 解析站点(主域): detail.1688.com → 1688.com
      ② 依次尝试 https://<子域>/favicon.ico、https://<主域>/favicon.ico、apple-touch-icon.png
      ③ 取到就存内存 + data/favicons/<md5>.<ext>, 下次直接命中
      ④ 都取不到返回 404, 前端 <img onerror> 回退成纯域名文字, 不会破图错位
"""
import hashlib
import os
import threading
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional, Tuple

import config

#: 二级后缀 (取主域时要多吃一段): www.amazon.co.jp → amazon.co.jp
_TWO_LEVEL = {"com.cn", "net.cn", "org.cn", "gov.cn", "co.jp", "ne.jp", "or.jp",
              "com.hk", "com.tw", "co.uk", "com.au", "com.sg", "com.my"}
_MAX_BYTES = 200_000
_TIMEOUT = 6
_CT_EXT = (("image/x-icon", ".ico"), ("image/vnd.microsoft.icon", ".ico"),
           ("image/png", ".png"), ("image/svg+xml", ".svg"),
           ("image/jpeg", ".jpg"), ("image/webp", ".webp"))

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_LOCK = threading.Lock()
_MEM: Dict[str, Optional[Tuple[bytes, str]]] = {}


def _host(url: str) -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    try:
        return (urllib.parse.urlparse(u).hostname or "").lower()
    except Exception:                           # noqa: BLE001
        return ""


def site_domain(url: str) -> str:
    """站点主域: detail.1688.com → 1688.com / mobile.yangkeduo.com → yangkeduo.com"""
    host = _host(url)
    if not host or host.replace(".", "").isdigit():
        return host
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _cache_dir() -> str:
    d = os.path.join(config.DATA_DIR, "favicons")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_base(site: str) -> str:
    return os.path.join(_cache_dir(), hashlib.md5(site.encode("utf-8")).hexdigest())


def _load_disk(site: str) -> Optional[Tuple[bytes, str]]:
    base = _cache_base(site)
    for ctype, ext in _CT_EXT:
        p = base + ext
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    return f.read(), ctype
            except Exception:                   # noqa: BLE001
                return None
    return None


def _save_disk(site: str, data: bytes, ctype: str) -> None:
    ext = next((e for ct, e in _CT_EXT if ct == ctype), ".ico")
    try:
        with open(_cache_base(site) + ext, "wb") as f:
            f.write(data)
    except Exception:                           # noqa: BLE001
        pass
    _neg_clear(site)


#: 取不到图标的站点也落盘记一笔(冷却 _NEG_TTL 秒), 免得每次重启都去白等 4×6s 超时
_NEG_TTL = 7 * 24 * 3600


def _neg_path(site: str) -> str:
    return _cache_base(site) + ".none"


def _neg_hit(site: str) -> bool:
    p = _neg_path(site)
    try:
        return os.path.exists(p) and (time.time() - os.path.getmtime(p)) < _NEG_TTL
    except Exception:                           # noqa: BLE001
        return False


def _neg_save(site: str) -> None:
    try:
        open(_neg_path(site), "w").close()
    except Exception:                           # noqa: BLE001
        pass


def _neg_clear(site: str) -> None:
    try:
        os.remove(_neg_path(site))
    except Exception:                           # noqa: BLE001
        pass


def _fetch(u: str) -> Optional[Tuple[bytes, str]]:
    req = urllib.request.Request(u, headers={
        "User-Agent": config.USER_AGENT, "Referer": "https://" + _host(u) + "/"})
    r = None
    try:
        r = _OPENER.open(req, timeout=_TIMEOUT)          # 先直连(国内站点大多只能直连)
    except Exception:                                    # noqa: BLE001
        try:
            r = urllib.request.urlopen(req, timeout=_TIMEOUT)   # 再走系统代理(亚马逊等)
        except Exception:                                # noqa: BLE001
            return None
    try:
        with r:
            data = r.read(_MAX_BYTES + 1)
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    except Exception:                                    # noqa: BLE001
        return None
    if not data or len(data) > _MAX_BYTES:
        return None
    if ctype and not ctype.startswith("image/"):
        return None
    if not ctype:
        ctype = "image/x-icon"
    return data, ctype


def candidates(page_url: str) -> list:
    """按优先级给出该页面可能放 favicon 的地址"""
    host, site = _host(page_url), site_domain(page_url)
    out = []
    if host:
        out.append(f"https://{host}/favicon.ico")
    if site and site != host:
        out.append(f"https://{site}/favicon.ico")
    if site:
        out += [f"https://{site}/apple-touch-icon.png",
                f"https://www.{site}/favicon.ico"]
    return out


def get(page_url: str) -> Optional[Tuple[bytes, str]]:
    """取 favicon → (bytes, content_type); 取不到返回 None"""
    site = site_domain(page_url)
    if not site:
        return None
    with _LOCK:
        if site in _MEM:
            return _MEM[site]
    hit = _load_disk(site)
    if hit is None:
        if not _neg_hit(site):
            for u in candidates(page_url):
                hit = _fetch(u)
                if hit:
                    _save_disk(site, hit[0], hit[1])
                    break
            else:
                _neg_save(site)
    with _LOCK:
        _MEM[site] = hit
    return hit
