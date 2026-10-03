import { describe, it, expect, afterEach, beforeEach } from 'vitest'
import * as auth from './auth'
import { clearAccessToken, getAccessToken, setAccessToken } from './authToken'
import { stubFetch, jsonResponse } from '@/test/sse-helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  clearAccessToken()
})

/** 造一个 base64url payload 为 {exp} 的假 JWT(仅解 exp,不验签) */
function fakeJwt(exp: number): string {
  const b64 = btoa(JSON.stringify({ exp, sub: '1', type: 'access' }))
  return `header.${b64}.signature`
}

describe('api/auth REST', () => {
  it('login:POST /api/auth/login 带 credentials,返回 Token', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ access_token: 'a.b.c', token_type: 'bearer' }))
    const t = await auth.login({ email: 'x@qq.com', password: 'password123' })
    expect(t.access_token).toBe('a.b.c')
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/auth/login')
    expect(opts?.method).toBe('POST')
    expect(opts?.credentials).toBe('include')
    expect(JSON.parse(String(opts?.body))).toEqual({ email: 'x@qq.com', password: 'password123' })
  })

  it('register:POST /api/auth/register 返回 UserPrivate', async () => {
    stubFetch(async () => jsonResponse({ id: 1, username: 'u', email: 'x@qq.com' }))
    const r = await auth.register({ username: 'u', email: 'x@qq.com', password: 'password123' })
    expect(r.email).toBe('x@qq.com')
  })

  it('logout:204 无响应体也 resolve', async () => {
    stubFetch(async () => ({ ok: true, status: 204, json: async () => { throw new Error('no body') } }) as unknown as Response)
    await expect(auth.logout()).resolves.toBeUndefined()
  })

  it('非 2xx:抛后端 detail(login 401)', async () => {
    stubFetch(async () => jsonResponse({ detail: 'Incorrect email or password' }, { ok: false, status: 401 }))
    await expect(auth.login({ email: 'x@qq.com', password: 'bad' })).rejects.toThrow('Incorrect email or password')
  })

  it('me:带 Authorization 头(从 token holder 取)', async () => {
    setAccessToken('tok123')
    const fetchMock = stubFetch(async () => jsonResponse({ id: 1, username: 'u', email: 'x@qq.com' }))
    await auth.me()
    const [, opts] = fetchMock.mock.calls[0]
    expect((opts?.headers as Record<string, string>).Authorization).toBe('Bearer tok123')
  })
})

describe('refreshAccessToken 单飞', () => {
  it('成功:写入 token holder 并返回新 token', async () => {
    stubFetch(async () => jsonResponse({ access_token: 'new.tok', token_type: 'bearer' }))
    const tok = await auth.refreshAccessToken()
    expect(tok).toBe('new.tok')
    expect(getAccessToken()).toBe('new.tok')
  })

  it('失败:清空 token 并返回 null(不抛)', async () => {
    setAccessToken('stale')
    stubFetch(async () => jsonResponse({ detail: 'Refresh token is revoked' }, { ok: false, status: 401 }))
    const tok = await auth.refreshAccessToken()
    expect(tok).toBeNull()
    expect(getAccessToken()).toBeNull()
  })

  it('并发只发一次 /refresh(单飞)', async () => {
    let calls = 0
    stubFetch(async () => {
      calls += 1
      return jsonResponse({ access_token: 'shared', token_type: 'bearer' })
    })
    const [a, b, c] = await Promise.all([auth.refreshAccessToken(), auth.refreshAccessToken(), auth.refreshAccessToken()])
    expect(calls).toBe(1)
    expect([a, b, c]).toEqual(['shared', 'shared', 'shared'])
  })
})

describe('decodeExp / ensureFreshToken', () => {
  beforeEach(() => clearAccessToken())

  it('decodeExp:解出 exp;非法 token 返回 null', () => {
    const exp = Math.floor(Date.now() / 1000) + 600
    expect(auth.decodeExp(fakeJwt(exp))).toBe(exp)
    expect(auth.decodeExp('garbage')).toBeNull()
  })

  it('ensureFreshToken:无 token 返回 null 且不发请求', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ access_token: 'x' }))
    expect(await auth.ensureFreshToken()).toBeNull()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ensureFreshToken:token 未临近过期 → 原样返回,不刷新', async () => {
    setAccessToken(fakeJwt(Math.floor(Date.now() / 1000) + 600))
    const fetchMock = stubFetch(async () => jsonResponse({ access_token: 'x' }))
    const before = getAccessToken()
    expect(await auth.ensureFreshToken()).toBe(before)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('ensureFreshToken:临近过期 → 刷新并返回新 token', async () => {
    setAccessToken(fakeJwt(Math.floor(Date.now() / 1000) + 5)) // 5s 后过期 < 30s skew
    stubFetch(async () => jsonResponse({ access_token: 'refreshed', token_type: 'bearer' }))
    expect(await auth.ensureFreshToken()).toBe('refreshed')
    expect(getAccessToken()).toBe('refreshed')
  })
})
