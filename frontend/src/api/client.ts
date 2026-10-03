import type {
  BuildAck,
  BuildRequest,
  ConversationList,
  ConversationMessages,
  Health,
  ResumeParseAck,
  ResumeParseStatus,
  SupportedExtensions,
  TaskList,
  TaskStatus,
} from '@/types/events'
import { getAccessToken, notifySessionLost } from './authToken'
import { refreshAccessToken } from './auth'

/**
 * REST 客户端(契约 3/4,非 SSE 接口)。
 * SSE 接口(/chat、/tasks/{id}/stream)由 composables 用 fetch 流式消费,不走此处。
 *
 * 鉴权:每个受保护请求自动带 `Authorization: Bearer <access>`(token 见 authToken 模块),
 * 并 credentials:'include' 以携带 httpOnly refresh cookie。收到 401 时单飞刷新一次并重放原请求;
 * 刷新仍失败 → 触发会话失效回调(跳登录)并抛错。
 */
const BASE = '/api'

function authHeaders(extra?: HeadersInit): HeadersInit | undefined {
  const tok = getAccessToken()
  if (!tok) return extra
  return { ...(extra as Record<string, string>), Authorization: `Bearer ${tok}` }
}

async function request<T>(url: string, init?: RequestInit, isRetry = false): Promise<T> {
  const res = await fetch(url, {
    ...init,
    credentials: 'include',
    headers: authHeaders(init?.headers),
  })
  if (res.status === 401 && !isRetry) {
    // access token 过期/失效 → 尝试用 refresh cookie 换新的,成功则重放一次
    const tok = await refreshAccessToken()
    if (tok) return request<T>(url, init, true)
    notifySessionLost()
  }
  if (!res.ok) {
    // 尽量还原 FastAPI 错误体 {"detail": "..."}
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body && typeof body.detail === 'string') detail = body.detail
      else if (body && Array.isArray(body.detail)) detail = JSON.stringify(body.detail)
    } catch {
      /* 错误体非 JSON 时保留 HTTP 状态文本 */
    }
    throw new Error(detail)
  }
  return (await res.json()) as T
}

/** GET /api/health */
export function health(): Promise<Health> {
  return request<Health>(`${BASE}/health`)
}

/** GET /api/resume/supported-extensions */
export function getSupportedExtensions(): Promise<SupportedExtensions> {
  return request<SupportedExtensions>(`${BASE}/resume/supported-extensions`)
}

/**
 * POST /api/resume/parse(契约 4.3):multipart 上传,提交后台解析任务。
 * 异步化后返回 202 + `ResumeParseAck`(task_id),不再同步返回解析内容;
 * 前端拿 task_id 后订阅 SSE 进度(契约 4.5)或轮询 getResumeParseStatus(契约 4.4)。
 */
export function parseResume(file: File): Promise<ResumeParseAck> {
  const form = new FormData()
  form.append('file', file)
  return request<ResumeParseAck>(`${BASE}/resume/parse`, { method: 'POST', body: form })
}

/**
 * GET /api/resume/parse/{task_id}(契约 4.4):轮询兜底,查解析任务状态/结果。
 * 走带 401 自动刷新的 request;status==='success' 时响应带 content(解析出的 Markdown)。
 */
export function getResumeParseStatus(taskId: string): Promise<ResumeParseStatus> {
  return request<ResumeParseStatus>(`${BASE}/resume/parse/${encodeURIComponent(taskId)}`)
}

/** POST /api/knowledge/build */
export function buildKnowledge(req: BuildRequest): Promise<BuildAck> {
  return request<BuildAck>(`${BASE}/knowledge/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  })
}

/** GET /api/knowledge/tasks/{task_id}(轮询兜底) */
export function getTaskStatus(taskId: string): Promise<TaskStatus> {
  return request<TaskStatus>(`${BASE}/knowledge/tasks/${encodeURIComponent(taskId)}`)
}

/** GET /api/knowledge/tasks(历史列表,created_at 降序分页) */
export function getTaskList(page = 1, pageSize = 10): Promise<TaskList> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request<TaskList>(`${BASE}/knowledge/tasks?${qs.toString()}`)
}

/** GET /api/conversations(历史会话列表,updated_at 降序分页) */
export function getConversations(page = 1, pageSize = 20): Promise<ConversationList> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return request<ConversationList>(`${BASE}/conversations?${qs.toString()}`)
}

/** GET /api/conversations/{id}/messages(历史消息,created_at 升序) */
export function getConversationMessages(conversationId: number): Promise<ConversationMessages> {
  return request<ConversationMessages>(`${BASE}/conversations/${conversationId}/messages`)
}
