import { defineStore } from 'pinia'
import * as authApi from '@/api/auth'
import { clearAccessToken, setAccessToken } from '@/api/authToken'
import type { UserPrivate } from '@/types/events'

/**
 * 认证状态(契约 §1.2 / 4.11~4.18)。
 *
 * access token 只存内存(authToken 模块),不落本地存储;刷新页面后靠 httpOnly
 * refresh cookie 走 initialize() 静默续期。user 为 null 即视为未登录。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as UserPrivate | null,
    /** 是否已做过一次启动初始化(静默刷新),供路由守卫判定是否需要等待 */
    initialized: false,
    /** 登录/注册进行中(按钮 loading) */
    loading: false,
  }),
  getters: {
    isAuthenticated: (state): boolean => state.user !== null,
  },
  actions: {
    /** 拉取当前用户信息(需已有 access token) */
    async loadMe(): Promise<void> {
      this.user = await authApi.me()
    },
    /**
     * 应用启动时的静默续期:用 refresh cookie 换新 access token。
     * 成功 → 载入 /me 置为已登录;失败(无 cookie/已吊销) → 保持未登录。
     * 无论成败都标记 initialized,避免守卫反复等待。
     */
    async initialize(): Promise<void> {
      if (this.initialized) return
      const tok = await authApi.refreshAccessToken()
      if (tok) {
        try {
          await this.loadMe()
        } catch {
          // token 换到了但 /me 失败(极少见):清会话,回未登录态
          this.reset()
        }
      }
      this.initialized = true
    },
    /** 登录:签发 token 对(access 入内存,refresh 由浏览器存 cookie),随后载入用户 */
    async login(email: string, password: string): Promise<void> {
      this.loading = true
      try {
        const t = await authApi.login({ email, password })
        setAccessToken(t.access_token)
        await this.loadMe()
      } finally {
        this.loading = false
      }
    },
    /** 注册成功后自动登录(注册接口本身只返回 UserPrivate,不下发 token) */
    async register(username: string, email: string, password: string): Promise<void> {
      this.loading = true
      try {
        await authApi.register({ username, email, password })
      } finally {
        this.loading = false
      }
      await this.login(email, password)
    },
    /** 登出:吊销服务端 family + 清本地态(后端不清 cookie,原 cookie 已变死票) */
    async logout(): Promise<void> {
      try {
        await authApi.logout()
      } catch {
        /* 网络异常也要清本地态,保证前端登出可用 */
      }
      this.reset()
    },
    /** 清空本地会话(不发请求):用于会话失效回调与登出后收尾 */
    reset(): void {
      clearAccessToken()
      this.user = null
    },
  },
})
