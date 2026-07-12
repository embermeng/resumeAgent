"""
LangGraph Agent主图定义
双模式路由：quick_response / deep_thinking / chitchat
"""
import logging
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
        self.intent_classifier = IntentClassifier(provider=self.config.llm.provider)
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

        knowledge = self.tools.search_knowledge(query, category=category)

        # 直接回答
        response = self.tools.answer_question(user_input, knowledge)

        return {
            "retrieved_knowledge": knowledge,
            "final_response": response,
            "step": "quick_response_done",
        }

    def _deep_thinking_path(self, state: AgentState) -> Dict:
        """深思熟虑路径：多步检索 -> 草稿 -> 优化"""
        user_input = state.get("user_input", "")
        entities = state.get("extracted_entities", {})
        has_job = state.get("has_job_requirement", False)

        # 多步检索：并行获取知识和项目
        knowledge = self.tools.search_knowledge(user_input, top_n=10)
        projects = self.tools.search_projects(user_input, top_n=5)

        # 提取岗位要求（如有）
        job_requirement = None
        if has_job:
            # 简单提取：查找JD相关文本
            job_requirement = entities.get("job_requirement", user_input)

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

    def _chitchat_path(self, state: AgentState) -> Dict:
        """闲聊兜底路径"""
        user_input = state.get("user_input", "")

        try:
            result = self.api.send_message(
                model=self.config.llm.model,
                temperature=0.7,
                system_content=CHITCHAT_SYSTEM,
                human_content=user_input,
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
