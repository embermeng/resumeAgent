import { describe, it, expect, afterEach } from 'vitest'
import * as client from './client'
import { stubFetch, jsonResponse } from '@/test/sse-helpers'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('api/client', () => {
  it('getSupportedExtensions:GET 并返回扩展名列表', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ extensions: ['.md', '.txt', '.docx', '.pdf'] }))
    const res = await client.getSupportedExtensions()
    expect(res.extensions).toEqual(['.md', '.txt', '.docx', '.pdf'])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/resume/supported-extensions')
  })

  it('parseResume:multipart 上传 file,POST /api/resume/parse,返回 202 回执(task_id)', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ task_id: 't1', status: 'pending' }, { status: 202 }))
    const file = new File(['# A'], 'a.md', { type: 'text/markdown' })
    const res = await client.parseResume(file)
    expect(res).toEqual({ task_id: 't1', status: 'pending' })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/resume/parse')
    expect(opts?.method).toBe('POST')
    expect(opts?.body).toBeInstanceOf(FormData)
    const sent = (opts?.body as FormData).get('file')
    expect(sent).toBeInstanceOf(File)
    expect((sent as File).name).toBe('a.md')
  })

  it('getResumeParseStatus:GET /api/resume/parse/{id},task_id URL 编码,success 带 content', async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse({ task_id: 'a/b', status: 'success', filename: 'a.md', content: '# A', created_at: 100 }),
    )
    const res = await client.getResumeParseStatus('a/b')
    expect(res.status).toBe('success')
    expect(res.content).toBe('# A')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/resume/parse/a%2Fb')
  })

  it('buildKnowledge:POST JSON,返回 task_id/status', async () => {
    const fetchMock = stubFetch(async () => jsonResponse({ task_id: 't1', status: 'pending' }, { status: 202 }))
    const res = await client.buildKnowledge({ task: 'build-all', force: true, chunk_size: 300 })
    expect(res).toEqual({ task_id: 't1', status: 'pending' })

    const [url, opts] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/knowledge/build')
    expect(opts?.method).toBe('POST')
    expect(JSON.parse(String(opts?.body))).toEqual({ task: 'build-all', force: true, chunk_size: 300 })
    expect((opts?.headers as Record<string, string>)['Content-Type']).toBe('application/json')
  })

  it('getTaskStatus:GET 快照,task_id URL 编码', async () => {
    const fetchMock = stubFetch(async () =>
      jsonResponse({ task_id: 't1', task: 'build-all', status: 'running', created_at: 100 }),
    )
    const res = await client.getTaskStatus('t1')
    expect(res.status).toBe('running')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/knowledge/tasks/t1')
  })

  it('health:返回 {status:"ok"}', async () => {
    stubFetch(async () => jsonResponse({ status: 'ok' }))
    await expect(client.health()).resolves.toEqual({ status: 'ok' })
  })

  it('非 2xx:抛出后端 detail 文本(400 不支持格式)', async () => {
    stubFetch(async () =>
      jsonResponse({ detail: '不支持的简历文件格式: .exe' }, { ok: false, status: 400 }),
    )
    await expect(client.parseResume(new File(['x'], 'a.exe'))).rejects.toThrow(
      '不支持的简历文件格式: .exe',
    )
  })

  it('非 2xx 且错误体非 JSON:回退到 HTTP 状态文本', async () => {
    stubFetch(async () =>
      ({
        ok: false,
        status: 500,
        json: async () => {
          throw new Error('not json')
        },
      }) as unknown as Response,
    )
    await expect(client.getTaskStatus('x')).rejects.toThrow('HTTP 500')
  })
})
