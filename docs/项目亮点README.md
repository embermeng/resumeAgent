# ResumeAgent - 基于 LangGraph 的 RAG 简历生成 Agent（全栈生产化）

## 一句话简介

ResumeAgent 是一个面向个人求职场景的智能简历生成 Agent，基于 LangGraph 状态机实现双模式路由（快速问答/深度思考），通过 FAISS + BM25 混合检索从课程 PDF 和项目亮点文档构建的知识库中检索相关知识，简历生成时优先挑选预写好的项目介绍成品文档（无介绍时降级检索项目亮点分块），自动生成针对特定岗位要求的定制化简历，支持基于上传的已有简历增强生成；在此之上完成了全栈生产化改造：FastAPI + Vue3 前后端分离、JWT 双令牌用户系统与资源归属、PostgreSQL 持久化 + Alembic 迁移、Redis 旁路检索缓存、Celery 双队列后台任务（简历解析/知识库构建）、全链路 SSE 流式与断线重连、Docker Compose 多服务编排与 Nginx/systemd 双模式部署，同时保留 Streamlit 与 CLI 交互。近期又补齐了多用户素材隔离（项目文档按用户可见范围检索、进程级检索快照按版本懒刷新）与简历素材管线（素材章节按优先级拼接并限 token 预算，替代原先的单节全文引用）。

## 技术栈

- **AI/RAG**：LangGraph（Agent 状态机编排）、LangChain（LLM 工具链）、FAISS（向量检索）、rank-bm25（全文检索）、jieba（中文分词）、MinerU（GPU 加速 PDF 解析 + 官方轻量 API 远程解析）、DashScope/OpenAI/Gemini（多 LLM 提供商）、tiktoken（Token 计数）
- **后端**：FastAPI + Uvicorn（异步 Web 框架）、Pydantic v2（数据校验）、Celery（分布式任务队列）、SQLAlchemy 2.0（ORM，同步/异步双引擎）、Alembic（schema 迁移）、python-jose/passlib（JWT 与密码哈希）、pytest（608 个测试）
- **前端**：Vue 3 + TypeScript + Vite、Pinia（状态管理）、Element Plus（组件库）、markdown-it + highlight.js + DOMPurify（Markdown 安全渲染）、手写 SSE 解析器（半帧缓冲/UTF-8 多字节防截断/abort 中断）、Vitest（83 个测试）
- **数据与基础设施**：PostgreSQL 16（对话/任务/用户持久化）、Redis 7（检索缓存 + Celery broker/backend + SSE 重放缓冲）、Docker Compose 多服务编排（API/db/redis/双 worker）、Nginx（反向代理/SSE 缓冲关闭/HTTPS）、systemd（免 Docker 部署模式）
- **保留形态**：Streamlit 单机 UI 与 CLI 交互模式（本地调试用）

## 架构设计

前后端分离的多服务架构：Vue3 SPA 经 Nginx 反代访问 FastAPI；API 进程只做受理与读写，重活（PDF 解析、知识库构建）经 Redis 队列投递给独立 Celery worker 容器；PostgreSQL 是任务状态与业务数据的唯一权威源；Redis 承担三角色（检索旁路缓存 / Celery broker+backend / 对话流断线重放缓冲）。

- **前端层**（`frontend/src/`）：composables 封装 SSE 流解析与任务流订阅（useChatStream/useTaskStream），Pinia store 做事件派发与块分段渲染，视图含对话/简历/知识库管理；开发期 Vite 代理 /api 规避 CORS
- **API 层**（`src/api/`）：FastAPI 路由（chat 流式对话 / resume 解析三端点 / knowledge 构建与任务查询 / auth 注册登录刷新）、依赖注入单例（AgentService）、task_stream SSE 适配器（DB 快照首帧 + 轮询转发，帧键名与前端契约逐字对齐）
- **worker 层**（`src/worker/`）：Celery 应用（双队列路由 resume/knowledge、acks_late、JSON 序列化）、任务体（进度双通道：update_state 进 backend + update_task 进权威 DB）、task_store（build_tasks 表读写，吞异常降级）
- **Agent 层**（`src/agent/`）：IntentClassifier（LLM 结构化输出意图分类）、ResumeAgent（LangGraph StateGraph 双模式路由：quick_response → 单次检索回答；deep_thinking → 分层检索 + 草稿生成 + 岗位优化）、AgentTools（知识检索、简历生成、简历优化工具集）
- **检索层**（`src/retrieval/`）：BM25Retriever、VectorRetriever、HybridRetriever（加权融合 vector_weight=0.6 + Redis cache-aside 包装）、visibility（按用户算可见 doc_id 白名单，检索与缓存 key 都参与）、LLMReranker（预留）；三类检索器共享的进程级索引快照支持按 `{doc_id: updated_at}` 版本比对做懒刷新（含删除驱逐）
- **知识处理层**（`src/knowledge/`）：PDFParser（MinerU 双通道：本地 GPU / 官方轻量 API）、TextSplitter（tiktoken 分块）、CourseSummarizer、ProjectDocParser/Scanner/Ingestor（项目文档解析、素材按优先级拼接与目录挑选、按章节分块入库，`简历优先` 标签解析）、doc_template + project_normalizer（模板是唯一真理来源，不合规整篇交 LLM 按模板重写）、ResumeFileParser（docx/pdf/md/txt 分流解析）、双通道索引构建
- **数据层**（`src/database/`）：SQLAlchemy 2.0 声明式模型（users/refresh_tokens/conversations/messages/build_tasks/project_docs），Alembic 迁移，同步 SessionLocal（worker）与异步 AsyncSession（API 读路径）双引擎

数据流关键路径：
1. 流式对话：POST /api/chat → 意图分类 → LangGraph 路由 → run_stream 结构化事件流（intent/token/done）→ SSE 帧 → 前端块分段渲染；事件同步写 Redis 重放缓冲，断线重连按序号回放
2. 后台任务：上传/构建请求 → 先 insert build_tasks(pending) 再 apply_async 投递（显式 task_id）→ 202 立即返回 → worker 消费并双通道推进度 → 前端 SSE 快照+轮询转发 / 轮询兜底查 DB
3. 知识入库：PDF → MinerU 解析 → tiktoken 分块 → Embedding 向量化 → FAISS/BM25 双通道索引（三级增量判断）

## 核心功能

- **知识库构建**：PDF 解析→分块→向量化入库，FAISS + BM25 双通道，增量构建只处理新增文档（三级增量判断 + 原子写入）；已迁移为 Celery 后台任务（knowledge 队列串行化，防止并发写坏共享索引）
- **项目介绍文档管理**：简历措辞成品一个项目一份，放入 `projects/`（公共在根目录、私人在 `u{uid}/`）即生效；生成简历时遍历挑选（≤6 全选、>6 按目录 LLM 挑选、标签项目必选且排前），素材按「简历描述→技术栈→量化成果→职责→亮点摘句」优先级拼接并受单份 token 预算约束；无介绍文档时自动降级检索项目亮点分块兜底
- **项目素材多用户隔离**：项目文档登记 `project_docs` 表并按用户可见范围（公共 + 本人）计算 doc_id 白名单，检索、定向加权与缓存 key 全部受该白名单约束；用户对话开始时按文档版本懒刷新进程级索引快照，上传/删除即时生效且不越界影响他人
- **项目亮点入库与优先标签**：项目亮点 README 入库为 `category=project` 分块，`简历优先` 标签解析入元信息，检索项目素材时定向加权入选
- **双模式路由**：LLM 意图识别自动选择快速问答或深度思考路径，降级策略保证检索兜底
- **混合检索 + Redis 缓存**：BM25 关键词 + 向量语义加权融合，异常时自动降级为纯 BM25；检索结果 cache-aside 缓存，命中跳过 Embedding 网络调用
- **分层检索**：深思路径下 LLM 先浏览课程目录选高价值知识点，再定向检索对应课程
- **简历生成与优化**：综合知识检索 + 项目经历，按目标岗位要求自动生成并优化简历
- **基于已有简历生成**：上传 .docx/.pdf/.md/.txt 分流解析，已有简历作为事实骨架、沿用其章节结构与字段格式输出；解析经 Celery 后台任务（202 受理 + 状态轮询 + SSE 进度流三端点）
- **用户系统**：注册/登录/JWT 双令牌（access Bearer + refresh httpOnly cookie 轮换），对话、任务与项目文档按用户归属，跨用户访问统一 404（IDOR 防护），注册开关由 ALLOW_REGISTER 环境变量控制（默认关闭）
- **对话持久化**：会话与消息落 PostgreSQL，前端历史面板 + sessionStorage 续接，刷新/重开不丢对话
- **前端 AI 交互层**：稳定块分段渲染（长输出无掉帧）、工具调用时间线、RAG 引用卡片（可点击溯源）、断线重连 + 已生成内容保全、TTFT 埋点与后端耗时统计打通
- **多模型支持**：环境变量切换 DashScope/OpenAI/Gemini 三类 LLM 提供商
- **生产部署**：docker compose 一键起 API+db+redis+双 worker；Nginx 关 SSE 缓冲 + 600s 长超时 + auth_basic 可选口令；免 Docker 模式退化为 systemd（uvicorn + 两个 celery worker 单元）；update.sh 热更新自动重启 API 与 worker

## 技术亮点

**多用户项目素材隔离与检索快照懒刷新**：项目文档支持多人上传后暴露三类越界缺陷——(1) 检索链路没有可见范围约束，用户 B 的项目会作为素材进入用户 A 的简历；(2) FAISS/BM25/混合检索三类检索器共享同一份进程级索引快照，刷新时按 doc_id 前缀整批驱逐，两个用户对话会互相踢掉对方的文档；(3) 删除一份"较旧"的项目文档不改变版本号，快照判废逻辑失效，已删除的项目继续参与挑选（幽灵文档）。修复方案：项目文档登记 `project_docs` 表，doc_id 带 `u{uid}-` 段，检索与定向加权统一走可见 doc_id 白名单（公共目录 + 本人目录，同名以私有为准），检索缓存 key 把 doc_ids 纳入并在前缀升 v2 一次性作废污染缓存；刷新判据从 `MAX(updated_at)` 单值改为 `{doc_id: updated_at}` 字典比对，得出 changed/removed 两个集合并首次同步即真正刷新（检索器可能已在"别人先对话"时构造完成，只记账不刷会永远缺本用户的文档）；驱逐改为按 removed_ids 精确删除而非按前缀整批清。以 13 条跨用户隔离用例 + 快照懒刷新用例锁定回归（含"删旧文档仍触发刷新""不越界驱逐他人文档""未变更不重复加载"）。

**简历素材通道重建（单节引用 → 优先级拼接 + token 预算）**：上传旧简历生成的新简历几乎与原件一字不差，根因不在模型而在素材闸门——项目文档只把「简历描述」一节放进 prompt，万字文档实进 180 字，模型看不到技术栈、量化成果与新做的后端体系；同时提示词把旧简历钉成不可动摇的事实骨架，却没有两条素材冲突时该信谁的判据。重建素材层：按「简历描述 → 技术栈 → 量化成果 → 个人职责与贡献 → 技术亮点(摘句) → 核心功能(摘句) → 项目难点」固定优先级拼成 `【标签】正文` 块，亮点类长章节每条只取首句（≤120 字）让预算覆盖更多条目，单份 1500 token 预算内整节取用、装不下按字符比例截断保住开头、剩余预算过少则整节省略；提示词补冲突判据（素材记录的是更新的事实，测试数量/性能指标/技术清单冲突时改写旧简历里的过期数字，素材有而旧简历未提的能力补进技能与责任描述不算编造），并把"不得丢弃"收窄到事实性章节。刻意不合并「一句话简介」与「简历描述」两节：模板是下载样板、LLM 归一化基准与合规校验三合一的唯一真理来源，合并会让存量文档判为不合规、重新上传时被 LLM 改写回去。单份素材进入 prompt 的体量从 129~180 字提升到 1158~1504 token。

**知识库增量构建与成本优化**：课程知识库含 28 篇 PDF，每次全量重建需对全部分块调用 Embedding API，新增 1 篇文档也要承担全部 API 成本。设计了以 doc_id 为主键的三级增量判断机制（存在性检查→mtime 过期检测→--force 强制全量），配合 BM25 索引原子写入（`.pkl.tmp` + `os.replace`）防止构建中断损坏索引，以及 `--prune` 清理孤儿索引。新增 1 篇 PDF 的 Embedding API 调用从约 28 次降为 1 次，API 成本降至全量重建的 1/28。

**知识问答失效三重根因排查与修复**：用户反馈问答答案与 PDF 内容不符，排查发现三层级联 bug——(1) API 客户端未检查 HTTP 状态码，403 错误被静默吞掉导致意图识别永远降级到"闲聊"（不检索）；(2) BM25 索引和查询均用 `str.split()` 分词，中文无空格导致整句成为单个 token，检索完全无效；(3) FAISS C++ 层 `fopen` 不支持中文路径，28 个向量索引全部加载失败且无报错。修复方案：API 非 200 立即抛异常、封装统一 jieba 分词器（索引与查询共用）、FAISS 读取侧临时文件兼容层。修复后检索 top1 精确命中目标文档章节。

**BM25 + 向量混合检索与降级链**：单纯 BM25 在长查询下被高频词稀释排名，单纯向量检索对精确术语匹配不敏感。实现 HybridRetriever 双通道召回加权融合（vector_weight=0.6，分数归一后合并排序），并构建三级降级链：混合检索失败→纯 BM25→返回友好提示，保证问答功能在任何异常下不中断。

**检索结果 Redis 旁路缓存（cache-aside）**：混合检索每次查询都要调用 Embedding API 向量化 query（网络往返最贵），相同问题重复付费。在 HybridRetriever 外包一层 cache-aside：缓存 key 对全部影响结果的入参（query 空白归一化/category/top_n/vector_weight/doc_ids 排序拼接）做 sha256 摘要，命中直接返回跳过 Embedding 调用；空结果也用短 TTL 缓存防穿透，TTL 加随机抖动防雪崩，key 前缀带版本号（v1→v2）实现零代码全局失效；Redis 读写全 try/except 静默降级——缓存故障只退化为直查，绝不影响问答主链路。

**PDF 解析从 Docling 迁移到 MinerU（GPU 加速）**：Docling 解析中文课程 PDF 速度慢且公式/表格还原差。迁移到 MinerU（pipeline backend，GPU 加速），保持 PDFParser 类接口签名不变实现上层零改动，测试同步重写（mock 新解析入口 + conftest 全局 mock 防误触发真实模型），解决 torch cu130 索引、HF 模型缓存目录、子进程环境变量继承等 RTX 50 系显卡适配问题。生产侧另接 MinerU 官方轻量 API 做云端简历 PDF 远程解析（免装 6GB+ 的 torch 镜像），形成"本地 GPU 批量构建 + 云端轻量 API 单文件解析"双通道。

**JWT 双令牌认证与资源归属（IDOR 防护）**：从零建用户体系：注册/登录签发短期 access token（响应体 Bearer）+ 长期 refresh token（httpOnly cookie，刷新时轮换防重放）；对话、简历解析任务全部挂 user_id 归属，所有读端点带归属校验，跨用户查询统一返回 404 不泄露资源存在性（IDOR 防护）；build_tasks 外键 ondelete=SET NULL，删号保留任务历史只切断归属。SQLAlchemy where 条件曾踩"Python and 短路静默丢弃右半条件"坑（归属校验形同虚设），用测试锁定必须用 and_ 组合。

**Celery 后台任务正式化（契约不变的架构迁移）**：简历解析（MinerU 分钟级）原以线程跑在 API 进程内——抢占对话推理资源、发版重启即丢任务、进程内信号量无法跨副本限流。分三段演进迁移到 Celery：先最小 PoC 验证具名任务链路，再正式化双链路（简历解析 + 知识库构建），最后退役旧线程模型。关键设计：①双队列按任务性质拆分——resume 队列 concurrency=2 承接用户级并发（替代 threading.Semaphore），knowledge 队列 concurrency=1 把"共享索引文件不可并行写"从使用习惯升级为结构保证；②PostgreSQL build_tasks 表为唯一权威状态源，受理端点严格「先 insert(pending) 再 apply_async(显式 task_id)」规避 worker 抢先 update 命中 0 行的经典竞态，worker 进度双通道（Celery backend + DB）；③对外契约一字不改——三端点路径、SSE 帧键名（progress/done/error）、轮询兜底全部保持，SSE 从"内存事件历史重放"降级为"DB 快照首帧 + 轮询转发"适配器，前端零改动无感知；④acks_late 让 worker 崩溃时消息回队列重投，.upload 临时文件 finally 统一清理。迁移后 API 受理 202 毫秒级返回，重活完全隔离，API 发版重启不再影响进行中的构建任务。

**前后端分离 Web 应用（SDD + TDD）**：为对接真实 Web 前端，将 Streamlit 单体拆分为 FastAPI 后端 + Vue3 前端。后端以 `docs/specs/api-contract.md` 为唯一契约（SDD 规格驱动开发），用生成器包装把 Agent 的 `run_stream()` 事件映射为 SSE 帧（chat: status/intent/token/done/error；task: progress/log/done/error）；前端 `types/events.ts` 从契约派生判别联合类型，SSE 用 fetch + ReadableStream 消费（支持 POST、按空行切帧、处理跨 chunk 半帧与多字节 UTF-8），Markdown 经 markdown-it（html:false）+ DOMPurify 双层 XSS 防护。全程 TDD：前端 83 个 Vitest 用例（SSE 解析/composable/store/组件）、后端 pytest 全部通过，vue-tsc 类型检查与 vite build 均通过。任务进度链路后续由「内存任务表 + daemon 线程」演进为 Celery 后台任务，对外帧契约保持不变。

**全链路 SSE 流式与断线重连**：对话生成是 30~90s 的长任务，整块等待体验差且移动端易断线。后端 run_stream yield 结构化事件流（intent/token/done，事件名即前后端唯一耦合点），经 SSE 帧编码下发；前端不用现成库、手写 SSE 解析器处理半帧缓冲、UTF-8 多字节截断、abort 中断三个真实坑；断线重连靠后端 Redis 重放缓冲——每帧带递增序号写入 Redis，重连时按最后序号回放已生成内容再继续实时流，用户刷新页面不丢已生成的半截回答。配套前端五项交互增强：稳定块分段渲染（长输出无可见掉帧）、工具调用时间线（Agent 检索过程可视化）、RAG 引用卡片（可点击溯源）、TTFT 埋点与后端耗时统计打通。配合关闭意图识别 thinking 模式，首 token 从 11s 降至 0.6s。

**数据持久化迁移（文件系统 → PostgreSQL + Alembic）**：对话记录与任务状态原存文件系统，无法多副本共享、无事务保障。迁入 PostgreSQL 五表（users/refresh_tokens/conversations/messages/build_tasks），SQLAlchemy 2.0 声明式建模（Mapped 注解），Alembic 管理 schema 迁移；API 读路径用异步 AsyncSession（psycopg v3 异步驱动，Docker 内跑 Linux 事件循环规避 Windows Proactor 限制），worker 写路径用同步 SessionLocal（Celery 任务体内无事件循环）——同一张表按运行环境选对引擎形态。时间戳列统一 DateTime(timezone=True)，读接口 to_epoch 归一化为 epoch 秒。

**项目素材管线：亮点入库 + 优先标签加权检索**：简历需要稳定可控的项目素材，而项目源码体量大、直接喂给 LLM 成本高。设计文档级管线：用提示词把每个项目总结成一份亮点 README，HighlightIngestor 按章节分块入库并打 `category=project` 标签（mtime 增量、变更文档自动重建）；文档级标签机制只识别第一个二级标题前的 `> 标签：简历优先` 行，解析入元信息并从正文剥离；检索时标签项目定向均分名额、轮询交错保证入选且排序靠前，其余名额按相关度竞争。端到端验证：23 份亮点文档入库后，检索前 4 条精确命中 4 个标签项目各一块。

**项目介绍文档零构建挑选**：每次生成简历都现场措辞项目经历，耗时长且措辞不稳定。将项目介绍改为离线预写成品：一个项目一份 `.md`（H1 项目名 + 一句话简介 + 亮点），放入目录即生效，不入库不向量化。挑选策略分三层：标签项目必选且排前；文档数 ≤6 时全选，零 LLM 调用；超过时一次 LLM 调用按"项目名+一句话简介"轻量目录挑选（结构化输出、过滤编造项目名、失败自动兜底为标签+顺序选取）。入选文档全文直接进简历生成上下文，项目经历部分省去现场长生成，响应更快、措辞稳定可编辑。

**基于已有简历的格式跟随生成**：直接从素材生成简历容易丢失用户真实事实（个人信息/教育/工作经历）且输出结构漂移。设计事实骨架机制：上传侧 ResumeFileParser 按扩展名分流解析（docx 用 python-docx 按 body 顺序提取段落表格、并将手工排版的加粗章节行识别为标题，pdf 复用 MinerU，md/txt utf-8→gbk 兜底解码），已有简历作为事实骨架进入生成 prompt，提取其标题行显式注入"严格沿用章节结构与顺序"约束，并要求章节内字段与条目格式同样沿用、项目素材改写为该字段格式而非照搬素材文档结构；LLM 输出经开场白剥离后处理，保证简历正文纯净。链路完全向下兼容（不传已有简历时行为不变）。

**分层检索策略（目录选点→定向检索）**：深思路径下直接混合检索容易召回大量低相关分块。设计两级检索策略：第一级 LLM 浏览全部课程结构化摘要目录，按"简历含金量"和岗位匹配度挑选高价值知识点；第二级按选中知识点定向检索对应课程（每门课公平分配名额、轮询交错合并），过滤 LLM 编造的不存在 doc_id。任一环节失败自动降级为普通混合检索，保证功能可用。

**生产部署工程化（Docker/Nginx/systemd 双模式）**：docker compose 编排五服务（API + PostgreSQL + Redis + 双 Celery worker），environment 覆盖 env_file 的连接串规则、健康检查门控启动顺序、共享卷传递上传文件（消息只带路径不带字节）；Nginx 反代关闭 SSE 缓冲（proxy_buffering off）+ 600s 读超时保住长任务流式；部署脚本双模式——Docker 模式一键构建启动，无法访问 Docker Hub 的服务器自动退化 systemd 模式（uvicorn + redis-server + 两个 celery worker 单元，EnvironmentFile 之后写 Environment 覆盖连接串）；update.sh 增量更新识别部署模式，重启 API 时联动重启 worker（celery 不热重载，漏重启则内存里还是旧任务体）。

## 量化成果

- 测试规模：后端 608 个 pytest + 前端 83 个 Vitest 全部通过，覆盖知识处理、检索、Agent、API 契约、任务队列、认证、多用户隔离、前端流式解析等模块
- 简历素材通道：单份项目文档进入生成上下文的体量从 129~180 字提升到 1158~1504 token（单份预算 1500），并以 13 条跨用户隔离用例锁定越界可见/互相踢除/删除幽灵三类缺陷不再复发
- 流式性能：意图识别关闭 thinking 后首响应从 6s 降至 1.7s；问答关闭 thinking 后首 token 从 11s 降至 0.6s
- 检索缓存：BM25 索引内存缓存省约 0.4s/次反序列化；Redis 缓存命中跳过整条混合检索（含 Embedding API 网络调用）
- 增量构建：新增 1 篇 PDF 的 Embedding API 成本从 ~28 次调用降为 1 次（成本降至 1/28）
- 知识库规模：32 篇课程 PDF + 按用户隔离的项目文档，逐篇独立 BM25 + FAISS 双索引（文档级增量，改一份只重建该份）
- 后台任务：API 受理从同步阻塞重活变为 202 毫秒级返回；并发多个解析任务服务不垮（resume 队列并发 2 + knowledge 队列串行 1）；API 发版重启不影响进行中任务，worker 崩溃消息回队列重投

## 个人职责与贡献

- 独立完成全部系统设计与开发（知识处理、索引构建、检索、Agent 路由、前后端与部署）
- 主导全栈生产化改造：FastAPI + Vue3 前后端分离、JWT 用户系统、PostgreSQL 持久化 + Alembic、Redis 检索缓存、Celery 双队列后台任务迁移（对外契约不变、前端零改动）
- 设计增量索引构建机制并实施文档级 mtime 过期检测方案
- 排查并修复知识问答失效的三重根因（API 静默吞错、中文分词失效、FAISS 中文路径）
- 主导 PDF 解析引擎从 Docling 到 MinerU 的迁移（保持接口零改动）+ 云端轻量 API 双通道
- 采用 SDD + TDD 设计并实现前后端分离 Web 应用（FastAPI REST/SSE 后端 + Vue3 前端，契约文档驱动类型派生）
- 手写前端 SSE 解析器与断线重连重放机制，落地五项 AI 交互增强（块分段/工具时间线/引用卡片/内容保全/TTFT 埋点）
- 设计已有简历上传解析与格式跟随生成机制（事实骨架 + 章节/字段格式约束）
- 修复多用户项目素材的三类越界缺陷（可见范围缺失、检索快照互踢、删除幽灵），并以 13 条隔离用例锁定
- 重建简历素材通道（章节优先级拼接 + token 预算 + 摘句压缩），确立素材优先于旧简历的提示词冲突判据
- 编写后端 608 个 pytest + 前端 83 个 Vitest 测试用例（TDD 驱动开发）
- 搭建 Docker Compose 多服务编排与 Nginx/systemd 双模式部署脚本，完成云服务器部署

## 检索关键词

ResumeAgent, 简历生成, RAG, 检索增强生成, LangGraph, Agent, FAISS, BM25, 混合检索, hybrid retrieval, 向量检索, 增量索引, incremental indexing, MinerU, PDF解析, 流式输出, streaming, SSE, 断线重连, 意图识别, intent classification, 分层检索, 知识库构建, FastAPI, Vue3, TypeScript, Pinia, 前后端分离, Celery, 任务队列, task queue, Redis, 旁路缓存, cache-aside, PostgreSQL, SQLAlchemy, Alembic, JWT, 双令牌轮换, IDOR, 认证授权, Docker Compose, Nginx, systemd, 部署, Streamlit, LangChain, Pydantic, 双模式路由, 项目亮点入库, 优先标签加权检索, 项目介绍挑选, 已有简历生成, 格式跟随, python-docx, SDD, TDD, Vitest, REST API, 多用户隔离, 可见范围白名单, visible_doc_ids, project_docs 表, doc_id 用户段, 检索快照懒刷新, doc_id 版本字典比对, 幽灵文档, 跨用户互踢, 缓存 key 版本失效, 素材闸门, 章节优先级拼接, token 预算裁剪, 亮点摘句, 新旧素材冲突优先级
