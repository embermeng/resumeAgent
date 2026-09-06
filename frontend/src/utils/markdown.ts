import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'

/**
 * Markdown 渲染(XSS 防护双层):
 * 1) markdown-it 关闭 html(原始 HTML 一律转义为文本),并对代码块做 highlight.js 高亮
 * 2) DOMPurify 兜底清洗最终 HTML,移除任何可执行/事件属性
 */
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  highlight(str: string, lang: string): string {
    if (lang && hljs.getLanguage(lang)) {
      try {
        const code = hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
        return `<pre class="hljs"><code>${code}</code></pre>`
      } catch {
        /* 高亮失败则退回纯转义 */
      }
    }
    return `<pre class="hljs"><code>${md.utils.escapeHtml(str)}</code></pre>`
  },
})

/** 把 Markdown 渲染为经过消毒、可直接 v-html 的 HTML 字符串 */
export function renderMarkdown(src: string | null | undefined): string {
  if (!src) return ''
  const raw = md.render(src)
  return DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
}
