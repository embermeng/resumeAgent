"""
Ingestion 模块测试
Mock外部API调用（Embedding），测试索引构建逻辑
"""
import json
import os
import pickle
import time
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

    def test_process_chunks_dir_incremental_skip(self, bm25_ingestor, sample_doc_data, tmp_path):
        """增量构建：索引已存在且未过期时跳过，不重建"""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "bm25"

        chunk_path = chunks_dir / "abc123.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
        pkl_path = output_dir / "abc123.pkl"

        # 让索引比分块新（未过期），第二次运行应跳过
        os.utime(chunk_path, (time.time() - 10, time.time() - 10))
        with patch.object(BM25Ingestor, "create_bm25_index", wraps=bm25_ingestor.create_bm25_index) as mock_create:
            bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
            assert mock_create.call_count == 0
        assert len(list(output_dir.glob("*.pkl"))) == 1

    def test_process_chunks_dir_stale_rebuild(self, bm25_ingestor, sample_doc_data, tmp_path):
        """分块文件比索引新时重建"""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "bm25"

        chunk_path = chunks_dir / "abc123.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
        pkl_path = output_dir / "abc123.pkl"

        # 模拟分块更新（mtime变新）→ 应重建
        os.utime(chunk_path, (time.time() + 10, time.time() + 10))
        with patch.object(BM25Ingestor, "create_bm25_index", wraps=bm25_ingestor.create_bm25_index) as mock_create:
            bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
            assert mock_create.call_count == 1  # 过期重建
        assert pkl_path.exists()

    def test_process_chunks_dir_force_rebuild(self, bm25_ingestor, sample_doc_data, tmp_path):
        """force=True时全量重建"""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "bm25"

        chunk_path = chunks_dir / "abc123.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
        pkl_path = output_dir / "abc123.pkl"
        os.utime(pkl_path, (time.time() + 10, time.time() + 10))  # 索引未过期

        with patch.object(BM25Ingestor, "create_bm25_index", wraps=bm25_ingestor.create_bm25_index) as mock_create:
            bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
            assert mock_create.call_count == 0  # 增量跳过
            bm25_ingestor.process_chunks_dir(chunks_dir, output_dir, force=True)
            assert mock_create.call_count == 1  # force重建

    def test_process_chunks_dir_prune_orphan(self, bm25_ingestor, sample_doc_data, tmp_path):
        """prune清理孤儿索引"""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "bm25"
        output_dir.mkdir()

        with open(chunks_dir / "abc123.json", "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        # 预先放一个孤儿索引（doc_id在分块目录中不存在）
        orphan = output_dir / "orphan999.pkl"
        orphan.write_bytes(b"fake")

        bm25_ingestor.process_chunks_dir(chunks_dir, output_dir, prune=True)
        assert not orphan.exists()  # 孤儿被清理
        assert (output_dir / "abc123.pkl").exists()  # 正常索引保留

    def test_process_chunks_dir_no_tmp_leftover(self, bm25_ingestor, sample_doc_data, tmp_path):
        """原子写入不残留临时文件"""
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "bm25"

        with open(chunks_dir / "abc123.json", "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        bm25_ingestor.process_chunks_dir(chunks_dir, output_dir)
        assert len(list(output_dir.glob("*.tmp"))) == 0


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

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_chunks_dir_incremental_skip(self, mock_embed, vector_ingestor, sample_doc_data, tmp_path):
        """增量构建：索引已存在且未过期时跳过，不调embedding API"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "faiss"

        chunk_path = chunks_dir / "abc123.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        vector_ingestor.process_chunks_dir(chunks_dir, output_dir)
        assert mock_embed.call_count == 1

        # 索引未过期，第二次运行应跳过，不再调embedding
        faiss_path = output_dir / "abc123.faiss"
        os.utime(faiss_path, (time.time() + 10, time.time() + 10))
        vector_ingestor.process_chunks_dir(chunks_dir, output_dir)
        assert mock_embed.call_count == 1

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_chunks_dir_stale_rebuild(self, mock_embed, vector_ingestor, sample_doc_data, tmp_path):
        """分块文件比索引新时重建（重新调embedding）"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "faiss"

        chunk_path = chunks_dir / "abc123.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        vector_ingestor.process_chunks_dir(chunks_dir, output_dir)
        assert mock_embed.call_count == 1

        # 模拟分块更新 → 应重建
        os.utime(chunk_path, (time.time() + 10, time.time() + 10))
        vector_ingestor.process_chunks_dir(chunks_dir, output_dir)
        assert mock_embed.call_count == 2

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_chunks_dir_force_rebuild(self, mock_embed, vector_ingestor, sample_doc_data, tmp_path):
        """force=True时全量重建（重新调embedding）"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "faiss"

        chunk_path = chunks_dir / "abc123.json"
        with open(chunk_path, "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        vector_ingestor.process_chunks_dir(chunks_dir, output_dir)
        assert mock_embed.call_count == 1

        # 索引未过期，但force强制重建
        os.utime(chunk_path, (time.time() - 10, time.time() - 10))
        vector_ingestor.process_chunks_dir(chunks_dir, output_dir, force=True)
        assert mock_embed.call_count == 2

    @patch("src.knowledge.ingestion.VectorDBIngestor._get_embeddings")
    def test_process_chunks_dir_prune_orphan(self, mock_embed, vector_ingestor, sample_doc_data, tmp_path):
        """prune清理孤儿索引"""
        mock_embed.return_value = [[0.1] * 128 for _ in range(3)]
        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        output_dir = tmp_path / "faiss"
        output_dir.mkdir()

        with open(chunks_dir / "abc123.json", "w", encoding="utf-8") as f:
            json.dump(sample_doc_data, f, ensure_ascii=False)

        orphan = output_dir / "orphan999.faiss"
        orphan.write_bytes(b"fake")

        vector_ingestor.process_chunks_dir(chunks_dir, output_dir, prune=True)
        assert not orphan.exists()
        assert (output_dir / "abc123.faiss").exists()

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
