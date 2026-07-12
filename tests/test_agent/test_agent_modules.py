"""
Agent模块测试
"""
import pytest
from unittest.mock import patch, MagicMock

from src.agent.state import AgentState
from src.agent.intent import IntentClassifier, IntentType, IntentResult
from src.agent.tools import AgentTools


# ============================================================
# State 测试
# ============================================================

class TestAgentState:
    def test_state_creation(self):
        state = AgentState(
            messages=[],
            user_input="test",
            intent="quick_response",
            extracted_entities={},
            has_job_requirement=False,
            retrieved_knowledge="",
            retrieved_projects="",
            resume_draft="",
            resume_final="",
            final_response="",
            step="init",
        )
        assert state["user_input"] == "test"
        assert state["intent"] == "quick_response"
        assert state["step"] == "init"

    def test_state_defaults(self):
        state = AgentState(
            messages=[],
            user_input="",
            intent="",
            extracted_entities={},
            has_job_requirement=False,
            retrieved_knowledge="",
            retrieved_projects="",
            resume_draft="",
            resume_final="",
            final_response="",
            step="",
        )
        assert state["messages"] == []
        assert state["has_job_requirement"] is False


# ============================================================
# Intent 测试
# ============================================================

@pytest.fixture
def mock_api():
    with patch("src.agent.intent.APIProcessor") as mock:
        instance = MagicMock()
        instance.default_model = "test-model"
        mock.return_value = instance
        yield instance


@pytest.fixture
def classifier(mock_api):
    return IntentClassifier(provider="dashscope")


class TestIntentClassifier:
    def test_classify_resume_intent(self, classifier, mock_api):
        """测试简历生成意图"""
        mock_api.send_message.return_value = {
            "intent": "deep_thinking",
            "entities": {"job_title": "AI工程师"},
            "confidence": 0.9,
        }
        result = classifier.classify("帮我生成一份AI工程师的简历")
        assert result.intent == IntentType.DEEP_THINKING
        assert result.confidence == 0.9

    def test_classify_question_intent(self, classifier, mock_api):
        """测试知识问答意图"""
        mock_api.send_message.return_value = {
            "intent": "quick_response",
            "entities": {"tech_keywords": ["RAG"]},
            "confidence": 0.85,
        }
        result = classifier.classify("RAG的核心流程是什么？")
        assert result.intent == IntentType.QUICK_RESPONSE

    def test_classify_chitchat(self, classifier, mock_api):
        """测试闲聊意图"""
        mock_api.send_message.return_value = {
            "intent": "chitchat",
            "entities": {},
            "confidence": 0.3,
        }
        result = classifier.classify("你好呀")
        assert result.intent == IntentType.CHITCHAT

    def test_classify_api_error_fallback(self, classifier, mock_api):
        """测试API异常时降级"""
        mock_api.send_message.side_effect = Exception("API Error")
        result = classifier.classify("RAG是什么")
        # 降级应分类为quick_response（包含"什么"）
        assert result.intent == IntentType.QUICK_RESPONSE

    def test_fallback_resume_keywords(self):
        """测试降级分类 - 简历关键词"""
        result = IntentClassifier._fallback_classify("帮我生成简历")
        assert result.intent == IntentType.DEEP_THINKING

    def test_fallback_tech_keywords(self):
        """测试降级分类 - 技术关键词"""
        result = IntentClassifier._fallback_classify("FAISS怎么用")
        assert result.intent == IntentType.QUICK_RESPONSE

    def test_fallback_chitchat(self):
        """测试降级分类 - 闲聊"""
        result = IntentClassifier._fallback_classify("今天天气真好")
        assert result.intent == IntentType.CHITCHAT


# ============================================================
# Tools 测试
# ============================================================

class TestAgentTools:
    @patch("src.agent.tools.get_config")
    def test_search_knowledge_no_data(self, mock_config):
        """测试知识库为空时的检索"""
        config = MagicMock()
        config.paths.bm25_dbs_dir = MagicMock()
        config.paths.course_chunks_dir = MagicMock()
        config.paths.vector_dbs_dir = MagicMock()
        config.embedding.provider = "dashscope"
        config.embedding.model = "text-embedding-v1"
        mock_config.return_value = config

        tools = AgentTools(config=config)
        result = tools.search_knowledge("test query")
        assert "检索失败" in result or "未检索到" in result

    @patch("src.agent.tools.APIProcessor")
    @patch("src.agent.tools.get_config")
    def test_answer_question(self, mock_config, mock_api_class):
        """测试回答问题"""
        config = MagicMock()
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
        mock_config.return_value = config

        mock_api = MagicMock()
        mock_api.send_message.return_value = {"content": "RAG是检索增强生成..."}
        mock_api_class.return_value = mock_api

        tools = AgentTools(config=config)
        result = tools.answer_question("RAG是什么", "RAG相关内容...")
        assert "RAG" in result

    @patch("src.agent.tools.APIProcessor")
    @patch("src.agent.tools.get_config")
    def test_generate_resume(self, mock_config, mock_api_class):
        """测试简历生成"""
        config = MagicMock()
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
        mock_config.return_value = config

        mock_api = MagicMock()
        mock_api.send_message.return_value = {"content": "# 简历\n\n## 项目经历"}
        mock_api_class.return_value = mock_api

        tools = AgentTools(config=config)
        result = tools.generate_resume("知识内容", "项目信息")
        assert "简历" in result or "项目" in result

    @patch("src.agent.tools.APIProcessor")
    @patch("src.agent.tools.get_config")
    def test_generate_resume_with_job(self, mock_config, mock_api_class):
        """测试带岗位要求的简历生成"""
        config = MagicMock()
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
        mock_config.return_value = config

        mock_api = MagicMock()
        mock_api.send_message.return_value = {"content": "# 优化后的简历"}
        mock_api_class.return_value = mock_api

        tools = AgentTools(config=config)
        result = tools.generate_resume("知识", "项目", job_requirement="JD内容")
        assert isinstance(result, str)
