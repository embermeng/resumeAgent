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
event: <事件名>
data: <单行 JSON>

```

约定:
- `data` 必须是**单行** JSON;内容中的换行由 `json.dumps` 转义为 `\n`,不会出现裸换行。
- `data` 使用 `ensure_ascii=False`,中文原样传输。
- 客户端按 `\n\n` 切帧;需处理"半帧跨 chunk"的情况(缓冲不完整片段到下次)。

### 1.2 认证约定

- **受保护接口**:#2 `POST /api/chat`、#9 `GET /api/conversations`、#10 `GET /api/conversations/{id}/messages`、#17 `PATCH` 与 #18 `DELETE /api/auth/{user_id}`(仅限本人)。其余接口(health/resume/knowledge/#16 用户查询)开放。
- **请求头**:`Authorization: Bearer <access_token>`。SSE 受保护接口同样走此头(前端用 fetch 流式请求,不受 EventSource 无法带头的限制)。
- **JWT**:HS256 签名;payload 含 `sub`(用户 id 字符串)、`exp`、`type`(`"access"` / `"refresh"`)。密钥取自环境变量 `SECRET_KEY`(经 .env / compose env_file 注入);TTL 由 `src/config.py` 的 `AuthConfig` 配置(代码中经 `settings.auth.*` 访问):`access_token_expire_minutes`(默认 1 分钟,便于演示过期自动刷新闭环)、`refresh_token_expire_minutes`(默认 7×24×60)。
- **401 矩阵**(受保护接口):
  | 情形 | 状态码 | detail |
  |---|---|---|
  | 缺失 Authorization 头 | 401 | `Not authenticated`(OAuth2PasswordBearer 内置文案) |
  | 解码失败/已过期/`type≠access`/`sub` 非整数 | 401 | `Invalid or expired token` |
  | token 指向的用户已不存在 | 401 | `User not found` |
- **refresh token 载体**:不进 JSON 响应体,经 `Set-Cookie` 下发——名 `refresh-token`、`HttpOnly`(JS 不可读,收窄 XSS 面)、`SameSite=Strict`(防 CSRF)、`Path=/api/auth`(只随认证端点发送)、`Max-Age` = refresh_token_expire_minutes×60;`Secure` 开发(http)为 false、生产(https)为 true。dev(vite proxy)与 prod(静态托管)均同源部署,cookie 天然可用。
- **轮换与复用检测(jti + family)**:库 `refresh_tokens` 表不存 token 本体,只存其 payload 里的随机 `jti` 与所属 `family_id`(一次登录派生的所有 refresh 同属一个 family)。每次调 `/api/auth/refresh`:吊销旧 jti → 签发新对(新 refresh 沿用 family_id) → Set-Cookie 覆盖。**已吊销的 jti 被再次使用 = token 泄露信号 → 吊销整个 family**。前端收到 refresh 接口的 401 应直接登出(不做重试,防死循环)。
- **归属判据**:目标会话不属于当前用户 → `404`(与"不存在"同判据,**不用 403**,避免泄露资源存在性)。

## 2. 数据模型

以下模型同时对应后端 Pydantic(`src/api/schemas_api.py`)与前端 TS 类型(`frontend/src/types/events.ts`)。

### 2.1 请求/响应模型

| 模型 | 字段 | 类型 | 说明 |
|---|---|---|---|
| `ChatRequest` | `prompt` | string | 用户输入,必填,非空 |
| | `existing_resume` | string? | 已有简历 Markdown 文本,可选 |
| | `conversation_id` | int? | 所属会话 id;为空则服务端新建会话并经 `conversation` 事件回传 |
| `ParseResponse` | `filename` | string | 原始文件名 |
| | `content` | string | 解析出的 Markdown 文本 |
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

`Token` **不含 `expires_in`**:前端采用 **401 驱动刷新**——受保护请求收到 401 时先调 `/api/auth/refresh`,成功则用新 access 重放原请求,失败则登出(开发期 access TTL 仅 1 分钟,此路径会被高频走到,天然完成验收)。

### 2.2 枚举

- `TaskKind`(构建任务类型,对应现有 CLI 命令):
  `"build-all" | "parse-pdfs" | "extract-summaries" | "split-chunks" | "build-indexes" | "ingest-highlights"`
- `TaskState`(任务状态):
  `"pending" | "running" | "success" | "failed"`
- `Intent`(意图,对齐 `run_stream` 的 intent 事件):
  `"quick_response" | "deep_thinking" | "chitchat"`

## 3. 接口清单

| # | 方法 | 路径 | 类型 | 说明 |
|---|---|---|---|---|
| 1 | GET | `/api/health` | JSON | 健康检查 |
| 2 | POST | `/api/chat` | SSE | 流式对话(问答/简历生成/闲聊)🔒 |
| 3 | POST | `/api/resume/parse` | JSON | 上传简历文件解析为 Markdown |
| 4 | GET | `/api/resume/supported-extensions` | JSON | 支持的文件扩展名 |
| 5 | POST | `/api/knowledge/build` | JSON | 触发知识库构建后台任务 |
| 6 | GET | `/api/knowledge/tasks/{task_id}/stream` | SSE | 订阅任务进度 |
| 7 | GET | `/api/knowledge/tasks/{task_id}` | JSON | 查询任务状态快照(轮询兜底) |
| 8 | GET | `/api/knowledge/tasks` | JSON | 任务列表(分页,created_at 降序) |
| 9 | GET | `/api/conversations` | JSON | 会话列表(分页,updated_at 降序)🔒 |
| 10 | GET | `/api/conversations/{id}/messages` | JSON | 查询某会话的消息历史 🔒 |
| 11 | POST | `/api/auth/register` | JSON | 注册新用户 |
| 12 | POST | `/api/auth/login` | JSON | 登录,签发 token 对 |
| 13 | POST | `/api/auth/refresh` | JSON | 用 cookie 中的 refresh token 轮换新 token 对 🍪 |
| 14 | GET | `/api/auth/me` | JSON | 当前用户信息 🔒 |
| 15 | POST | `/api/auth/logout` | JSON | 登出:吊销整个 token family(不清 cookie,见 §4.15)🍪 |
| 16 | GET | `/api/auth/user/{user_id}` | JSON | 查询用户公开信息(UserPublic) |
| 17 | PATCH | `/api/auth/{user_id}` | JSON | 修改本人 username/email 🔒 |
| 18 | DELETE | `/api/auth/{user_id}` | JSON | 删除本人账号(级联删会话/消息/refresh_tokens)🔒 |

🔒 = 需 `Authorization: Bearer <access_token>`(见 §1.2);🍪 = 读/写 `refresh-token` httpOnly cookie。

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

对话 SSE 事件协议:

```
event: conversation data: {"conversation_id":42}
event: status   data: {"text":"正在识别意图..."}
event: intent   data: {"value":"quick_response"}
event: token    data: {"text":"RAG"}
event: done     data: {"intent":"quick_response","step":"quick_response_done","resume_final":"","retrieved_knowledge":"...","retrieved_projects":""}
event: error    data: {"message":"..."}
```

事件字段说明:
- `conversation`:`{conversation_id: int}` **流首帧**,回传本次对话所属会话 id(新建或沿用);前端存 sessionStorage,后续消息经 `ChatRequest.conversation_id` 回传以归入同一会话。
- `status`:`{text}` 阶段进度提示(深思路径会有多条)。
- `intent`:`{value: Intent}` 意图识别结果,通常在首个 status 之后到达。
- `token`:`{text}` 回答文本增量(quick_response/chitchat 逐块;deep_thinking 一次性给出整份简历文本)。
- `done`:结束帧,携带 `{intent, step, resume_final?, retrieved_knowledge?, retrieved_projects?}`;`resume_final` 非空表示生成了简历(前端提供预览/下载)。
- `error`:`{message}` 流中异常。

事件顺序保证:
- 一定以 `conversation` 开头(恰一次),其后 `status`,以 `done` 或 `error` 结尾。
- `intent` 恰出现一次,在首个 `status` 之后。
- `token` 出现 0 次或多次。

### 4.3 POST /api/resume/parse

- 请求:`multipart/form-data`,字段 `file`(二进制)。
- 响应 `200`:`ParseResponse` = `{"filename": "...", "content": "<markdown>"}`
- 错误:
  - 不支持的扩展名 → `400` `{"detail": "不支持的简历文件格式: ..."}`
  - 解析结果为空/解析失败 → `400` `{"detail": "..."}`
  - 未携带文件 → `422`

### 4.4 GET /api/resume/supported-extensions

- 响应 `200`:`{"extensions": [".md", ".txt", ".docx", ".pdf"]}`

### 4.5 POST /api/knowledge/build

- 请求体:`BuildRequest`
- 响应 `202`:`BuildAck` = `{"task_id": "<uuid>", "status": "pending"}`
- 行为:创建任务并入内存任务表,启动后台线程执行 `knowledge_service`,立即返回。
- 错误:非法 `task` → `422`。

### 4.6 GET /api/knowledge/tasks/{task_id}/stream(SSE)

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

### 4.7 GET /api/knowledge/tasks/{task_id}

- 响应 `200`:`TaskStatus` 快照。
- 不存在 → `404` `{"detail": "task not found"}`

### 4.8 GET /api/knowledge/tasks

- 查询参数:`page`(默认 1,≥1)、`page_size`(默认 10,1~100)。
- 响应 `200`:`TaskList`,直接查 `build_tasks` 表,按 `created_at` 降序分页。

### 4.9 GET /api/conversations

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 查询参数:`page`(默认 1,≥1)、`page_size`(默认 20,1~100)。
- 响应 `200`:`ConversationList`,**仅返回当前用户的会话**,按 `updated_at` 降序分页。

### 4.10 GET /api/conversations/{id}/messages

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 响应 `200`:`ConversationMessages`,消息按 `created_at` 升序(时间正序)。
- 会话不存在**或不属于当前用户** → `404` `{"detail": "conversation not found"}`(同判据,不泄露存在性)。

### 4.11 POST /api/auth/register

- 请求体:`UserCreate`(username 1~50、email EmailStr、password ≥8,不满足 → `422`)。
- 响应 `201`:`UserPrivate`(id/username/email)。
- 错误:用户名已存在 → `409` `{"detail": "Username already exists"}`;邮箱已注册 → `409` `{"detail": "Email already registered"}`(均大小写不敏感查重)。
- 行为:email 小写归一后入库;密码经 argon2id(pwdlib `PasswordHash.recommended()`)哈希后落 `users.password_hash` 列;**注册不自动登录**,前端注册成功后引导调 login。

### 4.12 POST /api/auth/login

- 请求体:`UserLoginReq`(email + password)。
- 响应 `200`:`Token`;同时 `Set-Cookie: refresh-token=<JWT>`(属性见 §1.2,`Path=/api/auth`)。
- 行为:按 email 大小写不敏感查用户;生成新 `family_id`(登录 = 新 family 的起点),落首条 `refresh_tokens` 记录(jti/family_id/expire_at)。
- 错误:邮箱或密码错误 → `401` `{"detail": "Incorrect email or password"}`(统一文案,不区分哪个字段错,避免账号枚举)。

### 4.13 POST /api/auth/refresh

- 请求:无 body,从 cookie 读 `refresh-token`。
- 响应 `200`:`Token`(新 access);同时 Set-Cookie 覆盖为新 refresh(沿用原 family_id)。
- 行为:解码校验 `type=refresh` → 按 `jti` 查 `refresh_tokens` 表:
  - 无记录 → `401`;
  - `revoked=true` → **复用检测**:吊销整个 family,`401`;
  - 正常 → 吊销旧 jti、插入新 jti 记录(同 family)、签发新对。
- 错误:cookie 缺失 → `401` `{"detail": "Refresh token is missing"}`;解码失败/类型不符/表中无该 jti → `401` `{"detail": "Invalid refresh token"}`;jti 已吊销仍被使用(复用检测) → `401` `{"detail": "Refresh token is revoked"}`。

### 4.14 GET /api/auth/me

- 鉴权:需 Bearer access token(401 见 §1.2)。
- 响应 `200`:`UserPrivate`。

### 4.15 POST /api/auth/logout

- 请求:无 body,从 cookie 读 `refresh-token`。
- 行为与响应:无 cookie → `204`(幂等);cookie 有效 → 按 jti 找到 family,**吊销整个 family** → `204`;cookie 存在但解码失败 → `401` `{"detail": "Invalid refresh token"}`。
- **不清 cookie**:服务端吊销已足够——cookie 里的 refresh token 成为死票,后续 `/api/auth/refresh` 一律 401,前端据此登出。

### 4.16 GET /api/auth/user/{user_id}

- 公开接口(无鉴权)。响应 `200`:`UserPublic`(仅 id/username,不含 email)。
- 错误:用户不存在 → `404` `{"detail": "User not found"}`。

### 4.17 PATCH /api/auth/{user_id}

- 鉴权:需 Bearer access token,且 `user_id` 必须为本人。
- 请求体:`UserUpdate`(username/email 均可省略,只改提供的字段)。
- 响应 `200`:`UserPrivate`。
- 错误:非本人 → `403` `{"detail": "Not authorized to update this user"}`;用户不存在 → `404`;新用户名被占用 → `409` `{"detail": "Username already exists"}`;新邮箱被注册 → `409` `{"detail": "Email already registered"}`(与自身现值相同不算冲突)。
- 行为:email 小写归一;username/email 查重均大小写不敏感。

### 4.18 DELETE /api/auth/{user_id}

- 鉴权:需 Bearer access token,且 `user_id` 必须为本人。
- 响应 `204`。错误:非本人 → `403` `{"detail": "Not authorized to delete this user"}`;用户不存在 → `404`。
- 行为:删除用户,ORM `cascade="all, delete-orphan"` 级联删除其全部 conversations(含 messages)与 refresh_tokens。

## 5. 阶段进度映射(build-all)

`knowledge_service` 在各阶段调用 `progress_callback(stage, message, percent)`,`build-all` 的推荐百分比:

| stage | message | percent |
|---|---|---|
| `parse-pdfs` | [1/5] 解析PDF... | 10 |
| `extract-summaries` | [2/5] 提取课程摘要... | 35 |
| `split-chunks` | [3/5] 文本分块... | 55 |
| `ingest-highlights` | [4/5] 项目亮点入库... | 70 |
| `build-indexes` | [5/5] 构建索引... | 90 |
| `done` | 知识库构建完成 | 100 |

单任务(如只 `parse-pdfs`)进度按该任务内部阶段折算。

## 6. 前端 TS 类型派生(示例)

`frontend/src/types/events.ts` 依据本契约定义(判别联合):

```ts
export type Intent = 'quick_response' | 'deep_thinking' | 'chitchat'
export type TaskKind =
  | 'build-all' | 'parse-pdfs' | 'extract-summaries'
  | 'split-chunks' | 'build-indexes' | 'ingest-highlights'
export type TaskState = 'pending' | 'running' | 'success' | 'failed'

export type ChatEvent =
  | { type: 'conversation'; conversation_id: number }
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
```

## 7. 兼容性说明

- 现有 `app_streamlit.py` 与 `main.py` CLI 保留;`main.py` 的 `cmd_*` 委托 `knowledge_service`,CLI 行为不变(有回归测试)。
- 对话 SSE 事件与 `run_stream()` 现有事件字典一一对应,后端仅做"字典 → SSE 帧"的封装,不改动 Agent 逻辑。
