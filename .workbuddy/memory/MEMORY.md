# adsAutoAna 项目长期记忆

## 运行方式
- **当前环境是 Windows，工作区 `D:/myCoding/adsAutoAna`**（10-16 迁入；下面 macOS 段落仅作历史参考）。
  查端口用 `netstat -ano | grep 8320`（无 `lsof`）。
- 服务端口 **8320**（`run.py`：`--port` 默认 8320 / `--host` 默认 127.0.0.1），`python3 run.py`；
  macOS 用 `/Library/Frameworks/Python.framework/Versions/3.11/bin/python3`（系统 3.13 无依赖）。
- curl 一律要加 `--noproxy '*'`（本机有代理环境）。
- **改后端必须重启服务**。日志在 **`data/logs/server.log`**（`/tmp/adsautoana.log` 是
  10-05 之前的老路径，已失效，别再用它排查）。确认服务在跑只需两步：
  `lsof -nP -iTCP:8320 -sTCP:LISTEN` + `curl -s --noproxy '*' .../api/status`。
  项目是 git 仓库，重启前可 `git log -1` 看工作区是否比运行中的进程更新。
- 重启务必用**双重 fork + setsid** 脱离会话（macOS 没有 `setsid`
  命令；Bash 工具的 `run_in_background` 起的进程会在**该轮对话结束时被回收**）：

```bash
cd /Users/gx/Desktop/mypro/adsAutoAna && lsof -ti:8320 | xargs -r kill 2>/dev/null
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3 - <<'PY'
import os, sys
PY_EXE = "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
os.chdir("/Users/gx/Desktop/mypro/adsAutoAna")
if os.fork() > 0: sys.exit(0)
os.setsid()
if os.fork() > 0: os._exit(0)
log = open("/tmp/adsautoana.log", "ab"); os.dup2(log.fileno(), 1); os.dup2(log.fileno(), 2)
os.execv(PY_EXE, [PY_EXE, "run.py"])
PY
```

  启完用 `lsof -nP -iTCP:8320 -sTCP:LISTEN` + `curl -s --noproxy '*' .../api/status` 确认。

## 提交与推送（Windows）
- ⚠ **`git push origin main` 会挂死**：`origin` = `https://ghfast.top/https://github.com/mosterlie/adsAutoAna.git`，
  而 `~/.git-credentials`（`credential.helper=store`）里**只有 github.com / gitcode.com，没有 ghfast.top**；
  且 PortableGit 的系统级配置 `D:/workbuddy/resources/vendor/PortableGit/etc/gitconfig`
  写了 `credential.helper=manager` —— helper 是**叠加**的，store 答不上就落到 GCM，
  GCM 弹窗等人输入 → 推送无限挂起（进程列表能同时看到 `git.exe` 和 `git-credential-manager.exe`）。
- ✅ 绕过办法：**直连 GitHub 推**（直连实测 200，1.2s），github.com 的凭证是好的：
  ```bash
  GIT_TERMINAL_PROMPT=0 git push https://github.com/mosterlie/adsAutoAna.git main:main
  ```
  推 URL（非 remote 名）不会更新 `refs/remotes/origin/main`，需手动
  `git update-ref refs/remotes/origin/main <sha>` 对齐。
- 排查手法：`GIT_TERMINAL_PROMPT=0` + `git ls-remote <url>` 先验凭证，避免直接 push 挂死。

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
> 全量回归统一看 **`verify_ui.py`（当前 187 项）**；不要再引用「172 项」那个旧数字。
- 分段：D 接口契约 / A+B+C 界面 / H 子体父体 / I 产品视角父ASIN合并与交互 / K 亚马逊结果字段 /
  M 搜索词默认口径与过滤 / N 表头合计 / E 与原系统对比 / F 表头查询框对照 / G 评分补全。
  新增功能务必同步补测试。
- **搜索词视图默认「只看搜索词」**（`TERM_DEFAULT_SCOPE="sp:keyword"`，因为
  `sp:asin` 是竞品商品投放、不是搜索词）：`loadTerms/loadParentTerms` 的 `dim===undefined`
  → 默认 keyword，显式 `""` 才是全部。若该活动/父体**一条搜索词都没有**则自动回退「全部」。
  过滤按钮上的 `(N)` 用后端返回的 `counts{all,keyword,targeting}`（统计在过滤**之前**，
  父体级按合并后行数），否则过滤后会变成 0。
- `verify_fixes.py`(13) / `verify_filters.py`(15) / `verify_term_relevance.py`(28) /
  `compare_with_origin.py` 为专项/历史回归。
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

## 搜索词 × SKU 相关性分析（10-15 新增）
- `backend/term_relevance.py`（打分核心）+ `analyze_term_relevance.py`（出报告，`--min-cost` 默认 100）
  + `verify_term_relevance.py`（T 段 28 项）。产物落 `data/reports/term_relevance.{html,md,json}`。
- 口径：`score = 0.60×coverage + 0.25×contig + 0.15×lcs`。**别再退回用 Dice 当第二成分** ——
  长标题会把 Dice 稀释，导致「整串就是标题子串」只拿 0.78。
- 分档：≥0.85 高度相关 / ≥0.60 相关 / ≥0.35 弱相关 / <0.35 不相关。
- **janome 会把片假名复合词并成一个词**（`ワイヤーメッシュフェンス` 不拆），
  必须走 `covered_ratio()` greedy 最长匹配拆解，否则误判。
- `normalize()` 的 `_KANA_PUNCT` 黑名单不能删：`・`(U+30FB) 落在假名区间 U+3040–U+30FF 内，
  不剔掉会污染匹配（「ー」U+30FC 要保留）。
- 搜索词记录**没有 asin 字段**，ASIN 只能从 `campaignName`/`adGroupName` 的 `B0XXXXXXXX` 前缀取。
- 同一「词 × ASIN」跨活动会重复出现，**必须先合并累加再比阈值**，否则漏行（41→45）。

## 图片相似度判别（10-15 新增）
- `explore/probe_image_similarity.py`；产物 `data/reports/image_similarity.md` +
  `image_similarity_check.png`（原图/掩膜/主体裁剪三联图）。
- **判断「是不是同一个商品」优先看 ORB 好匹配数**，别看直方图：
  同物换角度通常 80~500 对好匹配且 RANSAC 内点率 60%+；不同物会掉到个位数甚至拟合不出变换。
- **整图指标会被「白底 + 居中斜放摆拍」这个电商通用套路抬高**：
  灰度直方图相关性 0.993、三维 HSV 0.969 也照样是两个不同商品。
  必须**剥掉白底只看主体**再算一遍，才是真实差距。
- 环境已就绪：pillow 9.5.0 / numpy 2.4.6 / cv2 4.11.0。
  Chrome 在 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，
  可用 `--headless=new --screenshot=... --window-size=W,H` 自查 HTML 报告排版。

## 上云托管（10-16）
- **分享链接 https://sellfox-ads-console.app.workbuddy.host/**，由 `directory=<项目根>` 发布，
  `startCmd="python3 run.py"`（沙箱注 `PORT`，`run.py` 已改成有 `PORT` 就绑 `0.0.0.0`）。
- 仓库根的 `.wbapp_*.genie` 是应用标记，**别删**；重新发布必须从同一 `directory` 发，链接才不变。
- ⚠ `backends/api.py` 的 `/settings` 曾因 `from workbuddy.client import KNOWN_MODELS`（客户端在
  项目外部 `/Users/gx/Desktop/mypro/testApi`）在托管环境 500，已改为 `_known_models()` 降级。
  **本项目的 AI 能力（语义相关性/视觉比对）依赖项目外部的 `testApi` 与 `.env`，托管环境一律不可用**，
  线上只剩纯规则文本分——加新的 AI 功能时务必保持「拿不到就降级」而不是抛异常。
- 链接**无鉴权**，任何拿到的人都能看全部经营数据（已告知用户并获确认）。

## 已知遗留
- 性能债：`get_stats` 全表 JSON 扫描、keyword LIKE 全表、JSON 排序无索引 → 建议抽实体列+索引。
- CORS `allow_origins=["*"]`、Cookie 明文、接口无鉴权。
- 批量抓 ASIN 需抖动+熔断+断点续跑（未实现）。
- 在线产品写操作（改价/改库存/导出）未还原。
