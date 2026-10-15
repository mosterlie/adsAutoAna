# -*- coding: utf-8 -*-
"""搜索词中文翻译: 亚马逊(日本站)搜索词 → 简体中文短语

- 复用 ai_label 的模型管道与配置(workbuddy / 本地 ollama, 页面「配置」可切换)
- 结果统一落 translations 表(kind=search_term), 永久缓存: 已翻译过的不重复调模型
- 生成走后台线程串行(推理型模型单条约 8 秒), 前端用 /api/translate/status 轮询
- 「父SKU品名」的翻译(ai_label)也同步写入同一张表(kind=product_name)
"""
import threading
from typing import Any, Dict, List, Tuple

import config
from backend import ai_label
from backend import database as db

_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}

KIND_TERM = "search_term"        # 搜索词
KIND_PRODUCT = "product_name"    # 父SKU品名 (由 ai_label 写入)

_PROMPT = (
    "你是跨境电商关键词翻译助手。下面是一个亚马逊日本站的用户搜索词，"
    "请翻译成简体中文，表达出用户想买的商品是什么。\n"
    "要求：只输出中文短语本身，不超过 12 个字；"
    "不要拼音、不要日文假名、不要引号、不要解释。\n\n"
    "示例：\n"
    "搜索词：ガスコンロ\n中文：燃气灶\n"
    "搜索词：ピックルボールネット\n中文：匹克球网\n"
    "搜索词：有刺鉄線 獣よけ\n中文：铁丝网防兽\n"
    "搜索词：シフトノブ おもしろ\n中文：排挡头个性\n\n"
    "搜索词：{q}\n中文："
)


def is_asin(s: Any) -> bool:
    """ASIN 形态(10 位大写字母/数字)——搜索词页签里混着 ASIN 定位行, 不需要翻译"""
    s = str(s or "").strip()
    return len(s) == 10 and s.isalnum() and s == s.upper()


def clean_zh(text: str, limit: int = 20) -> str:
    """把模型输出规整成短语: 去掉前缀/引号/标点, 只留正文"""
    raw = (text or "").strip()
    if not raw:
        return ""
    line = raw.splitlines()[0].strip()
    return ai_label._clean_label(line, max_words=8)[:limit]


def _generate_one(text: str, cfg: Dict[str, Any]) -> Tuple[str, str]:
    """返回 (中文短语, 模型名); 模型管道与「配置」里的品名解析共用"""
    prompt = _PROMPT.format(q=str(text)[:120])
    if cfg.get("provider") == "workbuddy":
        raw = ai_label._workbuddy_generate(prompt, cfg)
        model = cfg.get("workbuddy_model") or config.WORKBUDDY_MODEL
    else:
        raw = ai_label._ollama_generate(prompt, cfg)
        model = cfg.get("ollama_model") or config.OLLAMA_MODEL
    return clean_zh(raw), model


# ---------------------------------------------------------------------------
# 缓存 + 后台生成 (结构与 ai_label 的 job 一致)
# ---------------------------------------------------------------------------
def ensure(kind: str, sources: List[str], refresh: bool = False) -> Dict[str, Any]:
    """返回已缓存的翻译; 缺失的启动后台生成(不阻塞)"""
    items = [str(s).strip() for s in (sources or []) if str(s).strip()]
    items = list(dict.fromkeys(items))
    if kind == KIND_TERM:
        items = [s for s in items if not is_asin(s)]
    if not items:
        return {"translations": {}, "pending": [], "job": None}

    cached = {} if refresh else db.get_translations(kind, items)
    translations = {s: cached[s] for s in items if s in cached}
    pending = [s for s in items if s not in cached]
    job = _start(kind, items, pending) if pending else None
    return {"translations": translations, "pending": pending, "job": job}


def _start(kind: str, all_items: List[str], pending: List[str]) -> Dict[str, Any]:
    """启动后台生成; key 用请求的完整列表(与 ai_label 同一套约定)"""
    key = _key_of(all_items)
    with _LOCK:
        cur = JOBS.get(key)
        if cur and cur.get("running"):
            return {"running": True, "done": cur["done"], "total": cur["total"]}
        JOBS[key] = {"running": True, "done": 0, "total": len(pending),
                     "current": "", "ok": 0, "failed": 0, "error": ""}
    t = threading.Thread(target=_work, args=(key, kind, pending),
                         name=f"translate-{kind}", daemon=True)
    t.start()
    return {"running": True, "done": 0, "total": len(pending)}


def _key_of(items: List[str]) -> str:
    return "|".join(sorted(str(s).strip() for s in (items or []) if str(s).strip()))


def job_state(kind: str, sources: List[str]) -> Dict[str, Any]:
    with _LOCK:
        j = JOBS.get(_key_of([s for s in (sources or []) if not is_asin(s)]))
        return dict(j) if j else {"running": False, "done": 0, "total": 0}


def _work(key: str, kind: str, sources: List[str]) -> None:
    """串行生成; 逐条落库, 任何单条失败不影响其它"""
    cfg = ai_label.get_config()
    for i, s in enumerate(sources, 1):
        with _LOCK:
            if key in JOBS:
                JOBS[key]["current"] = s
        try:
            zh, model = _generate_one(s, cfg)
            if zh:
                db.save_translation(kind, s, zh, model)
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
