import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from './chat'
import { sseResponse, stubFetch } from '@/test/sse-helpers'

beforeEach(() => {
  setActivePinia(createPinia())
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function pushAssistant(store: ReturnType<typeof useChatStore>) {
  store.messages.push({ id: 'a', role: 'assistant', content: '', streaming: true })
  return store.messages[store.messages.length - 1]
}

describe('chat store', () => {
  it('applyEvent: token 增量拼接到助手消息', () => {
    const store = useChatStore()
    const msg = pushAssistant(store)
    store.applyEvent(msg, { type: 'token', text: '你' })
    store.applyEvent(msg, { type: 'token', text: '好' })
    expect(msg.content).toBe('你好')
  })

  it('applyEvent: status/intent 更新全局态与消息', () => {
    const store = useChatStore()
    const msg = pushAssistant(store)
    store.applyEvent(msg, { type: 'status', text: '正在检索知识库...' })
    expect(store.status).toBe('正在检索知识库...')
    store.applyEvent(msg, { type: 'intent', value: 'quick_response' })
    expect(store.intent).toBe('quick_response')
    expect(msg.intent).toBe('quick_response')
  })

  it('applyEvent: done 结束流式,resume_final 落到消息', () => {
    const store = useChatStore()
    store.streaming = true
    const msg = pushAssistant(store)
    store.applyEvent(msg, {
      type: 'done',
      intent: 'deep_thinking',
      step: 'deep_thinking_done',
      resume_final: '# 简历',
      retrieved_knowledge: '知识点',
    })
    expect(msg.streaming).toBe(false)
    expect(msg.resumeFinal).toBe('# 简历')
    expect(msg.retrievedKnowledge).toBe('知识点')
    expect(store.streaming).toBe(false)
    expect(store.status).toBeNull()
  })

  it('applyEvent: error 记录到消息并停止流式', () => {
    const store = useChatStore()
    store.streaming = true
    const msg = pushAssistant(store)
    store.applyEvent(msg, { type: 'error', message: 'boom' })
    expect(msg.error).toBe('boom')
    expect(msg.streaming).toBe(false)
    expect(store.streaming).toBe(false)
  })

  it('send: 推入 user+assistant,流式拼接内容,透传 existing_resume', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse([
        'event: status\ndata: {"text":"正在识别意图..."}\n\n',
        'event: intent\ndata: {"value":"quick_response"}\n\n',
        'event: token\ndata: {"text":"RAG "}\n\n',
        'event: token\ndata: {"text":"是检索增强"}\n\n',
        'event: done\ndata: {"intent":"quick_response","step":"quick_response_done"}\n\n',
      ]),
    )
    const store = useChatStore()
    store.setExistingResume('# 旧简历')
    await store.send('什么是RAG')

    expect(store.messages).toHaveLength(2)
    expect(store.messages[0]).toMatchObject({ role: 'user', content: '什么是RAG' })
    expect(store.messages[1].role).toBe('assistant')
    expect(store.messages[1].content).toBe('RAG 是检索增强')
    expect(store.messages[1].streaming).toBe(false)
    expect(store.intent).toBe('quick_response')
    expect(store.streaming).toBe(false)

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body))
    expect(body).toEqual({ prompt: '什么是RAG', existing_resume: '# 旧简历' })
  })

  it('send: 空白输入不发送、不产生消息', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse(['event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n']),
    )
    const store = useChatStore()
    await store.send('   ')
    expect(store.messages).toHaveLength(0)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('send: 无 existingResume 时请求体不带该字段', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse(['event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n']),
    )
    const store = useChatStore()
    await store.send('hi')
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ prompt: 'hi' })
  })

  it('clear: 清空消息与状态', async () => {
    stubFetch(async () =>
      sseResponse(['event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n']),
    )
    const store = useChatStore()
    await store.send('hi')
    expect(store.messages.length).toBeGreaterThan(0)
    store.clear()
    expect(store.messages).toHaveLength(0)
    expect(store.intent).toBeNull()
    expect(store.status).toBeNull()
  })

  it('stop: 复位 streaming', () => {
    const store = useChatStore()
    store.streaming = true
    store.stop()
    expect(store.streaming).toBe(false)
  })
})
