# -*- coding: utf-8 -*-
"""验证「搜索词详情页 - 整体相似度」前端渲染 (Playwright)

用法:
    python verify_sku_sim_ui.py            # 默认验证下面的样例页
    python verify_sku_sim_ui.py <url>

做三件事:
  1. 打开页面 → 等异步接口返回
  2. 提取 #amzCardsMeta / #termDeepBody 文本 + 每张卡片的 .sim-badge 徽章
  3. 截图到临时目录

优先复用本机已开的 Chrome 调试端口(127.0.0.1:9222, 与抓取同一套),
连不上再自己拉一个 Chromium。
"""
import json
import os
import sys
import tempfile

from playwright.sync_api import sync_playwright

DEFAULT_URL = ("http://127.0.0.1:8320/?page=term&asin=B0HHR6KGCV"
               "&term=%E3%82%B7%E3%83%95%E3%83%88%E3%83%8E%E3%83%96"
               "&mt=close_match")
CDP = "http://127.0.0.1:9222"
WAIT_MS = 22000
# 本机浏览器(Playwright 自带内核未下载时用系统 Chrome/Edge 起)
BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]


def main() -> int:
    # Windows 控制台默认 GBK, 直接 print 日文/符号会 UnicodeEncodeError
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                       # noqa: BLE001
            pass
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    shot = os.path.join(tempfile.gettempdir(), "term_page.png")
    out = os.path.join(tempfile.gettempdir(), "term_page_check.txt")
    lines = []

    with sync_playwright() as p:
        try:
            b = p.chromium.connect_over_cdp(CDP)
            ctx = b.contexts[0] if b.contexts else b.new_context()
            reuse = True
            lines.append("MODE=cdp(9222)")
        except Exception as e:                  # noqa: BLE001
            lines.append(f"CDP failed({e}) -> 尝试系统浏览器")
            b, reuse = None, False
            for exe in BROWSERS:
                if not os.path.exists(exe):
                    continue
                try:
                    b = p.chromium.launch(executable_path=exe)
                    lines.append(f"MODE=launch({exe})")
                    break
                except Exception as e2:         # noqa: BLE001
                    lines.append(f"launch {exe} failed: {e2}")
            if b is None:
                b = p.chromium.launch()
                lines.append("MODE=launch(自带内核)")
            ctx = b.new_context()
        pg = ctx.new_page()
        if not reuse:                # 复用用户已开的浏览器时不动它的窗口大小
            pg.set_viewport_size({"width": 1680, "height": 1000})
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(WAIT_MS)
            lines.append("META=" + (pg.inner_text("#amzCardsMeta") or ""))
            lines.append("DEEP=" + (pg.inner_text("#termDeepBody") or "").replace("\n", " | "))
            lines.append("REL=" + (pg.inner_text("#termRelBody") or "").replace("\n", " | ")[:300])
            lines.append("CARDS=" + str(pg.eval_on_selector_all(".amz-card2", "e => e.length")))
            lines.append("BADGES=" + json.dumps(
                pg.eval_on_selector_all(".sim-badge", "e => e.map(x => x.textContent.trim())"),
                ensure_ascii=False))
            lines.append("TIPS=" + json.dumps(
                pg.eval_on_selector_all(".sim-badge", "e => e.map(x => x.title)"),
                ensure_ascii=False)[:700])
            pg.screenshot(path=shot)
            lines.append("SHOT=" + shot)
            shot2 = os.path.join(tempfile.gettempdir(), "term_page_cards.png")
            pg.eval_on_selector("#amzCards", "e => e.scrollIntoView({block:'start'})")
            pg.wait_for_timeout(2500)
            pg.screenshot(path=shot2)
            lines.append("SHOT2=" + shot2)
        except Exception as e:                  # noqa: BLE001
            lines.append("ERR=" + repr(e))
        finally:
            pg.close()                          # 只关本页, 不动用户已开的浏览器
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"wrote {out}")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
