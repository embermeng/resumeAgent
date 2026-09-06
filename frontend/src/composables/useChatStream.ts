import { ref } from 'vue'
import { streamSSE, createEventDispatcher } from './sse'
import type { ChatEvent, ChatRequest } from '@/types/events'

export interface UseChatStreamOptions {
  /** 对话 SSE 接口地址,默认走 vite proxy 的 /api/chat */
  url?: string
  /** 每解析出一个事件即回调(用于 store 增量更新) */
  onEvent?: (event: ChatEvent) => void
}

/**
 * 对话流 composable(契约 4.2)。
 * 用 fetch + ReadableStream 消费 POST SSE(EventSource 不支持 POST,故手写)。
 * - 逐帧解析并映射为 ChatEvent(判别字段 type = SSE 事件名)
 * - 跨 chunk 半帧由 createSSEParser 缓冲
 * - error 帧 / HTTP 异常 / 网络异常统一置 error 并派发 error 事件
 * - abort() 主动中断(AbortError 不视为错误)
 */
export function useChatStream(options: UseChatStreamOptions = {}) {
  const url = options.url ?? '/api/chat'
  const streaming = ref(false)
  const error = ref<string | null>(null)
  let controller: AbortController | null = null

  async function start(req: ChatRequest): Promise<void> {
    streaming.value = true
    error.value = null
    controller = new AbortController()
    const dispatch = createEventDispatcher<ChatEvent>(
      (msg) => (error.value = msg),
      (evt) => options.onEvent?.(evt),
    )
    try {
      await streamSSE({
        url,
        signal: controller.signal,
        init: {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
          body: JSON.stringify(req),
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
