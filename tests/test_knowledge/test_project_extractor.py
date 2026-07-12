"""
ProjectExtractor 测试
Mock LLM API调用，测试项目提炼逻辑
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.knowledge.project_extractor import ProjectExtractor, SUPPORTED_CODE_EXTENSIONS
from src.schemas.project import ProjectExtract


@pytest.fixture
def mock_api():
    """Mock APIProcessor"""
    with patch("src.knowledge.project_extractor.APIProcessor") as mock:
        instance = MagicMock()
        instance.default_model = "test-model"
        mock.return_value = instance
        yield instance


@pytest.fixture
def extractor(mock_api):
    return ProjectExtractor(provider="dashscope")


@pytest.fixture
def sample_project_dir(tmp_path):
    """创建模拟项目目录"""
    project = tmp_path / "test_project"
    project.mkdir()
    (project / "README.md").write_text("# Test Project\n\nA test project for unit testing.", encoding="utf-8")
    (project / "main.py").write_text("def main():\n    print('Hello')\n\nif __name__ == '__main__':\n    main()\n", encoding="utf-8")
    (project / "requirements.txt").write_text("flask==3.0\nrequests==2.31\n", encoding="utf-8")
    # 创建子目录
    src = project / "src"
    src.mkdir()
    (src / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    # 创建应被忽略的目录
    pycache = project / "__pycache__"
    pycache.mkdir()
    (pycache / "main.cpython-311.pyc").write_text("bytecode", encoding="utf-8")
    return project


class TestCollectProjectFiles:
    def test_collect_files(self, extractor, sample_project_dir):
        files = extractor.collect_project_files(sample_project_dir)
        file_names = [f.name for f in files]
        assert "README.md" in file_names
        assert "main.py" in file_names
        assert "requirements.txt" in file_names
        assert "utils.py" in file_names
        # __pycache__ 应被忽略
        assert "main.cpython-311.pyc" not in file_names

    def test_collect_files_priority(self, extractor, sample_project_dir):
        files = extractor.collect_project_files(sample_project_dir)
        # README.md 和 requirements.txt 应在前面
        first_two = [f.name for f in files[:2]]
        assert "README.md" in first_two or "requirements.txt" in first_two

    def test_collect_files_max_limit(self, extractor, sample_project_dir):
        files = extractor.collect_project_files(sample_project_dir, max_files=2)
        assert len(files) == 2


class TestBuildProjectContext:
    def test_build_context(self, extractor, sample_project_dir):
        context = extractor.build_project_context(sample_project_dir)
        assert "Test Project" in context
        assert "main.py" in context or "def main" in context
        assert len(context) > 0

    def test_build_context_truncation(self, extractor, tmp_path):
        project = tmp_path / "big_project"
        project.mkdir()
        # 创建超大文件
        (project / "big.py").write_text("x = 1\n" * 100000, encoding="utf-8")

        extractor.max_file_content_chars = 1000
        context = extractor.build_project_context(project)
        assert "内容已截断" in context


class TestExtractFromText:
    def test_extract_success(self, extractor, mock_api):
        """测试LLM成功返回结构化数据"""
        mock_api.send_message.return_value = {
            "project_name": "TestProject",
            "description": "A test project",
            "tech_stack": ["Python", "Flask"],
            "highlights": ["Fast API", "Clean code"],
            "role_contribution": "Lead developer",
            "key_metrics": "99% uptime",
        }

        result = extractor.extract_from_text("some project code", "TestProject")
        assert isinstance(result, ProjectExtract)
        assert result.project_name == "TestProject"
        assert "Python" in result.tech_stack

    def test_extract_with_parse_error(self, extractor, mock_api):
        """测试LLM返回解析错误时降级"""
        mock_api.send_message.return_value = {
            "content": "raw text",
            "parse_error": "JSON decode failed",
        }

        result = extractor.extract_from_text("some code", "FallbackProject")
        assert result.project_name == "FallbackProject"

    def test_extract_api_exception(self, extractor, mock_api):
        """测试API异常时降级"""
        mock_api.send_message.side_effect = Exception("API Error")

        result = extractor.extract_from_text("some code", "ErrorProject")
        assert result.project_name == "ErrorProject"
        assert result.tech_stack == []
        assert result.highlights == []


class TestExtractFromDirectory:
    def test_extract_from_dir(self, extractor, mock_api, sample_project_dir):
        mock_api.send_message.return_value = {
            "project_name": "test_project",
            "description": "A test project",
            "tech_stack": ["Python"],
            "highlights": ["Unit tested"],
        }

        result = extractor.extract_from_directory(sample_project_dir)
        assert isinstance(result, ProjectExtract)
        assert result.project_name == "test_project"

    def test_extract_empty_dir(self, extractor, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        result = extractor.extract_from_directory(empty_dir)
        assert result.project_name == "empty"
        assert result.description == "项目信息提取失败，请手动补充。"


class TestExtractBatch:
    def test_batch_extract(self, extractor, mock_api, tmp_path):
        """测试批量提炼"""
        # 创建两个项目目录
        for name in ["project_a", "project_b"]:
            p = tmp_path / name
            p.mkdir()
            (p / "main.py").write_text(f"# {name}\nprint('hello')", encoding="utf-8")

        mock_api.send_message.return_value = {
            "project_name": "mock_project",
            "description": "Mocked",
            "tech_stack": ["Python"],
            "highlights": ["Tested"],
        }

        results = extractor.extract_batch(tmp_path)
        assert len(results) == 2

    def test_batch_extract_with_output(self, extractor, mock_api, tmp_path):
        """测试批量提炼并保存"""
        projects_dir = tmp_path / "projects"
        output_dir = tmp_path / "extracts"
        projects_dir.mkdir()

        p = projects_dir / "my_project"
        p.mkdir()
        (p / "app.py").write_text("# app code", encoding="utf-8")

        mock_api.send_message.return_value = {
            "project_name": "MyProject",
            "description": "Test",
            "tech_stack": ["Python"],
            "highlights": ["Good"],
        }

        results = extractor.extract_batch(projects_dir, output_dir=output_dir)
        assert len(results) == 1
        json_files = list(output_dir.glob("*.json"))
        assert len(json_files) == 1


class TestFallbackExtract:
    def test_fallback(self):
        result = ProjectExtractor._fallback_extract("TestName")
        assert result.project_name == "TestName"
        assert result.tech_stack == []
        assert result.highlights == []
        assert "失败" in result.description

    def test_fallback_no_name(self):
        result = ProjectExtractor._fallback_extract("")
        assert result.project_name == "Unknown Project"
