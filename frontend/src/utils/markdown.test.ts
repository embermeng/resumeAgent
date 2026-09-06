import { describe, it, expect } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown', () => {
  it('渲染标题、粗体与段落', () => {
    const html = renderMarkdown('# 标题\n\n**加粗**文字')
    expect(html).toContain('<h1>标题</h1>')
    expect(html).toContain('<strong>加粗</strong>')
  })

  it('渲染有序/无序列表', () => {
    const html = renderMarkdown('- 项目一\n- 项目二')
    expect(html).toContain('<ul>')
    expect(html).toContain('<li>项目一</li>')
  })

  it('代码块带 highlight.js 的 hljs class', () => {
    const html = renderMarkdown('```js\nconst a = 1\n```')
    expect(html).toContain('hljs')
    expect(html).toContain('<code>')
  })

  it('中文与换行正常输出', () => {
    const html = renderMarkdown('第一行\n第二行')
    expect(html).toContain('第一行')
    expect(html).toContain('第二行')
  })

  it('XSS 防护:原始 <script> 不会成为可执行标签', () => {
    const html = renderMarkdown('你好<script>alert(1)</script>')
    expect(html).not.toMatch(/<script/i)
  })

  it('XSS 防护:原始 <img onerror> 被转义为文本,不成为真实元素', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)>')
    expect(html).not.toMatch(/<img/i) // 无真实 img 标签
    expect(html).toContain('&lt;img') // 已转义为可见文本(不可执行)
  })

  it('空/无效输入返回空串且不抛错', () => {
    expect(renderMarkdown('')).toBe('')
    expect(renderMarkdown(null)).toBe('')
    expect(renderMarkdown(undefined)).toBe('')
  })
})
