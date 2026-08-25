"""
知识条目数据模型
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import Dict, Any, Literal, List


class KnowledgeChunk(BaseModel):
    """知识库中的单个文本块"""
    chunk_id: str = Field(description="唯一标识符")
    source: str = Field(description="来源课程名或文件名")
    category: Literal["course", "project", "interview"] = Field(description="知识分类")
    content: str = Field(description="文本内容")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="元数据（页码、章节等）")

    model_config = ConfigDict(json_schema_extra={
            "example": {
                "chunk_id": "course_001_chunk_0",
                "source": "RAG技术与应用",
                "category": "course",
                "content": "RAG的核心流程包括：文档解析、文本分块、向量化、检索、生成...",
                "metadata": {"page": 1, "chapter": "RAG基础"},
            }
        })


class KnowledgeDocument(BaseModel):
    """知识文档（包含多个chunk）"""
    doc_id: str = Field(description="文档唯一标识")
    source: str = Field(description="来源")
    category: Literal["course", "project", "interview"] = Field(description="知识分类")
    chunks: List[KnowledgeChunk] = Field(default_factory=list, description="文本块列表")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="文档级元数据")


class CourseSummary(BaseModel):
    """课程结构化摘要（分层检索第一层：供LLM浏览目录选点）"""
    course_name: str = Field(description="课程名称")
    one_line_intro: str = Field(description="一句话课程定位")
    key_points: List[str] = Field(
        description="适合作为简历技能点的核心知识点，5-10条，每条含技术名词与能解决的问题",
        min_length=1,
    )
    tech_keywords: List[str] = Field(default_factory=list, description="技术关键词列表")


class DocSelection(BaseModel):
    """单个课程的选中知识点"""
    doc_id: str = Field(description="课程文档ID")
    selected_points: List[str] = Field(description="选中的知识点（将作为检索查询词）", min_length=1)


class SelectionPlan(BaseModel):
    """简历知识点选择计划（分层检索第二层的输入）"""
    selections: List[DocSelection] = Field(default_factory=list, description="选中的课程及其知识点")
    reason: str = Field(default="", description="选择理由简述")
