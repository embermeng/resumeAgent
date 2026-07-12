"""
schemas 数据模型单元测试
"""
import json
import pytest
from src.schemas.knowledge import KnowledgeChunk, KnowledgeDocument
from src.schemas.project import ProjectExtract, ProjectInResume
from src.schemas.resume import Resume, EducationItem


class TestKnowledgeChunk:
    """知识块模型测试"""

    def test_create_valid_chunk(self):
        chunk = KnowledgeChunk(
            chunk_id="test_001",
            source="RAG课程",
            category="course",
            content="RAG的核心流程...",
            metadata={"page": 1},
        )
        assert chunk.chunk_id == "test_001"
        assert chunk.category == "course"

    def test_invalid_category(self):
        with pytest.raises(Exception):
            KnowledgeChunk(
                chunk_id="test_002",
                source="test",
                category="invalid_category",
                content="content",
            )

    def test_default_metadata(self):
        chunk = KnowledgeChunk(
            chunk_id="test_003",
            source="test",
            category="project",
            content="content",
        )
        assert chunk.metadata == {}

    def test_json_serialization(self):
        chunk = KnowledgeChunk(
            chunk_id="test_004",
            source="test",
            category="interview",
            content="面试题内容",
            metadata={"difficulty": "hard"},
        )
        json_str = chunk.model_dump_json()
        restored = KnowledgeChunk.model_validate_json(json_str)
        assert restored.chunk_id == chunk.chunk_id
        assert restored.metadata["difficulty"] == "hard"


class TestKnowledgeDocument:
    """知识文档模型测试"""

    def test_create_document_with_chunks(self):
        chunk = KnowledgeChunk(
            chunk_id="c1", source="test", category="course", content="内容"
        )
        doc = KnowledgeDocument(
            doc_id="doc_001",
            source="RAG课程",
            category="course",
            chunks=[chunk],
        )
        assert len(doc.chunks) == 1
        assert doc.chunks[0].chunk_id == "c1"

    def test_empty_chunks(self):
        doc = KnowledgeDocument(
            doc_id="doc_002", source="test", category="project"
        )
        assert doc.chunks == []


class TestProjectExtract:
    """项目精华模型测试"""

    def test_create_valid_project(self):
        proj = ProjectExtract(
            project_name="RAG知识库系统",
            description="基于RAG的企业知识库问答系统",
            tech_stack=["Python", "LangChain", "FAISS"],
            highlights=["混合检索", "LLM重排序"],
            role_contribution="独立完成开发",
            key_metrics="检索准确率提升30%",
        )
        assert proj.project_name == "RAG知识库系统"
        assert len(proj.tech_stack) == 3
        assert len(proj.highlights) == 2

    def test_required_fields(self):
        with pytest.raises(Exception):
            ProjectExtract(
                project_name="test",
                # 缺少 description, tech_stack, highlights
            )

    def test_default_optional_fields(self):
        proj = ProjectExtract(
            project_name="test",
            description="desc",
            tech_stack=["Python"],
            highlights=["h1"],
        )
        assert proj.role_contribution == ""
        assert proj.key_metrics == ""

    def test_json_roundtrip(self):
        proj = ProjectExtract(
            project_name="test_proj",
            description="测试项目",
            tech_stack=["Python", "Pytest"],
            highlights=["亮点1"],
        )
        json_str = proj.model_dump_json()
        restored = ProjectExtract.model_validate_json(json_str)
        assert restored.project_name == "test_proj"
        assert restored.tech_stack == ["Python", "Pytest"]


class TestProjectInResume:
    """简历项目条目测试"""

    def test_create_valid(self):
        proj = ProjectInResume(
            project_name="RAG系统",
            description="企业级知识库",
            tech_stack=["Python"],
            highlights=["亮点"],
            role="开发者",
        )
        assert proj.project_name == "RAG系统"


class TestResume:
    """简历模型测试"""

    def _make_sample_resume(self):
        return Resume(
            name="张三",
            title="AI Agent开发工程师",
            summary="前端转AI，具备RAG和Agent开发经验",
            skills=["Python", "LangChain", "LangGraph", "RAG"],
            projects=[
                ProjectInResume(
                    project_name="RAG知识库",
                    description="企业知识库系统",
                    tech_stack=["Python", "FAISS"],
                    highlights=["混合检索"],
                    role="独立开发",
                )
            ],
            education=[
                EducationItem(
                    school="某大学",
                    degree="本科",
                    major="计算机科学",
                    start_date="2018-09",
                    end_date="2022-06",
                )
            ],
            additional="英语CET-6",
        )

    def test_create_valid_resume(self):
        resume = self._make_sample_resume()
        assert resume.name == "张三"
        assert len(resume.skills) == 4
        assert len(resume.projects) == 1

    def test_to_markdown(self):
        resume = self._make_sample_resume()
        md = resume.to_markdown()
        assert "# 张三" in md
        assert "AI Agent开发工程师" in md
        assert "## 专业技能" in md
        assert "Python" in md
        assert "## 项目经历" in md
        assert "RAG知识库" in md
        assert "## 教育背景" in md
        assert "某大学" in md
        assert "英语CET-6" in md

    def test_to_markdown_minimal(self):
        resume = Resume(
            name="李四",
            title="开发工程师",
            summary="简介",
            skills=["Python"],
        )
        md = resume.to_markdown()
        assert "# 李四" in md
        assert "## 项目经历" not in md  # 没有项目时不显示该section

    def test_json_serialization(self):
        resume = self._make_sample_resume()
        json_str = resume.model_dump_json()
        restored = Resume.model_validate_json(json_str)
        assert restored.name == resume.name
        assert len(restored.projects) == 1


class TestEducationItem:
    """教育经历模型测试"""

    def test_create_valid(self):
        edu = EducationItem(
            school="某大学",
            degree="硕士",
            major="人工智能",
        )
        assert edu.school == "某大学"
        assert edu.start_date == ""  # 默认空
