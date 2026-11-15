---
name: sellfox-ads-exec
description: 对赛狐后台的亚马逊 SP 广告执行写操作，包括改预算、改竞价、暂停/启用活动或广告组、调整广告位加价、添加否定关键词或否定商品、修改广告组默认竞价、新建广告组、按计划文件新建广告活动、活动移入组合。当用户明确要求"改""调""暂停""加否定""新建"广告时使用。所有写操作默认只输出变更预览，必须用户确认后才带 --apply 真执行。
---

# 赛狐 SP 广告 · 写操作执行

## 解释器

所有命令用 `/usr/local/bin/python3` 执行（已确认装了 `requests`）。WorkBuddy 自带的 `python3` 缺依赖，会直接报错。

## 五条铁律（任何情况下不得绕过）

1. **默认 dry-run。** 不带 `--apply` 只输出"将要改什么"，不发请求。必须用户明确确认后才能带 `--apply`。
2. **先读后写。** 修改接口是**全量覆盖**：先 GET 当前完整值，只替换目标字段，其余原样回传。漏传 `portfolioId` 就会把活动从组合里踢出去，漏传广告位比例就会清零。
3. **不许构造 id。** 一律用名称定位，`name → id` 由脚本解析。查不到或有重名就停下报告，**绝不用"名字最像的那个"代替**。
4. **写完必须读回。** 调用成功后立刻 GET 比对目标字段，不一致就报"写入未生效"，不要直接说"已完成"。
5. **出错立即停。** 不重试风暴，不批量继续。一次失败就中断整批并报告已完成的部分。

## 动作与命令

| 用户意图 | 命令 | 风险 |
|---|---|---|
| 改活动预算 | `scripts/sf.py set-budget --campaign "CA_xxx" --amount 300` | 中 |
| 改竞价 | `scripts/sf.py set-bid --campaign "CA_xxx" --group "GR_xxx" --target queryHighRelMatches --bid 18` | 中 |
| 暂停/启用活动 | `scripts/sf.py set-state --campaign "CA_xxx" --state paused` | 中 |
| 暂停/启用广告组 | `scripts/sf.py set-state --campaign "CA_xxx" --group "GR_xxx" --state paused` | 中 |
| 改广告组默认竞价 | `scripts/sf.py set-group --campaign "CA_xxx" --group "GR_xxx" --default-bid 30` | 中 |
| 调广告位 | `scripts/sf.py set-placement --campaign "CA_xxx" --top 20 --rest 10 --product 30 --business 0` | 中 |
| 加否定词/否定 ASIN | `scripts/sf.py add-negative --campaign "CA_xxx" --group "GR_xxx" --keywords "a,b" --asins "B0X,B0Y"` | 中 |
| 新建广告组 | `scripts/sf.py create-group --campaign "CA_xxx" --plan plan.json` | 高 |
| 新建广告活动 | `scripts/sf.py create-campaign --plan plan.json` | 高 |
| 活动移入组合 | `scripts/sf.py move-portfolio --campaign "CA_xxx" --portfolio "技术测试组合"` | 高 |
| 归档活动 | 不提供。人工在赛狐页面操作 | 禁止 |

`set-bid` 的 `--target` 取值：`queryHighRelMatches`（紧密匹配）、`queryBroadRelMatches`（宽泛匹配）、`asinSubstituteRelated`（同类商品）、`asinAccessoryRelated`（关联商品）。

## 执行流程（照做，不要跳步）

1. **解析对象**：按名称查出唯一 id。多个候选 → 列出来问用户。
2. **读取现状**：拿到修改前完整快照，保存到 `data/snapshots/<时间戳>_<对象>.json`。
3. **输出预览**：用表格列出"字段 / 当前值 / 目标值"，等用户确认。
4. **执行**：带 `--apply` 调用。
5. **读回校验**：重新 GET，逐字段比对。
6. **报告**：成功说成功了什么；失败说失败在哪一步、当前处于什么状态、怎么回滚。

中间任何一步用户没说"确认/可以/执行"，就停在预览。

## 新建活动（最高风险）

- **只接受 plan 文件**，不接受对话里临时给的参数。plan 结构见 `docs/plan_2026-11-09_ads_adjust.md`。
- 创建**非幂等**，每次调用都真实新建活动 + 广告组 + 4 个自动投放，重试一次就多一套。
- 创建间隔 **3–5 秒**，连续创建会触发风控。
- 响应要看**子模块**：根 `code=0` 不代表成功，必须逐个检查 `campaignResp.code`、`groupResp.code`、`targetResp.targetList[].code`。
- **已知的部分成功风险**：`campaignResp` 成功但 `groupResp` 失败时，会留下一个没有广告组的空活动。遇到这种情况必须报告"活动 XXX 已创建但广告组失败"，并给出清理建议，不要说"创建失败"。
- 创建完成后接口不返回最终状态，要再查一次列表确认状态。

## 禁止事项

- 不主动归档、不删除任何对象，除非用户在当次对话中明确要求并二次确认。
- 不在用户未确认的情况下连续执行多个写操作。
- 不用写操作"试探"接口是否可用；要试就用 `campaigns` 这类读接口。
- `code: 2001` = Cookie 过期，停下来让用户重新登录，不要循环重试。

## 报告口径

失败时同时说清楚三件事：**哪一步失败、系统当前处于什么状态、下一步建议做什么**。

只报"执行失败"等于没报——用户无法判断广告现在是停了、改了、还是建了一半。
