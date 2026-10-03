import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import App from './App.vue'
import router from './router'
import './assets/main.css'
import { setOnSessionLost } from '@/api/authToken'
import { useAuthStore } from '@/stores/auth'

const app = createApp(App)
const pinia = createPinia()
app.use(pinia).use(router).use(ElementPlus)

// 会话彻底失效(refresh 也 401)时:清本地态并跳登录,带上当前路径便于登录后回跳。
// 在请求层(client.ts)与路由层之间架桥,避免 client 直接依赖 router/store。
setOnSessionLost(() => {
  const auth = useAuthStore(pinia)
  auth.reset()
  const current = router.currentRoute.value
  if (current.name !== 'login') {
    router.replace({ name: 'login', query: { redirect: current.fullPath } })
  }
})

app.mount('#app')
