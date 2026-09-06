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
})
