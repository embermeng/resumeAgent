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
      <div class="brand">📄 ResumeAgent</div>
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
        <span class="user-chip">👤 {{ auth.user?.username }}</span>
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
  border-bottom: 1px solid var(--el-border-color-light);
  background-color: var(--el-bg-color);
}
.brand {
  font-weight: 700;
  font-size: 18px;
  white-space: nowrap;
}
.nav-menu {
  flex: 1;
  border-bottom: none;
}
.user-chip {
  cursor: pointer;
  font-size: 14px;
  color: var(--el-text-color-primary);
  white-space: nowrap;
  outline: none;
}
.app-main {
  padding: 0;
  overflow: hidden;
}
</style>
