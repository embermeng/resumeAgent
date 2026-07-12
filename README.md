# ResumeAgent - 智能简历生成 Agent

基于 **LangGraph + RAG** 构建的双模式路由智能简历生成助手。

将课程 PDF 和项目源码转化为知识库，通过 Agent 自动识别意图：技术问题快速回答，简历生成深度思考多步推理。

---

## 功能概览

- **知识库构建**：PDF 解析 → 文本分块 → 向量化入库（FAISS + BM25 双通道）
- **项目精华提炼**：用 LLM 从项目源码/文档中提炼技术栈、亮点、贡献等结构化信息
- **智能问答**：基于知识库的技术知识问答（RAG）
- **简历生成**：综合知识库 + 项目精华，自动生成/优化简历
- **双模式路由**：Agent 自动判断意图，选择快速回答或深度思考路径

---

## 项目结构

```
ResumeAgent/
├── main.py                  # CLI 入口（知识库构建命令）
├── app_streamlit.py         # Streamlit Web UI
├── requirements.txt
├── .env.example             # 环境变量示例
│
├── data/
│   ├── knowledge_base/
│   │   ├── course_pdfs/     # ← 放入课程 PDF 文件
│   │   └── project_sources/ # ← 放入项目源码目录
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
│   └── schemas/             # 数据模型
│
├── tests/                   # 132 个测试用例
└── docs/                    # 设计文档与报告
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
DEFAULT_LLM_MODEL=qwen-turbo-latest
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

#### 3.2 放入项目源码（可选）

将你的项目源码目录放到 `data/knowledge_base/project_sources/`，每个项目一个子目录：

```
data/knowledge_base/project_sources/
├── my-rag-project/
│   ├── README.md
│   ├── requirements.txt
│   ├── src/
│   └── ...
├── my-web-app/
│   ├── README.md
│   ├── package.json
│   └── ...
└── ...
```

> 每个项目目录中建议包含 `README.md` 和依赖文件（如 `requirements.txt`），Agent 会优先读取这些文件来提炼项目信息。

---

## 构建知识库

### 方式一：一键构建（推荐）

```bash
python main.py build-all
```

这条命令会自动完成以下全部步骤：
1. 解析 PDF → Markdown
2. 文本分块（按 token 数切分）
3. 构建 FAISS 向量索引 + BM25 索引
4. 用 LLM 提炼项目精华

### 方式二：分步执行

如果你想更精细地控制每一步：

```bash
# 第 1 步：解析 PDF 为 Markdown
python main.py parse-pdfs

# 第 2 步：文本分块
python main.py split-chunks
python main.py split-chunks --chunk-size 500 --chunk-overlap 100  # 自定义参数

# 第 3 步：构建向量索引和 BM25 索引
python main.py build-indexes

# 第 4 步：提炼项目精华（需要已放入项目源码）
python main.py extract-projects
```

### 构建完成后

构建完成后，数据会存储在以下目录：

```
data/processed/
├── course_chunks/       # 课程知识分块（JSON）
└── project_extracts/    # 项目精华提炼结果（JSON）

data/databases/
├── vector_dbs/          # FAISS 向量索引
└── bm25_dbs/            # BM25 全文检索索引
```

> **提示**：`data/processed/project_extracts/` 中的项目精华 JSON 可以手动编辑修改，确保简历中展示的项目信息准确。

---

## 使用 Agent 生成简历

知识库构建完成后，就可以开始使用 Agent 了。有两种方式：

### 方式一：Streamlit Web UI（推荐）

```bash
streamlit run app_streamlit.py
```

浏览器会自动打开 `http://localhost:8501`，在聊天框中直接输入即可。

**使用示例：**

| 你说的话 | Agent 行为 |
|---------|-----------|
| "RAG 的核心流程是什么？" | ⚡ 快速回答模式 → 从知识库检索并回答 |
| "Faiss 和 Milvus 有什么区别？" | ⚡ 快速回答模式 → 知识对比回答 |
| "我这个 RAG 项目用了什么技术栈？" | ⚡ 快速回答模式 → 查询项目精华 |
| "帮我生成一份简历" | 🧠 深度思考模式 → 检索知识 + 项目 → 生成简历 |
| "帮我生成一份 AI 工程师方向的简历，岗位要求如下..." | 🧠 深度思考模式 → 针对性生成 + 优化 |

生成简历后，页面会出现 **下载按钮**，可以下载 Markdown 格式的简历文件。

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
| 通义千问 (DashScope) | `dashscope` | qwen-turbo-latest, qwen-plus |
| OpenAI | `openai` | gpt-4o, gpt-4o-mini |
| Gemini | `gemini` | gemini-pro |

---

## 运行测试

```bash
# 运行全部测试
pytest tests/ -v

# 运行某个模块的测试
pytest tests/test_agent/ -v
pytest tests/test_knowledge/ -v
```

---

## 常见问题

**Q: PDF 解析失败怎么办？**

项目使用 Docling 解析 PDF。如果某些 PDF 格式特殊导致解析失败，可以尝试：
- 确认 PDF 文件不是扫描件（扫描件需要先 OCR）
- 检查 PDF 是否加密

**Q: 项目精华提炼结果不准确？**

提炼完成后，打开 `data/processed/project_extracts/` 目录，手动编辑 JSON 文件即可。Agent 在生成简历时会读取这些 JSON 作为项目信息。

**Q: 如何更新知识库？**

重新运行 `python main.py build-all` 即可。已有的索引会被覆盖。

**Q: 支持哪些知识来源？**

目前支持：
- 课程 PDF（`data/knowledge_base/course_pdfs/`）
- 项目源码（`data/knowledge_base/project_sources/`）
- 面试题资料（`data/knowledge_base/interview_qa/`，后续扩展）

---

## 技术栈

- **Agent 编排**：LangGraph
- **LLM 工具链**：LangChain
- **向量数据库**：FAISS
- **全文检索**：BM25 (rank-bm25)
- **PDF 解析**：Docling
- **Token 计数**：tiktoken
- **数据模型**：Pydantic v2
- **Web UI**：Streamlit
- **测试**：pytest（132 个测试用例）

---

## 相关文档

- [项目设计计划](docs/项目计划.md) - SDD + TDD 完整实施计划
- [完成报告](docs/完成报告.md) - 项目完成情况总结
# resumeAgent