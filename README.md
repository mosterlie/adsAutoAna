# 赛狐广告数据平台 (adsAutoAna)

爬取 **赛狐ERP → 广告管理** 下全部子菜单数据并落库, 并附带一套 **复刻赛狐样式/布局** 的前端页面。

- **采集方式**: 后端 `requests` 直连接口 (非 DOM 解析), 依赖登录 Cookie
- **时间范围**: 2026-08-01 ~ 今天 (可配置)
- **其他筛选**: 全状态 (status / servingStatus 全部置空)
- **覆盖页签**: 广告组合 / 广告活动 / 广告组 / 广告产品 / 投放 / 搜索词 / 否定投放 / 广告位 / 广告日志

---

## 一、目录结构

```
adsAutoAna/
├── run.py                      # 启动服务 (uvicorn)
├── scripts_sync_cookies.py     # 从调试 Chrome 同步登录 Cookie
├── config.py                   # 全局配置 (DB/接口/默认时间范围)
├── requirements.txt
├── backend/
│   ├── sellfox_client.py       # 赛狐接口客户端 (页签定义 + 请求体 + 字段中文名)
│   ├── crawler.py              # 采集编排 (店铺→页签→分页→落库)
│   ├── database.py             # SQLite 落库/查询
│   ├── cookies.py              # Cookie 管理 (落盘/读取/从浏览器同步)
│   ├── api.py                  # FastAPI 路由
│   └── main.py                 # 服务入口 (API + 静态前端)
├── frontend/                   # 复刻赛狐样式的页面
│   ├── index.html / style.css / app.js
├── data/                       # ads.db / cookies.json / logs/
└── explore/                    # 逆向分析脚本与产物 (可选)
```

---

## 二、快速开始

```bash
cd adsAutoAna
python3 -m pip install -r requirements.txt

# 1) 启动调试 Chrome (已登录赛狐)
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    --remote-debugging-port=9222 --user-data-dir="$HOME/ChromeDebugUser"

# 2) 同步登录态 → data/cookies.json
python3 scripts_sync_cookies.py

# 3) 启动服务
python3 run.py            # http://127.0.0.1:8320
```

打开 http://127.0.0.1:8320 , 点击「**同步数据**」即开始采集 (后台线程, 右下角显示进度)。

---

## 三、接口

### 采集控制

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/crawl` | 启动采集 `{start_date,end_date,tabs?,shop_ids?,cookie_string?}` |
| GET  | `/api/crawl/state` | 采集进度/日志 |
| GET  | `/api/crawl/runs` | 最近一次采集批次 |
| POST | `/api/cookies/sync` | 从 9222 调试 Chrome 同步 Cookie |
| POST | `/api/cookies` | 手动粘贴 Cookie 字符串 |

### 数据查询

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tabs` | 页签元数据 (含可用维度 sp/sb/sd) |
| GET | `/api/status` | 各页签落库数量 / 最近同步时间 |
| GET | `/api/shops` `/api/portfolios` | 店铺 / 广告组合 |
| GET | `/api/records` | 分页查询 `tab,page,page_size,keyword,shop_id,scope,order_field,order_dir` |
| GET | `/api/stats` | 统计条 (有成交/有点击无成交/有曝光无点击/无曝光) |

---

## 四、采集原理 (实测逆向)

所有数据接口统一前缀:

```
POST https://www.sellfox.com/api/gw/sellfox/sellfox-cpc/api/sellfox/<...>
Headers: Content-Type: application/json / Origin / Referer
鉴权:    登录 Cookie (关键 sf_u)
分页:    pageNo / pageSize(<=200), 响应 data.page.{rows,totalSize,totalPage}
```

| 页签 | 列表接口 | 唯一键 |
| --- | --- | --- |
| 广告组合 | `multiple/portfolio/getAllPortfolioData` | portfolioId |
| 广告活动 | `campaign/getAllCampaignData` | campaignId |
| 广告组 | `multiple/group/getAllGroupData` | adGroupId |
| 广告产品 | `multiple/adProduct/getAdProductList` | id |
| 投放 | `multiple/target/getAllTargetData` | targetId |
| 搜索词 | `multiple/search/getAllSearchData` | queryId |
| 否定投放 | `multiple/neTarget/getAllNeTargetData` | id |
| 广告位 | `multiple/placement/getAllPlacementData` | placementId |
| 广告日志 | `log/sellfoxAndAuto/getPage` | id |

- 店铺/广告组合来源: `commonMultiShop/getPortfolioListProductRight`
- `pageSign` 参数**非必需**; 广告类型维度通过 `adType`/`type`/`types` 字段切换 (sp/sb/sd)
- sb/sd 在部分店铺/页签不受支持, 接口返回业务错误 → 采集层已优雅跳过

---

## 五、数据表

| 表 | 说明 |
| --- | --- |
| `ad_records` | 全量记录 (tab, scope, shop_id, biz_key, raw_json, 唯一键去重) |
| `ad_stats` | 各页签汇总 (aggregate) 原始响应 |
| `ad_shops` / `ad_portfolios` | 店铺 / 广告组合 |
| `crawl_runs` | 采集批次 (状态/耗时/数量) |

展示字段: 完整行存于 `raw_json`, 表头中文名由 `backend/sellfox_client.py: FIELD_LABELS` 映射,
未知字段回落为原始 key。

---

## 六、产品视角 · 搜索词附图与详情

### 交互链路（两段式新开页面）

```
产品视角 → 某父 ASIN 记录 → 点「搜索词」
   → 新页面  /?page=terms&asin=<父ASIN>        列出该父体全部搜索词
   → 点某个词 → 新页签 /?page=term&asin=<父ASIN>&term=<搜索词>
        ├─ 上：父 ASIN 图片 + 全部附图（中等尺寸）+ 标题 + 价格
        ├─ 中：该搜索词各项指标（曝光/点击/CTR/花费/CPC/订单/销售额/ACoS/ROAS/CVR/CPA…）
        └─ 下：亚马逊搜索结果卡片网格（方形卡片：中等图片占主体，下方标题/价格/评分/标签）
```

### 附图（需求 1）

**父 ASIN 的附图 = 该父体下各子 ASIN 亚马逊商品页图廊的合集**（子体详情页附图作为父体附图），
父 ASIN 详情页集中展示全部附图。

- 抓取：`backend/amazon_product.py` 的 `EXTRACT_JS` 从 `/dp/{ASIN}` 抽取
  `#landingImage[data-a-dynamic-image]` + `#altImages li img`（去掉 `._SX679_` 之类尺寸后缀取原图）
- 落库：表 `amz_product_images`（asin 粒度缓存，`thumb`/`large`/`source`）
- 聚合：`database.get_parent_images(父ASIN)` 跨子体去重合并
- 详情页若无附图，**首次进入自动抓取一次**（也可点「从亚马逊抓取附图」）

### 新增接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/product/images` | 父 ASIN 图片资料（主图 + 附图 + 抓取覆盖情况） |
| POST | `/api/product/images/crawl` | **后台**抓取父体 + 全部子体商品页附图，返回任务信息 |
| GET | `/api/product/images/status` | 附图抓取进度（running/total/done/current/images） |
| GET | `/api/product/term_detail` | 单个搜索词详情：父体资料(主图/附图/标题/价格) + 词指标 + 明细变体 |
| GET | `/api/amazon/metrics` | 新增 `images=true`，同时返回该 ASIN 的图片 |

### 新增表 / 列

| 对象 | 说明 |
| --- | --- |
| 表 `amz_product_images` | 商品页图片（asin, domain, position, thumb, large, source） |
| 表 `amz_image_crawls` | 附图抓取记录（asin 是否已抓过，含抓空），用于判断父体是否已全量覆盖、避免重复抓取 |
| 列 `amz_product_metrics.price` | 商品页主价格（详情页展示用，回退赛狐商品行价格） |

### 附图抓取与展示口径

- **只取「一个子 ASIN」的全部附图作为父 ASIN 的附图**：
  图片源子体按顺序取第一个已抓到图片的子体（都没有则取第一个子体，无子体时回退父体），
  取该商品页图廊的**全部**图片（主图 + 全部附图，按原图 URL 去重）
- **主图** = 图片来源子体的主图；**附图** = 该子体的其余图片，因此首图不会与主图重复
- 只抓 1 个商品页，速度快；走**后台线程 + 前端轮询进度**，
  已抓过的来源子体不会重复抓取（`refresh=true` 可强制重抓）
- 快速模式：只取图片时缩短页面等待（等图廊出现即可，不等 BSR 等指标）

### 父 ASIN 售价（子体售价中位数）

- 产品列表（父 ASIN 维度）新增 **售价** 列：取该父体下**全部子体售价的中位数**
- 支持点击表头排序（服务端排序，字段 `price_median`）；表头 Σ 显示各父体售价的中位数
