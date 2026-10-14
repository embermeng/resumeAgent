"""
API 层请求/响应数据模型

契约见 docs/specs/api-contract.md 第 2 节,与前端 frontend/src/types/events.ts 一一对应。
"""
from typing import List, Literal, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field

# 构建任务类型(对应现有 CLI 命令)
TaskKind = Literal[
    "build-all",
    "parse-pdfs",
    "extract-summaries",
    "split-chunks",
    "build-indexes",
    "ingest-projects",
]

# 任务状态机
TaskState = Literal["pending", "running", "success", "failed"]

# 意图类型(对齐 run_stream 的 intent 事件)
Intent = Literal["quick_response", "deep_thinking", "chitchat"]
# 消息角色
MessageRole = Literal["user", "assistant", "system"]


class ChatRequest(BaseModel):
    """流式对话请求(POST /api/chat)"""

    conversation_id: Optional[int] = Field(
        default=None, description="对话 ID,不传则自动生成"
    )
    prompt: str = Field(min_length=1, description="用户输入,非空")
    existing_resume: Optional[str] = Field(
        default=None, description="已有简历 Markdown 文本,深思路径生成时作为事实骨架"
    )


class ParseResponse(BaseModel):
    """简历文件解析结果(POST /api/resume/parse)"""

    filename: str = Field(description="原始文件名")
    content: str = Field(description="解析出的 Markdown 文本")


class SupportedExtensions(BaseModel):
    """支持的简历文件扩展名(GET /api/resume/supported-extensions)"""

    extensions: List[str] = Field(description="扩展名列表,含点号")


class BuildRequest(BaseModel):
    """知识库构建请求(POST /api/knowledge/build)"""

    task: TaskKind = Field(description="构建任务类型")
    force: bool = Field(default=False, description="强制全量重建")
    chunk_size: int = Field(default=300, description="分块大小(token)")
    chunk_overlap: int = Field(default=50, description="重叠 token 数")
    prune: bool = Field(default=False, description="清理孤儿索引")


class BuildAck(BaseModel):
    """构建任务受理响应(POST /api/knowledge/build)"""

    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="任务状态,受理时为 pending")


class TaskStatus(BaseModel):
    """任务状态快照(GET /api/knowledge/tasks/{task_id})"""

    task_id: str = Field(description="任务唯一 ID")
    task: TaskKind = Field(description="任务类型")
    status: TaskState = Field(description="任务状态")
    stage: Optional[str] = Field(default=None, description="当前阶段标识")
    percent: Optional[float] = Field(default=None, description="进度百分比 0-100")
    message: Optional[str] = Field(default=None, description="最新进度文本")
    created_at: float = Field(description="创建时间(epoch 秒)")
    finished_at: Optional[float] = Field(
        default=None, description="结束时间(epoch 秒)")
    error: Optional[str] = Field(default=None, description="失败原因")


class TaskList(BaseModel):
    """任务列表(GET /api/knowledge/tasks)"""

    tasks: List[TaskStatus] = Field(description="任务状态列表")
    total: int = Field(description="任务总数")


class ConversationSummary(BaseModel):
    """单个对话摘要"""

    id: int = Field(description="对话 ID")
    title: str = Field(description="对话标题")
    created_at: float = Field(description="创建时间(epoch 秒)")
    updated_at: float = Field(description="更新时间(epoch 秒)")


class ConversationList(BaseModel):
    """对话列表(GET /api/conversations)"""

    conversations: List[ConversationSummary] = Field(description="对话摘要列表")
    total: int = Field(description="对话总数")


class MessageOut(BaseModel):
    """对话消息"""

    id: int = Field(description="消息 ID")
    role: MessageRole = Field(description="消息角色")
    content: str = Field(description="消息内容")
    intent: Optional[Intent] = Field(
        default=None, description="消息意图(仅 assistant 消息有值)")
    created_at: float = Field(description="创建时间(epoch 秒)")


class ConversationMessages(BaseModel):
    """对话消息列表 GET /api/conversations/{id}/messages"""

    conversation_id: int = Field(description="对话 ID")
    messages: List[MessageOut] = Field(description="消息列表")


class UserBase(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email: EmailStr = Field(max_length=120)


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserLoginReq(BaseModel):
    email: EmailStr = Field(max_length=120)
    password: str = Field(min_length=8)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str


class UserPrivate(UserPublic):
    email: EmailStr
    # 前端据此控制管理端入口(/admin、知识库构建)的显隐
    is_superuser: bool = False


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=120)


class Token(BaseModel):
    access_token: str
    token_type: str


class ProjectUploadAck(BaseModel):
    """项目文档上传任务受理(POST /api/project/upload, 202)"""
    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="受理时为 pending")


class ProjectUploadStatus(BaseModel):
    """项目文档上传任务状态(GET /api/project/upload/{task_id})"""
    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="任务状态")
    stage: Optional[str] = Field(default=None)
    percent: Optional[float] = Field(default=None)
    message: Optional[str] = Field(default=None)
    filename: Optional[str] = Field(default=None, description="上传的原始文件名")
    error: Optional[str] = Field(default=None)
    created_at: float = Field(description="创建时间(epoch 秒)")
    finished_at: Optional[float] = Field(default=None)


class ProjectDocOut(BaseModel):
    """项目文档条目(GET /api/project/docs)"""
    doc_id: str = Field(description="文档 ID")
    project_name: str = Field(description="项目名")
    file_name: str = Field(description="源文件名")
    tags: List[str] = Field(default_factory=list, description="标签,如「简历优先」")
    has_resume_section: bool = Field(
        description="是否含「简历描述」章节;false 时简历生成降级为全文引用")
    is_public: bool = Field(description="true 表示公共素材,所有用户可见")
    created_at: float = Field(description="创建时间(epoch 秒)")
    updated_at: float = Field(description="更新时间(epoch 秒)")


class ProjectDocList(BaseModel):
    """项目文档列表(GET /api/project/docs)"""
    docs: List[ProjectDocOut] = Field(default_factory=list)
    total: int = Field(description="条目数")
    max_docs: int = Field(description="该用户文档数上限")


class ProjectPreprocessOut(BaseModel):
    """
    预处理结果（POST /api/project/preprocess）。
    前端据此弹预览：展示系统整理后的文本，用户确认（可再编辑）后才入库。
    """
    preview_id: str = Field(description="暂存 ID，确认时回传")
    project_name: str = Field(description="解析出的项目名")
    file_name: str = Field(description="用户上传时的原始文件名")
    original_text: str = Field(description="用户原始文本（清洗后）")
    normalized_text: str = Field(description="整理后的文本（最终采用）")
    normalized: bool = Field(description="是否经过大模型整理")
    reason: str = Field(description="未整理/回退的原因，便于前端提示")
    has_resume_section: bool = Field(
        description="整理结果是否含「简历描述」章节；false 时简历生成降级用全文")


class ProjectConfirmIn(BaseModel):
    """确认入库请求体（POST /api/project/confirm）"""
    preview_id: str = Field(description="preprocess 返回的暂存 ID")
    text: Optional[str] = Field(
        default=None, description="用户在预览中编辑后的最终文本；不传则用整理结果")


class ProjectDocBatchDeleteIn(BaseModel):
    """批量删除请求体(POST /api/project/docs/batch-delete)"""
    doc_ids: List[str] = Field(
        min_length=1, max_length=100, description="要删除的文档 ID 列表(自动去重)"
    )


class ProjectDocBatchDeleteResult(BaseModel):
    """批量删除结果"""
    deleted_ids: List[str] = Field(default_factory=list, description="实际删除的文档 ID")
    not_found_ids: List[str] = Field(
        default_factory=list, description="不存在或无权删除而被跳过的文档 ID"
    )


class ProjectTemplateOut(BaseModel):
    """
    标准模板（GET /api/project/template）。

    内容与入库校验基准、LLM 下发的标准格式同源（同一份 md 文件），
    保证"照着模板写"的文档能直接通过校验，不必经过大模型整理。
    """
    file_name: str = Field(description="建议的下载文件名")
    content: str = Field(description="模板 Markdown 原文")


class ResumeParseAck(BaseModel):
    """简历解析任务受理(POST /api/resume/parse, 202)"""
    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="受理时为 pending")


class ResumeParseStatus(BaseModel):
    """简历解析任务状态/结果(GET /api/resume/parse/{task_id})。不含 task 字段。"""
    task_id: str = Field(description="任务唯一 ID")
    status: TaskState = Field(description="任务状态")
    stage: Optional[str] = Field(default=None)
    percent: Optional[float] = Field(default=None)
    message: Optional[str] = Field(default=None)
    filename: Optional[str] = Field(default=None, description="上传的原始文件名")
    content: Optional[str] = Field(default=None, description="解析出的 Markdown,仅 success 返回")
    error: Optional[str] = Field(default=None)
    created_at: float = Field(description="创建时间(epoch 秒)")
    finished_at: Optional[float] = Field(default=None)


class ResumeCurrent(BaseModel):
    """
    我当前的简历(GET /api/resume/current)。每个用户只有一份,
    新的解析成功后旧的由 worker 清理,删除走 DELETE /api/resume/current,因此不带 task_id。
    """
    filename: Optional[str] = Field(default=None, description="上传时的原始文件名")
    created_at: float = Field(description="任务创建时间(epoch 秒)")
    finished_at: Optional[float] = Field(default=None)
    content: Optional[str] = Field(
        default=None,
        description="解析出的 Markdown,前端据此自动带入生成上下文;产物文件丢失时为 null",
    )