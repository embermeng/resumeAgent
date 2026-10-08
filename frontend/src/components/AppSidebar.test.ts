import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'
import { createRouter, createWebHashHistory, type Router } from 'vue-router'
import AppSidebar from './AppSidebar.vue'
import ConversationHistory from './ConversationHistory.vue'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'

vi.mock('@/api/client')

let pinia: Pinia
let router: Router

beforeEach(() => {
  sessionStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  vi.mocked(api.getConversations).mockResolvedValue({ conversations: [], total: 0 })
  router = createRouter({
    history: createWebHashHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/admin', component: { template: '<div />' } },
    ],
  })
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

async function factory(path = '/') {
  router.push(path)
  await router.isReady()
  const w = mount(AppSidebar, { global: { plugins: [pinia, ElementPlus, router] } })
  await flushPromises()
  return w
}

describe('AppSidebar', () => {
  it('渲染新对话按钮、两个菜单与历史面板', async () => {
    const w = await factory()
    expect(w.find('[data-test="new-chat-btn"]').exists()).toBe(true)
    expect(w.find('[data-test="menu-dialog"]').exists()).toBe(true)
    expect(w.find('[data-test="menu-admin"]').exists()).toBe(true)
    expect(w.findComponent(ConversationHistory).exists()).toBe(true)
  })

  it('按当前路由高亮对应菜单', async () => {
    const w = await factory('/admin')
    expect(w.find('[data-test="menu-admin"]').classes()).toContain('active')
    expect(w.find('[data-test="menu-dialog"]').classes()).not.toContain('active')
  })

  it('点击开启新对话 → store.clear', async () => {
    const store = useChatStore()
    store.messages.push({ id: '1', role: 'user', content: 'x' })
    const spy = vi.spyOn(store, 'clear')
    const w = await factory()
    await w.find('[data-test="new-chat-btn"]').trigger('click')
    expect(spy).toHaveBeenCalled()
  })

  it('点击知识库管理菜单 → 导航到 /admin', async () => {
    const w = await factory()
    await w.find('[data-test="menu-admin"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/admin')
  })
})
