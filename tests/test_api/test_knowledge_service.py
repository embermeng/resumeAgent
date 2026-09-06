"""src/api/services/knowledge_service.py 测试(TDD)

聚焦新增的编排与进度契约:
- build_all 的阶段调用顺序与 progress(stage, percent) 序列(契约第5节)
- run(task) dispatch
- 各阶段委托到正确的底层 ingestor/parser(冒烟)
底层文件 IO 逻辑搬运自 main.py,不在此深测。
"""
from unittest.mock import MagicMock, patch

import pytest

from src.api.services.knowledge_service import KnowledgeService


def _make_config(parsed_json_glob: bool = True):
    """构造 mock config:各目录守卫默认通过;parsed_pdfs 的 *.json glob 可控"""
    cfg = MagicMock()
    cfg.llm.provider = "dashscope"
    cfg.llm.model = "test-model"
    cfg.embedding.provider = "dashscope"
    cfg.embedding.model = "text-embedding-v1"

    p = cfg.paths
    parsed_dir = MagicMock()
    parsed_dir.exists.return_value = True
    parsed_dir.glob.return_value = [MagicMock()] if parsed_json_glob else []
    md_dir = MagicMock()

    def _truediv(name):
        if name == "parsed_pdfs":
            return parsed_dir
        if name == "markdown_temp":
            return md_dir
        return MagicMock()

    p.processed_dir.__truediv__.side_effect = _truediv

    for attr in [
        "course_chunks_dir", "course_summaries_dir", "project_highlights_dir",
        "bm25_dbs_dir", "vector_dbs_dir", "course_pdfs_dir", "catalog_path",
    ]:
        d = MagicMock()
        d.exists.return_value = True
        d.glob.return_value = [MagicMock()]
        setattr(p, attr, d)
    return cfg


class TestBuildAllOrchestration:
    def test_progress_sequence_matches_contract(self):
        svc = KnowledgeService(config=MagicMock())
        calls = []

        def progress(stage, message, percent):
            calls.append((stage, percent))

        with patch.object(svc, "parse_pdfs"), \
             patch.object(svc, "extract_summaries"), \
             patch.object(svc, "split_chunks"), \
             patch.object(svc, "ingest_highlights"), \
             patch.object(svc, "build_indexes"):
            svc.build_all(progress=progress)

        # 契约第5节:阶段与百分比
        assert calls == [
            ("parse-pdfs", 10),
            ("extract-summaries", 35),
            ("split-chunks", 55),
            ("ingest-highlights", 70),
            ("build-indexes", 90),
        ]

    def test_calls_all_stages_in_order(self):
        svc = KnowledgeService(config=MagicMock())
        order = []
        with patch.object(svc, "parse_pdfs", side_effect=lambda **k: order.append("parse")), \
             patch.object(svc, "extract_summaries", side_effect=lambda **k: order.append("summ")), \
             patch.object(svc, "split_chunks", side_effect=lambda **k: order.append("split")), \
             patch.object(svc, "ingest_highlights", side_effect=lambda **k: order.append("hl")), \
             patch.object(svc, "build_indexes", side_effect=lambda **k: order.append("idx")):
            svc.build_all()
        assert order == ["parse", "summ", "split", "hl", "idx"]

    def test_subtasks_do_not_report_progress_themselves_in_build_all(self):
        # build_all 调子阶段时不传 progress(避免重复上报),子阶段收到的 progress 为 None
        svc = KnowledgeService(config=MagicMock())
        with patch.object(svc, "parse_pdfs") as m_parse, \
             patch.object(svc, "extract_summaries"), \
             patch.object(svc, "split_chunks"), \
             patch.object(svc, "ingest_highlights"), \
             patch.object(svc, "build_indexes"):
            svc.build_all(progress=lambda *a: None)
        assert m_parse.call_args.kwargs.get("progress") is None


class TestRunDispatch:
    @pytest.mark.parametrize("task,method", [
        ("build-all", "build_all"),
        ("parse-pdfs", "parse_pdfs"),
        ("extract-summaries", "extract_summaries"),
        ("split-chunks", "split_chunks"),
        ("build-indexes", "build_indexes"),
        ("ingest-highlights", "ingest_highlights"),
    ])
    def test_dispatch(self, task, method):
        svc = KnowledgeService(config=MagicMock())
        with patch.object(svc, method) as m:
            svc.run(task)
            m.assert_called_once()

    def test_run_passes_progress(self):
        svc = KnowledgeService(config=MagicMock())
        prog = lambda *a: None  # noqa: E731
        with patch.object(svc, "parse_pdfs") as m:
            svc.run("parse-pdfs", progress=prog)
            assert m.call_args.kwargs["progress"] is prog

    def test_unknown_task_raises(self):
        svc = KnowledgeService(config=MagicMock())
        with pytest.raises(ValueError):
            svc.run("not-a-task")


class TestStagesSmoke:
    def test_parse_pdfs_calls_parser(self):
        cfg = _make_config()
        with patch("src.knowledge.pdf_parser.PDFParser") as MockParser:
            KnowledgeService(config=cfg).parse_pdfs(force=True)
            MockParser.return_value.parse_and_export_json.assert_called_once()
            assert MockParser.return_value.parse_and_export_json.call_args.kwargs["force"] is True

    def test_extract_summaries_calls_summarizer_and_catalog(self):
        cfg = _make_config(parsed_json_glob=True)
        with patch("src.knowledge.summarizer.CourseSummarizer") as MockSum:
            KnowledgeService(config=cfg).extract_summaries()
            MockSum.return_value.process_parsed_dir.assert_called_once()
            MockSum.return_value.build_catalog.assert_called_once()

    def test_extract_summaries_skips_when_empty(self):
        cfg = _make_config(parsed_json_glob=False)
        with patch("src.knowledge.summarizer.CourseSummarizer") as MockSum:
            KnowledgeService(config=cfg).extract_summaries()
            MockSum.return_value.process_parsed_dir.assert_not_called()

    def test_split_chunks_calls_splitter(self):
        # parsed glob 为空 → 跳过文件循环,仍调用 split_and_save
        cfg = _make_config(parsed_json_glob=False)
        with patch("src.knowledge.text_splitter.TextSplitter") as MockSplit:
            KnowledgeService(config=cfg).split_chunks(chunk_size=200, chunk_overlap=20)
            MockSplit.return_value.split_and_save.assert_called_once()
            kwargs = MockSplit.return_value.split_and_save.call_args.kwargs
            assert kwargs["chunk_size"] == 200
            assert kwargs["chunk_overlap"] == 20

    def test_build_indexes_calls_both_ingestors(self):
        cfg = _make_config()
        with patch("src.knowledge.ingestion.BM25Ingestor") as MockBM25, \
             patch("src.knowledge.ingestion.VectorDBIngestor") as MockVec:
            KnowledgeService(config=cfg).build_indexes(bm25=True, vector=True)
            MockBM25.return_value.process_chunks_dir.assert_called_once()
            MockVec.return_value.process_chunks_dir.assert_called_once()

    def test_build_indexes_bm25_only(self):
        cfg = _make_config()
        with patch("src.knowledge.ingestion.BM25Ingestor") as MockBM25, \
             patch("src.knowledge.ingestion.VectorDBIngestor") as MockVec:
            KnowledgeService(config=cfg).build_indexes(bm25=True, vector=False)
            MockBM25.return_value.process_chunks_dir.assert_called_once()
            MockVec.return_value.process_chunks_dir.assert_not_called()

    def test_ingest_highlights_calls_ingestor(self):
        cfg = _make_config()
        with patch("src.knowledge.highlight_ingestor.HighlightIngestor") as MockHL:
            KnowledgeService(config=cfg).ingest_highlights(force=True)
            MockHL.return_value.ingest.assert_called_once()
            assert MockHL.return_value.ingest.call_args.kwargs["force"] is True
