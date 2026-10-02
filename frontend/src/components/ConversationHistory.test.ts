import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'
import ConversationHistory from './ConversationHistory.vue'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'

vi.mock('@/api/client')

let pinia: Pinia

beforeEach(() => {
  sessionStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

const LIST = {
  conversations: [
    { id: 4, title: '蒸馏是什么意思', created_at: 1790924602, updated_at: 1790924615 },
    { id: 3, title: 'hi', created_at: 1790924122, updated_at: 1790924290 },
  ],
  total: 2,
}

async function factory() {
  vi.mocked(api.getConversations).mockResolvedValue(LIST)
  const w = mount(ConversationHistory, { global: { plugins: [pinia, ElementPlus] } })
  await flushPromises()
  return w
}

describe('ConversationHistory', () => {
  it('挂载拉取列表并渲染标题与条目', async () => {
    const w = await factory()
    expect(api.getConversations).toHaveBeenCalledWith(1, 20)
    expect(w.findAll('[data-test^="history-item-"]')).toHaveLength(2)
    expect(w.text()).toContain('蒸馏是什么意思')
  })

  it('点击条目 → openConversation 并 emit opened', async () => {
    vi.mocked(api.getConversationMessages).mockResolvedValue({ conversation_id: 4, messages: [] })
    const w = await factory()
    await w.find('[data-test="history-item-4"]').trigger('click')
    await flushPromises()
    expect(api.getConversationMessages).toHaveBeenCalledWith(4)
    expect(useChatStore().conversationId).toBe(4)
    expect(w.emitted('opened')).toBeTruthy()
  })

  it('total 超一页时显示加载更多,追加第二页', async () => {
    vi.mocked(api.getConversations).mockResolvedValue({ conversations: LIST.conversations, total: 25 })
    const w = mount(ConversationHistory, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    expect(w.find('[data-test="load-more-btn"]').exists()).toBe(true)
    await w.find('[data-test="load-more-btn"]').trigger('click')
    await flushPromises()
    expect(api.getConversations).toHaveBeenLastCalledWith(2, 20)
    // append 模式:两页拼接
    expect(useChatStore().history).toHaveLength(4)
  })

  it('拉取失败静默降级为空状态,不抛错', async () => {
    vi.mocked(api.getConversations).mockRejectedValue(new Error('down'))
    const w = mount(ConversationHistory, { global: { plugins: [pinia, ElementPlus] } })
    await flushPromises()
    expect(w.text()).toContain('暂无历史会话')
  })
})
