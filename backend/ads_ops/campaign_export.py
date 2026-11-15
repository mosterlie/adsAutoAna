# -*- coding: utf-8 -*-
"""活动分析 Excel 导出。

工作簿固定 3 个 sheet：
  汇总 / 搜索词 / 搜索ASIN
"""
from __future__ import annotations

import io
from typing import Any, Dict, List

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

TARGET_TYPE_CN = {
    "auto": "自动投放",
    "keyword": "关键词投放",
    "targeting": "商品定向",
}
TARGET_TEXT_CN = {
    "queryHighRelMatches": "紧密匹配",
    "queryBroadRelMatches": "宽泛匹配",
    "asinSubstituteRelated": "同类商品",
    "asinAccessoryRelated": "关联商品",
}
MATCH_TYPE_CN = {
    "close_match": "紧密匹配",
    "loose_match": "宽泛匹配",
    "complements": "关联商品",
    "substitutes": "同类商品",
    "exact": "精准",
    "phrase": "词组",
    "broad": "广泛",
    "negativeExact": "否定精准",
    "negativePhrase": "否定词组",
}
QUERY_TYPE_CN = {"keyword": "搜索词", "asin": "搜索 ASIN"}
MATCH_SHORT_CN = {
    "loose_match": "宽泛", "close_match": "紧密",
    "complements": "关联", "substitutes": "同类",
    "exact": "精准", "phrase": "词组", "broad": "广泛",
}
MATCH_ORDER = ["loose_match", "close_match", "substitutes", "complements",
               "exact", "phrase", "broad"]


def label_target_type(value: Any) -> str:
    raw = str(value or "")
    return TARGET_TYPE_CN.get(raw, raw)


def label_target_text(value: Any) -> str:
    raw = str(value or "")
    return TARGET_TEXT_CN.get(raw, raw)


def label_match(value: Any) -> str:
    raw = str(value or "")
    return MATCH_TYPE_CN.get(raw, raw)


def label_query_type(value: Any) -> str:
    raw = str(value or "")
    return QUERY_TYPE_CN.get(raw, raw)


def _zh_text(value: Any) -> str:
    text = str(value if value is not None else "")
    mapping = {}
    mapping.update(TARGET_TYPE_CN)
    mapping.update(TARGET_TEXT_CN)
    mapping.update(MATCH_TYPE_CN)
    mapping.update(QUERY_TYPE_CN)
    for key in sorted(mapping, key=len, reverse=True):
        text = text.replace(key, mapping[key])
    return text


def _num_text(value: Any, field: str) -> str:
    if value is None or value == "":
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if field in ("cost", "cpc", "price"):
        return f"{number:.2f}"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.2f}"


def _break_text(row: Dict[str, Any], field: str) -> str:
    breakdown = row.get("match_breakdown") or {}
    if len(breakdown) < 2:
        return ""
    keys = [x for x in MATCH_ORDER if x in breakdown]
    keys += [x for x in breakdown if x not in keys]
    return " / ".join(
        f"{MATCH_SHORT_CN.get(k, label_match(k))} {_num_text(breakdown[k].get(field), field)}"
        for k in keys)


def _metric_cell(row: Dict[str, Any], field: str) -> Any:
    total = row.get(field)
    if field == "acos":
        total_text = "-" if total is None else f"{_num_text(total, field)}%"
    else:
        total_text = _num_text(total, field)
    extra = _break_text(row, field)
    return f"{total_text}（{extra}）" if extra else total_text


def _header(ws, row: int, titles: List[str]) -> None:
    fill = PatternFill("solid", fgColor="EAF0FF")
    for col, title in enumerate(titles, 1):
        cell = ws.cell(row=row, column=col, value=title)
        cell.font = Font(bold=True, color="234FA8")
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")


def _widths(ws, widths: List[int]) -> None:
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width


def _search_rows(analysis: Dict[str, Any], query_type: str) -> List[Dict[str, Any]]:
    return [x for x in analysis.get("search_terms") or []
            if str(x.get("query_type") or "") == query_type]


def build_workbook(analysis: Dict[str, Any]) -> io.BytesIO:
    wb = Workbook()
    summary = wb.active
    summary.title = "汇总"
    campaign = analysis.get("campaign") or {}
    sm = analysis.get("summary") or {}

    summary["A1"] = "广告活动分析"
    summary["A1"].font = Font(bold=True, size=16)
    summary.append(["活动名称", campaign.get("name")])
    summary.append(["campaignId", campaign.get("campaign_id")])
    summary.append(["父 ASIN", campaign.get("parent_asin")])
    summary.append(["日期范围", f"{analysis.get('date_from')} ~ {analysis.get('date_to')}"])
    summary.append(["覆盖天数", (analysis.get("coverage") or {}).get("days")])
    summary.append([])
    summary.append(["汇总指标"])
    summary.cell(row=summary.max_row, column=1).font = Font(bold=True, size=12)
    _header(summary, summary.max_row + 1, [
        "曝光", "点击", "花费", "订单", "销售额", "CTR", "CPC", "CVR", "ACOS", "ROAS"])
    summary.append([
        sm.get("impressions"), sm.get("clicks"), sm.get("cost"),
        sm.get("orders"), sm.get("sales"), sm.get("ctr"), sm.get("cpc"),
        sm.get("cvr"), sm.get("acos"), sm.get("roas"),
    ])
    summary.append([])
    summary.append(["诊断"])
    summary.cell(row=summary.max_row, column=1).font = Font(bold=True, size=12)
    _header(summary, summary.max_row + 1, ["等级", "问题", "证据"])
    for d in analysis.get("diagnostics") or []:
        ev = "; ".join(f"{k}={_zh_text(v)}" for k, v in (d.get("evidence") or {}).items())
        summary.append([d.get("severity"), _zh_text(d.get("title")), ev])
    summary.append([])
    summary.append(["策略建议"])
    summary.cell(row=summary.max_row, column=1).font = Font(bold=True, size=12)
    _header(summary, summary.max_row + 1,
            ["动作", "中文动作", "等级", "中文解释", "原因", "选择器", "参数"])
    for a in (analysis.get("strategy") or {}).get("actions") or []:
        summary.append([a.get("action"), a.get("action_label") or a.get("action"),
                        a.get("level"), _zh_text(a.get("explanation")),
                        _zh_text(a.get("reason")),
                        _zh_text(a.get("selector") or {}),
                        _zh_text(a.get("params") or {})])
    _widths(summary, [26, 26, 26, 26, 26, 10, 10, 10, 10, 10])

    for sheet_name, qtype, title in (
            ("搜索词", "keyword", "搜索词"),
            ("搜索ASIN", "asin", "搜索 ASIN")):
        ws = wb.create_sheet(sheet_name)
        headers = ["序号", title, "父 ASIN", "广告组", "匹配类型",
                   "曝光", "点击", "花费", "订单", "ACOS", "CTR", "CPC"]
        _header(ws, 1, headers)
        rows = _search_rows(analysis, qtype)
        for i, r in enumerate(rows, 1):
            ws.append([
                i, r.get("term"), r.get("parent_asin"), r.get("group_name"),
                "、".join(label_match(x) for x in (r.get("match_types") or [])),
                _metric_cell(r, "impressions"), _metric_cell(r, "clicks"),
                _metric_cell(r, "cost"), _metric_cell(r, "orders"),
                _metric_cell(r, "acos"), _metric_cell(r, "ctr"),
                _metric_cell(r, "cpc"),
            ])
        _widths(ws, [6, 34, 16, 28, 20, 20, 20, 24, 16, 20, 16, 20])
        ws.freeze_panes = "A2"

    # 竞品数据：按本活动命中的搜索词关联本地 fact_competitor
    comp = wb.create_sheet("竞品数据")
    comp_headers = ["搜索词", "排名", "ASIN", "标题", "价格", "评分",
                    "评论数", "配送", "优惠券", "库存", "广告位", "链接", "抓取时间"]
    _header(comp, 1, comp_headers)
    for c in analysis.get("competitors") or []:
        comp.append([
            c.get("term"), c.get("position"), c.get("asin"), c.get("title"),
            c.get("price"), c.get("rating"), c.get("reviews"),
            c.get("delivery"), c.get("coupon"), c.get("stock"),
            "是" if c.get("sponsored") else "否", c.get("url"), c.get("fetched_at"),
        ])
    _widths(comp, [28, 7, 15, 60, 10, 8, 9, 30, 28, 18, 8, 55, 18])
    comp.freeze_panes = "A2"

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.value is not None:
                    cell.alignment = Alignment(vertical="center", wrap_text=True)

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out
