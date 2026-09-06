import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus, { ElMessage } from 'element-plus'
import ResumeUploader from './ResumeUploader.vue'
import * as api from '@/api/client'

vi.mock('@/api/client')

function factory(props: Record<string, unknown> = {}) {
  return mount(ResumeUploader, { props, global: { plugins: [ElementPlus] } })
}

// 通过 defineExpose 暴露的方法在 vm 上可访问
type VM = {
  isSupported(name: string): boolean
  beforeUpload(file: File): boolean
  httpRequest(options: { file: File }): Promise<void>
}

describe('ResumeUploader', () => {
  beforeEach(() => {
    vi.spyOn(ElMessage, 'error').mockImplementation(() => undefined as never)
    vi.spyOn(ElMessage, 'success').mockImplementation(() => undefined as never)
  })
  afterEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
  })

  it('isSupported:大小写不敏感,白名单外为 false', () => {
    const vm = factory().vm as unknown as VM
    expect(vm.isSupported('resume.md')).toBe(true)
    expect(vm.isSupported('RESUME.PDF')).toBe(true)
    expect(vm.isSupported('virus.exe')).toBe(false)
  })

  it('beforeUpload:不支持的扩展名 → false 且 emit error', () => {
    const w = factory()
    const vm = w.vm as unknown as VM
    expect(vm.beforeUpload(new File(['x'], 'a.exe'))).toBe(false)
    expect(w.emitted('error')?.[0]?.[0]).toContain('不支持的文件格式')
  })

  it('beforeUpload:支持的扩展名 → true,不 emit error', () => {
    const w = factory()
    const vm = w.vm as unknown as VM
    expect(vm.beforeUpload(new File(['x'], 'a.md'))).toBe(true)
    expect(w.emitted('error')).toBeFalsy()
  })

  it('httpRequest:解析成功 → 调用 parseResume 并 emit parsed', async () => {
    vi.mocked(api.parseResume).mockResolvedValue({ filename: 'a.md', content: '# A' })
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File(['# A'], 'a.md') })
    expect(api.parseResume).toHaveBeenCalledTimes(1)
    expect(w.emitted('parsed')?.[0]).toEqual([{ filename: 'a.md', content: '# A' }])
  })

  it('httpRequest:解析失败 → emit error', async () => {
    vi.mocked(api.parseResume).mockRejectedValue(new Error('内容为空'))
    const w = factory()
    const vm = w.vm as unknown as VM
    await vm.httpRequest({ file: new File([''], 'a.md') })
    expect(w.emitted('error')?.[0]).toEqual(['内容为空'])
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
