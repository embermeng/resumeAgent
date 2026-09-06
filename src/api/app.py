"""
FastAPI 应用工厂:装配路由、CORS、健康检查。
契约见 docs/specs/api-contract.md。
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import chat, knowledge, resume

_log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="ResumeAgent API",
        version="1.0.0",
        description="ResumeAgent 前后端分离后端。契约见 docs/specs/api-contract.md",
    )

    # 开发/演示:放开跨域;生产环境应收敛为具体前端域名。
    # 注意:allow_origins=["*"] 时 allow_credentials 必须为 False(浏览器 CORS 规范)。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(chat.router, prefix="/api", tags=["chat"])
    app.include_router(resume.router, prefix="/api", tags=["resume"])
    app.include_router(knowledge.router, prefix="/api", tags=["knowledge"])

    @app.get("/api/health", tags=["meta"])
    def health():
        return {"status": "ok"}

    return app
