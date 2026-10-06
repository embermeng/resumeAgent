import { describe, it, expect, beforeEach } from 'vitest'
import { splitBlocks, renderBlocks, clearBlockCache } from './blocks'

beforeEach(() => {
  clearBlockCache()
})

describe('splitBlocks', () => {
  it('空/无效输入返回空数组', () => {
    expect(splitBlocks('')).toEqual([])
    expect(splitBlocks('   \n\n  ')).toEqual([])
  })

  it('按顶层空行切块', () => {
    expect(splitBlocks('# 标题\n\n正文一\n\n正文二')).toEqual(['# 标题', '正文一', '正文二'])
  })

  it('块内单换行(breaks)不切,仅空行切', () => {
    expect(splitBlocks('第一行\n第二行')).toEqual(['第一行\n第二行'])
  })

  it('``` 围栏内部的空行不切块', () => {
    const src = '前文\n\n```js\nconst a = 1\n\nconst b = 2\n```\n\n后文'
    const blocks = splitBlocks(src)
    expect(blocks).toHaveLength(3)
    expect(blocks[1]).toContain('```js')
    expect(blocks[1]).toContain('const b = 2')
    expect(blocks[1]).toContain('```')
  })

  it('~~~ 围栏同样受保护', () => {
    const blocks = splitBlocks('~~~\na\n\nb\n~~~')
    expect(blocks).toHaveLength(1)
    expect(blocks[0]).toContain('a')
    expect(blocks[0]).toContain('b')
  })

  it('未闭合围栏:后续空行不再切块(视为仍在围栏内)', () => {
    const blocks = splitBlocks('```js\ncode\n\nmore')
    expect(blocks).toHaveLength(1)
  })
})

describe('renderBlocks', () => {
  it('每块产出 key 与消毒后的 html', () => {
    const out = renderBlocks('**加粗**\n\n普通')
    expect(out).toHaveLength(2)
    expect(out[0].html).toContain('<strong>加粗</strong>')
    expect(out[1].html).toContain('普通')
    expect(out.every((b) => typeof b.key === 'string' && b.key.length > 0)).toBe(true)
  })

  it('相同内容的块 key 稳定(缓存命中,保证已完结块 Vue diff 跳过)', () => {
    const a = renderBlocks('# 标题\n\n尾块')
    const b = renderBlocks('# 标题\n\n尾块变化了')
    // 首块内容未变 → key 与 html 均一致
    expect(a[0].key).toBe(b[0].key)
    expect(a[0].html).toBe(b[0].html)
    // 尾块变化 → key 变化(触发重渲)
    expect(a[1].key).not.toBe(b[1].key)
  })

  it('空输入返回空数组', () => {
    expect(renderBlocks('')).toEqual([])
  })

  it('XSS 防护仍生效(经 renderMarkdown)', () => {
    const out = renderBlocks('<script>alert(1)</script>')
    expect(out[0].html).not.toMatch(/<script/i)
  })
})
