/* 广告智能体前端 */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s === null || s === undefined ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;");

let currentRun = null;
let currentRunId = "";
let busy = false;

function toast(msg, isErr = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("err", !!isErr);
  el.classList.add("show");
  clearTimeout(el._timer);
  el._timer = setTimeout(() => el.classList.remove("show"), 2600);
}

async function api(path, options = {}) {
  const r = await fetch(path, options);
  let data = {};
  try { data = await r.json(); } catch (_) { /* ignore */ }
  if (!r.ok && data.ok !== false) {
    throw new Error(data.message || `HTTP ${r.status}`);
  }
  return data;
}

async function post(path, body) {
  return api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}

const pad = (n) => String(n).padStart(2, "0");
function isoDate(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
function initDates() {
  const today = new Date();
  const start = new Date(today);
  start.setDate(start.getDate() - 7);
  $("dateFrom").value = isoDate(start);
  $("dateTo").value = isoDate(today);
}

function fmt(n, digits = 0) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "-";
  return v.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}
function money(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "-";
  return `¥${v.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
}
function statusInfo(status) {
  const map = {
    observing: ["观测中", "blue"], analyzing: ["分析中", "blue"],
    planning: ["生成策略", "blue"], awaiting_approval: ["待批准", "orange"],
    approved: ["已批准", "blue"], preview_ready: ["已预览", "purple"],
    done: ["已完成", "green"], partial_execution: ["部分执行", "red"],
    data_incomplete: ["数据不足", "red"],
  };
  return map[status] || [status || "未知", "gray"];
}
function badge(status) {
  const [label, cls] = statusInfo(status);
  return `<span class="ag-badge ${cls}">${esc(label)}</span>`;
}
function levelBadge(level) {
  const cls = level === "L1" ? "green" : level === "L2" ? "orange" : "red";
  return `<span class="ag-badge ${cls}">${esc(level || "-")}</span>`;
}
function jsonBlock(obj) {
  return `<div class="ag-json">${esc(JSON.stringify(obj || {}, null, 2))}</div>`;
}
function setBusy(on, label = "") {
  busy = on;
  $("btnRun").disabled = on;
  $("runLoading").classList.toggle("ag-hidden", !on);
  if (label) toast(label);
}

async function loadPortfolios() {
  const sel = $("scopePortfolio");
  try {
    const d = await api("/api/adcreator/portfolios?shop_id=711811");
    const rows = d.rows || [];
    sel.innerHTML = `<option value="">全部组合</option>` +
      rows.map((p) => `<option value="${esc(p.portfolio_id)}">${esc(p.portfolio_name || p.portfolio_id)}</option>`).join("");
    if (!rows.length) sel.innerHTML = `<option value="">没有组合数据</option>`;
  } catch (e) {
    sel.innerHTML = `<option value="">组合加载失败，可直接用 campaignId</option>`;
  }
}

function onScopeChange() {
  const type = $("scopeType").value;
  const isPortfolio = type === "portfolio";
  $("scopePortfolio").classList.toggle("ag-hidden", !isPortfolio);
  $("scopeValue").classList.toggle("ag-hidden", isPortfolio || type === "all");
  $("scopeValueWrap").classList.toggle("ag-hidden", type === "all");
  const labels = { portfolio: "选择组合", campaign: "活动名称 / campaignId", parent_asin: "父 ASIN" };
  $("scopeLabel").textContent = labels[type] || "范围";
  const ph = { campaign: "如 1113TEST_Auto_ToolBucket_20261113 或 38504980800018", parent_asin: "如 B0HLKZ1DLM" };
  $("scopeValue").placeholder = ph[type] || "";
}

function buildScope() {
  const type = $("scopeType").value;
  if (type === "portfolio") {
    const v = $("scopePortfolio").value;
    return v ? { portfolio: v } : {};
  }
  if (type === "all") return {};
  const v = $("scopeValue").value.trim();
  if (!v) throw new Error(type === "campaign" ? "请输入活动名或 campaignId" : "请输入父 ASIN");
  if (type === "parent_asin") return { parent_asin: v };
  return /^\d+$/.test(v) ? { campaign_id: v } : { campaign: v };
}

function scopeText(scope) {
  if (!scope) return "全部范围";
  if (scope.portfolio) return `组合：${scope.portfolio}`;
  if (scope.campaign_id) return `活动：${scope.campaign_id}`;
  if (scope.campaign) return `活动：${scope.campaign}`;
  if (scope.parent_asin) return `父 ASIN：${scope.parent_asin}`;
  return "全部范围";
}

async function loadRuns() {
  try {
    const d = await api("/api/agent/runs?limit=30");
    const runs = d.runs || [];
    const box = $("runList");
    if (!runs.length) {
      box.innerHTML = `<div class="ag-note" style="padding:9px">暂无运行记录</div>`;
      return;
    }
    box.innerHTML = runs.map((r) => `
      <div class="ag-run-item ${r.run_id === currentRunId ? "active" : ""}" data-run="${esc(r.run_id)}">
        <div class="ag-run-name">${esc(r.objective || "广告分析")}</div>
        <div class="ag-run-meta"><span>${esc((r.created_at || "").replace("T", " ").slice(0, 16))}</span>${badge(r.status)}</div>
      </div>`).join("");
    box.querySelectorAll("[data-run]").forEach((el) => {
      el.onclick = () => loadRun(el.dataset.run);
    });
  } catch (e) {
    $("runList").innerHTML = `<div class="ag-alert red" style="margin:8px">${esc(e.message)}</div>`;
  }
}

async function loadRun(runId) {
  try {
    const d = await api(`/api/agent/runs/${encodeURIComponent(runId)}`);
    if (!d.run) throw new Error(d.message || "run 不存在");
    currentRun = d.run;
    currentRunId = runId;
    renderRun(currentRun);
    loadRuns();
  } catch (e) {
    toast(e.message, true);
  }
}

async function createRun() {
  if (busy) return;
  let scope;
  try { scope = buildScope(); } catch (e) { toast(e.message, true); return; }
  const dateFrom = $("dateFrom").value;
  const dateTo = $("dateTo").value;
  if (!dateFrom || !dateTo) { toast("请选择日期范围", true); return; }
  if (dateFrom > dateTo) { toast("开始日期不能晚于结束日期", true); return; }
  setBusy(true, "正在分析，请稍候...");
  try {
    const d = await post("/api/agent/runs", {
      scope, date_from: dateFrom, date_to: dateTo,
      objective: $("objective").value.trim() || "降低无效花费，保留高意图流量",
    });
    if (!d.ok || !d.run) throw new Error(d.message || "分析失败");
    currentRun = d.run;
    currentRunId = d.run.run_id;
    renderRun(currentRun);
    await loadRuns();
    toast("分析完成");
  } catch (e) {
    toast(e.message, true);
  } finally {
    setBusy(false);
  }
}

function renderRun(run) {
  $("emptyState").classList.add("ag-hidden");
  $("runView").classList.remove("ag-hidden");
  const status = run.status;
  const [label, cls] = statusInfo(status);
  $("runTitle").textContent = run.objective || "广告分析";
  $("runMeta").textContent = `${scopeText(run.scope)} · ${run.date_from || "-"} ~ ${run.date_to || "-"}`;
  $("runId").textContent = run.run_id || "-";
  $("runStatus").className = `ag-badge ${cls}`;
  $("runStatus").textContent = label;

  const canApprove = status === "awaiting_approval";
  const canPreview = status === "approved" || status === "preview_ready";
  const canExecute = status === "preview_ready";
  $("btnApprove").disabled = !canApprove;
  $("btnPreview").disabled = !canPreview;
  $("btnExecute").disabled = !canExecute;
  $("btnPreview").classList.toggle("primary", canPreview);
  $("btnExecute").classList.toggle("danger", canExecute);
  $("btnExecute").classList.toggle("ag-hidden", !canExecute && status !== "done" && status !== "partial_execution");

  const warnings = (run.coverage || {}).warnings || [];
  $("coverageBox").innerHTML = warnings.length
    ? `<div class="ag-alert">${warnings.map(esc).join("<br>")}</div>`
    : `<div class="ag-alert blue">数据覆盖检查通过（仍建议核对日期范围和活动范围）。</div>`;

  renderMetrics(run.diagnosis || {});
  renderDiagnosis(run.diagnosis || {});
  renderPlan(run.plan || {});
  renderPreviews(run.plan || {});
  renderExecutions(run.plan || {});
}

function renderMetrics(diag) {
  const t = diag.totals || {};
  const items = [
    ["活动", fmt(t.campaigns)],
    ["点击", fmt(t.clicks)],
    ["花费", money(t.cost)],
    ["订单", fmt(t.orders)],
    ["ACOS", t.acos === null || t.acos === undefined ? "-" : `${fmt(t.acos, 1)}%`],
  ];
  $("metrics").innerHTML = items.map(([k, v]) => `
    <div class="ag-metric"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");
}

function renderDiagnosis(diag) {
  const campaigns = diag.campaigns_zero_order || [];
  const terms = diag.search_terms_zero_order || [];
  $("diagBadge").textContent = `${campaigns.length + terms.length} 个问题`;
  const parts = [];
  if (diag.summary) parts.push(`<p class="ag-summary">${esc(diag.summary)}</p>`);
  if (!campaigns.length && !terms.length) {
    parts.push(`<div class="ag-alert blue">当前范围内没有命中“高点击零单”的规则。</div>`);
  }
  if (campaigns.length) {
    parts.push(`<div style="font-size:12px;font-weight:600;margin:4px 0 7px">活动级：点击 ≥10 且 0 单</div>`);
    parts.push(`<table class="ag-table"><thead><tr><th>活动</th><th>父 ASIN</th><th class="num">点击</th><th class="num">花费</th></tr></thead><tbody>` +
      campaigns.map((x) => `<tr><td>${esc(x.campaign_name || x.campaign_id)}</td><td class="mono">${esc(x.parent_asin || "-")}</td><td class="num">${fmt(x.clicks)}</td><td class="num">${money(x.cost)}</td></tr>`).join("") +
      `</tbody></table>`);
  }
  if (terms.length) {
    parts.push(`<div style="font-size:12px;font-weight:600;margin:12px 0 7px">搜索词：点击 >10 且 0 单</div>`);
    parts.push(`<table class="ag-table"><thead><tr><th>搜索词</th><th>父 ASIN</th><th class="num">点击</th><th class="num">花费</th></tr></thead><tbody>` +
      terms.map((x) => `<tr><td>${esc(x.query)}</td><td class="mono">${esc(x.parent_asin || "-")}</td><td class="num">${fmt(x.clicks)}</td><td class="num">${money(x.cost)}</td></tr>`).join("") +
      `</tbody></table>`);
  }
  $("diagnosis").innerHTML = parts.join("");
}

function renderPlan(plan) {
  const actions = plan.actions || [];
  $("planBadge").textContent = `${actions.length} 个动作`;
  const parts = [];
  if (plan.summary) parts.push(`<p class="ag-summary">${esc(plan.summary)}</p>`);
  if (!actions.length) {
    parts.push(`<div class="ag-alert blue">当前没有生成策略动作。</div>`);
  } else {
    parts.push(`<table class="ag-table"><thead><tr><th>#</th><th>动作</th><th>等级</th><th>目标 / 参数</th><th>理由</th></tr></thead><tbody>`);
    actions.forEach((a) => {
      parts.push(`<tr>
        <td class="mono">${esc(a.id || "-")}</td>
        <td><b>${esc(a.action)}</b><div class="ag-note">${a.requires_approval ? "需审批" : "低风险"}</div></td>
        <td>${levelBadge(a.level)}</td>
        <td class="ag-json-cell">${jsonBlock({ selector: a.selector, params: a.params })}</td>
        <td class="ag-reason-cell">${esc(a.reason || "")}</td>
      </tr>`);
    });
    parts.push(`</tbody></table>`);
  }
  $("plan").innerHTML = parts.join("");
}

function renderPreviews(plan) {
  const previews = plan.previews || [];
  const panel = $("previewPanel");
  if (!previews.length) { panel.classList.add("ag-hidden"); return; }
  panel.classList.remove("ag-hidden");
  $("previewBadge").textContent = `${previews.length} 个`;
  $("previews").innerHTML = previews.map((item) => {
    const p = item.preview || {};
    if (!item.ok) {
      return `<div class="ag-preview"><div class="ag-preview-head"><span>${esc(item.plan_action_id || "-")}</span><span class="ag-badge red">预览失败</span></div><div class="ag-alert red" style="margin-top:8px">${esc(item.error || "未知错误")}</div></div>`;
    }
    return `<div class="ag-preview">
      <div class="ag-preview-head"><span>${esc(item.plan_action_id || "-")} · ${esc(p.action || "")}</span><span class="ag-badge ${p.ok ? "green" : "red"}">${p.ok ? "预览通过" : "预览失败"}</span></div>
      <div class="ag-kv">
        <div class="box"><b>修改前</b>${esc(JSON.stringify(p.before || {}, null, 2))}</div>
        <div class="box"><b>修改后</b>${esc(JSON.stringify(p.after || {}, null, 2))}</div>
      </div>
      <div class="ag-note">确认令牌：${esc(String(p.confirm_token || "").slice(0, 18))}${p.confirm_token ? "..." : ""}</div>
    </div>`;
  }).join("");
}

function renderExecutions(plan) {
  const results = plan.execution || [];
  const panel = $("executionPanel");
  if (!results.length) { panel.classList.add("ag-hidden"); return; }
  panel.classList.remove("ag-hidden");
  const okCount = results.filter((x) => x.ok).length;
  $("executionBadge").textContent = `${okCount}/${results.length} 成功`;
  $("executions").innerHTML = `<table class="ag-table"><thead><tr><th>动作</th><th>结果</th><th>详情</th></tr></thead><tbody>` +
    results.map((x) => {
      const r = x.result || {};
      return `<tr><td class="mono">${esc(x.plan_action_id || "-")}</td>
        <td><span class="ag-badge ${x.ok ? "green" : "red"}">${x.ok ? "成功" : "失败"}</span></td>
        <td>${x.error ? esc(x.error) : `status=${esc(r.status || "-")} · verified=${esc(r.verified)} · ${esc(r.request_id || "")}`}</td></tr>`;
    }).join("") + `</tbody></table>`;
}

async function runAgentAction(kind) {
  if (!currentRunId || busy) return;
  if (kind === "execute") {
    if (!confirm("确认执行该策略计划？这会调用真实赛狐写接口。")) return;
  }
  if (kind === "preview") {
    if (!confirm("生成预览会读取实时数据并创建确认令牌，不会写广告。继续？")) return;
  }
  const paths = {
    approve: `/api/agent/runs/${encodeURIComponent(currentRunId)}/approve`,
    preview: `/api/agent/runs/${encodeURIComponent(currentRunId)}/preview`,
    execute: `/api/agent/runs/${encodeURIComponent(currentRunId)}/execute`,
  };
  setBusy(true, kind === "execute" ? "正在执行..." : "处理中...");
  try {
    const body = kind === "execute" ? { confirm: true } : {};
    const d = await post(paths[kind], body);
    if (!d.ok) throw new Error(d.message || "操作失败");
    currentRun = d.run;
    renderRun(currentRun);
    await loadRuns();
    toast(kind === "execute" ? "执行完成，已读回结果" : "操作完成");
  } catch (e) {
    toast(e.message, true);
  } finally {
    setBusy(false);
  }
}

async function loadTools() {
  try {
    const d = await api("/api/ads/tools");
    const tools = d.tools || [];
    const actions = d.actions || [];
    $("toolsBadge").textContent = `${tools.length} 个工具 · ${actions.length} 个动作`;
    $("toolGrid").innerHTML = tools.map((t) => `
      <div class="ag-tool"><b>${esc(t.name)}</b><code>${esc(t.endpoint || "")}</code><div class="n">${esc(t.description || "")}</div></div>`).join("");
    $("actionRows").innerHTML = actions.map((a) => `
      <tr><td><b>${esc(a.name)}</b></td><td>${levelBadge(a.level)}</td><td>${esc(a.desc || "")}</td>
      <td class="mono">${esc(Object.keys((a.selector_schema || {}).properties || {}).join(", "))}</td></tr>`).join("");
    $("liveDot").classList.remove("bad");
    $("liveText").textContent = "服务正常";
    $("serviceStatus").textContent = "Agent API 正常";
  } catch (e) {
    $("toolsBadge").textContent = "加载失败";
    $("actionRows").innerHTML = `<tr><td colspan="4" class="muted">${esc(e.message)}</td></tr>`;
    $("liveText").textContent = "服务异常";
    $("serviceStatus").textContent = "服务异常";
  }
}

function startClock() {
  const tick = () => {
    const d = new Date();
    $("clock").textContent = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  };
  tick();
  setInterval(tick, 1000);
}

function init() {
  initDates();
  startClock();
  onScopeChange();
  $("scopeType").onchange = onScopeChange;
  $("btnRun").onclick = createRun;
  $("btnRefreshRuns").onclick = loadRuns;
  $("btnReload").onclick = () => currentRunId && loadRun(currentRunId);
  $("btnApprove").onclick = () => runAgentAction("approve");
  $("btnPreview").onclick = () => runAgentAction("preview");
  $("btnExecute").onclick = () => runAgentAction("execute");
  loadPortfolios();
  loadRuns();
  loadTools();
}

document.addEventListener("DOMContentLoaded", init);
