"""src/api/services/agent_service.py 测试(TDD):单例复用与 run_stream 透传"""
from unittest.mock import MagicMock, patch

from src.api.services.agent_service import AgentService


class TestAgentService:
    def test_lazy_singleton_created_once(self):
        # 未注入 agent 时懒加载,多次调用只创建一个 ResumeAgent
        with patch("src.api.services.agent_service.ResumeAgent") as MockAgent, \
             patch("src.api.services.agent_service.get_config"):
            MockAgent.return_value.run_stream.return_value = iter([])
            svc = AgentService()
            list(svc.run_stream("a"))
            list(svc.run_stream("b"))
            assert MockAgent.call_count == 1

    def test_injected_agent_reused_and_passthrough(self):
        fake = MagicMock()
        stream = [
            {"type": "status", "text": "正在识别意图..."},
            {"type": "intent", "value": "chitchat"},
            {"type": "token", "text": "你好"},
            {"type": "done", "intent": "chitchat", "step": "chitchat_done"},
        ]
        fake.run_stream.return_value = iter(stream)
        svc = AgentService(agent=fake)

        events = list(svc.run_stream("你好"))

        assert events == stream
        fake.run_stream.assert_called_once_with("你好", existing_resume=None)

    def test_passes_existing_resume(self):
        fake = MagicMock()
        fake.run_stream.return_value = iter([])
        svc = AgentService(agent=fake)

        list(svc.run_stream("帮我生成简历", existing_resume="# 旧简历"))

        assert fake.run_stream.call_args.kwargs["existing_resume"] == "# 旧简历"

    def test_returns_generator_not_list(self):
        # run_stream 应保持惰性(生成器/迭代器),不提前消费
        fake = MagicMock()
        fake.run_stream.return_value = iter([{"type": "token", "text": "x"}])
        svc = AgentService(agent=fake)
        result = svc.run_stream("hi")
        assert hasattr(result, "__iter__")
