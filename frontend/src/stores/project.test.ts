import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useProjectStore } from './project'
import * as api from '@/api/client'
import type { ProjectDocOut, ProjectPreprocessOut } from '@/types/events'
import { sseResponse, stubFetch } from '@/test/sse-helpers'

beforeEach(() => {
  setActivePinia(createPinia())
})
afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

function doc(over: Partial<ProjectDocOut> = {}): ProjectDocOut {
  return {
    doc_id: 'project-u9-a',
    project_name: '甲项目',
    file_name: 'a.md',
    tags: [],
    has_resume_section: true,
    is_public: false,
    created_at: 1,
    updated_at: 1,
    ...over,
  }
}

function seedList(store: ReturnType<typeof useProjectStore>, docs: ProjectDocOut[]) {
  store.docs = docs
  store.maxDocs = 30
}

describe('project store', () => {
  it('loadDocs 拉取列表并记录配额', async () => {
    const spy = vi
      .spyOn(api, 'listProjectDocs')
      .mockResolvedValue({ docs: [doc()], total: 1, max_docs: 30 })
    const store = useProjectStore()

    await store.loadDocs()

    expect(spy).toHaveBeenCalled()
    expect(store.docs).toHaveLength(1)
    expect(store.maxDocs).toBe(30)
    expect(store.loading).toBe(false)
  })

  it('loadDocs 失败时清空而非抛错(侧边栏入口不阻塞主流程)', async () => {
    vi.spyOn(api, 'listProjectDocs').mockRejectedValue(new Error('HTTP 500'))
    const store = useProjectStore()
    seedList(store, [doc()])

    await store.loadDocs()

    expect(store.docs).toEqual([])
    expect(store.error).toContain('500')
  })

  it('mineCount 只统计非公共素材', () => {
    const store = useProjectStore()
    seedList(store, [doc(), doc({ doc_id: 'b', is_public: true }), doc({ doc_id: 'c' })])
    expect(store.mineCount).toBe(2)
  })

  it('atQuota 在达到上限时为真', () => {
    const store = useProjectStore()
    seedList(store, [doc()])
    expect(store.atQuota).toBe(false)

    store.docs = Array.from({ length: 30 }, (_, i) => doc({ doc_id: `d${i}` }))
    expect(store.atQuota).toBe(true)
  })

  it('missingResumeSection 列出缺章节的文档', () => {
    const store = useProjectStore()
    seedList(store, [doc({ has_resume_section: false }), doc({ doc_id: 'b' })])
    expect(store.missingResumeSection.map((d) => d.doc_id)).toEqual(['project-u9-a'])
  })

  it('uploadDoc 成功:订阅 SSE 并刷新列表', async () => {
    const file = new File(['# P'], 'a.md', { type: 'text/markdown' })
    vi.spyOn(api, 'uploadProjectDoc').mockResolvedValue({ task_id: 't1', status: 'pending' })
    const listSpy = vi
      .spyOn(api, 'listProjectDocs')
      .mockResolvedValue({ docs: [doc()], total: 1, max_docs: 30 })

    stubFetch(async () =>
      sseResponse([
        'event: progress\ndata: {"stage":"indexing","message":"构建向量索引…","percent":70}\n\n',
        'event: done\ndata: {"task_id":"t1","status":"success"}\n\n',
      ]),
    )

    const store = useProjectStore()
    const ok = await store.uploadDoc(file)

    expect(ok).toBe(true)
    expect(store.upload?.status).toBe('success')
    expect(listSpy).toHaveBeenCalled()
    expect(store.docs).toHaveLength(1)
  })

  it('uploadDoc: SSE 报 failed 时返回 false 并保留原因', async () => {
    const file = new File(['# P'], 'a.md', { type: 'text/markdown' })
    vi.spyOn(api, 'uploadProjectDoc').mockResolvedValue({ task_id: 't1', status: 'pending' })

    // 真实失败流:先 error 帧带原因,再由 DB 快照推出 done(failed)
    stubFetch(async () =>
      sseResponse([
        'event: error\ndata: {"message":"文档缺少一级标题"}\n\n',
        'event: done\ndata: {"task_id":"t1","status":"failed"}\n\n',
      ]),
    )

    const store = useProjectStore()
    const ok = await store.uploadDoc(file)

    expect(ok).toBe(false)
    expect(store.upload?.status).toBe('failed')
    expect(store.error).toContain('一级标题')
  })

  it('uploadDoc: SSE 未给终态时以快照兜底', async () => {
    const file = new File(['# P'], 'a.md', { type: 'text/markdown' })
    vi.spyOn(api, 'uploadProjectDoc').mockResolvedValue({ task_id: 't1', status: 'pending' })
    vi.spyOn(api, 'listProjectDocs').mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
    const statusSpy = vi
      .spyOn(api, 'getProjectUploadStatus')
      .mockResolvedValue({ task_id: 't1', status: 'failed', error: '索引失败', created_at: 1 })

    stubFetch(async () => sseResponse([])) // 空流:无 done 帧

    const store = useProjectStore()
    const ok = await store.uploadDoc(file)

    expect(statusSpy).toHaveBeenCalledWith('t1')
    expect(ok).toBe(false)
    expect(store.error).toContain('索引失败')
  })

  it('uploadDoc: 提交失败时置 failed', async () => {
    const file = new File(['# P'], 'a.md', { type: 'text/markdown' })
    vi.spyOn(api, 'uploadProjectDoc').mockRejectedValue(new Error('只支持 Markdown 文件(.md)'))
    const store = useProjectStore()

    expect(await store.uploadDoc(file)).toBe(false)
    expect(store.upload?.status).toBe('failed')
    expect(store.error).toContain('Markdown')
  })

  it('removeDoc 成功后从列表移除', async () => {
    const spy = vi.spyOn(api, 'deleteProjectDoc').mockResolvedValue(undefined)
    const store = useProjectStore()
    seedList(store, [doc(), doc({ doc_id: 'b' })])

    expect(await store.removeDoc('project-u9-a')).toBe(true)
    expect(spy).toHaveBeenCalledWith('project-u9-a')
    expect(store.docs.map((d) => d.doc_id)).toEqual(['b'])
  })

  it('removeDoc 失败(如越权 404)时保留列表并记录原因', async () => {
    vi.spyOn(api, 'deleteProjectDoc').mockRejectedValue(new Error('project doc not found'))
    const store = useProjectStore()
    seedList(store, [doc()])

    expect(await store.removeDoc('project-u9-a')).toBe(false)
    expect(store.docs).toHaveLength(1)
    expect(store.error).toContain('not found')
  })

  it('removeDocs 成功:移除已删的,保留被跳过的', async () => {
    vi.spyOn(api, 'batchDeleteProjectDocs').mockResolvedValue({
      deleted_ids: ['project-u9-a'],
      not_found_ids: ['b'],
    })
    const store = useProjectStore()
    seedList(store, [doc(), doc({ doc_id: 'b' }), doc({ doc_id: 'c' })])

    const r = await store.removeDocs(['project-u9-a', 'b'])

    expect(r?.deleted_ids).toEqual(['project-u9-a'])
    expect(r?.not_found_ids).toEqual(['b'])
    // 只移除真正删掉的;被跳过的仍留在列表
    expect(store.docs.map((d) => d.doc_id)).toEqual(['b', 'c'])
  })

  it('removeDocs 传空数组时直接返回 null(不发请求)', async () => {
    const spy = vi.spyOn(api, 'batchDeleteProjectDocs')
    const store = useProjectStore()
    expect(await store.removeDocs([])).toBeNull()
    expect(spy).not.toHaveBeenCalled()
  })

  it('removeDocs 请求失败时返回 null 并记录原因', async () => {
    vi.spyOn(api, 'batchDeleteProjectDocs').mockRejectedValue(new Error('HTTP 500'))
    const store = useProjectStore()
    seedList(store, [doc()])

    expect(await store.removeDocs(['project-u9-a'])).toBeNull()
    expect(store.error).toContain('500')
    expect(store.docs).toHaveLength(1)
  })

  it('removeDocs 传递的正是勾选的 id 列表', async () => {
    const spy = vi
      .spyOn(api, 'batchDeleteProjectDocs')
      .mockResolvedValue({ deleted_ids: ['a', 'b'], not_found_ids: [] })
    const store = useProjectStore()
    seedList(store, [doc({ doc_id: 'a' }), doc({ doc_id: 'b' })])

    await store.removeDocs(['a', 'b'])
    expect(spy).toHaveBeenCalledWith(['a', 'b'])
  })

  it('preprocessDoc:成功时写入 preview 并标记结束', async () => {
    const preview: ProjectPreprocessOut = {
      preview_id: 'p1',
      project_name: '甲项目',
      file_name: 'a.md',
      original_text: '原始内容',
      normalized_text: '# 甲项目\n\n## 技术栈\nPython',
      normalized: true,
      reason: 'ok',
      has_resume_section: false,
    }
    vi.spyOn(api, 'preprocessProjectDoc').mockResolvedValue(preview)
    const store = useProjectStore()

    const r = await store.preprocessDoc(new File(['x'], 'a.md'))

    expect(r).toEqual(preview)
    expect(store.preview).toEqual(preview)
    expect(store.preprocessing).toBe(false)
  })

  it('preprocessDoc:失败时清空 preview 并记录原因', async () => {
    vi.spyOn(api, 'preprocessProjectDoc').mockRejectedValue(
      new Error('只支持 Markdown 文件(.md)'),
    )
    const store = useProjectStore()

    expect(await store.preprocessDoc(new File(['x'], 'a.txt'))).toBeNull()
    expect(store.preview).toBeNull()
    expect(store.preprocessing).toBe(false)
    expect(store.error).toContain('Markdown')
  })

  it('preprocessDoc:进行中置 preprocessing(供 UI 显示 loading)', async () => {
    let resolveFn: (v: ProjectPreprocessOut) => void = () => {}
    vi.spyOn(api, 'preprocessProjectDoc').mockImplementation(
      () => new Promise((res) => (resolveFn = res)),
    )
    const store = useProjectStore()
    const base: ProjectPreprocessOut = {
      preview_id: 'p', project_name: 'P', file_name: 'a.md',
      original_text: '', normalized_text: '', normalized: false,
      reason: 'ok', has_resume_section: false,
    }

    const pending = store.preprocessDoc(new File(['x'], 'a.md'))
    expect(store.preprocessing).toBe(true)
    resolveFn(base)
    await pending
    expect(store.preprocessing).toBe(false)
  })

  it('confirmDoc:无预览时直接返回 false(不发请求)', async () => {
    const spy = vi.spyOn(api, 'confirmProjectDoc')
    const store = useProjectStore()
    expect(await store.confirmDoc()).toBe(false)
    expect(spy).not.toHaveBeenCalled()
  })

  it('confirmDoc:成功后清空预览并刷新列表', async () => {
    vi.spyOn(api, 'listProjectDocs').mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
    const spy = vi
      .spyOn(api, 'confirmProjectDoc')
      .mockResolvedValue({ task_id: 't1', status: 'pending' })
    stubFetch(async () =>
      sseResponse(['event: done\ndata: {"task_id":"t1","status":"success"}\n\n']),
    )

    const store = useProjectStore()
    store.preview = {
      preview_id: 'p1', project_name: 'P', file_name: 'a.md',
      original_text: '', normalized_text: '', normalized: false,
      reason: 'ok', has_resume_section: false,
    }

    expect(await store.confirmDoc()).toBe(true)
    expect(spy).toHaveBeenCalledWith('p1', undefined)
    expect(store.preview).toBeNull()
  })

  it('confirmDoc:把用户编辑后的文本传给后端', async () => {
    vi.spyOn(api, 'listProjectDocs').mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
    const spy = vi
      .spyOn(api, 'confirmProjectDoc')
      .mockResolvedValue({ task_id: 't1', status: 'pending' })
    stubFetch(async () =>
      sseResponse(['event: done\ndata: {"task_id":"t1","status":"success"}\n\n']),
    )

    const store = useProjectStore()
    store.preview = {
      preview_id: 'p1', project_name: 'P', file_name: 'a.md',
      original_text: '', normalized_text: '', normalized: false,
      reason: 'ok', has_resume_section: false,
    }

    await store.confirmDoc('# P\n\n## 简历描述\n我改过的版本')
    expect(spy).toHaveBeenCalledWith('p1', '# P\n\n## 简历描述\n我改过的版本')
  })

  it('confirmDoc:失败时保留预览(用户可重试)', async () => {
    vi.spyOn(api, 'confirmProjectDoc').mockRejectedValue(new Error('preview not found'))
    const store = useProjectStore()
    store.preview = {
      preview_id: 'p1', project_name: 'P', file_name: 'a.md',
      original_text: '', normalized_text: '', normalized: false,
      reason: 'ok', has_resume_section: false,
    }

    expect(await store.confirmDoc()).toBe(false)
    expect(store.preview).not.toBeNull()
    expect(store.error).toContain('not found')
  })

  it('clearPreview:放弃预览', () => {
    const store = useProjectStore()
    store.preview = {
      preview_id: 'p1', project_name: 'P', file_name: 'a.md',
      original_text: '', normalized_text: '', normalized: false,
      reason: 'ok', has_resume_section: false,
    }
    store.clearPreview()
    expect(store.preview).toBeNull()
  })

  it('入库完成后描述为"入库完成"且上传区恢复可用', async () => {
    /**
     * 回归守卫：单文档入库很快，SSE 常只见首帧"排队中"+ done 帧——
     * 此前成功分支从不更新 message，进度条会卡在"排队中" 100%。
     */
    vi.spyOn(api, 'uploadProjectDoc').mockResolvedValue({ task_id: 't1', status: 'pending' })
    vi.spyOn(api, 'listProjectDocs').mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
    stubFetch(async () =>
      sseResponse(['event: done\ndata: {"task_id":"t1","status":"success"}\n\n']),
    )

    const store = useProjectStore()
    const ok = await store.uploadDoc(new File(['x'], 'a.md'))

    expect(ok).toBe(true)
    expect(store.upload?.status).toBe('success')
    expect(store.upload?.message).toBe('入库完成')
    expect(store.upload?.percent).toBe(100)
    // 上传区恢复可用：uploading 必须为 false
    expect(store.uploading).toBe(false)
  })

  it('入库失败时描述为"入库失败"且带原因', async () => {
    vi.spyOn(api, 'uploadProjectDoc').mockResolvedValue({ task_id: 't1', status: 'pending' })
    stubFetch(async () =>
      sseResponse(['event: done\ndata: {"task_id":"t1","status":"failed"}\n\n']),
    )

    const store = useProjectStore()
    expect(await store.uploadDoc(new File(['x'], 'a.md'))).toBe(false)

    expect(store.upload?.status).toBe('failed')
    expect(store.upload?.message).toBe('入库失败')
    expect(store.uploading).toBe(false)
  })

  it('SSE 中途断线时以快照结算且描述正确', async () => {
    vi.spyOn(api, 'uploadProjectDoc').mockResolvedValue({ task_id: 't1', status: 'pending' })
    vi.spyOn(api, 'listProjectDocs').mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
    // 空流：无任何帧，模拟连接被掐断
    stubFetch(async () => sseResponse([]))
    vi.spyOn(api, 'getProjectUploadStatus').mockResolvedValue({
      task_id: 't1', status: 'success', created_at: 1,
    })

    const store = useProjectStore()
    expect(await store.uploadDoc(new File(['x'], 'a.md'))).toBe(true)
    expect(store.upload?.message).toBe('入库完成')
  })

  it('uploading 仅在任务进行中为真', () => {
    const store = useProjectStore()
    expect(store.uploading).toBe(false)
    store.upload = {
      taskId: 't1',
      filename: 'a.md',
      percent: 40,
      message: '写入分块…',
      status: 'running',
    }
    expect(store.uploading).toBe(true)
    store.upload.status = 'success'
    expect(store.uploading).toBe(false)
  })

  it('clearUpload 清理上传态', () => {
    const store = useProjectStore()
    store.upload = {
      taskId: 't1',
      filename: 'a.md',
      percent: 100,
      message: 'done',
      status: 'success',
    }
    store.clearUpload()
    expect(store.upload).toBeNull()
  })
})
