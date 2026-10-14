<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete } from '@element-plus/icons-vue'
import { useChatStore } from '@/stores/chat'
import type { ConversationSummary } from '@/types/events'

/**
 * 历史会话面板(drawer 内,契约 4.11/4.12/4.12b)。
 * - 挂载拉列表;流结束后自动刷新(新会话入列 / updated_at 变化)
 * - 点击条目回填消息并切换会话,emit opened 供父级关闭 drawer
 * - 条目 hover 才显示删除图标,点击弹确认后删除该会话及其消息
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

/** 删除会话:确认后真删,取消则不动。图标已 .stop 阻止冒泡,不会连带打开会话 */
async function onRemove(conv: ConversationSummary): Promise<void> {
  if (streaming.value) {
    ElMessage.warning('当前对话流式进行中,结束后再删除历史')
    return
  }
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${conv.title}」?其中的对话内容会一并删除,不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  if (await store.removeConversation(conv.id)) ElMessage.success('已删除')
  else ElMessage.error('删除失败,会话可能已不存在')
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
        <el-button
          link
          type="danger"
          size="small"
          class="del"
          :data-test="`history-delete-${c.id}`"
          title="删除会话"
          @click.stop="onRemove(c)"
        >
          <el-icon><Delete /></el-icon>
        </el-button>
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
  gap: 6px;
}
.history-list {
  margin: 0;
  padding: 0;
  list-style: none;
}
.history-item {
  position: relative;
  display: flex;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.history-item:hover {
  background: rgba(0, 0, 0, 0.04);
}
.history-item.active {
  background: rgba(78, 110, 242, 0.15);
  color: var(--rp-brand-solid);
}
.history-item.active .time {
  color: var(--rp-brand-solid);
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
/*
 * 删除图标平时隐身,hover(或键盘聚焦)时顶掉右侧时间显示在同一位置:
 * 既不给条目增加常驻占位,也不会因宽度变化引起布局跳动。
 */
.history-item .del {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  opacity: 0;
  /* 隐身时不接收点击,否则透明按钮会吃掉条目右侧的点击区 */
  pointer-events: none;
}
.history-item:hover .del,
.history-item:focus-within .del {
  opacity: 1;
  pointer-events: auto;
}
.history-item:hover .time,
.history-item:focus-within .time {
  opacity: 0;
}
</style>
