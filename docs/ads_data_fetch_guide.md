# 广告数据分析取数指南（广告表现 / 流量结构 / 商品与竞品）

> 本文只讲**怎么把数据取下来**：接口地址、请求体、返回字段、参数坑、落盘规范。
> 接口契约的完整逆向记录见 `docs/sellfox_sp_create_api.md`；本文是可执行的操作手册。
>
> 覆盖范围：
> 1. 广告表现（活动 / 广告组 / 投放 / 广告产品 / 广告位 / 分时 / 建议竞价 / 日志 / 否定）
> 2. 流量结构（搜索词 / 搜索 ASIN / ABA 搜索词排名 / 建议关键词）
> 3. 商品与竞品（我方售价 / 竞品搜索结果抓取 / 竞品指标计算口径）

---

## 0. 前置：鉴权与通用封装

### 0.1 拿 Cookie（两种方式）

**方式 A：直接导出已登录浏览器的 Cookie（推荐，最省事）**

```python
from playwright.sync_api import sync_playwright
import json

with sync_playwright() as pw:
    b = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
    ctx = b.contexts[0]
    cookies = {c["name"]: c["value"] for c in ctx.cookies()
               if "sellfox" in (c.get("domain") or "")}
    json.dump(cookies, open("/tmp/sf_cookies.json", "w"))
```

前提：Chrome 带 `--remote-debugging-port=9222` 启动，且已登录赛狐。

**方式 B：CDP 浏览器级 `Storage.getCookies`（不依赖任何页面）**

```
GET http://127.0.0.1:9222/json/version → webSocketDebuggerUrl
WS  {"id":1,"method":"Storage.getCookies"}
→ 过滤 domain 含 sellfox.com 的 name/value
```

> Cookie 属于登录态，**不要提交 git**，放 `/tmp` 或 `.gitignore` 的目录。

### 0.2 通用请求封装（本文所有接口都用它）

```python
import json, urllib.request

GW     = "https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox"
ORIGIN = "https://www.sellfox.com"
COOKIE = "; ".join(f"{k}={v}" for k, v in json.load(open("/tmp/sf_cookies.json")).items())

HDR = {
    "Content-Type": "application/json",
    "Cookie": COOKIE,
    "Origin": ORIGIN,
    "Referer": f"{ORIGIN}/cpc-vue3/web/cpc-vue3/ad-create/index.html",
    "X-Language": "zh-CN",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
}

def post(path, body, base=GW, timeout=60):
    """POST JSON 并自动把 data 里的字符串反序列化"""
    url = path if path.startswith("http") else base + path
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for k, v in HDR.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode())
    if isinstance(d.get("data"), str):
        d["data"] = json.loads(d["data"])
    return d

def rows(d, *keys):
    """从 {data:{page:{rows:[]}}} 里取行；也兼容 data 直挂 page 的结构"""
    data = d.get("data") or {}
    page = data.get("page") if isinstance(data.get("page"), dict) else data
    for k in ("rows",) + keys:
        if isinstance(page.get(k), list):
            return page[k]
    return []
```

### 0.3 三个通用坑（先看，能省很多时间）

| 坑 | 表现 | 对策 |
|---|---|---|
| 过滤参数不生效 | `portfolioIdList` / `statusList` / `adType` 传了没用，返回全量 | 拉全量后**按行字段二次过滤** |
| 部分失败 | `code:0` 但 `targetList[].code=-1`、`errMsgList` 有错 | 逐条检查，别只看 code |
| 双 id 混用 | 内部 id（`id`）vs Amazon id（`campaignId`/`adGroupId`） | 读列表拿 Amazon id 查询，写操作多用内部 id |

---

## 1. 广告表现

### 1.1 接口总表

| 数据 | 接口 | 关键参数 | 返回 |
|---|---|---|---|
| 活动列表（含全部指标） | `POST campaign/getAllCampaignData` | `startDate`/`endDate`（日级=同一天） | `data.page.rows` |
| 活动配置 | `POST adCampaign/getInfo` | `campaignId`(Amazon) | 扁平对象 |
| 广告组 | `POST multiple/group/getAllGroupData` | `campaignIdList` | `data.page.rows` |
| 投放 | `POST multiple/target/getAllTargetData` | `targetType` | `data.page.rows` |
| 广告产品 | `POST multiple/adProduct/getAdProductList` | `campaignIdList` | `data.page.rows` |
| 广告位 | `POST multiple/placement/getAllPlacementData` | `campaignIdList` | `data.page.rows`（4 行） |
| 分时（24h） | `POST multiple/target/getAllTargetHourData` | `dateModel` | `hour[]` / `periodReportList` |
| 建议竞价 | `POST spTarget/getSuggestedBid` | `targetIds`（逗号串） | 数组 |
| 操作日志 | `POST log/sellfoxAndAuto/getPage` | `types:["sp"]` | `data.rows`（直挂） |
| 否定项 | `POST multiple/neTarget/getAllNeTargetData` | `neType` | `data.page.rows` |

### 1.2 活动列表（日级指标的主力接口）

```python
d = post("campaign/getAllCampaignData", {
    "shopIdList": ["711811"], "adType": "sp",
    "statusList": ["enabled", "paused"],
    "startDate": "2026-11-01", "endDate": "2026-11-07",
    "orderField": "adCost", "orderType": "desc",
    "pageNo": 1, "pageSize": 200, "fromSingle": True,
    # 需要搜名字时加：
    "searchType": "contains", "searchField": "name", "searchValue": "B0HLKZD3MZ",
})
```

- **日级取数**：`startDate = endDate = 当天` → 当天指标；给区间 → 区间累计
- **逐日曲线**：按天循环 7 次
- 行内常用字段：`campaignId` / `id` / `name` / `state` / `servingStatusName` /
  `campaignTargetingType` / `dailyBudget` / `strategy` / `portfolioName`
  + 全量指标（`impressions` / `clicks` / `ctr` / `cvr` / `adCost` / `adCostPerClick` /
  `adOrderNum` / `adSale` / `acos` / `roas` / `cpa` / `addToCart` / `detailPageViews` …）
- 坑：`statusList` / `portfolioIdList` / `adType` 均**不严格生效**

### 1.3 广告组 / 投放 / 广告产品 / 广告位

```python
# 广告组
post("multiple/group/getAllGroupData", {
    "shopIdList": ["711811"], "campaignIdList": [CID],
    "adType": "sp", "startDate": S, "endDate": E})

# 投放（targetType 三选一：auto / keyword / targeting）
post("multiple/target/getAllTargetData", {
    "orderField": "adCost", "orderType": "desc", "shopIdList": ["711811"],
    "campaignIdList": [CID], "adType": "sp", "targetType": "auto",
    "matchTypes": [], "statusList": ["enabled", "paused"],
    "startDate": S, "endDate": E, "pageNo": 1, "pageSize": 100,
    "fromSingle": True, "useAdvanced": False})

# 广告产品（注意 type 而不是 adType）
post("multiple/adProduct/getAdProductList", {
    "type": "sp", "campaignIdList": [CID], "shopIdList": ["711811"],
    "statusList": ["enabled", "paused"], "startDate": S, "endDate": E,
    "pageNo": 1, "pageSize": 100, "orderBy": "adCost", "desc": True,
    "searchContents": [], "searchMode": "exact", "searchType": "asin",
    "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": False})

# 广告位（返回 4 行，用 predicate 区分）
post("multiple/placement/getAllPlacementData", {
    "dxmCampaignId": "", "campaignSite": "", "orderField": "adCost",
    "orderType": "desc", "adType": "sp", "campaignIdList": [CID],
    "devIds": [], "strategyType": "", "predicate": "",
    "startDate": S, "endDate": E, "isCompare": False,
    "compareStartDate": "", "compareEndDate": "",
    "adStrategyTypeList": [], "pageNo": 1, "pageSize": 20, "fromSingle": True})
```

`targetType` 枚举实测只有 `auto` / `keyword` / `targeting`，其他值报「请求参数错误」。

广告位 `predicate` 取值：`placementTop` / `placementRestOfSearch` /
`placementProductPage` / `offAmazon`（站外 `isUpdateBid=false`，不可调）。

### 1.4 分时（24 小时）

```python
DAY = "2026-11-06"
post("multiple/target/getAllTargetHourData", {
    "orderField": None, "orderType": None,
    "shopIdList": [711811], "shopId": "711811",
    "adType": "sp", "type": "sp", "targetType": "auto",
    "matchTypes": [], "productTargetTypes": [],
    "campaignIdList": [CID],
    "groupIdList": [],        # 传了 = 组级；再传 targetId = 投放级
    "targetId": "",
    "servingStatusList": [], "statusList": ["enabled", "paused"],
    "adStrategyTypeList": [], "devIds": [],
    "startDate": DAY, "endDate": DAY,
    "analyzeStartDate": DAY, "analyzeEndDate": DAY,
    "dateModel": "HOURLY",
    "weekList": ["1","2","3","4","5","6","7"],
    "isCompare": 0, "isHide": True, "fromSingle": True,
    "searchType": "exact", "searchField": "name", "searchValue": None,
    "wordRoot": "", "useAdvanced": False,
    "pageNo": 1, "pageSize": 200, "advanceFilter": {}})
```

**`dateModel` 枚举（实测有效 5 个）**

| 值 | 维度 | 返回形态 |
|---|---|---|
| `HOURLY` | 活动 / 组 / 投放（由出入参决定） | `hour[]`（每个指标一条，records 为 24 值） |
| `HOURLY_ASIN` | 广告产品 | `periodReportList` 每产品一行 + `detail[24]` |
| `HOURLY_KEYWORD` | 投放/匹配 | 每投放一行 + `detail[24]` |
| `HOURLY_PLACEMENT` | 广告位 | 每广告位一行 + `detail[24]` |
| `DAILY` | 当天汇总 | `hour` 只有 1 条 |

另外三个（`HOURLY_TARGET` / `HOURLY_SEARCH_TERM` / `HOURLY_CAMPAIGN`）报 `160000 系统异常`。

### 1.5 建议竞价

```python
# 投放维度（自动匹配位 / ASIN 定向 / 品类定向）
post("spTarget/getSuggestedBid", {"shopId": "711811",
                                  "targetIds": "142618529430288,194035837860233"})

# 关键词维度
post("spKeyword/getSuggestedBid", {"shopId": "711811",
                                   "keywordIds": "90429492515259"})
```

返回：`suggested` + `rangeStart` / `rangeEnd`，自动位另有
`estimatedImpressionLower/Upper`；`extension[0].type` 可反查投放位类型
（`queryHighRelMatches` / `queryBroadRelMatches` / `asinSubstituteRelated` /
`asinAccessoryRelated`）。

**两个坑**：`targetIds` 必须是 **逗号字符串**（传数组报 160000）；必须是
**Amazon targetId**（传内部 id 报「对象不存在」）。

### 1.6 操作日志 / 否定项

```python
# 日志（注意 data 直挂分页对象，不是 data.page）
post("log/sellfoxAndAuto/getPage", {
    "types": ["sp"], "shopIdList": ["711811"],
    "startDate": "2026-10-25", "endDate": "2026-11-07",
    "pageNo": 1, "pageSize": 200})

# 否定项（neType 只有 keyword / target）
post("multiple/neTarget/getAllNeTargetData", {
    "orderField": "adCost", "orderType": "desc", "shopIdList": ["711811"],
    "campaignIdList": [CID], "adType": "sp", "neType": "target",
    "neLevels": ["group"], "matchType": "", "servingStatusList": [],
    "startDate": None, "endDate": None, "neBeforeAfterDay": 30, "dataFrom": 2,
    "searchType": "exact", "searchField": "name", "searchValue": "",
    "devIds": [], "useAdvanced": False, "pageNo": 1, "pageSize": 50,
    "onlyShowImpressions": False})
```

日志行关键字段：`action`（`editBudget` / `editBid` / `editState` / `editPortfolio` /
`add` / `editName` / `editBidStrategy` / `editDate`）、`logType`
（`sellfox` / `scheduler`）、`operationTime`（服务器，UTC+8）、
`siteOperationTime`（站点时区，JP 为 UTC+9，比服务器晚 1 小时）。

> 日志只记录**赛狐侧**操作；在 Amazon 后台改预算查不到。

---

## 2. 流量结构

### 2.1 搜索词 / 搜索 ASIN（同一接口两个维度）

```python
def search_terms(qtype, cid, S, E):
    return rows(post("multiple/search/getAllSearchData", {
        "orderField": "impressions", "orderType": "desc",
        "shopIdList": ["711811"], "campaignIdList": [cid], "adType": "sp",
        "searchQueryType": qtype,           # searchKeyword | searchAsin
        "statusList": ["enabled", "paused"], "servingStatusList": [],
        "startDate": S, "endDate": E,
        "pageNo": 1, "pageSize": 500, "fromSingle": True, "useAdvanced": False}))

kw_rows  = search_terms("searchKeyword", CID, S, E)   # 顾客搜的词
asin_rows = search_terms("searchAsin",    CID, S, E)  # 广告出现的商品页
```

| 维度 | 标识字段 | 关键返回字段 |
|---|---|---|
| 搜索词 | `query`（= 搜索词） | `matchType`（`close_match`/`loose_match`/`complements`）、`clicks`、`adCost`、`suggested`、`originalBid`、`addToCart`、`detailPageViews` |
| 搜索 ASIN | `query`（= ASIN） | 额外有 `price`（竞品价）、`asinRating`、`ratingCount` |

两维度数据**互补**：词维度看意图，ASIN 维度看竞争页。

### 2.2 ABA 搜索词排名（含历史趋势）⭐

```python
d = post("https://www.sellfox.com/api/search/terms/analysis/getTrend.json", {
    "marketplaceId": "A1VC38T7YXB528",
    "searchTerms": "チェストリグ######ラジオハーネス",   # 6 个 # 连接
    "periodType": "WEEK",                            # DAY/WEEK/MONTH/QUARTER
    "startDate": "", "endDate": "",                  # 必须空串
    "departmentNames": "Amazon.co.jp######Amazon.co.jp",  # 与词数一一对应
})
```

返回

```json
{"code":0,"data":[{
  "searchTerm":"チェストリグ","abaRank":"11000","rank":null,
  "list":[{"startDate":"2026-08-02","endDate":"2026-08-08",
           "searchFrequencyRank":15007,"weekStr":"26年第32周"}]}]}
```

- `abaRank` = 最新一期排名，**越小越热**
- `list` = 历史（周 13 期 / 月 12 期）
- 无 ABA 记录的长尾词**直接不返回**（不报错）

**三个必踩的坑**：`######` 分隔符、`departmentNames` 必填且数量对齐、
日期传空串。另外 **不要用** `keywordLib/searchTermGetTrend`（新网关路径恒返回空）。

> 排名 ≠ 搜索量。要绝对搜索量需 ABA 后台导出或第三方工具，赛狐无此字段。

### 2.3 建议关键词（按 ASIN）

```python
post("adKeyword/sp/suggestKeywordsByAsin/page", {
    "shopId": "711811",
    "asinList": ["B0HQGMTCB3", "B0HQRVCQ88"],   # 字段名是 asinList
    "pageNo": 1, "pageSize": 20})
```

返回 `data.keywordTypeList[{matchType, keywordText, keywordTextCn}]`，
`data.totalSize` 实测 200。**不含排名**，要排名需再过 2.2 补。

### 2.4 推荐建议竞价（按 ASIN 批量）

```python
post("multiple/target/suggestBid/recommendations", {
    "shopId": "711811",
    "asins": ["B0HQGMTCB3"],
    "placementProductPage": None, "placementTop": None, "placementRestOfSearch": None,
    "biddingStrategy": "AUTO_FOR_SALES",
    "targetingExpressions": [
        {"type": "KEYWORD_GROUP", "value": "keyword-group=\"category\""},
        {"type": "KEYWORD_GROUP", "value": "keyword-group=\"peerbrand\""},
        {"type": "KEYWORD_GROUP", "value": "keyword-group=\"gift\""},
        {"type": "KEYWORD_GROUP", "value": "keyword-group=\"brand\""}]})
```

---

## 3. 商品与竞品

### 3.1 我方售价与 listing 信息（form 编码，容易踩错）

```python
import urllib.parse

URL = "https://www.sellfox.com/api/gw/sellfox/sellfox-product/sellfox/product/pageList"

BASE = {
    "currency": "", "pageType": "child", "asinType": "asin", "shopIds": "711811",
    "marketplaceId": "", "labelIds": "", "labelQuery": "0",
    "onlineStatus": "active,inActive", "switchFulfillmentTo": "", "isVariation": "",
    "match": "", "lowCostStore": "", "searchField": "asin", "searchValue": "",
    "searchMode": "exact", "pageSize": "200", "pageNo": "1", "fullCid": "",
    "organiser": "", "startTime": "", "endTime": "", "buyBoxWinners": "",
    "defaultFilterType": "1", "productBundle": "", "useAdvanced": "1", "devIds": "",
    "advancedSearchItem": "", "defaultPageType": "1", "complianceStatus": "",
}

def one_product(asin):
    body = dict(BASE, searchValue=asin)
    req = urllib.request.Request(URL, data=urllib.parse.urlencode(body).encode(), method="POST")
    for k, v in {**HDR, "Content-Type": "application/x-www-form-urlencoded",
                 "Referer": "https://www.sellfox.com/amzup-web-main/web/product/index.html"}.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode())
    data = d.get("data") or {}
    rs = (data.get("page") or {}).get("rows") or []
    return next((x for x in rs if x.get("asin") == asin), None)
```

要点：**必须 form-urlencoded**、必须带完整 `BASE`、`defaultPageType=1`
（=2 只返回 totalSize 不带行）、`searchMode="exact"`。

返回关键字段：`asin` / `sku` / `parentAsin` / `isVariation` /
`standardPrice`（标准价）/ `landedPrice`（到手价）/ `title` / `dxmState`。

### 3.2 竞品搜索结果（Playwright 抓取）

```bash
python3 amazon_search_scrape.py --query "チェストリグ" --headless --scrolls 1
```

- 抓 `https://www.amazon.co.jp/s?k=<词>&language=ja_JP` 的第一页
- 输出：`explore/amazon_search_<词>.json`
- 字段：`asin` / `title` / `price_value` / `list_price` / `rating_value` /
  `review_count` / `delivery_date` / `free_shipping` / `badges` /
  `bought_recently` / `sponsored` / `raw_text`
- 缓存：本地文件永久 + 库 TTL 1800s；`--refresh` 强制重抓

**安全边界**（避免反爬）

| 约束 | 值 |
|---|---|
| 两次真实抓取最小间隔 | 6s（+0~3s 随机抖动） |
| 并发 | 禁止（全局锁） |
| 单次翻页 | 1~2 屏（`--scrolls 1`） |
| 每日建议上限 | ≤30 个词 |
| 撞验证码 | 停 5 分钟以上（`AMAZON_PREFETCH_BLOCK_BACKOFF_SEC`） |
| 账号风险 | 用匿名 headless，**不要**用登录态 Chrome 抓亚马逊 |

### 3.3 竞品指标计算口径

拿到「我方售价 + 竞品 12 条」后按下面口径算：

| 指标 | 公式 | 判断阈值 |
|---|---|---|
| 价格分位 | 比我方便宜的竞品数 ÷ 样本数 | >85% = 价格劣势明显 |
| 溢价率 | 我方价 ÷ 竞品中位价 − 1 | >50% = 转化几乎不可能 |
| 标题相似度 | 关键 token 命中数 ÷ token 总数 | <25% 误匹配；≥40% 同类 |
| 时效差 | 我方到货天数 − 竞品最快到货天数 | 越大越吃亏 |
| 竞争强度 | 前 12 名中位评论数 + 是否有 500+ 评论头部 | 决定新品能否挤进去 |

```python
import statistics as st

def compete(our_price, items):
    prices = [i["price_value"] for i in items if i.get("price_value")]
    return {
        "median": st.median(prices),
        "our_gap_pct": (our_price / st.median(prices) - 1) * 100,
        "our_percentile": sum(1 for p in prices if p < our_price) / len(prices) * 100,
        "min": min(prices), "max": max(prices),
    }
```

### 3.4 ABA 排名分档（替代“月搜索量”）

拿不到绝对搜索量，用排名分档做相对判断（日站经验值）：

| ABA 排名 | 档位 | 投放策略 |
|---|---|---|
| < 10,000 | 极热 | 大词，严控 CPC |
| 10,000 – 50,000 | 中热 | 值得投，建议价上沿出价 |
| 50,000 – 200,000 | 长尾 | 低竞价养 |
| > 200,000 | 几乎无流量 | 直接否定 |

---

## 4. 落盘规范

```
explore/analysis/<日期>_<campaignId 或 活动名>/
├── campaign.json          活动汇总 + 逐日
├── layers.json            组 / 投放 / 产品 / 广告位
├── targets.json           投放位类型反查 + 建议价
├── hourly.json            分时
├── search_keyword.json    搜索词维度
├── search_asin.json       搜索 ASIN 维度
├── aba_week.json          ABA 周排名
├── aba_month.json         ABA 月排名
├── own_price.json         我方售价
└── competitors/           amazon_search_<词>.json 的副本
```

- 命名统一：`<粒度>_<日期区间>.json`，方便回溯
- `/tmp` 只放中间过程，**结论数据必须落到 `explore/`**
- Cookie 文件单独放，禁止入库

---

## 5. 取数频次建议

| 数据 | 频次 | 说明 |
|---|---|---|
| 活动/组/投放/产品/广告位指标 | 每天 1 次 | 取前一天数据 |
| 分时 | 每天 1 次 | 只取前一天（`dateModel=HOURLY`） |
| 搜索词 / 搜索 ASIN | 每天 1 次 | 数据量随曝光增长 |
| ABA 排名 | 每周 1 次 | 数据按周更新，日抓没意义 |
| 竞品抓取 | 每 3 天 1 次 | 受反爬约束，30 分钟缓存 |
| 建议价 | 按需 | 调整出价前跑一次 |

---

## 6. 常见错误码

| code | 含义 | 典型原因 |
|---|---|---|
| `0` | 请求被受理 | **不代表全部成功**，检查行内 `code`/`errMsg` |
| `-1` | 请求参数错误 / 对象不存在 | id 类型传错（内部 id vs Amazon id） |
| `160000` | 参数校验失败 | 数组传成字符串、必填字段缺失（如 ABA 的 `departmentNames`） |
| `360000` | 套餐限制 / 校验失败 | 例：免费套餐仅 1 条策略模板 |
| `500` | 系统异常 | 老接口参数结构不对 |
| `404` | 路径不存在或未灰度 | 换新/旧两套路径试 |

---

## 7. 快速自检清单

跑完一轮取数后，逐条核对：

- [ ] 活动级 `impressions` / `clicks` / `adCost` 与后台一致
- [ ] 组级求和 = 活动级（差异应在个位数）
- [ ] 投放 4 个匹配位求和 ≈ 活动级
- [ ] 搜索词 + 搜索 ASIN 的花费覆盖 ≥85% 总花费（剩余为未归因）
- [ ] ABA 排名：抽查 1 个词，与页面显示的「搜索词排名」一致
- [ ] 我方售价：与 Amazon 前台一致
- [ ] 竞品抓取：`item_count` ≥ 12，`blocked=false`
