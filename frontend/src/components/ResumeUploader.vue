<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as api from '@/api/client'
import { useTaskStream } from '@/composables/useTaskStream'
import type { ResumeParseStatus, TaskEvent } from '@/types/events'

const props = withDefaults(
  defineProps<{
    /** 允许的扩展名白名单,默认取契约值 */
    extensions?: string[]
    disabled?: boolean
  }>(),
  {
    extensions: () => ['.md', '.txt', '.docx', '.pdf'],
    disabled: false,
  },
)

const emit = defineEmits<{
  (e: 'parsed', payload: { filename: string; content: string }): void
  (e: 'error', message: string): void
}>()

/**
 * 异步解析流程(契约 4.3~4.5):
 *   上传 → POST /resume/parse 拿 task_id(202) → SSE 订阅进度(排队/解析中/百分比)
 *   → done(success) 后 GET /resume/parse/{id} 取 content → emit('parsed')。
 * SSE 若意外中断未给终态,则轮询 getResumeParseStatus 兜底。
 */
type Phase = 'idle' | 'queued' | 'parsing' | 'success' | 'failed'
const loading = ref(false)
const phase = ref<Phase>('idle')
const percent = ref(0)
const statusMessage = ref('')
// SSE 终态与错误(done 帧不带 content,故 success 后仍需查快照取内容)
let terminalStatus: 'success' | 'failed' | null = null
let lastError: string | null = null

const resumeStreamUrl = (id: string) => `/api/resume/parse/${encodeURIComponent(id)}/stream`

function handleTaskEvent(evt: TaskEvent): void {
  if (evt.type === 'progress') {
    phase.value = evt.stage === 'queued' ? 'queued' : 'parsing'
    percent.value = evt.percent
    statusMessage.value = evt.message
  } else if (evt.type === 'error') {
    lastError = evt.message
  } else if (evt.type === 'done') {
    terminalStatus = evt.status
  }
}

const stream = useTaskStream({ url: resumeStreamUrl, onEvent: handleTaskEvent })

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 用快照刷新进度 UI(轮询兜底路径复用) */
function applyStatus(st: ResumeParseStatus): void {
  if (typeof st.percent === 'number') percent.value = st.percent
  if (st.message) statusMessage.value = st.message
  if (st.stage) phase.value = st.stage === 'queued' ? 'queued' : 'parsing'
}

/** 轮询直到终态(SSE 未给终态时兜底);拿到 success/failed 即返回,否则 null */
async function pollUntilTerminal(
  taskId: string,
  attempts = 60,
  intervalMs = 1000,
): Promise<ResumeParseStatus | null> {
  for (let i = 0; i < attempts; i++) {
    let st: ResumeParseStatus
    try {
      st = await api.getResumeParseStatus(taskId)
    } catch {
      return null // 404/网络错误 → 放弃兜底,交由上层报错
    }
    applyStatus(st)
    if (st.status === 'success' || st.status === 'failed') return st
    await sleep(intervalMs)
  }
  return null
}

function succeed(name: string, content: string): void {
  phase.value = 'success'
  percent.value = 100
  emit('parsed', { filename: name, content })
  ElMessage.success(`已解析: ${name}`)
}

function fail(msg: string): void {
  phase.value = 'failed'
  emit('error', msg)
  ElMessage.error(msg)
}

/** 流结束后结算:有终态取快照拿 content;无终态则轮询兜底 */
async function resolveResult(taskId: string, fileName: string): Promise<void> {
  if (terminalStatus === 'success') {
    try {
      const st = await api.getResumeParseStatus(taskId)
      succeed(st.filename ?? fileName, st.content ?? '')
    } catch (e) {
      fail(e instanceof Error ? e.message : String(e))
    }
    return
  }
  if (terminalStatus === 'failed') {
    fail(lastError ?? '解析失败')
    return
  }
  // SSE 意外中断(未收到 done)→ 轮询兜底
  const st = await pollUntilTerminal(taskId)
  if (st?.status === 'success') succeed(st.filename ?? fileName, st.content ?? '')
  else if (st?.status === 'failed') fail(st.error ?? lastError ?? '解析失败')
  else fail(lastError ?? '解析进度中断,请重试')
}

/** 扩展名白名单校验(大小写不敏感) */
function isSupported(name: string): boolean {
  const lower = name.toLowerCase()
  return props.extensions.some((ext) => lower.endsWith(ext.toLowerCase()))
}

/** el-upload before-upload:不支持的扩展名直接拦截 */
function beforeUpload(file: File): boolean {
  if (!isSupported(file.name)) {
    const msg = `不支持的文件格式: ${file.name}(仅支持 ${props.extensions.join(', ')})`
    emit('error', msg)
    ElMessage.error(msg)
    return false
  }
  return true
}

/** el-upload 自定义上传:提交后台解析任务并订阅进度 */
async function httpRequest(options: UploadRequestOptions): Promise<void> {
  const file = options.file as File
  loading.value = true
  phase.value = 'queued'
  percent.value = 0
  statusMessage.value = '提交中…'
  terminalStatus = null
  lastError = null
  try {
    const ack = await api.parseResume(file)
    statusMessage.value = '已提交,等待解析槽位…'
    await stream.start(ack.task_id) // SSE 进度;流结束(含 done)后返回
    await resolveResult(ack.task_id, file.name)
  } catch (e) {
    // parseResume 直接失败(如 400 不支持格式 / 401 未登录 / 网络)
    fail(e instanceof Error ? e.message : String(e))
  } finally {
    loading.value = false
  }
}

defineExpose({ isSupported, beforeUpload, httpRequest, phase, percent, statusMessage })
</script>

<template>
  <div class="resume-uploader-wrap">
    <el-upload
      drag
      :auto-upload="true"
      :show-file-list="false"
      :before-upload="beforeUpload"
      :http-request="httpRequest"
      :accept="extensions.join(',')"
      :disabled="disabled || loading"
      class="resume-uploader"
      data-test="uploader"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">将简历拖到此处,或<em>点击上传</em></div>
      <template #tip>
        <div class="el-upload__tip">支持 {{ extensions.join(' / ') }}</div>
      </template>
    </el-upload>

    <div v-if="loading || phase === 'success' || phase === 'failed'" class="upload-progress" data-test="progress">
      <el-progress
        :percentage="percent"
        :status="phase === 'failed' ? 'exception' : phase === 'success' ? 'success' : undefined"
      />
      <span class="progress-text" data-test="progress-text">{{ statusMessage }}</span>
    </div>
  </div>
</template>

<style scoped>
.resume-uploader {
  width: 100%;
}
.resume-uploader :deep(.el-upload-dragger) {
  border-radius: 12px;
  border: 1px dashed var(--el-border-color);
  padding: 28px 16px;
  transition: all 0.2s;
}
.resume-uploader :deep(.el-upload-dragger:hover) {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.resume-uploader :deep(.el-icon--upload) {
  color: var(--el-color-primary);
}
.upload-progress {
  margin-top: 10px;
}
.progress-text {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
