"""
FastAPI 依赖注入:进程内单例

单用户场景:单例复用,避免每请求重建 Agent(重建会重新加载检索器/LLM 客户端,很慢)。
测试通过 app.dependency_overrides[getter] 覆盖为 mock。

历史注:曾有 get_task_manager/get_knowledge_service/get_resume_parser 三个单例,
Celery 正式化后任务状态归 DB(build_tasks)、KnowledgeService/ResumeFileParser
改由 worker 进程内自建,均已退役删除。
"""
from functools import lru_cache

from src.api.services.agent_service import AgentService


@lru_cache
def get_agent_service() -> AgentService:
    return AgentService()
