<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { Delete, Document, UploadFilled, WarningFilled } from '@element-plus/icons-vue'
import { useProjectStore } from '@/stores/project'
import { fetchProjectTemplate } from '@/api/client'
import { downloadTextFile } from '@/utils/download'
import type { ProjectDocOut } from '@/types/events'

/**
 * 我的项目文档面板(抽屉内容)。
 *
 * 与简历上传的区别:项目文档有配额、按项目名覆盖,且供检索定向命中;
 * 简历(见 ChatView 右侧卡片)同样服务端长期保留,但只用于生成时的事实骨架。
 * 上传流程同构:POST 拿 task_id → SSE 进度 → 终态后刷新列表。
 */
const store = useProjectStore()
const extensions = ['.md']

const PRIORITY_TAG = '简历优先'

// 列表拉取由 AppSideBar 的抽屉 watch 统一触发(挂载即拉会与 watch 重复请求);
// 页面加载时的徽标拉取也由 AppSideBar.onMounted 负责,本组件不再自己拉

function isSupported(name: string): boolean {
  const lower = name.toLowerCase()
  return extensions.some((ext) => lower.endsWith(ext))
}

function beforeUpload(file: File): boolean {
  if (!isSupported(file.name)) {
    ElMessage.error(`只支持 Markdown 文件: ${extensions.join(' / ')}`)
    return false
  }
  if (store.atQuota) {
    ElMessage.error(`项目文档数量已达上限(${store.maxDocs} 份),请先删除后再上传`)
    return false
  }
  return true
}

/** 同名项目后端按项目名覆盖,上传前提示一次,避免静默替换 */
function hasSameName(file: File): boolean {
  const stem = file.name.replace(/\.md$/i, '')
  return store.docs.some((d) => d.project_name === stem || d.file_name === file.name)
}

async function httpRequest(options: UploadRequestOptions): Promise<void> {
  const file = options.file as File
  if (hasSameName(file)) {
    try {
      await ElMessageBox.confirm(
        `已存在同名项目「${file.name.replace(/\.md$/i, '')}」,上传将覆盖其内容。`,
        '覆盖确认',
        { type: 'warning', confirmButtonText: '覆盖', cancelButtonText: '取消' },
      )
    } catch {
      return // 用户取消
    }
  }
  // 只做预处理；store.preview 有值后弹窗自动打开，入库等用户确认（见 onConfirm）
  const preview = await store.preprocessDoc(file)
  if (!preview) ElMessage.error(store.error ?? '预处理失败')
}

/**
 * 下载标准模板。文件名由后端给定（中文友好），避免前端拼接产生不一致。
 * 放在上传区**外面**而非 el-upload 内部——内部的点击会冒泡触发文件选择框。
 */
async function onDownloadTemplate(): Promise<void> {
  try {
    const tpl = await fetchProjectTemplate()
    downloadTextFile(tpl.file_name, tpl.content)
  } catch {
    ElMessage.error('模板下载失败，请稍后重试')
  }
}

/** 用户在预览中确认：提交入库（成功后 store 清空 preview，弹窗随之关闭） */
async function onConfirm(): Promise<void> {
  const ok = await store.confirmDoc(editableText.value)
  if (ok) ElMessage.success('项目文档已入库')
  else ElMessage.error(store.error ?? '入库失败')
}

async function onRemove(doc: ProjectDocOut): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确定删除「${doc.project_name}」?其分块与索引会一并清除。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  if (await store.removeDoc(doc.doc_id)) ElMessage.success('已删除')
  else ElMessage.error(store.error ?? '删除失败')
}

function tooltip(doc: ProjectDocOut): string {
  if (doc.has_resume_section) return '含「简历描述」章节,简历生成直接引用'
  return '缺少「简历描述」章节,简历生成将降级使用全文'
}

/* ----------------------------------------------------------
 * 预处理预览
 * ---------------------------------------------------------- */

/**
 * 预览弹窗开关直接由 store.preview 驱动（状态单一来源）：
 * 预处理成功 → 有 preview → 弹窗打开；确认成功/取消 → preview 清空 → 关闭。
 * 失败时 preview 保留，弹窗不关，用户可直接重试。
 */
const previewVisible = computed<boolean>({
  get: () => store.preview !== null,
  set: (v: boolean) => {
    if (!v) store.clearPreview()
  },
})
/** 用户在预览里可编辑的文本（默认取整理结果） */
const editableText = ref('')
/** 是否对照展示原文 */
const showOriginal = ref(false)

watch(
  () => store.preview,
  (p) => {
    if (p) {
      editableText.value = p.normalized_text
      showOriginal.value = false
    }
  },
)

/* ----------------------------------------------------------
 * 多选与批量删除
 * ---------------------------------------------------------- */

/** 已选中的 doc_id。公共素材不可选(后端会跳过,前端直接不给入口) */
const selected = ref<Set<string>>(new Set())

/** 可勾选的文档 = 非公共素材 */
const selectableDocs = computed(() => store.docs.filter((d) => !d.is_public))
const selectedCount = computed(() => selected.value.size)
const allSelected = computed(
  () => selectableDocs.value.length > 0 && selected.value.size === selectableDocs.value.length,
)
/** 半选态:选了一部分但不是全部 */
const indeterminate = computed(
  () => selected.value.size > 0 && selected.value.size < selectableDocs.value.length,
)

function toggleAll(checked: boolean): void {
  selected.value = checked ? new Set(selectableDocs.value.map((d) => d.doc_id)) : new Set()
}

function toggleOne(docId: string, checked: boolean): void {
  const next = new Set(selected.value)
  if (checked) next.add(docId)
  else next.delete(docId)
  selected.value = next
}

/** 列表变化(上传成功刷新/他人删除)后丢弃已不存在的选中项,避免按钮数与实际不符 */
watch(
  () => store.docs,
  (docs) => {
    const alive = new Set(docs.map((d) => d.doc_id))
    const next = new Set([...selected.value].filter((id) => alive.has(id)))
    if (next.size !== selected.value.size) selected.value = next
  },
)

async function onBatchRemove(): Promise<void> {
  const ids = [...selected.value]
  if (!ids.length) return
  try {
    await ElMessageBox.confirm(
      `确定删除选中的 ${ids.length} 份项目文档?其分块与索引会一并清除。`,
      '批量删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }

  const r = await store.removeDocs(ids)
  if (!r) {
    ElMessage.error(store.error ?? '批量删除失败')
    return
  }
  selected.value = new Set()
  if (r.not_found_ids.length) {
    // 后端对越权/不存在的 id 静默跳过并回显,这里如实告知
    ElMessage.warning(
      `已删除 ${r.deleted_ids.length} 份,${r.not_found_ids.length} 份被跳过(不存在或无权删除)`,
    )
  } else {
    ElMessage.success(`已删除 ${r.deleted_ids.length} 份`)
  }
}
</script>

<template>
  <div class="project-docs-panel" data-test="project-docs-panel">
    <el-upload
      drag
      :auto-upload="true"
      :show-file-list="false"
      :before-upload="beforeUpload"
      :http-request="httpRequest"
      :accept="extensions.join(',')"
      :disabled="store.uploading || store.atQuota"
      class="doc-uploader"
      data-test="doc-uploader"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">将项目文档拖到此处,或<em>点击上传</em></div>
      <template #tip>
        <div class="el-upload__tip">
          支持 {{ extensions.join(' / ') }} · 已用 {{ store.mineCount }} / {{ store.maxDocs }}
        </div>
      </template>
    </el-upload>

    <!-- 照模板写的文档能通过校验直接入库，不必经过大模型整理 -->
    <div class="template-tip" data-test="template-tip">
      <span>照模板填写的文档可直接入库、不会被整理；</span>
      <el-link
        type="primary"
        :underline="false"
        data-test="download-template"
        @click="onDownloadTemplate"
      >
        下载模板
      </el-link>
    </div>

    <!-- 预处理中：大模型整理需要几秒，必须给出明确反馈而不是静默等待 -->
    <div v-if="store.preprocessing" class="preprocessing" data-test="preprocessing">
      <el-skeleton :rows="3" animated />
      <span class="preprocessing-text" data-test="preprocessing-text">
        正在整理文档格式…
      </span>
    </div>

    <div v-if="store.upload" class="upload-progress" data-test="upload-progress">
      <el-progress
        :percentage="store.upload.percent"
        :status="
          store.upload.status === 'failed'
            ? 'exception'
            : store.upload.status === 'success'
              ? 'success'
              : undefined
        "
      />
      <span class="progress-text" data-test="progress-text">{{ store.upload.message }}</span>
      <span v-if="store.upload.error" class="progress-error">{{ store.upload.error }}</span>
    </div>

    <div class="doc-head">
      <el-checkbox
        v-if="selectableDocs.length"
        :model-value="allSelected"
        :indeterminate="indeterminate"
        :disabled="store.uploading"
        data-test="select-all"
        @update:model-value="toggleAll"
      >
        全选
      </el-checkbox>
      <span v-else>项目文档</span>
      <span class="doc-count" data-test="doc-count">{{ store.docs.length }}</span>
      <el-button
        v-if="selectedCount"
        type="danger"
        size="small"
        plain
        :loading="false"
        data-test="batch-delete"
        @click="onBatchRemove"
      >
        <el-icon><Delete /></el-icon>
        删除选中({{ selectedCount }})
      </el-button>
    </div>

    <el-empty
      v-if="!store.loading && !store.docs.length"
      description="还没有项目文档,上传后即可用于简历生成与知识问答"
      :image-size="72"
      data-test="empty"
    />

    <ul v-else class="doc-list" data-test="doc-list">
      <li v-for="doc in store.docs" :key="doc.doc_id" class="doc-item" :data-test="'doc-item'">
        <el-checkbox
          v-if="!doc.is_public"
          :model-value="selected.has(doc.doc_id)"
          :disabled="store.uploading"
          class="doc-check"
          data-test="doc-check"
          @update:model-value="(v: unknown) => toggleOne(doc.doc_id, Boolean(v))"
        />
        <span v-else class="doc-check doc-check--placeholder" />
        <el-icon class="doc-icon"><Document /></el-icon>
        <div class="doc-main">
          <div class="doc-name">
            {{ doc.project_name }}
            <el-tag v-if="doc.tags.includes(PRIORITY_TAG)" size="small" type="danger">
              {{ PRIORITY_TAG }}
            </el-tag>
            <el-tag v-if="doc.is_public" size="small" type="info">公共</el-tag>
          </div>
          <div class="doc-meta">
            <el-tooltip :content="tooltip(doc)" placement="top">
              <span class="meta-line" :data-test="'resume-section-' + doc.has_resume_section">
                <el-icon v-if="!doc.has_resume_section" class="warn"><WarningFilled /></el-icon>
                {{ doc.has_resume_section ? '已含简历描述' : '缺简历描述章节' }}
              </span>
            </el-tooltip>
          </div>
        </div>
        <el-button
          v-if="!doc.is_public"
          link
          type="danger"
          size="small"
          data-test="doc-delete"
          @click="onRemove(doc)"
        >
          删除
        </el-button>
      </li>
    </ul>

    <!-- 预处理预览：确认后才入库，避免系统在用户不知情时改写文档 -->
    <el-dialog
      v-model="previewVisible"
      title="确认文档内容"
      width="640px"
      :close-on-click-modal="false"
      data-test="preview-dialog"
    >
      <div v-if="store.preview" class="preview-body">
        <el-alert
          :type="store.preview.normalized ? 'success' : 'info'"
          :closable="false"
          show-icon
          class="preview-tip"
          data-test="preview-tip"
        >
          <template #title>
            <span v-if="store.preview.normalized">
              已按项目文档模板整理（仅重组与提炼，未新增内容）。请核对后确认入库。
            </span>
            <span v-else>
              符合项目文档模板，未做改动（{{ store.preview.reason }}）。
            </span>
          </template>
        </el-alert>

        <div v-if="!store.preview.has_resume_section" class="preview-warn" data-test="preview-warn">
          未识别到「简历描述」章节，简历生成将使用全文。可在下方补充该章节。
        </div>

        <div class="preview-meta">
          <span>项目：{{ store.preview.project_name }}</span>
          <el-checkbox v-model="showOriginal" size="small" data-test="toggle-original">
            对照原文
          </el-checkbox>
        </div>

        <el-input
          v-model="editableText"
          type="textarea"
          :rows="14"
          resize="vertical"
          data-test="preview-text"
        />

        <div v-if="showOriginal" class="preview-original" data-test="preview-original">
          <div class="original-head">你上传的原文</div>
          <pre>{{ store.preview.original_text }}</pre>
        </div>
      </div>

      <template #footer>
        <el-button data-test="preview-cancel" @click="previewVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="store.uploading"
          data-test="preview-confirm"
          @click="onConfirm"
        >
          确认入库
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.project-docs-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.doc-uploader {
  width: 100%;
}
.doc-uploader :deep(.el-upload-dragger) {
  border-radius: 10px;
  padding: 18px 12px;
}
.upload-progress {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.progress-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.template-tip {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 2px;
  margin-top: -4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.progress-error {
  font-size: 12px;
  color: var(--el-color-danger);
}
.doc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.doc-head :deep(.el-checkbox) {
  margin-right: 0;
}
/* 批量删除按钮靠右,与计数分隔开 */
.doc-head > .el-button {
  margin-left: auto;
}
.doc-check {
  flex: 0 0 auto;
  margin-right: 0;
  height: auto;
}
/* 公共素材无复选框,占位保持与可选项对齐 */
.doc-check--placeholder {
  display: inline-block;
  width: 14px;
}
.doc-count {
  color: var(--el-text-color-secondary);
  font-weight: 400;
}
.doc-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 46vh;
  overflow-y: auto;
}
.doc-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
}
.doc-icon {
  color: var(--el-color-primary);
  flex: 0 0 auto;
}
.doc-main {
  flex: 1;
  min-width: 0;
}
.doc-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.doc-meta {
  margin-top: 2px;
}
.meta-line {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  cursor: help;
}
.warn {
  color: var(--el-color-warning);
}
.preprocessing {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.preprocessing-text {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.preview-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.preview-tip :deep(.el-alert__title) {
  font-size: 13px;
  line-height: 1.5;
}
.preview-warn {
  font-size: 12px;
  color: var(--el-color-warning);
}
.preview-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.preview-original {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 10px;
  background: var(--el-fill-color-lighter);
}
.original-head {
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--el-text-color-secondary);
}
.preview-original pre {
  margin: 0;
  max-height: 180px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
