# -*- coding: utf-8 -*-
"""定位承载赛狐广告 UI 的 iframe: 在每个 frame 内查找 9 个页签与表格结构"""
import asyncio
import sys

from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
TABS = ["广告组合", "广告活动", "广告组", "广告产品", "投放", "搜索词", "否定投放", "广告位", "广告日志"]

PROBE = r"""
(tabs) => {
    const out = {};
    const visible = el => el && el.offsetParent !== null && el.getBoundingClientRect().width > 0;
    // 页签
    out.tabs = tabs.filter(t => [...document.querySelectorAll('*')].some(
        e => e.children.length === 0 && (e.textContent||'').trim() === t && visible(e)));
    // 表格
    const heads = [...document.querySelectorAll('th, .vxe-header--column')].filter(visible);
    out.header_count = heads.length;
    out.headers = heads.slice(0, 40).map(h => (h.innerText||'').replace(/\s+/g,' ').trim()).filter(Boolean);
    const rows = [...document.querySelectorAll('tr, .vxe-body--row')].filter(visible);
    out.row_count = rows.length;
    // 输入/筛选
    out.inputs = [...document.querySelectorAll('input')].filter(visible)
        .slice(0, 25).map(i => ({ph: i.placeholder||'', val: (i.value||'').slice(0,30)}));
    // 按钮
    out.buttons = [...document.querySelectorAll('button, .el-button, [class*=btn]')].filter(visible)
        .map(b => (b.innerText||'').replace(/\s+/g,' ').trim()).filter(t => t && t.length < 12).slice(0, 40);
    out.body_len = (document.body.innerHTML||'').length;
    return out;
}
"""


async def connect(pw):
    try:
        return await pw.chromium.connect_over_cdp(CDP, timeout=20000)
    except Exception as e:
        if "setDownloadBehavior" in str(e) or "context management" in str(e):
            sys.path.insert(0, "/Users/gx/Desktop/mypro/browser_toolkit")
            from core.cdp_proxy import get_proxy
            return await pw.chromium.connect_over_cdp(get_proxy(CDP).ws_endpoint, timeout=20000)
        raise


async def main():
    pw = await async_playwright().start()
    browser = await connect(pw)
    ctx = browser.contexts[0]
    page = next((p for p in ctx.pages if "sellfox" in p.url and "cpc" in p.url), None)
    if page is None:
        print("no sellfox page")
        return
    for i, f in enumerate(page.frames):
        print(f"\n===== frame[{i}] {f.url[:130]} =====")
        try:
            info = await f.evaluate(PROBE, TABS)
            print("  body_len:", info["body_len"], "| tabs:", info["tabs"])
            print("  headers(%d):" % info["header_count"], info["headers"][:30])
            print("  rows:", info["row_count"])
            print("  inputs:", info["inputs"][:12])
            print("  buttons:", info["buttons"][:25])
        except Exception as e:
            print("  probe fail:", str(e)[:120])
    await pw.stop()


asyncio.run(main())
