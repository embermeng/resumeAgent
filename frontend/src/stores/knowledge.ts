import { defineStore } from 'pinia'
import * as api from '@/api/client'
import { useTaskStream } from '@/composables/useTaskStream'
import type { BuildRequest, TaskEvent, TaskKind, TaskState, TaskStatus } from '@/types/events'

/** 当前标签页正在跟踪的任务 id 的 sessionStorage 键(按标签页隔离,刷新可恢复) */
const CURRENT_TASK_KEY = 'resumeagent.current_task_id'

export interface KnowledgeTask {
  taskId: string
  kind: TaskKind
  status: TaskState
  stage?: string
  percent: number
  message?: string
  logs: string[]
  error?: string
  elapsed?: number
}

/** 后端 TaskStatus(REST) → 前端 KnowledgeTask(SSE 视图模型) */
function fromStatus(s: TaskStatus): KnowledgeTask {
  return {
    taskId: s.task_id,
    kind: s.task,
    status: s.status,
    stage: s.stage,
    percent: s.percent ?? 0,
    message: s.message,
    logs: [],
    error: s.error,
  }
}

export const useKnowledgeStore = defineStore('knowledge', {
  state: () => ({
    tasks: [] as KnowledgeTask[],
    /** 是否有任务在进行中 */
    building: false,
    /** 历史任务列表(来自 GET /knowledge/tasks,降序) */
    history: [] as TaskStatus[],
    historyTotal: 0,
  }),
  getters: {
    latest(state): KnowledgeTask | null {
      return state.tasks.length ? state.tasks[state.tasks.length - 1] : null
    },
  },
  actions: {
    /** 将单个任务 SSE 事件应用到对应任务(纯逻辑,便于单测) */
    applyEvent(taskId: string, evt: TaskEvent) {
      const t = this.tasks.find((x) => x.taskId === taskId)
      if (!t) return
      switch (evt.type) {
        case 'progress':
          t.status = 'running'
          t.stage = evt.stage
          t.message = evt.message
          t.percent = evt.percent
          break
        case 'log':
          t.logs.push(evt.line)
          break
        case 'done':
          t.status = evt.status
          t.elapsed = evt.elapsed
          if (evt.status === 'success') t.percent = 100
          this.building = false
          break
        case 'error':
          t.status = 'failed'
          t.error = evt.message
          this.building = false
          break
      }
    },
    /**
     * 触发构建:POST /knowledge/build 建任务,随后订阅其 SSE 进度流。
     * 返回的 Promise 在进度流结束时 resolve(便于测试/等待)。
     */
    async startBuild(req: BuildRequest): Promise<void> {
      const ack = await api.buildKnowledge(req)
      const taskId = ack.task_id
      this.tasks.push({
        taskId,
        kind: req.task,
        status: ack.status,
        percent: 0,
        logs: [],
      })
      this.building = true
      this.persistCurrentTask(taskId)

      await this.subscribeTask(taskId)
    },
    /** 订阅某任务的 SSE 进度流(内部复用,错误已通过 error 事件反映到状态) */
    subscribeTask(taskId: string): Promise<void> {
      const { start } = useTaskStream({
        onEvent: (evt) => this.applyEvent(taskId, evt),
      })
      return start(taskId).catch(() => {
        /* 错误已通过 error 事件反映到任务状态,这里不再向上抛 */
      })
    },
    /** 把当前任务 id 记入本标签页的 sessionStorage(刷新后可恢复) */
    persistCurrentTask(taskId: string) {
      try {
        sessionStorage.setItem(CURRENT_TASK_KEY, taskId)
      } catch {
        /* 隐私模式等场景 sessionStorage 不可用,忽略 */
      }
    },
    /** 插入或更新一个任务视图模型(避免恢复时重复 push) */
    upsertTask(t: KnowledgeTask) {
      const i = this.tasks.findIndex((x) => x.taskId === t.taskId)
      if (i >= 0) this.tasks[i] = { ...this.tasks[i], ...t, logs: this.tasks[i].logs }
      else this.tasks.push(t)
    },
    /**
     * 刷新后恢复本标签页正在跟踪的任务:
     * 读 sessionStorage 的 task_id → 查状态 → 若仍在跑则重连 SSE(subscribe 会全量重放历史)。
     * 全程兜底:后端不可达/任务已 404 时静默忽略,不影响渲染。
     */
    async restoreCurrentTask(): Promise<void> {
      let id: string | null = null
      try {
        id = sessionStorage.getItem(CURRENT_TASK_KEY)
      } catch {
        return
      }
      if (!id) return
      try {
        const s = await api.getTaskStatus(id)
        this.upsertTask(fromStatus(s))
        if (s.status === 'running' || s.status === 'pending') {
          this.building = true
          void this.subscribeTask(id)
        }
      } catch {
        /* 任务不存在或后端不可达,放弃恢复 */
      }
    },
    /** 拉取历史任务列表(降序),失败时清空而非抛错 */
    async loadHistory(page = 1, pageSize = 10): Promise<void> {
      try {
        const r = await api.getTaskList(page, pageSize)
        this.history = r.tasks
        this.historyTotal = r.total
      } catch {
        this.history = []
        this.historyTotal = 0
      }
    },
    clearTasks() {
      this.tasks = []
      this.building = false
    },
  },
})
