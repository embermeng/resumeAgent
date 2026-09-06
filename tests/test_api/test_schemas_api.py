"""src/api/schemas_api.py 测试(TDD):契约模型约束与默认值"""
import pytest
from pydantic import ValidationError

from src.api.schemas_api import (
    ChatRequest,
    BuildRequest,
    ParseResponse,
    TaskStatus,
    SupportedExtensions,
)


class TestChatRequest:
    def test_ok(self):
        r = ChatRequest(prompt="RAG 是什么?")
        assert r.prompt == "RAG 是什么?"
        assert r.existing_resume is None

    def test_with_existing_resume(self):
        r = ChatRequest(prompt="生成简历", existing_resume="# 张三")
        assert r.existing_resume == "# 张三"

    def test_empty_prompt_rejected(self):
        # 契约:prompt 非空
        with pytest.raises(ValidationError):
            ChatRequest(prompt="")


class TestBuildRequest:
    def test_defaults(self):
        r = BuildRequest(task="build-all")
        assert r.force is False
        assert r.chunk_size == 300
        assert r.chunk_overlap == 50
        assert r.prune is False

    def test_invalid_task_rejected(self):
        with pytest.raises(ValidationError):
            BuildRequest(task="not-a-task")

    def test_all_task_kinds_accepted(self):
        for kind in [
            "build-all", "parse-pdfs", "extract-summaries",
            "split-chunks", "build-indexes", "ingest-highlights",
        ]:
            assert BuildRequest(task=kind).task == kind


class TestResponses:
    def test_parse_response(self):
        r = ParseResponse(filename="a.md", content="# hi")
        assert r.filename == "a.md"
        assert r.content == "# hi"

    def test_supported_extensions(self):
        r = SupportedExtensions(extensions=[".md", ".txt", ".docx", ".pdf"])
        assert len(r.extensions) == 4

    def test_task_status_optional_fields(self):
        s = TaskStatus(
            task_id="t1", task="build-all", status="running", created_at=1.0, percent=50.0
        )
        assert s.status == "running"
        assert s.percent == 50.0
        assert s.finished_at is None
        assert s.error is None
