# -*- coding: utf-8 -*-
"""启动带调试端口的浏览器，打开赛狐广告创建页，并持续记录 XHR/Fetch 接口

- 调试端口: 9333（CDP: http://127.0.0.1:9333/json）
- 接口日志: /tmp/sellfox_net.jsonl（每条一行 JSON：时间/方法/URL/请求体/状态码）
- 浏览器常驻，手动关闭窗口或 kill 本进程即退出
"""
import json
import time

from playwright.sync_api import sync_playwright

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp%26ts%3D1794030255179%26portfolioId%3D-1")
PROFILE = "/Users/gx/Desktop/mypro/adsAutoAna/data/chrome-debug-profile"
PORT = 9333
LOG_PATH = "/tmp/sellfox_net.jsonl"

log = open(LOG_PATH, "a", encoding="utf-8")


def ts():
    return time.strftime("%H:%M:%S")


def on_request(req):
    try:
        if req.resource_type not in ("xhr", "fetch"):
            return
        rec = {"ts": ts(), "method": req.method, "url": req.url,
               "type": req.resource_type, "post": req.post_data}
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.flush()
    except Exception:  # noqa: BLE001
        pass


def on_response(resp):
    try:
        req = resp.request
        if req.resource_type not in ("xhr", "fetch"):
            return
        rec = {"ts": ts(), "kind": "resp", "status": resp.status,
               "url": resp.url, "method": req.method}
        log.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log.flush()
    except Exception:  # noqa: BLE001
        pass


with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        PROFILE,
        headless=False,
        viewport=None,
        args=[
            f"--remote-debugging-port={PORT}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--start-maximized",
        ],
    )
    ctx.on("request", on_request)
    ctx.on("response", on_response)
    for extra in list(ctx.pages[1:]):
        try:
            extra.close()
        except Exception:
            pass
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(URL, wait_until="domcontentloaded", timeout=90000)
    print(f"READY cdp=http://127.0.0.1:{PORT}/json url={page.url}", flush=True)
    try:
        page.wait_for_timeout(10 ** 9)
    except Exception:  # noqa: BLE001
        pass
