import { describe, it, expect, afterEach } from 'vitest'
import { useTaskStream } from './useTaskStream'
import type { TaskEvent } from '@/types/events'
import { sseResponse, stubFetch } from '@/test/sse-helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('useTaskStream', () => {
  it('按序分发 progress/log/done,percent 逐步更新', async () => {
    const events: TaskEvent[] = []
    stubFetch(async () =>
      sseResponse([
        'event: progress\ndata: {"stage":"parse-pdfs","message":"[1/5] 解析PDF...","percent":10}\n\n',
        'event: log\ndata: {"line":"解析 a.pdf"}\n\n',
        'event: progress\ndata: {"stage":"build-indexes","message":"[5/5] 构建索引...","percent":90}\n\n',
        'event: done\ndata: {"task_id":"t1","status":"success","elapsed":12.3}\n\n',
      ]),
    )
    const { start, streaming } = useTaskStream({ onEvent: (e) => events.push(e) })
    await start('t1')

    expect(events.map((e) => e.type)).toEqual(['progress', 'log', 'progress', 'done'])
    const percents = events
      .filter((e): e is Extract<TaskEvent, { type: 'progress' }> => e.type === 'progress')
      .map((e) => e.percent)
    expect(percents).toEqual([10, 90])
    const done = events.at(-1) as Extract<TaskEvent, { type: 'done' }>
    expect(done.status).toBe('success')
    expect(done.elapsed).toBe(12.3)
    expect(streaming.value).toBe(false)
  })

  it('请求地址为任务流路径且方法为 GET', async () => {
    const fetchMock = stubFetch(async () =>
      sseResponse(['event: done\ndata: {"task_id":"abc","status":"success","elapsed":1}\n\n']),
    )
    const { start } = useTaskStream()
    await start('abc')
    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/knowledge/tasks/abc/stream')
    expect(opts?.method).toBe('GET')
  })

  it('task_id 含特殊字符时 URL 编码', async () => {
    const fetchMock = stubFetch(async () => sseResponse([]))
    const { start } = useTaskStream()
    await start('a/b c')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/knowledge/tasks/a%2Fb%20c/stream')
  })

  it('error 帧:设置 error 并派发', async () => {
    const events: TaskEvent[] = []
    stubFetch(async () =>
      sseResponse(['event: error\ndata: {"message":"构建失败"}\n\n']),
    )
    const { start, error } = useTaskStream({ onEvent: (e) => events.push(e) })
    await start('t1')
    expect(error.value).toBe('构建失败')
    expect(events.at(-1)).toEqual({ type: 'error', message: '构建失败' })
  })

  it('HTTP 404(任务不存在):转为 error 事件', async () => {
    const events: TaskEvent[] = []
    stubFetch(async () => sseResponse([], { ok: false, status: 404 }))
    const { start, error } = useTaskStream({ onEvent: (e) => events.push(e) })
    await start('missing')
    expect(error.value).toContain('404')
    expect(events.some((e) => e.type === 'error')).toBe(true)
  })
})
