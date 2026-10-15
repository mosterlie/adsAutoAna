# -*- coding: utf-8 -*-
"""深度检测(统一口径): 「这个搜索词的搜索结果集」与「我的商品」有多像

三层判断(提示词见 backend/prompts.py, 共用同一评分标尺):
  ① 标题级: 我的标题 vs 每条竞品标题 → 是否同类
  ② 图片级: 我的主图 vs 每条竞品主图 → 是否同类(image_sim 负责)
  ③ 词级  : 该词的用户搜索意图 vs 我的品 → 词要不要留

★ 结论用途: 综合相关度过低 + 有花费无成交 → 建议**否定该词**。

持久化:
  - title_similarity : 每条 (父ASIN, 词, 对方ASIN) 的标题同类判断(重算覆盖)
  - term_verdict     : 词级综合结论与建议(重算覆盖)
"""
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import config
from backend import database as db
from backend import llm, prompts

# 标题噪声正则: **已不再用于标题比对**(2026-10-15 起改为原始标题直接送模型)。
# 保留仅供 normalize_title() 的历史用途/展示, 注意它有已知缺陷: 因用了 \b,
# 「数字+单位+日文汉字」边界不成立时会漏剥(如 12mm対応 / H70mm)。
_NOISE_RE = [
    re.compile(r"\d+(\.\d+)?\s*(mm|cm|m|kg|g|ml|l|inch|in|ft|個|枚|本|台|セット|枚入り|pcs|PCS)\b", re.I),
    re.compile(r"(大容量|高品質|新品|人気|送料無料|セール|ポイント|正規品|並行輸入|日本製|多機能)"),
    re.compile(r"(ブラック|ホワイト|シルバー|グレー|ブルー|レッド|グリーン|ブラウン|ベージュ|黒|白|赤|青|緑|黄)"),
    re.compile(r"[\s　]+"),
]


def normalize_title(t: str) -> str:
    """去掉尺寸/颜色/数量/促销等噪声, 保留品类词

    注意: 标题比对已改为**不做本地预处理**(见 title_pair_judge), 此函数不再是比对入口。
    """
    s = str(t or "")
    for r in _NOISE_RE:
        s = r.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# 标题对判断 (逐条)
# ---------------------------------------------------------------------------
def title_pair_judge(a: str, b: str, model: Optional[str] = None) -> Dict[str, Any]:
    """我的标题 × 竞品标题 → 是否同类(0~100)

    直接送**原始标题**: 不做本地正则预处理。
    尺寸/颜色/数量/品牌/促销词等本来就由提示词(TITLE_PAIR_PROMPT 的 IGNORE_RULE)
    要求模型自行忽略, 交给模型判断比正则更稳(正则误伤品类词、漏剥单位都出现过)。
    这里只保留一个防超长的硬截断(400 字符), 正常标题不会触达。
    """
    p = prompts.render(prompts.TITLE_PAIR_PROMPT,
                       a=str(a or "")[:400], b=str(b or "")[:400])
    j = llm.ask_json(p, model=model)
    return {"score": max(0, min(100, int(float(j.get("score"))))),
            "same": bool(j.get("same_category")),
            "core_a": str(j.get("core_a") or "")[:20],
            "core_b": str(j.get("core_b") or "")[:20],
            "reason": str(j.get("reason") or "")[:60],
            "model": f"text:{model or llm.model_name('text')}"}


# ---------------------------------------------------------------------------
# 词级判断 (搜索意图 vs 我的品)
# ---------------------------------------------------------------------------
def term_verdict_judge(term: str, my_title: str,
                       model: Optional[str] = None) -> Dict[str, Any]:
    p = prompts.render(prompts.TERM_VERDICT_PROMPT,
                       term=str(term or "")[:80],
                       my_title=normalize_title(my_title)[:200])
    j = llm.ask_json(p, model=model)
    return {"score": max(0, min(100, int(float(j.get("score"))))),
            "same": bool(j.get("same_category")),
            "intent": str(j.get("term_intent") or "")[:30],
            "reason": str(j.get("reason") or "")[:60],
            "model": f"text:{model or llm.model_name('text')}"}


# ---------------------------------------------------------------------------
# 标题匹配任务 (逐条竞品标题, 后台 + 落库缓存)
# ---------------------------------------------------------------------------
_TJOBS: Dict[str, Dict[str, Any]] = {}
_TLOCK = threading.Lock()


def _tkey(asin: str, term: str) -> str:
    return f"title|{asin}\x00{term}"


def start_title_job(asin: str, term: str, top_n: int = 10) -> Dict[str, Any]:
    asin, term = str(asin or "").strip(), str(term or "").strip()
    top_n = max(1, min(int(top_n or 10), 20))
    my_title = db.get_parent_title(asin)
    rows = db.latest_amazon_results(term, top_n)[:top_n]
    cached = db.get_title_sims(asin, term)
    items: List[Dict[str, Any]] = []
    for r in rows:
        it = {"position": r.get("position"), "asin": r.get("asin"),
              "title": (r.get("title") or "")[:80], "score": None,
              "reason": "", "core_a": "", "core_b": ""}
        hit = cached.get(str(r.get("asin") or ""))
        if hit and hit.get("score") is not None:
            it.update({"score": hit["score"], "reason": hit.get("reason") or "",
                       "core_a": hit.get("core_a") or "",
                       "core_b": hit.get("core_b") or "", "cached": True})
        items.append(it)
    base: Dict[str, Any] = {
        "running": False, "asin": asin, "term": term, "my_title": my_title,
        "model": llm.model_name("text"), "items": items, "total": len(rows),
        "done": sum(1 for i in items if i.get("cached")),
        "avg": None, "max": None, "note": "",
    }
    if not my_title:
        base["note"] = "未找到我的商品标题"
        return base
    if not rows:
        base["note"] = "该词暂无亚马逊搜索结果缓存"
        return base
    scores = [i["score"] for i in items if i.get("score") is not None]
    base["avg"] = round(sum(scores) / len(scores), 1) if scores else None
    base["max"] = max(scores) if scores else None
    pending = [(i, r) for i, (r, it) in enumerate(zip(rows, items))
               if it.get("score") is None]
    if not pending:
        return base
    base["running"] = True
    with _TLOCK:
        cur = _TJOBS.get(_tkey(asin, term))
        if cur and cur.get("running"):
            return dict(cur)
        _TJOBS[_tkey(asin, term)] = base
        snap = dict(base)
    threading.Thread(target=_title_work, args=(_tkey(asin, term), asin, term,
                                               my_title, pending),
                     daemon=True).start()
    return snap


def _title_work(k: str, asin: str, term: str, my_title: str,
                pending: List[Any]) -> None:
    with _TLOCK:
        n = {"v": _TJOBS[k]["done"] if k in _TJOBS else 0}

    def _one(idx: int, r: Dict[str, Any]) -> None:
        it = {"position": r.get("position"), "asin": r.get("asin"),
              "title": (r.get("title") or "")[:80], "score": None,
              "reason": "", "core_a": "", "core_b": "", "via": "title"}
        try:
            j = title_pair_judge(my_title, r.get("title") or "")
            it.update({"score": j["score"], "reason": j["reason"],
                       "core_a": j["core_a"], "core_b": j["core_b"],
                       "same": j["same"]})
            db.save_title_sim(asin, term, str(r.get("asin") or ""),
                              r.get("position"), j["score"], j["reason"],
                              j["core_a"], j["core_b"], j.get("model") or "")
        except Exception as e:              # noqa: BLE001
            it["reason"] = f"判断失败: {str(e)[:40]}"
        with _TLOCK:
            if k in _TJOBS:
                _TJOBS[k]["items"][idx] = it
                n["v"] += 1
                _TJOBS[k]["done"] = n["v"]

    with ThreadPoolExecutor(max_workers=config.WB_VISION_WORKERS) as ex:
        list(ex.map(lambda p: _one(*p), pending))
    with _TLOCK:
        job = _TJOBS.get(k)
        if job:
            scores = [i["score"] for i in job["items"] if i.get("score") is not None]
            job["avg"] = round(sum(scores) / len(scores), 1) if scores else None
            job["max"] = max(scores) if scores else None
            job["running"] = False
            job["finished_at"] = db.now()


def title_job_status(asin: str, term: str) -> Dict[str, Any]:
    with _TLOCK:
        cur = _TJOBS.get(_tkey(asin, term))
        if cur:
            return dict(cur)
    cached = db.get_title_sims(asin, term)
    if cached:
        rows = db.latest_amazon_results(term, 20)
        items: List[Dict[str, Any]] = []
        for r in rows:
            hit = cached.get(str(r.get("asin") or ""))
            it = {"position": r.get("position"), "asin": r.get("asin"),
                  "title": (r.get("title") or "")[:80], "score": None,
                  "reason": "", "core_a": "", "core_b": ""}
            if hit and hit.get("score") is not None:
                it.update({"score": hit["score"], "reason": hit.get("reason") or "",
                           "core_a": hit.get("core_a") or "",
                           "core_b": hit.get("core_b") or "", "cached": True})
            items.append(it)
        scores = [i["score"] for i in items if i.get("score") is not None]
        return {"running": False, "asin": asin, "term": term, "items": items,
                "total": len(items), "done": len(items),
                "avg": round(sum(scores) / len(scores), 1) if scores else None,
                "max": max(scores) if scores else None,
                "model": next((cached[k].get("model") for k in cached
                               if cached[k].get("model")), ""),
                "note": "库中历史结果"}
    return {"running": False, "asin": asin, "term": term, "items": [],
            "total": 0, "done": 0, "note": "尚未做标题匹配"}


# ---------------------------------------------------------------------------
# 词级综合结论: 结果集匹配评估 + 是否否定建议
# ---------------------------------------------------------------------------
def evaluate(asin: str, term: str, top_n: int = 10,
             refresh: bool = False) -> Dict[str, Any]:
    """综合「词级判断 + 标题匹配率 + 图片相似率 + 广告表现」给出结论与建议"""
    asin, term = str(asin or "").strip(), str(term or "").strip()
    my_title = db.get_parent_title(asin)
    out: Dict[str, Any] = {"asin": asin, "term": term, "my_title": my_title,
                           "note": ""}
    if not my_title:
        out["note"] = "未找到我的商品标题"
        return out

    # ① 词级判断(可缓存)
    verdict = None if refresh else db.get_term_verdict(asin, term)
    if not verdict:
        try:
            j = term_verdict_judge(term, my_title)
            verdict = {"score": j["score"], "same": j["same"],
                       "intent": j["intent"], "reason": j["reason"],
                       "model": j["model"]}
            db.save_term_verdict(asin, term, my_title, j["score"], j["same"],
                                 j["intent"], j["reason"], j["model"])
        except Exception as e:              # noqa: BLE001
            out["note"] = f"词级判断失败: {str(e)[:60]}"

    # ② 标题匹配率(取已落库结果)
    ts = db.get_title_sims(asin, term)
    t_scores = [v["score"] for v in ts.values() if v.get("score") is not None]
    t_same = sum(1 for v in ts.values() if v.get("score") is not None
                 and v["score"] >= 70)

    # ③ 图片相似(取已落库结果)
    ims = db.get_image_sims(asin, term)
    i_scores = [v["score"] for v in ims.values() if v.get("score") is not None]

    # ④ 广告表现(花费/订单)
    metrics = db.get_term_metrics(asin, term)

    title_score = round(sum(t_scores) / len(t_scores), 1) if t_scores else None
    img_score = round(sum(i_scores) / len(i_scores), 1) if i_scores else None
    match_rate = round(100 * t_same / len(t_scores), 1) if t_scores else None

    # 综合相关度: 词级 0.4 + 标题匹配率 0.3 + 图片 0.3 (缺项按可用项归一)
    parts = []
    if verdict:
        parts.append((verdict["score"], 0.4))
    if match_rate is not None:
        parts.append((match_rate, 0.3))
    if img_score is not None:
        parts.append((img_score, 0.3))
    if parts:
        wsum = sum(w for _, w in parts)
        total = round(sum(s * w for s, w in parts) / wsum, 1)
    else:
        total = None

    spend = metrics.get("adCost") or 0
    orders = metrics.get("orderNum") or 0
    # 建议口径: 先看「流量是否同类」, 再看「花了钱有没有成交」
    #   —— 同类但零成交 => 问题在 Listing/价格/竞争力, 不该否定词
    #   —— 不同类且零成交 => 词本身跑偏, 该否定
    if total is None:
        advice, advice_reason = "数据不足", "尚未完成标题/图片/词级判断"
    elif total >= 70:
        if spend >= 100 and orders == 0:
            advice = "保留·优化转化"
            advice_reason = ("结果集与我的品同类(流量精准), 但有花费无成交, "
                             "问题在 Listing/价格/竞争力, 不建议否定")
        else:
            advice, advice_reason = "保留", "结果集与我的品同类, 流量精准"
    elif total >= 40:
        advice = "观察"
        advice_reason = "结果集部分同类, 建议优化 Listing 或降低竞价"
        if spend >= 100 and orders == 0:
            advice = "观察·可否定"
            advice_reason += "; 有花费无成交, 可考虑否定"
    else:
        advice = "建议否定"
        advice_reason = "搜索结果集与我的品不同类, 流量不相关"
        if spend < 100:
            advice_reason += " (花费较低, 可先观察)"

    out.update({
        "verdict": verdict, "title_avg": title_score, "title_max":
            max(t_scores) if t_scores else None, "match_rate": match_rate,
        "title_same": t_same, "title_count": len(t_scores),
        "image_avg": img_score, "image_max": max(i_scores) if i_scores else None,
        "image_count": len(i_scores),
        "spend": spend, "orders": orders,
        "total": total, "advice": advice, "advice_reason": advice_reason,
        "model": llm.model_name("text"),
    })
    return out
