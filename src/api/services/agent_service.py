"""
Agent 服务层

封装 ResumeAgent 单例(与 Streamlit get_agent 一致,避免每请求重建),
向路由层暴露 run_stream 事件流。路由层负责把事件字典封装为 SSE 帧。
"""
from typing import Any, Dict, Iterator, Optional

from src.agent.graph import ResumeAgent
from src.config import get_config


class AgentService:
    """ResumeAgent 的薄封装。

    - agent 可注入(便于测试);未注入时懒加载单例。
    - run_stream 透传 prompt 与 existing_resume,yield 事件字典(status/intent/token/done)。
    """

    def __init__(self, agent: Optional[ResumeAgent] = None):
        self._agent = agent

    @property
    def agent(self) -> ResumeAgent:
        if self._agent is None:
            self._agent = ResumeAgent(config=get_config())
        return self._agent

    def run_stream(
        self, prompt: str, existing_resume: Optional[str] = None
    ) -> Iterator[Dict[str, Any]]:
        """透传到 ResumeAgent.run_stream"""
        return self.agent.run_stream(prompt, existing_resume=existing_resume)
