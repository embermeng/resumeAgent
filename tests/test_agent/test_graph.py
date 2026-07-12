"""
LangGraph Agent图测试
"""
import pytest
from unittest.mock import patch, MagicMock

from src.agent.graph import ResumeAgent
from src.agent.intent import IntentType


@pytest.fixture
def mock_components():
    """Mock所有外部依赖"""
    with patch("src.agent.graph.IntentClassifier") as mock_intent_cls, \
         patch("src.agent.graph.AgentTools") as mock_tools_cls, \
         patch("src.agent.graph.APIProcessor") as mock_api_cls, \
         patch("src.agent.graph.get_config") as mock_config:

        config = MagicMock()
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
        mock_config.return_value = config

        # Mock IntentClassifier
        mock_classifier = MagicMock()
        mock_intent_cls.return_value = mock_classifier

        # Mock AgentTools
        mock_tools = MagicMock()
        mock_tools.search_knowledge.return_value = "检索到的知识内容"
        mock_tools.search_projects.return_value = "检索到的项目信息"
        mock_tools.answer_question.return_value = "这是回答"
        mock_tools.generate_resume.return_value = "# 简历\n\n## 项目经历"
        mock_tools.optimize_resume.return_value = "# 优化后的简历"
        mock_tools_cls.return_value = mock_tools

        # Mock APIProcessor
        mock_api = MagicMock()
        mock_api.send_message.return_value = {"content": "你好！"}
        mock_api_cls.return_value = mock_api

        yield {
            "config": config,
            "classifier": mock_classifier,
            "tools": mock_tools,
            "api": mock_api,
        }


class TestResumeAgent:
    def test_quick_response_path(self, mock_components):
        """测试快速反应路径"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.QUICK_RESPONSE,
            entities={"tech_keywords": ["RAG"]},
            confidence=0.8,
        )

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("RAG的核心流程是什么？")

        assert result["intent"] == "quick_response"
        assert result["step"] == "quick_response_done"
        assert result["final_response"] == "这是回答"
        mock_components["tools"].search_knowledge.assert_called_once()
        mock_components["tools"].answer_question.assert_called_once()

    def test_deep_thinking_path(self, mock_components):
        """测试深思熟虑路径"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("帮我生成一份简历")

        assert result["intent"] == "deep_thinking"
        assert result["step"] == "deep_thinking_done"
        assert "简历" in result["resume_draft"]
        mock_components["tools"].search_knowledge.assert_called_once()
        mock_components["tools"].generate_resume.assert_called_once()

    def test_deep_thinking_with_job(self, mock_components):
        """测试带岗位要求的深思熟虑路径"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={"job_title": "AI工程师"},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("帮我生成一份AI工程师的简历，岗位要求如下...")

        assert result["intent"] == "deep_thinking"
        assert result["has_job_requirement"] is True
        mock_components["tools"].optimize_resume.assert_called_once()

    def test_chitchat_path(self, mock_components):
        """测试闲聊路径"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.CHITCHAT,
            entities={},
            confidence=0.3,
        )

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("你好呀")

        assert result["intent"] == "chitchat"
        assert result["step"] == "chitchat_done"
        assert result["final_response"] == "你好！"

    def test_chitchat_api_error(self, mock_components):
        """测试闲聊API异常"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.CHITCHAT,
            entities={},
            confidence=0.3,
        )
        mock_components["api"].send_message.side_effect = Exception("API Error")

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("你好")

        assert result["step"] == "chitchat_done"
        assert "ResumeAgent" in result["final_response"]
