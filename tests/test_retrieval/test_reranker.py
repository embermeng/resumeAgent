"""
Reranker 模块测试
"""
import pytest
from unittest.mock import patch, MagicMock

from src.retrieval.reranker import LLMReranker


@pytest.fixture
def mock_api():
    with patch("src.retrieval.reranker.APIProcessor") as mock:
        instance = MagicMock()
        instance.default_model = "test-model"
        mock.return_value = instance
        yield instance


@pytest.fixture
def reranker(mock_api):
    return LLMReranker(provider="dashscope")


class TestLLMReranker:
    def test_get_rank_single_block(self, reranker, mock_api):
        """测试单文档评分"""
        mock_api.send_message.return_value = {
            "relevance_score": 0.8,
            "reasoning": "高度相关",
        }
        result = reranker.get_rank_for_single_block("RAG查询", "RAG检索增强生成的核心流程...")
        assert result["relevance_score"] == 0.8

    def test_get_rank_single_block_error(self, reranker, mock_api):
        """测试单文档评分异常"""
        mock_api.send_message.side_effect = Exception("API Error")
        result = reranker.get_rank_for_single_block("query", "text")
        assert result["relevance_score"] == 0.0

    def test_get_rank_multiple_blocks(self, reranker, mock_api):
        """测试批量评分"""
        mock_api.send_message.return_value = {
            "block_rankings": [
                {"relevance_score": 0.9, "reasoning": "非常相关"},
                {"relevance_score": 0.3, "reasoning": "不太相关"},
            ]
        }
        result = reranker.get_rank_for_multiple_blocks("query", ["text1", "text2"])
        assert len(result["block_rankings"]) == 2

    def test_get_rank_multiple_blocks_error(self, reranker, mock_api):
        """测试批量评分异常"""
        mock_api.send_message.side_effect = Exception("API Error")
        result = reranker.get_rank_for_multiple_blocks("query", ["text1", "text2"])
        assert len(result["block_rankings"]) == 2
        assert result["block_rankings"][0]["relevance_score"] == 0.0

    def test_rerank_documents_batch(self, reranker, mock_api):
        """测试批量重排"""
        mock_api.send_message.return_value = {
            "block_rankings": [
                {"relevance_score": 0.9, "reasoning": "高相关"},
                {"relevance_score": 0.3, "reasoning": "低相关"},
            ]
        }
        documents = [
            {"text": "文档1", "distance": 0.5},
            {"text": "文档2", "distance": 0.8},
        ]
        results = reranker.rerank_documents("query", documents, batch_size=2)
        assert len(results) == 2
        assert "combined_score" in results[0]
        # 结果应按combined_score降序排列
        assert results[0]["combined_score"] >= results[1]["combined_score"]

    def test_rerank_documents_single(self, reranker, mock_api):
        """测试逐条重排"""
        mock_api.send_message.return_value = {
            "relevance_score": 0.7,
            "reasoning": "较相关",
        }
        documents = [
            {"text": "文档A", "distance": 0.6},
            {"text": "文档B", "distance": 0.9},
        ]
        results = reranker.rerank_documents("query", documents, batch_size=1)
        assert len(results) == 2
        assert all("combined_score" in r for r in results)

    def test_rerank_empty_documents(self, reranker, mock_api):
        """测试空文档列表"""
        results = reranker.rerank_documents("query", [])
        assert results == []

    def test_rerank_sorted_desc(self, reranker, mock_api):
        """测试结果降序排列"""
        mock_api.send_message.return_value = {
            "block_rankings": [
                {"relevance_score": 0.1, "reasoning": "低"},
                {"relevance_score": 0.9, "reasoning": "高"},
                {"relevance_score": 0.5, "reasoning": "中"},
            ]
        }
        documents = [
            {"text": "低分", "distance": 0.3},
            {"text": "高分", "distance": 0.9},
            {"text": "中分", "distance": 0.6},
        ]
        results = reranker.rerank_documents("query", documents, batch_size=3)
        scores = [r["combined_score"] for r in results]
        assert scores == sorted(scores, reverse=True)
