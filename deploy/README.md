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

### 2. 控制台放行端口

腾讯云轻量 → 防火墙 → 添加规则：`HTTP 80`、`HTTPS 443`（来源 `0.0.0.0/0`）。**不做这步后面全白搭。**

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

`ENABLE_AUTH=1` 会让你设一个访问密码（账号 `admin`）。**公网暴露强烈建议开启**——项目本身无鉴权。

访问：`http://124.222.88.64`

---

## 二、日常更新

```powershell
# 本地一条命令打包+上传（改过前端且本地构建过就加 -IncludeFrontendDist）
powershell -ExecutionPolicy Bypass -File deploy\make_release.ps1 -Remote ubuntu@124.222.88.64
```

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

## 三、运维命令

| 操作 | systemd 模式 | Docker 模式 |
|---|---|---|
| 看日志 | `journalctl -u resume-agent -f` | `docker compose logs -f --tail 100` |
| 重启 | `systemctl restart resume-agent` | `docker compose restart` |
| 停止 | `systemctl stop resume-agent` | `docker compose down` |
| 状态 | `systemctl status resume-agent` | `docker compose ps` |
| 健康检查 | `curl http://127.0.0.1:8000/api/health` | 同左 |

改密钥：`sudo vi /opt/ResumeAgent/.env` → `sudo bash deploy/update.sh backend`

备份/回滚索引：

```bash
sudo cp -r /opt/ResumeAgent/data /opt/ResumeAgent/data.bak          # 备份
sudo rm -rf /opt/ResumeAgent/data && sudo mv /opt/ResumeAgent/data.bak /opt/ResumeAgent/data
sudo bash deploy/update.sh data                                      # 回滚
```

---

## 四、故障排查

| 现象 | 原因 / 处理 |
|---|---|
| `docker pull` 超时 `dial tcp ...:443 i/o timeout` | 出站访问 Docker Hub 被阻断，**不是**入站防火墙。配加速器：见下；不行就换 `setup_server_nodocker.sh` |
| `permission denied ... docker.sock` | 当前用户不在 docker 组：`sudo usermod -aG docker ubuntu && newgrp docker`，或命令前加 `sudo` |
| 浏览器打不开 | 99% 是腾讯云防火墙没放行 80；服务器内 `curl http://127.0.0.1:8000/api/health` 能通就说明后端没问题 |
| 502 Bad Gateway | `docker compose logs --tail 100` 或 `journalctl -u resume-agent -n 100` |
| 页面能开但问答为空 | `ls /opt/ResumeAgent/data/databases/vector_dbs \| head`，应有一堆 `.faiss` |
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

## 五、脚本清单

| 文件 | 运行位置 | 作用 |
|---|---|---|
| `deploy/make_release.ps1` | 本地 Windows | 打包（可选自动上传），排除 `node_modules`/课程PDF/`.env` |
| `deploy/setup_server.sh` | 服务器 | 首次部署（Docker 模式） |
| `deploy/setup_server_nodocker.sh` | 服务器 | 首次部署（venv + systemd，免 Docker） |
| `deploy/update.sh` | 服务器 | 后续更新，自动识别两种模式 |
| `deploy/nginx.conf` | 服务器 | 反代：SSE 关缓冲、600s 超时、20MB 上传、可选口令 |

---

## 六、安全提醒

- `.env` **永不进包、永不进镜像、永不进 Git**（`.gitignore` / `.dockerignore` 已排除）
- 不要把 API Key、服务器密码发给任何人（包括贴到聊天里）
- 公网建议保持 `auth_basic` 开启；若不想让人触发知识库重建，可在 Nginx 加：
  ```nginx
  location = /admin { return 404; }
  location = /api/knowledge/build { return 403; }
  ```
- 单进程 `--workers 1`，并发上限约 5~10 人
