# -*- coding: utf-8 -*-
"""广告创建(亚马逊 Bulk 批量广告表) 接口 —— 自 autoAds 平移

与 autoAds 的差异只有**数据来源**:
  · autoAds: 上传赛狐导出 Excel → parser 解析
  · 本项目: 默认直接取「在线产品」库(GET /products)，Excel 上传保留为兼容入口

接口前缀 /api/adcreator，前端页面 /static/ad-creator.html
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.ad_creator import db_source, sellfox_products
from backend.ad_creator.budget_engine import build_shop_payload
from backend.ad_creator.bulk_generator import generate_bulk_excel
from backend.ad_creator.parser import parse_sellfox_excel_multi_shop
from backend.ad_creator.suggest_bid import (
    DEFAULT_STRATEGY, _friendly, fetch_bids, resolve_shop_id,
    search_asins_for_targeting)
from backend.ad_creator.translator import noun_matcher
from backend.cookies import load_cookies

router = APIRouter(prefix="/api/adcreator")


class ExportRequest(BaseModel):
    products: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}


class SuggestBidRequest(BaseModel):
    shop: str = ""                 # 店铺名（后端查 shopId）或直接给数字 shopId
    shop_id: str = ""
    bidding_strategy: str = ""     # LEGACY_FOR_SALES / AUTO_FOR_SALES / MANUAL
    asins: List[str] = []          # 当前已选的全部商品 ASIN（合并一次请求, 对齐赛狐）
    placement_top: Optional[float] = None       # 搜索顶部加价 %
    placement_rest: Optional[float] = None      # 其余位置加价 %
    placement_product: Optional[float] = None   # 商品页加价 %


class DictEntry(BaseModel):
    ja: str
    zh: str


class RematchItem(BaseModel):
    key: str = ""
    parent_sku: str = ""
    raw_name: str = ""


class RematchRequest(BaseModel):
    items: List[RematchItem] = []


# ---------------------------------------------------------------------------
# 商品数据（本项目在线产品库）
# ---------------------------------------------------------------------------
@router.get("/products")
def products(mark: str = "", shops: str = "", base_budget: float = 100.0,
             source: str = "db") -> Dict[str, Any]:
    """广告创建的商品列表

    source=db（默认）: 直接读本地在线产品库。
    source=sellfox: 走赛狐在线接口 productRightSpPageListSearch 拉取；
        接口不可用（Cookie 失效等）时自动回退本地在线产品库。
    """
    shop_list = [s.strip() for s in (shops or "").split(",") if s.strip()]

    if source != "db":
        try:
            cookies = load_cookies()
            if not cookies:
                raise RuntimeError("未找到登录 Cookie")
            want_ids = []
            for name in shop_list:
                sid = resolve_shop_id(name)
                if sid:
                    want_ids.append(sid)
            if not want_ids:       # 未指定店铺 → 全部已知 shopId
                import re as _re
                with db_source.db.get_conn() as conn:
                    want_ids = [str(r["s"]) for r in conn.execute(
                        "SELECT DISTINCT json_extract(raw_json,'$.shopId') s "
                        "FROM online_products "
                        "WHERE json_extract(raw_json,'$.shopId') IS NOT NULL")]
                want_ids = [s for s in want_ids if _re.fullmatch(r"\d+", s)]
            parsed = sellfox_products.build_parsed(cookies, want_ids, mark=mark)
            if parsed["shops"]:
                shop_payload = build_shop_payload(parsed, base_budget=base_budget)
                return {
                    "shops": shop_payload,
                    "source": "sellfox",
                    "summary": parsed.get("summary") or db_source.source_summary(),
                    "header_row": 0,
                    "resolved_columns": parsed.get("resolved_columns", {}),
                    "parent_count": parsed.get("parent_count", 0),
                }
        except Exception as exc:    # noqa: BLE001 - 赛狐接口不可用 → 回退本地库
            fallback_note = f"赛狐接口不可用，已回退本地库：{_friendly(str(exc))}"
        else:
            fallback_note = "赛狐接口未返回商品，已回退本地库"
    else:
        fallback_note = ""

    parsed = db_source.build_parsed(mark=mark, shops=shop_list)
    shop_payload = build_shop_payload(parsed, base_budget=base_budget)
    summary = db_source.source_summary()
    if fallback_note:
        summary["note"] = fallback_note
    return {
        "shops": shop_payload,
        "source": "db",
        "summary": summary,
        "header_row": 0,
        "resolved_columns": parsed.get("resolved_columns", {}),
        "parent_count": parsed.get("parent_count", 0),
    }


@router.get("/portfolios")
def ad_portfolios(shop: str = "", shop_id: str = "", refresh: int = 0) -> Dict[str, Any]:
    """广告组合下拉（对齐赛狐 ad-create 页的取数方式）

    按店铺走赛狐 ``/api/portfolio/getAllPortfolioDataProductRight.json``；
    取不到时回退本地 ad_portfolios 表，``source`` 标明来源。
    """
    from backend.ad_creator import portfolios as pf

    sid = (shop_id or "").strip() or resolve_shop_id(shop)
    if not sid:
        return {"rows": [], "source": "empty", "note": "未选择店铺", "shopId": ""}
    cookies = load_cookies() or {}
    try:
        out = pf.get_portfolios(cookies, sid, shop_name=shop, force=bool(refresh))
    except Exception as exc:            # noqa: BLE001 - 不让下拉拖垮页面
        raise HTTPException(400, _friendly(str(exc))) from exc
    return {**out, "shopId": sid}


@router.post("/analyze")
async def analyze_file(file: UploadFile = File(...)) -> Dict[str, Any]:
    """兼容入口：上传赛狐导出的 Excel（与原 autoAds 行为一致）"""
    try:
        contents = await file.read()
        parsed = parse_sellfox_excel_multi_shop(contents)
        shop_payload = build_shop_payload(parsed, base_budget=100.0)
        if not shop_payload:
            raise ValueError("未从 Excel 中解析出任何有效的商品及店铺数据。")
        return {
            "shops": shop_payload,
            "source": "excel",
            "file_name": file.filename,
            "summary": db_source.source_summary(),
            "header_row": parsed.get("header_row", 0),
            "resolved_columns": parsed.get("resolved_columns", {}),
        }
    except HTTPException:
        raise
    except Exception as exc:        # noqa: BLE001 - 精确错误要透传给前端
        raise HTTPException(400, f"解析失败：{exc}") from exc


@router.post("/generate")
def generate_file(payload: ExportRequest) -> StreamingResponse:
    """按勾选的商品 + 配置生成亚马逊 Bulk Excel 并下载"""
    try:
        buffer = generate_bulk_excel(payload.products, payload.settings)
    except Exception as exc:        # noqa: BLE001
        raise HTTPException(400, f"生成失败：{exc}") from exc
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=amazon_bulk_ads.xlsx"},
    )


# ---------------------------------------------------------------------------
# 广告活动记录：创建入库存起来，需要时再（批量）导出
# ---------------------------------------------------------------------------
class RecordCreateRequest(BaseModel):
    products: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}
    shop_name: str = ""


class RecordExportRequest(BaseModel):
    ids: List[int] = []
    mode: str = "auto"             # create | update | auto


class RecordUpdateRequest(BaseModel):
    """「修改」弹窗提交的字段（只提交改动的项）。"""
    campaign_name: Optional[str] = None
    ad_group_name: Optional[str] = None
    budget: Optional[float] = None
    default_bid: Optional[float] = None
    bid_close: Optional[float] = None
    bid_loose: Optional[float] = None
    bid_substitutes: Optional[float] = None
    bid_complements: Optional[float] = None
    state: Optional[str] = None
    start_date: Optional[str] = None
    amazon_campaign_id: Optional[str] = None
    amazon_ad_group_id: Optional[str] = None
    amazon_ids: Optional[Dict[str, Any]] = None      # 赛狐一键回填的完整 ID 结构


@router.post("/records")
def create_records(payload: RecordCreateRequest) -> Dict[str, Any]:
    """生成 Bulk 行数据并入库（不再直接下载文件）。"""
    from backend.ad_creator import records as rec

    try:
        return rec.create_records(payload.products, payload.settings, payload.shop_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:        # noqa: BLE001
        raise HTTPException(500, f"创建失败：{str(exc)[:200]}") from exc


@router.get("/records")
def list_records(keyword: str = "", page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    from backend.ad_creator import records as rec

    return rec.list_records(keyword=keyword, page=page, page_size=page_size)


@router.get("/amazon-ids")
def amazon_ids(campaign_name: str, shop: str = "", shop_id: str = "") -> Dict[str, Any]:
    """按广告活动名从赛狐拉取亚马逊真实 ID（活动/广告组/投放/否定/广告）。"""
    from backend.ad_creator import amazon_ids as aid

    sid = (shop_id or "").strip() or resolve_shop_id(shop)
    if not sid:
        raise HTTPException(400, "未选择店铺，无法查询赛狐数据")
    try:
        return aid.lookup(campaign_name, [int(sid)])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:            # noqa: BLE001
        raise HTTPException(500, f"查询失败：{str(exc)[:200]}") from exc


@router.get("/records/{rid}")
def get_record(rid: int) -> Dict[str, Any]:
    from backend.ad_creator import records as rec

    try:
        return rec.get_record(rid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.patch("/records/{rid}")
def update_record(rid: int, payload: RecordUpdateRequest) -> Dict[str, Any]:
    """修改单条记录（名称/预算/竞价/状态/亚马逊真实 ID）。"""
    from backend.ad_creator import records as rec

    fields = payload.model_dump(exclude_none=True) if hasattr(payload, "model_dump") \
        else payload.dict(exclude_none=True)
    try:
        return rec.update_record(rid, fields)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/records/export")
def export_records(payload: RecordExportRequest) -> StreamingResponse:
    """勾选多条记录 → 合并导出一个 Bulk xlsx（create / update / auto）。"""
    from backend.ad_creator import records as rec

    try:
        buffer, fname, _count = rec.export_records(payload.ids, payload.mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.delete("/records")
def delete_records(ids: str = Query("")) -> Dict[str, Any]:
    from backend.ad_creator import records as rec

    id_list = [int(x) for x in re.split(r"[,，\s]+", ids or "") if x.strip().isdigit()]
    return {"deleted": rec.delete_records(id_list)}


# ---------------------------------------------------------------------------
# 离线名词词库（日文标题 → 中文品名）
# ---------------------------------------------------------------------------
@router.get("/dictionary")
def get_dictionary() -> Dict[str, Any]:
    return {**noun_matcher.stats(), "entries": noun_matcher.list_entries()}


@router.get("/dictionary/stats")
def get_dict_stats() -> Dict[str, Any]:
    return {"total_count": len(noun_matcher.dictionary)}


@router.get("/dictionary/export")
def export_dictionary() -> JSONResponse:
    payload = json.dumps(noun_matcher.export_dictionary(), ensure_ascii=False, indent=2)
    return JSONResponse(content=json.loads(payload),
                        headers={"Content-Disposition": "attachment; filename=ja_zh_dict.json"})


@router.post("/dictionary")
def upsert_dictionary(entry: DictEntry) -> Dict[str, Any]:
    ja, zh = entry.ja.strip(), entry.zh.strip()
    if not ja or not zh:
        raise HTTPException(400, "日文词与中文词都不能为空")
    noun_matcher.upsert(ja, zh)
    return {"ok": True, "total_count": len(noun_matcher.dictionary)}


@router.delete("/dictionary")
def delete_dictionary(ja: str = Query("")) -> Dict[str, Any]:
    if not noun_matcher.delete(ja):
        raise HTTPException(404, f"词库中不存在：{ja}")
    return {"ok": True, "total_count": len(noun_matcher.dictionary)}


@router.post("/dictionary/reload")
def reload_dictionary() -> Dict[str, Any]:
    return {"ok": True, "total_count": noun_matcher.reload()}


@router.post("/dictionary/import")
async def import_dictionary(file: UploadFile = File(...)) -> Dict[str, Any]:
    try:
        content = await file.read()
        try:
            data = json.loads(content.decode("utf-8-sig"))
        except UnicodeDecodeError:
            data = json.loads(content.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError('导入的词典格式必须为 JSON 对象键值对: {"日文名词": "中文名词"}')
        result = noun_matcher.update_dictionary(data)
        return {"status": "success", "imported_count": result["imported"],
                "purged_keys": result["purged"], "total_count": result["total"]}
    except HTTPException:
        raise
    except Exception as exc:        # noqa: BLE001
        raise HTTPException(400, f"词库导入失败: {exc}") from exc


@router.post("/dictionary/rematch")
def rematch_names(payload: RematchRequest = Body(default=RematchRequest())) -> Dict[str, Any]:
    """用最新词库重匹配一批标题（不落盘，供前端就地回填）"""
    matched: Dict[str, str] = {}
    hit_skus: List[str] = []
    miss_skus: List[str] = []
    for item in payload.items:
        key = (item.key or item.parent_sku or "").strip()
        raw = (item.raw_name or "").strip()
        if not key or not raw:
            continue
        zh = noun_matcher.match_noun_only(raw)
        if zh:
            matched[key] = zh
            hit_skus.append(key)
        else:
            matched[key] = noun_matcher.match_first_noun(raw)
            miss_skus.append(key)
    return {"matched_names": matched, "hit_skus": hit_skus, "miss_skus": miss_skus,
            "matched_count": len(matched), "hit_count": len(hit_skus),
            "miss_count": len(miss_skus), "dict_total": len(noun_matcher.dictionary)}


@router.get("/search-asins-for-targeting")
def search_asins(shop: str = "", shop_id: str = "", kw: str = "") -> Dict[str, Any]:
    """否定商品搜索：对齐赛狐专用接口 searchAsinsForTargeting（逐个 ASIN 查）"""
    sid = (shop_id or "").strip() or resolve_shop_id(shop)
    if not sid:
        raise HTTPException(400, f"未能确定店铺 shopId：{shop or '未指定'}")
    cookies = load_cookies()
    if not cookies:
        raise HTTPException(400, "未找到登录 Cookie，请先在「同步登录态」里同步赛狐登录态")
    kws = [k.strip().upper() for k in re.split(r"[,，;；\s]+", kw or "") if k.strip()][:20]
    if not kws:
        return {"items": [], "shopId": sid}
    try:
        items = search_asins_for_targeting(cookies, sid, kws)
    except Exception as exc:    # noqa: BLE001
        raise HTTPException(400, _friendly(str(exc))) from exc
    return {"items": items, "shopId": sid}


@router.get("/sellfox-search")
def sellfox_search(shop: str = "", shop_id: str = "", kw: str = "") -> Dict[str, Any]:
    """赛狐在线搜索商品（按 ASIN，可多个，逗号/空格分隔）"""
    sid = (shop_id or "").strip() or resolve_shop_id(shop)
    if not sid:
        raise HTTPException(400, f"未能确定店铺 shopId：{shop or '未指定'}")
    cookies = load_cookies()
    if not cookies:
        raise HTTPException(400, "未找到登录 Cookie，请先在「同步登录态」里同步赛狐登录态")
    kws = [k for k in re.split(r"[,，;；\s]+", kw or "") if k][:10]
    if not kws:
        return {"items": [], "shopId": sid}
    try:
        rows = sellfox_products.search_by_asins(cookies, sid, kws)
    except Exception as exc:    # noqa: BLE001
        raise HTTPException(400, _friendly(str(exc))) from exc
    return {"items": sellfox_products.simplify_rows(rows), "shopId": sid}


@router.post("/suggest-bids")
def suggest_bids(payload: SuggestBidRequest) -> Dict[str, Any]:
    """按赛狐口径取建议竞价：当前已选的全部 ASIN 合并一次请求

    返回 ``{shopId, bids:{close-match:[低,中,高], ...}, strategy, asin_count}``；
    取不到时 bids 为空，前端保持 ``-``。
    """
    shop_id = (payload.shop_id or "").strip() or resolve_shop_id(payload.shop)
    if not shop_id:
        raise HTTPException(400, f"未能确定店铺 shopId：{payload.shop or '未指定'}")
    cookies = load_cookies()
    if not cookies:
        raise HTTPException(400, "未找到登录 Cookie，请先在「同步登录态」里同步赛狐登录态")

    strategy = (payload.bidding_strategy or DEFAULT_STRATEGY).strip().upper()
    try:
        bids = fetch_bids(cookies, shop_id, payload.asins, strategy,
                          placement_top=payload.placement_top,
                          placement_rest=payload.placement_rest,
                          placement_product=payload.placement_product)
    except Exception as exc:        # noqa: BLE001 - 业务错误(如登录态失效)透传给前端提示
        raise HTTPException(400, _friendly(str(exc))) from exc
    return {"ok": True, "shopId": shop_id, "bids": bids, "strategy": strategy,
            "asin_count": len([a for a in payload.asins if str(a).strip()])}


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "engine": "offline-noun-dict", "version": "9.0.0",
            "data_source": "online_products", "dict_total": len(noun_matcher.dictionary)}
