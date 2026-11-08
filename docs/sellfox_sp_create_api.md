# 赛狐 SP 广告活动创建接口（实测记录）

> 记录日期：2026-11-07 ｜ 店铺：711811（金梧汇辰）｜ 站点：amazon.co.jp
> 结论一句话：**只带登录 Cookie、不带 `sf-vvv-*` 签名头，可成功创建 SP 广告活动**（实测 2 次，均 `code:0`）。

---

## 1. 抓包方法（可复用）

赛狐广告页是**无界（wujie）微前端**，内容不在 iframe，而在 `<wujie-app>` 的 **shadow DOM** 里，
直接读 `document.body` 只能拿到顶部菜单，必须穿透 shadowRoot。

**启动可调试浏览器**

```python
from playwright.sync_api import sync_playwright

ctx = p.chromium.launch_persistent_context(
    "data/chrome-debug-profile", headless=False, viewport=None,
    args=["--remote-debugging-port=9333", "--remote-allow-origins=*",   # 必须放行 origin，否则 WS 403
          "--no-first-run", "--no-default-browser-check"])
```

**监听接口（CDP）**

1. 连 `http://127.0.0.1:9333/json` 取 page 的 `webSocketDebuggerUrl`；
2. `Network.enable` + `Runtime.enable`；
3. 点击按钮（在 shadowRoot 里找 `button`，`b.click()`）；
4. 收 `Network.requestWillBeSent`（含 `postData`/`headers`）与 `Network.responseReceived`；
5. 对每个 requestId 调 `Network.getResponseBody` 拿响应体。

**读取页面状态（shadow DOM）**

```js
var host = [...document.querySelectorAll('wujie-app')]
  .find(h => h.offsetWidth > 0 && h.shadowRoot.querySelectorAll('.el-form-item').length > 3);
var root = host.shadowRoot;   // 22 个 .el-form-item、按钮、表格都在这里
```

---

## 2. 接口契约

| 项 | 内容 |
|---|---|
| URL | `https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/ad/sp/create` |
| 方法 | `POST`（`application/json`） |
| 鉴权 | 登录 Cookie（含 `sf_u`） |
| 签名头 | **不需要** `sf-vvv-i` / `sf-vvv-t` / `x-request-id`（实测不带也成功） |
| 幂等 | 否，每次调用都新建 |
| 实测耗时 | 2.42s |

**必需请求头**

```
Content-Type: application/json
Accept: application/json, text/plain, */*
Origin: https://www.sellfox.com
Referer: https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3?amzup-web-cpc-vue3=%2F…ad-create%2Findex.html%3FshopIdList%3D711811…
User-Agent: Chrome/151 …
X-Language: zh-CN
Cookie: <浏览器登录态，14 项，含 sf_u>
```

### 2.1 请求体（三段）

完整样例见 [`samples/sp_create_request.json`](samples/sp_create_request.json)。

**根**

| 字段 | 类型 | 说明 |
|---|---|---|
| `shopId` | string | `"711811"` |
| `marketplaceId` | string | 日本站 `"A1VC38T7YXB528"` |

**`spCampaignInfo`（活动层）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `campaignId` | string | 新建传 `""` |
| `startDateStr` / `endDateStr` | string | `yyyy-mm-dd` |
| `dailyBudget` | string | 每日预算，如 `"300"` |
| `targetingType` | string | `auto` / `manual` |
| `strategy` | string | `autoForSales`（提高和降低）/ `legacyForSales`（只降低）/ `manual`（固定竞价） |
| `placementTop` | string | 搜索结果顶部加价 % |
| `placementRestOfSearch` | string | 搜索结果其余位置 % |
| `placementProductPage` | string | 商品详情页 % |
| `placementSiteAmazonBusiness` | string | 企业购 % |
| `creativeBidPercentage` | null | — |
| `campaignSite` | string | `amazonAll`（亚马逊及其他网站）/ 企业购为另一枚举 |
| `name` | string | 活动名称 |
| `portfolioId` | string | 广告组合 ID，无组合传 `""` |
| `audienceId` / `audienceBidPercentage` / `defaultAudience` | string/string/bool | 人群包，未选为 `""` / `""` / `false` |

**`spGroupInfo`（广告组层）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `groupId` | string | 新建传 `""` |
| `defaultBid` | string | 广告组默认竞价，如 `"30"` |
| `name` | string | 广告组名称 |
| `products[]` | array | `{id, sku, asin, mainImage, spotlightVideos}` |

⚠️ `products[].id` 是**赛狐广告产品行 id**（如 `56628517`），不是 ASIN、也不是 `adId`，需要从广告产品列表接口取。

**`spTargetInfo`（投放层）**

| 字段 | 类型 | 说明 |
|---|---|---|
| `autoTarget` / `keywordTarget` | bool | 自动投放 / 手动投放开关 |
| `keywords[]` / `targetings[]` | array | 手动投放用；自动投放传 `[]` |
| `queryHighRelMatchesState` / `Bid` | bool / string | 紧密匹配（`true` / `"17.0"`） |
| `queryBroadRelMatchesState` / `Bid` | bool / string | 宽泛匹配（`"18.0"`） |
| `asinSubstituteRelatedState` / `Bid` | bool / string | 同类商品（`"16.0"`） |
| `asinAccessoryRelatedState` / `Bid` | bool / string | 关联商品（`"17.0"`） |
| `neKeywords[]` | array | `{keywordText, matchType:"negativeExact"/"negativePhrase", index, campaignId:""}` |
| `neTargetings[]` | array | `{asin, title, imgUrl, type:"asin", index, campaignId:"", groupId:""}` |

### 2.2 响应体

完整样例见 [`samples/sp_create_response.json`](samples/sp_create_response.json)。

```json
{
  "code": 0, "msg": "",
  "data": {
    "campaignResp": {"code": 0, "campaignId": "262736104900156"},
    "groupResp":    {"code": 0, "adGroupId": "365363228240902"},
    "targetResp":   {"code": 0, "targetType": "autoTarget", "targetList": [
        {"code": 0, "targetId": "3023664913597",     "matchTypeForAutoTarget": "queryHighRelMatches"},
        {"code": 0, "targetId": "18345118220585",    "matchTypeForAutoTarget": "queryBroadRelMatches"},
        {"code": 0, "targetId": "157193560593586",   "matchTypeForAutoTarget": "asinAccessoryRelated"},
        {"code": 0, "targetId": "200767725408856",   "matchTypeForAutoTarget": "asinSubstituteRelated"}]},
    "neKeywordResp": {"code": 0, "neKeywords": [{"code": 0, "index": 0, "neKeywordTargetId": "..."}]},
    "neTargetResp":  {"code": 0, "neTargets":  [{"code": 0, "index": 0, "neProductTargetId": "..."}]}
  }
}
```

判读：根 `code=0` 且各子模块 `Resp.code=0` 才算对应部分成功；否定投放为空时子响应只回 `{"code":0}`。

---

## 3. 实测记录

| # | 方式 | 请求头 | 结果 |
|---|---|---|---|
| 1 | 页面点击「创建」（`CA_test1`） | 完整（含 `sf-vvv-*`） | `campaignId=8908866945446`，`adGroupId=433001404372202` |
| 2 | 脚本直连（`CA_api_test_11071403`） | 仅 Cookie + 基础头 | HTTP 200，`code=0`，`campaignId=262736104900156`，2.42s |

第 2 次创建的内容：
- 活动 `CA_api_test_11071403`，2026-11-22 ~ 2026-11-30，预算 300，组合「技术测试组合」
- 广告组 `GR_api_test_11071403`，默认竞价 30
- 4 个自动投放：17 / 18 / 16 / 17
- 广告位：10% / 20% / 30% / 40%
- 否定投放：主动清空

---

## 4. 最小直连示例

```python
import json, requests

URL = ("https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/ad/sp/create")
payload = json.load(open("docs/samples/sp_create_request.json", encoding="utf-8"))
payload["spCampaignInfo"]["campaignId"] = ""
payload["spCampaignInfo"]["name"] = "CA_api_test"

r = requests.post(URL, json=payload, cookies=cookies, timeout=60, headers={
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://www.sellfox.com",
    "Referer": "<ad-create 页地址>",
    "User-Agent": "<Chrome UA>",
    "X-Language": "zh-CN",
})
print(r.json()["data"]["campaignResp"]["campaignId"])
```

Cookie 取法（实时，最稳）：

```python
# CDP: Network.getAllCookies → 过滤 domain 含 sellfox.com
```

---

## 5. 结论与风险

**结论**
- 写接口不校验前端签名头，Cookie 即鉴权；可安全接口化。
- 每次调用真实创建 活动 + 广告组 + 4 个自动投放，**非幂等**。

**风险与节制**
- Cookie 过期返回 `code=2001`，重新从浏览器取，不要高频重登；
- 建议创建间隔 3–5s，单账号每小时不超过几十个；
- 出错立即停，不做重试风暴；
- 平台侧限流/风控表现为 429 或要求重新登录，正常量级不会触发。

---

## 6. 待确认（封装前）

1. `products[].id` 的确切来源字段（疑似广告产品列表行 `id`，需再抓一次列表返回核对）；
2. **部分成功语义**：`campaignResp` 成功但 `groupResp` 失败时，是否留下空活动；
3. 创建后活动状态（投放中/待审核）接口不返回，只能查列表；
4. `campaignSite` 企业购枚举值待补；
5. 修改/暂停已有活动走哪个接口（尚未抓）。

---

## 7. 修改 SP 广告活动（spCampaign/update）

**入口**：活动设置页 `cpc-single/web/cpcManage/manage/single.html?type=SPCampaignSetting&...`
点击左下「保存」触发。

| 项 | 内容 |
|---|---|
| URL | `https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/spCampaign/update` |
| 方法 | `POST`（`application/json`） |
| 鉴权 | 同创建：登录 Cookie 即可 |
| 响应 | `{"code":0,"msg":"","data":null}`（无返回体，成功即 null） |

**请求体**（实测，样例见 [`samples/sp_campaign_update_request.json`](samples/sp_campaign_update_request.json)）

```json
{
  "shopId": "711811",
  "dxmCampaignId": 14312600346,
  "name": "CA_api_test_ttt",
  "portfolioId": "-1",
  "startDateStr": "2026-12-01",
  "endDateStr": "2026-12-05",
  "dailyBudget": "150",
  "targetingType": "auto",
  "strategy": "manual",
  "placementTop": 15,
  "placementProductPage": 35,
  "placementRestOfSearch": 25,
  "placementSiteAmazonBusiness": 45,
  "creativeBidPercentage": 0,
  "audienceId": "",
  "audienceBidPercentage": 0,
  "defaultAudience": false,
  "campaignSite": "amazonAll",
  "offAmazonBudgetControlStrategy": null
}
```

**与创建接口的关键差异（容易踩坑）**

| 字段 | create | update |
|---|---|---|
| 标识 | `spCampaignInfo.campaignId: ""` | `dxmCampaignId`（**数字**，= 活动设置页 URL 的 `id`） |
| 结构 | 三段：campaign/group/target | **单层扁平**，只改活动层 |
| `portfolioId` | 具体 ID 字符串（如 `"230512165427632"`） | 无组合 = `"-1"` |
| `placementTop` 等 | **字符串**（`"10"`） | **数字**（`15`） |
| `creativeBidPercentage` | `null` | `0` |
| `audienceBidPercentage` | `""` | `0` |
| 额外字段 | — | `offAmazonBudgetControlStrategy: null` |
| `strategy` | `autoForSales` | `manual`（固定竞价）/ `legacyForSales` / `autoForSales` / `ruleBased` |

**注意**
- 这是**全量覆盖**语义：请求体里没带的字段可能被清空，改造前先 GET 一次当前值再提交；
- 只能改活动层（名称/组合/日期/预算/策略/广告位/受众），改广告组、投放要走各自的接口；
- 实测保存后列表页数据会刷新，接口本身不回传新值，需再查列表确认。

## 8. 暂停 / 启用活动（spCampaign/updateState）

**入口**：广告管理列表 → 行内「⋮」（`icon_sf_more`）→ 复制 / **暂停** / 归档

| 项 | 内容 |
|---|---|
| URL | `https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/spCampaign/updateState` |
| 方法 | `POST` |
| Content-Type | **`application/x-www-form-urlencoded`**（不是 JSON！） |
| 请求体 | `shopId=711811&id=14312600346&state=paused` |
| 响应 | `{"code":0,"msg":"","data":null}` |
| 鉴权 | 登录 Cookie |

**字段**

| 字段 | 说明 |
|---|---|
| `shopId` | 店铺 ID |
| `id` | **dxmCampaignId（数字）**，即设置页 URL 的 `id` |
| `state` | `paused` 暂停 / `enabled` 启用（均已实测） |

**实测**
- 暂停：页面点击 → `updateState` 200，`code:0`；列表读回 `state=paused`、`servingStatus=CAMPAIGN_PAUSED`；
- 启用：接口直调 `state=enabled` → `code:0`；列表读回 `state=enabled`、`servingStatus=PENDING_START_DATE`（开始日期 2026-11-22 未到，故尚未开跑）；
- 用 JSON 提交 → `{"code":360000,"msg":"请求参数错误"}`；用 form-urlencoded 提交 → `code:0`。**编码不对会直接报参数错误**。

**状态对照**

| state | servingStatus | 含义 |
|---|---|---|
| `enabled` | `PENDING_START_DATE` | 已开启但未到开始日期 |
| `enabled` | `CAMPAIGN_STATUS_ENABLED`（列表页显示"正在投放"） | 正常投放 |
| `paused` | `CAMPAIGN_PAUSED` | 已暂停 |

**注意**
- 该接口只切状态，不动其他字段；
- 列表校验建议按 `name` 搜索（`searchType: contains`），因为按 `dxmCampaignId` 过滤时列表接口可能不返回该字段。

## 9. 归档活动（spCampaign/archive）

**入口**：广告管理列表 → 行内「⋮」→ **归档** → 二次确认「确定要归档吗？」

| 项 | 内容 |
|---|---|
| URL | `https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/spCampaign/archive` |
| 方法 | `POST` |
| Content-Type | **`application/x-www-form-urlencoded`** |
| 请求体 | `shopId=711811&id=14312473523` |
| 响应 | `{"code":0,"msg":"","data":null}` |
| 鉴权 | 登录 Cookie |

**字段**

| 字段 | 说明 |
|---|---|
| `shopId` | 店铺 ID |
| `id` | **dxmCampaignId（数字）**，不是亚马逊 campaignId |

**实测**
- 对 `CA_test1`（dxmCampaignId=14312473523）归档 → `code:0`；
- 读回：`state=archived`、`servingStatus=PENDING_START_DATE`；
- 归档后需要用 `status: "archived"` 才能在列表接口查到（默认视图不再展示）；
- 同店铺其他活动不受影响（`CA_api_test_11071403` 仍 `enabled`）。

**状态补充**：`state` 取值已见 `enabled` / `paused` / `archived` 三种。

**如何拿到 dxmCampaignId（关键）**
创建接口只返回亚马逊侧 `campaignId`，`update` / `updateState` / `archive` 需要的是内部 `dxmCampaignId`。
从 `campaign/getAllCampaignData` 的返回行里取 **`id` 字段**即可（实测 `ttttt` → `id=14313084761`，与设置页 URL 的 `id` 完全一致）：

```json
{"name": "ttttt", "id": 14313084761, "campaignId": "170267213427440", ...}
```

## 10. 添加广告组（复用 ad/sp/create）

**入口**：活动 → 广告组 tab → 「添加广告组」弹窗 → 「创建」按钮

**重要**：这不是独立接口，**复用 `ad/sp/create`**，靠 `spCampaignInfo.campaignId` 非空来区分"给已有活动加组"。

样例：[`samples/sp_add_group_request.json`](samples/sp_add_group_request.json)

**与「新建活动」的字段差异**

| 字段 | 新建活动 | 添加广告组 |
|---|---|---|
| `spCampaignInfo.campaignId` | `""` | **已有活动ID**（`262736104900156`） |
| `spCampaignInfo.name` | 新活动名 | `""` |
| `startDateStr` / `endDateStr` | 日期 | `""` |
| `dailyBudget` | 预算 | 活动当前预算（`"300.00"`，仅透传） |
| `placementTop` 等 4 项 | 数字 | **`null`** |
| `portfolioId` | 组合 ID | `""` |
| `spGroupInfo` | 新组信息 | 新组信息（`groupId: ""`） |
| `spTargetInfo.neKeywords/neTargetings` | 可空 | 带上该活动已有否定项（`campaignId` 指向活动） |

**实际请求要点（实测）**

```json
{
  "shopId": "711811",
  "marketplaceId": "A1VC38T7YXB528",
  "spCampaignInfo": {"campaignId": "262736104900156", "startDateStr": "", "endDateStr": "",
    "dailyBudget": "300.00", "targetingType": "auto", "strategy": "autoForSales",
    "placementProductPage": null, "placementTop": null, "placementRestOfSearch": null,
    "placementSiteAmazonBusiness": null, "name": "", "portfolioId": "",
    "campaignSite": "amazonAll", "audienceId": "", "audienceBidPercentage": "",
    "defaultAudience": false},
  "spGroupInfo": {"groupId": "", "defaultBid": "22", "name": "GR_test2",
    "products": [{"id": 56628535, "sku": "abc005-A-10個セット", "asin": "B0HHWBRQNC", "mainImage": "..."}]},
  "spTargetInfo": {"autoTarget": true, "keywordTarget": true,
    "queryHighRelMatchesBid": "13.0", "queryBroadRelMatchesBid": "13.0",
    "asinSubstituteRelatedBid": "12.0", "asinAccessoryRelatedBid": "15.0",
    "neKeywords": [{"keywordText": "ggg", "matchType": "negativeExact", "index": 0,
                    "campaignId": "262736104900156"}]}
}
```

**响应**：`campaignResp.campaignId` 回的是**已有活动 ID**，新组的 ID 在 `groupResp.adGroupId`（实测 `490926517106271`），`targetResp.targetList` 是 4 个新投放。

**注意**
- 组名、默认竞价、产品（`products[].id`）是真正要填的；
- 活动层字段只是"带上下文"，填了也不改活动（要改活动走 `spCampaign/update`）；
- 一次弹窗 = 一次 create 调用，返回的 `adGroupId` 即新组。

**多组活动（实测）**：`spGroupInfo` 是**对象不是数组**，一次请求只能建 1 个组。
要建「1 活动 + N 组」需 **1 + (N-1) 次调用**：首次 `campaignId=""` 建活动，后续 `campaignId=<新活动ID>` 追加组。

```
CA_3groups_143826 (campaignId=51358980046548)
  ├─ G1_143826  479411477721192  默认竞价 20   ← 第 1 次调用（建活动）
  ├─ G2_143826  367981348535344  默认竞价 25   ← 第 2 次调用
  └─ G3_143826  549710424919867  默认竞价 30   ← 第 3 次调用
```
每次 2–3s；3 组总耗时约 8s。批量建组建议间隔 ≥2s。

### 10.1 UI 抓包实锤（2026-11-07，ctt1111 加组）

入口：活动 → 广告组 tab → 「添加广告组」弹窗（填组名/默认竞价/勾产品/关键词）→ 弹窗内「创建」。

**接口仍是同一个** `POST .../ad/sp/create`（JSON），body 9,622 字节。**活动层字段的精确取值**：

```json
"spCampaignInfo": {
  "campaignId": "244077946952639",     // 已有活动 id（关键：非空即"加组"）
  "startDateStr": "", "endDateStr": "", // 空串，不动活动日期
  "dailyBudget": "333.00",              // 活动当前预算（透传，非改动）
  "targetingType": "manual",            // 与活动一致
  "strategy": "autoForSales",
  "placementProductPage": null, "placementTop": null,
  "placementRestOfSearch": null, "placementSiteAmazonBusiness": null,
  "creativeBidPercentage": null,
  "campaignSite": "amazonAll",
  "name": "",                           // 空串，不动活动名
  "portfolioId": "",                    // 空串，不动组合
  "audienceId": "", "audienceBidPercentage": "", "defaultAudience": false,
  "offAmazonBudgetControlStrategy": null
}
```
对比第 10 节早期记录的 `placementXxx: null` 与 `portfolioId: ""`——一致；
本次新增确认为 **`offAmazonBudgetControlStrategy: null` 也会带**。

**新组实测结果（ctt1111 → 加组 cttsss）**

| 项 | 值 |
|---|---|
| 活动 | ctt1111（campaignId 244077946952639，已有） |
| 新组 | `cttsss`，adGroupId **266788419820716**（内部 12734272985），默认竞价 **222** |
| 产品 | **42 个**（一次全量勾选，products 数组直接传 42 条） |
| 关键词 | **12 个**（4 theme + 3 broad + 3 phrase + 2 exact），响应 12/12 全成功 |
| 读回 | 该活动 `getAllGroupData` 组数 1 → **2**，新组产品 42、关键词 12 ✅ |

**新发现：关键词行的行 key 字段不固定**

| 场景 | 行 key 字段 |
|---|---|
| 新建活动页（ad-create） | `_XROW_KEY` |
| 添加广告组弹窗（cpc-single） | **`_XID`** |

两个都是前端表格内部 key（如 `row_96`），后端忽略，构造请求时**可不传**；
但抓包对比时别把两者当同一字段。

## 11. 经验流程：1 个活动 + N 个广告组

> 已验证案例：`CA_3groups_143826`（campaignId=51358980046548），3 组，总耗时约 8s。

### 11.1 适用场景
- 一个活动下按「品类 / 价格带 / 变体分组」拆多个广告组
- 每个组独立默认竞价、独立产品、独立 4 档投放竞价

### 11.2 标准流程（3 步 + 1 校验）

```
① 建活动 + 第 1 组：campaignId=""，spGroupInfo 填 G1
       ↓ 拿到 campaignId
② 追加第 2 组：campaignId=<上一步活动ID>，spGroupInfo 填 G2
       ↓
③ 追加第 N 组：同上
       ↓
④ 读回校验：multiple/group/getAllGroupData[campaignIdList=[活动ID]]
```

**关键规则**：`spGroupInfo` 是对象不是数组，**一次请求只能建 1 个组**；N 个组 = N 次 create 调用（第 1 次顺带建活动）。

### 11.3 参数约定（实测踩过的点）

| 项 | 约定 |
|---|---|
| 活动层字段 | 第 1 次调用才填（名称、日期、预算、组合、广告位 4 档）；后续追加统一传空/`null`，只保留 `campaignId` |
| `name` 唯一性 | 组名可重复，无唯一校验；建议 `G1_<HHMMSS>` 形式，便于回溯 |
| `neKeywords` / `neTargetings` | **追加组时清空**，否则会把活动已有否定项重复写一遍 |
| `products[].id` | 赛狐广告产品行 id（非 ASIN），每个组可放多个 |
| 4 档竞价 | 列表型场景可按组差异化（如 15/15/14/16、18/18/16/19） |
| 间隔 | 每次调用间隔 ≥2s，单次 2–3s |

### 11.4 代码骨架

```python
cid = ""
for i, g in enumerate(groups, 1):
    payload = build(campaign_id=cid, group=g)      # 首次 cid 为空
    r = requests.post(GW + "ad/sp/create", json=payload, headers=H, cookies=ck)
    d = r.json()["data"]
    cid = cid or d["campaignResp"]["campaignId"]   # 首次回填
    g["adGroupId"] = d["groupResp"]["adGroupId"]
    time.sleep(2)
```

### 11.5 校验与失败处理

- 读回 `getAllGroupData` 的 `totalSize` 应等于 N；
- 某次追加失败只影响该组，活动已建成功，可单独重试该组（幂等性差，重试前先按组名查是否已存在）；
- 全部失败时活动可能残留（只有活动没有组），需要人工或后续接口清理。

### 11.6 耗时参考

| 组数 | 调用次数 | 预计耗时 |
|---|---|---|
| 1 | 1 | 2–3s |
| 3 | 3 | 7–9s |
| 10 | 10 | 25–30s |
| 30 | 30 | 80–95s |

## 12. 广告组合（Portfolio）接口

**重要：写接口不在 gw 网关前缀下**，而在老路径 `/api/portfolio/*.json`，且编码是 **form-urlencoded**；读接口仍在 `.../api/sellfox/multiple/portfolio/getAllPortfolioData`（JSON）。

### 12.1 创建组合

```
POST https://www.sellfox.com/api/portfolio/createPortfolioData.json
Content-Type: application/x-www-form-urlencoded

shopId=711811&portfolioName=PF_api_test_1107
→ {"code":0,"msg":"success","data":"270905298437020"}
```
- **最小参数只要两个**：`shopId` + `portfolioName`
- 响应 `data` 就是新建的 **portfolioId**
- 实测：`PF_api_test_1107` → `270905298437020`

### 12.2 修改组合

```
POST https://www.sellfox.com/api/portfolio/updatePortFolioData.json     ← 注意大写 P
Content-Type: application/x-www-form-urlencoded

portfolioName=PF_api_test_1107_v2
shopId=711811
policy=noBudget
portfolioId=270905298437020
amount=0.00
budgetEndDate=
budgetStartDate=
budgetSharingState=ENABLED
→ {"code":0,"msg":"success","data":"270905298437020"}
```
- **必须要带的字段比创建多**：只传 `portfolioId`+`portfolioName` 会返回 `code=360000 请求参数错误`
- `policy`：预算策略（`noBudget` = 不启用预算管理）
- `amount` / `budgetStartDate` / `budgetEndDate` / `budgetSharingState`：预算相关，未启用时传 `0.00` / 空串 / `ENABLED`
- 实测改名成功，读回 `PF_api_test_1107_v2`

### 12.3 其它组合接口（JS 中已确认存在，未逐个实测）

| 方法 | 路径 | 用途 |
|---|---|---|
| `updatePortfolioHiddenState` | `/api/portfolio/updatePortfolioHiddenState.json` | 隐藏/显示组合 |
| `removePortFolioData` | `/api/portfolio/removePortFolioData.json` | 删除组合 |
| `movePortFolioData` | `/api/portfolio/movePortFolioData.json` | 活动移入/移出组合 |
| `updatePortFolioRank` | `/api/portfolio/updatePortFolioRank.json` | 组合排序 |
| `getAllPortfolioData` | gw 前缀 + `multiple/portfolio/getAllPortfolioData` | 列表（JSON） |

**命名坑**：`updatePortFolioData`、`removePortFolioData`、`movePortFolioData` 里是**大写 P（Folio）**，而 `getAllPortfolioData` 是小写。写错直接 404 或跳转登录页。

## 13. 广告产品维度（AdAdList）

**页面**：`cpc-single/web/cpcManage/manage/single.html?type=AdAdList&...&adGroupId=<组ID>`
（活动 → 广告组 → 广告产品）

### 13.1 添加广告产品

```
POST https://www.sellfox.com/api/cpcSPManage/product/v2/add.json
Content-Type: application/json

{
  "shopId": "711811",
  "groupIds": ["490926517106271"],
  "products": [{
    "id": 60427897,                 // 产品库 id（不是广告产品 id）
    "shopId": 711811,
    "sku": "ll058-B-71*46*46cm",
    "asin": "B0HQGMTCB3",
    "onlineStatus": "Active",
    "title": "…",
    "imgUrl": "https://m.media-amazon.com/images/I/…._SL75_.jpg",
    "domain": "www.amazon.co.jp",
    "isMeetConditions": true,
    "price": "11499.00",
    "batchCreateParentAsin": "B0HQGMTCB3",
    "keyId": 1794034469182,
    "href": "https://www.amazon.co.jp/dp/B0HQGMTCB3",
    "isAdd": false, "variantAdd": false, "type": "sp"
  }]
}
→ {"code":0,"msg":"","data":null}
```
- 可一次传多个 `groupIds`（把同一个产品加到多个组）
- 样例：[`samples/sp_add_product_request.json`](samples/sp_add_product_request.json)
- 实测：加 `ll058-B-71*46*46cm` 后列表出现 `id=86988578432`
- 精简可用字段（实测通过）：`id`(产品库 id) / `shopId` / `sku` / `asin` / `onlineStatus:"Active"` / `title` / `imgUrl` / `domain` / `isMeetConditions:true` / `price:"0"` / `type:"sp"` / `isAdd:false` / `variantAdd:false`
- 返回后新广告产品 id 需重新查列表获得（如 `abc002-イエロー` → `86988890175`）

**部分失败的判定（重要）**：批量添加时即使有 SKU 重复，HTTP 与根 `code` 仍是 `0`，错误在 `data` 里：

```json
{"code":0,"msg":"","data":"[{\"asin\":\"B0HHWBRQNC\",\"msku\":\"abc005-A-10個セット\",
  \"adGroupName\":\"GR_test2\",\"error\":\"目标广告组 GR_test2 中已有该MSKU，无法添加\"}]"}
```
→ 必须解析 `data`（字符串形式的 JSON 数组），为空/null 才算全部成功。

**同 SKU 的重复判定（实测）**
| 组内已有状态 | 再次添加 |
|---|---|
| `enabled` | 拒绝（已有该 MSKU） |
| `paused` | **拒绝** |
| `archived` | **允许**，会新增一条 `enabled` 记录（同 SKU 出现两条） |

### 13.2 暂停 / 启用广告产品

```
POST https://www.sellfox.com/api/cpcSPManage/product/updateState.json
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=86986007429&state=paused
→ {"code":0,"msg":"","data":null}
```
- 页面入口 = 行内「有效」开关（`el-switch`），关 → `paused`，开 → `enabled`
- `id` 是**广告产品 id**（组内 id），不是产品库 id

### 13.3 归档广告产品

```
POST https://www.sellfox.com/api/cpcSPManage/product/archive.json
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=86986007428
→ {"code":0,"msg":"","data":null}
```
- 页面入口 = 行内「归档」文字按钮，前端有二次确认「确定要归档吗？」，接口本身无确认
- 归档后 `state=archived`，需用 `statusList:["archived"]` 才能查到

### 13.4 读回校验

```
POST .../api/sellfox/multiple/adProduct/getAdProductList     （JSON）
{
  "type": "sp", "campaignIdList": ["262736104900156"],
  "groupIdList": ["490926517106271"], "shopIdList": ["711811"],
  "statusList": ["enabled"],          // 或 ["paused"] / ["archived"]
  "startDate": "2026-11-01", "endDate": "2026-11-07",
  "pageNo": 1, "pageSize": 50, "orderBy": "adCost", "desc": true,
  "searchContents": [], "searchMode": "exact", "searchType": "asin",
  "adStrategyTypeList": [], "servingStatusList": [], "useAdvanced": false,
  "advanceFilter": {}
}
```
实测结果（GR_test2 组）：

| 状态 | 数量 | 明细 |
|---|---|---|
| enabled | 3 | ll058-B-71*46*46cm（新增）、abc005-A-10、abc005-A-50 |
| paused | 1 | abc005-A-30（`id=86986007429`） |
| archived | 1 | abc005-A-20（`id=86986007428`） |

### 13.5 两个 id 别混用

| id | 示例 | 用途 |
|---|---|---|
| 产品库 id | `60427897` | 添加产品时 `products[].id`（同一产品全局唯一） |
| 广告产品 id | `86988578432` | 暂停/启用/归档时的 `id`（**组内实例**，不唯一） |

**同一产品在不同组里是两个 id**（实测）：
`ll058-B-71*46*46cm` 加到 GR_test2 后是 `86988578432`，加到 GR_test3 后是 `86988789308`。
所以暂停/归档必须用「该组的」广告产品 id，不能用另一个组查到的。

## 14. 投放（Target / AdTargetList）

**页面**：`cpc-single/web/cpcManage/manage/single.html?type=AdTargetList&...&adGroupId=<组ID>`
（活动 → 广告组 → 投放；自动投放固定 4 行：紧密 / 宽泛 / 同类产品 / 关联产品）

### 14.1 暂停 / 启用投放

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/spTarget/updateState
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=14324634421&state=paused
→ {"code":0,"msg":"","data":null}
```
- 页面入口：行内「有效」开关（`el-switch`），关 → `paused`，开 → `enabled`
- 实测：关联产品被暂停，读回 `state=paused`

### 14.2 修改竞价

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/spTarget/updateBid
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=14324634422&bid=14.5
→ {"code":0,"msg":"","data":null}
```
- 页面入口：点「竞价」单元格里的金额 → 出现输入框 + 取消/确定 → 改值后点确定
- 实测：同类产品 12.00 → 14.50，读回 `bid=14.50`

### 14.3 读回校验

```
POST .../api/sellfox/multiple/target/getAllTargetData     （JSON）
{"shopIdList":["711811"],"campaignIdList":["262736104900156"],
 "groupIdList":["490926517106271"],"adType":"sp","targetType":"auto",
 "statusList":["enabled","paused"],"startDate":"2026-11-01","endDate":"2026-11-07",
 "pageNo":1,"pageSize":50,"fromSingle":true,"searchType":"exact","searchField":"name"}
```
实测（GR_test2）：

| 匹配类型 | bid | state |
|---|---|---|
| asinSubstituteRelated（同类产品） | **14.50**（本次改） | enabled |
| asinAccessoryRelated（关联产品） | 15.00 | **paused**（本次暂停） |
| queryBroadRelMatches（宽泛） | 13.00 | enabled |
| queryHighRelMatches（紧密） | 13.00 | enabled |

### 14.4 两个 target id

| id | 示例 | 用途 |
|---|---|---|
| 赛狐内部 target id | `14324634422` | `updateBid` / `updateState` 的 `id` |
| 亚马逊 targetId | `154803584360166` | 列表接口返回的 `targetId`，只读 |

与广告产品一样，**操作接口用赛狐内部 id**，不要用列表里的 `targetId`。

## 15. 批量操作与组合移动（补充实测）

### 15.1 投放批量「调整」下拉

投放列表工具栏的「调整」是 `el-dropdown`，菜单项：

| 菜单项 | 条件 |
|---|---|
| 竞价 | 需先勾选行（自动投放行复选框为 `is--disabled`，不可勾选 → 实际不可用） |
| **竞价(跨页全选)** | 无需勾选，直接作用于筛选出的全部投放 |
| 开启 / 暂停 | 需勾选行（同上，自动投放不可用） |
| 分时调竞价 / 自动化规则 | 需勾选行 |

### 15.2 批量改竞价（异步任务）

UI 流程（弹窗有**二次确认**）：
调整 → 竞价(跨页全选) → 弹窗「将竞价 JP￥ [调整到] [数值]」→ 确定
→ 弹出「确认提交：本次调整覆盖 N 条投放」→ **勾选「我已确认以上调整细节无误」** → 确定

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/target/bid/batchAsyncUpdateBid
Content-Type: application/json

{"bidUpdateReq":{"bidType":"FIXED_BID","adjustValue":"14.80"},
 "targetReq":{ ...与 getAllTargetData 完全相同的筛选条件（shopIdList/campaignIdList/
              groupIdList/adType/targetType/statusList/startDate/endDate/...）...}}
→ {"code":0,"msg":"success","data":"6993315"}     ← data 是异步任务 id
```

任务进度轮询（前端每隔约 1.5s 查一次）：

```
POST .../api/sellfox/target/task/list     （JSON）
{"shopId":"711811","targetPageType":1,"sourceAdCampaignId":"262736104900156",
 "pageNo":1,"pageSize":10}
→ rows:[{"id":6993315,"status":3,"targetObjectSize":4,"targetSize":4,...}]
```
`status=3` 即完成。

**实测结果与坑**：
- 对 GR_test2 的 4 条自动投放批量设 14.80 → 结果全部为 **15.00**。
  （JPY 账户整数取整；前端 i18n 亦有提示「日本站预算仅支持整数…系统已自动取整」，
  批量调价的 `adjustValue` 同样会被取整。行内单条 `spTarget/updateBid` 则 14.50 可正常保存）
- 弹窗里点确定若未填数值，仅触达预检接口
  `multiple/target/getAllTargetCount`（返回 `{data:{count:N}}`），不会提交任务。
- 竞价值输入框是 `el-input` 文本框，程序化填值必须真实键入（native setter + input 事件
  不进 v-model，会报「请输入数值」）。

### 15.3 movePortFolioData：活动移入组合（已实测）

```
POST https://www.sellfox.com/api/portfolio/movePortFolioData.json
Content-Type: application/x-www-form-urlencoded

shopId=711811&portfolioId=230512165427632&campaignIds=262736104900156
→ {"code":0,"msg":"","data":null}
```
- `campaignIds` 为亚马逊 campaignId，逗号分隔可多个；批量操作时返回
  `data:[{campaignId, name, errMsg}]` 逐条结果（部分失败不报错，看 errMsg）。
- **移出不支持**：`portfolioId=0` 移出 → Amazon 报 `"errMsg":"EntityID does not exist."`。
  亚马逊组合体系没有「无组合」回退，只能把活动移入**另一个**组合。
- 验证：移入后 `getAllPortfolioData` 里 `campaignNumber` 0 → 1。

### 15.4 removePortFolioData：删除组合（未打通，疑似废弃）

- 接口在 api 层有定义（`/api/portfolio/removePortFolioData.json`，postForm），
  但**新旧两版前端均已无调用点**（新组合页行内仅 分析/编辑，工具栏仅
  创建/调整预算/隐藏/添加预警，无删除；旧版 chunk 亦无调用）。
- 试探了 8 种常见参数组合（`shopId+portfolioId`、`ids`、`portfolioIds`、
  `portfolioIdList`、`id`（两种 id 体系）等）全部 `code:360000 请求参数错误`。
- 结论：判定为遗留死代码。且亚马逊组合 API 本身不支持删除，此接口即使打通
  大概率也只是内部隐藏/归档。如需"删除"组合，先用 `updatePortFolioData` 置空预算
  并把活动移走，或走隐藏 `updatePortfolioHiddenState.json`。

### 15.5 组合的两个 id（补充）

| id | 示例 | 用途 |
|---|---|---|
| 亚马逊 portfolioId | `270905298437020` | 创建时返回、move 的 `portfolioId`、列表展示 |
| 赛狐内部 id | `1646953252` | 列表行内 `id` 字段，暂未发现写接口使用 |

`getTypeCampaignData.json`（读组合下活动）当前会话直接调用会 302 跳转
`/web/dashboard.html`，参数契约未打通。

### 15.6 本次实测留下的数据变更

| 变更 | 状态 |
|---|---|
| GR_test2 4 条投放竞价 14.8→15.00 | ✅ 已用 `spTarget/updateBid` 恢复基线 14.50/15.00/13.00/13.00 |
| 批量调价任务 6993315 | 已完成（status=3），可在任务进度里查到 |
| `CA_api_test_11071403` 移入技术测试组合 | 保留（原属"无组合"，Amazon 不支持移出） |
| 组合 `PF_api_test_1107_v2` | 现为空组合，待清理 |

## 16. 否定投放（否定关键词 / 否定商品）

**页面**：`cpc-single/web/cpcManage/manage/single.html?type=AdNegateTargetList&id=<组ID>&campaignId=<活动ID>&adType=sp`
（广告组 → 否定投放；两个子 tab：否定关键词 / 否定商品，接口按 `neType` 区分）

### 16.1 读：否定投放列表

```
POST .../api/sellfox/multiple/neTarget/getAllNeTargetData     （JSON）
{orderField:"adCost", orderType:"desc", shopIdList:["711811"],
 campaignIdList:["262736104900156"], groupIdList:["490926517106271"],
 adType:"sp", neType:"keyword",            // "keyword"=否定关键词, "target"=否定商品
 neLevels:["group"], matchType:"", servingStatusList:[],
 startDate:null, endDate:null, neBeforeAfterDay:30, dataFrom:2,
 searchType:"exact", searchField:"name", searchValue:"",
 devIds:[], useAdvanced:false, pageNo:1, pageSize:20, onlyShowImpressions:false}
→ data.page.rows[]: {id, neTargetId, state:"enabled"|"archived", ...}
```
配套计数接口 `multiple/neTarget/getNeTargetAdvanceCount`（高级筛选计数，可忽略）。

### 16.2 添加否定关键词

```
POST .../api/sellfox/negative/createSpGroupNeKeywords     （JSON）
{"shopId":"711811","groupId":"490926517106271",
 "neKeywords":[{"keywordText":"zzz_apitest_kw1","keywordTextCn":"zzzapitestkw1",
                "matchType":"negativeExact"}]}
→ {"code":0,"data":{"successList":[{"keywordText":...,"matchType":...}],"errMsgList":[]}}
```
- `matchType`：`negativeExact`（否定精准）| `negativePhrase`（否定词组）
- **部分失败模式**：`errMsgList` 非空表示有失败项，`code:0` 不代表全部成功
- **字符限制（实测）**：含下划线的词 Amazon 报「关键词无效」
  （如 `zzz_negtest_01` 三个全失败；纯字母数字 `zzzapitestkw1` 正常）。
  亚马逊否定词仅允许字母/数字/空格/部分标点，提交前建议过滤 `_\|` 等符号
- 前置可选：`common/wordTranslate`（`[{word, marketplaceId}]` → `wordCn` 翻译）
- UI 弹窗：否定范围（广告活动/广告组）→ 匹配类型 → 非法字符处理 → textarea
  （换行/中文逗号分隔多个词）→ 点「添加」（进已添加列表）→「确定」才提交

### 16.3 添加否定商品（否定 ASIN）

先搜 ASIN（**老路径**，非 gw）：

```
POST https://www.sellfox.com/api/cpcSPManage/product/searchAsinsForTargeting.json     （JSON）
{"shopId":"711811","asins":"B0HHHPQQC4"}
→ data:[{asin, sku, title, imgUrl, ...}]     （不存在的 ASIN 返回 []）
```
- **一次只能查一个 ASIN**（实测拼接 `"A"+"B"` 返回 `[]`），多个 ASIN 需逐个查询后合并提交

再提交（gw 路径）：

```
POST .../api/sellfox/spTarget/createNeTargeting     （JSON）
{"shopId":"711811","groupId":"490926517106271",
 "neTargetings":[{"asin":"B0HHHPQQC4","imgUrl":"...",
                  "isAdd":true,"type":"asin", ...}]}
→ {"code":0}
```
- UI 弹窗三种来源：「输入列表」（radio `excludeAsin`/`excludeBrand` + 搜索ASIN）、
  「排除产品」（商品库勾选）、「排除品牌」
- 搜索框是**累积输入**（不自动清空），多次输入会拼接，先清空再搜

### 16.4 归档（否定关键词 / 否定商品通用）

```
POST .../api/sellfox/multiple/neTarget/updateBatchMultiShop     （JSON）
{"vos":[{"shopId":711811,"adType":"sp","neLevel":"group",
         "neType":"keyword",            // 否定商品用 "target"
         "neTargetId":"23223797985689", // 亚马逊 id
         "id":"39770066465"}]}          // 赛狐内部 id
→ {"code":0,"data":{"successNum":1,"failNum":0,"countNum":1,"errorList":[]}}
```
- **两个 id 都必传**（与投放层相同的双 id 体系），`neType` 决定操作对象
- 归档后列表 `state=archived`，totalSize 不变（列表含已归档）

### 16.5 调整能力边界

| 功能 | 否定关键词 | 否定商品 |
|---|---|---|
| 添加 | ✅ `createSpGroupNeKeywords` | ✅ `createNeTargeting` |
| 归档 | ✅ `updateBatchMultiShop` | ✅ 同左 |
| 暂停/启用 | ❌ 无（Amazon 否定词无状态切换，仅"已开启"展示） | ❌ 无 |
| 工具栏扩展 | 添加到关键词投放 / 添加到否定关键词投放 / 加入词库 | 添加到商品投放 / 添加到否定商品投放 / 加入词库 |

### 16.6 UI 坑

- 弹窗底部按钮可能被视口截断（800 高视口下「添加」在 y=838 不可点），
  CDP 自动化需先 `Emulation.setDeviceMetricsOverride` 调高视口再点击，测完 clear。
- 行级「归档」在右侧固定列（vxe-table 分容器渲染），与工具栏「归档」（需勾选行）
  同名，注意按 y 坐标区分。

### 16.7 实测留下的数据变更

| 变更 | 状态 |
|---|---|
| 否定关键词 `zzzapitestkw1`（内部 id `39770066465`） | 已归档（测试数据） |
| 否定关键词 `zzznegkwone` / `zzznegkwtwo` / `zzznegkwthree` | 已归档（测试数据） |
| 否定商品 `B0HHHPQQC4`（内部 id `14274108291`） | 已归档（测试数据） |
| 否定商品 `B0HJPSQ7TQ` / `B0HLDV9H1G` | 用户要求新增，后已按要求归档 |
| 原有 ccc / ggg / B0HHWBV6N1 / B0HHWQT377 | 未动，仍 enabled |

## 17. 广告组设置（GroupSetting）

**页面**：`cpc-single/web/cpcManage/manage/single.html?type=GroupSetting&adGroupId=<Amazon组ID>&campaignId=<Amazon活动ID>&shopId=...`
表单只有两项：**广告组名称 + 默认竞价**，加一个「保存」按钮。
（注意 URL 里的 `id=86986007429` 是列表页点击行的上下文 id，与设置接口无关）

### 17.1 读：组信息

```
GET https://www.sellfox.com/api/cpc/group/getInfo.json?shopId=711811&campaignId=262736104900156&groupId=490926517106271&type=sp
→ {"code":0,"data":{"adGroupName":"GR_test2","defaultBid":"22.0","state":"enabled",
    "groupType":"auto","campaignName":"CA_api_test_11071403",...}}
```
- 老路径 **GET** 请求；`groupId` 用 Amazon adGroupId

### 17.2 改：名称 / 默认竞价

```
POST https://www.sellfox.com/api/cpcSPManage/group/update.json     （JSON）
{"shopId":"711811","dxmGroupId":12731284648,"name":"GR_test2","defaultBid":"22.00"}
→ {"code":0}
```
- `dxmGroupId` = **赛狐内部组 id**，即组列表 `getAllGroupData` 行里的 `id` 字段
  （≠ Amazon adGroupId，≠ URL 里的 id）
- 实测：`defaultBid` 22→23 读回 23.0 → 恢复 22 读回 22.0 ✅

### 17.3 组状态：暂停 / 启用

```
POST https://www.sellfox.com/api/cpcSPManage/group/updateState.json     （form-urlencoded）
shopId=711811&id=12731284648&state=paused          # 或 enabled
→ {"code":0}
```
- `id` 同样是**赛狐内部组 id**（`dxmGroupId`）
- 页面入口：组列表（AdGroupList）行内的状态开关；设置页本身没有状态项
- 实测：paused → enabled，`getAllGroupData` 读回 `state=enabled` ✅

### 17.4 组列表读

```
POST .../api/sellfox/multiple/group/getAllGroupData     （JSON）
{"shopIdList":["711811"],"campaignIdList":["262736104900156"],"adType":"sp",
 "adGroupIds":["490926517106271"],"startDate":"2026-11-07","endDate":"2026-11-07"}
→ data.page.rows[0]: {id:12731284648, adGroupId:"490926517106271",
    name:"GR_test2", state:"enabled", defaultBid:22.0,
    portfolioName:"技术测试组合", campaignName:"CA_api_test_11071403", ...}
```

### 17.5 组的三个 id 汇总（GR_test2 实测）

| id | 值 | 用途 |
|---|---|---|
| 赛狐内部 id（`dxmGroupId`） | `12731284648` | `group/update.json`、`group/updateState.json` |
| Amazon adGroupId | `490926517106271` | `group/getInfo.json`、`getAllGroupData.adGroupIds`、各列表页 URL 的 `adGroupId` |
| URL 上下文 id | `86986007429` | 仅页面跳转参数，接口不用 |

## 18. 待验证模块（广告组下，后续再弄）

组级侧边栏还剩 3 个模块未做接口分析（截至 2026-11-07）：

| 模块 | 页面入口 | 状态 |
|---|---|---|
| 搜索词 | 广告组 → 搜索词 | ⏳ 未验证 |
| 提示词 | 广告组 → 提示词 | ⏳ 未验证 |
| 广告策略 | 广告组 → 广告策略 | 🔶 主体已解决（第 20 节，组合级分时预算模板；组级 tab 的受控对象为组，接口同体系） |

已完成的组级模块：广告产品（13）、投放（14）、否定投放（16）、广告组设置（17）。
已完成的活动级模块：广告位（19）。

## 19. 广告位（AdPlacementList，活动层级）

**页面**：`cpc-single/web/cpcManage/manage/single.html?type=AdPlacementList&campaignId=<Amazon活动ID>&level=2&...`
子 tab：全部广告位 / 企业购广告位。

### 19.1 行结构（重要：4 行 = 同一活动的 4 个广告位）

`getAllPlacementData` 返回 4 行，`id` 都是**活动内部 id**（如 `14312600346`），
用 `predicate` 区分广告位，没有独立的"广告位 id"：

| predicate | 广告位 | 可调 |
|---|---|---|
| `placementTop` | 广告位顶部（Top of Search） | ✅ |
| `placementRestOfSearch` | 其余广告位（Other on-Amazon） | ✅ |
| `placementProductPage` | 商品详情页（Detail Page） | ✅ |
| `offAmazon` | 站外（Off Amazon） | ❌ `isUpdateBid=false` |

- 每行 `percentage` = 该行广告位的当前调整比例
- 行上的 `placementTop/placementRestOfSearch/placementProductPage/placementSiteAmazonBusiness`
  四个字段 = 活动级当前值（每行都一样）
- `placementId` = `"<campaignId>|<Amazon广告位名>"` 组合键（只读展示用）
- `strategy` = `autoForSales`（动态竞价-提高和降低）/ `legacyForSales`（固定竞价）等，
  即**活动级竞价策略**；改策略走活动设置接口，不是广告位接口
- `bidSellfoxLog` / `campaignStrategySellfoxLog` = 赛狐操作日志（previousValue/newValue）

### 19.2 读

```
POST .../api/sellfox/multiple/placement/getAllPlacementData     （JSON）
{"dxmCampaignId":"", "campaignSite":"", "orderField":"adCost", "orderType":"desc",
 "adType":"sp", "campaignIdList":["262736104900156"], "devIds":[], "strategyType":"",
 "predicate":"", "startDate":"2026-11-01", "endDate":"2026-11-08", "isCompare":false,
 "compareStartDate":"", "compareEndDate":"", "adStrategyTypeList":[],
 "pageNo":1, "pageSize":20, "fromSingle":true}
→ data.page.rows[]
```
配套：`multiple/placement/getAllPlacementAggregateData`（汇总）、
`multiple/placement/getPlacementAdvanceCount`（高级筛选计数）。
另 `GET adCampaign/getInfo`（`{shopId, campaignId, type:"sp"}`）拿活动信息。

### 19.3 改：单条调整比例（已实测）

```
POST https://www.sellfox.com/api/cpcSPManage/campaign/updatePlacementAdjustment.json
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=14312600346&placement=placementTop&value=15
→ {"code":0}
```
- `id` = **活动内部 id**（= `getAllPlacementData` 行里的 `id`）
- `placement` = predicate；`value` = **0~900 的整数**（百分比，前端校验 Number.isInteger）
- 实测：placementTop 10→15 读回 15.0 → 恢复 10 读回 10.0 ✅
- gw 灰度路径：`.../api/sellfox/spCampaign/updatePlacementAdjustment`（参数同）

### 19.4 批量调整（未打通）

- 接口存在：`POST /api/cpcSPManage/campaign/batchUpdatePlacementAdjustment`（JSON）
  灰度路径 `.../spCampaign/batchUpdatePlacementAdjustment`
- 弹窗在懒加载 chunk 中未加载，且页面「调整」下拉菜单（批量调整/分时调广告位/自动化规则）
  在 CDP 下无法展开（DOM click/hover/强制显隐均无效）
- 试探 5 种参数形态（`vos[]`、`list`、`ids+placement`、平铺等）全部参数错误，
  **参数契约未知**

### 19.5 UI 坑

- 「调整」下拉在 CDP 自动化下点不开（与投放页「调整」可正常点开不同），
  菜单项只能从隐藏 DOM 里读出：批量调整 / 分时调广告位 / 自动化规则
- 广告位行与活动共享 id，误把行 id 当广告位 id 无影响（本身就是活动 id）

## 20. 广告策略——分时组合预算模板（portfolio/Strategy 页）

**页面**：`cpc-single/web/cpcManage/manage/single.html?type=portfolio&pageType=Strategy&portfolioId=<组合ID>&shopId=...`
（广告组合 → 广告策略 tab，即「分时组合预算」）
页面按钮：创建策略 / 选择策略模板；列表列：模板、有效、生效日期、当前规则策略、规则更新状态。

### 20.1 模板体系

创建策略 = 新建「分时调广告组合预算模板」（仅保存为模板，或保存并应用到当前组合）。
策略与模板分离：模板（template）→ 应用（submitStrategy）到具体组合。

### 20.2 创建模板（已实测）

```
POST .../api/sellfox/strategy/insertTemplate     （JSON）
{"shopId":"711811",
 "templateName":"zzz_test_budget_tpl",
 "marketplaceId":"A1VC38T7YXB528",
 "itemType":"PORTFOLIO",
 "type":"DAILY",                       // HOUR | DAILY | WEEKLY（设置方式+周期）
 "portfolioRuleList":[{"siteDate":"0","portfolios":[{"amount":500}]}],
                                      // DAILY 固定 siteDate="0"；HOUR 用 portfolioHourRuleList
 "returnValue":{"policy":"noBudget","amount":""},
                                      // noBudget=无预算上限 | MonthlyRecurring | dateRange
 "effectiveStartTime":"2026-11-08",
 "effectiveEndTime":"2026-12-08"}
→ {"code":0,"msg":"新增模板:933343","data":"933343"}     ← data 即 templateId
```
- HOUR 模式额外带 `portfolioHourRuleList`（24h 拖拽格数据）和 `childrenItemType`，
  `type` 取自小时组件（拖拽格 UI 无法用 CDP 自动化，但接口可直接构造）
- 仅保存 = 只调 insertTemplate；**保存并应用** = insertTemplate 后再调：

```
POST .../api/sellfox/strategy/schedule/submitStrategy     （JSON）
{"templateId":"933343","itemType":"PORTFOLIO",
 "adStrategyStatusVoList":[{"policy":"noBudget","itemId":"<组合portfolioId>",
                            "shopId":"711811","marketplaceId":"A1VC38T7YXB528"}]}
```

### 20.3 读 / 删（已实测）

```
POST .../api/sellfox/strategy/getPageTemplate     （JSON）
{"shopId":"711811","itemType":"PORTFOLIO","pageNo":1,"pageSize":20}
→ rows[{id, templateName, type, effectiveStartTime/EndTime,
        effectiveStatus:"NOT_STARTED"|..., usageAmount, ...}]

POST .../api/sellfox/strategy/deleteTemplate     （JSON）
{"templateId":"933343"}     → {"code":0}
```

### 20.4 相关接口全景（bundle.4319，同一策略体系）

| 类别 | 路径（gw 前缀 `.../api/sellfox/`） |
|---|---|
| 模板 | `strategy/insertTemplate` `updateTemplate` `deleteTemplate` `getPageTemplate` `independentPageTemplate` `updateTemplateStatus` `templateExport` `templateNumForCreateOnly` |
| 应用/撤除 | `strategy/schedule/submitStrategy` `updateStrategy` `removeStrategy` `transferStrategy` `updateStrategyStatus` |
| 独立页 | `strategy/schedule/independentPageQuery` `independentCampaign(Space)` `independentTarget` `independentUpdate*` |
| 预览 | `strategy/schedule/executePreview` `queryCampaignPreview` `queryStartStopPreview` `queryTargetPreview` 等 |
| 受控对象 | `strategy/schedule/pagePortfolioList` `pageCampaignList` `pageAdGroupList` `pageTargetList` `pageProductList` `pageSpaceList` |
| 任务 | `strategy/task/pageTaskRecord` `processTask` `queryTaskHourglass` |
| Tab 状态 | `strategy/tabStatus`（`{shopId, itemId, level, adGroupId}`） |

### 20.5 UI 坑

- 该页弹窗的按钮（保存/取消）在 CDP 下**均无法触发**（DOM click、真实鼠标、
  强制显隐都无效），与广告位页「调整」下拉同属一类问题——
  但接口可直接调用，绕过 UI 即可
- 弹窗校验：名称 + 生效日期必填；HOUR 模式还需至少一个时段有预算值
  （`validateForm` 里 scheduleData 为空会静默 return，无错误提示）
- 时间格是拖拽绘制组件（`scheduleColumn` + `data-coordinate`），非标准输入框

## 21. 手动广告创建（SP 手动投放）

**页面**：`amzup-web-main/amzup-web-cpc-vue3?…%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html%3FshopIdList%3D711811%26adType%3Dsp…%26portfolioId%3D…`
新版单页创建器（活动 / 广告组同一页面填完再提交）。

### 21.1 页面表单结构（实测）

| 区块 | 字段 |
|---|---|
| 广告类型 | 商品推广(SP) / 品牌推广(SB) / 展示型推广(SD) / 流媒体电视(ST) |
| 活动 | 店铺、广告活动名称、广告组合、开始/结束日期、每日预算 |
| **定向策略** | **自动投放** / **手动投放**（手动时有「关键词投放 / 商品投放」面板切换） |
| 竞价策略 | 动态竞价-提高和降低(`autoForSales`) / 动态竞价-只降低(`legacyForSales`) / 固定竞价(`manual`) |
| 站点 | 亚马逊及其他网站(`amazonAll`) / 亚马逊企业购 |
| 按广告位调整竞价 | 搜索结果顶部 / 其余位置 / 商品详情页 / 企业购（%） |
| AMC 自定义受众 | audienceId + 加价比例 |
| 广告组 | 广告组名称、广告组默认竞价、添加推广的广告产品（搜索 / 输入列表 / 本页 / 变体） |

### 21.2 创建接口（复用 ad/sp/create，已实测）

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/ad/sp/create   （JSON）
```
灰度分支：开关 `useSpCreateOrCopyAds` 打开时走 `ad/sp/createAdsNew`（若 404 说明未灰度，用 `create`）。

**手动关键词投放请求体（实测成功）**

```json
{
  "shopId": "711811",
  "marketplaceId": "A1VC38T7YXB528",
  "spCampaignInfo": {
    "campaignId": "", "startDateStr": "2026-11-08", "endDateStr": "2026-12-08",
    "dailyBudget": "300", "targetingType": "manual", "strategy": "autoForSales",
    "placementProductPage": null, "placementTop": null, "placementRestOfSearch": null,
    "placementSiteAmazonBusiness": null, "creativeBidPercentage": null,
    "campaignSite": "amazonAll", "name": "CA_manual_test_1107",
    "portfolioId": "29811114365267", "audienceId": "", "audienceBidPercentage": "",
    "defaultAudience": false
  },
  "spGroupInfo": {
    "groupId": "", "defaultBid": "20", "name": "GR_manual_test",
    "products": [{"id": 56628535, "sku": "abc005-A-10個セット", "asin": "B0HHWBRQNC",
                  "mainImage": "", "marketplaceId": "A1VC38T7YXB528",
                  "domain": "www.amazon.co.jp", "spotlightVideos": null}]
  },
  "spTargetInfo": {
    "autoTarget": false, "keywordTarget": true,
    "keywords": [{"keywordText": "dog cage", "matchType": "exact", "bid": "15"}],
    "neKeywords": [], "neTargetings": []
  }
}
```

**响应（实测）**

```json
{"code":0,"data":{
  "campaignResp":{"code":0,"campaignId":"151381955464935"},
  "groupResp":{"code":0,"adGroupId":"118965466955231"},
  "targetResp":{"code":0,"targetType":"keywordTarget",
                "targetList":[{"code":0,"index":0,"targetId":"178979189835407"}]},
  "neKeywordResp":{"code":0},"neTargetResp":{"code":0}}}
```

- 读回 `getAllCampaignData`：`targetingType=manual`、`campaignTargetingType=manual`、
  `portfolioName=自动-当前在跑`、`dailyBudget=300.0000`、`state=enabled` ✅
- 关键差异（相对自动投放）：`targetingType: "manual"`、`autoTarget: false`、
  `keywordTarget: true`、`keywords[]` 非空；自动投放的 4 个匹配位 Bid 字段不需要
- **商品投放变体**：`keywordTarget: false` + `targetings:[{asin, ...}]`（对应页面「商品投放」面板）

### 21.2.1 UI 抓包补充（真实提交，19KB 请求体）

一次 UI 提交实测：**42 个产品 + 64 个关键词**，请求体 19,037 字节，耗时约 2s。

**① 关键词对象真实字段**

```json
{"keywordText":"キッチン ラック","keywordTextCn":"厨房架","matchType":"broad",
 "isAdd":true,"bid":"30","suggested":"77.0","rangeStart":"39.0","rangeEnd":"96.0",
 "_X_ROW_KEY":"row_2368"}
```
- `matchType` 取值：`broad` / `phrase` / `exact` / **`theme`**
- `suggested/rangeStart/rangeEnd`（建议竞价区间）、`isAdd`、`_X_ROW_KEY` 为前端透传字段
- **`matchType: "theme"`（新发现）**：亚马逊「关键词群组」投放，`keywordText` 是特殊标记：

| keywordText | keywordTextCn |
|---|---|
| `keyword-group="category"` | 与您的商品类别相关的关键词 |
| `keyword-group="peerbrand"` | 顾客还会考虑品牌关键词 |
| `keyword-group="gift"` | 与礼物相关的关键词 |
| `keyword-group="brand"` | 与您的品牌相关的关键词 |

theme 项额外带 `legacyKeywordTextCn`、`tips`（长文案说明）。实测 4 个 theme + 20 broad +
20 phrase + 20 exact = 64 个一次提交。

**theme 也会生成 target，只是排在响应末尾（修正早期误判）**：第二次实测
4 theme + 6 broad + 6 phrase + 6 exact = 22 个关键词 → `targetList` **返回 22 条、全部 code:0**，
但 `index` 顺序是 `[4..21, 0, 1, 2, 3]`——非 theme 在前、theme 4 条追加在尾部。
所以按 `index` 回填 targetId 是对的，**不要按数组位置**。

**② 手动投放仍带自动 4 位字段**

`spTargetInfo` 里即使 `autoTarget:false`、`keywordTarget:true`，仍会带：

```json
{"queryHighRelMatchesState":true,"queryHighRelMatchesBid":"",
 "queryBroadRelMatchesState":true,"queryBroadRelMatchesBid":"",
 "asinSubstituteRelatedState":true,"asinSubstituteRelatedBid":"",
 "asinAccessoryRelatedState":true,"asinAccessoryRelatedBid":""}
```
即 State 全 true、Bid 全空串（不按匹配位出价）。

**③ 其它细节**

- `spCampaignInfo` 里 4 个广告位加价是**字符串**（`"placementTop":"12"`），
  与 `spCampaign/update` 要求的**数字**不同（见第 7 节对比表）
- `dailyBudget` 也是字符串（`"333"`）
- `products[]` 只需 `{id, sku, asin, mainImage, spotlightVideos}`，
  不需要 `marketplaceId/domain`（可一次挂 42 个产品）
- `spGroupInfo` 仍然是**对象**，一次请求只建 1 个组
- 创建成功后前端**停留在原页并刷新列表**（不跳转），伴随
  `campaign/getAllCampaignData` 等列表请求

**③₊ 第三次抓包补充（否定关键词 + 部分广告位）**

实测一次含 4 theme + 6 broad + 6 phrase + 6 exact（共 22 个关键词）+ 4 个产品 + 2 个否定词：

- `neKeywords` 结构（确认）：
  `[{"keywordText":"b0hqgdv8ny","matchType":"negativeExact","index":0,"campaignId":""}]`
  响应回 `neKeywordResp.neKeywords[{index, neKeywordTargetId}]`
- `placementSiteAmazonBusiness` **允许为 `null`**（4 个广告位不要求全填）
- 关键词 `bid` 是**字符串小数**（如 `"61.0"`），theme 的 bid 也可单独设（如 `"33"`）

**④ 本次 UI 实测结果**

| 项 | 值 |
|---|---|
| 活动 | `手动测试`，campaignId `118611383747187`（内部 14315373292） |
| 广告组 | `手工测试2`，adGroupId `42357841920716`（内部 12733513784），默认竞价 22 |
| 组合 | 技术测试组合（页面里手动选的，与 URL 的 portfolioId 无关） |
| 预算/日期 | ¥333/天，2026-11-10 ~ 2026-11-13 |
| 读数回验 | `targetingType=manual`、`state=enabled`、`servingStatus=尚未开始` ✅ |

### 21.2.2 商品投放模式（UI 实测，第二次创建）

同页切到「商品投放」面板（`AddProductList`）后创建，接口仍是 **`ad/sp/create`**（同一端点）。

- `spTargetInfo.keywordTarget = false`，`targetings[]` 承载商品/品类定向项
- 定向项可用识别字段：`type`（`asin` / 品类 / 品牌等）、`asin` / `category` / `categoryId`、
  `bid`、`suggested`、`rangeStart`、`rangeEnd`
- 响应 `targetResp.targetType = "productTarget"`，每个 target 带 `targetText` + `matchType`，
  前端用 `(type, text)` 二元组回填到对应定向行的 `targetId`（见 `du()` 逻辑）
- **品类定向行允许竞价留空**（UI 显示 `- - -`），仍能提交成功
- 实测这次提交了 **40 行定向**（3 行 ASIN：B0HHF13X1G / B0HHQJFM2C / B0HHQTQSKG，
  其余为品类定向）+ 2 个否定商品（B0HHWBVHKD / B0HHWJBL24）

**本次创建结果**

| 项 | 值 |
|---|---|
| 活动 | `手动testttttt`，campaignId `113227129783425`（内部 14315925296） |
| 组合 | 技术测试组合 |
| 预算/日期 | ¥333/天，2026-11-17 ~ |
| 读数回验 | `targetingType=manual`、`state=enabled` ✅ |

⚠️ 本次请求体（约 40 行定向）因页面提交后立即跳转，CDP 会话关闭前未保存，
`targetings[]` 的完整字段以源码 `lu()`/`du()` 逻辑与上述识别字段为准。

### 21.3 关键词相关写接口（源码整理，未逐个实测）

| 功能 | 路径（前缀 `.../api/sellfox/`） |
|---|---|
| 新增关键词 | `spKeyword/createKeywords`、`spKeyword/asyncCreateKeywords` |
| 改竞价 | `spKeyword/updateBid` |
| 暂停/启用 | `spKeyword/updateState` |
| 批量改（单店/多店） | `spKeyword/updateBatch`、`spKeyword/updateBatchMultiShop` |
| 归档 | `spKeyword/archive` |
| 建议竞价 | `spKeyword/getSuggestedBid`、`getSuggestedBidByText`、`getSuggestedBidMultiShop`、`batchGetSuggestedBid` |
| 关键词推荐 | `spKeyword/suggestKeywords`、`suggestKeywordsByAsin` |

**关键词列表读取接口暂未定位**（`multiple/keyword/getAllKeywordData` 等 5 种路径均 404），
关键词模块（关键词投放 tab）待后续专项分析。

### 21.4 创建页 UI 坑

- 页面内容在 `wujie-app` shadow DOM 的 `body` 里，`shadowRoot.innerText` 为空，
  必须取 `shadowRoot.querySelector('body').innerText`（约 18K 字符）才能读到表单
- 页面 chunk：`ad-create-*.js`（表单）、`submit-ad-create-*.js`（payload 构造 + 提交）、
  `use-ad-create-steps-anchor-*.js`（步骤控制）
- payload 构造函数 `lu()`：`spTargetInfo.keywords` = `keywordsVo` 里
  `targetId` 为空的项（已存在的关键词不带）；`neKeywords/neTargetings` 同理
- **「创建」按钮必须用真实鼠标事件**（`Input.dispatchMouseEvent` 的
  mouseMoved/mousePressed/mouseReleased 三连），`btn.click()` 在商品投放模式下
  **不触发**提交（无请求、无报错、无 toast，容易误判为"校验失败"）。
  关键词模式那次 `btn.click()` 能成功，两种模式行为不一致，稳妥做法一律用真实鼠标
- shadow 内命中测试要用 `shadowRoot.elementFromPoint(x,y)`（`document.elementFromPoint`
  只会返回 `WUJIE-APP` 宿主元素，看起来像"点不到按钮"）
- 提交后页面立即跳转到广告管理列表（`ds.json` → `ads-management`），
  抓包脚本必须在 click 后立即读取 response body，否则会话关闭后拿不到请求体

## 22. 关键词数据：ABA 搜索词排名 / 建议词 / 建议价

> 来源页：`cpc-vue3/web/cpc-vue3/ad-create/index.html`（手动广告 → 关键词投放 → 建议）
> 前置条件：**必须先添加一个广告产品（ASIN）**，否则接口不返回任何关键词与排名。

### 22.1 三个接口总览

| 用途 | 接口 | 方法 | 备注 |
|---|---|---|---|
| **ABA 搜索词排名（含历史趋势）** | `/api/search/terms/analysis/getTrend.json` | POST | ⭐核心；老路径，**不是** `keywordLib/searchTermGetTrend` |
| 建议关键词（按 ASIN） | `.../api/sellfox/adKeyword/sp/suggestKeywordsByAsin/page` | POST | 返回关键词文本 + 匹配类型，**不含排名** |
| 推荐建议竞价 | `.../api/sellfox/multiple/target/suggestBid/recommendations` | POST | 按 ASIN + 投放表达式算建议价 |

> ⚠️ 常见误区：`/api/gw/sellfox/sellfox-cpc/api/sellfox/keywordLib/searchTermGetTrend`
> 这个新网关路径**始终返回 `data: []`**（即使参数齐全），是废弃/未接通的入口。
> 真正的数据在老的 `/api/search/terms/analysis/getTrend.json`。

### 22.2 排名趋势 `getTrend.json`（⭐最有用）

**请求**

```json
POST /api/search/terms/analysis/getTrend.json
{
  "marketplaceId": "A1VC38T7YXB528",
  "searchTerms": "キッチン ラック######収納 ラック######チェストリグ",
  "periodType": "WEEK",
  "startDate": "",
  "endDate": "",
  "departmentNames": "Amazon.co.jp######Amazon.co.jp######Amazon.co.jp"
}
```

**三个必踩的坑**

1. **多词用 `######`（6 个 #）连接**，不是逗号、不是数组
2. **`departmentNames` 必填，且必须与 `searchTerms` 数量一一对应**（同样用 `######` 连接）。
   站点域名形如 `Amazon.co.jp` / `Amazon.com`（由 marketplaceId 映射得来）。**少了这个参数就返回空**
3. `startDate` / `endDate` 传**空串**（趋势区间由 `periodType` 决定），不要传具体日期

**periodType 枚举**：`DAY` / `WEEK` / `MONTH` / `QUARTER`（前端默认 `WEEK`）

**响应**

```json
{
  "code": 0, "msg": "OK",
  "data": [
    {
      "departmentName": "Amazon.co.jp",
      "marketplaceId": "A1VC38T7YXB528",
      "searchTerm": "チェストリグ",
      "createTime": null,
      "rank": null,
      "abaRank": "11000",                 // 当前排名 = list 最后一期的 searchFrequencyRank
      "list": [
        {"startDate":"2026-08-02","endDate":"2026-08-08",
         "searchFrequencyRank":15007,"weekStr":"26年第32周"},
        {"startDate":"2026-08-09","endDate":"2026-08-15",
         "searchFrequencyRank":15231,"weekStr":"26年第33周"}
      ]
    }
  ]
}
```

| 字段 | 含义 |
|---|---|
| `abaRank` | 最新一期排名，**数字越小越热门** |
| `list[]` | 历史趋势：周=13 期、月=12 期 |
| `list[].searchFrequencyRank` | 该期的搜索频次排名 |
| `list[].weekStr` | 周维度才有（如 `26年第44周`）；月维度用 `26年10月` |
| `rank` | 另一套排名（自然位），此处常为 null |

**注意**：没有 ABA 记录的长尾词**不会出现在 `data` 里**（不是报错，是直接不返回）。

### 22.3 建议关键词 `suggestKeywordsByAsin/page`

```json
POST /api/gw/sellfox/sellfox-cpc/api/sellfox/adKeyword/sp/suggestKeywordsByAsin/page
{"shopId":"711811","asinList":["B0HQGMTCB3","B0HQRVCQ88", "...最多 20 个"],"pageNo":1,"pageSize":20}
```

响应：`data.keywordTypeList[{matchType, keywordText, keywordTextCn}]`，`data.totalSize` 实测 200。

> 该接口**只给词、不给排名**。排名要拿这些词去调 22.2 的 `getTrend` 补。
> 参数校验有个易错点：字段名是 `asinList`（传 `asins` 会报 `asinList: must not be null`）。

### 22.4 推荐建议竞价 `suggestBid/recommendations`

```json
POST /api/gw/sellfox/sellfox-cpc/api/sellfox/multiple/target/suggestBid/recommendations
{
  "shopId":"711811",
  "asins":["B0HQGMTCB3", "..."],
  "placementProductPage": null, "placementTop": null, "placementRestOfSearch": null,
  "biddingStrategy": "AUTO_FOR_SALES",
  "targetingExpressions": [
    {"type":"KEYWORD_GROUP","value":"keyword-group=\"category\""},
    {"type":"KEYWORD_GROUP","value":"keyword-group=\"peerbrand\""},
    {"type":"KEYWORD_GROUP","value":"keyword-group=\"gift\""},
    {"type":"KEYWORD_GROUP","value":"keyword-group=\"brand\""}
  ]
}
```

- `targetingExpressions` 就是建活动时那 4 个 theme 群组，用于算「主题词」的建议价
- `biddingStrategy` 与活动策略一致（`AUTO_FOR_SALES` / `LEGACY_FOR_SALES` / `MANUAL`）
- 相比单投放的 `spTarget/getSuggestedBid`，这个接口是**按 ASIN 批量**给建议价，适合建活动前批量估价

### 22.5 前端如何决定「搜索词排名」列显示什么

`submit-ad-create-*.js` 里的取值优先级：

```js
function Po(row, trend, source) {
  const r = No(trend?.abaRank);
  return r === null
    ? (source === KEYWORD_LIBRARY
        ? No(row.searchFrequencyRank) ?? No(trend?.searchFrequencyRank)
        : No(trend?.rank))
    : r;
}
```

| 优先级 | 字段 | 来源 |
|---|---|---|
| 1 | `abaRank` | getTrend 返回 |
| 2 | `searchFrequencyRank` | 词库页签行 / getTrend 行 |
| 3 | `rank` | 其他页签行 |
| 4 | 无 → 显示 `-` | |

周期下拉（按日/按周/月度/季度）**只改前端状态**，不发请求；列头随之变成
`ABA搜索词排名-按周` / `-月度` 等。

### 22.6 实测数据（店铺 711811，2026-11-07）

| 搜索词 | 周排名（26 年第 44 周） | 近 4 周走势 | 10 月月排名 |
|---|---|---|---|
| チェストリグ | **11,000** | 11,260 → 14,868 → 12,509 → 11,000 | 12,285 |
| プレートキャリア | **31,054** | 29,168 → 42,019 → 32,605 → 31,054 | 32,741 |
| サスペンダー 作業用 | **54,376** | 85,575 → 108,543 → 87,304 → 54,376 | 79,261 |
| ラジオハーネス | **275,361** | 318,554 → 352,684 → 306,966 → 275,361 | 无 |
| タクティカル チェストリグ | 无记录 | — | 无 |
| チェストリグ パネル | 无记录 | — | 无 |

> 排名是「全站搜索频次排名」，**不是搜索量**。要换算月搜索量需 ABA 后台导出或第三方工具，
> 赛狐这条链路里没有搜索量字段。

### 22.7 抓包方法（这个页面的坑）

`page.on("request")` 在本页面**抓不到**关键词相关请求（页面注册了 service worker，
请求不在 page 级事件流里）。可行做法是**浏览器级 CDP 自动附加所有 target**：

```python
# 连浏览器级 ws（/json/version → webSocketDebuggerUrl）
# 1) Target.setAutoAttach({autoAttach:True, waitForDebuggerOnStart:False, flatten:True})
# 2) 收到 Target.attachedToTarget → 对每个 sessionId 发 Network.enable
#    （会附加 page / shared_worker / service_worker 三类）
# 3) 用 Playwright 真实鼠标点击「添加」按钮 → 在 target 会话里收 Network.requestWillBeSent
```

另外：`button` 选择器取不到「添加」按钮（实际是 **`<span>`**），
Playwright 的 `fr.locator("button:has-text('添加')")` 返回 0，必须用坐标点击。

## 附：相关文件

| 文件 | 说明 | 是否入库 |
|---|---|---|
| `docs/samples/sp_create_request.json` | 创建请求样例 | 是 |
| `docs/samples/sp_create_response.json` | 创建响应样例 | 是 |
| `docs/samples/sp_campaign_update_request.json` | 修改活动请求样例 | 是 |
| `docs/samples/sp_add_group_request.json` | 添加广告组请求样例 | 是 |
| `/tmp/cdp_save_realclick.py` | 真实鼠标点击「保存」并抓包 | 否（临时） |
| `/tmp/save_capture2.json` | 保存时的完整 XHR 记录 | 否（临时） |
| `explore/api_contracts.json` → `创建SP广告活动(写)` | 结构化契约（含字段说明） | 否（`.gitignore`） |
| `/tmp/create_capture.json` | 原始事件（含全部请求头/响应体） | 否（临时） |
| `/tmp/cdp_click_create.py` | 点击并抓包脚本 | 否（临时） |
