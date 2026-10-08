<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AppSidebar from '@/components/AppSidebar.vue'

/**
 * 应用外壳(参考企业级 AI 助手布局):
 * - 顶栏:粉彩渐变 + 品牌标识 + 用户菜单
 * - 左侧:AppSidebar(新对话/菜单/历史)
 * - 主区:router-view
 * 登录页(meta.public)不套壳,整屏交给 LoginView。
 */
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const isPublic = computed(() => route.meta.public === true)

async function onLogout() {
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <!-- 公开页(登录):不套导航壳 -->
  <router-view v-if="isPublic" />

  <div v-else class="app-shell">
    <header class="app-topbar">
      <div class="brand">
        <span class="brand-logo">AI</span>
        <span class="brand-name">ResumeAgent 智能体</span>
      </div>
      <el-dropdown v-if="auth.isAuthenticated" trigger="click">
        <span class="user-chip">
          <span class="user-avatar">{{ auth.user?.username?.charAt(0)?.toUpperCase() }}</span>
          {{ auth.user?.username }}
        </span>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>{{ auth.user?.email }}</el-dropdown-item>
            <el-dropdown-item divided @click="onLogout">退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </header>
    <div class="app-body">
      <AppSidebar />
      <main class="app-main">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100vh;
}
.app-topbar {
  flex: 0 0 56px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  background: var(--rp-topbar-bg);
  z-index: 10;
}
.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  white-space: nowrap;
}
.brand-logo {
  width: 32px;
  height: 32px;
  border-radius: 9px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 800;
  color: #fff;
  background: var(--rp-brand-gradient);
  box-shadow: 0 2px 6px rgba(78, 110, 242, 0.35);
}
.brand-name {
  font-weight: 800;
  font-size: 17px;
  letter-spacing: -0.01em;
  color: #1f2937;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px 4px 5px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
  cursor: pointer;
  font-size: 13px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  outline: none;
}
.user-avatar {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--rp-brand-gradient);
}
.app-body {
  flex: 1;
  display: flex;
  min-height: 0;
}
.app-main {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  background: var(--rp-page-bg);
}
</style>
