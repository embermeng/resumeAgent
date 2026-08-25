"""
api_client.py 单元测试
使用Mock避免真实API调用
"""
import json
import pytest
from unittest.mock import patch, MagicMock
from pydantic import BaseModel, Field


# 测试用的Pydantic Schema
class SampleAnswerSchema(BaseModel):
    answer: str = Field(description="答案")
    confidence: float = Field(description="置信度")


class TestBaseDashscopeProcessor:
    """DashScope处理器测试（OpenAI兼容端点实现）"""

    def _make_mock_completion(self, content="测试回复"):
        """构造OpenAI兼容格式的模拟响应"""
        mock_completion = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = content
        mock_completion.choices = [mock_choice]
        mock_completion.usage.prompt_tokens = 10
        mock_completion.usage.completion_tokens = 20
        return mock_completion

    def _make_proc(self):
        from src.api_client import BaseDashscopeProcessor
        proc = BaseDashscopeProcessor.__new__(BaseDashscopeProcessor)
        proc.llm = MagicMock()
        proc.default_model = "qwen-turbo-latest"
        proc.response_data = {}
        return proc

    def test_send_message_basic(self):
        proc = self._make_proc()
        proc.llm.chat.completions.create.return_value = self._make_mock_completion(
            '{"answer": "你好", "confidence": 0.9}')

        result = proc.send_message(human_content="你好")
        assert result["answer"] == "你好"
        assert result["confidence"] == 0.9
        assert proc.response_data["input_tokens"] == 10
        # 确认走的是兼容端点客户端
        call_kwargs = proc.llm.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("model") == "qwen-turbo-latest"

    def test_send_message_structured(self):
        proc = self._make_proc()
        proc.llm.chat.completions.create.return_value = self._make_mock_completion(
            '{"answer": "测试", "confidence": 0.8}')

        result = proc.send_message(
            human_content="测试",
            is_structured=True,
            response_format=SampleAnswerSchema,
        )
        assert result["answer"] == "测试"
        assert result["confidence"] == 0.8

    def test_send_message_non_json(self):
        proc = self._make_proc()
        proc.llm.chat.completions.create.return_value = self._make_mock_completion("这是一段普通文本")

        result = proc.send_message(human_content="测试")
        assert "content" in result
        assert result["content"] == "这是一段普通文本"

    def test_send_message_with_markdown_code_block(self):
        proc = self._make_proc()
        content = '```json\n{"answer": "代码块测试", "confidence": 0.7}\n```'
        proc.llm.chat.completions.create.return_value = self._make_mock_completion(content)

        result = proc.send_message(
            human_content="测试",
            is_structured=True,
            response_format=SampleAnswerSchema,
        )
        assert result["answer"] == "代码块测试"

    def test_response_data_populated(self):
        proc = self._make_proc()
        proc.llm.chat.completions.create.return_value = self._make_mock_completion("ok")

        proc.send_message(model="qwen-plus", human_content="hi")
        assert proc.response_data["model"] == "qwen-plus"
        assert proc.response_data["input_tokens"] == 10
        assert proc.response_data["output_tokens"] == 20

    def test_send_message_api_error_raises(self):
        """API报错（如403模型无权限）时应抛异常，而不是把错误当正常内容吞掉"""
        proc = self._make_proc()
        err = Exception("Access denied.")
        err.status_code = 403
        proc.llm.chat.completions.create.side_effect = err

        with pytest.raises(RuntimeError, match="403"):
            proc.send_message(human_content="hi")

    def test_compatible_base_url(self):
        """应使用OpenAI兼容端点（支持qwen3.8-max等多模态新模型）"""
        from src.api_client import BaseDashscopeProcessor
        assert "compatible-mode" in BaseDashscopeProcessor.COMPATIBLE_BASE_URL

    def test_send_message_stream(self):
        """流式输出应逐块yield文本增量"""
        proc = self._make_proc()
        proc.default_model = "qwen-turbo"

        # 模拟OpenAI流式响应的多个增量块
        chunks = []
        for text in ["是的，", "草稿模型", "生成内容。"]:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = text
            chunks.append(chunk)
        proc.llm.chat.completions.create.return_value = iter(chunks)

        result = list(proc.send_message_stream(human_content="测试"))
        assert result == ["是的，", "草稿模型", "生成内容。"]
        # 确认使用了stream参数
        call_kwargs = proc.llm.chat.completions.create.call_args.kwargs
        assert call_kwargs.get("stream") is True

    def test_send_message_stream_error_raises(self):
        """流式调用报错（如429限流）应抛异常"""
        proc = self._make_proc()
        proc.default_model = "qwen-turbo"
        err = Exception("Rate limit")
        err.status_code = 429
        proc.llm.chat.completions.create.side_effect = err

        with pytest.raises(RuntimeError, match="429"):
            list(proc.send_message_stream(human_content="hi"))


class TestAPIProcessor:
    """统一API处理器测试"""

    def test_provider_dashscope(self):
        with patch("src.api_client.BaseDashscopeProcessor.__init__", return_value=None):
            from src.api_client import APIProcessor
            proc = APIProcessor(provider="dashscope")
            assert proc.provider == "dashscope"

    def test_provider_openai(self):
        with patch("src.api_client.BaseOpenaiProcessor.__init__", return_value=None):
            from src.api_client import APIProcessor
            proc = APIProcessor(provider="openai")
            assert proc.provider == "openai"

    def test_provider_gemini(self):
        with patch("src.api_client.BaseGeminiProcessor.__init__", return_value=None):
            from src.api_client import APIProcessor
            proc = APIProcessor(provider="gemini")
            assert proc.provider == "gemini"

    def test_invalid_provider(self):
        from src.api_client import APIProcessor
        with pytest.raises(ValueError, match="不支持的provider"):
            APIProcessor(provider="invalid")

    def test_default_model_property(self):
        with patch("src.api_client.BaseDashscopeProcessor.__init__", return_value=None):
            from src.api_client import APIProcessor
            proc = APIProcessor(provider="dashscope")
            proc.processor.default_model = "qwen-turbo-latest"
            assert proc.default_model == "qwen-turbo-latest"

    def test_send_message_routes_to_processor(self):
        with patch("src.api_client.BaseDashscopeProcessor.__init__", return_value=None):
            from src.api_client import APIProcessor
            proc = APIProcessor(provider="dashscope")
            proc.processor = MagicMock()
            proc.processor.default_model = "qwen-turbo-latest"
            proc.processor.send_message.return_value = {"answer": "ok"}

            result = proc.send_message(human_content="测试")
            proc.processor.send_message.assert_called_once()
            assert result["answer"] == "ok"


class TestGetEmbedding:
    """Embedding获取测试"""

    @patch.dict("os.environ", {"DASHSCOPE_API_KEY": "fake_key"})
    def test_dashscope_embedding(self):
        from src.api_client import APIProcessor
        with patch("src.api_client.BaseDashscopeProcessor.__init__", return_value=None):
            proc = APIProcessor(provider="dashscope")
            mock_dashscope = MagicMock()
            mock_rsp = {
                "output": {
                    "embeddings": [{"embedding": [0.1, 0.2, 0.3], "text_index": 0}]
                }
            }
            mock_dashscope.TextEmbedding.call.return_value = mock_rsp

            with patch.dict("sys.modules", {"dashscope": mock_dashscope}):
                # 直接测试 _extract_json_str 静态方法
                from src.api_client import BaseDashscopeProcessor
                result = BaseDashscopeProcessor._extract_json_str('```json\n{"a": 1}\n```')
                assert result == '{"a": 1}'

    def test_extract_json_str_plain(self):
        from src.api_client import BaseDashscopeProcessor
        assert BaseDashscopeProcessor._extract_json_str('{"a": 1}') == '{"a": 1}'

    def test_extract_json_str_with_code_block(self):
        from src.api_client import BaseDashscopeProcessor
        result = BaseDashscopeProcessor._extract_json_str('```json\n{"a": 1}\n```')
        assert result == '{"a": 1}'

    def test_try_parse_json_valid(self):
        from src.api_client import BaseDashscopeProcessor
        result = BaseDashscopeProcessor._try_parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_try_parse_json_with_code_block(self):
        from src.api_client import BaseDashscopeProcessor
        result = BaseDashscopeProcessor._try_parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_try_parse_json_invalid(self):
        from src.api_client import BaseDashscopeProcessor
        with pytest.raises(json.JSONDecodeError):
            BaseDashscopeProcessor._try_parse_json("not json")
