# -*- coding: utf-8 -*-
"""批量预抓取: **按父 ASIN 逐个抓** —— 抓完一个父 ASIN 下的所有广告词(点击达标)再换下一个

落盘结构 (config.AMAZON_DATA_DIR, 默认 E:\\amazonData\\adsData):
    <父ASIN>/json/<搜索词>.json               搜索结果完整数据(日元报价)
    <父ASIN>/主图/<搜索词>/<顺位>_<ASIN>.jpg   结果页里各商品的主图
    <父ASIN>/_done.json                       该父 ASIN 已抓完的标记
    _parents.json                             各父 ASIN 状态汇总(已完成 x/y)
    _index.json                               搜索词 -> 归属父ASIN/时间/主图数
    _shots/<搜索词>.png                        抓取截图(自查反爬拦截用)

抓取顺序: 父 ASIN 组按"组内点击合计"降序, 组内搜索词按点击降序;
        一个词挂在多个父体下时, 抓一次会同时写入它所有的父目录, 后续组遇到会直接跳过。

反爬策略(核心: 让访问"少、慢、不规律"):
  1. 严格串行: 同一时刻只有一个标签页在抓(amazon._LOCK 全局互斥);
  2. 词间随机间隔: 默认 8~15 秒随机, 不是固定节拍;
  3. 批内长休: 每抓 N 条(默认 20)额外休息一次;
  4. 复用已登录的调试 Chrome: 真实浏览器指纹, 不伪造 UA、不并发;
  5. 断点续抓: 已抓过的词直接跳过(只缺主图则只补图片), 已标记完成的父 ASIN 整组跳过;
  6. 撞验证码立即长退避; 连续多次或连续失败则中止本批次, 保留已抓成果。
"""
import random
import socket
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import config
from backend import amazon, amazon_images, amazon_store, amazon_targets
from backend import database as db

_STATE_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {
    "running": False, "min_clicks": config.AMAZON_PREFETCH_MIN_CLICKS,
    "root": "", "terms": 0, "parents": 0, "parents_total": 0, "parents_done": 0,
    "parent": "", "parent_index": 0, "parent_terms": 0,
    "total": 0, "index": 0, "done": 0, "ok": 0, "skipped": 0, "failed": 0,
    "images": 0, "img_new": 0, "img_failed": 0,
    "blocked": 0, "current": "", "started_at": None, "finished_at": None,
    "error": None, "stopped": False, "log": [],
}
_STOP = threading.Event()
_THREAD: Optional[threading.Thread] = None


def _log(msg: str) -> None:
    line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
    with _STATE_LOCK:
        _STATE["log"].append(line)
        if len(_STATE["log"]) > 400:      # 只留最近 400 行
            del _STATE["log"][:-400]
    print(line, flush=True)


def _set(**kw: Any) -> None:
    with _STATE_LOCK:
        _STATE.update(kw)


def get_state() -> Dict[str, Any]:
    """采集进度 (供接口/CLI 轮询)"""
    with _STATE_LOCK:
        st = dict(_STATE)
        st["log"] = list(_STATE["log"])[-100:]
    st["local"] = amazon_store.stats()
    st["parent_state"] = amazon_store.parents_state()
    return st


# ---------------------------------------------------------------------------
# 启动前预检: 网络 + 调试浏览器
# ---------------------------------------------------------------------------
def cdp_ok(cdp: str = "") -> bool:
    """调试 Chrome(CDP)在不在 —— 抓取走的就是它, 没开着会整批失败"""
    url = cdp or config.CDP_URL
    host, port = "127.0.0.1", 9222
    try:
        u = urlparse(url)
        host = u.hostname or host
        port = u.port or port
    except Exception:       # noqa: BLE001
        pass
    try:
        with socket.create_connection((host, port), timeout=5):
            return True
    except OSError:
        return False


def _net_once(host: str = "www.amazon.co.jp", port: int = 443,
              timeout: float = 8) -> bool:
    pm = config.proxy_map()
    if pm:
        try:
            import requests
            requests.get(f"https://{host}/", proxies=pm, timeout=timeout + 7,
                         headers={"User-Agent": config.USER_AGENT})
            return True                     # 有响应(含 403/503)即视为可达
        except Exception:       # noqa: BLE001  连不上/代理不通
            return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def net_ok(retries: int = 3) -> bool:
    """Python(requests)侧能不能到亚马逊 —— 主图下载走这条路

    代理会抖动, 一次失败不代表真断, 所以失败后重试几次再下结论。
    """
    for i in range(max(1, retries)):
        if _net_once():
            return True
        if i < retries - 1:
            time.sleep(3)
    return False


def browser_ok(timeout_ms: int = 30000) -> bool:
    """浏览器能不能打开亚马逊 —— 页面抓取真正走的是这条路

    代理抖动时 Python 侧可能连不上, 但浏览器(走系统代理)照样能抓页面;
    此时只影响主图下载, 不该因此把整批中止。
    """
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(config.CDP_URL, timeout=15000)
            page = b.contexts[0].new_page()
            try:
                page.goto("https://www.amazon.co.jp/", wait_until="domcontentloaded",
                          timeout=timeout_ms)
                return True
            finally:
                page.close()
    except Exception:       # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 主图: 抓完一个搜索词就把它结果页里各商品的主图下到对应父 ASIN 目录
# ---------------------------------------------------------------------------
def _save_images(query: str, parents: List[str], items: List[Dict[str, Any]],
                 image_limit: int = 0) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for parent in parents:
        dest = amazon_store.image_dir(parent, query)
        st = amazon_images.download(items or [], dest, max_items=image_limit)
        counts[parent] = amazon_images.count(dest)
        _log(f"      主图[{parent}] 新增 {st['saved']} · 已有 {st['skipped']} · "
             f"失败 {st['failed']} · 共 {counts[parent]} 张")
        _set(img_new=_STATE["img_new"] + st["saved"],
             img_failed=_STATE["img_failed"] + st["failed"],
             images=sum(counts.values()))
    if counts:
        amazon_store.note_images(query, counts)
    return counts


# ---------------------------------------------------------------------------
# 抓取主体: 按父 ASIN 逐组推进
# ---------------------------------------------------------------------------
def _worker(min_clicks: int, limit: int, refresh: bool, gap_min: float, gap_max: float,
            batch_size: int, rest_sec: float, block_backoff: float, max_blocked: int,
            screenshot: bool, scrolls: int, images: bool, image_limit: int,
            stop: threading.Event, max_fail: int = 5, skip_done_parents: bool = True) -> None:
    try:
        root = amazon_store.root()
    except Exception as e:      # noqa: BLE001  磁盘不可用(如 E 盘不存在)
        _set(running=False, error=f"本地目录不可用: {e}", finished_at=db.now())
        _log(f"✗ 本地目录不可用: {e}")
        return

    # 旧版(按站点分目录)的数据迁到新版(按父 ASIN)
    try:
        mig = amazon_store.migrate_legacy(amazon_targets.parents_of)
        if mig["moved"] or mig["kept"]:
            _log(f"· 旧数据迁移: 迁入 {mig['moved']} 个, 保留 {mig['kept']} 个")
    except Exception as e:      # noqa: BLE001
        _log(f"! 旧数据迁移跳过: {str(e)[:80]}")

    items = amazon_targets.build(min_clicks, keywords_only=True, limit=limit)
    groups = amazon_targets.group_by_parent(items)
    sm = amazon_targets.summary(items)
    _set(root=root, min_clicks=min_clicks, total=len(items), terms=sm["terms"],
         parents=len(groups), parents_total=len(groups))
    _log(f"▶ 目标: 点击 >= {min_clicks} 的搜索词 {sm['terms']} 个 · "
         f"父 ASIN {len(groups)} 个 · 合计花费 {sm['adCost']}")
    if not items:
        _log("! 没有达标的搜索词(先采集「搜索词」页签)")
        _set(running=False, finished_at=db.now())
        return
    _log(f"  本地目录: {root}")
    _log(f"  抓取顺序: 按父 ASIN 逐个抓, 组内按点击降序")

    # 先探一下网络: 连不上就别把整批都跑成失败
    if not net_ok():
        # Python 侧不通还可能只是代理抖动; 页面抓取走浏览器, 以浏览器为准
        if browser_ok():
            _log("! Python 侧到亚马逊不通(代理抖动?), 但浏览器可正常打开 → "
                 "继续抓页面; 主图下载可能失败, 稍后重跑会自动补")
        else:
            _log("✗ 连不上 www.amazon.co.jp(网络不通或被墙), 本次不抓; "
                 "网络恢复后重跑即可(已抓成果保留)")
            _set(running=False, error="网络不通: www.amazon.co.jp",
                 finished_at=db.now())
            return

    # 再探浏览器: 抓取走的是调试 Chrome, 没开着就自动拉起(拉不起来才放弃)
    if not cdp_ok():
        _log("· 调试 Chrome 没开着, 正在自动拉起…")
        if amazon.ensure_cdp():
            _log("✔ 调试 Chrome 已就绪")
        else:
            _log(f"✗ 连不上调试 Chrome({config.CDP_URL}) 且自动拉起失败, 请手动启动: "
                 f"chrome --remote-debugging-port=9222 --user-data-dir=C:\\ChromeDebugUser")
            _set(running=False, error=f"调试 Chrome 不可用: {config.CDP_URL}",
                 finished_at=db.now())
            return

    ok = skipped = failed = 0
    real = 0                 # 真实抓取次数(用于节流计数)
    blocked_streak = 0
    fail_streak = 0          # 连续失败次数(熔断用)
    done = 0
    total = len(items)
    aborted = False

    for gi, g in enumerate(groups, 1):
        if stop.is_set() or aborted:
            break
        parent = g["parent"]
        _set(parent=parent, parent_index=gi, parent_terms=len(g["terms"]), current="")

        # 已标记完成的父 ASIN 整组跳过(除非要求重抓)
        if not refresh and skip_done_parents and amazon_store.parent_done(parent):
            _log(f"· [{gi}/{len(groups)}] 父ASIN {parent} 已标记完成, 整组跳过")
            _set(parents_done=_STATE["parents_done"] + 1)
            continue

        _log(f"▶ [{gi}/{len(groups)}] 父ASIN {parent}: {len(g['terms'])} 个搜索词 "
             f"(点击合计 {g['clicks']})")
        p_ok = p_skip = p_fail = 0

        for j, t in enumerate(g["terms"], 1):
            if stop.is_set():
                break
            q = t["query"]
            all_parents = t["parents"] or [parent]
            _set(done=done, ok=ok, skipped=skipped, failed=failed,
                 current=q, parent_index=gi)

            # 断点续抓: 本地已有数据 → 跳过; 只差主图就只补图片
            if not refresh and amazon_store.exists(q):
                skipped += 1
                p_skip += 1
                done += 1
                pending = amazon_store.images_pending(q, [parent]) if images else []
                if pending:
                    local = amazon_store.find(q) or {}
                    _log(f"· [{gi}.{j}] 已抓过, 补主图: {q}")
                    _save_images(q, pending, local.get("items") or [], image_limit)
                else:
                    _log(f"· [{gi}.{j}] 跳过(本地已有): {q}")
                _set(done=done, skipped=skipped)
                continue

            # 词间随机等待(第一条约等于直接开始, 之后每条都等)
            if real > 0:
                gap = random.uniform(gap_min, max(gap_min, gap_max))
                _log(f"   … 等待 {gap:.1f}s 后抓取")
                if stop.wait(gap):      # 等待期间被停止 → 立即退出, 不发起请求
                    _log("■ 收到停止请求, 中断本批次")
                    break

            try:
                res = amazon.fetch(q, refresh=True, use_cache=False,
                                   parents=all_parents, screenshot=screenshot,
                                   scrolls=scrolls)
            except Exception as e:      # noqa: BLE001
                failed += 1
                p_fail += 1
                real += 1
                done += 1
                fail_streak += 1
                _log(f"✗ [{gi}.{j}] 抓取异常: {q} | {str(e)[:120]}")
                _set(done=done, failed=failed)
                if fail_streak >= max_fail:
                    _log(f"■ 连续 {fail_streak} 次抓取失败, 中止本批次; 修好后重跑自动续抓")
                    _set(error=f"连续 {fail_streak} 次抓取失败(检查网络/调试浏览器)")
                    aborted = True
                    break
                continue

            real += 1
            done += 1
            n = res.get("item_count") or len(res.get("items") or [])
            if res.get("blocked"):
                blocked_streak += 1
                failed += 1
                p_fail += 1
                _log(f"⚠ [{gi}.{j}] 疑似被拦截(验证码): {q} | 第 {blocked_streak} 次")
                _set(done=done, failed=failed, blocked=blocked_streak)
                if blocked_streak >= max_blocked:
                    _log(f"■ 连续 {blocked_streak} 次被拦截, 中止本批次; "
                         f"{int(block_backoff)} 秒内不再访问")
                    if stop.wait(block_backoff):
                        break
                    blocked_streak = 0
                    continue
                if stop.wait(block_backoff):
                    break
                continue

            blocked_streak = 0
            fail_streak = 0
            ok += 1
            p_ok += 1
            _set(done=done, ok=ok)
            _log(f"✓ [{gi}.{j}/{len(g['terms'])}] {q} → {n} 条"
                 + ("  [美元!]" if res.get("usd") else ""))

            if images:
                _save_images(q, all_parents, res.get("items") or [], image_limit)

            # 批内长休: 降低长时间连续访问的密度
            if batch_size > 0 and real % batch_size == 0 and done < total:
                _log(f"   … 已抓 {real} 条, 长休 {int(rest_sec)}s")
                if stop.wait(rest_sec):
                    _log("■ 收到停止请求, 中断本批次")
                    break

        # 一组(一个父 ASIN)处理完 → 标记
        if stop.is_set() or aborted:
            break
        if p_fail:
            _log(f"! 父ASIN {parent}: {p_fail} 个词失败, 暂不标记完成(重跑会补)")
            continue
        ent = amazon_store.mark_parent_done(parent, {
            "terms": len(g["terms"]), "terms_ok": p_ok, "terms_skipped": p_skip,
            "terms_failed": p_fail, "clicks": g["clicks"], "adCost": g["adCost"],
            "images": amazon_store.image_count(parent),
        })
        _set(parents_done=_STATE["parents_done"] + 1)
        _log(f"✔ 父ASIN {parent} 抓取完成: {len(g['terms'])} 个词 / "
             f"{ent['images']} 张主图 → 已标记 _done.json")

    _set(running=False, finished_at=db.now(), current="")
    st = amazon_store.stats()
    _log(f"■ 结束: 新抓 {ok} · 跳过 {skipped} · 失败 {failed} · "
         f"父ASIN {st.get('parents_done', 0)}/{len(groups)} 已标记完成 · "
         f"本地 {st['terms']} 词 / {st['images']} 张主图")


# ---------------------------------------------------------------------------
# 对外: 后台启动 / 同步跑完 / 停止
# ---------------------------------------------------------------------------
def _reset(min_clicks: int) -> None:
    with _STATE_LOCK:
        _STATE.update({"running": True, "stopped": False, "min_clicks": min_clicks,
                       "total": 0, "index": 0, "done": 0, "ok": 0, "skipped": 0,
                       "failed": 0, "images": 0, "img_new": 0, "img_failed": 0,
                       "blocked": 0, "current": "", "error": None,
                       "parents_total": 0, "parents_done": 0, "parent": "",
                       "parent_index": 0, "parent_terms": 0,
                       "started_at": db.now(), "finished_at": None, "log": []})
    _STOP.clear()


def _kwargs(min_clicks: int, limit: int, refresh: bool, gap_min: Optional[float],
            gap_max: Optional[float], batch_size: Optional[int],
            rest_sec: Optional[float], screenshot: bool, scrolls: int,
            images: bool, image_limit: int, skip_done_parents: bool) -> Dict[str, Any]:
    return dict(
        min_clicks=config.AMAZON_PREFETCH_MIN_CLICKS if min_clicks is None else min_clicks,
        limit=limit, refresh=refresh,
        gap_min=config.AMAZON_PREFETCH_GAP_MIN_SEC if gap_min is None else gap_min,
        gap_max=config.AMAZON_PREFETCH_GAP_MAX_SEC if gap_max is None else gap_max,
        batch_size=(config.AMAZON_PREFETCH_BATCH_SIZE
                    if batch_size is None else batch_size),
        rest_sec=(config.AMAZON_PREFETCH_REST_SEC if rest_sec is None else rest_sec),
        block_backoff=config.AMAZON_PREFETCH_BLOCK_BACKOFF_SEC,
        max_blocked=config.AMAZON_PREFETCH_MAX_BLOCKED,
        max_fail=config.AMAZON_PREFETCH_MAX_FAIL,
        screenshot=screenshot, scrolls=scrolls, images=images,
        image_limit=image_limit, stop=_STOP, skip_done_parents=skip_done_parents)


def _run_guarded(**kw: Any) -> None:
    # 跨进程互斥: CLI 与网页按钮共用一把锁, 不然并发抓取会让访问量翻倍
    if not amazon_store.acquire_run_lock("prefetch"):
        info = amazon_store.run_lock_info() or {}
        msg = (f"已有抓取在跑(PID {info.get('pid')} @ {info.get('at')}), "
               f"本次不重复启动")
        _set(running=False, error=msg, finished_at=db.now())
        _log(f"✗ {msg}")
        return
    try:
        _worker(**kw)
    except Exception as e:      # noqa: BLE001  兜底: 任何异常都要把 running 复位
        _set(running=False, error=str(e), finished_at=db.now())
        _log(f"✗ 任务异常终止: {e}")
    finally:
        amazon_store.release_run_lock()


def _check_free() -> None:
    """启动前先看有没有别的抓取在跑(CLI/网页/另一个进程)"""
    info = amazon_store.run_lock_info()
    if info:
        raise RuntimeError(
            f"已有抓取在跑: PID {info.get('pid')} ({info.get('kind')}) "
            f"@ {info.get('at')}, 等它结束或先停止再试")


def start(min_clicks: int = 2, limit: int = 0, refresh: bool = False,
          gap_min: Optional[float] = None, gap_max: Optional[float] = None,
          batch_size: Optional[int] = None, rest_sec: Optional[float] = None,
          screenshot: bool = True, scrolls: int = 2, images: bool = True,
          image_limit: int = 0, skip_done_parents: bool = True) -> Dict[str, Any]:
    """后台启动一次批量预抓取(不阻塞); 已在运行时抛错"""
    global _THREAD
    with _STATE_LOCK:
        if _STATE["running"]:
            raise RuntimeError("预抓取任务已在运行中")
    _check_free()
    _reset(min_clicks)
    _THREAD = threading.Thread(
        target=lambda: _run_guarded(
            **_kwargs(min_clicks, limit, refresh, gap_min, gap_max, batch_size,
                      rest_sec, screenshot, scrolls, images, image_limit,
                      skip_done_parents)),
        name="amazon-prefetch", daemon=True)
    _THREAD.start()
    return {"started": True, "min_clicks": min_clicks,
            "root": amazon_store.root()}


def run_blocking(min_clicks: int = 2, limit: int = 0, refresh: bool = False,
                 gap_min: Optional[float] = None, gap_max: Optional[float] = None,
                 batch_size: Optional[int] = None, rest_sec: Optional[float] = None,
                 screenshot: bool = True, scrolls: int = 2, images: bool = True,
                 image_limit: int = 0, skip_done_parents: bool = True) -> Dict[str, Any]:
    """同步跑完一批(供 CLI 使用), 返回最终统计"""
    with _STATE_LOCK:
        if _STATE["running"]:
            raise RuntimeError("预抓取任务已在运行中")
    _check_free()
    _reset(min_clicks)
    _run_guarded(**_kwargs(min_clicks, limit, refresh, gap_min, gap_max, batch_size,
                           rest_sec, screenshot, scrolls, images, image_limit,
                           skip_done_parents))
    return get_state()


def stop() -> Dict[str, Any]:
    """请求停止(当前这条抓完即退出); 已抓成果保留"""
    _STOP.set()
    _set(stopped=True)
    return {"stopping": True, "running": _STATE["running"]}


def wait(timeout: Optional[float] = None, poll: float = 2.0) -> Dict[str, Any]:
    """等待后台任务结束(CLI 用)"""
    t0 = time.time()
    while _STATE["running"]:
        if timeout and time.time() - t0 > timeout:
            break
        time.sleep(poll)
    return get_state()
