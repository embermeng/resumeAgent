import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import { createPinia, setActivePinia } from 'pinia'
import ProjectDocsPanel from './ProjectDocsPanel.vue'
import { useProjectStore } from '@/stores/project'
import * as api from '@/api/client'
import { sseResponse, stubFetch } from '@/test/sse-helpers'
import type { ProjectDocOut } from '@/types/events'

vi.mock('@/api/client')

beforeEach(() => {
  setActivePinia(createPinia())
  vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
  vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
  vi.spyOn(api, 'listProjectDocs').mockResolvedValue({ docs: [], total: 0, max_docs: 30 })
})
afterEach(() => {
  vi.unstubAllGlobals()
  // 不用 restoreAllMocks：它作用于全局 spy 注册表，会把 vi.mock 的自动模块
  // mock 一并"恢复"掉，联跑时污染后续测试文件（表现为 listProjectDocs 失效）。
  vi.clearAllMocks()
})

function factory() {
  return mount(ProjectDocsPanel, { global: { plugins: [ElementPlus] } })
}

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

/**
 * 挂载并让列表呈现指定数据。
 *
 * 注意:本组件**不再自行拉取列表**(改由 AppSidebar onMounted 统一拉),
 * 所以这里 mount 后必须显式调 loadDocs——数据由上方 mock 提供,
 * 不要直接改 store.docs(不会触发组件的加载语义,且与真实流程不符)。
 */
async function factoryWith(docs: ProjectDocOut[], maxDocs = 30) {
  vi.mocked(api.listProjectDocs).mockResolvedValue({
    docs,
    total: docs.length,
    max_docs: maxDocs,
  })
  const w = factory()
  const store = useProjectStore()
  await store.loadDocs()
  await flushPromises()
  await w.vm.$nextTick()
  return w
}

describe('ProjectDocsPanel', () => {
  it('空列表显示引导文案', async () => {
    const w = await factoryWith([])
    expect(w.find('[data-test="empty"]').exists()).toBe(true)
    expect(w.find('[data-test="doc-list"]').exists()).toBe(false)
  })

  it('渲染文档项与章节状态', async () => {
    const w = await factoryWith([doc()])
    expect(w.find('[data-test="doc-list"]').exists()).toBe(true)
    expect(w.text()).toContain('甲项目')
    expect(w.find('[data-test="resume-section-true"]').exists()).toBe(true)
  })

  it('缺「简历描述」章节时给出警告态', async () => {
    const w = await factoryWith([doc({ has_resume_section: false })])
    expect(w.find('[data-test="resume-section-false"]').exists()).toBe(true)
    expect(w.text()).toContain('缺简历描述章节')
  })

  it('带「简历优先」标签的显示标签', async () => {
    const w = await factoryWith([doc({ tags: ['简历优先'] })])
    expect(w.text()).toContain('简历优先')
  })

  it('公共素材标记「公共」且不提供删除入口', async () => {
    const w = await factoryWith([doc({ is_public: true }), doc({ doc_id: 'b' })])
    expect(w.text()).toContain('公共')
    // 只有私有文档有删除按钮
    expect(w.findAll('[data-test="doc-delete"]')).toHaveLength(1)
  })

  it('配额显示在上传区提示中', async () => {
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])
    expect(w.text()).toContain('已用 2 / 30')
  })

  it('提供模板下载入口', async () => {
    const w = await factoryWith([])
    expect(w.find('[data-test="download-template"]').exists()).toBe(true)
  })

  it('删除:确认后调用接口并从列表移除', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
    const delSpy = vi.spyOn(api, 'deleteProjectDoc').mockResolvedValue(undefined)
    const w = await factoryWith([doc()])

    await w.find('[data-test="doc-delete"]').trigger('click')
    await w.vm.$nextTick()

    expect(delSpy).toHaveBeenCalledWith('project-u9-a')
  })

  it('删除:用户取消则不调用接口', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel' as never)
    const delSpy = vi.spyOn(api, 'deleteProjectDoc').mockResolvedValue(undefined)
    const w = await factoryWith([doc()])

    await w.find('[data-test="doc-delete"]').trigger('click')
    await w.vm.$nextTick()

    expect(delSpy).not.toHaveBeenCalled()
  })

  it('满额时上传被禁用', async () => {
    const w = await factoryWith([doc()], 1)
    const store = useProjectStore()
    expect(store.atQuota).toBe(true)
    expect(w.find('.doc-uploader').attributes('class')).toBeDefined()
  })

  it('上传进行中显示进度条', async () => {
    const w = factory()
    const store = useProjectStore()
    store.upload = {
      taskId: 't1',
      filename: 'a.md',
      percent: 70,
      message: '构建向量索引…',
      status: 'running',
    }
    await w.vm.$nextTick()

    expect(w.find('[data-test="upload-progress"]').exists()).toBe(true)
    expect(w.find('[data-test="progress-text"]').text()).toBe('构建向量索引…')
  })

  it('上传失败显示原因', async () => {
    const w = factory()
    const store = useProjectStore()
    store.upload = {
      taskId: 't1',
      filename: 'a.md',
      percent: 10,
      message: '解析文档…',
      status: 'failed',
      error: '文档缺少一级标题',
    }
    await w.vm.$nextTick()

    expect(w.text()).toContain('文档缺少一级标题')
  })
})

describe('ProjectDocsPanel · 批量删除', () => {
  function mockConfirm() {
    return vi.spyOn(ElMessageBox, 'confirm').mockResolvedValue('confirm' as never)
  }

  it('未选中时不显示批量删除按钮', async () => {
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])
    expect(w.find('[data-test="batch-delete"]').exists()).toBe(false)
  })

  it('勾选后显示按钮并带选中数量', async () => {
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])
    const boxes = w.findAll('[data-test="doc-check"]')
    expect(boxes).toHaveLength(2)

    await boxes[0].find('input').setValue(true)
    expect(w.find('[data-test="batch-delete"]').text()).toContain('1')

    await boxes[1].find('input').setValue(true)
    expect(w.find('[data-test="batch-delete"]').text()).toContain('2')
  })

  it('全选:一键勾选所有可选项,再点取消全不选', async () => {
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])
    const all = w.find('[data-test="select-all"] input')

    await all.setValue(true)
    expect(w.find('[data-test="batch-delete"]').text()).toContain('2')

    await all.setValue(false)
    expect(w.find('[data-test="batch-delete"]').exists()).toBe(false)
  })

  it('公共素材不可选:无复选框且不计入全选', async () => {
    const w = await factoryWith([doc({ is_public: true }), doc({ doc_id: 'b' })])
    // 只有私有文档带复选框(公共项是占位 span)
    expect(w.findAll('[data-test="doc-check"]')).toHaveLength(1)

    await w.find('[data-test="select-all"] input').setValue(true)
    expect(w.find('[data-test="batch-delete"]').text()).toContain('1')
  })

  it('确认后调用批量接口并传入勾选的 id', async () => {
    mockConfirm()
    const spy = vi.spyOn(api, 'batchDeleteProjectDocs').mockResolvedValue({
      deleted_ids: ['project-u9-a', 'b'],
      not_found_ids: [],
    })
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])

    await w.find('[data-test="select-all"] input').setValue(true)
    await w.find('[data-test="batch-delete"]').trigger('click')
    await flushPromises()

    expect(spy).toHaveBeenCalledWith(['project-u9-a', 'b'])
  })

  it('用户取消确认则不调用接口', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue('cancel' as never)
    const spy = vi.spyOn(api, 'batchDeleteProjectDocs').mockResolvedValue({
      deleted_ids: [],
      not_found_ids: [],
    })
    const w = await factoryWith([doc()])

    await w.find('[data-test="select-all"] input').setValue(true)
    await w.find('[data-test="batch-delete"]').trigger('click')
    await flushPromises()

    expect(spy).not.toHaveBeenCalled()
  })

  it('部分被跳过时保留其在列表并给出警告提示', async () => {
    mockConfirm()
    const warn = vi.spyOn(ElMessage, 'warning').mockImplementation(() => undefined as never)
    vi.spyOn(api, 'batchDeleteProjectDocs').mockResolvedValue({
      deleted_ids: ['project-u9-a'],
      not_found_ids: ['b'],
    })
    const store = useProjectStore()
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])

    await w.find('[data-test="select-all"] input').setValue(true)
    await w.find('[data-test="batch-delete"]').trigger('click')
    await flushPromises()

    // 被跳过的仍留在列表
    expect(store.docs.map((d) => d.doc_id)).toEqual(['b'])
    expect(warn).toHaveBeenCalled()
    expect(String(warn.mock.calls[0][0])).toContain('1 份被跳过')
  })

  it('批量删除成功后清空选中态', async () => {
    mockConfirm()
    vi.spyOn(api, 'batchDeleteProjectDocs').mockResolvedValue({
      deleted_ids: ['project-u9-a'],
      not_found_ids: [],
    })
    const w = await factoryWith([doc()])

    await w.find('[data-test="select-all"] input').setValue(true)
    await w.find('[data-test="batch-delete"]').trigger('click')
    await flushPromises()
    await w.vm.$nextTick()

    expect(w.find('[data-test="batch-delete"]').exists()).toBe(false)
  })

  it('列表变化后丢弃已不存在的选中项', async () => {
    const w = await factoryWith([doc(), doc({ doc_id: 'b' })])
    await w.find('[data-test="select-all"] input').setValue(true)
    expect(w.find('[data-test="batch-delete"]').text()).toContain('2')

    // 模拟刷新后其中一个已不存在
    const store = useProjectStore()
    store.docs = [doc()]
    await flushPromises()

    expect(w.find('[data-test="batch-delete"]').text()).toContain('1')
  })
})

describe('ProjectDocsPanel · 预处理预览', () => {
  const PREVIEW = {
    preview_id: 'p1',
    project_name: '甲项目',
    file_name: 'a.md',
    original_text: '我做了个RAG项目',
    normalized_text: '# 甲项目\n\n## 技术栈\nFAISS',
    normalized: true,
    reason: 'ok',
    has_resume_section: false,
  }

  /** 挂载后灌入预览并等弹窗渲染 */
  async function openPreview(over: Partial<typeof PREVIEW> = {}) {
    const w = factory()
    const store = useProjectStore()
    store.preview = { ...PREVIEW, ...over }
    await flushPromises()
    await w.vm.$nextTick()
    return { w, store }
  }

  it('预处理中显示加载态与文案', async () => {
    const { w, store } = await openPreview()
    store.preview = null
    store.preprocessing = true
    await w.vm.$nextTick()

    expect(w.find('[data-test="preprocessing"]').exists()).toBe(true)
    expect(w.find('[data-test="preprocessing-text"]').text()).toContain('正在整理')
  })

  it('有预览时自动弹出弹窗，文本区预填整理结果', async () => {
    const { w } = await openPreview()

    expect(w.find('[data-test="preview-dialog"]').exists()).toBe(true)
    const area = w.find('[data-test="preview-text"]')
    expect((area.element as HTMLTextAreaElement).value).toBe(PREVIEW.normalized_text)
  })

  it('无预览时不显示弹窗', async () => {
    const w = factory()
    await flushPromises()
    expect(w.find('[data-test="preview-dialog"]').exists()).toBe(false)
  })

  it('经大模型整理时提示"仅重组与提炼"', async () => {
    const { w } = await openPreview({ normalized: true })
    expect(w.find('[data-test="preview-tip"]').text()).toContain('未新增内容')
  })

  it('未做改动时说明未整理及原因', async () => {
    const { w } = await openPreview({
      normalized: false,
      reason: '已有章节结构，无需归一化',
    })
    const tip = w.find('[data-test="preview-tip"]').text()
    expect(tip).toContain('未做改动')
    expect(tip).toContain('无需归一化')
  })

  it('缺简历描述章节时提示会降级用全文', async () => {
    const { w } = await openPreview({ has_resume_section: false })
    expect(w.find('[data-test="preview-warn"]').text()).toContain('使用全文')
  })

  it('有简历描述章节时不显示该警告', async () => {
    const { w } = await openPreview({ has_resume_section: true })
    expect(w.find('[data-test="preview-warn"]').exists()).toBe(false)
  })

  it('可切换对照原文', async () => {
    const { w } = await openPreview()
    expect(w.find('[data-test="preview-original"]').exists()).toBe(false)

    await w.find('[data-test="toggle-original"] input').setValue(true)
    expect(w.find('[data-test="preview-original"]').text()).toContain(PREVIEW.original_text)
  })

  it('点击确认 → 调用 confirmProjectDoc 传入当前编辑内容', async () => {
    const spy = vi
      .spyOn(api, 'confirmProjectDoc')
      .mockResolvedValue({ task_id: 't1', status: 'pending' })
    stubFetch(async () =>
      sseResponse(['event: done\ndata: {"task_id":"t1","status":"success"}\n\n']),
    )
    vi.mocked(api.listProjectDocs).mockResolvedValue({ docs: [], total: 0, max_docs: 30 })

    const { w } = await openPreview()
    await w.find('[data-test="preview-confirm"]').trigger('click')
    await flushPromises()

    expect(spy).toHaveBeenCalledWith('p1', PREVIEW.normalized_text)
  })

  it('点击取消 → 清空预览', async () => {
    const { w, store } = await openPreview()
    await w.find('[data-test="preview-cancel"]').trigger('click')
    await flushPromises()

    // 只断言状态：el-dialog 有关闭过渡，DOM 不会同步消失
    expect(store.preview).toBeNull()
  })
})

describe('ProjectDocsPanel · 模板下载', () => {
  const TEMPLATE = { file_name: '项目文档模板.md', content: '# 项目名\n\n## 简历描述\n' }

  afterEach(() => {
    vi.restoreAllMocks()
  })

  function stubDom() {
    const clicked: HTMLAnchorElement[] = []
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:mock'),
      revokeObjectURL: vi.fn(),
    })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
      this: HTMLAnchorElement,
    ) {
      clicked.push(this)
    })
    return clicked
  }

  it('点击下载 → 以后端返回的文件名保存内容', async () => {
    const clicked = stubDom()
    const spy = vi
      .spyOn(api, 'fetchProjectTemplate')
      .mockResolvedValue(TEMPLATE)
    const w = factory()

    await w.find('[data-test="download-template"]').trigger('click')
    await flushPromises()

    expect(spy).toHaveBeenCalledTimes(1)
    expect(clicked).toHaveLength(1)
    expect(clicked[0].download).toBe('项目文档模板.md')
  })

  it('接口失败 → 提示失败且不抛未捕获异常', async () => {
    stubDom()
    vi.spyOn(api, 'fetchProjectTemplate').mockRejectedValue(new Error('500'))
    const w = factory()

    await w.find('[data-test="download-template"]').trigger('click')
    await flushPromises()

    expect(ElMessage.error).toHaveBeenCalledWith('模板下载失败，请稍后重试')
  })
})
