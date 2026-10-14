import { defineStore } from 'pinia'
import * as api from '@/api/client'
import { useTaskStream } from '@/composables/useTaskStream'
import type {
  ProjectDocBatchDeleteResult,
  ProjectDocOut,
  ProjectPreprocessOut,
  TaskEvent,
} from '@/types/events'

/** SSE 进度阶段文案(后端 stage 为英文,此处做展示层映射) */
const STAGE_TEXT: Record<string, string> = {
  queued: '排队中…',
  parsing: '解析文档…',
  saving: '写入分块…',
  indexing: '构建向量索引…',
}

export interface UploadProgress {
  taskId: string
  filename: string
  percent: number
  message: string
  status: 'pending' | 'running' | 'success' | 'failed'
  error?: string
}

/**
 * 项目文档 store。
 *
 * 与简历上传(临时上下文)不同,项目文档是**长期资产**:落在服务端、跨会话保留、
 * 有配额。检索侧由后端 chat 开流前懒同步(sync_project_docs),所以这里上传成功后
 * 只需刷新列表,不必通知对话页。
 */
export const useProjectStore = defineStore('project', {
  state: () => ({
    /** 我的项目文档列表(公共素材 + 我上传的) */
    docs: [] as ProjectDocOut[],
    /** 文档数上限(配额) */
    maxDocs: 0,
    loading: false,
    /** 当前上传任务(同时只允许一个,避免配额竞态) */
    upload: null as UploadProgress | null,
    /** 预处理结果,等待用户在预览弹窗确认 */
    preview: null as ProjectPreprocessOut | null,
    /** 预处理中(上传后到预览出现之前的 loading) */
    preprocessing: false,
    error: null as string | null,
  }),
  getters: {
    /** 我上传的文档数(不含公共素材) */
    mineCount(state): number {
      return state.docs.filter((d) => !d.is_public).length
    },
    /** 是否已满额 */
    atQuota(state): boolean {
      const mine = state.docs.filter((d) => !d.is_public).length
      return state.maxDocs > 0 && mine >= state.maxDocs
    },
    uploading(state): boolean {
      return state.upload?.status === 'pending' || state.upload?.status === 'running'
    },
    /** 缺「简历描述」章节的文档(简历生成会降级用全文) */
    missingResumeSection(state): ProjectDocOut[] {
      return state.docs.filter((d) => !d.has_resume_section)
    },
  },
  actions: {
    /** 拉取列表;失败时清空而非抛错(侧边栏入口不阻塞主流程) */
    async loadDocs(): Promise<void> {
      this.loading = true
      this.error = null
      try {
        const r = await api.listProjectDocs()
        this.docs = r.docs
        this.maxDocs = r.max_docs
      } catch (e) {
        this.docs = []
        this.maxDocs = 0
        this.error = e instanceof Error ? e.message : String(e)
      } finally {
        this.loading = false
      }
    },

    /**
     * 订阅某入库任务的 SSE 进度并结算(直接上传与预览确认共用)。
     *
     * 注意必须通过 `this.upload` 修改进度——不要把局部普通对象传进来改：
     * Pinia 赋值后 store 内是 reactive 代理，改局部变量指向的原始对象
     * 不会触发依赖更新，进度条会卡在旧值直到别处碰巧触发重渲染。
     */
    async _awaitIngest(taskId: string): Promise<boolean> {
      let terminal: 'success' | 'failed' | null = null
      let lastError: string | null = null

      const { start } = useTaskStream({
        url: (id) => `/api/project/upload/${encodeURIComponent(id)}/stream`,
        onEvent: (evt: TaskEvent) => {
          const p = this.upload
          if (!p) return
          if (evt.type === 'progress') {
            p.status = 'running'
            p.percent = evt.percent ?? 0
            p.message = STAGE_TEXT[evt.stage ?? ''] ?? evt.message ?? '处理中…'
          } else if (evt.type === 'error') {
            lastError = evt.message
          } else if (evt.type === 'done') {
            terminal = evt.status
          }
        },
      })

      await start(taskId)

      const p = this.upload
      if (!p) return false

      if (terminal === 'failed') {
        p.status = 'failed'
        p.percent = 100
        p.message = '入库失败'
        p.error = lastError ?? '入库失败'
        this.error = p.error
        return false
      }
      // 终态缺失(SSE 意外中断)时以快照为准兜底一次
      if (terminal === null) {
        const st = await api.getProjectUploadStatus(taskId)
        if (st.status === 'failed') {
          p.status = 'failed'
          p.percent = 100
          p.message = '入库失败'
          p.error = st.error ?? '入库失败'
          this.error = p.error
          return false
        }
      }
      // 终态描述以 done 帧结果为准,不停留在最后一个 progress 帧
      // (单文档入库很快,常只见首帧"排队中"+done,此前会卡在"排队中")
      p.status = 'success'
      p.percent = 100
      p.message = '入库完成'
      await this.loadDocs()
      return true
    },

    /** 直接上传并入库(不经预览的兼容路径) */
    async uploadDoc(file: File): Promise<boolean> {
      this.upload = {
        taskId: '',
        filename: file.name,
        percent: 0,
        message: '提交中…',
        status: 'pending',
      }
      this.error = null

      try {
        const ack = await api.uploadProjectDoc(file)
        if (this.upload) this.upload.taskId = ack.task_id
        return await this._awaitIngest(ack.task_id)
      } catch (e) {
        if (this.upload) {
          this.upload.status = 'failed'
          this.upload.error = e instanceof Error ? e.message : String(e)
        }
        this.error = this.upload?.error ?? '上传失败'
        return false
      }
    },

    /**
     * 第 1 步：上传并预处理。返回整理结果供用户预览，**尚未入库**。
     * 失败时返回 null 并把原因写进 error。
     */
    async preprocessDoc(file: File): Promise<ProjectPreprocessOut | null> {
      this.preprocessing = true
      this.error = null
      try {
        const r = await api.preprocessProjectDoc(file)
        this.preview = r
        return r
      } catch (e) {
        this.preview = null
        this.error = e instanceof Error ? e.message : String(e)
        return null
      } finally {
        this.preprocessing = false
      }
    },

    /**
     * 第 2 步：预览确认后入库。text 为用户在预览里编辑过的内容（不传则用整理结果）。
     */
    async confirmDoc(text?: string): Promise<boolean> {
      if (!this.preview) return false
      const { preview_id: previewId, file_name: filename } = this.preview

      this.upload = {
        taskId: '',
        filename,
        percent: 0,
        message: '提交中…',
        status: 'pending',
      }
      this.error = null

      try {
        const ack = await api.confirmProjectDoc(previewId, text)
        if (this.upload) this.upload.taskId = ack.task_id
        const ok = await this._awaitIngest(ack.task_id)
        if (ok) this.preview = null // 成功后清掉预览,避免重复确认
        return ok
      } catch (e) {
        if (this.upload) {
          this.upload.status = 'failed'
          this.upload.message = '入库失败'
          this.upload.error = e instanceof Error ? e.message : String(e)
        }
        this.error = this.upload?.error ?? '入库失败'
        return false
      }
    },

    /** 放弃本次预览 */
    clearPreview(): void {
      this.preview = null
    },

    /** 删除文档(公共素材后端会 404,前端也不给入口) */
    async removeDoc(docId: string): Promise<boolean> {
      try {
        await api.deleteProjectDoc(docId)
        this.docs = this.docs.filter((d) => d.doc_id !== docId)
        return true
      } catch (e) {
        this.error = e instanceof Error ? e.message : String(e)
        return false
      }
    },

    /**
     * 批量删除。返回删除结果与被跳过的 id。
     * 后端对越权/不存在的 id 静默跳过并回显,不抛错——故这里只在网络/5xx 时抛错。
     */
    async removeDocs(docIds: string[]): Promise<ProjectDocBatchDeleteResult | null> {
      if (!docIds.length) return null
      try {
        const r = await api.batchDeleteProjectDocs(docIds)
        const gone = new Set(r.deleted_ids)
        // 只移除真正删掉的;被跳过的留在列表里,由调用方提示
        this.docs = this.docs.filter((d) => !gone.has(d.doc_id))
        return r
      } catch (e) {
        this.error = e instanceof Error ? e.message : String(e)
        return null
      }
    },

    /** 供调用方(如抽屉关闭)清理上传态 */
    clearUpload() {
      this.upload = null
    },

    reset() {
      this.docs = []
      this.maxDocs = 0
      this.upload = null
      this.error = null
      this.loading = false
    },
  },
})
