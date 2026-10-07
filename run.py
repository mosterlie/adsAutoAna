#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""启动: 采集/展示 一体化服务

    python run.py            # http://127.0.0.1:8320
    python run.py --port 9000
"""
import argparse

import uvicorn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8320)
    ap.add_argument("--reload", action="store_true")
    args = ap.parse_args()
    print(f"赛狐广告数据平台: http://{args.host}:{args.port}")
    uvicorn.run("backend.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
