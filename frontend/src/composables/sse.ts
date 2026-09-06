import type { SSEFrame } from '@/types/events'

/**
 * 增量 SSE 帧解析器(契约 1.1)。
 * - 按 "\n\n" 切帧,支持"半帧跨 chunk"缓冲(不完整片段留到下次 feed)
 * - 解析每帧的 event: / data: 字段;data 多行按 SSE 规范以 \n 连接
 * - 忽略注释行(以 ":" 开头)与 id/retry 等无关字段
 * - feed 接受字符串或字节;字节经 TextDecoder(stream 模式)解码,
 *   多字节 UTF-8 字符即使被切到不同 chunk 也不会乱码
 */
export interface SSEParser {
  feed(chunk: Uint8Array | string): void
  flush(): void
}

function parseFrame(raw: string): SSEFrame | null {
  if (!raw.trim()) return null
  let event = 'message'
  const dataLines: string[] = []
  for (const line of raw.split('\n')) {
    if (line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      let v = line.slice('data:'.length)
      // SSE 规范:data: 后若紧跟一个空格,该空格属于分隔符需去除
      if (v.startsWith(' ')) v = v.slice(1)
      dataLines.push(v)
    }
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}

export function createSSEParser(onFrame: (frame: SSEFrame) => void): SSEParser {
  let buffer = ''
  const decoder = new TextDecoder()

  function drain(): void {
    let sep: number
    while ((sep = buffer.indexOf('\n\n')) !== -1) {
      const raw = buffer.slice(0, sep)
      buffer = buffer.slice(sep + 2)
      const frame = parseFrame(raw)
      if (frame) onFrame(frame)
    }
  }

  return {
    feed(chunk) {
      buffer += typeof chunk === 'string' ? chunk : decoder.decode(chunk, { stream: true })
      drain()
    },
    flush() {
      // 冲刷 TextDecoder 内部残留的多字节序列
      buffer += decoder.decode()
      // 处理结尾缺失 "\n\n" 的最后一帧(防御性,后端契约保证有结尾)
      if (buffer.trim()) {
        const frame = parseFrame(buffer)
        if (frame) onFrame(frame)
      }
      buffer = ''
    },
  }
}

/** 打开一个 SSE 连接并消费到流结束(fetch + ReadableStream,支持 GET/POST)。 */
export interface StreamSSEOptions {
  url: string
  init?: RequestInit
  signal?: AbortSignal
  onFrame: (frame: SSEFrame) => void
}

/**
 * 用 fetch 打开 SSE 流并逐帧回调 onFrame,直到流结束。
 * EventSource 仅支持 GET 且不能自定义头,故对话(POST)统一走此实现。
 * 抛出:HTTP 非 2xx / 网络错误 / AbortError(由调用方区分主动中断)。
 */
export async function streamSSE(opts: StreamSSEOptions): Promise<void> {
  const res = await fetch(opts.url, { ...opts.init, signal: opts.signal })
  if (!res.ok || !res.body) {
    throw new Error(`SSE 请求失败: HTTP ${res.status}`)
  }
  const parser = createSSEParser(opts.onFrame)
  const reader = res.body.getReader()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    if (value) parser.feed(value)
  }
  parser.flush()
}

/** 把 SSE 帧合并为判别联合事件:{ type: 事件名, ...JSON(data) }。data 非法 JSON 时抛错。 */
export function frameToEvent<TEvent>(frame: SSEFrame): TEvent {
  const data = frame.data ? JSON.parse(frame.data) : {}
  return { ...(data as object), type: frame.event } as TEvent
}

/**
 * 构造 onFrame 处理器:解析帧为事件并派发。
 * JSON 解析失败或收到 error 事件时,通过 onError 上报错误文本,并以 error 事件派发。
 */
export function createEventDispatcher<TEvent extends { type: string }>(
  onError: (message: string) => void,
  onEvent: (event: TEvent) => void,
): (frame: SSEFrame) => void {
  return (frame) => {
    let evt: TEvent
    try {
      evt = frameToEvent<TEvent>(frame)
    } catch {
      const msg = `SSE 数据解析失败: ${frame.data}`
      onError(msg)
      onEvent({ type: 'error', message: msg } as unknown as TEvent)
      return
    }
    if (evt.type === 'error') onError((evt as unknown as { message: string }).message)
    onEvent(evt)
  }
}
