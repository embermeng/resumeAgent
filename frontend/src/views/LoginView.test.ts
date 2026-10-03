import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import ElementPlus from 'element-plus'
import LoginView from './LoginView.vue'
import { useAuthStore } from '@/stores/auth'

let pinia: Pinia
let router: Router

function makeRouter(): Router {
  const r = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/login', name: 'login', component: { template: '<div/>' } },
      { path: '/', name: 'chat', component: { template: '<div/>' } },
    ],
  })
  return r
}

async function factory(query = '') {
  router = makeRouter()
  await router.push(`/login${query}`)
  await router.isReady()
  const w = mount(LoginView, { global: { plugins: [pinia, ElementPlus, router] } })
  await flushPromises()
  return w
}

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

describe('LoginView', () => {
  it('登录模式:渲染邮箱/密码,不含用户名', async () => {
    const w = await factory()
    expect(w.text()).toContain('登录')
    expect(w.find('input[type="email"]').exists()).toBe(true)
    expect(w.find('input[type="password"]').exists()).toBe(true)
    // 登录模式无 username 输入
    expect(w.findAll('input').length).toBe(2)
  })

  it('切到注册模式:出现用户名输入', async () => {
    const w = await factory()
    await w.findAll('.el-link').find((l) => l.text().includes('去注册'))!.trigger('click')
    await flushPromises()
    expect(w.text()).toContain('注册')
    expect(w.findAll('input').length).toBe(3) // username + email + password
  })

  it('登录成功:跳转离开登录页(默认回主页)', async () => {
    const auth = useAuthStore(pinia)
    const spy = vi.spyOn(auth, 'login').mockResolvedValue(undefined)
    const w = await factory()

    await w.find('input[type="email"]').setValue('a@qq.com')
    await w.find('input[type="password"]').setValue('password123')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(spy).toHaveBeenCalledWith('a@qq.com', 'password123')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('chat')
  })

  it('登录失败:展示后端错误文案,停在登录页', async () => {
    const auth = useAuthStore(pinia)
    vi.spyOn(auth, 'login').mockRejectedValue(new Error('Incorrect email or password'))
    const w = await factory()
    await w.find('input[type="email"]').setValue('a@qq.com')
    await w.find('input[type="password"]').setValue('password123')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(w.text()).toContain('Incorrect email or password')
    expect(router.currentRoute.value.name).toBe('login')
  })
})
