# -*- coding: utf-8 -*-
"""连通性体检: 直连 / 系统代理 / 调试 Chrome(CDP) 三条路各测一遍

用于排查「预抓取连续失败被熔断」是网络原因还是亚马逊反爬。
用法: D:\\Python11\\python.exe explore\\probe_net_check.py
"""
import socket
import sys

sys.path.insert(0, ".")

import config            # noqa: E402


def t_direct() -> None:
    for host in ("www.amazon.co.jp", "www.google.com"):
        try:
            with socket.create_connection((host, 443), timeout=6):
                print(f"[直连 ] {host:22s} OK")
        except OSError as e:
            print(f"[直连 ] {host:22s} 失败: {type(e).__name__}")


def t_proxy() -> None:
    pm = config.proxy_map()
    print(f"[代理 ] 配置 = {pm.get('https') if pm else '(无)'}")
    if not pm:
        return
    import requests
    for url in ("http://www.gstatic.com/generate_204",
                "https://www.google.com/generate_204",
                "https://www.amazon.co.jp/"):
        try:
            r = requests.get(url, proxies=pm, timeout=12,
                             headers={"User-Agent": config.USER_AGENT})
            print(f"[代理 ] {url[:46]:48s} HTTP {r.status_code}")
        except Exception as e:      # noqa: BLE001
            print(f"[代理 ] {url[:46]:48s} 失败: {type(e).__name__}")


def t_browser() -> None:
    """真正的抓取路径: 连调试 Chrome 打开亚马逊"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        print(f"[浏览器] playwright 不可用: {e}")
        return
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp(config.CDP_URL, timeout=15000)
            page = b.contexts[0].new_page()
            try:
                r = page.goto("https://www.amazon.co.jp/", wait_until="domcontentloaded",
                              timeout=30000)
                print(f"[浏览器] amazon.co.jp HTTP {r.status if r else '?'} "
                      f"title={(page.title() or '')[:40]!r}")
            finally:
                page.close()
    except Exception as e:      # noqa: BLE001
        print(f"[浏览器] 失败: {type(e).__name__}: {str(e)[:120]}")


if __name__ == "__main__":
    print(f"CDP = {config.CDP_URL}")
    t_direct()
    t_proxy()
    t_browser()
