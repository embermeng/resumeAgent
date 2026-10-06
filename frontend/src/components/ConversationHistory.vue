<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage } from 'element-plus'
import { useChatStore } from '@/stores/chat'

/**
 * 历史会话面板(drawer 内,契约 4.9/4.10)。
 * - 挂载拉列表;流结束后自动刷新(新会话入列 / updated_at 变化)
 * - 点击条目回填消息并切换会话,emit opened 供父级关闭 drawer
 * - 拉取失败由 store 静默降级;打开失败(如 404)由 ElMessage 提示
 */
const PAGE_SIZE = 20

const store = useChatStore()
const { history, historyTotal, historyLoading, streaming, conversationId } = storeToRefs(store)

const page = ref(1)
const emit = defineEmits(['opened'])

onMounted(() => {
  void store.loadHistory(page.value, PAGE_SIZE)
})

// streaming 由 true→false 即一轮对话结束,刷新列表以纳入新会话
watch(streaming, (now, prev) => {
  if (!now && prev) {
    page.value = 1
    void store.loadHistory(page.value, PAGE_SIZE)
  }
})

async function loadMore() {
  page.value += 1
  await store.loadHistory(page.value, PAGE_SIZE, true)
}

async function open(id: number) {
  if (streaming.value) {
    ElMessage.warning('当前对话流式进行中,结束后再打开历史')
    return
  }
  try {
    await store.openConversation(id)
    emit('opened')
  } catch {
    ElMessage.error('加载历史会话失败,可能已不存在')
  }
}

function fmtTime(epoch: number): string {
  const d = new Date(epoch * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}
</script>

<template>
  <div v-loading="historyLoading" class="history-panel">
    <ul v-if="history.length" class="history-list" data-test="history-list">
      <li
        v-for="c in history"
        :key="c.id"
        class="history-item"
        :class="{ active: c.id === conversationId }"
        :data-test="`history-item-${c.id}`"
        :title="c.title"
        @click="open(c.id)"
      >
        <span class="title">{{ c.title }}</span>
        <span class="time">{{ fmtTime(c.updated_at) }}</span>
      </li>
    </ul>
    <el-empty v-else-if="!historyLoading" :image-size="60" description="暂无历史会话" />
    <el-button
      v-if="history.length < historyTotal"
      size="small"
      text
      data-test="load-more-btn"
      @click="loadMore"
    >
      加载更多
    </el-button>
  </div>
</template>

<style scoped>
.history-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 200px;
}
.history-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.history-item {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, box-shadow 0.15s;
}
.history-item:hover {
  background: var(--el-fill-color-light);
}
.history-item.active {
  background: var(--el-color-primary-light-9);
  box-shadow: inset 3px 0 0 var(--el-color-primary);
}
.history-item .title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.history-item .time {
  flex: 0 0 auto;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
