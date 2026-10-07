# -*- coding: utf-8 -*-
"""用本地 Ollama 模型解析商品标题 → 提炼「这是什么」的短语(≤5 个词)

- 结果按 ASIN 缓存到 ol_ai_labels 表; 标题变了(哈希不同)才重新解析
- 一页可能有几十个 ASIN, 逐个跑模型较慢 → 后台线程串行生成, 前端轮询结果
- 本机地址强制绕过系统代理 (否则 Clash 之类会把 127.0.0.1 也拦成 502)
"""
import hashlib
import json
import re
import threading
import urllib.request
from typing import Any, Dict, List, Optional

import config
from backend import database as db

_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_PROMPT = (
    "你是电商标题解析助手。请判断下面这个亚马逊商品标题里，"
    "这个商品**本身是什么**（品类/主体），用不超过{maxw}个中文词回答。\n"
    "要求：只描述商品本体，不要罗列用途/场景/卖点；只输出短语本身，"
    "不要标点、不要引号、不要解释。\n\n"
    "示例：\n"
    "标题：水道ホースリール 巻き取りやすい 洗車 庭 園芸 農場 屋外\n"
    "短语：水管卷盘\n"
    "标题：四角型工具バケツ 電工バケツ 荷揚げ袋 道具袋 帆布製 吊り下げフック付き\n"
    "短语：帆布工具袋\n"
    "标题：バクテリア培養リング ろ材 水槽用濾材 多孔質構造 魚缸用 水槽用品\n"
    "短语：细菌培养环\n\n"
    "标题：{title}\n"
    "短语："
)


def _clean_label(text: str, max_words: int = 5) -> str:
    """把模型输出规整成短标签"""
    raw = (text or "").strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    line = re.sub(r'^(短?语|答案|商品|标签)\s*[:：]\s*', "", line)
    line = line.strip('"\'“”‘’「」『』【】《》 \t')
    line = re.sub(r"[，。、,\.；;：:！!？?~～\-—|/\\]+", " ", line)
    tokens = [t for t in line.split() if t]
    if not tokens:
        return ""
    label = " ".join(tokens[:max_words]) if len(tokens) > 1 else tokens[0]
    return label[:24]


def _generate(title: str) -> str:
    prompt = _PROMPT.format(maxw=config.OLLAMA_LABEL_MAX_WORDS, title=(title or "")[:300])
    body = json.dumps({
        "model": config.OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 32, "top_p": 0.9},
    }).encode("utf-8")
    req = urllib.request.Request(
        config.OLLAMA_URL + "/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=config.OLLAMA_TIMEOUT) as r:
        data = json.loads(r.read().decode("utf-8"))
    return _clean_label(data.get("response") or "", config.OLLAMA_LABEL_MAX_WORDS)


def available() -> bool:
    """Ollama 是否可用"""
    try:
        with _OPENER.open(config.OLLAMA_URL + "/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:       # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# 缓存 + 后台生成
# ---------------------------------------------------------------------------
def ensure_labels(asins: List[str], refresh: bool = False) -> Dict[str, Any]:
    """返回已缓存的标签; 缺失的启动后台生成"""
    items = [str(a).strip() for a in (asins or []) if str(a).strip()]
    items = list(dict.fromkeys(items))
    if not items:
        return {"labels": {}, "pending": [], "job": None}

    titles = db.get_online_titles(items)              # asin -> {title, title_hash}
    cached = {} if refresh else db.get_ai_labels(items)

    labels: Dict[str, str] = {}
    pending: List[str] = []
    for a in items:
        t = titles.get(a) or {}
        th = t.get("title_hash") or ""
        hit = cached.get(a)
        if hit and hit.get("label") and (not th or hit.get("title_hash") == th):
            labels[a] = hit["label"]
        elif t.get("title"):
            pending.append(a)

    job = None
    if pending:
        job = _start(pending, titles)
    return {"labels": labels, "pending": pending, "job": job}


def _start(asins: List[str], titles: Dict[str, Dict[str, str]]) -> Dict[str, Any]:
    key = "|".join(sorted(asins))
    with _LOCK:
        cur = JOBS.get(key)
        if cur and cur.get("running"):
            return {"running": True, "done": cur["done"], "total": cur["total"]}
        JOBS[key] = {"running": True, "done": 0, "total": len(asins),
                     "current": "", "ok": 0, "failed": 0, "error": ""}
    t = threading.Thread(target=_work, args=(key, asins, titles), daemon=True)
    t.start()
    return {"running": True, "done": 0, "total": len(asins)}


def job_state(asins: List[str]) -> Dict[str, Any]:
    key = "|".join(sorted(str(a).strip() for a in (asins or []) if str(a).strip()))
    with _LOCK:
        j = JOBS.get(key)
        return dict(j) if j else {"running": False, "done": 0, "total": 0}


def _work(key: str, asins: List[str], titles: Dict[str, Dict[str, str]]) -> None:
    for i, a in enumerate(asins, 1):
        title = (titles.get(a) or {}).get("title") or ""
        with _LOCK:
            if key in JOBS:
                JOBS[key]["current"] = a
        try:
            label = _generate(title)
            if label:
                db.save_ai_label(a, label, config.OLLAMA_MODEL,
                                 (titles.get(a) or {}).get("title_hash") or "")
                with _LOCK:
                    JOBS[key]["ok"] += 1
            else:
                with _LOCK:
                    JOBS[key]["failed"] += 1
        except Exception as e:      # noqa: BLE001
            with _LOCK:
                JOBS[key]["failed"] += 1
                JOBS[key]["error"] = str(e)[:140]
        with _LOCK:
            JOBS[key]["done"] = i
    with _LOCK:
        if key in JOBS:
            JOBS[key]["running"] = False
            JOBS[key]["current"] = ""


def title_hash(title: Optional[str]) -> str:
    return hashlib.md5((title or "").strip().encode("utf-8")).hexdigest()[:16]
