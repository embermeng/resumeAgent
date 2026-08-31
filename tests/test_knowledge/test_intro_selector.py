"""
项目介绍文档扫描模块测试
覆盖：扫描解析、摘要提取、标签、重名跳过、目录构建
"""
import pytest

from src.knowledge.intro_selector import IntroSelector, PRIORITY_TAG


INTRO_A = """# 项目Alpha

> 标签：简历优先

## 一句话简介
基于RAG的企业财报问答系统。

## 技术栈
Python, FAISS
"""

INTRO_B = """# 项目Beta

## 一句话简介
Agent自主规划三模式实战。

## 项目描述
金融场景递进实现三种Agent架构。
"""

INTRO_NO_SUMMARY = """# 项目Gamma

这里是正文首段，没有简介章节时退化使用它。

## 技术栈
LangGraph
"""


@pytest.fixture
def selector():
    return IntroSelector()


class TestScan:
    def test_scan_parses_entries(self, tmp_path, selector):
        (tmp_path / "a.md").write_text(INTRO_A, encoding="utf-8")
        (tmp_path / "b.md").write_text(INTRO_B, encoding="utf-8")

        entries = selector.scan(tmp_path)
        assert [e["name"] for e in entries] == ["项目Alpha", "项目Beta"]
        alpha = entries[0]
        assert PRIORITY_TAG in alpha["tags"]
        assert alpha["summary"] == "基于RAG的企业财报问答系统。"
        # 全文已剥离标签行
        assert "标签" not in alpha["text"]
        assert "## 技术栈" in alpha["text"]

    def test_scan_empty_or_missing_dir(self, tmp_path, selector):
        assert selector.scan(tmp_path / "not_exist") == []
        assert selector.scan(tmp_path) == []

    def test_scan_skips_doc_without_h1(self, tmp_path, selector):
        (tmp_path / "bad.md").write_text("没有一级标题", encoding="utf-8")
        assert selector.scan(tmp_path) == []

    def test_scan_skips_duplicate_names(self, tmp_path, selector):
        (tmp_path / "a.md").write_text("# 同名\n\n## 一句话简介\n第一份", encoding="utf-8")
        (tmp_path / "b.md").write_text("# 同名\n\n## 一句话简介\n第二份", encoding="utf-8")
        entries = selector.scan(tmp_path)
        assert len(entries) == 1
        assert entries[0]["summary"] == "第一份"


class TestExtractSummary:
    def test_summary_from_first_paragraph_after_h1(self, selector):
        entries_summary = IntroSelector._extract_summary(INTRO_NO_SUMMARY)
        assert entries_summary.startswith("这里是正文首段")

    def test_summary_strips_inner_newline(self, selector):
        text = "# P\n\n## 一句话简介\n第一行\n第二行续写。\n"
        assert IntroSelector._extract_summary(text) == "第一行 第二行续写。"


class TestBuildCatalog:
    def test_catalog_lines_with_priority_mark(self, tmp_path, selector):
        (tmp_path / "a.md").write_text(INTRO_A, encoding="utf-8")
        (tmp_path / "b.md").write_text(INTRO_B, encoding="utf-8")
        entries = selector.scan(tmp_path)

        catalog = selector.build_catalog(entries)
        assert "项目Alpha [简历优先]" in catalog
        assert "项目Beta —— Agent自主规划三模式实战。" in catalog
