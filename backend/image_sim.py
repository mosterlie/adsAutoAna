# -*- coding: utf-8 -*-
"""深度检测: SKU 主图 × 亚马逊搜索结果主图 的相似度

比较方式(首选大模型):
  把「SKU 主图」和「该词亚马逊搜索结果的前 N 张主图」逐对送入**配置的视觉大模型**
  (配置管理页可选, 默认 glm-5v-turbo), 让模型判断是否同类/同款并给出 0~100 分 + 理由。

兜底:
  大模型不可用/单张失败时, 回落到本地感知特征比对
  (0.6×dHash 哈希 + 0.4×RGB 直方图), 保证仍有分值。

任务化:
  视觉模型单次 2~40s, 故深度检测以**后台任务**方式跑(并发 WB_VISION_WORKERS),
  前端轮询进度, 结果逐条回填。每张图的本地特征按 URL 缓存(image_phash 表)。
"""
import base64
import json
import re
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import Any, Dict, List, Optional

import config
from backend import database as db
from backend import wb_config

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_SIZE_SUFFIX = re.compile(r"\._[^/./]+\.(jpg|jpeg|png|webp|gif)$", re.I)

_JOBS: Dict[str, Dict[str, Any]] = {}
_JOB_LOCK = threading.Lock()

def _vision_prompt() -> str:
    """统一口径的图片判断提示词(backend/prompts.py, 与标题/词级共用同一评分标尺)"""
    from backend import prompts
    return prompts.render(prompts.IMAGE_PAIR_PROMPT)


def _big_url(u: str) -> str:
    return _SIZE_SUFFIX.sub(r".\1", str(u or ""))


def vision_model() -> str:
    from backend.relevance import current_model
    return current_model("vision")


# ---------------------------------------------------------------------------
# 本地兜底: dHash + 颜色直方图
# ---------------------------------------------------------------------------
def _dhash_bits(img) -> List[bool]:
    g = img.convert("L").resize((9, 8))
    px = g.load()
    return [px[x, y] > px[x + 1, y] for y in range(8) for x in range(8)]


def _hist_vec(img) -> List[float]:
    small = img.convert("RGB").resize((64, 64))
    h = small.histogram()
    vec: List[float] = []
    for c in range(3):
        ch = h[c * 256:(c + 1) * 256]
        bins = [0.0] * 8
        for i, v in enumerate(ch):
            bins[min(7, i * 8 // 256)] += v
        total = sum(bins) or 1.0
        vec.extend(b / total for b in bins)
    return vec


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = (sum(x * x for x in a) ** 0.5) or 1.0
    nb = (sum(y * y for y in b) ** 0.5) or 1.0
    return max(0.0, min(1.0, dot / (na * nb)))


def _fetch_img(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _OPENER.open(req, timeout=timeout) as r:
        data = r.read()
    from PIL import Image
    img = Image.open(BytesIO(data))
    img.load()
    return img


def _features(url: str) -> Optional[Dict[str, Any]]:
    """本地感知特征 (DB 缓存优先)"""
    if not url:
        return None
    hit = db.get_image_phash(url)
    if hit:
        return hit
    try:
        img = _fetch_img(_big_url(url))
        feat = {"dhash": "".join("1" if b else "0" for b in _dhash_bits(img)),
                "hist": _hist_vec(img)}
        db.save_image_phash(url, feat["dhash"], feat["hist"])
        return feat
    except Exception:                           # noqa: BLE001
        return None


def _bits(s: str) -> List[bool]:
    return [c == "1" for c in s]


def phash_similarity(f1: Dict[str, Any], f2: Dict[str, Any]) -> int:
    same = sum(1 for a, b in zip(_bits(f1["dhash"]), _bits(f2["dhash"])) if a == b)
    return round(100 * (0.6 * (same / 64.0) + 0.4 * _cosine(f1["hist"], f2["hist"])))


# ---------------------------------------------------------------------------
# 大模型视觉比较
# ---------------------------------------------------------------------------
def _b64(url: str) -> str:
    """下载 -> 缩放 -> JPEG -> base64 (控制请求体大小)"""
    img = _fetch_img(_big_url(url))
    img = img.convert("RGB")
    img.thumbnail((config.WB_VISION_IMG_MAX, config.WB_VISION_IMG_MAX))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=config.WB_VISION_IMG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode()


def _client(model: str):
    """透传模式客户端(由 backend/wb_config.py 统一构造与缓存)"""
    return wb_config.client(model, "vision")


def _parse_verdict(raw: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise RuntimeError("模型未返回 JSON")
    j = json.loads(m.group(0))
    score = int(float(j.get("score")))
    return {"score": max(0, min(100, score)),
            "same": bool(j.get("same")),
            "reason": str(j.get("reason") or "")[:60]}


def vision_judge(main_b64: str, other_b64: str, model: str) -> Dict[str, Any]:
    """把两张图交给视觉模型判断, 返回 {score, same, reason, model}"""
    from backend import llm
    r = _client(model).chat_raw(
        llm.vision_messages(_vision_prompt(), [main_b64, other_b64]),
        max_tokens=config.WB_VISION_MAX_TOKENS)
    out = _parse_verdict(r.content or "")
    out["model"] = f"vision:{model}"
    return out


# ---------------------------------------------------------------------------
# 后台任务: 逐张比对并回填结果
# ---------------------------------------------------------------------------
def _key(asin: str, term: str) -> str:
    return f"{asin}\x00{term}"


def start_job(asin: str, term: str, top_n: int = 10,
              engine: str = "llm") -> Dict[str, Any]:
    """启动深度检测任务 (engine=llm 用配置的视觉模型; engine=phash 用本地算法)

    已有落库结果的 (父ASIN, 词, 对方ASIN) 直接回填, 不重复调用模型。
    """
    asin = str(asin or "").strip()
    term = str(term or "").strip()
    top_n = max(1, min(int(top_n or 10), 20))
    k = _key(asin, term)

    main_url = db.get_parent_main_image(asin)
    rows = [r for r in db.latest_amazon_results(term, top_n) if r.get("image")][:top_n]
    cached = db.get_image_sims(asin, term)
    items: List[Dict[str, Any]] = []
    for r in rows:
        it = {"position": r.get("position"), "asin": r.get("asin"),
              "image": r.get("image"), "title": (r.get("title") or "")[:60],
              "score": None, "reason": "", "via": ""}
        hit = cached.get(str(r.get("asin") or ""))
        if hit and hit.get("score") is not None:
            it.update({"score": hit["score"], "via": hit.get("via") or "",
                       "reason": hit.get("reason") or "", "cached": True})
        items.append(it)
    base: Dict[str, Any] = {
        "running": False, "asin": asin, "term": term, "engine": engine,
        "model": vision_model() if engine == "llm" else "local-phash",
        "main_image": main_url, "done": sum(1 for i in items if i.get("cached")),
        "total": len(rows), "items": items, "avg": None, "max": None, "note": "",
    }
    if not main_url:
        base["note"] = "未找到父 SKU 主图"
        return base
    if not rows:
        base["note"] = "该词暂无亚马逊搜索结果缓存（先加载搜索结果再深度匹配）"
        return base

    scores = [i["score"] for i in items if i.get("score") is not None]
    base["avg"] = round(sum(scores) / len(scores), 1) if scores else None
    base["max"] = max(scores) if scores else None

    pending = [(idx, r) for idx, (r, i) in enumerate(zip(rows, items))
               if i.get("score") is None]
    if not pending:                           # 全部命中缓存
        return base

    base["running"] = True
    with _JOB_LOCK:
        cur = _JOBS.get(k)
        if cur and cur.get("running"):
            return dict(cur)
        _JOBS[k] = base                       # 存原对象, _work 就地更新
        snapshot = dict(base)
    threading.Thread(target=_work, args=(k, main_url, pending, engine),
                     daemon=True).start()
    return snapshot


def _work(k: str, main_url: str, targets: List[Any], engine: str) -> None:
    """targets: [(items 下标, 结果行)] —— 只处理未命中缓存的行"""
    model = vision_model()
    main_b64: Optional[str] = None
    main_feat: Optional[Dict[str, Any]] = None
    if engine == "llm":
        try:
            main_b64 = _b64(main_url)
        except Exception:                       # noqa: BLE001
            main_b64 = None
    if main_b64 is None:
        main_feat = _features(main_url)
        engine = "phash"                        # 主图拿不到 -> 整体回落本地算法
        with _JOB_LOCK:
            if k in _JOBS:
                _JOBS[k]["engine"] = "phash"
                _JOBS[k]["model"] = "local-phash"
    with _JOB_LOCK:
        base_done = _JOBS[k]["done"] if k in _JOBS else 0
    done = {"n": base_done}

    def _one(idx: int, r: Dict[str, Any]) -> None:
        item = {"position": r.get("position"), "asin": r.get("asin"),
                "image": r.get("image"), "title": (r.get("title") or "")[:60],
                "score": None, "reason": "", "via": ""}
        ok = False
        if engine == "llm" and main_b64:
            try:
                j = vision_judge(main_b64, _b64(r["image"]), model)
                item.update({"score": j["score"], "reason": j["reason"],
                             "same": j["same"], "via": "llm"})
                ok = True
            except Exception as e:              # noqa: BLE001
                item["reason"] = f"模型失败({str(e)[:30]}), 已用本地算法"
        if not ok:
            try:
                f1 = main_feat or _features(main_url)
                f2 = _features(r["image"])
                if f1 and f2:
                    item.update({"score": phash_similarity(f1, f2), "via": "phash",
                                 "reason": item["reason"] or "本地感知特征比对"})
                else:
                    item["reason"] = item["reason"] or "图片下载失败"
            except Exception as e:              # noqa: BLE001
                item["reason"] = f"失败: {str(e)[:40]}"
        # 结果落库 (重算覆盖更新)
        if item.get("score") is not None:
            try:
                db.save_image_sim(k.split("\x00")[0], k.split("\x00")[1],
                                  str(item.get("asin") or ""), item.get("position"),
                                  item["score"], item.get("via") or "",
                                  item.get("reason") or "",
                                  model if item.get("via") == "llm" else "local-phash",
                                  item.get("image") or "")
            except Exception:                   # noqa: BLE001
                pass
        with _JOB_LOCK:
            if k in _JOBS:
                _JOBS[k]["items"][idx] = item
                done["n"] += 1
                _JOBS[k]["done"] = done["n"]

    with ThreadPoolExecutor(max_workers=config.WB_VISION_WORKERS) as ex:
        list(ex.map(lambda p: _one(*p), targets))

    with _JOB_LOCK:
        job = _JOBS.get(k)
        if job:
            scores = [i["score"] for i in job["items"] if i.get("score") is not None]
            job["avg"] = round(sum(scores) / len(scores), 1) if scores else None
            job["max"] = max(scores) if scores else None
            job["running"] = False
            job["finished_at"] = db.now()


def job_status(asin: str, term: str) -> Dict[str, Any]:
    with _JOB_LOCK:
        cur = _JOBS.get(_key(asin, term))
        if cur:
            return dict(cur)
    # 无内存任务: 用落库结果回填(服务重启后仍可直接查看历史检测)
    cached = db.get_image_sims(asin, term)
    if cached:
        rows = db.latest_amazon_results(term, 20)
        items: List[Dict[str, Any]] = []
        for r in rows:
            hit = cached.get(str(r.get("asin") or ""))
            it = {"position": r.get("position"), "asin": r.get("asin"),
                  "image": r.get("image"), "title": (r.get("title") or "")[:60],
                  "score": None, "reason": "", "via": ""}
            if hit and hit.get("score") is not None:
                it.update({"score": hit["score"], "via": hit.get("via") or "",
                           "reason": hit.get("reason") or "", "cached": True})
            items.append(it)
        scores = [i["score"] for i in items if i.get("score") is not None]
        model = next((cached[k].get("model") for k in cached
                      if cached[k].get("model")), "")
        return {"running": False, "asin": asin, "term": term, "engine": "llm",
                "model": model, "main_image": db.get_parent_main_image(asin),
                "items": items, "done": len(items), "total": len(items),
                "avg": round(sum(scores) / len(scores), 1) if scores else None,
                "max": max(scores) if scores else None, "note": "库中历史结果"}
    return {"running": False, "asin": asin, "term": term, "items": [],
            "done": 0, "total": 0, "note": "尚未进行深度检测"}
