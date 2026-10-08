#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ResumeAgent 服务器端一键部署脚本
#
# 用法（在服务器上，代码已放到 /opt/ResumeAgent 后执行）：
#   sudo bash deploy/setup_server.sh                    # 仅 HTTP，用 IP 访问
#   sudo ENABLE_AUTH=1 bash deploy/setup_server.sh      # 再加一层访问口令（推荐）
#   sudo DOMAIN=resume.example.com bash deploy/setup_server.sh   # 有域名时自动申请 HTTPS
#
# 可选环境变量：
#   APP_DIR          代码目录，默认 /opt/ResumeAgent
#   DOMAIN           域名或公网 IP；填域名时自动申请证书，填 IP 则只配 HTTP
#   ENABLE_AUTH      设为 1 时开启 Nginx 访问口令（账号 admin，执行中交互输入密码）
#   SKIP_MIGRATIONS  设为 1 时跳过 alembic upgrade head（数据库已自行迁移时用）
#
# 前置条件：.env 中除 *_API_KEY 外，还必须配置 SECRET_KEY（JWT 鉴权，空值启动即报错）
#           与 DATABASE_URL（PostgreSQL；本编排不含 db 服务，需外部可访问的数据库）
#
# 脚本额外完成两件 update.sh 不做的事：
#   1. 初始化 ./runtime-data（compose 挂载卷，不初始化会遮盖镜像内置知识库）
#   2. 构建镜像后执行 alembic upgrade head 建表
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ResumeAgent}"
DOMAIN="${DOMAIN:-}"
ENABLE_AUTH="${ENABLE_AUTH:-0}"
SKIP_MIGRATIONS="${SKIP_MIGRATIONS:-0}"

log()  { echo -e "\033[32m[部署]\033[0m $*"; }
warn() { echo -e "\033[33m[提示]\033[0m $*"; }
die()  { echo -e "\033[31m[失败]\033[0m $*"; exit 1; }

[ -d "$APP_DIR" ] || die "找不到代码目录 $APP_DIR，请先把项目上传到该目录"
cd "$APP_DIR"

# ---------------------------------------------------------------- 0. 环境检查
log "检查运行环境"
[ "$(id -u)" -eq 0 ] || die "请用 root 执行：sudo bash deploy/setup_server.sh"

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    warn "未发现 .env，已从模板生成。请填好 API Key 后重跑本脚本："
    echo "    vi $APP_DIR/.env"
    exit 1
fi

grep -qE "^(DASHSCOPE|OPENAI|GEMINI)_API_KEY=[[:print:]]+" "$APP_DIR/.env" \
    || die ".env 中未填写任何 *_API_KEY，请编辑 $APP_DIR/.env 后重跑"
grep -qE "^[A-Z_]+_API_KEY=(your_)?$" "$APP_DIR/.env" \
    && die ".env 中 API Key 仍是占位值，请替换为真实 Key 后重跑"

# 多用户鉴权与数据库：两者缺失都会在启动时直接崩（src/config.py 对 SECRET_KEY 是硬校验）
grep -qE "^SECRET_KEY=[[:print:]]+$" "$APP_DIR/.env" \
    || die ".env 未设置 SECRET_KEY（JWT 鉴权必需，留空会 RuntimeError: SECRET_KEY not set）"
grep -qE "^DATABASE_URL=[[:print:]]+$" "$APP_DIR/.env" \
    || die ".env 未设置 DATABASE_URL（PostgreSQL 连接串，如 postgresql+psycopg://user:pwd@host:5432/db）"
grep -qE "^DATABASE_URL=.*(username:password|table_name)" "$APP_DIR/.env" \
    && die ".env 的 DATABASE_URL 仍是 .env.example 的占位值，请改成真实连接串"
# Redis 由 compose 内部服务提供，REDIS_URL 会被 compose environment 覆盖，故不强制校验
if grep -qE "^DATABASE_URL=.*@(localhost|127\.0\.0\.1)(:|/|$)" "$APP_DIR/.env"; then
    warn "DATABASE_URL 指向 localhost/127.0.0.1：容器内 localhost 是容器自身，连不到宿主数据库，请改成外部地址"
fi

# ---------------------------------------------------------------- 1. Docker
if command -v docker >/dev/null 2>&1; then
    log "Docker 已安装：$(docker --version)"
else
    log "安装 Docker（约 2 分钟）"
    apt-get update -y
    apt-get install -y ca-certificates curl gnupg apache2-utils
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

docker compose version >/dev/null 2>&1 \
    || { apt-get install -y docker-compose-plugin; }

# ------------------------------------------------- 2. 数据卷 / 迁移 / 启动
# compose 把 ./runtime-data 挂到 /app/data。空目录挂载会「遮盖」镜像内置的知识库索引，
# 首次必须先用随包发布的 data/ 填充；之后该卷就是权威数据（更新走 deploy/update.sh data）。
seed_runtime_data() {
    mkdir -p "$APP_DIR/runtime-data"
    if [ -n "$(ls -A "$APP_DIR/runtime-data" 2>/dev/null)" ]; then
        log "runtime-data 已有数据，跳过初始化"
        return
    fi
    log "初始化 runtime-data（知识库索引与素材）"
    for sub in processed databases knowledge_base/project_intros knowledge_base/project_highlights; do
        if [ -d "$APP_DIR/data/$sub" ]; then
            mkdir -p "$APP_DIR/runtime-data/$sub"
            cp -a "$APP_DIR/data/$sub/." "$APP_DIR/runtime-data/$sub/"
        else
            warn "缺少 data/$sub，已跳过（问答可能为空，请确认本地执行过 python main.py build-all）"
        fi
    done
}

# 迁移在容器内执行：镜像已内置 alembic.ini 与 alembic/（见 Dockerfile），
# 且 alembic/env.py 通过 src.config 读取 .env 的 DATABASE_URL。
run_migrations() {
    if [ "$SKIP_MIGRATIONS" = "1" ]; then
        warn "SKIP_MIGRATIONS=1，跳过数据库迁移"
        return
    fi
    log "执行数据库迁移：alembic upgrade head"
    docker compose run --rm -T --no-deps resume-agent alembic upgrade head \
        || die "迁移失败：请检查 .env 的 DATABASE_URL 是否正确且服务器能访问该数据库"
}

seed_runtime_data

log "构建镜像（首次约 10~20 分钟：前端 npm build + 后端 pip 安装）"
docker compose build

run_migrations

log "启动容器"
docker compose up -d

log "等待健康检查通过"
for i in $(seq 1 60); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        log "后端就绪：$(curl -fsS http://127.0.0.1:8000/api/health)"
        break
    fi
    if [ "$i" -eq 60 ]; then
        docker compose logs --tail=80
        die "健康检查超时，请看上方日志"
    fi
    sleep 5
done

# ---------------------------------------------------------------- 3. Nginx
if ! command -v nginx >/dev/null 2>&1; then
    log "安装 Nginx"
    apt-get install -y nginx
    systemctl enable --now nginx
fi

if [ -z "$DOMAIN" ]; then
    SERVER_NAME="$(curl -fsS --max-time 5 ifconfig.me 2>/dev/null || true)"
    [ -z "$SERVER_NAME" ] && SERVER_NAME="_"
    warn "未设置 DOMAIN，使用默认站点名 $SERVER_NAME（通过服务器 IP 访问）"
else
    SERVER_NAME="$DOMAIN"
fi

sed "s/your-domain.com/$SERVER_NAME/" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/conf.d/resume-agent.conf

# 访问口令：账号固定 admin，密码执行时输入
if [ "$ENABLE_AUTH" = "1" ]; then
    log "开启访问口令"
    read -rsp '请输入访问密码（输入不回显）: ' AUTH_PWD
    echo
    [ -n "$AUTH_PWD" ] || die "密码不能为空"
    htpasswd -bc /etc/nginx/.htpasswd admin "$AUTH_PWD"
    sed -i 's|^    # auth_basic "ResumeAgent";|    auth_basic "ResumeAgent";|' /etc/nginx/conf.d/resume-agent.conf
    sed -i 's|^    # auth_basic_user_file /etc/nginx/.htpasswd;|    auth_basic_user_file /etc/nginx/.htpasswd;|' /etc/nginx/conf.d/resume-agent.conf
    log "已开启口令：admin / ******"
fi

# Ubuntu 默认站点会抢占 80 端口，必须移除
rm -f /etc/nginx/sites-enabled/default
nginx -t || die "Nginx 配置有误"
systemctl reload nginx
log "Nginx 已加载配置"

# ---------------------------------------------------------------- 4. HTTPS
# 仅在 DOMAIN 是域名（非 IP）时申请证书
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log "为 $DOMAIN 申请 HTTPS 证书"
    command -v certbot >/dev/null 2>&1 || apt-get install -y certbot python3-certbot-nginx
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email \
        || warn "证书申请失败（域名未解析到本机？80/443 未放通？）。稍后可手动执行：certbot --nginx -d $DOMAIN"
fi

# ---------------------------------------------------------------- 5. 结果
echo
log "部署完成"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "  访问地址：https://$DOMAIN"
else
    echo "  访问地址：http://124.222.88.64 （若脚本取到的 IP 不对，以你的公网 IP 为准）"
fi
echo "  查看日志：cd $APP_DIR && docker compose logs -f --tail 200（含 redis 与两个 celery worker）"
echo "  重启服务：cd $APP_DIR && docker compose restart"
echo "  停止服务：cd $APP_DIR && docker compose down"
echo "  重跑迁移：cd $APP_DIR && docker compose run --rm -T --no-deps resume-agent alembic upgrade head"
