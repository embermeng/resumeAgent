import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'
import { createRouter, createWebHashHistory, type Router } from 'vue-router'
import AppSidebar from './AppSidebar.vue'
import ConversationHistory from './ConversationHistory.vue'
import { useChatStore } from '@/stores/chat'
import { useAuthStore } from '@/stores/auth'
import * as api from '@/api/client'

vi.mock('@/api/client')

let pinia: Pinia
let router: Router

beforeEach(() => {
  sessionStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  vi.mocked(api.getConversations).mockResolvedValue({ conversations: [], total: 0 })
  vi.mocked(api.listProjectDocs).mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
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

/** 置为管理员(/admin 入口与知识库构建需该标记) */
function asSuperuser(id = 9) {
  const auth = useAuthStore()
  auth.user = { id, username: 'a', email: 'a@x.com', is_superuser: true }
}

describe('AppSidebar', () => {
  it('渲染新对话按钮、两个菜单与历史面板', async () => {
    const auth = useAuthStore()
    auth.user = { id: 9, username: 'a', email: 'a@x.com', is_superuser: true }
    const w = await factory()
    expect(w.find('[data-test="new-chat-btn"]').exists()).toBe(true)
    expect(w.find('[data-test="menu-dialog"]').exists()).toBe(true)
    expect(w.find('[data-test="menu-admin"]').exists()).toBe(true)
    expect(w.findComponent(ConversationHistory).exists()).toBe(true)
  })

  it('非管理员不渲染「知识库管理」入口', async () => {
    const auth = useAuthStore()
    auth.user = { id: 10, username: 'b', email: 'b@x.com', is_superuser: false }
    const w = await factory()
    expect(w.find('[data-test="menu-admin"]').exists()).toBe(false)
    expect(w.find('[data-test="menu-dialog"]').exists()).toBe(true)
  })

  it('按当前路由高亮对应菜单', async () => {
    asSuperuser()
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
    asSuperuser()
    const w = await factory()
    await w.find('[data-test="menu-admin"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/admin')
  })

  it('挂载即拉取项目列表(侧边栏徽标需要)', async () => {
    // 列表拉取职责在 AppSidebar 的 onMounted,面板组件不自拉
    await factory()
    expect(api.listProjectDocs).toHaveBeenCalled()
  })

  it('渲染「我的项目」入口并显示配额', async () => {
    // AppSidebar onMounted 会自拉列表,数据须经 mock 提供而非注入 store
    vi.mocked(api.listProjectDocs).mockResolvedValue({
      docs: [
        {
          doc_id: 'a',
          project_name: '甲',
          file_name: 'a.md',
          tags: [],
          has_resume_section: true,
          is_public: false,
          created_at: 1,
          updated_at: 1,
        },
      ],
      total: 1,
      max_docs: 30,
    })
    const w = await factory()
    expect(w.find('[data-test="menu-project-docs"]').exists()).toBe(true)
    expect(w.find('[data-test="project-count"]').text()).toBe('1/30')
  })

  it('点击「我的项目」→ 打开抽屉并拉取列表', async () => {
    const w = await factory()
    await w.find('[data-test="menu-project-docs"]').trigger('click')
    await flushPromises()
    expect(api.listProjectDocs).toHaveBeenCalled()
  })
})
