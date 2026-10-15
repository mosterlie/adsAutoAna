# -*- coding: utf-8 -*-
"""搜索词 / 父 SKU 的「竞品素材拉取」与「人工打标」进度状态

两个口径的分母都是「该父 SKU 的搜索词全集」(只算搜索词行, 商品投放 ASIN 不算):
  * 拉取状态: 该词的亚马逊竞品结果是否已落到本地仓库(amazon_store 的 _index.json),
              命中就能在详情页直接读本地, 不再访问亚马逊
  * 标记状态: 该词是否已在 term_labels 里人工打过相关度标(紧密/宽泛/不太相关/不相关)

状态口径(与需求一致):
  搜索词级     已拉取 / 未拉取
  父SKU 拉取   拉取完成(全部已拉取) / 部分拉取 / 未拉取
  父SKU 标记   标记完成(全部已打标) / 部分标记 / 未标记

不新增表, 全部现算: 词集来自 ad_records(经 amazon_targets 归到父 ASIN),
拉取来自本地索引, 打标来自 term_labels。词集/索引解析偏贵, 加短 TTL 缓存
(列表页与详情页会反复渲染)。
"""
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from backend import amazon_store, amazon_targets
from backend import database as db

_TTL = 15.0                     # 词集缓存(秒): 广告数据变了最迟 15 秒后跟上
_CACHE: Dict[str, Any] = {"at": 0.0, "by_parent": {}}

# 父 SKU 级状态取值(前端按这个 key 上色)
K_DONE, K_PARTIAL, K_NONE, K_EMPTY = "done", "partial", "none", "empty"


def term_sets() -> Dict[str, Set[str]]:
    """父 ASIN -> 该父体下的全部搜索词(去重); 只算搜索词, 不含商品投放"""
    now = time.time()
    cached = _CACHE.get("by_parent") or {}
    if cached and now - float(_CACHE.get("at") or 0) < _TTL:
        return cached
    by_parent: Dict[str, Set[str]] = {}
    for t in amazon_targets.build(0, keywords_only=True):
        q = str(t.get("query") or "").strip()
        if not q:
            continue
        for p in t.get("parents") or []:
            by_parent.setdefault(str(p), set()).add(q)
    _CACHE.update({"at": now, "by_parent": by_parent})
    return by_parent


def terms_of(parent: str) -> List[str]:
    """该父体的搜索词清单(排序后)"""
    return sorted(term_sets().get(str(parent or "").strip(), set()))


def _rollup(done: int, total: int,
            done_text: str, part_text: str, none_text: str) -> Tuple[str, str]:
    if total <= 0:
        return K_EMPTY, "无搜索词"
    if done >= total:
        return K_DONE, done_text
    if done > 0:
        return K_PARTIAL, part_text
    return K_NONE, none_text


def fetch_rollup(done: int, total: int) -> Tuple[str, str]:
    """父 SKU 拉取状态: 拉取完成 / 部分拉取 / 未拉取"""
    return _rollup(done, total, "拉取完成", "部分拉取", "未拉取")


def label_rollup(done: int, total: int) -> Tuple[str, str]:
    """父 SKU 标记状态: 标记完成 / 部分标记 / 未标记"""
    return _rollup(done, total, "标记完成", "部分标记", "未标记")


def term_flags(parent: str, queries: List[str],
               index: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
    """一批搜索词的本地素材情况: {搜索词: {fetched, images}} (搜索词列表页行内用)"""
    idx = index if index is not None else amazon_store.load_index()
    out: Dict[str, Dict[str, Any]] = {}
    for q in queries or []:
        s = str(q or "").strip()
        if s and s not in out:
            out[s] = amazon_store.parent_term_state(parent, s, idx)
    return out


def _state(parent: str, terms: List[str], labels: Dict[str, str],
           index: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """一个父体的状态汇总(不含逐词明细)"""
    fetched = labeled = images = 0
    for q in terms:
        st = amazon_store.parent_term_state(parent, q, index)
        if st["fetched"]:
            fetched += 1
        images += int(st["images"] or 0)
        if labels.get(q):
            labeled += 1
    total = len(terms)
    fk, ft = fetch_rollup(fetched, total)
    lk, lt = label_rollup(labeled, total)
    return {"parent": parent, "terms_total": total,
            "terms_fetched": fetched, "terms_labeled": labeled, "images": images,
            "fetch_status": fk, "fetch_text": ft,
            "label_status": lk, "label_text": lt}


def parent_state(parent: str) -> Dict[str, Any]:
    """单个父 SKU 的状态(搜索词列表页 / 详情页用)"""
    p = str(parent or "").strip()
    if not p:
        return _state("", [], {})
    return _state(p, terms_of(p), db.get_term_labels(p))


def parent_states(parents: List[str]) -> Dict[str, Dict[str, Any]]:
    """批量父 SKU 状态(产品列表用): 索引与打标各只读一次"""
    vals = [str(p).strip() for p in dict.fromkeys(parents or []) if str(p or "").strip()]
    if not vals:
        return {}
    index = amazon_store.load_index()
    labels = db.get_term_labels_multi(vals)
    sets = term_sets()
    return {p: _state(p, sorted(sets.get(p, set())), labels.get(p) or {}, index)
            for p in vals}
