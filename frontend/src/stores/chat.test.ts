import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useChatStore } from './chat'
import { sseResponse, sseDropResponse, stubFetch, jsonResponse } from '@/test/sse-helpers'

beforeEach(() => {
  sessionStorage.clear()
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

  it('applyEvent: conversation 首帧写入 conversationId 并存 sessionStorage', () => {
    const store = useChatStore()
    const msg = pushAssistant(store)
    store.applyEvent(msg, { type: 'conversation', conversation_id: 42 })
    expect(store.conversationId).toBe(42)
    expect(sessionStorage.getItem('resumeagent.conversation_id')).toBe('42')
  })

  it('初始化时从 sessionStorage 续接 conversationId', () => {
    sessionStorage.setItem('resumeagent.conversation_id', '55')
    const store = useChatStore()
    expect(store.conversationId).toBe(55)
  })

  it('send: 首帧回传后,后续请求带上 conversation_id', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse([
        'event: conversation\ndata: {"conversation_id":7}\n\n',
        'event: token\ndata: {"text":"hi"}\n\n',
        'event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n',
      ]),
    )
    const store = useChatStore()
    await store.send('first')
    expect(store.conversationId).toBe(7)
    // 首条:发送时尚未知会话 id,请求体不带 conversation_id
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ prompt: 'first' })
    await store.send('second')
    // 第二条:带上首帧回传的 conversation_id
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      prompt: 'second',
      conversation_id: 7,
    })
  })

  it('clear: 重置 conversationId 并清除 sessionStorage', async () => {
    stubFetch(async () =>
      sseResponse([
        'event: conversation\ndata: {"conversation_id":9}\n\n',
        'event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n',
      ]),
    )
    const store = useChatStore()
    await store.send('hi')
    expect(store.conversationId).toBe(9)
    expect(sessionStorage.getItem('resumeagent.conversation_id')).toBe('9')
    store.clear()
    expect(store.conversationId).toBeNull()
    expect(sessionStorage.getItem('resumeagent.conversation_id')).toBeNull()
  })

  it('loadHistory: 拉取列表与 total,URL 带分页参数', async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse({ conversations: [{ id: 4, title: 'hi', created_at: 1, updated_at: 2 }], total: 1 }),
    )
    const store = useChatStore()
    await store.loadHistory()
    expect(store.history).toHaveLength(1)
    expect(store.historyTotal).toBe(1)
    expect(String(fetchMock.mock.calls[0][0])).toContain('/api/conversations?page=1&page_size=20')
  })

  it('loadHistory: 失败静默降级,保留旧列表且复位 loading', async () => {
    stubFetch(async () => jsonResponse({ detail: 'boom' }, { ok: false, status: 500 }))
    const store = useChatStore()
    store.history = [{ id: 1, title: 'old', created_at: 1, updated_at: 1 }]
    await store.loadHistory()
    expect(store.history).toHaveLength(1)
    expect(store.historyLoading).toBe(false)
  })

  it('openConversation: 回填消息(过滤 system)、切换会话并持久化', async () => {
    stubFetch(async () =>
      jsonResponse({
        conversation_id: 4,
        messages: [
          { id: 3, role: 'user', content: 'hi', intent: null, created_at: 1 },
          { id: 4, role: 'assistant', content: '你好', intent: 'chitchat', created_at: 2 },
          { id: 5, role: 'system', content: 'skip', intent: null, created_at: 3 },
        ],
      }),
    )
    const store = useChatStore()
    await store.openConversation(4)
    expect(store.messages.map((m) => m.role)).toEqual(['user', 'assistant'])
    expect(store.messages[1]).toMatchObject({ content: '你好', intent: 'chitchat' })
    expect(store.messages[0].intent).toBeUndefined()
    expect(store.conversationId).toBe(4)
    expect(sessionStorage.getItem('resumeagent.conversation_id')).toBe('4')
  })

  it('openConversation: 404 时抛错且不改当前消息与会话', async () => {
    stubFetch(async () =>
      jsonResponse({ detail: 'conversation not found' }, { ok: false, status: 404 }),
    )
    const store = useChatStore()
    store.messages = [{ id: 'x', role: 'user', content: 'keep' }]
    await expect(store.openConversation(999)).rejects.toThrow('conversation not found')
    expect(store.messages).toHaveLength(1)
    expect(store.conversationId).toBeNull()
  })

  it('openConversation: streaming 期间不切会话、不发请求', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ conversation_id: 1, messages: [] }))
    const store = useChatStore()
    store.streaming = true
    await store.openConversation(1)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(store.conversationId).toBeNull()
  })

  // ---- 并行线:工具时间线 / TTFT 埋点 / 重试 / 内容保全 ----

  it('applyEvent: status 逐条收集 timeline 并结算上一阶段耗时', () => {
    const store = useChatStore()
    const msg = pushAssistant(store)
    store.applyEvent(msg, { type: 'status', text: '阶段一' })
    expect(msg.timeline).toHaveLength(1)
    expect(msg.timeline![0].elapsed).toBeNull()
    store.applyEvent(msg, { type: 'status', text: '阶段二' })
    expect(msg.timeline).toHaveLength(2)
    expect(typeof msg.timeline![0].elapsed).toBe('number')
    expect(msg.timeline![1].elapsed).toBeNull()
  })

  it('applyEvent: done 结算末阶段耗时', () => {
    const store = useChatStore()
    const msg = pushAssistant(store)
    store.applyEvent(msg, { type: 'status', text: '阶段一' })
    store.applyEvent(msg, { type: 'done', intent: 'chitchat', step: 'chitchat_done' })
    expect(msg.timeline![0].elapsed).not.toBeNull()
  })

  it('send: 首个 token 记录 ttftMs,done 记录 elapsedMs', async () => {
    stubFetch(async () =>
      sseResponse([
        'event: token\ndata: {"text":"a"}\n\n',
        'event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n',
      ]),
    )
    const store = useChatStore()
    await store.send('hi')
    const msg = store.messages[1]
    expect(typeof msg.ttftMs).toBe('number')
    expect(typeof msg.elapsedMs).toBe('number')
  })

  it('send: 流正常结束(done)后清除保全草稿', async () => {
    stubFetch(async () =>
      sseResponse([
        'event: token\ndata: {"text":"内容"}\n\n',
        'event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n',
      ]),
    )
    const store = useChatStore()
    await store.send('hi')
    expect(sessionStorage.getItem('resumeagent.draft')).toBeNull()
  })

  it('retryMessage: 复用助手气泡重发同一 prompt,retryCount++', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse([
        'event: token\ndata: {"text":"部分"}\n\n',
        'event: error\ndata: {"message":"断线"}\n\n',
      ]),
    )
    const store = useChatStore()
    await store.send('问题')
    const assistantId = store.messages[1].id
    expect(store.messages[1].error).toBe('断线')
    fetchMock.mockImplementation(async () =>
      sseResponse([
        'event: token\ndata: {"text":"完整答案"}\n\n',
        'event: done\ndata: {"intent":"chitchat","step":"chitchat_done"}\n\n',
      ]),
    )
    await store.retryMessage(assistantId)
    expect(store.messages).toHaveLength(2) // 未新增消息对
    expect(store.messages[1].content).toBe('完整答案')
    expect(store.messages[1].retryCount).toBe(1)
    expect(store.messages[1].error).toBeNull()
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body)).prompt).toBe('问题')
  })

  it('retryMessage: streaming 期间不重入', async () => {
    const fetchMock = stubFetch(async () => sseResponse(['event: done\ndata: {"intent":"chitchat","step":"x"}\n\n']))
    const store = useChatStore()
    store.messages.push({ id: 'u', role: 'user', content: 'q' })
    store.messages.push({ id: 'a', role: 'assistant', content: '', error: 'x' })
    store.streaming = true
    await store.retryMessage('a')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('restoreDraft: 恢复为 user+assistant 消息并回填会话、清除草稿', () => {
    const store = useChatStore()
    store.recoverableDraft = { prompt: '原问题', content: '已生成部分', conversationId: 8, savedAt: 1 }
    store.restoreDraft()
    expect(store.messages).toHaveLength(2)
    expect(store.messages[0]).toMatchObject({ role: 'user', content: '原问题' })
    expect(store.messages[1]).toMatchObject({ role: 'assistant', content: '已生成部分' })
    expect(store.messages[1].error).toBeTruthy()
    expect(store.conversationId).toBe(8)
    expect(store.recoverableDraft).toBeNull()
  })

  it('restoreDraft: prompt 为空(手动停止)时不伪造 user 消息', () => {
    const store = useChatStore()
    store.recoverableDraft = { prompt: '', content: '停止前内容', conversationId: null, savedAt: 1 }
    store.restoreDraft()
    expect(store.messages).toHaveLength(1)
    expect(store.messages[0].role).toBe('assistant')
  })

  it('discardDraft: 放弃草稿', () => {
    const store = useChatStore()
    store.recoverableDraft = { prompt: 'q', content: 'c', conversationId: null, savedAt: 1 }
    store.discardDraft()
    expect(store.recoverableDraft).toBeNull()
  })

  it('初始化时从 sessionStorage 读回可恢复草稿', () => {
    sessionStorage.setItem(
      'resumeagent.draft',
      JSON.stringify({ prompt: 'q', content: '部分', conversationId: null, savedAt: 1 }),
    )
    const store = useChatStore()
    expect(store.recoverableDraft?.content).toBe('部分')
  })

  // ---- 并行线:断线自动重连(接后端 GET /chat/{stream_id}/stream 续传) ----

  it('send: 断线自动重连,重连期间 reconnecting=true,续传补齐内容后清草稿', async () => {
    let resolveGet: ((r: Response) => void) | null = null
    const fetchMock = stubFetch((url) => {
      if (url === '/api/chat') {
        return Promise.resolve(
          sseDropResponse([
            'id: 0\nevent: conversation\ndata: {"conversation_id":7,"stream_id":"s1"}\n\n',
            'id: 1\nevent: token\ndata: {"text":"前半"}\n\n',
          ]),
        )
      }
      // GET 重连挂起,由测试手动 resolve(模拟后端轮询等终态的延迟)
      return new Promise<Response>((res) => {
        resolveGet = res
      })
    })
    const store = useChatStore()
    const p = store.send('问题')
    // 等 POST 断线 + 退避后 GET 被发起并挂起
    await vi.waitFor(() => expect(resolveGet).not.toBeNull(), { timeout: 2000 })
    expect(store.reconnecting).toBe(true)
    // 后端续传:重放 seq1(去重)+seq2+终态
    resolveGet!(
      sseResponse([
        'id: 1\nevent: token\ndata: {"text":"前半"}\n\n',
        'id: 2\nevent: token\ndata: {"text":"后半"}\n\n',
        'id: 3\nevent: done\ndata: {"intent":"chitchat","step":"interrupted"}\n\n',
      ]),
    )
    await p
    expect(store.reconnecting).toBe(false)
    expect(store.messages[1].content).toBe('前半后半') // 去重:"前半"只一次
    expect(store.messages[1].streaming).toBe(false)
    expect(sessionStorage.getItem('resumeagent.draft')).toBeNull() // done 清草稿
    expect(String(fetchMock.mock.calls.at(-1)?.[0])).toContain('/api/chat/s1/stream?after=1')
  })

  it('send: 重连遇 404(缓冲过期)→ 降级错误提示 + 保全草稿,不重发', async () => {
    const fetchMock = stubFetch((url) => {
      if (url === '/api/chat') {
        return Promise.resolve(
          sseDropResponse([
            'id: 0\nevent: conversation\ndata: {"conversation_id":7,"stream_id":"s2"}\n\n',
            'id: 1\nevent: token\ndata: {"text":"已生成半截"}\n\n',
          ]),
        )
      }
      return Promise.resolve(sseResponse([], { ok: false, status: 404 }))
    })
    const store = useChatStore()
    await store.send('问题')
    const msg = store.messages[1]
    expect(msg.content).toBe('已生成半截') // 本地内容保留
    expect(msg.error).toContain('过期') // 降级提示
    expect(msg.streaming).toBe(false)
    expect(store.streaming).toBe(false)
    expect(store.reconnecting).toBe(false)
    // finally 补写完整草稿:刷新可恢复
    const draft = JSON.parse(sessionStorage.getItem('resumeagent.draft')!)
    expect(draft.content).toBe('已生成半截')
    expect(draft.prompt).toBe('问题')
    expect(fetchMock).toHaveBeenCalledTimes(2) // POST + 一次 GET(404 后停,不重发)
  })

  it('出错帧中断:finally 补写完整草稿(不因节流丢尾巴)', async () => {
    stubFetch(async () =>
      sseResponse([
        'event: conversation\ndata: {"conversation_id":3,"stream_id":"s3"}\n\n',
        'event: token\ndata: {"text":"部分"}\n\n',
        'event: token\ndata: {"text":"内容"}\n\n',
        'event: error\ndata: {"message":"后端炸了"}\n\n',
      ]),
    )
    const store = useChatStore()
    await store.send('问题')
    expect(store.messages[1].error).toBe('后端炸了')
    // 节流下第二个 token 不会单独写入,但 finally 补写保证草稿完整
    const draft = JSON.parse(sessionStorage.getItem('resumeagent.draft')!)
    expect(draft.content).toBe('部分内容')
  })
})
