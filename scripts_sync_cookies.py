#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从调试 Chrome(9222) 同步赛狐登录 Cookie 到 data/cookies.json

前置: 先以调试模式启动 Chrome 并登录赛狐:
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
      --remote-debugging-port=9222 --user-data-dir="$HOME/ChromeDebugUser"

用法:
  python scripts_sync_cookies.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backend.cookies import sync_from_browser   # noqa: E402

if __name__ == "__main__":
    c = sync_from_browser()
    print(f"✅ 已同步 {len(c)} 项 Cookie → data/cookies.json")
    print("   关键项:", [k for k in c if k in ("sf_u", "SESSION", "tgw_l7_route")] or list(c)[:6])
