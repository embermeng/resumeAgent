import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus from 'element-plus'
import { ElMessageBox } from 'element-plus'
import ChatView from './ChatView.vue'
import ChatMessage from '@/components/ChatMessage.vue'
import ResumeUploader from '@/components/ResumeUploader.vue'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'

vi.mock('@/api/client')

let pinia: Pinia

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  pinia = createPinia()
  setActivePinia(pinia)
  vi.mocked(api.getSupportedExtensions).mockResolvedValue({ extensions: ['.md', '.txt', '.docx', '.pdf'] })
  // 默认「没传过简历」:GET /resume/current 返 204 → client 给 undefined
  vi.mocked(api.getResumeCurrent).mockResolvedValue(undefined)
  vi.mocked(api.deleteResumeCurrent).mockResolvedValue()
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

  it('上传解析后写入 existingResume,并用服务端真值补齐解析时间', async () => {
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: 'a.md',
      created_at: 500,
      content: '# 旧简历',
    })
    const store = useChatStore()
    const w = await factory()
    w.findComponent(ResumeUploader).vm.$emit('parsed', { filename: 'a.md', content: '# 旧简历' })
    await flushPromises()
    expect(store.existingResume).toBe('# 旧简历')
    expect(store.existingResumeTime).toBe(500)
    expect(w.find('[data-test="resume-current"]').text()).toContain('a.md')
    // 挂载取一次 + 新上传后再对账一次
    expect(api.getResumeCurrent).toHaveBeenCalledTimes(2)
  })

  it('挂载后回显服务端「我的简历」,并自动带入生成上下文', async () => {
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: '全栈.docx',
      created_at: 1700000000,
      content: '# 张三简历',
    })
    const store = useChatStore()
    const w = await factory()
    const card = w.find('[data-test="resume-current"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('全栈.docx')
    expect(card.text()).toContain(new Date(1700000000 * 1000).toLocaleString())
    // 无需用户点任何"附加":正文已就位,下一次 send 就带上
    expect(store.existingResume).toBe('# 张三简历')
  })

  it('没有简历时不渲染卡片与删除入口', async () => {
    const w = await factory()
    expect(w.find('[data-test="resume-current"]').exists()).toBe(false)
    expect(w.find('[data-test="resume-delete"]').exists()).toBe(false)
  })

  it('输入框上方不再有可取消的简历标签(简历始终自动带入)', async () => {
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: 'a.docx',
      created_at: 1,
      content: '# 简历',
    })
    const w = await factory()
    expect(w.find('[data-test="resume-chip"]').exists()).toBe(false)
  })

  it('已有简历时给上传组件传 has-resume,由其弹替换确认', async () => {
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: 'a.docx',
      created_at: 1,
      content: '# 简历',
    })
    const w = await factory()
    expect(w.findComponent(ResumeUploader).props('hasResume')).toBe(true)
  })

  it('首次上传(还没简历)不触发替换确认', async () => {
    const w = await factory()
    expect(w.findComponent(ResumeUploader).props('hasResume')).toBe(false)
  })

  it('删除:确认后调 store.removeResume 并收起卡片', async () => {
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: 'a.docx',
      created_at: 100,
      content: '# 简历',
    })
    const confirm = vi
      .spyOn(ElMessageBox, 'confirm')
      .mockResolvedValue('confirm' as unknown as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    const store = useChatStore()
    // 打桩处替 store 清态:这里验证的是视图收到成功后的反应,不是 store 本身
    const remove = vi.spyOn(store, 'removeResume').mockImplementation(async () => {
      store.clearExistingResume()
      return true
    })
    const w = await factory()
    await w.find('[data-test="resume-delete"]').trigger('click')
    await flushPromises()
    expect(confirm.mock.calls[0][0]).toContain('a.docx')
    expect(remove).toHaveBeenCalled()
    expect(w.find('[data-test="resume-current"]').exists()).toBe(false)
    expect(w.find('[data-test="resume-delete"]').exists()).toBe(false)
  })

  it('删除:用户取消则不调接口', async () => {
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: 'a.docx',
      created_at: 100,
      content: '# 简历',
    })
    const confirm = vi
      .spyOn(ElMessageBox, 'confirm')
      .mockRejectedValue(new Error('cancel') as never)
    const remove = vi.spyOn(useChatStore(), 'removeResume').mockResolvedValue(true)
    const w = await factory()
    await w.find('[data-test="resume-delete"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(remove).not.toHaveBeenCalled()
    expect(w.find('[data-test="resume-current"]').exists()).toBe(true)
  })

  it('刷新后先走 sessionStorage 快路径,再以服务端时间为准', async () => {
    sessionStorage.setItem(
      'resumeagent.existing_resume',
      JSON.stringify({ filename: 'old.docx', content: '# 旧简历', created_at: 1 }),
    )
    vi.mocked(api.getResumeCurrent).mockResolvedValue({
      filename: 'old.docx',
      created_at: 888,
      content: '# 旧简历',
    })
    const store = useChatStore()
    const w = await factory()
    expect(w.find('[data-test="resume-current"]').text()).toContain('old.docx')
    expect(store.existingResumeTime).toBe(888)
  })

  it('服务端已无简历(在别处删过)→ 本地残留随之清掉,不带空简历生成', async () => {
    sessionStorage.setItem(
      'resumeagent.existing_resume',
      JSON.stringify({ filename: 'old.docx', content: '# 旧简历', created_at: 1 }),
    )
    const store = useChatStore()
    const w = await factory()
    expect(store.existingResume).toBe('')
    expect(w.find('[data-test="resume-current"]').exists()).toBe(false)
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
