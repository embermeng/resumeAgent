"""
检索结果 Redis 缓存层测试（src/cache/retrieval_cache.py）
mock redis 客户端，不依赖真实 Redis 实例
"""
import json
from unittest.mock import patch, MagicMock

from src.cache.retrieval_cache import build_key, cache_get, cache_set
from src.config import get_config


class TestBuildKey:
    def test_same_input_same_key(self):
        k1 = build_key("RAG 检索", "course", 5, 0.6, None)
        k2 = build_key("RAG 检索", "course", 5, 0.6, None)
        assert k1 == k2

    def test_whitespace_normalized(self):
        """空白差异应归一化为同一 key"""
        k1 = build_key("RAG   检索", "course", 5, 0.6, None)
        k2 = build_key(" RAG 检索 ", "course", 5, 0.6, None)
        assert k1 == k2

    def test_different_params_different_key(self):
        """所有影响结果的入参都应进 key，漏一个就会串味"""
        base = build_key("q", "course", 5, 0.6, None)
        assert build_key("q", "project", 5, 0.6, None) != base
        assert build_key("q", "course", 10, 0.6, None) != base
        assert build_key("q", "course", 5, 0.8, None) != base
        assert build_key("q", "course", 5, 0.6, ["d1"]) != base

    def test_doc_ids_order_insensitive(self):
        """doc_ids 顺序不同应视为同一查询（内部 sorted）"""
        k1 = build_key("q", None, 5, 0.6, ["d2", "d1"])
        k2 = build_key("q", None, 5, 0.6, ["d1", "d2"])
        assert k1 == k2

    def test_key_has_prefix(self):
        k = build_key("q", None, 5, 0.6, None)
        assert k.startswith(get_config().redis.key_prefix)


class TestCacheGet:
    def test_hit_returns_list(self):
        payload = [{"text": "a", "score": 1.0}]
        client = MagicMock()
        client.get.return_value = json.dumps(payload)
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            assert cache_get("k") == payload

    def test_miss_returns_none(self):
        client = MagicMock()
        client.get.return_value = None
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            assert cache_get("k") is None

    def test_cached_empty_list_is_hit_not_miss(self):
        """缓存的空列表应返回 [](命中) 而非 None(miss)——防穿透的关键区分"""
        client = MagicMock()
        client.get.return_value = "[]"
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            assert cache_get("k") == []

    def test_redis_error_degrades_to_none(self):
        """redis 读失败应降级为 miss(None)，不抛异常"""
        client = MagicMock()
        client.get.side_effect = Exception("redis down")
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            assert cache_get("k") is None


class TestCacheSet:
    def test_set_with_ttl(self):
        client = MagicMock()
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            cache_set("k", [{"text": "a"}], 60)
        client.set.assert_called_once()
        assert client.set.call_args.kwargs.get("ex") == 60

    def test_redis_error_swallowed(self):
        """redis 写失败应静默降级，不抛异常打断检索"""
        client = MagicMock()
        client.set.side_effect = Exception("redis down")
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            cache_set("k", [{"text": "a"}], 60)  # 不应抛

    def test_set_serializes_numpy_scores(self):
        """回归:检索结果 score 为 numpy float32/float64(faiss/rank_bm25 产物),
        json.dumps 不得抛 TypeError 导致回写静默失败"""
        import numpy as np
        client = MagicMock()
        payload = [
            {"text": "a", "score": np.float32(0.5)},
            {"text": "b", "score": np.float64(0.7)},
        ]
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            cache_set("k", payload, 60)
        client.set.assert_called_once()
