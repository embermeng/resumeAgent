import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import CitationCards from './CitationCards.vue'

function factory(props: { knowledge?: string; projects?: string }) {
  return mount(CitationCards, { props })
}

const KNOWLEDGE =
  '[1] 来源: rag.pdf\nRAG 是检索增强生成\n\n---\n\n[2] 来源: agent.md\nLangGraph 负责编排'

describe('CitationCards', () => {
  it('无引用数据时不渲染', () => {
    expect(factory({}).find('[data-test="citations"]').exists()).toBe(false)
    expect(factory({ knowledge: '未检索到相关知识。' }).find('[data-test="citations"]').exists()).toBe(false)
  })

  it('解析 knowledge 渲染对应数量的引用卡片', () => {
    const w = factory({ knowledge: KNOWLEDGE })
    expect(w.find('[data-test="citations"]').exists()).toBe(true)
    expect(w.find('[data-test="citation-1"]').exists()).toBe(true)
    expect(w.find('[data-test="citation-2"]').exists()).toBe(true)
    // 来源去扩展名展示
    expect(w.text()).toContain('rag')
    expect(w.text()).toContain('agent')
  })

  it('默认折叠显示摘要,点击卡片头展开全文', async () => {
    const w = factory({ knowledge: KNOWLEDGE })
    // 折叠态:有摘要容器,无全文容器
    expect(w.find('[data-test="citation-1"] .cit-snippet').exists()).toBe(true)
    expect(w.find('[data-test="citation-1"] .cit-full').exists()).toBe(false)
    await w.find('[data-test="citation-1"] .cit-head').trigger('click')
    // 展开态:全文容器出现且含完整正文
    expect(w.find('[data-test="citation-1"] .cit-full').text()).toContain('RAG 是检索增强生成')
  })

  it('knowledge 与 projects 分组各自渲染', () => {
    const w = factory({
      knowledge: '[1] 来源: k.pdf\n知识点',
      projects: '[1] 来源: p.md\n项目素材',
    })
    expect(w.text()).toContain('知识引用')
    expect(w.text()).toContain('项目素材')
  })
})
