import { describe, it, expect, vi, afterEach } from 'vitest'
import { downloadTextFile } from './download'

/**
 * jsdom 不实现 URL.createObjectURL，也不真正下载，这里全部打桩后
 * 通过被点击的 <a> 元素断言下载行为。
 */
let clicked: HTMLAnchorElement | null = null

function stubDownload() {
  clicked = null
  const createObjectURL = vi.fn((_blob: Blob) => 'blob:mock-url')
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', {
    ...URL,
    createObjectURL,
    revokeObjectURL,
  })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
    this: HTMLAnchorElement,
  ) {
    clicked = this
  })
  return { createObjectURL, revokeObjectURL }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('downloadTextFile', () => {
  it('用后端给的文件名触发下载', () => {
    stubDownload()
    downloadTextFile('项目文档模板.md', '# 项目名')

    expect(clicked).not.toBeNull()
    expect(clicked!.download).toBe('项目文档模板.md')
    expect(clicked!.href).toBe('blob:mock-url')
  })

  it('以 UTF-8 Markdown 类型打包内容', () => {
    const { createObjectURL } = stubDownload()
    downloadTextFile('a.md', '# 中文标题')

    const blob = createObjectURL.mock.calls[0][0] as unknown as Blob
    expect(blob.type).toBe('text/markdown;charset=utf-8')
  })

  it('下载后释放 objectURL，避免内存泄漏', () => {
    const { revokeObjectURL } = stubDownload()
    downloadTextFile('a.md', 'x')

    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
  })

  it('不把临时链接留在 DOM 里', () => {
    stubDownload()
    downloadTextFile('a.md', 'x')

    expect(document.querySelectorAll('a').length).toBe(0)
  })
})
