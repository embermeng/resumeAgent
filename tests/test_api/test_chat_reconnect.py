"""GET /api/chat/{stream_id}/stream 恢复端点测试。

覆盖:归属 404、Last-Event-ID 优先、query after、无参默认全量、脏值不 500、重放 seq>after。
patch Redis 为 FakeRedis + monkeypatch check_conversation,不依赖真实 Redis/DB。

踩坑记录:TestClient 保持默认 raise_server_exceptions=True。若设为 False,
SSE StreamingResponse 在跨文件全量连跑时会被误判成 500(单跑却正常),极难排查;
用默认 True 时服务端异常会正常 re-raise,反而更易定位。
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.deps import get_agent_service
from src.api.routers import chat as chat_mod
from src.api.routers.utils import save_stream
from src.auth.security import get_current_user
from tests.test_api.helpers import FakeRedis, event_names, parse_sse


@pytest.fixture(autouse=True)
def fake_redis():
    """chat.py 与 save_stream.py 各自 import get_sync_client,两处都 patch 到同一 FakeRedis。"""
    fr = FakeRedis()
    with patch.object(chat_mod, "get_sync_client", return_value=fr), \
         patch.object(save_stream, "get_sync_client", return_value=fr):
        yield fr


@pytest.fixture(autouse=True)
def _owner(monkeypatch):
    """默认放行归属校验(404 用例自行覆盖为 False)。"""
    monkeypatch.setattr("src.api.routers.chat.check_conversation", lambda *a, **k: True)


@pytest.fixture
def client():
    app = create_app()
    app.dependency_overrides[get_agent_service] = lambda: MagicMock()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    # 保持默认 raise_server_exceptions=True(见模块 docstring 踩坑记录)
    return TestClient(app)


def _seed(fr, sid, conv_id=7, events=None):
    """预置一个已结束的流:归属映射 + 事件缓冲(默认 conversation/token/done 三帧)。"""
    fr.setex(save_stream.conv_key(sid), 3600, str(conv_id))
    if events is None:
        events = [
            (0, "conversation", {"conversation_id": conv_id, "stream_id": sid}),
            (1, "token", {"text": "你好"}),
            (2, "done", {"intent": "x", "step": "interrupted"}),
        ]
    for seq, ev, data in events:
        save_stream.buffer_event(sid, seq, ev, data, 3600)


class TestReconnectAuth:
    def test_404_when_no_mapping(self, client, fake_redis):
        r = client.get("/api/chat/nonexistent/stream")
        assert r.status_code == 404
        assert r.json()["detail"] == "stream not found"

    def test_404_when_not_owner(self, client, fake_redis, monkeypatch):
        _seed(fake_redis, "sid1", conv_id=7)
        monkeypatch.setattr("src.api.routers.chat.check_conversation", lambda cid, uid: False)
        r = client.get("/api/chat/sid1/stream")
        assert r.status_code == 404


class TestReconnectReplay:
    def test_no_header_no_query_replays_all(self, client, fake_redis):
        """无 Last-Event-ID、无 after → lid 兜底 -1 → 从 seq=0 全量重放。

        回归保护:曾因 int(headers.get(...)) 对 None 抛 TypeError 且只 except ValueError 而 500,
        现改为 raw is None 时用 query + int 带 (ValueError, TypeError) 兜底。
        """
        _seed(fake_redis, "sid1")
        r = client.get("/api/chat/sid1/stream")
        assert r.status_code == 200
        assert event_names(parse_sse(r.text)) == ["conversation", "token", "done"]

    def test_query_after_filters(self, client, fake_redis):
        """无 Last-Event-ID 头 + ?after=1 → 只重放 seq>1(done)。

        回归保护:无头时必须回落到 query after(而非被兜底 -1 覆盖成全量重放)。
        """
        _seed(fake_redis, "sid1")
        r = client.get("/api/chat/sid1/stream?after=1")
        assert r.status_code == 200
        assert event_names(parse_sse(r.text)) == ["done"]

    def test_last_event_id_prior_over_query(self, client, fake_redis):
        """Last-Event-ID=1 优先于 ?after=0 → 只重放 seq>1(done)。"""
        _seed(fake_redis, "sid1")
        r = client.get("/api/chat/sid1/stream?after=0", headers={"Last-Event-ID": "1"})
        assert r.status_code == 200
        assert event_names(parse_sse(r.text)) == ["done"]

    def test_dirty_last_event_id_not_500(self, client, fake_redis):
        """脏 Last-Event-ID 应兜底 -1 全量重放,不该 500。"""
        _seed(fake_redis, "sid1")
        r = client.get("/api/chat/sid1/stream", headers={"Last-Event-ID": "abc"})
        assert r.status_code == 200
        assert event_names(parse_sse(r.text)) == ["conversation", "token", "done"]
