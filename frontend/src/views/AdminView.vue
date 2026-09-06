<script setup lang="ts">
import KnowledgeAdmin from '@/components/KnowledgeAdmin.vue'
</script>

<template>
  <div class="admin-view">
    <div class="page-head">
      <h2>知识库管理</h2>
      <p class="hint">
        触发知识库构建任务(解析课程 PDF、提取摘要、分块、构建 BM25/向量索引、项目亮点入库),
        并通过 SSE 实时查看进度。
      </p>
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
  margin-bottom: 20px;
}
.page-head h2 {
  margin: 0 0 6px;
}
.hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.7;
  max-width: 820px;
}
</style>
