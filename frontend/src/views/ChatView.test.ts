import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'
import ChatView from './ChatView.vue'
import ChatMessage from '@/components/ChatMessage.vue'
import ResumeUploader from '@/components/ResumeUploader.vue'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'

vi.mock('@/api/client')

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
  vi.mocked(api.getSupportedExtensions).mockResolvedValue({ extensions: ['.md', '.txt', '.docx', '.pdf'] })
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.clearAllMocks()
})

async function factory() {
  const w = mount(ChatView, { global: { plugins: [pinia, ElementPlus] } })
  await flushPromises()
  return w
}

describe('ChatView', () => {
  it('渲染输入框、发送/清空按钮与上传区', async () => {
    const w = await factory()
    expect(w.find('textarea').exists()).toBe(true)
    expect(w.find('[data-test="send-btn"]').exists()).toBe(true)
    expect(w.find('[data-test="clear-btn"]').exists()).toBe(true)
    expect(w.findComponent(ResumeUploader).exists()).toBe(true)
  })

  it('无消息时显示空状态引导', async () => {
    const w = await factory()
    expect(w.text()).toContain('开始对话')
  })

  it('输入并点击发送 → store.send(文本),且输入框清空', async () => {
    const store = useChatStore()
    const spy = vi.spyOn(store, 'send').mockResolvedValue()
    const w = await factory()
    await w.find('textarea').setValue('什么是RAG')
    await w.find('[data-test="send-btn"]').trigger('click')
    expect(spy).toHaveBeenCalledWith('什么是RAG')
    expect((w.find('textarea').element as HTMLTextAreaElement).value).toBe('')
  })

  it('store 有消息时渲染对应数量 ChatMessage', async () => {
    const store = useChatStore()
    store.messages.push({ id: '1', role: 'user', content: 'hi' })
    store.messages.push({ id: '2', role: 'assistant', content: '你好', streaming: false })
    const w = await factory()
    expect(w.findAllComponents(ChatMessage)).toHaveLength(2)
  })

  it('上传解析后写入 existingResume 并显示附加提示', async () => {
    const store = useChatStore()
    const w = await factory()
    w.findComponent(ResumeUploader).vm.$emit('parsed', { filename: 'a.md', content: '# 旧简历' })
    await flushPromises()
    expect(store.existingResume).toBe('# 旧简历')
    expect(w.find('[data-test="resume-chip"]').exists()).toBe(true)
  })

  it('点击清空 → store.clear', async () => {
    const store = useChatStore()
    store.messages.push({ id: '1', role: 'user', content: 'hi' })
    const spy = vi.spyOn(store, 'clear')
    const w = await factory()
    await w.find('[data-test="clear-btn"]').trigger('click')
    expect(spy).toHaveBeenCalled()
  })

  it('streaming 时显示停止按钮而非发送', async () => {
    const store = useChatStore()
    store.streaming = true
    const w = await factory()
    expect(w.find('[data-test="stop-btn"]').exists()).toBe(true)
    expect(w.find('[data-test="send-btn"]').exists()).toBe(false)
  })
})
