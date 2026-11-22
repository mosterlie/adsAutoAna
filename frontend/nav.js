/* 全站统一顶部菜单
 *
 * 所有页面只维护这一份菜单定义。
 * 通过 URL 跳转，避免各页面自己写一套 onclick 后出现“点完一个，其它菜单消失”。
 */
(function () {
  const MENU = [
    { key: "table", label: "广告活动", href: "/" },
    { key: "product", label: "产品视角", href: "/?view=product" },
    { key: "online", label: "在线产品", href: "/?view=online" },
    { key: "daily", label: "广告数据", href: "/static/daily-probe.html" },
    { key: "analysis", label: "活动分析", href: "/campaign-analysis" },
    { key: "agent", label: "智能体", href: "/agent" },
  ];

  function activeKey() {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/agent") return "agent";
    if (path === "/search-ops") return "daily";
    if (path === "/campaign-analysis") return "analysis";
    if (path === "/static/api-docs.html") return "api";

    if (path === "/static/daily-probe.html") return "daily";
    if (path === "/static/campaign-probe.html") return "table";
    if (path === "/" || path === "/index.html") {
      const view = new URLSearchParams(location.search).get("view") || "table";
      return ["table", "product", "online"].includes(view) ? view : "table";
    }
    return "";
  }

  function bindLogo() {
    document.querySelectorAll(".logo").forEach((el) => {
      if (el.dataset.fulfillmentBound) return;
      el.dataset.fulfillmentBound = "1";
      el.style.cursor = "pointer";
      el.title = "打开履约中心";
      el.addEventListener("click", () => {
        window.open("http://127.0.0.1:8000/fulfillment", "_blank", "noopener");
      });
    });
  }

  function render() {
    bindLogo();
    const bar = document.querySelector(".seg-top");
    if (!bar) return;
    const active = activeKey();
    bar.id = "viewSwitch";
    // 按内容宽度显示，不能拉满整行；否则菜单右侧会出现空的蓝色边框。
    bar.style.flex = "0 0 auto";
    bar.style.width = "auto";
    bar.style.maxWidth = "calc(100vw - 430px)";
    bar.style.minWidth = "0";
    bar.style.overflowX = "auto";
    bar.style.flexWrap = "nowrap";
    const viewKeys = new Set(["table", "product", "online"]);
    bar.innerHTML = MENU.map((item) =>
      `<button type="button" class="seg-btn ${item.key === active ? "active" : ""}" ` +
      `data-nav="${item.key}" data-href="${item.href}"` +
      (viewKeys.has(item.key) ? ` data-view="${item.key}"` : "") +
      `>${item.label}</button>`
    ).join("");
    bar.querySelectorAll("[data-nav]").forEach((btn) => {
      btn.style.flex = "0 0 auto";
      btn.style.whiteSpace = "nowrap";
      btn.addEventListener("click", () => {
        const href = btn.dataset.href || "/";
        if (href === location.pathname + location.search) return;
        location.href = href;
      });
    });

    // 接口查询不占主菜单，放到右侧以图标展示。
    let right = document.querySelector(".topbar-right");
    if (!right) {
      const topbar = document.querySelector(".topbar");
      if (topbar) {
        right = document.createElement("div");
        right.className = "topbar-right";
        topbar.appendChild(right);
      }
    }
    if (right && !right.querySelector(".nav-api-icon")) {
      const icon = document.createElement("a");
      icon.className = "nav-api-icon" + (active === "api" ? " active" : "");
      icon.href = "/static/api-docs.html";
      icon.title = "接口查询";
      icon.setAttribute("aria-label", "接口查询");
      icon.textContent = "</>";
      right.appendChild(icon);
    }

    ensureTopActions(active);
  }

  function ensureTopActions(active) {
    const right = document.querySelector(".topbar-right");
    if (!right) return;
    // 采集记录：只读查询，不占用主操作按钮位置。
    if (!right.querySelector(".nav-history-link")) {
      const link = document.createElement("a");
      link.className = "nav-history-link";
      link.href = "#";
      link.textContent = "采集记录";
      link.title = "查询每次「采集最新」的开始/结束时间和采集结果";
      link.onclick = (e) => { e.preventDefault(); openLatestHistory(); };
      right.appendChild(link);
    }
    // 配置：本页有配置弹窗直接点；没有则回首页并自动打开。
    if (!document.getElementById("btnAiConfig")) {
      const btn = document.createElement("button");
      btn.id = "navBtnConfig";
      btn.className = "btn btn-ghost";
      btn.textContent = "配置";
      btn.title = "品名解析模型 + 相关性判断底座模型 + Ollama / WorkBuddy 配置";
      btn.onclick = () => { location.href = "/?view=table&openConfig=1"; };
      right.appendChild(btn);
    }
    // 打开调试 Chrome：统一入口，省的自己找命令行；已运行则复用。
    if (!document.getElementById("navBtnOpenChrome")) {
      const btn = document.createElement("button");
      btn.id = "navBtnOpenChrome";
      btn.className = "btn btn-ghost";
      btn.textContent = "打开调试 Chrome";
      btn.title = "启动带 9222 调试端口的 Chrome（已在运行则复用，并补开赛狐/店小秘页面）";
      btn.onclick = async () => {
        btn.disabled = true;
        btn.textContent = "启动中…";
        try {
          const r = await fetch("/api/cookies/browser/open", { method: "POST" });
          const d = await r.json();
          navToast(d.message || (d.ok ? "调试 Chrome 已就绪" : "启动失败"), !d.ok);
        } catch (e) {
          navToast("启动失败：" + (e.message || e), true);
        } finally {
          btn.disabled = false;
          btn.textContent = "打开调试 Chrome";
        }
      };
      right.appendChild(btn);
    }
    // 同步登录态：所有页面统一调用同一个接口。
    if (!document.getElementById("btnSyncCookie")) {
      const btn = document.createElement("button");
      btn.id = "navBtnSync";
      btn.className = "btn btn-ghost";
      btn.textContent = "同步登录态";
      btn.title = "从调试 Chrome 同步登录态";
      btn.onclick = async () => {
        btn.disabled = true; btn.textContent = "同步中…";
        try {
          const r = await fetch("/api/cookies/sync", { method: "POST" });
          const d = await r.json();
          if (!r.ok) { navToast(d.detail || "同步失败", true); return; }
          const sites = d.sites || [];
          const brief = sites.map((s) => (s.saved
            ? `${s.label} ${s.count} 项`
            : `${s.label}失败（${s.error || "未知"}）`));
          navToast(`登录态已同步：${brief.join(" / ")}`, sites.some((s) => !s.saved));
        } catch (e) { navToast("同步失败：" + (e.message || e), true); }
        finally { btn.disabled = false; btn.textContent = "同步登录态"; }
      };
      right.appendChild(btn);
    }
    // 查看登录态：展示 data/cookies.json 当前记录的 Cookie 明细（默认打码）。
    if (!document.getElementById("navBtnCookieView")) {
      const btn = document.createElement("button");
      btn.id = "navBtnCookieView";
      btn.className = "btn btn-ghost";
      btn.textContent = "查看登录态";
      btn.title = "查看当前已保存的登录 Cookie 明细";
      btn.onclick = () => openCookieView();
      right.appendChild(btn);
    }
    // 采集最新：所有菜单均可触发同一个后台任务。
    if (!document.getElementById("btnLatest")) {
      const btn = document.createElement("button");
      btn.className = "btn btn-primary";
      btn.id = "navBtnLatest";
      btn.textContent = "采集最新";
      btn.title = "在线产品 + 上一日全量广告数据 + 上一日在投活动搜索数据";
      btn.onclick = () => navStartLatest(btn);
      right.appendChild(btn);
      checkLatestRunning(btn);
    }

    // 统一所有菜单的右上角：只保留这三个操作按钮。
    const allowed = new Set(["btnAiConfig", "navBtnConfig", "btnSyncCookie",
      "navBtnSync", "navBtnCookieView", "navBtnOpenChrome", "btnLatest", "navBtnLatest"]);
    right.querySelectorAll("button").forEach((btn) => {
      if (!allowed.has(btn.id)) { btn.remove(); return; }
      const isLatest = btn.id === "btnLatest" || btn.id === "navBtnLatest";
      btn.classList.toggle("btn-primary", isLatest);
      btn.classList.toggle("btn-ghost", !isLatest);
    });
  }

  let navLatestTimer = null;
  function navToast(message, isError = false) {
    let el = document.getElementById("navToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "navToast";
      el.className = "nav-toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.toggle("err", !!isError);
    el.classList.add("on");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("on"), 3200);
  }
  function navLatestPanel() {
    let el = document.getElementById("navLatestPanel");
    if (!el) {
      el = document.createElement("div");
      el.id = "navLatestPanel";
      el.className = "nav-latest-panel";
      document.body.appendChild(el);
    }
    return el;
  }
  async function checkLatestRunning(btn) {
    try {
      const r = await fetch("/api/daily/latest/status");
      const s = await r.json();
      if (s.running) navPollLatest(btn);
    } catch (e) { /* ignore */ }
  }
  async function navStartLatest(btn) {
    if (!btn || btn.disabled) return;
    const d = new Date(); d.setDate(d.getDate() - 1);
    const day = d.toISOString().slice(0, 10);
    if (!confirm(`将执行采集最新：\n\n1. 在线产品全量\n2. ${day} 全量广告数据（已有则跳过）\n3. ${day} 在投活动精简采集（已有则跳过）\n4. 近 30 天履约同步（妙手订单 → 履约订单/包裹，并回填缺失运单号）\n\n开始?`)) return;
    btn.disabled = true; btn.textContent = "采集中…";
    try {
      const r = await fetch("/api/daily/latest/start", { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "启动失败");
      navPollLatest(btn);
    } catch (e) {
      navToast("采集最新启动失败：" + (e.message || e), true);
      btn.disabled = false; btn.textContent = "采集最新";
    }
  }
  function navPollLatest(btn) {
    if (navLatestTimer) clearInterval(navLatestTimer);
    const panel = navLatestPanel();
    navLatestTimer = setInterval(async () => {
      let s;
      try {
        const r = await fetch("/api/daily/latest/status");
        s = await r.json();
      } catch (e) { return; }
      const lines = (s.progress || []).slice(-5).map(x =>
        `[${x.time || ""}] ${x.message || ""}${x.detail ? " · " + x.detail : ""}`);
      panel.innerHTML = `<b>采集最新 · ${s.day || ""}</b><pre>${escapeHtml(lines.join("\n") || "等待中...")}</pre>`;
      panel.classList.add("on");
      if (btn) btn.textContent = s.running
        ? `采集中…${s.stage === "ad_data" ? "广告数据" : s.stage === "search" ? ` ${s.done || 0}/${s.total || 0}` : s.stage === "fulfillment" ? "履约同步" : ""}`
        : "采集最新";
      if (!s.running) {
        clearInterval(navLatestTimer); navLatestTimer = null;
        if (btn) btn.disabled = false;
        setTimeout(() => panel.classList.remove("on"), 2500);
        navToast(s.error ? "采集最新完成，但有错误" : "采集最新完成", !!s.error);
      }
    }, 2000);
  }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, c =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  const HISTORY_STATUS = { running: "采集中", done: "完成", partial: "部分完成", failed: "失败" };
  const AD_STATUS = { success: "完成", skipped: "已存在跳过", failed: "失败" };
  const FULFILL_STATUS = { success: "完成", partial: "部分完成", failed: "失败" };
  async function openLatestHistory() {
    let modal = document.getElementById("navHistoryModal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "navHistoryModal";
      modal.className = "nav-history-modal";
      modal.innerHTML = `<div class="nav-history-card">
        <div class="nav-history-head"><b>采集记录</b>
          <span class="muted">每次点击「采集最新」的记录</span>
          <span class="sp"></span>
          <button type="button" class="nav-history-close">关闭</button>
        </div>
        <div class="nav-history-body"><div class="nav-history-empty">加载中…</div></div>
      </div>`;
      document.body.appendChild(modal);
      modal.querySelector(".nav-history-close").onclick = () => modal.classList.remove("on");
      modal.onclick = (e) => { if (e.target === modal) modal.classList.remove("on"); };
    }
    modal.classList.add("on");
    const body = modal.querySelector(".nav-history-body");
    body.innerHTML = `<div class="nav-history-empty">加载中…</div>`;
    try {
      const r = await fetch("/api/daily/latest/history?limit=50");
      const d = await r.json();
      let rows = [];
      if (r.status === 404) {
        // 旧进程尚未加载历史接口：至少展示当前正在跑的任务。
        const sr = await fetch("/api/daily/latest/status");
        const s = await sr.json();
        if (s.running || s.startedAt) {
          rows = [{
            started_at: s.startedAt || "-",
            finished_at: s.finishedAt || "-",
            day: s.day || "-",
            status: s.running ? "running" : "done",
            online_count: 0,
            ad_data_status: "",
            active_total: s.total || 0,
            search_done: s.done || 0,
            search_skipped: 0,
            fulfillment_status: s.fulfillment_status || "",
            fulfillment_orders: s.fulfillment_orders || 0,
            fulfillment_packages: s.fulfillment_packages || 0,
            fulfillment_backfilled: s.fulfillment_backfilled || 0,
            fulfillment_labeled: s.fulfillment_labeled || 0,
            errors_json: s.error || "当前任务尚未写入历史库，重启服务后历史接口生效",
          }];
        } else {
          body.innerHTML = `<div class="nav-history-empty">采集记录接口尚未加载，请重启服务后查看</div>`;
          return;
        }
      } else {
        if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
        rows = d.items || [];
      }
      if (!rows.length) {
        body.innerHTML = `<div class="nav-history-empty">还没有采集记录</div>`;
        return;
      }
      body.innerHTML = `<table class="nav-history-table">
        <thead><tr><th>开始时间</th><th>完成时间</th><th>日期</th><th>状态</th>
          <th>在线产品</th><th>广告数据</th><th>精简完成</th><th>跳过</th>
          <th>履约同步</th><th>错误</th></tr></thead>
        <tbody>${rows.map(x => `<tr>
          <td>${escapeHtml(x.started_at || "-")}</td>
          <td>${escapeHtml(x.finished_at || "-")}</td>
          <td>${escapeHtml(x.day || "-")}</td>
          <td><span class="nav-history-status ${escapeHtml(x.status || "")}">${escapeHtml(HISTORY_STATUS[x.status] || x.status || "-")}</span></td>
          <td>${Number(x.online_count || 0)}</td>
          <td>${escapeHtml(AD_STATUS[x.ad_data_status] || x.ad_data_status || "-")}</td>
          <td>${Number(x.search_done || 0)}</td>
          <td>${Number(x.search_skipped || 0)}</td>
          <td>${escapeHtml(FULFILL_STATUS[x.fulfillment_status] || x.fulfillment_status || "-")}<br>
            <span class="muted">订单 ${Number(x.fulfillment_orders || 0)} / 包裹 ${Number(x.fulfillment_packages || 0)}
              / 标识 ${Number(x.fulfillment_labeled || 0)} / 回填 ${Number(x.fulfillment_backfilled || 0)}</span></td>
          <td class="nav-history-err">${escapeHtml(x.errors_json && x.errors_json !== "[]" ? x.errors_json : "-")}</td>
        </tr>`).join("")}</tbody></table>`;
    } catch (e) {
      body.innerHTML = `<div class="nav-history-empty">读取失败：${escapeHtml(e.message || e)}</div>`;
    }
  }

  /* 查看登录态：账号管理 (赛狐 / 店小秘) + 明细 (默认明文) + 设默认/重命名/识别/删除 */
  const ckState = { sites: [], details: {}, active: {} };

  async function openCookieView() {
    let modal = document.getElementById("navCookieModal");
    if (!modal) {
      modal = document.createElement("div");
      modal.id = "navCookieModal";
      modal.className = "nav-history-modal";
      modal.innerHTML = `<div class="nav-history-card" style="width:min(1040px,96vw);">
        <div class="nav-history-head"><b>登录态账号</b>
          <span class="muted" data-meta></span>
          <span class="sp"></span>
          <label style="font-size:12px;color:#667085;cursor:pointer;user-select:none;">
            <input type="checkbox" data-reveal checked style="vertical-align:-2px;"> 显示明文
          </label>
          <button type="button" class="nav-history-close" data-refresh>刷新</button>
          <button type="button" class="nav-history-close" data-close>关闭</button>
        </div>
        <div class="nav-history-body" data-body><div class="nav-history-empty">加载中…</div></div>
      </div>`;
      document.body.appendChild(modal);
      modal.querySelector("[data-close]").onclick = () => modal.classList.remove("on");
      modal.onclick = (e) => { if (e.target === modal) modal.classList.remove("on"); };
      modal.querySelector("[data-reveal]").onchange = () => renderCookieAccounts();
      modal.querySelector("[data-refresh]").onclick = () => loadCookieAccounts();
    }
    modal.classList.add("on");
    await loadCookieAccounts();
  }

  async function loadCookieAccounts() {
    const modal = document.getElementById("navCookieModal");
    if (!modal) return;
    const body = modal.querySelector("[data-body]");
    const meta = modal.querySelector("[data-meta]");
    body.innerHTML = `<div class="nav-history-empty">加载中…</div>`;
    try {
      const r = await fetch("/api/cookies/accounts");
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      ckState.sites = d.sites || [];
      ckState.details = {};
      meta.textContent = `${ckState.sites.reduce((n, s) => n + (s.accounts || []).length, 0)} 个账号`;
      renderCookieAccounts();
    } catch (e) {
      body.innerHTML = `<div class="nav-history-empty">读取失败：${escapeHtml(e.message || e)}</div>`;
    }
  }

  async function toggleCookieDetail(site, key) {
    if (ckState.active[site] === key) {
      ckState.active[site] = "";
      renderCookieAccounts();
      return;
    }
    const cacheKey = `${site}:${key}`;
    if (!ckState.details[cacheKey]) {
      try {
        const r = await fetch(`/api/cookies/detail?site=${encodeURIComponent(site)}`
          + `&account=${encodeURIComponent(key)}&reveal=true`);
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
        ckState.details[cacheKey] = d;
      } catch (e) {
        navToast("读取明细失败：" + (e.message || e), true);
        return;
      }
    }
    ckState.active[site] = key;
    renderCookieAccounts();
  }

  async function cookieAccountAction(site, key, action) {
    let label = "";
    if (action === "rename") {
      label = prompt("账号备注：") || "";
      if (!label) return;
    }
    try {
      const r = await fetch("/api/cookies/account", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site, key, action, label }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
      if (action === "default") navToast("已设为默认账号");
      else if (action === "delete") navToast("已删除");
      else if (action === "detect") navToast(`已识别为「${d.label || ""}」`);
      else navToast("备注已更新");
      await loadCookieAccounts();
    } catch (e) {
      navToast("操作失败：" + (e.message || e), true);
    }
  }

  /* 有效期单元: 空=会话型(关闭浏览器失效); 已过期标红; 3 天内标橙 */
  function expiryCell(v) {
    const s = String(v || "").trim();
    if (!s) return '<span style="color:#94a3b8;">会话型</span>';
    const t = new Date(s.replace(/-/g, "/")).getTime();
    if (!t) return escapeHtml(s);
    const left = t - Date.now();
    if (left <= 0) {
      return `<span class="nav-history-status failed">${escapeHtml(s)}（已过期）</span>`;
    }
    if (left < 3 * 864e5) {
      return `<span style="color:#b54708;">${escapeHtml(s)}</span>`;
    }
    return escapeHtml(s);
  }

  function maskCookieValue(v) {
    const s = String(v || "");
    if (s.length <= 8) return "*".repeat(s.length);
    return s.slice(0, 4) + "*".repeat(Math.min(24, s.length - 6)) + s.slice(-2);
  }

  function renderCookieAccounts() {
    const modal = document.getElementById("navCookieModal");
    if (!modal) return;
    const body = modal.querySelector("[data-body]");
    const reveal = modal.querySelector("[data-reveal]").checked;
    if (!ckState.sites.length) {
      body.innerHTML = `<div class="nav-history-empty">暂无账号，请点右上角「打开调试 Chrome」登录后，「同步登录态」</div>`;
      return;
    }
    body.innerHTML = ckState.sites.map((s) => {
      const accounts = s.accounts || [];
      const rows = accounts.map((a) => `<tr>
        <td><b>${escapeHtml(a.label)}</b>${a.is_default
          ? ' <span class="nav-history-status running">默认</span>' : ""}</td>
        <td>${escapeHtml(a.updated_at || "—")}</td>
        <td>${expiryCell(a.expires_at)}</td>
        <td>${Number(a.count || 0)}</td>
        <td>${a.has_key
          ? '<span class="nav-history-status done">已登录</span>'
          : `<span class="nav-history-status failed">缺少 ${escapeHtml(s.key_name || "")}</span>`}</td>
        <td style="white-space:nowrap;">
          <button type="button" class="nav-history-close" data-ck="detail"
            data-site="${s.key}" data-key="${a.key}">${
            ckState.active[s.key] === a.key ? "收起" : "查看"}</button>
          ${a.is_default ? "" : `<button type="button" class="nav-history-close" data-ck="default"
            data-site="${s.key}" data-key="${a.key}">设为默认</button>`}
          <button type="button" class="nav-history-close" data-ck="rename"
            data-site="${s.key}" data-key="${a.key}">重命名</button>
          <button type="button" class="nav-history-close" data-ck="detect"
            data-site="${s.key}" data-key="${a.key}">识别账号</button>
          <button type="button" class="nav-history-close" data-ck="delete"
            data-site="${s.key}" data-key="${a.key}" style="color:#c53030;">删除</button>
        </td>
      </tr>`).join("");

      const activeKey = ckState.active[s.key];
      const detail = activeKey ? ckState.details[`${s.key}:${activeKey}`] : null;
      const detailHtml = detail ? `<div style="padding:4px 2px 10px;">
        <table class="nav-history-table">
          <thead><tr><th style="width:120px;">账号</th><th style="width:170px;">名称</th>
            <th>值</th><th style="width:60px;">长度</th></tr></thead>
          <tbody>${(detail.items || []).map((x) => `<tr>
            <td><span class="nav-history-status running">${
              escapeHtml(detail.account_label || activeKey)}</span></td>
            <td><b>${escapeHtml(x.name)}</b>${String(x.name) === String(detail.key_name || "")
              ? ' <span class="nav-history-status done">关键</span>' : ""}</td>
            <td style="white-space:normal;word-break:break-all;
              font-family:ui-monospace,Menlo,monospace;">${
              escapeHtml(reveal ? x.value : maskCookieValue(x.value))}</td>
            <td>${Number(x.length || 0)}</td>
          </tr>`).join("")}</tbody>
        </table>
        <div style="display:flex;align-items:center;gap:10px;padding:6px 2px 0;">
          <button type="button" class="nav-history-close" data-ck="copy"
            data-site="${s.key}" data-key="${activeKey}">复制整串</button>
          <span style="font-size:11px;color:#94a3b8;">来源：${escapeHtml(detail.source || "-")}</span>
        </div>
      </div>` : "";

      return `<div style="padding-top:10px;">
        <div style="display:flex;align-items:center;gap:8px;padding:8px 2px;">
          <b style="font-size:13px;">${escapeHtml(s.label)}</b>
          <span style="font-size:11.5px;color:#667085;">${accounts.length} 个账号</span>
          ${s.error ? `<span class="nav-history-status failed">${escapeHtml(s.error)}</span>` : ""}
        </div>
        ${accounts.length
          ? `<table class="nav-history-table">
               <thead><tr><th>账号</th><th style="width:140px;">同步时间</th>
                 <th style="width:140px;">有效期至</th>
                 <th style="width:76px;">Cookie 数</th><th style="width:90px;">登录态</th>
                 <th style="width:320px;">操作</th></tr></thead>
               <tbody>${rows}</tbody></table>`
          : `<div class="nav-history-empty" style="padding:16px;">
               还没有保存账号：先在调试 Chrome 登录 ${escapeHtml(s.label)}，
               再点右上角「同步登录态」</div>`}
        ${detailHtml}
      </div>`;
    }).join("") + `<div style="padding:10px 2px 4px;color:#667085;font-size:11.5px;">
        Cookie 等同登录凭证，请勿外发。不填备注同步时会自动识别登录账号名；
        识别不到可点「识别账号」或手动重命名。</div>`;

    body.querySelectorAll("[data-ck]").forEach((btn) => {
      const site = btn.dataset.site;
      const key = btn.dataset.key;
      const act = btn.dataset.ck;
      btn.onclick = () => {
        if (act === "detail") { toggleCookieDetail(site, key); return; }
        if (act === "copy") {
          const d = ckState.details[`${site}:${key}`] || {};
          const raw = (d.items || []).map((x) => `${x.name}=${x.value}`).join("; ");
          if (!raw) { navToast("没有可复制的 Cookie", true); return; }
          if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(raw)
              .then(() => navToast("Cookie 整串已复制"))
              .catch(() => navToast("复制失败，请手动选择复制", true));
          } else {
            navToast("当前浏览器不支持自动复制，请手动选择复制", true);
          }
          return;
        }
        if (act === "delete" && !confirm("确定删除该账号的 cookie？删除后需重新登录同步。")) return;
        cookieAccountAction(site, key, act);
      };
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
