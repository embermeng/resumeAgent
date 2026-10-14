/**
 * 触发浏览器下载文本附件。
 *
 * 用 Blob + a[download] 而不是跳 URL / 依赖后端 Content-Disposition：
 * 只有这样才能自定义中文文件名（HTTP header 里的中文 filename 需要
 * 额外编码且各浏览器行为不一致），也便于单测断言。
 */
export function downloadTextFile(
  fileName: string,
  content: string,
  mime = 'text/markdown;charset=utf-8',
): void {
  const url = URL.createObjectURL(new Blob([content], { type: mime }))
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
