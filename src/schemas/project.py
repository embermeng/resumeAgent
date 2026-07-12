"""
项目精华数据模型
"""
from pydantic import BaseModel, ConfigDict, Field
from typing import List


class ProjectExtract(BaseModel):
    """从项目源码/文档中提炼的结构化信息"""
    project_name: str = Field(description="项目名称")
    description: str = Field(description="项目描述（2-3句话）")
    tech_stack: List[str] = Field(description="技术栈列表")
    highlights: List[str] = Field(description="项目亮点（技术亮点、优化手段等）")
    role_contribution: str = Field(default="", description="个人角色与贡献")
    key_metrics: str = Field(default="", description="可量化的成果指标（如有）")

    model_config = ConfigDict(json_schema_extra={
            "example": {
                "project_name": "企业知识库RAG系统",
                "description": "基于RAG技术构建的企业级知识库问答系统，支持PDF解析、向量化检索和智能问答。",
                "tech_stack": ["Python", "LangChain", "FAISS", "DashScope", "Streamlit"],
                "highlights": [
                    "实现了混合检索（向量+BM25）提升召回率",
                    "支持LLM重排序优化检索结果相关性",
                    "模块化Pipeline设计，支持灵活配置",
                ],
                "role_contribution": "独立完成系统架构设计与核心模块开发",
                "key_metrics": "检索准确率提升30%，支持5+种文档格式",
            }
        })


class ProjectInResume(BaseModel):
    """简历中的项目经历条目"""
    project_name: str = Field(description="项目名称")
    description: str = Field(description="项目简述")
    tech_stack: List[str] = Field(description="使用的技术栈")
    highlights: List[str] = Field(description="项目亮点（简历措辞版本）")
    role: str = Field(default="", description="担任角色")
