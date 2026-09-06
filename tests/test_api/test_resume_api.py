"""简历接口测试(TDD):/api/resume/parse 与 /api/resume/supported-extensions"""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_resume_parser


def _client(mock_parser):
    app = create_app()
    app.dependency_overrides[get_resume_parser] = lambda: mock_parser
    return TestClient(app)


class TestSupportedExtensions:
    def test_returns_four(self):
        client = TestClient(create_app())
        r = client.get("/api/resume/supported-extensions")
        assert r.status_code == 200
        assert set(r.json()["extensions"]) == {".md", ".txt", ".docx", ".pdf"}


class TestParseResume:
    def test_success(self):
        parser = MagicMock()
        parser.parse.return_value = "# 张三的简历"
        client = _client(parser)
        r = client.post(
            "/api/resume/parse",
            files={"file": ("resume.md", b"# content", "text/markdown")},
        )
        assert r.status_code == 200
        assert r.json() == {"filename": "resume.md", "content": "# 张三的简历"}

    def test_passes_filename_and_bytes(self):
        parser = MagicMock()
        parser.parse.return_value = "ok"
        client = _client(parser)
        client.post(
            "/api/resume/parse",
            files={"file": ("a.docx", b"BINARY", "application/octet-stream")},
        )
        args = parser.parse.call_args.args
        assert args[0] == "a.docx"
        assert args[1] == b"BINARY"

    def test_unsupported_extension_400(self):
        parser = MagicMock()
        parser.parse.side_effect = ValueError("不支持的简历文件格式: .xyz")
        client = _client(parser)
        r = client.post(
            "/api/resume/parse",
            files={"file": ("bad.xyz", b"x", "application/octet-stream")},
        )
        assert r.status_code == 400
        assert "不支持" in r.json()["detail"]

    def test_empty_result_400(self):
        parser = MagicMock()
        parser.parse.side_effect = ValueError("简历解析结果为空: e.md")
        client = _client(parser)
        r = client.post(
            "/api/resume/parse", files={"file": ("e.md", b"", "text/markdown")}
        )
        assert r.status_code == 400

    def test_missing_file_422(self):
        client = _client(MagicMock())
        r = client.post("/api/resume/parse")
        assert r.status_code == 422
