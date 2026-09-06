import type {
  BuildAck,
  BuildRequest,
  Health,
  ParseResponse,
  SupportedExtensions,
  TaskStatus,
} from '@/types/events'

/**
 * REST 客户端(契约 3/4,非 SSE 接口)。
 * SSE 接口(/chat、/tasks/{id}/stream)由 composables 用 fetch 流式消费,不走此处。
 */
const BASE = '/api'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
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

/** POST /api/resume/parse(multipart 上传,Content-Type 由浏览器带 boundary) */
export function parseResume(file: File): Promise<ParseResponse> {
  const form = new FormData()
  form.append('file', file)
  return request<ParseResponse>(`${BASE}/resume/parse`, { method: 'POST', body: form })
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
