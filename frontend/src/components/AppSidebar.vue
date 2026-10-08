<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ChatDotRound, ChatLineSquare, Clock, FolderOpened } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'
import ConversationHistory from './ConversationHistory.vue'

/**
 * 全局侧边栏(参考企业级 AI 助手布局):
 * - 渐变「开启新对话」主按钮:清空当前会话并回到对话页
 * - 菜单:智能对话 / 知识库管理(路由切换,当前项高亮)
 * - 历史记录:复用 ConversationHistory 面板(挂载拉取、流结束自动刷新)
 * - 底部版本号
 */
const route = useRoute()
const router = useRouter()
const store = useChatStore()

const activeMenu = computed<'dialog' | 'admin'>(() =>
  route.path.startsWith('/admin') ? 'admin' : 'dialog',
)

function newChat() {
  store.clear()
  if (route.path !== '/') void router.push('/')
}

function go(menu: 'dialog' | 'admin') {
  void router.push(menu === 'admin' ? '/admin' : '/')
}
</script>

<template>
  <aside class="app-sidebar" data-test="app-sidebar">
    <el-button type="primary" class="new-chat-btn" data-test="new-chat-btn" @click="newChat">
      <el-icon><ChatLineSquare /></el-icon>
      <span>开启新对话</span>
    </el-button>

    <nav class="menu-list">
      <button
        type="button"
        class="menu-item"
        :class="{ active: activeMenu === 'dialog' }"
        data-test="menu-dialog"
        @click="go('dialog')"
      >
        <el-icon><ChatDotRound /></el-icon>
        <span>智能对话</span>
      </button>
      <button
        type="button"
        class="menu-item"
        :class="{ active: activeMenu === 'admin' }"
        data-test="menu-admin"
        @click="go('admin')"
      >
        <el-icon><FolderOpened /></el-icon>
        <span>知识库管理</span>
      </button>
    </nav>

    <div class="divider" />

    <div class="his-head">
      <el-icon><Clock /></el-icon>
      <span>历史记录</span>
    </div>
    <div class="his-body">
      <ConversationHistory />
    </div>

    <div class="side-footer">ResumeAgent v1.0</div>
  </aside>
</template>

<style scoped>
.app-sidebar {
  flex: 0 0 240px;
  width: 240px;
  display: flex;
  flex-direction: column;
  padding: 16px 12px 12px;
  background: var(--rp-sidebar-bg);
  border-right: 1px solid var(--el-border-color-lighter);
  font-size: 14px;
}
.new-chat-btn {
  width: 100%;
  height: 40px;
  margin-bottom: 10px;
  border: none;
  border-radius: 8px;
  justify-content: flex-start;
  gap: 10px;
  font-weight: 600;
  background: var(--rp-brand-gradient);
}
.new-chat-btn:hover,
.new-chat-btn:focus {
  background: var(--rp-brand-gradient);
  opacity: 0.92;
}
.menu-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 14px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  font-weight: 500;
  color: #333;
  text-align: left;
  transition: background 0.15s, color 0.15s;
}
.menu-item:hover,
.menu-item.active {
  background: rgba(78, 110, 242, 0.15);
  color: var(--rp-brand-solid);
}
.divider {
  height: 1px;
  background: #d9d9d9;
  margin: 12px 4px;
}
.his-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 14px 8px;
  font-size: 14px;
  font-weight: 500;
  color: #333;
}
.his-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 0 6px;
}
.side-footer {
  margin-top: auto;
  padding-top: 10px;
  text-align: center;
  font-size: 10px;
  color: #999;
}
</style>
