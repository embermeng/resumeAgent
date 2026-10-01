# ResumeAgent 前后端分离 API 契约(SDD 规格)

本文档是后端(FastAPI)与前端(Vue3)之间的唯一真理来源(Single Source of Truth)。
任何接口/事件变更须先改本文档,再同步两端实现与测试。

## 1. 通用约定

- 基础路径:所有接口以 `/api` 前缀。
- 开发地址:后端 `http://localhost:8000`;前端 Vite dev server `http://localhost:5173`,通过 proxy 将 `/api` 转发到后端。
- 编码:请求/响应体一律 UTF-8 JSON(文件上传除外,用 `multipart/form-data`)。
- 鉴权:无(单用户本地/演示)。
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
| 2 | POST | `/api/chat` | SSE | 流式对话(问答/简历生成/闲聊) |
| 3 | POST | `/api/resume/parse` | JSON | 上传简历文件解析为 Markdown |
| 4 | GET | `/api/resume/supported-extensions` | JSON | 支持的文件扩展名 |
| 5 | POST | `/api/knowledge/build` | JSON | 触发知识库构建后台任务 |
| 6 | GET | `/api/knowledge/tasks/{task_id}/stream` | SSE | 订阅任务进度 |
| 7 | GET | `/api/knowledge/tasks/{task_id}` | JSON | 查询任务状态快照(轮询兜底) |
| 8 | GET | `/api/knowledge/tasks` | JSON | 任务列表(分页,created_at 降序) |
| 9 | GET | `/api/conversations` | JSON | 会话列表(分页,updated_at 降序) |
| 10 | GET | `/api/conversations/{id}/messages` | JSON | 查询某会话的消息历史 |

## 4. 接口详情

### 4.1 GET /api/health

- 响应 `200`:`{"status": "ok"}`

### 4.2 POST /api/chat(SSE)

- 请求体:`ChatRequest`
- 响应:`text/event-stream`,事件序列由后端 `ResumeAgent.run_stream()` 映射而来。
- 校验:`prompt` 为空 → `422`(FastAPI/Pydantic 自动)。
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

- 查询参数:`page`(默认 1,≥1)、`page_size`(默认 20,1~100)。
- 响应 `200`:`ConversationList`,按 `updated_at` 降序分页。

### 4.10 GET /api/conversations/{id}/messages

- 响应 `200`:`ConversationMessages`,消息按 `created_at` 升序(时间正序)。
- 会话不存在 → `404` `{"detail": "conversation not found"}`。

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
