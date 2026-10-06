import { describe, it, expect } from 'vitest'
import { parseKnowledge } from './knowledge'

describe('parseKnowledge', () => {
  it('空/无效输入返回空数组', () => {
    expect(parseKnowledge('')).toEqual([])
    expect(parseKnowledge(null)).toEqual([])
    expect(parseKnowledge(undefined)).toEqual([])
    expect(parseKnowledge('   ')).toEqual([])
  })

  it('后端"未检索到"占位文本返回空数组', () => {
    expect(parseKnowledge('未检索到相关知识。')).toEqual([])
  })

  it('解析标准两条(编号/来源/正文,--- 分隔)', () => {
    const raw = '[1] 来源: rag.pdf\nRAG 是检索增强\n\n---\n\n[2] 来源: agent.md\nLangGraph 编排'
    const refs = parseKnowledge(raw)
    expect(refs).toHaveLength(2)
    expect(refs[0]).toEqual({ index: 1, source: 'rag.pdf', text: 'RAG 是检索增强' })
    expect(refs[1]).toEqual({ index: 2, source: 'agent.md', text: 'LangGraph 编排' })
  })

  it('正文含多行时整体保留', () => {
    const refs = parseKnowledge('[1] 来源: a.pdf\n第一行\n第二行')
    expect(refs[0].text).toBe('第一行\n第二行')
  })

  it('头行不符合契约格式时按"未知来源"兜底,编号按顺序补', () => {
    const refs = parseKnowledge('这是一段没有头行的自由文本')
    expect(refs).toHaveLength(1)
    expect(refs[0]).toMatchObject({ index: 1, source: '未知来源' })
    expect(refs[0].text).toContain('自由文本')
  })

  it('来源为空时回退"未知来源"', () => {
    const refs = parseKnowledge('[1] 来源:\n正文')
    expect(refs[0].source).toBe('未知来源')
  })

  it('只有头行没有正文时 text 为空串', () => {
    const refs = parseKnowledge('[3] 来源: x.pdf')
    expect(refs[0]).toEqual({ index: 3, source: 'x.pdf', text: '' })
  })
})
