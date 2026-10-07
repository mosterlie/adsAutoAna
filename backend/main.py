# -*- coding: utf-8 -*-
"""服务入口: 提供 API + 静态前端

启动:
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8320
或:
    python run.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI                       # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse       # noqa: E402
from fastapi.staticfiles import StaticFiles      # noqa: E402

import config                                     # noqa: E402
from backend import database as db                # noqa: E402
from backend.api import router                    # noqa: E402

FRONTEND_DIR = os.path.join(config.BASE_DIR, "frontend")

app = FastAPI(title="赛狐广告数据平台", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db.init_db()
app.include_router(router)

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/favicon.ico")
def favicon():
    from fastapi import Response
    return Response(status_code=204)
