import { defineStore } from 'pinia'
import { useChatStream } from '@/composables/useChatStream'
import { getConversationMessages, getConversations } from '@/api/client'
import type { ChatEvent, ConversationSummary, Intent, MessageOut } from '@/types/events'

/** 当前标签页所属会话 id 的 sessionStorage 键(按标签页隔离,刷新可续接同一会话) */
const CONVERSATION_KEY = 'resumeagent.conversation_id'
/** 流式进行中草稿的 sessionStorage 键(刷新/断线后保全已生成内容) */
const DRAFT_KEY = 'resumeagent.draft'

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

export interface TimelineStage {
  text: string
  /** 阶段开始时刻(ms,performance.now 同时基) */
  at: number
  /** 阶段耗时(ms);进行中为 null,下一帧/done 时结算 */
  elapsed: number | null
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
  /** 工具调用时间线(status 帧逐条收集,含每阶段耗时) */
  timeline?: TimelineStage[]
  /** 首 token 耗时(ms,send 发起 → 首个 token 帧) */
  ttftMs?: number
  /** 全流程耗时(ms,send 发起 → done/error) */
  elapsedMs?: number
  /** 断线/出错后重试的次数 */
  retryCount?: number
}

let seq = 0
function nextId(): string {
  seq += 1
  return `msg_${Date.now().toString(36)}_${seq}`
}

// 单用户单流:模块级保存当前中断句柄(stop 用)与本次发送的计时起点(TTFT/总耗时埋点)
let activeAbort: (() => void) | null = null
let sendStartedAt = 0
// 草稿写入节流:避免每个 token 都同步写 sessionStorage
let lastDraftWrite = 0
const DRAFT_WRITE_INTERVAL_MS = 300
// 用户是否主动 stop():区分「手动停止」与「断线/出错」,决定 streamInto 的 finally 是否补写草稿
let stoppedManually = false

/** 流式草稿(刷新后可恢复已生成内容) */
export interface StreamDraft {
  prompt: string
  content: string
  conversationId: number | null
  savedAt: number
}

function readDraft(): StreamDraft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY)
    if (!raw) return null
    const d = JSON.parse(raw) as StreamDraft
    return d && typeof d.content === 'string' && d.content ? d : null
  } catch {
    return null
  }
}

function writeDraft(d: StreamDraft) {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(d))
  } catch {
    /* 隐私模式等场景不可用,忽略 */
  }
}

function removeDraft() {
  try {
    sessionStorage.removeItem(DRAFT_KEY)
  } catch {
    /* 忽略 */
  }
}

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
    /** 断线重连进行中(「恢复中」):POST 流断线后自动 GET 续传期间为 true */
    reconnecting: false,
    /** 当前会话 id(来自 conversation 首帧;刷新后从 sessionStorage 续接) */
    conversationId: readConversationId() as number | null,
    /** 历史会话列表(drawer 面板数据源,updated_at 降序) */
    history: [] as ConversationSummary[],
    historyTotal: 0,
    historyLoading: false,
    /** 刷新后待恢复的流式草稿(内容保全,由 ChatView 展示恢复横幅) */
    recoverableDraft: readDraft() as StreamDraft | null,
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
      this.reconnecting = false
      this.conversationId = null
      this.recoverableDraft = null
      removeDraft()
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
      // 切会话后旧草稿不再属于当前上下文,丢弃避免恢复横幅误导
      this.recoverableDraft = null
      removeDraft()
    },
    /** 把刷新前保全的草稿恢复为消息对(已生成内容不丢),并清除草稿 */
    restoreDraft() {
      const d = this.recoverableDraft
      if (!d) return
      // prompt 为空 = 用户手动停止的生成,不伪造用户消息
      if (d.prompt) this.messages.push({ id: nextId(), role: 'user', content: d.prompt })
      this.messages.push({
        id: nextId(),
        role: 'assistant',
        content: d.content,
        error: '连接中断,已保全生成内容',
      })
      if (d.conversationId !== null && d.conversationId !== undefined) {
        this.conversationId = d.conversationId
        this.persistConversationId(d.conversationId)
      }
      this.recoverableDraft = null
      removeDraft()
    },
    /** 放弃恢复保全的草稿 */
    discardDraft() {
      this.recoverableDraft = null
      removeDraft()
    },
    /**
     * 断线重连最终失败(缓冲过期 404 / 超过重试次数)的降级处理:
     * 不自动从头重发(会重复生成、多花一次 LLM),而是标记中断并交由 finally 保全草稿,
     * 用户可选择手动重试或刷新后从草稿恢复。
     */
    handleReconnectFailed(msg: ChatMessage, reason: 'expired' | 'exhausted') {
      msg.streaming = false
      msg.error =
        reason === 'expired'
          ? '连接中断,云端缓冲已过期,已保全本地生成内容'
          : '连接中断,多次重连失败,已保全本地生成内容'
    },
    /** 将单个 SSE 事件应用到助手消息(纯逻辑,便于单测) */
    applyEvent(msg: ChatMessage, evt: ChatEvent) {
      switch (evt.type) {
        case 'conversation':
          // 流首帧:记录会话 id 并持久化,后续消息经 send 回传以归入同一会话
          this.conversationId = evt.conversation_id
          this.persistConversationId(evt.conversation_id)
          break
        case 'status': {
          this.status = evt.text
          msg.status = evt.text
          // 工具调用时间线:结算上一阶段耗时,开启新阶段
          const now = performance.now()
          const tl = (msg.timeline ??= [])
          const prev = tl[tl.length - 1]
          if (prev && prev.elapsed === null) prev.elapsed = now - prev.at
          tl.push({ text: evt.text, at: now, elapsed: null })
          break
        }
        case 'intent':
          this.intent = evt.value
          msg.intent = evt.value
          break
        case 'token':
          // TTFT 埋点:首个 token 帧到达时记录(与后端日志的首token耗时同口径可对照)
          if (msg.ttftMs === undefined && sendStartedAt > 0) {
            msg.ttftMs = performance.now() - sendStartedAt
          }
          msg.content += evt.text
          break
        case 'done':
          msg.intent = evt.intent
          msg.streaming = false
          if (evt.resume_final) msg.resumeFinal = evt.resume_final
          if (evt.retrieved_knowledge) msg.retrievedKnowledge = evt.retrieved_knowledge
          if (evt.retrieved_projects) msg.retrievedProjects = evt.retrieved_projects
          this.finishTimeline(msg)
          if (sendStartedAt > 0) msg.elapsedMs = performance.now() - sendStartedAt
          this.status = null
          this.streaming = false
          // 流正常结束:草稿已无保全价值
          this.recoverableDraft = null
          removeDraft()
          break
        case 'error':
          msg.error = evt.message
          msg.streaming = false
          this.finishTimeline(msg)
          if (sendStartedAt > 0) msg.elapsedMs = performance.now() - sendStartedAt
          this.streaming = false
          break
      }
    },
    /** 结算时间线末阶段耗时(done/error/stop 时调用) */
    finishTimeline(msg: ChatMessage) {
      const tl = msg.timeline
      if (!tl?.length) return
      const last = tl[tl.length - 1]
      if (last.elapsed === null) last.elapsed = performance.now() - last.at
    },
    async send(prompt: string) {
      const text = prompt.trim()
      if (!text || this.streaming) return

      this.messages.push({ id: nextId(), role: 'user', content: text })
      this.messages.push({ id: nextId(), role: 'assistant', content: '', streaming: true })
      await this.streamInto(this.messages[this.messages.length - 1], text)
    },
    /**
     * 断线/出错后重试:重新发起同一 prompt,复用原助手气泡。
     * 已生成的部分内容被替换(后端会重新完整生成),时间线/埋点重置。
     */
    async retryMessage(assistantId: string) {
      if (this.streaming) return
      const idx = this.messages.findIndex((m) => m.id === assistantId)
      if (idx <= 0) return
      let prompt: string | null = null
      for (let i = idx - 1; i >= 0; i--) {
        if (this.messages[i].role === 'user') {
          prompt = this.messages[i].content
          break
        }
      }
      if (!prompt) return
      const msg = this.messages[idx]
      msg.content = ''
      msg.error = null
      msg.timeline = []
      msg.ttftMs = undefined
      msg.elapsedMs = undefined
      msg.resumeFinal = undefined
      msg.retrievedKnowledge = undefined
      msg.retrievedProjects = undefined
      msg.retryCount = (msg.retryCount ?? 0) + 1
      msg.streaming = true
      await this.streamInto(msg, prompt)
    },
    /** 对指定助手消息发起一次完整流式请求(send/retry 共用) */
    async streamInto(rawMsg: ChatMessage, prompt: string) {
      // 取回响应式代理引用(直接改动原始对象不会触发视图更新)
      const msg = this.messages[this.messages.length - 1] === rawMsg
        ? this.messages[this.messages.length - 1]
        : (this.messages.find((m) => m.id === rawMsg.id) ?? rawMsg)

      this.streaming = true
      this.reconnecting = false
      this.status = null
      this.intent = null
      sendStartedAt = performance.now()
      lastDraftWrite = 0 // 重置节流窗口:新流首个 token 立即写草稿,不被上一轮时间戳挡住
      stoppedManually = false
      let doneOk = false // 是否收到终态 done 帧:区分正常结束与断线/出错,决定 finally 是否补写草稿

      const { start, abort } = useChatStream({
        onEvent: (evt) => {
          this.applyEvent(msg, evt)
          if (evt.type === 'done') doneOk = true
          // 内容保全:流式期间把已生成内容节流写入 sessionStorage,刷新/断线不丢
          if (evt.type === 'token' && msg.content) {
            const now = Date.now()
            if (now - lastDraftWrite >= DRAFT_WRITE_INTERVAL_MS) {
              lastDraftWrite = now
              writeDraft({
                prompt,
                content: msg.content,
                conversationId: this.conversationId,
                savedAt: now,
              })
            }
          }
        },
        // 断线重连:进入/退出「恢复中」同步到 store 驱动 UI;最终失败则降级保全(不自动重发)
        onReconnectingChange: (v) => {
          this.reconnecting = v
        },
        onReconnectFailed: (reason) => this.handleReconnectFailed(msg, reason),
      })
      activeAbort = abort
      try {
        await start({
          prompt,
          existing_resume: this.existingResume || undefined,
          conversation_id: this.conversationId ?? undefined,
        })
      } finally {
        activeAbort = null
        sendStartedAt = 0
        this.streaming = false
        this.reconnecting = false
        msg.streaming = false
        this.finishTimeline(msg)
        // 断线/出错(未收到 done)且非手动停止:补写一次完整草稿,避免节流丢尾巴
        // (手动停止由 stop() 以 prompt:'' 自行保全,此处跳过以免覆盖)
        if (!doneOk && !stoppedManually && msg.content) {
          writeDraft({
            prompt,
            content: msg.content,
            conversationId: this.conversationId,
            savedAt: Date.now(),
          })
        }
      }
    },
    stop() {
      stoppedManually = true // 标记手动停止:streamInto 的 finally 不再补写草稿(保留下面的 prompt:'')
      activeAbort?.()
      activeAbort = null
      this.streaming = false
      this.reconnecting = false
      // 主动停止也保全已生成内容(含末阶段耗时结算由最后一个助手消息承担)
      const last = this.lastAssistant
      if (last) {
        this.finishTimeline(last)
        if (last.content) {
          writeDraft({
            prompt: '',
            content: last.content,
            conversationId: this.conversationId,
            savedAt: Date.now(),
          })
        }
      }
    },
  },
})
