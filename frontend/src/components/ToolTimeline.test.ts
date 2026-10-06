import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ElementPlus from 'element-plus'
import ToolTimeline from './ToolTimeline.vue'
import type { TimelineStage } from '@/stores/chat'

function factory(stages: TimelineStage[], streaming = false) {
  return mount(ToolTimeline, {
    props: { stages, streaming },
    global: { plugins: [ElementPlus] },
  })
}

const doneStages: TimelineStage[] = [
  { text: '正在识别意图...', at: 0, elapsed: 120 },
  { text: '正在检索知识库...', at: 120, elapsed: 800 },
]

describe('ToolTimeline', () => {
  it('无阶段时不渲染', () => {
    expect(factory([]).find('[data-test="timeline"]').exists()).toBe(false)
  })

  it('有阶段时渲染时间线与全部条目', () => {
    const w = factory(doneStages)
    expect(w.find('[data-test="timeline"]').exists()).toBe(true)
    expect(w.text()).toContain('正在识别意图...')
    expect(w.text()).toContain('正在检索知识库...')
  })

  it('非流式默认收起(列表不可见),摘要显示阶段数', () => {
    const w = factory(doneStages, false)
    expect(w.find('[data-test="timeline-toggle"]').attributes('aria-expanded')).toBe('false')
    expect(w.text()).toContain('2 个阶段')
  })

  it('流式默认展开,列表可见并显示进行中阶段', () => {
    const streaming: TimelineStage[] = [
      { text: '正在识别意图...', at: 0, elapsed: 120 },
      { text: '正在生成回答...', at: 120, elapsed: null },
    ]
    const w = factory(streaming, true)
    expect(w.find('[data-test="timeline-toggle"]').attributes('aria-expanded')).toBe('true')
    expect(w.text()).toContain('进行中')
  })

  it('点击 toggle 切换展开/收起', async () => {
    const w = factory(doneStages, false)
    const toggle = w.find('[data-test="timeline-toggle"]')
    expect(toggle.attributes('aria-expanded')).toBe('false')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('true')
    await toggle.trigger('click')
    expect(toggle.attributes('aria-expanded')).toBe('false')
  })

  it('已完结阶段显示耗时(ms 与 s 两种量级)', () => {
    const w = factory(
      [
        { text: '快阶段', at: 0, elapsed: 300 },
        { text: '慢阶段', at: 0, elapsed: 2500 },
      ],
      false,
    )
    const elapsed = w.findAll('[data-test="tl-elapsed"]').map((e) => e.text())
    expect(elapsed).toContain('300ms')
    expect(elapsed).toContain('2.5s')
  })
})
