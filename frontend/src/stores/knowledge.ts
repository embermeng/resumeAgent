import { defineStore } from 'pinia'
import * as api from '@/api/client'
import { useTaskStream } from '@/composables/useTaskStream'
import type { BuildRequest, TaskEvent, TaskKind, TaskState } from '@/types/events'

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

export const useKnowledgeStore = defineStore('knowledge', {
  state: () => ({
    tasks: [] as KnowledgeTask[],
    /** 是否有任务在进行中 */
    building: false,
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

      const { start } = useTaskStream({
        onEvent: (evt) => this.applyEvent(taskId, evt),
      })
      await start(taskId).catch(() => {
        /* 错误已通过 error 事件反映到任务状态,这里不再向上抛 */
      })
    },
    clearTasks() {
      this.tasks = []
      this.building = false
    },
  },
})
