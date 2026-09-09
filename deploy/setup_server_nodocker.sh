#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ResumeAgent 免 Docker 部署脚本（Plan B）
#
# 适用：服务器无法访问 Docker Hub（docker pull 超时/被限）时，用 venv + systemd 直跑。
#
# 用法（代码已放到 /opt/ResumeAgent 后执行）：
#   sudo bash deploy/setup_server_nodocker.sh
#   sudo ENABLE_AUTH=1 bash deploy/setup_server_nodocker.sh   # 加访问口令（推荐）
#   sudo DOMAIN=resume.example.com bash deploy/setup_server_nodocker.sh  # 有域名时配 HTTPS
#
# 可选环境变量：
#   APP_DIR      代码目录，默认 /opt/ResumeAgent
#   DOMAIN       域名或公网 IP；填域名时自动申请证书
#   ENABLE_AUTH  设为 1 时开启 Nginx 访问口令（账号 admin，执行中交互输入密码）
#   SKIP_FRONTEND_BUILD=1  已自行上传 frontend/dist 时跳过前端构建
# ---------------------------------------------------------------------------
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/ResumeAgent}"
DOMAIN="${DOMAIN:-}"
ENABLE_AUTH="${ENABLE_AUTH:-0}"
SKIP_FRONTEND_BUILD="${SKIP_FRONTEND_BUILD:-0}"
VENV="$APP_DIR/.venv"
PIP_INDEX="https://mirrors.cloud.tencent.com/pypi/simple"

log()  { echo -e "\033[32m[部署]\033[0m $*"; }
warn() { echo -e "\033[33m[提示]\033[0m $*"; }
die()  { echo -e "\033[31m[失败]\033[0m $*"; exit 1; }

[ -d "$APP_DIR" ] || die "找不到代码目录 $APP_DIR"
cd "$APP_DIR"
[ "$(id -u)" -eq 0 ] || die "请用 root 执行：sudo bash deploy/setup_server_nodocker.sh"

# ---------------------------------------------------------------- 0. 环境检查
log "检查运行环境"
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    warn "未发现 .env，已从模板生成，请填好 API Key 后重跑：vi $APP_DIR/.env"
    exit 1
fi
grep -qE "^(DASHSCOPE|OPENAI|GEMINI)_API_KEY=[[:print:]]+" "$APP_DIR/.env" \
    || die ".env 中未填写任何 *_API_KEY"

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl ca-certificates apache2-utils

# ---------------------------------------------------------------- 1. 前端产物
if [ "$SKIP_FRONTEND_BUILD" != "1" ] && [ ! -f "$APP_DIR/frontend/dist/index.html" ]; then
    log "未发现前端产物，安装 Node 20 并构建（约 3~5 分钟）"
    if ! command -v node >/dev/null 2>&1 || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 18 ]; then
        curl -fsSL https://npmmirror.com/mirrors/node/v20.18.0/node-v20.18.0-linux-x64.tar.xz \
            | tar -xJ -C /opt
        ln -sf /opt/node-v20.18.0-linux-x64/bin/node /usr/local/bin/node
        ln -sf /opt/node-v20.18.0-linux-x64/bin/npm  /usr/local/bin/npm
        ln -sf /opt/node-v20.18.0-linux-x64/bin/npx  /usr/local/bin/npx
    fi
    cd "$APP_DIR/frontend"
    npm config set registry https://registry.npmmirror.com
    npm ci
    npm run build || { warn "类型检查未通过，改用 vite 直接打包"; npx vite build; }
    cd "$APP_DIR"
fi
[ -f "$APP_DIR/frontend/dist/index.html" ] || die "缺少前端产物 frontend/dist/index.html"

# ---------------------------------------------------------------- 2. Python 依赖
if [ ! -x "$VENV/bin/python" ]; then
    log "创建虚拟环境"
    python3 -m venv "$VENV"
fi
log "安装后端依赖（约 3~8 分钟）"
"$VENV/bin/pip" install --upgrade pip -i "$PIP_INDEX"
"$VENV/bin/pip" install -r requirements-prod.txt -i "$PIP_INDEX"

# ---------------------------------------------------------------- 3. systemd 服务
log "注册 systemd 服务"
cat > /etc/systemd/system/resume-agent.service <<EOF
[Unit]
Description=ResumeAgent API (uvicorn)
After=network.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
Environment=SERVE_STATIC=1
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV/bin/uvicorn app_api:app --host 127.0.0.1 --port 8000 --workers 1 --timeout-keep-alive 75
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now resume-agent

log "等待健康检查通过"
for i in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        log "后端就绪：$(curl -fsS http://127.0.0.1:8000/api/health)"
        break
    fi
    if [ "$i" -eq 40 ]; then
        journalctl -u resume-agent -n 80 --no-pager
        die "健康检查超时，请看上方日志"
    fi
    sleep 3
done

# ---------------------------------------------------------------- 4. Nginx
if ! command -v nginx >/dev/null 2>&1; then
    log "安装 Nginx"
    apt-get install -y nginx
    systemctl enable --now nginx
fi

if [ -z "$DOMAIN" ]; then
    SERVER_NAME="$(curl -fsS --max-time 5 ifconfig.me 2>/dev/null || echo '_')"
    warn "未设置 DOMAIN，站点名为 $SERVER_NAME（通过公网 IP 访问）"
else
    SERVER_NAME="$DOMAIN"
fi

sed "s/your-domain.com/$SERVER_NAME/" "$APP_DIR/deploy/nginx.conf" > /etc/nginx/conf.d/resume-agent.conf

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

rm -f /etc/nginx/sites-enabled/default
nginx -t || die "Nginx 配置有误"
systemctl reload nginx
log "Nginx 已加载配置"

# ---------------------------------------------------------------- 5. HTTPS
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    log "为 $DOMAIN 申请 HTTPS 证书"
    command -v certbot >/dev/null 2>&1 || apt-get install -y certbot python3-certbot-nginx
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email \
        || warn "证书申请失败，稍后可手动执行：certbot --nginx -d $DOMAIN"
fi

# ---------------------------------------------------------------- 6. 结果
echo
log "部署完成（免 Docker 模式）"
if [ -n "$DOMAIN" ] && ! [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "  访问地址：https://$DOMAIN"
else
    echo "  访问地址：http://<你的公网IP>"
fi
echo "  查看日志：journalctl -u resume-agent -f"
echo "  重启服务：systemctl restart resume-agent"
echo "  停止服务：systemctl stop resume-agent"
