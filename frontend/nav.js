/* 全站统一顶部菜单
 *
 * 所有页面只维护这一份菜单定义。
 * 通过 URL 跳转，避免各页面自己写一套 onclick 后出现“点完一个，其它菜单消失”。
 */
(function () {
  const MENU = [
    { key: "table", label: "数据表格", href: "/" },
    { key: "product", label: "产品视角", href: "/?view=product" },
    { key: "online", label: "在线产品", href: "/?view=online" },
    { key: "adcreator", label: "广告管理", href: "/?view=adcreator" },
    { key: "bulk", label: "批量建广告", href: "/?view=bulk" },
    { key: "hourly", label: "分时分析", href: "/static/hourly.html" },
    { key: "probe", label: "活动测数", href: "/static/campaign-probe.html" },
    { key: "api", label: "接口查询", href: "/static/api-docs.html" },
    { key: "daily", label: "广告数据", href: "/static/daily-probe.html" },
    { key: "analysis", label: "活动分析", href: "/campaign-analysis" },
    { key: "agent", label: "智能体", href: "/agent" },
  ];

  function activeKey() {
    const path = location.pathname.replace(/\/+$/, "") || "/";
    if (path === "/agent") return "agent";
    if (path === "/search-ops") return "daily";
    if (path === "/campaign-analysis") return "analysis";
    if (path === "/static/hourly.html") return "hourly";
    if (path === "/static/api-docs.html") return "api";
    if (path === "/static/daily-probe.html") return "daily";
    if (path === "/static/campaign-probe.html") return "probe";
    if (path === "/" || path === "/index.html") {
      const view = new URLSearchParams(location.search).get("view") || "table";
      return ["table", "product", "online", "adcreator", "bulk"].includes(view)
        ? view : "table";
    }
    return "";
  }

  function render() {
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
    const viewKeys = new Set(["table", "product", "online", "adcreator", "bulk"]);
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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", render);
  } else {
    render();
  }
})();
