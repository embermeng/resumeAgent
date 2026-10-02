import { defineStore } from 'pinia'
import { useChatStream } from '@/composables/useChatStream'
import { getConversationMessages, getConversations } from '@/api/client'
import type { ChatEvent, ConversationSummary, Intent, MessageOut } from '@/types/events'

/** 当前标签页所属会话 id 的 sessionStorage 键(按标签页隔离,刷新可续接同一会话) */
const CONVERSATION_KEY = 'resumeagent.conversation_id'

/** 从 sessionStorage 读回会话 id(不可用/无值时返回 null) */
function readConversationId(): number | null {
  try {
    const raw = sessionStorage.getItem(CONVERSATION_KEY)
    if (raw === null) return null
    const n = Number(raw)
    return Number.isFinite(n) ? n : null
  } catch {
    return null
  }
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  intent?: Intent
  status?: string
  streaming?: boolean
  error?: string | null
  /** done 帧带回的最终简历 Markdown(非空则可预览/下载) */
  resumeFinal?: string
  retrievedKnowledge?: string
  retrievedProjects?: string
}

let seq = 0
function nextId(): string {
  seq += 1
  return `msg_${Date.now().toString(36)}_${seq}`
}

// 单用户单流:模块级保存当前中断句柄(stop 用)
let activeAbort: (() => void) | null = null

export const useChatStore = defineStore('chat', {
  state: () => ({
    messages: [] as ChatMessage[],
    /** 最新阶段提示(来自 status 事件) */
    status: null as string | null,
    /** 当前意图(来自 intent 事件) */
    intent: null as Intent | null,
    /** 已上传/暂存的简历 Markdown,随下一次 send 透传 */
    existingResume: '',
    streaming: false,
    /** 当前会话 id(来自 conversation 首帧;刷新后从 sessionStorage 续接) */
    conversationId: readConversationId() as number | null,
    /** 历史会话列表(drawer 面板数据源,updated_at 降序) */
    history: [] as ConversationSummary[],
    historyTotal: 0,
    historyLoading: false,
  }),
  getters: {
    lastAssistant(state): ChatMessage | null {
      for (let i = state.messages.length - 1; i >= 0; i--) {
        if (state.messages[i].role === 'assistant') return state.messages[i]
      }
      return null
    },
  },
  actions: {
    setExistingResume(md: string) {
      this.existingResume = md
    },
    clearExistingResume() {
      this.existingResume = ''
    },
    clear() {
      this.messages = []
      this.status = null
      this.intent = null
      this.streaming = false
      this.conversationId = null
      this.clearConversationId()
    },
    /** 把会话 id 记入本标签页 sessionStorage(刷新后续接同一会话) */
    persistConversationId(id: number) {
      try {
        sessionStorage.setItem(CONVERSATION_KEY, String(id))
      } catch {
        /* 隐私模式等场景 sessionStorage 不可用,忽略 */
      }
    },
    /** 清除本标签页记录的会话 id(清空对话时调用,下一条消息另起新会话) */
    clearConversationId() {
      try {
        sessionStorage.removeItem(CONVERSATION_KEY)
      } catch {
        /* 忽略 */
      }
    },
    /** 拉取历史会话列表(契约 4.9);失败静默降级保留旧列表,不抛给调用方 */
    async loadHistory(page = 1, pageSize = 20, append = false) {
      this.historyLoading = true
      try {
        const r = await getConversations(page, pageSize)
        this.history = append ? [...this.history, ...r.conversations] : r.conversations
        this.historyTotal = r.total
      } catch {
        /* 后端不可达时保持面板可用(与挂载期拉取的静默降级惯例一致) */
      } finally {
        this.historyLoading = false
      }
    },
    /** 打开历史会话(契约 4.10):回填消息并切换当前会话,后续 send 续接该会话 */
    async openConversation(id: number) {
      // 流式期间不切会话,避免两条流交叉写消息列表
      if (this.streaming) return
      const r = await getConversationMessages(id)
      this.messages = r.messages
        .filter((m): m is MessageOut & { role: 'user' | 'assistant' } => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({
          id: `hist_${m.id}`,
          role: m.role,
          content: m.content,
          intent: m.intent ?? undefined,
        }))
      this.conversationId = id
      this.persistConversationId(id)
      this.status = null
      this.intent = null
    },
    /** 将单个 SSE 事件应用到助手消息(纯逻辑,便于单测) */
    applyEvent(msg: ChatMessage, evt: ChatEvent) {
      switch (evt.type) {
        case 'conversation':
          // 流首帧:记录会话 id 并持久化,后续消息经 send 回传以归入同一会话
          this.conversationId = evt.conversation_id
          this.persistConversationId(evt.conversation_id)
          break
        case 'status':
          this.status = evt.text
          msg.status = evt.text
          break
        case 'intent':
          this.intent = evt.value
          msg.intent = evt.value
          break
        case 'token':
          msg.content += evt.text
          break
        case 'done':
          msg.intent = evt.intent
          msg.streaming = false
          if (evt.resume_final) msg.resumeFinal = evt.resume_final
          if (evt.retrieved_knowledge) msg.retrievedKnowledge = evt.retrieved_knowledge
          if (evt.retrieved_projects) msg.retrievedProjects = evt.retrieved_projects
          this.status = null
          this.streaming = false
          break
        case 'error':
          msg.error = evt.message
          msg.streaming = false
          this.streaming = false
          break
      }
    },
    async send(prompt: string) {
      const text = prompt.trim()
      if (!text || this.streaming) return

      this.messages.push({ id: nextId(), role: 'user', content: text })
      this.messages.push({ id: nextId(), role: 'assistant', content: '', streaming: true })
      // 取回响应式代理引用(直接改动原始对象不会触发视图更新)
      const msg = this.messages[this.messages.length - 1]

      this.streaming = true
      this.status = null
      this.intent = null

      const { start, abort } = useChatStream({
        onEvent: (evt) => this.applyEvent(msg, evt),
      })
      activeAbort = abort
      try {
        await start({
          prompt: text,
          existing_resume: this.existingResume || undefined,
          conversation_id: this.conversationId ?? undefined,
        })
      } finally {
        activeAbort = null
        this.streaming = false
      }
    },
    stop() {
      activeAbort?.()
      activeAbort = null
      this.streaming = false
    },
  },
})
