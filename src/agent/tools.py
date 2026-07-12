"""
Agent工具定义
知识检索、项目查询、简历生成、问答回答
"""
import logging
from pathlib import Path
from typing import Optional

from src.api_client import APIProcessor
from src.retrieval.retriever import BM25Retriever, VectorRetriever
from src.config import get_config
from src.prompts.knowledge_prompts import KNOWLEDGE_QA_SYSTEM, KNOWLEDGE_QA_USER
from src.prompts.resume_prompts import (
    RESUME_GENERATE_SYSTEM, RESUME_GENERATE_USER,
    RESUME_OPTIMIZE_SYSTEM, RESUME_OPTIMIZE_USER,
)

_log = logging.getLogger(__name__)


class AgentTools:
    """Agent工具集"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._vector_retriever = None
        self._bm25_retriever = None

    @property
    def vector_retriever(self):
        if self._vector_retriever is None:
            self._vector_retriever = VectorRetriever(
                vector_db_dir=self.config.paths.vector_dbs_dir,
                documents_dir=self.config.paths.course_chunks_dir,
                embedding_provider=self.config.embedding.provider,
                embedding_model=self.config.embedding.model,
            )
        return self._vector_retriever

    @property
    def bm25_retriever(self):
        if self._bm25_retriever is None:
            self._bm25_retriever = BM25Retriever(
                bm25_db_dir=self.config.paths.bm25_dbs_dir,
                documents_dir=self.config.paths.course_chunks_dir,
            )
        return self._bm25_retriever

    def search_knowledge(self, query: str, category: str = None, top_n: int = 5) -> str:
        """
        从知识库检索课程知识点
        参数:
            query: 查询文本
            category: 按类别过滤 (course/project/interview)
            top_n: 返回结果数
        返回:
            格式化的检索结果文本
        """
        try:
            results = self.bm25_retriever.retrieve(query, category=category, top_n=top_n)
        except Exception as e:
            _log.warning(f"BM25检索失败: {e}, 尝试向量检索")
            try:
                results = self.vector_retriever.retrieve(query, category=category, top_n=top_n)
            except Exception as e2:
                _log.error(f"向量检索也失败: {e2}")
                return "知识库检索失败，暂无可用数据。"

        if not results:
            return "未检索到相关知识。"

        formatted = []
        for i, r in enumerate(results, 1):
            source = r.get("source", "未知来源")
            text = r.get("text", "")
            formatted.append(f"[{i}] 来源: {source}\n{text}")

        return "\n\n---\n\n".join(formatted)

    def search_projects(self, query: str, top_n: int = 5) -> str:
        """
        从知识库检索项目精华
        """
        return self.search_knowledge(query, category="project", top_n=top_n)

    def answer_question(self, question: str, context: str, provider: str = None) -> str:
        """
        基于检索上下文回答问题
        """
        provider = provider or self.config.llm.provider
        api = APIProcessor(provider=provider)

        user_prompt = KNOWLEDGE_QA_USER.format(context=context, question=question)

        try:
            result = api.send_message(
                model=self.config.llm.model,
                temperature=0.3,
                system_content=KNOWLEDGE_QA_SYSTEM,
                human_content=user_prompt,
            )
            if isinstance(result, dict):
                return result.get("content", str(result))
            return str(result)
        except Exception as e:
            _log.error(f"回答问题失败: {e}")
            return f"抱歉，回答问题时出现错误: {e}"

    def generate_resume(
        self,
        knowledge: str,
        projects: str,
        job_requirement: str = None,
        provider: str = None,
    ) -> str:
        """
        调用LLM生成简历
        """
        provider = provider or self.config.llm.provider
        api = APIProcessor(provider=provider)

        job_section = ""
        if job_requirement:
            job_section = f"## 目标岗位要求\n{job_requirement}"

        user_prompt = RESUME_GENERATE_USER.format(
            knowledge=knowledge,
            projects=projects,
            job_section=job_section,
        )

        try:
            result = api.send_message(
                model=self.config.llm.model,
                temperature=0.5,
                system_content=RESUME_GENERATE_SYSTEM,
                human_content=user_prompt,
            )
            if isinstance(result, dict):
                return result.get("content", str(result))
            return str(result)
        except Exception as e:
            _log.error(f"简历生成失败: {e}")
            return f"简历生成失败: {e}"

    def optimize_resume(
        self,
        resume: str,
        job_requirement: str,
        provider: str = None,
    ) -> str:
        """
        根据岗位要求优化简历
        """
        provider = provider or self.config.llm.provider
        api = APIProcessor(provider=provider)

        user_prompt = RESUME_OPTIMIZE_USER.format(
            resume=resume,
            job_requirement=job_requirement,
        )

        try:
            result = api.send_message(
                model=self.config.llm.model,
                temperature=0.4,
                system_content=RESUME_OPTIMIZE_SYSTEM,
                human_content=user_prompt,
            )
            if isinstance(result, dict):
                return result.get("content", str(result))
            return str(result)
        except Exception as e:
            _log.error(f"简历优化失败: {e}")
            return f"简历优化失败: {e}"
