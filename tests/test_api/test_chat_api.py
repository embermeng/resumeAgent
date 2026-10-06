"""POST /api/chat SSE 测试(TDD)

用 dependency_overrides 注入 mock AgentService,验证:
- Content-Type=text/event-stream
- run_stream 事件字典 -> SSE 帧映射(type 作事件名,其余字段作 data)
- existing_resume 透传
- 空 prompt -> 422
- 服务异常 -> error 帧
"""
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_agent_service
from src.api.routers import chat as chat_mod
from src.api.routers.utils import save_stream
from src.auth.security import get_current_user
from tests.test_api.helpers import FakeRedis, event_names, only_buf_key, parse_sse


@pytest.fixture(autouse=True)
def _stub_dialog(monkeypatch):
    """解耦真实 DB:chat 现在会建会话/写消息(且需 user_id 归属)。
    将这些副作用打桩,让 SSE 映射测试专注事件转换本身。
    create_conversation 返回 None 时 chat 会跳过 conversation 帧与落库,符合断言预期。"""
    monkeypatch.setattr("src.api.routers.chat.create_conversation", lambda *a, **k: None)
    monkeypatch.setattr("src.api.routers.chat.add_message", lambda *a, **k: None)
    monkeypatch.setattr("src.api.routers.chat.check_conversation", lambda *a, **k: True)


@pytest.fixture(autouse=True)
def _fake_redis():
    """chat.py 与 save_stream.py 各自 import get_sync_client,两处都 patch 到同一 FakeRedis,
    让 POST 对话测试不连真实 Redis(否则每次 buffer_event 连接超时会把用例拖到几十秒)。"""
    fr = FakeRedis()
    with patch.object(chat_mod, "get_sync_client", return_value=fr), \
         patch.object(save_stream, "get_sync_client", return_value=fr):
        yield fr


def _client(events=None, side_effect=None):
    app = create_app()
    fake = MagicMock()
    if side_effect is not None:
        fake.run_stream.side_effect = side_effect
    else:
        fake.run_stream.return_value = iter(events or [])
    app.dependency_overrides[get_agent_service] = lambda: fake
    # chat 现需鉴权:注入一个假登录用户(id=1),免走真实 token/DB
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
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


class TestChatBuffering:
    """conv_id 有效路径:conversation 帧带 stream_id、归属映射落 Redis、事件按 seq 写缓冲。

    现有 TestChatSSE 的 create_conversation 打桩为 None,走不到 setex/save_conv_mapping 分支
    (正是之前 setex 漏降级却未被测出的盲区)。
    """

    def _client_with_conv(self, monkeypatch, events, conv_id=42):
        monkeypatch.setattr("src.api.routers.chat.create_conversation", lambda *a, **k: conv_id)
        monkeypatch.setattr("src.api.routers.chat.add_message", lambda *a, **k: None)
        app = create_app()
        fake = MagicMock()
        fake.run_stream.return_value = iter(events)
        app.dependency_overrides[get_agent_service] = lambda: fake
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
        return TestClient(app)

    def test_conv_frame_carries_stream_id(self, monkeypatch, _fake_redis):
        client = self._client_with_conv(
            monkeypatch, [{"type": "token", "text": "hi"},
                          {"type": "done", "intent": "x", "step": "y"}])
        r = client.post("/api/chat", json={"prompt": "hi"})
        frames = parse_sse(r.text)
        assert frames[0][0] == "conversation"
        assert frames[0][1]["conversation_id"] == 42
        assert frames[0][1]["stream_id"]  # 非空字符串

    def test_conv_mapping_written_to_redis(self, monkeypatch, _fake_redis):
        """归属映射 stream_id→conv_id 落 Redis(供恢复端点校验归属)。"""
        client = self._client_with_conv(
            monkeypatch, [{"type": "done", "intent": "x", "step": "y"}])
        r = client.post("/api/chat", json={"prompt": "hi"})
        sid = parse_sse(r.text)[0][1]["stream_id"]
        assert _fake_redis.kv[save_stream.conv_key(sid)] == "42"

    def test_events_buffered_with_sequential_seq(self, monkeypatch, _fake_redis):
        client = self._client_with_conv(
            monkeypatch, [{"type": "token", "text": "a"},
                          {"type": "token", "text": "b"},
                          {"type": "done", "intent": "x", "step": "y"}])
        client.post("/api/chat", json={"prompt": "hi"})
        buf = [json.loads(x) for x in _fake_redis.lists[only_buf_key(_fake_redis)]]
        # conversation(0) + token(1) + token(2) + done(3)
        assert [e["seq"] for e in buf] == [0, 1, 2, 3]
        assert [e["event"] for e in buf] == ["conversation", "token", "token", "done"]
