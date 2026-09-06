import { defineStore } from 'pinia'
import { useChatStream } from '@/composables/useChatStream'
import type { ChatEvent, Intent } from '@/types/events'

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
    },
    /** 将单个 SSE 事件应用到助手消息(纯逻辑,便于单测) */
    applyEvent(msg: ChatMessage, evt: ChatEvent) {
      switch (evt.type) {
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
        await start({ prompt: text, existing_resume: this.existingResume || undefined })
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
