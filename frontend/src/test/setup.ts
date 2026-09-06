import { TextDecoder, TextEncoder } from 'node:util'

// jsdom 缺失的浏览器 API polyfill,保证 composable / 组件测试可运行。

// SSE 字节流解析依赖
if (typeof globalThis.TextEncoder === 'undefined') {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).TextEncoder = TextEncoder
}
if (typeof globalThis.TextDecoder === 'undefined') {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).TextDecoder = TextDecoder
}

// Element Plus 部分组件(el-upload 等)依赖 ResizeObserver
if (typeof globalThis.ResizeObserver === 'undefined') {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// 响应式布局依赖 matchMedia
if (typeof window !== 'undefined' && !window.matchMedia) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(window as any).matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false
    },
  })
}

// Markdown 下载依赖 createObjectURL
if (typeof URL.createObjectURL === 'undefined') {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(URL as any).createObjectURL = () => 'blob:mock'
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(URL as any).revokeObjectURL = () => {}
}
