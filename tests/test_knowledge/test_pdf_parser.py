"""
PDFParser 测试
使用Mock避免依赖真实的Docling库
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.knowledge.pdf_parser import PDFParser


@pytest.fixture
def parser(tmp_path):
    return PDFParser(output_dir=tmp_path / "output")


@pytest.fixture
def mock_converter():
    """创建一个模拟的DocumentConverter"""
    converter = MagicMock()
    return converter


class TestPDFParserInit:
    def test_init_default(self):
        p = PDFParser()
        assert p.output_dir is None
        assert p.num_threads is None
        assert p._doc_converter is None

    def test_init_with_output_dir(self, tmp_path):
        p = PDFParser(output_dir=tmp_path)
        assert p.output_dir == tmp_path

    def test_init_with_threads(self):
        p = PDFParser(num_threads=4)
        assert p.num_threads == 4


class TestParseSingle:
    def test_parse_single_success(self, parser, mock_converter):
        """测试单文件解析成功"""
        mock_result = MagicMock()
        mock_result.status = "SUCCESS"  # 匹配 ConversionStatus.SUCCESS (mocked as "SUCCESS")
        mock_result.document.export_to_markdown.return_value = "# 测试内容\n\n这是解析后的Markdown"
        mock_converter.convert_all.return_value = [mock_result]
        parser._doc_converter = mock_converter

        result = parser.parse_single(Path("test.pdf"))
        assert "测试内容" in result

    def test_parse_single_failure(self, parser, mock_converter):
        """测试解析失败抛出异常"""
        mock_result = MagicMock()
        mock_result.status = "FAILURE"  # 不匹配 SUCCESS
        mock_converter.convert_all.return_value = [mock_result]
        parser._doc_converter = mock_converter

        with pytest.raises(RuntimeError, match="PDF解析失败"):
            parser.parse_single(Path("test.pdf"))


class TestParseBatch:
    def test_parse_batch_empty_dir(self, parser, mock_converter, tmp_path):
        """测试空目录"""
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        parser._doc_converter = mock_converter

        result = parser.parse_batch(pdf_dir)
        assert result == []

    def test_parse_batch_with_files(self, parser, mock_converter, tmp_path):
        """测试批量解析"""
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        (pdf_dir / "test1.pdf").touch()
        (pdf_dir / "test2.pdf").touch()

        mock_result1 = MagicMock()
        mock_result1.status = "SUCCESS"
        mock_result1.input.file.stem = "test1"
        mock_result1.input.file.name = "test1.pdf"
        mock_result1.document.export_to_markdown.return_value = "# 内容1"

        mock_result2 = MagicMock()
        mock_result2.status = "SUCCESS"
        mock_result2.input.file.stem = "test2"
        mock_result2.input.file.name = "test2.pdf"
        mock_result2.document.export_to_markdown.return_value = "# 内容2"

        mock_converter.convert_all.return_value = [mock_result1, mock_result2]
        parser._doc_converter = mock_converter

        result = parser.parse_batch(pdf_dir)
        assert len(result) == 2
        assert result[0]["source"] == "test1"
        assert result[1]["source"] == "test2"


class TestParseAndExportJson:
    def test_export_json_structure(self, parser, mock_converter, tmp_path):
        """测试导出JSON结构正确"""
        pdf_dir = tmp_path / "pdfs"
        output_dir = tmp_path / "output"
        pdf_dir.mkdir()
        (pdf_dir / "course1.pdf").touch()

        mock_result = MagicMock()
        mock_result.status = "SUCCESS"
        mock_result.input.file.stem = "course1"
        mock_result.input.file.name = "course1.pdf"
        mock_result.document.export_to_markdown.return_value = "# 课程内容"

        mock_converter.convert_all.return_value = [mock_result]
        parser._doc_converter = mock_converter

        parser.parse_and_export_json(pdf_dir, output_dir, category="course")

        json_files = list(output_dir.glob("*.json"))
        assert len(json_files) == 1

        with open(json_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "metainfo" in data
        assert "content" in data
        assert data["metainfo"]["category"] == "course"
        assert "markdown" in data["content"]
