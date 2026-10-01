# ResumeAgent - 智能简历生成 Agent

基于 **LangGraph + RAG** 构建的双模式路由智能简历生成助手。

将课程 PDF 和项目亮点文档转化为知识库，通过 Agent 自动识别意图：技术问题快速回答，简历生成深度思考多步推理。

---

## 功能概览

- **知识库构建**：PDF 解析 → 文本分块 → 向量化入库（FAISS + BM25 双通道）
- **项目介绍文档管理**：用户离线生成的简历措辞成品直接放入目录，生成简历时遍历挑选、全文引用（主路径，不入库不向量化）
- **项目亮点文档入库**：项目亮点 README 入库打上 `project` 类别标签，供项目知识问答与素材兜底
- **智能问答**：基于知识库的技术知识问答（RAG）
- **简历生成**：综合课程知识分块 + 项目介绍成品（无介绍文档时降级用亮点分块），自动生成/优化简历
- **基于已有简历生成**：Web 侧边栏上传已有简历（Word/PDF/md/txt，按类型分流解析），生成时沿用其章节结构与字段顺序，保留其中真实事实（个人信息/教育/工作经历）并用知识库素材增强措辞
- **双模式路由**：Agent 自动判断意图，选择快速回答或深度思考路径

---

## 项目结构

```
ResumeAgent/
├── main.py                  # CLI 入口（知识库构建命令）
├── app_streamlit.py         # Streamlit Web UI（保留）
├── app_api.py               # FastAPI 后端入口（前后端分离）
├── requirements.txt
├── .env.example             # 环境变量示例
│
├── data/
│   ├── knowledge_base/
│   │   ├── course_pdfs/        # ← 放入课程 PDF 文件
│   │   ├── project_intros/     # ← 放入项目介绍文档（.md，简历主路径）
│   │   └── project_highlights/ # ← 放入项目亮点文档（.md，问答/兜底）
│   ├── processed/           # 处理后的中间数据（自动生成）
│   └── databases/           # 向量索引和 BM25 索引（自动生成）
│
├── src/
│   ├── config.py            # 全局配置
│   ├── api_client.py        # 多模型 LLM API 封装
│   ├── knowledge/           # 知识处理模块
│   ├── retrieval/           # 检索模块
│   ├── agent/               # LangGraph Agent 模块
│   ├── prompts/             # 提示词模板
│   ├── schemas/             # 数据模型
│   └── api/                 # FastAPI 后端（app/routers/services/task_manager/sse）
│
├── frontend/                # Vue3 + Vite + TS 前端（Pinia/Element Plus/Vitest）
├── tests/                   # 后端测试：329 passed（含 tests/test_api/）
└── docs/                    # 设计文档、报告与 specs/api-contract.md（前后端契约）
```

---

## 快速开始

### 1. 安装依赖

```bash
cd ResumeAgent
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，填入你的 API Key：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```ini
# 选择 LLM 提供商：dashscope / openai / gemini
DEFAULT_LLM_PROVIDER=dashscope
DEFAULT_LLM_MODEL=qwen3.8-max
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx

# 如果用 OpenAI
# DEFAULT_LLM_PROVIDER=openai
# DEFAULT_LLM_MODEL=gpt-4o
# OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxx

# Embedding 模型（用于向量化）
EMBEDDING_PROVIDER=dashscope
EMBEDDING_MODEL=text-embedding-v1
```

### 3. 准备数据

#### 3.1 放入课程 PDF

将你的课程 PDF 文件复制到 `data/knowledge_base/course_pdfs/` 目录：

```
data/knowledge_base/course_pdfs/
├── RAG技术与应用.pdf
├── LangGraph实战.pdf
├── Python高级编程.pdf
└── ...
```

#### 3.2 放入项目介绍文档（简历主路径，推荐）

项目介绍是**简历措辞成品**：你用提示词+大模型离线生成，一个项目一份，**放入目录即生效，无需任何构建命令**：

1. 用 [`src/prompts/生成项目介绍.md`](src/prompts/生成项目介绍.md) 中的提示词，把项目材料（亮点文档/源码说明）改写成简历项目介绍（需有 `# 项目名` 一级标题和 `## 一句话简介` 章节）
2. 将 `.md` 放入 `data/knowledge_base/project_intros/`
3. （可选）必上简历的项目在一级标题后加一行 `> 标签：简历优先`
4. 直接去生成简历，无需入库/建索引（改文档后同样即时生效）

生成简历时深思路径会遍历该目录：文档数 **≤6 份时全部使用**；超过时 LLM 根据“项目名+一句话简介”轻量目录按诉求/岗位挑选（标签项目必选且排前，挑选失败自动兜底）。入选文档全文直接进简历生成上下文，项目经历部分无需现场措辞，更快且措辞稳定。

#### 3.3 放入项目亮点文档（知识问答与兜底）

**用提示词把每个项目总结成一份亮点文档再入库**（内容可控、无 API 成本）：

1. 用 [`src/prompts/总结项目亮点.md`](src/prompts/总结项目亮点.md) 中的提示词，让 AI 把每个项目总结成一份亮点 README（含技术栈、亮点、量化成果等固定章节）
2. 将生成的 `.md` 文件放入 `data/knowledge_base/project_highlights/`（一个项目一份，文档需有 `# 项目名` 一级标题）
3. （可选）给重点项目打标签：在一级标题后加一行 `> 标签：简历优先`（多个标签用、/,分隔）
4. 运行 `python main.py ingest-highlights` 入库，再 `build-indexes` 建索引（或直接 `build-all`）

入库时文档会按章节分块并打上 `category="project"` 标签，与课程知识区分开；用于项目相关的快速问答（如“我这个项目用了什么技术栈”），并在未放项目介绍文档时作为简历生成的兜底素材。后续也支持网页上传到该目录，入库流程不变。

**优先标签的作用**：打了 `简历优先` 标签的项目在检索项目素材时被定向保证入选（每个标签项目至少一块、轮询交错排序靠前），剩余名额才由其余项目按相关度竞争。想让某个项目上简历，给它加这行标签再重跑入库即可；改标签后无需重建全部索引，只有变更文档会增量重建。项目介绍文档（3.2）沿用同一标签约定（必选项目）。

---

## 构建知识库

### 方式一：一键构建（推荐）

```bash
python main.py build-all
```

这条命令会自动完成以下全部步骤：
1. 解析 PDF → Markdown
2. 提取课程摘要目录（分层检索用）
3. 文本分块（按 token 数切分）
4. 项目亮点文档入库（`project_highlights/` 下的 md，打 `project` 标签）
5. 构建 FAISS 向量索引 + BM25 索引（课程与项目亮点合并建索引）

### 方式二：分步执行

如果你想更精细地控制每一步：

```bash
# 第 1 步：解析 PDF 为 Markdown
python main.py parse-pdfs

# 第 2 步：文本分块
python main.py split-chunks
python main.py split-chunks --chunk-size 500 --chunk-overlap 100  # 自定义参数

# 第 3 步：项目亮点文档入库（需已放入 project_highlights/）
python main.py ingest-highlights

# 第 4 步：构建向量索引和 BM25 索引
python main.py build-indexes
```

### 增量构建机制

以上各环节（解析、分块、亮点入库、索引构建）默认都是**增量执行**：

- 已解析/已分块/已建索引的文档会自动跳过，只处理新增文档
- 源文档更新过（如用 `--force` 重新解析）时，下游分块和索引会自动感知并重建
- 新增 PDF 后直接运行 `python main.py build-all`，只有新文档会调 embedding API，不会重复花钱

常用可选参数：

```bash
# 强制全量重新执行（解析/分块/建索引各命令均支持）
python main.py build-all --force
python main.py parse-pdfs --force
python main.py split-chunks --force
python main.py build-indexes --force

# 删除或改名了 PDF 后，清理残留的孤儿索引
python main.py build-indexes --prune
python main.py build-all --prune
```

### 构建完成后

构建完成后，数据会存储在以下目录：

```
data/processed/
└── course_chunks/       # 知识分块（课程 + 项目亮点，按 metainfo.category 区分）

data/databases/
├── vector_dbs/          # FAISS 向量索引（项目文档的 doc_id 带 project- 前缀）
└── bm25_dbs/            # BM25 全文检索索引
```

> **提示**：简历生成使用的项目素材是 `course_chunks/` 中 `category="project"` 的亮点分块（由 `project_highlights/` 的 md 入库而来），可随时编辑后重跑 `ingest-highlights` + `build-indexes` 增量生效。

---

## 使用 Agent 生成简历

知识库构建完成后，就可以开始使用 Agent 了。有两种方式：

### 方式一：Streamlit Web UI（推荐）

```bash
streamlit run app_streamlit.py
```

浏览器会自动打开 `http://localhost:8501`，在聊天框中直接输入即可。

**基于已有简历生成**（可选）：在侧边栏“📎 我的已有简历”上传 `.docx/.pdf/.md/.txt` 文件（Word 用 python-docx 提取段落表格，手工排版的加粗章节行也会识别为标题；PDF 复用 MinerU 解析），上传后解析一次并缓存，可预览/移除；之后说“生成简历”时，Agent 会以它为事实骨架，**沿用它的章节结构与章节内字段格式输出**（如“时间/公司/职位/工作内容”“技术栈/项目描述/责任描述”，不套用默认模板），保留个人信息、教育背景、工作经历等真实内容，再用知识库素材增强项目经历与技能措辞。

回答采用**流式输出**（SSE效果）：先显示阶段进度（意图识别 → 知识库检索 → 生成回答），然后逐字展示答案，无需等待全部生成完毕。

**使用示例：**

| 你说的话 | Agent 行为 |
|---------|-----------|
| "RAG 的核心流程是什么？" | ⚡ 快速回答模式 → 从知识库检索并回答 |
| "Faiss 和 Milvus 有什么区别？" | ⚡ 快速回答模式 → 知识对比回答 |
| "我这个 RAG 项目用了什么技术栈？" | ⚡ 快速回答模式 → 检索项目亮点分块 |
| "帮我生成一份简历" | 🧠 深度思考模式 → 检索知识 + 项目 → 生成简历 |
| "帮我生成一份 AI 工程师方向的简历，岗位要求如下..." | 🧠 深度思考模式 → 针对性生成 + 优化 |

生成简历后，页面会出现 **下载按钮**（Markdown 格式）。

### 方式二：CLI 交互模式

```bash
python main.py chat
```

进入交互终端后，直接输入问题：

```
==================================================
ResumeAgent CLI - 输入问题，输入 'quit' 退出
==================================================

You: RAG的核心流程是什么？

[quick_response]
RAG的核心流程包括以下几个步骤：
1. 文档解析：将PDF等格式转为文本...
2. 文本分块：按语义或token数切分...
3. 向量化：使用Embedding模型...
4. 检索：根据用户问题匹配相关文本块...
5. 生成：将检索到的上下文交给LLM生成回答...

You: 帮我生成一份简历

[deep_thinking]
# 个人简历
## 个人简介
...
## 技能栈
...
## 项目经历
...

You: quit
Bye!
```

---

## 前后端分离 Web 应用（FastAPI + Vue3）

除 Streamlit 与 CLI 外，项目提供**前后端分离**的现代 Web 应用：后端用 FastAPI 封装 Agent 流式能力与知识库构建任务，前端用 Vue3 全新实现。单用户本地/演示、无鉴权、全局单 Agent 实例。接口契约见 [docs/specs/api-contract.md](docs/specs/api-contract.md)（SDD 唯一真理来源）。

### 架构

- **后端**（`app_api.py` + `src/api/`）：FastAPI 应用工厂，复用 `src.*` 全部逻辑，不改动 Agent。
  - `POST /api/chat`（SSE）：封装 `ResumeAgent.run_stream()`，逐帧下发 `status/intent/token/done/error`。
  - `POST /api/resume/parse`、`GET /api/resume/supported-extensions`：简历文件上传解析。
  - `POST /api/knowledge/build` + `GET /api/knowledge/tasks/{id}/stream`（SSE）：后台线程执行知识库构建，实时推送进度。
  - `GET /api/health`：健康检查。
- **前端**（`frontend/`）：Vue3 + Vite + TypeScript + Pinia + Vue Router + Element Plus；用 `fetch + ReadableStream` 消费 SSE，`markdown-it + highlight.js + DOMPurify` 安全渲染。

### 启动后端

```bash
cd ResumeAgent
pip install -r requirements.txt          # 含 fastapi / uvicorn[standard] / python-multipart
uvicorn app_api:app --reload --port 8000
```

后端就绪后：`http://localhost:8000/docs` 查看自动生成的 OpenAPI 文档，`http://localhost:8000/api/health` 做健康检查。

> **Windows 注意**：`GET /api/knowledge/tasks` 与 `GET /api/knowledge/tasks/{id}` 是**异步路由**（psycopg3 异步引擎）。Windows 上 uvicorn 默认使用 ProactorEventLoop，不支持异步 DB 驱动依赖的 selector 回调，直接在宿主跑这两个接口会报错。Windows 开发者请改用下方的「用 Docker 运行后端」小节；Linux/macOS 默认 Selector 循环，无此问题。

### 启动前端

```bash
cd frontend
npm install
npm run dev                              # http://localhost:5173，/api 已代理到 :8000
```

打开 `http://localhost:5173`：

- **智能对话**（`/`）：流式问答、上传已有简历、深思路径生成简历并预览/下载 Markdown。
- **知识库管理**（`/admin`）：一键触发 `build-all` 等构建任务，SSE 实时进度条与日志。

> 前端 dev server 通过 Vite proxy 将 `/api` 转发到后端 `:8000`，无需额外配置跨域。

### 前端构建产物

```bash
cd frontend
npm run build                            # vue-tsc 类型检查 + vite 打包到 dist/
npm run preview                          # 本地预览生产包
```

---

## 用 Docker 运行后端（Windows 异步开发推荐）

后端的 `task_status` / `task_list` 是异步路由，依赖 psycopg3 异步引擎。**Windows 上 uvicorn 默认的 ProactorEventLoop 不支持异步 DB 驱动**，直接在宿主跑这两个接口会崩。最省事的解法是把后端放进 **Linux 容器**运行——容器默认 Selector(epoll) 循环，异步 SQLAlchemy 原样跑通，且与生产环境一致。

项目为此提供了独立的开发编排（不影响生产用的 `Dockerfile` / `docker-compose.yml`）：

- `Dockerfile.dev`：精简后端镜像（`python:3.11-slim` + `requirements-prod.txt`，不含前端构建 / MinerU / torch），源码靠挂载、默认开 `--reload`。
- `docker-compose.dev.yml`：`app`（后端）+ `db`（`postgres:16-alpine`）两服务，同一内部网络。

### 前置条件

- 已安装并**启动 Docker Desktop**（`docker version` 能连上 daemon）。
- 宿主上直接跑的 uvicorn 已停止（容器要发布 `127.0.0.1:8000`，否则抢端口）。

### 启动

```bash
# 1. 构建并启动 app + db 两个容器（首次会拉镜像、装依赖，稍慢）
docker compose -f docker-compose.dev.yml up --build

# 2. 另开一个终端：容器内是全新空库，先跑迁移建表
docker compose -f docker-compose.dev.yml exec app alembic upgrade head

# 3. 验证异步读路由（空库返回 {"tasks":[],"total":0} 即正常）
curl "http://127.0.0.1:8000/api/knowledge/tasks?page=1&page_size=10"
```

前端**无需改动**：照旧 `cd frontend; npm run dev`，Vite proxy 把 `/api` 转发到 `127.0.0.1:8000`，此时指向的就是容器里的后端。改 `src/` 下的后端代码，`--reload` 会自动重载。

### 关键说明

- **数据库是容器内独立的 PostgreSQL，不是你本机装的那个**：数据存在命名卷 `pgdata_dev`，宿主端口映射为 `127.0.0.1:5433`（避开本机 5432），两者可同时运行、互不干扰。app 通过服务名 `db` 连接（compose 的 `environment.DATABASE_URL` 覆盖了 `.env` 里的 `localhost`——容器内 `localhost` 指容器自己，连不到宿主）。
- **全新空库**：`down -v` 会清空数据卷，重新 `up` 后需再 `alembic upgrade head` 建表。
- **停止 / 清理**：
  ```bash
  docker compose -f docker-compose.dev.yml down        # 停止，保留数据卷
  docker compose -f docker-compose.dev.yml down -v     # 停止并删除数据卷（清空开发库）
  ```
- **PowerShell 假报错**：`alembic` / `docker` 的正常 INFO 日志走 stderr，PowerShell 会判为错误并让 `ExitCode=1`，属假报错，以日志内容为准。

> 生产部署（单容器同域 + Nginx + HTTPS，用 `Dockerfile` / `docker-compose.yml`）见 [deploy/README.md](deploy/README.md) 与 [docs/部署方案.md](docs/部署方案.md)。

---

## Agent 工作原理

Agent 通过 **意图识别** 自动选择处理路径：

```
用户输入
   │
   ▼
意图识别（LLM 结构化输出）
   │
   ├── quick_response（快速回答）
   │   └── 单次检索 → 直接回答
   │
   ├── deep_thinking（深度思考）
   │   └── 多步检索 → 简历草稿 → 优化润色 → 最终简历
   │
   └── chitchat（闲聊兜底）
       └── 以 Agent 角色回复，引导用户提出需求
```

- **快速回答**：适合技术问答、知识查询、项目信息查询
- **深度思考**：适合简历生成、简历优化等需要多步推理的任务
- **闲聊兜底**：无法识别意图时，引导用户提出具体需求

---

## 支持的 LLM 模型

通过 `.env` 配置切换：

| 提供商 | 配置值 | 示例模型 |
|--------|--------|---------|
| 通义千问 (DashScope) | `dashscope` | qwen3.8-max, qwen3-max, qwen-plus |
| OpenAI | `openai` | gpt-4o, gpt-4o-mini |
| Gemini | `gemini` | gemini-pro |

> 注：`dashscope` 提供商内部使用 OpenAI 兼容端点（compatible-mode）调用，纯文本与多模态模型（如 qwen3.8-max）均可正常使用。

---

## 运行测试

**后端（pytest，329 passed）：**

```bash
# 运行全部后端测试
pytest tests/ -v

# 按模块运行
pytest tests/test_agent/ -v
pytest tests/test_api/ -v          # FastAPI 路由/服务/SSE/任务管理
```

**前端（Vitest，83 passed）：**

```bash
cd frontend
npm run test                       # vitest run（composables/api/stores/组件/视图）
npm run build                      # 附带 vue-tsc --noEmit 类型检查
```

---

## 常见问题

**Q: PDF 解析失败怎么办？**

项目使用 MinerU 解析 PDF（GPU 加速）。如果某些 PDF 格式特殊导致解析失败，可以尝试：
- 确认 PDF 文件不是扫描件（扫描件需启用 OCR）
- 检查 PDF 是否加密

**Q: 项目经历素材不准确？**

简历生成的项目素材优先来自 `data/knowledge_base/project_intros/` 下的项目介绍成品，直接编辑对应 `.md` 即时生效（无需任何构建命令）。若未放介绍文档，则降级用 `project_highlights/` 亮点文档的分块（编辑后重跑 `ingest-highlights` + `build-indexes` 增量生效）。

**Q: 如何更新知识库？**

放入新 PDF 或新项目亮点文档后重新运行 `python main.py build-all` 即可。各环节默认增量执行，只处理新增/更新过的文档；如需全量重建加 `--force`，删除过文档后加 `--prune` 清理孤儿索引。注意：删除 `project_highlights/` 里的文档后，需手动删除 `course_chunks/` 中对应的 `project-*.json` 再 `--prune`。

**Q: 支持哪些知识来源？**

目前支持：
- 课程 PDF（`data/knowledge_base/course_pdfs/`）
- 项目介绍文档（`data/knowledge_base/project_intros/`，简历主路径，放入即用）
- 项目亮点文档（`data/knowledge_base/project_highlights/`，知识问答与兜底，用提示词生成）
- 面试题资料（`data/knowledge_base/interview_qa/`，后续扩展）

---

## 技术栈

- **Agent 编排**：LangGraph
- **LLM 工具链**：LangChain
- **向量数据库**：FAISS
- **全文检索**：BM25 (rank-bm25)
- **PDF 解析**：MinerU
- **Word 解析**：python-docx（上传已有简历的 docx 解析）
- **Token 计数**：tiktoken
- **数据模型**：Pydantic v2
- **Web UI**：Streamlit（保留）+ 前后端分离 Web 应用（FastAPI + Vue3）
- **后端 API**：FastAPI + Uvicorn（SSE 流式、后台线程任务）
- **前端**：Vue3 + Vite + TypeScript + Pinia + Vue Router + Element Plus
- **Markdown 渲染**：markdown-it + highlight.js + DOMPurify
- **测试**：pytest（后端 329）+ Vitest / @vue/test-utils（前端 83）

---

## 相关文档

- [云端部署方案](docs/部署方案.md) - 单容器同域部署（Docker + Nginx + HTTPS）与备选方案
- [项目设计计划](docs/项目计划.md) - SDD + TDD 完整实施计划
- [完成报告](docs/完成报告.md) - 项目完成情况总结
# resumeAgent