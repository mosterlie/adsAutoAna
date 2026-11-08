# 项目上下文速查（压缩版）

> 用途：新开会话时把本文件内容/路径提供给 AI，即可跳过历史对话。
> 最后更新：2026-11-07

## 1. 项目与运行

- 目录：`/Users/gx/Desktop/mypro/adsAutoAna`
- 服务：`python3 run.py --port 8320`（FastAPI，静态页在 `/static/`，主站在 `/`）
- 数据库：`data/ads.db`（SQLite，`config.DB_PATH`）
- 店铺：`SHOP_ID=711811`，站点 `Amazon.co.jp` / `A1VC38T7YXB528`
- 登录态：`data/cookies.json`；失效时 `backend.cookies.sync_from_browser("http://127.0.0.1:9222")`

## 2. 核心模块

| 文件 | 职责 |
|---|---|
| `backend/ads_store.py` | **广告数据仓库**：dim/fact 建表、UPSERT 写入、按活动+区间查询、迁移、校验 |
| `backend/daily_probe_api.py` | 按天全量采集（后台任务）+ 新旧查询接口 `/api/daily/*` |
| `backend/campaign_probe_api.py` | 活动级取数 + `layer_campaign_info/list` |
| `backend/daily_db.py` | 旧 `probe_*` 表读写（保留备份） |
| `backend/rename_tool.py` | 活动/组改名（规则 `CA_商品标识_父ASIN`） |
| `backend/rename_batch.py` | 批量改名执行器（进度落 `data/rename_progress.json`） |
| `backend/ai_label.py` / `translate.py` | 商品标识解析（Ollama qwen2.5:1.5b）|
| `backend/amazon.py` | 亚马逊搜索结果抓取（6s 限流 + 30min 缓存）|

前端：`frontend/daily-probe.html`（广告数据：查询/采集两页签）、`index.html`+`app.js`（在线产品/产品视角）、`hourly.html`、`api-docs.html`、`campaign-probe.html`（已从菜单下架）

## 3. 数据模型（实体中心化，2026-11-07 改造）

**维度**：`dim_campaign` / `dim_group` / `dim_product` / `dim_target` / `dim_search_term`
**事实**：`fact_campaign` / `fact_group` / `fact_product` / `fact_target` / `fact_search_term` / `fact_competitor`
**视图**：`v_campaign_daily` / `v_group_daily` / `v_product_daily` / `v_campaign_hourly`
**日志**：`fetch_log`

| 事实表 | 主键 |
|---|---|
| fact_campaign | campaign_id + date + hour + placement |
| fact_group | group_id + date + hour + placement |
| fact_product | asin + campaign_id + date + hour + **system**(total/placement/match) + code |
| fact_target | target_id + date + hour |
| fact_search_term | date + scope(campaign/group) + owner_id + query_type + term + **match_type** |
| fact_competitor | term + asin（**无日期**，全局共享） |

约定：
- `hour = -1` 当日汇总，`hour >= 0` 分时；**不能混 SUM**
- 写入一律 `INSERT … ON CONFLICT DO UPDATE`（幂等，补某天不影响其他天）
- 指标列 `impressions/clicks/cost/orders/sales/add_to_cart/dpv` 可直接 SUM；其余 60+ 在 `metrics_json`
- 竞品按词全局一份，刷新只改 `fetched_at`

## 4. 关键接口

| 接口 | 说明 |
|---|---|
| `GET /api/daily/store/campaigns` | 活动列表（含天数/区间/花费）|
| `GET /api/daily/store/dates?campaignId=` | 已落库日期 |
| `GET /api/daily/store/day?campaignId=&date=` | 单日全层级 |
| `GET /api/daily/store/range?campaignId=&start=&end=` | 区间汇总（同结构）|
| `GET /api/daily/store/trend` `hourly` `products` `parents` | 趋势/小时/产品/父体 |
| `GET /api/daily/store/competitor/terms?campaignId=&start=&end=` | 词概览 |
| `GET /api/daily/store/competitor/items?term=` | 某词全量竞品 |
| `POST /api/daily/start` + `GET /api/daily/task/{tid}` | 采集任务（按天）|
| `POST /api/daily/store/import` | 从 `data/probe_daily/*/day_*.json` 回填 |

## 5. 已完成的关键改造

1. **存储重构**：task_id 中心 → 实体+日期中心；旧 `probe_*` 保留只读
2. **迁移校验**：以每日 JSON 为基准，35 项（日期×层级）`SUM(cost)` 差异全 0
3. **页面重构**：`查询数据 / 采集数据` 两页签；查询支持「区间汇总 / 按天」，日期球首项为「汇总」；父体信息卡（图片放大、ASIN/SKU/标题点击复制、活动名可复制）
4. **改名**：自动组合 117 个活动全改 `CA_商品标识_父ASIN`；手工广告组合 2 个加 `_手动` 后缀
5. **竞品扩展**：28 字段（主图/链接/徽标/促销/销量信号/最快到货等），全量 48 条/词
6. **文档**：`docs/ads_data_fetch_guide.md`（取数手册）、`docs/sellfox_sp_create_api.md`（接口契约，§22 关键词数据）；页面「表结构 & SQL 说明」浮框（12 表 + 6 约定 + 14 条可执行 SQL）

## 6. 已知坑（必须记住）

| 坑 | 说明 |
|---|---|
| `campaign/getAllCampaignHourData` | 只认 **单值 `campaignId`**，传 `campaignIdList` 退化成全店 |
| `multiple/group/getAllGroupHourData` | 反之，认 `groupIdList` |
| `multiple/target/getAllTargetData` | 传 `groupIdList` 返回空 → 按活动取再用 `adGroupId` 本地过滤 |
| 活动改名 | `campaign/updateName.json`（form）`{shopId, id=内部id, name}` |
| 组改名 | `group/update.json`（JSON）`{shopId, dxmGroupId, name, defaultBid}`，必须带原竞价 |
| 名称唯一 | 赛狐禁止活动重名；同父 ASIN 的自动/手动活动需加后缀区分 |
| 旧 `probe_search` | 主键漏 `match_type`，同词不同匹配类型会互相覆盖（已改为从 JSON 重建）|
| 组级搜索词 owner_id | 曾混入 Amazon adGroupId 导致重复，`normalize_search_owner()` 已归一 |
| 竞品字段名 | 原始是 `price_value/rating_value/review_count/delivery_date` |
| `getTrend` | 多词用 `######` 连接，`departmentNames` 必填且一一对应 |
| 当天数据 | 是**采集时快照**，赛狐仍在回传（11-07 差 30 元）；建议 T+1 重采 |
| 无头浏览器截图 | 蓝底按钮文字常显示空白，属截图渲染问题，实际有字 |

## 7. 待办

- [ ] 采集流程写入新表的端到端验证（`write_day` 已接入 `_run_task`，需跑一次真实任务确认）
- [ ] 页面切到新接口后的回归（`/store/*` 已通，`daily-probe.html` 已改造）
- [ ] `probe_fact` 瘦身（只存 12 指标可省 92%）
- [ ] 定时任务：每日 T+1 自动重采前一天
- [ ] `fact_*` 表补 `fetched_at`（区分采集时点）

## 8. 常用命令

```bash
# 启动
python3 run.py --port 8320

# 回填历史 JSON → 新表
python3 -c "from backend import ads_store as st; print(st.import_from_dirs())"

# 校验（JSON vs 新表，逐日逐层）
python3 -c "from backend import ads_store as st; r=st.validate_vs_json(); print(sum(1 for x in r if not x['ok']))"

# 补维度名称
python3 -c "from backend import ads_store as st; print(st.enrich_dims('88247362850188','2026-11-06'))"

# 批量改名（dry-run / 执行）
python3 -m backend.rename_tool --portfolio <portfolioId> --day 2026-11-07 --dry-run
python3 -m backend.rename_batch --plan data/rename_plan_<pf>.json --day 2026-11-07 --workers 1
```

## 9. 一条 SQL 拿全（某天某活动）

```sql
SELECT '活动' 层级, SUM(clicks) 点击, ROUND(SUM(cost),2) 花费 FROM fact_campaign
 WHERE campaign_id=? AND date=? AND hour=-1 AND placement=''
UNION ALL
SELECT '广告位·'||placement, SUM(clicks), ROUND(SUM(cost),2) FROM fact_campaign
 WHERE campaign_id=? AND date=? AND hour=-1 AND placement<>'' GROUP BY placement
UNION ALL
SELECT '产品·'||asin, SUM(clicks), ROUND(SUM(cost),2) FROM fact_product
 WHERE campaign_id=? AND date=? AND hour=-1 AND system='total' GROUP BY asin;
```
