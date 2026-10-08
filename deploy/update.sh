#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ResumeAgent 更新脚本（在服务器上执行）
#
# 用法：
#   sudo bash deploy/update.sh            # 全量更新（默认）：前端 + 后端 + 数据
#   sudo bash deploy/update.sh backend    # 只更新后端 Python 代码
#   sudo bash deploy/update.sh frontend   # 只重新构建前端
#   sudo bash deploy/update.sh data       # 只更新知识库数据（索引/素材）
#
# 可选环境变量：
#   APP_DIR              代码目录，默认 /opt/ResumeAgent
#   SKIP_FRONTEND_BUILD=1 已自行上传 frontend/dist 时跳过前端构建
#   SKIP_MIGRATIONS=1    跳过 alembic upgrade head
#
# 说明：
#   - 自动识别当前是 Docker 模式还是 systemd（免 Docker）模式
#   - Docker 模式：前端产物与 data 都内嵌在镜像里，任何更新都需 --build 重建
#   - systemd 模式：dist 与 data 都在宿主机，改完重启进程即可
#   - backend / all 会在重启后执行 alembic upgrade head（ORM 变更需先跑迁移再重启才完全生效）
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ResumeAgent}"
TARGET="${1:-all}"
SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"
SKIP_MIGRATIONS="${SKIP_MIGRATIONS:-0}"
VENV="$APP_DIR/.venv"
PIP_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"

log()  { echo -e "\033[32m[更新]\033[0m $*"; }
warn() { echo -e "\033[33m[提示]\033[0m $*"; }
die()  { echo -e "\033[31m[失败]\033[0m $*"; exit 1; }

cd "$APP_DIR" || die "找不到目录 $APP_DIR"
[ "$(id -u)" -eq 0 ] || die "请用 root 执行：sudo bash deploy/update.sh"

# ---------------------------------------------------------------- 识别部署模式
if [ -f /etc/systemd/system/resume-agent.service ]; then
    MODE="systemd"
else
    MODE="docker"
fi
log "部署模式：$MODE，更新范围：$TARGET"

# ---------------------------------------------------------------- 前端
build_frontend() {
    if [ "$SKIP_FRONTEND_BUILD" = "1" ] || [ -f "$APP_DIR/frontend/dist/index.html" ]; then
        log "跳过前端构建（已存在 dist）"
        return
    fi
    if [ "$MODE" = "docker" ]; then
        log "前端由镜像构建阶段处理"
        return
    fi
    log "构建前端"
    if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]; then
        curl -fsSL https://npmmirror.com/mirrors/node/v20.18.0/node-v20.18.0-linux-x64.tar.xz | tar -xJ -C /opt
        ln -sf /opt/node-v20.18.0-linux-x64/bin/node /usr/local/bin/node
        ln -sf /opt/node-v20.18.0-linux-x64/bin/npm  /usr/local/bin/npm
        ln -sf /opt/node-v20.18.0-linux-x64/bin/npx  /usr/local/bin/npx
    fi
    cd "$APP_DIR/frontend"
    npm config set registry https://registry.npmmirror.com
    npm ci
    npm run build || { warn "类型检查未通过，改用 vite 直接打包"; npx vite build; }
    cd "$APP_DIR"
}

# ---------------------------------------------------------------- 后端/服务
restart_workers() {
    # celery worker 不热重载：不重启则内存里还是旧任务体（老部署机无 worker 单元时静默跳过）
    for u in resume-agent-worker-resume resume-agent-worker-knowledge; do
        # || true：set -e 下短路返回非零会退出脚本（老部署机无 worker 单元）
        { [ -f "/etc/systemd/system/$u.service" ] && systemctl restart "$u"; } || true
    done
}

run_migrations() {
    if [ "$SKIP_MIGRATIONS" = "1" ]; then
        warn "SKIP_MIGRATIONS=1，跳过数据库迁移"
        return
    fi
    if [ ! -f "$APP_DIR/alembic.ini" ]; then
        warn "未找到 alembic.ini（旧版本发布包？），跳过迁移；建议重新打包发布"
        return
    fi
    if [ "$MODE" = "systemd" ]; then
        log "执行数据库迁移：alembic upgrade head"
        (cd "$APP_DIR" && "$VENV/bin/alembic" upgrade head) \
            || die "迁移失败：检查 .env 的 DATABASE_URL 与数据库可达性"
    else
        # 镜像已内置 alembic.ini / alembic/，用一次性容器跑迁移（--no-deps 不拉起 redis）
        log "执行数据库迁移：容器内 alembic upgrade head"
        (cd "$APP_DIR" && docker compose run --rm -T --no-deps resume-agent alembic upgrade head) \
            || die "迁移失败：检查 .env 的 DATABASE_URL 与数据库可达性"
    fi
}

restart_service() {
    if [ "$MODE" = "systemd" ]; then
        log "安装/更新后端依赖"
        "$VENV/bin/pip" install -r requirements-prod.txt -i "$PIP_INDEX" -q
        log "重启 resume-agent 服务（API + worker）"
        systemctl restart resume-agent
        restart_workers
        for i in $(seq 1 30); do
            curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
            [ "$i" -eq 30 ] && { journalctl -u resume-agent -n 60 --no-pager; die "启动失败，请看上方日志"; }
            sleep 2
        done
    else
        log "重建镜像并重启容器"
        docker compose up -d --build
        for i in $(seq 1 40); do
            curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
            [ "$i" -eq 40 ] && { docker compose logs --tail 60; die "启动失败，请看上方日志"; }
            sleep 3
        done
    fi
}

case "$TARGET" in
    frontend)
        build_frontend
        if [ "$MODE" = "docker" ]; then restart_service; else log "静态资源每次请求读盘，无需重启"; fi
        ;;
    backend)
        restart_service
        run_migrations
        ;;
    data)
        if [ "$MODE" = "docker" ]; then
            warn "Docker 模式下 data 内嵌镜像，需重建镜像"
            restart_service
        else
            log "重启以重新加载索引（API + worker）"
            systemctl restart resume-agent
            restart_workers
            for i in $(seq 1 30); do
                curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && break
                sleep 2
            done
        fi
        ;;
    all)
        build_frontend
        restart_service
        run_migrations
        ;;
    *)
        die "未知参数 $TARGET（可选：backend | frontend | data | all）"
        ;;
esac

log "更新完成：$(curl -fsS http://127.0.0.1:8000/api/health)"
if [ "$MODE" = "systemd" ]; then
    echo "  查看日志：journalctl -u resume-agent -f"
else
    echo "  查看日志：cd $APP_DIR && docker compose logs -f --tail 100"
fi
