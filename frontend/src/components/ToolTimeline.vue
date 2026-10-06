<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Loading } from '@element-plus/icons-vue'
import type { TimelineStage } from '@/stores/chat'

/**
 * 工具调用时间线(并行线第 2 项)。
 * 数据源:后端 run_stream 的 status 帧,由 chat store 收集为 timeline
 * (含每阶段耗时结算)。流式期间自动展开,done 后自动收起为摘要行。
 */
const props = defineProps<{ stages: TimelineStage[]; streaming?: boolean }>()

const expanded = ref(false)
watch(
  () => props.streaming,
  (v) => {
    expanded.value = v === true
  },
  { immediate: true },
)

const allDone = computed(
  () => props.stages.length > 0 && props.stages.every((s) => s.elapsed !== null),
)
const totalMs = computed(() =>
  props.stages.reduce((acc, s) => acc + (s.elapsed ?? 0), 0),
)

function fmt(ms: number | null): string {
  if (ms === null) return ''
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
</script>

<template>
  <div v-if="stages.length" class="tool-timeline" data-test="timeline">
    <button
      type="button"
      class="tl-toggle"
      data-test="timeline-toggle"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <el-icon v-if="streaming && !allDone" class="is-loading"><Loading /></el-icon>
      <span v-else class="tl-dot">✓</span>
      <span class="tl-summary">
        {{ streaming ? stages[stages.length - 1].text : `处理过程 · ${stages.length} 个阶段` }}
        <template v-if="allDone && totalMs > 0"> · 共 {{ fmt(totalMs) }}</template>
      </span>
      <span class="tl-arrow" :class="{ open: expanded }">›</span>
    </button>

    <ul v-show="expanded" class="tl-list">
      <li v-for="(s, i) in stages" :key="i" class="tl-item" :class="{ active: s.elapsed === null }">
        <span class="tl-node" />
        <span class="tl-text">{{ s.text }}</span>
        <span class="tl-elapsed" data-test="tl-elapsed">
          {{ s.elapsed === null ? '进行中…' : fmt(s.elapsed) }}
        </span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.tool-timeline {
  margin: 2px 0 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
  overflow: hidden;
}
.tl-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  text-align: left;
}
.tl-toggle:hover {
  color: var(--el-color-primary);
}
.tl-dot {
  color: var(--el-color-success);
  font-size: 12px;
}
.tl-summary {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tl-arrow {
  transform: rotate(90deg);
  transition: transform 0.2s;
  font-size: 14px;
}
.tl-arrow.open {
  transform: rotate(-90deg);
}
.tl-list {
  list-style: none;
  margin: 0;
  padding: 4px 10px 8px 26px;
}
.tl-item {
  position: relative;
  padding: 3px 0 3px 12px;
  font-size: 12px;
  color: var(--el-text-color-regular);
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.tl-item::before {
  content: '';
  position: absolute;
  left: -8px;
  top: 0;
  bottom: 0;
  width: 1px;
  background: var(--el-border-color);
}
.tl-item:first-child::before {
  top: 50%;
}
.tl-item:last-child::before {
  bottom: 50%;
}
.tl-node {
  position: absolute;
  left: -11px;
  top: 50%;
  transform: translateY(-50%);
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--el-color-success);
}
.tl-item.active .tl-node {
  background: var(--el-color-primary);
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  50% {
    opacity: 0.4;
  }
}
.tl-text {
  flex: 1;
  min-width: 0;
}
.tl-elapsed {
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
  color: var(--el-text-color-secondary);
}
</style>
