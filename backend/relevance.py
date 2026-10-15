# -*- coding: utf-8 -*-
"""搜索词 × SKU 相关性分析

口径:
  对「花费(广告花费)超过阈值」的每个**搜索词**, 找到它所在**广告活动**里投放的
  全部 SKU(广告产品), 计算「这个词 × 这个 SKU」的相关性。

双层依据:
  1) 文本匹配(确定、可解释): 搜索词与商品标题的字符级重合度 + 命中片段
  2) 语义判断(大模型): 词与标题在语义上是否同类 + 一句话理由
     首选 WorkBuddy API(腾讯 copilot, 配置见 config.WB_*), 失败时回落本地 Ollama

Ollama/WorkBuddy 结果按 (term, title_hash) 缓存到 term_relevance 表;
一条词的多个 SKU 常是同一标题(变体), 按 (term, 标题) 去重后调用模型,
避免重复请求。
"""
import json
import re
import sys
import threading
import urllib.request
from collections import defaultdict
from typing import Any, Dict, List, Optional

import config

# workbuddy 客户端包位于 testApi 项目
if "/Users/gx/Desktop/mypro/testApi" not in sys.path:
    sys.path.insert(0, "/Users/gx/Desktop/mypro/testApi")

from backend import database as db

_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_PROMPT = (
    "判断下面“搜索词”和“商品标题”的相关性：用户搜这个词，是不是在找这个商品？\n"
    "只输出 JSON：{{\"score\":0到10的整数,\"reason\":\"20字内中文理由\"}}\n\n"
    "搜索词：{term}\n标题：{title}\nJSON："
)


# ---------------------------------------------------------------------------
# 文本匹配
# ---------------------------------------------------------------------------
#: 投放 ASIN 形态的搜索词 (商品投放来源, 如 B0H69XP9TT): 不参与「词×标题」比较
_ASN_RE = re.compile(r"^(B0[A-Z0-9]{8}|[A-Z]{2}[A-Z0-9]{8})$")


def is_asin_term(term: str) -> bool:
    t = str(term or "").strip().upper()
    return bool(t) and bool(_ASN_RE.fullmatch(t)) and any(c.isdigit() for c in t)


def _norm(s: str) -> str:
    return re.sub(r"[\s　\-—_・、,。()（）\[\]【】/\\|]", "", str(s or "")).lower()


def _bigrams(s: str) -> set:
    s = _norm(s)
    if len(s) >= 2:
        return {s[i:i + 2] for i in range(len(s) - 1)}
    return {s} if s else set()


def _lcs(a: str, b: str) -> str:
    """最长公共子串 (用于高亮命中片段)"""
    a, b = _norm(a), _norm(b)
    best = ""
    for i in range(len(a)):
        for j in range(i + 2, len(a) + 1):
            seg = a[i:j]
            if seg in b and len(seg) > len(best):
                best = seg
    return best


def text_relevance(term: str, title: str) -> Dict[str, Any]:
    """文本匹配分(0~100) + 命中依据"""
    nq, nt = _norm(term), _norm(title)
    if not nq or not nt:
        return {"text_score": 0, "hits": [], "lcs": "", "full": False}
    full = nq in nt
    bq, bt = _bigrams(term), _bigrams(title)
    recall = len(bq & bt) / len(bq) if bq else 0.0
    parts = [p for p in re.split(r"[\s　]+", str(term)) if p]
    hits = [p for p in parts if _norm(p) in nt]
    pr = len(hits) / len(parts) if parts else recall
    lcs = _lcs(term, title)
    score = round(100 * (0.5 * (1.0 if full else 0.0) + 0.3 * recall + 0.2 * pr))
    return {"text_score": score, "hits": hits, "lcs": lcs, "full": full,
            "recall": round(recall, 2)}


# ---------------------------------------------------------------------------
# 大模型语义判断: WorkBuddy API 首选, 本地 Ollama 兜底
# 底座模型可在「配置管理」页切换, 持久化在 online_meta 表(key=wb_model/ollama_model)
# ---------------------------------------------------------------------------
_WB_CLIENTS: Dict[str, Any] = {}
_WB_LOCK = threading.Lock()


def current_model(engine: str = "workbuddy") -> str:
    """当前生效的底座模型 (配置管理页可改, 库里没有则用 config 默认值)

    - workbuddy : 相关性语义判断
    - vision    : 深度检测(图片相似度)的视觉模型
    - ollama    : 本地兜底
    """
    key, default = {
        "workbuddy": ("wb_model", config.WB_MODEL),
        "vision": ("wb_vision_model", config.WB_VISION_MODEL),
        "ollama": ("ollama_model", config.OLLAMA_MODEL),
    }.get(engine, ("wb_model", config.WB_MODEL))
    try:
        v = db.get_online_meta(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    except Exception:           # noqa: BLE001
        pass
    return default


def _workbuddy(prompt: str) -> str:
    model = current_model("workbuddy")
    with _WB_LOCK:
        c = _WB_CLIENTS.get(model)
        if c is None:
            from workbuddy import WorkBuddyClient
            c = WorkBuddyClient(mode="passthrough", model=model,
                                env_file=config.WB_ENV_FILE, timeout=config.WB_TIMEOUT)
            _WB_CLIENTS[model] = c
    r = c.chat_raw(prompt, temperature=config.WB_TEMPERATURE,
                   max_tokens=config.WB_MAX_TOKENS)
    return (r.content or "").strip()


def _ollama(prompt: str) -> str:
    body = json.dumps({"model": config.OLLAMA_MODEL, "prompt": prompt,
                       "stream": False,
                       "options": {"temperature": 0.1, "num_predict": 90}}).encode()
    req = urllib.request.Request(config.OLLAMA_URL + "/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=config.OLLAMA_TIMEOUT) as r:
        return (json.loads(r.read().decode("utf-8")).get("response") or "").strip()


def _parse_verdict(raw: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", raw, re.S)
    score, reason = None, ""
    if m:
        try:
            j = json.loads(m.group(0))
            score = int(float(j.get("score")))
            reason = str(j.get("reason") or "")[:60]
        except Exception:       # noqa: BLE001
            reason = re.sub(r"[\{\}\"]", "", raw)[:60]
    if score is None:
        nm = re.search(r"([0-9]|10)\b", raw)
        score = int(nm.group(1)) if nm else None
        reason = reason or raw.strip()[:60]
    if score is None:
        raise RuntimeError("模型未返回分数")
    return {"score": max(0, min(10, score)), "reason": reason}


def _ai_judge(term: str, title: str) -> Dict[str, Any]:
    prompt = _PROMPT.format(term=(term or "")[:80], title=(title or "")[:220])
    err = None
    for name, fn, engine in (("workbuddy", _workbuddy, "workbuddy"),
                             ("ollama", _ollama, "ollama")):
        try:
            out = _parse_verdict(fn(prompt))
            out["model"] = f"{name}:{current_model(engine)}"
            return out
        except Exception as e:              # noqa: BLE001
            err = e
    raise RuntimeError(f"语义判断失败: {err}")


# ---------------------------------------------------------------------------
# 单对判断 (搜索词详情页: 点击某个词 -> 该词 × 父 SKU 标题)
# 同一对并发请求只算一次, 其余等待后读缓存
# ---------------------------------------------------------------------------
_INFLIGHT: Dict[str, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()


def check_pair(term: str, title: str, wait: bool = True,
               timeout: float = 60.0, force: bool = False,
               asin: str = "", source: str = "pair") -> Dict[str, Any]:
    """计算并缓存一对 (词, 标题) 的相关性; 返回文本分 + 语义分 + 依据

    - force=True 忽略缓存重新调模型, 结果**覆盖更新** term_relevance;
    - 每次比较(含缓存命中/投放ASIN跳过)都写 relevance_compare_log 流水;
    - 投放 ASIN 形态的词(商品投放来源)不参与比较, skipped=True。
    """
    term, title = str(term or "").strip(), str(title or "").strip()
    out: Dict[str, Any] = {"term": term, "title": title, "found": bool(title)}
    out.update(text_relevance(term, title))
    from backend.ai_label import title_hash
    th = title_hash(title)

    def _log(ai_score, ai_reason, model, cached):
        try:
            db.log_compare(term, asin, th, title, out.get("text_score") or 0,
                           bool(out.get("full")), ai_score, ai_reason, model,
                           source, cached)
        except Exception:               # noqa: BLE001
            pass

    if not title:
        return out
    if is_asin_term(term):
        out["skipped"] = True
        out["skip_reason"] = "投放 ASIN（商品投放来源）不参与词×标题比较"
        _log(None, out["skip_reason"], "", False)
        return out

    if not force:
        hit = db.get_relevance(term, th)
        if hit:
            out.update({"ai_score": hit["score"], "ai_reason": hit["reason"],
                        "model": hit["model"], "cached": True})
            _log(hit["score"], hit["reason"], hit["model"], True)
            return out

    key = f"{term}\x00{th}"
    with _INFLIGHT_LOCK:
        if key in _INFLIGHT:
            ev, owner = _INFLIGHT[key], False
        else:
            ev = threading.Event()
            _INFLIGHT[key] = ev
            owner = True
    if not owner:
        # 已有同款任务在跑: 等它完成后读缓存
        if wait:
            ev.wait(timeout)
        hit = db.get_relevance(term, th)
        if hit:
            out.update({"ai_score": hit["score"], "ai_reason": hit["reason"],
                        "model": hit["model"], "cached": True})
        return out

    try:
        j = _ai_judge(term, title)
        db.save_relevance(term, th, title, j["score"], j["reason"],
                          out.get("text_score") or 0, j.get("model") or "")
        out.update({"ai_score": j["score"], "ai_reason": j["reason"],
                    "model": j.get("model"), "cached": False})
        _log(j["score"], j["reason"], j.get("model"), False)
        return out
    finally:
        with _INFLIGHT_LOCK:
            _INFLIGHT.pop(key, None)
        ev.set()


# ---------------------------------------------------------------------------
# 配对收集
# ---------------------------------------------------------------------------
def _f(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def collect_pairs(min_spend: float = 100.0) -> List[Dict[str, Any]]:
    """花费>=阈值的搜索词 × 其广告活动内 SKU (阈值含等于)

    返回每个 (词, SKU) 一行: term/cost/asin/title; 同词同标题只保留一行代表,
    但保留同标题的 SKU 列表(变体)便于展示。
    """
    with db.get_conn() as c:
        srows = [json.loads(r["raw_json"]) for r in c.execute(
            "SELECT raw_json FROM ad_records WHERE tab='search'")]
        prows = [json.loads(r["raw_json"]) for r in c.execute(
            "SELECT raw_json FROM ad_records WHERE tab='product'")]

    camp: Dict[str, Dict[str, str]] = defaultdict(dict)   # campaignId -> asin -> title
    for r in prows:
        a = r.get("asin")
        if a:
            camp[str(r.get("campaignId") or "")][a] = str(r.get("title") or "")

    term_cost: Dict[str, float] = defaultdict(float)
    term_sku: Dict[str, Dict[str, str]] = defaultdict(dict)   # term -> asin -> title
    for r in srows:
        cost = _f(r.get("adCost"))
        if cost < min_spend:
            continue
        q = str(r.get("query") or "").strip()
        if not q or is_asin_term(q):     # 只比较真实搜索词, 排除投放 ASIN
            continue
        term_cost[q] += cost
        for a, t in camp.get(str(r.get("campaignId") or ""), {}).items():
            term_sku[q].setdefault(a, t)

    rows: List[Dict[str, Any]] = []
    for q, skus in term_sku.items():
        # 按标题分组 (同标题变体合并展示)
        by_title: Dict[str, List[str]] = defaultdict(list)
        for a, t in skus.items():
            by_title[t or "(无标题)"].append(a)
        for t, asins in by_title.items():
            rows.append({"term": q, "cost": round(term_cost[q], 2),
                         "title": t, "asin": asins[0],
                         "skus": sorted(asins), "sku_count": len(asins)})
    rows.sort(key=lambda x: -x["cost"])
    return rows


# ---------------------------------------------------------------------------
# 分析 (文本即时 + AI 后台)
# ---------------------------------------------------------------------------
def analyze(min_spend: float = 100.0, use_ai: bool = True) -> Dict[str, Any]:
    rows = collect_pairs(min_spend)
    for r in rows:
        r.update(text_relevance(r["term"], r["title"]))
    out = {"min_spend": min_spend, "total": len(rows), "rows": rows,
           "ai_running": False, "ai_done": 0, "ai_total": 0}

    if not use_ai or not rows:
        return out

    # 按 (term,title_hash) 取缓存, 缺失的进后台
    from backend.ai_label import title_hash
    pending = []
    for r in rows:
        th = title_hash(r["title"])
        r["title_hash"] = th
        hit = db.get_relevance(r["term"], th)
        if hit:
            r["ai_score"] = hit["score"]
            r["ai_reason"] = hit["reason"]
        else:
            pending.append(r)
    done = len(rows) - len(pending)
    out["ai_done"] = done
    out["ai_total"] = len(rows)
    if pending:
        _start(min_spend, rows, pending)
        out["ai_running"] = True
    return out


def _key(min_spend: float) -> str:
    return f"rel|{min_spend:g}"


def _start(min_spend: float, rows: List[Dict[str, Any]],
           pending: List[Dict[str, Any]]) -> None:
    k = _key(min_spend)
    with _LOCK:
        cur = JOBS.get(k)
        if cur and cur.get("running"):
            return
        JOBS[k] = {"running": True, "done": 0, "total": len(pending),
                   "current": "", "failed": 0, "error": ""}
    threading.Thread(target=_work, args=(k, pending), daemon=True).start()


def _work(k: str, pending: List[Dict[str, Any]]) -> None:
    from concurrent.futures import ThreadPoolExecutor

    done_count = {"n": 0}

    def _one(r: Dict[str, Any]) -> None:
        try:
            j = _ai_judge(r["term"], r["title"])
            db.save_relevance(r["term"], r["title_hash"], r["title"],
                              j["score"], j["reason"],
                              r.get("text_score") or 0,
                              j.get("model") or config.WB_MODEL)
            try:
                db.log_compare(r["term"], "", r["title_hash"], r["title"],
                               r.get("text_score") or 0, bool(r.get("full")),
                               j["score"], j["reason"], j.get("model") or "",
                               "analyze", False)
            except Exception:       # noqa: BLE001
                pass
        except Exception as e:      # noqa: BLE001
            with _LOCK:
                JOBS[k]["failed"] += 1
                JOBS[k]["error"] = str(e)[:140]
        with _LOCK:
            done_count["n"] += 1
            if k in JOBS:
                JOBS[k]["done"] = done_count["n"]
                JOBS[k]["current"] = r["term"]

    # WorkBuddy API 并发 4 路, 约提速 4 倍
    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_one, pending))
    with _LOCK:
        if k in JOBS:
            JOBS[k]["running"] = False
            JOBS[k]["current"] = ""


def status(min_spend: float = 100.0) -> Dict[str, Any]:
    rows = collect_pairs(min_spend)
    from backend.ai_label import title_hash
    for r in rows:
        th = title_hash(r["title"])
        r["title_hash"] = th
        r.update(text_relevance(r["term"], r["title"]))
        hit = db.get_relevance(r["term"], th)
        if hit:
            r["ai_score"] = hit["score"]
            r["ai_reason"] = hit["reason"]
    with _LOCK:
        j = dict(JOBS.get(_key(min_spend)) or {})
    j["rows"] = rows
    j["total"] = len(rows)
    j["ai_done"] = sum(1 for r in rows if r.get("ai_score") is not None)
    return j


# ---------------------------------------------------------------------------
# 批量分析: 选中多个父 SKU, 按「搜索词花费 Top N」或「花费 > 阈值」选词批量判断
# ---------------------------------------------------------------------------
_BATCH_KEY = "batch"
_BATCH_LOCK = threading.Lock()


def build_batch_tasks(asins: List[str], mode: str = "top",
                      top_n: int = 10, min_spend: float = 100.0
                      ) -> List[Dict[str, Any]]:
    """构造批量任务清单: 每个 (父体, 搜索词) 一条

    mode=top       : 每个父体按搜索词总花费取前 top_n 个词
    mode=threshold : 每个父体取搜索词总花费 >= min_spend 的词 (包含等于)
    """
    top_n = max(1, min(int(top_n or 10), 100))
    tasks: List[Dict[str, Any]] = []
    seen_titles: Dict[str, str] = {}          # asin -> title (同父体复用)
    for a in asins:
        a = str(a or "").strip()
        if not a:
            continue
        title = seen_titles.get(a)
        if title is None:
            title = db.get_parent_title(a)
            seen_titles[a] = title
        if not title:
            continue
        try:
            pt = db.get_parent_terms(a)
        except Exception:                   # noqa: BLE001
            continue
        # 按「词」聚合花费 (同一词不同匹配方式/活动合并); 投放 ASIN 不参与比较
        by_q: Dict[str, float] = defaultdict(float)
        for r in pt.get("rows") or []:
            q = str(r.get("query") or "").strip()
            if q and not is_asin_term(q):
                by_q[q] += float(r.get("adCost") or 0)
        if mode == "threshold":
            picked = [(q, c) for q, c in by_q.items() if c >= float(min_spend or 0)]
        else:
            picked = sorted(by_q.items(), key=lambda x: -x[1])[:top_n]
        for q, c in picked:
            tasks.append({"asin": a, "term": q, "title": title,
                          "cost": round(c, 2)})
    # 去重: 同 (词,标题) 只判一次, 花费取最大 (展示仍按每个父体一行)
    return tasks


def start_batch(asins: List[str], mode: str = "top",
                top_n: int = 10, min_spend: float = 100.0) -> Dict[str, Any]:
    """启动批量关联度分析 (后台线程), 返回任务概况"""
    tasks = build_batch_tasks(asins, mode, top_n, min_spend)
    with _BATCH_LOCK:
        old = JOBS.get(_BATCH_KEY)
        if old and old.get("running"):
            raise RuntimeError("已有批量分析在运行中")
        JOBS[_BATCH_KEY] = {
            "running": True, "done": 0, "total": len(tasks), "failed": 0,
            "error": "", "current": "", "results": [], "started_at": db.now(),
            "mode": mode, "top_n": top_n, "min_spend": min_spend,
            "asins": list(asins or []),
        }
    if not tasks:
        with _BATCH_LOCK:
            JOBS[_BATCH_KEY]["running"] = False
        return batch_status()
    threading.Thread(target=_batch_work, args=(tasks,), daemon=True).start()
    return batch_status()


def _batch_work(tasks: List[Dict[str, Any]]) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from backend.ai_label import title_hash

    # 预查缓存, 已缓存的直接出结果
    results: List[Dict[str, Any]] = []
    pending: List[Dict[str, Any]] = []
    for t in tasks:
        th = title_hash(t["title"])
        t["title_hash"] = th
        t.update(text_relevance(t["term"], t["title"]))
        hit = db.get_relevance(t["term"], th)
        if hit:
            results.append({**t, "ai_score": hit["score"],
                            "ai_reason": hit["reason"], "model": hit["model"],
                            "cached": True})
            try:
                db.log_compare(t["term"], t.get("asin") or "", th, t["title"],
                               t.get("text_score") or 0, bool(t.get("full")),
                               hit["score"], hit["reason"], hit["model"],
                               "batch", True)
            except Exception:       # noqa: BLE001
                pass
        else:
            pending.append(t)
    with _BATCH_LOCK:
        JOBS[_BATCH_KEY]["results"] = results
        JOBS[_BATCH_KEY]["done"] = len(results)

    n = {"v": len(results)}

    def _one(t: Dict[str, Any]) -> None:
        with _BATCH_LOCK:
            JOBS[_BATCH_KEY]["current"] = f"{t['asin']} × {t['term'][:20]}"
        try:
            j = _ai_judge(t["term"], t["title"])
            db.save_relevance(t["term"], t["title_hash"], t["title"],
                              j["score"], j["reason"],
                              t.get("text_score") or 0, j.get("model") or "")
            row = {**t, "ai_score": j["score"], "ai_reason": j["reason"],
                   "model": j.get("model"), "cached": False}
            db.log_compare(t["term"], t.get("asin") or "", t["title_hash"],
                           t["title"], t.get("text_score") or 0,
                           bool(t.get("full")), j["score"], j["reason"],
                           j.get("model") or "", "batch", False)
        except Exception as e:              # noqa: BLE001
            row = {**t, "ai_score": None, "ai_reason": f"判断失败: {e}"[:80],
                   "model": "", "cached": False}
            db.log_compare(t["term"], t.get("asin") or "", t["title_hash"],
                           t["title"], t.get("text_score") or 0,
                           bool(t.get("full")), None,
                           f"判断失败: {str(e)[:60]}", "", "batch", False)
            with _BATCH_LOCK:
                JOBS[_BATCH_KEY]["failed"] += 1
                JOBS[_BATCH_KEY]["error"] = str(e)[:140]
        with _BATCH_LOCK:
            n["v"] += 1
            JOBS[_BATCH_KEY]["results"].append(row)
            JOBS[_BATCH_KEY]["done"] = n["v"]

    with ThreadPoolExecutor(max_workers=4) as ex:
        list(ex.map(_one, pending))
    with _BATCH_LOCK:
        JOBS[_BATCH_KEY]["running"] = False
        JOBS[_BATCH_KEY]["current"] = ""
        JOBS[_BATCH_KEY]["finished_at"] = db.now()


def batch_status() -> Dict[str, Any]:
    with _BATCH_LOCK:
        return dict(JOBS.get(_BATCH_KEY) or {"running": False})
