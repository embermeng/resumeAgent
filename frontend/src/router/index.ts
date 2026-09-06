import { createRouter, createWebHistory } from 'vue-router'

// 路由表:对话主区 '/' + 知识库管理 '/admin'(懒加载分包)
const router = createRouter({
  history: createWebHistory(),
  routes: [
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

export default router
