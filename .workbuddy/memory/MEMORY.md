# adsAutoAna 项目长期记忆

## 运行方式
- 服务端口 **8320**，`python3 run.py`；Python 用
  `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3`（系统 3.13 无依赖）。
- curl 一律要加 `--noproxy '*'`（本机有代理环境）。
- **改后端必须重启服务**；重启用 Bash 工具的 `run_in_background=true`（macOS 无 setsid，
  普通 `nohup &` 有时会被回收）。

## 赛狐接口要点（踩过的坑）
- 广告管理：分页接口按页签不同，`tab` + `scope` 维度用 `:` 连接（如 `sp:keyword`）。
  `campaignId` 是**字符串**，比较别转数字。
- **在线产品「子体 / 父体」是两个不同端点，不是换参数**：
  - 子体 `POST /api/gw/sellfox/sellfox-product/sellfox/product/pageList`
  - 父体 `POST /api/parent/product/pageList.json`（且 `startTime`/`endTime` **必填**，空则 0 条）
  - 行内 `isVariation`：子体=2，父体=1（父体行 `asin == parentAsin`）。
- `defaultPageType=1` 返 rows，`=2` 只返 totalSize；`pageSize` 上限 200。
- 原站「在线产品」页面关键路径：`/amzup-web-main/web/product/index.html`。

## 亚马逊取数
- **不调接口**，用真实浏览器加载页面后读 DOM（请求头指纹不全时返回 503：
  光有 UA 会 503，补 `Accept-Encoding` 或 `Accept-Language` 即 200）。
- 解析排名的连字符字符集**不能含日文长音符「ー」(U+30FC)**。
- 频率控制：搜索结果缓存 30min + 最小间隔 6s + 全局锁串行。

## 数据与界面约定
- 库里存的是**快照**（最后一次成功采集的区间），不是实时库 → 默认查询区间要取
  `/api/status` 的 `last_run.range_start/range_end`，不能直接用「今天」。
- ⚠ `ad_records` 唯一键含 `range_start/range_end`，**跨天采集会把同一份数据再存一遍**
  → 指标翻倍。已加三层防护：落库前 `purge_stale_ranges`、采集收尾
  `purge_all_other_ranges`、统计只读 `_latest_range`。改动这里务必保留。
- 前端三种视图：广告管理「数据表格」+「产品视角」三级下钻 +「在线产品」独立视图。
- **产品视角以父 ASIN 为一条记录**（多子 ASIN 合并统计），列为
  图 | 父ASIN | 父SKU | 子ASIN | 标题 | 子体数 | 关联活动 | 有词活动 | 搜索词条目 | 花费 |
  曝光 | 点击 | 订单 | CTR | ACOS | 操作(固定)。
  父子映射来源 = `online_products` 的 `parent_asin`；`get_products()` 按父聚合，
  `get_product_campaigns()` 入参父/子 ASIN 均可并自动换算、同活动合并成一行。
  交互约定：**整行不可点**，只有「操作」列的按钮才下钻；「操作」列 `position:sticky;right:0` 固定。
- 列渲染：有值覆盖，无值仍显示 `***`（与原站一致）。
- 子ASIN 列：**有广告投放的排最前 + 蓝底胶囊高亮**，未投放的灰字排在后面；
  概览条必须有配色图例（不能只靠颜色传达信息）。
- 图片：赛狐 `mainBigImage` 是 75px 缩略图（URL 带 `._SL75_`）。要原图就**删掉尺寸后缀**
  （`._SL1000_` 会 400）。统一用前端的 `amzBigImage()`。
- **表头点击排序**（三级表格都有）：`sortableTh(state,{cls,label,en,field,type,total,totalTip})` +
  `bindSortHeaders(root,state,onSort)` + `sortRows()`。一级走后端（`sort1` 视图模型 ←→
  `pv.orderField/orderDir`），二/三级走前端（`pv.cSort` / `pv.tSort`）。
  箭头用 `th::after` 画，**绝不能写进 DOM 文本**（会污染 `inner_text` 与列宽）；
  `th.th-sortable` **不要写 `position:relative`**，会破坏 `thead th` 的吸顶。
  后端 `get_products` 排序白名单在 `NUM_KEYS`/`TEXT_KEYS`，加可排序列要同时改两处。
- **表头「合计」**：可累加列在标题下多一行 `<span class="th-total">Σ 值</span>`。
  一级用后端 `totals`（`_sum_product_rows`，在**分页前**对全部过滤行求和，所以表头 Σ 是
  「全部 N 个父体」而不是本页）；二/三级用 `sumBy(rows, field)` 前端求和，随 scope 变化。
  **比率列(CTR/ACOS/CVR)必须用总量重算**，不能把各行比率相加。
  `bindSortHeaders` 只在 `!th.title` 时才写「点击排序」，否则会冲掉列的说明与合计口径。

## 测试
- `verify_ui.py` 全量回归（当前 **187 项**）：D 接口契约 / A+B+C 界面 / H 子体父体 /
  I 产品视角父ASIN合并与交互 / K 亚马逊结果字段 / M 搜索词默认口径与过滤 /
  N 表头合计 / E 与原系统对比 / F 表头查询框对照 / G 评分补全。新增功能务必同步补测试。
- **搜索词视图默认「只看搜索词」**（`TERM_DEFAULT_SCOPE="sp:keyword"`，因为
  `sp:asin` 是竞品商品投放、不是搜索词）：`loadTerms/loadParentTerms` 的 `dim===undefined`
  → 默认 keyword，显式 `""` 才是全部。若该活动/父体**一条搜索词都没有**则自动回退「全部」。
  过滤按钮上的 `(N)` 用后端返回的 `counts{all,keyword,targeting}`（统计在过滤**之前**，
  父体级按合并后行数），否则过滤后会变成 0。

## 测试
- `verify_ui.py` 全量回归（当前 **172 项**）：D 接口契约 / A+B+C 界面 / H 子体父体 /
  I 产品视角父ASIN合并与交互 / K 亚马逊结果字段 / **M 搜索词默认口径与过滤** /
  E 与原系统对比 / F 表头查询框对照 / G 评分补全。新增功能务必同步补测试。
- `verify_fixes.py`(13) / `verify_filters.py`(15) / `compare_with_origin.py` 为历史回归。
- 排查脚本放 `explore/`，命名 `probe_*.py`。
- 产品视角表格改列宽时记得同步改 `verify_ui.py` 的 I17/I18（断言列宽上限），
  以及 I14 取父 ASIN 要用 `.asin-copy`（该单元格还含 ↗ 跳转按钮）。
- 亚马逊结果加字段时，除后端 `EXTRACT_JS` 外必须在 `app.js` 的 `AMZ_FIELD_LABELS`
  （或 `AMZ_SPECIAL_KEYS`）里给出展示位，否则 K12 会失败——这是刻意留的守卫。

## 亚马逊搜索结果抓取（搜索词 → 结果页）
- `backend/amazon.py` 的 `EXTRACT_JS` 三段式：精确选择器 + innerText 通用扫描(extras) + raw_text 兜底。
- **评论数**只能从「文本是 `(1,234)` 的评价链接」拿；aria-label 里是「5つ星のうちX」会取错。
- **品牌**没有独立节点，只能从图片 alt 前缀切（`スポンサー広告 - <品牌> <标题>`）。
- 广告位 href 是 `/sspa/click?…` → `url` 一律用 `/dp/{ASIN}`，原链存 `url_href`。
- 高清图走 `img.s-image` 的 **`srcset` 最大档**（`._AC_UL960_`，960px），别去猜尺寸后缀。
- 落库：`amz_results.raw_json` 存完整 item；读回走 `_amz_item()` 合并（老数据兼容）。

## 已知遗留
- 性能债：`get_stats` 全表 JSON 扫描、keyword LIKE 全表、JSON 排序无索引 → 建议抽实体列+索引。
- CORS `allow_origins=["*"]`、Cookie 明文、接口无鉴权。
- 批量抓 ASIN 需抖动+熔断+断点续跑（未实现）。
- 在线产品写操作（改价/改库存/导出）未还原。
