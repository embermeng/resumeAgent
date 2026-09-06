"""
Agent工具定义
知识检索、项目查询、简历生成、问答回答
"""
import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from src.api_client import APIProcessor
from src.retrieval.retriever import BM25Retriever, VectorRetriever, HybridRetriever
from src.config import get_config
from src.knowledge.intro_selector import IntroSelector, PRIORITY_TAG
from src.schemas.knowledge import SelectionPlan
from src.schemas.project import IntroSelectionPlan
from src.prompts.knowledge_prompts import (
    KNOWLEDGE_QA_SYSTEM, KNOWLEDGE_QA_USER,
    RESUME_KNOWLEDGE_SELECT_SYSTEM, RESUME_KNOWLEDGE_SELECT_USER,
    INTRO_SELECT_SYSTEM, INTRO_SELECT_USER,
)
from src.prompts.resume_prompts import (
    RESUME_GENERATE_SYSTEM, RESUME_GENERATE_USER,
    RESUME_OPTIMIZE_SYSTEM, RESUME_OPTIMIZE_USER,
)

_log = logging.getLogger(__name__)

# 介绍文档数量不超过该值时全部直接使用，省去一次LLM挑选调用
INTRO_SMALL_COUNT = 6

# 已有简历的Markdown标题行（提取章节结构，生成时按其顺序输出）
_RESUME_SECTION_PATTERN = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def extract_resume_sections(resume_text: str) -> List[str]:
    """
    提取已有简历的章节标题列表（Markdown标题行，去重保序）
    无标题行（如纯txt简历）时返回空列表，由LLM自行从正文归纳结构
    """
    sections = []
    for title in _RESUME_SECTION_PATTERN.findall(resume_text):
        if title not in sections:
            sections.append(title)
    return sections


# LLM开场白特征（如"以下是为您优化后的简历…"）与首个标题行
_PREAMBLE_TRIGGER_PATTERN = re.compile(r"^(以下是|下面是|这是|您好|本次|这份|经过|根据您)")
_FIRST_HEADING_PATTERN = re.compile(r"^#{1,6}\s+", re.MULTILINE)


def strip_llm_preamble(text: str) -> str:
    """
    剥离LLM输出中混入简历正文前的开场白（导出兼容兜底，prompt已硬约束）
    仅当首个标题行之前存在命中开场白特征的文本时才丢弃前缀，避免误删正常内容
    """
    if not text:
        return text
    match = _FIRST_HEADING_PATTERN.search(text)
    if not match or match.start() == 0:
        return text
    preamble = text[:match.start()].strip()
    if preamble and _PREAMBLE_TRIGGER_PATTERN.match(preamble):
        _log.info(f"剥离LLM开场白: {len(preamble)}字")
        return text[match.start():]
    return text


class AgentTools:
    """Agent工具集"""

    def __init__(self, config=None):
        self.config = config or get_config()
        self._vector_retriever = None
        self._bm25_retriever = None
        self._hybrid_retriever = None
        self._intro_selector = None
        # 共享持久API客户端：连接池保持TLS长连接，避免每次LLM调用新建连接
        self._api = APIProcessor(provider=self.config.llm.provider)

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

    @property
    def intro_selector(self):
        if self._intro_selector is None:
            self._intro_selector = IntroSelector()
        return self._intro_selector

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
        return self._format_results(results)

    @staticmethod
    def _format_results(results) -> str:
        """检索结果统一格式化：编号 + 来源 + 正文"""
        formatted = []
        for i, r in enumerate(results, 1):
            source = r.get("source", "未知来源")
            text = r.get("text", "")
            formatted.append(f"[{i}] 来源: {source}\n{text}")
        return "\n\n---\n\n".join(formatted)

    def search_projects(self, query: str, top_n: int = 5) -> str:
        """
        从知识库检索项目亮点素材：
        打了“简历优先”标签的项目按项目均分名额定向检索、轮询交错合并，
        保证每个重点项目都进入上下文；剩余名额用全局项目检索补齐（去重）。
        无标签文档时退化为普通混合检索。
        """
        tagged = self.list_tagged_project_ids()
        if not tagged:
            return self.search_knowledge(query, category="project", top_n=top_n)

        per_doc = max(1, top_n // len(tagged))
        per_project = []
        for doc_id, _source in tagged:
            try:
                results = self.hybrid_retriever.retrieve(
                    query, category="project", top_n=per_doc, doc_ids=[doc_id]
                )
            except Exception as e:
                _log.warning(f"优先项目混合检索失败: {e}, 降级BM25")
                try:
                    results = self.bm25_retriever.retrieve(
                        query, category="project", top_n=per_doc, doc_ids=[doc_id]
                    )
                except Exception as e2:
                    _log.error(f"优先项目BM25检索也失败: {e2}")
                    continue
            per_project.append(results)

        # 轮询交错合并：每轮每个项目取一块，保证各项目分布均衡
        merged = []
        seen = set()
        round_idx = 0
        while len(merged) < top_n:
            added = False
            for results in per_project:
                if round_idx < len(results) and len(merged) < top_n:
                    r = results[round_idx]
                    key = (r.get("doc_id"), r.get("chunk_id"))
                    if key not in seen:
                        seen.add(key)
                        merged.append(r)
                        added = True
            if not added:
                break
            round_idx += 1

        # 剩余名额用全局项目检索补齐（去重）；全局检索失败不影响已选的优先项目
        if len(merged) < top_n:
            try:
                global_results = self.hybrid_retriever.retrieve(
                    query, category="project", top_n=top_n
                )
            except Exception as e:
                _log.warning(f"全局项目检索失败: {e}，仅使用优先项目结果")
                global_results = []
            for r in global_results:
                if len(merged) >= top_n:
                    break
                key = (r.get("doc_id"), r.get("chunk_id"))
                if key not in seen:
                    seen.add(key)
                    merged.append(r)

        if not merged:
            return self.search_knowledge(query, category="project", top_n=top_n)
        return self._format_results(merged)

    def list_tagged_project_ids(self, tag: str = PRIORITY_TAG) -> List[Tuple[str, str]]:
        """扫描项目分块文档，返回带指定标签的 [(doc_id, source), ...]"""
        tagged = []
        chunks_dir = self.config.paths.course_chunks_dir
        for p in sorted(Path(chunks_dir).glob("project-*.json")):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    meta = json.load(f).get("metainfo", {})
            except Exception:
                continue
            if meta.get("category") == "project" and tag in meta.get("tags", []):
                tagged.append((meta.get("doc_id", ""), meta.get("source", "")))
        return tagged

    # ----------------------------------------------------------
    # 项目介绍文档挑选（简历生成的项目素材主路径）
    # ----------------------------------------------------------

    def select_project_intros(
        self, user_input: str, job_requirement: str = None, top_n: int = 5
    ) -> str:
        """
        从 project_intros/ 挑选适合本次简历的项目介绍成品，拼接为素材文本。
        规则：带“简历优先”标签的项目必选且排前；文档数≤INTRO_SMALL_COUNT时全选；
        否则由LLM按诉求/岗位从轻量目录中挑选（失败降级为标签+文件顺序兜底）。
        无介绍文档时返回空串，调用方应降级用 search_projects 检索亮点分块。
        """
        entries = self.intro_selector.scan(self.config.paths.project_intros_dir)
        if not entries:
            return ""

        tagged = [e for e in entries if PRIORITY_TAG in e["tags"]]

        if len(entries) <= INTRO_SMALL_COUNT:
            # 文档少：全部使用，标签项目排前，不调LLM
            selected = tagged + [e for e in entries if e not in tagged]
        else:
            chosen_names = self._llm_select_intros(entries, user_input, job_requirement, top_n)
            if chosen_names is None:
                # 挑选调用失败：标签项目 + 文件顺序兜底补齐
                selected = tagged + [e for e in entries if e not in tagged]
                selected = selected[: max(top_n, len(tagged))]
            else:
                by_name = {e["name"]: e for e in entries}
                picked = [by_name[n] for n in chosen_names if n in by_name]
                picked = [e for e in picked if e not in tagged]
                selected = (tagged + picked)[: max(top_n, len(tagged))]

        if not selected:
            return ""

        formatted = [
            f"[{i}] 项目：{e['name']}\n{e['text']}"
            for i, e in enumerate(selected, 1)
        ]
        _log.info(f"简历项目素材已选 {len(selected)} 个介绍文档: "
                  f"{[e['name'] for e in selected]}")
        return "\n\n---\n\n".join(formatted)

    def _llm_select_intros(
        self, entries: List[Dict], user_input: str, job_requirement: str = None,
        top_n: int = 5,
    ) -> Optional[List[str]]:
        """LLM从介绍目录中挑选项目，返回项目名列表；失败返回None（调用方降级）"""
        catalog = self.intro_selector.build_catalog(entries)
        job_section = f"\n## 目标岗位要求\n{job_requirement}" if job_requirement else ""

        try:
            result = self._api.send_message(
                model=self.config.llm.model,
                temperature=0.3,
                system_content=INTRO_SELECT_SYSTEM,
                human_content=INTRO_SELECT_USER.format(
                    catalog=catalog, user_input=user_input,
                    job_section=job_section, top_n=top_n,
                ),
                is_structured=True,
                response_format=IntroSelectionPlan,
            )
        except Exception as e:
            _log.error(f"项目介绍挑选调用失败: {e}")
            return None

        if result.get("parse_error"):
            _log.error(f"项目介绍挑选解析失败: {result['parse_error'][:200]}")
            return None
        try:
            plan = IntroSelectionPlan.model_validate(result)
        except Exception as e:
            _log.error(f"项目介绍挑选校验失败: {e}")
            return None

        # 防LLM编造：只保留目录中真实存在的项目名，去重保序
        valid_names = {e["name"] for e in entries}
        seen, picked = set(), []
        for n in plan.selected_projects:
            if n in valid_names and n not in seen:
                seen.add(n)
                picked.append(n)
        return picked

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
            result = self._api.send_message(
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
        api = APIProcessor(provider=provider) if provider else self._api

        user_prompt = KNOWLEDGE_QA_USER.format(context=context, question=question)

        try:
            result = api.send_message(
                model=self.config.llm.model,
                temperature=0.3,
                system_content=KNOWLEDGE_QA_SYSTEM,
                human_content=user_prompt,
                # 问答是轻任务，关闭thinking降低首响应延迟
                enable_thinking=False,
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
        api = APIProcessor(provider=provider) if provider else self._api

        user_prompt = KNOWLEDGE_QA_USER.format(context=context, question=question)

        yield from api.send_message_stream(
            model=self.config.llm.model,
            temperature=0.3,
            system_content=KNOWLEDGE_QA_SYSTEM,
            human_content=user_prompt,
            # 问答是轻任务，关闭thinking避免首token被隐藏思考阶段阻塞（实测 11s->0.6s）
            enable_thinking=False,
        )

    def generate_resume(
        self,
        knowledge: str,
        projects: str,
        job_requirement: str = None,
        existing_resume: str = None,
        provider: str = None,
    ) -> str:
        """
        调用LLM生成简历
        existing_resume: 用户提供的已有简历（Markdown文本），传入时作为事实骨架增强生成
        """
        api = APIProcessor(provider=provider) if provider else self._api

        job_section = ""
        if job_requirement:
            job_section = f"## 目标岗位要求\n{job_requirement}"

        existing_resume_section = ""
        if existing_resume and existing_resume.strip():
            # 能提取到章节时显式列出顺序，约束LLM按已有简历的结构字段输出
            sections = extract_resume_sections(existing_resume)
            structure_hint = ""
            if sections:
                structure_hint = (
                    f"\n输出必须严格沿用已有简历的章节结构与顺序：{'、'.join(sections)}，"
                    f"不增删或重排章节，不套用默认简历模板；"
                    f"各章节内部的字段与条目格式也必须沿用已有简历（如工作经历的"
                    f"“时间/公司/职位/工作内容”字段行、项目经历的“技术栈/项目描述/责任描述”字段），"
                    f"项目素材需改写为该字段格式，不得照搬素材文档自身的结构。"
                )
            existing_resume_section = (
                f"\n## 我的已有简历（事实骨架，保留其中真实内容）{structure_hint}\n{existing_resume}\n"
            )

        user_prompt = RESUME_GENERATE_USER.format(
            knowledge=knowledge,
            projects=projects,
            job_section=job_section,
            existing_resume_section=existing_resume_section,
        )

        try:
            result = api.send_message(
                model=self.config.llm.model,
                temperature=0.5,
                system_content=RESUME_GENERATE_SYSTEM,
                human_content=user_prompt,
            )
            if isinstance(result, dict):
                return strip_llm_preamble(result.get("content", str(result)))
            return strip_llm_preamble(str(result))
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
        api = APIProcessor(provider=provider) if provider else self._api

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
                return strip_llm_preamble(result.get("content", str(result)))
            return strip_llm_preamble(str(result))
        except Exception as e:
            _log.error(f"简历优化失败: {e}")
            return f"简历优化失败: {e}"
