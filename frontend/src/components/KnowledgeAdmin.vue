<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useKnowledgeStore } from '@/stores/knowledge'
import type { TaskKind } from '@/types/events'

const store = useKnowledgeStore()
const { building } = storeToRefs(store)
const latest = computed(() => store.latest)

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
  </div>
</template>

<style scoped>
.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 18px;
}
.progress-card {
  border: 1px solid var(--el-border-color-light);
}
.progress-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
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
.logs {
  margin-top: 12px;
  max-height: 220px;
  overflow: auto;
  background: var(--el-fill-color-lighter);
  border-radius: 6px;
  padding: 8px 12px;
  font-family: Consolas, Monaco, monospace;
  font-size: 12px;
}
.log-line {
  line-height: 1.7;
  white-space: pre-wrap;
}
</style>
