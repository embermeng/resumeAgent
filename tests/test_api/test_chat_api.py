"""POST /api/chat SSE 测试(TDD)

用 dependency_overrides 注入 mock AgentService,验证:
- Content-Type=text/event-stream
- run_stream 事件字典 -> SSE 帧映射(type 作事件名,其余字段作 data)
- existing_resume 透传
- 空 prompt -> 422
- 服务异常 -> error 帧
"""
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_agent_service
from tests.test_api.helpers import event_names, parse_sse


def _client(events=None, side_effect=None):
    app = create_app()
    fake = MagicMock()
    if side_effect is not None:
        fake.run_stream.side_effect = side_effect
    else:
        fake.run_stream.return_value = iter(events or [])
    app.dependency_overrides[get_agent_service] = lambda: fake
    return TestClient(app), fake


class TestChatSSE:
    def test_content_type_event_stream(self):
        client, _ = _client(events=[{"type": "done", "intent": "chitchat", "step": "x"}])
        r = client.post("/api/chat", json={"prompt": "hi"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

    def test_event_sequence_and_mapping(self):
        events = [
            {"type": "status", "text": "正在识别意图..."},
            {"type": "intent", "value": "quick_response"},
            {"type": "token", "text": "RAG"},
            {"type": "token", "text": " 是检索增强"},
            {"type": "done", "intent": "quick_response",
             "step": "quick_response_done", "resume_final": ""},
        ]
        client, _ = _client(events=events)
        r = client.post("/api/chat", json={"prompt": "RAG 是什么"})
        frames = parse_sse(r.text)

        assert event_names(frames) == ["status", "intent", "token", "token", "done"]
        assert frames[0][1] == {"text": "正在识别意图..."}
        assert frames[1][1] == {"value": "quick_response"}
        assert frames[2][1] == {"text": "RAG"}
        # done 帧去掉 type,保留其余字段
        assert frames[4][1]["intent"] == "quick_response"
        assert frames[4][1]["step"] == "quick_response_done"
        assert "type" not in frames[4][1]

    def test_existing_resume_passthrough(self):
        client, fake = _client(events=[{"type": "done", "intent": "deep_thinking", "step": "x"}])
        client.post("/api/chat", json={"prompt": "生成简历", "existing_resume": "# 旧"})
        assert fake.run_stream.call_args.args[0] == "生成简历"
        assert fake.run_stream.call_args.kwargs["existing_resume"] == "# 旧"

    def test_empty_prompt_422(self):
        client, _ = _client(events=[])
        r = client.post("/api/chat", json={"prompt": ""})
        assert r.status_code == 422

    def test_error_frame_on_exception(self):
        client, _ = _client(side_effect=RuntimeError("LLM 挂了"))
        r = client.post("/api/chat", json={"prompt": "hi"})
        frames = parse_sse(r.text)
        assert frames[-1][0] == "error"
        assert "LLM 挂了" in frames[-1][1]["message"]
