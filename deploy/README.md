# 部署速查表（复制即用）

> 完整说明见 [`../docs/部署方案.md`](../docs/部署方案.md)
> 当前环境：腾讯云轻量 **Ubuntu 22.04 / 2核4G**，公网 IP `124.222.88.64`，登录用户 `ubuntu`

---

## 一、首次部署

### 1. 本地打包上传（Windows PowerShell）

```powershell
cd f:\study\AI大模型课\ResumeAgent

# 打包 + 自动上传（19MB，自动排除 node_modules / 课程PDF / .env）
powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64

# 密钥单独传（不进包、不进镜像）
scp .env ubuntu@124.222.88.64:/home/ubuntu/.env
```

> 不加 `-Remote` 则只打包，产物在**项目上一级目录**：`f:\study\AI大模型课\resume-agent.tgz`
> 打包清单已含 `alembic.ini` 与 `alembic/`（迁移必需），`.env` 仍单独传。
>
> **`-Remote` 只上传不解压**（默认传到 `~`，即 `/home/ubuntu/`）。想一步到位：
>
> ```powershell
> # 打包 → 上传 → 服务器解压 → 执行 update.sh（全量）
> powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64 -Apply
> # 只更新后端
> powershell ... -Remote ubuntu@124.222.88.64 -Apply -UpdateArgs backend
> # 自定义上传目录 / 项目目录
> powershell ... -Remote ubuntu@124.222.88.64 -RemoteDir /opt/ResumeAgent -AppDir /opt/ResumeAgent -Apply
> ```

`.env` 除 API Key 外**必须**包含（缺一项就启动失败）：

```ini
SECRET_KEY=<随机长字符串>          # 空值 → RuntimeError: SECRET_KEY not set
DATABASE_URL=postgresql+psycopg://user:pwd@<host>:5432/resumeagent
REDIS_URL=redis://localhost:6379/0 # Docker 模式由 compose 覆盖为 redis://redis:6379/0
```

### 2. 控制台放行端口

腾讯云轻量 → 防火墙 → 添加规则：`HTTP 80`、`HTTPS 443`（来源 `0.0.0.0/0`）。**不做这步后面全白搭。**
`8000`（API）与 `6379`（Redis）**不要**对外，只走 Nginx。

### 3. 服务器部署

```bash
sudo mkdir -p /opt/ResumeAgent && sudo chown -R ubuntu /opt/ResumeAgent
cd /opt/ResumeAgent && tar -xzf /home/ubuntu/resume-agent.tgz
mv /home/ubuntu/.env /opt/ResumeAgent/.env && chmod 600 .env

# 方案 A：Docker（需能拉取 Docker Hub 镜像）
sudo ENABLE_AUTH=1 bash deploy/setup_server.sh

# 方案 B：免 Docker（Docker Hub 拉不动时用，只依赖 apt/pip 国内源）
sudo ENABLE_AUTH=1 bash deploy/setup_server_nodocker.sh
```

脚本会自动完成：校验 `.env` → 装依赖 → **初始化 `runtime-data`**（Docker 模式）→ 构建 → **`alembic upgrade head` 建表** → 启动 → 配 Nginx。
数据库需你自备（见下一节）。

`ENABLE_AUTH=1` 会让你设一个访问密码（账号 `admin`）。项目自带**多用户 JWT 注册登录**，但仍建议再加这层口令，避免陌生人随意注册。

访问：`http://124.222.88.64`

---

## 二、数据库与迁移（重要，别漏）

项目已是 **多用户 JWT 鉴权 + PostgreSQL + Celery/Redis** 架构：

| 依赖 | Docker 模式 | 免 Docker 模式 | 说明 |
|---|---|---|---|
| PostgreSQL | **外部数据库**（`docker-compose.yml` 不含 db 服务） | 外部或本机 `apt install -y postgresql` | 由 `.env` 的 `DATABASE_URL` 指定 |
| Redis | compose 内 `redis:7-alpine`，无需配置 | 脚本自动 `apt install -y redis-server` | Celery broker/backend + 检索缓存 |

**建表（迁移）时机**：

- 首次部署：两个 `setup_server*.sh` 已自动执行 `alembic upgrade head`
- 日常更新：`sudo bash deploy/update.sh`（全量或 `backend`）重启服务后自动执行
- 手动重跑：
  ```bash
  # Docker 模式
  cd /opt/ResumeAgent && docker compose run --rm -T --no-deps resume-agent alembic upgrade head
  # systemd 模式
  cd /opt/ResumeAgent && .venv/bin/alembic upgrade head
  ```
- 跳过迁移：`sudo SKIP_MIGRATIONS=1 bash deploy/update.sh`

> Docker 模式下 `DATABASE_URL` 别写 `localhost`（容器内 localhost 指容器自己），脚本检测到会告警。
> 想让 compose 自带数据库：自行在 `docker-compose.yml` 加一个 `postgres:16-alpine` 服务（参考 `docker-compose.dev.yml`），并把 `DATABASE_URL` 改成 `...@db:5432/...`。

---

## 三、日常更新

```powershell
# 本地一条命令打包+上传（改过前端且本地构建过就加 -IncludeFrontendDist）
powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64

# 一步到位：打包+上传+服务器解压+更新（加 -UpdateArgs backend/frontend/data 可缩小范围）
powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64 -Apply
```

不带 `-Apply` 时才需要在服务器手动执行下面这段（**上传了没解压是常见坑，包在 `~/resume-agent.tgz` 但 `/opt/ResumeAgent` 还是旧的**）：

```bash
cd /opt/ResumeAgent && sudo tar -xzf ~/resume-agent.tgz

sudo bash deploy/update.sh            # 全量（默认）
sudo bash deploy/update.sh backend    # 只改了 Python
sudo bash deploy/update.sh frontend   # 只改了前端
sudo bash deploy/update.sh data       # 只更新了知识库索引/素材
```

| 改动 | systemd 模式 | Docker 模式 |
|---|---|---|
| 前端 `.vue/.ts` | 重建 `dist`，**不用重启** | 必须重建镜像 |
| 后端 `.py` | 重启服务 | 重建镜像 |
| `data/` 索引 | 重启（重新加载索引） | 重建镜像（data 内嵌） |
| 依赖清单 | 自动重装 + 重启 | 自动重建镜像 |

**只改项目介绍文档 → 连重启都不用**（运行时直接读）：

```powershell
scp 我的项目.md ubuntu@124.222.88.64:/home/ubuntu/
```
```bash
sudo mv /home/ubuntu/我的项目.md /opt/ResumeAgent/data/knowledge_base/project_intros/
```

---

## 四、运维命令

| 操作 | systemd 模式 | Docker 模式 |
|---|---|---|
| 看日志（API） | `journalctl -u resume-agent -f` | `docker compose logs -f --tail 100 resume-agent` |
| 看日志（worker） | `journalctl -u resume-agent-worker-resume -f` | `docker compose logs -f worker-resume worker-knowledge` |
| 重启 | `systemctl restart resume-agent resume-agent-worker-resume resume-agent-worker-knowledge` | `docker compose restart` |
| 停止 | `systemctl stop resume-agent resume-agent-worker-resume resume-agent-worker-knowledge` | `docker compose down` |
| 状态 | `systemctl status resume-agent` | `docker compose ps` |
| 健康检查 | `curl http://127.0.0.1:8000/api/health` | 同左 |
| Redis | `redis-cli ping`（应回 `PONG`） | `docker compose exec redis redis-cli ping` |

改密钥/改 `DATABASE_URL`：`sudo vi /opt/ResumeAgent/.env` → `sudo bash deploy/update.sh backend`

备份/回滚索引（Docker 模式请备份 `runtime-data`，它才是挂载生效的数据）：

```bash
sudo cp -r /opt/ResumeAgent/runtime-data /opt/ResumeAgent/runtime-data.bak   # Docker 模式
sudo rm -rf /opt/ResumeAgent/runtime-data && sudo mv /opt/ResumeAgent/runtime-data.bak /opt/ResumeAgent/runtime-data
sudo bash deploy/update.sh data                                              # 回滚并重启
```

---

## 五、故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `docker pull` 超时 `dial tcp ...:443 i/o timeout` | 出站访问 Docker Hub 被阻断，**不是**入站防火墙。配加速器：见下；不行就换 `setup_server_nodocker.sh` |
| `permission denied ... docker.sock` | 当前用户不在 docker 组：`sudo usermod -aG docker ubuntu && newgrp docker`，或命令前加 `sudo` |
| 浏览器打不开 | 99% 是腾讯云防火墙没放行 80；服务器内 `curl http://127.0.0.1:8000/api/health` 能通就说明后端没问题 |
| 502 Bad Gateway | `docker compose logs --tail 100` 或 `journalctl -u resume-agent -n 100` |
| 启动即 `RuntimeError: SECRET_KEY not set` | `.env` 缺 `SECRET_KEY`，补一个随机串后 `sudo bash deploy/update.sh backend` |
| 注册/登录 500，日志报数据库连接 | `DATABASE_URL` 不对或数据库不可达；Docker 模式别写 `localhost`；确认库已建、账号有权限 |
| 迁移报 `relation already exists` / 表不存在 | 库是旧的：先 `alembic stamp head` 对齐，或换空库重跑 `alembic upgrade head` |
| 简历上传后一直不解析 | worker 没起来：`docker compose ps` / `systemctl status resume-agent-worker-resume`；Redis 是否 `PONG` |
| 页面能开但问答为空 | Docker 模式看 `ls /opt/ResumeAgent/runtime-data/databases/vector_dbs \| head`，应有一堆 `.faiss`；systemd 模式看 `/opt/ResumeAgent/data/databases/vector_dbs` |
| 改了 `data/knowledge_base/project_intros` 不生效 | Docker 模式权威数据在 `runtime-data`，要把文档放到 `runtime-data/knowledge_base/project_intros/` |
| `~/resume-agent.tgz` 是新的，但 `/opt/ResumeAgent/deploy` 是旧的 | 上传了没解压（`-Remote` 只上传）。补 `cd /opt/ResumeAgent && sudo tar -xzf ~/resume-agent.tgz`，或以后加 `-Apply` |
| 流式变成一次性返回 | Nginx 缓冲没关，确认用的是 `deploy/nginx.conf` |
| 简历生成中途断 | Nginx `proxy_read_timeout` 被改小了，应为 `600s` |

Docker 加速器（仍想用 Docker 时）：

```bash
sudo tee /etc/docker/daemon.json > /dev/null <<'EOF'
{
  "registry-mirrors": [
    "https://mirror.ccs.tencentyun.com",
    "https://docker.m.daocloud.io",
    "https://hub-mirror.c.163.com"
  ]
}
EOF
sudo systemctl daemon-reload && sudo systemctl restart docker
timeout 120 sudo docker pull node:20-alpine
```

---

## 六、脚本清单

| 文件 | 运行位置 | 作用 |
|---|---|---|
| `deploy/make_release.ps1` | 本地 Windows | 打包（含 `alembic/`；排除 `node_modules`/课程PDF/`.env`）；`-Remote` 上传、`-Apply` 上传后自动解压并跑 `update.sh` |
| `deploy/setup_server.sh` | 服务器 | 首次部署（Docker 模式）：校验 `.env` → 初始化 `runtime-data` → 构建 → `alembic upgrade head` → 启动 → Nginx |
| `deploy/setup_server_nodocker.sh` | 服务器 | 首次部署（venv + systemd，免 Docker）：装 Redis → 装依赖 → 迁移 → 注册 API + 两个 worker → Nginx |
| `deploy/update.sh` | 服务器 | 后续更新，自动识别两种模式；`backend` / `all` 会顺带跑迁移 |
| `deploy/nginx.conf` | 服务器 | 反代：SSE 关缓冲、600s 超时、20MB 上传、可选口令 |

---

## 七、安全提醒

- `.env` **永不进包、永不进镜像、永不进 Git**（`.gitignore` / `.dockerignore` 已排除）
- 不要把 API Key、服务器密码发给任何人（包括贴到聊天里）
- `SECRET_KEY` 决定 JWT 签名，**泄露等于可伪造登录**；改它会使已签发令牌失效，改完跑 `deploy/update.sh backend`
- 项目自带多用户注册登录，任何人都能注册——公网建议保持 `auth_basic` 开启；若不想让人触发知识库重建，可在 Nginx 加：
  ```nginx
  location = /admin { return 404; }
  location = /api/knowledge/build { return 403; }
  ```
- PostgreSQL / Redis 只对内网开放（不要映射 `5432` / `6379` 到公网）
- API 单进程 `--workers 1`，并发上限约 5~10 人；重活由两个 celery worker 承担（resume 并发 2 / knowledge 串行）
