"""
API 层请求/响应数据模型

契约见 docs/specs/api-contract.md 第 2 节,与前端 frontend/src/types/events.ts 一一对应。
"""
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

# 构建任务类型(对应现有 CLI 命令)
TaskKind = Literal[
    "build-all",
    "parse-pdfs",
    "extract-summaries",
    "split-chunks",
    "build-indexes",
    "ingest-highlights",
]

# 任务状态机
TaskState = Literal["pending", "running", "success", "failed"]

# 意图类型(对齐 run_stream 的 intent 事件)
Intent = Literal["quick_response", "deep_thinking", "chitchat"]


class ChatRequest(BaseModel):
    """流式对话请求(POST /api/chat)"""

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
    finished_at: Optional[float] = Field(default=None, description="结束时间(epoch 秒)")
    error: Optional[str] = Field(default=None, description="失败原因")


class TaskList(BaseModel):
    """任务列表(GET /api/knowledge/tasks)"""

    tasks: List[TaskStatus] = Field(description="任务状态列表")
    total: int = Field(description="任务总数")
