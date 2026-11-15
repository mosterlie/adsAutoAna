# 11-09 广告调整预案（待复核，尚未执行）

> 依据：2026-11-08 实时取数归因（`explore/analysis/2026-11-08_attribution/`）
> 店铺：711811 金梧汇辰 / Amazon.co.jp
> 状态：**本文件只是预案。截至生成时，未向赛狐发出任何写请求，账户零变更。**
> 执行器：根目录 `plan_20261109_ads_adjust.py`（默认 dry-run）
> 预演结果：2026-11-09 已跑通全量预演，18 步 / **54 个写请求**，前值校验全部通过。

---

## 复核清单（请逐条打勾，任何一条不同意就告诉我改）

- [ ] **A 组方向**：两个出单活动提价 30%（20→26、30→39），商品页加价 0→20，预算不动
- [ ] **A 组幅度**：+30% 是否够/太多（我可以改成 +20% 或 +50%）
- [ ] **B 组必要性**：现在建手动竞投，还是先等 A 组跑 3 天再说
- [ ] **B 组出价**：排水沟 35、防撞角 40 是否接受
- [ ] **B 组预算**：各 100 JPY/天是否合适
- [ ] **B 组风险**：接受"`targetings[]` 字段靠推断、可能第一次提交失败"这个风险
- [ ] **C 组名单**：这 10 个活动降 25%，有没有你不想动的
- [ ] **C 组幅度**：−25% 是否够狠（也可以直接暂停其中几个）
- [ ] **撤回否词**：同意不否 `チェストリグ` / `ピックルボール セット`（理由见第 5 节）
- [ ] **执行顺序**：先 A+C，24 小时后再 B

复核通过后执行：
```bash
python3 plan_20261109_ads_adjust.py --apply --only A,C   # 第一批
python3 plan_20261109_ads_adjust.py --apply --only B     # 24 小时后
```

---

## 0. 已读到的当前真实值（只读采集，`explore/analysis/plan_state.json`）

| 活动 | 亚马逊 campaignId | 内部 dxmCampaignId | 组 | 组默认竞价 | 4 位实际出价 | 商品页加价 | 预算 |
|---|---|---|---|---|---|---|---|
| CA_排水沟_B0HJCLZPM4 | 90709676806138 | 13804946433 | 11818153634 | 20.0 | 20.0 ×4 | 0 | 200 |
| CA_W型防撞角_B0HJ274S14 | 113908165179926 | 13804825329 | 11817682331 | 30.0 | 30.0 ×4 | 0 | 300 |

⚠️ **关键发现（影响指令正确性）**：自动广告有 4 个匹配位，各自有独立出价，
**组默认竞价不一定等于实际出价**。实测：

| 活动 | 组默认竞价 | 匹配位实际出价 | 结论 |
|---|---|---|---|
| CA_匹克球网 | 13.0 | **20.0** | 不一致 |
| CA_旅行用背包 | 25.0 | **30.0** | 不一致 |
| CA_战术胸挂 | 25.0 | **30.0** | 不一致 |
| 其余 7 个 | 20/30 | 同默认 | 一致 |

所以 **降价必须改匹配位出价（`spTarget/updateBid`），改组默认竞价无效**。
下面 C 组的"前值"一律用匹配位实际出价。

---

## 1. 变更清单总览

| 编号 | 对象 | 字段 | 前值 | 后值 | 变化 |
|---|---|---|---|---|---|
| A1 | 排水沟 组 | 默认竞价 | 20 | **26** | +30% |
| A2 | 排水沟 4 个匹配位 | bid | 20 | **26** | +30% |
| A3 | 排水沟 活动 | 商品页加价 | 0 | **20** | +20pt |
| A4 | 防撞角 组 | 默认竞价 | 30 | **39** | +30% |
| A5 | 防撞角 4 个匹配位 | bid | 30 | **39** | +30% |
| A6 | 防撞角 活动 | 商品页加价 | 0 | **20** | +20pt |
| B1 | 新建手动活动（排水沟） | — | — | 竞投 B0GZJR2TVF | 新增 |
| B2 | 新建手动活动（防撞角） | — | — | 竞投 B0HFNS6DS9 | 新增 |
| C1–C10 | 10 个零转化活动 | 4 个匹配位 bid | 20/30 | **15/22** | −25% |

预算、策略、状态：**一律不动**。

---

## 2. A 组：提高两个出单活动的竞价

**依据**：两单 ACOS 分别 0.31% / 0.81%，但曝光只有 270 / 72；预算 200/300 只花了 20/30。
瓶颈是竞价（拿不到商品页曝光），不是预算。

### A1｜排水沟 组默认竞价 20 → 26

```
POST https://www.sellfox.com/api/cpcSPManage/group/update.json
Content-Type: application/json

{"shopId":"711811","dxmGroupId":11818153634,"name":"GR_排水沟_B0HJCLZPM4","defaultBid":"26.00"}
```
（该接口需带原 `name`，漏了会被清空）

### A2｜排水沟 4 个匹配位 20 → 26

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/spTarget/updateBid
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=14313810921&bid=26
shopId=711811&id=14313810920&bid=26
shopId=711811&id=14313810919&bid=26
shopId=711811&id=14313810916&bid=26
```
（`id` 是**赛狐内部 target id**，不是亚马逊 targetId）

### A3｜排水沟 商品页加价 0 → 20

```
POST https://www.sellfox.com/api/cpcSPManage/campaign/updatePlacementAdjustment.json
Content-Type: application/x-www-form-urlencoded

shopId=711811&id=13804946433&placement=placementProductPage&value=20
```
（`id` = 活动内部 id；`value` 必须是 0~900 的**整数**）
顶部/其余位**保持 0**——昨天顶部 0 曝光 0 单，不做无效加价。

### A4–A6｜防撞角 同结构

```
# A4 组默认竞价 30 → 39
{"shopId":"711811","dxmGroupId":11817682331,"name":"GR_W型防撞角_B0HJ274S14","defaultBid":"39.00"}

# A5 匹配位 30 → 39
shopId=711811&id=14313809589&bid=39
shopId=711811&id=14313809569&bid=39
shopId=711811&id=14313809588&bid=39
shopId=711811&id=14313809590&bid=39

# A6 商品页加价 0 → 20
shopId=711811&id=13804825329&placement=placementProductPage&value=20
```

---

## 3. B 组：把两个竞品页做成手动商品投放

**依据**：昨天两单 100% 从商品页进来，但那是自动广告的"偶然命中"。
抽成手动活动后可以独立控价、独立预算、独立观测。

### B1｜CA_排水沟_B0HJCLZPM4_竞投B0GZJR2TVF

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/ad/sp/create
Content-Type: application/json

{
  "shopId": "711811",
  "marketplaceId": "A1VC38T7YXB528",
  "spCampaignInfo": {
    "campaignId": "", "startDateStr": "2026-11-10", "endDateStr": "2026-12-10",
    "dailyBudget": "100", "targetingType": "manual", "strategy": "legacyForSales",
    "placementProductPage": null, "placementTop": null, "placementRestOfSearch": null,
    "placementSiteAmazonBusiness": null, "creativeBidPercentage": null,
    "campaignSite": "amazonAll", "name": "CA_排水沟_B0HJCLZPM4_竞投B0GZJR2TVF",
    "portfolioId": "-1", "audienceId": "", "audienceBidPercentage": "",
    "defaultAudience": false
  },
  "spGroupInfo": {
    "groupId": "", "defaultBid": "35", "name": "GR_竞投B0GZJR2TVF",
    "products": [{"id": 57615613, "sku": "abc033-e-a", "asin": "B0HJCCY8MS",
                  "mainImage": "https://m.media-amazon.com/images/I/71uXjOugB7L.jpg",
                  "spotlightVideos": null}]
  },
  "spTargetInfo": {
    "autoTarget": false, "keywordTarget": false,
    "keywords": [],
    "targetings": [{"type": "asin", "asin": "B0GZJR2TVF", "bid": "35", "isAdd": true}],
    "neKeywords": [], "neTargetings": []
  }
}
```

### B2｜CA_W型防撞角_B0HJ274S14_竞投B0HFNS6DS9

同上结构，替换：

| 字段 | 值 |
|---|---|
| `name` | `CA_W型防撞角_B0HJ274S14_竞投B0HFNS6DS9` |
| 组名 | `GR_竞投B0HFNS6DS9` |
| `products[0].id` | `56739636` |
| `products[0].sku` | `abc010-長さ5m-ピンク` |
| `products[0].asin` | `B0HJ2476BX` |
| `mainImage` | `https://m.media-amazon.com/images/I/41mu9upuF4L.jpg` |
| `targetings[0].asin` | `B0HFNS6DS9` |
| `bid` / 组默认 | `"40"` |

**为什么排水沟 35、防撞角 40**：这两单实际承担的 CPC 就是 20 / 30，
手动竞投位要给到高于自动的水平才抢得到位置，但不给到翻倍——新品期先测 3 天。

---

## 4. C 组：10 个零转化活动降 25%

**依据**：这 10 个活动昨天合计花掉 2,776 JPY、0 单，占全站花费 47%。
降 25% 后预计日省约 690 JPY，且不至于断流（它们本身还有曝光）。

| 编号 | 活动 | 亚马逊 campaignId | 内部 dxmCampaignId | 4 个匹配位内部 id | 前 → 后 |
|---|---|---|---|---|---|
| C1 | CA_匹克球网_B0HJPDD8XV | 57452546140024 | 13804946428 | 14313810906 / 903 / 904 / 902 | 20 → **15** |
| C2 | CA_木制猫屋_B0HKN42H6R | 169999519387603 | 13804955697 | 14313811041 / 037 / 040 / 039 | 20 → **15** |
| C3 | CA_厨房燃气灶_B0HJYG85BK | 234162204808225 | 13804946435 | 14313810895 / 893 / 907 / 892 | 20 → **15** |
| C4 | CA_壁挂式收纳壁架_B0HLDP321N | 200747849489161 | 13805071187 | 14313812087 / 088 / 077 / 086 | 30 → **22** |
| C5 | CA_旅行用背包_B0HLD2DCST | 276956384537792 | 13805071184 | 14313812075 / 051 / 074 / 049 | 30 → **22** |
| C6 | CA_战术胸挂_B0HLKZ1DLM | 88247362850188 | 13805083570 | 14313812143 / 144 / 141 / 145 | 30 → **22** |
| C7 | CA_宠物车用软笼_B0HKLS2NBP | 251746535103294 | 13804914939 | 14313810571 / 570 / 572 / 553 | 30 → **22** |
| C8 | CA_纯钛盖杯_B0HJ276G99 | 135554617647734 | 13804838324 | 14313809743 / 731 / 734 / 733 | 30 → **22** |
| C9 | CA_高尔夫练习网_B0HJFVVBHB | 69875152037809 | 13804838333 | 14313809741 / 739 / 737 / 742 | 30 → **22** |
| C10 | CA_焚火台_B0HKV72L28 | 253177867055866 | 13804926607 | 14313810700 / 681 / 699 / 705 | 30 → **22** |

每条 4 个请求，全部形如：
```
POST .../spTarget/updateBid
shopId=711811&id=<上面 4 个 id 之一>&bid=15
```

---

## 5. 我撤回的一条建议（必须说明）

原建议"先否定 `チェストリグ`、`ピックルボール セット`"——**撤回，因为它是错的**。

我把 11-08 全部 21 条搜索词逐条看了相关性：

| 搜索词 | 点击 | 花费 | 对应产品 | 相关性 |
|---|---|---|---|---|
| チェストリグ（= chest rig） | 6 | 179 | 战术胸挂 | **完全同义，核心词** |
| ピックルボール セット | 5 | 100 | 匹克球网 | 高度相关 |
| 犬 防音 ケージ | 2 | 38 | 宠物隔音室 | 高度相关 |
| キャットウォーク セット | 2 | 33 | 木制猫屋 | 高度相关 |
| 天然ヘチマたわし | 1 | 30 | 丝瓜络洗碗海绵 | 完全同义 |
| …其余 16 条 | 1–2 | 15–30 | 各自对应 | 全部相关 |

**没有一条是垃圾流量**。把这些词否掉，等于把自己最精准的流量关掉。
"相关但不转化"说明问题在**价格 / 图片 / 评论 / 配送时效**，不在词。
所以改成用 C 组降竞价控亏损，而不是否词。

---

## 6. 执行顺序与批次

| 批次 | 内容 | 请求数 | 建议间隔 |
|---|---|---|---|
| 第 1 批 | A1–A3（排水沟提价） | 6 | 2s |
| 第 2 批 | A4–A6（防撞角提价） | 6 | 2s |
| 第 3 批 | C1–C10（降零转化） | 40 | 2s |
| 第 4 批 | B1、B2（新建手动竞投） | 2 | 5s |

**先跑第 1–3 批，观察 24 小时再跑第 4 批**。理由：A/C 是改现有值，可秒回滚；
B 是新建实体，字段有一部分靠推断（见第 8 节），风险等级不同，不要混在一起。

---

## 7. 校验方法（每批执行完立刻读回）

| 改动 | 校验方式 | 期望 |
|---|---|---|
| 匹配位 bid | `multiple/target/getAllTargetData`（`targetType=auto`） | 4 个位 bid = 新值 |
| 组默认竞价 | `GET /api/cpc/group/getInfo.json?shopId=711811&campaignId=<Amazon id>&groupId=<Amazon adGroupId>&type=sp` | `defaultBid` = 新值 |
| 商品页加价 | `multiple/placement/getAllPlacementData` | `placementProductPage` 行的 percentage = 20 |
| 新建活动 | `campaign/getAllCampaignData` 按 `name` 精确搜 | 存在、`targetingType=manual`、预算/日期正确 |

任一条对不上 → 立即停止后续批次，用 `--rollback` 回滚已完成部分。

---

## 8. 风险与不确定点（复核时请重点看这 3 条）

1. **B 组的 `targetings[]` 字段是推断的**。文档 §21.2.2 明确写了"本次请求体未保存"，
   `type/asin/bid/isAdd` 来自前端源码逻辑推断，没有完整实测。
   → 建议：B 组执行前，先在赛狐 UI 手工建一个同样的手动商品投放并抓包，
   用真实请求体替换本节。或者先只跑 B1，看响应再决定 B2。
2. **`portfolioId: "-1"`（无组合）在 create 接口未实测**。update 接口已知 `-1`=无组合。
   若返回 360000/业务错误，改用 `29811114365267`（自动-当前在跑）重试。
3. **"改匹配位 bid 会不会被组默认竞价覆盖"未验证**。本预案两者都改、值一致，
   所以无论哪边生效都是 26/39，规避了该不确定性。

---

## 9. 预期效果（3 天观察口径）

- A 组：出单活动曝光应从 270 / 72 明显上升；只要 ACOS 仍在 5% 以内就算成功。
- C 组：日均花费约降 690 JPY，全站日花费从 ~5,955 降到 ~5,265。
- B 组：3 天内若各出 ≥1 单，保留并提预算；0 单则归档，不再追加。

---

## 10. 声明

本文档生成过程中，对赛狐只发起了**读请求**（活动/组/投放/广告位/否定词/产品库列表）。
**没有任何 create / update / updateBid / updateState / archive 调用。**
复核通过后再执行 `python3 plan_20261109_ads_adjust.py --apply --only A,C`。
