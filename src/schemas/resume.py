"""
简历输出数据模型
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from src.schemas.project import ProjectInResume


class EducationItem(BaseModel):
    """教育经历"""
    school: str = Field(description="学校名称")
    degree: str = Field(description="学位")
    major: str = Field(description="专业")
    start_date: str = Field(default="", description="开始时间")
    end_date: str = Field(default="", description="结束时间")


class Resume(BaseModel):
    """完整简历"""
    name: str = Field(description="姓名")
    title: str = Field(description="求职意向")
    summary: str = Field(description="个人简介")
    skills: List[str] = Field(description="技能列表")
    projects: List[ProjectInResume] = Field(default_factory=list, description="项目经历")
    education: List[EducationItem] = Field(default_factory=list, description="教育背景")
    additional: str = Field(default="", description="其他信息（证书、语言等）")

    def to_markdown(self) -> str:
        """导出为Markdown格式"""
        lines = []
        lines.append(f"# {self.name}")
        lines.append(f"**{self.title}**\n")

        # 个人简介
        lines.append("## 个人简介")
        lines.append(f"{self.summary}\n")

        # 技能
        lines.append("## 专业技能")
        for skill in self.skills:
            lines.append(f"- {skill}")
        lines.append("")

        # 项目经历
        if self.projects:
            lines.append("## 项目经历")
            for proj in self.projects:
                lines.append(f"### {proj.project_name}")
                lines.append(f"{proj.description}")
                lines.append(f"**技术栈**: {', '.join(proj.tech_stack)}")
                if proj.role:
                    lines.append(f"**角色**: {proj.role}")
                lines.append("**项目亮点**:")
                for h in proj.highlights:
                    lines.append(f"- {h}")
                lines.append("")

        # 教育背景
        if self.education:
            lines.append("## 教育背景")
            for edu in self.education:
                period = f"{edu.start_date} - {edu.end_date}" if edu.start_date else ""
                lines.append(f"- **{edu.school}** | {edu.degree} | {edu.major} {period}")
            lines.append("")

        # 其他
        if self.additional:
            lines.append("## 其他")
            lines.append(f"{self.additional}")

        return "\n".join(lines)
