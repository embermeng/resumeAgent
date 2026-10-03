"""
PDFParser 测试
使用Mock避免依赖真实的MinerU库（GPU模型加载）
"""
import contextlib
import hashlib
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from src.knowledge.pdf_parser import PDFParser


def _doc_id(source: str) -> str:
    return hashlib.md5(source.encode()).hexdigest()[:16]


@pytest.fixture
def parser(tmp_path):
    return PDFParser(output_dir=tmp_path / "output")


def _fake_mineru_output(output_dir: Path, stem: str, markdown: str, parse_method: str = "auto"):
    """按MinerU的输出结构写入模拟的Markdown文件"""
    md_dir = output_dir / stem / parse_method
    md_dir.mkdir(parents=True, exist_ok=True)
    (md_dir / f"{stem}.md").write_text(markdown, encoding="utf-8")


def _make_mock_run_mineru(contents: dict):
    """构造_run_mineru的mock，contents: {stem: markdown}"""
    def mock_run_mineru(self, output_dir, pdf_paths):
        for pdf_path in pdf_paths:
            _fake_mineru_output(output_dir, pdf_path.stem, contents[pdf_path.stem])
    return mock_run_mineru


class TestPDFParserInit:
    def test_init_default(self):
        p = PDFParser()
        assert p.output_dir is None
        assert p.num_threads is None
        assert p.backend == "pipeline"
        assert p.parse_method == "auto"
        assert p.lang == "ch"

    def test_init_with_output_dir(self, tmp_path):
        p = PDFParser(output_dir=tmp_path)
        assert p.output_dir == tmp_path

    def test_init_with_threads(self):
        p = PDFParser(num_threads=4)
        assert p.num_threads == 4


class TestCleanMarkdown:
    def test_remove_image_refs(self):
        md = "# 标题\n\n![](images/abc.jpg)\n\n正文内容"
        cleaned = PDFParser._clean_markdown(md)
        assert "![" not in cleaned
        assert "正文内容" in cleaned

    def test_keep_normal_text(self):
        md = "# 标题\n\n普通文本"
        assert PDFParser._clean_markdown(md) == md


class TestParseSingle:
    """parse_single 走 MinerU Agent 轻量 API（免 token），mock 四步 HTTP 流程"""

    @staticmethod
    def _patch_agent_flow(markdown="# 测试内容\n\n这是解析后的Markdown"):
        """构造四个 _agent_* 静态方法的 patch，模拟创建/上传/轮询/下载全流程"""
        return [
            patch.object(PDFParser, "_agent_create_task",
                         staticmethod(lambda requests, cfg, pdf_path: ("task-1", "http://upload/url"))),
            patch.object(PDFParser, "_agent_upload",
                         staticmethod(lambda requests, cfg, file_url, pdf_path: None)),
            patch.object(PDFParser, "_agent_poll",
                         staticmethod(lambda requests, cfg, task_id: "http://cdn/result.md")),
            patch.object(PDFParser, "_agent_download",
                         staticmethod(lambda requests, cfg, markdown_url: markdown)),
        ]

    def test_parse_single_success(self, parser):
        """测试单文件解析成功"""
        with contextlib.ExitStack() as stack:
            for p in self._patch_agent_flow("# 测试内容\n\n这是解析后的Markdown"):
                stack.enter_context(p)
            result = parser.parse_single(Path("test.pdf"))
        assert "测试内容" in result

    def test_parse_single_failure(self, parser):
        """测试解析失败抛出异常"""
        def raise_error(requests, cfg, pdf_path):
            raise RuntimeError("model error")

        with patch.object(PDFParser, "_agent_create_task", staticmethod(raise_error)):
            with pytest.raises(ValueError, match="PDF解析失败"):
                parser.parse_single(Path("test.pdf"))

    def test_parse_single_empty_result(self, parser):
        """测试解析结果为空抛出异常"""
        with contextlib.ExitStack() as stack:
            for p in self._patch_agent_flow("   \n  "):
                stack.enter_context(p)
            with pytest.raises(RuntimeError, match="PDF解析结果为空"):
                parser.parse_single(Path("test.pdf"))


class TestParseBatch:
    def test_parse_batch_empty_dir(self, parser, tmp_path):
        """测试空目录"""
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()

        result = parser.parse_batch(pdf_dir)
        assert result == []

    def test_parse_batch_with_files(self, parser, tmp_path):
        """测试批量解析"""
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        (pdf_dir / "test1.pdf").touch()
        (pdf_dir / "test2.pdf").touch()

        with patch.object(
            PDFParser, "_run_mineru",
            _make_mock_run_mineru({"test1": "# 内容1", "test2": "# 内容2"}),
        ):
            result = parser.parse_batch(pdf_dir)

        assert len(result) == 2
        assert result[0]["source"] == "test1"
        assert result[0]["file_name"] == "test1.pdf"
        assert result[1]["source"] == "test2"

    def test_parse_batch_partial_failure(self, parser, tmp_path):
        """测试部分文件解析失败时跳过失败文件"""
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        (pdf_dir / "good.pdf").touch()
        (pdf_dir / "bad.pdf").touch()

        with patch.object(
            PDFParser, "_run_mineru",
            _make_mock_run_mineru({"good": "# 正常内容"}),  # bad.pdf无输出
        ):
            result = parser.parse_batch(pdf_dir)

        assert len(result) == 1
        assert result[0]["source"] == "good"


class TestParseAndExportJson:
    def test_export_json_structure(self, parser, tmp_path):
        """测试导出JSON结构正确"""
        pdf_dir = tmp_path / "pdfs"
        output_dir = tmp_path / "output"
        pdf_dir.mkdir()
        (pdf_dir / "course1.pdf").touch()

        with patch.object(
            PDFParser, "_run_mineru",
            _make_mock_run_mineru({"course1": "# 课程内容"}),
        ):
            parser.parse_and_export_json(pdf_dir, output_dir, category="course")

        json_files = list(output_dir.glob("*.json"))
        assert len(json_files) == 1

        with open(json_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "metainfo" in data
        assert "content" in data
        assert data["metainfo"]["category"] == "course"
        assert data["metainfo"]["source"] == "course1"
        assert "markdown" in data["content"]

    def test_export_json_incremental_skip_existing(self, parser, tmp_path):
        """增量解析：已存在结果的PDF被跳过"""
        pdf_dir = tmp_path / "pdfs"
        output_dir = tmp_path / "output"
        pdf_dir.mkdir()
        output_dir.mkdir()
        (pdf_dir / "old.pdf").touch()
        (pdf_dir / "new.pdf").touch()

        # old.pdf的解析结果已存在
        (output_dir / f"{_doc_id('old')}.json").write_text("{}", encoding="utf-8")

        parsed_stems = []

        def mock_run_mineru(self, output_dir, pdf_paths):
            for pdf_path in pdf_paths:
                parsed_stems.append(pdf_path.stem)
                _fake_mineru_output(output_dir, pdf_path.stem, "# 内容")

        with patch.object(PDFParser, "_run_mineru", mock_run_mineru):
            results = parser.parse_and_export_json(pdf_dir, output_dir)

        assert parsed_stems == ["new"]  # 只解析new.pdf
        assert len(results) == 1
        assert (output_dir / f"{_doc_id('new')}.json").exists()

    def test_export_json_incremental_all_parsed(self, parser, tmp_path):
        """增量解析：全部已解析时直接返回空列表"""
        pdf_dir = tmp_path / "pdfs"
        output_dir = tmp_path / "output"
        pdf_dir.mkdir()
        output_dir.mkdir()
        (pdf_dir / "old.pdf").touch()
        (output_dir / f"{_doc_id('old')}.json").write_text("{}", encoding="utf-8")

        with patch.object(PDFParser, "_run_mineru") as mock_run:
            results = parser.parse_and_export_json(pdf_dir, output_dir)

        assert results == []
        mock_run.assert_not_called()

    def test_export_json_force_reparse_all(self, parser, tmp_path):
        """force=True时全量重新解析"""
        pdf_dir = tmp_path / "pdfs"
        output_dir = tmp_path / "output"
        pdf_dir.mkdir()
        output_dir.mkdir()
        (pdf_dir / "old.pdf").touch()
        (output_dir / f"{_doc_id('old')}.json").write_text("{}", encoding="utf-8")

        parsed_stems = []

        def mock_run_mineru(self, output_dir, pdf_paths):
            for pdf_path in pdf_paths:
                parsed_stems.append(pdf_path.stem)
                _fake_mineru_output(output_dir, pdf_path.stem, "# 内容")

        with patch.object(PDFParser, "_run_mineru", mock_run_mineru):
            results = parser.parse_and_export_json(pdf_dir, output_dir, force=True)

        assert parsed_stems == ["old"]  # force时不跳过
        assert len(results) == 1
