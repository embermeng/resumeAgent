# ResumeAgent - 基于 LangGraph 的 RAG 知识库与简历生成 Agent

## 一句话简介

ResumeAgent 是一个面向个人求职场景的智能简历生成 Agent，基于 LangGraph 状态机实现双模式路由（快速问答/深度思考），通过 FAISS + BM25 混合检索从课程 PDF 和项目亮点文档构建的知识库中检索相关知识，简历生成时优先挑选预写好的项目介绍成品文档（无介绍时降级检索项目亮点分块），自动生成针对特定岗位要求的定制化简历。

## 技术栈

LangGraph（Agent 状态机编排）、LangChain（LLM 工具链）、FAISS（Facebook AI Similarity Search，向量数据库）、BM25（Best Matching 25，全文检索）、rank-bm25（BM25 Python 实现）、jieba（中文分词）、MinerU（GPU 加速 PDF 解析）、DashScope（通义千问 API）、tiktoken（Token 计数）、Pydantic v2（数据模型校验）、Streamlit（Web UI）、pytest（单元测试框架）

## 架构设计

ResumeAgent 采用分层模块化架构，数据流为：课程 PDF/项目亮点文档 → 知识处理层 → 索引层 → 检索层 → Agent 层 → UI 层；项目介绍成品文档独立存放、不入库，生成简历时直接遍历挑选。

- **知识处理层**（`src/knowledge/`）：PDFParser（MinerU GPU 解析）、TextSplitter（tiktoken 按 token 数分块）、CourseSummarizer（LLM 提取课程摘要）、HighlightIngestor（项目亮点 README 入库，category=project，支持 `简历优先` 标签解析）、IntroSelector（项目介绍文档扫描与目录构建，生成简历时挑选）、BM25Ingestor + VectorDBIngestor（双通道索引构建）
- **检索层**（`src/retrieval/`）：BM25Retriever（关键词匹配）、VectorRetriever（语义向量检索）、HybridRetriever（加权融合，vector_weight=0.6）、LLMReranker（LLM 重排序，预留模块）
- **Agent 层**（`src/agent/`）：IntentClassifier（LLM 结构化输出意图分类）、ResumeAgent（LangGraph StateGraph 双模式路由：quick_response → 单次检索回答；deep_thinking → 分层检索 + 草稿生成 + 岗位优化）、AgentTools（知识检索、简历生成、简历优化工具集）
- **配置层**（`src/config.py`）：全局路径、模型参数、环境变量统一管理
- **UI 层**：Streamlit Web UI（流式输出 + 简历下载）与 CLI 交互模式

数据流关键路径：
1. 知识入库：PDF → MinerU 解析 → tiktoken 分块 → Embedding 向量化 → FAISS/BM25 双通道索引
2. 意图路由：用户输入 → LLM 意图分类 → 条件边路由到 quick_response/deep_thinking/chitchat
3. 深思路径：课程目录选点 → 定向检索 → 项目介绍挑选（无介绍文档时降级检索项目亮点分块）→ 简历草稿生成 → 按 JD 优化

## 核心功能

- **知识库构建**：PDF 解析→分块→向量化入库，支持 FAISS + BM25 双通道，增量构建只处理新增文档（三级增量判断 + 原子写入）
- **项目介绍文档管理**：简历措辞成品一个项目一份，放入 `project_intros/` 即生效、零构建；生成简历时遍历挑选（≤6 全选、>6 按目录 LLM 挑选、标签项目必选且排前）、全文引用；无介绍文档时自动降级检索项目亮点分块兜底
- **项目亮点入库与优先标签**：项目亮点 README 入库为 `category=project` 分块，`简历优先` 标签解析入元信息，检索项目素材时定向加权入选（标签项目均分名额、轮询交错排前，剩余名额按相关度竞争）
- **双模式路由**：LLM 意图识别自动选择快速问答或深度思考路径，降级策略保证检索兜底
- **混合检索**：BM25 关键词 + 向量语义加权融合，异常时自动降级为纯 BM25
- **分层检索**：深思路径下 LLM 先浏览课程目录选高价值知识点，再定向检索对应课程
- **简历生成与优化**：综合知识检索 + 项目经历，按目标岗位要求自动生成并优化简历
- **流式输出**：四层流式链路（API→Agent→UI），用户秒见进度、逐字输出
- **多模型支持**：通过环境变量切换 DashScope/OpenAI/Gemini 三类 LLM 提供商

## 技术亮点

**知识库增量构建与成本优化**：课程知识库含 28 篇 PDF，每次全量重建需对全部分块调用 Embedding API，新增 1 篇文档也要承担全部 API 成本。设计了以 doc_id 为主键的三级增量判断机制（存在性检查→mtime 过期检测→--force 强制全量），配合 BM25 索引原子写入（`.pkl.tmp` + `os.replace`）防止构建中断损坏索引，以及 `--prune` 清理孤儿索引。新增 1 篇 PDF 的 Embedding API 调用从约 28 次降为 1 次，API 成本降至全量重建的 1/28。

**知识问答失效三重根因排查与修复**：用户反馈问答答案与 PDF 内容不符，排查发现三层级联 bug——(1) API 客户端未检查 HTTP 状态码，403 错误被静默吞掉导致意图识别永远降级到"闲聊"（不检索）；(2) BM25 索引和查询均用 `str.split()` 分词，中文无空格导致整句成为单个 token，检索完全无效；(3) FAISS C++ 层 `fopen` 不支持中文路径，28 个向量索引全部加载失败且无报错。修复方案：API 非 200 立即抛异常、封装统一 jieba 分词器（索引与查询共用）、FAISS 读取侧临时文件兼容层。修复后检索 top1 精确命中目标文档章节，测试从 150 增至 156 全部通过。

**BM25 + 向量混合检索与降级链**：单纯 BM25 在长查询下被高频词稀释排名，单纯向量检索对精确术语匹配不敏感。实现 HybridRetriever 双通道召回加权融合（vector_weight=0.6，分数归一后合并排序），并构建三级降级链：混合检索失败→纯 BM25→返回友好提示，保证问答功能在任何异常下不中断。

**PDF 解析从 Docling 迁移到 MinerU（GPU 加速）**：Docling 解析中文课程 PDF 速度慢且公式/表格还原差。迁移到 MinerU（pipeline backend，GPU 加速），保持 PDFParser 类接口签名不变实现上层零改动，测试同步重写（mock 新解析入口 + conftest 全局 mock 防误触发真实模型），解决 torch cu130 索引、HF 模型缓存目录、子进程环境变量继承等 RTX 50 系显卡适配问题，136 个测试全部通过。

**四层流式输出架构**：Web UI 原先需等待 Agent 完整生成后才一次性展示回答，体验差。设计四层流式链路——API 层三个 provider 统一 `send_message_stream()` 生成器；工具层 `answer_question_stream()` 基于检索上下文流式问答；Agent 层 `run_stream()` yield 结构化事件流（status 阶段进度 / token 文本增量 / done 完整结果）；UI 层 Streamlit 占位符增量渲染，首 token 到达后逐字输出带光标。用户从整块等待改为秒级进度提示 + 逐字输出。

**项目素材管线：亮点入库 + 优先标签加权检索**：简历需要稳定可控的项目素材，而项目源码体量大、直接喂给 LLM 成本高。设计文档级管线：用提示词把每个项目总结成一份亮点 README，HighlightIngestor 按章节分块入库并打 `category=project` 标签（mtime 增量、变更文档自动重建）；文档级标签机制只识别第一个二级标题前的 `> 标签：简历优先` 行，解析入元信息并从正文剥离；检索时标签项目定向均分名额、轮询交错保证入选且排序靠前，其余名额按相关度竞争。端到端验证：23 份亮点文档入库后，检索前 4 条精确命中 4 个标签项目各一块。

**项目介绍文档零构建挑选**：每次生成简历都现场措辞项目经历，耗时长且措辞不稳定。将项目介绍改为离线预写成品：一个项目一份 `.md`（H1 项目名 + 一句话简介 + 亮点），放入目录即生效，不入库不向量化。挑选策略分三层：标签项目必选且排前；文档数 ≤6 时全选，零 LLM 调用；超过时一次 LLM 调用按"项目名+一句话简介"轻量目录挑选（结构化输出、过滤编造项目名、失败自动兜底为标签+顺序选取）。入选文档全文直接进简历生成上下文，项目经历部分省去现场长生成，响应更快、措辞稳定可编辑。

**分层检索策略（目录选点→定向检索）**：深思路径下直接混合检索容易召回大量低相关分块。设计两级检索策略：第一级 LLM 浏览全部课程结构化摘要目录，按"简历含金量"和岗位匹配度挑选高价值知识点；第二级按选中知识点定向检索对应课程（每门课公平分配名额、轮询交错合并），过滤 LLM 编造的不存在 doc_id。任一环节失败自动降级为普通混合检索，保证功能可用。

## 量化成果

- 增量构建：新增 1 篇 PDF 的 Embedding API 成本从 ~28 次调用降为 1 次（成本降至 1/28）
- 测试覆盖：215 个测试用例全部通过，覆盖知识处理、检索、Agent、配置、Schema 等模块
- 知识库规模：52 篇文档级索引（29 篇课程 PDF + 23 篇项目亮点），每篇独立 BM25 + FAISS 双索引
- 流式优化：意图识别关闭 thinking 后首响应从 6s 降至 1.7s；问答关闭 thinking 后首 token 从 11s 降至 0.6s
- 检索缓存：BM25 索引内存缓存避免重复反序列化，实测省约 0.4s/次查询

## 个人职责与贡献

- 独立完成全部系统设计与开发（知识处理、索引构建、检索、Agent 路由、流式 UI）
- 设计增量索引构建机制并实施文档级 mtime 过期检测方案
- 排查并修复知识问答失效的三重根因（API 静默吞错、中文分词失效、FAISS 中文路径）
- 主导 PDF 解析引擎从 Docling 到 MinerU 的迁移（保持接口零改动）
- 实现四层流式输出架构并适配 Streamlit 增量渲染
- 编写 215 个 pytest 测试用例（TDD 驱动开发）

## 检索关键词

ResumeAgent, 简历生成, RAG, 检索增强生成, LangGraph, Agent, FAISS, BM25, 混合检索, hybrid retrieval, 向量检索, 增量索引, incremental indexing, MinerU, PDF解析, 流式输出, streaming, 意图识别, intent classification, 分层检索, 知识库构建, Streamlit, LangChain, Pydantic, 双模式路由, 项目亮点入库, 优先标签加权检索, 项目介绍挑选
