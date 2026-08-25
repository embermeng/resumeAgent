"""
TextSplitter 测试
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.knowledge.text_splitter import TextSplitter


@pytest.fixture
def splitter():
    return TextSplitter()


@pytest.fixture
def sample_text():
    return "这是一段测试文本。" * 100  # 约200字符


@pytest.fixture
def sample_md_file(tmp_path):
    md_path = tmp_path / "test.md"
    md_path.write_text("# 标题\n\n" + "测试内容。\n" * 200, encoding="utf-8")
    return md_path


class TestCountTokens:
    def test_count_tokens_non_empty(self, splitter):
        tokens = splitter.count_tokens("Hello world")
        assert tokens > 0

    def test_count_tokens_empty(self, splitter):
        tokens = splitter.count_tokens("")
        assert tokens == 0

    def test_count_tokens_chinese(self, splitter):
        tokens = splitter.count_tokens("你好世界，这是一个测试。")
        assert tokens > 0


class TestSplitText:
    def test_split_text_returns_list(self, splitter, sample_text):
        chunks = splitter.split_text(sample_text, chunk_size=50, chunk_overlap=10)
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    def test_split_text_chunk_structure(self, splitter, sample_text):
        chunks = splitter.split_text(sample_text, chunk_size=50, chunk_overlap=10)
        for chunk in chunks:
            assert "text" in chunk
            assert "length_tokens" in chunk
            assert isinstance(chunk["text"], str)
            assert isinstance(chunk["length_tokens"], int)
            assert len(chunk["text"]) > 0

    def test_split_text_respects_chunk_size(self, splitter, sample_text):
        chunks = splitter.split_text(sample_text, chunk_size=50, chunk_overlap=10)
        # 每个chunk的token数不应远超chunk_size（允许一定误差）
        for chunk in chunks:
            assert chunk["length_tokens"] <= 100  # 合理上限

    def test_split_text_empty_input(self, splitter):
        chunks = splitter.split_text("", chunk_size=50, chunk_overlap=10)
        assert chunks == []

    def test_split_text_with_special_tokens(self, splitter):
        """文本中包含特殊token字面量（如课件中的tokenizer示例）不应报错"""
        text = "GPT-2的结束token是<|endoftext|>，填充token是<|padding|>。" * 100
        chunks = splitter.split_text(text, chunk_size=50, chunk_overlap=10)
        assert len(chunks) > 0

    def test_count_tokens_with_special_tokens(self, splitter):
        """count_tokens遇到特殊token字面量不报错"""
        tokens = splitter.count_tokens("示例<|endoftext|>文本")
        assert tokens > 0


class TestSplitMarkdownFile:
    def test_split_markdown_file(self, splitter, sample_md_file):
        chunks = splitter.split_markdown_file(sample_md_file, chunk_size=50, chunk_overlap=10)
        assert isinstance(chunks, list)
        assert len(chunks) > 0

    def test_split_markdown_file_nonexistent(self, splitter, tmp_path):
        with pytest.raises(FileNotFoundError):
            splitter.split_markdown_file(tmp_path / "nonexistent.md")


class TestSplitAndSave:
    def test_split_and_save_creates_json(self, splitter, tmp_path):
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        # 创建测试md文件
        (input_dir / "test1.md").write_text("# 测试1\n\n内容A" * 50, encoding="utf-8")
        (input_dir / "test2.md").write_text("# 测试2\n\n内容B" * 50, encoding="utf-8")

        splitter.split_and_save(input_dir, output_dir, category="course", chunk_size=50)

        json_files = list(output_dir.glob("*.json"))
        assert len(json_files) == 2

        # 验证JSON结构
        with open(json_files[0], "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "metainfo" in data
        assert "content" in data
        assert "chunks" in data["content"]
        assert data["metainfo"]["category"] == "course"

    def test_split_and_save_empty_dir(self, splitter, tmp_path):
        input_dir = tmp_path / "empty"
        output_dir = tmp_path / "output"
        input_dir.mkdir()

        splitter.split_and_save(input_dir, output_dir)
        assert len(list(output_dir.glob("*.json"))) == 0

    def test_split_and_save_incremental_skip(self, splitter, tmp_path):
        """增量分块：已存在结果的文档被跳过"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        output_dir.mkdir()
        (input_dir / "test1.md").write_text("# 测试1\n\n内容A" * 50, encoding="utf-8")

        # 第一次全量分块
        splitter.split_and_save(input_dir, output_dir, chunk_size=50)
        json_file = list(output_dir.glob("*.json"))[0]
        first_mtime = json_file.stat().st_mtime

        # 新增一个md文件后第二次运行：test1应被跳过，只分块test2
        (input_dir / "test2.md").write_text("# 测试2\n\n内容B" * 50, encoding="utf-8")
        splitter.split_and_save(input_dir, output_dir, chunk_size=50)

        assert len(list(output_dir.glob("*.json"))) == 2
        # test1的输出未被重写
        assert json_file.stat().st_mtime == first_mtime

    def test_split_and_save_force_rechunk(self, splitter, tmp_path):
        """force=True时全量重新分块"""
        input_dir = tmp_path / "input"
        output_dir = tmp_path / "output"
        input_dir.mkdir()
        (input_dir / "test1.md").write_text("# 测试1\n\n内容A" * 50, encoding="utf-8")

        with patch.object(TextSplitter, "split_markdown_file", wraps=splitter.split_markdown_file) as mock_split:
            splitter.split_and_save(input_dir, output_dir, chunk_size=50)
            splitter.split_and_save(input_dir, output_dir, chunk_size=50)  # 增量：不应再分块
            assert mock_split.call_count == 1

            splitter.split_and_save(input_dir, output_dir, chunk_size=50, force=True)
            assert mock_split.call_count == 2  # force：重新分块


class TestSplitTextDirect:
    def test_split_text_direct(self, splitter):
        result = splitter.split_text_direct("测试文本" * 50, source="test_source", category="project")
        assert "metainfo" in result
        assert "content" in result
        assert result["metainfo"]["source"] == "test_source"
        assert result["metainfo"]["category"] == "project"
        assert len(result["content"]["chunks"]) > 0

    def test_split_text_direct_doc_id_deterministic(self, splitter):
        r1 = splitter.split_text_direct("text", source="same_source")
        r2 = splitter.split_text_direct("text", source="same_source")
        assert r1["metainfo"]["doc_id"] == r2["metainfo"]["doc_id"]

    def test_split_text_direct_different_sources(self, splitter):
        r1 = splitter.split_text_direct("text", source="source_a")
        r2 = splitter.split_text_direct("text", source="source_b")
        assert r1["metainfo"]["doc_id"] != r2["metainfo"]["doc_id"]
