/* 赛狐广告管理 - 前端逻辑 */
const $ = (id) => document.getElementById(id);
const api = (p, o) => fetch(p, o).then((r) => r.json());
const esc = (s) => String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

const state = {
  tabs: [], today: "", defaultStart: "2026-08-01",
  snapStart: "", snapEnd: "",      // 最后一次成功采集的快照区间
  tab: "", page: 1, pageSize: 200, total: 0,
  columns: [], rows: [], hidden: {}, keyword: "", orderField: "", orderDir: "desc",
  filters: {}, searchField: "", searchMode: "blur", filterMeta: null,
  amzQuery: "", amzItems: [], amzCached: new Set(),   // amzCached: 本地已抓过的搜索词
  translations: {}, transPending: new Set(),          // 搜索词中文翻译(已落库) + 生成中
  transFailed: new Set(),                             // 本次会话内生成失败的词(不再重复入队)
  transTimer: null,
  transHooks: new Map(),                              // 视图key -> 译文更新后的刷新回调(按 key 覆盖, 不会累积)
  prefetchRunning: false, prefetchTimer: null,
  fetchRunning: false, fetchState: null, fetchTimer: null,   // 全量拉取素材(产品视角工具栏)
  polling: null,
};

const NUM_KEYS = new Set([
  "impressions", "clicks", "ctr", "cvr", "adCost", "cpm", "vcpm", "adCostPerClick", "cpc",
  "adOrderNum", "adSaleNum", "adSales", "adSale", "orderNum", "sales", "cpa", "acos", "roas",
  "acots", "asots", "acoas", "asoas", "adOrderNumPercentage", "adSalePercentage",
  "adSalesPercentage", "clickPercentage", "impressionsPercentage", "orderNumPercentage",
  "adSelfSaleNum", "adOtherSaleNum", "adSelfOrderNum", "adOtherOrderNum", "adSelfSales",
  "adOtherSales", "searchFrequencyRank", "weekRatio", "impressionRank", "dailyBudget",
  "defaultBid", "bid", "originalBid", "budgetUsage", "overBudgetCount", "viewImpressions",
  "addToCart", "addToCartRate", "brandedSearches", "detailPageViews", "cumulativeReach",
  "impressionsFrequencyAverage", "video5SecondViews", "videoCompleteViews", "videoUnmutes",
  "viewabilityRate", "viewClickThroughRate", "topImpressionShare", "impressionShare",
]);

const STATE_MAP = {
  enabled: ["投放中", "on"], paused: ["已暂停", "pause"], archived: ["已归档", ""],
  CAMPAIGN_STATUS_ENABLED: ["正在投放", "on"], CAMPAIGN_PAUSED: ["广告活动已暂停", "pause"],
  CAMPAIGN_ARCHIVED: ["已归档", ""],
};

function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  setTimeout(() => t.classList.remove("show"), 2200);
}

function fmtCell(key, val) {
  if (val === null || val === undefined || val === "") return "";
  if (STATE_MAP[val]) {
    const [label, cls] = STATE_MAP[val];
    return `<span class="badge ${cls}">${label}</span>`;
  }
  if (NUM_KEYS.has(key)) {
    const n = Number(val);
    if (!isNaN(n)) {
      return Number.isInteger(n) ? n.toLocaleString("en-US")
        : (Math.abs(n) >= 1000 ? n.toLocaleString("en-US", { maximumFractionDigits: 2 }) : n.toFixed(2));
    }
  }
  const s = String(val);
  return s.length > 60 ? s.slice(0, 60) + "…" : s;
}

/* 默认时间范围: 优先用「最后一次成功采集」的快照区间
   (库里存的是快照, 若默认取今天, 隔天打开就会因为区间对不上而查不到任何数据) */
function applyDefaultRange() {
  $("fStart").value = state.snapStart || state.defaultStart;
  $("fEnd").value = state.snapEnd || state.today;
}

/* ---------- 初始化 ---------- */
async function boot() {
  // 独立页面 (?page=terms | ?page=term): 只渲染对应视图, 不加载主应用
  const sp = new URLSearchParams(location.search);
  if (sp.get("page")) { await bootStandalone(sp); return; }

  const meta = await api("/api/tabs");
  state.tabs = meta.tabs; state.today = meta.today; state.defaultStart = meta.default_start;
  state.tab = state.tabs[1] ? state.tabs[1].key : state.tabs[0].key;   // 默认「广告活动」
  applyDefaultRange();
  renderTabs();
  renderScopeOptions();
  await Promise.all([loadStatus(), loadShops(), loadPortfolios()]);
  applyDefaultRange();          // loadStatus 拿到快照区间后再对齐一次
  await loadFilterMeta();
  await loadAmzCached();        // 先拿到"哪些搜索词本地已有", 表格才能标出可直读的词
  await loadAll();
  tick();
  setInterval(tick, 1000);
  setInterval(() => { if (state.polling) pollCrawl(); }, 1200);
}

function tick() {
  $("clock").textContent = new Date().toLocaleString("zh-CN", { hour12: false });
}

function renderTabs() {
  $("tabbar").innerHTML = state.tabs.map((t) =>
    `<div class="tab ${t.key === state.tab ? "active" : ""}" data-tab="${t.key}">${t.label}</div>`).join("");
  $("tabbar").querySelectorAll(".tab").forEach((el) => el.onclick = () => {
    state.tab = el.dataset.tab; state.page = 1; state.orderField = ""; state.hidden = {};
    state.filters = {}; state.searchField = ""; state.keyword = ""; $("fKeyword").value = "";
    renderTabs(); renderScopeOptions();
    // 切到「搜索词」页签时刷新本地缓存清单(后台可能刚抓完新的词)
    if (state.tab === "search") loadAmzCached().then(loadFilterMeta).then(loadAll);
    else loadFilterMeta().then(loadAll);
  });
}

/* ---------- 动态查询框 (按页签数据自动生成) ---------- */
async function loadFilterMeta() {
  const shop = $("fShop").value;
  const q = new URLSearchParams({ tab: state.tab });
  if (shop) q.set("shop_id", shop);
  try {
    state.filterMeta = await api("/api/filters?" + q.toString());
  } catch (e) { state.filterMeta = { filters: [], search_fields: [] }; }
  // 页签/店铺切换后, 旧的筛选值可能已非法 -> 仅保留新 meta 中仍存在的键
  const ok = new Set((state.filterMeta.filters || []).map((f) => f.key));
  Object.keys(state.filters).forEach((k) => { if (!ok.has(k)) delete state.filters[k]; });
  renderAdvFilters();
  renderSearchFields();
}

function renderSearchFields() {
  const m = state.filterMeta || {};
  const sel = $("fSearchField");
  const fields = m.search_fields || [];
  sel.innerHTML = `<option value="">全部字段</option>` +
    fields.map((s) => `<option value="${esc(s.key)}">${esc(s.label)}</option>`).join("");
  sel.value = state.searchField || "";
  if (!sel.value) state.searchField = "";
}

function renderAdvFilters() {
  const box = $("advFilters");
  const m = state.filterMeta;
  if (!m || !(m.filters || []).length) { box.innerHTML = ""; return; }
  box.innerHTML = m.filters.map((f) => {
    const sel = state.filters[f.key] || [];
    const label = sel.length === 0 ? "全部"
      : (sel.length === 1
        ? ((f.options.find((o) => o.value === sel[0]) || {}).label || sel[0])
        : `已选 ${sel.length} 项`);
    return `<div class="fld"><label>${esc(f.label)}</label>
      <div class="dd" data-key="${esc(f.key)}">
        <button type="button" class="dd-btn">${esc(label)}</button>
        <div class="dd-pop">${f.options.map((o) =>
          `<label><input type="checkbox" data-k="${esc(f.key)}" value="${esc(o.value)}"${sel.includes(o.value) ? " checked" : ""}> ${esc(o.label)}<span class="cnt">${o.count}</span></label>`).join("")}</div>
      </div></div>`;
  }).join("");

  box.querySelectorAll(".dd-btn").forEach((b) => b.onclick = (e) => {
    e.stopPropagation();
    const pop = b.nextElementSibling;
    box.querySelectorAll(".dd-pop").forEach((p) => { if (p !== pop) p.classList.remove("open"); });
    pop.classList.toggle("open");
  });
  box.querySelectorAll("input[data-k]").forEach((el) => el.onchange = () => {
    const k = el.dataset.k;
    const set = new Set(state.filters[k] || []);
    el.checked ? set.add(el.value) : set.delete(el.value);
    const arr = [...set];
    if (arr.length) state.filters[k] = arr; else delete state.filters[k];
    const f = m.filters.find((x) => x.key === k);
    el.closest(".dd").querySelector(".dd-btn").textContent = arr.length === 0 ? "全部"
      : (arr.length === 1 ? ((f.options.find((o) => o.value === arr[0]) || {}).label || arr[0])
        : `已选 ${arr.length} 项`);
    state.page = 1; loadAll();
  });
}

function renderScopeOptions() {
  const t = state.tabs.find((x) => x.key === state.tab);
  const scopes = [...new Set((t ? t.scopes : []).filter(Boolean))];
  $("fScope").innerHTML = `<option value="">全部</option>` +
    scopes.map((s) => `<option value="${s}">${s.toUpperCase()}</option>`).join("");
}

async function loadStatus() {
  const st = await api("/api/status");
  $("lastUpdated").textContent = st.last_updated ? `最近同步: ${st.last_updated}` : "数据未同步";
  const lr = st.last_run;
  if (lr && lr.range_start && lr.range_end && lr.status === "success") {
    state.snapStart = lr.range_start; state.snapEnd = lr.range_end;
  }
  if (st.crawl && st.crawl.running) startPolling();
}

async function loadShops() {
  const r = await api("/api/shops");
  $("fShop").innerHTML = `<option value="">全部店铺</option>` +
    r.rows.map((s) => `<option value="${s.shop_id}">${s.shop_name}${s.site_name ? " · " + s.site_name : ""}</option>`).join("");
}

function portfolioFilterable() {
  const m = state.filterMeta;
  return !!(m && (m.filters || []).some((f) => f.key === "portfolioId"));
}

async function loadPortfolios() {
  const r = await api("/api/portfolios");
  const kw = ($("fPortfolio").value || "").toLowerCase();
  const uniq = [];
  const seen = new Set();
  r.rows.forEach((p) => {
    const key = p.portfolio_id || p.portfolio_name;
    if (!seen.has(key)) { seen.add(key); uniq.push(p); }
  });
  let html = `<li class="active" data-pid="" data-name="">全部广告组合</li>`;
  html += uniq.filter((p) => (p.portfolio_name || "").toLowerCase().includes(kw))
    .map((p) => `<li data-pid="${esc(p.portfolio_id || "")}" data-name="${esc(p.portfolio_name || "")}">${esc(p.portfolio_name || p.portfolio_id)}</li>`).join("");
  $("tree").innerHTML = html;
  $("tree").querySelectorAll("li").forEach((el) => el.onclick = () => {
    $("tree").querySelectorAll("li").forEach((x) => x.classList.remove("active"));
    el.classList.add("active");
    const pid = el.dataset.pid, name = el.dataset.name;
    if (!pid && !name) {                       // 全部广告组合
      delete state.filters.portfolioId; state.keyword = ""; $("fKeyword").value = "";
    } else if (portfolioFilterable()) {        // 有 portfolioId 字段 -> 按 ID 精确过滤
      state.filters.portfolioId = [String(pid)]; state.keyword = ""; $("fKeyword").value = "";
    } else {                                   // 该页签无 portfolioId -> 退回按名称搜索
      state.keyword = name; $("fKeyword").value = name;
      delete state.filters.portfolioId;
    }
    state.page = 1; renderAdvFilters(); loadAll();
  });
}

/* ---------- 数据加载 ---------- */
async function loadAll() { await Promise.all([loadRecords(), loadStats()]); }

function commonParams() {
  const p = new URLSearchParams({ tab: state.tab });
  const shop = $("fShop").value; if (shop) p.set("shop_id", shop);
  const scope = $("fScope").value; if (scope) p.set("scope", scope);
  const rs = $("fStart").value, re = $("fEnd").value;
  if (rs) p.set("range_start", rs);
  if (re) p.set("range_end", re);
  if (state.keyword) p.set("keyword", state.keyword);
  if (state.searchField) p.set("search_field", state.searchField);
  p.set("search_mode", state.searchMode || "blur");
  const fl = Object.fromEntries(Object.entries(state.filters).filter(([, v]) => v && v.length));
  if (Object.keys(fl).length) p.set("filters", JSON.stringify(fl));
  return p;
}

function queryParams() {
  const p = commonParams();
  p.set("page", state.page); p.set("page_size", state.pageSize);
  p.set("order_dir", state.orderDir);
  if (state.orderField) p.set("order_field", state.orderField);
  return p;
}

async function loadRecords() {
  const d = await api("/api/records?" + queryParams().toString());
  state.columns = d.columns; state.rows = d.rows; state.total = d.total;
  renderTable(); renderPager();
  if (state.tab === "search") ensureTerms();     // 本页搜索词: 缺译文的排队自动翻译
}

/* ---------- 搜索词中文翻译 (AI 生成, 落库, 已译不重调) ---------- */

/* ASIN 形态的 query(商品定位行)不需要翻译 */
function isAsin(q) { return /^[A-Z0-9]{10}$/.test(String(q || "").trim()); }

function termOfRow(r) { return String(r.query || r.keywordText || "").trim(); }

/* 把一批搜索词的缺译文发给后端; 已缓存的立即返回; 译文有更新时回调 onChange
   (主表格 / 搜索词列表页 / 抽屉搜索词表 / 搜索词详情页 共用这一套)

   hookKey: 视图标识。回调按 key 覆盖而不是累积 —— 否则「渲染->入队->回调->渲染」
   会不断新增回调, 一个词生成失败就会指数级放大直到页面卡死。
   state.transFailed: 本次会话内生成失败的词(不落库的词, 如品牌名), 不再重复入队。 */
async function ensureTermList(queries, onChange, hookKey) {
  const qs = [...new Set((queries || []).map((s) => String(s || "").trim())
    .filter((q) => q && !isAsin(q)))]
    .filter((q) => !state.translations[q] && !state.transPending.has(q)
      && !state.transFailed.has(q));
  if (!qs.length) return;
  if (onChange) state.transHooks.set(hookKey || "default", onChange);

  let d;
  try {
    d = await api("/api/translate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kind: "search_term", sources: qs }),
    });
  } catch (e) { return; }
  if (d.detail) return;
  Object.assign(state.translations, d.translations || {});
  const pending = (d.pending || []).filter((q) => !state.translations[q]);
  pending.forEach((q) => state.transPending.add(q));
  if (Object.keys(d.translations || {}).length) notifyTransHooks();
  if (pending.length) pollTrans(qs);
}

/* 搜索词页签(主表格): 本页缺译文的词排队翻译 */
function ensureTerms(extra) {
  return ensureTermList([
    ...(extra || []),
    ...state.rows.map(termOfRow),
  ], () => { renderTable(); updateAmzZh(state.amzQuery); }, "table");
}

function notifyTransHooks() {
  state.transHooks.forEach((fn) => {
    try { fn(); } catch (e) { /* 单个视图刷新失败不影响其它 */ }
  });
}

/* 有生成任务时轮询; 每拿到新译文就通知所有视图刷新 */
function pollTrans(queries) {
  if (state.transTimer) return;
  const tick = async () => {
    const qs = [...new Set([...queries, ...state.transPending])].filter((q) => q);
    if (!qs.length) { clearInterval(state.transTimer); state.transTimer = null; return; }
    let d;
    try {
      d = await api("/api/translate/status?kind=search_term&sources="
        + encodeURIComponent(qs.join(",")));
    } catch (e) { return; }
    let got = 0;
    Object.entries(d.translations || {}).forEach(([k, v]) => {
      if (!state.translations[k]) got++;
      state.translations[k] = v;
    });
    [...state.transPending].forEach((q) => {
      if (state.translations[q]) state.transPending.delete(q);
    });
    if (got) notifyTransHooks();
    const job = d.job || {};
    if (!job.running) {
      clearInterval(state.transTimer); state.transTimer = null;
      if (state.transPending.size) {          // 结束了还有没拿到的 → 这批词翻译失败
        const err = job.error ? `：${job.error}` : "";
        toast(`部分搜索词翻译失败${err}`);
        // 记入失败集合(而不是直接清空): 否则下一轮渲染会把它们当「缺译文」重新入队,
        // 形成 渲染→入队→回调→渲染 的死循环
        state.transPending.forEach((q) => state.transFailed.add(q));
        state.transPending.clear();
        notifyTransHooks();
      }
    }
  };
  state.transTimer = setInterval(tick, 2500);
}

/* 弹层里的译文展示位 */
function updateAmzZh(q) {
  const el = $("amzZh");
  if (!el) return;
  if (!q || isAsin(q)) { el.textContent = ""; return; }
  const zh = state.translations[q];
  if (zh) { el.textContent = `中文：${zh}`; el.classList.remove("pending"); }
  else if (state.transPending.has(q)) {
    el.textContent = "翻译生成中…"; el.classList.add("pending");
  } else { el.textContent = ""; el.classList.remove("pending"); }
}

async function loadStats() {
  const s = await api("/api/stats?" + commonParams().toString());
  $("chips").innerHTML = `
    <span class="deal">有成交 <b>${s.deal}</b></span>
    <span class="cnd">有点击无成交 <b>${s.click_no_deal}</b></span>
    <span class="inc">有曝光无点击 <b>${s.imp_no_click}</b></span>
    <span class="ni">无曝光 <b>${s.no_imp}</b></span>
    <span class="ni">合计 <b>${s.total}</b></span>`;
}

/* ---------- 表格 ---------- */
function visibleColumns() {
  return state.columns.filter((c) => !state.hidden[c.key]);
}

function renderTable() {
  const cols = visibleColumns();
  $("headRow").innerHTML = `<th><input type="checkbox" id="checkAll"></th>` +
    cols.map((c) => `<th class="${NUM_KEYS.has(c.key) ? "num" : ""}" data-k="${c.key}">${c.label}
      ${state.orderField === c.key ? (state.orderDir === "desc" ? "↓" : "↑") : ""}</th>`).join("");
  if (!state.rows.length) {
    $("bodyRow").innerHTML = ""; $("empty").style.display = "block";
    const rs = $("fStart").value, re = $("fEnd").value;
    const snap = state.snapStart && state.snapEnd &&
      (state.snapStart !== rs || state.snapEnd !== re);
    $("empty").innerHTML = snap
      ? `当前区间 ${esc(rs)} ~ ${esc(re)} 无数据（本地存的是快照，不是实时库）<br>
         <span style="color:var(--muted,#8a8f98)">快照区间为 ${esc(state.snapStart)} ~ ${esc(state.snapEnd)}</span>
         <button class="btn btn-mini" id="useSnapRange">用快照区间查询</button>`
      : "暂无数据，点击「同步数据」采集";
    const ub = $("useSnapRange");
    if (ub) ub.onclick = () => { applyDefaultRange(); state.page = 1; loadAll(); };
  } else {
    $("empty").style.display = "none";
    $("bodyRow").innerHTML = state.rows.map((r) =>
      `<tr><td><input type="checkbox" class="rowchk"></td>` +
      cols.map((c) => `<td class="${NUM_KEYS.has(c.key) ? "num" : ""} ${c.key === "name" ? "name" : ""}">${cellHtml(c, r)}</td>`).join("") + `</tr>`
    ).join("");
  }
  $("headRow").querySelectorAll("th[data-k]").forEach((th) => th.onclick = () => {
    const k = th.dataset.k;
    if (state.orderField === k) state.orderDir = state.orderDir === "desc" ? "asc" : "desc";
    else { state.orderField = k; state.orderDir = "desc"; }
    state.page = 1; loadRecords();
  });
  const ca = $("checkAll");
  if (ca) ca.onclick = () => {
    $("bodyRow").querySelectorAll(".rowchk").forEach((c) => { c.checked = ca.checked; });
    updateSel();
  };
  $("bodyRow").querySelectorAll(".rowchk").forEach((c) => c.onchange = updateSel);
  $("bodyRow").querySelectorAll(".amz-link").forEach((el) => el.onclick = () => openAmazon(el.dataset.q));
  renderColMenu();
}

/* 搜索词的 AI 中文翻译小字 (有译文显示译文, 生成中显示占位) */
function termZhHtml(q, r) {
  const zh = (r && r.query_zh) || state.translations[q] || "";
  if (zh) return `<div class="term-zh" title="AI 翻译(已落库)">${esc(zh)}</div>`;
  if (state.transPending.has(q)) {
    return `<div class="term-zh pending" title="AI 翻译生成中(模型较慢, 稍候自动刷新)">翻译中…</div>`;
  }
  return "";
}

/* 搜索词列表: 同一个词给同一种淡色背景(一眼看出哪些词重复出现),
   只出现一次的词不着色(保持白底); 重复词按词排序后依次取色, 保证同词同色 */
const TERM_BG = ["#eef6ff", "#e8f7ee", "#fff8e4", "#fdeef4", "#f1eefc",
                 "#e7f8fa", "#fdf1e7", "#f1f7e6", "#eef1fb", "#fbeef0"];
function termBgMap(queries) {
  const cnt = new Map();
  (queries || []).forEach((q) => { if (q) cnt.set(q, (cnt.get(q) || 0) + 1); });
  const dup = [...cnt.keys()].filter((q) => cnt.get(q) > 1).sort();
  const m = new Map();
  dup.forEach((q, i) => m.set(q, TERM_BG[i % TERM_BG.length]));
  return m;
}
/* 用在 <td>/<span> 上的内联背景样式; 没有重复时不加(白底) */
function termBgAttr(bg) { return bg ? ` style="background:${bg}"` : ""; }

/* 搜索词打标结果: 父ASIN -> {搜索词: 标签}
   打标在详情页做, 列表页只读展示(带缓存, 取不到也记空避免反复请求) */
const termLabels = {};
const termLabelLoading = {};

async function loadTermLabels(asin) {
  const pa = String(asin || "").trim();
  if (!pa) return {};
  if (termLabels[pa]) return termLabels[pa];
  if (termLabelLoading[pa]) return termLabelLoading[pa];
  termLabelLoading[pa] = api("/api/product/term_labels?asin=" + encodeURIComponent(pa))
    .then((d) => {
      const m = {};
      Object.entries(d.rows || {}).forEach(([t, v]) => { m[t] = (v && v.label) || ""; });
      termLabels[pa] = m;
      return m;
    })
    .catch(() => { termLabels[pa] = {}; return {}; })
    .finally(() => { delete termLabelLoading[pa]; });
  return termLabelLoading[pa];
}

/* 打标单元格: 打过标显示彩色标签, 没打过显示 - */
function termLabelCell(label) {
  return label
    ? `<span class="lb-tag" data-l="${esc(label)}">${esc(label)}</span>`
    : '<span class="pmeta">-</span>';
}

/* 进度状态标签: done=完成/已拉取 · partial=部分 · none=未开始 · empty=无数据
   (文案由后端给, 前端只上色; 见 backend/term_status.py) */
function stTag(key, text) {
  const k = key || "none";
  return `<span class="st-tag" data-st="${esc(k)}">${esc(text || "")}</span>`;
}

/* 父SKU 的「素材拉取」单元: 状态标签 + 已完成/总数 */
function fetchCell(r) {
  if (!r.terms_total) {
    return `<span class="st-tag" data-st="empty" title="该父体在广告数据里没有搜索词">无搜索词</span>`;
  }
  const tip = `该父体共 ${r.terms_total} 个搜索词，已把竞品结果拉到本地 ${r.terms_fetched} 个`
    + (r.terms_fetched >= r.terms_total ? "（全部完成）"
      : "；点该行「搜索词」可看哪些还没拉取");
  return `<span title="${esc(tip)}">${stTag(r.fetch_status, r.fetch_text)}`
    + `<span class="st-num">${r.terms_fetched}/${r.terms_total}</span></span>`;
}

/* 排名列: 一列里塞 4 个名次(花费/曝光/点击/订单), 每个数一个颜色 + 单字标签
   [k, 单字, 全称, 指标字段] —— 顺序即显示顺序(2×2 网格, 前两个在上排)
   名次口径: 该词在本父体**同类行**里的名次(搜索词 / 商品投放 各排各的), 1 = 最高 */
const RANK_KEYS = [
  ["cost", "花", "花费", "adCost"],
  ["imp", "曝", "曝光", "impressions"],
  ["clk", "点", "点击", "clicks"],
  ["ord", "单", "订单", "orderNum"],
];

/* 当前视图里各指标有数据的行数(做 tooltip 里的「共 N 个」) */
function rankDenoms(rows) {
  const out = {};
  RANK_KEYS.forEach(([k, , , field]) => {
    out[k] = (rows || []).filter((r) => Number(r[field] || 0) > 0).length;
  });
  return out;
}

function rankCell(r, den) {
  const cells = RANK_KEYS.map(([k, short, label, field]) => {
    const n = Number(r["rank_" + k] || 0);
    if (!n) {
      return `<span class="rk none" data-k="${k}" title="${label}排名：该词没有${label}数据">`
        + `<i>${short}</i>-</span>`;
    }
    const d = (den || {})[k] || 0;
    const v = k === "cost" ? money(r[field]) : nf(r[field]);
    const tip = `${label}排名：第 ${n} 名${d ? `（当前视图 ${d} 个有${label}的词）` : ""}`
      + `，${label} ${v}`;
    return `<span class="rk" data-k="${k}" title="${esc(tip)}"><i>${short}</i>${n}</span>`;
  }).join("");
  return `<div class="rk-grid">${cells}</div>`;
}

/* 父SKU 的「人工打标」单元: 同上口径 */
function labelCell(r) {
  if (!r.terms_total) return '<span class="pmeta">-</span>';
  const tip = `该父体共 ${r.terms_total} 个搜索词，已人工打标 ${r.terms_labeled} 个`
    + (r.terms_labeled >= r.terms_total ? "（全部完成）"
      : "；在搜索词详情页打标，未打满则为「部分标记」");
  return `<span title="${esc(tip)}">${stTag(r.label_status, r.label_text)}`
    + `<span class="st-num">${r.terms_labeled}/${r.terms_total}</span></span>`;
}

/* 详情页保存/清除打标后, 同步进列表页缓存 */
function cacheTermLabel(asin, term, label) {
  const pa = String(asin || "").trim();
  if (!pa || !term) return;
  const m = termLabels[pa] || (termLabels[pa] = {});
  if (label) m[term] = label; else delete m[term];
}

/* 搜索词页签: 单元格渲染为「可点击 -> 打开亚马逊搜索结果页」
   搜索词下方带 AI 中文翻译(已落库的显示译文, 生成中的显示占位) */
function cellHtml(c, r) {
  const v = fmtCell(c.key, r[c.key]);
  const isTerm = state.tab === "search" && (c.key === "query" || c.key === "keywordText") && r[c.key];
  if (isTerm) {
    const q = r[c.key];
    const cached = state.amzCached.has(q);
    const tip = cached
      ? "已有本地抓取结果：点击直接查看，不会跳转亚马逊"
      : "本地没有：点击打开亚马逊搜索结果页并取回数据（约 10~25 秒）";
    return `<span class="term-cell"><span class="amz-link${cached ? " cached" : ""}" data-q="${esc(q)}"`
      + ` title="${esc(tip)}">${v}</span>${termZhHtml(q, r)}</span>`;
  }
  return v;
}

/* 打开亚马逊搜索结果页 + 用浏览器取回该页数据
   本地已有结果时不再开新标签, 直接看本地数据 */
function openAmazon(q) {
  if (!q) return;
  if (!state.amzCached.has(q)) {
    const url = `https://www.amazon.co.jp/s?k=${encodeURIComponent(q)}`;
    window.open(url, "_blank", "noopener");
  }
  showAmazon(q, false);
}

/* 拉一次「本地已抓过的搜索词」清单, 用于判断点击时要不要跳亚马逊 */
async function loadAmzCached() {
  try {
    const d = await api("/api/amazon/local?limit=5000");
    state.amzCached = new Set((d.rows || []).map((r) => r.query).filter(Boolean));
  } catch (e) { /* 忽略: 拿不到就按"没抓过"处理 */ }
  return state.amzCached;
}

/* 亚马逊搜索结果卡: 字段清单(有啥爬啥) —— 顺序即详情面板的展示顺序 */
const AMZ_FIELD_LABELS = [
  ["position", "顺位"], ["asin", "ASIN"], ["brand", "品牌"], ["title", "标题"],
  ["differentiators", "标题副文案"], ["price", "价格"], ["price_value", "价格(数值)"],
  ["price_symbol", "货币符号"], ["price_whole", "价格(整数部分)"],
  ["price_fraction", "价格(小数部分)"], ["list_price", "参考价(划线价)"],
  ["rating", "星级(原文)"], ["rating_value", "星级"],
  ["reviews", "评论数(原文)"], ["review_count", "评论数"],
  ["sponsored", "广告位"], ["ad_label", "广告标识"],
  ["points", "积分"], ["coupon", "优惠券"], ["discount", "折扣/优惠"],
  ["delivery", "配送"], ["delivery_date", "预计送达"], ["delivery_fee", "运费"],
  ["free_shipping", "免运费"], ["delivery_secondary", "备选配送"],
  ["stock", "库存/库存紧张"], ["bought_recently", "近期已购"],
  ["other_offers", "其它售卖渠道"], ["return_policy", "退货政策"],
  ["badges", "徽标/榜单"], ["add_to_cart", "可直接加购"], ["has_video", "含视频"],
  ["image_big", "大图地址"], ["image_alt", "图片 ALT"], ["url", "商品链接"],
];
/* 这些字段不走上面那张表, 由详情面板特殊渲染:
   extras → 归类标签、raw_text → 折叠原文、url_href/image → 仅作链接/缩略图用 */
const AMZ_SPECIAL_KEYS = ["extras", "raw_text", "url_href", "image"];

function amzVal(it, key) {
  const v = it[key];
  if (v === null || v === undefined || v === "" || v === false) return "";
  if (v === true) return "是";
  if (Array.isArray(v)) {
    const arr = v.map((x) => (x && typeof x === "object" ? `[${x.tag || "other"}] ${x.text}` : x))
      .filter(Boolean);
    return arr.join("　|　");
  }
  return String(v);
}

function amzDetailHtml(it) {
  const rows = AMZ_FIELD_LABELS.map(([k, label]) => {
    const v = amzVal(it, k);
    if (!v) return "";
    const body = /^https?:\/\//.test(v)
      ? `<a class="link" href="${esc(v)}" target="_blank" rel="noopener">${esc(v.slice(0, 110))}</a>`
      : esc(v.slice(0, 500));
    return `<div class="drow"><span class="dk">${esc(label)}</span><span class="dv">${body}</span></div>`;
  }).join("");
  const extras = (it.extras || [])
    .map((e) => `<span class="amz-x" title="归类: ${esc(e.tag)}">${esc(e.text)}</span>`).join("");
  const raw = it.raw_text
    ? `<details class="amz-raw"><summary>卡片原文（兜底，未归类的信息都在这儿）</summary>
         <div>${esc(it.raw_text)}</div></details>` : "";
  return `<div class="amz-detail">
    <div class="amz-detail-grid">${rows || '<div class="pmeta">该卡片未抓到任何字段</div>'}</div>
    ${extras ? `<div class="amz-xs"><span class="amz-xs-h">其它抓到的信息</span>${extras}</div>` : ""}
    ${raw}
  </div>`;
}

async function showAmazon(q, refresh) {
  const url = `https://www.amazon.co.jp/s?k=${encodeURIComponent(q)}`;
  state.amzQuery = q;
  $("amzTitle").textContent = `亚马逊搜索结果 · ${q}`;
  $("amzOpen").href = url;
  $("amzMeta").textContent = refresh ? "强制重新抓取中…" : "查询中…";
  $("amzBody").innerHTML = `<div class="amz-loading">${refresh
    ? "正在重新打开亚马逊抓取最新页面…"
    : "正在查询（本地已有数据则立即返回；否则才打开浏览器加载，约 10~25 秒）…"}</div>`;
  $("amzModal").classList.add("open");
  $("amzRefresh").disabled = true;
  updateAmzZh(q);          // 译文(有缓存立即显示, 没有则下面排队生成)
  ensureTerms([q]);        // 点击搜索词 → 自动调模型翻译(已译过的不重复调)
  try {
    const p = new URLSearchParams({ query: q });
    if (refresh) p.set("refresh", "true");
    const d = await api("/api/amazon/search?" + p.toString());
    if (d.detail) {
      $("amzBody").innerHTML = `<div class="amz-loading">抓取失败：${esc(d.detail)}</div>`;
      $("amzMeta").textContent = "失败";
      return;
    }
    if (d.url) $("amzOpen").href = d.url;
    const src = d.from_local ? "本地文件（未访问亚马逊）"
      : (d.from_cache ? "库缓存" : "实时抓取");
    const items = d.items || [];
    state.amzItems = items;
    const covered = AMZ_FIELD_LABELS
      .filter(([k]) => items.some((it) => amzVal(it, k))).length;
    const pa = (d.local_parent || (d.parents || [])[0] || "");
    $("amzMeta").textContent = (d.blocked ? "⚠ 疑似被验证码拦截 · " : "") +
      (pa ? `父ASIN ${pa} · ` : "") +
      `页面「${d.page_title || ""}」· 解析 ${d.item_count} 条 · ` +
      `抓到 ${covered} 类字段 · ${src} · 抓取于 ${d.fetched_at || "-"} · #${d.search_id || "-"}`;
    $("amzMeta").title = d.local_path ? "本地文件: " + d.local_path : "";
    if (!d.blocked) state.amzCached.add(q);      // 已落本地 → 下次点击不再跳亚马逊
    const rows = items.map((it, idx) => {
      const star = it.rating_value
        ? `<b>★${it.rating_value}</b>${it.review_count ? `<span class="amz-rev">(${nf(it.review_count)})</span>` : ""}`
        : '<span class="pmeta">-</span>';
      const price = it.price
        ? `<b>${esc(it.price)}</b>${it.list_price ? `<span class="amz-list">${esc(it.list_price)}</span>` : ""}`
        : '<span class="pmeta">-</span>';
      const perks = [
        it.points ? `<span class="amz-tag pt">${esc(it.points)}</span>` : "",
        it.coupon ? `<span class="amz-tag cp">${esc(it.coupon)}</span>` : "",
        it.discount ? `<span class="amz-tag dc">${esc(it.discount)}</span>` : "",
      ].filter(Boolean).join("");
      const badges = (it.badges || []).slice(0, 2)
        .map((b) => `<span class="amz-tag bg">${esc(b)}</span>`).join("");
      const ship = it.delivery
        ? `<span class="${it.free_shipping ? "amz-free" : ""}">${esc(it.delivery)}</span>`
        : '<span class="pmeta">-</span>';
      const stock = it.stock ? `<span class="amz-tag st">${esc(it.stock)}</span>`
        : (it.bought_recently ? `<span class="amz-tag st">${esc(it.bought_recently)}</span>` : "");
      const nExtras = (it.extras || []).length;
      // 本地已有主图就用本地文件(不联亚马逊图片 CDN), 没有才回退远程地址
      const thumb = it.image_local || it.image || "";
      const big = it.image_local || it.image_big || amzBigImage(it.image);
      return `<tr class="amz-row" data-i="${idx}">
      <td class="p">${it.position}</td>
      <td class="amz-img-td">${thumb ? `<img class="amz-thumb" src="${esc(thumb)}" loading="lazy" alt=""
          data-preview="${esc(big)}" data-fallback="${esc(thumb)}"
          data-cap="${esc(it.asin + (it.title ? " · " + it.title : ""))}">` : ""}</td>
      <td class="p">${it.sponsored ? `<span class="ad">${esc(it.ad_label || "广告")}</span>` : ""}</td>
      <td class="amz-td-title">
        ${it.brand ? `<div class="amz-brand">${esc(it.brand)}</div>` : ""}
        <a href="${esc(it.url)}" target="_blank" rel="noopener" title="${esc(it.title)}">
          ${esc(String(it.title || "").slice(0, 96))}</a>
        ${it.differentiators ? `<div class="amz-diff">${esc(it.differentiators.slice(0, 70))}</div>` : ""}
        ${perks || badges ? `<div class="amz-perks">${perks}${badges}</div>` : ""}
      </td>
      <td class="p amz-td-price">${price}</td>
      <td class="p amz-td-rate">${star}</td>
      <td class="amz-td-ship">${ship}${stock ? `<div>${stock}</div>` : ""}</td>
      <td class="p amz-td-asin">${esc(it.asin)}</td>
      <td class="amz-ops">
        <button class="btn btn-mini" data-amz-toggle="${idx}"
          title="展开该商品的全部已抓取字段">详情${nExtras ? ` ${nExtras}` : ""}</button>
      </td>
    </tr>
    <tr class="amz-detail-row" data-d="${idx}" style="display:none">
      <td colspan="9">${amzDetailHtml(it)}</td>
    </tr>`;
    }).join("");
    $("amzBody").innerHTML = rows
      ? `<table class="amz-table"><thead><tr>
           <th class="p">顺位</th><th>图</th><th class="p"></th><th>商品</th>
           <th class="p">价格</th><th class="p">评分</th><th>配送 / 库存</th>
           <th class="p">ASIN</th><th class="p">操作</th>
         </tr></thead><tbody>${rows}</tbody></table>`
      : `<div class="amz-loading">未解析到结果（页面可能未渲染完成或无结果）</div>`;

    // 缩略图 -> 大图预览 (优先用 srcset 取到的最大分辨率)
    $("amzBody").querySelectorAll("img[data-preview]").forEach((img) => img.onclick = () =>
      openImagePreview(img.dataset.preview, img.dataset.cap, img.dataset.fallback));
    // 「详情」行内展开
    $("amzBody").querySelectorAll("button[data-amz-toggle]").forEach((btn) => btn.onclick = () => {
      const i = btn.dataset.amzToggle;
      const tr = $("amzBody").querySelector(`tr[data-d="${i}"]`);
      const show = tr.style.display === "none";
      tr.style.display = show ? "" : "none";
      btn.classList.toggle("btn-primary", show);
      btn.textContent = (show ? "收起" : "详情")
        + ((state.amzItems[i].extras || []).length ? ` ${state.amzItems[i].extras.length}` : "");
    });
    const openAll = $("amzExpandAll");
    openAll.disabled = !items.length;
    openAll.onclick = () => {
      const anyHidden = [...$("amzBody").querySelectorAll(".amz-detail-row")]
        .some((tr) => tr.style.display === "none");
      $("amzBody").querySelectorAll(".amz-detail-row")
        .forEach((tr) => { tr.style.display = anyHidden ? "" : "none"; });
      $("amzBody").querySelectorAll("button[data-amz-toggle]").forEach((b, k) => {
        b.classList.toggle("btn-primary", anyHidden);
        const n = (state.amzItems[k].extras || []).length;
        b.textContent = (anyHidden ? "收起" : "详情") + (n ? ` ${n}` : "");
      });
      openAll.textContent = anyHidden ? "收起全部详情" : "展开全部详情";
    };
    openAll.textContent = "展开全部详情";
  } catch (e) {
    $("amzBody").innerHTML = `<div class="amz-loading">请求失败：${esc(e && e.message ? e.message : e)}</div>`;
    $("amzMeta").textContent = "失败";
  } finally {
    $("amzRefresh").disabled = false;
  }
}

function updateSel() {
  const n = $("bodyRow").querySelectorAll(".rowchk:checked").length;
  $("selInfo").textContent = `已选 ${n} 条`;
}

function renderPager() {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  $("pagerTotal").textContent = `共 ${state.total} 条`;
  $("pageInfo").textContent = `${state.page} / ${pages}`;
  $("jumpPage").value = state.page;
}

/* ---------- 自定义列 ---------- */
function renderColMenu() {
  const open = $("colMenu").querySelector(".pop.open") ? "open" : "";
  $("colMenu").innerHTML =
    `<button class="btn" id="btnColDrop">自定义列</button>
     <div class="pop ${open}">` +
    state.columns.map((c) =>
      `<label><input type="checkbox" data-ck="${c.key}" ${state.hidden[c.key] ? "" : "checked"}> ${c.label}</label>`).join("") +
    `</div>`;
  $("btnColDrop").onclick = (e) => {
    e.stopPropagation(); $("colMenu").querySelector(".pop").classList.toggle("open");
  };
  $("colMenu").querySelectorAll("input[data-ck]").forEach((el) => el.onchange = () => {
    state.hidden[el.dataset.ck] = !el.checked; renderTable();
  });
}

/* ---------- 导出 ---------- */
function exportCsv() {
  const cols = visibleColumns();
  const head = cols.map((c) => `"${c.label}"`).join(",");
  const body = state.rows.map((r) => cols.map((c) =>
    `"${String(r[c.key] ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob(["\ufeff" + head + "\n" + body], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${state.tab}_${Date.now()}.csv`; a.click();
  toast("已导出当前页 CSV");
}

/* ---------- 采集 ---------- */
async function doCrawl() {
  const body = {
    start_date: $("fStart").value || state.defaultStart,
    end_date: $("fEnd").value || state.today,
  };
  if (!confirm(`将采集「广告管理」下全部子菜单数据\n时间范围: ${body.start_date} ~ ${body.end_date}\n其他筛选: 全状态\n\n开始?`)) return;
  const r = await api("/api/crawl", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.started) { toast("采集已启动"); openDrawer(); startPolling(); }
  else toast(r.detail || "启动失败");
}

function openDrawer() { $("drawer").classList.add("open"); }
function startPolling() { state.polling = true; $("drawer").classList.add("open"); pollCrawl(); }

async function pollCrawl() {
  const s = await api("/api/crawl/state");
  $("drawerBody").textContent = (s.progress || []).join("\n") || "等待中...";
  $("drawerBody").scrollTop = $("drawerBody").scrollHeight;
  if (!s.running) {
    state.polling = false;
    if (s.error) toast("采集失败: " + s.error.slice(0, 60));
    else { toast("采集完成"); await loadAll(); loadStatus(); }
  }
}

async function syncCookies() {
  toast("正在从调试 Chrome 同步登录态…");
  const r = await api("/api/cookies/sync", { method: "POST" });
  if (r.saved) toast(`登录态已同步 (${r.count} 项)`); else toast(r.detail || "同步失败");
}

/* ---------- 事件绑定 ---------- */
$("btnQuery").onclick = () => { state.keyword = $("fKeyword").value.trim(); state.page = 1; loadAll(); };
$("btnReset").onclick = () => {
  $("fShop").value = ""; $("fScope").value = ""; $("fKeyword").value = "";
  applyDefaultRange();
  $("fSearchField").value = ""; $("fSearchMode").value = "blur";
  state.keyword = ""; state.searchField = ""; state.searchMode = "blur";
  state.filters = {}; state.page = 1;
  loadFilterMeta().then(loadAll);
};
$("fSearchField").onchange = (e) => { state.searchField = e.target.value; state.page = 1; loadAll(); };
$("fSearchMode").onchange = (e) => { state.searchMode = e.target.value; state.page = 1; loadAll(); };
$("fStart").onchange = () => { state.page = 1; loadAll(); };
$("fEnd").onchange = () => { state.page = 1; loadAll(); };
$("btnRefresh").onclick = () => loadAll();
$("btnCrawl").onclick = doCrawl;
$("btnSyncCookie").onclick = syncCookies;
$("btnExport").onclick = exportCsv;
$("pageSize").onchange = (e) => { state.pageSize = +e.target.value; state.page = 1; loadRecords(); };
$("prevPage").onclick = () => { if (state.page > 1) { state.page--; loadRecords(); } };
$("nextPage").onclick = () => {
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  if (state.page < pages) { state.page++; loadRecords(); }
};
$("jumpPage").onchange = (e) => { state.page = Math.max(1, +e.target.value || 1); loadRecords(); };
$("fPortfolio").oninput = loadPortfolios;
$("drawerClose").onclick = () => $("drawer").classList.remove("open");
$("amzClose").onclick = () => $("amzModal").classList.remove("open");
$("amzModal").onclick = (e) => { if (e.target.id === "amzModal") $("amzModal").classList.remove("open"); };
$("amzRefresh").onclick = () => { if (state.amzQuery) showAmazon(state.amzQuery, true); };

/* 预抓取: 把「点击量达标」的搜索词提前抓到本地(按父 ASIN 分目录)
   (串行限速 + 随机间隔 + 断点续抓; 抓完查看搜索词直接读本地, 不再访问亚马逊) */
async function startPrefetch() {
  if (!confirm("将按父 ASIN 逐个预抓取点击量达标的搜索词。\n\n"
    + "· 抓取范围: 点击 >= 2 的搜索词(商品定位已排除)\n"
    + "· 顺序: 一个父 ASIN 下的词全部抓完, 再抓下一个; 各自标记完成\n"
    + "· 落盘: <父ASIN>\\json\\<搜索词>.json、<父ASIN>\\主图\\<搜索词>\\、<父ASIN>\\_done.json\n"
    + "· 售价按日本站日元抓取(￥)\n"
    + "· 串行抓取, 每个搜索词之间随机间隔 8~15 秒, 每 20 条长休一次\n"
    + "· 已抓过的词/已完成的父 ASIN 自动跳过\n"
    + "· 耗时较长, 期间可关闭本窗口, 进度会继续\n\n开始?")) return;
  const r = await api("/api/amazon/prefetch", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  });
  if (r.detail) { toast(r.detail); return; }
  toast("预抓取已启动 → " + (r.root || "本地目录"));
  pollPrefetch();
}

async function stopPrefetch() {
  const r = await api("/api/amazon/prefetch/stop", { method: "POST" });
  toast(r.stopping ? "已请求停止, 当前这条抓完即退出" : "任务未在运行");
}

function renderPrefetch(s) {
  const btn = $("amzPrefetch");
  if (btn) btn.textContent = s.running ? "停止预抓取" : "预抓取达标搜索词";
  if (s.running || s.done) {
    const loc = s.local || {};
    $("amzMeta").textContent =
      `预抓取 父ASIN ${s.parents_done || 0}/${s.parents_total || 0}`
      + ` · 词 ${s.done}/${s.total}(点击>=${s.min_clicks})`
      + ` · 新抓 ${s.ok} · 跳过 ${s.skipped} · 失败 ${s.failed}`
      + ` · 主图 +${s.img_new || 0}`
      + ` · 本地 ${loc.terms || 0}词/${loc.images || 0}图`
      + (s.parent ? ` · 当前父ASIN ${s.parent}` : "")
      + (s.current ? `「${s.current}」` : "");
    $("amzMeta").title = s.root || "";
  }
}

async function pollPrefetch() {
  const tick = async () => {
    let s;
    try { s = await api("/api/amazon/prefetch/state"); } catch (e) { return; }
    state.prefetchRunning = !!s.running;
    renderPrefetch(s);
    if (!s.running) {
      clearInterval(state.prefetchTimer); state.prefetchTimer = null;
      toast(`预抓取结束: 新抓 ${s.ok} · 跳过 ${s.skipped} · 失败 ${s.failed}`);
      loadAmzCached().then(() => { if (state.tab === "search") renderTable(); });
      if (state.amzQuery && $("amzModal").classList.contains("open")) {
        showAmazon(state.amzQuery, false);      // 重新读一次, 现在应命中本地文件
      }
    }
  };
  if (state.prefetchTimer) return;
  state.prefetchTimer = setInterval(tick, 2000);
  tick();
}

$("amzPrefetch").onclick = () => (state.prefetchRunning ? stopPrefetch() : startPrefetch());

/* 打开页面时同步一次预抓取状态: 后台正在跑(比如服务端/命令行启的)就自动开始显示进度 */
async function initPrefetchState() {
  try {
    const s = await api("/api/amazon/prefetch/state");
    state.prefetchRunning = !!s.running;
    renderPrefetch(s);
    if (s.running) pollPrefetch();
  } catch (e) { /* 忽略: 不影响主流程 */ }
}

/* ===================================================================
   拉取全部搜索词素材 (产品视角工具栏)
   复用后台预抓取任务, 但两个参数必须改:
     min_clicks = 0            不限点击量 —— 「所有搜索词」
     skip_done_parents = false 不整组跳过「已标记完成」的父体
       (那是上一轮只抓「点击≥2」时打的完成标记, 组里仍有无点击量的词没抓,
        跳过就永远补不上)
   素材 = 该词的亚马逊搜索结果 JSON(标题/价格/评分/ASIN/顺位...) + 结果页各竞品主图,
   落到本地仓库; 已抓过的词按 exists() 断点跳过, 只补缺的。
   =================================================================== */
async function startFetchAll() {
  let tg;
  try {
    tg = await api("/api/amazon/targets?min_clicks=0&limit=1");
  } catch (e) { toast("取不到抓取目标: " + e.message); return; }
  const sm = (tg && tg.summary) || {};
  const todo = sm.todo_terms != null ? sm.todo_terms : (sm.terms || 0);
  const mins = Math.max(1, Math.round(todo * 30 / 60));
  if (!confirm(
    `把全部搜索词（不限点击量）的亚马逊竞品素材拉到本地。\n\n`
    + `· 目标: ${sm.terms} 个搜索词 / ${sm.parents} 个父 ASIN\n`
    + `· 本地已有 ${sm.local_terms} 个会自动跳过，本次实际要抓 ${todo} 个\n`
    + `· 落盘: <父ASIN>\\json\\<搜索词>.json + <父ASIN>\\主图\\<搜索词>\\<顺位>_<ASIN>.jpg\n`
    + `· 结果页各竞品主图全下（售价按日本站日元）\n`
    + `· 串行抓取, 词间随机间隔 8~15 秒, 每 20 条长休 1 分钟（反爬, 期间别关调试 Chrome）\n`
    + `· 粗估 ${mins} 分钟以上；可关闭本页面, 后台继续跑\n\n开始?`)) return;
  let r;
  try {
    r = await api("/api/amazon/prefetch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_clicks: 0, skip_done_parents: false }),
    });
  } catch (e) { toast("启动失败: " + e.message); return; }
  if (r.detail) { toast(r.detail); return; }        // 例如「已有抓取在跑」
  toast(`已开始拉取: 待抓 ${todo} 个搜索词, 进度看工具栏`);
  state.fetchRunning = true;
  pollFetchAll();
}

async function stopFetchAll() {
  const r = await api("/api/amazon/prefetch/stop", { method: "POST" });
  toast(r.stopping ? "已请求停止，当前这条抓完即退出" : "任务未在运行");
}

/* 进度显示: 与预抓取共用 /api/amazon/prefetch/state */
function renderFetchState(s) {
  const btn = $("pFetchAll");
  if (btn) btn.textContent = (s && s.running) ? "停止拉取" : "拉取全部搜索词素材";
  const el = $("pFetchState");
  if (!el) return;
  if (!s) { el.textContent = ""; el.title = ""; return; }
  if (s.running) {
    el.innerHTML = `拉取中 · 父体 <b>${s.parents_done || 0}/${s.parents_total || 0}</b>`
      + ` · 词 <b>${s.done || 0}/${s.total || 0}</b>`
      + ` · 新抓 ${s.ok || 0} 跳过 ${s.skipped || 0} 失败 ${s.failed || 0}`
      + (s.current ? ` · 当前「${esc(s.current)}」` : "");
    el.title = (s.parent ? `当前父 ASIN ${s.parent}` : "") + (s.root ? `\n落盘目录 ${s.root}` : "");
  } else if (s.error) {
    el.innerHTML = `<span class="fetch-err">拉取失败: ${esc(s.error)}</span>`;
    el.title = s.error;
  } else if (s.finished_at) {
    el.innerHTML = `拉取结束 · 新抓 <b>${s.ok || 0}</b> 跳过 ${s.skipped || 0}`
      + ` 失败 ${s.failed || 0} · 主图新增 ${s.img_new || 0} 张 · ${esc(s.finished_at)}`;
    el.title = "";
  }
}

async function pollFetchAll() {
  const tick = async () => {
    let s;
    try { s = await api("/api/amazon/prefetch/state"); } catch (e) { return; }
    state.fetchRunning = !!s.running;
    state.fetchState = s;
    renderFetchState(s);
    if (!s.running) {
      clearInterval(state.fetchTimer); state.fetchTimer = null;
      if (s.error) toast("拉取失败: " + s.error);
      else toast(`拉取结束: 新抓 ${s.ok} · 跳过 ${s.skipped} · 失败 ${s.failed}`);
      loadProducts();       // 刷新「素材拉取」列(状态标签要跟着变)
    }
  };
  if (state.fetchTimer) return;
  state.fetchTimer = setInterval(tick, 3000);
  tick();
}

/* 打开页面就同步一次(后台正在跑就自动接着显示进度) */
async function initFetchState() {
  try {
    const s = await api("/api/amazon/prefetch/state");
    state.fetchState = s; state.fetchRunning = !!s.running;
    renderFetchState(s);
    if (s.running) pollFetchAll();
  } catch (e) { /* 忽略: 不影响主流程 */ }
}

/* 图片大图预览 (产品视角/在线产品 的缩略图)
   赛狐给的主图是 75px 缩略图(URL 里带 ._SL75_ 之类的尺寸后缀)。
   亚马逊的图片服务支持「去掉尺寸后缀取原图」, 但**不接受任意尺寸**(._SL1000_ 会 400),
   所以这里直接把尺寸后缀整段去掉。 */
function amzBigImage(u) {
  if (!u) return "";
  return String(u).replace(/\._[^\/.]+\.(jpg|jpeg|png|webp|gif)$/i, ".$1");
}

function openImagePreview(url, caption, fallback) {
  if (!url) return;
  const box = $("imgPreview");
  const img = $("imgPreviewImg");
  img.onerror = () => {
    if (fallback && img.src !== fallback) { img.onerror = null; img.src = fallback; }
  };
  img.src = url;
  $("imgPreviewCap").textContent = caption || "";
  box.classList.add("open");
}
function closeImagePreview() {
  const box = $("imgPreview");
  if (!box) return;
  box.classList.remove("open");
  $("imgPreviewImg").removeAttribute("src");
}
$("imgPreview").onclick = closeImagePreview;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeImagePreview(); });
document.addEventListener("click", (e) => {
  if (!e.target.closest("#colMenu")) {
    const pop = $("colMenu").querySelector(".pop"); if (pop) pop.classList.remove("open");
  }
  if (!e.target.closest(".dd")) {
    document.querySelectorAll(".dd-pop.open").forEach((p) => p.classList.remove("open"));
  }
});
$("fShop").onchange = () => { state.page = 1; state.filters = {}; loadFilterMeta().then(loadAll); };
$("fScope").onchange = () => { state.page = 1; loadAll(); };

/* ===================================================================
   产品视角: 产品(ASIN) -> 广告活动 -> 搜索词(-> 亚马逊搜索结果) 逐级下钻
   =================================================================== */
const pvSel = new Set();                       // 勾选的父 ASIN (批量关联度分析)
let rbPoll = null;                             // 批量分析轮询器

const pv = {
  level: 1, product: null, campaign: null, dim: "",
  kidAsins: [], campaigns: [], terms: [], rows: [], snapshot: {}, totals: {},
  parentTerms: false, ptMeta: null,
  kidParent: "", kidAll: [], kidAdvSet: new Set(),
  termCounts: null,                              // 搜索词视图三个口径的条目数
  keyword: "", orderField: "ad_cost", orderDir: "desc",
  cSort: { field: "ad_cost", dir: "desc" },      // 活动列表(前端排序)
  tSort: { field: "adCost", dir: "desc" },       // 搜索词列表(前端排序)
  page: 1, pageSize: 50, total: 0, loaded: false,
  aiLabels: {},                                  // 父ASIN -> AI 品名(模型解析标题)
};

const nf = (v) => {
  const n = Number(v);
  if (!isFinite(n)) return "";
  return Number.isInteger(n) ? n.toLocaleString("en-US")
    : n.toLocaleString("en-US", { maximumFractionDigits: 2 });
};
const money = (v) => "¥" + Number(v || 0).toLocaleString("en-US",
  { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pctf = (v) => (Number(v) ? (Number(v) * 100).toFixed(2) + "%" : "-");
const stBadge = (v) => {
  const m = STATE_MAP[v];
  return m ? `<span class="badge ${m[1]}">${m[0]}</span>` : esc(v);
};

/* ---------- 表头点击排序 ----------
   一级(产品列表)按 state.orderField/orderDir 走后端排序;
   二/三级(活动、搜索词)数据已整体载入, 直接前端排序(state.field/state.dir)。
   指示箭头用 th::after 绘制, 不写进 DOM 文本(避免污染 inner_text 与列宽)。 */
const SORT_TYPES = {
  /* 产品列表 */
  parent_asin: "text", sku: "text", title: "text",
  all_child_count: "num", child_count: "num", campaign_count: "num",
  active_campaign_count: "num", term_campaign_count: "num", search_term_count: "num",
  keyword_term_count: "num", targeting_term_count: "num",
  ad_cost: "num", impressions: "num", clicks: "num", order_num: "num",
  ad_sales: "num", ctr: "num", acos: "num", cvr: "num",
  /* 活动列表 */
  campaignName: "text", state: "text", adType: "text", startDate: "text",
  strategy: "text", dailyBudget: "num",
  /* 搜索词列表 */
  dimension: "text", query: "text", matchType: "text",
  adCost: "num", orderNum: "num", searchFrequencyRank: "num",
};

function sortableTh(state, o) {
  const type = o.type || SORT_TYPES[o.field] || "num";
  const active = state.field === o.field;
  const st = active ? (state.dir === "asc" ? " sort-asc" : " sort-desc") : "";
  const cls = o.cls ? `class="${o.cls} th-sortable${st}"` : `class="th-sortable${st}"`;
  const t = o.title ? ` title="${esc(o.title)}"` : "";
  const en = o.en ? `<span class="th-en">(${esc(o.en)})</span>` : "";
  // 可累加列在表头直接给出合计(值是格式化好的字符串)
  const tot = o.total != null && o.total !== ""
    ? `<span class="th-total" title="合计：${esc(o.totalTip || o.total)}">Σ ${esc(o.total)}</span>` : "";
  return `<th ${cls} data-sort="${o.field}" data-stype="${type}"`
    + ` data-sdir="${o.dir || (type === "text" ? "asc" : "desc")}"${t}>${o.label}${en}${tot}</th>`;
}

function bindSortHeaders(root, state, onSort) {
  root.querySelectorAll("th[data-sort]").forEach((th) => {
    th.classList.add("th-sortable");
    if (!th.title) th.title = "点击排序";     // 别覆盖列自己的说明(title 里已含合计口径)
    th.onclick = () => {
      const k = th.dataset.sort;
      if (state.field === k) state.dir = state.dir === "asc" ? "desc" : "asc";
      else {
        state.field = k;
        state.dir = th.dataset.sdir || (th.dataset.stype === "text" ? "asc" : "desc");
      }
      onSort();
    };
  });
}

/* 合计(客户端, 用于二/三级表: 数据已整体载入) */
function sumBy(rows, field) {
  return rows.reduce((a, r) => a + (Number(r[field]) || 0), 0);
}
const ratio = (a, b) => (b ? a / b : 0);

/* 前端排序(用于二/三级表): 数值列按数值, 文本列按本地化字符串 */
function sortRows(arr, field, dir, type) {
  const sgn = dir === "asc" ? 1 : -1;
  const num = (v) => { const n = Number(v); return isFinite(n) ? n : -Infinity; };
  return arr.slice().sort((a, b) => {
    if ((type || SORT_TYPES[field]) === "text") {
      return String(a[field] == null ? "" : a[field])
        .localeCompare(String(b[field] == null ? "" : b[field])) * sgn;
    }
    const x = num(a[field]), y = num(b[field]);
    return x === y ? 0 : (x < y ? -1 : 1) * sgn;
  });
}

function showView(v) {
  document.querySelectorAll("#viewSwitch .seg-btn")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  $("tableView").style.display = v === "table" ? "" : "none";
  $("productView").style.display = v === "product" ? "" : "none";
  $("onlineView").style.display = v === "online" ? "" : "none";
  $("settingsView").style.display = v === "settings" ? "" : "none";
  if (v === "product" && !pv.loaded) { pv.loaded = true; goProducts(); }
  if (v === "online" && !ol.loaded) { ol.loaded = true; showOnline(); }
  if (v === "settings") { loadSettings(); }
}

function renderCrumbs() {
  const parts = [`<span class="cb ${pv.level === 1 ? "cur" : ""}" data-lv="1">产品列表<span class="cmeta"> ${pv.total || ""}</span></span>`];
  if (pv.product) {
    const pa = pv.product.parent_asin || pv.product.asin || "";
    const adv = (pv.product.child_asins || []).length;      // 有广告投放的子体
    const all = (pv.kidAsins || []).length || adv;          // 全部子体
    const label = all > 1 ? `${pa}（${all > adv ? adv + "/" + all : all} 个子体）` : pa;
    parts.push(`<span class="sep">›</span><span class="cb ${pv.level === 2 ? "cur" : ""}" data-lv="2">${esc(label)}</span>`);
  }
  if (pv.campaign) {
    const nm = String(pv.campaign.campaignName || pv.campaign.campaignId || "");
    parts.push(`<span class="sep">›</span><span class="cb cur">${esc(nm.slice(0, 34))}</span>`);
  }
  if (pv.parentTerms) {
    parts.push(`<span class="sep">›</span><span class="cb cur">搜索词（全部活动）</span>`);
  }
  $("pCrumbs").innerHTML = parts.join("");
  $("pCrumbs").querySelectorAll(".cb[data-lv]").forEach((el) => el.onclick = () => {
    const lv = +el.dataset.lv;
    if (lv === 1) { resetToProducts(); }
    else if (lv === 2 && pv.product) { loadCampaigns(pv.product.parent_asin || pv.product.asin); }
  });
}

function resetToProducts() {
  pv.level = 1; pv.product = null; pv.campaign = null; pv.dim = ""; pv.kidAsins = [];
  pv.parentTerms = false; pv.ptMeta = null;
  renderCrumbs(); renderProducts();
}

/* ---------- 复制 / 子 ASIN 弹层 ---------- */
function copyText(text, label) {
  const val = String(text == null ? "" : text);
  if (!val) return;
  const done = () => toast(`已复制 ${label || ""}${val}`);
  const fallback = () => {
    const ta = document.createElement("textarea");
    ta.value = val; ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:-1000px;opacity:0";
    document.body.appendChild(ta); ta.select();
    try { document.execCommand("copy"); done(); }
    catch (e) { toast("复制失败，请手动选中后复制"); }
    document.body.removeChild(ta);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(val).then(done).catch(fallback);
  } else fallback();
}

function openKidModal(pa, adv, all, title) {
  const advSet = new Set(adv || []);
  const ordered = [...(adv || []), ...(all || []).filter((a) => !advSet.has(a))];
  pv.kidParent = pa; pv.kidAll = ordered; pv.kidAdvSet = advSet;
  $("kidTitle").textContent = `子 ASIN · 父 ${pa}`;
  const m = $("kidMeta");
  m.textContent = `共 ${ordered.length} 个子 ASIN，其中 ${advSet.size} 个正在投放广告`;
  m.title = title ? String(title) : "";
  $("kidFilter").value = "";
  renderKidList("");
  $("kidModal").classList.add("open");
  window.setTimeout(() => $("kidFilter").focus(), 30);
}

function renderKidList(kw) {
  const q = (kw || "").trim().toLowerCase();
  const advSet = pv.kidAdvSet || new Set();
  const list = (pv.kidAll || []).filter((a) => !q || String(a).toLowerCase().includes(q));
  if (!list.length) {
    $("kidBody").innerHTML = `<div class="kid-none">没有匹配的子 ASIN</div>`;
    return;
  }
  $("kidBody").innerHTML = `<div class="kid-grid">` + list.map((a) => `
    <div class="kid-item${advSet.has(a) ? " ad" : ""}">
      <span class="kid-asin" title="${esc(a)}">${esc(a)}</span>
      ${advSet.has(a) ? '<span class="kid-tag">投放中</span>' : ""}
      <span class="kid-acts">
        <button class="btn btn-mini" data-copy="${esc(a)}" title="复制 ASIN">复制</button>
        <a class="btn btn-mini btn-primary" href="https://www.amazon.co.jp/dp/${esc(a)}"
           target="_blank" rel="noopener" title="在亚马逊打开该 ASIN 页面">跳转</a>
      </span>
    </div>`).join("") + `</div>`;
  $("kidBody").querySelectorAll("button[data-copy]").forEach((b) =>
    b.onclick = () => copyText(b.dataset.copy, "子 ASIN "));
}

$("kidClose").onclick = () => $("kidModal").classList.remove("open");
$("kidModal").onclick = (e) => { if (e.target.id === "kidModal") $("kidModal").classList.remove("open"); };
$("kidFilter").oninput = (e) => renderKidList(e.target.value);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("kidModal").classList.remove("open");
});

/* ---------- Level 1: 产品列表 ---------- */
async function goProducts() {
  pv.level = 1; pv.product = null; pv.campaign = null; pv.kidAsins = [];
  await loadProducts();
}

async function loadProducts() {
  const q = new URLSearchParams({
    keyword: pv.keyword, order_field: pv.orderField, order_dir: pv.orderDir,
    page: pv.page, page_size: pv.pageSize,
  });
  const d = await api("/api/product/list?" + q.toString());
  pv.rows = d.rows; pv.total = d.total; pv.snapshot = d.snapshot || {};
  pv.totals = d.totals || {};
  renderCrumbs(); renderProducts();
}

function renderProducts() {
  const sum = pv.rows.reduce((a, r) => {
    a.cost += r.ad_cost; a.imp += r.impressions; a.clk += r.clicks;
    a.ord += r.order_num; a.sales += r.ad_sales; a.terms += r.search_term_count; return a;
  }, { cost: 0, imp: 0, clk: 0, ord: 0, sales: 0, terms: 0 });
  // 表头排序视图模型(后端按 orderField/orderDir 排序, 这里做一层字段名适配)
  const sort1 = { field: pv.orderField, dir: pv.orderDir };

  $("pBar").innerHTML =
    `<div class="pline">
      <div class="p-tools">
        <span class="ptitle">产品列表</span>
        <input type="text" id="pKw" placeholder="搜索 父/子 ASIN / 标题 / SKU" value="${esc(pv.keyword)}">
        <button class="btn" id="pQuery">查询</button>
        <button class="btn btn-primary" id="btnRelBatch" ${pvSel.size ? "" : "disabled"}>
          批量关联度分析${pvSel.size ? `（已选 ${pvSel.size}）` : ""}</button>
        <button class="btn" id="pFetchAll"
          title="把全部搜索词（不限点击量）的亚马逊竞品素材拉到本地：结果 JSON + 各竞品主图；抓过的自动跳过、断点续抓">拉取全部搜索词素材</button>
        <span class="pmeta" id="pFetchState"></span>
        <span class="pmeta">以<b>父 ASIN</b> 为一条记录；同一父体下的多个子 ASIN 指标合并统计。勾选后可批量分析搜索词关联度</span>
      </div>
    </div>
    <div class="p-sum">
      <span>父体 <b>${pv.total}</b></span>
      <span>本页投放子体 <b>${pv.rows.reduce((a, r) => a + (r.child_count || 1), 0)}</b></span>
      <span>本页参与活动 <b>${pv.rows.reduce((a, r) => a + r.campaign_count, 0)}</b></span>
      <span>本页搜索词条目 <b>${nf(sum.terms)}</b></span>
      <span>本页花费 <b>${money(sum.cost)}</b></span>
      <span>本页曝光 <b>${nf(sum.imp)}</b></span>
      <span>本页点击 <b>${nf(sum.clk)}</b></span>
      <span>本页订单 <b>${nf(sum.ord)}</b></span>
      <span class="kid-legend"><i class="dot-ad"></i>子ASIN 蓝底＝已投广告（排在最前）</span>
      <span class="pmeta sort-hint">点击表头可排序（再点一次切换升降序）</span>
      ${pv.snapshot && pv.snapshot.range_end
        ? `<span class="pmeta">快照区间 ${esc(pv.snapshot.range_start)} ~ ${esc(pv.snapshot.range_end)}</span>`
        : ""}
    </div>`;
  $("pQuery").onclick = () => { pv.keyword = $("pKw").value.trim(); pv.page = 1; loadProducts(); };
  $("pKw").onkeydown = (e) => { if (e.key === "Enter") $("pQuery").click(); };
  const fa = $("pFetchAll");
  if (fa) fa.onclick = () => (state.fetchRunning ? stopFetchAll() : startFetchAll());
  renderFetchState(state.fetchState);      // 页面重画时把拉取进度补回来

  const rows = pv.rows.map((r) => {
    const pa = r.parent_asin || r.asin;
    const adv = r.child_asins || [];                 // 有广告投放的子 ASIN
    const all = r.all_child_asins || adv;            // 全部子 ASIN(含未投放)
    const advSet = new Set(adv);
    // 有广告的排在最前, 其余变体排在后面
    const ordered = [...adv, ...all.filter((a) => !advSet.has(a))];
    // 子 ASIN: 纯展示(点击整格打开弹层), 有广告的蓝底
    const kidHtml = ordered.length
      ? `<span class="asin-kid-list">` +
        ordered.map((a) => `<span class="asin-kid${advSet.has(a) ? " ad" : ""}">${esc(a)}</span>`)
          .join('<span class="asin-kid-sep">·</span>') + `</span>`
      : '<span class="pmeta">-</span>';
    const tip = ordered.length
      ? `有广告投放 ${adv.length} 个 / 共 ${ordered.length} 个：` + ordered.join(", ")
        + "（点击查看全部并跳转）"
      : "该父体下暂无子 ASIN（无子体数据）";
    return `<tr data-asin="${esc(pa)}">
      <td class="col-chk"><input type="checkbox" class="pv-chk" data-pa="${esc(pa)}"
        ${pvSel.has(pa) ? "checked" : ""}></td>
      <td class="col-img">${r.img_url ? `<img class="prod-thumb" src="${esc(r.img_url)}" loading="lazy" alt=""
            data-preview="${esc(amzBigImage(r.img_url))}" data-fallback="${esc(r.img_url)}"
            data-cap="${esc(pa + (r.title ? " · " + r.title : ""))}"
            onerror="this.style.visibility='hidden'">` : ""}</td>
      <td class="col-asin" title="点击 ASIN 复制，点 ↗ 跳转亚马逊">
        <span class="asin-parent-box">
          <span class="asin-copy" data-copy="${esc(pa)}" title="点击复制父 ASIN：${esc(pa)}">${esc(pa)}</span>
          <a class="btn-jump" href="https://www.amazon.co.jp/dp/${esc(pa)}" target="_blank"
             rel="noopener" title="在亚马逊打开该父 ASIN 页面">↗</a>
        </span>
      </td>
      <td class="col-sku cell-ellip" title="父SKU：${esc(r.sku || "-")}">${esc(r.sku || "-")}</td>
      <td class="col-name">${pvAiHtml(pa)}</td>
      <td class="col-fetch">${fetchCell(r)}</td>
      <td class="col-mark">${labelCell(r)}</td>
      <td class="col-kid kid-cell" data-kid="${esc(pa)}" title="${esc(tip)}">${kidHtml}</td>
      <td class="col-title p-title-cell" title="${esc(r.title)}">${esc(r.title || "")}</td>
      <td class="col-price num" title="父ASIN售价 = 该父体下 ${r.price_child_count || 0} 个子体售价的中位数">${r.price_median ? money(r.price_median) : "-"}</td>
      <td class="col-kids num" title="有广告投放 ${adv.length} 个 / 全部子体 ${ordered.length} 个">
        ${adv.length}<span class="kid-ratio">/${ordered.length}</span></td>
      <td class="col-camp num">${r.campaign_count}</td>
      <td class="col-terms num">${r.term_campaign_count}</td>
      <td class="col-items num" title="搜索词条目 ${nf(r.search_term_count)}">${nf(r.search_term_count)}</td>
      <td class="col-cost num" title="花费 ${money(r.ad_cost)}">${money(r.ad_cost)}</td>
      <td class="col-imp num" title="曝光 ${nf(r.impressions)}">${nf(r.impressions)}</td>
      <td class="col-clk num" title="点击 ${nf(r.clicks)}">${nf(r.clicks)}</td>
      <td class="col-ord num" title="订单 ${nf(r.order_num)}">${nf(r.order_num)}</td>
      <td class="col-ctr num" title="点击率 ${pctf(r.ctr)}">${pctf(r.ctr)}</td>
      <td class="col-acos num" title="广告成本销售比 ${r.acos ? pctf(r.acos) : "-"}">${r.acos ? pctf(r.acos) : "-"}</td>
      <td class="col-ops">
        <div class="ops-btns">
          <button class="btn btn-mini btn-primary" data-act="campaigns" data-pa="${esc(pa)}"
                  title="查看该父体参与的全部广告活动">活动</button>
          <button class="btn btn-mini" data-act="pterms" data-pa="${esc(pa)}"
                  title="查看该父体全部活动的搜索词（${nf(r.search_term_count)} 条）">搜索词</button>
        </div>
      </td>
    </tr>`; }).join("");

  // 表头「合计」: 一级用后端 totals(当前查询的全部父体, 非仅本页); 比率类用总量重算
  const T1 = pv.totals || {};
  const tip1 = `当前查询全部 ${T1.parent_count != null ? T1.parent_count : pv.total} 个父体`;
  $("pWrap").innerHTML = `<table class="tbl-products"><thead><tr>
      <th class="col-chk"><input type="checkbox" id="pvCheckAll"
        title="全选/全不选本页"></th>
      <th class="col-img">图</th>
      ${sortableTh(sort1, { cls: "col-asin", label: "父ASIN", field: "parent_asin",
        title: "点击按父 ASIN 排序" })}
      ${sortableTh(sort1, { cls: "col-sku", label: "父SKU", field: "sku",
        title: "点击按父 SKU 排序" })}
      <th class="col-name" title="该父体的「品名」= 模型解析商品标题得到的中文短语（≤5 词）；模型可在右上角「配置」里切换">品名</th>
      <th class="col-fetch" title="竞品素材拉取进度：该父体的搜索词是否已把亚马逊竞品结果拉到本地。
全部拉完 = 拉取完成；拉了一部分 = 部分拉取；一个没拉 = 未拉取。括号内为 已拉取/搜索词总数">素材拉取<span class="th-en">(Assets)</span></th>
      <th class="col-mark" title="人工打标进度：该父体的搜索词是否已在搜索词详情页打过相关度标。
全部打完 = 标记完成；打了一部分 = 部分标记；一个没打 = 未标记。括号内为 已打标/搜索词总数">打标<span class="th-en">(Labeled)</span></th>
      <th class="col-kid" title="蓝底 = 正在投放广告的子 ASIN（已排在最前）；灰字 = 未投放广告的变体。点击单元格查看全部并跳转">子ASIN</th>
      ${sortableTh(sort1, { cls: "col-title", label: "标题", field: "title",
        title: "点击按标题排序" })}
      ${sortableTh(sort1, { cls: "col-price num", label: "售价", en: "Price(median)",
        field: "price_median", total: T1.price_median ? money(T1.price_median) : "",
        title: "父 ASIN 售价 = 该父体下全部子体售价的中位数；点击按售价排序",
        totalTip: `当前查询全部父体的售价中位数` })}
      ${sortableTh(sort1, { cls: "col-kids num", label: "子体数", en: "Variations",
        field: "all_child_count", total: nf(T1.all_child_count),
        title: "点击按全部子 ASIN 数排序（含未投放）",
        totalTip: `${tip1} 的子 ASIN 总数` })}
      ${sortableTh(sort1, { cls: "col-camp num", label: "关联活动", en: "Campaigns",
        field: "campaign_count", total: nf(T1.campaign_count),
        title: "点击按关联活动数排序", totalTip: `${tip1} 的「父体×活动」行数之和` })}
      ${sortableTh(sort1, { cls: "col-terms num", label: "有词活动", en: "w/ Terms",
        field: "term_campaign_count", total: nf(T1.term_campaign_count),
        title: "点击按有搜索词的活动数排序",
        totalTip: `${tip1} 的有搜索词活动数之和` })}
      ${sortableTh(sort1, { cls: "col-items num", label: "搜索词条目", en: "Search Terms",
        field: "search_term_count", total: nf(T1.search_term_count),
        title: "点击按搜索词条目数排序",
        totalTip: `${tip1} 的搜索词条目之和` })}
      ${sortableTh(sort1, { cls: "col-cost num", label: "花费", en: "Spend",
        field: "ad_cost", total: money(T1.ad_cost),
        title: "点击按花费排序", totalTip: `${tip1} 的花费合计` })}
      ${sortableTh(sort1, { cls: "col-imp num", label: "曝光", en: "Impressions",
        field: "impressions", total: nf(T1.impressions),
        title: "点击按曝光排序", totalTip: `${tip1} 的曝光合计` })}
      ${sortableTh(sort1, { cls: "col-clk num", label: "点击", en: "Clicks",
        field: "clicks", total: nf(T1.clicks),
        title: "点击按点击排序", totalTip: `${tip1} 的点击合计` })}
      ${sortableTh(sort1, { cls: "col-ord num", label: "订单", en: "Orders",
        field: "order_num", total: nf(T1.order_num),
        title: "点击按订单排序", totalTip: `${tip1} 的订单合计` })}
      ${sortableTh(sort1, { cls: "col-ctr num", label: "点击率", en: "CTR",
        field: "ctr", total: pctf(T1.ctr),
        title: "点击按点击率排序",
        totalTip: `合计点击 ÷ 合计曝光（不是各行 CTR 相加）` })}
      ${sortableTh(sort1, { cls: "col-acos num", label: "广告成本销售比", en: "ACOS",
        field: "acos", total: T1.acos ? pctf(T1.acos) : "-",
        title: "点击按 ACOS 排序",
        totalTip: `合计花费 ÷ 合计销售额（不是各行 ACOS 相加）` })}
      <th class="col-ops">操作</th>
    </tr></thead><tbody>${rows || ""}</tbody></table>` +
    (rows ? "" : `<div class="empty">没有匹配的产品</div>`) + pagerHtml();

  bindSortHeaders($("pWrap"), sort1, () => {
    pv.orderField = sort1.field; pv.orderDir = sort1.dir; pv.page = 1; loadProducts();
  });

  // 点击缩略图 -> 预览大图
  $("pWrap").querySelectorAll("img[data-preview]").forEach((img) => img.onclick = () =>
    openImagePreview(img.dataset.preview, img.dataset.cap, img.dataset.fallback));
  // 勾选父 SKU -> 批量关联度分析
  $("pWrap").querySelectorAll(".pv-chk").forEach((c) => c.onchange = () => {
    const pa = c.dataset.pa;
    if (c.checked) pvSel.add(pa); else pvSel.delete(pa);
    syncRelBatchBtn();
  });
  const ca2 = $("pvCheckAll");
  if (ca2) ca2.onchange = () => {
    $("pWrap").querySelectorAll(".pv-chk").forEach((c) => {
      c.checked = ca2.checked;
      if (ca2.checked) pvSel.add(c.dataset.pa); else pvSel.delete(c.dataset.pa);
    });
    syncRelBatchBtn();
  };
  const rbBtn = $("btnRelBatch");
  if (rbBtn) rbBtn.onclick = openRelBatch;
  // 点击父 ASIN 文本 -> 复制
  $("pWrap").querySelectorAll(".asin-copy[data-copy]").forEach((el) => el.onclick = () => {
    copyText(el.dataset.copy, "父 ASIN ");
    el.classList.add("copied");
    setTimeout(() => el.classList.remove("copied"), 1200);
  });
  // 点击「子ASIN」单元格 -> 打开子 ASIN 弹层
  $("pWrap").querySelectorAll(".kid-cell[data-kid]").forEach((td) => td.onclick = () => {
    const pa = td.dataset.kid;
    const p = pv.rows.find((x) => String(x.parent_asin || x.asin) === pa);
    if (!p) return;
    openKidModal(pa, p.child_asins || [], p.all_child_asins || p.child_asins || [], p.title);
  });
  // 操作列按钮
  $("pWrap").querySelectorAll('button[data-act="campaigns"]').forEach((btn) => btn.onclick = () => {
    const pa = btn.dataset.pa;
    const p = pv.rows.find((x) => String(x.parent_asin || x.asin) === pa);
    loadCampaigns(pa, p);
  });
  // 「搜索词」→ 在新页面打开该父体的搜索词列表(页面内点词再弹新页签)
  $("pWrap").querySelectorAll('button[data-act="pterms"]').forEach((btn) => btn.onclick = () => {
    openTermsPage(btn.dataset.pa);
  });
  // 品名: 本页父 ASIN 逐个解析(已解析过的直接取缓存, 不重复调模型)
  ensurePvLabels(pv.rows.map((r) => r.parent_asin || r.asin).filter(Boolean));
  bindPager();
}

/* ---------- Level 2: 某父体的广告活动 (多子 ASIN 合并) ---------- */
async function loadCampaigns(asin, product) {
  pv.level = 2;
  const parent = (product && (product.parent_asin || product.asin)) || asin;
  pv.product = product || pv.product || { parent_asin: parent, asin: parent };
  if (!pv.product.parent_asin) pv.product.parent_asin = parent;
  const d = await api("/api/product/campaigns?asin=" + encodeURIComponent(parent));
  pv.campaigns = d.rows || []; pv.campaign = null;
  pv.kidAsins = d.child_asins || [];
  renderCrumbs(); renderCampaigns();
}

function renderCampaigns() {
  const P = pv.product || {};
  const rows = sortRows(pv.campaigns, pv.cSort.field, pv.cSort.dir);
  const kidsAll = pv.kidAsins || P.child_asins || [];
  const advN = (P.child_asins || []).length;
  const tot = rows.reduce((a, r) => {
    a.cost += r.ad_cost; a.imp += r.impressions; a.clk += r.clicks;
    a.ord += r.order_num; a.sales += r.ad_sales; a.terms += r.search_term_count; return a;
  }, { cost: 0, imp: 0, clk: 0, ord: 0, sales: 0, terms: 0 });

  $("pBar").innerHTML =
    `<div class="pline">
      <div class="p-tools">
        <img class="prod-thumb" src="${esc(P.img_url || "")}" alt="" onerror="this.style.visibility='hidden'">
        <span class="ptitle">${esc(P.parent_asin || P.asin || "")}</span>
        <span class="pmeta">${esc(String(P.title || "").slice(0, 60))}${P.sku ? " · 父SKU " + esc(P.sku) : ""}
          · 该父体下共 ${kidsAll.length} 个子 ASIN${advN ? `，其中 ${advN} 个有广告投放` : ""}，指标已合并</span>
        <button class="btn btn-mini" id="pBack">‹ 返回产品列表</button>
      </div>
    </div>
    <div class="p-sum">
      <span>参与活动 <b>${rows.length}</b></span>
      <span>有搜索词的活动 <b>${rows.filter((r) => r.search_term_count > 0).length}</b></span>
      <span>搜索词条目 <b>${nf(tot.terms)}</b></span>
      <span>该父体花费 <b>${money(tot.cost)}</b></span>
      <span>曝光 <b>${nf(tot.imp)}</b></span>
      <span>点击 <b>${nf(tot.clk)}</b></span>
      <span>订单 <b>${nf(tot.ord)}</b></span>
      <span class="pmeta sort-hint">点击表头可排序</span>
      <span class="pmeta">指标为「该父体下全部子 ASIN 在此活动中的合计」；搜索词为「活动级」口径</span>
    </div>`;
  $("pBack").onclick = resetToProducts;

  const body = rows.map((r) => {
    const kids = r.child_asins || [];
    return `<tr data-cid="${esc(r.campaignId)}" style="cursor:pointer">
      <td class="p-title-cell">${esc(r.campaignName || r.campaignId)}</td>
      <td>${stBadge(r.state)}</td>
      <td>${esc(String(r.adType || "").toUpperCase())}</td>
      <td class="p-title-cell" title="${esc(kids.join(", "))}">${
        kids.length ? kids.map((a) => `<span class="asin-chip" title="${esc(a)}">${esc(a)}</span>`).join("")
                    : '<span class="pmeta">-</span>'}</td>
      <td>${esc(r.startDate || "")}</td>
      <td class="num">${r.dailyBudget != null && r.dailyBudget !== "" ? money(r.dailyBudget) : "-"}</td>
      <td>${esc(r.strategy || "")}</td>
      <td class="num">${r.search_term_count ? `<b>${r.search_term_count}</b>` : 0}</td>
      <td class="num">${money(r.ad_cost)}</td>
      <td class="num">${nf(r.impressions)}</td>
      <td class="num">${nf(r.clicks)}</td>
      <td class="num">${nf(r.order_num)}</td>
      <td class="num">${r.ad_sales ? money(r.ad_sales) : "-"}</td>
      <td class="col-ops">${r.search_term_count
        ? `<button class="btn btn-mini btn-primary" data-terms="1"
             title="查看该活动的搜索词 / 商品投放明细">搜索词</button>`
        : '<span class="pmeta">无搜索词</span>'}</td>
    </tr>`; }).join("");

  // 表头「合计」: 活动列表数据已整体载入, 直接前端求和
  const T2 = {
    dailyBudget: money(sumBy(rows, "dailyBudget")),
    search_term_count: nf(sumBy(rows, "search_term_count")),
    ad_cost: money(sumBy(rows, "ad_cost")),
    impressions: nf(sumBy(rows, "impressions")),
    clicks: nf(sumBy(rows, "clicks")),
    order_num: nf(sumBy(rows, "order_num")),
    ad_sales: money(sumBy(rows, "ad_sales")),
  };
  $("pWrap").innerHTML = `<table><thead><tr>
      ${sortableTh(pv.cSort, { label: "广告活动", en: "Campaign", field: "campaignName" })}
      ${sortableTh(pv.cSort, { label: "状态", en: "Status", field: "state" })}
      ${sortableTh(pv.cSort, { label: "类型", en: "Type", field: "adType" })}
      <th>涉及子ASIN<span class="th-en">(Child ASINs)</span></th>
      ${sortableTh(pv.cSort, { label: "开始日期", en: "Start", field: "startDate" })}
      ${sortableTh(pv.cSort, { cls: "num", label: "日预算", en: "Budget", field: "dailyBudget",
        total: T2.dailyBudget, totalTip: `本页 ${rows.length} 个活动的日预算之和` })}
      ${sortableTh(pv.cSort, { label: "投放策略", en: "Strategy", field: "strategy" })}
      ${sortableTh(pv.cSort, { cls: "num", label: "搜索词数", en: "Terms", field: "search_term_count",
        total: T2.search_term_count, totalTip: `本页 ${rows.length} 个活动的搜索词数之和` })}
      ${sortableTh(pv.cSort, { cls: "num", label: "花费", en: "Spend", field: "ad_cost",
        total: T2.ad_cost, totalTip: `本页 ${rows.length} 个活动的花费合计` })}
      ${sortableTh(pv.cSort, { cls: "num", label: "曝光", en: "Impressions", field: "impressions",
        total: T2.impressions, totalTip: `本页 ${rows.length} 个活动的曝光合计` })}
      ${sortableTh(pv.cSort, { cls: "num", label: "点击", en: "Clicks", field: "clicks",
        total: T2.clicks, totalTip: `本页 ${rows.length} 个活动的点击合计` })}
      ${sortableTh(pv.cSort, { cls: "num", label: "订单", en: "Orders", field: "order_num",
        total: T2.order_num, totalTip: `本页 ${rows.length} 个活动的订单合计` })}
      ${sortableTh(pv.cSort, { cls: "num", label: "销售额", en: "Sales", field: "ad_sales",
        total: T2.ad_sales, totalTip: `本页 ${rows.length} 个活动的销售额合计` })}
      <th class="col-ops">操作</th>
    </tr></thead><tbody>${body}</tbody></table>` + (rows.length ? "" : `<div class="empty">该父体暂无关联活动</div>`);
  bindSortHeaders($("pWrap"), pv.cSort, () => renderCampaigns());
  $("pWrap").querySelectorAll("tbody tr").forEach((tr) => {
    const c = rows.find((x) => String(x.campaignId) === tr.dataset.cid);
    if (!c) return;
    tr.onclick = () => loadTerms(c);
    const btn = tr.querySelector('button[data-terms]');
    if (btn) btn.onclick = (e) => { e.stopPropagation(); loadTerms(c); };
  });
}

/* ---------- Level 3: 搜索词 / 商品投放 ----------
   进入该视图**默认只看搜索词**(sp:keyword): 用户点的是「搜索词」按钮, 商品投放
   (sp:asin, 竞品 ASIN 定向)不是搜索词。若该活动/父体一条搜索词都没有, 自动回退「全部」。 */
const TERM_DEFAULT_SCOPE = "sp:keyword";

function applyTermScope(counts, want) {
  const c = counts || {};
  if (want === TERM_DEFAULT_SCOPE && !(c.keyword > 0) && (c.targeting > 0)) return "";
  return want;
}

async function loadTerms(campaign, dim) {
  pv.level = 3; pv.campaign = campaign; pv.parentTerms = false; pv.ptMeta = null;
  pv.dim = dim === undefined ? TERM_DEFAULT_SCOPE : dim;
  const q = new URLSearchParams({ campaign_id: campaign.campaignId });
  if (pv.dim) q.set("scope", pv.dim);
  const d = await api("/api/product/terms?" + q.toString());
  pv.termCounts = d.counts || null;
  // 默认口径下没有搜索词 -> 回退显示全部(避免打开就是空表)
  if (pv.dim === TERM_DEFAULT_SCOPE && applyTermScope(d.counts, pv.dim) === "" && dim === undefined) {
    return loadTerms(campaign, "");
  }
  pv.terms = d.rows || [];
  renderCrumbs(); renderTerms();
}

/* ---------- Level 3b: 某父体「全部活动」的搜索词汇总 ---------- */
async function loadParentTerms(asin, product, dim) {
  pv.level = 3; pv.parentTerms = true; pv.campaign = null;
  const parent = (product && (product.parent_asin || product.asin)) || asin;
  pv.product = product || pv.product || { parent_asin: parent, asin: parent };
  if (!pv.product.parent_asin) pv.product.parent_asin = parent;
  pv.dim = dim === undefined ? TERM_DEFAULT_SCOPE : dim;
  const q = new URLSearchParams({ asin: parent });
  if (pv.dim) q.set("scope", pv.dim);
  const d = await api("/api/product/terms?" + q.toString());
  pv.termCounts = d.counts || null;
  if (pv.dim === TERM_DEFAULT_SCOPE && applyTermScope(d.counts, pv.dim) === "" && dim === undefined) {
    return loadParentTerms(asin, product, "");
  }
  pv.terms = d.rows || [];
  pv.ptMeta = { campaign_count: d.campaign_count || 0, child_asins: d.child_asins || [] };
  renderCrumbs(); renderTerms();
}

function renderTerms() {
  const PT = !!pv.parentTerms;
  const P = pv.product || {};
  const C = pv.campaign || {};
  const rows = sortRows(pv.terms, pv.tSort.field, pv.tSort.dir);
  const tot = rows.reduce((a, r) => {
    a.cost += Number(r.adCost || 0); a.imp += Number(r.impressions || 0);
    a.clk += Number(r.clicks || 0); a.ord += Number(r.orderNum || 0); return a;
  }, { cost: 0, imp: 0, clk: 0, ord: 0 });
  // 按钮计数用后端 counts(不受当前过滤影响), 否则过滤后会显示「只看商品投放 (0)」
  const cn = pv.termCounts || {};
  const nkw = cn.keyword != null ? cn.keyword : rows.filter((r) => r.scope.endsWith("keyword")).length;
  const ntg = cn.targeting != null ? cn.targeting : rows.length - nkw;
  const nAll = cn.all != null ? cn.all : rows.length;

  const title = PT ? `父 ${P.parent_asin || P.asin || ""} · 全部活动的搜索词`
                   : String(C.campaignName || C.campaignId || "").slice(0, 48);
  $("pBar").innerHTML =
    `<div class="pline">
      <div class="p-tools">
        <span class="ptitle">${esc(title)}</span>
        <span class="pmeta">${PT
          ? `覆盖该父体参与的 ${(pv.ptMeta && pv.ptMeta.campaign_count) || 0} 个活动；同一搜索词跨活动合并累加`
          : `活动 ID ${esc(C.campaignId)}`}</span>
        <button class="btn btn-mini" id="pTgKw" data-scope="sp:keyword"
                title="只看真实搜索词（spQuery）">只看搜索词 (${nkw})</button>
        <button class="btn btn-mini" id="pTgAsin" data-scope="sp:asin"
                title="只看商品投放定向的竞品 ASIN（spTargeting），不是搜索词">只看商品投放 (${ntg})</button>
        <button class="btn btn-mini" id="pTgAll" data-scope="" title="搜索词 + 商品投放">全部 (${nAll})</button>
        <button class="btn btn-mini" id="pBack2">‹ 返回活动列表</button>
      </div>
    </div>
    <div class="p-sum">
      <span>条目 <b>${rows.length}</b></span>
      <span>花费 <b>${money(tot.cost)}</b></span>
      <span>曝光 <b>${nf(tot.imp)}</b></span>
      <span>点击 <b>${nf(tot.clk)}</b></span>
      <span>订单 <b>${nf(tot.ord)}</b></span>
      <span class="pmeta sort-hint">点击表头可排序</span>
      <span class="pmeta">⚠ 搜索词为活动级口径，接口不支持按单个 ASIN 拆分${PT ? "；此处已跨活动合并" : ""}</span>
    </div>`;
  $("pBack2").onclick = () => loadCampaigns(pv.product ? (pv.product.parent_asin || pv.product.asin) : "");
  ["pTgKw", "pTgAsin", "pTgAll"].forEach((id) => {
    const b = $(id);
    const active = (pv.dim || "") === b.dataset.scope;
    b.classList.toggle("btn-primary", active);
    b.onclick = () => {
      if ((pv.dim || "") === b.dataset.scope) return;          // 已是当前口径, 不重复请求
      // 父体级与活动级都要把 scope 传下去 (此前父体级漏传 -> 点了没反应)
      if (PT) loadParentTerms(P.parent_asin || P.asin, P, b.dataset.scope);
      else loadTerms(pv.campaign, b.dataset.scope);
    };
  });

  const tbg = termBgMap(rows.map((r) => String(r.query || "")));   // 重复词同色
  const paT = P.parent_asin || P.asin || "";
  if (paT && !termLabels[paT]) loadTermLabels(paT).then(() => renderTerms());  // 首次取到打标后重画
  const lmT = termLabels[paT] || {};
  const body = rows.map((r) => {
    const isKw = r.scope.endsWith("keyword");
    const val = esc(r.query || "");
    const cell = isKw
      ? `<span class="term-cell"><span class="amz-link" data-q="${val}" title="点击查看该搜索词在亚马逊的搜索结果">${val}</span>${termZhHtml(r.query, r)}</span>`
      : `<a class="cell-link" href="https://www.amazon.co.jp/dp/${val}" target="_blank" rel="noopener">${val}</a>`;
    return `<tr>
      <td><span class="dim-tag ${isKw ? "kw" : "tg"}">${r.dimension}</span></td>
      <td${termBgAttr(tbg.get(String(r.query || "")))}>${cell}</td>
      <td class="col-label">${isKw ? termLabelCell(lmT[r.query]) : "-"}</td>
      <td>${esc(r.matchType || "")}</td>
      ${PT ? `<td class="num">${r.campaign_count || 1}</td>` : ""}
      <td class="num">${nf(r.impressions)}</td>
      <td class="num">${nf(r.clicks)}</td>
      <td class="num">${money(r.adCost)}</td>
      <td class="num">${r.orderNum ? nf(r.orderNum) : "-"}</td>
      <td class="num">${r.searchFrequencyRank ? nf(r.searchFrequencyRank) : "-"}</td>
    </tr>`;
  }).join("");

  // 表头「合计」: 搜索词明细已整体载入, 直接前端求和
  const T3 = {
    campaign_count: nf(sumBy(rows, "campaign_count")),
    impressions: nf(sumBy(rows, "impressions")),
    clicks: nf(sumBy(rows, "clicks")),
    adCost: money(sumBy(rows, "adCost")),
    orderNum: nf(sumBy(rows, "orderNum")),
  };
  const scopeName = pv.dim === "sp:keyword" ? "搜索词"
    : (pv.dim === "sp:asin" ? "商品投放" : "全部维度");
  const t3tip = `当前${rows.length}条（${scopeName}）`;
  $("pWrap").innerHTML = `<table><thead><tr>
      ${sortableTh(pv.tSort, { cls: "col-dim", label: "维度", en: "Dimension", field: "dimension" })}
      ${sortableTh(pv.tSort, { label: "搜索词 / 投放 ASIN", en: "Search Term / Target ASIN", field: "query" })}
      <th class="col-label" title="该搜索词对本父体的相关度打标（紧密 / 宽泛 / 不太相关 / 不相关），在搜索词详情页打标">打标<span class="th-en">(Relevance)</span></th>
      ${sortableTh(pv.tSort, { label: "匹配方式", en: "Match Type", field: "matchType" })}
      ${PT ? sortableTh(pv.tSort, { cls: "num", label: "涉及活动", en: "Campaigns",
        field: "campaign_count", total: T3.campaign_count,
        totalTip: `${t3tip} 的「条目×活动」行数之和` }) : ""}
      ${sortableTh(pv.tSort, { cls: "num", label: "曝光", en: "Impressions", field: "impressions",
        total: T3.impressions, totalTip: `${t3tip} 的曝光合计` })}
      ${sortableTh(pv.tSort, { cls: "num", label: "点击", en: "Clicks", field: "clicks",
        total: T3.clicks, totalTip: `${t3tip} 的点击合计` })}
      ${sortableTh(pv.tSort, { cls: "num", label: "花费", en: "Spend", field: "adCost",
        total: T3.adCost, totalTip: `${t3tip} 的花费合计` })}
      ${sortableTh(pv.tSort, { cls: "num", label: "订单", en: "Orders", field: "orderNum",
        total: T3.orderNum, totalTip: `${t3tip} 的订单合计` })}
      ${sortableTh(pv.tSort, { cls: "num", label: "搜索热度", en: "Search Freq. Rank", field: "searchFrequencyRank" })}
    </tr></thead><tbody>${body}</tbody></table>` +
    (rows.length ? "" : `<div class="empty">该${PT ? "父体" : "活动"}在此维度下没有数据</div>`);
  bindSortHeaders($("pWrap"), pv.tSort, () => renderTerms());
  $("pWrap").querySelectorAll(".amz-link").forEach((el) => el.onclick = () => openAmazon(el.dataset.q));
  // 缺译文的搜索词排队自动翻译; 译文到位后重画
  ensureTermList(rows.map((r) => r.query), () => renderTerms(), "drawerTerms");
}

/* ---------- 产品视角分页 ---------- */
function pagerHtml() {
  const pages = Math.max(1, Math.ceil(pv.total / pv.pageSize));
  return `<div class="pager">
    <span>共 ${pv.total} 个产品</span>
    <button class="btn btn-mini" id="pPrev">上一页</button>
    <span>${pv.page} / ${pages}</span>
    <button class="btn btn-mini" id="pNext">下一页</button>
  </div>`;
}
function bindPager() {
  const p = $("pPrev"), n = $("pNext");
  if (p) p.onclick = () => { if (pv.page > 1) { pv.page--; loadProducts(); } };
  if (n) n.onclick = () => {
    const pages = Math.max(1, Math.ceil(pv.total / pv.pageSize));
    if (pv.page < pages) { pv.page++; loadProducts(); }
  };
}

document.querySelectorAll("#viewSwitch .seg-btn")
  .forEach((b) => b.onclick = () => showView(b.dataset.view));

/* ===================================================================
   在线产品 (销售 > 在线产品)
   列定义 —— 与原页面默认 47 列(含分组列)逐一对齐
   查询框 —— 与原页面 11 项控件逐一对齐
   =================================================================== */
const ol = {
  page: 1, pageSize: 50, total: 0, rows: [],
  keyword: "", searchField: "asin", filters: { isVariation: ["2"] }, pageType: "child",
  orderField: "", orderDir: "desc",
  fields: [], shops: [], filterMeta: [], updated: "", counts: { child: 0, parent: 0, total: 0 },
  visible: null,          // Set(列 key)
  aiLabels: {},           // asin -> AI 品名(本地 Ollama 解析)
  loaded: false,
};

/* 原站默认列: key / 表头名 / 取值字段(可多字段合成一列) / 渲染类型 */
const OL_COLUMNS = [
  { key: "onlineStatus", label: "状态", fields: ["onlineStatus"], type: "status" },
  { key: "image", label: "图片", fields: ["mainBigImage"], type: "image" },
  { key: "asinSku", label: "ASIN/MSKU", fields: ["asin", "sku"], type: "asinlink" },
  // 品名 = 本地 Ollama 解析标题得到的「这是什么」(≤5 个词), 展示在标题列之前
  { key: "aiTitle", label: "品名", fields: [], type: "ai" },
  { key: "title", label: "标题", fields: ["title"], type: "title" },
  // 原「分析」列替换为价格列: 父体 = 全部子体售价中位数
  { key: "priceMedian", label: "价格", fields: ["priceMedian"], type: "medianprice" },
  { key: "variationChildStr", label: "属性", fields: ["variationChildStr"] },
  { key: "labelName", label: "产品标签", fields: ["labelName"] },
  { key: "parentAsin", label: "父ASIN", fields: ["parentAsin"] },
  { key: "parentSku", label: "父SKU", fields: ["parentSku"] },
  { key: "fnsku", label: "FNSKU", fields: ["fnsku"] },
  { key: "aiCopy", label: "AI文案优化", fields: [], type: "icons" },
  { key: "commodity", label: "品名/SKU", fields: ["commodityName", "commoditySku"] },
  { key: "shopSite", label: "店铺/站点", fields: ["shopName", "siteName"] },
  { key: "standardPrice", label: "价格", fields: ["standardPrice"], type: "money" },
  { key: "strike", label: "划线价/类型", fields: ["crawlerStrikethroughPrice", "crawlerStrikethroughType"], type: "money2" },
  { key: "listPrice", label: "List Price", fields: ["compSummaryWasPrice"], type: "money" },
  { key: "businessPrice", label: "B2B价格", fields: ["businessPrice"], type: "money" },
  { key: "skuType", label: "商品编码/类型", fields: ["standardProductId", "standardProductType"] },
  { key: "listingPricing", label: "优惠价", fields: ["listingPricing"], type: "money" },
  { key: "discountPrice", label: "折扣价", fields: ["discountPrice"], type: "money" },
  { key: "profit", label: "毛利润率和毛利润", fields: ["profitRate", "profitPrice"], type: "profit" },
  { key: "totalFee", label: "预计费用", fields: ["totalFee"], type: "money" },
  { key: "listingPrice", label: "Buy Box价格", fields: ["listingPrice"], type: "money" },
  { key: "buyBoxWinner", label: "Buy Box资格", fields: ["buyBoxWinner"], type: "bool" },
  { key: "saleNum", label: "销量", fields: ["saleNum"], type: "num0" },
  { key: "yesterdaySaleNum", label: "昨日销量", fields: ["yesterdaySaleNum"], type: "num0" },
  { key: "triSaleNum", label: "7天|14天|30天销量", fields: ["day7SaleNum", "day14SaleNum", "day30SaleNum"], type: "tri" },
  { key: "triAvgSaleNum", label: "7天|14天|30天日均销量", fields: ["day7AvgSaleNum", "day14AvgSaleNum", "day30AvgSaleNum"], type: "tri1" },
  { key: "salePrices", label: "销售额", fields: ["salePrices"], type: "money" },
  { key: "yesterdaySalePrice", label: "昨日销售额", fields: ["yesterdaySalePrice"], type: "money" },
  { key: "triSalePrice", label: "7天|14天|30天销售额", fields: ["day7SalePrice", "day14SalePrice", "day30SalePrice"], type: "triMoney" },
  { key: "adCosts", label: "广告花费", fields: ["adCosts"], type: "money" },
  { key: "yesterdayAdTotalCost", label: "昨日广告花费", fields: ["yesterdayAdTotalCost"], type: "money" },
  { key: "triAdCost", label: "7天|14天|30天广告花费", fields: ["day7AdTotalCost", "day14AdTotalCost", "day30AdTotalCost"], type: "triMoney" },
  { key: "rating", label: "星级评分", fields: ["rating"], type: "metric", m: "_m_rating", mkind: "star" },
  { key: "ratingCount", label: "评分数", fields: ["ratingCount"], type: "metric", m: "_m_rating_count", mkind: "num" },
  { key: "smallBsrRank", label: "小类目排名", fields: ["smallBsrRank"], type: "metric", m: "_m_bsr_small", mcat: "_m_bsr_small_cat", mkind: "rank" },
  { key: "bigBsrRank", label: "大类目排名", fields: ["bigBsrRank"], type: "metric", m: "_m_bsr_big", mcat: "_m_bsr_big_cat", mkind: "rank" },
  { key: "quantity", label: "可售", fields: ["quantity"], type: "num0" },
  { key: "purchaseCost", label: "采购成本(¥)", fields: ["purchaseCost"], type: "num4" },
  { key: "headTripCost", label: "头程费用(¥)", fields: ["headTripCost"], type: "num4" },
  { key: "devNames", label: "业务员", fields: ["devNames"] },
  { key: "firstOrderDate", label: "首单时间", fields: ["firstOrderDate"] },
  { key: "saleStartDate", label: "开售时间", fields: ["saleStartDate"] },
  { key: "lastSyncTime", label: "最新更新时间", fields: ["lastSyncTime"] },
  { key: "openDate", label: "上架时间", fields: ["openDate"] },
  { key: "ops", label: "操作", fields: [], type: "ops" },
];
const OL_KEYS = OL_COLUMNS.map((c) => c.key);
const OL_DASH = /^(null|undefined|)$/i;
const OL_NUM_TYPES = new Set(["money", "money2", "profit", "num0", "num1", "num4",
  "tri", "tri1", "triMoney", "medianprice"]);

/* 原站状态文案 */
const OL_STATUS = { Active: ["在售", "on"], active: ["在售", "on"],
  Inactive: ["不可售", "off"], inActive: ["不可售", "off"],
  Incomplete: ["信息不完整", "pause"] };

function olVal(v) { return (v === null || v === undefined || v === "" || v === "None") ? "" : v; }
function olIsMoney(k) { return /(price|cost|fee|amount|sales|profit)/i.test(k); }

function olMoney(v) {
  const s = olVal(v); if (s === "") return "-";
  const n = Number(s); if (isNaN(n)) return esc(String(s));
  return "JP￥\u200e " + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}
function olPlain(v) {
  const s = olVal(v); if (s === "") return "-";
  if (Array.isArray(s)) return esc(s.join(", "));
  const str = String(s);
  return str.length > 90 ? esc(str.slice(0, 90)) + "…" : esc(str);
}
function olNum(v, digits) {
  const s = olVal(v); if (s === "") return "-";
  const n = Number(s); if (isNaN(n)) return esc(String(s));
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

/* 单列渲染 */
function olCell(col, r) {
  const g = (k) => r[k];
  switch (col.type) {
    case "status": {
      const v = String(olVal(g("onlineStatus")));
      const hit = OL_STATUS[v];
      return hit ? `<span class="badge ${hit[1]}">${hit[0]}</span>` : esc(v);
    }
    case "image": {
      const v = olVal(g("mainBigImage"));
      if (!v) return "";
      const cap = [g("asin"), g("sku"), g("title")].filter(Boolean).join(" · ");
      return `<img class="prod-thumb" src="${esc(v)}" loading="lazy" alt=""
        data-preview="${esc(amzBigImage(v))}" data-fallback="${esc(v)}" data-cap="${esc(cap)}"
        onerror="this.style.visibility='hidden'">`;
    }
    case "asinlink": {
      const a = olVal(g("asin")), s = olVal(g("sku"));
      return `<div class="ol-asin"><a class="link" href="https://www.amazon.co.jp/dp/${esc(a)}" target="_blank" rel="noopener">${esc(a)}</a></div>
              <div class="ol-sku" title="${esc(s)}">${esc(String(s).slice(0, 22))}</div>`;
    }
    case "ai": {
      const a = esc(r.asin || "");
      const lab = (ol.aiLabels || {})[r.asin];
      return `<span class="ol-ai${lab ? "" : " ol-ai-empty"}" data-ai-asin="${a}"
        title="${lab ? esc(lab) : "正在解析品名(模型可在右上角「配置」里切换)…"}">${lab ? esc(lab) : "解析中…"}</span>`;
    }
    case "title":
      return `<div class="ol-title-in" title="${esc(olVal(r.title))}">${olPlain(r.title)}</div>`;
    case "medianprice": {
      const v = Number(r._price_median || 0);
      if (!v) return "-";
      const n = r._price_child_count || 0;
      return `<span title="该父体下 ${n} 个子体售价的中位数">${olMoney(v)}</span>`;
    }
    case "icons":
      return `<span class="ol-icons">🔍 💬</span>`;
    case "ops":
      return `<button class="btn btn-mini" data-ops="1">详情</button>
              <button class="btn btn-mini" data-fill="${esc(r.asin || "")}" title="从亚马逊商品页抓取星级/评分数/排名">补全评分</button>`;
    case "money":
      return olMoney(col.fields.length ? g(col.fields[0]) : "");
    case "money2": {
      const a = olVal(g(col.fields[0])), b = olVal(g(col.fields[1]));
      return a === "" && b === "" ? "-" : `${a === "" ? "-" : olMoney(a)} ${b === "" ? "-" : esc(b)}`;
    }
    case "profit": {
      const rate = olVal(g("profitRate")), price = olVal(g("profitPrice"));
      if (rate === "" && price === "") return "-";
      const rr = rate === "" ? "-" : (Number(rate) * 100).toFixed(2) + "%";
      return `${rr} ${price === "" ? "-" : olMoney(price)}`;
    }
    case "bool": {
      const v = olVal(g("buyBoxWinner"));
      if (v === "") return "-";
      return ["true", "1", "True"].includes(String(v)) ? "是" : "否";
    }
    case "num0": return olNum(g(col.fields[0]), 0);
    case "num4": return olNum(g(col.fields[0]), 4);
    case "tri": {
      const vals = col.fields.map((f) => { const s = olVal(g(f)); return s === "" ? "-" : olNum(s, 0); });
      return vals.join(" | ");
    }
    case "tri1": {
      const vals = col.fields.map((f) => { const s = olVal(g(f)); return s === "" ? "-" : olNum(s, 1); });
      return vals.join(" | ");
    }
    case "triMoney":
      return col.fields.map((f) => olMoney(g(f))).join(" | ");
    case "mask": {
      const v = olVal(g(col.fields[0]));
      return v === "" ? "***" : olPlain(v);
    }
    case "metric": {
      // 赛狐接口这几列恒为 null; 若已从亚马逊商品页补全则显示真实值(覆盖原列)
      const mv = r[col.m];
      const fetched = r._m_fetched_at;
      if (mv === null || mv === undefined || mv === "") {
        const tip = fetched
          ? `已从亚马逊抓取(${fetched})，该商品亚马逊上无此数据`
          : "赛狐接口无此数据；点「补全评分」可从亚马逊商品页获取";
        return `<span class="ol-na" title="${esc(tip)}">***</span>`;
      }
      const tip2 = `来源: 亚马逊商品页${fetched ? " · " + fetched : ""}`;
      if (col.mkind === "star") return `<span title="${esc(tip2)}">★ ${Number(mv).toFixed(1)}</span>`;
      if (col.mkind === "rank") {
        const cat = r[col.mcat] ? esc(String(r[col.mcat])) + " " : "";
        return `<span title="${esc(tip2)}">${cat}${Number(mv).toLocaleString("en-US")}位</span>`;
      }
      return `<span title="${esc(tip2)}">${Number(mv).toLocaleString("en-US")}</span>`;
    }
    default: {
      if (col.fields.length > 1) {
        const parts = col.fields.map((f) => olVal(g(f))).filter((x) => x !== "");
        return parts.length ? esc(parts.join(" ")) : "-";
      }
      return olPlain(g(col.fields[0] || col.key));
    }
  }
}

function olIsNumCol(col) { return OL_NUM_TYPES.has(col.type); }

/* 统计区间: 与原站一致显示「近7天」，取采集日期倒推 */
function olRangeText() {
  const d = (ol.updated || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(d)) return "近7天";
  const end = new Date(d + "T00:00:00");
  const start = new Date(end.getTime() - 6 * 86400000);
  const f = (x) => `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, "0")}-${String(x.getDate()).padStart(2, "0")}`;
  return `${f(start)} ~ ${f(end)}`;
}

async function loadOnlineMeta() {
  const d = await api("/api/online/meta");
  ol.fields = d.fields || []; ol.shops = d.shops || [];
  ol.filterMeta = d.filters || []; ol.total = d.total || 0; ol.updated = d.updated_at || "";
  ol.counts = d.counts || { child: 0, parent: 0, total: ol.total };
  ol.visible = new Set(OL_KEYS);
}

/* 「子体 / 父体」切换: 原站是两个不同接口, 本地落库后用 isVariation 区分
   子体=2 / 父体=1 —— 这里同步维护过滤条件, 保证两个视图都能查到数据 */
function olSetPageType(v) {
  ol.pageType = v === "parent" ? "parent" : "child";
  ol.filters.isVariation = [ol.pageType === "parent" ? "1" : "2"];
  ol.page = 1;
}

async function showOnline() {
  if (!ol.fields.length) await loadOnlineMeta();
  await loadOnlineProducts();
}

function olParams() {
  const q = new URLSearchParams({
    page: ol.page, page_size: ol.pageSize, keyword: ol.keyword,
    search_field: ol.searchField, order_field: ol.orderField, order_dir: ol.orderDir,
  });
  const fl = Object.fromEntries(Object.entries(ol.filters).filter(([, v]) => v && v.length));
  if (Object.keys(fl).length) q.set("filters", JSON.stringify(fl));
  return q;
}

async function loadOnlineProducts() {
  const d = await api("/api/online/products?" + olParams().toString());
  ol.rows = d.rows; ol.total = d.total;
  renderOnline();
}

function renderOnline() {
  // 重新渲染前停掉上一页的品名轮询(通用轮询表里 key="ol")
  if (AI_POLLS.ol) { clearInterval(AI_POLLS.ol); delete AI_POLLS.ol; }
  const opt = (arr, cur) => arr.map((o) =>
    `<option value="${esc(o.value)}"${String(cur) === String(o.value) ? " selected" : ""}>${esc(o.label)}${o.count != null ? ` (${o.count})` : ""}</option>`).join("");
  const sel = (id, label, key) => {
    const f = ol.filterMeta.find((x) => x.key === key);
    if (!f) return "";
    const cur = (ol.filters[key] || [])[0] || "";
    return `<select id="${id}" data-ol="${key}"><option value="">${esc(label)}: 全部</option>${opt(f.options, cur)}</select>`;
  };

  $("olBar").innerHTML =
    `<div class="pline">
      <span class="ptitle">在线产品</span>
      <div class="seg seg-mini" id="olPageType">
        <button class="seg-btn${ol.pageType === "child" ? " active" : ""}" data-v="child" title="子体: 与原站「子体」接口同一数据源">子体</button>
        <button class="seg-btn${ol.pageType === "parent" ? " active" : ""}" data-v="parent" title="父体: 与原站「父体」接口同一数据源">父体</button>
      </div>
      ${sel("olSite", "全部站点", "marketplaceId")}
      ${sel("olShopSel", "全部店铺", "shopId")}
      ${sel("olLabel", "产品标签", "labelName")}
      <div class="colpick" id="olStatusPick"></div>
      ${sel("olFulfill", "配送类型", "switchFulfillmentTo")}
      ${sel("olMatch", "配对状态", "match")}
      ${sel("olStrike", "划线价类型", "crawlerStrikethroughType")}
      <span class="ol-range" title="本地为快照数据，时间区间仅作显示，不参与过滤">统计区间 ${esc(olRangeText())}</span>
      <select id="olSearchField">
        ${[["asin", "ASIN"], ["sku", "MSKU"], ["title", "标题"], ["parentAsin", "父ASIN"],
          ["fnsku", "FNSKU"], ["commodityName", "品名"], ["commoditySku", "品名SKU"],
          ["standardProductId", "商品编码"]].map(([v, t]) =>
          `<option value="${v}"${ol.searchField === v ? " selected" : ""}>${esc(t)}</option>`).join("")}
      </select>
      <input type="text" id="olKw" placeholder="双击可批量搜索内容" value="${esc(ol.keyword)}">
      <button class="btn btn-primary" id="olQuery">查询</button>
      <button class="btn" id="olReset">重置</button>
      <div class="colpick" id="olColPick"></div>
      <button class="btn" id="olCrawl">采集最新</button>
    </div>
    <div class="p-sum">
      <span>在线产品 <b>${ol.total}</b></span>
      <span>子体 <b>${ol.counts.child || 0}</b> · 父体 <b>${ol.counts.parent || 0}</b></span>
      <span>默认列 <b>${OL_COLUMNS.length}</b></span>
      <span>显示 <b>${ol.visible.size}</b> 列</span>
      <span>查询条件 <b>${ol.filterMeta.length}</b> 项</span>
      <span>最近采集 <b>${esc(ol.updated || "未采集")}</b></span>
    </div>`;

  $("olPageType").querySelectorAll(".seg-btn").forEach((b) => b.onclick = () => {
    olSetPageType(b.dataset.v);
    // 只切换按钮态, 不做整表重渲染 —— 否则会拿着上一页签的 rows 去请求 AI 品名
    $("olPageType").querySelectorAll(".seg-btn")
      .forEach((x) => x.classList.toggle("active", x === b));
    loadOnlineProducts();
  });
  $("olQuery").onclick = () => { ol.keyword = $("olKw").value.trim(); ol.page = 1; loadOnlineProducts(); };
  $("olKw").onkeydown = (e) => { if (e.key === "Enter") $("olQuery").click(); };
  $("olSearchField").onchange = (e) => { ol.searchField = e.target.value; ol.page = 1; loadOnlineProducts(); };
  $("olBar").querySelectorAll("select[data-ol]").forEach((el) => el.onchange = () => {
    const k = el.dataset.ol;
    if (el.value) ol.filters[k] = [el.value]; else delete ol.filters[k];
    ol.page = 1; loadOnlineProducts();
  });
  $("olReset").onclick = () => {
    ol.keyword = ""; ol.filters = {}; ol.searchField = "asin";
    ol.orderField = ""; olSetPageType("child");
    renderOnline(); loadOnlineProducts();
  };
  $("olCrawl").onclick = doOnlineCrawl;
  renderOnlineColPick();
  renderOlStatusPick();

  const cols = OL_COLUMNS.filter((c) => ol.visible.has(c.key));
  const HEAD_TIPS = {
    aiTitle: "本地模型解析标题得出的「这是什么」(≤5 个词)",
    priceMedian: "父体价格 = 该父体下全部子体售价的中位数；点击排序",
  };
  const head = cols.map((c) => {
    const num = olIsNumCol(c);
    const sortable = c.fields.length === 1;
    const tip = HEAD_TIPS[c.key] || (sortable ? "点击排序" : "");
    return `<th class="${num ? "num" : ""}" data-k="${c.key}"${tip ? ` title="${tip}"` : ""}>${esc(c.label)}
      ${ol.orderField === c.fields[0] ? (ol.orderDir === "desc" ? "↓" : "↑") : ""}</th>`;
  }).join("");

  const body = ol.rows.map((r, i) => `<tr data-i="${i}" style="cursor:pointer">` + cols.map((c) => {
    const num = olIsNumCol(c);
    return `<td class="${num ? "num" : ""}${c.key === "title" ? " ol-title-td" : ""}">${olCell(c, r)}</td>`;
  }).join("") + `</tr>`).join("");

  $("olWrap").innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>` +
    (ol.rows.length ? "" : `<div class="empty">暂无数据，点击「采集最新」拉取在线产品</div>`) + olPager();

  $("olWrap").querySelectorAll("img[data-preview]").forEach((img) => img.onclick = () =>
    openImagePreview(img.dataset.preview, img.dataset.cap, img.dataset.fallback));

  $("olWrap").querySelectorAll("thead th[data-k]").forEach((th) => th.onclick = () => {
    const c = OL_COLUMNS.find((x) => x.key === th.dataset.k);
    if (!c || c.fields.length !== 1) return;
    const k = c.fields[0];
    if (ol.orderField === k) ol.orderDir = ol.orderDir === "desc" ? "asc" : "desc";
    else { ol.orderField = k; ol.orderDir = "desc"; }
    ol.page = 1; loadOnlineProducts();
  });
  $("olWrap").querySelectorAll("tbody tr").forEach((tr) => tr.onclick = (e) => {
    const fill = e.target.dataset && e.target.dataset.fill;
    if (fill) { e.stopPropagation(); fillMetrics(fill, e.target); return; }
    if (e.target.dataset.ops) {
      e.stopPropagation();
      openOlDetail(ol.rows[+tr.dataset.i]);
      return;
    }
    openOlDetail(ol.rows[+tr.dataset.i]);
  });
  // 父体: 拉取「品名」AI 标签(本地 Ollama, 后台生成 + 轮询回填)
  if (ol.pageType === "parent") {
    ensureAiLabels(ol.rows.map((r) => r.asin).filter(Boolean));
  }
  olBindPager();
}

/* ---------- 「品名」AI 标签 (模型解析标题 → 中文短语)
   两处共用同一套逻辑, 各自一份缓存与轮询:
     · 在线产品(父体) → ol.aiLabels + #olWrap  (key="ol")
     · 产品视角(父体) → pv.aiLabels + #pWrap   (key="pv") ---------- */
const AI_POLLS = {};        // key -> interval id (两处互不干扰)

async function aiEnsure(asins, key, store, sel) {
  const list = (asins || []).filter(Boolean);
  if (!list.length) return;
  try {
    const r = await api("/api/online/ai_labels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asins: list }),
    });
    Object.assign(store, r.labels || {});
    // 库里没有这些 ASIN 的标题 → 无从解析, 直接标掉, 免得一直「解析中…」
    (r.no_title || []).forEach((a) => { if (!store[a]) store[a] = "—"; });
    aiPaint(store, sel);
    if ((r.pending || []).length) aiPoll(list, key, store, sel);
  } catch (e) { /* 模型不可用时保持「解析中…」 */ }
}

function aiPaint(store, sel) {
  document.querySelectorAll(`${sel} span[data-ai-asin]`).forEach((el) => {
    const v = store[el.dataset.aiAsin];
    if (!v) return;
    el.textContent = v;
    el.title = v;
    el.classList.remove("ol-ai-empty");   // 去掉「解析中」占位样式 -> 变蓝色标签
  });
}

function aiPoll(asins, key, store, sel) {
  if (AI_POLLS[key]) clearInterval(AI_POLLS[key]);
  let rounds = 0;
  AI_POLLS[key] = setInterval(async () => {
    rounds += 1;
    let d;
    try {
      d = await api("/api/online/ai_labels/status?asins=" + encodeURIComponent(asins.join(",")));
    } catch (e) { return; }
    Object.assign(store, d.labels || {});
    aiPaint(store, sel);
    // 以「本页 ASIN 是否都拿到标签」为准, 不依赖后台任务状态
    const left = asins.filter((a) => !store[a]);
    if (!left.length || rounds > 120) {
      clearInterval(AI_POLLS[key]); delete AI_POLLS[key];
      if (left.length) aiGaveUp(sel);       // 超时仍未生成(如模型不可用)
    }
  }, 1500);
}

/* 轮询超时仍无结果: 把占位文案改掉, 避免一直显示「解析中…」 */
function aiGaveUp(sel) {
  document.querySelectorAll(`${sel} span[data-ai-asin].ol-ai-empty`).forEach((el) => {
    el.textContent = "—";
    el.title = "未能解析（可在右上角「配置」里检查模型是否可用）";
  });
}

/* 在线产品(父体) */
function ensureAiLabels(asins) {
  return aiEnsure(asins, "ol", ol.aiLabels || (ol.aiLabels = {}), "#olWrap");
}
function paintAiLabels() {
  aiPaint(ol.aiLabels || (ol.aiLabels = {}), "#olWrap");
}
function pollAiLabels(asins) {
  return aiPoll(asins, "ol", ol.aiLabels || (ol.aiLabels = {}), "#olWrap");
}
function markAiGaveUp() { aiGaveUp("#olWrap"); }

/* 产品视角(父体) */
function ensurePvLabels(asins) {
  return aiEnsure(asins, "pv", pv.aiLabels || (pv.aiLabels = {}), "#pWrap");
}
function pvAiHtml(asin) {
  const lab = (pv.aiLabels || {})[asin];
  return `<span class="ol-ai${lab ? "" : " ol-ai-empty"}" data-ai-asin="${esc(asin)}"
    title="${lab ? esc(lab) : "正在解析品名(模型可在右上角「配置」里切换)…"}">${lab ? esc(lab) : "解析中…"}</span>`;
}

function olPager() {
  const pages = Math.max(1, Math.ceil(ol.total / ol.pageSize));
  return `<div class="pager">
    <span>共 ${ol.total} 条</span>
    <select id="olPageSize">
      ${[50, 100, 200].map((n) => `<option value="${n}"${ol.pageSize === n ? " selected" : ""}>${n}条/页</option>`).join("")}
    </select>
    <button class="btn btn-mini" id="olPrev">上一页</button>
    <span>${ol.page} / ${pages}</span>
    <button class="btn btn-mini" id="olNext">下一页</button>
    <span>前往</span><input type="number" class="jump" id="olJump" value="${ol.page}"><span>页</span>
  </div>`;
}
function olBindPager() {
  $("olPageSize").onchange = (e) => { ol.pageSize = +e.target.value; ol.page = 1; loadOnlineProducts(); };
  $("olPrev").onclick = () => { if (ol.page > 1) { ol.page--; loadOnlineProducts(); } };
  $("olNext").onclick = () => {
    const pages = Math.max(1, Math.ceil(ol.total / ol.pageSize));
    if (ol.page < pages) { ol.page++; loadOnlineProducts(); }
  };
  $("olJump").onchange = (e) => { ol.page = Math.max(1, +e.target.value || 1); loadOnlineProducts(); };
}

/* 列选择器: 列出与原站一致的 47 个显示列 */
function renderOnlineColPick() {
  const groups = { "基础": [], "价格/费用": [], "销售/广告": [], "成本/时间": [] };
  OL_COLUMNS.forEach((c) => {
    if (["onlineStatus", "image", "asinSku", "title", "analyze", "variationChildStr", "labelName",
      "parentAsin", "parentSku", "fnsku", "aiCopy", "commodity", "shopSite"].includes(c.key)) groups["基础"].push(c);
    else if (["standardPrice", "strike", "listPrice", "businessPrice", "skuType", "listingPricing",
      "discountPrice", "profit", "totalFee", "listingPrice", "buyBoxWinner"].includes(c.key)) groups["价格/费用"].push(c);
    else if (["saleNum", "yesterdaySaleNum", "triSaleNum", "triAvgSaleNum", "salePrices",
      "yesterdaySalePrice", "triSalePrice", "adCosts", "yesterdayAdTotalCost", "triAdCost", "rating",
      "ratingCount", "smallBsrRank", "bigBsrRank"].includes(c.key)) groups["销售/广告"].push(c);
    else groups["成本/时间"].push(c);
  });
  const html = Object.entries(groups).map(([g, items]) =>
    items.length ? `<div class="grp">${esc(g)}</div><div class="grid">` +
      items.map((c) => `<label><input type="checkbox" data-ck="${esc(c.key)}"${ol.visible.has(c.key) ? " checked" : ""}> ${esc(c.label)}</label>`).join("") +
      `</div>` : "").join("");
  $("olColPick").innerHTML =
    `<button class="btn" id="olColBtn">自定义列 (${ol.visible.size})</button>
     <div class="pop"><div class="ops">
       <button class="btn btn-mini" id="olColAll">全选</button>
       <button class="btn btn-mini" id="olColNone">全不选</button>
       <button class="btn btn-mini" id="olColDefault">恢复默认</button>
     </div>${html}</div>`;
  $("olColBtn").onclick = (e) => {
    e.stopPropagation(); $("olColPick").querySelector(".pop").classList.toggle("open");
  };
  $("olColPick").querySelectorAll("input[data-ck]").forEach((el) => el.onchange = () => {
    el.checked ? ol.visible.add(el.dataset.ck) : ol.visible.delete(el.dataset.ck);
    ol.visible = new Set([...ol.visible]);
    renderOnline();
  });
  $("olColAll").onclick = () => { ol.visible = new Set(OL_KEYS); renderOnline(); };
  $("olColNone").onclick = () => { ol.visible = new Set(["asinSku"]); renderOnline(); };
  $("olColDefault").onclick = () => { ol.visible = new Set(OL_KEYS); renderOnline(); };
}

/* 在线状态: 原站为多选(如「在售 +1」) */
function renderOlStatusPick() {
  const f = (ol.filterMeta || []).find((x) => x.key === "onlineStatus");
  const cur = ol.filters.onlineStatus || [];
  const opts = f ? f.options : [];
  const title = cur.length === 0 ? "在线状态: 全部"
    : (cur.length === 1 ? `在线状态: ${(opts.find((o) => o.value === cur[0]) || {}).label || cur[0]}`
      : `在线状态: ${(opts.find((o) => o.value === cur[0]) || {}).label || cur[0]} +${cur.length - 1}`);
  $("olStatusPick").innerHTML =
    `<button class="btn" id="olStatusBtn">${esc(title)}</button>
     <div class="pop"><div class="ops">
       <button class="btn btn-mini" id="olStatusAll">全选</button>
       <button class="btn btn-mini" id="olStatusNone">清空</button>
     </div><div class="grid">
       ${(opts.length ? opts : [{ value: "", label: "（暂无数据）", count: 0 }]).map((o) =>
      `<label><input type="checkbox" data-st="${esc(o.value)}"${cur.includes(o.value) ? " checked" : ""}${o.value ? "" : " disabled"}> ${esc(o.label)}${o.count != null ? ` (${o.count})` : ""}</label>`).join("")}
     </div></div>`;
  $("olStatusBtn").onclick = (e) => {
    e.stopPropagation(); $("olStatusPick").querySelector(".pop").classList.toggle("open");
  };
  $("olStatusPick").querySelectorAll("input[data-st]").forEach((el) => el.onchange = () => {
    const set = new Set(ol.filters.onlineStatus || []);
    el.checked ? set.add(el.dataset.st) : set.delete(el.dataset.st);
    if (set.size) ol.filters.onlineStatus = [...set]; else delete ol.filters.onlineStatus;
    ol.page = 1; renderOnline(); loadOnlineProducts();
  });
  $("olStatusAll").onclick = () => {
    const vals = opts.map((o) => o.value);
    if (vals.length) ol.filters.onlineStatus = vals;
    ol.page = 1; renderOnline(); loadOnlineProducts();
  };
  $("olStatusNone").onclick = () => {
    delete ol.filters.onlineStatus; ol.page = 1; renderOnline(); loadOnlineProducts();
  };
}

/* 按需补全: 从亚马逊商品页抓 星级/评分数/排名 (赛狐接口没有这几列) */
async function fillMetrics(asin, btn) {
  if (!asin) return;
  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "抓取中…"; }
  try {
    const d = await api("/api/amazon/metrics?asin=" + encodeURIComponent(asin));
    if (d.detail) { toast("补全失败: " + d.detail); return; }
    const row = ol.rows.find((x) => x.asin === asin);
    if (row) {
      row._m_rating = d.rating; row._m_rating_count = d.rating_count;
      row._m_bsr_small = d.bsr_small; row._m_bsr_small_cat = d.bsr_small_cat;
      row._m_bsr_big = d.bsr_big; row._m_bsr_big_cat = d.bsr_big_cat;
      row._m_fetched_at = d.fetched_at;
    }
    const parts = [`星级 ${d.rating == null ? "无" : d.rating}`,
      `评分数 ${d.rating_count == null ? "无" : d.rating_count}`];
    if (d.bsr_small != null) parts.push(`${d.bsr_small_cat || "小类目"} ${d.bsr_small}位`);
    if (d.bsr_big != null) parts.push(`${d.bsr_big_cat || "大类目"} ${d.bsr_big}位`);
    toast(`${asin} 补全完成${d.from_cache ? "(缓存)" : ""}: ` + parts.join(" · "));
    renderOnline();
  } catch (e) {
    toast("补全失败: " + (e && e.message ? e.message : e));
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = old || "补全评分"; }
  }
}

/* 商品详情抽屉: 展示该商品全部原始字段 */
function openOlDetail(r) {
  if (!r) return;
  const groups = {};
  ol.fields.forEach((f) => { (groups[f.group] = groups[f.group] || []).push(f); });
  const known = new Set(ol.fields.map((f) => f.headField));
  const fmt = (k, v) => {
    if (v === null || v === undefined || v === "") return "";
    if (typeof v === "object") return esc(JSON.stringify(v, null, 1));
    return esc(String(v));
  };
  let html = `<div style="display:flex;gap:14px;align-items:flex-start">
      ${r.mainBigImage ? `<img class="dimg" src="${esc(r.mainBigImage)}" alt="">` : ""}
      <div><div style="font-weight:600;margin-bottom:4px">${esc(r.asin || "")}</div>
      <div style="font-size:12px;color:var(--muted)">${esc(String(r.title || "").slice(0, 90))}</div></div>
    </div>
    <div style="margin:10px 0;display:flex;align-items:center;gap:10px">
      <button class="btn btn-primary" id="olFillBtn" data-asin="${esc(r.asin || "")}">补全评分 / 排名</button>
      <span class="muted" style="font-size:12px">从亚马逊商品页抓取（不走接口）</span>
    </div>`;
  if (r._m_fetched_at) {
    const bs = [];
    if (r._m_rating != null) bs.push(`星级 ${r._m_rating}`);
    if (r._m_rating_count != null) bs.push(`评分数 ${r._m_rating_count}`);
    if (r._m_bsr_small != null) bs.push(`${r._m_bsr_small_cat || "小类目"} ${r._m_bsr_small}位`);
    if (r._m_bsr_big != null) bs.push(`${r._m_bsr_big_cat || "大类目"} ${r._m_bsr_big}位`);
    html += `<div class="muted" style="font-size:12px;margin-bottom:6px">亚马逊补全(${esc(r._m_fetched_at)})：${esc(bs.join(" · ") || "该商品无评分/排名")}</div>`;
  }
  Object.entries(groups).forEach(([g, items]) => {
    const rows = items.filter((f) => r[f.headField] !== undefined && r[f.headField] !== null && r[f.headField] !== "")
      .map((f) => `<div class="drow"><div class="dk">${esc(f.headName)}</div><div class="dv">${fmt(f.headField, r[f.headField])}</div></div>`).join("");
    if (rows) html += `<div class="dsec">${esc(g)}</div>${rows}`;
  });
  const extra = Object.keys(r).filter((k) => !known.has(k));
  if (extra.length) {
    html += `<div class="dsec">其它原始字段 (${extra.length})</div>` +
      extra.map((k) => `<div class="drow"><div class="dk">${esc(k)}</div><div class="dv">${fmt(k, r[k])}</div></div>`).join("");
  }
  $("olDetailTitle").textContent = `商品详情 · ${r.asin || ""}`;
  $("olDetailBody").innerHTML = html;
  $("olDetail").classList.add("open");
  const fb = $("olFillBtn");
  if (fb) fb.onclick = async () => {
    await fillMetrics(fb.dataset.asin, null);
    const m = $("olFillBodyMsg");
    if (m) m.textContent = "已更新";
  };
}

async function doOnlineCrawl() {
  $("olCrawl").disabled = true; $("olCrawl").textContent = "采集中…";
  try {
    const r = await api("/api/online/crawl", { method: "POST" });
    if (r.ok) { toast(`采集完成: ${r.count} 条`); await loadOnlineMeta(); ol.page = 1; await loadOnlineProducts(); }
    else toast(r.detail || "采集失败");
  } catch (e) { toast("采集失败: " + e.message); }
  finally { const b = $("olCrawl"); if (b) { b.disabled = false; b.textContent = "采集最新"; } }
}

document.addEventListener("click", (e) => {
  if (!e.target.closest("#olColPick")) {
    const p = $("olColPick") && $("olColPick").querySelector(".pop"); if (p) p.classList.remove("open");
  }
  if (!e.target.closest("#olStatusPick")) {
    const p = $("olStatusPick") && $("olStatusPick").querySelector(".pop"); if (p) p.classList.remove("open");
  }
});
$("olDetailClose").onclick = () => $("olDetail").classList.remove("open");
/* 详情抽屉展开后: 点击其他任意位置自动隐藏
   (抽屉内部 / 表格行(会切换详情) / 大图预览 除外) */
document.addEventListener("click", (e) => {
  const d = $("olDetail");
  if (!d || !d.classList.contains("open")) return;
  if (e.target.closest("#olDetail")) return;
  if (e.target.closest("#olWrap")) return;
  if (e.target.closest("#imgPreview")) return;
  d.classList.remove("open");
});

/* ===================================================================
   搜索词独立页面 / 详情页签
   · /?page=terms&asin=父ASIN            —— 点击产品视角「搜索词」打开的新页面
   · /?page=term&asin=父ASIN&term=搜索词  —— 在上一页点击某个词弹出的新页签
   =================================================================== */

/* 亚马逊图片取「中等尺寸」: 插入 ._SL500_ 后缀; 失败由全局 error 兜底回退原图 */
function amzMidImage(u, size) {
  if (!u) return "";
  const s = size || 500;
  return String(u).replace(/\.(jpg|jpeg|png|webp|gif)(\?.*)?$/i, `._SL${s}_.$1$2`);
}
/* 图片加载失败 -> 回退 data-fallback (避免插入尺寸后缀被亚马逊拒绝时白图) */
document.addEventListener("error", (e) => {
  const el = e.target;
  if (el && el.tagName === "IMG" && el.dataset.fallback && el.src !== el.dataset.fallback) {
    const fb = el.dataset.fallback;
    el.removeAttribute("data-fallback");
    el.src = fb;
  }
}, true);

function isStandalone() { return document.body.classList.contains("standalone"); }

function openTermsPage(asin) {
  if (!asin) return;
  window.open(`/?page=terms&asin=${encodeURIComponent(asin)}`, "_blank", "noopener");
}
function openTermPage(asin, term, mt) {
  if (!asin || !term) return;
  window.open(termDetailUrl(asin, term, mt), "_blank", "noopener");
}

/* 搜索词详情页地址(单开 / 批量开都用它, 保证 URL 一致) */
function termDetailUrl(asin, term, mt) {
  const q = new URLSearchParams({ page: "term", asin: asin || "", term: term || "" });
  if (mt) q.set("mt", mt);
  return `/?${q.toString()}`;
}

/* ---- 批量打开页签(搜索词列表页勾选多个词 → 各开一个详情页签做研判) ----
   浏览器策略上是「一次点击只放行第一个 window.open」, 其余会被当弹窗拦掉。
   所以这里: 先逐个试开, 被拦的退回下面一行可点链接(点一下就一次新用户手势, 不会再被拦)。
   另外 window.open 不能带 "noopener"(Chrome 下会直接返回 null, 就没法判断有没有被拦),
   改成拿到窗口后手工把 opener 置空, 效果等价。 */
const TERM_TAB_MAX = 12;          // 一次最多开这么多页签, 再多浏览器会卡/被拦

function openTermTabs(asin, sel) {
  // 去重保护: 同一个搜索词只开一个页签。
  // (勾选表本身以「搜索词」为键, 不会存重复; 这里是第二道保险 —— 将来有人改选择逻辑
  //  也不会出现「一个词开两个页签」。词尾空格/大小写差异也算同一个词。)
  const seen = new Set();
  const list = [];
  [...(sel || new Map()).entries()].forEach(([term, mt]) => {
    const key = String(term || "").trim();
    if (!key || seen.has(key.toLowerCase())) return;
    seen.add(key.toLowerCase());
    list.push([key, mt]);
  });
  if (!list.length) { toast("先勾选要研判的搜索词"); return; }
  if (list.length > TERM_TAB_MAX) {
    toast(`一次最多开 ${TERM_TAB_MAX} 个页签（当前勾选 ${list.length} 个），请分批`);
    return;
  }
  const blocked = [];
  let opened = 0;
  list.forEach(([term, mt]) => {
    const w = window.open(termDetailUrl(asin, term, mt), "_blank");
    if (w) { w.opener = null; opened += 1; } else { blocked.push([term, mt]); }
  });
  paintBlockedTabs(asin, blocked, opened);
  if (!blocked.length) { toast(`已打开 ${opened} 个页签，切过去逐个研判`); return; }
  toast(opened
    ? `已打开 ${opened} 个页签，另有 ${blocked.length} 个被拦截（见下方列表）`
    : `页签被浏览器拦截了 ${blocked.length} 个：见下方列表，或允许本站弹窗后重试`);
}

/* 被拦截的搜索词: 在工具条下方给一行可点链接兜底 */
function paintBlockedTabs(asin, blocked, opened) {
  const box = $("tBlocked");
  if (!box) return;
  if (!blocked || !blocked.length) { box.innerHTML = ""; return; }
  const tip = opened ? `已打开 ${opened} 个，其余 ${blocked.length} 个被拦截：`
                     : `有 ${blocked.length} 个页签被浏览器拦截：`;
  box.innerHTML = `<span class="pmeta">${tip}地址栏右侧把本站设为「允许弹出窗口」` +
    `后可一次性打开，或逐个点开 →</span>` +
    blocked.map(([t, mt]) => {
      const s = String(t || "");
      return `<a class="btn btn-mini" target="_blank" rel="noopener"
        href="${esc(termDetailUrl(asin, t, mt))}" title="打开「${esc(s)}」的详情">` +
        `${esc(s.length > 18 ? s.slice(0, 18) + "…" : s)} ↗</a>`;
    }).join("");
}

/* 抓取父体附图(父体 + 全部子体商品页图廊)
   子体可能几十个, 同步请求会超时 → 后端后台线程逐个抓, 前端轮询进度 */
const _imgTried = new Set();
let _imgPoll = null;
let _imgDoneMsg = "";

async function doFetchParentImages(pa, term, refresh) {
  if (!pa) return;
  const msg = $("tgImgMsg"), btn = $("tgFetchImgs");
  if (btn) { btn.disabled = true; btn.textContent = "抓取中…"; }
  if (msg) msg.textContent = "正在启动抓取…";
  try {
    const r = await api(`/api/product/images/crawl?asin=${encodeURIComponent(pa)}`
      + (refresh ? "&refresh=true" : ""), { method: "POST" });
    if (!r.started && !r.running) {
      if (msg) msg.textContent = "启动失败";
      if (btn) { btn.disabled = false; btn.textContent = "抓取全部附图"; }
      return;
    }
    if (!r.total && !r.running) {
      _imgDoneMsg = "全部子体均已抓取";
      await refreshTermTop(pa, term);
      return;
    }
    if (msg) msg.textContent = `待抓取 ${r.total || 0} 个 ASIN…`;
    await pollImages(pa, term);
  } catch (e) {
    if (msg) msg.textContent = "抓取失败: " + e.message;
    if (btn) { btn.disabled = false; btn.textContent = "抓取全部附图"; }
  }
}

function pollImages(pa, term) {
  return new Promise((resolve) => {
    if (_imgPoll) clearInterval(_imgPoll);
    _imgPoll = setInterval(async () => {
      let s;
      try { s = await api(`/api/product/images/status?asin=${encodeURIComponent(pa)}`); }
      catch (e) { return; }
      const msg = $("tgImgMsg");
      if (msg && s.running) {
        msg.textContent = `抓取中 ${s.done}/${s.total}`
          + (s.current ? ` · ${s.current}` : "") + (s.failed ? ` · 失败 ${s.failed}` : "");
      }
      if (!s.running) {
        clearInterval(_imgPoll); _imgPoll = null;
        _imgDoneMsg = `抓取完成：附图 ${s.images || 0} 张`
          + (s.failed ? `（失败 ${s.failed} 个${s.error ? "：" + s.error : ""}）` : "");
        await refreshTermTop(pa, term);
        resolve();
      }
    }, 1500);
  });
}

async function refreshTermTop(pa, term) {
  try {
    const nd = await api("/api/product/term_detail?" + new URLSearchParams(
      { asin: pa, term: term || "" }).toString());
    renderTermTop(nd);
  } catch (e) { /* 忽略: 保持原样 */ }
}

/* ---------------- 搜索词列表页 ---------------- */
async function renderTermsPage(asin) {
  document.title = `搜索词 · ${asin}`;
  $("tCrumbs").innerHTML =
    `<span class="cb cur">父 ASIN ${esc(asin)}</span>
     <span class="sep">›</span><span class="cb cur">搜索词</span>
     <span class="pmeta">勾选左侧复选框 →「批量打开页签研判」；点「搜索词」或「详情」按钮 → 单条打开；点击表头可排序</span>`;
  $("tBar").innerHTML = `<div class="pline"><div class="p-tools">
      <span class="ptitle">搜索词</span><span class="pmeta" id="tMeta">加载中…</span>
      <span id="tStatus" class="st-legend"></span>
      <span class="p-tools" id="tScopes"></span>
      <span class="p-tools" id="tBulk">
        <span class="pmeta" id="tSelInfo">已勾选 <b>0</b> 个</span>
        <button class="btn btn-mini" id="tSelUnlabeled"
          title="勾选当前视图里所有还没打标的搜索词（同一个词出现多行只算一个；已勾选的不动，方便接着批量开页签去研判打标）">选中未打标</button>
        <button class="btn btn-mini" id="tSelPage" title="勾选当前筛选+排序后的全部行">全选本页</button>
        <button class="btn btn-mini" id="tSelNone" title="清空勾选">清空</button>
        <button class="btn btn-mini btn-primary" id="tOpenTabs" disabled
          title="给每个勾选的搜索词各开一个详情页签，逐个研判（关联度 / 结果集评估 / 打标）">批量打开页签研判</button>
      </span>
    </div></div>
    <div class="pline" id="tBlocked"></div>`;
  $("tWrap").innerHTML = `<div class="empty">加载中…</div>`;

  const sel = new Map();      // 勾选的搜索词: term -> 匹配方式 (排序/切维度/翻译回填重画都不丢)
  let shownRows = [];         // 当前渲染出来的行(供「全选本页」与表头全选框状态用)

  /* 勾选状态 → 顶部计数 / 按钮可用性 / 表头全选框(全选 / 半选) */
  function paintSel() {
    const info = $("tSelInfo");
    if (info) info.innerHTML = `已勾选 <b>${sel.size}</b> 个`;
    const btn = $("tOpenTabs");
    if (btn) btn.disabled = !sel.size;
    const all = $("tSelAll");
    if (all) {
      const hit = shownRows.filter((r) => sel.has(String(r.query || ""))).length;
      all.checked = !!shownRows.length && hit === shownRows.length;
      all.indeterminate = hit > 0 && hit < shownRows.length;
    }
  }

  const d = await api("/api/product/terms?asin=" + encodeURIComponent(asin));
  const all = d.rows || [];
  const cn = d.counts || {};
  const pa = d.parent_asin || asin;
  const lm = await loadTermLabels(pa);      // 打标结果(详情页打的, 这里只读展示)
  const dim = isStandalone() ? (new URLSearchParams(location.search).get("dim") || "sp:keyword") : "sp:keyword";

  // 该父体的两个进度: 素材拉取 / 人工打标(后端依据本地仓库与 term_labels 现算)
  const sts = d.status || {};
  if ($("tStatus")) {
    $("tStatus").innerHTML = sts.terms_total
      ? `<span class="pmeta">素材</span> ${stTag(sts.fetch_status, sts.fetch_text)}`
        + `<span class="st-num">${sts.terms_fetched}/${sts.terms_total}</span>`
        + `<span class="pmeta st-gap">打标</span> ${stTag(sts.label_status, sts.label_text)}`
        + `<span class="st-num">${sts.terms_labeled}/${sts.terms_total}</span>`
      : "";
  }

  let curScope = dim;
  // 排序状态(默认按花费倒序); 点表头切换升降序
  const tSort = { field: "adCost", dir: "desc" };

  function paint(scope) {
    curScope = scope;
    const filtered = scope ? all.filter((r) => r.scope === scope) : all.slice();
    const rows = sortRows(filtered, tSort.field, tSort.dir);
    const tbg = termBgMap(rows.map((r) => String(r.query || "")));   // 重复词同色
    const rankDen = rankDenoms(rows);                                // 排名 tooltip 里的「共 N 个」
    const sum = rows.reduce((a, r) => {
      a.imp += Number(r.impressions || 0); a.clk += Number(r.clicks || 0);
      a.cost += Number(r.adCost || 0); a.ord += Number(r.orderNum || 0); return a;
    }, { imp: 0, clk: 0, cost: 0, ord: 0 });
    $("tMeta").innerHTML =
      `共 <b>${rows.length}</b> 条 · 覆盖 ${d.campaign_count || 0} 个活动 · ` +
      `花费 <b>${money(sum.cost)}</b> · 曝光 <b>${nf(sum.imp)}</b> · 点击 <b>${nf(sum.clk)}</b> · 订单 <b>${nf(sum.ord)}</b>` +
      `<span class="pmeta sort-hint">　点击表头可排序（再点一次切换升降序）</span>`;
    const body = rows.map((r) => {
      const q = String(r.query || "");
      const isKw = String(r.scope || "").endsWith("keyword");
      const mt = String(r.matchType || "");
      return `<tr class="t-row${sel.has(q) ? " sel" : ""}">
     <td class="col-chk"><input type="checkbox" class="t-chk" data-q="${esc(q)}"
         data-mt="${esc(mt)}"${sel.has(q) ? " checked" : ""}></td>
      <td class="kw-cell"${termBgAttr(tbg.get(q))}><span class="term-cell"><span class="amz-link kw-link" data-q="${esc(q)}"
          data-mt="${esc(r.matchType || "")}" title="${esc(q)}（列窄会省略，鼠标悬停看全称）· 点击在新页签打开该搜索词详情">${esc(q)}</span>${termZhHtml(q, r)}</span></td>
      <td class="col-rank">${rankCell(r, rankDen)}</td>
      <td class="col-label" title="${lm[q] ? "该词对本父体的相关度打标" : "未打标（在搜索词详情页打标）"}">${termLabelCell(lm[q])}</td>
      <td>${esc(r.matchType || "")}</td>
      <td><span class="dim-tag ${r.scope.endsWith("keyword") ? "kw" : "tg"}">${esc(r.dimension)}</span></td>
      <td class="num">${r.searchFrequencyRank ? nf(r.searchFrequencyRank) : "-"}</td>
      <td class="num">${r.campaign_count || 1}</td>
      <td class="num">${nf(r.impressions)}</td>
      <td class="num">${nf(r.clicks)}</td>
      <td class="num">${money(r.adCost)}</td>
      <td class="num">${r.orderNum ? nf(r.orderNum) : "-"}</td>
      <td class="col-fetch" title="${!isKw ? "商品投放行(ASIN)：没有竞品搜索素材的概念"
        : (r.fetched ? `竞品结果已拉到本地（主图 ${r.images} 张），详情页直接读本地`
          : "竞品结果还没拉到本地，打开详情页会实时访问亚马逊")}">${
        isKw ? stTag(r.fetched ? "done" : "none", r.fetched ? "已拉取" : "未拉取")
          : '<span class="pmeta">-</span>'}</td>
      <td class="num"><button class="btn btn-mini btn-primary kw-link"
          data-q="${esc(r.query || "")}" data-mt="${esc(r.matchType || "")}">详情 ↗</button></td>
    </tr>`;
    }).join("");
    $("tWrap").innerHTML = `<table class="tbl-terms"><thead><tr>
        <th class="col-chk"><input type="checkbox" id="tSelAll"
          title="全选 / 取消本页（当前筛选+排序后的行）"></th>
        ${sortableTh(tSort, { cls: "col-term", label: "搜索词 / 投放 ASIN", en: "Search Term / Target ASIN", field: "query" })}
        <th class="col-rank" title="各项指标在该父体内的排名（1 = 最高）。花=花费 曝=曝光 点=点击 单=订单；
搜索词与商品投放各自排名、不混排；鼠标悬停某个数看完整口径">排名<span class="th-en">(Rank)</span></th>
        <th class="col-label" title="该搜索词对本父体的相关度打标（紧密 / 宽泛 / 不太相关 / 不相关），在搜索词详情页打标">打标<span class="th-en">(Relevance)</span></th>
        ${sortableTh(tSort, { cls: "col-match", label: "匹配方式", en: "Match Type", field: "matchType" })}
        ${sortableTh(tSort, { cls: "col-dim", label: "维度", en: "Dimension", field: "dimension" })}
        ${sortableTh(tSort, { cls: "num col-freq", label: "ABA搜索词排名", en: "ABA Search Rank",
          field: "searchFrequencyRank", dir: "asc",
          title: "亚马逊 ABA 搜索词排名，数值越小越热门；点击排序" })}
        ${sortableTh(tSort, { cls: "num col-camp", label: "涉及活动", en: "Campaigns", field: "campaign_count",
          total: nf(sumBy(rows, "campaign_count")), totalTip: "当前列表涉及活动数合计" })}
        ${sortableTh(tSort, { cls: "num col-imp", label: "曝光", en: "Impressions", field: "impressions",
          total: nf(sumBy(rows, "impressions")), totalTip: "当前列表曝光合计" })}
        ${sortableTh(tSort, { cls: "num col-clk", label: "点击", en: "Clicks", field: "clicks",
          total: nf(sumBy(rows, "clicks")), totalTip: "当前列表点击合计" })}
        ${sortableTh(tSort, { cls: "num col-cost", label: "花费", en: "Spend", field: "adCost",
          total: money(sumBy(rows, "adCost")), totalTip: "当前列表花费合计" })}
        ${sortableTh(tSort, { cls: "num col-ord", label: "订单", en: "Orders", field: "orderNum",
          total: nf(sumBy(rows, "orderNum")), totalTip: "当前列表订单合计" })}
        <th class="col-fetch" title="竞品素材是否已拉到本地：已拉取 = 本地已有该搜索词的亚马逊竞品结果（详情页直接读本地）；未拉取 = 还没抓。该父体全部词拉完即为「拉取完成」">素材<span class="th-en">(Assets)</span></th>
        <th class="col-ops"></th>
      </tr></thead><tbody>${body}</tbody></table>` +
      (rows.length ? "" : `<div class="empty">该父体在此维度下没有数据</div>`);
    // 排序
    bindSortHeaders($("tWrap"), tSort, () => paint(curScope));
    // 仅「搜索词」文字 与「详情」按钮 可跳转（整行不再响应点击）
    $("tWrap").querySelectorAll(".kw-link").forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        openTermPage(asin, el.dataset.q, el.dataset.mt);
      };
    });
    // 批量勾选: 单行勾选框 / 表头全选
    shownRows = rows;
    $("tWrap").querySelectorAll(".t-chk").forEach((el) => {
      el.onclick = (ev) => {
        ev.stopPropagation();
        if (el.checked) sel.set(el.dataset.q, el.dataset.mt || "");
        else sel.delete(el.dataset.q);
        const tr = el.closest("tr");
        if (tr) tr.classList.toggle("sel", el.checked);   // 就地切样式, 不整表重画
        paintSel();
      };
    });
    const allCk = $("tSelAll");
    if (allCk) {
      allCk.onclick = () => {
        rows.forEach((r) => {
          const q = String(r.query || "");
          if (allCk.checked) sel.set(q, String(r.matchType || ""));
          else sel.delete(q);
        });
        paint(curScope);
      };
    }
    paintSel();
  }

  const scopes = [["sp:keyword", `只看搜索词 (${cn.keyword || 0})`],
                  ["sp:asin", `只看商品投放 (${cn.targeting || 0})`],
                  ["", `全部 (${cn.all || all.length})`]];
  $("tScopes").innerHTML = scopes.map(([s, label]) =>
    `<button class="btn btn-mini" data-s="${s}">${label}</button>`).join("");
  $("tScopes").querySelectorAll("button").forEach((b) => {
    b.classList.toggle("btn-primary", b.dataset.s === dim);
    b.onclick = () => {
      $("tScopes").querySelectorAll("button").forEach((x) => x.classList.remove("btn-primary"));
      b.classList.add("btn-primary");
      paint(b.dataset.s);
    };
  });
  // 批量勾选工具条
  /* 勾选「未打标」的搜索词: 以当前视图为准
     · 只认搜索词行(商品投放 ASIN 行没有打标概念, 永远算"未打标", 不能选进来)
     · 同一个词出现多行(不同匹配方式)时**多行都会被勾上**(看起来是一个词全选中),
       而选择表是以「搜索词」为键的, 所以重复行天然只留一条 → 开页签不会重复
     · 已打过标的不选; 已勾过的重复点不会累加 */
  $("tSelUnlabeled").onclick = () => {
    let rowsHit = 0;
    let added = 0;
    shownRows.forEach((r) => {
      const q = String(r.query || "");
      if (!q) return;
      if (!String(r.scope || "").endsWith("keyword")) return;   // 商品投放行跳过
      if (lm[q]) return;                                        // 已打标的跳过
      rowsHit += 1;
      // 同一词多行: 只在实际新增时写入(保留首个匹配方式, 再点一次不变)
      if (!sel.has(q)) { added += 1; sel.set(q, String(r.matchType || "")); }
    });
    if (!rowsHit) {
      toast(sel.size ? "没有新的未打标词可勾选（已勾选的保留）" : "当前视图里没有未打标词");
      return;
    }
    paint(curScope);
    toast(rowsHit > added
      ? `已勾选 ${sel.size} 个未打标搜索词（命中 ${rowsHit} 行，重复词只会开一个页签）`
      : `已勾选 ${sel.size} 个未打标搜索词`);
  };
  $("tSelPage").onclick = () => {
    if (!shownRows.length) { toast("当前没有可勾选的行"); return; }
    shownRows.forEach((r) => sel.set(String(r.query || ""), String(r.matchType || "")));
    paint(curScope);
  };
  $("tSelNone").onclick = () => {
    sel.clear();
    paintBlockedTabs(asin, [], 0);      // 顺手清掉上次被拦的兜底链接
    paint(curScope);
  };
  $("tOpenTabs").onclick = () => openTermTabs(asin, sel);
  paint(dim);
  // 本页缺译文的搜索词排队自动翻译; 译文到位后重画当前视图
  ensureTermList(all.map((r) => r.query), () => paint(curScope), "termsPage");
}

/* ---------------- 搜索词详情页签 ---------------- */
async function renderTermPage(asin, term, mt) {
  document.title = `${term} · 搜索词详情`;
  $("termTop").innerHTML = `<div class="empty">加载中…</div>`;
  $("termMetrics").innerHTML = "";
  $("amzCards").innerHTML = `<div class="amz-loading">查询中…</div>`;
  termLabel.asin = ""; termLabel.term = term; termLabel.cur = ""; termLabel.sel = "";
  $("termLabelOpts").innerHTML = ""; termLabelMsg("读取中…");

  const q = new URLSearchParams({ asin, term });
  if (mt) q.set("match_type", mt);
  let d;
  try {
    d = await api("/api/product/term_detail?" + q.toString());
  } catch (e) {
    $("termTop").innerHTML = `<div class="empty">加载失败: ${esc(e.message)}</div>`;
    return;
  }
  renderTermTop(d);
  renderTermMetrics(d);
  loadTermRelevance(asin, term);  // 自动相关性打分(规则+模型)
  renderTermLabelBar(d);          // 人工打标区(吸顶): 回显已打的标
  loadAmzCards(term, false);
}

/* ---------------- 搜索词 × 父 SKU 关联度 ---------------- */
function relBadge(score) {
  if (score == null) return `<span class="rel-badge rel-none">分析中</span>`;
  const cls = score >= 8 ? "rel-high" : score >= 5 ? "rel-mid" : score >= 3 ? "rel-low" : "rel-bad";
  const txt = score >= 8 ? "高度相关" : score >= 5 ? "相关" : score >= 3 ? "弱相关" : "不相关";
  return `<span class="rel-badge ${cls}">${score}<i>/10</i> ${txt}</span>`;
}

async function loadTermRelevance(asin, term) {
  const body = $("termRelBody");
  body.innerHTML = `<div class="empty">关联度分析中（首次分析需调大模型，约 2~5 秒；命中缓存即时返回）…</div>`;
  $("termRelTools").innerHTML = "";
  let d;
  try {
    d = await api("/api/term_relevance/pair?" +
      new URLSearchParams({ asin, term }).toString());
  } catch (e) {
    body.innerHTML = `<div class="empty">关联度分析失败: ${esc(e.message)}</div>`;
    return;
  }
  renderTermRel(d);
}

function renderTermRel(d) {
  const body = $("termRelBody");
  if (!d.found) {
    body.innerHTML = `<div class="empty">未找到该父 SKU 的商品标题，无法比对</div>`;
    return;
  }
  if (d.skipped) {
    body.innerHTML = `<div class="empty">${esc(d.skip_reason || "该搜索词不参与比较")}
（文本比对仅针对真实搜索词；投放 ASIN 来源于商品投放广告，无关键词含义）</div>`;
    return;
  }
  const ai = d.ai_score;
  const hits = d.hits || [];
  const basis = [];
  if (d.full) basis.push(`搜索词「${esc(d.term)}」<b>整词出现</b>在标题中`);
  if (hits.length) basis.push(`命中分词: ${hits.map((h) => `<b>${esc(h)}</b>`).join("、")}`);
  if (d.lcs && !d.full) basis.push(`最长公共片段: <b>${esc(d.lcs)}</b>`);
  if (!basis.length) basis.push(`标题中未出现搜索词的任何片段（文本匹配 ${d.text_score ?? 0} 分）`);
  const textPct = (d.text_score ?? 0);
  body.innerHTML = `
    <div class="rel-grid">
      <div class="rel-score-cell">
        <div class="rel-score-line">${relBadge(ai)}</div>
        <div class="rel-model">${esc(d.model || "")}${d.cached ? " · 缓存" : " · 实时判断"}</div>
      </div>
      <div class="rel-detail">
        <div class="rel-row"><span class="rel-k">语义依据 (AI)</span>
          <span class="rel-v">${ai != null ? esc(d.ai_reason || "-") : "<span class='muted'>待判断</span>"}</span></div>
        <div class="rel-row"><span class="rel-k">文本依据 (标题比对)</span>
          <span class="rel-v">${basis.join("；")}</span></div>
        <div class="rel-row"><span class="rel-k">标题</span>
          <span class="rel-v rel-title">${esc(d.title)}</span></div>
        <div class="rel-meters">
          <div class="meter"><span class="meter-k">文本匹配</span>
            <div class="meter-bar"><i style="width:${Math.min(100, textPct)}%"></i></div>
            <b>${textPct}</b></div>
          <div class="meter"><span class="meter-k">语义关联</span>
            <div class="meter-bar"><i style="width:${ai != null ? ai * 10 : 0}%"></i></div>
            <b>${ai != null ? ai : "-"}</b></div>
        </div>
      </div>
    </div>`;
  $("termRelTools").innerHTML =
    `<button class="btn btn-mini" id="termRelRetry">重新判断</button>`;
  $("termRelRetry").onclick = async () => {
    body.innerHTML = `<div class="empty">重新调用模型判断中…（约 2~5 秒）</div>`;
    try {
      const nd = await api("/api/term_relevance/pair?" +
        new URLSearchParams({ asin, term, force: "true" }).toString());
      renderTermRel(nd);
    } catch (e) {
      body.innerHTML = `<div class="empty">失败: ${esc(e.message)}</div>`;
    }
  };
}

/* ---------------- 批量关联度分析 (产品视角勾选父 SKU) ---------------- */
function syncRelBatchBtn() {
  const b = $("btnRelBatch");
  if (!b) return;
  b.disabled = !pvSel.size;
  b.textContent = pvSel.size ? `批量关联度分析（已选 ${pvSel.size}）` : "批量关联度分析";
}

function openRelBatch() {
  $("relBatchCount").textContent = pvSel.size;
  $("rbBody").innerHTML = `<div class="empty">勾选父 SKU 并选择选词方式后，点「开始分析」。结果按「词+标题」缓存，重复分析不重复调用模型。</div>`;
  $("rbMsg").textContent = "";
  $("relBatchModal").classList.add("open");
}

$("relBatchClose").onclick = () => {
  $("relBatchModal").classList.remove("open");
  stopRbPoll();
};
$("relBatchModal").onclick = (e) => {
  if (e.target.id === "relBatchModal") { $("relBatchModal").classList.remove("open"); stopRbPoll(); }
};

$("rbStart").onclick = async () => {
  const mode = document.querySelector('input[name="rbMode"]:checked').value;
  const body = { asins: [...pvSel], mode,
    top_n: +$("rbTopN").value || 10,
    min_spend: +$("rbMinSpend").value || 100 };
  $("rbMsg").textContent = "启动中…";
  try {
    const r = await api("/api/term_relevance/batch", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body) });
    if (r.detail) { $("rbMsg").textContent = "启动失败: " + r.detail; return; }
    $("rbMsg").textContent = "";
    renderRbProgress(r);
    startRbPoll();
  } catch (e) {
    $("rbMsg").textContent = "启动失败: " + e.message;
  }
};

function startRbPoll() {
  stopRbPoll();
  rbPoll = setInterval(async () => {
    try {
      const d = await api("/api/term_relevance/batch/status");
      renderRbProgress(d);
      if (!d.running) stopRbPoll();
    } catch (e) { /* 轮询失败忽略, 下次继续 */ }
  }, 2000);
}
function stopRbPoll() {
  if (rbPoll) { clearInterval(rbPoll); rbPoll = null; }
}

function renderRbProgress(d) {
  if (!d.total) {
    $("rbBody").innerHTML = `<div class="empty">未构造出任何分析任务（勾选的父 SKU 没有搜索词数据或标题）</div>`;
    return;
  }
  const done = d.done || 0, total = d.total;
  const pct = Math.round(done / total * 100);
  const head = `<div class="rb-progress">
    <div class="rb-bar"><i style="width:${pct}%"></i></div>
    <span class="rb-pct">${d.running ? "分析中" : "已完成"} ${done}/${total}（${pct}%）${d.failed ? ` · 失败 ${d.failed}` : ""}</span>
    ${d.running && d.current ? `<span class="rb-cur">当前: ${esc(d.current)}</span>` : ""}
    ${!d.running ? `<button class="btn btn-mini" id="rbExport">导出 CSV</button>` : ""}
  </div>`;
  const rows = (d.results || []).slice().sort((a, b) =>
    (b.ai_score ?? -1) - (a.ai_score ?? -1) || b.cost - a.cost);
  const badge = (s) => {
    if (s == null) return `<span class="rel-badge rel-none">失败</span>`;
    const cls = s >= 8 ? "rel-high" : s >= 5 ? "rel-mid" : s >= 3 ? "rel-low" : "rel-bad";
    return `<span class="rel-badge ${cls}">${s}</span>`;
  };
  const tbl = `<table class="rb-tbl"><thead><tr>
      <th>父 ASIN</th><th>搜索词</th><th class="num">花费</th>
      <th class="num">文本分</th><th class="num">语义分</th><th>依据</th><th>标题</th>
    </tr></thead><tbody>` +
    rows.map((r) => `<tr>
      <td class="rb-asin">${esc(r.asin)}</td>
      <td class="rb-term" title="${esc(r.term)}">${esc(r.term)}</td>
      <td class="num">${money(r.cost)}</td>
      <td class="num">${r.text_score ?? "-"}</td>
      <td class="num">${badge(r.ai_score)}</td>
      <td class="rb-reason">${esc(r.ai_reason || "-")}${r.cached ? ` <span class="muted">(缓存)</span>` : ""}</td>
      <td class="rb-title" title="${esc(r.title)}">${esc(r.title.slice(0, 40))}</td>
    </tr>`).join("") + `</tbody></table>`;
  $("rbBody").innerHTML = head + tbl;
  const ex = $("rbExport");
  if (ex) ex.onclick = () => exportRbCsv(rows);
}

function exportRbCsv(rows) {
  const head = ["父ASIN", "搜索词", "花费", "文本分", "语义分", "AI依据", "标题"];
  const lines = [head.join(",")].concat(rows.map((r) => [
    r.asin, r.term, r.cost, r.text_score ?? "", r.ai_score ?? "",
    r.ai_reason || "", r.title || "",
  ].map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")));
  const blob = new Blob(["\ufeff" + lines.join("\n")], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `关联度分析_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

/* ---------------- 配置管理页 ---------------- */
async function loadSettings() {
  $("stMsg").textContent = "加载中…";
  try {
    const d = await api("/api/settings");
    const fill = (sel, list, cur) => {
      sel.innerHTML = (list.length ? list : [cur]).map((m) =>
        `<option value="${esc(m)}" ${m === cur ? "selected" : ""}>${esc(m)}</option>`).join("");
      if (!list.includes(cur)) sel.insertAdjacentHTML("afterbegin",
        `<option value="${esc(cur)}" selected>${esc(cur)}（当前）</option>`);
    };
    fill($("stWbModel"), d.wb_models || [], d.wb_model || "");
    fill($("stWbVisionModel"), d.wb_models || [], d.wb_vision_model || "");
    fill($("stOlModel"), d.ollama_models || [], d.ollama_model || "");
    $("stMsg").textContent =
      `当前底座: ${d.wb_model} · 视觉: ${d.wb_vision_model}${d.updated_at ? " · 更新于 " + d.updated_at : ""}`;
  } catch (e) {
    $("stMsg").textContent = `加载失败: ${e.message}`;
  }
}

$("stSave").onclick = async () => {
  $("stMsg").textContent = "保存中…";
  try {
    await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      wb_model: $("stWbModel").value,
      wb_vision_model: $("stWbVisionModel").value,
      ollama_model: $("stOlModel").value,
    }) });
    toast("已保存，立即生效");
    loadSettings();
  } catch (e) {
    toast("保存失败: " + e.message);
  }
};

async function stTest(kind, btn) {
  const box = $("stTestResult");
  box.style.display = "";
  box.innerHTML = `<div class="empty">测试中（现场调用一次模型${kind === "vision" ? "，视觉调用约 2~40 秒" : "，约 2~5 秒"}）…</div>`;
  btn.disabled = true;
  try {
    const d = await api(`/api/settings/test?kind=${kind}`, { method: "POST" });
    const max = kind === "vision" ? 100 : 10;
    if (d.ok) {
      box.innerHTML = `<div class="st-ok">✅ ${esc(d.model)} 可用 · ${d.elapsed}s ·
        样例评分 <b>${d.score}/${max}</b> — ${esc(d.reason || "")}</div>`;
    } else {
      box.innerHTML = `<div class="st-err">❌ 测试失败（${d.elapsed}s）：${esc(d.error || "")}</div>`;
    }
  } catch (e) {
    box.innerHTML = `<div class="st-err">❌ 请求失败: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}
$("stTest").onclick = () => stTest("text", $("stTest"));
$("stTestVision").onclick = () => stTest("vision", $("stTestVision"));

function renderTermTop(d) {
  const p = d.product || {};
  const pa = p.parent_asin || d.parent_asin || "";
  const mainUrl = p.main_image || p.img_url || "";
  const thumbs = p.images || [];                    // 附图(接口已排除主图)
  const amz = p.amz || {};
  const bsr = amz.bsr_small
    ? `${esc(amz.bsr_small_cat || "小类目")} ${nf(amz.bsr_small)}位` : "";
  const childN = p.child_count != null ? p.child_count : (p.child_asins || []).length;
  // 该词的竞品素材是否已拉到本地 + 该父体的整体进度(后端现算)
  const st = d.status || {};
  const stTip = d.fetched
    ? `竞品结果已在本地（主图 ${d.images || 0} 张），本页直接读本地，不再访问亚马逊`
      + (st.terms_total ? `；该父体进度 ${st.terms_fetched}/${st.terms_total}（${st.fetch_text || ""}）` : "")
    : "竞品结果还没拉到本地，本页会实时访问亚马逊抓取"
      + (st.terms_total ? `；该父体进度 ${st.terms_fetched}/${st.terms_total}（${st.fetch_text || ""}）` : "");

  const thumbHtml = thumbs.map((im, i) => {
    const t = im.thumb || im.large;
    return `<img src="${esc(amzMidImage(t, 160))}" data-big="${esc(im.large)}"
      data-fallback="${esc(t)}" data-idx="${i}" loading="lazy"
      title="附图 ${i + 1}${im.asin ? " · 来自 " + esc(im.asin) : ""}">`;
  }).join("");

  $("termTop").innerHTML = `
    <div class="tg-main">
      ${mainUrl
        ? `<img id="tgMainImg" src="${esc(amzMidImage(mainUrl))}" data-big="${esc(mainUrl)}"
             data-fallback="${esc(mainUrl)}" data-cap="${esc(pa)}" alt="">`
        : `<div class="empty" style="padding:70px 0">暂无图片</div>`}
    </div>
    <div class="tg-info">
      <div class="tg-term-line">
        <span class="tg-term">${esc(d.query || "")}</span>
        <span class="term-zh-lg ${state.translations[d.query] ? "" : "pending"}" id="tgTermZh"></span>
        <span class="tg-term-label" id="tgTermLabel" data-l="${esc(d.term_label || "")}"${d.term_label ? "" : ' style="display:none"'}>${esc(d.term_label || "")}</span>
        <span title="${esc(stTip)}">${stTag(d.fetched ? "done" : "none",
          d.fetched ? "素材已拉取" : "素材未拉取")}</span>
      </div>
      <div class="tg-title" title="${esc(p.title || "")}">${esc(p.title || "-")}</div>
      <div class="tg-meta">
        <span class="tg-price">${p.price ? esc(p.price) : "-"}</span>
        <span class="tg-kv">
          <span>父 ASIN <b class="tg-asin" data-copy="${esc(pa)}" title="点击复制">${esc(pa)}</b></span>
          ${p.sku ? `<span>父 SKU <b>${esc(p.sku)}</b></span>` : ""}
          <span>子体数 <b>${childN}</b></span>
          <span>附图 <b>${thumbs.length}</b> 张</span>
          ${amz.rating ? `<span>星级 <b>${esc(amz.rating)}</b>${amz.rating_count ? ` (${nf(amz.rating_count)})` : ""}</span>` : ""}
          ${bsr ? `<span>BSR <b>${bsr}</b></span>` : ""}
        </span>
      </div>
      <div class="tg-thumb-bar">
        <button class="btn btn-mini" id="tgFetchImgs"
          title="抓取该父体的图片来源子 ASIN 商品页, 取其全部附图">抓取附图</button>
        ${p.image_source_asin ? `<span class="pmeta">图片来源子体 <b>${esc(p.image_source_asin)}</b>
          ${p.image_source_crawled ? "" : "（未抓取）"}</span>` : ""}
        <span class="pmeta" id="tgImgMsg"></span>
      </div>
      <div class="tg-thumbs">${thumbHtml || '<span class="pmeta">暂无附图</span>'}</div>
    </div>`;

  const main = $("tgMainImg");
  if (main) main.onclick = () => openImagePreview(main.dataset.big, main.dataset.cap, main.dataset.fallback);
  $("termTop").querySelectorAll(".tg-thumbs img").forEach((im) => im.onclick = () => {
    const m = $("tgMainImg");
    if (!m) return;
    m.removeAttribute("data-fallback");
    m.src = amzMidImage(im.dataset.big, 500);
    m.dataset.big = im.dataset.big;
    m.dataset.fallback = im.dataset.fallback;
  });
  const cp = $("termTop").querySelector(".tg-asin[data-copy]");
  if (cp) cp.onclick = () => copyText(cp.dataset.copy, "父 ASIN ");
  updateTgTermZh(d.query);                 // 译文(缓存立即显示)
  ensureTermList([d.query], () => updateTgTermZh(d.query), "termDetail");   // 没有则排队生成
  const fb = $("tgFetchImgs");
  if (fb) fb.onclick = () => doFetchParentImages(pa, d.query, true);
  if (_imgDoneMsg && $("tgImgMsg")) $("tgImgMsg").textContent = _imgDoneMsg;

  // 图片来源子体尚未抓过 -> 首次进入自动抓一次(每个父体每次打开只自动触发一次)
  if (pa && !p.image_source_crawled && !_imgTried.has(pa)) {
    _imgTried.add(pa);
    doFetchParentImages(pa, d.query, false);
  }
}

/* ---------- 搜索词详情页: 相关度打标 (紧密 / 宽泛 / 不太相关 / 不相关)
   交互: 单击选项 = 选中(点第二次取消), 点「确认」才写库
   选项顺序由后端 db.TERM_LABELS 决定, 前端不写死 ---------- */
const termLabel = { asin: "", term: "", cur: "", sel: "" };

function termLabelMsg(t) {
  const el = $("termLabelMsg");
  if (el) el.textContent = t || "";
}

function paintTermLabelOpts() {
  const box = $("termLabelOpts");
  if (!box) return;
  box.querySelectorAll(".tlb-opt").forEach((el) => {
    el.classList.toggle("sel", el.dataset.v === termLabel.sel);
  });
}

function renderTermLabelBar(d) {
  const opts = d.label_options || ["紧密", "宽泛", "不太相关", "不相关"];
  termLabel.asin = d.parent_asin || (d.product || {}).parent_asin || "";
  termLabel.term = d.query || "";
  termLabel.cur = d.term_label || "";
  termLabel.sel = termLabel.cur;          // 默认选中已打的标, 未打标则不选
  const box = $("termLabelOpts");
  box.innerHTML = opts.map((o) =>
    `<span class="tlb-opt" data-v="${esc(o)}">${esc(o)}</span>`).join("");
  box.querySelectorAll(".tlb-opt").forEach((el) => {
    el.onclick = () => {
      termLabel.sel = termLabel.sel === el.dataset.v ? "" : el.dataset.v;
      paintTermLabelOpts();
      termLabelMsg(termLabel.sel
        ? `已选「${termLabel.sel}」，点「确认」生效`
        : `已取消选择${termLabel.cur ? `（已保存的是「${termLabel.cur}」）` : ""}`);
    };
  });
  paintTermLabelOpts();
  termLabelMsg(termLabel.cur ? `当前：${termLabel.cur}` : "尚未打标");
}

function updateTgTermLabel(label) {
  const el = $("tgTermLabel");
  if (!el) return;
  el.textContent = label || "";
  el.dataset.l = label || "";        // 角标底色跟着四档配色走(见 style.css)
  el.style.display = label ? "" : "none";
}

async function saveTermLabel(label) {
  if (!termLabel.asin || !termLabel.term) { termLabelMsg("缺少父 ASIN / 搜索词"); return; }
  try {
    const r = await api("/api/product/term_label", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asin: termLabel.asin, term: termLabel.term, label }),
    });
    if (r.detail) { termLabelMsg("保存失败：" + r.detail); return; }
    termLabel.cur = r.label || "";
    termLabel.sel = termLabel.cur;
    cacheTermLabel(termLabel.asin, termLabel.term, termLabel.cur);   // 同步列表页缓存
    paintTermLabelOpts();
    updateTgTermLabel(termLabel.cur);
    termLabelMsg(r.label ? `已保存：${r.label}` : "已清除打标");
    toast(r.label ? `已打标「${r.label}」` : "已清除打标");
  } catch (e) {
    termLabelMsg("保存失败：" + e.message);
  }
}

function bindTermLabelBar() {
  const save = $("termLabelSave"), clear = $("termLabelClear");
  if (save) save.onclick = () => {
    if (!termLabel.sel) { termLabelMsg("请先单击选择一个标签"); return; }
    saveTermLabel(termLabel.sel);
  };
  if (clear) clear.onclick = () => saveTermLabel("");
}
bindTermLabelBar();

/* 搜索词详情页顶部的译文展示 */
function updateTgTermZh(q) {
  const el = $("tgTermZh");
  if (!el) return;
  const zh = state.translations[q] || "";
  if (zh) { el.textContent = `中文：${zh}`; el.classList.remove("pending"); }
  else if (state.transPending.has(q)) {
    el.textContent = "翻译生成中…"; el.classList.add("pending");
  } else { el.textContent = ""; el.classList.remove("pending"); }
}

function renderTermMetrics(d) {
  const t = d.term || {};
  const pctf2 = (v) => (Number(v) || 0).toFixed(2) + "%";
  const num2 = (v) => (Number(v) || 0).toFixed(2);
  // 单行表格: 表头=指标名, 数据行=值
  const cols = [
    ["曝光", nf(t.impressions)],
    ["点击", nf(t.clicks)],
    ["点击率", pctf2(t.ctr)],
    ["花费", money(t.adCost)],
    ["CPC", money(t.cpc)],
    ["订单", nf(t.orderNum)],
    ["销量", nf(t.adSaleNum)],
    ["销售额", money(t.adSales)],
    ["转化率", pctf2(t.cvr)],
    ["ACoS", pctf2(t.acos)],
    ["ROAS", num2(t.roas)],
    ["CPA", money(t.cpa)],
    ["涉及活动", nf(t.campaign_count)],
    ["匹配方式", esc(t.matchType || "-")],
    ["维度", esc(t.dimension || "-")],
    ["ABA搜索词排名", t.searchFrequencyRank ? nf(t.searchFrequencyRank) : "-"],
  ];
  $("termMetrics").innerHTML = `<table class="tm-tbl">
    <thead><tr>${cols.map((c) => `<th>${esc(c[0])}</th>`).join("")}</tr></thead>
    <tbody><tr>${cols.map((c) => `<td>${c[1]}</td>`).join("")}</tr></tbody>
  </table>`;
  const ags = t.adGroupNames || [];
  $("termMetricsTools").innerHTML =
    `<span class="pmeta">搜索词：<b>${esc(t.query || "")}</b></span>` +
    (ags.length ? `<span class="pmeta">广告组：${esc(ags.slice(0, 3).join("、"))}${ags.length > 3 ? ` 等 ${ags.length} 个` : ""}</span>` : "") +
    (t.found ? "" : `<span class="pmeta warn">⚠ 未在广告数据中匹配到该词（可能已过期）</span>`);
}

/* ---------------- 亚马逊搜索结果卡片 ---------------- */
async function loadAmzCards(term, refresh) {
  const url = `https://www.amazon.co.jp/s?k=${encodeURIComponent(term)}`;
  $("termAmzOpen").href = url;
  amzSum.base = "查询中…";
  amzSum.sim = "";
  paintAmzMeta();
  $("amzCards").innerHTML = `<div class="amz-loading">正在抓取亚马逊搜索结果…（命中缓存立即返回；否则用浏览器加载页面，约 10~25 秒）</div>`;
  $("termAmzRefresh").disabled = true;
  try {
    const p = new URLSearchParams({ query: term });
    if (refresh) p.set("refresh", "true");
    const d = await api("/api/amazon/search?" + p.toString());
    if (d.detail) {
      $("amzCards").innerHTML = `<div class="amz-loading">抓取失败：${esc(d.detail)}</div>`;
      amzSum.base = "失败";
      paintAmzMeta();
      return;
    }
    if (d.url) $("termAmzOpen").href = d.url;
    const items = d.items || [];
    amzSum.base = (d.blocked ? "⚠ 疑似被验证码拦截 · " : "") +
      `解析 ${d.item_count} 条 · ${d.from_cache ? "缓存" : "实时抓取"} · ${d.fetched_at || ""}`;
    amzSum.sim = "";
    paintAmzMeta();
    $("amzCards").innerHTML = items.map(amzCardHtml).join("") ||
      `<div class="empty">没有抓到搜索结果（可能被验证码拦截，可点「重新抓取」）</div>`;
    $("amzCards").querySelectorAll("img[data-preview]").forEach((img) => img.onclick = () =>
      openImagePreview(img.dataset.preview, img.dataset.cap, img.dataset.fallback));
    if ($("simMatch") && $("simMatch").checked) loadSimMatch(term);
    loadDeepVerdict(term);
  } finally {
    const b = $("termAmzRefresh");
    if (b) b.disabled = false;
  }
}

function amzCardHtml(it) {
  // 本地有主图时缩略图/大图都用本地文件(不再联亚马逊 CDN)
  const local = it.image_local || "";
  const img = local || it.image || it.image_big || "";
  const big = local || it.image_big || it.image || "";
  const price = it.price
    ? `<div class="card-price">${esc(it.price)}${it.list_price ? `<span class="list">${esc(it.list_price)}</span>` : ""}</div>`
    : `<div class="card-price">-</div>`;
  const star = it.rating_value
    ? `<span class="star">★${esc(it.rating_value)}</span>${it.review_count ? `<span class="rev">(${nf(it.review_count)})</span>` : ""}`
    : "";
  const ship = it.delivery ? `<span>${esc(String(it.delivery).slice(0, 24))}</span>` : "";
  const tags = [it.points, it.coupon, it.discount, it.stock, it.bought_recently]
    .filter(Boolean).slice(0, 3)
    .map((x) => `<span class="amz-tag">${esc(String(x).slice(0, 16))}</span>`).join("");
  const cap = `${it.asin || ""}${it.title ? " · " + it.title : ""}`;
  return `<div class="amz-card2">
    <div class="card-img">
      <span class="card-pos">${esc(String(it.position ?? ""))}</span>
      ${it.sponsored ? `<span class="card-ad">${esc(it.ad_label || "广告")}</span>` : ""}
      ${img ? `<img src="${esc(local || amzMidImage(img, 320))}" loading="lazy" alt=""
          data-preview="${esc(big)}" data-fallback="${esc(img)}" data-cap="${esc(cap)}">` : ""}
    </div>
    <div class="card-body">
      ${it.brand ? `<div class="card-brand" title="${esc(it.brand)}">${esc(it.brand)}</div>` : ""}
      <div class="card-title" title="${esc(it.title || "")}">
        <a class="cell-link" href="${esc(it.url || "#")}" target="_blank" rel="noopener">${esc(it.title || "")}</a>
      </div>
      ${price}
      <div class="card-meta">${star}${ship}</div>
      <div class="card-tags">${tags}</div>
    </div>
  </div>`;
}

/* ---------------- 深度检测: 结果区汇总文案 (基础信息 / 整体相似度 分开拼) ---------------- */
const amzSum = { base: "", sim: "" };

function paintAmzMeta() {
  const el = $("amzCardsMeta");
  if (!el) return;
  el.textContent = [amzSum.base, amzSum.sim].filter(Boolean).join(" · ");
}

/* ---------------- 整体相似度: 我的品(标题+主图) × 竞品(标题+主图) ----------------
   一次把「我的标题+我的主图+竞品标题+竞品主图」交给同一个多模态大模型整体判断,
   不再拆成「标题比标题 / 图比图」两个分数。 */
function simLevel(score) {
  if (score == null) return "rel-none";
  return score >= 75 ? "rel-high" : score >= 55 ? "rel-mid" : "rel-low";
}

let simPoll = null;

function stopSimPoll() {
  if (simPoll) { clearInterval(simPoll); simPoll = null; }
}

async function loadSimMatch(term) {
  const asin = new URLSearchParams(location.search).get("asin") || "";
  if (!asin || !term) return;
  amzSum.sim = "整体比对中…";
  paintAmzMeta();
  stopSimPoll();
  let d;
  try {
    d = await api("/api/sku_similarity?" +
      new URLSearchParams({ asin, term, top_n: 10 }).toString(), { method: "POST" });
  } catch (e) {
    amzSum.sim = "整体比对启动失败";
    paintAmzMeta();
    return;
  }
  renderSim(d);
  if (d.running) {
    simPoll = setInterval(async () => {
      try {
        const s = await api("/api/sku_similarity/status?" +
          new URLSearchParams({ asin, term }).toString());
        renderSim(s);
        if (!s.running) { stopSimPoll(); loadDeepVerdict(term, true); }
      } catch (e) { /* 忽略单次轮询失败 */ }
    }, 2500);
  }
}

function renderSim(d) {
  if (!d.items || !d.items.length) {
    amzSum.sim = `整体相似度: ${d.note || "无结果"}`;
    paintAmzMeta();
    return;
  }
  const cards = $("amzCards").querySelectorAll(".amz-card2");
  d.items.forEach((it, i) => {
    const card = cards[i];
    if (!card) return;
    const holder = card.querySelector(".card-img");
    if (!holder) return;
    let badge = holder.querySelector(".sim-badge");
    if (!badge) {
      holder.insertAdjacentHTML("beforeend", `<span class="sim-badge rel-none">…</span>`);
      badge = holder.querySelector(".sim-badge");
    }
    if (it.score == null) {
      badge.className = "sim-badge rel-none";
      badge.textContent = d.running ? "分析中" : "失败";
      badge.title = it.reason || "";
      return;
    }
    badge.className = `sim-badge ${simLevel(it.score)}`;
    badge.textContent = `相似 ${it.score}%`;
    badge.title = `[${d.model || ""}] ${it.reason || ""}`;
  });
  const pct = d.total ? Math.round((d.done || 0) / d.total * 100) : 0;
  amzSum.sim = d.running
    ? `整体比对中 ${d.done || 0}/${d.total}（${pct}%）· ${d.model || ""}`
    : `整体相似度（${d.model || ""}）: 平均 ${d.avg ?? "-"}% / 最高 ${d.max ?? "-"}%`;
  paintAmzMeta();
}

/* 等整体相似度任务结束(重新评估时用): 返回最终状态, 超时返回 null */
async function waitSimJob(asin, term) {
  for (let i = 0; i < 90; i++) {
    const s = await api("/api/sku_similarity/status?" +
      new URLSearchParams({ asin, term }).toString());
    renderSim(s);
    if (!s.running) return s;
    await new Promise((r) => setTimeout(r, 2000));
  }
  return null;
}

$("simMatch").onchange = () => {
  if ($("simMatch").checked) {
    const t = new URLSearchParams(location.search).get("term") || "";
    if (t) loadSimMatch(t);
  } else {
    stopSimPoll();
    $("amzCards").querySelectorAll(".sim-badge").forEach((b) => b.remove());
    amzSum.sim = "";
    paintAmzMeta();
  }
};

/* ---------------- 词级结论: 结果集评估 + 是否否定建议 ---------------- */
async function loadDeepVerdict(term, force) {
  const asin = new URLSearchParams(location.search).get("asin") || "";
  if (!asin || !term) return;
  if (!force && loadDeepVerdict._done === `${asin}|${term}`) return;
  const body = $("termDeepBody");
  if (!body) return;
  body.innerHTML = `<div class="empty">评估中（首次需调模型，约 3~5 秒）…</div>`;
  let d;
  try {
    d = await api("/api/deep_check?" +
      new URLSearchParams({ asin, term }).toString());
  } catch (e) {
    body.innerHTML = `<div class="empty">评估失败: ${esc(e.message)}</div>`;
    return;
  }
  loadDeepVerdict._done = `${asin}|${term}`;
  renderDeepVerdict(d);
}

function renderDeepVerdict(d) {
  const body = $("termDeepBody");
  if (!d) return;
  if (d.note && d.verdict == null && d.sim_count === 0) {
    body.innerHTML = `<div class="empty">${esc(d.note)}</div>`;
    return;
  }
  const adviceCls = String(d.advice || "").startsWith("保留") ? "rel-high"
    : String(d.advice || "").startsWith("观察") ? "rel-mid" : "rel-bad";
  const cls = (s) => s == null ? "rel-none" : s >= 70 ? "rel-high" : s >= 40 ? "rel-mid" : "rel-bad";
  const v = d.verdict || {};
  body.innerHTML = `
    <div class="rel-grid">
      <div class="rel-score-cell">
        <div class="rel-score-line">
          <span class="rel-badge ${cls(d.total)}">${d.total ?? "-"}<i>/100</i> 综合相关度</span>
        </div>
        <div style="margin-top:8px">
          <span class="rel-badge ${adviceCls}">${esc(d.advice || "-")}</span>
        </div>
        <div class="rel-model">${esc(d.model || "")}</div>
      </div>
      <div class="rel-detail">
        <div class="rel-row"><span class="rel-k">否定建议依据</span>
          <span class="rel-v">${esc(d.advice_reason || "-")}
            <span class="muted">（该词花费 ${money(d.spend || 0)} · 订单 ${nf(d.orders || 0)}）</span></span></div>
        <div class="rel-row"><span class="rel-k">搜索意图</span>
          <span class="rel-v">${esc(v.intent || "-")} — <b>${v.score ?? "-"}</b>/100 ${esc(v.reason || "")}</span></div>
        <div class="rel-row"><span class="rel-k">结果集整体相似</span>
          <span class="rel-v">${d.sim_count ? `平均 <b>${d.sim_avg ?? "-"}</b> 分 / 最高 ${d.sim_max ?? "-"} 分（${d.sim_count} 条）· 其中 ${d.sim_same ?? 0} 条 ≥70 分` : "<span class='muted'>未做整体相似度比对</span>"}</span></div>
        <div class="rel-meters">
          <div class="meter"><span class="meter-k">词级意图</span>
            <div class="meter-bar"><i style="width:${v.score ?? 0}%"></i></div><b>${v.score ?? "-"}</b></div>
          <div class="meter"><span class="meter-k">整体相似</span>
            <div class="meter-bar"><i style="width:${d.sim_avg ?? 0}%"></i></div><b>${d.sim_avg ?? "-"}</b></div>
        </div>
      </div>
    </div>`;
  $("termDeepTools").innerHTML = `<button class="btn btn-mini" id="deepReEval">重新评估</button>`;
  $("deepReEval").onclick = async () => {
    body.innerHTML = `<div class="empty">重新评估中（重算整体相似度 + 词级结论）…</div>`;
    stopSimPoll();
    try {
      const q = new URLSearchParams({ asin: d.asin, term: d.term }).toString();
      if ($("simMatch") && $("simMatch").checked) {
        await api("/api/sku_similarity?" + q + "&top_n=10&refresh=true",
          { method: "POST" });
        await waitSimJob(d.asin, d.term);
      }
      loadDeepVerdict._done = "";
      const nd = await api("/api/deep_check?" + q + "&refresh=true");
      renderDeepVerdict(nd);
    } catch (e) {
      body.innerHTML = `<div class="empty">失败: ${esc(e.message)}</div>`;
    }
  };
}

/* ---------------- 独立页面入口 ---------------- */
async function bootStandalone(sp) {
  document.body.classList.add("standalone");
  const page = sp.get("page");
  const asin = (sp.get("asin") || "").trim();
  if (page === "terms") {
    $("termsView").style.display = "";
    await renderTermsPage(asin);
  } else if (page === "term") {
    $("termView").style.display = "";
    await renderTermPage(asin, (sp.get("term") || "").trim(), sp.get("mt") || "");
  }
}

$("termAmzRefresh").onclick = () => {
  const t = new URLSearchParams(location.search).get("term") || "";
  if (t) loadAmzCards(t, true);
};

boot();
initPrefetchState();      // 页面打开就同步预抓取进度(有任务在跑时自动显示)
initFetchState();         // 全量拉取素材的进度同理

/* ============ 配置: 品名解析用本地 Ollama 还是 workbuddy ============ */
function cfgProvider() {
  const el = [...document.querySelectorAll('input[name=cfgProvider]')].find((x) => x.checked);
  return el ? el.value : "ollama";
}

function syncCfgGroups() {
  const v = cfgProvider();
  $("cfgOllamaGroup").classList.toggle("off", v !== "ollama");
  $("cfgWbGroup").classList.toggle("off", v !== "workbuddy");
}

/* 表单里的值(Key 留空表示不修改) */
function cfgForm() {
  const out = {
    provider: cfgProvider(),
    ollama_url: $("cfgOllamaUrl").value.trim(),
    ollama_model: $("cfgOllamaModel").value.trim(),
    workbuddy_base: $("cfgWbBase").value.trim(),
    workbuddy_model: $("cfgWbModel").value.trim(),
    workbuddy_fallback: $("cfgWbFallback").value
      .split(/[,，]/).map((s) => s.trim()).filter(Boolean),
  };
  const k = $("cfgWbKey").value.trim();
  if (k) out.workbuddy_key = k;
  return out;
}

function paintCfgStatus(d) {
  const c = d.config || {};
  const st = d.status || {};
  $("cfgWbKeyState").textContent = c.workbuddy_key_set
    ? `已保存(尾号 ${c.workbuddy_key_tail})` : "未配置";
  $("cfgProviderHint").textContent =
    `可用性：Ollama ${st.ollama ? "✔" : "✘"} · WorkBuddy ${st.workbuddy ? "✔" : "✘"}`;
}

async function openAiConfig() {
  $("cfgModal").classList.add("open");
  $("cfgTestOut").textContent = "";
  try {
    const d = await api("/api/settings/ai");
    const c = d.config || {};
    document.querySelectorAll('input[name=cfgProvider]')
      .forEach((el) => { el.checked = el.value === (c.provider || "ollama"); });
    $("cfgOllamaUrl").value = c.ollama_url || "";
    $("cfgOllamaModel").value = c.ollama_model || "";
    $("cfgWbBase").value = c.workbuddy_base || "";
    $("cfgWbModel").value = c.workbuddy_model || "";
    $("cfgWbFallback").value = (c.workbuddy_fallback || []).join(", ");
    $("cfgWbKey").value = "";
    paintCfgStatus(d);
    syncCfgGroups();
  } catch (e) { $("cfgTestOut").textContent = "读取配置失败: " + e.message; }
}

async function saveAiConfig() {
  $("cfgTestOut").textContent = "保存中…";
  $("cfgSave").disabled = true;
  try {
    const d = await api("/api/settings/ai", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfgForm()),
    });
    if (d.detail) { $("cfgTestOut").textContent = "保存失败: " + d.detail; return; }
    $("cfgWbKey").value = "";
    paintCfgStatus(d);
    $("cfgTestOut").textContent = "已保存";
    toast("配置已保存");
    // 已解析过的缓存不重算; 本页还没解析的立刻按新 provider 走
    ol.aiLabels = {};
    if (ol.loaded && ol.pageType === "parent") {
      ensureAiLabels(ol.rows.map((r) => r.asin).filter(Boolean));
    }
  } catch (e) { $("cfgTestOut").textContent = "保存失败: " + e.message; }
  finally { $("cfgSave").disabled = false; }
}

async function testAiConfig() {
  $("cfgTestOut").textContent = "测试中…(在线模型约 10~30 秒)";
  $("cfgTest").disabled = true;
  try {
    const d = await api("/api/settings/ai/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(cfgForm()),
    });
    $("cfgTestOut").textContent = d.ok
      ? `✔ ${d.provider} / ${d.model} → 「${d.sample}」`
      : `✘ ${d.provider} 失败: ${d.error || "未知错误"}`;
  } catch (e) { $("cfgTestOut").textContent = "测试失败: " + e.message; }
  finally { $("cfgTest").disabled = false; }
}

/* 忽略缓存, 用当前配置重算「在线产品 · 父体」本页品名 */
async function relabelCurrentPage() {
  const asins = ((ol.rows || []).map((r) => r.asin)).filter(Boolean);
  if (!asins.length) {
    $("cfgTestOut").textContent = "当前页没有父体数据(先切到「在线产品 · 父体」)";
    return;
  }
  $("cfgTestOut").textContent = `已提交重算 ${asins.length} 个父 ASIN…`;
  try {
    const r = await api("/api/online/ai_labels", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ asins, refresh: true }),
    });
    ol.aiLabels = Object.assign({}, r.labels || {});
    document.querySelectorAll("#olWrap span[data-ai-asin]").forEach((el) => {
      if (!(ol.aiLabels || {})[el.dataset.aiAsin]) {
        el.textContent = "解析中…";
        el.classList.add("ol-ai-empty");
      }
    });
    paintAiLabels();
    pollAiLabels(asins);
  } catch (e) { $("cfgTestOut").textContent = "重算失败: " + e.message; }
}

$("btnAiConfig").onclick = openAiConfig;
$("cfgClose").onclick = () => $("cfgModal").classList.remove("open");
$("cfgModal").onclick = (e) => { if (e.target.id === "cfgModal") $("cfgModal").classList.remove("open"); };
document.querySelectorAll('input[name=cfgProvider]').forEach((el) => el.onchange = syncCfgGroups);
$("cfgSave").onclick = saveAiConfig;
$("cfgTest").onclick = testAiConfig;
$("cfgReLabel").onclick = relabelCurrentPage;
