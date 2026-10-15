#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动: 采集/展示 一体化服务

    python run.py            # http://127.0.0.1:8320
    python run.py --port 9000
"""
import argparse
import sys

import uvicorn


def _force_utf8_stdio() -> None:
    """把 stdout/stderr 钉成 UTF-8

    抓取线程的日志里带 ▶ ✔ ✗ 这类符号, 而 Windows 下输出被重定向(例如起服务时
    把输出写进日志文件)时 Python 会用系统默认编码(简体中文 = GBK), print 直接抛
    UnicodeEncodeError → 抓取线程整个挂掉(实测: 启动 1 秒即「任务异常终止」)。
    统一成 UTF-8, 并且用 errors="replace" 兜底(宁可少一个符号, 不能中断抓取)。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:       # noqa: BLE001  流被替换过/版本不支持: 忽略
            pass


def main():
    _force_utf8_stdio()
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8320)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    print(f"赛狐广告数据平台: http://{args.host}:{args.port}")
    uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
