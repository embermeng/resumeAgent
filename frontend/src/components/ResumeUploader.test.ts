import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus, { ElMessage, ElMessageBox } from 'element-plus'
import ResumeUploader from './ResumeUploader.vue'
import * as api from '@/api/client'
import { sseResponse, stubFetch } from '@/test/sse-helpers'

vi.mock('@/api/client')

function factory(props: Record<string, unknown> = {}) {
  return mount(ResumeUploader, { props, global: { plugins: [ElementPlus] } })
}

// 通过 defineExpose 暴露的方法在 vm 上可访问
type VM = {
  isSupported(name: string): boolean
  beforeUpload(file: File): Promise<boolean>
  httpRequest(options: { file: File }): Promise<void>
  statusMessage: string
}

describe('ResumeUploader', () => {
  beforeEach(() => {
    vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  it('isSupported:大小写不敏感,白名单外为 false', () => {
    const vm = factory().vm as unknown as VM
    expect(vm.isSupported('resume.md')).toBe(true)
    expect(vm.isSupported('RESUME.PDF')).toBe(true)
    expect(vm.isSupported('virus.exe')).toBe(false)
  })

  it('beforeUpload:不支持的扩展名 → false 且 emit error', async () => {
    const w = factory()
    const vm = w.vm as unknown as VM
    expect(await vm.beforeUpload(new File(['x'], 'a.exe'))).toBe(false)
    expect(w.emitted('error')?.[0]?.[0]).toContain('不支持的文件格式')
  })

  it('beforeUpload:支持的扩展名 → true,不 emit error', async () => {
    const w = factory()
    const vm = w.vm as unknown as VM
    expect(await vm.beforeUpload(new File(['x'], 'a.md'))).toBe(true)
    expect(w.emitted('error')).toBeFalsy()
  })

  it('beforeUpload:还没简历时不弹替换确认', async () => {
    const confirm = vi.spyOn(ElMessageBox, 'confirm')
    const vm = factory().vm as unknown as VM
    expect(await vm.beforeUpload(new File(['x'], 'a.md'))).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('beforeUpload:已有简历时先确认替换,用户同意才放行', async () => {
    const confirm = vi
      .spyOn(ElMessageBox, 'confirm')
      .mockResolvedValue('confirm' as unknown as Awaited<ReturnType<typeof ElMessageBox.confirm>>)
    const vm = factory({ hasResume: true }).vm as unknown as VM
    expect(await vm.beforeUpload(new File(['x'], 'a.md'))).toBe(true)
    expect(confirm).toHaveBeenCalled()
    // 说明真删的是旧简历,而不是上传即删(解析失败时旧的留着)
    expect(confirm.mock.calls[0][0]).toContain('解析成功后')
  })

  it('beforeUpload:已有简历时用户取消 → false,不提交任务', async () => {
    vi.spyOn(ElMessageBox, 'confirm').mockRejectedValue(new Error('cancel') as never)
    const vm = factory({ hasResume: true }).vm as unknown as VM
    expect(await vm.beforeUpload(new File(['x'], 'a.md'))).toBe(false)
    expect(api.parseResume).not.toHaveBeenCalled()
  })

  it('httpRequest:提交任务→SSE done(success)→取 content 并 emit parsed', async () => {
    vi.mocked(api.parseResume).mockResolvedValue({ task_id: 't1', status: 'pending' })
    vi.mocked(api.getResumeParseStatus).mockResolvedValue({
      task_id: 't1',
      status: 'success',
      filename: 'a.md',
      content: '# A',
      created_at: 100,
    })
    stubFetch(async () =>
      sseResponse([
        'event: progress\ndata: {"stage":"queued","message":"排队等待解析槽位","percent":5}\n\n',
        'event: progress\ndata: {"stage":"parsing","message":"MinerU 解析中","percent":50}\n\n',
        'event: done\ndata: {"task_id":"t1","status":"success","elapsed":2}\n\n',
      ]),
    )
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File(['# A'], 'a.md') })

    expect(api.parseResume).toHaveBeenCalledTimes(1)
    expect(api.getResumeParseStatus).toHaveBeenCalledWith('t1')
    expect(w.emitted('parsed')?.[0]).toEqual([{ filename: 'a.md', content: '# A' }])
    // 终态文案要盖掉最后一个 progress 帧,否则进度条 100% 而文字还停在"排队中"
    expect(vm.statusMessage).toBe('解析完成')
  })

  it('httpRequest:SSE done(failed)→ emit error(取 error 帧文案),不 emit parsed', async () => {
    vi.mocked(api.parseResume).mockResolvedValue({ task_id: 't2', status: 'pending' })
    stubFetch(async () =>
      sseResponse([
        'event: error\ndata: {"message":"简历解析结果为空"}\n\n',
        'event: done\ndata: {"task_id":"t2","status":"failed","elapsed":1}\n\n',
      ]),
    )
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File([''], 'a.md') })

    expect(w.emitted('error')?.[0]).toEqual(['简历解析结果为空'])
    expect(w.emitted('parsed')).toBeFalsy()
    // 失败时提示区要换成原因,不能继续显示上一步的进度文案
    expect(vm.statusMessage).toBe('简历解析结果为空')
  })

  it('httpRequest:只送来首帧"排队中"就 done → 文案落定为解析完成', async () => {
    // 回归:本地 docx 解析太快,worker 往往只来得及发建行时的"排队中"就结束,
    // 成功分支不覆盖文案就会出现进度条 100% 而文字还写着"排队中"
    vi.mocked(api.parseResume).mockResolvedValue({ task_id: 't4', status: 'pending' })
    vi.mocked(api.getResumeParseStatus).mockResolvedValue({
      task_id: 't4',
      status: 'success',
      filename: '全栈开发工程师.docx',
      content: '# A',
      created_at: 100,
    })
    stubFetch(async () =>
      sseResponse([
        'event: progress\ndata: {"stage":"queued","message":"排队中","percent":0}\n\n',
        'event: done\ndata: {"task_id":"t4","status":"success","elapsed":1}\n\n',
      ]),
    )
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File(['x'], '全栈开发工程师.docx') })

    expect(vm.statusMessage).toBe('解析完成')
    expect(w.find('[data-test="progress-text"]').text()).toBe('解析完成')
  })

  it('httpRequest:SSE 无终态(空流)→ 轮询兜底取 content', async () => {
    vi.mocked(api.parseResume).mockResolvedValue({ task_id: 't3', status: 'pending' })
    vi.mocked(api.getResumeParseStatus).mockResolvedValue({
      task_id: 't3',
      status: 'success',
      filename: 'b.pdf',
      content: '# B',
      created_at: 100,
    })
    stubFetch(async () => sseResponse([])) // 流立即结束,无 done 帧
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File(['x'], 'b.pdf') })

    expect(api.getResumeParseStatus).toHaveBeenCalledWith('t3')
    expect(w.emitted('parsed')?.[0]).toEqual([{ filename: 'b.pdf', content: '# B' }])
  })

  it('httpRequest:parseResume 直接失败(如 400 不支持格式)→ emit error', async () => {
    vi.mocked(api.parseResume).mockRejectedValue(new Error('不支持的简历文件格式: .exe'))
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File(['x'], 'a.exe') })

    expect(w.emitted('error')?.[0]).toEqual(['不支持的简历文件格式: .exe'])
    expect(w.emitted('parsed')).toBeFalsy()
  })

  it('自定义 extensions prop 生效', () => {
    const vm = factory({ extensions: ['.pdf'] }).vm as unknown as VM
    expect(vm.isSupported('a.pdf')).toBe(true)
    expect(vm.isSupported('a.md')).toBe(false)
  })

  it('渲染拖拽上传区与提示', () => {
    const w = factory()
    expect(w.find('[data-test="uploader"]').exists()).toBe(true)
    expect(w.text()).toContain('点击上传')
    expect(w.text()).toContain('.md')
  })
})
