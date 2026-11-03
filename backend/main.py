# -*- coding: utf-8 -*-
"""服务入口: 提供 API + 静态前端

启动:
    python -m uvicorn backend.main:app --host 127.0.0.1 --port 8320
或:
    python run.py
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI                       # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402
from fastapi.staticfiles import StaticFiles      # noqa: E402

import config                                     # noqa: E402
from backend import amazon_store                  # noqa: E402
from backend import database as db                # noqa: E402
from backend.ad_creator_api import router as ad_creator_router  # noqa: E402
from backend.api import router                    # noqa: E402

FRONTEND_DIR = os.path.join(config.BASE_DIR, "frontend")

app = FastAPI(title="赛狐广告数据平台", version="1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

db.init_db()
app.include_router(router)
app.include_router(ad_creator_router)      # 广告创建(自 autoAds 平移)

if os.path.isdir(FRONTEND_DIR):
    class NoCacheStatic(StaticFiles):
        """前端资源每次都回源校验

        StaticFiles 默认不发 Cache-Control, 浏览器会按启发式缓存 app.js,
        改完前端刷新看不到新代码(这在本地工具里很容易踩)。这里强制 no-cache:
        仍带 ETag/Last-Modified, 未变时 304, 开销可忽略。
        """

        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            resp.headers["Cache-Control"] = "no-cache"
            return resp

    app.mount("/static", NoCacheStatic(directory=FRONTEND_DIR), name="static")


def _asset_version() -> str:
    """静态资源指纹（文件名 + mtime + 大小）。

    index.html 里的 css/js 引用会带上 ?v=指纹：文件一改指纹就变，
    浏览器不会再拿旧缓存渲染（此前出现过「代码已更新、页面还是老布局」）。
    """
    h = hashlib.md5()
    for name in ("style.css", "app.js"):
        try:
            st = os.stat(os.path.join(FRONTEND_DIR, name))
            h.update(f"{name}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            continue
    return h.hexdigest()[:10]


@app.get("/")
def index():
    path = os.path.join(FRONTEND_DIR, "index.html")
    try:
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return FileResponse(path)
    v = _asset_version()
    html = html.replace("/static/style.css", f"/static/style.css?v={v}")
    html = html.replace("/static/app.js", f"/static/app.js?v={v}")
    return Response(html, media_type="text/html",
                    headers={"Cache-Control": "no-cache, must-revalidate"})


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
