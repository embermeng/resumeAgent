/**
 * retrieved_knowledge 解析(并行线第 3 项:RAG 引用卡片)。
 *
 * 后端 done 帧带回的 retrieved_knowledge 是 src/agent/tools.py `_format_results`
 * 拼好的纯文本,格式固定:每条以 "[i] 来源: xxx" 头行开始,后接正文,
 * 条与条之间用一行 "---" 分隔。
 * 前端按该格式解析为结构化引用条目,渲染可展开的引用卡片。
 */

export interface KnowledgeRef {
  /** 后端编号([i]),解析失败时按顺序补 */
  index: number
  /** 来源文件名/文档名 */
  source: string
  /** 正文片段 */
  text: string
}

const HEADER_RE = /^\[(\d+)\]\s*来源:\s*(.*)$/

/** 解析 retrieved_knowledge 文本;无法识别的条目按"未知来源"兜底,空输入返回 [] */
export function parseKnowledge(raw: string | null | undefined): KnowledgeRef[] {
  if (!raw || !raw.trim() || raw.trim() === '未检索到相关知识。') return []
  return raw
    .split(/\n-{3,}\n/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part, i) => {
      const nl = part.indexOf('\n')
      const head = nl === -1 ? part : part.slice(0, nl)
      const body = nl === -1 ? '' : part.slice(nl + 1).trim()
      const m = HEADER_RE.exec(head.trim())
      if (m) {
        return { index: Number(m[1]), source: m[2].trim() || '未知来源', text: body }
      }
      // 头行不符合契约格式:整段当正文,编号按顺序补
      return { index: i + 1, source: '未知来源', text: part }
    })
}
