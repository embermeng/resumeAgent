import { describe, it, expect, afterEach } from 'vitest'
import { getConversations } from './client'
import { clearAccessToken, setAccessToken, setOnSessionLost } from './authToken'
import { stubFetch, jsonResponse } from '@/test/sse-helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  clearAccessToken()
  setOnSessionLost(null)
})

describe('client 401 → 刷新 → 重放', () => {
  it('首个 401 触发刷新,刷新成功后带新 token 重放并拿到数据', async () => {
    setAccessToken('stale.token')
    let hitProtected = 0
    const fetchMock = stubFetch(async (url: string, opts?: RequestInit) => {
      if (url.startsWith('/api/conversations')) {
        hitProtected += 1
        if (hitProtected === 1) return jsonResponse({ detail: 'Invalid or expired token' }, { ok: false, status: 401 })
        // 重放:应带上新 token
        expect((opts?.headers as Record<string, string>).Authorization).toBe('Bearer fresh.token')
        return jsonResponse({ conversations: [], total: 0 })
      }
      if (url.endsWith('/auth/refresh')) return jsonResponse({ access_token: 'fresh.token', token_type: 'bearer' })
      return jsonResponse({ detail: 'unexpected' }, { ok: false, status: 404 })
    })

    const res = await getConversations()
    expect(res).toEqual({ conversations: [], total: 0 })
    expect(hitProtected).toBe(2) // 原请求 + 重放
    // 受保护 1 次(401) + 刷新 1 次 + 重放 1 次 = 3
    expect(fetchMock.mock.calls.length).toBe(3)
  })

  it('刷新也 401:触发会话失效回调并抛错,不再重放', async () => {
    setAccessToken('stale.token')
    let sessionLost = 0
    setOnSessionLost(() => { sessionLost += 1 })
    let hitProtected = 0
    stubFetch(async (url: string) => {
      if (url.startsWith('/api/conversations')) {
        hitProtected += 1
        return jsonResponse({ detail: 'Invalid or expired token' }, { ok: false, status: 401 })
      }
      if (url.endsWith('/auth/refresh')) return jsonResponse({ detail: 'Refresh token is revoked' }, { ok: false, status: 401 })
      return jsonResponse({ detail: 'unexpected' }, { ok: false, status: 404 })
    })

    await expect(getConversations()).rejects.toThrow('Invalid or expired token')
    expect(hitProtected).toBe(1) // 未重放
    expect(sessionLost).toBe(1) // 通知了会话失效
  })
})
