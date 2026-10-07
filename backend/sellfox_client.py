# -*- coding: utf-8 -*-
"""赛狐广告接口客户端 (requests 直连, 依赖登录 Cookie)

接口契约来源于对页面 XHR 的实测逆向:
  - 统一前缀  POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/<...>
  - 鉴权      登录 Cookie (关键 sf_u)
  - 分页      pageNo / pageSize(<=200), 响应 data.page.totalSize/totalPage
  - 响应体    {code, msg, data:{page:{...}}}  (广告日志为 data 直挂 page)
"""
import json
import time
from typing import Any, Dict, List, Optional

import requests

import config

GW = config.GW_PREFIX


# ---------------------------------------------------------------------------
# 页签定义: key / 中文名 / 列表接口 / 汇总接口 / 唯一键 / 采集维度(scope)
# ---------------------------------------------------------------------------
TAB_DEFS: Dict[str, Dict[str, Any]] = {
    "portfolio": {
        "label": "广告组合",
        "list": "multiple/portfolio/getAllPortfolioData",
        "aggregate": "multiple/portfolio/getAllPortfolioAggregateData",
        "keys": ["portfolioId", "id"],
        "scopes": [{}],
    },
    "campaign": {
        "label": "广告活动",
        "list": "campaign/getAllCampaignData",
        "aggregate": "campaign/getAllCampaignAggregateData",
        "keys": ["campaignId", "id"],
        "scopes": [{}],                       # 通过 type 维度覆盖全部广告类型
    },
    "group": {
        "label": "广告组",
        "list": "multiple/group/getAllGroupData",
        "aggregate": "multiple/group/getAllGroupAggregateData",
        "keys": ["adGroupId", "id"],
        "scopes": [{"adType": t} for t in config.AD_TYPES],
    },
    "product": {
        "label": "广告产品",
        "list": "multiple/adProduct/getAdProductList",
        "aggregate": "multiple/adProduct/getAllProductAggregateData",
        "keys": ["id", "adId"],
        "scopes": [{"type": t} for t in config.AD_TYPES],
    },
    "target": {
        "label": "投放",
        "list": "multiple/target/getAllTargetData",
        "aggregate": "multiple/target/getAllTargetAggregateData",
        "keys": ["targetId", "id"],
        "scopes": [{"adType": t} for t in config.AD_TYPES],
    },
    "search": {
        "label": "搜索词",
        "list": "multiple/search/getAllSearchData",
        "aggregate": "multiple/search/getAllSearchAggregateData",
        "keys": ["queryId", "id"],
        # 搜索词页签有 2 个维度: searchKeyword=搜索词 / searchAsin=ASIN, 两者数据独立, 均需采集
        "scopes": [{"adType": t, "searchQueryType": sqt}
                   for t in config.AD_TYPES
                   for sqt in ("searchKeyword", "searchAsin")],
    },
    "netarget": {
        "label": "否定投放",
        "list": "multiple/neTarget/getAllNeTargetData",
        "aggregate": None,
        "keys": ["id"],
        "scopes": [{"adType": "sp", "neType": nt} for nt in ("keyword", "product")],
    },
    "placement": {
        "label": "广告位",
        "list": "multiple/placement/getAllPlacementData",
        "aggregate": "multiple/placement/getAllPlacementAggregateData",
        "keys": ["placementId", "id"],
        "scopes": [{"adType": t} for t in config.AD_TYPES],
    },
    "log": {
        "label": "广告日志",
        "list": "log/sellfoxAndAuto/getPage",
        "aggregate": None,
        "keys": ["id"],
        "scopes": [{"types": [t]} for t in config.AD_TYPES],
    },
}

TAB_ORDER = list(TAB_DEFS.keys())


# scope 维度取值的短名映射 (让落库/展示可读且可区分)
_SCOPE_VALUE_ALIAS = {
    "searchKeyword": "keyword",     # 搜索词维度
    "searchAsin": "asin",           # ASIN 维度
}


def scope_str(scope: Optional[Dict[str, Any]]) -> str:
    """把 scope 维度字典归一为「可区分、可过滤」的字符串。

    例:
        {"adType": "sp"}                                          -> "sp"
        {"adType": "sp", "neType": "keyword"}                     -> "sp:keyword"  (否定关键词)
        {"adType": "sp", "neType": "product"}                     -> "sp:product"  (否定商品)
        {"adType": "sp", "searchQueryType": "searchKeyword"}      -> "sp:keyword"  (搜索词)
        {"adType": "sp", "searchQueryType": "searchAsin"}         -> "sp:asin"     (ASIN)
        {"types": ["sp"]}                                         -> "sp"
        {}                                                        -> ""

    注意: 旧实现对 neType 等次维度直接丢弃, 导致「否定关键词/否定商品」塌缩成同一个
    scope 值而无法区分, 这里改为按出现顺序用 ":" 连接全部维度。
    """
    if not scope:
        return ""
    parts: List[str] = []
    for k in ("adType", "type", "neType", "neLevel", "searchQueryType"):
        v = scope.get(k)
        if v:
            parts.append(_SCOPE_VALUE_ALIAS.get(str(v), str(v)))
    if not parts and scope.get("types"):
        parts.append(str(scope["types"][0]))
    if parts:
        return ":".join(parts)
    return json.dumps(scope, ensure_ascii=False, sort_keys=True)

# 店铺/广告组合
SHOP_PORTFOLIO_API = "commonMultiShop/getPortfolioListProductRight"


# ---------------------------------------------------------------------------
# 各页签基准请求体 (已剔除 shopIdList/startDate/endDate/pageNo/pageSize/pageSign,
# 并在采集时按「全状态」清空 status/servingStatus)
# ---------------------------------------------------------------------------
def _base_payload(tab: str) -> Dict[str, Any]:
    if tab == "portfolio":
        return {
            "marketplaceIdList": [], "portfolioId": "", "servingStatusList": [],
            "isHidden": False, "isCompare": False, "searchField": "name",
            "searchValue": "", "searchType": "blur", "orderField": "adCost",
            "orderType": "desc", "useAdvanced": False,
        }
    if tab == "campaign":
        return {
            "marketplaceIdList": [], "portfolioId": "", "type": "", "strategyType": "",
            "budgetState": "", "adStrategyTypeList": [], "devIds": [],
            "audienceStatusList": [], "costControlEnabled": "", "searchValue": "",
            "isCompare": False, "adTagIds": "", "searchField": "name",
            "searchType": "exact", "productType": "asin", "productSearchMode": "exact",
            "productValue": "", "campaignSites": [], "filterTargetType": "",
            "status": "", "servingStatus": "",              # 全状态
            "orderField": "startDate", "orderType": "desc",
            "useAdvanced": False, "advanceFilter": {},
        }
    if tab == "group":
        return {
            "marketplaceIdList": [], "portfolioIdList": [], "campaignStateList": [],
            "campaignIdList": [], "adStrategyTypeList": [], "adTagIdList": [],
            "devIds": [], "isCompare": False, "searchField": "name", "searchValue": "",
            "searchType": "exact", "productSearchType": "asin", "productSearchContents": [],
            "productSearchMode": "exact", "statusList": [], "servingStatusList": [],
            "orderField": "adCost", "orderType": "desc", "useAdvanced": False,
        }
    if tab == "product":
        return {
            "campaignIdList": [], "groupIdList": [], "statusList": [], "servingStatusList": [],
            "adStrategyTypeList": [], "adTagIdList": [], "devIds": [], "searchType": "asin",
            "campaignStateList": [], "adGroupStateList": [], "portfolioIdList": [],
            "isCompare": False, "compareStartDate": "", "compareEndDate": "",
            "searchMode": "exact", "searchContents": [], "searchValue": "",
            "marketplaceIdList": [], "orderBy": "adCost", "desc": True,
            "useAdvanced": False, "advanceFilter": {},
        }
    if tab == "target":
        return {
            "searchValue": "", "searchField": "name", "isSearchContentExclude": False,
            "campaignIdList": [], "groupIdList": [], "targetType": "keyword",
            "statusList": [],                                # 全状态
            "matchTypes": [], "productTargetTypes": [], "servingStatusList": [],
            "adStrategyTypeList": [], "searchType": "exact", "devIds": [],
            "orderField": "adCost", "orderType": "desc", "marketplaceIdList": [],
            "portfolioIdList": [], "useAdvanced": False, "adTagIdList": [],
            "isCompare": False, "productSearchType": "asin", "productSearchMode": "exact",
        }
    if tab == "search":
        return {
            "searchQueryType": "searchKeyword", "statusList": [], "matchTypeList": [],
            "campaignIdList": [], "groupIdList": [], "portfolioIdList": [],
            "marketplaceIdList": [], "queryWordTagTypes": [], "adStrategyTypeList": [],
            "servingStatusList": [], "devIds": [], "isCompare": False,
            "compareStartDate": "", "compareEndDate": "", "wordRoot": "",
            "searchType": "query", "searchMode": "exact", "searchContents": [],
            "isSearchContentExclude": False, "productSearchType": "asin",
            "productSearchContents": [], "productSearchMode": "exact",
            "adGroupStateList": [], "useAdvanced": False, "orderBy": "adCost", "desc": True,
        }
    if tab == "netarget":
        return {
            "neLevels": [], "matchType": "", "state": "", "neBeforeAfterDay": 30,
            "dataFrom": 2, "statusList": [], "servingStatusList": [], "portfolioIdList": [],
            "campaignIdList": [], "groupIdList": [], "searchValue": "", "searchField": "name",
            "productSearchType": "asin", "productSearchContents": [], "productSearchMode": "exact",
            "onlyShowImpressions": False, "isSearchContentExclude": False, "advanceFilter": {},
            "orderField": "adCost", "orderType": "desc", "useAdvanced": False,
            "marketplaceIdList": [],
        }
    if tab == "placement":
        return {
            "orderType": "desc", "portfolioIdList": [], "useAdvanced": False,
            "marketplaceIdList": [],
        }
    if tab == "log":
        return {
            "queryPage": "1", "portfolioIds": [], "campaignIds": [], "groupIds": [],
            "targets": [], "operationContentType": "targetDetail", "operationContent": "",
            "operationContentSearchMode": "exact", "actions": [], "isSuccess": 0,
            "userIds": [], "logTypes": [], "templateIds": [], "searchMode": "exact",
            "searchType": "asin", "searchContents": [], "marketplaceIdList": [],
        }
    raise KeyError(tab)


def build_payload(tab: str, shop_ids: List[int], start: str, end: str,
                  scope: Optional[Dict[str, Any]] = None, page_no: int = 1,
                  page_size: int = config.PAGE_SIZE) -> Dict[str, Any]:
    """组装请求体: 基准 + 店铺 + 时间范围 + 维度(scope) + 分页"""
    p = _base_payload(tab)
    p["shopIdList"] = list(shop_ids)
    p["startDate"], p["endDate"] = start, end
    p["pageNo"], p["pageSize"] = page_no, page_size
    if scope:
        p.update(scope)
    if tab == "log":
        p["queryPage"] = str(page_no)
    return p


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class SellfoxClient:
    def __init__(self, cookies: Dict[str, str], verbose_logger=None):
        self.cookies = dict(cookies or {})
        self._log = verbose_logger or (lambda *_: None)
        self.session = requests.Session()
        for k, v in self.cookies.items():
            self.session.cookies.set(k, v, domain="www.sellfox.com", path="/")

    # ---- 通用请求 ----
    def _post(self, api: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = config.ORIGIN + GW + api
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "Origin": config.ORIGIN,
            "Referer": config.REFERER,
            "User-Agent": config.USER_AGENT,
        }
        last_err = None
        for attempt in range(1, config.REQUEST_RETRY + 1):
            try:
                r = self.session.post(url, json=payload, headers=headers,
                                      timeout=config.REQUEST_TIMEOUT)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(1.2 * attempt)
                    continue
                j = r.json()
                if j.get("code") != 0:
                    raise RuntimeError(f"业务错误 code={j.get('code')} msg={j.get('msg')}")
                return j
            except Exception as e:      # noqa: BLE001
                last_err = str(e)
                time.sleep(1.2 * attempt)
        raise RuntimeError(f"接口请求失败 {api}: {last_err}")

    # ---- 店铺 + 广告组合 ----
    def get_shops(self, shop_ids: Optional[List[int]] = None) -> Dict[str, Any]:
        """返回 {rows:[{shopId,shopName,siteName,marketplaceId,portfolioId,portfolioName,...}]}"""
        payload = {
            "position": "left", "searchValue": "", "marketplaceIdList": [],
            "shopIdList": shop_ids or [], "pageNo": 1, "pageSize": 1000,
        }
        j = self._post(SHOP_PORTFOLIO_API, payload)
        return j.get("data") or {}

    # ---- 列表分页 ----
    @staticmethod
    def _extract_page(data: Any) -> Dict[str, Any]:
        """兼容 {page:{...}} 与 直挂 page 两种结构"""
        if not isinstance(data, dict):
            return {}
        if isinstance(data.get("page"), dict):
            return data["page"]
        if "rows" in data:
            return data
        return {}

    def fetch_list(self, tab: str, shop_ids: List[int], start: str, end: str,
                   scope: Optional[Dict[str, Any]] = None,
                   page_size: int = config.PAGE_SIZE) -> List[Dict[str, Any]]:
        """按页拉取某页签(某scope)的全量记录"""
        api = TAB_DEFS[tab]["list"]
        out: List[Dict[str, Any]] = []
        page_no = 1
        total = None
        while page_no <= config.MAX_PAGES:
            payload = build_payload(tab, shop_ids, start, end, scope, page_no, page_size)
            data = self._post(api, payload).get("data")
            page = self._extract_page(data)
            rows = page.get("rows") or []
            if page_no == 1:
                total = page.get("totalSize")
            out.extend(rows)
            total_page = page.get("totalPage") or 0
            if not rows or page_no >= total_page:
                break
            page_no += 1
        self._log(f"    [{tab}] scope={scope or {}} 共 {len(out)} 条 (接口报告 total={total})")
        return out

    def fetch_aggregate(self, tab: str, shop_ids: List[int], start: str, end: str,
                        scope: Optional[Dict[str, Any]] = None) -> Any:
        api = TAB_DEFS[tab].get("aggregate")
        if not api:
            return None
        payload = build_payload(tab, shop_ids, start, end, scope)
        payload.pop("pageNo", None)
        payload.pop("pageSize", None)
        return self._post(api, payload).get("data")


# ---------------------------------------------------------------------------
# 字段中文名 (用于前端表头; 未知字段回落为原始 key)
# ---------------------------------------------------------------------------
FIELD_LABELS: Dict[str, str] = {
    # 标识/维度
    "id": "ID", "campaignId": "广告活动ID", "campaignName": "广告活动",
    "adGroupId": "广告组ID", "adGroupName": "广告组", "adId": "广告产品ID",
    "asin": "ASIN", "sku": "SKU", "portfolioId": "广告组合ID", "portfolioName": "广告组合",
    "shopId": "店铺ID", "shopName": "店铺", "marketplaceId": "站点", "siteName": "站点名",
    "currency": "币种", "name": "名称", "state": "状态", "servingStatus": "服务状态",
    "servingStatusName": "服务状态", "servingStatusDec": "服务状态说明",
    "adType": "广告类型", "type": "广告类型", "campaignType": "广告活动类型",
    "campaignState": "广告活动状态", "targetState": "投放状态", "adGroupState": "广告组状态",
    "defaultBid": "默认竞价", "dailyBudget": "每日预算", "bid": "竞价", "originalBid": "源竞价",
    "startDate": "开始日期", "endDate": "结束日期", "createTime": "创建时间",
    "updateTime": "更新时间", "creator": "创建人", "devNames": "业务员", "devNamesStr": "业务员",
    "adTags": "标签", "tags": "标签", "matchType": "匹配类型",
    "keywordText": "投放", "query": "用户搜索词", "queryCn": "搜索词中文",
    "targetingExpression": "投放表达式", "targetingType": "投放类型",
    "searchFrequencyRank": "ABA搜索词排名", "weekRatio": "排名周变化率",
    "impressionRank": "实时广告排名", "impressionShare": "曝光份额",
    "topImpressionShare": "首页首位IS", "maxTopIs": "搜索结果首页首位IS",
    "suggestedBid": "建议竞价", "suggestedBidRange": "建议竞价/范围",
    "queryType": "活动类型", "targetSource": "投放来源", "domain": "域名",
    "placementId": "广告位ID", "isHidden": "隐藏", "action": "操作类型",
    "operationContents": "操作内容", "logType": "日志类型", "operatorName": "操作人",
    # 指标
    "impressions": "广告曝光量", "clicks": "广告点击量", "ctr": "广告点击率",
    "cvr": "广告转化率", "adCost": "广告花费", "cpm": "CPM", "vcpm": "vCPM",
    "adCostPerClick": "CPC", "cpc": "CPC", "adCostPercentage": "广告花费占比",
    "adOrderNum": "广告订单量", "adSaleNum": "广告销量", "adSales": "广告销售额",
    "adSale": "广告销售额", "orderNum": "订单量", "sales": "销售额",
    "cpa": "CPA", "acos": "ACoS", "roas": "ROAS", "acots": "ACoTS", "asots": "ASoTS",
    "acoas": "ACoAS", "asoas": "ASoAS",
    "adOrderNumPercentage": "广告订单量占比", "adSalePercentage": "广告销售额占比",
    "adSalesPercentage": "广告销售额占比", "clickPercentage": "点击占比",
    "impressionsPercentage": "曝光占比", "orderNumPercentage": "订单量占比",
    "adSelfSaleNum": "本广告产品销量", "adOtherSaleNum": "其他产品广告销量",
    "adSelfOrderNum": "本广告产品订单量", "adOtherOrderNum": "其他产品广告订单量",
    "adSelfSales": "本广告产品销售额", "adOtherSales": "其他产品广告销售额",
    "selfAdvertisingUnitPrice": "本广告产品客单价", "otherAdvertisingUnitPrice": "其他产品客单价",
    "advertisingUnitPrice": "广告客单价",
    "budgetUsage": "预算使用率", "overBudgetCount": "超预算次数",
    "dailyActiveDuration": "日活跃时长", "viewImpressions": "浏览曝光量",
    "addToCart": "加购量", "addToCartRate": "加购率", "brandedSearches": "品牌搜索量",
    "detailPageViews": "详情页浏览量", "cumulativeReach": "累计触达",
    "impressionsFrequencyAverage": "平均曝光频次", "newToBrandDetailPageViews": "新客详情页浏览",
    "video5SecondViews": "视频5秒观看", "video5SecondViewRate": "视频5秒观看率",
    "videoCompleteViews": "视频完整观看", "videoUnmutes": "视频取消静音",
    "viewabilityRate": "可见率", "viewClickThroughRate": "可见点击率",
    "ordersNewToBrandFTD": "新客订单量", "salesNewToBrandFTD": "新客销售额",
    "placementProductPage": "商品页面", "placementTop": "搜索结果顶部",
    "placementRestOfSearch": "搜索结果其余", "placementSiteAmazonBusiness": "亚马逊企业购",
    "budgetState": "预算状态", "costType": "成本控制",
}


def label_of(field: str) -> str:
    return FIELD_LABELS.get(field, field)
