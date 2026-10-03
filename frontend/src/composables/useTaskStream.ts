import { ref } from 'vue'
import { streamSSE, createEventDispatcher } from './sse'
import { ensureFreshToken } from '@/api/auth'
import { getAccessToken } from '@/api/authToken'
import type { TaskEvent } from '@/types/events'

export interface UseTaskStreamOptions {
  /**
   * 任务流地址解析器;默认知识库任务流 `/api/knowledge/tasks/{id}/stream`(契约 4.8)。
   * 简历解析传入 `(id) => \`/api/resume/parse/${id}/stream\``(契约 4.5),事件协议一致。
   */
  url?: (taskId: string) => string
  /** 每解析出一个任务事件即回调(用于更新进度/日志) */
  onEvent?: (event: TaskEvent) => void
}

const defaultUrl = (taskId: string) => `/api/knowledge/tasks/${encodeURIComponent(taskId)}/stream`

/**
 * 任务进度流 composable(知识库契约 4.8 / 简历解析契约 4.5,共用 progress/log/done/error 事件)。
 *
 * 受保护的流(如简历解析)开流前注入 `Authorization` + `credentials:'include'`,并先
 * `ensureFreshToken()`——SSE 一旦开流,中途 401 无法像普通请求那样优雅刷新重放,故临期先刷新。
 * 开放的知识库流:无 token 时 `ensureFreshToken()` 直接返回(不发请求),行为不变,向后兼容。
 */
export function useTaskStream(options: UseTaskStreamOptions = {}) {
  const resolveUrl = options.url ?? defaultUrl
  const streaming = ref(false)
  const error = ref<string | null>(null)
  let controller: AbortController | null = null

  async function start(taskId: string): Promise<void> {
    streaming.value = true
    error.value = null
    controller = new AbortController()
    // 受保护流开流前确保 access token 未临近过期(无 token 时为 no-op,不主动刷新)
    await ensureFreshToken()
    const token = getAccessToken()
    const dispatch = createEventDispatcher<TaskEvent>(
      (msg) => (error.value = msg),
      (evt) => options.onEvent?.(evt),
    )
    try {
      await streamSSE({
        url: resolveUrl(taskId),
        signal: controller.signal,
        init: {
          method: 'GET',
          credentials: 'include',
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
        },
        onFrame: dispatch,
      })
    } catch (e) {
      const err = e as { name?: string; message?: string }
      if (err?.name === 'AbortError') return // 主动中断,静默结束
      const msg = err?.message ?? String(e)
      error.value = msg
      options.onEvent?.({ type: 'error', message: msg })
    } finally {
      streaming.value = false
      controller = null
    }
  }

  function abort(): void {
    controller?.abort()
    controller = null
    streaming.value = false
  }

  return { streaming, error, start, abort }
}
