<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

// 登录页不显示顶部导航壳(整屏交给 LoginView)
const isPublic = computed(() => route.meta.public === true)

async function onLogout() {
  await auth.logout()
  await router.replace({ name: 'login' })
}
</script>

<template>
  <!-- 公开页(登录):不套导航壳 -->
  <router-view v-if="isPublic" />

  <el-container v-else class="app-shell">
    <el-header class="app-header" height="56px">
      <div class="brand">
        <span class="brand-logo">📄</span>
        <span class="brand-name">ResumeAgent</span>
      </div>
      <el-menu
        :default-active="route.path"
        mode="horizontal"
        router
        class="nav-menu"
        :ellipsis="false"
      >
        <el-menu-item index="/">智能对话</el-menu-item>
        <el-menu-item index="/admin">知识库管理</el-menu-item>
      </el-menu>
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
    </el-header>
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.app-shell {
  height: 100vh;
}
.app-header {
  display: flex;
  align-items: center;
  gap: 24px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  background-color: var(--el-bg-color);
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.04);
  z-index: 10;
}
.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.brand-logo {
  font-size: 20px;
  filter: drop-shadow(0 2px 4px rgba(79, 70, 229, 0.3));
}
.brand-name {
  font-weight: 800;
  font-size: 18px;
  letter-spacing: -0.02em;
  background: linear-gradient(135deg, var(--rp-brand-from), var(--rp-brand-to));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.nav-menu {
  flex: 1;
  border-bottom: none;
}
.user-chip {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  outline: none;
}
.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, var(--rp-brand-from), var(--rp-brand-to));
}
.app-main {
  padding: 0;
  overflow: hidden;
}
</style>
