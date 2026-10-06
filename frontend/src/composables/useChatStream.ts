import { ref } from 'vue'
import { streamSSE, createEventDispatcher, SSEHttpError } from './sse'
import { ensureFreshToken } from '@/api/auth'
import { getAccessToken } from '@/api/authToken'
import type { ChatEvent, ChatRequest, SSEFrame } from '@/types/events'

export interface UseChatStreamOptions {
  /** 对话 SSE 接口地址,默认走 vite proxy 的 /api/chat;断线重连在其后拼 /{stream_id}/stream */
  url?: string
  /** 每解析出一个事件即回调(用于 store 增量更新) */
  onEvent?: (event: ChatEvent) => void
  /** 进入/退出「恢复中」(断线重连)状态时回调,驱动 UI 提示 */
  onReconnectingChange?: (reconnecting: boolean) => void
  /**
   * 自动重连最终失败,应降级为「保全本地已生成内容」而非从头重发:
   * - 'expired':云端缓冲过期/不存在(重连 GET 返回 404),已无法续传
   * - 'exhausted':连续重连超过 maxReconnects 次仍失败
   */
  onReconnectFailed?: (reason: 'expired' | 'exhausted') => void
  /** 单次流最大自动重连次数,默认 3;每次重连前按 attempt 递增退避 */
  maxReconnects?: number
  /** 重连退避基准 ms(第 n 次重连前等待 base*n);默认 400,测试可注入极小值加速 */
  reconnectBaseDelayMs?: number
}

/** 重连退避基准:第 n 次重连前等待 base*n ms(默认 400/800/1200...) */
const DEFAULT_RECONNECT_BASE_DELAY_MS = 400

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms)
    // 退避等待期间被 abort 也要立即醒来,避免 stop() 后仍空等
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        resolve()
      },
      { once: true },
    )
  })
}

function isAbort(e: unknown): boolean {
  return (e as { name?: string })?.name === 'AbortError'
}

function errMsg(e: unknown): string {
  return (e as { message?: string })?.message ?? String(e)
}

/**
 * 对话流 composable(契约 4.2 + 断线重连)。
 * 用 fetch + ReadableStream 消费 POST SSE(EventSource 不支持 POST,故手写)。
 * - 逐帧解析并映射为 ChatEvent(判别字段 type = SSE 事件名);跨 chunk 半帧由 createSSEParser 缓冲
 * - conversation 首帧带回 stream_id;每帧 SSE id 承载单调递增 seq
 * - 断线(网络错误)后:若已拿到 stream_id,自动 GET /{stream_id}/stream?after={lastSeq} 续传,
 *   按 seq 去重(重放会带回已应用帧),收到终态帧即完成
 * - 重连遇 404(缓冲过期)→ onReconnectFailed('expired');连续超过 maxReconnects 次 → 'exhausted'
 * - error 帧 / HTTP 异常 → 置 error 并派发 error 事件;abort() 主动中断不视为错误
 */
export function useChatStream(options: UseChatStreamOptions = {}) {
  const url = options.url ?? '/api/chat'
  const maxReconnects = options.maxReconnects ?? 3
  const baseDelay = options.reconnectBaseDelayMs ?? DEFAULT_RECONNECT_BASE_DELAY_MS
  const streaming = ref(false)
  const error = ref<string | null>(null)
  /** 断线重连进行中(「恢复中」);UI 据此提示,区别于正常流式 */
  const reconnecting = ref(false)
  let controller: AbortController | null = null

  // 断线重连游标(每次 start 重置):stream_id 来自 conversation 首帧,lastSeq 来自帧 SSE id
  let streamId: string | null = null
  let lastSeq = -1

  function setReconnecting(v: boolean) {
    if (reconnecting.value === v) return
    reconnecting.value = v
    options.onReconnectingChange?.(v)
  }

  function authHeader(): Record<string, string> {
    const t = getAccessToken()
    return t ? { Authorization: `Bearer ${t}` } : {}
  }

  function fail(msg: string) {
    error.value = msg
    options.onEvent?.({ type: 'error', message: msg })
  }

  // 派发器:捕获 conversation 帧的 stream_id 供重连;其余原样交给 onEvent
  const dispatch = createEventDispatcher<ChatEvent>(
    (msg) => (error.value = msg),
    (evt) => {
      if (evt.type === 'conversation' && evt.stream_id) streamId = evt.stream_id
      options.onEvent?.(evt)
    },
  )

  // 帧处理:先按 seq 去重(重连重放会带回已应用帧),再派发
  function onFrame(frame: SSEFrame) {
    if (frame.id !== undefined) {
      const s = Number(frame.id)
      if (Number.isFinite(s)) {
        if (s <= lastSeq) return // 已应用过,丢弃
        lastSeq = s
      }
    }
    dispatch(frame)
  }

  /** 断线后自动重连:GET /{stream_id}/stream?after={lastSeq} 续传,直到成功/降级/主动中断 */
  async function reconnect(signal: AbortSignal): Promise<void> {
    setReconnecting(true)
    for (let attempt = 1; attempt <= maxReconnects; attempt++) {
      await sleep(baseDelay * attempt, signal)
      if (signal.aborted) return
      try {
        // 重连同样受保护:开流前续期 token(SSE 中途 401 无法优雅重试)
        await ensureFreshToken()
        const reconnectUrl = `${url}/${encodeURIComponent(streamId!)}/stream?after=${lastSeq}`
        await streamSSE({
          url: reconnectUrl,
          signal,
          init: {
            method: 'GET',
            credentials: 'include',
            headers: { Accept: 'text/event-stream', ...authHeader() },
          },
          onFrame,
        })
        // 重连流正常读到终态帧结束:续传成功
        setReconnecting(false)
        return
      } catch (e) {
        if (isAbort(e)) return // 主动中断,静默结束
        if (e instanceof SSEHttpError && e.status === 404) {
          // 云端缓冲过期/不存在:无法续传,交 store 降级(保全本地已生成内容)
          setReconnecting(false)
          options.onReconnectFailed?.('expired')
          return
        }
        // 其它(网络仍不通):进入下一轮退避重连
      }
    }
    // 超过最大重连次数仍失败
    setReconnecting(false)
    options.onReconnectFailed?.('exhausted')
  }

  async function start(req: ChatRequest): Promise<void> {
    streaming.value = true
    error.value = null
    streamId = null
    lastSeq = -1
    controller = new AbortController()
    const signal = controller.signal
    // /api/chat 受保护:开流前先确保 access token 未临近过期
    await ensureFreshToken()
    try {
      await streamSSE({
        url,
        signal,
        init: {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': 'application/json',
            Accept: 'text/event-stream',
            ...authHeader(),
          },
          body: JSON.stringify(req),
        },
        onFrame,
      })
      // POST 流正常读到终态帧结束
    } catch (e) {
      if (isAbort(e)) return // 主动中断,静默结束
      // POST 开流即失败(HTTP 非 2xx):无流可续,直接报错
      if (e instanceof SSEHttpError) {
        fail(errMsg(e))
        return
      }
      // 其余为网络中断:已拿到 stream_id 则自动重连续传,否则降级报错
      if (streamId) {
        await reconnect(signal)
        return
      }
      fail(errMsg(e))
    } finally {
      streaming.value = false
      setReconnecting(false)
      controller = null
    }
  }

  function abort(): void {
    controller?.abort()
    controller = null
    streaming.value = false
    setReconnecting(false)
  }

  return { streaming, error, reconnecting, start, abort }
}
