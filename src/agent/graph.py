"""
LangGraph Agent主图定义
双模式路由：quick_response / deep_thinking / chitchat
"""
import logging
import time
from typing import Dict, Any

from langgraph.graph import StateGraph, END

from src.agent.state import AgentState
from src.agent.intent import IntentClassifier, IntentType, IntentResult
from src.agent.tools import AgentTools
from src.api_client import APIProcessor
from src.prompts.resume_prompts import CHITCHAT_SYSTEM
from src.config import get_config

_log = logging.getLogger(__name__)


class ResumeAgent:
    """简历生成Agent，基于LangGraph双模式路由"""

    def __init__(self, config=None):
        self.config = config or get_config()
        # 必须显式传model：API客户端默认模型(qwen-turbo-latest)可能无权限，会导致意图识别永远失败
        self.intent_classifier = IntentClassifier(
            provider=self.config.llm.provider,
            model=self.config.llm.model,
        )
        self.tools = AgentTools(config=self.config)
        self.api = APIProcessor(provider=self.config.llm.provider)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建LangGraph状态图"""
        workflow = StateGraph(AgentState)

        # 添加节点
        workflow.add_node("classify_intent", self._classify_intent)
        workflow.add_node("quick_response_path", self._quick_response_path)
        workflow.add_node("deep_thinking_path", self._deep_thinking_path)
        workflow.add_node("chitchat_path", self._chitchat_path)

        # 入口
        workflow.set_entry_point("classify_intent")

        # 条件路由
        workflow.add_conditional_edges(
            "classify_intent",
            self._route_intent,
            {
                "quick_response": "quick_response_path",
                "deep_thinking": "deep_thinking_path",
                "chitchat": "chitchat_path",
            },
        )

        # 所有路径最终到END
        workflow.add_edge("quick_response_path", END)
        workflow.add_edge("deep_thinking_path", END)
        workflow.add_edge("chitchat_path", END)

        return workflow.compile()

    def _classify_intent(self, state: AgentState) -> Dict:
        """意图识别节点"""
        user_input = state.get("user_input", "")
        result = self.intent_classifier.classify(user_input)

        # 检查是否有岗位要求
        has_job = bool(result.entities.get("job_title")) or any(
            kw in user_input.lower() for kw in ["jd", "岗位要求", "职位描述"]
        )

        return {
            "intent": result.intent.value,
            "extracted_entities": result.entities,
            "has_job_requirement": has_job,
            "step": "intent_classified",
        }

    def _route_intent(self, state: AgentState) -> str:
        """路由函数：根据意图类型选择路径"""
        intent = state.get("intent", "chitchat")
        if intent == "quick_response":
            return "quick_response"
        elif intent == "deep_thinking":
            return "deep_thinking"
        return "chitchat"

    def _quick_response_path(self, state: AgentState) -> Dict:
        """快速反应路径：单次检索 -> 直接回答"""
        user_input = state.get("user_input", "")
        entities = state.get("extracted_entities", {})

        # 根据实体决定检索策略
        tech_keywords = entities.get("tech_keywords", [])
        query = " ".join(tech_keywords) if tech_keywords else user_input

        # 检索知识
        category = None
        project_name = entities.get("project_name")
        if project_name:
            category = "project"

        t0 = time.time()
        knowledge = self.tools.search_knowledge(query, category=category)
        retrieval_elapsed = time.time() - t0

        # 直接回答
        t0 = time.time()
        response = self.tools.answer_question(user_input, knowledge)
        answer_elapsed = time.time() - t0

        _log.info(
            f"[快速回答] 耗时统计 - 检索: {retrieval_elapsed:.2f}s | "
            f"回答生成: {answer_elapsed:.2f}s | 合计: {retrieval_elapsed + answer_elapsed:.2f}s"
        )

        return {
            "retrieved_knowledge": knowledge,
            "final_response": response,
            "step": "quick_response_done",
        }

    def _deep_thinking_path(self, state: AgentState) -> Dict:
        """深思熟虑路径：分层检索（目录选点->定向检索） -> 草稿 -> 优化"""
        user_input = state.get("user_input", "")
        entities = state.get("extracted_entities", {})
        has_job = state.get("has_job_requirement", False)

        # 提取岗位要求（如有）
        job_requirement = entities.get("job_requirement", user_input) if has_job else None

        # 分层检索：LLM根据课程目录选高价值知识点 -> 定向检索对应课程；失败降级为普通混合检索
        knowledge = self._hierarchical_search(user_input, job_requirement)
        # 项目素材：优先用预写好的项目介绍成品，无介绍文档时降级检索亮点分块
        projects = self.tools.select_project_intros(user_input, job_requirement)
        if not projects:
            projects = self.tools.search_projects(user_input, top_n=5)

        # 生成简历草稿
        resume_draft = self.tools.generate_resume(
            knowledge=knowledge,
            projects=projects,
            job_requirement=job_requirement,
        )

        # 如果有岗位要求，进一步优化
        resume_final = resume_draft
        if has_job and job_requirement:
            resume_final = self.tools.optimize_resume(resume_draft, job_requirement)

        return {
            "retrieved_knowledge": knowledge,
            "retrieved_projects": projects,
            "resume_draft": resume_draft,
            "resume_final": resume_final,
            "final_response": resume_final,
            "step": "deep_thinking_done",
        }

    def _hierarchical_search(self, user_input: str, job_requirement: str = None) -> str:
        """分层检索：目录选点 -> 定向检索；任一环节失败降级为普通混合检索"""
        try:
            plan = self.tools.select_resume_knowledge(user_input, job_requirement)
        except Exception as e:
            _log.warning(f"知识点选择异常: {e}")
            plan = None

        if plan:
            _log.info(f"分层检索命中 {len(plan.selections)} 门课程")
            knowledge = self.tools.retrieve_by_plan(plan)
            if knowledge and knowledge != "未检索到相关知识。":
                return knowledge

        _log.info("分层检索不可用，降级为普通混合检索")
        return self.tools.search_knowledge(user_input, top_n=10)

    def _chitchat_path(self, state: AgentState) -> Dict:
        """闲聊兜底路径"""
        user_input = state.get("user_input", "")

        try:
            result = self.api.send_message(
                model=self.config.llm.model,
                temperature=0.7,
                system_content=CHITCHAT_SYSTEM,
                human_content=user_input,
                enable_thinking=False,
            )
            if isinstance(result, dict):
                response = result.get("content", str(result))
            else:
                response = str(result)
        except Exception as e:
            response = f"你好！我是ResumeAgent，你的简历生成和知识问答助手。请问有什么可以帮你的？"

        return {
            "final_response": response,
            "step": "chitchat_done",
        }

    def run(self, user_input: str) -> Dict:
        """
        运行Agent
        参数:
            user_input: 用户输入
        返回:
            包含final_response等字段的字典
        """
        initial_state = {
            "messages": [],
            "user_input": user_input,
            "intent": "",
            "extracted_entities": {},
            "has_job_requirement": False,
            "retrieved_knowledge": "",
            "retrieved_projects": "",
            "resume_draft": "",
            "resume_final": "",
            "final_response": "",
            "step": "init",
        }

        result = self.graph.invoke(initial_state)
        return result

    def run_stream(self, user_input: str):
        """
        流式运行Agent，逐步yield事件字典（供Web UI增量渲染，SSE效果）
        事件类型:
            {"type": "status", "text": str}  阶段进度提示
            {"type": "token", "text": str}   回答文本增量
            {"type": "done", ...}            结束，携带intent/final_response等完整结果
        """
        # Step 1: 意图识别
        t_start = time.time()
        yield {"type": "status", "text": "正在识别意图..."}
        t0 = time.time()
        result = self.intent_classifier.classify(user_input)
        intent_elapsed = time.time() - t0
        intent = result.intent.value
        entities = result.entities
        has_job = bool(entities.get("job_title")) or any(
            kw in user_input.lower() for kw in ["jd", "岗位要求", "职位描述"]
        )
        yield {"type": "intent", "value": intent}

        if intent == "quick_response":
            # 快速回答：检索 -> 流式回答
            tech_keywords = entities.get("tech_keywords", [])
            query = " ".join(tech_keywords) if tech_keywords else user_input
            category = "project" if entities.get("project_name") else None

            yield {"type": "status", "text": "正在检索知识库..."}
            t0 = time.time()
            knowledge = self.tools.search_knowledge(query, category=category)
            retrieval_elapsed = time.time() - t0

            yield {"type": "status", "text": "正在生成回答..."}
            first_token_elapsed = None
            total_chars = 0
            t0 = time.time()
            try:
                for chunk in self.tools.answer_question_stream(user_input, knowledge):
                    if first_token_elapsed is None:
                        first_token_elapsed = time.time() - t0
                    total_chars += len(chunk)
                    yield {"type": "token", "text": chunk}
            except Exception as e:
                _log.error(f"流式回答失败: {e}")
                yield {"type": "token", "text": f"抱歉，回答问题时出现错误: {e}"}
            generation_elapsed = time.time() - t0

            _log.info(
                f"[快速回答] 耗时统计 - 意图识别: {intent_elapsed:.2f}s | "
                f"检索: {retrieval_elapsed:.2f}s | 首token: {first_token_elapsed:.2f}s | "
                f"生成: {generation_elapsed:.2f}s({total_chars}字) | "
                f"全链路: {time.time() - t_start:.2f}s"
            )

            yield {"type": "done", "intent": intent, "step": "quick_response_done",
                   "retrieved_knowledge": knowledge}

        elif intent == "deep_thinking":
            # 深思路径：多步调用，简历为长文档，保持非流式，输出阶段进度
            job_requirement = entities.get("job_requirement", user_input) if has_job else None

            yield {"type": "status", "text": "正在分析课程目录，挑选高价值知识点..."}
            knowledge = self._hierarchical_search(user_input, job_requirement)

            yield {"type": "status", "text": "正在挑选项目介绍..."}
            projects = self.tools.select_project_intros(user_input, job_requirement)
            if not projects:
                projects = self.tools.search_projects(user_input, top_n=5)

            yield {"type": "status", "text": "正在生成简历草稿（耗时较长，请稍候）..."}
            resume_draft = self.tools.generate_resume(
                knowledge=knowledge, projects=projects, job_requirement=job_requirement,
            )

            resume_final = resume_draft
            if has_job and job_requirement:
                yield {"type": "status", "text": "正在按岗位要求优化简历..."}
                resume_final = self.tools.optimize_resume(resume_draft, job_requirement)

            yield {"type": "token", "text": resume_final}
            yield {"type": "done", "intent": intent, "step": "deep_thinking_done",
                   "retrieved_knowledge": knowledge, "retrieved_projects": projects,
                   "resume_draft": resume_draft, "resume_final": resume_final}

        else:
            # 闲聊：流式输出
            try:
                for chunk in self.api.send_message_stream(
                    model=self.config.llm.model,
                    temperature=0.7,
                    system_content=CHITCHAT_SYSTEM,
                    human_content=user_input,
                    enable_thinking=False,
                ):
                    yield {"type": "token", "text": chunk}
            except Exception as e:
                _log.error(f"闲聊流式输出失败: {e}")
                yield {"type": "token",
                       "text": "你好！我是ResumeAgent，你的简历生成和知识问答助手。请问有什么可以帮你的？"}

            yield {"type": "done", "intent": "chitchat", "step": "chitchat_done"}
