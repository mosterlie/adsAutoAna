/* 登录态账号管理 (单站点): 页面通过 window.ACC_SITE 指定 sellfox / dianxiaomi
 * 功能: 账号列表 / 同步 / 设默认 / 重命名 / 删除 / 明细(默认明文) / 打开调试 Chrome
 */
(function () {
  const SITE = window.ACC_SITE || "sellfox";
  const $ = (id) => document.getElementById(id);
  const state = { site: null, detail: null };

  const esc = (s) => String(s === undefined || s === null ? "" : s).replace(
    /[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  let toastTimer = null;
  function toast(msg, isErr) {
    const el = $("accToast");
    el.textContent = msg;
    el.className = "acc-toast on" + (isErr ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "acc-toast"; }, 3600);
  }

  async function api(url, opt) {
    const r = await fetch(url, opt);
    let d = {};
    try { d = await r.json(); } catch (e) { d = {}; }
    if (!r.ok) throw new Error(d.detail || ("HTTP " + r.status));
    return d;
  }

  async function load() {
    try {
      const d = await api("/api/cookies/accounts");
      state.site = (d.sites || []).find((s) => s.key === SITE) || null;
      render();
    } catch (e) {
      $("siteCard").innerHTML = `<div class="acc-empty">读取失败：${esc(e.message)}</div>`;
    }
  }

  function render() {
    const site = state.site;
    if (!site) {
      $("siteCard").innerHTML = `<div class="acc-empty">接口未返回该站点数据</div>`;
      return;
    }
    const accounts = site.accounts || [];
    const title = $("siteTitle");
    if (title) title.textContent = `${site.label}账号`;
    const rows = accounts.map((a) => `<tr>
      <td><b>${esc(a.label)}</b>${a.is_default ? ' <span class="acc-tag def">默认</span>' : ""}</td>
      <td>${esc(a.updated_at || "—")}</td>
      <td>${expiryCell(a.expires_at)}</td>
      <td>${Number(a.count || 0)}</td>
      <td>${a.has_key
        ? '<span class="acc-tag ok">已登录</span>'
        : `<span class="acc-tag err">缺少 ${esc(site.key_name)}</span>`}</td>
      <td class="acc-ops">
        <button data-act="detail" data-key="${esc(a.key)}">查看</button>
        ${a.is_default ? "" : `<button data-act="default" data-key="${esc(a.key)}">设为默认</button>`}
        <button data-act="rename" data-key="${esc(a.key)}">重命名</button>
        <button data-act="delete" data-key="${esc(a.key)}" class="danger">删除</button>
      </td>
    </tr>`).join("");

    $("siteCard").innerHTML = `<div class="acc-card">
      <div class="acc-card-h">
        <h3>${esc(site.label)}</h3>
        <span class="acc-tag">${accounts.length} 个账号</span>
        ${site.error ? `<span class="acc-tag err">${esc(site.error)}</span>` : ""}
        <span class="sp"></span>
        <span class="acc-sub">关键 cookie：${esc(site.key_name)}</span>
      </div>
      ${accounts.length
        ? `<table class="acc-table">
             <thead><tr><th>账号备注</th><th>同步时间</th><th>有效期至</th>
               <th>Cookie 数</th><th>登录态</th><th>操作</th></tr></thead>
             <tbody>${rows}</tbody></table>`
        : `<div class="acc-empty">还没有保存过账号：先在调试 Chrome 登录 ${esc(site.label)}，
             再点上方「从调试 Chrome 同步」</div>`}
    </div>`;
    document.querySelectorAll("[data-act]").forEach((btn) => {
      btn.onclick = () => handle(btn.dataset.act, btn.dataset.key);
    });
  }

  async function handle(act, key) {
    if (!state.site) return;
    if (act === "detail") { showDetail(key); return; }
    if (act === "default") {
      try {
        await api("/api/cookies/account", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ site: SITE, key, action: "default" }),
        });
        toast("已设为默认账号");
        await load();
      } catch (e) { toast("设置失败：" + e.message, true); }
      return;
    }
    if (act === "rename") {
      const cur = (state.site.accounts || []).find((a) => a.key === key);
      const label = prompt("账号备注：", (cur && cur.label) || "");
      if (label === null) return;
      try {
        await api("/api/cookies/account", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ site: SITE, key, action: "rename", label }),
        });
        toast("备注已更新");
        await load();
      } catch (e) { toast("重命名失败：" + e.message, true); }
      return;
    }
    if (act === "delete") {
      if (!confirm("确定删除该账号的 cookie？删除后需重新登录同步。")) return;
      try {
        await api("/api/cookies/account", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ site: SITE, key, action: "delete" }),
        });
        toast("已删除");
        await load();
      } catch (e) { toast("删除失败：" + e.message, true); }
    }
  }

  async function showDetail(key) {
    try {
      const d = await api(`/api/cookies/detail?site=${encodeURIComponent(SITE)}`
        + `&account=${encodeURIComponent(key)}&reveal=true`);
      state.detail = d;
      $("detailTitle").textContent = `${d.label} · ${d.account_label || d.account}`;
      $("detailMeta").textContent = `${d.count} 项 · 同步于 ${d.updated_at || "-"}`
        + ` · 有效期至 ${d.expires_at || "会话型"}`
        + ` · 来源 ${d.source || "-"}`;
      $("detailReveal").checked = true;      // 默认明文
      renderDetail();
      $("detailModal").classList.add("open");
    } catch (e) { toast("读取明细失败：" + e.message, true); }
  }

  /* 有效期: 空=会话型; 已过期红; 3 天内橙 */
  function expiryCell(v) {
    const s = String(v || "").trim();
    if (!s) return '<span style="color:#94a3b8;">会话型</span>';
    const t = new Date(s.replace(/-/g, "/")).getTime();
    if (!t) return esc(s);
    const left = t - Date.now();
    if (left <= 0) return `<span class="acc-tag err">${esc(s)}（已过期）</span>`;
    if (left < 3 * 864e5) return `<span style="color:#b54708;">${esc(s)}</span>`;
    return esc(s);
  }

  function mask(v) {
    const s = String(v || "");
    if (s.length <= 8) return "*".repeat(s.length);
    return s.slice(0, 4) + "*".repeat(Math.min(24, s.length - 6)) + s.slice(-2);
  }

  function renderDetail() {
    const d = state.detail;
    if (!d) return;
    const reveal = $("detailReveal").checked;
    const items = d.items || [];
    if (!items.length) {
      $("detailBody").innerHTML = `<div class="acc-empty">该账号没有 cookie</div>`;
      return;
    }
    /* 每条 cookie 标注所属账号, 便于对照多账号 */
    $("detailBody").innerHTML = `<table class="acc-table">
      <thead><tr><th style="width:130px;">账号</th><th style="width:180px;">名称</th>
        <th>值</th><th style="width:60px;">长度</th></tr></thead>
      <tbody>${items.map((x) => `<tr>
        <td><span class="acc-tag def">${esc(d.account_label || d.account || "")}</span></td>
        <td><b>${esc(x.name)}</b>${String(x.name) === String(d.key_name || "")
          ? ' <span class="acc-tag ok">关键</span>' : ""}</td>
        <td class="acc-val">${esc(reveal ? x.value : mask(x.value))}</td>
        <td>${Number(x.length || 0)}</td>
      </tr>`).join("")}</tbody></table>
      <div style="padding:10px 2px;color:#667085;font-size:11.5px;">
        cookie 等同登录凭证，拿到即可以该账号身份调用对应平台接口，请勿外发。
      </div>`;
  }

  /* ---------- 事件 ---------- */
  $("btnSync").onclick = async () => {
    const label = $("syncLabel").value.trim();
    const btn = $("btnSync");
    btn.disabled = true;
    btn.textContent = "同步中…";
    try {
      const d = await api("/api/cookies/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label }),
      });
      const mine = (d.sites || []).find((s) => s.key === SITE) || {};
      const msg = mine.saved
        ? `${mine.label} 同步成功：${mine.count} 项（账号「${
            (mine.account && mine.account.label) || label || "自动编号"}」）`
        : `${(mine.label || SITE)} 同步失败：${mine.error || "未知原因"}`;
      toast(msg, !mine.saved);
      $("syncHint").textContent = msg;
      await load();
    } catch (e) {
      toast("同步失败：" + e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "从调试 Chrome 同步";
    }
  };

  $("btnOpenChrome").onclick = async () => {
    const btn = $("btnOpenChrome");
    btn.disabled = true;
    btn.textContent = "启动中…";
    try {
      const d = await api("/api/cookies/browser/open", { method: "POST" });
      toast(d.message || (d.ok ? "调试 Chrome 已就绪" : "启动失败"), !d.ok);
    } catch (e) {
      toast("启动失败：" + e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "打开调试 Chrome";
    }
  };

  $("btnReload").onclick = load;
  $("detailClose").onclick = () => $("detailModal").classList.remove("open");
  $("detailModal").onclick = (e) => {
    if (e.target.id === "detailModal") $("detailModal").classList.remove("open");
  };
  $("detailReveal").onchange = renderDetail;
  $("detailCopy").onclick = () => {
    const d = state.detail;
    if (!d) return;
    const raw = (d.items || []).map((x) => `${x.name}=${x.value}`).join("; ");
    if (!raw) { toast("没有可复制的内容", true); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(raw)
        .then(() => toast("Cookie 整串已复制"))
        .catch(() => toast("复制失败，请手动选择文本复制", true));
    } else {
      toast("当前浏览器不支持自动复制，请手动选择复制", true);
    }
  };

  setInterval(() => {
    const el = $("clock");
    if (el) el.textContent = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  }, 1000);

  load();
})();
