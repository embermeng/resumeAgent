"""
Agent工具定义
知识检索、项目查询、简历生成、问答回答
"""
import json
import logging
from pathlib import Path
from typing import Optional

from src.api_client import APIProcessor
from src.retrieval.retriever import BM25Retriever, VectorRetriever, HybridRetriever
from src.config import get_config
from src.schemas.knowledge import SelectionPlan
from src.prompts.knowledge_prompts import (
    KNOWLEDGE_QA_SYSTEM, KNOWLEDGE_QA_USER,
    RESUME_KNOWLEDGE_SELECT_SYSTEM, RESUME_KNOWLEDGE_SELECT_USER,
)
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
        self._hybrid_retriever = None

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

    @property
    def hybrid_retriever(self):
        if self._hybrid_retriever is None:
            self._hybrid_retriever = HybridRetriever(
                vector_db_dir=self.config.paths.vector_dbs_dir,
                bm25_db_dir=self.config.paths.bm25_dbs_dir,
                documents_dir=self.config.paths.course_chunks_dir,
                embedding_provider=self.config.embedding.provider,
                embedding_model=self.config.embedding.model,
            )
        return self._hybrid_retriever

    def search_knowledge(self, query: str, category: str = None, top_n: int = 5) -> str:
        """
        从知识库检索课程知识点（混合检索：BM25关键词 + 向量语义）
        参数:
            query: 查询文本
            category: 按类别过滤 (course/project/interview)
            top_n: 返回结果数
        返回:
            格式化的检索结果文本
        """
        try:
            results = self.hybrid_retriever.retrieve(query, category=category, top_n=top_n)
        except Exception as e:
            # 混合检索失败（如embedding API异常）时降级为纯BM25
            _log.warning(f"混合检索失败: {e}, 降级为BM25检索")
            try:
                results = self.bm25_retriever.retrieve(query, category=category, top_n=top_n)
            except Exception as e2:
                _log.error(f"BM25检索也失败: {e2}")
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

    # ----------------------------------------------------------
    # 分层检索（课程目录选点 -> 定向检索）
    # ----------------------------------------------------------

    def load_catalog(self) -> Optional[list]:
        """加载课程摘要目录，不存在或为空返回None"""
        catalog_path = self.config.paths.catalog_path
        if not catalog_path.exists():
            return None
        try:
            with open(catalog_path, "r", encoding="utf-8") as f:
                courses = json.load(f).get("courses", [])
            return courses or None
        except Exception as e:
            _log.warning(f"课程目录加载失败: {e}")
            return None

    def select_resume_knowledge(self, user_input: str, job_requirement: str = None) -> Optional[SelectionPlan]:
        """
        分层检索第二层：LLM浏览全部课程目录，按“简历含金量”（+岗位匹配度）挑选知识点
        失败（无目录/解析失败/选择为空）返回None，调用方应降级为普通混合检索
        """
        courses = self.load_catalog()
        if not courses:
            _log.info("课程目录不存在，无法选点")
            return None

        job_section = f"\n## 目标岗位要求\n{job_requirement}" if job_requirement else ""
        catalog_text = json.dumps(courses, ensure_ascii=False, indent=1)

        try:
            api = APIProcessor(provider=self.config.llm.provider)
            result = api.send_message(
                model=self.config.llm.model,
                temperature=0.3,
                system_content=RESUME_KNOWLEDGE_SELECT_SYSTEM,
                human_content=RESUME_KNOWLEDGE_SELECT_USER.format(
                    catalog=catalog_text, user_input=user_input, job_section=job_section,
                ),
                is_structured=True,
                response_format=SelectionPlan,
            )
        except Exception as e:
            _log.error(f"知识点选择调用失败: {e}")
            return None

        if result.get("parse_error"):
            _log.error(f"知识点选择解析失败: {result['parse_error'][:200]}")
            return None
        try:
            plan = SelectionPlan.model_validate(result)
        except Exception as e:
            _log.error(f"知识点选择校验失败: {e}")
            return None

        # 过滤目录中不存在的doc_id（防LLM编造）与空选择
        valid_ids = {c["doc_id"] for c in courses}
        plan.selections = [
            s for s in plan.selections if s.doc_id in valid_ids and s.selected_points
        ]
        return plan if plan.selections else None

    def retrieve_by_plan(
        self,
        plan: SelectionPlan,
        top_per_point: int = 3,
        max_results: int = 15,
    ) -> str:
        """
        分层检索第二层执行：按选中的知识点定向检索对应课程，合并去重后格式化
        每门课程公平分配名额（轮询交错取块），保证多门课程的知识点都能进入上下文；
        课程保持LLM选择顺序（已按重要性排序），总量封顶防上下文爆炸
        """
        per_course_cap = max(1, max_results // max(len(plan.selections), 1))
        per_course = []  # 每门课程去重后的分块列表，顺序与选择顺序一致
        seen = set()
        for sel in plan.selections:
            chunks = []
            for point in sel.selected_points:
                try:
                    results = self.hybrid_retriever.retrieve(
                        point, top_n=top_per_point, doc_ids=[sel.doc_id]
                    )
                except Exception as e:
                    _log.warning(f"定向混合检索失败({point}): {e}, 降级BM25")
                    try:
                        results = self.bm25_retriever.retrieve(
                            point, top_n=top_per_point, doc_ids=[sel.doc_id]
                        )
                    except Exception as e2:
                        _log.error(f"定向BM25检索也失败({point}): {e2}")
                        continue
                for r in results:
                    key = (r["doc_id"], r.get("chunk_id"))
                    if key not in seen:
                        seen.add(key)
                        chunks.append(r)
            per_course.append(chunks[:per_course_cap])

        # 轮询交错合并：每轮每门课取一块，保证课程间分布均衡
        merged = []
        round_idx = 0
        while len(merged) < max_results:
            added = False
            for chunks in per_course:
                if round_idx < len(chunks) and len(merged) < max_results:
                    merged.append(chunks[round_idx])
                    added = True
            if not added:
                break
            round_idx += 1

        if not merged:
            return "未检索到相关知识。"

        formatted = []
        for i, r in enumerate(merged, 1):
            formatted.append(f"[{i}] 来源: {r.get('source', '未知来源')}\n{r['text']}")
        return "\n\n---\n\n".join(formatted)

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

    def answer_question_stream(self, question: str, context: str, provider: str = None):
        """
        基于检索上下文流式回答问题，逐块yield文本增量
        """
        provider = provider or self.config.llm.provider
        api = APIProcessor(provider=provider)

        user_prompt = KNOWLEDGE_QA_USER.format(context=context, question=question)

        yield from api.send_message_stream(
            model=self.config.llm.model,
            temperature=0.3,
            system_content=KNOWLEDGE_QA_SYSTEM,
            human_content=user_prompt,
        )

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
