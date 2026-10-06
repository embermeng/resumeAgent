<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'
import ChatMessage from '@/components/ChatMessage.vue'
import ResumeUploader from '@/components/ResumeUploader.vue'
import ConversationHistory from '@/components/ConversationHistory.vue'

const store = useChatStore()
const { messages, streaming, reconnecting, existingResume, recoverableDraft } = storeToRefs(store)

const input = ref('')
const scroller = ref<HTMLElement | null>(null)
const extensions = ref<string[]>(['.md', '.txt', '.docx', '.pdf'])
const previewVisible = ref(false)
const previewContent = ref('')
const previewHtml = computed(() => renderMarkdown(previewContent.value))
const historyVisible = ref(false)

// 空状态引导:点击示例问题直接填入输入框
const SUGGESTIONS = [
  { icon: '💡', text: '什么是 RAG?它解决了什么问题?' },
  { icon: '📝', text: '帮我根据课程知识生成一份 AI 应用工程师简历' },
  { icon: '🎯', text: '这是我的目标岗位 JD,请针对性优化简历' },
]

onMounted(async () => {
  try {
    const r = await api.getSupportedExtensions()
    if (r?.extensions?.length) extensions.value = r.extensions
  } catch {
    /* 拉取失败则用默认白名单 */
  }
})

function scrollToBottom() {
  if (scroller.value) scroller.value.scrollTop = scroller.value.scrollHeight
}

// 消息数量或流式内容变化时自动滚到底部
watch(
  () => [messages.value.length, messages.value[messages.value.length - 1]?.content],
  async () => {
    await nextTick()
    scrollToBottom()
  },
)

async function onSend() {
  const text = input.value.trim()
  if (!text || streaming.value) return
  input.value = ''
  await store.send(text)
}

function onRetry(id: string) {
  void store.retryMessage(id)
}

function onParsed(payload: { filename: string; content: string }) {
  store.setExistingResume(payload.content)
}

function onUploadError(message: string) {
  ElMessage.warning(message)
}

function onPreview(markdown: string) {
  previewContent.value = markdown
  previewVisible.value = true
}

function onDownload(markdown: string) {
  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `resume_${Date.now()}.md`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
</script>

<template>
  <div class="chat-view">
    <section class="chat-main">
      <!-- 断线/刷新后的内容保全恢复横幅 -->
      <div v-if="recoverableDraft" class="recover-bar" data-test="recover-bar">
        <span class="recover-text">检测到上次未完成的生成内容({{ recoverableDraft.content.length }} 字),是否恢复?</span>
        <div class="recover-actions">
          <el-button size="small" type="primary" data-test="recover-yes" @click="store.restoreDraft()">恢复</el-button>
          <el-button size="small" text data-test="recover-no" @click="store.discardDraft()">丢弃</el-button>
        </div>
      </div>

      <!-- 断线自动重连进行中提示(区别于正常流式) -->
      <div v-if="reconnecting" class="reconnect-bar" data-test="reconnect-bar">
        <span class="reconnect-text">连接中断,正在恢复…</span>
      </div>

      <div ref="scroller" class="messages" data-test="messages">
        <div v-if="!messages.length" class="hero">
          <div class="hero-logo">📄</div>
          <h2 class="hero-title">ResumeAgent</h2>
          <p class="hero-sub">课程知识问答 · 项目素材组装 · 一键生成简历</p>
          <div class="hero-suggests">
            <button
              v-for="s in SUGGESTIONS"
              :key="s.text"
              type="button"
              class="suggest-card"
              data-test="suggestion"
              @click="input = s.text"
            >
              <span class="suggest-icon">{{ s.icon }}</span>
              <span class="suggest-text">{{ s.text }}</span>
            </button>
          </div>
          <p class="hero-hint">开始对话:提问课程知识,或粘贴 JD 让我生成简历</p>
        </div>
        <ChatMessage
          v-for="m in messages"
          :key="m.id"
          :message="m"
          @preview="onPreview"
          @download="onDownload"
          @retry="onRetry"
        />
      </div>

      <div class="composer">
        <div v-if="existingResume" class="resume-chip">
          <el-tag type="success" closable data-test="resume-chip" @close="store.clearExistingResume()">
            已附加简历(深思路径将基于它增强生成)
          </el-tag>
        </div>
        <div class="input-row">
          <el-input
            v-model="input"
            type="textarea"
            :rows="3"
            resize="none"
            data-test="chat-input"
            placeholder="输入问题或岗位要求...(Enter 发送,Shift+Enter 换行)"
            @keydown.enter.exact.prevent="onSend"
          />
          <div class="btns">
            <el-button
              v-if="!streaming"
              type="primary"
              class="send-btn"
              data-test="send-btn"
              :disabled="!input.trim()"
              @click="onSend"
            >
              发送 ↑
            </el-button>
            <el-button v-else type="danger" class="send-btn" data-test="stop-btn" @click="store.stop()">
              停止 ■
            </el-button>
            <div class="btn-row">
              <el-button text size="small" data-test="clear-btn" :disabled="!messages.length" @click="store.clear()">
                清空
              </el-button>
              <el-button text size="small" data-test="history-btn" @click="historyVisible = true">
                历史
              </el-button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <aside class="chat-side">
      <h3 class="side-title">📎 已有简历</h3>
      <ResumeUploader :extensions="extensions" @parsed="onParsed" @error="onUploadError" />
      <p class="tip">上传简历后,深思路径生成时会基于它增强。</p>
    </aside>

    <el-dialog v-model="previewVisible" title="简历预览" width="70%" top="6vh">
      <div class="markdown-body preview-body" v-html="previewHtml" />
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button type="primary" @click="onDownload(previewContent)">下载 Markdown</el-button>
      </template>
    </el-dialog>

    <!-- 历史会话抽屉;teleported=false 保持 DOM 在组件树内,便于测试定位 -->
    <el-drawer
      v-model="historyVisible"
      title="历史会话"
      direction="ltr"
      size="320px"
      :teleported="false"
    >
      <ConversationHistory @opened="historyVisible = false" />
    </el-drawer>
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  height: 100%;
  gap: 16px;
  padding: 16px;
}
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  background: var(--el-bg-color);
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
  overflow: hidden;
}
.recover-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  background: var(--el-color-warning-light-9);
  border-bottom: 1px solid var(--el-color-warning-light-7);
  font-size: 13px;
  color: var(--el-text-color-primary);
}
.recover-text {
  flex: 1;
  min-width: 0;
}
.recover-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.reconnect-bar {
  display: flex;
  align-items: center;
  padding: 6px 16px;
  background: var(--el-color-primary-light-9);
  border-bottom: 1px solid var(--el-color-primary-light-7);
  font-size: 13px;
  color: var(--el-color-primary);
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px 0;
  scroll-behavior: smooth;
}
/* 空状态 hero */
.hero {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 24px;
}
.hero-logo {
  font-size: 44px;
  filter: drop-shadow(0 4px 10px rgba(79, 70, 229, 0.25));
}
.hero-title {
  margin: 4px 0 0;
  font-size: 22px;
  background: linear-gradient(135deg, var(--rp-brand-from), var(--rp-brand-to));
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.hero-sub {
  margin: 0 0 18px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.hero-suggests {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: min(460px, 100%);
}
.suggest-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 10px;
  background: var(--el-bg-color);
  cursor: pointer;
  text-align: left;
  font-size: 13px;
  color: var(--el-text-color-regular);
  transition: all 0.2s;
}
.suggest-card:hover {
  border-color: var(--el-color-primary-light-5);
  background: var(--el-color-primary-light-9);
  transform: translateX(2px);
}
.hero-hint {
  margin: 16px 0 0;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}
.composer {
  border-top: 1px solid var(--el-border-color-lighter);
  padding: 12px 16px;
  background: var(--el-bg-color);
}
.resume-chip {
  margin-bottom: 8px;
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.input-row :deep(.el-textarea__inner) {
  border-radius: 10px;
  padding: 10px 12px;
  box-shadow: 0 0 0 1px var(--el-border-color) inset;
  transition: box-shadow 0.2s;
}
.input-row :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
}
.btns {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.send-btn {
  border-radius: 10px;
  font-weight: 600;
}
.btn-row {
  display: flex;
  gap: 2px;
  justify-content: flex-end;
}
.chat-side {
  flex: 0 0 280px;
  width: 280px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  padding: 16px;
  background: var(--el-bg-color);
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
  align-self: flex-start;
}
.side-title {
  margin: 0 0 12px;
  font-size: 15px;
}
.tip {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
}
.preview-body {
  max-height: 66vh;
  overflow: auto;
}
</style>
