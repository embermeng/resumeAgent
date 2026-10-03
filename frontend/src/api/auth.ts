import type { Token, UserCreate, UserLoginReq, UserPrivate, UserPublic, UserUpdate } from '@/types/events'
import { clearAccessToken, getAccessToken, setAccessToken } from './authToken'

/**
 * 认证 REST 接口(契约 4.11~4.18)。
 *
 * 这些函数刻意不走 client.ts 的 request():后者带「401→自动刷新→重试」逻辑,
 * 而 login/register/refresh/logout 本身就可能返回 401,若共用会触发刷新递归。
 * 故此处用最小 fetch 封装,非 2xx 直接抛后端 detail。
 *
 * refresh token 载体是 httpOnly cookie(Path=/api/auth、SameSite=Strict),
 * JS 读不到,只能靠 credentials:'include' 让浏览器自动带上。
 */
const BASE = '/api/auth'
export const REFRESH_COOKIE = 'refresh-token'

async function authFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { credentials: 'include', ...init })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body && typeof body.detail === 'string') detail = body.detail
      else if (body && Array.isArray(body.detail)) detail = JSON.stringify(body.detail)
    } catch {
      /* 错误体非 JSON 时保留状态文本 */
    }
    throw new Error(detail)
  }
  // 204 无响应体
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

/** 4.11 注册(201 UserPrivate) */
export function register(payload: UserCreate): Promise<UserPrivate> {
  return authFetch<UserPrivate>(`${BASE}/register`, jsonInit('POST', payload))
}

/** 4.12 登录(200 Token + Set-Cookie refresh-token) */
export function login(payload: UserLoginReq): Promise<Token> {
  return authFetch<Token>(`${BASE}/login`, jsonInit('POST', payload))
}

/** 4.13 用 cookie 里的 refresh token 换新 access token(200 Token,cookie 轮换) */
export function refresh(): Promise<Token> {
  return authFetch<Token>(`${BASE}/refresh`, { method: 'POST' })
}

/** 4.14 当前用户信息(需 Bearer,走 client 层注入,这里直连) */
export function me(): Promise<UserPrivate> {
  return authFetch<UserPrivate>(`${BASE}/me`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
  })
}

/** 4.15 登出:吊销整个 family(204,幂等) */
export function logout(): Promise<void> {
  return authFetch<void>(`${BASE}/logout`, { method: 'POST' })
}

/** 4.16 查询用户公开信息(无鉴权) */
export function getUser(userId: number): Promise<UserPublic> {
  return authFetch<UserPublic>(`${BASE}/user/${userId}`, { method: 'GET' })
}

/** 4.17 修改本人 username/email(需 Bearer + 本人) */
export function patchUser(userId: number, payload: UserUpdate): Promise<UserPrivate> {
  return authFetch<UserPrivate>(`${BASE}/${userId}`, {
    ...jsonInit('PATCH', payload),
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${getAccessToken() ?? ''}` },
  })
}

/** 4.18 删除本人账号(需 Bearer + 本人,级联) */
export function deleteUser(userId: number): Promise<void> {
  return authFetch<void>(`${BASE}/${userId}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${getAccessToken() ?? ''}` },
  })
}

// ---------- 刷新单飞 + 到期预判 ----------

let inflight: Promise<string | null> | null = null

/**
 * 刷新 access token(单飞:并发 401 只发一次 /refresh)。
 * 成功 → 写入 token holder 并返回新 token;失败 → 清空 token 并返回 null。
 */
export function refreshAccessToken(): Promise<string | null> {
  if (!inflight) {
    inflight = (async () => {
      try {
        const t = await refresh()
        setAccessToken(t.access_token)
        return t.access_token
      } catch {
        clearAccessToken()
        return null
      }
    })().finally(() => {
      inflight = null
    })
  }
  return inflight
}

/** 解出 JWT payload.exp(秒);非法 token 返回 null。仅用于到期预判,不做验签。 */
export function decodeExp(token: string): number | null {
  try {
    const part = token.split('.')[1]
    if (!part) return null
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'))
    const payload = JSON.parse(json) as { exp?: unknown }
    return typeof payload.exp === 'number' ? payload.exp : null
  } catch {
    return null
  }
}

/**
 * SSE 开流前确保 token 未临近过期(契约无 expires_in,前端自行解 exp)。
 * - 无 token:返回 null(不主动刷新,交由路由守卫/登录处理,也避免测试期误发请求);
 * - 30s 内到期:先刷新再返回;
 * - 否则原样返回。
 */
export async function ensureFreshToken(skewMs = 30_000): Promise<string | null> {
  const tok = getAccessToken()
  if (!tok) return null
  const exp = decodeExp(tok)
  if (exp && exp * 1000 - Date.now() < skewMs) return refreshAccessToken()
  return tok
}
