<script setup lang="ts">
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import type { ChatMessage as ChatMessageModel } from '@/stores/chat'
import { renderMarkdown } from '@/utils/markdown'

const props = defineProps<{ message: ChatMessageModel }>()
const emit = defineEmits<{
  (e: 'preview', markdown: string): void
  (e: 'download', markdown: string): void
}>()

const INTENT_LABEL: Record<string, string> = {
  quick_response: '快速回答',
  deep_thinking: '深度思考',
  chitchat: '闲聊',
}

const isUser = computed(() => props.message.role === 'user')
const html = computed(() => renderMarkdown(props.message.content))
const showCursor = computed(
  () => !isUser.value && props.message.streaming === true,
)
// 尚无正文、仅有阶段提示时,展示 status(带 loading 图标)
const showStatus = computed(
  () => !isUser.value && props.message.streaming === true && !props.message.content && !!props.message.status,
)
const intentText = computed(() =>
  props.message.intent ? INTENT_LABEL[props.message.intent] ?? props.message.intent : '',
)
</script>

<template>
  <div class="chat-message" :class="isUser ? 'is-user' : 'is-assistant'">
    <div class="avatar">{{ isUser ? '我' : 'AI' }}</div>
    <div class="bubble">
      <el-tag v-if="intentText" size="small" type="info" class="intent-tag">{{ intentText }}</el-tag>

      <div v-if="showStatus" class="status-line">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>{{ message.status }}</span>
      </div>

      <div v-if="isUser" class="user-text">{{ message.content }}</div>
      <div v-else class="assistant-content">
        <div class="markdown-body" v-html="html" />
        <span v-if="showCursor" class="typing-cursor" data-test="cursor" />
      </div>

      <el-alert
        v-if="message.error"
        type="error"
        :closable="false"
        show-icon
        :title="message.error"
        class="err"
      />

      <div v-if="message.resumeFinal" class="resume-actions">
        <el-button size="small" data-test="preview-btn" @click="emit('preview', message.resumeFinal!)">
          预览简历
        </el-button>
        <el-button size="small" type="primary" data-test="download-btn" @click="emit('download', message.resumeFinal!)">
          下载 Markdown
        </el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-message {
  display: flex;
  gap: 10px;
  padding: 12px 16px;
}
.chat-message.is-user {
  flex-direction: row-reverse;
}
.avatar {
  flex: 0 0 34px;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--el-color-primary-light-8);
  color: var(--el-color-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 13px;
  font-weight: 600;
}
.bubble {
  max-width: 76%;
  padding: 10px 14px;
  border-radius: 8px;
  background: var(--el-fill-color-light);
  line-height: 1.6;
  word-break: break-word;
}
.is-user .bubble {
  background: var(--el-color-primary-light-9);
}
.intent-tag {
  margin-bottom: 6px;
}
.status-line {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.user-text {
  white-space: pre-wrap;
}
.typing-cursor {
  display: inline-block;
  width: 7px;
  height: 15px;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--el-color-primary);
  animation: blink 1s steps(2, start) infinite;
}
@keyframes blink {
  to {
    visibility: hidden;
  }
}
.resume-actions {
  margin-top: 10px;
  display: flex;
  gap: 8px;
}
.err {
  margin-top: 8px;
}
.markdown-body :deep(pre.hljs) {
  padding: 12px;
  border-radius: 6px;
  overflow: auto;
  background: #282c34;
  color: #abb2bf;
}
.markdown-body :deep(code) {
  font-family: 'Fira Code', Consolas, Monaco, monospace;
}
</style>
