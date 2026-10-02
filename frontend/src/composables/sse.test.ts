import { describe, it, expect, vi } from 'vitest'
import { createSSEParser, createEventDispatcher } from './sse'
import type { SSEFrame } from '@/types/events'

const enc = new TextEncoder()

function collect() {
  const frames: SSEFrame[] = []
  const parser = createSSEParser((f) => frames.push(f))
  return { frames, parser }
}

describe('createSSEParser', () => {
  it('解析单帧完整字符串(event + data)', () => {
    const { frames, parser } = collect()
    parser.feed('event: token\ndata: {"text":"hi"}\n\n')
    expect(frames).toEqual([{ event: 'token', data: '{"text":"hi"}' }])
  })

  it('一个 chunk 内含多帧,全部按序解析', () => {
    const { frames, parser } = collect()
    parser.feed(
      'event: status\ndata: {"text":"a"}\n\nevent: intent\ndata: {"value":"chitchat"}\n\n',
    )
    expect(frames.map((f) => f.event)).toEqual(['status', 'intent'])
    expect(JSON.parse(frames[1].data).value).toBe('chitchat')
  })

  it('半帧跨 chunk 缓冲:分两次 feed 才产出一帧', () => {
    const { frames, parser } = collect()
    parser.feed(enc.encode('event: token\nda'))
    expect(frames).toHaveLength(0)
    parser.feed(enc.encode('ta: {"text":"你好"}\n\n'))
    expect(frames).toEqual([{ event: 'token', data: '{"text":"你好"}' }])
  })

  it('多字节 UTF-8 字符被逐字节切割也不乱码', () => {
    const { frames, parser } = collect()
    const bytes = enc.encode('event: token\ndata: {"text":"简历生成"}\n\n')
    for (let i = 0; i < bytes.length; i++) parser.feed(bytes.slice(i, i + 1))
    expect(frames).toHaveLength(1)
    expect(JSON.parse(frames[0].data).text).toBe('简历生成')
  })

  it('data 内被转义的换行仍为单行,JSON 可还原真实换行', () => {
    const { frames, parser } = collect()
    parser.feed('event: token\ndata: {"text":"a\\nb"}\n\n')
    expect(frames[0].data).toBe('{"text":"a\\nb"}')
    expect(JSON.parse(frames[0].data).text).toBe('a\nb')
  })

  it('忽略注释行(心跳):纯 ":" 注释帧不产出事件', () => {
    const { frames, parser } = collect()
    parser.feed(': keep-alive\n\n')
    parser.feed('event: done\ndata: {"ok":true}\n\n')
    expect(frames).toEqual([{ event: 'done', data: '{"ok":true}' }])
  })

  it('空 data 行按 SSE 规范视为一次空数据事件(非注释)', () => {
    const { frames, parser } = collect()
    parser.feed('event: ping\ndata:\n\n')
    expect(frames).toEqual([{ event: 'ping', data: '' }])
  })

  it('多行 data 按 SSE 规范以 \\n 连接', () => {
    const { frames, parser } = collect()
    parser.feed('event: log\ndata: line1\ndata: line2\n\n')
    expect(frames[0].data).toBe('line1\nline2')
  })

  it('flush 冲刷结尾缺失 \\n\\n 的最后一帧', () => {
    const { frames, parser } = collect()
    parser.feed('event: done\ndata: {"status":"success"}')
    expect(frames).toHaveLength(0)
    parser.flush()
    expect(frames).toEqual([{ event: 'done', data: '{"status":"success"}' }])
  })
})

describe('createEventDispatcher', () => {
  it('DEV 下每帧无损打印到 console.log(含首帧)且正常派发', () => {
    const debug = vi.spyOn(console, 'log').mockImplementation(() => {})
    const events: { type: string }[] = []
    const dispatch = createEventDispatcher(() => {}, (e) => events.push(e))
    dispatch({ event: 'conversation', data: '{"conversation_id":5}' })
    dispatch({ event: 'token', data: '{"text":"hi"}' })
    expect(debug.mock.calls.map((c) => [c[1], c[2]])).toEqual([
      ['conversation', '{"conversation_id":5}'],
      ['token', '{"text":"hi"}'],
    ])
    expect(events.map((e) => e.type)).toEqual(['conversation', 'token'])
    debug.mockRestore()
  })

  it('非法 JSON 帧仍先打印原始帧再报错(日志无损不受解析失败影响)', () => {
    const debug = vi.spyOn(console, 'log').mockImplementation(() => {})
    const errors: string[] = []
    const events: { type: string }[] = []
    const dispatch = createEventDispatcher((m) => errors.push(m), (e) => events.push(e))
    dispatch({ event: 'token', data: '{bad json' })
    expect(debug).toHaveBeenCalledTimes(1)
    expect(debug.mock.calls[0][1]).toBe('token')
    expect(errors).toHaveLength(1)
    expect(events.map((e) => e.type)).toEqual(['error'])
    debug.mockRestore()
  })
})
