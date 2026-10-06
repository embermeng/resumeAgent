<script setup lang="ts">
import { computed, ref } from 'vue'
import { parseKnowledge } from '@/utils/knowledge'

/**
 * RAG 引用卡片(并行线第 3 项)。
 * 数据源:done 帧的 retrieved_knowledge(tools._format_results 固定格式),
 * 经 parseKnowledge 解析为 [编号/来源/正文] 条目,可展开查看全文溯源。
 */
const props = defineProps<{ knowledge?: string; projects?: string }>()

const refs_ = computed(() => parseKnowledge(props.knowledge))
const projectRefs = computed(() => parseKnowledge(props.projects))
const open = ref<Set<string>>(new Set())

function toggle(key: string) {
  const next = new Set(open.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  open.value = next
}

/** 来源文件名去扩展名做展示主体,过长截断由 CSS 负责 */
function sourceLabel(source: string): string {
  return source.replace(/\.(pdf|md|txt|docx)$/i, '')
}

function snippet(text: string): string {
  const t = text.replace(/\s+/g, ' ').trim()
  return t.length > 72 ? `${t.slice(0, 72)}…` : t
}
</script>

<template>
  <div v-if="refs_.length || projectRefs.length" class="citations" data-test="citations">
    <div v-if="refs_.length" class="cit-group">
      <div class="cit-title">📚 知识引用 · {{ refs_.length }}</div>
      <div
        v-for="r in refs_"
        :key="`k${r.index}`"
        class="cit-card"
        :class="{ open: open.has(`k${r.index}`) }"
        :data-test="`citation-${r.index}`"
      >
        <button type="button" class="cit-head" @click="toggle(`k${r.index}`)">
          <span class="cit-idx">{{ r.index }}</span>
          <span class="cit-source">{{ sourceLabel(r.source) }}</span>
          <span class="cit-arrow">›</span>
        </button>
        <p v-if="!open.has(`k${r.index}`)" class="cit-snippet">{{ snippet(r.text) }}</p>
        <div v-else class="cit-full">{{ r.text }}</div>
      </div>
    </div>

    <div v-if="projectRefs.length" class="cit-group">
      <div class="cit-title">🗂 项目素材 · {{ projectRefs.length }}</div>
      <div
        v-for="r in projectRefs"
        :key="`p${r.index}`"
        class="cit-card"
        :class="{ open: open.has(`p${r.index}`) }"
      >
        <button type="button" class="cit-head" @click="toggle(`p${r.index}`)">
          <span class="cit-idx alt">{{ r.index }}</span>
          <span class="cit-source">{{ sourceLabel(r.source) }}</span>
          <span class="cit-arrow">›</span>
        </button>
        <p v-if="!open.has(`p${r.index}`)" class="cit-snippet">{{ snippet(r.text) }}</p>
        <div v-else class="cit-full">{{ r.text }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.citations {
  margin-top: 10px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.cit-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
  margin-bottom: 6px;
}
.cit-group {
  display: flex;
  flex-direction: column;
}
.cit-card {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-bg-color);
  margin-bottom: 6px;
  overflow: hidden;
  transition: border-color 0.2s;
}
.cit-card:hover {
  border-color: var(--el-color-primary-light-5);
}
.cit-card.open {
  border-color: var(--el-color-primary-light-5);
}
.cit-head {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.cit-idx {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
  font-size: 11px;
  font-weight: 700;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.cit-idx.alt {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}
.cit-source {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cit-arrow {
  flex-shrink: 0;
  color: var(--el-text-color-secondary);
  transform: rotate(90deg);
  transition: transform 0.2s;
}
.cit-card.open .cit-arrow {
  transform: rotate(-90deg);
}
.cit-snippet {
  margin: 0;
  padding: 0 10px 8px 38px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}
.cit-full {
  padding: 0 12px 10px 38px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--el-text-color-regular);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 220px;
  overflow-y: auto;
}
</style>
