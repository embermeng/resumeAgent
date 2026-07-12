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

from src.retrieval.retriever import BM25Retriever, VectorRetriever, HybridRetriever


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

    # 创建BM25索引
    from rank_bm25 import BM25Okapi
    for doc in [sample_doc_data, doc_with_project]:
        doc_id = doc["metainfo"]["doc_id"]
        texts = [c["text"] for c in doc["content"]["chunks"]]
        tokenized = [t.split() for t in texts]
        index = BM25Okapi(tokenized)
        with open(bm25_dir / f"{doc_id}.pkl", "wb") as f:
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

    def test_retrieve_empty_result(self, setup_bm25_env):
        docs_dir, bm25_dir = setup_bm25_env
        retriever = BM25Retriever(bm25_dir, docs_dir)
        results = retriever.retrieve("完全不相关的xyzquery", doc_id="nonexistent")
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


class TestHybridRetriever:
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
