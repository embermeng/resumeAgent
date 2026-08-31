"""
项目相关数据模型
"""
from pydantic import BaseModel, Field
from typing import List


class ProjectInResume(BaseModel):
    """简历中的项目经历条目"""
    project_name: str = Field(description="项目名称")
    description: str = Field(description="项目简述")
    tech_stack: List[str] = Field(description="使用的技术栈")
    highlights: List[str] = Field(description="项目亮点（简历措辞版本）")
    role: str = Field(default="", description="担任角色")


class IntroSelectionPlan(BaseModel):
    """项目介绍挑选结果（从介绍目录中按岗位匹配度选出的项目名列表）"""
    selected_projects: List[str] = Field(description="选中的项目名列表，须与目录中项目名完全一致")
