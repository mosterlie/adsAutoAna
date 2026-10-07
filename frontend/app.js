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
  amzQuery: "", amzItems: [],
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
  const meta = await api("/api/tabs");
  state.tabs = meta.tabs; state.today = meta.today; state.defaultStart = meta.default_start;
  state.tab = state.tabs[1] ? state.tabs[1].key : state.tabs[0].key;   // 默认「广告活动」
  applyDefaultRange();
  renderTabs();
  renderScopeOptions();
  await Promise.all([loadStatus(), loadShops(), loadPortfolios()]);
  applyDefaultRange();          // loadStatus 拿到快照区间后再对齐一次
  await loadFilterMeta();
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
    renderTabs(); renderScopeOptions(); loadFilterMeta().then(loadAll);
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

/* 搜索词页签: 单元格渲染为「可点击 -> 打开亚马逊搜索结果页」*/
function cellHtml(c, r) {
  const v = fmtCell(c.key, r[c.key]);
  const isTerm = state.tab === "search" && (c.key === "query" || c.key === "keywordText") && r[c.key];
  if (isTerm) return `<span class="amz-link" data-q="${esc(r[c.key])}" title="点击打开亚马逊搜索结果页并取回数据">${v}</span>`;
  return v;
}

/* 打开亚马逊搜索结果页 + 用浏览器取回该页数据 */
function openAmazon(q) {
  if (!q) return;
  const url = `https://www.amazon.co.jp/s?k=${encodeURIComponent(q)}`;
  window.open(url, "_blank", "noopener");
  showAmazon(q, false);
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
    : "正在查询（命中缓存立即返回；否则用浏览器加载页面，约 10~25 秒）…"}</div>`;
  $("amzModal").classList.add("open");
  $("amzRefresh").disabled = true;
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
    const src = d.from_cache ? "缓存" : "实时抓取";
    const items = d.items || [];
    state.amzItems = items;
    const covered = AMZ_FIELD_LABELS
      .filter(([k]) => items.some((it) => amzVal(it, k))).length;
    $("amzMeta").textContent = (d.blocked ? "⚠ 疑似被验证码拦截 · " : "") +
      `页面「${d.page_title || ""}」· 解析 ${d.item_count} 条 · ` +
      `抓到 ${covered} 类字段 · ${src} · 抓取于 ${d.fetched_at || "-"} · #${d.search_id || "-"}`;
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
      return `<tr class="amz-row" data-i="${idx}">
      <td class="p">${it.position}</td>
      <td class="amz-img-td">${it.image ? `<img class="amz-thumb" src="${esc(it.image)}" loading="lazy" alt=""
          data-preview="${esc(it.image_big || amzBigImage(it.image))}" data-fallback="${esc(it.image)}"
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
  if (v === "product" && !pv.loaded) { pv.loaded = true; goProducts(); }
  if (v === "online" && !ol.loaded) { ol.loaded = true; showOnline(); }
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
        <span class="pmeta">以<b>父 ASIN</b> 为一条记录；同一父体下的多个子 ASIN 指标合并统计。用「操作」列的<b>活动</b>／<b>搜索词</b>下钻</span>
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
      <td class="col-kid kid-cell" data-kid="${esc(pa)}" title="${esc(tip)}">${kidHtml}</td>
      <td class="col-title p-title-cell" title="${esc(r.title)}">${esc(r.title || "")}</td>
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
      <th class="col-img">图</th>
      ${sortableTh(sort1, { cls: "col-asin", label: "父ASIN", field: "parent_asin",
        title: "点击按父 ASIN 排序" })}
      ${sortableTh(sort1, { cls: "col-sku", label: "父SKU", field: "sku",
        title: "点击按父 SKU 排序" })}
      <th class="col-kid" title="蓝底 = 正在投放广告的子 ASIN（已排在最前）；灰字 = 未投放广告的变体。点击单元格查看全部并跳转">子ASIN</th>
      ${sortableTh(sort1, { cls: "col-title", label: "标题", field: "title",
        title: "点击按标题排序" })}
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
  $("pWrap").querySelectorAll('button[data-act="pterms"]').forEach((btn) => btn.onclick = () => {
    const pa = btn.dataset.pa;
    const p = pv.rows.find((x) => String(x.parent_asin || x.asin) === pa);
    loadParentTerms(pa, p);
  });
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

  const body = rows.map((r) => {
    const isKw = r.scope.endsWith("keyword");
    const val = esc(r.query || "");
    const cell = isKw
      ? `<span class="amz-link" data-q="${val}" title="点击查看该搜索词在亚马逊的搜索结果">${val}</span>`
      : `<a class="cell-link" href="https://www.amazon.co.jp/dp/${val}" target="_blank" rel="noopener">${val}</a>`;
    return `<tr>
      <td><span class="dim-tag ${isKw ? "kw" : "tg"}">${r.dimension}</span></td>
      <td>${cell}</td>
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
  loaded: false,
};

/* 原站默认列: key / 表头名 / 取值字段(可多字段合成一列) / 渲染类型 */
const OL_COLUMNS = [
  { key: "onlineStatus", label: "状态", fields: ["onlineStatus"], type: "status" },
  { key: "image", label: "图片", fields: ["mainBigImage"], type: "image" },
  { key: "asinSku", label: "ASIN/MSKU", fields: ["asin", "sku"], type: "asinlink" },
  { key: "title", label: "标题", fields: ["title"], type: "title" },
  { key: "analyze", label: "分析", fields: [], type: "icons" },
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
const OL_NUM_TYPES = new Set(["money", "money2", "profit", "num0", "num1", "num4", "tri", "tri1", "triMoney"]);

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
    case "title":
      return `<div class="p-title-cell" title="${esc(olVal(r.title))}">${olPlain(r.title)}</div>`;
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
    renderOnline(); loadOnlineProducts();
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
  const head = cols.map((c) => {
    const num = olIsNumCol(c);
    const sortable = c.fields.length === 1;
    return `<th class="${num ? "num" : ""}" data-k="${c.key}"${sortable ? ` title="点击排序"` : ""}>${esc(c.label)}
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
  olBindPager();
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

boot();
