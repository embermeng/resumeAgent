import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useKnowledgeStore } from './knowledge'
import { sseResponse, stubFetch, jsonResponse } from '@/test/sse-helpers'

beforeEach(() => {
  setActivePinia(createPinia())
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function seed(store: ReturnType<typeof useKnowledgeStore>) {
  store.tasks.push({ taskId: 't1', kind: 'build-all', status: 'running', percent: 0, logs: [] })
}

describe('knowledge store', () => {
  it('applyEvent: progress 更新 stage/percent/message 并置 running', () => {
    const store = useKnowledgeStore()
    seed(store)
    store.applyEvent('t1', { type: 'progress', stage: 'parse-pdfs', message: '[1/5] 解析PDF...', percent: 10 })
    const t = store.tasks[0]
    expect(t.percent).toBe(10)
    expect(t.stage).toBe('parse-pdfs')
    expect(t.message).toBe('[1/5] 解析PDF...')
    expect(t.status).toBe('running')
  })

  it('applyEvent: log 追加到日志数组', () => {
    const store = useKnowledgeStore()
    seed(store)
    store.applyEvent('t1', { type: 'log', line: 'ingesting a.pdf' })
    store.applyEvent('t1', { type: 'log', line: 'ingesting b.pdf' })
    expect(store.tasks[0].logs).toEqual(['ingesting a.pdf', 'ingesting b.pdf'])
  })

  it('applyEvent: done(success)置 100 并结束 building', () => {
    const store = useKnowledgeStore()
    store.building = true
    seed(store)
    store.tasks[0].percent = 90
    store.applyEvent('t1', { type: 'done', task_id: 't1', status: 'success', elapsed: 5 })
    expect(store.tasks[0].status).toBe('success')
    expect(store.tasks[0].percent).toBe(100)
    expect(store.tasks[0].elapsed).toBe(5)
    expect(store.building).toBe(false)
  })

  it('applyEvent: error 置 failed 并记录原因', () => {
    const store = useKnowledgeStore()
    store.building = true
    seed(store)
    store.applyEvent('t1', { type: 'error', message: '构建炸了' })
    expect(store.tasks[0].status).toBe('failed')
    expect(store.tasks[0].error).toBe('构建炸了')
    expect(store.building).toBe(false)
  })

  it('applyEvent: 未知 taskId 被忽略,不抛错', () => {
    const store = useKnowledgeStore()
    seed(store)
    expect(() =>
      store.applyEvent('nope', { type: 'progress', stage: 'x', message: 'y', percent: 5 }),
    ).not.toThrow()
    expect(store.tasks[0].percent).toBe(0)
  })

  it('startBuild: 建任务→订阅进度流→跑完置 success', async () => {
    const fetchMock = stubFetch(async (url) => {
      if (url.endsWith('/knowledge/build')) return jsonResponse({ task_id: 't1', status: 'pending' }, { status: 202 })
      if (url.includes('/tasks/t1/stream'))
        return sseResponse([
          'event: progress\ndata: {"stage":"parse-pdfs","message":"[1/5]","percent":10}\n\n',
          'event: progress\ndata: {"stage":"build-indexes","message":"[5/5]","percent":90}\n\n',
          'event: done\ndata: {"task_id":"t1","status":"success","elapsed":3}\n\n',
        ])
      throw new Error('unexpected url ' + url)
    })
    const store = useKnowledgeStore()
    await store.startBuild({ task: 'build-all' })

    expect(fetchMock.mock.calls[0][0]).toBe('/api/knowledge/build')
    expect(store.tasks).toHaveLength(1)
    expect(store.tasks[0].taskId).toBe('t1')
    expect(store.tasks[0].kind).toBe('build-all')
    expect(store.tasks[0].status).toBe('success')
    expect(store.tasks[0].percent).toBe(100)
    expect(store.building).toBe(false)
  })

  it('startBuild: 进度流 error 帧使任务 failed', async () => {
    stubFetch(async (url) => {
      if (url.endsWith('/knowledge/build')) return jsonResponse({ task_id: 't2', status: 'pending' }, { status: 202 })
      return sseResponse(['event: error\ndata: {"message":"磁盘不足"}\n\n'])
    })
    const store = useKnowledgeStore()
    await store.startBuild({ task: 'parse-pdfs' })
    expect(store.tasks[0].status).toBe('failed')
    expect(store.tasks[0].error).toBe('磁盘不足')
  })

  it('clearTasks: 清空任务列表', () => {
    const store = useKnowledgeStore()
    seed(store)
    store.clearTasks()
    expect(store.tasks).toHaveLength(0)
  })
})
