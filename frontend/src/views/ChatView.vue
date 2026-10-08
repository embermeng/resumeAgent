<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { Top } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'
import ChatMessage from '@/components/ChatMessage.vue'
import ResumeUploader from '@/components/ResumeUploader.vue'

const store = useChatStore()
const { messages, streaming, reconnecting, existingResume, recoverableDraft } = storeToRefs(store)

const input = ref('')
const scroller = ref<HTMLElement | null>(null)
const extensions = ref<string[]>(['.md', '.txt', '.docx', '.pdf'])
const previewVisible = ref(false)
const previewContent = ref('')
const previewHtml = computed(() => renderMarkdown(previewContent.value))

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
      <div class="chat-column">
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
      </div>

      <!-- 滚动容器横跨整个主区,滚动条贴屏幕右侧;内容仍在居中窄栏内 -->
      <div ref="scroller" class="messages" data-test="messages">
        <div class="messages-inner">
          <div v-if="!messages.length" class="hero">
            <div class="welcome">
              <div class="welcome-avatar">🤖</div>
              <div class="welcome-bubble">
                <p class="wb-title">欢迎使用 <b>ResumeAgent</b></p>
                <p class="wb-sub">
                  我已准备好协助你完成课程知识问答、项目素材组装与简历生成,
                  可以点击下方示例,或直接开始对话。
                </p>
              </div>
            </div>
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
      </div>

      <div class="chat-column">
        <div class="composer">
          <div v-if="existingResume" class="resume-chip">
            <el-tag type="success" closable data-test="resume-chip" @close="store.clearExistingResume()">
              已附加简历(深思路径将基于它增强生成)
            </el-tag>
          </div>
          <div class="composer-box">
            <el-input
              v-model="input"
              type="textarea"
              :rows="3"
              resize="none"
              data-test="chat-input"
              placeholder="输入问题或岗位要求...(Enter 发送,Shift+Enter 换行)"
              @keydown.enter.exact.prevent="onSend"
            />
            <div class="composer-foot">
              <div class="foot-left">
                <el-button text size="small" data-test="clear-btn" :disabled="!messages.length" @click="store.clear()">
                  清空
                </el-button>
              </div>
              <button
                v-if="!streaming"
                type="button"
                class="send-circle"
                data-test="send-btn"
                :disabled="!input.trim()"
                title="发送(Enter)"
                @click="onSend"
              >
                <el-icon><Top /></el-icon>
              </button>
              <button
                v-else
                type="button"
                class="send-circle is-stop"
                data-test="stop-btn"
                title="停止生成"
                @click="store.stop()"
              >
                <span class="stop-square" />
              </button>
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
  </div>
</template>

<style scoped>
.chat-view {
  position: relative;
  display: flex;
  height: 100%;
  padding: 16px;
}
/* 主区右侧为悬浮的简历卡片预留空间(300px 遮挡带) */
.chat-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  padding-right: 300px;
}
/* 居中对话窄栏:参考企业级助手的阅读体验 */
.chat-column {
  flex-shrink: 0;
  width: min(920px, 100%);
  margin: 0 auto;
}
.recover-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  padding: 8px 14px;
  border-radius: 10px;
  background: var(--el-color-warning-light-9);
  border: 1px solid var(--el-color-warning-light-7);
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
  margin-bottom: 8px;
  padding: 6px 14px;
  border-radius: 10px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  font-size: 13px;
  color: var(--el-color-primary);
}
.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* 负 margin 抵消 chat-main 的右侧预留,让滚动条贴到屏幕右边;
     再用同宽 padding 把内容挡回预留带之外 */
  margin-right: -300px;
  padding: 8px 300px 16px 0;
  scroll-behavior: smooth;
}
.messages-inner {
  width: min(920px, 100%);
  margin: 0 auto;
}
/* 空状态:渐变欢迎横幅 + 示例卡片 */
.hero {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 6vh 0 24px;
}
.welcome {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.welcome-avatar {
  flex: 0 0 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  background: #101426;
  box-shadow: 0 4px 10px rgba(16, 20, 38, 0.35);
}
.welcome-bubble {
  flex: 1;
  background: var(--rp-brand-gradient);
  color: #fff;
  border-radius: 8px 24px 24px 24px;
  padding: 14px 20px;
  box-shadow: 0 6px 16px rgba(78, 110, 242, 0.25);
}
.wb-title {
  margin: 0 0 6px;
  font-size: 14px;
}
.wb-title b {
  font-size: 16px;
}
.wb-sub {
  margin: 0;
  font-size: 13px;
  line-height: 1.8;
  opacity: 0.95;
}
.hero-suggests {
  display: flex;
  flex-direction: column;
  gap: 8px;
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
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  text-align: center;
}
/* 卡片式输入区:白底圆角阴影,圆形发送按钮 */
.composer {
  padding-top: 4px;
}
.resume-chip {
  margin-bottom: 8px;
}
.composer-box {
  background: var(--el-bg-color);
  border-radius: 12px;
  box-shadow: 0 4px 13px rgba(15, 23, 42, 0.08);
  padding: 12px 14px 10px;
}
.composer-box :deep(.el-textarea__inner) {
  border: none;
  box-shadow: none;
  background: transparent;
  padding: 4px 2px;
}
.composer-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}
.foot-left {
  display: flex;
  align-items: center;
  gap: 4px;
}
.send-circle {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--rp-brand-gradient);
  color: #fff;
  font-size: 16px;
  cursor: pointer;
  box-shadow: 0 3px 8px rgba(78, 110, 242, 0.35);
  transition: transform 0.15s, opacity 0.15s;
}
.send-circle:hover:not(:disabled) {
  transform: scale(1.06);
}
.send-circle:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  box-shadow: none;
}
.send-circle.is-stop {
  background: var(--el-color-danger);
  box-shadow: 0 3px 8px rgba(245, 108, 108, 0.35);
}
.stop-square {
  width: 10px;
  height: 10px;
  border-radius: 2px;
  background: #fff;
}
/* 右侧简历卡片:绝对定位悬浮,不占用滚动容器宽度 */
.chat-side {
  position: absolute;
  right: 28px;
  top: 16px;
  width: 280px;
  border-radius: 12px;
  padding: 16px;
  background: var(--el-bg-color);
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.06);
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
