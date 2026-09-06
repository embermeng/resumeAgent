<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import * as api from '@/api/client'

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

const loading = ref(false)

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

/** el-upload 自定义上传:交给后端解析为 Markdown */
async function httpRequest(options: UploadRequestOptions): Promise<void> {
  const file = options.file as File
  loading.value = true
  try {
    const res = await api.parseResume(file)
    emit('parsed', { filename: res.filename, content: res.content })
    ElMessage.success(`已解析: ${res.filename}`)
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    emit('error', msg)
    ElMessage.error(msg)
  } finally {
    loading.value = false
  }
}

defineExpose({ isSupported, beforeUpload, httpRequest })
</script>

<template>
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
</template>

<style scoped>
.resume-uploader {
  width: 100%;
}
</style>
