"""
课程摘要提取模块测试（分层检索第一层）
使用Mock避免真实API调用
"""
import os
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.knowledge.summarizer import CourseSummarizer, _need_rebuild


# ============================================================
# _need_rebuild 增量判断
# ============================================================

class TestNeedRebuild:
    def test_force_always_rebuild(self, tmp_path):
        src = tmp_path / "a.json"
        tgt = tmp_path / "b.json"
        src.write_text("x")
        tgt.write_text("y")
        assert _need_rebuild(src, tgt, force=True) is True

    def test_missing_target_rebuild(self, tmp_path):
        src = tmp_path / "a.json"
        src.write_text("x")
        assert _need_rebuild(src, tmp_path / "not_exist.json", force=False) is True

    def test_fresh_target_skip(self, tmp_path):
        src = tmp_path / "a.json"
        tgt = tmp_path / "b.json"
        src.write_text("x")
        tgt.write_text("y")
        # 让摘要比解析结果新
        os.utime(src, (1000, 1000))
        os.utime(tgt, (2000, 2000))
        assert _need_rebuild(src, tgt, force=False) is False

    def test_stale_target_rebuild(self, tmp_path):
        src = tmp_path / "a.json"
        tgt = tmp_path / "b.json"
        src.write_text("x")
        tgt.write_text("y")
        # 解析结果比摘要新（上游更新过）
        os.utime(src, (2000, 2000))
        os.utime(tgt, (1000, 1000))
        assert _need_rebuild(src, tgt, force=False) is True


# ============================================================
# summarize_markdown LLM摘要提取
# ============================================================

def _valid_summary():
    return {
        "course_name": "RAG技术与应用",
        "one_line_intro": "讲解RAG全流程",
        "key_points": ["RAG核心流程：构建检索增强问答", "向量检索：FAISS相似度召回"],
        "tech_keywords": ["RAG", "FAISS"],
    }


class TestSummarizeMarkdown:
    @patch("src.knowledge.summarizer.APIProcessor")
    def test_valid_summary(self, mock_api_class):
        mock_api = MagicMock()
        mock_api.send_message.return_value = _valid_summary()
        mock_api_class.return_value = mock_api

        s = CourseSummarizer(provider="dashscope", model="test-model")
        result = s.summarize_markdown("# 课程内容" * 100)
        assert result["course_name"] == "RAG技术与应用"
        assert len(result["key_points"]) == 2

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_parse_error_returns_none(self, mock_api_class):
        mock_api = MagicMock()
        mock_api.send_message.return_value = {"content": "无法解析", "parse_error": "bad json"}
        mock_api_class.return_value = mock_api

        s = CourseSummarizer(provider="dashscope")
        assert s.summarize_markdown("内容") is None

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_invalid_fields_returns_none(self, mock_api_class):
        """缺少必填字段（key_points为空）应返回None"""
        mock_api = MagicMock()
        mock_api.send_message.return_value = {
            "course_name": "X", "one_line_intro": "Y", "key_points": [], "tech_keywords": []
        }
        mock_api_class.return_value = mock_api

        s = CourseSummarizer(provider="dashscope")
        assert s.summarize_markdown("内容") is None

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_fallback_course_name(self, mock_api_class):
        """LLM未给课程名时用source兜底"""
        mock_api = MagicMock()
        summary = _valid_summary()
        summary["course_name"] = ""
        mock_api.send_message.return_value = summary
        mock_api_class.return_value = mock_api

        s = CourseSummarizer(provider="dashscope")
        result = s.summarize_markdown("内容", course_name="课程A")
        assert result["course_name"] == "课程A"


# ============================================================
# process_parsed_dir 批量增量处理
# ============================================================

def _write_parsed_doc(parsed_dir: Path, doc_id: str, markdown: str, source: str):
    payload = {
        "metainfo": {"doc_id": doc_id, "source": source, "category": "course"},
        "content": {"markdown": markdown},
    }
    with open(parsed_dir / f"{doc_id}.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class TestProcessParsedDir:
    @patch("src.knowledge.summarizer.APIProcessor")
    def test_build_and_skip_incremental(self, mock_api_class, tmp_path):
        """首次全量构建；二次运行应全部跳过且不再调LLM"""
        mock_api = MagicMock()
        mock_api.send_message.return_value = _valid_summary()
        mock_api_class.return_value = mock_api

        parsed_dir = tmp_path / "parsed"
        out_dir = tmp_path / "summaries"
        parsed_dir.mkdir()
        _write_parsed_doc(parsed_dir, "d1", "# 课程1", "课程1")
        _write_parsed_doc(parsed_dir, "d2", "# 课程2", "课程2")

        s = CourseSummarizer(provider="dashscope")
        stats = s.process_parsed_dir(parsed_dir, out_dir)
        assert stats["built"] == 2
        assert mock_api.send_message.call_count == 2
        assert (out_dir / "d1.json").exists()

        # 二次运行：摘要比解析新 → 全部跳过，不再调LLM
        stats2 = s.process_parsed_dir(parsed_dir, out_dir)
        assert stats2["skipped"] == 2 and stats2["built"] == 0
        assert mock_api.send_message.call_count == 2

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_force_rebuild(self, mock_api_class, tmp_path):
        mock_api = MagicMock()
        mock_api.send_message.return_value = _valid_summary()
        mock_api_class.return_value = mock_api

        parsed_dir = tmp_path / "parsed"
        out_dir = tmp_path / "summaries"
        parsed_dir.mkdir()
        _write_parsed_doc(parsed_dir, "d1", "# 课程1", "课程1")

        s = CourseSummarizer(provider="dashscope")
        s.process_parsed_dir(parsed_dir, out_dir)
        stats = s.process_parsed_dir(parsed_dir, out_dir, force=True)
        assert stats["built"] == 1
        assert mock_api.send_message.call_count == 2

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_stale_summary_rebuilt(self, mock_api_class, tmp_path):
        """解析结果更新后（mtime更新），摘要应重建"""
        mock_api = MagicMock()
        mock_api.send_message.return_value = _valid_summary()
        mock_api_class.return_value = mock_api

        parsed_dir = tmp_path / "parsed"
        out_dir = tmp_path / "summaries"
        parsed_dir.mkdir()
        _write_parsed_doc(parsed_dir, "d1", "# 课程1", "课程1")

        s = CourseSummarizer(provider="dashscope")
        s.process_parsed_dir(parsed_dir, out_dir)

        # 模拟源文档更新：解析结果mtime变新
        os.utime(parsed_dir / "d1.json", (9999999999, 9999999999))
        stats = s.process_parsed_dir(parsed_dir, out_dir)
        assert stats["built"] == 1

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_no_tmp_residue_and_atomic_write(self, mock_api_class, tmp_path):
        """写入后不应残留.tmp临时文件"""
        mock_api = MagicMock()
        mock_api.send_message.return_value = _valid_summary()
        mock_api_class.return_value = mock_api

        parsed_dir = tmp_path / "parsed"
        out_dir = tmp_path / "summaries"
        parsed_dir.mkdir()
        _write_parsed_doc(parsed_dir, "d1", "# 课程1", "课程1")

        s = CourseSummarizer(provider="dashscope")
        s.process_parsed_dir(parsed_dir, out_dir)
        assert list(out_dir.glob("*.tmp")) == []

    @patch("src.knowledge.summarizer.APIProcessor")
    def test_empty_markdown_skipped(self, mock_api_class, tmp_path):
        mock_api = MagicMock()
        mock_api_class.return_value = mock_api

        parsed_dir = tmp_path / "parsed"
        out_dir = tmp_path / "summaries"
        parsed_dir.mkdir()
        _write_parsed_doc(parsed_dir, "d1", "   ", "空课程")

        s = CourseSummarizer(provider="dashscope")
        stats = s.process_parsed_dir(parsed_dir, out_dir)
        assert stats["built"] == 0
        mock_api.send_message.assert_not_called()


# ============================================================
# build_catalog 目录合并
# ============================================================

class TestBuildCatalog:
    def test_build_catalog(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        for doc_id, name in [("d1", "课程1"), ("d2", "课程2")]:
            payload = {
                "doc_id": doc_id,
                "source": name,
                "summary": {
                    "course_name": name,
                    "one_line_intro": f"{name}定位",
                    "key_points": [f"{name}知识点"],
                    "tech_keywords": ["kw"],
                },
            }
            with open(summaries_dir / f"{doc_id}.json", "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

        catalog_path = tmp_path / "catalog.json"
        entries = CourseSummarizer.build_catalog(summaries_dir, catalog_path)

        assert len(entries) == 2
        assert catalog_path.exists()
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data["courses"]) == 2
        assert data["courses"][0]["doc_id"] == "d1"
        assert list(tmp_path.glob("*.tmp")) == []

    def test_corrupt_file_skipped(self, tmp_path):
        summaries_dir = tmp_path / "summaries"
        summaries_dir.mkdir()
        (summaries_dir / "bad.json").write_text("{损坏的json", encoding="utf-8")
        payload = {
            "doc_id": "d1", "source": "课程1",
            "summary": {"course_name": "课程1", "one_line_intro": "x",
                        "key_points": ["p"], "tech_keywords": []},
        }
        with open(summaries_dir / "d1.json", "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)

        entries = CourseSummarizer.build_catalog(summaries_dir, tmp_path / "catalog.json")
        assert len(entries) == 1


# ============================================================
# 分层检索工具（AgentTools的选点与定向检索）
# ============================================================

class TestHierarchicalTools:
    def _make_tools(self, tmp_path):
        from src.agent.tools import AgentTools
        config = MagicMock()
        config.paths.catalog_path = tmp_path / "catalog.json"
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
        return AgentTools(config=config)

    def _write_catalog(self, tmp_path, doc_ids=("d1", "d2")):
        courses = [{
            "doc_id": did, "source": f"课程{did}", "course_name": f"课程{did}",
            "one_line_intro": "定位", "key_points": ["知识点A", "知识点B"],
            "tech_keywords": ["kw"],
        } for did in doc_ids]
        with open(tmp_path / "catalog.json", "w", encoding="utf-8") as f:
            json.dump({"courses": courses}, f, ensure_ascii=False)

    def test_load_catalog_missing(self, tmp_path):
        tools = self._make_tools(tmp_path)
        assert tools.load_catalog() is None

    def test_select_no_catalog_returns_none(self, tmp_path):
        tools = self._make_tools(tmp_path)
        assert tools.select_resume_knowledge("生成简历") is None

    @patch("src.agent.tools.APIProcessor")
    def test_select_valid_plan(self, mock_api_class, tmp_path):
        self._write_catalog(tmp_path)
        mock_api = MagicMock()
        mock_api.send_message.return_value = {
            "selections": [
                {"doc_id": "d1", "selected_points": ["LangChain链式编排"]},
                {"doc_id": "d2", "selected_points": ["模型微调"]},
            ],
            "reason": "含金量高",
        }
        mock_api_class.return_value = mock_api

        tools = self._make_tools(tmp_path)
        plan = tools.select_resume_knowledge("生成简历", job_requirement="AI工程师")
        assert plan is not None
        assert len(plan.selections) == 2
        assert plan.selections[0].doc_id == "d1"

    @patch("src.agent.tools.APIProcessor")
    def test_select_filters_fake_doc_ids(self, mock_api_class, tmp_path):
        """LLM编造的doc_id应被过滤"""
        self._write_catalog(tmp_path)
        mock_api = MagicMock()
        mock_api.send_message.return_value = {
            "selections": [
                {"doc_id": "d1", "selected_points": ["真实知识点"]},
                {"doc_id": "fake_doc", "selected_points": ["编造的"]},
            ],
            "reason": "",
        }
        mock_api_class.return_value = mock_api

        tools = self._make_tools(tmp_path)
        plan = tools.select_resume_knowledge("生成简历")
        assert len(plan.selections) == 1
        assert plan.selections[0].doc_id == "d1"

    @patch("src.agent.tools.APIProcessor")
    def test_select_parse_error_returns_none(self, mock_api_class, tmp_path):
        self._write_catalog(tmp_path)
        mock_api = MagicMock()
        mock_api.send_message.return_value = {"content": "xxx", "parse_error": "bad"}
        mock_api_class.return_value = mock_api

        tools = self._make_tools(tmp_path)
        assert tools.select_resume_knowledge("生成简历") is None

    def test_retrieve_by_plan_dedupe_and_format(self, tmp_path):
        from src.schemas.knowledge import SelectionPlan, DocSelection
        tools = self._make_tools(tmp_path)
        # mock检索器：两次查询返回含重复chunk的结果
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.side_effect = [
            [{"doc_id": "d1", "chunk_id": 0, "text": "内容1", "source": "课程d1", "score": 0.9},
             {"doc_id": "d1", "chunk_id": 1, "text": "内容2", "source": "课程d1", "score": 0.8}],
            [{"doc_id": "d1", "chunk_id": 0, "text": "内容1", "source": "课程d1", "score": 0.9},
             {"doc_id": "d1", "chunk_id": 2, "text": "内容3", "source": "课程d1", "score": 0.7}],
        ]
        tools._hybrid_retriever = mock_hybrid

        plan = SelectionPlan(selections=[
            DocSelection(doc_id="d1", selected_points=["知识点A", "知识点B"]),
        ])
        result = tools.retrieve_by_plan(plan)

        # 去重后3条
        assert result.count("[") == 3
        assert "内容1" in result and "内容3" in result
        # 定向检索带了doc_ids过滤
        for call in mock_hybrid.retrieve.call_args_list:
            assert call.kwargs["doc_ids"] == ["d1"]

    def test_retrieve_by_plan_empty(self, tmp_path):
        from src.schemas.knowledge import SelectionPlan, DocSelection
        tools = self._make_tools(tmp_path)
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.return_value = []
        tools._hybrid_retriever = mock_hybrid

        plan = SelectionPlan(selections=[DocSelection(doc_id="d1", selected_points=["知识点"])])
        assert tools.retrieve_by_plan(plan) == "未检索到相关知识。"

    def test_retrieve_by_plan_fair_allocation(self, tmp_path):
        """多门课程应公平分配名额，轮询交错，第一门课不应占满全部名额"""
        from src.schemas.knowledge import SelectionPlan, DocSelection
        tools = self._make_tools(tmp_path)
        mock_hybrid = MagicMock()
        # 两门课各返回3块（每门课各一次检索调用）
        mock_hybrid.retrieve.side_effect = [
            [{"doc_id": "d1", "chunk_id": i, "text": f"课程一内容{i}", "source": "课程一"}
             for i in range(3)],
            [{"doc_id": "d2", "chunk_id": i, "text": f"课程二内容{i}", "source": "课程二"}
             for i in range(3)],
        ]
        tools._hybrid_retriever = mock_hybrid

        plan = SelectionPlan(selections=[
            DocSelection(doc_id="d1", selected_points=["知识点A"]),
            DocSelection(doc_id="d2", selected_points=["知识点B"]),
        ])
        result = tools.retrieve_by_plan(plan, max_results=4)

        # 4个名额两门课各占一半，且轮询交错（课程一第一块在课程二第一块之前）
        assert result.count("课程一内容") == 2
        assert result.count("课程二内容") == 2
        assert result.index("课程一内容0") < result.index("课程二内容0")
        assert result.index("课程二内容0") < result.index("课程一内容1")
