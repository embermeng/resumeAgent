"""
FastAPI 依赖注入:进程内单例

单用户场景:单例复用,避免每请求重建 Agent(重建会重新加载检索器/LLM 客户端,很慢)。
测试通过 app.dependency_overrides[getter] 覆盖为 mock。
"""
from functools import lru_cache

from src.api.services.agent_service import AgentService
from src.api.services.knowledge_service import KnowledgeService
from src.api.task_manager import TaskManager
from src.knowledge.resume_file_parser import ResumeFileParser


@lru_cache
def get_agent_service() -> AgentService:
    return AgentService()


@lru_cache
def get_knowledge_service() -> KnowledgeService:
    return KnowledgeService()


@lru_cache
def get_task_manager() -> TaskManager:
    return TaskManager()


@lru_cache
def get_resume_parser() -> ResumeFileParser:
    return ResumeFileParser()
