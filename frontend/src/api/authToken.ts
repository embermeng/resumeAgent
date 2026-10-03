/**
 * access token 的模块级持有者(单一真理来源)。
 *
 * 为什么单独抽一个模块而不是直接放 store:
 * - client.ts / useChatStream.ts 是普通模块(非组件),读写 token 若依赖 Pinia store,
 *   会与「store 又 import client」形成循环依赖;
 * - token 只存内存(不落 localStorage):刷新页面靠 httpOnly refresh cookie 静默续期,
 *   避免 access token 被 XSS 从本地存储读走。
 *
 * 另注册一个「会话失效」回调:当 401 且刷新也失败时,由 client 触发,
 * main.ts 把它接到 router.push('/login'),实现请求层与路由层解耦。
 */
let accessToken: string | null = null
let onSessionLost: (() => void) | null = null

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function clearAccessToken(): void {
  accessToken = null
}

/** 注册「会话彻底失效」回调(refresh 也 401 时调用,用于跳登录页) */
export function setOnSessionLost(fn: (() => void) | null): void {
  onSessionLost = fn
}

export function notifySessionLost(): void {
  onSessionLost?.()
}
