import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia, type Pinia } from 'pinia'
import ElementPlus, { ElProgress } from 'element-plus'
import KnowledgeAdmin from './KnowledgeAdmin.vue'
import { useKnowledgeStore } from '@/stores/knowledge'

let pinia: Pinia

beforeEach(() => {
  pinia = createPinia()
  setActivePinia(pinia)
})
afterEach(() => {
  vi.restoreAllMocks()
})

function factory() {
  return mount(KnowledgeAdmin, { global: { plugins: [pinia, ElementPlus] } })
}

describe('KnowledgeAdmin', () => {
  it('渲染全部构建按钮', () => {
    const w = factory()
    expect(w.find('[data-test="build-build-all"]').exists()).toBe(true)
    expect(w.find('[data-test="build-parse-pdfs"]').exists()).toBe(true)
    expect(w.find('[data-test="build-build-indexes"]').exists()).toBe(true)
  })

  it('无任务时显示空状态', () => {
    expect(factory().text()).toContain('尚无构建任务')
  })

  it('点击一键构建 → 调用 store.startBuild({task:"build-all"})', async () => {
    const store = useKnowledgeStore()
    const spy = vi.spyOn(store, 'startBuild').mockResolvedValue()
    const w = factory()
    await w.find('[data-test="build-build-all"]').trigger('click')
    expect(spy).toHaveBeenCalledWith({ task: 'build-all' })
  })

  it('有任务时 el-progress 反映 percent', () => {
    const store = useKnowledgeStore()
    store.tasks.push({ taskId: 't1', kind: 'build-all', status: 'running', percent: 55, message: '[3/5] 文本分块...', logs: [] })
    const w = factory()
    const progress = w.findComponent(ElProgress)
    expect(progress.exists()).toBe(true)
    expect(progress.props('percentage')).toBe(55)
    expect(w.text()).toContain('[3/5] 文本分块...')
  })

  it('任务成功:进度 100 且状态标签为成功', () => {
    const store = useKnowledgeStore()
    store.tasks.push({ taskId: 't1', kind: 'build-all', status: 'success', percent: 100, logs: [] })
    const w = factory()
    expect(w.findComponent(ElProgress).props('percentage')).toBe(100)
    expect(w.text()).toContain('成功')
  })

  it('渲染日志行', () => {
    const store = useKnowledgeStore()
    store.tasks.push({ taskId: 't1', kind: 'build-all', status: 'running', percent: 20, logs: ['解析 a.pdf', '解析 b.pdf'] })
    const w = factory()
    expect(w.findAll('.log-line')).toHaveLength(2)
  })

  it('building 时按钮禁用', () => {
    const store = useKnowledgeStore()
    store.building = true
    store.tasks.push({ taskId: 't1', kind: 'build-all', status: 'running', percent: 10, logs: [] })
    const w = factory()
    expect(w.find('[data-test="build-parse-pdfs"]').attributes('disabled')).toBeDefined()
  })
})
