# -*- coding: utf-8 -*-
"""FastAPI 路由: 采集控制 + 数据查询"""
import json
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

import config
from backend import (ai_label, amazon, amazon_prefetch, amazon_product,
                     amazon_store, amazon_targets, crawler, database as db,
                     image_jobs, relevance, term_status, translate, wb_config)
from backend.cookies import load_cookies, parse_cookie_string, save_cookies, sync_from_browser
from backend.sellfox_client import TAB_DEFS, TAB_ORDER, scope_str

router = APIRouter(prefix="/api")


class CrawlReq(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    tabs: Optional[List[str]] = None
    shop_ids: Optional[List[int]] = None
    cookie_string: Optional[str] = None


def _scope_label(s: Dict[str, Any]) -> str:
    return scope_str(s)


@router.get("/tabs")
def tabs() -> Dict[str, Any]:
    return {"tabs": [
        {"key": k, "label": TAB_DEFS[k]["label"],
         "scopes": [_scope_label(s) for s in TAB_DEFS[k]["scopes"]]}
        for k in TAB_ORDER
    ], "today": date.today().strftime("%Y-%m-%d"),
        "default_start": config.DEFAULT_START_DATE}


@router.get("/status")
def status() -> Dict[str, Any]:
    st = db.get_status()
    st["crawl"] = crawler.get_state()
    st["cookie_saved"] = bool(load_cookies())
    return st


@router.get("/shops")
def shops() -> Dict[str, Any]:
    return {"rows": db.get_shops()}


@router.get("/portfolios")
def portfolios() -> Dict[str, Any]:
    return {"rows": db.get_portfolios()}


def _parse_filters(raw: str) -> Dict[str, List[str]]:
    """解析 filters 查询参数 (JSON 对象: {"state":["enabled"],...})"""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except Exception:       # noqa: BLE001
        raise HTTPException(400, "filters 需为 JSON 对象字符串")
    if not isinstance(d, dict):
        raise HTTPException(400, "filters 需为 JSON 对象")
    out: Dict[str, List[str]] = {}
    for k, v in d.items():
        out[str(k)] = [str(x) for x in (v if isinstance(v, list) else [v])]
    return out


def _term_of(row: Dict[str, Any]) -> str:
    """搜索词页签行里的「搜索词」字段(ASIN 定位行的 query 是 ASIN, 由调用方排除)"""
    return str(row.get("query") or row.get("keywordText") or "").strip()


def _inject_term_zh(rows: List[Dict[str, Any]]) -> None:
    """给一批搜索词行注入 query_zh (已落库的中文翻译, 没有则空串)

    ASIN 形态的 query(商品投放行)不需要翻译, 置空。
    """
    rows = rows or []
    terms = {t for t in (_term_of(r) for r in rows) if t and not translate.is_asin(t)}
    zs = db.get_translations(translate.KIND_TERM, list(terms)) if terms else {}
    for r in rows:
        t = _term_of(r)
        r["query_zh"] = zs.get(t, "") if t and not translate.is_asin(t) else ""


_RANK_METRICS = (("cost", "adCost"), ("imp", "impressions"),
                 ("clk", "clicks"), ("ord", "orderNum"))


def _inject_ranks(rows: List[Dict[str, Any]]) -> None:
    """给搜索词行加「本父体内排名」: 花费/曝光/点击/订单 各排第几(1 = 最高)

    按 scope 分组各自排名 —— 页面上「只看搜索词 / 只看商品投放」是前端过滤,
    而两类行的指标量级差很多, 混在一起排会把商品投放行全挤到两头, 名次没意义。
    并列同名次(1,2,2,4); 该指标为 0 的行名次给 0, 前端显示为「-」。
    """
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows or []:
        groups.setdefault(str(r.get("scope") or ""), []).append(r)
    for grp in groups.values():
        for key, field in _RANK_METRICS:
            vals = [float(r.get(field) or 0) for r in grp]
            order = sorted((v for v in vals if v > 0), reverse=True)
            first: Dict[float, int] = {}
            for i, v in enumerate(order):
                first.setdefault(v, i + 1)
            for r, v in zip(grp, vals):
                r["rank_" + key] = (first.get(v) or 0) if v > 0 else 0


def _inject_translations(out: Dict[str, Any]) -> None:
    """搜索词页签: 行内注入译文。注入在 _collect_columns 之后, 不新增表格列——
    前端把译文渲染在搜索词单元格里。"""
    _inject_term_zh(out.get("rows") or [])


@router.get("/filters")
def filters(tab: str = Query(...), shop_id: Optional[int] = None) -> Dict[str, Any]:
    """某页签可用的筛选字段与候选值 (供前端动态渲染查询框)"""
    if tab not in TAB_DEFS:
        raise HTTPException(400, f"未知页签: {tab}")
    return db.get_filter_options(tab, shop_id)


@router.get("/records")
def records(tab: str = Query(...), page: int = 1, page_size: int = 50,
            keyword: str = "", shop_id: Optional[int] = None, scope: str = "",
            order_field: str = "", order_dir: str = "desc",
            range_start: str = "", range_end: str = "",
            filters: str = "", search_field: str = "",
            search_mode: str = "blur") -> Dict[str, Any]:
    if tab not in TAB_DEFS:
        raise HTTPException(400, f"未知页签: {tab}")
    if order_field and not db.is_safe_field(order_field):
        raise HTTPException(400, f"非法排序字段: {order_field!r}")
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    out = db.get_records(tab, page, page_size, keyword, shop_id, scope,
                         order_field, order_dir, range_start, range_end,
                         _parse_filters(filters), search_field, search_mode)
    if tab == "search":
        _inject_translations(out)     # 搜索词页签: 行内带上已落库的中文翻译
    return out


@router.get("/stats")
def stats(tab: str = Query(...), shop_id: Optional[int] = None, scope: str = "",
          range_start: str = "", range_end: str = "", keyword: str = "",
          filters: str = "", search_field: str = "",
          search_mode: str = "blur") -> Dict[str, Any]:
    if tab not in TAB_DEFS:
        raise HTTPException(400, f"未知页签: {tab}")
    return db.get_stats(tab, shop_id, scope, range_start, range_end,
                        _parse_filters(filters), keyword, search_field, search_mode)


@router.post("/crawl")
def crawl(req: CrawlReq = Body(default=CrawlReq())) -> Dict[str, Any]:
    try:
        run_id = crawler.start_crawl_async(
            start_date=req.start_date, end_date=req.end_date,
            tabs=req.tabs, shop_ids=req.shop_ids, cookie_string=req.cookie_string)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"run_id": run_id, "started": True}


@router.get("/crawl/state")
def crawl_state() -> Dict[str, Any]:
    return crawler.get_state()


@router.get("/crawl/runs")
def crawl_runs() -> Dict[str, Any]:
    last = db.last_run()
    return {"last": last}


@router.get("/amazon/search")
def amazon_search(query: str = Query(...), domain: str = "co.jp",
                  screenshot: bool = False, refresh: bool = False,
                  ttl: Optional[int] = None) -> Dict[str, Any]:
    """用真实浏览器加载亚马逊搜索结果页, 返回从页面 DOM 抽取的结果(不走接口)

    - 命中缓存(TTL 内)直接返回库中结果, 不重复打开浏览器;
    - 未命中则受「最小间隔」限流后抓取, 并落库;
    - refresh=true 强制重新抓取。
    """
    q = (query or "").strip()
    if not q:
        raise HTTPException(400, "query 不能为空")
    if domain not in amazon.MARKET:
        raise HTTPException(400, f"不支持的站点: {domain}")
    try:
        d = amazon.fetch(q, domain=domain, screenshot=screenshot,
                         refresh=refresh, ttl=ttl)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"抓取失败: {str(e)[:200]}")
    return attach_local_images(d, q)


def attach_local_images(d: Dict[str, Any], query: str) -> Dict[str, Any]:
    """给每个 item 补 image_local(本地主图地址)

    本地已有主图时页面就直接读本地文件, 不必再去亚马逊图片 CDN 取图;
    本地没下到图的卡片不补该字段, 前端自动回退用原来的远程地址。
    """
    if not d:
        return d
    parent = d.get("local_parent") or next(iter(d.get("parents") or []), "")
    if not parent:
        return d
    n = 0
    for it in d.get("items") or []:
        u = amazon_store.local_image_url(parent, query, it)
        if u:
            it["image_local"] = u
            n += 1
    d["local_images"] = n
    return d


@router.get("/amazon/history")
def amazon_history(limit: int = 50, query: str = "") -> Dict[str, Any]:
    """亚马逊搜索抓取历史(元信息)"""
    return {"rows": db.get_amazon_searches(max(1, min(limit, 200)), query)}


@router.get("/amazon/results")
def amazon_results(search_id: int = Query(...)) -> Dict[str, Any]:
    """某次抓取的明细结果"""
    return {"rows": db.get_amazon_results(search_id)}


@router.get("/amazon/metrics")
def amazon_metrics(asin: str = Query(...), domain: str = "", refresh: bool = False,
                   ttl: Optional[int] = None, images: bool = False) -> Dict[str, Any]:
    """按需补全单个 ASIN 的「星级评分 / 评分数 / 小类目-大类目排名」。

    赛狐 product/pageList 里这几列恒为 null, 只能从亚马逊商品页取:
    用真实浏览器加载 /dp/{ASIN}, 渲染后读 DOM(不走接口)。
    命中缓存则直接返回; 否则受限流节拍约束。
    """
    a = (asin or "").strip()
    if not a:
        raise HTTPException(400, "asin 不能为空")
    dm = domain or config.AMAZON_DOMAIN
    if dm not in amazon.MARKET:
        raise HTTPException(400, f"不支持的站点: {dm}")
    try:
        out = amazon_product.fetch(a, domain=dm, refresh=refresh, ttl=ttl)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"抓取失败: {str(e)[:200]}")
    if images:
        try:
            out["images"] = db.get_amz_product_images(a, dm)
        except Exception:       # noqa: BLE001
            out["images"] = []
    return out


# ---- 亚马逊搜索页「本地文件仓库 + 批量预抓取」 ----
class AmzPrefetchReq(BaseModel):
    min_clicks: int = config.AMAZON_PREFETCH_MIN_CLICKS   # 只抓点击量达标的搜索词
    limit: int = 0              # 0 = 全部达标搜索词
    refresh: bool = False       # True = 已抓过的也重抓
    gap_min: Optional[float] = None
    gap_max: Optional[float] = None
    screenshot: bool = True
    scrolls: int = 2
    images: bool = True         # 是否下载搜索结果里各商品的主图
    image_limit: int = 0        # 每个搜索词最多下几张主图, 0=全部
    skip_done_parents: bool = True   # 已标记完成的父 ASIN 整组跳过


@router.get("/amazon/targets")
def amazon_targets_list(min_clicks: Optional[int] = None, limit: int = 2000) -> Dict[str, Any]:
    """预抓取目标: 点击量达标的搜索词 → 归属父 ASIN → 本地是否已抓

    数据链路: 搜索词 → 广告活动 → 广告产品 ASIN → 在线产品变体关系 → 父 ASIN。
    抓取按父 ASIN 逐组进行, groups 里给的就是这个顺序与各组完成情况。
    """
    mc = config.AMAZON_PREFETCH_MIN_CLICKS if min_clicks is None else min_clicks
    items = amazon_targets.build(mc)
    idx = amazon_store.load_index()
    have = amazon_store.local_queries([t["query"] for t in items], idx)   # 本地已有结果的词
    rows = []
    for t in items[:max(1, min(limit, 5000))]:
        ent = idx.get(t["query"]) or {}
        rows.append({
            "query": t["query"], "clicks": t["clicks"], "adCost": round(t["adCost"], 2),
            "impressions": t["impressions"], "parents": t["parents"],
            "asins": t["asins"], "match_types": t["match_types"],
            "cached": t["query"] in have,
            "cached_at": ent.get("fetched_at"),
            "images": ent.get("images") or {},
        })
    pstate = amazon_store.parents_state()
    groups = amazon_targets.group_by_parent(items)
    parents = []
    for g in groups:
        st = pstate.get(g["parent"]) or {}
        parents.append({
            "parent": g["parent"], "terms": len(g["terms"]), "clicks": g["clicks"],
            "adCost": g["adCost"], "done": bool(st),
            "images": st.get("images"), "finished_at": st.get("finished_at"),
        })
    summ = amazon_targets.summary(items)
    summ["parents_done"] = len(pstate)
    summ["local_terms"] = len(have)                  # 目标词里本地已有的
    summ["todo_terms"] = len(items) - len(have)      # 真正还要抓的
    return {"min_clicks": mc, "summary": summ, "groups": parents, "rows": rows}


@router.get("/amazon/local")
def amazon_local(parent: str = "", limit: int = 500) -> Dict[str, Any]:
    """本地仓库概况 + 已缓存的搜索词清单(可按父 ASIN 过滤)

    页面查看某个搜索词时, 只要本地有数据就直接用它, 不再访问亚马逊。
    """
    n = max(1, min(limit, 5000))
    return {"stats": amazon_store.stats(), "rows": amazon_store.list_local(parent)[:n]}


@router.post("/amazon/prefetch")
def amazon_prefetch_start(
        req: AmzPrefetchReq = Body(default=AmzPrefetchReq())) -> Dict[str, Any]:
    """批量预抓取「点击量达标」的搜索词 → 按父 ASIN 落本地(后台任务)

    进度查 /amazon/prefetch/state。串行抓取 + 随机间隔 + 批内长休 + 撞验证码退避,
    断点续抓(本地已有的跳过, 只缺主图则只补图片)。
    """
    try:
        return amazon_prefetch.start(
            min_clicks=req.min_clicks, limit=req.limit, refresh=req.refresh,
            gap_min=req.gap_min, gap_max=req.gap_max,
            screenshot=req.screenshot, scrolls=req.scrolls,
            images=req.images, image_limit=req.image_limit,
            skip_done_parents=req.skip_done_parents)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(400, str(e))


@router.get("/amazon/prefetch/state")
def amazon_prefetch_state() -> Dict[str, Any]:
    """预抓取进度(含最近日志与本地文件数)"""
    return amazon_prefetch.get_state()


@router.post("/amazon/prefetch/stop")
def amazon_prefetch_stop() -> Dict[str, Any]:
    """停止预抓取(当前这条抓完即退出, 已抓成果保留)"""
    return amazon_prefetch.stop()


# ---- 产品视角 (产品 -> 广告活动 -> 搜索词 逐级下钻) ----
@router.get("/product/list")
def product_list(keyword: str = "", order_field: str = "ad_cost",
                 order_dir: str = "desc", page: int = 1,
                 page_size: int = 50) -> Dict[str, Any]:
    """产品列表 —— 以父 ASIN 为一条记录, 多子 ASIN 合并统计

    每行附上该父体的两个进度状态(见 backend/term_status.py):
      拉取完成 / 部分拉取 / 未拉取 (竞品素材是否已拉到本地)
      标记完成 / 部分标记 / 未标记 (搜索词是否已人工打标)
    """
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    out = db.get_products(keyword, order_field, order_dir, page, page_size)
    rows = out.get("rows") or []
    st = term_status.parent_states(
        [r.get("parent_asin") or r.get("asin") or "" for r in rows])
    for r in rows:
        s = st.get(r.get("parent_asin") or r.get("asin") or "") or {}
        r["terms_total"] = int(s.get("terms_total") or 0)
        r["terms_fetched"] = int(s.get("terms_fetched") or 0)
        r["terms_labeled"] = int(s.get("terms_labeled") or 0)
        r["fetch_status"] = s.get("fetch_status") or term_status.K_NONE
        r["fetch_text"] = s.get("fetch_text") or "未拉取"
        r["label_status"] = s.get("label_status") or term_status.K_NONE
        r["label_text"] = s.get("label_text") or "未标记"
    return out


@router.get("/product/campaigns")
def product_campaigns(asin: str = Query(...), parent_asin: str = "") -> Dict[str, Any]:
    """某父 ASIN 下全部子 ASIN 参与的活动(按活动合并) + 活动搜索词数

    asin 传父 ASIN 或子 ASIN 均可; 也可显式用 parent_asin。
    """
    a = (parent_asin or asin or "").strip()
    if not a:
        raise HTTPException(400, "asin 不能为空")
    return db.get_product_campaigns(a)


@router.get("/product/terms")
def product_terms(campaign_id: str = "", scope: str = "", asin: str = "") -> Dict[str, Any]:
    """搜索词/商品投放明细

    - 传 campaign_id: 单个活动的明细 (活动级口径)
    - 传 asin(父/子 ASIN): 该父体下**全部活动**的搜索词汇总(按词合并累加)
    """
    if campaign_id.strip():
        out = db.get_campaign_terms(campaign_id.strip(), scope)
    else:
        a = (asin or "").strip()
        if not a:
            raise HTTPException(400, "campaign_id 与 asin 至少传一个")
        out = db.get_parent_terms(a, scope)
    _inject_term_zh(out.get("rows") or [])      # 行内带上已落库的中文翻译
    _inject_ranks(out.get("rows") or [])        # 行内带上本父体内的四项指标排名
    # 行内带上「竞品素材是否已拉取到本地」+ 该父体的两个进度状态
    p = str(out.get("parent_asin") or "").strip()
    if p:
        flags = term_status.term_flags(
            p, [str(r.get("query") or "") for r in out.get("rows") or []])
        for r in out.get("rows") or []:
            f = flags.get(str(r.get("query") or "")) or {}
            r["fetched"] = bool(f.get("fetched"))
            r["images"] = int(f.get("images") or 0)
        out["status"] = term_status.parent_state(p)
    return out


@router.get("/product/images")
def product_images(asin: str = Query(...), domain: str = "", crawl: bool = False,
                   refresh: bool = False, max_children: int = 3) -> Dict[str, Any]:
    """父 ASIN 的图片(主图 + 全部附图)。

    附图来源: 该父体下**各子 ASIN 商品页**的图廊 —— 子 ASIN 详情页的附图即父 ASIN 的附图。
    crawl=true 时按需用浏览器抓取父体+子体商品页(受最小间隔限流), 再返回聚合结果。
    """
    a = (asin or "").strip()
    if not a:
        raise HTTPException(400, "asin 不能为空")
    dm = domain or config.AMAZON_DOMAIN
    if dm not in amazon.MARKET:
        raise HTTPException(400, f"不支持的站点: {dm}")
    prof = db.get_parent_profile(a, dm)
    status = {"crawled": [], "failed": []}
    if crawl:
        targets = [prof["parent_asin"]] + [k for k in prof["child_asins"]
                                           if k and k != prof["parent_asin"]]
        limit = max(1, min(int(max_children or 3), 12))
        for t in targets[:limit]:
            try:
                amazon_product.fetch_images(t, domain=dm, refresh=refresh)
                status["crawled"].append(t)
            except Exception as e:      # noqa: BLE001
                status["failed"].append({t: str(e)[:120]})
        prof = db.get_parent_profile(a, dm)
    prof["crawl_status"] = status
    return prof


@router.post("/product/images/crawl")
def product_images_crawl(asin: str = Query(...), domain: str = "",
                         refresh: bool = False, max_children: int = 0) -> Dict[str, Any]:
    """后台抓取父 ASIN 的附图(父体 + **全部子体**, 逐个记录已抓取)

    默认只抓「尚未抓过」的 ASIN, refresh=true 则全部重抓。
    同步返回任务信息, 用 /product/images/status 轮询进度。
    """
    a = (asin or "").strip()
    if not a:
        raise HTTPException(400, "asin 不能为空")
    dm = domain or config.AMAZON_DOMAIN
    if dm not in amazon.MARKET:
        raise HTTPException(400, f"不支持的站点: {dm}")
    try:
        return image_jobs.start(a, dm, refresh=refresh, max_children=max_children)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"启动抓取失败: {str(e)[:200]}")


@router.get("/product/images/status")
def product_images_status(asin: str = Query(...), domain: str = "") -> Dict[str, Any]:
    """附图抓取进度"""
    a = (asin or "").strip()
    if not a:
        raise HTTPException(400, "asin 不能为空")
    dm = domain or config.AMAZON_DOMAIN
    st = image_jobs.get_status(a, dm)
    if not st.get("running"):
        st["profile"] = db.get_parent_profile(a, dm)
    return st


@router.get("/product/term_detail")
def product_term_detail(asin: str = Query(...), term: str = Query(...),
                        match_type: str = "", domain: str = "") -> Dict[str, Any]:
    """单个搜索词的详情: 父体商品资料(图片/标题/价格) + 该词指标 + 明细变体

    用于「产品视角 → 搜索词 → 点击某个词」弹出的详情页。
    """
    a = (asin or "").strip()
    t = (term or "").strip()
    if not a or not t:
        raise HTTPException(400, "asin 与 term 不能为空")
    dm = domain or config.AMAZON_DOMAIN
    out = db.get_term_detail(a, t, match_type, dm)
    if not translate.is_asin(t):                 # 顶部展示用: 该词的中文翻译
        hit = db.get_translations(translate.KIND_TERM, [t])
        out["query_zh"] = hit.get(t, "")
    else:
        out["query_zh"] = ""
    out["term_label"] = db.get_term_label(out.get("parent_asin") or a, t)   # 已打标
    out["label_options"] = list(db.TERM_LABELS)
    pa = out.get("parent_asin") or a
    st = amazon_store.parent_term_state(pa, t)      # 该词的竞品素材在不在本地
    out["fetched"] = st["fetched"]
    out["images"] = st["images"]
    out["status"] = term_status.parent_state(pa)    # 该父体的拉取/标记进度
    return out


# ---- 搜索词打标 (紧密 / 宽泛 / 不太相关 / 不相关, 顺序见 db.TERM_LABELS) ----
class TermLabelReq(BaseModel):
    asin: str                      # 父 ASIN
    term: str
    label: str = ""                # 空 = 清除打标


@router.get("/product/term_label")
def term_label_get(asin: str = Query(...), term: str = Query(...)) -> Dict[str, Any]:
    """某个词当前的打标"""
    return {"label": db.get_term_label(asin, term),
            "label_options": list(db.TERM_LABELS)}


@router.get("/product/term_labels")
def term_labels_list(asin: str = Query(...)) -> Dict[str, Any]:
    """某父体下已打标的搜索词清单 (term -> label)"""
    return {"rows": db.get_term_labels(asin), "counts": db.term_label_counts(),
            "label_options": list(db.TERM_LABELS)}


@router.post("/product/term_label")
def term_label_save(req: TermLabelReq = Body(...)) -> Dict[str, Any]:
    """保存打标(代表该父 ASIN 下这个词与本品类的相关程度); label 传空即清除"""
    lb = (req.label or "").strip()
    lb = db.TERM_LABEL_ALIASES.get(lb, lb)      # 老页面可能还发「紧密相关」→ 归一到「紧密」
    if lb and lb not in db.TERM_LABELS:
        raise HTTPException(400, f"打标只能是: {' / '.join(db.TERM_LABELS)}")
    try:
        saved = db.save_term_label(req.asin, req.term, lb)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "label": saved, "asin": req.asin.strip(), "term": req.term.strip()}


# ---- 在线产品 (销售 > 在线产品) ----
@router.get("/online/meta")
def online_meta() -> Dict[str, Any]:
    """列定义 / 店铺 / 筛选候选值 / 数据量"""
    return {
        "fields": db.get_online_meta("head_fields") or [],
        "shops": db.get_online_meta("shops") or [],
        "filters": db.get_online_filters(),
        "total": db.get_online_count(),
        "counts": db.get_online_counts(),
        "updated_at": db.get_online_meta("updated_at"),
    }


@router.get("/online/products")
def online_products(page: int = 1, page_size: int = 50, keyword: str = "",
                    search_field: str = "", filters: str = "",
                    order_field: str = "", order_dir: str = "desc") -> Dict[str, Any]:
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    return db.get_online_products(page, page_size, keyword, search_field,
                                  _parse_filters_obj(filters), order_field, order_dir)


class AiLabelReq(BaseModel):
    asins: List[str] = []
    refresh: bool = False


@router.post("/online/ai_labels")
def online_ai_labels(req: AiLabelReq = Body(default=AiLabelReq())) -> Dict[str, Any]:
    """按 ASIN 取「品名」AI 标签(解析标题 → ≤5 个词的中文短语)

    走哪家模型由「配置」菜单决定(本地 Ollama / workbuddy), 见 /api/settings/ai。
    已缓存的立即返回; 缺失的在后台生成, 用 /online/ai_labels/status 轮询。
    """
    res = ai_label.ensure_labels(req.asins, refresh=req.refresh)
    res["provider"] = ai_label.get_config().get("provider")
    res["available"] = ai_label.available()
    return res


@router.get("/online/ai_labels/status")
def online_ai_labels_status(asins: str = "") -> Dict[str, Any]:
    items = [a.strip() for a in (asins or "").split(",") if a.strip()]
    cached = db.get_ai_labels(items)
    return {"labels": {k: v["label"] for k, v in cached.items() if v.get("label")},
            "job": ai_label.job_state(items),
            "provider": ai_label.get_config().get("provider"),
            "available": ai_label.available(),
            "ollama": ai_label.available("ollama")}


# ---- 配置: 品名解析用哪个模型 ----
class AiConfigReq(BaseModel):
    provider: Optional[str] = None          # ollama | workbuddy
    ollama_url: Optional[str] = None
    ollama_model: Optional[str] = None
    workbuddy_base: Optional[str] = None
    workbuddy_key: Optional[str] = None     # 传 __KEEP__ 表示不改
    workbuddy_model: Optional[str] = None
    workbuddy_fallback: Optional[List[str]] = None


@router.get("/settings/ai")
def ai_settings_get() -> Dict[str, Any]:
    """当前 AI 配置(不含明文 Key) + 两家可用性"""
    return {"config": ai_label.public_config(),
            "status": {"ollama": ai_label.available("ollama"),
                       "workbuddy": ai_label.available("workbuddy")}}


@router.post("/settings/ai")
def ai_settings_save(req: AiConfigReq = Body(...)) -> Dict[str, Any]:
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    if patch.get("provider") and patch["provider"] not in ai_label.PROVIDERS:
        raise HTTPException(400, "provider 只能是 ollama 或 workbuddy")
    ai_label.save_config(patch)
    return {"saved": True, "config": ai_label.public_config(),
            "status": {"ollama": ai_label.available("ollama"),
                       "workbuddy": ai_label.available("workbuddy")}}


@router.post("/settings/ai/test")
def ai_settings_test(req: AiConfigReq = Body(default=AiConfigReq())) -> Dict[str, Any]:
    """用当前(或表单里填的)配置真跑一次, 看能否出短语"""
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    return ai_label.test_provider(patch)


# ---- 搜索词中文翻译(模型生成, 落 translations 表) ----
class TranslateReq(BaseModel):
    kind: str = translate.KIND_TERM     # search_term | product_name
    sources: List[str] = []
    refresh: bool = False               # True = 已翻译过的也重翻


@router.post("/translate")
def translate_ensure(req: TranslateReq = Body(default=TranslateReq())) -> Dict[str, Any]:
    """取搜索词的中文翻译; 缺失的启动后台生成(串行), 用 /translate/status 轮询

    已翻译过的直接返回(不再调模型); ASIN 形态的 query 自动跳过。
    """
    kind = req.kind if req.kind in (translate.KIND_TERM, translate.KIND_PRODUCT) \
        else translate.KIND_TERM
    try:
        res = translate.ensure(kind, req.sources, refresh=req.refresh)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"翻译任务启动失败: {str(e)[:200]}")
    res["provider"] = ai_label.get_config().get("provider")
    res["available"] = ai_label.available()
    return res


@router.get("/translate/status")
def translate_status(kind: str = translate.KIND_TERM, sources: str = "") -> Dict[str, Any]:
    """轮询翻译进度与结果 (sources 逗号分隔)"""
    items = [s.strip() for s in (sources or "").split(",") if s.strip()]
    return {"translations": db.get_translations(kind, items),
            "job": translate.job_state(kind, items),
            "provider": ai_label.get_config().get("provider"),
            "available": ai_label.available()}


@router.get("/translate/list")
def translate_list(kind: str = translate.KIND_TERM, limit: int = 5000) -> Dict[str, Any]:
    """已落库的翻译清单(按时间倒序) + 各类条数"""
    return {"rows": db.get_all_translations(kind, limit), "counts": db.translation_counts()}


def _parse_filters_obj(raw: str) -> Dict[str, List[str]]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except Exception:       # noqa: BLE001
        raise HTTPException(400, "filters 需为 JSON 对象")


@router.post("/online/crawl")
def online_crawl() -> Dict[str, Any]:
    """采集在线产品全量数据 (销售 > 在线产品)"""
    try:
        n = crawl_online_products()
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"采集失败: {str(e)[:200]}")
    return {"ok": True, "count": n}


def crawl_online_products(progress=None) -> int:
    """同步采集在线产品: 字段目录 + 店铺 + 子体全量 + 父体全量

    注意: 「子体 / 父体」在原站是两个**不同端点**, 必须分别采集:
        子体 -> /api/gw/sellfox/sellfox-product/sellfox/product/pageList
        父体 -> /api/parent/product/pageList.json
    只采子体的话, 前端切到「父体」会查不到数据。
    """
    from backend import online_product as op
    sess = op.make_session()
    fields = op.fetch_head_fields(sess)
    db.save_online_meta("head_fields", fields)
    db.save_online_meta("shops", op.fetch_shops(sess))
    n = 0
    for page_type, label in (("child", "子体"), ("parents", "父体")):
        if progress:
            progress(f"[{label}] 采集…")
        rows = op.fetch_all(sess, page_type=page_type, progress=progress)
        n += db.save_online_products(rows)
        db.save_online_meta(f"count_{page_type}", len(rows))
    db.save_online_meta("counts", db.get_online_counts())
    db.save_online_meta("updated_at", db.now())
    return n


# ---- Cookie ----
@router.get("/cookies/status")
def cookie_status() -> Dict[str, Any]:
    c = load_cookies()
    return {"saved": bool(c), "count": len(c or {}),
            "keys": list((c or {}).keys())[:20]}


@router.post("/cookies")
def set_cookies(cookie_string: str = Body("", embed=True)) -> Dict[str, Any]:
    c = parse_cookie_string(cookie_string)
    if not c:
        raise HTTPException(400, "Cookie 解析为空")
    save_cookies(c)
    return {"saved": True, "count": len(c)}


@router.post("/cookies/sync")
def sync_cookies() -> Dict[str, Any]:
    try:
        c = sync_from_browser()
    except Exception as e:      # noqa: BLE001
        raise HTTPException(400, str(e))
    return {"saved": True, "count": len(c), "keys": list(c.keys())[:20]}


# ---- 搜索词 × SKU 相关性 ----
@router.get("/term_relevance")
def term_relevance(min_spend: float = Query(100.0), use_ai: bool = True) -> Dict[str, Any]:
    """花费>阈值的搜索词 × 其活动内 SKU 的相关性(文本分即时返回; 语义分后台补)"""
    return relevance.analyze(min_spend, use_ai=use_ai)


@router.get("/term_relevance/status")
def term_relevance_status(min_spend: float = Query(100.0)) -> Dict[str, Any]:
    """相关性语义判断进度 + 最新结果(含缓存)"""
    return relevance.status(min_spend)


@router.get("/term_relevance/pair")
def term_relevance_pair(term: str = Query(...), asin: str = Query(...),
                        force: bool = False) -> Dict[str, Any]:
    """单个搜索词 × 父 SKU 标题 的相关性(搜索词详情页用)

    asin 传父/子 ASIN 均可; 语义分未缓存时现场调大模型(约 2~5 秒);
    force=true 忽略缓存重新判断。
    """
    t = (term or "").strip()
    a = (asin or "").strip()
    if not t or not a:
        raise HTTPException(400, "term 与 asin 不能为空")
    title = db.get_parent_title(a)
    out = relevance.check_pair(t, title, force=force, asin=a, source="pair")
    out["asin"] = a
    return out


class BatchRelReq(BaseModel):
    asins: List[str]
    mode: str = "top"                 # top=花费前N | threshold=花费>=X
    top_n: int = 10
    min_spend: float = 100.0


@router.post("/term_relevance/batch")
def term_relevance_batch(req: BatchRelReq = Body(...)) -> Dict[str, Any]:
    """批量关联度分析: 选中多个父 SKU, 按选词方式批量判断 (后台任务)"""
    asins = [a.strip() for a in (req.asins or []) if a.strip()]
    if not asins:
        raise HTTPException(400, "请先勾选至少一个父 SKU")
    if req.mode not in ("top", "threshold"):
        raise HTTPException(400, "mode 须为 top 或 threshold")
    try:
        return relevance.start_batch(asins, req.mode, req.top_n, req.min_spend)
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@router.get("/term_relevance/batch/status")
def term_relevance_batch_status() -> Dict[str, Any]:
    """批量关联度分析进度与结果"""
    return relevance.batch_status()


@router.post("/image_similarity")
def image_similarity_start(asin: str = Query(...), term: str = Query(...),
                           top_n: int = Query(10),
                           engine: str = Query("llm")) -> Dict[str, Any]:
    """深度检测: SKU 主图 × 亚马逊搜索结果前 N 主图 的相似度 (后台任务)

    - engine=llm(默认): 用「配置管理」页选定的**视觉大模型**逐对比较;
      单张失败自动回落本地感知特征(0.6×dHash + 0.4×颜色直方图);
    - engine=phash: 直接用本地算法(不调模型);
    - 只读已缓存的搜索结果(不触发抓取)。
    用 /image_similarity/status 轮询进度与结果。
    """
    from backend import image_sim
    t = (term or "").strip()
    a = (asin or "").strip()
    if not t or not a:
        raise HTTPException(400, "term 与 asin 不能为空")
    if engine not in ("llm", "phash"):
        raise HTTPException(400, "engine 须为 llm 或 phash")
    return image_sim.start_job(a, t, max(1, min(top_n, 20)), engine)


@router.get("/image_similarity/status")
def image_similarity_status(asin: str = Query(...), term: str = Query("")
                            ) -> Dict[str, Any]:
    """深度检测进度与结果"""
    from backend import image_sim
    return image_sim.job_status((asin or "").strip(), (term or "").strip())


# ---- 深度检测: 标题同类 + 词级结论 ----
@router.post("/title_similarity")
def title_similarity_start(asin: str = Query(...), term: str = Query(...),
                           top_n: int = Query(10)) -> Dict[str, Any]:
    """标题同类匹配: 我的标题 × 每条竞品标题 (后台任务, 逐条落库)

    原始标题直接送大模型判断(不做本地正则预处理); 尺寸/颜色/品牌/促销词
    由提示词要求模型自行忽略。
    """
    from backend import deep_check
    t, a = (term or "").strip(), (asin or "").strip()
    if not t or not a:
        raise HTTPException(400, "term 与 asin 不能为空")
    return deep_check.start_title_job(a, t, max(1, min(top_n, 20)))


@router.get("/title_similarity/status")
def title_similarity_status(asin: str = Query(...), term: str = Query("")
                            ) -> Dict[str, Any]:
    from backend import deep_check
    return deep_check.title_job_status((asin or "").strip(), (term or "").strip())


@router.get("/deep_check")
def deep_check_eval(asin: str = Query(...), term: str = Query(...),
                    refresh: bool = False) -> Dict[str, Any]:
    """词级结论: 该词的结果集与我的品有多像 + 是否否定建议

    综合: 词级判断(搜索意图) + 标题同类匹配率 + 图片相似率 + 广告表现(花费/订单)
    """
    from backend import deep_check
    t, a = (term or "").strip(), (asin or "").strip()
    if not t or not a:
        raise HTTPException(400, "term 与 asin 不能为空")
    return deep_check.evaluate(a, t, refresh=refresh)


# ---- 配置管理 ----
class SettingsReq(BaseModel):
    wb_model: Optional[str] = None
    wb_vision_model: Optional[str] = None
    ollama_model: Optional[str] = None


def _ollama_models() -> List[str]:
    try:
        req = urllib.request.Request(config.OLLAMA_URL + "/api/tags")
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                req, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name") for m in data.get("models") or [] if m.get("name")]
    except Exception:           # noqa: BLE001
        return []


@router.get("/settings")
def get_settings() -> Dict[str, Any]:
    from backend.vendor.workbuddy.client import KNOWN_MODELS
    return {
        "wb_model": relevance.current_model("workbuddy"),
        "wb_vision_model": relevance.current_model("vision"),
        "wb_models": list(KNOWN_MODELS),
        "ollama_model": relevance.current_model("ollama"),
        "ollama_models": _ollama_models(),
        "updated_at": db.get_online_meta("wb_model_updated_at"),
    }


@router.post("/settings")
def save_settings(req: SettingsReq = Body(default=SettingsReq())) -> Dict[str, Any]:
    from backend.vendor.workbuddy.client import KNOWN_MODELS
    changed = {}
    # 统一写入口: 文本模型会同时写 online_meta 与「配置」弹窗那份配置, 两处保持一致
    for val, kind, key in ((req.wb_model, "text", "wb_model"),
                           (req.wb_vision_model, "vision", "wb_vision_model")):
        if val:
            if val not in KNOWN_MODELS:
                raise HTTPException(400, f"未知模型: {val}")
            wb_config.set_model(val, kind)
            changed[key] = val
    if req.ollama_model:
        wb_config.set_model(req.ollama_model, "ollama")
        changed["ollama_model"] = req.ollama_model
    if changed:
        db.save_online_meta("wb_model_updated_at", db.now())
    return {"saved": True, **changed}


@router.post("/settings/test")
def settings_test(kind: str = Query("text")) -> Dict[str, Any]:
    """验证当前模型可用性 (kind=text 文本判断 / vision 视觉比对)"""
    import time as _t
    from backend import image_sim
    t0 = _t.time()
    if kind == "vision":
        # 用两张某商品的真实主图做一次视觉比对
        try:
            rows = db.latest_amazon_results("金網フェンス", 2) or db.latest_amazon_results("", 2)
        except Exception:       # noqa: BLE001
            rows = []
        try:
            if len(rows) >= 2:
                a, b = rows[0]["image"], rows[1]["image"]
            else:
                a = b = db.get_parent_main_image("B0HJ1MX7Y4")
            if not a or not b:
                return {"ok": False, "elapsed": round(_t.time() - t0, 1),
                        "error": "没有可用图片做视觉测试"}
            j = image_sim.vision_judge(image_sim._b64(a), image_sim._b64(b),
                                       image_sim.vision_model())
            return {"ok": True, "elapsed": round(_t.time() - t0, 1),
                    "score": j["score"], "reason": j["reason"],
                    "model": j.get("model")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "elapsed": round(_t.time() - t0, 1),
                    "error": str(e)[:200]}
    try:
        j = relevance._ai_judge("蔦巻きポール", "蔦巻きポール トマト支柱 園芸支柱 植物サポート 耐候性")
        return {"ok": True, "elapsed": round(_t.time() - t0, 1),
                "score": j["score"], "reason": j["reason"], "model": j.get("model")}
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "elapsed": round(_t.time() - t0, 1),
                "error": str(e)[:200]}
