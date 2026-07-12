"""
Agent状态定义
"""
from typing import TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage


class AgentState(TypedDict):
    """Agent状态，用于LangGraph图节点间传递"""
    messages: List[BaseMessage]           # 对话消息列表
    user_input: str                       # 用户原始输入
    intent: str                           # 意图分类：quick_response / deep_thinking / chitchat
    extracted_entities: Dict[str, Any]    # 意图识别时提取的实体
    has_job_requirement: bool             # 是否有岗位要求
    retrieved_knowledge: str              # 检索到的知识上下文
    retrieved_projects: str               # 检索到的项目精华
    resume_draft: str                     # 简历草稿
    resume_final: str                     # 最终简历
    final_response: str                   # 最终回复
    step: str                             # 当前执行步骤
