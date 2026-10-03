import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

// 路由表:登录页 '/login'(公开) + 对话主区 '/' + 知识库管理 '/admin'(懒加载分包)
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { title: '登录', public: true },
    },
    {
      path: '/',
      name: 'chat',
      component: () => import('@/views/ChatView.vue'),
      meta: { title: '智能对话' },
    },
    {
      path: '/admin',
      name: 'admin',
      component: () => import('@/views/AdminView.vue'),
      meta: { title: '知识库管理' },
    },
  ],
})

/**
 * 全局前置守卫:
 * - 首次导航先做一次静默续期(initialize:用 refresh cookie 换 access token);
 * - 公开页(登录)已登录则回主页,避免登录后仍停在登录页;
 * - 受保护页未登录 → 跳登录并带上 redirect,登录成功后回到原目标。
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.initialized) await auth.initialize()

  if (to.meta.public) {
    if (auth.isAuthenticated && to.name === 'login') return { name: 'chat' }
    return true
  }
  if (auth.isAuthenticated) return true
  return { name: 'login', query: { redirect: to.fullPath } }
})

export default router
