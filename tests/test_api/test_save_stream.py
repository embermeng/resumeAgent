"""save_stream.py 单元测试:事件缓冲写入/读取、终态判断、归属映射,以及 Redis 降级。

patch save_stream.get_sync_client 为 FakeRedis,不依赖真实 Redis。
降级用例用 MagicMock 让 redis 命令抛 RedisError,验证不向上冒泡(对话不被拖垮)。
"""
import json
from unittest.mock import MagicMock, patch

import pytest
import redis as redis_lib

from src.api.routers.utils import save_stream
from src.api.routers.utils.save_stream import (
    buffer_event,
    conv_key,
    last_is_terminal,
    read_buffer,
    save_conv_mapping,
)
from tests.test_api.helpers import FakeRedis


@pytest.fixture
def fake():
    fr = FakeRedis()
    with patch.object(save_stream, "get_sync_client", return_value=fr):
        yield fr


def _raise_client():
    """所有 redis 命令都抛 RedisError 的 client,用于降级测试。"""
    client = MagicMock()
    client.rpush.side_effect = redis_lib.RedisError("down")
    client.lrange.side_effect = redis_lib.RedisError("down")
    client.lindex.side_effect = redis_lib.RedisError("down")
    client.setex.side_effect = redis_lib.RedisError("down")
    return client


class TestBufferEvent:
    def test_writes_seq_event_data(self, fake):
        buffer_event("sid1", 0, "conversation",
                     {"conversation_id": 1, "stream_id": "sid1"}, 3600)
        raw = fake.lists["chat:stream:sid1"]
        assert len(raw) == 1
        assert json.loads(raw[0]) == {
            "seq": 0, "event": "conversation",
            "data": {"conversation_id": 1, "stream_id": "sid1"},
        }

    def test_rpush_appends_in_seq_order(self, fake):
        for i in range(3):
            buffer_event("sid1", i, "token", {"text": str(i)}, 3600)
        seqs = [json.loads(x)["seq"] for x in fake.lists["chat:stream:sid1"]]
        assert seqs == [0, 1, 2]

    def test_refreshes_ttl_each_write(self, fake):
        buffer_event("sid1", 0, "token", {"text": "a"}, 3600)
        buffer_event("sid1", 1, "token", {"text": "b"}, 3600)
        assert fake.ttls["chat:stream:sid1"] == 3600

    def test_chinese_not_escaped(self, fake):
        """ensure_ascii=False:中文原样存,便于重放时直读。"""
        buffer_event("sid1", 0, "token", {"text": "检索增强"}, 3600)
        assert "检索增强" in fake.lists["chat:stream:sid1"][0]

    def test_redis_error_swallowed(self):
        with patch.object(save_stream, "get_sync_client", return_value=_raise_client()):
            buffer_event("sid1", 0, "token", {"text": "a"}, 3600)  # 不应抛


class TestReadBuffer:
    def test_filters_seq_greater_than_after(self, fake):
        for i in range(5):
            buffer_event("sid1", i, "token", {"text": str(i)}, 3600)
        assert [e["seq"] for e in read_buffer("sid1", 2)] == [3, 4]

    def test_after_minus_one_returns_all(self, fake):
        for i in range(3):
            buffer_event("sid1", i, "token", {"text": str(i)}, 3600)
        assert len(read_buffer("sid1", -1)) == 3

    def test_empty_key_returns_empty(self, fake):
        assert read_buffer("nope", -1) == []

    def test_redis_error_returns_empty(self):
        with patch.object(save_stream, "get_sync_client", return_value=_raise_client()):
            assert read_buffer("sid1", -1) == []


class TestLastIsTerminal:
    def test_done_is_terminal(self, fake):
        buffer_event("sid1", 0, "done", {"step": "x"}, 3600)
        assert last_is_terminal("sid1") is True

    def test_error_is_terminal(self, fake):
        buffer_event("sid1", 0, "error", {"message": "x"}, 3600)
        assert last_is_terminal("sid1") is True

    def test_interrupted_done_is_terminal(self, fake):
        """断线补的 done(step=interrupted) 也算终态,重连读到即收流。"""
        buffer_event("sid1", 0, "done", {"step": "interrupted"}, 3600)
        assert last_is_terminal("sid1") is True

    def test_token_not_terminal(self, fake):
        buffer_event("sid1", 0, "token", {"text": "x"}, 3600)
        assert last_is_terminal("sid1") is False

    def test_only_last_event_matters(self, fake):
        buffer_event("sid1", 0, "done", {"step": "x"}, 3600)
        buffer_event("sid1", 1, "token", {"text": "x"}, 3600)
        assert last_is_terminal("sid1") is False  # 末尾是 token

    def test_empty_is_terminal(self, fake):
        """空缓冲返回 True:避免 finally 往空缓冲补孤立终态帧。"""
        assert last_is_terminal("nope") is True

    def test_redis_error_returns_true(self):
        with patch.object(save_stream, "get_sync_client", return_value=_raise_client()):
            assert last_is_terminal("sid1") is True


class TestSaveConvMapping:
    def test_writes_conv_id_as_str(self, fake):
        save_conv_mapping("sid1", 42, 3600)
        assert fake.kv[conv_key("sid1")] == "42"
        assert fake.ttls[conv_key("sid1")] == 3600

    def test_conv_key_format(self):
        assert conv_key("abc") == "chat:stream:abc:conv"

    def test_redis_error_swallowed(self):
        with patch.object(save_stream, "get_sync_client", return_value=_raise_client()):
            save_conv_mapping("sid1", 42, 3600)  # 不应抛
