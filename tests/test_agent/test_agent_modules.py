"""
Agent模块测试
"""
import json
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

    def test_classify_disables_thinking(self, classifier, mock_api):
        """意图识别是轻任务，应关闭thinking降低延迟（qwen3系列thinking默认开启）"""
        mock_api.send_message.return_value = {
            "intent": "quick_response",
            "entities": {},
            "confidence": 0.8,
        }
        classifier.classify("蒸馏是什么意思？")
        assert mock_api.send_message.call_args.kwargs["enable_thinking"] is False

    def test_classify_api_error_fallback(self, classifier, mock_api):
        """测试API异常时降级"""
        mock_api.send_message.side_effect = Exception("API Error")
        result = classifier.classify("RAG是什么")
        # 降级应分类为quick_response（保证走知识库检索）
        assert result.intent == IntentType.QUICK_RESPONSE

    def test_classify_parse_error_fallback(self, classifier, mock_api):
        """测试API返回错误响应（如403被吞掉后产生parse_error）时降级为检索问答"""
        mock_api.send_message.return_value = {
            "content": "{'status_code': 403, 'code': 'AccessDenied'}",
            "parse_error": "intent Field required",
        }
        result = classifier.classify("推测解码是不是草稿模型和打分模型配合？")
        assert result.intent == IntentType.QUICK_RESPONSE

    def test_fallback_resume_keywords(self):
        """测试降级分类 - 简历关键词"""
        result = IntentClassifier._fallback_classify("帮我生成简历")
        assert result.intent == IntentType.DEEP_THINKING

    def test_fallback_default_quick_response(self):
        """降级默认走快速回答（检索后回答），而非闲聊，避免纯靠LLM自身知识回答"""
        result = IntentClassifier._fallback_classify("今天天气真好")
        assert result.intent == IntentType.QUICK_RESPONSE
        result = IntentClassifier._fallback_classify("推测解码是不是草稿模型和打分模型配合？")
        assert result.intent == IntentType.QUICK_RESPONSE


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
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
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


# ============================================================
# search_projects 标签优先检索测试
# ============================================================

def _write_project_chunk_doc(chunks_dir, doc_id, source, tags):
    """写一份最简项目分块文档（只关心metainfo，供list_tagged_project_ids扫描）"""
    data = {
        "metainfo": {"doc_id": doc_id, "source": source, "category": "project", "tags": tags},
        "content": {"chunks": []},
    }
    (chunks_dir / f"{doc_id}.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )


class TestSearchProjectsPriority:
    def _make_tools(self, tmp_path):
        config = MagicMock()
        config.paths.course_chunks_dir = tmp_path
        config.llm.provider = "dashscope"
        return AgentTools(config=config)

    @patch("src.agent.tools.APIProcessor")
    def test_list_tagged_project_ids(self, mock_api, tmp_path):
        _write_project_chunk_doc(tmp_path, "project-aaa", "优先项目A", ["简历优先"])
        _write_project_chunk_doc(tmp_path, "project-bbb", "普通项目B", [])
        _write_project_chunk_doc(tmp_path, "project-ccc", "优先项目C", ["简历优先", "其他"])
        # 非项目文档不干扰（不匹配project-前缀文件名）
        (tmp_path / "abcdef1234567890.json").write_text("{}", encoding="utf-8")

        tools = self._make_tools(tmp_path)
        tagged = tools.list_tagged_project_ids()
        assert tagged == [("project-aaa", "优先项目A"), ("project-ccc", "优先项目C")]

    @patch("src.agent.tools.APIProcessor")
    def test_priority_projects_all_present(self, mock_api, tmp_path):
        """打了标签的每个项目都保证进入检索结果，剩余名额全局补齐"""
        _write_project_chunk_doc(tmp_path, "project-p1", "优先一", ["简历优先"])
        _write_project_chunk_doc(tmp_path, "project-p2", "优先二", ["简历优先"])
        _write_project_chunk_doc(tmp_path, "project-x", "普通项目", [])

        tools = self._make_tools(tmp_path)
        mock_hybrid = MagicMock()

        def fake_retrieve(query, category=None, top_n=5, doc_ids=None):
            if doc_ids:
                d = doc_ids[0]
                return [{"doc_id": d, "chunk_id": 0, "source": f"src-{d}", "text": f"定向内容{d}"}]
            return [
                {"doc_id": "project-p1", "chunk_id": 0, "source": "src-project-p1", "text": "重复块"},
                {"doc_id": "project-x", "chunk_id": 0, "source": "src-project-x", "text": "全局内容"},
            ]

        mock_hybrid.retrieve.side_effect = fake_retrieve
        tools._hybrid_retriever = mock_hybrid

        result = tools.search_projects("生成简历", top_n=5)
        # 两个优先项目都入选 + 全局补齐（去重后）
        assert "src-project-p1" in result
        assert "src-project-p2" in result
        assert "src-project-x" in result
        # 重复块只出现一次（去重生效）
        assert result.count("定向内容project-p1") == 1

    @patch("src.agent.tools.APIProcessor")
    def test_no_tags_fallback_to_plain_search(self, mock_api, tmp_path):
        """无标签文档时退化为普通混合检索（不带doc_ids过滤）"""
        _write_project_chunk_doc(tmp_path, "project-x", "普通项目", [])

        tools = self._make_tools(tmp_path)
        mock_hybrid = MagicMock()
        mock_hybrid.retrieve.return_value = [
            {"doc_id": "project-x", "chunk_id": 0, "source": "src-x", "text": "内容"}
        ]
        tools._hybrid_retriever = mock_hybrid

        result = tools.search_projects("生成简历")
        assert "src-x" in result
        # 只调了一次检索，且未传doc_ids定向
        assert mock_hybrid.retrieve.call_count == 1
        _, kwargs = mock_hybrid.retrieve.call_args
        assert kwargs.get("doc_ids") is None


# ============================================================
# select_project_intros 项目介绍挑选测试
# ============================================================

def _write_intro(intros_dir, file_name, project_name, tagged=False, summary="一个项目"):
    tag_line = "\n> 标签：简历优先\n" if tagged else ""
    content = f"# {project_name}\n{tag_line}\n## 一句话简介\n{summary}\n\n## 技术栈\nPython\n"
    (intros_dir / file_name).write_text(content, encoding="utf-8")


class TestSelectProjectIntros:
    def _make_tools(self, tmp_path):
        config = MagicMock()
        config.paths.project_intros_dir = tmp_path
        config.llm.provider = "dashscope"
        config.llm.model = "test-model"
        return AgentTools(config=config)

    @patch("src.agent.tools.APIProcessor")
    def test_empty_dir_returns_empty(self, mock_api, tmp_path):
        tools = self._make_tools(tmp_path)
        assert tools.select_project_intros("生成简历") == ""

    @patch("src.agent.tools.APIProcessor")
    def test_small_count_use_all_without_llm(self, mock_api, tmp_path):
        """≤6份时全选，标签项目排前，不调LLM"""
        _write_intro(tmp_path, "b.md", "普通项目")
        _write_intro(tmp_path, "a.md", "重点项目", tagged=True)

        tools = self._make_tools(tmp_path)
        tools._api = MagicMock()
        result = tools.select_project_intros("生成简历")
        assert "重点项目" in result and "普通项目" in result
        # 标签项目排在前，且未触发LLM挑选
        assert result.index("重点项目") < result.index("普通项目")
        tools._api.send_message.assert_not_called()

    @patch("src.agent.tools.APIProcessor")
    def test_many_docs_llm_select_with_tagged_mandatory(self, mock_api, tmp_path):
        """>6份时LLM挑选；标签项目必选；编造项目名被过滤"""
        _write_intro(tmp_path, "00.md", "优先项目", tagged=True)
        for i in range(1, 9):
            _write_intro(tmp_path, f"{i:02d}.md", f"项目{i}", summary=f"简介{i}")

        tools = self._make_tools(tmp_path)
        mock_llm = MagicMock()
        # LLM选中两个真实项目 + 一个编造项目名 + 一个标签项目（应去重）
        mock_llm.send_message.return_value = {
            "selected_projects": ["项目3", "编造项目", "项目1", "优先项目"]
        }
        tools._api = mock_llm

        result = tools.select_project_intros("生成简历", top_n=5)
        assert "优先项目" in result  # 标签必选
        assert "项目3" in result and "项目1" in result
        assert "编造项目" not in result
        assert "项目2" not in result  # 未被选中的不入选（总量≤5）
        # 标签项目排最前，且只调了一次LLM挑选（非每项目一次）
        assert result.index("优先项目") < result.index("项目3")
        assert mock_llm.send_message.call_count == 1

    @patch("src.agent.tools.APIProcessor")
    def test_llm_failure_fallback_to_tagged_plus_head(self, mock_api, tmp_path):
        """挑选调用失败：标签项目 + 文件顺序兜底，总量受top_n约束"""
        _write_intro(tmp_path, "00.md", "优先项目", tagged=True)
        for i in range(1, 9):
            _write_intro(tmp_path, f"{i:02d}.md", f"项目{i}")

        tools = self._make_tools(tmp_path)
        mock_llm = MagicMock()
        mock_llm.send_message.side_effect = Exception("API down")
        tools._api = mock_llm

        result = tools.select_project_intros("生成简历", top_n=3)
        assert "优先项目" in result
        # 总量受top_n约束：共3个条目编号 [1]-[3]
        assert result.count("项目：") == 3
