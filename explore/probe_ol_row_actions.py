#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""临时自查: 在线产品行点击 / ASIN 复制 / 跳转按钮

    "D:/Python11/python.exe" explore/probe_ol_row_actions.py
"""
import sys

sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright   # noqa: E402

BASE = "http://127.0.0.1:8320"

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, channel="chrome")
    pg = b.new_context(viewport={"width": 1680, "height": 980}).new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_selector("#tabbar .tab", timeout=20000)
    pg.click('#viewSwitch .seg-btn[data-view="online"]')
    pg.wait_for_selector("#olWrap tbody tr", timeout=25000)

    idx = pg.evaluate("() => [...document.querySelectorAll('#olWrap thead th')]"
                      ".findIndex(t => t.dataset.k === 'title')")
    ia = pg.evaluate("() => [...document.querySelectorAll('#olWrap thead th')]"
                     ".findIndex(t => t.dataset.k === 'asinSku')")
    row = pg.locator("#olWrap tbody tr").first
    row.locator("td").nth(idx).click()
    pg.wait_for_timeout(500)
    print("点标题列后详情是否打开:", pg.locator("#olDetail.open").count())
    print("行内联 cursor:", row.get_attribute("style"))

    cell = row.locator("td").nth(ia)
    print("单元格 innerText:", repr(cell.inner_text()))
    print("跳转按钮数:", cell.locator("button.ol-jump").count())
    print("跳转按钮文字:", repr(cell.locator("button.ol-jump").first.inner_text()))

    pg.evaluate("() => { window.__opened = [];"
                " window.open = (u) => { window.__opened.push(u); return null; }; }")
    cell.locator("span.asin-copy").first.click()
    pg.wait_for_timeout(400)
    print("复制后 class:", cell.locator("span.asin-copy").first.get_attribute("class"))
    print("复制是否触发跳转:", pg.evaluate("window.__opened"))
    print("toast:", pg.locator(".toast").first.inner_text() if pg.locator(".toast").count() else "(无)")

    cell.locator("button.ol-jump").first.click()
    pg.wait_for_timeout(400)
    print("点跳转后 opened:", pg.evaluate("window.__opened"))

    row.locator("button[data-ops]").click()
    pg.wait_for_selector("#olDetail.open", timeout=8000)
    print("详情按钮 -> drow:", pg.locator("#olDetail .drow").count())
    pg.click("#olDetailClose")
    print("JS 错误:", errs)
    b.close()
