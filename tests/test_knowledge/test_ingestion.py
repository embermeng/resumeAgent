"""
Ingestion 模块测试
Mock外部API调用（Embedding），测试索引构建逻辑
"""
import json
import pickle
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import patch, MagicMock

import faiss

from src.knowledge.ingestion import BM25Ingestor, VectorDBIngestor


# ============================================================
# BM25Ingestor 测试
# ============================================================

@pytest.fixture
def bm25_ingestor():
    return BM25Ingestor()


@pytest.fixture
def sample_doc_data():
    return {
        "metainfo": {"doc_id": "abc123", "source": "test_doc", "category": "course"},
        "content": {
            "chunks": [
                {"id": 0, "text": "RAG 检索增强生成 核心流程", "length_tokens": 10},
                {"id": 1, "text": "FAISS 向量数据库 余弦距离", "length_tokens": 10},
                {"id": 2, "text": "LangChain LLM 应用开发框架", "length_tokens": 10},
            ]
        },
    }


class TestBM25Ingestor:
    def test_create_bm25_index(self, bm25_ingestor):
        chunks = ["hello world", "test document", "another chunk"]
        index = bm25_ingestor.create_bm25_index(chunks)
        assert index is not None
        # 查询应该有分数输出
        scores = index.get_scores("hello".split())
        assert len(scores) == 3

    def test_process_single(self, bm25_ingestor, sample_doc_data, tmp_path):
        output_dir = tmp_path / "bm25"
        result_path = bm25_ingestor.process_single(sample_doc_data, output_dir)
        assert result_path.exists()
        assert result_path.suffix == ".pkl"

        # 加载并验证
        with open(result_path, "rb") as f:
            index = pickle.load(f)
        scores = index.get_scores("RAG".split())
        assert len(scores) == 3

    def test_process_chunks_dir(self, bm25_ingestor, sample_doc_data, tmp_path):
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "bm25"

        # 写入测试JSON
        with open(chunks_dir / "abc123.json", "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
        pkl_files = list(output_dir.glob("*.pkl"))
        assert len(pkl_files) == 1


# ============================================================
# VectorDBIngestor 测试
# ============================================================

@pytest.fixture
def vector_ingestor():
    return VectorDBIngestor(embedding_provider="dashscope")


class TestVectorDBIngestor:
    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_get_embeddings_mock(self, mock_embed, vector_ingestor):
        """测试embedding获取（Mock）"""
        mock_embed.return_value = [[0.1] * 128, [0.2] * 128]
        embeddings = vector_ingestor._get_embeddings(["text1", "text2"])
        assert len(embeddings) == 2
        assert len(embeddings[0]) == 128

    def test_create_vector_db(self, vector_ingestor):
        """测试FAISS向量索引创建"""
        embeddings = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        index = vector_ingestor._create_vector_db(embeddings)
        assert index.ntotal == 2
        assert index.d == 3

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_document(self, mock_embed, vector_ingestor, sample_doc_data):
        """测试单文档处理"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        index = vector_ingestor._process_document(sample_doc_data)
        assert index.ntotal == 3

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_single(self, mock_embed, vector_ingestor, sample_doc_data, tmp_path):
        """测试单文档保存"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        output_dir = tmp_path / "faiss"
        result_path = vector_ingestor.process_single(sample_doc_data, output_dir)
        assert result_path.exists()
        assert result_path.suffix == ".faiss"

        # 加载验证
        index = faiss.read_index(str(result_path))
        assert index.ntotal == 3

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_chunks_dir(self, mock_embed, vector_ingestor, sample_doc_data, tmp_path):
        """测试批量处理"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "faiss"

        with open(chunks_dir / "abc123.json", "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        vector_ingestor.process_chunks_dir(chunks_dir, output_dir)
        faiss_files = list(output_dir.glob("*.faiss"))
        assert len(faiss_files) == 1

    def test_empty_text_raises_error(self, vector_ingestor):
        """测试空文本抛出异常"""
        with pytest.raises(ValueError):
            vector_ingestor._get_embeddings("")

    def test_empty_list_raises_error(self, vector_ingestor):
        """测试空列表抛出异常"""
        with pytest.raises(ValueError):
            vector_ingestor._get_embeddings([])

    def test_unsupported_provider(self):
        """测试不支持的provider"""
        ingestor = VectorDBIngestor(embedding_provider="unsupported")
        with pytest.raises(ValueError, match="不支持的embedding provider"):
            ingestor._get_embeddings(["test"])
