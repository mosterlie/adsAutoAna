/* 活动精细分析 */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");
let analysis = null;
let activeDim = "trend";
let searchSheet = "summary";

const TARGET_TYPE_CN = { auto: "自动投放", keyword: "关键词投放", targeting: "商品定向" };
const TARGET_TEXT_CN = {
  queryHighRelMatches: "紧密匹配",
  queryBroadRelMatches: "宽泛匹配",
  asinSubstituteRelated: "同类商品",
  asinAccessoryRelated: "关联商品",
};
const MATCH_TYPE_CN = {
  close_match: "紧密匹配",
  loose_match: "宽泛匹配",
  complements: "关联商品",
  substitutes: "同类商品",
  exact: "精准",
  phrase: "词组",
  broad: "广泛",
  negativeExact: "否定精准",
  negativePhrase: "否定词组",
};
const QUERY_TYPE_CN = { keyword: "搜索词", asin: "搜索 ASIN" };
const MATCH_SHORT_CN = {
  loose_match: "宽泛", close_match: "紧密",
  complements: "关联", substitutes: "同类",
  exact: "精准", phrase: "词组", broad: "广泛",
};
const MATCH_ORDER = ["loose_match", "close_match", "substitutes", "complements",
  "exact", "phrase", "broad"];
const ZH_DICT = { ...TARGET_TYPE_CN, ...TARGET_TEXT_CN, ...MATCH_TYPE_CN, ...QUERY_TYPE_CN };
function targetTypeCN(v) { return TARGET_TYPE_CN[v] || v || "-"; }
function targetTextCN(v) { return TARGET_TEXT_CN[v] || v || "-"; }
function matchCN(v) { return MATCH_TYPE_CN[v] || v || "-"; }
function queryTypeCN(v) { return QUERY_TYPE_CN[v] || v || "-"; }
function zhText(v) {
  let s = String(v === null || v === undefined ? "" : v);
  // 长词优先，避免 negativeExact 先被 exact 替换
  Object.keys(ZH_DICT).sort((a, b) => b.length - a.length)
    .forEach((k) => { s = s.split(k).join(ZH_DICT[k]); });
  return s;
}

function toast(msg, err = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("err", !!err);
  el.classList.add("show");
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove("show"), 2600);
}
async function api(path, options = {}) {
  const r = await fetch(path, options);
  let d = {};
  try { d = await r.json(); } catch (_) {}
  if (!r.ok || d.ok === false) throw new Error(d.message || `HTTP ${r.status}`);
  return d;
}
const pad = (n) => String(n).padStart(2, "0");
const iso = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const fmt = (n, d = 0) => Number.isFinite(Number(n))
  ? Number(n).toLocaleString("zh-CN", { maximumFractionDigits: d }) : "-";
const money = (n) => Number.isFinite(Number(n))
  ? `¥${Number(n).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}` : "-";
const pct = (n) => n === null || n === undefined ? "-" : `${fmt(n, 1)}%`;

function startClock() {
  const tick = () => { const d = new Date(); $("clock").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`; };
  tick(); setInterval(tick, 1000);
}

async function loadCampaigns() {
  try {
    const d = await api("/api/ads/campaigns");
    const rows = d.rows || [];
    $("campaign").innerHTML = rows.map((c) =>
      `<option value="${esc(c.campaign_id)}">${esc(c.name)} · ${esc(c.campaign_id)}` +
      `${c.has_data ? ` · ${c.data_days}天数据` : " · 无本地数据"}</option>`).join("");
    if (!rows.length) {
      $("campaign").innerHTML = `<option value="">本地没有活动数据</option>`;
    } else {
      const firstWithData = rows.find((c) => c.has_data);
      if (firstWithData) $("campaign").value = firstWithData.campaign_id;
    }
  } catch (e) {
    $("campaign").innerHTML = `<option value="">活动加载失败</option>`;
    toast(e.message, true);
  }
}

async function loadCoverage() {
  try {
    const d = await api("/api/ads/query", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resource: "data_coverage" }),
    });
    const days = (d.campaign_days || []).map((x) => x.date).sort();
    const max = days.length ? days[days.length - 1] : iso(new Date());
    const start = new Date(`${max}T00:00:00`); start.setDate(start.getDate() - 6);
    $("dateFrom").value = iso(start);
    $("dateTo").value = max;
    const warnings = d.warnings || [];
    $("coverageHint").textContent = `本地数据日期：${days[0] || "-"} ~ ${days[days.length - 1] || "-"}；` +
      `搜索词覆盖 ${d.search_campaign_count || 0} 个活动。` + (warnings.length ? ` ${warnings.join("；")}` : "");
  } catch (e) {
    const today = new Date(); const start = new Date(today); start.setDate(start.getDate() - 6);
    $("dateFrom").value = iso(start); $("dateTo").value = iso(today);
    $("coverageHint").textContent = "本地数据覆盖读取失败，请手动选择日期。";
  }
}

async function analyze() {
  const cid = $("campaign").value;
  if (!cid) { toast("请选择活动", true); return; }
  const from = $("dateFrom").value, to = $("dateTo").value;
  if (!from || !to || from > to) { toast("日期范围不合法", true); return; }
  const btn = $("btnAnalyze");
  btn.disabled = true; btn.textContent = "分析中...";
  try {
    analysis = await api("/api/ads/campaign-analysis", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ campaign_id: cid, date_from: from, date_to: to,
                             target_acos: Number($("targetAcos").value) || 35 }),
    });
    $("emptyState").classList.add("ca-hidden");
    $("result").classList.remove("ca-hidden");
    $("btnExport").disabled = false;
    render();
    toast("分析完成");
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false; btn.textContent = "开始分析";
  }
}

function render() {
  if (!analysis) return;
  renderSummary();
  renderDiagnostics();
  renderStrategy();
  renderTabs();
  renderDimension(activeDim);
}

function renderSummary() {
  const s = analysis.summary || {};
  const c = analysis.campaign || {};
  const warning = analysis.coverage && (analysis.coverage.warnings || []);
  const w = $("warning");
  if (warning.length) { w.textContent = warning.join("；"); w.classList.remove("ca-hidden"); }
  else w.classList.add("ca-hidden");
  const items = [
    ["曝光", fmt(s.impressions)], ["点击", fmt(s.clicks)],
    ["花费", money(s.cost)], ["订单", fmt(s.orders)],
    ["ACOS", pct(s.acos)], ["CPC", money(s.cpc)],
  ];
  $("summary").innerHTML = items.map(([k, v]) =>
    `<div class="ca-metric"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
  $("coverageHint").textContent = `${c.name || c.campaign_id} · ${analysis.date_from} ~ ${analysis.date_to} · ` +
    `覆盖 ${analysis.coverage.days || 0} 天 · 父 ASIN ${c.parent_asin || "-"}`;
}

function renderDiagnostics() {
  const ds = analysis.diagnostics || [];
  $("diagBadge").textContent = `${ds.length} 个问题`;
  $("diagnostics").innerHTML = ds.length ? ds.map((d) => {
    const sev = d.severity === "high" ? "high" : d.severity === "medium" ? "medium" : "low";
    const ev = d.evidence ? Object.entries(d.evidence).map(([k, v]) => `${k}=${zhText(v)}`).join(" · ") : "";
    const hint = d.action_hint ? `<div class="e">建议：${esc(d.action_hint.action)}（需实时竞价/对象解析）</div>` : "";
    return `<div class="ca-diag ${sev}"><div class="t">${esc(zhText(d.title))} <span class="ca-badge ${sev}">${esc(sev)}</span></div><div class="e">${esc(ev)}</div>${hint}</div>`;
  }).join("") : `<div class="ca-badge green">没有命中规则的问题</div>`;
}

const PLACEMENT_CN = {
  placementTop: "搜索结果顶部",
  placementRestOfSearch: "搜索结果其余",
  placementProductPage: "商品详情页",
  placementSiteAmazonBusiness: "企业购",
};
function paramText(key, value) {
  if (key === "placement") return PLACEMENT_CN[value] || value;
  if (Array.isArray(value)) {
    return value.map((x) => {
      if (x && typeof x === "object") {
        const word = x.keyword_text || x.asin || "";
        return word + (x.match_type ? `（${matchCN(x.match_type)}）` : "");
      }
      return String(x);
    }).join("、");
  }
  if (value && typeof value === "object") return JSON.stringify(value);
  return value === null || value === undefined || value === "" ? "-" : String(value);
}
function renderStrategy() {
  const st = analysis.strategy || {};
  const actions = st.actions || [];
  $("strategyBadge").textContent = `${actions.length} 个动作`;
  $("strategy").innerHTML = actions.length ? actions.map((a) => {
    const params = Object.entries(a.params || {}).map(([k, v]) =>
      `<span class="ca-param"><b>${esc((a.param_explanation || {})[k] || k)}</b>：${esc(paramText(k, v))}</span>`).join("");
    return `<div class="ca-strategy">
      <div class="top"><span class="name">${esc(a.action_label || a.action)}</span><span class="ca-badge ${a.level === "L1" ? "green" : "medium"}">${esc(a.level)} ${a.requires_approval ? "需审批" : "低风险"}</span></div>
      <div class="ca-action-code">${esc(a.action)}</div>
      <div class="reason">${esc(zhText(a.explanation || a.reason || ""))}</div>
      <div class="ca-params">${params}</div>
    </div>`;
  }).join("") : `<div class="ca-badge green">当前没有可执行策略</div>`;
}

const DIMS = [
  ["trend", "日趋势"], ["groups", "广告组"], ["targets", "投放"],
  ["products", "产品 ASIN"], ["placements", "广告位"],
  ["hourly", "分时"], ["search", "搜索词"],
];
function renderTabs() {
  $("tabs").querySelectorAll("[data-dim]").forEach((b) => {
    b.classList.toggle("active", b.dataset.dim === activeDim);
    b.onclick = () => { activeDim = b.dataset.dim; renderTabs(); renderDimension(activeDim); };
  });
  $("searchSheets").classList.toggle("ca-hidden", activeDim !== "search");
  if (activeDim === "search") renderSearchSubtabs();
}

function renderSearchSubtabs() {
  $("searchSheets").querySelectorAll("[data-search-sheet]").forEach((b) => {
    b.classList.toggle("active", b.dataset.searchSheet === searchSheet);
    b.onclick = () => {
      searchSheet = b.dataset.searchSheet;
      renderSearchSubtabs();
      renderDimension("search");
    };
  });
}

function metricCells(r) {
  return `<td class="num">${fmt(r.impressions)}</td><td class="num">${fmt(r.clicks)}</td>
    <td class="num">${money(r.cost)}</td><td class="num">${fmt(r.orders)}</td>
    <td class="num">${pct(r.acos)}</td>`;
}

function matchBreakdown(r, field, formatter) {
  const mb = r.match_breakdown || {};
  const keys = Object.keys(mb);
  if (keys.length < 2) return "";
  const ordered = MATCH_ORDER.filter((k) => keys.includes(k))
    .concat(keys.filter((k) => !MATCH_ORDER.includes(k)));
  return ordered.map((k) =>
    `${MATCH_SHORT_CN[k] || matchCN(k)} ${formatter(mb[k][field])}`).join(" / ");
}

function searchMetric(r, field, formatter) {
  const total = formatter(r[field]);
  const br = matchBreakdown(r, field, formatter);
  return br ? `${total}<span class="ca-brk">（${br}）</span>` : total;
}
function table(headers, rows) {
  return `<table class="ca-table"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table>`;
}

function renderDimension(dim) {
  if (!analysis) return;
  const box = $("dimension");
  if (dim === "trend") {
    const rows = (analysis.trend || []).map((r) => {
      const max = Math.max(...(analysis.trend || []).map((x) => Number(x.cost) || 0), 1);
      const w = Math.max(2, (Number(r.cost) || 0) / max * 100);
      return `<tr><td>${esc(r.date)}</td>${metricCells(r)}<td><div class="bar"><i style="width:${w}%"></i></div></td></tr>`;
    });
    box.innerHTML = table(["日期", "曝光", "点击", "花费", "订单", "ACOS", "花费趋势"], rows);
  } else if (dim === "groups") {
    const rows = (analysis.groups || []).map((r) =>
      `<tr><td>${esc(r.name || r.group_id)}</td><td class="num">${money(r.default_bid)}</td>${metricCells(r)}</tr>`);
    box.innerHTML = table(["广告组", "默认竞价", "曝光", "点击", "花费", "订单", "ACOS"], rows);
  } else if (dim === "targets") {
    const rows = (analysis.targets || []).map((r) =>
      `<tr><td>${esc(r.group_name || r.group_id)}</td><td>${esc(targetTypeCN(r.target_type))}</td><td>${esc(targetTextCN(r.target_text || r.target_id))}</td>${metricCells(r)}</tr>`);
    box.innerHTML = table(["广告组", "类型", "投放", "曝光", "点击", "花费", "订单", "ACOS"], rows);
  } else if (dim === "products") {
    const rows = (analysis.products || []).map((r) =>
      `<tr><td class="mono">${esc(r.asin)}</td><td class="mono">${esc(r.parent_asin)}</td><td>${esc(r.title || "")}</td>${metricCells(r)}</tr>`);
    box.innerHTML = table(["ASIN", "父 ASIN", "标题", "曝光", "点击", "花费", "订单", "ACOS"], rows);
  } else if (dim === "placements") {
    const total = (analysis.placements || []).reduce((a, x) => a + (Number(x.cost) || 0), 0) || 1;
    const rows = (analysis.placements || []).map((r) =>
      `<tr><td>${esc(r.label || r.placement)}</td><td class="num">${pct((Number(r.cost) || 0) / total * 100)}</td>${metricCells(r)}</tr>`);
    box.innerHTML = table(["广告位", "花费占比", "曝光", "点击", "花费", "订单", "ACOS"], rows);
  } else if (dim === "hourly") {
    const rows = (analysis.hourly || []).map((r) =>
      `<tr><td>${pad(r.hour)}:00</td>${metricCells(r)}</tr>`);
    box.innerHTML = table(["小时", "曝光", "点击", "花费", "订单", "ACOS"], rows);
  } else if (dim === "search") {
    const all = analysis.search_terms || [];
    const filtered = searchSheet === "summary" ? all
      : all.filter((r) => String(r.query_type) === searchSheet);
    const termLabel = searchSheet === "asin" ? "搜索 ASIN" : "搜索词";
    const rows = filtered.map((r) =>
      `<tr><td>${esc(queryTypeCN(r.query_type))}</td><td>${esc(r.term)}</td><td class="mono">${esc(r.parent_asin || "-")}</td><td>${esc(r.group_name || "-")}</td><td>${esc((r.match_types || []).map(matchCN).join("、") || "-")}</td>` +
      `<td class="num">${searchMetric(r, "impressions", fmt)}</td>` +
      `<td class="num">${searchMetric(r, "clicks", fmt)}</td>` +
      `<td class="num">${searchMetric(r, "cost", money)}</td>` +
      `<td class="num">${searchMetric(r, "orders", fmt)}</td>` +
      `<td class="num">${searchMetric(r, "acos", pct)}</td></tr>`);
    box.innerHTML = table(["类型", termLabel, "父 ASIN", "广告组", "匹配方式", "曝光", "点击", "花费", "订单", "ACOS"], rows);
  } else if (dim === "competitors") {
    const rows = (analysis.competitors || []).map((r) =>
      `<tr><td>${esc(r.term)}</td><td class="num">${fmt(r.position)}</td><td class="mono">${esc(r.asin)}</td><td>${esc(r.title || "")}</td><td class="num">${money(r.price)}</td><td class="num">${fmt(r.rating, 1)}</td><td class="num">${fmt(r.reviews)}</td><td>${esc(r.delivery || "")}</td><td>${esc(r.coupon || "")}</td><td>${esc(r.stock || "")}</td><td>${r.sponsored ? "是" : "否"}</td></tr>`);
    box.innerHTML = table(["搜索词", "排名", "ASIN", "标题", "价格", "评分", "评论数", "配送", "优惠券", "库存", "广告位"], rows);
  }
}

async function exportExcel() {
  if (!analysis) { toast("请先分析活动", true); return; }
  const btn = $("btnExport");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "导出中...";
  try {
    const r = await fetch("/api/ads/campaign-analysis/export", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        campaign_id: $("campaign").value,
        date_from: $("dateFrom").value,
        date_to: $("dateTo").value,
        target_acos: Number($("targetAcos").value) || 35,
      }),
    });
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const d = await r.json(); msg = d.message || msg; } catch (_) {}
      throw new Error(msg);
    }
    const blob = await r.blob();
    const cd = r.headers.get("content-disposition") || "";
    const m = cd.match(/filename="?([^"]+)"?/);
    const filename = m ? m[1] : `campaign_analysis_${$("campaign").value}.xlsx`;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast("Excel 已导出");
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

function init() {
  startClock();
  $("btnAnalyze").onclick = analyze;
  $("btnExport").onclick = exportExcel;
  loadCampaigns();
  loadCoverage();
}
document.addEventListener("DOMContentLoaded", init);
