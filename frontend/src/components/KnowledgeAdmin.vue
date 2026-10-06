<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { storeToRefs } from 'pinia'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { TaskKind } from '@/types/events'

const store = useKnowledgeStore()
const { building, history } = storeToRefs(store)
const latest = computed(() => store.latest)

onMounted(() => {
  // 刷新后恢复本标签页正在跟踪的任务;同时拉历史列表。均内部兜底,不阻塞渲染。
  void store.restoreCurrentTask()
  void store.loadHistory()
})

const TASKS: { kind: TaskKind; label: string; primary?: boolean }[] = [
  { kind: 'build-all', label: '一键构建全部', primary: true },
  { kind: 'parse-pdfs', label: '解析 PDF' },
  { kind: 'extract-summaries', label: '提取课程摘要' },
  { kind: 'split-chunks', label: '文本分块' },
  { kind: 'ingest-highlights', label: '项目亮点入库' },
  { kind: 'build-indexes', label: '构建索引' },
]

const STATUS_TEXT: Record<string, string> = {
  pending: '等待中',
  running: '进行中',
  success: '成功',
  failed: '失败',
}

const tagType = computed(() => {
  const s = latest.value?.status
  if (s === 'success') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'running') return 'warning'
  return 'info'
})

const progressStatus = computed(() => {
  const s = latest.value?.status
  if (s === 'failed') return 'exception'
  if (s === 'success') return 'success'
  return undefined
})

function fmtTime(epoch?: number): string {
  if (!epoch) return '-'
  return new Date(epoch * 1000).toLocaleString()
}

async function run(kind: TaskKind) {
  if (building.value) return
  await store.startBuild({ task: kind })
}
</script>

<template>
  <div class="knowledge-admin">
    <div class="actions">
      <el-button
        v-for="t in TASKS"
        :key="t.kind"
        :type="t.primary ? 'primary' : 'default'"
        :loading="building && latest?.kind === t.kind"
        :disabled="building"
        :data-test="`build-${t.kind}`"
        @click="run(t.kind)"
      >
        {{ t.label }}
      </el-button>
    </div>

    <el-card v-if="latest" class="progress-card" shadow="never">
      <div class="progress-head">
        <span class="kind">当前任务:{{ latest.kind }}</span>
        <el-tag :type="tagType" size="small">{{ STATUS_TEXT[latest.status] || latest.status }}</el-tag>
      </div>
      <el-progress :percentage="Math.round(latest.percent)" :status="progressStatus" />
      <div v-if="latest.message" class="progress-msg">{{ latest.message }}</div>
      <el-alert
        v-if="latest.error"
        type="error"
        :closable="false"
        show-icon
        :title="latest.error"
        class="err"
      />
      <div v-if="latest.logs.length" class="logs">
        <div v-for="(line, i) in latest.logs" :key="i" class="log-line">{{ line }}</div>
      </div>
    </el-card>
    <el-empty v-else description="尚无构建任务" />

    <el-card v-if="history.length" class="history-card" shadow="never">
      <template #header>
        <span class="kind">历史任务(共 {{ store.historyTotal }} 条)</span>
      </template>
      <el-table :data="history" size="small" data-test="task-history">
        <el-table-column prop="task" label="类型" width="150" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag size="small" :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'warning'">
              {{ STATUS_TEXT[row.status] || row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="90">
          <template #default="{ row }">{{ row.percent ?? 0 }}%</template>
        </el-table-column>
        <el-table-column prop="message" label="最新进度" show-overflow-tooltip />
        <el-table-column label="创建时间" width="180">
          <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<style scoped>
.knowledge-admin {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  padding: 16px;
  background: var(--el-bg-color);
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
}
.actions :deep(.el-button) {
  border-radius: 10px;
  font-weight: 500;
}
.progress-card,
.history-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 14px;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
}
.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.kind {
  font-weight: 600;
}
.progress-msg {
  margin-top: 8px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.err {
  margin-top: 10px;
}
/* 日志区:终端深色风格 */
.logs {
  margin-top: 12px;
  max-height: 240px;
  overflow: auto;
  background: #1e1e2e;
  border-radius: 10px;
  padding: 12px 14px;
  font-family: 'Fira Code', Consolas, Monaco, monospace;
  font-size: 12px;
}
.log-line {
  line-height: 1.7;
  white-space: pre-wrap;
  color: #cdd6f4;
}
.log-line::before {
  content: '› ';
  color: #89b4fa;
}
.history-card :deep(.el-card__header) {
  padding: 14px 20px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
</style>
