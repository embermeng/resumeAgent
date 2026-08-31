"""
项目亮点文档入库模块测试
覆盖：清洗、项目名提取、doc_id、按标题分块、格式校验、增量、重名检测
"""
import json
import pytest

from src.knowledge.highlight_ingestor import HighlightIngestor


SAMPLE_MD = """# ResumeAgent - 简历生成Agent

## 一句话简介
基于LangGraph的RAG知识库与简历生成Agent。

## 技术栈
LangGraph、FAISS、BM25

## 技术亮点
**增量构建**：新增文档只重建该文档索引。

## 检索关键词
RAG, LangGraph, 混合检索
"""


@pytest.fixture
def ingestor():
    return HighlightIngestor()


class TestCleanAndParse:
    def test_clean_strips_fence_wrapper(self, ingestor):
        wrapped = "```markdown\n# 项目A\n\n## 简介\n内容\n```"
        cleaned = ingestor.clean_markdown(wrapped)
        assert cleaned.startswith("# 项目A")
        assert "```" not in cleaned

    def test_clean_keeps_normal_doc(self, ingestor):
        assert ingestor.clean_markdown(SAMPLE_MD).startswith("# ResumeAgent")

    def test_extract_project_name_from_h1(self, ingestor):
        assert ingestor.extract_project_name(SAMPLE_MD) == "ResumeAgent - 简历生成Agent"

    def test_extract_project_name_missing_h1(self, ingestor):
        assert ingestor.extract_project_name("## 只有二级标题") == ""

    def test_doc_id_has_project_prefix(self, ingestor):
        doc_id = ingestor.make_doc_id("ResumeAgent")
        assert doc_id.startswith("project-")
        # 同名项目doc_id稳定（幂等）
        assert doc_id == ingestor.make_doc_id("ResumeAgent")
        # 不同项目不冲突
        assert doc_id != ingestor.make_doc_id("OpenManus")


class TestSplitByHeadings:
    def test_split_per_h2_section(self, ingestor):
        chunks = ingestor.split_by_headings(SAMPLE_MD, "ResumeAgent")
        assert len(chunks) == 4
        # 每块都带项目名前缀
        for c in chunks:
            assert c.startswith("【项目：ResumeAgent】")

    def test_chunk_contains_section_title_and_body(self, ingestor):
        chunks = ingestor.split_by_headings(SAMPLE_MD, "ResumeAgent")
        tech_chunk = [c for c in chunks if "技术栈" in c]
        assert len(tech_chunk) == 1
        assert "LangGraph、FAISS、BM25" in tech_chunk[0]

    def test_empty_section_skipped(self, ingestor):
        text = "# P\n\n## 空章节\n\n## 有内容\n正文"
        chunks = ingestor.split_by_headings(text, "P")
        assert len(chunks) == 1
        assert "有内容" in chunks[0]

    def test_long_section_subdivided_with_title(self, ingestor):
        long_body = "很长的技术描述内容。" * 300
        text = f"# P\n\n## 技术亮点\n{long_body}"
        chunks = ingestor.split_by_headings(text, "P")
        assert len(chunks) > 1
        # 细分块同时带项目名与章节名，保证可溯源
        for c in chunks:
            assert "【项目：P｜技术亮点】" in c

    def test_no_h2_fallback_whole_doc(self, ingestor):
        text = "# P\n\n没有章节结构的纯文本内容"
        chunks = ingestor.split_by_headings(text, "P")
        assert len(chunks) >= 1
        assert chunks[0].startswith("【项目：P】")


class TestBuildDocData:
    def test_schema_matches_knowledge_base_format(self, ingestor):
        doc = ingestor.build_doc_data(SAMPLE_MD, "ResumeAgent", "ResumeAgent.md", 1000.0)
        meta = doc["metainfo"]
        assert meta["doc_id"].startswith("project-")
        assert meta["source"] == "ResumeAgent"
        assert meta["category"] == "project"
        assert meta["file_name"] == "ResumeAgent.md"
        assert meta["mtime"] == 1000.0
        chunks = doc["content"]["chunks"]
        assert len(chunks) == 4
        assert all(set(c.keys()) == {"id", "text", "length_tokens"} for c in chunks)
        assert [c["id"] for c in chunks] == [0, 1, 2, 3]


class TestIngest:
    def test_ingest_creates_chunk_json(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        (hl_dir / "ResumeAgent.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        stats = ingestor.ingest(hl_dir, chunks_dir)

        assert stats == {"total": 1, "ingested": 1, "skipped": 0, "failed": 0, "duplicate": 0}
        doc_id = ingestor.make_doc_id("ResumeAgent - 简历生成Agent")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["category"] == "project"
        assert len(out["content"]["chunks"]) == 4

    def test_incremental_skip_unchanged(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        md = hl_dir / "A.md"
        md.write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        ingestor.ingest(hl_dir, chunks_dir)
        stats = ingestor.ingest(hl_dir, chunks_dir)
        assert stats["skipped"] == 1 and stats["ingested"] == 0

    def test_rebuild_when_source_updated(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        md = hl_dir / "A.md"
        md.write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"
        ingestor.ingest(hl_dir, chunks_dir)

        # 更新源文件（mtime更晚）并改写内容
        import os
        md.write_text(SAMPLE_MD + "\n## 新章节\n新内容", encoding="utf-8")
        os.utime(md, (md.stat().st_mtime + 10, md.stat().st_mtime + 10))

        stats = ingestor.ingest(hl_dir, chunks_dir)
        assert stats["ingested"] == 1
        doc_id = ingestor.make_doc_id("ResumeAgent - 简历生成Agent")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert len(out["content"]["chunks"]) == 5

    def test_force_rebuild(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        (hl_dir / "A.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"
        ingestor.ingest(hl_dir, chunks_dir)

        stats = ingestor.ingest(hl_dir, chunks_dir, force=True)
        assert stats["ingested"] == 1 and stats["skipped"] == 0

    def test_no_h1_skipped_as_failed(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        (hl_dir / "bad.md").write_text("没有一级标题的文档", encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        stats = ingestor.ingest(hl_dir, chunks_dir)
        assert stats["failed"] == 1 and stats["ingested"] == 0
        assert not list(chunks_dir.glob("*.json"))

    def test_duplicate_project_name_skipped(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        # 两份文件一级标题同名
        (hl_dir / "a.md").write_text("# 同名项目\n\n## 简介\n第一份", encoding="utf-8")
        (hl_dir / "b.md").write_text("# 同名项目\n\n## 简介\n第二份", encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        stats = ingestor.ingest(hl_dir, chunks_dir)
        assert stats["duplicate"] == 1 and stats["ingested"] == 1
        # 先入库的（a.md按文件名排序在前）内容保留
        doc_id = ingestor.make_doc_id("同名项目")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["file_name"] == "a.md"

    def test_atomic_write_no_tmp_leftover(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        (hl_dir / "A.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        ingestor.ingest(hl_dir, chunks_dir)
        assert not list(chunks_dir.glob("*.tmp"))


TAGGED_MD = """# 重点项目A

> 标签：简历优先、核心项目

## 一句话简介
重点项目内容。
"""


class TestTags:
    def test_extract_tags_with_quote_mark(self, ingestor):
        tags, text = ingestor.extract_tags(TAGGED_MD)
        assert tags == ["简历优先", "核心项目"]
        # 标签行从正文中移除，不污染分块
        assert "标签" not in text
        assert text.startswith("# 重点项目A")
        assert "重点项目内容" in text

    def test_extract_tags_plain_line(self, ingestor):
        tags, text = ingestor.extract_tags("# P\n\n标签：简历优先\n\n## A\n正文")
        assert tags == ["简历优先"]
        assert "标签" not in text

    def test_tag_after_first_h2_ignored(self, ingestor):
        text = "# P\n\n## A\n正文\n\n标签：不该被识别"
        tags, out = ingestor.extract_tags(text)
        assert tags == []
        assert out == text

    def test_no_tags_returns_original(self, ingestor):
        tags, out = ingestor.extract_tags(SAMPLE_MD)
        assert tags == []
        assert out == SAMPLE_MD

    def test_build_doc_data_tags_in_metainfo(self, ingestor):
        doc = ingestor.build_doc_data(SAMPLE_MD, "P", "P.md", 1.0, tags=["简历优先"])
        assert doc["metainfo"]["tags"] == ["简历优先"]

    def test_build_doc_data_default_empty_tags(self, ingestor):
        doc = ingestor.build_doc_data(SAMPLE_MD, "P", "P.md", 1.0)
        assert doc["metainfo"]["tags"] == []

    def test_ingest_persists_tags_and_strips_from_chunks(self, tmp_path, ingestor):
        hl_dir = tmp_path / "highlights"
        hl_dir.mkdir()
        (hl_dir / "A.md").write_text(TAGGED_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        ingestor.ingest(hl_dir, chunks_dir)
        doc_id = ingestor.make_doc_id("重点项目A")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["tags"] == ["简历优先", "核心项目"]
        # 分块正文不含标签行内容
        for c in out["content"]["chunks"]:
            assert "简历优先" not in c["text"]
