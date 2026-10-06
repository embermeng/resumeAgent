import { describe, it, expect, vi, afterEach } from 'vitest'
import { useChatStream } from './useChatStream'
import type { ChatEvent } from '@/types/events'

const enc = new TextEncoder()

/** 构造一个假的流式 Response:body.getReader() 按 chunks 逐块吐出 */
function sseResponse(
  chunks: (string | Uint8Array)[],
  init: { ok?: boolean; status?: number } = {},
): Response {
  let i = 0
  const body = {
    getReader() {
      return {
        async read() {
          if (i >= chunks.length) return { done: true, value: undefined }
          const c = chunks[i++]
          return { done: false, value: typeof c === 'string' ? enc.encode(c) : c }
        },
      }
    },
  }
  return { ok: init.ok ?? true, status: init.status ?? 200, body } as unknown as Response
}

/** 构造一个「中途断线」的假流:先吐 chunks,吐完后在下次 read() 时 reject(模拟网络中断) */
function sseDropResponse(
  chunks: (string | Uint8Array)[],
  err: Error = Object.assign(new Error('network error'), { name: 'TypeError' }),
): Response {
  let i = 0
  const body = {
    getReader() {
      return {
        async read() {
          if (i < chunks.length) {
            const c = chunks[i++]
            return { done: false, value: typeof c === 'string' ? enc.encode(c) : c }
          }
          throw err
        },
      }
    },
  }
  return { ok: true, status: 200, body } as unknown as Response
}

function stubFetch(impl: (url: string, opts?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useChatStream', () => {
  it('按序分发 status/intent/token/done,token 增量可拼接', async () => {
    const events: ChatEvent[] = []
    stubFetch(async () =>
      sseResponse([
        'event: status\ndata: {"text":"正在识别意图..."}\n\n',
        'event: intent\ndata: {"value":"quick_response"}\n\n',
        'event: token\ndata: {"text":"RA"}\n\n',
        'event: token\ndata: {"text":"G"}\n\n',
        'event: done\ndata: {"intent":"quick_response","step":"quick_response_done"}\n\n',
      ]),
    )
    const { start, streaming } = useChatStream({ onEvent: (e) => events.push(e) })
    await start({ prompt: '什么是RAG' })

    expect(events.map((e) => e.type)).toEqual(['status', 'intent', 'token', 'token', 'done'])
    const text = events
      .filter((e): e is Extract<ChatEvent, { type: 'token' }> => e.type === 'token')
      .map((e) => e.text)
      .join('')
    expect(text).toBe('RAG')
    expect(streaming.value).toBe(false)
  })

  it('跨 chunk 半帧缓冲:事件名被拆开仍正确解析', async () => {
    const events: ChatEvent[] = []
    stubFetch(async () =>
      sseResponse([
        'event: status\ndata: {"text":"正在生成回答..."}\n\neve',
        'nt: token\ndata: {"text":"你好"}\n\nevent: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n',
      ]),
    )
    const { start } = useChatStream({ onEvent: (e) => events.push(e) })
    await start({ prompt: 'q' })
    expect(events.map((e) => e.type)).toEqual(['status', 'token', 'done'])
    expect((events[1] as Extract<ChatEvent, { type: 'token' }>).text).toBe('你好')
  })

  it('透传 prompt 与 existing_resume 到请求体,方法与地址正确', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse(['event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n']),
    )
    const { start } = useChatStream()
    await start({ prompt: 'hi', existing_resume: '# 我的简历' })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/chat')
    expect(opts?.method).toBe('POST')
    expect(JSON.parse(String(opts?.body))).toEqual({ prompt: 'hi', existing_resume: '# 我的简历' })
    expect((opts?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('收到 error 帧:设置 error 并派发 error 事件', async () => {
    const events: ChatEvent[] = []
    stubFetch(async () =>
      sseResponse([
        'event: status\ndata: {"text":"x"}\n\n',
        'event: error\ndata: {"message":"boom"}\n\n',
      ]),
    )
    const { start, error } = useChatStream({ onEvent: (e) => events.push(e) })
    await start({ prompt: 'q' })
    expect(error.value).toBe('boom')
    expect(events.at(-1)).toEqual({ type: 'error', message: 'boom' })
  })

  it('done 帧携带 resume_final 时原样透传(供预览/下载)', async () => {
    const events: ChatEvent[] = []
    stubFetch(async () =>
      sseResponse([
        'event: done\ndata: {"intent":"deep_thinking","step":"deep_thinking_done","resume_final":"# 简历"}\n\n',
      ]),
    )
    const { start } = useChatStream({ onEvent: (e) => events.push(e) })
    await start({ prompt: '生成简历' })
    const done = events[0] as Extract<ChatEvent, { type: 'done' }>
    expect(done.type).toBe('done')
    expect(done.resume_final).toBe('# 简历')
  })

  it('HTTP 非 2xx:转为 error 事件', async () => {
    const events: ChatEvent[] = []
    stubFetch(async () => sseResponse([], { ok: false, status: 500 }))
    const { start, error } = useChatStream({ onEvent: (e) => events.push(e) })
    await start({ prompt: 'q' })
    expect(error.value).toContain('500')
    expect(events.some((e) => e.type === 'error')).toBe(true)
  })

  it('abort() 中断流:streaming 复位且不产生 error', async () => {
    const events: ChatEvent[] = []
    stubFetch(async (_url, opts) => {
      const signal = opts?.signal
      const body = {
        getReader: () => ({
          read: () =>
            new Promise<{ done: boolean; value?: Uint8Array }>((resolve, reject) => {
              setTimeout(() => {
                if (signal?.aborted) {
                  reject(Object.assign(new Error('Aborted'), { name: 'AbortError' }))
                } else {
                  resolve({ done: false, value: enc.encode('event: token\ndata: {"text":"x"}\n\n') })
                }
              }, 1)
            }),
        }),
      }
      return { ok: true, status: 200, body } as unknown as Response
    })

    const { start, abort, streaming } = useChatStream({ onEvent: (e) => events.push(e) })
    const pending = start({ prompt: 'q' })
    await new Promise((r) => setTimeout(r, 6))
    expect(streaming.value).toBe(true)
    abort()
    await pending
    expect(streaming.value).toBe(false)
    expect(events.some((e) => e.type === 'error')).toBe(false)
  })

  // ---- 断线自动重连 ----

  it('断线后自动重连:GET 带 after=lastSeq 续传,按 seq 去重,收终态完成', async () => {
    const events: ChatEvent[] = []
    const flags: boolean[] = []
    const fetchMock = stubFetch(async (url) => {
      if (url === '/api/chat') {
        // POST:conversation(stream_id,seq0)+token seq1+token seq2,随后断线
        return sseDropResponse([
          'id: 0\nevent: conversation\ndata: {"conversation_id":7,"stream_id":"sid1"}\n\n',
          'id: 1\nevent: token\ndata: {"text":"你好"}\n\n',
          'id: 2\nevent: token\ndata: {"text":"世界"}\n\n',
        ])
      }
      // GET 重连:重放 seq2(重复→去重)+seq3+终态 seq4
      return sseResponse([
        'id: 2\nevent: token\ndata: {"text":"世界"}\n\n',
        'id: 3\nevent: token\ndata: {"text":"!"}\n\n',
        'id: 4\nevent: done\ndata: {"intent":"chitchat","step":"interrupted"}\n\n',
      ])
    })
    const { start, reconnecting } = useChatStream({
      onEvent: (e) => events.push(e),
      onReconnectingChange: (v) => flags.push(v),
      reconnectBaseDelayMs: 1,
    })
    await start({ prompt: 'q' })

    // 重连 URL:GET /api/chat/sid1/stream?after=2(断点 lastSeq)
    const [getUrl, getOpts] = fetchMock.mock.calls.at(-1)!
    expect(getUrl).toBe('/api/chat/sid1/stream?after=2')
    expect(getOpts?.method).toBe('GET')
    // token 去重:"世界"只应用一次
    const text = events
      .filter((e): e is Extract<ChatEvent, { type: 'token' }> => e.type === 'token')
      .map((e) => e.text)
      .join('')
    expect(text).toBe('你好世界!')
    expect(events.at(-1)).toMatchObject({ type: 'done', step: 'interrupted' })
    // 「恢复中」true→false
    expect(flags).toEqual([true, false])
    expect(reconnecting.value).toBe(false)
  })

  it('重连遇 404(缓冲过期):onReconnectFailed(expired),不再重连、不派发 error', async () => {
    const events: ChatEvent[] = []
    const failures: string[] = []
    const fetchMock = stubFetch(async (url) => {
      if (url === '/api/chat') {
        return sseDropResponse([
          'id: 0\nevent: conversation\ndata: {"conversation_id":7,"stream_id":"sidX"}\n\n',
          'id: 1\nevent: token\ndata: {"text":"半截"}\n\n',
        ])
      }
      return sseResponse([], { ok: false, status: 404 })
    })
    const { start } = useChatStream({
      onEvent: (e) => events.push(e),
      onReconnectFailed: (r) => failures.push(r),
      reconnectBaseDelayMs: 1,
    })
    await start({ prompt: 'q' })
    expect(failures).toEqual(['expired'])
    expect(fetchMock).toHaveBeenCalledTimes(2) // POST + 一次 GET(404 后停)
    expect(events.some((e) => e.type === 'token')).toBe(true) // 断线前 token 已应用
    expect(events.some((e) => e.type === 'error')).toBe(false) // 降级不走 error 事件
  })

  it('重连持续网络失败:超过 maxReconnects 次后 onReconnectFailed(exhausted)', async () => {
    const failures: string[] = []
    let getCalls = 0
    stubFetch(async (url) => {
      if (url === '/api/chat') {
        return sseDropResponse([
          'id: 0\nevent: conversation\ndata: {"conversation_id":7,"stream_id":"sidY"}\n\n',
        ])
      }
      getCalls++
      throw Object.assign(new Error('still down'), { name: 'TypeError' })
    })
    const { start, reconnecting } = useChatStream({
      onReconnectFailed: (r) => failures.push(r),
      maxReconnects: 2,
      reconnectBaseDelayMs: 1,
    })
    await start({ prompt: 'q' })
    expect(failures).toEqual(['exhausted'])
    expect(getCalls).toBe(2) // 恰好重连 maxReconnects 次
    expect(reconnecting.value).toBe(false)
  })

  it('断线时还没拿到 stream_id:不重连,直接降级为 error 事件', async () => {
    const events: ChatEvent[] = []
    const fetchMock = stubFetch(async () =>
      sseDropResponse(['id: 0\nevent: status\ndata: {"text":"..."}\n\n']), // 无 conversation 帧
    )
    const { start, error } = useChatStream({ onEvent: (e) => events.push(e), reconnectBaseDelayMs: 1 })
    await start({ prompt: 'q' })
    expect(fetchMock).toHaveBeenCalledTimes(1) // 未发起重连
    expect(events.at(-1)).toMatchObject({ type: 'error' })
    expect(error.value).toBeTruthy()
  })
})
