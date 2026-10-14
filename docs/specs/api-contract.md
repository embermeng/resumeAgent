# ResumeAgent 前后端分离 API 契约(SDD 规格)

本文档是后端(FastAPI)与前端(Vue3)之间的唯一真理来源(Single Source of Truth)。
任何接口/事件变更须先改本文档,再同步两端实现与测试。

## 1. 通用约定

- 基础路径:所有接口以 `/api` 前缀。
- 开发地址:后端 `http://localhost:8000`;前端 Vite dev server `http://localhost:5173`,通过 proxy 将 `/api` 转发到后端。
- 编码:请求/响应体一律 UTF-8 JSON(文件上传除外,用 `multipart/form-data`)。
- 鉴权:部分接口需 JWT(见 §1.2);其余接口暂开放,鉴权加固留待后续阶段。
- 后端全局单 `ResumeAgent` 实例;知识库构建任务用内存任务表 + 后台线程。
- 普通接口错误:返回标准 HTTP 状态码 + FastAPI 默认错误体 `{"detail": "..."}`。
- SSE 接口错误:响应头一旦发出(200)后不可改状态码,错误以 `event: error` 帧下发。

### 1.1 SSE 帧格式

所有 SSE 接口 `Content-Type: text/event-stream`,响应头包含:

```
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no
```

每个事件是一帧,格式如下,帧与帧之间以空行(`\n\n`)分隔:

```
id: <可选:单调递增整数序号 seq>
event: <事件名>
data: <单行 JSON>

```

约定:
- `data` 必须是**单行** JSON;内容中的换行由 `json.dumps` 转义为 `\n`,不会出现裸换行。
- `data` 使用 `ensure_ascii=False`,中文原样传输。
- 客户端按 `\n\n` 切帧;需处理"半帧跨 chunk"的情况(缓冲不完整片段到下次)。
- `id`(可选):**仅可重放的流**(§4.2 对话流)使用,值为该事件在本次流内的单调递增序号 `seq`(从 0 起)。客户端记录最后收到的 `id`,断线重连时经 `Last-Event-ID` 头或 `after` 查询参数回传,服务端只重放 `seq` 更大的事件(见 §4.2.1)。不带 `id` 的流(任务进度流 §4.5/§4.8)按快照重放,无需序号。前端 SSE 解析器需相应解析并保留 `id` 字段(见 §6)。

### 1.2 认证约定

- **受保护接口**:#2 `POST /api/chat`、#2b `GET /api/chat/{stream_id}/stream`、#3 `POST /api/resume/parse`、#4 `GET /api/resume/parse/{task_id}`、#4b `DELETE /api/resume/current`、#5 `GET /api/resume/parse/{task_id}/stream`、#5b `GET /api/resume/current`、#11 `GET /api/conversations`、#12 `GET /api/conversations/{id}/messages`、#12b `DELETE /api/conversations/{id}`、#19 `PATCH` 与 #20 `DELETE /api/auth/{user_id}`(仅限本人)。其余接口(health、#6 supported-extensions、knowledge、#18 用户查询)开放。
- **请求头**:`Authorization: Bearer <access_token>`。SSE 受保护接口同样走此头(前端用 fetch 流式请求,不受 EventSource 无法带头的限制)。
- **JWT**:HS256 签名;payload 含 `sub`(用户 id 字符串)、`exp`、`type`(`"access"` / `"refresh"`)。密钥取自环境变量 `SECRET_KEY`(经 .env / compose env_file 注入);TTL 由 `src/config.py` 的 `AuthConfig` 配置(代码中经 `settings.auth.*` 访问):`access_token_expire_minutes`(默认 60 分钟)、`refresh_token_expire_minutes`(默认 7×24×60)。
- **401 矩阵**(受保护接口):
  | 情形 | 状态码 | detail |
  |---|---|---|
  | 缺失 Authorization 头 | 401 | `Not authenticated`(OAuth2PasswordBearer 内置文案) |
  | 解码失败/已过期/`type≠access`/`sub` 非整数 | 401 | `Invalid or expired token` |
  | token 指向的用户已不存在 | 401 | `User not found` |
- **refresh token 载体**:不进 JSON 响应体,经 `Set-Cookie` 下发——名 `refresh-token`、`HttpOnly`(JS 不可读,收窄 XSS 面)、`SameSite=Strict`(防 CSRF)、`Path=/api/auth`(只随认证端点发送)、`Max-Age` = refresh_token_expire_minutes×60;`Secure` 由 `settings.auth.cookie_secure` 配置驱动(开发 http 置 false、生产 https 置 true)。dev(vite proxy)与 prod(静态托管)均同源部署,cookie 天然可用。
- **轮换与复用检测(jti + family)**:库 `refresh_tokens` 表不存 token 本体,只存其 payload 里的随机 `jti` 与所属 `family_id`(一次登录派生的所有 refresh 同属一个 family)。每次调 `/api/auth/refresh`:吊销旧 jti → 签发新对(新 refresh 沿用 family_id) → Set-Cookie 覆盖。**已吊销的 jti 被再次使用 = token 泄露信号 → 吊销整个 family**。前端收到 refresh 接口的 401 应直接登出(不做重试,防死循环)。
- **归属判据**:目标会话不属于当前用户 → `404`(与"不存在"同判据,**不用 403**,避免泄露资源存在性)。

## 2. 数据模型

以下模型同时对应后端 Pydantic(`src/api/schemas_api.py`)与前端 TS 类型(`frontend/src/types/events.ts`)。

### 2.1 请求/响应模型

| 模型 | 字段 | 类型 | 说明 |
|---|---|---|---|
| `ChatRequest` | `prompt` | string | 用户输入,必填,非空 |
| | `existing_resume` | string? | 已有简历 Markdown 文本,可选。**每人只有一份简历**,前端挂载时经 §4.5b 自动取回并随每次请求带入,不提供"取消附加"的开关(要不含简历只能先删简历) |
| | `conversation_id` | int? | 所属会话 id;为空则服务端新建会话并经 `conversation` 事件回传 |
| `ParseResponse` | `filename` | string | 原始文件名(**旧同步解析响应**;异步化后 `POST /api/resume/parse` 改返回 `ResumeParseAck`,解析结果经 `ResumeParseStatus.content` 下发) |
| | `content` | string | 解析出的 Markdown 文本 |
| `ResumeParseAck` | `task_id` | string | 简历解析任务 ID |
| | `status` | TaskState | 初始为 `"pending"` |
| `ResumeParseStatus` | `task_id` | string | 任务 ID |
| | `status` | TaskState | 状态(**不含 `task` 字段**,与知识库 `TaskStatus` 区分) |
| | `stage` | string? | 当前阶段标识(`queued` 排队 / `parsing` 解析中 等) |
| | `percent` | number? | 进度百分比 0-100 |
| | `message` | string? | 最新进度文本 |
| | `filename` | string? | 原始上传文件名(前端回显) |
| | `content` | string? | 解析出的 Markdown 文本(仅 `status=="success"` 时返回) |
| | `error` | string? | 失败原因 |
| | `created_at` | number | 创建时间(epoch 秒) |
| | `finished_at` | number? | 结束时间(epoch 秒) |
| `ResumeCurrent` | `filename` | string? | 上传时的原始文件名(§4.5b「我的简历」回显) |
| | `created_at` | number | 任务创建时间(epoch 秒) |
| | `finished_at` | number? | 解析完成时间(epoch 秒) |
| | `content` | string? | 解析出的 Markdown,前端据此自动带入生成上下文;产物文件丢失时为 `null` |
| `SupportedExtensions` | `extensions` | string[] | 如 `[".md",".txt",".docx",".pdf"]` |
| `BuildRequest` | `task` | TaskKind | 构建任务类型,必填 |
| | `force` | boolean? | 是否强制全量,默认 false |
| | `chunk_size` | int? | 分块大小,默认 300 |
| | `chunk_overlap` | int? | 重叠 token,默认 50 |
| | `prune` | boolean? | 清理孤儿索引,默认 false |
| `BuildAck` | `task_id` | string | 任务唯一 ID |
| | `status` | TaskState | 初始为 `"pending"` |
| `TaskStatus` | `task_id` | string | 任务 ID |
| | `task` | TaskKind | 任务类型 |
| | `status` | TaskState | 状态 |
| | `stage` | string? | 当前阶段标识 |
| | `percent` | number? | 进度百分比 0-100 |
| | `message` | string? | 最新进度文本 |
| | `created_at` | number | 创建时间(epoch 秒) |
| | `finished_at` | number? | 结束时间(epoch 秒) |
| | `error` | string? | 失败原因 |
| `TaskList` | `tasks` | TaskStatus[] | 任务状态列表 |
| | `total` | int | 任务总数 |
| `ConversationSummary` | `id` | int | 会话 id |
| | `title` | string | 会话标题(取首条用户消息截断至 50 字) |
| | `created_at` | number | 创建时间(epoch 秒) |
| | `updated_at` | number | 最后活动时间(epoch 秒) |
| `ConversationList` | `conversations` | ConversationSummary[] | 会话列表(updated_at 降序) |
| | `total` | int | 会话总数 |
| `MessageOut` | `id` | int | 消息 id |
| | `role` | string | `"user"` / `"assistant"` |
| | `content` | string | 消息文本 |
| | `intent` | string? | 意图(仅 assistant 消息有值) |
| | `created_at` | number | 创建时间(epoch 秒) |
| `ConversationMessages` | `conversation_id` | int | 会话 id |
| | `messages` | MessageOut[] | 消息列表(created_at 升序) |
| `UserCreate` | `username` | string | 1~50 字符 |
| | `email` | string | EmailStr,≤120 字符,服务端小写归一后入库 |
| | `password` | string | ≥8 字符 |
| `UserLoginReq` | `email` | string | EmailStr,登录凭证为 email(不是 username) |
| | `password` | string | ≥8 字符 |
| `Token` | `access_token` | string | JWT,type=access |
| | `token_type` | string | 固定 `"bearer"` |
| `UserPublic` | `id` | int | 用户 id |
| | `username` | string | 用户名(不含 email) |
| `UserPrivate` | 继承 UserPublic + `email` | string | 完整用户信息,仅注册/本人接口返回 |
| `UserUpdate` | `username` | string? | 1~50 字符,可省略 |
| | `email` | string? | EmailStr,≤120 字符,可省略 |

refresh token 只经 httpOnly cookie 下发,不出现在任何 JSON 响应体中。

`Token` **不含 `expires_in`**:前端采用 **401 驱动刷新**——受保护请求收到 401 时先调 `/api/auth/refresh`,成功则用新 access 重放原请求,失败则登出。前端另在请求/开流前用 `ensureFreshToken()` 解析 access 的 `exp`,临期(默认 30s 内)主动刷新,减少 401 往返。

### 2.2 枚举

- `TaskKind`(知识库构建任务类型,对应现有 CLI 命令):
  `"build-all" | "parse-pdfs" | "extract-summaries" | "split-chunks" | "build-indexes" | "ingest-projects"`
  > 旧名 `"ingest-highlights"` 已随 CLI 命令改名 `"ingest-projects"`(**破坏性**:前端再发 `ingest-highlights` 会被 `BuildRequest.task` 判 `422`)。库里可能仍有 `task="ingest-highlights"` 的历史行,按下面的外来值处理。
  > `build_tasks` 是共享表,除知识库任务外还写着 `"parse-resume"`(简历解析)与 `"ingest-project"`(单数,项目文档入库)两类**外来值**,均不属于 `TaskKind`。`GET /api/knowledge/tasks`、`GET /api/knowledge/tasks/{task_id}` 与其 `/stream` 一律按 **`TaskKind` 白名单**过滤(实现取 `get_args(TaskKind)`,与出参模型同源):命中的外来行按"不存在"处理(`404`),列表与 `total` 一并排除。之所以用白名单而非逐个排除,是因为 `TaskStatus.task` 是必填 Literal,**任何一行未知 task 值都会让整个列表接口 500**(真机发生过:41 行 `ingest-project` 把任务中心打崩)。简历解析状态一律经 `ResumeParseStatus`(不含 `task` 字段)返回,项目文档入库经 `ProjectUploadStatus`。
- `TaskState`(任务状态):
  `"pending" | "running" | "success" | "failed"`
- `Intent`(意图,对齐 `run_stream` 的 intent 事件):
  `"quick_response" | "deep_thinking" | "chitchat"`

## 3. 接口清单

| # | 方法 | 路径 | 类型 | 说明 |
|---|---|---|---|---|
| 1 | GET | `/api/health` | JSON | 健康检查 |
| 2 | POST | `/api/chat` | SSE | 流式对话(问答/简历生成/闲聊);事件序号化 + 双写 Redis,支持断线重放(降级:生成绑定连接) 🔒 |
| 2b | GET | `/api/chat/{stream_id}/stream` | SSE | 重连一次对话生成,从指定序号重放已缓冲事件直到终态 🔒 |
| 3 | POST | `/api/resume/parse` | JSON(202) | 提交简历解析后台任务,返回 `task_id`(不再同步返回内容)🔒 |
| 4 | GET | `/api/resume/parse/{task_id}` | JSON | 查询简历解析任务状态/结果(轮询兜底)🔒 |
| 4b | DELETE | `/api/resume/current` | 204 | 删除我的简历(每人一份;行 + 解析产物文件一并清除,幂等)🔒 |
| 5 | GET | `/api/resume/parse/{task_id}/stream` | SSE | 订阅简历解析任务进度 🔒 |
| 5b | GET | `/api/resume/current` | JSON/204 | 我的当前简历(含正文),供挂载时自动带入生成上下文 🔒 |
| 6 | GET | `/api/resume/supported-extensions` | JSON | 支持的文件扩展名 |
| 7 | POST | `/api/knowledge/build` | JSON | 触发知识库构建后台任务 🔒👑 |
| 8 | GET | `/api/knowledge/tasks/{task_id}/stream` | SSE | 订阅任务进度 🔒 |
| 9 | GET | `/api/knowledge/tasks/{task_id}` | JSON | 查询任务状态快照(轮询兜底)🔒 |
| 10 | GET | `/api/knowledge/tasks` | JSON | 知识库任务列表(分页,created_at 降序;不含 parse-resume)🔒 |
| 21 | POST | `/api/project/preprocess` | JSON | 上传并预处理,返回整理结果供预览确认(**不入库**)🔒 |
| 21b | POST | `/api/project/confirm` | JSON(202) | 预览确认后提交入库后台任务 🔒 |
| 21c | POST | `/api/project/upload` | JSON(202) | 不经预览直接入库(兼容路径)🔒 |
| 22 | GET | `/api/project/upload/{task_id}` | JSON | 查询入库任务状态(轮询兜底)🔒 |
| 23 | GET | `/api/project/upload/{task_id}/stream` | SSE | 订阅入库任务进度 🔒 |
| 24 | GET | `/api/project/docs` | JSON | 我的项目文档列表(公共素材 + 我上传的)🔒 |
| 25 | DELETE | `/api/project/docs/{doc_id}` | JSON(204) | 删除我的项目文档(越权/不存在均 404)🔒 |
| 26 | POST | `/api/project/docs/batch-delete` | JSON | 批量删除我的项目文档(越权 id 静默跳过并回显)🔒 |
| 11 | GET | `/api/conversations` | JSON | 会话列表(分页,updated_at 降序)🔒 |
| 12 | GET | `/api/conversations/{id}/messages` | JSON | 查询某会话的消息历史 🔒 |
| 12b | DELETE | `/api/conversations/{id}` | —(204) | 删除我的会话及其全部消息(越权/不存在均 404)🔒 |
| 13 | POST | `/api/auth/register` | JSON | 注册新用户 |
| 14 | POST | `/api/auth/login` | JSON | 登录,签发 token 对 |
| 15 | POST | `/api/auth/refresh` | JSON | 用 cookie 中的 refresh token 轮换新 token 对 🍪 |
| 16 | GET | `/api/auth/me` | JSON | 当前用户信息 🔒 |
| 17 | POST | `/api/auth/logout` | JSON | 登出:吊销整个 token family(不清 cookie,见 §4.17)🍪 |
| 18 | GET | `/api/auth/user/{user_id}` | JSON | 查询用户公开信息(UserPublic) |
| 19 | PATCH | `/api/auth/{user_id}` | JSON | 修改本人 username/email 🔒 |
| 20 | DELETE | `/api/auth/{user_id}` | JSON | 删除本人账号(级联删会话/消息/refresh_tokens)🔒 |

🔒 = 需 `Authorization: Bearer <access_token>`(见 §1.2);🍪 = 读/写 `refresh-token` httpOnly cookie;
👑 = 仅管理员(`users.is_superuser`),普通用户 `403`。

> **多用户隔离变更(阶段 1~4)**:
> - 课程知识仍是公共资源,检索不过滤。
> - **项目文档按用户隔离**:可见范围 = 公共素材(`user_id IS NULL`)+ 本人,由
>   `project_docs` 表换算成 `doc_ids` 白名单下传给检索器(FAISS 不支持属性过滤)。
> - 对话流 `POST /api/chat` 依据登录用户取项目素材,未登录/CLI 仅见公共素材。
> - 知识库任务查询按 `user_id` 收敛;改造前的历史任务 `user_id` 为 NULL,仅管理员可见。

## 4. 接口详情

### 4.1 GET /api/health

- 响应 `200`:`{"status": "ok"}`

### 4.2 POST /api/chat(SSE)

- 鉴权:需 Bearer access token,缺失/无效 → `401`(依赖注入在开流前校验,与 404 同模式,返回真实状态码而非 error 帧)。
- 请求体:`ChatRequest`
- 响应:`text/event-stream`,事件序列由后端 `ResumeAgent.run_stream()` 映射而来。
- 校验:`prompt` 为空 → `422`(FastAPI/Pydantic 自动)。
- 校验:`conversation_id` 传了但会话不存在**或不属于当前用户** → `404`(在开流前校验,返回真实 404 而非 error 帧)。
- 新建会话时服务端将 `user_id` 记为当前用户。
- 运行时错误 → `event: error` 帧。

**断线重放(当前实现:生成绑定 HTTP 连接)**:

为支持"断线重连 + 已生成内容保全",`POST /api/chat` 在请求内迭代 `run_stream()` 生成,每个事件推送前双写 Redis 重放缓冲(带 `seq`);断线后生成随连接终止并补 `interrupted` 终态,§4.2.1 用 `stream_id` + 最后 `seq` 重放**到断点为止**的已产出事件(不续接未生成部分)。这是相对"完全后台化"的**降级实现**(取舍见本节末「实现强度说明」),端点/序号/去重/404 契约与完全后台化一致:

1. **stream_id**:服务端为每次 `POST /api/chat` 生成唯一 `stream_id`(uuid4),标识"这一次生成",经**流首帧** `conversation` 下发。
2. **事件序号 seq**:本次生成的每个事件按产出顺序分配单调递增整数 `seq`(从 0 起),写入 SSE 帧的 `id:` 字段(§1.1)。`conversation` 首帧 `seq=0`。
3. **事件缓冲(Redis)**:每个事件推送前,以 `{seq, event, data}` 追加写入 Redis 列表 `chat:stream:{stream_id}`,设 TTL(默认 `CHAT_STREAM_BUFFER_TTL=3600` 秒)。这是**瞬态重放缓冲**,非业务数据;最终 assistant 消息仍照常落 `messages` 表(不变)。选 Redis 而非 PostgreSQL:事件序列短命(覆盖重连窗口即可)、TTL 自动回收、复用现有 `redis_client` 基建,避免表膨胀。
4. **生成与连接绑定(降级)**:`run_stream()` 由 `StreamingResponse` 经线程池(`iterate_in_threadpool`)在请求内迭代,逐事件"分配 seq → 写 Redis → yield 给当前连接"。客户端断开**会终止生成**:ASGI 取消响应后,同步生成器在**下一个 yield 点**收到 `GeneratorExit`(生成器常阻塞在 `next()` 等 LLM,故断开到终止之间有秒级延迟),进入 `finally`:①把已生成文本落 `messages` 表;②若缓冲末尾非终态,补写 `done {step:"interrupted"}` 到 Redis(不能 yield,生成器正在关闭)。首 token 低延迟特性保持(请求内直推,无额外 broker 往返)。
5. `POST /api/chat` 的响应流即"首个订阅者":从 `seq=0` 开始推送。若客户端中途断开,可经 §4.2.1 用 `stream_id` + 最后 `seq` 重新订阅,**重放到断点**(含断开后、`GeneratorExit` 前多产出并已入缓冲的少量事件),以 `done {step:"interrupted"}` 收尾;**不续接尚未生成的部分**(区别于完全后台化)。

对话 SSE 事件协议(每帧带 `id: <seq>`):

```
id: 0
event: conversation data: {"conversation_id":42,"stream_id":"<uuid>"}
id: 1
event: status   data: {"text":"正在识别意图..."}
id: 2
event: intent   data: {"value":"quick_response"}
id: 3
event: token    data: {"text":"RAG"}
...
id: N
event: done     data: {"intent":"quick_response","step":"quick_response_done","resume_final":"","retrieved_knowledge":"...","retrieved_projects":""}
event: error    data: {"message":"..."}
```

事件字段说明:
- `conversation`:`{conversation_id: int, stream_id: string}` **流首帧**(seq=0),回传本次对话所属会话 id(新建或沿用)与本次生成的 `stream_id`。前端:`conversation_id` 存 sessionStorage(归属后续消息、刷新续接同一会话);`stream_id` + 最后 `seq` **仅存内存**,用于**页面内**断网自动重连(§4.2.1)。**刷新**场景不靠 `stream_id` 重连(内存已丢),而是从 sessionStorage 的**流式草稿**(节流保全的已生成内容)恢复(见 §7)。
- `status`:`{text}` 阶段进度提示(深思路径会有多条)。
- `intent`:`{value: Intent}` 意图识别结果,通常在首个 status 之后到达。
- `token`:`{text}` 回答文本增量(quick_response/chitchat 逐块;deep_thinking 一次性给出整份简历文本)。
- `done`:结束帧,携带 `{intent, step, resume_final?, retrieved_knowledge?, retrieved_projects?}`;`resume_final` 非空表示生成了简历(前端提供预览/下载)。
- `error`:`{message}` 流中异常。

事件顺序保证:
- 一定以 `conversation` 开头(恰一次),其后 `status`,以 `done` 或 `error` 结尾。
- `intent` 恰出现一次,在首个 `status` 之后。
- `token` 出现 0 次或多次。
- `seq` 严格单调递增、无空洞;重放与实时推送共用同一 `seq` 空间(同一 `stream_id` 内全局唯一)。

### 4.2.1 GET /api/chat/{stream_id}/stream(SSE)

断线重连 / 续订一次对话生成。语义对齐 §4.5「订阅时若已结束则补发终态」。

- 鉴权:需 Bearer access token;**401/404 校验必须在建立流(`StreamingResponse`)之前完成**,返回真实状态码而非 error 帧。
- 归属校验:`stream_id` 无对应缓冲(Redis 键不存在,含 TTL 过期)**或不属于当前用户** → `404` `{"detail": "stream not found"}`(同判据,不泄露存在性)。归属经 `stream_id → conversation_id → user_id` 链校验。
- 重放起点(二选一,`Last-Event-ID` 优先):
  - 请求头 `Last-Event-ID: <seq>`(SSE 标准;本项目 fetch 手写流由前端显式设置);
  - 或查询参数 `?after=<seq>`;
  - 两者都缺省时 `after=-1`(从头全量重放)。
- 行为:先从 Redis 重放所有 `seq > after` 的**已缓冲事件**(按 seq 升序),随后**轮询**(0.15s/次)等新事件直到读到终态帧(`done`/`error`)或 300s 超时(超时补 `done {step:"timeout"}` 收流)。降级实现下断线后生成已在终止,故重连通常是"重放已缓冲事件 → 等到 `finally` 补写的 `done {step:"interrupted"}` → 关闭流";因 `GeneratorExit` 有秒级延迟,重连可能先重放完再**短暂等待**终态(前端以「恢复中」状态呈现)。
- 幂等/去重:服务端保证只发 `seq > after` 的事件;前端另按 `seq` 去重(丢弃 `seq <= 已应用最大 seq` 的帧),双重保证 `token` 不重复拼接。
- 缓冲已过期/不存在(GET 返回 404):前端**不自动重发**(会重复生成),而是降级为「保全本地已生成内容」——把断点前内容标记为中断并写入草稿,用户可手动重试或刷新后从草稿恢复(见 §7)。此时 `messages` 表通常已有该次 assistant 消息(`finally` 落库,含断开后补产出的尾部),如需比本地草稿更全的文本,可选经 §4.12 拉取回填(当前前端未做此拉取,以本地草稿为准)。
- 事件协议与 §4.2 完全一致(含 `id:` 序号)。

> **实现强度说明(当前采用降级版)**:本项目采用**降级实现**——`POST /api/chat` 在请求内迭代生成、每事件双写 Redis(带 seq);断线则生成随连接终止(补 `interrupted`),§4.2.1 仅重放到断点为止、**不续接未生成部分**。**完全后台化**(把 `run_stream()` 移到与连接解耦的后台任务,断开后继续跑完并持续写 Redis,重连可续接到完整答案)为**未来可选升级,当前不做**。两版下 §4.2.1 的端点/序号/去重/404 契约完全一致,仅"断线后生成是否继续"的强度不同,**前端代码对两版无感知**(将来升级到完全后台化时前端无需改动)。

### 4.3 POST /api/resume/parse

- 鉴权:需 Bearer access token(401 见 §1.2)。
- **语义变更(破坏性)**:由「同步阻塞解析并返回 Markdown」改为「**提交后台解析任务并立即返回 `task_id`**」。前端拿到 `task_id` 后经 §4.5 SSE 订阅进度(或 §4.4 轮询兜底),任务成功后从 `ResumeParseStatus.content` 取解析结果。
- 请求:`multipart/form-data`,字段 `file`(二进制)。服务端在请求内读出 `file` 字节后交给后台线程(请求结束 `UploadFile` 即关闭)。
- 响应 `202`:`ResumeParseAck` = `{"task_id": "<uuid>", "status": "pending"}`。
- 行为:任务以 `task="parse-resume"` 入 `build_tasks` 表并记 `user_id`(归属)、`filename`(回显);后台由**进程级信号量**限流(并发上限 `RESUME_PARSE_MAX_CONCURRENCY`,默认 1),超出的任务排队等待槽位。解析出的 Markdown 写入 `data/processed/resume_uploads/{uuid}.md`,DB 仅存 `result_path`。
- **每用户一份(替换语义)**:worker 在把本次任务置 `success` 并回写 `result_path` **之后**,才删除该用户其余 `parse-resume` 行及其产物文件。放在成功之后是为了让"新文件解析失败"不至于把用户原来的简历一起弄丢;清理范围含 `pending`/`failed` 的历史行,否则失败行会永久残留。归属 `user_id` 随 Celery 消息传入(可选第三参,默认 `None` → 不做清理,兼容升级前已入队的旧消息)。前端在提交前就"会替换当前简历"向用户确认一次,但真正的删除只由上述成功回写触发。
- 错误:
  - 不支持的扩展名 → `400` `{"detail": "不支持的简历文件格式: ..."}`(提交前白名单快速校验,不浪费任务槽位)。
  - 未携带文件 → `422`。
  - 解析结果为空/解析失败 → 任务置 `failed`,错误经 §4.4/§4.5 的 `error` 字段返回(不再是提交时的 400)。

### 4.4 GET /api/resume/parse/{task_id}

- 鉴权:需 Bearer access token(401 见 §1.2)。轮询兜底通道(与 §4.5 SSE 二选一或并用)。
- 响应 `200`:`ResumeParseStatus` 快照。`status=="success"` 时读 `result_path` 文件,把解析出的 Markdown 一并放入 `content` 返回(前端一次拿到)。
- 归属校验:`task_id` 不存在**或不属于当前用户** → `404` `{"detail": "task not found"}`(同判据,不泄露存在性,延续 IDOR 防护)。
- 内存任务表 miss 时回落查 `build_tasks` 表(服务重启后仍可查历史任务)。

### 4.4b DELETE /api/resume/current 🔒

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 用途:删除"我的简历"这一份资产(§4.5b 回显的那份)。每人只保留一份,故端点不带 `task_id`。
- 响应 `204`:无响应体。**幂等**:本来就没有简历同样返回 204(对前端而言"删掉了"和"本来没有"是同一结果)。
- 行为:删除当前用户**全部** `task="parse-resume"` 行(存量多份的历史数据由此一次自愈),并删除这些行 `result_path` 指向的解析产物 Markdown。产物文件已丢失不算失败,行照删(留一个取不到正文的行更糟)。
- 归属:条件恒含 `user_id == 当前用户`,他人行、知识库构建任务(`task="build-all"` 等)与历史无归属(`user_id` 为 NULL)的行一律不动。端点不接受外部传入的任务标识,因此无从越权。

### 4.5 GET /api/resume/parse/{task_id}/stream(SSE)

- 鉴权:需 Bearer access token;**401/404 校验必须在建立流(返回 `StreamingResponse`)之前完成**,返回真实状态码而非 error 帧。
- 归属校验:`task_id` 不存在**或不属于当前用户** → `404`(开流前)。
- 响应:`text/event-stream`,复用知识库任务 SSE 事件协议(见 §4.8):`progress`(`{stage,message,percent}`,如排队 `queued`/解析中 `parsing`)、`done`(`{task_id,status,elapsed}`)、`error`(`{message}`)。
- 订阅时若任务已结束,补发当前最终状态后立即 `done`。

### 4.5b GET /api/resume/current 🔒

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 用途:每个用户在服务端只保留**一份**简历,该端点即"我的简历"。前端挂载时调用它,把正文自动带入生成上下文(刷新、换标签页、重开浏览器后皆适用)。
- 响应 `200`:`ResumeCurrent` = `{"filename", "created_at", "finished_at", "content"}`,取 `task="parse-resume"` 且 `status=="success"` 且 `user_id` 为当前用户的**最近一条**(`created_at` 降序 limit 1)。单份资源不必再让前端二次请求取正文,故 `content` 直接内联(解析出的 Markdown)。
- 响应 `204`:空响应体,表示"当前没有简历"。**没有简历不是错误**(不是 404),前端据此清掉本地残留,避免带着已被删掉的简历去生成。
- `content` 为 `null`:行在但产物文件已被外部清理。元信息照常返回(前端仍给出删除入口),上下文不塞空正文。
- 归属:查询条件恒含 `user_id == 当前用户`;历史无归属(`user_id` 为 NULL)的行与他人行一律不可见,管理员也不例外(该接口语义是"我的简历",不是后台审计)。
- 存量多行:取最近一条即自愈为"每人一份",无需数据迁移;其余行由下一次上传成功或 §4.4b 删除清掉。

### 4.6 GET /api/resume/supported-extensions

- 响应 `200`:`{"extensions": [".md", ".txt", ".docx", ".pdf"]}`

### 4.7 POST /api/knowledge/build 🔒👑

- 请求体:`BuildRequest`
- 响应 `202`:`BuildAck` = `{"task_id": "<uuid>", "status": "pending"}`
- 行为:创建任务并写入 `build_tasks` 表(`user_id` 记发起人),投递 Celery `knowledge` 队列。
- 错误:
  - 非法 `task` → `422`
  - 非管理员 → `403` `{"detail": "仅管理员可执行知识库构建"}`

> build 是**全局**操作:重扫全部课程 PDF 与所有用户的项目文档、重建索引、产生 embedding 开销,
> 故必须是管理员操作。前端 `/admin` 入口同样按 `is_superuser` 显隐(仅入口,后端仍独立校验)。

### 4.8 GET /api/knowledge/tasks/{task_id}/stream(SSE)

- 响应:`text/event-stream`,实时推送该任务进度。
- 不存在的 `task_id` → `404`(在建立流之前返回)。

任务 SSE 事件协议:

```
event: progress data: {"stage":"parse-pdfs","message":"[1/5] 解析PDF...","percent":20}
event: log      data: {"line":"..."}
event: done     data: {"task_id":"...","status":"success","elapsed":123.4}
event: error    data: {"message":"..."}
```

- `progress`:`{stage, message, percent}` 阶段进度。
- `log`:`{line}` 可选的细粒度日志行。
- `done`:`{task_id, status: "success"|"failed", elapsed}` 结束帧。
- `error`:`{message}` 执行异常(随后仍会有 done 或作为终止)。
- 订阅时若任务已结束,补发当前最终状态后立即 `done`。

### 4.9 GET /api/knowledge/tasks/{task_id} 🔒

- 响应 `200`:`TaskStatus` 快照。
- 不存在 → `404` `{"detail": "task not found"}`
- 命中 `task="parse-resume"` 的行同样按不存在处理(`404`);简历解析任务状态请走 §4.4。
- **可见范围**:管理员看全部;普通用户只看自己发起的(`user_id` 匹配),他人的一律 `404`。

### 4.10 GET /api/knowledge/tasks 🔒

- 查询参数:`page`(默认 1,≥1)、`page_size`(默认 10,1~100)。
- 响应 `200`:`TaskList`,查 `build_tasks` 表,按 `created_at` 降序分页;**只返回 `task` 落在 `TaskKind` 白名单内的行**(见 §2.2)——`parse-resume`、`ingest-project` 等外来值一律排除,避免 `TaskStatus.task` 序列化非法值令整个列表 `500`。
- **可见范围**:管理员看全量;普通用户只看自己发起的。历史任务 `user_id` 为 NULL 的不属于任何人,仅管理员可见。

### 4.11 GET /api/conversations

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 查询参数:`page`(默认 1,≥1)、`page_size`(默认 20,1~100)。
- 响应 `200`:`ConversationList`,**仅返回当前用户的会话**,按 `updated_at` 降序分页。

### 4.12 GET /api/conversations/{id}/messages

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 响应 `200`:`ConversationMessages`,消息按 `created_at` 升序(时间正序)。
- 会话不存在**或不属于当前用户** → `404` `{"detail": "conversation not found"}`(同判据,不泄露存在性)。

### 4.12b DELETE /api/conversations/{id} 🔒

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 响应 `204`:**无响应体**。
- 行为:删除该会话**及其全部消息**。`messages.conversation_id` 外键没有数据库级 `ON DELETE`,子行由 ORM 的 `cascade="all, delete-orphan"` 清除,因此实现必须走 `session.delete(会话对象)`(查询时预取子行),不能发裸 `DELETE` 语句 —— 那样会留下没有会话的孤儿消息。
- 错误:会话不存在**或不属于当前用户** → `404` `{"detail": "conversation not found"}`(同判据,不泄露存在性);重复删除同一条 → 第二次 `404`,不报错。
- 幂等性:成功即 204;已删除后再删返回 404,前端按"刷新列表即可"处理。
- 前端:历史面板条目 hover 才显示删除图标,点击需二次确认;若删的是当前正在看的会话,回到新会话态(清空消息、`conversation_id` 与 sessionStorage 记录),**不影响已上传简历的上下文**。

### 4.13 POST /api/auth/register

- 请求体:`UserCreate`(username 1~50、email EmailStr、password ≥8,不满足 → `422`)。
- 响应 `201`:`UserPrivate`(id/username/email)。
- 错误:用户名已存在 → `409` `{"detail": "Username already exists"}`;邮箱已注册 → `409` `{"detail": "Email already registered"}`(均大小写不敏感查重)。
- 行为:email 小写归一后入库;密码经 argon2id(pwdlib `PasswordHash.recommended()`)哈希后落 `users.password_hash` 列;**注册不自动登录**,前端注册成功后引导调 login。

### 4.14 POST /api/auth/login

- 请求体:`UserLoginReq`(email + password)。
- 响应 `200`:`Token`;同时 `Set-Cookie: refresh-token=<JWT>`(属性见 §1.2,`Path=/api/auth`)。
- 行为:按 email 大小写不敏感查用户;生成新 `family_id`(登录 = 新 family 的起点),落首条 `refresh_tokens` 记录(jti/family_id/expire_at)。
- 错误:邮箱或密码错误 → `401` `{"detail": "Incorrect email or password"}`(统一文案,不区分哪个字段错,避免账号枚举)。

### 4.15 POST /api/auth/refresh

- 请求:无 body,从 cookie 读 `refresh-token`。
- 响应 `200`:`Token`(新 access);同时 Set-Cookie 覆盖为新 refresh(沿用原 family_id)。
- 行为:解码校验 `type=refresh` → 按 `jti` 查 `refresh_tokens` 表:
  - 无记录 → `401`;
  - `revoked=true` → **复用检测**:吊销整个 family,`401`;
  - 正常 → 吊销旧 jti、插入新 jti 记录(同 family)、签发新对。
- 错误:cookie 缺失 → `401` `{"detail": "Refresh token is missing"}`;解码失败/类型不符/表中无该 jti → `401` `{"detail": "Invalid refresh token"}`;jti 已吊销仍被使用(复用检测) → `401` `{"detail": "Refresh token is revoked"}`。

### 4.16 GET /api/auth/me

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 响应 `200`:`UserPrivate`。

### 4.17 POST /api/auth/logout

- 请求:无 body,从 cookie 读 `refresh-token`。
- 行为与响应:无 cookie → `204`(幂等);cookie 有效 → 按 jti 找到 family,**吊销整个 family** → `204`;cookie 存在但解码失败 → `401` `{"detail": "Invalid refresh token"}`。
- **不清 cookie**:服务端吊销已足够——cookie 里的 refresh token 成为死票,后续 `/api/auth/refresh` 一律 401,前端据此登出。

### 4.18 GET /api/auth/user/{user_id}

- 公开接口(无鉴权)。响应 `200`:`UserPublic`(仅 id/username,不含 email)。
- 错误:用户不存在 → `404` `{"detail": "User not found"}`。

### 4.19 PATCH /api/auth/{user_id}

- 鉴权:需 Bearer access token,且 `user_id` 必须为本人。
- 请求体:`UserUpdate`(username/email 均可省略,只改提供的字段)。
- 响应 `200`:`UserPrivate`。
- 错误:非本人 → `403` `{"detail": "Not authorized to update this user"}`;用户不存在 → `404`;新用户名被占用 → `409` `{"detail": "Username already exists"}`;新邮箱被注册 → `409` `{"detail": "Email already registered"}`(与自身现值相同不算冲突)。
- 行为:email 小写归一;username/email 查重均大小写不敏感。

### 4.20 DELETE /api/auth/{user_id}

- 鉴权:需 Bearer access token,且 `user_id` 必须为本人。
- 响应 `204`。错误:非本人 → `403` `{"detail": "Not authorized to delete this user"}`;用户不存在 → `404`。
- 行为:删除用户,ORM `cascade="all, delete-orphan"` 级联删除其全部 conversations(含 messages)、refresh_tokens 与 project_docs 行。
  **删号同时在 `db.delete(user)` 之前清理其项目文档的磁盘产物**(源 md / 分块 JSON / faiss / pkl + `projects/u{id}/` 目录);
  清理失败不阻断删号(否则用户永远删不掉账号),残留由 `prune` 兜底。

### 4.21 POST /api/project/upload 🔒

上传项目文档(.md),提交后台入库任务。

- 请求:`multipart/form-data`,字段 `file`。
- 响应 `202`:`ProjectUploadAck` = `{"task_id": "<hex>", "status": "pending"}`
- 行为:落盘临时文件 → 写 `build_tasks`(task=`ingest-project`)→ 投递 Celery **`project` 队列**
  (独立队列:`knowledge` 队列 `concurrency=1` 且跑 `build-all`,上传排在后面不可接受)。
- 校验(同步快速失败,不占队列):
  - 扩展名非 `.md` → `400`
  - 超过 `PROJECT_DOC_MAX_FILE_SIZE`(默认 200KB)→ `413`
  - 该用户文档数达 `PROJECT_DOC_MAX_DOCS_PER_USER`(默认 30)→ `409`
- 异步阶段(worker 内):无一级标题 → 任务 `failed` 并清理半截产物。
- 去重口径:**项目名**(不是文件名)。同名项目(同用户)直接覆盖——上传即"重新上传修正内容"。

### 4.21b POST /api/project/preprocess 🔒

上传项目文档并做**两层预处理**，返回整理结果供用户预览确认。**本端点不入库**。

预处理分层（详见 `src/knowledge/project_normalizer.py`）：

| 层 | 手段 | 解决什么 |
|---|---|---|
| 第 1 层 | 规则化容错（零成本、确定性） | 无一级标题 → 回退文件名；`###` 等任意层级标题均作切分点；剥离代码围栏 |
| 第 2 层 | 大模型结构化（仅当章节数 < 2 时触发） | 散乱文本重组为标准章节、提炼「一句话简介」「简历描述」 |

**第 2 层的安全边界（设计底线，不可放宽）**：
- 提示词硬约束:禁止编造 / 禁止夸大 / 禁止推断补全 / 禁止改数 / 禁止丢失技术名词与数字
- 温度 0.2（要稳定复述，不要创造性）
- 任何失败（调用异常、输出为空、输出缺一级标题）一律**回退原文**，绝不因归一化失败而拒收文档

- 请求:`multipart/form-data`,字段 `file`。校验同 §4.21c（扩展名 400 / 大小 413 / 配额 409）。
- 响应 `200`:`ProjectPreprocessOut`
  - `preview_id` — 暂存 ID，确认时回传
  - `project_name` — 解析出的项目名
  - `original_text` / `normalized_text` — 原文与整理结果（前端可对照展示）
  - `normalized` / `reason` — 是否经大模型整理及原因（未整理时说明为何跳过或为何回退）
  - `has_resume_section` — 整理结果是否含「简历描述」章节

### 4.21c POST /api/project/confirm 🔒

预览确认后入库。

- 请求体:`ProjectConfirmIn` = `{preview_id, text?}`;`text` 为用户在预览中编辑过的内容,不传则用整理结果（传空白视为不改，回退整理结果，绝不入库空文档）。
- 响应 `202`:`ProjectUploadAck`,随后进度流同 §4.23。
- 行为:校验预览归属 → 文本落盘 → 写 `build_tasks` → 投递 Celery `project` 队列 → **预览立即失效**（防重复提交）。
- 错误:预览不存在/过期/非本人 → `404`;文本为空 → `400`。

### 4.22 GET /api/project/upload/{task_id} 🔒

- 响应 `200`:`ProjectUploadStatus`(task_id / status / stage / percent / message / filename / error / created_at / finished_at)。
- 不存在或非本人任务 → `404`。

### 4.23 GET /api/project/upload/{task_id}/stream(SSE) 🔒

- 事件协议同 §4.8(`progress` / `log` / `done` / `error`)。
- 不存在或非本人任务 → `404`(在建立流之前返回)。

### 4.24 GET /api/project/docs 🔒

- 响应 `200`:`ProjectDocList` = `{"docs": [ProjectDocOut], "total": N, "max_docs": M}`
- `ProjectDocOut`:`doc_id` / `project_name` / `file_name` / `tags` /
  `has_resume_section`(false 表示简历生成降级用全文)/ `is_public`(公共素材)/ 创建更新时间。
- 列表 = 公共素材 + 我上传的。

### 4.25 DELETE /api/project/docs/{doc_id} 🔒

- 响应 `204`。
- 越权(他人的文档)、公共素材、不存在 —— **统一 `404`**,不泄露存在性。
- 行为:先取记录(行删了就没 `doc_id` 可用于定位)→ 清四类磁盘产物 → 删 DB 行。

### 4.26 POST /api/project/docs/batch-delete 🔒

批量删除。用 POST 而非 `DELETE`+body——**部分反向代理会丢弃 DELETE 请求体**。

- 请求体:`ProjectDocBatchDeleteIn` = `{"doc_ids": ["...", "..."]}`,1~100 个,后端保序去重。
- 响应 `200`:`ProjectDocBatchDeleteResult` = `{"deleted_ids": [...], "not_found_ids": [...]}`
- **越权/不存在的 id 不报错**,静默跳过并回显在 `not_found_ids`——与单条删除「不泄露存在性」一致,
  也避免整批因一个坏 id 而失败。
- 行为:一次查询按 `user_id` 过滤归属 → 批量删行 → 逐个清四类磁盘产物。

前端约定:仅对已删除的 id 做本地移除,被跳过的保留在列表并提示数量;
公共素材不可勾选(后端也会跳过)。

## 5. 阶段进度映射(build-all)

`knowledge_service` 在各阶段调用 `progress_callback(stage, message, percent)`,`build-all` 的推荐百分比:

| stage | message | percent |
|---|---|---|
| `parse-pdfs` | [1/5] 解析PDF... | 10 |
| `extract-summaries` | [2/5] 提取课程摘要... | 35 |
| `split-chunks` | [3/5] 文本分块... | 55 |
| `ingest-projects` | [4/5] 项目文档入库... | 70 |
| `build-indexes` | [5/5] 构建索引... | 90 |
| `done` | 知识库构建完成 | 100 |

单任务(如只 `parse-pdfs`)进度按该任务内部阶段折算。

## 6. 前端 TS 类型派生(示例)

`frontend/src/types/events.ts` 依据本契约定义(判别联合):

```ts
export type Intent = 'quick_response' | 'deep_thinking' | 'chitchat'
export type TaskKind =
  | 'build-all' | 'parse-pdfs' | 'extract-summaries'
  | 'split-chunks' | 'build-indexes' | 'ingest-projects'
export type TaskState = 'pending' | 'running' | 'success' | 'failed'

export type ChatEvent =
  | { type: 'conversation'; conversation_id: number; stream_id: string }
  | { type: 'status'; text: string }
  | { type: 'intent'; value: Intent }
  | { type: 'token'; text: string }
  | { type: 'done'; intent: Intent; step: string;
      resume_final?: string; retrieved_knowledge?: string; retrieved_projects?: string }
  | { type: 'error'; message: string }

export type TaskEvent =
  | { type: 'progress'; stage: string; message: string; percent: number }
  | { type: 'log'; line: string }
  | { type: 'done'; task_id: string; status: 'success' | 'failed'; elapsed: number }
  | { type: 'error'; message: string }

// 简历解析任务(异步):复用 TaskState,不含 task 字段
export interface ResumeParseAck { task_id: string; status: TaskState }
export interface ResumeParseStatus {
  task_id: string; status: TaskState;
  stage?: string; percent?: number; message?: string;
  filename?: string; content?: string; error?: string;
  created_at: number; finished_at?: number;
}
```

前端 SSE 解析器(`frontend/src/composables/sse.ts`)需扩展:`SSEFrame` 增加可选 `id?: string` 字段,`parseFrame` 解析 `id:` 行(当前实现显式忽略 id/retry,需改为保留 id);`useChatStream` 记录最后 `seq`(=parseInt(id))与首帧 `stream_id`,非正常结束时用它们请求 §4.2.1 自动重连,并对 `seq <= lastSeq` 的帧去重。

## 7. 兼容性说明

- **`POST /api/resume/parse` 破坏性变更**:由同步返回解析内容改为异步任务(`202` + `task_id`);前端上传流程随之改为「提交 → SSE/轮询进度 → 成功后取 `content`」,旧同步行为不再保留。
- **简历由"多份"收敛为"每人一份"(破坏性)**:`GET /api/resume/parses`(列表)与 `DELETE /api/resume/parse/{task_id}` 由 `GET /api/resume/current` / `DELETE /api/resume/current`(§4.5b/§4.4b)取代,`ResumeParseList`/`ResumeParseItem` 出参模型删除、新增 `ResumeCurrent`(正文内联)。旧前端按 `task_id` 定位的写法不再适用;`ResumeParseStatus`(§4.4/§4.5 的进度与结果快照)保持不变,上传链路本身不受影响。存量多行数据由"取最近一条 + 删除时一次清"自愈,无需迁移。
- **`TaskKind` 与前端联合对齐为 `ingest-projects`(破坏性)**:CLI/服务层早已把"项目亮点入库"改名为 `ingest-projects`,前端 `types/events.ts` 与 `KnowledgeAdmin.vue` 仍发 `ingest-highlights`,该值会被 `BuildRequest.task` 判 `422`(即管理员点那个按钮必然失败)。现以出参模型为准改正前端。库里遗留的 `task="ingest-highlights"` 行不再出现在知识库任务列表(§4.10),它本就不是当前可触发的任务类型;项目文档入库的历史行为单数 `ingest-project`,同样只归 §4.2x 的项目端点可见。
- **`DELETE /api/conversations/{id}`(§4.12b)为纯增量端点**:既有 `#11`/`#12` 两个 GET 的契约不变,老前端不删会话照常工作。级联删消息依赖 ORM,不涉及表结构与迁移。
- 现有 `app_streamlit.py` 与 `main.py` CLI 保留;`main.py` 的 `cmd_*` 委托 `knowledge_service`,CLI 行为不变(有回归测试)。
- 对话 SSE 事件与 `run_stream()` 现有事件字典一一对应,后端仅做"字典 → SSE 帧"的封装,不改动 Agent 逻辑(事件序号 seq 与 stream_id 为**外层封装**新增,不侵入 run_stream 事件本身)。
- **对话流断线重放(§4.2/§4.2.1)向后兼容**:`POST /api/chat` 仅在 `conversation` 首帧增加 `stream_id` 字段、每帧可选带 `id:` 序号;不消费重连的旧前端忽略这两者即可正常工作(事件名/顺序/既有字段不变)。新增 `GET /api/chat/{stream_id}/stream` 为纯增量端点。后端**当前为"降级实现"**(断线生成终止、仅重放已产出);"完全后台化"(断线生成继续)为未来可选升级,前端无感知(见 §4.2.1「实现强度说明」)。**前端降级/保全策略**:①页面内断网 → 用内存中的 `stream_id`+`seq` 自动重连续传(§4.2.1);②重连遇 `404`(缓冲过期)或多次失败 → 不自动重发,保全本地已生成内容(标记中断 + 写草稿);③刷新 → 从 sessionStorage 草稿恢复已生成内容。三者共同保证"已生成内容保全"验收(可选增强:404 时经 §4.12 拉取落库的完整消息回填,当前未做)。
