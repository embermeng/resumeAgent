<script setup lang="ts">
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <el-container class="app-shell">
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
.app-main {
  padding: 0;
  overflow: hidden;
}
</style>
