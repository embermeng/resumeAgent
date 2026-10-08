# ---------- 阶段一：构建前端 ----------
FROM node:20-alpine AS frontend
# 国内服务器建议用 npmmirror；海外服务器可 --build-arg NPM_REGISTRY=https://registry.npmjs.org
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm config set registry "$NPM_REGISTRY" && npm ci
COPY frontend/ ./
# vue-tsc 类型检查 + vite 打包 -> dist/
RUN npm run build

# ---------- 阶段二：后端运行时 ----------
FROM python:3.11-slim

# 腾讯云/国内机器用镜像源更快；海外机器可 --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.cloud.tencent.com/pypi/simple
ENV PIP_INDEX_URL=$PIP_INDEX_URL

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SERVE_STATIC=1

WORKDIR /app

# curl 供容器健康检查使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-prod.txt requirements.txt ./
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY app_api.py main.py ./
COPY src/ ./src/
COPY --from=frontend /build/dist ./frontend/dist

# 数据库迁移目录：内置后才能在容器内直接执行 alembic upgrade head
# （alembic/env.py 会 import src.* 读取 DATABASE_URL，故必须与源码同在）
COPY alembic.ini ./
COPY alembic/ ./alembic/

# 知识库数据：只打包「索引产物 + 文档素材」，不打包课程 PDF（约 98MB，云端不解析）
# 若 data 目录尚未构建，请先在本地执行 python main.py build-all，或删除下面三行改为挂载卷
COPY data/processed ./data/processed
COPY data/databases ./data/databases
COPY data/knowledge_base/project_intros ./data/knowledge_base/project_intros
COPY data/knowledge_base/project_highlights ./data/knowledge_base/project_highlights

# API Key 等敏感配置通过 docker run --env-file / compose env_file 注入，绝不打进镜像
EXPOSE 8000

# workers 必须为 1：Agent / TaskManager 为进程内单例，多进程会导致任务状态与 SSE 流查不到
CMD ["uvicorn", "app_api:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "1", "--timeout-keep-alive", "75"]
