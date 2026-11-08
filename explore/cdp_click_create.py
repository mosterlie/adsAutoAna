# -*- coding: utf-8 -*-
"""点击赛狐「创建」按钮，抓取创建相关的请求/响应

输出：
    /tmp/create_capture.json   —— 原始事件（请求头/请求体/状态码/响应体）
    /tmp/create_summary.txt    —— 精简摘要（可直接看接口契约）
"""
import json
import time

import urllib.request
import websocket

OUT_JSON = "/tmp/create_capture.json"
OUT_TXT = "/tmp/create_summary.txt"

tabs = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json", timeout=5))
tab = next(x for x in tabs if x.get("type") == "page")
ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30, suppress_origin=True)
ws.settimeout(1.0)

_id = [0]


def send(method, params=None, timeout=10):
    _id[0] += 1
    mid = _id[0]
    ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            m = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        if m.get("id") == mid:
            return m
    return {}


send("Network.enable", {"maxPostDataSize": 200000})
send("Runtime.enable")
send("Page.enable")

# ---- 点击「创建」 ----
click_expr = r"""
(function(){
  var host=[...document.querySelectorAll('wujie-app')].find(h=>h.offsetWidth>0 &&
      h.shadowRoot && h.shadowRoot.querySelectorAll('.el-form-item').length>3);
  if(!host) return 'no host';
  var root=host.shadowRoot;
  var btns=[...root.querySelectorAll('button')].map(b=>b.innerText.trim());
  var b=[...root.querySelectorAll('button')].find(x=>/^创\s*建$/.test(x.innerText.trim()));
  if(!b) return 'no create btn; all=' + JSON.stringify(btns);
  b.scrollIntoView({block:'center'});
  b.click();
  return 'clicked';
})()
"""
r = send("Runtime.evaluate", {"expression": click_expr, "returnByValue": True})
print("点击结果:", r.get("result", {}).get("result", {}).get("value"))

# ---- 收集事件 ----
events = []
t0 = time.time()
confirmed = 0
while time.time() - t0 < 40:
    try:
        m = json.loads(ws.recv())
    except websocket.WebSocketTimeoutException:
        m = None
    if m:
        events.append(m)
    # 每 3 秒检查一次二次确认弹窗
    if time.time() - t0 - confirmed * 3 >= 3:
        confirmed += 1
        r = send("Runtime.evaluate", {"expression": r"""
        (function(){
          var host=[...document.querySelectorAll('wujie-app')].find(h=>h.offsetWidth>0 && h.shadowRoot);
          var roots=[document, host?host.shadowRoot:null].filter(Boolean);
          for (var r of roots) {
            var boxes=[...r.querySelectorAll('.el-message-box, .el-dialog')].filter(d=>d.offsetParent!==null);
            for (var bx of boxes) {
              var b=[...bx.querySelectorAll('button')].find(x=>/确定|确认|创建/.test(x.innerText));
              if (b && !b.disabled) { b.click(); return 'confirmed:'+bx.innerText.replace(/\s+/g,' ').slice(0,60); }
            }
          }
          return 'no dialog';
        })()
        """, "returnByValue": True}, timeout=3)
        v = r.get("result", {}).get("result", {}).get("value")
        if v and v.startswith("confirmed"):
            print("二次确认:", v)

# ---- 拉响应体 ----
reqs = {}
for m in events:
    p = m.get("params") or {}
    if m.get("method") == "Network.requestWillBeSent":
        rid = p["requestId"]
        req = p["request"]
        if p.get("type") in ("XHR", "Fetch"):
            reqs[rid] = {"url": req.get("url"), "method": req.get("method"),
                         "headers": req.get("headers"), "postData": req.get("postData"),
                         "status": None, "respBody": None}
    elif m.get("method") == "Network.responseReceived":
        rid = p["requestId"]
        if rid in reqs:
            reqs[rid]["status"] = p["response"].get("status")
            reqs[rid]["mimeType"] = p["response"].get("mimeType")

for rid, rec in reqs.items():
    if rec["status"] is None:
        continue
    r = send("Network.getResponseBody", {"requestId": rid}, timeout=5)
    body = r.get("result", {}).get("body")
    rec["respBody"] = body

json.dump(list(reqs.values()), open(OUT_JSON, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ---- 摘要 ----
lines = []
lines.append(f"共捕获 XHR/Fetch {len(reqs)} 个（点击后 40s 内）\n")
for rec in reqs.values():
    u = rec["url"] or ""
    lines.append(f"{rec['method']:4} {rec['status']}  {u[:150]}")

key = [r for r in reqs.values()
       if r["method"] == "POST" and any(k in (r["url"] or "") for k in
                                        ("create", "campaign", "ad/", "adGroup", "target", "save"))]
lines.append("\n\n======== 疑似创建相关接口 ========\n")
for rec in key:
    lines.append(f"\n--- {rec['method']} {rec['url']}")
    lines.append(f"status={rec['status']}")
    lines.append(f"headers={json.dumps(rec['headers'], ensure_ascii=False)[:600]}")
    lines.append(f"postData={rec['postData']}")
    rb = rec.get("respBody") or ""
    lines.append(f"resp={rb[:1200]}")

open(OUT_TXT, "w", encoding="utf-8").write("\n".join(lines))
print(f"\n已保存 {OUT_JSON} / {OUT_TXT}")
print("\n".join(lines[:40]))
ws.close()
