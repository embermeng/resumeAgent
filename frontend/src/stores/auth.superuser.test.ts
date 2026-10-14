import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useAuthStore } from './auth'
import type { UserPrivate } from '@/types/events'

beforeEach(() => {
  setActivePinia(createPinia())
})
afterEach(() => {
  vi.restoreAllMocks()
})

function me(over: Partial<UserPrivate> = {}): UserPrivate {
  return { id: 9, username: 'mlx', email: 'me@x.com', ...over }
}

describe('auth store · isSuperuser', () => {
  it('is_superuser 为 true 时为管理员', () => {
    const auth = useAuthStore()
    auth.user = me({ is_superuser: true })
    expect(auth.isSuperuser).toBe(true)
  })

  it('is_superuser 为 false 时不是管理员', () => {
    const auth = useAuthStore()
    auth.user = me({ is_superuser: false })
    expect(auth.isSuperuser).toBe(false)
  })

  it('字段缺失(旧后端/未下发)时按非管理员处理,不因 undefined 报错', () => {
    const auth = useAuthStore()
    auth.user = me()
    expect(auth.isSuperuser).toBe(false)
  })

  it('未登录时为 false', () => {
    const auth = useAuthStore()
    expect(auth.user).toBeNull()
    expect(auth.isSuperuser).toBe(false)
  })
})
