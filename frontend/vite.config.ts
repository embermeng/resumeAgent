import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// Vite 配置 + Vitest 配置合一(契约见 docs/specs/api-contract.md)
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 开发期把 /api 代理到 FastAPI 后端,规避跨域
    // 用 127.0.0.1 而非 localhost:Windows 下 localhost 可能优先解析为 IPv6(::1),
    // 而 uvicorn 默认绑定 IPv4,会导致代理 ECONNREFUSED(500)。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.ts'],
  },
})
