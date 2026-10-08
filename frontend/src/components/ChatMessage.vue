<script setup lang="ts">
import { computed } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import type { ChatMessage as ChatMessageModel } from '@/stores/chat'
import { renderBlocks } from '@/utils/blocks'
import ToolTimeline from './ToolTimeline.vue'
import CitationCards from './CitationCards.vue'

const props = defineProps<{ message: ChatMessageModel }>()
const emit = defineEmits<{
  (e: 'preview', markdown: string): void
  (e: 'download', markdown: string): void
  (e: 'retry', id: string): void
}>()

const INTENT_LABEL: Record<string, string> = {
  quick_response: '快速回答',
  deep_thinking: '深度思考',
  chitchat: '闲聊',
}

const isUser = computed(() => props.message.role === 'user')
/**
 * 稳定块分段渲染:已完结块 key/HTML 均不变,Vue diff 原地跳过,
 * 每帧只重渲仍在增长的尾块 → 长输出不掉帧。
 */
const blocks = computed(() => renderBlocks(props.message.content))
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
const showTimeline = computed(
  () => !isUser.value && (props.message.timeline?.length ?? 0) > 0,
)
const showCitations = computed(
  () => !isUser.value && !props.message.streaming && (!!props.message.retrievedKnowledge || !!props.message.retrievedProjects),
)

function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
// 性能埋点脚注:TTFT + 总耗时 + 字数(与后端耗时日志同口径可对照)
const showFooter = computed(
  () => !isUser.value && !props.message.streaming && (props.message.ttftMs !== undefined || props.message.elapsedMs !== undefined),
)
</script>

<template>
  <div class="chat-message" :class="isUser ? 'is-user' : 'is-assistant'">
    <div class="bubble">
      <div v-if="intentText" class="meta-row">
        <el-tag size="small" :type="isUser ? 'info' : 'primary'" effect="light" round class="intent-tag">
          {{ intentText }}
        </el-tag>
        <span v-if="message.retryCount" class="retry-badge">已重试 {{ message.retryCount }} 次</span>
      </div>

      <ToolTimeline v-if="showTimeline" :stages="message.timeline!" :streaming="message.streaming" />

      <div v-if="showStatus" class="status-line">
        <el-icon class="is-loading"><Loading /></el-icon>
        <span>{{ message.status }}</span>
      </div>

      <div v-if="isUser" class="user-text">{{ message.content }}</div>
      <div v-else class="assistant-content">
        <div class="markdown-body">
          <div v-for="b in blocks" :key="b.key" class="md-block" v-html="b.html" />
        </div>
        <span v-if="showCursor" class="typing-cursor" data-test="cursor" />
      </div>

      <div v-if="message.error" class="err-box" role="alert">
        <span class="err-icon">⚠</span>
        <span class="err-text">{{ message.error }}</span>
        <button
          v-if="!isUser && !message.streaming"
          type="button"
          class="retry-btn"
          data-test="retry-btn"
          @click="emit('retry', message.id)"
        >
          重试
        </button>
      </div>

      <CitationCards
        v-if="showCitations"
        :knowledge="message.retrievedKnowledge"
        :projects="message.retrievedProjects"
      />

      <div v-if="message.resumeFinal" class="resume-actions">
        <el-button size="small" data-test="preview-btn" @click="emit('preview', message.resumeFinal!)">
          预览简历
        </el-button>
        <el-button size="small" type="primary" data-test="download-btn" @click="emit('download', message.resumeFinal!)">
          下载 Markdown
        </el-button>
      </div>

      <div v-if="showFooter" class="msg-footer" data-test="msg-footer">
        <span v-if="message.ttftMs !== undefined">首 token {{ fmtMs(message.ttftMs) }}</span>
        <span v-if="message.elapsedMs !== undefined">总耗时 {{ fmtMs(message.elapsedMs) }}</span>
        <span v-if="message.content">{{ message.content.length }} 字</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-message {
  display: flex;
  padding: 6px 0;
}
.chat-message.is-user {
  justify-content: flex-end;
}
/* 气泡:AI 白底阴影、用户浅紫,圆角参考企业级助手(对角小圆角) */
.bubble {
  max-width: 85%;
  padding: 12px 18px;
  border-radius: 8px 24px 24px 24px;
  background: var(--el-bg-color);
  box-shadow: 0 4px 10px rgba(15, 23, 42, 0.07);
  line-height: 1.7;
  word-break: break-word;
}
.is-user .bubble {
  background: rgba(78, 110, 242, 0.14);
  color: var(--el-text-color-primary);
  border-radius: 24px 8px 24px 24px;
}
.meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.retry-badge {
  font-size: 11px;
  color: var(--el-text-color-secondary);
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
.err-box {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--el-color-danger-light-9);
  border: 1px solid var(--el-color-danger-light-7);
  color: var(--el-color-danger);
  font-size: 13px;
}
.err-icon {
  flex-shrink: 0;
}
.err-text {
  flex: 1;
  min-width: 0;
}
.retry-btn {
  flex-shrink: 0;
  padding: 3px 12px;
  border-radius: 6px;
  border: 1px solid var(--el-color-danger-light-5);
  background: var(--el-bg-color);
  color: var(--el-color-danger);
  font-size: 12px;
  cursor: pointer;
  transition: all 0.2s;
}
.retry-btn:hover {
  background: var(--el-color-danger);
  border-color: var(--el-color-danger);
  color: #fff;
}
.msg-footer {
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--el-border-color-lighter);
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  font-variant-numeric: tabular-nums;
}
.markdown-body :deep(pre.hljs) {
  padding: 12px;
  border-radius: 8px;
  overflow: auto;
  background: #282c34;
  color: #abb2bf;
}
.markdown-body :deep(code) {
  font-family: 'Fira Code', Consolas, Monaco, monospace;
}
.markdown-body :deep(p) {
  margin: 0.4em 0;
}
.markdown-body :deep(.md-block:first-child > *:first-child) {
  margin-top: 0;
}
.markdown-body :deep(.md-block:last-child > *:last-child) {
  margin-bottom: 0;
}
</style>
