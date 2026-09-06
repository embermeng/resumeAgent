import { ref } from 'vue'
import { streamSSE, createEventDispatcher } from './sse'
import type { TaskEvent } from '@/types/events'

export interface UseTaskStreamOptions {
  /** 每解析出一个任务事件即回调(用于 store 更新进度/日志) */
  onEvent?: (event: TaskEvent) => void
}

/**
 * 任务进度流 composable(契约 4.6)。
 * GET /api/knowledge/tasks/{id}/stream,消费 progress/log/done/error 事件。
 */
export function useTaskStream(options: UseTaskStreamOptions = {}) {
  const streaming = ref(false)
  const error = ref<string | null>(null)
  let controller: AbortController | null = null

  async function start(taskId: string): Promise<void> {
    streaming.value = true
    error.value = null
    controller = new AbortController()
    const dispatch = createEventDispatcher<TaskEvent>(
      (msg) => (error.value = msg),
      (evt) => options.onEvent?.(evt),
    )
    try {
      await streamSSE({
        url: `/api/knowledge/tasks/${encodeURIComponent(taskId)}/stream`,
        signal: controller.signal,
        init: { method: 'GET', headers: { Accept: 'text/event-stream' } },
        onFrame: dispatch,
      })
    } catch (e) {
      const err = e as { name?: string; message?: string }
      if (err?.name === 'AbortError') return
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
