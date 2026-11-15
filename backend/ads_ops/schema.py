# -*- coding: utf-8 -*-
"""广告写动作的入参/出参模型。

原则
-----
* LLM 只提供业务参数，不提供 URL、Cookie、内部 ID。
* 创建活动使用强类型模型，拒绝未知字段。
* 选择器中的 id 仅用于解析已有对象，执行前仍会重新读回。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductRef(_StrictModel):
    """推广商品。优先用 asin，也可用 sku；父 ASIN 可一次展开全部子体。"""

    asin: str = ""
    sku: str = ""
    parent_asin: str = ""


class KeywordSpec(_StrictModel):
    keyword_text: str
    match_type: Literal["broad", "phrase", "exact", "theme"] = "exact"
    bid: Optional[float] = None
    keyword_text_cn: str = ""


class ProductTargetSpec(_StrictModel):
    """手动商品投放行。type=asin 时 asin 必填。"""

    type: Literal["asin", "category", "brand", "keyword-group"] = "asin"
    asin: str = ""
    category: str = ""
    category_id: str = ""
    keyword_text: str = ""
    match_type: str = ""
    bid: Optional[float] = None


class NegativeKeywordSpec(_StrictModel):
    keyword_text: str
    match_type: Literal["negativeExact", "negativePhrase"] = "negativeExact"


class GroupSpec(_StrictModel):
    """多广告组创建时，每个组的独立商品、竞价和投放。"""

    name: str = Field(min_length=1, max_length=120)
    default_bid: float = Field(gt=0, le=1000)
    products: List[ProductRef] = Field(min_length=1)
    keywords: List[KeywordSpec] = []
    targetings: List[ProductTargetSpec] = []
    auto_bids: Dict[str, float] = {}
    negative_keywords: List[NegativeKeywordSpec] = []
    negative_asins: List[str] = []


class CampaignCreateParams(_StrictModel):
    """新建 SP 活动的业务参数。

    targeting_type=auto 时不能传 keywords/targetings；
    targeting_type=manual 时 keywords 与 targetings 只能二选一。
    """

    name: str = Field(min_length=1, max_length=120)
    start_date: str
    end_date: str = ""
    daily_budget: float = Field(gt=0)
    targeting_type: Literal["auto", "manual"] = "manual"
    strategy: Literal["autoForSales", "legacyForSales", "manual"] = "legacyForSales"
    campaign_site: str = "amazonAll"

    # 新建测试活动默认进入技术测试组合；传空字符串可显式表示无组合
    portfolio: str = "技术测试组合"
    placement_top: Optional[float] = None
    placement_rest: Optional[float] = None
    placement_product: Optional[float] = None
    placement_business: Optional[float] = None

    # 单组兼容字段；多组时使用 groups，下面字段可留空
    group_name: str = ""
    default_bid: float = Field(default=0, ge=0, le=1000)
    products: List[ProductRef] = Field(default=[], max_length=200)

    keywords: List[KeywordSpec] = []
    targetings: List[ProductTargetSpec] = []
    auto_bids: Dict[str, float] = {}

    negative_keywords: List[NegativeKeywordSpec] = []
    negative_asins: List[str] = []

    # 多广告组：至少 2 组；与单组字段互斥
    groups: List[GroupSpec] = Field(default=[], max_length=10)

    audience_id: str = ""
    audience_bid_percentage: str = ""
    default_audience: bool = False


class CampaignArchiveParams(_StrictModel):
    """归档无业务参数；保留模型便于统一入口。"""

    pass


class BidParams(_StrictModel):
    bid: float = Field(ge=1, le=1000)
    expected_before: Optional[float] = None


class StateParams(_StrictModel):
    state: Literal["enabled", "paused"]
    expected_before: Optional[str] = None


class GroupBidParams(_StrictModel):
    default_bid: float = Field(ge=1, le=1000)
    expected_before: Optional[float] = None


class PlacementParams(_StrictModel):
    placement: Literal["placementTop", "placementRestOfSearch",
                       "placementProductPage", "placementSiteAmazonBusiness"]
    value: int = Field(ge=0, le=900)
    expected_before: Optional[float] = None


class BudgetParams(_StrictModel):
    daily_budget: float = Field(gt=0)
    expected_before: Optional[float] = None


class NegativeKeywordParams(_StrictModel):
    keywords: List[NegativeKeywordSpec] = Field(min_length=1, max_length=100)
    ne_level: Literal["group", "campaign"] = "group"


class NegativeAsinParams(_StrictModel):
    asins: List[str] = Field(min_length=1, max_length=100)
    ne_level: Literal["group", "campaign"] = "group"


class ArchiveNegativeParams(_StrictModel):
    ne_type: Literal["keyword", "target"]
    ne_level: Literal["group", "campaign"] = "group"


class AddProductsParams(_StrictModel):
    products: List[ProductRef] = Field(min_length=1, max_length=200)


class AddGroupParams(_StrictModel):
    name: str = Field(min_length=1, max_length=120)
    default_bid: float = Field(gt=0, le=1000)
    products: List[ProductRef] = Field(min_length=1, max_length=200)
    keywords: List[KeywordSpec] = []
    targetings: List[ProductTargetSpec] = []
    auto_bids: Dict[str, float] = {}
    targeting_type: Literal["auto", "manual"] = "auto"
    negative_keywords: List[NegativeKeywordSpec] = []
    negative_asins: List[str] = []


class RenameParams(_StrictModel):
    name: str = Field(min_length=1, max_length=120)


class MovePortfolioParams(_StrictModel):
    portfolio: str = Field(min_length=1)


class PreviewRequest(BaseModel):
    action: str
    selector: Dict[str, Any] = {}
    params: Dict[str, Any] = {}


class ExecuteRequest(BaseModel):
    confirm_token: str
    action: str = ""
    selector: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, Any]] = None
