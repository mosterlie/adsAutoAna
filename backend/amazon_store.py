# -*- coding: utf-8 -*-
"""亚马逊搜索结果的「本地文件仓库」(按父 ASIN 分目录)

目录结构 (ROOT = config.AMAZON_DATA_DIR, 默认 E:\\amazonData\\adsData):
    _index.json                              全局索引: 搜索词 -> 归属父ASIN/抓取时间/文件
    _shots/<搜索词>.png                       抓取当时的整页截图(自查反爬拦截用, 不放父目录)
    <父ASIN>/json/<搜索词>.json               该搜索词的亚马逊搜索结果(完整数据)
    <父ASIN>/主图/<搜索词>/<顺位>_<ASIN>.jpg   该搜索词结果页里各商品的主图

为什么要文件仓库:
  * 预抓取是长任务, 需要"抓一条存一条", 断点续抓时按索引直接跳过;
  * 一个搜索词可能归属多个父 ASIN(挂在多个广告活动下), 每个父目录各存一份;
  * 网页点击搜索词时先查索引, 命中即不再访问亚马逊。
"""
import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import config

# 可重入锁: 有些流程会在持锁时再调用同样加锁的函数(mark_parent_done -> parents_state),
# 用 Lock 会自己把自己锁死, 必须用 RLock
_IO_LOCK = threading.RLock()
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_MAX_NAME = 60
INDEX_NAME = "_index.json"
SHOT_DIRNAME = "_shots"
UNMAPPED_DIR = "_unmapped"      # 解析不出父 ASIN 的搜索词放这里
DONE_NAME = "_done.json"        # 放在 <父ASIN>/ 下: 该父体已抓完的标记
PARENTS_FILE = "_parents.json"  # 各父 ASIN 状态的汇总

# 主图可能的扩展名 (与 amazon_images._EXT_OK 保持一致, 这里另定义避免循环依赖)
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------
def root() -> str:
    """本地仓库根目录 (不存在则创建)"""
    d = config.AMAZON_DATA_DIR
    os.makedirs(d, exist_ok=True)
    return d


def index_path() -> str:
    return os.path.join(root(), INDEX_NAME)


def safe_name(query: str) -> str:
    """搜索词 → 文件名安全串 (保留中文/日文, 末尾加哈希防撞名)"""
    q = (query or "").strip()
    name = _ILLEGAL.sub("_", q)
    name = re.sub(r"\s+", "_", name).strip(" ._")
    if len(name) > _MAX_NAME:
        name = name[:_MAX_NAME].rstrip(" ._")
    digest = hashlib.md5(q.encode("utf-8")).hexdigest()[:8]
    return f"{name or 'query'}_{digest}"


def _safe_parent(parent: str) -> str:
    return _ILLEGAL.sub("_", (parent or "").strip()) or "unknown"


def _safe_segment(seg: str) -> str:
    """校验一段路径(来自 URL/外部输入): 含路径分隔符或 .. 直接判非法"""
    s = (seg or "").strip()
    if not s or ".." in s or "/" in s or "\\" in s:
        return ""
    return _ILLEGAL.sub("_", s)


def json_dir(parent: str) -> str:
    d = os.path.join(root(), _safe_parent(parent), "json")
    os.makedirs(d, exist_ok=True)
    return d


def json_path(parent: str, query: str) -> str:
    return os.path.join(json_dir(parent), safe_name(query) + ".json")


def image_dir(parent: str, query: str) -> str:
    """该父 ASIN 下、该搜索词的竞品主图目录"""
    d = os.path.join(root(), _safe_parent(parent), "主图", safe_name(query))
    os.makedirs(d, exist_ok=True)
    return d


def shot_path(query: str) -> str:
    """抓取当时的整页截图 (诊断用, 不放进父 ASIN 目录)"""
    d = os.path.join(root(), SHOT_DIRNAME)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, safe_name(query) + ".png")


# ---------------------------------------------------------------------------
# 索引: 搜索词 -> {parents, fetched_at, item_count, blocked, json{父:路径}, images{父:张数}}
# ---------------------------------------------------------------------------
def load_index() -> Dict[str, Any]:
    p = index_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:       # noqa: BLE001  索引损坏当作空, 按"未抓取"处理
        return {}


def _save_index(idx: Dict[str, Any]) -> None:
    p = index_path()
    tmp = p + ".tmp"
    with _IO_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, p)


def info(query: str) -> Optional[Dict[str, Any]]:
    """该搜索词的索引项 (未抓取返回 None)"""
    if not query:
        return None
    return load_index().get(query)


def exists(query: str) -> bool:
    """是否已抓过(索引里有, 且至少一个 json 文件还在)"""
    ent = info(query)
    if not ent:
        return False
    for p in (ent.get("json") or {}).values():
        if p and os.path.exists(p):
            return True
    return False


def local_queries(queries: List[str],
                  index: Optional[Dict[str, Any]] = None) -> set:
    """这一批搜索词里哪些本地已有结果(批量版 exists, 整份索引只读一次)

    用于「还有多少没抓」这类统计: 逐个 exists() 会把索引文件读 N 遍。
    """
    idx = index if index is not None else load_index()
    out: set = set()
    for q in queries or []:
        s = str(q or "").strip()
        if not s or s in out:
            continue
        for p in ((idx.get(s) or {}).get("json") or {}).values():
            if p and os.path.exists(p):
                out.add(s)
                break
    return out


def find(query: str) -> Optional[Dict[str, Any]]:
    """取本地结果(网页查看用): 命中返回结果 dict, 未命中返回 None

    返回体与实时抓取一致, 额外带 from_local / local_path / local_parent / parents。
    """
    ent = info(query)
    if not ent:
        return None
    for parent, p in (ent.get("json") or {}).items():
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:       # noqa: BLE001
                continue
            if not isinstance(data, dict) or "items" not in data:
                continue
            data["from_local"] = True
            data["from_cache"] = True
            data["local_path"] = p
            data["local_parent"] = parent
            data["parents"] = ent.get("parents") or list((ent.get("json") or {}).keys())
            return data
    return None


def save_result(query: str, parents: List[str], result: Dict[str, Any]) -> Dict[str, str]:
    """存一次抓取结果: 写索引 + 每个父 ASIN 的 json/<词>.json; 返回 {父: 路径}"""
    parents = [p for p in dict.fromkeys(parents or []) if p]
    payload = dict(result)
    payload["query"] = query
    if not payload.get("fetched_at"):
        payload["fetched_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # 解析不出父 ASIN 的搜索词(不在广告数据里/无变体关系)也要落盘, 否则数据只进了索引、
    # 下次查看又会重抓。这类词统一放 _unmapped 目录, 一眼能看出是"没归属"的。
    if not parents:
        parents = [UNMAPPED_DIR]

    saved: Dict[str, str] = {}
    for parent in parents:
        p = json_path(parent, query)
        tmp = p + ".tmp"
        with _IO_LOCK:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
        saved[parent] = p

    idx = load_index()
    ent = idx.get(query) or {}
    ent.update({
        "query": query,
        "parents": parents,
        "fetched_at": payload.get("fetched_at"),
        "item_count": payload.get("item_count") or len(payload.get("items") or []),
        "blocked": bool(payload.get("blocked")),
        "via": payload.get("via"),
        "url": payload.get("url"),
        "json": saved,
    })
    ent.setdefault("images", {})
    idx[query] = ent
    _save_index(idx)
    return saved


def note_images(query: str, counts: Dict[str, int]) -> None:
    """记录某个搜索词在各父 ASIN 下已下载的主图张数"""
    idx = load_index()
    ent = idx.get(query)
    if not ent:
        return
    imgs = ent.setdefault("images", {})
    for parent, n in (counts or {}).items():
        imgs[parent] = int(n)
    _save_index(idx)


def images_pending(query: str, parents: List[str]) -> List[str]:
    """哪些父 ASIN 的主图目录还是空的(用于只补图片、不重开浏览器)"""
    ent = info(query) or {}
    imgs = ent.get("images") or {}
    return [p for p in parents if not imgs.get(p)]


def parent_term_state(parent: str, query: str,
                      index: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """(父 ASIN, 搜索词) 的本地素材情况 —— 给「已拉取」状态判断用

        fetched: 结果数据(json)已落盘 → 详情页可直接读本地, 不用再访问亚马逊
        images : 已下载的竞品主图张数(索引没记按 0 算, 与 images_pending 口径一致)

    批量判断时把 index(load_index() 的结果)传进来, 否则每个词都会重新加载整份索引。
    """
    p, q = (parent or "").strip(), (query or "").strip()
    if not p or not q:
        return {"fetched": False, "images": 0}
    ent = (index if index is not None else load_index()).get(q) or {}
    path = (ent.get("json") or {}).get(p) or ""
    return {"fetched": bool(path) and os.path.exists(path),
            "images": int((ent.get("images") or {}).get(p) or 0)}


# ---------------------------------------------------------------------------
# 清单 / 统计 / 维护
# ---------------------------------------------------------------------------
def list_local(parent: str = "") -> List[Dict[str, Any]]:
    """本地已抓的搜索词清单(按抓取时间倒序); 传 parent 只看该父 ASIN"""
    idx = load_index()
    rows: List[Dict[str, Any]] = []
    for q, ent in idx.items():
        parents = ent.get("parents") or list((ent.get("json") or {}).keys())
        if parent and parent not in parents:
            continue
        rows.append({
            "query": q,
            "parents": parents,
            "item_count": ent.get("item_count"),
            "blocked": bool(ent.get("blocked")),
            "via": ent.get("via"),
            "fetched_at": ent.get("fetched_at") or "",
            "images": ent.get("images") or {},
            "file": next(iter((ent.get("json") or {}).values()), ""),
        })
    rows.sort(key=lambda x: x.get("fetched_at") or "", reverse=True)
    return rows


# 体积统计要遍历整棵目录树(上万张图时很贵), 页面每 2 秒轮询一次进度,
# 所以加个短 TTL 缓存, 没必要每次都重算。
_SIZE_CACHE: Dict[str, Any] = {"at": 0.0, "size": 0}
_SIZE_TTL = 10.0


def _dir_size(r: str) -> int:
    now = time.time()
    if now - _SIZE_CACHE["at"] < _SIZE_TTL:
        return int(_SIZE_CACHE["size"])
    total = 0
    for dirpath, _dirnames, filenames in os.walk(r):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    _SIZE_CACHE.update({"at": now, "size": total})
    return total


def stats() -> Dict[str, Any]:
    """仓库概况: 根目录 / 词数 / 父ASIN数 / 主图张数 / 体积"""
    r = root()
    idx = load_index()
    parents: set = set()
    imgs = 0
    size = 0
    for q, ent in idx.items():
        parents |= set(ent.get("parents") or [])
        imgs += sum(int(v or 0) for v in (ent.get("images") or {}).values())
    size = _dir_size(r)
    return {"root": r, "terms": len(idx), "parents": len(parents),
            "parents_done": len(parents_state()),
            "images": imgs, "size_mb": round(size / 1048576, 2)}


def remove(query: str) -> bool:
    """删掉某搜索词的本地数据(索引 + 各父目录下的 json 与主图)"""
    idx = load_index()
    ent = idx.pop(query, None)
    if not ent:
        return False
    for p in (ent.get("json") or {}).values():
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    for parent in ent.get("parents") or []:
        d = os.path.join(root(), _safe_parent(parent), "主图", safe_name(query))
        if os.path.isdir(d):
            for fn in os.listdir(d):
                try:
                    os.remove(os.path.join(d, fn))
                except OSError:
                    pass
            try:
                os.rmdir(d)
            except OSError:
                pass
    _save_index(idx)
    return True


def parents_state() -> Dict[str, Any]:
    """各父 ASIN 的抓取状态 (父 ASIN -> {terms, terms_done, images, finished_at})"""
    p = os.path.join(root(), PARENTS_FILE)
    with _IO_LOCK:
        if not os.path.exists(p):
            return {}
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:       # noqa: BLE001
            return {}


def parent_done(parent: str) -> bool:
    """该父 ASIN 是否标记为"已抓完\""""
    return parent in parents_state()


def mark_parent_done(parent: str, info: Dict[str, Any]) -> Dict[str, Any]:
    """标记父 ASIN 抓取完成

    两处留痕:
      * <父ASIN>/_done.json  —— 目录里直接能看到(人工核对方便)
      * _parents.json        —— 汇总, 供接口/页面统计"已完成 x/y"
    """
    if not parent:
        return {}
    ent = dict(info or {})
    ent["parent"] = parent
    ent.setdefault("finished_at", time.strftime("%Y-%m-%d %H:%M:%S"))

    d = os.path.join(root(), _safe_parent(parent))
    os.makedirs(d, exist_ok=True)
    with _IO_LOCK:
        with open(os.path.join(d, DONE_NAME), "w", encoding="utf-8") as f:
            json.dump(ent, f, ensure_ascii=False, indent=2)

        st = parents_state()
        st[parent] = ent
        p = os.path.join(root(), PARENTS_FILE)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, p)
    return ent


def local_image_url(parent: str, query: str, item: Dict[str, Any]) -> str:
    """该 item 在本地的主图地址(相对 URL); 本地没有这张图时返回 ""

    本地文件名规则与 amazon_images.file_name 一致: <顺位:02d>_<ASIN>.<ext>,
    扩展名不固定(jpg/png/webp), 所以 URL 只带文件主干, 由 /amz-media 路由去匹配。
    """
    try:
        pos = int(item.get("position") or 0)
    except (TypeError, ValueError):
        return ""
    asin = (item.get("asin") or "").strip()
    if not parent or not pos or not asin:
        return ""
    stem = f"{pos:02d}_{asin}"
    d = os.path.join(root(), _safe_parent(parent), "主图", safe_name(query))
    if not os.path.isdir(d):
        return ""
    if not any(os.path.exists(os.path.join(d, stem + e)) for e in IMAGE_EXTS):
        return ""
    return (f"/amz-media/{quote(_safe_parent(parent))}"
            f"/{quote(safe_name(query))}/{quote(stem)}")


def resolve_image(parent: str, term: str, stem: str) -> Optional[str]:
    """按 (父ASIN, 搜索词目录名, 文件主干) 找本地图片; 找不到或不合法返回 None

    只允许落在 <root>/<父ASIN>/主图/<搜索词>/ 之内, 防目录穿越。
    """
    p_seg, t_seg = _safe_segment(parent), _safe_segment(term)
    s_seg = _safe_segment(stem)
    if not (p_seg and t_seg and s_seg) or "." in s_seg:
        return None
    base = os.path.join(root(), p_seg, "主图", t_seg)
    for e in IMAGE_EXTS:
        p = os.path.join(base, s_seg + e)
        try:
            if os.path.isfile(p) and os.path.realpath(p).startswith(
                    os.path.realpath(base) + os.sep):
                return p
        except OSError:
            continue
    return None


def image_count(parent: str) -> int:
    """该父 ASIN 下已下载的主图总张数"""
    d = os.path.join(root(), _safe_parent(parent), "主图")
    if not os.path.isdir(d):
        return 0
    n = 0
    for _dirpath, _dirnames, filenames in os.walk(d):
        n += len(filenames)
    return n


def _pid_alive(pid: int) -> bool:
    """进程是否还在 (判断锁是不是陈旧的)"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.OpenProcess(0x1000, False, pid)      # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            k32.CloseHandle(h)
            return True
        except Exception:       # noqa: BLE001
            return True         # 判定不了就当作还活着, 宁可拒绝也不并发
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def run_lock_info() -> Optional[Dict[str, Any]]:
    """当前抓取锁的持有者(没有则 None)"""
    p = os.path.join(root(), "_lock")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:       # noqa: BLE001
        return {"pid": 0, "kind": "unknown"}
    if not _pid_alive(int(d.get("pid") or 0)):
        return None         # 陈旧的锁
    return d


def acquire_run_lock(kind: str = "prefetch") -> bool:
    """跨进程互斥: CLI 与网页按钮共用一把锁, 保证同一时刻只有一个抓取在跑

    并发抓取会让访问量翻倍, 是最容易触发风控的做法, 所以宁可直接拒绝也不并发。
    进程异常退出留下的陈旧锁(PID 已不存在)会被自动接管。
    """
    if run_lock_info():
        return False
    p = os.path.join(root(), "_lock")
    with _IO_LOCK:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "kind": kind,
                       "at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)
    return True


def release_run_lock() -> None:
    p = os.path.join(root(), "_lock")
    try:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            if int(d.get("pid") or 0) == os.getpid():
                os.remove(p)
    except Exception:       # noqa: BLE001
        pass


def migrate_legacy(resolve_parents: Callable[[str], List[str]]) -> Dict[str, int]:
    """把旧版 <domain>/<词>.json 迁移到新版 <父ASIN>/json/ 下

    旧版按站点分目录(co.jp/...), 新版按父 ASIN 分目录。迁移后删除旧文件,
    避免同一份数据两处存在; 解析不出父 ASIN 的词保持不动。
    """
    moved = kept = 0
    r = root()
    for entry in os.listdir(r):
        d = os.path.join(r, entry)
        if not os.path.isdir(d) or entry.startswith("_") or "." not in entry:
            continue                                # 只处理 co.jp 这类站点目录
        for fn in list(os.listdir(d)):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(d, fn)
            try:
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
                q = data.get("query") or fn[:-5]
                parents = resolve_parents(q) or []
                if not parents:
                    kept += 1
                    continue
                save_result(q, parents, data)
                os.remove(p)
                moved += 1
            except Exception:       # noqa: BLE001
                kept += 1
        try:
            os.rmdir(d)                             # 空了就顺手删掉站点目录
        except OSError:
            pass
    return {"moved": moved, "kept": kept}
