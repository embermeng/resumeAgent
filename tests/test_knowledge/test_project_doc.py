"""
项目文档模块测试（原「项目亮点入库」+「项目介绍扫描」合并）
覆盖：清洗/项目名/doc_id、按标题分块、格式校验、增量、重名检测、标签、
      简历描述章节提取与降级、素材拼接与token预算、目录扫描、目录构建
"""
import json
import os
from pathlib import Path

import pytest

from src.knowledge.project_doc import (
    PRIORITY_TAG,
    ProjectDocIngestor,
    ProjectDocParser,
    ProjectDocScanner,
)


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

TAGGED_MD = """# 重点项目A

> 标签：简历优先、核心项目

## 一句话简介
重点项目内容。
"""

# 含「简历描述」章节：简历只应引用该章节
RESUME_MD = """# 项目Alpha

## 一句话简介
基于RAG的企业财报问答系统。

## 简历描述
负责检索链路设计与实现，召回准确率提升 30%。

## 技术栈
Python, FAISS

## 项目难点
中文分词与混合检索权重调优。
"""

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
def ingestor():
    return ProjectDocIngestor()


@pytest.fixture
def scanner():
    return ProjectDocScanner()


class TestCleanAndParse:
    def test_clean_only_trims_whitespace(self):
        """
        回归守卫：这里**不修正格式**（不剥围栏、不改标题层级）。
        格式问题交给 project_normalizer 按模板处理；此处若动手去修，
        不合规的文档会被"修成"看似合规的样子，从而绕过本该进行的整理。
        """
        wrapped = "```markdown\n# 项目A\n\n## 简介\n内容\n```"
        assert ProjectDocParser.clean_markdown(wrapped) == wrapped.strip()

    def test_clean_keeps_normal_doc(self):
        assert ProjectDocParser.clean_markdown(SAMPLE_MD).startswith("# ResumeAgent")

    def test_clean_strips_bom(self):
        """BOM 是编码标记而非文档格式；不剥会让标题正则失配，整批文档白跑大模型"""
        cleaned = ProjectDocParser.clean_markdown("\ufeff" + SAMPLE_MD)
        assert not cleaned.startswith("\ufeff")
        assert cleaned.startswith("# ResumeAgent")

    def test_extract_project_name_from_h1(self):
        assert ProjectDocParser.extract_project_name(SAMPLE_MD) == "ResumeAgent - 简历生成Agent"

    def test_extract_project_name_missing_h1(self):
        assert ProjectDocParser.extract_project_name("## 只有二级标题") == ""

    def test_doc_id_has_project_prefix(self):
        doc_id = ProjectDocParser.make_doc_id("ResumeAgent")
        assert doc_id.startswith("project-")
        # 同名项目doc_id稳定（幂等）
        assert doc_id == ProjectDocParser.make_doc_id("ResumeAgent")
        # 不同项目不冲突
        assert doc_id != ProjectDocParser.make_doc_id("OpenManus")

    def test_doc_id_with_user_id(self):
        """多用户隔离的根：同名项目在不同用户下 doc_id 不冲突"""
        assert ProjectDocParser.make_doc_id("同名项目", 9).startswith("project-u9-")
        assert ProjectDocParser.make_doc_id("同名项目", 9) != ProjectDocParser.make_doc_id(
            "同名项目", 10)
        # 公共素材（user_id=None）不带 u 段，兼容单用户时期已入库的文档
        assert ProjectDocParser.make_doc_id("同名项目") == ProjectDocParser.make_doc_id("同名项目", None)


class TestProjectNameFromFilename:
    """第1层容错：无一级标题时回退文件名，而不是直接拒收"""

    def test_plain_stem(self):
        assert ProjectDocParser.project_name_from_filename(Path("智能问答助手.md")) == "智能问答助手"

    def test_strips_highlight_suffix(self):
        assert ProjectDocParser.project_name_from_filename(
            Path("智能问答助手-项目亮点.md")) == "智能问答助手"

    def test_strips_intro_suffix(self):
        assert ProjectDocParser.project_name_from_filename(
            Path("智能问答助手-项目介绍.md")) == "智能问答助手"

    def test_all_suffix_falls_back_to_stem(self):
        """文件名本身就是后缀时不能截成空串（doc_id 会退化成 project-<hash of 空>）"""
        assert ProjectDocParser.project_name_from_filename(Path("-项目亮点.md")) == "-项目亮点"


class TestParseFileFallback:
    """第1层容错：parse_file 的项目名回退"""

    def test_missing_h1_falls_back_to_filename(self, ingestor, tmp_path):
        md = tmp_path / "智能问答助手.md"
        md.write_text("这是一个基于大模型的问答系统，支持多轮对话。", encoding="utf-8")

        parsed = ingestor.parse_file(md)

        assert parsed is not None
        assert parsed["project_name"] == "智能问答助手"
        assert parsed["name_from_fallback"] is True

    def test_h1_present_no_fallback(self, ingestor, tmp_path):
        md = tmp_path / "随便的名字.md"
        md.write_text("# 真正的标题\n\n## 技术栈\nPython", encoding="utf-8")

        parsed = ingestor.parse_file(md)

        assert parsed["project_name"] == "真正的标题"
        assert parsed["name_from_fallback"] is False

    def test_fallback_can_be_disabled(self, ingestor, tmp_path):
        """要求严格文档约定时仍可保持拒收语义"""
        md = tmp_path / "无标题.md"
        md.write_text("没有一级标题的正文", encoding="utf-8")
        assert ingestor.parse_file(md, fallback_name=False) is None

    def test_parse_text_uses_explicit_fallback(self, ingestor):
        """
        上传场景：临时文件叫 {uuid}.upload.md，不能拿它当项目名，
        必须显式传入用户看到的原始文件名。
        """
        parsed = ingestor.parse_text(
            "这是一个基于大模型的问答系统。", fallback_name="智能问答助手.md")

        assert parsed["project_name"] == "智能问答助手"
        assert parsed["name_from_fallback"] is True

    def test_parse_text_without_fallback_rejects(self, ingestor):
        assert ingestor.parse_text("没有标题的纯文本") is None


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

    def test_h3_headings_are_split_points(self, ingestor):
        """
        回归守卫：此前只认 `##`，用 `###` 分节的文档会退化成"整篇一块"——
        上传成功但检索质量极差，且用户无从察觉。
        """
        text = "# P\n\n### 技术栈\nPython, FAISS\n\n### 项目亮点\n召回提升30%"
        chunks = ingestor.split_by_headings(text, "P")

        assert len(chunks) == 2
        assert any("技术栈" in c for c in chunks)
        assert any("项目亮点" in c for c in chunks)

    def test_mixed_level_headings_all_split(self, ingestor):
        """## 与 ### 混用也能各自成块"""
        text = "# P\n\n## 简介\n一句话\n\n### 技术栈\nPython\n\n### 部署\nDocker"
        chunks = ingestor.split_by_headings(text, "P")
        assert len(chunks) == 3

    def test_resume_section_found_under_h3(self, ingestor):
        """章节层级放宽后，「简历描述」在 ### 下同样要能被识别"""
        text = "# P\n\n### 一句话简介\n简介\n\n### 简历描述\n精炼措辞\n\n### 技术栈\nPython"
        assert ProjectDocParser.extract_resume_section(text) is not None


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
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        (docs_dir / "ResumeAgent.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        stats = ingestor.ingest(docs_dir, chunks_dir, persist=False)

        assert stats == {"total": 1, "ingested": 1, "skipped": 0, "failed": 0, "duplicate": 0}
        doc_id = ProjectDocParser.make_doc_id("ResumeAgent - 简历生成Agent")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["category"] == "project"
        assert len(out["content"]["chunks"]) == 4

    def test_incremental_skip_unchanged(self, tmp_path, ingestor):
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        (docs_dir / "A.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        ingestor.ingest(docs_dir, chunks_dir, persist=False)
        stats = ingestor.ingest(docs_dir, chunks_dir, persist=False)
        assert stats["skipped"] == 1 and stats["ingested"] == 0

    def test_rebuild_when_source_updated(self, tmp_path, ingestor):
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        md = docs_dir / "A.md"
        md.write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"
        ingestor.ingest(docs_dir, chunks_dir, persist=False)

        # 更新源文件（mtime更晚）并改写内容
        md.write_text(SAMPLE_MD + "\n## 新章节\n新内容", encoding="utf-8")
        os.utime(md, (md.stat().st_mtime + 10, md.stat().st_mtime + 10))

        stats = ingestor.ingest(docs_dir, chunks_dir, persist=False)
        assert stats["ingested"] == 1
        doc_id = ProjectDocParser.make_doc_id("ResumeAgent - 简历生成Agent")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert len(out["content"]["chunks"]) == 5

    def test_force_rebuild(self, tmp_path, ingestor):
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        (docs_dir / "A.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"
        ingestor.ingest(docs_dir, chunks_dir, persist=False)

        stats = ingestor.ingest(docs_dir, chunks_dir, force=True, persist=False)
        assert stats["ingested"] == 1 and stats["skipped"] == 0

    def test_no_h1_falls_back_to_filename(self, tmp_path, ingestor):
        """
        宽容处理（行为变更）：此前无一级标题直接计入 failed 并拒收，
        现在以文件名主干作为项目名入库——用户手写的纯文本说明不该被整个拒绝。
        """
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        (docs_dir / "我的项目.md").write_text("没有一级标题的文档", encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        stats = ingestor.ingest(docs_dir, chunks_dir, persist=False)
        assert stats["ingested"] == 1 and stats["failed"] == 0

        doc_id = ProjectDocParser.make_doc_id("我的项目")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["source"] == "我的项目"

    def test_duplicate_project_name_skipped(self, tmp_path, ingestor):
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        # 两份文件一级标题同名
        (docs_dir / "a.md").write_text("# 同名项目\n\n## 简介\n第一份", encoding="utf-8")
        (docs_dir / "b.md").write_text("# 同名项目\n\n## 简介\n第二份", encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        stats = ingestor.ingest(docs_dir, chunks_dir, persist=False)
        assert stats["duplicate"] == 1 and stats["ingested"] == 1
        # 先入库的（a.md按文件名排序在前）内容保留
        doc_id = ProjectDocParser.make_doc_id("同名项目")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["file_name"] == "a.md"

    def test_atomic_write_no_tmp_leftover(self, tmp_path, ingestor):
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        (docs_dir / "A.md").write_text(SAMPLE_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        ingestor.ingest(docs_dir, chunks_dir, persist=False)
        assert not list(chunks_dir.glob("*.tmp"))


class TestTags:
    def test_extract_tags_with_quote_mark(self):
        tags, text = ProjectDocParser.extract_tags(TAGGED_MD)
        assert tags == ["简历优先", "核心项目"]
        # 标签行从正文中移除，不污染分块
        assert "标签" not in text
        assert text.startswith("# 重点项目A")
        assert "重点项目内容" in text

    def test_extract_tags_plain_line(self):
        tags, text = ProjectDocParser.extract_tags("# P\n\n标签：简历优先\n\n## A\n正文")
        assert tags == ["简历优先"]
        assert "标签" not in text

    def test_tag_after_first_h2_ignored(self):
        text = "# P\n\n## A\n正文\n\n标签：不该被识别"
        tags, out = ProjectDocParser.extract_tags(text)
        assert tags == []
        assert out == text

    def test_no_tags_returns_original(self):
        tags, out = ProjectDocParser.extract_tags(SAMPLE_MD)
        assert tags == []
        assert out == SAMPLE_MD

    def test_build_doc_data_tags_in_metainfo(self, ingestor):
        doc = ingestor.build_doc_data(SAMPLE_MD, "P", "P.md", 1.0, tags=[PRIORITY_TAG])
        assert doc["metainfo"]["tags"] == [PRIORITY_TAG]

    def test_build_doc_data_default_empty_tags(self, ingestor):
        doc = ingestor.build_doc_data(SAMPLE_MD, "P", "P.md", 1.0)
        assert doc["metainfo"]["tags"] == []

    def test_ingest_persists_tags_and_strips_from_chunks(self, tmp_path, ingestor):
        docs_dir = tmp_path / "projects"
        docs_dir.mkdir()
        (docs_dir / "A.md").write_text(TAGGED_MD, encoding="utf-8")
        chunks_dir = tmp_path / "chunks"

        ingestor.ingest(docs_dir, chunks_dir, persist=False)
        doc_id = ProjectDocParser.make_doc_id("重点项目A")
        out = json.loads((chunks_dir / f"{doc_id}.json").read_text(encoding="utf-8"))
        assert out["metainfo"]["tags"] == ["简历优先", "核心项目"]
        # 分块正文不含标签行内容
        for c in out["content"]["chunks"]:
            assert "简历优先" not in c["text"]


class TestResumeSection:
    """「简历描述」章节：简历引用区，缺失降级全文"""

    def test_extract_resume_section(self):
        sec = ProjectDocParser.extract_resume_section(RESUME_MD)
        assert sec is not None
        assert sec.startswith("## 简历描述")
        assert "召回准确率提升 30%" in sec
        # 不越界到后续章节
        assert "技术栈" not in sec

    def test_missing_resume_section_returns_none(self):
        assert ProjectDocParser.extract_resume_section(SAMPLE_MD) is None

    def test_variant_title_supported(self):
        for title in ("简历描述", "简历表述"):
            assert ProjectDocParser.extract_resume_section(f"# P\n\n## {title}\n内容") is not None

    def test_generic_title_not_matched(self):
        """「项目描述」太通用，不纳入匹配，避免误伤亮点文档的概述章节"""
        assert ProjectDocParser.extract_resume_section("# P\n\n## 项目描述\n概述") is None

    def test_scanner_uses_resume_section_when_present(self, tmp_path, scanner):
        (tmp_path / "a.md").write_text(RESUME_MD, encoding="utf-8")
        e = scanner.scan(tmp_path)[0]
        assert e["has_resume_section"] is True
        assert e["resume_text"] == e["resume_section"]
        # 简历素材不应包含技术栈/难点等无关章节
        assert "技术栈" not in e["resume_text"]
        # 全文仍保留完整内容，供分块检索使用
        assert "技术栈" in e["text"]

    def test_scanner_falls_back_to_full_text(self, tmp_path, scanner):
        (tmp_path / "b.md").write_text(SAMPLE_MD, encoding="utf-8")
        e = scanner.scan(tmp_path)[0]
        assert e["has_resume_section"] is False
        assert e["resume_section"] is None
        assert e["resume_text"] == e["text"]


class _CharSplitter:
    """按字符数当 token 用：让预算断言不依赖 tiktoken 的分词结果"""

    def count_tokens(self, text: str) -> int:
        return len(text)


LONG_MD = """# 项目Delta

## 简历描述
{resume}

## 技术栈
{stack}

## 量化成果
{result}
"""


class TestMaterial:
    """简历素材通道：不再只引用「简历描述」一节，按优先级拼接多章节并限长"""

    def test_material_carries_tech_stack_and_beyond(self):
        """主因回归：技术栈/难点要进素材，否则模型看不到能力清单，简历翻新不了"""
        material = ProjectDocScanner().build_material(RESUME_MD)
        assert material.index("【简历描述】") < material.index("【技术栈】") < material.index("【项目难点】")
        assert "召回准确率提升 30%" in material
        assert "Python, FAISS" in material
        assert "中文分词与混合检索权重调优" in material

    def test_material_omits_unused_sections(self):
        """检索关键词是入库用的辅助节，不该占素材预算"""
        assert "检索关键词" not in ProjectDocScanner().build_material(SAMPLE_MD)

    def test_missing_resume_section_uses_summary_not_fulltext(self):
        """无「简历描述」时只让一句话简介顶位；降级为全文等于把万字文档整篇塞进 prompt"""
        material = ProjectDocScanner().build_material(SAMPLE_MD)
        assert material.startswith("【项目简介】\n基于LangGraph的RAG知识库与简历生成Agent。")
        assert "## 技术栈" not in material

    def test_headlines_take_first_sentence(self):
        """亮点类长章节每条只取首句，同样预算内覆盖更多条目"""
        md = (
            "# 项目Epsilon\n\n## 一句话简介\n屏幕操作 Agent。\n\n## 技术亮点\n"
            "- **机制甲**：第一句是能立住的结论。后面是三段展开细节，讲实现过程与踩坑，"
            "整段进素材会把预算全吃掉，挤掉其它条目。\n"
            "- **机制乙**：这条没有句号结尾\n"
        )
        material = ProjectDocScanner().build_material(md)
        assert "- **机制甲**：第一句是能立住的结论" in material
        assert "后面是三段展开细节" not in material
        assert "- **机制乙**：这条没有句号结尾" in material

    def test_headline_item_length_cap(self):
        """首句本身超长时按字数截断，避免一条独占预算"""
        md = "# P\n\n## 技术亮点\n- 甲：" + "句" * 200 + "\n"
        material = ProjectDocScanner().build_material(md)
        item = material.splitlines()[-1]
        assert item.endswith("…")
        assert len(item) <= 125

    def test_budget_truncates_last_section_and_stops(self):
        """装不下整节时按字符比例截断（保住开头），且不再往后取用"""
        md = LONG_MD.format(resume="负" * 20, stack="S" * 100, result="R" * 200)
        scanner = ProjectDocScanner(material_max_tokens=250, splitter=_CharSplitter())
        material = scanner.build_material(md)
        assert "【量化成果】" in material
        assert material.endswith("…")
        assert scanner._count_tokens(material) <= 250

    def test_tight_budget_drops_the_section_whole(self):
        """剩余预算太少就不硬塞残块，整节省略"""
        md = LONG_MD.format(resume="负" * 20, stack="S" * 100, result="R" * 200)
        scanner = ProjectDocScanner(material_max_tokens=150, splitter=_CharSplitter())
        material = scanner.build_material(md)
        assert "【量化成果】" not in material
        assert scanner._count_tokens(material) <= 150

    def test_find_section_prefers_exact_then_contains(self):
        """别名先精确后包含，兼容"项目技术栈"这类自行加前缀的写法"""
        sections = ProjectDocParser.iter_sections("# P\n\n## 项目技术栈\nVue3\n")
        assert ProjectDocParser.find_section(sections, ("技术栈", "技术选型")) == ("项目技术栈", "Vue3")
        assert ProjectDocParser.find_section(sections, ("不存在的节",)) is None

    def test_scan_exposes_material_per_entry(self, tmp_path, scanner):
        (tmp_path / "a.md").write_text(RESUME_MD, encoding="utf-8")
        e = scanner.scan(tmp_path)[0]
        # resume_text 语义保持不变（只含简历描述节），素材走新字段
        assert e["resume_text"] == e["resume_section"]
        assert "Python, FAISS" in e["material"]


class TestScan:
    def test_scan_parses_entries(self, tmp_path, scanner):
        (tmp_path / "a.md").write_text(INTRO_A, encoding="utf-8")
        (tmp_path / "b.md").write_text(INTRO_B, encoding="utf-8")

        entries = scanner.scan(tmp_path)
        assert [e["name"] for e in entries] == ["项目Alpha", "项目Beta"]
        alpha = entries[0]
        assert PRIORITY_TAG in alpha["tags"]
        assert alpha["summary"] == "基于RAG的企业财报问答系统。"
        # 全文已剥离标签行
        assert "标签" not in alpha["text"]
        assert "## 技术栈" in alpha["text"]

    def test_scan_empty_or_missing_dir(self, tmp_path, scanner):
        assert scanner.scan(tmp_path / "not_exist") == []
        assert scanner.scan(tmp_path) == []

    def test_scan_skips_doc_without_h1(self, tmp_path, scanner):
        (tmp_path / "bad.md").write_text("没有一级标题", encoding="utf-8")
        assert scanner.scan(tmp_path) == []

    def test_scan_skips_duplicate_names(self, tmp_path, scanner):
        (tmp_path / "a.md").write_text("# 同名\n\n## 一句话简介\n第一份", encoding="utf-8")
        (tmp_path / "b.md").write_text("# 同名\n\n## 一句话简介\n第二份", encoding="utf-8")
        entries = scanner.scan(tmp_path)
        assert len(entries) == 1
        assert entries[0]["summary"] == "第一份"


class TestExtractSummary:
    def test_summary_from_first_paragraph_after_h1(self):
        assert ProjectDocParser.extract_summary(INTRO_NO_SUMMARY).startswith("这里是正文首段")

    def test_summary_strips_inner_newline(self):
        text = "# P\n\n## 一句话简介\n第一行\n第二行续写。\n"
        assert ProjectDocParser.extract_summary(text) == "第一行 第二行续写。"


class TestBuildCatalog:
    def test_catalog_lines_with_priority_mark(self, tmp_path, scanner):
        (tmp_path / "a.md").write_text(INTRO_A, encoding="utf-8")
        (tmp_path / "b.md").write_text(INTRO_B, encoding="utf-8")
        entries = scanner.scan(tmp_path)

        catalog = scanner.build_catalog(entries)
        assert "项目Alpha [简历优先]" in catalog
        assert "项目Beta —— Agent自主规划三模式实战。" in catalog
