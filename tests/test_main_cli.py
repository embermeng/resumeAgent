"""main.py CLI 委托回归测试(TDD)

重构后各 cmd_* 应委托 KnowledgeService,并原样透传参数,保证 CLI 行为不变。
"""
from unittest.mock import MagicMock, patch

import main

_SVC = "src.api.services.knowledge_service.KnowledgeService"


def _args(**kwargs):
    a = MagicMock()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


class TestCliDelegation:
    def test_parse_pdfs(self):
        with patch("main.get_config"), patch(_SVC) as MockSvc:
            main.cmd_parse_pdfs(_args(force=True))
            MockSvc.return_value.parse_pdfs.assert_called_once_with(force=True)

    def test_split_chunks(self):
        with patch("main.get_config"), patch(_SVC) as MockSvc:
            main.cmd_split_chunks(_args(chunk_size=200, chunk_overlap=20, force=False))
            MockSvc.return_value.split_chunks.assert_called_once_with(
                chunk_size=200, chunk_overlap=20, force=False
            )

    def test_extract_summaries(self):
        with patch("main.get_config"), patch(_SVC) as MockSvc:
            main.cmd_extract_summaries(_args(force=True))
            MockSvc.return_value.extract_summaries.assert_called_once_with(force=True)

    def test_build_indexes(self):
        with patch("main.get_config"), patch(_SVC) as MockSvc:
            main.cmd_build_indexes(_args(bm25=True, vector=False, force=True, prune=True))
            MockSvc.return_value.build_indexes.assert_called_once_with(
                bm25=True, vector=False, force=True, prune=True
            )

    def test_ingest_highlights(self):
        with patch("main.get_config"), patch(_SVC) as MockSvc:
            main.cmd_ingest_highlights(_args(force=False))
            MockSvc.return_value.ingest_highlights.assert_called_once_with(force=False)

    def test_build_all_passes_params_and_progress(self):
        with patch("main.get_config"), patch(_SVC) as MockSvc:
            main.cmd_build_all(_args(force=True, chunk_size=300, chunk_overlap=50, prune=False))
            call = MockSvc.return_value.build_all.call_args
            assert call.kwargs["force"] is True
            assert call.kwargs["chunk_size"] == 300
            assert call.kwargs["chunk_overlap"] == 50
            assert call.kwargs["prune"] is False
            # build_all 传入 CLI 进度回调(保持原 [x/5] 日志输出)
            assert callable(call.kwargs["progress"])

    def test_cli_progress_logs_message(self, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            main._cli_progress("parse-pdfs", "[1/5] 解析PDF...", 10)
        assert "[1/5] 解析PDF..." in caplog.text
