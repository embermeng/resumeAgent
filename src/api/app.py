"""
FastAPI 应用工厂:装配路由、CORS、健康检查、(可选)前端静态托管。
契约见 docs/specs/api-contract.md。
"""
import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.api.routers import chat, knowledge, resume, conversations, auth

_log = logging.getLogger(__name__)


def _cors_origins() -> list[str]:
    """允许的跨域来源。

    生产建议通过 CORS_ORIGINS 指定前端域名(逗号分隔),例如:
        CORS_ORIGINS=https://resume.example.com
    未设置时沿用开发默认 "*"(allow_origins=["*"] 时 credentials 必须为 False)。
    """
    raw = os.getenv("CORS_ORIGINS", "*")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    return origins or ["*"]


def _mount_frontend(app: FastAPI) -> None:
    """生产单容器部署:托管 frontend/dist 构建产物,实现前后端同域。

    - 仅当 SERVE_STATIC=1(镜像中默认开启,本地/CI 默认关闭)且 dist 存在时生效;
    - 前端全部请求走相对路径 /api,同域后无需 CORS 与额外 baseURL 配置;
    - /api/* 前缀未命中时仍返回 404 JSON,不改变既有 API 行为(见 tests/test_api)。
    """
    if os.getenv("SERVE_STATIC", "0") != "1":
        return

    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    index = dist / "index.html"
    if not index.exists():
        _log.warning(f"SERVE_STATIC=1 但未找到前端产物: {index}，跳过静态托管")
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="static-assets")

    @app.get("/", include_in_schema=False)
    def _spa_index():
        return FileResponse(index)

    @app.get("/{full_path:path}", include_in_schema=False)
    def _spa_fallback(full_path: str):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        candidate = (dist / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(index)

    _log.info(f"已启用前端静态托管: {dist}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="ResumeAgent API",
        version="1.0.0",
        description="ResumeAgent 前后端分离后端。契约见 docs/specs/api-contract.md",
    )

    # 跨域:开发用 "*";生产用 CORS_ORIGINS 收敛为具体前端域名。
    # 注意:allow_origins=["*"] 时 allow_credentials 必须为 False(浏览器 CORS 规范)。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(resume.router, prefix="/api", tags=["resume"])
    app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])
    app.include_router(conversations.router, prefix="/api", tags=["conversations"])
    app.include_router(auth.router, prefix="/api", tags=["auth"])

    @app.get("/api/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    # 必须最后挂载:catch-all 路由不能抢占 /api 与 /docs
    _mount_frontend(app)

    return app
