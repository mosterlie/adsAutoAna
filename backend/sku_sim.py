# -*- coding: utf-8 -*-
"""整体相似度: 「我的品(标题+主图)」 × 「竞品(标题+主图)」 的一次性判断

与旧的 title_sim / image_sim 的区别:
  旧: 标题比标题、主图比主图, 两个分数各自独立, 谁也不能否决谁
      (常见: 标题同类 90 分, 图片完全不是一回事)。
  新: 把「我的标题 + 我的主图 + 竞品标题 + 竞品主图」作为**一个整体**送进同一个
      多模态大模型, 一次给出「这两条链接是不是同一种东西」的 0~100 分 + 理由。

图片取用顺序(竞品侧):
  ① 抓取时已下载到本地的原图 <根目录>/<父ASIN>/主图/<词>/<顺位>_<ASIN>.<ext>
  ② 回落到 amz_results.image (亚马逊 CDN)

不再做本地感知特征(dHash/直方图)兜底 —— 那是「图比图」的思路, 与本模块口径不符;
模型失败就在该条上写明失败原因, 由前端显示失败, 不伪造分数。

任务化: 视觉模型单次 2~40s, 故按后台任务跑(并发 WB_VISION_WORKERS), 前端轮询进度。
持久化: sku_similarity 表, 按 (父ASIN, 词, 对方ASIN) 覆盖更新。
"""
import base64
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Dict, List, Optional

import config
from backend import amazon_store
from backend import database as db
from backend import image_sim
from backend import wb_config

_JOBS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()


def vision_model() -> str:
    """当前「配置管理」页选定的视觉模型"""
    return image_sim.vision_model()


def _prompt(my_title: str, other_title: str) -> str:
    from backend import prompts
    return prompts.render(prompts.PRODUCT_PAIR_PROMPT,
                          a=str(my_title or "")[:400],
                          b=str(other_title or "")[:400])


# ---------------------------------------------------------------------------
# 图片 -> base64
# ---------------------------------------------------------------------------
def _img_b64(img) -> str:
    """缩放 + JPEG + base64 (控制请求体大小)"""
    img = img.convert("RGB")
    img.thumbnail((config.WB_VISION_IMG_MAX, config.WB_VISION_IMG_MAX))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=config.WB_VISION_IMG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode()


def b64_by_url(url: str) -> str:
    return _img_b64(image_sim._fetch_img(image_sim._big_url(str(url or ""))))


def _local_file(parent: str, query: str, item: Dict[str, Any]) -> str:
    """该竞品主图的本地文件(抓取时已下载); 没有则返回 ""

    文件名规则与 amazon_images.file_name 一致: <顺位:02d>_<ASIN>.<ext>
    """
    try:
        pos = int(item.get("position") or 0)
    except (TypeError, ValueError):
        return ""
    asin = str(item.get("asin") or "").strip()
    if not (parent and query and pos and asin):
        return ""
    try:
        d = amazon_store.image_dir(parent, query)
    except Exception:                           # noqa: BLE001
        return ""
    stem = f"{pos:02d}_{asin}"
    for ext in amazon_store.IMAGE_EXTS:
        p = os.path.join(d, stem + ext)
        if os.path.exists(p):
            return p
    return ""


def _b64_local(path: str) -> str:
    from PIL import Image
    with Image.open(path) as im:
        im.load()
        return _img_b64(im)


def other_b64(parent: str, term: str, item: Dict[str, Any]) -> Optional[str]:
    """竞品主图 base64: 本地优先, 失败回落 CDN; 都失败返回 None"""
    p = _local_file(parent, term, item)
    if p:
        try:
            return _b64_local(p)
        except Exception:                       # noqa: BLE001  本地文件坏了就走 CDN
            pass
    return b64_by_url(item.get("image") or "")


# ---------------------------------------------------------------------------
# 大模型整体判断
# ---------------------------------------------------------------------------
def judge(my_title: str, my_b64: Optional[str], other_title: str,
          other_b64_: Optional[str], model: str) -> Dict[str, Any]:
    """我的品(标题+主图) × 竞品(标题+主图) → {score, same, reason, model}

    两张图各自带文字标签, 缺图(我的主图拿不到/竞品图下载失败)时只送已有信息,
    由模型按已有证据判断, 不因此直接判失败。
    """
    content: List[Dict[str, Any]] = [
        {"type": "text", "text": _prompt(my_title, other_title)}]
    if my_b64:
        content.append({"type": "text", "text": "第1张(我的商品主图):"})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + my_b64}})
    if other_b64_:
        content.append({"type": "text", "text": "第2张(竞品主图):"})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64," + other_b64_}})
    r = wb_config.client(model, "vision").chat_raw(
        {"messages": [{"role": "user", "content": content}]},
        max_tokens=config.WB_VISION_MAX_TOKENS)
    out = image_sim._parse_verdict(r.content or "")
    out["model"] = f"vision:{model}"
    return out


# ---------------------------------------------------------------------------
# 后台任务
# ---------------------------------------------------------------------------
def _key(asin: str, term: str) -> str:
    return f"{asin}\x00{term}"


def _item(r: Dict[str, Any], hit: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    it = {"position": r.get("position"), "asin": r.get("asin"),
          "title": (r.get("title") or "")[:80], "image": r.get("image"),
          "score": None, "reason": "", "same": None, "via": "llm"}
    if hit and hit.get("score") is not None:
        it.update({"score": hit["score"], "reason": hit.get("reason") or "",
                   "same": hit.get("same_category"), "cached": True})
    return it


def _finish(base: Dict[str, Any], items: List[Dict[str, Any]]) -> None:
    scores = [i["score"] for i in items if i.get("score") is not None]
    same = sum(1 for s in scores if s >= 70)
    base["avg"] = round(sum(scores) / len(scores), 1) if scores else None
    base["max"] = max(scores) if scores else None
    base["match_rate"] = round(100 * same / len(scores), 1) if scores else None


def start_job(asin: str, term: str, top_n: int = 10,
              refresh: bool = False) -> Dict[str, Any]:
    """启动整体相似度任务

    refresh=False: 已落库的 (父ASIN, 词, 对方ASIN) 直接回填, 不重复调模型;
    refresh=True : 忽略缓存, 全部重算(「重新评估」按钮用)。
    """
    asin = str(asin or "").strip()
    term = str(term or "").strip()
    top_n = max(1, min(int(top_n or 10), 20))
    k = _key(asin, term)

    my_title = db.get_parent_title(asin)
    my_image = db.get_parent_main_image(asin)
    rows = [r for r in db.latest_amazon_results(term, top_n) if r.get("image")][:top_n]
    cached = {} if refresh else db.get_sku_sims(asin, term)
    items = [_item(r, cached.get(str(r.get("asin") or ""))) for r in rows]

    base: Dict[str, Any] = {
        "running": False, "asin": asin, "term": term,
        "my_title": my_title, "my_image": my_image,
        "model": vision_model(), "items": items, "total": len(rows),
        "done": sum(1 for i in items if i.get("cached")),
        "avg": None, "max": None, "match_rate": None, "note": "",
    }
    _finish(base, items)
    if not my_title:
        base["note"] = "未找到我的商品标题"
        return base
    if not rows:
        base["note"] = "该词暂无亚马逊搜索结果缓存（先加载搜索结果再比对）"
        return base

    pending = [(idx, r) for idx, (r, i) in enumerate(zip(rows, items))
               if i.get("score") is None]
    if not pending:                             # 全部命中缓存
        return base

    base["running"] = True
    with _LOCK:
        cur = _JOBS.get(k)
        if cur and cur.get("running"):
            return dict(cur)
        _JOBS[k] = base                           # 存原对象, _work 就地更新
        snapshot = dict(base)
    threading.Thread(target=_work, args=(k, asin, term, my_title, my_image, pending),
                     daemon=True).start()
    return snapshot


def _work(k: str, parent: str, term: str, my_title: str, my_image: str,
          targets: List[Any]) -> None:
    """targets: [(items 下标, 结果行)] —— 只处理未命中缓存的行"""
    model = vision_model()
    my_b64: Optional[str] = None
    if my_image:
        try:
            my_b64 = b64_by_url(my_image)
        except Exception:                       # noqa: BLE001  我的主图拿不到: 只用文字+竞品图
            my_b64 = None
    with _LOCK:
        base_done = _JOBS[k]["done"] if k in _JOBS else 0
    done = {"n": base_done}

    def _one(idx: int, r: Dict[str, Any]) -> None:
        item = _item(r)
        try:
            j = judge(my_title, my_b64, r.get("title") or "",
                      other_b64(parent, term, r), model)
            item.update({"score": j["score"], "same": j["same"],
                         "reason": j["reason"]})
            db.save_sku_sim(parent, term, str(r.get("asin") or ""),
                            r.get("position"), j["score"], j["same"],
                            j["reason"], j.get("model") or "",
                            str(r.get("title") or "")[:400], str(r.get("image") or ""),
                            my_title[:400], my_image or "")
        except Exception as e:                  # noqa: BLE001
            item["reason"] = f"模型失败: {str(e)[:40]}"
        with _LOCK:
            if k in _JOBS:
                _JOBS[k]["items"][idx] = item
                done["n"] += 1
                _JOBS[k]["done"] = done["n"]

    with ThreadPoolExecutor(max_workers=config.WB_VISION_WORKERS) as ex:
        list(ex.map(lambda p: _one(*p), targets))

    with _LOCK:
        job = _JOBS.get(k)
        if job:
            _finish(job, job["items"])
            job["running"] = False
            job["finished_at"] = db.now()


def job_status(asin: str, term: str) -> Dict[str, Any]:
    asin, term = str(asin or "").strip(), str(term or "").strip()
    with _LOCK:
        cur = _JOBS.get(_key(asin, term))
        if cur:
            return dict(cur)
    # 无内存任务: 用落库结果回填(服务重启后仍可直接查看历史结果)
    cached = db.get_sku_sims(asin, term)
    if cached:
        rows = [r for r in db.latest_amazon_results(term, 20) if r.get("image")]
        items = [_item(r, cached.get(str(r.get("asin") or ""))) for r in rows]
        base: Dict[str, Any] = {
            "running": False, "asin": asin, "term": term, "items": items,
            "my_title": db.get_parent_title(asin),
            "my_image": db.get_parent_main_image(asin),
            "model": next((cached[x].get("model") for x in cached
                           if cached[x].get("model")), ""),
            "total": len(items), "done": len(items), "note": "库中历史结果",
        }
        _finish(base, items)
        return base
    return {"running": False, "asin": asin, "term": term, "items": [],
            "total": 0, "done": 0, "avg": None, "max": None,
            "match_rate": None, "note": "尚未做整体相似度比对"}
