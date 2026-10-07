# -*- coding: utf-8 -*-
"""在线产品 (销售 → 在线产品) 接口客户端

接口契约来自对 https://www.sellfox.com/amzup-web-main/web/product/index.html
页面 XHR 的实测逆向 (详见 explore/online_product_contract.json):

  子体列表  POST /api/gw/sellfox/sellfox-product/sellfox/product/pageList
  父体列表  POST /api/parent/product/pageList.json          <-- 注意是**另一个网关**
        Content-Type: application/x-www-form-urlencoded
        关键参数 defaultPageType:
            1 = 返回数据行 (rows)
            2 = 只返回总数 (totalSize), rows 为空
        pageSize 上限 200

  「子体 / 父体」不是靠 body 里的 pageType 参数切换的 (实测 pageType=parents
  打到子体端点仍返回子体数据), 而是**换了一个完全不同的端点**:
      子体 -> /api/gw/sellfox/sellfox-product/sellfox/product/pageList
      父体 -> /api/parent/product/pageList.json
  两者返回的行用 isVariation 区分: 子体=2, 父体=1。

  字段  POST /api/excel/getHeadField.json          {"type":"product","multiPlatform":0}
  店铺  POST /shop/getAllShopSite.json
"""
import json
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import requests

import config
from backend.cookies import load_cookies

ORIGIN = "https://www.sellfox.com"
PAGE_URL = ORIGIN + "/amzup-web-main/web/product/index.html"
# 子体端点
LIST_API = ORIGIN + "/api/gw/sellfox/sellfox-product/sellfox/product/pageList"
# 父体端点 (与子体完全不同的网关)
PARENT_LIST_API = ORIGIN + "/api/parent/product/pageList.json"
HEAD_FIELD_API = ORIGIN + "/api/excel/getHeadField.json"
SHOP_API = ORIGIN + "/shop/getAllShopSite.json"

PAGE_SIZE_MAX = 200
MAX_PAGES = 300

# page_type -> 端点 / 载荷模板 (页面「子体/父体」单选的真正含义)
PAGE_TYPES = ("child", "parents")


def _headers(ctype: str = "application/x-www-form-urlencoded") -> Dict[str, str]:
    return {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": ctype,
        "Referer": PAGE_URL,
        "Origin": ORIGIN,
    }


def make_session(cookies: Optional[Dict[str, str]] = None) -> requests.Session:
    s = requests.Session()
    for k, v in (cookies or load_cookies() or {}).items():
        s.cookies.set(k, v, domain="www.sellfox.com", path="/")
    return s


# 子体页面默认查询条件 (已逆向) —— 作为基准请求体
def base_payload(**over: Any) -> Dict[str, str]:
    p = {
        "currency": "", "pageType": "child", "asinType": "asin", "shopIds": "",
        "marketplaceId": "", "labelIds": "", "labelQuery": "0",
        "onlineStatus": "active,inActive", "switchFulfillmentTo": "", "isVariation": "",
        "match": "", "lowCostStore": "", "searchField": "asin", "searchValue": "",
        "searchMode": "exact", "pageSize": str(PAGE_SIZE_MAX), "pageNo": "1",
        "fullCid": "", "organiser": "", "startTime": "", "endTime": "",
        "buyBoxWinners": "", "defaultFilterType": "1", "productBundle": "",
        "useAdvanced": "1", "devIds": "", "advancedSearchItem": "",
        "defaultPageType": "1", "complianceStatus": "",
    }
    for k, v in over.items():
        if v is None:
            continue
        p[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return p


# 父体页面默认查询条件 (实测抓包所得, 比子体少若干字段、多 excludeSingleProduct)
# ⚠ 实测坑: 父体端点对 startTime / endTime **有硬性要求** —— 两者任一为空
#   totalSize 直接返回 0 (不是过滤, 只是必填); 原站固定传「近7天」区间。
def base_parent_payload(**over: Any) -> Dict[str, str]:
    end = date.today()
    start = end - timedelta(days=6)
    p = {
        "currency": "", "pageType": "parents", "asinType": "asin", "shopIds": "",
        "marketplaceId": "", "labelIds": "", "labelQuery": "0",
        "onlineStatus": "active,inActive", "isVariation": "", "lowCostStore": "",
        "searchField": "asin", "searchValue": "", "searchMode": "exact",
        "pageSize": str(PAGE_SIZE_MAX), "pageNo": "1", "fullCid": "", "organiser": "",
        "startTime": start.isoformat(), "endTime": end.isoformat(),
        "defaultFilterType": "1", "useAdvanced": "1", "excludeSingleProduct": "0",
        "advancedSearchItem": "", "defaultPageType": "1", "complianceStatus": "",
    }
    for k, v in over.items():
        if v is None:
            continue
        p[k] = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    # 兜底: 时间区间不可为空, 否则父体端点恒返回 0 条
    if not p.get("startTime"):
        p["startTime"] = start.isoformat()
    if not p.get("endTime"):
        p["endTime"] = end.isoformat()
    return p


def endpoint_for(page_type: str) -> str:
    """page_type: child(子体) / parents(父体) -> 对应端点"""
    return PARENT_LIST_API if str(page_type).lower() in ("parents", "parent") else LIST_API


def payload_for(page_type: str, **over: Any) -> Dict[str, str]:
    if str(page_type).lower() in ("parents", "parent"):
        return base_parent_payload(**over)
    return base_payload(**over)


def _post(sess: requests.Session, url: str, payload: Dict[str, Any],
          json_body: bool = False, retry: int = 3) -> Dict[str, Any]:
    last = None
    for i in range(max(1, retry)):
        try:
            if json_body:
                r = sess.post(url, data=json.dumps(payload, ensure_ascii=False),
                              headers=_headers("application/json;charset=UTF-8"), timeout=config.REQUEST_TIMEOUT)
            else:
                r = sess.post(url, data=payload, headers=_headers(), timeout=config.REQUEST_TIMEOUT)
            j = r.json()
            if j.get("code") not in (0, None):
                raise RuntimeError(f"业务错误 code={j.get('code')} msg={j.get('msg')}")
            return j
        except Exception as e:      # noqa: BLE001
            last = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"请求失败 {url}: {last}")


def fetch_count(sess: requests.Session, page_type: str = "child", **filters: Any) -> int:
    """计数模式: defaultPageType=2 → totalSize"""
    p = payload_for(page_type, defaultPageType="2", pageNo="1", **filters)
    d = _post(sess, endpoint_for(page_type), p).get("data") or {}
    return int(d.get("totalSize") or 0)


def fetch_page(sess: requests.Session, page_no: int = 1, page_type: str = "child",
               **filters: Any) -> List[Dict[str, Any]]:
    """数据模式: defaultPageType=1 → rows"""
    p = payload_for(page_type, defaultPageType="1", pageNo=str(page_no), **filters)
    d = _post(sess, endpoint_for(page_type), p).get("data") or {}
    return d.get("rows") or []


def fetch_all(sess: requests.Session, page_size: int = PAGE_SIZE_MAX, page_type: str = "child",
              progress=None, **filters: Any) -> List[Dict[str, Any]]:
    """翻页拉取全部在线产品 (page_type: child=子体 / parents=父体)"""
    total = fetch_count(sess, page_type=page_type, **filters)
    out: List[Dict[str, Any]] = []
    page_no = 1
    ps = max(1, min(page_size, PAGE_SIZE_MAX))
    if progress:
        progress(f"  接口报告 totalSize={total}")
    while page_no <= MAX_PAGES:
        rows = fetch_page(sess, page_no=page_no, page_type=page_type,
                          pageSize=str(ps), **filters)
        out.extend(rows)
        if progress:
            progress(f"  第 {page_no} 页: {len(rows)} 条 (累计 {len(out)})")
        if not rows or len(rows) < ps or len(out) >= total:
            break
        page_no += 1
    return out


def fetch_head_fields(sess: requests.Session) -> List[Dict[str, Any]]:
    j = _post(sess, HEAD_FIELD_API, {"type": "product", "multiPlatform": 0}, json_body=True)
    return j.get("data") or []


def fetch_shops(sess: requests.Session) -> List[Dict[str, Any]]:
    j = _post(sess, SHOP_API, {}, json_body=True)
    return j.get("data") or []
