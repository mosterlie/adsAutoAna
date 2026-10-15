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
from backend import amazon_store                  # noqa: E402
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


@app.get("/amz-media/{parent}/{term}/{stem}")
def amz_media(parent: str, term: str, stem: str):
    """本地主图: <父ASIN>/主图/<搜索词>/<顺位>_<ASIN>.<ext>

    页面看竞品图时走这里(读本地文件), 不再去亚马逊的图片 CDN 取,
    扩展名不固定, 由 backend.amazon_store.resolve_image 逐个试(并做目录穿越校验)。
    """
    from fastapi import Response
    path = amazon_store.resolve_image(parent, term, stem)
    if not path:
        return Response(status_code=404)
    return FileResponse(
        path, headers={"Cache-Control": "public, max-age=604800"})
