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
        """测试深思熟虑路径（默认走分层检索：目录选点->定向检索）"""
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
        mock_components["tools"].select_resume_knowledge.assert_called_once()
        mock_components["tools"].retrieve_by_plan.assert_called_once()
        mock_components["tools"].search_knowledge.assert_not_called()
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

    def test_deep_thinking_hierarchical_search(self, mock_components):
        """深思路径应优先走分层检索（目录选点->定向检索）"""
        from src.agent.intent import IntentResult
        from src.schemas.knowledge import SelectionPlan, DocSelection
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )
        plan = SelectionPlan(selections=[DocSelection(doc_id="d1", selected_points=["LangChain链式编排"])])
        mock_components["tools"].select_resume_knowledge.return_value = plan
        mock_components["tools"].retrieve_by_plan.return_value = "定向检索的知识"

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("帮我生成简历")

        assert result["retrieved_knowledge"] == "定向检索的知识"
        mock_components["tools"].select_resume_knowledge.assert_called_once()
        mock_components["tools"].retrieve_by_plan.assert_called_once()
        # 分层检索成功时不应走普通检索
        mock_components["tools"].search_knowledge.assert_not_called()

    def test_deep_thinking_fallback_without_plan(self, mock_components):
        """选点失败（无目录/解析失败）应降级为普通混合检索"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )
        mock_components["tools"].select_resume_knowledge.return_value = None

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("帮我生成简历")

        assert result["retrieved_knowledge"] == "检索到的知识内容"
        mock_components["tools"].search_knowledge.assert_called_once()
        mock_components["tools"].retrieve_by_plan.assert_not_called()

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

    def test_deep_thinking_with_existing_resume(self, mock_components):
        """已有简历应透传给generate_resume作为事实骨架"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        result = agent.run("帮我生成简历", existing_resume="# 张三的旧简历")

        assert result["existing_resume"] == "# 张三的旧简历"
        mock_components["tools"].generate_resume.assert_called_once()
        assert mock_components["tools"].generate_resume.call_args.kwargs["existing_resume"] == "# 张三的旧简历"

    def test_deep_thinking_without_existing_resume(self, mock_components):
        """未提供已有简历时透传None（行为不变）"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        agent.run("帮我生成简历")

        assert mock_components["tools"].generate_resume.call_args.kwargs["existing_resume"] is None

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


class TestResumeAgentStream:
    """run_stream流式输出测试"""

    def test_quick_response_stream(self, mock_components):
        """快速回答应流式输出token事件"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.QUICK_RESPONSE,
            entities={"tech_keywords": ["推测解码"]},
            confidence=0.9,
        )
        mock_components["tools"].answer_question_stream.return_value = iter(["是的，", "草稿模型", "生成内容。"])

        agent = ResumeAgent(config=mock_components["config"])
        events = list(agent.run_stream("推测解码是什么？"))

        types = [e["type"] for e in events]
        assert "status" in types
        assert "token" in types
        assert types[-1] == "done"
        # token拼接后是完整回答
        full = "".join(e["text"] for e in events if e["type"] == "token")
        assert full == "是的，草稿模型生成内容。"
        assert events[-1]["intent"] == "quick_response"
        assert events[-1]["retrieved_knowledge"] == "检索到的知识内容"

    def test_chitchat_stream(self, mock_components):
        """闲聊应流式输出"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.CHITCHAT,
            entities={},
            confidence=0.3,
        )
        mock_components["api"].send_message_stream.return_value = iter(["你好！", "有什么可以帮你？"])

        agent = ResumeAgent(config=mock_components["config"])
        events = list(agent.run_stream("你好"))

        full = "".join(e["text"] for e in events if e["type"] == "token")
        assert full == "你好！有什么可以帮你？"
        assert events[-1]["intent"] == "chitchat"

    def test_chitchat_stream_error_fallback(self, mock_components):
        """闲聊流式输出异常时降级为固定欢迎语"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.CHITCHAT,
            entities={},
            confidence=0.3,
        )
        mock_components["api"].send_message_stream.side_effect = Exception("API Error")

        agent = ResumeAgent(config=mock_components["config"])
        events = list(agent.run_stream("你好"))

        full = "".join(e["text"] for e in events if e["type"] == "token")
        assert "ResumeAgent" in full

    def test_deep_thinking_stream(self, mock_components):
        """深思路径应输出阶段状态和最终简历"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        events = list(agent.run_stream("帮我生成简历"))

        assert events[-1]["type"] == "done"
        assert events[-1]["resume_final"] == "# 简历\n\n## 项目经历"
        status_texts = [e["text"] for e in events if e["type"] == "status"]
        assert len(status_texts) >= 2  # 至少有检索和生成两个阶段提示

    def test_deep_thinking_stream_with_existing_resume(self, mock_components):
        """流式深思路径：有已有简历时输出提示并透传"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        events = list(agent.run_stream("帮我生成简历", existing_resume="# 旧简历"))

        status_texts = [e["text"] for e in events if e["type"] == "status"]
        assert any("检测到已有简历" in t for t in status_texts)
        assert mock_components["tools"].generate_resume.call_args.kwargs["existing_resume"] == "# 旧简历"

    def test_deep_thinking_stream_without_existing_resume(self, mock_components):
        """流式深思路径：无已有简历时不输出相关提示"""
        from src.agent.intent import IntentResult
        mock_components["classifier"].classify.return_value = IntentResult(
            intent=IntentType.DEEP_THINKING,
            entities={},
            confidence=0.9,
        )

        agent = ResumeAgent(config=mock_components["config"])
        events = list(agent.run_stream("帮我生成简历"))

        status_texts = [e["text"] for e in events if e["type"] == "status"]
        assert not any("检测到已有简历" in t for t in status_texts)
        assert mock_components["tools"].generate_resume.call_args.kwargs["existing_resume"] is None
