---
name: sellfox-ads-query
description: 只读查询赛狐后台的亚马逊 SP 广告现状，包括活动列表与详情、广告组、投放与竞价、否定投放、广告位比例、广告产品、ABA 搜索词排名、建议关键词、建议竞价。当用户问"现在有哪些活动""这个组投了什么""预算多少""ABA 排名怎么样""建议出价多少""为什么没曝光"这类需要看后台真实数据的问题时使用。本技能绝不修改任何数据。
---

# 赛狐 SP 广告 · 只读查询

## 何时使用

用户想**看现状**时：活动有哪些、预算是多少、组里投了什么词、竞价多少、否定词有哪些、广告位加价多少、某个 ASIN 的 ABA 排名和建议竞价。

## 何时不用

- 要改预算、改竞价、暂停启用、加否定、建活动 → 用 `sellfox-ads-exec`
- 问的是亚马逊官方后台（Seller Central / 广告后台），不是赛狐 → 不用本技能
- 账户申诉、Listing 优化、库存 → 与广告数据无关

## 前置条件

鉴权只靠登录 Cookie（含 `sf_u`），**不需要** `sf-vvv-*` 签名头。

- Cookie 过期时接口返回 `code: 2001` → 提示用户重新在浏览器登录，不要自己反复重试登录
- Cookie 从本地调试浏览器读取，路径由 `scripts/sf.py` 处理，不要手工拼接

执行器统一用这个解释器（已确认装了 `requests`）：

```
/usr/local/bin/python3
```

WorkBuddy 自带的 `python3` 没有 `requests`，不要用它。若换过环境，先跑 `python3 -c "import requests"` 确认。

## 命令

全部输出 JSON 到 stdout。`--help` 可查参数。

| 命令 | 作用 |
|---|---|
| `scripts/sf.py campaigns --status enabled` | 活动列表，可按状态、店铺筛 |
| `scripts/sf.py campaign --name "CA_xxx"` | 单个活动详情（预算、策略、广告位比例、组合） |
| `scripts/sf.py groups --campaign "CA_xxx"` | 活动下的广告组 |
| `scripts/sf.py targets --campaign "CA_xxx" --group "GR_xxx"` | 组内投放与竞价 |
| `scripts/sf.py negatives --campaign "CA_xxx"` | 否定关键词 / 否定商品 |
| `scripts/sf.py placements --campaign "CA_xxx"` | 四个广告位（搜索结果顶部 / 其余 / 商品页 / 企业购） |
| `scripts/sf.py products --campaign "CA_xxx"` | 活动下的广告产品 |
| `scripts/sf.py aba --asin B0XXXXXXXX --days 30` | ABA 搜索词排名趋势 |
| `scripts/sf.py suggest-keywords --asin B0XXXXXXXX` | 系统建议关键词 |
| `scripts/sf.py suggest-bid --asin B0XXXXXXXX --keyword "木製 収納"` | 建议竞价区间 |

活动一律用**名称**指定，不要向用户要内部 id。名称查不到或有重名时，先把候选列出来让用户选，**不要猜**。

## 输出约定

- 单条记录也返回数组，便于统一处理
- 金额单位日元，为字符串原样返回，不做四舍五入
- 时间为 `yyyy-mm-dd`
- 查不到时返回 `{"found": false, "candidates": [...]}`，不是空数组

## 已知坑（读数据时必须避开）

1. **两套 id 不能混**：`campaignId` 是亚马逊活动 id，`dxmCampaignId` 是赛狐内部 id，同名不同物。查询结果里两个都返回时，要注明哪个是哪个，不要让用户拿走错的那个去做写操作。
2. **广告位是 4 行同一个活动**：搜索结果顶部、搜索结果其余、商品详情页、企业购。汇总时不要当成 4 个活动。
3. **搜索词排名一列可能为空**：ABA 数据按 ASIN 缓存，新 ASIN 或冷门词可能没有排名，空值表示"未收录"，不是 0 名。
4. **搜索词报告只有过点击的词**：亚马逊侧的口径，零点击搜索词不会出现。用户问"为什么有曝光没搜索词"时，直接说明这条规则，不要去找不存在的记录。

## 回答方式

先给数字，再给判断。用户要的是"现在是什么情况"，不是"我查了哪些接口"。

不要复述 JSON。把关键字段挑出来讲：活动名、预算、曝光、点击、花费、ACOS、四个广告位比例。

数据有缺口时明说缺哪一块，不要用推测填。
