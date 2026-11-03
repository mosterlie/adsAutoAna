#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探查父体级搜索词页面(?page=terms)的结构

    "D:/Python11/python.exe" explore/probe_terms_page.py B0HJ1MX7Y4
"""
import sys

sys.path.insert(0, ".")
from playwright.sync_api import sync_playwright   # noqa: E402

BASE = "http://127.0.0.1:8320"
asin = sys.argv[1] if len(sys.argv) > 1 else "B0HJ1MX7Y4"

with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, channel="chrome")
    pg = b.new_context(viewport={"width": 1680, "height": 980}).new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
    pg.goto(f"{BASE}/?page=terms&asin={asin}", wait_until="domcontentloaded", timeout=30000)
    pg.wait_for_timeout(3000)
    print("url:", pg.url)
    print("表头:", [pg.locator("#tWrap thead th").nth(i).inner_text().strip().replace("\n", "")
                    for i in range(pg.locator("#tWrap thead th").count())])
    print("行数:", pg.locator("#tWrap tbody tr").count())
    print("首行 dim-tag:", pg.locator("#tWrap tbody tr").first.locator(".dim-tag").count())
    print("面包屑:", [pg.locator("#tCrumbs .cb").nth(i).inner_text().strip()
                     for i in range(pg.locator("#tCrumbs .cb").count())])
    print("bar 含合并说明:", "合并" in pg.inner_text("#tBar"))
    print("可排序表头:", pg.locator("#tWrap thead th[data-sort]").count(),
          "adCost:", pg.locator('#tWrap thead th[data-sort="adCost"]').count())
    print("amz-link:", pg.locator("#tWrap .amz-link").count())
    print("scopes:", [x.strip() for x in pg.eval_on_selector_all("#tScopes button", "els=>els.map(e=>e.textContent.trim())")])
    print("JS 错误:", errs)
    b.close()
