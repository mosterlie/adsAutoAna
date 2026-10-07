# -*- coding: utf-8 -*-
"""FastAPI 路由: 采集控制 + 数据查询"""
import json
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

import config
from backend import amazon, amazon_product, crawler, database as db, image_jobs
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
    return db.get_records(tab, page, page_size, keyword, shop_id, scope,
                          order_field, order_dir, range_start, range_end,
                          _parse_filters(filters), search_field, search_mode)


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
        return amazon.fetch(q, domain=domain, screenshot=screenshot,
                            refresh=refresh, ttl=ttl)
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"抓取失败: {str(e)[:200]}")


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


# ---- 产品视角 (产品 -> 广告活动 -> 搜索词 逐级下钻) ----
@router.get("/product/list")
def product_list(keyword: str = "", order_field: str = "ad_cost",
                 order_dir: str = "desc", page: int = 1,
                 page_size: int = 50) -> Dict[str, Any]:
    """产品列表 —— 以父 ASIN 为一条记录, 多子 ASIN 合并统计"""
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    return db.get_products(keyword, order_field, order_dir, page, page_size)


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
        return db.get_campaign_terms(campaign_id.strip(), scope)
    a = (asin or "").strip()
    if not a:
        raise HTTPException(400, "campaign_id 与 asin 至少传一个")
    return db.get_parent_terms(a, scope)


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
    return db.get_term_detail(a, t, match_type, dm)


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
