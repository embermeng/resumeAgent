import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'
import { clearAccessToken, getAccessToken } from '@/api/authToken'
import { stubFetch, jsonResponse } from '@/test/sse-helpers'

/** 按 URL 分发的 fetch 桩:模拟 login/me/refresh/logout 端点 */
function stubAuthServer(overrides: Partial<Record<'login' | 'me' | 'refresh' | 'logout', Response>> = {}) {
  return stubFetch(async (url: string) => {
    if (url.endsWith('/auth/register')) return jsonResponse({ id: 7, username: 'alice', email: 'a@qq.com' }, { status: 201 })
    if (url.endsWith('/auth/login')) return overrides.login ?? jsonResponse({ access_token: 'acc.tok', token_type: 'bearer' })
    if (url.endsWith('/auth/me')) return overrides.me ?? jsonResponse({ id: 7, username: 'alice', email: 'a@qq.com' })
    if (url.endsWith('/auth/refresh')) return overrides.refresh ?? jsonResponse({ access_token: 'new.tok', token_type: 'bearer' })
    if (url.endsWith('/auth/logout')) return overrides.logout ?? ({ ok: true, status: 204, json: async () => undefined } as unknown as Response)
    return jsonResponse({ detail: 'unexpected' }, { ok: false, status: 404 })
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  clearAccessToken()
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  clearAccessToken()
})

describe('auth store', () => {
  it('login:写入 access token + 载入用户 → 已认证', async () => {
    stubAuthServer()
    const auth = useAuthStore()
    await auth.login('a@qq.com', 'password123')
    expect(getAccessToken()).toBe('acc.tok')
    expect(auth.isAuthenticated).toBe(true)
    expect(auth.user?.username).toBe('alice')
    expect(auth.loading).toBe(false)
  })

  it('login 失败:抛错且保持未认证,loading 复位', async () => {
    stubAuthServer({ login: jsonResponse({ detail: 'Incorrect email or password' }, { ok: false, status: 401 }) })
    const auth = useAuthStore()
    await expect(auth.login('a@qq.com', 'bad')).rejects.toThrow('Incorrect email or password')
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.loading).toBe(false)
  })

  it('register:先注册再自动登录', async () => {
    const fetchMock = stubAuthServer()
    const auth = useAuthStore()
    await auth.register('alice', 'a@qq.com', 'password123')
    const urls = fetchMock.mock.calls.map((c) => c[0] as string)
    expect(urls.some((u) => u.endsWith('/auth/register'))).toBe(true)
    expect(urls.some((u) => u.endsWith('/auth/login'))).toBe(true)
    expect(auth.isAuthenticated).toBe(true)
  })

  it('initialize:refresh 成功 → 载入用户,标记 initialized', async () => {
    stubAuthServer()
    const auth = useAuthStore()
    await auth.initialize()
    expect(auth.initialized).toBe(true)
    expect(auth.isAuthenticated).toBe(true)
    expect(getAccessToken()).toBe('new.tok')
  })

  it('initialize:refresh 失败(无 cookie) → 未认证但仍标记 initialized', async () => {
    stubAuthServer({ refresh: jsonResponse({ detail: 'Refresh token is missing' }, { ok: false, status: 401 }) })
    const auth = useAuthStore()
    await auth.initialize()
    expect(auth.initialized).toBe(true)
    expect(auth.isAuthenticated).toBe(false)
  })

  it('initialize 幂等:第二次不再发请求', async () => {
    const fetchMock = stubAuthServer()
    const auth = useAuthStore()
    await auth.initialize()
    const n = fetchMock.mock.calls.length
    await auth.initialize()
    expect(fetchMock.mock.calls.length).toBe(n)
  })

  it('logout:调后端 + 清本地态', async () => {
    stubAuthServer()
    const auth = useAuthStore()
    await auth.login('a@qq.com', 'password123')
    await auth.logout()
    expect(auth.isAuthenticated).toBe(false)
    expect(auth.user).toBeNull()
    expect(getAccessToken()).toBeNull()
  })

  it('logout:后端不可达也清本地态', async () => {
    stubAuthServer({ logout: jsonResponse({ detail: 'boom' }, { ok: false, status: 500 }) })
    const auth = useAuthStore()
    await auth.login('a@qq.com', 'password123')
    await auth.logout()
    expect(auth.isAuthenticated).toBe(false)
  })
})
