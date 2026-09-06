import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import ChatMessage from './ChatMessage.vue'
import type { ChatMessage as ChatMessageModel } from '@/stores/chat'

function factory(msg: Partial<ChatMessageModel>) {
  const message: ChatMessageModel = { id: 'm1', role: 'assistant', content: '', ...msg }
  return mount(ChatMessage, { props: { message }, global: { plugins: [ElementPlus] } })
}

describe('ChatMessage', () => {
  it('用户消息以纯文本渲染,不走 markdown', () => {
    const w = factory({ role: 'user', content: '**不解析**' })
    expect(w.find('.user-text').text()).toBe('**不解析**')
    expect(w.find('.markdown-body').exists()).toBe(false)
  })

  it('助手消息用 markdown 渲染(v-html)', () => {
    const w = factory({ role: 'assistant', content: '**加粗**' })
    expect(w.find('.markdown-body').html()).toContain('<strong>加粗</strong>')
  })

  it('streaming 时显示打字光标,结束后隐藏', () => {
    expect(factory({ streaming: true, content: 'hi' }).find('[data-test="cursor"]').exists()).toBe(true)
    expect(factory({ streaming: false, content: 'hi' }).find('[data-test="cursor"]').exists()).toBe(false)
  })

  it('streaming 且无正文时展示 status 阶段提示', () => {
    const w = factory({ streaming: true, content: '', status: '正在检索知识库...' })
    expect(w.find('.status-line').text()).toContain('正在检索知识库...')
  })

  it('有正文后不再展示 status', () => {
    const w = factory({ streaming: true, content: '答案', status: '正在生成回答...' })
    expect(w.find('.status-line').exists()).toBe(false)
  })

  it('error 显示告警文本', () => {
    const w = factory({ error: '出错了' })
    expect(w.text()).toContain('出错了')
  })

  it('intent 显示中文标签', () => {
    expect(factory({ intent: 'deep_thinking' }).text()).toContain('深度思考')
    expect(factory({ intent: 'quick_response' }).text()).toContain('快速回答')
  })

  it('resumeFinal 显示预览/下载按钮,点击各自 emit', async () => {
    const w = factory({ resumeFinal: '# 简历内容', streaming: false })
    await w.find('[data-test="preview-btn"]').trigger('click')
    await w.find('[data-test="download-btn"]').trigger('click')
    expect(w.emitted('preview')?.[0]).toEqual(['# 简历内容'])
    expect(w.emitted('download')?.[0]).toEqual(['# 简历内容'])
  })

  it('无 resumeFinal 时不渲染下载按钮', () => {
    const w = factory({ content: '普通回答', streaming: false })
    expect(w.find('[data-test="download-btn"]').exists()).toBe(false)
  })
})
