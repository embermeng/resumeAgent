# AI 应用全栈转型学习路线

> **定位**：AI 应用全栈工程师 —— Python 做 Agent 编排与 RAG，TS 做流式交互层，两端自己打通。
> **唯一载体**：ResumeAgent 项目生产化改造，不开任何玩具项目。
> **预期周期**：约 16 周（4 个月）。投入占比：后端主线 60% ／ 前端副线 30% ／ 工程与 Linux 基线 10%。
> **分工原则**：后端落地代码一律先自己动手写，AI 只做 review 与答疑；「AI 写完我再读一遍」不算落地。

## 路线总览

```
阶段 0 盘点自检 + 代码接管 (W1-2)
  → 阶段 1 数据层 (W2-6)
  → 阶段 2 认证与异步 (W6-10)
  → 阶段 3 工程闭环 (W10-14)
  → 阶段 4 收尾与叙事 (W14-16)

并行线：前端 AI 交互层五项 (W2-14，每周 3-4h)
```

## 阶段 0｜第 1~2 周：盘点、自检与代码接管

- [x] 完成 Linux 七条自检（见附录 A），产出缺口清单
- [x] 优先补两条：systemd unit 手写、journalctl 查日志
- [x] 其余缺口改为问题驱动补漏（部署踩坑时定向补，不系统学课程）

### 代码接管（全路线前置条件）

后端此前由 AI 生成，主线开始前必须先把它变成「自己能负责的代码」。补的不是 FastAPI 教程，而是这套 codebase 的 ownership；框架知识（Depends/StreamingResponse/Pydantic 等）在读代码时按需查官方文档单点补，不系统过教程。

方法：

- [x] 追链路画图：`POST /api/chat` 从入口到出口走一遍（app_api → routers/chat → deps → agent_service.run_stream → agent/graph → retrieval → sse 出帧），再追一条知识库入库链路
- [x] 逐模块一句话：说清 src/ 各目录职责（api / agent / knowledge / retrieval / schemas / prompts）
- [x] 用测试当文档：读 tests/test_api 与 tests/test_agent，搞懂每组测试在防什么；改一行代码让测试变红，确认那行代码的作用
- [x] 手写重写：不看原文重写一个小模块（sse.py 体量合适），或无 AI 辅助给现有模块加一个小接口

验收：

- [x] 能画出 POST /api/chat 完整调用链图（router 到 SSE 帧输出）
- [x] 随机抽 10 个 pytest，能说出各自在防什么
- [x] 独立（不用 AI）加一个小功能且 329 个测试全绿

## 阶段 1｜第 2~6 周：数据层

### 学习

- [x] PostgreSQL：SQL → 索引（B+ 树、失效条件）→ 事务与隔离级别
- [x] SQLAlchemy 2.0：session 生命周期、懒加载陷阱、N+1 问题
- [x] Alembic：schema 迁移管理（SQLAlchemy 的生产化伴侣）
- [x] Redis：旁路缓存、穿透/雪崩/过期策略

### 落地

- [ ] 对话记录与任务状态从文件系统迁入 PostgreSQL（conversations / messages / build_tasks）
- [ ] 检索结果加 Redis 缓存层

### 验收

- [x] 给一条慢 SQL 能说出为什么慢、索引怎么加
- [ ] 能画出缓存与 DB 的读写时序

## 阶段 2｜第 6~10 周：认证与异步

### 学习

- [x] JWT 全链路：access/refresh 轮换、前端 token 存储安全、XSS/CSRF
- [x] asyncio：事件循环、信号量并发控制
- [ ] 后台任务：BackgroundTasks → Celery 的设计思想

### 落地

- [ ] 用户系统：注册/登录/对话归属
- [ ] MinerU 解析改为正式后台任务 + 状态查询接口

### 验收

- [x] 注册 → 登录 → 带 token 对话 → token 过期自动刷新的完整闭环跑通
- [ ] 并发多个解析任务服务不垮

## 阶段 3｜第 10~14 周：工程闭环

### 学习

- [ ] Docker 多阶段构建与镜像瘦身
- [ ] GitHub Actions 流水线
- [ ] HTTPS/域名/Nginx 生产配置
- [ ] 结构化日志 + 错误上报（Prometheus 入门可选）

### 落地

- [ ] 一键部署流水线：push → 跑测试（329+83）→ 构建镜像 → 部署

### 验收

- [ ] push 后自动上线
- [ ] 线上出问题能用 journalctl / docker logs 定位

## 并行线｜第 2~14 周：前端 AI 交互层（每周 3~4h）

按序落地五项：

- [ ] 1. 稳定块分段渲染（解决长输出掉帧）
- [ ] 2. 工具调用时间线（后端事件流已就绪）
- [ ] 3. RAG 引用卡片
- [ ] 4. 断线重连 + 已生成内容保全
- [ ] 5. TTFT 前端埋点，与后端耗时统计打通

研读参考：Vercel AI SDK 的流协议设计与 `useChat` 状态机，移植为 Vue composable（不迁移 Next.js/React）。

### 验收

- [ ] 长输出无可见掉帧
- [ ] Agent 检索过程可视化
- [ ] 引用可点击溯源

## 阶段 4｜第 14~16 周：收尾与叙事

- [ ] 简历按「背景—方案—结果」重写，每条量化（如首 token 11s→0.6s）
- [ ] 整理各模块面试高频题清单（索引失效、隔离级别、缓存雪崩、JWT 轮换、SSE 重连……）
- [ ] 产出：生产级项目 + 简历 + 题库

## 不做清单

- Node.js 后端、Next.js/React 迁移
- AWS 五件套（Route53/VPC/SES/EC2/S3 深入）、Ansible、Terraform
- 系统化 Linux 课程（改为问题驱动补漏）
- 系统过 FastAPI/Python 教程（改为接管代码时按需查单点）
- 任何不落在 ResumeAgent 上的新项目

## 节奏与纠偏机制

- 每周投入：主线 10~15h ／ 副线 5~8h ／ 基线踩坑时补
- **每两周复盘，只问一个问题：这个阶段有什么落进了项目？** 答案是「只看了教程」就放慢速度先落地，再继续往下走
- roadmap.sh：裁剪后（划掉 AWS/Ansible/Terraform）只当打勾清单，不当路线

## 附录 A：Linux 自检七条

能答上来 → 放心划掉；答不上来 → 只补那一条：

1. 服务突然 CPU 打满或僵死，排查命令序列是什么？（top/htop → ps → kill 的信号区别）

2. 端口被占用怎么定位和释放？（`ss -tlnp` 或 `lsof -i`）

3. ~~Docker 容器内服务日志、宿主机 systemd 服务日志分别怎么查？（`docker logs` vs `journalctl -u`）~~

   docker logs --tail 200 resume-agent; journalctl -u resume-agent -f

4. ~~把 Python 服务注册成开机自启、崩溃自拉的 systemd unit，能手写吗？~~

   1) copy一个模板 服务名.service 放到/etc/systemd/system/下
   2) 让 systemd 重新扫描 unit 目录（每次改文件都要做）：sudo systemctl daemon-reload
   3) 开机自启 + 立刻启动：sudo systemctl enable --now resume-agent
   4) 看状态：sudo systemctl status resume-agent
   5) 跟日志确认没错：sudo journalctl -u resume-agent -f

5. 磁盘「满了」但 `df -h` 显示还有空间，是什么原因？（inode 耗尽，`df -i`）

6. ~~`chmod 755` 三个数字分别管谁？为什么脚本忘加执行位会报 Permission denied？~~

   分别是管理员、用户组、一般用户的权限

7. `.bashrc` 里加的环境变量，为什么 systemd 启动的服务读不到？

优先补第 3、4 条（生产化部署必用），其余可问题驱动再补。

## 附录 B：已有资产盘点

- **后端**：FastAPI + LangGraph + RAG（FAISS + BM25 混合检索）、SSE 流式、329 个 pytest
- **前端**：Vue3 + TS、手写 SSE 解析器（半帧缓冲 / UTF-8 多字节防截断 / abort 中断）、83 个 vitest
- **部署**：docker-compose + nginx.conf + setup_server.sh，有云服务器部署经验
- **性能故事**：首 token 11s → 0.6s
