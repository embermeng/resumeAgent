<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/stores/chat'
import * as api from '@/api/client'
import { renderMarkdown } from '@/utils/markdown'
import ChatMessage from '@/components/ChatMessage.vue'
import ResumeUploader from '@/components/ResumeUploader.vue'

const store = useChatStore()
const { messages, streaming, existingResume } = storeToRefs(store)

const input = ref('')
const scroller = ref<HTMLElement | null>(null)
const extensions = ref<string[]>(['.md', '.txt', '.docx', '.pdf'])
const previewVisible = ref(false)
const previewContent = ref('')
const previewHtml = computed(() => renderMarkdown(previewContent.value))

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
      <div ref="scroller" class="messages" data-test="messages">
        <el-empty
          v-if="!messages.length"
          description="开始对话:提问课程知识,或粘贴 JD 让我生成简历"
        />
        <ChatMessage
          v-for="m in messages"
          :key="m.id"
          :message="m"
          @preview="onPreview"
          @download="onDownload"
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
              data-test="send-btn"
              :disabled="!input.trim()"
              @click="onSend"
            >
              发送
            </el-button>
            <el-button v-else type="danger" data-test="stop-btn" @click="store.stop()">停止</el-button>
            <el-button data-test="clear-btn" :disabled="!messages.length" @click="store.clear()">
              清空
            </el-button>
          </div>
        </div>
      </div>
    </section>

    <aside class="chat-side">
      <h3 class="side-title">已有简历</h3>
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
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  background: var(--el-bg-color);
}
.messages {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}
.composer {
  border-top: 1px solid var(--el-border-color-light);
  padding: 12px;
}
.resume-chip {
  margin-bottom: 8px;
}
.input-row {
  display: flex;
  gap: 10px;
  align-items: flex-end;
}
.btns {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.chat-side {
  flex: 0 0 280px;
  width: 280px;
  border: 1px solid var(--el-border-color-light);
  border-radius: 10px;
  padding: 16px;
  background: var(--el-bg-color);
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
