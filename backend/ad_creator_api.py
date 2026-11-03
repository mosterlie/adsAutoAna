# -*- coding: utf-8 -*-
"""广告创建(亚马逊 Bulk 批量广告表) 接口 —— 自 autoAds 平移

与 autoAds 的差异只有**数据来源**:
  · autoAds: 上传赛狐导出 Excel → parser 解析
  · 本项目: 默认直接取「在线产品」库(GET /products)，Excel 上传保留为兼容入口

接口前缀 /api/adcreator，前端页面 /static/ad-creator.html
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from backend.ad_creator import db_source
from backend.ad_creator.budget_engine import build_shop_payload
from backend.ad_creator.bulk_generator import generate_bulk_excel
from backend.ad_creator.parser import parse_sellfox_excel_multi_shop
from backend.ad_creator.translator import noun_matcher

router = APIRouter(prefix="/api/adcreator")


class ExportRequest(BaseModel):
    products: List[Dict[str, Any]] = []
    settings: Dict[str, Any] = {}


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
def products(mark: str = "", shops: str = "", base_budget: float = 100.0) -> Dict[str, Any]:
    """从本地在线产品库聚合出「按店铺分层」的商品列表与预算建议

    mark: pass / fail 只看打过该标记的品；shops: 逗号分隔的店铺名白名单。
    """
    shop_list = [s.strip() for s in (shops or "").split(",") if s.strip()]
    parsed = db_source.build_parsed(mark=mark, shops=shop_list)
    shop_payload = build_shop_payload(parsed, base_budget=base_budget)
    return {
        "shops": shop_payload,
        "source": "db",
        "summary": db_source.source_summary(),
        "header_row": 0,
        "resolved_columns": parsed.get("resolved_columns", {}),
        "parent_count": parsed.get("parent_count", 0),
    }


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


@router.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "engine": "offline-noun-dict", "version": "9.0.0",
            "data_source": "online_products", "dict_total": len(noun_matcher.dictionary)}
