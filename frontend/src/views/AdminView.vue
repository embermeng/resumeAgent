<script setup lang="ts">
import KnowledgeAdmin from '@/components/KnowledgeAdmin.vue'
</script>

<template>
  <div class="admin-view">
    <div class="page-head">
      <div class="head-icon">🗄️</div>
      <div class="head-text">
        <h2>知识库管理</h2>
        <p class="hint">
          触发知识库构建任务(解析课程 PDF、提取摘要、分块、构建 BM25/向量索引、项目亮点入库),
          并通过 SSE 实时查看进度。
        </p>
      </div>
    </div>
    <KnowledgeAdmin />
  </div>
</template>

<style scoped>
.admin-view {
  height: 100%;
  overflow-y: auto;
  padding: 24px 32px;
}
.page-head {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  margin-bottom: 22px;
}
.head-icon {
  font-size: 28px;
  line-height: 1;
  padding: 10px;
  border-radius: 12px;
  background: linear-gradient(135deg, var(--rp-brand-from), var(--rp-brand-to));
  box-shadow: 0 4px 12px rgba(79, 70, 229, 0.25);
}
.head-text h2 {
  margin: 0 0 6px;
  font-size: 20px;
}
.hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.7;
  max-width: 820px;
}
</style>
