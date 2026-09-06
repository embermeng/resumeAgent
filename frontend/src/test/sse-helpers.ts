import { vi } from 'vitest'

export const enc = new TextEncoder()

/** 构造一个假的流式 Response:body.getReader() 按 chunks 逐块吐出(可含字符串或字节) */
export function sseResponse(
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

/** 用给定实现替换全局 fetch,返回该 mock(便于断言调用参数) */
export function stubFetch(impl: (url: string, opts?: RequestInit) => Promise<Response>) {
  const fn = vi.fn(impl)
  vi.stubGlobal('fetch', fn)
  return fn
}

/** 构造一个普通 JSON Response(带 ok/status/json()) */
export function jsonResponse(data: unknown, init: { ok?: boolean; status?: number } = {}): Response {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => data,
  } as unknown as Response
}
