"""
重排序模块
使用LLM对检索结果进行重排序
参考 RAG-cy/src/reranking.py 适配
"""
import logging
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor

from src.api_client import APIProcessor

_log = logging.getLogger(__name__)

# 重排序提示词
RERANK_SINGLE_PROMPT = """你是一个文本相关性评估专家。请评估以下查询和文本块的相关性。

请输出一个JSON对象，包含：
- relevance_score: 0-1之间的相关性分数（1为最相关）
- reasoning: 简短的评分理由

查询: "{query}"

文本块:
"""

RERANK_MULTIPLE_PROMPT = """你是一个文本相关性评估专家。请评估以下查询与多个文本块的相关性。

请输出一个JSON对象，包含 block_rankings 数组，每个元素有：
- relevance_score: 0-1之间的相关性分数
- reasoning: 简短的评分理由

查询: "{query}"

文本块:
"""


class LLMReranker:
    """基于LLM的重排序器"""

    def __init__(self, provider: str = "dashscope", model: str = None):
        self.api = APIProcessor(provider=provider)
        self.model = model or self.api.default_model

    def get_rank_for_single_block(self, query: str, text: str) -> Dict:
        """对单个文本块进行相关性评分"""
        user_prompt = f'{RERANK_SINGLE_PROMPT}{query}"""\n{text}\n"""'
        try:
            result = self.api.send_message(
                model=self.model,
                temperature=0,
                system_content="你是一个文本相关性评估专家。请以JSON格式输出评分结果。",
                human_content=user_prompt,
            )
            if isinstance(result, dict) and "relevance_score" in result:
                return result
            return {"relevance_score": 0.5, "reasoning": str(result)}
        except Exception as e:
            _log.error(f"单文档重排失败: {e}")
            return {"relevance_score": 0.0, "reasoning": f"Error: {e}"}

    def get_rank_for_multiple_blocks(self, query: str, texts: List[str]) -> Dict:
        """对多个文本块进行批量相关性评分"""
        formatted = "\n\n---\n\n".join(
            [f'Block {i+1}:\n"""\n{text}\n"""' for i, text in enumerate(texts)]
        )
        user_prompt = (
            f'{RERANK_MULTIPLE_PROMPT.format(query=query)}'
            f"{formatted}\n\n"
            f"You should provide exactly {len(texts)} rankings."
        )
        try:
            result = self.api.send_message(
                model=self.model,
                temperature=0,
                system_content="你是一个文本相关性评估专家。请以JSON格式输出评分结果。",
                human_content=user_prompt,
            )
            if isinstance(result, dict) and "block_rankings" in result:
                return result
            return {
                "block_rankings": [
                    {"relevance_score": 0.5, "reasoning": "default"} for _ in texts
                ]
            }
        except Exception as e:
            _log.error(f"批量重排失败: {e}")
            return {
                "block_rankings": [
                    {"relevance_score": 0.0, "reasoning": f"Error: {e}"} for _ in texts
                ]
            }

    def rerank_documents(
        self,
        query: str,
        documents: List[Dict],
        batch_size: int = 4,
        llm_weight: float = 0.7,
    ) -> List[Dict]:
        """
        对检索结果进行重排序
        参数:
            query: 查询语句
            documents: 待重排文档列表，每个包含 'text' 和 'distance'
            batch_size: 每批送入LLM的文档数（1=逐条评分）
            llm_weight: LLM分数权重（0-1）
        返回:
            按融合分数降序排列的文档列表
        """
        vector_weight = 1 - llm_weight

        if batch_size == 1:
            def process_single(doc):
                ranking = self.get_rank_for_single_block(query, doc["text"])
                doc_with_score = doc.copy()
                doc_with_score["relevance_score"] = ranking.get("relevance_score", 0.0)
                doc_with_score["combined_score"] = round(
                    llm_weight * ranking.get("relevance_score", 0.0)
                    + vector_weight * doc.get("distance", 0.0),
                    4,
                )
                return doc_with_score

            with ThreadPoolExecutor(max_workers=1) as executor:
                results = list(executor.map(process_single, documents))
        else:
            doc_batches = [
                documents[i:i + batch_size]
                for i in range(0, len(documents), batch_size)
            ]

            def process_batch(batch):
                texts = [doc["text"] for doc in batch]
                rankings = self.get_rank_for_multiple_blocks(query, texts)
                block_rankings = rankings.get("block_rankings", [])

                # 补齐缺失的评分
                while len(block_rankings) < len(batch):
                    block_rankings.append({"relevance_score": 0.0, "reasoning": "missing"})

                results = []
                for doc, rank in zip(batch, block_rankings):
                    doc_with_score = doc.copy()
                    doc_with_score["relevance_score"] = rank.get("relevance_score", 0.0)
                    doc_with_score["combined_score"] = round(
                        llm_weight * rank.get("relevance_score", 0.0)
                        + vector_weight * doc.get("distance", 0.0),
                        4,
                    )
                    results.append(doc_with_score)
                return results

            with ThreadPoolExecutor(max_workers=1) as executor:
                batch_results = list(executor.map(process_batch, doc_batches))

            results = []
            for batch in batch_results:
                results.extend(batch)

        results.sort(key=lambda x: x["combined_score"], reverse=True)
        return results
