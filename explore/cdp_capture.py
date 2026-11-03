# -*- coding: utf-8 -*-
"""直连 Chrome(9222) 抓取赛狐「创建广告活动」页渲染结果: 截图 + DOM + 表单结构。

Chrome 156 的 /json/* 已 404, 故用 DevToolsActivePort 里的 browser ws 直连。
"""
import base64
import json
import os
import time

import websocket

URL = ("https://www.sellfox.com/amzup-web-main/amzup-web-cpc-vue3"
       "?amzup-web-cpc-vue3=%2Fcpc-vue3%2Fweb%2Fcpc-vue3%2Fad-create%2Findex.html"
       "%3FshopIdList%3D711811%26adType%3Dsp%26ts%3D1793686640990")
OUT = "/Users/gx/Desktop/mypro/adsAutoAna/explore/sellfox_ref"

port, path = open(os.path.expanduser(
    "~/Library/Application Support/Google/Chrome/DevToolsActivePort")).read().split()
ws = websocket.create_connection(f"ws://127.0.0.1:{port}{path}", timeout=30, max_size=200 * 1024 * 1024)
_id = 0


def call(method, params=None, session=None, timeout=60):
    global _id
    _id += 1
    msg = {"id": _id, "method": method, "params": params or {}}
    if session:
        msg["sessionId"] = session
    ws.send(json.dumps(msg))
    end = time.time() + timeout
    while time.time() < end:
        ws.settimeout(max(1, end - time.time()))
        data = json.loads(ws.recv())
        if data.get("id") == _id:
            if "error" in data:
                raise RuntimeError(f"{method}: {data['error']}")
            return data["result"]
    raise TimeoutError(method)


tid = call("Target.createTarget", {"url": URL, "background": False})["targetId"]
sid = call("Target.attachToTarget", {"targetId": tid, "flatten": True})["sessionId"]
call("Page.enable", session=sid)
call("Runtime.enable", session=sid)


def ev(js, await_promise=False):
    r = call("Runtime.evaluate", {"expression": js, "returnByValue": True,
                                  "awaitPromise": await_promise}, session=sid)
    return r.get("result", {}).get("value")


# 等待表单渲染（出现「广告活动名称」或登录跳转）
for i in range(40):
    time.sleep(1.5)
    url = ev("location.href")
    txt = ev("document.body ? document.body.innerText.slice(0,200) : ''") or ""
    if "广告活动名称" in txt or "创建广告活动" in txt:
        break
    if "login" in (url or ""):
        print("!! 跳转到登录页:", url)
        break
print("URL:", ev("location.href"))
print("片段:", (ev("document.body.innerText.slice(0,120)") or "").replace("\n", " | "))

os.makedirs(OUT, exist_ok=True)
html = ev("document.documentElement.outerHTML") or ""
open(f"{OUT}/adcreate.html", "w", encoding="utf-8").write(html)
print("HTML 长度:", len(html))

# 全页截图
metrics = call("Page.getLayoutMetrics", session=sid)["cssContentSize"]
shot = call("Page.captureScreenshot",
            {"format": "png", "captureBeyondViewport": True,
             "clip": {"x": 0, "y": 0, "width": metrics["width"],
                      "height": min(metrics["height"], 4000), "scale": 1}}, session=sid)
open(f"{OUT}/adcreate_full.png", "wb").write(base64.b64decode(shot["data"]))
print("截图尺寸:", metrics)

# 结构提取：所有标签 + 控件
struct = ev("""(() => {
  const out = [];
  const walk = (el, depth) => {
    for (const c of el.children) {
      const tag = c.tagName.toLowerCase();
      if (/^(input|select|textarea|button)$/.test(tag)) {
        out.push({t: tag, type: c.type, ph: c.placeholder || '', val: c.value || '',
                  txt: (c.innerText || '').slice(0, 30)});
      } else if (c.children.length === 0 && (c.innerText || '').trim()) {
        out.push({t: 'text', txt: c.innerText.trim().slice(0, 60)});
      } else walk(c, depth + 1);
      if (out.length > 1200) return;
    }
  };
  walk(document.body, 0);
  return out;
})()""")
open(f"{OUT}/struct.json", "w", encoding="utf-8").write(
    json.dumps(struct, ensure_ascii=False, indent=1))
print("结构条目:", len(struct or []))

call("Target.closeTarget", {"targetId": tid})
ws.close()
print("done:", OUT)
