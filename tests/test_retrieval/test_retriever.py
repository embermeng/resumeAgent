"""
Retriever 模块测试
"""
import json
import pickle
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

import faiss

from src.retrieval.retriever import BM25Retriever, VectorRetriever, HybridRetriever, read_faiss_index
from src.knowledge.tokenizer import tokenize


class TestReadFaissIndex:
    def test_read_chinese_path(self, tmp_path):
        """中文路径下的faiss索引应能正常加载（FAISS C++层fopen不支持中文路径，需临时文件降级）"""
        chinese_dir = tmp_path / "中文目录"
        chinese_dir.mkdir()
        index_path = chinese_dir / "测试索引.faiss"

        dim = 8
        index = faiss.IndexFlatIP(dim)
        index.add(np.random.rand(3, dim).astype(np.float32))
        # 写入也走临时文件，避免中文路径写入失败
        import tempfile, shutil
        with tempfile.NamedTemporaryFile(suffix=".faiss", delete=False) as tmp:
            tmp_path_str = tmp.name
        faiss.write_index(index, tmp_path_str)
        shutil.move(tmp_path_str, str(index_path))

        loaded = read_faiss_index(index_path)
        assert loaded.ntotal == 3
        assert loaded.d == dim


@pytest.fixture
def sample_doc_data():
    return {
        "metainfo": {"doc_id": "doc1", "source": "RAG课程", "category": "course"},
        "content": {
            "chunks": [
                {"id": 0, "text": "RAG 检索增强生成 核心流程 向量检索"},
                {"id": 1, "text": "FAISS 向量数据库 余弦距离 IndexFlatIP"},
                {"id": 2, "text": "LangChain LLM 应用开发 链式调用"},
            ]
        },
    }


@pytest.fixture
def doc_with_project():
    return {
        "metainfo": {"doc_id": "doc2", "source": "项目A", "category": "project"},
        "content": {
            "chunks": [
                {"id": 0, "text": "项目A 技术栈 Python FastAPI"},
                {"id": 1, "text": "项目亮点 性能优化 缓存策略"},
            ]
        },
    }


@pytest.fixture
def setup_bm25_env(tmp_path, sample_doc_data, doc_with_project):
    """设置BM25检索环境"""
    docs_dir = tmp_path / "docs"
    bm25_dir = tmp_path / "bm25"
    docs_dir.mkdir()
    bm25_dir.mkdir()

    # 保存文档
    for doc in [sample_doc_data, doc_with_project]:
        doc_id = doc["metainfo"]["doc_id"]
        with open(docs_dir / f"{doc_id}.json", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)

    # 创建BM25索引（必须与生产代码使用同一分词器）
    from rank_bm25 import BM25Okapi
    for doc in [sample_doc_data, doc_with_project]:
        doc_id = doc["metainfo"]["doc_id"]
        texts = [c["text"] for c in doc["content"]["chunks"]]
        tokenized = [tokenize(t) for t in texts]
        index = BM25Okapi(tokenized)
        with open(bm25_dir / f"{doc_id}.pkl", "wb") as f:
            pickle.dump(index, f)

    return docs_dir, bm25_dir


@pytest.fixture
def setup_chinese_bm25_env(tmp_path):
    """设置中文BM25检索环境（验证jieba分词链路）"""
    docs_dir = tmp_path / "docs"
    bm25_dir = tmp_path / "bm25"
    docs_dir.mkdir()
    bm25_dir.mkdir()

    doc = {
        "metainfo": {"doc_id": "cn1", "source": "AI服务核心", "category": "course"},
        "content": {
            "chunks": [
                {"id": 0, "text": "推测解码是一种加速大模型推理的技术，由草稿模型生成候选token，目标模型并行验证。"},
                {"id": 1, "text": "高并发场景下需要做好负载均衡和限流降级。"},
                {"id": 2, "text": "性能监控需要关注吞吐量和延迟指标。"},
            ]
        },
    }
    with open(docs_dir / "cn1.json", "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)

    from rank_bm25 import BM25Okapi
    texts = [c["text"] for c in doc["content"]["chunks"]]
    index = BM25Okapi([tokenize(t) for t in texts])
    with open(bm25_dir / "cn1.pkl", "wb") as f:
        pickle.dump(index, f)

    return docs_dir, bm25_dir


@pytest.fixture
def setup_vector_env(tmp_path, sample_doc_data, doc_with_project):
    """设置向量检索环境"""
    docs_dir = tmp_path / "docs"
    vec_dir = tmp_path / "vectors"
    docs_dir.mkdir(exist_ok=True)
    vec_dir.mkdir()

    for doc in [sample_doc_data, doc_with_project]:
        doc_id = doc["metainfo"]["doc_id"]
        with open(docs_dir / f"{doc_id}.json", "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)

        # 创建FAISS索引
        dim = 8
        n_chunks = len(doc["content"]["chunks"])
        embeddings = np.random.rand(n_chunks, dim).astype(np.float32)
        index = faiss.IndexFlatIP(dim)
        index.add(embeddings)
        faiss.write_index(index, str(vec_dir / f"{doc_id}.faiss"))

    return docs_dir, vec_dir


class TestBM25Retriever:
    def test_retrieve_basic(self, setup_bm25_env):
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("RAG 向量检索", top_n=3)
        assert len(results) > 0
        assert "text" in results[0]
        assert "score" in results[0]

    def test_retrieve_by_category(self, setup_bm25_env):
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("项目", category="project", top_n=5)
        for r in results:
            assert r["doc_id"] == "doc2"

    def test_retrieve_by_doc_id(self, setup_bm25_env):
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("RAG", doc_id="doc1", top_n=2)
        assert len(results) <= 2
        for r in results:
            assert r["doc_id"] == "doc1"

    def test_retrieve_by_doc_ids(self, setup_bm25_env):
        """doc_ids过滤：分层检索定向召回，只返回指定文档的结果"""
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        # 查询词在两个文档都可能命中，但限定doc_ids后只能来自doc1
        results = retriever.retrieve("项目 技术栈 RAG", doc_ids=["doc1"], top_n=10)
        for r in results:
            assert r["doc_id"] == "doc1"

    def test_index_and_doc_cached(self, setup_bm25_env):
        """索引/文档应带内存缓存：多次检索不重复反序列化（降低查询延迟）"""
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)

        real_load = pickle.load
        with patch("src.retrieval.retriever.pickle.load", side_effect=real_load) as mock_load:
            retriever.retrieve("RAG 向量检索", top_n=3)
            retriever.retrieve("FAISS 余弦距离", top_n=3)
            # 两个文档的pkl各只应加载一次
            assert mock_load.call_count == 2
        assert len(retriever._index_cache) == 2
        assert len(retriever._doc_cache) == 2

    def test_retrieve_empty_result(self, setup_bm25_env):
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("完全不相关的xyzquery", doc_id="nonexistent")
        assert results == []

    def test_retrieve_chinese(self, setup_chinese_bm25_env):
        """中文查询应能通过jieba分词命中相关分块（回归：split()对中文无效）"""
        docs_dir, bm25_dir = setup_chinese_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("推测解码是不是草稿模型配合打分模型？", top_n=3)
        assert len(results) > 0
        assert "推测解码" in results[0]["text"]
        assert results[0]["score"] > 0

    def test_retrieve_zero_score_filtered(self, setup_chinese_bm25_env):
        """无任何词匹配时（全部零分）不应返回无关分块"""
        docs_dir, bm25_dir = setup_chinese_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("zzz qqq xxx", top_n=3)
        assert results == []


class TestVectorRetriever:
    @patch("src.retrieval.retriever.VectorRetriever._get_embedding")
    def test_retrieve_basic(self, mock_embed, setup_vector_env):
        docs_dir, vec_dir = setup_vector_env
        mock_embed.return_value = [0.1] * 8
        retriever = VectorRetriever(vec_dir, docs_dir)
        results = retriever.retrieve("RAG", top_n=3)
        assert len(results) > 0
        assert "text" in results[0]
        assert "distance" in results[0]

    @patch("src.retrieval.retriever.VectorRetriever._get_embedding")
    def test_retrieve_by_category(self, mock_embed, setup_vector_env):
        docs_dir, vec_dir = setup_vector_env
        mock_embed.return_value = [0.1] * 8
        retriever = VectorRetriever(vec_dir, docs_dir)
        results = retriever.retrieve("项目", category="project", top_n=5)
        for r in results:
            assert r["doc_id"] == "doc2"

    @patch("src.retrieval.retriever.VectorRetriever._get_embedding")
    def test_retrieve_by_doc_ids(self, mock_embed, setup_vector_env):
        """doc_ids过滤：分层检索定向召回"""
        docs_dir, vec_dir = setup_vector_env
        mock_embed.return_value = [0.1] * 8
        retriever = VectorRetriever(vec_dir, docs_dir)
        results = retriever.retrieve("项目", top_n=10, doc_ids=["doc1"])
        assert len(results) > 0
        for r in results:
            assert r["doc_id"] == "doc1"


class TestHybridRetriever:
    # 缓存隔离由 tests/conftest.py 的全局 autouse fixture 统一负责

    @patch("src.retrieval.retriever.VectorRetriever._get_embedding")
    def test_hybrid_retrieve(self, mock_embed, setup_bm25_env, setup_vector_env):
        docs_dir, bm25_dir = setup_bm25_env
        _, vec_dir = setup_vector_env
        mock_embed.return_value = [0.1] * 8

        retriever = HybridRetriever(vec_dir, bm25_dir, docs_dir)
        results = retriever.retrieve("RAG 检索", top_n=3)
        assert isinstance(results, list)
        # 结果应该有score字段
        for r in results:
            assert "score" in r
            assert "text" in r

    @patch("src.retrieval.retriever.VectorRetriever._get_embedding")
    def test_hybrid_retrieve_by_doc_ids(self, mock_embed, setup_bm25_env, setup_vector_env):
        """混合检索doc_ids过滤：向量和BM25两路都应限定在指定文档"""
        docs_dir, bm25_dir = setup_bm25_env
        _, vec_dir = setup_vector_env
        mock_embed.return_value = [0.1] * 8

        retriever = HybridRetriever(vec_dir, bm25_dir, docs_dir)
        results = retriever.retrieve("项目 技术栈 RAG", top_n=10, doc_ids=["doc2"])
        assert len(results) > 0
        for r in results:
            assert r["doc_id"] == "doc2"


class TestHybridCacheAside:
    """HybridRetriever.retrieve 的 cache-aside 行为(mock redis 与底层检索)"""

    @pytest.fixture(autouse=True)
    def _enable_cache(self, monkeypatch):
        """同上:patch retriever 实际读取的 settings 对象,保证开关生效"""
        monkeypatch.setattr(
            "src.retrieval.retriever.settings.redis.cache_enabled", True)

    @pytest.fixture
    def retriever(self, tmp_path):
        """空索引目录即可:底层检索被 mock,不依赖真实 faiss/bm25"""
        docs = tmp_path / "docs"; docs.mkdir()
        bm25 = tmp_path / "bm25"; bm25.mkdir()
        vec = tmp_path / "vec"; vec.mkdir()
        return HybridRetriever(vec, bm25, docs)

    def test_hit_skips_underlying_retrieve(self, retriever):
        """命中缓存应直接返回,不打底层检索、不回写"""
        cached = [{"text": "cached", "score": 1.0, "source": "s", "doc_id": "d", "chunk_id": 0}]
        with patch("src.retrieval.retriever.cache_get", return_value=cached), \
             patch.object(HybridRetriever, "_retrieve_uncached") as mu, \
             patch("src.retrieval.retriever.cache_set") as ms:
            out = retriever.retrieve("q")
        assert out == cached
        mu.assert_not_called()
        ms.assert_not_called()

    def test_miss_backfills_with_long_ttl(self, retriever):
        """未命中应检索并回写,非空结果用 cache_ttl+jitter"""
        from src.config import get_config
        results = [{"text": "fresh", "score": 1.0, "source": "s", "doc_id": "d", "chunk_id": 0}]
        with patch("src.retrieval.retriever.cache_get", return_value=None), \
             patch.object(HybridRetriever, "_retrieve_uncached", return_value=results) as mu, \
             patch("src.retrieval.retriever.cache_set") as ms, \
             patch("src.retrieval.retriever.random.randint", return_value=0):
            out = retriever.retrieve("q")
        assert out == results
        mu.assert_called_once()
        ms.assert_called_once()
        assert ms.call_args[0][2] == get_config().redis.cache_ttl

    def test_empty_result_uses_short_ttl(self, retriever):
        """防穿透回归:空结果必须用 empty_ttl,不能被长 TTL 覆盖"""
        from src.config import get_config
        with patch("src.retrieval.retriever.cache_get", return_value=None), \
             patch.object(HybridRetriever, "_retrieve_uncached", return_value=[]), \
             patch("src.retrieval.retriever.cache_set") as ms:
            out = retriever.retrieve("q")
        assert out == []
        ms.assert_called_once()
        assert ms.call_args[0][2] == get_config().redis.empty_ttl

    def test_redis_down_degrades_to_uncached(self, retriever):
        """Redis 整体不可用时检索应静默降级正常返回,不中断"""
        results = [{"text": "x", "score": 1.0, "source": "s", "doc_id": "d", "chunk_id": 0}]
        with patch("src.cache.retrieval_cache.get_sync_client", side_effect=Exception("down")), \
             patch.object(HybridRetriever, "_retrieve_uncached", return_value=results):
            out = retriever.retrieve("q")
        assert out == results

    @patch("src.retrieval.retriever.VectorRetriever._get_embedding")
    def test_real_pipeline_results_write_back(self, mock_embed, setup_bm25_env, setup_vector_env):
        """回归:真检索管线产物(含 numpy score)必须能序列化回写,
        防 json.dumps TypeError 被降级吞掉导致缓存静默失效"""
        docs_dir, bm25_dir = setup_bm25_env
        _, vec_dir = setup_vector_env
        mock_embed.return_value = [0.1] * 8
        retriever = HybridRetriever(vec_dir, bm25_dir, docs_dir)

        client = MagicMock()
        client.get.return_value = None  # 强制 miss,走回写路径
        with patch("src.cache.retrieval_cache.get_sync_client", return_value=client):
            results = retriever.retrieve("RAG 检索", top_n=3)
        assert len(results) > 0
        client.set.assert_called_once()
        # 写入值必须是可反解的 JSON 字符串
        stored = json.loads(client.set.call_args[0][1])
        assert isinstance(stored, list) and len(stored) == len(results)
