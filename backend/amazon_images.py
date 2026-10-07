# -*- coding: utf-8 -*-
"""把搜索结果里各商品的主图下载到本地

落盘位置: <父ASIN>/主图/<搜索词>/<顺位>_<ASIN>.<ext>
反爬处理:
  * 图片走亚马逊的静态 CDN(m.media-amazon.com), 与搜索页不同源, 仍严格串行;
  * 每张之间加随机小间隔(默认 0.4~1.0 秒), 不并发、不重试风暴;
  * 已存在的文件直接跳过 → 重复运行只补缺失的;
  * 只取搜索结果卡片自带的主图地址(优先 srcset 里的高清图), 不做其它请求。
"""
import os
import random
import time
from typing import Any, Dict, List, Optional

import requests

import config

_DEFAULT_HEADERS = {
    "User-Agent": config.USER_AGENT,
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Referer": "https://www.amazon.co.jp/",
}

_EXT_OK = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _ext_of(url: str) -> str:
    path = (url or "").split("?")[0]
    low = path.lower()
    for e in _EXT_OK:
        if low.endswith(e):
            return ".jpg" if e in (".jpeg",) else e
    return ".jpg"


def file_name(item: Dict[str, Any], ext: str = "") -> str:
    """<顺位>_<ASIN>.jpg —— 顺位补零便于排序"""
    pos = item.get("position") or 0
    asin = (item.get("asin") or "unknown").strip() or "unknown"
    return f"{int(pos or 0):02d}_{asin}{ext}"


def _existing(dest: str, stem: str) -> Optional[str]:
    """同名(不含扩展名)文件已存在则返回其路径"""
    if not os.path.isdir(dest):
        return None
    for e in _EXT_OK:
        p = os.path.join(dest, stem + e)
        if os.path.exists(p) and os.path.getsize(p) > 512:
            return p
    return None


def download(items: List[Dict[str, Any]], dest: str, delay_min: Optional[float] = None,
             delay_max: Optional[float] = None, max_items: int = 0,
             session: Optional[requests.Session] = None) -> Dict[str, Any]:
    """下载一页搜索结果里各商品的主图, 返回 {saved, skipped, failed, bytes, files}"""
    dmin = config.AMAZON_IMAGE_DELAY_MIN_SEC if delay_min is None else delay_min
    dmax = config.AMAZON_IMAGE_DELAY_MAX_SEC if delay_max is None else delay_max
    os.makedirs(dest, exist_ok=True)

    sess = session or requests.Session()
    sess.headers.update(_DEFAULT_HEADERS)
    pm = config.proxy_map()
    if pm and sess.proxies != pm:
        sess.proxies.update(pm)     # 与浏览器一致: 浏览器走系统代理, 这里也走
    stat = {"saved": 0, "skipped": 0, "failed": 0, "bytes": 0, "files": []}

    todo = items[:max_items] if max_items and max_items > 0 else items
    for i, it in enumerate(todo):
        url = it.get("image_big") or it.get("image") or ""
        stem = file_name(it).rsplit(".", 1)[0]
        if not url:
            stat["failed"] += 1
            continue
        old = _existing(dest, stem)
        if old:
            stat["skipped"] += 1
            stat["files"].append(old)
            continue
        ext = _ext_of(url)
        path = os.path.join(dest, stem + ext)
        tmp = path + ".part"
        try:
            r = sess.get(url, timeout=config.AMAZON_IMAGE_TIMEOUT_SEC, stream=True)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if r.status_code != 200 or "image" not in ctype:
                stat["failed"] += 1
                continue
            n = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    if chunk:
                        f.write(chunk)
                        n += len(chunk)
            if n < 512:                      # 太小基本是占位图/错误页
                os.remove(tmp)
                stat["failed"] += 1
                continue
            os.replace(tmp, path)
            stat["saved"] += 1
            stat["bytes"] += n
            stat["files"].append(path)
        except Exception:       # noqa: BLE001  单张失败不影响整批
            stat["failed"] += 1
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
        # 随机间隔: 别做成固定节拍的批量抓图
        if i < len(todo) - 1:
            time.sleep(random.uniform(dmin, max(dmin, dmax)))
    stat["bytes_mb"] = round(stat["bytes"] / 1048576, 2)
    return stat


def count(dest: str) -> int:
    """目录里已有的图片张数"""
    if not os.path.isdir(dest):
        return 0
    return len([f for f in os.listdir(dest) if os.path.splitext(f)[1].lower() in _EXT_OK])
