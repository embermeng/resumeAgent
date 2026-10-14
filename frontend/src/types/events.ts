/**
 * SSE / REST 契约类型(唯一真理来源: docs/specs/api-contract.md)
 * 后端对应 src/api/schemas_api.py 与 run_stream() 事件字典。
 * 变更须先改契约文档,再同步两端。
 */

// ---- 枚举(契约 2.2) ----
export type Intent = 'quick_response' | 'deep_thinking' | 'chitchat'

export type TaskKind =
  | 'build-all'
  | 'parse-pdfs'
  | 'extract-summaries'
  | 'split-chunks'
  | 'build-indexes'
  | 'ingest-projects'

export type TaskState = 'pending' | 'running' | 'success' | 'failed'

// ---- 对话 SSE 事件(契约 4.2),type 为判别字段 ----
export type ChatEvent =
  | { type: 'conversation'; conversation_id: number; stream_id?: string }
  | { type: 'status'; text: string }
  | { type: 'intent'; value: Intent }
  | { type: 'token'; text: string }
  | {
      type: 'done'
      intent: Intent
      step: string
      resume_final?: string
      resume_draft?: string
      retrieved_knowledge?: string
      retrieved_projects?: string
    }
  | { type: 'error'; message: string }

// ---- 任务 SSE 事件(知识库契约 4.8;简历解析流复用同协议,契约 4.5),type 为判别字段 ----
export type TaskEvent =
  | { type: 'progress'; stage: string; message: string; percent: number }
  | { type: 'log'; line: string }
  | { type: 'done'; task_id: string; status: 'success' | 'failed'; elapsed: number }
  | { type: 'error'; message: string }

// ---- REST 请求/响应模型(契约 2.1) ----
export interface ChatRequest {
  prompt: string
  existing_resume?: string
  /** 归属会话 id;首条消息不传,由后端建会话并经 conversation 首帧回传 */
  conversation_id?: number
}

export interface ParseResponse {
  filename: string
  content: string
}

/**
 * POST /api/resume/parse 回执(契约 4.3):提交后台解析任务后返回。
 * 异步化后不再同步返回解析内容,内容改经 ResumeParseStatus.content 下发。
 */
export interface ResumeParseAck {
  task_id: string
  status: TaskState
}

/**
 * GET /api/resume/parse/{task_id} 快照(契约 4.4)。
 * 不含 task 字段(与知识库 TaskStatus 区分);status==='success' 时带 content。
 */
export interface ResumeParseStatus {
  task_id: string
  status: TaskState
  stage?: string
  percent?: number
  message?: string
  filename?: string
  content?: string
  error?: string
  created_at: number
  finished_at?: number
}

/**
 * GET /api/resume/current(契约 4.5b):我当前的简历。每个用户只有一份,
 * content 直接随响应下发,前端挂载即自动带入生成上下文(无取消入口)。
 * 后端无简历时返回 204,前端拿到 undefined。
 */
export interface ResumeCurrent {
  filename?: string
  created_at: number
  finished_at?: number
  /** 解析出的 Markdown;产物文件丢失时为 null(此时只回显、不带入上下文) */
  content?: string | null
}

export interface SupportedExtensions {
  extensions: string[]
}

export interface BuildRequest {
  task: TaskKind
  force?: boolean
  chunk_size?: number
  chunk_overlap?: number
  prune?: boolean
}

export interface BuildAck {
  task_id: string
  status: TaskState
}

export interface TaskStatus {
  task_id: string
  task: TaskKind
  status: TaskState
  stage?: string
  percent?: number
  message?: string
  created_at: number
  finished_at?: number
  error?: string
}

/** GET /api/knowledge/tasks 分页列表(created_at 降序) */
export interface TaskList {
  tasks: TaskStatus[]
  total: number
}

/* ============================================================
 * 项目文档(契约:POST/GET /api/project/*)
 * 与后端 src/api/schemas_api.py 的 ProjectUploadAck/ProjectUploadStatus/
 * ProjectDocOut/ProjectDocList 一一对应。
 * ========================================================== */

/** POST /api/project/upload 回执:提交后台入库任务后返回(202) */
export interface ProjectUploadAck {
  task_id: string
  status: TaskState
}

/** GET /api/project/upload/{task_id} 快照(轮询兜底) */
export interface ProjectUploadStatus {
  task_id: string
  status: TaskState
  stage?: string
  percent?: number
  message?: string
  filename?: string
  error?: string
  created_at: number
  finished_at?: number
}

/** GET /api/project/docs 列表项 */
export interface ProjectDocOut {
  doc_id: string
  project_name: string
  file_name: string
  tags: string[]
  /** false 时简历生成降级为全文引用,UI 应给出提示 */
  has_resume_section: boolean
  /** true 表示公共素材(所有用户可见),不可删除 */
  is_public: boolean
  created_at: number
  updated_at: number
}

/** GET /api/project/docs 列表 */
export interface ProjectDocList {
  docs: ProjectDocOut[]
  total: number
  /** 该用户文档数上限(配额) */
  max_docs: number
}

/**
 * POST /api/project/preprocess 响应：上传后的预处理结果。
 * 前端据此弹预览——展示系统整理后的文本，用户确认（可再编辑）后才入库。
 */
export interface ProjectPreprocessOut {
  preview_id: string
  project_name: string
  file_name: string
  /** 用户原始文本（清洗后） */
  original_text: string
  /** 整理后的文本（最终采用，保证含一级标题） */
  normalized_text: string
  /** 是否经过大模型整理 */
  normalized: boolean
  /** 未整理/回退的原因 */
  reason: string
  has_resume_section: boolean
}

/** POST /api/project/confirm 请求体 */
export interface ProjectConfirmIn {
  preview_id: string
  /** 用户在预览中编辑后的文本；不传则用整理结果 */
  text?: string
}

/** POST /api/project/docs/batch-delete 请求体(1~100 个,后端自动去重) */
export interface ProjectDocBatchDeleteIn {
  doc_ids: string[]
}

/**
 * GET /api/project/template 响应:标准模板。
 * 内容与入库校验基准同源,照它写的文档能通过校验直接入库,不被大模型改写。
 */
export interface ProjectTemplateOut {
  /** 建议的下载文件名(后端给定,前端不拼) */
  file_name: string
  /** 模板 Markdown 原文 */
  content: string
}

/**
 * POST /api/project/docs/batch-delete 结果。
 * 越权/不存在的 id 不报错而是回显在 not_found_ids(与单条删除"不泄露存在性"一致)。
 */
export interface ProjectDocBatchDeleteResult {
  deleted_ids: string[]
  not_found_ids: string[]
}

/** 会话摘要(GET /api/conversations 列表项,契约 4.11) */
export interface ConversationSummary {
  id: number
  title: string
  created_at: number
  updated_at: number
}

/** GET /api/conversations 分页列表(updated_at 降序) */
export interface ConversationList {
  conversations: ConversationSummary[]
  total: number
}

/** 历史消息(GET /api/conversations/{id}/messages 列表项,契约 4.12) */
export interface MessageOut {
  id: number
  role: 'user' | 'assistant' | 'system'
  content: string
  intent: Intent | null
  created_at: number
}

/** GET /api/conversations/{id}/messages 响应 */
export interface ConversationMessages {
  conversation_id: number
  messages: MessageOut[]
}

export interface Health {
  status: string
}

// ---- 认证模型(契约 2.1 / 4.13~4.20) ----

/** POST /api/auth/register 请求体 */
export interface UserCreate {
  username: string
  email: string
  password: string
}

/** POST /api/auth/login 请求体(登录凭证为 email) */
export interface UserLoginReq {
  email: string
  password: string
}

/** 登录/刷新响应体(refresh token 只经 httpOnly cookie 下发,不在此) */
export interface Token {
  access_token: string
  token_type: string
}

/** 用户公开视图(GET /api/auth/user/{id},不含 email) */
export interface UserPublic {
  id: number
  username: string
}

/** 用户完整视图(register/me/patch 响应) */
export interface UserPrivate extends UserPublic {
  email: string
  /** 管理员标记;前端据此决定是否展示管理端入口 */
  is_superuser?: boolean
}

/** PATCH /api/auth/{id} 请求体(字段均可省略,只改提供的) */
export interface UserUpdate {
  username?: string
  email?: string
}

/**
 * SSE 原始帧(解析中间态):event 为事件名,data 为单行 JSON 字符串。
 * composable 解析后合并为 { type: event, ...JSON.parse(data) }。
 */
export interface SSEFrame {
  event: string
  data: string
  /** SSE id 字段:对话流用它承载单调递增 seq(断线重连按此去重与续传);任务流等无 id 时为 undefined */
  id?: string
}
