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
  | 'ingest-highlights'

export type TaskState = 'pending' | 'running' | 'success' | 'failed'

// ---- 对话 SSE 事件(契约 4.2),type 为判别字段 ----
export type ChatEvent =
  | { type: 'conversation'; conversation_id: number }
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

// ---- 任务 SSE 事件(契约 4.6),type 为判别字段 ----
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

/** 会话摘要(GET /api/conversations 列表项,契约 4.9) */
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

/** 历史消息(GET /api/conversations/{id}/messages 列表项,契约 4.10) */
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

/**
 * SSE 原始帧(解析中间态):event 为事件名,data 为单行 JSON 字符串。
 * composable 解析后合并为 { type: event, ...JSON.parse(data) }。
 */
export interface SSEFrame {
  event: string
  data: string
}
