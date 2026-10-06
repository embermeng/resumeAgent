import { renderMarkdown } from './markdown'

/**
 * 稳定块分段渲染(并行线第 1 项:解决长输出掉帧)。
 *
 * 背景:流式 token 逐字追加后若对全文重跑 markdown-it + hljs + DOMPurify,
 * 渲染成本随内容长度线性增长,长输出必然掉帧。
 *
 * 方案:把 Markdown 按顶层空行切成块(围栏代码块内部的空行不切),
 * 除最后一块(尾块,仍在增长)外其余块视为已完结;
 * ChatMessage 用 keyed v-for 渲染,已完结块 key 与 HTML 均不再变化,
 * Vue 原地复用 DOM,每帧只重渲尾块 → 成本恒定。
 */

export interface Block {
  /** 稳定 key:块序号_内容长度_内容 hash 摘要,尾块增长时 key 变化触发重渲 */
  key: string
  /** 渲染并消毒后的 HTML,可直接 v-html */
  html: string
}

/** 按顶层空行切块;``` 围栏内部(含 ~~~ 与缩进围栏)不切 */
export function splitBlocks(src: string): string[] {
  if (!src) return []
  const lines = src.split('\n')
  const blocks: string[] = []
  let buf: string[] = []
  let fence: string | null = null

  const flush = () => {
    // 去掉块首尾空行后非空才成块
    const text = buf.join('\n').replace(/^\n+|\n+$/g, '')
    if (text) blocks.push(text)
    buf = []
  }

  for (const line of lines) {
    const m = /^\s{0,3}(`{3,}|~{3,})/.exec(line)
    if (m) {
      if (fence === null) fence = m[1][0] // 进入围栏,记围栏字符(` 或 ~)
      else if (line.trimStart().startsWith(fence.repeat(3))) fence = null // 离开围栏
      buf.push(line)
      continue
    }
    if (fence === null && line.trim() === '') {
      flush()
    } else {
      buf.push(line)
    }
  }
  flush()
  return blocks
}

/** 简易字符串 hash(djb2),用于块 key 摘要 */
function digest(s: string): string {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0
  return h.toString(36)
}

/** 块文本 -> HTML 的模块级 LRU 缓存:已完结块跨 computed 重算直接命中,只重渲尾块 */
const HTML_CACHE_MAX = 400
const htmlCache = new Map<string, string>()

function cachedRender(text: string): string {
  const hit = htmlCache.get(text)
  if (hit !== undefined) {
    // LRU:命中后移到最近使用端
    htmlCache.delete(text)
    htmlCache.set(text, hit)
    return hit
  }
  const html = renderMarkdown(text)
  htmlCache.set(text, html)
  if (htmlCache.size > HTML_CACHE_MAX) {
    // Map 迭代序即插入序,删最久未使用的头部
    const oldest = htmlCache.keys().next()
    if (!oldest.done) htmlCache.delete(oldest.value)
  }
  return html
}

/** 测试隔离用:清空块渲染缓存 */
export function clearBlockCache(): void {
  htmlCache.clear()
}

/**
 * 切块并逐块渲染(带缓存)。已完结块内容不再变化 → 缓存命中、key 不变,
 * Vue diff 原地跳过;只有仍在增长的尾块会真正重跑 markdown 渲染。
 */
export function renderBlocks(src: string): Block[] {
  return splitBlocks(src).map((text, i) => ({
    key: `${i}_${text.length}_${digest(text)}`,
    html: cachedRender(text),
  }))
}
